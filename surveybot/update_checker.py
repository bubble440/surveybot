"""
update_checker.py
Auto-update — deux mécanismes selon le contexte d'exécution, même manifeste
(version/url/sha256) et même logique de vérification/version dans les deux cas.

PARC INTERNE (phase déploiement interne, bare metal) :
Le bot tourne en Python interprété (venv\\Scripts\\python.exe code\\main.py),
pas depuis un .exe Nuitka. L'update remplace l'intégralité du dossier code\\
contenant les sources (_replace_source_and_restart / _swap_code_dir).

PIPELINE TIERS/RÉCEPTEURS (binaire Nuitka onefile, nuitka_build_release.ps1) :
Correction (24/07/2026) — _replace_source_and_restart ne peut pas fonctionner
dans ce contexte : sys.executable et __file__ pointent vers le dossier
d'extraction temporaire du onefile (%TEMP%\\onefile_...), pas vers l'exe
distribué réel — même constat déjà établi et exploité dans
preselection/secret_loader.py::_bot_root_dirs(). Détection via la variable
NUITKA_ONEFILE_BINARY (même mécanisme que secret_loader.py) : si présente,
_replace_exe_and_restart() remplace le binaire lui-même (adapté de
l'implémentation pré-pivot de ce fichier, qui utilisait sys.executable — donc
sujette au même piège avant même la migration Nuitka).

Layout attendu sur chaque machine (voir launch_all.ps1 / nssm_setup_bot.ps1) :
  C:\\surveybot\\                  racine persistante — jamais touchée par l'update
      accounts.json, receiver_config.json, profiles\\, pids\\, logs\\, venv\\
      code\\                       <- ce dossier est intégralement remplacé
          main.py, launch.py, update_checker.py, global_config.py,
          _license_config.py, Survey\\, Management\\, preselection\\, captcha\\, ...

Actif uniquement si UPDATE_CHECK_ENABLED=1 et UPDATE_MANIFEST_URL défini (inchangé
par rapport à l'ancienne version).

Logique :
  1. Télécharger UPDATE_MANIFEST_URL (JSON) — contient version, url (zip), sha256.
  2. Comparer avec BOT_VERSION (embarquée dans _license_config.py, dans code\\ courant).
  3. Si identiques -> rien à faire, on continue.
  4. Si différents  -> télécharger le zip, vérifier SHA256, extraire dans un dossier
                       temporaire, swap atomique du dossier code\\ (rename code -> code.old,
                       rename staging -> code), supprimer le PID, os.execv() pour relancer.
  5. Si inaccessible, hash invalide, ou zip incomplet -> log + ignorer, réessaie au
     prochain cycle. Le dossier code\\ courant n'est JAMAIS touché tant que la
     nouvelle version n'est pas intégralement extraite et validée sur le disque.

Format du manifeste (JSON hébergé sur R2, GitHub Releases, ou tout HTTP public) —
inchangé dans sa forme, seul le contenu de "url" change (zip au lieu d'exe) :
  {
    "version": "1.2.3",
    "url": "https://your-bucket.r2.dev/surveybot-code-1.2.3.zip",
    "sha256": "abcdef1234..."
  }

Variables d'environnement (inchangées) :
  UPDATE_CHECK_ENABLED  = "1"           — active la vérification
  UPDATE_MANIFEST_URL   = "https://..." — URL du manifeste JSON
  BOT_VERSION           = "1.0.0"       — version courante (embarquée dans _license_config.py)

Note : cette fonction est un no-op complet si UPDATE_CHECK_ENABLED != "1"
ou si le manifeste est inaccessible. Elle ne bloque jamais le bot en cas d'échec.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import ssl
import sys
import tempfile
import time
import urllib.request
import urllib.error
import json
import zipfile

log = logging.getLogger("update_checker")

_HTTP_TIMEOUT = 15  # secondes
_MAX_ZIP_ENTRIES = 20000  # garde-fou : borne la taille d'archive traitée

# check_and_apply() est appelé au tout début du process, avant tout autre accès
# réseau applicatif. Sur une machine qui vient de démarrer (boot ou redémarrage
# du service), la pile réseau/DNS locale peut ne pas être stabilisée pendant les
# tout premiers instants : la résolution DNS de UPDATE_MANIFEST_URL échoue alors
# que le réseau est en réalité sur le point de devenir disponible (les connexions
# suivantes du même run, quelques secondes plus tard, réussissent normalement).
# Budget borné pour absorber cette fenêtre sans retarder significativement le
# démarrage ni abandonner silencieusement alors que le réseau est sur le point
# de revenir.
_MANIFEST_FETCH_MAX_ATTEMPTS = 3
_MANIFEST_FETCH_RETRY_DELAY = 2  # secondes entre tentatives

# Contexte SSL explicite base sur le bundle de certificats certifi, plutot que de
# dependre du magasin de certificats racine de Windows (ssl.create_default_context()
# sans cafile) : sur une machine fraichement installee (avant tout Windows Update),
# ce magasin est souvent incomplet et fait echouer la verification TLS avec
# "unable to get local issuer certificate", meme quand le certificat distant est valide.
# Fallback sur le contexte par defaut si certifi n'est pas installe (ne devrait pas
# arriver en prod, requirements.txt l'inclut, mais coherent avec les autres imports
# optionnels de ce fichier).
try:
    import certifi
    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CONTEXT = ssl.create_default_context()

# UPDATE_CHECK_ENABLED / UPDATE_MANIFEST_URL sont des variables GLOBAL_CONFIG : elles
# proviennent de global_config.py (dossier code\\ courant). En dev/attach (global_config.py
# absent du projet), fallback os.getenv.
try:
    from global_config import UPDATE_CHECK_ENABLED, UPDATE_MANIFEST_URL  # type: ignore
except ImportError:
    UPDATE_CHECK_ENABLED = os.getenv("UPDATE_CHECK_ENABLED", "0")
    UPDATE_MANIFEST_URL = os.getenv("UPDATE_MANIFEST_URL", "")


def _is_onefile_binary() -> bool:
    """
    True si ce process tourne depuis un binaire Nuitka onefile compilé
    (pipeline tiers/récepteurs). Même variable, même raison que
    preselection/secret_loader.py::_bot_root_dirs() : NUITKA_ONEFILE_BINARY
    est fiable pour retrouver l'exe réel ; sys.executable/__file__ ne le sont
    pas dans ce mode.
    """
    return bool(os.environ.get("NUITKA_ONEFILE_BINARY", "").strip())


def _current_version() -> str:
    """Version courante du code — lue depuis _license_config ou BOT_VERSION env."""
    try:
        from _license_config import BOT_VERSION  # type: ignore
        return (BOT_VERSION or "").strip()
    except ImportError:
        pass
    return os.getenv("BOT_VERSION", "").strip()


def _code_dir() -> str:
    """Dossier code\\ courant — celui qui contient CE fichier (chemin absolu)."""
    return os.path.dirname(os.path.abspath(__file__))


def _fetch_manifest(url: str) -> dict:
    """Télécharge et parse le manifeste JSON distant."""
    req = urllib.request.Request(url, headers={"User-Agent": "SurveyBot-Updater/1.0"})
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT, context=_SSL_CONTEXT) as resp:
        # decode("utf-8-sig") : tolère un BOM UTF-8 optionnel en tête de fichier
        # (certains outils Windows en ajoutent un à la génération/édition du
        # manifeste) sans dépendre d'une convention d'encodage imposée côté
        # hébergement — comportement identique avec ou sans BOM.
        return json.loads(resp.read().decode("utf-8-sig"))


def _fetch_manifest_with_early_boot_retry(url: str) -> dict:
    """
    Enrobe _fetch_manifest() d'un budget de réessais borné (_MANIFEST_FETCH_MAX_ATTEMPTS,
    pause _MANIFEST_FETCH_RETRY_DELAY entre chaque tentative), pour tolérer une
    indisponibilité réseau transitoire limitée aux tout premiers instants suivant le
    démarrage du process (voir commentaire sur les constantes). N'affecte que ce premier
    accès réseau ; _fetch_manifest() elle-même n'est pas modifiée.
    """
    last_exc: urllib.error.URLError | None = None
    for attempt in range(1, _MANIFEST_FETCH_MAX_ATTEMPTS + 1):
        try:
            return _fetch_manifest(url)
        except urllib.error.URLError as e:
            last_exc = e
            if attempt < _MANIFEST_FETCH_MAX_ATTEMPTS:
                log.debug(
                    "[UPDATE] Manifeste inaccessible (tentative %s/%s, réseau probablement "
                    "pas encore stabilisé après démarrage) : %s — nouvel essai dans %ss.",
                    attempt, _MANIFEST_FETCH_MAX_ATTEMPTS, e, _MANIFEST_FETCH_RETRY_DELAY,
                )
                time.sleep(_MANIFEST_FETCH_RETRY_DELAY)
    raise last_exc


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _download_zip(url: str, dest: str) -> None:
    """Télécharge le zip du nouveau code vers dest."""
    req = urllib.request.Request(url, headers={"User-Agent": "SurveyBot-Updater/1.0"})
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT, context=_SSL_CONTEXT) as resp, \
         open(dest, "wb") as out:
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            out.write(chunk)


def _extract_zip_flat(zip_path: str, dest_dir: str) -> None:
    """
    Extrait le zip dans dest_dir. Si l'archive contient un unique dossier racine
    (ex. tous les chemins préfixés par 'code/'), on aplatit son contenu directement
    dans dest_dir — évite toute dépendance à la convention exacte du script de build
    qui a produit le zip côté R2.
    Garde-fous : nombre d'entrées borné, aucune évasion hors de dest_dir (path traversal).
    """
    os.makedirs(dest_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = [n for n in zf.namelist() if n.strip("/")]
        if not names or len(names) > _MAX_ZIP_ENTRIES:
            raise ValueError(f"Zip suspect ({len(names)} entrées) — extraction annulée.")

        top_level = {n.split("/", 1)[0] for n in names}
        single_root = len(top_level) == 1

        for member in names:
            if member.endswith("/"):
                continue
            norm = os.path.normpath(member)
            if norm.startswith("..") or os.path.isabs(norm):
                raise ValueError(f"Entrée zip invalide (path traversal) : {member}")

            rel = member.split("/", 1)[1] if single_root else member
            if not rel:
                continue

            target = os.path.join(dest_dir, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(member) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


def _swap_code_dir(new_code_dir: str) -> None:
    """
    Remplace le dossier code\\ courant par new_code_dir. L'ancien code\\ est
    renommé en code.old le temps du swap (rollback automatique en cas d'échec
    du rename du nouveau dossier), puis supprimé dès que le swap est confirmé
    réussi — pas de sauvegarde persistante entre deux updates.

    Suppose que le processus courant tourne avec un cwd EXTÉRIEUR à code\\
    (AppDirectory = C:\\surveybot, pas code\\) : sur Windows, un dossier qui est
    le cwd d'un process ne peut pas être renommé ni supprimé.
    """
    code_dir = _code_dir()
    root_dir = os.path.dirname(code_dir)
    old_dir = os.path.join(root_dir, "code.old")

    if os.path.exists(old_dir):
        shutil.rmtree(old_dir, ignore_errors=True)

    os.rename(code_dir, old_dir)
    try:
        os.rename(new_code_dir, code_dir)
    except Exception:
        # Rollback best-effort : ne jamais laisser la machine sans dossier code\\.
        os.rename(old_dir, code_dir)
        raise

    # Swap confirmé réussi (nouveau code en place, aucune exception levée) :
    # la sauvegarde n'a plus de raison de rester sur le disque en permanence.
    shutil.rmtree(old_dir, ignore_errors=True)


def _replace_source_and_restart(zip_path: str, account_id: str) -> None:
    """
    Extrait le zip vers un dossier temporaire, swap avec code\\, nettoie le PID,
    puis os.execv() pour relancer avec le nouveau code.
    Ne retourne jamais si le swap et le re-exec réussissent.
    """
    code_dir = _code_dir()
    root_dir = os.path.dirname(code_dir)
    staging_dir = os.path.join(root_dir, "code_new_tmp")

    if os.path.exists(staging_dir):
        shutil.rmtree(staging_dir, ignore_errors=True)

    _extract_zip_flat(zip_path, staging_dir)

    # Garde-fou minimal avant tout swap : un zip tronqué/mal formé ne doit
    # jamais écraser un code\\ courant qui fonctionne.
    if not os.path.isfile(os.path.join(staging_dir, "main.py")):
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise ValueError("main.py absent du zip extrait — swap annulé, code\\ courant conservé.")

    _swap_code_dir(staging_dir)

    log.info("[UPDATE] Code remplacé. Relancement...")

    # Supprimer le PID pour éviter un faux "déjà actif" dans launch_all.ps1
    try:
        pid_path = os.path.join(root_dir, "pids", f"bot_{account_id}.pid")
        if os.path.exists(pid_path):
            os.remove(pid_path)
    except Exception as e:
        log.warning("[UPDATE] Impossible de supprimer le PID avant re-exec : %s", e)

    # Même chemin absolu qu'avant le swap (seul le contenu a changé) : on peut
    # réutiliser sys.executable (l'interpréteur python, pas un exe applicatif) tel quel.
    python_exe = sys.executable
    main_py = os.path.join(code_dir, "main.py")
    os.execv(python_exe, [python_exe, main_py] + sys.argv[1:])


def _replace_exe_and_restart(new_exe: str, account_id: str) -> None:
    """
    Remplace le binaire onefile Nuitka courant par new_exe et relance le
    processus. Utilisé uniquement quand _is_onefile_binary() est vrai (pipeline
    tiers/récepteurs) — jamais pour le parc interne, qui passe par
    _replace_source_and_restart() ci-dessus, inchangée.

    Adapté de l'implémentation pré-pivot de ce fichier (avant le 20/07/2026,
    voir git log), qui utilisait déjà cette stratégie renommer-copier-relancer
    mais avec sys.executable comme référence de l'exe courant — non fiable en
    onefile Nuitka (voir docstring du module). Seul changement : NUITKA_ONEFILE_BINARY
    remplace sys.executable comme source de vérité pour le chemin de l'exe réel,
    même mécanisme que preselection/secret_loader.py::_bot_root_dirs().
    shutil est déjà importé au niveau module (utilisé par _extract_zip_flat/
    _swap_code_dir ci-dessus), pas besoin de le réimporter ici.
    """
    current_exe = os.environ.get("NUITKA_ONEFILE_BINARY", "").strip()
    if not current_exe or not os.path.isfile(current_exe):
        raise RuntimeError(
            "NUITKA_ONEFILE_BINARY absent ou invalide — remplacement du binaire "
            "annulé (sys.executable n'est pas fiable pour cette opération en "
            "onefile Nuitka)."
        )

    old_exe = current_exe + ".old"

    # Supprimer un éventuel .old résiduel du cycle précédent
    try:
        if os.path.exists(old_exe):
            os.remove(old_exe)
    except Exception as e:
        log.warning("[UPDATE] Impossible de supprimer l'ancien .old : %s", e)

    # Renommer l'exe courant en .old (Windows autorise le renommage d'un exe en
    # cours d'exécution, pas son écrasement direct), copier le nouveau à sa place.
    os.rename(current_exe, old_exe)
    shutil.copy2(new_exe, current_exe)

    log.info("[UPDATE] Binaire remplacé. Relancement...")

    # Supprimer le PID pour éviter un faux "déjà actif" dans launch_all.ps1
    try:
        base = os.path.dirname(current_exe)
        pid_path = os.path.join(base, "pids", f"bot_{account_id}.pid")
        if os.path.exists(pid_path):
            os.remove(pid_path)
    except Exception as e:
        log.warning("[UPDATE] Impossible de supprimer le PID avant re-exec : %s", e)

    # Relance avec l'exe réel (pas sys.executable) — ne retourne jamais.
    os.execv(current_exe, [current_exe] + sys.argv[1:])


def check_and_apply(account_id: str) -> None:
    """
    Vérifie si une mise à jour du code est disponible et l'applique si oui.
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

        manifest = _fetch_manifest_with_early_boot_retry(manifest_url)
        remote_version = manifest.get("version", "").strip()
        remote_url     = manifest.get("url", "").strip()
        remote_sha256  = manifest.get("sha256", "").strip().lower()

        if not remote_version or not remote_url or not remote_sha256:
            log.warning("[UPDATE] Manifeste invalide (champs manquants) — ignoré.")
            return

        if current_version and current_version == remote_version:
            log.info("[UPDATE] Code à jour (%s).", current_version)
            return

        log.info("[UPDATE] Nouvelle version disponible : %s -> %s. Téléchargement...",
                 current_version or "?", remote_version)

        # Télécharger dans un fichier temporaire. Suffixe informatif uniquement
        # (zip pour le parc interne, exe pour le pipeline tiers/récepteurs) —
        # _download_zip() est un téléchargeur générique malgré son nom (simple
        # flux d'octets vers un fichier), réutilisable tel quel dans les deux cas.
        onefile = _is_onefile_binary()
        tmp_suffix = ".exe" if onefile else ".zip"
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=tmp_suffix, prefix="surveybot_update_")
        os.close(tmp_fd)
        try:
            _download_zip(remote_url, tmp_path)

            # Vérifier l'intégrité
            actual_sha256 = _sha256_file(tmp_path)
            if actual_sha256 != remote_sha256:
                log.error(
                    "[UPDATE] SHA256 invalide (attendu=%s, reçu=%s) — mise à jour annulée.",
                    remote_sha256, actual_sha256,
                )
                return

            log.info("[UPDATE] SHA256 OK. Application de la mise à jour...")
            # Un seul point de branchement : pipeline tiers (binaire onefile)
            # vs parc interne (source+venv, comportement inchangé).
            if onefile:
                _replace_exe_and_restart(tmp_path, account_id)
            else:
                _replace_source_and_restart(tmp_path, account_id)
            # Ne retourne pas si l'une ou l'autre réussit

        finally:
            # Nettoyage du fichier temporaire si on n'a pas relancé (erreur ou version identique)
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass

    except urllib.error.URLError as e:
        log.warning("[UPDATE] Manifeste inaccessible — mise à jour ignorée : %s", e)
    except Exception as e:
        log.warning("[UPDATE] Échec mise à jour, bot continue sans relance : %s", e)