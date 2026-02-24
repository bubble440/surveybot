# CLASSIFIER_TAG — Référence DOM SurveyBot

> **Version** : 3.0.0 — Session 3 : +10 DOMs (textarea, slider, confirmit, mat-list, dropdown, text-input, RSCH, instructions, consent)
> **Règle générale** : Le DOM est la source principale. Un marker est retenu seulement s'il est robuste sur les variantes probables de la plateforme (pas de tokens dynamiques, pas d'IDs générés). Une famille = un pattern structurel distinct. Priorité à la prédictibilité sur l'exhaustivité.

---

## 1. FAMILLES D'EXTRACTION

### FAMILLE A — `checkbox_standard_named`
**Description** : Checkboxes HTML natifs avec `name` et `value` stables. Extraction et interaction directes via `input[type=checkbox]`.

**Signature HTML** :
```html
<input type="checkbox" name="[stable_name]" id="[id]" value="[value]">
<label for="[id]">Texte</label>
```

**Critères de détection** : `input[type=checkbox]` + `name` stable + `value` distinctif par option + label lié par `for` ou wrapping.

**Plateformes** : CMIX, IPSOS Cortex

---

### FAMILLE B — `checkbox_atm1d_tile` *(Decipher)*
**Description** : Widget propriétaire Decipher ATM1D. L'interaction se fait via clic sur `<li>`. L'état est matérialisé par `class="sq-atm1d-selected"` sur le `<li>`.

**Signature HTML** :
```html
<div class="sq-atm1d-widget" data-label="[VARNAME]">
  <ul class="sq-atm1d-buttons sq-atm1d-tiled">
    <li class="sq-atm1d-button clickable" data-label="r1" data-index="0">
      <input type="checkbox" class="input clickable"> <!-- NE PAS CLIQUER -->
    </li>
  </ul>
</div>
```

**Critères de détection** : `div.sq-atm1d-widget` + `ul.sq-atm1d-buttons` + `li.sq-atm1d-button[data-label]`.

**Plateformes** : Decipher / FocusVision self-serve

**⚠️ Point critique** : Cliquer sur `<li>`, jamais sur les `<input>` internes.

---

### FAMILLE C — `checkbox_angular_ng_change` *(Dynata Auto-Screener)*
**Description** : Checkboxes AngularJS, état géré par `ng-change`/`checklist-value`.

**Critères de détection** : `ng-controller="autoScreenerController"` + `div.parameter-rendered.multi_select` + `input[ng-change][checklist-value]` + `name` pattern `ms_[chiffres]`.

**Plateformes** : Dynata Auto-Screener

---

### FAMILLE D — `checkbox_angular_component` *(PureSpectrum)*
**Description** : Checkboxes Angular 19 PureSpectrum. Options identifiées par `data-e2e`. Présence optionnelle d'un champ recherche.

**Critères de détection** : `ps-root[ng-version]` + `ps-multi-choice-question[qualificationid]` + `input.multi-select-input[data-e2e]` + `div[role=listbox].multi-select-container`.

**Plateformes** : PureSpectrum

---

### FAMILLE E — `checkbox_cortex_wicket` *(IPSOS Cortex)*
**Description** : Checkboxes Wicket IPSOS. Option exclusive identifiée par `input.logic.exclusive`.

**Critères de détection** : `body.screening-body` + assets `ipsosinteractive.com` + `span.multipleChoice-checkbox` + `input[name*="checkGroup"]`.

**Plateformes** : IPSOS Cortex

---

### FAMILLE F — `radio_standard_html`
**Description** : Radios HTML natifs standard. Couvre CMIX, IPSOS Cortex, Decipher standard, Samplicious. L'extraction est uniforme (`input[type=radio]` + `label[for]`). La plateforme est identifiée par les markers body/container pour choisir le bon submit.

| Plateforme | `name` pattern | Texte dans | Submit |
|---|---|---|---|
| CMIX radio | `[questionId]` (chiffres seuls) | `span.cm-radio-label-text` | `a#cm-NextButton` |
| IPSOS Cortex | `questionContainer:...:radioGroup` (Wicket) | `span.font-weight-light` | `a#submitQuestion` |
| Decipher standard | `ans[N].[row].[col]` (dots) | `<p>` dans `<label>` | `input#btn_continue` |
| Samplicious | `question_[id]` | `<span>` dans `label.radio` | `input#ctl00_Content_btnContinue` |

**Critères de détection communs** : `input[type=radio]` + `name` stable (pas de float/UUID) + label lié par `for` ou wrapping.

**⚠️ Point critique** : Identifier la plateforme via les markers body/container AVANT d'appliquer la stratégie submit.

---

### FAMILLE G — `radio_angular_ps` *(PureSpectrum single-choice)*
**Description** : Radio mono-sélect Angular 19 PureSpectrum. Même plateforme que Famille D mais composant `ps-single-choice-question`. `name="[object Object]"` — non stable.

**Signature HTML** :
```html
<ps-single-choice-question qualificationid="[id]">
  <div role="radiogroup">
    <label class="form-check hide-button" for="choice-[n]">
      <input type="radio" class="form-check-input handset-choice-view" 
             id="choice-[n]" name="[object Object]" data-e2e="[code]">
      Texte
    </label>
  </div>
</ps-single-choice-question>
```

**Critères de détection** : `ps-root[ng-version]` + `ps-single-choice-question[qualificationid]` + `input.handset-choice-view[data-e2e]`.

**⚠️ Points critiques** : Ne jamais utiliser le `name`. Cliquer sur le `<label>` wrapping. État sélectionné : `class="active-bg fw-bold"` sur le label.

**Plateformes** : PureSpectrum

---

### FAMILLE H — `radio_angular_material`
**Description** : Radios Angular Material MDC. Inputs avec `tabindex="-1"`. Interaction via `label.mdc-label`.

**Signature HTML** :
```html
<app-root ng-version="[version]">
  <mat-radio-group role="radiogroup" name="radioOptField">
    <mat-radio-button class="mat-mdc-radio-button theme-radio mat-accent">
      <input type="radio" class="mdc-radio__native-control" tabindex="-1"
             id="mat-radio-[n]-input" name="radioOptField" value="[value]">
      <label class="mdc-label" for="mat-radio-[n]-input">Texte</label>
    </mat-radio-button>
  </mat-radio-group>
</app-root>
```

**Critères de détection** : `app-root[ng-version]` + `mat-radio-group[role=radiogroup]` + `mat-radio-button.mat-mdc-radio-button` + `input.mdc-radio__native-control`.

**⚠️ Point critique** : Cliquer sur `label.mdc-label`, pas sur l'input (`tabindex="-1"`). Option sélectionnée : `mat-radio-button.mat-mdc-radio-checked`.

**Plateformes** : Angular Material (interne/custom)

---

### FAMILLE I — `radio_vue_dynata_profiler` *(Dynata Profiler Vue 3)*
**Description** : Radio Vue 3 Dynata Profiler. `name` = float aléatoire à chaque rendu. Bouton submit conditionnel (`v-if`), absent au chargement.

**Signature HTML** :
```html
<div id="app" data-v-app="">
  <div id="profiler-choice">
    <div class="row single-choice-container">
      <input class="form-check-input btn-check choice-input" type="radio"
             name="single_choice_[FLOAT]" id="single_choice_[FLOAT]_[n]" value="[responseId]">
      <label class="form-label" for="single_choice_[FLOAT]_[n]"><span>Texte</span></label>
    </div>
    <div class="profiler-choice">
      <div class="d-grid d-md-block"><!----></div>  <!-- bouton v-if ici -->
    </div>
  </div>
</div>
```

**Critères de détection** : `div#app[data-v-app]` + `div#profiler-choice` + `div.single-choice-container` + `input.choice-input.btn-check`.

**⚠️ Points critiques** : `name` aléatoire — ne jamais utiliser comme selector. Bouton submit absent au chargement, apparaît après sélection via `v-if`. Evidon banner (`div#_evidon-barrier-wrapper`) peut bloquer — détecter et accepter si présent avant d'interagir.

**Plateformes** : Dynata Profiler

---

### FAMILLE J — `grid_cmix_simple` *(CMIX Simple Grid)*
**Description** : Grille radio CMIX. Chaque ligne = sous-question avec son propre `name` numérique (`data-parent-id`). Colonnes dans `<thead>`, lignes dans `<tbody>`.

**Signature HTML** :
```html
<div class="cm-element" data-type="SIMPLE_GRID" data-qnum="[VARNAME]">
  <div class="cm-simple-grid">
    <table class="cm-simple-grid__table">
      <thead>
        <th class="cm-simple-grid__column-header"><div>Label col</div></th>
      </thead>
      <tr data-response-batch="0">
        <td class="cm-simple-grid__row-header">
          <div data-subquestionname="[QNAME]_[N]">Label ligne</div>
        </td>
        <td class="cm-simple-grid__cell">
          <div class="cm-radio-input-container cm-grid-cell" questionid="[QNAME]_[N]">
            <input type="radio" name="[numericSubQId]" value="[responseId]"
                   data-response-id="[responseId]" data-parent-id="[numericSubQId]" class="with-gap">
          </div>
        </td>
      </tr>
    </table>
  </div>
</div>
```

