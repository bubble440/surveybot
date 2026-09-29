"""
validate_patch_static.py — Phase 8. Valide statiquement le patch présent dans
un worktree autofix déjà préparé (Phase 7, tools/prepare_autofix_worktree.py) :
compilation, import isolé, lint minimal (erreurs réelles uniquement, via
Ruff) et tests unitaires existants associés, si une convention de nommage
fichier-source -> fichier-de-test existe déjà dans ce dépôt (aucune à ce
jour, documenté explicitement plutôt que masqué — cf. Survey/autofix/static_validator.py).

Lecture seule sur la Phase 7 : lit uniquement worktree.json (le seul artefact
produit par la Phase 7, Partie 1 de ce chantier) — ne rouvre jamais
manifest.json ni diagnosis.json, ne recalcule aucune éligibilité déjà
tranchée. Le sous-ensemble de fichiers vérifié est déterminé par comparaison
Git entre base_sha et l'état courant du worktree, jamais l'ensemble du dépôt.
Aucun test live n'est déclenché, quel que soit le verdict. Toute la logique
vit dans Survey/autofix/static_validator.py ; ce script n'est qu'une façade CLI.

Usage :
    python tools\\validate_patch_static.py autofix_worktrees\\<case_id>\\worktree.json
    python tools\\validate_patch_static.py autofix_worktrees\\<case_id>\\worktree.json --out-root autofix_static_validations
    python tools\\validate_patch_static.py autofix_worktrees\\<case_id>\\worktree.json --force
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Survey.autofix.static_validator import (  # noqa: E402
    DEFAULT_COMPILE_TIMEOUT_S,
    DEFAULT_GIT_TIMEOUT_S,
    DEFAULT_IMPORT_TIMEOUT_S,
    DEFAULT_LINT_TIMEOUT_S,
    DEFAULT_TESTS_TIMEOUT_S,
    StaticValidationError,
    write_static_validation,
)


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Valide statiquement (compilation, import isolé, lint minimal, tests unitaires "
            "associés existants) le patch présent dans un worktree autofix déjà préparé "
            "(Phase 7) — jamais de test live."
        )
    )
    parser.add_argument(
        "worktree_manifest",
        help="Chemin de worktree.json produit par la Phase 7 (ex: autofix_worktrees\\<case_id>\\worktree.json)",
    )
    parser.add_argument(
        "--out-root",
        default="autofix_static_validations",
        help="Dossier racine des validations générées (défaut : autofix_static_validations)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Régénère une validation déjà existante (la supprime avant reconstruction)",
    )
    parser.add_argument("--git-timeout", type=float, default=DEFAULT_GIT_TIMEOUT_S, help=f"Budget (s) pour les commandes git diff/status, défaut={DEFAULT_GIT_TIMEOUT_S}")
    parser.add_argument("--compile-timeout", type=float, default=DEFAULT_COMPILE_TIMEOUT_S, help=f"Budget (s) par fichier compilé, défaut={DEFAULT_COMPILE_TIMEOUT_S}")
    parser.add_argument("--import-timeout", type=float, default=DEFAULT_IMPORT_TIMEOUT_S, help=f"Budget (s) par fichier importé, défaut={DEFAULT_IMPORT_TIMEOUT_S}")
    parser.add_argument("--lint-timeout", type=float, default=DEFAULT_LINT_TIMEOUT_S, help=f"Budget (s) pour l'appel Ruff, défaut={DEFAULT_LINT_TIMEOUT_S}")
    parser.add_argument("--tests-timeout", type=float, default=DEFAULT_TESTS_TIMEOUT_S, help=f"Budget (s) pour l'exécution pytest, défaut={DEFAULT_TESTS_TIMEOUT_S}")
    args = parser.parse_args(argv)

    try:
        out_file = write_static_validation(
            args.worktree_manifest,
            out_root=args.out_root,
            force=args.force,
            git_timeout_s=args.git_timeout,
            compile_timeout_s=args.compile_timeout,
            import_timeout_s=args.import_timeout,
            lint_timeout_s=args.lint_timeout,
            tests_timeout_s=args.tests_timeout,
        )
    except StaticValidationError as exc:
        print(f"[ERREUR] {args.worktree_manifest} : {exc}", file=sys.stderr)
        return 1

    import json
    data = json.loads(out_file.read_text(encoding="utf-8"))
    print(f"case           : {data['case_id']}")
    print(f"branche        : {data['branch']}")
    print(f"fichiers ({len(data['changed_files'])}) :")
    for f in data["changed_files"]:
        print(f"  - {f}")
    for name in ("compile", "import", "lint", "tests"):
        print(f"{name:<8} ok={data['checks'][name]['ok']}")
    print(f"verdict        : {data['verdict']}")
    if data["reasons"]:
        for r in data["reasons"]:
            print(f"  ! {r}")
    if data["warnings"]:
        for w in data["warnings"]:
            print(f"  ! {w}")
    print(f"-> {out_file}")

    return 0 if data["verdict"] == "ACCEPTED" else 1


if __name__ == "__main__":
    sys.exit(main())
