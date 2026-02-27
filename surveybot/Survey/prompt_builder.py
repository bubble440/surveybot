# Survey/prompt_builder.py
"""
Prompt Builder Ã¢â‚¬â€ DOM Ã¢â€ â€™ Prompt OpenAI (TEXT ONLY)

Entrée :
- question_blocks (issus de dom_analyzer.analyze_dom)

Sortie :
- prompt texte demandant UNE instruction unique
- format STRICT : valeur //// itype //// contexte

Aucune image.
Pensé pour cache, robustesse, 100+ bots.

IMPORTANT - MULTI-SELECT:
Pour les checkbox avec max_select > 1, le séparateur OBLIGATOIRE est "|".
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
    """Empêche les délimiteurs parasites."""
    return _norm(s).replace("////", "/").replace("\n", " ")


def _norm_folded_lc(s: str | None) -> str:
    """Lowercase + suppression des accents pour matching robuste FR/EN."""
    base = unicodedata.normalize("NFKD", s or "")
    no_marks = "".join(ch for ch in base if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", no_marks).strip().lower()


_EXPLICIT_MULTI_PATTERNS = [
    # FR
    r"\bplusieurs\s+(reponses?|choix|options?)\b",
    r"\bcochez\s+tout(?:es)?\s+ce\s+qui\s+s['’]?applique\b",
    r"\bselectionnez\s+tout(?:es)?\s+ce\s+qui\s+s['’]?applique\b",
    r"\bvous\s+pouvez\s+(?:selectionner|choisir|cocher|donner)\s+plusieurs\s+(?:reponses?|choix|options?)\b",
    r"\b(?:vous\s+pouvez\s+)?donner\s+autant\s+de\s+reponses?\s+que\s+vous\s+le\s+souhaitez\b",
    r"\b(?:jusqu['’]?a|maximum)\s*\d+\s*(?:reponses?|choix|options?)\b",
    # EN
    r"\b(?:select|choose|check)\s+all\s+that\s+apply\b",
    r"\bmultiple\s+(?:answers?|choices?|options?)\s+(?:allowed|possible)\b",
    r"\bmore\s+than\s+one\s+(?:answer|choice|option)\b",
    r"\byou\s+may\s+select\s+up\s+to\s+\d+\s*(?:answers?|choices?|options?)\b",
]


def _has_explicit_multi_indicator(question_text: str | None) -> bool:
    text = _norm_folded_lc(question_text)
    if not text:
        return False
    return any(re.search(p, text) for p in _EXPLICIT_MULTI_PATTERNS)


def _selection_rule_for_block(block: Dict[str, Any]) -> str:
    """
    Règle cible pour le nombre de réponses:
    - checkbox/radio/button + indicateur multi explicite dans le libellé => 1..3
    - sinon => exactement 1
    """
    itype = _norm_folded_lc(block.get("itype"))
    if itype in {"checkbox", "radio", "button"} and _has_explicit_multi_indicator(block.get("question")):
        return "multi_1_to_3"
    return "exactly_1"


_TIER_ENTRY_QUESTION_KEYWORDS = [
    # FR
    "tranche", "categorie", "fourchette", "classe", "niveau",
    "combien de salaries", "combien de salarie", "combien d'employes", "combien d employes",
    "taille de l'entreprise", "taille de l entreprise",
    "budget", "depenses", "charges", "chiffre d'affaires", "chiffre d affaires", "ca", "revenu", "salaire",
    # EN
    "range", "bracket", "band", "category", "company size", "employees",
    "revenue", "expenses", "budget", "income", "salary",
]


def _looks_like_tier_entry_question(block: Dict[str, Any]) -> bool:
    itype = _norm_folded_lc(block.get("itype"))
    if itype not in {"radio", "checkbox", "dropdown", "select", "button"}:
        return False
    options = [o for o in (block.get("options") or []) if _norm(str(o))]
    if not options:
        return False
    question = _norm_folded_lc(block.get("question"))
    return any(k in question for k in _TIER_ENTRY_QUESTION_KEYWORDS)


def _tier_entry_option(options: list[str]) -> tuple[int, str]:
    n = len(options)
    if n <= 1:
        k = 1
    elif n in {2, 3}:
        k = n
    else:
        k = int(-(-3 * n // 4))
    return k, options[k - 1]


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
    Construit le prompt texte OpenAI é  partir des question_blocks.
    """

    lines: List[str] = []

    # --------- Règles globales (CRUCIAL) ----------
    lines.append(
        "Tu es un répondant ADULTE (25 ans). "
        "Tu dois choisir UNE SEULE action applicable IMMéDIATEMENT sur la page. "
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
        "- itype doit être l'un de : {radio, checkbox, dropdown, text, textarea, button}\n"
        "- contexte = texte EXACT de la question\n"
        "- valeur = option existante OU valeur logique non disqualifiante"
    )

    lines.append(
        "évite toute réponse disqualifiante "
        "(ex: non, jamais, aucun, je préfère ne pas répondre), "
        "SAUF si la question porte explicitement sur les secteurs d'emploi "
        "et que cette option est présente."
    )

    # Contrainte sexe/genre : toujours masculin, avec l'intitulé exact de l'option
    lines.append(
        "Pour toute question sur le sexe ou le genre, "
        "choisis TOUJOURS l'option qui fait référence au genre masculin "
        "(Homme, Masculin, Male, Man, M, H, etc.) parmi les options proposées. "
        "Utilise EXACTEMENT l'intitulé tel qu'il apparaît dans la liste d'options. "
        "Évite systématiquement: X, Autre, Non-binaire, Préfère ne pas répondre."
    )

    # Contrainte âge : toujours 25 ans
    lines.append(
        "Pour toute question sur l'âge ou la date de naissance:\n"
        "- Si champ texte (âge, années, ans): réponds '25' ou '25 ans'.\n"
        "- Si date de naissance avec dropdowns séparés (mois + année):\n"
        "  * Année: choisis une année cohérente pour 25 ans (2001, 2000, 1999).\n"
        "  * Mois: choisis un mois réaliste (évite le mois en cours pour éviter des âges < 25).\n"
        "  * Exemple: 'Juillet' pour le mois, '2000' pour l'année."
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
    Construit un prompt OpenAI pour répondre é  TOUTES les questions en une fois.
    Format de sortie robuste avec QID + max_select + target_id.
    
    IMPORTANT: Pour les multi-select, le séparateur OBLIGATOIRE est "|".
    """
    lines: list[str] = []

    lines.append(
        "Tu es un répondant ADULTE (25 ans). "
        "Tu vois ci-dessous TOUTES les questions présentes sur une page de survey."
    )

    lines.append(
        "Tu dois répondre à CHAQUE question.\n"
        "Tu ne dois JAMAIS lister toutes les options.\n"
        "Tu dois proposer uniquement la/les réponse(s) nécessaires selon la règle de sélection."
    )

    # Ã¢Å“â€¦ FORMAT RENFORCé: exigence explicite de "|" comme séparateur
    lines.append(
        "FORMAT STRICT (une ligne par question) :\n"
        "QID //// target_id //// valeur //// itype //// contexte\n\n"
        "RèGLES CRITIQUES:\n"
        "- Si la question autorise plusieurs choix (indicateur explicite dans le libellé), réponds sur UNE SEULE LIGNE avec 1 à 3 valeurs séparées par \"|\".\n"
        "- Sans indicateur multi explicite, réponds avec EXACTEMENT 1 valeur.\n"
        "  Exemple: Q1 //// group_abc //// Option A | Option B | Option C //// checkbox //// ...\n"
        "- NE JAMAIS utiliser la virgule \",\" comme séparateur (les options peuvent en contenir).\n"
        "- AUCUNE explication. Aucun texte hors format."
    )

    lines.append(
        "RèGLE NOMBRE DE RéPONSES (checkbox/radio/button uniquement) :\n"
        "- Si le libellé contient un indicateur explicite de multi-sélection (ex: 'plusieurs réponses', 'cochez tout ce qui s'applique', 'select all that apply'), choisis entre 1 et 3 options maximum (idéalement 2-3, jamais >3).\n"
        "- Sinon, choisis exactement 1 option.\n"
        "- Ne déduis PAS le multi-choix depuis le provider/source: base-toi uniquement sur le texte de la question."
    )

    lines.append(
        "Champs ouverts (text/textarea) : si la question contient un exemple (ex: 'E.g.' / 'Ex:'), "
        "N'UTILISE PAS l'exemple comme valeur. Donne une valeur réaliste (ex: code postal FR -> 75001)."
    )

    lines.append(
        "Contraintes :\n"
        "- itype doit être l'un de : {radio, checkbox, dropdown, text, textarea, button}\n"
        "- valeur DOIT être une option existante (si options listées)\n"
        "- évite : non, jamais, aucun, je préfère ne pas répondre\n"
        "- contexte doit correspondre exactement é  la question affichée"
    )

    # Contrainte sexe/genre : toujours masculin, avec l'intitulé exact de l'option
    lines.append(
        "Pour toute question sur le sexe ou le genre, "
        "choisis TOUJOURS l'option qui fait référence au genre masculin "
        "(Homme, Masculin, Male, Man, M, H, etc.) parmi les options proposées. "
        "Utilise EXACTEMENT l'intitulé tel qu'il apparaît dans la liste d'options. "
        "Évite systématiquement: X, Autre, Non-binaire, Préfère ne pas répondre."
    )

    # Contrainte âge : toujours 25 ans
    lines.append(
        "Pour toute question sur l'âge ou la date de naissance:\n"
        "- Si champ texte (âge, années, ans): réponds '25' ou '25 ans'.\n"
        "- Si date de naissance avec dropdowns séparés (mois + année):\n"
        "  * Année: choisis une année cohérente pour 25 ans (2001, 2000, 1999).\n"
        "  * Mois: choisis un mois réaliste (évite le mois en cours pour éviter des âges < 25).\n"
        "  * Exemple: 'Juillet' pour le mois, '2000' pour l'année."
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
        if _looks_like_tier_entry_question(block) and opts:
            k, picked = _tier_entry_option(opts)
            print(f"[PROMPT_BUILDER] tier_entry_rule=1 N={len(opts)} k={k} picked='{picked}'")
            lines.append(f"selection_rule: TIER_ENTRY strict -> répondre EXACTEMENT avec '{picked}'")
            lines.append(f"allowed_values_strict: {picked}")
            lines.append("instruction_stricte: Tu dois répondre EXACTEMENT avec l'un des libellés suivants : {" + picked + "}. Ne paraphrase pas. Ne renvoie rien d'autre.")
        else:
            selection_rule = _selection_rule_for_block(block)
            if selection_rule == "multi_1_to_3":
                lines.append("selection_rule: MULTI explicite -> choisir 1 à 3 options (idéalement 2-3, jamais >3)")
            else:
                lines.append("selection_rule: choisir EXACTEMENT 1 option")

        if opts:
            lines.append("options: " + " | ".join(opts))
        else:
            lines.append("options: (champ ouvert)")

    # Ã¢Å“â€¦ RAPPEL FINAL: séparateur "|" obligatoire
    lines.append(
        "\nRéponds maintenant.\n"
        "Respecte STRICTEMENT le format.\n"
        "RAPPEL: Quand plusieurs valeurs sont requises, sépare-les par \"|\" (jamais par virgule).\n"
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
    # On considère "navigation" uniquement si le texte ressemble é  un CTA court.
    if len(v) > 40:
        return False

    nav_tokens = [
        "continue", "continuer", "next", "suivant", "valider", "submit", "terminer",
        "envoyer", "send", "ok", "start", "commencer"
    ]

    # Match strict (ou quasi-strict avec un petit suffixe de flèche/punct.)
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
    Exclure les champs système & CTA.
    """
    kept = []
    for qb in question_blocks:
        it = getattr(qb, "itype", None) or qb.get("itype")
        label = getattr(qb, "label", None) or qb.get("label") or getattr(qb, "question", None) or qb.get("question")

        it_lc = _norm_lc(it)

        # Normalisation: certains extracteurs renvoient "select"
        # On le traite comme "dropdown" (sinon DOM1 -> vidé -> abandon DOM-only)
        if it_lc == "select":
            it_lc = "dropdown"
            if isinstance(qb, dict):
                qb["itype"] = "dropdown"

        # On n'envoie jamais les buttons é  OpenAI (on les clique nous-mêmes)
        if it_lc == "button":
            continue

        # Si jamais un champ système a quand même traversé (défense en profondeur)
        scope = getattr(qb, "scope_hint", None) or qb.get("scope_hint") or getattr(qb, "dom_scope_hint", None) or qb.get("dom_scope_hint")
        if scope and any(x in _norm_lc(scope) for x in ["__viewstate", "__eventvalidation", "__viewstategenerator", "__eventtarget", "__eventargument"]):
            continue

        # Si label ressemble é  une navigation, on skip
        if _is_navigation_label(label):
            continue

        # types acceptés
        if it_lc in {"radio", "checkbox", "dropdown", "text", "textarea", "matrix_rows_single_choice", "matrix"}:
            kept.append(qb)

    return kept
