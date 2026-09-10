from __future__ import annotations

"""Transformation d'un failure_case (Phase 2, Survey/failure_case_builder.py) et de
son résultat de replay (Phase 3, Survey/failure_replay.py) en diagnostic structuré
(diagnosis.json) : symptôme observé, comportement attendu, cause avec justification
traçable, modules probablement concernés, niveau de confiance global.

Lecture seule sur les Phases 1-3 : ce module appelle Survey.failure_replay.replay_failure_case()
tel quel (aucune modification de dom_analyzer.py/des validators/failure_replay.py) et ne lit
que des fichiers déjà produits par ces phases. Il n'écrit jamais dans failure_cases/ ni ne
modifie le case source — sa sortie va dans un dossier séparé (diagnoses/ par défaut).

Ne génère aucun prompt pour un agent de coding, ne nomme aucune fonction à modifier, ne
propose aucun patch — hors périmètre explicite de cette phase.

── Comportement attendu (expected_behavior) ──────────────────────────────────
Table statique par failure_type, dérivée directement de la lecture du code source des
validators (Survey/action_validator.py, Survey/question_block_validator.py) — chaque
description cite la fonction/le check exact d'où elle vient. Ce n'est pas une narration :
c'est la condition que le validator vérifie, telle qu'elle est écrite dans son code.

── Cause (niveau + justification) ─────────────────────────────────────────────
Le niveau de cause est déterminé UNIQUEMENT par le verdict de replay (jamais par une
lecture "plausible" du symptôme seul) :
  - REPRODUIT (mêmes failure_types exacts sur le DOM figé)      -> certain
  - DIFFERENT (le replay tourne et signale un problème, mais un
    ensemble différent de failure_types)                        -> probable
  - NON_REPRODUIT / NON_REJOUABLE / replay non concluant        -> plausible
Un case dont le manifest porte incomplete=true (Phase 2 a déjà signalé des artefacts
manquants/invalides) plafonne la confiance globale à "plausible", quel que soit le
verdict de replay — la donnée source elle-même est déjà reconnue incomplète.

── Modules probablement concernés (modules_likely_involved) ──────────────────
Recherche exclusivement dans Survey/BOT_EVOLUTION_MEMORY.md (jamais dans le code source)
des entrées "### ..." dont le corps contient, en correspondance EXACTE (sous-chaîne,
insensible à la casse), un signal du case : un flag booléen =true du dict "context" d'un
question_block (ex. ifop_zip2city_widget, cloudresearch_sentry, mui_consent_checkbox),
ou le group_key d'un bloc (entier, puis son préfixe avant le premier ":" — sauf préfixe
générique comme "checkbox"/"radio", écarté car trop large pour être spécifique à ce case).
Le fichier associé à une entrée est lu soit sur une ligne "Fichier : X" explicite, soit
directement dans l'en-tête "### nom — Survey/x.py" quand l'entrée n'a pas de ligne "Fichier :"
séparée (les deux formats coexistent réellement dans le fichier).
provider_domain seul est délibérément EXCLU de cette recherche : vérifié en pratique, un
hostname donné héberge des dizaines de widgets différents et matche donc des entrées sans
rapport avec le pattern précis de ce case (faux positif avéré, pas une supposition — cf.
commentaire au-dessus de _search_tokens). Le(s) fichier(s) associé(s) viennent de la ligne
"Fichier :" de l'entrée trouvée — jamais devinés. Si rien ne correspond, la liste reste
vide (jamais une association approximative).
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from Survey.failure_case_builder import _failure_types as _report_failure_types
from Survey.failure_replay import replay_failure_case
from Survey.log_utils import log_debug, log_info

_TAG = "[FAILURE_DIAGNOSIS]"
SCHEMA_VERSION = "1.0"

_MEMORY_FILE = Path(__file__).resolve().parent / "BOT_EVOLUTION_MEMORY.md"

LEVEL_CERTAIN = "certain"
LEVEL_PROBABLE = "probable"
LEVEL_PLAUSIBLE = "plausible"


class DiagnosisError(Exception):
    """Échec contrôlé de génération d'un diagnostic."""


