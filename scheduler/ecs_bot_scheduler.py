import time, os, socket
from account_state import load_state, update_state
from ecs import is_task_running, start_task
from account_state import try_acquire_account_lock

def scheduler_tick(account_id):
    state = load_state(account_id)
    print(f"[SCHEDULER] Tick pour {account_id} | état={state}")
    # 🔐 Verrou logique anti-conflit
    now = time.time()

    # 🧹 Nettoyage état fantôme (task arrêtée manuellement / crash)
    if not is_task_running(account_id):
        if state.get("status") not in ("idle", None, "starting"):
            print(f"[SCHEDULER] Reset état fantôme pour {account_id}")

            update_state(account_id, lambda st: st.update({
                "status": "idle",
                "lock_owner": None,
                "lock_until_ts": 0,
                "proxy_lock_owner": None,
                "proxy_lock_until_ts": 0,
                "last_stop_reason": "forced_reset",
            }))
            return
    
    if state.get("lock_until_ts", 0) > now:
        print(f"[SCHEDULER] Lock actif pour {account_id}")
        return  # lock actif

    if state.get("status") != "idle":
        print(f"[SCHEDULER] Status non idle pour {account_id}")
        return

    # 1) Si task déjà active → rien à faire
    if is_task_running(account_id):
        print(f"[SCHEDULER] Task déjà active pour {account_id}")
        return

    # 2) Cooldown pas terminé → on attend
    cooldown = state.get("cooldown_until_ts")
    if cooldown and now < cooldown:
        print(f"[SCHEDULER] Cooldown actif pour {account_id}")
        return

    # 3) Banni → on ne relance jamais
    if state.get("banned"):
        print(f"[SCHEDULER] Compte banni, pas de relance pour {account_id}")
        return
    
    SCHEDULER_ID = os.getenv("SCHEDULER_ID", socket.gethostname())

    LOCK_OK = try_acquire_account_lock(
        account_id=account_id,
        owner=SCHEDULER_ID,
        ttl_sec=120,  # juste pour le démarrage
    )

    if not LOCK_OK:
        print(
            f"[SCHEDULER] Lock refusé pour {account_id} "
            f"(déjà pris par une autre task)"
        )
        return

    # 4) Sinon → on relance la task
    try:
        start_task(account_id)
    except Exception as e:
        print(f"[SCHEDULER] ECHEC start_task pour {account_id}: {e}")

        update_state(account_id, lambda st: st.update({
            "status": "idle",
            "lock_owner": None,
            "lock_until_ts": 0,
            "last_stop_reason": "start_task_failed",
        }))
