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

try:
    from Survey.dom_selection_rules import (
        explicit_exact_count_from_question,
        has_explicit_multi_indicator,
    )
except ImportError:
    from surveybot.Survey.dom_selection_rules import (
        explicit_exact_count_from_question,
        has_explicit_multi_indicator,
    )


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


def _has_explicit_multi_indicator(question_text: str | None) -> bool:
    return has_explicit_multi_indicator(question_text)


def _explicit_exact_count_from_question(question_text: str | None) -> int | None:
    return explicit_exact_count_from_question(question_text)


def _selection_signal_text(block: Dict[str, Any]) -> str:
    parts: list[str] = []

    q = _norm(block.get("question", ""))
    if q:
        parts.append(q)

    instruction = _norm(block.get("instruction", ""))
    if instruction:
        parts.append(instruction)

    ctx = block.get("context")
    if isinstance(ctx, dict):
        for key in ("instruction", "instruction_text"):
            value = _norm(ctx.get(key, ""))
            if value:
                parts.append(value)

    return " ".join(parts)


def _selection_rule_for_block(block: Dict[str, Any]) -> str:
    """
    Règle cible pour le nombre de réponses:
    - checkbox/radio/button + cardinalité exacte explicite dans le libellé => exactly_N
    - checkbox/radio/button + indicateur multi explicite => 1..3
    - sinon => exactement 1
    """
    itype = _norm_folded_lc(block.get("itype"))
    if itype in {"checkbox", "radio", "button"}:
        signal_text = _selection_signal_text(block)
        exact_count = _explicit_exact_count_from_question(signal_text)
        if exact_count and exact_count > 1:
            return f"exactly_{exact_count}"
        if _has_explicit_multi_indicator(signal_text):
            return "multi_1_to_3"
    return "exactly_1"


def _selection_bounds_for_prompt(block: Dict[str, Any]) -> tuple[int, int]:
    """Normalise min_select/max_select pour les consignes de prompt."""
    itype = _norm_folded_lc(block.get("itype"))
    try:
        max_sel = int(block.get("max_select", 1) or 1)
    except Exception:
        max_sel = 1
    max_sel = max(1, max_sel)

    ctx = block.get("context") if isinstance(block.get("context"), dict) else {}
    is_multi_text = (
        itype in {"text", "textarea", "number"}
        and max_sel >= 2
        and (str(block.get("target_id") or "").startswith("multi_") or str((ctx or {}).get("kind") or "") == "multi_text")
    )
    if is_multi_text:
        return max_sel, max_sel

    if itype != "checkbox":
        return 1, 1

    min_raw = block.get("min_select", 1)
    try:
        min_sel = int(min_raw)
    except Exception:
        min_sel = 1

    min_sel = max(0, min_sel)
    min_sel = min(min_sel, max_sel)
    return min_sel, max_sel


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

_CLASSIFICATION_QUESTION_KEYWORDS = [
    # FR - poste/fonction/statut
    "poste", "fonction", "profession", "metier", "occupation", "categorie socioprofessionnelle", "csp", "statut",
    # FR - éducation
    "niveau d'etude", "niveau d etude", "diplome", "etudes", "formation",
    # FR/EN - hiérarchie
    "cadre", "direction", "manager", "executive", "senior", "lead", "head", "director", "owner", "founder",
    # EN
    "job", "position", "role", "education level", "degree", "highest education",
]

_CLASSIFICATION_DISQUALIFIERS = [
    "autre", "other", "aucun", "none", "ne sais pas", "don't know", "dont know",
    "prefere ne pas", "prefer not", "sans activite", "no activity", "n/a", "na",
]

_CLASSIFICATION_SCORING = [
    # Très haut
    (120, ["direction generale", "executive", "c-level", "ceo", "founder", "owner", "head", "director", "partner"]),
    (110, ["direction", "directeur", "administrative", "professionnelle"]),
    # Haut
    (90, ["cadre", "manager", "lead", "supervisor", "chef d'entreprise", "chef d entreprise"]),
    # Moyen
    (60, ["profession liberale", "profession intermediaire", "technicien", "contremaitre", "agent de maitrise"]),
    # Bas
    (20, ["employe", "ouvrier", "etudiant", "sans activite", "foyer", "retraite", "premier emploi"]),
]


_PERSONA_RESIDENCE_COUNTRY = "France"
_PERSONA_RESIDENCE_CITY = "Paris"

