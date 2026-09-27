from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from atp.exchange.contracts import BTC_EUR, BTC_USDT, VenueId
from atp.exchange.kraken import (
    KRAKEN_PUBLIC_ROUTE_ALLOWLIST,
    KrakenPublicClient,
    KrakenPublicHTTPTransport,
    parse_asset_pairs,
    parse_closed_ohlc,
    parse_server_time,
    parse_system_status,
    parse_ticker_price,
)
from atp.exchange.read_only import EvidenceError

FIXTURES = Path("tests/fixtures/kraken")
AT = datetime(2026, 9, 24, 12, 0, 1, tzinfo=UTC)


def fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text())


class FixtureTransport:
    routes = {
        "/0/public/Time": "time.json",
        "/0/public/SystemStatus": "system-status.json",
        "/0/public/AssetPairs": "asset-pairs.json",
        "/0/public/OHLC": "ohlc.json",
        "/0/public/Ticker": "ticker.json",
    }

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[tuple[str, str], ...]]] = []

    def get(self, path: str, parameters: tuple[tuple[str, str], ...] = ()) -> object:
        self.calls.append((path, parameters))
        return fixture(self.routes[path])


def evidence():
    mapping, metadata = parse_asset_pairs(fixture("asset-pairs.json"), BTC_EUR, AT)
    return mapping, metadata


def test_offline_parsers_resolve_btc_eur_and_closed_candles() -> None:
    mapping, metadata = evidence()
    assert mapping.venue is VenueId.KRAKEN
    assert mapping.instrument == BTC_EUR
    assert mapping.native_identifier == "XXBTZEUR"
    assert mapping.native_aliases == ("XXBTZEUR", "XBTEUR", "XBT/EUR")
    assert metadata.mapping_identity == mapping.content_identity
    assert str(metadata.price_increment) == "0.1"
    assert str(metadata.quantity_increment) == "1E-8"
    assert parse_server_time(fixture("time.json"), AT).venue is VenueId.KRAKEN
    assert parse_system_status(fixture("system-status.json"), AT).status == "online"
    candles = parse_closed_ohlc(fixture("ohlc.json"), BTC_EUR, mapping, 5, AT)
    assert len(candles.candles) == 2
    assert candles.candles[-1].close.is_finite()
    price = parse_ticker_price(fixture("ticker.json"), BTC_EUR, mapping, AT)
    assert str(price.price) == "95250.0"
    assert price.source == "KRAKEN_TICKER_LAST_TRADE"


def test_alias_multiplicity_is_one_mapping_but_economic_ambiguity_blocks() -> None:
    payload = fixture("asset-pairs.json")
    mapping, _ = parse_asset_pairs(payload, BTC_EUR, AT)
    assert len(mapping.native_aliases) == 3
    assert isinstance(payload, dict) and isinstance(payload["result"], dict)
    payload["result"]["XBTZEUR.SECOND"] = dict(payload["result"]["XXBTZEUR"])
    with pytest.raises(EvidenceError, match="KRAKEN_MAPPING_AMBIGUOUS"):
        parse_asset_pairs(payload, BTC_EUR, AT)


def test_unknown_fields_errors_and_foreign_mappings_fail_closed() -> None:
    payload = fixture("asset-pairs.json")
    assert isinstance(payload, dict) and isinstance(payload["result"], dict)
    payload["result"]["XXBTZEUR"]["unknown_constraint"] = "1"
    with pytest.raises(EvidenceError, match="KRAKEN_METADATA_UNKNOWN_FIELD"):
        parse_asset_pairs(payload, BTC_EUR, AT)
    errored = fixture("time.json")
    assert isinstance(errored, dict)
    errored["error"] = ["EService:Unavailable"]
    with pytest.raises(EvidenceError, match="KRAKEN_ENVELOPE_INVALID"):
        parse_server_time(errored, AT)
    mapping, _ = evidence()
    with pytest.raises(EvidenceError, match="FOREIGN_MAPPING"):
        parse_ticker_price(fixture("ticker.json"), BTC_USDT, mapping, AT)


