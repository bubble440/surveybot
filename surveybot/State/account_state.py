from __future__ import annotations
import os

try:
    from botocore.exceptions import ClientError as _BotoClientError
except ImportError:
    _BotoClientError = Exception  # type: ignore

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
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# En environnement non-local (docker / aws), le filesystem n'est PAS une source de vérité partagée.
# Donc: pas de fallback fichier → DynamoDB doit être correctement configuré.
STRICT_NO_FILE_FALLBACK = not IS_LOCAL

from decimal import Decimal
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
STATE_BACKEND = os.getenv("STATE_BACKEND", "").strip().lower()  # "dynamodb" ou "firestore" en prod
STATE_TABLE = os.getenv("STATE_TABLE", "").strip()             # ex: surveybot_account_state
AWS_REGION = os.getenv("AWS_REGION", "").strip()               # optionnel (boto3 peut le déduire)
STATE_TTL_DAYS = int(os.getenv("STATE_TTL_DAYS", "0") or "0")   # 0 = pas de TTL auto
GCP_PROJECT = os.getenv("GCP_PROJECT", "").strip()             # optionnel (ADC / metadata server sinon)

# Fallback fichier (debug seulement)
_STATE_DIR = Path(os.getenv("STATE_DIR", "/data/accounts"))
_FILE_LOCK = Lock()


def _today_str() -> str:
    return date.today().isoformat()


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _ts_add(seconds: int) -> str:
    from datetime import datetime, timezone, timedelta
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S")


