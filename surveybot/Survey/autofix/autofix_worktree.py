from __future__ import annotations

"""Phase 7 — préparation d'un espace Git isolé (branche + worktree dédiés) pour
un case déjà diagnostiqué, prompté et jugé éligible — en amont d'un lancement
manuel de Codex sur ce prompt. Ne lance aucun agent de coding, n'applique aucun
patch, ne commit rien, ne push rien, ne merge rien.

Lecture seule sur les Phases 2/4/6 : ne recalcule aucun failure case, replay,
diagnostic ou prompt — lit manifest.json (Phase 2), les preuves DOM déjà
nettoyées du case, diagnosis.json (Phase 4) et prompt.txt (Phase 6).
N'écrit jamais dans les dossiers d'artefacts d'origine.

── Portée : stage="extraction" ET stage="action" (preuve distincte par stage) ──
Un case extraction REPRODUIT revérifie réellement dom_analyzer.analyze_dom() +
le validator d'extraction sur le DOM figé — preuve forte, seule retenue pour ce
stage (inchangé).

Le replay passif d'un case action (Phase 3A/3B, Survey/autofix/failure_replay.py)
réutilise dispatcher_success du case d'origine tel quel et ne réexécute jamais
le dispatcher réel : un verdict REPRODUIT sur ce stage ne prouve donc toujours
rien sur le dispatcher lui-même. Phase 4 (Survey/autofix/failure_diagnosis.py) calcule
désormais, en plus, un signal réel indépendant pour ce stage quand le case
dispose de ce qu'exige le Chromium isolé (Phase 3C.4,
Survey/autofix/replay_browser.py::execute_case_action, non modifié) :
real_dispatch_replay.validation_comparison.outcome="BUG_PERSISTANT" confirme
ACTIVEMENT que le dispatcher réel échoue encore, de la même façon, sur le code
actuel non corrigé. C'est ce signal, et seulement lui, qui rend un case action
éligible ici — jamais le verdict REPRODUIT du replay passif seul, selon
exactement le même principe que pour l'extraction (une preuve réelle proche du
mécanisme concerné, jamais un critère plus permissif).

── Éligibilité : toutes les conditions ensemble, avant tout effet de bord ─────
manifest.json et diagnosis.json existent et sont valides ; prompt.txt existe
réellement et MANUAL_REVIEW_REQUIRED.txt n'est pas présent à sa place ; les
case_id du manifeste, du diagnostic et des trois dossiers fournis correspondent
tous exactement ; stage="extraction" ou stage="action" ; replay.verdict=
"REPRODUIT" ; pour stage="action" uniquement, EN PLUS :
real_dispatch_replay.validation_comparison.outcome="BUG_PERSISTANT" ;
confidence_global="certain" ; case_incomplete=false ; case_id est utilisable
sans transformation comme composant de chemin ET comme référence Git valide
(validé via une regex conservatrice, puis via `git check-ref-format`, qui fait
autorité sur la syntaxe réelle des refs Git plutôt qu'une réimplémentation
partielle de ses règles). "certain" n'est PAS revérifié indépendamment de
REPRODUIT pour stage="extraction" — Phase 4 ne produit "certain" que lorsque
REPRODUIT est déjà vrai pour ce stage (cf. Survey/autofix/failure_diagnosis.py::
_cause_level_from_replay) : un garde-fou de cohérence avec un diagnostic déjà
calculé, pas une seconde preuve. Pour stage="action", "certain" est de la même
façon déjà conditionné à BUG_PERSISTANT côté Phase 4 ; la vérification de
BUG_PERSISTANT ci-dessous reste néanmoins indépendante, en plus de
confidence_global, jamais à sa place — même principe de double vérification
que celui déjà appliqué à REPRODUIT/confidence_global pour l'extraction.

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

── Extension : artefact de traçabilité (même convention que les Phases 2-6) ───
En plus de son comportement inchangé ci-dessus, cette phase persiste désormais
le résultat (case_id, branch, worktree_path, base_sha, source_branch,
prompt_path) sous out_root/<case_id>/worktree.json — schema_version,
horodatage, avertissements explicites, même convention JSON déjà en usage
(Survey/autofix/context_selector.py, Survey/autofix/failure_diagnosis.py,
Survey/autofix/prompt_generator.py). Refus explicite (jamais un écrasement silencieux)
si cet artefact existe déjà sans force=True, vérifié AVANT toute mutation Git
(branche/worktree) pour ne pas créer d'état Git si l'écriture va de toute
façon être refusée. Reste un artefact technique interne (case_id, chemins) :
ces champs ne doivent fuiter nulle part ailleurs dans le pipeline (prompt,
sortie destinée à un humain non technique) — seule la sortie CLI déjà
existante, déjà destinée à l'opérateur technique, les affiche également.
"""

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from Survey.log_utils import log_debug, log_info

