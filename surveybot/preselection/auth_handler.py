import time, os, requests, base64, re
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

def _is_aws_env() -> bool:
    """
    Retourne True si on tourne dans un environnement AWS (ECS/Lambda, etc.).
    On se base sur les variables d'environnement standard + override RUN_ENV='aws'.
    """
    return bool(
        os.getenv("AWS_EXECUTION_ENV")                     # Lambda / ECS
        or os.getenv("ECS_CONTAINER_METADATA_URI")         # ECS v3
        or os.getenv("ECS_CONTAINER_METADATA_URI_V4")      # ECS v4
        or os.getenv("RUN_ENV") == "aws"                   # override manuel
    )

def dom_probe(driver):
    """
    Petit dump DOM pour debug.
    ⚠️ Ne s'exécute que lorsque l'on tourne sur AWS (ECS, etc.).
    En local, on retourne immédiatement pour éviter les erreurs XPath et le bruit.
    """
    if not _is_aws_env():
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
    if not _is_aws_env():
        return

    try:
        png = driver.get_screenshot_as_png()
        path = f"/tmp/{label}.png"
        with open(path, "wb") as f:
            f.write(png)
        b64 = base64.b64encode(png).decode()
        print(f"[SNAP] saved {path}")
        print(f"data:image/png;base64,{b64}")
    except Exception as e:
        print("[SNAP][ERROR]", e)


import re  # en haut du fichier si ce n'est pas déjà fait

def login(driver, email, password):
    wait = WebDriverWait(driver, 20)

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

    # --- Étape 1 : ouvrir la modale "Se connecter" de façon robuste
    try:
        selectors = [
            # Bouton texte "Se connecter"
            (By.XPATH, "//button[contains(normalize-space(), 'Se connecter')]"),
            # FR actuel TopSurveys
            (By.XPATH, '//a[normalize-space()="Se connecter / S\'inscrire"]'),
            (By.XPATH, '//a[normalize-space()="Login / Sign up"]'),
            # Variante anglaise éventuelle
            (By.XPATH, "//a[normalize-space()='Sign up']"),
            (By.XPATH, "//a[normalize-space()='Login']"),
            # Lien direct vers la page login
            (By.CSS_SELECTOR, "a[href*='/auth/login']"),
            (By.XPATH, "//button[contains(normalize-space(), 'Login')]"),
            (By.XPATH, "//button[contains(normalize-space(), 'Sign up')]"),
            # Bouton texte "Sign in"
            (
                By.XPATH,
                "//button[contains(translate(normalize-space(), 'SIGNUP', 'signup'), 'signup')]",
            ),
        ]

        login_btn = None
        for by, sel in selectors:
            try:
                login_btn = wait.until(EC.element_to_be_clickable((by, sel)))
                print(f"[LOGIN] Bouton trouvé via sélecteur : {sel}")
                break
            except TimeoutException:
                continue

        if not login_btn:
            print("❌ Aucun CTA de connexion trouvé (FR/EN).")
            dom_probe(driver)
            return

        driver.execute_script("arguments[0].click();", login_btn)
        print("✅ Bouton de connexion cliqué.")

        # 🔄 Attente que la modale soit bien visible
        wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, 'button[class*="auth-action-button"]')
            )
        )
        print("🟢 Modale de connexion détectée.")

    except Exception as err:
        print("❌ Étape 1 (ouverture modale) échouée :", type(err).__name__, "-", err)
        return

    dom_probe(driver)

    # Étape 2 : Remplir l’e-mail
    try:
        email_input = wait.until(
            EC.element_to_be_clickable((
                # By.XPATH, "//input[contains(@placeholder, 'email') or contains(@autocomplete, 'username')]"
                By.CSS_SELECTOR, "input[data-test-id='check-email-field-input']"
            ))
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", email_input)
        time.sleep(0.3)

        email_input.clear()
        email_input.send_keys(email)
        print(f"✅ Email saisi : {email}")

        time.sleep(2)  # petit délai pour stabilité

        # Cliquer sur le bouton "Continue"
        continue_btn = wait.until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Continue')]"))
        )
        driver.execute_script("arguments[0].click();", continue_btn)
        print("✅ Bouton « Continue » cliqué.")

    except Exception as e:
        print("❌ Échec injection e-mail :", type(e).__name__, "-", e)
        with open("debug_email_page.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)

    time.sleep(10)  # attendre le chargement de la page suivante
    dom_probe(driver)
    snap(driver, "after_email")
    # Étape 3 : Remplir le mot de passe et valider
    try:
        pwd_input = wait.until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, 'input[data-test-id="sign-in-password-field-input"]')
        ))
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", pwd_input)
        time.sleep(0.3)

        driver.execute_script("""
            arguments[0].value = arguments[1];
            arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
            arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
        """, pwd_input, password)

        if pwd_input.get_attribute("value").strip() == "":
            pwd_input.clear()
            pwd_input.send_keys(password)
            snap(driver, "after_pwd_fallback")
            print("🔁 Fallback : mot de passe injecté via send_keys()")
        else:
            print("🔑 Mot de passe injecté via JS.")
            snap(driver, "after_pwd_js")
            
        time.sleep(2)  # petit délai pour stabilité
        # ✅ Corrigé ici : bouton Se connecter avec data-test-id
        login_btn = wait.until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, 'button[data-test-id="sign-in-submit-button"]')
        ))
        driver.execute_script("arguments[0].click();", login_btn)
        print("✅ Bouton « Se connecter » cliqué.")

    except Exception as e:
        print("🛑 Exception mot de passe :", type(e).__name__, "-", e)
        #with open("debug_pwd_page.html", "w", encoding="utf-8") as f:
        #    f.write(driver.page_source)

