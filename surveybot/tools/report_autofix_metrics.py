"""
report_autofix_metrics.py — outil de statistiques du pipeline autofix.
Prérequis à une future Phase 17 (jamais son déclencheur) : compte et rapporte
ce qui s'est réellement passé sur l'ensemble des cases connus, sans jamais
modifier, recalculer ni relancer aucune phase existante (2 à 16).

Ne construit aucun mécanisme de merge automatique, aucune liste de
catégories de confiance, ni aucune logique de décision — seulement la
mesure. Toute la logique vit dans Survey/autofix_metrics.py ; ce script
n'est qu'une façade CLI.

Écrit un instantané horodaté (jamais un fichier écrasé, cet outil est fait
pour tourner à répétition sur plusieurs semaines) sous
out-root/<horodatage>/metrics_report.json et imprime un résumé lisible.

Usage :
    python tools\\report_autofix_metrics.py
    python tools\\report_autofix_metrics.py --out-root autofix_metrics
    python tools\\report_autofix_metrics.py --failure-cases-root failure_cases --diagnoses-root diagnoses
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Survey.autofix_metrics import (  # noqa: E402
    AutofixMetricsError,
    write_autofix_metrics,
)

_ROOT_ARGS = [
    ("failure-cases-root", "failure_cases", "failure_cases", "Phase 2 — seule liste exhaustive de cases"),
    ("diagnoses-root", "diagnoses", "diagnoses", "Phase 4"),
    ("prompts-root", "prompts", "prompts", "Phase 6"),
    ("autofix-worktrees-root", "autofix_worktrees", "autofix_worktrees", "Phase 7"),
    ("autofix-static-validations-root", "autofix_static_validations", "autofix_static_validations", "Phase 8"),
    ("patch-replays-root", "patch_replays", "patch_replays", "Phase 9"),
    ("live-validations-root", "live_validations", "live_validations", "Phase 10"),
    ("extractor-integrity-checks-root", "extractor_integrity_checks", "extractor_integrity_checks", "Phase 11-A"),
    ("confidence-scores-root", "confidence_scores", "confidence_scores", "Phase 12"),
    ("human-reviews-root", "human_reviews", "human_reviews", "Phase 13"),
    ("bem-proposals-root", "bem_proposals", "bem_proposals", "Phase 14"),
    ("commit-results-root", "commit_results", "commit_results", "Phase 15"),
    ("merge-reviews-root", "merge_reviews", "merge_reviews", "Phase 16"),
]


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Calcule et écrit un instantané de statistiques du pipeline autofix (Phases 2 à 16) — "
            "purement en lecture seule, aucune phase existante n'est modifiée, recalculée ni relancée."
        )
    )
    for flag, dest_key, default, help_suffix in _ROOT_ARGS:
        parser.add_argument(
            f"--{flag}",
            default=default,
            help=f"Dossier racine des artefacts {help_suffix} (défaut : {default})",
        )
    parser.add_argument(
        "--out-root",
        default="autofix_metrics",
        help="Dossier racine des instantanés générés (défaut : autofix_metrics)",
    )
    args = parser.parse_args(argv)

    roots = {dest_key: getattr(args, flag.replace("-", "_")) for flag, dest_key, _default, _help in _ROOT_ARGS}

    try:
        out_file = write_autofix_metrics(roots=roots, out_root=args.out_root)
    except AutofixMetricsError as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 1

    data = json.loads(out_file.read_text(encoding="utf-8"))

    print(f"généré           : {data['generated_at']}")
    print(f"cases détectés   : {data['case_count']} (Phase 2, failure_cases/ — seule liste exhaustive)")
    print()

    p4 = data["phase4_diagnosis"]
    print(f"Phase 4  diagnostiqués                : {p4['diagnosed']}")

    p6 = data["phase6_prompt"]
    print(f"Phase 6  prompt généré                : {p6['with_prompt']}")
    print(f"Phase 6  MANUAL_REVIEW_REQUIRED        : {p6['with_manual_review_required']}")

    p7 = data["phase7_worktree"]
    print(f"Phase 7  worktree préparé              : {p7['with_worktree']}")

    p8 = data["phase8_static_validation"]
    print(f"Phase 8  validation statique (run/ACCEPTED/REJECTED) : {p8['run']}/{p8['accepted']}/{p8['rejected']}")

    p9 = data["phase9_patch_replay"]
    print(
        f"Phase 9  patch_replay (run/refused/CORRECTIF_CONFIRME/BUG_PERSISTANT/NON_CONCLUANT) : "
        f"{p9['run']}/{p9['refused']}/{p9['outcome_correctif_confirme']}/"
        f"{p9['outcome_bug_persistant']}/{p9['outcome_non_concluant']}"
    )

    ft = data["first_try_validated"]
    print(f"         validés du premier coup       : {ft['count']}")
    print(f"           définition : {ft['definition']}")

    p10 = data["phase10_live_validation"]
    print(
        f"Phase 10 live_validation (run/refused/CORRECTIF_CONFIRME) : "
        f"{p10['run']}/{p10['refused']}/{p10['outcome_correctif_confirme']}"
    )
    print(
        f"         parmi NON_CONCLUANT Phase 9 ({p10['phase9_non_concluant_count']}) : "
        f"{p10['phase9_non_concluant_then_live_confirme']} confirmés en live"
    )

    p11 = data["phase11a_extractor_integrity"]
    print(f"Phase 11-A intégrité (run/ACCEPTED/REJECTED) : {p11['run']}/{p11['accepted']}/{p11['rejected']}")

    p12 = data["phase12_confidence_score"]
    print(f"Phase 12 confidence_score (run/HIGH/MEDIUM/REJECT) : {p12['run']}/{p12['high']}/{p12['medium']}/{p12['reject']}")

    p13 = data["phase13_human_review"]
    print(f"Phase 13 décisions humaines (APPROVED/REJECTED) : {p13['approved']}/{p13['rejected']}")

    p14 = data["phase14_bem_proposal"]
    print(f"Phase 14 propositions BEM générées    : {p14['generated']}")

    p15 = data["phase15_commit"]
    print(f"Phase 15 commits                       : {p15['commits']}")

    p16 = data["phase16_merge_review"]
    print(f"Phase 16 décisions de merge (APPROVED/REJECTED) : {p16['approved']}/{p16['rejected']}")

    print()
    print(f"répartition par stage  : {data['breakdown_by_stage']}")
    mods = data["breakdown_by_module"]
    print(f"répartition par module (proxy grossier, cf. note) : {mods['counts']}")

    print()
    print(f"régressions : {data['regressions']['note']}")

    if data["warnings"]:
        print()
        print(f"avertissements ({len(data['warnings'])}) :")
        for w in data["warnings"][:20]:
            print(f"  ! {w}")
        if len(data["warnings"]) > 20:
            print(f"  ... et {len(data['warnings']) - 20} de plus (voir {out_file})")

    print()
    print(f"-> {out_file}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
