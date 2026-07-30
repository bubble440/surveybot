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

## MODULE TRANSVERSAL : SWITCH_TO_FRAME_CHAIN — ARGUMENT frame_chain MANQUANT + ROBUSTESSE THROW() DANS LE CONTEXT MANAGER

### analyze_dom — argument frame_chain manquant sur _extract_focusvision_answers_list_groups (2e point d'appel)
Fichier : Survey/dom_analyzer.py
Emplacement : bloc de repli (ligne ~4179), à l'intérieur de `with switch_to_frame_chain(driver, []) as ok:`.
Bug corrigé : l'appel `_extract_focusvision_answers_list_groups(driver)` ne transmettait pas l'argument
positionnel requis `frame_chain`, provoquant un `TypeError` systématique dès que ce chemin de repli était
atteint (n'importe quelle plateforme, pas spécifique à Ipsos/FocusVision). Ce `TypeError`, levé pendant que
le générateur `switch_to_frame_chain` était suspendu au `yield`, se propageait sous forme de
`RuntimeError: generator didn't stop after throw()` (contextlib), masquant totalement l'erreur d'origine.
Correction : ajout de `frame_chain=None` à cet appel (contexte racine à ce point du code, cohérent avec les
autres appels du même bloc de repli déjà en `frame_chain=None`).
Patterns exclus : le 1er point d'appel (ligne ~4166, bloc `best_chain`) passait déjà correctement
`frame_chain=chain` — non concerné, non modifié.

### switch_to_frame_chain — séparation résolution de chaîne / bloc yield (throw-safety)
Fichier : Survey/frame_utils.py
Problème résolu : la résolution de la chaîne d'iframes (navigation dans `child_frames`) était auparavant
susceptible d'être fusionnée avec le `try/except` entourant le `yield` du context manager. Si une exception
survient dans le corps du `with` (ex. le `TypeError` ci-dessus), Python la relance dans le générateur via
`throw()` au point du `yield` ; un `except Exception` trop large à cet endroit peut intercepter cette
relance et, en tentant de continuer/yield à nouveau, provoque `RuntimeError("generator didn't stop after
throw()")` côté contextlib — masquant l'exception réelle du corps du `with`.
Correction : la résolution de la chaîne (recherche des `child_frames`, calcul de `ok`/`target`) est
effectuée entièrement AVANT le `yield`, dans son propre `try/except Exception: ok = False` isolé. Le bloc
`try/finally` autour du `yield` ne contient plus que `_reset()` en `finally` — aucun `except` autour du
`yield` lui-même, donc toute exception levée dans le corps du `with` remonte immédiatement et proprement.
Patterns couverts :
- Tout appel à `switch_to_frame_chain` où le corps du `with` peut lever une exception (signature manquante,
  erreur DOM, etc.) — l'exception d'origine reste visible dans la stack trace, plus de `RuntimeError`
  masquante.
Patterns exclus :
- Aucun changement du comportement fonctionnel (résolution de chaîne, mise à jour de `_current_frame`,
  retour au contexte racine via `_reset()` en sortie) — patch défensif sur la gestion d'exception uniquement.

Diagnostic associé : le symptôme observable avant ce patch était `RuntimeError: generator didn't stop after
throw()` dans la boucle attach (main.py::run_attach_takeover), sans lien apparent avec un extracteur ou une
plateforme précise, et sans stack trace exploitable (le bloc `except Exception` de la boucle attach
n'affichait que type+message). Un patch de traceback complet a permis de révéler le `TypeError` réel sous-
jacent, confirmant que ce n'était pas un problème de connexion CDP partagée ou de concurrence entre threads,
mais un simple désalignement d'arguments à un point d'appel après évolution de signature.

Statut : patch validé.

### switch_to_frame_chain — assignation inconditionnelle de _current_frame (hasattr gate supprimé)
Fichier : Survey/frame_utils.py
Bug corrigé : `_reset()` et la mise à jour post-switch de `_current_frame` étaient conditionnées par
`hasattr(driver, "_current_frame")`. Sur un driver Page Playwright native obtenu via `connect_over_cdp`
(flux d'attache à un navigateur déjà lancé, main.py::run_attach_takeover), cet attribut n'existait jamais
au préalable → la condition était toujours fausse → `_current_frame` n'était jamais créé, et
`getattr(driver, "_current_frame", driver)` retombait en permanence sur le document racine dans tous les
modules appelants (dom_frame_selector.py, dom_classifier.py), quel que soit le chain sélectionné.
Correction : assignation inconditionnelle (`try: driver._current_frame = ... except Exception: pass`),
Playwright Page/Frame supportant l'attribution dynamique d'attributs.
Patterns couverts :
- Tout driver Page Playwright natif sans shim préexistant (attache CDP à un navigateur déjà lancé).
Patterns exclus :
- Aucun changement pour un driver qui exposait déjà `_current_frame` au préalable (comportement identique).

### analyze_dom / dom_extractors — passage explicite du contexte résolu (_ctx) aux extracteurs
Fichier : Survey/dom_analyzer.py (bloc principal, ligne ~4156-4186)
Constat : Page.evaluate/query_selector*/query_selector_all ignorent l'attribut `_current_frame` posé par
switch_to_frame_chain — seules les fonctions qui résolvent explicitement
`getattr(driver, "_current_frame", driver)` avant d'interroger le DOM opèrent dans le frame réellement
sélectionné. Le bloc d'extraction principal calcule `_ctx = getattr(driver, "_current_frame", driver)` une
fois après le switch, et passe `_ctx` (et non `driver`) à tous les extracteurs de ce bloc.
Patterns couverts :
- Frameset classique (`<frameset>` + `<frame>` sœurs, ex. Ipsos/mrIWeb `frame#mainFrame` + `frame#leftFrame`)
  où le contenu de la question est dans un frame enfant, jamais dans le document racine.
Patterns exclus :
- Extracteurs/modules qui reçoivent encore `driver` directement sans résoudre `_current_frame` (ex. couche
  de sélection/clic action_dispatcher.py / input_radio.py / input_utils.py) — non couverts par ce patch,
  cause probable d'un échec de sélection malgré une extraction réussie (voir diagnostic en cours).

Diagnostic associé : Ipsos/mrIWeb (insights.ipsosinteractive.com), question SA (single answer) native
`<input type="radio" name="_QHH__FR01INC_C">` dans `frame#mainFrame`. Avant patch : score de contexte à 0,
0 bloc extrait malgré 21 options radio visibles. Après patch : score=90, extraction réussie
(21 options, target_id="group_..." group_key="radio:name:_qhh__fr01inc_c"). La sélection de la réponse
échoue encore à ce stade — cause suspectée : même classe de bug (résolution de `_current_frame` absente),
mais localisée dans la couche de clic/dispatch plutôt que d'extraction.

Statut : patch extraction validé (score + blocks_count > 0 confirmés en conditions réelles).

### action_dispatcher / input_radio — résolution du contexte de frame dans la couche de sélection/clic
Fichier : Survey/action_dispatcher.py (_apply_by_target_id / _apply_in_current_context), Survey/input_radio.py
(click_radio_by_label et fonctions appelées en cascade), Survey/input_utils.py (find_question_container_by_ctx)
Bug corrigé : `_apply_by_target_id` lisait bien `frame_chain` depuis le payload du registry et se positionnait
correctement dans ce contexte via `switch_to_frame_chain`, mais les fonctions de recherche/clic appelées
ensuite (recherche par xpath du label associé à l'option, recherche de conteneur de question, clic JS)
interrogeaient l'objet driver directement plutôt que de résoudre le contexte de frame actif au moment de la
recherche. Sur un driver Page Playwright native (attache CDP), ces recherches s'exécutaient donc toujours sur
le document racine de la frameset (aucun input) plutôt que dans `frame#mainFrame`, malgré une extraction et un
scoring de contexte déjà corrigés en amont (cf. entrées précédentes de cette section).
Correction : la couche de sélection/clic résout désormais le contexte de frame actif de la même manière que
le module de scoring, avant toute recherche DOM.
Patterns couverts :
- Ipsos/mrIWeb (frameset `frame#mainFrame`/`frame#leftFrame`), question SA native
  (`<input type="radio" name="_QHH__FR01INC_C">`, label `for="_Q0_C{n}"`) — sélection confirmée en run réel
  (apply ok=true strategy=target_id, option "175 000 euros ou plus" correctement cochée à l'écran).
Patterns exclus :
- Aucune modification du contenu des stratégies de clic existantes (xpath, JS, overlay) — seule la résolution
  du contexte dans lequel elles s'exécutent a été corrigée.

Diagnostic associé : la chaîne complète du bug frame-context (extraction ET sélection) touchait 3 points
distincts, tous liés à la même cause racine (propagation de `_current_frame` non systématique) :
1. `switch_to_frame_chain` ne créait jamais `_current_frame` sur un driver qui ne l'avait pas déjà (hasattr
   gate) — corrigé (entrée précédente).
2. Les extracteurs de dom_analyzer.py recevaient `driver` au lieu du contexte résolu `_ctx` — corrigé (entrée
   précédente).
3. La couche de sélection/clic (action_dispatcher.py, input_radio.py, input_utils.py) recevait/interrogeait
   `driver` directement au lieu du contexte résolu — corrigé par ce patch.

Statut : patch validé en conditions réelles (log `[TARGET] apply ok=true strategy=target_id reason=applied`,
confirmation visuelle du radio coché sur la question Ipsos/mrIWeb de test).

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

### _apply_by_target_id — bloc confirmit_wix_fieldset_radio_abtn (variante "AnswerButtons" sans <a>)
Fichier : Survey/action_dispatcher.py
Emplacement : bloc `payload.get("confirmit_wix_fieldset_radio") and resolved_itype == "radio"`, juste après la
résolution de `xp` (option_xpath_map) et avant la séquence de fallbacks génériques radio_main.
Guard : le `xp` existant (XPath `//input[@id=...]/ancestor::td[1]//a[1]`) ne résout aucun candidat
(`driver.query_selector_all(xp)` vide) ET un `label[for="{input_id}"]` est trouvé dans un
`ancestor::td[contains(@class,'confirmit-abtn')]` — confirmation structurelle de la variante AnswerButtons.
Patterns couverts :
- Variante Confirmit/Wix "AnswerButtons" : `td.confirmit-abtn` contenant `<input type="radio" hidden>` +
  `div.confirmit-abtn-label` + `<label for="{input_id}">`, sans aucun `<a href="javascript:void(0)">`
  dans le `<td>` (contrairement au layout classique déjà couvert par `_extract_confirmit_wix_fieldset_radio_block`)
- Sans ce patch : `xp` ne résout jamais rien pour aucune option → fallback générique radio_main non
  déterministe (clic par recherche de label pleine page, sans garantie de cibler la bonne option)
- Clic : `label.click()` natif, fallback `driver.evaluate("(e) => e.click()", label)` si échec
- Validation : `_wait_checked(input_id, None)` sur l'input radio masqué
- Log : `[TARGET_DEBUG] confirmit_wix_fieldset_radio_abtn: ok/ko id={input_id}`
- Succès : `log_info("[TARGET]", "apply ok=true strategy=confirmit_wix_fieldset_radio_abtn reason=input_checked")`
Patterns exclus :
- `xp` classique résolvant au moins un candidat (layout `<a>` déjà couvert) → chemin existant inchangé
- Structure abtn non confirmée (aucun `label[for]` trouvé dans un `td.confirmit-abtn` ancêtre) → fall through
  inchangé vers la séquence de fallbacks génériques existante

### _apply_by_target_id — bloc confirmit_wix_fieldset_checkbox_abtn (variante "AnswerButtons" checkbox)
Fichier : Survey/action_dispatcher.py
Emplacement : juste après le bloc `confirmit_wix_fieldset_radio_abtn` ci-dessus (radio), avant `_first_input_under`.
Guard : `payload.get("confirmit_wix_fieldset_radio") and resolved_itype == "checkbox"` ET le `xp` existant
(XPath `//input[@id=...]/ancestor::td[1]//a[1]`) ne résout aucun candidat ET un `label[for="{input_id}"]` est
trouvé dans un `ancestor::td[contains(@class,'confirmit-abtn')]`.
Patterns couverts :
- Même structure DOM "AnswerButtons" que le bloc radio équivalent (`td.confirmit-abtn` : `<input type="checkbox"
  hidden>` + `div.confirmit-abtn-label` + `<label for="{input_id}">`, sans `<a>`), mais pour un groupe checkbox
  (multi-select). Sans ce patch : le `xp` hérité de `_extract_confirmit_wix_fieldset_radio_block` suppose
  toujours un `<a>` → aucune option ne pouvait être cochée, échec systématique sur chaque option du groupe.
- Fonction nommée distincte, purement additive : ne modifie pas le bloc radio existant.
- Clic : `label.click()` natif, fallback `driver.evaluate("(e) => e.click()", label)` si échec
- Validation : `_wait_checked(input_id, None)` sur l'input checkbox masqué
- Log : `[TARGET_DEBUG] confirmit_wix_fieldset_checkbox_abtn: ok/ko id={input_id}`
- Succès : `log_info("[TARGET]", "apply ok=true strategy=confirmit_wix_fieldset_checkbox_abtn reason=input_checked")`
Patterns exclus :
- `xp` classique résolvant au moins un candidat (layout `<a>` déjà couvert) → chemin existant inchangé
- Structure abtn non confirmée (aucun `label[for]` trouvé dans un `td.confirmit-abtn` ancêtre) → fall through
  inchangé vers la séquence de fallbacks génériques checkbox existante

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

## CHAMP DATE NATIF (input type="date") — CONFIRMIT/FORSTA ET GÉNÉRIQUE
Signature DOM : `<input type="date">` natif (ex : Confirmit/Forsta `cf-question--date`,
`cf-date-answer__input`). Détecté via l'attribut `type` de l'input, indépendamment de la
plateforme — pas une classe CSS spécifique à Confirmit.

### dom_analyzer.py — flag native_date_input (boucle singles)
Fichier : Survey/dom_analyzer.py
Guard : `el_tag == "input"` ET `el.get_attribute("type") == "date"`
Patterns couverts :
- Ajoute `native_date_input: bool` dans le registre DOM_REGISTRY (register_target, clé
  racine) et dans `context` du bloc GPT, sans changer `itype` (reste "text") ni toucher
  `_detect_itype()` (dom_utils.py).
Patterns exclus :
- Aucun changement pour les inputs type="text"/"number"/etc. — flag additif, `False` par défaut.

### prompt_builder.py — selection_rule dédiée date native
Fichier : Survey/prompt_builder.py
Guard : `context.native_date_input is True`
Patterns couverts :
- Consigne dédiée demandant une date complète au format AAAA-MM-JJ (ISO), remplaçant la
  règle générique "renvoyer EXACTEMENT 1 valeur" pour ce bloc précis.
Patterns exclus :
- Champs text/textarea/number sans ce flag → règle générique inchangée.

### batch_response_parser.py — préservation valeur composite date
Fichier : Survey/batch_response_parser.py
Guard : bloc correspondant à `native_date_input=True`
Patterns couverts :
- La valeur ISO complète (AAAA-MM-JJ) est conservée telle quelle, sans troncature par la
  logique min_select/max_select générique (pensée pour les séparateurs "|" multi-select).
Patterns exclus :
- Toute autre question text à valeur unique → logique min_select/max_select inchangée.

### action_dispatcher.py — branche dédiée native_date_input
Fichier : Survey/action_dispatcher.py
Emplacement : bloc `itype in ("text","number","textarea")`, avant le fallback générique
`fill_text_input`.
Guard : `target_payload.get("native_date_input") is True` (lu à la racine du registre
DOM_REGISTRY flat, pas sous "context" — registre construit par dom_analyzer.py)
Patterns couverts :
- Appelle `Survey.input_handler.fill_native_date_input(driver, label, element_id=fid,
  frame_chain=...)`, stratégie dédiée et unique pour ce type de champ.
- En cas d'échec : `continue` explicite, AUCUN retour vers `fill_text_input` générique
  (son sélecteur ne couvre pas input[type=date] et son fallback page-entière peut cibler
  un autre champ texte de la page — cause racine historique de l'écrasement croisé zipcode/DOB).
Patterns exclus :
- Champs text sans ce flag → chemin générique `fill_text_input` inchangé.

### input_text.py — fill_native_date_input
Fichier : Survey/input_text.py
Guard : `element_id` non vide ; résolution stricte par id uniquement (jamais par
contexte/texte de question).
Patterns couverts :
- Résolution de l'élément via `driver.query_selector(f"#{element_id}")` (API Playwright
  native) — PAS `driver.find_element(...)` (API Selenium absente de l'objet Page au
  runtime → AttributeError silencieuse ; cause racine du bug "date jamais saisie").
- Support iframe : si `frame_chain` est renseigné (porté par le registry DOM_REGISTRY),
  résolution/saisie/vérification s'exécutent dans ce contexte, même convention que
  `_apply_by_target_id` (switch_to_frame_chain, action_dispatcher.py).
- Assignation directe `el.value = iso` + dispatch input/change (pattern déjà validé pour
  `select_native_option_by_target`, input_dropdown.py) — PAS `react_set_value_and_fire`.
- Formats d'entrée acceptés : AAAA-MM-JJ (ISO) ou JJ/MM/AAAA, normalisés en ISO.
Patterns exclus :
- Champs text/textarea classiques → `fill_text_input` inchangé, non appelé depuis cette fonction.
- Aucun repli en cas d'échec de résolution/assignation (pas de fallback empilé) : retourne
  `False`, laisse action_dispatcher.py logguer l'échec sans dérive vers un autre champ.

---

## PLATEFORME : KANTAR / mrIWeb — ROWPICKER RADIO / CHECKBOX (REACT OVERLAY)
Signature DOM : `form[name="mrForm"]` (sa.ktrmr.com), `metaType="rowpicker"` dans le SEJson.
Double couche : `div.questionContainer[questionname][display:none]` (inputs natifs non interactables) + `div#container_{questionname}._rowpicker` (cartes React cliquables).
Le même conteneur `_rowpicker` sert aussi bien des questions à choix unique que des questions à choix multiple (avec éventuellement une option exclusive) — la couche visuelle est identique dans les deux cas, seule la couche native cachée distingue les deux.

### _extract_kantar_rowpicker_radio_blocks
Fichier : Survey/dom_extractors_misc.py
Emplacement : appelée en priorité dans `analyze_dom` avant l'extracteur générique radio/checkbox.
Guard : `div[id^='container_'] [data-test='main-contain']._rowpicker` présent dans le DOM
Patterns couverts :
- Question : `#qc_{q_suffix} span.mrQuestionText` ; variante : `.questionContainer[questionname$='.{q_suffix}'] span.mrQuestionText`
- Cartes : itération sur les overlays `div[dir='ltr'][tabindex='0']` dans le picker ; remontée au conteneur de carte via `ancestor::div[@dir='ltr'][not(@tabindex)][1]` ; label depuis `label span`
- `option_xpath_map` pointe sur l'overlay (seul élément interactable), pas sur les inputs natifs
- Distinction radio/checkbox : lecture de `input[questionname='{q_suffix}']` dans la couche native cachée. ≥2 inputs `type="checkbox"` (classe `mrMultiple`) → `itype="checkbox"` ; sinon `itype="radio"`.
- Option exclusive (checkbox uniquement) : détectée via `isexclusive="true"` sur l'input natif ; le label correspondant est résolu via `label[for={input_id}] .mrMultipleText` et stocké normalisé dans `payload["meta"]["exclusive_options_norm"]`. `max_select = nb_inputs_checkbox - nb_exclusifs` (min 1).
- Flag payload : `kantar_rowpicker_radio=True` dans les deux cas (radio ET checkbox) ; `group_key` : `kantar_rowpicker:{itype}:{q_suffix}`
Note DOM : l'overlay `div[dir='ltr'][tabindex='0']` est séparé de la carte `div[dir='ltr']` par un div intermédiaire sans attribut `dir` — `div[@tabindex='0']` (enfant direct) ne matche pas ; il faut itérer sur les overlays et remonter.
Note DOM : le flag `kantar_rowpicker_radio` reste nommé ainsi même pour les blocs `itype="checkbox"` — c'est le nom du widget (rowpicker), pas le type de sélection ; ne pas le renommer sans mettre à jour tous les guards dispatcher qui le lisent.
Patterns exclus :
- `div[id^='sq-QARTS-container-']` (Decipher/LifePoints QARTS) → extracteur séparé
- `_rowrank` (metaType=rowrank) → `_extract_kantar_rowrank_blocks`
- Inputs natifs `input[type=radio][class*="mrSingle"]` / `input[type=checkbox][class*="mrMultiple"]` dans `div.questionContainer` → jamais ciblés directement (uniquement lus pour déterminer itype/exclusivité)

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
- `_JS_FIND` : cherche le label par texte dans `._rowpicker`, remonte au conteneur de carte réel via boucle `parentElement` jusqu'au premier `div[dir="ltr"]` SANS `tabindex` (mirroir exact de l'ancestor-axis XPath de `_extract_kantar_rowpicker_radio_blocks` : `ancestor::div[@dir='ltr'][not(@tabindex)][1]`), puis cible l'overlay `div[dir="ltr"][tabindex="0"]` descendant avec `cursor` dans le style inline
- Résolution d'élément cliquable : `driver.evaluate_handle("(arg) => {...}", label).as_element()` — PAS `driver.evaluate(...)`. `evaluate()` sérialise la valeur de retour (un noeud DOM renvoyé par le script redescend en Python comme `str`/`dict` sans méthodes d'interaction, échec silencieux `AttributeError` sur `.click()`) ; `evaluate_handle().as_element()` retourne un véritable ElementHandle cliquable. Même convention que `action_dispatcher.py` (`cell_pre`/`decipher_cell`/`decipher_radio_cell` via `evaluate_handle(...).as_element()`).
- Clic : `overlay.click()`, fallback `overlay.hover(); overlay.click()`
- `_JS_VERIFY` : changement de `background-color` sur `div[style*="transition: background-color"]` de la carte
- Logging de diagnostic à chaque branche de retour anticipé (`js_find_exception` avec type+message, `overlay_not_found`, `overlay_click_failed` avec les deux exceptions click/hover séparées, `native_verify=ok/ko`) — indispensable ici car les échecs précédents (JS cassé, mauvais mécanisme de résolution) étaient totalement silencieux sans ces logs.
Note DOM : `input.checked` toujours `false` sur ce DOM — les inputs natifs dans `display:none` ne sont jamais synchronisés par React. Vérification obligatoirement via background-color de la carte.
Note historique (piège à ne pas réintroduire) : les scripts `_JS_FIND`/`_JS_VERIFY` sont enveloppés en fonction fléchée `(arg) => {...}` au point d'appel — ne jamais référencer `arguments[0]` dans leur corps (une fonction fléchée n'a pas d'objet `arguments`), toujours utiliser le nom du paramètre (`arg`).
Patterns exclus :
- `div[id^='sq-QARTS-container-']` → guard DOM distinct

### execute_action — bloc radio : pas de fallback générique après échec kantar_rowpicker_radio
Fichier : Survey/action_dispatcher.py
Emplacement : section `if itype == "radio":`, juste après `_tp = target_payload or {}`, avant le calcul de `_tmr_opt_keys`.
Guard : `_tp.get("kantar_rowpicker_radio")` truthy (payload posé par `_extract_kantar_rowpicker_radio_blocks`).
Problème résolu : quand `_apply_by_target_id()` échoue pour un bloc `kantar_rowpicker_radio` (ex. option "Homme"), `execute_action` retombait sur la séquence générique `radio_main` (`click_radio_by_label`) / `radio_buttonish`. Ces stratégies génériques forcent `input.checked = true` en JS et rapportent un succès même quand l'overlay React n'a reçu aucun clic effectif — aucune sélection visible sur la carte, faux positif silencieux.
Correction : dès l'entrée du bloc radio, si `kantar_rowpicker_radio=True`, retour `False` immédiat (aucune stratégie générique invoquée). L'échec de la stratégie dédiée est donc rapporté tel quel.
Patterns exclus :
- Tout bloc radio sans flag `kantar_rowpicker_radio` → séquence `aa_answer_matrix` / `radio_slider` / `radio_main` / `radio_buttonish` inchangée.
- Détections Kantar rowpicker par scan DOM runtime dans `_apply_by_target_id` (lignes ~2481/2644, sans passer par le payload extrait) → non concernées, elles retournent déjà `bool(_rp_ok)` directement sans tomber dans ce bloc radio générique.

### execute_action — court-circuit dédié pour kantar_rowpicker en itype="checkbox"
Fichier : Survey/action_dispatcher.py
Emplacement : juste avant l'appel à `_apply_by_target_id` (bloc `if target_id and not skip_apply_by_target_id`), dans la section qui gère les stratégies dépendant du payload `_p`.
Guard : `_p.get("kantar_rowpicker_radio") and itype == "checkbox"`
Problème résolu : sans ce court-circuit, le premier clic d'un groupe checkbox rowpicker (cache de stratégie checkbox vide) traversait `_apply_toluna_runtime_answerrow_cached()` — sonde hors-scope pour ce widget qui échoue systématiquement avec `AttributeError: 'str' object has no attribute 'evaluate'` (elle appelle `.evaluate()` sur `value`, qui est le label texte de la réponse, pas un ElementHandle) — puis une résolution XPath qui échoue toujours (l'input natif est dans un conteneur `display:none`), avant d'atteindre `checkbox_fallback_radio` → `click_kantar_rowpicker_radio` qui fonctionne réellement. Ce chemin complet (sonde cassée + 2 stratégies génériques en échec avec timeouts) ne s'exécutait que pour la première option du groupe ; les options suivantes réutilisaient directement la stratégie mise en cache.
Correction : le widget étant déjà identifiable via le flag `kantar_rowpicker_radio` posé par l'extracteur, `execute_action` appelle directement `click_kantar_rowpicker_radio(driver, value)` dès l'entrée, `skip_apply_by_target_id=True`, sans passer par `_apply_by_target_id` ni par la liste ordonnée `checkbox_main`/`checkbox_buttonish`/`checkbox_fallback_radio`. Pas de fallback générique après échec (retour `False` direct), même logique que le bloc radio équivalent.
Note : `click_kantar_rowpicker_radio` (Survey/input_radio.py, voir entrée ci-dessus) est réutilisée telle quelle pour le cas checkbox — elle clique par label sur l'overlay React, ce qui fonctionne indépendamment du type de sélection sous-jacent.
Patterns exclus :
- Tout bloc checkbox sans flag `kantar_rowpicker_radio` → séquence `checkbox_main` / `checkbox_buttonish` / `checkbox_fallback_radio` inchangée (y compris la sonde `_apply_toluna_runtime_answerrow_cached`, non touchée par ce patch).

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

### _collect_dropdown_blocks — budget/deadline sur le scan des customs
Constantes : `_CUSTOM_DROPDOWN_SCAN_BUDGET = 40`, `_CUSTOM_DROPDOWN_SCAN_DEADLINE_S = 3.0`.
Problème résolu : les sélecteurs CSS de la passe 2 (`.dropdown`, `.select`) sont génériques et peuvent matcher un grand nombre d'éléments hors-scope (navbars, sélecteurs de langue, bannières cookies…). Chaque candidat coûte plusieurs aller-retours navigateur (`_visible` + `_extract_label`) ; sans borne, un DOM avec de nombreux éléments correspondants pouvait faire durer la résolution plusieurs dizaines de secondes avant de rendre la main à la stratégie suivante — aucun `time.sleep`/deadline codé en dur nulle part dans ce chemin, le coût venait uniquement du nombre d'aller-retours cumulés (accentué par la latence réseau du proxy ISP par bot).
Fix : liste `customs` tronquée à `_CUSTOM_DROPDOWN_SCAN_BUDGET` éléments (log si dépassement), puis boucle de collecte bornée par une deadline `time.monotonic()` de `_CUSTOM_DROPDOWN_SCAN_DEADLINE_S` secondes avec abandon contrôlé et log.
Patterns exclus :
- Ne change rien à la passe 1 (`<select>` natifs) : pas de budget ni de deadline dessus (nombre de `<select>` par page toujours faible en pratique).

---

## PLATEFORME : IPSOS / WICKET — DROPDOWN NATIF BOOTSTRAP-SELECT (bs-select-hidden)
Contexte DOM : formulaires Wicket (ex. enter.ipsosinteractive.com), champ date de naissance
à deux `<select>` natifs (mois, année) côte à côte. Chaque `<select>` porte la classe
`bs-select-hidden` et est rendu invisible au profit d'un widget bootstrap-select
(bouton `.filter-option` + menu `<ul class="dropdown-menu inner">` de `<li><a>` cliquables)
qui est le seul élément réellement visible/interactif pour l'utilisateur.

### select_native_option_by_target — assignation JS directe (bypass actionability Playwright)
Fichier : Survey/input_dropdown.py
Guard : appelé depuis action_dispatcher.py, branche `itype == "dropdown"`, quand
`target_payload.get("tag") == "select"` (résolution par xpath/id/alt_xpaths/name du registry).
Bug corrigé : `el.select_option(label=...)` applique les vérifications d'actionability
Playwright, dont la visibilité. Sur un `<select class="bs-select-hidden">`, l'élément n'est
jamais visible au sens Playwright (le widget de substitution l'est, lui) → `select_option()`
échoue proprement (pas d'exception qui remonte) sans jamais appliquer la valeur → la fonction
retournait `False` ("dropdown_native_by_id échec"), et le dispatch retombait alors sur le
chemin générique (`select_option_with_hint` → `open_dropdown_generic`), qui lui plantait sur
`el.tag_name` (API Selenium absente d'un ElementHandle Playwright).
Fix : remplacement de `select_option()` par une assignation JS directe via `evaluate()`
(`sel.value = val` + dispatch `input`/`change` + `jQuery(sel).selectpicker('refresh')` si
présent) — fonctionne indépendamment de l'état de visibilité du `<select>` et rafraîchit le
widget bootstrap-select. Vérification post-assignation via lecture de
`options[selectedIndex].text`.
Patterns couverts :
- `<select>` natif résolu par target_id du registry, visible ou non (bs-select-hidden inclus)
Patterns exclus :
- Ne touche pas au chemin générique `select_option_with_hint`/`open_dropdown_generic`
  (toujours vulnérable à `el.tag_name` si jamais atteint — cf entrée dédiée plus haut) ; cette
  fonction est un contournement en amont, pas un correctif de ce chemin-là.

### execute_actions_plan — skip rescan same_qblock pour deux dropdowns consécutifs de même contexte GPT
Fichier : Survey/action_dispatcher.py
Emplacement : bloc `rescan_between_actions`, condition `same_question_block`.
Bug corrigé : pour une question à deux `<select>` liés (mois + année), chaque champ est un
QID GPT distinct (pas de qid partagé comme radio/checkbox) et n'a pas de target_id commun
(contrairement au multi_text). Le rescan DOM se déclenchait donc entre les deux actions. Or le
texte "question" du registry embarque la valeur déjà sélectionnée du premier champ (ex.
"... Juillet Année" après application du mois) — le rescan qui suit régénère un nouveau
target_id pour le second champ à partir de ce texte modifié, target_id que
`select_native_option_by_target` ne retrouve alors plus dans le registry (aucune tentative de
résolution même journalisée), et le dispatch retombe sur le chemin générique fautif.
Fix : `same_question_block = True` quand `itype_lower == next_itype == "dropdown"` ET le texte
de contexte GPT (`context`, statique, non re-extrait du DOM) est identique entre les deux
actions consécutives — signal stable contrairement au texte "question" du registry.
Patterns exclus :
- Deux dropdowns consécutifs de contexte différent (questions distinctes) → rescan maintenu.

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

## LEÇON TRANSVERSALE : RÉSOLUTION D'ÉLÉMENT DOM DEPUIS UN SCRIPT — evaluate() vs evaluate_handle().as_element()
Quand un script exécuté dans la page doit **retourner un élément DOM** pour qu'on agisse dessus
ensuite côté Python (`.click()`, `.hover()`, etc.), utiliser `driver.evaluate_handle(js, arg).as_element()`,
jamais `driver.evaluate(js, arg)`. `evaluate()` sérialise la valeur de retour : un noeud DOM redescend
alors comme `str`/`dict` sans méthode d'interaction → `AttributeError` silencieuse dès le premier
`.click()`, souvent absorbée par un `try/except` englobant sans log détaillé, et donc invisible sans
diagnostic explicite. `evaluate()` reste correct pour un retour de valeur simple (bool/str/number),
comme dans `_JS_VERIFY` de `click_kantar_rowpicker_radio`. Convention correcte déjà en usage dans
`action_dispatcher.py` (`cell_pre`/`decipher_cell`/`decipher_radio_cell`).
Cas confirmé et corrigé : `click_kantar_rowpicker_radio` (Survey/input_radio.py).
Cas signalés (même convention suspectée, **non confirmés ni corrigés** — à valider individuellement
sur DOM de référence avant tout patch, un par un) :
- `fallback_click_radio_js_generic` (Survey/input_radio.py)
- Bloc `decipher_ranksort_dropdown` (Survey/action_dispatcher.py)
- Bloc `DRAGDROP` (Survey/action_dispatcher.py)
- Résolution `<select>` natif (Survey/input_dropdown.py) — probablement code mort post-migration
  (mélange `execute_script`/`arguments[0]`/`evaluate` imbriqué)
Note associée (piège récurrent, même famille de bug) : ne jamais référencer `arguments[0]` dans un
corps de script enveloppé en fonction fléchée `(arg) => {...}` — utiliser le nom du paramètre.

---

## LEÇON TRANSVERSALE : CONTENEUR TECHNIQUE `aria-hidden="true"` HORS-VIEWPORT (honeypot / champs QA GfK mrIWeb)
Plateforme observée : GfK / mrIWeb (SPSSMR/HTMLPlayer), page NielsenIQ — mais le guard est
transversal (utilisé par les chemins texte ET radio/checkbox), pas un extracteur de plateforme.

### _is_hidden_offscreen_ariahidden_container
Fichier : Survey/dom_utils.py
Problème résolu : deux blocs parasites étaient extraits en plus de la question réellement
affichée (un radio de consentement) — un champ texte libre ("What is your name?") et un groupe
de 2 checkboxes ("Yes"/"No"), tous deux logés dans un conteneur `aria-hidden="true"` positionné
en `position:fixed`/`absolute` à des coordonnées très hors du viewport (ex. `top:-999px`). Ce
conteneur n'est ni `display:none` ni `visibility:hidden`, donc les vérifications de visibilité
existantes (bounding rect > 0, computed style display/visibility/opacity) le laissaient passer
comme "visible".
Guard : remonte les ancêtres de l'élément ; si un ancêtre porte `aria-hidden="true"` ET a un
`position` calculé `fixed` ou `absolute` ET un `getBoundingClientRect()` avec `top` ou `left`
inférieur à -300, retourne `True` (élément à ignorer).
Patterns couverts :
- Champs techniques/honeypot GfK mrIWeb rendus dans le DOM mais explicitement sortis de l'arbre
  d'accessibilité et du viewport visuel (ex. `_Q2` type=text, `_Q3` checkboxes de contrôle qualité
  dans `<div aria-hidden="true" style="position:fixed;top:-999px;left:-999px;">`)
Patterns exclus :
- Techniques "visually-hidden" d'accessibilité classiques (ex. classes sr-only) : ne posent
  jamais `aria-hidden="true"` sur ce type de conteneur, car cela les cacherait aussi aux lecteurs
  d'écran — donc pas de faux positif attendu sur ce pattern
- Conteneurs `display:none` (ex. `#HiddenBanners`) : déjà exclus par ailleurs (`hidden_or_system`),
  non concernés par ce guard

Deux points d'appel (additifs, un guard, deux entrées) :
- `Survey/dom_utils.py` → `_is_actionable_visible` : appelé en tête de fonction (étape 0-bis),
  avant les autres vérifications ; couvre le chemin "autres inputs" (texte/textarea/select/bouton)
  dans `Survey/dom_analyzer.py`.
- `Survey/dom_analyzer.py` → `_choice_has_visible_proxy` (fonction interne à la boucle de
  collecte des inputs radio/checkbox) : appelé avant le fallback JS de détection de proxy visible ;
  couvre le chemin radio/checkbox.

---

## PLATEFORME : IPSOS / SIMSTORE (MUI REACT) — CHOIX MULTIPLE IMAGE-ONLY (aria-labelledby)
Signature DOM : `<ul>` MUI (`MuiList*`) contenant N `<li>` > `div[role="button"].MuiListItemButton-root`
> `input[type="checkbox"]` (SANS `name` ni `id`, `tabindex="-1"`, `aria-labelledby="{id}"`)
+ `div[id="{id}"].MuiListItemText-root` frère contenant uniquement une `<img alt="...">` (aucun texte).
Domaine observé : field.simstore.ipsos.com. Le libellé de chaque option n'existe qu'à travers l'`alt`
de l'image référencée par `aria-labelledby` — aucun `label[for]`, aucun `name` partagé, aucun `id` sur l'input.

### _image_labelledby_option_alt / _image_labelledby_container_sig
Fichier : Survey/dom_extractors_misc.py
Rôle : résolution du libellé d'une option via `aria-labelledby` → élément référencé → `img[alt]`
(rejette si l'élément référencé contient un autre texte que l'alt) ; signature de groupement basée
sur le plus proche ancêtre `ul/ol/[role='listbox']/[role='group']/fieldset` (substitut au `name`
partagé, absent sur ce DOM).
Patterns exclus : options avec `label[for]` ou texte wrapper classique → pipeline générique existant.

### _extract_image_labelledby_choice_checkbox_blocks
Fichier : Survey/dom_extractors_misc.py
Enregistré dans : dom_analyzer.py (après `_extract_image_only_choice_checkbox_blocks`)
Guard (tous requis) :
1. `input[type='checkbox'][aria-labelledby]` sans `name`
2. `aria-labelledby` résolu vers un élément ne contenant qu'une `img[alt]` non vide
3. ≥2 tels inputs partageant le même conteneur ancêtre stable (signature ci-dessus)
Patterns couverts :
- Groupe checkbox complet extrait sans recours Vision : question + options (`alt` des images)
- Sans cet extracteur : `options=[]` → page classée `image_selection_challenge` par
  `_detect_image_only_unresolvable_dom` (survey_executor.py) → dom_only_abort → disqualification
- `group_key = checkbox:image_labelledby:{group_idx}` ; flag payload : `image_only_choice_checkbox=True`
Patterns exclus :
- Inputs avec `name` → `_extract_image_only_choice_checkbox_blocks`
- Libellé porté par `label[for]` ou texte wrapper → pipeline générique existant
- Radios (non checkbox) → hors scope

### option_xpath_map — résolution de clic par contenu (alt), pas par position DOM
Fichier : Survey/dom_extractors_misc.py, fonction `_extract_image_labelledby_choice_checkbox_blocks`
Problème résolu : l'input n'ayant ni `id` ni `name`, le repli initial vers un XPath absolu positionnel
(`_best_xpath_for_element`, basé sur les index de `<li>`/`<div>`) ne résolvait plus rien au moment du
clic — React re-render les indices de la liste, et l'input `tabindex="-1"` n'est de toute façon pas la
cible cliquable réelle (widget MUI stylé, clic géré par le `div[role="button"]` ancêtre).
Fix : XPath ancré sur le contenu, pas la position : `//img[@alt='{alt}']/ancestor::*[@role='button'][1]`
— cible directement le conteneur cliquable (MuiListItemButton) portant l'image de l'option choisie.
Patterns couverts :
- Toute option dont l'`alt` est unique dans la page au sein de ce groupe (garanti par le guard 2 ci-dessus)
Patterns exclus :
- Aucune modification du chemin `option_xpath_map` générique existant pour les autres extracteurs
  (`_extract_image_only_choice_checkbox_blocks` conserve son XPath par `id`/`_best_xpath_for_element`)

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
| Ipsos/simstore MUI | _extract_image_labelledby_choice_checkbox_blocks | _extract_image_only_choice_checkbox_blocks | inputs SANS `name` + libellé résolu via `aria-labelledby` (vs inputs avec `name` + wrapper label/parent direct) |

---

## PLATEFORME : GfK / mrIWeb (HTMLPlayer) — QUESTION TEXTAREA SINGLE
Signature DOM : `form#mrForm` (SPSSMR/mrIWeb.dll), question single dans `div.que_txa` contenant
un unique `<textarea name="_QQSeqMatchQuestion" id="_Q{N}">` (ex. question anti-inattention
"tapez le texte exactement tel qu'il est affiché").

### execute_action — inclusion itype "textarea" dans le dispatch générique TEXT/NUMBER
Fichier : Survey/action_dispatcher.py
Emplacement : bloc `# 🟦 TEXT / NUMBER / TEXTAREA`, juste avant le log terminal `no_strategy`.
Guard : `itype in ("text", "number", "textarea")` (avant le patch : `("text", "number")` seul).
Problème résolu : pour un bloc résolu `itype="textarea"` (question single avec un unique
`<textarea>`), une valeur de réponse valide et non vide était bien résolue en amont, mais
`execute_action` ne reconnaissait "textarea" dans aucun itype de son dispatch générique — le
pipeline tombait directement sur `log_info("apply ok=false reason=no_strategy ...")` sans jamais
appeler `fill_text_input`, alors que `_apply_by_target_id()` gérait déjà en interne
`resolved_itype in ("text", "textarea", "number")` (lignes ~3775/3851) pour les chemins qui en
dépendent (ex. `multi_text`). Le chemin générique post-`_apply_by_target_id` (celui qui appelle
directement `fill_text_input`) était le seul encore fermé à "textarea".
Correction : ajout de `"textarea"` au tuple d'entrée du bloc générique. `fill_text_input`
(Survey/input_text.py) ciblait déjà `textarea` dans son sélecteur DOM (`selector` inclut
`textarea`) — aucune modification de `fill_text_input` ni d'aucun extracteur existant.
Patterns couverts :
- Tout bloc `itype="textarea"`, kind `single`, dont le `target_id`/`_apply_by_target_id` ne
  résout rien (pas d'option map applicable) → retombe sur `fill_text_input` via `context.id`
  (même mécanisme que text/number, cf. entrée `execute_action — passage context.id →
  fill_text_input` plus haut dans ce fichier)
Patterns exclus :
- Aucun changement de comportement pour "text"/"number" (tuple additif, pas de retrait)
- Blocs textarea déjà résolus en amont par `_apply_by_target_id` (ex. `multi_text`) → chemin
  inchangé, ce patch ne concerne que le fallback générique post-target_id
Note : aucun CTA touché par ce patch (saisie de champ uniquement).

---

## LEÇON TRANSVERSALE : `is_selected()` INEXISTANT SUR PLAYWRIGHT — FAUX NÉGATIF SILENCIEUX SUR CHECKBOX/RADIO MASQUÉS EN CSS

Détecté sur : GfK / mrIWeb (HTMLPlayer/SPSSMR), widget "mrMultiple", checkbox à choix multiple
(conteneur `dom_container:span|mrquestiontable`, target_id de groupe). Le guard transversal
ci-dessous n'est pas limité à cette plateforme : toute question radio/checkbox dont l'`<input>`
natif est stylé `visibility:hidden`/masqué en CSS (widget custom re-stylé visuellement, très
courant) était concernée.

### `_is_selected` (Survey/action_dispatcher.py) et `is_checked` (Survey/input_utils.py)
Problème résolu : les deux fonctions appelaient `el.is_selected()` — méthode Selenium, jamais
migrée lors du passage à Playwright, **inexistante** sur `ElementHandle`/`Locator` Playwright.
L'appel levait donc systématiquement une `AttributeError`, avalée par un `try/except` qui
retournait `False` par défaut — indépendamment de l'état réel de l'input. Résultat observé :
un `<input type="checkbox">` réellement coché (propriété DOM live `checked=true` confirmée par
instrumentation, sur un nœud vérifié non-stale) était en permanence rapporté comme non coché.
Toutes les stratégies de clic ultérieures considéraient donc l'action comme un échec et
enchaînaient des fallbacks génériques (ex. `click_radio_by_label` en repli checkbox, tentative
`kantar_rowpicker`) qui finissaient par décocher la case déjà correctement cochée — symptôme
observable : case cochée puis décochée à chaque option, `apply ok=false reason=no_strategy` final
pour chaque option du groupe, alors que le tout premier clic avait réellement fonctionné.
Correction : remplacement de `el.is_selected()` par `el.is_checked()` (méthode Playwright native
qui lit l'état "checked" réel sans wait d'actionability/visibilité — valide donc aussi pour les
inputs masqués en CSS). Aucune autre stratégie de clic ni logique de fallback modifiée.
Patterns couverts :
- Tout `input[type=checkbox|radio]` dont l'état "checked" doit être vérifié après une tentative
  de sélection, y compris quand l'input est natif mais masqué visuellement (`visibility:hidden`,
  `position:absolute` hors-écran, etc.) et remplacé par un widget stylé (icône/`span` séparé).
Patterns exclus :
- Éléments `role="checkbox"` sans `<input>` natif sous-jacent (ARIA custom) : toujours couverts
  par le fallback `aria-checked` existant dans ces mêmes fonctions, chemin inchangé.
- Éléments avec état porté uniquement par des classes CSS (ex. `is-checked`) sans `checked`
  natif ni `aria-checked` : fallback classes existant inchangé.
Note diagnostic : ce bug ne se manifeste jamais par une exception visible — le faux `False`
silencieux ressemble en tout point à un problème de stratégie de clic ou de résolution DOM,
ce qui a nécessité une instrumentation dédiée (log de l'état "checked" via requête DOM fraîche,
indépendante de toute référence potentiellement obsolète) pour être distingué d'un problème de
clic. Si un futur bug checkbox/radio montre un premier clic visuellement réussi suivi d'un
décochage en cascade sur d'autres plateformes, vérifier en priorité l'état réel du DOM avant de
suspecter la stratégie de clic elle-même.

---

## MODULE TRANSVERSAL : CTA_HANDLER.PY — DÉTECTION/CLIC NAVIGATION GÉNÉRIQUE

### try_click_navigation_cta — exclusion structurelle des conteneurs de réponse radio/checkbox
Fichier : Survey/cta_handler.py
Emplacement : boucle de constitution des candidats CTA génériques (XPath `nav_xpath`), juste après
le filtre d'exclusion `ancestor::ps-footer` et avant le filtre `disabled_patterns`.
Guard : `el.query_selector_all("input[type='radio'], input[type='checkbox']")` non vide — le candidat
encapsule au moins un input radio/checkbox descendant.
Patterns couverts :
- Faux positif générique : `nav_xpath` inclut l'alternative `//*[@tabindex and not(self::input or
  self::textarea or self::select)]`, qui matche tout conteneur focusable non-input — y compris les
  widgets de réponse à une question stylés en bouton (ex. `td.confirmit-abtn[tabindex="0"]` enveloppant
  un `input[type=radio]` masqué + `label`, pattern Confirmit/Wix "AnswerButtons")
- Ces conteneurs portent un texte lisible (le label de l'option, ex. "Un homme"), ce qui leur permet de
  passer le filtre "doit contenir un mot-clé de navigation" (ce filtre ne s'applique que si le texte est
  vide) sans jamais être vérifiés comme candidats de navigation légitimes
- Ils accumulent ensuite un score suffisant (classe contenant la sous-chaîne "btn", `tabindex="0"`,
  `ancestor::form`) pour dépasser le score du vrai bouton de navigation quand celui-ci n'a ni id/name/texte
  correspondant aux mots-clés de scoring reconnus
- Symptôme observé : le clic CTA cible de façon répétée l'option de réponse au lieu du bouton de
  navigation réel — la sélection déjà validée bascule entre les options à chaque tentative de clic CTA,
  sans jamais progresser vers la page suivante (`PROGRESSED=false` en boucle, URL inchangée)
- Un vrai CTA de navigation (button/input[submit]/a) n'encapsule jamais un input radio/checkbox de
  réponse à une question ; ce signal structurel exclut donc précisément ce cas
Patterns exclus :
- Candidats sans input radio/checkbox descendant → filtre inopérant, scoring et sélection inchangés
- Aucun changement au scoring ni aux autres filtres existants (Askia, Forsta, Toluna nav wrapper,
  AreYouNet, Decipher, IntelliSurvey, MRIWeb, ps-next-button, etc.) — patch additif, exclusion structurelle
  uniquement

---

## PLATEFORME : IPSOS-NORM MUI REACT (dialog-question)
Signature DOM : bandeau technique `<div id="tr-check" style="display:none">This is an ipsos-norm survey</div>`.
Question rendue dans `div.dialog-question[-vertical]` contenant `div.text-container.question-text`
(texte de question) ET `ul` d'options bouton, tous deux enfants directs du même conteneur.
Options : `li` > conteneur cliquable (`role="button"` `tabindex="0"`, sans input radio/checkbox natif,
sans `name` partagé) > 2 niveaux imbriqués > `div.option-text` (texte affiché).

### _is_mui_dialog_question_optimal_container
Fichier : Survey/dom_analyzer.py
Emplacement : boucle `for b in btn_like`, juste avant l'appel à `_resolve_button_group_container`.
Guard : `cont.get_attribute("class")` contient `dialog-question` ET `cont.query_selector(".text-container.question-text")` non null.
Problème résolu : sans ce guard, `_resolve_button_group_container` remontait le conteneur résolu
jusqu'au `<ul>` d'options seul (premier ancêtre où ≥2 boutons visibles sont trouvés) — ce `<ul>`
exclut le texte de question, sibling du `<ul>` et non de ses ancêtres directs. `_extract_question_from_container(ul)`
ne trouvait alors que les options (filtrées comme telles) → question vide → bloc entier abandonné silencieusement.
Patterns couverts :
- Conteneur `div.dialog-question[-vertical|-text]` déjà optimal (question + options dans le même scope) : le patch court-circuite `_resolve_button_group_container` et conserve `cont` tel quel.
Patterns exclus :
- Conteneurs sans classe `dialog-question` → `_resolve_button_group_container` inchangé
- `div.dialog-question` sans `.text-container.question-text` descendant (cas non rencontré à ce jour) → non couvert, chemin existant

### _mui_dialog_question_option_text_xpath
Fichier : Survey/dom_analyzer.py
Emplacement : construction de `option_xpath_map`, boucle `for b in btns`, juste après le calcul de `xp` via `_best_xpath_for_element`, avant l'affectation dans le dict. Appelée uniquement si `_is_mui_dialog_question_optimal_container(cont)` est vrai.
Guard : `b.query_selector(".option-text")` non vide (texte affiché de l'option lisible).
Problème résolu : `_best_xpath_for_element` produit un xpath absolu positionnel (`/html/body/.../ul/li[2]/div`).
Cet index devient invalide après un re-render React (ex. réponse déjà appliquée sur une autre question de la page,
ou classe `Mui-selected` togglée sur une autre option) → "element not found for xpath" au moment du clic,
alors que l'extraction avait réussi. Le fallback générique suivant (`click_kantar_rowpicker_radio`, cherche un
"overlay" par label dans une structure de carte/rowpicker) est structurellement inadapté à ce DOM et échoue aussi
("overlay_not_found") → `apply ok=false reason=no_strategy` malgré une extraction correcte.
Fix : xpath ancré sur le contenu (`div.option-text` avec le texte exact de l'option), remontée à l'ancêtre
`[@role='button'][1]` (conteneur cliquable réel) — même famille de correctif que
`_extract_image_labelledby_choice_checkbox_blocks` (résolution par `alt`, pas par position).
Patterns couverts :
- Options dans un conteneur validé par `_is_mui_dialog_question_optimal_container`, avec un `.option-text` non vide et un texte stable entre extraction et clic
Patterns exclus :
- Conteneurs non `dialog-question` (guard parent) → `xp` reste le xpath positionnel `_best_xpath_for_element` d'origine, chemin générique inchangé
- Lignes `tr` (lookup tables) → hors scope (`_btns_are_tr` exclu explicitement)
- Options sans `.option-text` descendant → `xp` reste positionnel (pas de dégradation, juste pas d'amélioration)

---

## MISE A JOUR : IPSOS-NORM MUI REACT (dialog-question) — clic
Statut : la résolution par XPath ancré sur `.option-text` (`_mui_dialog_question_option_text_xpath`,
section précédente) reste utilisée pour peupler `option_xpath_map` à l'extraction, mais n'est plus
le mécanisme réellement emprunté au clic pour les blocs radio de ce widget : le dispatcher court-circuite
ce chemin via le flag `mui_dialog_question_option` (voir ci-dessous). Le XPath text()-based s'est révélé
non fiable au clic ("element not found for xpath") malgré une correspondance textuelle apparente et un
préfixe "xpath=" correct — cause exacte non isolée avec certitude (nœud texte/normalisation), non retestée
depuis le remplacement.

### click_mui_dialog_question_option (Survey/input_radio.py)
Fichier : Survey/input_radio.py (fonction), déclenchement additif dans Survey/dom_analyzer.py et
Survey/action_dispatcher.py.
Guard DOM strict : au moins un `.dialog-question .option-text` présent dans le document.
Mécanisme : résolution par comparaison de texte normalisée exécutée en JS côté page
(`document.querySelectorAll('.dialog-question .option-text')`, normalisation casse/espaces/NFKC),
`node.closest('[role="button"]')` pour remonter au conteneur cliquable réel — aucun XPath. Récupération
via `evaluate_handle(...).as_element()` (ElementHandle cliquable), même convention que
`click_kantar_rowpicker_radio`. Vérification post-clic : présence de la classe `Mui-selected` sur le
conteneur `role='button'` correspondant au libellé.
Déclenchement (additif, ordre de priorité) :
- dom_analyzer.py : à l'enregistrement du bloc, si `_block_itype == "radio"` et
  `_is_mui_dialog_question_optimal_container(cont)` est vrai → `_reg_ctx["mui_dialog_question_option"] = True`
  (registry), en plus de `option_xpath_map` (toujours peuplé, non utilisé au clic pour ce flag).
- action_dispatcher.py, chemin générique radio (avant résolution XPath) : si
  `payload.get("mui_dialog_question_option")` et `resolved_itype == "radio"` → appel direct de
  `click_mui_dialog_question_option`, retour immédiat (bypass total du chemin XPath/option_xpath_map).
- action_dispatcher.py, bloc `itype == "radio"` (fallback générique après échec de la stratégie dédiée) :
  si `_tp.get("mui_dialog_question_option")` → retour `False` direct, pas de fallback générique
  (`radio_main`/`radio_buttonish`/`click_kantar_rowpicker_radio`) — même logique defensive que
  `kantar_rowpicker_radio` (éviter un faux positif d'une stratégie générique non fiable sur ce DOM).
Patterns couverts :
- Bloc radio dont le conteneur est validé par `_is_mui_dialog_question_optimal_container` — options
  `div[role="button"]` sans input natif, libellé porté par `.option-text`
Patterns exclus :
- Blocs non radio (checkbox notamment) sur ce même widget — non couverts par ce flag, chemin générique
  inchangé
- Conteneurs non validés par `_is_mui_dialog_question_optimal_container` → chemin XPath/option_xpath_map
  générique inchangé
Validé sur run réel (clic + navigation CTA) le 24/07/2026.

---

## MISE A JOUR : IPSOS-NORM MUI REACT (dialog-question) — variante checkbox

Statut : le même widget `dialog-question` peut aussi se présenter en sélection multiple, avec
une case à cocher native (`input[type="checkbox"]`) visible dans chaque option — signal absent
de la variante radio décrite dans les sections précédentes. Sans ce patch, ce cas était détecté
comme itype=radio/max_select=1 (comportement par défaut du bloc button_group générique), ce qui
limitait artificiellement le nombre de valeurs renvoyables alors que la question autorisait
plusieurs sélections.

### Détection itype checkbox — `_is_mui_dialog_checkbox`
Fichier : Survey/dom_analyzer.py
Emplacement : boucle btn_groups, juste après le calcul de `_is_choice_multiple` (guard
interview-layout `ChoiceMultiple_ChoiceFields` / `image-select`), avant l'affectation de
`_block_itype`/`_block_max_select`.
Guard DOM strict : `_is_mui_dialog_question_optimal_container(cont)` vrai ET au moins un
`input[type="checkbox"]` présent dans le conteneur `.dialog-question` (évalué uniquement si
`_is_choice_multiple` est faux, donc jamais concurrent avec le guard interview-layout).
Mécanisme : `_block_itype` devient `"checkbox"` (et `_block_max_select = len(options)`) si
`_is_choice_multiple` OU `_is_mui_dialog_checkbox` est vrai — extension additive de la condition
existante, aucune branche radio existante modifiée.
Patterns couverts :
- Widget `dialog-question` avec options portant un `input[type="checkbox"]` natif visible
  (ex. question "Veuillez sélectionner toutes les réponses qui s'appliquent")
Patterns exclus :
- Widget `dialog-question` variante radio (options `div[role="button"]` sans input natif) →
  `_is_mui_dialog_checkbox` reste faux, chemin radio existant inchangé
- Tout conteneur non validé par `_is_mui_dialog_question_optimal_container` → non évalué

### Flag registry — `mui_dialog_question_checkbox_option`
Fichier : Survey/dom_analyzer.py
Emplacement : juste après l'enregistrement du flag `mui_dialog_question_option` (variante radio),
dans le même bloc `_reg_ctx`.
Guard : `_block_itype == "checkbox" and _is_mui_dialog_checkbox`.
Flag strictement distinct du flag radio existant — jamais posé simultanément (mutuellement
exclusifs via `_block_itype`).

### click_mui_dialog_question_checkbox_option (Survey/input_radio.py)
Fichier : Survey/input_radio.py (fonction distincte, n'affecte jamais `click_mui_dialog_question_option`).
Guard DOM strict : conteneur validé par `_is_mui_dialog_question_optimal_container` ET au moins un
`.dialog-question input[type="checkbox"]` présent (posé en amont dans dom_analyzer.py).
Mécanisme : même résolution par comparaison de texte normalisée en JS que la variante radio
(`.dialog-question .option-text`, le XPath positionnel étant invalidé par le re-render React),
mais ciblage direct de l'`input[type="checkbox"]` de l'option (via `li.querySelector('input[type="checkbox"]')`)
au lieu de l'overlay `[role="button"]`. Vérification déterministe via `input.checked === true`
(plus fiable sur ce widget que la classe `Mui-selected` utilisée côté radio). Court-circuite si
l'option est déjà cochée (`already_checked`, idempotent pour les dispatchs multi-valeurs).
Déclenchement (additif, ordre de priorité) :
- action_dispatcher.py, chemin dédié checkbox (avant `_apply_by_target_id`, même schéma que
  `kantar_rowpicker_checkbox`) : si `_p.get("mui_dialog_question_checkbox_option") and itype ==
  "checkbox"` → `skip_apply_by_target_id = True`, appel direct de
  `click_mui_dialog_question_checkbox_option`, retour immédiat (pas de fallback générique sur échec —
  même logique défensive que la variante radio et que `kantar_rowpicker_checkbox`).
Patterns couverts :
- Bloc checkbox dont le conteneur est validé par `_is_mui_dialog_question_optimal_container`, avec
  options portant un `input[type="checkbox"]` natif dans leur `li`
Patterns exclus :
- Bloc radio du même widget (`mui_dialog_question_option`) → chemin dédié radio inchangé
- Conteneurs non validés par `_is_mui_dialog_question_optimal_container` → chemin générique checkbox
  inchangé (`_apply_by_target_id` / stratégies génériques)
Validé sur run réel (sélection multiple + navigation CTA) le 24/07/2026.

### _handle_topsurveys_genial_reward_popup

Fichier : Survey/functions.py

Rôle : détecte et ferme un popup de récompense/remerciement TopSurveys dont le
bouton de validation affiche "Genial", sans forcer de navigation après
fermeture (le flux appelant reprend son cours normal).

Garde DOM (strict) : bouton visible dont le texte normalisé (accents retirés,
minuscule, espaces de bord retirés) == "genial". Recherche prioritaire via
button[data-test-id='ps-common-actions-button'], fallback sur tous les
boutons visibles de la page si le sélecteur ciblé ne matche pas.

Intégration pipeline : appelée en priorité 0 (avant Mystery boxes et
"Bon travail !") depuis _handle_topsurveys_exclusion_popup, elle-même
invoquée depuis :
  - main.py → run_attach_takeover() (mode ATTACH)
  - Survey/survey_executor.py
  - Survey/survey_handler.py
aux moments de chargement de la page listing TopSurveys, de retour dessus,
ou de retour après clic sur un sondage.

Patterns couverts : popup de bonus périodique / remerciement avec bouton
libellé "Genial" (DOM de référence : classe periodic-bonus-popup,
data-test-id="ps-periodic-bonus-popup" / "ps-periodic-bonus-close").

Patterns exclus (gérés par les branches existantes, non modifiées) :
  - Mystery box popup (data-test-id^="ps-mystery-box-item-button")
  - Popup "Bon travail !" (texte "bon travail" / "tu as partiellement
    repondu" / "credite ton compte")

CTA : clic conditionné par is_cta_intercept_only() (config.py). Comportement
vérifié identique en mode attach et en mode prod : False par défaut dans les
deux cas, sauf activation explicite de CTA_INTERCEPT_ONLY par variable
d'environnement.

Compatibilité mode ATTACH : validée. driver est le même objet Page Playwright
qu'en mode normal (obtenu via connect_over_cdp dans
attach_browser_playwright), et la fonction n'utilise que des méthodes
Playwright standard (query_selector_all, is_visible, inner_text, click).
Aucune divergence de comportement entre les deux modes.

Budget : 1 scan de détection, 1 tentative de clic. Retourne False si le
bouton n'est pas trouvé.

Statut : patch validé.

## MODULE TRANSVERSAL : RELOAD_RETRY — RÉCUPÉRATION PAGE BLOQUÉE SANS ÉLÉMENT ACTIONNABLE (execute_survey_page)

### RELOAD_RETRY (bloc inline en fin de Survey/survey_executor.py::execute_survey_page)

Fichier : Survey/survey_executor.py

Rôle : quand aucune stratégie de clic CTA ni aucun élément actionnable n'a pu
être trouvé sur la page courante (fin de la cascade CTA_FALLBACK), tente un
rechargement borné de la page en cours et relance la détection DOM
(dom_analyzer.analyze_dom) avant d'abandonner définitivement. Cible
principalement les pages de redirection intermédiaire figées sur un loader
(spinner) sans contenu exploitable, y compris quand aucun signal texte
("please wait", "veuillez patienter"...) n'est présent — c'est un mécanisme
générique, pas basé sur du texte.

Garde : `page.is_closed()` vérifié avant chaque tentative de reload — n'agit
pas si la page/contexte est déjà fermé (reload garanti en échec dans ce cas,
cf. TargetClosedError déjà observé sur d'autres fallbacks de navigation dans
preselection/survey_navigator.py).

Intégration pipeline : dernier recours dans execute_survey_page, après
l'échec de toutes les phases CTA_FALLBACK (texte, ID, structurel), avant le
retour False final (abort_reason=dom_no_match_abort). Ne modifie aucune
stratégie CTA existante — bloc additif en fin de fonction.

Budget : 2 tentatives de reload max (`_RELOAD_RETRY_MAX = 2`), 2s d'attente
après chaque reload avant re-détection. Si un bloc actionnable est détecté
après reload → return True (reprise normale du flux). Si les 2 tentatives
échouent ou si la page est fermée → poursuite vers l'abandon existant
(inchangé).

Validation : mécanisme équivalent déjà validé indépendamment dans
preselection/survey_navigator.py::_reload_and_retry_surveys_tab (même
principe : reload borné + re-détection, sans signal texte), confirmé en
conditions réelles sur une page de redirection tierce (new.surveylion.com)
qui restait bloquée à 0% et a progressé normalement vers le sondage cible
après reload.

Remplace : l'ancien bloc WAIT_PAGE conditionné par la variable d'environnement
PROXY_LATENCY_MODE (détection par signaux texte, désactivée par défaut,
ne couvrait pas les pages bloquées sans texte identifiable). PROXY_LATENCY_MODE
a été retiré du projet — ne plus le chercher ni le référencer dans un
diagnostic futur.

## MODULE TRANSVERSAL : CONSENT_SCREEN_STUCK_RELOAD — RECHARGEMENT BORNÉ SUR ÉCRAN DE CONSENTEMENT BLOQUÉ (execute_survey_page)

### _consent_screen_stuck_reload_retry (Survey/survey_executor.py)

Fichier : Survey/survey_executor.py

Rôle : couvre un cas distinct de RELOAD_RETRY (ci-dessus). Quand le
dom_classifier détecte itype="consent_screen" à chaque itération et que le
handler correspondant (handle_consent_screen, via action_dispatcher) retourne
False de façon répétée sur un DOM inchangé — checkbox de consentement cochée,
boutons CTA visibles, mais clic sans effet observable (pas de navigation, pas
de changement DOM) — déclenche un reload borné de la page pour tenter de
débloquer l'état. RELOAD_RETRY ne couvre pas ce cas car il ne s'invoque que
lorsqu'aucun élément actionnable n'est détecté ; ici un consent_screen valide
est détecté à chaque passage, donc cette branche n'est jamais atteinte.

Garde : état conservé par instance driver (`id(driver)` comme clé, dict module
`_CONSENT_SCREEN_STUCK_STATE`) car execute_survey_page est ré-invoquée à
chaque step avec des locals réinitialisés. Signature DOM légère comparée via
`cta_handler._dom_progress_marker(driver)` — reload déclenché seulement si la
signature reste identique `_CONSENT_SCREEN_STUCK_BUDGET` fois consécutives
(= 3). `driver.is_closed()` vérifié avant tout reload.

Intégration pipeline : dans execute_survey_page, immédiatement après l'appel
du handler quand `itype == "consent_screen" and not handler_result` — ne
modifie ni handle_consent_screen ni aucune stratégie de clic CTA existante
(bloc additif, appelé seulement en cas d'échec du handler).

Budget : 3 échecs consécutifs sur la même signature DOM avant reload
(`_CONSENT_SCREEN_STUCK_BUDGET = 3`), 2s d'attente après reload. Retourne
toujours False (le step courant reste un échec ; la reprise se fait au step
suivant sur un DOM frais après reload, ou retente normalement sinon). Compteur
réinitialisé après déclenchement du reload.

Contexte de détection : observé sur écran de consentement Ipsos
(enter.ipsosinteractive.com) — logs montrant strategy=press_click_release en
boucle avec wait_reason=timeout, target_changed=false, progressed=false,
malgré un rendu visuel apparemment normal (checkbox cochée, CTA visibles).

Statut : patch validé.


## MODULE TRANSVERSAL : CTA_NAV_BAD_KEYWORD_SUBSTRING_FALSE_POSITIVE — FAUX POSITIF FILTRE ANTI-RETOUR SUR SOUS-CHAÎNE "BACK"

### try_click_navigation_cta — faux positif du filtre anti-bouton-retour sur substring "back"

Fichier : Survey/cta_handler.py (boucle générique de collecte de candidats, vérification bad_keyword_check)

Bug corrigé : le filtre destiné à exclure les boutons "retour/annuler/quitter/précédent"
testait la présence des mots de la liste `bad` comme simple sous-chaîne (`in signature`)
dans la signature de l'élément (texte + id + name + classes + href + role concaténés).
Sur une page Ifop/SSI (s2.ifoponline.com), le seul CTA réel de la page
(`div#next_button`, role="button") porte une classe CSS `background_primary_color`
(couleur de fond), qui contient la sous-chaîne "back" issue de "background" — sans
rapport avec un bouton "retour". Le filtre l'excluait donc à tort, ne laissant plus
aucun candidat ("CTA_NOT_FOUND (no candidates)").

Correction : remplacement du test de sous-chaîne par un match sur mot entier
(`re.search(rf"\b{re.escape(b)}\b", signature)`), pour chaque mot de `bad`. `\b` s'appuie
sur les frontières `\w` (lettres/chiffres/underscore, y compris accents Unicode) : "back"
dans "background" n'a pas de frontière après (suivi de "g"), donc n'est plus détecté,
tandis qu'un vrai token "back"/"retour"/"précédent" isolé (espace, tiret, début/fin de
chaîne) reste détecté normalement.

Patterns couverts :
- Tout CTA dont un attribut (classe CSS notamment) contient accidentellement une des
  sous-chaînes de `bad` sans former un mot entier (ex. "background", et par extension
  tout autre attribut composé où un des mots de `bad` apparaît comme fragment interne).

Patterns exclus :
- Aucune régression sur la détection de vrais boutons "retour/annuler/quitter/précédent" :
  ceux-ci restent détectés tant que le mot apparaît isolé (espace, tiret, ponctuation,
  début/fin de chaîne) dans la signature.

Diagnostic associé : confirmé via instrumentation temporaire [CTA_NAV_DIAG]
(nav_xpath_matched count=5, no_candidates matched=5 retained=0
exclusions=[bad_keyword_match=1 no_text_no_nav_keyword=3 not_visible_or_disabled=1]),
isolant précisément le candidat exclu à tort avant correction. Après patch :
CTA_FOUND candidate score=110, clic réussi (strategy=press_click_release), navigation
détectée après CTA (confirmé en conditions réelles sur s2.ifoponline.com, page
"Vous êtes... ?" → "Dans quelle tranche d'âge vous vous situez ?").

Statut : patch validé.