class DiagnosisExistsError(DiagnosisError):
    """La sortie cible existe déjà et la régénération n'a pas été demandée."""


# ─────────────────────────────────────────────────────────────────────────────
# Comportement attendu par failure_type — dérivé de la lecture directe de
# Survey/action_validator.py::validate_actions et
# Survey/question_block_validator.py::validate_question_blocks. Ne pas ajouter
# d'entrée ici sans l'avoir vérifiée contre le code source réel de ces validators.
# ─────────────────────────────────────────────────────────────────────────────
_EXPECTED_BEHAVIOR: dict[str, dict[str, str]] = {
    "invalid_action_shape": {
        "description": "Chaque action du plan doit être un objet JSON structuré (dict).",
        "source": "Survey/action_validator.py::validate_actions",
    },
    "action_missing_target_id": {
        "description": "Chaque action doit porter un target_id non vide désignant le bloc/élément visé.",
        "source": "Survey/action_validator.py::validate_actions",
    },
    "action_target_missing": {
        "description": "Le target_id d'une action doit correspondre à une entrée existante dans le "
        "registry DOM (get_target), donc avoir été enregistrée pendant l'extraction.",
        "source": "Survey/action_validator.py::validate_actions",
    },
    "action_value_not_in_registry_options": {
        "description": "Pour itype radio/checkbox avec une option_xpath_map plate connue, la valeur "
        "demandée par l'action doit correspondre (après normalisation) à une option "
        "réellement listée dans le registry pour ce target_id.",
        "source": "Survey/action_validator.py::validate_actions",
    },
    "dispatcher_reported_failure": {
        "description": "Le dispatcher doit rapporter un succès (dispatcher_success=True) pour "
        "l'exécution du plan d'actions transmis.",
        "source": "Survey/action_validator.py::validate_actions",
    },
    "dispatcher_false_negative": {
        "description": "Cas ifop_zip2city : le dispatcher a rapporté un échec, mais le signal DOM "
        "(zip/ville résolus) indique que l'action a réellement abouti — le dispatcher "
        "aurait dû rapporter un succès.",
        "source": "Survey/action_validator.py::validate_actions (_ifop_zip2city_false_negative_issue)",
    },
    "missing_block": {
        "description": "Un signal DOM fort et reconnu (widget qualtrics_ranked_choices ou zappi_max_diff) "
        "est présent, mais aucun bloc de question n'a été extrait pour lui.",
        "source": "Survey/question_block_validator.py::validate_question_blocks",
    },
    "invalid_block_shape": {
        "description": "Chaque bloc de question_blocks doit être un objet JSON structuré (dict).",
        "source": "Survey/question_block_validator.py::validate_question_blocks",
    },
    "missing_target_id": {
        "description": "Chaque bloc de question doit porter un target_id non vide.",
        "source": "Survey/question_block_validator.py::validate_question_blocks",
    },
    "registry_target_missing": {
        "description": "Le target_id d'un bloc doit correspondre à une entrée existante dans le "
        "registry DOM (get_target) — cohérence extraction/registry.",
        "source": "Survey/question_block_validator.py::validate_question_blocks",
    },
    "choice_without_options": {
        "description": "Un bloc itype radio/checkbox/dropdown doit lister au moins une option.",
        "source": "Survey/question_block_validator.py::validate_question_blocks",
    },
    "duplicate_options": {
        "description": "Les options d'un bloc doivent être uniques après normalisation (casse/espaces).",
        "source": "Survey/question_block_validator.py::validate_question_blocks",
    },
    "invalid_selection_limits": {
        "description": "min_select ne doit jamais excéder max_select lorsque les deux sont renseignés.",
        "source": "Survey/question_block_validator.py::validate_question_blocks",
    },
    "max_select_exceeds_options": {
        "description": "Pour itype checkbox, max_select ne doit pas dépasser le nombre d'options "
        "réellement disponibles.",
        "source": "Survey/question_block_validator.py::validate_question_blocks",
    },
}


