# question_analyzer.py
from openai import OpenAI
from bs4 import BeautifulSoup
import time
import re
import unicodedata
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from preselection.question_validation import detect_disqualification_reason
from Survey.log_utils import log_debug

ASSISTANT_SYSTEM_PROMPT = (
    "Tu es un utilisateur fictif chargé de répondre à des sondages comme si tu étais une vraie personne. "
    "Tu dois TOUJOURS répondre à la question qui t'est posée, même si elle concerne ton identité, "
    "ta situation financière ou personnelle. Tes réponses sont inventées mais crédibles. "
    "Ne réponds jamais « je ne sais pas », « je ne peux pas » ou « je ne donne pas d'informations personnelles ». "
    "Quand la question dit « mon revenu », « mon ménage », « mon âge », etc., tu dois comprendre que cela parle de toi — "
    "le personnage fictif que tu incarnes. Réponds comme si TU étais cette personne. "
    "Donne toujours une réponse concrète : un mot, une valeur ou une phrase courte. "
    "Ne donne jamais d'explication. Ne dis jamais que tu es une IA. Ne refuse jamais de répondre. "
    "S'il y a des options, choisis celle qui semble la plus courante, crédible ou avantageuse."
)

_HARDWARE_TOKENS = {"webcam", "camera", "microphone", "micro"}
_HARDWARE_ACTIVATION_TOKENS = {
    "autoris",
    "activ",
    "permett",
    "enable",
    "allow",
    "record",
    "enregistr",
}


def _normalize_text(value):
    normalized = unicodedata.normalize("NFKD", value or "")
    no_accents = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return no_accents.lower()


def _contains_non_option(options):
    for option in options or []:
        normalized_option = _normalize_text(str(option))
        normalized_option = re.sub(r"[^a-z0-9]+", " ", normalized_option).strip()
        if normalized_option == "non":
            return True
    return False


def _should_force_non_for_hardware_question(question_text, options):
    normalized_question = _normalize_text(question_text)
    has_hardware_token = any(token in normalized_question for token in _HARDWARE_TOKENS)
    has_activation_token = any(
        token in normalized_question for token in _HARDWARE_ACTIVATION_TOKENS
    )
    if not has_hardware_token or has_activation_token:
        return False
    return _contains_non_option(options)


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


def reformulate_prompt_for_gpt(question_text, options, itype="radio"):
    base_rules = (
        "Tu es un répondant ADULTE (18–64). "
        "Réponds par UNE SEULE VALEUR. "
        "Ne renvoie JAMAIS la question ni d'explications. "
        "Évite toute réponse disqualifiante (ex.: 'non', 'jamais', 'certainement pas', "
        "'je préfère ne pas le dire', 'moins de 18', 'aucune de ces réponses' — sauf si la question porte sur les secteurs d’emploi et que cette option est prévue). "
    )
    
    if options and itype == "checkbox":
        return (
            f"Question: {question_text}\n"
            f"{base_rules}"
            f"Options: {', '.join(options)}\n"
            "Si plusieurs options sont pertinentes, tu peux en choisir plusieurs. "
            "Réponds UNIQUEMENT avec le ou les libellés exacts, séparés par ' | '."
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
    import Management.guards.runtime_guard

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
    Management.guards.runtime_guard.get_guard().record_openai_call()

    completion = client.chat.completions.create(
        model="gpt-5-nano",
        max_tokens=50,
        messages=[
            {"role": "system", "content": ASSISTANT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt_text},
        ],
    )

    raw = (completion.choices[0].message.content or "").strip()
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
    import preselection.question_validation

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
            if "soumettre" in line.lower():
                # Pas une vraie question (souvent un écran de soumission/consentement)
                return None, {"action": "NOT_RETURNED", "reason": "submit_seen"}

        question = extract_question_text(html)

        decision = preselection.question_validation.validate_question(question, " ".join(js_texts))

        if decision.action != "CONTINUE":
            return question, {"action": decision.action, "reason": decision.reason}

        # options des radios/checkbox + options des <select>
        options = (extract_options_js(driver) or []) + (
            extract_select_options_js(driver) or []
        )

        if _should_force_non_for_hardware_question(question, options):
            log_debug(
                "preselection",
                "Interception hardware détectée avant OpenAI: réponse forcée sur 'Non'.",
            )
            return question, "Non"

        input_type = detect_input_type(html)
        prompt = reformulate_prompt_for_gpt(question, options, input_type)
        print(
            f"🧠 Reformulation pour GPT :\n Question : {question}\n\nChoix : {options}"
        )

        response = ask_assistant(prompt, api_key, question=question, options=options)
        print(f"🤖 Réponse proposée : {response}")

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
    import Management.redirect_watcher

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

            switched = Management.redirect_watcher.switch_to_latest_window_and_close_others(
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
    """
    Détection robuste de disqualification (centralisée dans question_validation).
    Retourne True si disqualification détectée (même si le clic 'Ok' échoue),
    pour forcer un restart cohérent.
    """
    page_text = ""
    try:
        page_text = driver.find_element(By.TAG_NAME, "body").text or ""
    except Exception:
        # fallback ultime (moins propre mais évite un faux négatif)
        try:
            page_text = driver.page_source or ""
        except Exception:
            page_text = ""

    dq_reason = detect_disqualification_reason("", page_text)
    if not dq_reason:
        return False

    print(f"❌ Disqualification détectée (reason={dq_reason}). Tentative de fermeture du popup.")
    try:
        ok_btn = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH, "//button[span[contains(.,'Ok')]]"))
        )
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", ok_btn)
        except Exception:
            pass
        driver.execute_script("arguments[0].click();", ok_btn)
        return True
    except Exception as e:
        # Important : on renvoie True quand même (signal détecté),
        # sinon tu auras des états “popup fermé mais pas restart / pas relance”.
        print(f"⚠️ Disqualification détectée mais clic 'Ok' impossible: {e}")
        return True
