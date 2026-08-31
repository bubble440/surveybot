from __future__ import annotations
import os
import re
import time
from typing import Tuple
from State.account_state import update_state, load_state
from State.daily_target import DAILY_TARGET_EUR, record_daily_earning_and_target, init_daily_balance_target, today_str
from Management.guards.runtime_guard import get_guard
from Management.notifier import send_telegram
from config import RUN_ENV, is_cta_intercept_only
# Seuil minimal réel pour déclencher un encaissement sur TopSurveys.
# Le modal ne propose que des options >= 5 €, donc ouvrir en dessous est inutile.
MIN_CASHOUT_EUR = 5.0

IS_LOCAL = RUN_ENV == "local"


# ---------- Licence ----------

def _increment_license_payout(amount_eur: float) -> None:
    """
    Incrémente total_payout_eur dans la table licenses après un retrait confirmé.
    Non bloquant : un échec est loggué mais ne stoppe pas le bot
    (le retrait est déjà effectué à ce stade).
    Actif uniquement en prod (RUN_ENV=prod).
    """
    if IS_LOCAL:
        return

    try:
        from preselection.license_guard import _get_license_key, _get_database_url
        license_key = _get_license_key()
        if not license_key:
            return
        database_url = _get_database_url()
        if not database_url:
            return

        import psycopg2
        conn = psycopg2.connect(database_url)
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT increment_license_payout(%s, %s)",
                    (license_key, amount_eur),
                )
        finally:
            conn.close()
    except Exception as exc:
        print(f"[PAYOUT][LICENSE][WARN] Impossible d'incrémenter total_payout_eur: {exc}")


# ---------- Helpers ----------

def _notify_cashout_failure(account_id: str, amount: float, email: str = "") -> None:
    """Envoie une notification Telegram si les credentials sont configurés."""
    tg_token = os.getenv("telegram_bot_token", "").strip()
    tg_chat  = os.getenv("telegram_chat_id", "").strip()
    if not tg_token or not tg_chat:
        return
    email_part = f" | email : {email}" if email else ""
    msg = f"[PAYOUT][ÉCHEC] Retrait échoué — compte : {account_id}{email_part} | montant : {amount:.2f} €"
    try:
        send_telegram(msg, tg_token, tg_chat)
    except Exception:
        pass

def _notify_cashout_result(
    account_id: str,
    amount: float,
    *,
    failed_methods: list,
    succeeded_method,
    email: str = "",
) -> None:
    """Notification unique après un cycle avec au moins une méthode échouée."""
    tg_token = os.getenv("telegram_bot_token", "").strip()
    tg_chat  = os.getenv("telegram_chat_id", "").strip()
    if not tg_token or not tg_chat:
        return
    email_part = f" | email : {email}" if email else ""
    if succeeded_method:
        msg = (
            f"[PAYOUT][INFO] Méthode(s) échouée(s) : {', '.join(failed_methods)} → "
            f"retrait effectué via {succeeded_method} — "
            f"compte : {account_id}{email_part} | montant : {amount:.2f} €"
        )
    else:
        msg = (
            f"[PAYOUT][ÉCHEC] Toutes méthodes échouées ({', '.join(failed_methods)}) — "
            f"compte : {account_id}{email_part} | montant : {amount:.2f} €"
        )
    try:
        send_telegram(msg, tg_token, tg_chat)
    except Exception:
        pass

def _js_click(driver, el):
    el.scroll_into_view_if_needed()
    if is_cta_intercept_only():
        print("[PAYOUT] CTA trouvé — interception OK (CTA_INTERCEPT_ONLY actif)")
        time.sleep(3)
        return
    el.click()
    time.sleep(3)  # laisser le temps à l'UI de réagir (ex: activer le bouton 'Choisis' après sélection)

def _find(driver, by, sel, timeout=10):
    # by ignoré : CSS si pas de // sinon xpath=
    pw_sel = f"xpath={sel}" if sel.startswith("//") or sel.startswith("./") else sel
    return driver.wait_for_selector(pw_sel, state="attached", timeout=timeout * 1000)

# ---------- Lecture du solde & ouverture du modal ----------

def _open_cashout_modal(driver) -> bool:
    """
    Clique le bouton 'Encaissement'
    DOM fourni:
    <button data-test-id="balance-card-cashout">Encaissement</button>
    """
    try:
        btn = _find(driver, None, "button[data-test-id='balance-card-cashout']")
        _js_click(driver, btn)
        time.sleep(3)  # laisser le temps au modal de s'ouvrir
        # attend l'apparition du conteneur modal
        _find(driver, None, ".rewards-modal-container")
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
        return driver.query_selector("button[data-test-id='reward-select-button']")
    except Exception:
        return None

