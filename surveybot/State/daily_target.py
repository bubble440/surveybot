from __future__ import annotations

from datetime import date
import time
from typing import Any, Dict, Optional
from State.account_state import _now, _ts_to_unix

DAILY_TARGET_EUR = 1.0


def today_str() -> str:
    return date.today().isoformat()


def format_elapsed_hms(elapsed_seconds: int) -> str:
    elapsed = max(0, int(elapsed_seconds))
    hours = elapsed // 3600
    minutes = (elapsed % 3600) // 60
    seconds = elapsed % 60
    return f"{hours:02d}-{minutes:02d}-{seconds:02d}"


def ensure_daily_timer_started(
    state: Dict[str, Any],
    *,
    now_ts=None,
    day: Optional[str] = None,
) -> bool:
    now_str = now_ts if now_ts is not None else _now()
    current_day = day or today_str()

    starts = state.setdefault("daily_target_start_ts", {})
    if current_day in starts:
        return False

    starts[current_day] = now_str
    return True


def record_daily_earning_and_target(
    state: Dict[str, Any],
    *,
    amount_eur: float,
    daily_target_eur: float = DAILY_TARGET_EUR,
    now_ts=None,
    day: Optional[str] = None,
) -> None:
    now_str = now_ts if now_ts is not None else _now()
    current_day = day or today_str()

    ensure_daily_timer_started(state, now_ts=now_str, day=current_day)

    daily_earned = state.setdefault("daily_earned", {})
    earned_today = float(daily_earned.get(current_day, 0.0) or 0.0) + float(amount_eur)
    daily_earned[current_day] = earned_today

    state["earnings_today_eur"] = earned_today
    state["last_gain_ts"] = now_str

    times = state.setdefault("daily_time_to_target_hms", {})
    if current_day in times:
        return

    if earned_today >= float(daily_target_eur):
        start_str = state["daily_target_start_ts"].get(current_day, now_str)
        elapsed = max(0, _ts_to_unix(now_str) - _ts_to_unix(start_str))
        hms = format_elapsed_hms(elapsed)
        times[current_day] = hms
        state["time_to_target_hms"] = hms
        state["time_to_target_day"] = current_day
