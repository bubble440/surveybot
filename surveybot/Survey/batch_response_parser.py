# Survey/batch_response_parser.py
"""
Parse les réponses OpenAI batch.

Format attendu (préféré):
QID //// target_id //// valeur //// itype //// contexte

Fallback accepté:
valeur //// itype //// contexte

IMPORTANT - SÉPARATEUR MULTI-SELECT:
Pour les réponses multi (checkbox max_select > 1), le SEUL séparateur accepté est "|".
Le split sur "," a été supprimé car il cassait les options contenant des virgules internes
(ex: "Les médias, comme la télévision, la radio, la presse...").

FILTRAGE OPTIONS EXCLUSIVES:
Quand OpenAI retourne plusieurs valeurs pour une checkbox, certaines combinaisons
sont logiquement impossibles (ex: "Aucune de ces réponses" + autres options).
La fonction filter_exclusive_conflicts() élimine ces conflits AVANT exécution.
"""

from __future__ import annotations
import re, datetime
import difflib
import os
import math
import unicodedata
from typing import Dict, Optional, List, Any
from .log_utils import log_debug, log_info
from .dom_selection_rules import is_sector_activity_question
_ALLOWED_ITYPES = {"radio", "checkbox", "dropdown", "text", "textarea", "button", "number", "matrix", "cardsort"}
_QID_RE = re.compile(r"\bQ\d+\b", re.IGNORECASE)
_OTHER_SPECIFY_TEXT_RE = re.compile(r"autre.*pr[eé]cis|other.*specify", re.IGNORECASE)
_NEGATIVE_FREQ_RE = re.compile(r"jamais|never", re.IGNORECASE)


# =============================================================================
# DÉTECTION OPTIONS EXCLUSIVES
# =============================================================================

# Patterns d'options qui sont mutuellement exclusives avec les autres
# Ces options désactivent normalement toutes les autres sélections
_EXCLUSIVE_PATTERNS_FR = (
    r"^aucun(e)?(\s|$)",                    # "Aucun", "Aucune", "Aucune de ces..."
    r"aucun(e)?\s+(de\s+)?(ces|ceux|celles)",  # "Aucune de ces activités"
    r"^autre(\s|$|[^s])",                   # "Autre" mais pas "Autres"
    r"^pas\s+(de|d')",                      # "Pas de...", "Pas d'..."
    r"^je\s+ne\s+sais\s+pas(\s|$)",         # "Je ne sais pas"
    r"^ne\s+sai(?:s|t)\s+pas(\s|$)",              # "Ne sait pas", "Ne sais pas"
    r"^nsp(\s|$)",                           # NSP (Ne Sais Pas)
    r"^n/?a$",                              # N/A
    r"^sans\s+(avis|opinion|réponse)",      # "Sans avis", "Sans opinion"
    r"^refus",                              # "Refus", "Refuse"
    r"^préfère\s+ne\s+pas",                 # "Préfère ne pas répondre"
    r"^je\s+préfère\s+ne\s+pas",            # "Je préfère ne pas répondre"
    r"^pas\s+applicable",                   # "Pas applicable"
    r"^non\s+applicable",                   # "Non applicable"
    r"^non\s+concern[ée]",                  # "Non concerné(e)"
)

_EXCLUSIVE_PATTERNS_EN = (
    r"^none(\s|$)",                         # "None", "None of the above"
    r"^none\s+of\s+(the|these|those)",      # "None of the above", "None of these"
    r"^other(\s|$)",                        # "Other" seul
    r"^i\s+don'?t\s+know(\s|$)",            # "I don't know"
    r"^don'?t\s+know(\s|$)",                # "Don't know"
    r"^dk(\s|$)",                            # DK (Don't know)
    r"^not\s+applicable",                   # "Not applicable"
    r"^n/?a$",                              # N/A
    r"^prefer\s+not\s+to",                  # "Prefer not to say"
    r"^i\s+prefer\s+not",                   # "I prefer not to..."
    r"^refuse",                             # "Refuse"
    r"^not\s+sure(\s|$)",                   # "Not sure"
    r"^unsure(\s|$)",                        # "Unsure"
    r"^no\s+opinion",                       # "No opinion"
)

# Compilation des patterns pour performance
_EXCLUSIVE_REGEX_FR = [re.compile(p, re.IGNORECASE) for p in _EXCLUSIVE_PATTERNS_FR]
_EXCLUSIVE_REGEX_EN = [re.compile(p, re.IGNORECASE) for p in _EXCLUSIVE_PATTERNS_EN]


def _is_exclusive_value(value: str) -> bool:
    """
    Détecte si une valeur est une option "exclusive" (mutuellement exclusive avec les autres).
    
    Ces options, quand sélectionnées, devraient normalement désélectionner toutes les autres.
    Exemples: "Aucune de ces réponses", "None of the above", "Autre", "Je ne sais pas"
    
    Returns:
        True si l'option est de type exclusive
    """
    if not value:
        return False
    
    v = value.strip()
    if not v:
        return False
    
    # Normalisation pour matching
    v_norm = re.sub(r"\s+", " ", v).strip()
    
    # Test patterns français
    for regex in _EXCLUSIVE_REGEX_FR:
        if regex.search(v_norm):
            return True
    
    # Test patterns anglais
    for regex in _EXCLUSIVE_REGEX_EN:
        if regex.search(v_norm):
            return True
    
    return False


