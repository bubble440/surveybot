# bot_supervisor.py
"""
Suivi d'état local par bot (fichier JSON dans pids/).
Responsabilités :
  - heartbeat local → détection zombie par tâche planifiée (check_zombie_bots.ps1)
  - compteur de redémarrages → protection contre les crash-loops
  - codes de sortie normalisés → NSSM sait quand redémarrer ou non
"""
from __future__ import annotations
import json, os, time

# ---------------------------------------------------------------------------
# Codes de sortie normalisés — communiqués à NSSM via AppExit
# ---------------------------------------------------------------------------
EXIT_VOLUNTARY    = 0  # arrêt volontaire (SIGTERM, target journalier…) → pas de restart
EXIT_CRASH        = 1  # crash / sortie inattendue → restart (code Python par défaut)
EXIT_SOFT_RESTART = 2  # redémarrage intentionnel rapide (idle, erreurs…) → restart
EXIT_FATAL        = 3  # seuil de crashes dépassé → pas de restart, alerte Telegram

# Seuils par défaut (surchargeables via check_and_record_start)
_DEFAULT_MAX_RESTARTS = 5
_DEFAULT_WINDOW_SEC   = 600   # 10 minutes


# ---------------------------------------------------------------------------
# Résolution du dossier pids/
# ---------------------------------------------------------------------------

def _pids_dir() -> str:
    """
    Résout le dossier pids/ via _bot_root_dirs() de secret_loader — même logique
    de priorité que receiver_config.json, sans chemin en dur supplémentaire.
    """
    from preselection.secret_loader import _bot_root_dirs
    for d in _bot_root_dirs():
        candidate = os.path.join(d, "pids")
        if os.path.isdir(candidate):
            return candidate
    # Aucun pids/ existant → créer sous le premier candidat (C:\surveybot\ en prod)
    first = _bot_root_dirs()[0]
    path = os.path.join(first, "pids")
    os.makedirs(path, exist_ok=True)
    return path


def _state_path(account_id: str) -> str:
    return os.path.join(_pids_dir(), f"bot_{account_id}.state")


def _manual_stop_path(account_id: str) -> str:
    return os.path.join(_pids_dir(), f"bot_{account_id}.manual_stop")


def _freeze_resume_path(account_id: str) -> str:
    return os.path.join(_pids_dir(), f"bot_{account_id}.freeze_resume")


def _frozen_path(account_id: str) -> str:
    return os.path.join(_pids_dir(), f"bot_{account_id}.frozen")


# ---------------------------------------------------------------------------
# Lecture / écriture de l'état JSON
# ---------------------------------------------------------------------------

def _read_state(account_id: str) -> dict:
    path = _state_path(account_id)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_state(account_id: str, state: dict) -> None:
    path = _state_path(account_id)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"[SUPERVISOR][WARN] Impossible d'écrire {path}: {e}")


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------

def write_heartbeat(account_id: str) -> None:
    """
    Met à jour l'horodatage du dernier heartbeat dans le fichier d'état.
    Appelé depuis RuntimeGuard.heartbeat() toutes les ~60 s.
    Le script check_zombie_bots.ps1 détecte les bots zombies en comparant
    ce timestamp à l'heure courante.
    """
    state = _read_state(account_id)
    state["last_heartbeat_ts"] = time.time()
    state["pid"]               = os.getpid()
    state["account_id"]        = account_id
    _write_state(account_id, state)


def record_exit(account_id: str, exit_code: int, reason: str) -> None:
    """
    Enregistre le code de sortie réel juste avant que le process quitte.
    Écrase le sentinel EXIT_CRASH positionné par check_and_record_start()
    au démarrage — si ce fichier n'est jamais appelé (kill forcé, OOM…),
    le sentinel reste à EXIT_CRASH, ce qui est le comportement correct pour
    NSSM (il restartera le bot comme après un crash).
    """
    state = _read_state(account_id)
    state["last_exit_code"]   = exit_code
    state["last_exit_reason"] = reason
    state["last_exit_ts"]     = time.time()
    _write_state(account_id, state)


def clear_manual_stop_marker(account_id: str) -> None:
    """
    Supprime le marqueur d'arrêt manuel posé par stop_bot_manual.ps1, si présent.

    Ce marqueur est distinct du cooldown Postgres : il sert uniquement à
    wake_scheduler.ps1 pour ne pas relancer un bot volontairement arrêté par
    l'opérateur (contrairement au cooldown, un `nssm stop` seul ne suffit pas à
    le distinguer d'un arrêt de service Windows ordinaire, ex. redémarrage
    machine — voir stop_bot_manual.ps1). Appelée une seule fois, tout au début
    de chaque démarrage réel du bot (main.py) : ce démarrage, qu'il vienne d'un
    `nssm start` explicite ou d'un redémarrage machine qui relance le service
    NSSM, vaut reprise — wake_scheduler.ps1 n'a plus de raison d'ignorer ce
    compte ensuite.
    """
    path = _manual_stop_path(account_id)
    try:
        if os.path.isfile(path):
            os.remove(path)
            print(f"[SUPERVISOR] Marqueur d'arrêt manuel levé : {path}")
    except Exception as e:
        print(f"[SUPERVISOR][WARN] Impossible de supprimer {path}: {e}")


