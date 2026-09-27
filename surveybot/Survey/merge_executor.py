from __future__ import annotations

"""Exécution automatique du merge local dès confirmation Telegram (Phase 16,
Survey/merge_review.py — non modifiée, réutilisée telle quelle pour la
confirmation elle-même ; ce module ne fait qu'agir une fois decision.json déjà
écrit). Nouveau module, additif. Ne touche à aucun extracteur, ni à
Survey/merge_review.py/Survey/human_review.py existants. Ne recalcule, ne
relance et ne réexécute jamais rien des phases précédentes — lecture seule sur
leurs artefacts déjà produits (decision.json Phase 16, worktree.json Phase 7,
commit_result.json Phase 15).

── Différence majeure avec toutes les phases précédentes (7 à 16) ────────────
Phases 7 à 16 n'opèrent QUE dans le worktree Git isolé (autofix_worktrees/
<case_id>/), jamais sur le dépôt principal ni son checkout — Phase 7 le dit
explicitement dans sa propre docstring ("jamais un git checkout qui
bougerait le dépôt principal du développeur"). CE module fait l'inverse par
nécessité : la demande d'origine exige que le merge atterrisse sur la VRAIE
branche cible (worktree.json.source_branch) DU DÉPÔT PRINCIPAL, jamais dans
le worktree jetable. C'est donc la première phase de ce chantier qui bascule
et modifie le checkout réel de l'opérateur — traité avec une prudence accrue
en conséquence (dépôt principal exigé propre avant tout basculement de
branche, jamais un stash automatique, jamais un `git checkout` si le dépôt
n'est pas déjà sûr, cf. check_repo_ready_for_merge ci-dessous).

── Précondition (check_merge_execution_eligibility) : toutes ensemble ───────
- merge_reviews/<case_id>/decision.json (Phase 16, Survey/merge_review.py +
  Survey/human_review.py::check_pending_reviews — schéma vérifié dans le code
  avant d'écrire ce module : {schema_version, case_id, decision,
  decided_at, telegram_user_id, telegram_username}, AUCUN champ "kind" —
  c'est le dossier merge_reviews/ lui-même, jamais un champ interne, qui
  distingue cette décision d'une décision Phase 13) : decision="APPROVED"
  exactement.
- worktree.json (Phase 7) : désigne un worktree Git réel
  (`git rev-parse --is-inside-work-tree`, jamais une simple présence de
  dossier) et la branche effectivement checked-out à cet endroit correspond à
  worktree.json.branch — même garde exacte que Survey/patch_commit.py::
  check_commit_eligibility (dupliquée ici, modules indépendants, même
  convention que le reste de ce chantier — jamais un refactor de
  Survey/patch_commit.py pour en extraire un helper partagé, cf. RÈGLES
  STRICTES "pas de refactor des phases existantes").
- commit_result.json (Phase 15, Survey/patch_commit.py — schéma vérifié dans
  le code : {schema_version, case_id, branch, already_committed, commit_sha,
  commit_subject, ...}) : commit_sha non vide.
- case_id cohérent entre les trois artefacts et les trois dossiers fournis.
- branch (worktree.json, la branche autofix) hors PROTECTED_BRANCHES
  (Survey/autofix_worktree.py, réutilisée telle quelle) : défense en
  profondeur symbolique reprise de la Phase 15, ne peut structurellement pas
  se déclencher (préfixe "autofix/" toujours présent).
- source_branch (worktree.json, la VRAIE cible du merge) hors
  PROTECTED_BRANCHES : ICI, contrairement au point précédent, une protection
  RÉELLE — source_branch est purement informatif en Phase 7 (résolu via
  `git symbolic-ref` sur HEAD au moment de la création du worktree, jamais
  vérifié ni contraint) : un opérateur ayant lancé le pipeline autofix alors
  que son dépôt était checked-out sur main/prod/playwright-migration
  produirait un worktree.json avec source_branch="main" (etc.) sans qu'aucune
  phase précédente ne s'y oppose. Ce module refuse explicitement dans ce cas
  — c'est la seule protection réelle contre "Ne touche jamais à
  main/prod/playwright-migration" pour LA CIBLE du merge.
- source_branch non vide et différent du texte littéral "HEAD (detached)"
  (placeholder de Survey/autofix_worktree.py::_current_branch_label quand le
  dépôt était en detached HEAD à la création du worktree) — jamais une
  branche cible devinée.

── Comportement (execute_confirmed_merge) ────────────────────────────────────
1. Dépôt principal résolu via `git rev-parse --show-toplevel` depuis le
   répertoire courant du process (Path.cwd()) — même stratégie exacte que
   Survey/autofix_worktree.py::prepare_autofix_worktree pour son propre
   repo_root, JAMAIS worktree_path (qui désigne le worktree isolé, hors de
   propos ici).
2. source_branch vérifiée comme référence Git réelle dans CE dépôt
   (`git show-ref --verify --quiet refs/heads/<source_branch>`) — refus
   explicite sinon, jamais une branche créée à la volée.
3. Recherche d'un merge déjà effectué pour ce case_id : réutilise TELLE
   QUELLE Survey/patch_commit.py::_find_existing_case_commit (même fonction,
   même marqueur exact "case_id=<case_id>" en ligne de corps de commit —
   générique sur (cwd, branch, case_id), donc réutilisable sans modification
   pour chercher dans le dépôt principal/source_branch plutôt que dans le
   worktree/la branche autofix comme le fait déjà la Phase 15). Trouvé :
   status="ALREADY_MERGED", AUCUNE autre action (idempotent, jamais un
   deuxième merge, jamais une erreur).
4. Sinon, dépôt principal exigé ENTIÈREMENT PROPRE
   (`git status --porcelain` vide) avant tout basculement de branche — refus
   explicite sinon, jamais un stash automatique, jamais un `git checkout`
   d'un dépôt en état incertain.
5. Si la branche courante du dépôt principal n'est pas déjà source_branch :
   `git checkout source_branch` (refus explicite si cette commande échoue,
   ne devrait pas arriver après les vérifications précédentes). Déjà sur
   source_branch : aucun basculement, évite un aller-retour inutile.
6. `git merge --no-ff <branche autofix> -F <fichier temporaire>` — message
   composé du sujet de commit (commit_result.json.commit_subject, Phase 15,
   repris tel quel) et d'une ligne de corps exacte "case_id=<case_id>" (même
   marqueur que le point 3, pour qu'une future invocation le retrouve). Fichier
   temporaire plutôt qu'un argument -m direct : git merge n'a pas d'équivalent
   à `git commit -F -` (pas de lecture stdin) — même prudence que
   Survey/patch_commit.py sur un message multi-lignes, adaptée à la
   contrainte réelle de `git merge`.
7. Code de sortie non nul (conflit réel ou tout autre échec du merge) :
   `git merge --abort` immédiatement (best-effort, journalisé si lui-même
   échoue, jamais une exception supplémentaire propagée pour cela) — jamais
   un merge à moitié résolu laissé en l'état. status="CONFLICT", raw_output
   du merge conservé pour résolution manuelle. Jamais de résolution
   automatique.
8. Succès : sha du merge (`git rev-parse HEAD`), status="MERGED".
   Le dépôt principal reste sur source_branch après un succès (c'est le
   point d'atterrissage attendu pour la suite manuelle — jamais un retour
   automatique à la branche d'avant, qui surprendrait l'opérateur).

── RÈGLES STRICTES respectées ─────────────────────────────────────────────────
Jamais de push, jamais de déclenchement de release/déploiement (aucune
commande de ce type n'existe dans ce module). Jamais de résolution
automatique d'un conflit réel. Budget de temps explicite (git_timeout_s,
unique pour toutes les commandes git de ce module — même convention que
Survey/patch_commit.py) sur chaque commande. Patch minimal : aucune des
Phases 7/15/16 n'est modifiée, seulement importée/réutilisée
(PROTECTED_BRANCHES, _find_existing_case_commit).

── Sortie ──────────────────────────────────────────────────────────────────
merge_results/<case_id>/merge_result.json — même convention JSON que les
phases précédentes (schema_version/case_id/created_at). Refus explicite
(jamais un écrasement silencieux) si une sortie existe déjà sans force=True.
"""

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from Survey.autofix_worktree import PROTECTED_BRANCHES
from Survey.log_utils import log_debug, log_info
from Survey.patch_commit import PatchCommitError, _find_existing_case_commit

