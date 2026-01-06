from __future__ import annotations
import os

# State/account_state.py
"""
Stockage d'état "prod-first" pour 100+ bots.

Objectif:
- Scheduler et bots lisent/écrivent le même état via DynamoDB (source de vérité).
- Aucun partage de filesystem entre conteneurs.
- Fallback automatique sur fichiers si DynamoDB n'est pas configuré (utile pour tests, debug).
- Update atomique via "optimistic locking" (champ version).
"""

# State/account_state.py

RUN_ENV = os.getenv("RUN_ENV", "local").lower()
IS_LOCAL = RUN_ENV == "local"

from decimal import Decimal
import json
import time
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from threading import Lock
from typing import Callable, Dict, Optional, Any

log = logging.getLogger("account_state")

# -----------------------------
# Config backend
# -----------------------------
STATE_BACKEND = os.getenv("STATE_BACKEND", "").strip().lower()  # "dynamodb" recommandé en prod
STATE_TABLE = os.getenv("STATE_TABLE", "").strip()             # ex: surveybot_account_state
AWS_REGION = os.getenv("AWS_REGION", "").strip()               # optionnel (boto3 peut le déduire)
STATE_TTL_DAYS = int(os.getenv("STATE_TTL_DAYS", "0") or "0")   # 0 = pas de TTL auto

# Fallback fichier (debug seulement)
_STATE_DIR = Path(os.getenv("STATE_DIR", "/data/accounts"))
_FILE_LOCK = Lock()


def _today_str() -> str:
    return date.today().isoformat()


def _now() -> int:
    return int(time.time())


def _default_state(account_id: str) -> Dict[str, Any]:
    """
    Structure par défaut: garde ça minimal pour rester compatible avec les évolutions.
    """
    return {
        "account_id": account_id,
        "version": 0,                 # pour optimistic locking
        "banned": False,
        "cooldown_until_ts": 0,
        "status": "idle",
        "lock_owner": "",
        "lock_until_ts": 0,
        "proxy_id": "",          # identifiant du proxy
        "proxy_lock_owner": "",  # account_id du bot actif
        "proxy_lock_until_ts": 0,

        "last_stop_reason": "",
        "last_heartbeat_ts": 0,
        "daily_earned": {},           # ex: {"2025-12-31": 1.23}
        "total_earned": 0.0,
        "updated_ts": _now(),
    }


# -----------------------------
# DynamoDB helpers
# -----------------------------
def _ddb_enabled() -> bool:
    return STATE_BACKEND == "dynamodb" and bool(STATE_TABLE)


def _get_ddb_table():
    """
    Retourne l'objet table DynamoDB.
    Fallback silencieux si boto3 absent ou mal configuré.
    """
    try:
        import boto3  # type: ignore
        if AWS_REGION:
            resource = boto3.resource("dynamodb", region_name=AWS_REGION)
        else:
            resource = boto3.resource("dynamodb")
        return resource.Table(STATE_TABLE)
    except Exception as e:
        log.warning(f"[STATE] DynamoDB indisponible -> fallback fichier. err={e}")
        return None


def _json_safe(obj: Any) -> Any:
    """
    DynamoDB renvoie parfois Decimal -> on convertit pour JSON.
    """
    try:
        from decimal import Decimal  # type: ignore
        if isinstance(obj, Decimal):
            # Si entier exact -> int, sinon float
            return int(obj) if obj % 1 == 0 else float(obj)
    except Exception:
        pass
    return obj


def _normalize_state(st: Dict[str, Any], account_id: str) -> Dict[str, Any]:
    """
    Assure que l'état contient les champs critiques.
    Ne casse pas si on ajoute des champs plus tard.
    """
    base = _default_state(account_id)
    base.update(st or {})
    base["account_id"] = account_id
    base["updated_ts"] = _now()

    # TTL optionnel pour purge (si TTL activé dans la table)
    if STATE_TTL_DAYS > 0:
        base["ttl_ts"] = _now() + (STATE_TTL_DAYS * 86400)

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
    """
    Charge l'état depuis DynamoDB (prod) ou fallback fichier.
    """
    if IS_LOCAL:
        return _default_state(account_id)
    
    account_id = (account_id or "").strip()
    if not account_id:
        raise ValueError("account_id vide")

    if _ddb_enabled():
        table = _get_ddb_table()
        if table is not None:
            try:
                resp = table.get_item(Key={"account_id": account_id})
                item = resp.get("Item")
                if not item:
                    st = _default_state(account_id)
                    # on crée l'item au premier passage (idempotent)
                    table.put_item(Item=_to_dynamodb_compatible(st))
                    return st
                # conversion Decimal -> JSON friendly
                normalized = {k: _json_safe(v) for k, v in item.items()}
                return _normalize_state(normalized, account_id)
            except Exception as e:
                log.warning(f"[STATE] get_item failed -> fallback fichier. err={e}")

    # fallback fichier
    with _FILE_LOCK:
        return _normalize_state(_file_load(account_id), account_id)

