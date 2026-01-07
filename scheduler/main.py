from ecs_bot_scheduler import scheduler_tick
import os, boto3, sys

def load_accounts_from_dynamodb(prefix: str | None = None) -> list[str]:
    """
    Récupère dynamiquement les account_id depuis DynamoDB.
    Optionnellement filtre par prefix (ex: topsurveys_bot_).
    """

    table_name = os.getenv("STATE_TABLE")
    if not table_name:
        raise RuntimeError("STATE_TABLE manquant pour le scheduler")

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)

    accounts: list[str] = []
    scan_kwargs = {
        "ProjectionExpression": "account_id"
    }

    while True:
        resp = table.scan(**scan_kwargs)

        for item in resp.get("Items", []):
            aid = item.get("account_id")
            if not aid:
                continue
            if prefix and not aid.startswith(prefix):
                continue
            accounts.append(aid)

        if "LastEvaluatedKey" not in resp:
            break

        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    return sorted(accounts)
    
ACCOUNT_PREFIX = os.getenv("ACCOUNT_PREFIX", "topsurveys_bot_")

ACCOUNTS = load_accounts_from_dynamodb(prefix=ACCOUNT_PREFIX)

def main():
    print("🧠 Scheduler one-shot démarré")

    print(f"📋 Comptes détectés : {ACCOUNTS}")

    for account_id in ACCOUNTS:
        try:
            scheduler_tick(account_id)
        except Exception as e:
            print(f"⚠️ Scheduler error pour {account_id} : {e}")

    print("✅ Scheduler terminé → exit")
    sys.exit(0)

if __name__ == "__main__":
    main()
