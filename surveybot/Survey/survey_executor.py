from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
import re, openai, time, unicodedata, os, sys, hashlib, tempfile
from urllib.parse import urlsplit
from Survey.log_utils import log_debug, log_info
from Survey.functions import _handle_topsurveys_exclusion_popup

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

PAUSE_BEFORE_CTA = 1.0  # pause après le dispatch des réponses, avant le clic CTA (laisser le DOM se stabiliser)

def _local_pause_before_cta(reason: str = "") -> None:
    try:
        from config import should_pause_before_cta
        if not should_pause_before_cta():
            return
        msg = "[LOCAL][PAUSE] Appuie sur <Enter> pour autoriser le clic CTA"
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


def _has_unfilled_required_inputs(driver) -> bool:
    """
    Retourne True si la page courante contient des inputs required visibles et non remplis.
    Utilisé pour éviter un clic CTA prématuré sur les formulaires multi-champs
    (ex: page pre-screener avec postcode + age + education + gender dans un seul <form>).

    Critères DOM purs (sélecteurs observables) :
    - input[type=text/number/email][required] visible et vide
    - select[required] visible et sans sélection
    - groupes radio[required] visibles sans option cochée
    """
    try:
        return bool(driver.execute_script("""
            var isVisible = function(el) {
                if (!el) return false;
                var s = window.getComputedStyle(el);
                if (s.display === 'none' || s.visibility === 'hidden') return false;
                var r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
            };
            // Text/number/email inputs required and empty
            var textSel = "input[required][type='text'], input[required][type='number'], input[required][type='email'], textarea[required]";
            var isAngularHelper = function(el) {
                if ((el.className || '').indexOf('hold-model') !== -1) return true;
                if (el.getAttribute('tabindex') === '-1') return true;
                var p = el.parentElement;
                while (p) {
                    var tag = p.tagName ? p.tagName.toLowerCase() : '';
                    if (tag === 'rps-select') return true;
                    if (p.hasAttribute && p.hasAttribute('data-selector') && (p.className || '').indexOf('rps-select') !== -1) return true;
                    p = p.parentElement;
                }
                return false;
            };
            var textInputs = Array.from(document.querySelectorAll(textSel));
            if (textInputs.some(function(el) { return isVisible(el) && !isAngularHelper(el) && !(el.value || '').trim(); })) return true;
            // Required selects with no value
            var selects = Array.from(document.querySelectorAll("select[required]"));
            if (selects.some(function(el) { return isVisible(el) && !el.value; })) return true;
            // Required radio groups with no checked option
            var radioNames = {};
            Array.from(document.querySelectorAll("input[type='radio'][required]")).forEach(function(el) {
                if (isVisible(el) && el.name) radioNames[el.name] = true;
            });
            for (var name in radioNames) {
                if (!document.querySelector("input[type='radio'][name='" + name + "']:checked")) return true;
            }
            return false;
        """))
    except Exception:
        return False


