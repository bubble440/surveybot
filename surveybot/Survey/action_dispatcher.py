from __future__ import annotations
import re, unicodedata, os, time, zlib
from selenium.webdriver.common.by import By
import Survey.input_handler
from Survey.dom_registry import get_target
from typing import Optional
from selenium.webdriver.common.action_chains import ActionChains

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
        # radios souvent non-interactables (hidden) → JS events
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
            # Les inputs sont dans le même bloc question (souvent dans une QA-view cachée)
            qroot = widget.find_element(By.XPATH, "ancestor::*[contains(@class,'question')][1]")
            inputs = qroot.find_elements(By.CSS_SELECTOR, f"input[type='radio'][id^='ans{uid}.{col}.']")
            if not inputs:
                # fallback global (au cas où la structure varie)
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
    - clique une bucket "safe" pour chaque carte visible, jusqu'à completion ou max_cards
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

        return " — ".join([p for p in parts if p])

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
            "il y a 4 à 7 jours",
            "il y a 1 à 2 semaines",
            "il y a 2 à 3 semaines",
            "il y a 3 à 4 semaines",
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
    - NFKD + suppression des diacritiques (Île -> Ile)
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
    Construit un literal XPath safe, même si la chaîne contient des quotes.
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

            debug_target = (os.getenv("ACTION_DEBUG_TARGET", "0") or "").strip().lower() in ("1", "true", "yes", "on")

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

                # 1) affiché + taille > 2px (évite label 0x0)
                for c in cands:
                    try:
                        if c.is_displayed() and _rect_ok(c):
                            return c
                    except Exception:
                        continue

                # 2) affiché
                for c in cands:
                    try:
                        if c.is_displayed():
                            return c
                    except Exception:
                        continue

                # 3) fallback
                return cands[0]

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

            # --- cas "options map" (radio/checkbox)
            # IMPORTANT: on n'exige pas kind=="group" pour éviter le couplage à la classification (ex: matrix_rows_single_choice)
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
                        print(f"[TARGET_DEBUG] target_id='{target_id}' kind='{kind}' itype='{resolved_itype}' value='{value}' -> option introuvable (opt_map={len(opt_map)})")
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
                    try:
                        driver.execute_script(
                            """
                            const inp = arguments[0];
                            if (!inp) return;
                            inp.checked = true;
                            inp.dispatchEvent(new Event('input',  {bubbles:true}));
                            inp.dispatchEvent(new Event('change', {bubbles:true}));
                            inp.dispatchEvent(new MouseEvent('click', {bubbles:true}));
                            """,
                            inp,
                        )
                    except Exception:
                        pass

                def _cdp_click(el) -> bool:
                    # Click "réel" via CDP (plus proche d'un user click)
                    try:
                        if not hasattr(driver, "execute_cdp_cmd"):
                            if debug_target:
                                print("[TARGET_DEBUG] CDP click unavailable: driver has no execute_cdp_cmd()")
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
                            print(f"[TARGET_DEBUG] CDP click failed: {e}")
                        return False


                def _click_candidate(node, label: str) -> bool:
                    # 1) click webdriver standard
                    try:
                        node.click()
                        return True
                    except Exception as e:
                        if debug_target:
                            print(f"[TARGET_DEBUG] native click failed on {label}: {e}")

                    # 2) ActionChains (souvent plus robuste quand le DOM est “capricieux”)
                    try:
                        from selenium.webdriver.common.action_chains import ActionChains
                        ActionChains(driver).move_to_element(node).pause(0.05).click().perform()
                        return True
                    except Exception as e:
                        if debug_target:
                            print(f"[TARGET_DEBUG] actionchains click failed on {label}: {e}")

                    # 3) CDP click (trusted-ish)
                    if _cdp_click(node):
                        return True

                    # 4) JS click (dernier recours, parfois ignoré si anti-bot)
                    try:
                        driver.execute_script("arguments[0].click();", node)
                        return True
                    except Exception as e:
                        if debug_target:
                            print(f"[TARGET_DEBUG] js click failed on {label}: {e}")
                        return False

                # 1) trouver l'élément cible (label/span/input)
                try:
                    el = _find_best_visible(xp)
                    if not el:
                        if debug_target:
                            print(f"[TARGET_DEBUG] element not found for xpath: {xp}")
                        return False
                except Exception as ex:
                    if debug_target:
                        print(f"[TARGET_DEBUG] element not found for xpath={xp} ({type(ex).__name__}: {ex})")
                    return False

                # 2) clic “normal” sur la cible
                _click_candidate(el, "target")

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

                inp = _first_input_under(el)
                if not inp:
                    if _wait_selected_like(el, timeout_s=1.0):
                        return True

                # NEW: si la cible est un <label for="...">, forcer l'input associé
                try:
                    if (el.tag_name or "").lower() == "label":
                        fid = (el.get_attribute("for") or "").strip()
                        if fid:
                            inp_for = driver.find_element(By.ID, fid)
                            _dispatch_check_events(inp_for)
                except Exception:
                    pass

                inp = _first_input_under(el)
                if _is_selected(inp):
                    return True

                # 3) si on a cliqué un label non interactif (pointer-events, overlay), tenter le span
                try:
                    sp = el.find_element(By.XPATH, ".//span[1]")
                    _click_candidate(sp, "span")
                except Exception:
                    pass

                inp = inp or _first_input_under(el)
                if _is_selected(inp):
                    return True

                # 4) tenter un clic direct sur l'input (même si masqué) via JS
                if inp:
                    _click_candidate(inp, "input")
                    if _is_selected(inp):
                        return True

                    # 5) dernier recours DOM-only: forcer checked + events (ce qui active souvent le bouton Continue)
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

                if debug_target:
                    print(f"[TARGET_DEBUG] selection failed after waits: value='{value}' xpath='{xp}' inp_id='{inp_id}' inp_name='{inp_name}'")
                return False

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
                # + cas spécial "sq-sliderpoints" : cliquer / dragger la piste pour que l'UI se mette à jour
                if resolved_itype == "dropdown":
                    try:
                        sel = driver.find_element(By.XPATH, xp)
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", sel)
                    except Exception:
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
                        return False

                    best_val = None
                    best_idx = None
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
                        return False

                    # 1) source de vérité: <select>
                    try:
                        driver.execute_script(
                            """
                            const sel = arguments[0];
                            const val = arguments[1];
                            sel.value = val;
                            try { sel.dispatchEvent(new Event('input',  {bubbles:true})); } catch(e) {}
                            try { sel.dispatchEvent(new Event('change', {bubbles:true})); } catch(e) {}
                            """,
                            sel,
                            best_val,
                        )
                    except Exception:
                        return False

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
                                        return False
                                except Exception:
                                    pass

                                return True
                    except Exception:
                        # si le forcing UI échoue, on considère quand même le select comme set
                        pass

                    return True
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
    "checkbox": {"checkbox", "case", "case à cocher", "check", "cocher"},
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
    - Tolère des '>' accidentels après <type>.
    """
    parts = re.split(r"/{4,}", instr or "")
    label = _norm(parts[0]) if parts else ""
    raw_type = _norm_lc(parts[1]) if len(parts) > 1 else ""
    context = _norm(parts[2]) if len(parts) > 2 else ""

    # nettoyer un éventuel '>' parasite après type
    raw_type = raw_type.replace(">", "").strip()

    itype = None
    if raw_type:
        # réutilise la table d’alias existante
        for t, aliases in _TYPE_ALIASES.items():
            if raw_type in aliases:
                itype = t
                break
        if itype is None:
            # heuristiques déjà présentes dans _parse_typed_instruction
            if re.search(r"drop|select|menu|combo", raw_type): itype = "dropdown"
            elif re.search(r"button|bouton|cta", raw_type): itype = "button"
            elif re.search(r"check|coch", raw_type): itype = "checkbox"
            elif re.search(r"radio|option", raw_type): itype = "radio"
            elif re.search(r"text|champ|input", raw_type): itype = "text"
    return label, itype, context

# ---------- Sanitize instruction : corrige une option à risque ----------
def _get_visible_options(driver):
    # Récupère un set de libellés d’options visibles (radios/checkbox)
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

    # Listes de mots à risque / sûrs
    risky = {
        "non",
        "jamais",
        "certainement pas",
        "aucun",
        "aucune",
        "je préfère ne pas le dire",
        "preferer ne pas",
        "none",
        "no",
        "never",
    }
    safe_pos = ["oui", "souvent", "parfois", "régulièrement", "hebdomadaire", "mensuel"]
    safe_neutral = ["je ne sais pas", "je ne m'en souviens pas", "neutre"]

    # Exception emploi/secteurs → privilégier "Aucune de ces réponses" si présente
    if is_sector_question:
        for o in opts:
            if "aucune de ces réponses" in o or "aucun de ces choix" in o:
                return "Aucune de ces réponses"

    # Si la proposition est manifestement risquée → préférer une alternative
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

    # rien à corriger
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
            line = re.sub(r"^[\-\•\–\—\·]+\s*", "", line)  # - ...
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
# À placer AVANT la logique générique itype == "button"

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

        # 1️⃣ URL changée
        if driver.current_url != start_url:
            return True

        # 2️⃣ Bouton disparu ou disabled
        try:
            btn = driver.find_element(By.ID, "acceptAndTakeSurveyLink2")
            if not btn.is_displayed():
                return True
            if btn.get_attribute("aria-disabled") == "true":
                return True
            if "disabled" in (btn.get_attribute("class") or ""):
                return True
        except Exception:
            # bouton plus présent → effet OK
            return True

        # 3️⃣ Overlay / spinner
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

    # 1) Chercher le plus grand overlay CMP visible
    best = None
    for sel in CMP_CONTAINER_SELECTORS:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                try:
                    if not el.is_displayed():
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

    # 2) Si overlay trouvé, cliquer un bouton Accept/Agree à l'intérieur
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
            try:
                btn.click()
            except Exception:
                try:
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
        or Survey.input_handler.click_button_by_text(driver, "start")
        or Survey.input_handler.click_button_by_text(driver, "begin")
    )

def handle_end_screen(driver):
    return True  # on laisse la redirection se faire

def handle_captcha_guard(driver):
    print("[GUARD] CAPTCHA détecté → arrêt survey")
    return False

# ================================
# Anti double-fallback guard
# ================================

def _new_attempt_context(driver):
    """
    Initialise un contexte de tentative pour UNE instruction.
    Empêche toute stratégie d'être exécutée 2 fois.
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
    return fn()

