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
  Survey/input_slider.py               ✅ migré BLOC 3b5c
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
  captcha/datadome_handler.py          🔲 execute_script (×2), current_url, add_cookie,
                                          refresh — BLOC S6
  captcha/normal_captcha.py            🔲 By, find_elements, execute_script, is_displayed,
                                          get_attribute, screenshot_as_base64 — BLOC S6
  captcha/recaptcha_handler.py         🔲 execute_script (×3), current_url — BLOC S6
  captcha/recaptcha_utils.py           🔲 By, find_elements (×3), execute_script (×2) — BLOC S6
  captcha/tencent_handler.py           🔲 execute_script (×6), find_element, ActionChains,
                                          current_url — BLOC S6
  Cash/payout.py                       🔲 By, WebDriverWait, EC, ActionChains — BLOC S6
  Survey/functions.py                  🔲 By, WebDriverWait, EC, find_elements, execute_script,
                                          window_handles, switch_to, current_url — BLOC S6
  platforms/topsurveys.py              🔲 résidu mineur driver.current_url dans is_on_platform — BLOC S7
  platforms/ysense.py                  🔲 entièrement Selenium (select_survey = NotImplementedError) — BLOC S7

RÉSIDUS SHIM DANS FICHIERS "MIGRÉS" — À nettoyer dans BLOC S7 :
  Survey/dom_analyzer.py, cta_handler.py, input_utils.py, input_frame.py,
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
  Statut : 🔲 à migrer
  Périmètre : chemins captcha, encaissement, popup post-survey.

BLOC S7 — Résidus mineurs + nettoyage shim dans fichiers "migrés"
  Statut : 🔲 à migrer
  Périmètre :
  - platforms/topsurveys.py (driver.current_url → _pw_page(driver).url)
  - platforms/ysense.py (faible priorité — NotImplementedError)
  - Résidus string-literal shim dans dom_analyzer, cta_handler, input_utils,
    input_frame (in_each_frame_recursive), input_checkbox, input_radio,
    input_matrix, action_dispatcher (92 execute_script)

BLOC S8 — Suppression du shim
  Statut : 🔲 à faire (après S1→S7 tous validés)
  Actions :
  - Supprimer preselection/playwright_shim.py
  - Supprimer _page_to_shim() dans main.py (chemin attach)
  - Supprimer _make_shim() dans survey_solver.py
  - Nettoyage fonctions legacy playwright_launcher.py (lignes 634–699)
  - Nettoyage résidus attach dans main.py

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

FRONTIÈRE BLOC 1 → BLOC 2 (chemin attach)
  - main.py crée un shim via _page_to_shim(page, pw) avant run_attach_preselection_takeover.
  - En chemin prod : aucun shim créé, driver = Page Playwright native de bout en bout.

FRONTIÈRE BLOC 2 → BLOC 3a
  - _run_survey_impl appelle solve_full_survey(_pw_page(driver), ...).
  - solve_full_survey crée _shim = _make_shim(page) pour les appels hors-périmètre.

FRONTIÈRE BLOC 3a → BLOC 3b1
  - execute_survey_page reçoit _shim ; page = _pw_page(driver) pour les ops DOM.
  - driver (= shim) transmis aux sous-modules.

FRONTIÈRE BLOC 3b/S → fichiers encore non migrés
  - Les fichiers du bloc S6 reçoivent encore driver (= shim).
  - API Selenium absorbée par le shim — pas de crash tant que le shim existe.
  - Cette frontière se résorbe bloc par bloc jusqu'à S8.

INTERFACE switch_to_frame_chain
  - Entrée : driver = shim OU Page native.
  - Effet : met à jour driver._current_frame (si shim).
  - Usage : current_frame = getattr(driver, '_current_frame', _pw_page(driver))

================================================================================
HISTORIQUE
================================================================================

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