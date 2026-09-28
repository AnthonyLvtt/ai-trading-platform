from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from atp.exchange.contracts import BTC_EUR, VenueId
from atp.exchange.kraken.private import (
    KRAKEN_PRIVATE_READ_ROUTE_ALLOWLIST,
    KrakenPrivateError,
    KrakenPrivateReadRoute,
    MonotonicNonceProvider,
    account_wide_open_orders_request,
    parse_api_key_info,
    parse_balances,
    parse_open_orders,
    sign_private_read_request,
)
from atp.exchange.private_contracts import PrivateCredentialReference
from atp.exchange.read_only import EvidenceError, encoded
from atp.shared.environment import Environment

AT = datetime(2026, 9, 28, 12, tzinfo=UTC)
FIXTURES = Path(__file__).parents[1] / "fixtures" / "kraken" / "private"


def fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text())


def reference() -> PrivateCredentialReference:
    return PrivateCredentialReference(VenueId.KRAKEN, "a" * 32, Environment.TEST)


def evidence():
    ref = reference()
    capability = parse_api_key_info(fixture("api-key-info.json"), ref, AT)
    balances = parse_balances(fixture("balance.json"), ref, capability, AT)
    request = account_wide_open_orders_request(ref)
    orders = parse_open_orders(fixture("open-orders.json"), ref, capability, request, AT)
    return ref, capability, balances, request, orders


def test_read_route_guard_excludes_every_economic_route() -> None:
    assert set(KRAKEN_PRIVATE_READ_ROUTE_ALLOWLIST) == {
        "/0/private/GetApiKeyInfo",
        "/0/private/Balance",
        "/0/private/OpenOrders",
    }
    assert not any(
        token in route.casefold()
        for route in KRAKEN_PRIVATE_READ_ROUTE_ALLOWLIST
        for token in ("addorder", "cancel", "withdraw", "deposit")
    )


def test_read_request_signer_accepts_typed_allowlist_only() -> None:
    secret = "c2VjcmV0"
    body, signature = sign_private_read_request(KrakenPrivateReadRoute.BALANCE, 123, secret)
    assert body == "nonce=123"
    assert signature == (
        "+uDrF6q37xxMDI3XpBc//aEw3IwbIkeO2gQjMsWMDfVoViS78XFe8cUJVWUkh+VeG/LLS8U/CyVO1928z8qy4Q=="
    )
    with pytest.raises(KrakenPrivateError, match="KRAKEN_PRIVATE_ROUTE_FORBIDDEN"):
        sign_private_read_request("/0/private/AddOrder", 123, secret)  # type: ignore[arg-type]
    with pytest.raises(KrakenPrivateError, match="KRAKEN_PRIVATE_PARAMETERS_INVALID"):
        sign_private_read_request(KrakenPrivateReadRoute.BALANCE, 123, secret, (("otp", "123456"),))
    for parameters in ((("userref", "42"),), (("trades", "true"),), (("foo", "bar"),)):
        with pytest.raises(KrakenPrivateError, match="KRAKEN_PRIVATE_PARAMETERS_INVALID"):
            sign_private_read_request(KrakenPrivateReadRoute.OPEN_ORDERS, 123, secret, parameters)


def test_nonce_is_unique_under_concurrency_and_clock_rollback_blocks() -> None:
    nonce_reference = PrivateCredentialReference(VenueId.KRAKEN, "c" * 32, Environment.TEST)
    values = iter((1_000_000_000,) * 16 + (999_000_000,))
    provider = MonotonicNonceProvider(nonce_reference.content_identity, lambda: next(values))
    with ThreadPoolExecutor(max_workers=8) as pool:
        nonces = list(pool.map(lambda _: provider.next(), range(16)))
    assert len(nonces) == len(set(nonces)) == 16
    assert sorted(nonces) == list(range(1000, 1016))
    with pytest.raises(KrakenPrivateError, match="KRAKEN_NONCE_CLOCK_ROLLBACK"):
        provider.next()


def test_multiple_nonce_providers_share_reference_state() -> None:
    nonce_reference = PrivateCredentialReference(VenueId.KRAKEN, "d" * 32, Environment.TEST)
    first = MonotonicNonceProvider(nonce_reference.content_identity, lambda: 2_000_000_000)
    second = MonotonicNonceProvider(nonce_reference.content_identity, lambda: 2_000_000_000)
    assert (first.next(), second.next()) == (2000, 2001)


