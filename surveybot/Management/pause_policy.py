from enum import Enum, auto
from datetime import datetime, timedelta
import time
from zoneinfo import ZoneInfo

class PausePolicy(Enum):
    """
    Politique centrale de pause du bot.
    Une policy = une intention métier claire.
    """
    NONE = auto()                  # pas de pause
    SHORT_COOLDOWN = auto()        # incidents légers
    MEDIUM_COOLDOWN = auto()       # erreurs / no-gain
    LONG_COOLDOWN = auto()         # environnement défavorable
    DAILY_RESET = auto()           # objectif journalier atteint
    UNTIL_MANUAL = auto()          # intervention humaine requise


# ================================
# Résolution des durées de pause
# ================================

def resolve_pause_seconds(
    policy: PausePolicy,
    *,
    tz_name: str = "Europe/Paris",
) -> int:
    """
    Retourne le nombre de secondes de pause
    associées à une PausePolicy.
    """

    if policy == PausePolicy.NONE:
        return 0

    if policy == PausePolicy.SHORT_COOLDOWN:
        return 60 * 2            # 2 minutes

    if policy == PausePolicy.MEDIUM_COOLDOWN:
        return 60 * 5           # 5 minutes

    if policy == PausePolicy.LONG_COOLDOWN:
        return 60 * 30       # ✅ 30 min max, le scheduler relance et ré-auth

    if policy == PausePolicy.DAILY_RESET:
        tz = ZoneInfo(tz_name)
        now_dt = datetime.now(tz)

        tomorrow = now_dt.date() + timedelta(days=1)
        midnight = datetime.combine(
            tomorrow,
            datetime.min.time(),
            tzinfo=tz,
        )

        return int((midnight - now_dt).total_seconds())

    if policy == PausePolicy.UNTIL_MANUAL:
        return 60 * 60 * 24 * 365  # 1 an (effectivement infini)

    raise ValueError(f"PausePolicy inconnue: {policy}")
