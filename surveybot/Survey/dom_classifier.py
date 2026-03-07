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
import re
from urllib.parse import urlparse, parse_qs
from typing import Callable, Optional, Dict, Any
import Survey.dom_metrics as dom_metrics
from selenium.webdriver.common.by import By
from Survey.frame_utils import iter_frame_chains, switch_to_frame_chain
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
    #    (Le badge reCAPTCHA invisible/v3 injecte souvent un anchor avec size=invisible)
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
            # si pas de size explicite, on reste conservateur:
            # on ne déclenche PAS captcha juste sur anchor seul (trop de faux positifs)
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

    # widget visible
    if 'class="h-captcha"' in h or ("data-sitekey" in h and "hcaptcha" in h):
        return True

    # iframe de challenge (fréquent)
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
    Le simple badge reCAPTCHA / script render= / anchor size=invisible ne suffit pas.
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
    IMPORTANT: on ignore le footer (Privacy Policy / General Terms) car ça crée des faux positifs (consent_screen).
    """
    try:
        txt = driver.execute_script(
            """
            const root = document.querySelector('#survey') || document.querySelector('main') || document.body;
            if (!root) return '';
            const clone = root.cloneNode(true);

            // 0) IMPORTANT: virer les scripts/templates qui contiennent souvent des messages d'erreur (dont "captcha")
            // Ex: <script type="text/ng-template" id="v-error-messages"> ... Captcha incorrect ...
            clone.querySelectorAll('script, style, noscript, template').forEach(n => n.remove());
            // Angular cache souvent via ng-hide / aria-hidden (texte non visible -> bruit)
            clone.querySelectorAll('.ng-hide, [aria-hidden="true"]').forEach(n => n.remove());

            // 1) supprimer les zones footer-like (sinon faux positifs 'privacy policy' etc.)
            clone.querySelectorAll('footer, [id*="footer"], [class*="footer"], [id*="Footer"], [class*="Footer"]').forEach(n => n.remove());

            return (clone.innerText || '').trim();
            """
        )
        txt = _norm_lc(txt or "")
        return txt[:5000] if len(txt) > 5000 else txt
    except Exception:
        try:
            return _norm_lc(driver.page_source or "")
        except Exception:
            return ""

# ============================================================
# Signatures DOM (détecteurs)
# ============================================================

def _is_formal_survey_question_page(driver) -> bool:
    """
    Détecte une page de question de sondage formelle.
    
    Utilisé pour EXCLURE les pages de sondage du consent_screen (évite faux positifs
    sur des questions de consentement de participation qui contiennent "consent/agree").
    
    Patterns détectés:
    - Decipher/FocusVision: .question[role="radiogroup"], name="ans*"
    - Confirmit: .question, .sq-question
    - Generic: structure de question avec options de réponse formelles
    
    IMPORTANT: retourne True SEULEMENT si on a une structure de sondage CLAIRE,
    pas juste des radios/checkboxes (qui pourraient être dans un CMP).
    """
    try:
        return bool(driver.execute_script(r"""
            // --- Decipher/FocusVision ---
            // Pattern: .question[role="radiogroup"] + inputs name="ans*"
            const decipherQ = document.querySelector('.question[role="radiogroup"]');
            if (decipherQ) {
                const ansInputs = decipherQ.querySelectorAll('input[name^="ans"]');
                if (ansInputs.length >= 2) return true;
            }
            
            // Pattern: .survey-body + .question + inputs radio/checkbox avec name="ans*"
            const surveyBody = document.querySelector('.survey-body');
            if (surveyBody) {
                const questions = surveyBody.querySelectorAll('.question');
                for (const q of questions) {
                    const ansInputs = q.querySelectorAll('input[name^="ans"]');
                    if (ansInputs.length >= 2) return true;
                }
            }
            
            // --- Confirmit/Forsta ---
            // Pattern: .sq-question avec options de réponse
            const sqQuestions = document.querySelectorAll('.sq-question, [class*="sq-question"]');
            for (const sq of sqQuestions) {
                const radios = sq.querySelectorAll('input[type="radio"], input[type="checkbox"]');
                if (radios.length >= 2) return true;
            }

            // --- YouGov single-choice question layout ---
            // Pattern observé: .question-container > fieldset.question-single
            // + legend.question-text + liste de réponses radio avec labels textuels.
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
            // Pattern: élément .question-text + inputs radio/checkbox visibles dans un container survey
            const surveyContainer = document.querySelector('#survey, .survey-container, [class*="survey"]');
            if (surveyContainer) {
                const qText = surveyContainer.querySelector('.question-text, h1.question-text, [class*="question-text"]');
                if (qText) {
                    // Chercher les inputs dans le même container question
                    const qContainer = qText.closest('.question') || qText.closest('[id^="question_"]') || qText.parentElement;
                    if (qContainer) {
                        const inputs = qContainer.querySelectorAll('input[type="radio"], input[type="checkbox"]');
                        if (inputs.length >= 2) {
                            // Vérifier que les inputs ont des labels textuels (pas juste des icônes CMP)
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
            // Pattern: .QuestionBody avec options
            const qBody = document.querySelector('.QuestionBody, [class*="QuestionBody"]');
            if (qBody) {
                const radios = qBody.querySelectorAll('input[type="radio"], input[type="checkbox"], [role="radio"], [role="checkbox"]');
                if (radios.length >= 2) return true;
            }
            
            return false;
        """))
    except Exception:
        return False


def is_consent_screen(driver) -> bool:
    """
    Détecte un écran de consentement (cookies / RGPD) *bloquant*.

    But: éviter les faux positifs causés par des widgets non bloquants
    (ex: petit bouton flottant "Accepter les cookies" type Evidon).

    Critère central: on ne renvoie True que si on observe un *overlay/dialog*
    visible et suffisamment grand, ou un contrôle explicite de consentement.
    """

    # Hard negative: un écran "Start/Commencer" n'est pas un consent_screen.
    # (ex: page Dynata avec bouton Start + widget cookies)
    try:
        if is_start_screen(driver):
            return False
    except Exception:
        pass

    # -------------------------------------------------------------------------
    # NOUVEAU: Hard negative pour les pages de question de sondage formelle.
    # Évite les faux positifs sur les questions de consentement de participation
    # (ex: Decipher "Do you agree to participate... I consent...")
    # Ces pages ont "consent/agree" dans le texte mais ne sont PAS des CMP.
    # -------------------------------------------------------------------------
    try:
        if _is_formal_survey_question_page(driver):
            return False
    except Exception:
        pass

    # -------------------------------------------------------------------------
    # NOUVEAU: Confirmit/Forsta "welcome / GDPR info" pages (sans inputs) avec CTA "Suivant".
    # Cas réel: iWelcome / iWelcomeGDPR / qGDPRConsent (infos RGPD) + bouton navigation next.
    # Objectif: ne pas laisser ces pages en "unclassified" => allow handle_consent_screen => clic CTA.
    # -------------------------------------------------------------------------
    def _is_confirmit_welcome_gdpr_with_next() -> bool:
        try:
            return bool(driver.execute_script(r"""
            const hasConfirmit = !!document.querySelector('body.cf-page, form.cf-page__form, .cf-page__main, .cf-page__question-list');
            if (!hasConfirmit) return false;
                // blocs info typiques
                const infoBlocks = document.querySelectorAll(
                    '.cf-question--info#iWelcome, .cf-question--info#iWelcomeMRS, .cf-question--info#iWelcomeGDPR, .cf-question--info#qGDPRConsent, .cf-question--info[id^="iWelcome"]'
                );
                if (infoBlocks.length < 1) return false;

                // bouton next visible
                const nextBtn = document.querySelector('#navButtons .cf-navigation-next, button.cf-navigation-next, .cf-navigation__button.cf-navigation-next');
                if (!nextBtn) return false;

                // pas d'inputs de réponse visibles (sinon c'est une vraie question)
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

                // signal texte RGPD/bienvenue (réduit les faux positifs)
                const t = (document.querySelector('.cf-page__question-list')?.innerText || document.body.innerText || '').toLowerCase();
                const kw = ['rgpd','gdpr','données','adresse ip','protection des données','confidentielle','market research society','prêt','c\'est parti','c’est parti'];
                if (!kw.some(k => t.includes(k))) return false;

                return true;
            """))
        except Exception:
            return False

    if _is_confirmit_welcome_gdpr_with_next():
        return True

    # -------------------------------------------------------------------------
    # Survey-final redirect gate (Angular):
    # écran intermédiaire "Vous y êtes presque" / "Let's do this" qui ne contient
    # pas de questions mais un CTA pour poursuivre vers l'enquête.
    # Sans ce signal, la page tombe en "unclassified" et le CTA n'est pas cliqué.
    # -------------------------------------------------------------------------
    def _is_survey_redirect_gate() -> bool:
        try:
            return bool(driver.execute_script(r"""
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
            """))
        except Exception:
            return False

    if _is_survey_redirect_gate():
        return True

    # -------------------------------------------------------------------------
    # Walr country-routing interstitial:
    # Page Walr avec routage automatique par pays (.cRef / .cRadio / #btnNext).
    # Le JS de la page auto-sélectionne le pays et clique #btnNext, mais le bot
    # doit aussi pouvoir intercepter/cliquer ce CTA si le JS n'a pas encore agi.
    # Signal distinctif: présence de .cRadio (pays) + .cRef + #btnNext visible.
    # -------------------------------------------------------------------------
    def _is_walr_country_routing_gate() -> bool:
        try:
            return bool(driver.execute_script(r"""
                const isVisible = (el) => {
                    if (!el) return false;
                    const s = window.getComputedStyle(el);
                    if (!s || s.display === 'none' || s.visibility === 'hidden') return false;
                    const r = el.getBoundingClientRect();
                    return !!(r && r.width > 10 && r.height > 10);
                };

                // Signal fort 1: inputs .cRadio (liste pays Walr, auto-sélectionnés par JS)
                const cRadios = document.querySelectorAll('.cRadio');
                if (cRadios.length < 10) return false;

                // Signal fort 2: élément .cRef (code pays injecté côté serveur)
                const cRef = document.querySelector('.cRef');
                if (!cRef) return false;

                // Signal fort 3: bouton #btnNext visible
                const btnNext = document.querySelector('#btnNext');
                if (!isVisible(btnNext)) return false;

                return true;
            """))
        except Exception:
            return False

    if _is_walr_country_routing_gate():
        return True

    # -------------------------------------------------------------------------
    # Walr intro/final interstitial:
    # certaines pages Walr d'entrée exposent Q=FINAL + message intro + CTA #btnNext,
    # sans .cRadio/.cRef. Elles doivent être traitées comme consent_screen pour
    # permettre le clic/interception du CTA (notamment en mode CTA_INTERCEPT_ONLY).
    # -------------------------------------------------------------------------
    def _is_walr_intro_final_gate() -> bool:
        try:
            return bool(driver.execute_script(r"""
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
            """))
        except Exception:
            return False

    if _is_walr_intro_final_gate():
        return True

    # -------------------------------------------------------------------------
    # Affinnova / NIQ launch interstitial:
    # page "LANCER L'ÉTUDE" qui ouvre la survey en popup (window.open).
    # Ce n'est pas un end_screen même si un "Merci pour votre participation"
    # est présent dans un bloc secondaire masqué avant le clic.
    # -------------------------------------------------------------------------
    def _is_affinnova_launch_gate() -> bool:
        try:
            return bool(driver.execute_script(r"""
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

                // Signal technique robuste de la mécanique popup Affinnova
                const hasShowSurveyFn = /function\s+showSurvey\s*\(/i.test(document.documentElement?.innerHTML || '');
                if (!hasShowSurveyFn) return false;

                return true;
            """))
        except Exception:
            return False

    if _is_affinnova_launch_gate():
        return True

    # --- contrôle explicite type consent (checkbox/radio avec libellé agree/accept/consent) ---
    def _has_explicit_consent_control() -> bool:
        """
        Détecte un contrôle de consentement explicite (checkbox/radio).
        IMPORTANT: on vérifie que l'input est VISIBLE (évite faux positifs CookieYes caché).
        """

        markers = {
            "agree", "accept", "consent", "gdpr", "rgpd", "cookie", "cookies",
            "j'accepte", "j accepte", "i agree", "i accept"
        }

        def _is_input_visible(inp) -> bool:
            """Vérifie qu'un input est visible (pas dans un widget caché)."""
            try:
                if not inp.is_displayed():
                    return False
                # Vérifier si un ancêtre a une classe 'hide' (CookieYes: .cky-hide)
                has_hidden = driver.execute_script(
                    "return !!arguments[0].closest('.cky-hide, .ng-hide, [hidden], .hidden, [aria-hidden=\"true\"]')",
                    inp
                )
                if has_hidden:
                    return False
                # Vérifier taille minimale
                r = inp.rect or {}
                if float(r.get('width', 0) or 0) < 5 or float(r.get('height', 0) or 0) < 5:
                    return False
                return True
            except Exception:
                return False

        try:
            inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox'], input[type='radio']")
            for inp in inputs:
                try:
                    # NOUVEAU: vérifier la visibilité AVANT de traiter
                    if not _is_input_visible(inp):
                        continue
                    id_ = (inp.get_attribute("id") or "").strip()
                    name = (inp.get_attribute("name") or "").strip()
                    blob = f"{id_} {name}".lower()
                    if any(m in blob for m in markers):
                        return True

                    label_txt = ""
                    if id_:
                        labs = driver.find_elements(By.CSS_SELECTOR, f"label[for='{id_}']")
                        if labs:
                            label_txt = labs[0].text or labs[0].get_attribute("innerText") or ""
                    if not label_txt:
                        try:
                            lab = inp.find_element(By.XPATH, "ancestor::label[1]")
                            label_txt = lab.text or lab.get_attribute("innerText") or ""
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
        # On s'appuie sur la taille à l'écran pour distinguer un bandeau/dialog bloquant
        # d'un simple bouton "cookies" discret.
        js = r"""
        const vw = Math.max(320, window.innerWidth || 0);
        const vh = Math.max(240, window.innerHeight || 0);
        const minArea = vw * vh * 0.12; // >= 12% de l'écran => probablement bloquant

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
            // hors écran
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
            // si pas de texte, on accepte quand même certains CMP connus
            const blob = ((el.id||'') + ' ' + (el.className||'')).toLowerCase();
            if (!(blob.includes('onetrust') || blob.includes('qc-cmp') || blob.includes('didomi') || blob.includes('truste') || blob.includes('cookiebot')))
              continue;
          }
          return true;
        }

        // Evidon spécifique: le "button" flottant n'est PAS bloquant
        return false;
        """
        try:
            return bool(driver.execute_script(js))
        except Exception:
            return False

    # 1) Priorité: CMP overlay réellement bloquant
    if _has_blocking_cmp_overlay():
        return True

    # 2) Détection "contrôle explicite" UNIQUEMENT si le contexte ressemble à du cookies/RGPD/CMP.
    #    Sinon on évite les faux positifs sur des questions de survey du type "J'accepte la politique de confidentialité".
    def _cmp_container_exists_anywhere() -> bool:
        """
        Vérifie qu'un container CMP VISIBLE existe.
        IMPORTANT: on ignore les containers cachés (ex: CookieYes avec .cky-hide).
        """
        try:
            return bool(driver.execute_script(
                """
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
                """
            ))
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
    
    IMPORTANT: on ignore les éléments reCAPTCHA/hCaptcha cachés qui ne sont pas
    des inputs utilisateur (ex: textarea g-recaptcha-response).
    """
    txt = _page_text_lc(driver)
    # "démarrer" = bouton start courant sur Quantilope/plateformes FR
    if not any(k in txt for k in ["bienvenue", "welcome", "commencer", "démarrer", "start"]):
        return False
    
    # Compter les inputs utilisateur RÉELS (pas les reCAPTCHA/hCaptcha cachés)
    try:
        real_inputs_count = driver.execute_script("""
            const els = Array.from(document.querySelectorAll('input, select, textarea'));
            let count = 0;
            for (const el of els) {
                // Ignorer les éléments reCAPTCHA/hCaptcha par id/name/class
                const id = (el.id || '').toLowerCase();
                const name = (el.name || '').toLowerCase();
                const cls = (el.className || '').toLowerCase();
                
                if (id.includes('recaptcha') || id.includes('hcaptcha') ||
                    name.includes('recaptcha') || name.includes('hcaptcha') ||
                    cls.includes('recaptcha') || cls.includes('hcaptcha')) {
                    continue;
                }
                
                // Ignorer les éléments dans .grecaptcha-badge ou containers captcha
                if (el.closest('.grecaptcha-badge, .h-captcha, [data-hcaptcha]')) {
                    continue;
                }
                
                // Ignorer les éléments cachés (display:none)
                try {
                    const cs = getComputedStyle(el);
                    if (cs && cs.display === 'none') continue;
                } catch(_) {}
                
                count++;
            }
            return count;
        """)
    except Exception:
        # Fallback: comportement original
        real_inputs_count = len(driver.find_elements(By.CSS_SELECTOR, "input, select, textarea"))
    
    return real_inputs_count == 0

def _has_visible_answerables(driver) -> bool:
    """
    True si la page contient des éléments de réponse visibles.
    Important: Decipher/Confirmit peuvent rendre les options cliquables via:
    - <td class="clickableCell"> ... (input radio parfois masqué)
    - <li class="sq-cardrating-button" data-clickable="true"> ... (cartes)
    """
    js = """
      const isVisible = (e) => {
        if (!e) return false;
        const s = window.getComputedStyle(e);
        if (!s) return false;
        if (s.display === 'none' || s.visibility === 'hidden') return false;
        const r = e.getBoundingClientRect();
        return r && r.width > 2 && r.height > 2;
      };

      // 1) inputs classiques
      const inputs = Array.from(document.querySelectorAll(
        "input[type='radio'], input[type='checkbox'], select, textarea, " +
        "input[type='text'], input[type='number'], input[type='email'], input[type='tel'], input[type='search']"
      ));
      for (const el of inputs){
        try { if (isVisible(el)) return true; } catch(_){}
      }

      // 2) boutons/CTA
      const btns = Array.from(document.querySelectorAll(
        "button, a[role='button'], [role='button'], input[type='button'], input[type='submit']"
      ));
      for (const b of btns){
        try { if (isVisible(b)) return true; } catch(_){}
      }

      // 3) Decipher/Confirmit: cellules/cartes cliquables (inputs souvent masqués)
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
          if (count >= 3) return true; // seuil anti-faux positifs
        }catch(_){}
      }

        // ✅ 4) NOUVEAU : CloudResearch/Vue/React role="button" choice-option
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
          if (roleCount >= 2) return true; // 2+ options visibles => page answerable
        }catch(_){}
      }

      return false;
    """

    for chain in iter_frame_chains(driver, max_depth=2):
        with switch_to_frame_chain(driver, chain) as ok:
            if not ok:
                continue
            try:
                if driver.execute_script(js):
                    return True
            except Exception:
                continue

    return False


def _has_visible_active_cardsort(driver) -> bool:
    """
    Détecte un widget cardsort réellement actif/visible.

    Objectif: empêcher un faux `end_screen` quand un cardsort FocusVision/Decipher
    affiche un texte de complétion interne (ex: "Vous avez terminé !") ou un
    bouton continue masqué, alors que la question est encore en cours.
    """
    js = r"""
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

        // Signaux DOM minimaux d'une question cardsort active
        const hasBuckets = widget.querySelectorAll('.sq-cardsort-bucket').length >= 2;
        const hasCards = widget.querySelectorAll('.sq-cardsort-card, .sq-cardsort-card-item').length >= 1;

        if (hasBuckets && hasCards) return true;
      }

      return false;
    """

    for chain in iter_frame_chains(driver, max_depth=2):
        with switch_to_frame_chain(driver, chain) as ok:
            if not ok:
                continue
            try:
                if driver.execute_script(js):
                    return True
            except Exception:
                continue

    return False

def is_end_screen(driver):
    # Hard guard: un cardsort visible/actif n'est pas un écran de fin.
    if _has_visible_active_cardsort(driver):
        return False

    # 1) Un end-screen doit contenir un signal explicite de fin (pas juste "merci")
    txt = _page_text_lc(driver)

    # IMPORTANT: éviter les faux positifs sur des labels techniques type "CompleteDate"
    # (normalisé en "completedate") qui matchaient l'ancien "completed" en sous-chaîne.
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
        r"\bbon travail\b",                          # TopSurveys disqualification
        r"\bpartiellement r[ée]pondu\b",            # TopSurveys disqualification
        r"\bl'annonceur cherchait\b",               # TopSurveys disqualification
        r"\bcompleted\b",  # standalone uniquement (ne match pas "completedate")
    ]

    strong_end = any(re.search(p, txt) for p in end_patterns)
    if not strong_end:
        return False

    # 2) ET ne doit pas contenir d'inputs de réponse visibles
    if _has_visible_answerables(driver):
        return False

    return True

def is_captcha_screen(driver) -> bool:
    """
    Détecte un CAPTCHA *réellement bloquant*.
    Évite les faux positifs quand reCAPTCHA est chargé en background (invisible/1x1).
    """

    def _captcha_result(detected: bool, reason: str) -> bool:
        print(f"[DOM_CLASSIFIER][CAPTCHA] captcha_detected={'true' if detected else 'false'} reason={reason}")
        return detected

    # 1) Signal texte fort (visible seulement, via _page_text_lc)
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

    # 1a) PureSpectrum CAPTCHA (ps-captcha-question)
    # Important: le mot "captcha" n'est pas forcément présent dans le texte visible,
    # donc on détecte via signaux DOM forts (image + input dédié).
    try:
        js_ps = r"""
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
        """

        for chain in iter_frame_chains(driver, max_depth=2):
            with switch_to_frame_chain(driver, chain) as ok:
                if not ok:
                    continue
                try:
                    if driver.execute_script(js_ps):
                        return _captcha_result(True, "ps_captcha_widget_visible")
                except Exception:
                    continue
    except Exception:
        pass

    # "captcha" seul est trop bruité (templates / erreurs non visibles).
    # On n'accepte "captcha/hcaptcha" que si un widget/contrôle captcha *visible* est présent,
    # ou si la page n'a aucune réponse exploitable (rare mais possible sur des interstitiels).
    if ("captcha" in txt) or ("hcaptcha" in txt):
        try:
            has_visible_captcha = bool(driver.execute_script("""
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

                // Widgets classiques + quelques cas text-only (input id/name contenant captcha)
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
                // Seuils : iframe/widget doit être "grand", input captcha peut être petit
                if (tn === "iframe" || e.classList.contains("g-recaptcha") || e.classList.contains("h-captcha")) {
                    if (tn === "iframe") {
                        const src = (e.src || e.getAttribute("src") || "").toLowerCase();
                        // Un iframe /anchor seul est souvent un artefact non bloquant.
                        if (src.includes("recaptcha") && src.includes("/anchor")) continue;
                    }
                    if (r.width >= 60 && r.height >= 40) return true;
                } else {
                    if (r.width >= 10 && r.height >= 10) return true;
                }
                }
                return false;
            """))
        except Exception:
            has_visible_captcha = False

        if has_visible_captcha:
            return _captcha_result(True, "captcha_keyword_with_visible_widget")

        # Pas de widget visible => on ne classe pas captcha si on peut répondre à la page
        if _has_visible_answerables(driver):
            return _captcha_result(False, "captcha_keyword_but_answerables_present")

        # Sinon (page stérile + mot captcha) => on garde captcha
        return _captcha_result(True, "captcha_keyword_sterile_page")

    # 1bis) CAPTCHA arithmétique via image (ex: "Veuillez saisir le résultat" + image)
    # On le traite comme CAPTCHA car la donnée à saisir n'est pas dans le DOM texte.
    # Critères explicites (anti faux-positifs) :
    # - texte "saisir le résultat" (FR/EN)
    # - image visible dans le bloc question
    # - input numérique/texte visible dans le même bloc question
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
            has_img_math = bool(driver.execute_script(r"""
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
            """))
            if has_img_math:
                return _captcha_result(True, "arithmetic_captcha_widget")
    except Exception:
        pass

    # 1ter) Slider puzzle (NIQ / GfK mrIWeb) : "faites glisser le curseur..."
    # On le traite comme CAPTCHA car c'est une vérification humaine bloquante (pas une question).
    try:
        if any(k in txt for k in [
            "faites glisser le curseur",
            "veuillez faire glisser le curseur",
            "glisser le curseur vers la droite",
            "pour compléter la partie manquante de l'image",
            "pour completer la partie manquante de l'image",
        ]):
            has_slider_puzzle = bool(driver.execute_script(r"""
                const root = document.querySelector('#sliderpanel') || document.body;
                if (!root) return false;

                // Signaux DOM forts (anti faux-positifs)
                const hasVerify = !!root.querySelector(
                  "#sliderpanel, .verify-img-panel, .verify-gap, .verify-bar-area, .verify-move-block, .verify-sub-block"
                );
                if (!hasVerify) return false;

                // Image de fond visible (le puzzle)
                const sub = root.querySelector(".verify-sub-block");
                if (sub){
                  try{
                    const cs = getComputedStyle(sub);
                    const bg = (cs && cs.backgroundImage) ? cs.backgroundImage : "";
                    if (bg && bg !== "none") return true;
                  }catch(e){}
                }

                // Sinon, fallback: présence du panel + gap + barre
                return !!root.querySelector(".verify-img-panel") && !!root.querySelector(".verify-gap") && !!root.querySelector(".verify-bar-area");
            """))
            if has_slider_puzzle:
                return _captcha_result(True, "slider_captcha_widget")
    except Exception:
        pass

    # 2) Widget visible (taille minimale) : iframe/containers captcha visibles
    # (reCAPTCHA invisible / tracking iframes sont souvent 0x0 ou 1x1)
    # IMPORTANT: on ignore les reCAPTCHA v3/invisible (size=invisible dans l'URL)
    try:
        if bool(driver.execute_script("""
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

              // seuils anti-faux-positifs (1x1, 0x0, etc.)
              if (r.width < 60 || r.height < 40) continue;

              // NOUVEAU: ignorer reCAPTCHA invisible (v3) via paramètre size=invisible dans l'URL
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
              
              // Ignorer les éléments dans .grecaptcha-badge (badge reCAPTCHA v3)
              if (e.closest && e.closest(".grecaptcha-badge")) {
                continue;
              }

              // Vérifier si l'élément est vraiment visible dans le viewport
              const vw = window.innerWidth || document.documentElement.clientWidth;
              const vh = window.innerHeight || document.documentElement.clientHeight;

              // Ignore si complètement hors écran (gauche, droite, haut, bas)
              if (r.right < 0 || r.left > vw || r.bottom < 0 || r.top > vh) continue;

              // Ignore si moins de 30% de l'élément est visible (badge reCAPTCHA invisible partiel)
              const visibleWidth = Math.min(r.right, vw) - Math.max(r.left, 0);
              const visibleHeight = Math.min(r.bottom, vh) - Math.max(r.top, 0);
              if (visibleWidth < r.width * 0.3 || visibleHeight < r.height * 0.3) continue;
              
              return true;
            }
            return false;
        """)):
            return _captcha_result(True, "visible_captcha_widget")
    except Exception:
        # Fallback Selenium (moins précis, mais garde les seuils taille/visibilité)
        try:
            frames = driver.find_elements(
                By.CSS_SELECTOR,
                "iframe[src*='captcha'], iframe[src*='recaptcha'], iframe[src*='hcaptcha']"
            )
            for fr in frames:
                try:
                    if not fr.is_displayed():
                        continue
                    r = fr.rect or {}
                    src = (fr.get_attribute("src") or "").lower()
                    if "recaptcha" in src and "/anchor" in src:
                        continue
                    if (r.get("width", 0) or 0) >= 60 and (r.get("height", 0) or 0) >= 40:
                        return _captcha_result(True, "selenium_visible_captcha_iframe")
                except Exception:
                    continue
        except Exception:
            pass

    return _captcha_result(False, "no_visible_captcha_challenge")

def is_drag_drop(driver) -> bool:
    def _read_text(el) -> str:
        for getter in (
            lambda: el.text,
            lambda: el.get_attribute("innerText"),
            lambda: el.get_attribute("textContent"),
        ):
            try:
                txt = getter() or ""
                txt = re.sub(r"\s+", " ", txt).strip().lower()
                if txt:
                    return txt
            except Exception:
                continue
        return ""

    def _visible_count(elements) -> int:
        count = 0
        for el in elements:
            try:
                if el.is_displayed():
                    count += 1
            except Exception:
                count += 1
        return count

    try:
        titles = driver.find_elements(
            By.CSS_SELECTOR,
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

        draggables = driver.find_elements(By.CSS_SELECTOR, "[cdkdrag], .cdk-drag, [draggable='true']")
        drop_zones = driver.find_elements(
            By.CSS_SELECTOR,
            "#dropZoneList, [cdkdroplist].drop-zone, [cdkdroplist][aria-label*='Drop zone'], [cdkdroplist]",
        )

        return has_instruction and _visible_count(draggables) >= 2 and _visible_count(drop_zones) >= 1
    except Exception:
        return False

def is_matrix(driver) -> bool:
    radios = driver.find_elements(By.CSS_SELECTOR, "input[type='radio'], [role='radio']")
    if len(radios) < 6:
        return False
    ys = [r.rect.get("y", 0) for r in radios]
    return len(set(round(y / 30) for y in ys)) >= 2

def is_date_multi_dropdown(driver) -> bool:
    selects = driver.find_elements(By.TAG_NAME, "select")
    if len(selects) < 2:
        return False
    txt = _page_text_lc(driver)
    return any(k in txt for k in ["année", "annee", "year", "mois", "month"])

def is_open_textarea(driver) -> bool:
    """Vrai uniquement si un textarea est réellement exploitable (visible).

    Raison: certains providers injectent des <textarea> cachés (tracking/params).
    Si on les considère, la page est classée "textarea" à tort (comme le screen CMIX),
    ce qui pollue les logs/metrics et peut déclencher des handlers inadaptés.
    """
    try:
        tas = driver.find_elements(By.TAG_NAME, "textarea")
    except Exception:
        return False

    for ta in tas:
        try:
            if not ta.is_displayed():
                continue
            r = ta.rect or {}
            if float(r.get("width") or 0) < 20 or float(r.get("height") or 0) < 20:
                continue
            return True
        except Exception:
            continue
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
    Retourne le premier mapping DOM_REGISTRY qui match
    et enregistre les métriques d'usage OpenAI.
    """
    for rule in DOM_REGISTRY:
        try:
            if rule["signature"](driver):
                dom_metrics.record_dom_classification(
                    itype=rule["itype"],
                    openai=rule["openai"],
                )

                # ✅ IMPORTANT: on ne renvoie PAS la fonction signature (non sérialisable)
                public = dict(rule)
                sig = public.pop("signature", None)
                if callable(sig):
                    public["signature_name"] = getattr(sig, "__name__", "signature")
                return public
        except Exception:
            continue

    dom_metrics.record_dom_classification(
        itype="unclassified",
        openai=True,
    )
    return None
