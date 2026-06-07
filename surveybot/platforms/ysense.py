from __future__ import annotations

from typing import List

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from platforms.base import Platform
from Survey.log_utils import log_info, log_debug

_TAG = "[YSENSE]"
_LOGIN_URL = "https://www.ysense.com/login"


class YSensePlatform(Platform):

    def login(self, driver, config: dict) -> bool:
        email = config["Email"]
        password = config["Password"]
        log_info(_TAG, f"login() — navigation vers {_LOGIN_URL}")

        driver.get(_LOGIN_URL)

        # Attendre la présence du champ email (server-rendered, pas de SPA hydration)
        try:
            email_input = WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input#username"))
            )
        except TimeoutException:
            log_info(_TAG, "login() — timeout : input#username introuvable")
            return False

        log_debug(_TAG, "login() — champ email présent dans le DOM")

        email_input.clear()
        email_input.send_keys(email)
        driver.execute_script(
            "arguments[0].dispatchEvent(new Event('input',  { bubbles: true }));"
            "arguments[0].dispatchEvent(new Event('change', { bubbles: true }));",
            email_input,
        )
        log_debug(_TAG, "login() — email saisi")

        try:
            pwd_input = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
        except Exception as e:
            log_info(_TAG, f"login() — champ password introuvable : {e}")
            return False

        driver.execute_script(
            "arguments[0].value = arguments[1];"
            "arguments[0].dispatchEvent(new Event('input',  { bubbles: true }));"
            "arguments[0].dispatchEvent(new Event('change', { bubbles: true }));",
            pwd_input,
            password,
        )
        if not (pwd_input.get_attribute("value") or "").strip():
            pwd_input.clear()
            pwd_input.send_keys(password)
            log_debug(_TAG, "login() — mot de passe injecté via fallback send_keys()")
        else:
            log_debug(_TAG, "login() — mot de passe injecté via JS")

        # Recaptcha si présent et visible
        try:
            rc = driver.find_element(By.CSS_SELECTOR, "div#recaptcha-login")
            if rc.is_displayed():
                log_info(_TAG, "login() — recaptcha détecté, résolution en cours…")
                from captcha import recaptcha_handler
                recaptcha_handler.solve_recaptcha_v2_auto(driver)
                log_info(_TAG, "login() — recaptcha résolu")
        except Exception:
            pass

        # Soumettre via JS click
        try:
            submit_btn = driver.find_element(By.CSS_SELECTOR, "button.sbutton.large")
            driver.execute_script("arguments[0].click();", submit_btn)
            log_info(_TAG, "login() — bouton de soumission cliqué")
        except Exception as e:
            log_info(_TAG, f"login() — bouton de soumission introuvable : {e}")
            return False

        # Vérifier le succès : URL sans /login dans les 15s
        try:
            WebDriverWait(driver, 15).until(
                lambda d: "/login" not in d.current_url
            )
            log_info(_TAG, "login() — succès (URL sans /login)")
            return True
        except TimeoutException:
            pass

        # Fallback : div#errors vide
        try:
            errors_div = driver.find_element(By.CSS_SELECTOR, "div#errors")
            if not (errors_div.text or "").strip():
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
            url = driver.current_url or ""
            if "/login" in url:
                return True
            src = (driver.page_source or "").lower()
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
