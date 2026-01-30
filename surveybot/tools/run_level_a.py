import json
import subprocess
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent

# Cherche la racine du projet (dossier contenant "cases")
ROOT = next((p for p in [THIS_DIR] + list(THIS_DIR.parents) if (p / "cases").is_dir()), None)
if ROOT is None:
    print("[LEVEL_A] Could not locate project root containing 'cases' directory")
    sys.exit(1)

CASES_DIR = ROOT / "cases" / "level_a"
REPLAY_SNAPSHOT = THIS_DIR / "replay_snapshot.py"

def die(msg: str) -> None:
    print(msg)
    sys.exit(1)

def load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))

def find_block(blocks, itype: str, predicate):
    for b in blocks:
        if b.get("itype") != itype:
            continue
        if predicate(b):
            return b
    return None

def run_replay(snapshot_dir: Path) -> None:
    cmd = [
        sys.executable,
        str(REPLAY_SNAPSHOT),
        str(snapshot_dir),
        "--use-dom-outer",
        "--use-project-launcher",
    ]
    # On ne save pas baseline ici. Baseline = une action volontaire.
    r = subprocess.run(cmd, cwd=str(ROOT))
    if r.returncode != 0:
        die(f"[LEVEL_A] replay_snapshot FAILED: {snapshot_dir}")

