"""
diag_manual_browser.py — Diagnostic MANUEL (navigateur réel, interaction clavier).

NE FAIT PAS PARTIE de la suite pytest : le nom ne commence volontairement pas par
"test_" (sinon pytest l'importe pendant la collecte et ouvre Chrome). Tout le code
est sous `if __name__ == "__main__"` : un simple import est inerte.

Usage (depuis surveybot/, PowerShell) :
    $env:PROXY_URL  = "host:port"      # optionnel (cf. _parse_proxy_env)
    $env:PROXY_USER = "..."            # optionnel
    $env:PROXY_PASS = "..."            # optionnel
    python diag_manual_browser.py

Les identifiants proxy ne doivent JAMAIS être écrits dans ce fichier (dépôt versionné).
"""
import os


def main() -> None:
    # Profil dédié au diagnostic (ne touche pas aux profils de prod).
    profile_dir = os.environ.get("DIAG_PROFILE_DIR", r"C:\surveybot\profiles\test_diag")
    os.makedirs(profile_dir, exist_ok=True)
    os.environ["CHROME_PROFILE_DIR"] = profile_dir

    # Ne PAS définir BROWSER_MODE (chemin prod par défaut, pas attach).
    # Ne PAS définir SURVEY_HEADLESS (fenêtre visible sur Windows).
    from preselection.playwright_launcher import launch_browser_playwright

    import msvcrt  # Windows uniquement : import local pour ne pas casser un import ailleurs

    page = launch_browser_playwright()
    context = page.context

    print("Fenêtre ouverte (chemin PROD). Navigue et clique normalement jusqu'à PQ2. "
          "Appuie sur une touche ICI (terminal) pour terminer.")

    # try/finally : la fermeture (context puis playwright) s'exécute aussi sur
    # Ctrl+C, ce qui évite les tâches asyncio orphelines ("Task was destroyed...").
    try:
        while True:
            if msvcrt.kbhit():
                msvcrt.getch()
                break
            try:
                # Même mécanisme que redirect_watcher.py : attente événementielle
                # à court timeout (pas d'énumération de context.pages).
                new_page = context.wait_for_event("page", timeout=500)
                try:
                    cdp = context.new_cdp_session(new_page)
                    cdp.send("Runtime.runIfWaitingForDebugger")
                    print("[EVENT] Nouvel onglet détecté, débloqué.")
                except Exception as e:
                    print(f"[EVENT] Nouvel onglet détecté, déblocage échoué (ignoré) : {e}")
            except Exception:
                pass  # timeout normal (500 ms sans nouvel onglet) — on reboucle
    except KeyboardInterrupt:
        print("Interruption clavier — fermeture propre.")
    finally:
        print("Fermeture de la session.")
        try:
            context.close()
        except Exception as e:
            print(f"[CLEANUP] context.close() a échoué (ignoré) : {e}")
        try:
            page._pw.stop()
        except Exception as e:
            print(f"[CLEANUP] pw.stop() a échoué (ignoré) : {e}")


if __name__ == "__main__":
    main()