def filter_exclusive_conflicts(actions: list, qid_meta: dict | None = None) -> list:
    """
    Filtre les combinaisons incompatibles d'options exclusives.
    
    LOGIQUE EN 3 PHASES:
    
    Phase 1 - Détection des hallucinations OpenAI:
        Si le "contexte" d'une action exclusive correspond à une VALEUR d'une autre action,
        c'est une hallucination (OpenAI a inventé une fausse question).
        → Supprimer l'action hallucination
    
    Phase 2 - Filtrage par QID:
        Pour un même QID checkbox, si exclusives + régulières coexistent:
        → Garder seulement les régulières
    
    Phase 3 - Filtrage global checkbox:
        Si des exclusives (radio/checkbox) coexistent avec des régulières checkbox
        sur la page entière (QIDs différents), c'est souvent un conflit
        (ex: DOM extrait 2 groupes pour 1 question visuelle)
        → Supprimer les exclusives si elles sont minoritaires
    
    Returns:
        Liste d'actions filtrées sans conflits d'exclusivité
    """
    if not actions:
        return actions
    
    qid_meta = qid_meta or {}
    
    # ==========================================================================
    # PHASE 1: Détecter et supprimer les hallucinations OpenAI
    # ==========================================================================
    # Une hallucination = action exclusive dont le "contexte" est en fait
    # une VALEUR d'une autre action (OpenAI confond question et option)
    
    # Collecter toutes les valeurs non-exclusives (normalisées)
    all_regular_values: set = set()
    for a in actions:
        if not isinstance(a, dict):
            continue
        val = (a.get("value") or "").strip()
        if val and not _is_exclusive_value(val):
            all_regular_values.add(val.lower())
    
    # Filtrer les hallucinations
    phase1_filtered: list = []
    for a in actions:
        if not isinstance(a, dict):
            phase1_filtered.append(a)
            continue
        
        val = a.get("value") or ""
        ctx = (a.get("context") or "").strip()
        qid = a.get("qid") or ""
        
        # Si c'est une exclusive ET son contexte = une valeur d'autre action
        if _is_exclusive_value(val) and ctx:
            ctx_lower = ctx.lower()
            if ctx_lower in all_regular_values:
                print(f"[batch_response_parser] HALLUCINATION DETECTED {qid}: "
                      f"exclusive '{val}' has context '{ctx}' which is a VALUE of another action -> REMOVED")
                continue
        
        phase1_filtered.append(a)
    
    # ==========================================================================
    # PHASE 2: Filtrage par QID (même question)
    # ==========================================================================
    by_qid: Dict[str, list] = {}
    no_qid: list = []
    
    for a in phase1_filtered:
        qid = (a.get("qid") or "").strip().upper() if isinstance(a, dict) else ""
        if qid:
            by_qid.setdefault(qid, []).append(a)
        else:
            no_qid.append(a)
    
    phase2_filtered: list = []
    
    for qid, group in by_qid.items():
        if len(group) <= 1:
            phase2_filtered.extend(group)
            continue
        
        # Vérifier si checkbox présent
        itypes = {(a.get("itype") or "").lower() for a in group if isinstance(a, dict)}
        if "checkbox" not in itypes:
            phase2_filtered.extend(group)
            continue
        
        exclusives = [a for a in group if isinstance(a, dict) and _is_exclusive_value(a.get("value") or "")]
        regulars = [a for a in group if a not in exclusives]
        
        if exclusives and regulars:
            print(f"[batch_response_parser] QID CONFLICT {qid}: "
                  f"exclusive={[e.get('value') for e in exclusives]} vs "
                  f"regular={[r.get('value') for r in regulars]} -> keeping regular only")
            phase2_filtered.extend(regulars)
        else:
            phase2_filtered.extend(group)
    
    phase2_filtered.extend(no_qid)
    
    # ==========================================================================
    # PHASE 3: Filtrage global (conflits cross-QID pour même question visuelle)
    # ==========================================================================
    # Cas: le DOM extrait 2 groupes (2 QIDs) pour 1 seule question visuelle
    # Ex: checkboxes normales = Q1, radio "Aucune" = Q2
    # 
    # On ne filtre QUE si les contextes sont similaires (même question)
    # Pour éviter de supprimer des exclusives légitimes sur des questions distinctes
    
    # Identifier les exclusives et régulières de type radio/checkbox
    global_exclusives: list = []
    global_checkbox_regulars: list = []
    others: list = []
    
    for a in phase2_filtered:
        if not isinstance(a, dict):
            others.append(a)
            continue
        
        itype = (a.get("itype") or "").lower()
        val = a.get("value") or ""
        
        if itype in ("radio", "checkbox"):
            if _is_exclusive_value(val):
                global_exclusives.append(a)
            else:
                global_checkbox_regulars.append(a)
        else:
            others.append(a)
    
    # S'il y a des exclusives ET des régulières, vérifier si mêmes questions
    if global_exclusives and global_checkbox_regulars:
        # Collecter les contextes normalisés des régulières
        regular_contexts = set()
        for a in global_checkbox_regulars:
            ctx = (a.get("context") or "").strip().lower()
            if ctx:
                # Normaliser: premiers 50 caractères pour tolérer variations mineures
                regular_contexts.add(ctx[:50])
        
        # Identifier les exclusives qui partagent un contexte similaire
        exclusives_to_remove = []
        for e in global_exclusives:
            e_ctx = (e.get("context") or "").strip().lower()
            e_ctx_prefix = e_ctx[:50] if e_ctx else ""
            
            # Vérifier si le contexte de l'exclusive est similaire à celui d'une régulière
            ctx_match = False
            if e_ctx_prefix and e_ctx_prefix in regular_contexts:
                ctx_match = True
            else:
                # Fallback: vérifier si le contexte contient des mots-clés communs
                for rc in regular_contexts:
                    # Si >60% des mots sont communs, considérer comme même question
                    e_words = set(e_ctx.split())
                    r_words = set(rc.split())
                    if e_words and r_words:
                        common = len(e_words & r_words)
                        total = min(len(e_words), len(r_words))
                        if total > 0 and (common / total) > 0.6:
                            ctx_match = True
                            break
            
            if ctx_match:
                exclusives_to_remove.append(e)
        
        if exclusives_to_remove:
            print(f"[batch_response_parser] GLOBAL CONFLICT (same question): "
                  f"{len(exclusives_to_remove)} exclusive(s) vs "
                  f"{len(global_checkbox_regulars)} regular(s) -> removing exclusives")
            for e in exclusives_to_remove:
                print(f"  [REMOVED] {e.get('qid')}: '{e.get('value')}'")
            
            # Retourner tout sauf les exclusives à supprimer
            return [a for a in phase2_filtered if a not in exclusives_to_remove]
    
    # Pas de conflit global
    return phase2_filtered