def test_private_parsers_emit_canonical_sanitized_evidence() -> None:
    ref, capability, balances, request, orders = evidence()
    assert capability.permissions == ("query-funds", "query-open-trades")
    assert capability.trading_capability_absent is True
    assert capability.withdrawal_capability_absent is True
    assert [item.asset.value for item in balances.balances] == ["BTC", "EUR"]
    assert orders.scope == "ACCOUNT_WIDE"
    assert orders.complete is True
    assert request.parameters == ()
    assert orders.request_identity == request.content_identity
    assert orders.orders[0].instrument == BTC_EUR
    assert orders.orders[0].side == "BUY"
    document = json.dumps(encoded(capability), sort_keys=True)
    raw = json.dumps(fixture("api-key-info.json"), sort_keys=True)
    for secret_fragment in (
        "fixture-public-key-never-persisted",
        "fixture-iban-never-persisted",
        "192.0.2.1",
        "atp-read-only",
    ):
        assert secret_fragment in raw
        assert secret_fragment not in document


@pytest.mark.parametrize(
    ("permission", "reason"),
    [
        ("modify-trades", "KRAKEN_LEAST_PRIVILEGE_REQUIRED"),
        ("close-trades", "KRAKEN_LEAST_PRIVILEGE_REQUIRED"),
        ("withdraw-funds", "KRAKEN_LEAST_PRIVILEGE_REQUIRED"),
    ],
)
def test_write_permissions_never_create_read_capability(permission: str, reason: str) -> None:
    payload = fixture("api-key-info.json")
    payload["result"]["permissions"].append(permission)
    with pytest.raises(KrakenPrivateError, match=reason):
        parse_api_key_info(payload, reference(), AT)


@pytest.mark.parametrize("asset", ["USDT", "XXBT.F", "ZEUR.M", "UNKNOWN"])
def test_unknown_or_suffixed_balance_assets_fail_closed(asset: str) -> None:
    ref, capability, _, _, _ = evidence()
    payload = fixture("balance.json")
    payload["result"][asset] = "1"
    with pytest.raises(KrakenPrivateError, match="KRAKEN_BALANCE_ASSET_UNKNOWN"):
        parse_balances(payload, ref, capability, AT)


def test_absent_zero_balances_are_not_invented() -> None:
    ref, capability, _, _, _ = evidence()
    result = parse_balances({"error": [], "result": {"ZEUR": "10"}}, ref, capability, AT)
    assert [balance.asset.value for balance in result.balances] == ["EUR"]
    empty = parse_balances({"error": [], "result": {}}, ref, capability, AT)
    assert empty.balances == ()


@pytest.mark.parametrize(
    ("field", "value"),
    [("pair", "XBTUSD"), ("leverage", "2:1"), ("ordertype", "stop-loss")],
)
def test_unsafe_or_foreign_open_order_blocks(field: str, value: str) -> None:
    ref, capability, _, request, _ = evidence()
    payload = fixture("open-orders.json")
    order = next(iter(payload["result"]["open"].values()))
    order["descr"][field] = value
    with pytest.raises(KrakenPrivateError, match="KRAKEN_OPEN_ORDER_UNSAFE"):
        parse_open_orders(payload, ref, capability, request, AT)


def test_reference_or_capability_mismatch_blocks() -> None:
    ref, capability, _, request, _ = evidence()
    foreign = PrivateCredentialReference(VenueId.KRAKEN, "b" * 32, Environment.TEST)
    with pytest.raises(KrakenPrivateError, match="KRAKEN_PRIVATE_CAPABILITY_MISMATCH"):
        parse_balances(fixture("balance.json"), foreign, capability, AT)
    with pytest.raises(EvidenceError, match="INVALID_PRIVATE_CREDENTIAL_REFERENCE"):
        replace(ref, environment=Environment.LIVE)
    foreign_request = account_wide_open_orders_request(foreign)
    with pytest.raises(KrakenPrivateError, match="KRAKEN_OPEN_ORDERS_REQUEST_MISMATCH"):
        parse_open_orders(fixture("open-orders.json"), ref, capability, foreign_request, AT)


def test_non_economic_flags_are_immutable_false() -> None:
    _, capability, balances, _, orders = evidence()
    for value, changes, reason in (
        (
            capability,
            {"submission_authorized": True},
            "INVALID_PRIVATE_CREDENTIAL_CAPABILITY",
        ),
        (
            capability,
            {"side_effect_performed": True},
            "INVALID_PRIVATE_CREDENTIAL_CAPABILITY",
        ),
        (balances, {"side_effect_performed": True}, "INVALID_ACCOUNT_BALANCE_EVIDENCE"),
        (orders, {"side_effect_performed": True}, "INVALID_ACCOUNT_OPEN_ORDERS_EVIDENCE"),
    ):
        with pytest.raises(EvidenceError, match=reason):
            replace(value, **changes)


def test_account_wide_request_cannot_be_narrowed() -> None:
    request = account_wide_open_orders_request(reference())
    for changes in (
        {"parameters": (("userref", "42"),)},
        {"parameters": (("trades", "true"),)},
        {"scope": "FILTERED"},
        {"route": KrakenPrivateReadRoute.BALANCE},
    ):
        with pytest.raises(KrakenPrivateError, match="KRAKEN_OPEN_ORDERS_REQUEST_INVALID"):
            replace(request, **changes)
