from __future__ import annotations

"""Phase 8 — validation statique automatique d'un patch autofix déjà préparé
par la Phase 7 (Survey/autofix/autofix_worktree.py) : compilation, import isolé, lint
minimal (erreurs réelles uniquement, via Ruff) et tests unitaires existants
associés aux fichiers modifiés, si une convention fichier-source ->
fichier-de-test existe déjà dans ce dépôt. Avant même de tester le
comportement (Phase 9) — aucun test live, aucun navigateur, aucune page
réelle n'est jamais déclenché par cette phase, quel que soit le verdict.

Lecture seule sur la Phase 7 : lit uniquement le seul artefact qu'elle produit
(out_root/<case_id>/worktree.json, cf. Partie 1 de ce même chantier) — ne
rouvre jamais manifest.json ni diagnosis.json, ne recalcule aucune
éligibilité déjà tranchée en Phase 7.

── Fichiers concernés : jamais l'ensemble du dépôt ─────────────────────────
Le sous-ensemble vérifié est déterminé par comparaison Git entre base_sha
(enregistré par la Phase 7) et l'état courant du worktree autofix :
modifications trackées, committées ou non (`git diff --name-only base_sha`,
qui compare l'arbre de base_sha à l'arbre de travail courant — couvre donc
aussi bien un patch déjà committé sur la branche qu'un patch resté en attente
dans le worktree) UNIES aux nouveaux fichiers non trackés
(`git status --porcelain --untracked-files=all`, entrées `??`). Les fichiers
`.py` existants sont compilés/importés/lintés/testés ; tous les fichiers
ajoutés directement à la racine du paquet sont contrôlés séparément.

── Vérifications, une seule stratégie chacune ───────────────────────────────
1. Compilation isolée par sous-processus (`python -m py_compile`) — capture
   toute SyntaxError, y compris un nom d'argument dupliqué dans une
   signature (déjà un SyntaxError CPython natif — vérifié : « duplicate
   argument ... in function definition » — donc déjà couvert ici, jamais une
   seconde fois par un code de règle Ruff dédié qui n'existe d'ailleurs pas
   dans la version de Ruff installée sur ce dépôt, cf. point 3).
2. Import isolé par sous-processus dédié (`python -c "import <module>"`),
   PYTHONPATH limité à la racine du paquet détectée dans CE worktree (jamais
   supposée nommée en dur — cf. _resolve_package_root) — capture
   ImportError/NameError évident/signature de module rompue. sys.executable
   (l'interpréteur qui exécute cet outil, jamais un binaire "python" résolu
   au hasard sur PATH) est réutilisé tel quel pour l'enfant, afin de rester
   dans le même venv/la même installation de dépendances que l'outil
   lui-même — aucune hypothèse fragile sur l'environnement d'exécution.
3. Lint minimal via Ruff (`--isolated`, pour ignorer toute config présente
   dans le worktree/le dépôt qui élargirait ou réduirait la sélection),
   sélection figée aux seules règles de détection d'erreurs réelles
   disponibles dans la version installée : F821/F822/F823 (noms non
   définis/export non défini/variable locale référencée avant assignation) —
   jamais une règle de style/formatage. Un SyntaxError est de toute façon
   toujours rapporté par Ruff lui-même (code "invalid-syntax", vérifié non
   désactivable par --select), en plus du point 1 — les deux se recoupent
   volontairement, jamais un unique point de défaillance. F831 (argument
   dupliqué), mentionné dans la demande d'origine, n'existe pas comme règle
   sélectionnable dans cette version de Ruff (vérifié : « Rule F831 » est
   rejeté par `ruff rule`) — non grave, ce cas est un SyntaxError CPython
   natif déjà couvert par le point 1, documenté ici plutôt que masqué.
4. Tests unitaires associés par convention de nommage (cf.
   _find_associated_tests). Un module de correctif externe modifié doit avoir
   son test associé ; ce test est exécuté avec pytest dans l'interpréteur du
   pipeline. Pour les autres fichiers, l'absence de test reste non pénalisante.
5. Activation stricte si le loader ou un module de correctif change : dans un
   sous-processus borné, un registre neuf enregistre chaque déclaration et
   vérifie sa candidature pour l'ancrage et la position déclarés.
6. Rejet des nouveaux fichiers placés directement à la racine du paquet.

Chaque sous-processus a un budget de temps explicite ; un dépassement est
traité comme un échec de la vérification concernée, avec une raison
explicite — jamais un repli silencieux, jamais un processus qui pend
indéfiniment (subprocess.run(timeout=...) tue et attend l'enfant avant de
relancer TimeoutExpired : pas de processus zombie laissé derrière).

Sortie : un verdict tracé sur disque (même convention JSON que les phases
précédentes — schema_version, horodatage, case_id), out_root/<case_id>/
validation_static.json, distinguant explicitement ACCEPTED/REJECTED, avec le
détail et la raison d'échec de chaque vérification. Ne modifie jamais le
worktree, la branche autofix, ni aucun artefact d'une phase précédente.
"""

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from Survey.log_utils import log_debug, log_info

