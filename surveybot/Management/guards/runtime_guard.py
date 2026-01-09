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
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from Management.pause_policy import PausePolicy, resolve_pause_seconds

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
    SESSION_EXPIRED = "session_expired"


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
        restart_cooldown_sec: int = 60,      # 1 minute
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

    def try_open_application_cta(self, driver) -> bool:
        """
        Tente de cliquer sur le CTA 'Ouvrir l'application' si présent.
        Retourne True si le clic a réussi, False sinon.
        """

        try:
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
            print(f"ℹ️ CTA 'Ouvrir l'application' non cliquable.")
            return False
        
    def signal_no_gain(self):
        """Appelé par le watchdog si aucun gain prolongé."""
        self.pause(
            PausePolicy.MEDIUM_COOLDOWN,
            StopReason.NO_GAIN,
        )

    def signal_strict_survey(self, reason: str):
        """Survey trop strict (captcha, drag&drop, etc.)."""
        self.request_survey_restart(reason)

    def signal_fatal_error(self, reason: str):
        """Erreur non récupérable."""
        self.pause(
            PausePolicy.MEDIUM_COOLDOWN,
            StopReason.TOO_MANY_ERRORS,
        )

    def request_survey_restart(self, reason):
        """
        Redémarrage intelligent :
        1) tentative CTA (best effort)
        2) sinon délégation au flow principal (soft restart)
        3) si ça échoue → pause courte
        """
        print(f"🔁 Restart survey demandé | raison = {reason}")

        if not _is_prod_env():
            print("[RUNTIME_GUARD][LOCAL] restart survey simulé")
            return

        # 1) CTA best-effort
        cta_clicked = False
        try:
            if self.driver:
                cta_clicked = self.try_open_application_cta(self.driver)
        except Exception as e:
            # CTA qui explose = non bloquant
            print(f"[RUNTIME_GUARD][WARN] try_open_application_cta a levé: {e}")
            cta_clicked = False

        if cta_clicked:
            print("🟢 CTA cliqué → reprise via UI")
            return

        # 2) CTA absent / inutile → délégation soft restart
        try:
            print("🔄 CTA indisponible → délégation soft restart")
            if self.on_soft_restart:
                # IMPORTANT: on passe la vraie raison, pas un alias
                self.on_soft_restart(reason)
                return

            # Si on_soft_restart n'est pas défini, c'est une config invalide en prod
            raise RuntimeError("on_soft_restart non défini en prod")

        except Exception as e:
            print(f"❌ Échec soft restart (on_soft_restart) : {e}")

        # 3) Échec → pause courte
        print("⛔ Soft restart échoué → pause courte")
        self.pause(
            PausePolicy.SHORT_COOLDOWN,
            StopReason.TOO_MANY_ERRORS,  # plus cohérent que DAILY_TARGET_REACHED
        )
        
    # ----------------------------
    # EVENTS (appelés par le bot)
    # ----------------------------

    def heartbeat(self):
        with self._lock:
            self.state.last_activity_ts = time.time()

            def _refresh_lock(st):
                if st.get("lock_owner") == self.task_id:
                    st["lock_until_ts"] = int(time.time()) + 15 

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
        
        if _is_prod_env():
            return  # conditions gérées par ECS en prod

        with self._lock:
            idle_time = now - self.state.last_activity_ts
            runtime = now - self.state.start_ts
            errors = self.state.consecutive_errors
            earnings = self.state.earnings_today_eur

        # 1️⃣ Inactivité prolongée → restart silencieux
        if idle_time > self.idle_timeout_sec:
            self.pause(
                PausePolicy.SHORT_COOLDOWN,
                StopReason.IDLE,
            )
            return

        # 2️⃣ Trop d’erreurs consécutives → restart silencieux
        if errors >= self.max_errors_in_row:
            self.pause(
                PausePolicy.SHORT_COOLDOWN,
                StopReason.TOO_MANY_ERRORS,
            )
            return

        # 3️⃣ Objectif journalier atteint → arrêt jusqu’à demain
        if earnings >= self.daily_target_eur:
            self.pause(
                PausePolicy.DAILY_RESET,
                StopReason.DAILY_TARGET_REACHED,
            )
            return

        # 4️⃣ Runtime max atteint
        if runtime >= self.max_runtime_sec:
            if earnings < self.daily_target_eur:
                # 2h atteintes mais objectif NON atteint → pause 15 min
                self.pause(
                    PausePolicy.MEDIUM_COOLDOWN,
                    StopReason.RUNTIME_LIMIT
                )
            else:
                # objectif atteint → arrêt complet
                self.pause(
                    PausePolicy.DAILY_RESET,
                    StopReason.DAILY_TARGET_REACHED,
                )
            return

    def _notify(self, msg: str):
        try:
            self.notify_fn(msg)
        except Exception:
            pass

    def pause(self, policy: PausePolicy, reason: StopReason):
        """
        Applique une PausePolicy au bot et stoppe l'exécution.
        """
        pause_sec = resolve_pause_seconds(policy)

        self._notify(
            f"⏸️ Pause bot ({policy.name}) | raison={reason.value} | pause={pause_sec}s"
        )

        def _apply_pause(st):
            st["last_stop_reason"] = reason.value
            st["pause_policy"] = policy.name
            st["cooldown_until_ts"] = int(time.time()) + pause_sec

        update_state(self.account_id, _apply_pause)

        # En prod on laisse ECS / scheduler gérer
        raise SystemExit(reason.value)

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
    def attach_driver(self, driver): pass

_guard_instance = None

def set_guard(g: RuntimeGuard) -> None:
    """Enregistre le guard global à utiliser partout dans le projet."""
    global _guard_instance
    _guard_instance = g

def get_guard():
    """Retourne le guard global ; fallback no-op si non initialisé."""
    return _guard_instance or _NullGuard()
