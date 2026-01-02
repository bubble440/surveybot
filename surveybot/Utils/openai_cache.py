import time
import hashlib
import os
import boto3

TABLE_NAME = os.getenv("OPENAI_CACHE_TABLE", "openai_cache")
CACHE_TTL_SEC = 7 * 24 * 3600  # 7 jours

_dynamo = None

def _get_table():
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource("dynamodb").Table(TABLE_NAME)
    return _dynamo


def make_cache_key(question: str, options: list[str]) -> str:
    base = question.lower().strip()
    opts = "|".join(sorted(o.lower().strip() for o in options))
    raw = f"{base}|{opts}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_cached_answer(cache_key: str) -> str | None:
    try:
        resp = _get_table().get_item(Key={"cache_key": cache_key})
        item = resp.get("Item")
        if not item:
            return None

        # hit++
        _get_table().update_item(
            Key={"cache_key": cache_key},
            UpdateExpression="ADD hits :h",
            ExpressionAttributeValues={":h": 1},
        )

        return item.get("answer")
    except Exception:
        return None


def store_answer(cache_key: str, answer: str, model: str):
    now = int(time.time())
    try:
        _get_table().put_item(
            Item={
                "cache_key": cache_key,
                "answer": answer,
                "model": model,
                "created_ts": now,
                "hits": 0,
                "ttl": now + CACHE_TTL_SEC,
            }
        )
    except Exception:
        pass
