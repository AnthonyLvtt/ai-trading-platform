"""Pure projection of public exchange candles into canonical ATP DATA records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from atp.data.identity import DatasetId, SnapshotId, SourceId
from atp.data.lineage import DataLineage, LineageStep
from atp.data.snapshot import (
    DataFinality,
    DataPoint,
    DataQuality,
    DatasetSnapshot,
    FreshnessStatus,
    GapStatus,
)
from atp.data.temporal import AvailabilityRule, TemporalMetadata
from atp.exchange.contracts import (
    BTC_EUR,
    CanonicalInstrumentId,
    PublicCandle,
    PublicCandleEvidence,
    VenueId,
    VenueInstrumentMappingEvidence,
)
from atp.exchange.read_only import EvidenceError, EvidenceRecord, verify_record
from atp.shared.environment import ACTIVE_ENVIRONMENTS, Environment
from atp.shared.identity import ContentIdentity

KRAKEN_CANDLE_DATASET_ID = DatasetId("kraken-public:BTC-EUR:SPOT:5m:CLOSED:v1")
KRAKEN_CANDLE_SOURCE_ID = SourceId("kraken-public-ohlc")
KRAKEN_CANDLE_SCHEMA_VERSION = "atp-canonical-spot-candle/1.0"
KRAKEN_CANDLE_TRANSFORMATION_VERSION = "ENG-DATA-KRAKEN-001/1.0"
KRAKEN_CANDLE_AVAILABILITY_RULE = AvailabilityRule("kraken-closed-candle-observed", "1.0")
KRAKEN_CANDLE_LINEAGE_OPERATION = "kraken-public-closed-candle-ingestion"
KRAKEN_CANDLE_INTERVAL_MINUTES = 5


@dataclass(frozen=True, slots=True)
class ClosedCandleProvenance(EvidenceRecord):
    """Input-only provenance. It never depends on an output DATA identity."""

    venue: VenueId
    instrument: CanonicalInstrumentId
    interval_minutes: int
    mapping_identity: ContentIdentity
    candle_evidence_identity: ContentIdentity
    source_identity: ContentIdentity
    observed_at: datetime
    transformation_version: str
    availability_rule_identity: ContentIdentity

    def __post_init__(self) -> None:
        if (
            self.venue is not VenueId.KRAKEN
            or self.instrument != BTC_EUR
            or self.interval_minutes != KRAKEN_CANDLE_INTERVAL_MINUTES
            or type(self.mapping_identity) is not ContentIdentity
            or type(self.candle_evidence_identity) is not ContentIdentity
            or type(self.source_identity) is not ContentIdentity
            or type(self.observed_at) is not datetime
            or self.observed_at.utcoffset() != timedelta(0)
            or self.transformation_version != KRAKEN_CANDLE_TRANSFORMATION_VERSION
            or self.availability_rule_identity
            != ContentIdentity.from_canonical(
                {
                    "rule_id": KRAKEN_CANDLE_AVAILABILITY_RULE.rule_id,
                    "version": KRAKEN_CANDLE_AVAILABILITY_RULE.version,
                }
            )
        ):
            raise EvidenceError("INVALID_CLOSED_CANDLE_PROVENANCE")
        EvidenceRecord.__post_init__(self)


@dataclass(frozen=True, slots=True)
class CanonicalClosedCandleBatch:
    provenance: ClosedCandleProvenance
    snapshot: DatasetSnapshot


def _valid_candle(candle: object) -> bool:
    if not verify_record(candle, PublicCandle) or not isinstance(candle, PublicCandle):
        return False
    prices = (candle.open, candle.high, candle.low, candle.close)
    return (
        candle.interval_minutes == KRAKEN_CANDLE_INTERVAL_MINUTES
        and candle.open_time.utcoffset() == timedelta(0)
        and candle.open_time.timestamp() % 300 == 0
        and all(type(value) is Decimal and value.is_finite() and value > 0 for value in prices)
        and candle.low <= min(candle.open, candle.close)
        and candle.high >= max(candle.open, candle.close)
        and type(candle.volume) is Decimal
        and candle.volume.is_finite()
        and candle.volume >= 0
        and type(candle.trade_count) is int
        and candle.trade_count >= 0
    )


def _payload(candle: PublicCandle) -> dict[str, object]:
    return {
        "close": str(candle.close),
        "high": str(candle.high),
        "instrument": {
            "base_asset": BTC_EUR.base_asset,
            "market": BTC_EUR.market.value,
            "quote_asset": BTC_EUR.quote_asset,
        },
        "interval": "5m",
        "low": str(candle.low),
        "open": str(candle.open),
        "open_time": candle.open_time.isoformat(),
        "trade_count": candle.trade_count,
        "venue": VenueId.KRAKEN.value,
        "volume": str(candle.volume),
    }


def _snapshot_id(
    *,
    environment: Environment,
    provenance: ClosedCandleProvenance,
    lineage: DataLineage,
    points: tuple[DataPoint, ...],
) -> SnapshotId:
    manifest = {
        "dataset_id": str(KRAKEN_CANDLE_DATASET_ID),
        "environment": environment.value,
        "lineage_identity": str(lineage.content_identity),
        "points": [point.canonical_value() for point in points],
        "provenance_identity": str(provenance.content_identity),
        "schema_version": KRAKEN_CANDLE_SCHEMA_VERSION,
        "transformation_version": KRAKEN_CANDLE_TRANSFORMATION_VERSION,
    }
    return SnapshotId(str(ContentIdentity.from_canonical(manifest)))


def ingest_closed_public_candles(
    *,
    selected_venue: VenueId,
    instrument: CanonicalInstrumentId,
    mapping: VenueInstrumentMappingEvidence,
    candle_evidence: PublicCandleEvidence,
    interval_minutes: int,
    environment: Environment,
) -> CanonicalClosedCandleBatch:
    """Validate and project one already-acquired Kraken batch without network access."""

    if (
        type(selected_venue) is not VenueId
        or selected_venue is not VenueId.KRAKEN
        or instrument != BTC_EUR
        or type(environment) is not Environment
        or environment not in ACTIVE_ENVIRONMENTS
        or interval_minutes != KRAKEN_CANDLE_INTERVAL_MINUTES
        or not verify_record(mapping, VenueInstrumentMappingEvidence)
        or not verify_record(candle_evidence, PublicCandleEvidence)
        or mapping.venue is not selected_venue
        or mapping.instrument != instrument
        or mapping.instrument_status != "online"
        or candle_evidence.venue is not selected_venue
        or candle_evidence.instrument != instrument
        or candle_evidence.mapping_identity != mapping.content_identity
        or candle_evidence.observed_at.utcoffset() != timedelta(0)
        or not candle_evidence.candles
        or any(not _valid_candle(candle) for candle in candle_evidence.candles)
    ):
        raise EvidenceError("KRAKEN_CANDLE_INGESTION_INVALID")

    step = timedelta(minutes=KRAKEN_CANDLE_INTERVAL_MINUTES)
    candles = candle_evidence.candles
    if any(
        right.open_time - left.open_time != step
        for left, right in zip(candles, candles[1:], strict=False)
    ):
        raise EvidenceError("KRAKEN_CANDLE_INGESTION_INVALID")
    latest_close = candles[-1].open_time + step
    if not timedelta(0) <= candle_evidence.observed_at - latest_close < step:
        raise EvidenceError("KRAKEN_CANDLE_INGESTION_INVALID")

    rule_identity = ContentIdentity.from_canonical(
        {
            "rule_id": KRAKEN_CANDLE_AVAILABILITY_RULE.rule_id,
            "version": KRAKEN_CANDLE_AVAILABILITY_RULE.version,
        }
    )
    provenance = ClosedCandleProvenance(
        selected_venue,
        instrument,
        interval_minutes,
        mapping.content_identity,
        candle_evidence.content_identity,
        candle_evidence.source_identity,
        candle_evidence.observed_at,
        KRAKEN_CANDLE_TRANSFORMATION_VERSION,
        rule_identity,
    )
    lineage = DataLineage(
        (
            LineageStep(
                KRAKEN_CANDLE_LINEAGE_OPERATION,
                KRAKEN_CANDLE_TRANSFORMATION_VERSION,
                (provenance.content_identity,),
            ),
        )
    )
    points = tuple(
        DataPoint.from_value(
            symbol=instrument.symbol,
            value=_payload(candle),
            temporal=TemporalMetadata.derived(
                event_time=candle.open_time + step,
                provider_time=candle.open_time,
                ingested_at=candle_evidence.observed_at,
                rule=KRAKEN_CANDLE_AVAILABILITY_RULE,
            ),
            finality=DataFinality.FINAL,
        )
        for candle in candles
    )
    snapshot = DatasetSnapshot.create(
        dataset_id=KRAKEN_CANDLE_DATASET_ID,
        snapshot_id=_snapshot_id(
            environment=environment,
            provenance=provenance,
            lineage=lineage,
            points=points,
        ),
        source_id=KRAKEN_CANDLE_SOURCE_ID,
        environment=environment,
        schema_version=KRAKEN_CANDLE_SCHEMA_VERSION,
        transformation_version=KRAKEN_CANDLE_TRANSFORMATION_VERSION,
        created_at=candle_evidence.observed_at,
        points=points,
        quality=DataQuality.VALID,
        freshness=FreshnessStatus.FRESH,
        gap_status=GapStatus.NO_GAP_DETECTED,
        gaps=(),
        degradation_reasons=frozenset(),
        lineage=lineage,
    )
    return CanonicalClosedCandleBatch(provenance, snapshot)