def assert_case(case_dir: Path) -> None:
    spec = load_json(case_dir / "case.json")
    snapshot_dir = case_dir / spec["snapshot_dir"]

    run_replay(snapshot_dir)

    out_path = snapshot_dir / "dom_analyzer.out.json"
    if not out_path.exists():
        die(f"[LEVEL_A] Missing dom_analyzer.out.json: {out_path}")

    out = load_json(out_path)
    blocks = out.get("question_blocks", [])
    summary = out.get("summary", {})

    exp = spec["assert"]

    # 1) Summary
    if summary.get("total") != exp["summary"]["total"]:
        die(f"[LEVEL_A] {spec['name']} summary.total mismatch: {summary.get('total')} != {exp['summary']['total']}")

    by_type = summary.get("by_type", {})
    for k, v in exp["summary"]["by_type"].items():
        if by_type.get(k) != v:
            die(f"[LEVEL_A] {spec['name']} summary.by_type[{k}] mismatch: {by_type.get(k)} != {v}")

    # 2) Un seul "must_have_*" requis : radio OU checkbox OU text OU dropdown (prédictible)
    if "must_have_radio_group" in exp:
        gk = exp["must_have_radio_group"]["group_key"]
        min_opts = exp["must_have_radio_group"]["min_options"]
        max_select = exp["must_have_radio_group"]["max_select"]

        radio = find_block(
            blocks, "radio",
            lambda b: (b.get("context") or {}).get("group_key") == gk
        )
        if not radio:
            die(f"[LEVEL_A] {spec['name']} missing radio group_key={gk}")

        if radio.get("max_select") != max_select:
            die(f"[LEVEL_A] {spec['name']} radio.max_select mismatch: {radio.get('max_select')} != {max_select}")

        opts = radio.get("options") or []
        if len(opts) < min_opts:
            die(f"[LEVEL_A] {spec['name']} radio.options too small: {len(opts)} < {min_opts}")

    elif "must_have_checkbox_group" in exp:
        gk = exp["must_have_checkbox_group"]["group_key"]
        min_opts = exp["must_have_checkbox_group"]["min_options"]
        max_select = exp["must_have_checkbox_group"]["max_select"]

        cb = find_block(
            blocks, "checkbox",
            lambda b: (b.get("context") or {}).get("group_key") == gk
        )
        if not cb:
            die(f"[LEVEL_A] {spec['name']} missing checkbox group_key={gk}")

        if cb.get("max_select") != max_select:
            die(f"[LEVEL_A] {spec['name']} checkbox.max_select mismatch: {cb.get('max_select')} != {max_select}")

        opts = cb.get("options") or []
        if len(opts) < min_opts:
            die(f"[LEVEL_A] {spec['name']} checkbox.options too small: {len(opts)} < {min_opts}")

    elif "must_have_text_input" in exp:
        exp_txt = exp["must_have_text_input"]
        exp_itype = exp_txt.get("itype", "text")
        exp_id = exp_txt.get("id")
        exp_name = exp_txt.get("name")
        q_contains = (exp_txt.get("question_contains") or "").strip().lower()

        txt = find_block(
            blocks, exp_itype,
            lambda b: ((b.get("context") or {}).get("id") == exp_id)
                   and ((b.get("context") or {}).get("name") == exp_name)
        )
        if not txt:
            die(f"[LEVEL_A] {spec['name']} missing text input id={exp_id} name={exp_name}")

        if q_contains:
            q = (txt.get("question") or "").strip().lower()
            if q_contains not in q:
                die(f"[LEVEL_A] {spec['name']} text.question mismatch: expected contains '{q_contains}' got '{q}'")

    elif "must_have_dropdown_input" in exp:
        exp_dd = exp["must_have_dropdown_input"]
        min_opts = int(exp_dd.get("min_options", 1) or 1)
        max_select = int(exp_dd.get("max_select", 1) or 1)
        q_contains = (exp_dd.get("question_contains") or "").strip().lower()

        dd = find_block(
            blocks, "dropdown",
            lambda b: True
        )
        # raffine : on cherche un dropdown dont la question contient q_contains
        if q_contains:
            dd = find_block(
                blocks, "dropdown",
                lambda b: q_contains in ((b.get("question") or "").strip().lower())
            )

        if not dd:
            die(f"[LEVEL_A] {spec['name']} missing dropdown (question_contains='{q_contains}')")

        if int(dd.get("max_select", 1) or 1) != max_select:
            die(f"[LEVEL_A] {spec['name']} dropdown.max_select mismatch: {dd.get('max_select')} != {max_select}")

        opts = dd.get("options") or []
        if len(opts) < min_opts:
            die(f"[LEVEL_A] {spec['name']} dropdown.options too small: {len(opts)} < {min_opts}")

    else:
        die(f"[LEVEL_A] {spec['name']} missing must_have_radio_group or must_have_checkbox_group or must_have_text_input or must_have_dropdown_input in case.json")

    # 3) Continue button (OPTIONNEL)
    if "must_have_continue_button" in exp:
        exp_btn = exp["must_have_continue_button"]
        btn = find_block(
            blocks, "button",
            lambda b: ((b.get("context") or {}).get("id") == exp_btn["id"])
                      and ((b.get("context") or {}).get("name") == exp_btn["name"])
        )
        if not btn:
            die(f"[LEVEL_A] {spec['name']} missing Continue button id={exp_btn['id']} name={exp_btn['name']}")

    print(f"[LEVEL_A] PASS: {spec['name']}")

def main():
    if not CASES_DIR.exists():
        die(f"Missing cases dir: {CASES_DIR}")

    case_jsons = sorted(CASES_DIR.glob("*/case.json"))
    if not case_jsons:
        die(f"No cases found in: {CASES_DIR}")

    print(f"[LEVEL_A] Running {len(case_jsons)} case(s)...")

    failures = 0
    for cj in case_jsons:
        try:
            assert_case(cj.parent)
        except SystemExit:
            # die() -> sys.exit(1) : on capture pour continuer sur les autres cas
            failures += 1
            continue
        except Exception as e:
            failures += 1
            print(f"[LEVEL_A][ERROR] {cj.parent.name}: {type(e).__name__}: {e}")

    if failures:
        print(f"[LEVEL_A] FAILURES: {failures}/{len(case_jsons)}")
        sys.exit(1)

    print(f"[LEVEL_A] ALL PASS ({len(case_jsons)}/{len(case_jsons)})")

if __name__ == "__main__":
    main()