def test_public_client_uses_only_closed_allowlist_and_exact_mapping() -> None:
    transport = FixtureTransport()
    client = KrakenPublicClient(transport, clock=lambda: AT)
    client.server_time(AT)
    client.system_status(AT)
    mapping, _ = client.instrument_metadata(BTC_EUR, AT)
    client.closed_candles(BTC_EUR, mapping, 5, AT)
    client.price(BTC_EUR, mapping, AT)
    assert {path for path, _ in transport.calls} == KRAKEN_PUBLIC_ROUTE_ALLOWLIST
    assert all(path.startswith("/0/public/") for path in KRAKEN_PUBLIC_ROUTE_ALLOWLIST)
    assert not any(
        token in path.casefold()
        for path in KRAKEN_PUBLIC_ROUTE_ALLOWLIST
        for token in ("private", "order", "balance", "withdraw", "cancel")
    )


def test_http_transport_rejects_every_non_allowlisted_route_without_network() -> None:
    transport = KrakenPublicHTTPTransport()
    for path in (
        "/0/private/Balance",
        "/0/private/OpenOrders",
        "/0/private/AddOrder",
        "/0/private/Withdraw",
        "/0/public/Unknown",
    ):
        with pytest.raises(EvidenceError, match="KRAKEN_PUBLIC_ROUTE_FORBIDDEN"):
            transport.get(path)


@pytest.mark.parametrize("change", ["misaligned", "duplicate", "gap", "stale", "future"])
def test_ohlc_grid_continuity_and_freshness(change):
    mapping, _ = evidence()
    payload = fixture("ohlc.json")
    rows = payload["result"]["XXBTZEUR"]
    at = AT
    if change == "misaligned":
        rows[0][0] += 1
    elif change == "duplicate":
        rows[1][0] = rows[0][0]
    elif change == "gap":
        rows[0][0] -= 300
    elif change == "stale":
        at += timedelta(minutes=5)
    else:
        at -= timedelta(minutes=5)
    with pytest.raises(EvidenceError, match="KRAKEN_OHLC_INVALID"):
        parse_closed_ohlc(payload, BTC_EUR, mapping, 5, at)


def test_uncommitted_row_is_never_in_closed_evidence():
    mapping, _ = evidence()
    payload = fixture("ohlc.json")
    result = parse_closed_ohlc(payload, BTC_EUR, mapping, 5, AT)
    assert all(
        c.open_time.timestamp() < payload["result"]["XXBTZEUR"][-1][0] for c in result.candles
    )


def test_mapping_observation_is_self_contained_and_identity_sensitive():
    from dataclasses import replace

    mapping, _ = evidence()
    assert (
        mapping.native_base_asset,
        mapping.native_quote_asset,
        mapping.instrument_status,
        mapping.observed_at,
    ) == ("XXBT", "ZEUR", "online", AT)
    for changes in (
        {"observed_at": AT + timedelta(seconds=1)},
        {"native_base_asset": "XBT"},
        {"instrument_status": "cancel_only"},
    ):
        assert replace(mapping, **changes).content_identity != mapping.content_identity


def test_response_boundary_not_callers_pre_network_time():
    mapping, _ = evidence()
    times = iter((AT, AT + timedelta(seconds=2)))
    client = KrakenPublicClient(FixtureTransport(), clock=lambda: next(times))
    price = client.price(BTC_EUR, mapping, AT - timedelta(hours=1))
    assert price.observed_at == AT + timedelta(seconds=2)
    assert price.observation.request_started_at == AT
    assert price.freshness == "OBSERVATION_FRESH"
    assert price.event_time is None


@pytest.mark.parametrize("seconds", [-1, 11])
def test_unbounded_or_rollback_observation_blocks(seconds):
    times = iter((AT, AT + timedelta(seconds=seconds)))
    client = KrakenPublicClient(FixtureTransport(), clock=lambda: next(times))
    with pytest.raises(EvidenceError, match="PUBLIC_OBSERVATION_UNBOUNDED"):
        client.server_time()


def test_clock_skew_blocks():
    client = KrakenPublicClient(FixtureTransport(), clock=lambda: AT + timedelta(minutes=1))
    with pytest.raises(EvidenceError, match="KRAKEN_CLOCK_SKEW"):
        client.server_time()


def test_unbounded_ticker_parser_reports_unknown_freshness():
    mapping, _ = evidence()
    result = parse_ticker_price(fixture("ticker.json"), BTC_EUR, mapping, AT)
    assert result.freshness == "UNKNOWN"
    assert result.event_time is None
