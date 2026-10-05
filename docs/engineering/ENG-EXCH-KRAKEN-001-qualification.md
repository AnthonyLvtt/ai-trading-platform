# Kraken foundation qualification — PR review corrections

VenueId identifies KRAKEN; MarketKind.SPOT belongs to the canonical instrument.

Mapping evidence binds the venue, canonical instrument, native identifier and
aliases, native base/quote, instrument status, metadata digest and observation
time. Downstream metadata, candles and price bind that exact mapping digest.

## Public freshness contract

Each Kraken client GET samples UTC immediately before transport and immediately
after response parsing. Negative durations and intervals over 10 seconds block.
Caller-supplied historical timestamps are not used as network observation times.
Ticker evidence persists both request and response boundaries. OBSERVATION_FRESH
means a bounded observation of the public Ticker snapshot, never proof of the age
of its last trade. Ticker has no trade timestamp: event_time is explicitly null.
A parser without an observation interval returns UNKNOWN and cannot pass KQ.
This foundation evidence carries no execution authority and cannot substitute for
an economic price freshness gate.

Server Time must lie within the request/response interval extended by 5 seconds
at either end. Qualification additionally rejects observed clock skew over 15
seconds and a collected evidence set spanning more than 60 seconds. These are
explicit conservative foundation bounds, not exchange guarantees.

OHLC requires a 5-minute UTC grid, consecutive slots without duplicates/gaps,
and a latest closed candle ending no more than one interval before observation.
Future closes fail. The final uncommitted row is always excluded. An unavailable
latest candle causes failure rather than silent acceptance of stale history.

References:
- https://docs.kraken.com/api-reference/market-data/get-ticker-information
- https://docs.kraken.com/api-reference/market-data/get-ohlc-data

## KQ-SOURCE-001

Both OFFLINE_CONTRACT and PUBLIC_CONNECTIVITY inspect SourceTree immediately
before and after evaluation. Dirty, unavailable or unequal source fails closed.
Results bind commit SHA, Git tree SHA, repository identity and SourceTree identity.
Release binding is explicitly NOT_ESTABLISHED_PRE_MERGE. No ATP Release,
deployment, economic or Live authority is claimed.

After merge, rerun qualification on the exact merged clean main and use the
existing main-only Release process. A PR result is not presumed equivalent to
the merged source. The existing Release contract is unchanged.

PUBLIC_CONNECTIVITY is explicitly invoked via scripts/qualify_kraken.py; ordinary
pytest remains offline. It owns one Kraken client and transport; the selected
public composition has exactly one venue and port. There is no fallback registry,
alternate venue, private route or economic transport.
