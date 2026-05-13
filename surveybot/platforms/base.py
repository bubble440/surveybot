from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List


class Platform(ABC):
    """Interface abstraite que chaque plateforme d'agrégation de sondages doit implémenter."""

    @abstractmethod
    def login(self, driver, config: dict) -> bool:
        """Authentifie le bot sur la plateforme. Retourne True si succès."""

    @abstractmethod
    def select_survey(self, driver) -> bool:
        """
        Navigue vers le listing et clique sur le meilleur survey disponible.
        Retourne True si un survey a été sélectionné, False si aucun disponible.
        """

    @abstractmethod
    def handle_post_survey(self, driver, account_id: str) -> bool:
        """
        Gère tout ce qui se passe après qu'un survey externe se termine et que
        le driver revient sur la plateforme : popups, mystery box, disqualification,
        cashout. Retourne True si la plateforme a géré la situation et qu'on peut
        enchaîner un nouveau survey.
        """

    @abstractmethod
    def is_on_platform(self, driver) -> bool:
        """Retourne True si l'URL courante appartient à cette plateforme."""

    @abstractmethod
    def is_session_expired(self, driver) -> bool:
        """Détecte une expiration de session."""

    @abstractmethod
    def get_platform_name(self) -> str:
        """Retourne l'identifiant court de la plateforme (ex: 'topsurveys', 'ysense')."""

    @abstractmethod
    def get_home_url(self) -> str:
        """Retourne l'URL principale de la plateforme (page surveys ou landing)."""

    @abstractmethod
    def get_domains(self) -> List[str]:
        """Retourne la liste des domaines appartenant à cette plateforme."""
