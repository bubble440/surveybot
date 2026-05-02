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
Guard : `fieldset[id^="fieldset_"]` contenant `table.confirmit-table` avec `input[type="radio"]`
Patterns couverts :
- Inputs radio masqués (`position:absolute; top:-9000px`) — non interactables via Selenium standard
- Question : `div[id$="_text"].question_text_ng` (ou class `statementfontdesktoplayout2014`)
- Labels : `td.answer_label_ng label[for=<radio_id>]` ou `td.alternating_answer_label_ng label[for=<radio_id>]`
- Clic : `<a href="javascript:void(0)">` dans la même `<td>` que l'input (XPath : `//input[@id=...]/ancestor::td[1]//a[1]`)
- Labels tronqués à 80 chars (`_LABEL_MAX=80`) pour garantir la correspondance même si le LLM abrège
Patterns exclus :
- Layouts Confirmit modernes (`cf-question--*`) → extracteurs cf_*
- Modals consentement (`#modal-container`, `.consent-form-radiogroup`) → `_extract_consent_modal_radio_block`
- Checkboxes consentement → `_extract_single_consent_checkbox_block`
- fieldset avec classe `confirmit-rankedorderclick-default` → `_extract_confirmit_wix_rankedorderclick_block`

### _extract_confirmit_wix_rankedorderclick_block
Fichier : Survey/dom_extractors_misc.py
Guard : `fieldset[id^="fieldset_"].confirmit-rankedorderclick-default`
Patterns couverts :
- Items : `td.confirmit-rankedorderclick[tabindex="0"]` avec `label[for=cq{N}_{M}]`
- Question : `div[id$="_text"].question_text_ng` ; instruction fusionnée dans `question_for_openai`
- `min_select` extrait depuis `div[id$="_error"].error_text` (pattern "fournir N réponses")
- itype produit : "checkbox" ; flag : `confirmit_wix_rankedorderclick=True`
- Labels tronqués à 80 chars (même convention que fieldset_radio_block)
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

### _apply_by_target_id — exclusion _skip_opt_map_for_cached_checkbox pour confirmit_wix_checkbox_grid
Fichier : Survey/action_dispatcher.py
Emplacement : calcul de `_skip_opt_map_for_cached_checkbox`, bloc `# --- cas "options map" (radio/checkbox)`.
Guard : condition `_skip_opt_map_for_cached_checkbox` ajoute `and not payload.get("confirmit_wix_checkbox_grid")`
Problème résolu : sans ce guard, dès que `checkbox_main` réussit sur la première option d'un bloc (E.leclerc — première occurrence dans le DOM), le cache l'enregistre sous `target_id`. Pour les options suivantes du même bloc et pour toutes les lignes suivantes, `_skip_opt_map_for_cached_checkbox=True` bypassait l'`option_xpath_map`, forçant `checkbox_main` à chercher le label en pleine page → fausse correspondance sur la première occurrence déjà cochée → `ok=True` silencieux, case non cochée.
Correction : `confirmit_wix_checkbox_grid` doit toujours passer par l'`option_xpath_map` (XPath scopé par `input[@id]`), jamais par `checkbox_main` seul.
Patterns exclus :
- Tous blocs checkbox sans `confirmit_wix_checkbox_grid` → comportement cache inchangé


## FRONTIÈRES INTER-EXTRACTEURS

| Plateforme | Extracteur A | Extracteur B | Signal de discrimination |
|---|---|---|---|
| Askia | _extract_askia_adc_slider | _extract_askia_adc_responsive_table | class du div principal : `adc-slider` vs `adc-responsiveTable` |
| Askia | askia_responsive_table_checkbox (dispatcher) | chemin générique opt_map | flag `askia_responsive_table_checkbox` dans le payload |
| Confirmit | _extract_confirmit_cf_ranking_blocks | _extract_confirmit_cf_single/numeric/open | class `cf-question--ranking` sur le div parent |
| Toluna/Confirmit wix | _extract_confirmit_wix_rankedorderclick_block | _extract_confirmit_wix_fieldset_radio_block | classe `confirmit-rankedorderclick-default` présente ou absente sur le fieldset |
| Toluna/Confirmit wix | _extract_confirmit_wix_checkbox_grid_blocks | _extract_confirmit_wix_fieldset_radio_block | `table.confirmit-grid` présente dans le fieldset (vs `table.confirmit-table`) |
| Kantar mrIWeb | _extract_kantar_rowpicker_radio_blocks | extracteur générique radio | flag `kantar_rowpicker_radio` dans le payload + guard dispatcher avant `_find_best_visible` |