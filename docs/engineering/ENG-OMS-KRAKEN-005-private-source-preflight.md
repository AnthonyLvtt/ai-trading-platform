# ENG-OMS-KRAKEN-005 — Private source qualification preflight

Status: **offline hardening complete; runtime financial source still unqualified**.

Base: `2cfd70f453b6c96d6bfe0488978dbad43ab1f634` (post-merge Quality #190 SUCCESS).

The target private sources are:

- `/0/private/BalanceEx` for spendable-EUR evidence;
- `/0/private/TradeVolume` for the BTC/EUR fee bound.

Offline producers and bounded read-only transport exist, but complete runtime
financial evidence is not qualified.

BalanceEx uses exact decimal arithmetic and requires explicit fields for every
present row. A missing or incomplete field in a present row fails closed.

ENG-KRAKEN-EXPOSURE-002 resolves one separate ambiguity: a syntactically valid
BalanceEx response with **no EUR row** does not establish either a positive
balance or zero. It yields:

```text
EUR_BALANCE_NOT_OBSERVED
value = None
status = INCOMPLETE
```

This state is not spendable-EUR evidence and cannot produce OMS runtime PASS.
An explicit valid EUR row containing zero remains distinct and may produce a
numeric zero spendable-EUR proof.

TradeVolume is not queried after EUR_BALANCE_NOT_OBSERVED in the bounded operator
path.

The qualification layers remain distinct:

- authenticated connectivity;
- read-only permissions;
- route observation;
- valid financial evidence;
- operational qualification;
- economic authorization.

Success at an earlier layer never implies success at a later one.

No funding action is required or authorized by this preflight. A missing EUR row
must not be repaired operationally by depositing funds merely to make a technical
qualification pass.

No real economic call or Live capability is granted.
`REAL_ECONOMIC_CALLS = 0`; `LIVE = LIVE_FORBIDDEN`.
