from __future__ import annotations

from global_config import PLATFORM_DAILY_TARGET
from Management.guards.runtime_guard import get_guard, StopReason
from Management.pause_policy import PausePolicy
from State.account_state import load_state, update_state
from State.daily_target import init_daily_balance_target, today_str
from Survey.log_utils import log_debug, log_info

_TAG = "[DAILY_STOP]"


def check_and_stop_if_daily_target_reached(account_id: str, platform_name: str, balance: float) -> bool:
    """
    Généralisation par plateforme de la section DAILY STOP de
    Cash/payout.py::_payout_and_check_daily_stop. Compare `balance` (déjà lue
    par l'appelant, dans l'unité native de la plateforme — aucune conversion
    ici) à state["platforms"][platform_name]["daily_balance_target"] du jour,
    recalculée de façon défensive depuis daily_balance_start/gained pour
    éviter un faux DAILY_STOP sur une valeur target corrompue. Si atteinte,
    appelle get_guard().pause(DAILY_RESET, DAILY_TARGET_REACHED) — lève
    SystemExit, ne retourne jamais dans ce cas.

    KeyError volontairement non rattrapé si `platform_name` est absent de
    PLATFORM_DAILY_TARGET : la validation de démarrage
    (platforms.validate_platform_daily_targets) doit déjà avoir éliminé ce
    cas — ceci n'est qu'un filet de cohérence, pas un chemin normal.
    """
    daily_target = PLATFORM_DAILY_TARGET[platform_name]

    try:
        _today = today_str()
        update_state(account_id, lambda st: init_daily_balance_target(
            st.setdefault("platforms", {}).setdefault(platform_name, {}), balance, _today
        ))

        state = load_state(account_id)
        platform_state = state.get("platforms", {}).get(platform_name, {})

        start = float(platform_state.get("daily_balance_start", {}).get(_today, balance))
        gained = float(platform_state.get("daily_balance_gained", {}).get(_today, 0.0))
        target = (start - gained) + daily_target
    except Exception as e:
        log_debug(
            _TAG,
            f"check_and_stop_if_daily_target_reached({platform_name}) — erreur non bloquante, "
            f"pas d'arrêt sur un état non fiable : {e}",
        )
        return False

    if balance >= target:
        log_info(
            _TAG,
            f"{platform_name} — solde {balance:.2f} >= objectif {target:.2f} → arrêt journalier",
        )
        get_guard().pause(PausePolicy.DAILY_RESET, StopReason.DAILY_TARGET_REACHED)
        return True  # jamais atteint (pause lève SystemExit)

    return False
