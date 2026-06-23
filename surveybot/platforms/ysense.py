from __future__ import annotations

from typing import List

from platforms.base import Platform
from Survey.log_utils import log_info, log_debug

_TAG = "[YSENSE]"
_LOGIN_URL = "https://www.ysense.com/login"


def _pw_page(d):
    if hasattr(d, '_page'):
        return d._page
    return d


class YSensePlatform(Platform):

    def login(self, driver, config: dict) -> bool:
        email = config["Email"]
        password = config["Password"]
        log_info(_TAG, f"login() — navigation vers {_LOGIN_URL}")

        page = _pw_page(driver)
        page.goto(_LOGIN_URL)

        # Attendre la présence du champ email (server-rendered, pas de SPA hydration)
        try:
            email_input = page.wait_for_selector(
                "input#username", state="attached", timeout=20000
            )
        except Exception:
            log_info(_TAG, "login() — timeout : input#username introuvable")
            return False

        log_debug(_TAG, "login() — champ email présent dans le DOM")

        email_input.fill(email)
        log_debug(_TAG, "login() — email saisi")

        try:
            pwd_input = page.query_selector("input[type='password']")
            if pwd_input is None:
                raise RuntimeError("introuvable")
        except Exception as e:
            log_info(_TAG, f"login() — champ password introuvable : {e}")
            return False

        pwd_input.evaluate(
            "(e, v) => {"
            " e.value = v;"
            " e.dispatchEvent(new Event('input',  { bubbles: true }));"
            " e.dispatchEvent(new Event('change', { bubbles: true }));"
            "}",
            password,
        )
        if not (pwd_input.input_value() or "").strip():
            pwd_input.fill(password)
            log_debug(_TAG, "login() — mot de passe injecté via fallback fill()")
        else:
            log_debug(_TAG, "login() — mot de passe injecté via JS")

        # Recaptcha si présent et visible
        try:
            rc = page.query_selector("div#recaptcha-login")
            if rc is not None and rc.is_visible():
                log_info(_TAG, "login() — recaptcha détecté, résolution en cours…")
                from captcha import recaptcha_handler
                recaptcha_handler.solve_recaptcha_v2_auto(driver)
                log_info(_TAG, "login() — recaptcha résolu")
        except Exception:
            pass

        # Soumettre via click
        try:
            submit_btn = page.query_selector("button.sbutton.large")
            if submit_btn is None:
                raise RuntimeError("introuvable")
            submit_btn.click()
            log_info(_TAG, "login() — bouton de soumission cliqué")
        except Exception as e:
            log_info(_TAG, f"login() — bouton de soumission introuvable : {e}")
            return False

        # Vérifier le succès : URL sans /login dans les 15s
        try:
            page.wait_for_function(
                "() => !window.location.href.includes('/login')", timeout=15000
            )
            log_info(_TAG, "login() — succès (URL sans /login)")
            return True
        except Exception:
            pass

        # Fallback : div#errors vide
        try:
            errors_div = page.query_selector("div#errors")
            if errors_div is not None and not (errors_div.inner_text() or "").strip():
                log_info(_TAG, "login() — succès (div#errors vide)")
                return True
        except Exception:
            pass

        log_info(_TAG, "login() — échec : /login toujours dans l'URL")
        return False

    def select_survey(self, driver) -> bool:
        """
        Doit naviguer vers https://www.ysense.com/surveys?m=1&ds=39, analyser la liste
        de surveys disponibles, choisir le meilleur selon le ratio reward/durée,
        et cliquer pour l'ouvrir dans un nouvel onglet.
        Retourner True si un survey a été sélectionné, False si aucun disponible.
        """
        log_info(_TAG, "select_survey() called")
        raise NotImplementedError(f"{_TAG} select_survey() non implémenté")

    def handle_post_survey(self, driver, account_id: str) -> bool:
        """
        Doit gérer le retour sur ySense après qu'un survey externe s'est terminé :
        confirmation de gains, popups de statut (complété, disqualifié, quota plein),
        collecte du crédit éventuel.
        Retourner True si la plateforme a géré la situation et qu'on peut enchaîner
        un nouveau survey, False sinon.
        """
        log_info(_TAG, "handle_post_survey() called")
        raise NotImplementedError(f"{_TAG} handle_post_survey() non implémenté")

    def is_on_platform(self, driver) -> bool:
        """
        Retourne True si l'URL courante appartient au domaine ysense.com.
        """
        log_info(_TAG, "is_on_platform() called")
        raise NotImplementedError(f"{_TAG} is_on_platform() non implémenté")

    def is_session_expired(self, driver) -> bool:
        try:
            url = _pw_page(driver).url or ""
            if "/login" in url:
                return True
            src = (_pw_page(driver).content() or "").lower()
            signals = ["sign in", "session expired", "please log in", "your session"]
            return any(s in src for s in signals)
        except Exception:
            return False

    def get_platform_name(self) -> str:
        return "ysense"

    def get_home_url(self) -> str:
        return "https://www.ysense.com"

    def get_domains(self) -> List[str]:
        return ["ysense.com"]
