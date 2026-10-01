from __future__ import annotations

"""Phase 15 — commit automatique d'un patch autofix déjà validé et approuvé,
sur sa branche isolée existante (Phase 7). Nouveau module, en LECTURE SEULE
sur les artefacts déjà produits par les phases précédentes (confidence_score.json
Phase 12, decision.json Phase 13, worktree.json Phase 7, bem_proposal.json
Phase 14 optionnel) — ne recalcule, ne relance et ne réexécute jamais rien de
ces phases. Ne touche à aucun extracteur, aucune stratégie de dispatch, ni à
aucun autre module existant du pipeline. Ne pousse jamais rien (aucun push,
aucun merge) : le commit reste local à la branche autofix isolée.

── Précondition : toutes les conditions ensemble, jamais la première seule ───
confidence_score.json (Phase 12) avec confidence="HIGH" exactement ; decision.json
(Phase 13, human_reviews/<case_id>/) avec decision="APPROVED" exactement ;
worktree.json (Phase 7) désignant un worktree Git réel (vérifié via
`git rev-parse --is-inside-work-tree`, jamais une simple présence de dossier) ;
case_id cohérent entre les trois artefacts et les trois dossiers fournis ;
branch (worktree.json) hors de PROTECTED_BRANCHES (Survey/autofix/autofix_worktree.py,
réutilisée telle quelle, jamais redéfinie) et effectivement la branche
checked-out du worktree (garde défensive contre un worktree manipulé
manuellement entre la Phase 7 et cette phase).

En plus, "BEM mise à jour" est vérifiée par un FAIT observable, jamais une
déclaration : Survey/BOT_EVOLUTION_MEMORY.md doit figurer parmi les fichiers
réellement modifiés du worktree depuis base_sha — réutilise Survey/autofix/
static_validator.py::_git_changed_paths / _resolve_package_root (Phase 8,
non modifiées), jamais une redétection indépendante. Un flag dédié
(skip_bem_check_reason) exige une raison explicite non vide pour contourner
ce contrôle précis, jamais un simple booléen silencieux — certains patches
légitimes (ex. correctif d'infrastructure de test d'autofix) n'ont pas
vocation à générer d'entrée BEM.

── Filet de sécurité automatique (additif, avant de bloquer) ─────────────────
Le gabarit Phase 6 (Survey/autofix/prompt_generator.py) demande désormais à Codex
d'écrire lui-même l'entrée BEM en fin de patch. Si malgré cela le fait Git
ci-dessus reste négatif (BEM non modifié) ET que diagnosis_dir/
context_selection_dir sont fournis à cet appel (nouveaux paramètres optionnels
— absents, comportement strictement inchangé, cf. non-régression) :
Survey/autofix/bem_proposal.py::write_bem_proposal (Phase 14, non modifiée, ni
importée ni appelée autrement qu'elle ne l'est déjà par sa propre façade) est
tentée pour ce case. Si elle produit un brouillon, celui-ci est ajouté tel
quel en fin de Survey/BOT_EVOLUTION_MEMORY.md DANS LE WORKTREE, AVANT le
commit — précédé d'un marqueur explicite non ambigu
(AUTO_GENERATED_BEM_MARKER) inséré comme première ligne du CORPS de l'entrée
(juste après sa ligne d'en-tête "### ...", jamais avant : Survey/autofix/
failure_diagnosis.py découpe ce fichier sur les lignes "### ..." pour sa
recherche de signaux, Phase 4 — un marqueur placé avant la ligne d'en-tête
serait rattaché par erreur à l'entrée précédente). Le fait Git est alors
revérifié (le fichier vient d'être physiquement modifié) avant de retenter le
blocage. Si write_bem_proposal échoue (case non éligible à la Phase 14,
artefact manquant, etc.) ou si diagnosis_dir/context_selection_dir ne sont pas
fournis, le comportement est inchangé : blocage explicite, avec le détail de
l'échec du filet de sécurité ajouté au message d'erreur. --skip-bem-check-reason
reste le seul contournement pour un patch qui n'a légitimement pas vocation à
une entrée BEM.

Refus explicite si un commit portant déjà la référence de ce case_id
("case_id=<...>" en ligne exacte du corps du message) existe dans le log Git
de la branche autofix — recherche dans l'historique réel, jamais seulement
dans un artefact JSON qui pourrait avoir été supprimé.

── Comportement ──────────────────────────────────────────────────────────────
1. Sujet de commit mécanique par défaut (conventional commit), composé à
   partir des noms de fonctions réellement ajoutées/modifiées déjà détectées
   par la Phase 14 (bem_proposal.json, si fourni et cohérent en case_id) ;
   à défaut, à partir de la liste réelle des fichiers mis en index pour ce
   commit (jamais une formulation inventée). Un --message (`message`)
   remplace ce sujet par la formulation de l'opérateur — le corps du commit
   (case_id, résumé) reste dans tous les cas généré ici, jamais omis.
2. git add -A puis git commit sur la branche autofix existante du worktree
   (jamais un nouveau checkout, jamais un déplacement de HEAD ailleurs).
   Worktree déjà entièrement propre (rien en attente, tracked ou untracked) :
   traité comme un état déjà committé, jamais une erreur, jamais un commit
   vide artificiel — le sha courant de la branche est rapporté tel quel.
3. Ne touche jamais à main/prod/playwright-migration ni à aucune branche
   protégée (PROTECTED_BRANCHES) ; jamais de push, jamais de merge.

── Sortie ──────────────────────────────────────────────────────────────────
commit_results/<case_id>/commit_result.json — même convention JSON que les
phases précédentes (schema_version/case_id/created_at). Refus explicite
(jamais un écrasement silencieux) si une sortie existe déjà sans force=True.
"""

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from Survey.autofix.autofix_worktree import PROTECTED_BRANCHES
from Survey.autofix.bem_proposal import BemProposalError, write_bem_proposal
from Survey.log_utils import log_debug, log_info
from Survey.autofix.static_validator import (
    StaticValidationError,
    _git_changed_paths,
    _resolve_package_root,
)

