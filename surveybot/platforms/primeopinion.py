from __future__ import annotations

import os
import re
from typing import List

from config import is_cta_intercept_only
from platforms.base import Platform
from preselection.question_analyzer import (
    click_participer_if_qualified,
    handle_disqualification_and_retry,
)
from Survey.log_utils import log_info, log_debug

_TAG = "[PRIMEOPINION]"

# Landing marketing — héberge directement le formulaire email + déclenche la
# modale mot de passe. Aucune route /login séparée observée (cf. spec).
_HOME_URL = "https://www.primeopinion.com"

_EMAIL_INPUT_SEL = "input[data-test-id='check-email-field-input']"
_EMAIL_CONTINUE_BTN_SEL = "button[data-test-id='check-email-continue-button']"
_AUTH_MODAL_SEL = "div[role='dialog'][data-test-id='auth_modal']"
_PASSWORD_INPUT_SEL = "input[data-test-id='sign-in-password-field-input']"
_LOGIN_SUBMIT_BTN_SEL = "button[data-test-id='sign-in-submit-button']"
# Signal de session active (dashboard) — mêmes data-test-id que TopSurveys,
# même éditeur ("Prime Insights AB").
_AUTHENTICATED_SIGNAL_SEL = "[data-test-id='surveys-nav'], [data-test-id='user-balance']"

# Listing des sondages — mêmes sélecteurs que TopSurveys (même éditeur, même
# structure DOM). [data-test-id='ps-surveys-root'] (utilisé par TopSurveys pour
# confirmer le montage de la vue liste) n'a PAS été observé sur PrimeOpinion :
# volontairement absent ici, pas d'hypothèse fragile.
_SURVEYS_NAV_SEL = "[data-test-id='surveys-nav']"
_SURVEY_CARD_SEL = "div.survey-tile"
_PS_POPUP_SEL = "[data-test-id='ps-popup-content-wrapper']"

# Récompense affichée en Points (pas en €) : deux valeurs numériques par carte
# (montant sans bonus, puis montant avec bonus) — on retient le max des
# occurrences matchées, qui correspond toujours au montant incluant le bonus.
_POINTS_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:points?|pts?)\b", re.IGNORECASE)
_DURATION_RE = re.compile(r"(\d+)\s*(?:min|mn|minute(?:s)?)\b", re.IGNORECASE)


def _mask_secret(value: str) -> str:
    """Longueur + bordure(s) seulement — jamais la valeur complète."""
    v = value or ""
    if len(v) < 2:
        return f"len={len(v)}"
    return f"len={len(v)} [{v[0]}…{v[-1]}]"


def _parse_reward_points(text: str):
    """Extrait la récompense en Points depuis le texte de carte (max des valeurs trouvées)."""
    if not text:
        return None
    values = []
    for raw in _POINTS_RE.findall(text):
        try:
            v = float(raw.replace(",", "."))
        except ValueError:
            continue
        if v > 0:
            values.append(v)
    return max(values) if values else None


def _parse_duration_min(text: str):
    """Extrait la durée estimée en minutes depuis le texte de carte (ex: 22 min)."""
    if not text:
        return None
    match = _DURATION_RE.search(text)
    if not match:
        return None
    try:
        duration = int(match.group(1))
    except ValueError:
        return None
    return duration if duration > 0 else None


def _extract_survey_uuid(card) -> "str | None":
    """
    Remonte les ancêtres DOM de la carte pour trouver l'attribut
    data-test-id="ps-survey-<uuid>" et retourne l'UUID. card : Playwright ElementHandle.
    """
    try:
        result = card.evaluate(
            """(el) => {
                let node = el.parentElement;
                while (node) {
                    const tid = node.getAttribute('data-test-id') || '';
                    if (tid.startsWith('ps-survey-')) return tid.slice(10);
                    node = node.parentElement;
                }
                return null;
            }"""
        )
        return result if isinstance(result, str) else None
    except Exception:
        return None


def _select_best_primeopinion_card(page, excluded_uuids: set):
    """
    Scanne les cartes div.survey-tile visibles, score reward(Points)/durée(min),
    exclut les UUID déjà tentés dans cette session de sélection (disqualifiés).
    Retourne (card, uuid) ou None si aucune carte exploitable.
    """
    candidates = []
    for idx, card in enumerate(page.query_selector_all(_SURVEY_CARD_SEL), start=1):
        try:
            if not (card.is_visible() and card.is_enabled()):
                continue
            text = (card.inner_text() or "").strip()
            reward = _parse_reward_points(text)
            duration = _parse_duration_min(text)
            if reward is None or duration is None or duration <= 0:
                log_debug(_TAG, f"select_survey() — carte #{idx} ignorée (reward/durée non parsable)")
                continue
            uuid_ = _extract_survey_uuid(card)
            if uuid_ is not None and uuid_ in excluded_uuids:
                continue
            score = reward / duration
            candidates.append((score, card, uuid_))
        except Exception as e:
            log_debug(_TAG, f"select_survey() — carte #{idx} exception : {type(e).__name__}")

    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0], reverse=True)
    _, best_card, best_uuid = candidates[0]
    return best_card, best_uuid


