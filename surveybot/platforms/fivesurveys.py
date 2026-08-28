from __future__ import annotations

import os
import re
import time
from typing import List

from config import is_cta_intercept_only
from Management.notifier import send_telegram
from platforms.base import Platform
from preselection.question_analyzer import (
    click_participer_if_qualified,
    get_response_for_question,
    handle_disqualification_and_retry,
)
from preselection.response_executor import execute_response
from State.survey_memory import SurveySession, flush_disqualified, flush_qualified
from Survey.log_utils import log_info, log_debug

_TAG = "[FIVESURVEYS]"

# Landing marketing FR — porte le bouton d'ouverture de la modale d'auth.
_HOME_URL = "https://www.fivesurveys.com/fr-fr/"

# --- Auth : CONFIRMÉ par capture DOM (flux complet email → mot de passe) ---
# Le landing porte 3 boutons data-test-id="open-auth-modal-button" (header,
# section promo, section cta) — scope sur "header" pour cibler celui garanti
# visible sans scroll, même correctif déjà retenu pour HeyCash.
_OPEN_AUTH_MODAL_BTN_SEL = "header button[data-test-id='open-auth-modal-button']"
_MODAL_DIALOG_SEL = "[role='dialog']"
_EMAIL_INPUT_SEL = "input[data-test-id='check-email-field-input']"
_EMAIL_CONTINUE_BTN_SEL = "button[data-test-id='check-email-continue-button']"
_PASSWORD_INPUT_SEL = "input[data-test-id='sign-in-password-field-input']"
_LOGIN_SUBMIT_BTN_SEL = "button[data-test-id='sign-in-submit-button']"

# --- Dashboard --------------------------------------------------------
# CONFIRMÉ par capture DOM authentifiée : la carte porte directement
# class="list-item five-survey-tile survey-item" data-test-id="ps-survey-<uuid>".
# Même correctif que HeyCash : scope sur la classe "survey-item" en plus du
# préfixe "ps-survey-", pour exclure les sous-éléments partageant ce préfixe
# (ps-survey-rating-wrapper) des cartes "Jeux" (class="offer-item").
_SURVEYS_NAV_SEL = "[data-test-id='surveys-nav']"
_SURVEY_CARD_SEL = "div.survey-item[data-test-id^='ps-survey-']"
_RATING_WRAPPER_SEL = "[data-test-id='ps-survey-rating-wrapper']"
_RATING_AVERAGE_SEL = ".rating-average"
# CONFIRMÉ par capture DOM : bouton dédié dans la carte, sans data-test-id
# propre, distingué par sa classe "take-survey__button" (à la différence de
# TopSurveys/PrimeOpinion/HeyCash qui cliquent le conteneur de carte entier).
_TAKE_SURVEY_BUTTON_SEL = "button.take-survey__button"
_PS_POPUP_SEL = "[data-test-id='ps-popup-content-wrapper']"

# Signal de session authentifiée — CONFIRMÉ (surveys-nav ET user-balance sont
# tous deux visibles dans les captures du dashboard connecté).
_USER_BALANCE_SEL = "[data-test-id='user-balance']"
_AUTHENTICATED_SIGNAL_SEL = "[data-test-id='surveys-nav'], [data-test-id='user-balance']"

# Seuil de solde (€) déclenchant une notification — pas de réclamation
# automatique : notifier seulement. Confirmé par la mention "Complète 5
# sondages et obtiens 3,00€" affichée sur le dashboard.
_MIN_BALANCE_NOTIFY = 3.0

_DECLINE_LABELS = (
    "Je ne peux pas répondre à cette question",
    "Je ne peux pas répondre",
    "Je préfère ne pas répondre",
    "Prefer not to answer",
    "I prefer not to answer",
)
_SKIP_QUESTION_BTN_SEL = "button[data-test-id='ps-skip-question-button']"

_MAX_PRESELECTION_QUESTIONS = 30
_PRESELECTION_STUCK_THRESHOLD = 5
_MAX_ATTEMPTS = 3