_TAG = "[PATCH_COMMIT]"
SCHEMA_VERSION = "1.0"

DEFAULT_GIT_TIMEOUT_S = 15.0

BEM_PACKAGE_RELATIVE_PATH = "Survey/BOT_EVOLUTION_MEMORY.md"

_MAX_SUBJECT_NAMES = 6

# Marqueur explicite, jamais ambigu : distingue une entrée écrite par Codex
# (Phase 6, gabarit) d'une entrée de secours produite mécaniquement par le
# filet de sécurité ci-dessous (patterns couverts/exclus jamais validés par
# un jugement humain ou par Codex lui-même dans ce cas). Inséré comme première
# ligne du CORPS de l'entrée (cf. docstring du module) pour rester associé à
# la bonne entrée lors du découpage par Survey/autofix/failure_diagnosis.py.
AUTO_GENERATED_BEM_MARKER = (
    "[Entrée auto-générée — Codex n'a pas rédigé cette entrée, patterns couverts/exclus non validés]"
)

# Composant de chemin unique, allowlist conservatrice — même garde-fou dupliqué
# volontairement dans plusieurs modules indépendants de ce pipeline (cf.
# Survey/autofix/autofix_worktree.py, Survey/autofix/human_review.py, Survey/autofix/bem_proposal.py).
_CASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
_WINDOWS_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


class PatchCommitError(Exception):
    """Refus contrôlé — inéligibilité, échec Git, ou usage incorrect."""


class PatchCommitExistsError(PatchCommitError):
    """commit_result.json existe déjà pour ce case (sans --force)."""


class PatchCommitDuplicateError(PatchCommitError):
    """Un commit portant déjà la référence de ce case_id existe sur la branche."""


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


