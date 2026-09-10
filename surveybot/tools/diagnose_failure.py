"""
diagnose_failure.py — Produit un diagnostic structuré (diagnosis.json) à partir d'un
failure_case (Phase 2) et de son replay (Phase 3, calculé à la volée via
Survey/failure_replay.py) : symptôme observé, comportement attendu, cause (certain/
probable/plausible) avec justification traçable, modules probablement concernés,
confiance globale.

Outil de diagnostic en lecture seule : ne modifie jamais le case ni le snapshot
source, ne génère aucun prompt pour un agent de coding, ne nomme aucune fonction à
modifier. Toute la logique vit dans Survey/failure_diagnosis.py ; ce script n'est
qu'une façade CLI.

Usage :
    python tools\\diagnose_failure.py failure_cases\\case_<id>
    python tools\\diagnose_failure.py failure_cases\\case_<id> --out-root diagnoses
    python tools\\diagnose_failure.py failure_cases\\case_<id> --force
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Survey.failure_diagnosis import DiagnosisError, write_diagnosis  # noqa: E402


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Produit un diagnostic structuré (diagnosis.json) pour un failure_case — "
            "symptôme, comportement attendu, cause avec justification, modules probables."
        )
    )
    parser.add_argument("case_dir", help="Dossier du case (ex: failure_cases\\case_20260907_142347_action_validation_failure)")
    parser.add_argument(
        "--out-root",
        default="diagnoses",
        help="Dossier racine des diagnostics générés (défaut : diagnoses)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Régénère un diagnostic déjà existant (le supprime avant reconstruction)",
    )
    args = parser.parse_args(argv)

    try:
        out_file = write_diagnosis(args.case_dir, out_root=args.out_root, force=args.force)
    except DiagnosisError as exc:
        print(f"[ERREUR] {args.case_dir} : {exc}", file=sys.stderr)
        return 1

    import json
    data = json.loads(out_file.read_text(encoding="utf-8"))
    print(f"case                : {data['case_id']}")
    print(f"stage               : {data['stage']}")
    print(f"itype / target_id   : {data['itype']} / {data['target_id']}")
    print(f"provider_domain     : {data['provider_domain']}")
    print(f"symptom.failure_types : {data['symptom']['failure_types']}")
    print(f"replay.verdict      : {data['replay'].get('verdict')}")
    print(f"cause.level         : {data['cause']['level']}")
    print(f"cause.justification : {data['cause']['justification']}")
    print(f"confidence_global   : {data['confidence_global']}")
    print("modules_likely_involved :")
    for m in data["modules_likely_involved"]:
        print(f"  - {m['module']} (signaux: {m['matched_signals']})")
    if not data["modules_likely_involved"]:
        print("  (aucun)")
    if data["warnings"]:
        for w in data["warnings"]:
            print(f"  ! {w}")
    print(f"-> {out_file}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
