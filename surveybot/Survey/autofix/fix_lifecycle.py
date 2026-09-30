"""Suivi local/dev des correctifs externes et dossiers de consolidation.

Ce module ne charge aucun handler, ne modifie ni le registre runtime ni le core,
et ne prend aucune décision de merge. Les artefacts de preuve sont relus et
référencés par hash ; leur contenu sensible n'est jamais recopié dans les
sorties. Une preuve manquante ou incohérente bloque la transition concernée.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from Survey.external_fix_registry import ExternalFix, ExternalFixRegistry

SCHEMA_VERSION = "1.0"
NEW = "NOUVEAU"
OBSERVED = "OBSERVÉ"
VALIDATED = "VALIDÉ"
STABLE = "STABLE"
EVOLUTION_CANDIDATE = "EVOLUTION_CANDIDATE"

_MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
_MAX_EVENTS = 64
_MAX_CASES = 32
_MAX_METRIC_FILES = 128
_MAX_FIXES_PER_CANDIDATE = 16
_MAX_SIGNALS = 16
_SLUG = re.compile(r"[a-z][a-z0-9_]{0,47}\Z")
_FIX_ID = re.compile(r"[a-z][a-z0-9_]{2,63}\Z")
_CASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}\Z")
_REASONS = frozenset({
    "multiple_cases", "multiple_fixes", "activations", "overlapping_guards",
    "duplicated_core_logic",
})
_CORE_CHANGE_REASONS = frozenset({
    "too_many_fixes", "overlapping_guards", "duplicated_core_logic",
    "unsafe_external_fix", "incompatible_fixes",
})
_LINK_LEVELS = frozenset({"involved", "suspected", "root_cause_confirmed", "patched"})


class FixLifecycleError(ValueError):
    """Refus d'une transition ou d'une preuve incohérente."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: str) -> bool:
    return isinstance(value, str) and _SLUG.fullmatch(value) is not None


def _read(path: str | Path) -> tuple[dict, dict]:
    try:
        path = Path(path).resolve()
        valid = path.is_file() and path.stat().st_size <= _MAX_ARTIFACT_BYTES
    except (TypeError, ValueError, OSError) as exc:
        raise FixLifecycleError("chemin d'artefact invalide") from exc
    if not valid:
        raise FixLifecycleError(f"artefact absent ou trop grand : {path}")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise FixLifecycleError(f"artefact illisible : {path}") from exc
    if len(raw) > _MAX_ARTIFACT_BYTES:
        raise FixLifecycleError(f"artefact trop grand : {path}")
    try:
        data = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise FixLifecycleError(f"artefact JSON invalide : {path}") from exc
    if not isinstance(data, dict):
        raise FixLifecycleError(f"artefact JSON sans objet : {path}")
    return data, {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}


def _file_ref(path: str | Path, *, require_text: bool = False) -> dict:
    try:
        path = Path(path).resolve()
        if not path.is_file() or path.stat().st_size > _MAX_ARTIFACT_BYTES:
            raise FixLifecycleError(f"preuve absente ou trop grande : {path}")
        raw = path.read_bytes()
    except (TypeError, ValueError, OSError) as exc:
        raise FixLifecycleError("preuve illisible") from exc
    if len(raw) > _MAX_ARTIFACT_BYTES:
        raise FixLifecycleError("preuve trop grande")
    if require_text:
        try:
            if not raw.decode("utf-8").strip():
                raise FixLifecycleError("note de revue vide")
        except UnicodeError as exc:
            raise FixLifecycleError("note de revue non textuelle") from exc
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}


def _write(path: Path, value: dict) -> Path:
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if len(payload.encode("utf-8")) > _MAX_ARTIFACT_BYTES:
        raise FixLifecycleError("artefact de suivi trop grand")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".fix-lifecycle-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _record_path(root: str | Path, fix_id: str) -> Path:
    if not isinstance(fix_id, str) or _FIX_ID.fullmatch(fix_id) is None:
        raise FixLifecycleError("fix_id invalide")
    return Path(root) / fix_id / "lifecycle.json"


