from __future__ import annotations

"""Enregistrement passif des anomalies détectées par les validators Phase 1A."""

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from Survey.log_utils import log_debug, log_info


def record_validation_failure(
    driver,
    *,
    stage: str,
    report: dict,
    question_blocks: Any = None,
    actions: Any = None,
    pre_action_dom: Optional[str] = None,
    runtime_state: Any = None,
    action_trace: Any = None,
) -> Optional[str]:
    """Capture le contexte d'un validator en échec, sans jamais casser le survey.

    Réutilise page_snapshot comme source unique de capture DOM/frame/screenshot.
    Les artefacts propres à l'observabilité sont ajoutés dans le même dossier.

    runtime_state/action_trace (Browser Capsule, Phase 3B, additifs) : fournis
    par le hook d'action de page_snapshot.py via Survey/browser_capsule.py.
    Optionnels (None par défaut) — absents, ils ne changent rien au comportement
    existant ; fournis, ils sont écrits en plus des artefacts déjà produits,
    jamais à leur place.
    """
    if not isinstance(report, dict) or report.get("ok", True):
        return None

    try:
        from Survey.page_snapshot import dump_page_snapshot

        folder = dump_page_snapshot(
            driver,
            reason=f"{stage}_validation_failure",
            question_blocks=question_blocks,
            artifact_profile="action_validation" if stage == "action" else None,
            pre_action_dom=pre_action_dom if stage == "action" else None,
        )
        out = Path(folder)
        (out / "validation_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if actions is not None:
            (out / "actions_requested.json").write_text(
                json.dumps(actions, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        if runtime_state is not None:
            (out / "runtime_state.json").write_text(
                json.dumps(runtime_state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        if action_trace is not None:
            (out / "action_trace.json").write_text(
                json.dumps(action_trace, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        issue_types = [
            str(i.get("failure_type") or "unknown")
            for i in (report.get("issues") or [])
            if isinstance(i, dict)
        ]
        log_info(
            "[OBSERVABILITY]",
            f"validation_failure stage={stage} issues={issue_types} snapshot={folder}",
        )
        return folder
    except Exception as exc:
        # L'observabilité ne doit jamais devenir une nouvelle cause d'échec survey.
        log_debug("[OBSERVABILITY]", f"failure recorder error stage={stage}: {type(exc).__name__}: {exc}")
        return None


# =============================================================================
# BDL-2b — MODE COLLECTE (plans d'actions jugés PASS)
# =============================================================================
# Pourquoi : record_validation_failure() n'écrit que si le validator a déjà
# conclu à un échec. Les plans PASS ne laissent donc aucune trace, ce qui interdit
# de mesurer les faux négatifs du validator (BDL-6). Ce mode persiste, pour les
# plans PASS uniquement, les FAITS RUNTIME avant/après (pas de DOM complet, pas de
# screenshot : réservés aux échecs).
#
# Isolation : racine DISTINCTE de snapshots/ (jamais lue par l'Autofix, qui ne
# traite que des dossiers de failure cases) et noms de dossier sans le motif
# "*_validation_failure" ; meta.json porte kind="bdl_collect_pass".
#
# Garanties : jamais d'exception vers l'appelant (le survey n'est jamais impacté),
# volume borné par un plafond de dossiers avec abandon contrôlé et log unique.

BDL_COLLECT_KIND = "bdl_collect_pass"
_BDL_COLLECT_DEFAULT_MAX = 200

# État de processus : compteur de dossiers déjà présents/écrits (initialisé une
# seule fois par racine, pour éviter de re-lister le disque à chaque plan) et
# mémoire du log "plafond atteint" (1 seule ligne info par racine).
_collect_count_by_root: dict = {}
_collect_cap_logged: set = set()


def _bdl_collect_root() -> Path:
    """Racine de collecte, distincte de la racine des snapshots d'échec."""
    # Local sous ./bdl_collect, hors-local sous /tmp/bdl_collect (même logique
    # que page_snapshot pour ./snapshots et /tmp/snapshots).
    from config import RUN_ENV

    default_root = "./bdl_collect_action" if RUN_ENV != "prod" else "/tmp/bdl_collect_action"
    return Path(os.getenv("SURVEY_BDL_COLLECT_DIR", default_root))


def _bdl_collect_max() -> int:
    """Plafond de cas collectés par racine (entier > 0, sinon valeur par défaut)."""
    raw = (os.getenv("SURVEY_BDL_COLLECT_MAX", "") or "").strip()
    try:
        value = int(raw)
        return value if value > 0 else _BDL_COLLECT_DEFAULT_MAX
    except ValueError:
        return _BDL_COLLECT_DEFAULT_MAX


def record_collected_action(
    driver,
    *,
    report: Any,
    actions: Any,
    action_trace: Any,
    dispatcher_success: Optional[bool],
) -> Optional[str]:
    """Persiste les faits runtime avant/après d'un plan d'actions jugé PASS.

    Contrat : retourne le dossier créé, ou None (désactivé par l'appelant,
    plafond atteint, faits indisponibles, erreur). Ne lève JAMAIS : un échec de
    collecte ne doit ni casser le survey ni modifier le résultat du plan.
    """
    try:
        # Sans faits "après" exploitables (aucune cible résolue, capture
        # impossible), le cas n'apporterait rien à l'oracle et consommerait le
        # plafond : on ne l'écrit pas.
        after = action_trace.get("after") if isinstance(action_trace, dict) else None
        if not isinstance(after, dict):
            log_debug("[BDL_COLLECT]", "plan PASS ignoré : faits runtime 'après' indisponibles")
            return None

        root = _bdl_collect_root()
        key = str(root)
        if key not in _collect_count_by_root:
            # Initialisation unique : reprend le compte des dossiers déjà présents
            # (redémarrage du bot) pour que le plafond reste global à la racine.
            _collect_count_by_root[key] = (
                sum(1 for e in root.iterdir() if e.is_dir()) if root.is_dir() else 0
            )

        cap = _bdl_collect_max()
        count = _collect_count_by_root[key]
        if count >= cap:
            if key not in _collect_cap_logged:
                _collect_cap_logged.add(key)
                log_info("[BDL_COLLECT]", f"plafond atteint ({cap} cas) — collecte suspendue dans {root}")
            return None

        folder = root / f"{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}_{count:05d}_action_pass"
        folder.mkdir(parents=True, exist_ok=True)

        url = None
        try:
            url = getattr(driver, "url", None)
            url = url if isinstance(url, str) else None
        except Exception:
            url = None

        meta = {
            "kind": BDL_COLLECT_KIND,
            "stage": "action",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "url": url,
            "dispatcher_success": dispatcher_success,
            "validator_ok": True,
        }
        (folder / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # Nom volontairement distinct de validation_report.json : un dossier collecté
        # présenté par erreur au failure_case_builder serait ainsi vu comme case
        # incomplet (pas de validation_report.json) plutôt que comme un échec réel.
        (folder / "pass_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (folder / "action_trace.json").write_text(
            json.dumps(action_trace, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        _collect_count_by_root[key] = count + 1
        log_debug("[BDL_COLLECT]", f"plan PASS collecté ({count + 1}/{cap}) -> {folder}")
        return str(folder)
    except Exception as exc:
        # La collecte ne doit jamais devenir une cause d'échec survey.
        log_debug("[BDL_COLLECT]", f"collecte erreur: {type(exc).__name__}: {exc}")
        return None

# =============================================================================
# BDL-4 étape 2 — MODE COLLECTE EXTRACTION (pages analysées, PASS implicite)
# =============================================================================
# Pourquoi : la trace des règles (BDL-4 étape 1) et les blocs finaux d'une page
# analysée avec succès ne laissent aucune trace persistante. Cette collecte
# persiste, par page analysée, un JSON léger (blocs allégés + déclenchements de
# règles drainés). Aucun DOM, aucun screenshot, aucun MHTML.
#
# Isolation : racine DISTINCTE de bdl_collect/ (action) et de snapshots/
# (échecs). meta.json porte kind="bdl_collect_extraction" ; le fichier principal
# s'appelle extraction_pass.json (jamais validation_report.json) pour qu'un
# dossier collecté présenté par erreur au failure_case_builder soit vu comme
# incomplet, pas comme un échec réel.
#
# Garanties : jamais d'exception vers l'appelant, volume borné (plafond de
# dossiers + budget de taille par fichier), échantillonnage déterministe 1/N.

BDL_EXTRACT_COLLECT_KIND = "bdl_collect_extraction"
_BDL_EXTRACT_DEFAULT_MAX = 200
_BDL_EXTRACT_DEFAULT_SAMPLE = 5
_BDL_EXTRACT_DEFAULT_MAX_BYTES = 256 * 1024
_BDL_EXTRACT_STR_LIMIT = 300

_extract_count_by_root: dict = {}
_extract_cap_logged: set = set()
_extract_sample_counter: int = 0


def _bdl_extract_collect_root() -> Path:
    """Racine de collecte extraction, distincte de bdl_collect/ et snapshots/."""
    from config import RUN_ENV

    default_root = "./bdl_collect_extraction" if RUN_ENV != "prod" else "/tmp/bdl_collect_extraction"
    return Path(os.getenv("SURVEY_BDL_EXTRACT_COLLECT_DIR", default_root))


def _bdl_extract_collect_max() -> int:
    raw = (os.getenv("SURVEY_BDL_EXTRACT_COLLECT_MAX", "") or "").strip()
    try:
        value = int(raw)
        return value if value > 0 else _BDL_EXTRACT_DEFAULT_MAX
    except ValueError:
        return _BDL_EXTRACT_DEFAULT_MAX


def _bdl_extract_sample_n() -> int:
    """Échantillonnage déterministe : 1 page analysée collectée sur N."""
    raw = (os.getenv("SURVEY_BDL_EXTRACT_SAMPLE", "1") or "").strip()
    try:
        value = int(raw)
        return value if value > 1 else 1
    except ValueError:
        return _BDL_EXTRACT_DEFAULT_SAMPLE


def _bdl_extract_max_bytes() -> int:
    raw = (os.getenv("SURVEY_BDL_EXTRACT_MAX_BYTES", "") or "").strip()
    try:
        value = int(raw)
        return value if value > 0 else _BDL_EXTRACT_DEFAULT_MAX_BYTES
    except ValueError:
        return _BDL_EXTRACT_DEFAULT_MAX_BYTES


def bdl_extract_sample_tick() -> bool:
    """Compteur global déterministe : True une fois sur N (N = SURVEY_BDL_EXTRACT_SAMPLE).

    Appelée uniquement pour une page éligible : un False retourné ici signifie
    donc "page éligible écartée par l'échantillonnage". Cette trace debug est
    le seul moyen de la distinguer d'un hook non appelé ou d'une collecte en
    erreur. Avec N=1, jamais de False, donc jamais de trace.

    Ne lève jamais. Sans effet sur le survey.
    """
    global _extract_sample_counter
    try:
        _extract_sample_counter += 1
        n = _bdl_extract_sample_n()
        if (_extract_sample_counter % n) == 0:
            return True
        log_debug(
            "[BDL_COLLECT]",
            f"extraction ignorée (échantillonnage) page={_extract_sample_counter} N={n}",
        )
        return False
    except Exception:
        return False


def _sanitize_url(url: Any) -> Optional[str]:
    """URL sans paramètres de session : scheme://host/path uniquement (query/fragment jetés)."""
    if not isinstance(url, str) or not url:
        return None
    try:
        from urllib.parse import urlsplit

        parts = urlsplit(url)
        if not parts.scheme or not parts.netloc:
            return None
        return f"{parts.scheme}://{parts.netloc}{parts.path or ''}"
    except Exception:
        return None


def _light_scalar(value: Any, limit: int = _BDL_EXTRACT_STR_LIMIT) -> Any:
    """Valeur JSON-safe allégée : scalaires conservés (str tronquées), autres -> None."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return value[:limit]
    return None


def _lighten_block(block: Any) -> dict:
    """Bloc allégé : champs de contrat + contexte allégé. Neuf jamais : valeurs
    non sérialisables (handles Playwright, objets) remplacées par None."""
    out: dict = {}
    if not isinstance(block, dict):
        return out
    for key in ("itype", "question", "min_select", "max_select", "target_id"):
        out[key] = _light_scalar(block.get(key), limit=500 if key == "question" else _BDL_EXTRACT_STR_LIMIT)
    options = block.get("options")
    out["options"] = [
        _light_scalar(o) for o in (options or [])[:100] if _light_scalar(o) is not None
    ] if isinstance(options, list) else []
    # Total réel avant allègement/plafond : permet à l'oracle (BDL-4 étape 3)
    # de ne pas confondre « options non sérialisables / tronquées » et « vide ».
    out["options_count"] = len(options) if isinstance(options, list) else 0
    ctx = block.get("context")
    if isinstance(ctx, dict):
        light_ctx: dict = {}
        for k, v in ctx.items():
            if not isinstance(k, str) or len(light_ctx) >= 40:
                continue
            if isinstance(v, list):
                light_ctx[k] = [_light_scalar(x) for x in v[:20]]
            else:
                light_ctx[k] = _light_scalar(v)
        out["context"] = light_ctx
    return out


def record_collected_extraction(
    driver,
    *,
    question_blocks: Any,
    rule_events: Any = None,
) -> Optional[str]:
    """Persiste blocs allégés + déclenchements de règles d'une page analysée.

    Contrat : retourne le dossier créé, ou None (désactivé par l'appelant,
    plafond atteint, rien à écrire, erreur). Ne lève JAMAIS : un échec de
    collecte ne doit ni casser le survey ni modifier l'extraction.
    """
    try:
        blocks = [
            _lighten_block(b) for b in (question_blocks or []) if isinstance(b, dict)
        ]
        events = [e for e in (rule_events or []) if isinstance(e, dict)]

        url = None
        try:
            url = _sanitize_url(getattr(driver, "url", None))
        except Exception:
            url = None

        payload: dict = {
            "url": url,
            "blocks": blocks,
            "blocks_total": len(blocks),
            "rule_events": events,
        }

        # BDL-4 étape 3 : verdict d'observation calculé sur TOUS les blocs allégés
        # (avant troncature par budget). Purement additif : n'influence ni le
        # validateur, ni les failure_cases, ni AutoFix ; toute erreur est absorbée.
        try:
            from Survey.extraction_oracle import evaluate_extraction_pass

            oracle = evaluate_extraction_pass(payload)
            if oracle is not None:
                payload["oracle"] = oracle
        except Exception as exc:
            log_debug("[BDL_COLLECT]", f"extraction_oracle indisponible: {type(exc).__name__}: {exc}")

        # Budget de taille : tronque les blocs (jamais les rule_events) si dépassement.
        budget = _bdl_extract_max_bytes()
        while True:
            try:
                serialized = json.dumps(payload, ensure_ascii=False)
            except Exception:
                return None
            if len(serialized.encode("utf-8")) <= budget or not payload["blocks"]:
                break
            if len(payload["blocks"]) <= 1:
                # Un bloc unique restant au-dessus du budget : on le sacrifie
                # (les rule_events, jamais tronquées, restent la donnée utile).
                payload["blocks"] = []
            else:
                payload["blocks"] = payload["blocks"][: len(payload["blocks"]) // 2]
            payload["truncated"] = True

        root = _bdl_extract_collect_root()
        key = str(root)
        if key not in _extract_count_by_root:
            _extract_count_by_root[key] = (
                sum(1 for e in root.iterdir() if e.is_dir()) if root.is_dir() else 0
            )

        cap = _bdl_extract_collect_max()
        count = _extract_count_by_root[key]
        if count >= cap:
            if key not in _extract_cap_logged:
                _extract_cap_logged.add(key)
                log_info("[BDL_COLLECT]", f"plafond extraction atteint ({cap} cas) — collecte suspendue dans {root}")
            return None

        folder = root / f"{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}_{count:05d}_extraction_pass"
        folder.mkdir(parents=True, exist_ok=True)

        meta = {
            "kind": BDL_EXTRACT_COLLECT_KIND,
            "stage": "extraction",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "url": url,
        }
        (folder / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (folder / "extraction_pass.json").write_text(serialized, encoding="utf-8")

        _extract_count_by_root[key] = count + 1
        log_debug(
            "[BDL_COLLECT]",
            f"extraction collectée ({count + 1}/{cap}) -> {folder} blocks={len(payload['blocks'])} rules={len(events)}",
        )
        return str(folder)
    except Exception as exc:
        log_debug("[BDL_COLLECT]", f"collecte extraction erreur: {type(exc).__name__}: {exc}")
        return None


# ---------------------------------------------------------------------------
# BDL-5 v2 étape 1 : collecte des événements CTA (corpus de l'oracle CTA)
# ---------------------------------------------------------------------------
#
# Unité de collecte : l'ÉVÉNEMENT (un clic), persisté à sa création par
# l'enveloppe de cta_trace (pas de vidage au snapshot). Une ligne JSON par
# événement, un fichier .jsonl par processus, dans une racine distincte de
# snapshots/, bdl_collect_action/ et bdl_collect_extraction/.
#
# Persistés : les événements de clic (kind="click") et les appels de haut niveau
# classés unobserved_path (kind="entry", sans URL ni appel navigateur : ils ne
# servent qu'au calcul de couverture, l'oracle ne les juge pas).
# Jamais persistés : les événements mode="intercept" (CTA_INTERCEPT_ONLY est
# obsolète dans le projet, décision du 2026-11-18) ni les autres champs de
# l'événement : liste blanche de champs, URLs assainies (query et fragment
# jetés), chaînes tronquées.
#
# Garanties : jamais d'exception vers l'appelant (le survey n'est jamais
# impacté) ; volume borné par un plafond en OCTETS sur la racine (somme des
# fichiers .jsonl, initialisée une fois par processus), sans échantillonnage ;
# abandon contrôlé avec une seule ligne info par racine.

BDL_CTA_COLLECT_KIND = "bdl_collect_cta"
_BDL_CTA_DEFAULT_MAX_BYTES = 5 * 1024 * 1024
_BDL_CTA_STR_LIMIT = 120

_CTA_CLICK_KEYS = (
    "v", "kind", "fn", "caller", "mode", "returned", "raised",
    "marker_status", "t_start", "duration_ms",
)
_CTA_ENTRY_KEYS = (
    "v", "kind", "fn", "outcome", "mode", "clicks_observed", "t_start", "duration_ms",
)

_cta_bytes_by_root: dict = {}
_cta_file_by_root: dict = {}
_cta_cap_logged: set = set()


def _bdl_cta_collect_enabled() -> bool:
    """Collecte CTA (activée par défaut ; "0" la désactive).

    Flag DISTINCT de SURVEY_OBSERVABILITY, SURVEY_BDL_COLLECT (action),
    SURVEY_BDL_EXTRACT_COLLECT (extraction) et SURVEY_BDL_CTA_TRACE. L'enveloppe
    CTA n'étant installée que si l'observabilité est active, la collecte la
    suppose active : sans elle, ce flag n'a aucun effet.
    """
    value = (os.getenv("SURVEY_BDL_CTA_COLLECT", "1") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _bdl_cta_collect_root() -> Path:
    """Racine de collecte CTA, distincte des autres racines de collecte."""
    from config import RUN_ENV

    default_root = "./bdl_collect_cta" if RUN_ENV != "prod" else "/tmp/bdl_collect_cta"
    return Path(os.getenv("SURVEY_BDL_CTA_COLLECT_DIR", default_root))


def _bdl_cta_collect_max_bytes() -> int:
    """Plafond d'octets de la racine (entier > 0, sinon valeur par défaut)."""
    raw = (os.getenv("SURVEY_BDL_CTA_COLLECT_MAX_BYTES", "") or "").strip()
    try:
        value = int(raw)
        return value if value > 0 else _BDL_CTA_DEFAULT_MAX_BYTES
    except ValueError:
        return _BDL_CTA_DEFAULT_MAX_BYTES


def _light_cta_marker(marker: Any) -> Optional[dict]:
    """Marqueur de progression allégé : URL assainie, chaînes tronquées."""
    if not isinstance(marker, dict):
        return None
    return {
        "url": _sanitize_url(marker.get("url")),
        "txt": _light_scalar(marker.get("txt"), _BDL_CTA_STR_LIMIT),
        "qNodes": _light_scalar(marker.get("qNodes")),
        "notifSig": _light_scalar(marker.get("notifSig"), _BDL_CTA_STR_LIMIT),
        "progGridSig": _light_scalar(marker.get("progGridSig"), _BDL_CTA_STR_LIMIT),
    }


def _light_cta_event(event: Any) -> Optional[dict]:
    """Version persistable d'un événement CTA, ou None s'il ne doit pas l'être.

    Liste blanche de champs (tout champ inconnu est ignoré). None pour : non-dict,
    mode "intercept", kind inconnu.
    """
    if not isinstance(event, dict):
        return None
    if event.get("mode") == "intercept":
        return None
    kind = event.get("kind")
    if kind == "click":
        keys = _CTA_CLICK_KEYS
    elif kind == "entry":
        keys = _CTA_ENTRY_KEYS
    else:
        return None

    out: dict = {key: _light_scalar(event.get(key)) for key in keys}
    if kind == "click":
        changed = event.get("signals_changed")
        out["signals_changed"] = (
            [str(x)[:30] for x in changed[:10]] if isinstance(changed, (list, tuple)) else []
        )
        target = event.get("target")
        out["target"] = (
            {
                key: _light_scalar(target.get(key), 80)
                for key in ("tag", "id", "name", "className", "text")
            }
            if isinstance(target, dict)
            else None
        )
        out["marker_before"] = _light_cta_marker(event.get("marker_before"))
        out["marker_after"] = _light_cta_marker(event.get("marker_after"))
        for key in ("page_before", "page_after"):
            page = event.get(key)
            out[key] = (
                {"url": _sanitize_url(page.get("url")), "doc": _light_scalar(page.get("doc"), 40)}
                if isinstance(page, dict)
                else None
            )
    out["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    out["pid"] = os.getpid()
    return out


def record_collected_cta(event: Any) -> bool:
    """Persiste un événement CTA (une ligne JSON) dans le fichier du processus.

    Contrat : retourne True si la ligne a été écrite, False sinon (désactivé,
    événement non persistable, plafond atteint, erreur). Ne lève JAMAIS : un
    échec de collecte ne doit ni casser le survey ni modifier le clic.
    """
    try:
        if not _bdl_cta_collect_enabled():
            return False
        light = _light_cta_event(event)
        if light is None:
            return False
        data = (json.dumps(light, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")

        root = _bdl_cta_collect_root()
        key = str(root)
        if key not in _cta_bytes_by_root:
            # Initialisé une seule fois par processus : octets déjà présents.
            total = 0
            if root.is_dir():
                for f in root.glob("*.jsonl"):
                    try:
                        total += f.stat().st_size
                    except OSError:
                        continue
            _cta_bytes_by_root[key] = total

        cap = _bdl_cta_collect_max_bytes()
        used = _cta_bytes_by_root[key]
        if used + len(data) > cap:
            if key not in _cta_cap_logged:
                _cta_cap_logged.add(key)
                log_info("[BDL_COLLECT]", f"plafond CTA atteint ({cap} octets) — collecte suspendue dans {root}")
            return False

        if key not in _cta_file_by_root:
            _cta_file_by_root[key] = f"cta_events_{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}.jsonl"
        root.mkdir(parents=True, exist_ok=True)
        with open(root / _cta_file_by_root[key], "ab") as fh:
            fh.write(data)

        _cta_bytes_by_root[key] = used + len(data)
        log_debug(
            "[BDL_COLLECT]",
            f"cta collecté kind={light.get('kind')} fn={light.get('fn')} ({used + len(data)}/{cap} octets)",
        )
        return True
    except Exception as exc:
        log_debug("[BDL_COLLECT]", f"collecte cta erreur: {type(exc).__name__}: {exc}")
        return False
