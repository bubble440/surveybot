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
migré et un bloc pas-encore-migré est documentée explicitement ci-dessous —
c'est la partie la plus importante de ce fichier : elle dit quel type d'objet
(Page Playwright natif vs PlaywrightDriverShim) chaque fonction attend en
entrée et produit en sortie, tant que la migration n'est pas terminée.

⚠️ PÉRIMÈTRE PARTIEL DES BLOCS ATTACH (1, 2, 3x) — RÈGLE DE LECTURE CRITIQUE :
Les blocs 1–3b ont été migrés dans le cadre du chantier attach. Chaque bloc
couvrait UNIQUEMENT les fonctions utilisées dans le chemin d'appel attach
(run_attach_login_takeover → run_attach_preselection_takeover → run_attach_takeover).
Plusieurs fichiers listés dans ces blocs contiennent des fonctions NON migrées,
car hors du chemin attach. Ces fonctions sont dans les mêmes fichiers que des
fonctions déjà migrées, mais elles n'ont PAS été touchées.

Conséquence directe pour la lecture de ce fichier :
  "BLOC N migré" signifie "les fonctions du chemin listées dans ce bloc sont
  migrées". Cela ne signifie PAS que l'ensemble du fichier est migré.

================================================================================
ÉTAT DE LA MIGRATION PAR FICHIER (vérification lecture directe 2026-06-22)
================================================================================

PÉRIMÈTRE PROD (chemin main() → login → présélection) — VALIDÉ ✅
  launch.py                            ✅ propre
  main.py                              ✅ propre prod ; résidus attach intentionnels
  preselection/auth_handler.py         ✅ propre
  preselection/survey_navigator.py     ✅ propre
  preselection/survey_handler.py       ✅ propre prod ; résidu attach run_attach_preselection_takeover
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
                                          Sera supprimé en fin de chantier.
  preselection/playwright_launcher.py  ✅ propre pour le chemin prod (launch_browser_playwright) ;
                                          résidus dans launch_browser + fonction annexe
                                          lignes 634–699 — non appelées en prod.

PÉRIMÈTRE RÉSOLUTION SURVEY (blocs 3a–3b) — VALIDÉ attach, EN ATTENTE validation prod
  Survey/survey_solver.py              ✅ migré (attach 2026-06-22)
  Survey/survey_executor.py            ✅ migré (attach 2026-06-22)
  Survey/page_snapshot.py              ✅ migré (attach 2026-06-22)
  Survey/dom_classifier.py             ✅ propre — zéro résidu Selenium
  Survey/dom_frame_selector.py         ✅ propre — zéro résidu Selenium
  Survey/dom_analyzer.py               ✅ migré (attach 2026-06-22) — find_elements via shim
                                          string literals (pas By.*) — comportement attendu
  Survey/frame_utils.py                ✅ migré (attach 2026-06-22)
  Survey/input_utils.py                ✅ migré (attach 2026-06-22)
  Survey/cta_handler.py                ✅ migré (attach 2026-06-22) — find_elements via shim
                                          string literals — comportement attendu
  Survey/input_handler.py              ✅ migré (attach 2026-06-22)
  Survey/input_matrix.py               ✅ migré + fix (attach 2026-06-22)
  Survey/input_frame.py                ✅ migré (attach 2026-06-22)
  Survey/input_text.py                 ✅ migré (attach 2026-06-22)
  Survey/input_checkbox.py             ✅ migré (attach 2026-06-22)
  Survey/input_dropdown.py             ✅ migré (attach 2026-06-22)
  Survey/input_radio.py                ✅ migré (attach 2026-06-22)
  Survey/input_slider.py               ✅ migré (attach 2026-06-22)
  Survey/action_dispatcher.py          ✅ migré (attach 2026-06-22) — 92 execute_script via shim
                                          (documentés BLOC 3b6) — EN ATTENTE validation prod
  Survey/prompt_builder.py             ✅ propre (zéro driver)
  Survey/batch_response_parser.py      ✅ propre (zéro driver)
  Survey/action_types.py               ✅ propre (zéro driver)

FICHIERS NON MIGRÉS — couverts par le shim en prod, à traiter par ordre d'impact :
  Survey/functions.py                  🔲 _handle_topsurveys_exclusion_popup — API Selenium complète
  Survey/dom_context_mapper.py         🔲 By, ActionChains, execute_script (×3), find_element,
                                          current_url — non inclus dans les blocs précédents
  Survey/dom_extractors_areyounet.py   🔲 By, find_elements/find_element (×20+) — non inclus
  Survey/dom_extractors_decipher.py    🔲 By, find_elements — non inclus
  Survey/dom_extractors_misc.py        🔲 By, find_elements/find_element (×20+), execute_script —
                                          non inclus
  Management/survey_difficulty_guard.py 🔲 By, find_elements (×10+), execute_script,
                                          is_displayed, el.rect, el.tag_name, get_attribute
  Management/runtime_guard.py          🔲 By, WebDriverWait, EC, execute_script (lignes 120–135)
  captcha/datadome_handler.py          🔲 execute_script (×2), current_url, add_cookie, refresh
  captcha/normal_captcha.py            🔲 By, find_elements, execute_script, is_displayed,
                                          get_attribute, screenshot_as_base64
  captcha/recaptcha_handler.py         🔲 execute_script (×3), current_url
  captcha/recaptcha_utils.py           🔲 By, find_elements (×3), execute_script (×2)
  captcha/tencent_handler.py           🔲 execute_script (×6), find_element, ActionChains,
                                          current_url
  Cash/payout.py                       🔲 By, WebDriverWait, EC, ActionChains — entièrement Selenium
  platforms/topsurveys.py              🔲 résidu mineur : driver.current_url dans is_on_platform()
  platforms/ysense.py                  🔲 entièrement Selenium (select_survey = NotImplementedError)
  Management/snap_uploader.py          ⚠️ fallbacks mineurs get_screenshot_as_png / save_screenshot
                                          dans _capture_png() — scrot est le chemin principal

