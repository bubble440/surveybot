import time
import unicodedata
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains

from config import should_pause_before_cta, is_cta_intercept_only
from Survey.log_utils import log_info, log_debug


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


def execute_response(driver, answer_text, input_type=None):
    # Pas de choix → souvent page de blocage ou de consentement non mappée
    if not answer_text:
        print("⏭️ Aucun choix détecté — pas d'action sur cette page. source: reponse_executor.py")
        return False

    print(f"🌟 Tentative de sélection : {answer_text} source: reponse_executor.py")
    norm_answer = normalize(answer_text)
    checkbox_answers = [
        chunk.strip() for chunk in str(answer_text).split("|") if chunk.strip()
    ]
    success = False

    try:
        # 1) Tentative checkbox en priorité
        success = select_checkbox_answers(driver, checkbox_answers)
        if success:
            click_next_button(driver)
            return success

        # 2) Si le type d'input est explicitement checkbox, pas de fallback radio
        if input_type == "checkbox":
            print("❌ Option checkbox non cochée. Pas de fallback radio (type=checkbox confirmé).")
            return False

        # 2.5) Champ texte libre (input_text)
        text_input = None
        try:
            text_input = driver.find_element(
                By.CSS_SELECTOR, 'input[data-test-id*="input_text-input"]'
            )
        except Exception:
            pass

        if text_input is not None:
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", text_input
            )
            text_input.clear()
            text_input.send_keys(str(answer_text))
            driver.execute_script(
                "arguments[0].dispatchEvent(new Event('input', {bubbles:true}));"
                "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
                text_input,
            )
            time.sleep(1)
            log_info("response_executor", f"✅ Champ texte rempli : {answer_text}")
            click_next_button(driver)
            return True

        # 3) Si aucune checkbox trouvée → tentative radio
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
                    # 1) tenter clic direct sur l'input radio
                    try:
                        radio = label.find_element(By.CSS_SELECTOR, "input[type='radio']")
                        driver.execute_script(
                            "arguments[0].scrollIntoView({block:'center'});", radio
                        )
                        time.sleep(2)
                        driver.execute_script("arguments[0].click();", radio)
                    except Exception:
                        # 2) fallback clic humain
                        ActionChains(driver).move_to_element(label).click().perform()
                        time.sleep(2)
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
                    time.sleep(2)
                    click_next_button(driver)
                    return True  # ✅ succès
        print("❌ Option radio non cochée.")
        return False

    except Exception as e:
        print(
            "💥 Erreur d’exécution :",
            type(e).__name__,
            "-",
            e,
            "source: reponse_executor.py",
        )
        return False



def _confirm_before_cta_click() -> None:
    if should_pause_before_cta():
        print("⏸️ LOCAL_CTA_REQUIRE_ENTER=1 — appuyez sur Entrée pour cliquer sur le CTA. source: reponse_executor.py")
        input()

def click_next_button(driver):
    if is_cta_intercept_only():
        log_info("response_executor", "🛑 CTA_INTERCEPT_ONLY=1 — clic CTA intercepté, pas de navigation.")
        return True

    wait = WebDriverWait(driver, 10)
    try:
        next_btn = driver.find_element(
            By.CSS_SELECTOR, 'button[data-test-id="ps-common-actions-button"]'
        )
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'});", next_btn
        )
        time.sleep(2)
        _confirm_before_cta_click()
        driver.execute_script("arguments[0].click();", next_btn)
        print(
            "➡️ Bouton (flèche ou navigation) cliqué via data-test-id. source: reponse_executor.py"
        )
        from Management.redirect_watcher import wait_for_page_load
        wait_for_page_load(driver, timeout=15)
        return True

    except:
        try:
            xpath = (
                "//button["
                "contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'suivant') or "
                "contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'continuer') or "
                "contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'next') or "
                "contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'continue')"
                "]"
            )

            next_btn = wait.until(EC.presence_of_element_located((By.XPATH, xpath)))
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_btn)
            time.sleep(2)
            _confirm_before_cta_click()

            # attendre que le bouton devienne réellement cliquable (disabled retiré)
            try:
                WebDriverWait(driver, 6).until(
                    lambda d: next_btn.is_enabled() and (next_btn.get_attribute("disabled") is None)
                )
            except Exception:
                # dernier recours: si l’UI ne réagit pas malgré input/change, on force le enable
                try:
                    driver.execute_script(
                        "arguments[0].removeAttribute('disabled'); arguments[0].classList.remove('disabled');",
                        next_btn,
                    )
                except Exception:
                    pass

            driver.execute_script("arguments[0].click();", next_btn)
            print("➡️ Bouton cliqué via fallback textuel (case-insensitive + enabled). source: reponse_executor.py")
            from Management.redirect_watcher import wait_for_page_load
            wait_for_page_load(driver, timeout=15)
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

    normalized_targets = [
        normalize(str(answer)) for answer in (answers if isinstance(answers, list) else [answers])
    ]

    for target in normalized_targets:
        for label in labels:
            try:
                text_elem = label.find_element(
                    By.CSS_SELECTOR, '[data-test-id*="multiple_choice-text"]'
                )
                label_text = text_elem.text.strip()
                if normalize(label_text) != target:
                    continue

                inner_cb = label.find_element(By.CSS_SELECTOR, "input[type='checkbox']")
                if inner_cb.is_selected():
                    print(f"✅ Checkbox déjà cochée : {label_text}")
                    found = True
                    break

                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'}); arguments[0].click();",
                    inner_cb,
                )
                time.sleep(1)
                if not inner_cb.is_selected():
                    ActionChains(driver).move_to_element(label).click().perform()

                if inner_cb.is_selected():
                    print(f"✅ Checkbox cochée : {label_text} source: reponse_executor.py")
                    found = True
                else:
                    print(f"⚠️ Checkbox trouvée mais non cochée : {label_text} source: reponse_executor.py")
                break
            except Exception:
                continue
    if found:
        return True
    return False