def _split_values(value: str, itype: str = "", max_select: int = 1) -> List[str]:
    """
    Supporte les réponses multi *uniquement via le séparateur "|"*.

    Règles (prévisibles et sûres):
    - Split UNIQUEMENT sur "|" (format recommandé et obligatoire)
    - PAS de split sur "," car les options contiennent souvent des virgules internes
      (ex: "Les médias, comme la télévision, la radio, la presse, les journaux...")
    
    Historique du bug corrigé:
    - Avant: split sur "," si checkbox + max_select > 1 + len < 120
    - Problème: options avec virgules internes étaient fragmentées
    - Solution: supprimer le split sur virgule, exiger "|" dans le prompt
    """
    if not value:
        return []

    v = value.strip()

    # Split UNIQUEMENT sur | (séparateur explicite et non-ambigu)
    if "|" in v:
        return [p.strip() for p in v.split("|") if p.strip()]

    # ⚠️ SUPPRIMÉ: split sur virgule (causait le bug des options fragmentées)
    # L'ancien code faisait:
    # if it == "checkbox" and (max_select or 1) > 1 and "," in v and len(v) < 120:
    #     return [p.strip() for p in v.split(",") if p.strip()]
    # Ceci cassait les options comme "Les médias, comme la télévision..."

    return [v]


_MONTHS_EN = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}

_MONTHS_FR = {
    "janvier": 1,
    "jan": 1,
    "fevrier": 2,
    "février": 2,
    "mars": 3,
    "avril": 4,
    "avr": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "juil": 7,
    "aout": 8,
    "août": 8,
    "septembre": 9,
    "sep": 9,
    "sept": 9,
    "octobre": 10,
    "oct": 10,
    "novembre": 11,
    "nov": 11,
    "decembre": 12,
    "décembre": 12,
    "dec": 12,
}


def _debug_enabled() -> bool:
    lvl = (os.getenv("LOG_LEVEL") or "").strip().lower()
    return lvl == "debug"


def _debug_log(msg: str) -> None:
    if _debug_enabled():
        log_debug("batch_response_parser", msg)


def _warn_log(msg: str) -> None:
    log_info("batch_response_parser", f"[warn] {msg}")


_BIRTH_YEAR_HINTS = (
    "annee de naissance",
    "année de naissance",
    "year of birth",
    "birth year",
    "born in",
    "né en",
    "nee en",
)


# def _is_birth_year_question_text(*texts: str) -> bool:
#     haystack = " ".join(_fold_lc(t) for t in texts if t)
#     if not haystack:
#         return False
#     return any(_fold_lc(hint) in haystack for hint in _BIRTH_YEAR_HINTS)


def _selection_bounds_for_qid(qid: str, raw_max: int, qmeta: dict | None, itype_hint: str = "") -> tuple[int, int]:
    """Retourne (min_select, max_select) bornés de manière prévisible."""
    max_select = max(1, int(raw_max or 1))
    qmeta = qmeta if isinstance(qmeta, dict) else {}
    itype = str((qmeta.get("itype") or itype_hint or "")).strip().lower()

    context = qmeta.get("context") if isinstance(qmeta.get("context"), dict) else {}
    is_multi_text = (
        itype in {"text", "textarea", "number"}
        and max_select >= 2
        and (str(qmeta.get("target_id") or "").startswith("multi_") or str((context or {}).get("kind") or "") == "multi_text")
    )
    if is_multi_text:
        return max_select, max_select

    if itype == "matrix":
        return 1, max_select

    if itype == "cardsort":
        cards_count = len([c for c in (qmeta.get("cards") or []) if str(c or "").strip()])
        cards_count = max(1, cards_count)
        return cards_count, cards_count

    # Par défaut explicite et stable: 1
    if itype != "checkbox":
        return 1, 1

    return 1, max_select

def _enforce_selection_ranges(actions: list[dict], constraints: dict[str, int], qid_meta: dict | None = None) -> list[dict]:
    if not constraints:
        return actions

    qid_meta = qid_meta or {}
    by_qid: dict[str, list[dict]] = {}
    for a in actions:
        qid = (a.get("qid") or "").strip().upper()
        if not qid:
            continue
        by_qid.setdefault(qid, []).append(a)

    final_actions: list[dict] = []
    for qid, raw_max in constraints.items():
        q_actions = by_qid.get(qid, [])
        qmeta = qid_meta.get(qid) if isinstance(qid_meta, dict) else None
        qmeta = qmeta if isinstance(qmeta, dict) else {}
        itype = str((qmeta.get("itype") or (q_actions[0].get("itype") if q_actions else "") or "")).strip().lower()
        inferred_qmeta = dict(qmeta)
        if not inferred_qmeta.get("target_id") and q_actions:
            inferred_qmeta["target_id"] = q_actions[0].get("target_id")
        min_select, max_select = _selection_bounds_for_qid(
            qid=qid,
            raw_max=raw_max,
            qmeta=inferred_qmeta,
            itype_hint=itype,
        )

        # Matrices: ne pas tronquer ici.
        # _parse_matrix_pairs borne déjà les paires valides (row/col) à dispatcher.
        if itype in {"matrix", "cardsort"}:
            final_actions.extend(q_actions)
            continue

        if len(q_actions) > max_select:
            _debug_log(
                f"qid={qid} too_many_values max_select={max_select} received={len(q_actions)} action=truncate"
            )
            q_actions = q_actions[:max_select]

        raw_values = [str((a.get("value") or "")).strip() for a in q_actions if str((a.get("value") or "")).strip()]
        options = [str(o or "").strip() for o in (qmeta.get("options") or []) if str(o or "").strip()]
        completed_values = list(raw_values)
        if len(completed_values) > max_select:
            completed_values = completed_values[:max_select]

        if max_select == 0:
            completed_values = []

        _debug_log(
            f"qid={qid} min_select={min_select} max_select={max_select} "
            f"received={len(raw_values)} final_count={len(completed_values)} values={completed_values}"
        )

        template = q_actions[-1] if q_actions else {
            "qid": qid,
            "target_id": qmeta.get("target_id"),
            "value": "",
            "itype": itype,
            "context": qmeta.get("question", ""),
            "matrix_row_label": None,
            "matrix_col_label": None,
            "raw": "",
        }

        for idx, val in enumerate(completed_values):
            if idx < len(q_actions):
                cloned = dict(q_actions[idx])
            else:
                cloned = dict(template)
            cloned["qid"] = qid
            cloned["value"] = val
            final_actions.append(cloned)

    return final_actions


