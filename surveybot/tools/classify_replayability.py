"""
classify_replayability.py — Classe un failure case déjà diagnostiqué (Phase 4,
diagnosis.json) selon son mode de rejouabilité (Phase 3D) : STATIC_DOM /
BROWSER_CAPSULE / TRACE_REPLAY / EXTERNAL_NON_REPLAYABLE, ou UNDETERMINED si aucun
signal déjà disponible ne permet de choisir avec une confiance suffisante.

Outil de classification en lecture seule : ne recalcule ni ne réexécute rien (aucun
replay, aucun dispatcher, aucun navigateur) — lit exclusivement diagnosis.json déjà
produit par Survey/failure_diagnosis.py. Toute la logique vit dans
Survey/replayability_classifier.py ; ce script n'est qu'une façade CLI.

Usage :
    python tools\\classify_replayability.py diagnoses\\<case_id>
    python tools\\classify_replayability.py diagnoses\\<case_id> --out-root replayability
    python tools\\classify_replayability.py diagnoses\\<case_id> --force
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Survey.replayability_classifier import ReplayabilityError, write_classification  # noqa: E402


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Classe un failure case déjà diagnostiqué selon son mode de rejouabilité "
            "(STATIC_DOM/BROWSER_CAPSULE/TRACE_REPLAY/EXTERNAL_NON_REPLAYABLE/UNDETERMINED)."
        )
    )
    parser.add_argument(
        "diagnosis_dir",
        help="Dossier de diagnostic (ex: diagnoses\\20260907_142347_action_validation_failure)",
    )
    parser.add_argument(
        "--out-root",
        default="replayability",
        help="Dossier racine des classifications générées (défaut : replayability)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Régénère une classification déjà existante (la supprime avant reconstruction)",
    )
    args = parser.parse_args(argv)

    try:
        out_file = write_classification(args.diagnosis_dir, out_root=args.out_root, force=args.force)
    except ReplayabilityError as exc:
        print(f"[ERREUR] {args.diagnosis_dir} : {exc}", file=sys.stderr)
        return 1

    import json
    data = json.loads(out_file.read_text(encoding="utf-8"))
    print(f"case    : {data['case_id']}")
    print(f"stage   : {data['stage']}")
    print(f"mode    : {data['mode']}")
    print(f"reason  : {data['reason']}")
    print(f"signals : {data['signals']}")
    print(f"-> {out_file}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