def _wait_select_btn_enabled(driver, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        b = _get_select_btn(driver)
        if b and _is_enabled(b):
            return
        time.sleep(0.2)
    raise RuntimeError("reward-select-button never became enabled")

def _dispatch_mouse_sequence(driver, el) -> None:
    el.evaluate(
        "(el) => {"
        " el.scrollIntoView({block:'center'});"
        " for (const type of ['mouseover','mousemove','mousedown','mouseup','click']) {"
        "  el.dispatchEvent(new MouseEvent(type, {bubbles:true,cancelable:true,view:window}));"
        " }"
        "}"
    )

def _select_money_option_in_open_tab(driver, tab_el, amount="5") -> bool:
    """
    Dans un tab .p-active, sélectionne l'option 'amount €' (non .blocked)
    en cliquant le wrapper [data-test-id="reward-option"].
    """
    # Cible les spans '5 €' puis remonte au wrapper cliquable
    candidates = tab_el.query_selector_all(
        f"xpath=.//span[contains(@class,'option-money')][contains(normalize-space(.), '{amount}') and contains(normalize-space(.),'€')]"
    )
    for span in candidates:
        try:
            wrapper = span.query_selector(
                "xpath=./ancestor::*[@data-test-id='reward-option'][1]"
            )
            if wrapper is None:
                continue
            if "blocked" in (wrapper.get_attribute("class") or ""):
                continue

            # 1) scroll + click (conditionné CTA_INTERCEPT_ONLY)
            wrapper.scroll_into_view_if_needed()
            time.sleep(5)
            if is_cta_intercept_only():
                print("[PAYOUT] CTA reward-option trouvé — interception OK (CTA_INTERCEPT_ONLY actif)")
                return True
            try:
                wrapper.click()
                time.sleep(3)
            except Exception:
                # 2) hover + click
                try:
                    wrapper.hover()
                    wrapper.click()
                except Exception:
                    # 3) séquence d'événements souris JS (certains frameworks attendent ça)
                    _dispatch_mouse_sequence(driver, wrapper)

            # si ça a marché, le bouton 'Choisis' devient activable
            try:
                _wait_select_btn_enabled(driver, timeout=5)
                return True
            except Exception:
                # dernier essai : clic JS sur le <span>
                try:
                    _js_click(driver, span)
                    _wait_select_btn_enabled(driver, timeout=5)
                    return True
                except Exception:
                    continue

        except Exception:
            continue
    return False

def _accordion_open(driver, label_substr: str) -> bool:
    """Assure l'ouverture de l'accordéon par son titre."""
    try:
        btn = _find(
            driver,
            None,
            "//button[contains(@class,'p-accordion-button')][.//span[contains(normalize-space(.), %s)]]" %
            repr(label_substr)
        )
        tab = btn.query_selector("xpath=./ancestor::div[contains(@class,’p-accordion-tab’)]")
        if tab is None:
            return False
        if "p-active" not in (tab.get_attribute("class") or ""):
            _js_click(driver, btn)
            time.sleep(3)
        # s’assure que le contenu est présent
        deadline = time.time() + 5
        while time.time() < deadline:
            if tab.query_selector(".p-accordion-content"):
                break
            time.sleep(0.2)
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
        opt = tab_el.query_selector(
            "xpath=.//div[contains(@class,'reward-options')]"
            "//div[@data-test-id='reward-option' and not(contains(@class,'blocked'))]"
            "[.//span[contains(normalize-space(.),'5') and contains(normalize-space(.),'€')]]"
        )
        if opt is None:
            raise RuntimeError("opt not found")
        _js_click(None, opt)
        return True
    except Exception:
        try:
            # variation: cliquer directement sur le <span> '5 €'
            span = tab_el.query_selector(
                "xpath=.//span[contains(@class,'option-money')][contains(normalize-space(.),'5') and contains(normalize-space(.),'€')]"
            )
            if span is None:
                raise RuntimeError("span not found")
            _js_click(None, span)
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
        btn = driver.wait_for_selector(
            "button[data-test-id='reward-select-button']", state="visible", timeout=10000
        )
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
        None,
        "//div[contains(@class,'p-accordion-tab') and contains(@class,'p-active')][.//span[contains(normalize-space(.),'PayPal International')]]"
    )
    if not _select_money_option_in_open_tab(driver, tab, amount="5"):
        return False
    time.sleep(3)
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
        name_inp = driver.query_selector("input[data-test-id='claim-reward-revolut-name-field-input']")
        tag_inp  = driver.query_selector("input[data-test-id='claim-reward-revolut-tag-field-input']")
        if name_inp:
            name_inp.fill(fullname)
        if tag_inp:
            tag_inp.fill(tag)
    except Exception:
        # champs non présents (pas Revolut) -> OK
        pass

