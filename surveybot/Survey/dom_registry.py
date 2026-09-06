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

# Cache de secours PERSISTANT (jamais vidé par clear_registry) : target_id -> {"id","name"}
# pour les singles text/number/textarea. make_target_id() intègre le texte de question
# dans son hash ; sur un DOM sans conteneur sémantique identifiable (pas de classe
# 'question'/'form-group', pas de <label>), la question est résolue via l'heuristique de
# proximité géométrique _find_question_text_near_element (dom_question_extractor.py), qui
# n'est pas garantie stable d'un scan à l'autre de la même page (ex: rescan intra-plan,
# execute_actions_plan). Un rescan peut donc faire dériver le target_id d'un champ dont
# l'id/name DOM n'a pourtant pas changé -> get_target(ancien_target_id) renvoie None après
# rescan alors que le champ existe toujours. Ce cache permet de retrouver l'id/name DOM
# d'origine pour cet ancien target_id. Voir BOT_EVOLUTION_MEMORY.md.
_STABLE_TEXT_FIELD_LOCATOR: Dict[str, Dict[str, str]] = {}


def clear_registry() -> None:
    """À appeler avant un nouveau scan DOM."""
    DOM_REGISTRY.clear()
    # _STABLE_TEXT_FIELD_LOCATOR n'est PAS vidé ici : il doit survivre aux rescans
    # intra-page (cf. commentaire ci-dessus / get_stable_text_field_locator).


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
    # Additif : alimente le cache de secours id/name (cf. _STABLE_TEXT_FIELD_LOCATOR
    # ci-dessus), scopé strictement aux singles text/number/textarea porteurs d'un id
    # ou d'un name DOM. N'affecte aucun autre kind/itype, n'altère jamais DOM_REGISTRY.
    try:
        if (payload.get("kind") or "") == "single" and (payload.get("itype") or "") in ("text", "number", "textarea"):
            _id = (payload.get("id") or "").strip()
            _name = (payload.get("name") or "").strip()
            if _id or _name:
                _STABLE_TEXT_FIELD_LOCATOR[target_id] = {"id": _id, "name": _name}
    except Exception:
        pass


def get_target(target_id: str) -> Optional[Dict[str, Any]]:
    return DOM_REGISTRY.get(target_id)


def get_stable_text_field_locator(target_id: str) -> Optional[Dict[str, str]]:
    """
    Résolution de secours id/name DOM pour un target_id "single" text/number/textarea
    absent de DOM_REGISTRY après un rescan (target_id dérivé, cf. commentaire sur
    _STABLE_TEXT_FIELD_LOCATOR). Ne retourne jamais rien pour un target_id qui n'a
    jamais été enregistré via register_target() comme single text/number/textarea
    (pas de résolution "à l'aveugle").
    """
    return _STABLE_TEXT_FIELD_LOCATOR.get(target_id)