================================================================================
DÉCOUPAGE EN BLOCS
================================================================================

BLOC 1 — Login + sélection de survey
  Statut : ✅ migré (validé en attach le 2026-06-21)
  Fichiers : main.py (attach launcher), platforms/topsurveys.py,
             preselection/auth_handler.py, preselection/survey_navigator.py

BLOC 2 — Résolution pop-up de présélection (chemin attach)
  Statut : ✅ migré (validé en attach le 2026-06-22)
  Fichiers (fonctions chemin attach uniquement) :
    preselection/survey_handler.py : run_attach_preselection_takeover,
      is_topsurveys_preselection_popup, _safe_page_text
    preselection/question_analyzer.py : toutes les fonctions d'extraction et de réponse
    preselection/response_executor.py : execute_response, select_checkbox_answers,
      click_next_button, _execute_async_radio, _click_radio_label

BLOC 3 — Résolution du survey externe (chemin attach)
  BLOC 3a  — Survey/survey_solver.py                           ✅ migré (2026-06-22)
  BLOC 3b1 — Survey/survey_executor.py                         ✅ migré (2026-06-22)
  BLOC 3b2 — Survey/page_snapshot.py + redirect_watcher.py    ✅ migré (2026-06-22)
  BLOC 3b3 — Survey/dom_classifier.py + batch_response_parser ✅ migré (2026-06-22)
  BLOC 3b4 — Survey/dom_analyzer.py                           ✅ migré (2026-06-22)
  BLOC 3b5a — Survey/frame_utils.py                           ✅ migré (2026-06-22)
  BLOC 3b5b — Survey/input_utils.py + cta_handler.py          ✅ migré (2026-06-22)
  BLOC 3b5c — 7 × input_*.py                                  ✅ migré (2026-06-22)
  BLOC 3b5d — Survey/input_handler.py                         ✅ migré (2026-06-22)
  BLOC 3b5d-fix — input_matrix.py corrections                 ✅ corrigé (2026-06-22)
  BLOC 3b6 — Survey/action_dispatcher.py                      ✅ migré (2026-06-22)
                                                                  EN ATTENTE validation prod

--- BLOCS PROD ---

BLOC P1 — launch.py                                            ✅ migré (2026-06-22)
BLOC P2 — survey_handler._run_survey_impl
          + response_executor._click_radio_label               ✅ migré (2026-06-22)
BLOC P3 — redirect_watcher.switch_to_latest_window_and_close_others
          + question_analyzer.click_participer_if_qualified
          + question_analyzer.handle_disqualification_and_retry ✅ migré (2026-06-22)

================================================================================
POUR CLORE TOTALEMENT LE CHANTIER
================================================================================

  Priorité 1 — Validation prod :
    Test complet login → présélection → clic Participer → résolution survey.

  Priorité 2 — Fichiers non migrés, par ordre d'impact sur le chemin prod :
    a) Management/survey_difficulty_guard.py — appelé à chaque itération survey
    b) Survey/dom_context_mapper.py — appelé depuis survey_executor
    c) Survey/dom_extractors_areyounet.py, dom_extractors_decipher.py,
       dom_extractors_misc.py — extracteurs DOM appelés depuis dom_analyzer
    d) Management/runtime_guard.py — monitoring continu
    e) captcha/* (datadome_handler, normal_captcha, recaptcha_handler,
       recaptcha_utils, tencent_handler)
    f) Cash/payout.py
    g) Survey/functions.py._handle_topsurveys_exclusion_popup
    h) platforms/topsurveys.py (résidu mineur is_on_platform)
    i) Management/snap_uploader.py (fallbacks mineurs _capture_png)

  Priorité 3 — Fin de chantier :
    j) Suppression playwright_shim.py + adapter _page_to_shim dans main.py
    k) Suppression _make_shim() dans survey_solver.py
    l) Nettoyage fonctions Selenium legacy playwright_launcher.py (lignes 634–699)
    m) platforms/ysense.py (select_survey = NotImplementedError, faible priorité)
    n) Nettoyage résidus attach dans main.py (hors impact prod)

================================================================================
RÈGLES VALABLES POUR TOUS LES BLOCS
================================================================================

- Un bloc = un patch = une validation manuelle avant de passer au bloc suivant.
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
  - survey_difficulty_guard, dom_context_mapper, dom_extractors_*, captcha/*, payout,
    functions reçoivent driver (= shim) ; API Selenium absorbée par le shim.
  - Tant que le shim existe, pas de crash en prod.

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
2026-06-22  Lecture directe périmètre présélection : prod validé ✅.
2026-06-22  Lecture directe périmètre étendu — inventaire complet établi :
            - Fichiers propres : State/*, platforms/base, platforms/__init__,
              hot_reload, Survey/dom_classifier, Survey/dom_frame_selector,
              Survey/action_types, Survey/batch_response_parser, Survey/prompt_builder.
            - Fichiers migrés avec résidus shim attendus : Survey/dom_analyzer (shim
              string literals), Survey/cta_handler (shim string literals),
              Survey/action_dispatcher (92 execute_script via shim).
            - Fichiers non migrés identifiés : Survey/functions, Survey/dom_context_mapper,
              Survey/dom_extractors_areyounet/decipher/misc, Management/survey_difficulty_guard,
              Management/runtime_guard, captcha/*, Cash/payout, platforms/topsurveys
              (mineur), platforms/ysense, Management/snap_uploader (mineur).
            Tous couverts par le shim en prod. Roadmap Priorités 1→3 établie.

.