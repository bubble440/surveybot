from __future__ import annotations

"""Phase 16 — proposition de merge semi-automatique : notifie un humain via
Telegram (branche autofix, branche cible, sha/sujet du commit produit par la
Phase 15) et capture sa confirmation/annulation — NE DÉCLENCHE JAMAIS elle-même
un git merge, un push, ni aucune modification de la branche cible. La
confirmation humaine reste un signal à vérifier manuellement par l'opérateur
avant d'exécuter le merge lui-même (hors périmètre de ce chantier).

Nouveau module, en LECTURE SEULE sur commit_result.json (Phase 15) et
worktree.json (Phase 7) — ne recalcule, ne relance et ne réexécute jamais rien
de ces phases. Ne touche à aucun extracteur, aucune stratégie de dispatch, ni
à aucun autre module existant du pipeline.

── Plomberie Telegram : réutilisée, jamais réimplémentée ─────────────────────
Telegram ne fournit qu'UN SEUL flux getUpdates par bot (cf. Survey/autofix/
human_review.py, Phase 16, docstring du module) : ce module importe et
réutilise TELLES QUELLES la fonction d'envoi partagée (_send_two_button_review)
et les préfixes de callback_data dédiés à cette phase (_CB_MERGE_APPROVE_PREFIX/
_CB_MERGE_REJECT_PREFIX) — jamais un second poller indépendant. La capture de
la décision elle-même (getUpdates, routage, écriture de decision.json) est
entièrement déléguée à Survey/autofix/human_review.py::check_pending_reviews
(tools/check_human_review.py), déjà généralisé pour interroger human_reviews/
ET merge_reviews/ dans la même invocation.

── Précondition ──────────────────────────────────────────────────────────────
commit_result.json (Phase 15) doit exister, être lisible, et indiquer un
commit réussi OU déjà committé pour ce case (commit_sha non vide — les deux
cas le renseignent) — sinon refus explicite. worktree.json (Phase 7) doit
exister et être lisible : source_branch (branche cible du merge) en est repris
TEL QUEL, jamais une branche supposée/codée en dur. case_id et branch doivent
être cohérents entre les deux artefacts et les deux dossiers fournis.

── Comportement ──────────────────────────────────────────────────────────────
1. Compose un message Telegram de confirmation : branche autofix concernée,
   branche cible (worktree.json.source_branch, tel quel), sha et sujet du
   commit (commit_result.json) — jamais le diff complet.
2. Envoie ce message avec un clavier inline à deux boutons ("Confirmer le
   merge" / "Annuler"), persiste l'accusé sous merge_reviews/<case_id>/
   pending.json (même convention que Phase 13, dossier distinct).
3. Ne déclenche jamais elle-même un git merge, un push, ni aucune modification
   de la branche cible.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from Survey.autofix.human_review import (
    HumanReviewError,
    HumanReviewExistsError,
    ReviewRequestResult,
    _CB_MERGE_APPROVE_PREFIX,
    _CB_MERGE_REJECT_PREFIX,
    _is_safe_case_id,
    _load_json,
    _send_two_button_review,
)

_TAG = "[MERGE_REVIEW]"


class MergeReviewError(Exception):
    """Refus contrôlé — inéligibilité, ou échec de la plomberie Telegram partagée."""


class MergeReviewExistsError(MergeReviewError):
    """pending.json ou decision.json existe déjà pour ce case (sans --force)."""


# ─────────────────────────────── Éligibilité ─────────────────────────────────

@dataclass
class MergeEligibilityResult:
    eligible: bool
    case_id: Optional[str]
    reasons: "list[str]" = field(default_factory=list)


def check_merge_proposal_eligibility(
    *,
    commit_result_dir: "str | Path",
    worktree_dir: "str | Path",
) -> MergeEligibilityResult:
    """Vérifie toutes les conditions ensemble ; ne s'arrête jamais à la
    première raison de refus rencontrée."""
    commit_result_dir = Path(commit_result_dir)
    worktree_dir = Path(worktree_dir)
    reasons: "list[str]" = []

    commit_result, cr_err = _load_json(commit_result_dir / "commit_result.json")
    if cr_err or not isinstance(commit_result, dict):
        reasons.append(f"commit_result.json {cr_err or 'ne contient pas un objet JSON'}")

    worktree, wt_err = _load_json(worktree_dir / "worktree.json")
    if wt_err or not isinstance(worktree, dict):
        reasons.append(f"worktree.json {wt_err or 'ne contient pas un objet JSON'}")

    if cr_err or wt_err or not isinstance(commit_result, dict) or not isinstance(worktree, dict):
        return MergeEligibilityResult(eligible=False, case_id=None, reasons=reasons)

    case_ids = {
        "commit_result.json": str(commit_result.get("case_id") or ""),
        "worktree.json": str(worktree.get("case_id") or ""),
        "commit_result_dir": commit_result_dir.name,
        "worktree_dir": worktree_dir.name,
    }
    distinct = set(case_ids.values())
    resolved_case_id: Optional[str]
    if len(distinct) != 1 or "" in distinct:
        reasons.append(f"case_id incohérent entre les sources : {case_ids}")
        resolved_case_id = None
    else:
        resolved_case_id = next(iter(distinct))
        if not _is_safe_case_id(resolved_case_id):
            reasons.append(
                f"case_id {resolved_case_id!r} n'est pas utilisable sans transformation comme "
                "composant de chemin"
            )

    commit_sha = str(commit_result.get("commit_sha") or "")
    if not commit_sha:
        reasons.append(
            "commit_result.json (Phase 15) : commit_sha absent/vide — aucun commit réussi (ou déjà "
            "committé) pour ce case"
        )

    commit_branch = str(commit_result.get("branch") or "")
    worktree_branch = str(worktree.get("branch") or "")
    if commit_branch and worktree_branch and commit_branch != worktree_branch:
        reasons.append(
            f"branch incohérente entre commit_result.json ({commit_branch!r}) et worktree.json "
            f"({worktree_branch!r})"
        )

    return MergeEligibilityResult(eligible=not reasons, case_id=resolved_case_id, reasons=reasons)


# ────────────────────────────── Message Telegram ─────────────────────────────

def _compose_merge_message(*, case_id: str, worktree: dict, commit_result: dict) -> str:
    """Branche autofix, branche cible (source_branch tel quel), sha/sujet du
    commit — jamais le diff complet."""
    autofix_branch = str(worktree.get("branch") or "?")
    target_branch = str(worktree.get("source_branch") or "?")
    commit_sha = str(commit_result.get("commit_sha") or "?")
    commit_subject = str(commit_result.get("commit_subject") or "?")

    return (
        f"🔀 Confirmation de merge requise — case {case_id}\n\n"
        f"Branche autofix : {autofix_branch}\n"
        f"Branche cible : {target_branch}\n"
        f"Commit : {commit_sha[:12]} — {commit_subject}\n\n"
        "Rappel : cette confirmation ne déclenche aucun merge automatique — "
        "l'opérateur doit exécuter le merge lui-même après confirmation."
    )


# ────────────────────────────────── Orchestration ────────────────────────────

def send_merge_confirmation_request(
    *,
    commit_result_dir: "str | Path",
    worktree_dir: "str | Path",
    out_root: "str | Path" = "merge_reviews",
    force: bool = False,
) -> ReviewRequestResult:
    commit_result_dir = Path(commit_result_dir)
    worktree_dir = Path(worktree_dir)

    eligibility = check_merge_proposal_eligibility(
        commit_result_dir=commit_result_dir,
        worktree_dir=worktree_dir,
    )
    if not eligibility.eligible:
        raise MergeReviewError(
            "case non éligible pour la Phase 16 : " + " ; ".join(eligibility.reasons)
        )
    case_id = eligibility.case_id
    assert case_id is not None  # garanti par eligible=True

    commit_result, _ = _load_json(commit_result_dir / "commit_result.json")
    worktree, _ = _load_json(worktree_dir / "worktree.json")
    text = _compose_merge_message(case_id=case_id, worktree=worktree, commit_result=commit_result)

    try:
        return _send_two_button_review(
            out_root=Path(out_root),
            case_id=case_id,
            text=text,
            approve_prefix=_CB_MERGE_APPROVE_PREFIX,
            approve_label="✅ Confirmer le merge",
            reject_prefix=_CB_MERGE_REJECT_PREFIX,
            reject_label="❌ Annuler",
            force=force,
            log_tag=_TAG,
        )
    except HumanReviewExistsError as exc:
        raise MergeReviewExistsError(str(exc)) from exc
    except HumanReviewError as exc:
        raise MergeReviewError(str(exc)) from exc
