import os
import re
import time
import unicodedata
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains

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
        driver.execute_script(_JS_ATTACH_DIAG_LISTENER, element)
    except Exception as exc:
        log_debug("diag_istrusted", f"[DIAG_ISTRUSTED] attach failed tag={tag}: {exc}")

def _diag_read(driver, tag: str) -> None:
    """Lit et logue window.__diag_istrusted__ après le clic JS."""
    if not _DIAG_ISTRUSTED:
        return
    try:
        result = driver.execute_script("return window.__diag_istrusted__ || null;")
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
    # Vérification Python du handle avant d'invoquer le script JS.
    # tag_name appelle evaluate sur le handle Playwright — lève si le handle est disposé/stale.
    handle_ok = "unknown"
    try:
        tag = label.tag_name
        handle_ok = f"valid(tag={tag})"
    except Exception as h_exc:
        handle_ok = f"invalid({type(h_exc).__name__}: {h_exc})"
    try:
        state = driver.execute_script(_JS_DIAG_LABEL_STATE, label)
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
    # Call log Playwright complet depuis l'exception (détail de la boucle d'actionabilité)
    pw_log = str(exc).replace("\n", " | ")
    log_info("diag_checkbox_click", f"[DIAG_CHECKBOX_CLICK] pw_calllog={pw_log!r}")

def _diag_pw_actionability(label, label_text: str) -> None:
    """Vérifie les critères d'actionabilité Playwright natifs avant le clic — DIAG_CHECKBOX_CLICK=1.
    Accède à label._h (PlaywrightElementShim → ElementHandle) pour reproduire exactement
    les mêmes vérifications que click() effectue en interne."""
    if not _DIAG_CHECKBOX_CLICK:
        return
    try:
        h = label._h  # ElementHandle Playwright sous-jacent (shim interne)
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

def _diag_sample_stability(driver, label, label_text: str) -> None:
    """Échantillonne rect + scroll ancêtre + animations CSS du label N fois — DIAG_STABILITY=1."""
    if not _DIAG_STABILITY:
        return
    _N = 15
    _INTERVAL = 0.2  # 200 ms → fenêtre de 3 s
    # Vérification one-shot des animations actives (avant la boucle)
    anim_info = []
    try:
        anim_info = driver.execute_script(_JS_CHECK_ANIMATIONS, label) or []
    except Exception as ae:
        anim_info = [f"ERR_ANIM:{type(ae).__name__}"]
    samples = []
    for _ in range(_N):
        try:
            r = driver.execute_script(_JS_SAMPLE_RECT_EXT, label)
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
    try:
        if el.tag_name.lower() == "label":
            # tente de trouver l'input à l'intérieur
            try:
                cb = el.find_element(By.CSS_SELECTOR, "input[type='checkbox']")
                return cb.is_selected()
            except Exception:
                pass
        if (
            el.tag_name.lower() == "input"
            and (el.get_attribute("type") or "").lower() == "checkbox"
        ):
            return el.is_selected()
    except Exception:
        pass
    return False


