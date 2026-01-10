"""
preselection/question_validation.py

Objectif
--------
Centraliser la validation "métier" d'un bloc question de présélection.

Pourquoi ce patch ?
-------------------
TopSurveys (et parfois les fournisseurs externes) peuvent changer légèrement la
formulation des messages de disqualification :
  - tutoiement/vouvoiement
  - français/anglais
  - accents/ponctuation
  - petites variantes ("not eligible", "screened out", etc.)

Pour éviter les faux négatifs, on normalise les textes et on utilise des patterns
robustes. La règle d’or : tout ce qui ressemble à une disqualification doit être
détecté ICI (une seule source de vérité).
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Optional

from Management.guards.sensitive_question_guard import is_sensitive_question
from Management.guards.survey_difficulty_guard import detect_strict_survey

@dataclass
class QuestionDecision:
    action: str
    reason: Optional[str] = None

# actions possibles :
# - "CONTINUE"
# - "SKIP"
# - "DISQUALIFIED"
# - "NOT_RETURNED"
# - "BLOCKED"
# - "RESTART_SURVEY"

def validate_question(question_text: str, page_text: str) -> QuestionDecision:
    # ⚠️ Robustesse : certains extracteurs peuvent renvoyer None.
    question_text = question_text or ""
    page_text = page_text or ""

    if is_sensitive_question(question_text):
        return QuestionDecision("SKIP", "sensitive_question")

    dq_reason = detect_disqualification_reason(question_text, page_text)
    if dq_reason:
        return QuestionDecision("DISQUALIFIED", dq_reason)

    return QuestionDecision("CONTINUE")

def detect_disqualification_reason(question_text: Optional[str], page_text: Optional[str]) -> Optional[str]:
    """Détecte de manière robuste une disqualification, renvoie une reason stable.

    Centralisée pour éviter :
      - 3 implémentations différentes (source de bugs)
      - dépendance à une phrase exacte

    Retour :
      - None si aucun signal de disqualification
      - str reason (stable) si disqualification détectée
    """
    blob = f"{question_text or ''}\n{page_text or ''}".strip()
    if not blob:
        return None

    t = _norm_text(blob)
    if not t:
        return None

    # --- Signaux "forts" (faible risque de faux positifs) -----------------
    strong_phrases = (
        # FR (tutoiement / vouvoiement)
        "tu n'as pas ete qualifie",
        "tu ne t'es pas qualifie",
        "vous n'avez pas ete qualifie",
        "vous ne vous etes pas qualifie",
        "vous n'etes pas qualifie",
        "vous n etes pas qualifie",
        "pas ete qualifie cette fois",
        "pas qualifie cette fois",
        # EN
        "not qualified",
        "you are not qualified",
        "did not qualify",
        "didn't qualify",
        "you do not qualify",
        "not eligible",
        "you are not eligible",
        "ineligible",
        "screened out",
        "disqualified",
    )

    for p in strong_phrases:
        if p in t:
            return "qualification_failed"

    # --- Patterns regex (robustes aux petites variations) ------------------
    # Exemples: "Vous n'avez pas été qualifié(e)" / "tu ne t'es pas qualifié"
    fr_rx = re.compile(
        r"\b(?:tu|vous)\s+(?:n'?as|n'?avez|n'?etes|ne\s+t'?es|ne\s+vous\s+etes)\s+pas\s+(?:ete\s+)?qualifi(?:e|es)?\b"
    )
    if fr_rx.search(t):
        return "qualification_failed"

    en_rx = re.compile(
        r"\b(?:did\s+not\s+qualify|do\s+not\s+qualify|not\s+qualified|not\s+eligible|screened\s+out)\b"
    )
    if en_rx.search(t):
        return "qualification_failed"

    # --- Signaux "faibles" : on exige une co-occurrence -------------------
    # "malheureusement"/"unfortunately" seuls sont trop vagues → on les couple
    # à un contexte typique (qualif/eligible/screened/fit).
    if "malheureusement" in t or "unfortunately" in t:
        if re.search(r"\b(qualif|eligible|eligib|screened|fit)\b", t):
            return "qualification_failed"

    # "not a good fit" est commun en screening (EN), mais peut apparaître ailleurs :
    # on exige un contexte "survey/study/questionnaire/eligible".
    if "not a good fit" in t and re.search(r"\b(survey|study|questionnaire|qualif|eligible|eligib)\b", t):
        return "qualification_failed"

    return None


def _norm_text(s: str) -> str:
    """Normalise un texte pour matching robuste.

    - minuscule
    - suppression des accents
    - collapse des espaces
    """
    if not s:
        return ""
    s = s.strip().lower()
    # "qualifié" -> "qualifie"
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"\s+", " ", s)
    return s