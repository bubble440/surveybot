#!/usr/bin/env python3
"""BDL-4 étape 4 — annotation et mesure de l'oracle d'extraction.

Outil en lecture seule sur le corpus collecté (`bdl_collect_extraction/`) :
ne lance pas de navigateur, ne modifie ni SurveyBot ni l'oracle.

Deux commandes :
  template : écrit (ou complète) un CSV d'annotation, une ligne par bloc collecté.
             Un CSV existant n'est JAMAIS écrasé : les pages déjà présentes sont
             conservées telles quelles (annotations incluses), seules les
             nouvelles pages sont ajoutées.
  report   : lit le CSV annoté + le corpus et évalue les critères de clôture
             C1 à C4 (C5 = tri des signalements BDL-1 et C6 = fixtures de tests
             sont manuels, hors outil).

Vérité terrain (colonne `truth`, remplie à la main en regardant la page) :
  OK      le bloc représente correctement ce qui est affiché
  BAD     le bloc est faux (options erronées, mauvais type, limites fausses...)
  UNSURE  impossible de trancher : exclu de toutes les précisions
  MISSING (ligne ajoutée à la main, `block_index` vide) un bloc attendu est absent
Une ligne « page » (`block_index` vide) est générée pour chaque page : OK si rien
d'autre n'était attendu, MISSING s'il manque au moins un bloc.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

FIELDS = [
    "folder", "timestamp", "url", "block_index", "itype", "question",
    "options_count", "verdict", "signals", "reason", "truth", "note",
]
VALID_TRUTH = {"OK", "BAD", "UNSURE", "MISSING"}

# --- Critères de clôture BDL-4 étape 4 (figés avec l'utilisateur) -----------
MIN_PAGES = 20            # C1 : pages annotées (plafonnées par hôte)
MIN_HOSTS = 3             # C1
MAX_PAGES_PER_HOST = 3    # C1 : au-delà, la page est annotée mais ne compte plus
REQUIRED_ITYPES = ("radio", "checkbox", "text", "dropdown")  # C1 : itypes observés
SIGNAL_MIN_REVIEWED = 3   # C3 : en dessous, un signal reste « non évalué »
SIGNAL_MIN_PRECISION = 0.5  # C3 : en dessous (avec assez de revues), on retire

# Niveau de chaque signal de Survey/extraction_oracle.py (à garder synchronisé).
SIGNAL_LEVEL = {
    "choice_without_options": "BUG_CONFIRMED",
    "invalid_selection_limits": "BUG_CONFIRMED",
    "max_select_exceeds_options": "BUG_CONFIRMED",
    "radio_max_select_gt_1": "SUSPECTED",
    "duplicate_options": "SUSPECTED",
    "text_with_options": "SUSPECTED",
}


def _default_root() -> Path:
    return Path(os.getenv("SURVEY_BDL_EXTRACT_COLLECT_DIR", "./bdl_collect_extraction"))


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# --------------------------------------------------------------------------
# Corpus collecté
# --------------------------------------------------------------------------
def load_corpus(root: Path) -> list:
    """Pages collectées (une par sous-dossier contenant extraction_pass.json)."""
    pages = []
    if not root.is_dir():
        return pages
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        payload = _read_json(folder / "extraction_pass.json")
        if not isinstance(payload, dict):
            continue
        meta = _read_json(folder / "meta.json")
        meta = meta if isinstance(meta, dict) else {}
        blocks = payload.get("blocks")
        oracle = payload.get("oracle")
        pages.append({
            "folder": folder.name,
            "timestamp": str(meta.get("timestamp") or ""),
            "url": str(payload.get("url") or meta.get("url") or ""),
            "blocks": blocks if isinstance(blocks, list) else [],
            "oracle": oracle if isinstance(oracle, dict) else None,
        })
    return pages


def _oracle_entries(page: dict) -> list:
    """Entrées de verdict par bloc ; corpus antérieur à l'étape 3 -> NO_ORACLE."""
    oracle = page["oracle"]
    if oracle and isinstance(oracle.get("blocks"), list):
        return [e for e in oracle["blocks"] if isinstance(e, dict)]
    return [
        {"index": i, "itype": (b or {}).get("itype"), "verdict": "NO_ORACLE"}
        for i, b in enumerate(page["blocks"])
    ]


