from ecs import start_task
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
    Source de vérité = GCP Secret Manager (liste des secrets avec le préfixe donné).
    Pour chaque compte découvert, auto-crée le document Firestore s'il est absent.
    Collection Firestore = STATE_TABLE.
    """

    try:
        from google.cloud import secretmanager
    except ImportError:
        raise RuntimeError(
            "google-cloud-secret-manager manquant — "
            "pip install google-cloud-secret-manager"
        )

    try:
        from google.cloud import firestore
    except ImportError:
        raise RuntimeError(
            "google-cloud-firestore manquant — pip install google-cloud-firestore"
        )

    gcp_project = os.getenv("GCP_PROJECT")
    if not gcp_project:
        raise RuntimeError("GCP_PROJECT manquant pour le scheduler GCP")

    collection_name = os.getenv("STATE_TABLE")
    if not collection_name:
        raise RuntimeError("STATE_TABLE manquant pour le scheduler")

    # 1. Lister les secrets depuis Secret Manager
    sm_client = secretmanager.SecretManagerServiceClient()
    parent = f"projects/{gcp_project}"

    accounts: list[str] = []
    for secret in sm_client.list_secrets(request={"parent": parent}):
        # secret.name = "projects/{project}/secrets/{secret_id}"
        secret_id = secret.name.split("/")[-1]
        if prefix and not secret_id.startswith(prefix):
            continue
        accounts.append(secret_id)

    accounts = sorted(accounts)

    # 2. Auto-créer les documents Firestore manquants
    if accounts:
        db = firestore.Client(project=gcp_project)
        col = db.collection(collection_name)

        _DEFAULT_TS = "1970-01-01T00:00:00"
        for account_id in accounts:
            doc_ref = col.document(account_id)
            if not doc_ref.get().exists:
                doc_ref.set({
                    "account_id":        account_id,
                    "status":            "idle",
                    "banned":            False,
                    "version":           0,
                    "lock_owner":        "",
                    "lock_until_ts":     _DEFAULT_TS,
                    "cooldown_until_ts": _DEFAULT_TS,
                })
                print(f"[SCHEDULER] Firestore doc auto-créé : {account_id}")

    return accounts


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
            start_task(account_id)
        except Exception as e:
            print(f"⚠️ Scheduler error pour {account_id} : {e}")

    print("✅ Scheduler terminé → exit")
    sys.exit(0)

if __name__ == "__main__":
    main()
