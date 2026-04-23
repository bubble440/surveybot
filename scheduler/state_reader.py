# scheduler/state_reader.py
#
# Lecture batch des états de compte depuis Postgres (lecture seule).
# Utilisé par le scheduler pour pré-filtrer les comptes en cooldown
# sans ouvrir N connexions par tick.

import os
import json
import time
import logging
from datetime import datetime, timezone, timedelta

log = logging.getLogger("state_reader")

_TZ = timezone(timedelta(hours=2))


def _pg_enabled() -> bool:
    db_url = os.getenv("DATABASE_URL", "").strip()
    backend = os.getenv("STATE_BACKEND", "").strip().lower()
    return backend == "postgres" and bool(db_url)


def load_states_batch(account_ids: list[str]) -> dict[str, dict]:
    """
    Charge les états de plusieurs comptes en une seule requête Postgres.
    Retourne {account_id: state_dict}.
    Les comptes absents de la table ont un état vide (considérés disponibles).
    En mode local / Postgres absent, retourne des états vides pour tous.
    """
    if not account_ids:
        return {}

    if not _pg_enabled():
        return {aid: {} for aid in account_ids}

    db_url = os.getenv("DATABASE_URL", "").strip()
    try:
        import psycopg2
        import psycopg2.extras

        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT account_id, state FROM account_state WHERE account_id = ANY(%s)",
                    (list(account_ids),),
                )
                rows = cur.fetchall()
        finally:
            conn.close()

        result: dict[str, dict] = {aid: {} for aid in account_ids}
        for row in rows:
            result[row["account_id"]] = dict(row["state"])
        return result

    except Exception as e:
        log.error(f"[STATE_READER] load_states_batch failed: {e} — tick annulé (fail-closed)")
        raise


def is_in_cooldown(state: dict) -> bool:
    """
    Retourne True si le compte est banni ou si son cooldown_until_ts est encore dans le futur.
    Même logique que try_acquire_cooldown_slot() dans account_state.py (pré-filtre uniquement).
    """
    if state.get("banned"):
        return True

    ts = state.get("cooldown_until_ts", "")
    if not ts or ts == "1970-01-01T00:00:00":
        return False

    try:
        cooldown_until = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=_TZ).timestamp()
        return cooldown_until > time.time()
    except Exception:
        return False
