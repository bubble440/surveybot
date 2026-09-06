# ------------------------------------------------------------
# DOM Classifier
#
# Rôle :
# - Décider AVANT OpenAI ce que représente la page courante
# - Mapper DOM → itype logique → handler cible
#
# Principe :
# - Déterministe
# - Zéro IA
# - Extensible (100+ bots)
# ------------------------------------------------------------

from __future__ import annotations
import re, time, logging
from urllib.parse import urlparse, parse_qs
from typing import Callable, Optional, Dict, Any
from Survey.frame_utils import iter_frame_chains, switch_to_frame_chain

logger = logging.getLogger("dom_classifier")




# ============================================================
# Utils
# ============================================================
_IFRAME_SRC_RE = re.compile(r'<iframe[^>]+src="([^"]+)"', re.IGNORECASE)

def _has_visible_recaptcha_challenge(dom_html: str) -> bool:
    h = dom_html.lower()

    # 1) bframe => challenge visible (quasi certain)
    if "recaptcha/api2/bframe" in h:
        return True

    # 2) anchor iframe: visible si size != invisible
    for src in _IFRAME_SRC_RE.findall(dom_html):
        s = src.lower()
        if "recaptcha" in s and "api2/anchor" in s:
            try:
                q = parse_qs(urlparse(src).query)
                size = (q.get("size", [""])[0] or "").lower()
            except Exception:
                size = ""
            if size and size != "invisible":
                return True
    # 3) widget visible classique
    if 'class="g-recaptcha"' in h or "g-recaptcha" in h and "data-sitekey" in h:
        return True
    if "recaptcha-checkbox" in h:
        return True

    return False


def _has_visible_hcaptcha_challenge(dom_html: str) -> bool:
    h = dom_html.lower()
    if "hcaptcha.com" not in h and "h-captcha" not in h and "data-hcaptcha" not in h:
        return False

    if 'class="h-captcha"' in h or ("data-sitekey" in h and "hcaptcha" in h):
        return True

    for src in _IFRAME_SRC_RE.findall(dom_html):
        s = src.lower()
        if "hcaptcha.com" in s:
            return True

    return False


def _has_visible_turnstile_challenge(dom_html: str) -> bool:
    h = dom_html.lower()
    if "challenges.cloudflare.com" not in h and "cf-turnstile" not in h:
        return False
    if "cf-turnstile" in h and "data-sitekey" in h:
        return True
    for src in _IFRAME_SRC_RE.findall(dom_html):
        if "challenges.cloudflare.com" in src.lower():
            return True
    return False


def is_captcha_page_strict(dom_html: str) -> bool:
    """
    Politique stricte: on ne classe captcha QUE si on observe un challenge visible.
    """
    return (
        _has_visible_recaptcha_challenge(dom_html)
        or _has_visible_hcaptcha_challenge(dom_html)
        or _has_visible_turnstile_challenge(dom_html)
    )

