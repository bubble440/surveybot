"""
preselection/license_guard.py

Vérifie le quota de licence au démarrage du bot (prod uniquement).
Appelé en tout premier dans main(), avant toute autre initialisation.

Logique :
  - Lit LICENSE_KEY depuis _license_config.py (embarqué à la compilation PyInstaller).
  - Se connecte au Postgres central via DATABASE_URL (embarquée également).
  - Vérifie is_active, puis total_payout_eur < max_payout_eur.
  - Fail-closed : si Postgres injoignable → SystemExit.
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger("license_guard")

# ---------------------------------------------------------------------------
# Clé embarquée à la compilation (fichier _license_config.py non versionné,
# inclus dans le build PyInstaller via --add-data ou comme module interne).
# En dev/attach, ce fichier peut être absent — la guard est bypassée.
# ---------------------------------------------------------------------------
def _get_license_key() -> str | None:
    try:
        from _license_config import LICENSE_KEY  # type: ignore
        return LICENSE_KEY.strip() if LICENSE_KEY else None
    except ImportError:
        return None


def _get_database_url() -> str:
    # Résolution centralisée (partagée avec State/account_state.py et
    # State/survey_memory.py) : _license_config en priorité, os.getenv en dev/attach.
    from db_config import get_database_url
    return get_database_url()


# ---------------------------------------------------------------------------
# Vérification principale
# ---------------------------------------------------------------------------

def check_license_or_exit() -> None:
    """
    Vérifie la validité de la licence contre Postgres.
    SystemExit si : Postgres injoignable, licence inactive, quota atteint.
    No-op si LICENSE_KEY absent (mode dev/attach).
    """
    license_key = _get_license_key()
    if not license_key:
        log.info("[LICENSE] Pas de LICENSE_KEY embarquée — vérification ignorée (mode dev).")
        return

    database_url = _get_database_url()
    if not database_url:
        log.error("[LICENSE] DATABASE_URL manquante. Arrêt.")
        sys.exit("license_guard: DATABASE_URL manquante")

    try:
        import psycopg2
        conn = psycopg2.connect(database_url)
        conn.autocommit = True
    except Exception as exc:
        log.error("[LICENSE] Postgres injoignable : %s", exc)
        sys.exit(f"license_guard: Postgres injoignable — {exc}")

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM check_license(%s)",
                (license_key,),
            )
            row = cur.fetchone()
    except Exception as exc:
        log.error("[LICENSE] Erreur lecture table licenses : %s", exc)
        conn.close()
        sys.exit(f"license_guard: erreur lecture licenses — {exc}")
    finally:
        conn.close()

    if row is None:
        log.error("[LICENSE] Clé inconnue : %s", license_key[:8])
        sys.exit("license_guard: licence inconnue")

    is_active, total_payout_eur, max_payout_eur = row

    if not is_active:
        log.error("[LICENSE] Licence désactivée.")
        sys.exit("license_guard: licence désactivée")

    if total_payout_eur >= max_payout_eur:
        log.error(
            "[LICENSE] Quota atteint : %.2f / %.2f EUR.",
            total_payout_eur, max_payout_eur,
        )
        sys.exit("license_guard: quota atteint")

    log.info(
        "[LICENSE] OK — %.2f / %.2f EUR utilisés.",
        total_payout_eur, max_payout_eur,
    )
