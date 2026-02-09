# DOMs de RÃ©fÃ©rence - Non-RÃ©gression SurveyBot

Ce dossier contient les DOMs de rÃ©fÃ©rence utilisÃ©s pour valider que les modifications du code n'introduisent pas de rÃ©gressions.

## Objectif

Tout patch proposÃ© **DOIT** Ãªtre validÃ© contre ces DOMs pour garantir :
1. **Extraction correcte des `question_blocks`** (question, options, type d'input)
2. **Application correcte des instructions OpenAI** (sÃ©lection des bonnes rÃ©ponses)

---

## Inventaire des DOMs

### Par Plateforme

| Fichier | Plateforme | Type de Question | Points de Validation ClÃ©s |
|---------|------------|------------------|---------------------------|
| `DOM_radio_dom7.txt` | **Decipher** | Radio (consent) | Extraction `input[type=radio]`, labels via `<label for>` |
| `DOM_multi_checkbox.txt` | **Decipher** | Checkbox multi-select | Options exclusives (`class="exclusive"`), champ "Autre" avec input text |
| `DOM_362624.txt` | **Decipher** | Slider Points (Ã©chelle) | `<select>` cachÃ© sous slider UI, classes `sq-sliderpoints`, valeurs 0-4 |
| `DOM_cint_q1.txt` | **Cint** | Checkbox multi-select | Structure simple `div.answer`, extraction via `label > span` |
| `DOM_dynata.txt` | **Dynata** | Dropdown (single select) | `<select>` Angular (`ng-model`), options via `<option value="string:...">` |
| `DOM_ipso_birthdate.txt` | **IPSOS** | Multi-dropdown (date) | 2 `<select>` liÃ©s (mois + annÃ©e), Bootstrap Select overlay |
| `DOM_aa.txt` | **Ask&Answer** | Multi-select (mat-list) | Angular Material `mat-selection-list`, `mat-list-option`, aria-selected |
| `DOM_aa_28.txt` | **Ask&Answer** | Matrix (grille radio) | `mat-radio-group`, sous-questions par ligne, responsive mobile/desktop |
| `DOM_645.txt` | **Walr** | Radio (single select) | Structure ASP.NET, `input[type=radio]` avec `onclick`, labels imbriquÃ©s |
| `DOM_1249.txt` | **Lucid/Samplicio** | Checkbox multi-select | Template JS (`var question = {...}`), labels `label.checkbox` |
| `DOM_12415.txt` | **Lucid/Samplicio** | Radio (single select) | MÃªme structure que 1249, `label.radio`, template SINGLE |
| `DOM_ssi_confirmit_textarea.txt` | **SSI/Confirmit** | Textarea (open-ended) | Question dans `h1.qtext`, filtrage instructions validation, fieldset sÃ©parÃ© |
| `DOM_ovey_radio.txt` | **ovey.kr** | Radio (single select) | Inputs display:none, wrappers .radio-wrapper, labels span.answer-label > p.fr-tag |
| `DOM_cloudresearch_sentry_radio.txt` | **CloudResearch/Sentry** | Radio (single select) | Vue.js, `div[role="button"].choice-option`, texte dans `.cr-ct`, question `h1.question-prompt` |
| `DOM_walr_cardsort.txt` | **Walr** | CardSort (single select) | `#cardSortContainer`, question `.statement-box`, options `button.answer-button` |

---

## Types de Questions Couverts

| Type | DOMs ConcernÃ©s | SpÃ©cificitÃ©s |
|------|----------------|--------------|
| **Radio (single)** | `radio_dom7`, `645`, `12415`, `ovey_radio`, `cloudresearch_sentry_radio` | Un seul choix, `input[type=radio]` ou `div[role=button]` |
| **Checkbox (multi)** | `multi_checkbox`, `cint_q1`, `aa`, `1249` | Plusieurs choix, options exclusives possibles |
| **Dropdown** | `dynata` | `<select>` natif ou stylÃ© |
| **Multi-dropdown** | `ipso_birthdate` | Plusieurs `<select>` liÃ©s (ex: date) |
| **Slider/Ã‰chelle** | `362624` | UI slider + `<select>` cachÃ© fallback |
| **Matrix/Grille** | `aa_28` | Sous-questions Ã— options, radio par ligne |
| **Textarea (open-ended)** | `ssi_confirmit_textarea` | Texte libre, compteur caractÃ¨res, question hors fieldset |
---

## Patterns d'Extraction par Plateforme

### Decipher (`selfserve/53b/`, `selfserve/3507/`)
```
Question:     h1.question-text
Options:      div.element > span.cell-text > label
Inputs:       input.radio | input.checkbox
Exclusives:   input.exclusive
```

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

**âš ï¸ ParticularitÃ© SSI/Confirmit :**
- La question est **HORS** du `<fieldset>` contenant le textarea
- Le `<p>` adjacent au textarea contient souvent une instruction de validation (ex: "Please enter at least 40 characters") qui n'est **PAS** la question
- Utiliser `_extract_ssi_confirmit_question()` pour remonter au `h1.qtext`


### ovey.kr (`ovey.kr/ovey/mobile/`)
```
Question:     div.question-description > h4.fr-tag
Options:      div.answer-choice-wrapper (contient input cachÃ© + wrapper visible)
Inputs:       input[type=radio|checkbox] avec style="display: none;"
Labels:       span.answer-label > p.fr-tag
Wrappers:     div.radio-wrapper | div.checkbox-wrapper
CTA:          div.next-btn-wrapper onclick="goNext()"
Survey actif: div#surveyN.survey[style*="display: block"]
```

**ParticularitÃ©s:**
- Inputs TOUJOURS cachÃ©s (`style="display: none;"`)
- Le clic doit se faire via `label[for=input_id]`
- Plusieurs surveys dans le DOM, seul celui avec `display: block` est actif
- Questions en corÃ©en/franÃ§ais (i18n via `<p class="fr-tag">`)

### CloudResearch/Sentry (`sentry.cloudresearch.com`)
```
Question:     h1[class*='question-prompt'] | h1[id*='QuestionLabel'] | h1.cr-custom-qt
Options:      .choice-option[role='button'] .cr-ct | [class*='answer-choice']
Inputs:       div[role='button'].choice-option (clic direct, pas d'input natif)
Conteneur:    #sentry | .cr-question-card
CTA:          button.next (disabled tant qu'aucune option sÃ©lectionnÃ©e)
```

**ParticularitÃ©s:**
- Framework Vue.js, PAS d'inputs `<input type="radio">` traditionnels
- Les options sont des `<div role="button">` cliquables
- Le texte de l'option est dans `.cr-ct` (CloudResearch content)
- Chaque bouton a un `tabindex` unique (2, 3, 4, 5...)
- SVG d'icÃ´ne cercle dans chaque option (Ã  ignorer pour l'extraction texte)
- Le bouton "Next" est dÃ©sactivÃ© (`disabled`) tant qu'aucune option n'est sÃ©lectionnÃ©e
---

