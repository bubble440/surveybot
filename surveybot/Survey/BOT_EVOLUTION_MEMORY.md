# BOT_EVOLUTION_MEMORY.md
# Mémoire technique des extracteurs et fonctions critiques.
#
# LECTURE LLM — RÈGLES ABSOLUES :
# 1. Lire ce fichier AVANT tout diagnostic ou modification de code.
# 2. Extracteurs/fonctions présents dans le code mais absents de ce fichier :
#    ils préexistent à ce fichier. Absence ≠ permission de modifier.
# 3. Un extracteur qui échoue sur un DOM ≠ extracteur cassé.
#    Vérifier d'abord : le DOM courant correspond-il aux "Patterns couverts" ?
#    Si non → créer un extracteur additionnel. Ne pas réécrire l'existant.
# 4. "Patterns exclus" = frontières strictes, non négociables.
# 5. Mise à jour : uniquement en fin de session, sur demande explicite, après
#    validation fonctionnelle du patch.
#
# FORMAT PAR ENTRÉE :
# ### Nom de la fonction
# Fichier, guard, patterns couverts, patterns exclus, note DOM si non-intuitive.
# Max ~15 lignes par entrée.

---

## PLATEFORME : ASKIA
Signature : `<body onload="loadFormAskia();">`, form action `AskiaExt.dll`
Inputs : schéma `M{N} {value}` (checkbox/radio) ou `U{N}` (hidden slider)
Plusieurs types de questions peuvent coexister sur une même page — extracteurs indépendants, résultats concaténés.

### _extract_askia_adc_slider
Fichier : Survey/dom_extractors_misc.py
Guard : `div.adc-slider` contenant `div.noUiSlider` + `div.noUi-handle`
Patterns couverts :
- Input hidden `<input type="hidden" name="U{N}">` dans le même conteneur
- Labels pôles : `div.leftLabel`, `div.rightLabel` ; bouton DK optionnel : `div.dk[data-value]`
- Sous-question : `td.askia-question-label` ou `td[class*="askia-caption"]` dans le tr précédent
- Question globale possible en tête de page (format retourné : "global | sous-question")
- Valeurs exposées : 0%…100% par pas de 10, + DK si présent
Patterns exclus :
- Matrices checkbox (`div.adc-responsiveTable`) → extracteur distinct
- Radios classiques Askia (`div.myresponse`) → extracteur distinct
- Sliders Decipher sq-sliderpoints → input_slider.py

### _apply_by_target_id — bloc askia_responsive_table_checkbox
Fichier : Survey/action_dispatcher.py
Emplacement : bloc `if opt_map and resolved_itype in ("radio", "checkbox")`, avant `_click_candidate`.
Guard : `payload.get("askia_responsive_table_checkbox") and resolved_itype == "checkbox"`
Patterns couverts :
- Inputs `<input type="checkbox">` masqués CSS (taille 0) dans `div.adc-responsiveTable`
- Clic JS sur `<label for=inputId>` → déclenche les handlers Askia natifs
- Fallback : `input.checked = true` + dispatchEvent input/change si aucun label
- Vérification : `input.checked === true` après action
Patterns exclus :
- Sliders noUiSlider → _extract_askia_adc_slider
- Radios classiques Askia → chemin générique opt_map

---

## PLATEFORME : INTERVIEW-LAYOUT (areyounet / Potloc-style)
Signature DOM : `div.interview-layout` > `div.interview-header` + `div.interview-question` + `div.interview-footer__container`
Inputs : `button.choice-question__field[role="option"]` dans `ul.choice-question__field-list`
Options spéciales footer : `button[data-test-id="InterviewFooter_SpecialOption-*"]`
Champ libre "Autre" : `<input type="text" role="option">` dans `div.choice-question__custom-field-container`

### button_group générique — filtre interview-footer__options-container
Fichier : Survey/dom_analyzer.py
Emplacement : boucle `for b in btn_like`, après filtre CookieYes, avant `_nearest_question_container`.
Guard : `closest('.interview-footer__options-container') !== null`
Patterns couverts :
- Boutons "Aucun(e)", "Passer" dans `.interview-footer__options-container` — options de navigation, pas des réponses
- Exclus avant groupement → pas de bloc parasite
Patterns exclus :
- Tout bouton hors de `.interview-footer__options-container`

### button_group générique — récupération h1.interview-header__title
Fichier : Survey/dom_analyzer.py
Emplacement : boucle btn_groups, après `_extract_question_from_container`, avant filtre "un problème est survenu".
Guard : `closest('.interview-question') !== null` ET `h1.interview-header__title` existe dans la page
Patterns couverts :
- Question principale dans `h1.interview-header__title` (frère de `.interview-question` dans `.interview-layout`), hors scope du conteneur résolu
- Sans ce patch : seul le `h3.hint-text` ("CHOISISSEZ UNE OU PLUSIEURS RÉPONSES") est extrait
- Le texte du h1 est préfixé à la question si non déjà inclus
Patterns exclus :
- Conteneurs hors `.interview-question`
- DOM sans `h1.interview-header__title`

### button_group générique — détection multi-select image-choice
Fichier : Survey/dom_analyzer.py
Emplacement : boucle btn_groups, bloc `_is_choice_multiple`, après guard `ChoiceMultiple_ChoiceFields`.
Guard : `btns[0].closest('div[role="listbox"]')` avec classe `image-select` ou `image-choice-question__answers`
Patterns couverts :
- Questions image-choice multi-sélection : `div[role="listbox"].image-select` avec `button[role="option"]`
- Sans ce patch : itype=radio, max_select=1 (faux) car `ChoiceMultiple_ChoiceFields` ne matche pas ce conteneur
Patterns exclus :
- Boutons hors `div[role="listbox"].image-select`

### singles — filtre champ texte libre choice-question__custom-field-container
Fichier : Survey/dom_analyzer.py
Emplacement : chemin `other_inputs`, après `_is_other_specify_choice_companion`, avant `looks_like_other`.
Guard : `itype in ("text", "textarea")` ET `role="option"` ET `closest('.choice-question__custom-field-container') !== null`
Patterns couverts :
- `<input type="text" role="option">` dans `.choice-question__custom-field-container` (champ libre "Autre")
- `_is_other_specify_choice_companion` ne le détecte pas (0 input radio/checkbox dans le conteneur) → bloc text parasite sans ce guard
Patterns exclus :
- Tout input sans `role="option"` ou hors `.choice-question__custom-field-container`

---

## PLATEFORME : FORSTA / CONFIRMIT WIX
Signature : form action `*.aspx`, class `cf-page`, questions dans `div.cf-question.cf-question--{type}`
Extracteurs CF regroupés dans un bloc d'accumulation commun (pattern extend, pas return séquentiel).

### _extract_confirmit_cf_single_choice_blocks
Fichier : Survey/dom_extractors_misc.py
Guard : `div.cf-question--single` contenant `div.cf-list > div.cf-radio[role='radio']`
Patterns couverts :
- Texte depuis `div.cf-question__text`, options depuis `div.cf-radio-answer__text`
- Exclusion des conteneurs dans `table.cf-table-layout` (grids)
Patterns exclus :
- `cf-question--numeric-list` → _extract_confirmit_cf_numeric_list_blocks
- `cf-question--open-list` → _extract_confirmit_cf_open_list_blocks
- `table.cf-table-layout` → _extract_confirmit_cf_desktop_grid_blocks

### _extract_confirmit_cf_numeric_list_blocks
Fichier : Survey/dom_extractors_misc.py
Guard : `div.cf-question--numeric-list` contenant `div.cf-numeric-list-answer`
Patterns couverts :
- Un bloc distinct par ligne : `div.cf-numeric-list-answer__text` (label) + `input[type="number"]`
- Fallback : si aucun `div.cf-numeric-list-answer`, un seul bloc sur `inputs[0]` (ex : question âge)
- Répartition : `div.cf-numeric-list-auto-sum` → `multi_sum_total=100` + `group_id` + `group_question` dans context
- Flag payload : `confirmit_cf_numeric_list=True` ; itype retourné = "number" (normalisé "text" dans prompt_builder)
Patterns exclus :
- `cf-question--single`, `cf-question--open-list`

### _extract_confirmit_cf_open_list_blocks
Fichier : Survey/dom_extractors_misc.py
Guard : `div.cf-question--open-list` contenant `input[type="text"].cf-open-list-answer__input`
Patterns couverts :
- Texte depuis `div.cf-question__text`
- Fallback : si vide, cherche le frère `div.cf-question--info` précédent (pattern CP — libellé dans `i{N}_text`, input dans CP)
- Flag payload : `confirmit_cf_open_list=True`
Patterns exclus :
- `cf-question--single`, `cf-question--numeric-list`

### filter_blocks_for_openai — normalisation itype number → text
Fichier : Survey/prompt_builder.py
Guard : `it_lc == "number"`
Patterns couverts :
- Blocs `itype="number"` (Confirmit numeric list) normalisés en `"text"` avant envoi à OpenAI
- DOM_REGISTRY conserve `itype="number"` et `confirmit_cf_numeric_list=True` → dispatcher non affecté
Patterns exclus :
- Tout autre itype

### build_batch_prompt — contrainte somme répartition Confirmit
Fichier : Survey/prompt_builder.py
Emplacement : boucle de rendu des blocs, après `lines.append(f"contexte: {q}")`.
Guard : `ctx.get("confirmit_cf_numeric_list")` ET `ctx.get("multi_sum_total")` truthy
Patterns couverts :
- Injection de `groupe_contexte` (question parente) et `contrainte_somme` dans le prompt pour les blocs de répartition (somme=100)
Patterns exclus :
- Blocs sans `confirmit_cf_numeric_list` ou sans `multi_sum_total`

### _extract_confirmit_cf_hrs_single_blocks — mode carousel
Fichier : Survey/dom_extractors_misc.py
Guard : `div.cf-question--carousel-horizontal-rating-scale-grid` avec `div.cf-carousel` contenant `div.cf-carousel__content-item > div.cf-hrs-single[role='radiogroup']`
Patterns couverts :
- 1 bloc par item ; question = `span#{item_id}_text` préfixé par `div.cf-question__text`
- Options : `innerText` des `div.cf-horizontal-rating-item` (pas `aria-label` — contient le préfixe de ligne)
- `group_key = radio:name:dom:{labelledby}|cf-hrs-single|{item_id}` (discriminant unique par item)
- Context enrichi : `is_last_carousel_item`, `carousel_item_index`, `carousel_total_items`
- Flag payload : `confirmit_cf_hrs_single=True`
Note DOM : sans `item_id` dans le `group_key`, tous les radiogroups ont le même `aria-labelledby` → dédupliqués en 1 seul bloc.
Patterns exclus :
- `div.cf-hrs-single` standalone (hors `div.cf-carousel__content-item`) → chemin existant
- `div.cf-carousel` avec `div.cf-answer-button` → _extract_confirmit_cf_carousel_blocks

### _should_skip_post_actions_navigation — skip CTA carousel cf-hrs-single intermédiaire
Fichier : Survey/survey_executor.py
Guard : au moins un bloc dans `question_blocks` avec `context.is_last_carousel_item=False`
Patterns couverts :
- Pages carousel cf-hrs-single : après chaque sélection, le carousel avance automatiquement (mutation DOM, URL stable)
- CTA bloqué tant que `is_last_carousel_item=False` ; autorisé si `True` ou marqueur absent
Patterns exclus :
- Blocs cf-hrs-single standalone (pas de `is_last_carousel_item` dans context)
- Autres providers auto-navigation (walr_cardsort, studystream_auto_advance, qarts_autosubmit)

### _extract_confirmit_cf_single_image_choice_blocks
Fichier : Survey/dom_extractors_misc.py
Enregistré dans : dom_analyzer.py (bloc cf_combined, après _extract_confirmit_cf_single_choice_blocks)
Guard (double) :
  1. `div.cf-question--single` présent
  2. contient `div.cf-image-answer` ET `div.cf-image[role='radio']`
Patterns couverts :
- Question single-choice dont les options sont des images (ex : silhouettes genre Toluna)
- Texte option depuis `div.cf-image-answer__text` ; fallback : `aria-label` du contrôle ; fallback : `alt` de `img`
- Cible du clic : `div.cf-image[role='radio']` (par id)
- `group_key = radio:cf-single-image:{q_id}` ; flag payload : `confirmit_cf_single_image=True`
- Log discriminant : `[DOM_CONFIRMIT_CF_SINGLE_IMAGE] blocks_extracted=N`
Patterns exclus :
- `div.cf-radio[role='radio']` standard → `_extract_confirmit_cf_single_choice_blocks` (inchangé)
- `div.cf-question--multi` → `_extract_confirmit_cf_multi_choice_blocks`

### _extract_confirmit_cf_multi_choice_blocks
Fichier : Survey/dom_extractors_misc.py
Enregistré dans : dom_analyzer.py (bloc cf_combined, après _extract_confirmit_cf_single_image_choice_blocks)
Guard (double) :
  1. `div.cf-question--multi` présent
  2. contient `div.cf-checkbox[role='checkbox']`
