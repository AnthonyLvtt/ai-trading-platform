# ADR-002 — Plateforme modulaire ATP

## Métadonnées

| Champ | Valeur |
|---|---|
| Document | ADR-002 |
| Version | 1.0 |
| Statut | Accepted |
| Autorité de validation | CTO |
| Domaine | Architecture |
| Emplacement canonique | `docs/adr/ADR-002-modular-platform.md` |

> Note de restitution : le fichier autonome d’origine n’était pas disponible dans les sources accessibles lors de cet export. Le présent fichier restitue uniquement les décisions ADR-002 explicitement démontrées par les documents ATP disponibles ; aucune technologie interne non validée n’est ajoutée.

## 1. Contexte

ATP doit fournir une plateforme de trading algorithmique autonome, supervisable, explicable et évolutive, tout en conservant des frontières métier nettes et une architecture initiale suffisamment simple pour la V1.

Une architecture distribuée prématurée augmenterait la complexité opérationnelle sans nécessité démontrée pour le MVP et le vertical slice initial.

## 2. Décision

ATP démarre sous la forme d’un **monolithe modulaire**.

Les frontières logiques initiales comprennent :

1. Market Data ;
2. Strategy Engine ;
3. AI/ML ;
4. Risk Engine ;
5. OMS ;
6. Exchange Adapter ;
7. Portfolio & Accounting ;
8. Backtesting & Simulation ;
9. Observability ;
10. Web Supervision.

Ces modules définissent des responsabilités logiques. ADR-002 n’impose pas une technologie de transport, de persistence, de framework ou de déploiement particulière.

## 3. Frontières essentielles

### Market Data

Responsable des données de marché et de leur mise à disposition selon les contrats applicables.

### Strategy Engine

Produit des évaluations et propositions économiques.

Une proposition Strategy n’est pas une autorisation d’exposition.

### AI/ML

Peut contribuer à l’analyse ou aux propositions selon les contrats applicables.

Invariant :

```text
AI/ML
≠ accès direct Exchange
```

L’AI/ML ne transmet jamais directement un ordre à l’Exchange.

### Risk Engine

Moteur déterministe et indépendant chargé d’autoriser ou refuser l’exposition selon les règles Risk.

Tout signal exécutable passe par Risk.

### OMS

Responsable du cycle de vie des intentions d’ordre et des exécutions reconnues selon ses contrats.

### Exchange Adapter

Seule frontière d’intégration avec l’Exchange pour les opérations externes autorisées.

Binance est l’adapter initial, mais Binance ne fait pas partie du cœur métier de la plateforme.

### Portfolio & Accounting

Autorité sur l’état financier interne, positions, soldes et effets comptables selon ses contrats.

### Backtesting & Simulation

Permet l’expérimentation et l’exécution simulée sans confusion avec une exécution réelle.

### Observability

Expose les signaux de diagnostic sans devenir autorité métier.

### Web Supervision

Permet la supervision et les demandes opérateur sans devenir autorité Risk, OMS, Accounting, Security ou Exchange.

## 4. Flux d’autorité principal

```text
Market Data
→ Strategy
→ Risk
→ OMS
→ Exchange Adapter
→ OMS recognized executions
→ Portfolio & Accounting
```

Aucun flux d’ordre ne doit contourner Risk.

AI/ML ne communique pas directement avec l’Exchange Adapter pour placer des ordres.

## 5. Principes architecturaux

- monolithe modulaire initial ;
- séparation explicite des responsabilités ;
- Risk déterministe et indépendant ;
- AI/ML sans accès direct Exchange ;
- Binance limité à l’Exchange Adapter ;
- simulation, Testnet et réel distingués ;
- chemins critiques fail-closed selon les SPEC applicables ;
- pas de services distribués prématurés.

## 6. Décisions non prises par ADR-002

ADR-002 ne décide pas :

- l’architecture hexagonale exacte ;
- le découpage futur en microservices ;
- le framework applicatif ;
- la base de données ;
- le moteur de messages ;
- le runtime de containers ;
- l’orchestrateur ;
- le cloud ;
- le mécanisme interne exact de dependency injection ;
- la technologie d’API interne.

Toute décision structurante ultérieure sur ces sujets nécessite validation CTO et, lorsqu’elle est structurante, un ADR.

## 7. Évolution

Le monolithe modulaire peut évoluer ultérieurement si des besoins démontrés de scalabilité, isolation, déploiement ou résilience le justifient.

Cette possibilité future ne constitue pas une décision de migrer vers des microservices.

Invariant :

```text
modular monolith now
≠ microservices later by default
```

## 8. Conséquences

Cette décision permet :

- une implémentation V1 simple et testable ;
- des frontières compatibles avec une évolution future ;
- une réduction de la complexité distribuée ;
- une séparation claire des autorités métier ;
- l’utilisation d’un adapter Binance sans coupler le cœur métier à Binance.

## 9. Invariants

- Strategy proposal ≠ Risk authorization.
- AI/ML ≠ direct Exchange order path.
- Risk ≠ Strategy.
- OMS ≠ Accounting.
- Exchange Adapter ≠ cœur métier.
- Observability ≠ autorité métier.
- Web ≠ autorité métier.
- Simulation ≠ réel.
- Monolithe modulaire ≠ absence de frontières.
- Architecture initiale ≠ microservices prématurés.

## 10. Historique

| Version | Statut | Description |
|---|---|---|
| 1.0 | Accepted | Adoption d’un monolithe modulaire ATP avec dix frontières logiques et séparation stricte des autorités principales. |
