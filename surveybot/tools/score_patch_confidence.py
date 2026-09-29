"""
score_patch_confidence.py — Phase 12. Calcule un score de confiance (HIGH/
MEDIUM/REJECT) pour un patch autofix à partir des verdicts déjà produits par
les Phases 8 (validation statique), 9 (rejeu du bug), 11-A (intégrité des
fonctions gelées) et, si disponible, 10 (validation live).

Lecture seule sur ces artefacts : ne recalcule, ne relance et ne réexécute
jamais rien. Contrairement aux façades des Phases 7 à 11, cet outil N'A AUCUN
EFFET DE BORD (aucun Git, aucun navigateur, aucune page réelle) — il produit
TOUJOURS un verdict, y compris dégradé (MEDIUM/REJECT) si une pièce manque ou
qu'une phase n'a pas tourné pour ce case, sauf en cas de véritable erreur
d'usage (case_id/branch incohérents entre les artefacts fournis). Toute la
logique vit dans Survey/autofix/confidence_score.py ; ce script n'est qu'une façade
CLI.

Convention reprise des façades des Phases 8/9/10/11 : chaque argument est le
CHEMIN COMPLET vers le fichier JSON produit par la phase correspondante (pas
un dossier qui le contiendrait). live_validation est optionnel : absence
normale si la Phase 10 n'a jamais été déclenchée pour ce case (cf. Phase 10,
qui n'est éligible que si la Phase 9 est restée NON_CONCLUANT).

Usage :
    python tools\\score_patch_confidence.py autofix_worktrees\\<case_id>\\worktree.json autofix_static_validations\\<case_id>\\validation_static.json patch_replays\\<case_id>\\patch_replay.json extractor_integrity_checks\\<case_id>\\extractor_integrity_check.json
    python tools\\score_patch_confidence.py ... --live-validation live_validations\\<case_id>\\live_validation.json
    python tools\\score_patch_confidence.py ... --out-root confidence_scores --force
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Survey.autofix.confidence_score import (  # noqa: E402
    ConfidenceScoreError,
    write_patch_confidence,
)


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Calcule le score de confiance (HIGH/MEDIUM/REJECT) d'un patch autofix à partir des "
            "verdicts déjà produits par les Phases 8, 9, 11-A et, si disponible, 10 — toujours un "
            "verdict, jamais un crash sur une pièce manquante."
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
        "patch_replay",
        help="Chemin de patch_replay.json produit par la Phase 9 (ex: patch_replays\\<case_id>\\patch_replay.json)",
    )
    parser.add_argument(
        "extractor_integrity_check",
        help=(
            "Chemin de extractor_integrity_check.json produit par la Phase 11-A "
            "(ex: extractor_integrity_checks\\<case_id>\\extractor_integrity_check.json)"
        ),
    )
    parser.add_argument(
        "--live-validation",
        default=None,
        help=(
            "Chemin de live_validation.json produit par la Phase 10, SI cette phase a tourné pour "
            "ce case (optionnel — absence normale, cf. Phase 10)"
        ),
    )
    parser.add_argument(
        "--out-root",
        default="confidence_scores",
        help="Dossier racine des résultats générés (défaut : confidence_scores)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Régénère un résultat déjà existant (le supprime avant reconstruction)",
    )
    args = parser.parse_args(argv)

    try:
        out_file = write_patch_confidence(
            worktree_manifest_path=args.worktree_manifest,
            validation_static_path=args.validation_static,
            patch_replay_path=args.patch_replay,
            extractor_integrity_check_path=args.extractor_integrity_check,
            live_validation_path=args.live_validation,
            out_root=args.out_root,
            force=args.force,
        )
    except ConfidenceScoreError as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 1

    data = json.loads(out_file.read_text(encoding="utf-8"))
    print(f"case            : {data['case_id']}")
    print(f"branche         : {data['branch']}")
    for name, crit in data["criteria"].items():
        print(f"  {name:20s} : {crit['value']:12s} ({crit['detail']})")
    print(f"confiance       : {data['confidence']}")
    if data.get("reason"):
        print(f"raison          : {data['reason']}")
    for w in data["warnings"]:
        print(f"  ! {w}")
    print(f"-> {out_file}")

    return 0 if data["confidence"] == "HIGH" else 1


if __name__ == "__main__":
    sys.exit(main())
