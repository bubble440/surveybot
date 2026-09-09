from __future__ import annotations

"""Transformation d'un snapshot d'observabilité (Phase 1A/1B) en case normalisé.

Lecture seule sur le pipeline bot : ce module ne touche à aucun extracteur, aucun
validator, aucun dispatcher, et ne modifie ni ne déplace jamais le snapshot source.
Il se contente de lire les artefacts déjà produits par failure_recorder.py /
page_snapshot.py (meta.json, validation_report.json, question_blocks.json,
actions_requested.json, DOM/screenshot) et d'en extraire, sans réinterprétation ni
diagnostic, les quelques champs qu'une phase ultérieure (sélection de contexte,
prompt Codex, replay) pourra exploiter directement.

Règle de résolution target_id (dans cet ordre, on s'arrête au premier qui
aboutit) :
  1. Le premier issue de validation_report.json qui porte un target_id (ce champ
     est déjà recopié tel quel par les validators depuis l'action/le bloc fautif —
     on ne fait que le relire).
  2. Un target_id commun à toutes les actions de actions_requested.json (cas
     fréquent : plusieurs actions checkbox distinctes sur le même groupe).
  3. Un target_id commun à tous les blocs de question_blocks.json.
Si aucune de ces sources ne désigne un target_id non ambigu, target_id reste à
null plutôt que d'être deviné.

Règle de résolution itype (indépendante de target_id : un issue "missing_target_id"
/ "action_missing_target_id" porte un itype exploitable sans jamais porter de
target_id — c'est précisément l'échec constaté) : itype du même issue que celui
ayant fourni target_id (ou celui d'actions_requested.json / question_blocks.json
pour ce target_id, ex. "max_select_exceeds_options" qui ne porte pas itype), sinon
premier issue de validation_report.json qui porte un itype, sinon celui déjà trouvé
via le fallback target_id commun (actions_requested.json puis question_blocks.json).

frame_chain : lu depuis meta.json (best_frame.chain, résolu par la même logique
que analyze_dom au moment de la capture) puis, à défaut, depuis un champ
frame_chain porté directement par le bloc résolu de question_blocks.json.

provider_domain : hostname extrait de meta.json["url"] (urlparse), sans mapping
vers un nom de plateforme — pas d'heuristique.

Secrets : meta.json est le seul fichier connu du snapshot à porter des URLs de
survey, potentiellement assorties d'un token de session en query string (observé
en pratique : JWT Zappi, WID/XID CloudResearch). La copie de meta.json dans le
case retire donc la query string et le fragment de tout champ "url" (top-level,
frames[].url, best_frame.url) avant écriture — sinon meta.json n'est pas copié.
Les autres artefacts (JSON/HTML/PNG) sont copiés tels quels : ils ne portent pas
d'URL dans leur schéma connu, et leur contenu DOM n'est pas réinterprété ici.
"""

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from Survey.log_utils import log_debug, log_info

SCHEMA_VERSION = "1.0"

# Fichiers connus produits par page_snapshot.py / failure_recorder.py. Couvre les
# deux générations de nommage observées en pratique (profil "action_validation"
# récent : post_action_dom.html / post_action_viewport.png / pre_action_dom.html ;
# profil historique complet : dom_outer.html / dom_body.html / page_source.html /
# viewport.png / page.mhtml). Seuls ces noms sont copiés — pas de copie générique
# du dossier snapshot, précisément pour ne jamais embarquer un fichier imprévu.
_KNOWN_FILES = (
    "meta.json",
    "validation_report.json",
    "question_blocks.json",
    "actions_requested.json",
    "pre_action_dom.html",
    "post_action_dom.html",
    "post_action_viewport.png",
    "dom_outer.html",
    "dom_body.html",
    "page_source.html",
    "viewport.png",
    "page.mhtml",
    "body_text.txt",
    "mhtml_error.txt",
)

# Noms générés par page_snapshot._dump_frames_best_effort : frame_<chain>.dom_outer.html
# et frame_<chain>.page_source.html. Tout fichier de frames/ qui ne matche pas ce
# pattern est ignoré (et signalé) plutôt que copié aveuglément.
_FRAME_FILE_SUFFIXES = (".dom_outer.html", ".page_source.html")