_TAG = "[STATIC_VALIDATOR]"
SCHEMA_VERSION = "1.0"

# Budgets par défaut, un par catégorie de vérification — jamais un budget
# global partagé qui masquerait quelle étape a réellement dépassé son temps.
DEFAULT_GIT_TIMEOUT_S = 15.0
DEFAULT_COMPILE_TIMEOUT_S = 15.0
DEFAULT_IMPORT_TIMEOUT_S = 20.0
DEFAULT_LINT_TIMEOUT_S = 30.0
DEFAULT_TESTS_TIMEOUT_S = 120.0

# Sélection Ruff figée aux seules règles de détection d'erreurs réelles
# disponibles dans la version installée (cf. docstring du module pour F831).
RUFF_SELECT = ["F821", "F822", "F823"]


class StaticValidationError(Exception):
    """Échec contrôlé — précondition manquante (artefact Phase 7, worktree, git)."""


class StaticValidationExistsError(StaticValidationError):
    """La sortie cible existe déjà et la régénération n'a pas été demandée."""


def _load_json(path: Path) -> "tuple[Any, Optional[str]]":
    if not path.is_file():
        return None, f"{path} absent"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{path} illisible/invalide ({exc})"


def _run(
    cmd: "list[str]",
    *,
    cwd: Path,
    timeout: float,
    env: "Optional[dict]" = None,
) -> "tuple[bool, str, str, bool]":
    """Unique stratégie d'exécution de sous-processus, réutilisée par les
    quatre vérifications. Retourne (ok, stdout, stderr, timed_out). Un
    dépassement de budget ne lève jamais d'exception jusqu'à l'appelant :
    abandon contrôlé, ok=False, timed_out=True."""
    log_debug(_TAG, f"{' '.join(cmd)} (cwd={cwd}, timeout={timeout}s)")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
        return proc.returncode == 0, proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired as exc:
        def _decode(part: Any) -> str:
            if isinstance(part, bytes):
                return part.decode("utf-8", errors="replace")
            return part or ""
        return False, _decode(exc.stdout), _decode(exc.stderr), True
    except OSError as exc:
        return False, "", f"exécution impossible : {exc}", False


# ─────────────────────────── Fichiers modifiés (Git) ────────────────────────

def _git_changed_paths(worktree_repo_root: Path, base_sha: str, *, timeout: float) -> "tuple[list[str], list[str]]":
    """Union des chemins trackés modifiés depuis base_sha (committés ou non) et
    des nouveaux fichiers non trackés — jamais l'ensemble du dépôt."""
    ok, out, err, timed_out = _run(
        ["git", "diff", "--name-only", base_sha], cwd=worktree_repo_root, timeout=timeout,
    )
    if timed_out:
        raise StaticValidationError(f"git diff --name-only a dépassé son budget ({timeout}s)")
    if not ok:
        raise StaticValidationError(f"git diff --name-only {base_sha} a échoué : {err.strip()}")
    tracked = {line.strip() for line in out.splitlines() if line.strip()}

    ok_added, out_added, err_added, added_timeout = _run(
        ["git", "diff", "--name-only", "--diff-filter=AR", base_sha],
        cwd=worktree_repo_root, timeout=timeout,
    )
    if added_timeout or not ok_added:
        raise StaticValidationError(
            f"git diff des fichiers ajoutés a échoué : {err_added.strip() or 'budget dépassé'}"
        )
    added_tracked = {line.strip() for line in out_added.splitlines() if line.strip()}

    ok2, out2, err2, timed_out2 = _run(
        ["git", "status", "--porcelain", "--untracked-files=all"], cwd=worktree_repo_root, timeout=timeout,
    )
    if timed_out2:
        raise StaticValidationError(f"git status --porcelain a dépassé son budget ({timeout}s)")
    if not ok2:
        raise StaticValidationError(f"git status --porcelain a échoué : {err2.strip()}")
    untracked = {
        line[3:].strip().strip('"')
        for line in out2.splitlines()
        if line.startswith("??")
    }

    return sorted(tracked | untracked), sorted(added_tracked | untracked)


