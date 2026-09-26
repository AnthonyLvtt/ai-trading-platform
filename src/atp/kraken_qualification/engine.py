"""Fail-closed Kraken public qualification, independent from Binance TQ."""

from datetime import UTC, datetime

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


def _result(
    level: KrakenQualificationLevel,
    status: KrakenQualificationStatus,
    reason: KrakenQualificationReason,
    instrument: CanonicalInstrumentId,
    mapping: VenueInstrumentMappingEvidence | None = None,
    evidence: tuple[object, ...] = (),
) -> KrakenQualificationResult:
    identities = tuple(
        item.content_identity for item in evidence if hasattr(item, "content_identity")
    )
    return KrakenQualificationResult(
        level,
        status,
        reason,
        VenueId.KRAKEN_SPOT,
        instrument.content_identity,
        mapping.content_identity if mapping is not None else None,
        identities,
        tuple(sorted(KRAKEN_PUBLIC_ROUTE_ALLOWLIST)),
    )


def qualify_offline(
    instrument: object,
    mapping: object,
    metadata: object,
    server_time: object,
    system_status: object,
    candles: object,
    price: object,
) -> KrakenQualificationResult:
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
        any(
            getattr(value, "venue", VenueId.KRAKEN_SPOT) is not VenueId.KRAKEN_SPOT
            for value in evidence
        )
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


def qualify_public_connectivity(
    client: KrakenPublicClient,
    *,
    instrument: CanonicalInstrumentId = BTC_EUR,
    observed_at: datetime | None = None,
) -> KrakenQualificationResult:
    """Explicit network path. Ordinary tests never invoke this function with HTTP transport."""
    at = datetime.now(UTC) if observed_at is None else observed_at
    try:
        server_time = client.server_time(at)
        status = client.system_status(at)
        mapping, metadata = client.instrument_metadata(instrument, at)
        candles = client.closed_candles(instrument, mapping, 5, at)
        price = client.price(instrument, mapping, at)
        offline = qualify_offline(
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
