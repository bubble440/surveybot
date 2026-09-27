from __future__ import annotations

"""Registre de stabilité PROUVÉE des fonctions gelées (Survey/
extractor_integrity.json, Phase 11-A), calculé automatiquement à partir de
l'historique Git réel de ce fichier — jamais édité à la main, jamais un
remplacement de BOT_EVOLUTION_MEMORY.md (qui reste la mémoire narrative lue
par la Phase 4). Complément : un fait daté et compté, là où BEM porte un
récit humain.

Purement en LECTURE SEULE sur Survey/extractor_integrity.json (registre
actuel de CE dépôt, non modifié), l'historique Git de ce fichier, et
diagnoses/ (Phase 4) — ne modifie jamais BOT_EVOLUTION_MEMORY.md ni aucun
artefact d'une phase existante. Aucun effet de bord.

── Investigation faite avant d'écrire ce module (structure réelle de
l'historique Git de Survey/extractor_integrity.json) ─────────────────────────
Seulement 4 commits touchent ce fichier à ce jour (vérifié : `git log --follow
-- surveybot/Survey/extractor_integrity.json`) : sa création (176 lignes,
58 clés — incluant un doublon de chemin historique pour
`input_slider.py::set_sliderpoints`, sous deux orthographes distinctes,
"Survey/input_slider.py::..." ET "input_slider.py::...", même hash), puis
trois correctifs successifs (suppression de la mauvaise clé, renommage de
l'autre vers le chemin correct, réordonnancement cosmétique) — aucun de ces
commits ne change jamais la VALEUR de hash d'une clé qui reste présente sous
le même nom, seulement des ajouts/suppressions/renommages de clé. Le fichier
est du JSON indenté, chaque entrée sur 3 lignes ("clé": {\\n "hash": "..."
\\n},) : un changement de valeur pour une clé qui ne bouge pas apparaîtrait
comme un diff sur la seule ligne "hash" — mais UN RENOMMAGE DE CLÉ (déjà
observé en pratique ci-dessus) invaliderait un parsing par diff textuel ligne
à ligne. Stratégie retenue en conséquence : jamais un parsing de diff — le
contenu COMPLET du fichier est relu et JSON-parsé à CHAQUE commit qui le
touche (`git show <sha>:<chemin>`), puis comparé en mémoire d'un commit à
l'autre pour CHAQUE CLÉ indépendamment (présence/absence/valeur). Fiable quel
que soit l'historique réel (renommage de clé, réordonnancement, ajout,
suppression), jamais dépendant de la façon dont git a choisi de représenter
un diff au niveau ligne.

── Algorithme, par entrée du registre ACTUEL (fichier::fonction -> hash) ─────
1. Commits touchant Survey/extractor_integrity.json, du plus récent au plus
   ancien (`git log --format=... -- <chemin>`, PAS --follow : le fichier
   lui-même n'a jamais été renommé, vérifié ci-dessus ; un renommage futur du
   FICHIER DE REGISTRE lui-même tronquerait l'historique remonté plutôt que
   de deviner un chemin antérieur — limite disclosée, jamais masquée).
   Contenu JSON-parsé à chaque révision ; une révision illisible/invalide est
   consignée en avertissement et traitée comme "clé absente" à ce point,
   jamais devinée.
2. Si le dernier commit ne porte pas EXACTEMENT le hash actuel du disque pour
   cette clé (changement non encore committé) : history_exploitable=false,
   raison explicite — jamais une estimation à partir d'un historique qui ne
   couvre pas la valeur réellement en vigueur.
3. Sinon, en remontant depuis le commit le plus récent : stable_since =
   le commit le plus ANCIEN du run ININTERROMPU où cette clé porte déjà le
   hash actuel (une absence de la clé compte comme une interruption, au même
   titre qu'une valeur différente — jamais une continuité supposée à travers
   un trou).
4. Sur toute la durée de vie de la clé (tous les commits, pas seulement le
   run ci-dessus) : distinct_hash_values_count = nombre de valeurs de hash
   distinctes jamais portées par cette clé ; changes_count = ce compte - 1
   (jamais négatif) ; had_gap_in_history = la clé a-t-elle disparu puis
   réapparu au moins une fois (signal de transparence sur stable_since,
   jamais une correction silencieuse de ce dernier).
5. incidents_since_stable = nombre de diagnoses/<case_id>/diagnosis.json
   (Phase 4) dont modules_likely_involved cite ce même fichier ET dont
   created_at >= stable_since (date du commit) — les incidents antérieurs à
   la dernière modification ne concernent plus la version actuelle du code,
   ignorés. None (jamais 0) si stable_since est lui-même indéterminé.

── Rapprochement fichier registre <-> module diagnosis.json ─────────────────
Les clés du registre sont relatives à Survey/ (ex. "dom_analyzer.py", ou
"../preselection/x.py" pour un module hors Survey/) ; modules_likely_involved
(Survey/failure_diagnosis.py, chemins extraits de BOT_EVOLUTION_MEMORY.md)
porte parfois le préfixe "Survey/" (ex. "Survey/dom_analyzer.py") — les deux
formes coexistent réellement dans BEM (vérifié en lisant Survey/
failure_diagnosis.py::_files_from_entry). _normalize_module_path retire les
préfixes "./"/"../"/"Survey/" des deux côtés avant comparaison — stratégie
unique et déterministe, jamais une comparaison floue.

── RÈGLES STRICTES respectées ─────────────────────────────────────────────────
Purement en lecture seule (aucune commande Git mutante, jamais un `git show`
en dehors d'une lecture de contenu). Jamais un pourcentage de réussite
(aucune source ne compte les extractions réussies, cf. Survey/
autofix_metrics.py pour cette même limite déjà actée en Phase 17/19) —
seulement des faits comptables et datés. Jamais un chiffre deviné :
history_exploitable=false et une raison explicite remplacent toute valeur
qui aurait nécessité une supposition. Budget de temps explicite
(git_timeout_s, une seule valeur partagée par toutes les commandes Git de ce
module) sur chaque commande. Patch minimal : Survey/extractor_integrity.py
(REGISTRY_FILENAME/_load_registry) réutilisé tel quel, jamais réimplémenté ni
modifié ; Survey/failure_diagnosis.py non importé (diagnosis.json relu
directement, en JSON brut — aucune dépendance sur ses fonctions internes,
qui ne sont pas conçues pour cet usage).

── Sortie ──────────────────────────────────────────────────────────────────
Instantané horodaté (même convention que Survey/autofix_metrics.py — jamais
un fichier unique écrasé) sous out_root/<horodatage>/stability_report.json :
une entrée par fonction du registre actuel, triée par incidents_since_stable
décroissant (les fonctions les plus instables depuis leur dernière
modification en tête ; les entrées non exploitables, jamais confondues avec
un compte de zéro incident, sont reléguées en fin de liste).
"""

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from Survey.extractor_integrity import REGISTRY_FILENAME, _load_registry
from Survey.log_utils import log_debug, log_info

