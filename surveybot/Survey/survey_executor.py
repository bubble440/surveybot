from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
import re, openai, time, unicodedata, os, sys, hashlib, tempfile
from urllib.parse import urlsplit

def _short_url(url: str) -> str:
    try:
        p = urlsplit(url or "")
        return f"{p.scheme}://{p.netloc}" if (p.scheme and p.netloc) else (url or "<unknown>")
    except Exception:
        return url or "<unknown>"

def _norm_lc(s: str) -> str:
    s = unicodedata.normalize("NFKC", (s or "")).lower().strip()
    return re.sub(r"\s+", " ", s)

def _env_truthy(name: str, default: str = "0") -> bool:
    v = (os.getenv(name, default) or "").strip().lower()
    return v in ("1", "true", "yes", "on")

def _local_pause_before_cta(reason: str = "") -> None:
    """
    LOCAL ONLY: attend que l'utilisateur appuie sur  avant de cliquer un CTA.
     prod/docker: ne bloque jamais si stdin non-interactif.
    Active uniquement si LOCAL_CTA_REQUIRE_ENTER=1.
    
    En mode LOCAL_UNATTENDED, cette fonction retourne .
    """
    try:
        from config import should_block_for_input
        # En mode unattended ou prod, pas de pause
        if not should_block_for_input():
            return
        if not _env_truthy("LOCAL_CTA_REQUIRE_ENTER", "0"):
            return

        msg = "[LOCAL][PAUSE] Appuie sur  pour autoriser le clic CTA"
        if reason:
            msg += f" ({reason})"
        print(msg, flush=True)
        try:
            input()
        except KeyboardInterrupt:
            raise
    except Exception:
        return

def _is_visible_js(driver, el) -> bool:
    """
    Fallback JavaScript pour  la  d'un .
     quand Selenium.is_displayed() retourne False sur des structures DOM
    complexes (tables  AreYouNet, etc.) alors que l' est visible.
    """
    try:
        return driver.execute_script("""
            var el = arguments[0];
            if (!el) return false;
            var style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden') return false;
            var rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
        """, el)
    except Exception:
        return False


def _detect_rate_rank_image_eval_dom(driver) -> tuple[bool, str]:
    """
    Détecte un pattern DOM de type "image/product evaluation" (rate & rank)
    qui doit déclencher un abandon DOM-only (sans stratégie alternative).
    """
    try:
        dom = driver.execute_script(
            """
            const txt = (el) => ((el && (el.innerText || el.textContent)) || '').trim();
            const isVisible = (el) => {
              if (!el) return false;
              const s = window.getComputedStyle(el);
              if (!s || s.display === 'none' || s.visibility === 'hidden') return false;
              const r = el.getBoundingClientRect();
              return !!(r && r.width > 0 && r.height > 0);
            };
            const candidates = Array.from(document.querySelectorAll('button, [role="button"], .mat-fab'));
            const buttonTexts = candidates.filter(isVisible).map(txt).filter(Boolean).slice(0, 30);
            return {
              button_texts: buttonTexts,
              has_product_page: !!document.querySelector('app-product-page, [data-total-items]'),
              has_rate_rank_hint: /rate\s*and\s*rank/i.test(document.body?.innerText || ''),
              has_visual_media_hint: !!document.querySelector('app-game-item-media, .zoom-gallery, .MagicZoom, img[src*="imageviewer"], img[src*="firstinsight"]'),
            };
            """
        ) or {}
    except Exception:
        dom = {}

    button_texts = [_norm_lc(t) for t in (dom.get("button_texts") or []) if t]
    has_like = any(t == "aime" or " like" in f" {t}" for t in button_texts)
    has_dislike = any(
        ("n'aime pas" in t)
        or ("n aime pas" in t)
        or ("aime pas" in t)
        or ("dislike" in t)
        for t in button_texts
    )

    if (
        bool(dom.get("has_product_page"))
        and bool(dom.get("has_visual_media_hint"))
        and (bool(dom.get("has_rate_rank_hint")) or (has_like and has_dislike))
        and has_like
        and has_dislike
    ):
        return True, "rate_rank_image_eval_pair_buttons"

    return False, ""