def load_fix_lifecycle(fix_id: str, *, out_root: str | Path = "fix_lifecycle") -> dict:
    data, _ = _read(_record_path(out_root, fix_id))
    fix = data.get("fix")
    if (data.get("schema_version") != SCHEMA_VERSION or not isinstance(fix, dict)
            or fix.get("fix_id") != fix_id or data.get("state") not in
            (NEW, OBSERVED, VALIDATED, STABLE, EVOLUTION_CANDIDATE)
            or not isinstance(data.get("history"), list) or not 0 < len(data["history"]) <= _MAX_EVENTS
            or not isinstance(data.get("signals"), list) or len(data["signals"]) > _MAX_SIGNALS):
        raise FixLifecycleError("état de correctif incohérent")
    return data


def create_fix_lifecycle(
    fix: ExternalFix, *, registry: ExternalFixRegistry,
    out_root: str | Path = "fix_lifecycle",
) -> Path:
    """Initialise un suivi depuis une entrée effectivement validée du registre."""
    if not isinstance(fix, ExternalFix) or registry.get(fix.fix_id) != fix:
        raise FixLifecycleError("correctif absent du registre validé")
    path = _record_path(out_root, fix.fix_id)
    identity = {
        "fix_id": fix.fix_id, "stage": fix.stage,
        "core_function_id": fix.core_function_id,
        "core_hash": fix.expected_core_hash,
        "anchor_function_id": fix.anchor_function_id,
        "position": fix.position,
        "case_ids": sorted(set(fix.case_ids)),
        "guard_sha256": hashlib.sha256(json.dumps([
            fix.condition.required_selectors, fix.condition.excluded_selectors,
        ]).encode()).hexdigest(),
        "required_selector_count": len(fix.condition.required_selectors),
        "excluded_selector_count": len(fix.condition.excluded_selectors),
    }
    if path.exists():
        previous = load_fix_lifecycle(fix.fix_id, out_root=out_root)
        if previous["fix"] == identity:
            return path
        raise FixLifecycleError("fix_id déjà suivi avec une identité différente")
    now = _now()
    return _write(path, {
        "schema_version": SCHEMA_VERSION, "fix": identity, "state": NEW,
        "created_at": now, "updated_at": now, "linked_cases": identity["case_ids"],
        "root_area": None, "failure_mechanism": None,
        "signals": [],
        "history": [{"from": None, "to": NEW, "at": now, "evidence": {}}],
    })


def mark_core_change_candidate(
    fix_id: str, *, reason: str, evidence_path: str | Path,
    out_root: str | Path = "fix_lifecycle",
) -> Path:
    """Signale une revue du core sans changer l'état du fix ni modifier le core."""
    if reason not in _CORE_CHANGE_REASONS:
        raise FixLifecycleError("motif de revue du core invalide")
    record = load_fix_lifecycle(fix_id, out_root=out_root)
    ref = _file_ref(evidence_path, require_text=True)
    if any(signal["reason"] == reason and signal["evidence"] == ref for signal in record["signals"]):
        return _record_path(out_root, fix_id)
    if len(record["signals"]) >= _MAX_SIGNALS:
        raise FixLifecycleError("borne de signaux de revue atteinte")
    now = _now()
    record["signals"].append({"code": "CORE_CHANGE_CANDIDATE", "reason": reason,
                              "evidence": ref, "at": now})
    record["updated_at"] = now
    return _write(_record_path(out_root, fix_id), record)


def _usage(fix: dict, evidence: dict) -> dict:
    paths = evidence.get("usage_snapshot_paths")
    absent = evidence.get("usage_unavailable")
    if not isinstance(paths, list) or len(paths) > _MAX_METRIC_FILES:
        raise FixLifecycleError("liste de métriques absente ou trop longue")
    if bool(paths) == (absent is True):
        raise FixLifecycleError("fournir des métriques ou déclarer explicitement leur absence")
    if not paths:
        return {"available": False, "reason": "not_instrumented_or_not_collected", "sources": []}
    producers: set[str] = set()
    calls = 0
    first: str | None = None
    last: str | None = None
    sources: list[dict] = []
    for path in paths:
        data, ref = _read(path)
        producer = data.get("producer_id")
        rows = data.get("functions")
        if (data.get("schema_version") != "1.0" or not isinstance(producer, str)
                or producer in producers or not isinstance(rows, list) or len(rows) > 1024):
            raise FixLifecycleError("instantané de métriques invalide ou producteur dupliqué")
        producers.add(producer)
        for row in rows:
            if not isinstance(row, dict) or row.get("function_id") != fix["core_function_id"]:
                continue
            if row.get("code_hash") != fix["core_hash"] or row.get("stage") != fix["stage"]:
                continue  # Version et stage non comparables, jamais ajoutés.
            count = row.get("call_count")
            row_first, row_last = row.get("first_called_at"), row.get("last_called_at")
            if type(count) is not int or count < 0 or not isinstance(row_first, str) or not isinstance(row_last, str):
                raise FixLifecycleError("compteur de fonction invalide")
            calls += count
            first = min(first, row_first) if first else row_first
            last = max(last, row_last) if last else row_last
        sources.append(ref)
    return {"available": True, "call_count": calls, "first_called_at": first,
            "last_called_at": last, "producer_count": len(producers), "sources": sources}


