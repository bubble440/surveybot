from __future__ import annotations
import re, unicodedata, os, time, zlib
from selenium.webdriver.common.by import By
import Survey.input_handler
from Survey.dom_registry import get_target
from typing import Optional
from selenium.webdriver.common.action_chains import ActionChains
from Survey.log_utils import is_debug, log_debug, log_info

def _short_exc(e: Exception) -> str:
    """Rend les exceptions Selenium lisibles (sans Stacktrace bruyant)."""
    try:
        msg = getattr(e, "msg", None) or str(e) or ""
    except Exception:
        msg = ""
    # Selenium ajoute souvent un bloc 'Stacktrace:' énorme dans le message
    if "Stacktrace:" in msg:
        msg = msg.split("Stacktrace:")[0]
    msg = re.sub(r"\s+", " ", msg).strip()
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__

def solve_decipher_cardrating_rows(driver, preferred_label: Optional[str] = None, max_widgets: int = 3) -> bool:
    """
    Résout les questions Decipher 'sq-cardrating' groupées par rows.
    Stratégie (DOM-only, prédictible):
    - détecter les widgets .sq-cardrating-widget[data-uid]
    - choisir une colonne (option) (préférée si fournie, sinon un choix "safe")
    - cocher (via JS events) tous les radios cachés ans<uid>.<col>.<row>
    - vérifier que chaque groupe de name (ans<uid>.*) a un checked
    Retourne True si au moins un widget multi-row a été complété.
    """
    def _norm(s: str) -> str:
        return " ".join((s or "").split()).strip().lower()

    def _dispatch_check(el) -> None:
        # radios souvent non-interactables (hidden) ; JS events
        driver.execute_script(
            """
            const el = arguments[0];
            if (!el) return;
            try { el.checked = true; } catch(e) {}
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
            """,
            el,
        )

    def _all_rows_answered(widget_el) -> bool:
        return bool(driver.execute_script(
            """
            const widget = arguments[0];
            if (!widget) return false;
            const uid = widget.getAttribute('data-uid');
            if (!uid) return false;
            const root = widget.closest('.question') || document;
            const inputs = Array.from(root.querySelectorAll("input[type='radio'][name^='ans" + uid + ".']"));
            const names = [...new Set(inputs.map(i => i.name))];
            if (!names.length) return false;
            return names.every(n => !!root.querySelector("input[type='radio'][name='" + n + "']:checked"));
            """,
            widget_el
        ))

    def _row_group_count(widget_el) -> int:
        return int(driver.execute_script(
            """
            const widget = arguments[0];
            if (!widget) return 0;
            const uid = widget.getAttribute('data-uid');
            if (!uid) return 0;
            const root = widget.closest('.question') || document;
            const inputs = Array.from(root.querySelectorAll("input[type='radio'][name^='ans" + uid + ".']"));
            const names = [...new Set(inputs.map(i => i.name))];
            return names.length;
            """,
            widget_el
        ) or 0)

    def _pick_option_button(widget_el):
        btns = widget_el.find_elements(By.CSS_SELECTOR, ".sq-cardrating-buttons .sq-cardrating-button[data-clickable='true']")
        if not btns:
            return None

        # map text -> button
        wanted = _norm(preferred_label or "")
        if wanted:
            for b in btns:
                try:
                    t = b.text or ""
                    if not t:
                        t = b.find_element(By.CSS_SELECTOR, ".sq-cardrating-content").text
                    if _norm(t) == wanted:
                        return b
                except Exception:
                    continue

        # fallback "safe" (évite 'Jamais')
        safe_order = ["il y a quelques jours", "il y a 1-3 mois", "il y a 2-4 semaines", "il y a 1 semaine", "hier", "aujourd'hui"]
        # index par texte
        norm_to_btn = {}
        for b in btns:
            try:
                t = b.text or ""
                if not t:
                    t = b.find_element(By.CSS_SELECTOR, ".sq-cardrating-content").text
                nt = _norm(t)
                if nt and "jamais" not in nt:
                    norm_to_btn[nt] = b
            except Exception:
                continue

        for key in safe_order:
            if key in norm_to_btn:
                return norm_to_btn[key]

        # dernier recours: premier bouton non-'Jamais'
        for b in btns:
            try:
                t = b.text or ""
                if not t:
                    t = b.find_element(By.CSS_SELECTOR, ".sq-cardrating-content").text
                if "jamais" not in _norm(t):
                    return b
            except Exception:
                continue
        return None

    widgets = driver.find_elements(By.CSS_SELECTOR, ".sq-cardrating-widget[data-uid]")
    if not widgets:
        return False

    completed_any = False
    for widget in widgets[:max_widgets]:
        try:
            # on ne s'occupe que des multi-rows
            if _row_group_count(widget) <= 1:
                continue

            if _all_rows_answered(widget):
                completed_any = True
                continue

            btn = _pick_option_button(widget)
            if not btn:
                continue

            uid = (widget.get_attribute("data-uid") or "").strip()
            col = (btn.get_attribute("data-index") or "").strip()
            if not uid or not col.isdigit():
                continue

            # cocher toutes les rows pour cette colonne (ans<uid>.<col>.<row>)
            # Les inputs sont dans le meme bloc question (souvent dans une QA-view cachée)
            qroot = widget.find_element(By.XPATH, "ancestor::*[contains(@class,'question')][1]")
            inputs = qroot.find_elements(By.CSS_SELECTOR, f"input[type='radio'][id^='ans{uid}.{col}.']")
            if not inputs:
                # fallback global (au cas oÃƒÂ¹ la structure varie)
                inputs = driver.find_elements(By.CSS_SELECTOR, f"input[type='radio'][id^='ans{uid}.{col}.']")

            if not inputs:
                continue

            for inp in inputs:
                _dispatch_check(inp)
                time.sleep(0.03)

            # check final
            if _all_rows_answered(widget):
                completed_any = True
        except Exception:
            continue

    return completed_any

def solve_focusvision_cardsort(driver, preferred_label: Optional[str] = None, max_cards: int = 20) -> bool:
    """
    FocusVision/Decipher cardsort (DOM-only, prédictible, budget borné):
    - détecte .sq-cardsort
    - clique une bucket "safe" pour chaque carte visible, jusqu'ÃƒÂ  completion ou max_cards
    - clique "Continuer" si visible
    Retourne True si au moins 1 clic a été effectué.
    """
    def _norm(s: str) -> str:
        if not s:
            return ""
        s = unicodedata.normalize("NFKC", s).replace("\xa0", " ").strip()
        s = re.sub(r"\s+", " ", s)
        return s

    def _norm_lc(s: str) -> str:
        return _norm(s).lower()

    def _pick_cardsort_root():
        try:
            css = driver.find_elements(By.CSS_SELECTOR, ".sq-cardsort")
            return css[0] if css else None
        except Exception:
            return None

    def _active_card(cs):
        try:
            cards = cs.find_elements(By.CSS_SELECTOR, ".sq-cardsort-cards li")
        except Exception:
            cards = []
        for c in cards:
            try:
                cl = (c.get_attribute("class") or "").lower()
                if "sq-cardsort-completion" in cl:
                    continue
                style = (c.get_attribute("style") or "").lower()
                if "display: none" in style:
                    continue
                # fallback selenium visibility
                try:
                    if c.is_displayed():
                        return c
                except Exception:
                    return c
            except Exception:
                continue
        return None

    def _completion_visible(cs) -> bool:
        try:
            el = cs.find_elements(By.CSS_SELECTOR, ".sq-cardsort-completion")
            if not el:
                return False
            try:
                return bool(el[0].is_displayed())
            except Exception:
                return True
        except Exception:
            return False

    def _read_bucket_label(b) -> str:
        try:
            ps = b.find_elements(By.CSS_SELECTOR, ".sq-cardsort-bucket-legend")
            if ps:
                return _norm(ps[0].text or ps[0].get_attribute("innerText") or "")
        except Exception:
            pass
        try:
            raw = _norm(b.text or b.get_attribute("innerText") or "")
            return _norm((raw.splitlines()[0] if raw else ""))
        except Exception:
            return ""

    def _get_question_text(cs, card) -> str:
        parts = []
        # Question globale
        try:
            qels = driver.find_elements(By.CSS_SELECTOR, ".question-text")
            if qels:
                parts.append(_norm(qels[0].text or qels[0].get_attribute("innerText") or ""))
        except Exception:
            pass

        # Texte carte active
        try:
            parts.append(_norm(card.text or card.get_attribute("innerText") or ""))
        except Exception:
            pass

        return " Ã¢â‚¬â€ ".join([p for p in parts if p])

    def _pick_bucket(cs, question_text: str):
        try:
            buckets = cs.find_elements(By.CSS_SELECTOR, "li.sq-cardsort-bucket")
        except Exception:
            buckets = []

        label_to_el = {}
        for b in buckets:
            try:
                lbl = _read_bucket_label(b)
                if not lbl:
                    continue
                label_to_el[_norm_lc(lbl)] = b
            except Exception:
                continue

        if not label_to_el:
            return None

        qt = _norm_lc(question_text)

        # 1) Attention check explicite : si la consigne contient une option textuelle
        triggers = ["veuillez", "selectionnez", "sélectionnez", "choisissez", "pour vérifier", "attention"]
        if any(t in qt for t in triggers):
            for opt_lc, el in label_to_el.items():
                if opt_lc and opt_lc in qt:
                    return el

        # 2) Choix safe mais variable (déterministe)
        safe_order = [
            "il y a 2 ou 3 jours",
            "il y a 4 ÃƒÂ  7 jours",
            "il y a 1 ÃƒÂ  2 semaines",
            "il y a 2 ÃƒÂ  3 semaines",
            "il y a 3 ÃƒÂ  4 semaines",
            "il y a plus de 4 semaines",
            "hier",
            "aujourd'hui",
        ]
        available = [k for k in safe_order if k in label_to_el]

        # fallback : tout sauf "jamais"
        if not available:
            available = [k for k in label_to_el.keys() if "jamais" not in k and "ne se souvient" not in k]
        if not available:
            available = list(label_to_el.keys())

        # Seed stable par bot (optionnel) + question_text
        seed = (_norm_lc(os.getenv("BOT_ID", "")) or _norm_lc(os.getenv("ACCOUNT_ID", "")) or "")
        h = zlib.crc32(f"{seed}|{qt}".encode("utf-8")) & 0xffffffff
        chosen = available[h % len(available)]
        return label_to_el[chosen]

    def _click(el) -> bool:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            time.sleep(0.05)
        except Exception:
            pass
        try:
            el.click()
            return True
        except Exception:
            try:
                ActionChains(driver).move_to_element(el).click().perform()
                return True
            except Exception:
                try:
                    driver.execute_script("arguments[0].click();", el)
                    return True
                except Exception:
                    return False

    cs = _pick_cardsort_root()
    if not cs:
        return False

    did = False

    for _ in range(max_cards):
        if _completion_visible(cs):
            break

        card = _active_card(cs)
        if not card:
            break

        before_idx = ""
        try:
            before_idx = (card.get_attribute("index") or "").strip()
        except Exception:
            before_idx = ""

        qtxt = _get_question_text(cs, card)
        bucket = _pick_bucket(cs, qtxt)
        if not bucket:
            break

        if not _click(bucket):
            break
        did = True

        # petit wait pour l'auto-advance (page JS)
        time.sleep(0.12)

        # si la carte n'a pas changé, on retente 1 fois en cliquant l'item interne
        card2 = _active_card(cs)
        after_idx = ""
        try:
            after_idx = (card2.get_attribute("index") or "").strip() if card2 else ""
        except Exception:
            after_idx = ""

        if before_idx and after_idx and before_idx == after_idx:
            try:
                inner = bucket.find_element(By.CSS_SELECTOR, ".sq-cardsort-bucket-item")
                _click(inner)
                time.sleep(0.12)
            except Exception:
                pass

    # CTA Continue (si visible)
    try:
        btn = driver.find_elements(By.CSS_SELECTOR, "#btn_continue, input#btn_continue, input.button.continue")
        if btn:
            try:
                if btn[0].is_displayed():
                    _click(btn[0])
            except Exception:
                _click(btn[0])
    except Exception:
        pass

    return did

def _norm(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s).replace("\xa0", " ").strip()
    return re.sub(r"\s+", " ", s)

def _norm_lc(s: str) -> str:
    return _norm(s).lower()

