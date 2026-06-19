# question_analyzer.py
from openai import OpenAI
from bs4 import BeautifulSoup
import time, re, unicodedata
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from preselection.question_validation import detect_disqualification_reason
from Survey.log_utils import log_debug, log_info

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

    # Règle absolue sur les options exclusives négatives
    "RÈGLE ABSOLUE — OPTIONS EXCLUSIVES : "
    "Ne choisis JAMAIS une option exclusive négative, quelle que soit la question. "
    "Sont considérées comme options exclusives négatives : "
    "'Aucune de ces propositions', 'Aucun', 'Aucune', 'None of the above', 'None', "
    "'Non', 'Jamais', 'Je ne sais pas', 'NSP', 'N/A', 'Non applicable', "
    "'Préfère ne pas répondre', 'Je préfère ne pas répondre', 'Don't know', "
    "'Not applicable', 'Pas concerné', ou tout équivalent sémantique indiquant "
    "une absence, un refus ou une non-appartenance. "
    "Cette règle s'applique sans exception, indépendamment du type de question "
    "(participation à une étude, secteur d'activité, ou toute autre catégorie). "
    "Choisis toujours une option substantielle parmi celles disponibles.\n\n"

    # Règle possession de biens matériels
    "RÈGLE POSSESSION DE BIENS : "
    "Pour toute question portant sur la détention, l'accès ou l'usage d'un bien matériel "
    "(signaux : 'voiture', 'véhicule', 'logement', 'appartement', 'maison', 'résidence', "
    "'équipement', 'appareil', 'télévision', 'téléphone', 'ordinateur', 'abonnement', "
    "ou toute question où les options présentent une hiérarchie possession > accès > location > absence), "
    "choisis TOUJOURS l'option qui affirme la possession directe du bien. "
    "Cette option peut être libellée 'Je possède', 'Propriétaire', 'J'en ai un', 'Oui, j'ai', "
    "ou tout équivalent sémantique indiquant une possession personnelle directe. "
    "Ne jamais choisir 'J'ai accès', 'Je loue', 'J'emprunte', 'Non, pas d'accès', ou toute option "
    "indiquant une possession indirecte ou une absence de possession. "
    "Un répondant déclarant ne pas posséder un bien est souvent exclu des études portant sur ce bien."
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


_NEGATIVE_EXCLUSIVE_EXACT = frozenset({
    "aucun", "aucune", "none", "jamais", "nsp", "n a",
})

_NEGATIVE_EXCLUSIVE_SUBSTRINGS = (
    "aucune de ces",
    "aucune des",
    "aucun de ces",
    "aucun des",
    "none of the",
    "non applicable",
    "not applicable",
    "je ne sais pas",
    "dont know",
    "prefere ne pas repondre",
    "pas concerne",
)


def _is_negative_exclusive_option(option_text):
    norm = re.sub(r"[^a-z0-9]+", " ", _normalize_text(str(option_text))).strip()
    if norm in _NEGATIVE_EXCLUSIVE_EXACT:
        return True
    return any(sub in norm for sub in _NEGATIVE_EXCLUSIVE_SUBSTRINGS)


# Contextes sémantiques où une option exclusive négative peut être la réponse correcte
# selon le persona du bot (ex : sans enfants, sans voiture de fonction).
# Chaque tuple regroupe les tokens d'une même catégorie.
# À étendre si de nouveaux contextes "persona-négatif" sont identifiés.
_EXCLUSIVE_ALLOWED_CONTEXTS = (
    # Questions sur les enfants / situation parentale
    ("enfant", "enfants", "children", "child", "kids", "naissance", "fils", "fille"),
    # Questions sur les véhicules de fonction
    ("véhicule de fonction", "voiture de société", "company car", "fleet"),
)


