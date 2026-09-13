from __future__ import annotations

"""Phase 7 — préparation d'un espace Git isolé (branche + worktree dédiés) pour
un case déjà diagnostiqué, prompté et jugé éligible — en amont d'un lancement
manuel de Codex sur ce prompt. Ne lance aucun agent de coding, n'applique aucun
patch, ne commit rien, ne push rien, ne merge rien.

Lecture seule sur les Phases 2/4/6 : ne recalcule aucun failure case, replay,
diagnostic ou prompt — lit uniquement manifest.json (Phase 2), diagnosis.json
(Phase 4) et prompt.txt (Phase 6), déjà produits par ces outils. N'écrit jamais
dans failure_cases/, diagnoses/, context_selections/, ni prompts/.

── Portée : stage="extraction" uniquement ─────────────────────────────────────
Les cases stage="action" restent hors périmètre : leur replay (Phase 3A/3B)
vérifie l'état DOM après action, mais ne réexécute jamais réellement le
dispatcher (dispatcher_success d'origine réutilisé tel quel, cf.
Survey/failure_replay.py) — un verdict REPRODUIT sur un case action ne prouve
donc rien sur le dispatcher lui-même. Un case extraction REPRODUIT, en
revanche, revérifie réellement dom_analyzer.analyze_dom() + le validator
d'extraction sur le DOM figé — c'est une preuve plus forte, seule retenue ici.

── Éligibilité : toutes les conditions ensemble, avant tout effet de bord ─────
manifest.json et diagnosis.json existent et sont valides ; prompt.txt existe
réellement et MANUAL_REVIEW_REQUIRED.txt n'est pas présent à sa place ; les
case_id du manifeste, du diagnostic et des trois dossiers fournis correspondent
tous exactement ; stage="extraction" ; replay.verdict="REPRODUIT" ;
confidence_global="certain" ; case_incomplete=false ; case_id est utilisable
sans transformation comme composant de chemin ET comme référence Git valide
(validé via une regex conservatrice, puis via `git check-ref-format`, qui fait
autorité sur la syntaxe réelle des refs Git plutôt qu'une réimplémentation
partielle de ses règles). "certain" n'est PAS revérifié indépendamment de
REPRODUIT ici — Phase 4 ne produit "certain" que lorsque REPRODUIT est déjà
vrai (cf. Survey/failure_diagnosis.py::_cause_level_from_replay) : c'est un
garde-fou de cohérence avec un diagnostic déjà calculé, pas une seconde preuve.

── Stratégie Git unique, sans fallback ────────────────────────────────────────
1. git rev-parse --show-toplevel (dépôt réel, jamais un chemin codé en dur)
2. git rev-parse HEAD                    -> base_sha, résolu une seule fois
3. git symbolic-ref -q --short HEAD      -> source_branch, purement informatif
   (jamais utilisé pour une décision : si le dépôt est resté sur une vieille
   branche de feature ou en detached HEAD, ce n'est pas cet outil qui bloque —
   c'est affiché pour qu'un humain le remarque avant de perdre du temps dans
   le worktree créé)
4. git show-ref --verify (branche)       -> refuse si autofix/<case_id> existe
5. vérifie que le chemin cible du worktree n'existe pas déjà
6. git branch autofix/<case_id> <base_sha>   (ne touche jamais HEAD/le checkout)
7. git worktree add <chemin> autofix/<case_id>
Si l'étape 7 échoue après que l'étape 6 a réussi : rollback immédiat de la
branche créée par CETTE invocation (jamais une branche préexistante) avant de
relancer l'erreur. Aucune tentative alternative, aucun --force.

Emplacement du worktree, déterministe par défaut : <parent du dépôt>/
<nom du dépôt>-worktrees/<case_id> — un vrai frère du checkout principal,
jamais imbriqué dans son arbre suivi par Git (donc aucune entrée .gitignore à
ajouter). Peut être fourni explicitement (worktrees_root).
"""

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from Survey.log_utils import log_debug, log_info

_TAG = "[AUTOFIX_WORKTREE]"

PROTECTED_BRANCHES = {"playwright-migration", "main", "prod"}

# Composant de chemin unique (jamais de séparateur), allowlist conservatrice :
# alphanumérique + . _ - uniquement, débute par alphanumérique. Exclut donc déjà
# tout caractère invalide pour une ref Git (espace, ~^:?*[\, etc.) et toute
# tentative de traversée de chemin (.., /, \). Vérifié en plus par
# `git check-ref-format`, qui fait autorité sur la syntaxe réelle des refs.
_CASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")

# Noms de périphérique réservés Windows — case_id ne doit jamais matcher un de
# ces noms (insensible à la casse), qui casserait la création du dossier
# worktree correspondant sur ce système. Défensif : les case_id réels de ce
# pipeline sont des horodatages/slugs, jamais ces noms, mais "pas d'hypothèse
# fragile" est une règle explicite du chantier.
_WINDOWS_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


class AutofixWorktreeError(Exception):
    """Refus contrôlé — inéligibilité ou échec Git. Jamais un effet de bord partiel."""


