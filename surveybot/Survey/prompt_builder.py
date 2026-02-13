# Survey/prompt_builder.py
"""
Prompt Builder Ã¢â‚¬â€ DOM Ã¢â€ â€™ Prompt OpenAI (TEXT ONLY)

EntrÃƒÂ©e :
- question_blocks (issus de dom_analyzer.analyze_dom)

Sortie :
- prompt texte demandant UNE instruction unique
- format STRICT : valeur //// itype //// contexte

Aucune image.
PensÃƒÂ© pour cache, robustesse, 100+ bots.

IMPORTANT - MULTI-SELECT:
Pour les checkbox avec max_select > 1, le sÃƒÂ©parateur OBLIGATOIRE est "|".
NE JAMAIS utiliser "," car les options peuvent contenir des virgules internes.
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
    """EmpÃƒÂªche les dÃƒÂ©limiteurs parasites."""
    return _norm(s).replace("////", "/").replace("\n", " ")


# =========================
# Heuristiques mÃƒÂ©tier
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
    Construit le prompt texte OpenAI ÃƒÂ  partir des question_blocks.
    """

    lines: List[str] = []

    # --------- RÃƒÂ¨gles globales (CRUCIAL) ----------
    lines.append(
        "Tu es un rÃƒÂ©pondant ADULTE (18Ã¢â‚¬â€œ64). "
        "Tu dois choisir UNE SEULE action applicable IMMÃƒâ€°DIATEMENT sur la page. "
        "Ne rÃƒÂ©ponds JAMAIS par une question. "
        "Ne renvoie JAMAIS d'explication. "
        "Ne renvoie JAMAIS plusieurs actions."
    )

    lines.append(
        "Format OBLIGATOIRE de la rÃƒÂ©ponse (une seule ligne) :\n"
        "valeur //// itype //// contexte"
    )

    lines.append(
        "Contraintes importantes :\n"
        "- itype Ã¢Ë†Ë† {radio, checkbox, dropdown, text, textarea, button}\n"
        "- contexte = texte EXACT de la question\n"
        "- valeur = option existante OU valeur logique non disqualifiante"
    )

    lines.append(
        "Ãƒâ€°vite toute rÃƒÂ©ponse disqualifiante "
        "(ex: non, jamais, aucun, je prÃƒÂ©fÃƒÂ¨re ne pas rÃƒÂ©pondre), "
        "SAUF si la question porte explicitement sur les secteurs d'emploi "
        "et que cette option est prÃƒÂ©sente."
    )

    # Contrainte sexe/genre : toujours binaire (Homme/Femme uniquement)
    lines.append(
        "Pour toute question sur le sexe ou le genre, "
        "reponds UNIQUEMENT 'Homme' ou 'Femme' (jamais X, Autre, Non-binaire, Prefere ne pas repondre)."
    )

    # Contrainte âge : toujours 25 ans
    lines.append(
        "Pour toute question sur l'âge (age, années, ans, naissance, date de naissance), "
        "réponds TOUJOURS '25' ou '25 ans' selon le format demandé."
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
        "Format : target_id //// valeur //// itype //// contexte"
    )

    return "\n".join(lines)


