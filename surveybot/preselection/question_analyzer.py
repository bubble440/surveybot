# question_analyzer.py
from openai import OpenAI
from bs4 import BeautifulSoup
import time
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from Management.url_guard import is_allowed
from Management.guards.runtime_guard import get_guard
from Management.guards.sensitive_question_guard import is_sensitive_question

ASSISTANT_ID = "asst_dzB8sAFrNdPPD17auG4WI0EK"

def _is_block_page(text: str) -> bool:
    """Détecte une page de blocage (VPN/Proxy/Access denied/unusual traffic)."""
    if not text:
        return False
    t = text.lower()
    patterns = [
        "proxy ou vpn détecté",
        "vpn détecté",
        "proxy détecté",
        "proxy or vpn detected",
        "disable your vpn",
        "access denied",
        "we have detected unusual traffic",
        "security policy violation",
        "désactive ton proxy",
    ]
    return any(p in t for p in patterns)

def extract_popup_html(driver):
    try:
        popup = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "div[class*='common-container']")
            )
        )
        html = driver.execute_script("return arguments[0].outerHTML", popup)
        return html
    except Exception as e:
        print("❌ Aucun popup détecté, retour au DOM complet. Détail:", e)
        return driver.execute_script("return document.documentElement.outerHTML")


def extract_question_text(html):
    soup = BeautifulSoup(html, "html.parser")
    candidates = soup.find_all(["h1", "h2", "p", "div", "span"])

    ignore_keywords = ["quelques questions", "je ne peux pas répondre", "qualification"]
    questions = []

    for tag in candidates:
        text = tag.get_text(strip=True)
        if len(text) < 10:
            continue
        if any(kw in text.lower() for kw in ignore_keywords):
            continue
        if "?" in text or len(text.split()) > 5:
            questions.append((tag.name, text))

    if questions:
        for priority in ["h1", "h2", "p", "div", "span"]:
            for tagname, text in questions:
                if tagname == priority:
                    return text

    return "Question non trouvée"


def detect_input_type(html):
    soup = BeautifulSoup(html, "html.parser")
    # radios/checkbox (y compris rôle ARIA)
    if soup.find("input", {"type": "checkbox"}) or soup.select("[role='checkbox']"):
        return "checkbox"
    if soup.find("input", {"type": "radio"}) or soup.select("[role='radio']"):
        return "radio"

    return "radio"


def extract_popup_text_with_js(driver):
    """
    Utilise JavaScript via Selenium pour extraire le texte visible du popup.
    Plus robuste que BeautifulSoup.
    """
    try:
        popup_element = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "div[class*='common-container']")
            )
        )

        js_code = """
            const popup = arguments[0];
            const walker = document.createTreeWalker(popup, NodeFilter.SHOW_TEXT, null, false);
            const texts = [];
            while (walker.nextNode()) {
                const text = walker.currentNode.textContent.trim();
                if (text.length > 4) texts.push(text);
            }
            return [...new Set(texts)];
        """
        result = driver.execute_script(js_code, popup_element)

        print("\n🧠 Texte extrait via JavaScript (DEBUG):")
        print("────────────────────────────────────────────")
        for i, line in enumerate(result, 1):
            print(f"{i}. {line}")
        print("────────────────────────────────────────────")

        return result
    except Exception as e:
        print("❌ JS DOM extraction échouée :", e)
        return []


def extract_options_js(driver):
    """
    Utilise JavaScript pour extraire tous les textes visibles correspondant à des réponses.
    Cible les éléments contenant 'p-radio-text' dans leur class.
    """
    try:
        js_code = """
        return Array.from(document.querySelectorAll("label span"))
            .filter(span => 
                span.className.includes("p-radio-text") ||
                span.className.includes("p-checkbox-text")
            )
            .map(span => span.innerText.trim())
            .filter(text => text.length > 2);
        """

        options = driver.execute_script(js_code)
        # Pas de choix → souvent page de blocage ou de consentement non mappée
        if not options:
            print("⏭️ Aucun choix détecté — pas d'action sur cette page. source: reponse_executor.py")
            return False
        print(f"📋 Choix extraits via JS : {options}")
        return options
    except Exception as e:
        print("💥 JS extraction échouée :", e)
        return []


