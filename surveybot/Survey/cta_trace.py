# -*- coding: utf-8 -*-
"""Trace CTA passive, en mémoire (BDL-5, étape 1).

Objectif : produire un résultat CTA structuré pour chaque clic passant par
``cta_handler._click_with_intercept`` (point de passage commun des clics CTA),
sans oracle, sans failure case et sans persistance.

Principes :
- Additif : ``cta_handler.py`` n'est PAS modifié. L'enveloppe est posée par
  ``page_snapshot._install_cta_observer`` (même mécanisme que les observateurs
  d'extraction et d'action), le module n'est jamais importé par ``cta_handler``.
- Passif : l'enveloppe ne change ni le retour, ni l'ordre des appels, ni les
  exceptions de la fonction observée. Toute erreur d'observation est absorbée.
- Mémoire uniquement : buffer thread-local borné (anneau), drainé plus tard
  par un consommateur (oracle CTA, hors périmètre BDL-5 v1).
- Le booléen ``_did_progress`` n'est JAMAIS utilisé comme preuve : la trace
  expose ``marker_status`` et ``signals_changed`` calculés ici, indépendamment.
- Coût : en mode normal quelques ``evaluate`` légers par clic (cible, page,
  marqueur avant/après) ; en mode CTA_INTERCEPT_ONLY aucun marqueur n'est pris,
  seul le rapport d'interception (déjà présent dans la page) est lu.

Étape 2 — couverture déclarée :
Les clics natifs qui contournent ``_click_with_intercept`` (et les chemins
simulés sous CTA_INTERCEPT_ONLY) ne produisent aucun événement de clic. Pour que
l'oracle puisse S'ABSTENIR au lieu de conclure sur du vide, chaque appel de
plus haut niveau d'une fonction publique de cta_handler est classé :
    observed        : au moins un clic tracé pendant l'appel
    unobserved_path : l'appel a retourné une valeur vraie sans aucun clic tracé
                      (clic natif, repli JS, QPS skip, interception simulée)
    no_click        : retour faux sans clic tracé (rien trouvé / rien tenté)
    raised          : la fonction a levé
Ce sont des COMPTEURS (pas un événement par appel : les boucles d'attente
appellent ces fonctions souvent), plus un petit anneau de détails pour les
seuls ``unobserved_path``. Limite connue : un appel qui mélange clic tracé et
clic natif est classé ``observed``.

Convention d'événement de clic (version 1) :
    v               : version du format
    kind            : "click"
    fn              : fonction observée ("_click_with_intercept")
    caller          : fonction appelante dans cta_handler (None si inconnue)
    mode            : "normal" | "intercept"
    target          : {tag, id, name, className, text} de la cible, None si illisible
    returned        : booléen retourné par la fonction observée (None si exception)
    raised          : nom de l'exception si la fonction observée a levé, sinon None
    marker_status   : ok | empty_before | empty_after | both_empty | not_collected
    signals_changed : signaux qui diffèrent entre avant et après (liste fermée,
                      voir SIGNALS), vide si un marqueur est inexploitable
    marker_before / marker_after : marqueurs compacts (None en mode intercept)
    page_before / page_after     : {url, doc} (url, identité du document)
    intercept       : sous-ensemble du rapport d'interception (mode intercept)
    t_start / duration_ms        : horodatage (epoch s) et durée de l'appel
"""

from __future__ import annotations

import sys
import threading
import time
from collections import deque

EVENT_VERSION = "1"

# Taille max du buffer (anneau) : au-delà, les plus anciens sont écartés et
# comptés dans stats()["dropped"]. Évite toute croissance mémoire sans consommateur.
MAX_EVENTS = 200

# Liste fermée des signaux de changement exposés par la trace (v1).
#   url        : URL du contexte
#   doc        : identité du document (performance.timeOrigin), change à chaque
#                nouveau document, pas sur une SPA
#   txt        : empreinte du conteneur principal (220 premiers caractères)
#   qNodes     : indicateur de page (nombre de nœuds de question)
#   notifSig   : notification d'erreur/succès/warning
#   progGridSig: sous-catégorie GridProgressive (gabarit Ipsos/mrIWeb)
SIGNALS = ("url", "doc", "txt", "qNodes", "notifSig", "progGridSig")

# Fonctions publiques de cta_handler classées par la couverture déclarée.
# Exclu volontairement : _dismiss_blocking_overlays (fermeture d'overlays, pas un
# CTA de navigation).
ENTRY_FUNCTIONS = (
    "click_button_by_text",
    "click_icon_like_button",
    "click_primary_cta",
    "try_click_navigation_cta",
    "click_button_by_text_any_context",
    "click_icon_like_button_any_context",
    "click_primary_cta_any_context",
    "try_click_navigation_cta_any_context",
    "click_cta_strong_any_context",
    "try_click_qps_skip_to_survey",
)
OUTCOMES = ("observed", "unobserved_path", "no_click", "raised")
MAX_UNOBSERVED_DETAILS = 50

