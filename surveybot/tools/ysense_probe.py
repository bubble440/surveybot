"""
ysense_probe.py — Script de test autonome ySense
Objectif : login → /surveys → sélection meilleur ratio → clic → observer la redirection

Usage : python ysense_probe.py
Creds via env : YSENSE_EMAIL, YSENSE_PASSWORD
Snap R2 (prod uniquement) : SNAP_ENABLED=1 + SNAP_R2_ACCOUNT_ID,
                             SNAP_R2_ACCESS_KEY_ID, SNAP_R2_SECRET_ACCESS_KEY,
                             SNAP_R2_BUCKET
"""

import os
import time
import subprocess
import tempfile
from playwright.sync_api import sync_playwright, Page, BrowserContext


# ──────────────────────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────────────────────

EMAIL    = os.getenv("YSENSE_EMAIL", "wilsaah456@gmail.com")
PASSWORD = os.getenv("YSENSE_PASSWORD", "p@ssw0rD!123")

BASE_URL    = "https://www.ysense.com"
LOGIN_URL   = f"{BASE_URL}/login"
SURVEYS_URL = f"{BASE_URL}/surveys"

NAV_TIMEOUT = 45_000

# Domaines appartenant à ySense (onglets à fermer après switch)
PLATFORM_DOMAINS = ["ysense.com"]


# ──────────────────────────────────────────────────────────────────────────────
# SNAP — upload R2 uniquement si SNAP_ENABLED=1 (prod)
# En local, no-op silencieux.
# ──────────────────────────────────────────────────────────────────────────────

_session_id: str = time.strftime("%Y%m%d_%H%M%S")
_snap_step: int = 0


def _snap_enabled() -> bool:
    return os.getenv("SNAP_ENABLED", "").strip() == "1"


def _capture_png(page: Page) -> bytes:
    """
    Capture via scrot (Linux/Xvfb prod) avec fallback page.screenshot().
    """
    display = os.getenv("DISPLAY", "")
    if display:
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                path = tmp.name
            try:
                os.remove(path)
            except Exception:
                pass
            time.sleep(0.3)
            result = subprocess.run(
                ["scrot", path],
                env={**os.environ, "DISPLAY": display},
                timeout=5,
                capture_output=True,
            )
            if result.returncode == 0:
                with open(path, "rb") as f:
                    return f.read()
        except Exception as e:
            print(f"[SNAP] scrot failed ({e}) — fallback screenshot()")
        finally:
            try:
                os.remove(path)
            except Exception:
                pass

    return page.screenshot(full_page=False)


def snap_and_upload(page: Page, label: str) -> None:
    """
    En prod (SNAP_ENABLED=1) : capture + upload R2.
    En local : no-op silencieux.
    """
    if not _snap_enabled():
        return

    global _snap_step
    _snap_step += 1

    try:
        png = _capture_png(page)
    except Exception as e:
        print(f"[SNAP][ERROR] capture failed label={label} : {e}")
        return

    try:
        import boto3
        account_id = os.environ["SNAP_R2_ACCOUNT_ID"]
        access_key = os.environ["SNAP_R2_ACCESS_KEY_ID"]
        secret_key = os.environ["SNAP_R2_SECRET_ACCESS_KEY"]
        bucket     = os.environ["SNAP_R2_BUCKET"]
        endpoint   = f"https://{account_id}.r2.cloudflarestorage.com"
        key        = f"ysense/{_session_id}/{_snap_step:03d}_{label}.png"

        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
        )
        client.put_object(Bucket=bucket, Key=key, Body=png, ContentType="image/png")
        print(f"[SNAP] uploaded → r2://{bucket}/{key} ({len(png)}B)")
    except Exception as e:
        print(f"[SNAP][ERROR] R2 upload failed label={label} : {e}")


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS NAVIGATION
# ──────────────────────────────────────────────────────────────────────────────

def wait_for_network_idle(page: Page, idle_ms: int = 1500, timeout: int = 30_000):
    """Attend networkidle puis un délai fixe. Fallback silencieux."""
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        pass
    time.sleep(idle_ms / 1000)


