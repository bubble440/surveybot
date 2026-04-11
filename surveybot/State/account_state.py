from __future__ import annotations
import os

# State/account_state.py
"""
Stockage d'état "prod-first" pour 100+ bots.

Objectif:
- Scheduler et bots lisent/écrivent le même état via Postgres (source de vérité).
- Aucun partage de filesystem entre conteneurs.
- Fallback automatique sur fichiers si Postgres n'est pas configuré (utile pour tests, debug).
- Update atomique via row-level locking (SELECT FOR UPDATE).
"""

# State/account_state.py

RUN_ENV = os.getenv("RUN_ENV", "local").lower()
IS_LOCAL = RUN_ENV == "local"
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# En environnement non-local (prod), le filesystem n'est PAS une source de vérité partagée.
# Donc: pas de fallback fichier → Postgres doit être correctement configuré.
STRICT_NO_FILE_FALLBACK = not IS_LOCAL

import json
import time
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from threading import Lock
from typing import Callable, Dict, Any

log = logging.getLogger("account_state")

# -----------------------------
# Config backend
# -----------------------------
STATE_BACKEND = os.getenv("STATE_BACKEND", "").strip().lower()  # "postgres" en prod
STATE_TABLE = os.getenv("STATE_TABLE", "").strip()             # ex: surveybot_account_state
STATE_TTL_DAYS = int(os.getenv("STATE_TTL_DAYS", "0") or "0")   # 0 = pas de TTL auto

# Fallback fichier (debug seulement)
_default_state_dir = (
    Path(__file__).parent.parent / "data" / "accounts"
    if IS_LOCAL
    else Path("/data/accounts")
)
_STATE_DIR = Path(os.getenv("STATE_DIR", str(_default_state_dir)))
_FILE_LOCK = Lock()


def _today_str() -> str:
    return date.today().isoformat()


def _now() -> str:
    from datetime import datetime, timezone, timedelta
    tz = timezone(timedelta(hours=2))
    return datetime.now(tz).strftime("%Y-%m-%dT%H:%M:%S")


def _ts_add(seconds: int) -> str:
    from datetime import datetime, timezone, timedelta
    tz = timezone(timedelta(hours=2))
    return (datetime.now(tz) + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S")


def _ts_to_unix(ts) -> int:
    if isinstance(ts, int):
        return ts
    if isinstance(ts, str) and ts and ts != "1970-01-01T00:00:00":
        from datetime import datetime, timezone, timedelta
        tz = timezone(timedelta(hours=2))
        return int(datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=tz).timestamp())
    return 0

def _default_state(account_id: str) -> Dict[str, Any]:
    """
    Structure par défaut: garde ça minimal pour rester compatible avec les évolutions.
    """
    return {
        "account_id": account_id,
        "version": 0,                 # pour optimistic locking
        "banned": False,
        "cooldown_until_ts": "1970-01-01T00:00:00",
        "status": "idle",

        "last_stop_reason": "",
        "last_heartbeat_ts": "1970-01-01T00:00:00",
        "last_boot_ts": "1970-01-01T00:00:00",
        "last_start_ts": "1970-01-01T00:00:00",
        "daily_earned": {},           # ex: {"2025-12-31": 1.23}
        "daily_target_start_ts": {},  # ex: {"2026-03-17": "2026-03-17T08:00:00"}
        "daily_balance_start": {},    # ex: {"2026-04-10": 2.50} — solde lu au premier lancement du jour
        "daily_balance_target": {},   # ex: {"2026-04-10": 3.50} — objectif de solde courant pour la journée
        "daily_balance_gained": {},   # ex: {"2026-04-10": 1.00} — gain journalier cumulé (survit aux retraits)
        "total_earned": 0.0,
        "updated_ts": _now(),
    }


# -----------------------------
# Postgres helpers
# -----------------------------
def _pg_enabled() -> bool:
    return STATE_BACKEND == "postgres" and bool(DATABASE_URL)

def _get_pg_conn():
    """
    Connexion Postgres via psycopg2.
    DATABASE_URL injecté par Fly.io (fly postgres attach).
    """
    try:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        return conn
    except ImportError as e:
        raise RuntimeError(f"[STATE] psycopg2 non disponible. pip install psycopg2-binary. err={e}")
    except Exception as e:
        raise RuntimeError(f"[STATE] Postgres indisponible. err={e}")