_RATING_RE = re.compile(r"(\d+(?:[.,]\d+)?)")
_AMOUNT_RE = re.compile(r"(\d+(?:[.,]\d+)?)")


def _mask_secret(value: str) -> str:
    v = value or ""
    if len(v) < 2:
        return f"len={len(v)}"
    return f"len={len(v)} [{v[0]}…{v[-1]}]"


def _parse_rating(text: str):
    """
    Extrait la note moyenne chiffrée d'une carte (ex: "2.5", "2", "3").
    Retourne None si absente/illisible — couvre notamment la mention "Nouveau"
    (sondage sans vote), qui ne contient aucun chiffre.
    """
    if not text:
        return None
    match = _RATING_RE.search(text)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", "."))
    except ValueError:
        return None


def _parse_amount(text: str):
    """
    Extrait un montant décimal générique (ex: "3,00€", "$ 24.44") depuis un
    texte. Fonction distincte de _parse_rating (même forme de regex, mais
    domaine différent — solde vs note de carte) : aucune des deux n'est
    réutilisée pour l'autre usage.
    """
    if not text:
        return None
    match = _AMOUNT_RE.search(text)
    if not match:
        return None
    try:
        value = float(match.group(1).replace(",", "."))
    except ValueError:
        return None
    return value if value > 0 else None


def _check_balance_and_notify(page, account_id: str) -> None:
    """
    Lit le solde affiché (data-test-id='user-balance', jusqu'ici utilisé
    uniquement comme composant du signal de session authentifiée, jamais lu
    pour sa valeur) et, si >= seuil confirmé, envoie une notification
    Telegram. Même mécanisme que platforms/heycash.py et
    platforms/earnstar.py::_check_balance_and_notify
    (Management.notifier.send_telegram + telegram_bot_token/telegram_chat_id
    déjà en place dans le projet) — pas de canal réinventé. Non bloquant :
    toute erreur est journalisée, jamais propagée à l'appelant.
    """
    try:
        try:
            balance_el = page.wait_for_selector(_USER_BALANCE_SEL, state="attached", timeout=8000)
        except Exception:
            log_debug(_TAG, "_check_balance_and_notify() — élément solde introuvable après 8s")
            return
        balance = _parse_amount(balance_el.inner_text())
        if balance is None:
            log_debug(_TAG, "_check_balance_and_notify() — solde illisible")
            return
        log_debug(_TAG, f"_check_balance_and_notify() — solde courant : {balance}€")
        if balance >= _MIN_BALANCE_NOTIFY:
            from State.account_state import (
                has_notified_balance_today,
                load_state,
                mark_notified_balance_today,
                update_state,
            )
            from State.daily_target import today_str

            day = today_str()
            try:
                already_notified = has_notified_balance_today(load_state(account_id), "fivesurveys", day)
            except Exception as e:
                log_debug(_TAG, f"_check_balance_and_notify() — lecture état échouée, fail-open : {e}")
                already_notified = False

            if already_notified:
                log_debug(
                    _TAG,
                    f"_check_balance_and_notify() — notification ignorée (déjà notifié aujourd'hui, compte={account_id})",
                )
                return

            log_info(
                _TAG,
                f"_check_balance_and_notify() — seuil atteint (solde={balance}€ >= "
                f"{_MIN_BALANCE_NOTIFY}€, compte={account_id}) — notification à envoyer",
            )
            tg_token = os.getenv("telegram_bot_token", "").strip()
            tg_chat = os.getenv("telegram_chat_id", "").strip()
            if not tg_token or not tg_chat:
                log_debug(_TAG, "_check_balance_and_notify() — credentials Telegram absents, notification ignorée")
                return
            msg = (
                f"[FIVESURVEYS][SOLDE] compte : {account_id} | solde : {balance:.2f}€ "
                f">= seuil {_MIN_BALANCE_NOTIFY:.2f}€"
            )
            try:
                ok = send_telegram(msg, tg_token, tg_chat)
                log_debug(_TAG, f"_check_balance_and_notify() — send_telegram() ok={ok}")
            except Exception as e:
                ok = False
                log_debug(_TAG, f"_check_balance_and_notify() — envoi Telegram échoué (non bloquant) : {e}")

            if not ok:
                log_debug(_TAG, "_check_balance_and_notify() — marquage notifié sauté (envoi Telegram échoué)")
                return

            try:
                update_state(account_id, lambda st: mark_notified_balance_today(st, "fivesurveys", day))
            except Exception as e:
                log_debug(_TAG, f"_check_balance_and_notify() — marquage notifié échoué (non bloquant) : {e}")
    except Exception as e:
        log_debug(_TAG, f"_check_balance_and_notify() — exception non bloquante : {e}")