_TAG = "[MERGE_EXECUTOR]"
SCHEMA_VERSION = "1.0"

DEFAULT_GIT_TIMEOUT_S = 15.0

# Placeholder littéral de Survey/autofix_worktree.py::_current_branch_label
# quand le dépôt était en detached HEAD à la création du worktree — jamais
# une branche cible réelle.
_DETACHED_HEAD_PLACEHOLDER = "HEAD (detached)"

STATUS_MERGED = "MERGED"
STATUS_ALREADY_MERGED = "ALREADY_MERGED"
STATUS_CONFLICT = "CONFLICT"

# Composant de chemin unique, allowlist conservatrice — même garde-fou dupliqué
# volontairement dans plusieurs modules indépendants de ce pipeline (cf.
# Survey/autofix_worktree.py, Survey/human_review.py, Survey/bem_proposal.py,
# Survey/patch_commit.py).
_CASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
_WINDOWS_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


class MergeExecutionError(Exception):
    """Refus contrôlé — inéligibilité, dépôt principal non prêt, ou échec Git
    d'une commande hors merge lui-même. Jamais levée pour un conflit de merge
    réel (cf. MergeExecutionResult.status=CONFLICT, jamais une exception)."""


class MergeExecutionExistsError(MergeExecutionError):
    """merge_result.json existe déjà pour ce case (sans --force)."""


