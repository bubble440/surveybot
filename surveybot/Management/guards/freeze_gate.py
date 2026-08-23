# freeze_gate.py
"""
Point de gel unique pour la phase d'observation prod (FREEZE_ON_TRIGGER=1).

Contexte : plusieurs mécanismes automatiques indépendants (reprise de survey sur
retour plateforme, redémarrage applicatif déclenché depuis la boucle de résolution,
surveillance périodique de RuntimeGuard dans son thread daemon, fermeture/relance
du navigateur en fin de cycle main()) convergent tous vers une action consé-
quente sans coordination entre eux, produisant un enchaînement incontrôlé de
cycles en test. freeze_and_wait() est LE point de contrôle unique appelé par
chacun de ces déclencheurs juste avant l'action qu'il s'apprête à effectuer :
il journalise le déclencheur puis bloque le thread appelant jusqu'à la pose
d'un marqueur de reprise à usage unique (pids\\bot_<account_id>.freeze_resume,
cf. bot_supervisor.py), sur le même principe que le marqueur bot_<id>.manual_stop
déjà existant (stop_bot_manual.ps1). Une fois le marqueur consommé, l'appelant
reprend exactement l'action qu'il s'apprêtait à effectuer — le gel est un point
d'arrêt d'observation, pas une annulation de l'action.

Désactivé par défaut (FREEZE_ON_TRIGGER absent ou 0) : freeze_and_wait() est
alors un no-op immédiat, comportement strictement inchangé.

Exception assumée à la règle générale "toute boucle a un budget max N avec
abandon contrôlé" : l'attente ici est délibérément non bornée — un abandon
automatique reproduirait exactement l'enchaînement incontrôlé que ce mécanisme
sert à éliminer. Le seul déblocage possible est le marqueur de reprise explicite.
"""
from __future__ import annotations
import time
from config import is_freeze_mode_enabled
from Survey.log_utils import log_info, log_debug

_POLL_INTERVAL_SEC = 5
_DEBUG_REMINDER_EVERY_TICKS = 24  # ~2 min entre deux rappels debug


def freeze_and_wait(account_id: str, trigger: str) -> None:
    """
    No-op si FREEZE_ON_TRIGGER désactivé. Sinon, journalise `trigger` puis
    bloque jusqu'à la pose du marqueur de reprise pour `account_id` (posé via
    resume_bot_freeze.ps1), consommé (supprimé) dès sa détection.
    """
    if not is_freeze_mode_enabled():
        return

    from bot_supervisor import consume_freeze_resume_marker

    log_info(
        "[FREEZE]",
        f"account={account_id} trigger={trigger!r} — exécution figée, "
        "en attente du marqueur de reprise (resume_bot_freeze.ps1)",
    )

    ticks = 0
    while not consume_freeze_resume_marker(account_id):
        ticks += 1
        if ticks % _DEBUG_REMINDER_EVERY_TICKS == 0:
            log_debug(
                "[FREEZE]",
                f"account={account_id} trigger={trigger!r} — toujours figé "
                f"({ticks * _POLL_INTERVAL_SEC}s écoulées)",
            )
        time.sleep(_POLL_INTERVAL_SEC)

    log_info("[FREEZE]", f"account={account_id} trigger={trigger!r} — marqueur détecté, reprise")
