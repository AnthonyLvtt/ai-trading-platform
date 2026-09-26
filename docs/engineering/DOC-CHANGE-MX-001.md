# DOC-CHANGE-MX-001 — Plan de consolidation multi-exchange ATP

## Métadonnées

| Champ | Valeur |
|---|---|
| Document | DOC-CHANGE-MX-001 |
| Version | 1.0 |
| Statut | Accepted — Approved for consolidation planning |
| Auteur | Documentation Architect |
| Autorité | CTO |
| Date | 2026-09-26 |
| Emplacement | `docs/engineering/DOC-CHANGE-MX-001.md` |
| Objet | Transition documentaire Binance-centric → multi-exchange |

## 1. Décision de consolidation

La transition documentaire multi-exchange est approuvée sous l’autorité de `ADR-004 — Architecture multi-exchange et introduction de Kraken`.

Elle est additive :

```text
historical Binance decisions
→ preserved

new exchange-neutral architecture
→ introduced prospectively
```

Aucun document historique ne doit être réécrit comme si Kraken avait toujours fait partie de son périmètre.

## 2. Architecture cible

ATP supporte plusieurs backends Exchange au niveau architectural.

```text
Core ATP domains
        |
        v
Exchange-neutral contract
       / \
 Binance  Kraken
 Adapter  Adapter
```

Un runtime ou une campagne sélectionne explicitement son venue.

Ne sont pas autorisés par cette consolidation :

- exécution multi-venue simultanée ;
- smart order routing ;
- arbitrage ;
- automatic venue selection ;
- automatic venue fallback.

## 3. Instrument Kraken foundation/read-only

Instrument initial approuvé :

```text
BTC/EUR
```

Scope :

- foundation ;
- contrats neutres ;
- read-only Kraken ;
- qualification future correspondante.

Ce choix ne constitue aucune autorisation économique.

## 4. DOC-MAP-001

Une nouvelle version devra intégrer :

- `ADR-004 — Accepted v1.0` ;
- Binance comme backend historique conservé ;
- Kraken comme backend `DESIGN / FOUNDATION PLANNING` ;
- sélection explicite du venue ;
- qualification propre au venue ;
- credentials propres au venue ;
- interdiction du fallback automatique.

Chaîne mise à jour :

```text
OMS
→ Exchange Adapter canonical contract
→ explicitly selected venue adapter
→ external venue
```

## 5. Exchange Adapter documentation

La consolidation devra séparer conceptuellement :

### Canonical ATP Exchange contract

- instrument metadata ;
- quantity constraints ;
- price/notional constraints ;
- balances ;
- open orders ;
- time evidence ;
- candles/price evidence ;
- order request ;
- acknowledgement ;
- reconciliation.

### Binance-specific scope

Les décisions Binance existantes sont conservées dans leur historique et leur scope.

### Kraken-specific scope

Statut actuel :

```text
DESIGN / FOUNDATION PLANNING
```

Cible initiale :

```text
BTC/EUR
```

Aucune capacité économique n’est autorisée.

## 6. TEST / Qualification

Le venue devient une caractéristique matérielle de l’applicabilité des preuves Exchange.

```text
qualification(Binance)
≠ qualification(Kraken)
```

```text
candidate venue change
→ previous qualification not silently reusable
```

Les mêmes tests de contrat peuvent être réutilisés comme définitions.

Leurs exécutions, evidence et qualifications restent spécifiques au candidate et au venue.

## 7. Security

Les credentials sont séparés par venue.

```text
credential Binance
≠ credential Kraken
```

Maintenir :

- least privilege ;
- no secrets committed ;
- no secrets logged ;
- no secrets in artifacts ;
- no cross-venue credential reuse ;
- withdrawal forbidden.

## 8. Release / Deployment

Le venue doit être traité comme configuration matérielle lorsqu’il affecte le runtime Exchange.

```text
venue change
→ material configuration change
```

Une qualification ou promotion Binance ne doit pas être utilisée silencieusement pour Kraken.

Runtime verification doit confirmer le venue réellement configuré lorsqu’il est matériel au deployment.

## 9. Operations

Les runbooks doivent devenir venue-aware sans modifier silencieusement la state machine OPS.

Ils doivent permettre de distinguer :

- incident Binance ;
- incident Kraken ;
- problème transverse Exchange ;
- instrument mapping error ;
- credential scope error ;
- reconciliation venue-specific.

```text
venue failure
≠ permission to switch venue
```

## 10. First Testnet Order — évolution future

La procédure future devra identifier explicitement :

- venue ;
- environment ;
- instrument canonique ;
- instrument natif ;
- credential identity non secrète ;
- qualification applicable ;
- artifact/configuration ;
- Risk authorization applicable.

Une procédure Binance n’est pas automatiquement une procédure Kraken.

L’ADR canonique existant `ADR-003 — Controlled first-order campaign boundary` reste inchangé et conserve son périmètre historique First Testnet Order.

## 11. Migration

### Phase 1
Inventory Binance coupling.

### Phase 2
Exchange-neutral contracts.

### Phase 3
Kraken read-only adapter sur `BTC/EUR`.

### Phase 4
Kraken qualification.

### Phase 5
Controlled preparation.

### Phase 6
Future economic authorization séparée.

Aucune phase économique Kraken n’est autorisée par `DOC-CHANGE-MX-001`.

## 12. Statuts

| Élément | Statut |
|---|---|
| ADR-004 | Accepted v1.0 |
| Architecture multi-exchange | Accepted |
| Binance support | Conservé |
| Kraken backend architecture | DESIGN / FOUNDATION PLANNING |
| Kraken initial foundation instrument | BTC/EUR — CTO approved |
| Kraken read-only implementation | Non démontrée |
| Kraken qualification | Non effectuée |
| Kraken runtime économique | Interdit |
| Kraken real orders | Interdits |
| Automatic venue fallback | Interdit |
| Multi-venue simultaneous execution | Non autorisée |

## 13. Gouvernance des futures SPEC

Les SPEC Accepted existantes doivent conserver :

- leur version historique ;
- leur historique de décisions ;
- leur scope initial.

Les futures consolidations peuvent produire de nouvelles versions.

Ne pas inventer de nouveaux IDs de SPEC tant que la séparation exacte entre :

- contrat Exchange canonique ;
- Binance-specific specification ;
- Kraken-specific specification

n’a pas fait l’objet d’une revue CTO explicite.

## 14. Clôture

`DOC-CHANGE-MX-001` est approuvé comme plan de consolidation documentaire.

Cette approbation :

```text
documentation approval
≠ runtime authorization
≠ economic authorization
≠ Live authorization
```

## 15. Historique

| Version | Date | Statut | Description |
|---|---|---|---|
| 0.1 | 2026-09-26 | Draft — CTO Review | Plan initial de consolidation multi-exchange, référençant temporairement la décision sous ADR-003 pendant la planification. |
| 1.0 | 2026-09-26 | Accepted — Approved for consolidation planning | CTO valide l’architecture documentaire, `BTC/EUR` pour le chantier Kraken foundation/read-only et la formulation restrictive sur le multi-venue. Références corrigées vers ADR-004 avant publication afin de préserver l’ADR-003 canonique existant. |
