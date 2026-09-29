"""Façade CLI de la Phase 11-A, comparaison baseline/base_sha/patch.

Une intention de changement direct du core se fournit explicitement avec les
preuves de diagnostic et de rejeu. La Phase 11-B reste différée.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Survey.autofix.extractor_integrity_gate import (  # noqa: E402
    DEFAULT_TIME_BUDGET_S,
    IntegrityGateError,
    write_extractor_integrity_check,
)


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Vérifie l'intégrité des fonctions gelées (Survey/extractor_integrity.py/.json) d'un "
            "patch autofix déjà validé statiquement (Phase 8) — hash recalculé sur le code du "
            "worktree, jamais sur celui du dépôt principal."
        )
    )
    parser.add_argument(
        "worktree_manifest",
        help="Chemin de worktree.json produit par la Phase 7 (ex: autofix_worktrees\\<case_id>\\worktree.json)",
    )
    parser.add_argument(
        "validation_static",
        help=(
            "Chemin de validation_static.json produit par la Phase 8 "
            "(ex: autofix_static_validations\\<case_id>\\validation_static.json)"
        ),
    )
    parser.add_argument(
        "--out-root",
        default="extractor_integrity_checks",
        help="Dossier racine des résultats générés (défaut : extractor_integrity_checks)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Régénère un résultat déjà existant (le supprime avant reconstruction)",
    )
    parser.add_argument(
        "--time-budget-s",
        type=float,
        default=DEFAULT_TIME_BUDGET_S,
        help=f"Budget de temps (s) pour parcourir le registre, défaut={DEFAULT_TIME_BUDGET_S}",
    )
    parser.add_argument("--expected-changes", help="Déclaration locale de changement attendu, hors du worktree patché")
    parser.add_argument("--diagnosis", help="diagnosis.json du même case, requis avec --expected-changes")
    parser.add_argument("--patch-replay", help="patch_replay.json du même case, requis avec --expected-changes")
    args = parser.parse_args(argv)

    try:
        out_file = write_extractor_integrity_check(
            args.worktree_manifest,
            args.validation_static,
            out_root=args.out_root,
            force=args.force,
            time_budget_s=args.time_budget_s,
            expected_changes_path=args.expected_changes,
            diagnosis_path=args.diagnosis,
            patch_replay_path=args.patch_replay,
        )
    except IntegrityGateError as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 1

    data = json.loads(out_file.read_text(encoding="utf-8"))
    print(f"case            : {data['case_id']}")
    print(f"branche         : {data['branch']}")
    print(f"registre        : {data['registry_path']}")
    print(f"entrées         : {data['checked_entries']}/{data['total_entries']} vérifiées")
    print(f"états           : {data['state_counts']}")
    if data["mismatches"]:
        print("FONCTIONS MODIFIÉES (hash différent du registre) :")
        for m in data["mismatches"]:
            print(f"  ! {m['key']}")
    if data["errors"]:
        print("ENTRÉES EN ERREUR :")
        for e in data["errors"]:
            print(f"  ? {e['key']} -- {e['reason']}")
    if data["budget_exceeded"]:
        print("  ! budget de temps dépassé — voir errors[].reason == budget_exceeded")
    for w in data["warnings"]:
        print(f"  ! {w}")
    print(f"verdict         : {data['verdict']}")
    print(f"-> {out_file}")

    return 0 if data["verdict"] == "ACCEPTED" else 1


if __name__ == "__main__":
    sys.exit(main())
