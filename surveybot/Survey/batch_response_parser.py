# Survey/batch_response_parser.py
"""
Parse les réponses OpenAI batch.

Format attendu (préféré):
QID //// valeur //// itype //// contexte

Fallback accepté:
valeur //// itype //// contexte
"""

from __future__ import annotations
import re, datetime
from typing import Dict, Optional, List
_ALLOWED_ITYPES = {"radio", "checkbox", "dropdown", "text", "textarea", "button", "number"}
_QID_RE = re.compile(r"\bQ\d+\b", re.IGNORECASE)


def _split_values(value: str, itype: str = "", max_select: int = 1) -> List[str]:
    """
    Supporte les réponses multi *uniquement quand ça a du sens*.

    Règles (prédictibles):
    - split prioritaire sur "|" (format recommandé)
    - split sur "," UNIQUEMENT pour checkbox quand max_select > 1
      (évite de casser des libellés qui contiennent des virgules, ex: "NI D'ACCORD, NI PAS D'ACCORD")
    """
    if not value:
        return []

    v = value.strip()
    it = (itype or "").strip().lower()

    # split prioritaire sur |
    if "|" in v:
        return [p.strip() for p in v.split("|") if p.strip()]

    # ✅ NEW: multi champs ouverts (text/textarea) quand max_select>1
    # Cas fréquent: le modèle renvoie "A, B, C" sur une seule ligne.
    # On découpe de manière prédictible (virgule/point-virgule) et constraints coupera à max_select.
    if it in {"text", "textarea", "number"} and (max_select or 1) > 1 and ("," in v or ";" in v):
        parts = [p.strip(" \t-•") for p in re.split(r"\s*[,;]\s*", v) if p.strip()]
        if len(parts) >= 2:
            return parts

    # split sur virgule UNIQUEMENT pour checkbox multi
    if it == "checkbox" and (max_select or 1) > 1 and "," in v and len(v) < 120:
        return [p.strip() for p in v.split(",") if p.strip()]

    return [v]

def parse_batch_response(raw: str, constraints: Optional[Dict[str, int]] = None) -> list[dict]:
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

        values = _split_values(value, itype=itype, max_select=mx)

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
    """Nettoie/valide les actions OpenAI avant exécution."""
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

        # virer les “CTA” parasites très courts
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

    return cleaned
