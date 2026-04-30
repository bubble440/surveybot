# Survey/prompt_builder.py
"""
Prompt Builder  DOM → Prompt OpenAI (TEXT ONLY)

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
from copy import deepcopy
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
    normalized = re.sub(r"\s+", " ", no_marks).strip().lower()
    return normalized.replace("’", "'").replace("`", "'").replace("´", "'")


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


def _selection_max_for_prompt(block: Dict[str, Any]) -> int:
    """Normalise max_select pour les consignes de prompt."""
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
        return max_sel

    if itype != "checkbox":
        return 1

    return max_sel


_SURVEY_CONSENT_CONTEXT_PATTERNS = [
    # FR
    "sondage", "survey", "participation", "confidentialite", "confidentialité",
    "contenu", "regles", "règles", "preserver", "préserver",
]

_CONSENT_ACCEPT_PATTERNS = [
    "j'accepte", "j accepte", "i agree", "i accept", "je consens", "oui, je consens",
    "accepter et continuer", "accept and continue",
]

_CONSENT_REJECT_PATTERNS = [
    "je n'accepte pas", "je n accepte pas", "je refuse", "i do not accept", "i don't accept",
    "i disagree", "disagree", "ne souhaite pas poursuivre", "ne pas poursuivre",
    "do not continue", "not continue", "quitter", "exit",
]


# Détection des questions de disponibilité/volonté de participation (répondant ou tiers désigné).
# Deux ensembles de tokens doivent être présents simultanément dans le texte de la question.
_PARTICIPATION_WILLINGNESS_TOKENS = [
    "disponible", "heureux", "heureuse", "pret", "prete", "d accord",
    "agree", "willing", "happy", "available", "volontaire",
]
_PARTICIPATION_WILLINGNESS_SURVEY_TOKENS = [
    "enquete", "sondage", "etude", "recherche", "survey", "study",
]


def _preferred_participation_willingness_option(block: Dict[str, Any], options: list[str]) -> str | None:
    """Détecte les questions 'le participant serait-il disponible/heureux/prêt pour l'enquête ?'
    et retourne l'option 'oui/yes', car répondre non est systématiquement disqualificatoire."""
    if not options:
        return None
    question = _norm_folded_lc(block.get("question"))
    instruction = _norm_folded_lc(block.get("instruction"))
    context_signal = f"{question} {instruction}".strip()
    has_willingness = any(tok in context_signal for tok in _PARTICIPATION_WILLINGNESS_TOKENS)
    has_survey_ctx = any(tok in context_signal for tok in _PARTICIPATION_WILLINGNESS_SURVEY_TOKENS)
    if not (has_willingness and has_survey_ctx):
        return None
    yes_option = None
    has_no_option = False
    for option in options:
        folded = _norm_folded_lc(option)
        if not folded:
            continue
        if yes_option is None and folded in ("oui", "yes"):
            yes_option = option
        if folded in ("non", "no"):
            has_no_option = True
    if yes_option and has_no_option:
        return yes_option
    return None


_SECTOR_SCREENER_MAX_OPTIONS = 15

def _preferred_survey_consent_option(block: Dict[str, Any], options: list[str]) -> str | None:
    """Retourne l'option d'acceptation pour consentement participation/confidentialité, si détectée."""
    if not options:
        return None

    question = _norm_folded_lc(block.get("question"))
    instruction = _norm_folded_lc(block.get("instruction"))
    context_signal = f"{question} {instruction}".strip()
    has_context_signal = any(tok in context_signal for tok in _SURVEY_CONSENT_CONTEXT_PATTERNS)

    accept_option = None
    has_reject_option = False

    for option in options:
        folded_option = _norm_folded_lc(option)
        if not folded_option:
            continue
        if accept_option is None and any(pat in folded_option for pat in _CONSENT_ACCEPT_PATTERNS):
            accept_option = option
        if any(pat in folded_option for pat in _CONSENT_REJECT_PATTERNS):
            has_reject_option = True

    if not has_context_signal or not accept_option or not has_reject_option:
        return None

    return accept_option


def _matrix_row_labels(block: Dict[str, Any]) -> list[str]:
    """Retourne les libellés de lignes matrix si disponibles dans le contexte extracteur."""
    context = block.get("context") or {}
    rows = context.get("matrix_rows") if isinstance(context, dict) else None
    if not isinstance(rows, list):
        return []
    return [_escape(str(r)) for r in rows if _norm(str(r))]


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _norm_folded_lc(str(value)) in {"1", "true", "yes", "on"}


def _is_numeric_or_ordinal_label(value: str) -> bool:
    label = _norm_folded_lc(value)
    if not label:
        return False
    return bool(re.fullmatch(r"\d+(?:er|e|eme|eme|st|nd|rd|th|º|°|ª)?", label))