_TAG = "[EXTRACTOR_STABILITY]"
SCHEMA_VERSION = "1.0"

DEFAULT_GIT_TIMEOUT_S = 15.0

_SURVEY_DIR = Path(__file__).resolve().parent
_REGISTRY_PATH = _SURVEY_DIR / REGISTRY_FILENAME

_FIELD_SEP = "\x1f"
_RECORD_SEP = "\x1e"


class ExtractorStabilityError(Exception):
    """Échec contrôlé — registre absent/illisible, ou commande Git indisponible
    (budget dépassé, dépôt introuvable). Jamais levée pour une entrée
    individuelle sans historique exploitable — cf. KeyStability.
    history_exploitable=False, signalée explicitement, pas une exception."""


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


def _resolve_repo_root(git_timeout_s: float) -> Path:
    ok, out, err, timed_out = _run_git(
        ["rev-parse", "--show-toplevel"], cwd=_SURVEY_DIR, timeout=git_timeout_s,
    )
    if timed_out:
        raise ExtractorStabilityError(f"git rev-parse --show-toplevel a dépassé son budget ({git_timeout_s}s)")
    if not ok:
        raise ExtractorStabilityError(f"dépôt Git introuvable depuis {_SURVEY_DIR} : {err.strip()}")
    return Path(out.strip())


