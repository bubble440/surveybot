# Survey/batch_response_parser.py
"""
Parse les réponses OpenAI batch.

Format attendu (préféré):
QID //// valeur //// itype //// contexte

Fallback accepté:
valeur //// itype //// contexte
"""

from __future__ import annotations
import re
from typing import Dict, Optional, List
_ALLOWED_ITYPES = {"radio", "checkbox", "dropdown", "text", "textarea", "button", "number"}
_QID_RE = re.compile(r"\bQ\d+\b", re.IGNORECASE)


def _split_values(value: str) -> List[str]:
    """
    Supporte les réponses multi:
    - "A | B | C"
    - "A, B, C"
    """
    if not value:
        return []
    v = value.strip()

    # split prioritaire sur |
    if "|" in v:
        parts = [p.strip() for p in v.split("|") if p.strip()]
        return parts

    # split léger sur virgule si ça ressemble à une liste courte
    if "," in v and len(v) < 120:
        parts = [p.strip() for p in v.split(",") if p.strip()]
        return parts

    return [v]


def parse_batch_response(raw: str, constraints: Optional[Dict[str, int]] = None) -> list[dict]:
    """
    Transforme la réponse OpenAI en liste d'instructions exécutables.

    constraints: dict { "Q1": 1, "Q2": 3, ... } pour appliquer max_select.
    Si constraints est fourni => mode BATCH STRICT:
      - ignore toute ligne sans QID valide (Qn)
      - ignore les QID inconnus (non présents dans constraints)
    """
    print("[batch_response_parser] raw response", raw)

    actions: list[dict] = []
    if not raw:
        return actions

    lines = [l.strip() for l in raw.splitlines() if l.strip()]

    kept_count: Dict[str, int] = {}
    kept_values: Dict[str, set] = {}

    for line in lines:
        parts = [p.strip() for p in re.split(r"/{4,}", line) if p.strip()]
        if not parts:
            continue

        qid: Optional[str] = None
        target_id: Optional[str] = None
        value = ""
        itype = ""
        context = ""

        if len(parts) >= 5:
            # QID //// target_id //// valeur //// itype //// contexte
            qid = parts[0].strip()
            target_id = parts[1].strip()
            value = parts[2].strip()
            itype = parts[3].strip().lower()
            context = parts[4].strip()
        elif len(parts) == 4:
            # compat: QID //// valeur //// itype //// contexte
            qid = parts[0].strip()
            value = parts[1].strip()
            itype = parts[2].strip().lower()
            context = parts[3].strip()
        elif len(parts) == 3:
            # compat: valeur //// itype //// contexte
            value = parts[0].strip()
            itype = parts[1].strip().lower()
            context = parts[2].strip()
        else:
            continue

        # normalise QID (ex: "Q1)" -> "Q1")
        if qid:
            m = _QID_RE.search(qid)
            qid = (m.group(0) if m else qid).upper()

        # mode batch strict
        if constraints is not None:
            if not qid:
                continue
            if qid not in constraints:
                continue

        # filtre itype (évite hallucinations)
        if itype and itype not in _ALLOWED_ITYPES:
            continue

        values = _split_values(value)

        for v in values:
            v = (v or "").strip()
            if not v and not target_id:
                continue

            if qid and constraints:
                mx = int(constraints.get(qid, 1) or 1)
                kept_count.setdefault(qid, 0)
                kept_values.setdefault(qid, set())

                keyv = v.strip().lower()
                if keyv in kept_values[qid]:
                    continue
                if kept_count[qid] >= mx:
                    continue

                kept_values[qid].add(keyv)
                kept_count[qid] += 1

            actions.append(
                {
                    "qid": qid,
                    "target_id": target_id,
                    "value": v,
                    "itype": itype,
                    "context": context,
                    "raw": line,
                }
            )

    return actions
