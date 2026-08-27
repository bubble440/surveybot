from __future__ import annotations

import os
from typing import List

from platforms.base import Platform
from Survey.log_utils import log_info, log_debug

_TAG = "[HEYCASH]"

# Landing marketing FR — seule URL confirmée par capture DOM à ce stade.
_HOME_URL = "https://www.heycash.com/fr-fr"

# CONFIRMÉ (présent identiquement dans header/promo/faq/how-it-works du HTML
# fourni) : bouton déclenchant l'ouverture de la modale d'authentification.
# Scopé au header pour éviter l'ambiguïté avec les autres occurrences.
_OPEN_AUTH_MODAL_BTN_SEL = "header button[data-test-id='open-auth-modal-button']"

# NON CONFIRMÉ, volontairement absent : champ email, champ mot de passe,
# bouton de soumission, signal de session authentifiée, cartes de sondages,
# popups post-survey. Le contenu de la modale est injecté côté client et
# n'apparaît pas dans le document fourni (landing pré-interaction).


def _mask_secret(value: str) -> str:
    v = value or ""
    if len(v) < 2:
        return f"len={len(v)}"
    return f"len={len(v)} [{v[0]}…{v[-1]}]"


class HeyCashPlatform(Platform):
    """
    Implémentation HeyCash — périmètre de ce patch limité à l'ouverture de la
    modale d'authentification. Design system et machine d'état d'auth
    identiques à PrimeOpinion (cohérent avec un éditeur partagé), mais
    déclenchement différent (bouton préalable requis, route /login séparée
    existante) : pas de réutilisation des sélecteurs PrimeOpinion au-delà de
    ce qui est confirmé ici.
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

        try:
            open_modal_btn = page.wait_for_selector(
                _OPEN_AUTH_MODAL_BTN_SEL, state="visible", timeout=15000
            )
            open_modal_btn.click()
            log_info(_TAG, "login() — bouton d'ouverture de la modale cliqué")
        except Exception as e:
            log_info(_TAG, f"login() — bouton open-auth-modal-button introuvable/inclickable : {e}")
            return False

        # Suite du flux non implémentée : DOM de la modale non capturé.
        log_info(
            _TAG,
            "login() — modale ouverte, suite du flux (email/password) non "
            "implémentée : capture DOM requise avant de poursuivre",
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
        # Signal de session authentifiée non confirmé : traité comme
        # systématiquement expirée pour forcer login() plutôt que deviner.
        log_debug(_TAG, "is_session_expired() — signal authentifié non confirmé, retourne True par défaut")
        return True

    def get_platform_name(self) -> str:
        return "heycash"

    def get_home_url(self) -> str:
        return _HOME_URL

    def get_domains(self) -> List[str]:
        return ["heycash.com"]