Patterns couverts :
- Question multi-choice (checkbox) : options dans `div.cf-checkbox-answer__text`, cible `div.cf-checkbox[role='checkbox']`
- Option exclusive (type cf-radio-answer avec role='checkbox') : fallback `div.cf-radio-answer__text` + `div.cf-radio[role='checkbox']`
- `max_select = len(options)`, `min_select = 1`
- `group_key = checkbox:cf-multi:{q_id}` ; flag payload : `confirmit_cf_multi=True`
- Log discriminant : `[DOM_CONFIRMIT_CF_MULTI] blocks_extracted=N`
Patterns exclus :
- `div.cf-question--single` → extracteurs single existants
- `div.cf-question--numeric-list`, `--open-list`, `--ranking` → leurs extracteurs respectifs
- `table.cf-table-layout` (grids) → `_extract_confirmit_cf_desktop_grid_blocks`

| Toluna/Confirmit wix | _extract_confirmit_cf_single_image_choice_blocks | _extract_confirmit_cf_single_choice_blocks | `div.cf-image-answer` + `div.cf-image[role='radio']` présents dans le conteneur (vs `div.cf-radio[role='radio']` standard) |
| Toluna/Confirmit wix | _extract_confirmit_cf_multi_choice_blocks | extracteurs single/numeric/open | class `cf-question--multi` sur le div parent + `div.cf-checkbox[role='checkbox']` présent |

---

## PLATEFORME : CONFIRMIT / FORSTA WIX — RANKING
Signature DOM : `div.cf-question.cf-question--ranking` > `div.cf-list` > N `div.cf-list__item.cf-ranking-answer[role="button"]`
Interaction : clic séquentiel — chaque clic attribue le rang suivant. Pas d'input natif.
Signal sélection : `cf-ranking-answer--selected` + `aria-pressed="true"` + `cf-ranking-answer__rank` contient un entier.
Signal quota : items non sélectionnés reçoivent `cf-ranking-answer--disabled`.

