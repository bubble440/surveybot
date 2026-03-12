# Survey/dom_registry.py
"""
DOM_REGISTRY : registre en mémoire des cibles DOM pour la page courante.

But:
- Fournir un identifiant stable-ish par "question bloc" (target_id)
- Stocker des locators (XPath) pour retrouver l'élément / les options
- Permettre à execute_action() d'appliquer une instruction au bon input

Pensé pour:
- local + prod
- pages dynamiques (re-scan -> clear + rebuild)
"""

from __future__ import annotations
from typing import Dict, Any, Optional
import hashlib


DOM_REGISTRY: Dict[str, Dict[str, Any]] = {}


def clear_registry() -> None:
    """À appeler avant un nouveau scan DOM."""
    DOM_REGISTRY.clear()


def _stable_hash(s: str) -> str:
    """Hash court pour éviter des ids trop longs."""
    h = hashlib.sha1((s or "").encode("utf-8")).hexdigest()
    return h[:12]


def make_target_id(kind: str, group_key: str, question: str) -> str:
    """
    Construit un id stable-ish:
    - group_key vient du DOM (name/aria-labelledby/container id)
    - question stabilise contre collisions
    """
    base = f"{kind}|{group_key}|{question}"
    return f"{kind}_{_stable_hash(base)}"


def register_target(target_id: str, payload: Dict[str, Any]) -> None:
    DOM_REGISTRY[target_id] = payload


def get_target(target_id: str) -> Optional[Dict[str, Any]]:
    return DOM_REGISTRY.get(target_id)