def _execute_async_radio(driver, answer_text) -> bool:
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
    norm_answer = normalize(answer_text)

    # 1. Localiser le champ de recherche texte
    search_input = None
    for sel in [
        "[data-test-id='ps-async-answer-input-input']",
        "[data-test-id='ps-async-answer-input'] input",
        ".async-answer input[type='text']",
    ]:
        try:
            search_input = driver.find_element(By.CSS_SELECTOR, sel)
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
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", search_input)
            search_input.clear()
            search_input.send_keys(str(text))
            driver.execute_script(
                "arguments[0].dispatchEvent(new Event('input', {bubbles:true}));"
                "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
                search_input,
            )
            return True
        except Exception as e:
            print(f"❌ [async_radio] Échec saisie champ recherche : {e}")
            return False

    def _collect_radio_labels() -> list:
        """Retourne les labels radio visibles après filtrage (attente 1.5 s incluse)."""
        time.sleep(1.5)
        try:
            return driver.find_elements(
                By.CSS_SELECTOR,
                'label[data-test-id^="ps-question-input-single_choice-label"]',
            )
        except Exception:
            return []

    def _click_radio_label(label) -> bool:
        """Clique sur un label radio, avec fallback ActionChains. Retourne True si sélectionné.

        Note : après le clic JS, le widget async_radio re-rend le DOM (la liste filtrée
        disparaît et le champ de recherche est mis à jour). Les références aux éléments
        radio/label deviennent stale immédiatement. On intercepte StaleElementReferenceException
        sur is_selected() : si l'élément est stale, c'est que le clic a bien déclenché
        le re-render — on considère le clic réussi et on passe au CTA.
        """
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", label)
            time.sleep(0.3)
            radio = label.find_element(By.CSS_SELECTOR, "input[type='radio']")
            _diag_attach(driver, radio, "_click_radio_label")
            driver.execute_script("arguments[0].click();", radio)
            _diag_read(driver, "_click_radio_label")
            time.sleep(0.5)
            # Vérification post-clic : si l'élément est stale (DOM re-rendu après clic),
            # on considère le clic comme réussi — le re-render confirme l'interaction.
            try:
                if not radio.is_selected():
                    ActionChains(driver).move_to_element(label).click().perform()
                    time.sleep(0.5)
            except Exception:
                # StaleElementReferenceException attendu : le DOM a été re-rendu, clic OK.
                pass
            return True
        except Exception as e:
            print(f"❌ [async_radio] Échec clic radio : {e}")
            return False

    def _get_label_text(label) -> str:
        """Extrait le texte visible d'un label radio (textContent prioritaire)."""
        try:
            spans = label.find_elements(By.CSS_SELECTOR, 'span[class*="p-radio-text"]')
            for span in spans:
                # get_attribute("textContent") nécessaire : certains spans ont display:none sur .text
                t = span.get_attribute("textContent") or span.text or ""
                if t.strip():
                    return t.strip()
        except Exception:
            pass
        try:
            return label.text.strip()
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
    # Délégation immédiate au handler async_radio si itype détecté
    if input_type == "async_radio":
        return _execute_async_radio(driver, answer_text)

    # Pas de choix → souvent page de blocage ou de consentement non mappée
    if not answer_text:
        print("⏭️ Aucun choix détecté — pas d'action sur cette page. source: reponse_executor.py")
        return False

    print(f"🌟 Tentative de sélection : {answer_text} source: reponse_executor.py")
    norm_answer = normalize(answer_text)
    checkbox_answers = [
        chunk.strip() for chunk in str(answer_text).split("|") if chunk.strip()
    ]
    success = False

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
        text_input = None
        try:
            text_input = driver.find_element(
                By.CSS_SELECTOR, 'input[data-test-id*="input_text-input"]'
            )
        except Exception:
            pass

        if text_input is not None:
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", text_input
            )
            text_input.clear()
            text_input.send_keys(str(answer_text))
            driver.execute_script(
                "arguments[0].dispatchEvent(new Event('input', {bubbles:true}));"
                "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
                text_input,
            )
            time.sleep(1)
            log_info("response_executor", f"✅ Champ texte rempli : {answer_text}")
            click_next_button(driver)
            return True

        # 2.6) Champ numérique entier (input_int)
        int_input = None
        try:
            int_input = driver.find_element(
                By.CSS_SELECTOR, 'input[data-test-id*="input_int-input"]'
            )
        except Exception:
            pass

        if int_input is not None:
            raw_digits = re.sub(r"[^\d]", "", str(answer_text))
            if not raw_digits:
                raw_digits = str(answer_text).strip()
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", int_input
            )
            int_input.clear()
            int_input.send_keys(raw_digits)
            driver.execute_script(
                "arguments[0].dispatchEvent(new Event('input', {bubbles:true}));"
                "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
                int_input,
            )
            time.sleep(1)
            log_info("response_executor", f"✅ Champ numérique rempli : {raw_digits}")
            click_next_button(driver)
            return True

        # 3) Si aucune checkbox trouvée → tentative radio
        labels = driver.find_elements(
            By.CSS_SELECTOR,
            'label[data-test-id^="ps-question-input-single_choice-label"]',
        )
        for label in labels:
            spans = label.find_elements(
                By.CSS_SELECTOR, 'span[class*="p-radio-text"]'
            )
            for span in spans:
                if norm_answer in normalize(span.text) or normalize(span.text) in norm_answer:
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center'});", label
                    )
                    # L'input radio peut être CSS-masqué (widget personnalisé) et donc
                    # non actionnable pour Playwright. On clique le label visible qui
                    # déclenche nativement la sélection du radio associé (isTrusted=true).
                    # radio est récupéré uniquement pour la vérification post-clic.
                    radio = None
                    try:
                        radio = label.find_element(By.CSS_SELECTOR, "input[type='radio']")
                    except Exception:
                        pass
                    time.sleep(0.5)
                    _diag_attach(driver, label, "execute_response_radio_main")
                    label.click()
                    _diag_read(driver, "execute_response_radio_main")
                    print(
                        f"✅ Option radio sélectionnée : {span.text} source: reponse_executor.py"
                    )
                    # 🔍 Attendre le changement visuel (p-checked) avant de déclencher le CTA.
                    # Avec proxy lent, la confirmation backend arrive avant le re-render visuel ;
                    # le CTA n'apparaît qu'une fois p-checked présent sur le label.
                    try:
                        WebDriverWait(driver, 5).until(
                            lambda d: "p-checked" in (label.get_attribute("class") or "")
                            or (radio is not None and radio.is_selected())
                        )
                    except Exception:
                        try:
                            if radio is None or not radio.is_selected():
                                print("⚠️ Radio non sélectionné après clic natif — retry ActionChains")
                                ActionChains(driver).move_to_element(label).click().perform()
                        except Exception:
                            pass
                    click_next_button(driver)
                    return True  # ✅ succès
        print("❌ Option radio non cochée.")
        return False

    except Exception as e:
        print(
            "💥 Erreur d’exécution :",
            type(e).__name__,
            "-",
            e,
            "source: reponse_executor.py",
        )
        return False



