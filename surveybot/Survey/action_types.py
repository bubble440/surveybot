# Survey/action_types.py
# ------------------------------------------------------------
# Action (format canonique)
#
# Objectif :
# - Représenter UNE action à appliquer sur la page
# - Découpler l’exécution DOM du format texte "////" (ancien fallback retiré)
# - Être sérialisable (logs / metrics / replay / DynamoDB plus tard)
# ------------------------------------------------------------

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class Action:
    """
    Action canonique.

    - value     : valeur à appliquer (ex: "Île-de-France")
    - itype     : type logique ("radio", "checkbox", "dropdown", "text", ...)
    - context   : contexte (question / row label matrice, etc.)
    - qid       : identifiant logique "Q1", "Q2"... (optionnel)
    - target_id : identifiant DOM_REGISTRY (optionnel mais recommandé)
    - meta      : infos additionnelles (debug/source/etc.)
    """
    value: str
    itype: Optional[str] = None
    context: str = ""
    qid: str = ""
    target_id: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dispatcher_line(self) -> str:
        """
        Format le plus riche pour action_dispatcher.execute_action():
        - QID //// target_id //// value //// itype //// context
        - target_id //// value //// itype //// context
        - value //// itype //// context
        """
        v = self.value or ""
        t = self.itype or ""
        c = self.context or ""

        q = (self.qid or "").strip()
        tid = (self.target_id or "").strip()

        if q and tid:
            return f"{q} //// {tid} //// {v} //// {t} //// {c}"
        if tid:
            return f"{tid} //// {v} //// {t} //// {c}"
        return f"{v} //// {t} //// {c}"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "qid": self.qid,
            "target_id": self.target_id,
            "value": self.value,
            "itype": self.itype,
            "context": self.context,
            "meta": self.meta,
        }
