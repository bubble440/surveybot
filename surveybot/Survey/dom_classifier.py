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
    IMPORTANT: Decipher/Confirmit mettent souvent tout le contenu dans un iframe.
    Donc on collecte le texte visible sur default_content + iframes (profondeur 2).
    """
    js = """
        const els = Array.from(document.querySelectorAll('body *'));
        const out = [];
        for (const e of els){
          try{
            const s = getComputedStyle(e);
            if (s.display === 'none' || s.visibility === 'hidden') continue;
            const r = e.getBoundingClientRect();
            if (!r || r.width < 2 || r.height < 2) continue;
            const t = (e.innerText || '').trim();
            if (t) out.push(t);
          }catch(_){}
        }
        return out;
    """
    chunks = []
    for chain in iter_frame_chains(driver, max_depth=2):
        with switch_to_frame_chain(driver, chain) as ok:
            if not ok:
                continue
            try:
                arr = driver.execute_script(js) or []
                if arr:
                    chunks.append(" ".join(arr))
            except Exception:
                continue
    return " ".join(chunks).lower()

# ============================================================
# Signatures DOM (détecteurs)
# ============================================================

def is_consent_screen(driver) -> bool:
    """
    Détecte un écran de consentement / RGPD / privacy (y compris CTA-only comme rx.samplicio).
    Objectif: bypass OpenAI/vision et cliquer localement sur "Accepter/Continuer".

    Heuristique:
    - mots-clés consent/privacy/cookies/rgpd…
    - ET (checkbox/radio visibles OU présence d’un CTA "accept/agree/accepter/continuer" ou id "*agree*")
    - en scannant default_content + iframes (profondeur 2)
    """
    txt = _page_text_lc(driver)

    has_kw = any(k in txt for k in [
        "consent", "gdpr", "rgpd", "privacy",
        "politique de confidentialité", "confidentialité",
        "terms", "cookie", "cookies",
        "consent form",
    ])
    if not has_kw:
        return False

    bad_words = ("refuser", "disagree", "quitter", "quit", "exit", "annuler", "cancel", "fermer", "close")
    good_words = (
        "accepter", "j'accepte", "agree", "accept",
        "continuer", "continue", "proceed",
        "suivant", "next",
        "commencer", "start", "begin",
        "ok", "d'accord"
    )

    def _has_agree_cta_here() -> bool:
        # ids typiques: gtm-agree-button / agree / accept / consent…
        try:
            if driver.find_elements(By.CSS_SELECTOR, "#gtm-agree-button"):
                return True
        except Exception:
            pass

        sel = "button, [role='button'], input[type='button'], input[type='submit'], a"
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel) or []
        except Exception:
            els = []

        for el in els[:120]:  # cap perf
            try:
                t = _norm_lc(el.text or el.get_attribute("value") or el.get_attribute("aria-label") or "")
                _id = _norm_lc(el.get_attribute("id") or "")
                if not t and not _id:
                    continue

                # ne jamais considérer un CTA "refuser/quitter" comme positif
                if any(b in t for b in bad_words) or ("disagree" in _id) or ("quit" in _id):
                    continue

                # id "agree/accept/consent" -> très fort signal
                if any(k in _id for k in ("agree", "accept", "consent")):
                    return True

                # texte positif
                if any(g in t for g in good_words):
                    return True
            except Exception:
                continue

        return False

    # a) choix explicites (checkbox/radio)
    has_choice = False
    for chain in iter_frame_chains(driver, max_depth=2):
        with switch_to_frame_chain(driver, chain) as ok:
            if not ok:
                continue
            try:
                if driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox'], [role='checkbox'], input[type='radio'], [role='radio']"):
                    has_choice = True
                    break
            except Exception:
                continue

    # b) CTA accept/continue (même sans checkbox)
    has_agree_cta = False
    for chain in iter_frame_chains(driver, max_depth=2):
        with switch_to_frame_chain(driver, chain) as ok:
            if not ok:
                continue
            try:
                if _has_agree_cta_here():
                    has_agree_cta = True
                    break
            except Exception:
                continue

    return bool(has_choice or has_agree_cta)

def is_start_screen(driver) -> bool:
    txt = _page_text_lc(driver)
    return any(k in txt for k in ["bienvenue", "welcome", "commencer", "start"]) and \
           len(driver.find_elements(By.CSS_SELECTOR, "input, select, textarea")) == 0

def _has_visible_answerables(driver) -> bool:
    """
    True si la page contient des éléments de réponse visibles (radio/checkbox/select/input/textarea).
    Objectif: empêcher une classification end-screen quand la page est une vraie question.
    """
    try:
        return bool(driver.execute_script("""
            const sels = [
              "input:not([type='hidden']):not([type='submit']):not([type='button'])",
              "textarea",
              "select",
              "[role='radio']",
              "[role='checkbox']",
              "[contenteditable='true']"
            ];
            const els = Array.from(document.querySelectorAll(sels.join(",")));
            for (const e of els) {
              const cs = getComputedStyle(e);
              if (cs.display === "none" || cs.visibility === "hidden") continue;

              // offsetParent null => généralement invisible (sauf position fixed), on garde le check rect
              const r = e.getBoundingClientRect();
              if (!r || r.width < 4 || r.height < 4) continue;

              return true;
            }
            return false;
        """))
    except Exception:
        return False

def is_end_screen(driver):
    # 1) Un end-screen doit contenir un signal explicite de fin
    txt = _page_text_lc(driver)
    has_end_keyword = any(k in txt for k in [
        "thank you",
        "merci",
        "fin du sondage",
        "sondage terminé",
        "enquête terminée",
        "completed",
        "survey complete",
        "vous avez terminé",
    ])

    if not has_end_keyword:
        return False

    # 2) ET ne doit pas contenir d’inputs de réponse visibles
    # (Sinon on est sur une vraie question, comme ton écran “secteur d’activité”.)
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
        "itype": "consent_screen",
        "signature": is_consent_screen,
        "handler": "handle_consent_screen",
        "openai": False,
    },
    {
        "itype": "start_screen",
        "signature": is_start_screen,
        "handler": "handle_start_screen",
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
                return rule
        except Exception:
            continue

    # Aucun match → par défaut OpenAI (questions standards)
    dom_metrics.record_dom_classification(
        itype="unclassified",
        openai=True,
    )
    return None