def _fetch_registry_history(
    git_timeout_s: float,
) -> "tuple[list[tuple[str, str, Optional[dict]]], list[str]]":
    """Commits touchant Survey/extractor_integrity.json, du plus récent au
    plus ancien, chacun avec son contenu JSON-parsé à cette révision exacte
    (None si illisible/invalide à cette révision — jamais deviné, consigné en
    avertissement). Liste de commits vide si aucun historique Git exploitable
    (fichier jamais committé) — jamais une exception dans ce cas, chaque
    entrée du registre le signalera individuellement."""
    repo_root = _resolve_repo_root(git_timeout_s)
    try:
        registry_rel = _REGISTRY_PATH.resolve().relative_to(repo_root).as_posix()
    except ValueError as exc:
        raise ExtractorStabilityError(
            f"{_REGISTRY_PATH} n'est pas sous le dépôt Git résolu ({repo_root}) : {exc}"
        ) from exc

    ok, out, err, timed_out = _run_git(
        ["log", f"--format=%H{_FIELD_SEP}%cI{_RECORD_SEP}", "--", registry_rel],
        cwd=repo_root, timeout=git_timeout_s,
    )
    if timed_out:
        raise ExtractorStabilityError(f"git log -- {registry_rel} a dépassé son budget ({git_timeout_s}s)")
    if not ok:
        raise ExtractorStabilityError(f"git log -- {registry_rel} a échoué : {err.strip()}")

    warnings: "list[str]" = []
    commits: "list[tuple[str, str, Optional[dict]]]" = []
    for record in out.split(_RECORD_SEP):
        record = record.strip()
        if not record or _FIELD_SEP not in record:
            continue
        sha, date = record.split(_FIELD_SEP, 1)
        sha, date = sha.strip(), date.strip()
        if not sha:
            continue

        ok_show, show_out, show_err, timed_out_show = _run_git(
            ["show", f"{sha}:{registry_rel}"], cwd=repo_root, timeout=git_timeout_s,
        )
        if timed_out_show:
            raise ExtractorStabilityError(f"git show {sha}:{registry_rel} a dépassé son budget ({git_timeout_s}s)")

        content: Optional[dict] = None
        if not ok_show:
            warnings.append(
                f"commit {sha[:12]} : contenu de {registry_rel} illisible à cette révision "
                f"({show_err.strip()[:200]}) — traité comme registre absent à ce point"
            )
        else:
            try:
                parsed = json.loads(show_out)
            except ValueError as exc:
                warnings.append(
                    f"commit {sha[:12]} : {registry_rel} n'est pas un JSON valide à cette révision ({exc})"
                )
            else:
                if isinstance(parsed, dict):
                    content = parsed
                else:
                    warnings.append(
                        f"commit {sha[:12]} : {registry_rel} ne contient pas un objet JSON à cette révision"
                    )
        commits.append((sha, date, content))

    return commits, warnings


def _entry_hash(content: Optional[dict], key: str) -> Optional[str]:
    if not isinstance(content, dict):
        return None
    entry = content.get(key)
    if not isinstance(entry, dict):
        return None
    h = entry.get("hash")
    return h if isinstance(h, str) and h else None


def _normalize_module_path(path: str) -> str:
    """Normalisation unique et déterministe pour comparer une clé de registre
    (fichier relatif à Survey/) à un module de diagnosis.json.
    modules_likely_involved (chemins parfois préfixés "Survey/", cf. docstring
    du module) — retire "./"/"../"/"Survey/" en tête, jamais une comparaison
    floue/approximative."""
    p = path.strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    while p.startswith("../"):
        p = p[3:]
    if p.startswith("Survey/"):
        p = p[len("Survey/"):]
    return p


@dataclass
class KeyStability:
    key: str
    file: str
    function: str
    current_hash: str
    history_exploitable: bool
    history_reason: Optional[str] = None
    first_seen_commit: Optional[str] = None
    first_seen_date: Optional[str] = None
    stable_since_commit: Optional[str] = None
    stable_since_date: Optional[str] = None
    distinct_hash_values_count: Optional[int] = None
    changes_count: Optional[int] = None
    had_gap_in_history: Optional[bool] = None
    incidents_since_stable: Optional[int] = None
    incident_case_ids: "list[str]" = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "file": self.file,
            "function": self.function,
            "current_hash": self.current_hash,
            "history_exploitable": self.history_exploitable,
            "history_reason": self.history_reason,
            "first_seen_commit": self.first_seen_commit,
            "first_seen_date": self.first_seen_date,
            "stable_since_commit": self.stable_since_commit,
            "stable_since_date": self.stable_since_date,
            "distinct_hash_values_count": self.distinct_hash_values_count,
            "changes_count": self.changes_count,
            "had_gap_in_history": self.had_gap_in_history,
            "incidents_since_stable": self.incidents_since_stable,
            "incident_case_ids": self.incident_case_ids,
        }