def _detect_image_only_unresolvable_dom(driver, question_blocks: list[dict]) -> tuple[bool, str, str]:
    """
    Détecte un écran avec choix visuels (images/icônes) non résolus par l'analyse DOM.
    Critères explicites:
      1) le DOM expose un groupe radio/checkbox visible avec >=2 options image-only,
      2) l'extraction ne contient aucun bloc radio/checkbox exploitable (>=2 options).
    """
    has_exploitable_choice_block = False
    for b in question_blocks or []:
        it = _norm_lc((b.get("itype") or ""))
        if it not in {"radio", "checkbox"}:
            continue
        options = [
            _norm_lc(str(o))
            for o in (b.get("options") or [])
            if _norm_lc(str(o))
        ]
        if len(set(options)) >= 2:
            has_exploitable_choice_block = True
            break

    if has_exploitable_choice_block:
        return False, "", ""

    try:
        dom = driver.execute_script(
            """
            const isVisible = (el) => {
              if (!el) return false;
              const s = window.getComputedStyle(el);
              if (!s || s.display === 'none' || s.visibility === 'hidden') return false;
              const r = el.getBoundingClientRect();
              return !!(r && r.width > 0 && r.height > 0);
            };
            const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
            const shortText = (s, maxLen = 24) => {
              const t = norm(s);
              return t.length <= maxLen ? t : '';
            };
            const hasQuestionContainer = !!document.querySelector('[questionname], .questionContainer, .mrQuestionTable, .question-component');
            const findStableContainer = (el) => {
              if (!el) return null;
              return (
                el.closest('.mrQuestionTable')
                || el.closest('[questionname]')
                || el.closest('[data-test="main-contain"]')
                || el.closest('.__flexgrid_row')
                || el.closest('.question-component')
                || el.closest('.questionContainer')
                || el.parentElement
              );
            };
            const wrapperForInput = (inp) => {
              if (!inp) return null;
              if (inp.id) {
                const explicit = document.querySelector(`label[for="${CSS.escape(inp.id)}"]`);
                if (explicit) return explicit;
              }
              return (
                inp.closest('label')
                || inp.closest('[tabindex], [role="radio"], [role="checkbox"], button, a')
              );
            };
            const wrapperMeta = (wrapper) => {
              const txt = norm((wrapper && (wrapper.innerText || wrapper.textContent)) || '');
              const hasImageNode = !!(wrapper && wrapper.querySelector('img, svg'));
              const style = wrapper ? window.getComputedStyle(wrapper) : null;
              const hasBackgroundImage = !!(style && style.backgroundImage && style.backgroundImage !== 'none');
              return {
                text: txt,
                hasImageNode,
                hasBackgroundImage,
                isImageOnly: (hasImageNode || hasBackgroundImage) && txt.length <= 2,
              };
            };
            const inputs = Array.from(document.querySelectorAll("input[type='radio'][name], input[type='checkbox'][name]"));
            const groups = new Map();
            let visibleWrapperCount = 0;

            for (const inp of inputs) {
              const wrapper = wrapperForInput(inp);
              if (!isVisible(wrapper)) continue;
              visibleWrapperCount += 1;
              const type = (inp.type || '').toLowerCase();
              const container = findStableContainer(wrapper || inp);
              const groupName = norm(inp.name || '');
              const containerSig = container
                ? `${container.tagName}|${container.id || ''}|${container.className || ''}|${container.getAttribute('questionname') || ''}|${container.getAttribute('data-test') || ''}`
                : '';
              const key = groupName ? `${type}::name::${groupName}` : `${type}::container::${containerSig || (inp.id || '')}`;
              if (!groups.has(key)) groups.set(key, []);
              const meta = wrapperMeta(wrapper);
              const imgHint = norm((wrapper && (wrapper.querySelector('img')?.getAttribute('src') || wrapper.querySelector('img')?.getAttribute('alt'))) || '');

              groups.get(key).push({
                wrapperVisible: true,
                isImageOnly: meta.isImageOnly,
                wrapperText: shortText(meta.text),
                hasImageNode: meta.hasImageNode,
                hasBackgroundImage: meta.hasBackgroundImage,
                imgHint,
                containerSig,
              });
            }

            const imageOnly = [];
            let imageOnlyOptionCount = 0;
            for (const [groupKey, opts] of groups.entries()) {
              if (!opts || opts.length < 2) continue;
              const imageOnlyCount = opts.filter(o => o.isImageOnly).length;
              if (imageOnlyCount >= 2) {
                imageOnlyOptionCount += imageOnlyCount;
                imageOnly.push({
                  groupKey,
                  optionCount: opts.length,
                  imageOnlyCount,
                  imgHints: opts.map(o => o.imgHint).filter(Boolean).slice(0, 6),
                });
              }
            }

            const clickableCandidates = Array.from(document.querySelectorAll(
              'button, [role="button"], [role="radio"], [role="checkbox"], a, span[tabindex], div[tabindex], div[onclick]'
            ));
            const clickableByContainer = new Map();
            let clickableVisibleCount = 0;
            for (const el of clickableCandidates) {
              if (!isVisible(el)) continue;
              clickableVisibleCount += 1;
              const meta = wrapperMeta(el);
              const text = shortText(meta.text);
              const isVisualOption = meta.isImageOnly;
              if (!isVisualOption) continue;

              const container = findStableContainer(el);
              if (!container) continue;

              const key =
                container.getAttribute('data-test')
                || container.id
                || container.getAttribute('questionname')
                || container.className
                || container.tagName;
              if (!clickableByContainer.has(key)) clickableByContainer.set(key, []);
              clickableByContainer.get(key).push({
                hasImageNode: meta.hasImageNode,
                hasBackgroundImage: meta.hasBackgroundImage,
                text,
                containerSig: `${container.tagName}|${container.id || ''}|${container.className || ''}|${container.getAttribute('questionname') || ''}`,
              });
            }

            const clickableImageOnlyGroups = [];
            let clickableImageOnlyOptionCount = 0;
            for (const [containerKey, opts] of clickableByContainer.entries()) {
              if (!opts || opts.length < 2) continue;
              const textless = opts.filter(o => !o.text).length;
              if (textless < 2) continue;
              clickableImageOnlyOptionCount += textless;
              const sig = (opts[0] && opts[0].containerSig) || containerKey;
              clickableImageOnlyGroups.push({
                containerKey,
                optionCount: opts.length,
                textlessCount: textless,
                hasImageNodeCount: opts.filter(o => o.hasImageNode).length,
                hasBackgroundImageCount: opts.filter(o => o.hasBackgroundImage).length,
                containerSig: sig,
              });
            }

            const bodyText = norm((document.body && document.body.innerText) || '');
            const hasQuestionHint = /etes[-\s]*vous|quel\s+age|quel\s+âge|how\s+old|are\s+you/i.test(bodyText);
            const questionHints = (arguments[0] || []).map(q => norm(q)).filter(Boolean);
            const hasQuestionTextHint = questionHints.some(q => q.length >= 8 && bodyText.includes(q.slice(0, 48)));

            return {
              image_only_groups: imageOnly,
              clickable_image_only_groups: clickableImageOnlyGroups,
              has_question_hint: hasQuestionHint || hasQuestionTextHint || hasQuestionContainer,
              clickable_visible_count: clickableVisibleCount,
              input_count: inputs.length,
              visible_wrapper_count: visibleWrapperCount,
              input_group_count: groups.size,
              image_only_option_count: imageOnlyOptionCount,
              clickable_group_count: clickableImageOnlyGroups.length,
              clickable_image_only_option_count: clickableImageOnlyOptionCount,
              project: norm(document.querySelector("input[name='I.Project']")?.value || ''),
            };
            """
            , [
                _norm_lc((b.get("question") or ""))
                for b in (question_blocks or [])
                if _norm_lc((b.get("question") or ""))
            ]
        ) or {}
    except Exception:
        dom = {}

    image_groups = dom.get("image_only_groups") or []
    clickable_groups = dom.get("clickable_image_only_groups") or []

    has_question_hint = bool(dom.get("has_question_hint"))
    if clickable_groups and not has_question_hint:
        print("[DOM_ONLY_ABORT] detector_no_match source=clickable_icons reason=missing_question_hint")
        clickable_groups = []

    if not image_groups and not clickable_groups:
        print(
            "[DOM_ONLY_ABORT] detector_no_match "
            f"inputs={int(dom.get('input_count') or 0)} visible_wrappers={int(dom.get('visible_wrapper_count') or 0)} "
            f"input_groups={int(dom.get('input_group_count') or 0)} image_groups={len(image_groups)} "
            f"image_options={int(dom.get('image_only_option_count') or 0)} clickable_groups={len(clickable_groups)} "
            f"clickable_image_options={int(dom.get('clickable_image_only_option_count') or 0)} "
            f"clickable_visible={int(dom.get('clickable_visible_count') or 0)}"
        )
        return False, "", ""

    clickable_groups_norm = []
    for g in clickable_groups:
        sig = (g.get("containerSig") or g.get("containerKey") or "")
        gk_hash = hashlib.sha1(sig.encode("utf-8", errors="ignore")).hexdigest()[:12]
        clickable_groups_norm.append(
            {
                "groupKey": f"clickable_icons::{gk_hash}",
                "optionCount": int(g.get("optionCount") or 0),
                "textlessCount": int(g.get("textlessCount") or 0),
                "hasImageNodeCount": int(g.get("hasImageNodeCount") or 0),
                "hasBackgroundImageCount": int(g.get("hasBackgroundImageCount") or 0),
            }
        )

    image_groups_all = list(image_groups) + clickable_groups_norm
    pattern_reason = "image_only_inputs"
    if clickable_groups_norm:
        pattern_reason = "image_only_clickable_options"
    elif image_groups:
        pattern_reason = "image_only_wrapped_inputs"

    print(
        "[DOM_ONLY_ABORT] detector_match "
        f"reason={pattern_reason} inputs={int(dom.get('input_count') or 0)} "
        f"visible_wrappers={int(dom.get('visible_wrapper_count') or 0)} "
        f"input_groups={int(dom.get('input_group_count') or 0)} "
        f"image_groups={len(image_groups)} image_options={int(dom.get('image_only_option_count') or 0)} "
        f"clickable_groups={len(clickable_groups_norm)} "
        f"clickable_image_options={int(dom.get('clickable_image_only_option_count') or 0)}"
    )

    fp_payload = {
        "project": dom.get("project") or "",
        "groups": [
            {
                "group": (g.get("groupKey") or ""),
                "count": int(g.get("optionCount") or 0),
                "hints": g.get("imgHints") or [],
            }
            for g in image_groups_all
        ],
    }
    fingerprint = hashlib.sha1(repr(fp_payload).encode("utf-8", errors="ignore")).hexdigest()[:12]
    return True, pattern_reason, fingerprint