def _run_git(
    args: "list[str]", *, cwd: Path, timeout: float, input_text: Optional[str] = None,
) -> "tuple[bool, str, str, bool]":
    """Une seule stratégie d'exécution de sous-processus, jamais de retry ; un
    budget de temps explicite pour chaque commande. `input_text`, quand fourni,
    est transmis sur l'entrée standard (seul usage : `git commit -F -`, pour
    ne jamais dépendre de l'échappement d'un message multi-lignes dans les
    arguments de la commande). Retourne (ok, stdout, stderr, timed_out)."""
    log_debug(_TAG, f"git {' '.join(args)} (cwd={cwd}, timeout={timeout}s)")
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(cwd), input=input_text, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout, check=False,
        )
        out, err = proc.stdout or "", proc.stderr or ""
        if "\ufffd" in out or "\ufffd" in err:
            return False, out, f"{err}\nsortie UTF-8 invalide".strip(), False
        return proc.returncode == 0, out, err, False
    except subprocess.TimeoutExpired:
        return False, "", f"dépassement du budget ({timeout}s)", True
    except OSError as exc:
        return False, "", f"exécution impossible : {exc}", False


# ─────────────────────────────── Éligibilité ─────────────────────────────────

@dataclass
class CommitEligibilityResult:
    eligible: bool
    case_id: Optional[str]
    reasons: "list[str]" = field(default_factory=list)


