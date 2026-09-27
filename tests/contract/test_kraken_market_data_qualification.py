from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from atp.exchange.contracts import BTC_EUR, VenueId
from atp.exchange.kraken import KrakenPublicClient, parse_closed_ohlc
from atp.exchange.read_only import EvidenceError, verify_record
from atp.kraken_market_data_qualification import (
    KRAKEN_MARKET_DATA_ROUTES,
    KrakenMarketDataQualificationLevel,
    KrakenMarketDataQualificationReason,
    KrakenMarketDataQualificationResult,
    KrakenMarketDataQualificationStatus,
    qualify_offline,
    qualify_public_connectivity,
)
from atp.release_deployment.source import inspect_source
from atp.shared.environment import Environment
from tests.unit.test_kraken_public import AT, FixtureTransport, evidence, fixture


def offline_result():
    mapping, _ = evidence()
    candles = parse_closed_ohlc(fixture("ohlc.json"), BTC_EUR, mapping, 5, AT)
    return qualify_offline(BTC_EUR, mapping, candles)


@pytest.fixture(autouse=True)
def clean_source(monkeypatch: pytest.MonkeyPatch):
    source = replace(inspect_source(Path.cwd()), clean=True)
    monkeypatch.setattr(
        "atp.kraken_market_data_qualification.engine.inspect_source", lambda root: source
    )
    return source


def test_offline_contract_qualifies_canonical_data_without_authority() -> None:
    result = offline_result()
    assert result.level is KrakenMarketDataQualificationLevel.OFFLINE_CONTRACT
    assert result.status is KrakenMarketDataQualificationStatus.PASSED
    assert result.reason_code is KrakenMarketDataQualificationReason.KRAKEN_DATA_QUALIFIED
    assert result.venue is VenueId.KRAKEN
    assert result.environment is Environment.TEST
    assert result.mapping_identity is not None
    assert result.provenance_identity is not None
    assert result.snapshot_id is not None
    assert result.snapshot_content_identity is not None
    assert result.lineage_identity is not None
    assert result.real_economic_calls == 0
    assert result.live == "LIVE_FORBIDDEN"
    assert result.side_effect_performed is False
    assert verify_record(result, KrakenMarketDataQualificationResult)


def test_fixture_public_connectivity_uses_exact_integrated_route_set() -> None:
    transport = FixtureTransport()
    result = qualify_public_connectivity(KrakenPublicClient(transport, clock=lambda: AT))
    assert result.level is KrakenMarketDataQualificationLevel.PUBLIC_CONNECTIVITY
    assert result.status is KrakenMarketDataQualificationStatus.PASSED
    assert tuple(sorted(path for path, _ in transport.calls)) == KRAKEN_MARKET_DATA_ROUTES
    assert "/0/public/Ticker" not in result.route_allowlist


def test_system_status_blocks_before_mapping_and_ingestion() -> None:
    class MaintenanceTransport(FixtureTransport):
        def get(self, path: str, parameters: tuple[tuple[str, str], ...] = ()) -> object:
            payload = super().get(path, parameters)
            if path == "/0/public/SystemStatus":
                payload["result"]["status"] = "maintenance"
            return payload

    transport = MaintenanceTransport()
    result = qualify_public_connectivity(KrakenPublicClient(transport, clock=lambda: AT))
    assert result.status is KrakenMarketDataQualificationStatus.FAILED
    assert result.reason_code is KrakenMarketDataQualificationReason.KRAKEN_SYSTEM_NOT_ONLINE
    assert [path for path, _ in transport.calls] == [
        "/0/public/Time",
        "/0/public/SystemStatus",
    ]


def test_kraken_failure_has_no_fallback() -> None:
    class FailedTransport:
        def get(self, path: str, parameters: tuple[tuple[str, str], ...] = ()) -> object:
            del path, parameters
            raise OSError("Kraken unavailable")

    result = qualify_public_connectivity(KrakenPublicClient(FailedTransport()))
    assert result.status is KrakenMarketDataQualificationStatus.FAILED
    assert result.reason_code is KrakenMarketDataQualificationReason.KRAKEN_PUBLIC_UNAVAILABLE
    assert result.real_economic_calls == 0


def test_dirty_source_blocks_before_public_network(monkeypatch, clean_source) -> None:
    monkeypatch.setattr(
        "atp.kraken_market_data_qualification.engine.inspect_source",
        lambda root: replace(clean_source, clean=False),
    )
    transport = FixtureTransport()
    result = qualify_public_connectivity(KrakenPublicClient(transport))
    assert result.status is KrakenMarketDataQualificationStatus.FAILED
    assert result.reason_code is KrakenMarketDataQualificationReason.KRAKEN_DATA_SOURCE_INVALID
    assert transport.calls == []


def test_source_change_fails_closed(monkeypatch, clean_source) -> None:
    values = iter((clean_source, replace(clean_source, source_commit_sha="a" * 40)))
    monkeypatch.setattr(
        "atp.kraken_market_data_qualification.engine.inspect_source", lambda root: next(values)
    )
    result = offline_result()
    assert result.status is KrakenMarketDataQualificationStatus.FAILED
    assert result.source_identity is None


@pytest.mark.parametrize(
    "field",
    [
        "mapping_identity",
        "provenance_identity",
        "snapshot_id",
        "snapshot_content_identity",
        "lineage_identity",
        "source_commit_sha",
        "source_tree_sha",
        "repository_identity",
        "source_identity",
    ],
)
def test_passed_result_requires_complete_integrated_and_source_binding(field: str) -> None:
    with pytest.raises(EvidenceError, match="INVALID_KRAKEN_MARKET_DATA_QUALIFICATION"):
        replace(offline_result(), **{field: None})


def test_result_cannot_claim_economic_or_live_authority() -> None:
    result = offline_result()
    for changes in ({"real_economic_calls": 1}, {"live": "LIVE"}):
        with pytest.raises(EvidenceError, match="INVALID_KRAKEN_MARKET_DATA_QUALIFICATION"):
            replace(result, **changes)
