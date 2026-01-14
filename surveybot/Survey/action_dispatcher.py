from __future__ import annotations
import re, unicodedata
from selenium.webdriver.common.by import By
import captcha.captcha_solver as captcha_solver
import captcha.recaptcha_utils as recaptcha_utils
import Survey.input_handler
from Survey.dom_registry import get_target

def _norm(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s).replace("\xa0", " ").strip()
    return re.sub(r"\s+", " ", s)

def _norm_lc(s: str) -> str:
    return _norm(s).lower()

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
    """
    try:
        payload = get_target(target_id)
        if not payload:
            return False

        kind = payload.get("kind")
        reg_itype = (payload.get("itype") or "").lower()
        itype = (itype or reg_itype).lower().strip()

        v_norm = _norm_lc(value)

        # --- cas group (radio/checkbox)
        if kind == "group" and itype in ("radio", "checkbox"):
            opt_map = payload.get("option_xpath_map") or {}
            xp = opt_map.get(v_norm)

            if not xp and v_norm:
                for k, x in opt_map.items():
                    if not k:
                        continue
                    if v_norm == k or v_norm in k or k in v_norm:
                        xp = x
                        break

            if not xp:
                # ✅ NEW: si une seule checkbox dans le groupe et valeur "oui/true", on clique la seule option
                if itype == "checkbox" and len(opt_map) == 1:
                    if v_norm in {"oui", "yes", "true", "1", "checked", "on", "x"} or not v_norm:
                        xp = next(iter(opt_map.values()))

            if not xp:
                return False

            try:
                el = driver.find_element(By.XPATH, xp)
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                el.click()
                return True
            except Exception:
                try:
                    el = driver.find_element(By.XPATH, xp)
                    driver.execute_script("arguments[0].click();", el)
                    return True
                except Exception:
                    return False

        # --- cas single (text/textarea/dropdown/button)
        if kind == "single":
            xp = payload.get("xpath")
            if not xp:
                return False

            if itype in ("text", "textarea", "number"):
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

            if itype == "dropdown":
                try:
                    sel = driver.find_element(By.XPATH, xp)
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", sel)
                    sel.click()
                except Exception:
                    return False

                v = (value or "").strip()
                if not v:
                    return False

                lit = _xpath_literal(v)
                xps = [
                    f"{xp}//option[normalize-space(.)={lit}]",
                    f"{xp}//option[contains(normalize-space(.), {lit})]",
                ]
                for oxp in xps:
                    try:
                        opt = driver.find_element(By.XPATH, oxp)
                        opt.click()
                        return True
                    except Exception:
                        continue
                return False

            if itype == "button":
                try:
                    el = driver.find_element(By.XPATH, xp)
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                    el.click()
                    return True
                except Exception:
                    return False

        return False
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
    return (
        Survey.input_handler.click_button_by_text(driver, "accepter")
        or Survey.input_handler.click_button_by_text(driver, "accept")
        or Survey.input_handler.click_button_by_text(driver, "continue")
    )

def handle_start_screen(driver):
    return (
        Survey.input_handler.click_button_by_text(driver, "commencer")
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
    import Survey.action_types as Action

    if isinstance(instruction, Action):
        instruction = instruction.to_dispatcher_line()

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
        if target_id:
            try:
                if _apply_by_target_id(driver, target_id, itype, value):
                    return True
            except Exception:
                pass

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

        except Exception:
            continue

    return success_any