def _select_best_fivesurveys_card(page, excluded_uuids: set):
    """
    Scanne les cartes div.survey-item[data-test-id^='ps-survey-'] visibles.
    FiveSurveys n'affiche ni récompense ni durée par carte (à la différence du
    reste de la famille Prime Insights) — seul signal disponible : la note
    moyenne (ps-survey-rating-wrapper). Retient la carte à la meilleure note ;
    si aucune carte n'affiche de note chiffrée (mention "Nouveau" partout),
    retient la première carte de la liste. Retourne (card, uuid) ou None si
    aucune carte exploitable.
    """
    rated = []
    unrated = []
    raw_matches = page.query_selector_all(_SURVEY_CARD_SEL)
    for idx, card in enumerate(raw_matches, start=1):
        try:
            if not (card.is_visible() and card.is_enabled()):
                continue
            uuid_ = card.get_attribute("data-test-id")
            if uuid_ and uuid_ in excluded_uuids:
                continue

            unrated.append((card, uuid_))

            wrapper = card.query_selector(_RATING_WRAPPER_SEL)
            avg_el = wrapper.query_selector(_RATING_AVERAGE_SEL) if wrapper else None
            rating = _parse_rating(avg_el.inner_text() if avg_el else "")
            if rating is not None:
                rated.append((rating, card, uuid_))
        except Exception as e:
            log_debug(_TAG, f"select_survey() — carte #{idx} exception : {type(e).__name__}")

    if rated:
        rated.sort(key=lambda c: c[0], reverse=True)
        _, best_card, best_uuid = rated[0]
        return best_card, best_uuid

    if unrated:
        log_info(
            _TAG,
            f"select_survey() — aucune carte notée sur {len(unrated)} carte(s) "
            "valide(s) : sélection de la première de la liste",
        )
        best_card, best_uuid = unrated[0]
        return best_card, best_uuid

    log_info(
        _TAG,
        f"select_survey() — 0 candidat(e) valide sur {len(raw_matches)} "
        "carte(s) brute(s) détectée(s)",
    )
    return None


