import os
import re
import time
import unicodedata

from config import should_pause_before_cta, is_cta_intercept_only
from Survey.log_utils import log_info, log_debug



# ---------------------------------------------------------------------------
# DIAGNOSTIC TEMPORAIRE — isTrusted (retirer après confirmation)
# Activer avec : DIAG_ISTRUSTED=1
# ---------------------------------------------------------------------------
_DIAG_ISTRUSTED = os.environ.get("DIAG_ISTRUSTED", "").strip() not in ("", "0")

_JS_ATTACH_DIAG_LISTENER = """
(function(el) {
    window.__diag_istrusted__ = null;
    el.__diagListener__ = function(e) {
        window.__diag_istrusted__ = {
            isTrusted: e.isTrusted,
            type: e.type,
            timestamp: e.timeStamp
        };
        el.removeEventListener('click', el.__diagListener__, true);
    };
    el.addEventListener('click', el.__diagListener__, true);
})(arguments[0]);
"""

def _diag_attach(driver, element, tag: str) -> None:
    """Injecte un listener capture-phase sur element avant le clic JS."""
    if not _DIAG_ISTRUSTED:
        return
    try:
        # element est un ElementHandle Playwright natif
        driver.evaluate("""(el) => {
            window.__diag_istrusted__ = null;
            el.__diagListener__ = function(e) {
                window.__diag_istrusted__ = {isTrusted: e.isTrusted, type: e.type, timestamp: e.timeStamp};
                el.removeEventListener('click', el.__diagListener__, true);
            };
            el.addEventListener('click', el.__diagListener__, true);
        }""", element)
    except Exception as exc:
        log_debug("diag_istrusted", f"[DIAG_ISTRUSTED] attach failed tag={tag}: {exc}")


def _diag_read(driver, tag: str) -> None:
    """Lit et logue window.__diag_istrusted__ après le clic JS."""
    if not _DIAG_ISTRUSTED:
        return
    try:
        result = driver.evaluate("() => window.__diag_istrusted__ || null")
        if result:
            log_info(
                "diag_istrusted",
                f"[DIAG_ISTRUSTED] tag={tag} isTrusted={result.get('isTrusted')} "
                f"type={result.get('type')} timestamp={result.get('timestamp')}",
            )
        else:
            log_info("diag_istrusted", f"[DIAG_ISTRUSTED] tag={tag} listener n'a pas capturé d'événement")
    except Exception as exc:
        log_debug("diag_istrusted", f"[DIAG_ISTRUSTED] read failed tag={tag}: {exc}")

# ---------------------------------------------------------------------------
# DIAGNOSTIC TEMPORAIRE — échec clic label checkbox (retirer après confirmation)
# Activer avec : DIAG_CHECKBOX_CLICK=1
# ---------------------------------------------------------------------------
_DIAG_CHECKBOX_CLICK = os.environ.get("DIAG_CHECKBOX_CLICK", "").strip() not in ("", "0")

# Note : préfixer la IIFE avec "return" pour que la valeur traverse l'enveloppe
# (args) => (function() { <script> }).apply(null, args) du shim execute_script.
# Sans "return", la IIFE interne retourne mais la fonction externe retourne undefined → None.
_JS_DIAG_LABEL_STATE = """
return (function(el) {
    if (!el.isConnected) return {connected: false};
    var rect = el.getBoundingClientRect();
    var cx = rect.left + rect.width / 2, cy = rect.top + rect.height / 2;
    var st = window.getComputedStyle(el);
    var top = document.elementFromPoint(cx, cy);
    var topDesc = 'none';
    if (top) {
        var tid = top.getAttribute('data-test-id');
        var cls = top.className ? top.className.trim().split(/\s+/).slice(0, 3).join('.') : '';
        topDesc = top.tagName.toLowerCase() + (tid ? '[data-test-id=' + tid + ']' : (cls ? '.' + cls : ''));
    }
    return {
        connected: true,
        rect: {x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height)},
        display: st.display,
        visibility: st.visibility,
        opacity: st.opacity,
        pointerEvents: st.pointerEvents,
        topElement: topDesc,
        isSelf: top === el
    };
})(arguments[0]);
"""

