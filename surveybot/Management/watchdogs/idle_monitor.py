# idle_monitor.py
# Surveille le solde TopSurveys et envoie une alerte si aucune hausse n'est constatée pendant N minutes.
# - Thread daemon autonome (ne bloque pas l'agent)
# - Anti-spam : une seule notification tant qu'aucun nouveau gain n'est observé
# - Lecture du solde : réutilise payout._read_balance(driver)
from __future__ import annotations
import threading, time, traceback
from typing import Callable, Optional
import Cash.payout as payout  # on réutilise _read_balance(driver) et son parsing robuste
from ..guards.runtime_guard import get_guard
from ..guards.runtime_guard import StopReason

class GainWatchdog:
    """
    Surveille périodiquement le solde. Si aucun gain pendant `threshold_sec`,
    déclenche notify_fn(message) une fois, puis attend une nouvelle hausse pour réarmer l'alerte.
    """
    def __init__(
        self,
        driver,
        threshold_sec: int = 900,     # 15 min
        poll_seconds: int = 900,        # intervalle de sondage
        notify_fn: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.driver = driver
        self.threshold_sec = int(threshold_sec)
        self.poll_seconds = max(10, int(poll_seconds))  # garde-fou
        self.notify_fn = notify_fn or (lambda msg: print(f"[WATCHDOG] {msg}"))

        self._t: Optional[threading.Thread] = None
        self._stop = False
        self._armed = True  # prêt à notifier
        self._last_balance = None
        self._last_gain_ts = time.time()

    def _notify(self, msg: str) -> None:
        try:
            self.notify_fn(msg)
        except Exception:
            # on ne casse jamais le thread si la notif échoue
            traceback.print_exc()

    def _tick_once(self) -> None:
        try:
            # Essaye de lire le solde (peut échouer si on est sur une autre page)
            try:
                bal = payout._read_balance(self.driver)  # 5,57 € -> 5.57  (helper existant)
            except Exception:
                # ex: widget absent sur cette page → on ignore ce tick
                return

            now = time.time()
            if self._last_balance is None:
                self._last_balance = bal
                self._last_gain_ts = now
                return

            # gain observé ?
            if bal > self._last_balance:
                self._last_balance = bal
                self._last_gain_ts = now
                self._armed = True  # réarme la possibilité de notifier si re-inactivité
                return

            # Pas de hausse : vérifie la durée d'inactivité
            if self._armed and (now - self._last_gain_ts) >= self.threshold_sec:
                # ⛔ Aucun gain depuis threshold_sec
                print(f"[IDLE_GAIN] Aucun gain depuis {self.threshold_sec}s → pause 15 min")

                get_guard().signal_no_gain()
                self.stop()
                self._armed = False  # éviter le spam jusqu'à prochaine hausse

        except Exception:
            # Rien ne doit faire tomber le thread
            traceback.print_exc()

    def _loop(self) -> None:
        while not self._stop:
            self._tick_once()
            time.sleep(self.poll_seconds)

    def start(self) -> None:
        if self._t and self._t.is_alive():
            return
        self._stop = False
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def stop(self) -> None:
        self._stop = True

def start_idle_gain_watch(
    driver,
    threshold_sec: int = 900,
    check_every: int = 60,
    notify_fn: Optional[Callable[[str], None]] = None,
) -> GainWatchdog:
    """
    Helper pratique : instancie + démarre le watchdog et retourne l'instance.
    """
    wd = GainWatchdog(
        driver=driver,
        threshold_sec=threshold_sec,
        poll_seconds=check_every,
        notify_fn=notify_fn,
    )
    wd.start()
    return wd