def corpus_distribution(pages: list) -> dict:
    """Distribution des verdicts et motifs d'abstention sur TOUT le corpus."""
    verdicts: Counter = Counter()
    reasons: Counter = Counter()
    for page in pages:
        for e in _oracle_entries(page):
            verdicts[str(e.get("verdict") or "NO_ORACLE")] += 1
            if e.get("reason"):
                reasons[str(e["reason"])] += 1
    return {"pages": len(pages), "blocks": sum(verdicts.values()),
            "verdicts": dict(verdicts), "abstention_reasons": dict(reasons)}


# --------------------------------------------------------------------------
# CSV d'annotation
# --------------------------------------------------------------------------
def _clean(text: Any, limit: int = 80) -> str:
    return " ".join(str(text or "").split())[:limit]


def _rows_for_page(page: dict) -> list:
    base = {"folder": page["folder"], "timestamp": page["timestamp"], "url": page["url"]}
    rows = []
    for e in _oracle_entries(page):
        idx = e.get("index")
        block = page["blocks"][idx] if isinstance(idx, int) and 0 <= idx < len(page["blocks"]) else {}
        block = block if isinstance(block, dict) else {}
        count = block.get("options_count")
        rows.append({**base,
            "block_index": idx if idx is not None else "",
            "itype": e.get("itype") or "",
            "question": _clean(block.get("question")),
            "options_count": count if count is not None else "",
            "verdict": e.get("verdict") or "",
            "signals": ",".join(e.get("signals") or []),
            "reason": e.get("reason") or "",
            "truth": "", "note": ""})
    # Ligne « page » : permet d'annoter une page sans bloc ou à blocs manquants.
    rows.append({**base, "block_index": "", "itype": "",
                 "question": "(page : blocs manquants ?)" if rows else "(aucun bloc extrait)",
                 "options_count": "", "verdict": "", "signals": "", "reason": "",
                 "truth": "", "note": ""})
    return rows


def read_annotations(path: Path) -> list:
    """Lit le CSV (séparateur ';' ou ',' détecté sur l'en-tête, BOM toléré)."""
    text = path.read_text(encoding="utf-8-sig")
    header = text.splitlines()[0] if text.strip() else ""
    delimiter = ";" if header.count(";") >= header.count(",") else ","
    return [dict(r) for r in csv.DictReader(text.splitlines(), delimiter=delimiter)]


def cmd_template(root: Path, output: Path) -> int:
    pages = load_corpus(root)
    if not pages:
        print(f"Aucune page collectée dans {root}", file=sys.stderr)
        return 2
    existing = read_annotations(output) if output.is_file() else []
    known = {r.get("folder") for r in existing}
    new_rows = [r for p in pages if p["folder"] not in known for r in _rows_for_page(p)]
    with output.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, delimiter=";", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(existing)
        writer.writerows(new_rows)
    print(f"{output} : {len(existing)} lignes conservées, {len(new_rows)} ajoutées "
          f"({len(pages) - len(known & {p['folder'] for p in pages})} nouvelles pages).")
    return 0


# --------------------------------------------------------------------------
# Rapport (critères C1 à C4)
# --------------------------------------------------------------------------
def _truth(row: dict) -> str:
    return str(row.get("truth") or "").strip().upper()


def _is_block_row(row: dict) -> bool:
    return str(row.get("block_index") or "").strip() != ""


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower() or "(inconnu)"
    except Exception:
        return "(inconnu)"


