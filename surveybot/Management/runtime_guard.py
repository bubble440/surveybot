# runtime_guard.py
"""
Superviseur central d'exécution.
Objectif : protéger OpenAI, AWS et Proxy (pay-as-you-use)
"""

from __future__ import annotations
import time, socket, os, threading, traceback
from dataclasses import dataclass, field
from typing import Optional, Callable
from enum import Enum
from State.account_state import load_state, update_state

def _is_prod_env() -> bool:
    return bool(
        os.getenv("AWS_EXECUTION_ENV")
        or os.getenv("ECS_CONTAINER_METADATA_URI")
        or os.getenv("ECS_CONTAINER_METADATA_URI_V4")
        or os.getenv("RUN_ENV") in ("aws", "docker")
    )

class StopReason(Enum):
    IDLE = "idle"
    TOO_MANY_ERRORS = "too_many_errors"
    NO_GAIN = "no_gain"
    RUNTIME_LIMIT = "runtime_limit"
    DAILY_TARGET_REACHED = "daily_target_reached"


@dataclass
class RuntimeState:
    start_ts: float = field(default_factory=time.time)
    last_activity_ts: float = field(default_factory=time.time)
    last_success_ts: float = field(default_factory=time.time)

    consecutive_errors: int = 0
    total_errors: int = 0

    surveys_completed_today: int = 0
    earnings_today_eur: float = 0.0

    openai_calls: int = 0

    stopped: bool = False


