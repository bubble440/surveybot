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


def _infer_needs_browser_reason(soup: BeautifulSoup, blocks: List[Dict[str, Any]], case_spec: Optional[Dict[str, Any]]) -> Optional[str]:
    if not case_spec:
        return None

    exp = case_spec.get("assert", {})
    if not blocks:
        if soup.select("iframe, frame"):
            return "needs_browser: iframe content not captured in static snapshot"
        html_text = str(soup).lower()
        if "shadowroot" in html_text or "shadow-root" in html_text:
            return "needs_browser: shadow DOM content not available in static snapshot"
        if soup.select("script"):
            return "needs_browser: dynamic rendering (no inputs in static DOM; options likely injected by JS)"
        return "needs_browser: dynamic rendering (no inputs in static DOM)"

    if "must_have_radio_group" in exp:
        gk = exp["must_have_radio_group"].get("group_key")
        if gk and not any((b.get("itype") == "radio" and (b.get("context") or {}).get("group_key") == gk) for b in blocks):
            return f"needs_browser: expected radio group '{gk}' not found in static snapshot"

    if "must_have_checkbox_group" in exp:
        gk = exp["must_have_checkbox_group"].get("group_key")
        if gk and not any((b.get("itype") == "checkbox" and (b.get("context") or {}).get("group_key") == gk) for b in blocks):
            return f"needs_browser: expected checkbox group '{gk}' not found in static snapshot"

    if "must_have_dropdown_input" in exp and not any(b.get("itype") == "dropdown" for b in blocks):
        return "needs_browser: expected dropdown missing (options likely injected by JS)"

    if "must_have_continue_button" in exp:
        btn = exp["must_have_continue_button"]
        if not any(
            b.get("itype") == "button"
            and (b.get("context") or {}).get("id") == btn.get("id")
            and (b.get("context") or {}).get("name") == btn.get("name")
            for b in blocks
        ):
            return "needs_browser: expected continue button not found in static DOM"

    return None


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

    summary = _summarize(blocks)
    needs_browser_reason = _infer_needs_browser_reason(soup, blocks, case_spec)

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
