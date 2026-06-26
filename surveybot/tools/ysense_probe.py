"""
ysense_probe.py — Script de test autonome ySense
Objectif : login → /surveys → sélection meilleur ratio → clic → observer la redirection

Usage : python ysense_probe.py
Creds via env : YSENSE_EMAIL, YSENSE_PASSWORD
"""

import os
import time
from playwright.sync_api import sync_playwright


# ──────────────────────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────────────────────

EMAIL    = os.getenv("YSENSE_EMAIL", "")
PASSWORD = os.getenv("YSENSE_PASSWORD", "")

BASE_URL    = "https://www.ysense.com"
LOGIN_URL   = f"{BASE_URL}/login"
SURVEYS_URL = f"{BASE_URL}/surveys"

# Timeout réseau standard (ms)
NAV_TIMEOUT = 45_000


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def wait_for_network_idle(page, idle_ms: int = 1500, timeout: int = 30_000):
    """
    Attend que le réseau soit inactif (pas de requête en cours) pendant idle_ms.
    Fallback silencieux sur timeout.
    """
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        pass
    time.sleep(idle_ms / 1000)


def page_contains(page, text: str) -> bool:
    try:
        return text.lower() in (page.content() or "").lower()
    except Exception:
        return False


def pick_best_survey(page) -> dict | None:
    """
    Extrait tous les liens surveys de #survey-list-body via data-attributes DOM.
    Calcule le ratio reward/loi (cents/min) et retourne le meilleur.
    Retourne None si aucun survey trouvé.
    """
    surveys = []
    links = page.query_selector_all("#survey-list-body a.survey-link[data-survey_reward][data-survey_loi]")
    print(f"[SURVEYS] {len(links)} surveys trouvés dans #survey-list-body")

    for link in links:
        try:
            reward = int(link.get_attribute("data-survey_reward") or 0)
            loi    = int(link.get_attribute("data-survey_loi") or 0)
            sid    = link.get_attribute("data-survey_id") or "?"
            href   = link.get_attribute("href") or ""
            if loi <= 0:
                continue
            ratio = reward / loi
            surveys.append({
                "element": link,
                "sid":     sid,
                "reward":  reward,
                "loi":     loi,
                "ratio":   ratio,
                "href":    href,
            })
        except Exception as e:
            print(f"[SURVEYS][WARN] Erreur parsing survey : {e}")
            continue

    if not surveys:
        return None

    best = max(surveys, key=lambda s: s["ratio"])
    print(
        f"[SURVEYS] Meilleur ratio → id={best['sid']} "
        f"reward={best['reward']}¢ loi={best['loi']}min "
        f"ratio={best['ratio']:.2f}¢/min href={best['href'][:60]}"
    )
    return best


# ──────────────────────────────────────────────────────────────────────────────
# ÉTAPE 1 : LOGIN
# ──────────────────────────────────────────────────────────────────────────────

def do_login(page):
    """
    Navigue vers /login, saisit email + mot de passe, clique Sign In.
    Le formulaire ySense a target="dummy" : la soumission se fait dans un iframe
    caché, le frame principal ne navigue jamais. On attend la validation serveur
    via un sleep, puis on navigue explicitement vers la home pour vérifier la session.
    """
    print(f"[LOGIN] Navigation vers {LOGIN_URL}")
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
    wait_for_network_idle(page)

    # Saisie email (champ id="username" sur ySense)
    email_input = page.wait_for_selector("input#username", state="visible", timeout=20_000)
    email_input.fill(EMAIL)
    print(f"[LOGIN] Email saisi : {EMAIL}")
    time.sleep(0.4)

    # Saisie mot de passe
    pwd_input = page.wait_for_selector("input#password", state="visible", timeout=10_000)
    pwd_input.fill(PASSWORD)
    print("[LOGIN] Mot de passe saisi.")
    time.sleep(0.4)

    # Clic natif sur le bouton Sign In (button.sbutton.large)
    btn = page.wait_for_selector("button.sbutton.large", state="visible", timeout=10_000)
    btn.click()
    print("[LOGIN] Bouton Sign In cliqué.")

    # Le formulaire soumet dans un iframe target="dummy" — le frame principal
    # ne navigue pas. On laisse le temps au serveur de valider le login,
    # puis on navigue explicitement vers la home pour vérifier la session.
    time.sleep(4)
    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
    wait_for_network_idle(page, idle_ms=1500)
    print(f"[LOGIN] URL après goto home : {page.url}")