def extract_select_options_js(driver):
    """
    Retourne les textes visibles des <option> (on ignore les placeholders/disabled).
    """
    try:
        js = """
        return Array.from(document.querySelectorAll('select option'))
            .filter(o => !o.disabled && (o.value || '').trim() !== '' && (o.innerText||'').trim().length > 0)
            .map(o => (o.innerText || o.textContent).trim());
        """
        opts = driver.execute_script(js)
        print(f"extract_select_options_js : {opts}")
        # dédoublonne en conservant l'ordre
        return list(dict.fromkeys(opts))
    except Exception as e:
        print("💥 JS select options échouée :", e)
        return []


def reformulate_prompt_for_gpt(question_text, options):
    base_rules = (
        "Tu es un répondant ADULTE (18–64). "
        "Réponds par UNE SEULE VALEUR. "
        "Ne renvoie JAMAIS la question ni d'explications. "
        "Évite toute réponse disqualifiante (ex.: 'non', 'jamais', 'certainement pas', "
        "'je préfère ne pas le dire', 'moins de 18', 'aucune de ces réponses' — sauf si la question porte sur les secteurs d’emploi et que cette option est prévue). "
    )
    
    if options:
        return (
            f"Question: {question_text}\n"
            f"{base_rules}"
            f"Options: {', '.join(options)}\n"
            "Choisis **exactement une** des options ci-dessus. "
            "Réponds UNIQUEMENT par le libellé de l'option."
        )

    return (
        f"Question: {question_text}\n"
        f"{base_rules}"
        "Donne directement une **valeur logique** et non disqualifiante. "
        "Réponds UNIQUEMENT par la valeur."
    )


def ask_assistant(prompt_text, api_key, *, question=None, options=None):
    from Utils.openai_cache import (
        make_cache_key,
        get_cached_answer,
        store_answer,
    )

    # 1️⃣ Cache lookup (si possible)
    cache_key = None
    if question and options:
        cache_key = make_cache_key(question, options)
        cached = get_cached_answer(cache_key)
        if cached:
            print("⚡ OpenAI cache HIT")
            return cached

    # 2️⃣ Appel OpenAI normal
    client = OpenAI(api_key=api_key)
    get_guard().record_openai_call()

    thread = client.beta.threads.create()
    client.beta.threads.messages.create(
        thread_id=thread.id,
        role="user",
        content=prompt_text
    )

    run = client.beta.threads.runs.create(
        thread_id=thread.id,
        assistant_id=ASSISTANT_ID
    )

    while run.status not in ["completed", "failed"]:
        time.sleep(1)
        run = client.beta.threads.runs.retrieve(
            thread_id=thread.id,
            run_id=run.id
        )

    if run.status != "completed":
        return None

    messages = client.beta.threads.messages.list(thread_id=thread.id)
    raw = messages.data[0].content[0].text.value.strip()
    cleaned = raw.split("\n")[0].strip(" .,-–—•*➡️✅🤖⭐")

    # 3️⃣ Store cache
    if cache_key and cleaned:
        store_answer(
            cache_key,
            cleaned,
            model="gpt-4o-mini-ft"
        )

    return cleaned

def get_response_for_question(driver, api_key):
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )

        html = extract_popup_html(driver)
        js_texts = extract_popup_text_with_js(driver)
        # Détection qualification : si le texte contient "qualifié", on sort immédiatement
        for line in js_texts:
            if "tu t'es qualifié pour cette enquête" in line.lower():
                print(
                    "🎯 Message de qualification détecté : sortie de boucle autorisée."
                )
                return None, None
            if "Soumettre" in line.lower():
                return "NOT_RETURNED", None

        question = extract_question_text(html)

        if is_sensitive_question(question):
            print("⏭️ Question sensible détectée (hardware / permission) → SKIP")

            return {
                "action": "SKIP",
                "reason": "SENSITIVE_QUESTION",
            }

        # options des radios/checkbox + options des <select>
        options = (extract_options_js(driver) or []) + (
            extract_select_options_js(driver) or []
        )

        prompt = reformulate_prompt_for_gpt(question, options)
        # 🛡️ Détection page de blocage (vpn/proxy/denied) → pas de GPT, pas d'action
        page_debug = (question or "") + " " + (driver.page_source or "")
        if _is_block_page(page_debug):
            print("[BLOCK] Page de blocage détectée (vpn/proxy).")
            # 1) tenter un clic 'Recharger/Reload' si présent
            try:
                btn = WebDriverWait(driver, 2).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Recharger') or contains(., 'Reload')]"))
                )
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                time.sleep(0.2)
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(2)
            except Exception:
                # 2) sinon refresh
                try:
                    driver.refresh()
                    time.sleep(2)
                except Exception:
                    pass

        print(
            f"🧠 Reformulation pour GPT :\n Question : {question}\n\nChoix : {options}"
        )

        response = ask_assistant(prompt, api_key, question=question, options=options)
        print(f"🤖 Réponse proposée : {response}")

        def _norm(s):
            return (s or "").strip().lower().replace("’", "'")

        qn = _norm(question)
        rp = _norm(response)

        # si l'IA renvoie la question, un texte avec "?" ou une chaîne vide → on fabrique une valeur sûre
        if not rp or "?" in rp or rp == qn:
            ql = qn
            if any(k in ql for k in ["code postal", "postal", "zip"]):
                response = "95000"  # FR sûr (5 chiffres)
            elif any(k in ql for k in ["âge", "age"]):
                response = "28"
            elif any(k in ql for k in ["année", "naissance"]):
                response = "1996"
            print(f"🛡️ Fallback valeur sûre appliqué : {response}")

        return question, response

    except Exception as e:
        print("❌ Erreur dans get_response_for_question :", e)
        return None, None


