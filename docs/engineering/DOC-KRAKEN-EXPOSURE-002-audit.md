# DOC-KRAKEN-EXPOSURE-002 — Audit documentaire Kraken Spot / Exposure

## Métadonnées

| Champ | Valeur |
| --- | --- |
| Mission | DOC-KRAKEN-EXPOSURE-002 |
| Statut | Documentation Review Requested |
| Autorité | CTO |
| Baseline historique auditée | `ce068baca8df94f09181fd060f746193498d2598` |
| Baseline documentaire de correction | `d1abc33e7b2ba125fae09e507783b8ee1e209c5e` |
| Date | 2026-10-08 |

## 1. Règle de consolidation

Cet audit sépare cinq niveaux qui ne doivent jamais être confondus :

1. connectivité API authentifiée ;
2. permissions read-only ;
3. preuve d'exposition financière ;
4. qualification opérationnelle ;
5. autorisation de trading réel.

Une réussite à un niveau ne constitue pas une réussite du niveau suivant.

```text
API connectivity
≠ read-only permission qualification
≠ financial evidence
≠ operational qualification
≠ real-trading authorization
```

Le trading réel reste interdit.

## 2. Faits historiques à préserver

### Exposure Gate — tentative du 8 octobre 2026 sur ce068…

Le résultat historique observé sous le contrat alors effectif est conservé sans
réécriture rétroactive :

```text
status = FAILED
reason_code = EXPOSURE_GATE_BALANCE_EUR_MISSING
runtime_pass_qualified = false
completed_routes = ["/0/private/GetApiKeyInfo"]
total_private_network_calls = 2
real_economic_calls = 0
side_effect_performed = false
LIVE = LIVE_FORBIDDEN
```

`BalanceEx` a été interrogée mais n'a pas produit une preuve financière
qualifiée. `TradeVolume` n'a pas été exécuté.

Ce résultat n'efface pas la qualification historique séparée
`PRIVATE_CONNECTIVITY` de ENG-EXCH-KRAKEN-003, qui concernait
`GetApiKeyInfo + Balance + OpenOrders` et non l'Exposure Operator Gate.

## 3. Évolution après ENG-KRAKEN-EXPOSURE-002

La PR #66 a été mergée après la baseline historique. Le comportement courant
distingue désormais une ligne EUR absente d'un solde numérique zéro.

```text
BalanceEx syntactically valid
+ no EUR row
→ INCOMPLETE
→ EUR_BALANCE_NOT_OBSERVED
→ value = None
→ no TradeVolume request
→ runtime_pass_qualified = false
```

Un EUR explicite égal à zéro reste un vrai montant numérique observé et n'est
pas équivalent à une ligne absente.

Cette évolution ne réécrit pas l'échec historique de la baseline `ce068…`.

## 4. Diagnostics BalanceEx

La PR #65 a introduit des diagnostics assainis spécialisés :

- `EXPOSURE_GATE_BALANCE_EUR_MISSING`;
- `EXPOSURE_GATE_BALANCE_FIELDS_INCOMPLETE`;
- `EXPOSURE_GATE_BALANCE_ASSET_UNSUPPORTED`.

Les diagnostics génériques existants, dont
`EXPOSURE_GATE_EXPOSURE_EVIDENCE_INVALID`, restent valides lorsque leur
condition générale s'applique.

Après ENG-KRAKEN-EXPOSURE-002, l'absence simple d'une ligne EUR sur une réponse
BalanceEx autrement valide est représentée par
`EXPOSURE_GATE_EUR_BALANCE_NOT_OBSERVED` et `INCOMPLETE`.

## 5. Matrice d'audit