def _detect_rate_rank_image_eval_dom(driver) -> tuple[bool, str]:
    """
    Détecte un pattern DOM de type "image/product evaluation" (rate & rank)
    qui doit déclencher un abandon DOM-only (sans stratégie alternative).
    """
    try:
        dom = driver.execute_script(
            r"""
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
            r"""
            const isVisible = (el) => {
              if (!el) return false;
              const s = window.getComputedStyle(el);
              if (!s || s.display === 'none' || s.visibility === 'hidden') return false;
              const r = el.getBoundingClientRect();
              return !!(r && r.width > 0 && r.height > 0);
            };
            const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
            const fold = (s) => {
              const base = norm(s).toLowerCase();
              try {
                return base.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
              } catch (e) {
                return base;
              }
            };
            const shortText = (s, maxLen = 24) => {
              const t = norm(s);
              return t.length <= maxLen ? t : '';
            };
            const hasQuestionContainer = !!document.querySelector('[questionname], .questionContainer, .mrQuestionTable, .question-component');
            const pageTextFold = fold((document.body && (document.body.innerText || document.body.textContent)) || '');
            const hasSelectVerb = (
              pageTextFold.includes('veuillez selectionner')
              || pageTextFold.includes('please select')
              || pageTextFold.includes('select the')
              || pageTextFold.includes('choose')
            );
            const hasImageWord = (
              pageTextFold.includes(' image')
              || pageTextFold.includes(' images')
              || pageTextFold.includes('picture')
              || pageTextFold.includes('photo')
              || pageTextFold.includes('representing')
              || pageTextFold.includes('representant')
              || pageTextFold.includes('corresponding')
              || pageTextFold.includes('correspondant')
            );
            const hasCountWord = /(\b\d+\b|\bone\b|\btwo\b|\bthree\b|\bfour\b|\bfive\b|\bsix\b|\bseven\b|\beight\b|\bnine\b|\bten\b|\bun\b|\bune\b|\bdeux\b|\btrois\b|\bquatre\b|\bcinq\b|\bsept\b|\bhuit\b|\bneuf\b|\bdix\b)/i.test(pageTextFold);
            const hasVisualChallengeInstruction = hasSelectVerb && hasImageWord && hasCountWord;
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

            const visibleImgCount = Array.from(document.querySelectorAll('img')).filter(isVisible).length;
            const visibleBgTileCount = clickableCandidates.filter((el) => {
              if (!isVisible(el)) return false;
              const style = window.getComputedStyle(el);
              return !!(style && style.backgroundImage && style.backgroundImage !== 'none');
            }).length;

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
            const hasQuestionHint = /etes[-\s]*vous|quel\s+age|quel\s+ge|how\s+old|are\s+you/i.test(bodyText);
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
              visible_img_count: visibleImgCount,
              visible_bg_tile_count: visibleBgTileCount,
              has_visual_challenge_instruction: hasVisualChallengeInstruction,
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

    has_visual_challenge_instruction = bool(dom.get("has_visual_challenge_instruction"))
    visual_tile_count = int(dom.get("visible_img_count") or 0) + int(dom.get("visible_bg_tile_count") or 0)
    is_visual_challenge = has_visual_challenge_instruction and visual_tile_count >= 6

    if not image_groups and not clickable_groups and not is_visual_challenge:
        print(
            "[DOM_ONLY_ABORT] detector_no_match "
            f"inputs={int(dom.get('input_count') or 0)} visible_wrappers={int(dom.get('visible_wrapper_count') or 0)} "
            f"input_groups={int(dom.get('input_group_count') or 0)} image_groups={len(image_groups)} "
            f"image_options={int(dom.get('image_only_option_count') or 0)} clickable_groups={len(clickable_groups)} "
            f"clickable_image_options={int(dom.get('clickable_image_only_option_count') or 0)} "
            f"clickable_visible={int(dom.get('clickable_visible_count') or 0)} "
            f"instruction={1 if has_visual_challenge_instruction else 0} visual_tiles={visual_tile_count}"
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
    if is_visual_challenge:
        pattern_reason = "image_selection_challenge"
        image_groups_all.append({
            "groupKey": "visual_challenge::instruction_plus_tiles",
            "optionCount": visual_tile_count,
            "imgHints": [],
        })
    elif clickable_groups_norm:
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


# ---------------------------------------------------------------------------
# DÉTECTION PAGE DE DISQUALIFICATION / FIN DE SONDAGE
# Guard DOM : aucun input exploitable (branche else execute_survey_page) +
#   signal textuel de rejet OU iframe callback panel (samplicio.us, etc.)
# Non-régression : ces signaux n'apparaissent pas sur les pages intro/consent.
# ---------------------------------------------------------------------------

_DISQ_TEXT_SIGNALS = [
    "ne vous êtes pas qualifié",
    "vous ne vous y êtes pas qualifié",
    "not qualified",
    "not eligible",
    "quota full",
    "quota is full",
    "survey is closed",
    "survey closed",
    "survey has ended",
    "survey has been closed",
    "no longer available",
    "screened out",
    "disqualified",
    "you have been disqualified",
]

_DISQ_CALLBACK_PATTERNS = ["samplicio.us", "clientcallback", "client_callback"]


def _detect_disqualification_page(driver) -> tuple:
    """Retourne (True, signal) si la page est une page de disqualification/fin, (False, '') sinon."""
    try:
        cb_src = driver.execute_script(
            """
            var iframes = document.querySelectorAll('iframe[src]');
            for (var i = 0; i < iframes.length; i++) {
                var s = (iframes[i].getAttribute('src') || '').toLowerCase();
                if (s.includes('samplicio.us') || s.includes('clientcallback') || s.includes('client_callback')) {
                    return s;
                }
            }
            return null;
            """
        )
        if cb_src:
            return True, f"callback_iframe:{str(cb_src)[:80]}"
    except Exception:
        pass

    try:
        body_text = driver.execute_script(
            "return (document.body && document.body.innerText) || '';"
        )
        body_lc = _norm_lc(body_text or "")
        for sig in _DISQ_TEXT_SIGNALS:
            if sig in body_lc:
                return True, f"disq_text:{sig}"
    except Exception:
        pass

    return False, ""


def _budgeted_disqualification_restart(driver) -> str:
    """
    Retourne:
      - "restarted"        si page disqualification détectée + soft_restart demandé,
      - "budget_exhausted" si détecté mais budget anti-boucle dépassé,
      - "no_match"         sinon.
    """
    is_disq, signal = _detect_disqualification_page(driver)
    if not is_disq:
        return "no_match"

    try:
        current_url = driver.current_url or ""
    except Exception:
        current_url = ""
    up = urlsplit(current_url)
    budget_key = f"{up.scheme}://{up.netloc}{up.path}"

    try:
        counters = getattr(driver, "_disq_page_seen", None)
        if not isinstance(counters, dict):
            counters = {}
        current = int(counters.get(budget_key, 0) or 0)
        max_hits = 1
        if current >= max_hits:
            log_info("[DISQ_PAGE]", f"budget_exhausted key={budget_key} hits={current}/{max_hits}")
            driver._disq_page_seen = counters
            return "budget_exhausted"
        counters[budget_key] = current + 1
        driver._disq_page_seen = counters
    except Exception:
        pass

    log_info("[DISQ_PAGE]", f"disqualification détectée ({signal}) -> soft_restart key={budget_key}")
    try:
        import Management.guards.runtime_guard as runtime_guard
        runtime_guard.get_guard().request_survey_restart(f"disqualification_page:{signal}")
    except Exception as e:
        print(f"[DISQ_PAGE][WARN] soft_restart request failed: {type(e).__name__}: {e}")
    return "restarted"


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
      - "random_selected" si sélection aléatoire réussie (image_only_wrapped_inputs uniquement),
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
        max_hits = 1 if pattern_reason == "image_selection_challenge" else 2
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

    if pattern_reason == "image_only_wrapped_inputs":
        try:
            clicked = driver.execute_script(
                r"""
                const isVisible = (el) => {
                  if (!el) return false;
                  const s = window.getComputedStyle(el);
                  if (!s || s.display === 'none' || s.visibility === 'hidden') return false;
                  const r = el.getBoundingClientRect();
                  return !!(r && r.width > 0 && r.height > 0);
                };
                const inputs = Array.from(document.querySelectorAll(
                  "input[type='radio'][name], input[type='checkbox'][name]"
                ));
                const interactable = inputs.filter((inp) => {
                  if (inp.disabled) return false;
                  const lbl = inp.id
                    ? document.querySelector('label[for="' + CSS.escape(inp.id) + '"]')
                    : null;
                  const wrapper = lbl
                    || inp.closest('label')
                    || inp.closest('[tabindex], [role="radio"], [role="checkbox"]');
                  return isVisible(wrapper || inp);
                });
                if (!interactable.length) return null;
                const idx = Math.floor(Math.random() * interactable.length);
                const inp = interactable[idx];
                const lbl = inp.id
                  ? document.querySelector('label[for="' + CSS.escape(inp.id) + '"]')
                  : null;
                (lbl || inp).click();
                return inp.id || inp.name || 'ok';
                """
            )
        except Exception as _e:
            clicked = None
            log_debug("DOM_ONLY_ABORT", f"image_only_wrapped_inputs random_click failed: {type(_e).__name__}: {_e}")

        if clicked:
            log_info("DOM_ONLY_ABORT", f"image_only_wrapped_inputs -> random_selected input={clicked} key={budget_key}")
            return "random_selected"
        log_info("DOM_ONLY_ABORT", f"image_only_wrapped_inputs -> no_interactable_inputs key={budget_key}")

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


def _detect_open_text_embedded_image_unresolvable_dom(
    driver,
    question_blocks: list[dict],
) -> tuple[bool, str, str]:
    """
    Détecte un écran "question ouverte qualitative" non résoluble de façon fiable
    en DOM-only:
      - un seul textarea visible/exploitable,
      - pas d'autres contrôles de réponse (radio/checkbox/select/autre text input),
      - présence d'une image embarquée large (src data:image) de type taImage.
    """
    open_text_blocks = [
        b
        for b in (question_blocks or [])
        if _norm_lc((b.get("itype") or "")) == "textarea"
    ]
    if len(open_text_blocks) != 1:
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
            const isEnabled = (el) => {
              if (!el) return false;
              if (el.disabled) return false;
              if (String(el.getAttribute('aria-disabled') || '').toLowerCase() === 'true') return false;
              return true;
            };

            const textareas = Array.from(document.querySelectorAll('textarea'))
              .filter(el => isVisible(el) && isEnabled(el) && !el.readOnly);
            const textInputs = Array.from(document.querySelectorAll("input[type='text'], input[type='search'], input[type='number'], input[type='email'], input[type='tel']"))
              .filter(el => isVisible(el) && isEnabled(el) && !el.readOnly);
            const radios = Array.from(document.querySelectorAll("input[type='radio']")).filter(el => isVisible(el) && isEnabled(el));
            const checkboxes = Array.from(document.querySelectorAll("input[type='checkbox']")).filter(el => isVisible(el) && isEnabled(el));
            const selects = Array.from(document.querySelectorAll('select')).filter(el => isVisible(el) && isEnabled(el));

            const taImages = Array.from(document.querySelectorAll('img.taImage, img[class*="taImage"]')).filter(isVisible);
            const largeEmbeddedTaImages = taImages.filter((img) => {
              const src = String(img.getAttribute('src') || '').trim().toLowerCase();
              if (!src.startsWith('data:image/')) return false;
              const r = img.getBoundingClientRect();
              const isLarge = !!(r && r.width >= 320 && r.height >= 80);
              const style = window.getComputedStyle(img);
              const pointerNone = !!(style && style.pointerEvents === 'none');
              return isLarge && pointerNone;
            });

            return {
              textarea_count: textareas.length,
              other_text_input_count: textInputs.length,
              radio_count: radios.length,
              checkbox_count: checkboxes.length,
              select_count: selects.length,
              ta_image_count: taImages.length,
              large_embedded_ta_image_count: largeEmbeddedTaImages.length,
            };
            """
        ) or {}
    except Exception:
        dom = {}

    textarea_count = int(dom.get("textarea_count") or 0)
    other_inputs_count = (
        int(dom.get("other_text_input_count") or 0)
        + int(dom.get("radio_count") or 0)
        + int(dom.get("checkbox_count") or 0)
        + int(dom.get("select_count") or 0)
    )
    ta_image_count = int(dom.get("ta_image_count") or 0)
    large_embedded_ta_image_count = int(dom.get("large_embedded_ta_image_count") or 0)

    is_match = (
        textarea_count == 1
        and other_inputs_count == 0
        and ta_image_count >= 1
        and large_embedded_ta_image_count >= 1
    )
    if not is_match:
        return False, "", ""

    fp_payload = {
        "textarea_count": textarea_count,
        "other_inputs_count": other_inputs_count,
        "ta_image_count": ta_image_count,
        "large_embedded_ta_image_count": large_embedded_ta_image_count,
    }
    fingerprint = hashlib.sha1(repr(fp_payload).encode("utf-8", errors="ignore")).hexdigest()[:12]
    return True, "open_text_embedded_image", fingerprint


def _budgeted_soft_restart_for_open_text_embedded_image(driver, question_blocks: list[dict]) -> str:
    """
    Retourne:
      - "restarted" si pattern détecté + soft_restart demandé,
      - "budget_exhausted" si budget anti-boucle dépassé,
      - "no_match" sinon.
    """
    is_match, pattern_reason, dom_fp = _detect_open_text_embedded_image_unresolvable_dom(driver, question_blocks)
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
        max_hits = 1
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

    if has_forcewatch_video:
        # ── Étape 1 : déclencher la lecture vidéo via JS pour activer les handlers Kantar ──
        # Le script mrIWeb surveille 'timeupdate' et 'ended' pour retirer disabled du bouton.
        # En headless, on simule la fin de la vidéo sans attendre la lecture réelle.
        try:
            driver.execute_script("""
                var vids = Array.from(document.querySelectorAll('video[data-forcewatch]'));
                vids.forEach(function(v) {
                    try {
                        // Positionner à 95% de la durée (déclenche timeupdate)
                        if (v.duration && isFinite(v.duration) && v.duration > 0) {
                            v.currentTime = v.duration * 0.95;
                        }
                        v.dispatchEvent(new Event('timeupdate', {bubbles: true}));
                        v.dispatchEvent(new Event('ended', {bubbles: true}));
                    } catch(e) {}
                });
            """)
            log_debug("[VIDEO_GATE]", "forcewatch JS trigger: timeupdate + ended dispatched")
        except Exception as _vt_exc:
            log_debug("[VIDEO_GATE]", f"forcewatch JS trigger failed (non-fatal): {_vt_exc}")

        # ── Étape 2 : polling court — attendre que le bouton submit soit enabled ──
        # Budget : 10 itérations × 0,5 s = 5 s max
        _submit_enabled = False
        for _poll_i in range(10):
            try:
                _is_disabled = driver.execute_script(
                    "var b = document.querySelector('#submit-button'); "
                    "return b ? b.hasAttribute('disabled') : true;"
                )
                if not _is_disabled:
                    _submit_enabled = True
                    log_debug("[VIDEO_GATE]", f"submit-button enabled après {_poll_i + 1} itération(s)")
                    break
            except Exception:
                pass
            time.sleep(0.5)

        if not _submit_enabled:
            log_debug("[VIDEO_GATE]", "submit-button toujours disabled après 5s (non-fatal, on tente quand même)")

    if not has_forcewatch_video:
        # Détection complémentaire : player ISD (div#ISD présent dans le DOM)
        # + .cf-navigation__button masqué via CSS (computed style) — pattern Forsta/Confirmit.
        # Critère 1 : existence structurelle de div#ISD (pas de check visibilité — vidéo peut
        #             encore charger au moment de l'exécution, BoundingClientRect = 0).
        # Critère 2 : getComputedStyle pour tenir compte des règles CSS injectées (<style>),
        #             pas seulement du style inline (getAttribute('style') insuffisant).
        try:
            has_isd_gate = bool(driver.execute_script(
                """
                const isdRoot = document.querySelector('#ISD');
                if (!isdRoot) return false;
                const navBtn = document.querySelector('.cf-navigation__button');
                if (!navBtn) return false;
                return window.getComputedStyle(navBtn).display === 'none';
                """
            ))
        except Exception:
            has_isd_gate = False

        if has_isd_gate:
            print("[VIDEO_GATE] ISD video_gate detected -> soft_restart")
            log_debug("[VIDEO_GATE]", "ISD player + masked cf-navigation__button confirmed")
            try:
                import Management.guards.runtime_guard as runtime_guard
                runtime_guard.get_guard().request_survey_restart("video_gate_isd")
            except Exception as e:
                print(f"[VIDEO_GATE][WARN] soft_restart request failed: {type(e).__name__}: {e}")
            return "soft_restart"

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
    Si le modèle renvoie par erreur un intitulé de question au lieu d'une valeur,
    fabrique une valeur sûre en fonction du texte.
    Remappe aussi 'number' -> 'text'.
    """
    line = (raw_line or "").strip()
    # parse "label //// type //// contexte" tolérant
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
# PATCH: Detection popup TopSurveys "Bon travail !"
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


def _handle_cf_carousel_image_blocks(driver, question_blocks: list, api_key: str) -> bool:
    """
    Confirmit/GfK CF-Carousel avec image partagée : traitement Vision API.

    Détecte les blocs context.kind=cf_carousel_item qui possèdent un champ image_url.
    Envoie UNE seule requête gpt-4o Vision avec l'image + toutes les affirmations,
    puis navigue chaque item du carousel et clique la réponse.

    Retourne True si au moins un bloc a été traité, False sinon.
    Gate DOM strict : image_url présent sur au moins un bloc cf_carousel_item.
    """
    import requests
    import base64
    from Survey.dom_registry import get_target

    # Gate : blocs cf_carousel_item avec image_url
    carousel_blocks = [
        b for b in question_blocks
        if (b.get("context") or {}).get("kind") == "cf_carousel_item"
        and b.get("image_url")
    ]
    if not carousel_blocks:
        return False

    image_url = carousel_blocks[0]["image_url"]
    print(f"[CF_CAROUSEL_VISION] {len(carousel_blocks)} item(s) avec image: {image_url[:80]}")

    # Télécharger l'image une seule fois
    try:
        resp = requests.get(image_url, timeout=15)
        resp.raise_for_status()
        img_data = base64.b64encode(resp.content).decode("utf-8")
        content_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
        if content_type not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
            content_type = "image/jpeg"
    except Exception as e:
        print(f"[CF_CAROUSEL_VISION] Téléchargement image FAILED: {e}")
        return False

    # Construire le prompt Vision avec toutes les affirmations
    lines = ["Regarde attentivement cette image et réponds à chaque affirmation par exactement une des options fournies."]
    lines.append("Format STRICT — une ligne par question : Q<n>: <réponse exacte>")
    lines.append("")
    for i, block in enumerate(carousel_blocks, start=1):
        affirmation = (block.get("question") or "").strip()
        opts = block.get("options") or []
        opts_str = " / ".join(opts)
        lines.append(f"Q{i}: {affirmation}  [options: {opts_str}]")
    vision_prompt = "\n".join(lines)

    # Appel Vision API (gpt-4o)
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
                                "url": f"data:{content_type};base64,{img_data}",
                                "detail": "low",
                            },
                        },
                        {"type": "text", "text": vision_prompt},
                    ],
                }
            ],
            max_tokens=200,
        )
        raw_answer = (vision_response.choices[0].message.content or "").strip()
        print(f"[CF_CAROUSEL_VISION] Réponse brute: {raw_answer!r}")
    except Exception as e:
        print(f"[CF_CAROUSEL_VISION] Vision API FAILED: {e}")
        return False

    # Parser les réponses : "Q1: Vrai\nQ2: Faux\n..."
    answer_map: dict[int, str] = {}
    for line in raw_answer.splitlines():
        line = line.strip()
        m = re.match(r"Q(\d+)\s*[:=]\s*(.+)", line, re.IGNORECASE)
        if m:
            answer_map[int(m.group(1))] = m.group(2).strip()

    any_clicked = False
    for i, block in enumerate(carousel_blocks, start=1):
        chosen_raw = answer_map.get(i, "")
        target_id = block.get("target_id")
        options = block.get("options") or []

        if not target_id:
            print(f"[CF_CAROUSEL_VISION] Q{i}: pas de target_id, skip")
            continue

        registry_data = get_target(target_id)
        if not registry_data:
            print(f"[CF_CAROUSEL_VISION] Q{i}: target_id={target_id} absent du registry, skip")
            continue

        option_xpath_map = registry_data.get("option_xpath_map") or {}
        pre_click_xpaths = registry_data.get("pre_click_xpaths") or []
        frame_chain = registry_data.get("frame_chain") or []

        # Matcher la réponse (exact puis partiel)
        chosen_lc = (chosen_raw or "").lower().strip()
        matched_xpath = None
        matched_option = None
        for opt, xp in option_xpath_map.items():
            if opt.lower().strip() == chosen_lc:
                matched_xpath = xp
                matched_option = opt
                break
        if not matched_xpath:
            for opt, xp in option_xpath_map.items():
                opt_lc = opt.lower().strip()
                if chosen_lc in opt_lc or opt_lc in chosen_lc:
                    matched_xpath = xp
                    matched_option = opt
                    break
        if not matched_xpath and options:
            # Fallback : première option
            first_key = list(option_xpath_map.keys())[0] if option_xpath_map else None
            if first_key:
                matched_xpath = option_xpath_map[first_key]
                matched_option = first_key
                print(f"[CF_CAROUSEL_VISION] Q{i}: pas de match pour {chosen_raw!r}, fallback={matched_option!r}")

        if not matched_xpath:
            print(f"[CF_CAROUSEL_VISION] Q{i}: option introuvable, skip")
            continue

        print(f"[CF_CAROUSEL_VISION] Q{i}: réponse={matched_option!r} xpath={matched_xpath}")

        # Naviguer vers le frame si nécessaire
        try:
            driver.switch_to.default_content()
            for frame_idx in frame_chain:
                iframes = driver.find_elements(By.CSS_SELECTOR, "iframe")
                if frame_idx < len(iframes):
                    driver.switch_to.frame(iframes[frame_idx])
        except Exception:
            pass

        # Cliquer le paging button pour naviguer vers cet item
        for pxp in pre_click_xpaths[:1]:
            try:
                paging_cands = driver.find_elements(By.XPATH, pxp)
                if paging_cands:
                    pel = paging_cands[0]
                    aria_pressed = (pel.get_attribute("aria-pressed") or "").strip().lower()
                    if aria_pressed != "true":
                        driver.execute_script("arguments[0].click();", pel)
                        time.sleep(0.25)
            except Exception:
                pass

        # Cliquer la réponse
        try:
            cands = driver.find_elements(By.XPATH, matched_xpath)
            btn_el = cands[0] if cands else None
            if btn_el is None:
                print(f"[CF_CAROUSEL_VISION] Q{i}: élément introuvable xpath={matched_xpath}")
                continue
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn_el)
            driver.execute_script("arguments[0].click();", btn_el)
            time.sleep(0.15)
            ok = (btn_el.get_attribute("aria-checked") or "").strip().lower() == "true"
            if not ok:
                ActionChains(driver).move_to_element(btn_el).pause(0.05).click().perform()
                time.sleep(0.15)
            print(f"[CF_CAROUSEL_VISION] Q{i}: clicked OK")
            any_clicked = True
        except Exception as e:
            print(f"[CF_CAROUSEL_VISION] Q{i}: click FAILED: {e}")
            continue

    return any_clicked



