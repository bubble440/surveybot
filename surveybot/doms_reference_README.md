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
| `DOM_radio_dom7.txt` | **Decipher** | Radio (consent) | Extraction `input[type=radio]`, labels via `<label for>` |
| `DOM_multi_checkbox.txt` | **Decipher** | Checkbox multi-select | Options exclusives (`class="exclusive"`), champ "Autre" avec input text |
| `DOM_362624.txt` | **Decipher** | Slider Points (échelle) | `<select>` caché sous slider UI, classes `sq-sliderpoints`, valeurs 0-4 |
| `DOM_decipher_text_redherring.txt` | **Decipher/FocusVision** | Text input (validation) | Question `.question-text`, erreur `.question-error`, input text unique, Red Herring Math |
| `DOM_cint_q1.txt` | **Cint** | Checkbox multi-select | Structure simple `div.answer`, extraction via `label > span` |
| `DOM_dynata.txt` | **Dynata** | Dropdown (single select) | `<select>` Angular (`ng-model`), options via `<option value="string:...">` |
| `DOM_ipso_birthdate.txt` | **IPSOS** | Multi-dropdown (date) | 2 `<select>` liés (mois + année), Bootstrap Select overlay |
| `DOM_aa.txt` | **Ask&Answer** | Multi-select (mat-list) | Angular Material `mat-selection-list`, `mat-list-option`, aria-selected |
| `DOM_aa_28.txt` | **Ask&Answer** | Matrix (grille radio) | `mat-radio-group`, sous-questions par ligne, responsive mobile/desktop |
| `DOM_645.txt` | **Walr** | Radio (single select) | Structure ASP.NET, `input[type=radio]` avec `onclick`, labels imbriqués |
| `DOM_1249.txt` | **Lucid/Samplicio** | Checkbox multi-select | Template JS (`var question = {...}`), labels `label.checkbox` |
| `DOM_12415.txt` | **Lucid/Samplicio** | Radio (single select) | Même structure que 1249, `label.radio`, template SINGLE |
| `DOM_ssi_confirmit_textarea.txt` | **SSI/Confirmit** | Textarea (open-ended) | Question dans `h1.qtext`, filtrage instructions validation, fieldset séparé |
| `DOM_ovey_radio.txt` | **ovey.kr** | Radio (single select) | Inputs display:none, wrappers .radio-wrapper, labels span.answer-label > p.fr-tag |
| `DOM_cloudresearch_sentry_radio.txt` | **CloudResearch/Sentry** | Radio (single select) | Vue.js, `div[role="button"].choice-option`, texte dans `.cr-ct`, question `h1.question-prompt` |
| `DOM_walr_cardsort.txt` | **Walr** | CardSort (single select) | `#cardSortContainer`, question `.statement-box`, options `button.answer-button` |
| `DOM_cmix_simplegrid_QLEISUREACTIVITIES_60524984.txt` | **cmix** | Matrix/Grille (SIMPLE_GRID) | Table HTML classique, radios groupés par ligne via `name`, `data-type="SIMPLE_GRID"` |

---

## Types de Questions Couverts

| Type | DOMs Concernés | Spécificités |
|------|----------------|--------------|
| **Radio (single)** | `radio_dom7`, `645`, `12415`, `ovey_radio`, `cloudresearch_sentry_radio` | Un seul choix, `input[type=radio]` ou `div[role=button]` |
| **Checkbox (multi)** | `multi_checkbox`, `cint_q1`, `aa`, `1249` | Plusieurs choix, options exclusives possibles |
| **Dropdown** | `dynata` | `<select>` natif ou stylé |
| **Multi-dropdown** | `ipso_birthdate` | Plusieurs `<select>` liés (ex: date) |
| **Slider/Échelle** | `362624` | UI slider + `<select>` caché fallback |
| **Text input (validation)** | `decipher_text_redherring` | Input text unique, question Red Herring, filtrage erreurs/instructions |
| **Matrix/Grille** | `aa_28`, `cmix_simplegrid` | Sous-questions × options, radio par ligne |
| **Textarea (open-ended)** | `ssi_confirmit_textarea` | Texte libre, compteur caractères, question hors fieldset |
---

## Patterns d'Extraction par Plateforme

