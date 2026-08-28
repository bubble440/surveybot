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

_TAG = "[EARNSTAR]"

# Landing marketing FR — CONFIRMÉ par capture DOM (champ email directement sur
# cette page, pas de modale préalable). Seule URL certaine (cf. get_home_url).
_HOME_URL = "https://www.earnstar.com/fr-fr/"

# --- Auth : CONFIRMÉ par capture DOM (flux complet email → mot de passe) ---
# Convention d'attribut différente de HeyCash/PrimeOpinion : ces champs
# utilisent data-test="...", pas data-test-id="..." (confirmé par capture,
# cf. BOT_EVOLUTION_MEMORY.md / spec de ce patch). check-email-submit est un
# <button type="submit"> (libellé "Continue"), pas un <input> comme décrit
# dans la spec initiale — écart signalé ici, sélecteur corrigé sur le DOM réel.
_EMAIL_INPUT_SEL = "input[data-test='check-email']"
_EMAIL_CONTINUE_BTN_SEL = "button[data-test='check-email-submit']"
_MODAL_DIALOG_SEL = "[role='dialog']"
_PASSWORD_INPUT_SEL = "input[data-test='sign-in-password']"
_LOGIN_SUBMIT_BTN_SEL = "button[data-test='sign-in-submit']"

# --- Dashboard : NON CONFIRMÉ par capture EarnStar authentifiée (aucune
# disponible à ce jour). Composant tiers "Prime Insights" probablement
# réutilisé tel quel (mêmes classes p-text/p-btn/p-progress, mêmes libellés
# "Solde"/"Sondages" que TopSurveys/PrimeOpinion/HeyCash) — sélecteurs repris
# par analogie depuis les captures de référence PrimeOpinion authentifié.
# Toute divergence réelle doit apparaître dans les logs ci-dessous plutôt que
# provoquer un échec silencieux.
_SURVEYS_NAV_SEL = "[data-test-id='surveys-nav']"
_USER_BALANCE_SEL = "[data-test-id='user-balance']"
_AUTHENTICATED_SIGNAL_SEL = "[data-test-id='surveys-nav'], [data-test-id='user-balance']"
_PS_POPUP_SEL = "[data-test-id='ps-popup-content-wrapper']"
_SURVEY_TIME_SEL = "[data-test-id='ps-survey-item-time']"
_SURVEY_REWARD_AMOUNT_SEL = "[data-test-id='ps-reward-amount']"
# Préfixe partagé par la vraie carte ET par des sous-éléments internes
# (ps-survey-item-time, ps-survey-rating-wrapper — cf. correctif documenté
# dans heycash.py). Sans classe CSS EarnStar confirmée pour scoper le
# sélecteur (aucune capture authentifiée disponible), la carte réelle est
# distinguée par la forme de son suffixe : un UUID (ps-survey-<uuid>), jamais
# partagé par ces sous-éléments — cf. _select_best_earnstar_card().
_SURVEY_CARD_ATTR_PREFIX = "[data-test-id^='ps-survey-']"
_SURVEY_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)

# --- Popup promo "Joue et gagne !" : CONFIRMÉ par capture DOM EarnStar
# authentifiée réelle (accueil ET liste de sondages). Bloque tout clic
# derrière elle (div.p-modal-mask) — cause confirmée du timeout observé sur
# le clic de l'onglet surveys-nav ("subtree intercepts pointer events").
# L'icône croix elle-même n'est qu'un SVG brut portant un hash Vue
# scoped-style (data-v-af2caffe) non stable d'un build à l'autre — non
# utilisé comme sélecteur. Le bouton qui l'entoure porte en revanche un
# data-test-id stable : "close-modal-button". Attention : distinct de
# "modal-close-button" (autre bouton de fermeture, vu sur la modale d'auth
# de la page marketing) — ne pas confondre les deux.
_PLAY_AND_EARN_POPUP_SEL = "[data-test-id='ps-play-and-earn-popup']"
_PLAY_AND_EARN_CLOSE_BTN_SEL = "[data-test-id='close-modal-button']"

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

# Seuil de solde (€) déclenchant une notification — pas de réclamation
# automatique, sur demande explicite : notifier seulement (cf. spec, seuil 5€).
_MIN_BALANCE_NOTIFY = 5.0

_TIME_RE = re.compile(r"(\d+)\s*min", re.IGNORECASE)
_AMOUNT_RE = re.compile(r"(\d+(?:[.,]\d+)?)")


def _mask_secret(value: str) -> str:
    v = value or ""
    if len(v) < 2:
        return f"len={len(v)}"
    return f"len={len(v)} [{v[0]}…{v[-1]}]"