def _filter_existing_python_files(repo_root: Path, rel_paths: "list[str]") -> "list[Path]":
    result = []
    for rel in rel_paths:
        if not rel.endswith(".py"):
            continue
        abs_path = repo_root / rel
        if abs_path.is_file():
            result.append(abs_path)
    return result


def _resolve_package_root(repo_root: Path) -> Path:
    """Le worktree est un clone complet du dépôt Git : le code Python (Survey/,
    tools/) peut vivre à la racine du dépôt ou dans un sous-dossier direct
    (c'est le cas dans ce dépôt) — jamais supposé nommé en dur, détecté en
    cherchant Survey/ et tools/ comme frères directs (racine, puis chaque
    sous-dossier direct — pas de parcours récursif profond)."""
    candidates = [repo_root] + sorted(p for p in repo_root.iterdir() if p.is_dir())
    for candidate in candidates:
        if (candidate / "Survey").is_dir() and (candidate / "tools").is_dir():
            return candidate
    raise StaticValidationError(
        f"impossible de localiser Survey/ et tools/ comme frères directs dans {repo_root} "
        "(ni à la racine, ni dans un sous-dossier direct) — structure de dépôt inattendue, "
        "vérification statique impossible"
    )


# ────────────────────────────────── Compilation ─────────────────────────────

def _check_compile(files: "list[Path]", *, timeout: float) -> dict:
    entries = []
    ok_all = True
    for f in files:
        ok, out, err, timed_out = _run(
            [sys.executable, "-m", "py_compile", str(f)], cwd=f.parent, timeout=timeout,
        )
        success = ok and not timed_out
        entry: dict = {"file": str(f), "ok": success}
        if timed_out:
            entry["error"] = f"compilation a dépassé son budget ({timeout}s)"
        elif not ok:
            entry["error"] = (err or out).strip()
        else:
            entry["error"] = None
        entries.append(entry)
        ok_all = ok_all and success
    return {"ok": ok_all, "files": entries}


# ──────────────────────────────────── Import ────────────────────────────────

def _module_name_for(f: Path, package_root: Path) -> "Optional[str]":
    try:
        rel = f.relative_to(package_root)
    except ValueError:
        return None
    parts = rel.with_suffix("").parts
    if not parts:
        return None
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else None


def _check_import(files: "list[Path]", *, package_root: Path, timeout: float) -> dict:
    entries = []
    ok_all = True
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(package_root) + (os.pathsep + existing if existing else "")

    for f in files:
        module = _module_name_for(f, package_root)
        if module is None:
            entries.append({
                "file": str(f), "module": None, "ok": False, "timed_out": False,
                "error": (
                    f"hors de la racine de paquet détectée ({package_root}) — nom de module non "
                    "dérivable, import isolé impossible"
                ),
            })
            ok_all = False
            continue

        ok, out, err, timed_out = _run(
            [sys.executable, "-c", f"import {module}"],
            cwd=str(package_root), timeout=timeout, env=env,
        )
        success = ok and not timed_out
        entry: dict = {"file": str(f), "module": module, "ok": success, "timed_out": timed_out}
        if timed_out:
            entry["error"] = f"import a dépassé son budget ({timeout}s)"
        elif not ok:
            entry["error"] = (err or out).strip()
        else:
            entry["error"] = None
        entries.append(entry)
        ok_all = ok_all and success

    return {"ok": ok_all, "files": entries}


