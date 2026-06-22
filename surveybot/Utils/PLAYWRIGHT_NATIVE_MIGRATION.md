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
chacun suivant une chaîne d'appel réelle, testable seul en mode attach avant
de passer au suivant. Coexistence assumée pendant la transition : certains
blocs tournent en Playwright natif, d'autres encore sur le shim
(PlaywrightDriverShim / API façon Selenium). Chaque frontière entre un bloc
migré et un bloc pas-encore-migré est documentée explicitement ci-dessous.

⚠️ PÉRIMÈTRE PARTIEL DES BLOCS ATTACH (1, 2, 3x) — RÈGLE DE LECTURE CRITIQUE :
Les blocs 1–3b ont été migrés dans le cadre du chantier attach. Chaque bloc
couvrait UNIQUEMENT les fonctions utilisées dans le chemin d'appel attach.
Plusieurs fichiers listés dans ces blocs contiennent des fonctions NON migrées.
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
ÉTAT DE LA MIGRATION PAR FICHIER (inventaire complet — lecture directe 2026-06-22)
================================================================================

PÉRIMÈTRE PROD login → présélection — VALIDÉ ✅
  launch.py                            ✅ propre
  main.py                              ✅ propre prod ; résidus attach intentionnels
  preselection/auth_handler.py         ✅ propre
  preselection/survey_navigator.py     ✅ propre
  preselection/survey_handler.py       ✅ propre prod ; résidu attach intentionnel
  preselection/question_analyzer.py    ✅ propre
  preselection/response_executor.py    ✅ propre
  preselection/question_validation.py  ✅ propre (zéro driver)
  preselection/config_loader.py        ✅ propre (zéro driver)
  preselection/secret_loader.py        ✅ propre (zéro driver)
  preselection/chrome_profile_store.py ✅ propre (zéro driver)
  Management/redirect_watcher.py       ✅ propre
  Management/notifier.py               ✅ propre (zéro driver)
  Management/pause_policy.py           ✅ propre (zéro driver)
  hot_reload/hot_reload.py             ✅ propre (zéro driver)
  State/daily_target.py                ✅ propre (zéro driver)
  State/survey_memory.py               ✅ propre (zéro driver)
  State/account_state.py               ✅ propre (zéro driver)
  platforms/base.py                    ✅ propre (zéro driver)
  platforms/__init__.py                ✅ propre (zéro driver)

FICHIERS SPÉCIAUX :
  preselection/playwright_shim.py      ✅ normal — C'EST le shim, Selenium par design.
  preselection/playwright_launcher.py  ✅ propre pour le chemin prod (launch_browser_playwright) ;
                                          résidus dans fonctions legacy non appelées en prod.

PÉRIMÈTRE RÉSOLUTION SURVEY — VALIDÉ attach, EN ATTENTE validation prod
  Survey/survey_solver.py              ✅ migré (attach 2026-06-22)
  Survey/survey_executor.py            ✅ migré (attach 2026-06-22)
  Survey/page_snapshot.py              ✅ migré (attach 2026-06-22)
  Survey/dom_classifier.py             ✅ propre — zéro résidu Selenium
  Survey/dom_frame_selector.py         ✅ propre — zéro résidu Selenium
  Survey/dom_selection_rules.py        ✅ propre (zéro driver)
  Survey/dom_registry.py               ✅ propre (zéro driver)
  Survey/action_types.py               ✅ propre (zéro driver)
  Survey/prompt_builder.py             ✅ propre (zéro driver)
  Survey/batch_response_parser.py      ✅ propre (zéro driver)
  Survey/log_utils.py                  ✅ propre (zéro driver)
  Survey/fivesim_client.py             ✅ propre (zéro driver)
  Survey/frame_utils.py                ✅ migré (attach 2026-06-22) — Playwright natif pur
  Survey/input_handler.py              ✅ migré (attach 2026-06-22) — propre
  Survey/input_dropdown.py             ✅ migré (attach 2026-06-22) — propre
  Survey/dom_analyzer.py               ✅ migré (attach 2026-06-22) — résidus shim attendus
                                          (find_elements string literals via shim)
  Survey/cta_handler.py                ✅ migré (attach 2026-06-22) — résidus shim attendus
  Survey/input_utils.py                ✅ migré (attach 2026-06-22) — résidus shim mineurs
  Survey/input_frame.py                ✅ migré (attach 2026-06-22) — résidu shim dans
                                          in_each_frame_recursive (switch_to.frame/default_content)
                                          ; click_cta_strong_any_context est Playwright natif
  Survey/input_checkbox.py             ✅ migré (attach 2026-06-22) — résidus shim attendus
  Survey/input_radio.py                ✅ migré (attach 2026-06-22) — résidus shim attendus
  Survey/input_matrix.py               ✅ migré + fix (attach 2026-06-22) — résidus shim attendus
  Survey/input_slider.py               ✅ migré (attach 2026-06-22)
  Survey/input_text.py                 ✅ migré (attach 2026-06-22)
  Survey/action_dispatcher.py          ✅ migré (attach 2026-06-22) — 92 execute_script via shim
                                          EN ATTENTE validation prod