# ──────────────────────────────────────────────────────────────────────────────
# ÉTAPE 2 : NAVIGATION VERS /surveys
# ──────────────────────────────────────────────────────────────────────────────

def go_to_surveys(page):
    """
    Navigue vers /surveys et attend le chargement complet de la liste.
    La liste est dans #survey-list-body ; on attend qu'elle contienne au moins un lien.
    """
    print(f"[SURVEYS] Navigation vers {SURVEYS_URL}")
    page.goto(SURVEYS_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)

    # Attendre que le conteneur de liste soit présent et non vide
    try:
        page.wait_for_selector(
            "#survey-list-body a.survey-link",
            state="attached",
            timeout=30_000,
        )
        print("[SURVEYS] Liste surveys chargée.")
    except Exception:
        print("[SURVEYS][WARN] Timeout en attendant #survey-list-body — on continue quand même.")

    wait_for_network_idle(page, idle_ms=1500)
    print(f"[SURVEYS] URL courante : {page.url}")


# ──────────────────────────────────────────────────────────────────────────────
# ÉTAPE 3 : SÉLECTION & CLIC DU MEILLEUR SURVEY
# ──────────────────────────────────────────────────────────────────────────────

def click_best_survey(page, context) -> str | None:
    """
    Trouve le meilleur survey (ratio reward/loi), clique dessus.
    Gère les deux cas :
      - Ouverture dans un nouvel onglet (target="_blank") → switch + retour URL
      - Navigation même onglet → retour URL
    Retourne l'URL de destination finale, ou None si échec.
    """
    best = pick_best_survey(page)
    if best is None:
        print("[SURVEYS][ERROR] Aucun survey disponible.")
        return None

    pages_before = set(id(p) for p in context.pages)

    print(f"[CLICK] Clic natif sur survey id={best['sid']}")
    before_url = page.url

    # Clic natif Playwright (isTrusted: true)
    best["element"].click()
    time.sleep(1.5)

    # Détecter si un nouvel onglet s'est ouvert
    pages_after = context.pages
    new_pages = [p for p in pages_after if id(p) not in pages_before]

    if new_pages:
        survey_page = new_pages[-1]
        print(f"[CLICK] Nouvel onglet détecté — attente chargement...")
        try:
            survey_page.wait_for_load_state("domcontentloaded", timeout=30_000)
        except Exception:
            pass
        wait_for_network_idle(survey_page, idle_ms=2000)
        final_url = survey_page.url
        print(f"[CLICK] URL survey (nouvel onglet) : {final_url}")
        return final_url
    else:
        # Navigation même onglet — attendre stabilisation URL
        deadline = time.time() + 20
        while time.time() < deadline:
            if page.url != before_url:
                break
            time.sleep(0.3)
        wait_for_network_idle(page, idle_ms=1500)
        final_url = page.url
        print(f"[CLICK] URL survey (même onglet) : {final_url}")
        return final_url


# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────

def main():
    if not EMAIL or not PASSWORD:
        print("[ERROR] Définir YSENSE_EMAIL et YSENSE_PASSWORD dans l'environnement.")
        return

    with sync_playwright() as pw:
        # Lancement Chrome headful pour supervision visuelle
        browser = pw.chromium.launch(headless=False, slow_mo=50)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        try:
            # ── Étape 1 : Login
            do_login(page)

            # Vérification session par présence de la navbar logged-in (#ysnNavbarRight).
            # Plus fiable que l'URL car le formulaire soumet dans un iframe target="dummy".
            if page.query_selector("#ysnNavbarRight") is None:
                print("[LOGIN][ERROR] Session non établie après login (navbar absente). Arrêt.")
                return
            print("[LOGIN] Session établie ✓")

            # ── Étape 2 : Aller sur /surveys
            go_to_surveys(page)

            # ── Étape 3 : Cliquer sur le meilleur survey
            final_url = click_best_survey(page, context)
            if final_url:
                print(f"\n✅ Résultat final : {final_url}")
                print("\n[PROBE] Pause 3600s pour observation visuelle...")
                time.sleep(3600)
            else:
                print("[PROBE][ERROR] Aucun survey cliqué.")

        except Exception as e:
            print(f"[PROBE][FATAL] {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(15)  # laisser le browser ouvert pour debug
        finally:
            browser.close()


if __name__ == "__main__":
    main()