def _key_stability(
    key: str, current_hash: str, commits: "list[tuple[str, str, Optional[dict]]]",
) -> KeyStability:
    file_name, _, function_name = key.partition("::")

    if not commits:
        return KeyStability(
            key=key, file=file_name, function=function_name, current_hash=current_hash,
            history_exploitable=False,
            history_reason=f"aucun commit Git trouvé pour {REGISTRY_FILENAME} — historique inexploitable",
        )

    latest_sha, latest_date, latest_content = commits[0]
    latest_hash = _entry_hash(latest_content, key)
    if latest_hash != current_hash:
        return KeyStability(
            key=key, file=file_name, function=function_name, current_hash=current_hash,
            history_exploitable=False,
            history_reason=(
                f"la valeur actuelle sur disque ({current_hash[:12]}...) ne correspond pas au dernier "
                f"commit du registre ({latest_sha[:12]}, hash={(latest_hash or 'absent de ce commit')!r}) "
                "— changement probablement non committé, jamais un historique deviné"
            ),
        )

    # Remontée depuis le plus récent : stable_since = commit le plus ancien du
    # run ininterrompu à current_hash (absence de clé = interruption, comme
    # une valeur différente — jamais une continuité supposée à travers un trou).
    stable_since_sha, stable_since_date = latest_sha, latest_date
    for sha, date, content in commits:
        if _entry_hash(content, key) == current_hash:
            stable_since_sha, stable_since_date = sha, date
        else:
            break

    # Faits sur toute la durée de vie de la clé (tous les commits, pas
    # seulement le run ci-dessus) : valeurs distinctes jamais portées, et
    # détection d'un trou (disparition puis réapparition, même à l'identique).
    distinct_hashes: "set[str]" = set()
    ever_seen = False
    had_gap = False
    first_seen_sha: Optional[str] = None
    first_seen_date: Optional[str] = None
    for sha, date, content in reversed(commits):  # plus ancien -> plus récent
        h = _entry_hash(content, key)
        if h is not None:
            distinct_hashes.add(h)
            if not ever_seen:
                first_seen_sha, first_seen_date = sha, date
            ever_seen = True
        elif ever_seen:
            had_gap = True

    return KeyStability(
        key=key, file=file_name, function=function_name, current_hash=current_hash,
        history_exploitable=True, history_reason=None,
        first_seen_commit=first_seen_sha, first_seen_date=first_seen_date,
        stable_since_commit=stable_since_sha, stable_since_date=stable_since_date,
        distinct_hash_values_count=len(distinct_hashes),
        changes_count=max(0, len(distinct_hashes) - 1),
        had_gap_in_history=had_gap,
    )


def _load_diagnosis_facts(diagnoses_root: Path) -> "tuple[list[dict], list[str]]":
    """[{"case_id", "created_at": datetime aware, "modules": set(normalisé)}]
    à partir de diagnoses/<case_id>/diagnosis.json (Phase 4, relu en JSON brut
    — aucune dépendance sur Survey/failure_diagnosis.py). diagnoses_root
    absent/vide -> liste vide, jamais une erreur (Phase 4 peut n'avoir jamais
    tourné dans cet environnement) ; un case illisible/invalide/sans
    created_at exploitable est consigné en avertissement et ignoré, jamais
    deviné."""
    facts: "list[dict]" = []
    warnings: "list[str]" = []
    if not diagnoses_root.is_dir():
        return facts, warnings

    for case_dir in sorted(diagnoses_root.iterdir()):
        if not case_dir.is_dir():
            continue
        diag_path = case_dir / "diagnosis.json"
        if not diag_path.is_file():
            continue
        try:
            data = json.loads(diag_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            warnings.append(f"{diag_path} illisible/invalide, ignoré pour le comptage d'incidents ({exc})")
            continue
        if not isinstance(data, dict):
            warnings.append(f"{diag_path} ne contient pas un objet JSON, ignoré")
            continue

        created_at_raw = data.get("created_at")
        try:
            created_at = datetime.fromisoformat(str(created_at_raw))
        except (TypeError, ValueError):
            warnings.append(
                f"{diag_path} : created_at={created_at_raw!r} illisible — case ignoré pour le comptage "
                "d'incidents, jamais une date devinée"
            )
            continue

        modules = {
            _normalize_module_path(str(entry["module"]))
            for entry in (data.get("modules_likely_involved") or [])
            if isinstance(entry, dict) and entry.get("module")
        }
        facts.append({
            "case_id": str(data.get("case_id") or case_dir.name),
            "created_at": created_at,
            "modules": modules,
        })

    return facts, warnings


def _count_incidents(
    file_name: str, stable_since_date: Optional[str], facts: "list[dict]",
) -> "tuple[Optional[int], list[str]]":
    """None (jamais 0) si stable_since_date est lui-même indéterminé — un
    compte de zéro incidents et une absence de référence temporelle ne
    doivent jamais être confondus."""
    if stable_since_date is None:
        return None, []
    try:
        cutoff = datetime.fromisoformat(stable_since_date)
    except ValueError:
        return None, []

    normalized_file = _normalize_module_path(file_name)
    matched = sorted(
        f["case_id"] for f in facts
        if f["created_at"] >= cutoff and normalized_file in f["modules"]
    )
    return len(matched), matched


@dataclass
class StabilityReport:
    entries: "list[KeyStability]"
    warnings: "list[str]"
    registry_path: str
    diagnoses_root: str

    def as_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "registry_path": self.registry_path,
            "diagnoses_root": self.diagnoses_root,
            "total_entries": len(self.entries),
            "entries": [e.as_dict() for e in self.entries],
            "warnings": self.warnings,
        }