class AutofixWorktreeExistsError(AutofixWorktreeError):
    """La branche autofix/<case_id> ou le worktree cible existe déjà."""


def _load_json(path: Path) -> "tuple[Any, Optional[str]]":
    if not path.is_file():
        return None, f"{path} absent"
    try:
        import json
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


def _run_git(args: "list[str]", *, cwd: Path, timeout: float = 30.0) -> "subprocess.CompletedProcess[str]":
    """Une seule tentative, jamais de retry ni de stratégie alternative."""
    log_debug(_TAG, f"git {' '.join(args)} (cwd={cwd})")
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AutofixWorktreeError(f"commande git indisponible/expirée : git {' '.join(args)} ({exc})") from exc


@dataclass
class EligibilityResult:
    eligible: bool
    case_id: Optional[str]
    reasons: "list[str]" = field(default_factory=list)


def check_eligibility(
    *,
    failure_case_dir: "str | Path",
    diagnosis_dir: "str | Path",
    prompt_dir: "str | Path",
) -> EligibilityResult:
    """Vérifie toutes les conditions ensemble ; ne produit jamais un résultat
    partiel — soit toutes les conditions tiennent, soit la liste des raisons de
    refus est renvoyée complète (pas seulement la première rencontrée)."""
    failure_case_dir = Path(failure_case_dir)
    diagnosis_dir = Path(diagnosis_dir)
    prompt_dir = Path(prompt_dir)
    reasons: "list[str]" = []

    manifest, manifest_err = _load_json(failure_case_dir / "manifest.json")
    if manifest_err or not isinstance(manifest, dict):
        reasons.append(f"manifest.json {manifest_err or 'ne contient pas un objet JSON'}")

    diagnosis, diag_err = _load_json(diagnosis_dir / "diagnosis.json")
    if diag_err or not isinstance(diagnosis, dict):
        reasons.append(f"diagnosis.json {diag_err or 'ne contient pas un objet JSON'}")

    prompt_file = prompt_dir / "prompt.txt"
    manual_review_file = prompt_dir / "MANUAL_REVIEW_REQUIRED.txt"
    if not prompt_file.is_file():
        reasons.append(f"prompt.txt absent de {prompt_dir}")
    if manual_review_file.is_file():
        reasons.append(
            f"MANUAL_REVIEW_REQUIRED.txt présent dans {prompt_dir} — ce case a été jugé "
            "non éligible par la Phase 6, jamais transmissible à Codex"
        )

    # Les vérifications ci-dessous nécessitent manifest/diagnosis valides ; sans
    # eux, on s'arrête ici plutôt que de spéculer sur des données absentes.
    if manifest_err or diag_err or not isinstance(manifest, dict) or not isinstance(diagnosis, dict):
        return EligibilityResult(eligible=False, case_id=None, reasons=reasons)

    case_ids = {
        "manifest.json": str(manifest.get("case_id") or ""),
        "diagnosis.json": str(diagnosis.get("case_id") or ""),
        "failure_case_dir": failure_case_dir.name,
        "diagnosis_dir": diagnosis_dir.name,
        "prompt_dir": prompt_dir.name,
    }
    distinct = set(case_ids.values())
    if len(distinct) != 1 or "" in distinct:
        reasons.append(f"case_id incohérent entre les sources : {case_ids}")
        resolved_case_id = None
    else:
        resolved_case_id = next(iter(distinct))
        if not _is_safe_case_id(resolved_case_id):
            reasons.append(
                f"case_id {resolved_case_id!r} n'est pas utilisable sans transformation comme "
                "composant de chemin/référence Git"
            )

    stage = str(manifest.get("stage") or "")
    if stage != "extraction":
        reasons.append(f"stage={stage!r} — seul stage=\"extraction\" est dans le périmètre de cette phase")

    replay_verdict = str((diagnosis.get("replay") or {}).get("verdict") or "")
    if replay_verdict != "REPRODUIT":
        reasons.append(f"replay.verdict={replay_verdict!r} — \"REPRODUIT\" requis")

    confidence = str(diagnosis.get("confidence_global") or "")
    if confidence != "certain":
        reasons.append(f"confidence_global={confidence!r} — \"certain\" requis")

    if bool(diagnosis.get("case_incomplete")):
        reasons.append("case_incomplete=true")

    return EligibilityResult(eligible=not reasons, case_id=resolved_case_id, reasons=reasons)


@dataclass
class WorktreeResult:
    case_id: str
    branch: str
    worktree_path: Path
    base_sha: str
    source_branch: str
    prompt_path: Path


def _default_worktrees_root(repo_root: Path) -> Path:
    return repo_root.parent / f"{repo_root.name}-worktrees"


def _git_ref_exists(repo_root: Path, ref: str) -> bool:
    result = _run_git(["show-ref", "--verify", "--quiet", f"refs/heads/{ref}"], cwd=repo_root)
    return result.returncode == 0


