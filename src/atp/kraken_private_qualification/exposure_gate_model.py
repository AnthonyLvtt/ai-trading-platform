"""Sanitized outcome of the single exposure-observation operator gate."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from atp.exchange.contracts import VenueId
from atp.exchange.read_only import EvidenceError, EvidenceRecord
from atp.shared.identity import ContentIdentity


class ExposureGateStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"


class ExposureGateReason(StrEnum):
    QUALIFIED = "EXPOSURE_OPERATOR_SEQUENCE_QUALIFIED"
    SOURCE_INVALID = "EXPOSURE_GATE_SOURCE_INVALID"
    CREDENTIAL_INVALID = "EXPOSURE_GATE_CREDENTIAL_INVALID"
    KEY_INFO_INVALID = "EXPOSURE_GATE_KEY_INFO_INVALID"
    LEAST_PRIVILEGE_REQUIRED = "EXPOSURE_GATE_LEAST_PRIVILEGE_REQUIRED"
    ACCOUNT_BINDING_INVALID = "EXPOSURE_GATE_ACCOUNT_BINDING_INVALID"
    API_KEY_FIELD_INVALID = "EXPOSURE_GATE_API_KEY_FIELD_INVALID"
    IIBAN_FIELD_INVALID = "EXPOSURE_GATE_IIBAN_FIELD_INVALID"
    IIBAN_FORMAT_INVALID = "EXPOSURE_GATE_IIBAN_FORMAT_INVALID"
    API_KEY_MISMATCH = "EXPOSURE_GATE_API_KEY_MISMATCH"
    IIBAN_MISMATCH = "EXPOSURE_GATE_IIBAN_MISMATCH"
    EXPOSURE_INVALID = "EXPOSURE_GATE_EXPOSURE_INVALID"


@dataclass(frozen=True, slots=True)
class ExposureGateResult(EvidenceRecord):
    status: ExposureGateStatus
    reason_code: ExposureGateReason
    venue: VenueId
    account_identity: ContentIdentity | None
    credential_reference_identity: ContentIdentity | None
    capability_identity: ContentIdentity | None
    exposure_result_identity: ContentIdentity | None
    completed_routes: tuple[str, ...]
    total_private_network_calls: int
    started_at: datetime
    completed_at: datetime
    source_commit_sha: str | None
    source_tree_sha: str | None
    repository_identity: ContentIdentity | None
    source_identity: ContentIdentity | None
    level: str = "EXPOSURE_OPERATOR_GATE"
    real_economic_calls: int = 0
    runtime_pass_qualified: bool = False
    live: str = "LIVE_FORBIDDEN"
    side_effect_performed: bool = False

    def __post_init__(self) -> None:
        expected_routes = (
            "/0/private/GetApiKeyInfo",
            "/0/private/BalanceEx",
            "/0/private/TradeVolume",
        )
        passed = self.status is ExposureGateStatus.PASSED
        source_valid = (
            type(self.source_commit_sha) is str
            and re.fullmatch(r"[0-9a-f]{40}", self.source_commit_sha) is not None
            and type(self.source_tree_sha) is str
            and re.fullmatch(r"[0-9a-f]{40}", self.source_tree_sha) is not None
            and type(self.repository_identity) is ContentIdentity
            and type(self.source_identity) is ContentIdentity
        )
        if (
            type(self.status) is not ExposureGateStatus
            or type(self.reason_code) is not ExposureGateReason
            or self.venue is not VenueId.KRAKEN
            or self.level != "EXPOSURE_OPERATOR_GATE"
            or type(self.total_private_network_calls) is not int
            or not 0 <= self.total_private_network_calls <= 3
            or self.completed_routes != expected_routes[: len(self.completed_routes)]
            or len(self.completed_routes) > self.total_private_network_calls
            or type(self.started_at) is not datetime
            or self.started_at.tzinfo is None
            or type(self.completed_at) is not datetime
            or self.completed_at.tzinfo is None
            or self.completed_at < self.started_at
            or self.real_economic_calls != 0
            or self.runtime_pass_qualified is not False
            or self.live != "LIVE_FORBIDDEN"
            or self.side_effect_performed is not False
            or (
                passed
                and (
                    self.reason_code is not ExposureGateReason.QUALIFIED
                    or self.total_private_network_calls != 3
                    or self.completed_routes != expected_routes
                    or not source_valid
                    or any(
                        type(value) is not ContentIdentity
                        for value in (
                            self.account_identity,
                            self.credential_reference_identity,
                            self.capability_identity,
                            self.exposure_result_identity,
                        )
                    )
                )
            )
            or (not passed and self.reason_code is ExposureGateReason.QUALIFIED)
        ):
            raise EvidenceError("EXPOSURE_GATE_RESULT_INVALID")
        EvidenceRecord.__post_init__(self)
