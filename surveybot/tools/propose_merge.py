"""
propose_merge.py — Phase 16. Envoie une notification Telegram de confirmation
de merge (clavier inline Confirmer/Annuler) pour un case dont la Phase 15
(commit_result.json) indique un commit réussi (ou déjà committé) — refus
explicite sinon.

Compose un message concis à partir de worktree.json (Phase 7 : branche
autofix, branche cible = source_branch tel quel) et commit_result.json
(Phase 15 : sha/sujet du commit) — jamais le diff complet. Persiste un accusé
sous merge_reviews\\<case_id>\\pending.json (même convention que Phase 13,
dossier distinct).

Ne déclenche JAMAIS elle-même un git merge, un push, ni aucune modification de
la branche cible — la confirmation humaine reste un signal à vérifier
manuellement par l'opérateur avant d'exécuter le merge lui-même. La capture de
la décision se fait via tools\\check_human_review.py (généralisé Phase 16 pour
interroger human_reviews\\ ET merge_reviews\\ dans la même invocation, avec le
même offset persisté — jamais deux pollers Telegram indépendants). Toute la
logique vit dans Survey/autofix/merge_review.py ; ce script n'est qu'une façade CLI.

Usage :
    python tools\\propose_merge.py commit_results\\<case_id> autofix_worktrees\\<case_id>
    python tools\\propose_merge.py commit_results\\<case_id> autofix_worktrees\\<case_id> --out-root merge_reviews
    python tools\\propose_merge.py commit_results\\<case_id> autofix_worktrees\\<case_id> --force
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Survey.autofix.merge_review import MergeReviewError, send_merge_confirmation_request  # noqa: E402


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Envoie une notification Telegram de confirmation de merge (Confirmer/Annuler) pour un "
            "case dont commit_result.json (Phase 15) indique un commit réussi (ou déjà committé). "
            "Ne déclenche jamais elle-même le merge."
        )
    )
    parser.add_argument("commit_result_dir", help="Dossier du résultat de commit (ex: commit_results\\<case_id>)")
    parser.add_argument("worktree_dir", help="Dossier de l'artefact Phase 7 (ex: autofix_worktrees\\<case_id>)")
    parser.add_argument(
        "--out-root",
        default="merge_reviews",
        help="Dossier racine des accusés générés (défaut : merge_reviews)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Renvoie la notification même si pending.json/decision.json existe déjà pour ce case",
    )
    args = parser.parse_args(argv)

    try:
        result = send_merge_confirmation_request(
            commit_result_dir=args.commit_result_dir,
            worktree_dir=args.worktree_dir,
            out_root=args.out_root,
            force=args.force,
        )
    except MergeReviewError as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 1

    print(f"case_id    : {result.case_id}")
    print(f"chat_id    : {result.chat_id}")
    print(f"message_id : {result.message_id}")
    print(f"-> {result.pending_path}")
    print("Rappel : aucun merge n'est déclenché automatiquement — la confirmation reste à vérifier manuellement.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