def build_batch_prompt(question_blocks: list[dict]) -> str:
    """
    Construit un prompt OpenAI pour rÃƒÂ©pondre ÃƒÂ  TOUTES les questions en une fois.
    Format de sortie robuste avec QID + max_select + target_id.
    
    IMPORTANT: Pour les multi-select, le sÃƒÂ©parateur OBLIGATOIRE est "|".
    """
    lines: list[str] = []

    lines.append(
        "Tu es un rÃƒÂ©pondant ADULTE (18Ã¢â‚¬â€œ64). "
        "Tu vois ci-dessous TOUTES les questions prÃƒÂ©sentes sur une page de survey."
    )

    lines.append(
        "Tu dois rÃƒÂ©pondre ÃƒÂ  CHAQUE question.\n"
        "Tu ne dois JAMAIS lister toutes les options.\n"
        "Tu dois proposer uniquement la/les rÃƒÂ©ponse(s) nÃƒÂ©cessaires selon max_select."
    )

    # Ã¢Å“â€¦ FORMAT RENFORCÃƒâ€°: exigence explicite de "|" comme sÃƒÂ©parateur
    lines.append(
        "FORMAT STRICT (une ligne par question) :\n"
        "QID //// target_id //// valeur //// itype //// contexte\n\n"
        "RÃƒË†GLES CRITIQUES:\n"
        "- Si max_select=1 => EXACTEMENT 1 ligne pour ce QID.\n"
        "- Si max_select>1 => UNE SEULE LIGNE avec les valeurs sÃƒÂ©parÃƒÂ©es par \"|\".\n"
        "  Exemple: Q1 //// group_abc //// Option A | Option B | Option C //// checkbox //// ...\n"
        "- Ã¢Å¡Â Ã¯Â¸Â NE JAMAIS utiliser la virgule \",\" comme sÃƒÂ©parateur (les options peuvent en contenir).\n"
        "- AUCUNE explication. Aucun texte hors format."
    )

    lines.append(
        "Champs ouverts (text/textarea) : si la question contient un exemple (ex: 'E.g.' / 'Ex:'), "
        "N'UTILISE PAS l'exemple comme valeur. Donne une valeur rÃƒÂ©aliste (ex: code postal FR -> 75001)."
    )

    lines.append(
        "Contraintes :\n"
        "- itype Ã¢Ë†Ë† {radio, checkbox, dropdown, text, textarea, button}\n"
        "- valeur DOIT ÃƒÂªtre une option existante (si options listÃƒÂ©es)\n"
        "- Ãƒâ€°vite : non, jamais, aucun, je prÃƒÂ©fÃƒÂ¨re ne pas rÃƒÂ©pondre\n"
        "- contexte doit correspondre exactement ÃƒÂ  la question affichÃƒÂ©e"
    )

    # Contrainte sexe/genre : toujours binaire (Homme/Femme uniquement)
    lines.append(
        "Pour toute question sur le sexe ou le genre, "
        "reponds UNIQUEMENT 'Homme' ou 'Femme' (jamais X, Autre, Non-binaire, Prefere ne pas repondre)."
    )

    # Contrainte âge : toujours 25 ans
    lines.append(
        "Pour toute question sur l'âge (age, années, ans, naissance, date de naissance), "
        "réponds TOUJOURS '25' ou '25 ans' selon le format demandé."
    )

    lines.append("\n--- QUESTIONS ---")

    for i, block in enumerate(question_blocks or [], start=1):
        qid = f"Q{i}"
        q = _escape(block.get("question", ""))
        itype = _escape(block.get("itype", ""))
        opts = [_escape(o) for o in (block.get("options") or []) if o]
        max_sel = int(block.get("max_select", 1) or 1)
        target_id = _escape(block.get("target_id", ""))

        lines.append(f"\n{qid}")
        lines.append(f"target_id: {target_id}")
        lines.append(f"contexte: {q}")
        lines.append(f"itype: {itype}")
        lines.append(f"max_select: {max_sel}")

        if opts:
            lines.append("options: " + " | ".join(opts))
        else:
            lines.append("options: (champ ouvert)")

    # Ã¢Å“â€¦ RAPPEL FINAL: sÃƒÂ©parateur "|" obligatoire
    lines.append(
        "\nRÃƒÂ©ponds maintenant.\n"
        "Respecte STRICTEMENT le format.\n"
        "RAPPEL: Pour max_select>1, sÃƒÂ©pare les valeurs par \"|\" (jamais par virgule).\n"
        "Ne renvoie rien d'autre."
    )
        
    return "\n".join(lines)

def _norm_lc(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()

def _is_navigation_label(label: str | None) -> bool:
    v = _norm_lc(label)
    if not v:
        return False

    # IMPORTANT:
    # On ne doit PAS rejeter une vraie question juste parce qu'elle contient
    # un mot comme "commencer"/"continuer" dans un texte long
    # (ex: "Avant de commencer, ..." sur les pages de consentement Walr).
    # On considÃƒÂ¨re "navigation" uniquement si le texte ressemble ÃƒÂ  un CTA court.
    if len(v) > 40:
        return False

    nav_tokens = [
        "continue", "continuer", "next", "suivant", "valider", "submit", "terminer",
        "envoyer", "send", "ok", "start", "commencer"
    ]

    # Match strict (ou quasi-strict avec un petit suffixe de flÃƒÂ¨che/punct.)
    for tok in nav_tokens:
        if v == tok:
            return True

        if v.startswith(tok) and len(v) <= (len(tok) + 5):
            tail = v[len(tok):].strip()
            if tail in ("", ">", ">>", "Ã‚Â»", "Ã‚Â»>", ":", "-", "Ã¢â‚¬â€œ", "Ã¢â€ â€™", "Ã¢Å¾Â¡"):
                return True

    return False

def filter_blocks_for_openai(question_blocks: list) -> list:
    """
    Garder uniquement ce qui est 'answerable' : radio/checkbox/dropdown/text.
    Exclure les champs systÃƒÂ¨me & CTA.
    """
    kept = []
    for qb in question_blocks:
        it = getattr(qb, "itype", None) or qb.get("itype")
        label = getattr(qb, "label", None) or qb.get("label") or getattr(qb, "question", None) or qb.get("question")

        it_lc = _norm_lc(it)

        # Normalisation: certains extracteurs renvoient "select"
        # On le traite comme "dropdown" (sinon DOM1 -> vidÃƒÂ© -> fallback vision)
        if it_lc == "select":
            it_lc = "dropdown"
            if isinstance(qb, dict):
                qb["itype"] = "dropdown"

        # On n'envoie jamais les buttons ÃƒÂ  OpenAI (on les clique nous-mÃƒÂªmes)
        if it_lc == "button":
            continue

        # Si jamais un champ systÃƒÂ¨me a quand mÃƒÂªme traversÃƒÂ© (dÃƒÂ©fense en profondeur)
        scope = getattr(qb, "scope_hint", None) or qb.get("scope_hint") or getattr(qb, "dom_scope_hint", None) or qb.get("dom_scope_hint")
        if scope and any(x in _norm_lc(scope) for x in ["__viewstate", "__eventvalidation", "__viewstategenerator", "__eventtarget", "__eventargument"]):
            continue

        # Si label ressemble ÃƒÂ  une navigation, on skip
        if _is_navigation_label(label):
            continue

        # types acceptÃƒÂ©s
        if it_lc in {"radio", "checkbox", "dropdown", "text", "textarea", "matrix_rows_single_choice", "matrix"}:
            kept.append(qb)

    return kept