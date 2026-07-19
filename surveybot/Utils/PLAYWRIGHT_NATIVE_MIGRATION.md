# PLAYWRIGHT_NATIVE_MIGRATION.md
# Suivi de la migration franche (Option B) : suppression du shim Selenium,
# bascule vers l'API Playwright native (Page, Locator, frame_locator...).
# Démarré : 2026-06-21
# Scope initial : mode attach uniquement.
# Scope étendu au 2026-06-22 : migration globale (attach + prod / Fly.io).
# Phase S (suppression shim) démarrée le 2026-06-22.

================================================================================
PRINCIPE DU DÉCOUPAGE
================================================================================

Le chantier est découpé en BLOCS FONCTIONNELS (pas fichier par fichier isolé),
chacun suivant une chaîne d'appel réelle, testable seul avant de passer au suivant.

⚠️ PÉRIMÈTRE PARTIEL DES BLOCS ATTACH (1, 2, 3x) — RÈGLE DE LECTURE CRITIQUE :
Les blocs 1–3b ont été migrés dans le cadre du chantier attach. Chaque bloc
couvrait UNIQUEMENT les fonctions utilisées dans le chemin d'appel attach.
"BLOC N migré" signifie "les fonctions du chemin listées dans ce bloc sont
migrées". Cela ne signifie PAS que l'ensemble du fichier est migré.

NOTE SUR LES RÉSIDUS SHIM (blocs 3b) :
Plusieurs fichiers migrés en attach (dom_analyzer, cta_handler, input_checkbox,
input_radio, input_matrix, input_utils, input_frame, action_dispatcher) utilisent
encore find_element / find_elements / execute_script via string literals plutôt
que By.*. Ces appels passent par le shim (PlaywrightDriverShim → page.query_selector_all).
Ils seront supprimés dans les blocs S correspondants.

================================================================================
INVENTAIRE COMPLET — ÉTAT PAR FICHIER (lecture directe 2026-06-22)
================================================================================

PROPRES (zéro driver ou Playwright natif pur) :
  launch.py                            ✅ propre prod ; résidus attach intentionnels
  main.py                              ✅ propre prod ; résidus attach intentionnels
  preselection/auth_handler.py         ✅
  preselection/survey_navigator.py     ✅
  preselection/survey_handler.py       ✅ prod ; résidu attach intentionnel
  preselection/question_analyzer.py    ✅
  preselection/response_executor.py    ✅
  preselection/question_validation.py  ✅ (zéro driver)
  preselection/config_loader.py        ✅ (zéro driver)
  preselection/secret_loader.py        ✅ (zéro driver)
  preselection/chrome_profile_store.py ✅ (zéro driver)
  Management/redirect_watcher.py       ✅
  Management/notifier.py               ✅ (zéro driver)
  Management/pause_policy.py           ✅ (zéro driver)
  Management/survey_difficulty_guard.py ✅ migré BLOC S1 (2026-06-22)
  hot_reload/hot_reload.py             ✅ (zéro driver)
  State/daily_target.py                ✅ (zéro driver)
  State/survey_memory.py               ✅ (zéro driver)
  State/account_state.py               ✅ (zéro driver)
  platforms/base.py                    ✅ (zéro driver)
  platforms/__init__.py                ✅ (zéro driver)
  Survey/survey_solver.py              ✅ migré BLOC 3a
  Survey/survey_executor.py            ✅ migré BLOC 3b1
  Survey/survey_context.py             ✅ (zéro driver)
  Survey/page_snapshot.py              ✅ migré BLOC 3b2
  Survey/dom_classifier.py             ✅
  Survey/dom_frame_selector.py         ✅
  Survey/dom_selection_rules.py        ✅ (zéro driver)
  Survey/dom_registry.py               ✅ (zéro driver)
  Survey/action_types.py               ✅ (zéro driver)
  Survey/prompt_builder.py             ✅ (zéro driver)
  Survey/batch_response_parser.py      ✅ (zéro driver)
  Survey/log_utils.py                  ✅ (zéro driver)
  Survey/fivesim_client.py             ✅ (zéro driver)
  Survey/frame_utils.py                ✅ migré BLOC 3b5a
  Survey/input_handler.py              ✅ migré BLOC 3b5d
  Survey/input_dropdown.py             ✅ migré BLOC 3b5c
  Survey/input_slider.py               ⚠️ classé migré BLOC 3b5c mais résidu Selenium
                                          trouvé le 2026-07-19, cf HISTORIQUE — find_elements()
                                          string-literal non converti, échoue silencieusement
                                          depuis suppression du shim (BLOC S8)
  Survey/input_text.py                 ✅ migré BLOC 3b5c

MIGRÉS AVEC RÉSIDUS SHIM (à nettoyer dans les blocs S) :
  Survey/dom_analyzer.py               ✅ migré BLOC 3b4 — find_elements via shim
  Survey/cta_handler.py                ✅ migré BLOC 3b5b — find_elements via shim
  Survey/input_utils.py                ✅ migré BLOC 3b5b — find_element via shim (×3)
  Survey/input_frame.py                ✅ migré BLOC 3b5b — switch_to.frame dans
                                          in_each_frame_recursive
  Survey/input_checkbox.py             ✅ migré BLOC 3b5c — find_elements/execute_script via shim
  Survey/input_radio.py                ✅ migré BLOC 3b5c — find_element via shim
  Survey/input_matrix.py               ✅ migré + fix BLOC 3b5c — find_element via shim
  Survey/action_dispatcher.py          ✅ migré BLOC 3b6 — 92 execute_script via shim

SPÉCIAUX :
  preselection/playwright_shim.py      🔲 C'EST le shim — sera supprimé en BLOC S8
  preselection/playwright_launcher.py  ✅ propre prod ; résidus dans fonctions legacy non appelées

