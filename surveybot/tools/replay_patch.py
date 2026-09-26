"""
replay_patch.py — Phase 9. Rejoue le bug d'un case dans le worktree autofix déjà
préparé par la Phase 7 (branche/worktree patchés), avec le code présent dans ce
worktree — jamais avec le code du dépôt principal.

Ne s'exécute que si validation_static.json (Phase 8) existe pour ce case et
porte verdict="ACCEPTED" — sinon refus contrôlé avant tout effet de bord, sans
recalculer la Phase 8. Lit aussi, en lecture seule, worktree.json (Phase 7) et
diagnosis.json (Phase 4, pour le stage et le signal "avant patch" déjà établi —
jamais revérifié ici). Ne rouvre jamais manifest.json directement. Toute la
logique vit dans Survey/patch_replay.py ; ce script n'est qu'une façade CLI.

Écart important à connaître avant d'utiliser cet outil (documenté en détail
dans Survey/patch_replay.py) : ni Survey/static_validator.py (Phase 8) ni un
worktree.json persisté par la Phase 7 n'existent encore dans ce dépôt au moment
de ce patch — cet outil définit et vérifie leur contrat attendu, il ne les
produit pas. Tant qu'un case réel ne porte pas ces deux fichiers, cet outil
refusera systématiquement, par construction.

Usage :
    python tools\\replay_patch.py failure_cases\\<case_id> diagnoses\\<case_id> worktrees\\<case_id> validations\\<case_id>
    python tools\\replay_patch.py failure_cases\\<case_id> diagnoses\\<case_id> worktrees\\<case_id> validations\\<case_id> --out-root patch_replays --force
    python tools\\replay_patch.py failure_cases\\<case_id> diagnoses\\<case_id> worktrees\\<case_id> validations\\<case_id> --dispatch-budget-s 45
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Survey.patch_replay import PatchReplayError, write_patch_replay  # noqa: E402


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
    parser.add_argument("worktree_dir", help="Dossier portant worktree.json (Phase 7, ex: worktrees\\<case_id>)")
    parser.add_argument(
        "validation_dir",
        help="Dossier portant validation_static.json (Phase 8, ex: validations\\<case_id>)",
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
        help="Budget de temps (s) du dispatcher réel pour stage=action (défaut : celui de Survey/replay_browser.py)",
    )
    args = parser.parse_args(argv)

    try:
        out_file = write_patch_replay(
            failure_case_dir=args.failure_case_dir,
            diagnosis_dir=args.diagnosis_dir,
            worktree_dir=args.worktree_dir,
            validation_dir=args.validation_dir,
            out_root=args.out_root,
            force=args.force,
            dispatch_budget_s=args.dispatch_budget_s,
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