def _diag_checkbox_failure(driver, label, label_text: str, exc: Exception) -> None:
    """Capture l'état DOM du label au moment de l'échec du clic — DIAG_CHECKBOX_CLICK=1."""
    if not _DIAG_CHECKBOX_CLICK:
        return
    # label est un ElementHandle natif Playwright
    handle_ok = "unknown"
    try:
        tag = label.evaluate("(el) => el.tagName.toLowerCase()")
        handle_ok = f"valid(tag={tag})"
    except Exception as h_exc:
        handle_ok = f"invalid({type(h_exc).__name__}: {h_exc})"
    try:
        state = label.evaluate("""(el) => {
            if (!el.isConnected) return {connected: false};
            var rect = el.getBoundingClientRect();
            var cx = rect.left + rect.width / 2, cy = rect.top + rect.height / 2;
            var st = window.getComputedStyle(el);
            var top = document.elementFromPoint(cx, cy);
            var topDesc = 'none';
            if (top) {
                var tid = top.getAttribute('data-test-id');
                var cls = top.className ? top.className.trim().split(/\\s+/).slice(0, 3).join('.') : '';
                topDesc = top.tagName.toLowerCase() + (tid ? '[data-test-id=' + tid + ']' : (cls ? '.' + cls : ''));
            }
            return {
                connected: true,
                rect: {x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height)},
                display: st.display, visibility: st.visibility, opacity: st.opacity,
                pointerEvents: st.pointerEvents, topElement: topDesc, isSelf: top === el
            };
        }""")
        log_info(
            "diag_checkbox_click",
            f"[DIAG_CHECKBOX_CLICK] label={label_text!r} exc={type(exc).__name__} "
            f"handle={handle_ok} state={state}",
        )
    except Exception as diag_exc:
        log_info(
            "diag_checkbox_click",
            f"[DIAG_CHECKBOX_CLICK] label={label_text!r} exc={type(exc).__name__} "
            f"handle={handle_ok} script_exc={type(diag_exc).__name__}: {diag_exc}",
        )
    pw_log = str(exc).replace("\n", " | ")
    log_info("diag_checkbox_click", f"[DIAG_CHECKBOX_CLICK] pw_calllog={pw_log!r}")

def _diag_pw_actionability(label, label_text: str) -> None:
    """Vérifie les critères d'actionabilité Playwright natifs avant le clic — DIAG_CHECKBOX_CLICK=1.
    label est un ElementHandle natif (BLOC 2 migré) ou un PlaywrightElementShim (prod)."""
    if not _DIAG_CHECKBOX_CLICK:
        return
    try:
        # Supporte les deux cas : ElementHandle natif ou PlaywrightElementShim (._h)
        h = label
        pw_visible = h.is_visible()
        pw_enabled = h.is_enabled()
        pw_stable = "unknown"
        try:
            h.wait_for_element_state("stable", timeout=500)
            pw_stable = "ok"
        except Exception as se:
            pw_stable = f"TIMEOUT({type(se).__name__})"
        log_info(
            "diag_checkbox_click",
            f"[DIAG_CHECKBOX_CLICK_PRE] label={label_text!r} "
            f"pw_visible={pw_visible} pw_enabled={pw_enabled} pw_stable={pw_stable}",
        )
    except Exception as pre_exc:
        log_info(
            "diag_checkbox_click",
            f"[DIAG_CHECKBOX_CLICK_PRE] label={label_text!r} "
            f"pre_exc={type(pre_exc).__name__}: {pre_exc}",
        )

# ---------------------------------------------------------------------------
# DIAGNOSTIC TEMPORAIRE — stabilité géométrique avant clic label (retirer après confirmation)
# Activer avec : DIAG_STABILITY=1
# ---------------------------------------------------------------------------
_DIAG_STABILITY = os.environ.get("DIAG_STABILITY", "").strip() not in ("", "0")

# Rect + scrollTop du premier ancêtre scrollable (overflowY scroll/auto)
_JS_SAMPLE_RECT_EXT = """
return (function(el) {
    var r = el.getBoundingClientRect();
    var scrollEl = null, p = el.parentElement;
    while (p && p !== document.body) {
        var ov = window.getComputedStyle(p).overflowY;
        if (ov === 'scroll' || ov === 'auto') { scrollEl = p; break; }
        p = p.parentElement;
    }
    return {
        x: Math.round(r.x * 10) / 10, y: Math.round(r.y * 10) / 10,
        w: Math.round(r.width * 10) / 10, h: Math.round(r.height * 10) / 10,
        st: scrollEl ? Math.round(scrollEl.scrollTop * 10) / 10 : null
    };
})(arguments[0]);
"""