def _current_branch_label(repo_root: Path) -> str:
    """Nom de la branche courante, pour affichage humain uniquement — jamais
    utilisé pour une décision programmatique, jamais un critère de refus. Un
    dépôt resté sur une vieille branche de feature ou en detached HEAD n'est
    pas bloqué par cet outil (hypothèse fragile sur le workflow git du
    développeur à éviter) ; l'information est simplement rapportée pour qu'un
    humain le remarque avant de perdre du temps dans le worktree créé.
    """
    result = _run_git(["symbolic-ref", "-q", "--short", "HEAD"], cwd=repo_root)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return "HEAD (detached)"


def prepare_autofix_worktree(
    *,
    failure_case_dir: "str | Path",
    diagnosis_dir: "str | Path",
    prompt_dir: "str | Path",
    worktrees_root: "Optional[str | Path]" = None,
) -> WorktreeResult:
    """Prépare atomiquement une branche autofix/<case_id> et son worktree dédié.

    Refuse avant tout effet de bord si une condition d'éligibilité manque, si la
    branche ou le chemin cible existe déjà, ou si case_id n'est pas sûr. En cas
    d'échec Git après création de la branche, la retire avant de relancer
    l'erreur — jamais de ressource partielle laissée derrière.
    """
    failure_case_dir = Path(failure_case_dir)
    diagnosis_dir = Path(diagnosis_dir)
    prompt_dir = Path(prompt_dir)

    eligibility = check_eligibility(
        failure_case_dir=failure_case_dir,
        diagnosis_dir=diagnosis_dir,
        prompt_dir=prompt_dir,
    )
    if not eligibility.eligible:
        raise AutofixWorktreeError(
            "case non éligible pour la Phase 7 : " + " ; ".join(eligibility.reasons)
        )

    case_id = eligibility.case_id
    branch = f"autofix/{case_id}"
    if branch in PROTECTED_BRANCHES:  # ne peut arriver qu'avec un case_id absurde
        raise AutofixWorktreeError(f"nom de branche calculé {branch!r} coïncide avec une branche protégée")

    # 1) Dépôt réel, résolu depuis l'environnement (répertoire courant du
    # process) — jamais un chemin codé en dur.
    toplevel = _run_git(["rev-parse", "--show-toplevel"], cwd=Path.cwd())
    if toplevel.returncode != 0:
        raise AutofixWorktreeError(f"dépôt Git introuvable depuis {Path.cwd()} : {toplevel.stderr.strip()}")
    repo_root = Path(toplevel.stdout.strip())

    # 2) Vérification syntaxique de la référence Git, sur l'autorité réelle de Git
    # plutôt qu'une réimplémentation partielle de ses règles.
    ref_check = _run_git(["check-ref-format", "--branch", branch], cwd=repo_root)
    if ref_check.returncode != 0:
        raise AutofixWorktreeError(f"{branch!r} n'est pas une référence Git valide : {ref_check.stderr.strip()}")

    # 3) HEAD résolu en SHA immuable, une seule fois, avant toute mutation.
    head = _run_git(["rev-parse", "HEAD"], cwd=repo_root)
    if head.returncode != 0:
        raise AutofixWorktreeError(f"impossible de résoudre HEAD : {head.stderr.strip()}")
    base_sha = head.stdout.strip()

    # 3bis) Branche source courante — purement informatif (cf. docstring de
    # _current_branch_label). Ne peut jamais faire échouer cette fonction.
    source_branch = _current_branch_label(repo_root)

    # 4) Aucun état préexistant ne doit être réutilisé/écrasé — refus explicite.
    if _git_ref_exists(repo_root, branch):
        raise AutofixWorktreeExistsError(f"la branche {branch!r} existe déjà")

    target = Path(worktrees_root).resolve() / case_id if worktrees_root else _default_worktrees_root(repo_root) / case_id
    if target.exists():
        raise AutofixWorktreeExistsError(f"le chemin cible du worktree existe déjà : {target}")

    # 5) Création de la branche depuis base_sha — ne touche jamais HEAD/le checkout.
    branch_create = _run_git(["branch", branch, base_sha], cwd=repo_root)
    if branch_create.returncode != 0:
        raise AutofixWorktreeError(f"création de branche {branch!r} échouée : {branch_create.stderr.strip()}")

    # 6) Association immédiate à un nouveau worktree.
    target.parent.mkdir(parents=True, exist_ok=True)
    worktree_add = _run_git(["worktree", "add", str(target), branch], cwd=repo_root)
    if worktree_add.returncode != 0:
        # Rollback : seule la branche créée par CETTE invocation est retirée —
        # jamais une branche préexistante (impossible ici, cf. étape 4).
        log_debug(_TAG, f"worktree add a échoué, rollback de la branche {branch!r}")
        _run_git(["branch", "-D", branch], cwd=repo_root)
        raise AutofixWorktreeError(f"création du worktree échouée : {worktree_add.stderr.strip()}")

    log_info(
        _TAG,
        f"worktree créé case={case_id} branch={branch} base_sha={base_sha[:12]} "
        f"source_branch={source_branch} -> {target}",
    )

    return WorktreeResult(
        case_id=case_id,
        branch=branch,
        worktree_path=target,
        base_sha=base_sha,
        source_branch=source_branch,
        prompt_path=prompt_dir / "prompt.txt",
    )