# --------------------------- Dispatcher principal ---------------------------

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
    debug_target = (os.getenv("ACTION_DEBUG_TARGET", "0") or "").strip().lower() in ("1", "true", "yes", "on")

    # Print 1 seule fois pour prouver que CE fichier est chargé
    if debug_target and not getattr(driver, "_target_debug_header_printed", False):
        print(f"[TARGET_DEBUG] action_dispatcher file={__file__}")
        driver._target_debug_header_printed = True

    if isinstance(instruction, ActionModel):
        instruction = instruction.to_dispatcher_line()

    if debug_target:
        print(f"[TARGET_DEBUG] execute_action raw={instruction!r}")

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

        if not value and not target_id:
            continue

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

                    # Support iframe: on se place dans frame_chain si présent (même logique que _apply_by_target_id)
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
                        return True
                    continue

            except Exception as e:
                # même en exception: pas de fallback générique pour sliderpoints
                continue

        if target_id and not skip_apply_by_target_id:
            try:
                if _apply_by_target_id(driver, target_id, itype, value):
                    return True
            except Exception as e:
                if debug_target:
                    print(f"[TARGET_DEBUG] _apply_by_target_id exception: {type(e).__name__}: {e}")

        # 2) fallback legacy: label == valeur (IMPORTANT: pas QID)
        label = value

        _new_attempt_context(driver)

        # 1️⃣ MATRICES
        try:
            if (ctx or "").strip() and itype in ("checkbox", "radio"):
                if dom_context_mapper.try_click_matrix_by_visual_mapping(
                    driver,
                    row_label=ctx,
                    col_label=label,
                    debug=True,
                ):
                    return True
        except Exception:
            pass

        try:
            if (ctx or "").strip() and itype in ("checkbox", "radio") and Survey.input_handler._looks_like_matrix(driver):
                if Survey.input_handler.click_matrix_cell_by_row_and_col(driver, row_label=ctx, col_label=label):
                    return True
        except Exception:
            pass

        # 2️⃣ SANITIZER
        try:
            safe_label = _sanitize_instruction_with_page_context(driver, label, itype or "")
            if safe_label != label:
                label = safe_label
        except Exception:
            pass

        # ==========================================================
        # 🟦 BUTTON
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
        # 🟦 DROPDOWN
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

            if _try(driver, "dropdown_open", lambda:
                Survey.input_handler.open_dropdown_generic(driver, hint=label, context_hint=ctx)
            ):
                driver._last_dropdown_hint = label
                return True

            field_hint = ctx or getattr(driver, "_last_dropdown_hint", None)
            if _try(driver, "dropdown_select", lambda:
                Survey.input_handler.try_select_option_any(
                    driver, label, field_hint=field_hint, context_hint=ctx
                )
            ):
                driver._last_dropdown_hint = None
                return True

        # ==========================================================
        # 🟦 CHECKBOX
        # ==========================================================
        if itype == "checkbox":

            # ✅ NEW: si OpenAI renvoie "Oui" pour une checkbox "statement", on clique le statement (ctx)
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
        # 🟦 RADIO
        # ==========================================================
        if itype == "radio":

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
        # 🟦 TEXT / NUMBER
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
                    return True
            except Exception:
                pass

            if _try(driver, "text_input", lambda:
                Survey.input_handler.fill_text_input(driver, label, context_hint=ctx)
            ):
                return True

        if debug_target:
            print(f"[TARGET_DEBUG] parsed target_id={target_id!r} itype={itype!r} value={value!r} context={ctx!r}")

        # si cette ligne échoue, on tente la suivante (au lieu de return False)
        print("❌ Aucune stratégie n’a abouti pour :", raw, " source: action_dispatcher.py")
        continue

    # --- Fallback vidéo (Video.js / Brightcove) ----------------------
    try:
        if Survey.video_utils.try_watch_and_capture(driver, api_key=None, max_seconds=35):
            try:
                if Survey.input_handler.click_cta_strong_any_context(driver, text="Suivant"):
                    return True
            except Exception:
                pass
            return True
    except Exception as _e:
        print(f"[video] fallback error: {_e}")

    return False