def _load_json(path: Path) -> "tuple[Any, Optional[str]]":
    if not path.is_file():
        return None, f"{path} absent"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except OSError as exc:
        return None, f"{path} illisible ({exc})"
    except ValueError as exc:  # json.JSONDecodeError est une sous-classe de ValueError
        return None, f"{path} JSON invalide ({exc})"


def _is_safe_case_id(case_id: str) -> bool:
    if not case_id or not _CASE_ID_RE.match(case_id):
        return False
    if ".." in case_id:
        return False
    if case_id.lower() in _WINDOWS_RESERVED_NAMES:
        return False
    return True


def _run_git(args: "list[str]", *, cwd: Path, timeout: float) -> "tuple[bool, str, str, bool]":
    """Une seule stratégie d'exécution de sous-processus, jamais de retry ; un
    budget de temps explicite. Retourne (ok, stdout, stderr, timed_out)."""
    log_debug(_TAG, f"git {' '.join(args)} (cwd={cwd}, timeout={timeout}s)")
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout, check=False,
        )
        return proc.returncode == 0, proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired:
        return False, "", f"dépassement du budget ({timeout}s)", True
    except OSError as exc:
        return False, "", f"exécution impossible : {exc}", False


# ─────────────────────────────── Éligibilité ─────────────────────────────────

@dataclass
class MergeExecutionEligibilityResult:
    eligible: bool
    case_id: Optional[str]
    reasons: "list[str]" = field(default_factory=list)
    worktree: Optional[dict] = None
    commit_result: Optional[dict] = None


