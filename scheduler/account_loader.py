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
    Parse ACCOUNTS_JSON une seule fois. Supporte deux formats :

    Format groupé (recommandé) — un objet par proxy, emails listés dedans :
    [
      {
        "PROXY_ID": "1.2.3.4",
        "PROXY_URL": "1.2.3.4:12323",
        "PROXY_USER": "user",
        "PROXY_PASS": "pass",
        "ACCOUNTS": [
          { "ACCOUNT_ID": "bot_001", "EMAIL": "a@example.com" },
          { "ACCOUNT_ID": "bot_002", "EMAIL": "b@example.com" }
        ]
      }
    ]

    Format plat legacy (rétrocompatible) — un objet par compte :
    [
      { "ACCOUNT_ID": "bot_001", "EMAIL": "a@example.com", "PROXY_URL": "...", ... },
      ...
    ]

    Dans les deux cas, _cache contient des dicts plats {ACCOUNT_ID -> credentials complets}.
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
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"[ACCOUNT_LOADER] ACCOUNTS_JSON invalide (JSON malformé): {e}")

    if not isinstance(data, list):
        raise RuntimeError("[ACCOUNT_LOADER] ACCOUNTS_JSON doit être une liste JSON")

    # Détection du format : groupé si le premier élément contient "ACCOUNTS"
    if data and "ACCOUNTS" in data[0]:
        # Format groupé : dépliage des groupes proxy en dicts plats
        accounts_list = []
        for group in data:
            proxy_fields = {k: v for k, v in group.items() if k != "ACCOUNTS"}
            for acc in group.get("ACCOUNTS", []):
                # Les champs du compte ont priorité sur les champs proxy (override possible)
                accounts_list.append({**proxy_fields, **acc})
    else:
        # Format plat legacy
        accounts_list = data

    _cache = {}
    for acc in accounts_list:
        account_id = acc.get("ACCOUNT_ID", "").strip()
        if not account_id:
            raise RuntimeError(f"[ACCOUNT_LOADER] Compte sans ACCOUNT_ID: {acc}")
        if account_id in _cache:
            raise RuntimeError(f"[ACCOUNT_LOADER] ACCOUNT_ID dupliqué: {account_id}")
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