FICHIERS NON MIGRÉS — couverts par le shim en prod :
  Survey/functions.py                  🔲 By, WebDriverWait, EC, find_elements, execute_script,
                                          window_handles, switch_to, current_url
  Survey/dom_context_mapper.py         🔲 By, ActionChains, execute_script (×3), find_element,
                                          current_url
  Survey/dom_extractors_areyounet.py   🔲 By, find_elements/find_element (×20+)
  Survey/dom_extractors_decipher.py    🔲 By, find_elements
  Survey/dom_extractors_misc.py        🔲 By, find_elements/find_element (×20+), execute_script
  Survey/dom_question_extractor.py     🔲 By, find_element (×5), find_elements (×5), execute_script (×2)
  Survey/dom_utils.py                  🔲 By, find_elements(By.XPATH) (×8+), execute_script
  Survey/dropdown_block_resolver.py    🔲 By, WebElement, Select, find_elements/find_element (×10+),
                                          execute_script (×2), is_displayed, el.rect, el.text,
                                          get_attribute, el.click, el.tag_name
  Management/survey_difficulty_guard.py 🔲 By, find_elements (×10+), execute_script,
                                          is_displayed, el.rect, el.tag_name, get_attribute
  Management/runtime_guard.py          🔲 By, WebDriverWait, EC, execute_script (lignes 120–135)
  captcha/datadome_handler.py          🔲 execute_script (×2), current_url, add_cookie, refresh
  captcha/normal_captcha.py            🔲 By, find_elements, execute_script, is_displayed,
                                          get_attribute, screenshot_as_base64
  captcha/recaptcha_handler.py         🔲 execute_script (×3), current_url
  captcha/recaptcha_utils.py           🔲 By, find_elements (×3), execute_script (×2)
  captcha/tencent_handler.py           🔲 execute_script (×6), find_element, ActionChains, current_url
  Cash/payout.py                       🔲 By, WebDriverWait, EC, ActionChains — entièrement Selenium
  platforms/topsurveys.py              🔲 résidu mineur : driver.current_url dans is_on_platform()
  platforms/ysense.py                  🔲 entièrement Selenium (select_survey = NotImplementedError)
  Management/snap_uploader.py          ⚠️ fallbacks mineurs get_screenshot_as_png / save_screenshot

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
BLOC P3 — redirect_watcher.switch_to_latest_window_and_close_others
          + question_analyzer (×2)            ✅ migré (2026-06-22)

================================================================================
POUR CLORE TOTALEMENT LE CHANTIER
================================================================================

  Priorité 1 — Validation prod :
    Test complet login → présélection → clic Participer → résolution survey.

  Priorité 2 — Fichiers non migrés, par ordre d'impact :
    a) Management/survey_difficulty_guard.py — appelé à chaque itération
    b) Survey/dom_context_mapper.py — appelé depuis survey_executor
    c) Survey/dom_question_extractor.py, dom_utils.py, dom_extractors_areyounet.py,
       dom_extractors_decipher.py, dom_extractors_misc.py — extracteurs DOM
    d) Survey/dropdown_block_resolver.py — résolveur dropdown
    e) Management/runtime_guard.py — monitoring continu
    f) captcha/* (datadome_handler, normal_captcha, recaptcha_handler,
       recaptcha_utils, tencent_handler)
    g) Cash/payout.py
    h) Survey/functions.py._handle_topsurveys_exclusion_popup
    i) platforms/topsurveys.py (résidu mineur is_on_platform)
    j) Management/snap_uploader.py (fallbacks mineurs)

  Priorité 3 — Fin de chantier :
    k) Suppression playwright_shim.py + adapter _page_to_shim dans main.py
    l) Suppression _make_shim() dans survey_solver.py
    m) in_each_frame_recursive dans input_frame.py (switch_to.frame résiduel)
    n) Nettoyage fonctions legacy playwright_launcher.py (lignes 634–699)
    o) platforms/ysense.py (NotImplementedError, faible priorité)
    p) Nettoyage résidus attach dans main.py

================================================================================
RÈGLES VALABLES POUR TOUS LES BLOCS
================================================================================

- Un bloc = un patch = une validation manuelle avant de passer au suivant.
- Le patch ne touche QUE les fichiers du bloc en cours.
- Toute frontière doit être documentée dans FRONTIÈRES ACTIVES.
- Pas de fallback Vision, pas de branches parallèles dans une fonction migrée.
- ⚠️  RÈGLE DE LECTURE : "fichier X listé dans BLOC N" ≠ "fichier X entièrement migré".
  Lire le fichier source avant tout diagnostic.

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
    dom_utils, dropdown_block_resolver, captcha/*, payout, functions reçoivent driver (= shim).
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
2026-06-22  Lecture directe de TOUS les fichiers du projet — inventaire complet établi :
            - Propres (zéro driver) : State/*, Survey/log_utils, Survey/fivesim_client,
              Survey/dom_selection_rules, Survey/dom_registry, Survey/action_types,
              Survey/prompt_builder, Survey/batch_response_parser, platforms/base,
              platforms/__init__, hot_reload, notifier, pause_policy.
            - Migrés avec résidus shim attendus (string literals, pas By.*) :
              dom_analyzer, cta_handler, input_checkbox, input_radio, input_matrix,
              input_utils, input_frame (in_each_frame_recursive), action_dispatcher.
            - Non migrés (By.* Selenium direct) : Survey/functions, dom_context_mapper,
              dom_extractors_areyounet/decipher/misc, dom_question_extractor, dom_utils,
              dropdown_block_resolver, survey_difficulty_guard, runtime_guard,
              captcha/*, payout, platforms/topsurveys (mineur), platforms/ysense.
            Tous couverts par le shim en prod. Roadmap Priorités 1→3 établie.

.