def check_merge_execution_eligibility(
    *,
    merge_review_dir: "str | Path",
    worktree_dir: "str | Path",
    commit_result_dir: "str | Path",
    git_timeout_s: float = DEFAULT_GIT_TIMEOUT_S,
) -> MergeExecutionEligibilityResult:
    """Vérifie toutes les conditions ensemble ; ne s'arrête jamais à la
    première raison de refus rencontrée — même principe que les phases
    précédentes de ce chantier."""
    merge_review_dir = Path(merge_review_dir)
    worktree_dir = Path(worktree_dir)
    commit_result_dir = Path(commit_result_dir)
    reasons: "list[str]" = []

    decision, dec_err = _load_json(merge_review_dir / "decision.json")
    if dec_err or not isinstance(decision, dict):
        reasons.append(f"decision.json (Phase 16) {dec_err or 'ne contient pas un objet JSON'}")

    worktree, wt_err = _load_json(worktree_dir / "worktree.json")
    if wt_err or not isinstance(worktree, dict):
        reasons.append(f"worktree.json (Phase 7) {wt_err or 'ne contient pas un objet JSON'}")

    commit_result, cr_err = _load_json(commit_result_dir / "commit_result.json")
    if cr_err or not isinstance(commit_result, dict):
        reasons.append(f"commit_result.json (Phase 15) {cr_err or 'ne contient pas un objet JSON'}")

    if dec_err or wt_err or cr_err or not all(
        isinstance(x, dict) for x in (decision, worktree, commit_result)
    ):
        return MergeExecutionEligibilityResult(eligible=False, case_id=None, reasons=reasons)

    decision_value = str(decision.get("decision") or "")
    if decision_value != "APPROVED":
        reasons.append(f"decision.json (Phase 16) : decision={decision_value!r} — \"APPROVED\" requis")

    commit_sha = str(commit_result.get("commit_sha") or "")
    if not commit_sha:
        reasons.append("commit_result.json (Phase 15) : commit_sha absent/vide")

    branch = str(worktree.get("branch") or "")
    if branch in PROTECTED_BRANCHES:
        reasons.append(f"worktree.json : branch={branch!r} est une branche protégée (PROTECTED_BRANCHES)")

    source_branch = str(worktree.get("source_branch") or "")
    if not source_branch or source_branch == _DETACHED_HEAD_PLACEHOLDER:
        reasons.append(
            f"worktree.json : source_branch={source_branch!r} n'est pas une branche cible exploitable "
            "(absente, ou dépôt en detached HEAD à la création du worktree) — jamais une cible devinée"
        )
    elif source_branch in PROTECTED_BRANCHES:
        reasons.append(
            f"worktree.json : source_branch={source_branch!r} est une branche protégée "
            "(PROTECTED_BRANCHES) — jamais une cible de merge pour ce module"
        )

    worktree_path = Path(str(worktree.get("worktree_path") or ""))
    if not worktree_path.is_dir():
        reasons.append(f"worktree.json : worktree_path introuvable sur disque : {worktree_path}")
    else:
        ok, out, err, timed_out = _run_git(
            ["rev-parse", "--is-inside-work-tree"], cwd=worktree_path, timeout=git_timeout_s,
        )
        if timed_out:
            reasons.append(f"vérification du worktree Git a dépassé son budget ({git_timeout_s}s)")
        elif not ok or out.strip() != "true":
            reasons.append(
                f"worktree.json : worktree_path ne désigne pas un worktree Git réel : {err.strip() or out.strip()}"
            )
        elif branch:
            ok2, out2, _err2, timed_out2 = _run_git(
                ["symbolic-ref", "-q", "--short", "HEAD"], cwd=worktree_path, timeout=git_timeout_s,
            )
            if timed_out2:
                reasons.append(f"vérification de la branche checked-out a dépassé son budget ({git_timeout_s}s)")
            elif not ok2 or out2.strip() != branch:
                reasons.append(
                    f"worktree.json : la branche checked-out du worktree ({out2.strip() or 'detached'!r}) "
                    f"ne correspond pas à branch={branch!r} — worktree potentiellement manipulé manuellement"
                )

    case_ids = {
        "decision.json": str(decision.get("case_id") or ""),
        "worktree.json": str(worktree.get("case_id") or ""),
        "commit_result.json": str(commit_result.get("case_id") or ""),
        "merge_review_dir": merge_review_dir.name,
        "worktree_dir": worktree_dir.name,
        "commit_result_dir": commit_result_dir.name,
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

    return MergeExecutionEligibilityResult(
        eligible=not reasons, case_id=resolved_case_id, reasons=reasons,
        worktree=worktree, commit_result=commit_result,
    )


# ────────────────────────────────── Orchestration ────────────────────────────

@dataclass
class MergeExecutionResult:
    case_id: str
    autofix_branch: str
    target_branch: str
    status: str  # MERGED | ALREADY_MERGED | CONFLICT
    merge_sha: Optional[str]
    reason: Optional[str]
    warnings: "list[str]" = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "case_id": self.case_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "autofix_branch": self.autofix_branch,
            "target_branch": self.target_branch,
            "status": self.status,
            "merge_sha": self.merge_sha,
            "reason": self.reason,
            "warnings": self.warnings,
        }


