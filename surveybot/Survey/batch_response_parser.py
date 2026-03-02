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
import os
from typing import Dict, Optional, List, Any
_ALLOWED_ITYPES = {"radio", "checkbox", "dropdown", "text", "textarea", "button", "number"}
_QID_RE = re.compile(r"\bQ\d+\b", re.IGNORECASE)


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
    r"^je\s+ne\s+sais\s+pas",               # "Je ne sais pas"
    r"^ne\s+sais\s+pas",                    # "Ne sais pas"
    r"^nsp$",                               # NSP (Ne Sais Pas)
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
    r"^i\s+don'?t\s+know",                  # "I don't know"
    r"^don'?t\s+know",                      # "Don't know"
    r"^not\s+applicable",                   # "Not applicable"
    r"^n/?a$",                              # N/A
    r"^prefer\s+not\s+to",                  # "Prefer not to say"
    r"^i\s+prefer\s+not",                   # "I prefer not to..."
    r"^refuse",                             # "Refuse"
    r"^not\s+sure",                         # "Not sure"
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


def _debug_enabled() -> bool:
    lvl = (os.getenv("LOG_LEVEL") or "").strip().lower()
    return lvl == "debug"


def _debug_log(msg: str) -> None:
    if _debug_enabled():
        print(f"[batch_response_parser][debug] {msg}")


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


def _parse_matrix_value(value: str) -> tuple[str, str] | tuple[None, None]:
    txt = (value or "").strip()
    if not txt or "||" not in txt:
        return None, None
    left, right = txt.split("||", 1)
    row_label = (left or "").strip()
    col_label = (right or "").strip()
    if not row_label or not col_label:
        return None, None
    return row_label, col_label

def parse_batch_response(raw: str, constraints: Optional[Dict[str, int]] = None, qid_meta: Optional[Dict[str, Any]] = None) -> list[dict]:
    """
    Transforme la réponse OpenAI en liste d'instructions exécutables.

    constraints: dict { "Q1": 1, "Q2": 3, ... } pour appliquer max_select.
    Si constraints est fourni => mode BATCH STRICT:
      - ignore toute ligne sans QID valide (Qn)
      - ignore les QID inconnus (non présents dans constraints)
    """
    print("[batch_response_parser] raw response", raw)

    actions: list[dict] = []
    if not raw:
        return actions

    lines = [l.strip() for l in raw.splitlines() if l.strip()]

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
            # compat: QID //// valeur //// itype //// contexte
            qid = parts[0].strip()
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

        is_matrix = _is_matrix_action(itype=itype, qid=qid, target_id=target_id, qid_meta=qid_meta)
        matrix_row_label = None
        matrix_col_label = None
        if is_matrix:
            matrix_row_label, matrix_col_label = _parse_matrix_value(value)
            ok = bool(matrix_row_label and matrix_col_label)
            print(
                f"[PARSER_MATRIX] target_id={target_id!r} row={matrix_row_label!r} col={matrix_col_label!r} ok={ok}"
            )
            if not ok:
                continue

        values = [matrix_col_label] if is_matrix else _split_values(value, itype=itype, max_select=mx)

        for v in values:
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

            if qid and constraints:
                # mx déjà calculé au-dessus
                kept_count.setdefault(qid, 0)
                kept_values.setdefault(qid, set())

                keyv = v.strip().lower()
                if keyv in kept_values[qid]:
                    continue
                if kept_count[qid] >= mx:
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
                    "matrix_row_label": matrix_row_label,
                    "matrix_col_label": matrix_col_label if is_matrix else None,
                    "raw": line,
                }
            )

    return actions

def _norm_lc(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()

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
        meta_year_span = _year_span_from_options(meta_opts)

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
                    elif meta_year_span:
                        ref_year = int(meta_year_span[1])
                    elif page_year_span:
                        ref_year = int(page_year_span[1])
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
    cleaned = filter_exclusive_conflicts(cleaned, qid_meta)

    return cleaned
