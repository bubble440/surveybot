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

⚠️ PÉRIMÈTRE PARTIEL DES BLOCS ATTACH (1, 2, 3x) :
Les blocs 1–3b ont été migrés dans le cadre du chantier attach. Chaque bloc
couvrait uniquement les fonctions utilisées dans le chemin d'appel attach
(run_attach_login_takeover, run_attach_preselection_takeover, run_attach_takeover).
Plusieurs fichiers listés dans ces blocs contiennent des fonctions
NON migrées, car hors du chemin attach (ex : run_survey dans survey_handler.py,
fonctions Selenium dans launch.py, switch_to_latest_window_and_close_others dans
redirect_watcher.py, _handle_topsurveys_exclusion_popup dans functions.py).
Ces fonctions restantes constituent le périmètre de la migration PROD (blocs P1+).

================================================================================
DÉCOUPAGE EN BLOCS
================================================================================

BLOC 1 — Login + sélection de survey
  Statut : ✅ migré (validé en attach le 2026-06-21 — login + sélection survey OK,
           pont vers BLOC 2 fonctionnel)
  Fichiers : main.py (attach launcher + orchestration _attach_*),
             platforms/topsurveys.py (login, select_survey),
             preselection/auth_handler.py (login, is_session_expired,
               handle_proxy_error_page_if_needed, dom_probe, snap, net_probe),
             preselection/survey_navigator.py (go_to_best_value_survey et
               toutes ses fonctions internes : _click_surveys_tab,
               _handle_mystery_box_popup, _select_best_value_card,
               _wait_for_spa_ready, _wait_for_survey_popup)
  Entrée  : démarrage du mode attach (Chrome déjà lancé, port debug exposé)
  Sortie  : un survey/popup TopSurveys est ouvert dans l'onglet piloté →
            relais vers BLOC 2

BLOC 2 — Résolution pop-up de présélection
  Statut : ✅ migré (validé en attach le 2026-06-22 — présélection complète OK,
           pont vers BLOC 3 fonctionnel)
  Fichiers : preselection/survey_handler.py (run_attach_preselection_takeover,
               is_topsurveys_preselection_popup, et la boucle principale de
               présélection qui suit),
             preselection/question_analyzer.py (get_response_for_question,
               click_participer_if_present, click_participer_if_qualified,
               handle_disqualification_and_retry, extract_popup_html,
               extract_question_text),
             preselection/response_executor.py (execute_response,
               select_checkbox_answers, click_next_button)
  ⚠️  Fonctions hors périmètre attach dans survey_handler.py (non migrées) :
       run_survey() — chemin prod uniquement ; reste sur shim jusqu'au BLOC P2.
  Entrée  : popup de présélection TopSurveys détecté (objet produit par BLOC 1)
  Sortie  : soit retour à TopSurveys (boucle BLOC 1/2), soit appel à
            Survey.survey_solver.solve_full_survey(...) → relais vers BLOC 3

