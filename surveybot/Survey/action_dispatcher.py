from __future__ import annotations
import re, unicodedata, time
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
import Survey.input_handler 
from selenium.webdriver.common.action_chains import ActionChains
import captcha.captcha_solver as captcha_solver
import captcha.recaptcha_utils as recaptcha_utils
import Survey.video_utils  # nouveau module

def _norm_txt_soft(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s, flags=re.S).strip().lower()
    # allège un peu pour les "contains"
    return re.sub(r"[\"'’“”«»:·•→,.;!?()\[\]]+", "", s)

def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    aw, bw = set(a.split()), set(b.split())
    if not aw or not bw:
        return 0.0
    return len(aw & bw) / len(aw | bw)

def _find_question_container(driver, qctx: str):
    """Retourne le conteneur le plus proche du contexte (fieldset/div question...)."""
    if not qctx:
        return None
    ctx = _norm_txt_soft(qctx)
    # candidats fréquents de conteneurs "question"
    candidates = []
    try:
        candidates += driver.find_elements(By.XPATH,
            "//*[self::fieldset or @role='group' or contains(@class,'question') or contains(@class,'Question') or contains(@data-test-id,'question')]")
    except Exception:
        pass
    best, best_score = None, -1e9
    for c in candidates:
        try:
            t = _norm_txt_soft(c.text or c.get_attribute("innerText") or "")
            score = _similarity(ctx, t)
            # petit bonus si le contexte est entièrement inclus
            if ctx and ctx in t:
                score += 0.25
            if score > best_score:
                best, best_score = c, score
        except Exception:
            continue
    return best if best_score > 0.10 else None  # seuil doux

def _click_choice_in_container(driver, container, label: str, kind: str) -> bool:
    """Clique une option 'radio' ou 'checkbox' portant 'label' à l'intérieur d'un conteneur."""
    if container is None:
        return False
    target = _norm_txt_soft(label)

    # Textes visibles courants pour radios/checkbox (libellés)
    paths = [
        ".//label",  # cas le plus standard
        ".//*[contains(@class,'radio') or contains(@class,'check') or contains(@data-test-id,'multiple_choice')]",
        ".//span",   # certains libellés sont dans span
    ]
    seen = set()
    for p in paths:
        try:
            for el in container.find_elements(By.XPATH, p):
                try:
                    tid = getattr(el, "_id", id(el))
                    if tid in seen:
                        continue
                    seen.add(tid)

                    txt = _norm_txt_soft(el.text or el.get_attribute("innerText") or "")
                    if not txt or not (target == txt or target in txt or txt in target):
                        continue

                    # Vérifie le type recherché dans la proximité (input type=…)
                    ok_type = False
                    try:
                        if el.tag_name.lower() == "label":
                            inp = el.find_element(By.XPATH, ".//input")
                            t = (inp.get_attribute("type") or "").lower()
                            ok_type = (kind == "radio" and t == "radio") or (kind == "checkbox" and t == "checkbox")
                        else:
                            inp = el.find_element(By.XPATH, ".//input[@type='radio' or @type='checkbox']")
                            t = (inp.get_attribute("type") or "").lower()
                            ok_type = (kind == "radio" and t == "radio") or (kind == "checkbox" and t == "checkbox")
                    except Exception:
                        # pas d'input direct dans le libellé → on tolère, on cliquera le label parent
                        ok_type = True

                    if not ok_type:
                        continue

                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                    time.sleep(0.15)
                    try:
                        el.click()
                    except Exception:
                        ActionChains(driver).move_to_element(el).click().perform()
                    print(f"✅ {kind.title()} « {label} » cliquée (scopé par contexte). source: action_dispatcher.py")
                    return True
                except Exception:
                    continue
        except Exception:
            continue
    return False

# ------------------------- Normalisation -------------------------
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

