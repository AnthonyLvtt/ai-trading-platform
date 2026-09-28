"""Qualification evidence for canonical Kraken DATA to Strategy evaluation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from atp.data.identity import SnapshotId, UniverseSnapshotId
from atp.exchange.contracts import BTC_EUR, VenueId
from atp.exchange.read_only import EvidenceError, EvidenceRecord
from atp.shared.environment import ACTIVE_ENVIRONMENTS, Environment
from atp.shared.identity import ContentIdentity
from atp.strategy import SignalKind, StrategyEvaluationId


class KrakenStrategyQualificationLevel(StrEnum):
    OFFLINE_CONTRACT = "OFFLINE_CONTRACT"


class KrakenStrategyQualificationStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"


class KrakenStrategyQualificationReason(StrEnum):
    KRAKEN_STRATEGY_QUALIFIED = "KRAKEN_STRATEGY_QUALIFIED"
    KRAKEN_STRATEGY_INPUT_INVALID = "KRAKEN_STRATEGY_INPUT_INVALID"
    KRAKEN_STRATEGY_SOURCE_INVALID = "KRAKEN_STRATEGY_SOURCE_INVALID"


@dataclass(frozen=True, slots=True)
class KrakenStrategyQualificationResult(EvidenceRecord):
    level: KrakenStrategyQualificationLevel
    status: KrakenStrategyQualificationStatus
    reason_code: KrakenStrategyQualificationReason
    venue: VenueId
    environment: Environment
    instrument_identity: ContentIdentity
    snapshot_id: SnapshotId | None
    snapshot_content_identity: ContentIdentity | None
    universe_snapshot_id: UniverseSnapshotId | None
    universe_content_identity: ContentIdentity | None
    strategy_provenance_identity: ContentIdentity | None
    strategy_evaluation_id: StrategyEvaluationId | None
    strategy_evaluation_identity: ContentIdentity | None
    signal_kind: SignalKind | None
    source_commit_sha: str | None = None
    source_tree_sha: str | None = None
    repository_identity: ContentIdentity | None = None
    source_identity: ContentIdentity | None = None
    release_binding: str = "NOT_ESTABLISHED_PRE_MERGE"
    real_economic_calls: int = 0
    live: str = "LIVE_FORBIDDEN"
    side_effect_performed: bool = False

    def __post_init__(self) -> None:
        passed = self.status is KrakenStrategyQualificationStatus.PASSED
        source_bound = (
            type(self.source_commit_sha) is str
            and re.fullmatch(r"[0-9a-f]{40}", self.source_commit_sha) is not None
            and type(self.source_tree_sha) is str
            and re.fullmatch(r"[0-9a-f]{40}", self.source_tree_sha) is not None
            and type(self.repository_identity) is ContentIdentity
            and type(self.source_identity) is ContentIdentity
        )
        evaluated = (
            type(self.snapshot_id) is SnapshotId
            and type(self.snapshot_content_identity) is ContentIdentity
            and type(self.universe_snapshot_id) is UniverseSnapshotId
            and type(self.universe_content_identity) is ContentIdentity
            and type(self.strategy_provenance_identity) is ContentIdentity
            and type(self.strategy_evaluation_id) is StrategyEvaluationId
            and type(self.strategy_evaluation_identity) is ContentIdentity
            and type(self.signal_kind) is SignalKind
        )
        if (
            type(self.level) is not KrakenStrategyQualificationLevel
            or self.level is not KrakenStrategyQualificationLevel.OFFLINE_CONTRACT
            or type(self.status) is not KrakenStrategyQualificationStatus
            or type(self.reason_code) is not KrakenStrategyQualificationReason
            or self.venue is not VenueId.KRAKEN
            or self.environment not in ACTIVE_ENVIRONMENTS
            or self.instrument_identity != BTC_EUR.content_identity
            or (
                passed
                and self.reason_code
                is not KrakenStrategyQualificationReason.KRAKEN_STRATEGY_QUALIFIED
            )
            or (
                not passed
                and self.reason_code is KrakenStrategyQualificationReason.KRAKEN_STRATEGY_QUALIFIED
            )
            or (passed and (not source_bound or not evaluated))
            or self.release_binding != "NOT_ESTABLISHED_PRE_MERGE"
            or self.real_economic_calls != 0
            or self.live != "LIVE_FORBIDDEN"
            or self.side_effect_performed is not False
        ):
            raise EvidenceError("INVALID_KRAKEN_STRATEGY_QUALIFICATION")
        EvidenceRecord.__post_init__(self)