def _budgeted_dom_only_abort_for_image_eval(driver) -> str:
    """
    Retourne:
      - "restarted" si pattern détecté + soft_restart demandé,
      - "budget_exhausted" si pattern détecté mais budget dépassé,
      - "no_match" sinon.
    """
    is_match, pattern_reason = _detect_rate_rank_image_eval_dom(driver)
    if not is_match:
        return "no_match"

    try:
        current_url = driver.current_url or ""
    except Exception:
        current_url = ""
    up = urlsplit(current_url)
    budget_key = f"{up.scheme}://{up.netloc}{up.path}"

    try:
        counters = getattr(driver, "_dom_only_abort_seen", None)
        if not isinstance(counters, dict):
            counters = {}
        current = int(counters.get(budget_key, 0) or 0)
        max_hits = 1
        if current >= max_hits:
            print(
                f"[DOM_ONLY_ABORT] image_eval_detected budget_exhausted key={budget_key} "
                f"hits={current}/{max_hits} -> abort_without_vision"
            )
            driver._dom_only_abort_seen = counters
            return "budget_exhausted"
        counters[budget_key] = current + 1
        driver._dom_only_abort_seen = counters
    except Exception:
        pass

    reason = f"dom_only_abort_image_eval:{pattern_reason}"
    print(
        f"[DOM_ONLY_ABORT] image_eval_detected -> soft_restart(reason={reason}) key={budget_key}"
    )
    try:
        import Management.guards.runtime_guard as runtime_guard
        runtime_guard.get_guard().request_survey_restart(reason)
    except Exception as e:
        print(f"[DOM_ONLY_ABORT][WARN] soft_restart request failed: {type(e).__name__}: {e}")
    return "restarted"


def _budgeted_soft_restart_for_image_only_inputs(driver, question_blocks: list[dict]) -> str:
    """
    Retourne:
      - "restarted" si image-only non résoluble + soft_restart demandé,
      - "budget_exhausted" si budget anti-boucle dépassé,
      - "no_match" sinon.
    """
    is_match, pattern_reason, dom_fp = _detect_image_only_unresolvable_dom(driver, question_blocks)
    if not is_match:
        return "no_match"

    try:
        current_url = driver.current_url or ""
    except Exception:
        current_url = ""
    up = urlsplit(current_url)
    budget_key = f"{up.scheme}://{up.netloc}{up.path}#{dom_fp}"

    try:
        counters = getattr(driver, "_dom_only_abort_seen", None)
        if not isinstance(counters, dict):
            counters = {}
        current = int(counters.get(budget_key, 0) or 0)
        max_hits = 2
        if current >= max_hits:
            print(
                f"[DOM_ONLY_ABORT] {pattern_reason} budget_exhausted key={budget_key} "
                f"hits={current}/{max_hits} needs_browser_reason={pattern_reason}"
            )
            driver._dom_only_abort_seen = counters
            return "budget_exhausted"
        counters[budget_key] = current + 1
        driver._dom_only_abort_seen = counters
    except Exception:
        pass

    reason = f"dom_only_abort:{pattern_reason}"
    print(
        f"[DOM_ONLY_ABORT] {pattern_reason} -> soft_restart(reason={reason}) key={budget_key}"
    )
    try:
        import Management.guards.runtime_guard as runtime_guard
        runtime_guard.get_guard().request_survey_restart(reason)
    except Exception as e:
        print(f"[DOM_ONLY_ABORT][WARN] soft_restart request failed: {type(e).__name__}: {e}")
    return "restarted"


