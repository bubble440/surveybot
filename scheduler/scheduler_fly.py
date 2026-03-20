"""
scheduler_fly.py
Lit accounts.json et lance une machine Fly.io éphémère par compte.
Tourne toutes les 5 minutes (cron interne ou GCP Scheduler).
Les secrets globaux (OPENAI_API_KEY, DATABASE_URL, etc.) sont déjà
injectés par Fly.io via `flyctl secrets set` — on ne les repasse pas ici.
"""

import json
import os
import subprocess
import sys
import time

# ── Configuration ────────────────────────────────────────────────────────────

ACCOUNTS_FILE = os.getenv("ACCOUNTS_FILE", "accounts.json")
FLY_APP       = os.getenv("FLY_APP", "surveybot-bot")
FLY_REGION    = os.getenv("FLY_REGION", "cdg")
FLY_MEMORY    = os.getenv("FLY_MEMORY", "2048")
BOT_IMAGE     = os.getenv("BOT_IMAGE", "registry.fly.io/surveybot-bot:latest")

# Délai entre chaque lancement pour éviter un burst simultané (en secondes)
LAUNCH_DELAY_SEC = int(os.getenv("LAUNCH_DELAY_SEC", "2"))

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_accounts(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        accounts = json.load(f)
    if not isinstance(accounts, list):
        raise ValueError(f"accounts.json doit être une liste, got {type(accounts)}")
    return accounts


def launch_bot(account: dict) -> bool:
    """
    Lance une machine Fly.io éphémère pour le compte donné.
    Les clés du JSON sont passées directement en --env.
    Retourne True si le lancement a réussi.
    """
    account_id = account.get("ACCOUNT_ID", "unknown")
    machine_name = account_id.lower().replace("_", "-")
    required = ["ACCOUNT_ID", "EMAIL", "PASSWORD", "PROXY_URL", "PROXY_USER", "PROXY_PASS"]
    missing = [k for k in required if not account.get(k)]
    if missing:
        print(f"[SKIP] {account_id} — champs manquants : {missing}", flush=True)
        return False

    cmd = [
        "flyctl", "machine", "run",
        "--app",       FLY_APP,
        "--region",    FLY_REGION,
        "--vm-memory", FLY_MEMORY,
        "--name",      machine_name,
        "--env", f"ACCOUNT_ID={account['ACCOUNT_ID']}",
        "--env", f"EMAIL={account['EMAIL']}",
        "--env", f"PASSWORD={account['PASSWORD']}",
        "--env", f"PROXY_URL={account['PROXY_URL']}",
        "--env", f"PROXY_USER={account['PROXY_USER']}",
        "--env", f"PROXY_PASS={account['PROXY_PASS']}",
        "--rm",      # détruire la machine après exit
        "--detach",  # non-bloquant
        BOT_IMAGE,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            print(f"[OK]   {account_id} — machine lancée", flush=True)
            return True
        else:
            print(f"[ERR]  {account_id} — flyctl exit {result.returncode}: {result.stderr.strip()}", flush=True)
            return False
    except subprocess.TimeoutExpired:
        print(f"[ERR]  {account_id} — timeout flyctl", flush=True)
        return False
    except Exception as e:
        print(f"[ERR]  {account_id} — exception: {e}", flush=True)
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"[scheduler] Lecture de {ACCOUNTS_FILE}", flush=True)

    try:
        accounts = load_accounts(ACCOUNTS_FILE)
    except Exception as e:
        print(f"[FATAL] Impossible de lire {ACCOUNTS_FILE}: {e}", flush=True)
        sys.exit(1)

    print(f"[scheduler] {len(accounts)} compte(s) trouvé(s)", flush=True)

    ok, fail = 0, 0
    for account in accounts:
        success = launch_bot(account)
        if success:
            ok += 1
        else:
            fail += 1
        if LAUNCH_DELAY_SEC > 0:
            time.sleep(LAUNCH_DELAY_SEC)

    print(f"[scheduler] Terminé — {ok} lancé(s), {fail} ignoré(s)/échoué(s)", flush=True)


if __name__ == "__main__":
    main()
