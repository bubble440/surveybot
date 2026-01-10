# Survey/action_types.py
# ------------------------------------------------------------
# Action (format canonique)
#
# Objectif :
# - Représenter UNE action à appliquer sur la page
# - Découpler l’exécution DOM du format texte "////" (fallback vision)
# - Être sérialisable (logs / metrics / replay / DynamoDB plus tard)
# ------------------------------------------------------------

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class Action:
    """
    Action canonique.

    - value   : la valeur à appliquer (ex: "Souvent", "1999", "Paris")
    - itype   : type logique (ex: "radio", "checkbox", "dropdown", "text", "textarea", etc.)
    - context : contexte optionnel (ex: libellé de ligne pour matrice, question)
    - meta    : infos additionnelles non critiques (debug, source, qid...)
    """
    value: str
    itype: Optional[str] = None
    context: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_legacy_instruction(self) -> str:
        """
        Convertit en format legacy pour compatibilité avec execute_action(instruction: str).
        On garde toujours 3 segments pour stabiliser le parsing.
        """
        t = self.itype or ""
        c = self.context or ""
        v = self.value or ""
        return f"{v} //// {t} //// {c}"

    def as_dict(self) -> Dict[str, Any]:
        """Sérialisation simple (logs / export)."""
        return {
            "value": self.value,
            "itype": self.itype,
            "context": self.context,
            "meta": self.meta,
        }