_ACTIVATION_SCRIPT = r'''
import importlib
import json
import sys
from pathlib import Path

from Survey import external_fix_loader as loader
from Survey.external_fix_registry import ExternalFixRegistry

result = {"ok": True, "registered": [], "error": None}
try:
    registry = ExternalFixRegistry(root=Path(loader.__file__).resolve().parent)
    if len(loader.MODULE_IMPORTS) > loader.MAX_MODULES:
        raise ValueError("MODULE_IMPORTS dépasse MAX_MODULES")
    for import_fixes in loader.MODULE_IMPORTS:
        try:
            fixes = import_fixes()
        except Exception as exc:
            raise RuntimeError(f"module={import_fixes.__name__}: {type(exc).__name__}: {exc}") from exc
        if not isinstance(fixes, tuple) or len(fixes) > loader.MAX_FIXES_PER_MODULE:
            raise ValueError(f"module={import_fixes.__name__}: FIXES invalide ou trop grand")
        for fix in fixes:
            fix_id = getattr(fix, "fix_id", "<inconnu>")
            try:
                registry.register(fix)
            except Exception as exc:
                raise RuntimeError(f"fix_id={fix_id}: {type(exc).__name__}: {exc}") from exc
            candidates = registry.candidates(
                stage=fix.stage, anchor_function_id=fix.anchor_function_id,
                position=fix.position,
            )
            if not any(candidate is fix for candidate in candidates):
                raise RuntimeError(f"fix_id={fix_id}: absent des candidats pour son ancrage/position")
            result["registered"].append(fix_id)
    for module_name in json.loads(sys.argv[1]):
        module = importlib.import_module(f"Survey.{module_name}")
        fixes = getattr(module, "FIXES", None)
        if not isinstance(fixes, tuple) or not fixes:
            raise ValueError(f"module={module_name}: FIXES absent ou vide")
        for fix in fixes:
            fix_id = getattr(fix, "fix_id", "<inconnu>")
            if registry.get(fix_id) is not fix:
                raise RuntimeError(f"fix_id={fix_id}: module={module_name} absent de MODULE_IMPORTS")
except Exception as exc:
    result["ok"] = False
    result["error"] = f"{type(exc).__name__}: {exc}"
sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
'''


def _check_activation(changed_rel: "list[str]", *, worktree_path: Path,
                      package_root: Path, timeout: float) -> dict:
    survey_root = package_root / "Survey"
    changed = {worktree_path / rel for rel in changed_rel}
    modules = sorted({
        path.stem for path in changed
        if path.parent == survey_root and path.name.startswith("external_fix_")
        and path.suffix == ".py" and path.name not in
        {"external_fix_loader.py", "external_fix_registry.py"}
    })
    if survey_root / "external_fix_loader.py" not in changed and not modules:
        return {"ok": True, "skipped": True, "registered": [], "timed_out": False, "error": None}

    # Le loader de production ignore certains refus en debug. Un registre neuf
    # et register() strict rendent ces refus visibles avant tout score/commit.
    env = os.environ.copy()
    env["PYTHONPATH"] = str(package_root) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    env["LOG_LEVEL"] = "INFO"
    ok, out, err, timed_out = _run(
        [sys.executable, "-c", _ACTIVATION_SCRIPT, json.dumps(modules)],
        cwd=package_root, timeout=timeout, env=env,
    )
    if timed_out:
        return {"ok": False, "skipped": False, "registered": [], "timed_out": True,
                "error": f"contrôle d'activation : budget dépassé ({timeout}s)"}
    if not ok:
        return {"ok": False, "skipped": False, "registered": [], "timed_out": False,
                "error": f"contrôle d'activation : {(err or out).strip()[-2000:]}"}
    try:
        result = json.loads(out.strip().splitlines()[-1])
        if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
            raise ValueError("résultat incomplet")
    except (IndexError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "skipped": False, "registered": [], "timed_out": False,
                "error": f"résultat du contrôle d'activation illisible : {exc}"}
    return {"ok": result["ok"], "skipped": False,
            "registered": result.get("registered", []), "timed_out": False,
            "error": result.get("error")}


# ───────────────────────────────────── Lint ─────────────────────────────────

