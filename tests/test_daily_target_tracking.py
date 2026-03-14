import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "surveybot"))

from surveybot.State.daily_target import (
    DAILY_TARGET_EUR,
    ensure_daily_timer_started,
    format_elapsed_hms,
    record_daily_earning_and_target,
)
from surveybot.Management.guards.runtime_guard import RuntimeGuard


def test_daily_target_is_one_eur_everywhere():
    assert DAILY_TARGET_EUR == 1.0
    guard = RuntimeGuard(account_id="acc-test")
    assert guard.daily_target_eur == 1.0


def test_timer_starts_once_on_first_launch_of_day():
    state = {}

    started = ensure_daily_timer_started(state, now_ts=1000, day="2026-03-10")
    assert started is True
    assert state["daily_target_start_ts"]["2026-03-10"] == 1000

    started_again = ensure_daily_timer_started(state, now_ts=2000, day="2026-03-10")
    assert started_again is False
    assert state["daily_target_start_ts"]["2026-03-10"] == 1000


def test_timer_stops_at_target_and_persists_hms_format():
    state = {}

    record_daily_earning_and_target(
        state,
        amount_eur=0.40,
        daily_target_eur=1.0,
        now_ts=0,
        day="2026-03-10",
    )
    assert "2026-03-10" not in state.get("daily_time_to_target_hms", {})

    record_daily_earning_and_target(
        state,
        amount_eur=0.60,
        daily_target_eur=1.0,
        now_ts=2232,
        day="2026-03-10",
    )

    assert state["daily_time_to_target_hms"]["2026-03-10"] == "00-37-12"
    assert state["time_to_target_hms"] == "00-37-12"
    assert state["time_to_target_day"] == "2026-03-10"


def test_time_to_target_is_not_overwritten_once_reached_same_day():
    state = {}

    record_daily_earning_and_target(
        state,
        amount_eur=1.0,
        daily_target_eur=1.0,
        now_ts=10,
        day="2026-03-10",
    )
    first_value = state["daily_time_to_target_hms"]["2026-03-10"]

    record_daily_earning_and_target(
        state,
        amount_eur=0.50,
        daily_target_eur=1.0,
        now_ts=500,
        day="2026-03-10",
    )

    assert state["daily_time_to_target_hms"]["2026-03-10"] == first_value


def test_new_day_starts_new_timer_and_tracks_new_hms():
    state = {}

    ensure_daily_timer_started(state, now_ts=50, day="2026-03-10")
    ensure_daily_timer_started(state, now_ts=70, day="2026-03-11")

    assert state["daily_target_start_ts"]["2026-03-10"] == 50
    assert state["daily_target_start_ts"]["2026-03-11"] == 70

    record_daily_earning_and_target(
        state,
        amount_eur=1.0,
        daily_target_eur=1.0,
        now_ts=130,
        day="2026-03-11",
    )

    assert state["daily_time_to_target_hms"]["2026-03-11"] == "00-01-00"


def test_format_elapsed_hms_helper():
    assert format_elapsed_hms(0) == "00-00-00"
    assert format_elapsed_hms(59) == "00-00-59"
    assert format_elapsed_hms(2232) == "00-37-12"
