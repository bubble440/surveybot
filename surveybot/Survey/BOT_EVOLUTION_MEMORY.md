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
Guard : `div.survey-error` visible (is_displayed()) dans le DOM courant.
Patterns couverts :
- Détecte et logue : URL courante + texte du premier élément (tronqué 200 chars)
- Déclenche guard.record_success() + guard.request_survey_restart("decipher_survey_error")
Patterns exclus :
- div.errorPage, div.errorpage-wrapper → bloc précédent inchangé
- Pages Decipher avec questions valides → aucun div.survey-error visible

### Détection div.survey-error — main.py (route attach)
Fichier : main.py
Emplacement : boucle run_attach_takeover, après le bloc errorPage/errorpage-wrapper,
avant l'appel à execute_survey_page().
Guard : même guard DOM que survey_solver.py
Patterns couverts :
- Logue via print : [PLATFORM-ERR] step + url + texte (tronqué 200 chars) → break
Patterns exclus :
- Identiques à la route prod ci-dessus

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