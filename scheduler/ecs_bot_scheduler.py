import time
from State.account_state import load_state
from ecs import is_task_running, start_task

def scheduler_tick(account_id):
    state = load_state(account_id)
    now = time.time()

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

    # 4) Sinon → on relance la task
    start_task(account_id)
