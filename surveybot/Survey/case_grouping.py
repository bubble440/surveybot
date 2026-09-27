from __future__ import annotations

"""Déduplication de failure_cases diagnostiqués (Phase 4) qui partagent un
signal de diagnostic IDENTIQUE, mais ne sont pas encore engagés en Phase 5 —
nouveau mécanisme ADDITIF, hors du chantier 1A-17 numéroté par
Utils/SURVEYBOT_AUTOFIX_PLAN.md.

Ne touche à AUCUN fichier existant du pipeline (Survey/failure_case_builder.py,
Survey/failure_diagnosis.py, Survey/context_selector.py, etc.) : ce module lit
uniquement des artefacts déjà produits par les Phases 2 et 4
(failure_cases/<id>/manifest.json, diagnoses/<id>/diagnosis.json) et
context_selections/ (Phase 5, pour savoir quels cases sont déjà engagés), puis
écrit un failure_case + diagnosis.json SYNTHÉTIQUES — une copie exacte du case
représentatif choisi, sous un identifiant dérivé (group_id) — que les Phases 5
à 16 consomment ensuite exactement comme un case normal, SANS AUCUNE
MODIFICATION de leur code.

Principe "1 bug = 1 stratégie" : ce module ne regroupe JAMAIS des bugs
différents dans un même patch. Il reconnaît seulement que plusieurs cases
remontés séparément SONT le même bug déjà identifié, au sens le plus strict
possible (voir critère ci-dessous), pour éviter de le corriger N fois.

── Format vérifié avant d'écrire la logique de signature (consigne) ──────────
diagnosis.json (Phase 4, Survey/failure_diagnosis.py::DiagnosisResult.as_dict) :
  - "modules_likely_involved" : liste de {"module": <chemin fichier str>,
    "matched_signals": [<str>, ...], "memory_entries": [...]} — UN ÉLÉMENT PAR
    MODULE trouvé (Survey/failure_diagnosis.py::_modules_likely_involved,
    dict "by_module"), jamais un élément par paire module/signal.
  - "cause": {"level": "certain"|"probable"|"plausible", "justification": str}
    (Survey.failure_diagnosis.LEVEL_CERTAIN/LEVEL_PROBABLE/LEVEL_PLAUSIBLE).
  - "case_id" == le nom du dossier diagnoses/<case_id>/ (Survey/
    failure_diagnosis.py::write_diagnosis, out_dir = out_root / case_dir.name
    — jamais un second préfixe "case_").
manifest.json (Phase 2, Survey/failure_case_builder.py::build_failure_case) :
  "case_id" == snapshot_dir.name == le nom du dossier failure_cases/<case_id>/
  (case_dir = out_root / f"{case_id}", jamais préfixé non plus).

── Vérification faite avant de choisir extension directe vs sidecar (consigne) ─
Aucun consommateur existant (Phases 5 à 16 : grep fait sur Survey/ avant ce
patch, aucun "set(...) == {...}" ni rejet sur clé inconnue trouvé hors
Survey/action_dispatcher.py — payloads de dispatch, sans rapport) ne valide le
schéma de diagnosis.json/manifest.json de façon stricte : tous les accès
observés passent par .get(clé). Ajouter un champ aux copies synthétiques
serait donc, en théorie, sans risque pour tout consommateur existant. Décision
retenue malgré cela : AUCUN champ n'est ajouté aux copies manifest.json/
diagnosis.json au-delà du remplacement de "case_id" par group_id — remplacement
qui n'est PAS une extension mais une correction de cohérence déjà EXIGÉE par un
consommateur existant (Survey/autofix_worktree.py::check_eligibility, Phase 7,
compare "case_id" entre manifest.json, diagnosis.json et les noms de dossiers
et refuse toute incohérence). La seule source de traçabilité du regroupement
(membres, représentant, signature) est le sidecar group_members.json, jamais
dupliquée dans un artefact que les phases existantes consomment.

── Portée (candidats) ─────────────────────────────────────────────────────────
Un case candidat = diagnoses/<id>/diagnosis.json existe (lisible, objet JSON)
ET context_selections/<id>/context_selection.json N'EXISTE PAS (Phase 5 non
encore engagée pour ce case précis) — un case déjà engagé individuellement
n'est jamais regroupé rétroactivement (consigne). Un dossier diagnoses/<id>/
dont le nom porte déjà GROUP_ID_PREFIX (un groupe formé par une invocation
précédente de ce même module) n'est jamais lui-même reconsidéré comme candidat
— pas de groupe de groupes.

── Critère de regroupement (le plus strict possible) ─────────────────────────
Signature d'un case = ensemble de (module, frozenset(matched_signals)) — un
élément par entrée de modules_likely_involved. Deux cases partagent un groupe
si et seulement si leurs ensembles de signatures s'intersectent (identité
stricte module + ensemble de matched_signals, jamais un recouvrement partiel).
Un case dont les signatures le rattacheraient à PLUSIEURS signatures partagées
DIFFÉRENTES (donc plusieurs groupes candidats distincts) est une ambiguïté :
il n'est JAMAIS assigné arbitrairement à l'un d'eux, il est retiré de TOUT
regroupement automatique (y compris des groupes où il aurait pu être le seul
point de recouvrement), laissé solo, et signalé explicitement. Ce retrait est
calculé en un seul passage à partir des ensembles de signatures bruts (jamais
recalculé après coup en fonction des exclusions déjà faites) : un case ne peut
donc jamais, par construction, se retrouver dans deux groupes finaux
différents, ni contaminer un groupe dont il a été retiré.

── Représentant (déterministe) ────────────────────────────────────────────────
cause.level="certain" préféré à "probable"/"plausible" ; à égalité, le case_id
le plus ancien. Les case_id de ce pipeline sont des horodatages
"YYYYMMDD_HHMMSS_..." (cf. Survey/failure_case_builder.py::build_failure_case,
case_id = snapshot_dir.name) : l'ordre lexicographique croissant est donc déjà
l'ordre chronologique, comme déjà exploité tel quel par
Survey/autofix_metrics.py::list_case_ids (sorted(...iterdir())) — pas de
parsing de date séparé ici non plus.

── group_id ───────────────────────────────────────────────────────────────────
Dérivé UNIQUEMENT de la signature partagée (module + matched_signals triés),
JAMAIS du case_id du représentant, pour que le même bug reconnu plus tard
(nouveaux cases, même signature) reproduise le même group_id : sha256 tronqué
à 16 caractères hex (même longueur d'encodage que le callback_data de la
Phase 13, Survey/human_review.py) préfixé "dupgroup_" — jamais confondu avec
un vrai case_id (toujours "YYYYMMDD_HHMMSS_..."), et déjà conforme sans
transformation supplémentaire à l'allowlist de composant de chemin/ref Git de
la Phase 7 (Survey/autofix_worktree.py::_CASE_ID_RE, alphanumérique + . _ -).

── Un seul passage (RÈGLES STRICTES) ─────────────────────────────────────────
Un groupe déjà entièrement formé sur disque (failure_cases/<group_id>/
manifest.json + diagnoses/<group_id>/diagnosis.json + group_members.json, les
trois présents) n'est JAMAIS régénéré ni étendu avec de nouveaux membres par
une invocation ultérieure — gelé à sa création. Un case candidat qui
matcherait la signature d'un groupe déjà gelé est donc rapporté par
compute_case_groups() comme un groupe "reformé" identique (même group_id,
mêmes membres déterministes recalculés), mais write_case_groups() ne le
réécrit pas si déjà complet sur disque — extension incrémentale d'un groupe
déjà formé avec un nouveau membre apparu depuis : hors périmètre de ce patch,
sous-chantier différé, documenté plutôt que masqué. Un état PARTIELLEMENT
formé (un des trois artefacts manquant, ex. run précédent interrompu) n'est
jamais complété silencieusement ni traité comme neuf : signalé explicitement,
reconstruction refusée, vérification manuelle requise.
"""

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from Survey.log_utils import log_debug, log_info

