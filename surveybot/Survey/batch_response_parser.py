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


def _split_values(value: str, itype: str = "", max_select: int = 1) -> List[str]:
    """
    Supporte les réponses multi *uniquement quand ça a du sens*.

    Règles (prédictibles):
    - split prioritaire sur "|" (format recommandé)
    - split sur "," UNIQUEMENT pour checkbox quand max_select > 1
      (évite de casser des libellés qui contiennent des virgules, ex: "NI D'ACCORD, NI PAS D'ACCORD")
    """
    if not value:
        return []

    v = value.strip()
    it = (itype or "").strip().lower()

    # split prioritaire sur |
    if "|" in v:
        return [p.strip() for p in v.split("|") if p.strip()]

    # split sur virgule UNIQUEMENT pour checkbox multi
    # (évite de casser des libellés single-select contenant des virgules,
    # ex: "NI D'ACCORD, NI PAS D'ACCORD")
    if it == "checkbox" and (max_select or 1) > 1 and "," in v and len(v) < 120:
        return [p.strip() for p in v.split(",") if p.strip()]

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

        mx = 1
        if qid and constraints:
            mx = int(constraints.get(qid, 1) or 1)

        values = _split_values(value, itype=itype, max_select=mx)

        for v in values:
            v = (v or "").strip()
            if not v and not target_id:
                continue

            if qid and constraints:
                # mx déjà calculé au-dessus
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

def _norm_lc(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()

def _is_system_scope(scope: str | None) -> bool:
    v = _norm_lc(scope)
    return any(x in v for x in ["__viewstate", "__eventvalidation", "__viewstategenerator", "__eventtarget", "__eventargument"])

def sanitize_actions(actions: list) -> list:
    cleaned = []
    for a in actions:
        it = _norm_lc(getattr(a, "itype", None) or a.get("itype"))
        scope = getattr(a, "scope_hint", None) or a.get("scope_hint") or getattr(a, "dom_scope_hint", None) or a.get("dom_scope_hint")

        if scope and _is_system_scope(scope):
            continue

        # si jamais CTA passé en text/whatever
        answer = _norm_lc(getattr(a, "answer", None) or a.get("answer"))
        if it == "text" and any(tok in answer for tok in ["continue", "continuer", "next", "suivant"]):
            continue

        cleaned.append(a)

    return cleaned
