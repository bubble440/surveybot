import time, os, socket
from State.account_state import load_state, update_state
from ecs import is_task_running, start_task

def scheduler_tick(account_id):
    state = load_state(account_id)
    # 🔐 Verrou logique anti-conflit
    now = time.time()

    if state.get("lock_until_ts", 0) > now:
        return  # lock actif

    if state.get("status") != "idle":
        return

    # 1) Si task déjà active → rien à faire
    if is_task_running(account_id):
        return

    # 2) Cooldown pas terminé → on attend
    cooldown = state.get("cooldown_until_ts")
    if cooldown and now < cooldown:
        return

    # 3) Banni → on ne relance jamais
    if state.get("banned"):
        return
    
    SCHEDULER_ID = os.getenv("SCHEDULER_ID", socket.gethostname())

    def _lock(st):
        st["status"] = "starting"
        st["lock_owner"] = SCHEDULER_ID
        st["lock_until_ts"] = time.time() + 120  # 2 min de sécurité

    update_state(account_id, _lock)

    print(
        f"[SCHEDULER] lock pris | account_id={account_id} "
        f"owner={SCHEDULER_ID}"
    )

    # 4) Sinon → on relance la task
    start_task(account_id)
