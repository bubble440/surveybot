# BDL-4 — Audit et implémentation de l'oracle d'extraction

**Branche de référence :** `BDL`.
**État :** audit initial terminé ; étapes 1 et 2 implémentées ; étape 3 à réaliser.
**Validation de collecte :** run en mode attach du 2026-11-15.
**Convention :** `[V]` = vérifié ; `[NV]` = non vérifié. Les constats de l'audit initial restent historiques et doivent être réévalués si le code évolue.
---

## 0. Résumé exécutif

1. **Correction importante du cadrage.** Le suivi BDL (§20 quinquies, point 4) affirme que les artefacts sont « conditionnés à l'échec du validateur ». C'est vrai pour l'**action** et pour l'observateur d'extraction, mais **pas pour l'extraction dans son ensemble** : `survey_executor.py` appelle `page_snapshot.snapshot_if_enabled(reason="after_dom_analyze", question_blocks=extracted_question_blocks)` après chaque `analyze_dom`, et `SURVEY_SNAPSHOT` vaut `"1"` par défaut `[V]`. Un snapshot inconditionnel (DOM + `question_blocks.json`) existe donc **dans le code**. Sa valeur effective sur le parc et en local est `[NV]` (aucun script du dépôt ne positionne cette variable ; le corpus de 38 cas ne contient que des dossiers `*_validation_failure`, ce qui suggère qu'elle est neutralisée ou que les dossiers sont purgés).
2. **Le vrai trou n'est pas la capture, c'est la traçabilité des règles.** Sur 16 règles d'extraction inventoriées, **seules 4 laissent une trace exploitable** dans `question_blocks.json`. Les autres absorbent sans trace persistée → l'oracle ne peut pas distinguer « absent » de « absorbé » aujourd'hui.
3. **Décision et avancement :** la trace passive des règles (étape 1) et la collecte des extractions réussies (étape 2) sont implémentées. La collecte a été validée en mode attach. L'étape restante est l'oracle minimal d'extraction, à construire sur les artefacts collectés, avec abstention par défaut.

---

## Q1 — Artefacts d'entrée

### Constats vérifiés

| Artefact | Producteur | Condition de persistance |
|---|---|---|
| `question_blocks.json` + DOM + captures | `page_snapshot.dump_page_snapshot` via `snapshot_if_enabled(reason="after_dom_analyze")`, 4 sites dans `survey_executor.py` (~2384, 2420, 2442, 2478) | **Inconditionnel si `SURVEY_SNAPSHOT` vaut `1`/`true`/`all`/`on`/`yes` (défaut `"1"`)** `[V]`. Indépendant du validateur et de `RUN_ENV` (racine `./snapshots` ou `/tmp/snapshots`). |
| Blocs + rapport + snapshot | `_install_extraction_observer` (enveloppe `analyze_dom`) → `record_validation_failure(stage="extraction")` | **Uniquement si `validate_question_blocks` renvoie `ok=False`** `[V]`. Observateur installé seulement si `SURVEY_OBSERVABILITY` actif (défaut : `0` en prod, `1` hors prod) `[V]`. |
| Blocs en mémoire | `_LAST_EXTRACTED_BLOCKS[id(driver)]` | Éphémère, présent seulement si l'observateur est installé `[V]`. |
| Rapport du validateur | `validate_question_blocks` | Dans `validation_report.json`, échec seulement `[V]`. |

Points d'attention vérifiés :
- Les blocs persistés sont l'état **final après toutes les règles** (`analyze_dom`, chaîne `dedupe → merge_nfield → prune_*` aux lignes ~5030-5037). L'état **avant règles n'existe nulle part**.
- `question_blocks.json` est écrit dans un `try/except: pass` : un bloc non sérialisable fait disparaître le fichier **sans trace** `[V]`.
- `dump_page_snapshot` n'est pas léger : attente `_wait_dom_settle` jusqu'à 6 s, DOM, `page_source`, screenshot, MHTML, frames, scripts/styles externes (mesures du suivi : externes jusqu'à ~950 Ko, MHTML ~550 Ko) `[V]` pour le code, tailles reprises des mesures du suivi.
- `validate_question_blocks` exécute des signaux JS (`_help_text_as_question_signals`, `_hidden_block_visible_choice_signal`) **à chaque** `analyze_dom` quand l'observateur est actif ; le surcoût navigateur réel est `[NV]`.

### Réponse

Un oracle placé **après le validateur** ne voit pas les bugs validés à tort — mais pour l'extraction, le chemin `after_dom_analyze` offre déjà DOM + blocs sans condition d'échec **si le flag est actif**.

**Coût d'une capture inconditionnelle supplémentaire :** si `SURVEY_SNAPSHOT=1`, le coût est déjà payé ; sinon, il faut éviter le snapshot complet. Pour un oracle structurel, **les blocs seuls (JSON, quelques Ko) suffisent**, sans DOM ni navigateur : coût navigateur = 0 `[V par construction : les blocs existent déjà en mémoire]`.

**Challenge :** ne pas activer `SURVEY_SNAPSHOT=1` en prod « pour l'oracle » — 6 s d'attente + MHTML par page est incompatible avec « budget-friendly ». Collecter les blocs, pas le DOM.

---

## Q2 — Absent ou absorbé : traçabilité des règles

### Constats vérifiés (inventaire croisé avec le code)

Ordre d'application dans `analyze_dom` (`dom_analyzer.py:5030-5037`) : `_dedupe_question_blocks` → `_merge_nfield_rowpicker_exclusive_radio` → `_prune_focusvision_fragmented_groups` → `_prune_focusvision_auxiliary_openended_singles` → `_prune_trailing_open_inline_singles` → `_prune_cardrating_focusvision_blocks` → `_prune_cardrating_unlabeled_blocks_favor_row_groups`.

| Règle | Altère | Trace persistée dans `question_blocks.json` ? |
|---|---|---|
| Fusion nField rowpicker/radio exclusif | absorbe un radio dans un checkbox | **Oui** — `context.exclusive_options` `[V]` |
| Fusion de groupes `merge_single_name_inputs` | fusionne des groupes | **Partielle** — visible dans le groupe fusionné, arbitrage en log |
| Dédup/fusion `_dedupe_question_blocks` | union d'options, question « moins polluée » | **Résultat seul** ; l'arbitrage est invisible |
| Résolution bouton-groupe → checkbox, override `min_select` Qualtrics, tri DOM | itype / limites / ordre | **Oui**, dans les champs du bloc |
| « Autre » inline (détection auxiliaire) | supprime un champ texte avant création de bloc | **Résiduelle** — `context.inline_openend_names` sur le bloc radio/checkbox hôte (`dom_analyzer.py:3085-3086`) `[V]` ; aucun compteur d'effet |
| Élagage « Autre » FocusVision | supprime un text/textarea | **Résiduelle** — `context.aux_openended_names` `[V]` (cas sans `name` : aucune trace) |
| Élagage trailing « Autre » inline | supprime un text | **Non** — `log_debug` seulement (`:5613`) |
| Heuristique champ générique/filtre (`_is_auxiliary_text_for_choice_group`, seuil `score≥3`) | supprime un champ | **Non** — `log_debug` seulement |
| Opt-out checkbox companion | supprime un checkbox | **Compteur seulement** (`group_reject_reasons`), qui n'est imprimé que si `is_debug()` (`:3169-3174`) |
| Élagages FocusVision fragments / cardrating ×2 / matrice vs bouton-groupe | suppriment des blocs | **Non** — log ou rien |

Autres constats : `group_reject_reasons` n'est jamais persisté ; `question_block_analyzer.py` n'est importé nulle part (code non intégré, vérifié par grep, cohérent avec le suivi) `[V]`.

### Réponse

- **Laissent une trace persistée (exploitable) :** nField (`exclusive_options`), « Autre » inline et FocusVision (via `inline_openend_names` / `aux_openended_names`), champs du bloc pour les règles de typage/limites.
- **Aucune trace :** heuristique générique/filtre, trailing inline, matrice vs bouton-groupe, élagages FocusVision fragments/cardrating, arbitrage de dédup.
- **Pour distinguer « absent » de « absorbé par la règle X » il faut observer, pour chaque déclenchement :** `rule_id`, effet (`removed` / `merged`), identifiant du bloc ou du champ concerné (`name`/`id`/`group_key`), et nombre d'éléments retirés. Rien d'autre.

**Challenge à une idée implicite du suivi :** la trace des règles est présentée comme prérequis de l'oracle. Ce n'est vrai que pour les verdicts **« élément absent »**. Les contradictions **internes aux blocs** (Q3) n'en dépendent pas — d'où l'ordre des étapes plus bas.

---

## Q3 — Signaux existants et contradictions fortes détectables

### Réutilisables comme preuve indépendante

| Signal | Pourquoi |
|---|---|
| `choice_without_options` (radio/checkbox/dropdown) | Cohérence du bloc ; contrat BDL-1 l'exige explicitement hors `matrix`. Mais voir réserve dropdown ci-dessous. |
| `invalid_selection_limits`, `max_select_exceeds_options` | Invariants arithmétiques purs, indépendants du DOM. |
| `duplicate_options` | Invariant du contrat (types de choix), à sévérité modérée (widgets pouvant réutiliser des libellés). |
| `missing_block` provider-spécifiques (Qualtrics ranked, Zappi, Focaldata, Netsurvey) | Pattern DOM strict + liste vide ; fiables **sur leur site** seulement. |
| `question_text_is_help` | Compare à un nœud titre du même conteneur ; limite documentée : pas inter-frames. |

### Circulaires ou peu fiables

- `registry_target_missing` : compare deux structures **produites par le même extracteur** — utile comme garde-fou de forme, pas comme preuve sémantique.
- `min_select`/`max_select` : valeurs **interprétées** (mots-clés + comptage d'options), pas un comptage DOM.
- `hidden_block_visible_choice` : **décline silencieusement** au-delà de ses budgets (>30 blocs, >100 options) → le silence ne signifie pas « sain ».
- `_question_pollution_penalty` / `_block_quality_score` : locaux à la dédup, non persistés, mesurent un arbitrage relatif.
- `itype` seul : le contrat §2 liste 10 types, mais le corpus ne contient que 4 (`radio`, `checkbox`, `text`, `dropdown`) `[V par le suivi]`.

### Contradictions fortes sans second extracteur, par itype du contrat

| itype | Contradiction exploitable | Source de preuve | Verdict maximal |
|---|---|---|---|
| radio, checkbox | liste d'options vide / `max_select` > nb d'options / `min>max` | bloc seul | `BUG_CONFIRMED` (structurel) |
| radio | `max_select>1` déclaré | bloc seul | `SUSPECTED` |
| radio, checkbox | options en doublon après normalisation | bloc seul | `SUSPECTED` |
| radio, checkbox, dropdown | `option_xpath_map` absent ou plus petit que `options` | bloc seul — **à valider** `[NV]` qu'il survit à la dédup (le suivant le dit `potentiellement`) | `SUSPECTED` |
| text, textarea, number | `options` non vide | bloc seul | `SUSPECTED` |
| matrix | `matrix_rows`/`matrix_columns` présents mais vides | bloc seul | `SUSPECTED` |
| button | compté comme question répondable | bloc seul | exclusion (pas un verdict) |
| tous | `itype` hors contrat | bloc seul | « non documenté » (revue du contrat, jamais un bug) |
| dropdown, select | options vides | — | **abstention** (le contrat l'interdit comme preuve) |

Tout ce qui compare **DOM observable ↔ représentation** (option visible absente des options du bloc) exige un comptage DOM = un second extracteur. **Je recommande de ne pas le faire en v1** : seul le DOM d'un cas déjà bloqué par un signal provider-spécifique est comparé, ce qui existe déjà.

---

## Q4 — Corpus de test

### Constats vérifiés (index + inventaire)

- 38 cas indexés (~21 situations indépendantes), dont **5 d'extraction** : Focaldata `missing_block` ×1, Panelia `missing_block` ×1, Qualtrics `choice_without_options` ×2 (même page), SurveySip `question_text_is_help` ×2 (même page), MetrixLab `missing_block` ×1.
- **Aucun** cas d'extraction n'a de vérité terrain documentée de façon indépendante (la seule plausible, MetrixLab, ne l'est pas par identifiant exact).
- Inventaire BDL-1 historique : 39 fichiers, 55 blocs, 4 `itype`, **4 signalements jamais triés**.
- `Golden_DOMs/` : 1 copie manuelle d'un cas **d'action**, pas d'extraction.
- Corpus = **suite de non-régression, pas un benchmark** : aucune précision ni rappel calculable.

### Ce qui manque

1. Des **blocs d'extractions réussies** (vrais négatifs) : aucun aujourd'hui.
2. Les **types non observés** (`matrix`, `number`, `select`, `select_rps`, `textarea`, `button`) : zéro bloc.
3. Une **vérité terrain** minimale : pour 20 pages, « ce que le modèle de réponse aurait dû voir » (annotation humaine, 1 ligne par bloc).
4. Les **4 signalements** de l'inventaire BDL-1, à trier : ils sont gratuits et déjà là.

---

## Q5 — Mode collecte

### Constat

Le mode collecte BDL-2b couvre **l'action seulement** (`record_collected_action`, `failure_recorder.py`) ; l'extraction n'y figure pas `[V]`, et le suivi le dit.

### Réponse

**Oui, nécessaire, mais minuscule.** Périmètre minimal, une seule stratégie :

- Par page analysée : écrire **un seul fichier JSON** `extraction_pass.json` = blocs finaux (champs déjà sérialisables : `itype`, `question`, `options`, `min/max_select`, `target_id`, `context` allégé) + liste des déclenchements de règles (Q2) + `url` sans paramètres de session.
- **Pas** de DOM, pas de screenshot, pas de MHTML.
- Même mécanisme que BDL-2b : flag distinct (`SURVEY_BDL_COLLECT`), racine distincte, plafond par racine (200), abandon contrôlé avec 1 log info.
- Un échantillonnage **déterministe** (1 page sur N, N à fixer ; hypothèse de départ 1/5) évite de saturer le plafond avec des pages identiques.

**Challenge :** ne pas attendre le mode collecte pour mesurer. Tant que les blocs persistés sont ceux d'`after_dom_analyze` **et** que le flag est actif en local, le corpus de vrais négatifs peut commencer **sans aucun code**. À vérifier d'abord (`[NV]` : l'état réel de `SURVEY_SNAPSHOT` sur ta machine).

---

## Q6 — Risques de faux positifs

| Source | Risque | Garde proposée |
|---|---|---|
| Règles historiques (« Autre », élagages FocusVision, opt-out companion) | une différence DOM↔blocs est volontaire | v1 **ne compare pas** au DOM ; n'émet aucun verdict d'« absence » sans trace de règle |
| Variabilité intra-source | un même provider a plusieurs structures | aucun verdict conditionné au nom du provider ; signaux scopés par structure de bloc |
| Pages dynamiques (slides, carrousels, contenu injecté) | blocs capturés avant/après changement d'état ; carrousels : faits identiques d'un groupe à l'autre (déjà observé en BDL-3) | abstention si plusieurs blocs partagent le même `group_key` ou si le bloc change entre deux captures |
| Dropdown / select | options chargées à l'ouverture, `select` avec `options=[]` atteste par le code | **abstention structurelle** |
| `matrix` | lignes/colonnes dans `context`, pas dans `options` | ne pas exiger d'options ; vérifier seulement les clés présentes |
| Listes vides | légitimes sur transitions sans question | pas de verdict de « bloc absent » hors signaux provider-spécifiques existants |
| `itype` inconnu | nouveau type ≠ bug | journalisé « non documenté », jamais promu |

**Cible réaliste (conforme à ton principe 80-90 % stable) :** ~6 règles structurelles qui ne se déclenchent presque jamais à tort, plutôt qu'un oracle qui prétend couvrir 100 %.

---

## État d'implémentation du plan BDL-4

Les étapes 1 et 2 sont implémentées. L'étape 2 a été validée par une
collecte réelle en mode attach.

### Étape 1 — Trace passive des déclenchements de règles

**Statut : IMPLÉMENTÉE ET VALIDÉE**

Module : `Survey/extraction_rule_trace.py`.

API :
- `note_rule(rule_id, effect, ref, count)` : enregistre un événement.
- `drain()` : récupère et vide les événements accumulés.
- `count_events()` : expose le nombre d'événements enregistrés.

La trace permet de conserver les informations relatives aux règles
d'extraction qui retirent ou absorbent des éléments, sans modifier le
résultat fonctionnel de l'extraction.

### Étape 2 — Persister la trace avec les blocs (hors DOM)

**Statut : IMPLÉMENTÉE — COLLECTE VALIDÉE EN MODE ATTACH**

Fichiers concernés :
- `Survey/page_snapshot.py` : hook de collecte après extraction.
- `Survey/failure_recorder.py` : persistance des extractions collectées,
  échantillonnage et traçabilité des exclusions.
- `Survey/extraction_rule_trace.py` : événements de règles associés
  à l'extraction.

Validation observée le 2026-11-15 :

- Le run en mode attach a produit une collecte dans
  `bdl_collect_extraction`.
- Une extraction radio a été enregistrée avec un bloc.
- Un événement de règle `aux_text_choice_group`, effet `removed`,
  a été persisté.
- La collecte d'action existante a continué à fonctionner.

Échantillonnage :

- `SURVEY_BDL_EXTRACT_SAMPLE=1` force la collecte de chaque page éligible.
- Une trace explicite est désormais produite lorsqu'une page éligible
  est ignorée par l'échantillonnage.
- Aucune trace d'exclusion supplémentaire n'est produite pour une page
  effectivement collectée.

Le plafond de collecte reste fixé à 200 éléments.

### Étape 3 — Oracle d'extraction minimal (`Survey/extraction_oracle.py`)

**Statut : À FAIRE**

Fonction pure sur les artefacts de l'étape 2, calquée sur
`action_oracle.py`.

Verdicts :
`PASS / SUSPECTED / BUG_CONFIRMED / INCONCLUSIVE`.

Signaux v1 envisagés :

- options vides (radio/checkbox) ;
- limites incohérentes ;
- `max_select>1` sur radio ;
- options dupliquées ;
- `options` non vides sur text/textarea/number ;
- `itype` hors contrat.

L'oracle doit écrire son résultat dans le JSON collecté uniquement.
Il ne doit pas modifier le validateur, `failure_cases` ou AutoFix.

**Mesure attendue :** distribution des verdicts sur le corpus collecté,
avec revue manuelle de 100 % des `SUSPECTED`.

### Étape 4 — Annotation et mesure

**Statut : À FAIRE**

- Annoter environ 20 pages avec une vérité terrain manuelle.
- Trier les 4 signalements BDL-1.
- Ajouter un test par `itype` observé.
- Fixer les critères de clôture avant l'implémentation de cette étape.

### Hors BDL-4, à ne pas mélanger
- Décider du sort de `question_block_analyzer.py` (non intégré).
- Corriger les deux coquilles de `_sanitize_instruction_with_page_context` (déjà notées en tâche distincte).
- Ne pas copier `snapshots/` tel quel dans le dépôt public : URLs avec jetons de session (déjà signalé dans le suivi).

---

## Points non vérifiés (liste finale)

1. Valeur réelle de `SURVEY_SNAPSHOT` / `SURVEY_OBSERVABILITY` sur chaque machine et sur le parc.
2. Surcoût navigateur réel du validateur d'extraction par page et d'un snapshot `after_dom_analyze`.
3. Si `option_xpath_map` survit à la dédup et à la sérialisation de `question_blocks.json` dans tous les cas.
4. Fréquence réelle de blocs non sérialisables (fichier absent silencieusement).
5. Comparaison DOM des cas Qualtrics/SurveySip entre eux (même page ou occurrences indépendantes).
6. Le contenu détaillé des 4 signalements de l'inventaire BDL-1 historique.
7. Le corps de `_is_auxiliary_text_for_choice_group` n'a été relu qu'à travers `BUG_DETECTION_LAYER_VERIFICATIONS.md`, pas ligne à ligne dans cette passe.

---

## Journal de validation — 2026-11-15

La collecte d'extractions réussies a été testée en mode attach.

Artefact observé :

- Répertoire : `bdl_collect_extraction`
- Extraction : un bloc radio.
- Nombre d'événements de règles : 1.
- Règle : `aux_text_choice_group`.
- Effet : `removed`.

Le test confirme que le pipeline de collecte reçoit les blocs extraits
et les événements de règles.

Le réglage `SURVEY_BDL_EXTRACT_SAMPLE=1` a permis de collecter la page
éligible. La traçabilité des pages ignorées par échantillonnage a également
été ajoutée dans `failure_recorder.py`.

**Conclusion :** les étapes 1 et 2 sont implémentées. L'oracle d'extraction
(étape 3) reste le prochain travail.