def _check_lint(files: "list[Path]", *, timeout: float) -> dict:
    base = {"ok": True, "tool": "ruff", "select": RUFF_SELECT, "violations": [], "timed_out": False, "error": None}
    if not files:
        return base

    ruff_path = shutil.which("ruff")
    if not ruff_path:
        return {
            **base,
            "ok": False,
            "error": (
                "ruff introuvable sur PATH — vérification impossible, jamais ignorée "
                "silencieusement (traité comme un échec de cette étape)"
            ),
        }

    cmd = [
        ruff_path, "check", "--isolated", "--no-cache",
        f"--select={','.join(RUFF_SELECT)}", "--output-format=json",
        *[str(f) for f in files],
    ]
    ok, out, err, timed_out = _run(cmd, cwd=files[0].parent, timeout=timeout)
    if timed_out:
        return {**base, "ok": False, "timed_out": True, "error": f"ruff a dépassé son budget ({timeout}s)"}

    # ruff check retourne un code de sortie != 0 dès qu'au moins une violation
    # est trouvée : ce n'est pas en soi un échec d'exécution de l'outil. En
    # revanche une sortie standard vide n'est PAS "zéro violation" — un run
    # réussi émet toujours au moins "[]" (vérifié) ; une sortie vide signale un
    # échec réel de l'outil (erreur d'invocation, crash) et doit être traité
    # comme un échec de cette vérification, jamais comme un succès silencieux.
    if not out.strip():
        return {
            **base, "ok": False,
            "error": f"ruff n'a produit aucune sortie exploitable : {(err or out).strip()[:2000] or 'aucun détail'}",
        }
    try:
        violations = json.loads(out)
    except json.JSONDecodeError:
        return {
            **base, "ok": False,
            "error": f"sortie ruff illisible : {(err or out).strip()[:2000]}",
        }

    return {
        **base,
        "ok": len(violations) == 0,
        "violations": [
            {
                "file": v.get("filename"),
                "code": v.get("code"),
                "message": v.get("message"),
                "location": v.get("location"),
            }
            for v in violations
        ],
    }


# ──────────────────────────────────── Tests ─────────────────────────────────

def _find_associated_tests(source_file: Path, package_root: Path) -> "list[Path]":
    """Reconnaît plusieurs conventions Python usuelles de nommage
    fichier-source -> fichier-de-test — aucune n'est en usage dans ce dépôt à
    ce jour (cf. docstring du module), mais la détection reste réelle : elle
    s'activera automatiquement le jour où l'une d'elles apparaît, sans
    supposer laquelle à l'avance."""
    stem = source_file.stem
    candidates = [
        package_root / "tests" / f"test_{stem}.py",
        source_file.parent / f"test_{stem}.py",
        source_file.parent / f"{stem}_test.py",
    ]
    try:
        rel_dir = source_file.parent.relative_to(package_root)
        candidates.append(package_root / "tests" / rel_dir / f"test_{stem}.py")
    except ValueError:
        pass
    seen = set()
    found = []
    for c in candidates:
        if c.is_file() and c not in seen:
            seen.add(c)
            found.append(c)
    return found


def _pytest_available() -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec("pytest") is not None
    except (ImportError, ValueError):
        return False


def _check_tests(files: "list[Path]", *, package_root: Path, timeout: float,
                 required: "tuple[Path, ...]" = ()) -> dict:
    associated: "dict[str, list[Path]]" = {}
    for f in files:
        found = _find_associated_tests(f, package_root)
        if found:
            associated[str(f)] = found

    skipped = [str(f) for f in files if str(f) not in associated]

    missing_required = [str(f) for f in required if str(f) not in associated]
    if missing_required:
        return {
            "ok": False, "convention_found": bool(associated),
            "note": "test associé requis pour chaque module de correctif externe modifié",
            "executed": [], "skipped_no_test": skipped, "timed_out": False,
            "error": f"test associé absent pour {missing_required}",
        }

    if not associated:
        return {
            "ok": True,
            "convention_found": False,
            "note": (
                "aucune convention de nommage fichier-source -> fichier-de-test trouvée dans ce "
                "dépôt pour les fichiers modifiés (recherché : tests/test_<nom>.py, "
                "test_<nom>.py/<nom>_test.py à côté de la source, arbre tests/<même dossier> "
                "miroir) — absence de test non pénalisante en soi, cf. Survey/autofix/static_validator.py"
            ),
            "executed": [], "skipped_no_test": skipped, "timed_out": False, "error": None,
        }

    if not _pytest_available():
        return {
            "ok": False,
            "convention_found": True,
            "note": (
                f"test(s) associé(s) trouvé(s) pour {sorted(associated)} mais pytest n'est pas "
                "disponible dans l'interpréteur exécutant cet outil — vérification impossible, "
                "jamais ignorée silencieusement"
            ),
            "executed": [], "skipped_no_test": skipped, "timed_out": False, "error": "pytest indisponible",
        }

    test_files = sorted({str(t) for tests in associated.values() for t in tests})
    ok, out, err, timed_out = _run(
        [sys.executable, "-m", "pytest", "-q", *test_files],
        cwd=package_root, timeout=timeout,
    )
    success = ok and not timed_out
    entry = {
        "ok": success,
        "convention_found": True,
        "note": f"test(s) exécuté(s) pour {sorted(associated)}",
        "executed": test_files,
        "skipped_no_test": skipped,
        "timed_out": timed_out,
        "error": None,
    }
    if timed_out:
        entry["error"] = f"tests a dépassé son budget ({timeout}s)"
    elif not ok:
        entry["error"] = (out or err).strip()[-4000:]
    return entry