def _parse_typed_instruction(instr: str) -> tuple[str, str | None, str]:
    """
    Parse "libellé //// type //// contexte-question".
    - Le 'contexte' est optionnel ("" si absent).
    - Retourne (label, type_normalisé_ou_None, contexte).
    """
    if not instr:
        return "", None, ""
    parts = re.split(r"/{4,}", instr)
    label = _norm(parts[0])

    # type (2e partie)
    itype = None
    if len(parts) >= 2:
        raw_type = _norm_lc(parts[1])
        for t, aliases in _TYPE_ALIASES.items():
            if raw_type in aliases:
                itype = t
                break
        if itype is None:
            if re.search(r"drop|select|menu|combo", raw_type): itype = "dropdown"
            elif re.search(r"button|bouton|cta", raw_type):    itype = "button"
            elif re.search(r"check|coch", raw_type):           itype = "checkbox"
            elif re.search(r"radio|option", raw_type):         itype = "radio"
            elif re.search(r"textarea|long|open", raw_type):   itype = "textarea"
            elif re.search(r"text|champ|input", raw_type):     itype = "text"
            elif re.search(r"number|numér|numer", raw_type):   itype = "number"
            elif re.search(r"matrix|col", raw_type):           itype = "matrix-col"
            elif re.search(r"open|ouvrir", raw_type):          itype = "open"

    # contexte (3e partie)
    qctx = _norm(parts[2]) if len(parts) >= 3 else ""
    return label, itype, qctx

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


# ------------------- Détection du type d'intention -------------------
_CTA_WORDS = {
    "suivant",
    "continuer",
    "next",
    "continue",
    "start",
    "commencer",
    "valider",
    "envoyer",
    "submit",
    "terminer",
    "finish",
}
_OPEN_TOKENS = {
    "an",
    "annee",
    "année",
    "mois",
    "month",
    "year",
    "pays",
    "ville",
    "state",
    "province",
    "département",
    "department",
}
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


# --------------------------- Dispatcher principal ---------------------------