| Document | Section | Problème / constat | Action | Justification | Impact | Validation |
| --- | --- | --- | --- | --- | --- | --- |
| `docs/engineering/ENG-EXCH-KRAKEN-003-qualification.md` | Operational closeout | Contient un `PASSED` réel qui peut être confondu avec l'Exposure Gate. Il porte en réalité sur PRIVATE_CONNECTIVITY avec GetApiKeyInfo, Balance et OpenOrders. | CONSERVER | Qualification historique distincte et valide ; la supprimer falsifierait l'historique. | Non normatif / historique | CTO |
| `docs/engineering/ENG-EXCH-KRAKEN-019-balance-ex-offline.md` | Parser / authority boundary | Refuse déjà l'inférence de zéro lorsque les faits financiers requis ne sont pas explicites. | CONSERVER | Cohérent avec fail-closed et avec l'interdiction de déduire un montant non observé. | Non normatif | Documentation Architect / CTO si réinterprétation |
| `docs/engineering/ENG-EXCH-KRAKEN-020-private-observation-preparation.md` | Sanitized evidence and PASS/FAIL | Ancien texte assimilait implicitement toute absence de ligne EUR à FAILED et décrivait toujours deux observations. | CORRIGER | PR #66 distingue `EUR_BALANCE_NOT_OBSERVED` / INCOMPLETE et arrête avant TradeVolume. | Non normatif | CTO review |
| `docs/engineering/ENG-EXCH-KRAKEN-021-bounded-exposure-transport.md` | Boundary | Le transport borné, l'absence de route économique et le besoin d'autorisation explicite restent corrects. | CONSERVER | Les appels restent read-only, sans retry/fallback ni autorité économique. | Non normatif | CTO |
| `docs/engineering/ENG-EXCH-KRAKEN-022-exposure-qualification-runner.md` | Runner sequence | Affirmait que BalanceEx et TradeVolume étaient toujours tous deux envoyés. | CORRIGER | Le comportement courant short-circuite sur EUR non observé ; TradeVolume n'est alors pas exécuté. | Non normatif | CTO review |
| `docs/engineering/ENG-EXCH-KRAKEN-023-exposure-operational-gate.md` | Gate sequence / result | Texte pré-opérationnel devenu incomplet et sans closeout de l'échec réel ; diagnostics BalanceEx spécialisés absents. | CORRIGER | Baseline `ce068…`, PR #65, résultat historique du 08/10 et PR #66. | Non normatif / historique | CTO review |
| `docs/engineering/ENG-EXCH-KRAKEN-024-exposure-operator-command.md` | Operator procedure | Présentait trois lectures comme séquence toujours effectuée et ne documentait pas l'échec réel. | CORRIGER | La séquence est maximale ; l'exécution historique s'est arrêtée avant TradeVolume et la PR #66 formalise le short-circuit. | Non normatif / opérationnel | CTO review |
| `docs/engineering/ENG-OMS-KRAKEN-003-evidence-source-qualification.md` | Authority boundary | `runtime_pass_qualified=False` et sources financières non qualifiées restent exacts. | CONSERVER | La PR #66 ajoute un diagnostic, elle ne qualifie pas une exposition financière complète. | Non normatif | CTO |
| `docs/engineering/ENG-OMS-KRAKEN-004-offline-source-producers.md` | Spendable EUR | Exige déjà une ligne EUR complète pour produire `SpendableEurEvidence`. | CONSERVER | Une ligne absente ne produit toujours aucun montant spendable. | Non normatif | CTO |
| `docs/engineering/ENG-OMS-KRAKEN-005-private-source-preflight.md` | Real read-only qualification | Présentait l'absence EUR comme une ambiguïté future à résoudre ; cette distinction est maintenant implémentée par PR #66. | CORRIGER | Le contrat courant représente l'absence par `EUR_BALANCE_NOT_OBSERVED`, sans zéro implicite ni PASS. | Non normatif | CTO review |
| PR #65 / merge `ce068baca…` | BalanceEx diagnostics | Traçabilité du changement diagnostique nécessaire. | CONSERVER | Source des codes spécialisés BALANCE_EUR_MISSING / FIELDS_INCOMPLETE / ASSET_UNSUPPORTED. | Historique technique | CTO |
| PR #66 / merge `d1abc33e…` | EUR not observed | Initialement proposé dans la mission ; désormais mergé avec validation annoncée Ruff/mypy/881 tests. | CONSERVER / ACTUALISER | Le comportement n'est plus futur ; il est implémenté, mais n'accorde aucune qualification économique. | Technique + documentation | CTO |
| Documentation Accepted (ADR/SPEC) | Toutes | Aucune modification normative n'est nécessaire pour enregistrer les faits opérationnels ci-dessus dans ce PR. | DIFFÉRER | Toute révision normative doit suivre sa procédure dédiée et préserver les versions Accepted. | Normatif | CTO |
| Documentation/exemples opérationnels | Secrets / IIBAN / payloads / soldes | Aucun secret brut, IIBAN réel, payload privé réel ou montant financier réel n'a été trouvé dans les documents ciblés par cet audit. | CONSERVER les règles de redaction ; SUPPRIMER immédiatement toute valeur brute si découverte ultérieurement | SPEC-SEC / principe no-secret-telemetry et instruction CTO. | Sécurité | CTO / Security |
| Procédure opérateur | Funding / retry | Aucun dépôt de fonds ne doit être requis pour faire passer une qualification technique ; pas de boucle de retry identique après absence EUR. | CORRIGER / CONSERVER l'interdiction | Funding n'est ni preuve technique ni remède à une observation absente. | Opérationnel / sécurité | CTO |

## 6. Informations sensibles

Les documents corrigés n'introduisent aucune :

- clé API ;
- secret Kraken ;
- valeur d'IIBAN ;
- payload privé réel ;
- balance réelle ;
- montant financier réel ;
- donnée permettant de reconstruire un secret.

Les exemples restent fondés sur états, reason codes, identités opaques et compteurs.

## 7. Procédure opérateur

À la réception de `EUR_BALANCE_NOT_OBSERVED` :

- ne pas interpréter l'absence comme zéro ;
- ne pas interpréter l'absence comme montant positif ;
- ne pas déposer des fonds uniquement pour faire passer le test ;
- ne pas relancer indéfiniment la même qualification dans l'espoir d'un autre résultat ;
- conserver le diagnostic assaini et la source exacte ;
- toute nouvelle observation réelle exige l'autorisation opérationnelle applicable au SHA alors approuvé.

Aucune qualification read-only n'autorise le trading réel.

## 8. État de clôture documentaire

La documentation distingue désormais explicitement :

1. connectivité API ;
2. permissions read-only ;
3. preuve d'exposition financière ;
4. qualification opérationnelle ;
5. trading réel, toujours interdit.

Invariants conservés :

```text
runtime_pass_qualified = false
real_economic_calls = 0
side_effect_performed = false
LIVE = LIVE_FORBIDDEN
```

L'historique d'échec de `ce068…` reste conservé, même après l'évolution de
contrat introduite par ENG-KRAKEN-EXPOSURE-002.