def pick_best_survey(page: Page) -> dict | None:
    """
    Extrait tous les liens surveys de #survey-list-body via data-attributes DOM.
    Calcule le ratio reward/loi (cents/min) et retourne le meilleur.
    """
    surveys = []
    links = page.query_selector_all(
        "#survey-list-body a.survey-link[data-survey_reward][data-survey_loi]"
    )
    print(f"[SURVEYS] {len(links)} surveys trouvés dans #survey-list-body")

    for link in links:
        try:
            reward = int(link.get_attribute("data-survey_reward") or 0)
            loi    = int(link.get_attribute("data-survey_loi") or 0)
            sid    = link.get_attribute("data-survey_id") or "?"
            href   = link.get_attribute("href") or ""
            if loi <= 0:
                continue
            surveys.append({
                "element": link,
                "sid":     sid,
                "reward":  reward,
                "loi":     loi,
                "ratio":   reward / loi,
                "href":    href,
            })
        except Exception as e:
            print(f"[SURVEYS][WARN] Erreur parsing survey : {e}")

    if not surveys:
        return None

    best = max(surveys, key=lambda s: s["ratio"])
    print(
        f"[SURVEYS] Meilleur ratio → id={best['sid']} "
        f"reward={best['reward']}¢ loi={best['loi']}min "
        f"ratio={best['ratio']:.2f}¢/min"
    )
    return best


def switch_to_latest_window_and_close_others(
    context: BrowserContext,
    base_handles: list,
    timeout: int = 10,
) -> Page | None:
    """
    Attend l'ouverture d'un nouvel onglet survey, ferme les onglets plateforme.
    Adapté de redirect_watcher.py pour ce script autonome.
    Retourne la Page du survey, ou None si aucun nouvel onglet détecté.
    """
    # Cas 1 : nouvel onglet déjà présent au moment de l'appel
    already_new = [p for p in context.pages if p not in base_handles]
    new_page = already_new[-1] if already_new else None

    if new_page is None:
        # Cas 2 : attente événementielle CDP (résolution dès notification Chrome)
        try:
            new_page = context.wait_for_event("page", timeout=timeout * 1000)
            if new_page in base_handles:
                new_page = None
        except Exception:
            new_page = None

    if new_page is not None:
        new_page.bring_to_front()
        # Fermer les onglets plateforme (base_handles)
        for p in list(base_handles):
            try:
                p.close()
            except Exception:
                pass
        # Vérifier que le nouvel onglet est encore vivant après les fermetures
        live = context.pages
        if new_page in live:
            new_page.bring_to_front()
        elif live:
            new_page = live[-1]
            new_page.bring_to_front()
        else:
            raise RuntimeError("Aucun onglet restant après fermeture des onglets plateforme")
        print(f"[SWITCH] Nouvel onglet survey → {new_page.url}")
        return new_page

    # Cas 3 : fallback — chercher un onglet dont l'URL n'est pas sur la plateforme
    for p in context.pages:
        try:
            url = p.url or ""
            if not any(d in url for d in PLATFORM_DOMAINS):
                for op in context.pages:
                    if op is not p:
                        try:
                            op.close()
                        except Exception:
                            pass
                p.bring_to_front()
                print(f"[SWITCH] Fallback onglet externe → {url}")
                return p
        except Exception:
            continue

    print("[SWITCH][WARN] Aucun nouvel onglet détecté — navigation même onglet supposée.")
    return None


# ──────────────────────────────────────────────────────────────────────────────
# ÉTAPE 1 : LOGIN
# ──────────────────────────────────────────────────────────────────────────────

def do_login(page: Page):
    """
    Login ySense. Le formulaire a target="dummy" : la soumission se fait dans un
    iframe caché, le frame principal ne navigue jamais. On attend côté serveur
    via sleep puis on goto explicitement la home pour vérifier la session.
    """
    print(f"[LOGIN] Navigation vers {LOGIN_URL}")
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
    wait_for_network_idle(page)

    email_input = page.wait_for_selector("input#username", state="visible", timeout=20_000)
    email_input.fill(EMAIL)
    print(f"[LOGIN] Email saisi : {EMAIL}")
    time.sleep(0.4)

    pwd_input = page.wait_for_selector("input#password", state="visible", timeout=10_000)
    pwd_input.fill(PASSWORD)
    print("[LOGIN] Mot de passe saisi.")
    time.sleep(0.4)

    btn = page.wait_for_selector("button.sbutton.large", state="visible", timeout=10_000)
    btn.click()
    print("[LOGIN] Bouton Sign In cliqué.")

    # Attendre validation serveur puis naviguer explicitement vers la home
    time.sleep(4)
    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
    wait_for_network_idle(page, idle_ms=1500)
    print(f"[LOGIN] URL après goto home : {page.url}")