_TAG = "[AUTOFIX_WORKTREE]"
SCHEMA_VERSION = "1.0"

_DOM_EVIDENCE_FILES = (
    "dom_outer.html", "dom_body.html", "page_source.html",
    "pre_action_dom.html", "post_action_dom.html",
    "question_blocks.json", "validation_report.json",
)


def dom_evidence_relative_dir(case_id: str) -> Path:
    return Path("surveybot") / "failure_cases" / case_id / "artifacts"


def _copy_dom_evidence(failure_case_dir: Path, worktree_path: Path, case_id: str) -> None:
    """Copie les preuves déjà nettoyées dans un chemin ignoré du worktree.

    Cette isolation laisse git status, le calcul du patch et le commit aveugles
    aux preuves tout en les rendant lisibles à l'agent lancé dans le worktree.
    """
    source = failure_case_dir / "artifacts"
    destination = worktree_path / dom_evidence_relative_dir(case_id)
    sources = [source / name for name in _DOM_EVIDENCE_FILES]
    frames = source / "frames"
    if frames.is_dir():
        try:
            sources.extend(
                path for path in frames.iterdir()
                if path.is_file() and path.name.startswith("frame_")
                and path.name.endswith((".dom_outer.html", ".page_source.html"))
            )
        except OSError as exc:
            log_debug(_TAG, f"preuves DOM de frames indisponibles : {frames} ({exc})")
    for path in sources:
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        try:
            copied = destination / relative
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, copied)
            log_debug(_TAG, f"preuve DOM copiée : {relative.as_posix()} -> {copied}")
        except OSError as exc:
            log_debug(_TAG, f"preuve DOM indisponible : {path} ({exc})")

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
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        proc.stdout = proc.stdout or ""
        proc.stderr = proc.stderr or ""
        if "\ufffd" in proc.stdout or "\ufffd" in proc.stderr:
            raise AutofixWorktreeError(f"git {' '.join(args)} : sortie UTF-8 invalide")
        return proc
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
    if stage not in ("extraction", "action"):
        reasons.append(
            f"stage={stage!r} — seuls stage=\"extraction\" et stage=\"action\" sont dans le "
            "périmètre de cette phase"
        )

    replay_verdict = str((diagnosis.get("replay") or {}).get("verdict") or "")
    if replay_verdict != "REPRODUIT":
        reasons.append(f"replay.verdict={replay_verdict!r} — \"REPRODUIT\" requis")

    if stage == "action":
        real_dispatch = diagnosis.get("real_dispatch_replay")
        outcome = (
            (real_dispatch.get("validation_comparison") or {}).get("outcome")
            if isinstance(real_dispatch, dict) else None
        )
        if outcome != "BUG_PERSISTANT":
            reasons.append(
                f"stage=\"action\" : real_dispatch_replay.validation_comparison.outcome={outcome!r} "
                "— \"BUG_PERSISTANT\" requis (seule confirmation active, par réexécution réelle du "
                "dispatcher, que le bug persiste sur le code actuel — cf. Survey/autofix/replay_browser.py::"
                "execute_case_action) ; replay.verdict=\"REPRODUIT\" seul ne prouve rien sur le "
                "dispatcher pour ce stage (cf. Survey/autofix/failure_replay.py)"
            )

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
    manifest_path: Optional[Path] = None

    def as_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "case_id": self.case_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "branch": self.branch,
            "worktree_path": str(self.worktree_path),
            "base_sha": self.base_sha,
            "source_branch": self.source_branch,
            "prompt_path": str(self.prompt_path),
            "warnings": [],
        }


def _worktree_manifest_paths(out_root: Path, case_id: str) -> "tuple[Path, Path]":
    out_dir = out_root / case_id
    return out_dir, out_dir / "worktree.json"


