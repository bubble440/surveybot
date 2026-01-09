# Survey/batch_response_parser.py
"""
Parse les réponses OpenAI batch :
valeur //// itype //// contexte
"""

from __future__ import annotations
import re

def parse_batch_response(raw: str) -> list[dict]:
    """
    Transforme la réponse OpenAI en liste d'instructions exécutables.
    """
    print("[batch_response_parser] raw response", raw)
    
    actions = []

    if not raw:
        return actions

    lines = [l.strip() for l in raw.splitlines() if l.strip()]

    for line in lines:
        parts = re.split(r"/{4,}", line)
        if len(parts) < 3:
            continue

        value = parts[0].strip()
        itype = parts[1].strip().lower()
        context = parts[2].strip()

        actions.append({
            "value": value,
            "itype": itype,
            "context": context,
            "raw": line,
        })

    return actions
