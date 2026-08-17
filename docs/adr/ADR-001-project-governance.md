# ADR-001 — Gouvernance du projet ATP

## Métadonnées

| Champ | Valeur |
|---|---|
| Document | ADR-001 |
| Version | 1.0 |
| Statut | Accepted |
| Autorité de validation | CTO |
| Domaine | Gouvernance |
| Emplacement canonique attendu | `docs/adr/ADR-001-project-governance.md` |

> Note de restitution : le fichier autonome d’origine n’était pas disponible dans les sources accessibles lors de cet export. Le présent fichier restitue uniquement les décisions ADR-001 explicitement démontrées par les documents ATP disponibles ; aucune règle nouvelle n’est ajoutée.

## 1. Contexte

ATP exige une gouvernance explicite afin de séparer les responsabilités business, techniques, d’implémentation et documentaires, et d’empêcher qu’une décision structurante soit introduite implicitement par le code ou la documentation.

GitHub constitue la source de vérité du projet. Les décisions structurantes doivent être documentées par ADR.

## 2. Décision

Les rôles ATP sont séparés comme suit.

| Rôle | Autorité / responsabilité |
|---|---|
| CEO | Vision, priorités et décisions business |
| CTO | Architecture, sécurité, règles de trading, gestion du risque, IA, spécifications et validation des pull requests |
| Lead Engineer | Implémentation, tests, intégration continue et pull requests |
| Documentation Architect | Documentation officielle des décisions validées |

Principes de gouvernance :

- aucun rôle ne dépasse son autorité ;
- aucune pull request n’est fusionnée sans validation du CTO ;
- une décision structurante nécessite un ADR ;
- le Documentation Architect ne transforme pas une proposition non validée en décision normative ;
- l’implémentation doit rester cohérente avec les décisions CTO et les documents Accepted ;
- GitHub est la source de vérité du projet.

## 3. Cycle de vie des décisions

```text
Besoin
→ vision / priorités CEO
→ décision technique CTO
→ ADR si décision structurante
→ validation CTO
→ implémentation Lead Engineer
→ documentation officielle
```

Une implémentation ne constitue pas, à elle seule, une validation normative.

## 4. Règles de changement

Toute modification structurante d’architecture, de sécurité, de règles de trading, de gestion du risque, d’IA ou de gouvernance nécessite une décision CTO avant d’être considérée normative.

Les écarts entre code et documentation doivent être signalés ; ils ne doivent pas être corrigés silencieusement en réinterprétant une décision validée.

## 5. Pull requests

Le Lead Engineer prépare les changements, leurs tests et les preuves nécessaires.

Le CTO valide les pull requests avant merge.

Invariant :

```text
PR techniquement verte
≠ PR autorisée à être fusionnée
```

## 6. Documentation

Les documents Accepted constituent les sources normatives applicables.

Une proposition non encore validée doit être explicitement identifiable comme telle et ne doit pas être présentée comme une décision CTO.

## 7. Conséquences

Cette gouvernance :

- sépare décision, implémentation et documentation ;
- empêche une dérive d’autorité entre rôles ;
- impose une validation CTO des changements structurants ;
- rend les décisions structurantes traçables par ADR ;
- maintient GitHub comme source de vérité.

## 8. Invariants

- CEO ≠ CTO.
- CTO ≠ Lead Engineer.
- Lead Engineer ≠ Documentation Architect.
- Implémentation ≠ décision normative.
- Documentation ≠ autorité de validation.
- Tests verts ≠ merge autorisé.
- Décision structurante ≠ changement silencieux.
- Aucun rôle ne dépasse son autorité.

## 9. Historique

| Version | Statut | Description |
|---|---|---|
| 1.0 | Accepted | Gouvernance ATP, séparation des rôles, cycle de décision, ADR et validation CTO des pull requests. |