def _handle_phone_verification(driver):
    """
    Détecte et traite l'écran interstitiel "Courte pause – Vérifie ton profil"
    sur topsurveys.app.

    Critères DOM stricts : div.phone-verification-container ET input.phone-number-input
    présents simultanément. Scoped topsurveys.app uniquement.

    Retourne :
      True  — écran détecté + numéro saisi + bouton cliqué
      False — écran détecté mais impossible d'obtenir un numéro (log + abandon)
      None  — écran absent (continuer le flux normal)
    """
    try:
        current_url = driver.current_url or ""
    except Exception:
        current_url = ""

    if "topsurveys.app" not in current_url.lower():
        return None

    try:
        has_screen = bool(driver.execute_script(
            "return !!(document.querySelector('div.phone-verification-container')"
            " && document.querySelector('input.phone-number-input'));"
        ))
    except Exception:
        has_screen = False

    if not has_screen:
        return None

    log_info("[PHONE_VERIF]", "Écran vérification téléphone détecté — résolution du numéro")

    account_id = getattr(driver, "_survey_account_id", None)
    log_info("account_id", f"account_id: {account_id}")
    api_key_5sim = (os.getenv("FIVESIM_API_KEY") or "").strip()

    phone = None
    if api_key_5sim and account_id:
        try:
            from Survey.fivesim_client import buy_number, reuse_number
            from State.account_state import load_state
            state = load_state(account_id)
            stored_phone = (state.get("fivesim_phone") or "").strip()
            if stored_phone:
                log_debug("[PHONE_VERIF]", f"Tentative reuse numéro existant: {stored_phone}")
                phone, _ = reuse_number(account_id, stored_phone)
            else:
                log_debug("[PHONE_VERIF]", "Aucun numéro en state → achat nouveau")
                phone, _ = buy_number(account_id)
        except Exception as e:
            log_info("[PHONE_VERIF]", f"5sim indisponible ({e}) — fallback ACCOUNT_PHONE")

    if not phone:
        log_info("[PHONE_VERIF]", "Aucun numéro disponible (5sim + ACCOUNT_PHONE absents) — abandon")
        return False

    log_info("[PHONE_VERIF]", "Saisie du numéro de téléphone")

    try:
        inp = driver.find_element(By.CSS_SELECTOR, "input.phone-number-input")
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", inp)
        inp.click()
        inp.clear()
        inp.send_keys(phone)
        log_debug("[PHONE_VERIF]", f"Numéro saisi: {phone}")
        time.sleep(0.5)
    except Exception as e:
        log_info("[PHONE_VERIF]", f"Saisie téléphone échouée: {e}")
        return False

    try:
        btn = driver.find_element(
            By.CSS_SELECTOR,
            "div.phone-verification-container button.p-btn--fill"
        )
    except Exception:
        log_info("[PHONE_VERIF]", "Bouton Suivant introuvable après saisie")
        return False

    if _env_truthy("CTA_INTERCEPT_ONLY"):
        is_disabled = btn.get_attribute("disabled") is not None
        status = "disabled" if is_disabled else "enabled"
        log_info("[PHONE_VERIF]", f"CTA_INTERCEPT_ONLY — bouton={status}, interception OK sans navigation")
        return True

    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
        driver.execute_script("arguments[0].click();", btn)
        log_info("[PHONE_VERIF]", "Bouton Suivant cliqué")
        time.sleep(1.0)
        return True
    except Exception as e:
        log_info("[PHONE_VERIF]", f"Clic Suivant échoué: {e}")
        return False


