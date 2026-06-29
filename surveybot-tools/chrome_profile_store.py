from __future__ import annotations

import io
import os
import tarfile
import threading

from log_utils import log_info, log_debug

_TAG = "PROFILE"


def _log_warning(msg: str) -> None:
    log_info(f"[{_TAG}][WARN]", msg)


_CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB par chunk


def _connect():
    import psycopg2
    db_url = os.getenv("DATABASE_URL", "postgres://postgres:3o1L6kfCFxuncbY@localhost:5432/postgres").strip()
    if not db_url:
        return None
    return psycopg2.connect(db_url, connect_timeout=60, options="-c statement_timeout=120000")

def _ensure_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chrome_profile_chunks (
                account_id  TEXT,
                chunk_index INT,
                chunk_data  BYTEA,
                saved_at    TIMESTAMPTZ,
                PRIMARY KEY (account_id, chunk_index)
            )
        """)
    conn.commit()



# Dossiers exclus de l'archive : volumineux et recréés automatiquement par Chrome.
# Inutiles pour l'anti-détection (cache HTTP, bytecode JS compilé, shaders GPU).
# Dossiers exclus de l'archive : volumineux et recréés automatiquement par Chrome.
# Inutiles pour l'anti-détection (cache HTTP, bytecode JS, shaders GPU, extensions).
# Périmètre établi par analyse réelle du profil (profile_size_check.py).
# Fichiers de lock créés par Chrome à chaque démarrage et supprimés à l'arrêt propre.
# Ne doivent jamais être archivés : ils encodent le hostname/PID de la machine source
# et font croire à Chrome cible que le profil est verrouillé par un autre processus.
_EXCLUDED_FILES = {"SingletonLock", "SingletonSocket", "SingletonCookie"}

_EXCLUDED_DIRS = {
    # Racine du profil
    "Cache",
    "Code Cache",
    "GPUCache",
    "GrShaderCache",
    "ShaderCache",
    "DawnCache",
    "GraphiteDawnCache",
    "optimization_guide_model_store",
    "Safe Browsing",
    "component_crx_cache",
    "WasmTtsEngine",
    "OnDeviceHeadSuggestModel",
    "Crashpad",
    # Sous Default/
    "DawnWebGPUCache",
    "DawnGraphiteCache",
    "Extensions",
}


def _tar_add_safe(tf: tarfile.TarFile, src_dir: str) -> int:
    """
    Ajoute récursivement src_dir dans l'archive tf.
    Les dossiers dans _EXCLUDED_DIRS sont ignorés (trop volumineux, recréés par Chrome).
    Les fichiers inaccessibles (locks Windows, Permission denied) sont ignorés
    individuellement avec un log warning — l'archive continue.
    Retourne le nombre de fichiers/dossiers ignorés.
    """
    skipped = 0
    for root, dirs, files in os.walk(src_dir):
        # Exclure les sous-dossiers volumineux (modifie dirs in-place pour éviter os.walk dedans)
        dirs[:] = [d for d in dirs if d not in _EXCLUDED_DIRS]
        # Ajouter le répertoire lui-même
        rel_root = os.path.relpath(root, src_dir)
        arcname = "." if rel_root == "." else rel_root
        try:
            tf.add(root, arcname=arcname, recursive=False)
        except Exception as e:
            _log_warning(f"répertoire ignoré ({arcname}): {e}")
            skipped += 1

        for fname in files:
            if fname in _EXCLUDED_FILES:
                skipped += 1
                continue
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, src_dir)
            try:
                tf.add(fpath, arcname=rel, recursive=False)
            except Exception as e:
                _log_warning(f"fichier ignoré ({rel}): {e}")
                skipped += 1
    return skipped


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
                    "SELECT chunk_data FROM chrome_profile_chunks"
                    " WHERE account_id = %s ORDER BY chunk_index",
                    (account_id,),
                )
                rows = cur.fetchall()
        finally:
            conn.close()

        if not rows:
            log_info(_TAG, f"Aucun profil persisté pour account_id={account_id} — premier run.")
            return

        data = b"".join(bytes(r[0]) for r in rows)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            tf.extractall(dest_dir)
        log_info(_TAG, f"Profil chargé depuis Postgres pour account_id={account_id} ({len(data)} octets, {len(rows)} chunks).")

    except Exception as e:
        _log_warning(f"load_profile échoué pour account_id={account_id}: {e}")


def save_profile(account_id: str, src_dir: str) -> None:
    """
    Archive src_dir en tar.gz et stocke le blob en chunks dans chrome_profile_chunks.
    Chaque chunk est inséré dans une transaction séparée pour éviter les coupures réseau
    sur les gros transferts via WireGuard/Fly.io.
    Ne lève jamais d'exception : tout échec est loggué et ignoré.
    """
    try:
        import psycopg2

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            skipped = _tar_add_safe(tf, src_dir)
        data = buf.getvalue()

        if skipped:
            log_info(_TAG, f"{skipped} fichier(s) ignoré(s) (verrouillés) pour account_id={account_id}.")

        chunks = [data[i:i + _CHUNK_SIZE] for i in range(0, len(data), _CHUNK_SIZE)]
        n_chunks = len(chunks)
        log_debug(_TAG, f"save_profile: {len(data)} octets → {n_chunks} chunk(s) de {_CHUNK_SIZE // 1024 // 1024} MB pour account_id={account_id}.")

        conn = _connect()
        if conn is None:
            _log_warning("DATABASE_URL absent — profil non sauvegardé.")
            return

        try:
            _ensure_table(conn)
            # Suppression des anciens chunks en une seule transaction
            with conn.cursor() as cur:
                cur.execute("DELETE FROM chrome_profile_chunks WHERE account_id = %s", (account_id,))
            conn.commit()

            # Insertion de chaque chunk dans sa propre transaction
            for idx, chunk in enumerate(chunks):
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO chrome_profile_chunks (account_id, chunk_index, chunk_data, saved_at)
                        VALUES (%s, %s, %s, NOW())
                        """,
                        (account_id, idx, psycopg2.Binary(chunk)),
                    )
                conn.commit()
                log_debug(_TAG, f"Chunk {idx + 1}/{n_chunks} inséré pour account_id={account_id}.")
        finally:
            conn.close()

        log_info(_TAG, f"Profil sauvegardé dans Postgres pour account_id={account_id} ({len(data)} octets, {n_chunks} chunks).")

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