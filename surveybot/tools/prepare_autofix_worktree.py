"""
prepare_autofix_worktree.py — Phase 7. Prépare un espace Git isolé (nouvelle
branche autofix/<case_id> + nouveau worktree dédié) pour un case extraction
déjà diagnostiqué (Phase 4), sélectionné (Phase 5) et prompté (Phase 6), en
amont d'un lancement manuel de Codex sur le prompt.txt déjà généré.

Ne lance aucun agent de coding, n'applique aucun patch, ne commit rien, ne
push rien, ne merge rien. Lecture seule sur les Phases 2-6 : lit uniquement
manifest.json, diagnosis.json et prompt.txt déjà produits, ne les recalcule
jamais. Toute la logique vit dans Survey/autofix_worktree.py ; ce script n'est
qu'une façade CLI.

Éligibilité (toutes les conditions ensemble, avant tout effet de bord Git) :
stage="extraction", replay.verdict="REPRODUIT", confidence_global="certain",
case_incomplete=false, prompt.txt présent (jamais MANUAL_REVIEW_REQUIRED.txt à
sa place), case_id cohérent entre manifest/diagnosis/dossiers et sûr comme
composant de chemin et référence Git. Les cases stage="action" sont hors
périmètre (le replay ne réexécute jamais le dispatcher réel pour ces cases).

Usage :
    python tools\\prepare_autofix_worktree.py failure_cases\\<case_id> diagnoses\\<case_id> prompts\\<case_id>
    python tools\\prepare_autofix_worktree.py failure_cases\\<case_id> diagnoses\\<case_id> prompts\\<case_id> --worktrees-root D:\\autofix-worktrees
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Survey.autofix_worktree import (  # noqa: E402
    AutofixWorktreeError,
    prepare_autofix_worktree,
)


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Prépare une branche autofix/<case_id> et un worktree Git dédié pour un case "
            "extraction éligible (REPRODUIT, confiance certaine) — ne lance pas Codex."
        )
    )
    parser.add_argument("failure_case_dir", help="Dossier du failure case (ex: failure_cases\\<case_id>)")
    parser.add_argument("diagnosis_dir", help="Dossier du diagnostic (ex: diagnoses\\<case_id>)")
    parser.add_argument("prompt_dir", help="Dossier du prompt (ex: prompts\\<case_id>)")
    parser.add_argument(
        "--worktrees-root",
        default=None,
        help="Racine explicite des worktrees (défaut : <parent du dépôt>/<nom du dépôt>-worktrees, calculé)",
    )
    args = parser.parse_args(argv)

    try:
        result = prepare_autofix_worktree(
            failure_case_dir=args.failure_case_dir,
            diagnosis_dir=args.diagnosis_dir,
            prompt_dir=args.prompt_dir,
            worktrees_root=args.worktrees_root,
        )
    except AutofixWorktreeError as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 1

    print(f"case_id      : {result.case_id}")
    print(f"branche      : {result.branch}")
    print(f"base_sha     : {result.base_sha}")
    print(f"worktree     : {result.worktree_path}")
    print(f"prompt à transmettre manuellement à Codex : {result.prompt_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
