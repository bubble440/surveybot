from __future__ import annotations

import os
from typing import List
from urllib.parse import urlparse
from config import is_cta_intercept_only
from platforms.base import Platform
from Survey.log_utils import log_info, log_debug

_TAG = "[YSENSE]"
_LOGIN_URL = "https://www.ysense.com/login"


def _mask_secret(value: str) -> str:
    """Longueur + bordure(s) seulement — jamais la valeur complète."""
    v = value or ""
    if len(v) < 2:
        return f"len={len(v)}"
    return f"len={len(v)} [{v[0]}…{v[-1]}]"


class YSensePlatform(Platform):

    def login(self, driver, config: dict) -> bool:
        email = os.getenv("EMAIL") or config.get("Email", "")
        password = os.getenv("PASSWORD") or config.get("Password", "")
        log_debug(
            _TAG,
            f"login() — identifiants lus depuis config : email={email!r}, "
            f"password={_mask_secret(password)}",
        )
        log_info(_TAG, f"login() — navigation vers {_LOGIN_URL}")

        page = driver
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

        # Variante additive — "ré-authentification, profil persistant" :
        # quand le profil Chrome existe déjà et que ySense reconnaît la
        # session, /login sert une page qui ne redemande que le mot de
        # passe ("Please re-enter your password to continue."). Le champ
        # #username reste présent dans le DOM (attaché, donc le
        # wait_for_selector ci-dessus réussit toujours) mais son
        # form-group parent porte style="display: none" : il n'est ni
        # visible ni destiné à être rempli. Sans cette détection, le fill()
        # sur un champ invisible attend indéfiniment (jusqu'au timeout).
        # Détection scopée sur la visibilité réelle du champ, sans toucher
        # au chemin standard (username + password) qui reste inchangé
        # ci-dessous pour les autres cas (nouveau profil, session inconnue).
        password_only_reauth = False
        try:
            password_only_reauth = not email_input.is_visible()
        except Exception:
            password_only_reauth = False

        if password_only_reauth:
            log_info(
                _TAG,
                "login() — variante détectée : ré-authentification mot de passe seul "
                "(profil persistant, champ username non visible)",
            )
        else:
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

        # Stabilisation anti-hydration : la page charge vendor-react.compiled.js
        # + login.bundle.js après le HTML server-rendered. Une hydration React
        # peut remonter le formulaire après nos fill() (sans exception), vidant
        # silencieusement les champs avant le clic. On revérifie juste avant de
        # soumettre et on re-remplit si nécessaire (budget borné).
        # En variante password_only_reauth, le champ email n'est jamais rempli
        # ni revérifié (il reste vide/invisible par design de cette page) :
        # seule la stabilité du mot de passe conditionne la sortie de boucle.
        _MAX_STABILIZE_ATTEMPTS = 3
        stabilized = False
        for attempt in range(_MAX_STABILIZE_ATTEMPTS):
            email_val = "" if password_only_reauth else (email_input.input_value() or "").strip()
            pwd_val = (pwd_input.input_value() or "").strip()
            email_ok = password_only_reauth or (email_val == email)
            if email_ok and pwd_val == password:
                stabilized = True
                break
            log_debug(
                _TAG,
                f"login() — champs vidés avant soumission (tentative {attempt + 1}/"
                f"{_MAX_STABILIZE_ATTEMPTS}), re-remplissage",
            )
            if not password_only_reauth and email_val != email:
                email_input.fill(email)
            if pwd_val != password:
                pwd_input.fill(password)
        else:
            email_val = "" if password_only_reauth else (email_input.input_value() or "").strip()
            pwd_val = (pwd_input.input_value() or "").strip()
            email_ok = password_only_reauth or (email_val == email)
            stabilized = email_ok and pwd_val == password

        if not stabilized:
            log_info(_TAG, "login() — impossible de stabiliser les champs avant soumission, abandon")
            return False

        # Soumettre via click
        try:
            submit_btn = page.query_selector("button.sbutton.large")
            if submit_btn is None:
                raise RuntimeError("introuvable")

            # Diagnostic : relecture DOM juste avant le clic (fenêtre restante
            # après la boucle de stabilisation), pour trancher entre valeur
            # source incorrecte (cf. log en tête de login()) et réinitialisation
            # tardive par la page. Mot de passe jamais loggué en clair.
            final_email_val = "" if password_only_reauth else (email_input.input_value() or "").strip()
            final_pwd_val = (pwd_input.input_value() or "").strip()
            log_debug(
                _TAG,
                f"login() — juste avant clic soumission : "
                f"password_only_reauth={password_only_reauth}, "
                f"email_match={'n/a' if password_only_reauth else (final_email_val == email)}, "
                f"password_match={final_pwd_val == password} "
                f"(password_lu={_mask_secret(final_pwd_val)})",
            )

            submit_btn.click()
            log_info(_TAG, "login() — bouton de soumission cliqué")
        except Exception as e:
            log_info(_TAG, f"login() — bouton de soumission introuvable : {e}")
            return False

        # Vérifier le succès : URL sans /login dans les 15s.
        # Seul signal fiable — cohérent avec is_session_expired() qui définit
        # l'authentification de la même façon. L'ancien fallback "div#errors
        # vide → succès" produisait un faux positif (le champ peut être vide
        # avant le rendu async du message d'erreur), ce qui masquait un
        # échec de connexion réel.
        try:
            page.wait_for_function(
                "() => !window.location.href.includes('/login')", timeout=15000
            )
            log_info(_TAG, "login() — succès (URL sans /login)")
            try:
                from Cash.ysense_balance import check_balance_and_notify_if_needed
                check_balance_and_notify_if_needed(page, os.getenv("ACCOUNT_ID") or "unknown")
            except Exception as e:
                log_debug(_TAG, f"login() — vérification solde post-login échouée (non bloquant) : {e}")
            return True
        except Exception:
            pass

        try:
            errors_div = page.query_selector("div#errors")
            errors_text = (errors_div.inner_text() or "").strip() if errors_div else ""
        except Exception:
            errors_text = ""
        log_debug(_TAG, f"login() — échec : /login toujours dans l'URL, errors={errors_text!r}")
        return False

    def select_survey(self, driver) -> bool:
        """
        Navigue vers https://www.ysense.com/surveys?m=1&ds=39, analyse la liste
        de surveys disponibles en essayant une liste bornée de sélecteurs DOM
        candidats (cf. _CARD_SELECTORS — le DOM diffère selon la méthode de
        connexion à ySense), choisit le meilleur selon le ratio reward/durée,
        et clique pour l'ouvrir.
        Retourne True si un survey a été sélectionné, False si aucun disponible.
        """
        log_info(_TAG, "select_survey() called")
        page = driver
        _SURVEYS_URL = "https://www.ysense.com/surveys?m=1&ds=39"

        try:
            page.goto(_SURVEYS_URL)
        except Exception as e:
            log_info(_TAG, f"select_survey() — navigation échouée : {e}")
            return False

        try:
            page.wait_for_selector("#survey-list-body", state="attached", timeout=20000)
        except Exception:
            log_info(_TAG, "select_survey() — #survey-list-body introuvable après 20s")
            return False

        _CARD_SELECTORS = [
            "#survey-list-body > a.survey-link[data-survey_reward][data-survey_loi]",
            "#survey-list-body tr.survey-link[data-survey_reward][data-survey_loi]",
        ]

        # #survey-list-body peut être attaché au DOM avant que les lignes de
        # surveys ne soient injectées (rendu asynchrone par vendor-react.compiled.js
        # / surveys.js) : le wait_for_selector sur le conteneur seul ci-dessus ne
        # garantit donc pas la présence des cartes. C'est la cause probable de la
        # détection instable observée (parfois trouvées, parfois non selon le
        # timing). Budget borné : on attend qu'au moins une carte candidate
        # apparaisse réellement dans le DOM avant de scanner.
        try:
            page.wait_for_selector(
                ", ".join(_CARD_SELECTORS), state="attached", timeout=10000
            )
        except Exception:
            log_debug(_TAG, "select_survey() — aucune carte apparue dans le délai imparti (10s)")

        cards = []
        for _sel in _CARD_SELECTORS:
            try:
                _found = page.query_selector_all(_sel)
            except Exception as e:
                log_debug(_TAG, f"select_survey() — sélecteur {_sel!r} en erreur : {e}")
                continue
            if _found:
                log_debug(
                    _TAG,
                    f"select_survey() — sélecteur {_sel!r} → {len(_found)} carte(s) trouvée(s)",
                )
                cards = _found
                break

        if not cards:
            log_debug(
                _TAG,
                f"select_survey() — aucun des {len(_CARD_SELECTORS)} sélecteurs candidats n'a retourné de carte",
            )

        best = None
        best_ratio = -1.0
        for card in cards:
            try:
                reward = float(card.get_attribute("data-survey_reward") or "0")
                loi = float(card.get_attribute("data-survey_loi") or "0")
            except (TypeError, ValueError):
                continue
            if loi <= 0:
                continue
            ratio = reward / loi
            if ratio > best_ratio:
                best_ratio = ratio
                best = card

        if best is None:
            log_info(_TAG, "select_survey() — aucun survey disponible")
            return False

        survey_id = best.get_attribute("data-survey_id") or "?"

        if is_cta_intercept_only():
            log_info(
                _TAG,
                f"select_survey() — survey {survey_id} trouvé (ratio={best_ratio:.2f}) — "
                "interception OK (CTA_INTERCEPT_ONLY actif), pas de clic réel."
            )
            return False
    
        try:
            best.click()
            log_info(
                _TAG,
                f"select_survey() — survey {survey_id} sélectionné (ratio reward/min={best_ratio:.2f})",
            )
            return True
        except Exception as e:
            log_info(_TAG, f"select_survey() — clic échoué sur survey {survey_id} : {e}")
            return False

    def handle_post_survey(self, driver, account_id: str) -> bool:
        """
        Gère le retour sur ySense pendant la résolution d'un survey. À la
        différence de TopSurveys, il n'y a pas de popup à fermer ni de crédit à
        collecter ici : un retour signifie simplement qu'on est de nouveau sur
        la page de sélection de surveys. On attend la fin du chargement de la
        page puis on relance un nouveau survey via select_survey().
        Retourne True si un retour a été traité (nouveau survey tenté), False
        si l'URL courante n'est pas la page de sélection de surveys ySense.
        """
        log_info(_TAG, "handle_post_survey() called")
        page = driver
        try:
            url = (page.url or "").lower()
        except Exception:
            return False

        # Ne traiter que le retour effectif sur la page de liste des surveys
        # (path exact "/surveys"). Le sous-chaîne "ysense.com/surveys" matchait
        # aussi la page de résolution du survey ("/surveys/prescreener-v2?...",
        # atteinte juste après select_survey()), provoquant un faux positif de
        # "retour plateforme" qui interrompait solve_full_survey() avant même
        # le premier scan DOM.
        try:
            path = urlparse(url).path.rstrip("/")
        except Exception:
            return False
        if path != "/surveys":
            return False

        log_info(_TAG, "handle_post_survey() — retour sur la page de sélection de surveys détecté")

        from Management.redirect_watcher import wait_for_page_load
        wait_for_page_load(page, timeout=30)

        try:
            from Cash.ysense_balance import check_balance_and_notify_if_needed
            check_balance_and_notify_if_needed(page, account_id)
        except Exception as e:
            log_debug(_TAG, f"handle_post_survey() — vérification solde échouée (non bloquant) : {e}")

        self.select_survey(page)
        return True

    def is_on_platform(self, driver) -> bool:
        """
        Retourne True si l'URL courante appartient au domaine ysense.com.
        """
        log_info(_TAG, "is_on_platform() called")
        try:
            url = (driver.url or "").lower()
        except Exception:
            return False
        return any(d in url for d in self.get_domains())

    def is_session_expired(self, driver) -> bool:
        """
        Force la navigation vers _LOGIN_URL avant évaluation : un compte
        authentifié est automatiquement redirigé hors de /login, un compte
        non authentifié y reste. Évite de statuer sur l'état de session à
        partir d'un onglet attaché dont l'URL/contenu ne reflète pas
        forcément ysense.com (cause du login systématiquement court-circuité).
        """
        try:
            page = driver
            page.goto(_LOGIN_URL)
            page.wait_for_load_state("domcontentloaded")
            url = page.url or ""
            return "/login" in url
        except Exception:
            return True

    def get_platform_name(self) -> str:
        return "ysense"

    def get_home_url(self) -> str:
        return "https://www.ysense.com"

    def get_domains(self) -> List[str]:
        return ["ysense.com"]