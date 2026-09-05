from __future__ import annotations

"""Validation passive des question_blocks extraits.

Phase 1A : observabilité uniquement.
- Ne modifie jamais les blocs.
- Ne déclenche aucun retry.
- Ne bloque jamais l'exécution du survey.
- Ne signale que des incohérences structurelles à forte confiance.
"""

from typing import Any

from Survey.dom_registry import get_target


_ALLOWED_CHOICE_TYPES = {"radio", "checkbox", "dropdown", "matrix"}


def _norm(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _norm_lc(value: Any) -> str:
    return _norm(value).lower()


def validate_question_blocks(question_blocks: list[dict] | None) -> dict:
    """Retourne un rapport JSON-sérialisable sans effet de bord."""
    blocks = question_blocks or []
    issues: list[dict] = []

    for idx, block in enumerate(blocks):
        if not isinstance(block, dict):
            issues.append({
                "failure_type": "invalid_block_shape",
                "block_index": idx,
                "details": "question block is not a dict",
            })
            continue

        itype = _norm_lc(block.get("itype"))
        target_id = _norm(block.get("target_id"))
        question = _norm(block.get("question"))
        options = block.get("options") if isinstance(block.get("options"), list) else []
        normalized_options = [_norm_lc(opt) for opt in options if _norm(opt)]

        if not target_id:
            issues.append({
                "failure_type": "missing_target_id",
                "block_index": idx,
                "itype": itype,
                "question": question,
            })
        elif get_target(target_id) is None:
            issues.append({
                "failure_type": "registry_target_missing",
                "block_index": idx,
                "target_id": target_id,
                "itype": itype,
                "question": question,
            })

        # Pour radio/checkbox/dropdown, une liste d'options vide est une anomalie
        # structurelle forte. Matrix est exclu ici car certains extracteurs portent
        # lignes/colonnes dans context plutôt que dans options.
        if itype in {"radio", "checkbox", "dropdown"} and not normalized_options:
            issues.append({
                "failure_type": "choice_without_options",
                "block_index": idx,
                "target_id": target_id,
                "itype": itype,
                "question": question,
            })

        if normalized_options and len(normalized_options) != len(set(normalized_options)):
            issues.append({
                "failure_type": "duplicate_options",
                "block_index": idx,
                "target_id": target_id,
                "itype": itype,
                "question": question,
            })

        try:
            min_select = int(block.get("min_select", 0) or 0)
        except Exception:
            min_select = 0
        try:
            max_select = int(block.get("max_select", 0) or 0)
        except Exception:
            max_select = 0

        if min_select > 0 and max_select > 0 and min_select > max_select:
            issues.append({
                "failure_type": "invalid_selection_limits",
                "block_index": idx,
                "target_id": target_id,
                "itype": itype,
                "min_select": min_select,
                "max_select": max_select,
            })

        if itype == "checkbox" and max_select > 0 and normalized_options and max_select > len(normalized_options):
            issues.append({
                "failure_type": "max_select_exceeds_options",
                "block_index": idx,
                "target_id": target_id,
                "max_select": max_select,
                "options_count": len(normalized_options),
            })

    return {
        "stage": "extraction",
        "ok": not issues,
        "blocks_count": len(blocks),
        "issues": issues,
    }
