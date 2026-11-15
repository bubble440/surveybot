from __future__ import annotations

"""Oracle d'extraction BDL-4 (étape 3) — OBSERVATION SEULE.

Évalue, pour chaque bloc d'une page collectée (`extraction_pass.json`, étape 2),
des contradictions STRUCTURELLES internes au bloc. Fonction pure : aucune I/O,
aucun navigateur, aucune comparaison au DOM. Le verdict est écrit dans le JSON
collecté (clé "oracle") ; il ne modifie ni le validateur, ni failure_cases, ni
AutoFix, ni l'extraction.

Principe de prédictibilité : un seul jeu de règles par famille d'itype, aucune
cascade. On s'ABSTIENT (INCONCLUSIVE) dès que le bloc n'offre pas de preuve
fiable (itype inconnu ou non couvert, options tronquées par la collecte,
plusieurs blocs partageant un même group_key).

Verdicts par bloc (le plus fort l'emporte) :
  BUG_CONFIRMED : invariant arithmétique/structurel violé (preuve forte).
  SUSPECTED     : incohérence probable mais des widgets légitimes existent.
  PASS          : au moins une règle applicable évaluée, aucune ne se déclenche.
  INCONCLUSIVE  : aucune règle applicable ou preuve insuffisante (motif donné).

Règles v1 (aucune ne lit le DOM) :
  radio/checkbox : options vides                     -> BUG_CONFIRMED
  radio/checkbox : min_select > max_select (> 0)     -> BUG_CONFIRMED
  checkbox       : max_select > nb d'options         -> BUG_CONFIRMED
  radio          : max_select > 1                    -> SUSPECTED
  radio/checkbox : options dupliquées (normalisées)  -> SUSPECTED
  text/textarea/number : options non vides           -> SUSPECTED
  itype hors contrat BDL-1                           -> INCONCLUSIVE (« non documenté »)
  dropdown/select/select_rps/matrix/button           -> INCONCLUSIVE (abstention structurelle)
"""

import unicodedata
from typing import Any, Optional

ORACLE_VERSION = 1

PASS = "PASS"
SUSPECTED = "SUSPECTED"
BUG_CONFIRMED = "BUG_CONFIRMED"
INCONCLUSIVE = "INCONCLUSIVE"

# Rang de sévérité : le verdict d'un bloc est celui du signal le plus fort.
_RANK = {PASS: 0, SUSPECTED: 1, BUG_CONFIRMED: 2}

_CHOICE_ITYPES = {"radio", "checkbox"}
_TEXT_ITYPES = {"text", "textarea", "number"}
# Types du contrat BDL-1 (§2) sans règle v1 : abstention structurelle (le contrat
# interdit d'inférer un bug d'une liste d'options vide sur dropdown/select).
_ABSTAIN_ITYPES = {"dropdown", "select", "select_rps", "matrix", "button"}
_CONTRACT_ITYPES = _CHOICE_ITYPES | _TEXT_ITYPES | _ABSTAIN_ITYPES


def _norm_lc(value: Any) -> str:
    # Même forme canonique que question_block_validator._norm_lc (NFKC + espaces, minuscules).
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split()).lower()


def _to_int(value: Any) -> int:
    """Entier >= 0 ; absent / invalide / booléen -> 0 (= « non renseigné », comme le validateur)."""
    if isinstance(value, bool):
        return 0
    try:
        return max(int(value or 0), 0)
    except Exception:
        return 0


def _options_info(block: dict) -> tuple:
    """(options normalisées non vides, nombre total d'options, liste tronquée ?).

    `options_count` (écrit par la collecte) donne le total réel ; absent dans les
    anciens fichiers -> on retombe sur la longueur de la liste conservée.
    """
    raw = block.get("options")
    opts = raw if isinstance(raw, list) else []
    normalized = [n for n in (_norm_lc(o) for o in opts) if n]
    total = block.get("options_count")
    total = total if isinstance(total, int) and not isinstance(total, bool) and total >= 0 else len(opts)
    truncated = total > len(opts)
    return normalized, total, truncated


