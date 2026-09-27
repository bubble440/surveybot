"""
commit_patch.py — Phase 15. Commit automatique d'un patch autofix déjà validé
(confidence_score.json, Phase 12, confidence="HIGH") et approuvé par un humain
(decision.json, Phase 13, decision="APPROVED"), sur sa branche autofix isolée
existante (worktree.json, Phase 7).

Vérifie par un FAIT Git observable (jamais une déclaration) que
Survey/BOT_EVOLUTION_MEMORY.md figure parmi les fichiers modifiés du worktree
depuis base_sha ; --skip-bem-check-reason permet de contourner explicitement
ce contrôle pour un patch qui n'a légitimement pas vocation à générer d'entrée
BEM (ex. correctif d'infrastructure de test d'autofix) — jamais un simple
booléen silencieux, une raison est obligatoire.

Refuse explicitement si un commit portant déjà la référence de ce case_id
existe dans le log Git de la branche (jamais un commit en double). N'effectue
jamais de push ni de merge ; ne touche jamais à une branche protégée
(PROTECTED_BRANCHES). Toute la logique vit dans Survey/patch_commit.py ; ce
script n'est qu'une façade CLI.

Usage :
    python tools\\commit_patch.py confidence_scores\\<case_id> human_reviews\\<case_id> autofix_worktrees\\<case_id>
    python tools\\commit_patch.py ... --bem-proposal-dir bem_proposals\\<case_id>
    python tools\\commit_patch.py ... --message "fix(survey): ..."
    python tools\\commit_patch.py ... --skip-bem-check-reason "correctif d'infrastructure de test, pas d'extracteur"
    python tools\\commit_patch.py ... --out-root commit_results --force
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Survey.patch_commit import (  # noqa: E402
    DEFAULT_GIT_TIMEOUT_S,
    PatchCommitError,
    commit_patch,
)


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Commit automatique d'un patch autofix déjà validé (Phase 12, confidence=HIGH) et "
            "approuvé (Phase 13, decision=APPROVED) sur sa branche autofix existante (Phase 7). "
            "Jamais de push, jamais de merge."
        )
    )
    parser.add_argument("confidence_score_dir", help="Dossier du score de confiance (ex: confidence_scores\\<case_id>)")
    parser.add_argument("human_review_dir", help="Dossier de revue humaine (ex: human_reviews\\<case_id>)")
    parser.add_argument("worktree_dir", help="Dossier de l'artefact Phase 7 (ex: autofix_worktrees\\<case_id>)")
    parser.add_argument(
        "--bem-proposal-dir",
        default=None,
        help=(
            "Dossier de la proposition BEM (ex: bem_proposals\\<case_id>) — utilisé uniquement pour "
            "composer le sujet de commit par défaut à partir des fonctions réellement ajoutées/modifiées "
            "(Phase 14). Sans --message ni ce dossier, le sujet est dérivé des fichiers committés."
        ),
    )
    parser.add_argument(
        "--message",
        default=None,
        help="Formulation du sujet de commit fournie par l'opérateur, à la place du sujet mécanique par défaut.",
    )
    parser.add_argument(
        "--skip-bem-check-reason",
        default=None,
        help=(
            "Contourne explicitement le contrôle \"BOT_EVOLUTION_MEMORY.md modifié\" — exige une raison "
            "non vide, jamais un simple booléen silencieux."
        ),
    )
    parser.add_argument(
        "--git-timeout", type=float, default=DEFAULT_GIT_TIMEOUT_S,
        help=f"Budget de temps (secondes) par commande git (défaut : {DEFAULT_GIT_TIMEOUT_S})",
    )
    parser.add_argument(
        "--out-root", default="commit_results",
        help="Dossier racine des résultats générés (défaut : commit_results)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Régénère un commit_result.json déjà existant (ne recommit jamais si rien n'a changé).",
    )
    args = parser.parse_args(argv)

    try:
        result = commit_patch(
            confidence_score_dir=args.confidence_score_dir,
            human_review_dir=args.human_review_dir,
            worktree_dir=args.worktree_dir,
            bem_proposal_dir=args.bem_proposal_dir,
            message=args.message,
            skip_bem_check_reason=args.skip_bem_check_reason,
            git_timeout_s=args.git_timeout,
            out_root=args.out_root,
            force=args.force,
        )
    except PatchCommitError as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 1

    print(f"case_id           : {result.case_id}")
    print(f"branch            : {result.branch}")
    print(f"already_committed : {result.already_committed}")
    print(f"commit_sha        : {result.commit_sha}")
    print(f"commit_subject    : {result.commit_subject}")
    print(f"fichiers inclus   : {len(result.files_included)}")
    for warning in result.warnings:
        print(f"  [avertissement] {warning}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
