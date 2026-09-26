# ADR-004 — Architecture multi-exchange et introduction de Kraken

## Métadonnées

| Champ | Valeur |
|---|---|
| Document | ADR-004 |
| Version | 1.0 |
| Statut | Accepted |
| Auteur | Documentation Architect |
| Autorité de validation | CTO |
| Domaine | Architecture / Exchange |
| Dernière mise à jour | 2026-09-26 |
| Emplacement | `docs/adr/ADR-004-multi-exchange-kraken.md` |

## Note historique

During documentation planning this decision was temporarily designated ADR-003.
Repository reconciliation identified an existing canonical ADR-003 governing the
controlled first-order campaign. The multi-exchange decision was therefore assigned
ADR-004 before repository publication. No substantive architectural decision changed.

## 1. Contexte

ATP a historiquement été documenté et conçu avec Binance comme premier Exchange Adapter.

Cette décision historique reste valide dans son périmètre.

ATP évolue vers une architecture explicitement **multi-exchange** afin que les domaines métier internes ne dépendent pas directement :

- des symboles natifs d’un exchange ;
- des identifiants natifs d’un exchange ;
- des contraintes propres à Binance ;
- des contraintes propres à Kraken ;
- des mécanismes spécifiques d’acknowledgement ;
- des particularités de reconciliation ;
- des modèles de credentials propres à un venue.

Le prochain exchange à intégrer pour le Spot est **Kraken**.

Binance reste supporté.

## 2. Décision

ATP adopte une frontière Exchange Adapter explicitement neutre vis-à-vis du venue.

```text
Strategy
Risk
OMS
Accounting
TEST
Release / Deployment
        |
        v
canonical ATP exchange contracts
        |
        v
Exchange Adapter boundary
       / \
      /   \
Binance   Kraken
adapter   adapter
```

Les domaines internes consomment les contrats canoniques ATP.

Les particularités natives d’un venue sont confinées dans l’adapter correspondant.

## 3. Multi-exchange

ATP supporte plusieurs **backends Exchange au niveau architectural**.

Un runtime ou une campagne sélectionne explicitement son venue.

ADR-004 n’autorise pas :

- l’exécution multi-venue simultanée ;
- le smart order routing ;
- l’arbitrage ;
- la sélection automatique de venue ;
- le fallback automatique entre venues.

Dans la première évolution :

- Binance reste un backend supporté ;
- Kraken devient le second backend prévu ;
- chaque venue possède son propre adapter ;
- chaque venue possède ses propres credentials ;
- chaque venue possède sa propre qualification ;
- le venue utilisé doit être explicitement sélectionné.

Invariant :

```text
venue
≠ implicit global default
```

## 4. Aucun fallback automatique

Invariants :

```text
Kraken failure
≠ Binance fallback
```

```text
Binance failure
≠ Kraken fallback
```

Une défaillance, indisponibilité ou dégradation d’un venue ne doit jamais provoquer silencieusement une exécution sur un autre venue.

Tout changement de venue doit constituer une nouvelle décision explicite, observable et auditable selon le contrat applicable.

## 5. Instrument identity

ATP sépare :

```text
canonical ATP instrument identity
≠ native exchange instrument identity
```

Strategy, Risk, OMS et Accounting ne doivent pas dépendre directement des représentations natives Binance ou Kraken.

L’Exchange Adapter est responsable du mapping entre :

- identité canonique ATP ;
- identité native du venue ;
- métadonnées natives nécessaires.

### 5.1 Instrument Kraken initial

**Instrument initial Kraken approuvé par le CTO pour le scope foundation/read-only :**

```text
BTC/EUR
```

Cette décision autorise uniquement l’utilisation de `BTC/EUR` comme cible initiale du chantier :

- foundation ;
- contrats Exchange neutres ;
- adapter Kraken read-only ;
- qualification future correspondante.

Elle n’autorise pas :

- runtime économique Kraken ;
- ordre Kraken réel ;
- Live ;
- margin ;
- futures ;
- changement automatique de venue.

## 6. Contrat Exchange Adapter neutre

