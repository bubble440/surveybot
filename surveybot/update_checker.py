"""
update_checker.py
Vérifie si une mise à jour du code est disponible sur origin/main et relance
le bot proprement si c'est le cas.

Actif uniquement si UPDATE_CHECK_ENABLED=1.
Point d'appel : run_main_loop() dans launch.py, entre deux cycles survey.

Logique :
  1. git fetch origin  (silencieux, timeout 10s)
  2. Comparer git rev-parse HEAD vs git rev-parse origin/main
  3. Si identiques -> rien à faire, on continue.
  4. Si différents  -> git pull, supprimer le PID courant, os.execv() pour relancer.
  5. Si git inaccessible -> log + ignorer, réessayer au prochain cycle.

Prérequis sur chaque mini-PC :
  - Git installé et dans le PATH.
  - Credentials GitHub configurés (token GIT_TOKEN en env ou credentials Windows Git).
  - Code source présent (repo cloné sur la machine, pas le binaire PyInstaller seul).

Note : cette fonction est un no-op complet si UPDATE_CHECK_ENABLED != "1",
si git est absent, ou si le repo n'est pas accessible. Elle ne bloque jamais
le bot en cas d'échec.
"""

from __future__ import annotations

import os
import subprocess
import sys
import logging

log = logging.getLogger("update_checker")

# Timeout pour chaque appel git (secondes)
_GIT_TIMEOUT = 10


def _git(*args) -> str:
    """Lance une commande git et retourne stdout stripped. Lève si code != 0."""
    env = os.environ.copy()
    # Injecter GIT_TOKEN dans l'URL si présent (HTTPS sans prompt interactif)
    token = env.get("GIT_TOKEN", "").strip()
    if token:
        env["GIT_ASKPASS"] = "echo"
        env["GIT_USERNAME"] = "token"
        env["GIT_PASSWORD"] = token

    result = subprocess.run(
        ["git"] + list(args),
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} -> rc={result.returncode}: {result.stderr.strip()}")
    return result.stdout.strip()


def _delete_pid(account_id: str) -> None:
    """Supprime le fichier PID avant le re-exec pour éviter un faux 'déjà actif'."""
    try:
        base = os.path.dirname(os.path.abspath(__file__))
        pid_path = os.path.join(base, "pids", f"bot_{account_id}.pid")
        if os.path.exists(pid_path):
            os.remove(pid_path)
    except Exception as e:
        log.warning("[UPDATE] Impossible de supprimer le PID avant re-exec : %s", e)


def check_and_apply(account_id: str) -> None:
    """
    Vérifie si une mise à jour est disponible et relance le bot si oui.
    No-op si UPDATE_CHECK_ENABLED != "1" ou si git est inaccessible.
    Ne retourne jamais si une mise à jour est appliquée (os.execv remplace le processus).
    """
    if os.getenv("UPDATE_CHECK_ENABLED", "0").strip() != "1":
        return

    try:
        # Vérifier que le repo git existe (on n'est pas dans un binaire PyInstaller seul)
        _git("rev-parse", "--is-inside-work-tree")
    except Exception:
        log.debug("[UPDATE] Pas dans un repo git, update_checker ignoré.")
        return

    try:
        log.info("[UPDATE] Vérification des mises à jour...")
        _git("fetch", "origin", "--quiet")

        head = _git("rev-parse", "HEAD")
        remote = _git("rev-parse", "origin/main")

        if head == remote:
            log.info("[UPDATE] Code à jour (%s).", head[:8])
            return

        log.info("[UPDATE] Nouvelle version détectée : %s -> %s. Application...", head[:8], remote[:8])
        _git("pull", "origin", "main", "--ff-only", "--quiet")
        log.info("[UPDATE] Pull OK. Relancement du bot...")

        # Supprimer le PID avant de se relancer pour que launch_all.ps1
        # puisse correctement détecter le processus comme nouveau.
        _delete_pid(account_id)

        # Remplacer le processus courant par lui-même avec les mêmes arguments.
        # os.execv ne retourne jamais en cas de succès.
        os.execv(sys.executable, [sys.executable] + sys.argv)

    except subprocess.TimeoutExpired:
        log.warning("[UPDATE] git timeout — mise à jour ignorée pour ce cycle.")
    except Exception as e:
        log.warning("[UPDATE] Échec mise à jour, bot continue sans relance : %s", e)
