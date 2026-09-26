"""Closed Kraken Spot public GET adapter and strict response parsers."""

from __future__ import annotations

import json
import re
import ssl
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from http.client import HTTPException, HTTPSConnection
from typing import Protocol
from urllib.parse import urlencode

from atp.exchange.contracts import (
    CanonicalInstrumentId,
    PublicCandle,
    PublicCandleEvidence,
    PublicInstrumentMetadata,
    PublicPriceEvidence,
    PublicServerTimeEvidence,
    PublicSystemStatusEvidence,
    VenueId,
    VenueInstrumentMappingEvidence,
)
from atp.exchange.read_only import EvidenceError, safe_json
from atp.shared.identity import ContentIdentity
from atp.shared.serialization import canonical_json_bytes

KRAKEN_HOST = "api.kraken.com"
KRAKEN_PUBLIC_ROUTE_ALLOWLIST = frozenset(
    {
        "/0/public/Time",
        "/0/public/SystemStatus",
        "/0/public/AssetPairs",
        "/0/public/OHLC",
        "/0/public/Ticker",
    }
)

_PAIR_FIELDS = frozenset(
    {
        "altname",
        "wsname",
        "aclass_base",
        "base",
        "aclass_quote",
        "quote",
        "lot",
        "cost_decimals",
        "pair_decimals",
        "lot_decimals",
        "lot_multiplier",
        "leverage_buy",
        "leverage_sell",
        "fees",
        "fees_maker",
        "fee_volume_currency",
        "margin_call",
        "margin_stop",
        "ordermin",
        "costmin",
        "tick_size",
        "status",
        "long_position_limit",
        "short_position_limit",
        "execution_venue",
    }
)


class KrakenPublicTransport(Protocol):
    def get(self, path: str, parameters: tuple[tuple[str, str], ...] = ()) -> object: ...


class KrakenPublicHTTPTransport:
    """Unsigned GET-only transport with a fixed host and closed route allowlist."""

    def get(self, path: str, parameters: tuple[tuple[str, str], ...] = ()) -> object:
        if path not in KRAKEN_PUBLIC_ROUTE_ALLOWLIST:
            raise EvidenceError("KRAKEN_PUBLIC_ROUTE_FORBIDDEN")
        if type(parameters) is not tuple or any(
            type(item) is not tuple
            or len(item) != 2
            or type(item[0]) is not str
            or type(item[1]) is not str
            for item in parameters
        ):
            raise EvidenceError("INVALID_PUBLIC_QUERY")
        query = urlencode(parameters)
        connection = HTTPSConnection(KRAKEN_HOST, timeout=10, context=ssl.create_default_context())
        try:
            connection.request("GET", path + ("?" + query if query else ""), headers={})
            response = connection.getresponse()
            if response.status != 200:
                raise EvidenceError("KRAKEN_PUBLIC_UNAVAILABLE")
            body = response.read(2_000_001)
            if len(body) > 2_000_000:
                raise EvidenceError("KRAKEN_PUBLIC_UNAVAILABLE")
            result = json.loads(body)
            safe_json(result)
            return result
        except (OSError, HTTPException, UnicodeError, ValueError, RecursionError):
            raise EvidenceError("KRAKEN_PUBLIC_UNAVAILABLE") from None
        finally:
            connection.close()


def _source_identity(payload: object) -> ContentIdentity:
    safe_json(payload)
    return ContentIdentity.from_bytes(canonical_json_bytes(payload))


def _envelope(payload: object) -> object:
    safe_json(payload)
    if (
        type(payload) is not dict
        or set(payload) != {"error", "result"}
        or type(payload["error"]) is not list
        or payload["error"]
    ):
        raise EvidenceError("KRAKEN_ENVELOPE_INVALID")
    return payload["result"]


def _utc_from_seconds(value: object) -> datetime:
    if type(value) is not int or value < 0:
        raise EvidenceError("KRAKEN_TIME_INVALID")
    try:
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=value)
    except OverflowError:
        raise EvidenceError("KRAKEN_TIME_INVALID") from None


def _decimal(value: object, *, positive: bool = False) -> Decimal:
    if type(value) is not str:
        raise EvidenceError("KRAKEN_DECIMAL_INVALID")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise EvidenceError("KRAKEN_DECIMAL_INVALID") from None
    if not result.is_finite() or result < 0 or (positive and result <= 0):
        raise EvidenceError("KRAKEN_DECIMAL_INVALID")
    return result


def _iso_time(value: object) -> datetime:
    if type(value) is not str or not value.endswith("Z"):
        raise EvidenceError("KRAKEN_TIME_INVALID")
    try:
        result = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise EvidenceError("KRAKEN_TIME_INVALID") from None
    if result.tzinfo is not UTC:
        result = result.astimezone(UTC)
    return result


