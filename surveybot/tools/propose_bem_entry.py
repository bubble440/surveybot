"""
propose_bem_entry.py — Phase 14. Génère un brouillon d'entrée
BOT_EVOLUTION_MEMORY.md (bem_entry_proposal.md, jamais collé automatiquement)
pour un patch autofix déjà approuvé par un humain (Phase 13, decision.json).

Lecture seule sur les artefacts déjà produits par les Phases 4, 5, 7, 12 et 13,
et sur le worktree Git isolé de la Phase 7 (jamais modifié) ; n'écrit jamais
dans BOT_EVOLUTION_MEMORY.md lui-même. Toute la logique vit dans
Survey/autofix/bem_proposal.py ; ce script n'est qu'une façade CLI.

Éligibilité (toutes les conditions ensemble) : decision.json (Phase 13) avec
decision="APPROVED" exactement ; worktree.json (Phase 7), diagnosis.json
(Phase 4), context_selection.json (Phase 5) et confidence_score.json
(Phase 12) existants, lisibles et cohérents en case_id.

Usage :
    python tools\\propose_bem_entry.py human_reviews\\<case_id> autofix_worktrees\\<case_id> diagnoses\\<case_id> context_selections\\<case_id> confidence_scores\\<case_id>
    python tools\\propose_bem_entry.py ... --out-root bem_proposals --force
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Survey.autofix.bem_proposal import (  # noqa: E402
    DEFAULT_GIT_TIMEOUT_S,
    BemProposalError,
    write_bem_proposal,
)


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Génère un brouillon d'entrée BOT_EVOLUTION_MEMORY.md pour un patch autofix déjà "
            "approuvé par un humain (Phase 13) — jamais collé automatiquement, jamais d'écriture "
            "dans BOT_EVOLUTION_MEMORY.md lui-même."
        )
    )
    parser.add_argument("human_review_dir", help="Dossier de revue humaine (ex: human_reviews\\<case_id>)")
    parser.add_argument("worktree_dir", help="Dossier de l'artefact Phase 7 (ex: autofix_worktrees\\<case_id>)")
    parser.add_argument("diagnosis_dir", help="Dossier du diagnostic (ex: diagnoses\\<case_id>)")
    parser.add_argument(
        "context_selection_dir",
        help="Dossier de la sélection de contexte (ex: context_selections\\<case_id>)",
    )
    parser.add_argument(
        "confidence_score_dir",
        help="Dossier du score de confiance (ex: confidence_scores\\<case_id>)",
    )
    parser.add_argument(
        "--out-root", default="bem_proposals",
        help="Dossier racine des propositions générées (défaut : bem_proposals)",
    )
    parser.add_argument(
        "--git-timeout", type=float, default=DEFAULT_GIT_TIMEOUT_S,
        help=f"Budget de temps (secondes) par commande git (défaut : {DEFAULT_GIT_TIMEOUT_S})",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Régénère une proposition déjà existante (la supprime avant reconstruction)",
    )
    args = parser.parse_args(argv)

    try:
        draft_file = write_bem_proposal(
            human_review_dir=args.human_review_dir,
            worktree_dir=args.worktree_dir,
            diagnosis_dir=args.diagnosis_dir,
            context_selection_dir=args.context_selection_dir,
            confidence_score_dir=args.confidence_score_dir,
            out_root=args.out_root,
            force=args.force,
            git_timeout_s=args.git_timeout,
        )
    except BemProposalError as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 1

    print(f"brouillon   : {draft_file}")
    print(f"traçabilité : {draft_file.parent / 'bem_proposal.json'}")
    print("Rappel : jamais collé automatiquement dans BOT_EVOLUTION_MEMORY.md — relecture humaine requise.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