def _normalize_date_triplet_for_multi_text(value: str) -> str | None:
    txt = (value or "").strip()
    if not txt:
        return None

    m = re.match(r"^([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})$", txt)
    if m:
        month_token = m.group(1).lower()
        month = _MONTHS_EN.get(month_token)
        day = int(m.group(2))
        year = int(m.group(3))
        if month and 1 <= day <= 31:
            return f"{month:02d}|{day:02d}|{year:04d}"

    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", txt)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{month:02d}|{day:02d}|{year:04d}"

    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", txt)
    if m:
        a, b, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= a <= 31 and 1 <= b <= 31:
            if a > 12 and 1 <= b <= 12:
                month, day = b, a
            else:
                month, day = a, b
            if 1 <= month <= 12 and 1 <= day <= 31:
                return f"{month:02d}|{day:02d}|{year:04d}"

    return None


def _normalize_multi_text_month_segments(value: str) -> str:
    """Normalise les noms de mois en lettres dans une valeur multi_text déjà segmentée par |."""
    all_months: dict = {**_MONTHS_EN, **_MONTHS_FR}
    segs = value.split("|")
    out = []
    changed = False
    for seg in segs:
        s = seg.strip()
        month_num = all_months.get(s.lower())
        if month_num is not None:
            out.append(f"{month_num:02d}")
            changed = True
        else:
            out.append(s)
    return "|".join(out) if changed else value


def _is_matrix_action(itype: str, qid: str | None, target_id: str | None, qid_meta: dict | None) -> bool:
    it = (itype or "").strip().lower()
    if it == "matrix":
        return True

    if it not in {"radio", "checkbox"}:
        return False

    meta = (qid_meta or {})
    qmeta = meta.get((qid or "").upper()) if qid else None
    if isinstance(qmeta, dict) and (qmeta.get("itype") or "").strip().lower() == "matrix":
        return True

    if target_id:
        for v in meta.values():
            if not isinstance(v, dict):
                continue
            if (v.get("target_id") or "").strip() == target_id and (v.get("itype") or "").strip().lower() == "matrix":
                return True

    return False


def _parse_matrix_pairs(value: str, matrix_active_row: str = "") -> list[tuple[str, str]]:
    """
    Parse une valeur matrix en liste de paires (row, col).

    Format canonique supporté:
      - row_label || col_label
      - row_label || col1|col2|col3   (matrix checkbox multi-colonnes)

    Compatibilité contrôlée (même ligne uniquement):
      - row || col1 || row || col2
    """
    txt = (value or "").strip()
    active_row = (matrix_active_row or "").strip()
    if not txt:
        return []

    if "||" not in txt:
        if not active_row:
            return []
        col_labels = [c.strip() for c in txt.split("|") if c.strip()]
        if not col_labels:
            return []
        seen: set[str] = set()
        pairs: list[tuple[str, str]] = []
        for col in col_labels:
            key = col.lower()
            if key in seen:
                continue
            seen.add(key)
            pairs.append((active_row, col))
        return pairs

    if not txt or "||" not in txt:
        return []

    # Priorité au format ranking multi-paires:
    #   row1 || col1 | row2 || col2
    # On split uniquement sur le pipe SIMPLE (pas sur "||") pour préserver les couples.
    pair_chunks = [c.strip() for c in re.split(r"(?<!\|)\|(?!\|)", txt) if c.strip()]
    if len(pair_chunks) > 1 and all("||" in chunk for chunk in pair_chunks):
        parsed_pairs: list[tuple[str, str]] = []
        for chunk in pair_chunks:
            row_col = [p.strip() for p in chunk.split("||", 1)]
            if len(row_col) != 2:
                return []
            row_label, col_label = row_col
            if not row_label or not col_label:
                return []
            parsed_pairs.append((row_label, col_label))
        return parsed_pairs

    parts = [p.strip() for p in txt.split("||")]
    if len(parts) < 2:
        return []

    # Compatibilité input ambigu: row || col1 || row || col2
    if len(parts) >= 4 and len(parts) % 2 == 0:
        pairs: list[tuple[str, str]] = []
        first_row = parts[0]
        if not first_row:
            return []
        for i in range(0, len(parts), 2):
            row = parts[i]
            col = parts[i + 1]
            if not row or not col or row != first_row:
                return []
            pairs.append((row, col))
        return pairs

    row_label = parts[0]
    right = "||".join(parts[1:]).strip()
    if not row_label or not right:
        return []

    col_labels = [c.strip() for c in right.split("|") if c.strip()]
    if not col_labels:
        return []

    seen: set[str] = set()
    pairs: list[tuple[str, str]] = []
    for col in col_labels:
        key = col.lower()
        if key in seen:
            continue
        seen.add(key)
        pairs.append((row_label, col))
    return pairs


def _is_cmix_simple_grid_other_specify_block(qmeta: dict | None) -> bool:
    if not isinstance(qmeta, dict):
        return False

    context = qmeta.get("context") if isinstance(qmeta.get("context"), dict) else {}
    if not context.get("cmix_simple_grid"):
        return False

    subquestion_name = str(context.get("subquestion_name") or "").strip()
    has_other_specify_input = bool(context.get("has_other_specify_input"))
    question_txt = str(qmeta.get("question") or "").strip()

    return bool(
        (subquestion_name and subquestion_name.lower().endswith("_98"))
        or has_other_specify_input
        or _OTHER_SPECIFY_TEXT_RE.search(question_txt)
    )