# ────────────────────────────────── Orchestration ───────────────────────────

@dataclass
class StaticValidationResult:
    case_id: str
    branch: str
    base_sha: str
    changed_files: "list[str]"
    checks: dict
    reasons: "list[str]" = field(default_factory=list)
    warnings: "list[str]" = field(default_factory=list)

    @property
    def verdict(self) -> str:
        return "ACCEPTED" if not self.reasons else "REJECTED"

    def as_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "case_id": self.case_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "branch": self.branch,
            "base_sha": self.base_sha,
            "changed_files": self.changed_files,
            "checks": self.checks,
            "verdict": self.verdict,
            "reasons": self.reasons,
            "warnings": self.warnings,
        }


def validate_patch_static(
    worktree_manifest_path: "str | Path",
    *,
    git_timeout_s: float = DEFAULT_GIT_TIMEOUT_S,
    compile_timeout_s: float = DEFAULT_COMPILE_TIMEOUT_S,
    import_timeout_s: float = DEFAULT_IMPORT_TIMEOUT_S,
    lint_timeout_s: float = DEFAULT_LINT_TIMEOUT_S,
    tests_timeout_s: float = DEFAULT_TESTS_TIMEOUT_S,
) -> StaticValidationResult:
    """Valide statiquement le patch présent dans le worktree décrit par
    worktree_manifest_path (out_root/<case_id>/worktree.json, seul artefact
    produit par la Phase 7 — jamais manifest.json/diagnosis.json rouverts,
    jamais l'éligibilité Phase 7 recalculée)."""
    worktree_manifest_path = Path(worktree_manifest_path)
    manifest, err = _load_json(worktree_manifest_path)
    if err or not isinstance(manifest, dict):
        raise StaticValidationError(f"worktree.json (Phase 7) {err or 'ne contient pas un objet JSON'}")

    case_id = str(manifest.get("case_id") or "")
    branch = str(manifest.get("branch") or "")
    base_sha = str(manifest.get("base_sha") or "")
    worktree_path_raw = str(manifest.get("worktree_path") or "")
    if not case_id or not base_sha or not worktree_path_raw:
        raise StaticValidationError(
            f"worktree.json (Phase 7) incomplet (case_id/base_sha/worktree_path) : {worktree_manifest_path}"
        )

    worktree_path = Path(worktree_path_raw)
    if not worktree_path.is_dir():
        raise StaticValidationError(f"worktree_path (Phase 7) introuvable sur disque : {worktree_path}")

    warnings: "list[str]" = []

    changed_rel, added_rel = _git_changed_paths(worktree_path, base_sha, timeout=git_timeout_s)
    changed_files = _filter_existing_python_files(worktree_path, changed_rel)
    package_root = _resolve_package_root(worktree_path)
    root_new = sorted(
        rel for rel in added_rel
        if (worktree_path / rel).parent == package_root and (worktree_path / rel).is_file()
    )
    root_check = {
        "ok": not root_new, "files": root_new,
        "error": f"nouveau fichier à la racine du projet : {', '.join(root_new)}" if root_new else None,
    }

    if not changed_files:
        warnings.append(
            "aucun fichier .py modifié détecté entre base_sha et l'état courant du worktree "
            "autofix (patch vide ou pas encore appliqué) — rien à compiler/importer/linter/tester, "
            "vérifications vacuously réussies"
        )
        checks = {
            "compile": {"ok": True, "files": []},
            "import": {"ok": True, "files": []},
            "lint": {"ok": True, "tool": "ruff", "select": RUFF_SELECT, "violations": [], "timed_out": False, "error": None},
            "tests": {
                "ok": True, "convention_found": False,
                "note": "aucun fichier modifié à associer à un test",
                "executed": [], "skipped_no_test": [], "timed_out": False, "error": None,
            },
            "root_files": root_check,
            "activation": {"ok": True, "skipped": True, "registered": [], "timed_out": False, "error": None},
        }
        return StaticValidationResult(
            case_id=case_id, branch=branch, base_sha=base_sha,
            changed_files=[], checks=checks,
            reasons=[root_check["error"]] if root_new else [], warnings=warnings,
        )

    fix_modules = tuple(
        f for f in changed_files if f.parent == package_root / "Survey"
        and f.name.startswith("external_fix_")
        and f.name not in ("external_fix_loader.py", "external_fix_registry.py")
    )

    checks = {
        "compile": _check_compile(changed_files, timeout=compile_timeout_s),
        "import": _check_import(changed_files, package_root=package_root, timeout=import_timeout_s),
        "lint": _check_lint(changed_files, timeout=lint_timeout_s),
        "tests": _check_tests(changed_files, package_root=package_root,
                              timeout=tests_timeout_s, required=fix_modules),
        "activation": _check_activation(changed_rel, worktree_path=worktree_path,
                                        package_root=package_root, timeout=import_timeout_s),
        "root_files": root_check,
    }

    reasons: "list[str]" = []
    for name in ("compile", "import", "lint", "tests", "activation", "root_files"):
        if not checks[name]["ok"]:
            reasons.append(f"{name} : {checks[name].get('error') or 'échec'}")

    return StaticValidationResult(
        case_id=case_id, branch=branch, base_sha=base_sha,
        changed_files=[str(f) for f in changed_files],
        checks=checks, reasons=reasons, warnings=warnings,
    )


