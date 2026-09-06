"""
db_config.py

Résolution centralisée de DATABASE_URL — source unique pour tous les modules
qui se connectent à la base Postgres centrale (licences, état des comptes,
mémoire inter-bots des surveys).

Priorité (du plus fort au plus faible) :
  1) _license_config.py — valeur embarquée à la compilation (build Nuitka/PyInstaller).
  2) Variable d'environnement DATABASE_URL — fallback dev/attach uniquement
     (module _license_config absent du projet, import qui échoue).

Même convention que RUN_ENV (config.py) et STATE_BACKEND (global_config.py) :
en build compilé, une valeur figée à la compilation ne doit jamais pouvoir être
écrasée par une variable d'environnement définie avant le lancement du binaire.
"""

from __future__ import annotations
import os


def get_database_url() -> str:
    try:
        from _license_config import DATABASE_URL as _DB  # type: ignore
        if _DB and _DB.strip():
            return _DB.strip()
    except ImportError:
        pass
    return os.getenv("DATABASE_URL", "").strip()
