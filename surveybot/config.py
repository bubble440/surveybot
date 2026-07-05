"""
config.py — Configuration centrale du bot SurveyBot

Ce fichier centralise TOUTES les conditions liées à l'environnement d'exécution.
Objectif : un seul endroit pour switcher entre mode debug manuel et mode autonome.

MODES DISPONIBLES (2 seulement) :
──────────────────────────────────
1. PROD (défaut)
   - RUN_ENV=prod
   - Fonctionnement autonome, aucune pause interactive
   - RuntimeGuard + heartbeat toujours actifs

2. ATTACH (debug manuel)
   - BROWSER_MODE=attach
   - Attachement CDP à un Chrome déjà lancé, indépendant de RUN_ENV
   - Pauses interactives (captcha, CTA), hot reload et serveur HTTP debug activables
   - RuntimeGuard / heartbeat désactivés

UTILISATION :
─────────────
  # Mode prod (autonome)
  python main.py

  # Mode attach (debug manuel sur navigateur existant)
  BROWSER_MODE=attach ATTACH_DEBUGGER_ADDRESS=127.0.0.1:9222 python main.py
"""

import os

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION DE BASE
# ══════════════════════════════════════════════════════════════════════════════

RUN_ENV = os.getenv("RUN_ENV", "prod")  # prod (défaut) | autre valeur = non-prod
BROWSER_MODE = os.getenv("BROWSER_MODE", "normal")  # normal | attach

# ══════════════════════════════════════════════════════════════════════════════
# FONCTIONS DE DÉCISION CENTRALISÉES
# Pivot unique : is_attach_mode() = (BROWSER_MODE == "attach").
# Tout le reste (should_run_*, should_pause_*) se déduit de prod vs attach.
# ══════════════════════════════════════════════════════════════════════════════

def _env_truthy(name: str, default: str = "0") -> bool:
    """Retourne True si la variable d'environnement est truthy (1, true, yes, on)."""
    v = (os.getenv(name, default) or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def is_attach_mode() -> bool:
    """Mode attach = debug manuel sur navigateur existant (CDP attach)."""
    return BROWSER_MODE == "attach"


def is_prod_like() -> bool:
    """Retourne True si le bot doit se comporter comme en production (= hors attach)."""
    return not is_attach_mode()


def should_pause_for_captcha() -> bool:
    """Retourne True si on doit faire une pause interactive pour résoudre un CAPTCHA (attach uniquement)."""
    return is_attach_mode()


def should_block_for_input() -> bool:
    """Retourne True si les appels input() bloquants sont autorisés (attach + terminal interactif uniquement)."""
    if not is_attach_mode():
        return False
    import sys
    return getattr(sys.stdin, "isatty", lambda: False)()


def is_cta_intercept_only() -> bool:
    """
    Retourne True si CTA_INTERCEPT_ONLY est actif.
    Dans ce mode, les clics CTA sont interceptés (events déclenchés) mais la navigation
    ne se produit pas réellement.
    """
    return _env_truthy("CTA_INTERCEPT_ONLY", "0")


def should_pause_before_cta() -> bool:
    """
    Retourne True si le bot doit attendre une confirmation utilisateur avant un clic CTA.
    Activable uniquement en mode attach via LOCAL_CTA_REQUIRE_ENTER=1. Jamais en prod.
    """
    if not is_attach_mode():
        return False
    if not _env_truthy("LOCAL_CTA_REQUIRE_ENTER", "0"):
        return False
    import sys
    return getattr(sys.stdin, "isatty", lambda: False)()


def should_run_guard_monitor() -> bool:
    """Retourne True si le RuntimeGuard doit être activé avec son monitoring."""
    return is_prod_like()


def should_run_heartbeat() -> bool:
    """Retourne True si le heartbeat doit être activé."""
    return is_prod_like()


def should_run_hot_reload() -> bool:
    """Retourne True si le hot reload des modules doit être activé (attach uniquement)."""
    return is_attach_mode()


def get_captcha_behavior() -> str:
    """
    Retourne le comportement à adopter face à un CAPTCHA.
    "auto_2captcha" = résolution automatique via 2Captcha (prod + attach si clé disponible)
    "pause"         = attendre résolution manuelle (attach, fallback si pas de clé)
    "restart"       = abandonner le survey (prod sans clé)
    """
    # Priorité : résolution automatique si clé 2Captcha disponible
    from captcha.captcha_solver import TWO_CAPTCHA_KEY
    if TWO_CAPTCHA_KEY:
        return "auto_2captcha"
    # Fallback : comportement existant inchangé
    if should_pause_for_captcha():
        return "pause"
    return "restart"


def log_config_summary():
    """Affiche un résumé de la configuration au démarrage."""
    mode = "ATTACH" if is_attach_mode() else "PROD"
    print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ SURVEYBOT CONFIGURATION                                                      ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ RUN_ENV          : {RUN_ENV:<20}                                             ║
║ BROWSER_MODE     : {BROWSER_MODE:<20}                                        ║
║ MODE EFFECTIF    : {mode:<20}                                                ║
║                                                                              ║
║ Comportements :                                                              ║
║   • Pause CAPTCHA     : {str(should_pause_for_captcha()):<10}                ║
║   • RuntimeGuard      : {str(should_run_guard_monitor()):<10}                ║
║   • Heartbeat         : {str(should_run_heartbeat()):<10}                    ║
║   • Hot Reload        : {str(should_run_hot_reload()):<10}                   ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
