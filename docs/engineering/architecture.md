# ATP engineering architecture — foundation

ATP starts as a modular monolith. Packages under `src/atp` represent authority boundaries, not independently deployed services.

Dependencies should point through explicit contracts/ports where a domain requires another domain. Shared code is restricted to genuinely technical primitives such as identity, UTC time, configuration and result/error mechanics.

Importing any ATP package must be side-effect free with respect to external exchanges.
