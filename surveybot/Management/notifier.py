# notifier.py
# Envoi Telegram (gratuit). Si 'requests' n'est pas dispo, on tombe sur urllib.
from __future__ import annotations
import json
try:
    import requests  # type: ignore
except Exception:
    requests = None
from urllib.request import Request, urlopen

def send_telegram(message: str, bot_token: str, chat_id: str) -> bool:
    """
    Envoie 'message' via le bot Telegram.
    - bot_token: @BotFather (ex: 123456789:ABC...)
    - chat_id: identifiant du chat (ou @username si public)
    """
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}
    data = json.dumps(payload).encode("utf-8")
    try:
        if requests:
            r = requests.post(url, json=payload, timeout=10)
            return r.ok
        else:
            req = Request(url, data=data, headers={"Content-Type":"application/json"})
            with urlopen(req, timeout=10) as _:
                return True
    except Exception:
        return False
