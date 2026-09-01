import os
os.makedirs(r"C:\surveybot\profiles\test_diag", exist_ok=True)
os.environ["CHROME_PROFILE_DIR"] = r"C:\surveybot\profiles\test_diag"

# Recommandé : même proxy qu'un run prod récent, pour éliminer la réputation IP
# comme variable parasite (cf. _parse_proxy_env — lit config OU l'environnement)
os.environ["PROXY_URL"]  = ""
os.environ["PROXY_USER"] = "14a9c246242fe"
os.environ["PROXY_PASS"] = "605e436f75"

# Ne PAS définir BROWSER_MODE — on veut le chemin prod par défaut, pas attach.
# Ne PAS définir SURVEY_HEADLESS — laissé vide/absent => fenêtre visible sur Windows.

from preselection.playwright_launcher import launch_browser_playwright  # adapte le chemin si besoin

page = launch_browser_playwright()
context = page.context

# Correction (2e essai) : la 1ère version énumérait context.pages, ce que
# redirect_watcher.py ne fait JAMAIS pour détecter un nouvel onglet — il utilise
# context.wait_for_event("page", timeout=...), une attente événementielle.
# Énumérer context.pages juste après l'apparition d'un onglet en pause CDP peut
# bloquer l'énumération elle-même (avant même d'atteindre le code de reprise),
# ce qui expliquerait l'absence totale de log après le tout premier onglet.
# On reprend ici exactement le mécanisme de redirect_watcher.py, dans une boucle
# à courts timeouts pour rester réactif sans jamais quitter ce thread.
import msvcrt, time

print("Fenêtre ouverte (chemin PROD, webdriver=False confirmé). Navigue et clique "
      "normalement jusqu'à PQ2. Appuie sur une touche ICI (terminal) pour terminer.")

while True:
    if msvcrt.kbhit():
        msvcrt.getch()
        break
    try:
        new_page = context.wait_for_event("page", timeout=500)
        try:
            cdp = context.new_cdp_session(new_page)
            cdp.send("Runtime.runIfWaitingForDebugger")
            print(f"[EVENT] Nouvel onglet détecté, débloqué.")
        except Exception as e:
            print(f"[EVENT] Nouvel onglet détecté, déblocage échoué (ignoré) : {e}")
    except Exception:
        pass  # timeout normal (500ms écoulées sans nouvel onglet) — on reboucle

print("Terminé — fermeture propre de la session.")
try:
    context.close()
except Exception as e:
    print(f"[CLEANUP] context.close() a échoué (ignoré) : {e}")
try:
    page._pw.stop()
except Exception as e:
    print(f"[CLEANUP] pw.stop() a échoué (ignoré) : {e}")