def _parse_minutes(text: str):
    if not text:
        return None
    match = _TIME_RE.search(text)
    if not match:
        return None
    try:
        value = int(match.group(1))
    except ValueError:
        return None
    return value if value > 0 else None


def _parse_amount(text: str):
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


def _select_best_earnstar_card(page, excluded_uuids: set):
    """
    Stratégie de sélection propre à EarnStar (distincte de celles de HeyCash/
    PrimeOpinion) : scanne tous les éléments [data-test-id^='ps-survey-']
    visibles, ne retient que ceux dont le suffixe est un UUID (la vraie carte,
    cf. _SURVEY_UUID_RE) — écarte silencieusement les sous-éléments partageant
    le même préfixe (ps-survey-item-time, ps-survey-rating-wrapper) sans
    dépendre d'une classe CSS EarnStar non confirmée. Score reward(€)/durée(min).
    Retourne (card, uuid) ou None si aucune carte exploitable.
    """
    candidates = []
    raw_matches = page.query_selector_all(_SURVEY_CARD_ATTR_PREFIX)
    real_cards = 0
    # Diagnostic bug #2 (cf. spec) : sans capture DOM authentifiée EarnStar ni
    # log DEBUG par carte disponibles au moment du diagnostic, on enrichit ici
    # la distinction "élément absent" vs "élément présent mais texte non
    # parsable" plutôt que de deviner un nouveau sélecteur non vérifié.
    missing_time_el = 0
    missing_reward_el = 0
    time_unparsable = 0
    reward_unparsable = 0
    for idx, el in enumerate(raw_matches, start=1):
        try:
            tid = el.get_attribute("data-test-id") or ""
            uuid_ = tid[len("ps-survey-"):]
            if not _SURVEY_UUID_RE.match(uuid_):
                continue
            real_cards += 1

            if not (el.is_visible() and el.is_enabled()):
                continue
            if uuid_ in excluded_uuids:
                continue

            time_el = el.query_selector(_SURVEY_TIME_SEL)
            reward_el = el.query_selector(_SURVEY_REWARD_AMOUNT_SEL)
            time_text = time_el.inner_text() if time_el else None
            reward_text = reward_el.inner_text() if reward_el else None
            duration = _parse_minutes(time_text or "")
            reward = _parse_amount(reward_text or "")
            if reward is None or duration is None:
                reasons = []
                if time_el is None:
                    missing_time_el += 1
                    reasons.append("time_el absent (_SURVEY_TIME_SEL)")
                elif duration is None:
                    time_unparsable += 1
                    reasons.append(f"time non parsable ({time_text!r})")
                if reward_el is None:
                    missing_reward_el += 1
                    reasons.append("reward_el absent (_SURVEY_REWARD_AMOUNT_SEL)")
                elif reward is None:
                    reward_unparsable += 1
                    reasons.append(f"reward non parsable ({reward_text!r})")
                raw_sample = ((el.inner_text() or "").strip())[:150]
                log_debug(
                    _TAG,
                    f"select_survey() — carte #{idx} (uuid={uuid_}) ignorée : {'; '.join(reasons)} "
                    f"| texte brut carte={raw_sample!r}",
                )
                continue

            score = reward / duration
            candidates.append((score, el, uuid_))
        except Exception as e:
            log_debug(_TAG, f"select_survey() — carte #{idx} exception : {type(e).__name__}")

    if real_cards == 0 and raw_matches:
        log_info(
            _TAG,
            f"select_survey() — {len(raw_matches)} élément(s) [data-test-id^='ps-survey-'] "
            "détecté(s) mais aucun avec suffixe UUID : dashboard EarnStar potentiellement "
            "différent de l'hypothèse Prime Insights (structure non confirmée) — vérifier.",
        )

    if not candidates:
        log_info(
            _TAG,
            f"select_survey() — 0 candidat(e) valide sur {real_cards} carte(s) réelle(s) : "
            f"time_absent={missing_time_el} time_illisible={time_unparsable} "
            f"reward_absent={missing_reward_el} reward_illisible={reward_unparsable} "
            "(détail par carte en LOG_LEVEL=DEBUG)",
        )
        return None
    candidates.sort(key=lambda c: c[0], reverse=True)
    _, best_card, best_uuid = candidates[0]
    return best_card, best_uuid


