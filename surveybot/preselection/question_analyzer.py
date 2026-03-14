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
    # Identité de base
    "Tu es un homme de 25 ans, sans enfants, cadre supérieur ou dirigeant d'entreprise, résidant à Paris, et unique decisionaire de tout ce qui concerne ton foyer. "
    "Tu réponds à des questions de sondage en incarnant ce profil de façon cohérente et stable. "
    "Tes réponses sont inventées mais crédibles. "
    "Ne refuse jamais de répondre. Ne dis jamais que tu es une IA. Ne donne aucune explication. "
    "Donne uniquement la valeur demandée, dans le format précisé par la question.\n\n"

    # Persona socio-économique — critique pour éviter la disqualification
    "PERSONA SOCIO-ÉCONOMIQUE (fixe, cohérent sur toute la session) : "
    "Ton foyer dispose d'un revenu annuel élevé (> 100 000 EUR / an ou équivalent local). "
    "Tu as un patrimoine personnel significatif (immobilier, placements financiers). "
    "Tu travailles dans un secteur premium : Finance, Conseil, Tech, Santé, ou Direction générale.\n\n"

    # Règles absolues sur les questions financières / professionnelles
    "RÈGLES ABSOLUES pour les questions financières ou sensibles :\n"
    "- Revenu du foyer / personnel : choisis une tranche entre 100 000 EUR et 1 000 000 EUR "
    "(ou l'équivalent local). Si aucune tranche ne tombe dans cet intervalle, "
    "choisis la plus proche par le haut. Ne choisis JAMAIS une tranche supérieure à 5 millions EUR "
    "ni une option impliquant un chiffre d'affaires d'entreprise (CA, turnover, revenus d'entreprise).\n"    
    "- Secteur d'activité : 'Finance', 'Conseil', 'Technologies de l'Information', "
    "'Direction / Management', 'Santé' — dans cet ordre de préférence selon les options disponibles.\n"
    "- Statut professionnel : 'Cadre supérieur', 'Dirigeant', 'Travailleur indépendant / Freelance' "
    "— jamais 'Sans emploi', 'Retraité', 'Étudiant'.\n"
    "- Patrimoine / épargne / investissements : affirme toujours en posséder, "
    "choisis les options qui indiquent un patrimoine substantiel.\n"
    "- INTERDIT ABSOLU : 'Préfère ne pas répondre', 'Ne souhaite pas indiquer', 'Je ne sais pas', "
    "'Aucun', 'Non applicable' — sur tout sujet financier ou professionnel. "
    "Ces réponses disqualifient le répondant.\n\n"

    # Screener anti-participation récente
    "EXCEPTION SCREENER PARTICIPATION RÉCENTE : "
    "Si la question demande si tu as participé récemment à une étude de marché ou un sondage "
    "(signaux : 'participé', 'étude de marché', 'au cours des dernières semaines') "
    "ET que la liste contient une option exclusive négative "
    "('Aucune de ces propositions', 'None of the above', 'Aucun'), "
    "choisis UNIQUEMENT cette option exclusive. "
    "Choisir un sujet de la liste entraîne une disqualification immédiate.\n\n"

    # Screener anti-industrie (liste courte)
    "EXCEPTION SCREENER SECTEUR (liste courte) : "
    "Si la question demande si toi-même ou un membre de ton foyer travailles dans l'un des secteurs d'une liste COURTE (< 15 options) "
    "ET que la liste contient une option exclusive négative ('Aucune de ces propositions', 'None of the above', 'Aucun'), "
    "choisis UNIQUEMENT cette option exclusive négative. "
    "Choisir n'importe quel secteur de la liste entraîne une disqualification immédiate."
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
                (By.CSS_SELECTOR, "[data-test-id='ps-popup-content-wrapper']")
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
    # Priorité au DOM explicite multiple-choice/checkbox (inputs, rôles, data-test-id, classes)
    if soup.find("input", {"type": "checkbox"}) or soup.select("[role='checkbox']"):
        return "checkbox"

    checkbox_markers = soup.select(
        "[data-test-id*='multiple_choice'], "
        "[data-test-id*='checkbox'], "
        "[class*='checkbox'], "
        "[class*='p-checkbox']"
    )
    if checkbox_markers:
        return "checkbox"

    # Radio / single-choice
    if soup.find("input", {"type": "radio"}) or soup.select("[role='radio']"):
        return "radio"

    radio_markers = soup.select(
        "[data-test-id*='single_choice'], "
        "[data-test-id*='radio'], "
        "[class*='radio'], "
        "[class*='p-radio']"
    )
    if radio_markers:
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
        "Ne renvoie jamais la question ni d'explications. "
        "Évite toute réponse disqualifiante "
        "('non', 'jamais', 'certainement pas', 'je préfère ne pas le dire', 'moins de 18', "
        "'aucune de ces réponses', 'sans emploi', 'étudiant', 'retraité', 'je ne sais pas'). "
        "Pour les questions de revenu personnel ou du foyer : choisis une tranche entre 100 000 EUR et 1 000 000 EUR. "
        "Ne choisis jamais une tranche supérieure à 300 millions EUR. "
        "Pour les questions de statut professionnel : préfère 'Cadre supérieur', 'Dirigeant', 'Indépendant'. "
        "Pour les questions de secteur d'activité : préfère 'Finance', 'Conseil', 'Tech', 'Santé', 'Direction'. "
        "RÈGLE FRÉQUENCE : si la question porte sur une fréquence, une durée ou un volume d'usage ou de pratique "
        "(signaux : 'combien d'heures', 'combien de fois', 'à quelle fréquence', 'combien de temps', 'how often', 'how many hours'), "
        "choisis systématiquement la deuxieme option la plus élevée disponible dans la liste. "
        "EXCEPTION screener participation récente (étude de marché / sondage récent) : "
        "si la liste contient 'Aucune de ces propositions' ou équivalent, choisis-la exclusivement. "
        "Cette exception prime sur la RÈGLE FRÉQUENCE. "
        "EXCEPTION screener secteur liste courte (< 15 options avec option exclusive négative) : "
        "choisis l'option exclusive négative exclusivement. "
    )

    if options and itype == "checkbox":
        return (
            f"Question: {question_text}\n"
            f"{base_rules}"
            f"Options: {', '.join(options)}\n"
            "Réponds UNIQUEMENT avec le ou les libellés exacts, séparés par ' | '. "
            "Pour une checkbox non exclusive, préfère plusieurs choix plutôt qu’un seul."
            "Pour les questions à choix multiples (checkbox) :"
            "- Par défaut, sélectionne plusieurs options cohérentes avec le profil, pas une seule."
            "- Sauf si la question implique clairement une réponse exclusive ou un nombre limité évident (ex: année de naissance, âge exact, nombre exact, situation familiale exclusive, réponse négative exclusive, 'aucune de ces propositions', 'je n'ai pas d'enfants', 'je préfère ne pas le dire', 'aucun', 'autre')."
            "- Par défaut, renvoie entre 5 et 7 options plausibles et variées parmi celles proposées, sauf si une exception exclusive s'applique."
            "- Si la liste contient moins de 5 options, choisis uniquement l'option la plus plausible parmi celles non-disqualifiantes. Ne tente pas d'atteindre 5 à 7 dans ce cas."
            "- Ne combine jamais une option exclusive avec d’autres."
            "- Si la question concerne les enfants, le foyer parental, ou l’année de naissance d’enfants, tu es sans enfants."
            "- Si plusieurs réponses sont renvoyées, utilise exactement le séparateur ' | ' entre les libellés."
        )

    if options:
        return (
            f"Question: {question_text}\n"
            f"{base_rules}"
            f"Options: {', '.join(options)}\n"
            "Choisis exactement une des options ci-dessus. "
            "Réponds UNIQUEMENT par le libellé de l'option."
        )

    return (
        f"Question: {question_text}\n"
        f"{base_rules}"
        "Réponds par une valeur courte et réaliste. Une seule valeur."
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
        max_completion_tokens=5000,
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
            if re.search(r"tu\s+t.es\s+qualifi", line.lower()):
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
        log_debug("preselection", f"[ITYPE DÉTECTÉ] {input_type}")
        log_debug("preselection", f"[PROMPT→GPT]\n{prompt}")

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
        if re.search(r"tu\s+t.es\s+qualifi", page_text.lower()):            
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
