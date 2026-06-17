# PLAYWRIGHT_MIGRATION.md
# Suivi de migration Selenium → Playwright natif
# Créé : 2026-06-18
# Contexte : --remote-debugging-port identifié comme cause racine du blocage DataDome sur Cint.
#            Confirmé le 2026-06-17 par test playwright_access_check.py (page Cint chargée sans blocage).

================================================================================
STATUT GLOBAL
================================================================================

Option A (Shim)   : ✅ EN PLACE — playwright_shim.py déployé
Option B (Native) : ⬜ NON DÉMARRÉ

================================================================================
OPTION A — SHIM DE COMPATIBILITÉ (état courant)
================================================================================

Principe : PlaywrightDriverShim + PlaywrightElementShim imitent l'API Selenium.
Le code métier existant ne change pas. playwright_launcher.py expose une fonction
launch_browser_playwright() qui retourne un PlaywrightDriverShim.

Fichiers créés :
  preselection/playwright_shim.py     ← shim complet

Prochaine étape pour activer Option A :
  1. Ajouter launch_browser_playwright() dans playwright_launcher.py
     (importer playwright_shim, lancer via sync_playwright, retourner le shim)
  2. Dans main.py / launch.py : remplacer launch_browser() par launch_browser_playwright()
  3. Tester sur un sondage complet en prod

API Selenium couverte par le shim :
  ✅ driver.get(url)
  ✅ driver.current_url
  ✅ driver.title
  ✅ driver.page_source
  ✅ driver.find_element(by, value)
  ✅ driver.find_elements(by, value)
  ✅ driver.execute_script(script, *args)      — arguments[N] supportés
  ✅ driver.execute_async_script(script, *args)
  ✅ driver.execute_cdp_cmd(cmd, params)       — via CDPSession Playwright (sans port TCP)
  ✅ driver.get_screenshot_as_png()
  ✅ driver.save_screenshot(path)
  ✅ driver.switch_to.frame(element|index)
  ✅ driver.switch_to.default_content()
  ✅ driver.switch_to.parent_frame()
  ✅ driver.implicitly_wait(seconds)
  ✅ driver.set_page_load_timeout(seconds)
  ✅ driver.back() / forward() / refresh()
  ✅ driver.get_cookies() / add_cookie() / delete_all_cookies()
  ✅ driver.window_handles / current_window_handle
  ✅ driver.close() / driver.quit()
  ✅ element.tag_name / .text / .location / .size
  ✅ element.get_attribute() / get_property() / value_of_css_property()
  ✅ element.is_displayed() / is_enabled() / is_selected()
  ✅ element.click() / send_keys() / clear() / submit()
  ✅ element.find_element() / find_elements()
  ✅ ActionChains : move_to_element, click, double_click, drag_and_drop, send_keys
  ✅ Select : select_by_value, select_by_index, select_by_visible_text, options
  ✅ WebDriverWait.until() / until_not()

Limitations connues (à surveiller en prod) :
  ⚠️  execute_cdp_cmd : crée/détache une CDPSession à chaque appel.
      Acceptable pour les cas rares ; à optimiser si appelé en boucle.
  ⚠️  switch_to.frame(str) non implémenté (name/id). Si nécessaire, ajouter
      recherche par attribut name/id dans la liste page.frames.
  ⚠️  execute_async_script : wrap en Promise JS. Les scripts qui appellent
      callback plusieurs fois peuvent avoir un comportement inattendu.
  ⚠️  stale element : Playwright lève PlaywrightError au lieu de
      StaleElementReferenceException. Les catch Exception() existants
      devraient absorber ça — à valider sur les extracteurs qui re-cherchent
      les éléments en cas d'erreur.
  ⚠️  ActionChains.click() sans argument : utilise position (0,0). Rarement
      utilisé sans cible ; à surveiller.
  ⚠️  WebDriverWait.until() avec EC (expected_conditions) Selenium :
      les conditions EC standards (presence_of_element_located, etc.) reçoivent
      le driver shim — elles appellent driver.find_element() qui délègue
      à Playwright. Doit fonctionner sans modification des appels.

================================================================================
OPTION B — MIGRATION FRANCHE (objectif futur)
================================================================================

Principe : supprimer complètement le shim. Réécrire les fichiers métier pour
utiliser directement l'API Playwright (page, locator, frame_locator…).

Avantages vs Option A :
  - API Playwright plus expressive (Locator vs ElementHandle)
  - Attentes implicites natives (auto-wait sur les actions)
  - Gestion des frames plus propre (frame_locator)
  - Meilleure stabilité face aux éléments stale
  - Plus facile à maintenir long terme

Gains attendus sur la détection anti-bot :
  - Aucun supplémentaire vs Option A (le problème --remote-debugging-port
    est déjà résolu par le mode pipe, commun aux deux options)

Périmètre estimé :
  - action_dispatcher.py  : ~6100 lignes — impact majeur (driver.* partout)
  - survey_executor.py    : impact moyen (navigation, screenshots)
  - dom_utils.py          : impact moyen (el.find_elements, el.get_attribute...)
  - frame_utils.py        : impact moyen (switch_to.frame → frame_locator)
  - page_snapshot.py      : impact faible (execute_script → page.evaluate)
  - dom_extractors_*.py   : impact moyen par fichier
  - redirect_watcher.py   : impact faible
  - idle_monitor.py       : impact faible
  - cta_handler.py        : impact moyen

Priorité de migration suggérée (du moins risqué au plus risqué) :
  1. page_snapshot.py         (peu de surface Selenium, autour de execute_script)
  2. frame_utils.py           (switch_to → frame_locator, bien délimité)
  3. dom_utils.py             (éléments passés en paramètre — adapter les signatures)
  4. idle_monitor.py / redirect_watcher.py
  5. dom_extractors_misc.py   (1 extracteur à la fois, tester en prod)
  6. dom_extractors_*.py      (idem)
  7. survey_executor.py
  8. action_dispatcher.py     (en dernier — fichier le plus critique)

Règles pour Option B :
  - Un fichier à la fois, validé en prod avant de passer au suivant
  - Les signatures de fonctions qui reçoivent un `driver` ou `el` Selenium
    doivent accepter soit le shim soit l'objet Playwright natif (duck typing)
  - Supprimer playwright_shim.py uniquement quand action_dispatcher.py est migré

================================================================================
DÉCISION D'ACTIVATION OPTION B
================================================================================

Critère de déclenchement : Option A stable en prod sur ≥ 7 jours sans régression.
Décision finale : Wilfried

================================================================================
HISTORIQUE
================================================================================

2026-06-17  Diagnostic confirmé : --remote-debugging-port = cause racine blocage DataDome Cint
            Test playwright_access_check.py → page Cint chargée sans blocage
            (même IP IPRoyal ISP, même fingerprint JS, Playwright pipe mode)

2026-06-18  Option A créée : playwright_shim.py
            PLAYWRIGHT_MIGRATION.md créé
