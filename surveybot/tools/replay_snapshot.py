#!/usr/bin/env python3
# tools/replay_snapshot.py
"""
Rejoue un snapshot DOM dans un vrai navigateur, puis exécute Survey.dom_analyzer.analyze_dom()
et compare automatiquement avant/après via un fichier baseline.

Usage:
  python tools/replay_snapshot.py <snapshot_dir_or_dom_html> --save-baseline
  python tools/replay_snapshot.py <snapshot_dir_or_dom_html>
  python tools/replay_snapshot.py <snapshot_dir_or_dom_html> --use-page-source

Fichiers écrits dans le dossier snapshot:
  - dom_analyzer.out.json
  - dom_analyzer.baseline.json (si --save-baseline)
  - dom_analyzer.diff.json (si baseline existe)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple


# -----------------------------
# Helpers diff
# -----------------------------

def _sig(block: Dict[str, Any]) -> str:
    q = (block.get("question") or "").strip().lower()
    it = (block.get("itype") or "").strip().lower()
    return f"{it}|{q}"

def _index_blocks(blocks: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out = {}
    for b in blocks or []:
        out[_sig(b)] = b
    return out

def _summarize(blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_type: Dict[str, int] = {}
    for b in blocks or []:
        t = (b.get("itype") or "unknown").lower()
        by_type[t] = by_type.get(t, 0) + 1
    return {
        "total": len(blocks or []),
        "by_type": dict(sorted(by_type.items(), key=lambda x: (-x[1], x[0]))),
    }

def _diff_blocks(before: List[Dict[str, Any]], after: List[Dict[str, Any]]) -> Dict[str, Any]:
    b = _index_blocks(before)
    a = _index_blocks(after)

    added = sorted([k for k in a.keys() if k not in b])
    removed = sorted([k for k in b.keys() if k not in a])

    changed: List[Dict[str, Any]] = []
    for k in sorted(set(a.keys()) & set(b.keys())):
        bb = b[k]
        aa = a[k]
        # compare options + max_select (les plus importants pour toi)
        b_opts = bb.get("options") or []
        a_opts = aa.get("options") or []
        if (b_opts != a_opts) or (bb.get("max_select") != aa.get("max_select")):
            changed.append({
                "sig": k,
                "before": {"options": b_opts, "max_select": bb.get("max_select")},
                "after": {"options": a_opts, "max_select": aa.get("max_select")},
            })

    return {
        "summary_before": _summarize(before),
        "summary_after": _summarize(after),
        "added": added,
        "removed": removed,
        "changed": changed,
    }


# -----------------------------
# Driver launch (projet -> fallback selenium)
# -----------------------------

def _launch_driver(headful: bool = False):
    """
    1) Essaie le launcher du projet (preselection.playwright_launcher.launch_browser)
    2) Sinon fallback Selenium Chrome
    """
    # 1) launcher projet
    try:
        from preselection.config_loader import load_config
        from preselection.playwright_launcher import launch_browser  # type: ignore

        cfg = {}
        try:
            cfg = load_config() or {}
        except Exception:
            cfg = {}

        # Si ton launcher supporte un flag headless via config/env, tu peux l’adapter ici.
        if headful:
            os.environ["HEADLESS"] = "0"

        drv = launch_browser(cfg)
        return drv
    except Exception as e:
        print(f"[replay_snapshot] launcher projet indisponible -> fallback selenium. reason={type(e).__name__}: {e}")

    # 2) fallback selenium chrome
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options

        opt = Options()
        if not headful:
            # Chrome récent
            opt.add_argument("--headless=new")
        opt.add_argument("--disable-gpu")
        opt.add_argument("--no-sandbox")
        opt.add_argument("--disable-dev-shm-usage")

        drv = webdriver.Chrome(options=opt)
        return drv
    except Exception as e:
        raise RuntimeError(
            "Impossible de lancer un navigateur (ni launcher projet, ni Selenium Chrome). "
            "Vérifie que ton environnement local lance bien le bot normalement."
        ) from e


# -----------------------------
# Main
# -----------------------------

def _resolve_snapshot_paths(arg_path: str, use_page_source: bool) -> Tuple[Path, Path]:
    p = Path(arg_path).expanduser()

    # Si on pointe directement un fichier HTML
    if p.is_file():
        snap_dir = p.parent
        html_path = p
        return snap_dir, html_path

    # Sinon, on suppose un dossier snapshot
    snap_dir = p
    if not snap_dir.exists():
        raise FileNotFoundError(f"Snapshot introuvable: {snap_dir}")

    html_name = "page_source.html" if use_page_source else "dom_outer.html"
    html_path = snap_dir / html_name
    if not html_path.exists():
        raise FileNotFoundError(f"Fichier HTML manquant: {html_path}")

    return snap_dir, html_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="Dossier snapshot (ex: ./snapshots/20260113_214405_after_dom_analyze) ou fichier dom_outer.html")
    ap.add_argument("--use-page-source", action="store_true", help="Utiliser page_source.html au lieu de dom_outer.html")
    ap.add_argument("--save-baseline", action="store_true", help="Écrit dom_analyzer.baseline.json (pour faire le avant/après)")
    ap.add_argument("--headful", action="store_true", help="Lance le navigateur en mode visible (debug)")
    ap.add_argument("--no-classify", action="store_true", help="Ne pas exécuter dom_classifier.classify_dom()")
    args = ap.parse_args()

    snap_dir, html_path = _resolve_snapshot_paths(args.path, args.use_page_source)

    # imports Survey (assure l'import même si tu lances depuis ailleurs)
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from Survey import dom_analyzer  # type: ignore

    classify_info = None
    if not args.no_classify:
        try:
            from Survey import dom_classifier  # type: ignore
        except Exception:
            dom_classifier = None  # type: ignore

    out_file = snap_dir / "dom_analyzer.out.json"
    base_file = snap_dir / "dom_analyzer.baseline.json"
    diff_file = snap_dir / "dom_analyzer.diff.json"

    print(f"[replay_snapshot] snapshot_dir = {snap_dir}")
    print(f"[replay_snapshot] html         = {html_path.name}")

    driver = _launch_driver(headful=args.headful)
    try:
        # Ouvre le fichier local
        file_url = html_path.resolve().as_uri()
        try:
            driver.set_page_load_timeout(20)
        except Exception:
            pass

        driver.get(file_url)
        time.sleep(0.5)  # laisse le DOM se stabiliser

        # Optionnel: classification de page (super utile quand le type détecté est mauvais)
        if not args.no_classify:
            try:
                from Survey import dom_classifier  # type: ignore
                rule = dom_classifier.classify_dom(driver)
                classify_info = rule
                print(f"[replay_snapshot] classify_dom = {rule.get('itype') if rule else 'unclassified'}")
            except Exception as e:
                print(f"[replay_snapshot] classify_dom failed: {type(e).__name__}: {e}")

        # Analyse DOM
        blocks = dom_analyzer.analyze_dom(driver) or []
        summary = _summarize(blocks)

        payload = {
            "meta": {
                "source_html": html_path.name,
                "file_url": file_url,
                "ts": int(time.time()),
            },
            "classify_dom": classify_info,
            "summary": summary,
            "question_blocks": blocks,
        }

        out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[replay_snapshot] wrote {out_file.name}")
        print(f"[replay_snapshot] blocks: total={summary['total']} by_type={summary['by_type']}")

        # Baseline
        if args.save_baseline:
            base_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[replay_snapshot] wrote baseline {base_file.name}")

        # Diff si baseline existe
        if base_file.exists():
            try:
                before = json.loads(base_file.read_text(encoding="utf-8")).get("question_blocks") or []
                after = blocks
                d = _diff_blocks(before, after)
                diff_file.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"[replay_snapshot] wrote diff {diff_file.name}")
                print(f"[replay_snapshot] diff: +{len(d['added'])} -{len(d['removed'])} ~{len(d['changed'])}")
            except Exception as e:
                print(f"[replay_snapshot] diff failed: {type(e).__name__}: {e}")
        else:
            print("[replay_snapshot] pas de baseline -> lance avec --save-baseline pour créer le 'avant'.")

    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
