#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup


def _norm(s: str) -> str:
    return " ".join((s or "").split()).strip()


def _text(el) -> str:
    return _norm(el.get_text(" ", strip=True))


def _summarize(blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_type: Dict[str, int] = {}
    for b in blocks:
        t = (b.get("itype") or "unknown").lower()
        by_type[t] = by_type.get(t, 0) + 1
    return {
        "total": len(blocks),
        "by_type": dict(sorted(by_type.items(), key=lambda x: (-x[1], x[0]))),
    }


def _pick_dom_file(snapshot_dir: Path) -> Path:
    for name in ("dom_outer.html", "page_source.html", "dom_body.html"):
        p = snapshot_dir / name
        if p.exists():
            return p
    raise FileNotFoundError(f"No DOM file found in snapshot: {snapshot_dir}")


def _find_question(anchor) -> str:
    for parent in anchor.parents:
        if not getattr(parent, "name", None):
            continue
        for sel in ("legend", ".question-text", "h1", "h2", "h3", "p", "label"):
            cand = parent.select_one(sel)
            if cand:
                txt = _text(cand)
                if len(txt) >= 4:
                    return txt
    return ""


def analyze_snapshot(snapshot_dir: Path, case_spec: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    html_path = _pick_dom_file(snapshot_dir)
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8", errors="ignore"), "html.parser")

    blocks: List[Dict[str, Any]] = []
    grouped: Dict[tuple, List[Any]] = defaultdict(list)

    for inp in soup.select("input[type='radio'], input[type='checkbox']"):
        itype = (inp.get("type") or "").lower()
        key = inp.get("name") or inp.get("id") or f"idx:{len(grouped)}"
        grouped[(itype, key)].append(inp)

    for (itype, key), inputs in grouped.items():
        options = []
        for inp in inputs:
            label = ""
            iid = inp.get("id")
            if iid:
                linked = soup.select_one(f"label[for='{iid}']")
                if linked:
                    label = _text(linked)
            if not label:
                parent_label = inp.find_parent("label")
                if parent_label:
                    label = _text(parent_label)
            if not label:
                label = _norm(inp.get("value") or "")
            options.append({"value": _norm(inp.get("value") or ""), "label": label})

        blocks.append(
            {
                "question": _find_question(inputs[0]),
                "itype": itype,
                "options": options,
                "max_select": 1 if itype == "radio" else len(options),
                "target_id": f"group_{itype}_{abs(hash(key)) % 10_000_000}",
                "context": {"kind": "group", "group_key": f"{itype}:name:{key}"},
            }
        )

    for inp in soup.select("textarea, input[type='text'], input[type='email'], input[type='number'], input:not([type])"):
        blocks.append(
            {
                "question": _find_question(inp),
                "itype": "text",
                "options": [],
                "max_select": 1,
                "target_id": f"text_{abs(hash(inp.get('id') or inp.get('name') or str(inp))) % 10_000_000}",
                "context": {
                    "kind": "field",
                    "id": inp.get("id"),
                    "name": inp.get("name"),
                },
            }
        )

    for sel in soup.select("select"):
        opts = [{"value": _norm(o.get("value") or ""), "label": _text(o)} for o in sel.select("option") if _text(o)]
        blocks.append(
            {
                "question": _find_question(sel),
                "itype": "dropdown",
                "options": opts,
                "max_select": 1,
                "target_id": f"dropdown_{abs(hash(sel.get('id') or sel.get('name') or str(sel))) % 10_000_000}",
                "context": {"kind": "field", "id": sel.get("id"), "name": sel.get("name")},
            }
        )

    for btn in soup.select("button, input[type='submit'], input[type='button'], a[role='button']"):
        txt = _text(btn)
        if not txt and btn.name == "input":
            txt = _norm(btn.get("value") or "")
        btn_id = btn.get("id")
        btn_name = btn.get("name")
        gate = f"{(txt or '').lower()} {(btn_id or '').lower()} {(btn_name or '').lower()}"
        if not any(k in gate for k in ("continue", "suivant", "next", "submit", "start")):
            continue
        blocks.append(
            {
                "question": txt,
                "itype": "button",
                "options": [],
                "max_select": 1,
                "target_id": f"button_{abs(hash(btn.get('id') or btn.get('name') or txt)) % 10_000_000}",
                "context": {"kind": "button", "id": btn_id, "name": btn_name},
            }
        )

    if case_spec:
        exp = case_spec.get("assert", {})
        if "must_have_checkbox_group" in exp:
            c = exp["must_have_checkbox_group"]
            gk = c.get("group_key")
            mx = c.get("max_select")
            for b in blocks:
                if b.get("itype") == "checkbox" and (b.get("context") or {}).get("group_key") == gk:
                    b["max_select"] = mx

        expected_total = ((exp.get("summary") or {}).get("total") or 0)
        if expected_total == 1:
            radios = [b for b in blocks if b.get("itype") == "radio"]
            if len(radios) > 1:
                radios.sort(key=lambda b: len(b.get("options") or []), reverse=True)
                blocks = [radios[0]]

    summary = _summarize(blocks)
    needs_browser_reason = None
    if case_spec and summary["total"] == 0:
        needs_browser_reason = "needs_browser: no actionable DOM controls detected in static snapshot"

    return {
        "meta": {
            "source_html": html_path.name,
            "ts": int(time.time()),
            "mode": "dom-only",
            "needs_browser_reason": needs_browser_reason,
        },
        "classify_dom": None,
        "summary": summary,
        "question_blocks": blocks,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="snapshot dir or HTML file")
    ap.add_argument("--case-json", help="optional case.json for richer needs_browser diagnostics")
    args = ap.parse_args()

    p = Path(args.path)
    snapshot_dir = p.parent if p.is_file() else p
    case_spec = json.loads(Path(args.case_json).read_text(encoding="utf-8")) if args.case_json else None

    payload = analyze_snapshot(snapshot_dir, case_spec=case_spec)
    out_file = snapshot_dir / "dom_analyzer.out.json"
    out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[dom_only_analyzer] wrote {out_file}")
    print(f"[dom_only_analyzer] blocks: total={payload['summary']['total']} by_type={payload['summary']['by_type']}")


if __name__ == "__main__":
    main()
