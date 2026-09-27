from __future__ import annotations

from dataclasses import fields, replace
from pathlib import Path

import pytest

from atp.exchange.contracts import BTC_EUR, PublicObservation, SelectedPublicExchange, VenueId
from atp.exchange.kraken import (
    KrakenPublicClient,
    parse_closed_ohlc,
    parse_server_time,
    parse_system_status,
    parse_ticker_price,
)
from atp.exchange.read_only import EvidenceError
from atp.kraken_qualification import (
    KrakenQualificationLevel,
    KrakenQualificationStatus,
    qualify_offline,
    qualify_public_connectivity,
)
from atp.release_deployment.source import inspect_source
from tests.unit.test_kraken_public import AT, FixtureTransport, evidence, fixture


def offline_result():
    mapping, metadata = evidence()
    return qualify_offline(
        BTC_EUR,
        mapping,
        metadata,
        parse_server_time(fixture("time.json"), AT),
        parse_system_status(fixture("system-status.json"), AT),
        parse_closed_ohlc(fixture("ohlc.json"), BTC_EUR, mapping, 5, AT),
        parse_ticker_price(fixture("ticker.json"), BTC_EUR, mapping, AT, PublicObservation(AT, AT)),
    )


def test_offline_contract_has_independent_kraken_identity_and_no_authority() -> None:
    result = offline_result()
    assert result.level is KrakenQualificationLevel.OFFLINE_CONTRACT
    assert result.status is KrakenQualificationStatus.PASSED
    assert result.venue is VenueId.KRAKEN
    assert result.real_economic_calls == 0
    assert result.live == "LIVE_FORBIDDEN"
    assert result.side_effect_performed is False
    assert "BINANCE" not in str(result.content_identity)


def test_public_connectivity_level_is_explicit_and_fixture_driven_here() -> None:
    result = qualify_public_connectivity(
        KrakenPublicClient(FixtureTransport(), clock=lambda: AT),
    )
    assert result.level is KrakenQualificationLevel.PUBLIC_CONNECTIVITY
    assert result.status is KrakenQualificationStatus.PASSED
    assert result.real_economic_calls == 0


def test_qualification_result_cannot_claim_economic_or_live_authority() -> None:
    result = offline_result()
    with pytest.raises(EvidenceError, match="INVALID_KRAKEN_QUALIFICATION_RESULT"):
        replace(result, real_economic_calls=1)
    with pytest.raises(EvidenceError, match="INVALID_KRAKEN_QUALIFICATION_RESULT"):
        replace(result, live="LIVE")


def test_kraken_failure_never_calls_a_binance_fallback() -> None:
    class FailedKraken:
        def get(self, path: str, parameters: tuple[tuple[str, str], ...] = ()) -> object:
            del path, parameters
            raise OSError("kraken unavailable")

    client = KrakenPublicClient(FailedKraken())
    selected = SelectedPublicExchange(VenueId.KRAKEN, client)
    assert {f.name for f in fields(selected)} == {"venue", "port"}
    assert selected.port is client
    assert set(client.__slots__) == {"_transport", "_clock"}
    assert not hasattr(client, "__dict__")
    result = qualify_public_connectivity(client)
    assert result.status is KrakenQualificationStatus.FAILED
    assert result.real_economic_calls == 0


@pytest.fixture(autouse=True)
def clean_source(monkeypatch):
    # Synthetic clean fixture; production always calls the real inspector.
    source = replace(inspect_source(Path.cwd()), clean=True)
    monkeypatch.setattr("atp.kraken_qualification.engine.inspect_source", lambda root: source)
    return source


def test_source_change_fails_closed(monkeypatch, clean_source):
    values = iter((clean_source, replace(clean_source, source_commit_sha="a" * 40)))
    monkeypatch.setattr("atp.kraken_qualification.engine.inspect_source", lambda root: next(values))
    result = offline_result()
    assert result.status is KrakenQualificationStatus.FAILED
    assert result.reason_code.value == "KRAKEN_SOURCE_INVALID"


def test_dirty_source_blocks_before_network(monkeypatch, clean_source):
    monkeypatch.setattr(
        "atp.kraken_qualification.engine.inspect_source",
        lambda root: replace(clean_source, clean=False),
    )
    transport = FixtureTransport()
    result = qualify_public_connectivity(KrakenPublicClient(transport))
    assert result.status is KrakenQualificationStatus.FAILED
    assert transport.calls == []


def test_source_bound_without_release_authority(clean_source):
    result = offline_result()
    assert result.source_identity == clean_source.content_identity
    assert result.source_commit_sha == clean_source.source_commit_sha
    assert result.repository_identity == clean_source.repository_identity
    assert result.release_binding == "NOT_ESTABLISHED_PRE_MERGE"


@pytest.mark.parametrize("failure", ["inspection", "dirty_after"])
def test_source_inspection_failures_are_closed(monkeypatch, clean_source, failure):
    calls = []

    def inspect(root):
        calls.append(root)
        if len(calls) == 2:
            if failure == "inspection":
                raise OSError("source unavailable")
            return replace(clean_source, clean=False)
        return clean_source

    monkeypatch.setattr("atp.kraken_qualification.engine.inspect_source", inspect)
    result = offline_result()
    assert result.status is KrakenQualificationStatus.FAILED
    assert result.source_identity is None


def test_unknown_price_freshness_fails_qualification():
    mapping, metadata = evidence()
    result = qualify_offline(
        BTC_EUR,
        mapping,
        metadata,
        parse_server_time(fixture("time.json"), AT),
        parse_system_status(fixture("system-status.json"), AT),
        parse_closed_ohlc(fixture("ohlc.json"), BTC_EUR, mapping, 5, AT),
        parse_ticker_price(fixture("ticker.json"), BTC_EUR, mapping, AT),
    )
    assert result.status is KrakenQualificationStatus.FAILED