def _norm_lc(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()

def _page_text_lc(driver) -> str:
    """
    Texte visible "utile" pour les heuristiques.
    Ignore le footer (Privacy Policy / General Terms) pour éviter les faux positifs.
    """
    page = driver
    try:
        txt = page.evaluate("""() => {
            const root = document.querySelector('#survey') || document.querySelector('main') || document.body;
            if (!root) return '';
            const clone = root.cloneNode(true);

            clone.querySelectorAll('script, style, noscript, template').forEach(n => n.remove());
            clone.querySelectorAll('.ng-hide, [aria-hidden="true"]').forEach(n => n.remove());
            clone.querySelectorAll('footer, [id*="footer"], [class*="footer"], [id*="Footer"], [class*="Footer"]').forEach(n => n.remove());

            return (clone.innerText || '').trim();
        }""")
        txt = _norm_lc(txt or "")
        return txt[:5000] if len(txt) > 5000 else txt
    except Exception:
        try:
            return _norm_lc(page.content() or "")
        except Exception:
            return ""

# ============================================================
# Signatures DOM (détecteurs)
# ============================================================

def _is_formal_survey_question_page(driver) -> bool:
    """
    Détecte une page de question de sondage formelle.
    Utilisé pour EXCLURE les pages de sondage du consent_screen.
    """
    page = driver
    try:
        return bool(page.evaluate(r"""() => {
            // --- Decipher/FocusVision ---
            const decipherQ = document.querySelector('.question[role="radiogroup"]');
            if (decipherQ) {
                const ansInputs = decipherQ.querySelectorAll('input[name^="ans"]');
                if (ansInputs.length >= 2) return true;
            }

            const surveyBody = document.querySelector('.survey-body');
            if (surveyBody) {
                const questions = surveyBody.querySelectorAll('.question');
                for (const q of questions) {
                    const ansInputs = q.querySelectorAll('input[name^="ans"]');
                    if (ansInputs.length >= 2) return true;
                }
            }

            // --- Confirmit/Forsta ---
            const sqQuestions = document.querySelectorAll('.sq-question, [class*="sq-question"]');
            for (const sq of sqQuestions) {
                const radios = sq.querySelectorAll('input[type="radio"], input[type="checkbox"]');
                if (radios.length >= 2) return true;
            }

            // --- YouGov single-choice question layout ---
            const ygFieldsets = document.querySelectorAll(
                '.question-container fieldset.question-single, fieldset.question.question-single'
            );
            for (const fs of ygFieldsets) {
                const legend = fs.querySelector('legend.question-text, .question-text');
                if (!legend || (legend.innerText || '').trim().length < 8) continue;

                const radios = fs.querySelectorAll('input[type="radio"]');
                if (radios.length < 2) continue;

                let textLabels = 0;
                for (const radio of radios) {
                    const id = radio.id;
                    if (!id) continue;
                    const lab = fs.querySelector(`label[for="${id}"] .label-text, label[for="${id}"]`);
                    if (lab && (lab.innerText || '').trim().length > 5) {
                        textLabels++;
                    }
                }
                if (textLabels >= 2) return true;
            }

            // --- Generic survey question structure ---
            const surveyContainer = document.querySelector('#survey, .survey-container, [class*="survey"]');
            if (surveyContainer) {
                const qText = surveyContainer.querySelector('.question-text, h1.question-text, [class*="question-text"]');
                if (qText) {
                    const qContainer = qText.closest('.question') || qText.closest('[id^="question_"]') || qText.parentElement;
                    if (qContainer) {
                        const inputs = qContainer.querySelectorAll('input[type="radio"], input[type="checkbox"]');
                        if (inputs.length >= 2) {
                            let hasTextLabels = 0;
                            for (const inp of inputs) {
                                const id = inp.id;
                                if (id) {
                                    const lab = document.querySelector(`label[for="${id}"]`);
                                    if (lab && (lab.innerText || '').trim().length > 5) hasTextLabels++;
                                }
                            }
                            if (hasTextLabels >= 2) return true;
                        }
                    }
                }
            }

            // --- Qualtrics ---
            const qBody = document.querySelector('.QuestionBody, [class*="QuestionBody"]');
            if (qBody) {
                const radios = qBody.querySelectorAll('input[type="radio"], input[type="checkbox"], [role="radio"], [role="checkbox"]');
                if (radios.length >= 2) return true;
            }

            // --- Toluna / Confirmit Wix natif (/wix/2/) ---
            const wixFieldset = document.querySelector('fieldset[id^="fieldset_"]');
            if (wixFieldset && wixFieldset.querySelector('table.confirmit-table')) {
                const wixRadios = wixFieldset.querySelectorAll('input[type="radio"]');
                if (wixRadios.length >= 2) {
                    let wixTextLabels = 0;
                    for (const r of wixRadios) {
                        if (!r.id) continue;
                        const lab = wixFieldset.querySelector(
                            `td.answer_label_ng label[for="${r.id}"],` +
                            `td.alternating_answer_label_ng label[for="${r.id}"]`
                        );
                        if (lab && (lab.innerText || '').trim().length > 0) wixTextLabels++;
                    }
                    if (wixTextLabels >= 2) return true;
                }
            }

            // --- SurveyJS classic (sv_* framework) ---
            const svRadioGroups = document.querySelectorAll('fieldset.sv_qcbc[role="radiogroup"]');
            for (const fs of svRadioGroups) {
                const svRadios = fs.querySelectorAll('input.sv_q_radiogroup_control_item[type="radio"]');
                if (svRadios.length < 2) continue;

                let svTextLabels = 0;
                for (const r of svRadios) {
                    const label = (r.getAttribute('aria-label') || '').replace(/<[^>]*>/g, '').trim();
                    if (label.length > 3) svTextLabels++;
                }
                if (svTextLabels < 2) continue;

                const qRoot = fs.closest('.sv_q.sv_qstn') || fs.closest('[id^="sq_"]');
                const hasRequiredValidation = !!(qRoot && qRoot.querySelector('.sv_q_erbox, [role="alert"]'));
                if (hasRequiredValidation) return true;
            }

            return false;
        }"""))
    except Exception:
        return False


def is_consent_screen(driver) -> bool:
    """
    Détecte un écran de consentement (cookies / RGPD) *bloquant*.
    """
    page = driver

    # Hard negative: un écran "Start/Commencer" n'est pas un consent_screen.
    try:
        if is_start_screen(driver):
            return False
    except Exception:
        pass

    # Hard negative pour les pages de question de sondage formelle.
    try:
        if _is_formal_survey_question_page(driver):
            return False
    except Exception:
        pass

    # Confirmit/Forsta "welcome / GDPR info" pages
    def _is_confirmit_welcome_gdpr_with_next() -> bool:
        try:
            return bool(page.evaluate(r"""() => {
            const hasConfirmit = !!document.querySelector('body.cf-page, form.cf-page__form, .cf-page__main, .cf-page__question-list');
            if (!hasConfirmit) return false;
                const infoBlocks = document.querySelectorAll(
                    '.cf-question--info#iWelcome, .cf-question--info#iWelcomeMRS, .cf-question--info#iWelcomeGDPR, .cf-question--info#qGDPRConsent, .cf-question--info[id^="iWelcome"]'
                );
                if (infoBlocks.length < 1) return false;

                const nextBtn = document.querySelector('#navButtons .cf-navigation-next, button.cf-navigation-next, .cf-navigation__button.cf-navigation-next');
                if (!nextBtn) return false;

                const isVisible = (el) => {
                    try {
                        const s = window.getComputedStyle(el);
                        if (!s || s.display === 'none' || s.visibility === 'hidden') return false;
                        const r = el.getBoundingClientRect();
                        return r && r.width > 10 && r.height > 10;
                    } catch(_) { return false; }
                };
                const answerables = Array.from(document.querySelectorAll(
                    'input[type="radio"], input[type="checkbox"], select, textarea, input[type="text"], input[type="number"], input[type="email"], input[type="tel"], input[type="search"]'
                )).filter(isVisible);
                if (answerables.length > 0) return false;

                const t = (document.querySelector('.cf-page__question-list')?.innerText || document.body.innerText || '').toLowerCase();
                const kw = ['rgpd','gdpr','données','adresse ip','protection des données','confidentielle','market research society','prêt','c\'est parti','c’est parti'];
                if (!kw.some(k => t.includes(k))) return false;

                return true;
            }"""))
        except Exception:
            return False

    if _is_confirmit_welcome_gdpr_with_next():
        return True

    # Survey-final redirect gate (Angular)
    def _is_survey_redirect_gate() -> bool:
        try:
            return bool(page.evaluate(r"""() => {
                const isVisible = (el) => {
                    if (!el) return false;
                    const s = window.getComputedStyle(el);
                    if (!s || s.display === 'none' || s.visibility === 'hidden') return false;
                    const r = el.getBoundingClientRect();
                    return !!(r && r.width > 20 && r.height > 20);
                };

                const root = document.querySelector('app-survey-final, app-survey-final-page, [class*="survey-final"]');
                const txt = ((root?.innerText || document.body?.innerText || '')).toLowerCase();

                const hasRedirectSignal = [
                  'vous y êtes presque', 'vous y etes presque', 'you are almost there',
                  'redirigé', 'redirected', 'qualification'
                ].some(k => txt.includes(k));
                if (!hasRedirectSignal) return false;

                const ctas = Array.from(document.querySelectorAll(
                  'button.next_btn, button[type="submit"], button, a[role="button"], input[type="submit"]'
                )).filter(isVisible);
                if (ctas.length < 1) return false;

                const answerables = Array.from(document.querySelectorAll(
                  'input[type="radio"], input[type="checkbox"], select, textarea, input[type="text"], input[type="number"], input[type="email"], input[type="tel"], input[type="search"]'
                )).filter(isVisible);
                return answerables.length === 0;
            }"""))
        except Exception:
            return False

    if _is_survey_redirect_gate():
        return True

    # Walr country-routing interstitial
    def _is_walr_country_routing_gate() -> bool:
        try:
            return bool(page.evaluate(r"""() => {
                const isVisible = (el) => {
                    if (!el) return false;
                    const s = window.getComputedStyle(el);
                    if (!s || s.display === 'none' || s.visibility === 'hidden') return false;
                    const r = el.getBoundingClientRect();
                    return !!(r && r.width > 10 && r.height > 10);
                };

                const cRadios = document.querySelectorAll('.cRadio');
                if (cRadios.length < 10) return false;

                const cRef = document.querySelector('.cRef');
                if (!cRef) return false;

                const btnNext = document.querySelector('#btnNext');
                if (!isVisible(btnNext)) return false;

                return true;
            }"""))
        except Exception:
            return False

    if _is_walr_country_routing_gate():
        return True

    # Walr intro/final interstitial
    def _is_walr_intro_final_gate() -> bool:
        try:
            return bool(page.evaluate(r"""() => {
                const isVisible = (el) => {
                    if (!el) return false;
                    const s = window.getComputedStyle(el);
                    if (!s || s.display === 'none' || s.visibility === 'hidden') return false;
                    const r = el.getBoundingClientRect();
                    return !!(r && r.width > 10 && r.height > 10);
                };

                const hasWalrFooter = !!document.querySelector('a.logo2link[href*="walr.com"]');
                if (!hasWalrFooter) return false;

                const q = document.querySelector('input[type="hidden"]#Q');
                if (!q || (q.value || '').toUpperCase() !== 'FINAL') return false;

                const btnNext = document.querySelector('#btnNext');
                if (!isVisible(btnNext)) return false;

                const answerables = Array.from(document.querySelectorAll(
                  'input[type="radio"], input[type="checkbox"], select, textarea, input[type="text"], input[type="number"], input[type="email"], input[type="tel"], input[type="search"]'
                )).filter(isVisible);
                if (answerables.length > 0) return false;

                const txt = (document.querySelector('#rsPanelMain')?.innerText || document.body?.innerText || '').toLowerCase();
                const hasIntroSignal = ['veuillez cliquer', 'please click', 'suivant', 'next'].some(k => txt.includes(k));
                return hasIntroSignal;
            }"""))
        except Exception:
            return False

    if _is_walr_intro_final_gate():
        return True

    # Affinnova / NIQ launch interstitial
    def _is_affinnova_launch_gate() -> bool:
        try:
            return bool(page.evaluate(r"""() => {
                const isVisible = (el) => {
                    if (!el) return false;
                    const s = window.getComputedStyle(el);
                    if (!s || s.display === 'none' || s.visibility === 'hidden') return false;
                    const r = el.getBoundingClientRect();
                    return !!(r && r.width > 20 && r.height > 20);
                };

                const launchBtn = document.querySelector('a.launchButton[onclick*="showSurvey" i], a.launchButton');
                if (!isVisible(launchBtn)) return false;

                const blob = ((launchBtn.innerText || '') + ' ' + (document.body?.innerText || '')).toLowerCase();
                const hasLaunchText = ["lancer l'étude", "lancer l etude", 'launch', 'démarrer', 'commencer']
                  .some(k => blob.includes(k));
                if (!hasLaunchText) return false;

                const hasShowSurveyFn = /function\s+showSurvey\s*\(/i.test(document.documentElement?.innerHTML || '');
                if (!hasShowSurveyFn) return false;

                return true;
            }"""))
        except Exception:
            return False

    if _is_affinnova_launch_gate():
        return True

    # --- contrôle explicite type consent ---
    def _has_explicit_consent_control() -> bool:
        markers = {
            "agree", "accept", "consent", "gdpr", "rgpd", "cookie", "cookies",
            "j'accepte", "j accepte", "i agree", "i accept"
        }

        def _is_input_visible(inp) -> bool:
            try:
                if not inp.is_visible():
                    return False
                has_hidden = inp.evaluate(
                    "(el) => !!el.closest('.cky-hide, .ng-hide, [hidden], .hidden, [aria-hidden=\"true\"]')"
                )
                if has_hidden:
                    return False
                r = inp.bounding_box() or {}
                if float(r.get('width', 0) or 0) < 5 or float(r.get('height', 0) or 0) < 5:
                    return False
                return True
            except Exception:
                return False

        try:
            inputs = page.query_selector_all("input[type='checkbox'], input[type='radio']")
            for inp in inputs:
                try:
                    if not _is_input_visible(inp):
                        continue
                    id_ = (inp.get_attribute("id") or "").strip()
                    name = (inp.get_attribute("name") or "").strip()
                    blob = f"{id_} {name}".lower()
                    if any(m in blob for m in markers):
                        return True

                    label_txt = ""
                    if id_:
                        labs = page.query_selector_all(f"label[for='{id_}']")
                        if labs:
                            label_txt = labs[0].inner_text() or ""
                    if not label_txt:
                        try:
                            lab = inp.query_selector("xpath=ancestor::label[1]")
                            if lab:
                                label_txt = lab.inner_text() or ""
                        except Exception:
                            pass

                    label_txt = _norm_lc(label_txt)
                    if label_txt and any(m in label_txt for m in markers):
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

    # --- overlay CMP (OneTrust / Didomi / Quantcast / TrustArc / Cookiebot, etc.) ---
    def _has_blocking_cmp_overlay() -> bool:
        js = r"""() => {
        const vw = Math.max(320, window.innerWidth || 0);
        const vh = Math.max(240, window.innerHeight || 0);
        const minArea = vw * vh * 0.12;

        const selectors = [
          '#onetrust-banner-sdk', '#onetrust-consent-sdk',
          '.qc-cmp2-container', '.qc-cmp2-ui', '.qc-cmp-cleanslate',
          '.didomi-popup-container', '#didomi-popup',
          '.truste_overlay', '.truste_box_overlay',
          '#CybotCookiebotDialog', '#CookiebotWidget',
          '.cc-window', '.cookie-banner', '.cookie-consent', '.cookie-notice',
          '[role="alertdialog"]', '[role="dialog"][aria-modal="true"]', '[aria-modal="true"]'
        ];

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

        const candidates = Array.from(document.querySelectorAll(selectors.join(',')));
        for (const el of candidates){
          if (!isVisible(el)) continue;
          const r = el.getBoundingClientRect();
          if ((r.width * r.height) < minArea) continue;
          const t = (el.innerText || '').toLowerCase();
          if (!kw.some(k => t.includes(k))) {
            const blob = ((el.id||'') + ' ' + (el.className||'')).toLowerCase();
            if (!(blob.includes('onetrust') || blob.includes('qc-cmp') || blob.includes('didomi') || blob.includes('truste') || blob.includes('cookiebot')))
              continue;
          }
          return true;
        }

        return false;
        }"""
        try:
            return bool(page.evaluate(js))
        except Exception:
            return False

    # 1) Priorité: CMP overlay réellement bloquant
    if _has_blocking_cmp_overlay():
        return True

    # 2) Détection "contrôle explicite" avec contexte cookies/RGPD/CMP.
    def _cmp_container_exists_anywhere() -> bool:
        try:
            return bool(page.evaluate("""() => {
                const sels = [
                  '#onetrust-banner-sdk', '#onetrust-consent-sdk',
                  '.qc-cmp2-container', '.qc-cmp2-ui', '.qc-cmp-cleanslate',
                  '.didomi-popup-container', '#didomi-popup',
                  '.truste_overlay', '.truste_box_overlay',
                  '#CybotCookiebotDialog', '#CookiebotWidget',
                  '.cc-window', '.cookie-banner', '.cookie-consent', '.cookie-notice'
                ];
                const vw = Math.max(320, window.innerWidth || 0);
                const vh = Math.max(240, window.innerHeight || 0);

                function isVisible(el) {
                    if (!el) return false;
                    try {
                        const s = window.getComputedStyle(el);
                        if (!s) return false;
                        if (s.display === 'none' || s.visibility === 'hidden') return false;
                        if (parseFloat(s.opacity || '1') < 0.1) return false;
                        const r = el.getBoundingClientRect();
                        if (!r) return false;
                        if (r.width < 50 || r.height < 30) return false;
                        if (r.bottom < 0 || r.right < 0 || r.top > vh || r.left > vw) return false;
                        return true;
                    } catch(_) { return false; }
                }

                const candidates = document.querySelectorAll(sels.join(','));
                for (const el of candidates) {
                    const hasHiddenAncestor = el.closest('.cky-hide, .ng-hide, [hidden], .hidden');
                    if (hasHiddenAncestor) continue;
                    if (isVisible(el)) return true;
                }
                return false;
            }"""))
        except Exception:
            return False

    txt = _page_text_lc(driver)
    looks_like_cookie_context = (
        any(k in txt for k in ["cookie", "cookies", "gdpr", "rgpd", "consentement", "consent"])
        or _cmp_container_exists_anywhere()
    )

    if looks_like_cookie_context and _has_explicit_consent_control():
        return True

    return False

def is_start_screen(driver) -> bool:
    """
    Détecte un écran de démarrage (bienvenue/start).
    """
    page = driver
    txt = _page_text_lc(driver)
    if not any(k in txt for k in ["bienvenue", "welcome", "commencer", "démarrer", "start"]):
        return False

    # QuestMindshare — SPA chatbot cumulatif
    try:
        is_questmindshare_active = page.evaluate("""() => {
            const hasMsgContainer = !!document.querySelector('div[data-testid="message-container"]');
            if (!hasMsgContainer) return false;
            const hasOptions = Array.from(document.querySelectorAll('[data-testid^="option-"]')).some(function(el) {
                try {
                    const s = getComputedStyle(el);
                    if (s.display === 'none' || s.visibility === 'hidden') return false;
                    const r = el.getBoundingClientRect();
                    return r.width > 2 && r.height > 2;
                } catch(_) { return false; }
            });
            const hasInstructions = !!document.querySelector('div[data-testid="instructions"]');
            return hasOptions || hasInstructions;
        }""")
        if is_questmindshare_active:
            from Survey.log_utils import log_debug
            log_debug("[DOM_CLASSIFIER]", "is_start_screen: QuestMindshare message-container + options/instructions actifs => pas un start_screen")
            return False
    except Exception:
        pass

    # Compter les inputs utilisateur RÉELS (pas les reCAPTCHA/hCaptcha cachés)
    real_inputs_count = 0
    try:
        real_inputs_count = page.evaluate("""() => {
            const els = Array.from(document.querySelectorAll('input, select, textarea'));
            let count = 0;
            for (const el of els) {
                const id = (el.id || '').toLowerCase();
                const name = (el.name || '').toLowerCase();
                const cls = (el.className || '').toLowerCase();

                if (id.includes('recaptcha') || id.includes('hcaptcha') ||
                    name.includes('recaptcha') || name.includes('hcaptcha') ||
                    cls.includes('recaptcha') || cls.includes('hcaptcha')) {
                    continue;
                }

                if (el.closest('.grecaptcha-badge, .h-captcha, [data-hcaptcha]')) {
                    continue;
                }

                try {
                    const cs = getComputedStyle(el);
                    if (cs && cs.display === 'none') continue;
                } catch(_) {}

                count++;
            }
            return count;
        }""")
    except Exception:
        # Fallback natif Playwright
        real_inputs_count = len(page.query_selector_all("input, select, textarea"))

    if real_inputs_count > 0:
        return False

    # Vérifier les options React/Vue (data-testid="option-N")
    try:
        has_react_options = page.evaluate("""() => {
            const opts = Array.from(document.querySelectorAll('[data-testid^="option-"]'));
            return opts.some(function(el) {
                try {
                    const s = getComputedStyle(el);
                    if (s.display === 'none' || s.visibility === 'hidden') return false;
                    const r = el.getBoundingClientRect();
                    return r.width > 2 && r.height > 2;
                } catch(_) { return false; }
            });
        }""")
        if has_react_options:
            from Survey.log_utils import log_debug
            log_debug("[DOM_CLASSIFIER]", "is_start_screen: options data-testid='option-N' visibles => pas un start_screen")
            return False
    except Exception:
        pass

    # Vérifier les options Confirmit CF div[role="radio"]
    try:
        has_confirmit_single_radios = page.evaluate("""() => {
            const questions = Array.from(document.querySelectorAll('div.cf-question--single'));
            for (const q of questions) {
                const radios = Array.from(q.querySelectorAll('div.cf-radio[role="radio"]'));
                for (const r of radios) {
                    try {
                        const s = getComputedStyle(r);
                        if (s.display === 'none' || s.visibility === 'hidden') continue;
                        const rect = r.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) return true;
                    } catch(_) { continue; }
                }
            }
            return false;
        }""")
        if has_confirmit_single_radios:
            from Survey.log_utils import log_debug
            log_debug("[DOM_CLASSIFIER]", "is_start_screen: div.cf-radio[role='radio'] visibles dans cf-question--single => pas un start_screen")
            return False
    except Exception:
        pass

    return True

def _has_visible_answerables(driver) -> bool:
    """
    True si la page contient des éléments de réponse visibles.

    Convention frame : iter_frame_chains / switch_to_frame_chain reçoivent driver (shim).
    Après switch, on récupère current_frame via getattr(driver, "_current_frame", driver)
    pour évaluer le JS dans le contexte de l'iframe (même convention que page_snapshot.py BLOC 3b2).
    """
    _JS = """() => {
      const isVisible = (e) => {
        if (!e) return false;
        const s = window.getComputedStyle(e);
        if (!s) return false;
        if (s.display === 'none' || s.visibility === 'hidden') return false;
        const r = e.getBoundingClientRect();
        return r && r.width > 2 && r.height > 2;
      };

      const inputs = Array.from(document.querySelectorAll(
        "input[type='radio'], input[type='checkbox'], select, textarea, " +
        "input[type='text'], input[type='number'], input[type='email'], input[type='tel'], input[type='search']"
      ));
      for (const el of inputs){
        try { if (isVisible(el)) return true; } catch(_){}
      }

      const btns = Array.from(document.querySelectorAll(
        "button, a[role='button'], [role='button'], input[type='button'], input[type='submit']"
      ));
      for (const b of btns){
        try { if (isVisible(b)) return true; } catch(_){}
      }

      const special = Array.from(document.querySelectorAll(
        "td.clickableCell, div.clickableCell, li.sq-cardrating-button[data-clickable='true'], li.sq-cardrating-button"
      ));
      let count = 0;
      for (const e of special){
        try{
          if (!isVisible(e)) continue;
          const t = (e.innerText || "").trim();
          if (!t) continue;
          count++;
          if (count >= 3) return true;
        }catch(_){}
      }

      const roleButtons = Array.from(document.querySelectorAll(
        '[role="button"].choice-option, [role="button"].random-choice, ' +
        'div[tabindex][role="button"]'
      ));
      let roleCount = 0;
      for (const rb of roleButtons){
        try{
          if (!isVisible(rb)) continue;
          const txt = (rb.innerText || "").trim();
          if (!txt) continue;
          roleCount++;
          if (roleCount >= 2) return true;
        }catch(_){}
      }

      const qmOptions = Array.from(document.querySelectorAll(
        'div[data-testid^="option-"][tabindex="0"]'
      ));
      let qmCount = 0;
      for (const qm of qmOptions){
        try{
          if (!isVisible(qm)) continue;
          qmCount++;
          if (qmCount >= 2) return true;
        }catch(_){}
      }

      const qmMsgCont = document.querySelector('div[data-testid="message-container"]');
      if (qmMsgCont) {
        const qmInstr = document.querySelector('div[data-testid="instructions"]');
        if (qmInstr && isVisible(qmInstr)) return true;
        for (const opt of document.querySelectorAll('div[data-testid^="option-"][tabindex="0"]')) {
          try { if (isVisible(opt)) return true; } catch(_) {}
        }
      }

      const qmTextInput = document.querySelector('input[data-testid="question-input"]');
      if (qmTextInput && isVisible(qmTextInput)) return true;

      return false;
    }"""

    for chain in iter_frame_chains(driver, max_depth=2):
        with switch_to_frame_chain(driver, chain) as ok:
            if not ok:
                continue
            current_frame = getattr(driver, "_current_frame", driver)
            try:
                if current_frame.evaluate(_JS):
                    return True
            except Exception:
                continue

    return False


def _has_visible_active_cardsort(driver) -> bool:
    """
    Détecte un widget cardsort réellement actif/visible.
    """
    _JS = r"""() => {
      const isVisible = (e) => {
        if (!e) return false;
        const s = window.getComputedStyle(e);
        if (!s) return false;
        if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
        const r = e.getBoundingClientRect();
        return r && r.width > 20 && r.height > 20;
      };

      const widgets = Array.from(document.querySelectorAll('.sq-cardsort'));
      for (const widget of widgets) {
        if (!isVisible(widget)) continue;

        const hasBuckets = widget.querySelectorAll('.sq-cardsort-bucket').length >= 2;
        const hasCards = widget.querySelectorAll('.sq-cardsort-card, .sq-cardsort-card-item').length >= 1;

        if (hasBuckets && hasCards) return true;
      }

      return false;
    }"""

    for chain in iter_frame_chains(driver, max_depth=2):
        with switch_to_frame_chain(driver, chain) as ok:
            if not ok:
                continue
            current_frame = getattr(driver, "_current_frame", driver)
            try:
                if current_frame.evaluate(_JS):
                    return True
            except Exception:
                continue

    return False

def is_end_screen(driver):
    # Hard guard: un cardsort visible/actif n'est pas un écran de fin.
    if _has_visible_active_cardsort(driver):
        return False

    txt = _page_text_lc(driver)

    end_patterns = [
        r"\bthank you for\b",
        r"\bsurvey complete\b",
        r"\bsurvey completed\b",
        r"\bfin du sondage\b",
        r"\bsondage termin[eé]\b",
        r"\benqu[eê]te termin[eé]e\b",
        r"\bvous avez termin[eé]\b",
        r"\bmerci de votre participation\b",
        r"\bmerci pour votre participation\b",
        r"\bmerci d'avoir particip[eé]\b",
        r"\bbon travail\b",
        r"\bpartiellement r[ée]pondu\b",
        r"\bl'annonceur cherchait\b",
        r"\bcompleted\b",
    ]

    strong_end = any(re.search(p, txt) for p in end_patterns)
    if not strong_end:
        return False

    if _has_visible_answerables(driver):
        return False

    return True

def is_captcha_screen(driver) -> bool:
    """
    Détecte un CAPTCHA *réellement bloquant*.
    """
    page = driver

    def _captcha_result(detected: bool, reason: str) -> bool:
        print(f"[DOM_CLASSIFIER][CAPTCHA] captcha_detected={'true' if detected else 'false'} reason={reason}")
        return detected

    # 1) Signal texte fort
    txt = _page_text_lc(driver)
    robot_kw = [
        "je ne suis pas un robot",
        "i'm not a robot",
        "im not a robot",
        "verify you are human",
        "human verification",
        "vérifiez que vous êtes",
        "verification humaine",
    ]
    if any(k in txt for k in robot_kw):
        return _captcha_result(True, "robot_keyword_visible")

    # 1a) PureSpectrum CAPTCHA (ps-captcha-question) — parcours frames
    try:
        _JS_PS = r"""() => {
        const isVisible = (e) => {
          try{
            const cs = getComputedStyle(e);
            if (!cs) return false;
            if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') return false;
            const r = e.getBoundingClientRect();
            if (!r) return false;
            if (r.bottom < 0 || r.right < 0) return false;
            return (r.width > 10 && r.height > 10);
          }catch(_){ return false; }
        };

        const root = document.querySelector('ps-captcha-question') || document.querySelector('ps-captcha');
        if (!root || !isVisible(root)) return false;

        const img = root.querySelector("img[alt*='captcha' i], img[alt*='ps captcha' i]");
        const inp = root.querySelector(
          "input[data-e2e='alpha-numeric-input'], ps-alpha-numeric-input input, " +
          "input[aria-label*='characters you see' i], input[aria-label*='caract' i]"
        );

        if (!img || !inp) return false;
        return isVisible(img) && isVisible(inp);
        }"""

        for chain in iter_frame_chains(driver, max_depth=2):
            with switch_to_frame_chain(driver, chain) as ok:
                if not ok:
                    continue
                current_frame = getattr(driver, "_current_frame", driver)
                try:
                    if current_frame.evaluate(_JS_PS):
                        return _captcha_result(True, "ps_captcha_widget_visible")
                except Exception:
                    continue
    except Exception:
        pass

    if ("captcha" in txt) or ("hcaptcha" in txt):
        try:
            has_visible_captcha = bool(page.evaluate("""() => {
                const isVisible = (e) => {
                try{
                    const cs = getComputedStyle(e);
                    if (!cs) return false;
                    if (cs.display === "none" || cs.visibility === "hidden" || cs.opacity === "0") return false;
                    const r = e.getBoundingClientRect();
                    if (!r) return false;
                    if (r.bottom < 0 || r.right < 0) return false;
                    return (r.width > 2 && r.height > 2);
                }catch(_){ return false; }
                };

                const sels = [
                "iframe[src*='recaptcha']",
                "iframe[src*='captcha']",
                "iframe[src*='hcaptcha']",
                ".g-recaptcha",
                ".h-captcha",
                "#recaptcha",
                "[data-sitekey]",
                "#pscaptcha",
                "input[id*='captcha' i]",
                "input[name*='captcha' i]"
                ];

                const els = Array.from(document.querySelectorAll(sels.join(","))).filter(isVisible);
                for (const e of els){
                const r = e.getBoundingClientRect();
                const tn = (e.tagName||"").toLowerCase();
                if (tn === "iframe" || e.classList.contains("g-recaptcha") || e.classList.contains("h-captcha")) {
                    if (tn === "iframe") {
                        const src = (e.src || e.getAttribute("src") || "").toLowerCase();
                        if (src.includes("recaptcha") && src.includes("/anchor")) continue;
                    }
                    if (r.width >= 60 && r.height >= 40) return true;
                } else {
                    if (r.width >= 10 && r.height >= 10) return true;
                }
                }
                return false;
            }"""))
        except Exception:
            has_visible_captcha = False

        if has_visible_captcha:
            return _captcha_result(True, "captcha_keyword_with_visible_widget")

        if _has_visible_answerables(driver):
            return _captcha_result(False, "captcha_keyword_but_answerables_present")

        return _captcha_result(True, "captcha_keyword_sterile_page")

    # 1bis) CAPTCHA arithmétique via image
    try:
        if any(k in txt for k in [
            "veuillez saisir le résultat",
            "veuillez saisir le resultat",
            "saisir le résultat",
            "saisir le resultat",
            "enter the result",
            "type the result",
            "please enter the result",
        ]):
            has_img_math = bool(page.evaluate(r"""() => {
                const needles = ['saisir le résultat','saisir le resultat','enter the result','type the result','please enter the result'];
                const root = document.querySelector('#survey') || document.body;
                const t = (root && root.innerText ? root.innerText : '').toLowerCase();
                if (!needles.some(n => t.includes(n))) return false;

                const isVisible = (e) => {
                  try {
                    const cs = getComputedStyle(e);
                    if (!cs) return false;
                    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') return false;
                    const r = e.getBoundingClientRect();
                    if (!r) return false;
                    if (r.width < 40 || r.height < 30) return false;
                    if (r.bottom < 0 || r.right < 0) return false;
                    return true;
                  } catch (_) { return false; }
                };

                const imgSel = "h1.question-text img, .question-text img, img.survey_image, img.survey-image-original";
                const imgs = Array.from(document.querySelectorAll(imgSel)).filter(isVisible);
                if (!imgs.length) return false;

                for (const img of imgs) {
                  const q = img.closest('.question') || img.closest('[id^="question_"]') || img.closest('.survey-body') || img.parentElement;
                  if (!q) continue;
                  const inp = q.querySelector("input[type='number'], input[inputmode='numeric'], input[type='text'], input[type='tel']");
                  if (!inp) continue;
                  const r = inp.getBoundingClientRect();
                  if (r && r.width > 10 && r.height > 10) return true;
                }
                return false;
            }"""))
            if has_img_math:
                return _captcha_result(True, "arithmetic_captcha_widget")
    except Exception:
        pass

    # 1ter) Slider puzzle (NIQ / GfK mrIWeb)
    try:
        if any(k in txt for k in [
            "faites glisser le curseur",
            "veuillez faire glisser le curseur",
            "glisser le curseur vers la droite",
            "pour compléter la partie manquante de l'image",
            "pour completer la partie manquante de l'image",
        ]):
            has_slider_puzzle = bool(page.evaluate(r"""() => {
                const root = document.querySelector('#sliderpanel') || document.body;
                if (!root) return false;

                const hasVerify = !!root.querySelector(
                  "#sliderpanel, .verify-img-panel, .verify-gap, .verify-bar-area, .verify-move-block, .verify-sub-block"
                );
                if (!hasVerify) return false;

                const sub = root.querySelector(".verify-sub-block");
                if (sub){
                  try{
                    const cs = getComputedStyle(sub);
                    const bg = (cs && cs.backgroundImage) ? cs.backgroundImage : "";
                    if (bg && bg !== "none") return true;
                  }catch(e){}
                }

                return !!root.querySelector(".verify-img-panel") && !!root.querySelector(".verify-gap") && !!root.querySelector(".verify-bar-area");
            }"""))
            if has_slider_puzzle:
                return _captcha_result(True, "slider_captcha_widget")
    except Exception:
        pass

    # 2) Widget visible (taille minimale)
    try:
        if bool(page.evaluate("""() => {
            const sels = [
              "iframe[src*='recaptcha']",
              "iframe[src*='captcha']",
              "iframe[src*='hcaptcha']",
              ".g-recaptcha",
              ".h-captcha",
              "#recaptcha",
              "[data-sitekey]"
            ];
            const els = Array.from(document.querySelectorAll(sels.join(",")));

            for (const e of els) {
              const cs = getComputedStyle(e);
              if (cs.display === "none" || cs.visibility === "hidden") continue;

              const r = e.getBoundingClientRect();
              if (!r) continue;

              if (r.width < 60 || r.height < 40) continue;

              const tn = (e.tagName || "").toLowerCase();
              if (tn === "iframe") {
                const src = (e.src || e.getAttribute("src") || "").toLowerCase();
                if (src.includes("recaptcha") && src.includes("/anchor")) {
                  continue;
                }
                if (src.includes("recaptcha") && src.includes("size=invisible")) {
                  continue;
                }
              }

              if (e.closest && e.closest(".grecaptcha-badge")) {
                continue;
              }

              const vw = window.innerWidth || document.documentElement.clientWidth;
              const vh = window.innerHeight || document.documentElement.clientHeight;

              if (r.right < 0 || r.left > vw || r.bottom < 0 || r.top > vh) continue;

              const visibleWidth = Math.min(r.right, vw) - Math.max(r.left, 0);
              const visibleHeight = Math.min(r.bottom, vh) - Math.max(r.top, 0);
              if (visibleWidth < r.width * 0.3 || visibleHeight < r.height * 0.3) continue;

              return true;
            }
            return false;
        }""")):
            return _captcha_result(True, "visible_captcha_widget")
    except Exception:
        # Fallback natif Playwright
        try:
            frames = page.query_selector_all(
                "iframe[src*='captcha'], iframe[src*='recaptcha'], iframe[src*='hcaptcha']"
            )
            for fr in frames:
                try:
                    if not fr.is_visible():
                        continue
                    r = fr.bounding_box() or {}
                    src = (fr.get_attribute("src") or "").lower()
                    if "recaptcha" in src and "/anchor" in src:
                        continue
                    if (r.get("width", 0) or 0) >= 60 and (r.get("height", 0) or 0) >= 40:
                        return _captcha_result(True, "native_visible_captcha_iframe")
                except Exception:
                    continue
        except Exception:
            pass

    return _captcha_result(False, "no_visible_captcha_challenge")

def is_drag_drop(driver) -> bool:
    page = driver

    def _read_text(el) -> str:
        try:
            txt = el.inner_text() or ""
            txt = re.sub(r"\s+", " ", txt).strip().lower()
            if txt:
                return txt
        except Exception:
            pass
        return ""

    def _visible_count(elements) -> int:
        count = 0
        for el in elements:
            try:
                if el.is_visible():
                    count += 1
            except Exception:
                count += 1
        return count

    try:
        titles = page.query_selector_all(
            "p.question-title[psquestiontitle], p.question-title, [psquestiontitle]",
        )
        has_instruction = False
        for t in titles:
            txt = _read_text(t)
            if not txt:
                continue
            if not re.search(r"\b\d+\b", txt):
                continue
            if any(v in txt for v in ("déposer", "deposer", "glisser", "drag", "drop")):
                has_instruction = True
                break

        draggables = page.query_selector_all("[cdkdrag], .cdk-drag, [draggable='true']")
        drop_zones = page.query_selector_all(
            "#dropZoneList, [cdkdroplist].drop-zone, [cdkdroplist][aria-label*='Drop zone'], [cdkdroplist]",
        )

        return has_instruction and _visible_count(draggables) >= 2 and _visible_count(drop_zones) >= 1
    except Exception:
        return False

def is_matrix(driver) -> bool:
    page = driver
    radios = page.query_selector_all("input[type='radio'], [role='radio']")
    if len(radios) < 6:
        return False
    ys = [(r.bounding_box() or {}).get("y", 0) for r in radios]
    return len(set(round(y / 30) for y in ys)) >= 2

def is_date_multi_dropdown(driver) -> bool:
    page = driver
    selects = page.query_selector_all("select")
    if len(selects) < 2:
        return False
    txt = _page_text_lc(driver)
    return any(k in txt for k in ["année", "annee", "year", "mois", "month"])

_OE_AUX_CLASS_RE = re.compile(r'\bOE-\w+-input\b')

def _is_decipher_aux_openend(ta, driver) -> bool:
    """Retourne True si le textarea est un open-end auxiliaire Decipher/FocusVision.

    ta est un ElementHandle Playwright natif après migration BLOC 3b3.
    """
    try:
        cls = ta.get_attribute("class") or ""
        if _OE_AUX_CLASS_RE.search(cls):
            return True
        return bool(ta.evaluate("(el) => el.closest('.blocked-other') !== null"))
    except Exception:
        return False


def is_open_textarea(driver) -> bool:
    """Vrai uniquement si un textarea principal est visible et exploitable."""
    page = driver
    try:
        tas = page.query_selector_all("textarea")
    except Exception:
        return False

    for ta in tas:
        try:
            if not ta.is_visible():
                continue
            r = ta.bounding_box() or {}
            if float(r.get("width") or 0) < 20 or float(r.get("height") or 0) < 20:
                continue
            if _is_decipher_aux_openend(ta, driver):
                continue
            return True
        except Exception:
            continue
    return False


def is_error_recovery_screen(driver) -> bool:
    """
    Détecte la page d'erreur récupérable GreenXP / rx.samplicio.us.
    """
    page = driver
    try:
        return bool(page.evaluate(
            """() => !!(document.querySelector('[data-testid="layout-card"]')"""
            """ && document.querySelector('#confirmation-button'))"""
        ))
    except Exception:
        return False


def is_prodege_data_privacy_screen(driver) -> bool:
    """
    Détecte la page de consentement Prodege (prsrvy.com /surveys/data-privacy).
    """
    page = driver
    try:
        return bool(page.evaluate(r"""() => {
            if (!document.querySelector('form#dataPrivacyAgreeForm')) return false;
            if (!document.querySelector('input.dataPrivacyCheckboxRequired')) return false;
            if (!document.querySelector('button#dataPrivacySubmitBtn')) return false;
            return true;
        }"""))
    except Exception:
        return False


def is_datadiggers_icontrol_final_screen(driver) -> bool:
    """
    Détecte la page de transition DataDiggers iControl post-qualification.
    """
    page = driver
    try:
        return bool(page.evaluate(r"""() => {
            const wrap = document.querySelector('div.wrap.infrmtion');
            if (!wrap) return false;
            const btn = wrap.querySelector('button.next_btn[translate="srvyFinal.btnLtsDo"]');
            if (!btn) return false;
            const s = window.getComputedStyle(btn);
            if (!s || s.display === 'none' || s.visibility === 'hidden') return false;
            const r = btn.getBoundingClientRect();
            return !!(r && r.width > 10 && r.height > 10);
        }"""))
    except Exception:
        return False


# ============================================================
# DOM REGISTRY (ORDRE CRITIQUE)
# ============================================================

DOM_REGISTRY: list[dict[str, Any]] = [

    # ⚠️ Sécurité / hors OpenAI
    {
        "itype": "captcha",
        "signature": is_captcha_screen,
        "handler": "handle_captcha_guard",
        "openai": False,
    },
    {
        "itype": "start_screen",
        "signature": is_start_screen,
        "handler": "handle_start_screen",
        "openai": False,
    },
    {
        "itype": "prodege_data_privacy_screen",
        "signature": is_prodege_data_privacy_screen,
        "handler": "handle_prodege_data_privacy_screen",
        "openai": False,
    },
    {
        "itype": "datadiggers_icontrol_final_screen",
        "signature": is_datadiggers_icontrol_final_screen,
        "handler": "handle_datadiggers_icontrol_final_screen",
        "openai": False,
    },
    {
        "itype": "consent_screen",
        "signature": is_consent_screen,
        "handler": "handle_consent_screen",
        "openai": False,
    },
    {
        "itype": "end_screen",
        "signature": is_end_screen,
        "handler": "handle_end_screen",
        "openai": False,
    },
    {
        "itype": "error_recovery_screen",
        "signature": is_error_recovery_screen,
        "handler": "handle_error_recovery_screen",
        "openai": False,
    },

    # 🧩 Composites
    {
        "itype": "matrix_rows_single_choice",
        "signature": is_matrix,
        "handler": "handle_matrix_rows",
        "openai": True,
    },
    {
        "itype": "date_multi_dropdown",
        "signature": is_date_multi_dropdown,
        "handler": "handle_date_multi_dropdown",
        "openai": True,
    },
    {
        "itype": "ranking_or_drag",
        "signature": is_drag_drop,
        "handler": "handle_drag_drop_logic",
        "openai": False,
    },

    # 📝 Texte
    {
        "itype": "textarea",
        "signature": is_open_textarea,
        "handler": "handle_textarea",
        "openai": True,
    },
]

# ============================================================
# API publique
# ============================================================

def classify_dom(driver) -> Optional[dict]:
    """
    Retourne le premier mapping DOM_REGISTRY qui match.
    """
    time.sleep(2)
    logger.info("🔍 [DOM_CLASSIFIER] Début du scan DOM — itération du registry")
    for rule in DOM_REGISTRY:
        try:
            if rule["signature"](driver):
                public = dict(rule)
                sig = public.pop("signature", None)
                if callable(sig):
                    public["signature_name"] = getattr(sig, "__name__", "signature")
                return public
        except Exception:
            continue

    return None