def _clean_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _load_json(path: Path) -> "tuple[Any, Optional[str]]":
    if not path.is_file():
        return None, f"{path.name} absent"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{path.name} illisible/invalide ({exc})"


def _symptom_from_issues(report: Any) -> list[dict]:
    """Reformate chaque issue de validation_report.json tel quel (aucune interprétation)."""
    issues = report.get("issues") if isinstance(report, dict) else None
    out: list[dict] = []
    for issue in issues or []:
        if isinstance(issue, dict):
            out.append(dict(issue))
    return out


def _expected_behavior_for(failure_types: list[str]) -> list[dict]:
    out = []
    for ft in failure_types:
        entry = _EXPECTED_BEHAVIOR.get(ft)
        if entry:
            out.append({"failure_type": ft, **entry})
        else:
            out.append({
                "failure_type": ft,
                "description": "Aucune description enregistrée pour ce failure_type dans "
                "Survey/failure_diagnosis.py — non documenté, pas d'affirmation forcée.",
                "source": None,
            })
    return out


def _cause_level_from_replay(replay_verdict: Optional[str]) -> str:
    from Survey.failure_replay import VERDICT_REPRODUIT, VERDICT_DIFFERENT
    if replay_verdict == VERDICT_REPRODUIT:
        return LEVEL_CERTAIN
    if replay_verdict == VERDICT_DIFFERENT:
        return LEVEL_PROBABLE
    return LEVEL_PLAUSIBLE  # NON_REPRODUIT, NON_REJOUABLE, ou absence de verdict