class PrimeOpinionPlatform(Platform):
    """
    Implémentation PrimeOpinion — périmètre de ce patch limité au login.
    Conventions DOM quasi identiques à TopSurveys (même éditeur, "Prime
    Insights AB") mais domaines propres. Stratégie de connexion autonome
    (pas de délégation à preselection/auth_handler.py, réservé à TopSurveys),
    DOM-first uniquement, sans fallback Vision.
    """

    def login(self, driver, config: dict) -> bool:
        email = os.getenv("EMAIL") or config.get("Email", "")
        password = os.getenv("PASSWORD") or config.get("Password", "")
        log_debug(
            _TAG,
            f"login() — identifiants lus depuis config : email={email!r}, "
            f"password={_mask_secret(password)}",
        )
        log_info(_TAG, f"login() — navigation vers {_HOME_URL}")

        page = driver
        page.goto(_HOME_URL)

        # --- Étape 1 : email sur la page marketing (pas de modale préalable) ---
        try:
            email_input = page.wait_for_selector(
                _EMAIL_INPUT_SEL, state="visible", timeout=20000
            )
        except Exception:
            log_info(_TAG, "login() — timeout : champ email introuvable")
            return False

        email_input.fill(email)
        log_debug(_TAG, "login() — email saisi")

        try:
            continue_btn = page.wait_for_selector(
                _EMAIL_CONTINUE_BTN_SEL, state="visible", timeout=10000
            )
            continue_btn.click()
            log_debug(_TAG, "login() — bouton continue cliqué")
        except Exception as e:
            log_info(_TAG, f"login() — bouton continue introuvable/inclickable : {e}")
            return False

        # --- Étape 2 : modale mot de passe injectée après soumission de l'email ---
        try:
            page.wait_for_selector(_AUTH_MODAL_SEL, state="visible", timeout=15000)
        except Exception:
            log_info(_TAG, "login() — modale auth_modal non apparue après 15s")
            return False

        try:
            pwd_input = page.wait_for_selector(
                _PASSWORD_INPUT_SEL, state="visible", timeout=10000
            )
        except Exception:
            log_info(_TAG, "login() — champ password introuvable dans la modale")
            return False

        pwd_input.fill(password)
        log_debug(_TAG, "login() — mot de passe saisi")

        try:
            login_btn = page.wait_for_selector(
                _LOGIN_SUBMIT_BTN_SEL, state="visible", timeout=10000
            )
            login_btn.click()
            log_info(_TAG, "login() — bouton de soumission cliqué")
        except Exception as e:
            log_info(_TAG, f"login() — bouton de soumission introuvable/inclickable : {e}")
            return False

        # --- Étape 3 : auto-validation du succès avant retour (aucune attente
        # générique côté launch.py pour une plateforme != 'topsurveys') ---
        try:
            page.wait_for_selector(
                _AUTHENTICATED_SIGNAL_SEL, state="attached", timeout=20000
            )
            log_info(_TAG, "login() — succès (signal authentifié détecté)")
            try:
                from Cash.primeopinion_balance import check_and_claim_if_needed
                check_and_claim_if_needed(page, os.getenv("ACCOUNT_ID") or "unknown")
            except Exception as e:
                log_debug(_TAG, f"login() — vérification solde/retrait post-login échouée (non bloquant) : {e}")
            return True
        except Exception:
            log_info(_TAG, "login() — signal authentifié non détecté après 20s, échec")
            return False

    def select_survey(self, driver) -> bool:
        """
        Navigue vers l'onglet Sondages, sélectionne la meilleure carte par ratio
        reward(Points)/durée(min), la clique, puis résout le popup de
        qualification via les fonctions ps-* génériques (question_analyzer.py) :
        qualifié → participation lancée par click_participer_if_qualified() ;
        disqualifié → popup fermé par handle_disqualification_and_retry(), on
        retente une autre carte (budget borné). Aucune carte exploitable →
        cooldown générique NO_SURVEY_AVAILABLE (infra partagée, réutilisée
        telle quelle).
        """
        log_info(_TAG, "select_survey() called")
        page = driver

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

        _MAX_ATTEMPTS = 3
        excluded_uuids: set = set()

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            best = _select_best_primeopinion_card(page, excluded_uuids)
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
        """
        Version minimale généraliste (à l'image de YSensePlatform, pas de
        TopSurveysPlatform) : aucune capture DOM de l'écran de retour
        PrimeOpinion n'est disponible, donc aucune mécanique de popup
        spécifique (boîte mystère, série, "Bon travail"/"Génial") n'est
        tentée. Détection DOM-first du retour effectif sur la liste (onglet +
        cartes présents) plutôt qu'une hypothèse sur un chemin d'URL non
        confirmé, puis relance select_survey().
        """
        log_info(_TAG, "handle_post_survey() called")
        page = driver

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
            from Cash.primeopinion_balance import check_and_claim_if_needed
            check_and_claim_if_needed(page, account_id)
        except Exception as e:
            log_debug(_TAG, f"handle_post_survey() — vérification solde/retrait échouée (non bloquant) : {e}")

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
        signal authentifié (dashboard) dans un budget borné. Absent → session
        considérée expirée (force un nouveau login()).
        """
        try:
            page = driver
            page.goto(_HOME_URL)
            page.wait_for_selector(
                _AUTHENTICATED_SIGNAL_SEL, state="attached", timeout=8000
            )
            return False
        except Exception:
            return True

    def get_platform_name(self) -> str:
        return "primeopinion"

    def get_home_url(self) -> str:
        # URL du dashboard applicatif post-login (probable app.primeopinion.com,
        # par analogie avec app.topsurveys.app) non confirmée à partir des seules
        # captures DOM disponibles — cf. spec. On retourne la seule URL certaine
        # (landing marketing, qui porte aussi le formulaire de connexion) plutôt
        # que de figer une valeur devinée. À vérifier/ajuster en conditions
        # réelles avant tout patch étendant le périmètre au-delà du login.
        return _HOME_URL

    def get_domains(self) -> List[str]:
        return ["primeopinion.com"]