La frontière Exchange doit exposer des contrats conceptuellement neutres couvrant au minimum :

- instrument metadata ;
- quantity constraints ;
- price/notional constraints ;
- balances ;
- open orders ;
- clock/server-time evidence ;
- candles/price evidence ;
- order request ;
- acknowledgement ;
- reconciliation identity.

Les détails natifs restent confinés à chaque adapter.

### 6.1 Time evidence

```text
exchange server time
≠ ATP logical time
```

### 6.2 Order acknowledgement

Une réponse native ne doit jamais être transformée en résultat favorable lorsqu’un état externe reste ambigu.

### 6.3 External identities

Les identités natives sont scopées par venue.

```text
venue + native identity
→ external identity scope
```

## 7. Frontières métier

### Strategy

Produit des propositions économiques canoniques.

Strategy ne manipule pas directement les symboles natifs Binance ou Kraken.

### Risk

Reste l’autorité déterministe d’exposition.

Un changement de venue ne contourne jamais Risk.

### OMS

Conserve l’intention et le cycle de vie canoniques des ordres.

Deux identités natives appartenant à des venues différents ne doivent jamais être assimilées.

### Accounting

Conserve la vérité financière interne et la provenance venue nécessaire à la reconciliation.

### Exchange Adapter

Traduit entre les contrats ATP et le venue.

Il ne devient jamais autorité Strategy, Risk, OMS ou Accounting.

## 8. Invariants de sécurité

Les invariants ATP restent inchangés :

- Spot only ;
- LONG only ;
- no leverage ;
- no margin ;
- no futures ;
- `max_positions = 1` ;
- Risk déterministe ;
- AI hors chemin économique critique ;
- withdrawal forbidden ;
- fail-closed ;
- no secret telemetry.

## 9. Credentials

Les credentials sont isolés par venue.

```text
Binance credential
≠ Kraken credential
```

Ils doivent :

- respecter `SPEC-SEC-001` ;
- appliquer least privilege ;
- ne jamais être commités ;
- ne jamais être loggés ;
- ne jamais apparaître dans events/traces/telemetry ;
- ne jamais être embarqués dans les artifacts ;
- ne jamais être transférés ou réutilisés entre venues.

## 10. Qualification par venue

```text
Binance qualification
≠ Kraken qualification
```

Une qualification Binance ne démontre jamais :

- les contraintes Kraken ;
- les sémantiques Kraken ;
- la reconciliation Kraken ;
- les permissions Kraken ;
- la protection Kraken.

L’inverse est également vrai.

Les preuves TEST doivent conserver le venue du candidate qualifié.

## 11. Interchangeabilité

```text
same ATP adapter contract
≠ equivalent venue qualification
```

Deux adapters implémentant une même interface canonique ne sont pas automatiquement interchangeables pour une utilisation économique.

## 12. Release / Deployment

Le venue constitue un élément matériel de configuration lorsqu’il affecte le runtime Exchange.

Il doit être lié aux éléments applicables de :

- configuration ;
- qualification ;
- release candidate ;
- promotion ;
- deployment ;
- runtime verification.

```text
release qualified for Binance
≠ automatically qualified for Kraken
```

Un changement de venue exige une nouvelle décision ou une démonstration explicite d’applicabilité autorisée par les contrats concernés.

## 13. Operations

Les capacités Exchange doivent être observables par venue.

Une dégradation Kraken ne doit pas modifier silencieusement l’état Binance, et inversement.

Les runbooks doivent distinguer au minimum :

- incident Binance ;
- incident Kraken ;
- problème transverse de la frontière Exchange ;
- mapping instrument incohérent ;
- credentials mal scopés ;
- reconciliation propre à un venue.

```text
venue unhealthy
≠ permission to switch venue
```

## 14. Statut Kraken

Statut actuel :

```text
DESIGN / FOUNDATION PLANNING
```

Kraken n’est pas :

- production-ready ;
- Live-approved ;
- Live-active ;
- autorisé pour une exécution économique réelle.

Aucun ordre Kraken réel n’est autorisé.

