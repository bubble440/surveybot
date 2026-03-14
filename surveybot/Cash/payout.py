from __future__ import annotations
import os
import re
import time
from typing import Tuple
from State.account_state import update_state
from Management.guards.runtime_guard import get_guard
from selenium.webdriver.common.by import By
IS_LOCAL = os.getenv("RUN_ENV", "local") == "local"

if not IS_LOCAL:
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import (
        StaleElementReferenceException,
        ElementClickInterceptedException,
    )
    from selenium.webdriver.common.action_chains import ActionChains
# ---------- Helpers ----------

def _wait(driver, timeout=10):
    return WebDriverWait(driver, timeout)

def _js_click(driver, el):
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    driver.execute_script("arguments[0].click();", el)

def _find(driver, by, sel, timeout=10):
    return _wait(driver, timeout).until(EC.presence_of_element_located((by, sel)))

def _find_all(driver, by, sel, timeout=10):
    _wait(driver, timeout).until(EC.presence_of_all_elements_located((by, sel)))
    return driver.find_elements(by, sel)

# ---------- Lecture du solde & ouverture du modal ----------

def _open_cashout_modal(driver) -> bool:
    """
    Clique le bouton 'Encaissement'
    DOM fourni:
    <button data-test-id="balance-card-cashout">Encaissement</button>
    """
    try:
        btn = _find(driver, By.CSS_SELECTOR, "button[data-test-id='balance-card-cashout']")
        _js_click(driver, btn)
        # attend l'apparition du conteneur modal
        _find(driver, By.CSS_SELECTOR, ".rewards-modal-container")
        return True
    except Exception:
        return False

def _is_enabled(el) -> bool:
    try:
        return el.is_enabled() and not bool(el.get_attribute("disabled"))
    except Exception:
        return False

def _get_select_btn(driver):
    try:
        return driver.find_element(By.CSS_SELECTOR, "button[data-test-id='reward-select-button']")
    except Exception:
        return None

def _wait_select_btn_enabled(driver, timeout=5):
    _wait(driver, timeout).until(lambda d: (_b := _get_select_btn(d)) and _is_enabled(_b))

def _dispatch_mouse_sequence(driver, el) -> None:
    driver.execute_script("""
        const el = arguments[0];
        el.scrollIntoView({block:'center'});
        for (const type of ['mouseover','mousemove','mousedown','mouseup','click']) {
            el.dispatchEvent(new MouseEvent(type, {bubbles:true,cancelable:true,view:window}));
        }
    """, el)

def _select_money_option_in_open_tab(driver, tab_el, amount="5") -> bool:
    """
    Dans un tab .p-active, sélectionne l'option 'amount €' (non .blocked)
    en cliquant le wrapper [data-test-id="reward-option"].
    """
    # Cible les spans '5 €' puis remonte au wrapper cliquable
    candidates = tab_el.find_elements(
        By.XPATH,
        f".//span[contains(@class,'option-money')][contains(normalize-space(.), '{amount}') and contains(normalize-space(.),'€')]"
    )
    for span in candidates:
        try:
            wrapper = span.find_element(
                By.XPATH, "./ancestor::*[@data-test-id='reward-option'][1]"
            )
            if "blocked" in (wrapper.get_attribute("class") or ""):
                continue

            # 1) click() direct
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", wrapper)
            time.sleep(0.1)
            try:
                wrapper.click()
            except Exception:
                # 2) ActionChains
                try:
                    ActionChains(driver).move_to_element(wrapper).pause(0.05).click().perform()
                except Exception:
                    # 3) séquence d'événements souris JS (certains frameworks attendent ça)
                    _dispatch_mouse_sequence(driver, wrapper)

            # si ça a marché, le bouton 'Choisis' devient activable
            try:
                _wait_select_btn_enabled(driver, timeout=2)
                return True
            except Exception:
                # dernier essai : clic JS sur le <span>
                try:
                    _js_click(driver, span)
                    _wait_select_btn_enabled(driver, timeout=2)
                    return True
                except Exception:
                    continue

        except StaleElementReferenceException:
            continue
        except Exception:
            continue
    return False

def _accordion_open(driver, label_substr: str) -> bool:
    """Assure l'ouverture de l'accordéon par son titre."""
    try:
        btn = _find(
            driver,
            By.XPATH,
            "//button[contains(@class,'p-accordion-button')][.//span[contains(normalize-space(.), %s)]]" %
            repr(label_substr)
        )
        tab = btn.find_element(By.XPATH, "./ancestor::div[contains(@class,'p-accordion-tab')]")
        if "p-active" not in (tab.get_attribute("class") or ""):
            _js_click(driver, btn)
            time.sleep(0.3)
        # s’assure que le contenu est présent
        _wait(driver, 5).until(lambda d: len(tab.find_elements(By.CSS_SELECTOR, ".p-accordion-content")) > 0)
        return True
    except Exception:
        return False
    
