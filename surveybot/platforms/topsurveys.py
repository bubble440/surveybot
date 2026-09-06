from __future__ import annotations

import os
from typing import List

from platforms.base import Platform




class TopSurveysPlatform(Platform):
    """
    Implémentation TopSurveys. Délègue à la logique existante dans preselection/
    et Survey/functions.py — aucune logique dupliquée ici.
    """

    def login(self, driver, config: dict) -> bool:
        from preselection.auth_handler import login
        email = os.getenv("EMAIL") or config.get("Email", "")
        password = os.getenv("PASSWORD") or config.get("Password", "")
        login(driver, email, password)
        return True

    def select_survey(self, driver) -> bool:
        from preselection.survey_navigator import go_to_best_value_survey
        go_to_best_value_survey(driver)
        return True

    def handle_post_survey(self, driver, account_id: str) -> bool:
        from Survey.functions import _handle_topsurveys_exclusion_popup
        return bool(_handle_topsurveys_exclusion_popup(driver, account_id, platform=self))

    def is_on_platform(self, driver) -> bool:
        try:
            url = (driver.url or "").lower()
            return any(d in url for d in self.get_domains())
        except Exception:
            return False

    def is_session_expired(self, driver) -> bool:
        from preselection.auth_handler import is_session_expired
        return is_session_expired(driver)

    def get_platform_name(self) -> str:
        return "topsurveys"

    def get_home_url(self) -> str:
        # URL de l'application (surveys) plutôt que la landing marketing —
        # redirige automatiquement vers le login si la session est expirée.
        return "https://app.topsurveys.app/surveys"

    def get_domains(self) -> List[str]:
        return ["topsurveys.app"]
