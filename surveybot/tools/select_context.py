"""
select_context.py — Produit une sélection bornée et traçable de fichiers de code
(context_selection.json) à transmettre à la Phase 6 (génération de prompt), à
partir d'un diagnostic déjà produit (Phase 4, diagnosis.json) et du manifest du
failure_case associé (Phase 2, manifest.json).

Outil en lecture seule : ne modifie jamais failure_cases/ ni diagnoses/, ne
génère aucun prompt. Toute la logique vit dans Survey/context_selector.py ; ce
script n'est qu'une façade CLI.

Usage :
    python tools\\select_context.py diagnoses\\<case_id>
    python tools\\select_context.py diagnoses\\<case_id> --failure-cases-root failure_cases
    python tools\\select_context.py diagnoses\\<case_id> --out-root context_selections
    python tools\\select_context.py diagnoses\\<case_id> --max-code-files 8
    python tools\\select_context.py diagnoses\\<case_id> --force
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Survey.context_selector import (  # noqa: E402
    DEFAULT_CODE_FILES_CAP,
    ContextSelectionError,
    write_context_selection,
)


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Sélectionne un contexte de code borné et traçable (context_selection.json) "
            "à partir d'un diagnostic Phase 4 et du manifest Phase 2 du case associé."
        )
    )
    parser.add_argument("diagnosis_dir", help="Dossier du diagnostic (ex: diagnoses\\<case_id>)")
    parser.add_argument(
        "--failure-cases-root",
        default="failure_cases",
        help="Dossier racine des failure_cases (défaut : failure_cases)",
    )
    parser.add_argument(
        "--out-root",
        default="context_selections",
        help="Dossier racine des sélections générées (défaut : context_selections)",
    )
    parser.add_argument(
        "--max-code-files",
        type=int,
        default=DEFAULT_CODE_FILES_CAP,
        help=f"Plafond de fichiers de code (hors BOT_EVOLUTION_MEMORY.md), défaut={DEFAULT_CODE_FILES_CAP}",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Régénère une sélection déjà existante (la supprime avant reconstruction)",
    )
    args = parser.parse_args(argv)

    try:
        out_file = write_context_selection(
            args.diagnosis_dir,
            failure_cases_root=args.failure_cases_root,
            out_root=args.out_root,
            code_files_cap=args.max_code_files,
            force=args.force,
        )
    except ContextSelectionError as exc:
        print(f"[ERREUR] {args.diagnosis_dir} : {exc}", file=sys.stderr)
        return 1

    import json
    data = json.loads(out_file.read_text(encoding="utf-8"))
    print(f"case                : {data['case_id']}")
    print(f"toujours inclus     : {[f['file'] for f in data['always_included']]}")
    print(f"fichiers de code ({len(data['code_files'])}/{data['code_files_cap']}) :")
    for f in data["code_files"]:
        print(f"  - {f['file']}")
        print(f"      raison : {f['reason']}")
    if not data["code_files"]:
        print("  (aucun)")
    print(f"tronqué             : {data['truncated']}")
    if data["dropped_files"]:
        print(f"retirés (plafond)   : {data['dropped_files']}")
    if data["stale_references"]:
        print(f"références obsolètes ignorées : {data['stale_references']}")
    print(f"table de mapping itype/stage trouvée dans le code : {data['mapping_table_signal']['found']}")
    for w in data["warnings"]:
        print(f"  ! {w}")
    print(f"-> {out_file}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
