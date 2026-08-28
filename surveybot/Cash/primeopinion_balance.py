from __future__ import annotations

import os
import re
import time
from typing import Optional, Tuple

from config import is_cta_intercept_only
from Management.notifier import send_telegram
from Survey.log_utils import log_info, log_debug

_TAG = "[PRIMEOPINION_BALANCE]"

# 569 Points = 5,0 € confirmé (Wilfried + texte DOM "Solde actuel: X Points
# (≈ Y €)"). Palier fixe, pas de calcul de conversion dynamique nécessaire.
CASHOUT_THRESHOLD_POINTS = 569.0
CASHOUT_AMOUNT_EUR = 5.0

_OPEN_MODAL_BTN_SEL = "button[data-test-id='open-claim-reward-modal-button']"
_REVOLUT_ACCORDION_MARKER_SEL = "div[data-test-id='reward-accordion-button']"
_REVOLUT_NAME_INPUT_SEL = "input[data-test-id='claim-reward-revolut-name-field-input']"
_REVOLUT_TAG_INPUT_SEL = "input[data-test-id='claim-reward-revolut-tag-field-input']"
# Même data-test-id que TopSurveys pour le bouton de confirmation finale (même
# kit de composants "claim-reward-revolut-*" déjà confirmé identique sur ce
# parcours) — NON CONFIRMÉ par capture DOM PrimeOpinion pour CET écran précis :
# personne n'a encore atteint 569 Points en conditions réelles. Repli texte
# prévu si absent (cf. _confirm_claim_screen).
_CONFIRM_CLAIM_BTN_SEL = "button[data-test-id='confirm-claim-button']"

_POINTS_BALANCE_RE = re.compile(r"solde\s*:?\s*([\d.,]+)\s*points", re.IGNORECASE)
_MODAL_BALANCE_RE = re.compile(
    r"solde\s+actuel\s*:?\s*([\d.,]+)\s*points\s*\(\s*[≈~]?\s*([\d.,]+)\s*€", re.IGNORECASE
)


def _to_float(raw: str) -> Optional[float]:
    try:
        return float(raw.replace("\xa0", "").replace(",", "."))
    except (ValueError, AttributeError):
        return None


def _body_text(driver) -> str:
    try:
        return driver.evaluate("() => document.body ? document.body.innerText : ''") or ""
    except Exception:
        return ""


def _read_points_balance(driver) -> Optional[float]:
    """
    Lecture du solde en Points depuis le texte visible de la page (ex: page
    surveys, libellé confirmé "Solde: {N} Points"). Scan texte ancré sur le
    libellé confirmé plutôt qu'un data-test-id non observé pour cet élément.
    """
    match = _POINTS_BALANCE_RE.search(_body_text(driver))
    if not match:
        return None
    return _to_float(match.group(1))


def _read_modal_balance(driver) -> Tuple[Optional[float], Optional[float]]:
    """
    Lecture best-effort de la ligne "Solde actuel: X Points (≈ Y €)" du modal
    de récompenses. Informatif uniquement (log) — le palier 569 Points / 5 €
    est fixe, aucun calcul de conversion dynamique n'est requis pour agir.
    """
    match = _MODAL_BALANCE_RE.search(_body_text(driver))
    if not match:
        return None, None
    return _to_float(match.group(1)), _to_float(match.group(2))


def _already_notified_today(account_id: str) -> Tuple[bool, str]:
    """
    Guard de déduplication partagé entre _notify_threshold_reached() et
    _notify_claim_failure() : une seule clé "primeopinion" par jour pour les
    deux (cf. State/account_state.py::has_notified_balance_today), pas une
    clé par type de message. Fail-open (retourne False) sur toute erreur
    Postgres/état — mieux vaut un doublon occasionnel qu'un silence complet.
    """
    from State.account_state import has_notified_balance_today, load_state
    from State.daily_target import today_str

    day = today_str()
    try:
        return has_notified_balance_today(load_state(account_id), "primeopinion", day), day
    except Exception as e:
        log_debug(_TAG, f"_already_notified_today() — lecture état échouée, fail-open : {e}")
        return False, day


def _mark_notified_today(account_id: str, day: str) -> None:
    from State.account_state import mark_notified_balance_today, update_state

    try:
        update_state(account_id, lambda st: mark_notified_balance_today(st, "primeopinion", day))
    except Exception as e:
        log_debug(_TAG, f"_mark_notified_today() — marquage notifié échoué (non bloquant) : {e}")


