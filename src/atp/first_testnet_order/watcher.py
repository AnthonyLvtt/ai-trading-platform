"""Foreground, read-only observation under one externally pinned activation grant."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

from atp.exchange.read_only import EvidenceError
from atp.first_testnet_order.ledger import TestnetSubmissionLedger
from atp.first_testnet_order.preparation_runtime import ReadOnlySource, prepare_check_only
from atp.release_deployment.model import ReleaseBundle
from atp.shared.identity import ContentIdentity
from atp.testnet_activation.composition import CredentialCapabilityAuthority
from atp.testnet_activation.contracts import TestnetActivationGrant, valid
from atp.testnet_qualification.model import (
    TestnetCapabilityEvidence,
    TestnetQualificationSuiteResult,
)


class WatchWindow:
    """Injected UTC clock and interruptible waits; expiry applies even during pin input."""

    def __init__(
        self,
        now: Callable[[], datetime],
        stop: Event,
        wait: Callable[[float], bool] | None = None,
    ) -> None:
        self.clock, self.stop = now, stop
        self.wait = stop.wait if wait is None else wait
        self.grant: TestnetActivationGrant | None = None
        self.previous: datetime | None = None

    def read(self) -> datetime:
        at = self.clock()
        if type(at) is not datetime or at.tzinfo is not UTC:
            raise EvidenceError("TIME_EVIDENCE_INVALID")
        if self.previous is not None and at < self.previous:
            raise EvidenceError("TIME_EVIDENCE_INVALID")
        self.previous = at
        if self.grant is not None:
            if at >= self.grant.validity_end:
                raise EvidenceError("ACTIVATION_GRANT_EXPIRED")
            if at < self.grant.validity_start:
                raise EvidenceError("ACTIVATION_GRANT_NOT_YET_VALID")
        if self.stop.is_set():
            raise EvidenceError("WATCHER_STOPPED")
        return at

    def bind(self, grant: TestnetActivationGrant) -> None:
        if type(grant) is TestnetActivationGrant and grant.allowed_environment == "LIVE":
            raise EvidenceError("LIVE_FORBIDDEN")
        if not valid(grant, TestnetActivationGrant):
            raise EvidenceError("ACTIVATION_GRANT_INVALID")
        if self.grant is not None and self.grant != grant:
            raise EvidenceError("ACTIVATION_GRANT_INVALID")
        if grant.validity_end - grant.validity_start > timedelta(minutes=30):
            raise EvidenceError("ACTIVATION_GRANT_INVALID")
        self.grant = grant
        self.read()

    def next_close(self) -> None:
        at = self.read()
        target = at.replace(second=0, microsecond=0) + timedelta(minutes=5 - at.minute % 5)
        assert self.grant is not None
        target = min(target, self.grant.validity_end)
        while (at := self.read()) < target:
            # Short interruptible waits detect wall-clock changes and stop requests.
            self.wait(min(1.0, (target - at).total_seconds()))
        self.read()


class BoundedReadOnlySource:
    def __init__(self, source: ReadOnlySource, window: WatchWindow) -> None:
        self.source, self.window = source, window

    def read(self, resource: str, parameters: tuple[tuple[str, str], ...] = ()) -> object:
        self.window.read()
        result = self.source.read(resource, parameters)
        # A GET already in flight may finish after expiry; its response cannot be used.
        self.window.read()
        return result


def watch_check_only(
    *,
    source: ReadOnlySource,
    window: WatchWindow,
    release: ReleaseBundle,
    wheel: bytes,
    tq: TestnetQualificationSuiteResult,
    tq_evidence: TestnetCapabilityEvidence,
    credential_source_identity: ContentIdentity,
    credential_authority: CredentialCapabilityAuthority,
    workspace: Path,
    ledger: TestnetSubmissionLedger,
    artifact_sink: Callable[[str, object], None],
    trust_pin_source: Callable[[str], ContentIdentity | None],
    report_sink: Callable[[dict[str, object]], None],
    source_check: Callable[[], None],
    activation_grant: TestnetActivationGrant | None = None,
) -> dict[str, object]:
    """One initial evaluation of closed candles, then UTC 5m closes; never renew authority.

    Only NO_ACTION/EXIT repeats. Any gate failure or candidate first-order outcome stops.
    Pins are retained solely from the external channel, not generated from candidates.
    """
    grant = activation_grant
    pin: ContentIdentity | None = None
    tick = 0

    def save(kind: str, artifact: object) -> None:
        nonlocal grant
        window.read()
        if kind == "activation-grant":
            if type(artifact) is not TestnetActivationGrant:
                raise EvidenceError("ACTIVATION_GRANT_INVALID")
            window.bind(artifact)
            grant = artifact
            if tick != 1:
                return
        name = (
            kind
            if kind in ("activation-grant", "first-order-authorization")
            else (f"tick-{tick:04d}-{kind}")
        )
        artifact_sink(name, artifact)

    def external_pin(kind: str) -> ContentIdentity | None:
        nonlocal pin
        window.read()
        if kind == "activation-grant" and tick > 1:
            return pin
        supplied = trust_pin_source(kind)
        window.read()
        if kind == "activation-grant":
            pin = supplied
        return supplied

    try:
        if grant is not None:
            window.bind(grant)
        while True:
            window.read()
            source_check()
            window.read()
            tick += 1
            result = prepare_check_only(
                source=BoundedReadOnlySource(source, window),
                now=window.read,
                release=release,
                wheel=wheel,
                tq=tq,
                tq_evidence=tq_evidence,
                credential_source_identity=credential_source_identity,
                credential_authority=credential_authority,
                workspace=workspace,
                ledger=ledger,
                artifact_sink=save,
                trust_pin_source=external_pin,
                activation_grant=grant,
            )
            window.read()
            source_check()
            window.read()
            result["transport_call_count"] = 0
            if (
                result.get("strategy_signal") not in ("NO_ACTION", "EXIT")
                or result.get("reason_code") != "FIRST_ORDER_NOT_READY"
            ):
                return result
            result["status"] = "NOT_READY"
            report_sink(result)
            window.next_close()
    except EvidenceError as exc:
        # Only watcher lifecycle errors are handled here; existing CLI sanitizes other errors.
        if exc.args not in (
            ("ACTIVATION_GRANT_EXPIRED",),
            ("ACTIVATION_GRANT_NOT_YET_VALID",),
            ("WATCHER_STOPPED",),
        ):
            raise
        return {
            "status": "BLOCKED",
            "reason_code": exc.args[0],
            "real_economic_calls": 0,
            "transport_call_count": 0,
            "LIVE": "LIVE_FORBIDDEN",
        }