_MARKER_STR_CAP = 120   # bornage mémoire des chaînes de marqueur conservées
_TARGET_TEXT_CAP = 80

_local = threading.local()


# ---------------------------------------------------------------------------
# Buffer thread-local borné
# ---------------------------------------------------------------------------

def _state():
    st = getattr(_local, "state", None)
    if st is None:
        st = _local.state = {
            "events": deque(maxlen=MAX_EVENTS), "total": 0, "dropped": 0,
            # Couverture déclarée (étape 2)
            "depth": 0,
            "coverage": {},
            "unobserved": deque(maxlen=MAX_UNOBSERVED_DETAILS),
        }
    return st


def note_cta_event(event):
    """Enregistre un événement CTA. Jamais d'exception vers l'appelant."""
    try:
        if not isinstance(event, dict):
            return
        st = _state()
        if len(st["events"]) >= MAX_EVENTS:
            st["dropped"] += 1  # l'anneau écarte le plus ancien
        st["events"].append(event)
        st["total"] += 1
    except Exception:
        return


def drain():
    """Retourne la liste des événements et vide le buffer. Jamais d'exception."""
    try:
        st = _state()
        out = list(st["events"])
        st["events"].clear()
        return out
    except Exception:
        return []


def stats():
    """Retourne {total, buffered, dropped} (compteurs thread-local)."""
    try:
        st = _state()
        return {
            "total": st["total"],
            "buffered": len(st["events"]),
            "dropped": st["dropped"],
        }
    except Exception:
        return {"total": 0, "buffered": 0, "dropped": 0}


# ---------------------------------------------------------------------------
# Fonctions pures (testables hors navigateur)
# ---------------------------------------------------------------------------

def _is_empty_marker(marker):
    """Marqueur inexploitable : non-dict, ou celui que _dom_progress_marker
    retourne sur exception (URL vide ET qNodes à -1). Une vraie page a toujours
    une URL ; qNodes == 0 est une valeur légitime et n'est PAS un marqueur vide."""
    if not isinstance(marker, dict):
        return True
    return (not (marker.get("url") or "")) and marker.get("qNodes", -1) == -1


def marker_status(before, after):
    """Qualifie la fiabilité d'une paire de marqueurs.

    Remplace la lecture du booléen ``_did_progress`` : ce dernier retourne True
    sans preuve quand un marqueur est vide (voir audit BDL-5, Q2).
    """
    be, ae = _is_empty_marker(before), _is_empty_marker(after)
    if be and ae:
        return "both_empty"
    if be:
        return "empty_before"
    if ae:
        return "empty_after"
    return "ok"


def signals_changed(before, after, page_before=None, page_after=None):
    """Liste (ordre de SIGNALS) des signaux qui diffèrent entre avant et après.

    Les signaux du marqueur ne sont comparés que si les DEUX marqueurs sont
    exploitables. Le signal ``doc`` vient de la lecture indépendante de page
    et n'est comparé que si les deux identités sont non vides.
    Les valeurs sont comparées telles quelles (pas de conversion ``or -1``).
    """
    changed = []
    try:
        if marker_status(before, after) == "ok":
            for key in ("url", "txt", "qNodes", "notifSig", "progGridSig"):
                if before.get(key) != after.get(key):
                    changed.append(key)
        pb = page_before if isinstance(page_before, dict) else {}
        pa = page_after if isinstance(page_after, dict) else {}
        if pb.get("doc") and pa.get("doc") and pb.get("doc") != pa.get("doc"):
            changed.append("doc")
        # URL de la lecture indépendante, si le marqueur ne l'a pas déjà signalée.
        if (
            "url" not in changed
            and pb.get("url") and pa.get("url") and pb.get("url") != pa.get("url")
        ):
            changed.append("url")
    except Exception:
        return []
    return [s for s in SIGNALS if s in changed]


def compact_marker(marker):
    """Copie bornée d'un marqueur (chaînes tronquées) ; None si non-dict."""
    if not isinstance(marker, dict):
        return None
    out = {}
    for key in ("url", "txt", "qNodes", "notifSig", "progGridSig"):
        value = marker.get(key)
        if isinstance(value, str) and len(value) > _MARKER_STR_CAP:
            value = value[:_MARKER_STR_CAP]
        out[key] = value
    return out


def compact_intercept_report(report):
    """Sous-ensemble du rapport d'interception (mode CTA_INTERCEPT_ONLY)."""
    if not isinstance(report, dict):
        return None
    return {
        "clickCaptured": bool(report.get("clickCaptured")),
        "submitCaptured": bool(report.get("submitCaptured")),
        "prevented": bool(report.get("prevented")),
        "navigationBlocked": bool(report.get("navigationBlocked")),
    }