_TAG = "[CASE_GROUPING]"
SCHEMA_VERSION = "1.0"

GROUP_ID_PREFIX = "dupgroup_"

LEVEL_CERTAIN = "certain"
LEVEL_PROBABLE = "probable"
LEVEL_PLAUSIBLE = "plausible"
_CAUSE_LEVEL_RANK = {LEVEL_CERTAIN: 0, LEVEL_PROBABLE: 1, LEVEL_PLAUSIBLE: 2}

# Signature = (module, frozenset(matched_signals))
Signature = "tuple[str, frozenset]"


class CaseGroupingError(Exception):
    """Véritable erreur d'usage (racine failure_cases/diagnoses introuvable ou
    n'est pas un dossier) — jamais levée pour un case précis dont un artefact
    est absent/invalide, qui est simplement exclu et signalé en avertissement."""


def _load_json(path: Path) -> "tuple[Any, Optional[str]]":
    if not path.is_file():
        return None, f"{path} absent"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except OSError as exc:
        return None, f"{path} illisible ({exc})"
    except ValueError as exc:  # json.JSONDecodeError est une sous-classe de ValueError
        return None, f"{path} JSON invalide ({exc})"


def _signature_set(diagnosis: dict) -> "set":
    """Ensemble des signatures (module, frozenset(matched_signals)) portées par
    diagnosis["modules_likely_involved"]. Une entrée sans module non vide ou
    sans matched_signals non vide ne porte aucun signal exploitable pour le
    regroupement — ignorée plutôt que devinée."""
    out: "set" = set()
    modules = diagnosis.get("modules_likely_involved")
    if not isinstance(modules, list):
        return out
    for entry in modules:
        if not isinstance(entry, dict):
            continue
        module = str(entry.get("module") or "").strip()
        signals = entry.get("matched_signals")
        if not module or not isinstance(signals, list):
            continue
        clean_signals = frozenset(str(s).strip() for s in signals if str(s).strip())
        if not clean_signals:
            continue
        out.add((module, clean_signals))
    return out


