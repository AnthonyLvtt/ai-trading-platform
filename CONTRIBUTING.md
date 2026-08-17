# Contributing

## Branches and pull requests

- Work through focused branches and pull requests.
- Keep commits atomic and scoped.
- Do not merge without the project governance review required by ADR-001.
- Do not commit secrets or large market datasets.

## Before opening or updating a PR

Run:

```bash
make validate
```

## Architecture discipline

- Preserve module authority boundaries from the Accepted SPECs.
- Shared primitives are technical only; domain state machines remain domain-owned.
- Do not add abstractions without a concrete use in the V1 vertical slice.
- No Live activation, Binance credentials, withdrawal capability, leverage, margin, Futures, or shorting belongs in foundation work.
