"""Registre plat des correctifs externes, vide par défaut dans le bot.

Une entrée vise une fonction protégée (clé d'extractor_integrity.json) et
désigne la stratégie d'extraction autour de laquelle le hook peut l'évaluer.
Aucun correctif métier n'est enregistré par ce module.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from Survey.extractor_integrity import _hash_function, _load_registry
from Survey.log_utils import log_debug, log_info

_TAG = "[EXTERNAL_FIX_REGISTRY]"
_SURVEY_ROOT = Path(__file__).resolve().parent
_MAX_FIXES = 64
_MAX_CASE_IDS = 16
_MAX_SELECTORS = 8
_MAX_SELECTOR_LENGTH = 160
_FIX_ID = re.compile(r"[a-z][a-z0-9_]{2,63}")
_CASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}")
_SELECTOR_CHARS = re.compile(r"[A-Za-z0-9_ .#:\[\]()>+~*|^$=-]+")
_STAGES = frozenset({"extraction", "action"})
_POSITIONS = frozenset({"before", "after"})


class FixRegistryError(ValueError):
    """Déclaration invalide ; le registre reste inchangé."""


def _valid_selector(selector: str) -> bool:
    """Filtre structurel minimal, sans prétendre prouver la précision métier."""
    if (
        not isinstance(selector, str) or not 0 < len(selector) <= _MAX_SELECTOR_LENGTH
        or _SELECTOR_CHARS.fullmatch(selector) is None or selector != selector.strip()
        or "href" in selector.lower() or "src" in selector.lower()
        or selector[0] in ">+~" or selector[-1] in ">+~"
        or re.search(r"[>+~]\s*[>+~]", selector) is not None
    ):
        return False
    # Chaque segment doit désigner une structure, pas des mots libres.
    segments = [part for part in re.split(r"\s+|[>+~]", selector) if part]
    return bool(segments) and all(any(marker in part for marker in (".", "#", "[")) for part in segments)


@dataclass(frozen=True, slots=True)
class DomCondition:
    """Tous les sélecteurs requis présents ; tous les exclus absents.

    Le hook d'extraction évalue cette condition sur le contexte DOM courant. Les
    sélecteurs doivent décrire la structure du code, sans texte de question,
    URL ni DOM brut. Cette intention exige encore une revue humaine.
    """

    required_selectors: tuple[str, ...]
    excluded_selectors: tuple[str, ...] = ()

    def validate(self) -> None:
        if not isinstance(self.required_selectors, tuple) or not 0 < len(self.required_selectors) <= _MAX_SELECTORS:
            raise FixRegistryError("au moins un sélecteur DOM requis (maximum 8)")
        if not isinstance(self.excluded_selectors, tuple) or len(self.excluded_selectors) > _MAX_SELECTORS:
            raise FixRegistryError("trop de sélecteurs DOM exclus")
        selectors = self.required_selectors + self.excluded_selectors
        if any(not _valid_selector(selector) for selector in selectors):
            raise FixRegistryError("sélecteur DOM absent, trop large ou non structurel")
        if len(set(selectors)) != len(selectors):
            raise FixRegistryError("sélecteur DOM dupliqué ou contradictoire")


@dataclass(frozen=True, slots=True)
class ExternalFix:
    """Déclaration d'un fix indépendant ; aucune invocation ici.

    ``core_function_id`` est protégé. ``anchor_function_id`` utilise la même
    forme fichier.py::fonction, mais peut désigner une stratégie non protégée.
    En extraction, ``handler(driver, frame_chain)`` retourne des blocs ou None.
    """

    fix_id: str
    core_function_id: str
    expected_core_hash: str
    anchor_function_id: str
    position: str
    case_ids: tuple[str, ...]
    condition: DomCondition
    handler: Callable[..., object]
    stage: str = "extraction"


class ExternalFixRegistry:
    """Catalogue borné ; l'ordre de retour n'est jamais une priorité."""

    def __init__(self, *, root: Path = _SURVEY_ROOT) -> None:
        self._root = Path(root).resolve()
        self._fixes: dict[str, ExternalFix] = {}
        self._lock = threading.Lock()
        try:
            registry = _load_registry(self._root)
            if not isinstance(registry, dict) or not registry:
                raise ValueError("baseline absente ou invalide")
            self._core_hashes = {key: entry["hash"] for key, entry in registry.items()}
        except Exception as exc:
            self._core_hashes = {}
            try:
                log_debug(_TAG, f"baseline indisponible : {type(exc).__name__}")
            except Exception:
                pass

    def register(self, fix: ExternalFix) -> None:
        """Valide puis ajoute une entrée ; aucune exécution du handler."""
        if not isinstance(fix, ExternalFix):
            raise FixRegistryError("déclaration de correctif invalide")
        if not isinstance(fix.fix_id, str) or _FIX_ID.fullmatch(fix.fix_id) is None:
            raise FixRegistryError("fix_id invalide")
        if (
            not isinstance(fix.stage, str) or fix.stage not in _STAGES
            or not isinstance(fix.position, str) or fix.position not in _POSITIONS
        ):
            raise FixRegistryError("stage ou position invalide")
        if fix.stage == "action" and fix.position != "before":
            raise FixRegistryError("un correctif d'action doit précéder tout effet")
        if not isinstance(fix.core_function_id, str) or fix.core_function_id not in self._core_hashes:
            raise FixRegistryError("fonction core inconnue de la baseline")
        if fix.expected_core_hash != self._core_hashes[fix.core_function_id]:
            raise FixRegistryError("hash core différent de la baseline")
        core_file, core_function = fix.core_function_id.split("::", 1)
        try:
            current_hash = _hash_function(self._root, core_file, core_function)
        except Exception as exc:
            raise FixRegistryError("fonction core introuvable") from exc
        if current_hash != fix.expected_core_hash:
            raise FixRegistryError("code core différent du hash attendu")
        if not isinstance(fix.anchor_function_id, str) or fix.anchor_function_id.count("::") != 1:
            raise FixRegistryError("identifiant de stratégie d'ancrage invalide")
        file_name, function_name = fix.anchor_function_id.split("::", 1)
        anchor_path = (self._root / file_name).resolve()
        if (
            "\\" in file_name or Path(file_name).is_absolute()
            or not file_name.endswith(".py") or not function_name.isidentifier()
            or not anchor_path.is_relative_to(self._root.parent)
        ):
            raise FixRegistryError("stratégie d'ancrage hors du projet")
        try:
            _hash_function(self._root, file_name, function_name)
        except Exception as exc:
            raise FixRegistryError("stratégie d'ancrage introuvable") from exc
        if not isinstance(fix.case_ids, tuple) or not 0 < len(fix.case_ids) <= _MAX_CASE_IDS:
            raise FixRegistryError("un à seize case_id requis")
        if any(not isinstance(case_id, str) or _CASE_ID.fullmatch(case_id) is None for case_id in fix.case_ids):
            raise FixRegistryError("case_id invalide")
        if len(set(fix.case_ids)) != len(fix.case_ids):
            raise FixRegistryError("case_id dupliqué")
        if not isinstance(fix.condition, DomCondition):
            raise FixRegistryError("condition DOM manquante")
        fix.condition.validate()
        if not callable(fix.handler):
            raise FixRegistryError("handler de correctif manquant")
        with self._lock:
            if fix.fix_id in self._fixes:
                raise FixRegistryError("fix_id dupliqué")
            if len(self._fixes) >= _MAX_FIXES:
                raise FixRegistryError("borne de correctifs atteinte")
            self._fixes[fix.fix_id] = fix

    def get(self, fix_id: str) -> ExternalFix | None:
        """Résout une identité stable pour les futurs outils locaux/dev."""
        try:
            with self._lock:
                return self._fixes.get(fix_id)
        except (TypeError, ValueError):
            return None

    def try_register(self, fix: ExternalFix) -> bool:
        """Chemin fail-open pour un chargement futur dans le bot."""
        try:
            self.register(fix)
            return True
        except Exception as exc:
            try:
                fix_id = (
                    fix.fix_id if isinstance(fix, ExternalFix)
                    and isinstance(fix.fix_id, str) and _FIX_ID.fullmatch(fix.fix_id)
                    else "inconnu"
                )
                message = str(exc).replace("\r", " ").replace("\n", " ")[:160]
                log_info(_TAG, f"correctif ignoré fix_id={fix_id} : {type(exc).__name__}: {message}")
            except Exception:
                pass
            return False

    def candidates(self, *, stage: str, anchor_function_id: str, position: str) -> tuple[ExternalFix, ...]:
        """Retourne tous les candidats, sans choix ni évaluation du DOM."""
        if (
            not isinstance(stage, str) or stage not in _STAGES
            or not isinstance(position, str) or position not in _POSITIONS
            or not isinstance(anchor_function_id, str)
        ):
            return ()
        with self._lock:
            return tuple(sorted(
                (
                    fix for fix in self._fixes.values()
                    if fix.stage == stage and fix.anchor_function_id == anchor_function_id
                    and fix.position == position
                ),
                key=lambda fix: fix.fix_id,
            ))


# Registre commun vide tant qu'aucun correctif validé n'a été déclaré.
EXTERNAL_FIXES = ExternalFixRegistry()