def _pg_ensure_table(conn) -> None:
    """Crée la table si elle n'existe pas encore (idempotent)."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS account_state (
                account_id TEXT PRIMARY KEY,
                state      JSONB NOT NULL,
                version    INTEGER NOT NULL DEFAULT 0,
                updated_ts TIMESTAMPTZ DEFAULT now()
            )
        """)
        cur.execute("""
            ALTER TABLE account_state
            ADD COLUMN IF NOT EXISTS datadome_cookies JSONB DEFAULT '{}'
        """)
    conn.commit()


def _normalize_state(st: Dict[str, Any], account_id: str) -> Dict[str, Any]:
    """
    Assure que l'état contient les champs critiques.
    Ne casse pas si on ajoute des champs plus tard.
    """
    base = _default_state(account_id)
    base.update(st or {})
    base["account_id"] = account_id
    # NE PAS écraser updated_ts ici : _normalize_state est aussi appelé par load_state
    # (lecture seule). updated_ts est positionné à l'écriture dans save_state/update_state.

    # TTL optionnel pour purge (si TTL activé dans la table)
    if STATE_TTL_DAYS > 0:
        base["ttl_ts"] = _ts_add(STATE_TTL_DAYS * 86400)

    return base


# -----------------------------
# Backend FILE (fallback)
# -----------------------------
def _file_path(account_id: str) -> Path:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    return _STATE_DIR / f"{account_id}.json"


def _file_load(account_id: str) -> Dict[str, Any]:
    path = _file_path(account_id)
    if not path.is_file():
        return _default_state(account_id)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        # fichier corrompu -> on repart propre
        return _default_state(account_id)


def _file_save(state: Dict[str, Any]) -> None:
    path = _file_path(state["account_id"])
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


# -----------------------------
# Public API (utilisée partout)
# -----------------------------
def load_state(account_id: str) -> Dict[str, Any]:
    # En local, utiliser le fichier fallback pour tester la logique
    if IS_LOCAL:
        with _FILE_LOCK:
            return _normalize_state(_file_load(account_id), account_id)

    account_id = (account_id or "").strip()
    if not account_id:
        raise ValueError("account_id vide")

    if STRICT_NO_FILE_FALLBACK and not _pg_enabled():
        raise RuntimeError("[STATE] STATE_BACKEND (postgresql) et STATE_TABLE requis en environnement non-local")

    if _pg_enabled():
        conn = _get_pg_conn()
        _pg_ensure_table(conn)
        try:
            import psycopg2.extras
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT state FROM account_state WHERE account_id = %s", (account_id,))
                row = cur.fetchone()
            if not row:
                st = _default_state(account_id)
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO account_state (account_id, state, version) VALUES (%s, %s::jsonb, 0) ON CONFLICT DO NOTHING",
                        (account_id, json.dumps(st))
                    )
                conn.commit()
                return st
            return _normalize_state(dict(row["state"]), account_id)
        finally:
            conn.close()

    # fallback fichier (local/debug seulement)
    with _FILE_LOCK:
        return _normalize_state(_file_load(account_id), account_id)

