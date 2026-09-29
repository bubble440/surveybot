"""
run_autofix_pipeline.py — orchestrateur autofix. Une invocation, dans l'ordre
(cf. Survey/autofix/autofix_orchestrator.py, docstring, "Point 0") : (0) verrou
d'exclusion (jamais deux invocations en même temps — toujours actif) ; (1)
étape AVAL (décision Telegram Phase 13 -> commit -> merge local automatique,
sauf --no-downstream) ; (2) étape AMONT (failure_cases/ -> worktree prêt :
Phases 4/5/6/7 enchaînées automatiquement, déduplication incluse, sauf
--no-upstream) ; (3) étape EXISTANTE : Claude Code (mode headless) sur jusqu'à
--max-cases cases préparés, UN À LA FOIS, puis Phases 8/9/11-A/12/13 jusqu'à la
notification humaine si confidence="HIGH". --no-upstream/--no-downstream
restaurent chacun le comportement d'avant leur étape respective ; le verrou,
lui, n'a pas d'option de désactivation.

Étape AVAL (nouvelle) : un seul "Approuver" Telegram (Phase 13) suffit
désormais — la Phase 16 (seconde confirmation avant merge) SORT de la chaîne
orchestrée ; ses modules et tools/propose_merge.py restent utilisables à la
main, mais ce script ne les appelle jamais. Cases éligibles :
--human-reviews-root/<case_id>/decision.json avec decision="APPROVED", sans
--merge-results-root/<case_id>/merge_result.json déjà écrit. MERGED/
ALREADY_MERGED et CONFLICT sont des issues TERMINALES (jamais de retry
automatique — un conflit exige une résolution manuelle) ; un refus
opérationnel (dépôt principal non propre, commit refusé, échec Git) est
retenté à l'invocation suivante. Une notification Telegram concise est
envoyée une fois par changement d'état (mergé/conflit/bloqué).

Éligibilité d'un case pour l'étape EXISTANTE (toutes les conditions
ensemble) : worktree.json (--worktrees-root/<case_id>/worktree.json) présent ;
prompt.txt présent dans --prompts-root/<case_id>/ (jamais
MANUAL_REVIEW_REQUIRED.txt à sa place) ; aucun run_result.json déjà écrit sous
--codex-runs-root/<case_id>/. Avant chaque lancement, un contrôle de sécurité
du parallélisme (Survey/autofix/parallel_safety.py::check_pre_launch_safety,
réutilisée telle quelle) compare le case candidat à tout autre worktree encore
"en vol" (issue non définitive — merge/décision/confiance/validation/
invocation) ; une paire non sûre reporte le case (jamais lancé quand même),
sans consommer son éligibilité future.

Éligibilité d'un case pour l'étape AMONT (sauf --no-upstream) : dossier
--failure-cases-root/<case_id>/ (manifest.json présent) sans worktree.json ni
upstream_run.json terminal, et qui n'est membre d'aucun groupe de doublons
déjà formé (Survey/autofix/case_grouping.py). Le nombre de DIAGNOSTICS (Phase 4)
réellement NOUVEAUX par invocation est borné par --max-upstream-cases (la
Phase 4 lance un vrai Chromium isolé pour un case stage="action") ; un case
déjà diagnostiqué continue d'avancer sans consommer ce budget. Pour retenter
automatiquement un case dont l'étape amont s'est arrêtée sur un état terminal
(MANUAL_REVIEW_REQUIRED.txt, inéligibilité Phase 7, ou fusion dans un
groupe) : supprimer manuellement
--pipeline-runs-root/<case_id>/upstream_run.json. Un échec opérationnel non
terminal (ex. échec Git de la Phase 7) est, lui, retenté automatiquement à
l'invocation suivante, sans action manuelle.

Toute la logique vit dans Survey/autofix/autofix_orchestrator.py ; ce script n'est
qu'une façade CLI. Aucune phase existante n'est réimplémentée : chacune est
importée et appelée telle quelle par ce module (Survey/autofix/static_validator.py,
Survey/autofix/patch_replay.py, Survey/autofix/extractor_integrity_gate.py,
Survey/autofix/confidence_score.py, Survey/autofix/human_review.py,
Survey/autofix/parallel_safety.py, et pour l'étape amont : Survey/autofix/fleet_case_import.py,
Survey/autofix/failure_diagnosis.py, Survey/autofix/case_grouping.py,
Survey/autofix/context_selector.py, Survey/autofix/prompt_generator.py,
Survey/autofix/autofix_worktree.py, et pour l'étape aval : Survey/autofix/patch_commit.py,
Survey/autofix/merge_executor.py).

Codes de sortie : 0 (succès, aucune erreur), 1 (au moins un case en erreur, ou
--max-cases/--max-upstream-cases/--lock-stale-after-s invalide), 3 (verrou
d'exclusion déjà détenu par une autre invocation — pas une erreur d'usage).

Usage :
    python tools\\run_autofix_pipeline.py
    python tools\\run_autofix_pipeline.py --max-cases 3
    python tools\\run_autofix_pipeline.py --claude-timeout-s 900 --force
    python tools\\run_autofix_pipeline.py --no-upstream --no-downstream
    python tools\\run_autofix_pipeline.py --import-fleet --max-upstream-cases 5
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Survey.autofix.autofix_orchestrator import (  # noqa: E402
    DEFAULT_ALLOWED_TOOLS,
    DEFAULT_CLAUDE_TIMEOUT_S,
    DEFAULT_LOCK_STALE_AFTER_S,
    DEFAULT_MAX_CASES,
    DEFAULT_MAX_UPSTREAM_CASES,
    DEFAULT_PERMISSION_MODE,
    AutofixOrchestratorError,
    OrchestratorLockedError,
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
    parser.add_argument("--merge-results-root", default="merge_results", help="Racine des résultats de merge (Survey/autofix/merge_executor.py, défaut : merge_results)")
    parser.add_argument("--merge-reviews-root", default="merge_reviews", help="Racine des revues de merge (Phase 16, défaut : merge_reviews) — plus jamais écrite par ce script (Phase 16 sortie de la chaîne orchestrée), seulement lue par check_pending_reviews pour ne pas voler ses mises à jour Telegram à un usage manuel")
    parser.add_argument("--commit-results-root", default="commit_results", help="Racine des résultats de commit (Phase 15, défaut : commit_results)")
    parser.add_argument("--pipeline-runs-root", default="autofix_pipeline_runs", help="Racine des résumés de chaîne de cet orchestrateur (défaut : autofix_pipeline_runs)")
    parser.add_argument("--max-cases", type=int, default=DEFAULT_MAX_CASES, help=f"Nombre maximum de cases éligibles traités par invocation (défaut : {DEFAULT_MAX_CASES})")
    parser.add_argument("--claude-timeout-s", type=float, default=DEFAULT_CLAUDE_TIMEOUT_S, help=f"Budget de temps (s) de l'invocation Claude Code, défaut={DEFAULT_CLAUDE_TIMEOUT_S}")
    parser.add_argument("--allowed-tools", default=DEFAULT_ALLOWED_TOOLS, help=f"Liste d'outils autorisés pour Claude Code (--allowedTools), défaut={DEFAULT_ALLOWED_TOOLS!r}")
    parser.add_argument("--permission-mode", default=DEFAULT_PERMISSION_MODE, help=f"Mode de permission Claude Code (--permission-mode), défaut={DEFAULT_PERMISSION_MODE!r}")
    parser.add_argument("--force", action="store_true", help="Régénère un artefact déjà existant pour un case traité dans cette invocation (jamais un écrasement silencieux)")
    parser.add_argument("--no-upstream", dest="upstream", action="store_false", default=True, help="Désactive l'étape amont (failure_cases -> worktree) — restaure le comportement d'avant cette étape (worktree.json/prompt.txt déjà présents restent une précondition manuelle)")
    parser.add_argument("--import-fleet", action="store_true", help="Avant l'étape amont, importe les failure_cases disponibles côté stockage fleet (Survey/autofix/fleet_case_import.py) — désactivé par défaut, exige FLEET_R2_* dans l'environnement ; un échec est un avertissement, jamais un arrêt")
    parser.add_argument("--max-upstream-cases", type=int, default=DEFAULT_MAX_UPSTREAM_CASES, help=f"Nombre maximum de DIAGNOSTICS (Phase 4) réellement nouveaux tentés par invocation — un case déjà diagnostiqué n'est pas compté (défaut : {DEFAULT_MAX_UPSTREAM_CASES}, conservateur car la Phase 4 lance un vrai Chromium isolé pour un case stage=\"action\")")
    parser.add_argument("--no-downstream", dest="downstream", action="store_false", default=True, help="Désactive l'étape aval (décision Telegram -> commit -> merge local) — restaure le comportement d'avant cette étape")
    parser.add_argument("--lock-stale-after-s", type=float, default=DEFAULT_LOCK_STALE_AFTER_S, help=f"Âge (s) au-delà duquel un verrou d'exclusion (point 0) déjà présent est considéré abandonné et repris avec un avertissement (défaut : {DEFAULT_LOCK_STALE_AFTER_S}, généreux — doit rester supérieur au temps maximal théorique d'une invocation)")
    args = parser.parse_args(argv)

    try:
        downstream_summaries, upstream_summaries, summaries = run_autofix_pipeline(
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
            merge_results_root=args.merge_results_root,
            merge_reviews_root=args.merge_reviews_root,
            commit_results_root=args.commit_results_root,
            pipeline_runs_root=args.pipeline_runs_root,
            max_cases=args.max_cases,
            claude_timeout_s=args.claude_timeout_s,
            allowed_tools=args.allowed_tools,
            permission_mode=args.permission_mode,
            force=args.force,
            run_upstream=args.upstream,
            import_fleet=args.import_fleet,
            max_upstream_cases=args.max_upstream_cases,
            run_downstream=args.downstream,
            lock_stale_after_s=args.lock_stale_after_s,
        )
    except OrchestratorLockedError as exc:
        print(f"[VERROU] {exc}", file=sys.stderr)
        return 3
    except AutofixOrchestratorError as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 1

    any_error = False

    if downstream_summaries:
        print("== Étape aval (décision Telegram -> merge local) ==")
        for d in downstream_summaries:
            print(f"case          : {d.case_id}")
            print(f"  arrêté à    : {d.stopped_at}")
            if d.stop_reason:
                print(f"  raison      : {d.stop_reason}")
            print(f"  terminal    : {d.terminal}")
            print(f"  notifié     : {d.notified}")
            for stage, path in d.artifacts.items():
                print(f"    - {stage} -> {path}")
            for w in d.warnings:
                print(f"  ! {w}")
            any_error = any_error or d.is_error
        print()

    if upstream_summaries:
        print("== Étape amont (failure_cases -> worktree) ==")
        for u in upstream_summaries:
            print(f"case          : {u.case_id}")
            print(f"  arrêté à    : {u.stopped_at}")
            if u.stop_reason:
                print(f"  raison      : {u.stop_reason}")
            print(f"  terminal    : {u.terminal}")
            for stage, path in u.artifacts.items():
                print(f"    - {stage} -> {path}")
            any_error = any_error or u.is_error
        print()

    if not summaries:
        print("aucun case éligible pour cette invocation (étape existante)")
        return 1 if any_error else 0

    print("== Étape existante (worktree -> patch) ==")
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