def check_commit_eligibility(
    *,
    confidence_score_dir: "str | Path",
    human_review_dir: "str | Path",
    worktree_dir: "str | Path",
    git_timeout_s: float = DEFAULT_GIT_TIMEOUT_S,
) -> CommitEligibilityResult:
    """Vérifie toutes les conditions ensemble ; ne s'arrête jamais à la
    première raison de refus rencontrée."""
    confidence_score_dir = Path(confidence_score_dir)
    human_review_dir = Path(human_review_dir)
    worktree_dir = Path(worktree_dir)
    reasons: "list[str]" = []

    confidence_score, cf_err = _load_json(confidence_score_dir / "confidence_score.json")
    if cf_err or not isinstance(confidence_score, dict):
        reasons.append(f"confidence_score.json {cf_err or 'ne contient pas un objet JSON'}")

    decision, dec_err = _load_json(human_review_dir / "decision.json")
    if dec_err or not isinstance(decision, dict):
        reasons.append(f"decision.json {dec_err or 'ne contient pas un objet JSON'}")

    worktree, wt_err = _load_json(worktree_dir / "worktree.json")
    if wt_err or not isinstance(worktree, dict):
        reasons.append(f"worktree.json {wt_err or 'ne contient pas un objet JSON'}")

    if cf_err or dec_err or wt_err or not all(
        isinstance(x, dict) for x in (confidence_score, decision, worktree)
    ):
        return CommitEligibilityResult(eligible=False, case_id=None, reasons=reasons)

    case_ids = {
        "confidence_score.json": str(confidence_score.get("case_id") or ""),
        "decision.json": str(decision.get("case_id") or ""),
        "worktree.json": str(worktree.get("case_id") or ""),
        "confidence_score_dir": confidence_score_dir.name,
        "human_review_dir": human_review_dir.name,
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

    confidence = str(confidence_score.get("confidence") or "")
    if confidence != "HIGH":
        reasons.append(f"confidence_score.json : confidence={confidence!r} — \"HIGH\" requis")

    decision_value = str(decision.get("decision") or "")
    if decision_value != "APPROVED":
        reasons.append(f"decision.json : decision={decision_value!r} — \"APPROVED\" requis")

    branch = str(worktree.get("branch") or "")
    worktree_path = Path(str(worktree.get("worktree_path") or ""))
    if branch in PROTECTED_BRANCHES:
        reasons.append(f"worktree.json : branch={branch!r} est une branche protégée (PROTECTED_BRANCHES)")

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

    return CommitEligibilityResult(eligible=not reasons, case_id=resolved_case_id, reasons=reasons)


# ─────────────────────────── BEM / duplicate / sujet ─────────────────────────

def _check_bem_updated(worktree_path: Path, base_sha: str, *, timeout: float) -> "tuple[bool, list[str]]":
    """FAIT observable, jamais une déclaration : réutilise Survey/autofix/
    static_validator.py::_git_changed_paths / _resolve_package_root (Phase 8,
    non modifiées) pour vérifier si Survey/BOT_EVOLUTION_MEMORY.md figure
    parmi les fichiers réellement modifiés depuis base_sha."""
    try:
        # Le validateur renvoie (modifiés, ajoutés) : conserver la première
        # liste évite qu'un tuple de listes fasse planter le commit approuvé.
        changed_rel, _added_rel = _git_changed_paths(worktree_path, base_sha, timeout=timeout)
    except StaticValidationError as exc:
        raise PatchCommitError(
            "détection des fichiers modifiés (Survey/autofix/static_validator.py::_git_changed_paths, "
            f"réutilisée telle quelle) échouée : {exc}"
        ) from exc

    try:
        package_root = _resolve_package_root(worktree_path)
    except StaticValidationError as exc:
        raise PatchCommitError(
            f"résolution de la racine de paquet (Survey/autofix/static_validator.py::_resolve_package_root, "
            f"réutilisée telle quelle) échouée : {exc}"
        ) from exc

    package_root_rel = (
        package_root.relative_to(worktree_path).as_posix() if package_root != worktree_path else ""
    )
    prefix = f"{package_root_rel}/" if package_root_rel else ""
    bem_full_rel = f"{prefix}{BEM_PACKAGE_RELATIVE_PATH}"
    return (bem_full_rel in set(changed_rel)), changed_rel


def _append_auto_generated_bem_entry(worktree_path: Path, draft_markdown: str) -> Path:
    """Ajoute draft_markdown (brouillon Phase 14) en fin de
    Survey/BOT_EVOLUTION_MEMORY.md DU WORKTREE, précédé du même séparateur
    '---' déjà utilisé entre les entrées existantes de ce fichier, avec
    AUTO_GENERATED_BEM_MARKER inséré comme première ligne du corps (juste
    après la ligne d'en-tête '### ...' du brouillon — jamais avant, cf.
    docstring du module). Réutilise Survey/autofix/static_validator.py::
    _resolve_package_root (Phase 8, non modifiée) pour localiser le fichier
    réel dans CE worktree, jamais dans le dépôt principal."""
    try:
        package_root = _resolve_package_root(worktree_path)
    except StaticValidationError as exc:
        raise PatchCommitError(
            "résolution de la racine de paquet (Survey/autofix/static_validator.py::_resolve_package_root, "
            f"réutilisée telle quelle) échouée : {exc}"
        ) from exc

    bem_file = package_root / BEM_PACKAGE_RELATIVE_PATH
    if not bem_file.is_file():
        raise PatchCommitError(
            f"{BEM_PACKAGE_RELATIVE_PATH} introuvable dans le worktree ({bem_file}) — insertion du "
            "filet de sécurité impossible"
        )

    lines = draft_markdown.splitlines()
    if not lines or not lines[0].startswith("### "):
        raise PatchCommitError(
            "brouillon BEM (Survey/autofix/bem_proposal.py, Phase 14) inattendu : ne commence pas par une "
            "ligne d'en-tête '### ...' — insertion refusée plutôt que devinée"
        )
    spliced = "\n".join([lines[0], AUTO_GENERATED_BEM_MARKER, *lines[1:]])

    existing = bem_file.read_text(encoding="utf-8")
    separator = "" if existing.endswith("\n") else "\n"
    bem_file.write_text(f"{existing}{separator}\n---\n{spliced}\n", encoding="utf-8")
    return bem_file


def _find_existing_case_commit(worktree_path: Path, branch: str, case_id: str, *, timeout: float) -> Optional[str]:
    """Recherche une correspondance EXACTE de la ligne "case_id=<case_id>" dans
    le corps d'un commit du log Git de la branche — jamais une simple
    sous-chaîne (deux case_id partageant un préfixe ne doivent jamais se
    confondre)."""
    marker = f"case_id={case_id}"
    ok, out, err, timed_out = _run_git(
        ["log", branch, "--pretty=format:%H%x1f%B%x1e"], cwd=worktree_path, timeout=timeout,
    )
    if timed_out:
        raise PatchCommitError(f"git log {branch} a dépassé son budget ({timeout}s)")
    if not ok:
        raise PatchCommitError(f"git log {branch} a échoué : {err.strip()}")

    for record in out.split("\x1e"):
        if "\x1f" not in record:
            continue
        sha, body = record.split("\x1f", 1)
        sha = sha.strip()
        if not sha:
            continue
        for line in body.splitlines():
            if line.strip() == marker:
                return sha
    return None


def _function_names_from_bem_proposal(bem_proposal: dict) -> "list[str]":
    names: "list[str]" = []
    for entry in (bem_proposal.get("changed_files") or []):
        if not isinstance(entry, dict):
            continue
        functions = entry.get("functions") if isinstance(entry.get("functions"), dict) else {}
        names.extend(str(n) for n in (functions.get("added") or []))
        names.extend(str(n) for n in (functions.get("modified") or []))
    return names


def _mechanical_subject(names: "list[str]") -> str:
    ordered = sorted({n for n in names if n})
    if not ordered:
        return "fix(survey): update case"
    shown = ordered[:_MAX_SUBJECT_NAMES]
    joined = ", ".join(shown)
    if len(ordered) > _MAX_SUBJECT_NAMES:
        joined += f" (+{len(ordered) - _MAX_SUBJECT_NAMES} autres)"
    return f"fix(survey): update {joined}"


def _compose_commit_body(*, case_id: str, confidence: str, files: "list[str]") -> str:
    lines = [f"case_id={case_id}", "", f"confidence={confidence}", "fichiers modifiés :"]
    lines.extend(f"- {f}" for f in files)
    return "\n".join(lines)


# ────────────────────────────────── Orchestration ────────────────────────────

@dataclass
class CommitResult:
    case_id: str
    branch: str
    already_committed: bool
    commit_sha: str
    commit_subject: str
    commit_message: Optional[str]
    files_included: "list[str]"
    confidence: str
    bem_check_skipped: bool
    bem_check_skip_reason: Optional[str]
    bem_auto_generated: bool = False
    bem_auto_generation_error: Optional[str] = None
    warnings: "list[str]" = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "case_id": self.case_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "branch": self.branch,
            "already_committed": self.already_committed,
            "commit_sha": self.commit_sha,
            "commit_subject": self.commit_subject,
            "commit_message": self.commit_message,
            "files_included": self.files_included,
            "confidence": self.confidence,
            "bem_check_skipped": self.bem_check_skipped,
            "bem_check_skip_reason": self.bem_check_skip_reason,
            "bem_auto_generated": self.bem_auto_generated,
            "bem_auto_generation_error": self.bem_auto_generation_error,
            "warnings": self.warnings,
        }