# ---------------------------------------------------------------------------
# Lectures navigateur (passives, best-effort)
# ---------------------------------------------------------------------------

_TARGET_JS = r"""(el) => {
  try {
    const cn = (el.className && el.className.baseVal !== undefined) ? el.className.baseVal : (el.className || '');
    return {
      tag: (el.tagName || '').toLowerCase(),
      id: el.id || '',
      name: (el.getAttribute && el.getAttribute('name')) || '',
      className: String(cn).slice(0, 80),
      text: ((el.innerText || el.textContent || el.value || '').replace(/\s+/g, ' ').trim()).slice(0, %d),
    };
  } catch (e) { return null; }
}""" % _TARGET_TEXT_CAP

# performance.timeOrigin identifie la vie du document : il change à chaque
# nouveau document (navigation classique) et reste stable sur une SPA.
# Lecture seule, aucune écriture dans la page.
_PAGE_JS = (
    "() => ({ url: String(location.href || ''), "
    "doc: (typeof performance !== 'undefined' && performance.timeOrigin) "
    "? String(performance.timeOrigin) : '' })"
)


def _read_target(el):
    try:
        value = el.evaluate(_TARGET_JS)
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def _read_page(handler, driver):
    try:
        value = handler._resolve_ctx(driver).evaluate(_PAGE_JS)
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def _read_marker(handler, driver):
    try:
        return handler._dom_progress_marker(driver)
    except Exception:
        return None


def _caller_name():
    """Nom de la fonction de cta_handler qui a appelé la fonction observée.

    Frame 0 = cette fonction, 1 = l'enveloppe, 2 = l'appelant réel.
    """
    try:
        return sys._getframe(2).f_code.co_name
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Enveloppe passive de _click_with_intercept
# ---------------------------------------------------------------------------

def build_observed_click(original, handler):
    """Construit l'enveloppe passive de ``handler._click_with_intercept``.

    ``handler`` est le module cta_handler (injecté : testable sans navigateur).
    L'enveloppe appelle ``original`` exactement une fois, avec les mêmes
    arguments, et retourne son résultat ou relève son exception à l'identique.
    """

    def observed_click_with_intercept(driver, el, *args, **kwargs):
        caller = _caller_name()
        intercept = False
        target = marker_before = page_before = None
        t_start = time.time()
        try:
            intercept = bool(handler._cta_intercept_enabled())
            target = _read_target(el)
            page_before = _read_page(handler, driver)
            if not intercept:
                # En mode intercept aucun marqueur n'est pris : l'absence de
                # navigation y est le comportement attendu.
                marker_before = _read_marker(handler, driver)
        except Exception:
            pass

        returned = None
        raised = None
        try:
            returned = original(driver, el, *args, **kwargs)
            return returned
        except BaseException as exc:
            raised = type(exc).__name__
            raise
        finally:
            # finally : la trace est posée même si la fonction observée lève ;
            # aucune exception d'observation ne peut remplacer celle d'origine.
            try:
                marker_after = page_after = report = None
                if not intercept:
                    marker_after = _read_marker(handler, driver)
                    page_after = _read_page(handler, driver)
                    status = marker_status(marker_before, marker_after)
                    changed = signals_changed(marker_before, marker_after, page_before, page_after)
                else:
                    # Le rapport persiste dans la page après désarmement.
                    try:
                        report = handler.read_intercept_report(driver)
                    except Exception:
                        report = None
                    status = "not_collected"
                    changed = []
                event = {
                    "v": EVENT_VERSION,
                    "kind": "click",
                    "fn": "_click_with_intercept",
                    "caller": caller,
                    "mode": "intercept" if intercept else "normal",
                    "target": target,
                    "returned": bool(returned) if raised is None else None,
                    "raised": raised,
                    "marker_status": status,
                    "signals_changed": changed,
                    "marker_before": compact_marker(marker_before),
                    "marker_after": compact_marker(marker_after),
                    "page_before": page_before,
                    "page_after": page_after,
                    "intercept": compact_intercept_report(report),
                    "t_start": t_start,
                    "duration_ms": int((time.time() - t_start) * 1000),
                }
                note_cta_event(event)
                _log_event(event)
            except Exception:
                pass

    return observed_click_with_intercept


