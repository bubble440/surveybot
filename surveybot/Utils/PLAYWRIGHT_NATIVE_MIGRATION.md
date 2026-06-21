# PLAYWRIGHT_NATIVE_MIGRATION.md
# Suivi de la migration franche (Option B) : suppression du shim Selenium,
# bascule vers l'API Playwright native (Page, Locator, frame_locator...).
# Démarré : 2026-06-21
# Remplace/poursuit PLAYWRIGHT_MIGRATION.md (Option A — shim) pour le périmètre attach.

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

Mode attach uniquement pour l'instant (local, manuel). La prod (lancement
natif headless/Xvfb via launch_browser_playwright()) n'est pas concernée par
ce chantier et doit rester inchangée jusqu'à nouvel ordre.

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
  Entrée  : popup de présélection TopSurveys détecté (objet produit par BLOC 1)
  Sortie  : soit retour à TopSurveys (boucle BLOC 1/2), soit appel à
            Survey.survey_solver.solve_full_survey(...) → relais vers BLOC 3

BLOC 3 — Résolution du survey externe
  Statut : ⬜ non démarré
  Fichiers : Survey/survey_solver.py (solve_full_survey et toutes ses
               fonctions internes),
             Survey/survey_executor.py (execute_survey_page et toutes ses
               fonctions internes),
             + imports lazy d'execute_survey_page : Survey/dom_analyzer.py,
               Survey/page_snapshot.py, Survey/input_handler.py,
               Survey/prompt_builder.py, Survey/dom_classifier.py,
               Survey/action_dispatcher.py, Management/redirect_watcher.py,
               Survey/batch_response_parser.py
  Entrée  : appel direct depuis BLOC 2 (driver/page déjà sur le survey externe)
  Sortie  : fin du survey (soft-restart) ou retour TopSurveys (TopSurveysReturn)

  Note : action_dispatcher.py (~6100 lignes) est le fichier le plus gros et
  le plus critique de ce bloc. Cohérent avec PLAYWRIGHT_MIGRATION.md (Option A),
  il doit être traité en dernier, isolément, avec validation prod dédiée avant
  bascule finale — pas dans le même patch que le reste du BLOC 3.

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

FRONTIÈRE BLOC 2 → BLOC 3 (active depuis le 2026-06-22)

  Côté migré (BLOC 2, Playwright natif) :
    - preselection/survey_handler.py : _run_survey_impl appelle
      Survey.survey_solver.solve_full_survey(driver, ...) et
      Survey.survey_executor._handle_topsurveys_exclusion_popup(driver, ...)
      avec l'objet driver reçu en paramètre (shim dans tous les chemins).
    - main.py : run_attach_login_takeover appelle
      survey_executor.execute_survey_page(shim, ...) après la sortie du BLOC 2,
      avec le shim créé par _page_to_shim.

  Pont (où se fait l'adaptation) :
    - Le shim créé par _page_to_shim dans run_attach_login_takeover (main.py)
      traverse tout BLOC 2 et arrive intact en BLOC 3.
    - Après click_participer_if_qualified → switch_to_latest_window_and_close_others,
      le shim._page est mis à jour par le redirect_watcher (switch vers le nouvel
      onglet survey externe). BLOC 3 reçoit donc le shim pointant vers le bon onglet.

  Côté shim (BLOC 3, pas encore migré) :
    - Survey/survey_solver.py : solve_full_survey(driver, ...) attend un objet
      façon Selenium (le shim).
    - Survey/survey_executor.py : execute_survey_page(driver, ...) — même attente.

================================================================================
RÈGLES VALABLES POUR TOUS LES BLOCS
================================================================================

- Un bloc = un patch = une validation manuelle en attach avant de passer au
  bloc suivant.
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

2026-06-22  BLOC 2 migré : résolution popup présélection en Playwright natif.
            Cause racine popup_not_detected identifiée (timing Vue : ps-common-
            actions-button rendu après ps-popup-content-wrapper). Corrigé par
            wait_for_selector ciblé avant is_topsurveys_preselection_popup.
            ActionChains / WebDriverWait / By / EC supprimés des 3 fichiers BLOC 2.
            Pont BLOC 2 → BLOC 3 documenté : shim traverse tout BLOC 2,
            _page mis à jour par redirect_watcher après qualification,
            solve_full_survey et execute_survey_page reçoivent le shim (BLOC 3).