def _handle_pin_verification(driver):
    """
    Détecte et traite l'écran de saisie du code PIN à 6 chiffres (après vérification téléphone)
    sur topsurveys.app.

    Critères DOM stricts : div.phone-verification-container ET 6 input.pin-input-item présents.
    Scoped topsurveys.app uniquement.

    Retourne :
      True  — PIN saisi + bouton Confirmer cliqué
      False — écran détecté mais order_id absent ou timeout 5sim ou erreur
      None  — écran absent (continuer le flux normal)
    """
    try:
        current_url = driver.current_url or ""
    except Exception:
        current_url = ""

    if "topsurveys.app" not in current_url.lower():
        return None

    try:
        pin_inputs = driver.find_elements(By.CSS_SELECTOR,
            "div.phone-verification-container input.pin-input-item")
    except Exception:
        return None

    if len(pin_inputs) < 6:
        return None

    log_info("[PIN_VERIF]", "Écran PIN détecté (6 inputs pin-input-item)")

    # Résolution de l'order_id : account_state en priorité, env en fallback
    account_id = getattr(driver, "_survey_account_id", None)
    order_id = ""
    if account_id:
        try:
            from State.account_state import load_state
            state = load_state(account_id)
            order_id = (state.get("fivesim_order_id") or "").strip()
            log_debug("[PIN_VERIF]", f"order_id depuis account_state: {order_id}")
        except Exception as e:
            log_debug("[PIN_VERIF]", f"load_state échoué: {e}")

    if not order_id:
        order_id = (os.getenv("FIVESIM_ORDER_ID") or "").strip()
        if order_id:
            log_info("[PIN_VERIF]", "Fallback FIVESIM_ORDER_ID (env statique)")

    if not order_id:
        log_info("[PIN_VERIF]", "order_id introuvable (account_state + env) — abandon")
        return False

    try:
        from Survey.fivesim_client import poll_sms_code
        pin_code = poll_sms_code(order_id)
    except Exception as e:
        log_info("[PIN_VERIF]", f"poll_sms_code échoué: {e}")
        return False

    if not pin_code:
        log_info("[PIN_VERIF]", "Code PIN non reçu dans le délai imparti (60s) — abandon")
        return False

    log_info("[PIN_VERIF]", f"Code PIN reçu ({len(pin_code)} chiffres) — saisie")

    try:
        pin_inputs = driver.find_elements(By.CSS_SELECTOR,
            "div.phone-verification-container input.pin-input-item")
        for i, digit in enumerate(pin_code[:6]):
            inp = pin_inputs[i]
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", inp)
            inp.click()
            inp.send_keys(digit)
            time.sleep(0.1)
    except Exception as e:
        log_info("[PIN_VERIF]", f"Saisie PIN échouée: {e}")
        return False

    # Attendre que le bouton Confirmer soit enabled (3s max)
    confirm_btn = None
    for _ in range(30):
        try:
            btn = driver.find_element(By.CSS_SELECTOR,
                "div.phone-verification-container button.p-btn--fill")
            if btn.get_attribute("disabled") is None:
                confirm_btn = btn
                break
        except Exception:
            pass
        time.sleep(0.1)

    if confirm_btn is None:
        log_info("[PIN_VERIF]", "Bouton Confirmer toujours désactivé après 3s — abandon")
        return False

    if _env_truthy("CTA_INTERCEPT_ONLY"):
        log_info("[PIN_VERIF]", "CTA_INTERCEPT_ONLY — interception OK, bouton Confirmer enabled, pas de clic")
        return True

    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", confirm_btn)
        driver.execute_script("arguments[0].click();", confirm_btn)
        log_info("[PIN_VERIF]", "Bouton Confirmer cliqué")
        time.sleep(1.0)
    except Exception as e:
        log_info("[PIN_VERIF]", f"Clic Confirmer échoué: {e}")
        return False

    # Finaliser la commande 5sim (best-effort)
    try:
        from Survey.fivesim_client import finish_order
        finish_order(order_id)
    except Exception:
        pass

    return True