def compute_stability(
    *,
    diagnoses_root: "str | Path" = "diagnoses",
    git_timeout_s: float = DEFAULT_GIT_TIMEOUT_S,
) -> StabilityReport:
    """Calcule le rapport de stabilité pour le registre ACTUEL de ce dépôt.
    Lève ExtractorStabilityError seulement pour un échec Git réel (budget,
    dépôt introuvable) ou un registre fondamentalement illisible — jamais
    pour une entrée individuelle sans historique exploitable."""
    registry = _load_registry(_SURVEY_DIR)
    if not isinstance(registry, dict):
        raise ExtractorStabilityError(
            f"{_REGISTRY_PATH} ne contient pas un objet JSON exploitable comme registre"
        )

    commits, history_warnings = _fetch_registry_history(git_timeout_s)
    diagnoses_root_path = Path(diagnoses_root)
    facts, diag_warnings = _load_diagnosis_facts(diagnoses_root_path)

    warnings: "list[str]" = [*history_warnings, *diag_warnings]
    if not registry:
        warnings.append("registre d'intégrité vide — rien à analyser")

    entries: "list[KeyStability]" = []
    for key in sorted(registry):
        raw_entry = registry.get(key)
        current_hash = raw_entry.get("hash") if isinstance(raw_entry, dict) else None
        if not isinstance(current_hash, str) or not current_hash:
            warnings.append(f"entrée {key!r} du registre malformée (hash absent/invalide) — ignorée")
            continue

        stability = _key_stability(key, current_hash, commits)
        incidents, case_ids = _count_incidents(stability.file, stability.stable_since_date, facts)
        stability.incidents_since_stable = incidents
        stability.incident_case_ids = case_ids
        entries.append(stability)

    # Les plus instables en tête ; une entrée non exploitable (incidents=None)
    # n'est JAMAIS traitée comme "0 incident" — reléguée en fin de liste,
    # jamais mélangée aux comptes réels.
    entries.sort(key=lambda e: (
        0 if e.incidents_since_stable is not None else 1,
        -(e.incidents_since_stable or 0),
        e.key,
    ))

    return StabilityReport(
        entries=entries, warnings=warnings,
        registry_path=str(_REGISTRY_PATH), diagnoses_root=str(diagnoses_root_path),
    )


def _timestamped_output_dir(out_root: Path) -> Path:
    """Un instantané par run, jamais un fichier écrasé — même convention que
    Survey/autofix_metrics.py::_timestamped_output_dir. Dupliquée ici plutôt
    qu'importée : importer Survey/autofix_metrics.py entraînerait toute sa
    chaîne de dépendances (Survey/replay_browser.py, Playwright) pour une
    fonction utilitaire de huit lignes, sans rapport avec ce module purement
    Git/JSON (même raison déjà documentée par Survey/parallel_safety.py pour
    cette même duplication)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    candidate = out_root / stamp
    suffix = 1
    while candidate.exists():
        candidate = out_root / f"{stamp}_{suffix}"
        suffix += 1
    return candidate


def write_stability_report(
    *,
    diagnoses_root: "str | Path" = "diagnoses",
    out_root: "str | Path" = "extractor_stability_reports",
    git_timeout_s: float = DEFAULT_GIT_TIMEOUT_S,
) -> Path:
    """Exécute compute_stability et écrit out_root/<horodatage>/
    stability_report.json — toujours un nouveau dossier horodaté, jamais un
    rapport précédent écrasé (cet outil est fait pour tourner à répétition)."""
    report = compute_stability(diagnoses_root=diagnoses_root, git_timeout_s=git_timeout_s)

    out_root_path = Path(out_root)
    out_root_path.mkdir(parents=True, exist_ok=True)
    out_dir = _timestamped_output_dir(out_root_path)
    out_dir.mkdir(parents=True, exist_ok=False)
    out_file = out_dir / "stability_report.json"
    out_file.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    log_info(_TAG, f"{len(report.entries)} fonction(s) analysée(s) -> {out_file}")
    return out_file