def _select_money_option_5_eur_in_open_tab(tab_el) -> bool:
    """
    Dans un tab déjà 'p-active', clique l'option '5 €' non bloquée.
    DOM type:
      <div class="reward-options">
         <div data-test-id="reward-option" class="">
            <div class="reward-option">
              <span class="option-money">5 €</span>
            </div>
         </div>
    """
    try:
        opt = tab_el.find_element(
            By.XPATH,
            ".//div[contains(@class,'reward-options')]"
            "//div[@data-test-id='reward-option' and not(contains(@class,'blocked'))]"
            "[.//span[contains(normalize-space(.),'5') and contains(normalize-space(.),'€')]]"
        )
        _js_click(tab_el.parent, opt)  # JS click via driver du parent (hack simple)
        return True
    except Exception:
        try:
            # variation: cliquer directement sur le <span> '5 €'
            span = tab_el.find_element(
                By.XPATH,
                ".//span[contains(@class,'option-money')][contains(normalize-space(.),'5') and contains(normalize-space(.),'€')]"
            )
            _js_click(tab_el.parent, span)
            return True
        except Exception:
            return False

def _click_modal_choose(driver) -> bool:
    """
    Clique le bouton 'Choisis' dans le footer du modal.
    DOM:
      <button data-test-id="reward-select-button" ...>Choisis</button>
    """
    try:
        btn = _find(driver, By.CSS_SELECTOR, "button[data-test-id='reward-select-button']")
        # il peut être 'disabled' avant sélection -> on attend qu'il soit cliquable
        _wait(driver, 10).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-test-id='reward-select-button']")))
        _js_click(driver, btn)
        return True
    except Exception:
        return False

def _select_paypal_5_eur(driver) -> bool:
    """Ouvre 'PayPal International', choisit 5 € puis clique 'Choisis'."""
    if not _accordion_open(driver, "PayPal International"):
        return False
    tab = _find(
        driver,
        By.XPATH,
        "//div[contains(@class,'p-accordion-tab') and contains(@class,'p-active')][.//span[contains(normalize-space(.),'PayPal International')]]"
    )
    if not _select_money_option_in_open_tab(driver, tab, amount="5"):
        return False
    time.sleep(0.2)
    return _click_modal_choose(driver)

# ---------- Fallback Revolut ----------

def _fill_revolut_claim_if_needed(driver, fullname: str, tag: str) -> None:
    """
    Si la page de confimation Revolut demande des champs, on les remplit:
    DOM fourni:
      input[data-test-id="claim-reward-revolut-name-field-input"]
      input[data-test-id="claim-reward-revolut-tag-field-input"]
    """
    try:
        name_inp = driver.find_element(By.CSS_SELECTOR, "input[data-test-id='claim-reward-revolut-name-field-input']")
        tag_inp  = driver.find_element(By.CSS_SELECTOR, "input[data-test-id='claim-reward-revolut-tag-field-input']")
        name_inp.clear(); name_inp.send_keys(fullname)
        tag_inp.clear(); tag_inp.send_keys(tag)
    except Exception:
        # champs non présents (pas Revolut) -> OK
        pass

def _select_revolut_5_eur(driver) -> bool:
    if not _accordion_open(driver, "Revolut"):
        return False
    tab = _find(
        driver,
        By.XPATH,
        "//div[contains(@class,'p-accordion-tab') and contains(@class,'p-active')][.//span[contains(normalize-space(.),'Revolut')]]"
    )
    if not _select_money_option_5_eur_in_open_tab(tab):
        return False
    time.sleep(0.2)
    return _click_modal_choose(driver)

# ---------- Confirmation ----------

def _confirm_claim(driver, maybe_revolut_fullname: str = "", maybe_revolut_tag: str = "") -> bool:
    """
    Sur /confirm-claim, clique 'Réclamer une récompense'.
    Remplit Revolut si demandé.
    DOM:
      <button data-test-id="confirm-claim-button">Réclamer une récompense</button>
    """
    # Si Revolut, on remplit (si champs visibles)
    _fill_revolut_claim_if_needed(driver, maybe_revolut_fullname, maybe_revolut_tag)

    try:
        btn = _find(driver, By.CSS_SELECTOR, "button[data-test-id='confirm-claim-button']")
        _js_click(driver, btn)
        return True
    except Exception:
        return False

def _parse_amount(text: str) -> float:
    """
    Parse un montant € de façon robuste.
    Exemples acceptés :
      - "2,19 €"
      - "2.19€"
      - "2,19 €"
      - "€2,19"
    """
    if not text:
        raise ValueError("balance text vide")

    # normalisation unicode + espaces
    t = (
        text.replace("\u00a0", " ")  # nbsp
            .replace("€", "")
            .strip()
    )

    # extraction nombre (virgule ou point)
    m = re.search(r"(\d+[.,]\d+|\d+)", t)
    if not m:
        raise ValueError(f"montant non détecté dans '{text}'")

    num = m.group(1).replace(",", ".")
    return float(num)

