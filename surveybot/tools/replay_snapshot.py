#!/usr/bin/env python3
# tools/replay_snapshot.py
"""
Replay offline d'un snapshot DOM pour valider dom_analyzer.

Usage (depuis la racine du projet) :
  python tools/replay_snapshot.py ./snapshots/20260111_180000_after_dom_analyze
  python tools/replay_snapshot.py ./snapshots/.../dom_outer.html

Le script :
- charge dom_outer.html
- appelle Survey.dom_analyzer.analyze_dom() (via un driver offline minimal)
  ou Survey.dom_analyzer.analyze_html() si cette fonction existe
- compare au baseline question_blocks.json (si présent)
- écrit question_blocks.new.json dans le dossier snapshot
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------
# Driver offline minimal
# ---------------------------
class OfflineDriver:
    """
    Simule le strict minimum d'un Selenium driver pour les analyseurs
    qui ne font que driver.execute_script(...) / driver.page_source.
    """

    def __init__(self, html: str, url: str = "", title: str = "") -> None:
        self._html = html
        self._url = url
        self._title = title
        self.page_source = html  # fallback si le code utilise driver.page_source

    @property
    def current_url(self) -> str:
        return self._url

    def execute_script(self, script: str, *args, **kwargs):
        s = script or ""
        if "document.documentElement.outerHTML" in s:
            return self._html
        if "return document.title" in s or "document.title" in s:
            return self._title
        return None


# ---------------------------
# Normalisation / comparaison
# ---------------------------
def _as_dict(obj: Any) -> Dict[str, Any]:
    """Convertit dict/dataclass/objet en dict JSON-friendly."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    # dataclass / objet standard
    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)
    return {"value": obj}