def _resolve_preselection_questions(page, api_key: str, session: SurveySession, uuid_) -> str:
    """
    Boucle bornée de résolution des questions de présélection intermédiaires
    du popup ps-*. Vérification de disqualification en tête de boucle (même
    correctif que heycash.py — l'overlay de disqualification est distinct du
    wrapper ps-popup-content-wrapper et peut rester lu comme question active
    sinon), avant get_response_for_question().
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


def _check_balance_and_notify(page, account_id: str) -> None:
    """
    Lit le solde affiché et, si >= seuil configuré, envoie une notification
    Telegram. Sélecteur _USER_BALANCE_SEL non confirmé sur EarnStar (cf.
    en-tête de fichier) : wait_for_selector borné, échec = log debug seul,
    jamais bloquant pour le flux appelant.
    Envoi Telegram : mécanisme déjà existant du projet (Management.notifier.
    send_telegram + variables d'environnement telegram_bot_token/
    telegram_chat_id), même schéma que Cash/ysense_balance.py::
    _notify_manual_withdrawal — pas de canal réinventé, pas de plomberie
    notify_fn à travers login()/handle_post_survey() (les identifiants
    Telegram sont globaux au process, pas propres à un appel).
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
                f"[EARNSTAR][SOLDE] compte : {account_id} | solde : {balance:.2f}€ "
                f">= seuil {_MIN_BALANCE_NOTIFY:.2f}€"
            )
            try:
                ok = send_telegram(msg, tg_token, tg_chat)
                log_debug(_TAG, f"_check_balance_and_notify() — send_telegram() ok={ok}")
            except Exception as e:
                log_debug(_TAG, f"_check_balance_and_notify() — envoi Telegram échoué (non bloquant) : {e}")
    except Exception as e:
        log_debug(_TAG, f"_check_balance_and_notify() — exception non bloquante : {e}")


_PS_PLAY_AND_EARN_TAG = "[PS_PLAY_AND_EARN_POPUP]"


def _close_ps_play_and_earn_popup(driver):
    """
    Ferme la popup promotionnelle Prime Insights "Joue et gagne !"
    ([data-test-id='ps-play-and-earn-popup']) qui bloque tout clic derrière
    elle (div.p-modal-mask), observée sur EarnStar en accueil ET en liste de
    sondages — cause confirmée d'un timeout Playwright sur le clic de
    l'onglet surveys-nav dans select_survey(). Fonction générique et
    réutilisable, sans aucune logique propre à une plateforme dans son guard
    (sélecteurs de l'infrastructure tierce Prime Insights partagée, même
    convention data-test-id="ps-*" que le reste du widget) : appelable telle
    quelle depuis n'importe quel module platforms/*.py. Non intégrée au
    dispatcher partagé Survey.functions._resolve_topsurveys_popups faute de
    confirmation qu'elle apparaît sur TopSurveys/PrimeOpinion/HeyCash —
    appelée uniquement depuis earnstar.py pour l'instant.

    Guard DOM strict : conteneur [data-test-id='ps-play-and-earn-popup']
    visible, contenant le bouton [data-test-id='close-modal-button'] — la
    classe partagée p-modal-mask seule ne suffit pas à distinguer cette
    popup des autres partageant la même classe (cf. popup "Sélectionner les
    appareils", guard distinct).

    Valeur de retour (tri-état, même convention que
    Survey.functions._handle_topsurveys_streak_complete_popup) :
    - True  : popup détectée et fermée (bouton cliqué, ou interception
              CTA_INTERCEPT_ONLY).
    - False : popup détectée mais fermeture impossible (bouton introuvable
              ou clic échoué).
    - None  : popup non détectée.
    """
    try:
        popup = driver.query_selector(_PLAY_AND_EARN_POPUP_SEL)
        if not popup or not popup.is_visible():
            return None
    except Exception as e:
        log_debug(_PS_PLAY_AND_EARN_TAG, f"exception pendant le scan : {e}")
        return None

    log_info(_PS_PLAY_AND_EARN_TAG, "popup 'Joue et gagne !' détectée - fermeture...")

    try:
        close_btn = popup.query_selector(_PLAY_AND_EARN_CLOSE_BTN_SEL)
    except Exception as e:
        log_info(_PS_PLAY_AND_EARN_TAG, f"erreur lecture DOM du bouton de fermeture : {e}")
        return False

    if not close_btn:
        log_info(_PS_PLAY_AND_EARN_TAG, "bouton de fermeture introuvable")
        return False

    if is_cta_intercept_only():
        log_info(
            _PS_PLAY_AND_EARN_TAG,
            "bouton de fermeture trouvé - interception OK (CTA_INTERCEPT_ONLY actif), pas de clic réel.",
        )
        return True

    try:
        close_btn.click(timeout=3000)
        log_debug(_PS_PLAY_AND_EARN_TAG, "bouton de fermeture cliqué.")
        time.sleep(0.5)
        return True
    except Exception as e:
        log_info(_PS_PLAY_AND_EARN_TAG, f"clic bouton de fermeture échoué : {e}")
        return False