## Utilisation pour Validation

### Avant tout patch, vÃ©rifier :

```bash
# 1. Charger le DOM de rÃ©fÃ©rence
dom_content = load_dom("DOM_xxx.txt")

# 2. Extraire les question_blocks
blocks = extract_question_blocks(dom_content)

# 3. VÃ©rifier:
#    - Nombre de blocks correct
#    - Question text extrait
#    - Options complÃ¨tes (texte + value)
#    - Type d'input dÃ©tectÃ© (radio/checkbox/select)

# 4. Simuler rÃ©ponse OpenAI et vÃ©rifier:
#    - SÃ©lection correcte des inputs
#    - Pas de sÃ©lection d'options exclusives avec d'autres
```

### CritÃ¨res de Non-RÃ©gression

| Test | CritÃ¨re de SuccÃ¨s |
|------|-------------------|
| Extraction question | Texte non vide, sans balises HTML parasites |
| Extraction options | â‰¥ 2 options, chaque option a texte + value |
| DÃ©tection type | Correct parmi: radio, checkbox, select, slider, matrix, textarea |
| Application rÃ©ponse | Input sÃ©lectionnÃ© correspond Ã  l'instruction |
| Filtrage validation | Instructions "minimum X characters" ne sont PAS des questions |

---

## Notes Importantes

1. **Encodage** : Certains DOMs contiennent des caractÃ¨res UTF-8 mal encodÃ©s (ex: `ÃƒÆ’Ã‚Â©` pour `Ã©`). Le code doit Ãªtre robuste Ã  ces variations.

2. **Ã‰lÃ©ments cachÃ©s** : Les sliders Decipher ont des `<select>` cachÃ©s - le code doit pouvoir les exploiter en fallback.

3. **Options dynamiques** : Ask&Answer et Dynata utilisent Angular - les options peuvent Ãªtre rendues dynamiquement.

4. **Mobile vs Desktop** : Ask&Answer a des structures diffÃ©rentes selon le viewport (`fxhide.lt-md`).

5. **Options exclusives** : Decipher marque certaines options avec `class="exclusive"` - elles doivent dÃ©sÃ©lectionner les autres.

6. **Instructions de validation** : Sur SSI/Confirmit, les `<p>` adjacents aux textareas contiennent souvent des compteurs de caractÃ¨res ("Please enter at least 40 characters"). Ces textes doivent Ãªtre filtrÃ©s via `_is_validation_instruction()` pour ne pas Ãªtre confondus avec la vraie question.

---

## Ajout de Nouveaux DOMs

Pour ajouter un nouveau DOM de rÃ©fÃ©rence :

1. Sauvegarder le HTML `<body>` complet
2. Nommer : `DOM_[plateforme]_[type]_[id].txt`
3. Documenter dans ce README :
   - Plateforme identifiÃ©e
   - Type de question
   - Patterns d'extraction spÃ©cifiques
4. Ajouter un test de non-rÃ©gression correspondant

---

## Historique des Ajouts

| Date | DOM | Raison |
|------|-----|--------|
| 2025-02 | `DOM_ssi_confirmit_textarea.txt` | Bug extraction question textarea - instruction validation capturÃ©e au lieu de la vraie question |
| 2025-02 | `DOM_cloudresearch_sentry_radio.txt` | Support CloudResearch/Sentry - radios Vue.js via div[role="button"].choice-option |
| 2025-02 | `DOM_walr_cardsort.txt` | Support Walr CardSort - boutons answer-button sans inputs natifs |