def purge_freeze_resume_marker(account_id: str) -> None:
    """
    Purge un marqueur de reprise du mode gel (FREEZE_ON_TRIGGER, cf.
    Management/guards/freeze_gate.py) résiduel d'un lancement précédent.

    Appelée une seule fois, tout au début du démarrage réel du bot, AVANT toute
    boucle de gel — même principe que clear_manual_stop_marker() : un marqueur
    oublié par l'opérateur (posé pour un point de gel qui n'a jamais été atteint,
    ou laissé après un arrêt) ne doit jamais débloquer à tort le premier point de
    gel rencontré par ce nouveau démarrage.
    """
    path = _freeze_resume_path(account_id)
    try:
        if os.path.isfile(path):
            os.remove(path)
            print(f"[SUPERVISOR] Marqueur de reprise gel résiduel purgé : {path}")
    except Exception as e:
        print(f"[SUPERVISOR][WARN] Impossible de purger {path}: {e}")


def consume_freeze_resume_marker(account_id: str) -> bool:
    """
    Marqueur de reprise du mode gel — usage unique : si présent, le supprime et
    retourne True ; sinon retourne False. Ne jamais laisser un marqueur déjà
    consommé débloquer un futur point de gel (cf. purge_freeze_resume_marker()
    pour le résidu d'un lancement précédent).
    """
    path = _freeze_resume_path(account_id)
    try:
        if os.path.isfile(path):
            os.remove(path)
            return True
    except Exception as e:
        print(f"[SUPERVISOR][WARN] Impossible de supprimer {path}: {e}")
    return False


def mark_account_frozen(account_id: str) -> None:
    """
    Pose le marqueur local (indépendant de Postgres) signalant qu'une instance de
    ce compte est actuellement gelée (FREEZE_ON_TRIGGER, cf.
    Management/guards/freeze_gate.py) et retient encore des ressources actives
    (profil Chrome, session). Posé juste avant d'entrer dans la boucle de blocage
    de freeze_and_wait(), levé via clear_account_frozen_marker() dès la sortie de
    cette boucle (marqueur de reprise consommé ou arrêt externe détecté).

    Consommé par acquire_account_lock_or_exit() (launch.py) : contrairement au
    slot Postgres (TTL 240s, dépend du heartbeat pour être prolongé), ce marqueur
    fichier reste valide tant que le process gelé est vivant, indépendamment de la
    disponibilité de Postgres pendant le gel.
    """
    path = _frozen_path(account_id)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"{os.getpid()}|{time.time()}")
    except Exception as e:
        print(f"[SUPERVISOR][WARN] Impossible de poser le marqueur de gel {path}: {e}")


def clear_account_frozen_marker(account_id: str) -> None:
    """Retire le marqueur posé par mark_account_frozen(), si présent."""
    path = _frozen_path(account_id)
    try:
        if os.path.isfile(path):
            os.remove(path)
    except Exception as e:
        print(f"[SUPERVISOR][WARN] Impossible de supprimer {path}: {e}")


def is_account_frozen(account_id: str) -> bool:
    """
    True si une instance de ce compte est actuellement gelée (marqueur posé par
    mark_account_frozen(), pas encore levé). Lu par acquire_account_lock_or_exit()
    avant toute tentative de démarrage — cf. mark_account_frozen() pour le détail.
    """
    return os.path.isfile(_frozen_path(account_id))


def check_and_record_start(
    account_id:   str,
    max_restarts: int = _DEFAULT_MAX_RESTARTS,
    window_sec:   int = _DEFAULT_WINDOW_SEC,
) -> tuple[bool, int]:
    """
    Appelée au démarrage du bot, après chaque redémarrage NSSM.
    Lit le dernier code de sortie pour décider si ce démarrage est dû à un
    crash/soft_restart (compté dans la fenêtre) ou à un arrêt volontaire
    (remise à zéro du compteur).

    Écrit immédiatement un sentinel EXIT_CRASH dans last_exit_code : si le
    process est tué de force avant d'appeler record_exit(), le prochain
    démarrage lira EXIT_CRASH et comptera correctement ce run comme un crash.

    Retourne (should_abort, restart_count).
    Si should_abort=True → l'appelant doit alerter via Telegram puis quitter
    avec sys.exit(EXIT_FATAL) pour que NSSM ne redémarre plus.
    """
    now   = time.time()
    state = _read_state(account_id)

    prev_exit_code = state.get("last_exit_code")   # code du run précédent
    window_start   = state.get("restart_window_start_ts", now)
    restart_count  = state.get("restart_count", 0)

    # Démarrage frais : précédent arrêt volontaire, fatal, ou premier démarrage
    if prev_exit_code in (EXIT_VOLUNTARY, EXIT_FATAL, None):
        restart_count = 0
        window_start  = now
    elif now - window_start > window_sec:
        # Fenêtre expirée → remise à zéro
        restart_count = 0
        window_start  = now
    else:
        # Redémarrage après crash/soft_restart dans la fenêtre → incrément
        restart_count += 1

    # Écriture de l'état courant avec le sentinel "crash" :
    # record_exit() l'écrasera avec le vrai code à la fin du run.
    state.update({
        "account_id":              account_id,
        "pid":                     os.getpid(),
        "restart_count":           restart_count,
        "restart_window_start_ts": window_start,
        "last_start_ts":           now,
        "last_exit_code":          EXIT_CRASH,   # sentinel — écrasé par record_exit()
        "last_exit_reason":        "running",
        "last_heartbeat_ts":       now,
    })
    _write_state(account_id, state)

    return restart_count >= max_restarts, restart_count