def _resolve_preselection_questions(page, api_key: str, session: SurveySession, uuid_) -> str:
    """
    Boucle bornée de résolution des questions de présélection intermédiaires
    du popup ps-*, entre le clic sur le bouton de participation et la
    détermination qualifié/disqualifié. Même pattern que HeyCash/PrimeOpinion.
    Retourne "qualified" | "disqualified" | "unresolved".
    """
    last_scan_key = None
    same_scan_count = 0

    for _ in range(1, _MAX_PRESELECTION_QUESTIONS + 1):
        if handle_disqualification_and_retry(page):
            log_info(_TAG, f"select_survey() — disqualification détectée en présélection (uuid={uuid_})")
            return "disqualified"

        try:
            question, answer, input_type = get_response_for_question(page, api_key, session=session)
        except Exception as e:
            log_info(_TAG, f"select_survey() — get_response_for_question() a échoué (uuid={uuid_}) : {e}")
            return "unresolved"

        try:
            cur_url = page.url
        except Exception:
            cur_url = ""
        scan_key = (cur_url, str(question)[:150] if question else "")
        if scan_key == last_scan_key:
            same_scan_count += 1
        else:
            last_scan_key = scan_key
            same_scan_count = 1
        if same_scan_count >= _PRESELECTION_STUCK_THRESHOLD:
            log_info(
                _TAG,
                f"select_survey() — même page scannée {_PRESELECTION_STUCK_THRESHOLD} fois "
                f"(uuid={uuid_}), abandon présélection",
            )
            return "unresolved"

        if isinstance(answer, dict) and answer.get("action"):
            action = (answer.get("action") or "").upper()

            if action == "SKIP":
                skipped = False
                for label in _DECLINE_LABELS:
                    try:
                        if execute_response(page, label):
                            skipped = True
                            break
                    except Exception:
                        continue
                if not skipped:
                    try:
                        skip_btn = page.query_selector(_SKIP_QUESTION_BTN_SEL)
                        if skip_btn:
                            skip_btn.click()
                            skipped = True
                    except Exception:
                        pass
                if not skipped:
                    log_info(_TAG, f"select_survey() — question sensible non déclinable (uuid={uuid_})")
                    return "unresolved"
                time.sleep(1.0)
                continue

            if action == "DISQUALIFIED":
                log_info(_TAG, f"select_survey() — disqualification détectée en présélection (uuid={uuid_})")
                return "disqualified"

            if action == "NOT_RETURNED":
                question, answer = None, None
            else:
                log_info(_TAG, f"select_survey() — action présélection inconnue ({action}, uuid={uuid_})")
                return "unresolved"

        if question and answer:
            try:
                success = execute_response(page, answer, input_type)
            except Exception as e:
                log_info(_TAG, f"select_survey() — execute_response() a échoué (uuid={uuid_}) : {e}")
                return "unresolved"
            if not success:
                log_info(_TAG, f"select_survey() — exécution de la réponse échouée (uuid={uuid_})")
                return "unresolved"
            time.sleep(1.5)
            continue

        if click_participer_if_qualified(page):
            return "qualified"

        log_info(_TAG, f"select_survey() — ni question ni qualification/disqualification détectée (uuid={uuid_})")
        return "unresolved"

    log_info(_TAG, f"select_survey() — budget de {_MAX_PRESELECTION_QUESTIONS} questions épuisé (uuid={uuid_})")
    return "unresolved"