def _should_skip_post_actions_navigation(
    driver,
    question_blocks: list[dict],
    *,
    before_url: str | None = None,
    before_sig=None,
) -> bool:
    """
    Garde-fou minimal: certains blocs avancent automatiquement après le clic
    réponse (ex: Walr cardsort, StudyStream button.choice, QARTS autosubmit,
    Askia auto-navigation via tr.myresponse JS handler).
    La routine CTA post-actions ne doit pas tourner, sinon elle peut cliquer
    sur l'écran suivant et soumettre prématurément.
    """
    # ── Détection auto-navigation post-dispatch (critère purement DOM-observable) ──
    # Si l'URL a changé entre le clic réponse et l'appel CTA, la navigation a déjà
    # eu lieu — on ne doit pas cliquer le CTA, quel que soit le provider.
    # Pour les changements DOM-only (SPA sans changement d'URL), on exige en plus
    # la présence du marqueur Askia (form[action*="AskiaExt.dll"]) pour éviter les
    # faux-positifs sur les plateformes qui mutent le DOM sur sélection radio.
    if before_url is not None:
        try:
            url_changed = driver.current_url != before_url
            if url_changed:
                log_info("[AUTONAV]", "URL changée après clic radio → skip CTA (navigation déjà effectuée)")
                return True
        except Exception:
            pass
        if before_sig is not None:
            try:
                import Management.redirect_watcher as _rw
                if _rw._dom_signature(driver) != before_sig:
                    if driver.find_elements(By.CSS_SELECTOR, "form[action*='AskiaExt.dll']"):
                        # Guard: si la page contient un widget ranking Askia (adc-ranking-isotope),
                        # le changement DOM est une animation isotope (translate3d), PAS une navigation.
                        # Dans ce cas, ne pas skip le CTA — la page attend le clic "Suivant".
                        has_ranking_widget = bool(
                            driver.find_elements(By.CSS_SELECTOR, "div.adc-ranking-isotope")
                        )
                        if has_ranking_widget:
                            log_info("[ASKIA_AUTONAV]", "DOM changé mais widget ranking détecté → animation isotope, CTA requis")
                        else:
                            log_info("[ASKIA_AUTONAV]", "DOM changé après clic radio Askia → skip CTA")
                            return True
            except Exception:
                pass
            
    for block in question_blocks or []:
        try:
            ctx = block.get("context") if isinstance(block, dict) else None
            if isinstance(ctx, dict) and (
                ctx.get("walr_cardsort") is True or ctx.get("studystream_auto_advance") is True
            ):
                return True
        except Exception:
            continue

    # QARTS autosubmit: cliquer une option radio déclenche la navigation directement.
    for block in question_blocks or []:
        try:
            ctx = block.get("context") if isinstance(block, dict) else None
            if isinstance(ctx, dict) and ctx.get("qarts_autosubmit") is True:
                log_info("[QARTS_AUTOSUBMIT]", "autosubmit=true → skip CTA (navigation déjà déclenchée par le clic)")
                return True
        except Exception:
            continue

    # Savanta JQM carousel : skip CTA tant que tous les items ne sont pas répondus.
    # Condition : carousel-index < carousel-total (both present dans le DOM).
    for block in question_blocks or []:
        try:
            ctx = block.get("context") if isinstance(block, dict) else None
            if not (isinstance(ctx, dict) and ctx.get("savanta_jqm_carousel") is True):
                continue
            try:
                index_str = driver.execute_script(
                    "var s=document.querySelector('span.carousel-index'); return s ? s.textContent.trim() : null;"
                )
                total_str = driver.execute_script(
                    "var s=document.querySelector('span.carousel-total'); return s ? s.textContent.trim() : null;"
                )
                if index_str and total_str and index_str.strip() != total_str.strip():
                    print(
                        f"[SAVANTA_JQM_CAROUSEL] carousel not done ({index_str}/{total_str}) — skip CTA"
                    )
                    return True
            except Exception:
                pass
            break
        except Exception:
            continue

    # Confirmit cf-hrs-single carousel : skip CTA sauf sur le dernier card.
    for block in question_blocks or []:
        try:
            ctx = block.get("context") if isinstance(block, dict) else None
            if not (isinstance(ctx, dict) and ctx.get("confirmit_cf_hrs_single_carousel") is True):
                continue
            if not ctx.get("is_last_carousel_item", True):
                log_info(
                    "[CONFIRMIT_CAROUSEL]",
                    f"card {ctx.get('carousel_index', '?') + 1}/{ctx.get('carousel_total', '?')} "
                    f"(non-dernier) → skip CTA",
                )
                return True
            break  # dernier card : ne pas skip
        except Exception:
            continue

    # Critères DOM explicites (défense en profondeur si le contexte est absent)
    try:
        if driver.find_elements(By.CSS_SELECTOR, "#cardSortContainer button.answer-button"):
            return True
        return len(
            driver.find_elements(By.CSS_SELECTOR, "div.question-body-options__choice button.choice")
        ) >= 2
    except Exception:
        return False

