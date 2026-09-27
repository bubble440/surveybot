"""
notify_human_review.py — Phase 13 (Partie 1). Envoie une notification Telegram
de revue humaine (clavier inline Approuver/Rejeter) pour un case dont la
Phase 12 (confidence_score.json) a produit confidence="HIGH" — refus explicite
pour tout autre cas (MEDIUM/REJECT sont déjà visibles via les sorties CLI
existantes des phases précédentes, aucune notification envoyée).

Compose un message concis à partir de diagnosis.json (Phase 4, symptôme +
cause probable) et confidence_score.json (Phase 12, tests + confiance) —
jamais le diff du patch, jamais une donnée brute d'un vrai répondant. Persiste
un accusé sous human_reviews\\<case_id>\\pending.json (chat_id, message_id,
case_id, horodatage).

Outil en lecture seule sur les Phases 4/12 : ne recalcule rien. Toute la
logique vit dans Survey/human_review.py ; ce script n'est qu'une façade CLI.
Ne déclenche jamais de merge, de commit, ni de modification d'un worktree
autofix.

Usage :
    python tools\\notify_human_review.py confidence_scores\\<case_id> diagnoses\\<case_id>
    python tools\\notify_human_review.py confidence_scores\\<case_id> diagnoses\\<case_id> --out-root human_reviews
    python tools\\notify_human_review.py confidence_scores\\<case_id> diagnoses\\<case_id> --force
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Survey.human_review import HumanReviewError, send_review_request  # noqa: E402


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Envoie une notification Telegram de revue humaine (Approuver/Rejeter) pour un "
            "case dont confidence_score.json (Phase 12) porte confidence=\"HIGH\"."
        )
    )
    parser.add_argument("confidence_score_dir", help="Dossier du score de confiance (ex: confidence_scores\\<case_id>)")
    parser.add_argument("diagnosis_dir", help="Dossier du diagnostic (ex: diagnoses\\<case_id>)")
    parser.add_argument(
        "--out-root",
        default="human_reviews",
        help="Dossier racine des accusés générés (défaut : human_reviews)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Renvoie la notification même si pending.json/decision.json existe déjà pour ce case",
    )
    args = parser.parse_args(argv)

    try:
        result = send_review_request(
            confidence_score_dir=args.confidence_score_dir,
            diagnosis_dir=args.diagnosis_dir,
            out_root=args.out_root,
            force=args.force,
        )
    except HumanReviewError as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 1

    print(f"case_id    : {result.case_id}")
    print(f"chat_id    : {result.chat_id}")
    print(f"message_id : {result.message_id}")
    print(f"-> {result.pending_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