class FiveSurveysPlatform(Platform):
    """
    Implémentation FiveSurveys — 5e plateforme de la famille Prime Insights
    (même infra Vue/ps-*, mêmes conventions data-test-id que TopSurveys/
    PrimeOpinion/HeyCash). Auth et dashboard CONFIRMÉS par capture DOM.
    Spécificité : sélection par note moyenne (pas de reward/durée par carte),
    et clic sur le bouton de participation dédié de la carte plutôt que sur
    son conteneur entier.
    """

    def login(self, driver, config: dict) -> bool:
        email = os.getenv("EMAIL") or config.get("Email", "")
        password = os.getenv("PASSWORD") or config.get("Password", "")
        self._api_key = (
            os.getenv("OPENAI_API_KEY")
            or config.get("openai_api_key")
            or config.get("OPENAI_API_KEY")
            or ""
        )
        log_debug(
            _TAG,
            f"login() — identifiants lus depuis config : email={email!r}, "
            f"password={_mask_secret(password)}",
        )
        log_info(_TAG, f"login() — navigation vers {_HOME_URL}")

        page = driver
        page.goto(_HOME_URL)

        try:
            open_modal_btn = page.wait_for_selector(
                _OPEN_AUTH_MODAL_BTN_SEL, state="visible", timeout=15000
            )
            open_modal_btn.click()
            log_debug(_TAG, "login() — bouton d'ouverture de la modale cliqué")
        except Exception as e:
            log_info(_TAG, f"login() — bouton open-auth-modal-button introuvable/inclickable : {e}")
            return False

        try:
            page.wait_for_selector(_MODAL_DIALOG_SEL, state="visible", timeout=10000)
        except Exception:
            log_info(_TAG, "login() — modale (role=dialog) non apparue après 10s")
            return False

        try:
            email_input = page.wait_for_selector(_EMAIL_INPUT_SEL, state="visible", timeout=10000)
        except Exception:
            log_info(_TAG, "login() — champ email introuvable dans la modale")
            return False

        email_input.fill(email)
        log_debug(_TAG, "login() — email saisi")

        try:
            continue_btn = page.wait_for_selector(_EMAIL_CONTINUE_BTN_SEL, state="visible", timeout=10000)
            continue_btn.click()
            log_debug(_TAG, "login() — bouton continue cliqué")
        except Exception as e:
            log_info(_TAG, f"login() — bouton continue introuvable/inclickable : {e}")
            return False

        try:
            pwd_input = page.wait_for_selector(_PASSWORD_INPUT_SEL, state="visible", timeout=15000)
        except Exception:
            log_info(_TAG, "login() — champ password introuvable après l'étape email")
            return False

        pwd_input.fill(password)
        log_debug(_TAG, "login() — mot de passe saisi")

        try:
            login_btn = page.wait_for_selector(_LOGIN_SUBMIT_BTN_SEL, state="visible", timeout=10000)
            login_btn.click()
            log_info(_TAG, "login() — bouton de soumission cliqué")
        except Exception as e:
            log_info(_TAG, f"login() — bouton de soumission introuvable/inclickable : {e}")
            return False

        try:
            page.wait_for_selector(_AUTHENTICATED_SIGNAL_SEL, state="attached", timeout=20000)
            log_info(_TAG, "login() — succès (signal authentifié détecté)")
            try:
                _check_balance_and_notify(page, os.getenv("ACCOUNT_ID") or "unknown")
            except Exception as e:
                log_debug(_TAG, f"login() — vérification solde post-login échouée (non bloquant) : {e}")
            return True
        except Exception:
            log_info(_TAG, "login() — signal authentifié non détecté après 20s, échec")
            return False

    def _resolve_api_key(self) -> str:
        return os.getenv("OPENAI_API_KEY") or getattr(self, "_api_key", "") or ""

    def select_survey(self, driver) -> bool:
        log_info(_TAG, "select_survey() called")
        page = driver

        from Survey.functions import _resolve_topsurveys_popups
        _resolve_topsurveys_popups(driver)

        api_key = self._resolve_api_key()
        if not api_key:
            log_info(
                _TAG,
                "select_survey() — OPENAI_API_KEY introuvable (env + cache login) : "
                "résolution des questions de présélection intermédiaires désactivée",
            )

        try:
            tab = page.wait_for_selector(_SURVEYS_NAV_SEL, state="visible", timeout=15000)
            tab.click()
        except Exception as e:
            log_info(_TAG, f"select_survey() — onglet surveys-nav introuvable/inclickable : {e}")
            return False

        try:
            page.wait_for_selector(_SURVEY_CARD_SEL, state="attached", timeout=20000)
        except Exception:
            log_debug(_TAG, "select_survey() — aucune carte détectée après 20s")

        excluded_uuids: set = set()

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            best = _select_best_fivesurveys_card(page, excluded_uuids)
            if best is None:
                log_info(_TAG, "select_survey() — aucun survey disponible → cooldown")
                from Management.guards.runtime_guard import get_guard, StopReason
                from Management.pause_policy import PausePolicy
                get_guard().pause(PausePolicy.MEDIUM_LONG_COOLDOWN, StopReason.NO_SURVEY_AVAILABLE)
                return False  # pause() lève SystemExit — jamais atteint

            card, uuid_ = best

            if is_cta_intercept_only():
                log_info(
                    _TAG,
                    f"select_survey() — carte trouvée (uuid={uuid_}) — "
                    "interception OK (CTA_INTERCEPT_ONLY actif), pas de clic réel.",
                )
                return False

            take_survey_btn = None
            try:
                take_survey_btn = card.query_selector(_TAKE_SURVEY_BUTTON_SEL)
            except Exception:
                take_survey_btn = None

            if take_survey_btn is None:
                log_info(_TAG, f"select_survey() — bouton 'Participe au sondage' introuvable sur la carte (uuid={uuid_})")
                excluded_uuids.add(uuid_ or f"_nobtn_{attempt}")
                continue

            try:
                take_survey_btn.click()
                log_debug(_TAG, f"select_survey() — bouton de participation cliqué (tentative {attempt}/{_MAX_ATTEMPTS}, uuid={uuid_})")
            except Exception as e:
                log_info(_TAG, f"select_survey() — clic bouton de participation échoué (uuid={uuid_}) : {e}")
                excluded_uuids.add(uuid_ or f"_noclick_{attempt}")
                continue

            try:
                page.wait_for_selector(_PS_POPUP_SEL, state="visible", timeout=15000)
            except Exception:
                log_debug(_TAG, "select_survey() — popup ps-* non visible après 15s, on tente quand même la résolution")

            if api_key:
                session = SurveySession()
                outcome = _resolve_preselection_questions(page, api_key, session, uuid_)
                try:
                    if outcome == "qualified":
                        flush_qualified(session)
                    elif outcome == "disqualified":
                        flush_disqualified(session)
                except Exception as e:
                    log_debug(_TAG, f"select_survey() — flush survey_memory échoué (uuid={uuid_}) : {e}")

                if outcome == "qualified":
                    log_info(_TAG, "select_survey() — survey qualifié, participation lancée")
                    return True

                if outcome == "disqualified":
                    log_info(_TAG, f"select_survey() — carte disqualifiée (uuid={uuid_}), nouvelle tentative")
                    excluded_uuids.add(uuid_ or f"_dq_{attempt}")
                    continue

                log_info(_TAG, f"select_survey() — résolution présélection abandonnée (uuid={uuid_}), nouvelle tentative")
                excluded_uuids.add(uuid_ or f"_unresolved_{attempt}")
                continue

            if handle_disqualification_and_retry(page):
                log_info(_TAG, f"select_survey() — carte disqualifiée (uuid={uuid_}), nouvelle tentative")
                excluded_uuids.add(uuid_ or f"_dq_{attempt}")
                continue

            if click_participer_if_qualified(page):
                log_info(_TAG, "select_survey() — survey qualifié, participation lancée")
                return True

            log_info(_TAG, f"select_survey() — ni qualification ni disqualification détectée (uuid={uuid_}), abandon tentative")
            excluded_uuids.add(uuid_ or f"_unknown_{attempt}")

        log_info(_TAG, f"select_survey() — budget de {_MAX_ATTEMPTS} tentatives épuisé sans succès")
        return False

    def handle_post_survey(self, driver, account_id: str) -> bool:
        log_info(_TAG, "handle_post_survey() called")
        page = driver

        from Survey.functions import _resolve_topsurveys_popups
        _resolve_topsurveys_popups(driver)

        if not self.is_on_platform(page):
            return False

        try:
            page.wait_for_selector(_SURVEYS_NAV_SEL, state="attached", timeout=5000)
        except Exception:
            return False

        try:
            page.wait_for_selector(_SURVEY_CARD_SEL, state="attached", timeout=8000)
        except Exception:
            log_debug(
                _TAG,
                "handle_post_survey() — surveys-nav présent mais aucune carte détectée, "
                "retour listing non confirmé",
            )
            return False

        log_info(_TAG, "handle_post_survey() — retour sur la liste de sondages détecté")

        try:
            _check_balance_and_notify(page, account_id)
        except Exception as e:
            log_debug(_TAG, f"handle_post_survey() — vérification solde échouée (non bloquant) : {e}")

        self.select_survey(page)
        return True

    def is_on_platform(self, driver) -> bool:
        try:
            url = (driver.url or "").lower()
        except Exception:
            return False
        return any(d in url for d in self.get_domains())

    def is_session_expired(self, driver) -> bool:
        try:
            page = driver
            page.goto(_HOME_URL)
            page.wait_for_selector(_AUTHENTICATED_SIGNAL_SEL, state="attached", timeout=8000)
            return False
        except Exception:
            return True

    def get_platform_name(self) -> str:
        return "fivesurveys"

    def get_home_url(self) -> str:
        return _HOME_URL

    def get_domains(self) -> List[str]:
        return ["fivesurveys.com"]