### Decipher (`selfserve/53b/`, `selfserve/3507/`)
```
Question:     h1.question-text
Options:      div.element > span.cell-text > label
Inputs:       input.radio | input.checkbox
Exclusives:   input.exclusive
Erreurs:      div.question-error (à filtrer)
Instructions: h2.instruction-text (à filtrer)
```
**⚠ Particularité Decipher Text Input :**
- Pour les inputs text uniques (Red Herring Math, validations), la question est dans `h1.question-text`
- Le container `.question` contient aussi `.question-error` (message d'erreur) et `.instruction-text` (instructions)
- Ces éléments doivent être filtrés pour extraire uniquement la vraie question
- Structure: `div.question[role="radiogroup"] > .answers.answers-list > input[type="text"]`
- Un seul input text par question (contrairement aux multi-text qui ont plusieurs champs)

### Cint (`/survey/`)
```
Question:     h2#label
Options:      div.answer > label
Inputs:       input[type=checkbox]
```

### Dynata (`rsncdn.com`)
```
Question:     div.questionText
Options:      select > option
Inputs:       select (ng-model)
```

### IPSOS (`ipsosinteractive.com`)
```
Question:     h3.question-title-frontend
Options:      select > option (multiple selects)
Inputs:       select.form-control
```

### Ask&Answer (`/askandanswer/`)
```
Question:     mat-card-title > div
Options:      mat-list-option > div.mat-list-text
Inputs:       mat-pseudo-checkbox | mat-radio-button
Matrix:       mat-table ou accordion mobile
```

### Walr (`walr.com`, `azurewebsites.net`)
```
Question:     div.cQuestionText
Options:      tr.rsRow > td.cCellRowText > label
Inputs:       input.cRadio
```

### Walr CardSort (`walr.com`, `azurewebsites.net`)
```
Conteneur:    div#cardSortContainer
Question:     div.statement-box
Options:      button.answer-button (clic direct, pas d'input natif)
```

**Particularités:**
- Pas d'inputs `<input type="radio">` traditionnels
- Les options sont des `<button>` cliquables directement
- UI de type "card sort" avec statement unique + boutons de réponse
- Pattern similaire à CloudResearch mais avec des boutons au lieu de divs

### Lucid/Samplicio (`samplicio.us`)
```
Question:     div.question (texte direct)
Options:      label.radio | label.checkbox > span
Inputs:       input[type=radio|checkbox]
Data JSON:    var question = {...} (backup)
```

### SSI/Confirmit (`ssisurveys.com`, `researchnow.com`, `surveymonkey.com`)
```
Question:     h1.qtext > label > div.header-text-qs
              OU h1#*_text (ex: h1#HealthWellness_text)
Options:      N/A (open-ended)
Inputs:       textarea.confirmit-textarea
Fieldset:    fieldset#fieldset_* (contient l'input, PAS la question)
```

**⚠ Particularité SSI/Confirmit :**
- La question est **HORS** du `<fieldset>` contenant le textarea
- Le `<p>` adjacent au textarea contient souvent une instruction de validation (ex: "Please enter at least 40 characters") qui n'est **PAS** la question
- Utiliser `_extract_ssi_confirmit_question()` pour remonter au `h1.qtext`


### ovey.kr (`ovey.kr/ovey/mobile/`)
```
Question:     div.question-description > h4.fr-tag
Options:      div.answer-choice-wrapper (contient input caché + wrapper visible)
Inputs:       input[type=radio|checkbox] avec style="display: none;"
Labels:       span.answer-label > p.fr-tag
Wrappers:     div.radio-wrapper | div.checkbox-wrapper
CTA:          div.next-btn-wrapper onclick="goNext()"
Survey actif: div#surveyN.survey[style*="display: block"]
```

**Particularités:**
- Inputs TOUJOURS cachés (`style="display: none;"`)
- Le clic doit se faire via `label[for=input_id]`
- Plusieurs surveys dans le DOM, seul celui avec `display: block` est actif
- Questions en coréen/français (i18n via `<p class="fr-tag">`)

### CloudResearch/Sentry (`sentry.cloudresearch.com`)
```
Question:     h1[class*='question-prompt'] | h1[id*='QuestionLabel'] | h1.cr-custom-qt
Options:      .choice-option[role='button'] .cr-ct | [class*='answer-choice']
Inputs:       div[role='button'].choice-option (clic direct, pas d'input natif)
Conteneur:    #sentry | .cr-question-card
CTA:          button.next (disabled tant qu'aucune option sélectionnée)
```

**Particularités:**
- Framework Vue.js, PAS d'inputs `<input type="radio">` traditionnels
- Les options sont des `<div role="button">` cliquables
- Le texte de l'option est dans `.cr-ct` (CloudResearch content)
- Chaque bouton a un `tabindex` unique (2, 3, 4, 5...)
- SVG d'icône cercle dans chaque option (à ignorer pour l'extraction texte)
- Le bouton "Next" est désactivé (`disabled`) tant qu'aucune option n'est sélectionnée