class EarnStarPlatform(Platform):
    """
    Implémentation EarnStar — quatrième plateforme Prime Insights. Auth
    CONFIRMÉE par capture DOM (email sur la page marketing, modale mot de
    passe). Dashboard/listing de sondages NON CONFIRMÉS par capture EarnStar
    authentifiée : sélecteurs repris par analogie avec TopSurveys/PrimeOpinion/
    HeyCash (même éditeur, même infrastructure tierce probable), toute
    divergence réelle étant signalée en log plutôt que supposée silencieusement.
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

        # --- Étape 1 : email directement sur la page marketing (pas de modale
        # préalable) — CONFIRMÉ par capture DOM ---
        try:
            email_input = page.wait_for_selector(_EMAIL_INPUT_SEL, state="visible", timeout=20000)
        except Exception:
            log_info(_TAG, "login() — timeout : champ email introuvable")
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

        # --- Étape 2 : modale mot de passe injectée après soumission de l'email
        # — CONFIRMÉ par capture DOM (role="dialog", en-tête "Se connecter") ---
        try:
            page.wait_for_selector(_MODAL_DIALOG_SEL, state="visible", timeout=15000)
        except Exception:
            log_info(_TAG, "login() — modale (role=dialog) non apparue après 15s")
            return False

        try:
            pwd_input = page.wait_for_selector(_PASSWORD_INPUT_SEL, state="visible", timeout=10000)
        except Exception:
            log_info(_TAG, "login() — champ password introuvable dans la modale")
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

        # --- Étape 3 : signal authentifié — NON CONFIRMÉ sur EarnStar (cf.
        # en-tête de fichier), auto-validation avant retour ---
        try:
            page.wait_for_selector(_AUTHENTICATED_SIGNAL_SEL, state="attached", timeout=20000)
            log_info(_TAG, "login() — succès (signal authentifié détecté)")
            try:
                _check_balance_and_notify(page, os.getenv("ACCOUNT_ID") or "unknown")
            except Exception as e:
                log_debug(_TAG, f"login() — vérification solde post-login échouée (non bloquant) : {e}")
            return True
        except Exception:
            log_info(
                _TAG,
                "login() — signal authentifié non détecté après 20s : soit échec réel, soit "
                "hypothèse dashboard (surveys-nav/user-balance) incorrecte pour EarnStar — à vérifier.",
            )
            return False

    def _resolve_api_key(self) -> str:
        return os.getenv("OPENAI_API_KEY") or getattr(self, "_api_key", "") or ""

    def select_survey(self, driver) -> bool:
        log_info(_TAG, "select_survey() called")
        page = driver

        from Survey.functions import _resolve_topsurveys_popups
        _resolve_topsurveys_popups(driver)

        # Popup promo "Joue et gagne !" — bloque tout clic (masque pleine
        # page) tant qu'elle n'est pas fermée, y compris celui de l'onglet
        # surveys-nav ci-dessous (cause confirmée du timeout observé).
        _close_ps_play_and_earn_popup(page)

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
            page.wait_for_selector(_SURVEY_CARD_ATTR_PREFIX, state="attached", timeout=20000)
        except Exception:
            log_debug(_TAG, "select_survey() — aucune carte détectée après 20s")

        excluded_uuids: set = set()

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            best = _select_best_earnstar_card(page, excluded_uuids)
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

            try:
                card.click()
                log_debug(_TAG, f"select_survey() — carte cliquée (tentative {attempt}/{_MAX_ATTEMPTS}, uuid={uuid_})")
            except Exception as e:
                log_info(_TAG, f"select_survey() — clic carte échoué (uuid={uuid_}) : {e}")
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
            page.wait_for_selector(_SURVEY_CARD_ATTR_PREFIX, state="attached", timeout=8000)
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
        """
        Navigue vers la page d'accueil (seule URL confirmée) et cherche le
        signal authentifié (dashboard, non confirmé sur EarnStar — cf.
        en-tête de fichier) dans un budget borné. Absent → session considérée
        expirée (force un nouveau login()).
        """
        try:
            page = driver
            page.goto(_HOME_URL)
            page.wait_for_selector(_AUTHENTICATED_SIGNAL_SEL, state="attached", timeout=8000)
            return False
        except Exception:
            return True

    def get_platform_name(self) -> str:
        return "earnstar"

    def get_home_url(self) -> str:
        # URL du dashboard applicatif post-login (probable app.earnstar.com,
        # présente dans la config Nuxt embarquée de la page marketing) non
        # confirmée par capture d'un écran réellement connecté — on retourne
        # la seule URL certaine (landing marketing, qui porte aussi le
        # formulaire de connexion) plutôt que de figer une valeur devinée.
        return _HOME_URL

    def get_domains(self) -> List[str]:
        return ["earnstar.com"]
