# ------------------------------------------------------------
# DOM Metrics
#
# Objectif :
# - Suivre l’usage OpenAI vs Local
# - Agréger par itype DOM
# - Lightweight (local + prod)
# ------------------------------------------------------------

from collections import defaultdict
import logging
import os, time
from datetime import date

log = logging.getLogger("dom_metrics")

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

        add_fields = ["total_pages :one", "by_itype.#t :one"]
        if openai:
            add_fields.append("openai_pages :one")
        else:
            add_fields.append("local_pages :one")

        expr_vals = {
            ":one": 1,
            ":now": now,
        }
        expr_names = {
            "#t": itype,
        }

        table.update_item(
            Key={"account_id": account_id, "day": day},
            UpdateExpression="SET updated_ts = :now ADD " + ", ".join(add_fields),
            ExpressionAttributeValues=expr_vals,
            ExpressionAttributeNames=expr_names,
        )

    except Exception as e:
        # ⚠️ jamais bloquant
        log.warning(f"[DOM_METRICS] export DynamoDB échoué. account={account_id} err={e}")

def export_dom_rescans(rescans: int):
    """
    Export incrémental d'un seul compteur :
      - dom_rescans_total (ADD)
    Best-effort, jamais bloquant.
    """
    try:
        rescans = int(rescans or 0)
    except Exception:
        return

    # Pas d'écriture si 0 (économie de coût)
    if rescans <= 0:
        return

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

        table.update_item(
            Key={"account_id": account_id, "day": day},
            UpdateExpression="SET updated_ts = :now ADD dom_rescans_total :n",
            ExpressionAttributeValues={":now": now, ":n": rescans},
        )

    except Exception as e:
        # ⚠️ jamais bloquant
        log.warning(f"[DOM_METRICS] export rescans DynamoDB échoué. account={account_id} err={e}")
