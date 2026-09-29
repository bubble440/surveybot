"""
check_function_overlap.py — Partie B du contrôle de sécurité du parallélisme :
APRÈS que Codex a produit ses patchs, AVANT le merge, compare au niveau
FONCTION au moins deux worktree.json (Phase 7, Survey/autofix/autofix_worktree.py)
pour détecter les fonctions réellement modifiées par plusieurs worktrees à la
fois — les seuls vrais conflits de fusion à ce niveau.

Purement en lecture seule : n'applique, ne merge, ne modifie aucun worktree ni
aucune branche. N'écrit que son propre rapport horodaté. Ne décide jamais
d'une réconciliation — seulement un rapport pour l'opérateur, qui réconcilie
lui-même (manuellement, ou via un prompt Codex de réconciliation ciblé, hors
périmètre de cet outil). Toute la logique vit dans Survey/autofix/parallel_safety.py
(réutilisation stricte de Survey/autofix/static_validator.py et de la technique
AST/hash de Survey/autofix/bem_proposal.py, aucune réimplémentation) ; ce script n'est
qu'une façade CLI.

Un fichier modifié par plusieurs worktrees mais sans fonction en commun n'est
jamais signalé comme un conflit (Git le fusionnera normalement) ; un fichier
dont l'AST ne parse pas est signalé explicitement comme non comparable, jamais
deviné ni classé sûr par défaut.

Usage :
    python tools\\check_function_overlap.py autofix_worktrees\\<case_id_1>\\worktree.json autofix_worktrees\\<case_id_2>\\worktree.json
    python tools\\check_function_overlap.py autofix_worktrees\\*\\worktree.json --out-root function_overlap_checks
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Survey.autofix.parallel_safety import (  # noqa: E402
    DEFAULT_GIT_TIMEOUT_S,
    ParallelSafetyError,
    write_pre_merge_function_overlap_check,
)


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compare au niveau FONCTION les patchs d'au moins deux worktree.json (Phase 7) pour "
            "signaler les conflits réels avant merge — jamais une résolution automatique, "
            "seulement un rapport."
        )
    )
    parser.add_argument(
        "worktrees",
        nargs="+",
        help="Au moins 2 chemins vers des worktree.json (Phase 7)",
    )
    parser.add_argument(
        "--out-root",
        default="function_overlap_checks",
        help="Dossier racine des rapports générés (défaut : function_overlap_checks)",
    )
    parser.add_argument(
        "--git-timeout-s",
        type=float,
        default=DEFAULT_GIT_TIMEOUT_S,
        help=f"Budget de temps (s) par commande git, défaut={DEFAULT_GIT_TIMEOUT_S}",
    )
    args = parser.parse_args(argv)

    if len(args.worktrees) < 2:
        parser.error("au moins 2 worktree.json sont requis pour comparer les fonctions modifiées")

    try:
        out_file = write_pre_merge_function_overlap_check(
            args.worktrees,
            out_root=args.out_root,
            git_timeout_s=args.git_timeout_s,
        )
    except ParallelSafetyError as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 1

    data = json.loads(out_file.read_text(encoding="utf-8"))
    print(f"niveau de vérification : {data['granularity']} (précis, patchs réellement produits)")
    print(f"worktrees comparés      : {data['case_ids']}")
    for wt in data["worktrees"]:
        print(f"  - {wt['case_id']} : {wt['changed_files_count']} fichier(s) modifié(s) (branch={wt['branch']})")
    shared = [f for f in data["shared_files"]]
    print(f"fichiers partagés       : {len(shared)}")
    for f in shared:
        status = "NON COMPARABLE" if not f["comparable"] else ("CONFLIT" if f["conflicting_functions"] else "sûr")
        print(f"  - {f['file']} [{status}] cases={f['cases']}")
        if f["reason"]:
            print(f"      raison : {f['reason']}")
        if f["excluded_cases"]:
            print(f"      exclus (échec parsing AST) : {f['excluded_cases']}")
        if f["conflicting_functions"]:
            print(f"      fonctions en conflit : {f['conflicting_functions']}")
    if data["real_conflicts"]:
        print("CONFLITS RÉELS (fonction modifiée par plusieurs worktrees) :")
        for c in data["real_conflicts"]:
            print(f"  ! {c['file']}::{c['function']} <- {c['cases']}")
    if data["unresolved_files_count"]:
        print(f"fichiers non comparables (échec parsing AST) : {data['unresolved_files_count']}")
    for w in data["warnings"]:
        print(f"  ! {w}")
    print(f"sûr                     : {data['safe']}")
    print(f"-> {out_file}")

    return 0 if data["safe"] else 1


if __name__ == "__main__":
    sys.exit(main())
