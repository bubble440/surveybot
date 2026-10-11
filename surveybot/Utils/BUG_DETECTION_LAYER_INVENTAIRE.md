# Bug Detection Layer — Inventaire des signaux et règles existants (BDL-0 + BDL-1)

**Statut :** livrable de cartographie, produit en lecture seule — aucune fonction du bot n'a été modifiée pour produire ce document, aucun fichier existant n'a été altéré.
**Méthode :** cinq explorations automatisées indépendantes (lecture seule), chacune scopée à un sous-ensemble du périmètre demandé, citations `fichier:ligne` vérifiées au 2026-10-11. En cas d'écart avec le code actuel, se fier au code plutôt qu'à ce document.
**Fichiers couverts :** `Survey/question_block_validator.py`, `Survey/question_block_analyzer.py`, `Survey/action_validator.py`, `Survey/failure_recorder.py`, `Survey/page_snapshot.py`, `Survey/browser_capsule.py`, `Survey/action_dispatcher.py`, `Survey/cta_handler.py`, `Survey/dom_analyzer.py`, `Survey/prompt_builder.py`, `Survey/autofix/prompt_generator.py`, `Survey/autofix/failure_case_builder.py`, `Survey/autofix/failure_diagnosis.py`. `Survey/autofix/replay_browser.py` est cité par renvoi (c'est là que `target_shapes`/`requested_option_dom_facts` sont réellement calculés) mais n'a pas été ouvert directement dans ce périmètre — voir §0.5.

Ce document ne propose aucune implémentation et ne modifie aucune règle existante. Il répond à trois questions : qu'est-ce qui existe déjà, que prouve-t-il réellement, et qu'est-ce qui manque pour juger PASS / SUSPECTED / BUG_CONFIRMED.

---

## 0. Corrections au cadrage initial — à ne pas supposer acquises

1. **Le vocabulaire fermé de raisons d'échec de clic (`not_visible`, `intercepted`, `not_enabled`, `unstable`, `detached`) n'existe pas dans `action_dispatcher.py`** (absence confirmée par grep sur tout le fichier). Ce fichier journalise des raisons en texte libre, par stratégie de clic (`apply ok=<bool> strategy=<nom> reason=<texte libre>`, dizaines de sites, ex. `:884, :925, :3454, :8754-8883`). Ce vocabulaire fermé existe côté **CTA**, dans `cta_handler.py` (`reason=widget_not_visible` approx. `:2059`, `_diag_mark("not_visible_or_disabled")` `:2265`) — deux familles différentes, à ne pas confondre dans la conception de l'oracle.
2. **La post-vérification QT du chemin historique du dispatcher (`target_id`, radioQT/checkboxQT) ne lit pas la propriété native `checked`.** Elle lit un marqueur de classe CSS visuel posé par le JS du site : `wrapper.querySelector('.option_radio.input_on, .option_checkbox.input_on')` ou `.option_label.input_label_on` (`action_dispatcher.py:8297-8322`), avec capture d'erreur JS (`:8320-8322`). La lecture de `checked` natif existe bien dans ce fichier (`_is_selected`, `:2960-2969`, qui utilise `inp.is_checked()` sans attendre l'actionabilité), mais sur un chemin différent — le repli `label[for]` — pas sur le chemin principal QT.
3. **Confirmation précise, avec citation exacte, de la « cascade de clic » déjà notée dans `SURVEYBOT_AUTOFIX_PLAN.md:159`** : `_cdp_click` (`action_dispatcher.py:3093-3114`) retourne `True` sans lever d'exception même quand `bounding_box()` est vide (`{}` → `x=y=0`), en cliquant alors en `(0, 0)`. Ce faux succès empoisonne ensuite le cache de méthode gagnante (`skip_to`, `_cm_cache`, `:3187-3296`), qui n'est invalidé que si les 4 méthodes échouent (`:3296`).
4. **`QuestionBlock` (dataclass, `question_block_analyzer.py:142-148`) n'a que 5 champs** : `itype`, `label`, `dom_el`, `container` (toujours `None` à l'appel, ex. `:252,:306,:342,:447,:470,:492,:522`), `options`. Pas de `target_id`, `context`, ni `scope_hint`. Or `filter_blocks_for_openai` (`prompt_builder.py:990-1039`) lit ces attributs via `getattr(qb, ..., qb.get(...))`, ce qui suppose des blocs sous forme de `dict` ailleurs dans le pipeline. **UNCLEAR** : coexistence de deux représentations de « bloc » (dataclass vs dict) non résolue dans ce périmètre — à vérifier avant d'y adosser un oracle d'extraction.
5. `target_shapes` et `requested_option_dom_facts` apparaissent bien dans `diagnosis.json` (relayés par `failure_diagnosis.py:356-398`), mais sont **calculés dans `Survey/autofix/replay_browser.py`**, fichier non ouvert dans ce périmètre. Leur mode de calcul exact (bornes, cas limites) n'est donc pas vérifié ici — **UNCLEAR**, à confirmer séparément si cela devient important pour un oracle d'action.
6. `Survey/prompt_builder.py` ne sert pas à l'agent AutoFix : c'est le prompt du **bot d'enquête lui-même** (sélection de réponse via le modèle de réponse), un étage EXTRACTION → décision du bot, distinct de `autofix/prompt_generator.py` (prompt de l'agent correcteur, lu uniquement depuis `diagnosis.json`/`context_selection.json`, jamais recalculé).

---

## 1. Tableau des signaux de détection déjà disponibles

### 1.1 EXTRACTION (`dom_analyzer.py`, `question_block_analyzer.py`, `question_block_validator.py`)