def _pick(d: Dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _block_key(b: Dict[str, Any]) -> str:
    # On essaie d'abord les IDs stables
    qid = _pick(b, "qid", "q_id", "question_id")
    tid = _pick(b, "target_id", "targetId", "dom_target_id")
    it = _pick(b, "itype", "input_type", "type")
    label = _pick(b, "label", "question", "text")
    if qid:
        return f"qid:{qid}"
    if tid:
        return f"target:{tid}"
    # fallback : moins stable mais utile si pas d'IDs
    return f"sig:{(it or 'na')}:{(str(label)[:80] if label else 'no_label')}"


def _canonical(b: Dict[str, Any]) -> Dict[str, Any]:
    """Réduit un block à un sous-ensemble stable pour comparer facilement."""
    c: Dict[str, Any] = {}

    c["key"] = _block_key(b)
    c["qid"] = _pick(b, "qid", "q_id", "question_id")
    c["target_id"] = _pick(b, "target_id", "targetId", "dom_target_id")
    c["itype"] = _pick(b, "itype", "input_type", "type")
    c["label"] = _pick(b, "label", "question", "text")

    # options peuvent être volumineuses — on compare surtout le COUNT + un aperçu
    opts = _pick(b, "options", "choices", "items") or []
    if isinstance(opts, (list, tuple)):
        c["options_count"] = len(opts)
        c["options_preview"] = [str(x) for x in list(opts)[:5]]
    else:
        c["options_count"] = None
        c["options_preview"] = None

    c["max_select"] = _pick(b, "max_select", "maxSelect", "max_choices")
    c["scope_hint"] = _pick(b, "scope_hint", "dom_scope_hint", "context_scope")
    return c


def _diff_blocks(base: List[Dict[str, Any]], new: List[Dict[str, Any]]) -> Dict[str, Any]:
    base_map = {_block_key(b): _canonical(b) for b in base}
    new_map = {_block_key(b): _canonical(b) for b in new}

    base_keys = set(base_map.keys())
    new_keys = set(new_map.keys())

    added = sorted(new_keys - base_keys)
    removed = sorted(base_keys - new_keys)

    changed: List[Tuple[str, Dict[str, Any]]] = []
    for k in sorted(base_keys & new_keys):
        if base_map[k] != new_map[k]:
            # Diff champ par champ (simple)
            diffs = {}
            for field in sorted(set(base_map[k].keys()) | set(new_map[k].keys())):
                if base_map[k].get(field) != new_map[k].get(field):
                    diffs[field] = {"base": base_map[k].get(field), "new": new_map[k].get(field)}
            changed.append((k, diffs))

    return {
        "counts": {"base": len(base), "new": len(new)},
        "added": added,
        "removed": removed,
        "changed": [{"key": k, "diff": d} for k, d in changed],
    }


def _itype_stats(blocks: List[Dict[str, Any]]) -> Dict[str, int]:
    c = Counter()
    for b in blocks:
        it = _pick(b, "itype", "input_type", "type") or "unknown"
        c[str(it)] += 1
    return dict(c)


# ---------------------------
# Main
# ---------------------------
def _resolve_paths(p: Path) -> Tuple[Path, Path]:
    """
    Accepte :
      - un dossier snapshot (contient dom_outer.html)
      - un fichier dom_outer.html
    Retourne (snapshot_dir, dom_outer_path)
    """
    if p.is_dir():
        dom = p / "dom_outer.html"
        if not dom.exists():
            raise FileNotFoundError(f"dom_outer.html introuvable dans: {p}")
        return p, dom

    if p.is_file():
        if p.name != "dom_outer.html":
            # on tolère mais on avertit
            pass
        return p.parent, p

    raise FileNotFoundError(f"Chemin introuvable: {p}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", help="Dossier snapshot ou chemin vers dom_outer.html")
    parser.add_argument(
        "--baseline",
        default=None,
        help="Chemin vers question_blocks.json (par défaut: <snapshot_dir>/question_blocks.json)",
    )
    args = parser.parse_args()

    # Ajoute la racine du projet au PYTHONPATH
    # (tools/replay_snapshot.py => racine = parent de tools/)
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))

    snap_dir, dom_path = _resolve_paths(Path(args.path).resolve())

    html = dom_path.read_text(encoding="utf-8", errors="ignore")

    meta_path = snap_dir / "meta.json"
    url = ""
    title = ""
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            url = meta.get("url") or ""
            title = meta.get("title") or ""
        except Exception:
            pass

    # Import dom_analyzer
    try:
        from Survey import dom_analyzer  # type: ignore
    except Exception as e:
        print("❌ Impossible d'importer Survey.dom_analyzer")
        print(f"   Racine projet détectée: {project_root}")
        print(f"   Erreur: {e}")
        return 2

    # Analyse
    try:
        if hasattr(dom_analyzer, "analyze_html"):
            new_blocks = dom_analyzer.analyze_html(html)  # type: ignore
        elif hasattr(dom_analyzer, "analyze_dom_from_html"):
            new_blocks = dom_analyzer.analyze_dom_from_html(html)  # type: ignore
        else:
            drv = OfflineDriver(html=html, url=url, title=title)
            new_blocks = dom_analyzer.analyze_dom(drv)  # type: ignore
    except Exception as e:
        print("❌ Échec de l'analyse offline.")
        print("   Cause probable: analyze_dom() dépend de Selenium WebElement/find_elements().")
        print(f"   Exception: {repr(e)}")
        return 3

    # Normalise en list[dict]
    new_list = [_as_dict(x) for x in (new_blocks or [])]

    # Écrit résultat
    out_path = snap_dir / "question_blocks.new.json"
    out_path.write_text(json.dumps(new_list, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ Nouveau résultat écrit: {out_path}")

    # Baseline
    baseline_path = Path(args.baseline).resolve() if args.baseline else (snap_dir / "question_blocks.json")
    if not baseline_path.exists():
        print(f"⚠️ Baseline introuvable: {baseline_path}")
        print("   (Tu peux comparer manuellement question_blocks.json vs question_blocks.new.json)")
        print("   Stats itype (new):", _itype_stats(new_list))
        return 0

    base = json.loads(baseline_path.read_text(encoding="utf-8"))
    base_list = [_as_dict(x) for x in (base or [])]

    diff = _diff_blocks(base_list, new_list)

    print("\n--- Résumé comparaison ---")
    print("Counts:", diff["counts"])
    print("iType base:", _itype_stats(base_list))
    print("iType new :", _itype_stats(new_list))
    print("Added  :", len(diff["added"]))
    print("Removed:", len(diff["removed"]))
    print("Changed:", len(diff["changed"]))

    # Détails succincts (pour ne pas spam)
    if diff["added"]:
        print("\n+ Added keys (max 10):")
        for k in diff["added"][:10]:
            print("  ", k)

    if diff["removed"]:
        print("\n- Removed keys (max 10):")
        for k in diff["removed"][:10]:
            print("  ", k)

    if diff["changed"]:
        print("\n* Changed (max 5):")
        for item in diff["changed"][:5]:
            print("  ", item["key"])
            # affiche 3 champs qui bougent max
            fields = list(item["diff"].keys())[:3]
            for f in fields:
                print("     ", f, "=>", item["diff"][f])

    # Écrit diff machine-readable
    diff_path = snap_dir / "question_blocks.diff.json"
    diff_path.write_text(json.dumps(diff, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ Diff écrit: {diff_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
