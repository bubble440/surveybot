import time, os, requests, base64, re
_PLM = os.getenv("PROXY_LATENCY_MODE", "").strip().lower() in {"1", "true", "yes"}
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
import time
from selenium.common.exceptions import JavascriptException

def _is_prod_env() -> bool:
    """
    Retourne True si on tourne dans un environnement de production (Fly.io/Docker).
    """
    return os.getenv("RUN_ENV", "local").lower() != "local"

def dom_probe(driver):
    """
    Petit dump DOM pour debug.
    ⚠️ Ne s'exécute que lorsque l'on tourne sur AWS (ECS, etc.).
    En local, on retourne immédiatement pour éviter les erreurs XPath et le bruit.
    """
    if not _is_prod_env():
        return

    print("[DOM] url=", driver.current_url, "title=", driver.title)
    sels = [
        # XPath corrigé avec guillemets doubles pour gérer le ' de S'inscrire
        (By.XPATH, '//a[normalize-space()="Se connecter / S\'inscrire"]'),
        (By.XPATH, "//a[normalize-space()='Sign in']"),
        (By.CSS_SELECTOR, "a[href*='/auth/login']"),
        (By.CSS_SELECTOR, "button[type='submit']"),
    ]
    for by, sel in sels:
        els = driver.find_elements(by, sel)
        print(f"[DOM] {sel} -> {len(els)}")

    # Cookie banner ?
    cookies = driver.find_elements(
        By.CSS_SELECTOR, "#onetrust-accept-btn-handler, .cookie-accept"
    )
    print("[DOM] cookies=", len(cookies))

def is_session_expired(driver) -> bool:
    """
    Détecte une expiration de session / mot de passe.
    Doit être appelée AVANT toute logique survey.
    """
    try:
        txt = (driver.page_source or "").lower()

        signals = [
            "session expired",
            "your session has expired",
            "please log in again",
            "mot de passe expiré",
            "reconnectez-vous",
            "password expired",
            "log in again",
        ]

        return any(s in txt for s in signals)
    except Exception:
        return False

def is_proxy_error_page(driver) -> bool:
    """
    Détecte la page d'erreur Chrome ERR_TIMED_OUT indiquant un proxy expiré ou inaccessible.
    """
    try:
        url = driver.current_url or ""
        if "chrome-error://" in url:
            return True
        src = (driver.page_source or "").lower()
        return "err_timed_out" in src
    except Exception:
        return False


def handle_proxy_error_page_if_needed(driver) -> None:
    """
    Si la page courante est une erreur proxy (ERR_TIMED_OUT) :
    - envoie une notification Telegram avec l'account_id du bot
    - déclenche un DAILY_RESET (arrêt container jusqu'au lendemain)
    Ne fait rien si la page est normale.
    """
    if not is_proxy_error_page(driver):
        return

    from Management.guards.runtime_guard import get_guard, StopReason
    from Management.pause_policy import PausePolicy

    guard = get_guard()
    account_id = getattr(guard, "account_id", "unknown")
    msg = f"🔴 Proxy expiré — TopSurveys inaccessible (ERR_TIMED_OUT) | account={account_id}"
    print(msg)
    try:
        guard.notify_fn(msg)
    except Exception:
        pass
    guard.pause(PausePolicy.DAILY_RESET, StopReason.PROXY_EXPIRED)
    raise SystemExit("proxy_expired")  # garde-fou : pause() lève déjà SystemExit


def net_probe():
    try:
        ip_nat = requests.get("https://api.ipify.org", timeout=8).text
    except Exception as e:
        ip_nat = f"ERR_nat:{e}"
    env_proxy = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
    try:
        ip_proxy = requests.get("https://api.ipify.org", timeout=8,
                                proxies={'http': env_proxy, 'https': env_proxy}).text if env_proxy else "no-proxy"
    except Exception as e:
        ip_proxy = f"ERR_proxy:{e}"
    try:
        r = requests.get("https://www.topsurveys.app", timeout=10)
        http = f"{r.status_code} len={len(r.text)}"
    except Exception as e:
        http = f"ERR_http:{e}"
    print(f"[NET] ip_nat={ip_nat} ip_proxy={ip_proxy} topsurveys={http}")

def snap(driver, label: str = "state"):
    """
    Capture un screenshot + dump base64.
    ⚠️ Ne s'exécute que sur AWS pour éviter de spammer la console en local.
    """
    if not _is_prod_env():
        return

    try:
        png = driver.get_screenshot_as_png()
        path = f"/tmp/{label}.png"
        with open(path, "wb") as f:
            f.write(png)
        b64 = base64.b64encode(png).decode()
        print(f"[SNAP] saved {path}")
        print(f"data:image/png;base64,{b64}", flush=True)
    except Exception as e:
        print("[SNAP][ERROR]", e)


def wait_for_vue_hydration(driver, timeout=15):
    start = time.time()
    while time.time() - start < timeout:
        try:
            ready = driver.execute_script(
                "return !!(window.__nuxt && window.__nuxt.isHydrating === false)"
            )
            if ready:
                return True
        except JavascriptException:
            pass
        time.sleep(0.5)
    print("[LOGIN][WARN] Vue hydration timeout — on continue quand même")
    return False


