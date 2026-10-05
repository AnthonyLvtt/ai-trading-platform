# ADR-005 — Kraken-only exchange architecture

## Métadonnées

| Champ | Valeur |
|---|---|
| Document | ADR-005 |
| Version | 1.0 |
| Statut | Accepted |
| Autorité de validation | CTO |
| Domaine | Architecture Exchange |
| Emplacement canonique | `docs/adr/ADR-005-kraken-only.md` |

## 1. Contexte

ATP cible une seule venue d'exécution et de données : Kraken. Le cœur métier doit rester
indépendant des formats natifs de la venue et toute capacité privée ou économique doit être
explicitement qualifiée avant utilisation.

L'instrument canonique initial est `BTC/EUR` Spot. Les alias natifs restent confinés à
l'adapter Kraken et à ses preuves de résolution.

## 2. Décision

ATP adopte une architecture Kraken-only :

```text
contrat canonique ATP
→ adapter Kraken
→ qualification Kraken
```

La sélection de venue implicite ou automatique n'existe pas. Toute configuration de venue
absente, inconnue ou différente de Kraken bloque le traitement.

## 3. Frontière de l'adapter Kraken

L'adapter Kraken porte exclusivement :

- les identifiants et mappings natifs ;
- les routes et enveloppes de réponse ;
- l'authentification ;
- les métadonnées et contraintes ;
- les preuves de temps, prix et fraîcheur ;
- les lectures privées explicitement qualifiées ;
- la qualification propre à Kraken.

Toute contrainte requise inconnue, absente, ambiguë ou non qualifiée entraîne un blocage
fail-closed.

## 4. Capacités actuellement qualifiées

Les capacités publiques Kraken et les lectures privées read-only déjà qualifiées restent
autorisées dans leur périmètre exact.

Aucune capacité économique n'est autorisée par la présente décision.

## 5. Invariants de sécurité

- Spot uniquement.
- Long uniquement pour les chemins métier concernés.
- `max_positions = 1`.
- Risk reste déterministe et obligatoire avant toute future intention économique.
- AI/ML n'accède jamais à un chemin d'ordre économique.
- Withdrawals interdits.
- Aucun leverage, margin, futures ou short.
- Aucun fallback de venue.
- Aucun ordre économique n'est autorisé.
- Live est interdit.
- La possession de credentials ne constitue jamais une autorisation.

## 6. Qualification

Chaque capacité Kraken doit être qualifiée séparément avec une identité de source et des
preuves explicites. Une qualification read-only ne qualifie aucune capacité économique.

Les tests ordinaires restent déterministes et sans accès réseau privé réel. Toute
qualification réelle est déclenchée explicitement selon une mission CTO dédiée.

## 7. Extension future

Toute extension vers une capacité économique Kraken exige une nouvelle décision CTO, un
contrat dédié, des tests fail-closed, une qualification séparée et une autorisation
opérationnelle explicite liée à une révision exacte.

## 8. Historique

| Version | Statut | Description |
|---|---|---|
| 1.0 | Accepted | Architecture Kraken-only et conservation des seules capacités déjà qualifiées. |
