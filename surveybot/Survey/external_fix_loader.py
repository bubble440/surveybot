"""Chargement explicite et unique des correctifs externes du bot.

La liste reste vide tant qu'aucun correctif métier n'est adopté. Chaque entrée
future est une fonction qui importe statiquement un module nommé et retourne
son tuple ``FIXES``. Cette liste explicite rend l'ordre et le contenu d'une
release prédictibles, sans découverte de fichiers ni import construit au runtime.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from Survey.external_fix_registry import EXTERNAL_FIXES, ExternalFix
from Survey.log_utils import log_debug

_TAG = "[EXTERNAL_FIX_LOADER]"
MAX_MODULES = 64
MAX_FIXES_PER_MODULE = 64

# Pour activer un module : définir ici une fonction avec un import statique
# ``from Survey.<module_nomme> import FIXES`` puis l'ajouter à ce tuple.
# Un import dans la fonction permet d'isoler son échec des modules suivants.


def _load_hidden_consent_modal_visible_radio_question() -> tuple[ExternalFix, ...]:
    from Survey.external_fix_hidden_consent_radio_question import FIXES

    return FIXES


MODULE_IMPORTS: tuple[Callable[[], tuple[ExternalFix, ...]], ...] = (
    _load_hidden_consent_modal_visible_radio_question,
)

_lock = threading.Lock()
_loaded = False


def _debug(message: str) -> None:
    try:
        log_debug(_TAG, message)
    except Exception:
        pass


def load_external_fixes_once() -> None:
    """Enregistre au plus une fois par processus, sans bloquer un hook."""
    global _loaded
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        try:
            if len(MODULE_IMPORTS) > MAX_MODULES:
                _debug("liste de modules tronquée à la borne")
            for import_fixes in MODULE_IMPORTS[:MAX_MODULES]:
                try:
                    fixes = import_fixes()
                    if not isinstance(fixes, tuple) or len(fixes) > MAX_FIXES_PER_MODULE:
                        raise ValueError("déclarations invalides ou trop nombreuses")
                except Exception as exc:
                    _debug(f"module ignoré : {type(exc).__name__}")
                    continue
                for fix in fixes:
                    try:
                        EXTERNAL_FIXES.try_register(fix)
                    except Exception as exc:
                        _debug(f"déclaration ignorée : {type(exc).__name__}")
        except Exception as exc:
            _debug(f"chargement interrompu : {type(exc).__name__}")
        finally:
            _loaded = True