# ──────────────────────────────────────────────────────────────────────────────
# ÉTAPE 2 : NAVIGATION VERS /surveys
# ──────────────────────────────────────────────────────────────────────────────

def go_to_surveys(page: Page):
    """
    Navigue vers /surveys et attend que #survey-list-body contienne au moins un lien.
    """
    print(f"[SURVEYS] Navigation vers {SURVEYS_URL}")
    page.goto(SURVEYS_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)

    try:
        page.wait_for_selector(
            "#survey-list-body a.survey-link",
            state="attached",
            timeout=30_000,
        )
        print("[SURVEYS] Liste surveys chargée.")
    except Exception:
        print("[SURVEYS][WARN] Timeout #survey-list-body — on continue quand même.")

    wait_for_network_idle(page, idle_ms=1500)
    print(f"[SURVEYS] URL courante : {page.url}")


# ──────────────────────────────────────────────────────────────────────────────
# ÉTAPE 3 : SÉLECTION, CLIC, SWITCH & SNAP
# ──────────────────────────────────────────────────────────────────────────────

def click_best_survey(page: Page, context: BrowserContext) -> tuple[str | None, Page | None]:
    """
    Sélectionne le meilleur survey, clique, switch vers le nouvel onglet si ouvert,
    attend la stabilisation, puis snap (prod uniquement).
    Retourne (url_finale, survey_page) ou (None, None) si échec.
    """
    best = pick_best_survey(page)
    if best is None:
        print("[SURVEYS][ERROR] Aucun survey disponible.")
        return None, None

    # Capturer les Page objects ouverts AVANT le clic
    base_handles = list(context.pages)

    print(f"[CLICK] Clic natif sur survey id={best['sid']}")
    before_url = page.url

    # Clic natif Playwright (isTrusted: true)
    best["element"].click()

    # Switch vers le nouvel onglet (ou détection navigation même onglet)
    survey_page = switch_to_latest_window_and_close_others(
        context=context,
        base_handles=base_handles,
        timeout=10,
    )

    if survey_page is not None:
        # Nouvel onglet : attendre chargement complet
        try:
            survey_page.wait_for_load_state("domcontentloaded", timeout=30_000)
        except Exception:
            pass
        wait_for_network_idle(survey_page, idle_ms=2000)
        final_url = survey_page.url
        print(f"[CLICK] URL survey (nouvel onglet) : {final_url}")
    else:
        # Même onglet : attendre stabilisation URL
        survey_page = page
        deadline = time.time() + 20
        while time.time() < deadline:
            if page.url != before_url:
                break
            time.sleep(0.3)
        wait_for_network_idle(page, idle_ms=1500)
        final_url = page.url
        print(f"[CLICK] URL survey (même onglet) : {final_url}")

    # Snap de la page survey après stabilisation (prod uniquement)
    snap_and_upload(survey_page, "survey_landing")

    return final_url, survey_page


# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────

def main():
    if not EMAIL or not PASSWORD:
        print("[ERROR] Définir YSENSE_EMAIL et YSENSE_PASSWORD dans l'environnement.")
        return

    with sync_playwright() as pw:
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

            # Vérification session par présence de la navbar logged-in (#ysnNavbarRight)
            if page.query_selector("#ysnNavbarRight") is None:
                print("[LOGIN][ERROR] Session non établie (navbar absente). Arrêt.")
                return
            print("[LOGIN] Session établie ✓")

            # ── Étape 2 : /surveys
            go_to_surveys(page)

            # ── Étape 3 : Clic + switch + snap
            final_url, survey_page = click_best_survey(page, context)

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
            time.sleep(15)
        finally:
            browser.close()


if __name__ == "__main__":
    main()