def _handle_forcewatch_video_gate(driver) -> str:
    """
    Détection/traitement DOM-only d'un écran vidéo avec gate forcewatch.

    Retourne:
      - "resolved": question vidéo répondue via DOM,
      - "soft_restart": gate vidéo détecté sans action exploitable,
      - "no_match": aucun gate vidéo de ce type détecté.
    """
    try:
        has_forcewatch_video = bool(driver.execute_script(
            """
            const isVisible = (el) => {
              if (!el) return false;
              const s = window.getComputedStyle(el);
              if (!s || s.display === 'none' || s.visibility === 'hidden') return false;
              const r = el.getBoundingClientRect();
              return !!(r && r.width > 0 && r.height > 0);
            };
            const videos = Array.from(document.querySelectorAll('video[data-forcewatch]'));
            return videos.some(v => isVisible(v));
            """
        ))
    except Exception:
        has_forcewatch_video = False

    if not has_forcewatch_video:
        return "no_match"

    try:
        radios = [
            el for el in driver.find_elements(By.CSS_SELECTOR, "input[type='radio'][name]")
            if el.is_enabled() and el.is_displayed()
        ]
    except Exception:
        radios = []

    groups: dict[str, list] = {}
    for r in radios:
        name = (r.get_attribute("name") or "").strip()
        if not name:
            continue
        groups.setdefault(name, []).append(r)

    if not groups:
        print("[VIDEO_GATE] video_gate detected -> soft_restart")
        try:
            import Management.guards.runtime_guard as runtime_guard
            runtime_guard.get_guard().request_survey_restart("video_gate_forcewatch")
        except Exception as e:
            print(f"[VIDEO_GATE][WARN] soft_restart request failed: {type(e).__name__}: {e}")
        return "soft_restart"

    def _input_score(inp) -> int:
        score = 0
        try:
            aria = _norm_lc(inp.get_attribute("aria-labelledby") or "")
            col_txt = ""
            for tok in aria.split():
                if "columnlabel" in tok:
                    try:
                        txt = driver.find_element(By.ID, tok).text
                        col_txt += f" {txt}"
                    except Exception:
                        pass
            label_txt = _norm_lc(inp.find_element(By.XPATH, "ancestor::label[1]").text)
            blob = f"{label_txt} {_norm_lc(col_txt)}"
            if "oui" in blob or "yes" in blob:
                score += 100
            if "non" in blob or "no" in blob:
                score -= 10
        except Exception:
            pass
        return score

    clicked_groups = 0
    for _, inputs in groups.items():
        ordered = sorted(inputs, key=_input_score, reverse=True)
        target = ordered[0] if ordered else None
        if not target:
            continue
        try:
            driver.execute_script("arguments[0].click();", target)
            clicked_groups += 1
        except Exception:
            continue

    if clicked_groups <= 0:
        print("[VIDEO_GATE] video_gate detected -> soft_restart")
        try:
            import Management.guards.runtime_guard as runtime_guard
            runtime_guard.get_guard().request_survey_restart("video_gate_forcewatch")
        except Exception as e:
            print(f"[VIDEO_GATE][WARN] soft_restart request failed: {type(e).__name__}: {e}")
        return "soft_restart"

    print("[VIDEO_GATE] video_question resolved via DOM -> continue")
    return "resolved"
    
def _coerce_safe_value_if_questionish(raw_line: str) -> str:
    """
    Si le modÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¨le renvoie par erreur un intitulÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â© de question au lieu d'une valeur,
    fabrique une valeur sÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â»re en fonction du texte.
    Remappe aussi 'number' -> 'text'.
    """
    line = (raw_line or "").strip()
    # parse "label //// type //// contexte" tolÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â©rant
    m = re.split(r"/{4,}", line)
    label = (m[0] if m else "").strip()
    itype = (m[1] if len(m) > 1 else "").strip().lower() or "text"
    context = (m[2] if len(m) > 2 else "").strip()

    # forcer number -> text
    if itype == "number":
        itype = "text"


    low = _norm_lc(label)
    is_questiony = ("?" in label) or any(
        k in low
        for k in [
            "quel est",
            "quelle est",
            "what is",
            "how old",
            "postal code",
            "code postal",
            "zip",
            "age",
            "naissance",
            "year of birth",
        ]
    )

    if itype in ("text", "textarea") and (is_questiony or not label or len(label) < 2):
        # Heuristiques de valeur
        if any(k in low for k in ["postal", "code postal", "zip"]):
            label = "95000"  # 5 chiffres FR
        elif any(k in low for k in ["age", "how old"]):
            label = "28"  # adulte ok
        elif any(k in low for k in ["naissance", "year of birth"]):
            label = "1996"
        else:
            # valeur texte par  :  les  non  si champ num.
            label = "28"

    return f"{label} //// {itype} //// {context}"

# Fonction principale