| Signal | Producteur (fichier:ligne) | Ce qu'il prouve réellement | Persisté hors ligne ? |
|---|---|---|---|
| `qualtrics_ranked_choices`, `zappi_max_diff`, `focaldata_response_option_cards`, `netsurvey_choice_buttons` (`missing_block`) | `question_block_validator.py:29-76, :79-137, :140-192, :195-257` | Un pattern DOM précis et fortement spécifique à un provider est visible alors que `question_blocks` est vide. Ne prouvent rien sur le reste de l'extraction ; ne reconstruisent aucune option (dit explicitement en docstring). | Oui — `issues[]` dans `validation_report.json` (`failure_recorder.py:48-51`) |
| `hidden_block_visible_choice` | `question_block_validator.py:319-435` | Un bloc radio/checkbox déjà extrait a ses options enregistrées invisibles dans le DOM **et** un conteneur visible distinct avec ≥2 options non-sous-ensemble existe. **Décline silencieusement** au-delà de budgets durs (>30 blocs, >100 options/bloc, >100 options totales, budgets JS sur conteneurs/inputs/labels) — faux négatif non signalé passé ces seuils. | Oui |
| `question_text_is_help` | `question_block_validator.py:260-316` (signal), `:482-504,:594-602` (issue) | Le texte « question » du bloc correspond à un nœud d'aide/instruction alors qu'un nœud « titre » distinct existe dans le même conteneur (≤8 niveaux). Ne compare pas entre frames (limite documentée `:261-271`, aussi notée dans `BOT_EVOLUTION_MEMORY.md`). | Oui |
| `invalid_block_shape`, `missing_target_id`, `registry_target_missing`, `choice_without_options`, `duplicate_options`, `invalid_selection_limits`, `max_select_exceeds_options` | `question_block_validator.py:514-592` | Cohérence structurelle interne (forme du bloc, ancrage registre, options vs itype, doublons après normalisation, cohérence min/max) — ne compare pas directement au DOM, sauf `registry_target_missing` qui détecte une incohérence entre deux structures en mémoire. | Oui |
| `group_reject_reasons` (`missing_question`, `radio_aggregated_container_option`, `checkbox_optout_companion_text`, `duplicate_signature`, `group_exception`) | `dom_analyzer.py:2499` (init), incréments `:2914,:2929-2930,:2934-2935,:2951,:3166` | Compte, par cause, combien de groupes de choix candidats ont été rejetés avant de devenir des blocs. | **Non** — `print("[DOM_CONTEXT_DEBUG] ...")` uniquement, `:3169-3174` |
| `option_xpath_map` (par bloc) | construit `:2959/:3068, :3697/:3716, :1149/:1162`, stocké sur le bloc `:3079,:3726,:1184` | Chaque option extraite a (ou non) pu être résolue vers un xpath DOM concret — recoupement direct extraction-résultat vs DOM. | Potentiellement oui, s'il survit à la dédup et si `question_blocks.json` est écrit (voir §2) |
| `_question_pollution_penalty` | `dom_analyzer.py:5306-5324` | Heuristique « texte-question ressemble à du bruit DOM concaténé » (tokens répétés, ratio d'unicité faible) — utilisée pour arbitrer entre blocs concurrents. | Non — local à `_dedupe_question_blocks` |
| `_block_quality_score` (combine pollution penalty, `xpath_score`, `focusvision_priority`, `target_score`) | `dom_analyzer.py:5326-5333` | Score d'arbitrage entre deux blocs dupliqués pour la même question — ne juge pas la qualité absolue d'un bloc seul. | Non |
| `extracted_blocks count=... itypes=... question_len=... options_count=...` | `dom_analyzer.py:5169-5172` | Résumé final toujours émis (pas seulement en debug) : nombre de blocs, itypes distincts, longueur de la première question, total d'options. Le signal le plus proche d'un « bilan santé extraction » déjà actif en continu. | Non confirmé — `log_info`, capture par un agrégateur de logs externe non vérifiée dans ce périmètre |
| `min_select`/`max_select` calculés (`_compute_max_select`, `_compute_min_select`, override Qualtrics `:5048-5062`) | `dom_analyzer.py:5042-5062` | Cardinalité **interprétée** (comptage d'options + mots-clés de la question), pas un comptage DOM direct. | Oui — stocké sur le bloc |
| `_should_skip_focusvision_answers_list_groups` | `dom_analyzer.py:4862-4922` | Détecte un conflit DOM réel entre l'attribut `atmost` d'un widget cardsort et la structure effective de la table sous-jacente (pattern de nom `ansD.D.D`) — sert à choisir quel extracteur croire. | Non — booléen consommé inline |
| Heuristiques de `question_block_analyzer.py` (`_is_hidden_like`, détection ARIA radios, grilles matrices) | `question_block_analyzer.py:63, :347, :259` | Logique d'extraction alternative. **Aucun appelant identifié depuis `dom_analyzer.py`/le validateur dans ce périmètre de lecture.** | **UNCLEAR — statut d'intégration non vérifié** |

### 1.2 ACTION (`action_dispatcher.py`, `action_validator.py`, `browser_capsule.py`, `page_snapshot.py`, `replay_browser.py` par renvoi)

| Signal | Producteur (fichier:ligne) | Ce qu'il prouve réellement | Persisté hors ligne ? |
|---|---|---|---|
| `dispatcher_success` / `success_any` | `action_dispatcher.py:8982` (init), `:9203-9209` (`ok = execute_action(...); if ok: success_any = True`), `:9336` (return) | **OR logique sur tout le plan d'actions** : si 5 actions sur 6 échouent et 1 réussit, le plan entier reste `True`. C'est exactement la valeur transmise à `action_validator.validate_actions(dispatcher_success=...)` (`page_snapshot.py:713`, `replay_browser.py:1329/1433`) — confirme avec précision la mise en garde déjà notée dans la mémoire du projet, en identifiant le mécanisme exact (agrégation N:1, pas seulement un problème QT). | Oui — `action_trace.json`, `validation_report.json` |
| Logs `[TARGET] apply ok=<bool> strategy=<nom> reason=<texte libre>` | des dizaines de sites dans `action_dispatcher.py` | Vérification interne à chaque stratégie de clic/saisie, vocabulaire **non fermé**, propre à chaque type de widget. | **Non** — texte de log uniquement, jamais structuré ni parsé |
| Post-vérification QT (`input_on`/`input_label_on`) | `action_dispatcher.py:8297-8322` | Marqueur CSS visuel posé par le JS du site — plus robuste qu'un `checked` natif pour QT, mais **auto-rapporté par le même code qui vient d'agir**, donc pas une preuve indépendante au sens de l'oracle visé. | Non en tant que champ dédié — n'alimente que le booléen de retour et le log |
| Cache de méthode de clic gagnante (`skip_to` / `_cm_cache`) | `action_dispatcher.py:3187-3296` | Mémorise quelle méthode (native click / hover+click / CDP coordonnées / JS click) a « marché » pour une cible — prouve seulement l'absence d'exception, pas un clic réellement reçu (cf. §0.3, bug `_cdp_click` à (0,0)). | Non |
| `_is_selected` (repli `label[for]`) | `action_dispatcher.py:2960-2969` | Lecture native réelle de `is_checked()`, sans attendre l'actionabilité — valide même pour un input natif masqué en CSS. Chemin séparé de la post-vérification QT ci-dessus. | Non en tant que tel |
| `_wait_checked` (repli carrousel MX) | `action_dispatcher.py:1594-1623` | Lecture native réelle de `checked`, poll jusqu'à 1,2 s. | Non |
| `action_value_not_in_registry_options`, `action_missing_target_id`, `action_target_missing`, `invalid_action_shape` | `action_validator.py:515-565` | Cohérence de la **requête** d'action (valeur demandée vs registre, cible présente) — ne dit rien sur le résultat de l'action. | Oui |
| `dispatcher_false_negative` + 4 sous-signaux : `ifop_zip2city_resolved`, `checkbox_radio_checked` (natif, hors QT), `checkbox_radio_marker_selected` (classe « selected », sans input natif), `checkbox_radio_checked_captured` (hors-ligne via `runtime_state.json` capturé) | `action_validator.py:91-424` | Chacun est une preuve DOM indépendante pour une famille de widget précise. Ne requalifie en `dispatcher_false_negative` que si **toutes** les actions du plan ont une preuve ; sinon retombe sur `dispatcher_reported_failure` (`:567-595`). `checked` natif explicitement **ignoré pour QT** (`skip_qt_native_checked`, `:348-424`) — distrust documenté dans le code. | Oui — `issues[]` + repris dans `action_trace.json` (`dom_signal`/`observed`) |
| `action_fix_success_unconfirmed` | `action_validator.py:427-494` | Pour un correctif externe **QT uniquement** rapportant un succès : absence du marqueur visuel `input_on`/`input_label_on` → succès non confirmé. | Oui |
| `dispatcher_reported_failure` | `action_validator.py:567-595` | Bucket résiduel : aucune preuve DOM supplémentaire n'a confirmé ni infirmé l'échec rapporté par le dispatcher. | Oui |
| `pre_action_dom.html` | capturé `page_snapshot.py:262-286` (`_capture_current_outer_html`, léger, sans wait/clic), appelé avant le dispatch (`:688-692`), écrit `:427-435` | outerHTML avant action, sans wait/clic/navigation. | Oui, **mais seulement si le validateur a déjà conclu à un échec** (`failure_recorder.py:34-35`) |
| `post_action_dom.html` / capture écran `post_action_viewport.png` | `page_snapshot.py:416-425, :469-473` | État DOM + capture écran après action. | Oui, même condition |
| `runtime_state.json` (avant/après) | `browser_capsule.py:191-217` (`capture_runtime_state`), appelé avant (`page_snapshot.py:700-706`) et après, seulement sur échec (`:725-740`) | Pour la cible + options + 2 niveaux d'ancêtres (borné 40 éléments / 20 options par cible) : `tag, nativeInput, value, checked, selected, selectedIndex, disabled, readOnly, indeterminate, isContentEditable, className, ariaChecked, ariaSelected, ariaExpanded, ariaDisabled, href, text(≤300c), visible, rect`. `runtime_state_before` n'est jamais écrit seul — seulement noyé dans `action_trace.json["before"]`. | Oui pour `runtime_state_after` (fichier dédié) ; `before` seulement via `action_trace.json` |
| `action_trace.json` (`requested_actions, dispatcher_success, validator_signals, before, after, dom_mutations`) | assemblage `browser_capsule.py:299-342`, sans recalcul | Trace structurée mais **sans détail par stratégie de clic** (pas de skip_to, pas de reason-code fermé) — seulement l'agrégat `dispatcher_success` déjà vu plus haut. | Oui |
| Observateur de mutations DOM | `browser_capsule.py:266-296`, installé avant (`page_snapshot.py:704`), lu seulement sur échec (`:732`) | MutationObserver passif sur `document.body`, plafond 50 entrées. | Oui — dans `action_trace.json["dom_mutations"]` |
| `requested_option_dom_facts` | calculé dans `replay_browser.py` (non ouvert ici), relayé `failure_diagnosis.py:356-398` | Tag, type de champ, ≤3 classes assainies, visibilité, taille de la cible résolue + jusqu'à 6 frères, mesurés **avant** exécution du dispatcher sur le document figé (replay). | Oui — `diagnosis.json["real_dispatch_replay"]` |
| `target_shapes`, `dispatcher_steps`, `execute_scripts` | idem, relayé `failure_diagnosis.py:356-398` | Forme assainie des cibles du plan ; étapes du dispatcher au replay ; indicateur « scripts désactivés » (limite documentée : ne peut pas observer un état posé par le JS du site). | Oui, même chemin |

### 1.3 CTA (`cta_handler.py`)

| Signal | Producteur (fichier:ligne) | Ce qu'il prouve réellement | Persisté hors ligne ? |
|---|---|---|---|
| `_dom_progress_marker` (`url, txt, qNodes, notifSig, progGridSig`) | `cta_handler.py:616-679` | Empreinte DOM/URL légère avant/après clic. Ne prouve pas de lien causal avec le clic CTA (un widget asynchrone sans rapport peut la faire changer). | **Non** — jamais écrit en JSON |
| `_did_progress` | `cta_handler.py:682-714` | Verdict booléen combinant les signaux ci-dessus (URL changée OU `progGridSig` différent OU `txt`/`notifSig`/`qNodes` différents). **Cas dégénéré documenté** : retourne `True` sans aucun signal réel quand marqueur avant et après sont tous deux vides (`:688-695`, commentaire « éviter un 2e clic inutile ») — faux positif construit intentionnellement, dangereux si réutilisé hors de son contexte d'origine. | Non |
| `_wait_post_click_stabilization` (raisons : `timeout, url_changed, target_disconnected, target_hidden, target_replaced, target_stale, dom_marker_changed`) | `cta_handler.py:717-788` | Seuls `url_changed` et `dom_marker_changed` comptent comme progression ; une mutation propre à l'élément cliqué (masqué/détaché/remplacé) ne vaut jamais, à elle seule, preuve de progression (documenté explicitement `:724-727`) — elle ne fait qu'étendre l'attente. `reason` est loguée puis jetée, jamais renvoyée à l'appelant. | Non |
| `progGridSig` | `cta_handler.py:649-667` | Seul détecteur de « transition interne » existant, strictement scopé au widget Ipsos/mrIWeb `GridProgressive`. | Non |
| `CTA_INTERCEPT_ONLY` — `_cta_intercept_enabled`, `arm_interceptor`, `read_intercept_report`, `_probe_interceptor_state` | `cta_handler.py:41, :104, :329, :520, :527-531, :918-1023` | Prouve qu'un clic a atteint la cible et a été intercepté (`clickCaptured && prevented`), indépendamment de toute navigation/changement d'URL — c'est le mécanisme qui confirme explicitement dans le code que l'absence de changement d'URL est *attendue* en mode interception, pas un échec. `_probe_interceptor_state` re-vérifie l'état d'armement réel car le retour de `arm_interceptor` peut être trompeur sur des pages restrictives (`:528-531`). | Non |
| Logs `_diag_mark` (raisons d'exclusion de candidats CTA) | `cta_handler.py:2257-2259, :2507-2518` | Comptages de raisons d'exclusion de candidats (overlay, carousel, cookie banner, etc.) — utile au diagnostic humain, jamais exploité programmatiquement. | Non, souvent derrière le mode debug |
| `driver.last_action_success` | mis à `True` dans `click_cta_strong_any_context` (`:2778-2781`) | **UNCLEAR** — aucun autre lecteur de cet attribut identifié dans ce périmètre. | Non vérifié |

---

## 2. Artefacts disponibles en entrée d'un oracle, par famille

### ACTION
**Disponibles** : `pre_action_dom.html`, `post_action_dom.html`, `post_action_viewport.png`, `runtime_state.json` (après ; avant seulement noyé dans `action_trace.json`), `action_trace.json` (`requested_actions, dispatcher_success, validator_signals, before, after, dom_mutations`), `actions_requested.json`, puis au diagnostic : `requested_option_dom_facts`, `target_shapes`, `dispatcher_steps`, `execute_scripts`.
**Manquants** :
- Ces artefacts ne sont écrits **que si le validateur a déjà conclu à un échec** (`failure_recorder.py:34-35`) — sur un passage jugé « ok », rien n'est conservé ; un faux négatif du validateur lui-même reste invisible à tout oracle placé après lui.
- Aucune trace structurée par stratégie de clic (quelle des 4 méthodes a « gagné », état du cache `skip_to`) — seule la valeur agrégée `dispatcher_success` (OR sur tout le plan) survit dans les artefacts.
- `action_trace.json["requested_actions"]` n'a pas de statut par action individuelle — impossible de savoir, à partir des artefacts seuls, laquelle des N actions du plan a échoué quand `dispatcher_success=True` global.
- Le faux succès `_cdp_click` sur `bounding_box()` vide n'est marqué nulle part (pas de champ « cible sans boîte englobante » dans `runtime_state.json`).
- Aucune cible CTA n'entre dans `capture_runtime_state`/le registre (gap documenté explicitement dans `browser_capsule.py:28-34`).

### CTA
**Disponibles** : quasiment rien de persisté. Tous les signaux (`_dom_progress_marker`, `_wait_post_click_stabilization`, `progGridSig`, rapport d'interception) existent en mémoire au moment du clic, mais `cta_handler.py` ne contient aucun appel d'écriture fichier identifié dans ce périmètre.
**Manquants** : capture avant/après DOM spécifique CTA (équivalent `pre_action_dom.html`/`runtime_state.json`), persistance de la `reason` de stabilisation, persistance du rapport d'interception, persistance du score du candidat réellement cliqué, et toute distinction stockée entre navigation classique / transition interne / `CTA_INTERCEPT_ONLY` / échec réel — cette distinction n'existe nulle part aujourd'hui, ni en mémoire structurée ni persistée.

### EXTRACTION
**Disponibles** : `dom_outer.html`/`dom_body.html`, `page_source.html`, `body_text.txt`, `question_blocks.json` (si transmis), dump des frames, `external_scripts/stylesheets/requests.json`, `validation_report.json` (tous les signaux du §1.1).
**Manquants** :
- Les décisions de dédup/fusion/élagage de `dom_analyzer.py` (`group_reject_reasons`, score de qualité, pénalité de pollution, choix du bloc gagnant) sont **log-only** — aucune trace de « pourquoi ce bloc a été préféré/éliminé » ne survit dans `question_blocks.json`. Impossible d'auditer après coup si une règle historique a bien choisi le bon bloc.
- Aucun signal ne distingue « élément absent de l'extraction » de « élément volontairement transformé/absorbé par une règle historique » (§6) — c'est précisément la distinction nécessaire à un futur retrait de règle, et elle n'existe pas à l'exécution.
- `hidden_block_visible_choice` décline silencieusement au-delà de ses budgets, sans trace du déclin lui-même.
- Statut d'intégration de `question_block_analyzer.py` non confirmé (aucun appelant identifié dans ce périmètre) — à vérifier avant de s'appuyer dessus.

---

## 3. Signaux existants trompeurs ou non fiables comme preuve

| Signal | Pourquoi il ne fait pas preuve |
|---|---|
| `dispatcher_success` / `success_any` | OR logique sur tout le plan d'actions ; un plan majoritairement en échec peut rester `True` (§1.2, mécanisme précisé). |
| Logs `apply ok=true strategy=... reason=...` | Vocabulaire libre, jamais re-vérifié indépendamment ; `ok=true` peut signifier seulement « l'appel n'a pas levé d'exception », cf. `_cdp_click`. |
| `_cdp_click` sur élément sans boîte englobante | Retourne `True` en cliquant à `(0, 0)` — faux succès qui empoisonne ensuite le cache `skip_to` pour le reste du plan. |
| `checked` natif d'un input QT masqué | Explicitement non fiable pour un widget QT (confirmé dans `action_validator.py` par `skip_qt_native_checked`) — à l'origine du cas réel MetrixLab `radioQT` où un correctif « réussi » n'avait rien sélectionné. |
| Post-vérification QT du dispatcher (`input_on`) | Marqueur CSS, pas `checked` natif (correction §0.2) — et de toute façon auto-rapportée par le code qui vient d'agir, pas une preuve indépendante. |
| `_did_progress` sur contexte dégénéré | Retourne `True` sans aucun signal réel quand marqueurs avant/après sont tous deux vides — faux positif construit par conception, dangereux hors de son contexte d'origine. |
| Changement d'URL comme preuve de CTA | Rejeté explicitement par la conception (transitions internes, sliders, `CTA_INTERCEPT_ONLY`) et par le code lui-même (`_did_progress` n'en fait qu'un signal parmi d'autres, jamais seul suffisant côté absence). |
| Retour booléen des fonctions publiques de `cta_handler.py` | Un seul `False` peut signifier « non trouvé », « trouvé mais non cliquable », ou « cliqué mais transition non détectée » — indistinguable sans relire les logs. |
| `TRACE_REPLAY` / replay sans scripts (`execute_scripts=False`) | Ne peut pas observer un état posé par le JS du site ; un verdict `BUG_PERSISTANT` obtenu ainsi n'est pas une preuve pour un correctif dont la confirmation dépend du JS du site (déjà noté dans `SURVEYBOT_AUTOFIX_PLAN.md`, confirmé ici au niveau du mécanisme). |
| `hidden_block_visible_choice` au-delà de ses budgets | Décline silencieusement plutôt que de signaler un doute — risque de faux négatif invisible dans les artefacts. |
| Signaux `missing_block` gated par provider (`zappi_max_diff`, `focaldata_response_option_cards`, `netsurvey_choice_buttons`) | Fiables seulement sur le site qui les a motivés ; ne généralisent pas et ne doivent pas être lus comme représentatifs de la fiabilité globale de l'extracteur. |

---

## 4. Proposition de taxonomie minimale (fondée uniquement sur l'inventaire ci-dessus)

### EXTRACTION
- `BLOC_MANQUANT` — pattern DOM fort détecté sans bloc correspondant (`missing_block`, `hidden_block_visible_choice`).
- `BLOC_MAL_FORME` — incohérence structurelle (`invalid_block_shape`, `missing_target_id`, `registry_target_missing`, `choice_without_options`, `invalid_selection_limits`, `max_select_exceeds_options`).
- `OPTIONS_DOUBLONNEES` — `duplicate_options`.
- `QUESTION_MAL_IDENTIFIEE` — `question_text_is_help`.
- *(volontairement absente)* : aucune catégorie « règle historique mal appliquée » n'est proposée — l'inventaire ne montre aucun signal exécutable pouvant l'alimenter aujourd'hui (cf. §2, manquants EXTRACTION).

### ACTION
- `DISPATCHER_FAUX_NEGATIF_CONFIRME` — `dispatcher_false_negative`, toutes actions du plan couvertes par une preuve DOM indépendante.
- `SUCCES_NON_CONFIRME` — `action_fix_success_unconfirmed` (QT uniquement aujourd'hui).
- `REQUETE_ACTION_INVALIDE` — `action_value_not_in_registry_options`, `action_missing_target_id`, `invalid_action_shape`.
- `ECHEC_SANS_SIGNAL_SUPPLEMENTAIRE` — `dispatcher_reported_failure` (bucket résiduel, pas une preuve en soi).

### CTA
*(aucune de ces catégories n'existe comme verdict persisté aujourd'hui — elles reprennent les primitives déjà présentes en mémoire, à construire)*
- `NAVIGATION_CLASSIQUE_CONFIRMEE` — `url_changed`.
- `TRANSITION_INTERNE_PARTIELLEMENT_OBSERVEE` — `dom_marker_changed` générique, ou `progGridSig` (un seul gabarit couvert).
- `INTERCEPT_ONLY_CONFORME` / `INTERCEPT_ONLY_IMPOSSIBLE` — mode `CTA_INTERCEPT_ONLY`.
- `ECHEC_SANS_TRANSITION_DETECTEE` — ni URL, ni marqueur DOM, ni interception concluante.

---

## 5. Lacunes d'observabilité à combler en priorité (action → CTA → extraction)

### ACTION (priorité 1)
1. Les artefacts avant/après ne sont écrits **que si le validateur a déjà conclu à un échec** — un oracle placé après ce validateur ne verrait jamais les cas que le validateur a validés à tort. Capturer ces artefacts de façon inconditionnelle (ou les garder en mémoire jusqu'à décision de l'oracle) est le préalable le plus structurant.
2. Aucune trace par stratégie de clic ni par action individuelle d'un plan multi-actions — seul l'agrégat `dispatcher_success` (OR global) survit.
3. Le faux succès `_cdp_click` sur boîte englobante vide n'est marqué nulle part — un champ simple (« cible sans rect résolu ») manque dans `runtime_state.json`.
4. Aucune cible CTA n'entre dans `capture_runtime_state`/le registre — gap déjà documenté dans le code lui-même.
5. Aucun contrôle mécanique de qualité du geste (délai borné, précondition testée avant d'agir) — backlog déjà identifié dans `SURVEYBOT_AUTOFIX_PLAN.md:161`, jamais implémenté.

### CTA (priorité 2)
1. Rien n'est persisté : ni la `reason` de stabilisation, ni le rapport d'interception, ni `progGridSig`, ni le score du candidat cliqué. Tout oracle CTA devra d'abord obtenir une capture avant/après comparable à celle qui existe déjà pour ACTION.
2. `_did_progress` contient un faux positif construit par conception sur contexte dégénéré — à neutraliser avant de bâtir un oracle dessus, sinon le faux positif se propage silencieusement.
3. Le seul détecteur de « transition interne » est scopé à un unique gabarit (Ipsos `GridProgressive`) — aucune généralisation DOM-first n'existe encore pour les sliders/panneaux d'autres providers.
4. Aucun lien vérifié entre le verdict de `cta_handler.py` et `failure_recorder.py`/`diagnosis.json` — le champ `reason` attendu par `failure_case_builder.py` doit être renseigné en amont (dispatcher/exécuteur), chemin non vérifié dans ce périmètre.

### EXTRACTION (priorité 3, déjà la famille la mieux couverte)
1. Aucun signal ne distingue « élément absent » de « élément volontairement transformé/absorbé par une règle historique » — exactement la distinction nécessaire à un futur retrait de règle, absente à l'exécution.
2. `hidden_block_visible_choice` décline silencieusement au-delà de ses budgets — un oracle devrait au moins journaliser ce déclin plutôt que le laisser invisible.
3. Les décisions de dédup/fusion/élagage (`dom_analyzer.py`, §1.1) ne laissent aucune trace dans les artefacts — seul le résultat final est visible, jamais l'arbitrage.
4. Statut d'intégration de `question_block_analyzer.py` à vérifier avant de le considérer comme une base réutilisable.

---

## 6. Inventaire des règles qui altèrent volontairement l'extraction ou l'action

Recensement factuel, sans jugement ni classement de pertinence — objectif : rendre un futur retrait évaluable, pas décider lequel retirer.

### 6.1 Règles d'extraction (`dom_analyzer.py`)

| Règle (fichier:ligne, fonction) | Ce qu'elle transforme | Finalité : documentée (citation) ou déduite | Déclencheur DOM | Observable dans les artefacts d'un failure case ? |
|---|---|---|---|---|
| Règle « Autre, précisez » inline (détection auxiliaire) | `dom_analyzer.py:~366-397` | Un champ texte à l'intérieur du même wrapper d'option qu'un radio/checkbox est classé comme auxiliaire de ce choix, pas comme question autonome — supprime un bloc text/single qui aurait sinon été créé. | **Documentée** : « Cas inline 'Autre, précisez'... on le classe directement en auxiliaire pour éviter un bloc single parasite » (commentaire `:366-369`). | Ancêtre `label`/`li`/`.answer_options`/`.option`/`.form-check` contenant un radio/checkbox. | Non — absorbé silencieusement avant création de bloc, aucune trace sur les blocs survivants. |
| Règle « Autre, précisez » FocusVision (élagage) | `_prune_focusvision_auxiliary_openended_singles`, `dom_analyzer.py:5524-5579` | Supprime un bloc text/textarea autonome représentant le champ « Autre, précisez » d'un groupe answers-list déjà capturé — y compris le cas Decipher MX Collapsible sans `name`. | **Documentée**, docstring explicite `:5525-5533`. | Champ nommé dans `context.aux_openended_names`, ou textarea sans nom/id sur une page contenant déjà un bloc `focusvision_answers_list`. | Non — bloc supprimé, aucune trace conservée. |
| Élagage générique « Autre » inline (companion trailing) | `_prune_trailing_open_inline_singles`, `dom_analyzer.py:5582-5616` | Supprime un bloc text autonome dont le name/id correspond à un champ inline déjà absorbé par le chemin radio/checkbox (`context.inline_openend_names`). | **Documentée**, docstring `:5583-5588`. | `name`/`id` du champ texte présent dans `context.inline_openend_names`. | Loggé (`:5613`) puis supprimé — log seulement, pas dans les artefacts persistés. |
| Opt-out checkbox companion (« ne souhaite pas répondre ») | `_is_checkbox_optout_companion_for_text`, `dom_analyzer.py:~463-520` | Exclut, comme question autonome, un checkbox unique lié à un champ texte (via `openendid`/attributs exclusifs), y compris une exception Askia (classe `askia-exclusive` + `textarea` sœur dans la même table). | **Documentée**, docstring `:463-474` + commentaire branche Askia `:480-484`. | Exactement 1 checkbox + 1 option ; libellé « ne souhaite pas répondre »/« prefer not to answer » OU classe Askia avec textarea sœur. | Comptée dans `group_reject_reasons["checkbox_optout_companion_text"]` (incrément, pas détail), bloc lui-même supprimé silencieusement. |
| Déduplication/fusion de blocs concurrents | `_dedupe_question_blocks`, `dom_analyzer.py:5266-5448` | Deux blocs au même `(itype, group_key)` → fusionnés : union des options (casse insensible), `question` remplacée par le texte le moins « pollué », `max_select` recalculé. | **Documentée**, docstring `:5267-5277`. | Deux extracteurs produisent des blocs avec le même `group_key` (ou même signature itype+question+options). | Oui, visible dans le bloc final fusionné — mais l'arbitrage lui-même (pourquoi tel bloc a gagné) n'est pas conservé. |
| Priorité FocusVision sur collision générique | `dom_analyzer.py:5388-5399` | Quand un bloc FocusVision dédié collisionne avec un bloc générique du même `group_key`, le bloc dédié est conservé tel quel (pas d'union d'options) s'il a ≥2 options. | **Documentée**, commentaire `:5388-5392` : évite d'unionner des options polluées. | `focusvision_answers_list=True` sur un seul des deux blocs en collision. | Oui pour le résultat final, non pour l'arbitrage. |
| Filtre pré-dédup matrice vs bouton-groupe | `dom_analyzer.py:5335-5365` | Supprime un bloc `button_group` entier si ses options sont un sous-ensemble des labels de lignes déjà couverts par des blocs `table_matrix_radio` de la même question. | **Documentée**, commentaire `:5335-5338` (éviter un 17ᵉ bloc Likert parasite). | `group_key` contenant `:button_group:` avec options ⊆ labels de lignes d'une matrice existante. | Non — bloc supprimé avant construction de la carte de dédup. |
| Fusion nField rowpicker/radio exclusif | `_merge_nfield_rowpicker_exclusive_radio`, `dom_analyzer.py:5181-5263` | Absorbe un bloc radio « exclusif » autonome dans un bloc checkbox : options ajoutées, conservées séparément dans `context["exclusive_options"]`, bloc radio d'origine supprimé. | **Documentée**, docstring `:5182-5191`. | Checkbox avec `context.nfield_checkbox_name_prefix` finissant par `-` ; radio avec `context.nfield_exclusive_radio=True`, même question normalisée. | Oui — visible via `context["exclusive_options"]` sur le bloc survivant. |
| Élagage fragments FocusVision | `_prune_focusvision_fragmented_groups`, `dom_analyzer.py:5451-5521` | Supprime des blocs « fragments » (≤1 option, sans `:name:`) quand un bloc FocusVision plus riche couvre un sur-ensemble de leurs options. | **Documentée**, docstring `:5452-5456`. | Bloc riche `focusvision_answers_list=True` ≥2 options ; fragment ⊆ ses options, même `group_key`/texte de question. | Non — supprimé sans trace sur les survivants. |
| Élagage cardrating FocusVision (QA-view image-only) | `_prune_cardrating_focusvision_blocks`, `dom_analyzer.py:5619-5673` | Supprime des blocs `focusvision_answers_list` dont le préfixe de `group_key` correspond à un bloc `decipher_cardrating` dont les lignes sont image-only. | **Documentée**, docstring longue `:5620-5639`, dont une dérivation `ans{widget_uid}` explicitement qualifiée de « fallback additif » non issu de l'extracteur d'origine. | `decipher_cardrating=True` avec préfixe/`widget_uid` non vide ; match sur un bloc `focusvision_answers_list`. | Loggé (`:5667`) puis supprimé. |
| Cas inverse : cardrating non étiqueté favorisé par les groupes de lignes | `_prune_cardrating_unlabeled_blocks_favor_row_groups`, `dom_analyzer.py:5676-5772` | Supprime les blocs `decipher_cardrating` d'un widget quand aucune carte n'a de label, au profit de blocs `focusvision_answers_list` dont la question suit le motif `"{question_base} [{x}]"` (normalisé NFC). | **Documentée**, docstring `:5677-5709`. | Toutes les cartes d'un `cardrating_widget_uid` ont un label vide ; ≥2 blocs correspondants par motif de question. | Loggé (`:5767`) puis supprimé. |
| Heuristique champ générique/filtre de recherche (classement auxiliaire) | fonction englobante `~:300-520`, scoring `:404-460` | Un champ sans label propre, texte de question générique (recouvrement ≥0,7 avec les mots des options, ou formulations « autre »/« choisir »/« sélectionner »), placeholder générique et/ou consigne « choisir exactement/sélectionner tout » à proximité cumule un score ; seuil ≥3 → classé auxiliaire (supprimé). | **Partiellement déduite** : chaque heuristique individuelle a un commentaire local, mais le seuil global `score≥3` n'est pas documenté comme tel — à vérifier. | Combinaison de 4 heuristiques binaires sur le DOM/texte environnant. | Non — booléen consommé par l'appelant, bloc supprimé sans trace. |
| Fusion d'inputs nommés distincts (ex. radios jQuery Mobile) | `merge_single_name_inputs`, `dom_analyzer.py:2454-2493` | Fusionne plusieurs groupes à un seul élément partageant le même conteneur de question en un seul groupe. | **Documentée**, commentaire `:2454-2457`. | ≥2 groupes à un seul élément, même conteneur, même itype. | Oui — loggé (`:2486-2490`) et visible dans le groupe fusionné final. |
| Résolution bouton-groupe en checkbox multi-sélection | `dom_analyzer.py:3637-3682` | Résout un groupe de choix basé sur `button` en `checkbox`/`max_select=N` au lieu du `radio`/`max_select=1` par défaut, sur deux gardes DOM précises (layout interview ChoiceMultiple, ou MUI `.dialog-question` avec input natif checkbox). | **Documentée**, commentaires `:3637-3642, :3659-3668`, incluant la justification du bug évité sans ce garde. | Ancêtre DOM correspondant à l'un des deux sélecteurs de garde. | Oui — directement dans `itype`/`max_select` du bloc retourné. |
| Override `min_select` Qualtrics matrice-checkbox | `dom_analyzer.py:5048-5062` | Force `min_select=1` à la place du résultat générique (qualifié d'« artefact » dans le commentaire) pour un bloc matrice-checkbox Qualtrics. | **Documentée**, commentaire `:5048-5052`. | `context.qualtrics_choice_structure_checkbox=True`, ligne de matrice, pas de plafond dur. | Oui — champ `min_select` du bloc. |
| Tri par ordre DOM + promotion du bloc visible | `dom_analyzer.py:5064-5167` | Réordonne la liste finale de blocs selon la position DOM réelle de leurs inputs, puis place en tête le bloc du fieldset actuellement visible. | **Documentée**, commentaire `:5064-5070`. | `len(blocks) > 1`. | Oui — ordre de la liste retournée, loggé. |

### 6.2 Règles d'action (`action_dispatcher.py`)

| Règle (fichier:ligne) | Ce qu'elle transforme | Finalité : documentée ou déduite | Déclencheur | Observable dans les artefacts ? |
|---|---|---|---|---|
| Saut direct à la méthode CDP pour cible avec `label[for]` | `action_dispatcher.py:3196-3207` | Force la 3ᵉ méthode de clic (coordonnées CDP) d'emblée, en sautant clic natif et hover+clic, pour un `input` ayant un `label[for]` sœur. | **Documentée**, commentaire `:3193-3195` (évite deux timeouts de 30 s dus à l'interception de pointeur). | `node` est un `input` avec un `label[for=id]` sœur. | Non — visible seulement en log debug (`:3203-3204`). |
| Fermeture automatique des overlays avant le premier clic | `action_dispatcher.py:3223-3232` | Appelle `cta_handler._dismiss_blocking_overlays` avant la première tentative de clic d'une instruction. | **Documentée**, commentaire `:3209-3219` (bannière de consentement toujours au-dessus du point de clic). | Première tentative (`_first==1`) sur une instruction. | Non — résultat (`_overlay_dismiss_outcome`) seulement en log debug. |
| Substitution `label[for]` → input référencé | `action_dispatcher.py:5135-5159` | Quand la cible résolue est un `label` (ou son descendant), re-résout vers l'input référencé par `for="..."` dans le même contexte de frame, et agit/vérifie sur cet input substitué plutôt que sur le nœud littéralement demandé. | **Documentée**, commentaire `:5130-5134`. | Nœud cible est un `label` ou à l'intérieur d'un `label`. | Non — substitution visible seulement en log debug (`:5153-5157`). |
| Collapse de clé de cache pour grille cmix | `action_dispatcher.py:3188` | Toutes les lignes d'une grille `cmix_simple_grid` partagent une seule clé de cache de méthode de clic — une méthode « gagnante » sur la ligne 1 est appliquée (sans re-validation) à toutes les autres lignes. | **Documentée**, commentaire `:3184-3186` (« toutes les lignes partagent la même structure DOM »). | `payload.get("cmix_simple_grid")` vrai. | Non. |
| Ouverture préalable de panneaux repliés (pré-clics) | `_ensure_pre_clicks_ready`, `action_dispatcher.py:3116-3181, :3302-3322` | Clique jusqu'à 3 éléments `pre_click_xpaths` pour déplier un panneau avant de résoudre/cliquer la cible réellement demandée — insère des clics non demandés en amont de l'action. | **Documentée**, commentaire `:3117-3120` (`mat-expansion-panel` replié). | Présence de `pre_click_xpaths` sur l'instruction. | Non — n'apparaît pas dans le signal de succès de l'action elle-même. |
| Réécriture du label via le contexte de page avant recherche | `_sanitize_instruction_with_page_context`, appelée `action_dispatcher.py:8337-8343`, définie `:5934` | Peut remplacer silencieusement la valeur/libellé utilisé pour la recherche d'élément (repli historique) avant toute recherche DOM — transformation de la requête elle-même, pas seulement de la façon de cliquer. | **Déduite** — aucun commentaire au-delà du nom de la fonction ; corps non relu dans ce périmètre. **UNCLEAR**, à vérifier séparément. | Chemin de repli historique (`label`/valeur). | Non vérifié. |

### 6.3 Annexe informative — règles CTA (`cta_handler.py`)

Hors périmètre strict du point 6 de la mission (qui porte sur extraction/action), mais `cta_handler.py` faisait partie du périmètre de lecture et contient une couche de règles d'altération analogue, utile pour la suite :
- Détecteurs structurels par provider (IntelliSurvey, mrIWeb classique/Vue, CIS) — bonus de score documentés par docstring exacte de pattern DOM (`cta_handler.py:136, :161, :181, :203`).
- Exclusions silencieuses dans la boucle de candidats (carousel de tâche, chevron Qualtrics, CTA masqué en opacité, bannières CookieYes/`ps-footer`, conteneurs radio/checkbox, classes « disabled », wrapper non sémantique, mots-clés négatifs avec exception `has-`) — `cta_handler.py:2261-2505`, chacune documentée localement, aucune surfacée dans la valeur de retour.
- Désambiguïsation Encuesta (bouton « done » vs bouton « next ») — `:2223-2236`, documentée inline.
- Élargissement du texte de correspondance par mots-clés de repli (`good_fallback`) pour `click_cta_strong_any_context` — `:2724-2733`, non documentée au-delà du code lui-même.

---

## Points de doute à ne pas combler par hypothèse

- **UNCLEAR** — statut d'intégration réel de `question_block_analyzer.py` (aucun appelant identifié depuis `dom_analyzer.py` dans ce périmètre).
- **UNCLEAR** — coexistence `QuestionBlock` (dataclass, 5 champs) vs blocs `dict` (avec `target_id`/`context`/`scope_hint`) utilisés ailleurs dans le pipeline (§0.4).
- **UNCLEAR** — mode de calcul exact de `target_shapes`/`requested_option_dom_facts` dans `Survey/autofix/replay_browser.py`, non ouvert dans ce périmètre (§0.5).
- **UNCLEAR** — fonction exacte (nom/bornes) de la zone `dom_analyzer.py:~300-520` portant les heuristiques auxiliaires génériques et le seuil `score≥3` (§6.1, dernière ligne du tableau d'extraction).
- **UNCLEAR** — corps de `_sanitize_instruction_with_page_context` (`action_dispatcher.py:5934`), non relu — peut constituer une règle de réécriture supplémentaire côté action, non confirmée ici (§6.2, dernière ligne).
- **UNCLEAR** — chemin exact qui renseigne `meta["reason"]` consommé par `failure_case_builder.py` pour un cas CTA ; non produit par `cta_handler.py` lui-même d'après cette lecture.
- Toutes les citations `fichier:ligne` ci-dessus proviennent de lectures automatisées indépendantes au moment de leur exécution (2026-10-11) ; en cas d'écart avec le code actuel, se fier au code.
