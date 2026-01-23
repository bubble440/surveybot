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
from typing import Callable, Optional, Dict, Any
import Survey.dom_metrics as dom_metrics
from selenium.webdriver.common.by import By
from Survey.frame_utils import iter_frame_chains, switch_to_frame_chain
# ============================================================
# Utils
# ============================================================

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

            // 0) IMPORTANT: virer les scripts/templates qui contiennent souvent des messages d’erreur (dont "captcha")
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

    # --- contrôle explicite type consent (checkbox/radio avec libellé agree/accept/consent) ---
    def _has_explicit_consent_control() -> bool:
        markers = {
            "agree", "accept", "consent", "gdpr", "rgpd", "cookie", "cookies",
            "j'accepte", "j accepte", "i agree", "i accept"
        }
        try:
            inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox'], input[type='radio']")
            for inp in inputs:
                try:
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
                return !!document.querySelector(sels.join(','));
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
    txt = _page_text_lc(driver)
    return any(k in txt for k in ["bienvenue", "welcome", "commencer", "start"]) and \
           len(driver.find_elements(By.CSS_SELECTOR, "input, select, textarea")) == 0

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
    # 1) Un end-screen doit contenir un signal explicite de fin (pas juste "merci")
    txt = _page_text_lc(driver)

    strong_end = any(k in txt for k in [
        "thank you for",
        "survey complete",
        "completed",
        "fin du sondage",
        "sondage terminé",
        "enquête terminée",
        "vous avez terminé",
        "merci de votre participation",
        "merci pour votre participation",
        "merci d'avoir participé",
    ])

    if not strong_end:
        return False

    # 2) ET ne doit pas contenir d’inputs de réponse visibles
    if _has_visible_answerables(driver):
        return False

    return True

def is_captcha_screen(driver) -> bool:
    """
    Détecte un CAPTCHA *réellement bloquant*.
    Évite les faux positifs quand reCAPTCHA est chargé en background (invisible/1x1).
    """

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
        return True

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
                        return True
                except Exception:
                    continue
    except Exception:
        pass

    # "captcha" seul est trop bruité (templates / erreurs non visibles).
    # On n’accepte "captcha/hcaptcha" que si un widget/contrôle captcha *visible* est présent,
    # ou si la page n’a aucune réponse exploitable (rare mais possible sur des interstitiels).
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
                // Seuils : iframe/widget doit être “grand”, input captcha peut être petit
                if (tn === "iframe" || e.classList.contains("g-recaptcha") || e.classList.contains("h-captcha")) {
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
            return True

        # Pas de widget visible => on ne classe pas captcha si on peut répondre à la page
        if _has_visible_answerables(driver):
            return False

        # Sinon (page stérile + mot captcha) => on garde captcha
        return True

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
                return True
    except Exception:
        pass

    # 1ter) Slider puzzle (NIQ / GfK mrIWeb) : "faites glisser le curseur..."
    # On le traite comme CAPTCHA car c'est une vérification humaine bloquante (pas une question).
    try:
        if any(k in txt for k in [
            "faites glisser le curseur",
            "veuillez faire glisser le curseur",
            "glisser le curseur vers la droite",
            "pour compléter la partie manquante de l’image",
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
                return True
    except Exception:
        pass

    # 2) Widget visible (taille minimale) : iframe/containers captcha visibles
    # (reCAPTCHA invisible / tracking iframes sont souvent 0x0 ou 1x1)
    try:
        return bool(driver.execute_script("""
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

              // ignore si complètement hors écran
              if (r.bottom < 0 || r.right < 0) continue;

              return true;
            }
            return false;
        """))
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
                    if (r.get("width", 0) or 0) >= 60 and (r.get("height", 0) or 0) >= 40:
                        return True
                except Exception:
                    continue
        except Exception:
            pass

    return False

def is_drag_drop(driver) -> bool:
    return bool(
        driver.find_elements(By.CSS_SELECTOR, "[draggable='true'], .cdk-drag")
    )

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
    return bool(driver.find_elements(By.TAG_NAME, "textarea"))

# ============================================================
# DOM REGISTRY (ORDRE CRITIQUE)
# ============================================================

DOM_REGISTRY: list[dict[str, Any]] = [

    # ⛔ Sécurité / hors OpenAI
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
    et enregistre les métriques d’usage OpenAI.
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