# Animations/transitions CSS actives sur le label et toute la chaîne d'ancêtres
_JS_CHECK_ANIMATIONS = """
return (function(el) {
    var out = [];
    var node = el;
    while (node && node !== document.body) {
        if (typeof node.getAnimations === 'function') {
            var anims = node.getAnimations();
            if (anims.length) {
                out.push({
                    el: node.tagName.toLowerCase() + (node.className ? '.' + node.className.trim().split(/\\s+/)[0] : ''),
                    anims: anims.map(function(a) { return (a.animationName || 'transition') + ':' + a.playState; })
                });
            }
        }
        node = node.parentElement;
    }
    return out;
})(arguments[0]);
"""

_JS_SAMPLE_RECT_EXT_PW = """(el) => {
    var r = el.getBoundingClientRect();
    var scrollEl = null, p = el.parentElement;
    while (p && p !== document.body) {
        var ov = window.getComputedStyle(p).overflowY;
        if (ov === 'scroll' || ov === 'auto') { scrollEl = p; break; }
        p = p.parentElement;
    }
    return {
        x: Math.round(r.x * 10) / 10, y: Math.round(r.y * 10) / 10,
        w: Math.round(r.width * 10) / 10, h: Math.round(r.height * 10) / 10,
        st: scrollEl ? Math.round(scrollEl.scrollTop * 10) / 10 : null
    };
}"""

_JS_CHECK_ANIMATIONS_PW = """(el) => {
    var out = [], node = el;
    while (node && node !== document.body) {
        if (typeof node.getAnimations === 'function') {
            var anims = node.getAnimations();
            if (anims.length) {
                out.push({
                    el: node.tagName.toLowerCase() + (node.className ? '.' + node.className.trim().split(/\\s+/)[0] : ''),
                    anims: anims.map(function(a) { return (a.animationName || 'transition') + ':' + a.playState; })
                });
            }
        }
        node = node.parentElement;
    }
    return out;
}"""


def _diag_sample_stability(driver, label, label_text: str) -> None:
    """Échantillonne rect + scroll ancêtre + animations CSS du label N fois — DIAG_STABILITY=1."""
    if not _DIAG_STABILITY:
        return
    _N = 15
    _INTERVAL = 0.2
    anim_info = []
    try:
        anim_info = label.evaluate(_JS_CHECK_ANIMATIONS_PW) or []
    except Exception as ae:
        anim_info = [f"ERR_ANIM:{type(ae).__name__}"]
    samples = []
    for _ in range(_N):
        try:
            r = label.evaluate(_JS_SAMPLE_RECT_EXT_PW)
            samples.append(
                (r.get("x"), r.get("y"), r.get("w"), r.get("h"), r.get("st")) if r else None
            )
        except Exception as se:
            samples.append(f"ERR:{type(se).__name__}")
            break
        time.sleep(_INTERVAL)
    valid = [s for s in samples if isinstance(s, tuple)]
    if valid:
        xs, ys = [s[0] for s in valid], [s[1] for s in valid]
        sts = [s[4] for s in valid if s[4] is not None]
        dx, dy = max(xs) - min(xs), max(ys) - min(ys)
        dscroll = (max(sts) - min(sts)) if sts else None
        geo = f"MOVING(dx={dx:.1f} dy={dy:.1f})" if (dx > 0.5 or dy > 0.5) else "STABLE"
        scroll = (f"SCROLLING(d={dscroll:.1f})" if dscroll and dscroll > 0.5
                  else ("SCROLL_STABLE" if dscroll is not None else "NO_SCROLL_ANCESTOR"))
        status = f"{geo} {scroll}"
    else:
        status = "NO_VALID_SAMPLES"
    log_info(
        "diag_stability",
        f"[DIAG_STABILITY] label={label_text!r} status={status} n={len(valid)}/{_N} "
        f"animations={anim_info} samples={samples}",
    )
# ---------------------------------------------------------------------------


