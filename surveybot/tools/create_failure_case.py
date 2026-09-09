"""
create_failure_case.py — Convertit un ou plusieurs snapshots d'observabilité
(snapshots/*_validation_failure/) en cases normalisés sous failure_cases/case_<id>/.

Outil autonome de transformation, en lecture seule sur le pipeline bot : ne modifie
ni ne déplace jamais les snapshots source, n'exécute aucun diagnostic, aucun replay,
aucune réparation. Toute la logique d'extraction vit dans
Survey/failure_case_builder.py ; ce script n'est qu'une façade CLI.

Usage :
    python tools\\create_failure_case.py <snapshot_dir> [<snapshot_dir> ...]
    python tools\\create_failure_case.py <snapshot_dir> --out-root failure_cases
    python tools\\create_failure_case.py <snapshot_dir> --force

Par défaut, un case déjà existant pour un snapshot donné fait échouer la commande
pour ce snapshot (pas d'écrasement silencieux) ; --force le régénère.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Survey.failure_case_builder import FailureCaseError, build_failure_case  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Convertit un snapshot d'observabilité (action_validation_failure / "
            "extraction_validation_failure) en case normalisé failure_cases/case_<id>/."
        )
    )
    parser.add_argument(
        "snapshot",
        nargs="+",
        help="Dossier(s) snapshot source (ex: ..\\snapshots\\20260907_142347_action_validation_failure)",
    )
    parser.add_argument(
        "--out-root",
        default="failure_cases",
        help="Dossier racine des cases générés (défaut : failure_cases)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Régénère un case déjà existant (le supprime avant reconstruction)",
    )
    args = parser.parse_args(argv)

    failures = 0
    for snapshot in args.snapshot:
        try:
            case_dir = build_failure_case(snapshot, out_root=args.out_root, force=args.force)
            print(f"[OK] {snapshot} -> {case_dir}")
        except FailureCaseError as exc:
            print(f"[ERREUR] {snapshot} : {exc}", file=sys.stderr)
            failures += 1

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
