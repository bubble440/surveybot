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

# ============================================================
# Utils
# ============================================================

def _norm_lc(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _page_text_lc(driver) -> str:
    try:
        return " ".join(
            driver.execute_script(
                """
                return Array.from(document.querySelectorAll('body *'))
                  .filter(e => {
                    const s = getComputedStyle(e);
                    return s.display !== 'none' && s.visibility !== 'hidden' && e.offsetParent !== null;
                  })
                  .map(e => e.innerText || '')
                """
            )
        ).lower()
    except Exception:
        return ""

# ============================================================
# Signatures DOM (détecteurs)
# ============================================================

def is_consent_screen(driver) -> bool:
    txt = _page_text_lc(driver)
    return any(k in txt for k in [
        "consent", "cookie", "rgpd", "privacy",
        "politique de confidentialité", "terms"
    ]) and any(
        b.is_displayed()
        for b in driver.find_elements(By.CSS_SELECTOR, "button, a")
        if any(w in (b.text or "").lower() for w in ["accepter", "agree", "continuer", "continue"])
    )

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
    return bool(
        driver.find_elements(By.CSS_SELECTOR, "iframe[src*='captcha'], iframe[src*='recaptcha']")
    )

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