def _question_allows_exclusive(question_text: str) -> bool:
    """
    Retourne True si la question appartient à un contexte où l'option exclusive
    négative est la réponse correcte selon le persona (ex : 'Je n'ai pas d'enfants').
    Dans ce cas, le pré-filtrage est désactivé pour laisser le prompt GPT décider.
    """
    norm = _normalize_text(question_text)
    for tokens in _EXCLUSIVE_ALLOWED_CONTEXTS:
        if any(t in norm for t in tokens):
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

    # Pattern async-answer : champ texte de recherche + radios filtrés dynamiquement.
    # Ce pattern est prioritaire sur le radio classique car le flow d'interaction est différent :
    # (1) saisir dans le champ texte → (2) attendre les options filtrées → (3) cliquer le radio.
    # Deux signaux DOM discriminants : conteneur .async-answer ET input texte de recherche.
    async_container = soup.select_one(".async-answer, [data-v-12059ec2]")
    async_search_input = soup.select_one(
        "[data-test-id='ps-async-answer-input-input'], "
        "[data-test-id='ps-async-answer-input'] input"
    )
    if async_container and async_search_input:
        return "async_radio"

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

    # Champ numérique entier (input_int)
    if soup.find("input", {"type": "number"}) or soup.select("[data-test-id*='input_int']"):
        return "input_int"

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
            .filter(text => text.length > 0);
        """

        options = driver.execute_script(js_code)
        # Pas de choix → souvent page de blocage ou de consentement non mappée
        if not options:
            print("⏭️ Aucun choix détecté — pas d'action sur cette page. source: reponse_executor.py")
            return False
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
        # dédoublonne en conservant l'ordre
        return list(dict.fromkeys(opts))
    except Exception as e:
        print("💥 JS select options échouée :", e)
        return []


def reformulate_prompt_for_gpt(question_text, options, itype="radio", *, avoid_options=None):
    # Pré-filtrage conditionnel : on retire les options exclusives négatives sauf si
    # la question appartient à un contexte où elles sont la bonne réponse selon le
    # persona (ex : "Je n'ai pas d'enfants"). Dans ce cas, on laisse passer toutes
    # les options et le prompt GPT spécialisé prend la décision.
    filtered_options = options
    if options:
        if _question_allows_exclusive(question_text):
            # Contexte whitelist : toutes les options conservées, GPT décide
            filtered_options = options
            log_debug("preselection", "[PRE-FILTRAGE GPT] Contexte exclusif autorisé — options conservées telles quelles.")
        else:
            filtered_options = [o for o in options if not _is_negative_exclusive_option(o)]
            removed = [o for o in options if _is_negative_exclusive_option(o)]
            if removed:
                log_debug("preselection", f"[PRE-FILTRAGE GPT] Options exclusives négatives retirées : {removed}")

    base_rules = (
        "Ne renvoie jamais la question ni d'explications. "
        "Évite toute option exprimant une absence, un refus ou une non-appartenance. "
        "Pour les questions de revenu personnel ou du foyer : choisis une tranche entre 100 000 EUR et 1 000 000 EUR. "
        "Ne choisis jamais une tranche supérieure à 300 millions EUR. "
        "Pour les questions de statut professionnel : préfère 'Cadre supérieur', 'Dirigeant', 'Indépendant'. "
        "Pour les questions de secteur d'activité : préfère 'Finance', 'Conseil', 'Tech', 'Santé', 'Direction'. "
        "RÈGLE NOMBRE D'EMPLOYÉS : si la question porte sur l'effectif ou la taille d'une entreprise "
        "(signaux : 'combien d\\'employés', 'emploie environ', 'nombre d\\'employés', 'how many employees', 'company size', 'taille de l\\'entreprise', 'effectif'), "
        "choisis TOUJOURS la valeur la plus grande disponible dans la liste (ex : '50 000+', '10 000 et plus', etc.). "
        "Cette règle prime sur la RÈGLE FRÉQUENCE/QUANTITÉ pour ce cas précis. "
        "RÈGLE FRÉQUENCE/QUANTITÉ : si la question porte sur une fréquence, une durée, une quantité ou un volume d'usage ou de pratique "
        "(signaux : 'combien d\\'heures', 'combien de minutes', 'combien de fois', 'à quelle fréquence', 'combien de temps', 'how often', 'how many hours'), "
        "choisis systématiquement la deuxieme option la plus élevée disponible dans la liste. "
        "EXCEPTION screener participation récente (étude de marché / sondage récent) : "
        "si la liste contient 'Aucune de ces propositions' ou équivalent, choisis-la exclusivement. "
        "Cette exception prime sur la RÈGLE FRÉQUENCE/QUANTITÉ. "

        "RÈGLE ABSOLUE — OPTIONS EXCLUSIVES : "
        "Ne choisis JAMAIS une option exclusive négative, quelle que soit la question. "
        "Sont considérées comme options exclusives négatives(lite NON exhaustive) : "
        "'Aucune de ces propositions', 'Aucun', 'Aucune', 'None of the above', 'None', "
        "'Non', 'Jamais', 'Je ne sais pas', 'NSP', 'N/A', 'Non applicable', "
        "'Préfère ne pas répondre', 'Je préfère ne pas répondre', 'Don't know', "
        "'Not applicable', 'Pas concerné', ou tout équivalent sémantique indiquant "
        "une absence, un refus ou une non-appartenance. "
        "Cette règle s'applique sans exception, indépendamment du type de question "
        "(participation à une étude, secteur d'activité, ou toute autre catégorie). "
        "Choisis toujours une option substantielle parmi celles disponibles.\n\n"

    )

    avoid_section = ""
    if avoid_options:
        avoid_section = (
            "ATTENTION — OPTIONS À ÉVITER IMPÉRATIVEMENT : les options suivantes ont déjà été choisies "
            "lors de tentatives précédentes et ont conduit à une disqualification. "
            f"Ne les choisis SOUS AUCUN PRÉTEXTE : {', '.join(repr(o) for o in avoid_options)}\n"
            "Choisis obligatoirement une option différente parmi celles disponibles.\n\n"
        )

    if filtered_options and itype == "checkbox":
        return (
            f"Question: {question_text}\n"
            f"{base_rules}"
            f"{avoid_section}"
            f"Options: {', '.join(filtered_options)}\n"
            "Réponds UNIQUEMENT avec le ou les libellés exacts, séparés par ‘ | ‘. "
            "Pour une checkbox non exclusive, préfère plusieurs choix plutôt qu’un seul."
            "Pour les questions à choix multiples (checkbox) :"
            "- Par défaut, sélectionne plusieurs options cohérentes avec le profil, pas une seule."
            "- Sauf si la question implique clairement une réponse exclusive ou un nombre limité évident (ex: année de naissance, âge exact, nombre exact, situation familiale exclusive, réponse négative exclusive, ‘aucune de ces propositions’, ‘je n’ai pas d’enfants’, ‘je préfère ne pas le dire’, ‘aucun’, ‘autre’)."
            "- Par défaut, renvoie entre 5 et 7 options plausibles et variées parmi celles proposées, sauf si une exception exclusive s’applique."
            "- Si la liste contient moins de 5 options, choisis uniquement l’option la plus plausible parmi celles non-disqualifiantes. Ne tente pas d’atteindre 5 à 7 dans ce cas."
            "- Ne combine jamais une option exclusive avec d’autres."
            "- Si la question concerne les enfants, le foyer parental, ou l’année de naissance d’enfants : si une option exclusive négative est disponible (‘je n’ai pas d’enfants’, ‘sans enfants’, ‘aucun enfant’, ‘none’, ‘no children’ ou équivalent), choisis-la uniquement. Sinon, ignore cette règle et sélectionne des réponses cohérentes parmi les options proposées."
            "- Si plusieurs réponses sont renvoyées, utilise exactement le séparateur ‘ | ‘ entre les libellés."
        )

    if filtered_options:
        return (
            f"Question: {question_text}\n"
            f"{base_rules}"
            f"{avoid_section}"
            f"Options: {', '.join(filtered_options)}\n"
            "Choisis exactement une des options ci-dessus. "
            "Réponds UNIQUEMENT par le libellé de l'option."
        )

    if itype == "input_int":
        return (
            f"Question: {question_text}\n"
            f"{base_rules}"
            f"{avoid_section}"
            "Réponds UNIQUEMENT par un entier brut (chiffres seuls, sans symbole monétaire, "
            "sans espace, sans texte). Exemple de format attendu : 150000"
        )

    return (
        f"Question: {question_text}\n"
        f"{base_rules}"
        f"{avoid_section}"
        "Réponds par une valeur courte et réaliste. Une seule valeur."
    )

def ask_assistant(prompt_text, api_key, *, question=None, options=None):
    import Management.guards.runtime_guard

    client = OpenAI(api_key=api_key)
    Management.guards.runtime_guard.get_guard().record_openai_call()

    completion = client.chat.completions.create(
        model="gpt-4.1-mini",
        max_completion_tokens=5000,
        messages=[
            {"role": "system", "content": ASSISTANT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt_text},
        ],
    )

    raw = (completion.choices[0].message.content or "").strip()
    cleaned = raw.split("\n")[0].strip(" .,-–—•*➡️✅🤖⭐")

    return cleaned

def get_response_for_question(driver, api_key, *, session=None):
    import preselection.question_validation

    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )

        html = extract_popup_html(driver)
        input_type = detect_input_type(html)
        js_texts = extract_popup_text_with_js(driver)
        # Détection qualification : si le texte contient "qualifié", on sort immédiatement
        for line in js_texts:
            if re.search(r"tu\s+t.es\s+qualifi", line.lower()):
                print(
                    "🎯 Message de qualification détecté : sortie de boucle autorisée."
                )
                return None, None, None
            if "soumettre" in line.lower():
                # Pas une vraie question (souvent un écran de soumission/consentement)
                return None, {"action": "NOT_RETURNED", "reason": "submit_seen"}, None

        question = extract_question_text(html)
        log_info("[PRESELECTION]", f"Question extraite : {question}")

        decision = preselection.question_validation.validate_question(question, " ".join(js_texts))

        if decision.action != "CONTINUE":
            return question, {"action": decision.action, "reason": decision.reason}, None

        # options des radios/checkbox + options des <select>
        options = (extract_options_js(driver) or []) + (
            extract_select_options_js(driver) or []
        )

        # Dérive la clé question + initialise survey_key sur la première question
        _mem_qk = None
        if session is not None and options:
            try:
                from State.survey_memory import make_key
                _mem_qk = make_key(question, options)
                session.set_survey_key_if_first(_mem_qk)
            except Exception as _ke:
                log_debug("preselection", f"[SURVEY_MEMORY] key error: {_ke}")

        if _should_force_non_for_hardware_question(question, options):
            log_debug(
                "preselection",
                "Interception hardware détectée avant OpenAI: réponse forcée sur 'Non'.",
            )
            if _mem_qk is not None:
                try:
                    session.record_choice(_mem_qk, ["Non"])
                except Exception:
                    pass
            return question, "Non", input_type

        # Lecture mémoire : bypass GPT ou injection d'options à éviter
        avoid_options = []
        if _mem_qk is not None and session.survey_key:
            try:
                from State.survey_memory import read_guidance
                guidance = read_guidance(session.survey_key, _mem_qk, session.current_page_index)
                if guidance.use_options:
                    if input_type == "checkbox":
                        response = " | ".join(guidance.use_options)
                    else:
                        response = guidance.use_options[0] if guidance.use_options else None
                    if response:
                        session.record_choice(_mem_qk, guidance.use_options)
                        log_info("[PRESELECTION]", f"[MEM] Bypass GPT → {response}")
                        print(f"🧠 Réponse mémoire : {response}")
                        return question, response, input_type
                avoid_options = guidance.avoid_options
            except Exception as _ge:
                log_debug("preselection", f"[SURVEY_MEMORY] guidance error: {_ge}")

        prompt = reformulate_prompt_for_gpt(question, options, input_type, avoid_options=avoid_options)
        log_debug("preselection", f"[ITYPE DÉTECTÉ] {input_type}")
        log_debug("preselection", f"[PROMPT→GPT] Q: {question!r} | Options: {options}")

        response = ask_assistant(prompt, api_key, question=question, options=options)
        print(f"🤖 Réponse proposée : {response}")

        # Enregistre le choix dans la session locale
        if _mem_qk is not None:
            try:
                chosen = [r.strip() for r in response.split(" | ")] if " | " in response else [response]
                session.record_choice(_mem_qk, chosen)
            except Exception:
                pass

        return question, response, input_type

    except Exception as e:
        print("❌ Erreur dans get_response_for_question :", e)
        return None, None, None


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
            from Management.redirect_watcher import wait_for_page_load
            wait_for_page_load(driver, timeout=30)
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
            # 1b. Attendre que le bouton Participer soit réellement visible dans le DOM
            # (le message de qualification peut apparaître avant que le bouton soit rendu)
            wait = WebDriverWait(driver, 30)
            btn = wait.until(
                EC.visibility_of_element_located(
                    (By.CSS_SELECTOR, 'button[data-test-id="ps-common-actions-button"]')
                )
            )
            # S'assurer également que le bouton est cliquable (pas disabled)
            wait.until(
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
            time.sleep(0.5)
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