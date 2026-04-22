# scheduler/account_loader.py
#
# Version Fly.io : les credentials de chaque compte sont stockés dans
# ACCOUNTS_JSON (secret Fly.io injecté comme env var).
# Plus de dépendance à AWS Secrets Manager ou GCP Secret Manager.

import json
import os

_cache: dict | None = None


def _load_all_accounts() -> dict:
    """
    Parse ACCOUNTS_JSON une seule fois.
    Format attendu : liste de dicts, chacun avec au minimum ACCOUNT_ID.

    Exemple :
    [
      {
        "ACCOUNT_ID": "bot_001",
        "EMAIL": "bot001@example.com",
        "PROXY_URL": "http://...",
        "PROXY_USER": "user",
        "PROXY_PASS": "pass",
      },
      ...
    ]
    """
    global _cache
    if _cache is not None:
        return _cache

    raw = os.getenv("ACCOUNTS_JSON", "").strip()
    print(f"[ACCOUNT_LOADER] ACCOUNTS_JSON length={len(raw)} preview={raw[:800]!r}", flush=True)
    if not raw:
        raise RuntimeError(
            "[ACCOUNT_LOADER] ACCOUNTS_JSON manquant. "
            "Setter via: fly secrets set ACCOUNTS_JSON='[{...}]' --app surveybot-scheduler"
        )

    try:
        accounts_list = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"[ACCOUNT_LOADER] ACCOUNTS_JSON invalide (JSON malformé): {e}")

    if not isinstance(accounts_list, list):
        raise RuntimeError("[ACCOUNT_LOADER] ACCOUNTS_JSON doit être une liste JSON")

    _cache = {}
    for acc in accounts_list:
        account_id = acc.get("ACCOUNT_ID", "").strip()
        if not account_id:
            raise RuntimeError(f"[ACCOUNT_LOADER] Compte sans ACCOUNT_ID: {acc}")
        _cache[account_id] = acc

    return _cache


def list_account_ids() -> list[str]:
    """Retourne la liste triée des account_id disponibles."""
    return sorted(_load_all_accounts().keys())


def accounts_by_proxy() -> dict[str, list[str]]:
    """
    Groupe les comptes par PROXY_ID.
    Si PROXY_ID est absent, le compte forme son propre groupe (comportement inchangé).
    Retourne {proxy_id: [account_id, ...]} — groupes et IDs triés de façon déterministe.
    """
    groups: dict[str, list[str]] = {}
    for account_id, acc in _load_all_accounts().items():
        proxy_id = (acc.get("PROXY_ID") or "").strip() or account_id
        groups.setdefault(proxy_id, []).append(account_id)
    return {pid: sorted(aids) for pid, aids in sorted(groups.items())}


def load_account(account_id: str) -> dict:
    """
    Retourne le dict de credentials pour un account_id donné.
    Lève RuntimeError si introuvable ou si les champs critiques manquent.
    """
    accounts = _load_all_accounts()

    if account_id not in accounts:
        raise RuntimeError(f"[ACCOUNT_LOADER] account_id inconnu: {account_id}")

    account = accounts[account_id]

    # Validation des champs critiques
    missing = [f for f in ("EMAIL", "PROXY_URL") if not account.get(f)]
    if missing:
        raise RuntimeError(
            f"[ACCOUNT_LOADER] Champs manquants pour {account_id}: {missing}"
        )

    return account