def _fold_norm_lc(s: str) -> str:
    """
    Normalisation robuste pour comparer des libellés (options):
    - NFKD + suppression des diacritiques (ÃƒÅ½le -> Ile)
    - lower + collapse spaces
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("\xa0", " ").strip()
    s = re.sub(r"\s+", " ", s)
    return s.lower()

def _click_xpath(driver, xpath: str) -> bool:
    if not xpath:
        return False
    try:
        el = driver.find_element(By.XPATH, xpath)
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        el.click()
        return True
    except Exception:
        return False

def _set_text_xpath(driver, xpath: str, text: str) -> bool:
    if not xpath:
        return False
    try:
        el = driver.find_element(By.XPATH, xpath)
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        el.click()
        try:
            el.clear()
        except Exception:
            pass
        el.send_keys(text)
        return True
    except Exception:
        return False

def _xpath_literal(s: str) -> str:
    """
    Construit un literal XPath safe, meme si la chaÃƒÂ®ne contient des quotes.
    """
    s = s or ""
    if "'" not in s:
        return f"'{s}'"
    if '"' not in s:
        return f'"{s}"'
    parts = s.split("'")
    out = []
    for i, p in enumerate(parts):
        if p:
            out.append(f"'{p}'")
        if i != len(parts) - 1:
            out.append("\"'\"")
    return "concat(" + ", ".join(out) + ")"



def _parse_matrix_value_parts(value: str) -> tuple[str, str]:
    raw = (value or "").strip()
    if "||" not in raw:
        return "", raw
    left, right = raw.split("||", 1)
    return (left or "").strip(), (right or "").strip()


def _try_gridclick_matrix_set(driver, row_label: str, col_label: str) -> bool:
    row_label = (row_label or "").strip()
    col_label = (col_label or "").strip()
    if not row_label or not col_label:
        return False

    try:
        out = driver.execute_script(
            """
            const norm = (s) => String(s || '')
              .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
              .replace(/\s+/g, ' ').trim().toLowerCase();

            const rowNeedle = norm(arguments[0]);
            const colNeedle = norm(arguments[1]);
            const qDefs = (window.lukanka && window.lukanka.qDefs) || {};
            const qids = Object.keys(qDefs || {});
            if (!qids.length) return {ok:false, reason:'no_qdefs'};

            const matchLabel = (label, needle) => {
              const n = norm(label);
              return !!n && (n === needle || n.includes(needle) || needle.includes(n));
            };

            for (const qid of qids) {
              const def = qDefs[qid] || {};
              const rows = Array.isArray(def.rows) ? def.rows : [];
              const cols = Array.isArray(def.cols) ? def.cols : [];
              const uid = def.uid;
              if (!uid || !rows.length || !cols.length) continue;

              let row = null;
              for (const r of rows) {
                if (matchLabel(r.text, rowNeedle)) { row = r; break; }
              }
              if (!row) continue;

              let col = null;
              for (const c of cols) {
                if (matchLabel(c.text, colNeedle)) { col = c; break; }
              }
              if (!col) continue;

              const inputId = `ans${uid}.${col.index}.${row.index}`;
              const input = document.getElementById(inputId);
              if (!input) return {ok:false, reason:'input_not_found', inputId};

              input.checked = true;
              input.dispatchEvent(new Event('input', {bubbles:true}));
              input.dispatchEvent(new Event('change', {bubbles:true}));
              input.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true}));

              return {
                ok: !!input.checked,
                inputId,
                qid,
                row: row.text || '',
                col: col.text || ''
              };
            }

            return {ok:false, reason:'row_or_col_not_found'};
            """,
            row_label,
            col_label,
        )
    except Exception:
        return False

    if isinstance(out, dict) and out.get("ok"):
        log_info("[GRIDCLICK_MATRIX]", f"input_id={out.get('inputId')!r} checked=true")
        return True

    return False

def _apply_by_target_id(driver, target_id: str, itype: str, value: str) -> bool:
    """
    Applique l'action directement via DOM_REGISTRY (target_id -> xpath).
    Returns True si une action est exécutée.

    Support iframe: si le payload du registry contient frame_chain, on se positionne
    dans ce contexte le temps d'appliquer l'action.
    """
    try:
        payload = get_target(target_id)
        if not payload:
            return False

        # (Optionnel) exécution dans un iframe spécifique
        frame_chain = payload.get("frame_chain") or []
        
        # NEW: si le registry dit "pas d'iframe", on s'assure de revenir au default_content
        if not frame_chain:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass

        try:
            from Survey.frame_utils import switch_to_frame_chain  # type: ignore
        except Exception:
            switch_to_frame_chain = None  # type: ignore

        def _apply_in_current_context() -> bool:
            kind = payload.get("kind")
            reg_itype = (payload.get("itype") or "").lower()
            resolved_itype = (itype or reg_itype).lower().strip()

            debug_target = is_debug()

            def _find_best_visible(xpath: str):
                try:
                    cands = driver.find_elements(By.XPATH, xpath)
                except Exception:
                    cands = []

                if not cands:
                    return None

                def _rect_ok(el) -> bool:
                    try:
                        r = el.rect or {}
                        return (r.get("width", 0) or 0) > 2 and (r.get("height", 0) or 0) > 2
                    except Exception:
                        return False

                def _click_priority(el) -> int:
                    """Priorise les nœuds réellement cliquables pour radios/checkbox matrix."""
                    try:
                        tag = (el.tag_name or "").lower()
                    except Exception:
                        tag = ""
                    try:
                        cls = ((el.get_attribute("class") or "").lower())
                    except Exception:
                        cls = ""

                    score = 0
                    if tag in ("label", "input", "button"):
                        score += 100
                    if "clickablecell" in cls or "cell-sub-wrapper" in cls:
                        score += 80
                    if tag == "td" and "clickablecell" not in cls:
                        score -= 40
                    return score

                def _pick_best(scored):
                    if not scored:
                        return None
                    # max(..., key=...) garde le 1er élément en cas d'égalité:
                    # on conserve l'ordre DOM comme tie-breaker stable.
                    return max(scored, key=lambda item: item[0])[1]

                # 1) affiché + taille > 2px (évite label 0x0)
                visible_rect = []
                for c in cands:
                    try:
                        if c.is_displayed() and _rect_ok(c):
                            visible_rect.append((_click_priority(c), c))
                    except Exception:
                        continue
                best = _pick_best(visible_rect)
                if best:
                    return best

                # 2) affiché
                visible_any = []
                for c in cands:
                    try:
                        if c.is_displayed():
                            visible_any.append((_click_priority(c), c))
                    except Exception:
                        continue
                best = _pick_best(visible_any)
                if best:
                    return best

                # 3) fallback
                return _pick_best([(_click_priority(c), c) for c in cands])

            def _wait_checked(input_id: str | None, input_name: str | None, timeout_s: float = 1.2) -> bool:
                import time
                end = time.time() + timeout_s

                while time.time() < end:
                    try:
                        if input_id:
                            ok = driver.execute_script(
                                "var e=document.getElementById(arguments[0]); return !!(e && e.checked);",
                                input_id,
                            )
                            if ok:
                                return True

                        if input_name:
                            ok = driver.execute_script(
                                "return !!document.querySelector("
                                "  \"input[type='radio'][name=\\\"\"+arguments[0]+\"\\\"]:checked, \" +"
                                "  \"input[type='checkbox'][name=\\\"\"+arguments[0]+\"\\\"]:checked\""
                                ");",
                                input_name,
                            )
                            if ok:
                                return True
                    except Exception:
                        pass

                    time.sleep(0.05)

                return False

            v_norm = _norm_lc(value)
            v_fold = _fold_norm_lc(value)

            if payload.get("confirmit_slider_grid") and resolved_itype == "radio":
                row_id = (payload.get("slider_grid_row_id") or "").strip()
                scale_labels = [str(x or "").strip() for x in (payload.get("slider_grid_scale_labels") or []) if str(x or "").strip()]
                code_to_index = {
                    (str(k or "").strip().lower()): int(v)
                    for k, v in (payload.get("slider_grid_code_to_index") or {}).items()
                    if str(k or "").strip()
                }

                selected_index: int | None = None

                if not selected_index and v_norm:
                    try:
                        digits = re.sub(r"\D+", "", v_norm)
                    except Exception:
                        digits = ""
                    if digits:
                        if digits in code_to_index:
                            selected_index = code_to_index.get(digits)
                        else:
                            try:
                                maybe_idx = int(digits)
                                if 1 <= maybe_idx <= max(1, len(scale_labels)):
                                    selected_index = maybe_idx
                            except Exception:
                                pass

                if selected_index is None and scale_labels:
                    for idx, opt in enumerate(scale_labels, start=1):
                        o_norm = _norm_lc(opt)
                        o_fold = _fold_norm_lc(opt)
                        if not o_norm:
                            continue
                        if v_norm and (v_norm == o_norm or v_norm in o_norm or o_norm in v_norm):
                            selected_index = idx
                            break
                        if v_fold and (v_fold == o_fold or v_fold in o_fold or o_fold in v_fold):
                            selected_index = idx
                            break

                if not row_id:
                    log_debug("[TARGET_DEBUG]", f"slider-grid row skipped row_id=<missing> value='{value}' reason='missing_row_id'")
                    return False
                if selected_index is None:
                    log_debug("[TARGET_DEBUG]", f"slider-grid row skipped row_id={row_id} value='{value}' reason='unmapped_value'")
                    return False

                try:
                    js_result = driver.execute_script(
                        """
                        // confirmit_slider_grid_apply_v1
                        const rowId = arguments[0];
                        const selectedIndex = Number(arguments[1]);
                        const row = document.getElementById(rowId);
                        if (!row) return {ok:false, reason:'row_not_found'};

                        const handle = row.querySelector('.cf-slider__handle[role="slider"]');
                        const track = row.querySelector('.cf-slider__track');
                        if (!handle || !track) return {ok:false, reason:'slider_parts_missing'};

                        const tr = track.getBoundingClientRect();
                        const hr = handle.getBoundingClientRect();
                        if (!tr || tr.width < 4 || tr.height < 2) return {ok:false, reason:'track_not_interactable'};

                        const min = Number(handle.getAttribute('aria-valuemin') || 0);
                        const max = Number(handle.getAttribute('aria-valuemax') || 0);
                        if (!Number.isFinite(min) || !Number.isFinite(max) || max < min) {
                          return {ok:false, reason:'invalid_slider_bounds'};
                        }

                        const desired = Math.max(min, Math.min(max, min + Math.max(0, selectedIndex - 1)));
                        const ratio = (max === min) ? 0 : ((desired - min) / (max - min));
                        const targetX = tr.left + (tr.width * ratio);
                        const targetY = tr.top + (tr.height / 2);

                        const fire = (el, type, x, y, buttons) => {
                          el.dispatchEvent(new MouseEvent(type, {
                            bubbles: true,
                            cancelable: true,
                            clientX: x,
                            clientY: y,
                            buttons: buttons,
                          }));
                        };

                        const startX = hr.left + (hr.width / 2);
                        const startY = hr.top + (hr.height / 2);
                        fire(handle, 'mousedown', startX, startY, 1);
                        fire(document, 'mousemove', targetX, targetY, 1);
                        fire(document, 'mouseup', targetX, targetY, 0);
                        fire(track, 'click', targetX, targetY, 0);

                        try { handle.focus(); } catch(e) {}
                        try { handle.dispatchEvent(new Event('input', { bubbles: true })); } catch(e) {}
                        try { handle.dispatchEvent(new Event('change', { bubbles: true })); } catch(e) {}

                        const now = String(handle.getAttribute('aria-valuenow') || '');
                        const desiredText = String(desired);
                        if (now === desiredText) {
                          return {ok:true, desired:desiredText, now:now};
                        }

                        return {ok:false, reason:'aria_mismatch', desired:desiredText, now:now};
                        """,
                        row_id,
                        int(selected_index),
                    )
                except Exception as e:
                    log_debug("[TARGET_DEBUG]", f"slider-grid row skipped row_id={row_id} value='{value}' reason='script_error:{_short_exc(e)}'")
                    return False

                ok = bool((js_result or {}).get("ok")) if isinstance(js_result, dict) else False
                if ok:
                    log_debug("[TARGET_DEBUG]", f"slider-grid row applied row_id={row_id} value='{value}'")
                    return True

                reason = "unknown"
                if isinstance(js_result, dict):
                    reason = (js_result.get("reason") or reason)
                log_debug("[TARGET_DEBUG]", f"slider-grid row skipped row_id={row_id} value='{value}' reason='{reason}'")
                return False

            # --- cas "options map" (radio/checkbox)
            # IMPORTANT: on n'exige pas kind=="group" pour éviter le couplage ÃƒÂ  la classification (ex: matrix_rows_single_choice)
            opt_map = payload.get("option_xpath_map") or {}
            if opt_map and resolved_itype in ("radio", "checkbox"):

                # 1) lookup direct
                xp = opt_map.get(v_norm) or (opt_map.get(v_fold) if v_fold else None)

                # 2) lookup fuzzy (avec et sans accents)
                if not xp:
                    for k, x in opt_map.items():
                        if not k:
                            continue
                        k_norm = _norm_lc(k)
                        k_fold = _fold_norm_lc(k)

                        # match sur versions normalisées
                        if v_norm and (v_norm == k_norm or v_norm in k_norm or k_norm in v_norm):
                            xp = x
                            break
                        if v_fold and (
                            v_fold == k_norm or v_fold in k_norm or k_norm in v_fold
                            or v_fold == k_fold or v_fold in k_fold or k_fold in v_fold
                        ):
                            xp = x
                            break

                # 3) checkbox unique : "oui/true" => clique la seule option
                if not xp and resolved_itype == "checkbox" and len(opt_map) == 1:
                    if (v_norm in {"oui", "yes", "true", "1", "checked", "on", "x"} or not v_norm):
                        xp = next(iter(opt_map.values()))

                if not xp:
                    if debug_target:
                        log_debug("[TARGET_DEBUG]", f"target_id='{target_id}' kind='{kind}' itype='{resolved_itype}' value='{value}' -> option introuvable (opt_map={len(opt_map)})")
                    return False

                def _first_input_under(node):
                    try:
                        if (node.tag_name or "").lower() == "input":
                            return node
                    except Exception:
                        pass
                    try:
                        return node.find_element(By.XPATH, ".//input[@type='radio' or @type='checkbox']")
                    except Exception:
                        return None

                def _is_selected(inp):
                    try:
                        return bool(inp and inp.is_selected())
                    except Exception:
                        return False

                def _dispatch_check_events(inp):
                    """Set checkbox/radio de faÃƒÂ§on idempotente.

                    But: éviter le Ã¢â‚¬Å“coché puis décochéÃ¢â‚¬Â quand plusieurs stratégies s'enchaÃƒÂ®nent
                    (click label + events) et que la page a des handlers custom.

                    - checkbox: checked=true + input/change (PAS de click synthétique)
                    - radio: checked=true + input/change + click synthétique (souvent nécessaire)
                    """
                    try:
                        if not inp:
                            return

                        # Idempotence: si déjÃƒÂ  sélectionné, ne rien faire
                        try:
                            if inp.is_selected():
                                return
                        except Exception:
                            pass

                        driver.execute_script(
                            """
                            const inp = arguments[0];
                            if (!inp) return;
                            const type = (inp.type || '').toLowerCase();
                            try { inp.checked = true; } catch(e) {}
                            inp.dispatchEvent(new Event('input',  {bubbles:true}));
                            inp.dispatchEvent(new Event('change', {bubbles:true}));
                            // Pour checkbox, click peut retoggler via handlers. Pour radio, click aide souvent.
                            if (type === 'radio') {
                              inp.dispatchEvent(new MouseEvent('click', {bubbles:true}));
                            }
                            """,
                            inp,
                        )
                    except Exception:
                        pass

                def _cdp_click(el) -> bool:
                    # Click "réel" via CDP (plus proche d'un user click)
                    try:
                        # IMPORTANT: certaines grilles (mat-table) scrollent horizontalement.
                        # On force le scroll dans les 2 axes pour éviter les éléments 0x0 / hors viewport.
                        try:
                            driver.execute_script(
                                "arguments[0].scrollIntoView({block:'center', inline:'center'});",
                                el,
                            )
                        except Exception:
                            pass

                        if not hasattr(driver, "execute_cdp_cmd"):
                            if debug_target:
                                log_debug("[TARGET_DEBUG]", "CDP click unavailable: driver has no execute_cdp_cmd()")
                            return False

                        rect = driver.execute_script(
                            "const r = arguments[0].getBoundingClientRect();"
                            "return {x:r.left + r.width/2, y:r.top + r.height/2, w:r.width, h:r.height};",
                            el,
                        )
                        x = int(rect.get("x", 0))
                        y = int(rect.get("y", 0))

                        driver.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y, "button": "none"})
                        driver.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
                        driver.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})
                        return True
                    except Exception as e:
                        if debug_target:
                            log_debug("[TARGET_DEBUG]", f"CDP click failed: {_short_exc(e)}")
                        return False

                def _ensure_pre_clicks_ready(target_xpath: str) -> None:
                    """
                    Rend le target visible AVANT de le chercher/click.
                    Cas typique: Ask&Answer mobile matrix (mat-expansion-panel replié).
                    Budget borné, pas de retry infini.
                    """
                    pre_click_xps = payload.get("pre_click_xpaths") or []
                    if not pre_click_xps:
                        return

                    # DéjÃƒÂ  visible -> no-op
                    try:
                        cur = _find_best_visible(target_xpath)
                        if cur:
                            try:
                                if cur.is_displayed():
                                    return
                            except Exception:
                                return
                    except Exception:
                        pass

                    # Ouvrir le panneau/accordéon (au plus 3 xpaths)
                    for pre_xp in pre_click_xps[:3]:
                        try:
                            pre_el = _find_best_visible(pre_xp)
                            if not pre_el:
                                continue

                            # éviter de retoggler un panneau déjÃƒÂ  ouvert
                            try:
                                if (pre_el.get_attribute("aria-expanded") or "").strip().lower() == "true":
                                    break
                            except Exception:
                                pass

                            try:
                                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", pre_el)
                            except Exception:
                                pass

                            try:
                                pre_el.click()
                            except Exception:
                                try:
                                    driver.execute_script("arguments[0].click();", pre_el)
                                except Exception:
                                    continue
                            break
                        except Exception:
                            continue

                    # Attendre que l'option devienne visible (animation/lazy render Angular)
                    t0 = time.time()
                    while time.time() - t0 < 1.3:
                        try:
                            cur = _find_best_visible(target_xpath)
                            if cur:
                                try:
                                    if cur.is_displayed():
                                        return
                                except Exception:
                                    return
                        except Exception:
                            pass
                        time.sleep(0.05)

                def _click_candidate(node, label: str) -> bool:
                    # 1) click webdriver standard
                    try:
                        node.click()
                        return True
                    except Exception as e:
                        if debug_target:
                            log_debug("[TARGET_DEBUG]", f"native click failed on {label}: {_short_exc(e)}")

                    # 2) ActionChains (souvent plus robuste quand le DOM est Ã¢â‚¬Å“capricieuxÃ¢â‚¬Â)
                    try:
                        from selenium.webdriver.common.action_chains import ActionChains
                        ActionChains(driver).move_to_element(node).pause(0.05).click().perform()
                        return True
                    except Exception as e:
                        if debug_target:
                            log_debug("[TARGET_DEBUG]", f"actionchains click failed on {label}: {_short_exc(e)}")

                    # 3) CDP click (trusted-ish)
                    if _cdp_click(node):
                        return True

                    # 4) JS click (dernier recours, parfois ignoré si anti-bot)
                    try:
                        driver.execute_script("arguments[0].click();", node)
                        return True
                    except Exception as e:
                        if debug_target:
                            log_debug("[TARGET_DEBUG]", f"js click failed on {label}: {_short_exc(e)}")
                        return False

                # 0) pre-clicks (ex: ouvrir un panneau accordéon AVANT de chercher l'option)
                # IMPORTANT: certains panels sont lazy-rendered: tant que le panel est fermé,
                # les mat-radio-button n'existent pas / ne sont pas visibles -> xpath introuvable.
                if (payload.get("pre_click_xpaths") or []) and not payload.get("_preclick_done"):
                    payload["_preclick_done"] = True
                    for pre_xp in (payload.get("pre_click_xpaths") or [])[:3]:
                        try:
                            pre_el = _find_best_visible(pre_xp)
                            if not pre_el:
                                continue
                            # éviter de retoggler un panneau déjÃƒÂ  ouvert
                            try:
                                if (pre_el.get_attribute("aria-expanded") or "").strip().lower() == "true":
                                    continue
                            except Exception:
                                pass

                            try:
                                pre_el.click()
                            except Exception:
                                try:
                                    driver.execute_script("arguments[0].click();", pre_el)
                                except Exception:
                                    pass
                            time.sleep(0.12)
                        except Exception:
                            continue

                # 1) trouver l'élément cible (label/span/input)
                _ensure_pre_clicks_ready(xp)
                try:
                    el = _find_best_visible(xp)
                    if not el:
                        if debug_target:
                            log_debug("[TARGET_DEBUG]", f"element not found for xpath: {xp}")
                        return False
                except Exception as ex:
                    if debug_target:
                        log_debug("[TARGET_DEBUG]", f"element not found for xpath={xp} ({type(ex).__name__}: {_short_exc(ex)})")
                    return False

                # Cas widgets sans <input> sous l'option (ex: Decipher cardrating):
                # la sélection est reflétée par data-selected / aria-selected / aria-checked.
                def _selected_like(node) -> bool:
                    try:
                        ds = (node.get_attribute("data-selected") or "").strip().lower()
                        if ds in ("true", "1", "yes", "on"):
                            return True
                        if (node.get_attribute("aria-selected") or "").strip().lower() == "true":
                            return True
                        if (node.get_attribute("aria-checked") or "").strip().lower() == "true":
                            return True
                        cls = (node.get_attribute("class") or "").lower()
                        if any(tok in cls for tok in (" selected", "selected ", " active", "active ", "checked", "is-selected", "is-checked")):
                            return True
                    except Exception:
                        pass
                    return False

                def _wait_selected_like(node, timeout_s: float = 1.0) -> bool:
                    import time
                    end = time.time() + timeout_s
                    while time.time() < end:
                        if _selected_like(node):
                            return True
                        # Angular Material: l'état est souvent porté par la classe sur mat-radio-button
                        try:
                            cls = (node.get_attribute("class") or "")
                            if "mat-radio-checked" in cls:
                                return True
                        except Exception:
                            pass
                        try:
                            node.find_element(By.XPATH, "ancestor-or-self::*[contains(@class,'mat-radio-checked')][1]")
                            return True
                        except Exception:
                            pass

                        # parfois l'état est porté par un parent (li -> span, etc.)
                        try:
                            node.find_element(
                                By.XPATH,
                                "ancestor-or-self::*[@data-selected='true' or @aria-selected='true' or @aria-checked='true'][1]"
                            )
                            return True
                        except Exception:
                            pass
                        time.sleep(0.05)
                    return False

                def _wait_decipher_clickable_ranking_effect(node, timeout_s: float = 1.0) -> bool:
                    """Validation DOM stricte pour Decipher clickable ranking."""
                    import time
                    end = time.time() + timeout_s
                    while time.time() < end:
                        try:
                            ok = driver.execute_script(
                                """
                                const node = arguments[0];
                                if (!node) return false;
                                const item = node.closest ? node.closest('.customItem') : null;
                                if (!item) return false;
                                const rank = item.querySelector('.customRank');
                                if (!rank) return false;
                                const txt = String(rank.textContent || '').trim();
                                if (/^\d+$/.test(txt)) return true;
                                const cls = String(rank.className || '').toLowerCase();
                                if (cls.includes('customrankselected')) return true;
                                return false;
                                """,
                                node,
                            )
                            if ok:
                                return True
                        except Exception:
                            pass
                        time.sleep(0.05)
                    return False

                if payload.get("decipher_clickable_ranking"):
                    clicked = _click_candidate(el, "decipher_clickable_ranking")
                    if not clicked:
                        if debug_target:
                            log_debug("[TARGET_DEBUG]", f"decipher_clickable_ranking click failed: value='{value}' xpath='{xp}'")
                        return False
                    if _wait_decipher_clickable_ranking_effect(el, timeout_s=1.0):
                        return True
                    if debug_target:
                        log_debug("[TARGET_DEBUG]", f"decipher_clickable_ranking no rank signal after click: value='{value}' xpath='{xp}'")
                    return False

                # 2) clic Ã¢â‚¬Å“normalÃ¢â‚¬Â sur la cible
                _click_candidate(el, "target")

                def _ipsos_slider_value_matches(node, expected: str) -> bool:
                    """Validation DOM pour les sliders Likert IPSOS (bootstrap-slider)."""
                    if not payload.get("ipsos_slider"):
                        return False
                    exp = (expected or "").strip()
                    if not exp:
                        return False
                    try:
                        ok = driver.execute_script(
                            """
                            const marker = arguments[0];
                            const expected = String(arguments[1] || '').trim();
                            if (!expected) return false;

                            let container = marker;
                            for (let i = 0; i < 10 && container; i++) {
                              if (container.querySelector && container.querySelector('input.slider-form-field.bs-slider')) break;
                              container = container.parentElement;
                            }

                            let field = container && container.querySelector
                              ? container.querySelector('input.slider-form-field.bs-slider')
                              : null;
                            if (!field) {
                              field = document.querySelector('#questionContent input.slider-form-field.bs-slider, input.slider-form-field.bs-slider');
                            }
                            if (!field) return false;

                            const current = String(field.value || field.getAttribute('value') || field.getAttribute('data-value') || '').trim();
                            if (current === expected) return true;

                            const scope = field.parentElement || container || document;
                            const handle = scope.querySelector && scope.querySelector('.slider-handle[aria-valuenow]');
                            const ariaNow = handle ? String(handle.getAttribute('aria-valuenow') || '').trim() : '';
                            return ariaNow === expected;
                            """,
                            node,
                            exp,
                        )
                        return bool(ok)
                    except Exception:
                        return False

                inp = _first_input_under(el)
                if not inp:
                    if _wait_selected_like(el, timeout_s=1.0):
                        return True

                # AreYouNet: la sélection est stockée dans un <input type=hidden name=...>
                # (pas de radio/checkbox natif). On valide en lisant la valeur aprÃƒÂ¨s clic.
                try:
                    ayn_name = (payload.get('ayn_field_name') or '').strip()
                    ayn_map = payload.get('ayn_value_map') or {}
                    if ayn_name and ayn_map:
                        exp = ayn_map.get(v_norm) or (ayn_map.get(v_fold) if v_fold else None)
                        if exp is not None and exp != '':
                            ok = driver.execute_script(
                                """
                                const name = arguments[0];
                                const exp  = arguments[1];
                                const els = document.getElementsByName(name);
                                for (const e of els) {
                                  if (!e) continue;
                                  if ((e.value || '') === exp) return true;
                                }
                                return false;
                                """,
                                ayn_name,
                                str(exp),
                            )
                            if ok:
                                return True
                except Exception:
                    pass

                # NEW: si la cible est un <label for="...">, forcer l'input associé
                try:
                    if (el.tag_name or "").lower() == "label":
                        fid = (el.get_attribute("for") or "").strip()
                        if fid:
                            inp_for = driver.find_element(By.ID, fid)
                            if not _is_selected(inp_for):
                                _dispatch_check_events(inp_for)
                except Exception:
                    pass

                inp = _first_input_under(el)
                if _is_selected(inp):
                    return True

                # NEW: Angular Material (Ask&Answer desktop matrix)
                # Le <label> peut etre 0x0 / non-interactif. On clique le conteneur mat-radio-button,
                # puis on valide via mat-radio-checked (plus fiable que input.checked aprÃƒÂ¨s re-render).
                try:
                    mr = el.find_element(
                        By.XPATH,
                        "ancestor-or-self::*[self::mat-radio-button or contains(@class,'mat-radio-button')][1]",
                    )
                    _click_candidate(mr, "mat-radio-button")
                    if _wait_selected_like(mr, timeout_s=0.8):
                        return True
                    try:
                        mc = mr.find_element(By.XPATH, ".//span[contains(@class,'mat-radio-container')][1]")
                        _click_candidate(mc, "mat-radio-container")
                        if _wait_selected_like(mr, timeout_s=0.8):
                            return True
                    except Exception:
                        pass
                except Exception:
                    pass

                # 3) si on a cliqué un label non interactif (pointer-events, overlay), tenter le span
                try:
                    sp = el.find_element(By.XPATH, ".//span[1]")
                    _click_candidate(sp, "span")
                except Exception:
                    pass

                inp = inp or _first_input_under(el)
                if _is_selected(inp):
                    return True

                # 4) tenter un clic direct sur l'input (meme si masqué) via JS
                if inp:
                    _click_candidate(inp, "input")
                    if _is_selected(inp):
                        return True

                    # 5) dernier recours DOM-only: forcer checked + events (ce qui active souvent le bouton Continue)
                    if not _is_selected(inp):
                        _dispatch_check_events(inp)
                    if _is_selected(inp):
                        return True

                # --- vérification robuste (évite stale / re-render) ---
                inp_id = None
                inp_name = None

                try:
                    # si label[for] => input id
                    if (el.tag_name or "").lower() == "label":
                        _for = (el.get_attribute("for") or "").strip()
                        if _for:
                            inp_id = _for
                except Exception:
                    pass

                try:
                    if not inp_id:
                        if (el.tag_name or "").lower() == "input":
                            inp_id = (el.get_attribute("id") or "").strip() or None
                            inp_name = (el.get_attribute("name") or "").strip() or None
                except Exception:
                    pass

                try:
                    if not inp_id or not inp_name:
                        inp = _first_input_under(el)
                        if inp:
                            inp_id = inp_id or ((inp.get_attribute("id") or "").strip() or None)
                            inp_name = inp_name or ((inp.get_attribute("name") or "").strip() or None)
                except Exception:
                    pass

                if _wait_checked(inp_id, inp_name, timeout_s=1.2):
                    return True

                if _ipsos_slider_value_matches(el, value):
                    return True

                if debug_target:
                    log_debug("[TARGET_DEBUG]", f"selection failed after waits: value='{value}' xpath='{xp}' inp_id='{inp_id}' inp_name='{inp_name}'")
                return False

            # --- cas multi_text : plusieurs cases texte pour UNE meme question (OpenTextMultiLines)
            if kind == "multi_text" and resolved_itype in ("text", "textarea", "number"):
                fields = payload.get("fields") or []
                if not fields:
                    return False

                def _locate_field(fld: dict):
                    # 1) xpath + alt_xpaths
                    for cand_xp in [fld.get("xpath")] + list(fld.get("alt_xpaths") or []):
                        if not cand_xp:
                            continue
                        elc = _find_best_visible(cand_xp)
                        if elc:
                            return elc

                    # 2) By.NAME / By.ID (dernier recours)
                    nm = (fld.get("name") or "").strip()
                    if nm:
                        try:
                            for c in driver.find_elements(By.NAME, nm):
                                try:
                                    if c.is_displayed():
                                        return c
                                except Exception:
                                    continue
                        except Exception:
                            pass

                    fid = (fld.get("id") or "").strip()
                    if fid:
                        try:
                            for c in driver.find_elements(By.ID, fid):
                                try:
                                    if c.is_displayed():
                                        return c
                                except Exception:
                                    continue
                        except Exception:
                            pass

                    return None

                # Remplit la 1ÃƒÂ¨re case vide (déterministe, pas de boucle/retry infini)
                for fld in fields:
                    try:
                        elx = _locate_field(fld)
                        if not elx:
                            continue
                        try:
                            if not elx.is_enabled():
                                continue
                        except Exception:
                            pass

                        cur = (elx.get_attribute("value") or "").strip()
                        if cur:
                            continue

                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", elx)
                        try:
                            elx.clear()
                        except Exception:
                            pass
                        elx.send_keys(value or "")
                        return True
                    except Exception:
                        continue

                # Si tout est déjÃƒÂ  rempli, on ignore l'excÃƒÂ¨s de valeurs (évite fallback)
                return True

            # --- cas single (text/textarea/dropdown/button)
            if kind == "single":
                xp = payload.get("xpath")
                if not xp:
                    return False

                if resolved_itype in ("text", "textarea", "number"):
                    try:
                        el = driver.find_element(By.XPATH, xp)
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                        try:
                            el.clear()
                        except Exception:
                            pass
                        el.send_keys(value or "")
                        return True
                    except Exception:
                        return False

                # --- Dropdown: set value via JS + dispatch events (works even if <select> is hidden) ---
                # + cas spécial "sq-sliderpoints" : cliquer / dragger la piste pour que l'UI se mette ÃƒÂ  jour
                if resolved_itype == "dropdown":

                    # Trouver le <select> meme si l'id a changé (re-render).
                    def _iter_dropdown_candidates():
                        cands = []

                        def _add(elems):
                            for e in elems or []:
                                try:
                                    if (e.tag_name or "").lower() == "select":
                                        cands.append(e)
                                except Exception:
                                    continue

                        # 1) xpath principal
                        if xp:
                            try:
                                _add(driver.find_elements(By.XPATH, xp))
                            except Exception:
                                pass

                        # 2) alt_xpaths (ex: //select[@name='...'])
                        for ax in (payload.get("alt_xpaths") or []):
                            try:
                                _add(driver.find_elements(By.XPATH, ax))
                            except Exception:
                                pass

                        # 3) By.NAME / By.ID (au cas oÃƒÂ¹)
                        nm = (payload.get("name") or "").strip()
                        if nm:
                            try:
                                _add(driver.find_elements(By.NAME, nm))
                            except Exception:
                                pass

                        eid = (payload.get("id") or "").strip()
                        if eid:
                            try:
                                _add(driver.find_elements(By.ID, eid))
                            except Exception:
                                pass

                        # 4) fallback: si aucun locator n'a matché (re-render), tenter tous les <select>.
                        #    Le filtrage se fera plus bas via la présence de l'option demandée.
                        if not cands:
                            try:
                                _add(driver.find_elements(By.CSS_SELECTOR, "select"))
                            except Exception:
                                pass

                        # dédup
                        uniq = []
                        seen = set()
                        for e in cands:
                            try:
                                k = getattr(e, "_id", None) or getattr(e, "id", None)
                            except Exception:
                                k = None
                            if k and k in seen:
                                continue
                            if k:
                                seen.add(k)
                            uniq.append(e)
                        return uniq

                    v = (value or "").strip()
                    if not v:
                        return False

                    v = (value or "").strip()
                    if not v:
                        return False

                    def _key(s: str) -> str:
                        # Robust label normalization for dropdown/sliderpoints matching:
                        # - fold accents
                        # - normalize quotes/apostrophes
                        # - drop stray punctuation
                        s = (s or "").replace("\xa0", " ")
                        s = unicodedata.normalize("NFKD", s)
                        s = "".join(c for c in s if not unicodedata.combining(c))

                        # normalize common unicode quotes -> ascii
                        s = s.replace("\u2019", "'").replace("\u2018", "'")
                        s = s.replace("\u00b4", "'").replace("`", "'")
                        s = s.replace("\u201c", '"').replace("\u201d", '"')

                        # remove quotes/apostrophes and light punctuation
                        s = re.sub(r"[\"'\u2019\u2018\u00b4`]+", " ", s)
                        s = re.sub(r"[.,;:!?()\[\]{}<>]+", " ", s)
                        s = re.sub(r"\s+", " ", s).strip().lower()
                        return s

                    v_lc = _key(v)

                    for sel in _iter_dropdown_candidates():
                        # Best-effort: amener le select dans le viewport (meme s'il est masqué)
                        try:
                            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", sel)
                        except Exception:
                            pass

                        # options exploitables (hors placeholder)
                        try:
                            raw_opts = sel.find_elements(By.TAG_NAME, "option")
                        except Exception:
                            raw_opts = []

                        real_opts = []  # [(idx, text_lc, value)]
                        for o in raw_opts:
                            try:
                                if (o.get_attribute("disabled") or "").strip():
                                    continue
                                t = _key(o.text or "")
                                if not t:
                                    continue
                                ov = (o.get_attribute("value") or "").strip()
                                if ov in ("", "-1"):
                                    continue
                                if any(tok in t for tok in ("veuillez", "sélection", "selection", "select", "choose")):
                                    continue
                                real_opts.append((len(real_opts), t, ov))
                            except Exception:
                                continue

                        if not real_opts:
                            continue

                        best_val = None
                        best_idx = None

                        # matching strict puis partiel
                        for i, t, ov in real_opts:
                            if (t == v_lc) or (v_lc in t) or (t in v_lc):
                                best_val = ov
                                best_idx = i
                                break

                        if best_val is None:
                            for i, t, ov in real_opts:
                                if v_lc and (v_lc in t):
                                    best_val = ov
                                    best_idx = i
                                    break

                        if best_val is None or best_idx is None:
                            continue

                        # 1) source de vérité: <select>
                        try:
                            driver.execute_script(
                                """
                                const sel = arguments[0];
                                const val = arguments[1];
                                sel.value = val;
                                try { sel.dispatchEvent(new Event('input',  {bubbles:true})); } catch(e) {}
                                try { sel.dispatchEvent(new Event('change', {bubbles:true})); } catch(e) {}
                                try {
                                  if (window.jQuery && window.jQuery(sel).selectpicker) {
                                    window.jQuery(sel).selectpicker('refresh');
                                  }
                                } catch(e) {}
                                """,
                                sel,
                                best_val,
                            )
                        except Exception:
                            continue

                        # 2) sliderpoints: clic/drag sur la piste pour déplacer le handle (sinon off-scale)
                        try:
                            meta = payload.get("meta") or {}
                            src = (meta.get("source") or "").strip().lower()
                            sel_id = (sel.get_attribute("id") or "").strip()
                            is_sliderpoints = (src == "sq-sliderpoints") or ("sliderpoints" in sel_id.lower())

                            if is_sliderpoints:
                                track = None
                                if sel_id:
                                    try:
                                        track = driver.find_element(By.ID, f"sliderpoints_{sel_id}")
                                    except Exception:
                                        track = None

                                if track is None:
                                    try:
                                        container = sel.find_element(
                                            By.XPATH,
                                            "ancestor::*[contains(@class,'sq-sliderpoints-container') or contains(@class,'sq-sliderpoints-element')][1]",
                                        )
                                        track = container.find_element(By.CSS_SELECTOR, ".ui-slider-horizontal")
                                    except Exception:
                                        track = None

                                if track is not None:
                                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", track)
                                    r = track.rect or {}
                                    w = int(r.get("width", 0) or 0)
                                    h = int(r.get("height", 0) or 0)

                                    steps = max(1, len(real_opts) - 1)
                                    x = int((best_idx / steps) * max(1, w - 4)) + 2
                                    y = max(1, h // 2)

                                    try:
                                        ActionChains(driver).move_to_element_with_offset(track, x, y).click().perform()
                                    except Exception:
                                        pass

                                    def _handle_offscale() -> bool:
                                        try:
                                            hnd = track.find_element(By.CSS_SELECTOR, ".ui-slider-handle")
                                            st = (hnd.get_attribute("style") or "")
                                            return ("-40" in st) or ("offscale" in st.lower())
                                        except Exception:
                                            return False

                                    if _handle_offscale():
                                        try:
                                            hnd = track.find_element(By.CSS_SELECTOR, ".ui-slider-handle")
                                            ActionChains(driver).click_and_hold(hnd).move_to_element_with_offset(track, x, y).release().perform()
                                        except Exception:
                                            pass

                                    try:
                                        cur_val = (sel.get_attribute("value") or "").strip()
                                        if cur_val != best_val:
                                            # si on rate l'UI, on laisse quand meme le <select> en source de vérité
                                            pass
                                    except Exception:
                                        pass

                        except Exception:
                            # si le forcing UI échoue, on considÃƒÂ¨re quand meme le select comme set
                            pass

                        return True

                    return False

                if resolved_itype == "button":
                    try:
                        el = driver.find_element(By.XPATH, xp)
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                        el.click()
                        return True
                    except Exception:
                        return False

            return False

        # Exécuter dans le bon frame si possible
        if switch_to_frame_chain is not None and frame_chain:
            with switch_to_frame_chain(driver, frame_chain) as ok:
                if not ok:
                    return False
                return _apply_in_current_context()

        return _apply_in_current_context()

    except Exception:
        return False

# ------------------- Parsing "libellé //// type" -------------------
# Types acceptés (synonymes FR/EN)
_TYPE_ALIASES = {
    "dropdown": {"dropdown", "menu", "select", "liste", "combobox"},
    "button":   {"button", "bouton", "cta"},
    "checkbox": {"checkbox", "case", "case ÃƒÂ  cocher", "check", "cocher"},
    "radio":    {"radio", "option"},
    "text":     {"text", "texte", "champ", "input"},
    "textarea": {"textarea", "texte long", "open-end", "ouvert"},
    "number":   {"number", "numérique", "numerique"},
    "matrix-col": {"matrix-col", "matrix column", "colonne", "col"},
    "open":     {"open", "ouvrir"},
}

def _parse_typed_instruction3(instr: str) -> tuple[str, str | None, str]:
    """
    Parse 'libellé //// type //// contexte-question' (slashes >=4 tolérés).
    - Retourne (label, type_normalisé|None, context)
    - TolÃƒÂ¨re des '>' accidentels aprÃƒÂ¨s <type>.
    """
    parts = re.split(r"/{4,}", instr or "")
    label = _norm(parts[0]) if parts else ""
    raw_type = _norm_lc(parts[1]) if len(parts) > 1 else ""
    context = _norm(parts[2]) if len(parts) > 2 else ""

    # nettoyer un éventuel '>' parasite aprÃƒÂ¨s type
    raw_type = raw_type.replace(">", "").strip()

    itype = None
    if raw_type:
        # réutilise la table dÃ¢â‚¬â„¢alias existante
        for t, aliases in _TYPE_ALIASES.items():
            if raw_type in aliases:
                itype = t
                break
        if itype is None:
            # heuristiques déjÃƒÂ  présentes dans _parse_typed_instruction
            if re.search(r"drop|select|menu|combo", raw_type): itype = "dropdown"
            elif re.search(r"button|bouton|cta", raw_type): itype = "button"
            elif re.search(r"check|coch", raw_type): itype = "checkbox"
            elif re.search(r"radio|option", raw_type): itype = "radio"
            elif re.search(r"text|champ|input", raw_type): itype = "text"
    return label, itype, context

# ---------- Sanitize instruction : corrige une option ÃƒÂ  risque ----------
def _get_visible_options(driver):
    # RécupÃƒÂ¨re un set de libellés dÃ¢â‚¬â„¢options visibles (radios/checkbox)
    opts = set()
    sels = [
        "label span.p-radio-text",
        "label span.p-checkbox-text",
        "label",
        "li label",
        "[role='radio']",
        "[role='checkbox']",
    ]
    for css in sels:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, css):
                t = (el.text or el.get_attribute("innerText") or "").strip()
                if t and len(t) >= 2:
                    opts.add(_norm(t))
        except Exception:
            continue
    return opts

def _get_page_text_lc(driver):
    try:
        return " ".join(
            driver.execute_script(
                "return Array.from(document.querySelectorAll('body *'))"
                ".filter(e=>getComputedStyle(e).display!=='none' && e.offsetParent!==null)"
                ".map(e=>(e.innerText||'').trim()).filter(t=>t.length>4);"
            )
        ).lower()
    except Exception:
        return ""

def _sanitize_instruction_with_page_context(driver, label, itype):
    """
    Si l'IA propose une réponse potentiellement disqualifiante,
    tente de la remplacer par une alternative 'safe' disponible.
    """
    lbl = _norm(label)
    if not lbl or itype not in {"radio", "checkbox", "dropdown"}:
        return label  # pas concerné

    # Options visibles sur la page
    opts = _get_visible_options(driver)

    # Contexte question
    page = _get_page_text_lc(driver)
    is_sector_question = any(
        k in page for k in ["secteur", "industrie", "travaillez", "employ", "domaines"]
    )

    # Listes de mots ÃƒÂ  risque / sÃƒÂ»rs
    risky = {
        "non",
        "jamais",
        "certainement pas",
        "aucun",
        "aucune",
        "je préfÃƒÂ¨re ne pas le dire",
        "preferer ne pas",
        "none",
        "no",
        "never",
    }
    safe_pos = ["oui", "souvent", "parfois", "réguliÃƒÂ¨rement", "hebdomadaire", "mensuel"]
    safe_neutral = ["je ne sais pas", "je ne m'en souviens pas", "neutre"]

    # Exception emploi/secteurs ; privilégier "Aucune de ces réponses" si présente
    if is_sector_question:
        for o in opts:
            if "aucune de ces réponses" in o or "aucun de ces choix" in o:
                return "Aucune de ces réponses"

    # Si la proposition est manifestement risquée ; préférer une alternative
    if any(tok in lbl for tok in risky):
        # 1) 'Oui' si disponible
        for o in opts:
            if o == "oui":
                return "Oui"
        # 2) un choix positif si dispo
        for k in safe_pos:
            for o in opts:
                if k in o:
                    return _norm(o)
        # 3) sinon une neutre
        for k in safe_neutral:
            for o in opts:
                if k in o:
                    return _norm(o)

    # Cas spécifiques : ordinateur / internet
    if any(
        k in page for k in ["ordinateur", "computer", "internet", "e-mail", "email"]
    ):
        for o in opts:
            if o == "oui":
                return "Oui"

    # rien ÃƒÂ  corriger
    return label

_OPEN_FIELD_TOKENS = {
    "jour",
    "mois",
    "année",
    "annee",
    "year",
    "month",
    "pays",
    "country",
    "ville",
    "city",
    "state",
    "province",
}

def _split_multiline_instruction(instr: str) -> list[str]:
    items = []
    for line in (instr or "").splitlines():
        line = _norm(line)
        if line:
            line = re.sub(r"^\d+\)\s*", "", line)  # 1) ...
            line = re.sub(r"^[\-\Ã¢â‚¬Â¢\Ã¢â‚¬â€œ\Ã¢â‚¬â€\Ã‚Â·]+\s*", "", line)  # - ...
            items.append(line)
    return items

_QID_RE = re.compile(r"^Q\d+$", re.IGNORECASE)

def _parse_action_line(raw: str) -> dict:
    """
    Parse une ligne d'instruction (batch + legacy).
    Formats supportés :
    - QID //// target_id //// valeur //// itype //// contexte
    - QID //// valeur //// itype //// contexte
    - target_id //// valeur //// itype //// contexte
    - valeur //// itype //// contexte
    """
    parts = [p.strip() for p in re.split(r"/{4,}", (raw or "")) if p.strip()]

    out = {"qid": None, "target_id": None, "value": "", "itype": "", "context": ""}

    if len(parts) >= 5:
        out["qid"] = parts[0]
        out["target_id"] = parts[1]
        out["value"] = parts[2]
        out["itype"] = parts[3]
        out["context"] = parts[4]
        return out

    if len(parts) == 4:
        if _QID_RE.match(parts[0] or ""):
            out["qid"] = parts[0]
            out["value"] = parts[1]
            out["itype"] = parts[2]
            out["context"] = parts[3]
        else:
            out["target_id"] = parts[0]
            out["value"] = parts[1]
            out["itype"] = parts[2]
            out["context"] = parts[3]
        return out

    if len(parts) == 3:
        out["value"] = parts[0]
        out["itype"] = parts[1]
        out["context"] = parts[2]
        return out

    return out

# action_dispatcher.py
# Ãƒâ‚¬ placer AVANT la logique générique itype == "button"

def _wait_for_button_effect(driver, *, timeout=6):
    """
    Valide qu'un clic CTA a réellement eu un effet.
    Effets possibles :
    - changement d'URL
    - apparition spinner / overlay
    - disparition du bouton
    - désactivation du bouton
    """
    import time

    start_url = driver.current_url
    start_ts = time.time()

    while time.time() - start_ts < timeout:
        time.sleep(0.3)

        # 1Ã¯Â¸ÂÃ¢Æ’Â£ URL changée
        if driver.current_url != start_url:
            return True

        # 2Ã¯Â¸ÂÃ¢Æ’Â£ Bouton disparu ou disabled
        try:
            btn = driver.find_element(By.ID, "acceptAndTakeSurveyLink2")
            if not btn.is_displayed():
                return True
            if btn.get_attribute("aria-disabled") == "true":
                return True
            if "disabled" in (btn.get_attribute("class") or ""):
                return True
        except Exception:
            # bouton plus présent ; effet OK
            return True

        # 3Ã¯Â¸ÂÃ¢Æ’Â£ Overlay / spinner
        overlays = driver.find_elements(By.CSS_SELECTOR, ".loading, .spinner, .overlay")
        if overlays:
            return True

        try:
            spin = driver.find_element(By.ID, "loadingSpin3")
            if spin.is_displayed():
                return True
        except Exception:
            pass

    return False

def handle_consent_screen(driver):
    """
    Résout un écran/bandeau de consentement (cookies/RGPD) quand il est réellement bloquant.

    IMPORTANT:
    - Ne jamais retourner True si aucun effet DOM/URL n'est observé.
    - Ne pas confondre un simple widget cookies non bloquant (ex: Evidon) avec un overlay.
    """
    import time
    from selenium.webdriver.common.by import By

    def _norm_lc(s: str) -> str:
        return " ".join((s or "").lower().split()).strip()

    CMP_CONTAINER_SELECTORS = [
        "#onetrust-banner-sdk",
        "#onetrust-consent-sdk",
        ".qc-cmp2-container",
        ".qc-cmp2-ui",
        ".qc-cmp-cleanslate",
        ".didomi-popup-container",
        "#didomi-popup",
        ".truste_overlay",
        ".truste_box_overlay",
        "#CybotCookiebotDialog",
        ".cc-window",
        ".cookie-banner",
        "[role='alertdialog']",
        "[role='dialog'][aria-modal='true']",
        "[aria-modal='true']",
    ]

    ACCEPT_WORDS = [
        "tout accepter",
        "accepter",
        "j'accepte",
        "j accepte",
        "accept all",
        "accept",
        "i accept",
        "agree",
        "i agree",
        "ok",
        "d'accord",
    ]

    REJECT_WORDS = [
        "refuser",
        "reject",
        "decline",
        "disagree",
        "necessary",
        "nécessaire",
        "paramétrer",
        "settings",
        "préférences",
        "preferences",
    ]

    def _cmp_overlay_present() -> bool:
        """True si un container CMP *bloquant* (grand et visible) est présent."""
        try:
            return bool(driver.execute_script(
                r"""
                const vw = Math.max(320, window.innerWidth || 0);
                const vh = Math.max(240, window.innerHeight || 0);
                const minArea = vw * vh * 0.12;

                const selectors = arguments[0] || [];
                const kw = ['cookie','cookies','consent','gdpr','rgpd','privacy','confidential'];

                function isVisible(e){
                  try{
                    const s = window.getComputedStyle(e);
                    if (!s) return false;
                    if (s.display === 'none' || s.visibility === 'hidden') return false;
                    const r = e.getBoundingClientRect();
                    if (!r) return false;
                    if (r.width < 60 || r.height < 40) return false;
                    if (r.bottom < 0 || r.right < 0 || r.top > vh || r.left > vw) return false;
                    return true;
                  }catch(_){ return false; }
                }

                const cand = [];
                for (const sel of selectors){
                  try{ cand.push(...Array.from(document.querySelectorAll(sel))); }catch(_){ }
                }

                for (const el of cand){
                  if (!isVisible(el)) continue;
                  const r = el.getBoundingClientRect();
                  if ((r.width * r.height) < minArea) continue;
                  const t = (el.innerText || '').toLowerCase();
                  if (kw.some(k => t.includes(k))) return true;

                  const blob = ((el.id||'') + ' ' + (el.className||'')).toLowerCase();
                  if (blob.includes('onetrust') || blob.includes('qc-cmp') || blob.includes('didomi') || blob.includes('truste') || blob.includes('cookiebot'))
                    return true;
                }
                return false;
                """,
                CMP_CONTAINER_SELECTORS,
            ))
        except Exception:
            return False

    def _sig() -> str:
        try:
            url = driver.current_url or ""
        except Exception:
            url = ""
        try:
            txt_len = int(driver.execute_script("return (document.body && (document.body.innerText||'').length) || 0;") or 0)
        except Exception:
            txt_len = 0
        try:
            n_btn = len(driver.find_elements(By.CSS_SELECTOR, "button, a, [role='button'], input[type='submit'], input[type='button']"))
        except Exception:
            n_btn = 0
        return f"{url}||{txt_len}||{n_btn}||{int(_cmp_overlay_present())}"

    try:
        before_url = driver.current_url or ""
    except Exception:
        before_url = ""
    before_sig = _sig()

    def _wait_change(before_sig: str, before_url: str, timeout_s: float = 6.0) -> bool:
        end = time.time() + timeout_s
        while time.time() < end:
            time.sleep(0.25)

            # 1) URL changée
            try:
                if driver.current_url != before_url:
                    return True
            except Exception:
                pass

            # 2) Signature DOM/overlay changée
            if _sig() != before_sig:
                return True

        return False

    # 0) Ipsos / entercdn : écran "Politique de confidentialité" (checkbox + CTA "Accepter et commencer")
    #    Ex: input#privacyPolicyCheckbox1 + a#acceptAndTakeSurveyLink2
    def _scroll_center(el) -> None:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", el)
        except Exception:
            pass

    def _click_best_effort(el) -> bool:
        if el is None:
            return False
        _scroll_center(el)
        time.sleep(0.12)
        try:
            el.click()
            return True
        except Exception:
            try:
                ActionChains(driver).move_to_element(el).click().perform()
                return True
            except Exception:
                try:
                    driver.execute_script("arguments[0].click();", el)
                    return True
                except Exception:
                    return False

    def _handle_ipsos_privacy_policy_page() -> bool:
        # DÃ©tection volontairement stricte (Ã©vite les faux positifs sur d'autres consent screens)
        intercept_only = (os.getenv("CTA_INTERCEPT_ONLY", "") or "").strip().lower() in {"1", "true", "yes", "on"}
        try:
            cta = driver.find_element(
                By.CSS_SELECTOR,
                "a.btn.btn-primary[id^='acceptAndTakeSurveyLink']",
            )
        except Exception:
            if intercept_only:
                print("[CTA_INTERCEPT] ipsos_privacy_policy cta_not_found")
            return False

        try:
            # Patterns IPSOS observÃ©s:
            # - privacyPolicyCheckbox* (ancien pattern, conservÃ© pour rÃ©trocompatibilitÃ©)
            # - consentCheckbox* / consentContainer:* (pattern actuel 2025+)
            cbs = driver.find_elements(
                By.CSS_SELECTOR,
                "input[type='checkbox']#privacyPolicyCheckbox1, "
                "input[type='checkbox'][name*='privacyPolicyCheckbox'], "
                "input[type='checkbox'][id*='privacyPolicyCheckbox'], "
                "input[type='checkbox'][id*='consentCheckbox'], "
                "input[type='checkbox'][name*='consentCheckbox'], "
                "input[type='checkbox'][name*='consentContainer']"
            )
        except Exception:
            cbs = []
            
        if not cbs:
            return False

        cb = cbs[0]

        # 1) Cocher la policy checkbox (préférer le <label for=...>)
        try:
            already = bool(cb.is_selected())
        except Exception:
            already = False

        if not already:
            try:
                cb_id = (cb.get_attribute("id") or "").strip()
            except Exception:
                cb_id = ""

            clicked = False
            if cb_id:
                try:
                    lab = driver.find_element(By.CSS_SELECTOR, f"label[for='{cb_id}']")
                    clicked = _click_best_effort(lab)
                except Exception:
                    clicked = False

            if not clicked:
                clicked = _click_best_effort(cb)

            # Validation : on attend que is_selected() passe ÃƒÂ  True
            deadline = time.time() + 2.5
            while time.time() < deadline:
                try:
                    if cb.is_selected():
                        break
                except Exception:
                    break
                time.sleep(0.1)

        try:
            if not cb.is_selected():
                return False  # pas d'effet observable -> on n'annonce pas le succÃƒÂ¨s
        except Exception:
            return False

        # 2) Cliquer "Accepter et commencer" + valider un effet (URL/spinner/disparition)
        if intercept_only:
            try:
                import Survey.input_handler
                if Survey.input_handler.click_cta_strong_any_context(driver, "accepter et commencer"):
                    print("[CTA_INTERCEPT] ipsos_privacy_policy cta_found intercept_ok")
                    return True
                print("[CTA_INTERCEPT] ipsos_privacy_policy cta_found intercept_impossible")
                return False
            except Exception:
                print("[CTA_INTERCEPT] ipsos_privacy_policy cta_found intercept_impossible")
                return False

        if not _click_best_effort(cta):
            return False

        try:
            if _wait_for_button_effect(driver, timeout=10):
                return True
        except Exception:
            pass

        # fallback : signature/URL
        return _wait_change(before_sig, before_url, timeout_s=8.0)

    if _handle_ipsos_privacy_policy_page():
        return True

    # 0bis) Affinnova / NIQ launch gate: CTA "LANCER L'ÉTUDE" (popup + switch main/secondary)
    def _handle_affinnova_launch_gate() -> bool:
        try:
            launch = driver.find_element(By.CSS_SELECTOR, "a.launchButton[onclick*='showSurvey'], a.launchButton")
        except Exception:
            return False

        try:
            if not launch.is_displayed():
                return False
        except Exception:
            return False

        handles_before = set()
        try:
            handles_before = set(driver.window_handles)
        except Exception:
            pass

        if not _click_best_effort(launch):
            return False

        # Effet attendu: nouvelle fenêtre OU transition visuelle main->secondary
        end = time.time() + 7.0
        while time.time() < end:
            time.sleep(0.2)

            try:
                if set(driver.window_handles) != handles_before:
                    return True
            except Exception:
                pass

            try:
                transitioned = bool(driver.execute_script(r"""
                    const main = document.querySelector('#main');
                    const secondary = document.querySelector('#secondary');
                    if (!main || !secondary) return false;
                    const ms = window.getComputedStyle(main);
                    const ss = window.getComputedStyle(secondary);
                    const mainHidden = !!ms && (ms.display === 'none' || ms.visibility === 'hidden');
                    const secondaryShown = !!ss && (ss.display !== 'none' && ss.visibility !== 'hidden');
                    return mainHidden && secondaryShown;
                """))
                if transitioned:
                    return True
            except Exception:
                pass

        return _wait_change(before_sig, before_url, timeout_s=2.0)

    if _handle_affinnova_launch_gate():
        return True

    # 0ter) Walr country-routing gate: cliquer #btnNext (le JS de la page auto-sélectionne
    #        le pays via .cRadio, le bot doit juste déclencher le bouton Suivant).
    def _handle_walr_country_routing_gate() -> bool:
        # Vérifier le signal Walr distinctif (.cRadio + .cRef) avant de chercher #btnNext
        try:
            has_walr_signal = bool(driver.execute_script(
                "return document.querySelectorAll('.cRadio').length >= 10 && !!document.querySelector('.cRef');"
            ))
        except Exception:
            has_walr_signal = False
        if not has_walr_signal:
            return False

        try:
            btn = driver.find_element(By.CSS_SELECTOR, "#btnNext")
        except Exception:
            return False
        try:
            if not btn.is_displayed():
                return False
        except Exception:
            return False

        intercept_only = (os.getenv("CTA_INTERCEPT_ONLY", "") or "").strip().lower() in {"1", "true", "yes", "on"}
        if intercept_only:
            label = _norm_lc(btn.get_attribute("value") or btn.text or "suivant")
            try:
                import Survey.input_handler
                Survey.input_handler.click_cta_strong_any_context(driver, label)
            except Exception:
                pass
            return True  # interception signalée

        if not _click_best_effort(btn):
            return False
        return _wait_change(before_sig, before_url, timeout_s=8.0)

    if _handle_walr_country_routing_gate():
        return True

    # 0quater) Walr intro/final interstitial: Q=FINAL + #btnNext (sans .cRadio/.cRef).
    #          Exemple: page "Veuillez cliquer sur Suivant" avant entrée dans l'enquête.
    def _handle_walr_intro_final_gate() -> bool:
        try:
            has_signal = bool(driver.execute_script(r"""
                const hasWalrFooter = !!document.querySelector('a.logo2link[href*="walr.com"]');
                const q = document.querySelector('input[type="hidden"]#Q');
                const btn = document.querySelector('#btnNext');
                const isVisible = (el) => {
                    if (!el) return false;
                    const s = window.getComputedStyle(el);
                    if (!s || s.display === 'none' || s.visibility === 'hidden') return false;
                    const r = el.getBoundingClientRect();
                    return !!(r && r.width > 10 && r.height > 10);
                };
                return hasWalrFooter && q && (q.value || '').toUpperCase() === 'FINAL' && isVisible(btn);
            """))
        except Exception:
            has_signal = False
        if not has_signal:
            return False

        try:
            btn = driver.find_element(By.CSS_SELECTOR, "#btnNext")
        except Exception:
            return False

        intercept_only = (os.getenv("CTA_INTERCEPT_ONLY", "") or "").strip().lower() in {"1", "true", "yes", "on"}
        if intercept_only:
            label = _norm_lc(btn.get_attribute("value") or btn.text or "suivant")
            try:
                import Survey.input_handler
                Survey.input_handler.click_cta_strong_any_context(driver, label)
            except Exception:
                pass
            return True

        if not _click_best_effort(btn):
            return False
        return _wait_change(before_sig, before_url, timeout_s=8.0)

    if _handle_walr_intro_final_gate():
        return True

    # 1) Chercher le plus grand overlay CMP visible
    #    IMPORTANT: on ignore les containers cachés (ex: CookieYes avec .cky-hide)
    def _has_hidden_ancestor(el) -> bool:
        """Vérifie si un élément est dans un container caché (CookieYes, etc.)."""
        try:
            return bool(driver.execute_script(
                "return !!arguments[0].closest('.cky-hide, .ng-hide, [hidden], .hidden')",
                el
            ))
        except Exception:
            return False
    best = None
    for sel in CMP_CONTAINER_SELECTORS:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                try:
                    if not el.is_displayed():
                        continue
                    # NOUVEAU: vérifier si l'élément est dans un container caché
                    if _has_hidden_ancestor(el):
                        continue
                    r = el.rect or {}
                    area = float(r.get('width', 0) or 0) * float(r.get('height', 0) or 0)
                    if area <= 0:
                        continue
                    if (best is None) or (area > best[0]):
                        best = (area, el)
                except Exception:
                    continue
        except Exception:
            continue

    # 2) Si overlay trouvé, cliquer un bouton Accept/Agree ÃƒÂ  l'intérieur
    if best is not None:
        _, container = best
        try:
            cands = container.find_elements(By.CSS_SELECTOR, "button, a, [role='button'], input[type='submit'], input[type='button']")
        except Exception:
            cands = []

        def _score(el) -> int:
            try:
                t = _norm_lc(el.text or el.get_attribute('value') or el.get_attribute('innerText') or "")
            except Exception:
                t = ""
            if not t:
                return -1
            if any(w in t for w in REJECT_WORDS):
                return -1
            for i, w in enumerate(ACCEPT_WORDS):
                if w in t:
                    return 100 - i
            return -1

        cands = sorted(cands, key=_score, reverse=True)
        if cands and _score(cands[0]) >= 0:
            btn = cands[0]
            # En mode CTA_INTERCEPT_ONLY=1 (tests/non-régression), on force un clic via input_handler
            # pour que le CTA passe par cta_handler et soit intercepté.
            intercept_only = (os.getenv("CTA_INTERCEPT_ONLY", "") or "").strip().lower() in {"1", "true", "yes", "on"}
            try:
                if intercept_only:
                    label = _norm_lc(btn.text or btn.get_attribute('value') or btn.get_attribute('innerText') or "")
                    if label:
                        Survey.input_handler.click_cta_strong_any_context(driver, label)
                    else:
                        btn.click()
                else:
                    btn.click()
            except Exception:
                try:
                    if intercept_only:
                        label = _norm_lc(btn.text or btn.get_attribute('value') or btn.get_attribute('innerText') or "")
                        if label:
                            Survey.input_handler.click_cta_strong_any_context(driver, label)
                        else:
                            driver.execute_script("arguments[0].click();", btn)
                    else:
                        driver.execute_script("arguments[0].click();", btn)
                except Exception:
                    pass

            if _wait_change(before_sig, before_url):
                return True

    # 3) Fallback: tenter un clic accept/agree global (iframe-safe), mais toujours avec validation
    try:
        import Survey.input_handler as input_handler
        for needle in ("tout accepter", "accepter", "j'accepte", "accept all", "accept", "agree", "ok"):
            try:
                if input_handler.click_cta_strong_any_context(driver, needle):
                    if _wait_change(before_sig, before_url):
                        return True
            except Exception:
                continue
    except Exception:
        pass

    # 4) Confirmit/Forsta : bouton "Suivant" souvent SANS texte (icône) -> .cf-navigation-next
    # Exemple DOM: <button class="cf-navigation__button cf-navigation-next"><img title="Suivant"></button>
    try:
        next_buttons = driver.find_elements(
        By.CSS_SELECTOR,
        "#navButtons button.cf-navigation-next, button.cf-navigation-next, .cf-navigation__button.cf-navigation-next",
        )
        for nb in next_buttons:
            try:
                if not nb.is_displayed():
                    continue
                if _click_best_effort(nb):
                    if _wait_change(before_sig, before_url, timeout_s=8.0):
                        return True
            except Exception:
                continue
    except Exception:
        pass
    return False



def _extract_drag_drop_target_value(instruction_text: str) -> Optional[str]:
    text = _norm(instruction_text or "")
    if not text:
        return None

    keyed = re.search(r"(?:num[eé]ro|nombre|valeur)\s*[:=\-]?\s*(\d+)", text, flags=re.IGNORECASE)
    if keyed:
        return keyed.group(1)

    any_num = re.search(r"\b(\d+)\b", text)
    return any_num.group(1) if any_num else None


def handle_drag_drop_logic(driver):
    def _el_text(el) -> str:
        for getter in (
            lambda: el.text,
            lambda: el.get_attribute("innerText"),
            lambda: el.get_attribute("textContent"),
        ):
            try:
                txt = _norm(getter() or "")
                if txt:
                    return txt
            except Exception:
                continue
        return ""

    def _is_enabled(btn) -> bool:
        if not btn:
            return False
        try:
            if not btn.is_displayed():
                return False
        except Exception:
            pass
        attrs = []
        for k in ("disabled", "aria-disabled", "class"):
            try:
                attrs.append((btn.get_attribute(k) or "").strip().lower())
            except Exception:
                attrs.append("")
        disabled_attr, aria_disabled, cls = attrs
        if disabled_attr and disabled_attr not in ("false", "0"):
            return False
        if aria_disabled in ("true", "1"):
            return False
        if "disabled" in cls:
            return False
        return True

    def _attempt_cta_once() -> bool:
        intercept_only = (os.getenv("CTA_INTERCEPT_ONLY", "") or "").strip().lower() in {"1", "true", "yes", "on"}
        cta_found = False
        try:
            candidates = driver.find_elements(By.CSS_SELECTOR, "button[aria-label='Go to next question']")
            cta_found = any(_is_enabled(btn) for btn in candidates)
        except Exception:
            cta_found = False

        clicked = False
        try:
            clicked = bool(Survey.input_handler.try_click_navigation_cta_any_context(driver))
        except Exception:
            clicked = False

        if intercept_only:
            print(
                "[DRAGDROP][CTA] "
                f"cta_found={str(cta_found).lower()} "
                f"clicked={str(clicked).lower()} "
                f"intercept_ok={str(clicked).lower()}"
            )
        else:
            print(
                "[DRAGDROP][CTA] "
                f"cta_found={str(cta_found).lower()} "
                f"clicked={str(clicked).lower()}"
            )
        return clicked

    title_candidates = driver.find_elements(
        By.CSS_SELECTOR,
        "p.question-title[psquestiontitle], p.question-title, [psquestiontitle]",
    )
    instruction = ""
    for title in title_candidates:
        txt = _el_text(title)
        if any(v in txt.lower() for v in ("deposer", "déposer", "glisser", "drag", "drop")):
            instruction = txt
            break
    if not instruction and title_candidates:
        instruction = _el_text(title_candidates[0])

    target_value = _extract_drag_drop_target_value(instruction)
    print(f'[DRAGDROP] target_value={target_value} extracted_from="{instruction}"')
    if not target_value:
        return False

    draggables = driver.find_elements(By.CSS_SELECTOR, "[cdkdrag], .cdk-drag, [draggable='true']")
    try:
        drop_zone = driver.find_element(
            By.CSS_SELECTOR,
            "#dropZoneList.cdk-drop-list.drop-zone, #dropZoneList.drop-zone, #dropZoneList",
        )
        print("[DRAGDROP] drop_zone_selected id=dropZoneList ok=true")
    except Exception:
        print("[DRAGDROP] drop_zone_selected id=dropZoneList ok=false")
        return False
    if not draggables or not drop_zone:
        return False

    source = None
    source_selector = ""
    for drag in draggables:
        try:
            imgs = drag.find_elements(By.CSS_SELECTOR, f'img[alt="{target_value}"]')
            if imgs:
                source = drag
                source_selector = f'img[alt="{target_value}"]'
                break
        except Exception:
            continue

    if source is None:
        for drag in draggables:
            try:
                imgs = drag.find_elements(By.CSS_SELECTOR, "img")
            except Exception:
                imgs = []
            for img in imgs:
                try:
                    src = (img.get_attribute("src") or "")
                except Exception:
                    src = ""
                if f'/{target_value}.png' in src or target_value in src:
                    source = drag
                    source_selector = f'img[src*="{target_value}"]'
                    break
            if source is not None:
                break

    if source is None:
        for drag in draggables:
            txt = _el_text(drag)
            if target_value in txt:
                source = drag
                source_selector = "draggable_text"
                break

    if source is None:
        return False

    print(f"[DRAGDROP] source_found selector={source_selector}")

    next_buttons = driver.find_elements(By.CSS_SELECTOR, "button[aria-label='Go to next question']")
    next_button = next_buttons[0] if next_buttons else None

    offsets = [(0, 0), (15, 0)]
    can_use_cdp = hasattr(driver, "execute_cdp_cmd")
    is_local_env = (os.getenv("RUN_ENV", "local") or "local").strip().lower() == "local"
    for idx, (ox, oy) in enumerate(offsets, start=1):
        print(f"[DRAGDROP] attempt={idx} start")
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", source)
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", drop_zone)
            points = driver.execute_script(
                """
                const src = arguments[0];
                const dst = arguments[1];
                const ox = arguments[2] || 0;
                const oy = arguments[3] || 0;
                if (!src || !dst) return null;
                const srcRect = src.getBoundingClientRect();
                const dstRect = dst.getBoundingClientRect();
                const endX = Math.floor(dstRect.left + dstRect.width / 2 + ox);
                const endY = Math.floor(dstRect.top + dstRect.height / 2 + oy);
                const atPoint = document.elementFromPoint(endX, endY);
                const insideDropZone = !!(atPoint && (atPoint === dst || dst.contains(atPoint)));
                const insideDraggable = !!(atPoint && atPoint.closest('[cdkdrag], .cdk-drag, [draggable="true"]'));
                return {
                    startX: Math.floor(srcRect.left + srcRect.width / 2),
                    startY: Math.floor(srcRect.top + srcRect.height / 2),
                    endX,
                    endY,
                    verified: insideDropZone && !insideDraggable,
                    elementTag: atPoint ? atPoint.tagName.toLowerCase() : '',
                    elementId: atPoint && atPoint.id ? atPoint.id : '',
                    elementClass: atPoint && atPoint.className ? String(atPoint.className) : '',
                };
                """,
                source,
                drop_zone,
                ox,
                oy,
            )
            if not points:
                raise RuntimeError("drag_points_unavailable")

            element_desc = f"{points.get('elementTag', '')}#{points.get('elementId', '')}.{points.get('elementClass', '')}".strip(".")
            point_ok = bool(points.get("verified"))
            print(f"[DRAGDROP] end_point_verified ok={str(point_ok).lower()} elementFromPoint={element_desc}")
            if not point_ok:
                continue

            if can_use_cdp:
                start_x = int(points.get("startX", 0))
                start_y = int(points.get("startY", 0))
                end_x = int(points.get("endX", 0))
                end_y = int(points.get("endY", 0))

                driver.execute_cdp_cmd(
                    "Input.dispatchMouseEvent",
                    {"type": "mouseMoved", "x": start_x, "y": start_y, "button": "none"},
                )
                driver.execute_cdp_cmd(
                    "Input.dispatchMouseEvent",
                    {"type": "mousePressed", "x": start_x, "y": start_y, "button": "left", "clickCount": 1},
                )

                steps = 8
                for step in range(1, steps + 1):
                    x = int(start_x + ((end_x - start_x) * step) / steps)
                    y = int(start_y + ((end_y - start_y) * step) / steps)
                    driver.execute_cdp_cmd(
                        "Input.dispatchMouseEvent",
                        {"type": "mouseMoved", "x": x, "y": y, "button": "left", "buttons": 1},
                    )
                    time.sleep(0.02)

                driver.execute_cdp_cmd(
                    "Input.dispatchMouseEvent",
                    {"type": "mouseReleased", "x": end_x, "y": end_y, "button": "left", "clickCount": 1},
                )
                drag_done = True
            elif is_local_env:
                chain = ActionChains(driver).click_and_hold(source).move_to_element(drop_zone)
                if ox or oy:
                    chain = chain.move_by_offset(ox, oy)
                chain.release().perform()
                drag_done = True
            else:
                raise RuntimeError("cdp_unavailable_non_local")

            if not drag_done:
                raise RuntimeError("pointer_drag_failed")
            print(f"[DRAGDROP] attempt={idx} dropped")
        except Exception as e:
            print(f"[DRAGDROP] attempt={idx} dropped error={_short_exc(e)}")
            continue

        deadline = time.time() + 3.0
        enabled = False
        in_drop_zone = False
        while time.time() < deadline:
            try:
                in_drop_zone = bool(
                    driver.execute_script(
                        """
                        const dst = arguments[0];
                        if (!dst) return false;
                        const draggableInZone = dst.querySelector('[cdkdrag], .cdk-drag, [draggable="true"]');
                        const hasVisibleContent = (dst.innerText || '').trim().length > 0;
                        return !!(draggableInZone || hasVisibleContent);
                        """,
                        drop_zone,
                    )
                )
            except Exception:
                in_drop_zone = False
            if next_button is not None and _is_enabled(next_button):
                enabled = True
                break
            time.sleep(0.2)

        print(f"[DRAGDROP] attempt={idx} next_enabled={str(enabled).lower()} in_drop_zone={str(in_drop_zone).lower()}")
        if enabled or in_drop_zone:
            _attempt_cta_once()
            return True

    return False

def handle_start_screen(driver):
    """
    Start screen: accepter cookies si besoin, puis cliquer Start/Commencer.
    """
    import Survey.input_handler

    # 1) si un bandeau cookies bloque, on tente de le fermer (non bloquant)
    try:
        Survey.input_handler.click_cta_strong_any_context(driver, "accepter")
        Survey.input_handler.click_cta_strong_any_context(driver, "accept")
    except Exception:
        pass

    # 2) cliquer Start/Commencer (CTA nav le plus robuste)
    return (
        Survey.input_handler.try_click_navigation_cta_any_context(driver)
        or Survey.input_handler.click_button_by_text(driver, "commencer")
        or Survey.input_handler.click_button_by_text(driver, "démarrer")
        or Survey.input_handler.click_button_by_text(driver, "start")
        or Survey.input_handler.click_button_by_text(driver, "begin")
    )

def handle_end_screen(driver):
    return True  # on laisse la redirection se faire

def handle_captcha_guard(driver):
    """
    CAPTCHA / vérification humaine.

    Sécurité & prédictibilité:
    - Prod/Docker: on n'essaie pas de "résoudre" un CAPTCHA (arret controlé + snapshot si activé).
    - Local: on permet une résolution MANUELLE, puis on ATTEND la redirection / disparition du widget.
    """
    import os, sys, time

    def _env_truthy(name: str, default: str = "0") -> bool:
        v = (os.getenv(name, default) or "").strip().lower()
        return v in ("1", "true", "yes", "on")

    from config import should_pause_for_captcha, get_captcha_behavior, should_block_for_input
    captcha_behavior = get_captcha_behavior()

    # Snapshot best-effort (utile pour debug / nouveaux cas)
    try:
        if _env_truthy("SNAPSHOT_ON_GUARD", "0"):
            from Survey.page_snapshot import save_snapshot
            save_snapshot(driver, reason="captcha_guard", out_root=os.getenv("SURVEY_SNAPSHOT_DIR"))
    except Exception:
        pass

    # PROD/DOCKER: arret controlé (pas de bypass)
    if captcha_behavior == "restart":
        print("[GUARD] CAPTCHA détecté ; arret controlé (prod/docker)")
        return False

    # LOCAL: pause manuelle si terminal interactif
    print("[GUARD] CAPTCHA détecté ; résolution MANUELLE requise (local)")
    try:
        if should_block_for_input():
            input("[LOCAL][PAUSE] Résous le CAPTCHA dans le navigateur, puis appuie Entrée:\n")
    except KeyboardInterrupt:
        print("[LOCAL] Abandon demandé.")
        return False
    except Exception:
        pass

    # AprÃƒÂ¨s résolution: attendre que (1) l'URL change OU (2) le widget disparaisse
    try:
        before_url = driver.current_url
    except Exception:
        before_url = ""

    deadline = time.time() + float(os.getenv("CAPTCHA_WAIT_SEC", "25") or 25)

    while time.time() < deadline:
        try:
            cur_url = driver.current_url
        except Exception:
            cur_url = ""

        if cur_url and before_url and cur_url != before_url:
            print("[GUARD] CAPTCHA résolu ; URL changée")
            return True

        try:
            still_there = bool(driver.execute_script(r"""
                const isVisible = (e) => {
                  try{
                    const cs = getComputedStyle(e);
                    if (!cs) return false;
                    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') return false;
                    const r = e.getBoundingClientRect();
                    if (!r) return false;
                    if (r.bottom < 0 || r.right < 0) return false;
                    return (r.width > 2 && r.height > 2);
                  }catch(_){ return false; }
                };

                // 1) Slider puzzle (NIQ/GfK)
                const slider = document.querySelector('#sliderpanel');
                if (
                  slider && isVisible(slider) &&
                  slider.querySelector('.verify-img-panel, .verify-gap, .verify-bar-area, .verify-move-block, .verify-sub-block')
                ) return true;

                // 2) reCAPTCHA / hCaptcha visibles (ignorer 0x0 / 1x1)
                const widgetSels = [
                  "iframe[src*='recaptcha']",
                  "iframe[src*='captcha']",
                  "iframe[src*='hcaptcha']",
                  ".g-recaptcha",
                  ".h-captcha",
                  "#recaptcha",
                  "[data-sitekey]"
                ];
                for (const e of Array.from(document.querySelectorAll(widgetSels.join(",")))) {
                  if (!isVisible(e)) continue;
                  const r = e.getBoundingClientRect();
                  const tn = (e.tagName||"").toLowerCase();
                  if (tn === "iframe" || e.classList.contains("g-recaptcha") || e.classList.contains("h-captcha")) {
                    if (r.width >= 60 && r.height >= 40) return true;
                  } else {
                    if (r.width >= 60 && r.height >= 40) return true;
                  }
                }

                // 3) PureSpectrum CAPTCHA (ps-captcha-question)
                const psRoot = document.querySelector('ps-captcha-question') || document.querySelector('ps-captcha');
                if (psRoot && isVisible(psRoot)) {
                  const img = psRoot.querySelector("img[alt*='captcha' i], img[alt*='ps captcha' i]");
                  const inp = psRoot.querySelector("input[data-e2e='alpha-numeric-input'], ps-alpha-numeric-input input");
                  if (img && inp && isVisible(img) && isVisible(inp)) return true;
                }

                // 4) fallback: input id/name contient captcha (visible)
                for (const i of Array.from(document.querySelectorAll("input[id*='captcha' i], input[name*='captcha' i]"))) {
                  if (!isVisible(i)) continue;
                  const r = i.getBoundingClientRect();
                  if (r.width >= 10 && r.height >= 10) return true;
                }

                return false;
            """))
        except Exception:
            still_there = False

        if not still_there:
            print("[GUARD] CAPTCHA résolu - widget disparu")
            return True

        time.sleep(0.5)

    print("[GUARD] CAPTCHA: timeout d'attente après résolution manuelle (local)")
    return False

# ================================
# Anti double-fallback guard
# ================================

def _new_attempt_context(driver):
    """
    Initialise un contexte de tentative pour UNE instruction.
    Empeche toute stratégie d'etre exécutée 2 fois.
    """
    ctx = {
        "attempted": set(),
    }
    driver._action_attempt_ctx = ctx
    return ctx

def _try(driver, name: str, fn):
    """
    Exécute fn() UNE SEULE FOIS par nom de stratégie.
    """
    ctx = getattr(driver, "_action_attempt_ctx", None)
    if ctx is None:
        ctx = _new_attempt_context(driver)

    if name in ctx["attempted"]:
        return False

    ctx["attempted"].add(name)
    ok = bool(fn())
    if ok:
        log_info("[TARGET]", f"apply ok=true strategy={name} reason=applied")
    return ok

# --------------------------- Dispatcher principal ---------------------------
def _aa__norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def _aa__contains(hay: str, needle: str) -> bool:
    h = _aa__norm_ws(hay).lower()
    n = _aa__norm_ws(needle).lower()
    return bool(n) and (n in h)

def _aa__safe_scroll_center(driver, el) -> None:
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center', inline:'center'});",
            el
        )
    except Exception:
        pass

def _aa__safe_click(driver, el) -> bool:
    """
    Click robuste, sans retry infini.
    On scroll puis:
      1) click natif
      2) click ActionChains
      3) click JS
    """
    try:
        _aa__safe_scroll_center(driver, el)
    except Exception:
        pass

    try:
        el.click()
        return True
    except Exception:
        pass

    try:
        from selenium.webdriver.common.action_chains import ActionChains
        ActionChains(driver).move_to_element(el).click().perform()
        return True
    except Exception:
        pass

    try:
        driver.execute_script("arguments[0].click();", el)
        return True
    except Exception:
        return False

def _aa__is_mat_checked(node) -> bool:
    try:
        cls = (node.get_attribute("class") or "")
        return "mat-radio-checked" in cls
    except Exception:
        return False

def _aa__try_answer_matrix(driver, full_question: str, choice_text: str) -> bool:
    """
    Résout le cas Ask&Answer MATRIX Angular Material, en évitant les éléments cachés.
    Retourne True si l'action a bien été appliquée.
    """
    try:
        from selenium.webdriver.common.by import By
    except Exception:
        return False

    q = full_question or ""
    choice = _aa__norm_ws(choice_text)
    if not choice:
        return False

    # Garde-fou : on n'active ce helper que si on détecte app-matrix-question (Ask&Answer)
    try:
        if not driver.find_elements(By.XPATH, "//app-matrix-question"):
            return False
    except Exception:
        return False

    # Extraire le libellé de ligne (celebrity) depuis " ... Ã¢â‚¬â€ <statement>"
    statement = ""
    for sep in ("Ã¢â‚¬â€", " - ", " Ã¢â‚¬â€œ "):
        if sep in q:
            statement = q.split(sep, 1)[1]
            break
    statement = _aa__norm_ws(statement)
    statement_key = _aa__norm_ws(statement.split("(", 1)[0]) if statement else ""

    # ========== 1) Desktop table visible ==========
    try:
        tables = driver.find_elements(By.XPATH, "//app-matrix-question//table[contains(@class,'mat-table')]")
    except Exception:
        tables = []

    table = None
    for t in tables:
        try:
            if t.is_displayed():
                table = t
                break
        except Exception:
            continue

    if table is not None and statement_key:
        # Trouver la matrixId via le header (texte == choix)
        mid = None
        try:
            headers = table.find_elements(By.XPATH, ".//thead//th[contains(@class,'matrixId-')]")
        except Exception:
            headers = []

        for th in headers:
            try:
                if not th.is_displayed():
                    continue
                txt = _aa__norm_ws(th.text)
                if txt and _aa__contains(txt, choice):
                    cls = th.get_attribute("class") or ""
                    m = re.search(r"matrixId-(\d+)", cls)
                    if m:
                        mid = m.group(1)
                        break
            except Exception:
                continue

        if mid:
            # Trouver la ligne correspondant au statement (colonne "statement")
            try:
                rows = table.find_elements(By.XPATH, ".//tbody//tr")
            except Exception:
                rows = []

            for row in rows:
                try:
                    if not row.is_displayed():
                        continue
                    st_cell = row.find_element(By.XPATH, ".//td[contains(@class,'mat-column-statement')]")
                    st_txt = _aa__norm_ws(st_cell.text)
                    if not _aa__contains(st_txt, statement_key):
                        continue

                    # Cellule de la colonne mid
                    cell = row.find_element(By.XPATH, f".//td[contains(@class,'matrixId-{mid}')]")
                    # Cliquer la radio dans cette cellule (uniquement éléments visibles)
                    candidates = []
                    try:
                        candidates = cell.find_elements(
                            By.XPATH,
                            ".//mat-radio-button | .//label[contains(@class,'mat-radio-label')] | .//span[contains(@class,'mat-radio-container')] | .//input[@type='radio']"
                        )
                    except Exception:
                        candidates = []

                    for cand in candidates:
                        try:
                            if not cand.is_displayed():
                                continue
                        except Exception:
                            continue

                        _aa__safe_click(driver, cand)

                        # Vérif: mat-radio-button checked dans la cellule
                        try:
                            mr = cell.find_element(By.XPATH, ".//mat-radio-button[1]")
                            if _aa__is_mat_checked(mr):
                                return True
                        except Exception:
                            pass

                    return False
                except Exception:
                    continue

    # ========== 2) Mobile expansion visible ==========
    # (utile si la table est cachée par responsive)
    if statement_key:
        try:
            panels = driver.find_elements(By.XPATH, "//app-matrix-question//mat-expansion-panel")
        except Exception:
            panels = []

        for p in panels:
            try:
                if not p.is_displayed():
                    continue

                # header contient le statement
                hdr = p.find_element(By.XPATH, ".//mat-expansion-panel-header")
                hdr_txt = _aa__norm_ws(hdr.text)
                if not _aa__contains(hdr_txt, statement_key):
                    continue

                # ouvrir si nécessaire
                try:
                    expanded = (hdr.get_attribute("aria-expanded") or "").strip().lower() == "true"
                except Exception:
                    expanded = False
                if not expanded:
                    _aa__safe_click(driver, hdr)

                # choisir l'option par texte (labels visibles)
                opts = p.find_elements(
                    By.XPATH,
                    ".//mat-radio-button//label[contains(@class,'mat-radio-label')][.//span[contains(@class,'mat-radio-label-content')]]"
                )
                for lab in opts:
                    try:
                        if not lab.is_displayed():
                            continue
                        txt = _aa__norm_ws(lab.text)
                        if txt and _aa__contains(txt, choice):
                            _aa__safe_click(driver, lab)
                            # vérifier checked sur mat-radio-button parent
                            try:
                                mr = lab.find_element(By.XPATH, "ancestor::mat-radio-button[1]")
                                if _aa__is_mat_checked(mr):
                                    return True
                            except Exception:
                                pass
                            return False
                    except Exception:
                        continue
            except Exception:
                continue

    return False

def execute_action(driver, instruction: str) -> bool:
    """
    Applique une instruction OpenAI (batch + legacy).

    Formats supportés :
    - QID //// target_id //// valeur //// itype //// contexte
    - QID //// valeur //// itype //// contexte
    - target_id //// valeur //// itype //// contexte
    - valeur //// itype //// contexte
    """
    import Survey.input_handler
    import Survey.dom_context_mapper as dom_context_mapper
    import Survey.dropdown_block_resolver as dropdown_block_resolver
    from Survey.action_types import Action as ActionModel

    import os
    debug_target = is_debug()

    # Print 1 seule fois pour prouver que CE fichier est chargé
    if debug_target and not getattr(driver, "_target_debug_header_printed", False):
        log_debug("[TARGET_DEBUG]", f"action_dispatcher file={__file__}")
        driver._target_debug_header_printed = True

    if isinstance(instruction, ActionModel):
        instruction = instruction.to_dispatcher_line()

    log_info("[TARGET]", f"execute_action raw={instruction!r}")

    if not instruction or not instruction.strip():
        return False

    for raw in _split_multiline_instruction(instruction):
        parsed = _parse_action_line(raw)

        target_id = (parsed.get("target_id") or "").strip() or None
        value = (parsed.get("value") or "").strip()
        ctx = (parsed.get("context") or "").strip()

        raw_itype = (parsed.get("itype") or "").strip()
        _, itype, _ = _parse_typed_instruction3(f"x //// {raw_itype} //// y")
        itype = (itype or "").strip()
        raw_itype_norm = _norm_lc(raw_itype)
        matrix_like_itype = bool(raw_itype_norm and ("matrix" in raw_itype_norm or "grille" in raw_itype_norm))

        parsed_row, parsed_col = _parse_matrix_value_parts(value)
        matrix_row = parsed_row or ctx
        matrix_col = parsed_col or value

        matrix_by_target = False
        if target_id:
            try:
                p = get_target(target_id) or {}
                p_itype = (p.get("itype") or "").strip().lower()
                matrix_by_target = p_itype == "matrix"
            except Exception:
                matrix_by_target = False

        matrix_intent = matrix_like_itype or matrix_by_target

        log_info("[TARGET]", f"parsed target_id={target_id!r} itype={itype!r} value={value!r} context_len={len(ctx)}")

        if not value and not target_id:
            continue

        # Matrix: row + col obligatoires. Pas de clic aveugle.
        if matrix_intent:
            if not (matrix_row or "").strip():
                log_info("[MATRIX_ABORT]", "reason='missing_row'")
                return False
            if not (matrix_col or "").strip():
                log_info("[MATRIX_ABORT]", "reason='missing_col'")
                return False

            if _try_gridclick_matrix_set(driver, matrix_row, matrix_col):
                return True

            try:
                if dom_context_mapper.try_click_matrix_by_visual_mapping(
                    driver,
                    row_label=matrix_row,
                    col_label=matrix_col,
                    debug=True,
                ):
                    log_info("[TARGET]", "apply ok=true strategy=matrix_visual_map reason=applied")
                    return True
            except Exception:
                pass

            try:
                if Survey.input_handler._looks_like_matrix(driver) and Survey.input_handler.click_matrix_cell_by_row_and_col(
                    driver,
                    row_label=matrix_row,
                    col_label=matrix_col,
                ):
                    log_info("[TARGET]", "apply ok=true strategy=matrix_cell reason=applied")
                    return True
            except Exception:
                pass

        # 1) target_id => application directe via DOM_REGISTRY
        # IMPORTANT: sliderpoints (FocusVision/Decipher) ne doivent PAS passer par le chemin dropdown générique,
        # sinon on peut obtenir des faux positifs (dropdown ouvert) ou une valeur décalée (mapping 0/1-based).
        # On route donc explicitement vers set_sliderpoints.
        skip_apply_by_target_id = False
        if target_id:
            try:
                _p = get_target(target_id) or {}
                _m = _p.get("meta") or {}
                if (_m.get("source") or "").strip().lower() == "sq-sliderpoints":
                    skip_apply_by_target_id = True

                    # Support iframe: on se place dans frame_chain si présent (meme logique que _apply_by_target_id)
                    frame_chain = _p.get("frame_chain") or []
                    if not frame_chain:
                        try:
                            driver.switch_to.default_content()
                        except Exception:
                            pass
                    else:
                        try:
                            from Survey.frame_utils import switch_to_frame_chain  # type: ignore
                            switch_to_frame_chain(driver, frame_chain)
                        except Exception:
                            pass

                    # sliderpoints: une seule stratégie (DOM). Pas de fallback générique,
                    # sinon on peut écraser une sélection correcte (ex: valeur contenant une virgule).
                    ok_sp = Survey.input_handler.set_sliderpoints(driver, value, context_hint=ctx)
                    if ok_sp:
                        log_info("[TARGET]", "apply ok=true strategy=target_id_sliderpoints reason=applied")
                        return True
                    continue

            except Exception as e:
                # meme en exception: pas de fallback générique pour sliderpoints
                continue

        if target_id and not skip_apply_by_target_id:
            try:
                if _apply_by_target_id(driver, target_id, itype, value):
                    log_info("[TARGET]", "apply ok=true strategy=target_id reason=applied")
                    return True
            except Exception as e:
                if debug_target:
                    log_debug("[TARGET_DEBUG]", f"_apply_by_target_id exception: {type(e).__name__}: {e}")

        # 2) fallback legacy: label == valeur (IMPORTANT: pas QID)
        label = value

        _new_attempt_context(driver)

        # 2Ã¯Â¸ÂÃ¢Æ’Â£ SANITIZER
        try:
            safe_label = _sanitize_instruction_with_page_context(driver, label, itype or "")
            if safe_label != label:
                label = safe_label
        except Exception:
            pass

        # ==========================================================
        # Ã°Å¸Å¸Â¦ BUTTON
        # ==========================================================
        if itype == "button":

            if _try(driver, "btn_text", lambda:
                Survey.input_handler.click_button_by_text(driver, label)
            ):
                if _wait_for_button_effect(driver):
                    return True

            if _try(driver, "cta_strong", lambda:
                Survey.input_handler.click_cta_strong_any_context(
                    driver, label_hint=label, allow_generic=True
                )
            ):
                if _wait_for_button_effect(driver):
                    return True

            if _try(driver, "btn_fallback_radio", lambda:
                Survey.input_handler.click_radio_by_label(driver, label, context_hint=ctx)
            ):
                return True

            if _try(driver, "btn_fallback_checkbox", lambda:
                Survey.input_handler.click_checkbox_by_label(driver, label, context_hint=ctx)
            ):
                return True

        # ==========================================================
        # Ã°Å¸Å¸Â¦ DROPDOWN
        # ==========================================================
        if itype == "dropdown":

            # Guard: sliderpoints are rendered as <select> but behave like Likert sliders.
            # We forbid generic dropdown fallbacks here because they can return True without selecting a value.
            is_sliderpoints_target = False
            if target_id:
                try:
                    _p = get_target(target_id) or {}
                    _m = _p.get("meta") or {}
                    if (_m.get("source") or "").strip().lower() == "sq-sliderpoints":
                        is_sliderpoints_target = True
                except Exception:
                    pass

            if is_sliderpoints_target:
                if _try(driver, "dropdown_sliderpoints", lambda:
                    Survey.input_handler.set_sliderpoints(driver, label, context_hint=ctx)
                ):
                    return True
                # No other fallback for sliderpoints (avoid false positives).
                continue

            if ctx and _try(driver, "dropdown_block", lambda:
                dropdown_block_resolver.try_resolve_dropdown_block(
                    driver,
                    context_question=ctx,
                    value=label,
                    debug=True,
                )
            ):
                return True

            if _try(driver, "dropdown_sliderpoints", lambda:
                Survey.input_handler.set_sliderpoints(driver, label, context_hint=ctx)
            ):
                return True

            open_hint = (ctx or "").strip() or label
            _opened = _try(driver, "dropdown_open", lambda:
                Survey.input_handler.open_dropdown_generic(driver, hint=open_hint, context_hint=ctx)
            )
            if _opened:
                driver._last_dropdown_hint = open_hint

            field_hint = ctx or getattr(driver, "_last_dropdown_hint", None) or label
            if _try(driver, "dropdown_select", lambda:
                Survey.input_handler.select_option_with_hint(
                    driver, label, field_hint=field_hint, context_hint=ctx
                )
            ):
                driver._last_dropdown_hint = None
                return True

            # nettoyage: ne pas polluer l'action suivante
            driver._last_dropdown_hint = None

            field_hint = ctx or getattr(driver, "_last_dropdown_hint", None)
            if _try(driver, "dropdown_select", lambda:
                Survey.input_handler.select_option_with_hint(
                    driver, label, field_hint=field_hint, context_hint=ctx
                )
            ):
                driver._last_dropdown_hint = None
                return True

        # ==========================================================
        # Ã°Å¸Å¸Â¦ CHECKBOX
        # ==========================================================
        if itype == "checkbox":

            # Ã¢Å“â€¦ NEW: si OpenAI renvoie "Oui" pour une checkbox "statement", on clique le statement (ctx)
            if _norm_lc(label) in {"oui", "yes", "true", "1", "checked", "on", "x"} and ctx and len(ctx) >= 6:
                label = ctx

            if _try(driver, "checkbox_main", lambda:
                Survey.input_handler.click_checkbox_by_label(driver, label, context_hint=ctx)
            ):
                return True

            if _try(driver, "checkbox_buttonish", lambda:
                Survey.input_handler.click_checkbox_buttonish_by_label(driver, label, context_hint=ctx)
            ):
                return True

            if _try(driver, "checkbox_fallback_radio", lambda:
                Survey.input_handler.click_radio_by_label(driver, label, context_hint=ctx)
            ):
                return True

        # ==========================================================
        # Ã°Å¸Å¸Â¦ RADIO
        # ==========================================================
        if itype == "radio":

            # Ask&Answer (Angular Material MATRIX): éviter les éléments cachés (desktop/mobile) et cliquer la bonne cellule.
            # Ici, "ctx" contient le texte complet de la question (souvent avec "Ã¢â‚¬â€ <statement>"),
            # et "label" contient le choix (colonne) ÃƒÂ  sélectionner.
            question_text = ctx or ""
            answer_text = label or ""
            if question_text and answer_text and _aa__try_answer_matrix(driver, question_text, answer_text):
                log_info("[TARGET]", "apply ok=true strategy=aa_answer_matrix reason=applied")
                return True

            if _try(driver, "radio_slider", lambda:
                Survey.input_handler.set_sliderpoints(driver, label, context_hint=ctx)
            ):
                return True

            if _try(driver, "radio_main", lambda:
                Survey.input_handler.click_radio_by_label(driver, label, context_hint=ctx)
            ):
                return True

            if _try(driver, "radio_buttonish", lambda:
                Survey.input_handler.click_checkbox_buttonish_by_label(driver, label, context_hint=ctx)
            ):
                return True

        # ==========================================================
        # Ã°Å¸Å¸Â¦ TEXT / NUMBER
        # ==========================================================
        if itype in ("text", "number"):
            try:
                resolved = Survey.question_block_resolver.try_resolve_number_block(
                    driver,
                    context_question=ctx,
                    value=label,
                    min_score=0.75,
                    allow_overwrite=False,
                    debug=True,
                )
                if resolved:
                    log_info("[TARGET]", "apply ok=true strategy=number_block_resolver reason=applied")
                    return True
            except Exception:
                pass

            if _try(driver, "text_input", lambda:
                Survey.input_handler.fill_text_input(driver, label, context_hint=ctx)
            ):
                return True

        # si cette ligne échoue, on tente la suivante (au lieu de return False)
        log_info("[TARGET]", f"apply ok=false reason=no_strategy strategy=none itype={itype!r} target_id={target_id!r}")
        log_debug("[TARGET_DEBUG]", f"Aucune stratégie n'a abouti pour: {raw}")
        continue

    # --- Fallback vidéo (Video.js / Brightcove) ----------------------
    # V1: optionnel / best-effort. Si le module n'existe pas, on skip sans bruit.
    debug_video = (os.getenv("ACTION_DEBUG_VIDEO", "0") or "").strip().lower() in ("1", "true", "yes", "on")
    _video_utils = None
    try:
        from Survey import video_utils as _video_utils  # type: ignore
    except Exception:
        _video_utils = None

    if _video_utils and getattr(_video_utils, "try_watch_and_capture", None):
        try:
            if _video_utils.try_watch_and_capture(driver, api_key=None, max_seconds=35):
                try:
                    if Survey.input_handler.click_cta_strong_any_context(driver, text="Suivant"):
                        return True
                except Exception:
                    pass
                return True
        except Exception as _e:
            if debug_video:
                print(f"[VIDEO_DEBUG] video fallback error: {_short_exc(_e)}")

    return False

def reset_attempt_context(driver):
    """
    Reset du garde-fou anti double-fallback.
    Ãƒâ‚¬ appeler avant CHAQUE instruction, sinon une stratégie peut etre bloquée par un essai précédent.
    """
    try:
        _new_attempt_context(driver)
    except Exception:
        # fallback: on efface juste l'attribut
        try:
            if hasattr(driver, "_action_attempt_ctx"):
                delattr(driver, "_action_attempt_ctx")
        except Exception:
            pass

def execute_actions_plan(
    driver,
    actions: list[dict],
    *,
    stop_on_navigation: bool = True,
    rescan_between_actions: bool = True,
) -> bool:
    """
    Applique une série d'actions (issues du batch OpenAI).
    - reset attempt context avant chaque action
    - (NEW) rescan DOM entre actions si risque de re-render (évite xpaths obsolÃƒÂ¨tes)
    """
    success_any = False

    try:
        url_before = driver.current_url
    except Exception:
        url_before = ""

    # cap sécurité (évite un flood si OpenAI hallucine)
    # Par défaut on accepte plus que 25 pour couvrir les matrices longues (ex: 28 items).
    try:
        max_actions = int(os.getenv("MAX_ACTIONS_PER_PLAN", "60") or 60)
    except Exception:
        max_actions = 60
    if max_actions < 1:
        max_actions = 1

    actions = (actions or [])
    # IMPORTANT: si OpenAI a renvoyé un plan plus long que MAX_ACTIONS_PER_PLAN,
    # on ne tronque pas ici : le parser batch borne déja  (max_select / qid_constraints),
    # donc la taille reste controlée. Tronquer = “dernières questions jamais appliquées”.
    if actions and len(actions) > max_actions:
        print(f"[PLAN] MAX_ACTIONS_PER_PLAN={max_actions} < actions={len(actions)} -> pas de cap (plan déja  borné)")
        max_actions = len(actions)

    actions = actions[:max_actions]

    # Heuristique simple & prédictible:
    # - Un dropdown de langue peut reloader la page et annuler les réponses précédentes (ex: checkbox consent).
    # => si on détecte un "language selector", on l'applique AVANT le reste.
    def _is_language_dropdown(act: dict) -> bool:
        try:
            it = (act.get("itype") or "").strip().lower()
            if it != "dropdown":
                return False
            blob = " ".join([(act.get("context") or ""), (act.get("value") or "")]).lower()
            if any(k in blob for k in ("langue", "language", "idioma", "sprache", "lingua")):
                return True
            # valeurs fréquentes (au cas oÃƒÂ¹ le contexte est vide)
            if any(k in blob for k in ("franÃƒÂ§ais", "anglais", "english", "espaÃƒÂ±ol", "spanish", "deutsch", "german", "italiano", "portugu", "nederlands", "dutch")):
                return True
            return False
        except Exception:
            return False

    if any(_is_language_dropdown(a) for a in actions):
        actions = [a for _, a in sorted(list(enumerate(actions)), key=lambda t: (0 if _is_language_dropdown(t[1]) else 1, t[0]))]

    for idx, act in enumerate(actions):
        try:
            value = (act.get("value") or "").strip()
            itype = (act.get("itype") or "").strip()
            context = (act.get("context") or "").strip()

            matrix_row = (act.get("matrix_row_label") or "").strip()
            matrix_col = (act.get("matrix_col_label") or "").strip()
            if matrix_row:
                context = matrix_row
            if matrix_col:
                value = matrix_col

            if not value or not itype:
                continue

            reset_attempt_context(driver)

            tid = (act.get("target_id") or "").strip()
            qid = (act.get("qid") or "").strip()

            if tid and qid:
                instruction = f"{qid} //// {tid} //// {value} //// {itype} //// {context}"
            elif qid:
                instruction = f"{qid} //// {value} //// {itype} //// {context}"
            else:
                instruction = f"{value} //// {itype} //// {context}"
            # Si dropdown: peut déclencher un refresh/reload sans changer d'URL.
            # On attend la stabilisation avant de continuer, sinon on perd l'état (ex: checkbox décochée).
            before_sig = None
            try:
                if (itype or "").strip().lower() == "dropdown":
                    rs = ""
                    try:
                        rs = driver.execute_script("return document.readyState") or ""
                    except Exception:
                        rs = ""
                    try:
                        html_len = int(driver.execute_script("return document.documentElement.outerHTML.length") or 0)
                    except Exception:
                        html_len = 0
                    before_sig = f"{driver.current_url}|{rs}|{html_len}"
            except Exception:
                before_sig = None

            ok = execute_action(driver, instruction)
            if ok:
                success_any = True
            # Wait DOM stable after dropdown (budget borné)
            if ok and before_sig and (itype or "").strip().lower() == "dropdown":
                try:
                    import time
                    t0 = time.time()
                    last = None
                    stable_hits = 0
                    while time.time() - t0 < 10.0:
                        try:
                            rs = driver.execute_script("return document.readyState") or ""
                        except Exception:
                            rs = ""
                        try:
                            html_len = int(driver.execute_script("return document.documentElement.outerHTML.length") or 0)
                        except Exception:
                            html_len = 0
                        sig = f"{driver.current_url}|{rs}|{html_len}"

                        # on veut au minimum: readyState complete ET un DOM non trivial
                        if rs == "complete" and html_len > 500:
                            if sig == last:
                                stable_hits += 1
                            else:
                                stable_hits = 0
                                last = sig
                            if stable_hits >= 1:
                                break
                        time.sleep(0.2)
                except Exception:
                    pass

            if stop_on_navigation:
                try:
                    if driver.current_url != url_before:
                        break
                except Exception:
                    pass

            # (NEW) Entre deux actions, le DOM peut re-render (React/Qualtrics/etc.).
            #       On rebuild le registry pour que les target_id restent applicables.
            if ok and rescan_between_actions and idx < (len(actions) - 1):
                try:
                    if (itype or "").lower() in ("radio", "checkbox", "dropdown", "text", "number"):
                        import time
                        import Survey.dom_analyzer as dom_analyzer
                        time.sleep(0.2)  # laisse le framework appliquer l'état
                        dom_analyzer.analyze_dom(driver)  # clear+rebuild registry (target_id stable-ish)
                        # Ã°Å¸â€œË† micro-métrique: nombre de rescans DOM déclenchés sur la page courante
                        try:
                            driver._dom_rescans_this_page = int(getattr(driver, "_dom_rescans_this_page", 0)) + 1
                        except Exception:
                            pass

                except Exception:
                    pass

        except Exception as e:
            try:
                debug_target = is_debug()
                if debug_target:
                    log_debug("[TARGET_DEBUG]", f"execute_actions_plan idx={idx} crashed: {type(e).__name__}: {e}")
            except Exception:
                pass
            continue

    return success_any