def reset_attempt_context(driver):
    """
    Reset du garde-fou anti double-fallback.
    À appeler avant CHAQUE instruction, sinon une stratégie peut être bloquée par un essai précédent.
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
    - (NEW) rescan DOM entre actions si risque de re-render (évite xpaths obsolètes)
    """
    success_any = False

    try:
        url_before = driver.current_url
    except Exception:
        url_before = ""

    # cap sécurité (évite un flood si OpenAI hallucine)
    actions = (actions or [])[:25]

    for idx, act in enumerate(actions):
        try:
            value = (act.get("value") or "").strip()
            itype = (act.get("itype") or "").strip()
            context = (act.get("context") or "").strip()

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
            ok = execute_action(driver, instruction)
            if ok:
                success_any = True

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
                        # 📈 micro-métrique: nombre de rescans DOM déclenchés sur la page courante
                        try:
                            driver._dom_rescans_this_page = int(getattr(driver, "_dom_rescans_this_page", 0)) + 1
                        except Exception:
                            pass

                except Exception:
                    pass

        except Exception as e:
            try:
                import os
                debug_target = (os.getenv("ACTION_DEBUG_TARGET", "0") or "").strip().lower() in ("1", "true", "yes", "on")
                if debug_target:
                    print(f"[TARGET_DEBUG] execute_actions_plan idx={idx} crashed: {type(e).__name__}: {e}")
            except Exception:
                pass
            continue

    return success_any
