# Survey/prompt_builder.py
"""
Prompt Builder — DOM → Prompt OpenAI (TEXT ONLY)

Entrée :
- question_blocks (issus de dom_analyzer.analyze_dom)

Sortie :
- prompt texte demandant UNE instruction unique
- format STRICT : valeur //// itype //// contexte

Aucune image.
Pensé pour cache, robustesse, 100+ bots.
"""

from __future__ import annotations
from typing import List, Dict, Any
import unicodedata
import re


# =========================
# Utils texte
# =========================

def _norm(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u00a0", " ")
    s = s.strip()
    s = re.sub(r"\s+", " ", s)
    return s


def _escape(s: str) -> str:
    """Empêche les délimiteurs parasites."""
    return _norm(s).replace("////", "/").replace("\n", " ")


# =========================
# Heuristiques métier
# =========================

def _is_open_field(block: Dict[str, Any]) -> bool:
    return block["itype"] in ("text", "textarea")


def _is_choice_field(block: Dict[str, Any]) -> bool:
    return block["itype"] in ("radio", "checkbox", "select")


# =========================
# Construction du prompt
# =========================

def build_prompt(question_blocks: List[Dict[str, Any]]) -> str:
    """
    Construit le prompt texte OpenAI à partir des question_blocks.
    """

    lines: List[str] = []

    # --------- Règles globales (CRUCIAL) ----------
    lines.append(
        "Tu es un répondant ADULTE (18–64). "
        "Tu dois choisir UNE SEULE action applicable IMMÉDIATEMENT sur la page. "
        "Ne réponds JAMAIS par une question. "
        "Ne renvoie JAMAIS d'explication. "
        "Ne renvoie JAMAIS plusieurs actions."
    )

    lines.append(
        "Format OBLIGATOIRE de la réponse (une seule ligne) :\n"
        "valeur //// itype //// contexte"
    )

    lines.append(
        "Contraintes importantes :\n"
        "- itype ∈ {radio, checkbox, dropdown, text, textarea, button}\n"
        "- contexte = texte EXACT de la question\n"
        "- valeur = option existante OU valeur logique non disqualifiante"
    )

    lines.append(
        "Évite toute réponse disqualifiante "
        "(ex: non, jamais, aucun, je préfère ne pas répondre), "
        "SAUF si la question porte explicitement sur les secteurs d’emploi "
        "et que cette option est présente."
    )

    lines.append("\n--- QUESTIONS DISPONIBLES SUR LA PAGE ---")

    # --------- Injection des questions ----------
    for idx, block in enumerate(question_blocks, start=1):
        q = _escape(block.get("question", ""))
        itype = block.get("itype", "")
        options = block.get("options") or []

        lines.append(f"\n{idx}) Question : {q}")
        lines.append(f"   Type attendu : {itype}")

        if options:
            opts = ", ".join(_escape(o) for o in options)
            lines.append(f"   Options possibles : {opts}")
        else:
            lines.append("   Champ ouvert : valeur libre attendue")

    # --------- Instruction finale ----------
    lines.append(
        "\nChoisis LA MEILLEURE action possible MAINTENANT.\n"
        "Rappelle-toi : UNE SEULE ligne en sortie.\n"
        "Format : valeur //// itype //// contexte"
    )

    return "\n".join(lines)


def build_batch_prompt(question_blocks: list[dict]) -> str:
    """
    Construit un prompt OpenAI pour répondre à TOUTES les questions en une fois.
    """
    print("🛠️ Construction du prompt batch pour.")

    lines = []

    lines.append(
        "Tu es un répondant ADULTE (18–64). "
        "Tu vois ci-dessous TOUTES les questions présentes sur une page de survey."
    )

    lines.append(
        "Tu dois répondre à CHAQUE question.\n"
        "Pour CHAQUE question, renvoie EXACTEMENT UNE ligne.\n"
        "AUCUNE explication."
    )

    lines.append(
        "FORMAT STRICT (une ligne par question) :\n"
        "valeur //// itype //// contexte_question"
    )

    lines.append(
        "Contraintes :\n"
        "- itype ∈ {radio, checkbox, dropdown, text, textarea, button}\n"
        "- valeur DOIT être une option existante ou une valeur logique non disqualifiante\n"
        "- Évite : non, jamais, aucun, je préfère ne pas répondre\n"
        "- Respecte exactement le texte du contexte_question"
    )

    lines.append("\n--- QUESTIONS ---")

    for i, block in enumerate(question_blocks, start=1):
        q = block["question"]
        itype = block["itype"]
        opts = block.get("options") or []

        lines.append(f"\n{i}) {q}")
        lines.append(f"Type : {itype}")

        if opts:
            lines.append("Options : " + ", ".join(opts))
        else:
            lines.append("Champ ouvert (valeur libre)")

    lines.append(
        "\nRéponds maintenant.\n"
        "UNE ligne par question.\n"
        "AUCUN texte en dehors des lignes de réponse."
    )

    return "\n".join(lines)