BLOC 3 — Résolution du survey externe
  BLOC 3a — Survey/survey_solver.py
    Statut : ✅ migré (validé en attach le 2026-06-22 — résolution survey OK)
    Fichiers : Survey/survey_solver.py (solve_full_survey et toutes ses
                 fonctions internes : _switch_to_external_tab, count_actionable_elements,
                 _has_actionable_elements, _get_multi_page_state, _looks_like_end_screen,
                 _recover_from_network_error, _recover_from_yougov_app_error,
                 get_current_survey_ctx, TopSurveysReturn)
    Entrée  : Page Playwright native (externe, non-plateforme)
    Sortie  : fin du survey (soft-restart) ou retour TopSurveys (TopSurveysReturn)

  BLOC 3b1 — Survey/survey_executor.py
    Statut : ✅ migré (validé en attach le 2026-06-22)

  BLOC 3b2 — Survey/page_snapshot.py + Management/redirect_watcher.py (partiel)
    Statut : ✅ migré (validé en attach le 2026-06-22)
    Fichiers : Survey/page_snapshot.py (_wait_dom_settle, _dump_frames_best_effort,
                 dump_page_snapshot, snapshot_if_enabled, _slug),
               Management/redirect_watcher.py (wait_for_final_redirection, _dom_signature,
                 wait_for_navigation_or_dom_change, wait_for_page_load UNIQUEMENT)
    ⚠️  Fonction hors périmètre dans redirect_watcher.py (non migrée) :
         switch_to_latest_window_and_close_others — attend window_handles/switch_to.window/close
         (API shim). Sera migrée en BLOC P3 (multi-onglets Playwright natif).

  BLOC 3b3 — Survey/dom_classifier.py + Survey/batch_response_parser.py
    Statut : ✅ migré (validé en attach le 2026-06-22)
    Fichiers : Survey/dom_classifier.py (toutes les fonctions de classification DOM),
               Survey/batch_response_parser.py (aucun driver — parsing pur, rien à migrer)

  Survey/prompt_builder.py
    Statut : ✅ aucune migration nécessaire — zéro driver, construction de prompts GPT pure
             (vérifié le 2026-06-22)

  BLOC 3b4 -- Survey/dom_analyzer.py
    Statut : ✅ migré (valide en attach le 2026-06-22)
    Fichiers : Survey/dom_analyzer.py (~4600 lignes, ~20 fonctions top-level)

  BLOC 3b5a -- Survey/frame_utils.py
    Statut : ✅ migré (valide en attach le 2026-06-22)
    Fichiers : Survey/frame_utils.py (_frame_elements, switch_to_frame_chain, iter_frame_chains)

  BLOC 3b5b -- Survey/input_utils.py + Survey/cta_handler.py
    Statut : ✅ migré (valide en attach le 2026-06-22)
    Fichiers : Survey/input_utils.py, Survey/cta_handler.py

  BLOC 3b5c -- 7 modules input_*.py
    Statut : ✅ migré (valide en attach le 2026-06-22)
    Fichiers : Survey/input_frame.py, Survey/input_text.py,
               Survey/input_checkbox.py, Survey/input_dropdown.py,
               Survey/input_matrix.py, Survey/input_radio.py,
               Survey/input_slider.py

  BLOC 3b5d -- Survey/input_handler.py
    Statut : ✅ migré

  BLOC 3b5d-fix -- Survey/input_matrix.py (corrections post-BLOC 3b5c)
    Statut : ✅ corrigé (2026-06-22)
    Corrections :
    - BUG 1 (lignes 219, 248) : _pw_page(driver).evaluate(...) remplacé par
      _handle(r).click() / _handle(cb).click() dans select_cell_action()
      (driver hors scope → NameError à l'exécution).
    - BUG 2 (lignes 471-474) : txt = "" initialisé avant le try, affecté dans
      le try, except réaffecte "". if txt ... déplacé après le bloc try/except
      (évite txt indéfini en cas d'exception avant affectation).

  BLOC 3b6 -- Survey/action_dispatcher.py
    Statut : ✅ migré (2026-06-22) — EN ATTENTE de validation prod
    Périmètre : 7481 lignes, ~50 fonctions top-level
    Migrations appliquées :
    - Imports supprimés : selenium.webdriver.common.by.By,
      selenium.webdriver.common.action_chains.ActionChains,
      selenium.webdriver.common.keys.Keys
    - Helpers ajoutés : _pw_page(d), _handle(el) (identiques aux autres modules)
    - find_elements(By.CSS_SELECTOR/XPATH, sel) → query_selector_all(sel)
    - find_element(By.CSS_SELECTOR/XPATH, sel) → query_selector(sel) + None guard
    - driver.query_selector_all/query_selector → _pw_page(driver).query_selector_all/query_selector
    - By.ID → [id='...'], By.NAME → [name='...'], By.TAG_NAME → tag CSS direct
    - ActionChains.move_to_element(el).click() → el.hover(); el.click()
    - ActionChains.move_to_element_with_offset(track, x, y).click() → page.mouse.click(abs_x, abs_y)
    - ActionChains.click_and_hold(src).move_to_element(dst).release() → source.drag_to(drop_zone)
    - execute_cdp_cmd("Input.dispatchMouseEvent") → page.mouse.move/down/up
    - driver.execute_script("arguments[0].scrollIntoView(...)") → page.evaluate("(e) => e.scrollIntoView(...)", el)
    - driver.execute_script("arguments[0].click()") → page.evaluate("(e) => e.click()", el)
    - el.is_displayed() → el.is_visible()
    - el.rect → el.bounding_box()
    - el.send_keys(text) → el.type(text)
    - el.tag_name → el.evaluate("e => (e.tagName || '').toLowerCase()")
    - el.text → el.inner_text()
    - Keys.ARROW_RIGHT/LEFT → "ArrowRight"/"ArrowLeft" + handle_el.press(key)
    - Keys.ESCAPE → combobox.press("Escape")
    - Keys.CONTROL + "a" → element.press("Control+a")
    - driver.switch_to.default_content() → supprimé (no-op en Playwright)
    - driver.execute_script(...) restants (92 occurrences) : passent par le shim
      (shim.execute_script → page.evaluate) — OK pendant coexistence shim
    - XPath axes (ancestor-or-self, ancestor) : préfixés "xpath=" sur query_selector()
    - Frontière execute_action/execute_actions_plan : reçoivent encore un shim
      depuis survey_executor.py ; _pw_page(driver) extrait la Page native.

  FIN DU CHANTIER BLOC 3b — Tous les modules migrés :
    survey_executor.py (3b1), page_snapshot.py (3b2), dom_classifier.py (3b3),
    dom_analyzer.py (3b4), frame_utils.py (3b5a), input_utils.py + cta_handler.py (3b5b),
    7 × input_*.py (3b5c), input_handler.py (3b5d), input_matrix.py (3b5d-fix),
    action_dispatcher.py (3b6).

--- BLOCS PROD (migration mode Fly.io / headless) ---

BLOC P1 — Orchestration prod : launch.py
  Statut : ✅ migré (2026-06-22)
  Fichiers : launch.py (safe_get, init_session_and_enter_surveys, soft_restart_resume)
  Migrations appliquées :
  - Imports Selenium supprimés : selenium.webdriver, WebDriverWait, By, EC, TimeoutException,
    webdriver.Chrome, Options, Service.
  - _pw_page(d) helper ajouté en tête de fichier (pattern identique aux autres modules).
  - safe_get() : driver.get(url) + set_page_load_timeout(70) + TimeoutException
    → page.goto(url, timeout=70_000, wait_until="domcontentloaded") +
      détection de timeout par type(e).__name__ == "TimeoutError" + page.evaluate("window.stop()").
  - init_session_and_enter_surveys() : WebDriverWait(driver, N).until(EC.presence_of_element_located(...))
    → _page.wait_for_selector(sel, state="attached", timeout=N_000).
  - soft_restart_resume() : driver.find_elements("css selector", sel)
    → _pw_page(driver).query_selector(sel).
  ⚠️  Fonctions hors périmètre dans launch.py (non migrées, actuellement désactivées en prod) :
       restore_session_cookies() et restore_datadome_cookies() utilisent driver.execute_cdp_cmd().
       Ces fonctions sont commentées/désactivées en production — pas de risque d'appel actif.
       À migrer si réactivées (page.context.add_cookies() ou CDP via page.context.new_cdp_session()).

BLOC P2 — Présélection prod : survey_handler.run_survey()
  Statut : 🔲 à migrer
  Fichiers : preselection/survey_handler.py (run_survey et sa boucle interne _run_survey_impl,
               distincte de run_attach_preselection_takeover déjà migrée en BLOC 2)
  Dépendances : redirect_watcher.switch_to_latest_window_and_close_others (multi-onglets,
                hors périmètre BLOC 2) — à évaluer : migrer ici ou en BLOC P3.

BLOC P3 — Multi-onglets + fonctions Selenium résiduelles
  Statut : 🔲 à migrer
  Fichiers :
    Management/redirect_watcher.py (switch_to_latest_window_and_close_others —
      window_handles, switch_to.window, close → page.context.pages, page.bring_to_front(),
      page.close())
    Survey/functions.py (_handle_topsurveys_exclusion_popup — By, find_elements,
      execute_script, WebDriverWait, EC — jamais inclus dans aucun bloc précédent)
  Note : Survey/screenshot_analyzer.py (take_screenshot) attend encore un shim
         pour save_screenshot — à évaluer si réactivé.

  POUR CLORE TOTALEMENT LE CHANTIER (étapes restantes après P1/P2/P3) :
    1. Validation prod : test manuel d'une résolution complète de page survey
       passant par execute_action et execute_actions_plan (au moins un radio,
       un checkbox, un texte, une matrix). Confirmer aucune régression.
    2. Suppression playwright_shim.py : une fois P2/P3 validés prod, supprimer
       playwright_shim.py et PlaywrightDriverShim. Adapter le pont _page_to_shim
       dans main.py (plus de shim → passer la Page directement à execute_survey_page).
    3. Suppression shim interne survey_solver.py : _make_shim() dans
       solve_full_survey() à supprimer ; adapter execute_survey_page pour recevoir
       une Page native (pas un shim).

================================================================================
RÈGLES VALABLES POUR TOUS LES BLOCS
================================================================================

- Un bloc = un patch = une validation manuelle en attach (ou prod pour les blocs P)
  avant de passer au bloc suivant.
- Le patch ne touche QUE les fichiers du bloc en cours. Toute fonction encore
  hors périmètre reste sur le shim — ne pas migrer "au passage" une fonction
  d'un bloc ultérieur même si elle semble proche dans le code.
- Toute frontière (point où un objet Playwright natif doit être compatible
  avec du code consommant encore l'API shim, ou inversement) doit être
  documentée dans la section FRONTIÈRES ACTIVES avant de clore le patch.
- Pas de fallback Vision, pas de branches supplémentaires pour gérer les deux
  API en parallèle au sein d'une même fonction migrée — une fonction migrée
  parle Playwright natif uniquement ; l'adaptation se fait à la frontière, pas
  à l'intérieur.
- PROJECT_ARCHITECTURE.md et BOT_EVOLUTION_MEMORY.md restent les références
  pour tout ce qui ne concerne pas directement ce chantier (extracteurs DOM,
  etc.) — ce fichier ne les remplace pas, il s'y ajoute pour le périmètre
  migration Playwright native.

================================================================================
FRONTIÈRES ACTIVES (à mettre à jour après chaque patch validé)
================================================================================

FRONTIÈRE BLOC 1 → BLOC 2 (active depuis le 2026-06-21, inchangée après BLOC 2)

  Côté migré (Playwright natif) :
    - main.py : run_attach_login_takeover(page, pw, ...) reçoit une Page
      Playwright native dès l'attachement (connexion directe au port de debug,
      sans passer par webdriver.Chrome Selenium).
    - preselection/auth_handler.py : toutes les fonctions du périmètre BLOC 1
      opèrent sur l'API Playwright native. Helper _pw_page(d) utilisée comme
      adaptateur defensive (extrait ._page si shim, retourne d sinon).
    - preselection/survey_navigator.py : go_to_best_value_survey(page) et ses
      fonctions internes opèrent sur l'API Playwright native.

  Pont (le pont lui-même, inchangé) :
    - main.py, fonction _page_to_shim(page, pw) : enveloppe la Page Playwright
      native dans une instance du shim PlaywrightDriverShim, juste après la
      sélection du survey (popup de présélection ouvert) et juste avant
      l'appel à run_attach_preselection_takeover.
    - Ce pont reste nécessaire car les fonctions BLOC 2 appellent
      redirect_watcher.switch_to_latest_window_and_close_others(driver, ...)
      qui attend un objet shim (window_handles, switch_to, close). Ces appels
      sont des appels à du code hors périmètre BLOC 2 — le shim joue le rôle
      d'adaptateur uniquement pour ces appels ; tout le reste (DOM queries,
      evaluate, wait_for_selector) passe par _pw_page(driver) → Page native.

  Côté migré (BLOC 2, depuis le 2026-06-22) :
    - preselection/survey_handler.py : run_attach_preselection_takeover,
      is_topsurveys_preselection_popup, _safe_page_text — API Playwright native
      via _pw_page(driver). redirect_watcher passé avec driver (shim) au lieu
      d'un wrapper temporaire, ce qui est correct car driver = shim dans tous
      les chemins d'appel actuels.
    - preselection/question_analyzer.py : extract_popup_html, extract_popup_text_with_js,
      extract_options_js, extract_select_options_js, get_response_for_question,
      click_participer_if_present, click_participer_if_qualified,
      handle_disqualification_and_retry — API Playwright native via _pw_page(d).
    - preselection/response_executor.py : execute_response, select_checkbox_answers,
      click_next_button, _execute_async_radio — API Playwright native via _pw_page(d).
    - ElementHandle.click() remplace label.click() (shim) / ActionChains.
    - page.wait_for_function("(el) => el.classList.contains('p-checked')", arg=label)
      remplace WebDriverWait + lambda.
    - page.wait_for_load_state("load") remplace wait_for_page_load(driver).

  Correction popup_not_detected (cause racine identifiée et corrigée) :
    - Cause : _wait_for_survey_popup (BLOC 1) attendait ps-popup-content-wrapper
      (visible dès l'ouverture du popup), mais is_topsurveys_preselection_popup
      (BLOC 2) requiert hasActions = présence de ps-common-actions-button ou
      ps-skip-question-button, rendu par Vue APRÈS le chargement de la première
      question. Aucun lien avec le shim — timing pur.
    - Correction : dans run_attach_preselection_takeover, ajout d'un
      page.wait_for_selector("button[data-test-id='ps-common-actions-button'],
      button[data-test-id='ps-skip-question-button']", state='attached',
      timeout=10_000) avant le premier appel à is_topsurveys_preselection_popup.

FRONTIÈRE BLOC 2 → BLOC 3a (active depuis le 2026-06-22)

  Côté migré (BLOC 2, Playwright natif) :
    - preselection/survey_handler.py (_run_survey_impl) : appelle désormais
      Survey.survey_solver.solve_full_survey(_pw_page(driver), ...) en extrayant
      la Page native depuis le shim. Le shim._page a été mis à jour vers l'onglet
      externe par switch_to_latest_window_and_close_others après le clic Participer.
      L'import de _pw_page se fait via : from preselection.auth_handler import _pw_page.

  Pont (où se fait l'adaptation) :
    - Dans _run_survey_impl (survey_handler.py), ligne d'appel :
        Survey.survey_solver.solve_full_survey(_ss_pw_page(driver), ...)
      C'est l'unique point de transition BLOC 2 → BLOC 3a.

  Côté natif (BLOC 3a, depuis le 2026-06-22) :
    - Survey/survey_solver.py : solve_full_survey(driver, ...) accepte maintenant
      une Page Playwright native (ou un shim via _pw_page, rétrocompat prod path).
      page = _pw_page(driver) en tête.

FRONTIÈRE BLOC 3a → BLOC 3b1 (remplace BLOC 3a → BLOC 3b, active depuis le 2026-06-22)

  Côté natif (BLOC 3a) :
    - solve_full_survey() crée _shim = _make_shim(page) et appelle
      Survey.survey_executor.execute_survey_page(_shim, account_id, api_key, ctx).

  Pont (unique appel) :
    - execute_survey_page reçoit _shim comme `driver`.
    - page = _pw_page(driver) = shim._page (Page native) pour toutes les opérations DOM directes.
    - `driver` (= shim) transmis sans changement à tous les sous-modules encore sur le shim.

  Côté natif (BLOC 3b1, depuis le 2026-06-22) :
    - Survey/survey_executor.py : toutes les ~25 fonctions migrent en Playwright natif.
    - By, ActionChains supprimés. _pw_page(d) + _make_shim(page) ajoutés.
    - execute_survey_page() : page = _pw_page(driver) pour DOM direct ;
      driver (= shim) transmis aux sous-modules hors-périmètre.

FRONTIÈRE INTERNE BLOC 3b1 → BLOC 3b2 (active depuis le 2026-06-22, mise à jour BLOC 3b2)

  Côté natif (BLOC 3a) :
    - solve_full_survey() crée un shim interne en début de fonction :
        _shim = _make_shim(page)
        _shim._survey_account_id = account_id
    - Ce shim est mis à jour (_shim._page = page) après tout changement d'onglet.

  Pont (unique appel) :
    - execute_survey_page reçoit _shim comme `driver`.
    - Tous les 7 sous-modules (dom_analyzer, page_snapshot, input_handler,
      prompt_builder, dom_classifier, action_dispatcher, batch_response_parser)
      reçoivent `driver` (= shim) depuis execute_survey_page.
    - redirect_watcher (_dom_signature, wait_for_navigation_or_dom_change)
      reçoit aussi `driver` (= shim).

  Côté migré (BLOC 3b2, depuis le 2026-06-22) :
    - Survey/page_snapshot.py : _pw_page(driver) + page.evaluate/content/screenshot.
      MHTML via page.context.new_cdp_session(page).send("Page.captureSnapshot").
    - Management/redirect_watcher.py (wait_for_final_redirection, _dom_signature,
      wait_for_navigation_or_dom_change, wait_for_page_load) : _pw_page(driver).

  Frontière BLOC 3b2 → frame_utils.py :
    - _dump_frames_best_effort passe driver (shim) à iter_frame_chains /
      switch_to_frame_chain. Après switch, getattr(driver, "_current_frame", page)
      donne la Frame Playwright pour les ops DOM dans l'iframe.

  Frontière redirect_watcher.switch_to_latest_window_and_close_others :
    - Reste sur le shim (window_handles, switch_to.window, close).
    - Sera migrée en BLOC P3.

  Convention _current_frame identique à BLOC 3b2 (page_snapshot.py) :
    - Dans les blocs with switch_to_frame_chain(driver, chain), on utilise
      getattr(driver, "_current_frame", _pw_page(driver)) pour obtenir
      la Frame Playwright courante (main frame ou iframe selon chain).
      Aucune divergence par rapport à page_snapshot.py.

  Côté migré (BLOC 3b3, depuis le 2026-06-22) :
    - Survey/dom_classifier.py : _pw_page(driver) + page.evaluate/query_selector_all.
      Frame iteration : current_frame.evaluate() après switch_to_frame_chain.
    - Survey/batch_response_parser.py : parsing pur, zéro driver, rien à migrer.

FRONTIÈRE BLOC P1 → BLOC P2 (active depuis le 2026-06-22)

  Côté migré (BLOC P1, Playwright natif) :
    - launch.py : safe_get(), init_session_and_enter_surveys(), soft_restart_resume()
      opèrent sur l'API Playwright native via _pw_page(driver).
    - Le driver transmis depuis launch_driver_or_fail() est l'objet retourné par
      launch_browser_playwright() (playwright_launcher.py) — Page native ou shim selon
      l'implémentation de launch_browser_playwright.

  Pont actif (vers BLOC P2) :
    - run_main_loop() (launch.py) appelle run_survey(driver, ...) dans survey_handler.py.
    - run_survey() est la fonction prod non encore migrée (BLOC P2). Elle reçoit le même
      objet driver que safe_get / init_session_and_enter_surveys.
    - Tant que run_survey() n'est pas migrée, elle doit recevoir un objet compatible
      avec _pw_page() (shim ou Page native).

FRONTIÈRE BLOC 3a → Survey/functions.py (hors découpage en blocs)

FRONTIÈRES SUPPLÉMENTAIRES CONFIRMÉES (hors découpage en blocs)

  survey_solver._recover_from_network_error (migré BLOC 3a, utilise _pw_page en interne) :
    - execute_survey_page passe `driver` (shim) → _pw_page(shim) extrait la Page. ✓
    - Idem pour main.py (run_attach_takeover) qui passe directement le shim.

  Survey/dom_registry.py : get_target(target_id) — aucun paramètre driver. Pas de frontière.
  Survey/fivesim_client.py : buy_number, reuse_number, poll_sms_code, finish_order — aucun driver.
  Survey/screenshot_analyzer.py : take_screenshot(driver) attend encore un shim (save_screenshot).
    Pont : driver (= shim) transmis depuis execute_survey_page.

FRONTIÈRE BLOC 3b1 → Survey/functions.py (hors découpage en blocs)

  Survey/functions.py (_handle_topsurveys_exclusion_popup) n'a jamais été inclus
  dans aucun bloc de migration. Il utilise encore l'API Selenium complète
  (By, find_elements, execute_script, WebDriverWait, EC, etc.).
  Périmètre BLOC P3.

  Pont :
    - Dans solve_full_survey, tous les appels platform.is_on_platform() et
      platform.handle_post_survey() passent _shim (pas page) :
        platform.is_on_platform(_shim)
        platform.handle_post_survey(_shim, account_id)
    - is_on_platform() utilise driver.current_url → shim.current_url → page.url ✓
    - handle_post_survey() → _handle_topsurveys_exclusion_popup(_shim, ...) → API shim ✓
    - Les fonctions de survey_navigator appelées depuis _handle_topsurveys_exclusion_popup
      (go_to_best_value_survey, _handle_mystery_box_popup) sont déjà BLOC 1-migrées
      et utilisent _pw_page(d) en interne → compatibles shim et Page. ✓

INTERFACE switch_to_frame_chain (nouvelle, depuis BLOC 3b5a)

  Entrée : driver = PlaywrightDriverShim OU Page Playwright native.
  Yield  : True si navigation reussie, False si hors-borne ou erreur.
  Effet  : met a jour driver._current_frame (si shim) :
    - chain=[] : driver._current_frame = page (racine)
    - chain=[i] : driver._current_frame = Frame Playwright de l'iframe i
  Sortie : driver._current_frame remis a page (equivalent default_content).
  Usage appelant :
    current_frame = getattr(driver, '_current_frame', _pw_page(driver))
    current_frame.evaluate(js)  # ou .content(), .query_selector_all() etc.
  Appelants déjà migrés (dom_classifier, page_snapshot, dom_analyzer) :
    Aucun changement nécessaire. Ils utilisent déjà le getattr pattern.

================================================================================
HISTORIQUE
================================================================================

2026-06-21  Décision : migration franche (Option B), suppression du shim,
            zéro shim même temporairement sur le périmètre attach.
            Découpage en 3 blocs fonctionnels validé (login+sélection /
            résolution pop-up / résolution survey).
            Fichier créé. BLOC 1 non démarré.

2026-06-21  BLOC 1 validé en attach : login (deux interfaces topsurveys.app /
            app.topsurveys.app/app-login) + sélection survey (carte rentable
            #3, 0.61€/8min) réussis de bout en bout en Playwright natif.
            Pont vers BLOC 2 (_page_to_shim dans main.py) fonctionnel.
            Point d'attention noté : popup_not_detected immédiatement après
            sélection — à traiter dans le patch BLOC 2.

2026-06-22  BLOC 3b6 migré (action_dispatcher.py, 7481 lignes) :
            By/ActionChains/Keys Selenium supprimés. _pw_page + _handle ajoutés.
            find_elements/find_element → query_selector_all/query_selector.
            ActionChains → hover+click / page.mouse. execute_cdp_cmd → page.mouse.
            el.text → el.inner_text(). el.rect → el.bounding_box().
            Keys.ARROW → handle.press(). el.tag_name → el.evaluate(tagName).
            switch_to.default_content() supprimé. 92 execute_script restants via shim.
            FIN DU CHANTIER BLOC 3b — tous les modules traités.

2026-06-22  BLOC 3b5d-fix (input_matrix.py corrections post-3b5c) :
            BUG 1 : _pw_page(driver) dans select_cell_action() (driver hors scope)
            → _handle(r).click() / _handle(cb).click().
            BUG 2 : txt non défini avant try → initialisé à "" avant le bloc.

2026-06-22  BLOC 3b5c migre (7 modules input_*.py) :
            By.* -> string literals, ActionChains -> hover+click,
            Select() -> .select_option(), WebDriverWait -> polling,
            execute_script -> page.evaluate ou shim via execute_script,
            in_each_frame_recursive -> frame_utils switch_to_frame_chain,
            cell.parent -> page = _pw_page(driver).

2026-06-22  BLOC 3b5b migre (input_utils.py + cta_handler.py) :
            By.* -> string literals, execute_script -> page.evaluate,
            ActionChains -> hover+click/mouse.down/up,
            WebDriverWait -> polling loop, _in_each_frame_recursive
            -> frame_utils switch_to_frame_chain, StaleElement -> Exception.

2026-06-22  BLOC 3b5a migre (frame_utils.py) : migration Playwright native.
            switch_to_frame_chain met a jour driver._current_frame via
            page.main_frame.child_frames[idx] (natif). _reset() en finally.
            Selenium supprime. Interface getattr pattern preservee.

2026-06-22  BLOC 3b4 migre (dom_analyzer.py) : 23 execute_script -> page.evaluate().
            _handle(el) helper pour passer PlaywrightElementShim._h aux evaluate().
            _find_fullscreen_iframe_idx : suppression switch_to.default_content().
            find_elements/find_element gardes shim pour compat helpers non migres.

2026-06-22  BLOC 3b3 migré (dom_classifier.py + batch_response_parser.py) :
            dom_classifier : _pw_page + page.evaluate/query_selector_all.
            Frame iteration : current_frame.evaluate() (convention identique
            à page_snapshot.py BLOC 3b2). batch_response_parser : aucun driver,
            marqué directement migré.

2026-06-22  Survey/prompt_builder.py vérifié : zéro driver, zéro Selenium.
            Construction de prompts GPT pure. Aucune migration nécessaire. ✅

2026-06-22  BLOC 3b1 migré (survey_executor.py) : toutes les ~25 fonctions migrent
            en Playwright natif. By + ActionChains supprimés. _pw_page + _make_shim
            ajoutés. Pattern : page = _pw_page(driver) pour DOM direct ; driver
            (= shim) transmis aux 7 sous-modules (dom_analyzer, action_dispatcher,
            input_handler, etc.) et à redirect_watcher — frontière BLOC 3b1 → 3b2+.
            dom_registry + fivesim_client : pas de driver param, pas de frontière.
            screenshot_analyzer.take_screenshot : reçoit driver (shim).
            Frame switching (Walr/CF carousel) : content_frame() natif Playwright.

2026-06-22  BLOC 3a migré (survey_solver.py) : solve_full_survey et toutes ses
            fonctions internes passent en Playwright natif. Selenium supprimé.
            Shim interne (_make_shim) créé pour les appels hors-périmètre :
            execute_survey_page (BLOC 3b), platform.handle_post_survey
            (Survey/functions.py, non migré), detect_strict_survey, etc.
            _wait_for_url_stable remplace redirect_watcher.wait_for_final_redirection.
            Frontières documentées : BLOC 2→3a, BLOC 3a→3b, BLOC 3a→Survey/functions.py.
            Point d'appel survey_handler.py adapté (_pw_page(driver)).

2026-06-22  BLOC 2 migré : résolution popup présélection en Playwright natif.
            Cause racine popup_not_detected identifiée (timing Vue : ps-common-
            actions-button rendu après ps-popup-content-wrapper). Corrigé par
            wait_for_selector ciblé avant is_topsurveys_preselection_popup.
            ActionChains / WebDriverWait / By / EC supprimés des 3 fichiers BLOC 2.
            Pont BLOC 2 → BLOC 3 documenté : shim traverse tout BLOC 2,
            _page mis à jour par redirect_watcher après qualification,
            solve_full_survey et execute_survey_page reçoivent le shim (BLOC 3).
            ⚠️  run_survey() (chemin prod) non migrée — périmètre BLOC P2.

2026-06-22  Scope du fichier étendu : migration globale (attach + prod Fly.io).
            Blocs P1/P2/P3 introduits pour couvrir le chemin prod.
            Note transversale ajoutée : les BLOCs 1–3b couvrent uniquement les
            fonctions du chemin attach ; les fonctions prod dans les mêmes fichiers
            (run_survey, switch_to_latest_window_and_close_others, etc.) restent
            hors périmètre jusqu'aux blocs P correspondants.

2026-06-22  BLOC P1 migré (launch.py — orchestration prod) :
            Imports Selenium supprimés (selenium.webdriver, WebDriverWait, By, EC,
            TimeoutException). _pw_page(d) helper ajouté.
            safe_get() : driver.get + TimeoutException → page.goto(timeout=70_000) +
            détection type(e).__name__ == "TimeoutError" + page.evaluate("window.stop()").
            init_session_and_enter_surveys() : WebDriverWait/EC → wait_for_selector().
            soft_restart_resume() : find_elements → query_selector().
            Fonctions restore_session_cookies / restore_datadome_cookies : non migrées
            (désactivées en prod — exécute_cdp_cmd → à traiter si réactivées).
            Frontière P1 → P2 documentée.

.