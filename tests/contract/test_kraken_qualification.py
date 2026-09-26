from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from atp.exchange.contracts import BTC_EUR, VenueId
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
        parse_ticker_price(fixture("ticker.json"), BTC_EUR, mapping, AT),
    )


def test_offline_contract_has_independent_kraken_identity_and_no_authority() -> None:
    result = offline_result()
    assert result.level is KrakenQualificationLevel.OFFLINE_CONTRACT
    assert result.status is KrakenQualificationStatus.PASSED
    assert result.venue is VenueId.KRAKEN_SPOT
    assert result.real_economic_calls == 0
    assert result.live == "LIVE_FORBIDDEN"
    assert result.side_effect_performed is False
    assert "BINANCE" not in str(result.content_identity)


def test_public_connectivity_level_is_explicit_and_fixture_driven_here() -> None:
    result = qualify_public_connectivity(
        KrakenPublicClient(FixtureTransport()),
        observed_at=datetime(2026, 9, 24, 12, 0, 1, tzinfo=UTC),
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

    binance_calls: list[str] = []
    result = qualify_public_connectivity(KrakenPublicClient(FailedKraken()))
    assert result.status is KrakenQualificationStatus.FAILED
    assert binance_calls == []
    assert result.real_economic_calls == 0