def normalize(text):
    """Nettoie une chaîne pour comparaison souple (ASCII pur, sans accents ni apostrophes)"""
    # Remplacements avant décomposition
    text = text.replace("€", "e").replace("–", "-")
    # Normalisation Unicode → décomposition, puis suppression des combining characters (Mn)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower().strip()
    # Suppression des apostrophes sous toutes leurs formes Unicode et des espaces
    text = re.sub(r"['\u2018\u2019\u201a\u201b\u02bc\u02b9\u0060\u00b4\uff07]", "", text)
    text = text.replace(",", "").replace(" ", "")
    return text.rstrip(".!?")


def _is_checked_soft(el) -> bool:
    """el est un ElementHandle Playwright natif."""
    try:
        tag = el.evaluate("(el) => el.tagName.toLowerCase()")
        if tag == "label":
            try:
                cb = el.query_selector("input[type='checkbox']")
                if cb:
                    return cb.is_checked()
            except Exception:
                pass
        if tag == "input" and (el.get_attribute("type") or "").lower() == "checkbox":
            return el.is_checked()
    except Exception:
        pass
    return False


def _execute_async_radio(driver, answer_text) -> bool:  # noqa: C901
    """
    Handler pour le pattern 'async-answer' (Prime Opinion / PureSpectrum) :
    - Saisit la réponse dans le champ de recherche texte (filtrage dynamique)
    - Attend que les options radio filtrées apparaissent
    - Clique sur le radio correspondant
    - Clique sur le CTA de navigation

    Ce flow est nécessaire car les options ne sont pas pré-rendues : elles apparaissent
    dynamiquement après saisie, et un clic direct sur un radio absent échouerait.

    Fallback "premier résultat disponible" :
    Certains champs async_radio ne listent pas les entités exactes retournées par GPT
    (ex : le champ "Région" peut en réalité lister des communes ou des sous-divisions).
    Si la recherche exacte ne produit aucun résultat, on retente avec un préfixe court
    (2 premières lettres, puis 1 seule) et on sélectionne le premier radio disponible.
    Cela couvre les cas où la granularité des données du widget diverge du niveau
    géographique ou catégoriel supposé par GPT.
    """
    page = driver
    norm_answer = normalize(answer_text)

    # 1. Localiser le champ de recherche texte
    search_input = None
    for sel in [
        "[data-test-id='ps-async-answer-input-input']",
        "[data-test-id='ps-async-answer-input'] input",
        ".async-answer input[type='text']",
    ]:
        try:
            search_input = page.query_selector(sel)
            if search_input:
                break
        except Exception:
            pass

    if not search_input:
        print("❌ [async_radio] Champ de recherche introuvable.")
        return False

    def _type_in_search(text: str) -> bool:
        """Saisit `text` dans le champ de recherche et déclenche les events de filtrage."""
        try:
            search_input.evaluate("(el) => el.scrollIntoView({block:'center'})")
            search_input.fill("")
            search_input.type(str(text))
            search_input.evaluate(
                "(el) => { el.dispatchEvent(new Event('input', {bubbles:true}));"
                " el.dispatchEvent(new Event('change', {bubbles:true})); }"
            )
            return True
        except Exception as e:
            print(f"❌ [async_radio] Échec saisie champ recherche : {e}")
            return False

    def _collect_radio_labels() -> list:
        """Retourne les labels radio visibles après filtrage (attente 1.5 s incluse)."""
        time.sleep(1.5)
        try:
            return page.query_selector_all(
                'label[data-test-id^="ps-question-input-single_choice-label"]'
            )
        except Exception:
            return []

    def _click_radio_label(label) -> bool:
        """Clique sur un label radio. Retourne True si sélectionné.

        Après le clic JS, le widget async_radio re-rend le DOM (la liste filtrée disparaît).
        Si l'élément est stale, le clic a bien déclenché le re-render — considéré réussi.
        """
        try:
            label.evaluate("(el) => el.scrollIntoView({block:'center'})")
            time.sleep(0.3)
            radio = label.query_selector("input[type='radio']")
            _diag_attach(driver, radio, "_click_radio_label")
            radio.click()
            _diag_read(driver, "_click_radio_label")
            time.sleep(0.5)
            try:
                if not radio.is_checked():
                    label.hover()
                    label.click()
                    time.sleep(0.5)
            except Exception:
                # Stale : DOM re-rendu après clic, considéré réussi.
                pass
            return True
        except Exception as e:
            print(f"❌ [async_radio] Échec clic radio : {e}")
            return False

    def _get_label_text(label) -> str:
        """Extrait le texte visible d'un label radio (textContent prioritaire)."""
        try:
            spans = label.query_selector_all('span[class*="p-radio-text"]')
            for span in spans:
                t = span.get_attribute("textContent") or span.inner_text() or ""
                if t.strip():
                    return t.strip()
        except Exception:
            pass
        try:
            return label.inner_text().strip()
        except Exception:
            return ""

    # ── Étape 1 : recherche avec la valeur complète retournée par GPT ──────────
    log_info("response_executor", f"[async_radio] Texte saisi dans champ recherche : {answer_text}")
    if not _type_in_search(answer_text):
        return False

    labels = _collect_radio_labels()

    for label in labels:
        label_text = _get_label_text(label)
        if norm_answer in normalize(label_text) or normalize(label_text) in norm_answer:
            if _click_radio_label(label):
                log_info("response_executor", f"[async_radio] Option sélectionnée (exact) : {label_text}")
                click_next_button(driver)
                return True
            return False

    # ── Étape 2 : fallback préfixe court (2 lettres) → premier résultat ────────
    # Cas typique : le champ attend des communes alors que GPT a répondu une région.
    # On prend les 2 premières lettres non-diacritiques pour déclencher le filtrage,
    # puis on sélectionne simplement le premier radio disponible.
    prefix_candidates = []
    clean = re.sub(r"[^a-zA-Z]", "", answer_text)  # lettres seulement
    if len(clean) >= 2:
        prefix_candidates.append(clean[:2])
    if len(clean) >= 1:
        prefix_candidates.append(clean[:1])

    for prefix in prefix_candidates:
        log_info("response_executor", f"[async_radio] Fallback préfixe '{prefix}' → premier résultat")
        if not _type_in_search(prefix):
            continue

        fallback_labels = _collect_radio_labels()
        if not fallback_labels:
            continue

        # Ignorer les messages "Aucune réponse trouvée" qui s'affichent parfois comme pseudo-label
        for fl in fallback_labels:
            fl_text = _get_label_text(fl)
            if not fl_text or "aucune" in fl_text.lower() or "modifie" in fl_text.lower():
                continue
            if _click_radio_label(fl):
                log_info("response_executor", f"[async_radio] Option sélectionnée (fallback préfixe '{prefix}') : {fl_text}")
                click_next_button(driver)
                return True
            return False

    print(f"❌ [async_radio] Aucun radio trouvé pour : {answer_text} (ni en exact ni en préfixe)")
    return False


