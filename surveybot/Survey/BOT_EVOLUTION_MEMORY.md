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
# Fichier, patterns couverts, patterns exclus, contexte du dernier patch.
# Max ~25 lignes par entrée. Rester factuel et DOM-centrique.

---

## PLATEFORME : ASKIA
Signature : `<body onload="loadFormAskia();">`, form action `AskiaExt.dll`
Inputs : schéma `M{N} {value}` (checkbox/radio) ou `U{N}` (hidden slider)
Note : plusieurs types de questions peuvent coexister sur une même page.
Les extracteurs Askia sont indépendants et leurs résultats sont concaténés.

### _extract_askia_adc_slider
Fichier : Survey/dom_extractors_misc.py
Patterns couverts :
- Conteneur : div.adc-slider contenant div.noUiSlider + div.noUi-handle
- Input hidden : <input type="hidden" name="U{N}"> dans le même conteneur
- Labels pôles : div.leftLabel, div.rightLabel
- Bouton DK optionnel : div.dk[data-value]
- Sous-question : td.askia-question-label ou td[class*="askia-caption"] dans le tr précédent
- Question globale possible en tête de page (format retourné : "global | sous-question")
- Valeurs exposées : 0%…100% par pas de 10, + DK si présent
Patterns exclus :
- Matrices checkbox (div.adc-responsiveTable, tr[data-id]) → extracteur distinct
- Radios classiques Askia (div.myresponse, span.items) → extracteur distinct
- Sliders Decipher sq-sliderpoints → input_slider.py
Contexte patch :
- [date à compléter] Création initiale pour pages Askia multi-sliders noUiSlider.
  Un input hidden U{N} par slider, plusieurs adc-slider possibles sur une même page.

---

## FRONTIÈRES INTER-EXTRACTEURS

| Plateforme | Extracteur A | Extracteur B | Signal de discrimination |
|---|---|---|---|
| Askia | _extract_askia_adc_slider | _extract_askia_adc_responsive_table | class du div principal : adc-slider vs adc-responsiveTable |
| Askia | _apply_by_target_id (askia_responsive_table_checkbox) | chemin générique opt_map | flag `askia_responsive_table_checkbox` dans le payload registry |

---

## FONCTIONS CRITIQUES NON EXTRACTEURS

### _apply_by_target_id — bloc askia_responsive_table_checkbox
Fichier : Survey/action_dispatcher.py
Emplacement : dans le bloc `if opt_map and resolved_itype in ("radio", "checkbox")`,
  juste avant `_click_candidate(el, "target")`.
Guard d'activation : `payload.get("askia_responsive_table_checkbox") and resolved_itype == "checkbox"`
Patterns couverts :
- Matrices checkbox Askia ResponsiveTable (div.adc-responsiveTable, tr[data-id])
- Inputs `<input type="checkbox">` non-interactables (masqués CSS, taille 0) pointés par option_xpath_map
- Clic JS sur `<label for=inputId>` si présent → déclenche les handlers Askia natifs
- Fallback : `input.checked = true` + dispatchEvent input/change si aucun label trouvé
- Vérification stricte : `input.checked === true` après action
Patterns exclus :
- Sliders noUiSlider (div.adc-slider) → _extract_askia_adc_slider
- Radios classiques Askia (div.myresponse) → chemin générique opt_map
- Tout autre itype (radio, text…) → pas de guard activé
Contexte patch :
- [2025] Fix bug : sélection échouant à partir de la 2ème question de matrice
  (ElementNotInteractableException sur input natif + ActionChains, "has no size and location").
  Stratégie unique (pas de fallback empilés). Retourne False immédiatement si input.checked=false.
  Log : "[TARGET] apply ok=true strategy=askia_responsive_table_checkbox reason=label_js_click"

### execute_action — post-vérification target_id MetrixLab/Toluna QT
Fichier : Survey/action_dispatcher.py
Emplacement : dans `execute_action()`, immédiatement après le retour positif de `_apply_by_target_id(...)`
  et avant le log `strategy=target_id reason=applied`.
Guard d'activation :
- `target_id` présent
- `_apply_by_target_id(...)` a renvoyé `True`
- page contenant des wrappers `div.answer_options` avec `input.checkboxQT/radioQT`
Patterns couverts :
- MetrixLab / Toluna SPA avec options sous forme de `div.answer_options`
- État sélectionné porté visuellement par `.option_checkbox.input_on`
  et/ou `.option_label.input_label_on`
- Cas où la stratégie `target_id` réussit techniquement (clic/dispatch) mais sans effet UI réel
- Blocage du faux positif : ne pas logger `apply ok=true strategy=target_id` tant que le DOM
  ne montre pas une option réellement activée
Patterns exclus :
- Checkboxes/radios natifs validés par `input.checked`
- Widgets custom déjà vérifiés dans `_apply_by_target_id` (QARTS, Nfield swatches,
  Askia ResponsiveTable, Toluna Runtime AnswerRow, etc.)
- Toute page sans structure `div.answer_options` + input `*QT`
Contexte patch :
- [2026-04] Fix d'un faux succès sur question checkbox MetrixLab/Toluna (`group_4fe25a510f06`).
  Le parser et le dispatcher produisaient bien l'action, mais `execute_action()` déclarait
  `strategy=target_id reason=applied` alors que l'UI restait inchangée. La validation correcte
  sur ce provider repose sur les classes DOM `input_on` / `input_label_on`, pas sur le simple
  succès du clic ni sur `input.checked`.

