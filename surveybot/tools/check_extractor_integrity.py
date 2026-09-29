"""
check_extractor_integrity.py — Phase 11, partie A. Vérifie que le patch
présent dans un worktree autofix déjà validé statiquement (Phase 8) n'a pas
modifié, supprimé ni renommé une fonction listée comme "gelée" dans
Survey/extractor_integrity.py/.json — hash SHA256 recalculé sur le code du
worktree, jamais sur celui du dépôt principal.

Lecture seule sur les Phases 7/8 : lit uniquement worktree.json et
validation_static.json déjà produits, ne recalcule aucune éligibilité déjà
tranchée. Importe directement _load_registry/_hash_function de
Survey/extractor_integrity.py DU WORKTREE (jamais un sous-processus, jamais
une réimplémentation parallèle de la logique de hash). Toute la logique vit
dans Survey/autofix/extractor_integrity_gate.py ; ce script n'est qu'une façade CLI.

Portée : partie A uniquement (hash des fonctions gelées). Le rejeu de DOM
historiques/génériques représentatifs (regression_cases/) reste un
sous-chantier différé — cf. Survey/autofix/extractor_integrity_gate.py.

Convention reprise de tools/validate_patch_static.py (Phase 8) : worktree_manifest
et validation_static sont les CHEMINS COMPLETS vers worktree.json/
validation_static.json (pas un dossier qui les contiendrait).

Usage :
    python tools\\check_extractor_integrity.py autofix_worktrees\\<case_id>\\worktree.json autofix_static_validations\\<case_id>\\validation_static.json
    python tools\\check_extractor_integrity.py autofix_worktrees\\<case_id>\\worktree.json autofix_static_validations\\<case_id>\\validation_static.json --out-root extractor_integrity_checks --force
    python tools\\check_extractor_integrity.py autofix_worktrees\\<case_id>\\worktree.json autofix_static_validations\\<case_id>\\validation_static.json --time-budget-s 60
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Survey.autofix.extractor_integrity_gate import (  # noqa: E402
    DEFAULT_TIME_BUDGET_S,
    IntegrityGateError,
    write_extractor_integrity_check,
)


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Vérifie l'intégrité des fonctions gelées (Survey/extractor_integrity.py/.json) d'un "
            "patch autofix déjà validé statiquement (Phase 8) — hash recalculé sur le code du "
            "worktree, jamais sur celui du dépôt principal."
        )
    )
    parser.add_argument(
        "worktree_manifest",
        help="Chemin de worktree.json produit par la Phase 7 (ex: autofix_worktrees\\<case_id>\\worktree.json)",
    )
    parser.add_argument(
        "validation_static",
        help=(
            "Chemin de validation_static.json produit par la Phase 8 "
            "(ex: autofix_static_validations\\<case_id>\\validation_static.json)"
        ),
    )
    parser.add_argument(
        "--out-root",
        default="extractor_integrity_checks",
        help="Dossier racine des résultats générés (défaut : extractor_integrity_checks)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Régénère un résultat déjà existant (le supprime avant reconstruction)",
    )
    parser.add_argument(
        "--time-budget-s",
        type=float,
        default=DEFAULT_TIME_BUDGET_S,
        help=f"Budget de temps (s) pour parcourir le registre, défaut={DEFAULT_TIME_BUDGET_S}",
    )
    args = parser.parse_args(argv)

    try:
        out_file = write_extractor_integrity_check(
            args.worktree_manifest,
            args.validation_static,
            out_root=args.out_root,
            force=args.force,
            time_budget_s=args.time_budget_s,
        )
    except IntegrityGateError as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 1

    data = json.loads(out_file.read_text(encoding="utf-8"))
    print(f"case            : {data['case_id']}")
    print(f"branche         : {data['branch']}")
    print(f"registre        : {data['registry_path']}")
    print(f"entrées         : {data['checked_entries']}/{data['total_entries']} vérifiées")
    if data["mismatches"]:
        print("FONCTIONS MODIFIÉES (hash différent du registre) :")
        for m in data["mismatches"]:
            print(f"  ! {m['key']}")
    if data["errors"]:
        print("ENTRÉES EN ERREUR :")
        for e in data["errors"]:
            print(f"  ? {e['key']} -- {e['reason']}")
    if data["budget_exceeded"]:
        print("  ! budget de temps dépassé — voir errors[].reason == budget_exceeded")
    for w in data["warnings"]:
        print(f"  ! {w}")
    print(f"verdict         : {data['verdict']}")
    print(f"-> {out_file}")

    return 0 if data["verdict"] == "ACCEPTED" else 1


if __name__ == "__main__":
    sys.exit(main())