**Critères de détection** : `body.cm-Survey` + `div[data-type="SIMPLE_GRID"]` + `div.cm-simple-grid` + `div.cm-radio-input-container.cm-grid-cell[questionid]`.

**Méthode d'extraction** :
- Lignes : `div[data-subquestionname]` → attribut + texte
- Colonnes : `th.cm-simple-grid__column-header` → texte
- Cellule : `div.cm-grid-cell[questionid="[QNAME]_[N]"] input[value="[responseId]"]`

**Plateformes** : CMIX

---

### FAMILLE K — `grid_walr_card_sequential` *(Walr)*
**Description** : Grille Walr transformée en UI "cards" par JavaScript. La table HTML est **masquée par CSS**. Le JS crée des `.answer-button` par ligne, présentés un à la fois. Interaction via ces boutons dynamiques. IDs radio dupliqués dans la table sous-jacente.

**Signature HTML sous-jacente** :
```html
<form id="rsForm" action="./c?rs=[token]">
  <table class="cTable rsSingleGrid rsProcessedGrid">
    <th class="cCellHeader" id="h_[qid]_[n]">Label col</th>
    <th class="cCellRowText" id="r_[qid]_[n]">Label ligne</th>
    <td class="cCell">
      <input type="radio" name="rn[N][code]" id="rn[N][code]" value="[1..N]"
             class="cRadio" onclick="clearAll('rn[N][code]');this.checked=true;">
    </td>
  </table>
  <input type="button" id="btnNext" name="btnNext"
         onclick="rsSethf('rs_dir','1');WebForm_DoPostBackWithOptions(...)">
</form>
```

**Critères de détection** : `form#rsForm[action*="./c?rs="]` + `table.cTable.rsSingleGrid.rsProcessedGrid` + `input.cRadio[onclick*="clearAll"]` + `input#btnNext[onclick*="WebForm_DoPostBackWithOptions"]`.

**Interaction** :
1. Cliquer `.answer-button` correspondant à la réponse souhaitée (déclenche `inputFields[i].checked=true` + `showNextStatement()`)
2. Répéter pour chaque ligne
3. `#btnNext` apparaît → `.click()`

**⚠️ Points critiques** : Table cachée par CSS. IDs radio dupliqués — `getElementById` inopérant. `#btnNext` peut s'auto-cliquer quand toutes les lignes sont répondues.

**Plateformes** : Walr

---

### FAMILLE L — `text_ps_open_ended` *(PureSpectrum)*
**Description** : Question ouverte PureSpectrum Angular 19. Composant `ps-open-ended-question` avec `ps-textarea-input`. La `textarea` n'a pas de `name` ni d'`id` stable — cibler uniquement par descendance du composant. Submit identique à la Famille D/G (ps-next-button).

**Signature HTML** :
```html
<ps-open-ended-question qualificationid="[N]">
  <ps-textarea-input>
    <textarea class="form-control ... ng-valid" placeholder="Type your answer here..." rows="1"></textarea>
  </ps-textarea-input>
</ps-open-ended-question>
```

**Critères de détection** : `ps-root[ng-version]` + `ps-open-ended-question[qualificationid]` + `ps-textarea-input` + `textarea.form-control`.

**Interaction** : `clear()` + `send_keys(text)` sur `ps-open-ended-question ps-textarea-input textarea`.

**Submit** : `ps-next-button ps-button[data-e2e="next-button"] button` (identique Famille D/G).

**⚠️ Points critiques** :
- **Overlay CookieYes possible** : si `div#cky-consent` visible → cliquer `button[data-cky-tag="accept-button"]` avant interaction.
- **Validation min-mots** : "au moins 5 mots" — vérifier longueur de la réponse générée.
- DOM-15 et DOM-16 = contenu identique ; la seule différence est l'état du banner CookieYes.

**Plateformes** : PureSpectrum

---

### FAMILLE M — `slider_ipsos_wicket` *(IPSOS Cortex)*
**Description** : Sliders Likert multi-lignes IPSOS. Chaque ligne = un statement + un slider Bootstrap (`bs-slider`). L'input natif est **caché** (`display:none`). Le slider visuel est géré par JS. Interaction via JS direct sur l'input caché ou via `.slider-handle` avec `aria-valuenow`.

**Signature HTML** :
```html
<div id="slider-question-row" class="row">
  <div class="slider slider-horizontal">
    <div class="slider-handle min-slider-handle" role="slider"
         aria-valuemin="0" aria-valuemax="10" aria-valuenow="3" tabindex="0"
         style="left: 50%;"></div>
  </div>
  <input type="text" class="slider-form-field bs-slider"
         name="questionContainer:optionsPanel:question:N:actualQuestionPanel:content:sliderFormField"
         id="sliderFormField[hex]"
         data-slider-ticks="[1,2,3,4,5]"
         data-slider-value="5" value="3" style="display:none;">
</div>
```

**Critères de détection** : `form#questionForm[N]` + `div#slider-question-row` + `input.slider-form-field.bs-slider[data-slider-ticks]` + `a#submitQuestion`.

**Interaction** (stratégie JS) :
```javascript
// Pour chaque slider : définir valeur sur l'input caché, puis déclencher 'change'
input.value = targetValue;
input.dispatchEvent(new Event('change'));
```
Alternative : utiliser ActionChains pour déplacer `.slider-handle` (moins fiable).

**Extraction** : Pour chaque `div#slider-question-row`, lire `data-slider-ticks` (valeurs possibles) + labels `span.pull-left` / `span.pull-right` (extrêmes) + `h3.question-title-frontend` (statement text).

**Submit** : `a#submitQuestion` (identique autres IPSOS).

**⚠️ Points critiques** :
- L'input caché a `data-slider-value` = valeur par défaut pré-remplie. La réponse réelle est dans `value` (peut différer).
- `aria-valuemin/max` sur le handle = `[0,10]` même si les ticks sont `[1..5]` — ignorer, utiliser `data-slider-ticks`.
- Plusieurs sliders sur une même page → itérer sur tous les `div#slider-question-row` avant submit.

**Plateformes** : IPSOS Cortex

---

### FAMILLE N — `checkbox_confirmit_answer_button` *(Confirmit/Forsta)*
**Description** : Checkboxes Confirmit rendues comme boutons ARIA (`role="checkbox"`). Pas d'`<input>` natif utilisable — l'état est entièrement géré par `aria-checked` et JavaScript Confirmit. Option exclusive détectable par `class="cf-answer-button--exclusive"`.

**Signature HTML** :
```html
<div class="cf-question cf-question--answer-buttons-multi" id="[QNAME]">
  <div class="cf-list" role="group">
    <div class="cf-list__item cf-answer-button" id="[QNAME]_1"
         role="checkbox" aria-checked="false" tabindex="0"
         aria-labelledby="[QNAME]_1_text">
      <div class="cf-answer-button__text" id="[QNAME]_1_text">Texte option</div>
    </div>
    <!-- Exclusive : -->
    <div class="cf-list__item cf-answer-button cf-answer-button--exclusive"
         id="[QNAME]_4" role="checkbox" aria-checked="true">
    </div>
  </div>
</div>
```

**Critères de détection** : `div.cf-question.cf-question--answer-buttons-multi[id]` + `div.cf-list__item.cf-answer-button[role=checkbox][aria-checked]` + `button.cf-navigation-next`.

**Interaction** : `.click()` sur `div.cf-answer-button[id="[QNAME]_[code]"]` (le JS Confirmit gère `aria-checked`).

**Option exclusive** : `div.cf-answer-button.cf-answer-button--exclusive` → décocher toutes les autres options d'abord.

**Extraction** :
- Code option : attribut `id` → dernier segment (`[QNAME]_[N]` → code = `N`)
- Texte : `div.cf-answer-button__text[id="[QNAME]_[N]_text"]`
- Sélectionné : `aria-checked="true"` + `class` contient `cf-answer-button--selected`

**Submit** : `button.cf-navigation-next` (contient une image, pas de texte fiable).

**⚠️ Points critiques** :
- **Pas d'`<input>` à cibler** — tout passe par `.click()` sur les divs ARIA.
- **Option exclusive déclarée dans le JSON JS** : `"isExclusive":true` dans `window.Confirmit` → aussi vérifiable côté DOM par la class CSS.
- **Token anti-replay** : `input[name="__sid__"]` présent dans les champs cachés.

**Plateformes** : Confirmit / Forsta

---