def _precision(tp: int, fp: int):
    return round(tp / (tp + fp), 3) if (tp + fp) else None


def build_report(root: Path, annotations: Path) -> dict:
    pages = load_corpus(root)
    rows = read_annotations(annotations)
    invalid = [r for r in rows if _truth(r) and _truth(r) not in VALID_TRUTH]
    rows = [r for r in rows if _truth(r) in VALID_TRUTH or not _truth(r)]

    annotated_folders = {r.get("folder") for r in rows if _truth(r)}
    # Seules les pages annotées comptent : les autres sont du corpus, pas de la vérité.
    ann_rows = [r for r in rows if r.get("folder") in annotated_folders]

    # --- C1 : couverture ----------------------------------------------------
    host_of: dict = {}
    for r in ann_rows:
        host_of.setdefault(r["folder"], _host(r.get("url") or ""))
    per_host = Counter(host_of.values())
    effective = sum(min(n, MAX_PAGES_PER_HOST) for n in per_host.values())
    covered = {str(r.get("itype") or "").lower() for r in ann_rows if _is_block_row(r) and _truth(r)}
    missing_itypes = [t for t in REQUIRED_ITYPES if t not in covered]
    c1_ok = effective >= MIN_PAGES and len(per_host) >= MIN_HOSTS and not missing_itypes
    c1 = {"status": "OK" if c1_ok else "KO", "pages_annotated": len(annotated_folders),
          "pages_effective": effective, "hosts": dict(per_host),
          "hosts_over_cap": sorted(h for h, n in per_host.items() if n > MAX_PAGES_PER_HOST),
          "missing_itypes": missing_itypes}

    # --- C2 : BUG_CONFIRMED (0 faux positif) -------------------------------
    bug_rows = [r for r in ann_rows if _is_block_row(r) and r.get("verdict") == "BUG_CONFIRMED"]
    tp = sum(_truth(r) == "BAD" for r in bug_rows)
    fp = [r for r in bug_rows if _truth(r) == "OK"]
    unreviewed = sum(not _truth(r) for r in bug_rows)
    c2_status = "KO" if fp else ("INCOMPLET" if unreviewed else "OK")
    c2 = {"status": c2_status, "bug_confirmed_rows": len(bug_rows), "true_positive": tp,
          "false_positive": [{"folder": r["folder"], "block_index": r["block_index"],
                              "signals": r.get("signals")} for r in fp],
          "unreviewed": unreviewed}

    # --- C3 : signaux (SUSPECTED revus à 100 %, règle de retrait) ----------
    stats: dict = defaultdict(lambda: {"tp": 0, "fp": 0, "unsure": 0, "unreviewed": 0})
    for r in ann_rows:
        if not _is_block_row(r):
            continue
        for sig in filter(None, str(r.get("signals") or "").split(",")):
            t = _truth(r)
            key = "tp" if t == "BAD" else "fp" if t == "OK" else "unsure" if t == "UNSURE" else "unreviewed"
            stats[sig][key] += 1
    signals = {}
    for sig, s in sorted(stats.items()):
        level = SIGNAL_LEVEL.get(sig, "INCONNU")
        reviewed = s["tp"] + s["fp"]
        prec = _precision(s["tp"], s["fp"])
        if level == "BUG_CONFIRMED":
            decision = "CORRIGER_RÈGLE" if s["fp"] else "GARDER"
        elif reviewed < SIGNAL_MIN_REVIEWED:
            decision = "NON_ÉVALUÉ"
        elif prec is not None and prec < SIGNAL_MIN_PRECISION:
            decision = "RETIRER"
        else:
            decision = "GARDER"
        signals[sig] = {"level": level, **s, "precision": prec, "decision": decision}
    susp_unreviewed = sum(s["unreviewed"] for sig, s in stats.items()
                          if SIGNAL_LEVEL.get(sig) == "SUSPECTED")
    c3 = {"status": "INCOMPLET" if susp_unreviewed else "OK",
          "unreviewed_suspected": susp_unreviewed, "signals": signals}

    # --- C4 : angle mort (informatif, sans seuil) --------------------------
    blind = [{"folder": r["folder"], "block_index": r.get("block_index") or None,
              "itype": r.get("itype") or None, "truth": _truth(r), "verdict": r.get("verdict") or None,
              "note": r.get("note") or ""}
             for r in ann_rows
             if (_truth(r) == "BAD" and r.get("verdict") in ("PASS", "INCONCLUSIVE", "NO_ORACLE", ""))
             or _truth(r) == "MISSING"]
    c4 = {"count": len(blind), "items": blind}

    return {"corpus": corpus_distribution(pages), "C1": c1, "C2": c2, "C3": c3, "C4": c4,
            "invalid_truth_rows": len(invalid),
            "manual_criteria": "C5 (tri des 4 signalements BDL-1) et C6 (fixtures réelles) : hors outil"}


