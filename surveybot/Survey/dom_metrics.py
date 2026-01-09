# ------------------------------------------------------------
# DOM Metrics
#
# Objectif :
# - Suivre l’usage OpenAI vs Local
# - Agréger par itype DOM
# - Lightweight (local + prod)
# ------------------------------------------------------------

from collections import defaultdict
import os, time
from datetime import date

RUN_ENV = os.getenv("RUN_ENV", "local")
IS_LOCAL = RUN_ENV == "local"

DOM_METRICS_TABLE = os.getenv("DOM_METRICS_TABLE", "surveybot_dom_metrics")
AWS_REGION = os.getenv("AWS_REGION", "")

_DOM_METRICS = {
    "total_pages": 0,
    "openai_pages": 0,
    "local_pages": 0,
    "by_itype": defaultdict(int),
}


def record_dom_classification(*, itype: str | None, openai: bool):
    itype = itype or "unknown"

    _DOM_METRICS["total_pages"] += 1
    if openai:
        _DOM_METRICS["openai_pages"] += 1
    else:
        _DOM_METRICS["local_pages"] += 1

    _DOM_METRICS["by_itype"][itype] += 1

    # 🔁 Export incrémental (non bloquant)
    _export_to_dynamodb(itype=itype, openai=openai)


def snapshot() -> dict:
    total = max(_DOM_METRICS["total_pages"], 1)
    return {
        "total_pages": _DOM_METRICS["total_pages"],
        "openai_pages": _DOM_METRICS["openai_pages"],
        "local_pages": _DOM_METRICS["local_pages"],
        "openai_ratio": round(_DOM_METRICS["openai_pages"] / total, 3),
        "by_itype": dict(_DOM_METRICS["by_itype"]),
    }


def log_snapshot(prefix="[DOM_METRICS]"):
    snap = snapshot()
    print(
        f"{prefix} total={snap['total_pages']} "
        f"openai={snap['openai_pages']} "
        f"local={snap['local_pages']} "
        f"ratio_openai={snap['openai_ratio']}"
    )
    print(f"{prefix} by_itype={snap['by_itype']}")

def _export_to_dynamodb(itype: str, openai: bool):
    """
    Export incrémental, best-effort.
    Jamais bloquant pour le bot.
    """
    if IS_LOCAL:
        return

    account_id = os.getenv("ACCOUNT_ID")
    if not account_id:
        return

    try:
        import boto3
        resource = (
            boto3.resource("dynamodb", region_name=AWS_REGION)
            if AWS_REGION else boto3.resource("dynamodb")
        )
        table = resource.Table(DOM_METRICS_TABLE)

        day = date.today().isoformat()
        now = int(time.time())

        update_expr = [
            "SET updated_ts = :now",
            "ADD total_pages :one",
            "ADD by_itype.#t :one",
        ]
        expr_vals = {
            ":one": 1,
            ":now": now,
        }
        expr_names = {
            "#t": itype,
        }

        if openai:
            update_expr.append("ADD openai_pages :one")
        else:
            update_expr.append("ADD local_pages :one")

        table.update_item(
            Key={"account_id": account_id, "day": day},
            UpdateExpression=" ".join(update_expr),
            ExpressionAttributeValues=expr_vals,
            ExpressionAttributeNames=expr_names,
        )

    except Exception as e:
        # ⚠️ jamais bloquant
        print(f"[DOM_METRICS][WARN] export DynamoDB échoué: {e}")
