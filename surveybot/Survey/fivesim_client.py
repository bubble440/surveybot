from __future__ import annotations

import json
import os
import time
import urllib.request

from Survey.log_utils import log_debug, log_info

_TAG = "FIVESIM"


def _api_key() -> str:
    key = (os.getenv("FIVESIM_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("[FIVESIM] FIVESIM_API_KEY non défini")
    return key


def _get(url: str, api_key: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def _normalize_phone(raw: str) -> str:
    """Retire '+' et le préfixe pays '33' pour retourner le numéro national seul."""
    digits = raw.lstrip("+")
    if digits.startswith("33") and len(digits) >= 11:
        digits = digits[2:]
    return digits


def buy_number(account_id: str) -> tuple[str, str]:
    """Achète un nouveau numéro virtuel français et persiste phone+order_id dans account_state."""
    api_key = _api_key()
    data = _get(
        "https://5sim.net/v1/user/buy/activation/france/any/other",
        api_key,
    )
    phone = _normalize_phone(str(data["phone"]))
    order_id = str(data["id"])
    log_info(f"[{_TAG}]", f"Numéro acheté: {phone}, order_id={order_id}")
    _persist(account_id, phone, order_id)
    return phone, order_id


def reuse_number(account_id: str, phone: str) -> tuple[str, str]:
    """
    Tente de réutiliser un numéro existant.
    Si le reuse échoue (numéro expiré ou impossible) : achète un nouveau numéro.
    Retourne (phone, order_id).
    """
    api_key = _api_key()
    try:
        data = _get(f"https://5sim.net/v1/user/reuse/other/{phone}", api_key)
        status = (data.get("status") or "").lower()
        if "reuse not possible" in status or "reuse expired" in status:
            raise ValueError(f"reuse refusé: {status}")
        order_id = str(data["id"])
        phone = _normalize_phone(phone)
        log_info(f"[{_TAG}]", f"Numéro réutilisé: {phone}, order_id={order_id}")
        _persist(account_id, phone, order_id)
        return phone, order_id
    except Exception as e:
        log_info(f"[{_TAG}]", f"Reuse échoué ({e}) → achat nouveau numéro")
        return buy_number(account_id)


def finish_order(order_id: str) -> None:
    """Finalise une commande 5sim. No-op si order_id vide. Ne lève jamais d'exception."""
    if not order_id:
        return
    try:
        api_key = _api_key()
        _get(f"https://5sim.net/v1/user/finish/{order_id}", api_key)
        log_debug(f"[{_TAG}]", f"Commande {order_id} finalisée")
    except Exception as e:
        log_debug(f"[{_TAG}]", f"finish_order({order_id}) ignoré: {e}")


def poll_sms_code(
    order_id: str, max_attempts: int = 12, interval_sec: int = 5
) -> str | None:
    """
    Polling GET /v1/user/check/{order_id} jusqu'à status=="RECEIVED".
    Retourne sms[0]["code"] ou None après budget épuisé.
    """
    api_key = _api_key()
    url = f"https://5sim.net/v1/user/check/{order_id}"
    for attempt in range(max_attempts):
        try:
            data = _get(url, api_key)
            log_debug(f"[{_TAG}]", f"poll #{attempt + 1}: status={data.get('status')}")
            if data.get("status") == "RECEIVED":
                sms_list = data.get("sms") or []
                if sms_list:
                    code = (sms_list[0].get("code") or "").strip()
                    return code or None
        except Exception as e:
            log_debug(f"[{_TAG}]", f"poll #{attempt + 1} erreur: {e}")
        time.sleep(interval_sec)
    return None


def _persist(account_id: str, phone: str, order_id: str) -> None:
    """Persiste phone et order_id dans account_state (best-effort)."""
    try:
        from State.account_state import update_state

        def _upd(s: dict) -> dict:
            s["fivesim_phone"] = phone
            s["fivesim_order_id"] = order_id
            return s

        update_state(account_id, _upd)
    except Exception as e:
        log_info(f"[{_TAG}]", f"Persistance account_state échouée: {e}")