def _output_paths(out_root: Path, case_id: str) -> "tuple[Path, Path]":
    out_dir = out_root / case_id
    return out_dir, out_dir / "commit_result.json"


def commit_patch(
    *,
    confidence_score_dir: "str | Path",
    human_review_dir: "str | Path",
    worktree_dir: "str | Path",
    bem_proposal_dir: "Optional[str | Path]" = None,
    diagnosis_dir: "Optional[str | Path]" = None,
    context_selection_dir: "Optional[str | Path]" = None,
    bem_proposals_root: "str | Path" = "bem_proposals",
    message: Optional[str] = None,
    skip_bem_check_reason: Optional[str] = None,
    git_timeout_s: float = DEFAULT_GIT_TIMEOUT_S,
    out_root: "str | Path" = "commit_results",
    force: bool = False,
) -> CommitResult:
    """diagnosis_dir/context_selection_dir sont NOUVEAUX, optionnels : absents
    (défaut), le comportement du contrôle BEM est strictement inchangé (refus
    immédiat si BOT_EVOLUTION_MEMORY.md n'est pas parmi les fichiers modifiés).
    Fournis tous les deux, ils activent le filet de sécurité automatique
    (Survey/autofix/bem_proposal.py::write_bem_proposal, Phase 14, non modifiée) —
    cf. docstring du module."""
    confidence_score_dir = Path(confidence_score_dir)
    human_review_dir = Path(human_review_dir)
    worktree_dir = Path(worktree_dir)
    out_root = Path(out_root)

    if skip_bem_check_reason is not None and not skip_bem_check_reason.strip():
        raise PatchCommitError(
            "--skip-bem-check-reason exige une raison explicite non vide, jamais un simple booléen "
            "silencieux"
        )

    eligibility = check_commit_eligibility(
        confidence_score_dir=confidence_score_dir,
        human_review_dir=human_review_dir,
        worktree_dir=worktree_dir,
        git_timeout_s=git_timeout_s,
    )
    if not eligibility.eligible:
        raise PatchCommitError(
            "case non éligible pour la Phase 15 : " + " ; ".join(eligibility.reasons)
        )
    case_id = eligibility.case_id
    assert case_id is not None  # garanti par eligible=True

    out_dir, out_file = _output_paths(out_root, case_id)
    if out_dir.exists() and not force:
        raise PatchCommitExistsError(
            f"commit_result.json déjà présent dans {out_dir} (utiliser --force pour régénérer)"
        )

    confidence_score, _ = _load_json(confidence_score_dir / "confidence_score.json")
    worktree, _ = _load_json(worktree_dir / "worktree.json")
    confidence = str(confidence_score.get("confidence") or "")
    branch = str(worktree.get("branch") or "")
    worktree_path = Path(str(worktree.get("worktree_path") or ""))
    base_sha = str(worktree.get("base_sha") or "")
    if not base_sha:
        raise PatchCommitError("worktree.json (Phase 7) sans base_sha exploitable")

    warnings: "list[str]" = []

    bem_skipped = skip_bem_check_reason is not None
    bem_auto_generated = False
    bem_auto_generation_error: Optional[str] = None
    if bem_skipped:
        log_info(
            _TAG,
            f"case={case_id} : contrôle BEM contourné explicitement (raison : {skip_bem_check_reason})",
        )
    else:
        bem_updated, _changed = _check_bem_updated(worktree_path, base_sha, timeout=git_timeout_s)

        if not bem_updated and diagnosis_dir is not None and context_selection_dir is not None:
            # Filet de sécurité automatique (additif) : Codex n'a pas rédigé l'entrée
            # lui-même (Partie A du gabarit, Phase 6) — tenter un brouillon mécanique
            # (Phase 14, non modifiée) avant de bloquer. cf. docstring du module.
            try:
                draft_path = write_bem_proposal(
                    human_review_dir=human_review_dir,
                    worktree_dir=worktree_dir,
                    diagnosis_dir=diagnosis_dir,
                    context_selection_dir=context_selection_dir,
                    confidence_score_dir=confidence_score_dir,
                    out_root=bem_proposals_root,
                    git_timeout_s=git_timeout_s,
                )
            except BemProposalError as exc:
                bem_auto_generation_error = str(exc)
                log_debug(_TAG, f"case={case_id} : filet de sécurité BEM (Phase 14) indisponible : {exc}")
            else:
                draft_markdown = draft_path.read_text(encoding="utf-8")
                _append_auto_generated_bem_entry(worktree_path, draft_markdown)
                bem_auto_generated = True
                bem_updated, _changed = _check_bem_updated(worktree_path, base_sha, timeout=git_timeout_s)
                log_info(
                    _TAG,
                    f"case={case_id} : entrée BEM auto-générée et ajoutée (filet de sécurité, Phase 14)",
                )

        if not bem_updated:
            auto_detail = (
                f" ; filet de sécurité automatique (Phase 14) tenté mais échoué : {bem_auto_generation_error}"
                if bem_auto_generation_error else ""
            )
            raise PatchCommitError(
                f"{BEM_PACKAGE_RELATIVE_PATH} ne figure pas parmi les fichiers modifiés du worktree "
                "depuis base_sha — ce case ne semble pas avoir mis à jour BOT_EVOLUTION_MEMORY.md"
                f"{auto_detail} ; utiliser --skip-bem-check-reason \"<raison>\" si ce patch n'a "
                "légitimement pas vocation à générer d'entrée BEM (ex. correctif d'infrastructure de "
                "test d'autofix)"
            )

    ok_status, status_out, status_err, timed_out_status = _run_git(
        ["status", "--porcelain", "--untracked-files=all"], cwd=worktree_path, timeout=git_timeout_s,
    )
    if timed_out_status:
        raise PatchCommitError(f"git status a dépassé son budget ({git_timeout_s}s)")
    if not ok_status:
        raise PatchCommitError(f"git status a échoué : {status_err.strip()}")

    if not status_out.strip():
        # Rien en attente : traité comme un état déjà committé, jamais une erreur,
        # jamais un commit vide artificiel — le duplicate-check ci-dessous ne
        # s'applique qu'à une TENTATIVE de nouveau commit (branche dirty), pas à
        # ce cas de relance idempotente (ex. commit_result.json supprimé puis
        # régénéré sans aucun changement supplémentaire depuis).
        already_committed = True
        commit_message: Optional[str] = None
        files_included: "list[str]" = []
        ok_head, head_out, head_err, timed_out_head = _run_git(
            ["rev-parse", "HEAD"], cwd=worktree_path, timeout=git_timeout_s,
        )
        if timed_out_head or not ok_head:
            raise PatchCommitError(f"impossible de résoudre HEAD : {head_err.strip()}")
        commit_sha = head_out.strip()
        log_info(_TAG, f"case={case_id} : worktree déjà propre — traité comme déjà committé (sha={commit_sha[:12]})")
    else:
        already_committed = False

        existing_sha = _find_existing_case_commit(worktree_path, branch, case_id, timeout=git_timeout_s)
        if existing_sha:
            raise PatchCommitDuplicateError(
                f"un commit portant déjà la référence case_id={case_id!r} existe sur {branch!r} "
                f"({existing_sha[:12]}) — jamais un commit en double, alors que le worktree porte encore "
                "des changements non committés"
            )

        ok_add, _out_add, err_add, timed_out_add = _run_git(
            ["add", "-A"], cwd=worktree_path, timeout=git_timeout_s,
        )
        if timed_out_add:
            raise PatchCommitError(f"git add -A a dépassé son budget ({git_timeout_s}s)")
        if not ok_add:
            raise PatchCommitError(f"git add -A a échoué : {err_add.strip()}")

        ok_diff, diff_out, diff_err, timed_out_diff = _run_git(
            ["diff", "--cached", "--name-only"], cwd=worktree_path, timeout=git_timeout_s,
        )
        if timed_out_diff:
            raise PatchCommitError(f"git diff --cached a dépassé son budget ({git_timeout_s}s)")
        if not ok_diff:
            raise PatchCommitError(f"git diff --cached a échoué : {diff_err.strip()}")
        files_included = sorted(line.strip() for line in diff_out.splitlines() if line.strip())

        if not files_included:
            raise PatchCommitError(
                "git status signalait des changements mais git diff --cached n'en rapporte aucun après "
                "git add -A — état incohérent, refus plutôt qu'un commit vide"
            )

        if message is not None:
            subject = message
        else:
            bem_proposal: Optional[dict] = None
            if bem_proposal_dir is not None:
                bem_proposal_dir_path = Path(bem_proposal_dir)
                loaded, load_err = _load_json(bem_proposal_dir_path / "bem_proposal.json")
                if load_err or not isinstance(loaded, dict):
                    warnings.append(
                        f"bem_proposal.json ({bem_proposal_dir_path}) {load_err or 'ne contient pas un objet JSON'} "
                        "— sujet de commit dérivé des fichiers modifiés à la place des fonctions"
                    )
                elif str(loaded.get("case_id") or "") != case_id:
                    warnings.append(
                        f"bem_proposal.json ({bem_proposal_dir_path}) porte case_id="
                        f"{loaded.get('case_id')!r}, attendu {case_id!r} — ignoré pour le sujet de commit"
                    )
                else:
                    bem_proposal = loaded

            names = _function_names_from_bem_proposal(bem_proposal) if bem_proposal else []
            subject = _mechanical_subject(names if names else files_included)

        body = _compose_commit_body(case_id=case_id, confidence=confidence, files=files_included)
        commit_message = f"{subject}\n\n{body}"

        ok_commit, _out_commit, err_commit, timed_out_commit = _run_git(
            ["commit", "-F", "-"], cwd=worktree_path, timeout=git_timeout_s, input_text=commit_message,
        )
        if timed_out_commit:
            raise PatchCommitError(f"git commit a dépassé son budget ({git_timeout_s}s)")
        if not ok_commit:
            raise PatchCommitError(f"git commit a échoué : {err_commit.strip()}")

        ok_head2, head_out2, head_err2, timed_out_head2 = _run_git(
            ["rev-parse", "HEAD"], cwd=worktree_path, timeout=git_timeout_s,
        )
        if timed_out_head2 or not ok_head2:
            raise PatchCommitError(f"impossible de résoudre HEAD après commit : {head_err2.strip()}")
        commit_sha = head_out2.strip()

    commit_subject = ""
    ok_subj, subj_out, subj_err, timed_out_subj = _run_git(
        ["log", "-1", "--format=%s", commit_sha], cwd=worktree_path, timeout=git_timeout_s,
    )
    if timed_out_subj:
        raise PatchCommitError(f"git log -1 --format=%s a dépassé son budget ({git_timeout_s}s)")
    if not ok_subj:
        raise PatchCommitError(f"git log -1 --format=%s a échoué : {subj_err.strip()}")
    commit_subject = subj_out.strip()

    result = CommitResult(
        case_id=case_id,
        branch=branch,
        already_committed=already_committed,
        commit_sha=commit_sha,
        commit_subject=commit_subject,
        commit_message=commit_message,
        files_included=files_included,
        confidence=confidence,
        bem_check_skipped=bem_skipped,
        bem_check_skip_reason=skip_bem_check_reason,
        bem_auto_generated=bem_auto_generated,
        bem_auto_generation_error=bem_auto_generation_error,
        warnings=warnings,
    )
    write_commit_result(result, out_root=out_root, force=force)

    log_info(
        _TAG,
        f"case={case_id} : commit_sha={commit_sha[:12]} already_committed={already_committed} "
        f"fichiers={len(files_included)}",
    )
    return result


def write_commit_result(
    result: CommitResult,
    *,
    out_root: "str | Path" = "commit_results",
    force: bool = False,
) -> Path:
    out_root = Path(out_root)
    out_dir, out_file = _output_paths(out_root, result.case_id)

    if out_dir.exists():
        if not force:
            raise PatchCommitExistsError(
                f"commit_result.json déjà présent dans {out_dir} (utiliser --force pour régénérer)"
            )
        if not out_file.is_file():
            raise PatchCommitError(
                f"{out_dir} existe mais ne ressemble pas à une sortie générée par cet outil "
                "(pas de commit_result.json) — suppression refusée, vérifier manuellement"
            )
        import shutil
        log_debug(_TAG, f"régénération forcée de l'artefact Phase 15 : suppression de {out_dir}")
        shutil.rmtree(out_dir)

    out_dir.mkdir(parents=True, exist_ok=False)
    out_file.write_text(
        json.dumps(result.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out_file