def _validated(fix: dict, linked_cases: list[str], evidence: dict) -> dict:
    case_ids = set(linked_cases)
    diagnoses = evidence.get("diagnosis_paths")
    replays = evidence.get("patch_replay_paths")
    if not isinstance(diagnoses, dict) or not isinstance(replays, dict) or set(diagnoses) != case_ids or set(replays) != case_ids:
        raise FixLifecycleError("diagnostic et rejeu requis pour chaque case du correctif")
    static, static_ref = _read(evidence.get("static_validation_path"))
    integrity, integrity_ref = _read(evidence.get("integrity_path"))
    if static.get("verdict") != "ACCEPTED" or integrity.get("verdict") != "ACCEPTED" or integrity.get("schema_version") != "2.0":
        raise FixLifecycleError("validation statique ou intégrité Phase 11-A non acceptée")
    base_sha = static.get("base_sha")
    branch = static.get("branch")
    if not isinstance(base_sha, str) or not base_sha or not isinstance(branch, str) or not branch or integrity.get("base_sha") != base_sha or integrity.get("branch") != branch:
        raise FixLifecycleError("version ou branche de validation incohérente")
    if (static.get("case_id") not in case_ids or integrity.get("case_id") != static.get("case_id")
            or integrity.get("errors") != [] or integrity.get("budget_exceeded") is not False
            or integrity.get("checked_entries") != integrity.get("total_entries")):
        raise FixLifecycleError("contrôle Phase 11-A incomplet ou rattaché à un autre case")
    rows = integrity.get("functions")
    if not isinstance(rows, list) or not any(
        isinstance(row, dict) and row.get("key") == fix["core_function_id"]
        and row.get("state") == "UNCHANGED" and row.get("patched_hash") == fix["core_hash"]
        for row in rows
    ):
        raise FixLifecycleError("fonction core non inchangée sur cette version")
    checked: list[dict] = []
    for case_id in sorted(case_ids):
        diagnosis, diag_ref = _read(diagnoses[case_id])
        replay, replay_ref = _read(replays[case_id])
        worktree = replay.get("worktree")
        before = diagnosis.get("replay")
        real_dispatch = diagnosis.get("real_dispatch_replay")
        comparison = real_dispatch.get("validation_comparison") if isinstance(real_dispatch, dict) else None
        before_signal = replay.get("before_signal")
        expected_before = (
            before_signal.get("replay_verdict") == "REPRODUIT" if fix["stage"] == "extraction"
            else before_signal.get("real_dispatch_outcome") == "BUG_PERSISTANT"
        ) if isinstance(before_signal, dict) else False
        if (
            diagnosis.get("case_id") != case_id or diagnosis.get("stage") != fix["stage"]
            or diagnosis.get("confidence_global") != "certain" or diagnosis.get("case_incomplete") is not False
            or not isinstance(before, dict) or before.get("verdict") != "REPRODUIT"
            or (fix["stage"] == "action" and (
                not isinstance(comparison, dict) or comparison.get("outcome") != "BUG_PERSISTANT"
            )) or not expected_before
            or replay.get("case_id") != case_id or replay.get("stage") != fix["stage"]
            or replay.get("refused") is not False or replay.get("patch_validated") is not True
            or replay.get("outcome") != "CORRECTIF_CONFIRME"
            or not isinstance(worktree, dict) or worktree.get("base_sha") != base_sha
            or worktree.get("branch") != branch
        ):
            raise FixLifecycleError(f"preuve avant/après non confirmée pour {case_id}")
        checked.append({"case_id": case_id, "diagnosis": diag_ref, "patch_replay": replay_ref})
    neighbors = evidence.get("neighbor_replay_paths")
    if (not isinstance(neighbors, dict) or len(neighbors) > _MAX_CASES
            or any(not isinstance(case_id, str) or _CASE_ID.fullmatch(case_id) is None for case_id in neighbors)
            or set(neighbors) & case_ids):
        raise FixLifecycleError("liste de voisins invalide")
    if not neighbors and evidence.get("no_neighbor_cases_reason") != "none_identified":
        raise FixLifecycleError("absence de cas voisins à expliciter")
    neighbor_refs: list[dict] = []
    for case_id, path in sorted(neighbors.items()):
        replay, ref = _read(path)
        worktree = replay.get("worktree")
        if (replay.get("case_id") != case_id or replay.get("refused") is not False
                or replay.get("outcome") != "CORRECTIF_CONFIRME" or replay.get("patch_validated") is not True
                or not isinstance(worktree, dict) or worktree.get("base_sha") != base_sha
                or worktree.get("branch") != branch):
            raise FixLifecycleError(f"rejeu voisin non confirmé : {case_id}")
        neighbor_refs.append({"case_id": case_id, "patch_replay": ref})
    return {"base_sha": base_sha, "branch": branch, "case_checks": checked,
            "neighbor_checks": neighbor_refs, "neighbors_explicitly_absent": not neighbors,
            "static_validation": static_ref, "integrity": integrity_ref,
            "limits": ["phase11b_regression_suite_unavailable"]}


