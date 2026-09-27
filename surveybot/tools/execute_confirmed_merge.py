"""
execute_confirmed_merge.py — exécution automatique du merge local d'un patch
autofix dès confirmation Telegram (merge_reviews/<case_id>/decision.json,
Phase 16, decision="APPROVED"), approuvé (Phase 13/15) et déjà committé sur sa
branche autofix isolée (commit_result.json, Phase 15, commit_sha non vide).

Se positionne sur la VRAIE branche cible (worktree.json.source_branch) DU
DÉPÔT PRINCIPAL — jamais dans le worktree isolé, jetable, lui-même — puis
exécute `git merge --no-ff <branche autofix>`. Vérifie d'abord qu'aucun merge
n'a déjà eu lieu pour ce case_id (idempotent, jamais un doublon). Exige un
dépôt principal entièrement propre avant tout basculement de branche — jamais
un stash automatique. En cas de conflit Git réel : abandonne proprement
(git merge --abort), signale explicitement pour résolution manuelle — jamais
une résolution automatique. Ne touche jamais à main/prod/playwright-migration
ni à aucune branche protégée (PROTECTED_BRANCHES), que ce soit comme branche
autofix ou comme cible. Jamais de push, jamais de déclenchement de release.

Toute la logique vit dans Survey/merge_executor.py ; ce script n'est qu'une
façade CLI.

Usage :
    python tools\\execute_confirmed_merge.py merge_reviews\\<case_id> autofix_worktrees\\<case_id> commit_results\\<case_id>
    python tools\\execute_confirmed_merge.py ... --out-root merge_results --force
    python tools\\execute_confirmed_merge.py ... --git-timeout 30
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Survey.merge_executor import (  # noqa: E402
    DEFAULT_GIT_TIMEOUT_S,
    MergeExecutionError,
    write_merge_result,
)


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Exécute le merge local (--no-ff) d'un patch autofix confirmé (Phase 16) sur sa VRAIE "
            "branche cible, dans le dépôt principal — jamais de push, jamais de résolution automatique "
            "d'un conflit réel."
        )
    )
    parser.add_argument("merge_review_dir", help="Dossier de confirmation de merge (ex: merge_reviews\\<case_id>)")
    parser.add_argument("worktree_dir", help="Dossier de l'artefact Phase 7 (ex: autofix_worktrees\\<case_id>)")
    parser.add_argument("commit_result_dir", help="Dossier du résultat de commit (ex: commit_results\\<case_id>)")
    parser.add_argument(
        "--out-root", default="merge_results",
        help="Dossier racine des résultats générés (défaut : merge_results)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Régénère un merge_result.json déjà existant (ne remerge jamais si déjà mergé — idempotent).",
    )
    parser.add_argument(
        "--git-timeout", type=float, default=DEFAULT_GIT_TIMEOUT_S,
        help=f"Budget de temps (secondes) par commande git (défaut : {DEFAULT_GIT_TIMEOUT_S})",
    )
    args = parser.parse_args(argv)

    try:
        out_file = write_merge_result(
            merge_review_dir=args.merge_review_dir,
            worktree_dir=args.worktree_dir,
            commit_result_dir=args.commit_result_dir,
            out_root=args.out_root,
            force=args.force,
            git_timeout_s=args.git_timeout,
        )
    except MergeExecutionError as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 1

    data = json.loads(out_file.read_text(encoding="utf-8"))
    print(f"case_id        : {data['case_id']}")
    print(f"branche autofix: {data['autofix_branch']}")
    print(f"branche cible  : {data['target_branch']}")
    print(f"statut         : {data['status']}")
    if data.get("merge_sha"):
        print(f"merge_sha      : {data['merge_sha']}")
    if data.get("reason"):
        print(f"raison         : {data['reason']}")
    for w in data["warnings"]:
        print(f"  ! {w}")
    print(f"-> {out_file}")

    return 0 if data["status"] in ("MERGED", "ALREADY_MERGED") else 1


if __name__ == "__main__":
    sys.exit(main())
