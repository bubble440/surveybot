"""Compteurs locaux d'usage des fonctions protégées, communs aux stages.

L'identité est la clé ``fichier.py::fonction`` d'extractor_integrity.json,
relative à Survey/. Le hash du code présent dans ce processus distingue les
versions, même quand la baseline du registre est en retard. Aucun contenu de
survey n'entre dans cette API.
"""

from __future__ import annotations

import atexit
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from Survey.extractor_integrity import _hash_function, _load_registry
from Survey.log_utils import log_debug

_TAG = "[CORE_FUNCTION_METRICS]"
SCHEMA_VERSION = "1.0"
_MAX_FUNCTIONS = 512
_MAX_SERIES = 1024
_FLUSH_INTERVAL_S = 60.0
_STAGES = {None, "extraction", "action"}
_KEY_PATTERN = re.compile(r"(?:\.\./)?(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.py::[A-Za-z_][A-Za-z0-9_]*")
_HASH_PATTERN = re.compile(r"[0-9a-f]{64}")
_SURVEY_ROOT = Path(__file__).resolve().parent


def _utc_iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


class CoreFunctionMetrics:
    """Un producteur par processus, avec instantané atomique et borné.

    ``record_call`` est sans effet sur l'appelant en cas d'erreur. Une écriture
    est tentée au plus une fois par intervalle pendant les appels, puis à la
    sortie normale du processus. Les producteurs écrivent des fichiers séparés.
    """

    def __init__(
        self,
        *,
        root: Path = _SURVEY_ROOT,
        output_dir: Path | None = None,
        flush_interval_s: float = _FLUSH_INTERVAL_S,
        register_atexit: bool = True,
    ) -> None:
        self._root = Path(root).resolve()
        self._output_dir = Path(output_dir or os.getenv("SURVEYBOT_CORE_METRICS_DIR") or "core_function_metrics")
        self._registry = _load_registry(self._root)
        if not isinstance(self._registry, dict) or not 0 < len(self._registry) <= _MAX_FUNCTIONS:
            raise ValueError("registre de fonctions protégées invalide ou trop grand")
        for key, entry in self._registry.items():
            if (
                not isinstance(key, str) or _KEY_PATTERN.fullmatch(key) is None
                or not isinstance(entry, dict) or not isinstance(entry.get("hash"), str)
                or _HASH_PATTERN.fullmatch(entry["hash"]) is None
            ):
                raise ValueError("entrée de registre invalide")
        self._code_hashes: dict[str, str | None] = {}
        self._series: dict[tuple[str, str | None, str], list[float | int]] = {}
        self._lock = threading.Lock()
        self._run_id = uuid.uuid4().hex
        self._started_at = time.time()
        self._last_flush_attempt = time.monotonic()
        self._flush_interval_s = max(1.0, float(flush_interval_s))
        self._logged_errors: set[str] = set()
        if register_atexit:
            atexit.register(self.flush)

    def _debug_once(self, reason: str) -> None:
        if reason not in self._logged_errors and len(self._logged_errors) < 4:
            self._logged_errors.add(reason)
            try:
                log_debug(_TAG, f"métriques indisponibles : {reason}")
            except Exception:
                pass

    def record_call(self, function_id: str, *, stage: str | None = None) -> None:
        """Compte un appel identifié par une clé du registre, sans lever d'erreur."""
        try:
            if not isinstance(function_id, str) or function_id not in self._registry or stage not in _STAGES:
                return
            with self._lock:
                if function_id not in self._code_hashes:
                    file_name, function_name = function_id.split("::", 1)
                    try:
                        self._code_hashes[function_id] = _hash_function(self._root, file_name, function_name)
                    except Exception:
                        self._code_hashes[function_id] = None
                        self._debug_once("hash du code indisponible")
                code_hash = self._code_hashes[function_id]
                if code_hash is None:
                    return  # Jamais de compteur sans version de code vérifiable.
                key = (function_id, stage, code_hash)
                now = time.time()
                if key in self._series:
                    row = self._series[key]
                    row[0] += 1
                    row[2] = now
                elif len(self._series) < _MAX_SERIES:
                    self._series[key] = [1, now, now]
                else:
                    self._debug_once("borne de séries atteinte")
                    return
                if time.monotonic() - self._last_flush_attempt >= self._flush_interval_s:
                    self._last_flush_attempt = time.monotonic()
                    self._flush_locked()
        except Exception:
            # La métrique ne peut jamais devenir une condition du survey.
            self._debug_once("enregistrement impossible")

    def _snapshot(self) -> dict:
        functions = []
        for (function_id, stage, code_hash), (calls, first, last) in sorted(
            self._series.items(), key=lambda item: (item[0][0], item[0][1] or "", item[0][2])
        ):
            functions.append({
                "function_id": function_id,
                "stage": stage,
                "baseline_hash": self._registry[function_id]["hash"],
                "code_hash": code_hash,
                "call_count": calls,
                "first_called_at": _utc_iso(first),
                "last_called_at": _utc_iso(last),
            })
        return {
            "schema_version": SCHEMA_VERSION,
            "producer_id": self._run_id,
            "started_at": _utc_iso(self._started_at),
            "snapshot_at": _utc_iso(time.time()),
            "functions": functions,
        }

    def _flush_locked(self) -> bool:
        if not self._series:
            return True
        output = self._output_dir / f"{self._run_id}.json"
        temporary = self._output_dir / f".{self._run_id}.tmp"
        try:
            self._output_dir.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(self._snapshot(), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            os.replace(temporary, output)
            return True
        except Exception:
            self._debug_once("écriture locale impossible")
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    def flush(self) -> bool:
        """Persiste l'instantané courant ; renvoie False sans lever si impossible."""
        try:
            with self._lock:
                self._last_flush_attempt = time.monotonic()
                return self._flush_locked()
        except Exception:
            self._debug_once("flush impossible")
            return False


_default_metrics: CoreFunctionMetrics | bool | None = None
_default_lock = threading.Lock()


def record_core_call(function_id: str, *, stage: str | None = None) -> None:
    """Point d'entrée futur des instrumentations extraction et action."""
    global _default_metrics
    try:
        if _default_metrics is None:
            with _default_lock:
                if _default_metrics is None:
                    try:
                        _default_metrics = CoreFunctionMetrics()
                    except Exception as exc:
                        _default_metrics = False  # Pas de relecture du registre à chaque appel.
                        try:
                            log_debug(_TAG, f"initialisation impossible : {type(exc).__name__}")
                        except Exception:
                            pass
        if _default_metrics is not False:
            _default_metrics.record_call(function_id, stage=stage)
    except Exception:
        # Cela inclut un registre absent/illisible au démarrage.
        return
