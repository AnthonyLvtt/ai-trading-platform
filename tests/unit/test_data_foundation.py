from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from atp.data import (
    AvailabilityRule,
    BackfillEvidence,
    ConsumerContract,
    DataFinality,
    DataLineage,
    DataPoint,
    DataQuality,
    DatasetId,
    DatasetSnapshot,
    FreshnessStatus,
    Gap,
    GapStatus,
    LineageStep,
    SnapshotId,
    SourceId,
    SymbolDecision,
    TemporalMetadata,
    UniverseSnapshot,
    UniverseSnapshotId,
    assert_snapshot_consistent,
    build_historical_view,
)
from atp.shared.environment import Environment
from atp.shared.errors import DomainError, ValidationError
from atp.shared.identity import ContentIdentity

T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = T0 + timedelta(minutes=1)
T2 = T0 + timedelta(minutes=2)
T3 = T0 + timedelta(minutes=3)


def temporal(
    *, event: datetime = T0, ingested: datetime = T1, available: datetime = T1
) -> TemporalMetadata:
    return TemporalMetadata(
        event_time=event,
        provider_time=event,
        ingested_at=ingested,
        available_at=available,
    )


def point(
    *,
    symbol: str = "BTCUSDT",
    close: str = "100.00",
    timing: TemporalMetadata | None = None,
) -> DataPoint:
    return DataPoint.from_value(
        symbol=symbol,
        value={"close": close},
        temporal=timing or temporal(),
        finality=DataFinality.FINAL,
    )


def lineage() -> DataLineage:
    return DataLineage((LineageStep("normalize", "v1"),))


def snapshot(
    *,
    snapshot_id: str = "snapshot-1",
    points: tuple[DataPoint, ...] | None = None,
    quality: DataQuality = DataQuality.VALID,
    freshness: FreshnessStatus = FreshnessStatus.FRESH,
    gap_status: GapStatus = GapStatus.NO_GAP_DETECTED,
    gaps: tuple[Gap, ...] = (),
    degradation_reasons: frozenset[str] = frozenset(),
) -> DatasetSnapshot:
    return DatasetSnapshot.create(
        dataset_id=DatasetId("candles-btcusdt:v1"),
        snapshot_id=SnapshotId(snapshot_id),
        source_id=SourceId("historical-fixture"),
        environment=Environment.BACKTEST,
        schema_version="candles-v1",
        transformation_version="normalize-v1",
        created_at=T2,
        points=points or (point(),),
        quality=quality,
        freshness=freshness,
        gap_status=gap_status,
        gaps=gaps,
        degradation_reasons=degradation_reasons,
        lineage=lineage(),
    )


def universe(
    *,
    effective_at: datetime = T1,
    decisions: tuple[SymbolDecision, ...] | None = None,
) -> UniverseSnapshot:
    return UniverseSnapshot.create(
        universe_snapshot_id=UniverseSnapshotId("universe-1"),
        created_at=T2,
        effective_at=effective_at,
        rules_version="spot-usdt-v1",
        source_snapshot_ids=(SnapshotId("snapshot-1"),),
        decisions=decisions or (SymbolDecision("BTCUSDT", True, "historically eligible", T1),),
    )


def valid_contract() -> ConsumerContract:
    return ConsumerContract(
        accepted_quality=frozenset({DataQuality.VALID}),
        accepted_freshness=frozenset({FreshnessStatus.FRESH}),
    )


def test_data_identifiers_are_distinct_types() -> None:
    assert DatasetId("same") != SnapshotId("same")
    assert SnapshotId("same") != UniverseSnapshotId("same")
    assert SourceId("same") != DatasetId("same")


@pytest.mark.parametrize("value", ["", " value", "value ", "e\u0301"])
def test_data_identifiers_reject_empty_or_non_normalized_values(value: str) -> None:
    with pytest.raises(ValidationError):
        DatasetId(value)


def test_temporal_metadata_requires_utc() -> None:
    with pytest.raises(ValidationError, match="UTC"):
        temporal(event=datetime(2026, 1, 1))


def test_available_at_cannot_precede_ingestion_or_event() -> None:
    with pytest.raises(ValidationError, match="available_at"):
        temporal(event=T1, ingested=T2, available=T0)


def test_availability_derivation_is_deterministic_and_traceable() -> None:
    rule = AvailabilityRule("available-on-ingestion", "v1")

    first = TemporalMetadata.derived(
        event_time=T0,
        provider_time=T0,
        ingested_at=T1,
        rule=rule,
    )
    second = TemporalMetadata.derived(
        event_time=T0,
        provider_time=T0,
        ingested_at=T1,
        rule=rule,
    )

    assert first == second
    assert first.available_at == T1
    assert first.availability_rule == rule


