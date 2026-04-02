"""
config.py — Configuration centrale du bot SurveyBot

Ce fichier centralise TOUTES les conditions liées à l'environnement d'exécution.
Objectif : un seul endroit pour switcher entre mode debug interactif et mode autonome.

MODES DISPONIBLES :
───────────────────
1. LOCAL INTERACTIF (défaut)
   - RUN_ENV=local (ou absent)
   - Pauses CAPTCHA avec input()
   - Pas de heartbeat/guard
   - Hot reload activé

2. LOCAL UNATTENDED (simulation prod)
   - RUN_ENV=local + LOCAL_UNATTENDED=1
   - Pas de pauses bloquantes
   - RuntimeGuard activé
   - Heartbeat activé
   - Comportement identique à la prod (sauf proxies)

3. PROD (Fly.io)
   - RUN_ENV=prod
   - Tout activé, aucune pause interactive

UTILISATION :
─────────────
  # Mode debug (défaut)
  python main.py

  # Mode simulation prod (toute la journée)
  LOCAL_UNATTENDED=1 python main.py
"""

import os

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION DE BASE
# ══════════════════════════════════════════════════════════════════════════════

RUN_ENV = os.getenv("RUN_ENV", "local")  # local | prod
RUN_MODE = os.getenv("RUN_MODE", "local")        # prod | local
BROWSER_MODE = os.getenv("BROWSER_MODE", "normal")  # normal | attach

# ══════════════════════════════════════════════════════════════════════════════
# MODE LOCAL UNATTENDED — Le switch central
# ══════════════════════════════════════════════════════════════════════════════

def _env_truthy(name: str, default: str = "0") -> bool:
    """Retourne True si la variable d'environnement est truthy (1, true, yes, on)."""
    v = (os.getenv(name, default) or "").strip().lower()
    return v in ("1", "true", "yes", "on")

LOCAL_UNATTENDED = _env_truthy("LOCAL_UNATTENDED", "0")
# ══════════════════════════════════════════════════════════════════════════════
# FONCTIONS DE DÉCISION CENTRALISÉES
# ══════════════════════════════════════════════════════════════════════════════

def is_local_env() -> bool:
    """Retourne True si on est en environnement local (pas prod)."""
    return RUN_ENV == "local"


def is_attach_mode() -> bool:
    """Mode attach = debug sur navigateur existant. IMPOSSIBLE hors local."""
    return is_local_env() and RUN_MODE == "local" and BROWSER_MODE == "attach"


def is_prod_like() -> bool:
    """
    Retourne True si le bot doit se comporter comme en production.
    Inclut : environnement réel (prod/Fly.io) OU local avec LOCAL_UNATTENDED=1
    """
    if not is_local_env():
        return True
    return LOCAL_UNATTENDED


def should_pause_for_captcha() -> bool:
    """
    Retourne True si on doit faire une pause interactive pour résoudre un CAPTCHA.
    False en prod ou en local_unattended.
    """
    if not is_local_env():
        return False
    if LOCAL_UNATTENDED:
        return False
    return True


def should_block_for_input() -> bool:
    """
    Retourne True si les appels input() bloquants sont autorisés.
    False en prod ou en local_unattended.
    """
    if not is_local_env():
        return False
    if LOCAL_UNATTENDED:
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
    Retourne True si le bot doit attendre une confirmation utilisateur
    avant un clic CTA (Suivant/Continuer/etc.).
    Désactivé en prod-like (prod/local_unattended).
    """
    if is_prod_like():
        return False
    if not _env_truthy("LOCAL_CTA_REQUIRE_ENTER", "0"):
        return False
    return should_block_for_input()


def should_run_guard_monitor() -> bool:
    """Retourne True si le RuntimeGuard doit être activé avec son monitoring."""
    return is_prod_like()


def should_run_heartbeat() -> bool:
    """Retourne True si le heartbeat doit être activé."""
    return is_prod_like()


def should_run_hot_reload() -> bool:
    """Retourne True si le hot reload des modules doit être activé."""
    if not is_local_env():
        return False
    return True


def get_captcha_behavior() -> str:
    """
    Retourne le comportement à adopter face à un CAPTCHA.
    "auto_2captcha" = résolution automatique via 2Captcha (local + prod si clé disponible)
    "pause"         = attendre résolution manuelle (local interactif, fallback si pas de clé)
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
    mode = "PROD" if not is_local_env() else ("LOCAL_UNATTENDED" if LOCAL_UNATTENDED else "LOCAL_INTERACTIVE")
    print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ SURVEYBOT CONFIGURATION                                                      ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ RUN_ENV          : {RUN_ENV:<20}                                             ║
║ LOCAL_UNATTENDED : {str(LOCAL_UNATTENDED):<20}                               ║
║ MODE EFFECTIF    : {mode:<20}                                                ║
║                                                                              ║
║ Comportements :                                                              ║
║   • Pause CAPTCHA     : {str(should_pause_for_captcha()):<10}                ║
║   • RuntimeGuard      : {str(should_run_guard_monitor()):<10}                ║
║   • Heartbeat         : {str(should_run_heartbeat()):<10}                    ║
║   • Hot Reload        : {str(should_run_hot_reload()):<10}                   ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")

# Alias pour compatibilité
IS_LOCAL = is_local_env()