def _select_revolut_5_eur(driver) -> bool:
    if not _accordion_open(driver, "Revolut"):
        return False
    tab = _find(
        driver,
        None,
        "//div[contains(@class,'p-accordion-tab') and contains(@class,'p-active')][.//span[contains(normalize-space(.),'Revolut')]]"
    )
    if not _select_money_option_5_eur_in_open_tab(tab):
        return False
    time.sleep(2)
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
        btn = _find(driver, None, "button[data-test-id='confirm-claim-button']")
        _js_click(driver, btn)
        return True
    except Exception:
        return False

def _read_balance(driver) -> float:
    """
    Lecture robuste du solde TopSurveys.
    Fallbacks successifs basés sur le DOM réel.
    """
    import re

    candidates = []
    # 1️⃣ Méthode historique (si jamais ils réintroduisent le test-id)
    try:
        el = driver.query_selector("[data-test-id='balance-card-amount']")
        if el:
            candidates.append(el.inner_text())
    except Exception:
        pass

    # 2️⃣ DOM actuel : span contenant "€" dans balance-card-progress
    try:
        spans = driver.query_selector_all(".balance-card-progress span")
        for s in spans:
            txt = (s.inner_text() or "").strip()
            if "€" in txt and "/" not in txt:
                candidates.append(txt)
    except Exception:
        pass

    # 3️⃣ Fallback ultime : scan global (safe mais coûteux)
    if not candidates:
        try:
            spans = driver.query_selector_all("xpath=//span[contains(text(),'€')]")
            for s in spans:
                txt = (s.inner_text() or "").strip()
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
    min_amount_eur: float = MIN_CASHOUT_EUR,
    cashout_order: Tuple[str, str] = ("revolut", "paypal"),
    revolut_fullname: str = "",
    revolut_tag: str = "",
    email: str = "",
    platform=None,
):
    """
    - Lit le solde,
    - Si >= min_amount_eur (défaut 5 €, seuil minimum du modal TopSurveys),
      ouvre le modal,
    - Tente encaissement dans l'ordre `cashout_order` ('revolut' puis 'paypal' par défaut),
    - Confirme la réclamation sur la page suivante.
    Renvoie True si un retrait réel est confirmé, False si solde insuffisant (aucune tentative),
    None si un cashout a été tenté mais n'a pas abouti à une baisse réelle du solde.
    """

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
        _notify_cashout_failure(account_id, amount, email)
        return None  # tentative effectuée, échec

    # Boucle par méthode : sélection → confirm → vérification réelle du solde
    failed_methods = []
    succeeded_method = None

    for idx, method in enumerate(cashout_order):
        # Pour les tentatives suivantes, le modal doit être rouvert
        if idx > 0:
            time.sleep(2)
            if not _open_cashout_modal(driver):
                failed_methods.append(method)
                break

        if method == "revolut":
            selected = _select_revolut_5_eur(driver)
            if selected:
                print("[PAYOUT] Option Revolut 5 € sélectionnée.")
        elif method == "paypal":
            selected = _select_paypal_5_eur(driver)
            if selected:
                print("[PAYOUT] Option PayPal 5 € sélectionnée.")
        else:
            selected = False

        if not selected:
            failed_methods.append(method)
            continue

        time.sleep(0.4)
        if not _confirm_claim(driver, revolut_fullname, revolut_tag):
            failed_methods.append(method)
            continue

        # Source de vérité : le solde doit avoir effectivement baissé d'environ 5 €
        time.sleep(5)
        try:
            new_balance = _read_balance(driver)
        except Exception:
            failed_methods.append(method)
            continue

        if new_balance < amount - MIN_CASHOUT_EUR * 0.5:
            succeeded_method = method
            break

        print(f"[PAYOUT] [{method}] Solde inchangé après confirm ({new_balance:.2f}€ vs {amount:.2f}€) — retrait silencieusement échoué.")
        failed_methods.append(method)

    if succeeded_method is None:
        print("[PAYOUT] Aucune méthode n'a abouti à un retrait réel.")
        _notify_cashout_result(account_id, amount, failed_methods=failed_methods, succeeded_method=None, email=email)
        return None  # tentative effectuée, échec

    print("[PAYOUT] Récompense réclamée.")

    # Incrémenter le compteur de licence (non bloquant)
    _increment_license_payout(MIN_CASHOUT_EUR)

    if failed_methods:
        _notify_cashout_result(account_id, amount, failed_methods=failed_methods, succeeded_method=succeeded_method, email=email)

    # 🔐 Mise à jour de l'état — uniquement après confirmation d'un retrait réel
    # Portée plateforme : mêmes champs journaliers qu'avant, désormais scopés
    # sous state["platforms"][platform_name] au lieu de la racine — les
    # fonctions génériques (init_daily_balance_target/record_daily_earning_and_
    # target) restent inchangées, on leur passe simplement ce sous-dict.
    platform_name = platform.get_platform_name() if platform else "topsurveys"
    _cashout_result = {"daily_stop": False}

    def _apply_gain(st):
        _today = today_str()
        platform_state = st.setdefault("platforms", {}).setdefault(platform_name, {})
        init_daily_balance_target(platform_state, amount, _today)

        start = float(platform_state.get("daily_balance_start", {}).get(_today, amount))
        gained_prev = float(platform_state.get("daily_balance_gained", {}).get(_today, 0.0))
        gain_avant_retrait = max(0.0, amount - start)
        gain_total = gained_prev + gain_avant_retrait

        platform_state.setdefault("daily_balance_gained", {})[_today] = gain_total

        if gain_total >= DAILY_TARGET_EUR:
            _cashout_result["daily_stop"] = True
        else:
            solde_post_retrait = amount - MIN_CASHOUT_EUR
            platform_state.setdefault("daily_balance_target", {})[_today] = (
                solde_post_retrait + (DAILY_TARGET_EUR - gain_total)
            )

        record_daily_earning_and_target(
            platform_state,
            amount_eur=5.0,
            daily_target_eur=DAILY_TARGET_EUR,
            now_ts=int(time.time()),
        )

    update_state(account_id, _apply_gain)

    # 🧠 Mémoire runtime (cache)
    get_guard().record_earning(5.0)

    if _cashout_result["daily_stop"]:
        from Management.pause_policy import PausePolicy
        from Management.guards.runtime_guard import StopReason
        print(f"[DAILY_STOP] gain journalier >= {DAILY_TARGET_EUR}€ après retrait → arrêt journalier")
        get_guard().pause(PausePolicy.DAILY_RESET, StopReason.DAILY_TARGET_REACHED)
        return True  # jamais atteint (pause lève SystemExit)

    return True