# ============================================================================
# PATCH: Detection popup TopSurveys "Bon travail !" AVANT url_guard
# Ferme le popup, relance la preselection, ET execute le nouveau survey
# ============================================================================
def _handle_walr_image_eval_blocks(driver, question_blocks: list, api_key: str) -> bool:
    """
    Walr Image Evaluation: traitement spécial des questions d'évaluation d'images.
    
    Ce type de question nécessite l'envoi de l'image à OpenAI Vision pour analyse.
    Le bloc DOM contient:
      - requires_vision: True
      - image_url: URL de l'image à évaluer
      - context.walr_image_eval: True
      - target_id: pour récupérer option_xpath_map du registry
    
    Retourne True si un bloc a été traité, False sinon.
    """
    import base64
    import requests
    from Survey.dom_registry import get_target
    
    # Filtrer les blocs walr_image_eval
    vision_blocks = [
        b for b in question_blocks 
        if b.get("requires_vision") and b.get("context", {}).get("walr_image_eval")
    ]
    
    if not vision_blocks:
        return False
    
    print(f"[WALR_IMG_VISION] {len(vision_blocks)} bloc(s) image_eval détecté(s)")
    
    for block in vision_blocks:
        target_id = block.get("target_id")
        image_url = block.get("image_url")
        question = block.get("question", "Is this image positive or negative?")
        options = block.get("options", [])
        
        if not target_id or not image_url:
            print(f"[WALR_IMG_VISION] SKIP - missing target_id or image_url")
            continue
        
        # Récupérer les infos du registry (option_xpath_map)
        registry_data = get_target(target_id)
        if not registry_data:
            print(f"[WALR_IMG_VISION] SKIP - target_id {target_id} not in registry")
            continue
        
        option_xpath_map = registry_data.get("option_xpath_map", {})
        frame_chain = registry_data.get("frame_chain", [])
        
        if not option_xpath_map:
            print(f"[WALR_IMG_VISION] SKIP - no option_xpath_map for {target_id}")
            continue
        
        print(f"[WALR_IMG_VISION] Processing: question='{question[:50]}...'")
        print(f"[WALR_IMG_VISION] Options: {options}")
        print(f"[WALR_IMG_VISION] Image URL: {image_url[:80]}...")
        
        # Télécharger l'image et convertir en base64
        try:
            resp = requests.get(image_url, timeout=15)
            resp.raise_for_status()
            img_data = base64.b64encode(resp.content).decode("utf-8")
            
            # Détecter le type MIME
            content_type = resp.headers.get("Content-Type", "image/jpeg")
            if "png" in content_type.lower():
                media_type = "image/png"
            elif "gif" in content_type.lower():
                media_type = "image/gif"
            elif "webp" in content_type.lower():
                media_type = "image/webp"
            else:
                media_type = "image/jpeg"
            
            print(f"[WALR_IMG_VISION] Image downloaded: {len(resp.content)} bytes, type={media_type}")
        except Exception as e:
            print(f"[WALR_IMG_VISION] FAILED to download image: {e}")
            continue
        
        # Construire le prompt pour Vision API
        options_str = ", ".join(f'"{opt}"' for opt in options)
        vision_prompt = f"""Analyze this image and answer the following question.

Question: {question}

Available options: {options_str}

You MUST respond with EXACTLY one of the available options, nothing else.
Just output the option text that best answers the question based on what you see in the image."""
        
        # Appel OpenAI Vision API
        try:
            client = openai.OpenAI(api_key=api_key)
            
            vision_response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{media_type};base64,{img_data}",
                                    "detail": "low"  # low detail = moins cher
                                }
                            },
                            {
                                "type": "text",
                                "text": vision_prompt
                            }
                        ]
                    }
                ],
                max_tokens=50
            )
            
            chosen_option = (vision_response.choices[0].message.content or "").strip()
            print(f"[WALR_IMG_VISION] Vision API response: '{chosen_option}'")
        except Exception as e:
            print(f"[WALR_IMG_VISION] Vision API FAILED: {e}")
            # Fallback: choisir la première option
            chosen_option = options[0] if options else ""
            print(f"[WALR_IMG_VISION] Using fallback option: '{chosen_option}'")
        
        # Normaliser et matcher l'option
        chosen_lc = _norm_lc(chosen_option)
        matched_xpath = None
        matched_option = None
        
        for opt, xpath in option_xpath_map.items():
            if _norm_lc(opt) == chosen_lc:
                matched_xpath = xpath
                matched_option = opt
                break
        
        # Si pas de match exact, essayer match partiel
        if not matched_xpath:
            for opt, xpath in option_xpath_map.items():
                opt_lc = _norm_lc(opt)
                if chosen_lc in opt_lc or opt_lc in chosen_lc:
                    matched_xpath = xpath
                    matched_option = opt
                    print(f"[WALR_IMG_VISION] Partial match: '{chosen_option}' -> '{opt}'")
                    break
        
        if not matched_xpath:
            print(f"[WALR_IMG_VISION] NO MATCH for '{chosen_option}' in options")
            # Fallback: utiliser la première option
            matched_option = list(option_xpath_map.keys())[0]
            matched_xpath = option_xpath_map[matched_option]
            print(f"[WALR_IMG_VISION] Fallback to first option: '{matched_option}'")
        
        print(f"[WALR_IMG_VISION] Clicking option '{matched_option}' via XPath: {matched_xpath}")
        
        # Naviguer vers le frame si nécessaire
        try:
            driver.switch_to.default_content()
            for frame_idx in frame_chain:
                iframes = driver.find_elements(By.CSS_SELECTOR, "iframe")
                if frame_idx < len(iframes):
                    driver.switch_to.frame(iframes[frame_idx])
        except Exception as e:
            print(f"[WALR_IMG_VISION] Frame switch error (non-fatal): {e}")
        
        # Cliquer sur le bouton
        try:
            btn = driver.find_element(By.XPATH, matched_xpath)
            
            # Scroll into view
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
            time.sleep(0.3)
            
            # Clic via ActionChains (plus fiable que .click())
            ActionChains(driver).move_to_element(btn).pause(0.1).click().perform()
            print(f"[WALR_IMG_VISION] SUCCESS - clicked '{matched_option}'")
            
            time.sleep(0.5)  # Attendre réaction
            return True
            
        except Exception as e:
            print(f"[WALR_IMG_VISION] Click FAILED: {e}")
            # Essayer JS click en fallback
            try:
                btn = driver.find_element(By.XPATH, matched_xpath)
                driver.execute_script("arguments[0].click();", btn)
                print(f"[WALR_IMG_VISION] SUCCESS via JS click")
                return True
            except Exception as e2:
                print(f"[WALR_IMG_VISION] JS click also FAILED: {e2}")
                continue
    
    return False


def _handle_topsurveys_exclusion_popup(driver) -> bool:
    """
    Detecte et ferme le popup 'Bon travail !' sur TopSurveys.
    Si detecte: ferme le popup, navigue vers le meilleur survey, et l'execute.
    Retourne True si popup traite (le nouveau survey a ete lance).
    """
    import unicodedata
    import time
    from selenium.webdriver.common.by import By
    
    try:
        url = (driver.current_url or "").lower()
        if "topsurveys.app" not in url:
            return False
    except:
        return False
    
    try:
        txt = (driver.execute_script("return document.body.innerText || ''") or "").lower()
    except:
        return False
    
    def _norm(s):
        s = s.replace("'", "'").replace("'", "'")
        s = unicodedata.normalize('NFD', s)
        s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
        return s.lower()
    
    txt_norm = _norm(txt)
    
    patterns = ["bon travail", "tu as partiellement repondu", "credite ton compte"]
    
    if not any(p in txt_norm for p in patterns):
        return False
    
    print("[TOPSURVEYS_POPUP] Popup 'Bon travail !' detecte - fermeture...")
    
    # === ETAPE 1: Fermer le popup ===
    btn = None
    try:
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        btn = WebDriverWait(driver, 2).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-test-id='ps-common-actions-button']"))
        )
    except:
        pass
    
    if not btn:
        try:
            for b in driver.find_elements(By.CSS_SELECTOR, "button"):
                if b.is_displayed() and "compl" in _norm(b.text or ""):
                    btn = b
                    break
        except:
            pass
    
    if btn:
        try:
            driver.execute_script("arguments[0].click();", btn)
            print("[TOPSURVEYS_POPUP] Bouton 'Complete' clique.")
            time.sleep(1.0)
        except Exception as e:
            print(f"[TOPSURVEYS_POPUP] Erreur clic: {e}")
            return False
    else:
        print("[TOPSURVEYS_POPUP] Bouton non trouve.")
        return False
    
    # === ETAPE 2: Relancer la preselection vers un nouveau survey ===
    print("[TOPSURVEYS_POPUP] Relance preselection...")
    try:
        import preselection.survey_navigator as survey_navigator
        survey_navigator.go_to_best_paid_survey(driver)
        print("[TOPSURVEYS_POPUP] Navigation vers nouveau survey OK")
        time.sleep(1.0)
    except Exception as e:
        print(f"[TOPSURVEYS_POPUP] Erreur navigation: {e}")
        return False
    
    return True  # La boucle takeover continuera sur le nouveau survey