def _cause_level(diagnosis: dict) -> str:
    cause = diagnosis.get("cause")
    level = cause.get("level") if isinstance(cause, dict) else None
    return level if level in _CAUSE_LEVEL_RANK else LEVEL_PLAUSIBLE


def _group_id_for_signature(signature) -> str:
    module, signals = signature
    payload = module + "\n" + "\n".join(sorted(signals))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"{GROUP_ID_PREFIX}{digest}"


def _timestamped_output_dir(out_root: Path) -> Path:
    """Un instantané par run, jamais un fichier unique écrasé — même politique
    que Survey/autofix_metrics.py::_timestamped_output_dir (dupliquée ici à
    dessein, module indépendant, plutôt que de dépendre d'un détail interne
    d'un autre module)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    candidate = out_root / stamp
    suffix = 1
    while candidate.exists():
        candidate = out_root / f"{stamp}_{suffix}"
        suffix += 1
    return candidate


def list_candidate_case_ids(
    diagnoses_root: Path,
    context_selections_root: Path,
    *,
    warnings: list,
) -> "list[str]":
    """Cases diagnostiqués (Phase 4) mais pas encore engagés en Phase 5 — seule
    portée de ce module (cf. docstring, section Portée)."""
    if not diagnoses_root.is_dir():
        raise CaseGroupingError(f"racine diagnoses introuvable ou n'est pas un dossier : {diagnoses_root}")
    out: "list[str]" = []
    for entry in sorted(diagnoses_root.iterdir()):
        if not entry.is_dir():
            warnings.append(f"{entry} : entrée non-dossier sous diagnoses/, ignorée")
            continue
        if entry.name.startswith(GROUP_ID_PREFIX):
            continue  # groupe déjà formé — jamais un groupe de groupes
        if not (entry / "diagnosis.json").is_file():
            continue
        if (context_selections_root / entry.name / "context_selection.json").is_file():
            continue  # déjà engagé individuellement en Phase 5 — jamais regroupé rétroactivement
        out.append(entry.name)
    return out


@dataclass
class CaseGroup:
    group_id: str
    module: str
    matched_signals: "list[str]"
    members: "list[str]"
    representative_case_id: str

    def signature_dict(self) -> dict:
        return {"module": self.module, "matched_signals": self.matched_signals}


@dataclass
class AmbiguousCase:
    case_id: str
    conflicting_signatures: "list[dict]"


@dataclass
class GroupingResult:
    candidates_considered: int
    groups: "list[CaseGroup]" = field(default_factory=list)
    solo_case_ids: "list[str]" = field(default_factory=list)
    ambiguous_cases: "list[AmbiguousCase]" = field(default_factory=list)
    warnings: "list[str]" = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "candidates_considered": self.candidates_considered,
            "groups": [
                {
                    "group_id": g.group_id,
                    "signature": g.signature_dict(),
                    "representative_case_id": g.representative_case_id,
                    "members": g.members,
                }
                for g in self.groups
            ],
            "solo_case_ids": self.solo_case_ids,
            "ambiguous_cases": [
                {"case_id": a.case_id, "conflicting_signatures": a.conflicting_signatures}
                for a in self.ambiguous_cases
            ],
            "warnings": self.warnings,
        }


def compute_case_groups(
    *,
    diagnoses_root: "str | Path" = "diagnoses",
    context_selections_root: "str | Path" = "context_selections",
) -> GroupingResult:
    """Calcule le regroupement — lecture seule, n'écrit jamais rien. Voir le
    docstring du module pour le critère exact et la gestion des ambiguïtés."""
    diagnoses_root = Path(diagnoses_root)
    context_selections_root = Path(context_selections_root)
    warnings: "list[str]" = []

    candidate_ids = list_candidate_case_ids(diagnoses_root, context_selections_root, warnings=warnings)
    log_debug(_TAG, f"{len(candidate_ids)} case(s) candidat(s) sous {diagnoses_root}")

    diagnoses_by_case: "dict[str, dict]" = {}
    case_signatures: "dict[str, set]" = {}
    for case_id in candidate_ids:
        diagnosis, err = _load_json(diagnoses_root / case_id / "diagnosis.json")
        if err or not isinstance(diagnosis, dict):
            warnings.append(f"{case_id}: diagnosis.json {err or 'invalide'} — exclu du regroupement")
            continue
        diagnoses_by_case[case_id] = diagnosis
        case_signatures[case_id] = _signature_set(diagnosis)

    sig_to_cases: "dict[Any, set]" = {}
    for case_id, sigs in case_signatures.items():
        for sig in sigs:
            sig_to_cases.setdefault(sig, set()).add(case_id)

    # Seules les signatures réellement partagées par >= 2 cases candidats
    # définissent un groupe potentiel — une signature portée par un seul case
    # ne concerne que ce case (déjà couvert par le cas "groupe de taille 1").
    shared_sigs = {sig for sig, cases in sig_to_cases.items() if len(cases) >= 2}

    def _sig_sort_key(sig) -> tuple:
        return (sig[0], sorted(sig[1]))

    # Ambiguïté : calculée une seule fois à partir des ensembles bruts
    # ci-dessus, jamais recalculée après avoir retiré d'autres cases — un case
    # rattaché (par intersection) à >= 2 signatures partagées DIFFÉRENTES est
    # exclu de TOUT regroupement automatique, pas seulement du second.
    ambiguous: "dict[str, list]" = {}
    for case_id, sigs in case_signatures.items():
        matched_shared = sorted((s for s in sigs if s in shared_sigs), key=_sig_sort_key)
        if len(matched_shared) >= 2:
            ambiguous[case_id] = matched_shared

    eligible_ids = set(diagnoses_by_case) - set(ambiguous)

    groups: "list[CaseGroup]" = []
    grouped_case_ids: "set[str]" = set()
    for sig in sorted(shared_sigs, key=_sig_sort_key):
        members = sorted(c for c in sig_to_cases[sig] if c in eligible_ids)
        if len(members) < 2:
            continue  # ambiguïté(s) retirée(s) sous le seuil de 2 — reste solo, pas un groupe
        representative = min(
            members,
            key=lambda cid: (_CAUSE_LEVEL_RANK[_cause_level(diagnoses_by_case[cid])], cid),
        )
        module, signals = sig
        groups.append(CaseGroup(
            group_id=_group_id_for_signature(sig),
            module=module,
            matched_signals=sorted(signals),
            members=members,
            representative_case_id=representative,
        ))
        grouped_case_ids.update(members)

    solo_ids = sorted(
        cid for cid in diagnoses_by_case
        if cid not in grouped_case_ids and cid not in ambiguous
    )

    ambiguous_cases = [
        AmbiguousCase(
            case_id=cid,
            conflicting_signatures=[{"module": m, "matched_signals": sorted(s)} for m, s in sigs],
        )
        for cid, sigs in sorted(ambiguous.items())
    ]
    if ambiguous_cases:
        warnings.append(
            f"{len(ambiguous_cases)} case(s) exclu(s) du regroupement automatique pour ambiguïté "
            "(rattachés à plusieurs groupes différents par des signatures distinctes) — laissés "
            f"solo, jamais assignés arbitrairement : {[a.case_id for a in ambiguous_cases]}"
        )

    return GroupingResult(
        candidates_considered=len(candidate_ids),
        groups=groups,
        solo_case_ids=solo_ids,
        ambiguous_cases=ambiguous_cases,
        warnings=warnings,
    )


def _group_artifacts_complete(*, failure_cases_root: Path, diagnoses_root: Path, group_id: str) -> bool:
    return (
        (failure_cases_root / group_id / "manifest.json").is_file()
        and (diagnoses_root / group_id / "diagnosis.json").is_file()
        and (failure_cases_root / group_id / "group_members.json").is_file()
    )


def _group_artifacts_absent(*, failure_cases_root: Path, diagnoses_root: Path, group_id: str) -> bool:
    return (
        not (failure_cases_root / group_id).exists()
        and not (diagnoses_root / group_id).exists()
    )


def _build_group_artifacts(
    group: CaseGroup,
    *,
    failure_cases_root: Path,
    diagnoses_root: Path,
) -> "Optional[str]":
    """Construit failure_cases/<group_id>/ (manifest.json + artifacts/ copiés
    intégralement depuis le case représentatif, case_id remplacé par group_id)
    et diagnoses/<group_id>/diagnosis.json (même principe), puis
    group_members.json. Appelée seulement quand les deux dossiers cibles sont
    confirmés absents par l'appelant (cf. _group_artifacts_absent) — jamais un
    état partiel complété silencieusement. Retourne None si ok, sinon la
    raison de l'échec ; nettoie tout ce qu'elle a créé avant de retourner un
    échec (un groupe partiellement écrit est pire qu'aucun groupe, même
    principe que Survey/failure_case_builder.py::build_failure_case)."""
    fc_src_dir = failure_cases_root / group.representative_case_id
    manifest, err = _load_json(fc_src_dir / "manifest.json")
    if err or not isinstance(manifest, dict):
        return f"manifest.json du représentant {group.representative_case_id!r} {err or 'invalide'}"

    diag_src_file = diagnoses_root / group.representative_case_id / "diagnosis.json"
    diagnosis, err = _load_json(diag_src_file)
    if err or not isinstance(diagnosis, dict):
        return f"diagnosis.json du représentant {group.representative_case_id!r} {err or 'invalide'}"

    fc_dst_dir = failure_cases_root / group.group_id
    diag_dst_dir = diagnoses_root / group.group_id

    try:
        shutil.copytree(fc_src_dir, fc_dst_dir)

        synthetic_manifest = dict(manifest)
        synthetic_manifest["case_id"] = group.group_id
        (fc_dst_dir / "manifest.json").write_text(
            json.dumps(synthetic_manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        synthetic_diagnosis = dict(diagnosis)
        synthetic_diagnosis["case_id"] = group.group_id
        diag_dst_dir.mkdir(parents=True, exist_ok=False)
        (diag_dst_dir / "diagnosis.json").write_text(
            json.dumps(synthetic_diagnosis, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        members_payload = {
            "schema_version": SCHEMA_VERSION,
            "group_id": group.group_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "signature": group.signature_dict(),
            "representative_case_id": group.representative_case_id,
            "members": group.members,
        }
        (fc_dst_dir / "group_members.json").write_text(
            json.dumps(members_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        shutil.rmtree(fc_dst_dir, ignore_errors=True)
        shutil.rmtree(diag_dst_dir, ignore_errors=True)
        return f"écriture échouée ({exc})"

    return None


def write_case_groups(
    *,
    diagnoses_root: "str | Path" = "diagnoses",
    failure_cases_root: "str | Path" = "failure_cases",
    context_selections_root: "str | Path" = "context_selections",
    report_root: "str | Path" = "case_groupings",
) -> "tuple[GroupingResult, Path]":
    """Calcule le regroupement (compute_case_groups) puis matérialise chaque
    groupe de taille >= 2 sous failure_cases/<group_id>/ + diagnoses/<group_id>/
    + group_members.json, avant d'écrire un instantané horodaté du rapport
    sous report_root/<horodatage>/grouping_report.json (même convention que
    Survey/autofix_metrics.py : jamais un fichier unique écrasé).

    Un groupe déjà complet sur disque (les trois artefacts présents) n'est
    jamais régénéré — gelé, cf. docstring du module ("Un seul passage"). Un
    état partiel (un des trois artefacts manquant) n'est ni complété ni
    ignoré : signalé explicitement dans le rapport, reconstruction refusée.
    """
    diagnoses_root = Path(diagnoses_root)
    failure_cases_root = Path(failure_cases_root)
    context_selections_root = Path(context_selections_root)

    if not failure_cases_root.is_dir():
        raise CaseGroupingError(
            f"racine failure_cases introuvable ou n'est pas un dossier : {failure_cases_root}"
        )

    result = compute_case_groups(
        diagnoses_root=diagnoses_root,
        context_selections_root=context_selections_root,
    )

    for group in result.groups:
        complete = _group_artifacts_complete(
            failure_cases_root=failure_cases_root, diagnoses_root=diagnoses_root, group_id=group.group_id,
        )
        if complete:
            log_debug(_TAG, f"groupe {group.group_id} déjà formé — gelé, jamais régénéré")
            continue

        absent = _group_artifacts_absent(
            failure_cases_root=failure_cases_root, diagnoses_root=diagnoses_root, group_id=group.group_id,
        )
        if not absent:
            result.warnings.append(
                f"groupe {group.group_id}: état partiel détecté sur disque (ni complet ni absent) — "
                "reconstruction refusée, vérification manuelle requise plutôt qu'une complétion "
                "silencieuse ou un skip qui masquerait le problème"
            )
            continue

        reason = _build_group_artifacts(
            group, failure_cases_root=failure_cases_root, diagnoses_root=diagnoses_root,
        )
        if reason:
            result.warnings.append(f"groupe {group.group_id}: échec de création — {reason}")
            continue

        log_info(
            _TAG,
            f"groupe formé group_id={group.group_id} representant={group.representative_case_id} "
            f"membres={len(group.members)} -> {failure_cases_root / group.group_id}",
        )

    report_root = Path(report_root)
    report_root.mkdir(parents=True, exist_ok=True)
    out_dir = _timestamped_output_dir(report_root)
    out_dir.mkdir(parents=True, exist_ok=False)
    out_file = out_dir / "grouping_report.json"
    out_file.write_text(
        json.dumps(result.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    log_info(
        _TAG,
        f"rapport écrit candidats={result.candidates_considered} groupes={len(result.groups)} "
        f"solo={len(result.solo_case_ids)} ambigus={len(result.ambiguous_cases)} -> {out_file}",
    )
    return result, out_file
