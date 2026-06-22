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
  launch.py                  ✅ propre — zéro résidu Selenium dans le chemin prod
  main.py                    ✅ propre — résidus Selenium uniquement dans fonctions attach
                               (_attach_tab_score, _attach_select_tab, _attach_pick_ui_active_tab,
                               run_attach_takeover) ; jamais exécutées en prod
  preselection/auth_handler.py         ✅ propre
  preselection/survey_navigator.py     ✅ propre
  preselection/survey_handler.py       ✅ propre dans le chemin prod (_run_survey_impl) ;
                               résidus attach dans run_attach_preselection_takeover
                               (driver.window_handles ligne ~169) — hors périmètre prod
  preselection/question_analyzer.py    ✅ propre
  preselection/response_executor.py    ✅ propre
  preselection/question_validation.py  ✅ propre (zéro driver)
  preselection/config_loader.py        ✅ propre (zéro driver)
  preselection/secret_loader.py        ✅ propre (zéro driver)
  preselection/chrome_profile_store.py ✅ propre (zéro driver)
  Management/redirect_watcher.py       ✅ propre

RÉSIDUS ATTACH CONNUS (intentionnels, hors périmètre prod) :
  main.py — _attach_tab_score, _attach_select_tab, _attach_pick_ui_active_tab,
             run_attach_takeover : execute_script, switch_to.window, window_handles,
             current_url, WebDriverWait, By, find_elements — chemin LOCAL debug uniquement
  preselection/survey_handler.py — run_attach_preselection_takeover :
             driver.window_handles + switch_to_latest_window_and_close_others
             (base_handles en strings Selenium) — chemin attach uniquement

FICHIERS SPÉCIAUX :
  preselection/playwright_shim.py      ✅ normal — C'EST le shim, contient l'API Selenium
                               par design. Sera supprimé en fin de chantier.
  preselection/playwright_launcher.py  ✅ propre pour le chemin prod (launch_browser_playwright) ;
                               résidus Selenium dans launch_browser et une fonction annexe
                               (lignes 634–699) — fonctions legacy non appelées en prod

PÉRIMÈTRE RÉSOLUTION SURVEY (blocs 3a–3b) — À VALIDER en prod :
  Survey/survey_solver.py      ✅ migré (attach validé 2026-06-22)
  Survey/survey_executor.py    ✅ migré (attach validé 2026-06-22)
  Survey/page_snapshot.py      ✅ migré (attach validé 2026-06-22)
  Survey/dom_classifier.py     ✅ migré (attach validé 2026-06-22)
  Survey/dom_analyzer.py       ✅ migré (attach validé 2026-06-22)
  Survey/frame_utils.py        ✅ migré (attach validé 2026-06-22)
  Survey/input_utils.py        ✅ migré (attach validé 2026-06-22)
  Survey/cta_handler.py        ✅ migré (attach validé 2026-06-22)
  Survey/input_handler.py      ✅ migré (attach validé 2026-06-22)
  Survey/input_matrix.py       ✅ migré + fix (attach validé 2026-06-22)
  Survey/input_frame.py        ✅ migré (attach validé 2026-06-22)
  Survey/input_text.py         ✅ migré (attach validé 2026-06-22)
  Survey/input_checkbox.py     ✅ migré (attach validé 2026-06-22)
  Survey/input_dropdown.py     ✅ migré (attach validé 2026-06-22)
  Survey/input_radio.py        ✅ migré (attach validé 2026-06-22)
  Survey/input_slider.py       ✅ migré (attach validé 2026-06-22)
  Survey/action_dispatcher.py  ✅ migré (attach validé 2026-06-22) — EN ATTENTE validation prod
  Survey/prompt_builder.py     ✅ propre (zéro driver)
  Survey/batch_response_parser.py ✅ propre (zéro driver)
  Survey/functions.py          🔲 NON migré — _handle_topsurveys_exclusion_popup utilise
                               API Selenium complète. Couvert par le shim en prod.
                               À migrer dans un bloc futur.

