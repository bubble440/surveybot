from __future__ import annotations

import os
from typing import List

from platforms.base import Platform
from Survey.log_utils import log_info, log_debug

_TAG = "[HEYCASH]"

# Landing marketing FR — porte le bouton d'ouverture de la modale d'auth.
_HOME_URL = "https://www.heycash.com/fr-fr"

# --- CONFIRMÉ par capture DOM (flux complet email → mot de passe) ---
_OPEN_AUTH_MODAL_BTN_SEL = "header button[data-test-id='open-auth-modal-button']"
_MODAL_DIALOG_SEL = "[role='dialog']"
_EMAIL_INPUT_SEL = "input[data-test-id='check-email-field-input']"
_EMAIL_CONTINUE_BTN_SEL = "button[data-test-id='check-email-continue-button']"
_PASSWORD_INPUT_SEL = "input[data-test-id='sign-in-password-field-input']"
_LOGIN_SUBMIT_BTN_SEL = "button[data-test-id='sign-in-submit-button']"

# Sélecteurs identiques caractère pour caractère à ceux déjà confirmés côté
# PrimeOpinion (check-email-field-input, check-email-continue-button,
# sign-in-password-field-input, sign-in-submit-button) — les deux marques
# partagent visiblement le même composant Vue de flux d'authentification.
# Divergence confirmée : pas de data-test-id="auth_modal" sur le conteneur
# de la modale HeyCash (seul [role='dialog'] est présent) — ne pas réutiliser
# le sélecteur de modale de PrimeOpinion tel quel.

# --- NON CONFIRMÉ : le signal de session authentifiée (dashboard) n'a pas
# été capturé pour HeyCash. Vu l'identité stricte des sélecteurs d'auth avec
# PrimeOpinion, on réutilise son signal comme hypothèse motivée — à
# corriger dès qu'un dashboard HeyCash authentifié est capturé. ---
_AUTHENTICATED_SIGNAL_SEL = "[data-test-id='surveys-nav'], [data-test-id='user-balance']"


def _mask_secret(value: str) -> str:
    v = value or ""
    if len(v) < 2:
        return f"len={len(v)}"
    return f"len={len(v)} [{v[0]}…{v[-1]}]"


class HeyCashPlatform(Platform):
    """
    Implémentation HeyCash. Flux d'authentification (bouton d'ouverture →
    email → continue → mot de passe → soumission) confirmé identique
    sélecteur pour sélecteur à PrimeOpinion — même composant Vue partagé
    entre les deux marques. Le signal de dashboard authentifié reste une
    hypothèse (non capturé pour HeyCash) ; select_survey() et
    handle_post_survey() restent non implémentés (DOM dashboard/popups
    jamais observé pour cette marque).
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

        # --- Étape 1 : ouverture de la modale ---
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

        # --- Étape 2 : email ---
        try:
            email_input = page.wait_for_selector(
                _EMAIL_INPUT_SEL, state="visible", timeout=10000
            )
        except Exception:
            log_info(_TAG, "login() — champ email introuvable dans la modale")
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

        # --- Étape 3 : mot de passe (la modale bascule sur l'écran "Se connecter") ---
        try:
            pwd_input = page.wait_for_selector(
                _PASSWORD_INPUT_SEL, state="visible", timeout=15000
            )
        except Exception:
            log_info(_TAG, "login() — champ password introuvable après l'étape email")
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

        # --- Étape 4 : validation du succès — signal HYPOTHÈSE (non capturé
        # pour HeyCash, réutilisé depuis PrimeOpinion vu l'identité du flux
        # amont). À corriger dès qu'un dashboard HeyCash authentifié est
        # capturé. ---
        try:
            page.wait_for_selector(
                _AUTHENTICATED_SIGNAL_SEL, state="attached", timeout=20000
            )
            log_info(_TAG, "login() — succès (signal authentifié détecté, sélecteur hypothèse)")
            return True
        except Exception:
            log_info(
                _TAG,
                "login() — signal authentifié (hypothèse) non détecté après 20s : "
                "soit échec réel, soit sélecteur de dashboard incorrect pour HeyCash "
                "— capture DOM du dashboard requise pour trancher",
            )
            return False

    def select_survey(self, driver) -> bool:
        log_info(_TAG, "select_survey() — non implémenté : DOM du dashboard HeyCash non capturé")
        return False

    def handle_post_survey(self, driver, account_id: str) -> bool:
        log_info(_TAG, "handle_post_survey() — non implémenté : DOM des popups post-survey HeyCash non capturé")
        return False

    def is_on_platform(self, driver) -> bool:
        try:
            url = (driver.url or "").lower()
        except Exception:
            return False
        return any(d in url for d in self.get_domains())

    def is_session_expired(self, driver) -> bool:
        """
        Navigue vers la home et cherche le signal authentifié (même hypothèse
        que login(), non confirmée pour HeyCash). Absent → session considérée
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
        return "heycash"

    def get_home_url(self) -> str:
        return _HOME_URL

    def get_domains(self) -> List[str]:
        return ["heycash.com"]