def parse_server_time(payload: object, observed_at: datetime) -> PublicServerTimeEvidence:
    result = _envelope(payload)
    if type(result) is not dict or set(result) != {"unixtime", "rfc1123"}:
        raise EvidenceError("KRAKEN_TIME_INVALID")
    if type(result["rfc1123"]) is not str or not result["rfc1123"]:
        raise EvidenceError("KRAKEN_TIME_INVALID")
    return PublicServerTimeEvidence(
        VenueId.KRAKEN_SPOT,
        _utc_from_seconds(result["unixtime"]),
        observed_at,
        _source_identity(payload),
    )


def parse_system_status(payload: object, observed_at: datetime) -> PublicSystemStatusEvidence:
    result = _envelope(payload)
    if (
        type(result) is not dict
        or not {"status", "timestamp"}.issubset(result)
        or not set(result).issubset({"status", "timestamp", "upcoming_maintenance", "emergency"})
        or type(result["status"]) is not str
        or result["status"] not in {"online", "maintenance", "cancel_only", "post_only"}
        or ("upcoming_maintenance" in result and type(result["upcoming_maintenance"]) is not list)
        or ("emergency" in result and type(result["emergency"]) is not list)
    ):
        raise EvidenceError("KRAKEN_STATUS_INVALID")
    return PublicSystemStatusEvidence(
        VenueId.KRAKEN_SPOT,
        result["status"],
        _iso_time(result["timestamp"]),
        observed_at,
        _source_identity(payload),
    )


def _canonical_asset(value: object) -> str | None:
    if type(value) is not str:
        return None
    return {"XBT": "BTC", "XXBT": "BTC", "BTC": "BTC", "EUR": "EUR", "ZEUR": "EUR"}.get(value)


def parse_asset_pairs(
    payload: object, instrument: CanonicalInstrumentId, observed_at: datetime
) -> tuple[VenueInstrumentMappingEvidence, PublicInstrumentMetadata]:
    result = _envelope(payload)
    if type(result) is not dict or not result:
        raise EvidenceError("KRAKEN_MAPPING_INVALID")
    matches: list[tuple[str, dict[str, object]]] = []
    for native, raw in result.items():
        if type(native) is not str or not re.fullmatch(r"[A-Z0-9.]{3,32}", native):
            raise EvidenceError("KRAKEN_MAPPING_INVALID")
        if type(raw) is not dict or not set(raw).issubset(_PAIR_FIELDS):
            raise EvidenceError("KRAKEN_METADATA_UNKNOWN_FIELD")
        if (
            _canonical_asset(raw.get("base")) == instrument.base_asset
            and _canonical_asset(raw.get("quote")) == instrument.quote_asset
            and raw.get("execution_venue") == "international"
        ):
            matches.append((native, raw))
    if len(matches) != 1:
        raise EvidenceError("KRAKEN_MAPPING_AMBIGUOUS" if matches else "KRAKEN_MAPPING_MISSING")
    native, raw = matches[0]
    required = {
        "altname",
        "wsname",
        "aclass_base",
        "base",
        "aclass_quote",
        "quote",
        "lot",
        "cost_decimals",
        "pair_decimals",
        "lot_decimals",
        "lot_multiplier",
        "ordermin",
        "costmin",
        "tick_size",
        "status",
        "execution_venue",
    }
    if (
        not required.issubset(raw)
        or raw["aclass_base"] != "currency"
        or raw["aclass_quote"] != "currency"
        or raw["lot"] != "unit"
        or raw["execution_venue"] != "international"
        or type(raw["altname"]) is not str
        or type(raw["wsname"]) is not str
        or type(raw["status"]) is not str
        or type(raw["cost_decimals"]) is not int
        or type(raw["pair_decimals"]) is not int
        or type(raw["lot_decimals"]) is not int
        or raw["lot_multiplier"] != 1
    ):
        raise EvidenceError("KRAKEN_MAPPING_INVALID")
    aliases = tuple(dict.fromkeys((native, raw["altname"], raw["wsname"])))
    source = _source_identity(payload)
    mapping = VenueInstrumentMappingEvidence(
        VenueId.KRAKEN_SPOT, instrument, native, aliases, source
    )
    quantity_increment = Decimal(1).scaleb(-raw["lot_decimals"])
    return mapping, PublicInstrumentMetadata(
        VenueId.KRAKEN_SPOT,
        instrument,
        mapping.content_identity,
        raw["status"],
        _decimal(raw["tick_size"], positive=True),
        quantity_increment,
        _decimal(raw["ordermin"], positive=True),
        _decimal(raw["costmin"], positive=True),
        observed_at,
        source,
    )


def _mapping_for(
    instrument: CanonicalInstrumentId, mapping: VenueInstrumentMappingEvidence
) -> None:
    if mapping.venue is not VenueId.KRAKEN_SPOT or mapping.instrument != instrument:
        raise EvidenceError("FOREIGN_MAPPING")


