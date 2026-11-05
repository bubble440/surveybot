# -*- coding: utf-8 -*-
"""Trace passive des déclenchements de règles d'extraction (BDL-4, étape 1).

Objectif : permettre à l'oracle de distinguer « élément absent du DOM »
d'« élément absorbé / supprimé par une règle ».

Principes :
- Additif uniquement : ce module n'est jamais importé par le code existant,
  ce sont les fonctions d'extraction qui appellent ``note_rule`` (une ligne).
- Jamais d'exception vers l'appelant : tout est noyé dans try/except.
- Pas de persistance ici (étape 2) : les événements vivent en mémoire,
  buffer thread-local, drainé par le hook de ``page_snapshot`` plus tard.
- Coût : append dans une liste thread-local, négligeable.

Convention d'événement (version 1) :
    rule_id  : identifiant stable de la règle (snake_case)
    effect   : "removed" (élément(s) retiré(s)) ou "merged" (absorbé/fusionné)
    ref      : identifiant du bloc/champ concerné (name / id / group_key),
               None si inconnu — jamais une valeur calculée coûteuse
    count    : nombre d'éléments retirés/absorbés (0 si inconnu)
"""

from __future__ import annotations

import threading

EVENT_VERSION = "1"

_VALID_EFFECTS = frozenset({"removed", "merged"})

_local = threading.local()


def _buffer():
    buf = getattr(_local, "events", None)
    if buf is None:
        buf = _local.events = []
    return buf


def note_rule(rule_id, effect, ref=None, count=0):
    """Enregistre un déclenchement de règle. Jamais d'exception."""
    try:
        if not rule_id or not isinstance(rule_id, str):
            return
        if effect not in _VALID_EFFECTS:
            return
        if count is None or not isinstance(count, int) or count < 0:
            count = 0
        _buffer().append({
            "v": EVENT_VERSION,
            "rule_id": rule_id,
            "effect": effect,
            "ref": ref if isinstance(ref, str) else None,
            "count": count,
        })
    except Exception:
        # La trace ne doit jamais interrompre l'extraction.
        return


def drain():
    """Retourne la liste des événements et vide le buffer.

    Sans persistance (étape 2), les événements non drainés sont perdus
    à la fin de l'analyse de la page — comportement voulu.
    """
    try:
        buf = _buffer()
        out = list(buf)
        del buf[:]
        return out
    except Exception:
        return []


def count_events(events=None):
    """Compte les événements par (rule_id, effect). Utilitaire de mesure."""
    try:
        events = _buffer() if events is None else events
        stats = {}
        for ev in events:
            key = (ev.get("rule_id"), ev.get("effect"))
            stats[key] = stats.get(key, 0) + 1
        return stats
    except Exception:
        return {}
    