def _stable(fix: dict, evidence: dict) -> dict:
    adoption_case = evidence.get("adoption_case_id")
    if adoption_case not in fix["case_ids"]:
        raise FixLifecycleError("case d'adoption inconnu du correctif")
    human, human_ref = _read(evidence.get("human_review_path"))
    merge_review, merge_review_ref = _read(evidence.get("merge_review_path"))
    merge, merge_ref = _read(evidence.get("merge_result_path"))
    observation, observation_ref = _read(evidence.get("fix_observation_path"))
    if any(item.get("case_id") != adoption_case for item in (human, merge_review, merge)) or (
        human.get("decision") != "APPROVED" or merge_review.get("decision") != "APPROVED"
        or merge.get("status") not in ("MERGED", "ALREADY_MERGED")
    ):
        raise FixLifecycleError("adoption humaine et merge non prouvés")
    start, end = observation.get("period_start"), observation.get("period_end")
    evaluations, activations = observation.get("evaluation_count"), observation.get("activation_count")
    try:
        dates_valid = datetime.fromisoformat(start) < datetime.fromisoformat(end)
    except (TypeError, ValueError):
        dates_valid = False
    if (
        observation.get("schema_version") != "1.0" or observation.get("fix_id") != fix["fix_id"]
        or observation.get("core_function_id") != fix["core_function_id"]
        or observation.get("core_hash") != fix["core_hash"]
        or not dates_valid
        or type(evaluations) is not int or type(activations) is not int
        or evaluations < activations or activations <= 0
        or observation.get("incident_count") != 0 or observation.get("contradictions_checked") is not True
    ):
        raise FixLifecycleError("observation d'activation insuffisante ou contradictoire")
    return {"adoption_case_id": adoption_case, "period_start": start, "period_end": end,
            "evaluation_count": evaluations, "activation_count": activations,
            "human_review": human_ref, "merge_review": merge_review_ref,
            "merge_result": merge_ref, "fix_observation": observation_ref,
            "observation_provenance": "operator_supplied_not_runtime_verified"}


