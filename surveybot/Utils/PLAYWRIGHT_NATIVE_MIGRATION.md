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
  Survey/dom_extractors_misc.py        🔲 By, find_elements/find_element (×20+), execute_script — BLOC S3
  Survey/dropdown_block_resolver.py    🔲 By, WebElement, Select, find_elements/find_element,
                                          execute_script (×2) — BLOC S4
  Survey/question_block_analyzer.py    🔲 By, WebElement, find_elements/find_element (×15+) — BLOC S4
  Survey/question_block_resolver.py    🔲 By, Keys, find_elements/find_element,
                                          execute_script (15 occur.) — BLOC S4
  Survey/sliderpoints_extractor.py     🔲 By, find_elements/find_element, get_attribute — BLOC S4
  Survey/screenshot_analyzer.py        🔲 save_screenshot (×3), execute_cdp_cmd (×4),
                                          execute_script (×5) — BLOC S5
  Management/runtime_guard.py          🔲 By, WebDriverWait, EC, execute_script — BLOC S5
  Management/snap_uploader.py          ⚠️ fallbacks mineurs get_screenshot_as_png/save_screenshot — BLOC S5
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
  Statut : 🔲 à migrer
  Périmètre : extracteurs DOM appelés depuis survey_executor / dom_analyzer.

BLOC S3 — Survey/dom_extractors_areyounet.py + dom_extractors_decipher.py + dom_extractors_misc.py
  Statut : 🔲 à migrer
  Périmètre : extracteurs DOM par provider, appelés depuis dom_analyzer.

BLOC S4 — Survey/dropdown_block_resolver.py + question_block_analyzer.py
          + question_block_resolver.py + sliderpoints_extractor.py
  Statut : 🔲 à migrer
  Périmètre : résolveurs de blocs questions, appelés depuis survey_executor.

BLOC S5 — Survey/screenshot_analyzer.py + Management/runtime_guard.py
          + Management/snap_uploader.py
  Statut : 🔲 à migrer
  Périmètre : monitoring, screenshot, guard.

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
  - Les fichiers des blocs S2→S6 reçoivent encore driver (= shim).
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

.