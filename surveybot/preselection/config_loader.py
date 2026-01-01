#def load_config():
#    base_dir = os.path.dirname(os.path.dirname(__file__))
#    config_path = os.path.join(base_dir, "config")
#    with open(config_path, encoding="utf-8") as f:
#        return json.load(f)

from __future__ import annotations
import json, os
from pathlib import Path
from .secret_loader import load_remote_secrets

def _load_local_config() -> dict:
    base_dir = Path(__file__).resolve().parent.parent
    candidates = [
        base_dir / "Utils" / "config",
    ]

    for p in candidates:
        try:
            if p.is_file():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def load_config() -> dict:
    """
    Ordre de priorité (du plus fort au plus faible):
      1) Overrides ENV unitaires & TOPSURVEYS_SECRET_JSON & Secrets Manager (via secret_loader)
      2) Fichier local config.json (dev)
    """
    local = _load_local_config()
    remote = load_remote_secrets()

    # on part du local puis on écrase par le remote (prioritaire)
    merged = dict(local)
    merged.update(remote)

    # Normalisation légère des clés attendues ailleurs dans le code
    # (main.py lit: Email, Password, openai_api_key, payout_name, payout_revolut_tag,
    #  telegram_bot_token, telegram_chat_id)
    return merged