def _cause_justification(original_types: list[str], replay_result) -> str:
    from Survey.failure_replay import (
        VERDICT_REPRODUIT, VERDICT_DIFFERENT, VERDICT_NON_REPRODUIT, VERDICT_NON_REJOUABLE,
    )
    verdict = replay_result.verdict
    if verdict == VERDICT_REPRODUIT:
        return (
            f"Le replay (Phase 3) sur {replay_result.dom_file_used} reproduit exactement les "
            f"mêmes failure_types que le case d'origine : {original_types}."
        )
    if verdict == VERDICT_DIFFERENT:
        return (
            f"Le replay sur {replay_result.dom_file_used} s'exécute et signale un problème, mais "
            f"un ensemble de failure_types différent ({replay_result.replayed_failure_types}) de "
            f"celui d'origine ({original_types}) — evaluate() décliné(s) : "
            f"{replay_result.evaluate_declined} (cf. Survey/dom_replay_shim.py, limites de fidélité "
            "d'un DOM statique)."
        )
    if verdict == VERDICT_NON_REPRODUIT:
        return (
            f"Le replay sur {replay_result.dom_file_used} ne signale plus aucun problème "
            f"(rapport ok=True) alors que le case d'origine rapportait {original_types} — soit "
            "l'incident est dépendant d'un état non capturé dans le DOM figé (JS/layout/runtime, "
            f"cf. evaluate() décliné(s)={replay_result.evaluate_declined}), soit il n'est plus "
            "présent sur ce DOM précis. Aucune conclusion forcée entre ces deux possibilités."
        )
    if verdict == VERDICT_NON_REJOUABLE:
        return (
            f"Replay non rejouable ({replay_result.reason}) — seul le symptôme enregistré dans "
            f"validation_report.json ({original_types}) est disponible, sans confirmation "
            "indépendante sur le DOM figé."
        )
    return (
        f"Aucun verdict de replay exploitable — seul le symptôme enregistré dans "
        f"validation_report.json ({original_types}) est disponible."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Modules probablement concernés — recherche exacte dans BOT_EVOLUTION_MEMORY.md
# ─────────────────────────────────────────────────────────────────────────────

_ENTRY_SPLIT_RE = re.compile(r"(?m)^###\s+.*$")
_FICHIER_LINE_RE = re.compile(r"(?m)^Fichier\s*:\s*(.+)$")
_FILE_TOKEN_RE = re.compile(r"[A-Za-z0-9_./\\]+\.py")


def _load_memory_entries() -> list[str]:
    """Découpe BOT_EVOLUTION_MEMORY.md en entrées '### ...' (une par bloc)."""
    try:
        text = _MEMORY_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        log_debug(_TAG, f"BOT_EVOLUTION_MEMORY.md illisible : {exc}")
        return []
    headers = list(_ENTRY_SPLIT_RE.finditer(text))
    entries = []
    for i, m in enumerate(headers):
        start = m.start()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        entries.append(text[start:end])
    return entries


def _files_from_entry(entry_text: str) -> list[str]:
    """Extrait les fichiers cités par une entrée '### ...'.

    Deux formats réels coexistent dans BOT_EVOLUTION_MEMORY.md (vérifié en
    pratique, ex. entrées "fill_ifop_zip2city_widget", "execute_action" autour
    de la ligne 2697) : une ligne "Fichier : X" explicite (format canonique
    documenté en tête de fichier), ou le fichier cité directement dans la ligne
    d'en-tête "### nom — Survey/x.py" (sans ligne "Fichier :" séparée). Les
    deux sources sont combinées — ni l'une ni l'autre seule ne couvre tout le
    fichier.
    """
    files: list[str] = []

    header_line = entry_text.strip().splitlines()[0] if entry_text.strip() else ""
    for tok in _FILE_TOKEN_RE.findall(header_line):
        normalized = tok.replace("\\", "/").strip()
        if normalized not in files:
            files.append(normalized)

    for m in _FICHIER_LINE_RE.finditer(entry_text):
        for tok in _FILE_TOKEN_RE.findall(m.group(1)):
            normalized = tok.replace("\\", "/").strip()
            if normalized not in files:
                files.append(normalized)

    return files


# Valeurs de "kind"/itype trop génériques pour servir de préfixe de group_key
# isolé (ex. group_key="checkbox:name:dom:category-options" -> préfixe "checkbox"
# matcherait quasi toute entrée du fichier mémoire évoquant un checkbox, sans
# rapport avec CE case précis). Seul un préfixe absent de cette liste — donc
# vraisemblablement un namespace de plateforme comme "cloudresearch_sentry" —
# est retenu comme token de recherche isolé. group_key entier reste toujours
# utilisé tel quel (bien plus spécifique, jamais filtré).
_GENERIC_GROUP_KEY_PREFIXES = {
    "radio", "checkbox", "text", "textarea", "dropdown", "select", "matrix",
    "number", "date", "single", "group", "dom", "name", "id", "role",
}

# provider_domain seul n'est délibérément PAS un token de recherche : vérifié en
# pratique, un hostname (ex. s2.ifoponline.com) apparaît dans de nombreuses
# entrées BOT_EVOLUTION_MEMORY.md sans rapport avec le pattern précis de CE case
# (la même plateforme héberge des dizaines de widgets différents) — false
# positive avéré, pas une supposition. Les signaux retenus sont donc uniquement
# ceux qui identifient un pattern précis : un flag booléen =true du contexte du
# bloc, ou son group_key (entier, ou préfixe non générique).


def _search_tokens(question_blocks: Any, target_id: Optional[str]) -> list[dict]:
    """Retourne [{"signal": <étiquette lisible>, "token": <sous-chaîne recherchée>}]."""
    tokens: list[dict] = []

    blocks = question_blocks if isinstance(question_blocks, list) else []
    relevant_blocks = [
        b for b in blocks
        if isinstance(b, dict) and (not target_id or _clean_str(b.get("target_id")) == target_id)
    ] or [b for b in blocks if isinstance(b, dict)]

    seen_flags: set = set()
    seen_keys: set = set()
    for block in relevant_blocks:
        context = block.get("context") if isinstance(block.get("context"), dict) else {}
        for key, value in context.items():
            if value is True and key not in seen_flags:
                seen_flags.add(key)
                tokens.append({"signal": f"context_flag:{key}", "token": str(key)})
        group_key = _clean_str(context.get("group_key"))
        if group_key and group_key not in seen_keys:
            seen_keys.add(group_key)
            tokens.append({"signal": f"group_key={group_key}", "token": group_key})
            prefix = group_key.split(":", 1)[0]
            if (
                prefix and prefix != group_key and len(prefix) >= 4
                and prefix.lower() not in _GENERIC_GROUP_KEY_PREFIXES
            ):
                tokens.append({"signal": f"group_key_prefix={prefix}", "token": prefix})

    return tokens


def _modules_likely_involved(question_blocks: Any, target_id: Optional[str]) -> list[dict]:
    tokens = _search_tokens(question_blocks, target_id)
    if not tokens:
        return []

    entries = _load_memory_entries()
    if not entries:
        return []

    # Une ligne par module, signaux/entrées mémoire agrégés (pas une ligne par
    # paire module/signal — un même module peut légitimement matcher plusieurs
    # signaux du même case, ce n'est pas une sortie concurrente).
    by_module: dict[str, dict] = {}
    for entry_text in entries:
        files = _files_from_entry(entry_text)
        if not files:
            continue
        header_line = entry_text.strip().splitlines()[0].lstrip("#").strip() if entry_text.strip() else ""
        for t in tokens:
            if t["token"].lower() not in entry_text.lower():
                continue
            for f in files:
                row = by_module.setdefault(f, {"module": f, "matched_signals": [], "memory_entries": []})
                if t["signal"] not in row["matched_signals"]:
                    row["matched_signals"].append(t["signal"])
                excerpt = header_line[:160]
                if excerpt and excerpt not in row["memory_entries"]:
                    row["memory_entries"].append(excerpt)

    return list(by_module.values())


@dataclass
class DiagnosisResult:
    case_id: str
    stage: str
    itype: Optional[str]
    target_id: Optional[str]
    provider_domain: Optional[str]
    frame_chain: Any
    symptom_failure_types: list = field(default_factory=list)
    symptom_issues: list = field(default_factory=list)
    expected_behavior: list = field(default_factory=list)
    replay: dict = field(default_factory=dict)
    cause_level: str = LEVEL_PLAUSIBLE
    cause_justification: str = ""
    modules_likely_involved: list = field(default_factory=list)
    confidence_global: str = LEVEL_PLAUSIBLE
    case_incomplete: bool = False
    warnings: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "case_id": self.case_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "stage": self.stage,
            "itype": self.itype,
            "target_id": self.target_id,
            "provider_domain": self.provider_domain,
            "frame_chain": self.frame_chain,
            "symptom": {
                "failure_types": self.symptom_failure_types,
                "issues": self.symptom_issues,
            },
            "expected_behavior": self.expected_behavior,
            "replay": self.replay,
            "cause": {
                "level": self.cause_level,
                "justification": self.cause_justification,
            },
            "modules_likely_involved": self.modules_likely_involved,
            "confidence_global": self.confidence_global,
            "case_incomplete": self.case_incomplete,
            "warnings": self.warnings,
        }