def _read_balance(driver) -> float:
    """
    Lecture robuste du solde TopSurveys.
    Fallbacks successifs basés sur le DOM réel.
    """
    import re

    candidates = []
    time.sleep(5)
    # 1️⃣ Méthode historique (si jamais ils réintroduisent le test-id)
    try:
        el = driver.find_element(By.CSS_SELECTOR, "[data-test-id='balance-card-amount']")
        candidates.append(el.text)
    except Exception:
        pass

    # 2️⃣ DOM actuel : span contenant "€" dans balance-card-progress
    try:
        spans = driver.find_elements(
            By.CSS_SELECTOR,
            ".balance-card-progress span"
        )
        for s in spans:
            txt = (s.text or "").strip()
            if "€" in txt and "/" not in txt:
                candidates.append(txt)
    except Exception:
        pass

    # 3️⃣ Fallback ultime : scan global (safe mais coûteux)
    if not candidates:
        try:
            spans = driver.find_elements(By.XPATH, "//span[contains(text(),'€')]")
            for s in spans:
                txt = (s.text or "").strip()
                if "€" in txt and "/" not in txt:
                    candidates.append(txt)
        except Exception:
            pass

    if not candidates:
        raise RuntimeError("Impossible de lire le solde (aucun montant détecté)")

    # Nettoyage & parsing
    raw = candidates[0]
    raw = raw.replace("\xa0", " ").replace("€", "").strip()
    raw = raw.replace(",", ".")

    try:
        return float(re.findall(r"\d+(?:\.\d+)?", raw)[0])
    except Exception:
        raise RuntimeError(f"Parsing solde échoué: '{raw}'")

# ---------- API principale ----------

def check_and_cashout_if_needed(
    driver,
    *,
    account_id: str,
    min_amount_eur: float = 5.0,
    cashout_order: Tuple[str, str] = ("revolut", "paypal"),
    revolut_fullname: str = "",
    revolut_tag: str = ""
) -> bool:
    """
    - Lit le solde,
    - Si >= min_amount_eur, ouvre le modal,
    - Tente encaissement dans l'ordre `cashout_order` ('paypal' puis 'revolut' par défaut),
    - Confirme la réclamation sur la page suivante.
    Renvoie True si un encaissement a été tenté (et soumis), False sinon.
    """

    try:
        amount = _read_balance(driver)
    except Exception as e:
        print("[PAYOUT][ERROR] Lecture solde échouée:", e)
        return False

    # Retry: l'UI peut ne pas être prête juste après le login/redirection
    amount = None
    last_err = None
    deadline = time.time() + 12.0
    while time.time() < deadline:
        try:
            amount = _read_balance(driver)
            last_err = None
            break
        except Exception as e:
            last_err = e
            time.sleep(0.6)

    if amount is None:
        print("[PAYOUT][ERROR] Lecture solde échouée:", last_err)
        return False

    if amount < min_amount_eur:
        print(f"[PAYOUT] Solde insuffisant ({amount:.2f} €). Rien à faire.")
        return False

    print(f"[PAYOUT] Solde détecté: {amount:.2f} €. Ouverture du modal d'encaissement…")
    if not _open_cashout_modal(driver):
        print("[PAYOUT] Impossible d'ouvrir le modal d'encaissement.")
        return False

    # Essaye dans l'ordre demandé
    success_select = False
    for method in cashout_order:
        if method == "paypal":
            if _select_paypal_5_eur(driver):
                success_select = True
                print("[PAYOUT] Option PayPal 5 € sélectionnée.")
                break
        elif method == "revolut":
            if _select_revolut_5_eur(driver):
                success_select = True
                print("[PAYOUT] Option Revolut 5 € sélectionnée (fallback).")
                break

    if not success_select:
        print("[PAYOUT] Aucune option d'encaissement sélectionnée (PayPal/Revolut indisponibles ?).")
        return False

    # On est maintenant sur /confirm-claim
    time.sleep(0.4)
    if _confirm_claim(driver, revolut_fullname, revolut_tag):
        print("[PAYOUT] Récompense réclamée.")

        # 🔐 Source de vérité : mise à jour par compte
        def _apply_gain(st):
            st["earnings_today_eur"] = st.get("earnings_today_eur", 0.0) + 5.0
            st["last_gain_ts"] = time.time()

        update_state(account_id, _apply_gain)

        # 🧠 Mémoire runtime (cache)
        get_guard().record_earning(5.0)

        return True

    print("[PAYOUT] Impossible de confirmer la réclamation.")
    return False