def save_state(state: Dict[str, Any]) -> None:
    """
    Sauvegarde directe (rarement utile).
    En prod on préfère update_state().
    """
    if IS_LOCAL:
        return

    account_id = state.get("account_id", "").strip()
    if not account_id:
        raise ValueError("state sans account_id")

    st = _normalize_state(state, account_id)
    st["updated_ts"] = _now()

    if STRICT_NO_FILE_FALLBACK and not _pg_enabled():
        raise RuntimeError("[STATE] STATE_BACKEND (postgresql) et STATE_TABLE requis en environnement non-local")

    if _pg_enabled():
        conn = _get_pg_conn()
        _pg_ensure_table(conn)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO account_state (account_id, state, version, updated_ts)
                    VALUES (%s, %s::jsonb, %s, now())
                    ON CONFLICT (account_id) DO UPDATE
                    SET state = EXCLUDED.state, version = EXCLUDED.version, updated_ts = now()""",
                    (account_id, json.dumps(st), st.get("version", 0))
                )
            conn.commit()
            return
        finally:
            conn.close()

    # fallback fichier (local/debug seulement)
    with _FILE_LOCK:
        _file_save(st)


def update_state(account_id: str, fn: Callable[[Dict[str, Any]], None], max_retries: int = 5):
    """
    Update atomique:
    - charge l'état
    - applique fn(state)
    - écrit avec contrôle de version (optimistic locking)
    - retry si concurrence (scheduler + bot peuvent toucher le même item)
    """
    # 🧪 LOCAL MODE : no-op volontaire
    if IS_LOCAL:
        return {}

    account_id = (account_id or "").strip()
    if not account_id:
        raise ValueError("account_id vide")

    if STRICT_NO_FILE_FALLBACK and not _pg_enabled():
        raise RuntimeError("[STATE] STATE_BACKEND (postgresql) et STATE_TABLE requis en environnement non-local")

    if _pg_enabled():
        conn = _get_pg_conn()
        _pg_ensure_table(conn)
        try:
            import psycopg2.extras
            for attempt in range(1, max_retries + 1):
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    # SELECT FOR UPDATE = lock row-level atomique
                    cur.execute(
                        "SELECT state, version FROM account_state WHERE account_id = %s FOR UPDATE",
                        (account_id,)
                    )
                    row = cur.fetchone()
                if not row:
                    st = _default_state(account_id)
                    current_version = 0
                else:
                    st = _normalize_state(dict(row["state"]), account_id)
                    current_version = row["version"]
                fn(st)
                st = _normalize_state(st, account_id)
                st["updated_ts"] = _now()
                st["version"] = current_version + 1
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO account_state (account_id, state, version, updated_ts)
                        VALUES (%s, %s::jsonb, %s, now())
                        ON CONFLICT (account_id) DO UPDATE
                        SET state = EXCLUDED.state, version = EXCLUDED.version, updated_ts = now()
                        WHERE account_state.version = %s""",
                        (account_id, json.dumps(st), st["version"], current_version)
                    )
                    updated = cur.rowcount
                conn.commit()
                if updated == 1:
                    return st
                # rowcount == 0 = conflit de version, on retry
                if attempt < max_retries:
                    time.sleep(0.1 * (2 ** (attempt - 1)))
            log.error(f"[STATE] update_state postgres: conflit après {max_retries} tentatives. account={account_id}")
            raise RuntimeError("optimistic lock failed")
        finally:
            conn.close()

    # FALLBACK FILE (local/debug seulement)
    if STRICT_NO_FILE_FALLBACK:
        raise RuntimeError("[STATE] update_state: postgres requis en environnement non-local (pas de fallback fichier).")

    with _FILE_LOCK:
        st = _normalize_state(_file_load(account_id), account_id)
        fn(st)
        st = _normalize_state(st, account_id)
        st["updated_ts"] = _now()
        st["version"] = int(st.get("version", 0) or 0) + 1
        _file_save(st)
        return st

