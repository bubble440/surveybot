from __future__ import annotations

import os

from platforms.base import Platform

# PLATFORM est une variable GLOBAL_CONFIG : en build compilé (Nuitka), elle provient
# exclusivement de global_config.py, jamais de l'environnement du process (cf. config.py).
# En dev/attach (global_config.py absent du projet), fallback os.getenv.
try:
    from global_config import PLATFORM  # type: ignore
except ImportError:
    PLATFORM = os.getenv("PLATFORM", "")


def get_platform(name: str | None = None) -> Platform:
    """
    Retourne l'instance Platform correspondant à `name`.
    Si name est None, lit la variable PLATFORM (défaut: 'topsurveys').
    """
    if name is None:
        name = (PLATFORM or "topsurveys").strip().lower()

    if name == "topsurveys":
        from platforms.topsurveys import TopSurveysPlatform
        return TopSurveysPlatform()

    if name == "ysense":
        from platforms.ysense import YSensePlatform
        return YSensePlatform()

    raise ValueError(
        f"Plateforme inconnue: {name!r}. Valeurs supportées: 'topsurveys', 'ysense'"
    )