def execute_response(driver, answer_text, input_type=None):
    if input_type == "async_radio":
        return _execute_async_radio(driver, answer_text)

    if not answer_text:
        print("⏭️ Aucun choix détecté — pas d'action sur cette page. source: reponse_executor.py")
        return False

    page = driver
    print(f"🌟 Tentative de sélection : {answer_text} source: reponse_executor.py")
    norm_answer = normalize(answer_text)
    checkbox_answers = [
        chunk.strip() for chunk in str(answer_text).split("|") if chunk.strip()
    ]

    try:
        # 1) Tentative checkbox en priorité
        success = select_checkbox_answers(driver, checkbox_answers)
        if success:
            click_next_button(driver)
            return success

        # 2) Si le type d'input est explicitement checkbox, pas de fallback radio
        if input_type == "checkbox":
            print("❌ Option checkbox non cochée. Pas de fallback radio (type=checkbox confirmé).")
            return False

        # 2.5) Champ texte libre (input_text)
        text_input = page.query_selector('input[data-test-id*="input_text-input"]')
        if text_input is not None:
            text_input.evaluate("(el) => el.scrollIntoView({block:'center'})")
            text_input.fill("")
            text_input.type(str(answer_text))
            text_input.evaluate(
                "(el) => { el.dispatchEvent(new Event('input', {bubbles:true}));"
                " el.dispatchEvent(new Event('change', {bubbles:true})); }"
            )
            time.sleep(1)
            log_info("response_executor", f"✅ Champ texte rempli : {answer_text}")
            click_next_button(driver)
            return True

        # 2.6) Champ numérique entier (input_int)
        int_input = page.query_selector('input[data-test-id*="input_int-input"]')
        if int_input is not None:
            raw_digits = re.sub(r"[^\d]", "", str(answer_text))
            if not raw_digits:
                raw_digits = str(answer_text).strip()
            int_input.evaluate("(el) => el.scrollIntoView({block:'center'})")
            int_input.fill("")
            int_input.type(raw_digits)
            int_input.evaluate(
                "(el) => { el.dispatchEvent(new Event('input', {bubbles:true}));"
                " el.dispatchEvent(new Event('change', {bubbles:true})); }"
            )
            time.sleep(1)
            log_info("response_executor", f"✅ Champ numérique rempli : {raw_digits}")
            click_next_button(driver)
            return True

        # 3) Tentative radio
        labels = page.query_selector_all(
            'label[data-test-id^="ps-question-input-single_choice-label"]'
        )
        for label in labels:
            spans = label.query_selector_all('span[class*="p-radio-text"]')
            for span in spans:
                span_text = span.inner_text() or ""
                if norm_answer in normalize(span_text) or normalize(span_text) in norm_answer:
                    label.evaluate("(el) => el.scrollIntoView({block:'center'})")
                    # L'input radio peut être CSS-masqué — on clique le label visible
                    # qui déclenche nativement la sélection (isTrusted=true).
                    radio = label.query_selector("input[type='radio']")
                    time.sleep(0.5)
                    _diag_attach(driver, label, "execute_response_radio_main")
                    label.click()
                    _diag_read(driver, "execute_response_radio_main")
                    print(f"✅ Option radio sélectionnée : {span_text} source: reponse_executor.py")
                    # Attendre le changement visuel p-checked
                    _checked = False
                    try:
                        page.wait_for_function(
                            "(el) => el.classList.contains('p-checked')",
                            arg=label,
                            timeout=5_000,
                        )
                        _checked = True
                    except Exception:
                        if radio is not None:
                            try:
                                _checked = radio.is_checked()
                            except Exception:
                                pass
                    if not _checked:
                        print("⚠️ Radio non sélectionné après clic natif — retry hover+click")
                        try:
                            label.hover()
                            label.click()
                        except Exception:
                            pass
                    click_next_button(driver)
                    return True
        print("❌ Option radio non cochée.")
        return False

    except Exception as e:
        print("💥 Erreur d'exécution :", type(e).__name__, "-", e, "source: reponse_executor.py")
        return False



