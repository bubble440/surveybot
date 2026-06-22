# PLAYWRIGHT_NATIVE_MIGRATION.md
# Suivi de la migration franche (Option B) : suppression du shim Selenium,
# bascule vers l'API Playwright native (Page, Locator, frame_locator...).
# Démarré : 2026-06-21
# Scope initial : mode attach uniquement.
# Scope étendu au 2026-06-22 : migration globale (attach + prod / Fly.io).

================================================================================
PRINCIPE DU DÉCOUPAGE
================================================================================

Le chantier est découpé en BLOCS FONCTIONNELS (pas fichier par fichier isolé),
chacun suivant une chaîne d'appel réelle, testable seul avant de passer au suivant.
Coexistence assumée pendant la transition : certains blocs tournent en Playwright
natif, d'autres encore sur le shim (PlaywrightDriverShim / API façon Selenium).

⚠️ PÉRIMÈTRE PARTIEL DES BLOCS ATTACH (1, 2, 3x) — RÈGLE DE LECTURE CRITIQUE :
Les blocs 1–3b ont été migrés dans le cadre du chantier attach. Chaque bloc
couvrait UNIQUEMENT les fonctions utilisées dans le chemin d'appel attach.
"BLOC N migré" signifie "les fonctions du chemin listées dans ce bloc sont
migrées". Cela ne signifie PAS que l'ensemble du fichier est migré.

NOTE SUR LES RÉSIDUS SHIM ATTENDUS :
Plusieurs fichiers migrés (dom_analyzer, cta_handler, input_checkbox, input_radio,
input_matrix, input_utils, input_frame) utilisent encore find_element / find_elements /
execute_script via des string literals ("css selector", "xpath", "tag name", etc.)
plutôt que By.*. Ce n'est PAS du Selenium direct : ces appels passent par le shim
(PlaywrightDriverShim.find_elements → page.query_selector_all). C'est le comportement
attendu pendant la coexistence shim. Ils seront supprimés lors de la suppression du shim.

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
  hot_reload/hot_reload.py             ✅ (zéro driver)
  State/daily_target.py                ✅ (zéro driver)
  State/survey_memory.py               ✅ (zéro driver)
  State/account_state.py               ✅ (zéro driver)
  platforms/base.py                    ✅ (zéro driver)
  platforms/__init__.py                ✅ (zéro driver)
  Survey/survey_solver.py              ✅ migré BLOC 3a — Playwright natif pur
  Survey/survey_executor.py            ✅ migré BLOC 3b1 — Playwright natif pur
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
  Survey/frame_utils.py                ✅ migré BLOC 3b5a — Playwright natif pur
  Survey/input_handler.py              ✅ migré BLOC 3b5d — propre
  Survey/input_dropdown.py             ✅ migré BLOC 3b5c — propre
  Survey/input_slider.py               ✅ migré BLOC 3b5c
  Survey/input_text.py                 ✅ migré BLOC 3b5c

MIGRÉS AVEC RÉSIDUS SHIM ATTENDUS (string literals, pas By.* — comportement normal) :
  Survey/dom_analyzer.py               ✅ migré BLOC 3b4 — find_elements via shim
  Survey/cta_handler.py                ✅ migré BLOC 3b5b — find_elements via shim
  Survey/input_utils.py                ✅ migré BLOC 3b5b — find_element via shim (×3)
  Survey/input_frame.py                ✅ migré BLOC 3b5b — switch_to.frame résiduel
                                          dans in_each_frame_recursive uniquement
  Survey/input_checkbox.py             ✅ migré BLOC 3b5c — find_elements/execute_script via shim
  Survey/input_radio.py                ✅ migré BLOC 3b5c — find_element via shim
  Survey/input_matrix.py               ✅ migré + fix BLOC 3b5c — find_element via shim
  Survey/action_dispatcher.py          ✅ migré BLOC 3b6 — 92 execute_script via shim
                                          EN ATTENTE validation prod

SPÉCIAUX :
  preselection/playwright_shim.py      ✅ C'EST le shim — Selenium par design. Supprimé en fin.
  preselection/playwright_launcher.py  ✅ propre prod (launch_browser_playwright) ;
                                          résidus dans fonctions legacy non appelées en prod.

NON MIGRÉS (By.* Selenium direct — couverts par le shim en prod) :
  Survey/functions.py                  🔲 By, WebDriverWait, EC, find_elements, execute_script,
                                          window_handles, switch_to, current_url
  Survey/dom_context_mapper.py         🔲 By, ActionChains, execute_script (×3), find_element,
                                          current_url
  Survey/dom_question_extractor.py     🔲 By, find_element (×5), find_elements (×5), execute_script (×2)
  Survey/dom_utils.py                  🔲 By, find_elements(By.XPATH) (×8+), execute_script
  Survey/dom_extractors_areyounet.py   🔲 By, find_elements/find_element (×20+)
  Survey/dom_extractors_decipher.py    🔲 By, find_elements
  Survey/dom_extractors_misc.py        🔲 By, find_elements/find_element (×20+), execute_script
  Survey/dropdown_block_resolver.py    🔲 By, WebElement, Select, find_elements/find_element,
                                          execute_script (×2), is_displayed, el.rect, get_attribute
  Survey/question_block_analyzer.py    🔲 By, WebElement, find_elements/find_element (×15+)
  Survey/question_block_resolver.py    🔲 By, Keys, find_elements/find_element, execute_script (15 occur.)
  Survey/sliderpoints_extractor.py     🔲 By, find_elements/find_element, get_attribute, el.text
  Survey/screenshot_analyzer.py        🔲 save_screenshot (×3), execute_cdp_cmd (×4),
                                          execute_script (×5)
  Management/survey_difficulty_guard.py 🔲 By, find_elements (×10+), execute_script,
                                          is_displayed, el.rect, el.tag_name, get_attribute
  Management/runtime_guard.py          🔲 By, WebDriverWait, EC, execute_script (lignes 120–135)
  Management/snap_uploader.py          ⚠️ fallbacks mineurs get_screenshot_as_png / save_screenshot
  captcha/datadome_handler.py          🔲 execute_script (×2), current_url, add_cookie, refresh
  captcha/normal_captcha.py            🔲 By, find_elements, execute_script, is_displayed,
                                          get_attribute, screenshot_as_base64
  captcha/recaptcha_handler.py         🔲 execute_script (×3), current_url
  captcha/recaptcha_utils.py           🔲 By, find_elements (×3), execute_script (×2)
  captcha/tencent_handler.py           🔲 execute_script (×6), find_element, ActionChains, current_url
  Cash/payout.py                       🔲 By, WebDriverWait, EC, ActionChains — entièrement Selenium
  platforms/topsurveys.py              🔲 résidu mineur : driver.current_url dans is_on_platform()
  platforms/ysense.py                  🔲 entièrement Selenium (select_survey = NotImplementedError)