### FAMILLE O — `checkbox_angular_mat_list` *(RateAndRank)*
**Description** : Checkboxes Angular Material 14 (non-MDC) via `mat-selection-list` + `mat-list-option`. État géré par `aria-selected` + `mat-pseudo-checkbox`. Plusieurs blocs MULTI peuvent apparaître sur la même page, chacun avec son propre bouton "Ok" intermédiaire. Le submit final est `button#survey-submit-button`.

**⚠️ Note** : La même page peut contenir des questions de type `PULLDOWN` (radios Angular Material non-MDC). Ces radios sont compatibles Famille H mais utilisent les classes plus anciennes : `mat-radio-button.survey-radio-button` sans préfixe `mdc-`. Interaction identique : cliquer `label.mat-radio-label`.

**Signature HTML** :
```html
<div id="appQuestionContainer-[ID]" data-question-type="MULTI">
  <mat-selection-list id="multi-question-[N]" role="listbox" aria-multiselectable="true">
    <mat-list-option id="answer-[Q]-[N]" role="option"
                     aria-selected="false" tabindex="-1">
      <mat-pseudo-checkbox class="mat-pseudo-checkbox"></mat-pseudo-checkbox>
      <div class="mat-list-text">Texte option</div>
      <!-- Option "Autres" : contient un mat-input-element -->
    </mat-list-option>
  </mat-selection-list>
  <button id="okButton-[ID]">Ok</button>
</div>
<!-- Submit global -->
<button id="survey-submit-button" color="primary">SUIVANT</button>
```

**Critères de détection** : `app-root[ng-version="14.1.0"]` + `div[data-question-type="MULTI"]` + `mat-selection-list[role=listbox]` + `mat-list-option[role=option][aria-selected]`.

