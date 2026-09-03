# ATP engineering architecture

ATP starts as a modular monolith. Packages under `src/atp` represent authority boundaries, not independently deployed services.

Dependencies should point through explicit contracts/ports where a domain requires another domain. Shared code is restricted to genuinely technical primitives such as identity, UTC time, configuration and result/error mechanics.

Importing any ATP package must be side-effect free with respect to external exchanges.

The DATA boundary now provides immutable historical snapshots, causal availability filtering, deterministic lineage, and historical universe snapshots. These primitives expose DATA eligibility only and do not authorize risk, orders, or Live operation.

The Strategy boundary consumes only accepted causal DATA contracts. Its first baseline performs a deterministic, single-symbol SMA crossover evaluation and returns a typed signal or an explicit blocked result. A Strategy signal remains an economic proposal, never Risk authorization, sizing, or an order.

The Risk boundary consumes a reproducible Strategy proposal plus explicit Risk-owned market and portfolio evidence. Its deterministic V1 policy returns an approval, rejection, blocked result, or a non-economic `NO_DECISION` for Strategy `NO_ACTION`. Risk cannot create orders or quantities and has no dependency on OMS, Exchange, Accounting, AI/ML, network access, or the system clock.