def touch_heartbeat(account_id: str, ttl_sec: int = 240) -> bool:
    """
    Heartbeat Postgres (cheap & safe):
    - UPDATE ciblé (pas de load_state + put complet)
    - Condition: status == 'running' (seul le bot actif prolonge son slot)
    - Prolonge cooldown_until_ts à now + ttl_sec
    - Incrémente version pour éviter qu'un update_state() écrase un heartbeat récent.
    """
    if IS_LOCAL:
        return True

    if STRICT_NO_FILE_FALLBACK and not _pg_enabled():
        raise RuntimeError("[STATE] touch_heartbeat: STATE_BACKEND (postgresql) requis en environnement non-local")

    if not _pg_enabled():
        return False

    now = _now()
    expires = _ts_add(int(ttl_sec))

    conn = _get_pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE account_state
                SET state = jsonb_set(jsonb_set(state,
                    '{cooldown_until_ts}', to_jsonb(%s::text)),
                    '{last_heartbeat_ts}', to_jsonb(%s::text)),
                    updated_ts = now(),
                    version = version + 1
                WHERE account_id = %s
                    AND state->>'status' = 'running'""",
                (expires, now, account_id)
            )
            updated = cur.rowcount
        conn.commit()
        return updated == 1
    finally:
        conn.close()

# -----------------------------
# 🔐 ACCOUNT LOCK (CRITIQUE)
# -----------------------------

def try_acquire_cooldown_slot(
    account_id: str,
    ttl_sec: int = 240,
) -> bool:
    """
    Vérification atomique du cooldown (SELECT FOR UPDATE).
    Si cooldown_until_ts est expiré : le bot peut démarrer.
      → cooldown_until_ts = now + ttl_sec (slot actif, prolongé par heartbeat)
      → status = 'running'
    Si cooldown_until_ts est dans le futur : un bot tourne déjà ou la pause n'est pas écoulée.
      → exit immédiat, la machine est détruite.

    Retourne True si le bot peut s'exécuter, False sinon.
    """

    if IS_LOCAL:
        return True

    now_unix = int(time.time())
    expires = _ts_add(ttl_sec)

    if _pg_enabled():
        conn = _get_pg_conn()
        try:
            import psycopg2.extras
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT state FROM account_state WHERE account_id = %s FOR UPDATE",
                    (account_id,)
                )
                row = cur.fetchone()
            st = dict(row["state"]) if row else {}
            cooldown_until = _ts_to_unix(st.get("cooldown_until_ts", "1970-01-01T00:00:00"))
            if cooldown_until >= now_unix:
                conn.rollback()
                return False
            # Slot acquis : marquer le bot comme actif
            slot_fields = {
                "cooldown_until_ts": expires,
                "status": "running",
                "updated_ts": _now(),
            }
            if row:
                st.update(slot_fields)
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE account_state SET state = %s::jsonb, updated_ts = now() WHERE account_id = %s",
                        (json.dumps(st), account_id)
                    )
            else:
                new_st = _default_state(account_id)
                new_st.update(slot_fields)
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO account_state (account_id, state, version) VALUES (%s, %s::jsonb, 0)",
                        (account_id, json.dumps(new_st))
                    )
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            log.warning(f"[STATE] try_acquire_cooldown_slot postgres: err={e}")
            return False
        finally:
            conn.close()

    if STRICT_NO_FILE_FALLBACK:
        raise RuntimeError("[STATE] try_acquire_cooldown_slot: STATE_BACKEND (postgresql) requis en environnement non-local")
    return False


# -----------------------------
# 🍪 DataDome cookie persistence
# -----------------------------

def save_datadome_cookie(account_id: str, domain: str, cookie_value: str) -> None:
    """
    Persiste le cookie datadome pour un domaine donné.
    Plusieurs domaines par compte sont supportés (stockés dans datadome_cookies JSONB).
    No-op si STATE_BACKEND != postgres ou si un argument est vide.
    """
    if not _pg_enabled():
        return
    if not account_id or not domain or not cookie_value:
        return
    conn = _get_pg_conn()
    _pg_ensure_table(conn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE account_state
                   SET datadome_cookies = COALESCE(datadome_cookies, '{}'::jsonb)
                                         || jsonb_build_object(%s::text, %s::text),
                       updated_ts = now()
                   WHERE account_id = %s""",
                (domain, cookie_value, account_id)
            )
        conn.commit()
    except Exception as e:
        log.warning(f"[STATE] save_datadome_cookie: err={e}")
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()


def load_datadome_cookies(account_id: str) -> Dict[str, str]:
    """
    Charge les cookies datadome persistés pour ce compte.
    Retourne un dict {domain: cookie_value}, vide si aucun ou si STATE_BACKEND != postgres.
    """
    if not _pg_enabled():
        return {}
    if not account_id:
        return {}
    conn = _get_pg_conn()
    _pg_ensure_table(conn)
    try:
        import psycopg2.extras
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT datadome_cookies FROM account_state WHERE account_id = %s",
                (account_id,)
            )
            row = cur.fetchone()
        if not row or not row["datadome_cookies"]:
            return {}
        return dict(row["datadome_cookies"])
    except Exception as e:
        log.warning(f"[STATE] load_datadome_cookies: err={e}")
        return {}
    finally:
        conn.close()