def _log_event(event):
    """Une ligne de debug (LOG_LEVEL=DEBUG uniquement)."""
    try:
        from Survey.log_utils import log_debug
        tgt = event.get("target") or {}
        log_debug(
            "[CTA_TRACE]",
            f"caller={event.get('caller')} mode={event.get('mode')} "
            f"returned={event.get('returned')} status={event.get('marker_status')} "
            f"changed={','.join(event.get('signals_changed') or []) or '-'} "
            f"target={tgt.get('tag', '')}#{tgt.get('id', '')}",
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Étape 2 : couverture déclarée (appels de haut niveau classés)
# ---------------------------------------------------------------------------

def classify_entry(raised, returned, clicks_observed):
    """Classe un appel de haut niveau (fonction pure, testable hors navigateur)."""
    if raised:
        return "raised"
    if clicks_observed > 0:
        return "observed"
    return "unobserved_path" if returned else "no_click"


def _record_entry(name, outcome, returned, clicks_observed, mode, t_start):
    """Incrémente le compteur (fn, outcome) ; détaille les seuls unobserved_path."""
    st = _state()
    per_fn = st["coverage"].setdefault(name, {})
    per_fn[outcome] = per_fn.get(outcome, 0) + 1
    if outcome == "unobserved_path":
        st["unobserved"].append({
            "v": EVENT_VERSION,
            "kind": "entry",
            "fn": name,
            "outcome": outcome,
            "mode": mode,
            "clicks_observed": clicks_observed,
            "t_start": t_start,
            "duration_ms": int((time.time() - t_start) * 1000),
        })


def coverage():
    """Instantané de la couverture : {counts, totals, unobserved}. Sans remise à zéro."""
    try:
        st = _state()
        counts = {fn: dict(c) for fn, c in st["coverage"].items()}
        totals = {o: 0 for o in OUTCOMES}
        for c in counts.values():
            for outcome, n in c.items():
                totals[outcome] = totals.get(outcome, 0) + n
        totals["total"] = sum(totals[o] for o in OUTCOMES)
        return {"counts": counts, "totals": totals, "unobserved": list(st["unobserved"])}
    except Exception:
        return {"counts": {}, "totals": {o: 0 for o in OUTCOMES}, "unobserved": []}


def drain_coverage():
    """Retourne l'instantané de couverture puis remet les compteurs à zéro."""
    snap = coverage()
    try:
        st = _state()
        st["coverage"].clear()
        st["unobserved"].clear()
    except Exception:
        pass
    return snap


def build_observed_entry(original, name, handler):
    """Enveloppe passive d'une fonction publique de cta_handler.

    Seul l'appel de plus haut niveau est classé (profondeur thread-local) : les
    fonctions publiques s'appellent entre elles (ex. *_any_context -> version
    simple) et un appel imbriqué ne doit pas être compté deux fois. Retour et
    exceptions de ``original`` sont transmis à l'identique.
    """
    import functools

    @functools.wraps(original)
    def observed_entry(*args, **kwargs):
        try:
            st = _state()
            outer = st["depth"] == 0
            st["depth"] += 1
            clicks_before = st["total"]
            t_start = time.time()
        except Exception:
            return original(*args, **kwargs)

        returned = None
        raised = False
        try:
            returned = original(*args, **kwargs)
            return returned
        except BaseException:
            raised = True
            raise
        finally:
            try:
                st["depth"] = max(0, st["depth"] - 1)
                if outer:
                    clicks = st["total"] - clicks_before
                    outcome = classify_entry(raised, bool(returned), clicks)
                    try:
                        mode = "intercept" if handler._cta_intercept_enabled() else "normal"
                    except Exception:
                        mode = None
                    _record_entry(name, outcome, bool(returned), clicks, mode, t_start)
                    if outcome == "unobserved_path":
                        _log_unobserved(name, mode)
            except Exception:
                pass

    return observed_entry


def rebind_aliases(handler, original, wrapped, modules=None):
    """Remplace ``original`` par ``wrapped`` dans les modules qui l'ont importé PAR VALEUR.

    ``from Survey.cta_handler import click_primary_cta`` copie la référence au
    moment de l'import : remplacer l'attribut de cta_handler ne les atteint pas
    s'ils ont été importés avant l'installation. Cette passe rend la couverture
    indépendante de l'ordre d'import. Retourne le nombre de modules rebindés.
    Jamais d'exception.
    """
    count = 0
    try:
        mods = list(sys.modules.values()) if modules is None else list(modules)
        for mod in mods:
            try:
                if mod is None or mod is handler:
                    continue
                d = getattr(mod, "__dict__", None)
                if not isinstance(d, dict):
                    continue
                for key, value in list(d.items()):
                    if value is original:
                        d[key] = wrapped
                        count += 1
            except Exception:
                continue
    except Exception:
        return count
    return count


def _log_unobserved(name, mode):
    """Une ligne de debug par appel unobserved_path (rare)."""
    try:
        from Survey.log_utils import log_debug
        log_debug("[CTA_TRACE]", f"entry fn={name} outcome=unobserved_path mode={mode}")
    except Exception:
        pass
