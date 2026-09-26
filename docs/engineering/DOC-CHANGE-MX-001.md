# DOC-CHANGE-MX-001 — Consolidation documentaire multi-exchange

## Métadonnées

| Champ | Valeur |
|---|---|
| Document | DOC-CHANGE-MX-001 |
| Version | 1.0 |
| Statut | Accepted — Approved for consolidation planning |
| Autorité de validation | CTO |
| Domaine | Documentation / Exchange |
| Décision structurante | [ADR-004](../adr/ADR-004-multi-exchange-kraken.md) |
| Emplacement canonique | `docs/engineering/DOC-CHANGE-MX-001.md` |

## 1. Objet

Ce document matérialise le plan de consolidation documentaire de l'architecture
multi-exchange acceptée. Il organise les sources de vérité nécessaires à l'introduction de
Kraken sans modifier les décisions, contrats ou comportements existants de Binance.

Il ne constitue ni une autorisation d'implémentation économique, ni une qualification
Kraken, ni une autorisation Live.

## 2. Sources de vérité

- [ADR-002 — Plateforme modulaire ATP](../adr/ADR-002-modular-platform.md) : frontière
  Exchange Adapter du monolithe modulaire.
- [ADR-003 — Controlled first-order campaign boundary](../adr/ADR-003-controlled-first-order-campaign.md) :
  campagne contrôlée First Testnet Order Binance, inchangée.
- [ADR-004 — Architecture multi-exchange et introduction de Kraken](../adr/ADR-004-multi-exchange-kraken.md) :
  contrat canonique ATP, adapters et qualifications propres aux venues.

Les décisions existantes Binance, Risk, Testnet Activation, Testnet Qualification et First
Testnet Order restent applicables dans leur périmètre actuel.

## 3. Périmètre de consolidation

La consolidation doit maintenir une distinction explicite entre :

1. les contrats canoniques ATP ;
2. les détails natifs Binance ;
3. les détails natifs Kraken ;
4. la qualification Binance ;
5. la qualification Kraken ;
6. les capacités read-only ;
7. toute future autorité économique, qui reste hors périmètre.

Les documents Kraken utiliseront `BTC/EUR` comme instrument canonique initial de fondation
et de lecture. Les identifiants natifs Kraken resteront documentés au niveau de l'adapter et
de sa qualification.

## 4. Règles de référence

- Toute référence à la décision multi-exchange utilise `ADR-004` et son chemin canonique.
- `ADR-003` désigne exclusivement la campagne contrôlée First Testnet Order.
- Aucun document ne présente une qualification d'une venue comme valable pour une autre.
- Aucun document ne décrit un fallback automatique entre Binance et Kraken.
- Aucun document de fondation Kraken ne présente l'existence de code read-only comme une
  autorisation d'ordre.

## 5. Séquence documentaire

1. publier ADR-004 et le présent plan ;
2. documenter l'inventaire du couplage Binance dans la mission Kraken ;
3. documenter les contrats canoniques et le mapping d'instrument ;
4. documenter les routes publiques Kraken effectivement qualifiées ;
5. publier séparément les résultats de qualification Kraken ;
6. soumettre toute extension privée ou économique à une décision CTO préalable.

## 6. Hors périmètre

- modification ou renommage d'ADR-003 ;
- renumérotation des ADR historiques ;
- modification du runtime ou du code applicatif ;
- changement de la campagne First Testnet Order ;
- fallback, smart order routing, arbitrage ou exécution multi-venue ;
- credentials Kraken ;
- ordre, annulation, withdrawal ou runtime économique Kraken ;
- Live Kraken.

## 7. Critères de consolidation

La consolidation est cohérente lorsque :

- ADR-003 reste byte-for-byte inchangé ;
- toutes les références multi-exchange pointent vers ADR-004 ;
- Binance est décrit comme backend conservé ;
- Kraken est décrit comme backend ajouté ;
- les qualifications restent propres aux venues ;
- la sélection de venue reste explicite et sans fallback ;
- aucune autorité économique nouvelle n'est documentée.

## 8. Note historique

During documentation planning this decision was temporarily designated ADR-003. Repository
reconciliation identified an existing canonical ADR-003 governing the controlled first-order
campaign. The multi-exchange decision was therefore assigned ADR-004 before repository
publication. No substantive architectural decision changed.

## 9. Historique

| Version | Statut | Description |
|---|---|---|
| 1.0 | Accepted — Approved for consolidation planning | Publication du plan de consolidation et correction des références multi-exchange vers ADR-004. |