def advance_fix_lifecycle(
    fix_id: str, target: str, evidence: dict, *, out_root: str | Path = "fix_lifecycle",
) -> Path:
    """Applique une transition vérifiée ; rejoue la même demande sans effet."""
    path = _record_path(out_root, fix_id)
    record = load_fix_lifecycle(fix_id, out_root=out_root)
    if not isinstance(evidence, dict) or len(record["history"]) >= _MAX_EVENTS:
        raise FixLifecycleError("preuve invalide ou historique saturé")
    fingerprint = hashlib.sha256(json.dumps(evidence, sort_keys=True, default=str).encode()).hexdigest()
    if target == record["state"]:
        if record["history"][-1].get("request_sha256") == fingerprint:
            return path
        raise FixLifecycleError("état déjà atteint avec d'autres preuves")
    allowed = {
        NEW: {OBSERVED}, OBSERVED: {VALIDATED}, VALIDATED: {STABLE, EVOLUTION_CANDIDATE},
        STABLE: {EVOLUTION_CANDIDATE, OBSERVED}, EVOLUTION_CANDIDATE: {OBSERVED},
    }
    if target not in allowed.get(record["state"], set()):
        raise FixLifecycleError(f"transition {record['state']} -> {target} interdite")
    fix = record["fix"]
    if target == OBSERVED and record["state"] in (STABLE, EVOLUTION_CANDIDATE):
        new_case = evidence.get("new_case_id")
        level = evidence.get("attribution")
        diagnosis, ref = _read(evidence.get("diagnosis_path"))
        if (not isinstance(new_case, str) or _CASE_ID.fullmatch(new_case) is None
                or new_case in record["linked_cases"]
                or len(record["linked_cases"]) >= _MAX_CASES or level not in _LINK_LEVELS
                or diagnosis.get("case_id") != new_case or diagnosis.get("stage") != fix["stage"]):
            raise FixLifecycleError("nouvel incident incohérent")
        if level == "root_cause_confirmed" and (
            diagnosis.get("confidence_global") != "certain"
            or not isinstance(diagnosis.get("replay"), dict)
            or diagnosis["replay"].get("verdict") != "REPRODUIT"
        ):
            raise FixLifecycleError("cause non confirmée par le diagnostic")
        summary = {"new_case_id": new_case, "attribution": level, "diagnosis": ref}
        record["linked_cases"].append(new_case)
        record["linked_cases"].sort()
    elif target == OBSERVED:
        summary = _usage(fix, evidence)
    elif target == VALIDATED:
        summary = _validated(fix, record["linked_cases"], evidence)
    elif target == STABLE:
        summary = _stable(fix, evidence)
    else:
        candidate, ref = _read(evidence.get("candidate_path"))
        candidate_fixes = candidate.get("fixes")
        matching = [item for item in candidate_fixes
                    if isinstance(item, dict) and isinstance(item.get("identity"), dict)
                    and item["identity"].get("fix_id") == fix_id] if isinstance(candidate_fixes, list) else []
        if (candidate.get("schema_version") != SCHEMA_VERSION
                or candidate.get("review_status") != "READY_FOR_HUMAN_ANALYSIS"
                or candidate.get("blockers") != []
                or not isinstance(candidate.get("fix_ids"), list)
                or fix_id not in candidate["fix_ids"]
                or candidate.get("core_function_id") != fix["core_function_id"]
                or candidate.get("core_hash") != fix["core_hash"]
                or len(matching) != 1 or matching[0].get("source_lifecycle_sha256") != _digest(record)):
            raise FixLifecycleError("dossier de consolidation sans ce correctif")
        summary = {"candidate_id": candidate.get("candidate_id"), "candidate": ref}
    for key in ("root_area", "failure_mechanism"):
        if key in evidence:
            value = evidence[key]
            if value is not None and not _safe_id(value):
                raise FixLifecycleError(f"{key} invalide")
            record[key] = value
    now = _now()
    record["history"].append({"from": record["state"], "to": target, "at": now,
                              "request_sha256": fingerprint, "evidence": summary})
    record["state"] = target
    record["updated_at"] = now
    return _write(path, record)


