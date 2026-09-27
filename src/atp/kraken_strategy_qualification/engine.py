"""Source-bound OFFLINE qualification of canonical Kraken DATA through Strategy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from atp.data import DatasetSnapshot, SnapshotId, UniverseSnapshotId
from atp.exchange.contracts import BTC_EUR, VenueId
from atp.exchange.read_only import EvidenceError
from atp.kraken_strategy import compose_kraken_universe, evaluate_kraken_strategy
from atp.kraken_strategy_qualification.model import (
    KrakenStrategyQualificationLevel,
    KrakenStrategyQualificationReason,
    KrakenStrategyQualificationResult,
    KrakenStrategyQualificationStatus,
)
from atp.release_deployment.model import ReleaseError, SourceTree
from atp.release_deployment.source import inspect_source
from atp.shared.environment import Environment
from atp.shared.identity import ContentIdentity
from atp.strategy import EvaluationStatus, SignalKind, StrategyEvaluationId


@dataclass(frozen=True, slots=True)
class _Evaluation:
    status: KrakenStrategyQualificationStatus
    reason: KrakenStrategyQualificationReason
    environment: Environment
    snapshot_id: SnapshotId | None = None
    snapshot_content_identity: ContentIdentity | None = None
    universe_snapshot_id: UniverseSnapshotId | None = None
    universe_content_identity: ContentIdentity | None = None
    strategy_provenance_identity: ContentIdentity | None = None
    strategy_evaluation_id: StrategyEvaluationId | None = None
    strategy_evaluation_identity: ContentIdentity | None = None
    signal_kind: SignalKind | None = None


def _failed(reason: KrakenStrategyQualificationReason, environment: Environment) -> _Evaluation:
    return _Evaluation(KrakenStrategyQualificationStatus.FAILED, reason, environment)


def _evaluate(snapshot: DatasetSnapshot) -> _Evaluation:
    try:
        universe = compose_kraken_universe(snapshot)
        evaluation = evaluate_kraken_strategy(snapshot, universe)
        if evaluation.status is not EvaluationStatus.COMPLETED or evaluation.signal is None:
            raise EvidenceError("KRAKEN_STRATEGY_EVALUATION_BLOCKED")
        return _Evaluation(
            KrakenStrategyQualificationStatus.PASSED,
            KrakenStrategyQualificationReason.KRAKEN_STRATEGY_QUALIFIED,
            snapshot.environment,
            snapshot.snapshot_id,
            snapshot.content_identity,
            universe.universe_snapshot_id,
            universe.content_identity,
            evaluation.provenance.content_identity,
            evaluation.strategy_evaluation_id,
            evaluation.content_identity,
            evaluation.signal.kind,
        )
    except (EvidenceError, TypeError, ValueError):
        return _failed(
            KrakenStrategyQualificationReason.KRAKEN_STRATEGY_INPUT_INVALID,
            snapshot.environment,
        )


def _result(
    evaluation: _Evaluation,
    source: SourceTree | None,
) -> KrakenStrategyQualificationResult:
    return KrakenStrategyQualificationResult(
        KrakenStrategyQualificationLevel.OFFLINE_CONTRACT,
        evaluation.status,
        evaluation.reason,
        VenueId.KRAKEN,
        evaluation.environment,
        BTC_EUR.content_identity,
        evaluation.snapshot_id,
        evaluation.snapshot_content_identity,
        evaluation.universe_snapshot_id,
        evaluation.universe_content_identity,
        evaluation.strategy_provenance_identity,
        evaluation.strategy_evaluation_id,
        evaluation.strategy_evaluation_identity,
        evaluation.signal_kind,
        source_commit_sha=None if source is None else source.source_commit_sha,
        source_tree_sha=None if source is None else source.git_tree_sha,
        repository_identity=None if source is None else source.repository_identity,
        source_identity=None if source is None else source.content_identity,
    )


def qualify_offline(
    snapshot: DatasetSnapshot,
    *,
    source_root: Path | None = None,
) -> KrakenStrategyQualificationResult:
    root = Path.cwd() if source_root is None else source_root
    try:
        before = inspect_source(root)
        if not before.clean:
            raise ValueError("dirty source")
        evaluation = _evaluate(snapshot)
        after = inspect_source(root)
        if before != after or not after.clean:
            raise ValueError("source changed")
        return _result(evaluation, before)
    except (ReleaseError, OSError, ValueError):
        return _result(
            _failed(
                KrakenStrategyQualificationReason.KRAKEN_STRATEGY_SOURCE_INVALID,
                snapshot.environment,
            ),
            None,
        )
