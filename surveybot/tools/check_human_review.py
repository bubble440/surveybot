"""
check_human_review.py — Phase 13 (Partie 2), généralisé Phase 16. Interroge
l'API Telegram (getUpdates) UNE SEULE FOIS par invocation, à partir d'un
offset persisté sur disque partagé (créé à zéro s'il n'existe pas encore),
pour retrouver et capturer les décisions humaines (Approuver/Rejeter, ou
Confirmer le merge/Annuler) prises depuis Survey/autofix/human_review.py::
send_review_request (Phase 13) OU Survey/autofix/merge_review.py::
send_merge_confirmation_request (Phase 16).

Telegram ne fournit qu'UN SEUL flux getUpdates par bot : deux pollers
indépendants avec deux offsets indépendants se voleraient mutuellement les
mises à jour dès qu'ils tournent tous les deux (cf. Survey/autofix/human_review.py,
docstring du module) — cet outil reste donc l'unique poller, et route chaque
callback_query, selon son préfixe de callback_data, vers human_reviews\\
(Phase 13, comportement inchangé) OU merge_reviews\\ (Phase 16), avec le même
offset persisté.

Pour chaque callback_query : décode le case_id (même hachage que Survey/autofix/
human_review.py::_encode_case_ref, recherché symétriquement parmi les dossiers
déjà connus sous le dossier du type concerné), retrouve le pending.json
correspondant. Case inconnu (bouton pressé sur un message obsolète) :
avertissement journalisé, jamais un plantage — la mise à jour est quand même
considérée traitée. Décision déjà enregistrée pour ce case : ignorée (la
première décision fait foi), avec avertissement. Sinon, écrit
<dossier>\\<case_id>\\decision.json.

L'offset persisté n'avance qu'après que TOUTES les décisions du lot ont été
durablement écrites ou explicitement ignorées — jamais avant, pour ne perdre
aucune mise à jour en cas d'interruption en cours de traitement.

Ne merge rien, ne commit rien, ne modifie aucun worktree autofix — capture
uniquement la décision humaine. Toute la logique vit dans Survey/autofix/
human_review.py ; ce script n'est qu'une façade CLI.

Usage :
    python tools\\check_human_review.py
    python tools\\check_human_review.py --root human_reviews --merge-root merge_reviews
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Survey.autofix.human_review import HumanReviewError, check_pending_reviews  # noqa: E402


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Interroge une fois l'API Telegram (getUpdates) et capture les décisions "
            "Approuver/Rejeter reçues depuis la dernière invocation."
        )
    )
    parser.add_argument(
        "--root",
        default="human_reviews",
        help="Dossier racine des accusés/décisions de revue humaine, Phase 13 (défaut : human_reviews)",
    )
    parser.add_argument(
        "--merge-root",
        default="merge_reviews",
        help="Dossier racine des accusés/décisions de confirmation de merge, Phase 16 (défaut : merge_reviews)",
    )
    parser.add_argument(
        "--offset-file",
        default=None,
        help="Fichier d'offset explicite, partagé entre les deux types (défaut : <root>\\_telegram_offset.json)",
    )
    args = parser.parse_args(argv)

    try:
        result = check_pending_reviews(
            out_root=args.root, merge_out_root=args.merge_root, offset_file=args.offset_file,
        )
    except HumanReviewError as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 1

    print(f"updates reçus       : {result.updates_fetched}")
    print(f"callback_query vus  : {result.callback_queries_seen}")
    for item in result.processed:
        print(f"  - kind={item.kind} case={item.case_id!r} decision={item.decision} outcome={item.outcome}")
    print(f"prochain offset     : {result.next_offset}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
