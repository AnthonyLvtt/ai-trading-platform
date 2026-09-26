# ADR-004 — Architecture multi-exchange et introduction de Kraken

## Métadonnées

| Champ | Valeur |
|---|---|
| Document | ADR-004 |
| Version | 1.0 |
| Statut | Accepted |
| Autorité de validation | CTO |
| Domaine | Architecture Exchange |
| Emplacement canonique | `docs/adr/ADR-004-multi-exchange-kraken.md` |

## 1. Contexte

ATP dispose initialement d'un backend Binance Spot qualifié. L'architecture doit pouvoir
accueillir Kraken Spot comme seconde venue sans remplacer Binance, sans diffuser les
sémantiques natives d'un exchange dans le cœur métier et sans élargir l'autorité économique
du runtime.

L'instrument initial de fondation et de lecture Kraken est `BTC/EUR`, retenu pour son
alignement avec le périmètre européen. Cette sélection ne suppose aucune parité de symbole,
de filtre, de transport ou de comportement avec Binance.

## 2. Décision

ATP adopte une frontière multi-exchange additive organisée ainsi :

```text
contrat canonique ATP
→ adapter spécifique à la venue
→ qualification spécifique à la venue
```

Le cœur métier dépend de contrats ATP explicites. Chaque adapter traduit entre ces contrats
et les identifiants, payloads, contraintes, mécanismes d'authentification et réponses propres
à sa venue.

Binance reste supporté. Kraken est ajouté et ne se substitue pas à Binance.

## 3. Identité d'instrument

L'identité canonique ATP d'un instrument est distincte de tout identifiant natif d'exchange.

Pour la fondation Kraken initiale :

```text
instrument canonique ATP = BTC/EUR Spot
instrument natif Kraken = résolution explicite par l'adapter Kraken
```

La résolution native doit produire une preuve immuable liée aux métadonnées observées. Les
clés et alias natifs restent confinés à l'adapter. Une absence de mapping ou une ambiguïté
entre plusieurs instruments économiques admissibles bloque le traitement.

La multiplicité d'alias désignant le même instrument natif ne constitue pas, à elle seule,
une ambiguïté économique.

## 4. Sélection de venue

La venue est sélectionnée explicitement dans la composition du runtime. Cette sélection est
observable et auditable.

Il n'existe aucun fallback automatique :

- un échec Kraken ne route pas vers Binance ;
- un échec Binance ne route pas vers Kraken ;
- une sélection absente, inconnue ou incompatible bloque le traitement.

La présente décision n'autorise ni exécution simultanée multi-venue, ni smart order routing,
ni arbitrage.

## 5. Frontière des adapters

Chaque adapter possède ses propres :

- symboles et mappings natifs ;
- routes et enveloppes de réponse ;
- règles d'authentification ;
- modèles de métadonnées et de contraintes ;
- preuves de prix, de temps et de fraîcheur ;
- parsers de comptes et d'ordres ouverts lorsqu'ils sont autorisés ;
- qualification.

Les sémantiques Binance ne constituent pas un modèle universel. Un adapter ne fabrique pas
de filtres ou de payloads Binance pour représenter une contrainte native différente.

Toute contrainte requise inconnue, absente, ambiguë ou non qualifiée entraîne un blocage
fail-closed.

## 6. Qualification par venue

Binance et Kraken disposent de qualifications séparées, avec leurs propres identités,
preuves et résultats. Une qualification réussie pour une venue ne qualifie aucune autre
venue.

Les tests de contrat ordinaires restent déterministes et fondés sur des fixtures. Toute
qualification de connectivité publique est déclenchée explicitement et son échec reste un
échec Kraken ; il ne déclenche aucun appel Binance.

## 7. Introduction progressive de Kraken

La première étape Kraken est une fondation publique et read-only :

- temps serveur ;
- état du système ;
- métadonnées d'instruments ;
- chandeliers récents et clôturés ;
- preuve de prix publique qualifiée ;
- résolution canonique `BTC/EUR` vers l'identifiant natif exact.

Les accès privés read-only, credentials et preuves de permissions exigent une autorisation
et une qualification ultérieures séparées.

## 8. Invariants de sécurité

- Spot uniquement.
- Long uniquement pour les chemins métier concernés.
- Aucun leverage, margin, futures ou short.
- Risk reste déterministe et obligatoire avant toute future intention économique.
- `max_positions = 1` reste inchangé.
- AI/ML n'accède jamais à un chemin d'ordre économique.
- Les withdrawals restent interdits.
- Aucun fallback de venue.
- Aucun runtime économique Kraken n'est autorisé.
- Aucun ordre Kraken réel n'est autorisé.
- Live Kraken est interdit.

L'existence d'un adapter, d'une qualification réussie ou d'une preuve read-only ne constitue
pas une autorisation économique.

## 9. Migration

La migration est additive et progressive :

1. introduire les contrats canoniques de venue, instrument et ports publics ;
2. adapter le comportement Binance existant sans changer ses identités acceptées ;
3. ajouter et qualifier l'adapter public Kraken ;
4. étendre ultérieurement les capacités read-only privées après autorisation ;
5. soumettre toute capacité économique Kraken à une nouvelle décision CTO.

Le chemin First Testnet Order Binance, sa qualification, ses autorisations et son ledger ne
sont pas modifiés par la présente décision.

## 10. Conséquences

ATP peut intégrer plusieurs venues sans coupler son cœur métier à leurs formats natifs. Le
prix de cette séparation est une qualification indépendante et fail-closed de chaque mapping,
contrainte et capacité de venue.

La sélection explicite empêche qu'une indisponibilité soit transformée en changement de lieu
d'exécution implicite.

## 11. Note historique

During documentation planning this decision was temporarily designated ADR-003. Repository
reconciliation identified an existing canonical ADR-003 governing the controlled first-order
campaign. The multi-exchange decision was therefore assigned ADR-004 before repository
publication. No substantive architectural decision changed.

## 12. Historique

| Version | Statut | Description |
|---|---|---|
| 1.0 | Accepted | Architecture multi-exchange additive et introduction read-only de Kraken, avec sélection explicite et absence de fallback. |