def execute_survey_page(driver, account_id, api_key, ctx=None):
    """
    Nouvelle version : capture , demande GPT-4o quoi faire, puis applique l'action.
    """
    import Survey.action_dispatcher as action_dispatcher
    import selenium.webdriver.support.ui
    import Survey.dom_analyzer as dom_analyzer
    import Survey.prompt_builder as prompt_builder
    import Survey.batch_response_parser as batch_response_parser
    import Survey.dom_classifier as dom_classifier
    import Survey.action_dispatcher as action_dispatcher
    import Survey.batch_response_parser as batch_response_parser
    import Survey.input_handler as input_handler
    import Management.redirect_watcher as redirect_watcher
    import Survey.page_snapshot as page_snapshot

    # =========================================================================
    # PATCH: Récupération erreur réseau Chrome (ERR_TUNNEL_CONNECTION_FAILED)
    # Couvre le chemin takeover/attach qui appelle execute_survey_page() directement,
    # sans passer par solve_full_survey(). Le retour (_NET_ERR_*) est ignoré ici :
    # si la récupération échoue, execute_survey_page() verra un DOM vide et
    # abandonnera naturellement via le pipeline normal.
    # =========================================================================
    try:
        from Survey.survey_solver import _recover_from_network_error
        _recover_from_network_error(driver)
    except Exception as _nerr_exc:
        pass  # jamais bloquant

    # =========================================================================
    # PATCH: Écran vérification téléphone TopSurveys ("Courte pause")
    # =========================================================================
    # _phone_result = _handle_phone_verification(driver)
    # if _phone_result is not None:
        # return _phone_result

    # =========================================================================
    # PATCH: Écran saisie code PIN TopSurveys (après vérification téléphone)
    # =========================================================================
    # _pin_result = _handle_pin_verification(driver)
    # if _pin_result is not None:
        # return _pin_result

    # =========================================================================
    # PATCH: Detecter popup TopSurveys
    # =========================================================================
    try:
        _cur = driver.current_url
        if "topsurveys.app" in (_cur or "").lower():
            if _handle_topsurveys_exclusion_popup(driver, account_id):
                reason = "[TOPSURVEYS_POPUP] Popup traite -> continue boucle takeover"
                print(reason)
                _local_pause_before_cta(reason)
                return True
            # Fallback: topsurveys sans mystery box ni popup "Bon travail!"
            # (ex: retour apres fin complete de survey)
            # Les mystery boxes sont gerees en priorite dans _handle_topsurveys_exclusion_popup.
            print("[TOPSURVEYS_LISTING] URL=topsurveys sans popup -> best_survey")
            try:
                import preselection.survey_navigator as survey_navigator
                import time as _time
                survey_navigator.go_to_best_value_survey(driver)
                print("[TOPSURVEYS_LISTING] Navigation vers meilleur survey OK")
            except Exception as _nav_exc:
                print(f"[TOPSURVEYS_LISTING] Erreur navigation: {_nav_exc}")
            return True
    except Exception as e:
        reason = f"[TOPSURVEYS_POPUP] Exception: {e}"
        print(reason)
        _local_pause_before_cta(reason)

    # =========================================================================
    # CAPTCHA: Détection et résolution automatique (no-op si aucun captcha)
    # =========================================================================
    try:
        from captcha.normal_captcha import handle_captcha
        if handle_captcha(driver):
            print("[CAPTCHA] Captcha traité → reprise du flux")
    except Exception as _cap_exc:
        print(f"[CAPTCHA][WARN] {_cap_exc}")

    # DataDome CAPTCHA (DataDomeSliderTask via 2Captcha + injection cookie + refresh)
    try:
        from captcha.datadome_handler import solve_datadome_auto
        if solve_datadome_auto(driver):
            print("[DATADOME] DataDome résolu → reprise du flux")
            return True
    except Exception as _dd_exc:
        print(f"[DATADOME][WARN] {_dd_exc}")

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
    if video_gate_state == "resolved":
        # Radios répondues, CTA direct sans OpenAI (respect CTA_INTERCEPT_ONLY)
        intercept_only = _env_truthy("CTA_INTERCEPT_ONLY")
        try:
            before_url = driver.current_url
            before_sig = redirect_watcher._dom_signature(driver)
            time.sleep(PAUSE_BEFORE_CTA)
            _local_pause_before_cta("video_gate_resolved")
            clicked = input_handler.try_click_navigation_cta_any_context(driver)
            if intercept_only:
                log_info("[VIDEO_GATE]", f"CTA_INTERCEPT_ONLY — clic={'OK' if clicked else 'NOT FOUND'}, pas de navigation")
            elif clicked:
                redirect_watcher.wait_for_navigation_or_dom_change(
                    driver, before_url=before_url, before_sig=before_sig, timeout=10
                )
        except Exception as _vg_cta_e:
            log_debug("[VIDEO_GATE]", f"CTA error (non-bloquant): {_vg_cta_e}")
        return True

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
    if image_only_abort == "random_selected":
        print("[DOM_ONLY_ABORT] image_only_wrapped_inputs random_selected -> CTA direct")
        intercept_only = _env_truthy("CTA_INTERCEPT_ONLY")
        try:
            before_url = driver.current_url
            before_sig = redirect_watcher._dom_signature(driver)
            time.sleep(PAUSE_BEFORE_CTA)
            _local_pause_before_cta("navigation_cta")
            clicked = input_handler.try_click_navigation_cta_any_context(driver)
            if intercept_only:
                print(f"[DOM_ONLY_ABORT] CTA_INTERCEPT_ONLY — clic={'OK' if clicked else 'NOT FOUND'}, pas de navigation")
            elif clicked:
                redirect_watcher.wait_for_navigation_or_dom_change(
                    driver, before_url=before_url, before_sig=before_sig, timeout=10
                )
        except Exception as _cta_e:
            print(f"[DOM_ONLY_ABORT] random_selected CTA error (non-bloquant): {_cta_e}")
        return True

    open_text_image_abort = _budgeted_soft_restart_for_open_text_embedded_image(driver, question_blocks)
    if open_text_image_abort == "restarted":
        return True
    if open_text_image_abort == "budget_exhausted":
        return False

    # =========================================================================
    # CF-CAROUSEL AVEC IMAGE: Traitement Vision API AVANT le flux standard
    # Blocs cf_carousel_item portant un image_url → gpt-4o Vision
    # =========================================================================
    try:
        if question_blocks and _handle_cf_carousel_image_blocks(driver, question_blocks, api_key):
            print("[CF_CAROUSEL_VISION] Blocs traités avec succès -> CTA")
            intercept_only = _env_truthy("CTA_INTERCEPT_ONLY")
            try:
                before_url = driver.current_url
                before_sig = redirect_watcher._dom_signature(driver)
                time.sleep(PAUSE_BEFORE_CTA)
                _local_pause_before_cta("navigation_cta")
                clicked = input_handler.try_click_navigation_cta_any_context(driver)
                if intercept_only:
                    print(f"[CF_CAROUSEL_VISION] CTA_INTERCEPT_ONLY — clic={'OK' if clicked else 'NOT FOUND'}, pas de navigation")
                elif clicked:
                    redirect_watcher.wait_for_navigation_or_dom_change(
                        driver, before_url=before_url, before_sig=before_sig, timeout=10
                    )
            except Exception as _cta_e:
                print(f"[CF_CAROUSEL_VISION] CTA error (non-bloquant): {_cta_e}")
            return True
    except Exception as e:
        print(f"[CF_CAROUSEL_VISION] Exception: {e}")
        import traceback
        traceback.print_exc()

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

    try:
        has_gridclick = bool(driver.execute_script("return !!document.querySelector('.gridclick');"))
    except Exception:
        has_gridclick = False

    for block in (question_blocks or []):
        if (block.get("itype") or "").strip().lower() != "matrix":
            continue

        block_ctx = block.get("context")
        if not isinstance(block_ctx, dict):
            block_ctx = {}
            block["context"] = block_ctx

        wants_active_row = bool(block_ctx.get("focusvision_answers_list")) or has_gridclick
        if not wants_active_row:
            continue

        try:
            active_row = driver.execute_script(
                """
                const el = document.querySelector('.gridclick .item.current .text-content')
                    || document.querySelector('.gridclick .item.current');
                return el ? String(el.textContent || el.innerText || '') : '';
                """
            )
        except Exception:
            active_row = ""

        active_row = re.sub(r"\s+", " ", str(active_row or "")).strip()
        if not active_row:
            continue

        block_ctx["matrix_active_row"] = active_row
        print(
            f"[MATRIX_ACTIVE_ROW] target_id={block.get('target_id', '')} "
            f"row_active={active_row!r}"
        )

    client = openai.OpenAI(api_key=api_key)

    if question_blocks:
        question_blocks_for_batch = prompt_builder.expand_question_blocks_for_batch(question_blocks)

        # Séparation system / user pour activer le prompt caching OpenAI.
        # build_system_prompt() retourne un contenu statique identique entre tous les appels :
        # le cache s'active automatiquement dès le 2e appel (préfixe ≥ 1 024 tokens identiques).
        # Pour vérifier : usage.prompt_tokens_details.cached_tokens > 0 dans la réponse API.
        system_prompt = prompt_builder.build_system_prompt()
        user_prompt = prompt_builder.build_batch_prompt(question_blocks_for_batch, ctx=ctx)

        if (os.getenv("LOG_LEVEL") or "").strip().lower() == "debug":
            print("🧠 [PROMPT→GPT] ===== USER PROMPT =====")
            print(user_prompt[:200000])
            print("[PROMPT→GPT] ===================================")

        instruction_raw = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
        )

        raw_text = instruction_raw.choices[0].message.content or ""
        # contraintes max_select par QID (doit matcher le build_batch_prompt)
        qid_constraints = {
            f"Q{i}": (
                len([c for c in (b.get("cards") or []) if str(c or "").strip()])
                if (str((b.get("kind") or "")).strip().lower() == "cardsort")
                else int((b.get("max_select", 1) or 1))
            )
            for i, b in enumerate(question_blocks_for_batch, start=1)
        }
        if (os.getenv("LOG_LEVEL") or "").strip().lower() == "debug":
            print(f"[survey_executor][debug] qid_constraints={qid_constraints}")

        #  Meta par QID (pour sanitizer avec les options du DOM)
        qid_meta = {
            f"Q{i}": {
                "question": (b.get("question") or ""),
                "itype": (b.get("itype") or ""),
                "options": (b.get("options") or []),
                "max_select": int(b.get("max_select", 1) or 1),
                "target_id": (b.get("target_id") or ""),
                "kind": (b.get("kind") or ""),
                "cards": (b.get("cards") or []),
                "buckets": (b.get("buckets") or []),
                "context": (b.get("context") or {}),
            }
            for i, b in enumerate(question_blocks_for_batch, start=1)
        }

        actions = batch_response_parser.parse_batch_response(raw_text, constraints=qid_constraints, qid_meta=qid_meta)
        actions = batch_response_parser.sanitize_actions(actions, qid_meta=qid_meta)

        # Snapshot pré-dispatch : sert à détecter si le clic réponse a déjà
        # déclenché une navigation (ex: Askia tr.myresponse auto-submit).
        try:
            _pre_dispatch_url = driver.current_url
            _pre_dispatch_sig = redirect_watcher._dom_signature(driver)
        except Exception:
            _pre_dispatch_url = None
            _pre_dispatch_sig = None

        #  "plan" (multi actions) + anti-double-fallback par action
        result = action_dispatcher.execute_actions_plan(driver, actions, stop_on_navigation=True)

        # Record answered Q/R in session context for coherence (non-blocking)
        if ctx is not None:
            try:
                for action in (actions or []):
                    qid = (action.get("qid") or "") if isinstance(action, dict) else ""
                    target_id_act = (action.get("target_id") or "") if isinstance(action, dict) else ""
                    meta = qid_meta.get(qid) if qid else None
                    if not meta and target_id_act:
                        meta = next(
                            (m for m in qid_meta.values() if m.get("target_id") == target_id_act),
                            None,
                        )
                    if meta:
                        ctx.record(
                            question=meta.get("question", ""),
                            options=meta.get("options") or [],
                            answer=(action.get("value") or "") if isinstance(action, dict) else "",
                        )
                    else:
                        ctx.record(
                            question=qid or "unknown",
                            options=[],
                            answer=(action.get("value") or "") if isinstance(action, dict) else "",
                        )
            except Exception as e:
                print(f"[SURVEY_CTX] record error: {e}")
                
        # --- Post-actions CTA nav (sauf auto-navigation déjà déclenchée) ---
        if _should_skip_post_actions_navigation(
            driver,
            question_blocks,
            before_url=_pre_dispatch_url,
            before_sig=_pre_dispatch_sig,
        ):
            print("[AUTONAV] skip post-actions CTA navigation (auto-navigation déjà effectuée)")
        elif _has_unfilled_required_inputs(driver):
            # Des champs required sont encore vides → ne pas soumettre le formulaire maintenant.
            # La boucle externe relancera execute_survey_page pour remplir les champs restants.
            log_info("[CTA_SKIP]", "Champs required non remplis → skip CTA (formulaire multi-champs)")
        else:
            try:
                before_url = driver.current_url
                before_sig = redirect_watcher._dom_signature(driver)  # ou recalc local si tu veux optimiser

                # iframe-safe
                time.sleep(PAUSE_BEFORE_CTA)  # laisser les réponses se stabiliser dans le DOM avant de naviguer
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
                        _kind = "URL" if changed.url_changed else "DOM-only (SPA)"
                        print(f" Navigation détectée après CTA ({_kind}).")
            except Exception:
                pass

        return result    
    else:
        dom_only_abort = _budgeted_dom_only_abort_for_image_eval(driver)
        if dom_only_abort == "restarted":
            return True
        if dom_only_abort == "budget_exhausted":
            return False

        disq_abort = _budgeted_disqualification_restart(driver)
        if disq_abort == "restarted":
            return True
        if disq_abort == "budget_exhausted":
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
            ".footer #next",                   # MetrixLab/Toluna intro CTA icon-only (div#next)
            "#next.next",                      # MetrixLab/Toluna variant
            "#cm-NextButton",                    # CMIX
            ".cm-navigation-next-button",        # CMIX alt
            ".cf-question--info button.cf-navigation-next",  # Confirmit intro CTA inline
            ".cf-page__question-list button.cf-navigation-next",  # Confirmit fallback scope
            "button.cf-navigation__button.cf-navigation-next",  # Confirmit/Forsta nav button explicit
            ".cf-question--info button.cf-navigation-ok",  # Confirmit/Forsta info gate "OK"
            ".cf-page__question-list button.cf-navigation-ok",  # Confirmit/Forsta fallback scope
            "button.cf-navigation__button.cf-navigation-ok",  # Confirmit/Forsta nav button explicit
            "#btn_continue",                     # Decipher
            "#btnContinue",                      # navigatorsurveys/PureSpectrum routing page
            "input.continue",                    # Decipher alt
            "[data-role='next']",                # Generic data-role
            "#btn_next",                         # AreYouNet (img inside <a>)
            '[data-testid="start-button"]',      # Quantilope coversheet
            "#bnNext", # Primis/Primisoft (bouton "Suivant")
            "#consent-button-confirm",        # Consent modal RGPD (Toluna-like UI)
            "#consent-button-confirm",        # Consent modal RGPD (Toluna-like UI)
            "input.i-contbtn",                   # IntelliSurvey (value vide, id=contbtn)
            "input[type='image'][name='next']",  # Snap Survey / bouton image nav "next"
        ]        
        try:
            _local_pause_before_cta("cta_only_fallback")
            
            # Phase 1: CSS selectors directs (frameworks connus)
            log_debug("[CTA_FALLBACK]", f"Phase 1: testing {len(NAV_BUTTON_SELECTORS)} selectors")
            for selector in NAV_BUTTON_SELECTORS:
                try:
                    btn = driver.find_element(By.CSS_SELECTOR, selector)
                    log_debug("[CTA_FALLBACK]", f"Selector {selector} found: {btn.tag_name}")
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
                    log_debug("[CTA_FALLBACK]", f"{selector}: is_displayed={is_disp}, _is_visible_js={is_vis_js}")
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
                    log_debug("[CTA_FALLBACK]", f"Selector {selector} FAILED: {type(e).__name__}")
                    continue  #  non , essayer le suivant
            
            # Phase 2: Recherche par texte (fallback existant)
            clicked = (
                input_handler.click_cta_strong_any_context(driver, text="accepter")
                or input_handler.click_cta_strong_any_context(driver, text="continuer")
                or input_handler.click_cta_strong_any_context(driver, text="accept")
                or input_handler.click_cta_strong_any_context(driver, text="agree")
                or input_handler.click_cta_strong_any_context(driver, text="let's go")
                or input_handler.click_cta_strong_any_context(driver, text="lets go")
                or input_handler.click_cta_strong_any_context(driver, text="next")
                or input_handler.click_cta_strong_any_context(driver, text="suivant")
                or input_handler.click_cta_strong_any_context(driver, text="démarrer")
                or input_handler.click_cta_strong_any_context(driver, text="commencer")
                or input_handler.click_cta_strong_any_context(driver, text="confirmer")
                or input_handler.click_cta_strong_any_context(driver, text="confirmez")
                or input_handler.click_cta_strong_any_context(driver, text="confirm")
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
            # Phase 3: CTA structurel (mrIWeb mrNext, etc.) via scorer
            if not clicked:
                cta_intercept_only = _env_truthy("CTA_INTERCEPT_ONLY", "0")
                try:
                    if cta_intercept_only:
                        from selenium.webdriver.common.by import By as _By
                        _btn = None
                        try:
                            _btn = driver.find_element(_By.CSS_SELECTOR, "input[type='submit'][name='_NNext']")
                        except Exception:
                            pass
                        if _btn and _btn.is_displayed():
                            driver.execute_script("arguments[0].dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true}))", _btn)
                            clicked = True
                            log_debug("[CTA_FALLBACK]", "Phase 3: mrIWeb structural CTA intercepted (CTA_INTERCEPT_ONLY)")
                        else:
                            log_debug("[CTA_FALLBACK]", "Phase 3: CTA_INTERCEPT_ONLY — structural CTA not found")
                    else:
                        clicked = bool(input_handler.try_click_navigation_cta_any_context(driver))
                        if clicked:
                            log_debug("[CTA_FALLBACK]", "Phase 3: structural CTA clicked via try_click_navigation_cta_any_context")
                except Exception as _e3:
                    log_debug("[CTA_FALLBACK]", f"Phase 3 error: {type(_e3).__name__}: {_e3}")
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

        # ----------------------------------------------------------------
        # WAIT_PAGE : détection des pages transitoires "veuillez patienter"
        # (ex: sample.savanta.com "Validating details. Please do not refresh")
        # → attendre une redirection automatique, sinon forcer un refresh.
        # Actif uniquement en mode proxy haute latence (PROXY_LATENCY_MODE=1).
        # ----------------------------------------------------------------
        if _env_truthy("PROXY_LATENCY_MODE", "0"):
            try:
                _WAIT_SIGNALS = [
                    "please wait", "veuillez patienter",
                    "please do not refresh", "do not refresh",
                    "validating", "validation en cours",
                    "just a moment", "un instant",
                ]
                _wp_src = (driver.page_source or "").lower()
                if any(sig in _wp_src for sig in _WAIT_SIGNALS):
                    _wp_before_url = driver.current_url
                    print(f"[WAIT_PAGE] Page transitoire détectée ({_wp_before_url}) → attente redirection (10s max)")
                    for _ in range(10):
                        time.sleep(1)
                        try:
                            if driver.current_url != _wp_before_url:
                                print(f"[WAIT_PAGE] Redirection automatique détectée → {driver.current_url}")
                                return True
                        except Exception:
                            break
                    print("[WAIT_PAGE] Pas de redirection automatique → refresh forcé")
                    try:
                        driver.refresh()
                        time.sleep(5)
                    except Exception as _wp_re:
                        print(f"[WAIT_PAGE][WARN] Refresh échoué: {_wp_re}")
                    return True
            except Exception as _wp_e:
                print(f"[WAIT_PAGE][WARN] Détection échouée: {_wp_e}")

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