from __future__ import annotations

import inspect
from dataclasses import replace
from pathlib import Path

import pytest

from atp.exchange.read_only import EvidenceError, verify_record
from atp.kraken_strategy import evaluate_kraken_strategy
from atp.kraken_strategy_qualification import (
    KrakenStrategyQualificationLevel,
    KrakenStrategyQualificationReason,
    KrakenStrategyQualificationResult,
    KrakenStrategyQualificationStatus,
    qualify_offline,
)
from atp.release_deployment.source import inspect_source
from atp.strategy import SignalKind
from tests.unit.test_kraken_strategy_composition import batch


@pytest.fixture(autouse=True)
def clean_source(monkeypatch: pytest.MonkeyPatch):
    source = replace(inspect_source(Path.cwd()), clean=True)
    monkeypatch.setattr(
        "atp.kraken_strategy_qualification.engine.inspect_source", lambda root: source
    )
    return source


def test_offline_contract_qualifies_data_universe_strategy_without_authority() -> None:
    snapshot = batch().snapshot
    result = qualify_offline(snapshot)
    assert result.level is KrakenStrategyQualificationLevel.OFFLINE_CONTRACT
    assert result.status is KrakenStrategyQualificationStatus.PASSED
    assert result.reason_code is KrakenStrategyQualificationReason.KRAKEN_STRATEGY_QUALIFIED
    assert result.signal_kind is SignalKind.LONG_ENTRY
    assert result.snapshot_id == snapshot.snapshot_id
    assert result.snapshot_content_identity == snapshot.content_identity
    assert result.universe_snapshot_id is not None
    assert result.universe_content_identity is not None
    assert result.strategy_provenance_identity is not None
    assert result.strategy_evaluation_id is not None
    assert result.strategy_evaluation_identity is not None
    assert result.real_economic_calls == 0
    assert result.live == "LIVE_FORBIDDEN"
    assert result.side_effect_performed is False
    assert verify_record(result, KrakenStrategyQualificationResult)


def test_blocked_strategy_input_is_a_failed_qualification() -> None:
    result = qualify_offline(batch(closed_count=50).snapshot)
    assert result.status is KrakenStrategyQualificationStatus.FAILED
    assert result.reason_code is KrakenStrategyQualificationReason.KRAKEN_STRATEGY_INPUT_INVALID
    assert result.strategy_evaluation_identity is None


def test_dirty_or_changed_source_fails_closed(monkeypatch, clean_source) -> None:
    monkeypatch.setattr(
        "atp.kraken_strategy_qualification.engine.inspect_source",
        lambda root: replace(clean_source, clean=False),
    )
    result = qualify_offline(batch().snapshot)
    assert result.status is KrakenStrategyQualificationStatus.FAILED
    assert result.reason_code is KrakenStrategyQualificationReason.KRAKEN_STRATEGY_SOURCE_INVALID
    assert result.source_identity is None

    values = iter((clean_source, replace(clean_source, source_commit_sha="a" * 40)))
    monkeypatch.setattr(
        "atp.kraken_strategy_qualification.engine.inspect_source", lambda root: next(values)
    )
    changed = qualify_offline(batch().snapshot)
    assert changed.status is KrakenStrategyQualificationStatus.FAILED
    assert changed.source_identity is None


def test_result_cannot_claim_economic_or_live_authority() -> None:
    result = qualify_offline(batch().snapshot)
    for changes in ({"real_economic_calls": 1}, {"live": "LIVE"}):
        with pytest.raises(EvidenceError, match="INVALID_KRAKEN_STRATEGY_QUALIFICATION"):
            replace(result, **changes)


def test_composition_has_no_risk_oms_exchange_or_accounting_dependency() -> None:
    source = inspect.getsource(evaluate_kraken_strategy)
    for forbidden in ("atp.risk", "atp.oms", "atp.accounting", "execute_controlled"):
        assert forbidden not in source
