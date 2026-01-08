from __future__ import annotations
import re, unicodedata
from selenium.webdriver.common.by import By
import captcha.captcha_solver as captcha_solver
import captcha.recaptcha_utils as recaptcha_utils

def _norm(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s).replace("\xa0", " ").strip()
    return re.sub(r"\s+", " ", s)

def _norm_lc(s: str) -> str:
    return _norm(s).lower()

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
    Applique l'instruction. Supporte 'libellé //// type //// contexte-question'.
    """
    import Survey.input_handler 
    import Survey.dom_context_mapper as dom_context_mapper
    import Survey.dropdown_block_resolver as dropdown_block_resolver

    if not instruction or not instruction.strip():
        return False

    for raw in _split_multiline_instruction(instruction):
        label, itype, ctx = _parse_typed_instruction3(raw)
        low = _norm_lc(label)
        #print(f"🎯 Instruction parsée → label='{label}' | type='{itype}' | ctx='{ctx}', source: execute_action")

        # ============================================================
        # NEW — DOM Context Mapper (tableau visuel, DOM éclaté)
        #   - traite les “fausses matrices” où le DOM sépare contextes et inputs
        #   - doit passer AVANT _looks_like_matrix() (sinon on rate ces cas)
        # ============================================================
        try:
            if (ctx or "").strip() and itype in ("checkbox", "radio"):
                if dom_context_mapper.try_click_matrix_by_visual_mapping(
                    driver,
                    row_label=ctx,
                    col_label=label,
                    debug=True,
                ):
                    print(
                        f"✅ DOMMAP (visuel): cellule validée row='{ctx}' col='{label}'. source: action_dispatcher.py"
                    )
                    return True
        except Exception as e:
            print(f"[DOMMAP] exception ignorée (fallback): {e}")


        # 🎯 Cas matrice: si on a un contexte (ligne) + une intention checkbox/radio,
        # essaie la cellule (row × column) AVANT les handlers génériques.
        try:
            cond_matrix = bool((ctx or "").strip()) and (itype in ("checkbox", "radio") and Survey.input_handler._looks_like_matrix(driver))
            print(f"[DBG] matrix_cond itype={itype!r} ctx_empty={not bool((ctx or '').strip())} -> {cond_matrix}")
            if cond_matrix:
                print("condition matrice")
                if Survey.input_handler.click_matrix_cell_by_row_and_col(driver, row_label=ctx, col_label=label):
                    print(f"✅ Matrice (cellule) validée: row='{ctx}' col='{label}'. source: action_dispatcher.py")
                    return True
        except Exception as e:
            print(f"[DBG] matrix_try_exception: {e}")
            pass

        # ================================
        # Dispatcher principal sécurisé
        # ================================

        _new_attempt_context(driver)

        # 1️⃣ MATRICES (PRIORITÉ ABSOLUE)
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

        # 2️⃣ SANITIZER ANTI-DISQUALIFICATION
        safe_label = _sanitize_instruction_with_page_context(driver, label, itype or "")
        if safe_label != label:
            label = safe_label

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
            print("trying text input")
            try:
                resolved = Survey.question_block_resolver.try_resolve_number_block(
                    driver,
                    context_question=ctx,   # TEXTE de la question OpenAI / DOM
                    value=label,                     # valeur à injecter (ex: "28")
                    min_score=0.75,                   # seuil robuste (pas agressif)
                    allow_overwrite=False,            # JAMAIS écraser un champ déjà rempli
                    debug=True,                       # logs utiles en prod
                )

                if resolved:
                    print("[ACTION][QBR] Champ numérique résolu via QuestionBlockResolver")
                    return True   # ⚠️ STOP ici : on ne continue PAS la logique générique

            except :
                pass
            if _try(driver, "text_input", lambda:
                Survey.input_handler.fill_text_input(driver, label, context_hint=ctx)
            ):
                return True

        # ==========================================================
        # ❌ AUCUNE STRATÉGIE N’A ABOUTI
        # ==========================================================
        print(
            "❌ Aucune stratégie n’a abouti pour :",
            instruction,
            " source: action_dispatcher.py",
        )
        return False
    
    # --- Fallback vidéo (Video.js / Brightcove) ----------------------
    try:
        # Si une vidéo est présente, lire et capturer l'audio.
        if Survey.video_utils.try_watch_and_capture(driver, api_key=None, max_seconds=35):
            # Après lecture, certains surveys activent le bouton Suivant :
            try:
                if Survey.input_handler.click_cta_strong_any_context(driver, text="Suivant"):
                    return True
            except Exception:
                pass
            return True  # action faite (lecture + capture)
    except Exception as _e:
        print(f"[video] fallback error: {_e}")

    return False