class FailureCaseError(Exception):
    """Échec contrôlé de construction d'un case."""


class FailureCaseExistsError(FailureCaseError):
    """Le dossier de case cible existe déjà et la régénération n'a pas été demandée."""


def _clean_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _load_json(path: Path, warnings: list[str]) -> tuple[Any, bool]:
    """Retourne (donnee, ok). ok=False si fichier manquant/illisible/JSON invalide.

    L'absence du fichier n'est jamais un warning en soi (plusieurs artefacts sont
    optionnels selon le stage) : seule une lecture/décodage en échec l'est.
    """
    if not path.is_file():
        return None, False
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        warnings.append(f"{path.name}: lecture impossible ({exc})")
        log_debug("[FAILURE_CASE]", f"read error {path}: {exc}")
        return None, False
    try:
        return json.loads(raw), True
    except json.JSONDecodeError as exc:
        warnings.append(f"{path.name}: JSON invalide ({exc})")
        log_debug("[FAILURE_CASE]", f"invalid json {path}: {exc}")
        return None, False


def _strip_url(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    try:
        parsed = urlparse(value)
    except ValueError:
        return value
    if not parsed.query and not parsed.fragment:
        return value
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def _sanitize_meta(meta: dict) -> tuple[dict, bool]:
    """Retire query string/fragment de tout champ "url" avant copie dans le case."""
    stripped = False
    out = json.loads(json.dumps(meta))  # copie profonde indépendante de l'original

    def _walk(node: Any) -> Any:
        nonlocal stripped
        if isinstance(node, dict):
            for key, value in list(node.items()):
                if key == "url" and isinstance(value, str):
                    new_value = _strip_url(value)
                    if new_value != value:
                        stripped = True
                    node[key] = new_value
                else:
                    node[key] = _walk(value)
            return node
        if isinstance(node, list):
            return [_walk(item) for item in node]
        return node

    return _walk(out), stripped


def _common_target_itype(entries: Any) -> tuple[str | None, str | None]:
    """target_id (+ itype si univoque) partagé par toutes les entrées de la liste."""
    if not isinstance(entries, list):
        return None, None
    dicts = [e for e in entries if isinstance(e, dict)]
    target_ids = {tid for e in dicts if (tid := _clean_str(e.get("target_id")))}
    if len(target_ids) != 1:
        return None, None
    target_id = next(iter(target_ids))
    itypes = {
        itype
        for e in dicts
        if _clean_str(e.get("target_id")) == target_id and (itype := _clean_str(e.get("itype")))
    }
    itype = next(iter(itypes)) if len(itypes) == 1 else None
    return target_id, itype


def _itype_for_target(target_id: str, *sources: Any) -> str | None:
    for entries in sources:
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and _clean_str(entry.get("target_id")) == target_id:
                itype = _clean_str(entry.get("itype"))
                if itype:
                    return itype
    return None


def _resolve_itype_target_id(
    report: Any, question_blocks: Any, actions: Any
) -> tuple[str | None, str | None]:
    # target_id et itype sont résolus séparément : un issue de type
    # "missing_target_id" / "action_missing_target_id" porte un itype exploitable
    # sans jamais porter de target_id (c'est précisément l'échec constaté), donc
    # coupler les deux à la même condition perdrait l'itype dans ce cas.
    issues = [i for i in (report.get("issues") if isinstance(report, dict) else None) or [] if isinstance(i, dict)]

    target_id: str | None = None
    itype: str | None = None

    for issue in issues:
        tid = _clean_str(issue.get("target_id"))
        if tid:
            target_id = tid
            itype = _clean_str(issue.get("itype")) or _itype_for_target(
                tid, actions, question_blocks
            ) or None
            break

    if not target_id:
        target_id, itype = _common_target_itype(actions)
        if not target_id:
            target_id, itype = _common_target_itype(question_blocks)

    if not itype:
        for issue in issues:
            candidate = _clean_str(issue.get("itype"))
            if candidate:
                itype = candidate
                break

    return itype, target_id


def _resolve_frame_chain(meta: Any, question_blocks: Any, target_id: str | None) -> list | None:
    if isinstance(meta, dict):
        best_frame = meta.get("best_frame")
        if isinstance(best_frame, dict):
            chain = best_frame.get("chain")
            if isinstance(chain, list):
                return chain

    if isinstance(question_blocks, list):
        candidates = question_blocks
        if target_id:
            candidates = [
                b for b in candidates
                if isinstance(b, dict) and _clean_str(b.get("target_id")) == target_id
            ]
        for block in candidates:
            if not isinstance(block, dict):
                continue
            chain = block.get("frame_chain")
            if chain is None and isinstance(block.get("context"), dict):
                chain = block["context"].get("frame_chain")
            if isinstance(chain, list):
                return chain

    return None


def _resolve_provider_domain(meta: Any) -> str | None:
    if not isinstance(meta, dict):
        return None
    url = meta.get("url")
    if not isinstance(url, str) or not url:
        return None
    try:
        return urlparse(url).hostname or None
    except ValueError:
        return None


def _failure_types(report: Any) -> list[str]:
    if not isinstance(report, dict):
        return []
    issues = report.get("issues")
    if not isinstance(issues, list):
        return []
    seen: list[str] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        failure_type = _clean_str(issue.get("failure_type"))
        if failure_type and failure_type not in seen:
            seen.append(failure_type)
    return seen


def _resolve_stage(report: Any, snapshot_dir: Path) -> str:
    if isinstance(report, dict):
        stage = _clean_str(report.get("stage")).lower()
        if stage in ("action", "extraction"):
            return stage
    name = snapshot_dir.name.lower()
    # Ordre important : "extraction_validation_failure" contient lui-même le
    # sous-texte "action_validation_failure" (ex-TRACTION), donc le test le plus
    # spécifique doit passer en premier sous peine de classer toute extraction
    # comme action.
    if "extraction_validation_failure" in name:
        return "extraction"
    if "action_validation_failure" in name:
        return "action"
    return "unknown"


def _copy_known_files(
    snapshot_dir: Path,
    artifacts_dir: Path,
    *,
    meta: Any,
    meta_ok: bool,
    warnings: list[str],
) -> dict[str, bool]:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    presence: dict[str, bool] = {}

    for name in _KNOWN_FILES:
        src = snapshot_dir / name
        if not src.is_file():
            presence[name] = False
            continue

        if name == "meta.json":
            if meta_ok and isinstance(meta, dict):
                sanitized, stripped = _sanitize_meta(meta)
                (artifacts_dir / name).write_text(
                    json.dumps(sanitized, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                if stripped:
                    warnings.append(
                        "meta.json: query string retirée d'un ou plusieurs champs "
                        "'url' avant copie (token de session potentiel)"
                    )
                presence[name] = True
            else:
                warnings.append(
                    "meta.json: présent mais illisible/JSON invalide — non copié "
                    "(contenu non vérifiable, risque de token non filtré)"
                )
                presence[name] = False
            continue

        try:
            shutil.copy2(src, artifacts_dir / name)
            presence[name] = True
        except OSError as exc:
            warnings.append(f"{name}: copie échouée ({exc})")
            log_debug("[FAILURE_CASE]", f"copy failed {src}: {exc}")
            presence[name] = False

    frames_src = snapshot_dir / "frames"
    frame_copied = False
    if frames_src.is_dir():
        for item in frames_src.iterdir():
            if not item.is_file():
                continue
            if not item.name.endswith(_FRAME_FILE_SUFFIXES):
                warnings.append(f"frames/{item.name}: nom inattendu, ignoré")
                continue
            try:
                (artifacts_dir / "frames").mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, artifacts_dir / "frames" / item.name)
                frame_copied = True
            except OSError as exc:
                warnings.append(f"frames/{item.name}: copie échouée ({exc})")
                log_debug("[FAILURE_CASE]", f"copy failed {item}: {exc}")
    presence["frames"] = frame_copied

    return presence


def build_failure_case(
    snapshot_dir: str | Path,
    *,
    out_root: str | Path = "failure_cases",
    force: bool = False,
) -> Path:
    """Convertit un snapshot brut en case normalisé sous out_root/case_<id>/manifest.json.

    Ne modifie ni ne déplace jamais snapshot_dir (lecture seule). Lève
    FailureCaseExistsError si le case cible existe déjà et force=False — jamais
    d'écrasement silencieux.
    """
    snapshot_dir = Path(snapshot_dir)
    if not snapshot_dir.is_dir():
        raise FailureCaseError(f"snapshot introuvable ou n'est pas un dossier : {snapshot_dir}")

    case_id = snapshot_dir.name
    out_root = Path(out_root)
    case_dir = out_root / f"case_{case_id}"

    if case_dir.exists():
        if not force:
            raise FailureCaseExistsError(
                f"case déjà existant : {case_dir} (utiliser --force pour régénérer)"
            )
        if not (case_dir / "manifest.json").is_file():
            raise FailureCaseError(
                f"{case_dir} existe mais ne ressemble pas à un case généré par cet "
                "outil (pas de manifest.json) — suppression refusée, vérifier manuellement"
            )
        log_info("[FAILURE_CASE]", f"régénération forcée : suppression de {case_dir}")
        shutil.rmtree(case_dir)

    warnings: list[str] = []

    meta, meta_ok = _load_json(snapshot_dir / "meta.json", warnings)
    report, report_ok = _load_json(snapshot_dir / "validation_report.json", warnings)
    question_blocks, qb_ok = _load_json(snapshot_dir / "question_blocks.json", warnings)
    actions, actions_ok = _load_json(snapshot_dir / "actions_requested.json", warnings)

    # meta.json et validation_report.json sont toujours écrits par
    # failure_recorder.py : leur absence/invalidité rend le case incomplet.
    # question_blocks.json / actions_requested.json sont optionnels selon le stage
    # (ex: pas d'actions_requested.json en extraction) — seule leur présence-mais-
    # invalidité compte comme incomplétude, pas leur absence.
    incomplete = not (meta_ok and report_ok)
    if (snapshot_dir / "question_blocks.json").is_file() and not qb_ok:
        incomplete = True
    if (snapshot_dir / "actions_requested.json").is_file() and not actions_ok:
        incomplete = True

    stage = _resolve_stage(report if report_ok else None, snapshot_dir)
    reason = _clean_str(meta.get("reason")) if meta_ok and isinstance(meta, dict) else ""
    failure_types = _failure_types(report if report_ok else None)
    itype, target_id = _resolve_itype_target_id(
        report if report_ok else None,
        question_blocks if qb_ok else None,
        actions if actions_ok else None,
    )
    frame_chain = _resolve_frame_chain(
        meta if meta_ok else None, question_blocks if qb_ok else None, target_id
    )
    provider_domain = _resolve_provider_domain(meta if meta_ok else None)

    case_dir.mkdir(parents=True, exist_ok=False)
    try:
        artifacts = _copy_known_files(
            snapshot_dir,
            case_dir / "artifacts",
            meta=meta if meta_ok else None,
            meta_ok=meta_ok,
            warnings=warnings,
        )

        manifest = {
            "schema_version": SCHEMA_VERSION,
            "case_id": case_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_snapshot": str(snapshot_dir.resolve()),
            "stage": stage,
            "reason": reason or None,
            "failure_types": failure_types,
            "itype": itype,
            "target_id": target_id,
            "frame_chain": frame_chain,
            "provider_domain": provider_domain,
            "artifacts": artifacts,
            "incomplete": incomplete,
            "warnings": warnings,
        }

        (case_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        # Un case partiellement écrit est pire qu'aucun case.
        shutil.rmtree(case_dir, ignore_errors=True)
        raise

    log_info(
        "[FAILURE_CASE]",
        f"case créé stage={stage} failure_types={failure_types} "
        f"incomplete={incomplete} -> {case_dir}",
    )
    return case_dir