def _confirm_before_cta_click() -> None:
    if should_pause_before_cta():
        print("⏸️ LOCAL_CTA_REQUIRE_ENTER=1 — appuyez sur Entrée pour cliquer sur le CTA. source: reponse_executor.py")
        input()

def click_next_button(driver):
    if is_cta_intercept_only():
        log_info("response_executor", "🛑 CTA_INTERCEPT_ONLY=1 — clic CTA intercepté, pas de navigation.")
        return True

    page = driver
    CTA_SEL = 'button[data-test-id="ps-common-actions-button"]'
    _primary_exc = None
    try:
        # Attendre que le CTA soit visible (p-checked sur le label l'active)
        next_btn = page.wait_for_selector(CTA_SEL, state="visible", timeout=5_000)
        next_btn.evaluate("(el) => el.scrollIntoView({block: 'center'})")
        time.sleep(0.5)
        # Re-fetch après le scroll (DOM peut être re-rendu par async_radio)
        next_btn = page.query_selector(CTA_SEL)
        _confirm_before_cta_click()

        # Boucle de reprise bornée — TimeoutError Playwright possible si le bouton
        # n'est pas encore actionnable (scroll en cours, animation, etc.)
        _CTA_MAX_ATTEMPTS = 2
        for _attempt in range(_CTA_MAX_ATTEMPTS):
            if _attempt > 0:
                log_info("response_executor", f"[CTA_RETRY] tentative {_attempt + 1}/{_CTA_MAX_ATTEMPTS}")
                try:
                    next_btn = page.query_selector(CTA_SEL)
                    next_btn.evaluate("(el) => el.scrollIntoView({block: 'center'})")
                except Exception:
                    pass
            _diag_attach(driver, next_btn, "click_next_button[primary]")
            try:
                next_btn.click()
                _diag_read(driver, "click_next_button[primary]")
                print("➡️ Bouton (flèche ou navigation) cliqué via data-test-id. source: reponse_executor.py")
                try:
                    page.wait_for_load_state("load", timeout=5_000)
                except Exception:
                    pass
                return True
            except Exception as _cta_exc:
                _primary_exc = _cta_exc
                _is_last = _attempt >= _CTA_MAX_ATTEMPTS - 1
                if _is_last or type(_cta_exc).__name__ != "TimeoutError":
                    log_info("response_executor", f"[CTA_PRIMARY_FAILED] {type(_cta_exc).__name__}: {_cta_exc}")
                    break
                log_info("response_executor", f"[CTA_RETRY] tentative {_attempt + 1} {type(_cta_exc).__name__} — reprise")

    except Exception as _wait_exc:
        _primary_exc = _wait_exc
        log_info("response_executor", f"[CTA_PRIMARY_FAILED] (attente initiale) {type(_wait_exc).__name__}: {_wait_exc}")

    # Repli textuel
    if _primary_exc is not None:
        try:
            xpath = (
                "xpath=//button["
                "contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'suivant') or "
                "contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'continuer') or "
                "contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'next') or "
                "contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'continue')"
                "]"
            )
            next_btn = page.wait_for_selector(xpath, state="attached", timeout=5_000)
            next_btn.evaluate("(el) => el.scrollIntoView({block: 'center'})")
            time.sleep(2)
            _confirm_before_cta_click()

            # attendre que le bouton devienne enabled
            try:
                page.wait_for_function(
                    "(el) => !el.disabled && el.getAttribute('disabled') === null",
                    arg=next_btn,
                    timeout=6_000,
                )
            except Exception:
                try:
                    next_btn.evaluate(
                        "(el) => { el.removeAttribute('disabled'); el.classList.remove('disabled'); }"
                    )
                except Exception:
                    pass

            _diag_attach(driver, next_btn, "click_next_button[fallback]")
            next_btn.click()
            _diag_read(driver, "click_next_button[fallback]")
            print("➡️ Bouton cliqué via fallback textuel (case-insensitive + enabled). source: reponse_executor.py")
            try:
                page.wait_for_load_state("load", timeout=5_000)
            except Exception:
                pass
            return True

        except Exception as e:
            print("⏭️ Aucun bouton « Suivant » ou navigation détecté :", type(e).__name__, "-", e, " source: reponse_executor.py")
            return False


