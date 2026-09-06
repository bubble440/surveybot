from __future__ import annotations

import os


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _explicit_log_level() -> str | None:
    raw = (os.getenv("LOG_LEVEL", "") or "").strip()
    return raw.upper() if raw else None


def current_log_level() -> str:
    explicit = _explicit_log_level()
    if explicit:
        return explicit
    if _truthy(os.getenv("DOM_DEBUG_FRAMES")) or _truthy(os.getenv("ACTION_DEBUG_TARGET")):
        return "DEBUG"
    return "INFO"


def is_debug() -> bool:
    return current_log_level() == "DEBUG"


def is_step_summary_enabled() -> bool:
    raw = os.getenv("LOG_STEP_SUMMARY", "0")
    return _truthy(raw)


def _fmt(tag: str, msg: str) -> str:
    if tag.startswith("["):
        return f"{tag} {msg}"
    return f"[{tag}] {msg}"


def log_info(tag: str, msg: str) -> None:
    print(_fmt(tag, msg))


def log_debug(tag: str, msg: str) -> None:
    if is_debug():
        print(_fmt(tag, msg))
