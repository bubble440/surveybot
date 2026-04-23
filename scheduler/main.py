import os
import time

from account_loader import accounts_by_proxy, load_account
from fly import start_task
from state_reader import is_in_cooldown, load_states_batch

RUN_ENV       = os.getenv("RUN_ENV", "prod").lower()
LOOP_INTERVAL = int(os.getenv("LOOP_INTERVAL_SEC", "120"))


def _pick_candidate(proxy_id: str, candidate_ids: list[str], states: dict[str, dict]) -> str | None:
    """
    Retourne le premier account_id disponible (non en cooldown) dans le groupe proxy.
    Retourne None si tous les comptes sont en cooldown ou bannis.
    """
    for aid in candidate_ids:
        if not is_in_cooldown(states.get(aid, {})):
            return aid
    return None


def main() -> None:
    print(f"[SCHEDULER] Démarrage — RUN_ENV={RUN_ENV} interval={LOOP_INTERVAL}s")

    while True:
        proxy_groups = accounts_by_proxy()  # {proxy_id: [account_id, ...]}
        all_ids = [aid for ids in proxy_groups.values() for aid in ids]

        print(f"[SCHEDULER] {len(all_ids)} compte(s) / {len(proxy_groups)} proxy — tick")

        try:
            states = load_states_batch(all_ids)
        except Exception:
            print("[SCHEDULER] Postgres inaccessible — tick annulé (fail-closed)")
            time.sleep(LOOP_INTERVAL)
            continue

        launched = 0
        for proxy_id, candidate_ids in proxy_groups.items():
            chosen = _pick_candidate(proxy_id, candidate_ids, states)

            if chosen is None:
                reasons = [
                    states.get(aid, {}).get("last_stop_reason", "") or "cooldown"
                    for aid in candidate_ids
                ]
                print(
                    f"[SCHEDULER] proxy={proxy_id} — {len(candidate_ids)} compte(s) en cooldown "
                    f"({', '.join(reasons)}), skip"
                )
                continue

            try:
                account = load_account(chosen)
                start_task(chosen, account)
                launched += 1
            except Exception as e:
                print(f"[SCHEDULER] Erreur proxy={proxy_id} account={chosen}: {e}")

        print(f"[SCHEDULER] Tick terminé — {launched} machine(s) lancée(s) — attente {LOOP_INTERVAL}s")
        time.sleep(LOOP_INTERVAL)


if __name__ == "__main__":
    main()