def _coerce_to_negative_frequency_option(actions: list[dict], qid_meta: dict | None) -> list[dict]:
    if not actions:
        return actions

    meta = qid_meta or {}
    target_to_meta = {
        (m.get("target_id") or "").strip(): m
        for m in meta.values()
        if isinstance(m, dict) and (m.get("target_id") or "").strip()
    }

    coerced: list[dict] = []
    for action in actions:
        if not isinstance(action, dict):
            coerced.append(action)
            continue

        if (action.get("itype") or "").strip().lower() != "radio":
            coerced.append(action)
            continue

        qid = (action.get("qid") or "").strip().upper()
        target_id = (action.get("target_id") or "").strip()
        qmeta = meta.get(qid) if qid else None
        if not isinstance(qmeta, dict) and target_id:
            qmeta = target_to_meta.get(target_id)

        if not _is_cmix_simple_grid_other_specify_block(qmeta):
            coerced.append(action)
            continue

        options = [str(o or "").strip() for o in ((qmeta or {}).get("options") or []) if str(o or "").strip()]
        if not options:
            coerced.append(action)
            continue

        forced_value = next((opt for opt in options if _NEGATIVE_FREQ_RE.search(opt)), options[-1])
        if forced_value and (action.get("value") or "").strip() != forced_value:
            patched = dict(action)
            patched["value"] = forced_value
            coerced.append(patched)
            continue

        coerced.append(action)

    return coerced

_FIXED_LIST_ITYPES = {"radio", "checkbox", "dropdown"}

def _find_best_option_match(value: str, options: list[str], threshold: float = 0.80):
    """
    Vérifie si `value` correspond à une option connue (exact ou fuzzy).

    Returns:
        (matched_value, is_exact, score)  si une correspondance >= threshold est trouvée
        None                              si aucune option ne dépasse le seuil
    """
    if not options:
        return None
    v_fold = _fold_lc(value)
    # 1. match exact normalisé
    for opt in options:
        if _fold_lc(opt) == v_fold:
            return (opt, True, 1.0)
    # 2. meilleur match fuzzy
    best_opt = None
    best_score = 0.0
    for opt in options:
        score = difflib.SequenceMatcher(None, v_fold, _fold_lc(opt)).ratio()
        if score > best_score:
            best_score = score
            best_opt = opt
    if best_score >= threshold:
        return (best_opt, False, best_score)

    # Résolution suffixe numérique exact.
    # Cas : GPT abrège "Je suis certain(e) que j'achèterai auprès de ce détaillant 7" → "7".
    # Guard : valeur reçue = entier pur ET exactement une option se termine par ce chiffre
    # précédé d'un non-chiffre (évite "7" de matcher "17" ou "71").
    if re.fullmatch(r"\d+", v_fold.strip()):
        num_str = re.escape(v_fold.strip())
        suffix_candidates = [
            opt for opt in options
            if re.search(r"(?<!\d)" + num_str + r"$", _fold_lc(opt))
        ]
        if len(suffix_candidates) == 1:
            _debug_log(
                f"numeric_suffix_match: {value!r} -> {suffix_candidates[0]!r}"
            )
            return (suffix_candidates[0], False, 0.95)

    return None