================================================================================
DÉCOUPAGE EN BLOCS
================================================================================

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
BLOC 3b6 — action_dispatcher                  ✅ migré (attach 2026-06-22) EN ATTENTE prod

BLOC P1 — launch.py                           ✅ migré (2026-06-22)
BLOC P2 — survey_handler._run_survey_impl
          + response_executor._click_radio_label ✅ migré (2026-06-22)
BLOC P3 — redirect_watcher + question_analyzer ✅ migré (2026-06-22)

================================================================================
ROADMAP — POUR CLORE TOTALEMENT LE CHANTIER
================================================================================

  Priorité 0 — VALIDATION PROD (avant tout) :
    Test complet login → présélection → clic Participer → résolution survey.
    Tous les fichiers non migrés sont couverts par le shim : pas de crash attendu.

  Priorité 1 — Fichiers non migrés à fort impact (appelés à chaque itération) :
    a) Management/survey_difficulty_guard.py
    b) Survey/dom_context_mapper.py
    c) Survey/dom_question_extractor.py + dom_utils.py
    d) Survey/dom_extractors_areyounet.py + dom_extractors_decipher.py + dom_extractors_misc.py
    e) Survey/question_block_analyzer.py + question_block_resolver.py
    f) Survey/dropdown_block_resolver.py + sliderpoints_extractor.py

  Priorité 2 — Fichiers non migrés à impact moyen :
    g) Management/runtime_guard.py
    h) captcha/* (datadome_handler, normal_captcha, recaptcha_handler,
       recaptcha_utils, tencent_handler)
    i) Cash/payout.py
    j) Survey/functions.py._handle_topsurveys_exclusion_popup
    k) Survey/screenshot_analyzer.py (activé uniquement si VISION=1)

  Priorité 3 — Résidus mineurs :
    l) platforms/topsurveys.py (driver.current_url dans is_on_platform)
    m) Management/snap_uploader.py (fallbacks _capture_png)

  Priorité 4 — Fin de chantier (après tout ce qui précède) :
    n) Suppression playwright_shim.py + adapter _page_to_shim dans main.py
    o) Suppression _make_shim() dans survey_solver.py
    p) in_each_frame_recursive dans input_frame.py (switch_to.frame résiduel)
    q) Nettoyage fonctions legacy playwright_launcher.py (lignes 634–699)
    r) platforms/ysense.py (NotImplementedError, faible priorité)
    s) Nettoyage résidus attach dans main.py

================================================================================
RÈGLES VALABLES POUR TOUS LES BLOCS
================================================================================

- Un bloc = un patch = une validation manuelle avant de passer au suivant.
- Le patch ne touche QUE les fichiers du bloc en cours.
- Toute frontière doit être documentée dans FRONTIÈRES ACTIVES.
- Pas de fallback Vision, pas de branches parallèles dans une fonction migrée.
- ⚠️  RÈGLE DE LECTURE : "fichier X listé dans BLOC N" ≠ "fichier X entièrement migré".

================================================================================
FRONTIÈRES ACTIVES
================================================================================

FRONTIÈRE BLOC 1 → BLOC 2 (chemin attach)
  - main.py crée un shim via _page_to_shim(page, pw) juste avant run_attach_preselection_takeover.
  - En chemin prod : aucun shim créé, driver = Page Playwright native de bout en bout.

FRONTIÈRE BLOC 2 → BLOC 3a
  - _run_survey_impl appelle solve_full_survey(_pw_page(driver), ...).
  - solve_full_survey crée _shim = _make_shim(page) pour les appels hors-périmètre.

FRONTIÈRE BLOC 3a → BLOC 3b1
  - execute_survey_page reçoit _shim ; page = _pw_page(driver) pour les ops DOM.
  - driver (= shim) transmis aux sous-modules.

FRONTIÈRE BLOC 3b → fichiers non migrés
  - survey_difficulty_guard, dom_context_mapper, dom_extractors_*, dom_question_extractor,
    dom_utils, dropdown_block_resolver, question_block_*, sliderpoints_extractor,
    captcha/*, payout, functions, screenshot_analyzer reçoivent driver (= shim).
  - API Selenium absorbée par le shim — pas de crash en prod tant que le shim existe.

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
            Propres : 35+ fichiers. Migrés résidus shim attendus : 8 fichiers.
            Non migrés (By.* Selenium direct) : 22 fichiers, tous couverts par le shim.
            Roadmap Priorités 0→4 établie. Prochaine étape : validation prod.

.