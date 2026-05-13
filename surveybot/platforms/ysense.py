from __future__ import annotations

from typing import List

from platforms.base import Platform
from Survey.log_utils import log_info

_TAG = "[YSENSE]"


class YSensePlatform(Platform):
    """
    Squelette ySense — chaque méthode lève NotImplementedError.
    À implémenter quand le support ySense sera développé.
    """

    def login(self, driver, config: dict) -> bool:
        """
        Doit remplir le formulaire email + password de ySense (https://www.ysense.com),
        soumettre et vérifier que la session est active avant de retourner True.
        L'email est dans config['Email'], le mot de passe dans config['Password'].
        """
        log_info(_TAG, "login() called")
        raise NotImplementedError(f"{_TAG} login() non implémenté")

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
        """
        Doit détecter une expiration de session ySense : redirection vers la page de
        connexion, message d'erreur de session, token invalide, etc.
        """
        log_info(_TAG, "is_session_expired() called")
        raise NotImplementedError(f"{_TAG} is_session_expired() non implémenté")

    def get_platform_name(self) -> str:
        return "ysense"

    def get_home_url(self) -> str:
        return "https://www.ysense.com"

    def get_domains(self) -> List[str]:
        return ["ysense.com"]