def _notify_threshold_reached(account_id: str, balance_points: float) -> None:
    already_notified, day = _already_notified_today(account_id)
    if already_notified:
        log_debug(
            _TAG,
            f"_notify_threshold_reached() — notification ignorée (déjà notifié aujourd'hui, compte={account_id})",
        )
        return

    tg_token = os.getenv("telegram_bot_token", "").strip()
    tg_chat = os.getenv("telegram_chat_id", "").strip()
    if not tg_token or not tg_chat:
        log_debug(_TAG, "_notify_threshold_reached() — credentials Telegram absents, notification ignorée")
        return
    msg = (
        f"[PRIMEOPINION][RETRAIT] Seuil franchi — compte : {account_id} | "
        f"solde : {balance_points:.0f} Points — tentative de réclamation Revolut "
        f"({CASHOUT_THRESHOLD_POINTS:.0f} Points / {CASHOUT_AMOUNT_EUR:.2f} €) en cours "
        "(premier passage réel sur PrimeOpinion, à superviser)."
    )
    try:
        send_telegram(msg, tg_token, tg_chat)
        log_info(_TAG, "_notify_threshold_reached() — notification Telegram envoyée")
    except Exception:
        pass
    _mark_notified_today(account_id, day)


def _notify_claim_failure(account_id: str, reason: str) -> None:
    already_notified, day = _already_notified_today(account_id)
    if already_notified:
        log_debug(
            _TAG,
            f"_notify_claim_failure() — notification ignorée (déjà notifié aujourd'hui, compte={account_id})",
        )
        return

    tg_token = os.getenv("telegram_bot_token", "").strip()
    tg_chat = os.getenv("telegram_chat_id", "").strip()
    if not tg_token or not tg_chat:
        return
    msg = f"[PRIMEOPINION][RETRAIT][ÉCHEC] compte : {account_id} | raison : {reason}"
    try:
        send_telegram(msg, tg_token, tg_chat)
    except Exception:
        pass
    _mark_notified_today(account_id, day)


def _click(driver, el) -> bool:
    """Clic réel conditionné par CTA_INTERCEPT_ONLY (interception sans navigation)."""
    try:
        el.evaluate("(e) => e.scrollIntoView({block: 'center'})")
    except Exception:
        pass
    if is_cta_intercept_only():
        log_info(_TAG, "_click() — CTA_INTERCEPT_ONLY actif, interception OK sans clic réel.")
        return False
    el.click()
    return True


def _open_claim_modal(driver) -> bool:
    try:
        btn = driver.wait_for_selector(_OPEN_MODAL_BTN_SEL, state="visible", timeout=10000)
    except Exception:
        log_info(_TAG, "_open_claim_modal() — bouton d'ouverture introuvable")
        return False

    if not _click(driver, btn):
        return False

    try:
        driver.wait_for_selector(_REVOLUT_ACCORDION_MARKER_SEL, state="attached", timeout=10000)
        return True
    except Exception:
        log_info(_TAG, "_open_claim_modal() — modal non détecté après clic (accordéon Revolut absent)")
        return False


def _open_revolut_accordion(driver):
    """
    Ouvre l'accordéon Revolut (bouton contenant div[data-test-id='reward-accordion-button']
    + span 'Revolut', confirmés par capture) et retourne son conteneur de
    contenu déplié, ou None.
    """
    try:
        btn = driver.wait_for_selector(
            "xpath=//button[contains(@class,'p-accordion-button')]"
            "[.//div[@data-test-id='reward-accordion-button']]"
            "[.//span[contains(normalize-space(.),'Revolut')]]",
            state="visible",
            timeout=10000,
        )
    except Exception:
        log_info(_TAG, "_open_revolut_accordion() — bouton accordéon Revolut introuvable")
        return None

    try:
        tab = btn.query_selector("xpath=./ancestor::*[contains(@class,'p-accordion-tab')][1]")
    except Exception:
        tab = None
    if tab is None:
        log_info(_TAG, "_open_revolut_accordion() — conteneur accordéon (p-accordion-tab) introuvable")
        return None

    already_active = "p-active" in (tab.get_attribute("class") or "")
    if not already_active:
        if not _click(driver, btn):
            return None
        time.sleep(1.5)

    deadline = time.time() + 8
    while time.time() < deadline:
        content = tab.query_selector(".p-accordion-content")
        if content is not None:
            return content
        time.sleep(0.3)

    log_info(_TAG, "_open_revolut_accordion() — contenu déplié non détecté après clic")
    return None