### _extract_confirmit_cf_ranking_blocks
Fichier : Survey/dom_extractors_misc.py
Guard : `div.cf-question--ranking`
Patterns couverts :
- Texte depuis `div.cf-question__text` ; instruction depuis `div.cf-question__instruction` fusionnée dans `question_for_openai`
- `max_select = len(options)` (nombre total d'items DOM)
- Options = textes des `div.cf-ranking-answer__text` ; items "Autres" (sans cette div) exclus
- Flag payload : `confirmit_cf_ranking=True`
Patterns exclus :
- `cf-question--single`, `cf-question--numeric-list`, `cf-question--open-list`

### _apply_by_target_id — bloc confirmit_cf_ranking
Fichier : Survey/action_dispatcher.py
Emplacement : dans `_apply_by_target_id()`, après guard `toluna_runtime_ranking`.
Guard : `payload.get("confirmit_cf_ranking") and resolved_itype == "checkbox"`
Patterns couverts :
- Clic JS direct sur `div.cf-list__item.cf-ranking-answer[role="button"]`
- Item déjà sélectionné ou disabled → `True` immédiat
- Validation : `cf-ranking-answer--selected` présent dans les 1s, ou disabled (dernier rang)
Patterns exclus :
- Autres providers ranking (askia_ranking_isotope, decipher_clickable_ranking, toluna_runtime_ranking)

---

## PLATEFORME : SUPPLIER (supplier-{N} / js-question-options)
Signature DOM : `<body class="supplier-{N}">`, form `#aspnetForm`, conteneur `div#templates`
Structure : `div#templates > div.question + div.answer > div.options.js-question-options`
Note DOM : `div.question` (texte) et `div.answer` (inputs) sont frères, pas ancêtre/descendant — contrairement à la majorité des providers.

### _nearest_question_container — guard js-question-options
Fichier : Survey/dom_question_extractor.py
Emplacement : dans `_nearest_question_container()`, avant le retour du conteneur trouvé.
Guard : conteneur trouvé a la classe `js-question-options` (ou `js-resize-choices`)
Patterns couverts :
- `div.options.js-question-options` matche à tort `contains(@class,'question')` via le token `js-question-options`
- Fix : remontée vers `div.question` frère de `div.answer` dans le parent commun (`div#templates`)
Patterns exclus :
- Tout conteneur sans classe `js-question-options`

---

## PLATEFORME : TOLUNA / CONFIRMIT WIX NATIF
Signature DOM : form action `/wix/2/`, `fieldset[id^="fieldset_"]` contenant `table.confirmit-table`

### _extract_confirmit_wix_fieldset_radio_block
Fichier : Survey/dom_extractors_misc.py
Enregistré dans : `dom_analyzer.py` step `0h-sexies`
Guard : `fieldset[id^="fieldset_"]` + `table.confirmit-table` — 3 variantes selon les inputs présents :
- ≥2 `input[type="radio"]` même `name` → itype=radio
- 0 radio + ≥2 `input[type="checkbox"]` → itype=checkbox (pure-checkbox multi-select, ex. Toluna /wix/2/)
- 1 `radio[issinglepunch="true"]` + ≥2 checkboxes → itype=checkbox (mixte)
Patterns couverts :
- Inputs masqués (`position:absolute; top:-9000px`) — non interactables via Selenium standard
- Question : `div[id="{gname}_text"]` puis `div[id$="_text"].question_text_ng` (fallback)
- Clic : `<a href="javascript:void(0)">` dans la même `<td>` (XPath : `//input[@id=...]/ancestor::td[1]//a[1]`)
- Labels tronqués à 80 chars (`_LABEL_MAX=80`)
- Flag payload : `confirmit_wix_fieldset_radio=True`
Patterns exclus :
- Layouts Confirmit modernes (`cf-question--*`) → extracteurs cf_*
- Modals consentement (`#modal-container`, `.consent-form-radiogroup`) → `_extract_consent_modal_radio_block`
- Checkboxes consentement → `_extract_single_consent_checkbox_block`
- fieldset avec classe `confirmit-rankedorderclick-default` → `_extract_confirmit_wix_rankedorderclick_block`
  (couvre aussi le cas pure-checkbox 0 radio + ≥2 checkboxes avec `td.confirmit-rankedorderclick`)
- `table.confirmit-grid` dans le fieldset → `_extract_confirmit_wix_checkbox_grid_blocks`

### _apply_by_target_id — cache de stratégie gagnante (_cm_strategy_cache)
Fichier : Survey/action_dispatcher.py
Emplacement : bloc `toluna_runtime_answerrow` dans `_click_candidate`, avant la séquence de fallbacks.
Guard : `cm_key` présent dans `_cm_strategy_cache` (dict module-level, clé = identifiant de structure DOM, ex: `"cmix_simple_grid"`)
Patterns couverts :
- Grilles Toluna cmix_simple_grid (12 lignes, mêmes colonnes) : la première stratégie qui réussit
  est mémorisée sous `cm_key` ; les lignes suivantes sautent directement à `skip_to=N`
- Clé de cache : `cm_key` extrait du `group_key` du payload (ex: `cmix_simple_grid:name:62212050` → clé `cmix_simple_grid`)
- Cache reset : à chaque nouvelle page (appelé depuis survey_executor au début d'un nouveau step)
- Log au skip : `[TARGET_DEBUG] _click_candidate: skip_to={N} cm_key='{cm_key}'`
Patterns exclus :
- Blocs sans `cm_key` → séquence complète inchangée
- Structures avec un seul item (cache inutile) → skip_to=0, comportement identique à avant

### _click_candidate — pré-détection label-intercept (skip vers CDP)
Fichier : Survey/action_dispatcher.py
Emplacement : dans `_click_candidate`, juste après le calcul de `_first` via le cache, avant la méthode 1 (click natif).
Guard : `_first == 1` (premier appel, cache vide pour ce `cm_key`) ET le noeud est un `<input id=...>` dont le
`parentElement` contient un `label[for="{id}"]` (sibling direct) — générique, pas lié à une plateforme précise.
Patterns couverts :
- Inputs natifs `<input type="checkbox">`/`radio` immédiatement suivis d'un `<label for=...>` dans le même parent
  (ex. Alchemer/Gizmo) : le label recouvre visuellement l'input → méthodes 1 (click natif) et 2 (ActionChains
  hover+click) échouent quasi systématiquement après 30s de timeout chacune ("intercepts pointer events")
- Sans ce patch : ~60s perdues sur le tout premier clic d'un groupe avant de retomber sur la méthode 3 (CDP)
  qui elle réussit et alimente normalement le cache pour les clics suivants du même `cm_key`
- Avec le patch : `_first` est forcé à 3 dès la détection, sans jamais toucher au corps des méthodes 1/2/3/4
- Log : `[TARGET_DEBUG] _click_candidate: label-intercept detected on {label!r}, skip to CDP`
Patterns exclus :
- Inputs sans `id`, ou label absent/non-sibling direct du parent → séquence complète inchangée (1→2→3→4)
- Appels avec `_first > 1` (cache déjà résolu) → pré-détection non exécutée, skip_to standard s'applique

### _extract_confirmit_wix_rankedorderclick_block
Fichier : Survey/dom_extractors_misc.py
Guard : `fieldset[id^="fieldset_"].confirmit-rankedorderclick-default`
Patterns couverts :
- Items : `td.confirmit-rankedorderclick[tabindex="0"]` avec `label[for=cq{N}_{M}]`
- Question : `div[id$="_text"].question_text_ng` ; instruction fusionnée dans `question_for_openai`
- `min_select` extrait depuis `div[id$="_error"].error_text` (pattern "fournir N réponses")
- itype produit : "checkbox" ; flag : `confirmit_wix_rankedorderclick=True`
- Labels tronqués à 80 chars (même convention que fieldset_radio_block)
- Couvre aussi le cas pure-checkbox (0 radio + ≥2 checkboxes masqués) dès que le fieldset
  porte `confirmit-rankedorderclick-default` — interaction toujours via `td.confirmit-rankedorderclick`
Patterns exclus :
- fieldset sans `confirmit-rankedorderclick-default` → _extract_confirmit_wix_fieldset_radio_block
- `div.cf-question--ranking` (Forsta moderne) → _extract_confirmit_cf_ranking_blocks

### _apply_by_target_id — bloc confirmit_wix_rankedorderclick
Fichier : Survey/action_dispatcher.py
Guard : `payload.get("confirmit_wix_rankedorderclick") and resolved_itype == "checkbox"`
Patterns couverts :
- Selenium click natif sur `td.confirmit-rankedorderclick[tabindex="0"]` (tabindex → interactable)
- Fallback si ElementNotInteractable : `MouseEvent bubbles+cancelable` via JS
- Validation : `span.confirmit-ranked-order-value` contient un entier dans les 1s
- Item déjà sélectionné (`confirmit-rankedorderclick-selected`) → `True` immédiat
Note DOM : `td.click()` JS direct ne déclenche pas les handlers YUI — Selenium click natif obligatoire.
Patterns exclus :
- `cf-question--ranking` → bloc `confirmit_cf_ranking`
- Checkboxes Confirmit non-ranking → chemin générique opt_map

### _extract_table_matrix_radio_rows — fallback confirmit-grid
Fichier : Survey/dom_extractors_misc.py
Emplacement : après lignes 1479–1490, additif.
Guard : `"confirmit-grid" in table_cls` OU `table.find_elements("table.confirmit-grid")` retourne un résultat → `_is_confirmit=True`
Patterns couverts :
- `table.confirmit-grid` imbriquée dans des tables conteneurs (class `widthpartdesktoplayout2014`, etc.)
- Si `_is_confirmit` : `matrix_question` écrasé inconditionnellement par `driver.find_elements("div.question_text_ng")[0]`, tronqué à 500 chars
Note DOM : la table traitée peut être le conteneur externe (pas la grille elle-même) — d'où la détection via `find_elements` sur la table courante, pas sur `table_cls` seul.
Patterns exclus :
- Tables sans `table.confirmit-grid` → chemin existant inchangé
- Matrices Askia, SGE, IntelliSurvey, Encuesta → guards distincts

### dom_analyzer.py — priorité label[for] pour itype text/textarea
Fichier : Survey/dom_analyzer.py
Emplacement : boucle "autres inputs", avant `_extract_question_from_container()`.
Guard : `not question and itype in ("text", "textarea") and el_id`
Patterns couverts :
- `input[type="text"]` avec id dont le `<label for="id">` est hors du fieldset parent (dans `div[id$="_text"].question_text_ng`)
- `_extract_question_from_container()` retournerait l'instruction de format au lieu de la question
- Résolution : `label[for="{el_id}"]` portée globale driver, validé par `_is_question_text()`
Patterns exclus :
- Inputs sans id
- itype dropdown → chemin séparé inchangé
- Matrices radio/checkbox Confirmit → extracteurs dédiés

### fill_text_input — paramètre element_id
Fichier : Survey/input_text.py
Guard : `field is None and element_id`
Patterns couverts :
- Champ texte Toluna/Confirmit `/wix/2/` pour lequel `find_context_container` retourne `None`
- Résolution : `By.ID` en priorité, fallback `By.NAME`
- `element_id` transmis depuis `action_dispatcher.py` via `target_payload["context"]["id"]`
Patterns exclus :
- Champs avec scope résolu par `find_context_container` → `element_id` inutilisé

### execute_action — passage context.id → fill_text_input
Fichier : Survey/action_dispatcher.py
Emplacement : branche `itype in ("text", "number")`, avant `_try("text_input", ...)`.
Guard : `target_payload` présent ET `target_payload["context"]["id"]` non vide
Patterns couverts :
- Lecture `_field_id = target_payload.get("context", {}).get("id", "").strip() or None`
- Passage via closure `lambda fid=_field_id: ...` (évite capture tardive)
Patterns exclus :
- itype hors ("text", "number") ; context["id"] vide → `_field_id=None`, comportement inchangé

### build_batch_prompt — règle année courante dynamique
Fichier : Survey/prompt_builder.py
Guard : toujours injecté (règle statique, valeur calculée au runtime)
Patterns couverts :
- Questions "en quelle année sommes-nous", "what year is it", "current year", "année en cours"
- Valeur injectée : `datetime.now().year` ; format attendu : entier 4 chiffres
Patterns exclus :
- Questions année de naissance → règle birth year distincte
- Questions âge avec options fermées → règle distincte

### execute_action — post-vérification target_id MetrixLab/Toluna QT
Fichier : Survey/action_dispatcher.py
Emplacement : après retour positif de `_apply_by_target_id()`, avant log `strategy=target_id reason=applied`.
Guard : `target_id` présent ET `_apply_by_target_id()` → `True` ET page avec `div.answer_options` + `input.checkboxQT/radioQT`
Patterns couverts :
- État sélectionné porté par `.option_checkbox.input_on` et/ou `.option_label.input_label_on`
- Bloque le faux positif : ne logue `apply ok=true` que si le DOM confirme l'activation
Patterns exclus :
- Checkboxes/radios natifs validés par `input.checked`
- Widgets déjà vérifiés dans `_apply_by_target_id` (QARTS, Nfield, Askia, Toluna Runtime, etc.)

### execute_action / open_dropdown_generic — dropdown natif sans ouverture préalable
Fichiers : Survey/action_dispatcher.py, Survey/input_dropdown.py
Guard : action dropdown + présence possible d'un `<select>` natif
Patterns couverts :
- `<select>` natif sélectionnable directement par `select_option_with_hint()` sans `dropdown_open`
- `open_dropdown_generic()` ne clique/focus/envoie pas `ARROW_DOWN` sur un `<select>` natif
Patterns exclus :
- Dropdown custom nécessitant ouverture de menu
- Bootstrap-select / GfK `.mrDropdown` / RPS custom → guards DOM dédiés dans `select_option_with_hint()`

---

## PLATEFORME : KANTAR / mrIWeb — ROWPICKER RADIO (REACT OVERLAY)
Signature DOM : `form[name="mrForm"]` (sa.ktrmr.com), `metaType="rowpicker"` dans le SEJson.
Double couche : `div.questionContainer[questionname][display:none]` (inputs natifs non interactables) + `div#container_{questionname}._rowpicker` (cartes React cliquables).

### _extract_kantar_rowpicker_radio_blocks
Fichier : Survey/dom_extractors_misc.py
Emplacement : appelée en priorité dans `analyze_dom` avant l'extracteur générique radio/checkbox.
Guard : `div[id^='container_'] [data-test='main-contain']._rowpicker` présent dans le DOM
Patterns couverts :
- Question : `#qc_{q_suffix} span.mrQuestionText` ; variante : `.questionContainer[questionname$='.{q_suffix}'] span.mrQuestionText`
- Cartes : itération sur les overlays `div[dir='ltr'][tabindex='0']` dans le picker ; remontée au conteneur de carte via `ancestor::div[@dir='ltr'][not(@tabindex)][1]` ; label depuis `label span`
- `option_xpath_map` pointe sur l'overlay (seul élément interactable), pas sur les inputs natifs
- Flag payload : `kantar_rowpicker_radio=True` ; `group_key` : `kantar_rowpicker:radio:{q_suffix}`
Note DOM : l'overlay `div[dir='ltr'][tabindex='0']` est séparé de la carte `div[dir='ltr']` par un div intermédiaire sans attribut `dir` — `div[@tabindex='0']` (enfant direct) ne matche pas ; il faut itérer sur les overlays et remonter.
Patterns exclus :
- `div[id^='sq-QARTS-container-']` (Decipher/LifePoints QARTS) → extracteur séparé
- `_rowrank` (metaType=rowrank) → `_extract_kantar_rowrank_blocks`
- Inputs natifs `input[type=radio][class*="mrSingle"]` dans `div.questionContainer` → jamais ciblés

### kantar_rowpicker_radio — guard dispatcher
Fichier : Survey/action_dispatcher.py
Emplacement : bloc opt_map, avant toute résolution de `el` par `_find_best_visible`.
Guard : `payload.get("kantar_rowpicker_radio") and resolved_itype == "radio"`
Patterns couverts :
- Bypass total de `_find_best_visible` / `_click_candidate` → appel direct `click_kantar_rowpicker_radio(driver, value)`
Note DOM : `_find_best_visible` a un fallback qui retourne le meilleur candidat même non-visible — les inputs dans `display:none` sont retournés (tag=input, score=100), ce qui court-circuite tout guard placé après.
Patterns exclus :
- Tous les autres itypes et providers

### click_kantar_rowpicker_radio
Fichier : Survey/input_radio.py
Guard : appelée uniquement depuis le guard `kantar_rowpicker_radio` du dispatcher.
Patterns couverts :
- `_JS_FIND` : cherche l'overlay par label dans `._rowpicker`, via `closest('div[dir="ltr"]')` + `querySelector('div[tabindex="0"]')` avec vérification `cursor` dans le style
- Clic : `overlay.click()`, fallback `ActionChains`
- `_JS_VERIFY` : changement de `background-color` sur `div[style*="transition: background-color"]` de la carte
Note DOM : `input.checked` toujours `false` sur ce DOM — les inputs natifs dans `display:none` ne sont jamais synchronisés par React. Vérification obligatoirement via background-color de la carte.
Patterns exclus :
- `div[id^='sq-QARTS-container-']` → guard DOM distinct

---

### _extract_confirmit_wix_checkbox_grid_blocks
Fichier : Survey/dom_extractors_misc.py
Enregistré dans : dom_analyzer.py (step dédié)
Guard (triple) : `fieldset[id^="fieldset_"]` + `table.confirmit-grid` dans ce fieldset + ≥2 `th.grid_scale_ng`
Patterns couverts :
- Colonnes : `th.grid_scale_ng` — label via `childNodes[nodeType=3]` JS direct (exclut `div[style*="display:none"]`)
- Lignes : `tbody tr` contenant `td.grid_answer_input` ou `td.grid_alternating_answer_input`
- Facteur (label ligne) : `th.grid_answer_label_ng` ou `th.grid_alternating_answer_label_ng`, même technique JS direct
- `rowIdx` extrait du `name` du premier `input[type=checkbox]` : pattern `x{col}_{row}`
- 1 bloc par ligne visible (rowIdx ≠ 98) ; `question = {global} {instruction} - {facteur}` ; options = col_labels
- `option_xpath_map` scopé par `input[@id]` : `//input[@id={cb_id}]/ancestor::td[1]` — un XPath par colonne
- Flag payload : `confirmit_wix_checkbox_grid=True` ; `group_key : confirmit_checkbox_grid:row:{rowIdx}`
Patterns exclus :
- `table.confirmit-table` → _extract_confirmit_wix_fieldset_radio_block
- fieldset avec `confirmit-rankedorderclick-default` → _extract_confirmit_wix_rankedorderclick_block
- rowIdx=98 ("Autre") → laissé au bloc text extrait ailleurs

### _apply_by_target_id — exclusion _skip_opt_map_for_cached_checkbox pour confirmit_wix_checkbox_grid et confirmit_wix_fieldset_radio
Fichier : Survey/action_dispatcher.py
Emplacement : calcul de `_skip_opt_map_for_cached_checkbox`, bloc `# --- cas "options map" (radio/checkbox)`.
Guard : `and not payload.get("confirmit_wix_checkbox_grid") and not payload.get("confirmit_wix_fieldset_radio")`
Problème résolu : sans ce guard, dès que `checkbox_main` réussit sur la première option, le cache l'enregistre sous `target_id`. Pour les options suivantes, `_skip_opt_map_for_cached_checkbox=True` bypasse l'`option_xpath_map` → `checkbox_main` cherche le label en pleine page sur des labels tronqués → fausse correspondance ou échec.
Correction : ces blocs doivent toujours passer par `option_xpath_map` (XPath scopé par `input[@id]`).
Patterns exclus :
- Tous blocs checkbox sans ces flags → comportement cache inchangé

### _apply_by_target_id — vérification post-clic img src pour confirmit_wix_fieldset_radio checkbox
Fichier : Survey/action_dispatcher.py
Emplacement : dans le chemin XPath opt_map, juste avant `return False` final (après `_wait_checked` et `_ipsos_slider_value_matches`).
Guard : `payload.get("confirmit_wix_fieldset_radio") and resolved_itype == "checkbox"`
Problème résolu : `el` est un `<a href="javascript:void(0)">` ; l'input est un **sibling** dans la même `<td>`, pas un descendant → `_first_input_under(el)` retourne None → `inp_id=None` → `_wait_checked(None, None)` → False. De plus, cliquer le `<a>` ne met pas `input.checked=true` (YUI image-buttons) → vérification `e.checked` toujours False.
Correction : après le clic, vérifier `el.find_element(By.XPATH, './/img[1]')` — src passe de `check_up.png` à `check_down.png` quand la case est cochée.
Patterns exclus :
- Radios Toluna wix (même flag, mais itype=radio) → chemin radio distinct, `_wait_checked` fonctionne sur `input[name]:checked`
- Tout payload sans `confirmit_wix_fieldset_radio`

---

## PLATEFORME : DATADIGGERS ICONTROL (AngularJS Screener)
Signature DOM : `div.main_survey_page` + `form[id^="attention_questions_"]`, `ng-app="dataDiggerBackendApp"`
Domaine observé : api-icontrol.datadiggers-mr.com

### _extract_datadiggers_icontrol_radio_block
Fichier : Survey/dom_extractors_misc.py
Enregistré dans : dom_analyzer.py (step 0i-quinquies, avant le pipeline générique radio/checkbox)
Guard (double) :
  1. `div.main_survey_page` présent dans le DOM
  2. `div.main_survey_page form[id^='attention_questions_']` présent
Patterns couverts :
- Question : `h5.questions > span.main_crd_heding` dans le form (hors conteneur des inputs)
- Options : `div.survey_radioBtn > div.opt_color > input[type=radio]` + `label` (4 options typiques)
- Tous les radios partagent `name="radio-group"` — non discriminant, ignoré
- XPath option ancré sur `input[@id]` + `@name` → remonte à `div.survey_radioBtn[1]` (cliquable)
- Flag payload : `datadiggers_icontrol_radio=True`
- Bouton navigation : `button[type=submit].next_btn` avec `ng-disabled` Angular (déclenché par modèle, pas par `input.checked`)
Patterns exclus :
- Autres types de questions DataDiggers non observés (questionType != 0)
Note DOM : `input.checked` non fiable sur ce DOM Angular — la sélection passe par `ng-click` sur `div.survey_radioBtn` qui met à jour `demographic.selected_opt` dans le scope Angular. Le clic doit cibler le `div.survey_radioBtn` (via XPath), pas l'input nu.

---

## CHAMP DATE TRIPLET (DOM générique — prsrvy.com / Prodege et similaires)
Signature DOM : 3 `<input type="text">` dans un même conteneur, avec `name` ou `id`
contenant les tokens `date_m` / `date_d` / `date_y` (ou variantes : `month`, `day`, `year`, `mm`, `dd`, etc.)
Log discriminant : `[DOM_DATE_MULTI_TEXT] detected date triplet`

### Extracteur date triplet — 3 blocs distincts
Fichier : Survey/dom_analyzer.py
Emplacement : boucle `singles`, branche `has_date_triplet`, après détection des tokens mois/jour/année.
Guard : `has_date_triplet=True` (≥3 champs + présence des 3 tokens month/day/year dans les blobs de champ)
Patterns couverts :
- Création de 3 blocs indépendants (itype=text, max_select=1), un par rôle : month / day / year
- `target_id` préfixé `date_` avec suffixe de rôle : `date_{hash}:month`, `date_{hash}:day`, `date_{hash}:year`
- `context.kind = "single"` (pas `multi_text`) — traité comme 3 champs simples par le parser
- Questions soumises à GPT : "Birth month (MM)", "Birth day (DD)", "Birth year (YYYY)"
- GPT répond avec 3 chiffres distincts (ex: `06`, `15`, `1998`) — format naturel, sans logique multi_text
- `seen_multi_text_groups` mis à jour après insertion pour éviter doublon
Patterns exclus :
- Triplets non détectés (tokens absents dans les blobs) → chemin `multi_text` classique (max_select=N)
- Autres groupes de champs texte sans token date → extracteur multi_text générique inchangé
Note architecture : avant ce patch, le triplet était extrait comme 1 seul bloc `multi_text` (max_select=3).
GPT produisait des valeurs hétérogènes (mois en lettres, ordre non garanti) → `received=0` dans
`_enforce_selection_ranges` car les 3 actions perdaient leur `qid` ou étaient rejetées silencieusement.
La séparation en 3 blocs distincts supprime toute logique spéciale dans `batch_response_parser.py`.

---

## PLATEFORME : PRODEGE / SWAGBUCKS PRESCREENER (prsrvy.com)
Signature DOM : `div.profilerContainer` > `div.profilerContent` > `section.profilerQuestionSection`
Domaine observé : prsrvy.com (Prodege/Swagbucks)
Question : `p.profilerQuestionText`
Options : `ul.profilerAnswer[data-type="radio"]` > `li.profilerAnswerRadio` > `input.profilerRadioInput[type=radio]` + `label.profilerRadioLabel`
Log discriminant : `[DOM_PRODEGE_PRESCREENER] extracted 1 radio block`

### _extract_prodege_prescreener_radio_block
Fichier : Survey/dom_extractors_misc.py
Enregistré dans : dom_analyzer.py (step 0i-sexies, avant le pipeline générique radio/checkbox)
Guard (double) :
  1. `div.profilerContainer` présent dans le DOM
  2. `div.profilerContainer p.profilerQuestionText` présent
Patterns couverts :
- Question : `p.profilerQuestionText` (textContent)
- Options : `li.profilerAnswerRadio > label.profilerRadioLabel` + `input.profilerRadioInput[type=radio]`
- XPath option ancré sur `input[@id]` dans `div.profilerContainer` (scope strict)
- `group_key = prodege_prescreener:radio:{input_name}` (ex: `question_3`)
- Flag payload : `prodege_prescreener_radio=True` ; itype=radio, max_select=1
- Dispatch : chemin générique `opt_map` (aucun bloc dispatcher spécifique)
Patterns exclus :
- `ul.profilerAnswer[data-type!="radio"]` (non observé, guard passif)


## PLATEFORME : DECIPHER / FOCUSVISION — DROPDOWN + CHAMP "AUTRE" FRÈRE

### Pruning du bloc text companion d'un dropdown Decipher (div.question[id$="_open"])
Fichier : dom_analyzer.py (pipeline post-extraction)
Guard : présence d'un `div.question[id]` frère dont l'id est `<QID>_open` et dont le div.question du dropdown a l'id `<QID>`

**Symptôme corrigé :**
Sur les pages Decipher/FocusVision exposant une question dropdown (`div.question.noRows.noCols` + `div.fir-select > select.input.dropdown`) accompagnée d'un champ texte libre "Autre" rendu dans un `div.question` frère avec id suffixé `_open` (ex: `question_Q8x2` / `question_Q8x2_open`), le bot extrait deux blocs distincts — un `itype=dropdown` et un `itype=text` — ce qui conduit GPT à produire deux réponses pour une seule question.

**Cause racine :**
Les guards existants (`_is_open_ended_choice_companion`, `_is_other_specify_choice_companion`, `_prune_focusvision_auxiliary_openended_singles`) ne couvrent pas ce pattern : le champ texte est dans un `div.question` séparé (frère DOM), sans radio/checkbox dans son propre scope, sans préfixe `oe*` sur son `name`, et le groupe dropdown ne produit pas d'entrée `aux_openended_names`.

**Signal DOM discriminant :**
`id` du `div.question` contenant le dropdown = `question_<QID>` ; `id` du `div.question` contenant le `input[type='text']` = `question_<QID>_open`. Ce suffixe `_open` sur le conteneur frère est le signal Decipher de companion explicite.

**Fix appliqué :**
Nouvelle passe de pruning post-extraction dans `dom_analyzer.py` : pour chaque bloc `itype=text/textarea` de kind `single`, si son `div.question` parent a un `id` suffixé `_open` et qu'un bloc `itype=dropdown` avec `id` préfixe correspondant (`id[:-5]`) existe dans les blocs, le bloc text est supprimé.

**Patterns couverts :**
- `div.question[id="question_<QID>"]` + `div.fir-select > select.input.dropdown` (dropdown principal)
- `div.question[id="question_<QID>_open"]` + `input[type='text']` (companion supprimé)

**Patterns exclus :**
- Champs `_open` sans bloc dropdown frère correspondant (ex : question texte libre standalone)
- `oe*` open-ended dans le même conteneur que des radio/checkbox (couvert par `_is_open_ended_choice_companion`)

---

## PLATEFORME : DECIPHER / NORSTAT — RANKSORT (sq-ranksort, table.grid display:none)
Signature DOM : `div.question.sq-ranksort` contenant `h1.question-text`, `h2.instruction-text`,
et `table.grid[style*="display:none"]` avec N `tr.row.row-elements` (1 `<th>` + 1 `select.input.dropdown` par item)
Log discriminant : `[DOM_DECIPHER_RANKSORT] extracted 1 ranksort block: N items, M ranks`

### _extract_decipher_ranksort_dropdown_blocks
Fichier : Survey/dom_extractors_decipher.py
Guard (double) :
  1. `div.question.sq-ranksort` présent dans le DOM
  2. `table.grid` contenant au moins 1 `tr.row.row-elements` avec `<th>` + `select.input.dropdown`
Patterns couverts :
- Produit UN SEUL bloc `itype=checkbox`, `kind=group`
- `question` = texte h1 + instruction h2 fusionnés
- `options` = liste des textes `<th>` dans l'ordre DOM (les items à classer)
- `max_select` = `min_select` = nombre d'options hors placeholder dans un select (ex : 3)
- Registry payload : `rank_labels` (["Rang 1", "Rang 2", "Rang 3"]), `item_select_map`
  (item_norm → {sel_id, sel_name}), flag `decipher_ranksort_dropdown=True`
- La table.grid étant CSS-cachée (`display:none`), les selects ne sont pas visibles ;
  le pipeline générique les rejetait → ce bloc remplace les N anciens blocs dropdown
Patterns exclus :
- Autres extracteurs Decipher (answers-list, grid, QARTS) → pas de `div.question.sq-ranksort`

### _apply_by_target_id — bloc decipher_ranksort_dropdown
Fichier : Survey/action_dispatcher.py
Emplacement : avant le chemin générique `opt_map`, guard `payload.get("decipher_ranksort_dropdown") and resolved_itype == "checkbox"`
Guard : flag `decipher_ranksort_dropdown=True` + `resolved_itype == "checkbox"`
Patterns couverts :
- `value` = texte de l'item retourné par GPT (résolution fuzzy via `item_select_map`)
- `ordinal` = position 1-based de l'item dans la réponse GPT (tracké via `driver._decipher_ranksort_ordinal`)
- Sélection JS sur le select display:none : `selectedIndex` + `dispatchEvent('change')`
- Rang assigné = `rank_labels[ordinal - 1]` (1er item → Rang 1, 2ème → Rang 2, …)
- Compteurs ordinaux (`driver._decipher_ranksort_counts`) réinitialisés à chaque plan dans `execute_actions_plan`
Patterns exclus :
- Blocs checkbox sans flag `decipher_ranksort_dropdown` → chemin générique inchangé

---

## PLATEFORME : QUALTRICS — MULTI-CASES TEXTE LIBRE (FORM, N inputs)
Signature DOM : `div.QuestionOuter.TE` > `div.Inner.FORM` > `fieldset` > `table` > N `<tr>` avec `<input type="text" name="QR~{QID}~{N}~TEXT">`
Distinct de `div.Inner.SL` (1 seul input) → couvert par `_extract_qualtrics_sl_text_blocks`.
Log discriminant : `[DOM_QUALTRICS_FORM_MULTI_TEXT] blocks_extracted=1`

### _extract_qualtrics_form_multi_text_blocks
Fichier : Survey/dom_extractors_misc.py
Guard (double) :
  1. `div.QuestionOuter.TE` contenant `div.Inner.FORM`
  2. ≥2 `input[type="TEXT"][name^="QR~"]` dans ce même conteneur
Patterns couverts :
- Question : `fieldset legend label.QuestionText` (fallback : `legend label.QuestionText`, `label.QuestionText`, `div.QuestionText`)
- N inputs distincts : `id="QR~{QID}~{N}"`, `name="QR~{QID}~{N}~TEXT"` avec N = 1..10 (ou plus)
- 1 bloc unique `kind=multi_text`, `max_select=N`, `target_id` préfixé `multi_`
- Payload `fields` : liste ordonnée de dicts `{xpath, alt_xpaths, name, id, tag}` — un par input
- Dispatch : chemin générique `kind=multi_text` du dispatcher (aucun bloc dispatcher spécifique)
Patterns exclus :
- `div.Inner.SL` avec 1 seul input → `_extract_qualtrics_sl_text_blocks` inchangé
- Matrices, radios, checkboxes Qualtrics → leurs extracteurs respectifs

### parse_batch_response — fallback multi_text_bare
Fichier : Survey/batch_response_parser.py
Guard : ligne brute sans `////` + `qid_meta` contient exactement 1 QID + ce QID a `context.kind=multi_text`
Problème résolu : GPT répond parfois à une question multi_text avec la chaîne brute de valeurs (`A|B|C|...`) sans préfixe QID. En mode batch strict, cette ligne est rejetée (pas de QID valide) → 0 actions.
Correction : si les 3 conditions du guard sont réunies, mapper la ligne brute comme valeur du seul QID présent, puis la traiter normalement via `_split_values`.
Patterns exclus :
- Lignes contenant `////` → parsing normal inchangé
- `qid_meta` avec plus d'un QID → fallback non activé (ambiguïté)
- Blocs avec `context.kind != "multi_text"` → fallback non activé

### execute_actions_plan — skip rescan same_qblock pour itype text même target_id
Fichier : Survey/action_dispatcher.py
Emplacement : bloc `rescan_between_actions`, condition `same_question_block`.
Guard : `itype_lower == "text"` ET `next_itype == "text"` ET `tid == next_tid` ET `tid` non vide
Problème résolu : pour un bloc multi_text à N cases, N actions consécutives ont le même target_id et itype=text. La condition `same_question_block` ne couvrait que radio/checkbox → N-1 rescans DOM déclenchés inutilement (la structure DOM ne change pas entre deux send_keys sur des inputs distincts d'un même bloc).
Correction : étendre `same_question_block = True` quand deux actions text consécutives partagent le même target_id non vide.
Patterns exclus :
- target_id différents entre deux actions text → rescan maintenu
- itype non text → comportement inchangé

---

## PLATEFORME : DECIPHER / FOCUSVISION — MATRICE TRANSPOSÉE (colonnes = questions, lignes = options)
Signature DOM : `div.question[role='radiogroup']` > `.answers.answers-list` >
`table.grid[data-settings*='group-by-row'][data-settings*='table-mode']`
avec ≥2 `th[scope='col']` (en-têtes de colonnes = questions distinctes)
et N `tr.row-elements` (lignes = options de réponse communes à toutes les colonnes).
Log discriminant : `[DOM_DECIPHER_COL_SPLIT] extracted {N} column blocks for question …`

### _extract_focusvision_answers_list_groups — sous-branche col-split
Fichier : Survey/dom_extractors_decipher.py
Emplacement : dans la branche `group_by_row_table`, ajoutée comme sous-branche additionnelle
après la boucle existante sur `tr.row-elements`, déclenchée avant elle si le guard col-split est vrai.
Guard (double) :
  1. `table.grid[data-settings*='group-by-row']` présent (guard parent inchangé)
  2. ≥2 `th[scope='col']` dont le texte est non vide ET les inputs radio sont répartis
     par colonne (discriminant : `name` des inputs suffixé `.<col_idx>`, ex: `ans36163.0`, `ans36163.1`)
Patterns couverts :
- 1 bloc radio par colonne (`th[scope='col']`) ; question = question parente + `[label colonne]`
- Options = textes des `th[scope='row']` de chaque ligne (identiques pour toutes les colonnes)
- `group_key = radio:name:{base_name}.{col_idx}` (ex: `radio:name:ans36163.0`)
- `option_xpath_map` ancré sur `input[@id]` dans la cellule correspondant à la colonne
- `context.focusvision_answers_list = True` ; `min_select = max_select = 1`
Patterns exclus :
- Matrices classiques `group-by-row` (ligne = sous-question, colonne = échelle) → boucle row existante inchangée
- Blocs sans `th[scope='col']` multiples → chemin existant
- `div.question.sq-ranksort` → `_extract_decipher_ranksort_dropdown_blocks`

### build_batch_prompt — sibling_uniqueness_rule pour blocs radio à options identiques
Fichier : Survey/prompt_builder.py
Emplacement : boucle principale `build_batch_prompt`, rendu par bloc, après `lines.append(f"itype: {itype}")`.
Guard : groupe de ≥2 blocs `itype=radio` sur la même page partageant exactement le même
`frozenset` d'options normalisées (`_norm_folded_lc`). Détection pré-boucle, résultat stocké
dans un dict `{qid → [qid_sibling, …]}`.
Patterns couverts :
- Pour chaque bloc du groupe, injection d'une ligne :
  `sibling_uniqueness_rule: ta valeur doit être DIFFÉRENTE de celle choisie pour {QID_sibling, …}`
- Empêche GPT de retourner la même option pour deux colonnes d'une même matrice transposée
- Ne prescrit aucune valeur spécifique : GPT garde le choix, contraint seulement à la différence
Patterns exclus :
- Blocs avec options différentes → pas de sibling détecté, règle non injectée
- `itype != radio` → non concerné
- RÈGLE TABLEAU RADIO HOMOGÈNE (seuil ≥8) → non modifiée, reste indépendante

---

## PLATEFORME : RESEARCHNOW / SURVEYMYOPINION — AUTOSCREENER RADIO
Signature DOM : surveymyopinion.researchnow.com — page screener AngularJS.
`[ng-controller*="autoScreenerController"]` > `div.parameter-rendered.single_select.tooBigForDropdown`
> `div.questionAndAnswerWrap` > `div.questionText.ng-binding` + `div.answers > div.answer-wrapper`
> `label > input[type=radio][id][name][value] + span.ng-binding`
Log discriminant : `[DOM_RESEARCHNOW_AUTOSCREENER] extracted N radio block(s)`

### _extract_researchnow_autoscreener_radio_blocks
Fichier : Survey/dom_extractors_misc.py
Enregistré dans : dom_analyzer.py (step 0i-octies, retour immédiat si match — avant le pipeline générique radio)
Guard (double) :
  1. `[ng-controller*="autoScreenerController"]` présent dans le DOM
  2. `div.parameter-rendered.single_select.tooBigForDropdown` présent
Patterns couverts :
- Question : `div.questionText` (`textContent`)
- Options : `label span.ng-binding` (`textContent`) dans chaque `div.answer-wrapper`
- `option_xpath_map` ancré sur `input[@type='radio'][@id][@name][@value]` (triple attribut) — discriminant fort par option
- `group_key = researchnow_autoscreener:radio:{norm_key(question)}`
- Flag payload : `researchnow_autoscreener_radio=True` ; itype=radio, max_select=min_select=1
- 1 bloc par `div.parameter-rendered` (N questions sur même page → N blocs)
- Dispatch : chemin générique `opt_map` (aucun bloc dispatcher spécifique)
Problème résolu : les N inputs radio ont des `name` différents (31, 33, 35…) → le pipeline générique
crée 1 groupe par `name`, soit N blocs distincts au lieu de 1. Ce extracteur opère sur
`div.questionAndAnswerWrap` pour regrouper toutes les options en 1 seul bloc.
Patterns exclus :
- `div.parameter-rendered` sans class `single_select.tooBigForDropdown` → pipeline générique
- Pages ResearchNow sans `ng-controller*="autoScreenerController"` → pipeline générique

---

## PLATEFORME : DECIPHER / YOURSURVEYNOW — PAGE D'ERREUR APPLICATIVE
Signature DOM : `div.survey-error` visible contenant un message d'erreur serveur
(ex. "ERROR: SE-03 Variable 'psid' required for list='101'").
Apparaît quand le lien de suivi est invalide ou expiré (paramètre psid manquant).

### Détection div.survey-error — survey_solver.py (route prod)
Fichier : Survey/survey_solver.py
Emplacement : boucle solve_full_survey, après le bloc errorPage/errorpage-wrapper,
avant l'appel à execute_survey_page().
Guard primaire : `div.survey-error` visible (is_displayed()) dans le DOM courant.
Guard secondaire (anti faux-positif, validé en prod) : `_has_actionable_q` — présence d'au
moins un `div.question input[type='radio']` ou `div.question input[type='checkbox']` dans le DOM.
- Si `_has_actionable_q` est vrai → le `div.survey-error` visible est un simple message de
  validation inline (ex. "Veuillez sélectionner au moins 1 réponse(s)") ; la page continue
  normalement dans le pipeline, pas de log, pas de restart.
- Si `_has_actionable_q` est faux → vraie page d'erreur applicative bloquante :
  détecte et logue URL courante + texte du premier élément (tronqué 200 chars),
  déclenche guard.record_success() + guard.request_survey_restart("decipher_survey_error").
Patterns exclus :
- div.errorPage, div.errorpage-wrapper → bloc précédent inchangé
- Pages Decipher avec questions valides (avec ou sans message de validation inline) →
  jamais de restart tant qu'un input radio/checkbox exploitable est présent

### Détection div.survey-error — main.py (route attach)
Fichier : main.py
Emplacement : boucle run_attach_takeover, après le bloc errorPage/errorpage-wrapper,
avant l'appel à execute_survey_page().
État : guard DOM identique à l'ancienne version de survey_solver.py — ne dispose PAS encore
du guard secondaire `_has_actionable_q` validé côté prod. Reste donc sujet au même faux-positif
(message de validation inline confondu avec une page d'erreur bloquante) tant que le patch n'y
est pas répliqué.
Patterns couverts :
- Logue via print : [PLATFORM-ERR] step + url + texte (tronqué 200 chars) → break
Patterns exclus :
- Identiques à l'ancienne version de la route prod ci-dessus (avant guard secondaire)

---

## PLATEFORME : BULBSHARE
Signature DOM : `my.bulbshare.com` — `div.pollItemWrap` > `div.css-jp04m` > `h2.pollItemTitle`
+ `div.itemRulesWrapper` (instruction) + `div.css-gos33m` > `button.css-12slb6h` (options).
Boutons UI : `button[data-survey-progress]` (barre 0%) et `button[data-survey-bulbshare]` (branding).

### Filtre btn_like — data-survey-progress / data-survey-bulbshare
Fichier : Survey/dom_analyzer.py
Emplacement : boucle `for b in btn_like`, après filtre interview-footer, avant Decipher cardrating.
Guard : `b.get_attribute("data-survey-progress") is not None` OU `b.get_attribute("data-survey-bulbshare") is not None`
Patterns couverts :
- Bouton "0%" (`data-survey-progress="true"`) dans le footer Bulbshare → exclu (non réponse)
- Bouton "Powered by Bulbshare" (`data-survey-bulbshare="true"`) → exclu (non réponse)
- Sans ce filtre : ces 2 boutons formaient un 2e bloc parasite avec question polluée
Patterns exclus :
- Tout bouton sans ces attributs → pipeline inchangé

### Patch question btn_groups — h2.pollItemTitle dans .pollItemWrap
Fichier : Survey/dom_analyzer.py
Emplacement : boucle btn_groups, section résolution question, après le guard interview-layout.
Guard (double) :
  1. `cont.closest('.pollItemWrap')` retourne un élément non null
  2. `h2.pollItemTitle` existe dans ce `.pollItemWrap`
Patterns couverts :
- Question depuis `h2.pollItemTitle` (textContent)
- Instruction depuis `div.itemRulesWrapper` (textContent, si non vide) — concaténée à la question
- Résultat final : "Quel type de voiture conduisez-vous ? Required / Choose at least 1 answer"
- Sans ce patch : `_extract_question_from_container` sur `div.css-gos33m` ne trouve que les options
  → question vide ou tronquée via `_find_question_text_near_element`
Log discriminant : `[DOM_BUTTON_GROUP] bulbshare_pollItemWrap recovered: …`
Patterns exclus :
- Conteneurs hors `.pollItemWrap` → chemins existants inchangés
- `.pollItemWrap` sans `h2.pollItemTitle` → chemin existant inchangé

---

## PLATEFORME : QUALTRICS — MATRIX-TE MULTI-TEXT
Signature DOM : `div.QuestionOuter.BorderColor.Matrix.mf` (outer a les deux classes `Matrix` ET `mf`)
+ `div.Inner.BorderColor.TE` + `fieldset` > `table.ChoiceStructure` > N `tr.ChoiceRow`
chacun portant `input[type='text'][name^='QR~QID{N}~{row}~1~TEXT']`.
Plateforme observée : surveys.ipsossay.com (Ipsos KnowledgePanel, Qualtrics hébergé).
Distingué de `div.Inner.FORM` (layout horizontal → `_extract_qualtrics_form_multi_text_blocks`).

### _extract_qualtrics_te_matrix_multi_text_blocks
Fichier : Survey/dom_extractors_misc.py
Enregistré dans : dom_analyzer.py (step 0h-bis-3f), après `_extract_qualtrics_form_multi_text_blocks`.
Pose `_qualtrics_page = True` → le `return` anticipé à la fin du bloc Qualtrics supprime
implicitement les blocs checkbox singleton co-localisés (ex. QID315 "Je n'en connais aucun")
qui auraient été produits par `_extract_qualtrics_choice_structure_checkbox_blocks` (step 0h-bis-3b).
Guard (additif) :
  1. `div.QuestionOuter.Matrix.mf` présent
  2. `div.Inner.TE` présent dans ce QuestionOuter
  3. `div.Inner.FORM` absent (sinon → `_extract_qualtrics_form_multi_text_blocks`)
  4. ≥2 `input[type='text'][name^='QR~']` dans `table.ChoiceStructure tbody tr.ChoiceRow td`
Patterns couverts :
- N cases texte libres (saisie de marques, apps, etc.) dans une grille Qualtrics Matrix-TE
- Question depuis `fieldset legend label.QuestionText` (ou fallback `label.QuestionText`, `div.QuestionText`)
- `name_prefix` = name du premier input ; `fields_count` = N ; `min_select=1`, `max_select=N`
- Flag payload : `qualtrics_te_matrix_multi_text=True`
- Log discriminant : `[DOM_QUALTRICS_TE_MATRIX_MULTI_TEXT] blocks_extracted=N`
Patterns exclus :
- `div.Inner.FORM` présent → `_extract_qualtrics_form_multi_text_blocks`
- `div.Inner.SL` (1 input seul) → `_extract_qualtrics_sl_text_blocks`
- Matrices radio/checkbox Qualtrics → `_extract_qualtrics_choice_structure_radio/checkbox_blocks`

---

## PLATEFORME : QUALTRICS — MATRIX RADIO BANKEDSA (Ipsos KnowledgePanel)
Signature DOM : `div.QuestionOuter.Matrix.mf` + `fieldset` contenant une `table.ChoiceStructure`
avec `display:none` (masquée par `CS_BankedSA()`) et une `div.customChoice` > `div.bankedrow`
générée dynamiquement par le JS Qualtrics. Observé sur surveys.ipsossay.com.
Les radios sont présents dans `table.ChoiceStructure tbody tr.ChoiceRow` (non visible à l'écran
mais accessibles via Selenium). La question globale est dans `caption.QuestionText` (enfant direct
de la table) ou dans `fieldset > legend > label.QuestionText`.

### _extract_table_matrix_radio_rows — patch BankedSA matrix_question
Fichier : Survey/dom_extractors_misc.py
Emplacement : après les 3 tentatives existantes de résolution de matrix_question
(aria-label, askia-caption), avant le bloc _is_confirmit.
Guard : `not matrix_question` — s'active uniquement si toutes les tentatives précédentes ont échoué.
Patterns couverts :
- Tentative 1 : `caption.QuestionText` ou `caption[class*='QuestionText']` enfant direct de la table
  → `table.find_element(By.CSS_SELECTOR, ...)` + `.text` / `.innerText`
  → log discriminant : `[TABLE_MATRIX] bankedsa_caption matrix_question=…`
- Tentative 2 : remontée JS jusqu'à `FIELDSET` ancêtre (max 8 niveaux) puis
  `legend label.QuestionText, legend .QuestionText` → `innerText`
  → log discriminant : `[TABLE_MATRIX] bankedsa_fieldset_legend matrix_question=…`
Patterns exclus :
- Tables avec `display:block` (couvertes par `_find_question_text_near_element`)
- `div.cm-simple-grid__table` → _extract_cmix_simple_grid_question_blocks
- confirmit-grid → bloc _is_confirmit existant (inchangé)

---

## PLATEFORME : QUALTRICS — MATRIX.MF BANKEDSA SINGLE-ROW (Ipsos KnowledgePanel)
Signature DOM : `div.QuestionOuter.Matrix.mf` + `div.customChoice` (injecté par `CS_BankedSA()`)
+ `table.ChoiceStructure` avec `display:none` contenant exactement **1 tr.ChoiceRow** en tbody.
Tous les radios de cette ligne partagent le même `name` (`QR~QIDn~1`).
Distingué du cas multi-lignes BankedSA (≥2 ChoiceRow) géré par `_extract_qualtrics_choice_structure_radio_blocks`
dont la branche Likert exige `len(set(row_names)) >= 2` — silencieusement sauté pour 1 seule ligne.
Observé sur surveys.ipsossay.com (Ipsos KnowledgePanel).

### _extract_qualtrics_bankedsa_single_row_radio_blocks
Fichier : Survey/dom_extractors_misc.py
Enregistré dans : dom_analyzer.py (step 0h-bis-3g), après `_extract_qualtrics_te_matrix_multi_text_blocks`.
Pose `_qualtrics_page = True` → déclenche le `return` anticipé du bloc Qualtrics.
Guard (additif, tous requis) :
  1. `div.QuestionOuter.Matrix.mf` présent
  2. `div.customChoice` présent dans ce conteneur (signal CS_BankedSA rendu)
  3. `table.ChoiceStructure > tbody > tr.ChoiceRow` : exactement 1 ligne
  4. ≥2 `input[type='radio'][name^='QR~']` dans cette ligne, tous avec le même `name`
Patterns couverts :
- Question depuis `fieldset legend label.QuestionText`, `legend .QuestionText`, `caption.QuestionText`, `div.QuestionText` (priorité décroissante)
- Options depuis `thead tr.Answers th.Selection span.LabelWrapper span` ; fallback : `value` des radios
- `group_key` = `radio:name:{group_name.lower()}` ; flags payload : `qualtrics_choice_structure_radio=True`, `qualtrics_bankedsa_single_row=True`
- Log discriminant : `[DOM_QUALTRICS_BANKEDSA_SINGLE_ROW] extracted 1 radio block: question=… options=…`
Patterns exclus :
- ≥2 ChoiceRow dans tbody → `_extract_qualtrics_choice_structure_radio_blocks` (branche Likert multi-lignes)
- Absence de `div.customChoice` → extracteurs Qualtrics standards inchangés
- `div.Inner.TE` → `_extract_qualtrics_te_matrix_multi_text_blocks`

---

## PLATEFORME : QUESTMINDSHARE CHATBOT
Signature DOM : SPA React/Next.js (insights.questmindshare.com)
DOM cumulatif : l'historique de chat reste dans le DOM sur toutes les pages — le message
d'accueil "Bienvenue et merci de votre participation !" est présent à chaque step.
Options : div[data-testid^="option-"][tabindex="0"] (sans role="button", sans input natif, sans classe spécifique).

### is_start_screen — hard guard QuestMindshare
Fichier : Survey/dom_classifier.py
Emplacement : début de is_start_screen, après le check du mot-clé "bienvenue", avant real_inputs_count.
Guard : div[data-testid="message-container"] présent ET (≥1 div[data-testid^="option-"] visible OU div[data-testid="instructions"] présent)
Patterns couverts :
- DOM chatbot cumulatif où "bienvenue" reste dans l'historique : sans ce guard, is_start_screen retournait True sur chaque step
- Retourne False immédiatement si QuestMindshare actif
- Log : "is_start_screen: QuestMindshare message-container + options/instructions actifs => pas un start_screen"
Patterns exclus :
- Pages sans div[data-testid="message-container"] → guard inactif

### _has_visible_answerables — section 5 QuestMindshare
Fichier : Survey/dom_classifier.py
Emplacement : bloc JS de _has_visible_answerables, après section 4 (roleButtons), avant return false.
Guard : div[data-testid^="option-"][tabindex="0"] visibles, seuil ≥ 2
Patterns couverts :
- Options QuestMindshare sans role="button", sans input natif, ignorées par les sections 1–4
- Seuil 2 éléments visibles minimum (anti-faux-positifs)
- Sans ce guard : is_end_screen retournait True car _has_visible_answerables retournait False
Patterns exclus :
- Éléments div[data-testid^="option-"] non visibles ou < 2 visibles
---

## PLATEFORME : ELEMENT HUMAN (SurveyJS classic, framework sv_*)
Signature DOM : `sv_main sv_default_css` (framework SurveyJS classique), questions dans
`div.sv_q.sv_qstn` / `[id^="sq_"]`, choix unique via `fieldset.sv_qcbc[role="radiogroup"]`
+ `input.sv_q_radiogroup_control_item[type="radio"]`.
Domaine observé : activityv2.elementhuman.com

### _is_formal_survey_question_page — hard guard SurveyJS classic (sv_qcbc radiogroup)
Fichier : Survey/dom_classifier.py
Emplacement : bloc JS de _is_formal_survey_question_page, après le guard Toluna/Confirmit
Wix natif, avant le `return false` final.
Guard (tous requis) :
  1. `fieldset.sv_qcbc[role="radiogroup"]` présent
  2. ≥2 `input.sv_q_radiogroup_control_item[type="radio"]` dans ce fieldset
  3. ≥2 de ces radios ont un `aria-label` texte exploitable (>3 caractères après suppression des balises HTML)
  4. Le conteneur question (`.sv_q.sv_qstn` ou `[id^="sq_"]` ancêtre du fieldset) contient un
     bloc de validation obligatoire (`.sv_q_erbox` ou `[role="alert"]`)
Patterns couverts :
- Question de sondage SurveyJS à choix unique dont le libellé ou le `name` du champ contient
  un vocabulaire de consentement (ex. question "consentez-vous...", `name="consent_0_sq_101"`)
  — sans ce guard, `is_consent_screen` classait ces pages comme écran de consentement bloquant
  (faux positif déclenché par le mot "consent" dans le texte/attributs, alors qu'il s'agit d'une
  question de contenu du sondage, pas d'un bandeau cookie/RGPD)
- Le bloc de validation obligatoire est utilisé comme signal discriminant : une vraie question
  de sondage impose une réponse (message d'erreur affiché tant qu'aucune option n'est cochée),
  contrairement à un simple écran d'info
Patterns exclus :
- Pages SurveyJS sans `fieldset.sv_qcbc[role="radiogroup"]` → guard inactif
- Fieldset avec <2 radios ou <2 aria-label texte exploitables → non couvert
- Absence de bloc de validation (`.sv_q_erbox`/`[role="alert"]`) dans le conteneur question →
  non couvert (évite de qualifier une page d'info SurveyJS sans contrainte de réponse comme
  question formelle)

---

## PLATEFORME : DECIPHER — ATMRATING (sq-atmrating, boutons 1..N sur inputs text cachés)
Signature DOM : `div.question.sq-atmrating.hasRows` > N `div.sq-atmrating-container`
Chaque container : `div.sq-atmrating-row-legend` (texte sous-question) + `input[type="text" name="ans{Q}.0.{N}"]` (caché) + `div.atmrating_input > span.atmrating-btn` (1..5, cliquables).
Domaine observé : survey.researchresults.com (Decipher hébergé).
Les inputs `type=text` sont CSS-masqués → rejetés systématiquement par `[SINGLES_SKIP] not_actionable_visible` du pipeline générique.

### _extract_decipher_atmrating_blocks
Fichier : Survey/dom_extractors_decipher.py
Enregistré dans : dom_analyzer.py (step 0i-septies-bis), après `_extract_decipher_ranksort_dropdown_blocks`, retour immédiat si match.
Guard (double) :
  1. `div.question.sq-atmrating` présent dans le DOM
  2. contient au moins un `div.sq-atmrating-container span.atmrating-btn`
Patterns couverts :
- N blocs `itype=radio`, un par `div.sq-atmrating-container`
- Question = `h1.question-text` + `h2.instruction-text` + `" - "` + `div.sq-atmrating-row-legend`
- Options = valeurs des `span.atmrating-btn` (ex: ["1","2","3","4","5"]) — nettoyées des zero-width spaces (`\u200b`)
- XPath ancré sur `input[@id]` du container → `span.atmrating-btn` à la position N (1-based)
- `group_key = radio:atmrating:{inp_name}` ; flag payload : `decipher_atmrating=True`
- Log discriminant : `[DOM_DECIPHER_ATMRATING] blocks_extracted=N`
Patterns exclus :
- `div.question.sq-ranksort` → `_extract_decipher_ranksort_dropdown_blocks`
- Autres questions Decipher answers-list → `_extract_focusvision_answers_list_groups`

---

## PLATEFORME : DECIPHER / FOCUSVISION — ANSWERS-LIST CHECKBOX (fir-hidden)
Signature DOM : `div.answers.answers-list` > N `div.element.clickableCell`
Chaque cellule : `input[type="checkbox"].fir-hidden` (CSS-masqué) + `span.fir-icon` + `label[for=inputId]`
Signal de sélection : `span.fir-icon.selected` dans la cellule (PAS `input.checked` — non mis à jour par Decipher).
Domaine observé : selfserve Nielsen (selfserve/540/…), extrait par `_extract_focusvision_answers_list_groups`.
Flag payload : `focusvision_answers_list=True`.

### _apply_by_target_id — bloc decipher_clickable_cell checkbox
Fichier : Survey/action_dispatcher.py
Emplacement : bloc `if resolved_itype == "checkbox":`, après idempotence, avant Toluna Runtime.
Guard (double) :
  1. `el.evaluate_handle(closest('.clickableCell')).as_element()` retourne un ElementHandle non-null
  2. `cell.querySelector("input[type='checkbox'].fir-hidden")` présent dans la cellule
Patterns couverts :
- Extraction de l'input id depuis le XPath via `re.search(r'@id=["\']([^"\']+)["\']', xp)`
- Clic via `label[for=inp_id].click()` JS — déclenche les handlers natifs Decipher (fir-icon toggle)
- Fallback si label absent : `_click_candidate(decipher_cell, "decipher_clickable_cell")`
- Vérification post-clic via `document.getElementById(inp_id)` (DOM frais, jamais stale) :
  `inp.checked` OU `cell.querySelector('.fir-icon').classList.contains('selected')`
- Fallback vérif si pas d'id extractible : `_is_decipher_mx_collapsible_checkbox_selected(decipher_cell)`
Note DOM critique : `_click_candidate` sur `.clickableCell` est inopérant (pas d'event listener JS sur ce wrapper).
  Le clic doit impérativement cibler `label[for=id]` pour activer les handlers Decipher.
  Le handle `decipher_cell` peut être stale après clic (re-render DOM) — vérification via getElementById obligatoire.
Patterns exclus :
- `div.mx-stage .mx-collapsible-container` présent → `_is_decipher_mx_collapsible_checkbox_selected` (branche MX)
- `input[type='radio']` → chemin radio distinct (`decipher_radio_clickable_cell`, cf. ci-dessous)
- Inputs natifs interactables (non fir-hidden) → chemin générique `_click_candidate`

### _apply_by_target_id — bloc decipher_radio_clickable_cell (radio)
Fichier : Survey/action_dispatcher.py
Emplacement : bloc `if resolved_itype == "radio":`, juste après le bloc checkbox `decipher_clickable_cell` ci-dessus.
Guard : `el.closest('.clickableCell')` non-null ET `cell.querySelector("input[type='radio'].fir-hidden")` présent.
Problème résolu : même layout answers-list que la variante checkbox (input natif masqué CSS dans `.clickableCell`, label sibling), mais en `itype=radio`. Avant ce patch, aucun bloc dispatcher dédié ne couvrait ce cas : le clic retombait sur le chemin générique `radio_main`, dont `_first_input_under()` ne trouve pas l'input (masqué), et qui rapportait malgré tout `apply ok=true` sans vérification DOM fiable — l'option réellement sélectionnée restait alors celle par défaut (1ère de la liste) au lieu de la valeur demandée.
Patterns couverts :
- Extraction de l'input id depuis le XPath via `re.search(r'@id=["\']([^"\']+)["\']', xp)`
- Clic via `label[for=inp_id].click()` JS — déclenche les handlers natifs Decipher (fir-icon toggle), même logique que la variante checkbox
- Fallback si label absent : `_click_candidate(decipher_radio_cell, "decipher_radio_clickable_cell")`
- Vérification post-clic via `document.getElementById(inp_id)` (DOM frais) : `inp.checked` OU `cell.querySelector('.fir-icon').classList.contains('selected')`
- Aucun succès rapporté sans que cette vérification passe (pas de faux positif comme avec `radio_main`)
Patterns exclus :
- `input[type='checkbox'].fir-hidden` → bloc `decipher_clickable_cell` (checkbox, ci-dessus)
- Inputs natifs interactables (non fir-hidden) → chemin générique `_click_candidate` / `radio_main`

---

## PLATEFORME : ALCHEMER / SURVEYGIZMO (sg-question, table.sg-table)
Signature DOM : conteneur `div.sg-question-*` / `table.sg-table`, inputs `id`/`name` de forme `sgE-{surveyId}-{...}`.
Cas observé : liste de champs texte ("un item par case", ex. rappel de marques), question et instruction de saisie
concaténées dans le même bloc texte (ex. `<div>...question...?<br><em>Please enter one brand per box...</em></div>`).

### Filtre "validation_instruction" — exception chaîne mixte question+instruction
Fichier : Survey/dom_analyzer.py
Emplacement : bloc `# --- [PATCH SSI/Confirmit] Filtrer les instructions de validation ---`, branche
`elif _is_validation_instruction(question):`, avant le `continue` / log `[SINGLES_SKIP] validation_instruction`.
Guard : `"?" in question` sur la chaîne concaténée déjà classée `_is_validation_instruction=True`.
Patterns couverts :
- Chaînes mixtes où un intitulé de question réel (terminé par `?`) précède une instruction de saisie
  (ex. "Please enter one brand per box...") — la présence de `?` signale un contenu question exploitable,
  le rejet total est annulé (`pass` au lieu de `continue`).
- Sans ce guard : les N inputs texte de la liste étaient tous skippés (`[SINGLES_SKIP] validation_instruction`),
  puis `[DOM_ONLY_ABORT] detector_no_match` → page entière non extraite, clic CTA "Next" sans réponse saisie.
Patterns exclus :
- Chaînes composées uniquement d'une instruction de validation, sans `?` (aucun contenu question détectable)
  → rejet maintenu via `continue` (comportement inchangé).
- Ne couvre pas les cas où l'instruction elle-même contiendrait un `?` sans question réelle associée
  (non rencontré à ce jour — signal `?` traité comme heuristique généraliste, pas un cas Alchemer-only).

### _extract_table_matrix_radio_rows — guard table sge_like masquée + dédup exact
Fichier : Survey/dom_extractors_misc.py (guard), Survey/dom_analyzer.py (consommation `table_matrix_sge_exact_names`)
Cas observé : page à questions séquentielles dans le même DOM (ex. Q18 NPS actif + Q19 matrice
sge_like 13 lignes/5 colonnes encore `display:none`, révélée plus tard sans rechargement).
Guard : `getBoundingClientRect()` de la table + siblings du parent. Si aucune surface visible :
`_is_hidden_dedup_only=True` — la branche `sge_like_matrix` émet alors un bloc marqueur
`{"_sge_dedup_only": True, "sge_row_names": [...]}` (noms EXACTS des radios, pas le préfixe)
au lieu du bloc `itype=matrix` envoyé à l'IA. `dom_analyzer.py` route ce marqueur vers
`table_matrix_sge_exact_names` (jamais dans `question_blocks`), utilisé plus loin pour bloquer
le pipeline générique radio-par-name sur ces mêmes lignes.
Patterns couverts :
- Table sge_like visible → bloc `itype=matrix` unique inchangé (comportement historique)
- Table sge_like masquée sans contexte visible → 0 bloc exposé à l'IA, mais ses noms de radio
  exacts bloquent le groupement générique (évite les 13 blocs radio "row: col" parasites)
Patterns exclus :
- Noms EXACTS utilisés (pas le préfixe `sge-<survey>-<qid>`) pour ne pas bloquer une autre
  question sge_like du même préfixe sur la même page (ex. Q18 NPS)
- Table sge_like visible avec sibling visible dans le parent (ex. BankedSA customChoice) →
  jamais marquée dedup-only, chemin normal inchangé

### _extract_alchemer_sg_table_checkbox_matrix_block
Fichier : Survey/dom_extractors_misc.py (extraction), Survey/dom_analyzer.py (appel avant le
pipeline générique, ajout des préfixes de ligne à `table_matrix_sge_prefixes` pour bloquer le
groupement générique checkbox par name).
Cas observé : matrice checkbox Alchemer/SurveyGizmo (`fieldset.sg-question.sg-type-table.sg-type-table-checkbox`
> `table.sg-table`), lignes = attributs, colonnes = marques, chaque cellule un `<input type="checkbox">`
avec un `name` unique par cellule (`sge-<surveyId>-<pageId>-<rowId>-<colId>`, 4 groupes de chiffres).
Sans ce guard : `_extract_table_matrix_radio_rows` ne matche que `input[type='radio']` par ligne,
donc cette matrice checkbox lui est invisible ; elle retombait dans le pipeline générique checkbox
par name (chaque cellule = un name unique = un groupe), produisant un bloc `itype=checkbox` par
cellule (N lignes × M colonnes blocs), chacun avec `question` polluée par la concaténation de
toute la table (texte de tous les libellés de lignes et colonnes) via `_find_question_text_near_element`.
Guard : `fieldset.sg-type-table-checkbox` (sélecteur CSS strict, distinct de `sg-type-table-radio`
implicitement couvert par l'extracteur radio existant)
Patterns couverts :
- Question de la matrice lue depuis `legend` du fieldset (nettoyage numéro + mention required),
  jamais depuis `_find_question_text_near_element` (évite la pollution par le contenu de la table)
- Par ligne (`tbody tr` avec `th.sg-first-cell` pour le libellé) : 1 bloc `itype=checkbox` avec
  toutes les colonnes comme options, `question` = "{question matrice} | {libellé ligne}"
- Guard supplémentaire par ligne : le `name` du premier checkbox doit matcher `^sge-\d+-\d+-\d+-\d+$`
- `option_xpath_map` ancré sur `input[@id]` (1 XPath par colonne), consommé par le dispatcher
  via le chemin générique `option_xpath_map` (pas de stratégie de clic dédiée nécessaire)
- `sge_row_name_prefix` (préfixe `sge-N-N-N` sans le dernier `-colId`) exposé dans `context`,
  consommé par `dom_analyzer.py` pour peupler `table_matrix_sge_prefixes` et bloquer le
  groupement générique checkbox sur ces mêmes lignes (même mécanisme que `table_matrix_sge`
  pour les matrices radio cachées)
Patterns exclus :
- Matrices radio du même type de page (`sg-type-table-radio` ou équivalent) → `_extract_table_matrix_radio_rows`
  / branche `sge_like_matrix` existante, non modifiée
- Lignes avec moins de 2 checkboxes, ou name ne matchant pas le pattern à 4 groupes de chiffres
  → ligne ignorée (pas de bloc émis pour cette ligne)

### _extract_alchemer_rank_dragdrop_block
Fichier : Survey/dom_extractors_misc.py (extraction), Survey/dom_analyzer.py (étape 0i-nonies,
appel avant le pipeline générique singles), Survey/action_dispatcher.py (bloc dispatcher
`payload.get("alchemer_rank_dragdrop") and resolved_itype == "checkbox"`).
Cas observé : question de classement drag-and-drop (liste origine → liste classée), ex.
"sélectionnez vos 3 aspects les plus importants". Chaque item de `ul[id$='-origin'] > li`
contient un `<input type="text" aria-hidden="true">` (helper clavier) + `<label>` ; sans
ce guard, le pipeline générique singles captait chacun de ces inputs cachés comme une
question texte indépendante → N blocs fragmentés et incomplets au lieu d'un seul bloc.
Guard : `div.sg-question.sg-type-rank.sg-type-rank-dragdrop`
Patterns couverts :
- Texte de la question depuis `div.sg-question-title` (nettoyage numéro + `*` + mention required)
- Items depuis `ul[id$='-origin'] > li` (label + input `[type=text][aria-hidden='true']`), un seul
  bloc `itype=checkbox` avec toutes les options, dans l'ordre DOM d'origine
- `min_select`/`max_select` = `minimum_response` lu dans `window.SGAPI.surveyData[surveyId].questions[questionId].properties` (fallback 1 si absent)
- Registry : `item_input_map` (label normalisé → id de l'input caché) consommé par le dispatcher
- Dispatcher : pour chaque item choisi par l'IA, résout l'input caché via `item_input_map`
  (lookup direct puis fuzzy), fixe `input.value = ordinal` (position 1-based dans le plan de
  réponse) + dispatch `input`/`change` — pas de drag-and-drop simulé, Alchemer accepte la
  valeur numérique directement sur l'input caché sortable
- Compteur d'ordinal par question (`driver._alchemer_rank_dragdrop_counts` / `_ordinal`), réinitialisé à chaque nouveau plan
Patterns exclus :
- Ranking Alchemer non drag-drop (autres `sg-type-rank-*` non rencontrés à ce jour)
- Tables sge_like matricielles → `_extract_table_matrix_radio_rows`

### _try_table_matrix_sge_set (dispatcher)
Fichier : Survey/action_dispatcher.py
Appelé depuis : bloc matrix_intent, après _try_gridclick_matrix_set, avant le fallback visuel
(dom_context_mapper) et le fallback générique click_matrix_cell_by_row_and_col.
Guard : target_payload marqué table_matrix_sge (racine ou context), row_label et col_label non vides.
Patterns couverts :
- Localisation des `<tr>` contenant des radios `@name` via XPath explicitement préfixé "xpath="
  (`driver.query_selector_all("xpath=//tr[...]")`) — sans ce préfixe la requête ne matche aucune
  ligne sur ce driver (convention obligatoire, cf. Survey/input_matrix.py).
- Matching de ligne : `tr.querySelector('th, td')` comparé à row_label normalisé.
- Matching de colonne : égalité stricte (pas de sous-chaîne) sur `aria-label` normalisé des radios
  de la ligne matchée — une comparaison par sous-chaîne confond "Agree"/"Disagree" ou
  "Agree"/"Strongly Agree".
- Transmission d'éléments DOM à `evaluate()` : toujours appeler `.evaluate(fn, arg)` directement sur
  le handle d'élément concerné (ex. `row.evaluate(fn, col_need)`), jamais `driver.evaluate(fn, [handle, arg])`
  (un handle imbriqué dans une liste n'est pas résolu côté JS) ni `driver.evaluate(fn)` sans transmettre
  l'élément trouvé à l'étape précédente (sinon `_el` est `undefined` et le clic échoue silencieusement).
Patterns exclus :
- Pages sans flag table_matrix_sge sur le target_id → fonction retourne False immédiatement.

---

## UTILITAIRE : DROPDOWN BLOCK RESOLVER
Fichier : Survey/dropdown_block_resolver.py
Appelé depuis : Survey/action_dispatcher.py, bloc `itype == "dropdown"`, stratégie `dropdown_block` (avant `dropdown_select`).
Rôle : associer un contexte-question → bon `<select>` ou dropdown custom → sélectionner la valeur.

### _collect_dropdown_blocks
Guard : collecte en deux passes strictement séparées.
  Passe 1 — `<select>` natifs : `driver.query_selector_all("select")`, marqués `is_native=True`.
  Passe 2 — customs : `[role='combobox'], [aria-haspopup='listbox'], .dropdown, .select` avec
             exclusion immédiate de tout élément dont `tagName == "select"` (via `_el_is_select()`).
Patterns couverts :
- `<select>` natifs (Nielsen, Decipher, Forsta…) : options lues depuis `<option>` enfants
- Dropdowns custom ARIA (combobox, listbox) : options lues après ouverture
Patterns exclus :
- `<select class="... dropdown ...">` ne doit PAS être collecté deux fois :
  la classe CSS `.dropdown` peut matcher le sélecteur custom — `_el_is_select()` l'exclut.

### _extract_label — ordre de priorité
1. `aria-labelledby` → texte de l'élément référencé (robuste Nielsen/Decipher)
2. `label[for=id]` — recherche globale depuis la racine
3. `aria-label`, `placeholder`, `name` (scalaires)
4. texte parent immédiat — UNIQUEMENT pour les dropdowns custom (jamais pour `<select>`)
Note critique : `inner_text()` sur le parent d'un `<select>` retourne la concaténation de
toutes les options — ce label pollué fausse le score Jaccard. La branche 4 est donc
conditionnée à `not is_select`.

### Idempotence — _selected_text_native
Pour `is_native=True` : lit `options[selectedIndex].text` via `evaluate()`.
NE PAS appeler `inner_text()` sur un `<select>` : retourne le texte de toutes les options.
Pour custom (`is_native=False`) : `inner_text()` du trigger (texte visible affiché).

### Sélection natif
`select_option(label=value)` Playwright + dispatch events `input/change/blur`.
Fallback : fuzzy match sur `Array.from(el.options)` → `select_option(value=matched_value)`.

---

## DISPATCHER GÉNÉRIQUE : MULTI_TEXT (kind="multi_text") — DÉTECTION CHAMP VIDE

### _apply_by_target_id — bloc multi_text : lecture valeur DOM live
Fichier : Survey/action_dispatcher.py
Emplacement : bloc `if kind == "multi_text" ...`, boucle `for fld in fields`, juste avant `elx.clear()` / `elx.type()`.
Bug corrigé : `cur = elx.get_attribute("value")` lit l'attribut HTML statique, qui ne reflète pas la valeur réellement saisie via `.type()` sur le champ (reste vide après saisie) → le champ suivant du groupe était jugé "vide" à tort à chaque itération, donc toutes les valeurs retombaient sur le 1er champ, produisant une concaténation de toutes les réponses dans une seule case.
Fix : lecture de la propriété DOM live via `driver.evaluate("(e) => e.value", elx)` au lieu de `get_attribute("value")`.
Patterns couverts :
- Tout bloc `kind=multi_text` générique (N champs texte indépendants partageant un même target_id de groupe), quel que soit le fournisseur — confirmé sur FocusVision/Decipher (inputs `name="ansXXX.N.M"`, ex: question "marques de luxe" à 10 champs).
Patterns exclus :
- N'affecte pas les extracteurs Qualtrics dédiés (`_extract_qualtrics_form_multi_text_blocks`, `_extract_qualtrics_te_matrix_multi_text_blocks`), qui ont leur propre chemin d'extraction mais partagent ce même bloc d'application dans `_apply_by_target_id`.

---

## PARSER OPENAI : FALLBACK LIGNE BRUTE QID UNIQUE
Fichier : Survey/batch_response_parser.py
Fonction : `parse_batch_response`, bloc situé après la boucle principale de parsing (avant `_coerce_to_negative_frequency_option`).
Contexte : quand le batch ne contient qu'une seule question en attente, OpenAI répond parfois
par une valeur nue ("9") sans l'enveloppe `QID //// target_id //// valeur //// itype //// contexte`
et sans aucun séparateur `////`. Sans ce fallback, la ligne est ignorée : `received=0 final_count=0`,
aucune action générée, le champ reste vide et le bot bloque en attente de saisie.

### bare_single_qid_fallback
Guard : `constraints is not None`, exactement 1 QID de `constraints` encore sans action générée,
ET le `raw` contient exactement 1 ligne sans `////`.
Patterns couverts :
- N'importe quel `itype` (text, number, etc.) — pas restreint à `kind=multi_text`
  (généralisation de l'ancien `multi_text_bare_fallback`, qui ne couvrait que ce cas).
- Valeur récupérée depuis `qid_meta[qid]` (`target_id`, `itype`, `question`), split via `_split_values`
  (supporte le séparateur `|` si plusieurs segments).
Patterns exclus :
- Plusieurs QID encore sans réponse → fallback non déclenché (ambiguïté sur la question ciblée).
- Raw contenant plusieurs lignes sans `////` → fallback non déclenché (ambiguïté sur la ligne à utiliser).
- Toute ligne contenant déjà `////` → traitée par le parsing principal, hors scope de ce fallback.

---

## TRI POST-EXTRACTION : ORDRE DOM RÉEL + PROMOTION DU BLOC VISIBLE
Contexte : pages prescreener multi-fieldsets où plusieurs blocs coexistent dans le DOM
(ex. surveys.insights-today.com/v1/survey/prescreener) mais où un seul est visible/actionnable
à la fois. Sans ce tri, les blocs "groupe" (radio/checkbox) et les blocs "single" (text/dropdown)
étaient concaténés par famille de type selon l'ordre des passes d'extraction (tous les groupes
d'abord, puis tous les singles), et non selon leur position réelle dans le document — un bloc
single intercalé entre deux blocs groupe dans le DOM se retrouvait systématiquement rejeté en fin
de liste, provoquant une résolution dans le désordre (ex. la question d'âge en dropdown, positionnée
dans le DOM entre "genre" et "revenu du foyer", extraite en tout dernier).

### Tri par position DOM (`_block_dom_pos` + `sorted(...)`) — étape avant promote_visible_block
Fichier : Survey/dom_analyzer.py
Emplacement : juste avant le bloc `# Promote the currently visible/actionable block to position 0.`
Guard : `len(blocks) > 1` ; collecte JS unique (`driver.evaluate`) de tous les
`input:not([type="hidden"]), select, textarea` du document dans leur ordre DOM (`domIndex`),
mappés par `name`/`id` (premier index rencontré conservé).
Patterns couverts :
- Blocs `kind=single` : position = index DOM du `name`/`id` du champ (`context.name` / `context.id`)
- Blocs `kind=group` : position = index DOM du premier champ dont le `name` correspond au
  suffixe de `group_key` (ex. `radio:name:household_income` → `household_income`)
- Tri stable (`sorted` avec position d'origine en clé secondaire) : les blocs sans position
  résolue (`2**31`) gardent leur ordre relatif d'origine, jamais projetés arbitrairement en tête
- Ce tri s'applique sur la liste déjà extraite (post-extraction), aucune modification des
  extracteurs individuels
Patterns exclus :
- Pages à un seul bloc (`len(blocks) <= 1`) → tri non déclenché
- Blocs `kind` autre que `single`/`group` (ex. `multi_text`, `date`) → position non résolue,
  restent dans leur ordre relatif d'origine (fallback `2**31`)

### Promotion du bloc visible (`promote_visible_block`) — inchangé, exécuté après le tri
Fichier : Survey/dom_analyzer.py
Guard : signal `visible` de la même collecte JS — premier `fieldset` visible
(`display`/`visibility`/`opacity`/`getBoundingClientRect`) contenant un champ interactif visible.
Patterns couverts :
- Repêche le bloc correspondant au champ actuellement visible/actionnable et le place en
  position 0, après que le tri par position DOM a déjà remis le reste de la liste dans l'ordre
  réel du document — les deux mécanismes sont complémentaires, pas redondants
Patterns exclus :
- Aucun `fieldset` visible détecté (`visible=None`) → position 0 déterminée uniquement par le tri DOM

---

## PLATEFORME : DECIPHER / FOCUSVISION — WIDGET CARD RATING (sq-cardrating)
Signature DOM : `div.sq-cardrating-widget[data-uid]` avec bloc de configuration JS embarqué
(`cardrating:completion`, `rows`, `cols`, `title`) dans un `<script>` frère du widget.
Carrousel de cartes (une carte affichée à la fois, ex. logo de marque), avec un jeu unique de
3 boutons de réponse (`.sq-cardrating-button`) partagé et réutilisé à chaque étape ; défilement
automatique vers la carte suivante déclenché par le site après chaque sélection.
Le widget expose également une vue de contrôle/QA cachée (`div.sq-cardrating-qa-view`,
explicitement documentée "shown only when QA Codes are turned on"), non destinée à l'utilisateur
final, contenant une table listant tous les éléments (rows) avec des inputs radio réels.

### _extract_decipher_cardrating_blocks (nom de fonction à confirmer dans le code)
Fichier : Survey/dom_extractors_decipher.py
Enregistré dans : dom_analyzer.py (étape additive, avant/après le pipeline générique button_group
et avant l'extracteur générique de groupes answers-list/matrice — ordre à confirmer dans le diff)
Guard : présence de `div.sq-cardrating-widget[data-uid]` avec configuration `rows`/`cardrating:completion`
lisible dans le `<script>` associé.
Problème résolu :
1. Le pipeline générique `button_group` produisait un bloc unique dont l'intitulé de question était
   pollué par le texte du message de fin de séquence (`cardrating:completion`, ex. "C'est terminé !
   Veuillez cliquer sur le bouton « Continuer »..."), normalement affiché uniquement une fois toutes
   les cartes notées — ce message n'est pas une question et ne doit jamais être extrait.
2. L'extracteur générique de groupes answers-list/matrice (lecture de la vue QA cachée) ne produisait
   qu'un seul bloc fusionné pour l'ensemble des cartes (le libellé de chaque ligne, porté uniquement
   par une image `<img alt/title>` sans texte, n'était pas différencié), au lieu d'un bloc par carte.
Patterns couverts :
- Un bloc `itype=radio` par élément (row) listé dans la configuration DOM du widget au moment de
  l'extraction, avec la même question/mêmes options pour chacun, ancré sur le jeu unique de 3 boutons
  de réponse partagé (`.sq-cardrating-button`)
- `question` = intitulé réel de la question + instruction, suffixé par le libellé de l'élément
  courant (`cardrating_card_label`, dérivé de l'`alt`/`title` de l'image de la carte)
- `context.decipher_cardrating = True`, `cardrating_step_index`, `cardrating_total_steps`,
  `cardrating_card_label`, `cardrating_widget_uid`, `is_last_carousel_item`
- `group_key = decipher_cardrating:{uid}:card:{card_label normalisé}` (fallback `:step:{index}`
  si `card_label` vide) — garantit un target_id distinct par carte malgré la réutilisation du même
  jeu de boutons DOM, et surtout un target_id **stable** entre deux scans DOM successifs
- Le nombre d'éléments extraits reflète l'état courant de la configuration DOM au moment du scan
  (peut diminuer entre deux scans si le site retire les cartes déjà notées de sa configuration —
  confirmé en usage réel : 5 éléments au premier scan, 4 au scan suivant après une première sélection)

Correctif (clé par carte, pas par position) :
Problème résolu : `group_key` utilisait initialement `:step:{index}`, l'index de position parmi
les cartes *restantes*. Après application d'une réponse et retrait de la carte notée de la
configuration DOM, les cartes suivantes glissent d'un cran (`step:1` → `step:0`, etc.). Comme
`target_id = sha1(kind|group_key|question)`, ce glissement change le hash du target_id d'une carte
déjà pré-calculée par le batch parser (une seule extraction en amont produit tous les target_id
Q1..Qn), alors que la carte elle-même (et sa `question`, déjà suffixée par `card_label`) n'a pas
changé. Conséquence observée en prod : `_apply_by_target_id` échoue silencieusement (payload absent
du DOM_REGISTRY) dès la 2e étape du widget → fallback vers la chaîne générique radio
(`click_decipher_grid_radio_strict` puis `radio_main`), qui rapporte `apply ok=true` malgré un
`native_verify=ko` explicite juste avant (cf. entrée `_apply_by_target_id — cache de stratégie
gagnante` : même symptôme de fond que le faux positif `radio_main` déjà documenté, mais cause
différente — ici absence de resolution target_id, pas un défaut de `radio_main` lui-même).
Correction : clé par `card_label` (identité stable de la carte) au lieu de la position `step_i`.
Fallback conservé sur `step_i` si `card_label` est vide (évite une régression de garantie
d'unicité dans ce cas rare, au prix de la même instabilité qu'avant pour ce cas précis uniquement).
Patterns exclus :
- Bloc de message de fin (`cardrating:completion`) → jamais extrait, ni par cet extracteur ni par
  le pipeline générique button_group (guard négatif ajouté sur ce dernier)
- Bloc issu de la vue QA cachée (`div.sq-cardrating-qa-view`, group_key `radio:name:{uid}`) → supprimé
  par guard négatif additif dès lors que cet extracteur a produit des blocs pour le même `data-uid`
- Widgets `sq-cardrating` sans configuration `rows`/`cardrating:completion` lisible → non couverts,
  chemin générique inchangé

---

## FRONTIÈRES INTER-EXTRACTEURS

| Plateforme | Extracteur A | Extracteur B | Signal de discrimination |
|---|---|---|---|
| Askia | _extract_askia_adc_slider | _extract_askia_adc_responsive_table | class du div principal : `adc-slider` vs `adc-responsiveTable` |
| Askia | askia_responsive_table_checkbox (dispatcher) | chemin générique opt_map | flag `askia_responsive_table_checkbox` dans le payload |
| Confirmit | _extract_confirmit_cf_ranking_blocks | _extract_confirmit_cf_single/numeric/open | class `cf-question--ranking` sur le div parent |
| Toluna/Confirmit wix | _extract_confirmit_wix_rankedorderclick_block | _extract_confirmit_wix_fieldset_radio_block | classe `confirmit-rankedorderclick-default` présente sur le fieldset — discriminant prioritaire, indépendamment du type d'input (radio ou checkbox) |
| Toluna/Confirmit wix | _extract_confirmit_wix_checkbox_grid_blocks | _extract_confirmit_wix_fieldset_radio_block | `table.confirmit-grid` présente dans le fieldset (vs `table.confirmit-table`) |
| Toluna/Confirmit wix | _extract_confirmit_wix_fieldset_radio_block (pure-checkbox) | _extract_confirmit_wix_fieldset_radio_block (radio) | 0 radio + ≥2 checkboxes dans `table.confirmit-table` → itype=checkbox ; ≥2 radios → itype=radio |
| Kantar mrIWeb | _extract_kantar_rowpicker_radio_blocks | extracteur générique radio | flag `kantar_rowpicker_radio` dans le payload + guard dispatcher avant `_find_best_visible` |
| Prodege/Swagbucks | _extract_prodege_prescreener_radio_block | extracteur générique radio/checkbox | `div.profilerContainer` + `p.profilerQuestionText` (step 0i-sexies, retour immédiat si match) |
| Qualtrics TE | _extract_qualtrics_form_multi_text_blocks | _extract_qualtrics_sl_text_blocks | `div.Inner.FORM` + ≥2 inputs (multi-cases) vs `div.Inner.SL` + 1 input (champ unique) |
| Qualtrics TE | _extract_qualtrics_te_matrix_multi_text_blocks | _extract_qualtrics_form_multi_text_blocks | `div.QuestionOuter.Matrix.mf` + `div.Inner.TE` (sans `div.Inner.FORM`) vs `div.Inner.FORM` seul |
| ResearchNow | _extract_researchnow_autoscreener_radio_blocks | extracteur générique radio | `[ng-controller*="autoScreenerController"]` + `div.parameter-rendered.single_select.tooBigForDropdown` (step 0i-octies, retour immédiat si match) |
| Qualtrics BankedSA | _extract_table_matrix_radio_rows (patch caption/legend) | chemin _find_question_text_near_element | `table.ChoiceStructure` avec `display:none` + `caption.QuestionText` ou `fieldset > legend > .QuestionText` |
| Qualtrics BankedSA | _extract_qualtrics_bankedsa_single_row_radio_blocks | _extract_qualtrics_choice_structure_radio_blocks | `div.customChoice` présent + exactement 1 tr.ChoiceRow (single-row) vs ≥2 ChoiceRow même name (multi-lignes Likert) || Decipher | _extract_decipher_atmrating_blocks | extracteur générique singles/text | `div.question.sq-atmrating` + `div.sq-atmrating-container span.atmrating-btn` — inputs text cachés rejetés par pipeline générique |
| Decipher/FocusVision answers-list | decipher_radio_clickable_cell (dispatcher) | decipher_clickable_cell (dispatcher, checkbox) / radio_main générique | `input[type='radio'].fir-hidden` dans `.clickableCell` (vs `input[type='checkbox'].fir-hidden` pour la variante checkbox) — guard strict avant tout fallback générique |
| Decipher/FocusVision card rating | _extract_decipher_cardrating_blocks | button_group générique | `div.sq-cardrating-widget[data-uid]` avec config `rows`/`cardrating:completion` lisible — retour immédiat si match, guard négatif additif sur button_group pour ce widget |
| Decipher/FocusVision card rating | _extract_decipher_cardrating_blocks | extracteur générique answers-list/matrice (vue QA) | même `data-uid` de widget déjà couvert par `_extract_decipher_cardrating_blocks` → bloc `radio:name:{uid}` de la vue QA cachée supprimé par guard négatif additif |