def _ts_to_unix(ts) -> int:
    """Convertit ISO string ou int Unix → int Unix (pour calculs Python)."""
    if isinstance(ts, int):
        return ts
    if isinstance(ts, str) and ts and ts != "1970-01-01T00:00:00":
        from datetime import datetime, timezone
        return int(datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
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
        "lock_owner": "",
        "lock_until_ts": "1970-01-01T00:00:00",
        "proxy_id": "",          # identifiant du proxy

        "last_stop_reason": "",
        "last_heartbeat_ts": "1970-01-01T00:00:00",
        "last_boot_ts": "1970-01-01T00:00:00",
        "last_start_ts": "1970-01-01T00:00:00",
        "daily_earned": {},           # ex: {"2025-12-31": 1.23}
        "daily_target_start_ts": {},  # ex: {"2026-03-17": "2026-03-17T08:00:00"}
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
        if STRICT_NO_FILE_FALLBACK:
            raise RuntimeError(f"[STATE] DynamoDB indisponible en environnement non-local. err={e}")
        log.warning(f"[STATE] DynamoDB indisponible -> fallback fichier. err={e}")
        return None


# -----------------------------
# Firestore helpers
# -----------------------------
def _fs_enabled() -> bool:
    return STATE_BACKEND == "firestore" and bool(STATE_TABLE)

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
    conn.commit()

def _get_fs_client():
    """
    Retourne le client Firestore.
    GCP_PROJECT est utilisé si présent ; sinon google-cloud-firestore déduit le projet depuis ADC.
    Lève RuntimeError si l'import échoue et STRICT_NO_FILE_FALLBACK est True.
    """
    try:
        from google.cloud import firestore as _firestore  # type: ignore
        if GCP_PROJECT:
            return _firestore.Client(project=GCP_PROJECT)
        return _firestore.Client()
    except ImportError as e:
        if STRICT_NO_FILE_FALLBACK:
            raise RuntimeError(
                f"[STATE] google-cloud-firestore indisponible en environnement non-local. "
                f"Installer: pip install google-cloud-firestore. err={e}"
            )
        log.warning(f"[STATE] google-cloud-firestore indisponible -> fallback fichier. err={e}")
        return None
    except Exception as e:
        if STRICT_NO_FILE_FALLBACK:
            raise RuntimeError(f"[STATE] Firestore indisponible en environnement non-local. err={e}")
        log.warning(f"[STATE] Firestore indisponible -> fallback fichier. err={e}")
        return None


def _json_safe(obj: Any) -> Any:
    """
    DynamoDB renvoie parfois Decimal -> on convertit pour JSON.
    Appliqué récursivement sur les dicts et listes imbriqués (ex: daily_earned).
    """
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    return obj


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
    if IS_LOCAL and not os.getenv("FORCE_DYNAMODB"):
        # Utiliser fichier local mais avec la vraie logique
        with _FILE_LOCK:
            return _normalize_state(_file_load(account_id), account_id)
                
    account_id = (account_id or "").strip()
    if not account_id:
        raise ValueError("account_id vide")

    if STRICT_NO_FILE_FALLBACK and not _ddb_enabled() and not _fs_enabled():
        raise RuntimeError("[STATE] STATE_BACKEND (dynamodb|firestore) et STATE_TABLE requis en environnement non-local")

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
                if STRICT_NO_FILE_FALLBACK:
                    raise
                log.warning(f"[STATE] get_item failed -> fallback fichier. err={e}")

    # Backend Firestore: lecture simple (pas de transaction nécessaire pour un get).
    if _fs_enabled():
        client = _get_fs_client()
        if client is not None:
            try:
                doc_ref = client.collection(STATE_TABLE).document(account_id)
                doc = doc_ref.get()
                if not doc.exists:
                    st = _default_state(account_id)
                    # Crée le document au premier passage (idempotent).
                    doc_ref.set(st)
                    return st
                # Firestore renvoie des float natifs, pas de conversion Decimal nécessaire.
                return _normalize_state(doc.to_dict(), account_id)
            except Exception as e:
                if STRICT_NO_FILE_FALLBACK:
                    raise
                log.warning(f"[STATE] Firestore get failed -> fallback fichier. err={e}")

    # fallback fichier (local/debug seulement)
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
        return

    account_id = state.get("account_id", "").strip()
    if not account_id:
        raise ValueError("state sans account_id")

    st = _normalize_state(state, account_id)
    st["updated_ts"] = _now()

    if STRICT_NO_FILE_FALLBACK and not _ddb_enabled() and not _fs_enabled():
        raise RuntimeError("[STATE] STATE_BACKEND (dynamodb|firestore) et STATE_TABLE requis en environnement non-local")

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

    if _ddb_enabled():
        table = _get_ddb_table()
        if table is not None:
            try:
                table.put_item(Item=_to_dynamodb_compatible(st))
                return
            except Exception as e:
                if STRICT_NO_FILE_FALLBACK:
                    raise
                log.warning(f"[STATE] put_item failed -> fallback fichier. err={e}")

    # Backend Firestore: écriture complète du document (set remplace tout).
    if _fs_enabled():
        client = _get_fs_client()
        if client is not None:
            try:
                client.collection(STATE_TABLE).document(account_id).set(st)
                return
            except Exception as e:
                if STRICT_NO_FILE_FALLBACK:
                    raise
                log.warning(f"[STATE] Firestore set failed -> fallback fichier. err={e}")

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

    if STRICT_NO_FILE_FALLBACK and not _ddb_enabled() and not _fs_enabled():
        raise RuntimeError("[STATE] STATE_BACKEND (dynamodb|firestore) et STATE_TABLE requis en environnement non-local")

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

    # PROD: DynamoDB atomic update via version
    if _ddb_enabled():
        table = _get_ddb_table()
        if table is not None:
            for attempt in range(1, max_retries + 1):
                st = load_state(account_id)
                current_version = int(st.get("version", 0) or 0)

                fn(st)  # modifie en place
                st = _normalize_state(st, account_id)
                st["updated_ts"] = _now()
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
                except _BotoClientError as e:
                    code = e.response.get("Error", {}).get("Code", "")
                    if code == "ConditionalCheckFailedException":
                        if attempt < max_retries:
                            # M2: backoff exponentiel pour réduire la contention sous charge
                            time.sleep(0.1 * (2 ** (attempt - 1)))
                            continue
                        log.error(f"[STATE] update_state: conflit de version après {max_retries} tentatives. account={account_id}")
                        raise
                    # Erreur DynamoDB non-retryable (throttle, réseau, etc.)
                    log.error(f"[STATE] update_state: erreur DynamoDB non-retryable. code={code} account={account_id} err={e}")
                    raise
                except Exception as e:
                    log.error(f"[STATE] update_state: erreur inattendue. account={account_id} err={e}")
                    raise

    # Backend Firestore: optimistic locking via transaction Firestore.
    # @transactional gère les retries Firestore-level (erreur ABORTED sur contention).
    # Notre boucle externe gère les erreurs applicatives (ex: exception dans fn).
    if _fs_enabled():
        client = _get_fs_client()
        if client is not None:
            from google.cloud import firestore as _firestore  # type: ignore

            doc_ref = client.collection(STATE_TABLE).document(account_id)

            @_firestore.transactional
            def _apply_fn(transaction, doc_ref):
                """
                Lit l'état courant, applique fn(), incrémente version, et écrit atomiquement.
                Si un autre writer a modifié le document entre la lecture et l'écriture,
                Firestore abandonne et rejoue automatiquement la transaction (ABORTED retry).
                """
                snap = doc_ref.get(transaction=transaction)
                st = _normalize_state(snap.to_dict() if snap.exists else {}, account_id)
                current_version = int(st.get("version", 0) or 0)
                fn(st)  # modifie en place
                st = _normalize_state(st, account_id)
                st["updated_ts"] = _now()
                st["version"] = current_version + 1
                # set() remplace le document entier (équivalent put_item DynamoDB)
                transaction.set(doc_ref, st)
                return st

            for attempt in range(1, max_retries + 1):
                try:
                    return _apply_fn(client.transaction(), doc_ref)
                except Exception as e:
                    if attempt < max_retries:
                        time.sleep(0.1 * (2 ** (attempt - 1)))
                        continue
                    log.error(f"[STATE] update_state Firestore: échec après {max_retries} tentatives. account={account_id} err={e}")
                    raise

    # FALLBACK FILE (local/debug seulement)
    if STRICT_NO_FILE_FALLBACK:
        raise RuntimeError("[STATE] update_state: DynamoDB requis en environnement non-local (pas de fallback fichier).")

    with _FILE_LOCK:
        st = _normalize_state(_file_load(account_id), account_id)
        fn(st)
        st = _normalize_state(st, account_id)
        st["updated_ts"] = _now()
        st["version"] = int(st.get("version", 0) or 0) + 1
        _file_save(st)
        return st

def touch_heartbeat(account_id: str, owner: str, ttl_sec: int = 240) -> bool:
    """
    Heartbeat DynamoDB (cheap & safe):
    - UpdateExpression (pas de load_state + put_item)
    - Condition: lock_owner == owner (la task ne prolonge QUE son propre lock)
    - Prolonge lock_until_ts à now + ttl_sec (pas un +15 fragile)
    - ADD version +1 pour éviter qu'un update_state() écrase un heartbeat récent.
    """
    if IS_LOCAL:
        return True

    if STRICT_NO_FILE_FALLBACK and not _ddb_enabled() and not _fs_enabled():
        raise RuntimeError("[STATE] touch_heartbeat: STATE_BACKEND (dynamodb|firestore) requis en environnement non-local")

    if not _ddb_enabled() and not _fs_enabled():
        return False

    now = _now()
    expires = _ts_add(int(ttl_sec))

    if _pg_enabled():
        conn = _get_pg_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE account_state
                    SET state = jsonb_set(jsonb_set(state,
                        '{lock_until_ts}', to_jsonb(%s::text)),
                        '{last_heartbeat_ts}', to_jsonb(%s::text)),
                        updated_ts = now(),
                        version = version + 1
                    WHERE account_id = %s
                        AND state->>'lock_owner' = %s""",
                    (expires, now, account_id, owner)
                )
                updated = cur.rowcount
            conn.commit()
            return updated == 1
        finally:
            conn.close()

    if _ddb_enabled():
        table = _get_ddb_table()
        if table is None:
            return False

        try:
            table.update_item(
                Key={"account_id": account_id},
                UpdateExpression="""
                    SET lock_until_ts = :u,
                        last_heartbeat_ts = :now,
                        updated_ts = :now
                    ADD version :one
                """,
                ConditionExpression="lock_owner = :o",
                ExpressionAttributeValues=_to_dynamodb_compatible({
                    ":u": expires,
                    ":now": now,
                    ":o": owner,
                    ":one": 1,
                }),
            )
            return True
        except _BotoClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code != "ConditionalCheckFailedException":
                log.warning(f"[STATE] touch_heartbeat: erreur DynamoDB. account={account_id} code={code} err={e}")
            # ConditionalCheckFailedException = lock plus détenu, comportement normal
            return False
        except Exception as e:
            log.warning(f"[STATE] touch_heartbeat: erreur inattendue. account={account_id} err={e}")
            return False

    # Backend Firestore: écriture partielle (update) sans transaction.
    # Équivalent de l'UpdateExpression DynamoDB : mise à jour de champs spécifiques uniquement.
    # Note: Firestore update() ne supporte pas de condition atomique sans transaction ;
    # on se passe donc du check lock_owner (le heartbeat est à faible risque de race).
    if _fs_enabled():
        client = _get_fs_client()
        if client is None:
            return False
        try:
            from google.cloud import firestore as _firestore  # type: ignore
            client.collection(STATE_TABLE).document(account_id).update({
                "lock_until_ts": expires,
                "last_heartbeat_ts": now,
                "updated_ts": now,
                "version": _firestore.Increment(1),
            })
            return True
        except Exception as e:
            log.warning(f"[STATE] touch_heartbeat Firestore: erreur. account={account_id} err={e}")
            return False

# -----------------------------
# 🔐 ACCOUNT LOCK (CRITIQUE)
# -----------------------------

def try_acquire_account_lock(
    account_id: str,
    owner: str,
    ttl_sec: int = 180,
) -> bool:
    """
    Lock atomique du compte.
    Une seule task (bot ou scheduler) peut réussir.

    Retourne True si le lock est acquis, False sinon.
    """

    if IS_LOCAL:
        # 🧪 En local : on autorise toujours
        return True

    now = _now()
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
            existing_owner = st.get("lock_owner", "")
            lock_until = _ts_to_unix(st.get("lock_until_ts", "1970-01-01T00:00:00"))
            now_unix = int(time.time())
            if existing_owner and existing_owner != owner and lock_until >= now_unix:
                conn.rollback()
                return False
            # Acquiert le lock
            lock_fields = {"lock_owner": owner, "lock_until_ts": expires, "updated_ts": now}
            if row:
                st.update(lock_fields)
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE account_state SET state = %s::jsonb, updated_ts = now() WHERE account_id = %s",
                        (json.dumps(st), account_id)
                    )
            else:
                new_st = _default_state(account_id)
                new_st.update(lock_fields)
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO account_state (account_id, state, version) VALUES (%s, %s::jsonb, 0)",
                        (account_id, json.dumps(new_st))
                    )
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            log.warning(f"[STATE] try_acquire_account_lock postgres: err={e}")
            return False
        finally:
            conn.close()

    if not _ddb_enabled() and not _fs_enabled():
        if STRICT_NO_FILE_FALLBACK:
            raise RuntimeError("[STATE] try_acquire_account_lock: STATE_BACKEND (dynamodb|firestore) requis en environnement non-local")
        return False

    if _ddb_enabled():
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

        except _BotoClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code != "ConditionalCheckFailedException":
                log.warning(f"[STATE] try_acquire_account_lock: erreur DynamoDB. account={account_id} code={code} err={e}")
            # ConditionalCheckFailedException = lock déjà pris, comportement normal
            return False
        except Exception as e:
            log.warning(f"[STATE] try_acquire_account_lock: erreur inattendue. account={account_id} err={e}")
            return False

    # Backend Firestore: lecture + écriture conditionnelle dans une transaction atomique.
    # Équivalent du ConditionExpression DynamoDB :
    #   - pas de lock_owner  → libre
    #   - lock_owner == owner → renouvellement (même bot)
    #   - lock_until_ts expiré → lock périmé, on peut le voler
    if _fs_enabled():
        client = _get_fs_client()
        if client is None:
            return False
        try:
            from google.cloud import firestore as _firestore  # type: ignore

            doc_ref = client.collection(STATE_TABLE).document(account_id)
            now_unix = int(time.time())

            @_firestore.transactional
            def _acquire(transaction, doc_ref):
                snap = doc_ref.get(transaction=transaction)
                st = snap.to_dict() if snap.exists else {}

                existing_owner = st.get("lock_owner", "")
                lock_until = _ts_to_unix(st.get("lock_until_ts", "1970-01-01T00:00:00"))

                # Si un autre owner détient encore le lock (non expiré), on refuse.
                if existing_owner and existing_owner != owner and lock_until >= now_unix:
                    return False

                # Acquiert (ou renouvelle) le lock.
                lock_fields = {
                    "lock_owner": owner,
                    "lock_until_ts": expires,
                    "updated_ts": now,
                }
                if snap.exists:
                    transaction.update(doc_ref, lock_fields)
                else:
                    # Document inexistant : on crée avec l'état par défaut + lock.
                    new_st = _default_state(account_id)
                    new_st.update(lock_fields)
                    transaction.set(doc_ref, new_st)
                return True

            return _acquire(client.transaction(), doc_ref)

        except Exception as e:
            log.warning(f"[STATE] try_acquire_account_lock Firestore: erreur. account={account_id} err={e}")
            return False
