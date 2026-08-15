"""
Mémoire partagée inter-bots pour surveys (TTL 3h).

Chaque popup de qualification donne lieu à une SurveySession locale accumulée
en mémoire Python. À la fin du popup (DQ ou qualification), la session est
flushée en Postgres.

Lecture avant chaque sélection d'options :
  - Combinaison gagnante → bypass GPT direct
  - Page franchie avec succès par une tentative antérieure → réutiliser ses options
  - Page qui a causé une DQ → injecter les options échouées en "à éviter" dans le prompt
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import unicodedata
import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Optional

log = logging.getLogger("survey_memory")

_TTL_HOURS = 3

# STATE_BACKEND est une variable GLOBAL_CONFIG : en build compilé (Nuitka), elle provient
# exclusivement de global_config.py, jamais de l'environnement du process (cf. config.py).
# En dev/attach (global_config.py absent du projet), fallback os.getenv.
try:
    from global_config import STATE_BACKEND  # type: ignore
except ImportError:
    STATE_BACKEND = os.getenv("STATE_BACKEND", "")

# Résolution centralisée (partagée avec preselection/license_guard.py et
# State/account_state.py) : _license_config en priorité, os.getenv en dev/attach.
from db_config import get_database_url

DATABASE_URL = get_database_url()


# ---------------------------------------------------------------------------
# Backend helpers
# ---------------------------------------------------------------------------

def _pg_available() -> bool:
    return (
        STATE_BACKEND.strip().lower() == "postgres"
        and bool(DATABASE_URL)
    )


def _get_conn():
    import psycopg2
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    return conn


def _ensure_table(conn) -> None:
    """
    Le rôle surveybot_client n'a volontairement aucun droit DDL (CREATE/ALTER) — voir
    décision de restriction de rôle, section 3 du doc de suivi. La table doit donc déjà
    exister (créée manuellement une fois par un rôle privilégié). Un refus de permission
    ici est attendu dans ce cas et ne doit pas remonter comme une erreur de lecture/écriture
    normale : on le logue en debug et on continue, en supposant que la table existe déjà.
    """
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS survey_memory (
                    survey_key    TEXT NOT NULL,
                    attempt_id    TEXT NOT NULL,
                    outcome       TEXT NOT NULL DEFAULT 'disqualified',
                    dq_page_index INTEGER DEFAULT NULL,
                    choices       JSONB NOT NULL DEFAULT '[]',
                    created_at    TIMESTAMPTZ DEFAULT now(),
                    expires_at    TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (survey_key, attempt_id)
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS survey_memory_key_expires
                ON survey_memory (survey_key, expires_at)
            """)
        conn.commit()
    except Exception as e:
        conn.rollback()
        log.debug(
            "[SURVEY_MEMORY] Impossible de créer/modifier survey_memory (rôle sans droits "
            "DDL, attendu si la table existe déjà) : %s", e
        )


# ---------------------------------------------------------------------------
# Key derivation
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Texte normalisé : NFKD → sans accents → minuscules → espaces compressés."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def make_key(question_text: str, options: list) -> str:
    """Clé stable (24 hex) dérivée de la question et de ses options (ordre indépendant)."""
    q_norm = _normalize(question_text)
    opts_norm = "|".join(_normalize(str(o)) for o in sorted(options or []))
    payload = f"{q_norm}###{opts_norm}"
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------
# Session locale (en mémoire Python pendant un popup)
# ---------------------------------------------------------------------------

class SurveySession:
    """Accumule les choix du bot pendant un popup de qualification.

    Persisté en Postgres uniquement à la fin du popup via flush_disqualified /
    flush_qualified. Réinitialiser pour chaque nouveau popup.
    """

    def __init__(self) -> None:
        self.survey_key: Optional[str] = None
        self.attempt_id: str = str(uuid.uuid4())
        self.choices: List[dict] = []
        self._page_counter: int = 0

    @property
    def current_page_index(self) -> int:
        return self._page_counter

    def set_survey_key_if_first(self, question_key: str) -> None:
        """Fixe le survey_key depuis la première question (une seule fois)."""
        if self.survey_key is None:
            self.survey_key = question_key

    def record_choice(self, question_key: str, chosen_options) -> None:
        """Enregistre le choix pour la question courante et avance le compteur."""
        if not isinstance(chosen_options, list):
            chosen_options = [chosen_options] if chosen_options else []
        self.choices.append({
            "question_key": question_key,
            "page_index": self._page_counter,
            "chosen_options": chosen_options,
        })
        self._page_counter += 1


# ---------------------------------------------------------------------------
# Guidance (résultat de la lecture mémoire)
# ---------------------------------------------------------------------------

class MemoryGuidance:
    """Résultat de read_guidance pour une question donnée."""

    def __init__(self) -> None:
        self.use_options: Optional[List[str]] = None
        """Non-None → bypass GPT : utiliser directement ces options."""
        self.avoid_options: List[str] = []
        """Options à injecter en 'à éviter' dans le prompt GPT."""


# ---------------------------------------------------------------------------
# Lecture mémoire
# ---------------------------------------------------------------------------