def parse_batch_response(raw: str, constraints: Optional[Dict[str, int]] = None, qid_meta: Optional[Dict[str, Any]] = None) -> list[dict]:
    """
    Transforme la réponse OpenAI en liste d'instructions exécutables.

    constraints: dict { "Q1": 1, "Q2": 3, ... } pour appliquer max_select (plafond).
    Si constraints est fourni => mode BATCH STRICT:
      - ignore toute ligne sans QID valide (Qn)
      - ignore les QID inconnus (non présents dans constraints)
    """
    print("[batch_response_parser] raw response", raw)

    actions: list[dict] = []
    if not raw:
        return actions

    lines = [l.strip() for l in raw.splitlines() if l.strip()]

    # Index inverse target_id -> QID pour tolérer les réponses où OpenAI met
    # le target_id à la place du QID en première colonne.
    target_to_qid: Dict[str, str] = {}
    if isinstance(qid_meta, dict):
        for _qid, _meta in qid_meta.items():
            if not isinstance(_meta, dict):
                continue
            _tid = str(_meta.get("target_id") or "").strip()
            if _tid:
                target_to_qid[_tid] = str(_qid).strip().upper()

    kept_count: Dict[str, int] = {}
    kept_values: Dict[str, set] = {}
    # ✅ Anti-doublons : OpenAI peut répéter la même action sur plusieurs QIDs.
    # Pour les itypes "single-select", on n'exécute qu'une fois par target_id.
    seen_single_targets: set[str] = set()

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
            # Compat supporté:
            #   1) QID //// valeur //// itype //// contexte
            #   2) target_id //// valeur //// itype //// contexte
            first = parts[0].strip()
            m_first = _QID_RE.search(first)
            if m_first:
                qid = m_first.group(0).upper()
            else:
                target_id = first
                qid = target_to_qid.get(target_id)
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

        # Récupération secondaire: si OpenAI a mis le target_id dans la colonne QID
        # du format 5 champs, on le remappe ici.
        if constraints is not None and qid and qid not in constraints:
            recovered_qid = target_to_qid.get(qid)
            if recovered_qid:
                target_id = target_id or qid
                qid = recovered_qid

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

        meta = qid_meta or {}
        qmeta = meta.get(qid) if qid else None
        kind = ((qmeta or {}).get("context") or {}).get("kind") if isinstance(qmeta, dict) else None
        if not kind and target_id:
            for vv in meta.values():
                if not isinstance(vv, dict):
                    continue
                if (vv.get("target_id") or "").strip() == target_id:
                    kind = ((vv.get("context") or {}).get("kind") or "").strip()
                    if kind:
                        break

        is_multi_text_target = (
            itype in {"text", "textarea", "number"}
            and mx >= 2
            and (str(target_id or "").startswith("multi_") or str(kind or "") == "multi_text")
        )
        if is_multi_text_target and "|" not in value:
            normalized = _normalize_date_triplet_for_multi_text(value)
            if normalized:
                _debug_log(f"normalized multi_text date triplet: {value!r} -> {normalized!r}")
                value = normalized
        elif is_multi_text_target and "|" in value:
            normalized = _normalize_multi_text_month_segments(value)
            if normalized != value:
                _debug_log(f"normalized multi_text month segments: {value!r} -> {normalized!r}")
                value = normalized

        is_matrix = _is_matrix_action(itype=itype, qid=qid, target_id=target_id, qid_meta=qid_meta)
        is_cardsort = itype == "cardsort"

        cardsort_assignments: list[tuple[str, str]] = []
        if is_cardsort:
            chunks = [c.strip() for c in re.split(r"\s*;\s*", value) if c.strip()]
            for chunk in chunks:
                if "=>" not in chunk:
                    continue
                card_label, bucket_blob = chunk.split("=>", 1)
                card_label = card_label.strip()
                bucket_blob = bucket_blob.strip()
                if not card_label or not bucket_blob:
                    continue
                cardsort_assignments.append((card_label, bucket_blob))
            if not cardsort_assignments:
                continue

        matrix_pairs: list[tuple[str, str]] = []
        if is_matrix:
            qmeta = (qid_meta or {}).get((qid or "").upper()) if isinstance(qid_meta, dict) else None
            matrix_active_row = ""
            if isinstance(qmeta, dict):
                qmeta_ctx = qmeta.get("context") if isinstance(qmeta.get("context"), dict) else {}
                matrix_active_row = str(qmeta_ctx.get("matrix_active_row") or "").strip()
            matrix_pairs = _parse_matrix_pairs(value, matrix_active_row=matrix_active_row)
            ok = bool(matrix_pairs)
            print(
                f"[PARSER_MATRIX] target_id={target_id!r} pairs={matrix_pairs!r} ok={ok}"
            )
            if not ok:
                continue

        if is_cardsort:
            values = [bucket_blob for _, bucket_blob in cardsort_assignments]
        else:
            values = [col for _, col in matrix_pairs] if is_matrix else _split_values(value, itype=itype, max_select=mx)

        for idx_v, v in enumerate(values):
            v = (v or "").strip()
            if not v and not target_id:
                continue
            
            # ✅ Dédup par target_id pour les single-select
            if target_id:
                if constraints is not None:
                    # en batch strict, mx est fiable (max_select par QID)
                    if mx <= 1 and itype in {"radio", "dropdown", "text", "textarea", "number", "button", "checkbox"}:
                        if target_id in seen_single_targets:
                            continue
                        seen_single_targets.add(target_id)
                else:
                    # hors batch: on évite de casser les multi-checkbox (mx inconnu ici)
                    if itype in {"radio", "dropdown", "text", "textarea", "number", "button"}:
                        if target_id in seen_single_targets:
                            continue
                        seen_single_targets.add(target_id)

            # ✅ Validation de la valeur contre les options connues (anti-hallucination LLM)
            if v and itype in _FIXED_LIST_ITYPES and not is_matrix and not is_cardsort:
                known_options = [str(o or "").strip() for o in ((qmeta or {}).get("options") or []) if str(o or "").strip()]
                if known_options:
                    match = _find_best_option_match(v, known_options)
                    if match is None:
                        log_info("[batch_response_parser]",
                                 f"qid={qid} valeur rejetée (aucune option correspondante): {v!r} "
                                 f"options={known_options}")
                        continue
                    matched_val, is_exact, score = match
                    if not is_exact:
                        log_debug("[batch_response_parser]",
                                  f"qid={qid} valeur substituée fuzzy (score={score:.2f}): {v!r} -> {matched_val!r}")
                        v = matched_val

            if qid and constraints:
                # mx déjà calculé au-dessus
                kept_count.setdefault(qid, 0)
                kept_values.setdefault(qid, set())

                keyv = v.strip().lower()
                if keyv in kept_values[qid]:
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
                    "matrix_row_label": matrix_pairs[idx_v][0] if is_matrix else None,
                    "matrix_col_label": matrix_pairs[idx_v][1] if is_matrix else None,
                    "cardsort_card_label": cardsort_assignments[idx_v][0] if is_cardsort else None,
                    "cardsort_bucket_labels": cardsort_assignments[idx_v][1] if is_cardsort else None,
                    "raw": line,
                }
            )

    # --- Fallback : ligne brute sans //// pour bloc multi_text unique (mode batch strict) ---
    # Déclenché quand GPT renvoie les valeurs | sans enveloppe QID //// target_id //// ...
    # Conditions strictes :
    #   1. mode batch strict (constraints non None)
    #   2. exactement 1 QID dans constraints avec context.kind=multi_text
    #   3. ce QID n'a encore aucune action générée
    #   4. le raw brut contient exactement 1 ligne sans "////"
    if constraints is not None:
        _mt_qids: list[str] = []
        for _qid_c in constraints:
            _qmeta_c = (qid_meta or {}).get(_qid_c) if isinstance(qid_meta, dict) else None
            if not isinstance(_qmeta_c, dict):
                continue
            _ctx_c = _qmeta_c.get("context") if isinstance(_qmeta_c.get("context"), dict) else {}
            if str((_ctx_c or {}).get("kind") or "") == "multi_text":
                _mt_qids.append(_qid_c)
        if len(_mt_qids) == 1:
            _qid_mt = _mt_qids[0]
            _answered = {(a.get("qid") or "").upper() for a in actions}
            if _qid_mt not in _answered:
                _bare_lines = [l.strip() for l in raw.splitlines() if l.strip() and "////" not in l]
                if len(_bare_lines) == 1:
                    _bare_val = _bare_lines[0]
                    _mx_mt = int(constraints.get(_qid_mt, 1) or 1)
                    _qmeta_mt: dict = (qid_meta or {}).get(_qid_mt) or {}  # type: ignore[assignment]
                    _tid_mt = str(_qmeta_mt.get("target_id") or "").strip()
                    _itype_mt = str(_qmeta_mt.get("itype") or "text").strip().lower() or "text"
                    _ctx_mt_str = str(_qmeta_mt.get("question") or "")
                    _vals_mt = _split_values(_bare_val, itype=_itype_mt, max_select=_mx_mt)
                    _debug_log(
                        f"multi_text_bare_fallback: qid={_qid_mt} target_id={_tid_mt!r} "
                        f"segments={len(_vals_mt)} raw={_bare_val!r}"
                    )
                    for _v in _vals_mt:
                        _v = (_v or "").strip()
                        if not _v:
                            continue
                        actions.append({
                            "qid": _qid_mt,
                            "target_id": _tid_mt,
                            "value": _v,
                            "itype": _itype_mt,
                            "context": _ctx_mt_str,
                            "matrix_row_label": None,
                            "matrix_col_label": None,
                            "cardsort_card_label": None,
                            "cardsort_bucket_labels": None,
                            "raw": _bare_val,
                        })

    actions = _coerce_to_negative_frequency_option(actions, qid_meta=qid_meta)

    if constraints:
        actions = _enforce_selection_ranges(actions, constraints=constraints, qid_meta=qid_meta)

    return actions

