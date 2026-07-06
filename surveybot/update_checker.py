"""
update_checker.py
Auto-update du binaire PyInstaller depuis une URL distante (Option A2).

Actif uniquement si UPDATE_CHECK_ENABLED=1 et UPDATE_MANIFEST_URL défini.
Point d'appel : run_main_loop() dans launch.py, entre deux cycles survey.

Logique :
  1. Télécharger UPDATE_MANIFEST_URL (JSON) — contient version, url, sha256.
  2. Comparer avec BOT_VERSION (embarquée dans le compilé via _license_config).
  3. Si identiques -> rien à faire, on continue.
  4. Si différents  -> télécharger le nouveau .exe, vérifier SHA256,
                       remplacer l'exécutable courant, supprimer le PID,
                       os.execv() pour relancer avec le nouveau binaire.
  5. Si inaccessible ou hash invalide -> log + ignorer, réessayer au prochain cycle.

Format du manifeste (JSON hébergé sur R2, GitHub Releases, ou tout HTTP public) :
  {
    "version": "1.2.3",
    "url": "https://your-bucket.r2.dev/surveybot-1.2.3.exe",
    "sha256": "abcdef1234..."
  }

Variables d'environnement :
  UPDATE_CHECK_ENABLED  = "1"           — active la vérification
  UPDATE_MANIFEST_URL   = "https://..." — URL du manifeste JSON
  BOT_VERSION           = "1.0.0"       — version courante (embarquée dans le compilé)

Note : cette fonction est un no-op complet si UPDATE_CHECK_ENABLED != "1"
ou si le manifeste est inaccessible. Elle ne bloque jamais le bot en cas d'échec.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import tempfile
import urllib.request
import urllib.error
import json

log = logging.getLogger("update_checker")

_HTTP_TIMEOUT = 15  # secondes

# UPDATE_CHECK_ENABLED / UPDATE_MANIFEST_URL sont des variables GLOBAL_CONFIG : en
# build compilé (Nuitka), elles proviennent exclusivement de global_config.py, jamais
# de l'environnement du process (cf. config.py). En dev/attach (global_config.py
# absent du projet), fallback os.getenv.
try:
    from global_config import UPDATE_CHECK_ENABLED, UPDATE_MANIFEST_URL  # type: ignore
except ImportError:
    UPDATE_CHECK_ENABLED = os.getenv("UPDATE_CHECK_ENABLED", "0")
    UPDATE_MANIFEST_URL = os.getenv("UPDATE_MANIFEST_URL", "")


def _current_version() -> str:
    """Version courante du binaire — lue depuis _license_config ou BOT_VERSION env."""
    try:
        from _license_config import BOT_VERSION  # type: ignore
        return (BOT_VERSION or "").strip()
    except ImportError:
        pass
    return os.getenv("BOT_VERSION", "").strip()


def _fetch_manifest(url: str) -> dict:
    """Télécharge et parse le manifeste JSON distant."""
    req = urllib.request.Request(url, headers={"User-Agent": "SurveyBot-Updater/1.0"})
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _download_exe(url: str, dest: str) -> None:
    """Télécharge le nouveau binaire vers dest."""
    req = urllib.request.Request(url, headers={"User-Agent": "SurveyBot-Updater/1.0"})
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp, \
         open(dest, "wb") as out:
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            out.write(chunk)


def _replace_exe_and_restart(new_exe: str, account_id: str) -> None:
    """
    Remplace le binaire courant par new_exe et relance le processus.
    Sur Windows, on ne peut pas écraser un .exe en cours d'exécution directement.
    Stratégie : renommer l'ancien en .old, copier le nouveau à sa place, relancer.
    L'ancien .old sera supprimé au prochain démarrage.
    """
    import shutil

    current_exe = sys.executable
    old_exe = current_exe + ".old"

    # Supprimer un éventuel .old résiduel du cycle précédent
    try:
        if os.path.exists(old_exe):
            os.remove(old_exe)
    except Exception as e:
        log.warning("[UPDATE] Impossible de supprimer l'ancien .old : %s", e)

    # Renommer l'exe courant en .old
    os.rename(current_exe, old_exe)

    # Copier le nouveau binaire à l'emplacement de l'exe courant
    shutil.copy2(new_exe, current_exe)

    log.info("[UPDATE] Binaire remplacé. Relancement...")

    # Supprimer le PID pour éviter un faux "déjà actif" dans launch_all.ps1
    try:
        base = os.path.dirname(os.path.abspath(current_exe))
        pid_path = os.path.join(base, "pids", f"bot_{account_id}.pid")
        if os.path.exists(pid_path):
            os.remove(pid_path)
    except Exception as e:
        log.warning("[UPDATE] Impossible de supprimer le PID avant re-exec : %s", e)

    # Remplacer le processus courant — ne retourne jamais
    os.execv(current_exe, [current_exe] + sys.argv[1:])


def check_and_apply(account_id: str) -> None:
    """
    Vérifie si une mise à jour binaire est disponible et l'applique si oui.
    No-op si UPDATE_CHECK_ENABLED != "1" ou si UPDATE_MANIFEST_URL est absent.
    Ne retourne jamais si une mise à jour est appliquée (os.execv remplace le processus).
    """
    if UPDATE_CHECK_ENABLED.strip() != "1":
        return

    manifest_url = UPDATE_MANIFEST_URL.strip()
    if not manifest_url:
        log.debug("[UPDATE] UPDATE_MANIFEST_URL non défini — update ignoré.")
        return

    current_version = _current_version()

    try:
        log.info("[UPDATE] Vérification des mises à jour (version courante : %s)...",
                 current_version or "inconnue")

        manifest = _fetch_manifest(manifest_url)
        remote_version = manifest.get("version", "").strip()
        remote_url     = manifest.get("url", "").strip()
        remote_sha256  = manifest.get("sha256", "").strip().lower()

        if not remote_version or not remote_url or not remote_sha256:
            log.warning("[UPDATE] Manifeste invalide (champs manquants) — ignoré.")
            return

        if current_version and current_version == remote_version:
            log.info("[UPDATE] Binaire à jour (%s).", current_version)
            return

        log.info("[UPDATE] Nouvelle version disponible : %s -> %s. Téléchargement...",
                 current_version or "?", remote_version)

        # Télécharger dans un fichier temporaire
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".exe", prefix="surveybot_update_")
        os.close(tmp_fd)
        try:
            _download_exe(remote_url, tmp_path)

            # Vérifier l'intégrité
            actual_sha256 = _sha256_file(tmp_path)
            if actual_sha256 != remote_sha256:
                log.error(
                    "[UPDATE] SHA256 invalide (attendu=%s, reçu=%s) — mise à jour annulée.",
                    remote_sha256, actual_sha256,
                )
                return

            log.info("[UPDATE] SHA256 OK. Application de la mise à jour...")
            _replace_exe_and_restart(tmp_path, account_id)
            # Ne retourne pas si _replace_exe_and_restart réussit

        finally:
            # Nettoyage du temporaire si on n'a pas relancé (erreur ou version identique)
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass

    except urllib.error.URLError as e:
        log.warning("[UPDATE] Manifeste inaccessible — mise à jour ignorée : %s", e)
    except Exception as e:
        log.warning("[UPDATE] Échec mise à jour, bot continue sans relance : %s", e)