def _find_569_points_option(accordion_content):
    """
    Cherche la carte d'option "5 €/569 Points" dans l'accordéon Revolut
    déplié. Structure de classe exacte NON inspectée en détail côté
    PrimeOpinion (capture visuelle uniquement, cf. spec) — plusieurs
    stratégies successives par analogie avec le pattern reward-option déjà
    géré côté TopSurveys (Cash/payout.py, même kit "p-"), à valider/adapter
    en lisant le DOM réel si aucune ne matche.
    """
    strategies = (
        "[data-test-id='reward-option']",
        "[class*='reward-option']",
        "[class*='option-money']",
    )
    for selector in strategies:
        for card in accordion_content.query_selector_all(selector):
            try:
                text = card.inner_text() or ""
            except Exception:
                continue
            if "569" in text and re.search(r"points", text, re.IGNORECASE):
                return card

    try:
        return accordion_content.query_selector(
            "xpath=.//*[contains(normalize-space(.),'569') and contains(normalize-space(.),'Points')]"
        )
    except Exception:
        return None


def _is_option_locked(card) -> bool:
    """
    Détecte un verrouillage (icône cadenas / solde insuffisant) sans
    hypothèse fragile sur une classe exacte non confirmée : classe contenant
    'blocked'/'locked'/'disabled', ou élément enfant dont la classe/aria-label
    évoque un cadenas. Tout signal positif bloque le clic — en cas de doute,
    ne jamais cliquer une option potentiellement verrouillée.
    """
    try:
        cls = (card.get_attribute("class") or "").lower()
        if any(token in cls for token in ("blocked", "locked", "disabled")):
            return True
    except Exception:
        pass
    try:
        lock_marker = card.query_selector(
            "xpath=.//*[contains(translate(@class,'LOCK','lock'),'lock') "
            "or contains(translate(@aria-label,'LOCK','lock'),'lock') "
            "or contains(translate(@class,'CADENAS','cadenas'),'cadenas')]"
        )
        if lock_marker is not None:
            return True
    except Exception:
        pass
    return False


def _click_confirm_reclamation(driver) -> bool:
    """Bouton 'Réclamation' du footer du modal — sélecteur exact non confirmé, repli texte."""
    try:
        btn = driver.wait_for_selector(
            "xpath=//button[contains(normalize-space(.),'Réclamation')]",
            state="visible",
            timeout=10000,
        )
    except Exception:
        log_info(_TAG, "_click_confirm_reclamation() — bouton 'Réclamation' introuvable")
        return False

    try:
        if not btn.is_enabled() or bool(btn.get_attribute("disabled")):
            log_info(_TAG, "_click_confirm_reclamation() — bouton 'Réclamation' encore désactivé")
            return False
    except Exception:
        pass

    return _click(driver, btn)


def _confirm_claim_screen(driver, revolut_fullname: str = "", revolut_tag: str = "") -> bool:
    """
    Écran de confirmation post-'Réclamation'. AUCUNE CAPTURE DOM DISPONIBLE
    pour PrimeOpinion à ce jour (personne n'a encore atteint 569 Points en
    conditions réelles) — implémentation par analogie avec _confirm_claim de
    Cash/payout.py (mêmes data-test-id "claim-reward-revolut-*"/"confirm-claim-button"
    déjà confirmés identiques ailleurs sur ce parcours). À VÉRIFIER/ADAPTER au
    premier passage réel.
    """
    try:
        name_inp = driver.query_selector(_REVOLUT_NAME_INPUT_SEL)
        tag_inp = driver.query_selector(_REVOLUT_TAG_INPUT_SEL)
        if name_inp and revolut_fullname:
            name_inp.fill(revolut_fullname)
        if tag_inp and revolut_tag:
            tag_inp.fill(revolut_tag)
    except Exception:
        pass  # champs absents -> écran différent de celui anticipé, on continue quand même

    try:
        btn = driver.wait_for_selector(_CONFIRM_CLAIM_BTN_SEL, state="visible", timeout=8000)
        return _click(driver, btn)
    except Exception:
        pass

    try:
        btn = driver.wait_for_selector(
            "xpath=//button[contains(normalize-space(.),'Réclamer') "
            "or contains(normalize-space(.),'Confirmer') "
            "or contains(normalize-space(.),'Confirm')]",
            state="visible",
            timeout=5000,
        )
        return _click(driver, btn)
    except Exception:
        log_info(
            _TAG,
            "_confirm_claim_screen() — bouton de confirmation finale introuvable "
            "(écran non confirmé par capture DOM)",
        )
        return False