def test_data_point_canonicalizes_logically_equal_payloads() -> None:
    left = DataPoint.from_value(
        symbol="BTCUSDT",
        value={"close": "100", "volume": "2"},
        temporal=temporal(),
        finality=DataFinality.FINAL,
    )
    right = DataPoint.from_value(
        symbol="BTCUSDT",
        value={"volume": "2", "close": "100"},
        temporal=temporal(),
        finality=DataFinality.FINAL,
    )

    assert left.canonical_payload == right.canonical_payload
    assert left.content_identity == right.content_identity


def test_data_point_rejects_noncanonical_payload_bytes() -> None:
    with pytest.raises(ValidationError, match="canonical JSON"):
        DataPoint(
            symbol="BTCUSDT",
            canonical_payload=b'{"volume": 2, "close": 100}',
            temporal=temporal(),
            finality=DataFinality.FINAL,
        )


def test_snapshot_content_identity_is_deterministic() -> None:
    assert snapshot().content_identity == snapshot().content_identity


def test_snapshot_content_identity_changes_with_content() -> None:
    assert (
        snapshot(points=(point(close="100"),)).content_identity
        != snapshot(points=(point(close="101"),)).content_identity
    )


def test_snapshot_order_is_canonical_not_arrival_order() -> None:
    first = point(symbol="ETHUSDT")
    second = point(symbol="BTCUSDT")

    assert (
        snapshot(points=(first, second)).content_identity
        == snapshot(points=(second, first)).content_identity
    )


def test_same_snapshot_id_with_different_content_is_contradiction() -> None:
    existing = snapshot(points=(point(close="100"),))
    candidate = snapshot(points=(point(close="101"),))

    with pytest.raises(DomainError, match="contradictory"):
        assert_snapshot_consistent(existing, candidate)


def test_same_snapshot_id_with_different_immutable_manifest_is_contradiction() -> None:
    existing = snapshot()
    candidate = DatasetSnapshot.create(
        dataset_id=existing.dataset_id,
        snapshot_id=existing.snapshot_id,
        source_id=existing.source_id,
        environment=existing.environment,
        schema_version=existing.schema_version,
        transformation_version="normalize-v2",
        created_at=existing.created_at,
        points=existing.points,
        quality=existing.quality,
        freshness=existing.freshness,
        gap_status=existing.gap_status,
        gaps=existing.gaps,
        degradation_reasons=existing.degradation_reasons,
        lineage=existing.lineage,
    )

    with pytest.raises(DomainError, match="immutable manifest"):
        assert_snapshot_consistent(existing, candidate)


def test_snapshot_rejects_forged_content_identity() -> None:
    valid = snapshot()

    with pytest.raises(ValidationError, match="does not match"):
        DatasetSnapshot(
            dataset_id=valid.dataset_id,
            snapshot_id=valid.snapshot_id,
            content_identity=ContentIdentity.from_text("forged"),
            source_id=valid.source_id,
            environment=valid.environment,
            schema_version=valid.schema_version,
            transformation_version=valid.transformation_version,
            created_at=valid.created_at,
            points=valid.points,
            quality=valid.quality,
            freshness=valid.freshness,
            gap_status=valid.gap_status,
            gaps=valid.gaps,
            degradation_reasons=valid.degradation_reasons,
            lineage=valid.lineage,
            validation_as_of_use=valid.validation_as_of_use,
            current_validation_status=valid.current_validation_status,
        )


def test_quality_and_freshness_are_independent() -> None:
    fresh_invalid = snapshot(quality=DataQuality.INVALID, freshness=FreshnessStatus.FRESH)
    stale_valid = snapshot(quality=DataQuality.VALID, freshness=FreshnessStatus.STALE)

    assert fresh_invalid.quality is DataQuality.INVALID
    assert fresh_invalid.freshness is FreshnessStatus.FRESH
    assert stale_valid.quality is DataQuality.VALID
    assert stale_valid.freshness is FreshnessStatus.STALE


def test_degraded_data_requires_explicit_reason() -> None:
    with pytest.raises(ValidationError, match="explicit reasons"):
        snapshot(quality=DataQuality.DEGRADED)


def test_degraded_data_is_fail_closed_without_explicit_consumer_permission() -> None:
    degraded = snapshot(
        quality=DataQuality.DEGRADED,
        degradation_reasons=frozenset({"known-gap"}),
    )
    denied = ConsumerContract(
        accepted_quality=frozenset({DataQuality.DEGRADED}),
        accepted_freshness=frozenset({FreshnessStatus.FRESH}),
    )
    allowed = ConsumerContract(
        accepted_quality=frozenset({DataQuality.DEGRADED}),
        accepted_freshness=frozenset({FreshnessStatus.FRESH}),
        allowed_degradations=frozenset({"known-gap"}),
    )

    assert not denied.accepts(degraded)
    assert allowed.accepts(degraded)


