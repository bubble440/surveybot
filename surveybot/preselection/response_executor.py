import time
import unicodedata
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains


def normalize(text):
    """Nettoie une chaîne pour comparaison souple"""
    text = unicodedata.normalize("NFKD", text)
    text = text.lower().strip()
    return (
        text.replace("€", "e")
        .replace("à", "a")
        .replace("–", "-")
        .replace(",", "")
        .replace(" ", "")
    )


def _is_checked_soft(el) -> bool:
    try:
        if el.tag_name.lower() == "label":
            # tente de trouver l'input à l'intérieur
            try:
                cb = el.find_element(By.CSS_SELECTOR, "input[type='checkbox']")
                return cb.is_selected()
            except Exception:
                pass
        if (
            el.tag_name.lower() == "input"
            and (el.get_attribute("type") or "").lower() == "checkbox"
        ):
            return el.is_selected()
    except Exception:
        pass
    return False


def execute_response(driver, answer_text):
    # Pas de choix → souvent page de blocage ou de consentement non mappée
    if not answer_text:
        print("⏭️ Aucun choix détecté — pas d'action sur cette page. source: reponse_executor.py")
        return False

    print(f"🌟 Tentative de sélection : {answer_text} source: reponse_executor.py")
    norm_answer = normalize(answer_text)
    success = False

    try:
        # 1) Tentative checkbox en priorité
        success = select_checkbox_answers(driver, answer_text)
        if success:
            click_next_button(driver)
            return success

    
        # 2) Si aucune checkbox trouvée → tentative radio
        labels = driver.find_elements(
            By.CSS_SELECTOR,
            'label[data-test-id^="ps-question-input-single_choice-label"]',
        )
        for label in labels:
            spans = label.find_elements(
                By.CSS_SELECTOR, 'span[class*="p-radio-text"]'
            )
            for span in spans:
                if norm_answer in normalize(span.text) or normalize(span.text) in norm_answer:
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center'});", label
                    )
                    time.sleep(0.3)
                    # 1) tenter clic direct sur l'input radio
                    try:
                        radio = label.find_element(By.CSS_SELECTOR, "input[type='radio']")
                        driver.execute_script(
                            "arguments[0].scrollIntoView({block:'center'});", radio
                        )
                        time.sleep(0.2)
                        driver.execute_script("arguments[0].click();", radio)
                    except Exception:
                        # 2) fallback clic humain
                        ActionChains(driver).move_to_element(label).click().perform()
                    print(
                        f"✅ Option radio sélectionnée : {span.text} source: reponse_executor.py"
                    )
                    # 🔍 Vérification post-clic
                    try:
                        if not radio.is_selected():
                            print("⚠️ Radio non sélectionné après clic JS — retry ActionChains")
                            ActionChains(driver).move_to_element(radio).click().perform()
                    except Exception:
                        pass

                    click_next_button(driver)
                    return True  # ✅ succès
        print(
            "❌ Option radio non cochée. Tentative fallback vers checkbox. source: reponse_executor.py"
        )

    except Exception as e:
        print(
            "💥 Erreur d’exécution :",
            type(e).__name__,
            "-",
            e,
            "source: reponse_executor.py",
        )
        return False


def click_next_button(driver):
    wait = WebDriverWait(driver, 10)
    try:
        next_btn = driver.find_element(
            By.CSS_SELECTOR, 'button[data-test-id="ps-common-actions-button"]'
        )
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'});", next_btn
        )
        time.sleep(0.2)
        driver.execute_script("arguments[0].click();", next_btn)
        print(
            "➡️ Bouton (flèche ou navigation) cliqué via data-test-id. source: reponse_executor.py"
        )
        return True

    except:
        try:
            next_btn = wait.until(
                EC.element_to_be_clickable(
                    (
                        By.XPATH,
                        "//button[contains(., 'Suivant') or contains(., 'Continuer') or contains(., 'Next') or contains(., 'Continue')]",
                    )
                )
            )
            driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center'});", next_btn
            )
            time.sleep(0.2)
            driver.execute_script("arguments[0].click();", next_btn)
            print("➡️ Bouton cliqué via fallback textuel. source: reponse_executor.py")
            return True

        except Exception as e:
            print(
                "⏭️ Aucun bouton « Suivant » ou navigation détecté :",
                type(e).__name__,
                "-",
                e,
                " source: reponse_executor.py",
            )
            return False


def select_checkbox_answers(driver, answers):
    """
    Coche une ou plusieurs cases à cocher correspondant aux réponses proposées par l'IA.
    """
    labels = driver.find_elements(
        By.CSS_SELECTOR, '[data-test-id^="ps-question-input-multiple_choice-label"]'
    )
    found = False

    for answer in answers if isinstance(answers, list) else [answers]:
        for label in labels:
            try:
                text_elem = label.find_element(
                    By.CSS_SELECTOR, '[data-test-id*="multiple_choice-text"]'
                )
                label_text = text_elem.text.strip().lower()
                if answer.lower() in label_text:
                    # NEW: ne reclique pas si déjà coché
                    try:
                        inner_cb = label.find_element(
                            By.CSS_SELECTOR, "input[type='checkbox']"
                        )
                        if inner_cb.is_selected():
                            print(f"✅ Checkbox déjà cochée : {label_text}")
                            found = True
                            return True
                            break
                    except Exception:
                        pass
                    ActionChains(driver).move_to_element(label).click().perform()
                    print(
                        f"✅ Checkbox cochée : {label_text} source: reponse_executor.py"
                    )
                    found = True
                    return True
                    break
            except Exception:
                continue
    if found:
        print(
            f"❌ Aucun checkbox correspondant à la réponse : {answers} source: reponse_executor.py"
        )
        return False