def _payout_and_check_daily_stop(driver, account_id: str, email: str = "", platform=None) -> bool:
    """
    À appeler à chaque retour sur TopSurveys. Vérifie dans l'ordre :
      1) Solde >= 5€  → retrait automatique (best-effort)
      2) Objectif journalier (1€) atteint → DAILY STOP (lève SystemExit via guard.pause)
    Retourne False si tout va bien (le bot peut continuer).
    Retourne True / lève SystemExit si DAILY STOP déclenché.
    """
    from Management.guards.runtime_guard import StopReason
    from Management.pause_policy import PausePolicy
    from global_config import PLATFORM_DAILY_TARGET

    platform_name = platform.get_platform_name() if platform else "topsurveys"

    # 1) Retrait uniquement si solde >= 5 € (MIN_CASHOUT_EUR) :
    #    le modal TopSurveys ne propose que des options >= 5 €,
    #    donc l'ouverture en dessous de ce seuil est inutile.
    cashout_result = False
    try:
        cashout_result = check_and_cashout_if_needed(
            driver,
            account_id=account_id,
            min_amount_eur=MIN_CASHOUT_EUR,
            cashout_order=("revolut", "paypal"),
            revolut_fullname="",
            revolut_tag="",
            email=email,
            platform=platform,
        )
    except Exception as e:
        print(f"[PAYOUT][WARN] retour TopSurveys: {e}")

    # Si un cashout a été tenté mais a échoué (solde inchangé), on ne fait pas
    # de vérification daily stop ce cycle : la target en base peut être corrompue
    # et le solde n'a pas évolué → pas de motif d'arrêt.
    if cashout_result is None:
        return False

    # 2) DAILY STOP basé sur le solde courant vs objectif journalier
    guard = get_guard()

    try:
        balance = _read_balance(driver)
    except Exception as e:
        print(f"[PAYOUT][WARN] lecture solde pour daily stop: {e}")
        return False

    _today = today_str()
    update_state(account_id, lambda st: init_daily_balance_target(
        st.setdefault("platforms", {}).setdefault(platform_name, {}), balance, _today
    ))

    state = load_state(account_id)
    platform_state = state.get("platforms", {}).get(platform_name, {})

    # Recalcul défensif de la target depuis les champs source de vérité.
    # Évite les faux DAILY_STOP causés par une valeur daily_balance_target corrompue.
    start = float(platform_state.get("daily_balance_start", {}).get(_today, balance))
    gained = float(platform_state.get("daily_balance_gained", {}).get(_today, 0.0))
    target = (start - gained) + PLATFORM_DAILY_TARGET["topsurveys"]

    if balance >= target:
        print(f"[DAILY_STOP] solde {balance:.2f}€ >= objectif {target:.2f}€ → arrêt journalier")
        guard.pause(PausePolicy.DAILY_RESET, StopReason.DAILY_TARGET_REACHED)
        return True  # jamais atteint (pause lève SystemExit)

    return False