def _should_skip_post_actions_navigation(driver, question_blocks: list[dict]) -> bool:
    """
    Garde-fou minimal: sur Walr cardsort, l'avancement se fait via les
    boutons de réponse (answer-button). La routine CTA post-actions ne doit
    pas tourner, sinon elle peut re-cliquer une réponse.
    """
    for block in question_blocks or []:
        try:
            ctx = block.get("context") if isinstance(block, dict) else None
            if isinstance(ctx, dict) and ctx.get("walr_cardsort") is True:
                return True
        except Exception:
            continue

    # Critère DOM explicite (défense en profondeur si le contexte est absent)
    try:
        return bool(driver.find_elements(By.CSS_SELECTOR, "#cardSortContainer button.answer-button"))
    except Exception:
        return False

def execute_survey_page(driver, api_key):
    """
    Nouvelle version : capture , demande GPT-4o quoi faire, puis applique l'action.
    """
    import Management.guards.url_guard
    import Survey.action_dispatcher as action_dispatcher
    import selenium.webdriver.support.ui
    import Survey.dom_analyzer as dom_analyzer
    import Survey.prompt_builder as prompt_builder
    import Survey.batch_response_parser as batch_response_parser
    import Survey.dom_classifier as dom_classifier
    import Survey.action_dispatcher as action_dispatcher
    import Survey.dom_metrics as dom_metrics
    import Survey.batch_response_parser as batch_response_parser
    import Survey.input_handler as input_handler
    import Management.redirect_watcher as redirect_watcher
    import Survey.page_snapshot as page_snapshot

    # =========================================================================
    # PATCH: Detecter popup TopSurveys AVANT url_guard
    # =========================================================================
    try:
        _cur = driver.current_url
        if "topsurveys.app" in (_cur or "").lower():
            if _handle_topsurveys_exclusion_popup(driver):
                print("[TOPSURVEYS_POPUP] Popup traite -> continue boucle takeover")
                return True
    except Exception as e:
        print(f"[TOPSURVEYS_POPUP] Exception: {e}")

    try:
        cur = driver.current_url
    except Exception:
        cur = ""
    if not Management.guards.url_guard.is_allowed(cur):
        print(f"[URL_GUARD] Page hors , aucune action: {cur}")
        return False

    #  micro-: compteur rescans DOM sur CETTE page (reset  chaque page)
    try:
        driver._dom_rescans_this_page = 0
    except Exception:
        pass


    classification = dom_classifier.classify_dom(driver)

    if classification:
        itype = classification["itype"]
        handler_name = classification["handler"]
        allow_openai = classification["openai"]

        print(f"[DOM_CLASSIFIER] itype={itype} handler={handler_name} openai={allow_openai}")

        if not allow_openai:
            # handler local direct
            return getattr(action_dispatcher, handler_name)(driver)

    video_gate_state = _handle_forcewatch_video_gate(driver)
    if video_gate_state == "soft_restart":
        return True

    dom_metrics.log_snapshot()

    extracted_question_blocks = dom_analyzer.analyze_dom(driver) or []
    question_blocks = prompt_builder.filter_blocks_for_openai(extracted_question_blocks)
    if _env_truthy("DOM_CONTEXT_DEBUG", "0"):
        print(
            f"[DOM_CONTEXT_DEBUG] question_blocks_before_openai="
            f"{len(question_blocks or [])} extracted={len(extracted_question_blocks or [])}"
        )

    image_only_abort = _budgeted_soft_restart_for_image_only_inputs(driver, question_blocks)
    if image_only_abort == "restarted":
        return True
    if image_only_abort == "budget_exhausted":
        return False

    # =========================================================================
    # WALR IMAGE EVALUATION: Traitement Vision API AVANT le flux standard
    # Ces questions necessitent envoi de image a OpenAI Vision pour analyse.
    # =========================================================================
    try:
        if question_blocks and _handle_walr_image_eval_blocks(driver, question_blocks, api_key):
            print("[WALR_IMG_VISION] Bloc traite avec succes -> return True")
            return True
    except Exception as e:
        print(f"[WALR_IMG_VISION] Exception: {e}")
        import traceback
        traceback.print_exc()


    #  NEW: FocusVision/Decipher cardsort (DOM-only) avant OpenAI
    try:
        from Survey.action_dispatcher import solve_focusvision_cardsort
        if solve_focusvision_cardsort(driver):
            return True
    except Exception as e:
        print(f"[CARDSORT] solver failed: {e}")

    if not question_blocks:
        #  NEW: Decipher cardrating multi-rows (DOM-only) avant vision
        try:
            from Survey.action_dispatcher import solve_decipher_cardrating_rows
            if solve_decipher_cardrating_rows(driver):
                return True
        except Exception as e:
            print(f"[CARD RATING] solver failed before vision: {e}")

    #  SNAPSHOT DEBUG (opt-in)
    try:
        page_snapshot.snapshot_if_enabled(
            driver,
            reason="after_dom_analyze",
            question_blocks=extracted_question_blocks,
        )
    except Exception:
        pass

    client = openai.OpenAI(api_key=api_key)

    if question_blocks:
        prompt = prompt_builder.build_batch_prompt(question_blocks)

        instruction_raw = client.responses.create(
            input=prompt,
            model="gpt-5-nano",
        )

        raw_text = instruction_raw.output_text
        # contraintes max_select par QID (doit matcher le build_batch_prompt)
        qid_constraints = {f"Q{i}": int((b.get("max_select", 1) or 1)) for i, b in enumerate(question_blocks, start=1)}

        #  Meta par QID (pour sanitizer avec les options du DOM)
        qid_meta = {
            f"Q{i}": {
                "question": (b.get("question") or ""),
                "itype": (b.get("itype") or ""),
                "options": (b.get("options") or []),
                "max_select": int(b.get("max_select", 1) or 1),
            }
            for i, b in enumerate(question_blocks, start=1)
        }

        actions = batch_response_parser.parse_batch_response(raw_text, constraints=qid_constraints)
        actions = batch_response_parser.sanitize_actions(actions, qid_meta=qid_meta)

        #  "plan" (multi actions) + anti-double-fallback par action
        result = action_dispatcher.execute_actions_plan(driver, actions, stop_on_navigation=True)

        # --- Post-actions CTA nav (sauf Walr cardsort géré par answer-button) ---
        if _should_skip_post_actions_navigation(driver, question_blocks):
            print("[WALR_CS] skip post-actions CTA navigation (cardsort flow)")
        else:
            try:
                before_url = driver.current_url
                before_sig = redirect_watcher._dom_signature(driver)  # ou recalc local si tu veux optimiser

                # iframe-safe
                _local_pause_before_cta("navigation_cta")
                clicked = input_handler.try_click_navigation_cta_any_context(driver)

                if clicked:
                    changed = redirect_watcher.wait_for_navigation_or_dom_change(
                        driver,
                        before_url=before_url,
                        before_sig=before_sig,
                        timeout=10,
                    )
                    if changed:
                        print(" Navigation/DOM change   CTA.")
            except Exception:
                pass

        #  Export DynamoDB : compteur unique des rescans DOM (si > 0)
        try:
            rescans = int(getattr(driver, "_dom_rescans_this_page", 0))
            if rescans:
                # (optionnel) log local 1 ligne (utile pour debug)
                print(f"[DOM_RESCAN] rescans_this_page={rescans} url={_short_url(driver.current_url)}")
                dom_metrics.export_dom_rescans(rescans)
        except Exception:
            pass

        return result    
    else:
        dom_only_abort = _budgeted_dom_only_abort_for_image_eval(driver)
        if dom_only_abort == "restarted":
            return True
        if dom_only_abort == "budget_exhausted":
            return False

        # DOM-only: si le DOM est insuffisant, on abandonne proprement.
        print("DOM-only: aucun input exploitable (abort). source: survey_executor.py")

        screenshot_path = None

        # ------------------------------------------------------------
        # FALLBACK LOCAL "CTA-only" (question mais un bouton existe)
        # Objectif: éviter un abandon prématuré sur des pages comme "Consent"
        # ------------------------------------------------------------
        try:
            before_url = driver.current_url
        except Exception:
            before_url = ""

        try:
            before_sig = redirect_watcher._dom_signature(driver)
        except Exception:
            before_sig = ""

        # Essayer de cliquer sur un CTA de navigation (ex: "Start Survey", "Continue", "Next", etc.)
        # PHASE 1: Fallback CSS direct (connus de boutons nav)
        # Plus fiable que la recherche par texte pour les frameworks connus
        # PHASE 2: Si pas de bouton trouvé, recherche générique DOM (ex: boutons avec texte "next", "continue", etc.)
        NAV_BUTTON_SELECTORS = [
            "#cm-NextButton",                    # CMIX
            ".cm-navigation-next-button",        # CMIX alt
            ".cf-question--info button.cf-navigation-next",  # Confirmit intro CTA inline
            ".cf-page__question-list button.cf-navigation-next",  # Confirmit fallback scope
            "button.cf-navigation__button.cf-navigation-next",  # Confirmit/Forsta nav button explicit
            ".cf-question--info button.cf-navigation-ok",  # Confirmit/Forsta info gate "OK"
            ".cf-page__question-list button.cf-navigation-ok",  # Confirmit/Forsta fallback scope
            "button.cf-navigation__button.cf-navigation-ok",  # Confirmit/Forsta nav button explicit
            "#btn_continue",                     # Decipher
            "input.continue",                    # Decipher alt
            "[data-role='next']",                # Generic data-role
            "#btn_next",                         # AreYouNet (img inside <a>)
            '[data-testid="start-button"]',      # Quantilope coversheet
            "#bnNext", # Primis/Primisoft (bouton "Suivant")
        ]
        
        try:
            _local_pause_before_cta("cta_only_fallback")
            
            # Phase 1: CSS selectors directs (frameworks connus)
            print(f"[DEBUG] Phase 1: testing {len(NAV_BUTTON_SELECTORS)} selectors")
            for selector in NAV_BUTTON_SELECTORS:
                try:
                    btn = driver.find_element(By.CSS_SELECTOR, selector)
                    print(f"[DEBUG] Selector {selector} found: {btn.tag_name}")
                    # Si c'est une image dans un lien <a>, cibler le lien parent (AreYouNet, etc.)
                    if btn.tag_name.lower() == "img":
                        try:
                            parent = btn.find_element(By.XPATH, "./..")
                            if parent.tag_name.lower() == "a":
                                btn = parent
                        except Exception:
                            pass
                    is_disp = btn.is_displayed() if btn else False
                    is_vis_js = _is_visible_js(driver, btn) if btn else False
                    print(f"[DEBUG] {selector}: is_displayed={is_disp}, _is_visible_js={is_vis_js}")
                    if btn and (is_disp or is_vis_js):                        #  que ce n'est pas un bouton "refuser/exit"
                        btn_text = (btn.text or btn.get_attribute("value") or "").lower()
                        if any(bad in btn_text for bad in ["exit", "quit", "refuse", "disagree"]):
                            continue

                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                        intercept_only = (os.getenv("CTA_INTERCEPT_ONLY", "") or "").strip().lower() in {"1", "true", "yes", "on"}
                        if intercept_only:
                            clicked = input_handler.try_click_navigation_cta_any_context(driver)
                            if not clicked:
                                print(f"[CTA_NAV] found via selector but INTERCEPT FAILED: {selector}")
                                continue
                            print(f"[CTA_NAV] FOUND+INTERCEPTED via CSS: {selector}")
                        else:
                            driver.execute_script("arguments[0].click();", btn)
                            print(f" CTA  via  CSS: {selector}")

                        try:
                            redirect_watcher.wait_for_navigation_or_dom_change(
                                driver, before_url=before_url, before_sig=before_sig, timeout=10
                            )
                        except Exception:
                            pass
                        return True
                except Exception as e:
                    print(f"[DEBUG] Selector {selector} FAILED: {type(e).__name__}")
                    continue  #  non , essayer le suivant
            
            # Phase 2: Recherche par texte (fallback existant)
            clicked = (
                input_handler.click_cta_strong_any_context(driver, text="accepter")
                or input_handler.click_cta_strong_any_context(driver, text="continuer")
                or input_handler.click_cta_strong_any_context(driver, text="accept")
                or input_handler.click_cta_strong_any_context(driver, text="agree")
                or input_handler.click_cta_strong_any_context(driver, text="next")
                or input_handler.click_cta_strong_any_context(driver, text="suivant")
                or input_handler.click_cta_strong_any_context(driver, text="dÃ©marrer")
                or input_handler.click_cta_strong_any_context(driver, text="commencer")
            )
            # Fallback direct par ID pour Qualtrics et CTA standards
            if not clicked:
                for cta_id in ["cm-NextButton", "NextButton", "nextButton", "continueButton", "submitButton"]:
                    try:
                        btn = driver.find_element(By.ID, cta_id)
                        if btn.is_displayed():
                            driver.execute_script("arguments[0].click();", btn)
                            clicked = True
                            print(f"[CTA_FALLBACK] Clicked by ID: {cta_id}")
                            break
                    except Exception:
                        pass            
            if clicked:
                print(" CTA  via recherche par texte")
                try:
                    redirect_watcher.wait_for_navigation_or_dom_change(
                        driver, before_url=before_url, before_sig=before_sig, timeout=10
                    )
                except Exception:
                    pass
                return True
                
        except Exception as e:
            #  Logger l'erreur au lieu de l'avaler silencieusement
            print(f" Fallback CTA-only : {type(e).__name__}: {e}")


        # DOM-only: abandon explicite si aucun CTA DOM exploitable.
        if _env_truthy("SURVEY_DOM_ONLY_ABORT", "1"):
            print("DOM-only: abort_reason=dom_no_match_abort (SURVEY_DOM_ONLY_ABORT=1).")
            return False

        # Import lazy: n'embarquer screenshot_analyzer / PIL que si un traitement image est explicitement activé
        import Survey.screenshot_analyzer as screenshot_analyzer
        # 1) Tentative screenshot  (EdgeSurvey/InnovateMR : question souvent dans img.taImage)
        try:
            img = driver.find_element(By.CSS_SELECTOR, "img.taImage")
            tmp_dir = os.path.join(tempfile.gettempdir(), "surveybot_screens")
            os.makedirs(tmp_dir, exist_ok=True)
            screenshot_path = os.path.join(tmp_dir, f"taImage_{int(time.time()*1000)}.png")
            img.screenshot(screenshot_path)
            print(f" Screenshot  (img.taImage) -> {screenshot_path}")
        except Exception:
            screenshot_path = None

        # 2) Fallback viewport (moins lourd que full_page) puis full_page en dernier recours
        if not screenshot_path:
            print(" Screenshot viewport (pas full-page). source: survey_executor.py")
            try:
                screenshot_path = screenshot_analyzer.take_screenshot(driver, full_page=False)
            except Exception:
                screenshot_path = screenshot_analyzer.take_screenshot(driver, full_page=True)

        print(" Envoi  GPT pour  visuelle. source: survey_executor.py line 59")
        instruction = screenshot_analyzer.send_image_to_gpt(screenshot_path, api_key)

        #  UTILISATION, juste  avoir  la  du  (variable `instruction`)
        #    et avant de la renvoyer   :
        lines = [ln for ln in (instruction or "").splitlines() if ln.strip()]
        fixed_lines = [_coerce_safe_value_if_questionish(ln) for ln in lines]
        instruction = "\n".join(fixed_lines)
        #print(" Instruction  ( dans le fixed_lines) :", instruction, " source: survey_executor.py")

        # --- Ne conserver que la  ligne non vide ---
        if instruction:
            instruction = next(
                (ln.strip() for ln in instruction.splitlines() if ln.strip()), ""
            )

        print(
            " Instruction  () :",
            instruction,
            " source: survey_executor.py line 67",
        )

        try:
            success = action_dispatcher.execute_action(driver, instruction)
            if not success:
                print(
                    " Aucune action  par le dispatcher. source: survey_executor.py"
                )
            return success
        except Exception as e:
            print(
                " Erreur dans  de   sur GPT; source: survey_executor.py",
            )
            return False

