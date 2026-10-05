"""Fail-closed Kraken public qualification."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from atp.exchange.contracts import (
    BTC_EUR,
    CanonicalInstrumentId,
    PublicCandleEvidence,
    PublicInstrumentMetadata,
    PublicPriceEvidence,
    PublicServerTimeEvidence,
    PublicSystemStatusEvidence,
    VenueId,
    VenueInstrumentMappingEvidence,
)
from atp.exchange.kraken import KRAKEN_PUBLIC_ROUTE_ALLOWLIST, KrakenPublicClient
from atp.exchange.read_only import EvidenceError, verify_record
from atp.kraken_qualification.model import (
    KrakenQualificationLevel,
    KrakenQualificationReason,
    KrakenQualificationResult,
    KrakenQualificationStatus,
)
from atp.release_deployment.model import ReleaseError, SourceTree
from atp.release_deployment.source import inspect_source
from atp.shared.identity import ContentIdentity


@dataclass(frozen=True, slots=True)
class _Evaluation:
    """Internal evaluation only: not a serializable qualification evidence record."""

    level: KrakenQualificationLevel
    status: KrakenQualificationStatus
    reason_code: KrakenQualificationReason
    venue: VenueId
    instrument_identity: ContentIdentity
    mapping_identity: ContentIdentity | None
    evidence_identities: tuple[ContentIdentity, ...]
    route_allowlist: tuple[str, ...]


def _bind(result: _Evaluation, source: SourceTree | None) -> KrakenQualificationResult:
    return KrakenQualificationResult(
        result.level,
        result.status,
        result.reason_code,
        result.venue,
        result.instrument_identity,
        result.mapping_identity,
        result.evidence_identities,
        result.route_allowlist,
        source_commit_sha=None if source is None else source.source_commit_sha,
        source_tree_sha=None if source is None else source.git_tree_sha,
        repository_identity=None if source is None else source.repository_identity,
        source_identity=None if source is None else source.content_identity,
    )


def _result(
    level: KrakenQualificationLevel,
    status: KrakenQualificationStatus,
    reason: KrakenQualificationReason,
    instrument: CanonicalInstrumentId,
    mapping: VenueInstrumentMappingEvidence | None = None,
    evidence: tuple[object, ...] = (),
) -> _Evaluation:
    identities = tuple(
        item.content_identity for item in evidence if hasattr(item, "content_identity")
    )
    return _Evaluation(
        level,
        status,
        reason,
        VenueId.KRAKEN,
        instrument.content_identity,
        mapping.content_identity if mapping is not None else None,
        identities,
        tuple(sorted(KRAKEN_PUBLIC_ROUTE_ALLOWLIST)),
    )


def _qualify_offline(
    instrument: object,
    mapping: object,
    metadata: object,
    server_time: object,
    system_status: object,
    candles: object,
    price: object,
) -> _Evaluation:
    if not verify_record(instrument, CanonicalInstrumentId) or not isinstance(
        instrument, CanonicalInstrumentId
    ):
        return _result(
            KrakenQualificationLevel.OFFLINE_CONTRACT,
            KrakenQualificationStatus.FAILED,
            KrakenQualificationReason.KRAKEN_EVIDENCE_INVALID,
            BTC_EUR,
        )
    records = (
        (mapping, VenueInstrumentMappingEvidence),
        (metadata, PublicInstrumentMetadata),
        (server_time, PublicServerTimeEvidence),
        (system_status, PublicSystemStatusEvidence),
        (candles, PublicCandleEvidence),
        (price, PublicPriceEvidence),
    )
    if any(not verify_record(value, expected) for value, expected in records) or not isinstance(
        mapping, VenueInstrumentMappingEvidence
    ):
        return _result(
            KrakenQualificationLevel.OFFLINE_CONTRACT,
            KrakenQualificationStatus.FAILED,
            KrakenQualificationReason.KRAKEN_EVIDENCE_INVALID,
            instrument,
        )
    evidence = (mapping, metadata, server_time, system_status, candles, price)
    if (
        any(getattr(value, "venue", VenueId.KRAKEN) is not VenueId.KRAKEN for value in evidence)
        or any(
            getattr(value, "instrument", instrument) != instrument
            for value in (mapping, metadata, candles, price)
        )
        or any(
            getattr(value, "mapping_identity", mapping.content_identity) != mapping.content_identity
            for value in (metadata, candles, price)
        )
        or not isinstance(system_status, PublicSystemStatusEvidence)
        or system_status.status != "online"
        or not isinstance(candles, PublicCandleEvidence)
        or not candles.candles
        or not isinstance(price, PublicPriceEvidence)
        or price.source != "KRAKEN_TICKER_LAST_TRADE"
        or price.freshness != "OBSERVATION_FRESH"
        or not isinstance(server_time, PublicServerTimeEvidence)
        or abs((server_time.server_time - server_time.observed_at).total_seconds()) > 15
        or any(
            abs(
                (
                    price.observed_at - getattr(value, "observed_at", price.observed_at)
                ).total_seconds()
            )
            > 60
            for value in evidence
        )
        or not isinstance(metadata, PublicInstrumentMetadata)
        or mapping.metadata_identity != metadata.source_identity
        or mapping.observed_at != metadata.observed_at
        or mapping.instrument_status != metadata.status
        or mapping.instrument_status != "online"
    ):
        reason = (
            KrakenQualificationReason.KRAKEN_SYSTEM_NOT_ONLINE
            if isinstance(system_status, PublicSystemStatusEvidence)
            and system_status.status != "online"
            else KrakenQualificationReason.KRAKEN_EVIDENCE_INVALID
        )
        return _result(
            KrakenQualificationLevel.OFFLINE_CONTRACT,
            KrakenQualificationStatus.FAILED,
            reason,
            instrument,
            mapping,
            evidence,
        )
    return _result(
        KrakenQualificationLevel.OFFLINE_CONTRACT,
        KrakenQualificationStatus.PASSED,
        KrakenQualificationReason.KRAKEN_PUBLIC_QUALIFIED,
        instrument,
        mapping,
        evidence,
    )


def _qualify_public_connectivity(
    client: KrakenPublicClient,
    *,
    instrument: CanonicalInstrumentId = BTC_EUR,
) -> _Evaluation:
    """Explicit network path. Ordinary tests never invoke this function with HTTP transport."""
    try:
        server_time = client.server_time()
        status = client.system_status()
        mapping, metadata = client.instrument_metadata(instrument)
        candles = client.closed_candles(instrument, mapping, 5)
        price = client.price(instrument, mapping)
        offline = _qualify_offline(
            instrument, mapping, metadata, server_time, status, candles, price
        )
        return _result(
            KrakenQualificationLevel.PUBLIC_CONNECTIVITY,
            offline.status,
            offline.reason_code,
            instrument,
            mapping,
            (mapping, metadata, server_time, status, candles, price),
        )
    except (EvidenceError, OSError, ValueError, TypeError):
        return _result(
            KrakenQualificationLevel.PUBLIC_CONNECTIVITY,
            KrakenQualificationStatus.FAILED,
            KrakenQualificationReason.KRAKEN_PUBLIC_UNAVAILABLE,
            instrument,
        )


def _source_bound(
    root: Path, level: KrakenQualificationLevel, run: Callable[[], _Evaluation]
) -> KrakenQualificationResult:
    try:
        before = inspect_source(root)
        if not before.clean:
            raise ValueError("dirty source")
        result = run()
        after = inspect_source(root)
        if before != after or not after.clean:
            raise ValueError("source changed")
        return _bind(result, before)
    except (ReleaseError, OSError, ValueError):
        return _bind(
            _result(
                level,
                KrakenQualificationStatus.FAILED,
                KrakenQualificationReason.KRAKEN_SOURCE_INVALID,
                BTC_EUR,
            ),
            None,
        )


def qualify_offline(
    instrument: object,
    mapping: object,
    metadata: object,
    server_time: object,
    system_status: object,
    candles: object,
    price: object,
    *,
    source_root: Path | None = None,
) -> KrakenQualificationResult:
    return _source_bound(
        Path.cwd() if source_root is None else source_root,
        KrakenQualificationLevel.OFFLINE_CONTRACT,
        lambda: _qualify_offline(
            instrument, mapping, metadata, server_time, system_status, candles, price
        ),
    )


def qualify_public_connectivity(
    client: KrakenPublicClient,
    *,
    instrument: CanonicalInstrumentId = BTC_EUR,
    source_root: Path | None = None,
) -> KrakenQualificationResult:
    return _source_bound(
        Path.cwd() if source_root is None else source_root,
        KrakenQualificationLevel.PUBLIC_CONNECTIVITY,
        lambda: _qualify_public_connectivity(client, instrument=instrument),
    )
