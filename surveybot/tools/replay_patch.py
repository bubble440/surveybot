"""
replay_patch.py — Phase 9. Rejoue le bug d'un case dans le worktree autofix déjà
préparé par la Phase 7 (branche/worktree patchés), avec le code présent dans ce
worktree — jamais avec le code du dépôt principal.

Ne s'exécute que si validation_static.json (Phase 8) existe pour ce case et
porte verdict="ACCEPTED" — sinon refus contrôlé avant tout effet de bord, sans
recalculer la Phase 8. Lit aussi, en lecture seule, worktree.json (Phase 7) et
diagnosis.json (Phase 4, pour le stage et le signal "avant patch" déjà établi —
jamais revérifié ici). Ne rouvre jamais manifest.json directement. Toute la
logique vit dans Survey/autofix/patch_replay.py ; ce script n'est qu'une façade CLI.

Convention reprise de tools/validate_patch_static.py (Phase 8) : worktree_manifest
et validation_static sont les CHEMINS COMPLETS vers worktree.json/
validation_static.json (pas un dossier qui les contiendrait).

Usage :
    python tools\\replay_patch.py failure_cases\\<case_id> diagnoses\\<case_id> autofix_worktrees\\<case_id>\\worktree.json autofix_static_validations\\<case_id>\\validation_static.json
    python tools\\replay_patch.py failure_cases\\<case_id> diagnoses\\<case_id> autofix_worktrees\\<case_id>\\worktree.json autofix_static_validations\\<case_id>\\validation_static.json --out-root patch_replays --force
    python tools\\replay_patch.py failure_cases\\<case_id> diagnoses\\<case_id> autofix_worktrees\\<case_id>\\worktree.json autofix_static_validations\\<case_id>\\validation_static.json --dispatch-budget-s 45
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Survey.autofix.patch_replay import (  # noqa: E402
    DEFAULT_EXTRACTION_TIMEOUT_S,
    PatchReplayError,
    write_patch_replay,
)
from Survey.autofix.replay_browser import _DEFAULT_DISPATCH_BUDGET_S  # noqa: E402


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Rejoue le bug d'un case dans le worktree autofix déjà préparé (Phase 7), avec le "
            "code patché de ce worktree — valide le patch seulement si le rejeu confirme "
            "activement la correction (CORRECTIF_CONFIRME)."
        )
    )
    parser.add_argument("failure_case_dir", help="Dossier du failure case (ex: failure_cases\\<case_id>)")
    parser.add_argument("diagnosis_dir", help="Dossier du diagnostic Phase 4 (ex: diagnoses\\<case_id>)")
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
        default="patch_replays",
        help="Dossier racine des résultats générés (défaut : patch_replays)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Régénère un résultat déjà existant (le supprime avant reconstruction)",
    )
    parser.add_argument(
        "--dispatch-budget-s",
        type=float,
        default=None,
        help=f"Budget de temps (s) du dispatcher réel pour stage=action (défaut : {_DEFAULT_DISPATCH_BUDGET_S})",
    )
    parser.add_argument(
        "--extraction-timeout-s",
        type=float,
        default=None,
        help=f"Budget de temps (s) du rejeu statique pour stage=extraction (défaut : {DEFAULT_EXTRACTION_TIMEOUT_S})",
    )
    args = parser.parse_args(argv)

    try:
        out_file = write_patch_replay(
            failure_case_dir=args.failure_case_dir,
            diagnosis_dir=args.diagnosis_dir,
            worktree_manifest_path=args.worktree_manifest,
            validation_static_path=args.validation_static,
            out_root=args.out_root,
            force=args.force,
            dispatch_budget_s=args.dispatch_budget_s,
            extraction_timeout_s=args.extraction_timeout_s,
        )
    except PatchReplayError as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 1

    import json
    data = json.loads(out_file.read_text(encoding="utf-8"))
    print(f"case             : {data['case_id']}")
    print(f"stage            : {data['stage']}")
    print(f"refused          : {data['refused']}")
    if data["refused"]:
        for reason in data["refusal_reasons"]:
            print(f"  ! {reason}")
    else:
        print(f"before_signal    : {data['before_signal']}")
        print(f"outcome          : {data['outcome']}")
        print(f"patch_validated  : {data['patch_validated']}")
        if data.get("after_replay_error"):
            print(f"after_replay_error : {data['after_replay_error']}")
        for warning in data["warnings"]:
            print(f"  ! {warning}")
    print(f"-> {out_file}")

    return 0 if not data["refused"] and data.get("patch_validated") else 1


if __name__ == "__main__":
    sys.exit(main())