def extract_full_visible_text(driver):
    """
    Extrait tout le texte visible de la page, en ignorant les balises de type lien, script, style, header, etc.
    """
    js = """
    return Array.from(document.querySelectorAll('body *'))
      .filter(e => {
          const style = window.getComputedStyle(e);
          const tag = e.tagName.toLowerCase();
          const ignored = ['a', 'footer', 'header', 'nav', 'script', 'style'];
          return style && style.display !== 'none' &&
                 style.visibility !== 'hidden' &&
                 e.offsetParent !== null &&
                 !ignored.includes(tag);
      })
      .map(e => e.innerText)
      .filter(t => t && t.trim().length > 5)
      .map(t => t.trim());
    """

    try:
        result = driver.execute_script(js)
        return list(dict.fromkeys(result))  # supprimer les doublons
    except Exception as e:
        print(" JS extraction erreur:", e, "survey_executor.py line 251")
        return []

#  Sous-fonction : appliquer une action  par l'IA

def perform_action_based_on_text(driver, action):
    """
    Essaie de cliquer sur un bouton ou un label qui correspond  l'action textuelle de l'IA.
    """
    buttons = (
        driver.find_elements(By.TAG_NAME, "button")
        + driver.find_elements(By.TAG_NAME, "input")
        + driver.find_elements(By.TAG_NAME, "a")
    )

    for elem in buttons:
        try:
            label = elem.get_attribute("value") or elem.text
            if not label:
                spans = elem.find_elements(By.TAG_NAME, "span")
                for span in spans:
                    if span.text.strip():
                        label = span.text.strip()
                        break
            if label and action.lower() in label.lower():
                ActionChains(driver).move_to_element(elem).click().perform()
                print(
                    f" Action '{action}'  sur l' : {label} survey_executor.py line 274"
                )
                time.sleep(2)
                return True
        except:
            continue

    print(
        f" Aucun  ne correspond  l'action source: survey_executor.py line 280"
    )
    return False

def _page_fingerprint(driver) -> str:
    url = driver.current_url or ""
    # cheap: titre + un bout de body text
    title = driver.title or ""
    body = ""
    try:
        body = driver.find_element(By.TAG_NAME, "body").text[:2000]
    except Exception:
        pass
    raw = f"{url}\n{title}\n{body}"
    return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()