def login(driver, email, password):
    wait = WebDriverWait(driver, 20)

    print("[DEBUG][DRIVER] type=", type(driver))
    print("[DEBUG][DRIVER] has execute_script=", hasattr(driver, "execute_script"))
    print("[DEBUG][DRIVER] url=", getattr(driver, "current_url", None))

    def js_click(driver, el):
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            time.sleep(0.2)
            driver.execute_script("arguments[0].click();", el)
            return True
        except :
            print("[JS_CLICK][ERR]")
            return False

    # --- DEBUG: snapshot HTML complet + extraction éventuelle du code d'erreur ---
    try:
        html = driver.page_source
        path = "/tmp/topsurveys_initial.html"
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)

        snippet = html[:800].replace("\n", " ")
        print(f"[HTML_DEBUG] Saved initial HTML to {path}, len={len(html)}")
        print(f"[HTML_DEBUG] Snippet: {snippet}")

        # Essayer d'extraire un code du type ERR_XXXX
        m = re.search(r"ERR_[A-Z0-9_]+", html)
        if m:
            print(f"[HTML_DEBUG] Chrome error code detected: {m.group(0)}")
        else:
            print("[HTML_DEBUG] No ERR_* code found in HTML.")

    except Exception as e:
        print(f"[HTML_DEBUG][ERROR] {type(e).__name__}: {e}")

    # suite normale
    net_probe()
    dom_probe(driver)
    wait_for_vue_hydration(driver, timeout=15)

    # --- Étape 1 : Saisir l'email dans le champ inline (landing page, pas de modale)
    try:
        email_input = wait.until(
            EC.element_to_be_clickable((
                By.CSS_SELECTOR, "input[data-test-id='check-email-field-input']"
            ))
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", email_input)

        email_input.click()
        time.sleep(0.5)
        email_input.clear()
        email_input.send_keys(email)
        # Le champ email est pré-rempli côté SSR (attribut value dans le HTML Nuxt).
        # clear() + send_keys() met à jour la propriété DOM .value mais ne dispatche
        # aucun événement — Vue ne notifie jamais son v-model et la validation échoue
        # silencieusement. On force les événements réactifs attendus par Vue.
        driver.execute_script("""
            arguments[0].dispatchEvent(new Event('input',  { bubbles: true }));
            arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
        """, email_input)
        time.sleep(3 if _PLM else 0.5)
        print(f"[LOGIN] Email saisi : {email}")

        continue_btn = wait.until(
            EC.element_to_be_clickable((
                By.CSS_SELECTOR, "button[data-test-id='check-email-continue-button']"
            ))
        )
        # Clic natif Selenium (isTrusted: true) — le clic JS synthétique
        # (isTrusted: false) ne déclenchait pas le handler @submit Vue en prod headless.
        continue_btn.click()
        print("[LOGIN] Bouton Continue cliqué.")
        snap(driver, "after_continue_click")
        time.sleep(10 if _PLM else 2)

    except Exception as e:
        print("[LOGIN] Echec injection e-mail :", type(e).__name__, "-", e)
        with open("debug_email_page.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        return

    # Attente que le champ password soit présent dans le DOM (authStep == sign_in).
    # On utilise presence_of_element_located : après un clic natif, Vue déclenche la
    # transition asynchrone vers sign_in ; le champ peut apparaître dans le DOM avant
    # que Selenium le considère "clickable" (enabled + visible), ce qui ferait expirer
    # element_to_be_clickable sur des machines lentes. La présence DOM suffit comme
    # signal que la modale est prête ; le scrollIntoView + sleep suivants absorbent
    # le délai de rendu résiduel avant toute interaction.
    try:
        wait_pwd = WebDriverWait(driver, 60)
        pwd_input = wait_pwd.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, 'input[data-test-id="sign-in-password-field-input"]')
        ))
        # dom_probe(driver)
        snap(driver, "after_email")
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", pwd_input)

        driver.execute_script("""
            arguments[0].value = arguments[1];
            arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
            arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
        """, pwd_input, password)
        time.sleep(3 if _PLM else 0.5)

        if pwd_input.get_attribute("value").strip() == "":
            pwd_input.clear()
            pwd_input.send_keys(password)
            snap(driver, "after_pwd_fallback")
            print("🔁 Fallback : mot de passe injecté via send_keys()")
        else:
            print("🔑 Mot de passe injecté via JS.")
            snap(driver, "after_pwd_js")
            time.sleep(5 if _PLM else 1)  # petit délai pour que Vue traite les événements et active le bouton
            
        # ✅ Corrigé ici : bouton Se connecter avec data-test-id
        login_btn = wait.until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, 'button[data-test-id="sign-in-submit-button"]')
        ))
        driver.execute_script("arguments[0].click();", login_btn)
        time.sleep(3 if _PLM else 0.5)
        print("✅ Bouton « Se connecter » cliqué.")
        from Management.redirect_watcher import wait_for_page_load
        wait_for_page_load(driver, timeout=30)

    except Exception as e:
        snap(driver, "error_pwd_step")
        time.sleep(10 if _PLM else 2)
        print("🛑 Exception mot de passe :", type(e).__name__, "-", e, flush=True)