def test_known_gaps_are_explicit_and_preserved() -> None:
    known_gap = Gap(T0, T1, "missing candle")
    result = snapshot(
        quality=DataQuality.DEGRADED,
        gap_status=GapStatus.KNOWN_GAP,
        gaps=(known_gap,),
        degradation_reasons=frozenset({"known-gap"}),
    )

    assert result.gap_status is GapStatus.KNOWN_GAP
    assert result.gaps == (known_gap,)


def test_unknown_gap_status_is_not_demonstrated_continuity() -> None:
    result = snapshot(gap_status=GapStatus.GAP_STATUS_UNKNOWN)

    assert result.gap_status is not GapStatus.NO_GAP_DETECTED
    assert result.gaps == ()


def test_late_invalidation_preserves_validation_as_of_use() -> None:
    used = snapshot()
    invalidated = used.with_current_validation(DataQuality.INVALID)

    assert invalidated.validation_as_of_use is DataQuality.VALID
    assert invalidated.current_validation_status is DataQuality.INVALID
    assert invalidated.content_identity == used.content_identity


def test_lineage_identity_is_reproducible() -> None:
    parent = ContentIdentity.from_text("raw")
    first = DataLineage((LineageStep("normalize", "v1", (parent,)),))
    second = DataLineage((LineageStep("normalize", "v1", (parent,)),))

    assert first.content_identity == second.content_identity


def test_lineage_identity_changes_with_transformation_version() -> None:
    first = DataLineage((LineageStep("normalize", "v1"),))
    second = DataLineage((LineageStep("normalize", "v2"),))

    assert first.content_identity != second.content_identity


def test_backfill_improves_current_completeness_without_rewriting_history() -> None:
    evidence = BackfillEvidence(
        point=point(timing=temporal(event=T0, ingested=T3, available=T3)),
        backfilled_at=T3,
        reason="repair known gap",
        historical_available_at=None,
    )

    assert evidence.current_dataset_complete
    assert not evidence.was_historically_available_at(T1)
    assert evidence.historical_available_at is None


def test_backfill_rejects_rewritten_historical_availability() -> None:
    with pytest.raises(ValidationError, match="preserve"):
        BackfillEvidence(
            point=point(timing=temporal(event=T0, ingested=T2, available=T2)),
            backfilled_at=T3,
            reason="repair known gap",
            historical_available_at=T1,
        )


def test_universe_snapshot_is_immutable_and_deterministic() -> None:
    left = universe(
        decisions=(
            SymbolDecision("ETHUSDT", False, "not eligible", T1),
            SymbolDecision("BTCUSDT", True, "eligible", T1),
        )
    )
    right = universe(
        decisions=(
            SymbolDecision("BTCUSDT", True, "eligible", T1),
            SymbolDecision("ETHUSDT", False, "not eligible", T1),
        )
    )

    assert left.content_identity == right.content_identity
    assert left.eligible_symbols == frozenset({"BTCUSDT"})
    with pytest.raises(FrozenInstanceError):
        left.effective_at = T2  # type: ignore[misc]


@pytest.mark.parametrize("eligible", [True, False])
def test_universe_rejects_future_inclusion_or_exclusion_evidence(eligible: bool) -> None:
    decision = SymbolDecision("BTCUSDT", eligible, "future evidence", T2)

    with pytest.raises(ValidationError, match="unavailable"):
        universe(effective_at=T1, decisions=(decision,))


def test_historical_view_hides_data_not_yet_available() -> None:
    early = point(close="100", timing=temporal(event=T0, ingested=T1, available=T1))
    future = point(close="101", timing=temporal(event=T1, ingested=T2, available=T2))

    result = build_historical_view(
        snapshot=snapshot(points=(early, future)),
        universe=universe(),
        as_of=T1,
        contract=valid_contract(),
    )

    assert result.points == (early,)


def test_historical_view_rejects_future_universe() -> None:
    with pytest.raises(DomainError, match="future universe"):
        build_historical_view(
            snapshot=snapshot(),
            universe=universe(effective_at=T2),
            as_of=T1,
            contract=valid_contract(),
        )


def test_historical_view_rejects_invalid_or_stale_snapshot() -> None:
    for unusable in (
        snapshot(quality=DataQuality.INVALID),
        snapshot(freshness=FreshnessStatus.STALE),
    ):
        with pytest.raises(DomainError, match="consumer contract"):
            build_historical_view(
                snapshot=unusable,
                universe=universe(),
                as_of=T1,
                contract=valid_contract(),
            )