_RESIDENCE_COUNTRY_PATTERNS = [
    # FR
    "pays habitez",
    "pays de residence",
    "pays de résidence",
    "dans quel pays",
    "quel pays",
    "pays residez",
    "pays résidez",
    # EN
    "country of residence",
    "country do you live",
    "which country",
    "where do you live",
    "live in",
]

_FREQ_HIGH_MARKERS = [
    "plusieurs fois par jour", "tous les jours", "quotidien", "daily", "every day",
    "plusieurs fois par semaine", "most days", "often", "souvent",
]

_FREQ_LOW_MARKERS = [
    "une fois ce mois", "plusieurs fois ce mois", "once this month", "this month",
    "jamais", "never", "pas achete", "pas acheté", "ne sais pas", "dont know", "don't know",
]

_FREQ_UNIT_MARKERS = [
    "jour", "jours", "day", "week", "semaine", "month", "mois", "fois",
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


def _looks_like_classification_question(block: Dict[str, Any]) -> bool:
    itype = _norm_folded_lc(block.get("itype"))
    if itype not in {"radio", "checkbox", "dropdown", "select", "button"}:
        return False
    options = [o for o in (block.get("options") or []) if _norm(str(o))]
    if not options:
        return False
    question = _norm_folded_lc(block.get("question"))
    return any(k in question for k in _CLASSIFICATION_QUESTION_KEYWORDS)


def _pick_best_classification_option(options: list[str]) -> str:
    best_option = options[0]
    best_score = float("-inf")

    for option in options:
        folded = _norm_folded_lc(option)
        if not folded:
            continue

        score = 0
        if any(bad in folded for bad in _CLASSIFICATION_DISQUALIFIERS):
            score -= 200

        for weight, keywords in _CLASSIFICATION_SCORING:
            if any(keyword in folded for keyword in keywords):
                score += weight

        if score > best_score:
            best_score = score
            best_option = option

    return best_option


def _tier_entry_option(options: list[str]) -> tuple[int, str]:
    n = len(options)
    if n <= 1:
        k = 1
    elif _looks_like_frequency_scale(options):
        top_third = max(1, -(-n // 3))
        k = max(1, (top_third + 1) // 2)
    elif n in {2, 3}:
        k = n
    else:
        k = int(-(-3 * n // 4))
    return k, options[k - 1]


def _looks_like_frequency_scale(options: list[str]) -> bool:
    if not options:
        return False

    folded_options = [_norm_folded_lc(opt) for opt in options]
    high = any(any(marker in opt for marker in _FREQ_HIGH_MARKERS) for opt in folded_options)
    low = any(any(marker in opt for marker in _FREQ_LOW_MARKERS) for opt in folded_options)
    has_units = sum(
        1 for opt in folded_options if any(marker in opt for marker in _FREQ_UNIT_MARKERS)
    )
    return high and low and has_units >= 2


def _find_option_exact(options: list[str], expected_value: str) -> str | None:
    expected_folded = _norm_folded_lc(expected_value)
    for option in options or []:
        if _norm_folded_lc(option) == expected_folded:
            return option
    return None


def _is_residence_country_question(block: Dict[str, Any]) -> bool:
    itype = _norm_folded_lc(block.get("itype"))
    if itype not in {"radio", "checkbox", "dropdown", "select", "button"}:
        return False
    question = _norm_folded_lc(block.get("question"))
    if not question:
        return False
    has_country_token = any(tok in question for tok in ("pays", "country"))
    if not has_country_token:
        return False
    return any(p in question for p in _RESIDENCE_COUNTRY_PATTERNS)


def _matrix_row_labels(block: Dict[str, Any]) -> list[str]:
    """Retourne les libellés de lignes matrix si disponibles dans le contexte extracteur."""
    context = block.get("context") or {}
    rows = context.get("matrix_rows") if isinstance(context, dict) else None
    if not isinstance(rows, list):
        return []
    return [_escape(str(r)) for r in rows if _norm(str(r))]


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

    # ── PERSONA FINANCIER ──────────────────────────────────────────────────
    # Ce bloc établit un profil socio-économique cohérent et stable.
    # Il est CRITIQUE pour éviter la disqualification sur les questions
    # de revenu, emploi, secteur ou statut financier.
    # Règle absolue : ne jamais choisir "Préfère ne pas répondre" ou équivalent
    # sur ces thèmes — ces options sont DISQUALIFICATOIRES.
    lines.append(
        "PERSONA SOCIO-ÉCONOMIQUE (fixe, cohérent sur toute la session) :\n"
        "Tu incarnes un cadre supérieur ou dirigeant d'entreprise de 25 ans, "
        "résidant en zone urbaine (Paris). "
        "Ton foyer dispose d'un revenu annuel élevé (> 100 000 EUR / an, ou équivalent local). "
        "Tu as un patrimoine personnel significatif (immobilier, placements financiers). "
        "Tu travailles dans un secteur premium : Finance, Conseil, Tech, Santé, ou Direction générale.\n\n"
        "RÈGLES ABSOLUES pour les questions financières ou sensibles :\n"
        "- Revenu du foyer / personnel : choisis TOUJOURS la tranche la plus élevée disponible "
        "  parmi les options (ex: '100 000 EUR et plus', '250 000+', 'Plus d'1 million EUR', etc.).\n"
        "- Si la tranche la plus haute semble excessive (ex: '10M EUR+'), "
        "  choisis la DEUXIÈME tranche en partant du haut.\n"
        "- Secteur d'activité : 'Finance', 'Conseil', 'Technologies de l'Information', "
        "  'Direction / Management', 'Santé' — dans cet ordre de préférence selon les options.\n"
        "- Statut professionnel : 'Cadre supérieur', 'Dirigeant', 'Travailleur indépendant / Freelance' "
        "  — jamais 'Sans emploi', 'Retraité', 'Étudiant'.\n"
        "- Patrimoine / épargne / investissements : affirme toujours en posséder, "
        "  choisis les options qui indiquent un patrimoine substantiel.\n"
        "- INTERDIT ABSOLU : 'Préfère ne pas répondre', 'Ne souhaite pas indiquer', "
        "  'Je ne sais pas', 'Aucun', 'Non applicable' — sur tout sujet financier ou professionnel. "
        "  Ces réponses DISQUALIFIENT le répondant.\n"
        "- Si la question est perçue comme indiscrète (revenu, patrimoine, situation fiscale) : "
        "  réponds quand même avec une valeur haute cohérente avec le persona. "
        "  Un répondant aisé n'hésite pas sur ces sujets."
    )
    # ── FIN PERSONA FINANCIER ─────────────────────────────────────────────

    lines.append("\n--- QUESTIONS DISPONIBLES SUR LA PAGE ---")

    # --------- Injection des questions ----------
    for idx, block in enumerate(question_blocks, start=1):
        q = _escape(block.get("question", ""))
        itype = block.get("itype", "")
        options = block.get("options") or []
        matrix_rows = _matrix_row_labels(block)

        lines.append(f"\n{idx}) Question : {q}")
        lines.append(f"   Type attendu : {itype}")
        if matrix_rows:
            lines.append(f"   Sous-questions (lignes matrix) : {' | '.join(matrix_rows)}")

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

    print(
        f"[PROMPT_PERSONA] residence_country={_PERSONA_RESIDENCE_COUNTRY} "
        f"residence_city={_PERSONA_RESIDENCE_CITY}"
    )

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
        "- Pour chaque QID, le nombre de valeurs à renvoyer est défini par la selection_rule de ce QID. Ne pas utiliser max_select comme cible à atteindre : c'est un plafond, pas une obligation.\n"
        "- Si plusieurs valeurs sont nécessaires, les séparer UNIQUEMENT par \"|\".\n"
        "- Exemple: Q1 //// group_abc //// Option A|Option B|Option C //// checkbox //// ...\n"
        "- NE JAMAIS utiliser la virgule \",\" comme séparateur (les options peuvent en contenir).\n"
        "- AUCUNE explication. Aucun texte hors format."
    )

    lines.append(
        "RÈGLE CHAMP MULTI-CASES (context.kind=multi_text) :\n"
        "- Ce n'est PAS une multi-sélection checkbox: c'est un champ composé de plusieurs cases texte.\n"
        "- Si max_select >= 2, valeur DOIT contenir EXACTEMENT max_select segments séparés par \"|\".\n"
        "- Exemple DOB (3 cases): 03|02|2001"
    )

    lines.append(
        "RèGLE NOMBRE DE RéPONSES:\n"
        "- Ne déduis PAS le nombre depuis le provider/source.\n"
        "- Pour chaque QID, le nombre de valeurs à renvoyer est défini par la selection_rule de ce QID. Ne pas utiliser max_select comme cible à atteindre : c'est un plafond, pas une obligation."
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

    lines.append(
        "RÈGLE SPÉCIALE MATRICES (itype=matrix) :\n"
        "- valeur DOIT être au format STRICT: row_label || col_label\n"
        "- Si matrix_active_row est fourni dans le contexte, row_label DOIT être EXACTEMENT cette valeur (ne choisis jamais une autre ligne).\n"
        "- Exemple attendu: Crédit consommation || Transféré vers Revolut\n"
        "- INTERDIT: répondre uniquement une colonne (ex: 'Transféré vers Revolut')."
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

    # ── PERSONA FINANCIER ──────────────────────────────────────────────────
    # Ce bloc établit un profil socio-économique cohérent et stable.
    # Il est CRITIQUE pour éviter la disqualification sur les questions
    # de revenu, emploi, secteur ou statut financier.
    # Règle absolue : ne jamais choisir "Préfère ne pas répondre" ou équivalent
    # sur ces thèmes — ces options sont DISQUALIFICATOIRES.
    lines.append(
        "PERSONA SOCIO-ÉCONOMIQUE (fixe, cohérent sur toute la session) :\n"
        "Tu incarnes un cadre supérieur ou dirigeant d'entreprise de 25 ans, "
        "résidant en zone urbaine (Paris). "
        "Ton foyer dispose d'un revenu annuel élevé (> 100 000 EUR / an, ou équivalent local). "
        "Tu as un patrimoine personnel significatif (immobilier, placements financiers). "
        "Tu travailles dans un secteur premium : Finance, Conseil, Tech, Santé, ou Direction générale.\n\n"
        "RÈGLES ABSOLUES pour les questions financières ou sensibles :\n"
        "- Revenu du foyer / personnel : choisis TOUJOURS la tranche la plus élevée disponible "
        "  parmi les options (ex: '100 000 EUR et plus', '250 000+', 'Plus d'1 million EUR', etc.).\n"
        "- Si la tranche la plus haute semble excessive (ex: '10M EUR+'), "
        "  choisis la DEUXIÈME tranche en partant du haut.\n"
        "- Secteur d'activité : 'Finance', 'Conseil', 'Technologies de l'Information', "
        "  'Direction / Management', 'Santé' — dans cet ordre de préférence selon les options.\n"
        "- Statut professionnel : 'Cadre supérieur', 'Dirigeant', 'Travailleur indépendant / Freelance' "
        "  — jamais 'Sans emploi', 'Retraité', 'Étudiant'.\n"
        "- Patrimoine / épargne / investissements : affirme toujours en posséder, "
        "  choisis les options qui indiquent un patrimoine substantiel.\n"
        "- INTERDIT ABSOLU : 'Préfère ne pas répondre', 'Ne souhaite pas indiquer', "
        "  'Je ne sais pas', 'Aucun', 'Non applicable' — sur tout sujet financier ou professionnel. "
        "  Ces réponses DISQUALIFIENT le répondant.\n"
        "- Si la question est perçue comme indiscrète (revenu, patrimoine, situation fiscale) : "
        "  réponds quand même avec une valeur haute cohérente avec le persona. "
        "  Un répondant aisé n'hésite pas sur ces sujets."
    )
    # ── FIN PERSONA FINANCIER ─────────────────────────────────────────────

    lines.append("\n--- QUESTIONS ---")

    for i, block in enumerate(question_blocks or [], start=1):
        qid = f"Q{i}"
        q = _escape(block.get("question", ""))
        itype = _escape(block.get("itype", ""))
        opts = [_escape(o) for o in (block.get("options") or []) if o]
        min_sel, max_sel = _selection_bounds_for_prompt(block)
        target_id = _escape(block.get("target_id", ""))
        matrix_rows = _matrix_row_labels(block)
        matrix_active_row = _escape((block.get("context") or {}).get("matrix_active_row", ""))

        lines.append(f"\n{qid}")
        lines.append(f"target_id: {target_id}")
        lines.append(f"contexte: {q}")
        if matrix_rows:
            lines.append(f"sous_questions_matrix: {' | '.join(matrix_rows)}")
            lines.append("matrix_answer_format: row_label || col_label (row obligatoire, jamais col seule)")
        if matrix_active_row:
            lines.append(f"matrix_active_row: {matrix_active_row}")
            lines.append("matrix_rule_active_row: row_label DOIT être EXACTEMENT matrix_active_row")
            lines.append(f"matrix_example_active_row: {matrix_active_row} || Transféré vers Revolut")
        lines.append(f"itype: {itype}")
        display_max_sel = min(max_sel, 5) if (min_sel == 1 and max_sel > 3) else max_sel
        lines.append(f"max_select: {display_max_sel}")
        lines.append(f"min_select: {min_sel}")
        ctx = block.get("context") if isinstance(block.get("context"), dict) else {}
        if (ctx or {}).get("kind") == "multi_text" and max_sel >= 2:
            lines.append(
                f"CHAMP MULTI-CASES: fournir {max_sel} valeurs séparées par | (ex: 03|02|2001)"
            )
        forced_country = None
        if _is_residence_country_question(block) and opts:
            forced_country = _find_option_exact(opts, _PERSONA_RESIDENCE_COUNTRY)
            print(
                f"[PROMPT_PERSONA] residence_country_question=1 target_id={target_id} "
                f"option_present={bool(forced_country)}"
            )

        if itype == "checkbox":
            if min_sel == 0:
                lines.append(
                    f"selection_rule: Pour QID={qid}, renvoyer entre 0 et {max_sel} valeur(s) séparée(s) par |. Préfère 1 choix sûr si des options existent, sauf indication explicite autorisant 0 réponse. / For QID={qid}, return between 0 and {max_sel} values separated by |. Prefer 1 safe choice when options exist, unless the UI/text explicitly allows 0 answer."
                )
            elif min_sel == max_sel:
                lines.append(
                    f"selection_rule: Pour QID={qid}, renvoyer EXACTEMENT {max_sel} valeur(s) séparée(s) par |. / For QID={qid}, return exactly {max_sel} values separated by |."
                )
            else:
                lines.append(
                    f"selection_rule: Pour QID={qid}, renvoyer entre {min_sel} et {max_sel} valeur(s) séparée(s) par |. / For QID={qid}, return between {min_sel} and {max_sel} values separated by |."
                )
        else:
            lines.append(
                f"selection_rule: Pour QID={qid}, renvoyer EXACTEMENT 1 valeur"
            )
        if forced_country:
            lines.append(
                f"selection_rule: RESIDENCE_COUNTRY strict -> répondre EXACTEMENT avec '{forced_country}'"
            )
            lines.append(f"allowed_values_strict: {forced_country}")
            lines.append(
                "instruction_stricte: Persona résidence prioritaire. "
                "Tu dois répondre EXACTEMENT avec {"
                + forced_country
                + "}. Ne paraphrase pas."
            )
        elif _looks_like_classification_question(block) and opts:
            picked = _pick_best_classification_option(opts)
            print(f"[PROMPT_BUILDER] classification_rule=1 N={len(opts)} picked='{picked}'")
            lines.append(f"selection_rule: CLASSIFICATION_BEST strict -> répondre EXACTEMENT avec '{picked}'")
            lines.append(f"allowed_values_strict: {picked}")
            lines.append("instruction_stricte: Tu dois répondre EXACTEMENT avec l'un des libellés suivants : {" + picked + "}. Ne paraphrase pas. Ne renvoie rien d'autre.")
        elif _looks_like_tier_entry_question(block) and opts:
            k, picked = _tier_entry_option(opts)
            print(f"[PROMPT_BUILDER] tier_entry_rule=1 N={len(opts)} k={k} picked='{picked}'")
            lines.append(f"selection_rule: TIER_ENTRY strict -> répondre EXACTEMENT avec '{picked}'")
            lines.append(f"allowed_values_strict: {picked}")
            lines.append("instruction_stricte: Tu dois répondre EXACTEMENT avec l'un des libellés suivants : {" + picked + "}. Ne paraphrase pas. Ne renvoie rien d'autre.")
        elif bool((ctx or {}).get("consent_modal_radio")) and opts:
            forced_consent = _preferred_consent_option(opts)
            if forced_consent:
                lines.append(f"selection_rule: CONSENT_ACCEPT strict -> répondre EXACTEMENT avec '{forced_consent}'")
                lines.append(f"allowed_values_strict: {forced_consent}")
                lines.append("instruction_stricte: Consent modal. Tu dois choisir l'option de consentement positive et jamais l'option de refus.")
            else:
                lines.append("selection_rule: choisir une option valide de la liste")

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

def _preferred_consent_option(options: list[str]) -> str | None:
    """Retourne l'option consentement positive à forcer, si détectée."""
    if not options:
        return None

    for opt in options:
        v = _norm_lc(opt)
        if not v:
            continue
        if "je consens" in v or "i consent" in v or "i agree" in v:
            if "je ne consens pas" in v or "i do not consent" in v or "disagree" in v:
                continue
            return opt
    return None

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