def _confirm_before_cta_click() -> None:
    if should_pause_before_cta():
        print("⏸️ LOCAL_CTA_REQUIRE_ENTER=1 — appuyez sur Entrée pour cliquer sur le CTA. source: reponse_executor.py")
        input()

def click_next_button(driver):
    if is_cta_intercept_only():
        log_info("response_executor", "🛑 CTA_INTERCEPT_ONLY=1 — clic CTA intercepté, pas de navigation.")
        return True

    wait = WebDriverWait(driver, 10)
    CTA_SEL = 'button[data-test-id="ps-common-actions-button"]'
    try:
        # Attendre que le CTA soit présent ET visible dans le DOM.
        # Nécessaire car le bouton n'apparaît qu'après qu'au moins une option soit
        # visuellement cochée (classe p-checked sur le label). En cas de proxy lent,
        # le changement visuel peut prendre plusieurs secondes après la confirmation
        # backend du clic — on attend donc jusqu'à 10 s que le bouton soit cliquable.
        next_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, CTA_SEL))
        )
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'});", next_btn
        )
        time.sleep(0.5)
        # Re-fetch après le scroll : le DOM peut avoir été re-rendu (ex: async_radio)
        # et la référence initiale serait stale.
        next_btn = driver.find_element(By.CSS_SELECTOR, CTA_SEL)
        _confirm_before_cta_click()
        _diag_attach(driver, next_btn, "click_next_button[primary]")
        next_btn.click()
        _diag_read(driver, "click_next_button[primary]")
        print(
            "➡️ Bouton (flèche ou navigation) cliqué via data-test-id. source: reponse_executor.py"
        )
        from Management.redirect_watcher import wait_for_page_load
        wait_for_page_load(driver, timeout=15)
        return True

    except:
        try:
            xpath = (
                "//button["
                "contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'suivant') or "
                "contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'continuer') or "
                "contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'next') or "
                "contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'continue')"
                "]"
            )

            next_btn = wait.until(EC.presence_of_element_located((By.XPATH, xpath)))
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_btn)
            time.sleep(2)
            _confirm_before_cta_click()

            # attendre que le bouton devienne réellement cliquable (disabled retiré)
            try:
                WebDriverWait(driver, 6).until(
                    lambda d: next_btn.is_enabled() and (next_btn.get_attribute("disabled") is None)
                )
            except Exception:
                # dernier recours: si l’UI ne réagit pas malgré input/change, on force le enable
                try:
                    driver.execute_script(
                        "arguments[0].removeAttribute('disabled'); arguments[0].classList.remove('disabled');",
                        next_btn,
                    )
                except Exception:
                    pass

            _diag_attach(driver, next_btn, "click_next_button[fallback]")
            next_btn.click()
            _diag_read(driver, "click_next_button[fallback]")
            print("➡️ Bouton cliqué via fallback textuel (case-insensitive + enabled). source: reponse_executor.py")
            from Management.redirect_watcher import wait_for_page_load
            wait_for_page_load(driver, timeout=15)
            return True

        except Exception as e:
            print(
                "⏭️ Aucun bouton « Suivant » ou navigation détecté :",
                type(e).__name__,
                "-",
                e,
                " source: reponse_executor.py",
            )
            return False