def _to_dynamodb_compatible(value):
    """
    Convertit récursivement les types Python
    vers des types compatibles DynamoDB.
    """
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_dynamodb_compatible(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_dynamodb_compatible(v) for v in value]
    return value

def save_state(state: Dict[str, Any]) -> None:
    """
    Sauvegarde directe (rarement utile).
    En prod on préfère update_state().
    """
    if IS_LOCAL:
        return {}

    account_id = state.get("account_id", "").strip()
    if not account_id:
        raise ValueError("state sans account_id")

    st = _normalize_state(state, account_id)

    if _ddb_enabled():
        table = _get_ddb_table()
        if table is not None:
            try:
                table.put_item(Item=_to_dynamodb_compatible(st))
                return
            except Exception as e:
                log.warning(f"[STATE] put_item failed -> fallback fichier. err={e}")

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

    # PROD: DynamoDB atomic update via version
    if _ddb_enabled():
        table = _get_ddb_table()
        if table is not None:
            for attempt in range(1, max_retries + 1):
                st = load_state(account_id)
                current_version = int(st.get("version", 0) or 0)

                fn(st)  # modifie en place
                st = _normalize_state(st, account_id)
                st["version"] = current_version + 1

                try:
                    # Condition: on n'écrase pas si une autre écriture a eu lieu entre temps
                    table.put_item(
                        Item=_to_dynamodb_compatible(st),
                        ConditionExpression="attribute_not_exists(version) OR version = :v",
                        ExpressionAttributeValues=_to_dynamodb_compatible({
                            ":v": current_version
                        }),
                    )
                    return st
                except Exception as e:
                    # typiquement ConditionalCheckFailedException
                    if attempt < max_retries:
                        time.sleep(0.05 * attempt)  # petit backoff
                        continue
                    log.error(f"[STATE] update_state failed after retries. err={e}")
                    raise

    # FALLBACK FILE: lock process-local
    with _FILE_LOCK:
        st = _normalize_state(_file_load(account_id), account_id)
        fn(st)
        st = _normalize_state(st, account_id)
        st["version"] = int(st.get("version", 0) or 0) + 1
        _file_save(st)
        return st

def try_acquire_proxy_lock(proxy_id: str, account_id: str, ttl_sec: int) -> bool:
    now = int(time.time())

    def _lock(st):
        # si verrou libre ou expiré
        if not st.get("proxy_lock_owner") or st.get("proxy_lock_until_ts", 0) < now:
            st["proxy_lock_owner"] = account_id
            st["proxy_lock_until_ts"] = now + ttl_sec
            return True
        return False

    success = False

    def _apply(st):
        nonlocal success
        if _lock(st):
            success = True

    update_state(account_id, _apply)
    return success

# -----------------------------
# 🔐 ACCOUNT LOCK (CRITIQUE)
# -----------------------------

def try_acquire_account_lock(
    account_id: str,
    owner: str,
    ttl_sec: int = 900,
) -> bool:
    """
    Lock atomique du compte.
    Une seule task (bot ou scheduler) peut réussir.

    Retourne True si le lock est acquis, False sinon.
    """

    if IS_LOCAL:
        # 🧪 En local : on autorise toujours
        return True

    now = int(time.time())
    expires = now + ttl_sec

    if not _ddb_enabled():
        # fallback fichier : lock naïf (acceptable en local/debug)
        st = load_state(account_id)
        if st.get("lock_until_ts", 0) > now:
            return False
        update_state(account_id, lambda s: s.update({
            "lock_owner": owner,
            "lock_until_ts": expires,
        }))
        return True

    table = _get_ddb_table()
    if table is None:
        return False

    try:
        table.update_item(
            Key={"account_id": account_id},
            UpdateExpression="""
                SET lock_owner = :o,
                    lock_until_ts = :u,
                    updated_ts = :now
            """,
            ConditionExpression="""
                attribute_not_exists(lock_owner)
                OR lock_owner = :o
                OR lock_until_ts < :now
            """,
            ExpressionAttributeValues=_to_dynamodb_compatible({
                ":o": owner,
                ":u": expires,
                ":now": now,
            }),
        )
        return True

    except Exception as e:
        # ConditionalCheckFailedException = lock déjà pris
        return False
