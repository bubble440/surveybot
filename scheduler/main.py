from ecs_bot_scheduler import scheduler_tick
import os, boto3, sys

RUN_ENV = os.getenv("RUN_ENV", "")


# ============================================================================
# Chargement des comptes
# ============================================================================

def load_accounts(prefix: str | None = None) -> list[str]:
    """Route vers DynamoDB (AWS) ou Firestore (GCP) selon RUN_ENV."""
    if RUN_ENV == "gcp":
        return _load_accounts_from_firestore(prefix)
    return load_accounts_from_dynamodb(prefix)


def load_accounts_from_dynamodb(prefix: str | None = None) -> list[str]:
    """
    Récupère dynamiquement les account_id depuis DynamoDB.
    Optionnellement filtre par prefix (ex: topsurveys_bot_).
    """

    table_name = os.getenv("STATE_TABLE")
    if not table_name:
        raise RuntimeError("STATE_TABLE manquant pour le scheduler")

    dynamodb = boto3.resource("dynamodb")
    table    = dynamodb.Table(table_name)

    accounts: list[str] = []
    scan_kwargs = {"ProjectionExpression": "account_id"}

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


def _load_accounts_from_firestore(prefix: str | None = None) -> list[str]:
    """
    Récupère les account_id depuis Firestore.
    Collection = STATE_TABLE (même variable d'env que DynamoDB).
    Chaque document doit avoir un champ 'account_id'.
    """

    try:
        from google.cloud import firestore
    except ImportError:
        raise RuntimeError(
            "google-cloud-firestore manquant — pip install google-cloud-firestore"
        )

    collection_name = os.getenv("STATE_TABLE")
    if not collection_name:
        raise RuntimeError("STATE_TABLE manquant pour le scheduler")

    gcp_project = os.getenv("GCP_PROJECT")
    db = firestore.Client(project=gcp_project) if gcp_project else firestore.Client()

    accounts: list[str] = []

    for doc in db.collection(collection_name).stream():
        data = doc.to_dict() or {}
        aid = data.get("account_id")
        if not aid:
            continue
        if prefix and not aid.startswith(prefix):
            continue
        accounts.append(aid)

    return sorted(accounts)


# ============================================================================
# Point d'entrée
# ============================================================================

ACCOUNT_PREFIX = os.getenv("ACCOUNT_PREFIX", "topsurveys_bot_")

ACCOUNTS = load_accounts(prefix=ACCOUNT_PREFIX)

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
