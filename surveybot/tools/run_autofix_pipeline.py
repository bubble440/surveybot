"""
run_autofix_pipeline.py — orchestrateur autofix. Lance automatiquement Claude
Code (mode headless) sur jusqu'à --max-cases cases déjà diagnostiqués, prompt
és et préparés dans un worktree isolé (Phase 7, tools/prepare_autofix_worktree.py
— déjà exécutée séparément, précondition d'éligibilité, jamais déclenchée par
cet outil), UN À LA FOIS, puis enchaîne les phases existantes (8, 9, 11-A, 12,
13) jusqu'à la notification humaine si confidence="HIGH".

Éligibilité d'un case (toutes les conditions ensemble) : worktree.json
(--worktrees-root/<case_id>/worktree.json) présent ; prompt.txt présent dans
--prompts-root/<case_id>/ (jamais MANUAL_REVIEW_REQUIRED.txt à sa place) ;
aucun run_result.json déjà écrit sous --codex-runs-root/<case_id>/. Avant
chaque lancement, un contrôle de sécurité du parallélisme (Survey/
parallel_safety.py::check_pre_launch_safety, réutilisée telle quelle) compare
le case candidat à tout autre worktree déjà présent ("en vol") ; une paire non
sûre reporte le case (jamais lancé quand même), sans consommer son éligibilité
future.

Toute la logique vit dans Survey/autofix_orchestrator.py ; ce script n'est
qu'une façade CLI. Aucune phase existante n'est réimplémentée : chacune est
importée et appelée telle quelle par ce module (Survey/static_validator.py,
Survey/patch_replay.py, Survey/extractor_integrity_gate.py,
Survey/confidence_score.py, Survey/human_review.py,
Survey/parallel_safety.py).

Usage :
    python tools\\run_autofix_pipeline.py
    python tools\\run_autofix_pipeline.py --max-cases 3
    python tools\\run_autofix_pipeline.py --claude-timeout-s 900 --force
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Survey.autofix_orchestrator import (  # noqa: E402
    DEFAULT_ALLOWED_TOOLS,
    DEFAULT_CLAUDE_TIMEOUT_S,
    DEFAULT_MAX_CASES,
    DEFAULT_PERMISSION_MODE,
    AutofixOrchestratorError,
    run_autofix_pipeline,
)


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Lance Claude Code (headless) sur jusqu'à --max-cases cases autofix éligibles, un à la "
            "fois, puis enchaîne les Phases 8/9/11-A/12/13 jusqu'à la notification humaine."
        )
    )
    parser.add_argument("--failure-cases-root", default="failure_cases", help="Racine des failure cases (Phase 2, défaut : failure_cases)")
    parser.add_argument("--diagnoses-root", default="diagnoses", help="Racine des diagnostics (Phase 4, défaut : diagnoses)")
    parser.add_argument("--context-selections-root", default="context_selections", help="Racine des sélections de contexte (Phase 5, défaut : context_selections)")
    parser.add_argument("--prompts-root", default="prompts", help="Racine des prompts (Phase 6, défaut : prompts)")
    parser.add_argument("--worktrees-root", default="autofix_worktrees", help="Racine des worktrees (Phase 7, défaut : autofix_worktrees)")
    parser.add_argument("--codex-runs-root", default="codex_runs", help="Racine des résultats d'invocation Claude Code (défaut : codex_runs)")
    parser.add_argument("--static-validations-root", default="autofix_static_validations", help="Racine des validations statiques (Phase 8, défaut : autofix_static_validations)")
    parser.add_argument("--patch-replays-root", default="patch_replays", help="Racine des rejeux de patch (Phase 9, défaut : patch_replays)")
    parser.add_argument("--extractor-integrity-checks-root", default="extractor_integrity_checks", help="Racine des vérifications d'intégrité (Phase 11-A, défaut : extractor_integrity_checks)")
    parser.add_argument("--confidence-scores-root", default="confidence_scores", help="Racine des scores de confiance (Phase 12, défaut : confidence_scores)")
    parser.add_argument("--human-reviews-root", default="human_reviews", help="Racine des revues humaines (Phase 13, défaut : human_reviews)")
    parser.add_argument("--pipeline-runs-root", default="autofix_pipeline_runs", help="Racine des résumés de chaîne de cet orchestrateur (défaut : autofix_pipeline_runs)")
    parser.add_argument("--max-cases", type=int, default=DEFAULT_MAX_CASES, help=f"Nombre maximum de cases éligibles traités par invocation (défaut : {DEFAULT_MAX_CASES})")
    parser.add_argument("--claude-timeout-s", type=float, default=DEFAULT_CLAUDE_TIMEOUT_S, help=f"Budget de temps (s) de l'invocation Claude Code, défaut={DEFAULT_CLAUDE_TIMEOUT_S}")
    parser.add_argument("--allowed-tools", default=DEFAULT_ALLOWED_TOOLS, help=f"Liste d'outils autorisés pour Claude Code (--allowedTools), défaut={DEFAULT_ALLOWED_TOOLS!r}")
    parser.add_argument("--permission-mode", default=DEFAULT_PERMISSION_MODE, help=f"Mode de permission Claude Code (--permission-mode), défaut={DEFAULT_PERMISSION_MODE!r}")
    parser.add_argument("--force", action="store_true", help="Régénère un artefact déjà existant pour un case traité dans cette invocation (jamais un écrasement silencieux)")
    args = parser.parse_args(argv)

    try:
        summaries = run_autofix_pipeline(
            failure_cases_root=args.failure_cases_root,
            diagnoses_root=args.diagnoses_root,
            context_selections_root=args.context_selections_root,
            prompts_root=args.prompts_root,
            worktrees_root=args.worktrees_root,
            codex_runs_root=args.codex_runs_root,
            static_validations_root=args.static_validations_root,
            patch_replays_root=args.patch_replays_root,
            extractor_integrity_checks_root=args.extractor_integrity_checks_root,
            confidence_scores_root=args.confidence_scores_root,
            human_reviews_root=args.human_reviews_root,
            pipeline_runs_root=args.pipeline_runs_root,
            max_cases=args.max_cases,
            claude_timeout_s=args.claude_timeout_s,
            allowed_tools=args.allowed_tools,
            permission_mode=args.permission_mode,
            force=args.force,
        )
    except AutofixOrchestratorError as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 1

    if not summaries:
        print("aucun case éligible pour cette invocation")
        return 0

    any_error = False
    for summary in summaries:
        print(f"case          : {summary.case_id}")
        if summary.deferred:
            print(f"  reporté (pas traité cette fois) : {summary.stop_reason}")
        else:
            print(f"  arrêté à    : {summary.stopped_at}")
            if summary.stop_reason:
                print(f"  raison      : {summary.stop_reason}")
            if summary.confidence:
                print(f"  confiance   : {summary.confidence}")
            print(f"  notifié     : {summary.notified}")
        for stage, path in summary.artifacts.items():
            print(f"    - {stage} -> {path}")
        for w in summary.warnings:
            print(f"  ! {w}")
        any_error = any_error or summary.is_error

    return 1 if any_error else 0


if __name__ == "__main__":
    sys.exit(main())