class RuntimeGuard:
    def __init__(
        self,
        *,
        account_id: str,
        idle_timeout_sec: int = 120,          # 2 minutes
        restart_cooldown_sec: int = 900,      # 15 minutes
        max_errors_in_row: int = 5,
        max_runtime_sec: int = 2 * 3600,      # 2h
        daily_target_eur: float = 5.0,
        notify_fn: Optional[Callable[[str], None]] = None,
        on_soft_restart: Optional[Callable[[str], None]] = None,
    ):
        self.account_id = account_id
        self.driver = None  # sera injecté après le lancement du navigateur
        self.state = RuntimeState()
        self.task_id = os.getenv("ECS_TASK_ID") or socket.gethostname()
        self.idle_timeout_sec = idle_timeout_sec
        self.restart_cooldown_sec = restart_cooldown_sec
        self.max_errors_in_row = max_errors_in_row
        self.max_runtime_sec = max_runtime_sec
        self.daily_target_eur = daily_target_eur
        self.notify_fn = notify_fn or (lambda msg: print(f"[RUNTIME_GUARD] {msg}"))
        self.on_soft_restart = on_soft_restart
        self._lock = threading.Lock()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="runtime_guard",
        )

    # ----------------------------
    # LIFECYCLE
    # ----------------------------


    def start(self):
        self._monitor_thread.start()
        print("[RUNTIME_GUARD] actif")

    def stop(self, reason: str):
        with self._lock:
            if self.state.stopped:
                return
            self.state.stopped = True

        self._notify(f"🔄 Reset bot : {reason}")

        if self.on_soft_restart:
            self.on_soft_restart(reason)
        else:
            raise SystemExit(reason)

    def attach_driver(self, driver):
        """
        Injecte le driver Selenium/Playwright dans le RuntimeGuard.
        Permet au guard d'agir (CTA, restart léger, etc.).
        """
        self.driver = driver

    def sleep_and_exit(self, reason: str, notify: bool = True):
        if notify:
            self._notify(f"😴 Cooldown ({self.restart_cooldown_sec}s) : {reason}")
        time.sleep(self.restart_cooldown_sec)
        self.state.stopped = False
        self.on_soft_restart(reason)

    def _soft_restart(self, reason: StopReason, pause_sec: int):
        print(
            f"[RUNTIME_GUARD] arrêt dur demandé ({reason.value}) "
            f"| pause souhaitée = {pause_sec}s"
        )

        def _apply_stop(st):
            st["last_stop_reason"] = reason.value
            st["cooldown_until_ts"] = time.time() + pause_sec

        update_state(self.account_id, _apply_stop)

        if _is_prod_env():
            # ✅ en ECS : on laisse le scheduler gérer
            raise SystemExit(reason.value)
        else:
            # 🧪 en local : on ne tue pas brutalement le process
            print(
                "[RUNTIME_GUARD][LOCAL] SystemExit ignoré "
                f"(raison={reason.value}, pause={pause_sec}s)"
            )
            self.state.stopped = True

    def _stop_until_tomorrow(self, reason: StopReason):
        self._notify(f"🛑 Bot stoppé ({reason.value}) — reprise demain à 00h")

        raise SystemExit(reason.value)

    def try_open_application_cta(self, driver) -> bool:
        """
        Tente de cliquer sur le CTA 'Ouvrir l'application' si présent.
        Retourne True si le clic a réussi, False sinon.
        """

        try:
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC

            wait = WebDriverWait(driver, 6)

            # Sélecteurs volontairement larges pour anticiper les variations UI
            cta = wait.until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//button[contains(., 'Ouvrir')] | //a[contains(., 'Ouvrir')]"
                ))
            )

            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});",
                cta
            )
            driver.execute_script("arguments[0].click();", cta)

            print("✅ CTA 'Ouvrir l'application' cliqué avec succès")
            self.record_success()
            return True

        except Exception as e:
            print(f"ℹ️ CTA 'Ouvrir l'application' non cliquable : {e}")
            return False

    def request_survey_restart(self, reason):
        """
        Redémarrage intelligent :
        1) tente un clic léger sur 'Ouvrir l'application'
        2) sinon, arrêt dur + cooldown (scheduler prendra le relais)
        """
        print(f"🔁 Restart survey demandé | raison = {reason}")

        if not _is_prod_env():
            print("[RUNTIME_GUARD][LOCAL] restart survey simulé")
            return
        else:
            # tentative soft via CTA
            try:
                if self.driver and self.try_open_application_cta(self.driver):
                    print("🟢 Redémarrage léger réussi via CTA")
                    return
            except Exception as e:
                print(f"⚠️ Erreur tentative CTA : {e}")

        # fallback robuste et assumé
        print("🔴 Fallback : arrêt dur via RuntimeGuard")
        self._soft_restart(
            StopReason.IDLE,
            pause_sec=self.restart_cooldown_sec
        )

    # ----------------------------
    # EVENTS (appelés par le bot)
    # ----------------------------

    def heartbeat(self):
        with self._lock:
            self.state.last_activity_ts = time.time()
            from State.account_state import update_state

            def _refresh_lock(st):
                if st.get("lock_owner") == self.task_id:
                    st["lock_until_ts"] = int(time.time()) + 900

            update_state(self.account_id, _refresh_lock)


    def record_success(self):
        with self._lock:
            self.state.last_success_ts = time.time()
            self.state.consecutive_errors = 0

    def record_error(self, err: Exception | None = None):
        with self._lock:
            self.state.total_errors += 1
            self.state.consecutive_errors += 1

    def record_openai_call(self):
        with self._lock:
            self.state.openai_calls += 1

    def record_earning(self, amount_eur: float):
        with self._lock:
            self.state.earnings_today_eur += amount_eur

    # ----------------------------
    # MONITORING LOOP
    # ----------------------------

    def _check_ecs_stop_desync(self):
        try:
            st = load_state(self.account_id)
            if not st.get("ecs_stop_requested"):
                return
            if st.get("ecs_stop_notified"):
                return  # anti-spam

            stop_ts = st.get("ecs_stop_ts")
            if not stop_ts:
                return

            # Si le bot tourne encore 60s après SIGTERM → anomalie
            if time.time() - stop_ts > 60:
                msg = (
                    "🚨 BOT TOUJOURS ACTIF APRÈS ARRÊT ECS\n\n"
                    f"account_id: {self.account_id}\n"
                    f"uptime: {int(time.time() - self.state.start_ts)}s\n"
                    f"errors: {self.state.total_errors}\n"
                    f"openai_calls: {self.state.openai_calls}\n"
                    f"earnings_today: {self.state.earnings_today_eur} €\n"
                    "Action recommandée: kill forcé de la task ECS"
                )

                self.notify_fn(msg)

                # Anti-spam : notifier une seule fois
                update_state(self.account_id, lambda st: st.__setitem__("ecs_stop_notified", True))
        except Exception:
            traceback.print_exc()

    def _monitor_loop(self):
        while True:
            try:
                time.sleep(10)
                self._check_ecs_stop_desync()
                self._check_conditions()
            except SystemExit:
                raise
            except Exception:
                traceback.print_exc()

    def _check_conditions(self):
        now = time.time()

        with self._lock:
            idle_time = now - self.state.last_activity_ts
            runtime = now - self.state.start_ts
            errors = self.state.consecutive_errors
            earnings = self.state.earnings_today_eur

        # 1️⃣ Inactivité prolongée → restart silencieux
        if idle_time > self.idle_timeout_sec:
            self._soft_restart(
                StopReason.IDLE,
                pause_sec=self.restart_cooldown_sec
            )
            return

        # 2️⃣ Trop d’erreurs consécutives → restart silencieux
        if errors >= self.max_errors_in_row:
            self._soft_restart(
                StopReason.TOO_MANY_ERRORS,
                pause_sec=self.restart_cooldown_sec
            )
            return

        # 3️⃣ Objectif journalier atteint → arrêt jusqu’à demain
        if earnings >= self.daily_target_eur:
            self._stop_until_tomorrow(
                StopReason.DAILY_TARGET_REACHED
            )
            return

        # 4️⃣ Runtime max atteint
        if runtime >= self.max_runtime_sec:
            if earnings < self.daily_target_eur:
                # 6h atteintes mais objectif NON atteint → pause 30 min
                self._soft_restart(
                    StopReason.RUNTIME_LIMIT,
                    pause_sec=900  # 15 min
                )
            else:
                # objectif atteint → arrêt complet
                self._stop_until_tomorrow(
                    StopReason.DAILY_TARGET_REACHED
                )
            return

    def _notify(self, msg: str):
        try:
            self.notify_fn(msg)
        except Exception:
            pass

# ----------------------------
# Singleton global (robuste)
# ----------------------------

class _NullGuard:
    """
    Guard de secours (no-op) : permet d'appeler get_guard().record_*()
    même si le vrai guard n'est pas encore initialisé.
    """
    def heartbeat(self): pass
    def record_success(self): pass
    def record_error(self, err=None): pass
    def record_openai_call(self): pass
    def record_earning(self, amount_eur: float): pass

_guard_instance = None

def set_guard(g: RuntimeGuard) -> None:
    """Enregistre le guard global à utiliser partout dans le projet."""
    global _guard_instance
    _guard_instance = g

def get_guard():
    """Retourne le guard global ; fallback no-op si non initialisé."""
    return _guard_instance or _NullGuard()
