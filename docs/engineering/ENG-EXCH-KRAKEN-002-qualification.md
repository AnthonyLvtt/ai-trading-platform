# ENG-EXCH-KRAKEN-002 — Kraken private read-only foundation

This mission adds an offline-only Kraken Spot private observation boundary. It
does not add a private HTTP transport, operational credentials, Risk authority,
OMS, accounting, an order route, or Live authority.

The closed route vocabulary is:

- `/0/private/GetApiKeyInfo`
- `/0/private/Balance`
- `/0/private/OpenOrders`

The request signer accepts only the typed route vocabulary. `AddOrder`, cancel,
funding and withdrawal paths are not representable. Normal tests and the
qualification runner consume local fixtures only. Private network calls remain
zero.

`GetApiKeyInfo` is reduced to the exact permissions `query-funds` and
`query-open-trades`. Key value, key label, nonce state, IIBAN, IP allowlist and
usage timestamps never enter evidence. Any additional permission fails the
least-privilege qualification.

Balance evidence accepts exactly native `XXBT` and `ZEUR`, projects them to BTC
and EUR, and rejects suffixes or unknown assets. Open-orders evidence is always
account-wide before parsing; every order must map to canonical BTC/EUR Spot and
must have no leverage. Unknown pairs, leverage and order types fail closed.

The nonce provider is process-local and bound to one opaque credential reference.
It serializes concurrent calls and blocks clock rollback. It does not claim
cross-process safety; no private network runtime is authorized in this mission.

References:

- https://docs.kraken.com/exchange/guides/rest/authentication
- https://docs.kraken.com/api/docs/rest-api/get-api-key-info
- https://docs.kraken.com/api-reference/account-data/get-account-balance
- https://docs.kraken.com/api-reference/account-data/get-open-orders