def write_worktree_manifest(
    result: WorktreeResult,
    *,
    out_root: "str | Path" = "autofix_worktrees",
    force: bool = False,
) -> Path:
    """Persiste result sous out_root/<case_id>/worktree.json — même convention
    de traçabilité que les phases précédentes. Refuse si la sortie existe déjà
    et force=False ; jamais d'écrasement silencieux. Ne modifie rien d'autre
    (n'écrit jamais dans failure_cases/, diagnoses/, context_selections/,
    prompts/, ni dans le worktree Git lui-même).
    """
    out_root = Path(out_root)
    out_dir, out_file = _worktree_manifest_paths(out_root, result.case_id)

    if out_dir.exists():
        if not force:
            raise AutofixWorktreeExistsError(
                f"artefact de traçabilité Phase 7 déjà existant : {out_dir} "
                "(utiliser --force pour régénérer)"
            )
        if not out_file.is_file():
            raise AutofixWorktreeError(
                f"{out_dir} existe mais ne ressemble pas à une sortie générée par cet outil "
                "(pas de worktree.json) — suppression refusée, vérifier manuellement"
            )
        import shutil
        log_debug(_TAG, f"régénération forcée de l'artefact Phase 7 : suppression de {out_dir}")
        shutil.rmtree(out_dir)

    out_dir.mkdir(parents=True, exist_ok=False)
    out_file.write_text(
        json.dumps(result.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out_file


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


def remove_merged_case_worktree(
    *, case_id: str, manifest_path: Path, integration_branch: str,
    physical_worktrees_root: "Optional[str | Path]" = None,
) -> dict:
    """Retire uniquement le worktree Git vérifié d'un case mergé.

    Les contrôles de chemin, d'enregistrement Git, de branche et de propreté
    précèdent toute suppression : un manifeste erroné ne doit jamais pouvoir
    effacer un autre checkout ni des fichiers non suivis de l'opérateur.
    """
    def preserved(reason: str) -> dict:
        reason = reason.replace("\n", " ")
        log_debug(_TAG, f"case={case_id} worktree conservé : {reason}")
        return {"status": "PRESERVED", "reason": reason}

    try:
        manifest, error = _load_json(Path(manifest_path))
        if error or not isinstance(manifest, dict):
            return preserved(error or "worktree.json invalide")
        if not _is_safe_case_id(case_id) or manifest.get("case_id") != case_id:
            return preserved("case_id du manifeste incohérent")
        branch = manifest.get("branch")
        if not isinstance(branch, str) or not branch or branch in PROTECTED_BRANCHES:
            return preserved("branche absente ou protégée")
        if branch != f"autofix/{case_id}":
            return preserved("branche du manifeste inattendue pour ce case")
        path_value = manifest.get("worktree_path")
        if not isinstance(path_value, str) or not path_value:
            return preserved("chemin du worktree absent du manifeste")

        root_result = _run_git(["rev-parse", "--show-toplevel"], cwd=Path.cwd())
        if root_result.returncode:
            return preserved(f"dépôt courant introuvable : {root_result.stderr.strip()}")
        repo_root = Path(root_result.stdout.strip()).resolve()
        worktrees_root = Path(physical_worktrees_root or _default_worktrees_root(repo_root)).resolve()
        worktree_path = Path(path_value).resolve()
        if worktree_path == repo_root or worktree_path.parent != worktrees_root or worktree_path.name != case_id:
            return preserved("chemin hors de la racine des worktrees du case ou dépôt principal")
        if not worktree_path.exists():
            log_debug(_TAG, f"case={case_id} worktree déjà absent : {worktree_path}")
            return {"status": "ALREADY_ABSENT", "reason": "worktree déjà absent"}

        listed = _run_git(["worktree", "list", "--porcelain"], cwd=repo_root)
        if listed.returncode:
            return preserved(f"git worktree list a échoué : {listed.stderr.strip()}")
        records = []
        for block in listed.stdout.strip().split("\n\n"):
            fields = dict(line.split(" ", 1) for line in block.splitlines() if " " in line)
            if "worktree" in fields:
                records.append(fields)
        if not records or Path(records[0]["worktree"]).resolve() != repo_root:
            return preserved("le dépôt courant n'est pas le worktree principal enregistré")
        matches = [record for record in records if Path(record["worktree"]).resolve() == worktree_path]
        if len(matches) != 1 or matches[0].get("branch") != f"refs/heads/{branch}":
            return preserved("worktree non enregistré sur la branche du manifeste")

        current = _run_git(["symbolic-ref", "-q", "--short", "HEAD"], cwd=repo_root)
        if current.returncode or current.stdout.strip() != integration_branch:
            return preserved("branche d'intégration courante différente du résultat de merge")
        ancestor = _run_git([
            "merge-base", "--is-ancestor",
            f"refs/heads/{branch}", f"refs/heads/{integration_branch}",
        ], cwd=repo_root)
        if ancestor.returncode == 1:
            return preserved("branche du case non ancêtre de la branche d'intégration courante")
        if ancestor.returncode:
            return preserved(f"git merge-base a échoué : {ancestor.stderr.strip()}")
        status = _run_git(["status", "--porcelain", "--untracked-files=all"], cwd=worktree_path)
        if status.returncode:
            return preserved(f"git status du worktree a échoué : {status.stderr.strip()}")
        if status.stdout.strip():
            return preserved("worktree modifié ou fichiers non suivis présents")

        removed = _run_git(["worktree", "remove", str(worktree_path)], cwd=repo_root)
        if removed.returncode:
            return preserved(f"git worktree remove a échoué : {removed.stderr.strip()}")
        try:
            deleted = _run_git(["branch", "-d", branch], cwd=repo_root)
            if deleted.returncode:
                raise AutofixWorktreeError(f"git branch -d a échoué : {deleted.stderr.strip()}")
        except Exception as exc:
            # Rétablit le checkout si la seconde commande Git refuse la branche.
            try:
                restored = _run_git(["worktree", "add", str(worktree_path), branch], cwd=repo_root)
                if restored.returncode:
                    reason = f"{exc}; restauration échouée : {restored.stderr.strip()}"
                    log_debug(_TAG, f"case={case_id} retrait partiel : {reason}")
                    return {"status": "PARTIAL", "reason": reason}
            except Exception as restore_exc:
                reason = f"{exc}; restauration échouée : {restore_exc}"
                log_debug(_TAG, f"case={case_id} retrait partiel : {reason}")
                return {"status": "PARTIAL", "reason": reason}
            return preserved(str(exc))

        log_info(_TAG, f"case={case_id} worktree retiré et branche supprimée : {worktree_path} ({branch})")
        return {"status": "REMOVED", "reason": None}
    except Exception as exc:
        return preserved(f"retrait impossible ({type(exc).__name__}: {exc})")


def prepare_autofix_worktree(
    *,
    failure_case_dir: "str | Path",
    diagnosis_dir: "str | Path",
    prompt_dir: "str | Path",
    worktrees_root: "Optional[str | Path]" = None,
    out_root: "str | Path" = "autofix_worktrees",
    force: bool = False,
) -> WorktreeResult:
    """Prépare atomiquement une branche autofix/<case_id> et son worktree dédié,
    puis persiste le résultat sous out_root/<case_id>/worktree.json.

    Refuse avant tout effet de bord si une condition d'éligibilité manque, si la
    branche ou le chemin cible existe déjà, si case_id n'est pas sûr, ou si
    l'artefact de traçabilité existe déjà sans force=True. En cas d'échec Git
    après création de la branche, la retire avant de relancer l'erreur —
    jamais de ressource partielle laissée derrière.
    """
    failure_case_dir = Path(failure_case_dir)
    diagnosis_dir = Path(diagnosis_dir)
    prompt_dir = Path(prompt_dir)
    manifest_out_root = Path(out_root)

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

    # Vérifié avant toute mutation Git : inutile de créer branche/worktree si
    # l'écriture de l'artefact de traçabilité va de toute façon être refusée.
    manifest_out_dir, _manifest_out_file = _worktree_manifest_paths(manifest_out_root, case_id)
    if manifest_out_dir.exists() and not force:
        raise AutofixWorktreeExistsError(
            f"artefact de traçabilité Phase 7 déjà existant : {manifest_out_dir} "
            "(utiliser --force pour régénérer)"
        )

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

    _copy_dom_evidence(failure_case_dir, target, case_id)

    result = WorktreeResult(
        case_id=case_id,
        branch=branch,
        worktree_path=target,
        base_sha=base_sha,
        source_branch=source_branch,
        prompt_path=prompt_dir / "prompt.txt",
    )
    manifest_file = write_worktree_manifest(result, out_root=manifest_out_root, force=force)
    result.manifest_path = manifest_file

    log_info(
        _TAG,
        f"worktree créé case={case_id} branch={branch} base_sha={base_sha[:12]} "
        f"source_branch={source_branch} -> {target} (artefact : {manifest_file})",
    )

    return result