NON MIGRÉS — À traiter dans les blocs S :
  Survey/dom_context_mapper.py         🔲 By, ActionChains, execute_script (×3), find_element,
                                          current_url — BLOC S2
  Survey/dom_question_extractor.py     🔲 By, find_element (×5), find_elements (×5),
                                          execute_script (×2) — BLOC S2
  Survey/dom_utils.py                  🔲 By, find_elements(By.XPATH) (×8+), execute_script — BLOC S2
  Survey/dom_extractors_areyounet.py   🔲 By, find_elements/find_element (×20+) — BLOC S3
  Survey/dom_extractors_decipher.py    🔲 By, find_elements — BLOC S3
  Survey/dom_extractors_misc.py        ✅ migré BLOC S3b (2026-06-23)
  Survey/dropdown_block_resolver.py    ✅ migré BLOC S4 (2026-06-23)
  Survey/question_block_analyzer.py    ✅ migré BLOC S4 (2026-06-23)
  Survey/question_block_resolver.py    ✅ migré BLOC S4 (2026-06-23)
  Survey/sliderpoints_extractor.py     ✅ migré BLOC S4 (2026-06-23)
  Survey/screenshot_analyzer.py        ✅ migré BLOC S5 (2026-06-23)
  Management/guards/runtime_guard.py   ✅ migré BLOC S5 (2026-06-23)
  Management/snap_uploader.py          ✅ migré BLOC S5 (2026-06-23)
  captcha/datadome_handler.py          ✅ migré BLOC S6 (2026-06-23)
  captcha/normal_captcha.py            ✅ migré BLOC S6 (2026-06-23)
  captcha/recaptcha_handler.py         ✅ migré BLOC S6 (2026-06-23)
  captcha/recaptcha_utils.py           ✅ migré BLOC S6 (2026-06-23)
  captcha/tencent_handler.py           ✅ migré BLOC S6 (2026-06-23)
  Cash/payout.py                       ✅ migré BLOC S6 (2026-06-23)
  Survey/functions.py                  ✅ migré BLOC S6 (2026-06-23)
  platforms/topsurveys.py              ✅ migré BLOC S7a (2026-06-23)
  platforms/ysense.py                  ✅ migré BLOC S7a (2026-06-23)
  Survey/input_frame.py                ✅ migré BLOC S7a (2026-06-23)
    (iter_iframes_safe + in_each_frame_recursive — switch_to.frame supprimé)

RÉSIDUS SHIM STRING-LITERAL — ✅ nettoyés dans BLOC S7b (2026-06-24) :
  Survey/dom_analyzer.py, cta_handler.py, input_utils.py,
  input_checkbox.py, input_radio.py, input_matrix.py, action_dispatcher.py

================================================================================
DÉCOUPAGE EN BLOCS
================================================================================

--- BLOCS ATTACH (historique) ---

BLOC 1 — Login + sélection de survey          ✅ migré (attach 2026-06-21)
BLOC 2 — Résolution pop-up présélection       ✅ migré (attach 2026-06-22)
BLOC 3a — survey_solver.py                    ✅ migré (attach 2026-06-22)
BLOC 3b1 — survey_executor.py                 ✅ migré (attach 2026-06-22)
BLOC 3b2 — page_snapshot + redirect_watcher   ✅ migré (attach 2026-06-22)
BLOC 3b3 — dom_classifier + batch_parser      ✅ migré (attach 2026-06-22)
BLOC 3b4 — dom_analyzer                       ✅ migré (attach 2026-06-22)
BLOC 3b5a — frame_utils                       ✅ migré (attach 2026-06-22)
BLOC 3b5b — input_utils + cta_handler         ✅ migré (attach 2026-06-22)
BLOC 3b5c — 7 × input_*.py                   ✅ migré (attach 2026-06-22)
BLOC 3b5d — input_handler                     ✅ migré (attach 2026-06-22)
BLOC 3b5d-fix — input_matrix corrections      ✅ corrigé (attach 2026-06-22)
BLOC 3b6 — action_dispatcher                  ✅ migré (attach 2026-06-22)

--- BLOCS PROD (historique) ---

BLOC P1 — launch.py                           ✅ migré (2026-06-22)
BLOC P2 — survey_handler._run_survey_impl
          + response_executor._click_radio_label ✅ migré (2026-06-22)
BLOC P3 — redirect_watcher + question_analyzer ✅ migré (2026-06-22)

--- BLOCS S (suppression shim) ---

BLOC S1 — Management/guards/survey_difficulty_guard.py
  Statut : ✅ migré (2026-06-23)
  Migrations appliquées :
  - Import By supprimé.
  - _pw_page(d) helper ajouté en tête.
  - find_elements(By.CSS_SELECTOR, sel) → page.query_selector_all(sel)
  - find_elements(By.TAG_NAME, tag) → page.query_selector_all(tag)
  - execute_script("return document.body.innerText || ''")
    → page.evaluate("() => document.body.innerText || ''")
  - el.is_displayed() → el.is_visible()
  - el.rect → el.bounding_box() (retourne dict ou None, protégé par "or {}")
  - el.tag_name → el.evaluate("e => e.tagName.toLowerCase()")
  - el.parent.execute_script("return !!arguments[0].closest(...)", el)
    → el.evaluate("e => !!e.closest('.g-recaptcha-badge, .grecaptcha-badge')")
  - Logique métier de toutes les fonctions conservée à l'identique.