def execute_action(driver, instruction: str) -> bool:
    """
    Applique l'instruction. Supporte 'libellé //// type //// contexte-question'.
    """
    if not instruction or not instruction.strip():
        return False

    for raw in _split_multiline_instruction(instruction):
        label, itype, ctx = _parse_typed_instruction3(raw)
        low = _norm_lc(label)
        #print(f"🎯 Instruction parsée → label='{label}' | type='{itype}' | ctx='{ctx}', source: execute_action")

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


        # 🛡️ garde-fou anti-disqualification
        safe_label = _sanitize_instruction_with_page_context(driver, label, itype or "")
        if safe_label != label:
            print(f"🛡️ Instruction ajustée : « {label} » → « {safe_label} » (sanitizer)")
            label = safe_label
            low = _norm_lc(label)

        # 0) Types forcés
        if itype == "open":
            print("trying open")
            if Survey.input_handler.open_dropdown_generic(driver, hint=ctx or label):
                print(
                    f"🔽 Dropdown ouvert (hint « {ctx or label} »). source: action_dispatcher.py"
                )
                return True

        if itype == "dropdown":
            print("trying dropdown")
            lowlbl = _norm_lc(label)

            # sliderpoints avant la logique générique
            try:
                if Survey.input_handler.set_sliderpoints(driver, label, context_hint=ctx):
                    print(f"✅ Dropdown/sliderpoints « {label} ». source: action_dispatcher.py")
                    return True
            except Exception:
                pass

            # 1) Si ça ressemble à un NOM DE CHAMP → on ouvre
            if any(tok in lowlbl for tok in _OPEN_FIELD_TOKENS):
                if Survey.input_handler.open_dropdown_generic(driver, hint=label, context_hint=ctx):
                    # mémoriser le dernier champ ouvert pour la prochaine valeur
                    try:
                        driver._last_dropdown_hint = label
                    except Exception:
                        pass
                    print(
                        f"🔽 Dropdown ouvert (champ « {label} », ctx='{ctx}'). source: action_dispatcher.py"
                    )
                    return True

            # 2) Sinon, on considère que c’est une VALEUR → on sélectionne
            field_hint = ctx or getattr(driver, "_last_dropdown_hint", None)  
            if Survey.input_handler.try_select_option_any(driver, label, field_hint=field_hint, context_hint=ctx):
                try: driver._last_dropdown_hint = None
                except: pass
                print(f"✅ Dropdown: valeur « {label} » (ctx). source: action_dispatcher.py")
                return True

            if Survey.input_handler.open_dropdown_generic(driver, hint=label, context_hint=ctx):
                try: driver._last_dropdown_hint = label
                except: pass
                print(f"🔽 Dropdown ouvert (fallback, ctx) « {label} ». source: action_dispatcher.py")
                return True

        if itype == "button":
            print("trying button")
            # libellé CTA ?
            try:
                is_nav = Survey.input_handler._looks_like_nav_label(label or "") or Survey.input_handler._looks_like_nav_label(ctx or "")
            except Exception:
                is_nav = False

            if not is_nav:    
                if Survey.input_handler.click_button_by_text(driver, label):
                    print(f"✅ Bouton (texte) « {label} ». source: action_dispatcher.py")
                    return True
                # strict : cherche UNIQUEMENT un bouton portant exactement le hint, pas les 'suivant/next...'
                if Survey.input_handler.click_cta_strong_any_context(driver, label_hint=label, allow_generic=False):
                    print(f"✅ Bouton (strict via label_hint) « {label} ». source: action_dispatcher.py")
                    return True
            else:
                # 2) Si le libellé ressemble à un CTA, on autorise les hints génériques
                if Survey.input_handler.click_cta_strong_any_context(driver, label_hint=label or ctx, allow_generic=True):
                    print(f"✅ CTA « {label} ». source: action_dispatcher.py")
                    return True
                # puis texte en secours
                if Survey.input_handler.click_button_by_text(driver, label):
                    print(f"✅ Bouton (texte) « {label} ». source: action_dispatcher.py")
                    return True

            # 1.5) NEW — petit chevron d’ouverture d’un champ 'open-ended'
            if Survey.input_handler.ensure_open_ended_open(driver, context_hint=ctx, desired_state="open"):
                print("✅ Chevron open-ended ouvert. source: action_dispatcher.py")
                return True

            # 2) Fallbacks heuristiques : parfois un “bouton” est en fait une option
            print("↪️ Échec bouton : fallback → radio puis checkbox (heuristique).")
            if Survey.input_handler.click_radio_by_label(driver, label, context_hint=ctx):
                print(f"✅ Fallback-radio sur « {label} ». source: action_dispatcher.py")
                return True
            if Survey.input_handler.click_checkbox_by_label(driver, label, context_hint=ctx):
                print(f"✅ Fallback-checkbox sur « {label} ». source: action_dispatcher.py")
                return True

        if itype == "checkbox":
            print("trying checkbox")
            # 1) tentative principale
            if Survey.input_handler.click_checkbox_by_label(driver, label, context_hint=ctx):
                print(f"✅ Checkbox « {label} » ctx: {ctx}. source: action_dispatcher.py")
                return True

            # 2) NEW: checkbox 'button-like' (label role=button / ui-btn …)
            if Survey.input_handler.click_checkbox_buttonish_by_label(driver, label, context_hint=ctx):
                print(f"✅ Checkbox (button-like) « {label} ». source: action_dispatcher.py")
                return True

            # 3) fallbacks : certains “checkbox” sont rendus comme radios ou CTA
            print("↪️ Échec checkbox : fallback → radio puis bouton (CTA).")
            if Survey.input_handler.click_radio_by_label(driver, label, context_hint=ctx):
                print(f"✅ Fallback-radio sur « {label} ». source: action_dispatcher.py")
                return True
            if Survey.input_handler.click_button_by_text(driver, label):
                print(f"✅ Fallback-bouton (texte) « {label} ». source: action_dispatcher.py")
                return True
            # 2) bouton 'fort' mais SANS hints génériques (ne cherchera QUE label_hint)
            if Survey.input_handler.click_cta_strong_any_context(driver, label_hint=label, allow_generic=False):
                print(f"✅ Fallback-bouton (strict via label_hint) « {label} ». source: action_dispatcher.py")
                return True

        if itype == "captcha":
            print("trying captcha")
            subtype = ctx.get("captcha") or "recaptcha_v2"

            if subtype == "recaptcha_v2":
                try:
                    # 1) on récupère sitekey (depuis ctx OU auto-détection DOM)
                    sitekey = ctx.get("sitekey")
                    invisible = bool(ctx.get("invisible", False))
                    if not sitekey:
                        sitekey, auto_inv = recaptcha_utils.extract_recaptcha_v2_sitekey(driver)
                        if not sitekey:
                            print("[captcha] sitekey introuvable")
                            return False
                        # si 'invisible' pas précisé dans ctx, on prend l’auto
                        if "invisible" not in ctx:
                            invisible = bool(auto_inv)

                    # 2) URL courante (ou ctx["page_url"] si fourni)
                    page_url = ctx.get("page_url") or driver.current_url

                    # 3) Résolution 2Captcha
                    solver = captcha_solver.TwoCaptchaClient()
                    token = solver.solve_recaptcha_v2(
                        sitekey=sitekey,
                        url=page_url,
                        invisible=invisible
                    )

                    # 4) Injection du token et events
                    recaptcha_utils.inject_recaptcha_token(driver, token)

                    print("[captcha] reCAPTCHA v2 résolu et injecté")
                    return True

                except Exception as e:
                    print(f"[captcha] erreur: {e}")
                    return False

        if itype == "radio":
            print("trying radio")
            # certains sliders sont rédigés comme des "réponses uniques"
            try:
                if Survey.input_handler.set_sliderpoints(driver, label, context_hint=ctx):
                    print(f"✅ Radio/sliderpoints « {label} ». source: action_dispatcher.py")
                    return True
            except Exception:
                pass

            # 1) tentative principale
            if Survey.input_handler.click_radio_by_label(driver, label, context_hint=ctx):
                print(f"✅ Radio « {label} » ctx: {ctx}. source: action_dispatcher.py")
                return True

            # 2) NEW: checkbox 'button-like' (label role=button / ui-btn …)
            if Survey.input_handler.click_checkbox_buttonish_by_label(driver, label, context_hint=ctx):
                print(f"✅ Checkbox (button-like) « {label} ». source: action_dispatcher.py")
                return True
            
            # 3) fallbacks : des radios stylisées se comportent comme checkbox / CTA
            print("↪️ Échec radio : fallback → checkbox puis bouton (CTA).")
            if Survey.input_handler.click_checkbox_by_label(driver, label, context_hint=ctx):
                print(f"✅ Fallback-checkbox sur « {label} ». source: action_dispatcher.py")
                return True
            if Survey.input_handler.click_button_by_text(driver, label):
                print(f"✅ Fallback-bouton (texte) « {label} ». source: action_dispatcher.py")
                return True
            # 4) bouton 'fort' mais SANS hints génériques (ne cherchera QUE label_hint)
            if Survey.input_handler.click_cta_strong_any_context(driver, label_hint=label, allow_generic=False):
                print(f"✅ Fallback-bouton (strict via label_hint) « {label} ». source: action_dispatcher.py")
                return True
                
        if itype == "text":
            print("trying text input")
            if Survey.input_handler.fill_text_input(driver, label, context_hint=ctx):
                print(f"✅ Texte saisi « {label} » ctx: {ctx}. source: action_dispatcher.py")
                return True

        CTA_WORDS = {
            "suivant",
            "continuer",
            "next",
            "continue",
            "start",
            "commencer",
            "valider",
            "envoyer",
            "submit",
            "terminer",
            "finish",
        }
        if low not in CTA_WORDS:
            print(f"ℹ️ Instruction non-CTA, on continue. source: action_dispatcher.py")
            if Survey.input_handler.fill_text_input(driver, label):
                return True

        if Survey.input_handler._has_native_selects(driver):
            if Survey.input_handler.try_select_option_any(driver, label, field_hint=ctx, context_hint=ctx):
                print(f"✅ Radio→Dropdown fallback: « {label} ».")
                return True
            
        if Survey.input_handler.click_cta_strong_any_context(driver,text=label if 'text' in Survey.input_handler.click_cta_strong_any_context.__code__.co_varnames else label):
            return True

        print(f"ℹ️ Sous-instruction ignorée « {raw} », on continue.")

    print(
        "❌ Aucune stratégie n’a abouti pour :",
        instruction,
        " source: action_dispatcher.py",
    )
    
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