================================================================================
DÉCOUPAGE EN BLOCS
================================================================================

BLOC 1 — Login + sélection de survey
  Statut : ✅ migré (validé en attach le 2026-06-21)
  Fichiers : main.py (attach launcher),
             platforms/topsurveys.py,
             preselection/auth_handler.py,
             preselection/survey_navigator.py

BLOC 2 — Résolution pop-up de présélection (chemin attach)
  Statut : ✅ migré (validé en attach le 2026-06-22)
  Fichiers (fonctions du chemin attach uniquement) :
    preselection/survey_handler.py : run_attach_preselection_takeover,
      is_topsurveys_preselection_popup, _safe_page_text
    preselection/question_analyzer.py : toutes les fonctions d'extraction et de réponse
    preselection/response_executor.py : execute_response, select_checkbox_answers,
      click_next_button, _execute_async_radio, _click_radio_label

BLOC 3 — Résolution du survey externe (chemin attach)
  BLOC 3a — Survey/survey_solver.py    ✅ migré (2026-06-22)
  BLOC 3b1 — Survey/survey_executor.py ✅ migré (2026-06-22)
  BLOC 3b2 — Survey/page_snapshot.py + redirect_watcher.py (partiel) ✅ migré (2026-06-22)
  BLOC 3b3 — Survey/dom_classifier.py + batch_response_parser.py ✅ migré (2026-06-22)
  BLOC 3b4 — Survey/dom_analyzer.py   ✅ migré (2026-06-22)
  BLOC 3b5a — Survey/frame_utils.py   ✅ migré (2026-06-22)
  BLOC 3b5b — Survey/input_utils.py + cta_handler.py ✅ migré (2026-06-22)
  BLOC 3b5c — 7 × input_*.py          ✅ migré (2026-06-22)
  BLOC 3b5d — Survey/input_handler.py ✅ migré (2026-06-22)
  BLOC 3b5d-fix — input_matrix.py corrections ✅ corrigé (2026-06-22)
  BLOC 3b6 — Survey/action_dispatcher.py ✅ migré (2026-06-22) — EN ATTENTE validation prod

--- BLOCS PROD ---

BLOC P1 — launch.py                   ✅ migré (2026-06-22)
BLOC P2 — survey_handler._run_survey_impl + response_executor._click_radio_label ✅ migré (2026-06-22)
BLOC P3 — redirect_watcher.switch_to_latest_window_and_close_others
          + question_analyzer.click_participer_if_qualified
          + question_analyzer.handle_disqualification_and_retry ✅ migré (2026-06-22)

PROCHAINE ÉTAPE — Résolution survey prod :
  Lecture directe + verdict des fichiers Survey/* (survey_solver, survey_executor,
  action_dispatcher, dom_analyzer, input_*.py, etc.) pour confirmer l'état réel
  avant validation prod.

================================================================================
POUR CLORE TOTALEMENT LE CHANTIER
================================================================================

  1. Lecture + verdict des fichiers Survey/* (résolution survey).
  2. Validation prod complète : login → présélection → clic Participer → résolution survey.
  3. Migration Survey/functions.py._handle_topsurveys_exclusion_popup.
  4. Suppression playwright_shim.py + adapter _page_to_shim dans main.py.
  5. Suppression _make_shim() dans survey_solver.py.
  6. Nettoyage des fonctions Selenium legacy dans playwright_launcher.py
     (launch_browser, lignes 634–699).
  7. Nettoyage des résidus attach dans main.py si souhaité (hors impact prod).

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

FRONTIÈRE BLOC 3b → Survey/functions.py
  - _handle_topsurveys_exclusion_popup reçoit _shim ; API Selenium absorbée par le shim.
  - Non migré — à traiter en bloc futur.

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
2026-06-22  Lecture directe de tous les fichiers du périmètre présélection :
            prod validé ✅. Résidus attach identifiés et documentés (intentionnels).
            Prochaine étape : lecture + verdict fichiers Survey/* (résolution survey).

.