Aucun runtime économique Kraken n’est autorisé.

Une intégration read-only future ne constitue pas une autorisation d’ordre.

## 15. Migration incrémentale

### Phase 1 — Inventory Binance coupling

Identifier les dépendances directes aux sémantiques Binance.

### Phase 2 — Exchange-neutral contracts

Introduire les contrats canoniques ATP nécessaires à la frontière Exchange.

### Phase 3 — Kraken read-only adapter

Implémenter uniquement les capacités Kraken autorisées en lecture.

Instrument initial approuvé :

```text
BTC/EUR
```

### Phase 4 — Kraken qualification

Qualifier séparément les capacités Kraken.

### Phase 5 — Controlled preparation

Préparer Security, OPS, Release et contrats requis pour un éventuel scope économique futur.

### Phase 6 — Future economic authorization

Toute autorisation économique Kraken exige une décision CTO séparée et toutes les gates applicables.

ADR-004 n’autorise pas cette phase.

## 16. Conservation de l’historique Binance

```text
new multi-exchange architecture
≠ retroactive rewrite of Binance history
```

Les décisions Binance existantes restent valides dans leur périmètre historique.

La documentation future doit distinguer :

- les décisions historiquement Binance-specific ;
- le contrat canonique ATP multi-exchange introduit ultérieurement.

## 17. Impact documentaire

ADR-004 entraîne des évolutions contrôlées de :

- Architecture Overview ;
- DOC-MAP-001 ;
- Exchange Adapter documentation ;
- TEST / Qualification ;
- Security / credential handling lorsque nécessaire ;
- Release / Deployment ;
- Operations / runbooks ;
- future First Testnet Order procedure.

Les versions Accepted existantes doivent être préservées dans leur historique.

Aucun nouvel ID de SPEC ne doit être inventé avant revue de la séparation exacte entre :

- contrat canonique Exchange ;
- spécifications propres à chaque venue.

## 18. Non-objectifs

ADR-004 ne décide pas :

- du client Kraken ;
- d’une bibliothèque Kraken ;
- des endpoints exacts ;
- des order types Kraken économiques futurs ;
- du modèle de protection Kraken ;
- du smart order routing ;
- de l’arbitrage ;
- de l’exécution multi-venue simultanée ;
- de la sélection automatique de venue ;
- du fallback automatique ;
- d’une autorisation Live Kraken.

## 19. Invariants récapitulatifs

- Binance reste supporté.
- Kraken est ajouté, pas substitué.
- ATP supporte plusieurs backends Exchange au niveau architectural.
- Un runtime/campagne sélectionne explicitement un venue.
- ADR-004 n’autorise aucune exécution multi-venue simultanée.
- ATP instrument identity ≠ native venue identity.
- Strategy ≠ native symbol mapping.
- Risk reste l’autorité d’exposition.
- Venue selection est explicite, observable et auditable.
- Binance credential ≠ Kraken credential.
- Binance qualification ≠ Kraken qualification.
- Same adapter contract ≠ venue interchangeability.
- Kraken failure ≠ Binance fallback.
- Binance failure ≠ Kraken fallback.
- Kraken read-only ≠ Kraken economic authorization.
- `BTC/EUR` foundation target ≠ economic authorization.
- Kraken qualification ≠ Live approval.
- Architecture multi-exchange ≠ réécriture historique Binance.

## 20. Historique

| Version | Date | Statut | Description |
|---|---|---|---|
| 0.1 | 2026-09-26 | Draft — CTO Review | Proposition initiale architecture multi-exchange et introduction Kraken, temporairement désignée ADR-003 pendant la planification documentaire. |
| 1.0 | 2026-09-26 | Accepted | Validation CTO de l’architecture multi-exchange ; `BTC/EUR` fixé comme instrument Kraken initial pour foundation/read-only ; clarification qu’ADR-004 n’autorise ni multi-venue simultané, ni routing/arbitrage, ni sélection automatique de venue. Renumérotation ADR-003 → ADR-004 avant publication après détection de l’ADR-003 canonique existant, sans changement architectural substantiel. |