def _signals_for(block: dict, itype: str) -> list:
    """Liste de (signal, verdict) déclenchés pour ce bloc, ou [] si rien."""
    normalized, total, truncated = _options_info(block)
    min_sel = _to_int(block.get("min_select"))
    max_sel = _to_int(block.get("max_select"))
    out: list = []

    if itype in _CHOICE_ITYPES:
        if total == 0:
            out.append(("choice_without_options", BUG_CONFIRMED))
        if min_sel > 0 and max_sel > 0 and min_sel > max_sel:
            out.append(("invalid_selection_limits", BUG_CONFIRMED))
        # Comptage fiable seulement si la collecte n'a pas tronqué les options.
        if itype == "checkbox" and max_sel > 0 and total > 0 and not truncated and max_sel > total:
            out.append(("max_select_exceeds_options", BUG_CONFIRMED))
        if itype == "radio" and max_sel > 1:
            out.append(("radio_max_select_gt_1", SUSPECTED))
        if normalized and len(normalized) != len(set(normalized)):
            out.append(("duplicate_options", SUSPECTED))
    elif itype in _TEXT_ITYPES:
        if total > 0:
            out.append(("text_with_options", SUSPECTED))
    return out


def _group_key(block: dict) -> str:
    ctx = block.get("context")
    return _norm_lc(ctx.get("group_key")) if isinstance(ctx, dict) else ""


def _evaluate_block(block: dict, index: int, shared_keys: set) -> dict:
    itype = _norm_lc(block.get("itype"))
    item: dict = {"index": index, "target_id": block.get("target_id") or None, "itype": itype or None}

    if not itype:
        item.update(verdict=INCONCLUSIVE, reason="missing_itype")
    elif itype not in _CONTRACT_ITYPES:
        # Type nouveau : revue du contrat, jamais un bug d'extraction.
        item.update(verdict=INCONCLUSIVE, reason="undocumented_itype")
    elif itype in _ABSTAIN_ITYPES:
        item.update(verdict=INCONCLUSIVE, reason="unsupported_itype")
    elif _group_key(block) in shared_keys:
        # Carrousel / contenu dynamique : blocs jumeaux, faits non indépendants.
        item.update(verdict=INCONCLUSIVE, reason="shared_group_key")
    else:
        signals = _signals_for(block, itype)
        if signals:
            item["verdict"] = max((v for _, v in signals), key=lambda v: _RANK[v])
            item["signals"] = [s for s, _ in signals]
        else:
            item["verdict"] = PASS
    return item


def evaluate_extraction_pass(payload: Any) -> Optional[dict]:
    """Verdict par bloc à partir d'un payload `extraction_pass.json`.

    Retourne None si le payload n'a pas de liste `blocks` (on n'invente rien).
    Ne lève jamais sur un payload partiel ou mal formé.
    """
    blocks = payload.get("blocks") if isinstance(payload, dict) else None
    if not isinstance(blocks, list):
        return None
    entries = [b for b in blocks if isinstance(b, dict)]

    # group_key partagé par plusieurs blocs de la page (hors valeur vide).
    seen: dict = {}
    for b in entries:
        gk = _group_key(b)
        if gk:
            seen[gk] = seen.get(gk, 0) + 1
    shared_keys = {gk for gk, n in seen.items() if n > 1}

    results = []
    summary: dict = {}
    for i, b in enumerate(entries):
        try:
            item = _evaluate_block(b, i, shared_keys)
        except Exception:
            item = {"index": i, "verdict": INCONCLUSIVE, "reason": "oracle_error"}
        results.append(item)
        summary[item["verdict"]] = summary.get(item["verdict"], 0) + 1

    return {"version": ORACLE_VERSION, "blocks": results, "summary": summary}