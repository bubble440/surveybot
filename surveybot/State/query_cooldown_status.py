"""
State/query_cooldown_status.py

CLI utilitaire — lecture de l'état cooldown depuis Postgres.
Appelé par wake_scheduler.ps1 ; réutilise load_state() sans dupliquer la logique Postgres.

Usage:
    python State/query_cooldown_status.py account1 account2 ...

Sortie stdout: JSON array [{account_id, cooldown_until_ts, is_expired}]
Erreurs (connexion, etc.) → {account_id, error, is_expired: false}

Exit 0 dans tous les cas pour que le script appelant parse le JSON et décide.
"""
from __future__ import annotations
import json, os, sys, time

_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here)
if _root not in sys.path:
    sys.path.insert(0, _root)

from State.account_state import load_state, _ts_to_unix


def main() -> None:
    account_ids = sys.argv[1:]
    if not account_ids:
        print("[]")
        return

    now = int(time.time())
    results: list[dict] = []
    for aid in account_ids:
        try:
            st = load_state(aid)
            cooldown_ts = st.get("cooldown_until_ts", "1970-01-01T00:00:00")
            cooldown_unix = _ts_to_unix(cooldown_ts)
            results.append({
                "account_id": aid,
                "cooldown_until_ts": cooldown_ts,
                "is_expired": cooldown_unix < now,
            })
        except Exception as e:
            results.append({
                "account_id": aid,
                "error": str(e),
                "is_expired": False,
            })

    print(json.dumps(results))


if __name__ == "__main__":
    main()