def select_checkbox_answers(driver, answers):
    """
    Coche une ou plusieurs cases à cocher correspondant aux réponses proposées par l'IA.
    Scan DOM une seule fois → dict {texte_normalisé: (label, checkbox)} → lookup O(1) par cible.
    """
    page = driver
    labels = page.query_selector_all('[data-test-id^="ps-question-input-multiple_choice-label"]')
    if not labels:
        return False

    # Extraction JS en un seul aller-retour : texte + état coché pour tous les labels
    js_result = page.evaluate("""(labels) => {
        var results = [];
        for (var i = 0; i < labels.length; i++) {
            var label = labels[i];
            var textElem = label.querySelector('[data-test-id*="multiple_choice-text"]');
            var cb = label.querySelector("input[type='checkbox']");
            results.push({
                text: textElem ? textElem.textContent.trim() : "",
                checked: cb ? cb.checked : false,
                dtid: label.getAttribute('data-test-id') || ""
            });
        }
        return results;
    }""", labels)

    # Construire le dict normalisé → index
    label_map = {}
    for i, info in enumerate(js_result):
        key = normalize(info["text"])
        if key:
            label_map[key] = (i, info["text"], info["checked"], info.get("dtid", ""))

    normalized_targets = [
        normalize(str(a)) for a in (answers if isinstance(answers, list) else [answers])
    ]

    # Garantit le focus OS sur l'onglet avant tout clic (évite le throttle Chromium).
    try:
        page.bring_to_front()
    except Exception:
        pass

    found = False
    for target in normalized_targets:
        if target not in label_map:
            print(f"⚠️ Cible non trouvée dans les labels : {target} source: reponse_executor.py")
            continue

        idx, label_text, already_checked, label_dtid = label_map[target]

        if already_checked:
            print(f"✅ Checkbox déjà cochée : {label_text}")
            found = True
            continue

        # Re-résolution fraîche : le framework re-rend toute la liste à chaque clic.
        label = page.query_selector(f'[data-test-id="{label_dtid}"]')
        if label is None:
            continue
        inner_cb = label.query_selector("input[type='checkbox']")
        # Cibler .p-checkbox-box (taille fixe) plutôt que le centre du label
        # pour éviter les échecs d'actionabilité sur labels multi-lignes.
        click_target = label.query_selector(".p-checkbox-box") or label
        # block:'center' éloigne l'élément du bouton CTA fixe en bas.
        # behavior:'instant' évite l'animation de scroll.
        label.evaluate("(el) => el.scrollIntoView({block:'center', behavior:'instant'})")
        _diag_sample_stability(driver, label, label_text)
        _diag_attach(driver, label, "select_checkbox_answers")
        _diag_pw_actionability(label, label_text)
        _CLICK_MAX_ATTEMPTS = 3
        _click_failed = False
        for _attempt in range(_CLICK_MAX_ATTEMPTS):
            if _attempt > 0:
                log_info("response_executor", f"[CHECKBOX_RETRY] label={label_text!r} tentative {_attempt + 1}/{_CLICK_MAX_ATTEMPTS}")
                try:
                    label = page.query_selector(f'[data-test-id="{label_dtid}"]')
                    if label is None:
                        _click_failed = True
                        break
                    inner_cb = label.query_selector("input[type='checkbox']")
                    click_target = label.query_selector(".p-checkbox-box") or label
                except Exception:
                    pass
                label.evaluate("(el) => el.scrollIntoView({block:'center', behavior:'instant'})")
                _diag_attach(driver, label, "select_checkbox_answers")
            try:
                click_target.click()
                break
            except Exception as _ck_exc:
                _is_last = _attempt >= _CLICK_MAX_ATTEMPTS - 1
                if _is_last:
                    _diag_checkbox_failure(driver, label, label_text, _ck_exc)
                    log_info("response_executor", f"[CHECKBOX_SKIP] label={label_text!r} ignoré après {_CLICK_MAX_ATTEMPTS} tentatives ({type(_ck_exc).__name__})")
                    _click_failed = True
                    break
                if type(_ck_exc).__name__ != "TimeoutError":
                    _diag_checkbox_failure(driver, label, label_text, _ck_exc)
                    raise
                log_info("response_executor", f"[CHECKBOX_RETRY] label={label_text!r} tentative {_attempt + 1} TimeoutError — reprise")
        if _click_failed:
            continue
        _diag_read(driver, "select_checkbox_answers")

        # Attendre le changement visuel p-checked (confirme la sélection effective)
        _checked_visually = False
        try:
            page.wait_for_function(
                "(el) => el.classList.contains('p-checked')",
                arg=label,
                timeout=5_000,
            )
            _checked_visually = True
        except Exception:
            if inner_cb is not None:
                try:
                    _checked_visually = inner_cb.is_checked()
                except Exception:
                    pass

        if not _checked_visually:
            label.hover()
            label.click()
            try:
                page.wait_for_function(
                    "(el) => el.classList.contains('p-checked')",
                    arg=label,
                    timeout=5_000,
                )
                _checked_visually = True
            except Exception:
                pass

        # Vérification de la propriété DOM réelle (.checked), indépendante du
        # rendu CSS (.p-checked). p-checked confirme que Vue a bien re-rendu le
        # label après le clic ; .checked confirme que l'input natif sous-jacent
        # reflète bien l'état coché. Les deux peuvent diverger si le state du
        # framework se désynchronise de l'attribut DOM natif — ce check ne fait
        # que loguer l'écart, il ne déclenche aucun nouveau clic (pas de
        # fallback ici : un mismatch silencieux est un signal de diagnostic,
        # pas une raison suffisante pour relancer une boucle de clic).
        _checked_dom_prop = None
        if inner_cb is not None:
            try:
                _checked_dom_prop = inner_cb.is_checked()
            except Exception:
                pass
        if _checked_dom_prop is False:
            log_info(
                "response_executor",
                f"[CHECKBOX_STATE_MISMATCH] label={label_text!r} "
                f"p_checked={_checked_visually} dom_checked=False",
            )

        if _checked_visually:
            print(f"✅ Checkbox cochée : {label_text} source: reponse_executor.py")
            found = True
        else:
            print(f"⚠️ Checkbox trouvée mais non cochée : {label_text} source: reponse_executor.py")

    return found