def diagnose_failure_case(case_dir: "str | Path") -> DiagnosisResult:
    case_dir = Path(case_dir)
    case_id = case_dir.name

    manifest, manifest_err = _load_json(case_dir / "manifest.json")
    if manifest_err or not isinstance(manifest, dict):
        raise DiagnosisError(f"manifest.json {manifest_err or 'invalide'}")

    artifacts_dir = case_dir / "artifacts"
    original_report, report_err = _load_json(artifacts_dir / "validation_report.json")
    if report_err or not isinstance(original_report, dict):
        raise DiagnosisError(f"validation_report.json {report_err or 'invalide'}")

    question_blocks, _qb_err = _load_json(artifacts_dir / "question_blocks.json")

    stage = str(manifest.get("stage") or "unknown")
    itype = manifest.get("itype")
    target_id = manifest.get("target_id")
    provider_domain = manifest.get("provider_domain")
    frame_chain = manifest.get("frame_chain")
    case_incomplete = bool(manifest.get("incomplete"))

    failure_types = _report_failure_types(original_report)
    symptom_issues = _symptom_from_issues(original_report)
    expected = _expected_behavior_for(failure_types)

    log_debug(_TAG, f"replay en cours pour diagnostic case={case_id}")
    replay_result = replay_failure_case(case_dir)

    cause_level = _cause_level_from_replay(replay_result.verdict)
    cause_justification = _cause_justification(failure_types, replay_result)

    modules = _modules_likely_involved(question_blocks, target_id)

    confidence_global = cause_level
    warnings: list[str] = []
    if case_incomplete and confidence_global != LEVEL_PLAUSIBLE:
        warnings.append(
            "manifest.incomplete=true (Phase 2) : confiance globale plafonnée à "
            f"'{LEVEL_PLAUSIBLE}' malgré un niveau de cause '{confidence_global}'."
        )
        confidence_global = LEVEL_PLAUSIBLE

    if not modules:
        warnings.append(
            "Aucune association vérifiée trouvée dans BOT_EVOLUTION_MEMORY.md pour les signaux "
            "de ce case (provider_domain / context flags / group_key) — liste vide plutôt que devinée."
        )

    return DiagnosisResult(
        case_id=case_id,
        stage=stage,
        itype=itype,
        target_id=target_id,
        provider_domain=provider_domain,
        frame_chain=frame_chain,
        symptom_failure_types=failure_types,
        symptom_issues=symptom_issues,
        expected_behavior=expected,
        replay=replay_result.as_dict(),
        cause_level=cause_level,
        cause_justification=cause_justification,
        modules_likely_involved=modules,
        confidence_global=confidence_global,
        case_incomplete=case_incomplete,
        warnings=warnings,
    )


