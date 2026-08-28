from __future__ import annotations

import os

from platforms.base import Platform

# Plateformes reconnues par get_platform() ci-dessous — servent aussi à valider
# global_config.PLATFORM_ROTATION au démarrage (cf. validate_platform_rotation()
# et main.py, hors mode attach).
KNOWN_PLATFORMS = ("topsurveys", "ysense", "primeopinion", "heycash", "earnstar", "fivesurveys")


def get_platform(name: str | None = None) -> Platform:
    """
    Retourne l'instance Platform correspondant à `name`.
    Si name est None (mode attach uniquement — débogage manuel), lit la
    variable d'environnement PLATFORM pour cibler une plateforme précise
    (défaut: 'topsurveys'). Hors mode attach, `name` est toujours résolu
    explicitement par l'appelant (sélection de rotation, cf. main.py) — jamais
    par une constante globale, PLATFORM_ROTATION ayant remplacé l'ancienne
    variable PLATFORM (plateforme unique figée à la compilation).
    """
    if name is None:
        name = (os.getenv("PLATFORM", "") or "topsurveys").strip().lower()

    if name == "topsurveys":
        from platforms.topsurveys import TopSurveysPlatform
        return TopSurveysPlatform()

    if name == "ysense":
        from platforms.ysense import YSensePlatform
        return YSensePlatform()

    if name == "primeopinion":
        from platforms.primeopinion import PrimeOpinionPlatform
        return PrimeOpinionPlatform()

    if name == "heycash":
        from platforms.heycash import HeyCashPlatform
        return HeyCashPlatform()

    if name == "earnstar":
        from platforms.earnstar import EarnStarPlatform
        return EarnStarPlatform()

    if name == "fivesurveys":
        from platforms.fivesurveys import FiveSurveysPlatform
        return FiveSurveysPlatform()

    raise ValueError(
        f"Plateforme inconnue: {name!r}. Valeurs supportées: 'topsurveys', 'ysense', 'primeopinion', 'heycash', 'earnstar', 'fivesurveys'"
    )


def validate_platform_rotation(rotation) -> None:
    """
    Échoue immédiatement et explicitement si `rotation` (typiquement
    global_config.PLATFORM_ROTATION) contient un nom de plateforme non reconnu
    par get_platform() — plutôt que de risquer un crash imprévisible plus tard,
    seulement si la rotation retient justement cette entrée invalide.
    """
    unknown = [n for n in rotation if (n or "").strip().lower() not in KNOWN_PLATFORMS]
    if unknown:
        raise ValueError(
            f"PLATFORM_ROTATION contient {len(unknown)} plateforme(s) inconnue(s): {unknown!r}. "
            f"Valeurs supportées: {list(KNOWN_PLATFORMS)!r}"
        )