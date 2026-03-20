import os
import re
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from preselection.auth_handler import snap
from Survey.log_utils import log_debug, log_info


def _local_pause(reason: str = "") -> None:
    try:
        from config import should_block_for_input

        if not should_block_for_input():
            return
        if not _is_truthy_env(os.getenv("LOCAL_CTA_REQUIRE_ENTER", "0")):
            return

        msg = "[LOCAL][PAUSE] Appuie sur <Enter> pour continuer"
        if reason:
            msg += f" ({reason})"
        print(msg, flush=True)
        try:
            input()
        except KeyboardInterrupt:
            raise
    except Exception:
        return

def _is_debug_enabled() -> bool:
    return os.getenv("LOG_LEVEL", "INFO").strip().upper() == "DEBUG"


def _debug(msg: str):
    if _is_debug_enabled():
        log_debug("[TOPSURVEYS][DEBUG]", msg)


def _is_truthy_env(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _click_button_with_optional_intercept(driver, element) -> bool:
    """
    Clique un bouton normalement, ou en mode CTA_INTERCEPT_ONLY déclenche
    un click non destructif (dispatch évènement + preventDefault) pour exécuter
    les handlers UI sans soumission réelle.
    """
    if not _is_truthy_env(os.getenv("CTA_INTERCEPT_ONLY")):
        driver.execute_script("arguments[0].click();", element)
        return True

    return bool(
        driver.execute_script(
            """
            const el = arguments[0];
            if (!el) return false;
            const blocker = (evt) => { evt.preventDefault(); };
            el.addEventListener('click', blocker, { capture: true, once: true });
            const evt = new MouseEvent('click', { bubbles: true, cancelable: true, view: window });
            return el.dispatchEvent(evt);
            """,
            element,
        )
    )


def _handle_mystery_box_popup(driver) -> None:
    """
    Gère le popup de récompense TopSurveys si présent:
    - détecte via présence d'un bouton de mystery box ET d'un bouton "Complète"
    - ouvre uniquement la 3e boîte
    - clique "Complète" pour fermer
    Budget strict: 1 tentative d'ouverture, 1 tentative de fermeture.
    """
    tag = "[TOPSURVEYS_MYSTERY_BOX]"
    box_selector = "[data-test-id='ps-mystery-box-item-button-2']"
    mystery_presence_selector = "[data-test-id^='ps-mystery-box-item-button']"
    complete_xpath = "//button[normalize-space()='Complète' or .//span[normalize-space()='Complète']]"

    has_mystery_boxes = bool(driver.find_elements(By.CSS_SELECTOR, mystery_presence_selector))
    has_complete_btn = bool(driver.find_elements(By.XPATH, complete_xpath))
    if not (has_mystery_boxes and has_complete_btn):
        _debug("Popup mystery box non détecté avant sélection de survey.")
        return

    reason = "popup_detected=true"
    log_info(tag, reason)
    _local_pause(f"{tag} {reason}")

    wait_short = WebDriverWait(driver, 5)
    try:
        open_btn = wait_short.until(EC.element_to_be_clickable((By.CSS_SELECTOR, box_selector)))
        open_ok = _click_button_with_optional_intercept(driver, open_btn)
        reason = f"box3_click={'OK' if open_ok else 'INTERCEPTION_IMPOSSIBLE'}"
        log_info(tag, reason)
        _local_pause(f"{tag} {reason}")
    except Exception as e:
        reason = f"box3_click=FAILED reason={type(e).__name__}"
        log_info(tag, reason)
        _local_pause(f"{tag} {reason}")
        return

    time.sleep(1)

    try:
        complete_btn = wait_short.until(EC.element_to_be_clickable((By.XPATH, complete_xpath)))
        complete_ok = _click_button_with_optional_intercept(driver, complete_btn)
        reason = f"complete_click={'OK' if complete_ok else 'INTERCEPTION_IMPOSSIBLE'}"
        log_info(tag, reason)
        _local_pause(f"{tag} {reason}")
    except Exception as e:
        reason = f"complete_click=FAILED reason={type(e).__name__}"
        log_info(tag, reason)
        _local_pause(f"{tag} {reason}")


def _parse_reward_eur(text: str):
    """Extrait un montant EUR depuis le texte de carte (ex: 0,66 €)."""
    if not text:
        return None
    match = re.search(r"(?:€\s*(\d+[\.,]?\d*)|(\d+[\.,]?\d*)\s*€)", text)
    if not match:
        return None
    raw = (match.group(1) or match.group(2) or "").replace(",", ".").strip()
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _parse_duration_min(text: str):
    """Extrait la durée estimée en minutes depuis le texte de carte (ex: 22 min)."""
    if not text:
        return None
    match = re.search(r"(\d+)\s*(?:min|mn|minute(?:s)?)\b", text, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        duration = int(match.group(1))
    except ValueError:
        return None
    return duration if duration > 0 else None


def _find_survey_cards(driver):
    selectors = [
        "div.survey-tile",
        "[class*='survey-tile']",
        "[data-test-id*='survey-tile']",
        "[data-test-id*='survey-card']",
    ]
    cards = []
    seen = set()
    for selector in selectors:
        for card in driver.find_elements(By.CSS_SELECTOR, selector):
            card_id = card.id
            if card_id in seen:
                continue
            seen.add(card_id)
            cards.append(card)
    return cards


def _is_card_clickable(card) -> bool:
    try:
        return card.is_displayed() and card.is_enabled()
    except Exception:
        return False


def _select_best_value_card(driver):
    """
    Score chaque carte via reward_eur / duration_min et renvoie la meilleure exploitable.
    Les cartes non parsables/non cliquables sont ignorées pour garder une sélection stable.
    """
    candidates = []
    for idx, card in enumerate(_find_survey_cards(driver), start=1):
        try:
            text = (card.text or "").strip()
            reward = _parse_reward_eur(text)
            duration = _parse_duration_min(text)
            if reward is None:
                _debug(f"Carte #{idx} ignorée: reward non parsable | text={text!r}")
                continue
            if duration is None:
                _debug(f"Carte #{idx} ignorée: durée non parsable | text={text!r}")
                continue
            if duration <= 0:
                _debug(f"Carte #{idx} ignorée: durée <= 0 | text={text!r}")
                continue
            if not _is_card_clickable(card):
                _debug(f"Carte #{idx} ignorée: non cliquable")
                continue
            score = reward / duration
            candidates.append((score, reward, duration, idx, card))
            _debug(
                f"Carte #{idx} candidate: reward={reward:.2f}€ duration={duration}min score={score:.4f}€/min"
            )
        except Exception as e:
            _debug(f"Carte #{idx} ignorée: exception {type(e).__name__} - {e}")

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    best_score, best_reward, best_duration, best_idx, best_card = candidates[0]
    print(
        "🧠 Survey sélectionné par rentabilité: "
        f"carte #{best_idx} | {best_reward:.2f}€ / {best_duration} min = {best_score:.4f} €/min"
    )
    return best_card


def go_to_best_value_survey(driver):
    wait_short = WebDriverWait(driver, 8)
    wait = WebDriverWait(driver, 30)

    def _click_enquetes():
        # 1) XPATH texte
        try:
            tab = wait_short.until(EC.element_to_be_clickable((By.XPATH, "//span[normalize-space()='Enquêtes']")))
            driver.execute_script("arguments[0].click();", tab)
            print("🗂️  Onglet « Enquêtes » cliqué. [xpath texte]")
            return True
        except Exception:
            pass
        # 2) data-test-id si dispo
        try:
            tab = wait_short.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "[data-test-id='ps-side-menu-surveys']")))
            driver.execute_script("arguments[0].click();", tab)
            print("🗂️  Onglet « Enquêtes » cliqué. [data-test-id]")
            return True
        except Exception:
            pass
        return False

    if not _click_enquetes():
        # 3) fallback: navigation directe
        try:
            driver.get("https://app.topsurveys.app/surveys")
            print("↪️  Navigation directe /surveys")
            # vérifier qu'on est bien sur la page
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "[data-test-id='ps-surveys-root']")))
        except Exception as e:
            print("🛑 Exception navigation :", type(e).__name__, "-", e)
            return

    time.sleep(15)  # laisser le temps au contenu de charger
    _handle_mystery_box_popup(driver)
    snap(driver, "before_best_value_selection")

    if not _find_survey_cards(driver):
        log_info("[TOPSURVEYS][COOLDOWN]", "Aucun survey disponible → cooldown 15 min (DB + stop task)")
        from Management.guards.runtime_guard import get_guard, StopReason
        from Management.pause_policy import PausePolicy
        get_guard().pause(PausePolicy.MEDIUM_LONG_COOLDOWN, StopReason.NO_SURVEY_AVAILABLE)
        # pause() lève SystemExit — jamais atteint

    best_card = _select_best_value_card(driver)
    if best_card is not None:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", best_card)
            driver.execute_script("arguments[0].click();", best_card)
            print("📝 Survey le plus rentable cliqué.")
            from Management.redirect_watcher import wait_for_page_load
            wait_for_page_load(driver, timeout=30)
            return
        except Exception as e:
            print("⚠️ Échec clic survey le plus rentable:", type(e).__name__, "-", e)

    print("⚠️ Aucune carte exploitable trouvée via score €/min — fallback premier survey cliquable.")
    # Fallback simple et prévisible
    try:
        first = wait.until(EC.element_to_be_clickable((By.XPATH, "(//div[contains(@class, 'survey-tile')])[1]")))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", first)
        driver.execute_script("arguments[0].click();", first)
        print("📝 Fallback: premier survey cliqué.")
        from Management.redirect_watcher import wait_for_page_load
        wait_for_page_load(driver, timeout=30)
    except Exception as e:
        print("🛑 Exception sélection du survey :", type(e).__name__, "-", e)


def go_to_best_paid_survey(driver):
    """Alias rétrocompatible: redirige vers la sélection par meilleure rentabilité €/min."""
    go_to_best_value_survey(driver)
