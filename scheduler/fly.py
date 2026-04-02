# scheduler/fly.py
#
# Remplace ecs.py pour Fly.io.
# Lance les bots via l'API Machines Fly.io (HTTP) — pas de CLI requis.
# Chaque machine est éphémère : elle se détruit automatiquement après exit.

import os, time
import requests

RUN_ENV = os.getenv("RUN_ENV", "local").lower()
IS_PROD = RUN_ENV not in ("local", "")

FLY_API_TOKEN   = os.getenv("FLY_API_TOKEN", "")
FLY_BOT_APP     = os.getenv("FLY_BOT_APP", "surveybot-bot")      # nom de l'app bot sur Fly
FLY_BOT_IMAGE   = os.getenv("FLY_BOT_IMAGE", "")                 # ex: registry.fly.io/surveybot-bot:latest
FLY_REGION      = os.getenv("FLY_REGION", "cdg")
FLY_VM_MEMORY   = int(os.getenv("FLY_VM_MEMORY", "2048"))        # MB
FLY_VM_CPUS     = int(os.getenv("FLY_VM_CPUS", "1"))

_API_BASE = "https://api.machines.dev/v1"


def _headers() -> dict:
    if not FLY_API_TOKEN:
        raise RuntimeError("[FLY] FLY_API_TOKEN manquant")
    return {
        "Authorization": f"Bearer {FLY_API_TOKEN}",
        "Content-Type": "application/json",
    }


# ============================================================================
# API publique (même interface que ecs.py)
# ============================================================================

def is_task_running(account_id: str) -> bool:
    """
    Sur Fly.io on ne s'appuie pas sur l'état des machines pour le contrôle de
    concurrence — c'est le lock Postgres/Firestore qui fait foi.
    Cette fonction est conservée pour compatibilité avec ecs_bot_scheduler.py
    mais retourne toujours False en prod Fly (le lock gère tout).
    """
    return False


def start_task(account_id: str, account: dict):
    """
    Lance une machine Fly.io éphémère pour le bot account_id.
    La machine se détruit automatiquement après la fin du processus (auto_destroy=True).
    """
    print(f"[SCHEDULER] Demande lancement bot account_id={account_id}")

    if not IS_PROD:
        _start_task_dry_run(account_id)
        return

    _start_task_fly(account_id, account)


# ============================================================================
# Local dry-run
# ============================================================================

def _start_task_dry_run(account_id: str):
    print(f"[SCHEDULER][LOCAL][DRY-RUN] would launch Fly machine for account_id={account_id}")


# ============================================================================
# Fly.io – Machines API
# ============================================================================

def _start_task_fly(account_id: str, account: dict):
    if not FLY_BOT_IMAGE:
        raise RuntimeError("[FLY] FLY_BOT_IMAGE manquant")

    # Variables injectées dans la machine bot (même ensemble qu'ECS)
    env = {
        "ACCOUNT_ID":   account_id,
        "RUN_ENV":      "prod",

        "EMAIL":        account.get("EMAIL", ""),
        "PASSWORD":     account.get("PASSWORD", ""),

        "PROXY_URL":    account.get("PROXY_URL", ""),
        "PROXY_USER":   account.get("PROXY_USER", ""),
        "PROXY_PASS":   account.get("PROXY_PASS", ""),

        "GEO_LAT":      str(account.get("GEO_LAT", "")),
        "GEO_LON":      str(account.get("GEO_LON", "")),
        "SURVEY_LANG":  account.get("SURVEY_LANG", "fr-FR"),
        "SURVEY_TZ":    account.get("SURVEY_TZ", "Europe/Paris"),

        # State backend transmis depuis le scheduler
        "STATE_BACKEND": os.getenv("STATE_BACKEND", "postgres"),
        "DATABASE_URL":  os.getenv("DATABASE_URL", ""),
    }

    # Transmission des clés optionnelles présentes dans le compte (SNAP_*, etc.)
    # Toute clé en majuscules non déjà définie est transmise telle quelle.
    _OPTIONAL_KEYS = [
        "SNAP_ENABLED",
        "SNAP_R2_ACCOUNT_ID",
        "SNAP_R2_ACCESS_KEY_ID",
        "SNAP_R2_SECRET_ACCESS_KEY",
        "SNAP_R2_BUCKET",
    ]
    for _k in _OPTIONAL_KEYS:
        _v = account.get(_k, "")
        if _v:
            env[_k] = str(_v)

    payload = {
        "name": f"bot-{account_id}-{int(time.time())}",   # nom unique par account
        "region": FLY_REGION,
        "config": {
            "image": FLY_BOT_IMAGE,
            "auto_destroy": True,               # destruction automatique après exit
            "restart": {
                "policy": "no"                  # pas de restart automatique
            },
            "guest": {
                "cpu_kind": "shared",
                "cpus": FLY_VM_CPUS,
                "memory_mb": FLY_VM_MEMORY,
            },
            "env": env,
        }
    }

    url = f"{_API_BASE}/apps/{FLY_BOT_APP}/machines"

    print(f"[SCHEDULER][FLY] POST {url} account_id={account_id}")

    resp = requests.post(url, json=payload, headers=_headers(), timeout=15)

    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"[FLY] Machines API error: status={resp.status_code} body={resp.text[:300]}"
        )

    machine_id = resp.json().get("id", "?")
    print(f"[SCHEDULER][FLY] machine lancée: {machine_id} pour {account_id}")