def prepare_evolution_candidate(
    fix_ids: list[str], *, reason: str, rule_review_path: str | Path,
    compatibility: dict[str, str] | None = None,
    lifecycle_root: str | Path = "fix_lifecycle",
    out_root: str | Path = "evolution_candidates",
) -> Path:
    """Produit un dossier de revue immuable ; aucun patch ni merge du core."""
    if (not isinstance(fix_ids, list) or not 0 < len(fix_ids) <= _MAX_FIXES_PER_CANDIDATE
            or any(not isinstance(fix_id, str) or _FIX_ID.fullmatch(fix_id) is None for fix_id in fix_ids)
            or len(set(fix_ids)) != len(fix_ids)):
        raise FixLifecycleError("liste de correctifs invalide")
    if reason not in _REASONS:
        raise FixLifecycleError("motif de consolidation inconnu")
    records = [load_fix_lifecycle(fix_id, out_root=lifecycle_root) for fix_id in sorted(fix_ids)]
    identities = {(r["fix"]["core_function_id"], r["fix"]["core_hash"]) for r in records}
    if len(identities) != 1:
        raise FixLifecycleError("consolidation de fonctions ou versions différentes refusée")
    cases = sorted({case for record in records for case in record["linked_cases"]})
    validated_cases = {
        row["case_id"] for record in records for event in record["history"]
        if event["to"] == VALIDATED for row in event["evidence"]["case_checks"]
    }
    if (reason == "multiple_cases" and len(validated_cases) < 2) or (
        reason in ("multiple_fixes", "overlapping_guards") and len(records) < 2
    ):
        raise FixLifecycleError("motif non étayé par le nombre de cases ou de fixes")
    note_ref = _file_ref(rule_review_path, require_text=True)
    core_function_id, core_hash = next(iter(identities))
    pairs = [f"{a['fix']['fix_id']}::{b['fix']['fix_id']}" for i, a in enumerate(records) for b in records[i + 1:]]
    if compatibility is not None and not isinstance(compatibility, dict):
        raise FixLifecycleError("compatibilité invalide")
    supplied = compatibility or {}
    if set(supplied) - set(pairs):
        raise FixLifecycleError("paire de compatibilité inconnue")
    relations = {pair: supplied.get(pair, "UNKNOWN") for pair in pairs}
    if any(value not in ("DISJOINT", "CONFLICT", "UNKNOWN") for value in relations.values()):
        raise FixLifecycleError("statut de compatibilité invalide")
    guards = {record["fix"]["fix_id"]: record["fix"]["guard_sha256"] for record in records}
    if any(value == "DISJOINT" and guards[pair.split("::")[0]] == guards[pair.split("::")[1]]
           for pair, value in relations.items()):
        raise FixLifecycleError("gardes identiques déclarées disjointes")
    blockers = []
    if any(record["state"] not in (VALIDATED, STABLE, EVOLUTION_CANDIDATE) for record in records):
        blockers.append("fix_not_validated")
    if any(value != "DISJOINT" for value in relations.values()):
        blockers.append("compatibility_unresolved")
    if any(case not in validated_cases for case in cases):
        blockers.append("new_incident_not_validated")
    if reason == "activations" and not any(
        event["to"] == STABLE and event["evidence"].get("activation_count", 0) > 0
        for record in records for event in record["history"]
    ):
        blockers.append("activation_evidence_missing")
    candidate_key = json.dumps({
        "core_function_id": core_function_id, "core_hash": core_hash,
        "reason": reason, "rule_review_sha256": note_ref["sha256"],
        "compatibility": relations,
        "lifecycle_sha256": [_digest(record) for record in records],
    }, sort_keys=True, ensure_ascii=False)
    candidate_id = hashlib.sha256(candidate_key.encode()).hexdigest()[:20]
    path = Path(out_root) / candidate_id / "candidate.json"
    result = {
        "schema_version": SCHEMA_VERSION, "candidate_id": candidate_id,
        "created_at": _now(), "core_function_id": core_function_id, "core_hash": core_hash,
        "fix_ids": sorted(fix_ids), "case_ids": cases, "reason": reason,
        "rule_review": note_ref, "compatibility": relations,
        "fixes": [{"identity": record["fix"], "state": record["state"],
                   "source_lifecycle_sha256": _digest(record),
                   "lifecycle_path": str(_record_path(lifecycle_root, record["fix"]["fix_id"]).resolve()),
                   "linked_cases": record["linked_cases"],
                   "usage": [event["evidence"] for event in record["history"] if event["to"] == OBSERVED],
                   "validations": [event["evidence"] for event in record["history"] if event["to"] == VALIDATED],
                   "stability": [event["evidence"] for event in record["history"] if event["to"] == STABLE],
                   "core_change_signals": record["signals"],
                   "root_area": record["root_area"],
                   "failure_mechanism": record["failure_mechanism"]} for record in records],
        "review_status": "BLOCKED" if blockers else "READY_FOR_HUMAN_ANALYSIS",
        "blockers": blockers,
        "limits": ["phase11b_regression_suite_unavailable", "no_core_patch_or_adoption_performed"],
    }
    if path.exists():
        old, _ = _read(path)
        if {key: value for key, value in old.items() if key != "created_at"} == {
            key: value for key, value in result.items() if key != "created_at"
        }:
            return path
        raise FixLifecycleError("dossier candidat existant avec un contenu différent")
    return _write(path, result)