def parse_closed_ohlc(
    payload: object,
    instrument: CanonicalInstrumentId,
    mapping: VenueInstrumentMappingEvidence,
    interval_minutes: int,
    observed_at: datetime,
) -> PublicCandleEvidence:
    _mapping_for(instrument, mapping)
    result = _envelope(payload)
    if type(result) is not dict or set(result) != {mapping.native_identifier, "last"}:
        raise EvidenceError("KRAKEN_OHLC_INVALID")
    rows, last = result[mapping.native_identifier], result["last"]
    if type(rows) is not list or len(rows) < 2 or type(last) is not int or interval_minutes <= 0:
        raise EvidenceError("KRAKEN_OHLC_INVALID")
    candles = []
    for row in rows[:-1]:  # Kraken documents the final row as the current uncommitted frame.
        if type(row) is not list or len(row) != 8 or type(row[7]) is not int or row[7] < 0:
            raise EvidenceError("KRAKEN_OHLC_INVALID")
        candle = PublicCandle(
            _utc_from_seconds(row[0]),
            interval_minutes,
            _decimal(row[1], positive=True),
            _decimal(row[2], positive=True),
            _decimal(row[3], positive=True),
            _decimal(row[4], positive=True),
            _decimal(row[6]),
            row[7],
        )
        if candle.low > min(candle.open, candle.close) or candle.high < max(
            candle.open, candle.close
        ):
            raise EvidenceError("KRAKEN_OHLC_INVALID")
        candles.append(candle)
    if not candles or any(
        right.open_time <= left.open_time for left, right in zip(candles, candles[1:], strict=False)
    ):
        raise EvidenceError("KRAKEN_OHLC_INVALID")
    return PublicCandleEvidence(
        VenueId.KRAKEN_SPOT,
        instrument,
        mapping.content_identity,
        tuple(candles),
        observed_at,
        _source_identity(payload),
    )


def parse_ticker_price(
    payload: object,
    instrument: CanonicalInstrumentId,
    mapping: VenueInstrumentMappingEvidence,
    observed_at: datetime,
) -> PublicPriceEvidence:
    _mapping_for(instrument, mapping)
    result = _envelope(payload)
    if type(result) is not dict or set(result) != {mapping.native_identifier}:
        raise EvidenceError("KRAKEN_TICKER_INVALID")
    ticker = result[mapping.native_identifier]
    allowed = {"a", "b", "c", "v", "p", "t", "l", "h", "o"}
    if type(ticker) is not dict or set(ticker) != allowed:
        raise EvidenceError("KRAKEN_TICKER_INVALID")
    close = ticker["c"]
    if type(close) is not list or len(close) != 2:
        raise EvidenceError("KRAKEN_TICKER_INVALID")
    return PublicPriceEvidence(
        VenueId.KRAKEN_SPOT,
        instrument,
        mapping.content_identity,
        _decimal(close[0], positive=True),
        "KRAKEN_TICKER_LAST_TRADE",
        observed_at,
        _source_identity(payload),
    )


class KrakenPublicClient:
    venue = VenueId.KRAKEN_SPOT

    def __init__(self, transport: KrakenPublicTransport) -> None:
        self._transport = transport

    def server_time(self, observed_at: datetime) -> PublicServerTimeEvidence:
        return parse_server_time(self._transport.get("/0/public/Time"), observed_at)

    def system_status(self, observed_at: datetime) -> PublicSystemStatusEvidence:
        return parse_system_status(self._transport.get("/0/public/SystemStatus"), observed_at)

    def instrument_metadata(
        self, instrument: CanonicalInstrumentId, observed_at: datetime
    ) -> tuple[VenueInstrumentMappingEvidence, PublicInstrumentMetadata]:
        return parse_asset_pairs(
            self._transport.get("/0/public/AssetPairs"), instrument, observed_at
        )

    def closed_candles(
        self,
        instrument: CanonicalInstrumentId,
        mapping: VenueInstrumentMappingEvidence,
        interval_minutes: int,
        observed_at: datetime,
    ) -> PublicCandleEvidence:
        _mapping_for(instrument, mapping)
        return parse_closed_ohlc(
            self._transport.get(
                "/0/public/OHLC",
                (("pair", mapping.native_identifier), ("interval", str(interval_minutes))),
            ),
            instrument,
            mapping,
            interval_minutes,
            observed_at,
        )

    def price(
        self,
        instrument: CanonicalInstrumentId,
        mapping: VenueInstrumentMappingEvidence,
        observed_at: datetime,
    ) -> PublicPriceEvidence:
        _mapping_for(instrument, mapping)
        return parse_ticker_price(
            self._transport.get("/0/public/Ticker", (("pair", mapping.native_identifier),)),
            instrument,
            mapping,
            observed_at,
        )
