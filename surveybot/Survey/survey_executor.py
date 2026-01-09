from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
import re, openai, time, unicodedata

def _norm_lc(s: str) -> str:
    s = unicodedata.normalize("NFKC", (s or "")).lower().strip()
    return re.sub(r"\s+", " ", s)


def _coerce_safe_value_if_questionish(raw_line: str) -> str:
    """
    Si le modèle renvoie par erreur un intitulé de question au lieu d'une valeur,
    fabrique une valeur sûre en fonction du texte.
    Remappe aussi 'number' -> 'text'.
    """
    line = (raw_line or "").strip()
    # parse "label //// type //// contexte" tolérant
    m = re.split(r"/{4,}", line)
    label = (m[0] if m else "").strip()
    itype = (m[1] if len(m) > 1 else "").strip().lower() or "text"
    context = (m[2] if len(m) > 2 else "").strip()

    # forcer number -> text
    if itype == "number":
        itype = "text"


    low = _norm_lc(label)
    is_questiony = ("?" in label) or any(
        k in low
        for k in [
            "quel est",
            "quelle est",
            "what is",
            "how old",
            "postal code",
            "code postal",
            "zip",
            "âge",
            "age",
            "année",
            "naissance",
            "year of birth",
        ]
    )

    if itype in ("text", "textarea") and (is_questiony or not label or len(label) < 2):
        # Heuristiques de valeur sûre
        if any(k in low for k in ["postal", "code postal", "zip"]):
            label = "95000"  # 5 chiffres FR
        elif any(k in low for k in ["âge", "age", "how old"]):
            label = "28"  # adulte ok
        elif any(k in low for k in ["année", "naissance", "year of birth"]):
            label = "1996"
        else:
            # valeur texte par défaut : évite les caractères non numériques si champ num.
            label = "28"

    return f"{label} //// {itype} //// {context}"


# ✍️ Fonction principale
def execute_survey_page(driver, api_key):
    """
    Nouvelle version : capture l’image, demande à GPT-4o quoi faire, puis applique l'action.
    """
    global last_screenshot_path
    previous_screenshot_path = (
        last_screenshot_path if "last_screenshot_path" in globals() else None
    )
    import Management.guards.url_guard
    import Survey.screenshot_analyzer
    import Survey.action_dispatcher 
    import selenium.webdriver.support.ui
    import Survey.dom_analyzer
    import Survey.prompt_builder
    import Survey.batch_response_parser
    import Survey.dom_classifier as dom_classifier
    import Survey.action_dispatcher as action_dispatcher
    import Survey.dom_metrics as dom_metrics

    # ⏳ Attente que le DOM ait fini de charger avant capture
    try:
        selenium.webdriver.support.ui.WebDriverWait(driver, 8).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        selenium.webdriver.support.ui.WebDriverWait(driver, 8).until(
            lambda d: len(d.find_elements(By.CSS_SELECTOR,
                "input, select, textarea, button, [role='button'], [role='radio'], [role='checkbox']"
            )) > 0
        )
    except Exception:
        print("⚠️ Page encore vide, tentative de capture malgré tout.")

    print("📸 Capture de la page pour vision IA... source: survey_executor.py line 56")
    screenshot_path = Survey.screenshot_analyzer.take_screenshot(driver, full_page=True)  # ⬅️ plein‑page

    # 🛡️ Garde-fou URL: si on est hors périmètre, on n'agit pas
    try:
        cur = driver.current_url
    except Exception:
        cur = ""
    if not Management.guards.url_guard.is_allowed(cur):
        print(f"[URL_GUARD] Page hors périmètre, aucune action: {cur}")
        return False

    classification = dom_classifier.classify_dom(driver)

    if classification:
        itype = classification["itype"]
        handler_name = classification["handler"]
        allow_openai = classification["openai"]

        print(f"[DOM_CLASSIFIER] itype={itype} handler={handler_name} openai={allow_openai}")

        if not allow_openai:
            # handler local direct
            return getattr(action_dispatcher, handler_name)(driver)
        
    dom_metrics.log_snapshot()

    question_blocks = Survey.dom_analyzer.analyze_dom(driver)
    client = openai.OpenAI(api_key=api_key)

    if question_blocks:
        prompt = Survey.prompt_builder.build_batch_prompt(question_blocks)

        instruction_raw = client.responses.create(
            messages=[
                {"role": "user", "content": prompt}
                ],
            model="gpt-4o-2024-08-06",
            cache_key_hint="dom_batch"
        )

        actions = Survey.batch_response_parser.parse_batch_response(instruction_raw)

        success_any = False

        for act in actions:
            instruction = f"{act['value']} //// {act['itype']} //// {act['context']}"
            ok = Survey.action_dispatcher.execute_action(driver, instruction)
            if ok:
                success_any = True

        return success_any
    else:
        # fallback vision (existant)
        print("🤖 Envoi à GPT pour interprétation visuelle... source: survey_executor.py line 59")
        instruction = Survey.screenshot_analyzer.send_image_to_gpt(
            screenshot_path, api_key, previous_image_path=previous_screenshot_path,
            side_context=getattr(driver, "_last_video_transcript", None)
            )   
        pass


    #print("📥 Instruction reçue (non-nettoyée) :",instruction,)
    # 🔁 MàJ: mémoriser cette capture comme "précédente" pour le prochain tour
    last_screenshot_path = screenshot_path

    # ➜ UTILISATION, juste après avoir reçu la réponse du modèle (variable `instruction`)
    #    et avant de la renvoyer à l’exécuteur :
    lines = [ln for ln in (instruction or "").splitlines() if ln.strip()]
    fixed_lines = [_coerce_safe_value_if_questionish(ln) for ln in lines]
    instruction = "\n".join(fixed_lines)
    #print("📥 Instruction reçue (nettoyée dans le fixed_lines) :", instruction, " source: survey_executor.py")

    # --- Ne conserver que la 1ère ligne non vide ---
    if instruction:
        instruction = next(
            (ln.strip() for ln in instruction.splitlines() if ln.strip()), ""
        )

    print(
        "📥 Instruction reçue (nettoyée) :",
        instruction,
        " source: survey_executor.py line 67",
    )

    try:
        success = Survey.action_dispatcher.execute_action(driver, instruction)
        if not success:
            print(
                "ℹ️ Aucune action appliquée par le dispatcher. source: survey_executor.py"
            )
        return success
    except Exception as e:
        print(
            "❌ Erreur dans l’exécution de l’action basée sur GPT; source: survey_executor.py",
        )
        return False