def render(report: dict) -> str:
    c, c1, c2, c3, c4 = (report[k] for k in ("corpus", "C1", "C2", "C3", "C4"))
    out = [f"CORPUS : {c['pages']} pages, {c['blocks']} blocs — verdicts {c['verdicts']}",
           f"  motifs d'abstention : {c['abstention_reasons']}", ""]
    out.append(f"C1 couverture [{c1['status']}] : {c1['pages_effective']}/{MIN_PAGES} pages effectives "
               f"({c1['pages_annotated']} annotées), {len(c1['hosts'])}/{MIN_HOSTS} hôtes")
    if c1["missing_itypes"]:
        out.append(f"   itypes non couverts : {c1['missing_itypes']}")
    if c1["hosts_over_cap"]:
        out.append(f"   hôtes au-delà de {MAX_PAGES_PER_HOST} pages (excédent non compté) : {c1['hosts_over_cap']}")
    out.append(f"C2 BUG_CONFIRMED [{c2['status']}] : {c2['true_positive']}/{c2['bug_confirmed_rows']} confirmés, "
               f"{len(c2['false_positive'])} faux positif(s), {c2['unreviewed']} non revu(s)")
    for f in c2["false_positive"]:
        out.append(f"   FAUX POSITIF : {f}")
    out.append(f"C3 SUSPECTED [{c3['status']}] : {c3['unreviewed_suspected']} non revu(s)")
    for sig, s in c3["signals"].items():
        out.append(f"   {sig} ({s['level']}) : vrais={s['tp']} faux={s['fp']} incertains={s['unsure']} "
                   f"précision={s['precision']} -> {s['decision']}")
    out.append(f"C4 angle mort (informatif) : {c4['count']} cas")
    for it in c4["items"]:
        out.append(f"   {it}")
    if report["invalid_truth_rows"]:
        out.append(f"\nATTENTION : {report['invalid_truth_rows']} ligne(s) à `truth` invalide ignorée(s) "
                   f"(valeurs permises : {sorted(VALID_TRUTH)})")
    out.append(f"\n{report['manual_criteria']}")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("template", help="écrire/compléter le CSV d'annotation")
    t.add_argument("--root", type=Path, default=_default_root())
    t.add_argument("--output", type=Path, default=Path("bdl4_annotations.csv"))
    r = sub.add_parser("report", help="évaluer les critères C1 à C4")
    r.add_argument("--root", type=Path, default=_default_root())
    r.add_argument("--annotations", type=Path, default=Path("bdl4_annotations.csv"))
    r.add_argument("--json", type=Path, help="écrire aussi le rapport en JSON")
    args = parser.parse_args()

    if args.cmd == "template":
        return cmd_template(args.root, args.output)
    if not args.annotations.is_file():
        print(f"CSV introuvable : {args.annotations} (lance d'abord `template`)", file=sys.stderr)
        return 2
    report = build_report(args.root, args.annotations)
    print(render(report))
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())