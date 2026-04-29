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