def select_checkbox_answers(driver, answers):
    """
    Coche une ou plusieurs cases à cocher correspondant aux réponses proposées par l'IA.
    Scan DOM une seule fois → dict {texte_normalisé: (label, checkbox)} → lookup O(1) par cible.
    """
    labels = driver.find_elements(
        By.CSS_SELECTOR, '[data-test-id^="ps-question-input-multiple_choice-label"]'
    )
    if not labels:
        return False

    # Extraction JS en un seul aller-retour : texte + état coché pour tous les labels
    js_result = driver.execute_script("""
        var results = [];
        var labels = arguments[0];
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
    """, labels)

    # Construire le dict normalisé → index
    label_map = {}
    for i, info in enumerate(js_result):
        key = normalize(info["text"])
        if key:
            label_map[key] = (i, info["text"], info["checked"], info.get("dtid", ""))

    normalized_targets = [
        normalize(str(a)) for a in (answers if isinstance(answers, list) else [answers])
    ]

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

        # Re-résolution fraîche : le framework re-rend toute la liste à chaque clic
        # (tous les value= des inputs sont mis à jour), ce qui invalide les références
        # capturées avant la boucle. On re-fetch par data-test-id UUID stable.
        label = driver.find_element(
            By.CSS_SELECTOR, f'[data-test-id="{label_dtid}"]'
        )
        inner_cb = label.find_element(By.CSS_SELECTOR, "input[type='checkbox']")
        # block:'center' centre l'élément dans la zone scrollable, éloigné du bord inférieur
        # où le bouton CTA fixe (ps-common-actions-button) intercepte les clics si l'élément
        # s'y retrouve avec block:'nearest'. behavior:'instant' évite l'animation de scroll.
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center', behavior:'instant'});", label
        )
        _diag_sample_stability(driver, label, label_text)
        _diag_attach(driver, label, "select_checkbox_answers")
        _diag_pw_actionability(label, label_text)
        try:
            label.click()
        except Exception as _ck_exc:
            _diag_checkbox_failure(driver, label, label_text, _ck_exc)
            raise
        _diag_read(driver, "select_checkbox_answers")

        # Attendre le changement visuel (classe p-checked sur le label) plutôt que
        # de se fier uniquement à is_selected() dont la confirmation backend peut
        # arriver avant que le rendu visuel ne soit effectif (latence proxy).
        # Le CTA n'apparaît que lorsque p-checked est présent dans le DOM.
        _checked_visually = False
        try:
            WebDriverWait(driver, 5).until(
                lambda d: "p-checked" in (label.get_attribute("class") or "")
                or inner_cb.is_selected()
            )
            _checked_visually = True
        except Exception:
            pass

        if not _checked_visually:
            ActionChains(driver).move_to_element(label).click().perform()
            try:
                WebDriverWait(driver, 5).until(
                    lambda d: "p-checked" in (label.get_attribute("class") or "")
                    or inner_cb.is_selected()
                )
                _checked_visually = True
            except Exception:
                pass

        if _checked_visually:
            print(f"✅ Checkbox cochée : {label_text} source: reponse_executor.py")
            found = True
        else:
            print(f"⚠️ Checkbox trouvée mais non cochée : {label_text} source: reponse_executor.py")

    return found