def extract_full_visible_text(driver):
    """
    Extrait tout le texte visible de la page, en ignorant les balises de type lien, script, style, header, etc.
    """
    js = """
    return Array.from(document.querySelectorAll('body *'))
      .filter(e => {
          const style = window.getComputedStyle(e);
          const tag = e.tagName.toLowerCase();
          const ignored = ['a', 'footer', 'header', 'nav', 'script', 'style'];
          return style && style.display !== 'none' &&
                 style.visibility !== 'hidden' &&
                 e.offsetParent !== null &&
                 !ignored.includes(tag);
      })
      .map(e => e.innerText)
      .filter(t => t && t.trim().length > 5)
      .map(t => t.trim());
    """

    try:
        result = driver.execute_script(js)
        return list(dict.fromkeys(result))  # supprimer les doublons
    except Exception as e:
        print("❌ JS extraction erreur:", e, "survey_executor.py line 251")
        return []


# ⚖️ Sous-fonction : appliquer une action recommandée par l'IA


def perform_action_based_on_text(driver, action):
    """
    Essaie de cliquer sur un bouton ou un label qui correspond à l'action textuelle de l'IA.
    """
    buttons = (
        driver.find_elements(By.TAG_NAME, "button")
        + driver.find_elements(By.TAG_NAME, "input")
        + driver.find_elements(By.TAG_NAME, "a")
    )

    for elem in buttons:
        try:
            label = elem.get_attribute("value") or elem.text
            if not label:
                spans = elem.find_elements(By.TAG_NAME, "span")
                for span in spans:
                    if span.text.strip():
                        label = span.text.strip()
                        break
            if label and action.lower() in label.lower():
                ActionChains(driver).move_to_element(elem).click().perform()
                print(
                    f"✅ Action '{action}' exécutée sur l'élément : {label} survey_executor.py line 274"
                )
                time.sleep(2)
                return True
        except:
            continue

    print(
        f"❌ Aucun élément ne correspond à l'action source: survey_executor.py line 280"
    )
    return False