**Interaction** :
- Cliquer `mat-list-option[id="answer-[Q]-[N]"]` (Angular met à jour `aria-selected` et `mat-pseudo-checkbox`)
- Option "Autres" : sélectionner l'option PUIS `send_keys()` dans `mat-input-element#mat-input-[N]`
- Après chaque bloc MULTI visible : cliquer `button#okButton-[ID]` (apparaît quand ≥1 option sélectionnée)
- Submit final : `button#survey-submit-button` (disabled jusqu'à tous les blocs répondus)

**Radios PULLDOWN** : `mat-radio-group[id="radio-question-[N]"]` > `input.mat-radio-input[id="answer-[Q]-[N]-input"][value="[responseId]"]`. Cliquer `label.mat-radio-label[for="answer-[Q]-[N]-input"]`.

**Extraction** :
- `data-question-type` sur `div#appQuestionContainer-[ID]` → `MULTI` ou `PULLDOWN`
- Texte question : `mat-card-title > div:first-child`
- Options MULTI : `mat-list-option[id^="answer-"]` → texte dans `div.mat-list-text`
- Options PULLDOWN : `input.mat-radio-input[id][value]` → texte dans `span.mat-radio-label-content`

**Submit** : `button#survey-submit-button` (global, actif quand tout répondu).

**Plateformes** : RateAndRank (scripts `/rateandrank/v1/dist/`)

---

### FAMILLE P — `dropdown_ipsos_wicket_bs_select` *(IPSOS Cortex)*
**Description** : Dropdowns date IPSOS Cortex wrappés par Bootstrap Select. Le `<select>` natif est **caché** (`class="bs-select-hidden"`). Le composant visuel est `div.bootstrap-select`. Wicket AJAX déclenché sur `change` de chaque dropdown (chaînage : année → déclenche mise à jour mois si nécessaire).

**Signature HTML** :
```html
<!-- Select caché (exploitable via JS) -->
<select class="form-control bs-select-hidden"
        name="questionContainer:optionsPanel:question:0:actualQuestionPanel:dropdownsContainer:months"
        id="months18">
  <option disabled selected value="">Mois</option>
  <option value="0">Janvier</option>
  ...
</select>
<!-- Composant visuel Bootstrap Select -->
<div class="btn-group bootstrap-select form-control">
  <button class="btn dropdown-toggle" data-toggle="dropdown" data-id="months18">
    <span class="filter-option">Juillet</span>
  </button>
  <div class="dropdown-menu">
    <ul class="dropdown-menu inner" role="menu">
      <li data-original-index="7" class="selected"><a>...</a></li>
    </ul>
  </div>
</div>
```

**Critères de détection** : `body.screening-body` + `select.form-control.bs-select-hidden[name*="dropdownsContainer"]` + `div.bootstrap-select` + Wicket AJAX `c="months[N]"` ou `c="years[N]"`.

**Interaction** (stratégie JS sur select caché) :
```javascript
// 1. Définir valeur sur le select caché
select.value = targetValue;
// 2. Déclencher change pour activer Wicket AJAX
select.dispatchEvent(new Event('change'));
// 3. Attendre rechargement partiel Wicket avant select suivant
```
Ou click sur `div.bootstrap-select button.dropdown-toggle` → `li[data-original-index="N"] a`.

**Extraction** : `option[value]` sur le `select.bs-select-hidden` → mapping valeur ↔ texte.

**Submit** : `a#submitQuestion` (identique autres IPSOS).

**⚠️ Points critiques** :
- Sélectionner MOIS avant ANNÉE (ou vérifier l'ordre Wicket) — le changement d'année peut reset le mois.
- `selectpicker({mobile: false})` = Bootstrap Select initialisé → le select natif est masqué dès le chargement.
- ID du select contient un compteur hexagonal (`months18`, `years17`) — non stable entre sessions. Cibler par `name` pattern.

**Plateformes** : IPSOS Cortex

---

### FAMILLE Q — `text_decipher_standard` *(Decipher)*
**Description** : Input texte simple Decipher (`type="text"`, `type="number"`). Famille minimale : pas de composant framework, HTML natif. Utilisé pour les questions ouvertes numériques (red herring math, âge, codes postaux...).

**Signature HTML** :
```html
<div class="question number" id="question_[VARNAME]" role="radiogroup">
  <h1 class="question-text"><p>6 + 6 = ??</p></h1>
  <div class="answers answers-list">
    <div class="element">
      <input type="text" name="ans[N].0.0" id="ans[N].0.0" value="" size="2" class="input text-input">
    </div>
  </div>
</div>
```

**Critères de détection** : `form#primary` (Decipher) + `div.question.number[id^="question_"]` + `input.text-input[name^="ans"]` + `input#btn_continue`.

**Interaction** : `clear()` + `send_keys(answer)` sur `input.text-input[name^="ans"]`.

**Extraction** :
- Texte question : `h1.question-text` (texte brut)
- Champ : `input.text-input[name]` — le `name` = `ans[surveyId].[row].[col]`
- Valeur attendue : extraite par LLM à partir du texte de la question

**Submit** : `input#btn_continue[type=submit]` (identique Famille F Decipher).

**⚠️ Points critiques** :
- `class="hasError"` sur `div.question` si erreur de validation (ex. format non numérique).
- `size="2"` indique un champ court (1-2 chiffres) — adapter la longueur de la réponse.
- `_v2_counter` token anti-double-submit présent.

**Plateformes** : Decipher / FocusVision

---

### FAMILLE R — `mixed_rsch_page` *(RSCH / Custom)*
**Description** : Page RSCH (système PHP custom de type japonais) avec **N questions hétérogènes sur une seule page** (radio + texte + select natif). Submit unique pour toutes les questions de la page. Chaque question est identifiée par un code `SCN` (`sc1`, `sc2`, `sc3`...).

**Signature HTML** :
```html
<form id="mainForm" action="./index.php" method="post">
  <!-- Radio (SC1) -->
  <div class="question_default" data-survey-uid="c0">
    <input type="radio" name="sc1" id="sc1-1" value="1">
    <label for="sc1-1">Homme</label>
  </div>
  <!-- Texte (SC2) -->
  <input type="text" name="sc2_1" maxlength="2">
  <!-- Select natif (SC3) -->
  <select name="sc3">
    <option value="">--- </option>
    <option value="1">Auvergne-Rhône-Alpes</option>
  </select>
  <!-- Submit -->
  <input id="btnsmall" type="button" class="enterButton submitButton" value=">>" onclick="return clickCheck('next_button')">
</form>
```

**Critères de détection** : `body.fontF` + `form#mainForm[action="./index.php"]` + `div.question_default[data-survey-uid]` + `input#btnsmall.enterButton`.

**Sous-types par question** :
| Type | Signature | Interaction |
|---|---|---|
| Radio | `input[type=radio][name="sc[N]"][id="sc[N]-[M]"]` + `label[for]` | `.click()` sur input ou label |
| Texte | `input[type=text][name="sc[N]_1"]` | `clear()` + `send_keys()` |
| Select natif | `select[name="sc[N]"]` + `option[value]` | Selenium `Select().select_by_value()` |

**Extraction** : Itérer sur `div.question_default` → lire `div.content_note.note` (texte question) → détecter type via présence de radio/text/select dans `div.answer`.

**Submit** : `input#btnsmall[type=button]` — déclenche `clickCheck('next_button')` côté JS.

**⚠️ Points critiques** :
- **Page entière soumise en une fois** — répondre à toutes les questions avant submit.
- `input[name="sc[N]_count"]` (hidden) indique le nb d'options pour les radios.
- `oncontextmenu="return false"` + `oncopy="return false"` = protection anti-scraping côté client. N'affecte pas Selenium.
- Pas de AJAX — rechargement page entière.

**Plateformes** : RSCH (plateforme custom, probablement Shibuya Data Count ou équivalent)

---

### FAMILLE S — `navigation_instructions` *(CloudResearch Sentry)*
**Description** : Page d'instructions sans question à remplir. L'unique action est de cliquer le bouton CTA de navigation. Pattern applicable à toute page "instructions only" détectée par l'absence d'inputs de réponse.

**Signature HTML** :
```html
<div id="app" data-v-app="">
  <div id="sentry" class="flex flex-col">
    <div class="instructions-card">
      <div class="ql-editor instruction"><!-- texte instructions --></div>
    </div>
    <button class="next next-button" type="button">Suivant →</button>
  </div>
</div>
```

**Critères de détection** : `div#app[data-v-app]` + `div#sentry` + `div.instructions-card` + **absence** de tout `input`, `select`, `textarea` dans le contenu principal.

**Interaction** : Cliquer directement `button.next-button.next[type=button]` (ou `button[class*="next-button"]`).

**Extraction** : Lire `div.ql-editor.instruction` pour les instructions (contexte LLM uniquement, pas de réponse à générer).

**Submit** : Identique au clic CTA — `button.next-button`.

**⚠️ Points critiques** :
- **Aucune réponse à générer** — juste cliquer "Suivant".
- `reCAPTCHA` présent (`grecaptcha-badge`) — pas de bypass nécessaire si pas d'action suspecte.
- Distinguer de CloudResearch avec questions : absence de `div[class*="question"]` ou `input[type=radio]`.

**Plateformes** : CloudResearch Sentry

---

### FAMILLE T — `consent_grx_nextjs` *(GRX / Cint)*
**Description** : Page de consentement GRX (Next.js, préfixes CSS `grx-`). Deux actions mutuellement exclusives : **Accepter** (`button#gtm-agree-button`) ou **Refuser** (`a#gtm-disagree-button`). Refuser = abandon de l'enquête.

**Signature HTML** :
```html
<div id="__next">
  <div class="grx-bg-background grx-h-screen">
    <button class="grx-rounded grx-bg-primary" id="gtm-agree-button">
      Accepter et continuer
    </button>
    <a href="#" id="gtm-disagree-button">
      Refuser et quitter l'enquête
    </a>
  </div>
</div>
```

**Critères de détection** : `div#__next` + `div[class*="grx-"]` + `button#gtm-agree-button` + `a#gtm-disagree-button`.

**Interaction** : Cliquer `button#gtm-agree-button` (toujours accepter pour continuer l'enquête).

**⚠️ Points critiques** :
- **Cliquer "Accepter"** systématiquement — "Refuser" = fin de session.
- Pas d'input à extraire — page de consentement pure.
- URL contient tokens Cint (`SID=`, `PID=`, `MID=`, `cint_panelist_id=`) → identifiable comme flux Cint.

**Plateformes** : GRX (Cint)

---

## 2. GROUPES D'APPLICATION

| Groupe d'application | Sous-chemin module | DOM(s) concernés | Mécanisme d'interaction |
|---|---|---|---|
| `checkbox.standard` | `input_checkbox.py` → `click_standard_checkbox` | DOM-01, DOM-05 | `.click()` sur `input[type=checkbox]` ou label |
| `checkbox.atm1d` | `input_checkbox.py` → `click_atm1d_tile` | DOM-02 | `.click()` sur `li.sq-atm1d-button` |
| `checkbox.angular_ng` | `input_checkbox.py` → `click_angular_checkbox` | DOM-03 | `.click()` via Selenium (déclenche ng-change) |
| `checkbox.angular_component` | `input_checkbox.py` → `click_angular_checkbox` | DOM-04 | `.click()` sur `input.multi-select-input` ou label |
| `checkbox.confirmit` | `input_checkbox.py` → `click_confirmit_button` | DOM-18 | `.click()` sur `div.cf-answer-button[role=checkbox]` |
| `checkbox.angular_mat_list` | `input_checkbox.py` → `click_mat_list_option` | DOM-19 | `.click()` sur `mat-list-option[id]` + `button#okButton-[ID]` |
| `radio.standard` | `input_radio.py` → `click_standard_radio` | DOM-06, DOM-09, DOM-12, DOM-14, DOM-22 | `.click()` sur `input[type=radio]` ou label |
| `radio.angular_ps` | `input_radio.py` → `click_angular_radio` | DOM-07 | `.click()` sur `label.form-check` wrapping |
| `radio.angular_material` | `input_radio.py` → `click_angular_radio` | DOM-11, DOM-19(radio) | `.click()` sur `label.mdc-label` (MDC) ou `label.mat-radio-label` (non-MDC) |
| `radio.vue_dynata` | `input_radio.py` → `click_vue_dynata_radio` | DOM-13 | `.click()` sur `label.form-label` + attendre bouton v-if |
| `grid.cmix_simple` | `input_matrix.py` → `fill_cmix_simple_grid` | DOM-08 | `.click()` sur `input` par `(questionid, responseId)` |
| `grid.walr_card` | `input_matrix.py` → `fill_walr_card_grid` | DOM-10 | `.click()` sur `.answer-button` dynamique par position |
| `text.ps_open_ended` | `input_text.py` → `fill_ps_textarea` | DOM-15, DOM-16 | `clear()` + `send_keys()` sur `ps-textarea-input textarea` |
| `text.decipher` | `input_text.py` → `fill_decipher_text` | DOM-21 | `clear()` + `send_keys()` sur `input.text-input[name^="ans"]` |
| `text.rsch` | `input_text.py` → `fill_rsch_text` | DOM-22 | `clear()` + `send_keys()` sur `input[name^="sc"]` |
| `slider.ipsos` | `input_slider.py` → `fill_ipsos_slider` | DOM-17 | JS `value` + `dispatchEvent('change')` sur `input.bs-slider` |
| `dropdown.ipsos_bs` | `input_dropdown.py` → `fill_ipsos_bs_select` | DOM-20 | JS `.value` + `dispatchEvent('change')` sur `select.bs-select-hidden` |
| `dropdown.native` | `input_dropdown.py` → `fill_native_select` | DOM-22 | `Select(el).select_by_value()` sur `select[name]` |
| `navigation.instructions` | `survey_navigator.py` → `click_cta_only` | DOM-23 | `.click()` sur `button.next-button.next` |
| `consent.grx` | `survey_navigator.py` → `accept_grx_consent` | DOM-24 | `.click()` sur `button#gtm-agree-button` |
| `submit.anchor` | `input_handler.py` → `submit_via_anchor` | DOM-05, DOM-14, DOM-17, DOM-20 | `a#submitQuestion` |
| `submit.button_next` | `input_handler.py` → `submit_via_button` | DOM-01, DOM-06, DOM-08 | `a#cm-NextButton` |
| `submit.input_continue` | `input_handler.py` → `submit_via_button` | DOM-02, DOM-12, DOM-21 | `input#btn_continue[name=continue]` |
| `submit.button_yellow` | `input_handler.py` → `submit_via_button` | DOM-03 | `button[type=submit].button-yellow` |
| `submit.ps_next` | `input_handler.py` → `submit_via_button` | DOM-04, DOM-07, DOM-15, DOM-16 | `ps-next-button button[data-e2e="next-button"]` |
| `submit.aspnet_continue` | `input_handler.py` → `submit_via_button` | DOM-09 | `input#ctl00_Content_btnContinue[type=submit]` |
| `submit.angular_next` | `input_handler.py` → `submit_via_button` | DOM-11 | `button.next_btn` |
| `submit.vue_dynata` | `input_handler.py` → `submit_vue_dynata` | DOM-13 | Bouton `div.profiler-choice > div.d-grid` (v-if, à confirmer) |
| `submit.walr_next` | `input_handler.py` → `submit_via_button` | DOM-10 | `input#btnNext[type=button]` |
| `submit.confirmit_next` | `input_handler.py` → `submit_via_button` | DOM-18 | `button.cf-navigation-next` |
| `submit.rateandrank_submit` | `input_handler.py` → `submit_via_button` | DOM-19 | `button#survey-submit-button` |
| `submit.rsch_button` | `input_handler.py` → `submit_via_button` | DOM-22 | `input#btnsmall[type=button]` |

---

## 3. CATALOGUE DES DOMs DE RÉFÉRENCE

---

### DOM-01 · `DOM_cmix_intro_consent_checkbox_60552196`

| Attribut | Valeur |
|---|---|
| **Plateforme** | CMIX |
| **Type** | Checkbox — consentement (1 option) |
| **Famille** | A — `checkbox_standard_named` |
| **Application** | `checkbox.standard` + `submit.button_next` |

**Markers** : `body.cm-Survey[ng-app="cmix.tasks"]` · `div.cm-element[data-type="CHECKBOX"]` · `ul.cm-checkbox-response-set` · `input[name="[qId][]"]` (crochets) · `class="filled-in"` sur l'input

**Submit** : `a#cm-NextButton`

**Comportements spéciaux** : `data-hideifvalid="true"` — question disparaît après sélection valide. Navigation AJAX → attendre stabilisation DOM.

---

### DOM-02 · `DOM_decipher_selfserve_atm1d_checkbox_past_participation_fr`

| Attribut | Valeur |
|---|---|
| **Plateforme** | Decipher / FocusVision |
| **Type** | Checkbox multi — ATM1D tiled |
| **Famille** | B — `checkbox_atm1d_tile` |
| **Application** | `checkbox.atm1d` + `submit.input_continue` |

**Markers** : `div.sq-atm1d-widget[data-label]` · `ul.sq-atm1d-buttons.sq-atm1d-tiled` · `li.sq-atm1d-button[data-label][data-index]` · `i.fa-square-o` / `i.fa-check-square-o`

**Submit** : `input#btn_continue[type=submit][name=continue]`

**Comportements spéciaux** : Clic sur `<li>` uniquement. Option exclusive : `data-label="None"`. Sélection pré-existante possible (`sq-atm1d-selected`).

---

### DOM-03 · `DOM_dynata_autoscreener_multi_select_streaming_checkboxes_fr`

| Attribut | Valeur |
|---|---|
| **Plateforme** | Dynata Auto-Screener (AngularJS) |
| **Type** | Checkbox multi — AngularJS |
| **Famille** | C — `checkbox_angular_ng_change` |
| **Application** | `checkbox.angular_ng` + `submit.button_yellow` |

**Markers** : `div.auto-screener[ng-controller="autoScreenerController as vm"]` · `div.parameter-rendered.multi_select` · `input[name^="ms_"][ng-change][checklist-value]`

**Submit** : `button[type=submit].button.button-yellow`

**Comportements spéciaux** : Plusieurs questions par page possibles. Option exclusive par texte uniquement.

---

### DOM-04 · `DOM_purespectrum_multiselect_pathologies_search_checkbox_fr`

| Attribut | Valeur |
|---|---|
| **Plateforme** | PureSpectrum (Angular 19) |
| **Type** | Checkbox multi + recherche |
| **Famille** | D — `checkbox_angular_component` |
| **Application** | `checkbox.angular_component` + `submit.ps_next` |

**Markers** : `ps-root[ng-version]` · `ps-multi-choice-question[qualificationid]` · `input.multi-select-input[data-e2e]` · `div[role=listbox].multi-select-container`

**Submit** : `ps-next-button button[aria-label*="next"]`

**Comportements spéciaux** : Champ recherche `input#search-input`. Options exclusives : `data-e2e="998"` et `data-e2e="999"` / `class="none-of-the-above-input"`. IDs contiennent `-undefined` → préférer `data-e2e`.

---

### DOM-05 · `DOM_ipsos_leisure_activities_checkbox_multi_exclusive_fr`

| Attribut | Valeur |
|---|---|
| **Plateforme** | IPSOS Cortex (Wicket) |
| **Type** | Checkbox multi + option exclusive |
| **Famille** | E — `checkbox_cortex_wicket` |
| **Application** | `checkbox.standard` + `submit.anchor` |

**Markers** : `body.screening-body` · assets `ipsosinteractive.com` · `span.multipleChoice-checkbox` · `input[name*="checkGroup"]` · `input.logic.exclusive`

**Submit** : `a#submitQuestion`

**Comportements spéciaux** : Option exclusive : `class="logic exclusive"`. AJAX indicator `#ajaxLoadingImage`.

---

### DOM-06 · `DOM_cmix_language_selector_radio_60552194`

| Attribut | Valeur |
|---|---|
| **Plateforme** | CMIX |
| **Type** | Radio simple — sélecteur de langue |
| **Famille** | F — `radio_standard_html` (variante CMIX) |
| **Application** | `radio.standard` + `submit.button_next` |
| **Fichier référence** | `DOM_cmix_language_selector_radio_60552194.txt` |

**Markers** :
- `body.cm-Survey[ng-app="cmix.tasks"]`
- `div.cm-element[data-type="RADIO"]` (distingue du checkbox `data-type="CHECKBOX"`)
- `ul.cm-radio-response-set` contenant les options
- Input : `name="[questionId]"` (chiffres seuls, sans `[]`), `id="[qId]_[responseId]"`, `value="[responseId]"`, `class="with-gap"`
- `span.cm-radio-label-text` pour le texte
- `div.cm-response-container.selectedElem` = option sélectionnée

**Submit** : `a#cm-NextButton`

**Comportements spéciaux** : `data-hideifvalid="true"`. Navigation AJAX. **Distinction checkbox/radio** : `data-type` + présence/absence de `[]` dans le `name`.

---

### DOM-07 · `DOM_purespectrum_radio_211`

| Attribut | Valeur |
|---|---|
| **Plateforme** | PureSpectrum (Angular 19) |
| **Type** | Radio simple — genre |
| **Famille** | G — `radio_angular_ps` |
| **Application** | `radio.angular_ps` + `submit.ps_next` |
| **Fichier référence** | `DOM_purespectrum_radio_211.txt` |

**Markers** :
- `ps-root[ng-version]` + `ps-single-choice-question[qualificationid]`
- `div[role=radiogroup]` dans `ps-single-choice`
- `input.form-check-input.handset-choice-view[data-e2e]`
- Label wrapping : `label.form-check.hide-button[for="choice-[n]"]`
- `class="active-bg fw-bold"` sur le label sélectionné

**Submit** : `ps-next-button button[aria-label*="next"]`

**Comportements spéciaux** : `name="[object Object]"` — non stable, ne jamais utiliser. Texte directement dans le `<label>` (pas dans `<span>`). Cliquer sur `<label>` pour l'interaction.

---

### DOM-08 · `DOM_QLEISUREACTIVITIES_CMix_SimpleGrid_LeisureFrequency_FR`

| Attribut | Valeur |
|---|---|
| **Plateforme** | CMIX |
| **Type** | Grille radio — fréquence loisirs |
| **Famille** | J — `grid_cmix_simple` |
| **Application** | `grid.cmix_simple` + `submit.button_next` |
| **Fichier référence** | `DOM_QLEISUREACTIVITIES_CMix_SimpleGrid_LeisureFrequency_FR.txt` |

**Markers** :
- `body.cm-Survey` + `div[data-type="SIMPLE_GRID"]`
- `div.cm-simple-grid > table.cm-simple-grid__table`
- `th.cm-simple-grid__column-header` pour labels colonnes
- `td.cm-simple-grid__row-header > div[data-subquestionname]` pour labels lignes
- `div.cm-radio-input-container.cm-grid-cell[questionid]` pour chaque cellule
- Input : `name="[numericSubQId]"`, `data-parent-id="[numericSubQId]"`, `data-response-id="[responseId]"`, `class="with-gap"`

**Submit** : `a#cm-NextButton`

**Comportements spéciaux** : Chaque ligne est une sous-question indépendante avec son propre `name` numérique. Navigation AJAX → attendre stabilisation.

---

### DOM-09 · `DOM_samplicious_profiler_region_fr_radio_27_options`

| Attribut | Valeur |
|---|---|
| **Plateforme** | Samplicious (Lucid) |
| **Type** | Radio simple — région (27 options) |
| **Famille** | F — `radio_standard_html` (variante Samplicious) |
| **Application** | `radio.standard` + `submit.aspnet_continue` |
| **Fichier référence** | `DOM_samplicious_profiler_region_fr_radio_27_options.txt` |

**Markers** :
- `body#ctl00_supplierBranding` + `form#aspnetForm[action*="Profiler.aspx"]`
- `div.options.js-question-options`
- Input : `type=radio`, `id="option-[n]"`, `name="question_[id]"`, `value="[n]"` (index 1-based)
- Label : `label.radio[for="option-[n]"] > span`
- `input[name="__VIEWSTATE"]` (ASP.NET WebForms)

**Submit** : `input#ctl00_Content_btnContinue[type=submit]` (CSS : `input[name="ctl00$Content$btnContinue"]`)

**Comportements spéciaux** : 27 options → scroller si nécessaire. `value` = index (pas ID sémantique). Submit = rechargement page entière (pas AJAX).

---

### DOM-10 · `DOM_walr_QCGridCheck_French_frequency_grid_radio_fr`

| Attribut | Valeur |
|---|---|
| **Plateforme** | Walr |
| **Type** | Grille radio card-séquentielle — fréquence |
| **Famille** | K — `grid_walr_card_sequential` |
| **Application** | `grid.walr_card` + `submit.walr_next` |
| **Fichier référence** | `DOM_walr_QCGridCheck_French_frequency_grid_radio_fr.txt` |

**Markers** :
- `form#rsForm[action*="./c?rs="]`
- `table.cTable.rsSingleGrid.rsProcessedGrid`
- `th.cCellRowText[id^="r_"]` pour labels lignes
- `th.cCellHeader[id^="h_"]` pour labels colonnes
- `input.cRadio[onclick*="clearAll"]`
- `input#btnNext[onclick*="WebForm_DoPostBackWithOptions"]`

**Submit** : `input#btnNext[type=button]`

**Comportements spéciaux** : Table cachée par CSS. JS crée `.answer-button` dynamiques par ligne (un à la fois). IDs radio dupliqués dans le DOM — `getElementById` inopérant. `#btnNext` peut s'auto-cliquer quand toutes les lignes sont répondues. reCAPTCHA présent — ne pas aller trop vite.

---

### DOM-11 · `DOM_angular_material_radio_fruits`

| Attribut | Valeur |
|---|---|
| **Plateforme** | Angular Material (interne/custom) |
| **Type** | Radio simple — question logique |
| **Famille** | H — `radio_angular_material` |
| **Application** | `radio.angular_material` + `submit.angular_next` |
| **Fichier référence** | `DOM_angular_material_radio_fruits.txt` |

**Markers** :
- `app-root[ng-version]` + `app-survey`
- `mat-radio-group[role=radiogroup]`
- `mat-radio-button.mat-mdc-radio-button.theme-radio`
- `input.mdc-radio__native-control[tabindex="-1"]` (non sélectionné) / `[tabindex="0"]` (sélectionné)
- `label.mdc-label[for="mat-radio-[n]-input"]`
- Option sélectionnée : `mat-radio-button.mat-mdc-radio-checked`

**Submit** : `button.next_btn[translate="srvyPrcs.nextBtn"]`

**Comportements spéciaux** : Cliquer sur `label.mdc-label` (plus fiable que l'input). `translate="srvyPrcs.nextBtn"` = framework i18n propre — indicateur de plateforme custom.

---

### DOM-12 · `DOM_decipher_intro_consent_radio_yesno_en`

| Attribut | Valeur |
|---|---|
| **Plateforme** | Decipher (standard, pas ATM1D) |
| **Type** | Radio simple — consentement oui/non |
| **Famille** | F — `radio_standard_html` (variante Decipher) |
| **Application** | `radio.standard` + `submit.input_continue` |
| **Fichier référence** | `DOM_decipher_intro_consent_radio_yesno_en.txt` |

**Markers** :
- `body.survey-page` + `div#survey.survey-container`
- `div.question.radio[role=radiogroup]` (présence de `role=radiogroup` sur la div question)
- **Absence** de `div.sq-atm1d-widget` → confirme Decipher standard (pas ATM1D)
- Input : `name="ans[N].[row].[col]"` (dots notation), `class="input radio"`
- `div.element.clickableCell` — cellule entière cliquable
- `span.cell-text > label[for] > p` pour le texte

**Submit** : `input#btn_continue[type=submit][name=continue]`

**Comportements spéciaux** : `class="hasError"` sur la div question si non répondu. `div.clickableCell` → clic sur la cellule entière fonctionne. `_v2_counter` hidden input anti-double-submit.

---

### DOM-13 · `DOM_dynata_profiler_consent_radio_oui_non_fr`

| Attribut | Valeur |
|---|---|
| **Plateforme** | Dynata Profiler (Vue 3) |
| **Type** | Radio simple — consentement collecte données |
| **Famille** | I — `radio_vue_dynata_profiler` |
| **Application** | `radio.vue_dynata` + `submit.vue_dynata` |
| **Fichier référence** | `DOM_dynata_profiler_consent_radio_oui_non_fr.txt` |

**Markers** :
- `div#app[data-v-app]`
- `div#profiler-choice`
- `div.row.single-choice-container`
- `input.form-check-input.btn-check.choice-input[type=radio]`
- `label.form-label[for="single_choice_[float]_[n]"] > span`

**Submit** : Bouton dans `div.profiler-choice > div.d-grid` (v-if Vue — absent au chargement). **Comportement à confirmer en prod** : auto-advance ou attendre apparition du bouton.

**Comportements spéciaux** : `name` aléatoire — **ne jamais utiliser**. Evidon banner (`div#_evidon-barrier-wrapper`) peut bloquer → cliquer "Agree and Access Site" (`button._evidon-banner-acceptbutton`) si présent.

---

### DOM-14 · `DOM_ipsos_gender_radio_fr`

| Attribut | Valeur |
|---|---|
| **Plateforme** | IPSOS Cortex (Wicket) |
| **Type** | Radio simple — genre |
| **Famille** | F — `radio_standard_html` (variante IPSOS Cortex) |
| **Application** | `radio.standard` + `submit.anchor` |
| **Fichier référence** | `DOM_ipsos_gender_radio_fr.txt` |

**Markers** :
- `body.screening-body` + assets `ipsosinteractive.com`
- `div[role=tablist][aria-multiselectable=true]#radioGroup[N]`
- `div.radio > label > input[type=radio]` (pas de `span.multipleChoice-checkbox` — pattern légèrement différent du checkbox IPSOS)
- Input : `name="questionContainer:...:radioGroup"` (Wicket, contient `radioGroup`), `value="radio[N]"`
- `span.font-weight-light.text-hard-light` pour le texte

**Submit** : `a#submitQuestion`

**Comportements spéciaux** : Même submit que checkbox IPSOS. `[name*="radioGroup"]` comme sélecteur partiel robuste. AJAX indicator `#ajaxLoadingImage`.

---

## 4. ARBRE DE DÉCISION

```
ROOT : Type d'input et contexte détectés dans le DOM ?
│
├─► input[type=checkbox] présent ?
│   │
│   ├─► body.cm-Survey ET ng-app="cmix.tasks" ?
│   │   └─► FAMILLE A | CMIX checkbox | input[name*="[]"].filled-in | submit: a#cm-NextButton
│   │
│   ├─► body.screening-body ET assets ipsosinteractive ?
│   │   └─► FAMILLE E | IPSOS Cortex | input[name*="checkGroup"] | submit: a#submitQuestion
│   │       └─► Vérifier input.logic.exclusive → gestion option exclusive obligatoire
│   │
│   ├─► ps-root[ng-version] ET ps-multi-choice-question[qualificationid] ?
│   │   └─► FAMILLE D | PureSpectrum | input.multi-select-input[data-e2e] | submit: ps-next-button button
│   │       └─► Vérifier data-e2e="998/999" et class="none-of-the-above-input"
│   │       └─► Vérifier input#search-input → champ recherche présent
│   │
│   ├─► ng-controller="autoScreenerController" ?
│   │   └─► FAMILLE C | Dynata Auto-Screener | input[name^="ms_"][checklist-value] | submit: button.button-yellow
│   │
│   └─► div.sq-atm1d-widget ET ul.sq-atm1d-buttons ?
│       └─► FAMILLE B | Decipher ATM1D | li.sq-atm1d-button[data-label] | submit: input#btn_continue
│           └─► Clic sur <li>, PAS sur les <input> internes
│
├─► input[type=radio] présent ?
│   │
│   ├─► body.cm-Survey ET ng-app="cmix.tasks" ?
│   │   ├─► div[data-type="SIMPLE_GRID"] présent ?
│   │   │   └─► FAMILLE J | CMIX Simple Grid | div.cm-grid-cell[questionid] | submit: a#cm-NextButton
│   │   └─► div[data-type="RADIO"] présent ?
│   │       └─► FAMILLE F (CMIX) | ul.cm-radio-response-set | submit: a#cm-NextButton
│   │
│   ├─► body.screening-body ET assets ipsosinteractive ?
│   │   └─► FAMILLE F (IPSOS) | input[name*="radioGroup"] | submit: a#submitQuestion
│   │
│   ├─► ps-root[ng-version] ET ps-single-choice-question[qualificationid] ?
│   │   └─► FAMILLE G | PureSpectrum | input.handset-choice-view[data-e2e] | submit: ps-next-button button
│   │
│   ├─► app-root[ng-version] ET mat-radio-group ?
│   │   └─► FAMILLE H | Angular Material | input.mdc-radio__native-control | submit: button.next_btn
│   │
│   ├─► div#app[data-v-app] ET div#profiler-choice ?
│   │   └─► FAMILLE I | Dynata Profiler Vue | input.choice-input | submit: attendre bouton v-if
│   │
│   ├─► body.survey-page ET div#survey.survey-container ?
│   │   └─► Vérifier ABSENCE de sq-atm1d-widget (sinon → entrée checkbox ATM1D)
│   │       └─► FAMILLE F (Decipher) | div.question.radio[role=radiogroup] | input[name^="ans"] | submit: input#btn_continue
│   │
│   ├─► body#ctl00_supplierBranding ET form#aspnetForm[action*="Profiler.aspx"] ?
│   │   └─► FAMILLE F (Samplicious) | input[name^="question_"] | submit: input#ctl00_Content_btnContinue
│   │
│   └─► form#rsForm[action*="./c?rs="] ET table.cTable.rsSingleGrid ?
│       └─► FAMILLE K | Walr Card Grid | .answer-button dynamique | submit: input#btnNext
│
└─► Aucune famille reconnue → LOG "dom_unclassified" + abandon contrôlé (pas de retry)
```

---

## 5. TABLEAU RÉCAPITULATIF

| # | Nom DOM | Plateforme | Type question | Famille | Groupe application | Submit sélecteur |
|---|---|---|---|---|---|---|
| 01 | `DOM_cmix_intro_consent_checkbox_60552196` | CMIX | Checkbox consentement | A | `checkbox.standard` | `a#cm-NextButton` |
| 02 | `DOM_decipher_selfserve_atm1d_checkbox_past_participation_fr` | Decipher | Checkbox multi ATM1D | B | `checkbox.atm1d` | `input#btn_continue` |
| 03 | `DOM_dynata_autoscreener_multi_select_streaming_checkboxes_fr` | Dynata Auto-Screener | Checkbox multi AngularJS | C | `checkbox.angular_ng` | `button.button-yellow[type=submit]` |
| 04 | `DOM_purespectrum_multiselect_pathologies_search_checkbox_fr` | PureSpectrum | Checkbox multi + recherche | D | `checkbox.angular_component` | `ps-next-button button` |
| 05 | `DOM_ipsos_leisure_activities_checkbox_multi_exclusive_fr` | IPSOS Cortex | Checkbox multi + exclusive | E | `checkbox.standard` | `a#submitQuestion` |
| 06 | `DOM_cmix_language_selector_radio_60552194` | CMIX | Radio simple | F | `radio.standard` | `a#cm-NextButton` |
| 07 | `DOM_purespectrum_radio_211` | PureSpectrum | Radio simple | G | `radio.angular_ps` | `ps-next-button button` |
| 08 | `DOM_QLEISUREACTIVITIES_CMix_SimpleGrid_LeisureFrequency_FR` | CMIX | Grille radio simple | J | `grid.cmix_simple` | `a#cm-NextButton` |
| 09 | `DOM_samplicious_profiler_region_fr_radio_27_options` | Samplicious | Radio 27 options | F | `radio.standard` | `input#ctl00_Content_btnContinue` |
| 10 | `DOM_walr_QCGridCheck_French_frequency_grid_radio_fr` | Walr | Grille radio card-séq. | K | `grid.walr_card` | `input#btnNext` |
| 11 | `DOM_angular_material_radio_fruits` | Angular Material | Radio simple | H | `radio.angular_material` | `button.next_btn` |
| 12 | `DOM_decipher_intro_consent_radio_yesno_en` | Decipher | Radio simple (standard) | F | `radio.standard` | `input#btn_continue` |
| 13 | `DOM_dynata_profiler_consent_radio_oui_non_fr` | Dynata Profiler (Vue 3) | Radio simple | I | `radio.vue_dynata` | Bouton v-if (à confirmer) |
| 14 | `DOM_ipsos_gender_radio_fr` | IPSOS Cortex | Radio simple | F | `radio.standard` | `a#submitQuestion` |

---

## 6. POINTS CRITIQUES PAR PLATEFORME

### CMIX *(DOMs 01, 06, 08)*
- **AJAX navigation** : Attendre stabilisation DOM (disparition `div.cm-loader-wrapper`) après `#cm-NextButton`.
- **hideifvalid** : `data-hideifvalid="true"` — question disparaît après sélection valide.
- **Radio vs Checkbox** : `data-type="RADIO"` vs `"CHECKBOX"` sur `.cm-element`. `name` radio = `[qId]` seul, `name` checkbox = `[qId][]` (crochets).
- **Simple Grid** : Chaque ligne = sous-question propre (`data-parent-id`). Cibler par `div.cm-grid-cell[questionid]`.

---

### Decipher *(DOMs 02, 12)*
- **ATM1D vs Standard** : Tester `div.sq-atm1d-widget` en premier. Absence → standard.
- **ATM1D** : Cliquer sur `<li>`. Deux `<input>` par tile — ne jamais les cibler directement.
- **Standard** : `div.clickableCell` cliquable en entier. `_v2_counter` hidden = anti-double-submit.

---

### Dynata *(DOMs 03, 13)*
- **Auto-Screener (AngularJS)** : `ng-change` nécessite un clic Selenium natif. Plusieurs questions par page.
- **Profiler (Vue 3)** : `name` aléatoire — jamais utiliser. Bouton submit conditionnel `v-if` — attendre après sélection (timeout 2-3s). Evidon banner → `button._evidon-banner-acceptbutton` si présent.

---

### PureSpectrum *(DOMs 04, 07)*
- **IDs non fiables** : Contiennent `-undefined` (multi) ou sont séquentiels (single). Préférer `data-e2e`.
- **name non fiable (single-choice)** : `name="[object Object]"` — ne jamais utiliser.
- **Radio vs Checkbox** : `ps-single-choice-question` vs `ps-multi-choice-question`. Submit identique.
- **Options exclusives** (multi) : `data-e2e="998/999"`. Vider le champ recherche avant interaction.

---

### IPSOS Cortex *(DOMs 05, 14)*
- **Submit = `<a>`** : `a#submitQuestion`, pas `button` ni `input[type=submit]`.
- **Name Wicket** : Utiliser `[name*="checkGroup"]` / `[name*="radioGroup"]` (match partiel).
- **AJAX indicator** : Attendre disparition de `img#ajaxLoadingImage` après submit.
- **Option exclusive checkbox** : `input.logic.exclusive` → décocher tout le reste avant de cocher l'exclusive.

---

### Samplicious *(DOM 09)*
- **ASP.NET WebForms** : Submit = rechargement page entière. `__VIEWSTATE` présent.
- **Valeur = index** : `value="1"` = 1ère option. Mapping texte ↔ valeur dans le DOM via `label.radio > span`.
- **Submit selector CSS** : `input[name="ctl00$Content$btnContinue"]` (les `$` remplacent les `.` de l'ID ASP.NET).
- **27+ options** : Scroller avant de cliquer si option hors viewport.

---

### Angular Material *(DOM 11)*
- **tabindex="-1"** sur les inputs non sélectionnés — ne pas cibler par tabindex.
- **Cliquer sur `label.mdc-label`** plus fiable que l'input natif pour déclencher le binding.
- **Plateforme custom** : `translate="srvyPrcs.nextBtn"` = i18n propriétaire. Pas de panel externe connu.

---

### Walr *(DOM 10)*
- **Table cachée par CSS** : Interagir uniquement via `.answer-button` dynamiques.
- **IDs radio dupliqués** : `getElementById` inopérant. Cibler via `querySelectorAll('input.cRadio')` sur le `<tr>` parent.
- **Card séquentielle** : Ordre des `.answer-button` = ordre des colonnes headers. Vérifier l'ordre.
- **btnNext auto-click** : Peut se déclencher automatiquement après dernière réponse — vérifier si navigation déjà effectuée avant clic manuel.
- **reCAPTCHA** (`grecaptcha-badge`) : Ne pas aller trop vite entre les interactions.

---

### DOM-15 · `DOM_purespectrum_open_ended_textarea_next_fr`

| Attribut | Valeur |
|---|---|
| **Plateforme** | PureSpectrum (Angular 19) |
| **Type** | Texte libre — textarea open-ended |
| **Famille** | L — `text_ps_open_ended` |
| **Application** | `text.ps_open_ended` + `submit.ps_next` |

**Markers** : `ps-root[ng-version="19.2.11"]` · `ps-open-ended-question[qualificationid="4377"]` · `ps-textarea-input` · `textarea.form-control`

**Submit** : `ps-next-button ps-button[data-e2e="next-button"] button`

**Comportements spéciaux** : Validation "≥ 5 mots". CookieYes banner présent mais état fermé dans cette capture.

---

### DOM-16 · `DOM_purespectrum_open_ended_textarea_next_cookieyes_fr`

| Attribut | Valeur |
|---|---|
| **Plateforme** | PureSpectrum (Angular 19) |
| **Type** | Texte libre — textarea open-ended (avec CookieYes actif) |
| **Famille** | L — `text_ps_open_ended` |
| **Application** | `text.ps_open_ended` + `submit.ps_next` |

**Markers** : Identiques DOM-15. Différence : CookieYes overlay `div.cky-notice` visible.

**Overlay** : `button[data-cky-tag="accept-button"]` → cliquer avant interaction avec la textarea.

**Comportements spéciaux** : Contenu de survey 100% identique à DOM-15. Même famille, même extraction.

---

### DOM-17 · `DOM_ipsos_wicket_slider_likert_multi_fr`

| Attribut | Valeur |
|---|---|
| **Plateforme** | IPSOS Cortex (Wicket) |
| **Type** | Slider Likert multi-statements (7 sliders, échelle 1-5) |
| **Famille** | M — `slider_ipsos_wicket` |
| **Application** | `slider.ipsos` + `submit.anchor` |

**Markers** : `form#questionForm[N]` · `div#slider-question-row` · `input.slider-form-field.bs-slider[data-slider-ticks="[1,2,3,4,5]"]` · `a#submitQuestion`

**Extraction** : 7 sliders. Statements : `h3.question-title-frontend`. Ancres polaires : `span.pull-left.text-warning` / `span.pull-right.text-success`.

**Submit** : `a#submitQuestion`

**Comportements spéciaux** : Valeurs pré-remplies (`data-slider-value="5"`). Interagir sur tous les sliders avant submit.

---

### DOM-18 · `DOM_confirmit_children_household_multi_exclusive_fr`

| Attribut | Valeur |
|---|---|
| **Plateforme** | Confirmit / Forsta |
| **Type** | Checkbox multi ARIA — avec option exclusive |
| **Famille** | N — `checkbox_confirmit_answer_button` |
| **Application** | `checkbox.confirmit` + `submit.confirmit_next` |

**Markers** : `div.cf-question.cf-question--answer-buttons-multi#QHIDSAMPLEChildren` · `div.cf-answer-button[role=checkbox][aria-checked]` · `div.cf-answer-button.cf-answer-button--exclusive` · `button.cf-navigation-next`

**Submit** : `button.cf-navigation-next` (contient img "Suivant", pas de texte)

**Comportements spéciaux** : Option exclusive (`"Pas d'enfants dans le foyer"`) détectable par class `cf-answer-button--exclusive` ET `"isExclusive":true` dans `window.Confirmit` JSON. Option 4 pré-sélectionnée (`aria-checked="true"`).

---

### DOM-19 · `DOM_rateandrank_multi_checklists_radio_autres_text_fr`

| Attribut | Valeur |
|---|---|
| **Plateforme** | RateAndRank (Angular Material 14) |
| **Type** | Page mixte : 3x MULTI (mat-selection-list) + 2x PULLDOWN (mat-radio-group) |
| **Famille** | O — `checkbox_angular_mat_list` (MULTI) + H-variant (PULLDOWN) |
| **Application** | `checkbox.angular_mat_list` + `radio.angular_material` + `submit.rateandrank_submit` |

**Markers** : `app-root[ng-version="14.1.0"]` · `div[data-question-type="MULTI"]` · `mat-selection-list[id^="multi-question-"]` · `mat-list-option[role=option][id^="answer-"]` · `button#survey-submit-button`

**Questions MULTI** :
- Q-0 (id=806929) : 7 options occasions port vêtements → bouton `#okButton-806929` présent (validé)
- Q-1 (id=806930) : 31 options marques + "Autres" text input `#mat-input-0`
- Q-2 (id=806931) : 10 options lieux d'achat

**Questions PULLDOWN (radio)** :
- Q-3 (id=806932) : `mat-radio-group#radio-question-3` — 3 options intensité
- Q-4 (id=806933) : `mat-radio-group#radio-question-4` — 5 options qualité

**Submit** : `button#survey-submit-button` (disabled tant que tous non répondus)

**Comportements spéciaux** : reCAPTCHA présent. Bouton "Ok" intermédiaire par bloc MULTI. "Autres" = sélectionner option + remplir `input#mat-input-0`.

---

### DOM-20 · `DOM_ipsos_birthdate_dropdown_fr`

| Attribut | Valeur |
|---|---|
| **Plateforme** | IPSOS Cortex (Wicket) |
| **Type** | Dropdowns date — Mois + Année (Bootstrap Select) |
| **Famille** | P — `dropdown_ipsos_wicket_bs_select` |
| **Application** | `dropdown.ipsos_bs` + `submit.anchor` |

**Markers** : `body.screening-body` · `select.form-control.bs-select-hidden[id="months18"]` + `select[id="years17"]` · `div.bootstrap-select` · Wicket AJAX `c="months18"`, `c="years17"` · `a#submitQuestion`

**Extraction** : `option[value]` du select caché (ex. mois `value="6"` → "Juillet", valeur 0-indexée).

**Submit** : `a#submitQuestion`

**Comportements spéciaux** : Mois 0-indexé (value="6" = Juillet). Changement d'année déclenche Wicket AJAX → attendre stabilisation avant sélection suivante.

---

### DOM-21 · `DOM_decipher_red_herring_math_text_fr`

| Attribut | Valeur |
|---|---|
| **Plateforme** | Decipher / FocusVision |
| **Type** | Texte numérique — question red herring math |
| **Famille** | Q — `text_decipher_standard` |
| **Application** | `text.decipher` + `submit.input_continue` |

**Markers** : `form#primary[action*="/survey/selfserve/"]` · `div#question_Red_Herring_Math.question.number.hasError` · `input.text-input[name="ans383.0.0"]` · `input#btn_continue`

**Submit** : `input#btn_continue[type=submit]`

**Comportements spéciaux** : `class="hasError"` présent = état d'erreur (réponse manquante). `size="2"` = réponse courte (1-2 chiffres). Exemple : "6 + 6 = ??" → répondre "12".

---

### DOM-22 · `DOM_rsch_demographics_fr`

| Attribut | Valeur |
|---|---|
| **Plateforme** | RSCH (PHP custom) |
| **Type** | Page demographics mixte : SC1 radio + SC2 texte + SC3 select |
| **Famille** | R — `mixed_rsch_page` |
| **Application** | `radio.standard` + `text.rsch` + `dropdown.native` + `submit.rsch_button` |

**Markers** : `body.fontF` · `form#mainForm[action="./index.php"]` · `div.question_default[data-survey-uid]` · `input[type=radio][name^="sc"]` · `input[type=text][name^="sc"]` · `select[name^="sc"]` · `input#btnsmall.enterButton.submitButton`

**Sous-questions** :
- SC1 (genre) : radio `name="sc1"`, `id="sc1-[N]"`, `value="1|2|3"` + `label[for]`
- SC2 (âge) : text `name="sc2_1"` `maxlength="2"` (saisie "XX ans")
- SC3 (région) : select `name="sc3"` avec 14 `option[value="1..14"]`

**Submit** : `input#btnsmall[type=button]` onclick → `clickCheck('next_button')`

**Comportements spéciaux** : Soumettre toutes les questions ensemble. Rechargement page entière. `oncontextmenu/oncopy` bloqués (pas d'impact Selenium).

---

### DOM-23 · `DOM_cloudresearch_sentry_instructions_cta_suivant_fr`

| Attribut | Valeur |
|---|---|
| **Plateforme** | CloudResearch Sentry (Vue 3) |
| **Type** | Page instructions — pas de question, CTA uniquement |
| **Famille** | S — `navigation_instructions` |
| **Application** | `navigation.instructions` |

**Markers** : `div#app[data-v-app]` · `div#sentry` · `div.instructions-card` · `button.next-button.next[type=button]` · absence de `input[type=radio/checkbox]`

**Submit** / Action : `button.next-button.next` ("Suivant →")

**Comportements spéciaux** : Aucune réponse à générer. reCAPTCHA invisible (`grecaptcha-badge`). Même stack Vue 3 que DOM-13 (Dynata Profiler) mais pattern différent.

---

### DOM-24 · `DOM_grx_consent_accepter_et_continuer_refuser_quitter_fr`

| Attribut | Valeur |
|---|---|
| **Plateforme** | GRX / Cint (Next.js) |
| **Type** | Page consentement — Accepter ou Refuser |
| **Famille** | T — `consent_grx_nextjs` |
| **Application** | `consent.grx` |

**Markers** : `div#__next` · `div[class*="grx-"]` · `button#gtm-agree-button` "Accepter et continuer" · `a#gtm-disagree-button` "Refuser et quitter l'enquête"

**Action** : Cliquer `button#gtm-agree-button` (toujours).

**Comportements spéciaux** : URL contient tokens Cint (`cint_panelist_id`, `SID`, `PID`, `MID`). Aucune réponse à générer. Refuser = abandon de session.



---

*Fin du fichier — Version 3.0.0*