### cmix (`cdn2.cmix.com`, `cmix.com`)
```
Conteneur:    div.cm-survey-container
Question:     div.cm-qtext
Type détect:  div.cm-element[data-type="SIMPLE_GRID"]
Grille:       div.cm-simple-grid > table.cm-simple-grid__table
Headers col:  th.cm-simple-grid__column-header > div
Headers row:  td.cm-simple-grid__row-header > div[data-subquestionname]
Cellules:     td.cm-simple-grid__cell > div.cm-radio-input-container
Inputs:       input[type=radio] avec questionid, name (groupage par ligne)
CTA:          a#cm-NextButton.cm-navigation-next-button
Progress:     div.cm-progress-bar .determinate[style*="width:"]
```

**Particularités:**
- Structure table HTML classique (pas Angular Material comme Ask&Answer)
- Attribut `data-type="SIMPLE_GRID"` sur `div.cm-element.cm-question` identifie le type matrix
- Chaque ligne (sous-question) a son propre `name` pour les radios → groupage automatique
- `questionid` attribut sur les containers `.cm-radio-input-container` identifie la sous-question
- `data-subquestionname` sur les headers de ligne (ex: `QLEISUREACTIVITIES_4`)
- Inputs radios standards avec `value` et `data-response-id`
- Labels vides (`.cm-radio-input`) servent uniquement au styling
- Bouton "Suivant" via `a#cm-NextButton` (lien stylé en bouton)
- Progress bar avec pourcentage visible

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
#    - Type d'input détecté (radio/checkbox/select)

# 4. Simuler réponse OpenAI et vérifier:
#    - Sélection correcte des inputs
#    - Pas de sélection d'options exclusives avec d'autres
```

### Critères de Non-Régression

| Test | Critère de Succès |
|------|-------------------|
| Extraction question | Texte non vide, sans balises HTML parasites |
| Extraction options | ≥ 2 options, chaque option a texte + value |
| Détection type | Correct parmi: radio, checkbox, select, slider, matrix, textarea |
| Application réponse | Input sélectionné correspond à l'instruction |
| Filtrage validation | Instructions "minimum X characters" ne sont PAS des questions |

---

## Notes Importantes

1. **Encodage** : Certains DOMs contiennent des caractères UTF-8 mal encodés (ex: `ÃƒÆ'Ã†â€™Ãƒâ€šÃ‚©` pour `é`). Le code doit être robuste à ces variations.

2. **Éléments cachés** : Les sliders Decipher ont des `<select>` cachés - le code doit pouvoir les exploiter en fallback.

3. **Options dynamiques** : Ask&Answer et Dynata utilisent Angular - les options peuvent être rendues dynamiquement.

4. **Mobile vs Desktop** : Ask&Answer a des structures différentes selon le viewport (`fxhide.lt-md`).

5. **Options exclusives** : Decipher marque certaines options avec `class="exclusive"` - elles doivent désélectionner les autres.

6. **Instructions de validation** : Sur SSI/Confirmit, les `<p>` adjacents aux textareas contiennent souvent des compteurs de caractères ("Please enter at least 40 characters"). Ces textes doivent être filtrés via `_is_validation_instruction()` pour ne pas être confondus avec la vraie question.

7. **Decipher Text Input** : Sur Decipher/FocusVision, les questions Red Herring (validations anti-bot) utilisent des inputs text uniques dans `.answers.answers-list`. Le container `.question` contient la question (`.question-text`), mais aussi des erreurs (`.question-error`) et instructions (`.instruction-text`) qui doivent être filtrées. Utiliser `_extract_decipher_single_text_input()` pour extraction ciblée.

8. **Grilles cmix** : Structure table HTML classique avec `data-type="SIMPLE_GRID"`. Les radios sont groupés par `name` (un par ligne/sous-question). Utiliser `questionid` pour identifier la sous-question associée.

---

## Ajout de Nouveaux DOMs

Pour ajouter un nouveau DOM de référence :

1. Sauvegarder le HTML `<body>` complet
2. Nommer : `DOM_[plateforme]_[type]_[id].txt`
3. Documenter dans ce README :
   - Plateforme identifiée
   - Type de question
   - Patterns d'extraction spécifiques
4. Ajouter un test de non-régression correspondant

---

## Historique des Ajouts

| Date | DOM | Raison |
|------|-----|--------|
| 2025-02 | `DOM_ssi_confirmit_textarea.txt` | Bug extraction question textarea - instruction validation capturée au lieu de la vraie question |
| 2025-02 | `DOM_cloudresearch_sentry_radio.txt` | Support CloudResearch/Sentry - radios Vue.js via div[role="button"].choice-option |
| 2025-02 | `DOM_walr_cardsort.txt` | Support Walr CardSort - boutons answer-button sans inputs natifs |
| 2025-02 | `DOM_cmix_simplegrid_QLEISUREACTIVITIES_60524984.txt` | Support cmix SIMPLE_GRID - matrix table HTML avec radios groupés par ligne |
| 2025-02 | `DOM_decipher_text_redherring.txt` | Support Decipher input text unique - questions Red Herring Math avec filtrage erreurs/instructions |