### execute_action / open_dropdown_generic — dropdown natif sans ouverture préalable
Fichiers : Survey/action_dispatcher.py, Survey/input_dropdown.py
Emplacement : branche `itype == "dropdown"` dans `execute_action()`, et branche `<select>` native dans `open_dropdown_generic()`.
Guard d'activation : action dropdown avec target/question résolu, présence possible d'un `<select>` natif.
Patterns couverts :
- Dropdown natif `<select>` déjà présent dans le DOM et sélectionnable directement par `select_option_with_hint()`.
- Cas Toluna/MerlinAI où l'option `0` est la réponse valide et peut déjà être visible avant action.
- Sélection directe par valeur/texte via `select_option_with_hint()`, sans appel préalable à `dropdown_open`.
- `open_dropdown_generic()` ne doit pas cliquer/focuser/envoyer `ARROW_DOWN` sur un `<select>` natif.
Patterns exclus :
- Dropdown custom nécessitant ouverture de menu avant sélection visuelle.
- Bootstrap-select / GfK `.mrDropdown` / RPS custom : restent gérés dans `select_option_with_hint()` par leurs guards DOM dédiés.
- CTA/navigation : aucun changement de comportement attendu.
Contexte patch :
- [2026-04] Fix bug : l'ouverture préalable du dropdown natif envoyait un effet clavier/focus
  qui pouvait déplacer ou invalider la valeur sélectionnée avant la vraie sélection.
  La stratégie retenue est unique : pour les dropdowns, passer directement par `dropdown_select`.
  Validation observée : l'option `0` reste appliquée et l'exécution atteint ensuite le CTA.

---

## PLATEFORME : INTERVIEW-LAYOUT (areyounet / Potloc-style)
Signature DOM : `div.interview-layout` contenant `div.interview-header` + `div.interview-question` + `div.interview-footer__container`
Inputs : `button.choice-question__field[role="option"][data-test-id="ChoiceFields_Field-{uuid}"]` dans `ul.choice-question__field-list`
Options spéciales footer : `button[data-test-id="InterviewFooter_SpecialOption-*"]` dans `div.interview-footer__options-container`
Champ libre "Autre" : `<input type="text" role="option">` dans `div.choice-question__custom-field-container`

### button_group générique — filtre interview-footer__options-container
Fichier : Survey/dom_analyzer.py
Emplacement : boucle `for b in btn_like`, après le filtre CookieYes, avant `_nearest_question_container`.
Guard d'activation : `closest('.interview-footer__options-container') !== null`
Patterns couverts :
- Boutons spéciaux de page ("Aucun(e)", "Passer") dans `div.interview-footer__options-container`
- Ces boutons passent tous les filtres génériques (visibles, texte ≥ 2 chars, non-nav) mais
  ne sont pas des choix de réponse : ce sont des options de navigation de page.
- Le filtre les exclut avant groupement → pas de bloc parasite créé.
Patterns exclus :
- Tout bouton hors de `.interview-footer__options-container` → non affecté.
- Boutons nav classiques (Continuer…) déjà filtrés par `_is_nav_like_choice`.
Contexte patch :
- [2026-04] Fix : "Aucun(e)" et "Passer" formaient un 2e question_block indépendant
  avec itype=radio. Guard DOM strict via `closest`, aucun impact inter-provider.

### button_group générique — récupération h1.interview-header__title
Fichier : Survey/dom_analyzer.py
Emplacement : boucle `for _gk, g in btn_groups.items()`, après `_extract_question_from_container` et
  `_find_question_text_near_element`, avant le filtre "un problème est survenu".
Guard d'activation : `closest('.interview-question') !== null` sur le conteneur résolu
  ET existence de `h1.interview-header__title` dans la page.
Patterns couverts :
- Layout où la question principale est dans `h1.interview-header__title` (frère de
  `.interview-question` dans `.interview-layout`), hors scope du conteneur résolu.
- Sans ce patch, seul le `h3.hint-text` ("CHOISISSEZ UNE OU PLUSIEURS RÉPONSES") est extrait.
- Le texte du h1 est préfixé à la question déjà extraite si non déjà inclus.
- Log debug : "[DOM_BUTTON_GROUP] interview_layout_h1 recovered: ..."
Patterns exclus :
- Tout conteneur hors de `.interview-question` → guard non activé.
- Tout DOM sans `h1.interview-header__title` → guard non activé.
Contexte patch :
- [2026-04] Fix : question extraite = "CHOISISSEZ UNE OU PLUSIEURS RÉPONSES" au lieu de
  "Parmi les produits suivants… CHOISISSEZ UNE OU PLUSIEURS RÉPONSES". Double guard strict.

### singles — filtre champ texte libre dans choice-question__custom-field-container
Fichier : Survey/dom_analyzer.py
Emplacement : chemin `other_inputs`, après `_is_other_specify_choice_companion`, avant `looks_like_other`.
Guard d'activation : `itype in ("text", "textarea")` ET `role="option"` ET
  `closest('.choice-question__custom-field-container') !== null`.
Patterns couverts :
- `<input type="text" role="option">` dans `div.choice-question__custom-field-container`
  (champ libre "Autre" de la liste, options rendues en `<button role="option">` non natifs).
- `_is_other_specify_choice_companion` ne le détecte pas : 0 `input[type=radio/checkbox]`
  dans le conteneur → garde-fou `< 2` → retourne False → bloc text parasite créé.
- Log debug : "[DOM_DEBUG] skip_interview_layout_custom_text_field role=option"
Patterns exclus :
- Tout input sans `role="option"` → non affecté.
- Tout input hors de `.choice-question__custom-field-container` → non affecté.
Contexte patch :
- [2026-04] Fix : bloc `itype=text, question="System U"` créé à tort depuis le champ libre
  de la liste de courses. Double guard DOM strict, patch minimal additif.