"""Source-bound qualification of Kraken public candles through canonical ATP DATA."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from atp.data import ConsumerContract, DataFinality, DataQuality, FreshnessStatus, GapStatus
from atp.data.identity import SnapshotId
from atp.data.market_data import ingest_closed_public_candles
from atp.exchange.contracts import (
    BTC_EUR,
    CanonicalInstrumentId,
    PublicCandleEvidence,
    PublicServerTimeEvidence,
    PublicSystemStatusEvidence,
    SelectedPublicExchange,
    VenueId,
    VenueInstrumentMappingEvidence,
)
from atp.exchange.kraken import KrakenPublicClient
from atp.exchange.read_only import EvidenceError, verify_record
from atp.kraken_market_data_qualification.model import (
    KRAKEN_MARKET_DATA_ROUTES,
    KrakenMarketDataQualificationLevel,
    KrakenMarketDataQualificationReason,
    KrakenMarketDataQualificationResult,
    KrakenMarketDataQualificationStatus,
)
from atp.release_deployment.model import ReleaseError, SourceTree
from atp.release_deployment.source import inspect_source
from atp.shared.environment import Environment
from atp.shared.identity import ContentIdentity


@dataclass(frozen=True, slots=True)
class _Evaluation:
    level: KrakenMarketDataQualificationLevel
    status: KrakenMarketDataQualificationStatus
    reason_code: KrakenMarketDataQualificationReason
    environment: Environment
    instrument_identity: ContentIdentity
    mapping_identity: ContentIdentity | None = None
    provenance_identity: ContentIdentity | None = None
    snapshot_id: SnapshotId | None = None
    snapshot_content_identity: ContentIdentity | None = None
    lineage_identity: ContentIdentity | None = None
    evidence_identities: tuple[ContentIdentity, ...] = ()


def _failed(
    level: KrakenMarketDataQualificationLevel,
    reason: KrakenMarketDataQualificationReason,
    environment: Environment,
) -> _Evaluation:
    return _Evaluation(
        level,
        KrakenMarketDataQualificationStatus.FAILED,
        reason,
        environment,
        BTC_EUR.content_identity,
    )


def _evaluate(
    *,
    level: KrakenMarketDataQualificationLevel,
    environment: Environment,
    instrument: CanonicalInstrumentId,
    mapping: VenueInstrumentMappingEvidence,
    candles: PublicCandleEvidence,
    additional_evidence: tuple[object, ...] = (),
) -> _Evaluation:
    try:
        batch = ingest_closed_public_candles(
            selected_venue=VenueId.KRAKEN,
            instrument=instrument,
            mapping=mapping,
            candle_evidence=candles,
            interval_minutes=5,
            environment=environment,
        )
        contract = ConsumerContract(
            accepted_quality=frozenset({DataQuality.VALID}),
            accepted_freshness=frozenset({FreshnessStatus.FRESH}),
            accepted_finality=frozenset({DataFinality.FINAL}),
            accepted_gap_statuses=frozenset({GapStatus.NO_GAP_DETECTED}),
        )
        if not contract.accepts_current(batch.snapshot) or any(
            not contract.accepts_point(point) for point in batch.snapshot.points
        ):
            raise EvidenceError("KRAKEN_DATA_CONTRACT_INVALID")
        evidence = (mapping, candles, *additional_evidence)
        identities = tuple(
            value.content_identity for value in evidence if hasattr(value, "content_identity")
        )
        return _Evaluation(
            level,
            KrakenMarketDataQualificationStatus.PASSED,
            KrakenMarketDataQualificationReason.KRAKEN_DATA_QUALIFIED,
            environment,
            instrument.content_identity,
            mapping.content_identity,
            batch.provenance.content_identity,
            batch.snapshot.snapshot_id,
            batch.snapshot.content_identity,
            batch.snapshot.lineage.content_identity,
            identities,
        )
    except (EvidenceError, TypeError, ValueError):
        return _failed(
            level,
            KrakenMarketDataQualificationReason.KRAKEN_DATA_EVIDENCE_INVALID,
            environment,
        )


def _bind(
    evaluation: _Evaluation, source: SourceTree | None
) -> KrakenMarketDataQualificationResult:
    return KrakenMarketDataQualificationResult(
        evaluation.level,
        evaluation.status,
        evaluation.reason_code,
        VenueId.KRAKEN,
        evaluation.environment,
        evaluation.instrument_identity,
        evaluation.mapping_identity,
        evaluation.provenance_identity,
        evaluation.snapshot_id,
        evaluation.snapshot_content_identity,
        evaluation.lineage_identity,
        evaluation.evidence_identities,
        KRAKEN_MARKET_DATA_ROUTES,
        source_commit_sha=None if source is None else source.source_commit_sha,
        source_tree_sha=None if source is None else source.git_tree_sha,
        repository_identity=None if source is None else source.repository_identity,
        source_identity=None if source is None else source.content_identity,
    )


def _source_bound(
    source_root: Path,
    level: KrakenMarketDataQualificationLevel,
    environment: Environment,
    run: Callable[[], _Evaluation],
) -> KrakenMarketDataQualificationResult:
    try:
        before = inspect_source(source_root)
        if not before.clean:
            raise ValueError("dirty source")
        result = run()
        after = inspect_source(source_root)
        if before != after or not after.clean:
            raise ValueError("source changed")
        return _bind(result, before)
    except (ReleaseError, OSError, ValueError):
        return _bind(
            _failed(
                level,
                KrakenMarketDataQualificationReason.KRAKEN_DATA_SOURCE_INVALID,
                environment,
            ),
            None,
        )


def qualify_offline(
    instrument: CanonicalInstrumentId,
    mapping: VenueInstrumentMappingEvidence,
    candles: PublicCandleEvidence,
    *,
    environment: Environment = Environment.TEST,
    source_root: Path | None = None,
) -> KrakenMarketDataQualificationResult:
    return _source_bound(
        Path.cwd() if source_root is None else source_root,
        KrakenMarketDataQualificationLevel.OFFLINE_CONTRACT,
        environment,
        lambda: _evaluate(
            level=KrakenMarketDataQualificationLevel.OFFLINE_CONTRACT,
            environment=environment,
            instrument=instrument,
            mapping=mapping,
            candles=candles,
        ),
    )


def _public(client: KrakenPublicClient, environment: Environment) -> _Evaluation:
    try:
        selected = SelectedPublicExchange(VenueId.KRAKEN, client)
        server_time = client.server_time()
        system_status = client.system_status()
        if (
            not verify_record(server_time, PublicServerTimeEvidence)
            or server_time.venue is not VenueId.KRAKEN
            or not verify_record(system_status, PublicSystemStatusEvidence)
            or system_status.venue is not VenueId.KRAKEN
        ):
            raise EvidenceError("KRAKEN_PUBLIC_EVIDENCE_INVALID")
        if system_status.status != "online":
            return _failed(
                KrakenMarketDataQualificationLevel.PUBLIC_CONNECTIVITY,
                KrakenMarketDataQualificationReason.KRAKEN_SYSTEM_NOT_ONLINE,
                environment,
            )
        mapping, _ = client.instrument_metadata(BTC_EUR)
        selected.require_mapping(BTC_EUR, mapping)
        candles = client.closed_candles(BTC_EUR, mapping, 5)
        return _evaluate(
            level=KrakenMarketDataQualificationLevel.PUBLIC_CONNECTIVITY,
            environment=environment,
            instrument=BTC_EUR,
            mapping=mapping,
            candles=candles,
            additional_evidence=(server_time, system_status),
        )
    except (EvidenceError, OSError, TypeError, ValueError):
        return _failed(
            KrakenMarketDataQualificationLevel.PUBLIC_CONNECTIVITY,
            KrakenMarketDataQualificationReason.KRAKEN_PUBLIC_UNAVAILABLE,
            environment,
        )


def qualify_public_connectivity(
    client: KrakenPublicClient,
    *,
    environment: Environment = Environment.LOCAL,
    source_root: Path | None = None,
) -> KrakenMarketDataQualificationResult:
    return _source_bound(
        Path.cwd() if source_root is None else source_root,
        KrakenMarketDataQualificationLevel.PUBLIC_CONNECTIVITY,
        environment,
        lambda: _public(client, environment),
    )