BLOC S2 — Survey/dom_context_mapper.py + dom_question_extractor.py + dom_utils.py
  Statut : ✅ migré (2026-06-23)
  Migrations appliquées (commun aux 3 fichiers) :
  - Import By (et ActionChains pour dom_context_mapper) supprimés.
  - _pw_page(d) helper ajouté en tête de chaque fichier.
  - find_elements(By.CSS_SELECTOR/XPATH/TAG_NAME, ...) → query_selector_all("xpath=..." ou css)
  - find_element(By.CSS_SELECTOR/ID, ...) → query_selector(...) + None guard (pas d'exception)
  - execute_script(js, el) → page.evaluate(fn_form, el) ; JS converti en (el) => { ... }
  - execute_script(js, el, arg) → page.evaluate(fn_form, [el, arg]) ; ([el, arg]) => { ... }
  - el.is_displayed() → el.is_visible() ; wrapper.is_displayed() → wrapper.is_visible()
  - el.rect → el.bounding_box() or {}
  - el.tag_name → el.evaluate("e => e.tagName.toLowerCase()")
  - el.text (Selenium) → el.inner_text() ; el.get_attribute("textContent") → el.text_content()
  - el.find_elements(By.XPATH, "ancestor::...") → el.query_selector_all("xpath=ancestor::...")
  - el.find_elements(By.XPATH, "following-sibling::...") → el.query_selector_all("xpath=following-sibling::...")
  - el.find_elements(By.XPATH, "preceding-sibling::...") → el.query_selector_all("xpath=preceding-sibling::...")
  - el.find_elements(By.TAG_NAME, "option") → el.query_selector_all("option")
  dom_context_mapper spécifique :
  - ActionChains + modes js/ac dans try_click_matrix_by_visual_mapping supprimés.
  - Boucle 3 modes réduite à un clic natif unique (el.click()).
  - el.is_selected() → el.is_checked().
  - driver.execute_script("arguments[0].scrollIntoView(...)") → el.scroll_into_view_if_needed()
  - driver.current_url → _pw_page(driver).url (dans _build_visual_matrix_map)
  dom_utils spécifique :
  - _best_xpath_for_element : driver.execute_script(script, el) → _pw_page(driver).evaluate(fn, el)
  - _dropdown_field_hint : find_elements(By.TAG_NAME, "option") → el.query_selector_all("option")
  - _is_actionable_visible : is_displayed() → is_visible() ; find_elements(By.XPATH) → query_selector_all("xpath=...")
  dom_question_extractor spécifique :
  - _find_question_text_near_element, _find_associated_label, _find_group_heading_text_near_element :
    driver.execute_script(js, el[, option_keys]) → page.evaluate(fn, el) / page.evaluate(fn, [el, option_keys])
  - Logique métier de toutes les fonctions conservée à l'identique (aucun extracteur modifié).

BLOC S3a — Survey/dom_extractors_areyounet.py + dom_extractors_decipher.py
  Statut : ✅ migré (2026-06-23)
  Migrations appliquées (commun aux 2 fichiers) :
  - Import By supprimé. _pw_page(d) helper ajouté en tête.
  - driver.find_elements(By.CSS_SELECTOR/ID, ...) → _pw_page(driver).query_selector_all(...)
  - driver.find_element(By.CSS_SELECTOR/ID, ...) → _pw_page(driver).query_selector(...) + None guard
  - el.find_elements(By.CSS_SELECTOR/TAG_NAME, ...) → el.query_selector_all(...)
  - el.find_element(By.CSS_SELECTOR, ...) → el.query_selector(...) + None guard
  - el.find_element(By.XPATH, "following-sibling::X[1]") → el.query_selector("xpath=following-sibling::X[1]") + None guard
  - el.find_element(By.XPATH, "ancestor::X[1]") → el.query_selector("xpath=ancestor::X[1]") + None guard
  - el.find_element(By.XPATH, "ancestor::*[...][1]//Y") → el.query_selector("xpath=ancestor::*[...][1]//Y") + None guard
  - el.find_element(By.XPATH, "..") → el.query_selector("xpath=..") + None guard
  - el.find_element(By.XPATH, "ancestor::label[1]") → el.query_selector("xpath=ancestor::label[1]") + None guard
  - el.find_element(By.XPATH, "following-sibling::label[1]") → el.query_selector("xpath=following-sibling::label[1]") + None guard
  - el.text → el.inner_text() ; el.get_attribute("textContent") → el.text_content()
  - el.text or el.get_attribute("innerText") → el.inner_text()
  - driver.execute_script(js, arg) → _pw_page(driver).evaluate(fn, arg) [QARTS autosubmit]
  - field.tag_name → field.evaluate("e => e.tagName.toLowerCase()") [×2 dans decipher]
  - Helpers internes _visible_text, _extract_label_text, _label_text migrés (.text → .inner_text())
  - Logique métier de tous les extracteurs conservée à l'identique.

BLOC S3b — Survey/dom_extractors_misc.py
  Statut : ✅ migré (2026-06-23)
  Périmètre : extracteurs DOM provider divers (~12 600 lignes, ~76 fonctions publiques),
              appelés depuis dom_analyzer.
  Migrations appliquées :
  - Import By supprimé. _pw_page(d) helper ajouté en tête.
  - driver.find_elements(By.CSS_SELECTOR, ...) → _pw_page(driver).query_selector_all(...)
  - driver.find_element(By.CSS_SELECTOR, ...) → _pw_page(driver).query_selector(...)
  - driver.find_element(By.ID, expr) → _pw_page(driver).query_selector("#id") (littéral)
    ou _pw_page(driver).query_selector(f"#id_var") / f"#f-string_merged"
  - el.find_elements(By.CSS_SELECTOR/TAG_NAME, ...) → el.query_selector_all(...)
  - el.find_elements(By.XPATH, expr) → el.query_selector_all("xpath=" + expr)
  - el.find_element(By.CSS_SELECTOR, ...) → el.query_selector(...)
  - el.find_element(By.XPATH, expr) → el.query_selector("xpath=" + expr)
  - Appels multi-lignes find_elements(/find_element)(\n    By.TYPE, sel\n) normalisés
    en une ligne avant transformation.
  - Appels sur éléments indexés (var[0].find_element...) couverts.
  - execute_script("return arguments[0].innerText || '';", el) → el.inner_text()
  - execute_script("arguments[0].scrollIntoView(...);", el) → el.evaluate("(el) => el.scrollIntoView(...)")
  - execute_script("arguments[0].click();", el) → el.evaluate("(el) => el.click()")
  - execute_script(multiline_js_with_args, el) → _pw_page(driver).evaluate("""(_el) => { js }""", el)
  - execute_script(no_arg_js) → _pw_page(driver).evaluate("""() => { js }""")
  - _JS_DIRECT_TEXT (local) réécrit en arrow function ; appels → el.evaluate(_JS_DIRECT_TEXT)
  - el.text / var.text (Selenium) → el.inner_text() (tous les noms de variables couverts,
    y compris accès indexés var[0].text)
  - el.text or el.get_attribute("innerText") or "" → el.inner_text() or ""
  - from selenium.webdriver.common.keys import Keys as _Keys (inline dans corps) supprimé ;
    combobox.send_keys(Keys.ESCAPE) → combobox.press("Escape")
  - Signatures et logique métier des 76 fonctions publiques conservées à l'identique.
  - Résultat : 0 By., 0 from selenium, 0 execute_script. Syntaxe Python vérifiée (py_compile).

BLOC S4 — Survey/dropdown_block_resolver.py + question_block_analyzer.py
          + question_block_resolver.py + sliderpoints_extractor.py
  Statut : ✅ migré (2026-06-23)
  Périmètre : résolveurs de blocs questions, appelés depuis survey_executor.
  Migrations appliquées (commun aux 4 fichiers) :
  - Imports By / WebElement / Keys / ActionChains / Select supprimés.
  - _pw_page(d) helper ajouté en tête de chaque fichier.
  - WebElement (annotations de type) → Any (typing).
  - el.is_displayed() → el.is_visible().
  - el.rect → el.bounding_box() or {}.
  - el.tag_name → el.evaluate("e => e.tagName.toLowerCase()").
  - el.text → el.inner_text().
  - driver.find_elements(By.CSS_SELECTOR/TAG_NAME, ...) → _pw_page(driver).query_selector_all(...)
  - driver.find_element(By.CSS_SELECTOR/TAG_NAME, ...) → _pw_page(driver).query_selector(...) + None guard
  - el.find_elements(By.CSS_SELECTOR/TAG_NAME, ...) → el.query_selector_all(...)
  - el.find_element(By.CSS_SELECTOR, ...) → el.query_selector(...) + None guard
  - el.find_element(By.XPATH, "ancestor::...") → el.query_selector("xpath=ancestor::...") + None guard
  - el.find_elements(By.XPATH, xp) → el.query_selector_all("xpath=" + xp)
  - el.find_elements(By.XPATH, "//label[@for=...]") → el.query_selector_all("xpath=//label[@for=...]")
    (XPath absolu → recherche depuis racine, comportement identique via ElementHandle)
  question_block_resolver.py spécifique :
  - _is_numeric_input : el.tag_name → el.evaluate("e => e.tagName.toLowerCase()")
  - _nearest_container : find_element(By.XPATH, xp) → query_selector("xpath=" + xp) + None guard
  - _extract_label_from_dom : driver.find_element(By.XPATH/ID) → _pw_page(driver).query_selector(...)
    + None guard ; container.find_elements(By.XPATH, HEAD_XPATH) → query_selector_all("xpath=...")
  - _dispatch_events : execute_script(js, el) → el.evaluate("(e) => { ... }")
  - _safe_focus : execute_script(scrollIntoView) → el.scroll_into_view_if_needed() ;
    ActionChains(...).move_to_element(el).click() → el.hover(); el.click() ;
    execute_script(focus) → el.focus()
  - _safe_clear : send_keys(Keys.CONTROL, "a") → el.press("Control+a") ;
    send_keys(Keys.BACKSPACE) → el.press("Backspace") ;
    execute_script("arguments[0].value = '';", el) → el.evaluate("(e) => { e.value=''; ... }")
  - fill_number_input : send_keys(v) → input_el.type(v) ;
    execute_script("arguments[0].value = arguments[1];", el, v) → el.evaluate("(e,v) => {...}", v) ;
    send_keys(Keys.TAB) → input_el.press("Tab") ;
    el.rect.get("y") → el.bounding_box().get("y")
  dropdown_block_resolver.py spécifique :
  - _visible : is_displayed() + el.rect[...] → is_visible() + (bounding_box() or {}).get(...)
  - Select(trigger).first_selected_option.text → trigger.evaluate("e => e.options[e.selectedIndex]?.text || ''")
  - execute_script(scrollIntoView) → best.trigger.scroll_into_view_if_needed()
  - execute_script("arguments[0].click()", el) → el.click()
  - Import inline Select (ligne 210 origine) supprimé.
  sliderpoints_extractor.py spécifique :
  - _extract_continue_button : find_element → query_selector + None guard explicite (if el is None: continue)
  - extract_sliderpoints_question_blocks : find_element(".sq-question-text") → query_selector(...) + None guard ;
    find_element("select") → query_selector("select") + None guard (if sel is None: continue) ;
    find_element(".sq-sliderpoints-row-legend") → query_selector(...) + None guard.
  - Signatures et logique métier de toutes les fonctions publiques conservées à l'identique.
  - Résultat : 0 By., 0 selenium, 0 execute_script dans les 4 fichiers. Syntaxe Python validée.

BLOC S5 — Survey/screenshot_analyzer.py + Management/guards/runtime_guard.py
          + Management/snap_uploader.py
  Statut : ✅ migré (2026-06-23)
  Périmètre : monitoring, screenshot, guard.
  Migrations appliquées :
  - _pw_page(d) helper ajouté en tête des 3 fichiers.
  runtime_guard.py spécifique :
  - Imports By / WebDriverWait / EC supprimés.
  - WebDriverWait(driver, 6).until(EC.element_to_be_clickable((By.XPATH, "//button[...] | //a[...]")))
    → boucle sur 2 XPath séquentiels : wait_for_selector(f"xpath={xp}", state="visible", timeout=3000)
    (Playwright ne supporte pas XPath | dans un seul sélecteur ; 3 s × 2 = 6 s total conservé)
  - execute_script(scrollIntoView) → cta.scroll_into_view_if_needed()
  - execute_script(click) → cta.click(), conditionné à CTA_INTERCEPT_ONLY :
    si actif → log interception OK + return True sans clic réel.
  screenshot_analyzer.py spécifique :
  - Bloc CDP entier supprimé (Page.getLayoutMetrics, Emulation.setDeviceMetricsOverride,
    Page.enable, Page.captureScreenshot, Emulation.clearDeviceMetricsOverride, 4 execute_cdp_cmd)
    → remplacé par un seul appel page.screenshot(full_page=True, path=out_path).
  - driver.save_screenshot(out_path) (viewport + fallbacks) → page.screenshot(path=out_path).
  - _stitch_fullpage migré : execute_script(scrollTo/scrollHeight/innerHeight/innerWidth)
    → page.evaluate(f"() => ...") ; save_screenshot(part) → page.screenshot(path=part).
  - driver.get_screenshot_as_png() absent de ce fichier.
  snap_uploader.py spécifique :
  - Fallback 2 : driver.get_screenshot_as_png() → _pw_page(driver).screenshot() (bytes directs).
  - Fallback 3 : driver.save_screenshot(path) → _pw_page(driver).screenshot(path=path)
    + lecture fichier conservée (logique 3 niveaux préservée).
  - Résultat : 0 By., 0 selenium, 0 execute_cdp_cmd, 0 execute_script dans les 3 fichiers.
    Syntaxe Python validée (py_compile).

BLOC S6 — captcha/* + Cash/payout.py + Survey/functions.py
  Statut : ✅ migré (2026-06-23)
  Périmètre : chemins captcha, encaissement, popup post-survey. 7 fichiers.
  Migrations appliquées (commun) :
  - _pw_page(d) helper ajouté en tête des 7 fichiers.
  - Tous les imports Selenium (By, WebDriverWait, EC, ActionChains, exceptions) supprimés,
    y compris les imports inline dans les corps de fonctions.
  recaptcha_utils.py :
  - find_elements(By.CSS_SELECTOR) × 3 → query_selector_all.
  - execute_script(js) → page.evaluate("() => {" + js + "}").
  - execute_script(js, token) → IIFE (function(tok){...})(arguments[0]) converti en
    arrow function (tok) => {...} ; page.evaluate(fn, token).
  recaptcha_handler.py :
  - JS _fire_recaptcha_callbacks et _cfg_js : IIFE → arrow function ; execute_script → evaluate.
  - execute_script verify token → page.evaluate("() => { ... }").
  - driver.current_url → page.url.
  datadome_handler.py :
  - execute_script(iframe detect) → page.evaluate("() => { ... }").
  - execute_script("return navigator.userAgent") → page.evaluate("() => navigator.userAgent").
  - driver.current_url → page.url.
  - driver.add_cookie({...}) → page.context.add_cookies([{...}]).
  - driver.refresh() → page.reload().
  normal_captcha.py :
  - find_elements(By.CSS_SELECTOR) × 2 → query_selector_all.
  - el.is_displayed() → el.is_visible().
  - execute_script(parent traversal, img_el) → img_el.evaluate_handle("(e) => {...}")
    + handle.as_element() (retourne ElementHandle exploitable).
  - img_el.screenshot_as_base64 → base64.b64encode(img_el.screenshot()).decode().
  tencent_handler.py :
  - _extract_app_id JS IIFE → () => {...} ; execute_script → page.evaluate(js).
  - _inject_tencent_token JS 2 args → ([ticket, randstr]) => {...} ;
    execute_script(js, ticket, randstr) → page.evaluate(js, [ticket, randstr]).
  - solve_nielseniq_slider_auto : import inline ActionChains supprimé ;
    ActionChains drag → page.mouse.move/down/move(steps=10)/up.
  - find_element("css selector", ...) → page.query_selector(...) + None guard.
  - execute_script × 3 dans solve_tencent_auto → page.evaluate("() => { ... }").
  - driver.current_url → page.url.
  Survey/functions.py :
  - Imports inline By/WebDriverWait/EC supprimés.
  - execute_script("return document.body.innerText") → page.evaluate("() => ...").
  - driver.current_url → page.url.
  - find_elements(By.CSS_SELECTOR) → query_selector_all.
  - is_displayed() → is_visible(). b.text → b.inner_text().
  - WebDriverWait(driver, 2).until(EC.element_to_be_clickable(...))
    → page.wait_for_selector(sel, state="visible", timeout=2000).
  - execute_script(click, btn) → btn.click(), conditionné CTA_INTERCEPT_ONLY.
  - _close_other_tabs_in_current_session : window_handles/switch_to/close
    → page.context.pages + p.close() (pas de switch_to nécessaire).
  Cash/payout.py :
  - Bloc if not IS_LOCAL: (WebDriverWait/EC/ActionChains/exceptions) entièrement supprimé.
  - _wait() supprimé. _find(driver, by, sel) → page.wait_for_selector(pw_sel, state="attached")
    (XPath auto-préfixé via sel.startswith("//")).
  - _js_click(driver, el) → el.scroll_into_view_if_needed() + el.click(),
    conditionné CTA_INTERCEPT_ONLY ; driver ignoré.
  - _wait_select_btn_enabled → polling loop time.sleep(0.2).
  - _dispatch_mouse_sequence → el.evaluate("(el) => { ... }").
  - find_elements(By.XPATH/CSS_SELECTOR) → query_selector_all("xpath=..." / css).
  - find_element(By.*) → query_selector(...) + None guard.
  - span.find_element(By.XPATH, "ancestor::...") → span.query_selector("xpath=ancestor::...").
  - ActionChains.move_to_element(wrapper).pause(3).click() → wrapper.hover(); wrapper.click(),
    conditionné CTA_INTERCEPT_ONLY.
  - el.text → el.inner_text(). name_inp.clear()+send_keys(v) → name_inp.fill(v).
  - tab_el.parent (hack driver Selenium) → None (driver non requis via ElementHandle).
  - _click_modal_choose : double WebDriverWait → wait_for_selector(state="visible").
  - Résultat : 0 By., 0 selenium, 0 execute_script dans les 7 fichiers. py_compile OK.

BLOC S7a — platforms/topsurveys.py + platforms/ysense.py + Survey/input_frame.py
  Statut : ✅ migré (2026-06-23)
  Périmètre : résidus Selenium explicites (By/WebDriverWait/EC/switch_to.frame) dans 3 fichiers.
  Migrations appliquées :
  - _pw_page(d) helper ajouté dans les 3 fichiers.
  topsurveys.py :
  - driver.current_url → _pw_page(driver).url dans is_on_platform().
  ysense.py (fonctions actives uniquement — NotImplementedError non touchées) :
  - Imports By / WebDriverWait / EC / TimeoutException supprimés.
  - driver.get(url) → page.goto(url).
  - WebDriverWait(20).until(EC.presence_of_element_located(...))
    → page.wait_for_selector(sel, state="attached", timeout=20000).
  - email_input.clear() + send_keys(email) → email_input.fill(email).
  - execute_script(value + events, pwd_input, password) → pwd_input.evaluate("(e,v) => {...}", password).
  - get_attribute("value") → input_value().
  - fallback pwd send_keys → fill().
  - find_element(By.CSS_SELECTOR) × 3 → page.query_selector(...) + None guard.
  - rc.is_displayed() → rc.is_visible().
  - execute_script("arguments[0].click()", btn) → btn.click().
  - WebDriverWait(15).until(lambda d: "/login" not in d.current_url)
    → page.wait_for_function("() => !window.location.href.includes('/login')", timeout=15000).
  - errors_div.text → errors_div.inner_text().
  - is_session_expired : driver.current_url → page.url ; driver.page_source → page.content().
  input_frame.py — iter_iframes_safe :
  - find_elements("tag name", "iframe") → _pw_page(driver).query_selector_all("iframe").
  - fr.rect → fr.bounding_box() or {}.
  - fr.is_displayed() → fr.is_visible().
  input_frame.py — in_each_frame_recursive (réécriture complète) :
  - Boucle switch_to.frame / switch_to.default_content / récursion supprimée.
  - Remplacée par iter_frame_chains(driver, max_depth=depth) + switch_to_frame_chain
    (même pattern que click_cta_strong_any_context déjà présent dans le fichier).
  - Signature et comportement observable inchangés.
  - Résultat : 0 By., 0 switch_to.frame, 0 WebDriverWait dans les 3 fichiers. py_compile OK.

BLOC S7b — Résidus string-literal shim dans fichiers "migrés"
  Statut : ✅ migré (2026-06-24)
  Périmètre : find_elements/execute_script via string literals (sans By.*) dans 7 fichiers.
  Migrations appliquées (commun aux 7 fichiers) :
  - find_elements("css selector"/"tag name", sel) → .query_selector_all(sel)
  - find_elements("xpath", xp) → .query_selector_all("xpath=" + xp)
  - find_element("css selector"/"tag name", sel) → .query_selector(sel)
  - find_element("xpath", xp) → .query_selector("xpath=" + xp)
  - find_element("id", val) → .query_selector("#val") ou .query_selector(f"#{expr}")
  - el.tag_name → el.evaluate("e => e.tagName.toLowerCase()")
  - .is_displayed() → .is_visible()
  - el.rect / el.rect or {} → el.bounding_box() or {}
  - el.text → el.inner_text()
  - .send_keys(v) → .type(v)   [input_utils]
  - driver.current_url → _pw_page(driver).url
  - execute_script("""js""", el) → _handle(el).evaluate("""(_el) => { js_arguments[0]→_el }""")
  - execute_script("""js""", el0, el1) → page.evaluate("""([_el,_arg1]) => {...}""", [_handle(el0), el1])
  - execute_script("""js""") → page.evaluate("""() => { js }""")
  - execute_script("inline", el) → _handle(el).evaluate("(_el) => { ... }")
  - execute_script("inline") → page.evaluate("() => { ... }")
  Cas spéciaux (éditions ciblées) :
  - dom_analyzer.py : XPath multi-ligne entre parenthèses
  - input_radio.py : JS string-concaténation multi-ligne
  - action_dispatcher.py (7 cas) : JS pré-migré avec _el sans arg Python, querySelector avec string,
    JS no-arg single-line ; L2078 : _handle(inp).evaluate() — élément déduit du contexte
  - Résultat : 0 find_elements, 0 execute_script, 0 .send_keys dans les 7 fichiers.
    py_compile OK sur tous les fichiers.

BLOC S8 — Suppression du shim et nettoyage résidus Selenium string-literal
  Statut : ✅ migré (2026-06-24)
  Périmètre : suppression complète du shim + résidus API Selenium dans 4 fichiers input_*.
  Partie A — Suppression du shim et points d'injection :
  - preselection/playwright_shim.py supprimé entièrement.
  - main.py : _page_to_shim() supprimé. _attach_tab_score, _attach_select_best_tab,
    _attach_select_tab (dépendants de l'API shim Selenium) supprimés. Branche attach
    else utilise page directement. run_attach_takeover : Selenium API → Playwright natif
    (execute_script→evaluate, find_elements→query_selector_all, is_displayed→is_visible,
    current_url→url, WebDriverWait→wait_for_url). run_attach_login_takeover : shim
    supprimé, page transmis directement.
  - Survey/survey_solver.py : _make_shim() supprimé. Toutes les utilisations de _shim
    dans solve_full_survey remplacées par page (variable locale native Playwright).
    _survey_account_id positionné sur page directement.
  - Survey/survey_executor.py : _make_shim() supprimé (définie mais jamais appelée).
  - preselection/playwright_launcher.py : bloc Selenium legacy (webdriver.Chrome via
    debuggerAddress, execute_script cdc_*, fingerprint dump) supprimé. Imports
    selenium.webdriver.chrome.options/service + selenium.webdriver supprimés.
  - preselection/survey_handler.py : driver.window_handles → driver.context.pages.
  - preselection/response_executor.py : label._h guard → label directement.
  - Survey/dom_frame_selector.py : import mort By supprimé.
  - Survey/page_snapshot.py : docstring _dump_frames_best_effort mis à jour (API Playwright).
  - 47 fichiers : _pw_page(d)→d, _handle(el)→el (741 call sites). Définitions supprimées.
  Partie B — Résidus Selenium string-literal (4 fichiers non couverts par S7b) :
  - Survey/input_dropdown.py : find_elements("tag name"/"css selector"/"xpath") →
    query_selector_all ; find_element("xpath"/"id") → query_selector + None guard ;
    el.find_element → el.query_selector ; is_displayed()→is_visible() ; el.rect→bounding_box() ;
    execute_script dispatch events → el.evaluate ; execute_script scrollHeight → page.evaluate.
  - Survey/input_frame.py click_cta_strong_any_context : find_elements("css selector") →
    query_selector_all ; is_displayed()→is_visible() ; el.text→el.inner_text().
  - Survey/input_slider.py : find_elements("css selector"/"tag name") → query_selector_all ;
    find_element("css selector"/"tag name") → query_selector + None guard ; track.rect→
    bounding_box() ; execute_script dispatch events → el.evaluate(arrow_fn) ; jQuery slider
    execute_script → b.evaluate ; mouse click execute_script → track.evaluate.
  - Survey/input_text.py swagbucks_zip_patch : find_element("id") → query_selector + None guard.
  - Survey/input_slider.py : find_elements("css selector"/"tag name") → query_selector_all ;
    find_element("css selector"/"tag name") → query_selector + None guard ; track.rect→
    bounding_box() ; execute_script dispatch events → el.evaluate(arrow_fn) ; jQuery slider
    execute_script → b.evaluate ; mouse click execute_script → track.evaluate.
  - Survey/input_text.py swagbucks_zip_patch : find_element("id") → query_selector + None guard.
  Résultat : python -c "import preselection.playwright_shim" → ModuleNotFoundError ✓
  Aucun import selenium.webdriver hors tools/ ✓ py_compile OK sur 51 fichiers ✓
  Correctif post-S8 (2026-06-24) — playwright_launcher.py résidus shim :
  - launch_browser_playwright() : suppression du bloc shim = PlaywrightDriverShim(context,
    context, page) + assignations → page._pw = pw, page._chrome_user_data_dir = user_data_dir,
    return page. Import PlaywrightDriverShim local supprimé. Docstring mis à jour.
  - launch_browser_playwright_debug() : même correction. Annotation -> PlaywrightDriverShim
    retirée de la signature. Import local supprimé.
  - attach_browser_playwright() et launch_browser() non touchés.
  - import preselection.playwright_launcher → OK ✓

================================================================================
RÈGLES VALABLES POUR TOUS LES BLOCS
================================================================================

- Un bloc = un patch = une validation avant de passer au suivant.
- Le patch ne touche QUE les fichiers du bloc en cours.
- Toute frontière doit être documentée dans FRONTIÈRES ACTIVES.
- ⚠️  RÈGLE DE LECTURE : "fichier X listé dans BLOC N" ≠ "fichier X entièrement migré".

================================================================================
FRONTIÈRES ACTIVES
================================================================================

AUCUNE FRONTIÈRE ACTIVE — migration S8 complète (2026-06-24).
  - playwright_shim.py supprimé. Plus aucun shim dans le code applicatif.
  - Tous les chemins (attach + prod) opèrent sur des objets Page Playwright natifs.
  - _pw_page() et _handle() supprimés de tous les fichiers (47 fichiers, 741 call sites).

INTERFACE switch_to_frame_chain
  - Entrée : driver = Page native (shim supprimé).
  - Comportement inchangé côté frame_utils (opère sur Page/Frame Playwright natifs).

================================================================================
HISTORIQUE
================================================================================

2026-07-19  Correctif post-S8 (dropdown natif Ipsos/Wicket, bs-select-hidden) :
            select_native_option_by_target() (Survey/input_dropdown.py) utilisait
            `el.select_option(label=...)`, qui applique les vérifications d'actionability
            Playwright dont la visibilité — or les `<select>` de ce type de widget portent
            la classe `bs-select-hidden` et ne sont jamais visibles au sens Playwright (le
            widget de substitution l'est, lui). L'appel échouait proprement (pas d'exception
            qui remonte), la fonction retournait `False`, et le dispatch retombait sur le
            chemin générique `select_option_with_hint()` → `open_dropdown_generic()`, qui lui
            plante toujours sur `el.tag_name` (résidu Selenium non converti, cf plus bas).
            Corrigé : assignation JS directe (`sel.value = val` + dispatch `input`/`change` +
            `jQuery(sel).selectpicker('refresh')` si présent) au lieu de `select_option()`.
            Fonctionne indépendamment de la visibilité du `<select>`. En parallèle, un second
            bug corrigé dans Survey/action_dispatcher.py (execute_actions_plan) : deux
            `<select>` natifs consécutifs de même question (ex. mois + année) déclenchaient un
            rescan DOM entre les deux actions (aucune condition `same_question_block`
            existante ne couvrait ce cas), et ce rescan régénérait un target_id pour le second
            champ à partir d'un texte de question déjà modifié par la sélection du premier
            (pollution "... Juillet Année"), target_id que le registry ne reliait plus à rien
            — la résolution native n'était alors même plus tentée pour le second champ. Fix :
            skip du rescan quand deux actions dropdown consécutives partagent le même
            contexte-question GPT (texte statique, non re-extrait du DOM — signal stable
            contrairement au texte "question" du registry). Cf BOT_EVOLUTION_MEMORY.md,
            section "PLATEFORME : IPSOS / WICKET — DROPDOWN NATIF BOOTSTRAP-SELECT".
            —
            Découverte annexe au cours du même diagnostic (piste initialement suivie puis
            écartée, mais confirmée réelle) : Survey/input_slider.py (`set_sliderpoints()`)
            contient encore `root.find_elements("css selector", ".sq-sliderpoints-container")`
            — signature Selenium (`find_elements(by, value)`), jamais convertie en
            `query_selector_all()` malgré le classement "✅ migré BLOC 3b5c" de ce fichier dans
            l'inventaire ci-dessus. Avant BLOC S8, cet appel passait par
            PlaywrightDriverShim et fonctionnait ; depuis la suppression du shim (2026-06-24),
            un objet Playwright natif n'a pas de méthode `find_elements` → `AttributeError`
            immédiate, avalée par le `try/except` local, `blocks_all = []`, `return False`.
            Effet : `set_sliderpoints()` échoue désormais silencieusement à chaque appel,
            quel que soit le DOM (pas seulement sur les pages de date de naissance qui ont
            servi de fil rouge à ce diagnostic — sur toute page Decipher/Behaviorally à
            sliderpoints réels, le scoping par row-legend ne peut plus fonctionner du tout).
            NON CORRIGÉ à ce jour — hors périmètre du bug traité dans cette session, à traiter
            dans un patch dédié. Reclassé de "PROPRES" vers annotation ⚠️ dans l'inventaire
            ci-dessus en attendant. À vérifier si d'autres fichiers listés "✅ propre"
            contiennent le même type de résidu non détecté par la revue BLOC S7b/S8 (celle-ci
            portait sur `is_displayed→is_visible`, `tag_name→evaluate`, `text→inner_text`,
            `send_keys→type`, `rect→bounding_box`, mais pas systématiquement sur
            `find_elements`/`find_element` en signature string-literal comme celle-ci).
2026-07-16  Correctif post-S8 : résidu Selenium `el.is_selected()` trouvé dans
            `_is_selected()` (Survey/action_dispatcher.py) et `is_checked()`
            (Survey/input_utils.py). Cette méthode n'existe pas sur `ElementHandle`/
            `Locator` Playwright (contrairement à `WebElement` Selenium) : l'appel
            levait systématiquement une `AttributeError`, avalée silencieusement par
            un `try/except`, retournant `False` par défaut quel que soit l'état réel
            de l'input. Absent de la liste de correspondances établie au BLOC S7b
            (is_displayed()→is_visible(), tag_name→evaluate, text→inner_text(),
            send_keys→type(), rect→bounding_box()) — cette méthode n'y figurait pas et
            n'avait donc pas été convertie lors du passage en revue de action_dispatcher.py/
            input_utils.py. Impact concret : toute vérification post-clic de l'état
            coché d'un checkbox/radio rapportait un faux `False` permanent, y compris
            pour un input réellement coché (confirmé par instrumentation ciblée : état
            DOM live `checked=true` sur nœud non-stale). Repéré via un bug GfK/mrIWeb
            (widget "mrMultiple", checkbox masqué en CSS) où la case était cochée au
            premier clic puis décochée par les stratégies de repli déclenchées à tort
            par ce faux négatif — cf. BOT_EVOLUTION_MEMORY.md, section "LEÇON
            TRANSVERSALE : is_selected() inexistant sur Playwright". Corrigé :
            `el.is_selected()` → `el.is_checked()` (méthode Playwright native, lit
            l'état "checked" réel sans wait d'actionability/visibilité — valide aussi
            pour les inputs masqués en CSS). Portée : ces deux fonctions uniquement ;
            aucune autre stratégie de clic ni logique de fallback modifiée. Les autres
            fichiers migrés aux BLOCs 3b5b/3b5c/3b6/S7b n'ont pas été ré-audités pour
            un usage résiduel de `is_selected()` — à vérifier si un symptôme similaire
            réapparaît ailleurs.
2026-06-24  Correctif post-S8 : playwright_launcher.py — launch_browser_playwright() et
            launch_browser_playwright_debug() instanciaient encore PlaywrightDriverShim
            (import local dans le corps) après suppression du module. Corrigé : shim
            remplacé par return page direct ; _pw et _chrome_user_data_dir attachés sur
            page. Annotation -> PlaywrightDriverShim retirée. import OK ✓
2026-06-24  BLOC S8 migré : suppression complète du shim Playwright et nettoyage final.
            playwright_shim.py supprimé. _page_to_shim/_make_shim supprimés (main.py,
            survey_solver.py, survey_executor.py). Bloc Selenium legacy launcher supprimé.
            _attach_tab_score/_attach_select_best_tab/_attach_select_tab supprimés.
            run_attach_takeover converti aux API Playwright natives (evaluate/query_selector_all/
            wait_for_url). solve_full_survey : _shim → page natif partout. survey_handler :
            window_handles→context.pages. response_executor : label._h guard→label.
            dom_frame_selector : import mort By supprimé. page_snapshot : docstring mis à jour.
            47 fichiers : _pw_page()/​_handle() supprimés (741 call sites). Partie B :
            input_dropdown/input_frame/input_slider/input_text — derniers find_elements/
            execute_script/rect convertis. 51 fichiers, 0 erreur py_compile. Shim éliminé. ✅
2026-06-21  Décision migration franche (Option B). BLOC 1 validé en attach.
2026-06-22  BLOCs 3b6→3b1 migrés (attach). BLOC 3a migré. BLOC 2 migré (attach).
            Scope étendu prod. BLOC P1 migré. BLOC P2 migré. BLOC P3 migré.
            Chemin prod login → fin présélection entièrement Playwright natif. ✅
2026-06-22  Lecture directe de TOUS les fichiers du projet — inventaire complet établi.
            22 fichiers non migrés identifiés, tous couverts par le shim.
2026-06-23  Phase S démarrée — objectif : 100% Playwright natif, suppression du shim.
            BLOC S1 migré : survey_difficulty_guard.py — By supprimé, _pw_page ajouté,
            toutes les API Selenium remplacées par Playwright natif.
            Blocs S2→S8 définis et documentés.
2026-06-24  BLOC S7b migré : dom_analyzer.py + cta_handler.py + input_utils.py +
            input_checkbox.py + input_radio.py + input_matrix.py + action_dispatcher.py.
            ~500 appels shim string-literal convertis. find_elements/find_element("css selector"/
            "tag name"/"xpath"/"id") → query_selector_all/query_selector. execute_script(»js«, el)
            → _handle(el).evaluate(arrow_fn) ; execute_script(»js«) → page.evaluate(arrow_fn).
            tag_name → evaluate(tagName). is_displayed() → is_visible(). rect → bounding_box().
            text → inner_text(). send_keys → type(). current_url → page.url.
            7 cas spéciaux résolus par éditions ciblées (JS pré-migré _el, XPath multi-ligne…).
            py_compile OK sur les 7 fichiers. ✅ 100% API Playwright natif dans le code applicatif.
2026-06-23  BLOC S7a migré : topsurveys.py + ysense.py + input_frame.py.
            topsurveys : current_url → page.url. ysense : By/WebDriverWait/EC supprimés ;
            get/wait_for_selector/fill/evaluate/query_selector/wait_for_function/content().
            input_frame iter_iframes_safe : find_elements/rect/is_displayed → Playwright natif.
            input_frame in_each_frame_recursive : switch_to.frame/default_content supprimés →
            iter_frame_chains + switch_to_frame_chain. py_compile OK sur les 3 fichiers.
2026-06-23  BLOC S3a migré : dom_extractors_areyounet.py + dom_extractors_decipher.py.
            By supprimé dans les 2 fichiers. _pw_page ajouté.
            find_elements/find_element(By.*) → query_selector_all/query_selector + None guards.
            XPath ancestor/following-sibling : el.query_selector("xpath=...") + None guard.
            .text → .inner_text(). execute_script → evaluate (QARTS autosubmit).
            field.tag_name → evaluate(tagName). Helpers internes migrés.
            BLOC S3 découpé en S3a (fait) + S3b (dom_extractors_misc.py, à migrer).
2026-06-23  BLOC S3b migré : dom_extractors_misc.py (~12 600 lignes, ~76 fonctions, ~500+ By.*).
            By supprimé, _pw_page ajouté. query_selector_all/query_selector pour tous les
            find_elements/find_element (CSS, XPATH via "xpath="+expr, TAG_NAME, ID).
            execute_script → evaluate (multiline JS → arrow function triple-quote).
            _JS_DIRECT_TEXT réécrit en arrow function. .text → .inner_text() (tous vars).
            Inline Keys import supprimé → press("Escape"). Syntaxe Python validée (py_compile).
2026-06-23  BLOC S6 migré : captcha/* (5 fichiers) + Cash/payout.py + Survey/functions.py (7 fichiers).
            By/WebDriverWait/EC/ActionChains/exceptions supprimés dans tous. _pw_page ajouté.
            execute_script → evaluate (arrow function) ; IIFE → (tok) => {...} / ([t,r]) => {...}.
            add_cookie → context.add_cookies. refresh() → reload(). current_url → page.url.
            ActionChains drag → page.mouse.move/down/move/up. evaluate_handle + as_element() (normal_captcha).
            screenshot_as_base64 → base64.b64encode(el.screenshot()).decode().
            payout: _wait/_find → wait_for_selector ; _js_click → scroll_into_view_if_needed+click ;
            tous CTAs conditionnés CTA_INTERCEPT_ONLY. tab_el.parent supprimé.
            functions: window_handles/switch_to/close → page.context.pages + p.close().
            py_compile OK sur les 7 fichiers.
2026-06-23  BLOC S5 migré : screenshot_analyzer.py + runtime_guard.py + snap_uploader.py.
            _pw_page ajouté dans les 3 fichiers. By/WebDriverWait/EC supprimés (runtime_guard).
            runtime_guard : WebDriverWait+EC → wait_for_selector × 2 XPath séquentiels (3 s chacun) ;
            execute_script(scrollIntoView/click) → scroll_into_view_if_needed()/click() ;
            CTA conditionné à CTA_INTERCEPT_ONLY.
            screenshot_analyzer : bloc CDP entier (4 execute_cdp_cmd) → page.screenshot(full_page=True) ;
            save_screenshot → page.screenshot(path=...) ; _stitch_fullpage migré (evaluate+screenshot).
            snap_uploader : get_screenshot_as_png() → _pw_page(driver).screenshot() ;
            save_screenshot(path) → _pw_page(driver).screenshot(path=path). py_compile OK sur les 3 fichiers.
2026-06-23  BLOC S4 migré : dropdown_block_resolver.py + question_block_analyzer.py
            + question_block_resolver.py + sliderpoints_extractor.py.
            By/WebElement/Keys/ActionChains/Select supprimés dans les 4 fichiers. _pw_page ajouté.
            is_displayed() → is_visible(). el.rect → bounding_box() or {}. tag_name → evaluate(tagName).
            find_elements/find_element(By.*) → query_selector_all/query_selector + None guards.
            ActionChains → hover()+click(). send_keys(Keys.*) → press("..."). execute_script → evaluate.
            Select(trigger).first_selected_option.text → evaluate("e => e.options[e.selectedIndex]?.text").
            Import inline Select dans dropdown_block_resolver supprimé. py_compile OK sur les 4 fichiers.
2026-06-23  BLOC S2 migré : dom_context_mapper.py + dom_question_extractor.py + dom_utils.py.
            By/ActionChains supprimés. _pw_page ajouté dans les 3 fichiers.
            find_elements(By.*) → query_selector_all("xpath=..." ou css).
            find_element(By.*) → query_selector() + None guard.
            execute_script(js, el[, arg]) → page.evaluate(fn, el) / page.evaluate(fn, [el, arg]).
            el.is_displayed() → el.is_visible(). el.rect → bounding_box() or {}.
            el.tag_name → el.evaluate(tagName). .text → .inner_text().
            dom_context_mapper : boucle 3 modes js/ac/native → el.click() natif unique.
            Logique métier de toutes les fonctions conservée à l'identique.

.