def read_guidance(survey_key: str, question_key: str, page_index: int) -> MemoryGuidance:
    """
    Interroge Postgres pour un survey_key + question_key donnés.

    Priorités :
      1. Tentative qualifiée → use_options (bypass GPT)
      2. Tentative ayant passé cette page avec succès → use_options (bypass GPT)
      3. Tentatives DQ à cette page exacte → avoid_options (inject dans prompt)

    Le repli positionnel (_options_for_page, même page_index sans vérification du
    contenu de la question) n'est utilisé que pour le palier 3 (avoid_options) : la
    donnée n'y est qu'injectée comme signal dans le prompt GPT, qui reste l'arbitre
    final. Les paliers 1 et 2 déclenchent un bypass complet de GPT et exigent donc
    la correspondance exacte par question_key — un repli positionnel n'offre aucune
    garantie que la question antérieure est bien la même, en cas de branchement ou
    de question sautée dans la série.
    """
    guidance = MemoryGuidance()
    if not _pg_available() or not survey_key:
        return guidance

    try:
        conn = _get_conn()
        _ensure_table(conn)
        try:
            import psycopg2.extras
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT attempt_id, outcome, dq_page_index, choices
                    FROM survey_memory
                    WHERE survey_key = %s
                      AND expires_at > now()
                    """,
                    (survey_key,),
                )
                rows = cur.fetchall()
        finally:
            conn.close()

        if not rows:
            return guidance

        def _options_for(row) -> Optional[List[str]]:
            for ch in (row["choices"] or []):
                if ch.get("question_key") == question_key:
                    return ch.get("chosen_options") or []
            return None

        def _options_for_page(row, target_page_index) -> Optional[List[str]]:
            """Repli positionnel (question_key non trouvé) : même page_index dans la
            série pour ce survey_key. N'est jamais consulté quand _options_for()
            trouve déjà une correspondance exacte."""
            for ch in (row["choices"] or []):
                if ch.get("page_index") == target_page_index:
                    return ch.get("chosen_options") or []
            return None

        # 1. Combinaison gagnante (bypass GPT → correspondance exacte requise, pas de repli positionnel)
        for row in rows:
            if row["outcome"] == "qualified":
                opts = _options_for(row)
                if opts is not None:
                    guidance.use_options = opts
                    log.debug(
                        "[SURVEY_MEMORY] Combinaison gagnante trouvée pour survey=%s q=%s",
                        survey_key[:8], question_key[:8],
                    )
                    return guidance

        # 2. Tentative qui a passé cette page (DQ survenu plus tard) (bypass GPT → idem)
        for row in rows:
            dq_page = row.get("dq_page_index")
            if dq_page is not None and dq_page > page_index:
                opts = _options_for(row)
                if opts is not None:
                    guidance.use_options = opts
                    log.debug(
                        "[SURVEY_MEMORY] Réutilisation page %d (DQ à %d) survey=%s",
                        page_index, dq_page, survey_key[:8],
                    )
                    return guidance

        # 3. Tentatives DQ à cette page exacte → collecter options à éviter
        avoid_set: set = set()
        for row in rows:
            dq_page = row.get("dq_page_index")
            if dq_page == page_index:
                opts = _options_for(row)
                if opts is None:
                    opts = _options_for_page(row, page_index)
                if opts:
                    for o in opts:
                        avoid_set.add(str(o))
        if avoid_set:
            guidance.avoid_options = list(avoid_set)
            log.debug(
                "[SURVEY_MEMORY] Options à éviter page %d: %s", page_index, avoid_set,
            )

        return guidance

    except Exception as exc:
        log.warning("[SURVEY_MEMORY] read_guidance error: %s", exc)
        return MemoryGuidance()


# ---------------------------------------------------------------------------
# Flush vers Postgres
# ---------------------------------------------------------------------------

def flush_disqualified(session: SurveySession) -> None:
    """Persiste la session comme tentative disqualifiée."""
    if not _pg_available() or not session.survey_key or not session.choices:
        return
    dq_page = session.choices[-1]["page_index"]
    _write_attempt(session, outcome="disqualified", dq_page_index=dq_page)


def flush_qualified(session: SurveySession) -> None:
    """Persiste la session comme combinaison gagnante."""
    if not _pg_available() or not session.survey_key or not session.choices:
        return
    _write_attempt(session, outcome="qualified", dq_page_index=None)


def _write_attempt(session: SurveySession, outcome: str, dq_page_index: Optional[int]) -> None:
    expires_at = datetime.now(timezone.utc) + timedelta(hours=_TTL_HOURS)
    try:
        conn = _get_conn()
        _ensure_table(conn)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO survey_memory
                        (survey_key, attempt_id, outcome, dq_page_index, choices, expires_at)
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                    ON CONFLICT (survey_key, attempt_id) DO UPDATE
                        SET outcome        = EXCLUDED.outcome,
                            dq_page_index  = EXCLUDED.dq_page_index,
                            choices        = EXCLUDED.choices,
                            expires_at     = EXCLUDED.expires_at
                    """,
                    (
                        session.survey_key,
                        session.attempt_id,
                        outcome,
                        dq_page_index,
                        json.dumps(session.choices),
                        expires_at.isoformat(),
                    ),
                )
                # Purge des lignes expirées (TTL 3h) : seul point d'écriture existant,
                # évite la croissance indéfinie de la table sans composant d'orchestration
                # séparé. Filtre strict sur expires_at : ne touche jamais une ligne valide.
                cur.execute("DELETE FROM survey_memory WHERE expires_at <= now()")
                purged = cur.rowcount
            conn.commit()
            log.info(
                "[SURVEY_MEMORY] flush %s survey=%s attempt=%s pages=%d",
                outcome, session.survey_key[:8], session.attempt_id[:8], len(session.choices),
            )
            if purged:
                log.debug("[SURVEY_MEMORY] purge %d ligne(s) expirée(s)", purged)
        finally:
            conn.close()

    except Exception as exc:
        log.warning("[SURVEY_MEMORY] _write_attempt error: %s", exc)