def _record_gain(account_id: str) -> None:
    """
    Alimente le même système de gain journalier que TopSurveys — State.daily_target
    (record_daily_earning_and_target) + Management.guards.runtime_guard
    (get_guard().record_earning) — même unité €, aucune réinvention d'un
    système d'unités Points pour le daily target : conversion actée
    uniquement au point d'enregistrement (569 Points → 5,0 €).
    """
    from State.account_state import update_state
    from State.daily_target import DAILY_TARGET_EUR, record_daily_earning_and_target
    from Management.guards.runtime_guard import get_guard

    def _apply_gain(st):
        record_daily_earning_and_target(
            st,
            amount_eur=CASHOUT_AMOUNT_EUR,
            daily_target_eur=DAILY_TARGET_EUR,
            now_ts=int(time.time()),
        )

    update_state(account_id, _apply_gain)
    get_guard().record_earning(CASHOUT_AMOUNT_EUR)
    log_info(_TAG, f"_record_gain() — gain enregistré : {CASHOUT_AMOUNT_EUR:.2f} € (compte {account_id})")


def check_and_claim_if_needed(
    driver,
    account_id: str,
    *,
    revolut_fullname: str = "",
    revolut_tag: str = "",
) -> bool:
    """
    À appeler après login() et à chaque retour effectif sur PrimeOpinion
    (handle_post_survey()), à l'image de check_and_cashout_if_needed
    (Cash/payout.py, TopSurveys) / check_balance_and_notify_if_needed
    (Cash/ysense_balance.py, ySense). Lit le solde en Points ; si >=
    CASHOUT_THRESHOLD_POINTS, notifie puis tente la réclamation Revolut (569
    Points / 5 €) en respectant CTA_INTERCEPT_ONLY à chaque clic, et
    n'enregistre le gain journalier que si le solde a effectivement baissé
    après confirmation.
    Non bloquant : toute erreur est loggée, jamais propagée à l'appelant.
    Retourne True si un retrait a été confirmé réussi, False sinon.
    """
    try:
        balance_before = _read_points_balance(driver)
    except Exception as e:
        log_debug(_TAG, f"check_and_claim_if_needed() — lecture solde échouée : {e}")
        return False

    if balance_before is None:
        log_debug(_TAG, "check_and_claim_if_needed() — solde introuvable sur la page")
        return False

    log_info(_TAG, f"check_and_claim_if_needed() — solde détecté : {balance_before:.0f} Points")

    if balance_before < CASHOUT_THRESHOLD_POINTS:
        return False

    _notify_threshold_reached(account_id, balance_before)

    if not _open_claim_modal(driver):
        _notify_claim_failure(account_id, "modal_ouverture_echouee")
        return False

    modal_points, modal_eur = _read_modal_balance(driver)
    if modal_points is not None:
        log_debug(
            _TAG,
            f"check_and_claim_if_needed() — solde modal : {modal_points:.0f} Points"
            + (f" (≈ {modal_eur} €)" if modal_eur is not None else ""),
        )

    accordion_content = _open_revolut_accordion(driver)
    if accordion_content is None:
        _notify_claim_failure(account_id, "accordeon_revolut_introuvable")
        return False

    option_card = _find_569_points_option(accordion_content)
    if option_card is None:
        log_info(_TAG, "check_and_claim_if_needed() — option 569 Points introuvable dans l'accordéon Revolut")
        _notify_claim_failure(account_id, "option_569_points_introuvable")
        return False

    if _is_option_locked(option_card):
        log_info(_TAG, "check_and_claim_if_needed() — option 569 Points encore verrouillée (cadenas détecté), abandon propre")
        return False

    if not _click(driver, option_card):
        return False  # CTA_INTERCEPT_ONLY — interception OK, pas de suite

    time.sleep(1.0)

    if not _click_confirm_reclamation(driver):
        _notify_claim_failure(account_id, "clic_reclamation_echoue")
        return False

    if not _confirm_claim_screen(driver, revolut_fullname, revolut_tag):
        _notify_claim_failure(account_id, "ecran_confirmation_echoue_ou_non_confirme")
        return False

    time.sleep(5)
    balance_after = _read_points_balance(driver)
    if balance_after is None or balance_after > balance_before - (CASHOUT_THRESHOLD_POINTS * 0.5):
        log_info(
            _TAG,
            "check_and_claim_if_needed() — solde inchangé après confirmation "
            f"({balance_after} vs {balance_before:.0f}) — retrait probablement échoué",
        )
        _notify_claim_failure(account_id, "solde_inchange_apres_confirmation")
        return False

    log_info(_TAG, "check_and_claim_if_needed() — retrait Revolut confirmé (569 Points / 5 €)")
    _record_gain(account_id)
    return True