def write_diagnosis(
    case_dir: "str | Path",
    *,
    out_root: "str | Path" = "diagnoses",
    force: bool = False,
) -> Path:
    """Génère le diagnostic et l'écrit sous out_root/case_<id>/diagnosis.json.

    Ne modifie jamais case_dir. Lève DiagnosisExistsError si la sortie existe déjà
    et force=False — jamais d'écrasement silencieux.
    """
    case_dir = Path(case_dir)
    # case_dir.name est déjà "case_<snapshot_id>" (nommage de failure_case_builder.py) :
    # pas de second préfixe "case_" ici, sous peine de "case_case_<snapshot_id>".
    out_root = Path(out_root)
    out_dir = out_root / case_dir.name
    out_file = out_dir / "diagnosis.json"

    if out_dir.exists():
        if not force:
            raise DiagnosisExistsError(
                f"diagnostic déjà existant : {out_dir} (utiliser --force pour régénérer)"
            )
        if not out_file.is_file():
            raise DiagnosisError(
                f"{out_dir} existe mais ne ressemble pas à une sortie générée par cet outil "
                "(pas de diagnosis.json) — suppression refusée, vérifier manuellement"
            )
        import shutil
        log_info(_TAG, f"régénération forcée : suppression de {out_dir}")
        shutil.rmtree(out_dir)

    result = diagnose_failure_case(case_dir)

    out_dir.mkdir(parents=True, exist_ok=False)
    out_file.write_text(
        json.dumps(result.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    log_info(
        _TAG,
        f"diagnostic créé case={result.case_id} cause={result.cause_level} "
        f"confiance={result.confidence_global} modules={len(result.modules_likely_involved)} -> {out_file}",
    )
    return out_file
