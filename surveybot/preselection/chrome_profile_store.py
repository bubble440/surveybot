from __future__ import annotations

import io
import os
import tarfile
import threading

from Survey.log_utils import log_info, log_debug

_TAG = "PROFILE"


def _log_warning(msg: str) -> None:
    log_info(f"[{_TAG}][WARN]", msg)


def _connect():
    import psycopg2
    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url:
        return None
    return psycopg2.connect(db_url)


def _ensure_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chrome_profile_store (
                account_id  TEXT PRIMARY KEY,
                profile_data BYTEA,
                saved_at    TIMESTAMPTZ
            )
        """)
    conn.commit()


def load_profile(account_id: str, dest_dir: str) -> None:
    """
    Extrait le profil Chrome archivé depuis Postgres dans dest_dir.
    Ne lève jamais d'exception : tout échec est loggué et ignoré.
    """
    try:
        import psycopg2  # noqa: F401 — vérifie la dispo du driver
        conn = _connect()
        if conn is None:
            log_debug(_TAG, "DATABASE_URL absent — profil éphémère (load ignoré).")
            return
        try:
            _ensure_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT profile_data FROM chrome_profile_store WHERE account_id = %s",
                    (account_id,),
                )
                row = cur.fetchone()
        finally:
            conn.close()

        if row is None:
            log_info(_TAG, f"Aucun profil persisté pour account_id={account_id} — premier run.")
            return

        data = bytes(row[0])
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            tf.extractall(dest_dir)
        log_info(_TAG, f"Profil chargé depuis Postgres pour account_id={account_id} ({len(data)} octets).")

    except Exception as e:
        _log_warning(f"load_profile échoué pour account_id={account_id}: {e}")


def save_profile(account_id: str, src_dir: str) -> None:
    """
    Archive src_dir en tar.gz et fait un upsert dans chrome_profile_store.
    Ne lève jamais d'exception : tout échec est loggué et ignoré.
    """
    try:
        import psycopg2
        conn = _connect()
        if conn is None:
            _log_warning("DATABASE_URL absent — profil non sauvegardé.")
            return

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            tf.add(src_dir, arcname=".")
        data = buf.getvalue()

        try:
            _ensure_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO chrome_profile_store (account_id, profile_data, saved_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (account_id) DO UPDATE
                        SET profile_data = EXCLUDED.profile_data,
                            saved_at     = EXCLUDED.saved_at
                    """,
                    (account_id, psycopg2.Binary(data)),
                )
            conn.commit()
        finally:
            conn.close()

        log_info(_TAG, f"Profil sauvegardé dans Postgres pour account_id={account_id} ({len(data)} octets).")

    except Exception as e:
        _log_warning(f"save_profile échoué pour account_id={account_id}: {e}")


def start_profile_autosave(account_id: str, get_user_data_dir, interval_sec: int = 300) -> threading.Event:
    """
    Lance un thread daemon qui sauvegarde périodiquement le profil Chrome.
    get_user_data_dir est un callable (résolution tardive).
    Retourne un threading.Event à setter pour arrêter le thread proprement.
    """
    stop_event = threading.Event()

    def _loop():
        while not stop_event.wait(interval_sec):
            try:
                d = get_user_data_dir()
                if d:
                    save_profile(account_id, d)
            except Exception as e:
                _log_warning(f"autosave loop erreur pour account_id={account_id}: {e}")

    t = threading.Thread(target=_loop, daemon=True, name=f"profile-autosave-{account_id}")
    t.start()
    return stop_event