def _is_encuesta_ranking_matrix(block: Dict[str, Any]) -> bool:
    if _norm_folded_lc(block.get("itype")) != "matrix":
        return False
    context = block.get("context") if isinstance(block.get("context"), dict) else {}
    if not _is_truthy((context or {}).get("encuesta_matrix")):
        return False
    rows = (context or {}).get("matrix_rows")
    cols = (context or {}).get("matrix_columns")
    if not isinstance(rows, list) or not isinstance(cols, list):
        return False
    row_labels = [_norm(str(r)) for r in rows if _norm(str(r))]
    col_labels = [_norm(str(c)) for c in cols if _norm(str(c))]
    if not row_labels or not col_labels:
        return False
    if len(col_labels) >= len(row_labels):
        return False
    return all(_is_numeric_or_ordinal_label(col) for col in col_labels)


def _is_cardsort_block(block: Dict[str, Any]) -> bool:
    if not isinstance(block, dict):
        return False
    kind = _norm_folded_lc(block.get("kind"))
    if kind != "cardsort":
        return False
    cards = block.get("cards")
    buckets = block.get("buckets")
    return isinstance(cards, list) and bool(cards) and isinstance(buckets, list) and bool(buckets)


def expand_question_blocks_for_batch(question_blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Déplie les matrices multi-lignes en entrées QID unitaires (1 QID par ligne matrix).

    Garde-fou DOM : on n'active ce comportement que pour les blocks matrix qui exposent
    explicitement context.matrix_rows et qui n'ont pas déjà une matrix_active_row.
    """
    expanded: List[Dict[str, Any]] = []
    for block in question_blocks or []:
        if not isinstance(block, dict):
            expanded.append(block)
            continue

        itype = _norm_folded_lc(block.get("itype"))
        context = block.get("context") if isinstance(block.get("context"), dict) else {}
        matrix_rows = context.get("matrix_rows") if isinstance(context, dict) else None
        active_row = _norm(str((context or {}).get("matrix_active_row", "")))

        is_encuesta_ranking_matrix = _is_encuesta_ranking_matrix(block)
        should_expand = (
            itype == "matrix"
            and isinstance(matrix_rows, list)
            and bool(matrix_rows)
            and not active_row
            and not is_encuesta_ranking_matrix
        )
        if not should_expand:
            expanded.append(block)
            continue

        for raw_row in matrix_rows:
            row_label = _norm(str(raw_row or ""))
            if not row_label:
                continue
            cloned = deepcopy(block)
            cloned_context = cloned.get("context") if isinstance(cloned.get("context"), dict) else {}
            cloned_context["matrix_active_row"] = row_label
            cloned["context"] = cloned_context
            cloned["max_select"] = 1
            expanded.append(cloned)

    return expanded


# =========================
# Construction du prompt
# =========================


# =========================
# System prompt (bloc fixe — prompt caching OpenAI)
# =========================

# Estimation conservative du nombre de tokens du system prompt.
# OpenAI met en cache automatiquement à partir de 1 024 tokens identiques en préfixe.
# Si cette valeur descend sous 1 024 après une modification des règles, le cache
# ne s'active plus. À vérifier via usage.prompt_tokens_details.cached_tokens > 0.
SYSTEM_PROMPT_TOKEN_ESTIMATE = 1100


def build_system_prompt() -> str:
    """
    Retourne le bloc de règles invariant destiné au message 'system'.

    Ce contenu est STRICTEMENT STATIQUE : aucune variable dynamique, aucun QID,
    aucune option de question. Cela garantit que la chaîne est byte-perfect identique
    entre tous les appels, ce qui active le prompt caching OpenAI dès le premier appel
    (cache hit automatique pour gpt-4o-mini à partir de 1 024 tokens de préfixe identique).

    Pour vérifier l'activation du cache : inspecter
    `usage.prompt_tokens_details.cached_tokens` dans la réponse API (valeur > 0 = cache hit).

    NE PAS modifier cette fonction sans mesurer l'impact : toute modification
    invalide le cache pour TOUS les bots jusqu'au prochain warm-up (~1 appel).
    """
    lines: list[str] = []

    lines.append(
        "Tu es un répondant ADULTE (25 ans). "
        "Tu vois ci-dessous TOUTES les questions présentes sur une page de survey.\n"
    )

    lines.append(
        "Tu dois répondre à CHAQUE question.\n"
        "Tu ne dois JAMAIS lister toutes les options.\n"
        "Tu dois proposer uniquement la/les réponse(s) nécessaires selon la règle de sélection.\n"
    )

    # ✅ FORMAT RENFORC: exigence explicite de "|" comme sparateur
    lines.append(
        "FORMAT STRICT (une ligne par question) :\n"
        "QID //// target_id //// valeur //// itype //// contexte\n\n"
        "RèGLES CRITIQUES:\n"
        "- Pour chaque QID, le nombre de valeurs à renvoyer est défini par la selection_rule de ce QID. Ne pas utiliser max_select comme cible à atteindre : c'est un plafond, pas une obligation.\n"
        "- Si plusieurs valeurs sont nécessaires, les séparer UNIQUEMENT par \"|\".\n"
        "- Exemple: Q1 //// group_abc //// Option A|Option B|Option C //// checkbox //// ...\n"
        "- NE JAMAIS utiliser la virgule \",\" comme séparateur (les options peuvent en contenir).\n"
        "- AUCUNE explication. Aucun texte hors format.\n"
    )

    lines.append(
        "RÈGLE CHAMP MULTI-CASES (context.kind=multi_text) :\n"
        "- Ce n'est PAS une multi-sélection checkbox: c'est un champ composé de plusieurs cases texte.\n"
        "- Si max_select >= 2, valeur DOIT contenir EXACTEMENT max_select segments séparés par \"|\".\n"
        "- Exemple DOB (3 cases): 03|02|2001\n"
    )

    lines.append(
        "RèGLE NOMBRE DE RéPONSES:\n"
        "- Ne déduis PAS le nombre depuis le provider/source.\n"
        "- Pour chaque QID, le nombre de valeurs à renvoyer est défini par la selection_rule de ce QID. Ne pas utiliser max_select comme cible à atteindre : c'est un plafond, pas une obligation.\n"
    )

    lines.append(
        "Champs ouverts (text/textarea) : si la question contient un exemple (ex: 'E.g.' / 'Ex:'), "
        "N'UTILISE PAS l'exemple comme valeur. Donne une valeur réaliste (ex: code postal FR -> 75001).\n"
    )

    lines.append(
        "Contraintes :\n"
        "- itype doit être l'un de : {radio, checkbox, dropdown, text, textarea, button}\n"
        "- valeur DOIT être une option existante (si options listées)\n"
        "- RÈGLE ABSOLUE : utilise UNIQUEMENT les options listées ci-dessus, mot pour mot. N'invente PAS d'option absente de la liste, même si elle te semble logiquement attendue.\n"
        "- évite : non, jamais, aucun, je préfère ne pas répondre\n"
        "- contexte doit correspondre exactement é  la question affichée\n"
    )

    lines.append(
        "RÈGLE SPÉCIALE MATRICES (itype=matrix) :\n"
        "- valeur DOIT être au format STRICT: row_label || col_label (ou row_label || col1|col2|col3 pour matrices checkbox multi-colonnes)\n"
        "- Si matrix_active_row est fourni dans le contexte, row_label DOIT être EXACTEMENT cette valeur (ne choisis jamais une autre ligne).\n"
        "- EXCEPTION matrix_active_row: quand matrix_active_row est fourni, valeur DOIT contenir UNIQUEMENT la/les colonne(s) (col_label ou col1|col2|col3), sans row_label.\n"
        "- Exemple attendu: Crédit consommation || Transféré vers Revolut\n"
        "- Exemple multi-colonnes (checkbox): Whey protéines || Amazon|Decathlon\n"
        "- Exemple matrix_active_row (colonne(s) seule(s)): En ligne, sur Amazon|En magasin, chez Decathlon\n"
        "- INTERDIT: répondre uniquement une colonne (ex: 'Transféré vers Revolut').\n"
    )

    # Contrainte sexe/genre : toujours masculin, avec l'intitulé exact de l'option
    lines.append(
        "Pour toute question sur le sexe ou le genre, "
        "choisis TOUJOURS l'option qui fait référence au genre masculin "
        "(Homme, Masculin, Male, Man, M, H, etc.) parmi les options proposées. "
        "Utilise EXACTEMENT l'intitulé tel qu'il apparaît dans la liste d'options. "
        "Évite systématiquement: X, Autre, Non-binaire, Préfère ne pas répondre.\n"
    )

    # Contrainte ge / anne de naissance
    lines.append(
        "Pour toute question sur l'ge ou la date de naissance:\n"
        "- Si la question demande explicitement l'ANNE de naissance (ex: 'anne de naissance', 'year of birth', 'birth year', 'born in', 'n en'), rponds UNIQUEMENT avec une anne sur 4 chiffres (YYYY), jamais un ge.\n"
        "- Si la question demande l'âge (âge, years old, ans, age) ET qu'elle propose des options fermées (tranches libellées comme '19-44 ans', '18-25', etc.) : choisis PARMI CES OPTIONS la tranche qui inclut 25, comme pour n'importe quelle question radio/dropdown. Ne réponds PAS avec le chiffre brut '25'.\n"
        "- Si la question demande l'âge (âge, years old, ans, age) ET qu'il n'y a PAS d'options fermées (saisie libre) : réponds UNIQUEMENT avec le chiffre '25', sans JAMAIS ajouter de mot ('ans', 'years', 'yo', etc.).\n"
        "- Si date de naissance avec dropdowns séparés (mois + année):\n"
        "  * Année: choisis une année cohérente pour 25 ans (2001, 2000, 1999).\n"
        "  * Mois: choisis un mois raliste (vite le mois en cours pour viter des ges < 25).\n"
        "  * Exemple: 'Juillet' pour le mois, '2000' pour l'année.\n"
    )

    # ── PERSONA FINANCIER ──────────────────────────────────────────────────
    lines.append(
        "PERSONA SOCIO-ÉCONOMIQUE :\n"
        "Tu incarnes un cadre supérieur ou dirigeant d'entreprise de 25 ans, "
        "résidant en zone urbaine (Paris). "
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
        "  Un répondant aisé n'hésite pas sur ces sujets.\n"
    )

    lines.append(
        "RÈGLE RÉSIDENCE (priorité absolue sur RÈGLE ABSOLUE DE PROGRESSION) :\n"
        "Pour toute question dont le texte ou les options correspondent à un pays, "
        "une région, ou une ville de résidence du répondant "
        "(signaux question : 'où habitez-vous', 'pays de résidence', 'country of residence', "
        "'where do you live', 'where do you reside', 'your country', 'votre pays', "
        "'région de résidence', 'ville de résidence', 'city of residence', 'city where you live', "
        "'lieu de résidence', 'place of residence', 'domicile', 'home country', 'pays où vous vivez' ; "
        "signaux options : liste de pays dont 'France', 'Germany', 'United Kingdom', 'États-Unis', etc.) :\n"
        "- Tu DOIS sélectionner l'option correspondant à la France (ou Paris pour les questions de ville) "
        "parmi les options proposées, en utilisant EXACTEMENT l'intitulé tel qu'il apparaît dans la liste.\n"
        "- Si 'France' n'est pas disponible mais qu'une option 'Europe' ou 'Europe occidentale' existe, "
        "choisis-la.\n"
        "- Cette règle s'applique indépendamment de la position de l'option dans la liste "
        "et indépendamment du contexte du survey.\n"
        "- INTERDIT : choisir un autre pays ou une autre ville au motif que c'est la première option, "
        "la plus fréquente, ou la plus 'favorable' selon la thématique du survey.\n"
    )

    lines.append(
        "RÈGLE CHIFFRE D'AFFAIRES / TAILLE D'ENTREPRISE :\n"
        "Si la question porte sur le chiffre d'affaires, les revenus annuels, "
        "le budget ou la taille de l'entreprise où travaille le répondant "
        "(signaux : 'chiffre d'affaires', 'CA', 'revenus', 'turnover', 'revenue', "
        "'annual revenue', 'company revenue', 'fatturato', 'Umsatz', 'taille', 'size') :\n"
        "- Choisis TOUJOURS une tranche élevée, cohérente avec un grand groupe ou "
        "une entreprise de taille significative (évite la tranche la plus basse et "
        "les tranches inférieures à 100 millions).\n"
        "- Si la tranche la plus haute semble extrême, choisis la DEUXIÈME en partant du haut.\n"
        "- INTERDIT ABSOLU : ne jamais choisir la tranche la plus basse ni 'Je ne sais pas'.\n"
    )

    lines.append(
        "RÈGLE POSSESSION DE BIENS :\n"
        "Pour toute question portant sur la détention, l'accès ou l'usage d'un bien matériel\n"
        "(signaux : 'voiture', 'véhicule', 'logement', 'appartement', 'maison', 'résidence',\n"
        "'équipement', 'appareil', 'télévision', 'téléphone', 'ordinateur', 'abonnement',\n"
        "ou toute question où les options présentent une hiérarchie possession > accès > location > absence),\n"
        "choisis TOUJOURS l'option qui affirme la possession directe du bien.\n"
        "Cette option peut être libellée 'Je possède', 'Propriétaire', 'J'en ai un', 'Oui, j'ai',\n"
        "ou tout équivalent sémantique indiquant une possession personnelle directe.\n"
        "Ne jamais choisir 'J'ai accès', 'Je loue', 'J'emprunte', 'Non, pas d'accès', ou toute option\n"
        "indiquant une possession indirecte ou une absence de possession.\n"
        "Un répondant déclarant ne pas posséder un bien est souvent exclu des études portant sur ce bien.\n"
    )
    
    lines.append(
        "RÈGLE WEBCAM :\n"
        "Si une question demande si tu acceptes, consens, ou es prêt à utiliser une webcam ou une caméra "
        "(pour être filmé, enregistré, ou participer via vidéo), "
        "choisis TOUJOURS l'option négative (Non, No, Je refuse, Je ne souhaite pas, etc.).\n"
        "Cette règle s'applique uniquement au consentement d'usage actif — "
        "pas aux questions de simple possession d'un équipement.\n"
    )

    lines.append(
        "RÈGLE JAMAIS / NEVER (question d'exclusion négative) :\n"
        "Si une question demande, parmi une liste d'options (marques, produits, services, catégories), "
        "lesquelles tu n'achèterais JAMAIS, n'utiliserais JAMAIS, n'envisagerais JAMAIS "
        "(signaux : 'jamais', 'never', 'n'envisageriez jamais', 'would never', 'n'achèteriez jamais', "
        "'ne consommeriez jamais', 'would never buy', 'would never use', 'would never consider'),\n"
        "ET que la liste d'options contient une option exclusive négative du type "
        "'Aucune de ces marques', 'Aucune de ces options', 'Aucune', 'None of these', 'None of the above',\n"
        "alors tu DOIS choisir UNIQUEMENT cette option exclusive négative.\n"
        "C'est la réponse la plus favorable pour un profil actif et consommateur.\n"
    )

    lines.append(
        "RÈGLE COHÉRENCE CONTEXTE → APPROFONDISSEMENT :\n"
        "Avant de répondre, consulte systématiquement le [Survey context] (Summary + Recent Q&A).\n"
        "Si une question vise à approfondir ou préciser une information déjà établie dans le contexte "
        "(ex : marque, produit, lieu, habitude, événement, date, comportement, situation personnelle…), "
        "tu DOIS utiliser cette information comme point de départ pour choisir dans la liste.\n"
        "En cas d'ambiguïté entre plusieurs options compatibles : préfère l'option la plus récente, "
        "la plus précise, ou la plus cohérente avec le persona (cadre supérieur, urbain, revenus élevés).\n"
        "INTERDIT ABSOLU : choisir une option vague ou de repli ('Autre', 'Other', 'Je ne sais pas', "
        "'Non concerné', etc.) quand une option concrète de la liste est compatible avec le contexte établi.\n"
    )

    lines.append(
        "RÈGLE OPTION 'AUTRE' / 'OTHER' (priorité haute) :\n"
        "Dans une liste de choix (checkbox, radio, dropdown), ne JAMAIS sélectionner une option libellée "
        "'Autre', 'Autres', 'Other', 'Others', 'Autre (précisez)', 'Other (please specify)', "
        "'Autre chose', 'Something else', ou tout équivalent demandant une saisie libre complémentaire, "
        "SAUF si cette option apparaît dans une matrice ou un tableau (itype=matrix).\n\n"
        "Raison : sélectionner 'Autre' déclenche systématiquement un champ texte ouvert impossible à remplir "
        "automatiquement, ce qui bloque la progression du survey.\n\n"
        "RÈGLE DE SUBSTITUTION : si 'Autre' était la seule option pertinente selon le persona, "
        "choisis à la place l'option concrète la plus proche dans la liste. "
        "Si aucune option concrète n'est adaptée, choisis l'option la plus neutre ou la plus générique "
        "(hors options exclusives de type 'Aucun' / 'None of the above').\n"
        "EXCEPTION : cette règle ne s'applique PAS quand 'Autre' est dans une matrice (itype=matrix). "
        "Dans ce cas, 'Autre' est une colonne standard, pas un déclencheur de champ libre.\n"
    )

    lines.append(
        "RÈGLE ABSOLUE DE PROGRESSION (priorité maximale sur le persona) :\n"
        "L'objectif premier est de progresser dans le survey. Le persona est un point de départ — il doit être adapté si une réponse trop fidèle entraînerait une disqualification évidente.\n\n"
        "Pour toute question où les options décrivent une gradation (fréquence, intensité, quantité, niveau d'usage, degré d'implication), raisonne en trois étapes :\n"
        "1. Quelle est la thématique centrale de ce survey ? (déduis-la du contexte fourni ou des questions précédentes)\n"
        "2. Quelle réponse est cohérente avec un répondant actif, engagé, éligible pour ce sujet ?\n"
        "3. Sélectionne cette réponse, même si elle s'écarte du persona de base.\n\n"
        "CAS PARTICULIER — Contexte absent ou insuffisant (ex : première page du survey) :\n"
        "Si le contexte ne permet pas encore de déduire la thématique du survey, applique ces priorités dans l'ordre :\n"
        "- Pour une question de fréquence : choisis l'option la plus haute disponible (ex : \"Tous les jours\", \"Daily\", \"Chaque jour\").\n"
        "- Pour une question d'usage ou de possession : affirme l'usage ou la possession.\n"
        "- Pour une question d'intention ou de projet futur (signaux : \"intention\", \"prévoyez\", \"envisagez\", \"comptez\", \"planifiez\", \"dans les X mois\", \"prochainement\", ou options présentant une gradation temporelle d'intention du type immédiat > moyen terme > long terme > jamais/non) : choisis TOUJOURS l'option indiquant l'horizon le plus proche (ex : \"dans moins de 3 mois\", \"très bientôt\"). Ne jamais choisir \"Non\", \"Jamais\" ou toute option niant l'intention.\n"
        "- Pour toute autre gradation : choisis l'option la plus engagée ou favorable.\n"
        "Principe général : en l'absence de contexte, une réponse favorable est presque toujours plus sûre qu'une réponse restrictive.\n\n"
        "Ces exemples illustrent ce raisonnement — ils ne constituent PAS des règles fixes :\n"
        "- Survey sur la conduite automobile → préfère une fréquence de conduite élevée (ex : \"Tous les jours\").\n"
        "- Survey sur un produit tech → préfère un usage régulier de ce type de produit.\n"
        "- Survey sur les voyages → préfère une fréquence de voyage élevée.\n\n"
        "Sur une liste longue ou une matrice avec plusieurs comportements/produits :\n"
        "- N'applique PAS mécaniquement la fréquence la plus haute à chaque ligne.\n"
        "- Adapte chaque ligne en fonction de sa pertinence avec le sujet du survey.\n"
        "- Vise une cohérence globale crédible : un répondant très actif sur le sujet principal, avec des usages secondaires réalistes.\n\n"
        "INTERDIT dans tous les cas :\n"
        "- Choisir \"Jamais\", \"Rarement\", \"Presque jamais\", \"Aucune fois\" pour le comportement central du survey, sauf contexte explicite contraire.\n"
    )    
    
    lines.append(
        "RÈGLE OPTIONS EXCLUSIVES :\n"
        "Une option exclusive est une option qui, par nature, annule toutes les autres "
        "(exemples : 'Aucun', 'Aucune', 'Aucune de ces propositions', 'Aucun de ces sujets', "
        "'None', 'None of the above', 'Je ne sais pas', 'NSP', 'N/A', 'Pas applicable', "
        "'Préfère ne pas répondre', 'Don't know', 'Not applicable').\n\n"
        "RÈGLE GÉNÉRALE : Ne retourne JAMAIS une option exclusive. "
        "Ces options sont disqualificatoires dans la grande majorité des cas.\n\n"
        "EXCEPTION — Screener de secteur d'activité (liste courte) :\n"
        "Si la question demande si toi-même, ou un membre de ton foyer/famille/entourage,"
        "travailles dans l'un des domaines/secteurs/industries d'une liste proposée\n"
        "(signaux : 'travaillez', 'travaille', 'travaillez-vous', 'work in', 'employed in',"
        "'secteur', 'domaine', 'industrie', 'industry', 'field', 'profession'),\n"
        f"ET que la liste d'options contient au total MOINS DE {_SECTOR_SCREENER_MAX_OPTIONS} options (options exclusives comprises),"
        "ET que la liste contient une option exclusive négative\n"
        "('Aucune de ces propositions', 'None of the above', 'Aucun', 'Aucune', 'Non',"
        "'Aucune de ces réponses', 'None of these'),\n"
        "alors tu DOIS choisir UNIQUEMENT cette option exclusive négative, sans aucune autre valeur.\n"
        "Ce type de question est un screener anti-industrie : choisir n'importe quel secteur de la liste "
        "entraîne une disqualification immédiate, même si ce secteur est cohérent avec le persona.\n"
        "Cette règle ne s'applique que si la question concerne le répondant seul OU son foyer/famille/entourage.\n"
        "Cette exception PRÉVAUT sur le persona et sur la RÈGLE ABSOLUE DE PROGRESSION.\n"
        "Un persona Finance/Conseil n'empêche PAS d'appliquer cette règle :"
        " la disqualification est immédiate si tu choisis un secteur de la liste.\n"
    )

    lines.append(
    "RÈGLE SÉLECTION MINIMALE :\n"
    "Si le texte de la question indique explicitement un nombre minimum ou exact de réponses attendues "
    "(signaux : 'choisissez X', 'sélectionnez X', 'les X éléments', 'choose X', 'select X', 'pick X', "
    "'au moins X', 'at least X', 'exactly X', 'X options', 'X réponses'), "
    "tu DOIS sélectionner AU MINIMUM ce nombre de valeurs, séparées par |.\n"
    "Ne jamais renvoyer moins de valeurs que le nombre indiqué dans la question.\n"
    )
    
    lines.append(
    "RÈGLE TABLEAU RADIO HOMOGÈNE (distribution réaliste) :\n"
    "Quand ce batch contient 8 questions radio ou plus qui partagent toutes le même jeu d'options "
    "(ex : une grille d'expérience produit / genre de jeu / comportement avec des options du type "
    "\"actif / ancien / jamais\" ou \"oui / parfois / non\"), tu DOIS distribuer les réponses de "
    "façon réaliste et variée. Un répondant humain ne pratique pas activement 30 activités en même temps.\n\n"
    "Règles de distribution à respecter IMPÉRATIVEMENT dans ce cas :\n"
    "- Option la plus active : attribuée à 40-65 % des lignes.\n"
    "- Option intermédiaire : attribuée à 15-30 % des lignes.\n"
    "- Option la plus passive : attribuée au reste des lignes.\n"
    "- INTERDIT : répondre la même valeur pour toutes les lignes sans exception.\n"
    "- Varie les choix de manière imprévisible (ni alternance régulière, ni bloc uniforme).\n\n"
    "PRIORITÉ : Cette règle est subordonnée aux règles suivantes qui s'appliquent TOUJOURS EN PREMIER "
    "sur leurs lignes respectives : SURVEY_CONSENT_ACCEPT, RÈGLE SECTEUR, RÈGLE WEBCAM, RÈGLE JAMAIS / NEVER.\n"
    "Cette règle ne s'applique PAS aux questions radio unitaires, aux checkboxes, aux matrices, "
    "ni aux screeners disqualificatoires.\n"
    )

    return "\n".join(lines)


def build_batch_prompt(question_blocks: list[dict], ctx=None) -> str:
    """
    Construit un prompt OpenAI pour répondre é  TOUTES les questions en une fois.
    Format de sortie robuste avec QID + max_select + target_id.
    
    IMPORTANT: Pour les multi-select, le séparateur OBLIGATOIRE est "|".
    """
    lines: list[str] = []
    question_blocks = expand_question_blocks_for_batch(question_blocks)

    # Inject survey session context if available (coherence across pages)
    if ctx is not None:
        try:
            snippet = ctx.get_context_snippet()
            if snippet:
                lines.append(snippet)
                lines.append("")  # blank separator
        except Exception:
            pass

    # Règles fixes → déportées dans build_system_prompt() pour le prompt caching OpenAI.
    # Passer build_system_prompt() comme message 'system' dans l'appel API.
    lines.append("\n--- QUESTIONS ---")

    for i, block in enumerate(question_blocks or [], start=1):
        qid = f"Q{i}"
        q = _escape(block.get("question", ""))
        is_cardsort = _is_cardsort_block(block)
        itype = "cardsort" if is_cardsort else _escape(block.get("itype", ""))
        ctx = block.get("context") if isinstance(block.get("context"), dict) else {}
        matrix_active_row_raw = (ctx or {}).get("matrix_active_row", "")
        matrix_columns = (ctx or {}).get("matrix_columns")
        use_matrix_columns_for_active_row = (
            _norm_folded_lc(block.get("itype")) == "matrix"
            and bool(_norm(matrix_active_row_raw))
            and isinstance(matrix_columns, list)
            and bool(matrix_columns)
        )
        options_source = matrix_columns if use_matrix_columns_for_active_row else (block.get("options") or [])
        cards = [_escape(str(c)) for c in (block.get("cards") or []) if _norm(str(c))] if is_cardsort else []
        buckets = [_escape(str(b)) for b in (block.get("buckets") or []) if _norm(str(b))] if is_cardsort else []
        opts = [_escape(o) for o in options_source if o]
        max_sel = _selection_max_for_prompt(block)
        try:
            matrix_max_sel = int(block.get("max_select", 1) or 1)
        except Exception:
            matrix_max_sel = 1
        matrix_max_sel = max(1, matrix_max_sel)
        target_id = _escape(block.get("target_id", ""))
        matrix_rows = _matrix_row_labels(block)
        matrix_active_row = _escape(matrix_active_row_raw)

        lines.append(f"\n{qid}")
        lines.append(f"target_id: {target_id}")
        lines.append(f"contexte: {q}")
        if ctx.get("confirmit_cf_numeric_list") and ctx.get("multi_sum_total"):
            group_q = _escape(str(ctx.get("group_question", "") or ""))
            if group_q and group_q != q:
                lines.append(f"groupe_contexte: {group_q}")
            lines.append(
                f"contrainte_somme: Ce champ fait partie d'une répartition en pourcentage."
                f" La somme de TOUTES les valeurs du groupe doit être exactement {int(ctx['multi_sum_total'])}."
                " Choisis une valeur entière cohérente avec les autres lignes du groupe."
            )
        if ctx.get("decipher_table_text_rows") is True:
            row_label = _escape(str(ctx.get("row_label", "")))
            if row_label:
                lines.append(f"row_label: {row_label}")
        if matrix_rows:
            lines.append(f"sous_questions_matrix: {' | '.join(matrix_rows)}")
            lines.append(
                "matrix_answer_format: row_label || col_label (single) ; row_label || col1|col2|col3 (matrix checkbox multi-colonnes ; row obligatoire, jamais col seule)"
            )
        if matrix_active_row:
            lines.append(f"matrix_active_row: {matrix_active_row}")
            lines.append("matrix_rule_active_row: row_label DOIT être EXACTEMENT matrix_active_row")
            lines.append("matrix_active_row_value_rule: valeur DOIT contenir UNIQUEMENT la/les colonne(s), sans row_label")
            lines.append("matrix_example_active_row: Transféré vers Revolut")
        lines.append(f"itype: {itype}")
        display_max_sel = min(max_sel, 5) if max_sel > 3 else max_sel
        lines.append(f"max_select: {display_max_sel}")
        is_multi_text = (
            itype in {"text", "textarea", "number"}
            and max_sel >= 2
            and (
                str(block.get("target_id") or "").startswith("multi_")
                or str((ctx or {}).get("kind") or "") == "multi_text"
            )
        )
        if is_cardsort:
            lines.append(
                "selection_rule: Pour QID={qid}, renvoyer EXACTEMENT une affectation par carte au format "
                "card_label => bucket1|bucket2 ; card_label => bucketX. "
                "Les affectations sont séparées par \" ; \". Les bucket labels doivent exister dans buckets_cardsort."
                .format(qid=qid)
            )
            lines.append(
                "cardsort_rule: utiliser EXACTEMENT les labels de cartes et buckets fournis; "
                "1 ligne QID unique avec toutes les affectations."
            )
        elif itype == "matrix" and matrix_active_row:
            lines.append(
                f"selection_rule: Pour QID={qid}, renvoyer entre 1 et {matrix_max_sel} valeur(s) colonne(s) séparée(s) par | pour matrix_active_row. Ne renvoie jamais row_label dans valeur."
            )
        elif itype == "matrix" and matrix_rows:
            if _is_encuesta_ranking_matrix(block):
                rank_count = len([c for c in (ctx or {}).get("matrix_columns", []) if _norm(str(c))])
                lines.append(
                    f"selection_rule: Pour QID={qid}, renvoyer EXACTEMENT {rank_count} paires row_label || col_label séparées par | (ex: Ligne A || 1|Ligne B || 2)."
                )
                lines.append(
                    "selection_rule_matrix_ranking: Chaque col_label (rang) doit être utilisé UNE SEULE FOIS, et chaque row_label doit être différente (une seule attribution par ligne)."
                )
            else:
                row_count = len(matrix_rows)
                lines.append(
                    f"selection_rule: Pour QID={qid}, renvoyer EXACTEMENT {row_count} valeur(s), "
                    "une par ligne de sous_questions_matrix, au format STRICT row_label || col_label, "
                    "séparées par |."
                )
                _exc_cols = (ctx or {}).get("exclusive_columns") if isinstance(ctx, dict) else None
                if _exc_cols and isinstance(_exc_cols, list):
                    for _exc in _exc_cols:
                        lines.append(
                            f"exclusive_column_rule: La colonne \"{_exc}\" n'accepte qu'UNE SEULE marque parmi toutes les lignes. "
                            f"Exactement 1 ligne doit avoir col_label=\"{_exc}\", toutes les autres doivent utiliser une colonne différente."
                        )
        elif itype == "checkbox":
            if _is_truthy((ctx or {}).get("cap_hard")):
                lines.append(
                    f"selection_rule: Pour QID={qid}, renvoyer EXACTEMENT {max_sel} valeur(s) séparée(s) par | (obligatoire, pas un plafond). / For QID={qid}, return EXACTLY {max_sel} values separated by |."
                )
        else:
            if is_multi_text:
                lines.append(
                    f"CHAMP MULTI-CASES: fournir EXACTEMENT {max_sel} valeurs séparées par | "
                    f"(ex pour marques de sport: Nike|Adidas|Puma|Reebok|Under Armour)"
                )
                lines.append(
                    f"selection_rule: Pour QID={qid}, renvoyer EXACTEMENT {max_sel} valeurs "
                    f"séparées par |. Pas de répétition. Valeurs différentes obligatoires."
                )
            else:
                lines.append(
                    f"selection_rule: Pour QID={qid}, renvoyer EXACTEMENT 1 valeur"
                )
        if (forced_consent := _preferred_survey_consent_option(block, opts)) or \
                (forced_consent := _preferred_participation_willingness_option(block, opts)):
            lines.append(f"selection_rule: SURVEY_CONSENT_ACCEPT strict -> répondre EXACTEMENT avec '{forced_consent}'")
            lines.append(f"allowed_values_strict: {forced_consent}")
            lines.append("instruction_stricte: Consentement de participation/confidentialité détecté. Tu dois choisir l'option d'acceptation et jamais l'option de refus.")
        elif bool((ctx or {}).get("consent_modal_radio")) and opts:
            forced_consent = _preferred_consent_option(opts)
            if forced_consent:
                lines.append(f"selection_rule: CONSENT_ACCEPT strict -> répondre EXACTEMENT avec '{forced_consent}'")
                lines.append(f"allowed_values_strict: {forced_consent}")
                lines.append("instruction_stricte: Consent modal. Tu dois choisir l'option de consentement positive et jamais l'option de refus.")
            else:
                lines.append("selection_rule: choisir une option valide de la liste")

        if is_cardsort:
            lines.append("cards_cardsort: " + " | ".join(cards))
            lines.append("buckets_cardsort: " + " | ".join(buckets))
            lines.append("options: (cardsort_mapping_attendu)")
        elif opts:
            lines.append("options: " + " | ".join(opts))
        else:
            lines.append("options: (champ ouvert)")

    #RAPPEL FINAL: séparateur "|" obligatoire
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
            if tail in ("", ">", ">>", "»", "»>", ":", "-", "–", "→", "➡"):
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

        # Normalisation: certains extracteurs renvoient "select" ou "select_rps"
        # On les traite comme "dropdown" (itype connu de GPT) pour éviter les hallucinations.
        # L'entrée DOM_REGISTRY conserve itype="select_rps" et rps_select=True : le dispatcher
        # détecte le widget Angular via reg_itype, indépendamment de ce que GPT retourne.
        if it_lc in ("select", "select_rps"):
            it_lc = "dropdown"
            if isinstance(qb, dict):
                qb["itype"] = "dropdown"

        # Normalisation: "number" → "text" (champ numérique, traité comme saisie libre par GPT).
        # Le dispatcher lit itype depuis DOM_REGISTRY, pas depuis ce bloc : pas d'impact aval.
        if it_lc == "number":
            it_lc = "text"
            if isinstance(qb, dict):
                qb["itype"] = "text"

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

        # support cardsort (itype absent sur certains extracteurs, piloté par kind + cards/buckets)
        if _is_cardsort_block(qb):
            kept.append(qb)
            continue

        # types acceptés
        if it_lc in {"radio", "checkbox", "dropdown", "text", "textarea", "matrix_rows_single_choice", "matrix", "select_rps"}:
            kept.append(qb)

    return kept