import os, sys, time
from account_loader import list_account_ids, load_account
from fly import start_task

RUN_ENV       = os.getenv("RUN_ENV", "prod").lower()
LOOP_INTERVAL = int(os.getenv("LOOP_INTERVAL_SEC", "120"))

def main() -> None:
    print(f"[SCHEDULER] Démarrage — RUN_ENV={RUN_ENV} interval={LOOP_INTERVAL}s")

    while True:
        account_ids = list_account_ids()
        print(f"[SCHEDULER] {len(account_ids)} compte(s) — lancement en cours")

        for account_id in account_ids:
            try:
                account = load_account(account_id)
                start_task(account_id, account)
            except Exception as e:
                print(f"[SCHEDULER] Erreur {account_id}: {e}")

        print(f"[SCHEDULER] Tick terminé — attente {LOOP_INTERVAL}s")
        time.sleep(LOOP_INTERVAL)

if __name__ == "__main__":
    main()