"""
generate_prompt.py — Génère un prompt Codex/Claude Code (texte brut, prêt à
copier-coller) à partir d'un diagnostic (Phase 4, diagnosis.json) et d'une
sélection de contexte (Phase 5, context_selection.json) déjà produits.

Garde-fou obligatoire : un prompt normal n'est produit que si context_selection.json
contient au moins un fichier de code ET si diagnosis.json indique une confiance
globale "probable" ou "certain". Sinon, un signal de revue manuelle
(MANUAL_REVIEW_REQUIRED.txt) est produit à la place — jamais un prompt.

Outil en lecture seule : ne modifie jamais failure_cases/, diagnoses/, ni
context_selections/, ne recalcule rien (pas de nouvel appel replay/diagnosis/
context_selector). Toute la logique vit dans Survey/prompt_generator.py ; ce
script n'est qu'une façade CLI.

Usage :
    python tools\\generate_prompt.py diagnoses\\<case_id> context_selections\\<case_id>
    python tools\\generate_prompt.py diagnoses\\<case_id> context_selections\\<case_id> --out-root prompts
    python tools\\generate_prompt.py diagnoses\\<case_id> context_selections\\<case_id> --force
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Survey.prompt_generator import (  # noqa: E402
    PromptGenerationError,
    write_prompt,
)


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Génère un prompt Codex/Claude Code (ou un signal de revue manuelle) à "
            "partir d'un diagnostic Phase 4 et d'une sélection de contexte Phase 5."
        )
    )
    parser.add_argument("diagnosis_dir", help="Dossier du diagnostic (ex: diagnoses\\<case_id>)")
    parser.add_argument("context_selection_dir", help="Dossier de la sélection de contexte (ex: context_selections\\<case_id>)")
    parser.add_argument(
        "--out-root",
        default="prompts",
        help="Dossier racine des sorties générées (défaut : prompts)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Régénère une sortie déjà existante (la supprime avant reconstruction)",
    )
    args = parser.parse_args(argv)

    try:
        out_file = write_prompt(
            args.diagnosis_dir,
            args.context_selection_dir,
            out_root=args.out_root,
            force=args.force,
        )
    except PromptGenerationError as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 1

    print(f"-> {out_file}")
    print()
    print(out_file.read_text(encoding="utf-8"))

    return 0 if out_file.name == "prompt.txt" else 1


if __name__ == "__main__":
    sys.exit(main())