def _compose_merge_message(*, case_id: str, autofix_branch: str, commit_subject: str) -> str:
    return f"Merge {autofix_branch}: {commit_subject}\n\ncase_id={case_id}\n"


def execute_confirmed_merge(
    *,
    merge_review_dir: "str | Path",
    worktree_dir: "str | Path",
    commit_result_dir: "str | Path",
    git_timeout_s: float = DEFAULT_GIT_TIMEOUT_S,
) -> MergeExecutionResult:
    """Exécute le merge local confirmé pour un case. Lève MergeExecutionError
    si une précondition manque ou si le dépôt principal n'est pas dans un état
    sûr pour basculer de branche — jamais un effet de bord dans ce cas. Un
    conflit de merge RÉEL n'est jamais une exception : status=CONFLICT,
    merge abandonné proprement, résolution manuelle requise."""
    eligibility = check_merge_execution_eligibility(
        merge_review_dir=merge_review_dir,
        worktree_dir=worktree_dir,
        commit_result_dir=commit_result_dir,
        git_timeout_s=git_timeout_s,
    )
    if not eligibility.eligible:
        raise MergeExecutionError(
            "case non éligible à l'exécution du merge : " + " ; ".join(eligibility.reasons)
        )
    case_id = eligibility.case_id
    assert case_id is not None  # garanti par eligible=True
    worktree = eligibility.worktree or {}
    commit_result = eligibility.commit_result or {}

    autofix_branch = str(worktree.get("branch") or "")
    target_branch = str(worktree.get("source_branch") or "")
    commit_subject = str(commit_result.get("commit_subject") or "")

    warnings: "list[str]" = []

    # 1) Dépôt principal réel, résolu depuis l'environnement (répertoire courant
    # du process) — même stratégie exacte que Survey/autofix_worktree.py, jamais
    # worktree_path (le worktree isolé, hors de propos ici).
    ok_top, top_out, top_err, timed_out_top = _run_git(
        ["rev-parse", "--show-toplevel"], cwd=Path.cwd(), timeout=git_timeout_s,
    )
    if timed_out_top:
        raise MergeExecutionError(f"git rev-parse --show-toplevel a dépassé son budget ({git_timeout_s}s)")
    if not ok_top:
        raise MergeExecutionError(f"dépôt Git principal introuvable depuis {Path.cwd()} : {top_err.strip()}")
    main_repo_root = Path(top_out.strip())

    # 2) source_branch doit être une référence Git réelle DANS CE DÉPÔT.
    ok_ref, _out_ref, err_ref, timed_out_ref = _run_git(
        ["show-ref", "--verify", "--quiet", f"refs/heads/{target_branch}"],
        cwd=main_repo_root, timeout=git_timeout_s,
    )
    if timed_out_ref:
        raise MergeExecutionError(f"vérification de la branche cible a dépassé son budget ({git_timeout_s}s)")
    if not ok_ref:
        raise MergeExecutionError(
            f"source_branch={target_branch!r} (worktree.json) n'existe pas comme branche locale dans le "
            f"dépôt principal ({main_repo_root}) : {err_ref.strip()}"
        )

    # 3) Idempotence : un merge pour ce case_id existe-t-il déjà sur target_branch ?
    # Réutilise TELLE QUELLE Survey/patch_commit.py::_find_existing_case_commit
    # (générique sur cwd/branch/case_id — aucune modification nécessaire).
    try:
        existing_sha = _find_existing_case_commit(main_repo_root, target_branch, case_id, timeout=git_timeout_s)
    except PatchCommitError as exc:
        raise MergeExecutionError(
            f"recherche d'un merge déjà existant (Survey/patch_commit.py::_find_existing_case_commit, "
            f"réutilisée telle quelle) échouée : {exc}"
        ) from exc

    if existing_sha:
        log_info(_TAG, f"case={case_id} : déjà mergé sur {target_branch!r} (sha={existing_sha[:12]})")
        return MergeExecutionResult(
            case_id=case_id, autofix_branch=autofix_branch, target_branch=target_branch,
            status=STATUS_ALREADY_MERGED, merge_sha=existing_sha, reason=None, warnings=warnings,
        )

    # 4) Dépôt principal exigé entièrement propre avant tout basculement de
    # branche — jamais un stash automatique, jamais une hypothèse fragile sur
    # l'état du dépôt de l'opérateur.
    ok_status, status_out, status_err, timed_out_status = _run_git(
        ["status", "--porcelain", "--untracked-files=all"], cwd=main_repo_root, timeout=git_timeout_s,
    )
    if timed_out_status:
        raise MergeExecutionError(f"git status (dépôt principal) a dépassé son budget ({git_timeout_s}s)")
    if not ok_status:
        raise MergeExecutionError(f"git status (dépôt principal) a échoué : {status_err.strip()}")
    if status_out.strip():
        raise MergeExecutionError(
            f"dépôt principal ({main_repo_root}) non propre (changements en attente) — basculement de "
            "branche refusé plutôt que risquer de mélanger l'état de l'opérateur ; committer/stasher "
            "manuellement avant de relancer"
        )

    # 5) Basculement vers target_branch, seulement si nécessaire.
    ok_cur, cur_out, _cur_err, timed_out_cur = _run_git(
        ["symbolic-ref", "-q", "--short", "HEAD"], cwd=main_repo_root, timeout=git_timeout_s,
    )
    current_branch = cur_out.strip() if ok_cur and not timed_out_cur else None
    if current_branch != target_branch:
        ok_co, _out_co, err_co, timed_out_co = _run_git(
            ["checkout", target_branch], cwd=main_repo_root, timeout=git_timeout_s,
        )
        if timed_out_co:
            raise MergeExecutionError(f"git checkout {target_branch} a dépassé son budget ({git_timeout_s}s)")
        if not ok_co:
            raise MergeExecutionError(f"git checkout {target_branch} (dépôt principal) a échoué : {err_co.strip()}")
    else:
        log_debug(_TAG, f"case={case_id} : dépôt principal déjà sur {target_branch!r}, aucun basculement")

    # 6) git merge --no-ff, message via fichier temporaire (git merge n'a pas
    # d'équivalent à `git commit -F -`, cf. docstring du module).
    message = _compose_merge_message(
        case_id=case_id, autofix_branch=autofix_branch, commit_subject=commit_subject,
    )
    fd, tmp_path = tempfile.mkstemp(suffix=".txt", prefix="merge_message_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(message)
        ok_merge, merge_out, merge_err, timed_out_merge = _run_git(
            ["merge", "--no-ff", autofix_branch, "-F", tmp_path], cwd=main_repo_root, timeout=git_timeout_s,
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if timed_out_merge or not ok_merge:
        # 7) Conflit réel (ou tout autre échec) — abandon propre, jamais à
        # moitié résolu, jamais une résolution automatique.
        reason = (
            f"merge --no-ff {autofix_branch} a dépassé son budget ({git_timeout_s}s)" if timed_out_merge
            else f"merge --no-ff {autofix_branch} a échoué : {(merge_err or merge_out).strip()[-4000:]}"
        )
        ok_abort, _out_abort, err_abort, timed_out_abort = _run_git(
            ["merge", "--abort"], cwd=main_repo_root, timeout=git_timeout_s,
        )
        if timed_out_abort or not ok_abort:
            warnings.append(
                f"git merge --abort lui-même a échoué après le conflit — vérifier manuellement l'état de "
                f"{main_repo_root} avant toute nouvelle tentative : {err_abort.strip()}"
            )
        log_info(_TAG, f"case={case_id} : conflit de merge sur {target_branch!r} — abandonné, résolution manuelle requise")
        return MergeExecutionResult(
            case_id=case_id, autofix_branch=autofix_branch, target_branch=target_branch,
            status=STATUS_CONFLICT, merge_sha=None, reason=reason, warnings=warnings,
        )

    # 8) Succès.
    ok_head, head_out, head_err, timed_out_head = _run_git(
        ["rev-parse", "HEAD"], cwd=main_repo_root, timeout=git_timeout_s,
    )
    if timed_out_head or not ok_head:
        raise MergeExecutionError(f"merge réussi mais résolution de HEAD échouée : {head_err.strip()}")
    merge_sha = head_out.strip()

    log_info(
        _TAG,
        f"case={case_id} : merge {autofix_branch} -> {target_branch} réussi (sha={merge_sha[:12]})",
    )
    return MergeExecutionResult(
        case_id=case_id, autofix_branch=autofix_branch, target_branch=target_branch,
        status=STATUS_MERGED, merge_sha=merge_sha, reason=None, warnings=warnings,
    )


def _output_paths(out_root: Path, case_id: str) -> "tuple[Path, Path]":
    out_dir = out_root / case_id
    return out_dir, out_dir / "merge_result.json"


def write_merge_result(
    *,
    merge_review_dir: "str | Path",
    worktree_dir: "str | Path",
    commit_result_dir: "str | Path",
    out_root: "str | Path" = "merge_results",
    force: bool = False,
    git_timeout_s: float = DEFAULT_GIT_TIMEOUT_S,
) -> Path:
    """Exécute execute_confirmed_merge et écrit
    out_root/<case_id>/merge_result.json. Lève MergeExecutionExistsError si la
    sortie existe déjà et force=False — jamais d'écrasement silencieux."""
    merge_review_dir = Path(merge_review_dir)
    out_root = Path(out_root)

    # Résolution du case_id AVANT tout effet de bord (même principe que
    # Survey/autofix_worktree.py::write_worktree_manifest) : inutile de
    # tenter un merge si l'écriture du résultat va de toute façon être refusée.
    decision, dec_err = _load_json(merge_review_dir / "decision.json")
    if dec_err or not isinstance(decision, dict):
        raise MergeExecutionError(f"decision.json (Phase 16) {dec_err or 'ne contient pas un objet JSON'}")
    case_id = str(decision.get("case_id") or "")
    if not case_id:
        raise MergeExecutionError(f"decision.json (Phase 16) sans case_id exploitable : {merge_review_dir}")

    out_dir, out_file = _output_paths(out_root, case_id)
    if out_dir.exists():
        if not force:
            raise MergeExecutionExistsError(
                f"résultat de merge déjà existant : {out_dir} (utiliser --force pour régénérer)"
            )
        if not out_file.is_file():
            raise MergeExecutionError(
                f"{out_dir} existe mais ne ressemble pas à une sortie générée par cet outil "
                "(pas de merge_result.json) — suppression refusée, vérifier manuellement"
            )
        import shutil
        log_debug(_TAG, f"régénération forcée : suppression de {out_dir}")
        shutil.rmtree(out_dir)

    result = execute_confirmed_merge(
        merge_review_dir=merge_review_dir,
        worktree_dir=worktree_dir,
        commit_result_dir=commit_result_dir,
        git_timeout_s=git_timeout_s,
    )

    out_dir.mkdir(parents=True, exist_ok=False)
    out_file.write_text(json.dumps(result.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return out_file