def click_participer_if_present(driver):
    try:
        # On attend jusqu'à 7 secondes qu'un bouton "Participer" soit visible
        wait = WebDriverWait(driver, 7)
        participer_btn = wait.until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, 'button[data-test-id="ps-common-actions-button"]')
            )
        )
        if participer_btn:
            print(
                "🚨 Aucun choix détecté : tentative de clic sur le bouton 'Participer'..."
            )
            driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center'});", participer_btn
            )
            driver.execute_script("arguments[0].click();", participer_btn)
            print("✅ Bouton 'Participer' cliqué avec succès.")
            return True
    except Exception as e:
        print("❌ Aucun bouton 'Participer' détecté ou erreur :", e)
    return False


def click_participer_if_qualified(driver):
    try:
        # 1. Vérifie le message de qualification
        page_text = driver.execute_script(
            """
            return Array.from(document.querySelectorAll("span, div, p"))
                .map(e => e.innerText.trim())
                .filter(t => t.length > 5)
                .join(" ")
        """
        )
        if "tu t'es qualifié pour cette enquête" in page_text.lower():
            wait = WebDriverWait(driver, 5)
            btn = wait.until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, 'button[data-test-id="ps-common-actions-button"]')
                )
            )

            # 2. Scroll vers le bouton
            driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center'});", btn
            )
            time.sleep(0.5)

            # 3. Vrai clic utilisateur simulé
            # --- IMPORTANT: snapshot des handles AVANT le clic ---
            base_handles = set(driver.window_handles)

            ActionChains(driver).move_to_element(btn).click().perform()
            print("🖱️ Clic ActionChains simulé sur 'Participer'.")

            from Management.redirect_watcher import switch_to_latest_window_and_close_others

            switched = switch_to_latest_window_and_close_others(
                driver,
                base_handles=base_handles,
                timeout=12,
                prefer_external=True
            )
            print(f"🪟 Switch + close anciens onglets = {switched}")
            # petite pause pour laisser le survey peindre son DOM
            time.sleep(2)

            return True
        else:
            print("❌ Aucun message de qualification détecté.")
            return False
    except Exception as e:
        print("❌ Erreur clic sur Participer :", type(e).__name__, "-", e)
        return False


# ─────────────────────────────────────────────
# Détecter disqualification et cliquer sur "Ok"
# ─────────────────────────────────────────────
def handle_disqualification_and_retry(driver):
    try:
        print("🔍 Vérification disqualification...")
        # Vérifie si le texte du popup contient un message de disqualification
        full_text = driver.execute_script(
            """
            return Array.from(document.querySelectorAll(".p-modal-inner *"))
                        .map(el => el.innerText).join("\\n");
        """
        ).lower()

        if (
            "tu ne t'es pas qualifié cette", "Tu n'as pas été qualifié cette fois" in full_text
            or "malheureusement" in full_text
        ):
            print("❌ Disqualification détectée. Tentative de fermeture du popup.")
            ok_btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//button[span[contains(text(),'Ok')]]")
                )
            )
            driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center'});", ok_btn
            )
            time.sleep(0.3)
            ok_btn.click()
            return True
        return False
    except Exception as e:
        print("💥 Erreur gestion disqualification :", e)
        return False
