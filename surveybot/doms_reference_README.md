# DOMs de Référence - Non-Régression SurveyBot

Ce dossier contient les DOMs de référence utilisés pour valider que les modifications du code n'introduisent pas de régressions.

## Objectif

Tout patch proposé **DOIT** être validé contre ces DOMs pour garantir :
1. **Extraction correcte des `question_blocks`** (question, options, type d'input)
2. **Application correcte des instructions OpenAI** (sélection des bonnes réponses)

---

## Inventaire des DOMs

### Par Plateforme

| Fichier | Plateforme | Type de Question | Points de Validation Clés |
|---------|------------|------------------|---------------------------|
| `DOM_QLEISUREACTIVITIES_CMix_SimpleGrid_LeisureFrequency_FR.txt` | **cmix** | Matrix/Grille (SIMPLE_GRID) | Table HTML classique, radios groupés par ligne via `name`, `data-type="SIMPLE_GRID"`, fréquence loisirs |
| `DOM_purespectrum_radio_211.txt` | **Purespectrum** | Radio (single select) | Angular, `ps-single-choice-question`, `input[type=radio]` dans `label`, `data-e2e` sur les inputs |
| `DOM_confirmit_children_household_multi_exclusive_fr.txt` | **Forsta/Confirmit** | Checkbox multi-select + exclusives | `cf-answer-button--exclusive`, structure `cf-question`, options exclusives CSS-driven |
| `DOM_cmix_language_selector_radio_60552194.txt` | **cmix** | Radio (single select) | `data-type="RADIO"`, `cm-radio-label`, sélecteur de langue bilingue EN/FR |
| `DOM_cmix_intro_consent_checkbox_60552196.txt` | **cmix** | Checkbox (consentement) | `data-type="CHECKBOX"`, intro/welcome page, case à cocher unique de consentement |
| `DOM_rsch_demographics_fr.txt` | **Rsch (Research System)** | Multi-type (radio + text + dropdown) | Page dmographique multi-questions (genre SC1, ge SC2, rgion SC3), mix radio/text/select |
| `DOM_ipsos_gender_radio_fr.txt` | **IPSOS** | Radio (single select) | `h3.question-title-frontend`, `input[type=radio]` dans `.radio > label`, Wicket framework |
| `DOM_decipher_red_herring_math_text_fr.txt` | **Decipher** | Text input (red herring math) | `input[type=text]`, question `h1.question-text`, validation erreur `div.question-error`, anti-bot |
| `DOM_angular_material_radio_fruits.txt` | **Angular Material (custom)** | Radio (single select) | `mat-radio-button`, `mat-mdc-radio-checked` sur option sélectionnée, `value` numérique, red herring fruits |

---

## Types de Questions Couverts

| Type | DOMs Concernés | Spécificités |
|------|----------------|--------------|
| **Radio (single)** | `purespectrum_radio_211`, `cmix_language_selector_radio_60552194`, `ipsos_gender_radio_fr`, `angular_material_radio_fruits` | Un seul choix possible |
| **Checkbox (multi)** | `confirmit_children_household_multi_exclusive_fr`, `cmix_intro_consent_checkbox_60552196` | Plusieurs choix, options exclusives possibles |
| **Text input** | `decipher_red_herring_math_text_fr` | Saisie libre d'un nombre, anti-bot/attention check |
| **Multi-type (page démographique)** | `rsch_demographics_fr` | Radio + text + select sur une même page |
| **Matrix/Grille** | `QLEISUREACTIVITIES_CMix_SimpleGrid_LeisureFrequency_FR` | Sous-questions × options, radio par ligne |

---

## Patterns d'Extraction par Plateforme

### cmix (`cdn2.cmix.com`, `cmix.com`, `dynata.com`)
```
Conteneur:    div.cm-survey-container
Question:     div.cm-qtext
Type détect:  div.cm-element[data-type="RADIO|CHECKBOX|SIMPLE_GRID"]

— RADIO —
Options:      div.cm-radio-label (texte de l'option)
Inputs:       input[type=radio]
CTA:          a#cm-NextButton.cm-navigation-next-button

— CHECKBOX —
Options:      label (contient le texte de consentement ou l'option à cocher)
Inputs:       input[type=checkbox]
Particularité: Sur les pages d'intro/consentement, le texte de la question peut être
              dans des blocs de type cm-intro ou dans des paragraphes libres du DOM,
              en dehors du bloc cm-qtext habituel.

— SIMPLE_GRID —
Grille:       div.cm-simple-grid > table.cm-simple-grid__table
Headers col:  th.cm-simple-grid__column-header > div
Headers row:  td.cm-simple-grid__row-header > div[data-subquestionname]
Cellules:     td.cm-simple-grid__cell > div.cm-radio-input-container
Inputs:       input[type=radio] (name unique par ligne = groupage automatique)
Attributs:    data-subquestionname (ex: QLEISUREACTIVITIES_4), questionid
```

**Particularités cmix :**
- `data-type` sur `div.cm-element.cm-question` identifie systématiquement le type de question
- Pour les SIMPLE_GRID : chaque ligne a son propre `name` de radio → groupage automatique
- Labels radio (`.cm-radio-input`) souvent vides → ne pas les utiliser pour le texte
- Progress bar avec pourcentage visible : `div.cm-progress-bar .determinate[style*="width:"]`
- Bouton "Suivant" = `a#cm-NextButton` (lien stylé en bouton, pas un `<button>`)
- Sur les pages de consentement/intro, le texte explicatif précède le bloc question et peut contenir des balises `<a>` (liens politique de confidentialité)

---

### Purespectrum (`purespectrum.com`)
```
Framework:    Angular (ps-root, ps-single-choice-question, ps-question-orchestrator)
Question:     p[psquestiontitle] | p#single-choice-question-title.question-title
Options:      label.form-check (contient input + texte directement)
Inputs:       input[type=radio].form-check-input (data-e2e="111|112|113...")
Sélectionné:  label.active-bg (vs label.inactive-bg pour les non-sélectionnés)
CTA:          ps-next-button > ps-button > button (aria-label="Go to next question")
Progress:     ngb-progressbar[aria-valuenow]
```

**Particularités Purespectrum :**
- Framework Angular avec composants custom (`ps-*`)
- `value="[object Object]"` sur les inputs — ne pas utiliser `value` pour identifier l'option, utiliser `data-e2e` à la place
- L'option sélectionnée est identifiable par la classe `active-bg` sur le `<label>`
- Les radios ont `name="[object Object]"` (non discriminant), utiliser `id` (`choice-0`, `choice-1`...)
- Cookie consent overlay (CookieYes) présent dans le DOM mais sans impact sur la question

---

### Forsta / Confirmit (`qubiq-surveys.com`, `ssisurveys.com`, `forsta.com`)
```
Framework:    Confirmit/Forsta (renommage de SSI/Confirmit)
Question:     div.cf-question__text (texte de la question)
              div.cf-question__instruction (instruction optionnelle)
Options:      div.cf-list__item.cf-answer-button (chaque option = un div cliquable)
Texte option: div.cf-answer-button__label (texte visible de l'option)
Inputs:       input[type=checkbox] (masqués, interaction via le div parent)
Exclusives:   div.cf-answer-button--exclusive (désélectionne les autres au clic)
Sélectionné:  div.cf-answer-button--selected
CTA:          bouton/lien de navigation Forsta (cf. structure de la page)
```

**Particularités Forsta/Confirmit :**
- Les inputs `<input type="checkbox">` sont masqués — l'interaction se fait via clic sur le `div.cf-answer-button` parent
- Les options exclusives portent la classe `cf-answer-button--exclusive` ET `cf-answer-button--selected` quand cochées
- La logique exclusive est gérée via JS custom injecté dans la page (`Fix the exclusive button for Multis`)
- Structure CSS-driven : l'état sélectionné/désélectionné est géré par les classes CSS, pas par `checked` sur l'input
- Différent de Decipher (selfserve) malgré une parenté historique — patterns d'extraction distincts

---

### Rsch / Research System (`index.php`)
```
Framework:    PHP custom (formulaire HTML classique)
Question:     div.content_note.note (texte de la question)
N° question:  div.qno (ex: SC1, SC2, SC3)

— Radio (SC1 genre) —
Options:      label.rdck_label_sp (associé via for=)
Inputs:       input[type=radio] (name=sc1, values=1/2/3)

 Text input (SC2 ge) 
Inputs:       input[type=text] (name=sc2_1, maxlength=2)
Unité:        texte adjacent " ans"

— Dropdown (SC3 région) —
Inputs:       select[name=sc3] > option[value=1..14]
Options:      textes des <option> (régions françaises)

CTA:          input#btnsmall[type=button].submitButton (value=">>")
Progress:     div#progressbar (barre 0-100%)
```

**Particularités Rsch :**
- Plusieurs questions sur une même page (SC1, SC2, SC3) — extraire chaque `div.question_default` séparément
- Encodage UTF-8 parfois altr dans les caractres accentus (`é` pour ``, etc.)
- Les `<input type="hidden">` (nextdata, back_button) ne sont pas des questions
- `data-survey-uid` présent sur tous les éléments (utile pour le ciblage précis)
- La region est via un `<select>` natif avec `<option value="">--- </option>` comme placeholder

---

### IPSOS (`ipsosinteractive.com`)
```
Framework:    Apache Wicket (Java) + Bootstrap
Question:     h3.question-title-frontend
Options:      div.radio > label > span.font-weight-light (texte de l'option)
Inputs:       input[type=radio] (name long Wicket, value="radio0"|"radio1"...)
CTA:          a#submitQuestion.btn.btn-primary (texte span#submitLabel)
```

**Particularités IPSOS :**
- `name` des inputs = chemin Wicket complet (ex: `questionContainer:optionsPanel:question:0:actualQuestionPanel:radioGroup`) — non lisible mais cohérent
- `value` = `"radio0"`, `"radio1"` etc. (index numérique, pas le texte)
- Le texte de l'option est dans `<span class="font-weight-light text-hard-light">` à l'intérieur du `<label>`
- Footer contient un lien "Politique de confidentialité" (ne pas confondre avec une option)
- Indicateur AJAX : `img#ajaxLoadingImage.ajaxIndicator` (présent deux fois dans le DOM)

---

### Decipher (`selfserve/1da7/`)
```
Question:     h1.question-text (peut contenir des balises <p>)
Instructions: h2.instruction-text
Erreur:       div.question-error > h2.question-error-text (visible si validation échouée)

— Text input (red herring / attention check) —
Type detect:  div.question.number (classe "number" = saisie numérique)
Inputs:       input[type=text].text-input (name=ans383.0.0, size=2)
CTA:          input[type=submit]#btn_continue.button.continue (value="Continuer »")
```

**Particularités Decipher (text/math) :**
- Question de type "red herring" / attention check : math simple (ex: 6+6=??)
- Classe `number` sur le div question indique une réponse numérique attendue
- `name` de l'input suit le pattern `ans[pageId].[row].[col]` (ex: `ans383.0.0`)
- L'erreur de validation (`div.question-error`) est présente dans le DOM même si cachée
- `input[type=hidden]` `state` et `start_time` = données de session (ne pas interagir)

---

### Angular Material (custom/generic)
```
Framework:    Angular 19 + Angular Material (mat-radio-*)
Question:     h5.question-text (texte de la question)
Options:      label.mdc-label[for=mat-radio-N-input] (texte de l'option)
Inputs:       input[type=radio].mdc-radio__native-control (id=mat-radio-N-input)
Sélectionné:  mat-radio-button.mat-mdc-radio-checked (classe sur le composant parent)
              + input avec tabindex="0" (vs tabindex="-1" pour les non-sélectionnés)
CTA:          button.next_btn (texte "SUIVANT")
```

**Particularités Angular Material :**
- Les `value` des inputs sont numériques (`"1"`, `"2"`, `"3"`, `"4"`) — correspondent à un ordre logique, pas au texte
- La bonne réponse est identifiable via `mat-mdc-radio-checked` sur le `<mat-radio-button>` parent
- `tabindex="0"` sur l'input sélectionné vs `tabindex="-1"` sur les autres (utile en fallback)
- Question de type "red herring fruits" : vérification d'attention sur des achats (pomme/bananes/raisins)
- Structure Angular Material encapsulée : `mat-radio-group > mat-radio-button > div.mdc-radio > input`
- Le composant est rendu dans `app-survey` (application Angular standalone)

---

## Utilisation pour Validation

### Avant tout patch, vérifier :

```bash
# 1. Charger le DOM de référence
dom_content = load_dom("DOM_xxx.txt")

# 2. Extraire les question_blocks
blocks = extract_question_blocks(dom_content)

# 3. Vérifier:
#    - Nombre de blocks correct
#    - Question text extrait
#    - Options complètes (texte + value)
#    - Type d'input détecté (radio/checkbox/select/text)

# 4. Simuler réponse OpenAI et vérifier:
#    - Sélection correcte des inputs
#    - Pas de sélection d'options exclusives avec d'autres
```

### Critères de Non-Régression

| Test | Critère de Succès |
|------|-------------------|
| Extraction question | Texte non vide, sans balises HTML parasites |
| Extraction options | ≥ 2 options (sauf consentement unique), chaque option a texte + value |
| Détection type | Correct parmi : radio, checkbox, select, text, matrix |
| Application réponse | Input sélectionné correspond à l'instruction |
| Exclusives Forsta | Option exclusive désélectionne les autres (class-driven, pas checked) |
| Red herring | Réponse correcte au calcul ou à la question d'attention identifiée |

---

## Notes Importantes

1. **Encodage** : `DOM_rsch_demographics_fr.txt` contient des caractres UTF-8 mal encods (`é` pour ``, etc.). Le code doit tre robuste  ces variations.

2. **Éléments masqués** : Sur Forsta/Confirmit, les `<input type="checkbox">` sont masqués — interaction obligatoirement via le `div.cf-answer-button` parent.

3. **Value non fiable** : Sur Purespectrum, `value="[object Object]"` — utiliser `data-e2e` ou `id` pour identifier et interagir avec les options.

4. **Exclusives CSS-driven** : Sur Forsta, l'exclusivité est gérée par classes CSS + JS custom, non par l'attribut HTML `disabled` ou `exclusive`.

5. **Page multi-questions** : `DOM_rsch_demographics_fr.txt` contient 3 questions (SC1, SC2, SC3) sur une seule page — itérer sur chaque `div.question_default`.

6. **Page d'intro cmix** : `DOM_cmix_intro_consent_checkbox_60552196.txt` est une page de bienvenue + consentement — le texte "question" est dispersé dans des blocs libres, pas dans un `div.cm-qtext` standard.

7. **Red herring / attention checks** : `DOM_decipher_red_herring_math_text_fr.txt` et `DOM_angular_material_radio_fruits.txt` sont des questions de contrôle qualité. La bonne réponse doit être calculée/identifiée avant soumission.

---

## Historique des Ajouts

| Date | DOM | Raison |
|------|-----|--------|
| 2025-02 | `DOM_QLEISUREACTIVITIES_CMix_SimpleGrid_LeisureFrequency_FR.txt` | Support cmix SIMPLE_GRID — matrix table HTML avec radios groupés par ligne (fréquence loisirs, FR) |
| 2025-02 | `DOM_purespectrum_radio_211.txt` | Support Purespectrum Angular — radio single select, value=[object Object], data-e2e pour identification |
| 2025-02 | `DOM_confirmit_children_household_multi_exclusive_fr.txt` | Support Forsta/Confirmit multi+exclusives — checkbox masqués, interaction CSS-driven via cf-answer-button |
| 2025-02 | `DOM_cmix_language_selector_radio_60552194.txt` | Support cmix RADIO — sélecteur de langue bilingue, pattern cm-radio-label |
| 2025-02 | `DOM_cmix_intro_consent_checkbox_60552196.txt` | Support cmix CHECKBOX intro/consent — page bienvenue, texte question hors cm-qtext |
| 2025-02 | `DOM_rsch_demographics_fr.txt` | Support Rsch PHP custom — page multi-questions (radio + text + select), encodage UTF-8 dégradé |
| 2025-02 | `DOM_ipsos_gender_radio_fr.txt` | Support IPSOS Wicket — radio genre FR, name Wicket long, value=radio0/radio1 |
| 2025-02 | `DOM_decipher_red_herring_math_text_fr.txt` | Support Decipher text/number — attention check math, input[type=text], validation error visible |
| 2025-02 | `DOM_angular_material_radio_fruits.txt` | Support Angular Material radio — red herring fruits, mat-mdc-radio-checked, tabindex comme signal sélection |