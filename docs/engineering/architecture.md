# ATP engineering architecture

ATP starts as a modular monolith. Packages under `src/atp` represent authority boundaries, not independently deployed services.

Dependencies should point through explicit contracts/ports where a domain requires another domain. Shared code is restricted to genuinely technical primitives such as identity, UTC time, configuration and result/error mechanics.

Importing any ATP package must be side-effect free with respect to external exchanges.

The DATA boundary now provides immutable historical snapshots, causal availability filtering, deterministic lineage, and historical universe snapshots. These primitives expose DATA eligibility only and do not authorize risk, orders, or Live operation.
