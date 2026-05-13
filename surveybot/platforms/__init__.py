from __future__ import annotations

import os

from platforms.base import Platform


def get_platform(name: str | None = None) -> Platform:
    """
    Retourne l'instance Platform correspondant à `name`.
    Si name est None, lit la variable d'environnement PLATFORM (défaut: 'topsurveys').
    """
    if name is None:
        name = (os.getenv("PLATFORM") or "topsurveys").strip().lower()

    if name == "topsurveys":
        from platforms.topsurveys import TopSurveysPlatform
        return TopSurveysPlatform()

    if name == "ysense":
        from platforms.ysense import YSensePlatform
        return YSensePlatform()

    raise ValueError(
        f"Plateforme inconnue: {name!r}. Valeurs supportées: 'topsurveys', 'ysense'"
    )