def _norm_lc(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def _fold_lc(s: str | None) -> str:
    base = (s or "")
    # Neutraliser les variantes typographiques AVANT NFKD :
    # - apostrophes courbes/spéciales → ASCII '
    # - espaces insécables / fine / autres → espace ordinaire
    # Sans ça, deux libellés visuellement identiques mais encodés différemment
    # (LLM vs DOM) produisent un ratio SequenceMatcher < 0.80 et sont rejetés.
    base = base.replace("\u2019", "'").replace("\u2018", "'") \
               .replace("\u02bc", "'").replace("\u0060", "'") \
               .replace("\u00ab", "").replace("\u00bb", "") \
               .replace("\u00a0", " ").replace("\u202f", " ") \
               .replace("\u2009", " ").replace("\u2002", " ") \
               .replace("\u2003", " ")
    base = unicodedata.normalize("NFKD", base)
    base = "".join(ch for ch in base if not unicodedata.combining(ch))
    return _norm_lc(base)


def _is_system_scope(scope: str | None) -> bool:
    v = _norm_lc(scope)
    return any(x in v for x in ["__viewstate", "__eventvalidation", "__viewstategenerator", "__eventtarget", "__eventargument"])

def _year_span_from_options(opts: list) -> tuple[int, int, int] | None:
    """
    Déduit une plage d'années depuis les options (ex: 1926..2026).
    Retourne (min_year, max_year, span) si suffisamment robuste, sinon None.
    """
    if not opts:
        return None

    years: set[int] = set()
    for o in opts:
        s = str(o or "")
        m = re.search(r"\b(19\d{2}|20\d{2})\b", s)
        if m:
            try:
                years.add(int(m.group(1)))
            except Exception:
                pass

    if len(years) < 10:
        return None

    mn, mx = min(years), max(years)
    span = mx - mn
    if span < 40:
        return None

    return mn, mx, span

def _looks_like_month_options(opts: list) -> bool:
    """
    Heuristique simple : options contenant beaucoup de mois (FR/EN).
    """
    if not opts:
        return False
    blob = " ".join(str(o or "").strip().lower() for o in opts)
    month_tokens = [
        "janvier","février","fevrier","mars","avril","mai","juin","juillet","août","aout","septembre","octobre","novembre","décembre","decembre",
        "january","february","march","april","may","june","july","august","september","october","november","december",
    ]
    hits = sum(1 for t in month_tokens if t in blob)
    return hits >= 6

def sanitize_actions(actions: list, qid_meta: dict | None = None) -> list:
    """
    Nettoie/valide les actions OpenAI avant exécution.
    
    Étapes:
    1. Filtrage des CTA parasites
    2. Sanitization des dates (DOB, ranges)
    3. Filtrage des conflits d'options exclusives
    """
    cleaned = []
    now_year = datetime.datetime.utcnow().year
    qid_meta = qid_meta or {}

    # Heuristique "page" : présence d'un dropdown de mois + un dropdown d'années sur grande plage
    page_has_month_dropdown = False
    page_year_span: tuple[int, int, int] | None = None

    for _, meta in (qid_meta or {}).items():
        if not isinstance(meta, dict):
            continue
        opts = meta.get("options") or []
        if _looks_like_month_options(opts):
            page_has_month_dropdown = True
        ys = _year_span_from_options(opts)
        if ys:
            if (page_year_span is None) or (ys[2] > page_year_span[2]):
                page_year_span = ys

    months_fr = {
        "1": "Janvier", "01": "Janvier",
        "2": "Février", "02": "Février",
        "3": "Mars", "03": "Mars",
        "4": "Avril", "04": "Avril",
        "5": "Mai", "05": "Mai",
        "6": "Juin", "06": "Juin",
        "7": "Juillet", "07": "Juillet",
        "8": "Août", "08": "Août",
        "9": "Septembre", "09": "Septembre",
        "10": "Octobre",
        "11": "Novembre",
        "12": "Décembre",
    }
    months_en_to_fr = {
        "january": "Janvier", "february": "Février", "march": "Mars", "april": "Avril",
        "may": "Mai", "june": "Juin", "july": "Juillet", "august": "Août",
        "september": "Septembre", "october": "Octobre", "november": "Novembre", "december": "Décembre",
    }

    for a in actions or []:
        if not isinstance(a, dict):
            cleaned.append(a)
            continue

        it = _norm_lc(a.get("itype") or "")
        ctx_lc = _norm_lc(str(a.get("context") or ""))
        raw_lc = _norm_lc(str(a.get("raw") or ""))

        # valeur (clé correcte)
        v = a.get("value")
        v_lc = _norm_lc(str(v or ""))

        # virer les "CTA" parasites très courts
        if it in ("text", "dropdown", "radio", "checkbox", "") and any(tok in v_lc for tok in ["continue", "continuer", "next", "suivant"]) and len(v_lc) <= 16:
            continue

        # Détection "date range" (ex: IPSOS DOB: "entre 23/02/1926 et 29/01/2026")
        range_m = (
            re.search(r"entre\s+(\d{1,2}/\d{1,2}/\d{4})\s+et\s+(\d{1,2}/\d{1,2}/\d{4})", raw_lc)
            or re.search(r"entre\s+(\d{1,2}/\d{1,2}/\d{4})\s+et\s+(\d{1,2}/\d{1,2}/\d{4})", ctx_lc)
        )
        range_min_y = range_max_y = None
        range_span = None
        if range_m:
            try:
                y1 = int(range_m.group(1).split("/")[-1])
                y2 = int(range_m.group(2).split("/")[-1])
                range_min_y, range_max_y = (y1, y2) if y1 <= y2 else (y2, y1)
                range_span = range_max_y - range_min_y
            except Exception:
                range_m = None

        is_dob_keywords = any(k in ctx_lc or k in raw_lc for k in ["date de naissance", "naissance", "dob", "birth"])
        has_month_or_year_marker = any(k in ctx_lc or k in raw_lc for k in ["mois", "month", "année", "annee", "year"])
        # Règle simple : si gros range (>=40 ans) + champ mois/année => DOB
        is_dob = bool(is_dob_keywords or (range_m and has_month_or_year_marker and (range_span is not None and range_span >= 40)))

        act_qid = (a.get("qid") or "").strip().upper()
        meta = qid_meta.get(act_qid) if act_qid else None
        meta_opts = (meta.get("options") or []) if isinstance(meta, dict) else []
        meta_question = (meta.get("question") or "") if isinstance(meta, dict) else ""
        meta_year_span = _year_span_from_options(meta_opts)

        # if it in {"text", "textarea", "number"} and _is_birth_year_question_text(ctx_lc, raw_lc, meta_question):
        #     maybe_year = re.search(r"\b\d{1,4}\b", str(v or ""))
        #     if maybe_year:
        #         parsed_num = int(maybe_year.group(0))
        #         if parsed_num < 1900 or parsed_num > now_year:
        #             corrected_year = (now_year - parsed_num) if 0 < parsed_num < 120 else (now_year - 25)
        #             if corrected_year < 1900 or corrected_year > now_year:
        #                 corrected_year = now_year - 25
        #             _warn_log(
        #                 f"qid={act_qid or '?'} invalid_birth_year_value={parsed_num} corrected_to={corrected_year}"
        #             )
        #             a = dict(a)
        #             a["value"] = str(corrected_year)
        #             a["raw"] = (a.get("raw") or "") + f" [sanitized_birth_year:{parsed_num}->{corrected_year}]"

        # DOB "explicite" (keywords/range) OU DOB "implicite" (options années sur grande plage + mois sur la page)
        is_dob_like_by_options = bool(
            it == "dropdown"
            and page_has_month_dropdown
            and (meta_year_span is not None or page_year_span is not None)
            and any(k in ctx_lc or k in raw_lc for k in ["année", "annee", "year"])
        )

        # ✅ IMPORTANT: traiter les DOB implicites comme des DOB pour la sanitation de l'année
        is_dob_effective = bool(is_dob or is_dob_like_by_options)

        if it == "dropdown" and (is_dob or range_m or is_dob_like_by_options):
            # Année : évite les valeurs disqualifiantes (trop jeune) + respecte un éventuel range
            if any(k in ctx_lc or k in raw_lc for k in ["année", "annee", "year"]):
                m = re.search(r"\b(19\d{2}|20\d{2})\b", str(v or ""))
                if m:
                    y = int(m.group(1))
                    if range_m and range_max_y:
                        ref_year = int(range_max_y)
                    else:
                        ref_year = now_year

                    # ⬇️ AVANT: if is_dob:
                    if is_dob_effective:
                        min_y = ref_year - 64
                        max_y = ref_year - 18
                        if range_m and range_min_y is not None and range_max_y is not None:
                            min_y = max(min_y, int(range_min_y))
                            max_y = min(max_y, int(range_max_y))
                            if min_y > max_y:  # garde-fou
                                min_y, max_y = int(range_min_y), int(range_max_y)

                    if is_dob:
                        min_y = ref_year - 64
                        max_y = ref_year - 18
                        if range_m and range_min_y is not None and range_max_y is not None:
                            min_y = max(min_y, int(range_min_y))
                            max_y = min(max_y, int(range_max_y))
                            if min_y > max_y:  # garde-fou
                                min_y, max_y = int(range_min_y), int(range_max_y)

                        if y < min_y or y > max_y:
                            y2 = ref_year - 25
                            y2 = max(min_y, min(max_y, y2))
                            a = dict(a)
                            a["value"] = str(y2)
                            a["raw"] = (a.get("raw") or "") + f" [sanitized_year:{y}->{y2}]"

                    elif range_m and range_min_y is not None and range_max_y is not None:
                        # pas DOB : on se contente d'être dans le range
                        if y < int(range_min_y) or y > int(range_max_y):
                            y2 = int(range_max_y)
                            a = dict(a)
                            a["value"] = str(y2)
                            a["raw"] = (a.get("raw") or "") + f" [clamped_year:{y}->{y2}]"

            # Mois : map num/anglais -> FR (utile DOB et dates "range")
            if any(k in ctx_lc or k in raw_lc for k in ["mois", "month"]):
                if v_lc in months_fr:
                    a = dict(a); a["value"] = months_fr[v_lc]
                elif v_lc in months_en_to_fr:
                    a = dict(a); a["value"] = months_en_to_fr[v_lc]

        cleaned.append(a)

    # =========================================================================
    # ÉTAPE FINALE: Filtrage des conflits d'options exclusives
    # =========================================================================
    # Après nettoyage individuel, on filtre les combinaisons impossibles
    # (ex: "Aucune de ces réponses" + autres options pour le même QID) 
    #cleaned = filter_exclusive_conflicts(cleaned, qid_meta)

    return cleaned