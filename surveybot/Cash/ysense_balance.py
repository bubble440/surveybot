from __future__ import annotations

import os
import re
from typing import Optional

from config import is_cta_intercept_only
from Management.notifier import send_telegram
from Survey.log_utils import log_info, log_debug

_TAG = "[YSENSE_BALANCE]"

# $27.80 correspond au premier palier de retrait PayPal EUR observé sur
# /rewards/PayPal-EUR (affiché "$27.80", équivalent à €25) — même unité
# (dollars) que le solde affiché dans le dropdown du header.
CASHOUT_THRESHOLD_USD = 27.80

_REWARDS_URL = "https://www.ysense.com/rewards"


def _read_balance_usd(driver) -> Optional[float]:
    """
    Lecture du solde ySense depuis le dropdown du menu utilisateur
    (#ysnNavbarRight .dropdown-menu), présent dans le DOM même fermé
    (masqué uniquement par CSS, pas retiré).
    DOM confirmé : <li><a>Balance <strong>$0.57</strong></a></li>
    """
    try:
        items = driver.query_selector_all("#ysnNavbarRight .dropdown-menu li")
    except Exception:
        return None

    for li in items:
        try:
            text = (li.inner_text() or "").strip()
        except Exception:
            continue
        if not text.lower().startswith("balance"):
            continue
        try:
            strong = li.query_selector("strong")
            raw = (strong.inner_text() if strong else text) or ""
        except Exception:
            raw = text
        match = re.search(r"\d+(?:\.\d+)?", raw.replace(",", "."))
        if match:
            try:
                return float(match.group(0))
            except ValueError:
                return None
    return None


def _notify_manual_withdrawal(account_id: str, balance: float) -> None:
    tg_token = os.getenv("telegram_bot_token", "").strip()
    tg_chat = os.getenv("telegram_chat_id", "").strip()
    if not tg_token or not tg_chat:
        log_debug(_TAG, "_notify_manual_withdrawal() — credentials Telegram absents, notification ignorée")
        return
    msg = (
        f"[YSENSE][RETRAIT MANUEL REQUIS] compte : {account_id} | "
        f"solde : ${balance:.2f} — page PayPal EUR atteinte, retrait à faire manuellement."
    )
    try:
        send_telegram(msg, tg_token, tg_chat)
        log_info(_TAG, "_notify_manual_withdrawal() — notification Telegram envoyée")
    except Exception:
        pass


def _navigate_to_paypal_eur_reward(driver) -> bool:
    """
    Navigue vers /rewards puis clique le lien de récompense PayPal EUR
    (title="PayPal EUR", href="/rewards/PayPal-EUR") pour atteindre sa page
    de détail. Ne clique jamais le bouton de retrait ("Redeem") de cette page.
    """
    try:
        driver.goto(_REWARDS_URL)
    except Exception as e:
        log_info(_TAG, f"_navigate_to_paypal_eur_reward() — navigation /rewards échouée : {e}")
        return False

    try:
        link = driver.wait_for_selector(
            "a[title='PayPal EUR']", state="attached", timeout=15000
        )
    except Exception:
        log_info(_TAG, "_navigate_to_paypal_eur_reward() — lien PayPal EUR introuvable sur /rewards")
        return False

    if is_cta_intercept_only():
        log_info(
            _TAG,
            "_navigate_to_paypal_eur_reward() — lien PayPal EUR trouvé — "
            "interception OK (CTA_INTERCEPT_ONLY actif), pas de clic réel.",
        )
        return False

    try:
        link.click()
    except Exception as e:
        log_info(_TAG, f"_navigate_to_paypal_eur_reward() — clic sur lien PayPal EUR échoué : {e}")
        return False

    try:
        driver.wait_for_function(
            "() => window.location.pathname.toLowerCase().includes('/rewards/paypal-eur')",
            timeout=15000,
        )
    except Exception:
        log_info(_TAG, "_navigate_to_paypal_eur_reward() — page de détail PayPal EUR non atteinte après clic")
        return False

    log_info(_TAG, "_navigate_to_paypal_eur_reward() — page de détail PayPal EUR atteinte")
    return True


def check_balance_and_notify_if_needed(driver, account_id: str) -> None:
    """
    À appeler après login() et à chaque retour effectif sur ySense
    (handle_post_survey()). Lit le solde ; si >= CASHOUT_THRESHOLD_USD,
    navigue vers la page de détail PayPal EUR et notifie qu'un retrait
    manuel doit être effectué. Aucun clic sur le bouton de retrait.
    Non bloquant : toute erreur est loggée, jamais propagée à l'appelant.
    """
    try:
        balance = _read_balance_usd(driver)
    except Exception as e:
        log_debug(_TAG, f"check_balance_and_notify_if_needed() — lecture solde échouée : {e}")
        return

    if balance is None:
        log_debug(_TAG, "check_balance_and_notify_if_needed() — solde introuvable dans le dropdown")
        return

    log_info(_TAG, f"check_balance_and_notify_if_needed() — solde détecté : ${balance:.2f}")

    if balance < CASHOUT_THRESHOLD_USD:
        return

    if not _navigate_to_paypal_eur_reward(driver):
        return

    _notify_manual_withdrawal(account_id, balance)