def _manifest_paths(out_root: Path, case_id: str) -> "tuple[Path, Path]":
    out_dir = out_root / case_id
    return out_dir, out_dir / "validation_static.json"


def write_static_validation(
    worktree_manifest_path: "str | Path",
    *,
    out_root: "str | Path" = "autofix_static_validations",
    force: bool = False,
    git_timeout_s: float = DEFAULT_GIT_TIMEOUT_S,
    compile_timeout_s: float = DEFAULT_COMPILE_TIMEOUT_S,
    import_timeout_s: float = DEFAULT_IMPORT_TIMEOUT_S,
    lint_timeout_s: float = DEFAULT_LINT_TIMEOUT_S,
    tests_timeout_s: float = DEFAULT_TESTS_TIMEOUT_S,
) -> Path:
    """Exécute validate_patch_static et écrit le résultat sous
    out_root/<case_id>/validation_static.json. Lève StaticValidationExistsError
    si la sortie existe déjà et force=False — jamais d'écrasement silencieux."""
    worktree_manifest_path = Path(worktree_manifest_path)
    manifest, err = _load_json(worktree_manifest_path)
    if err or not isinstance(manifest, dict):
        raise StaticValidationError(f"worktree.json (Phase 7) {err or 'ne contient pas un objet JSON'}")
    case_id = str(manifest.get("case_id") or "")
    if not case_id:
        raise StaticValidationError(f"worktree.json (Phase 7) sans case_id exploitable : {worktree_manifest_path}")

    out_root = Path(out_root)
    out_dir, out_file = _manifest_paths(out_root, case_id)

    if out_dir.exists():
        if not force:
            raise StaticValidationExistsError(
                f"validation statique déjà existante : {out_dir} (utiliser --force pour régénérer)"
            )
        if not out_file.is_file():
            raise StaticValidationError(
                f"{out_dir} existe mais ne ressemble pas à une sortie générée par cet outil "
                "(pas de validation_static.json) — suppression refusée, vérifier manuellement"
            )
        log_debug(_TAG, f"régénération forcée : suppression de {out_dir}")
        shutil.rmtree(out_dir)

    result = validate_patch_static(
        worktree_manifest_path,
        git_timeout_s=git_timeout_s,
        compile_timeout_s=compile_timeout_s,
        import_timeout_s=import_timeout_s,
        lint_timeout_s=lint_timeout_s,
        tests_timeout_s=tests_timeout_s,
    )

    out_dir.mkdir(parents=True, exist_ok=False)
    out_file.write_text(
        json.dumps(result.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    log_info(
        _TAG,
        f"validation statique case={result.case_id} verdict={result.verdict} "
        f"fichiers={len(result.changed_files)} -> {out_file}",
    )
    return out_file
