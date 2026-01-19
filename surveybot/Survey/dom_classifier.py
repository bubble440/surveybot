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

            // supprimer les zones footer-like (sinon faux positifs 'privacy policy' etc.)
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

    if _has_explicit_consent_control():
        return True

    if _has_blocking_cmp_overlay():
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
    strong_kw = [
        "je ne suis pas un robot",
        "i'm not a robot",
        "im not a robot",
        "verify you are human",
        "human verification",
        "vérifiez que vous êtes",
        "verification humaine",
        "captcha",
        "hcaptcha",
    ]
    if any(k in txt for k in strong_kw):
        return True

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
