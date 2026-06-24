# Survey/question_block_resolver.py
# ------------------------------------------------------------
# Question Block Resolver (robuste, scalable 100+ bots)
#
# Objectif:
# - Quand une page a plusieurs champs (ex: plusieurs inputs numériques),
#   on NE cherche plus un champ "par le contexte exact" via label->input.
# - On construit une "carte locale" : une liste de blocs {label, input, container}
#   en exploitant des heuristiques DOM (fieldset/div question/form-row etc.).
# - Puis on match "context_question" -> meilleur bloc, et on remplit le champ.
#
# Conçu pour:
# - HTML legacy, React, Angular, Decipher, PureSpectrum, etc.
# - DOM où le texte (question) et l'input ne sont pas reliés simplement.
#
# Sécurité:
# - Ne JAMAIS écraser un champ déjà rempli (sauf override explicite).
# - Effacement safe: Ctrl+A + Backspace, events input/change/blur.
# - Pas de loop infinie: un match = une tentative.
# ------------------------------------------------------------

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Playwright page helper
# ---------------------------------------------------------------------------


# -------------------------
# Utils texte / similarité
# -------------------------

def _norm_soft(s: str) -> str:
    """Normalisation douce pour matcher du texte de question."""
    if not s:
        return ""
    s = s.replace(" ", " ")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().strip()
    s = re.sub(r"\s+", " ", s)
    # on retire une ponctuation fréquente qui casse les contains()
    s = re.sub(r"[\"''""«»•·→,:;.!?()\[\]{}<>]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _jaccard_words(a: str, b: str) -> float:
    """Similarité simple par chevauchement de mots (robuste et pas cher)."""
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    aw = {w for w in _norm_soft(a).split() if len(w) >= 3}
    bw = {w for w in _norm_soft(b).split() if len(w) >= 3}
    if not aw or not bw:
        return 0.0
    return len(aw & bw) / max(1, len(aw | bw))


def _best_text_candidate(lines: List[str], *, min_len: int = 6) -> str:
    """
    Choisit une ligne "question-like" parmi un ensemble de lignes.
    Heuristique: préfère la plus longue ligne non-vide, non-triviale.
    """
    clean = []
    for ln in lines:
        t = (ln or "").strip()
        if not t:
            continue
        # ignore micro tokens
        if len(_norm_soft(t)) < min_len:
            continue
        clean.append(t)
    if not clean:
        return ""
    clean.sort(key=lambda x: len(_norm_soft(x)), reverse=True)
    return clean[0]


# -------------------------
# Détection inputs numériques
# -------------------------

_NUMERIC_PATTERN_HINT = re.compile(r"(zip|postal|code|age|année|annee|year|birth|naiss|phone|tel)", re.I)
_ONLY_DIGITS = re.compile(r"^\d+$")


def _is_numeric_input(el) -> bool:
    """
    Détermine si un élément <input> ressemble à un champ numérique.
    On accepte aussi certains input[type=text] quand le site force du numérique.
    """
    try:
        tag = el.evaluate("e => e.tagName.toLowerCase()")
    except Exception:
        return False
    if tag != "input":
        return False

    try:
        t = (el.get_attribute("type") or "").lower().strip()
        inputmode = (el.get_attribute("inputmode") or "").lower().strip()
        pattern = (el.get_attribute("pattern") or "").strip()
        name = (el.get_attribute("name") or "") + " " + (el.get_attribute("id") or "")
        ph = (el.get_attribute("placeholder") or "")
        aria = (el.get_attribute("aria-label") or "")
        mx = (el.get_attribute("maxlength") or "").strip()
        mn = (el.get_attribute("min") or "").strip()
        mxv = (el.get_attribute("max") or "").strip()
    except Exception:
        t, inputmode, pattern, name, ph, aria, mx, mn, mxv = "", "", "", "", "", "", "", "", ""

    # cas direct
    if t == "number":
        return True
    if inputmode in ("numeric", "decimal"):
        return True

    # pattern "digits" / "[0-9]" etc.
    if pattern and re.search(r"\[0-9\]|\d\{", pattern):
        return True

    # text mais fortement hinté numérique (zip/age/year) + maxlength raisonnable
    sig = f"{name} {ph} {aria}"
    if _NUMERIC_PATTERN_HINT.search(sig):
        return True

    # text avec min/max présents -> souvent numeric déguisé
    if (mn or mxv) and t in ("text", ""):
        return True

    # maxlength court (2..6) + placeholder purement numérique (rare mais existe)
    try:
        mx_i = int(mx) if mx.isdigit() else 0
    except Exception:
        mx_i = 0
    if t in ("text", "") and 2 <= mx_i <= 6:
        if ph and _ONLY_DIGITS.match(ph.strip()):
            return True

    return False


def _is_fillable(el) -> bool:
    """Visible + pas disabled/readonly."""
    try:
        if not el.is_visible():
            return False
        if el.get_attribute("disabled"):
            return False
        if el.get_attribute("readonly"):
            return False
        r = el.bounding_box() or {}
        if r.get("width", 0) < 12 or r.get("height", 0) < 10:
            return False
        return True
    except Exception:
        return False


def _get_value(el) -> str:
    try:
        return (el.get_attribute("value") or "").strip()
    except Exception:
        return ""


# -------------------------
# Extraction "bloc question"
# -------------------------

@dataclass
class NumberBlock:
    label: str                  # texte question le plus probable
    input_el: Any               # ElementHandle
    container_el: Any           # ElementHandle
    signature: str              # texte enrichi (label + aria + placeholder + container)
    y: float = 0.0              # position verticale approx (pour tie-break)
    filled: bool = False        # déjà rempli
    reason: str = ""            # debug


_CONTAINER_XPATHS = [
    # candidats "question blocks" classiques
    "ancestor::*[self::fieldset or @role='group' or @role='radiogroup' or @role='form' "
    "or contains(@class,'question') or contains(@class,'Question') "
    "or contains(@class,'form-group') or contains(@class,'field') or contains(@class,'row')][1]",
    # fallback plus large
    "ancestor::*[self::div or self::section or self::li][1]",
]

_HEAD_XPATH = (
    ".//*[self::legend or self::h1 or self::h2 or self::h3 or self::h4 or self::label "
    "or contains(@class,'question-text') or contains(@class,'QuestionText') "
    "or contains(@class,'question__title') or contains(@data-test-id,'question')]"
)


def _nearest_container(el):
    for xp in _CONTAINER_XPATHS:
        try:
            result = el.query_selector("xpath=" + xp)
            if result is not None:
                return result
        except Exception:
            continue
    return None


def _extract_label_from_dom(driver, el, container) -> Tuple[str, str]:
    """
    Retourne (best_label, reason).
    On empile:
    - label[for=id]
    - aria-label / aria-labelledby
    - placeholder
    - textes proches dans le container (legend/h1/h2/label...)
    """
    lines: List[str] = []
    reasons: List[str] = []

    # A) label[for=id]
    try:
        eid = (el.get_attribute("id") or "").strip()
        if eid:
            try:
                lbl = driver.query_selector(f"xpath=//label[@for={_xpath_literal(eid)}]")
                if lbl is not None:
                    txt = (lbl.inner_text() or "").strip()
                    if txt:
                        lines.append(txt)
                        reasons.append("label[for=id]")
            except Exception:
                pass
    except Exception:
        pass

    # B) aria-label
    try:
        aria = (el.get_attribute("aria-label") or "").strip()
        if aria:
            lines.append(aria)
            reasons.append("aria-label")
    except Exception:
        pass

    # C) aria-labelledby
    try:
        labby = (el.get_attribute("aria-labelledby") or "").strip()
        if labby:
            for ref in labby.split():
                try:
                    n = driver.query_selector(f"#{ref}")
                    txt = (n.inner_text() or "").strip()  # AttributeError if n is None → caught
                    if txt:
                        lines.append(txt)
                        reasons.append("aria-labelledby")
                except Exception:
                    continue
    except Exception:
        pass

    # D) placeholder
    try:
        ph = (el.get_attribute("placeholder") or "").strip()
        if ph:
            lines.append(ph)
            reasons.append("placeholder")
    except Exception:
        pass

    # E) texte dans le container
    if container is not None:
        try:
            heads = container.query_selector_all("xpath=" + _HEAD_XPATH)
            for h in heads[:25]:
                try:
                    txt = (h.inner_text() or "").strip()
                    if txt:
                        lines.append(txt)
                except Exception:
                    continue
            if heads:
                reasons.append("container-heads")
        except Exception:
            pass

        # F) texte brut du container (fallback)
        try:
            raw = (container.inner_text() or "").strip()
            if raw:
                # souvent il y a plein de texte; on split en lignes
                for ln in raw.splitlines():
                    if ln and ln.strip():
                        lines.append(ln.strip())
                reasons.append("container-innerText")
        except Exception:
            pass

    best = _best_text_candidate(lines)
    return best, "|".join(reasons)


def _build_signature(driver, el, container, label: str) -> str:
    """
    Signature enrichie: aide le matching (context -> bloc).
    """
    bits = []
    if label:
        bits.append(label)

    for attr in ("name", "id", "aria-label", "placeholder"):
        try:
            v = (el.get_attribute(attr) or "").strip()
            if v:
                bits.append(v)
        except Exception:
            pass

    # un peu du texte container (limité)
    if container is not None:
        try:
            t = (container.inner_text() or "").strip()
            if t:
                # limiter pour ne pas exploser
                t = re.sub(r"\s+", " ", t)
                bits.append(t[:220])
        except Exception:
            pass

    return _norm_soft(" ".join(bits))


def _xpath_literal(s: str) -> str:
    """Encode une string pour XPath (évite les soucis quotes)."""
    if "'" not in s:
        return f"'{s}'"
    if '"' not in s:
        return f'"{s}"'
    parts = s.split("'")
    return "concat(" + ", \"'\", ".join([f"'{p}'" for p in parts]) + ")"


def extract_number_blocks(driver) -> List[NumberBlock]:
    """
    Scan DOM -> retourne une liste structurée de NumberBlocks.
    On se limite à des inputs "numériques" + fillables.
    """
    blocks: List[NumberBlock] = []

    try:
        inputs = driver.query_selector_all("input")
    except Exception:
        return blocks

    for el in inputs:
        try:
            if not _is_numeric_input(el):
                continue
            if not _is_fillable(el):
                continue

            container = _nearest_container(el) or driver
            label, reason = _extract_label_from_dom(driver, el, container)
            sig = _build_signature(driver, el, container, label)

            try:
                y = float((el.bounding_box() or {}).get("y", 0.0))
            except Exception:
                y = 0.0

            filled = bool(_get_value(el))

            blocks.append(
                NumberBlock(
                    label=label,
                    input_el=el,
                    container_el=container,
                    signature=sig,
                    y=y,
                    filled=filled,
                    reason=reason,
                )
            )
        except Exception:
            continue

    return blocks


# -------------------------
# Sélection du meilleur bloc
# -------------------------

def _score_block(context: str, b: NumberBlock) -> float:
    """
    Score de matching context -> bloc.
    - jaccard(label, context) + jaccard(signature, context) pondéré
    - bonus si inclusion substring
    - pénalité si bloc déjà rempli
    """
    ctx = _norm_soft(context)
    if not ctx:
        return 0.0

    lab = _norm_soft(b.label)
    sig = b.signature or ""

    s1 = _jaccard_words(ctx, lab)
    s2 = _jaccard_words(ctx, sig)

    bonus = 0.0
    if lab and (ctx in lab or lab in ctx):
        bonus += 0.25
    if sig and (ctx in sig):
        bonus += 0.15

    penalty = 0.0
    if b.filled:
        penalty -= 0.75  # gros stop: on évite d'écraser
    return (0.65 * s1) + (0.35 * s2) + bonus + penalty


def choose_best_number_block(
    blocks: List[NumberBlock],
    *,
    context_question: str,
    min_score: float = 0.75,
    driver=None,
) -> Tuple[Optional[NumberBlock], float]:
    """
    Retourne (best_block, score).
    """
    if not blocks:
        return None, 0.0

    best = None
    best_score = -1e9
    for b in blocks:
        sc = _score_block(context_question, b)
        if sc > best_score:
            best, best_score = b, sc

    # tie-break: si score proche, on préfère un bloc non-rempli plus haut dans la page
    if best and best_score >= min_score:
        return best, best_score

    return None, best_score


# -------------------------
# Remplissage safe
# -------------------------

def _dispatch_events(driver, el):
    try:
        el.evaluate(
            "(e) => { for (const t of ['input','change','blur']) {"
            " try { e.dispatchEvent(new Event(t, {bubbles:true})); } catch(_) {} } }"
        )
    except Exception:
        pass


def _safe_focus(driver, el) -> None:
    try:
        el.scroll_into_view_if_needed()
    except Exception:
        pass
    time.sleep(0.05)
    try:
        el.click()
        return
    except Exception:
        pass
    try:
        el.hover()
        el.click()
        return
    except Exception:
        pass
    try:
        el.focus()
    except Exception:
        pass


def _safe_clear(driver, el) -> None:
    """
    Effacement sans casser Angular/React:
    - Ctrl+A + Backspace
    - si ça échoue, on tente JS value="" + events
    """
    try:
        el.press("Control+a")
        el.press("Backspace")
        return
    except Exception:
        pass
    try:
        el.evaluate(
            "(e) => { e.value = '';"
            " e.dispatchEvent(new Event('input', {bubbles:true}));"
            " e.dispatchEvent(new Event('change', {bubbles:true})); }"
        )
        _dispatch_events(driver, el)
    except Exception:
        pass


def _sanitize_numeric_value(value: str) -> str:
    """
    Nettoie une valeur à injecter dans un champ "numérique".
    - garde chiffres (et éventuellement un point si decimal)
    """
    if value is None:
        return ""
    v = str(value).strip()
    # autoriser cas "28" / "1996" / "95000"
    digits = re.sub(r"[^\d]", "", v)
    return digits


def fill_number_input(
    driver,
    input_el,
    value: str,
    *,
    allow_overwrite: bool = False,
) -> bool:
    """
    Remplit un input numérique sans casser les frameworks.
    """
    try:
        if not _is_fillable(input_el):
            return False

        current = _get_value(input_el)
        if current and not allow_overwrite:
            # Ne jamais écraser un champ déjà rempli
            return False

        v = _sanitize_numeric_value(value)
        if not v:
            return False

        _safe_focus(driver, input_el)
        time.sleep(0.05)

        if allow_overwrite:
            _safe_clear(driver, input_el)
            time.sleep(0.03)

        # typing humain court
        try:
            input_el.type(v)
        except Exception:
            try:
                input_el.evaluate(
                    "(e, v) => { e.value = v;"
                    " e.dispatchEvent(new Event('input', {bubbles:true}));"
                    " e.dispatchEvent(new Event('change', {bubbles:true})); }",
                    v,
                )
            except Exception:
                return False

        _dispatch_events(driver, input_el)

        # vérif légère
        newv = _get_value(input_el)
        if not newv:
            # certains champs ne reflètent pas value immédiatement, mais on tente une seconde micro-action
            try:
                input_el.press("Tab")
            except Exception:
                pass
            time.sleep(0.03)
            newv = _get_value(input_el)

        return bool(newv)
    except Exception:
        return False


# -------------------------
# API principale (celle que tu appelles depuis action_dispatcher.py)
# -------------------------

def try_resolve_number_block(
    driver,
    *,
    context_question: str,
    value: str,
    min_score: float = 0.75,
    allow_overwrite: bool = False,
    debug: bool = True,
) -> bool:
    """
    1) extrait les NumberBlocks sur la page
    2) match context_question -> meilleur bloc
    3) remplit ce bloc si score >= min_score

    Retourne True si rempli, sinon False.
    """
    try:
        blocks = extract_number_blocks(driver)
        if not blocks:
            if debug:
                print("[QBR] aucun bloc numérique détecté.")
            return False

        # éviter de réutiliser exactement le même champ plusieurs fois dans une session
        try:
            used = getattr(driver, "_qbr_used_inputs", set())
            if not isinstance(used, set):
                used = set()
        except Exception:
            used = set()

        # on marque filled en tenant compte du "used"
        for b in blocks:
            try:
                bid = b.input_el._id  # type: ignore
            except Exception:
                bid = id(b.input_el)
            if bid in used:
                b.filled = True

        best, score = choose_best_number_block(
            blocks,
            context_question=context_question,
            min_score=min_score,
            driver=driver,
        )

        if best is None or score < min_score:
            if debug:
                # debug minimal (pas trop verbeux)
                top = sorted([(b.label, _score_block(context_question, b), b.filled) for b in blocks],
                             key=lambda x: x[1], reverse=True)[:3]
                print(f"[QBR] match faible (score={score:.2f} < {min_score}). top={top}")
            return False

        ok = fill_number_input(driver, best.input_el, value, allow_overwrite=allow_overwrite)
        if ok:
            # marquer utilisé pour empêcher les doublons
            try:
                bid = best.input_el._id  # type: ignore
            except Exception:
                bid = id(best.input_el)
            used.add(bid)
            try:
                setattr(driver, "_qbr_used_inputs", used)
            except Exception:
                pass

            if debug:
                lbl = (best.label or "").strip()
                lbl = lbl[:120] + ("…" if len(lbl) > 120 else "")
                print(f"[QBR] ✅ champ numérique rempli | score={score:.2f} | label='{lbl}' | value='{value}'")
            return True

        if debug:
            print(f"[QBR] échec de remplissage malgré match score={score:.2f}.")
        return False

    except Exception as e:
        if debug:
            print(f"[QBR] exception: {type(e).__name__}: {e}")
        return False
