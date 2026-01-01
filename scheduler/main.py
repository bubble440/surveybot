import time
import os
from ecs_bot_scheduler import scheduler_tick

# 🔐 Liste des comptes gérés par ce scheduler
# (plus tard : chargement dynamique depuis S3 / Dynamo / Secrets)
ACCOUNTS = [
    "topsurveys_wilfried_01",
    # "topsurveys_wilfried_02",
    # ...
]

# ⏱️ Intervalle entre deux ticks (secondes)
TICK_INTERVAL = int(os.getenv("SCHEDULER_TICK_SEC", "30"))

def main():
    print("🧠 Scheduler démarré")
    print(f"📋 Comptes gérés : {ACCOUNTS}")
    print(f"⏱️ Intervalle : {TICK_INTERVAL}s")

    while True:
        for account_id in ACCOUNTS:
            try:
                scheduler_tick(account_id)
            except Exception as e:
                # Le scheduler ne doit JAMAIS mourir
                print(f"⚠️ Scheduler error pour {account_id} : {e}")

        time.sleep(TICK_INTERVAL)


if __name__ == "__main__":
    main()
