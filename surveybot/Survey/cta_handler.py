"""
cta_handler.py - Gestion des CTA (Call To Action) et boutons de navigation

Ce module contient:
- click_button_by_text: clic sur bouton par texte
- click_icon_like_button: clic sur bouton icône (sans texte)
- click_primary_cta: clic sur le CTA principal
- try_click_navigation_cta: recherche et clic CTA navigation
- Variantes *_any_context: recherche dans les iframes
- click_cta_strong_any_context: version robuste multi-frame

Dépendances:
- input_utils pour les fonctions utilitaires
- frame_utils pour la navigation iframe
"""

from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException
import unicodedata
import re
from urllib.parse import urlsplit
import time
import os

from Survey.log_utils import log_debug, log_info


# =============================================================================
# CONSTANTES CTA
# =============================================================================

CTA_SYNONYMS = {
    "continuer", "suivant", "start", "commencer", "démarrer",
    "accepter", "accepter et commencer", "next", "continue",
    "submit", "soumettre", "valider", "proceed", "begin",
    "envoyer", "terminer", "send", "confirmer", "confirmez", "confirm",
}

CTA_INTERCEPT_ENV_VAR = "CTA_INTERCEPT_ONLY"
MIN_NAV_CTA_SCORE = 1

PAUSE_AFTER_CTA_CLICK = 1.0  # pause post-clic CTA, laisse le DOM réagir avant de rendre la main (absorbe latence proxy)


# =============================================================================
# HELPERS CTA
# =============================================================================

def _normalize_lbl(s: str) -> str:
    """Normalise un label de bouton pour comparaison."""
    if not s:
        return ""
    s = s.replace("\u00a0", " ")
    s = unicodedata.normalize("NFKC", s).strip().lower()
    s = re.sub(r"[»«""\"'›→·•:]+", "", s)
    s = re.sub(r"\s+", " ", s)
    return s


def _norm_btn_text(s: str) -> str:
    """Normalise le texte d'un bouton."""
    s = re.sub(r"\s+", " ", (s or "")).strip().lower()
    s = s.replace("→", " ").replace("»", " ").replace(">", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def looks_like_nav_label(s: str) -> bool:
    """
    Détermine si un texte ressemble à un label de navigation (CTA).
    """
    if not s:
        return False
    s = s.lower().strip()
    nav_kw = {
        "continuer", "suivant", "start", "commencer", "démarrer",
        "accepter", "accepter et commencer", "next", "continue",
        "submit", "soumettre", "valider", "confirmer", "confirmez", "confirm",
    }
    return any(k in s for k in nav_kw)


def _is_visible(driver, el) -> bool:
    """Vérifie si un élément est visible et a une taille suffisante."""
    try:
        if not el.is_displayed():
            return False
        box = el.rect
        return box and box.get("width", 0) > 5 and box.get("height", 0) > 5
    except Exception:
        return False


def _cta_intercept_enabled() -> bool:
    """Retourne True si le mode interception CTA est activé via variable d'environnement."""
    raw = (os.getenv(CTA_INTERCEPT_ENV_VAR, "") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _is_intellisurvey_structural_submit_cta(el) -> bool:
    """Détecte un CTA submit IntelliSurvey identifiable uniquement par sa structure DOM."""
    try:
        tag = (el.tag_name or "").strip().lower()
    except Exception:
        return False

    if tag != "input":
        return False

    input_type = (el.get_attribute("type") or "").strip().lower()
    if input_type != "submit":
        return False

    el_id = (el.get_attribute("id") or "").strip().lower()
    el_name = (el.get_attribute("name") or "").strip().lower()
    cls = (el.get_attribute("class") or "").strip().lower()

    return bool(
        el_id == "contbtn"
        or el_name == "contbtn"
        or "i-contbtn" in cls
    )


def _is_mriweb_structural_submit_cta(el) -> bool:
    """Détecte le vrai CTA submit mrIWeb (`input[type=submit][name=_NNext].mrNext`)."""
    try:
        tag = (el.tag_name or "").strip().lower()
    except Exception:
        return False

    if tag != "input":
        return False

    input_type = (el.get_attribute("type") or "").strip().lower()
    if input_type != "submit":
        return False

    el_name = (el.get_attribute("name") or "").strip().lower()
    cls = (el.get_attribute("class") or "").strip().lower()

    return el_name == "_nnext" and "mrnext" in cls


def _is_mriweb_vue_next_cta(el) -> bool:
    """Détecte le CTA Vue mrIWeb visible (`span#NextBtn.NavBtn.btn_visible`)."""
    try:
        tag = (el.tag_name or "").strip().lower()
    except Exception:
        return False

    if tag != "span":
        return False

    el_id = (el.get_attribute("id") or "").strip().lower()
    if el_id != "nextbtn":
        return False

    cls = (el.get_attribute("class") or "").strip().lower()
    return (
        "navbtn" in cls
        and "clickable" in cls
        and "btn_visible" in cls
    )


def _is_inline_hidden_cta(el) -> bool:
    """Retourne True si le style inline masque explicitement le CTA (opacity:0 + visibility:hidden)."""
    try:
        style = (el.get_attribute("style") or "").strip().lower()
    except Exception:
        return False

    if not style:
        return False

    normalized_style = re.sub(r"\s+", "", style)
    return "opacity:0" in normalized_style and "visibility:hidden" in normalized_style


def _is_internal_task_carousel_arrow(driver, el) -> bool:
    """
    Exclut les flches de carousel de tche (ex: Quantilope x/12)
    du ciblage CTA de navigation page.
    Critères DOM minimaux et observables:
    - bouton avec data-cy=left-arrow|right-arrow
    - présence d'un compteur p[data-cy="task-counter"] au format x/y (y >= 2)
    """
    try:
        data_cy = (el.get_attribute("data-cy") or "").strip().lower()
    except Exception:
        data_cy = ""

    if data_cy not in {"left-arrow", "right-arrow"}:
        return False

    try:
        counters = driver.find_elements(By.CSS_SELECTOR, 'p[data-cy="task-counter"]')
    except Exception:
        counters = []

    for counter in counters:
        try:
            txt = _norm_btn_text(counter.text or "")
            m = re.search(r"\b(\d{1,3})\s*/\s*(\d{1,3})\b", txt)
            if m and int(m.group(2)) >= 2:
                return True
        except Exception:
            continue
    return False


def _read_arm_error(driver) -> str:
    """Retourne le dernier message d'erreur d'armement JS si présent."""
    try:
        err = driver.execute_script("return window.__sbCtaInterceptLastError || null;")
        if isinstance(err, str) and err.strip():
            return err.strip()
    except Exception:
        pass
    return ""
def disarm_interceptor(driver) -> bool:
    """Désarme l'intercepteur JS (sans retirer les listeners installés une fois)."""
    js = """
    (() => {
      try {
        const s = window.__sbCtaIntercept;
        if (s) s.armed = false;
        // Nettoyage soft : le token courant n'est plus actif
        window.__sbCtaInterceptToken = null;
        return true;
      } catch (e) {
        return false;
      }
    })();
    """
    try:
        return bool(driver.execute_script(js))
    except Exception:
        return False

def arm_interceptor(driver) -> bool:
    """Arme l'intercepteur JS pour capter/bloquer click+submit+navigations scriptées."""
    js = """
    (() => {
    try {
      const mkTarget = (el) => {
        if (!el) return null;
        const txt = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
        return {
          tag: (el.tagName || '').toLowerCase(),
          id: el.id || '',
          name: el.getAttribute ? (el.getAttribute('name') || '') : '',
          className: el.className || '',
          textPreview: txt.slice(0, 120),
        };
      };

      const state = {
        armed: true,
        clickCaptured: false,
        submitCaptured: false,
        prevented: false,
        navigationBlocked: false,
        ts: Date.now(),
        target: null,
      };

      window.__sbCtaIntercept = state;
      // Status / debug : permet de diagnostiquer les pages "restrictive".
      window.__sbCtaInterceptArmedOk = true;
      window.__sbCtaInterceptLastError = null;

      // Token attendu pour filtrer l'interception au CTA ciblé uniquement.
      // (défini côté Python juste avant le dispatch du click synthétique)
      if (window.__sbCtaInterceptToken === undefined) window.__sbCtaInterceptToken = null;

      if (!window.__sbCtaInterceptInstalled) {
        window.__sbCtaInterceptInstalled = true;

        const safePatch = (fn) => {
          try {
            fn();
          } catch (e) {
            // Best-effort interception: un patch optionnel ne doit jamais faire échouer l'armement.
          }
        };

        window.addEventListener('click', (evt) => {
          const s = window.__sbCtaIntercept;
          if (!s || !s.armed) return;
          // Filtrage STRICT: on ne bloque que si le clic touche le CTA ciblé (token).
          const tok = window.__sbCtaInterceptToken;
          if (!tok) return;
          const hit = (evt.target && evt.target.closest)
            ? evt.target.closest(`[data-sb-cta-token="${tok}"]`)
            : null;
          if (!hit) return;

          s.clickCaptured = true;
          s.prevented = true;
          s.ts = Date.now();
          s.target = mkTarget(evt.target);
          s.target = mkTarget(hit);
          evt.preventDefault();
          evt.stopPropagation();
          evt.stopImmediatePropagation();
        }, true);

        window.addEventListener('submit', (evt) => {
          const s = window.__sbCtaIntercept;
          if (!s || !s.armed) return;
          // Même règle: ne bloquer submit que si ça vient du formulaire contenant le CTA token.
          const tok = window.__sbCtaInterceptToken;
          if (tok && evt.target && evt.target.querySelector) {
            const hasTok = evt.target.querySelector(`[data-sb-cta-token="${tok}"]`);
            if (!hasTok) return;
          } else if (tok) {
            return;
          }
          s.submitCaptured = true;
          s.prevented = true;
          s.ts = Date.now();
          s.target = mkTarget(evt.target);
          evt.preventDefault();
          evt.stopPropagation();
          evt.stopImmediatePropagation();
        }, true);

        window.addEventListener('beforeunload', (evt) => {
          const s = window.__sbCtaIntercept;
          if (!s || !s.armed) return;
          s.navigationBlocked = true;
          s.prevented = true;
          s.ts = Date.now();
          evt.preventDefault();
          evt.returnValue = '';
        }, true);

        safePatch(() => {
          if (window.__sbHistPatched) return;
          window.__sbHistPatched = true;
          const oldPush = history.pushState.bind(history);
          const oldReplace = history.replaceState.bind(history);
          history.pushState = function(...args) {
            const s = window.__sbCtaIntercept;
            if (s && s.armed) {
              s.navigationBlocked = true;
              s.prevented = true;
              s.ts = Date.now();
              return null;
            }
            return oldPush(...args);
          };
          history.replaceState = function(...args) {
            const s = window.__sbCtaIntercept;
            if (s && s.armed) {
              s.navigationBlocked = true;
              s.prevented = true;
              s.ts = Date.now();
              return null;
            }
            return oldReplace(...args);
          };
        });

        safePatch(() => {
          if (window.__sbFormSubmitPatched) return;
          window.__sbFormSubmitPatched = true;
          const oldSubmit = HTMLFormElement.prototype.submit;
          HTMLFormElement.prototype.submit = function(...args) {
            const s = window.__sbCtaIntercept;
            if (s && s.armed) {
              s.submitCaptured = true;
              s.navigationBlocked = true;
              s.prevented = true;
              s.ts = Date.now();
              s.target = mkTarget(this);
              return;
            }
            return oldSubmit.apply(this, args);
          };
        });

        safePatch(() => {
          if (window.__sbLocationPatched) return;
          window.__sbLocationPatched = true;
          const oldAssign = window.location.assign.bind(window.location);
          const oldReplace = window.location.replace.bind(window.location);
          window.location.assign = function(...args) {
            const s = window.__sbCtaIntercept;
            if (s && s.armed) {
              s.navigationBlocked = true;
              s.prevented = true;
              s.ts = Date.now();
              return;
            }
            return oldAssign(...args);
          };
          window.location.replace = function(...args) {
            const s = window.__sbCtaIntercept;
            if (s && s.armed) {
              s.navigationBlocked = true;
              s.prevented = true;
              s.ts = Date.now();
              return;
            }
            return oldReplace(...args);
          };
        });
      }

      return true;
      } catch (e) {
        // Important: on ne doit pas "hard fail" côté Python (sinon failed_to_arm=true et aucun clic).
        // On enregistre l'erreur et on retourne true pour garder un comportement prédictible.
        try {
          window.__sbCtaInterceptArmedOk = false;
          const msg = (e && (e.message || e.toString)) ? (e.message || e.toString()) : 'unknown';
          const name = (e && e.name) ? e.name : 'Error';
          window.__sbCtaInterceptLastError = `${name}: ${msg}`.slice(0, 400);
        } catch (_e2) {}
        return true;
      }
    })();
    """
    try:
        return bool(driver.execute_script(js))
    except Exception:
        return False


def read_intercept_report(driver):
    """Retourne le rapport d'interception CTA depuis window.__sbCtaIntercept."""
    try:
        return driver.execute_script("return window.__sbCtaIntercept || null;")
    except Exception:
        return None

def _probe_interceptor_state(driver):
    """
    Probe robuste (best-effort) pour savoir si l'intercepteur est réellement armé,
    au lieu de se fier uniquement au retour de arm_interceptor() qui peut être trompeur
    (pages restrictives / execute_script partiellement bloqué).
    """
    js = """
    return (function () {
      try {
        const s = window.__sbCtaIntercept || null;
        return {
          hasState: !!s,
          armed: !!(s && s.armed),
          installed: !!window.__sbCtaInterceptInstalled,
          armedOk: (window.__sbCtaInterceptArmedOk !== false),
          lastError: window.__sbCtaInterceptLastError || null,
        };
      } catch (e) {
        return { probeError: true, msg: (e && (e.message || String(e))) || "unknown" };
      }
    })();
    """
    try:
        v = driver.execute_script(js)
        return v if isinstance(v, dict) else {"probeError": True, "msg": "non-dict"}
    except Exception as e:
        return {"probeError": True, "msg": str(e)}

def _format_intercept_target(target) -> str:
    if not isinstance(target, dict):
        return "<none>"
    parts = [
        f"tag={target.get('tag') or ''}",
        f"id={target.get('id') or ''}",
        f"name={target.get('name') or ''}",
        f"class={target.get('className') or ''}",
        f"text={target.get('textPreview') or ''}",
    ]
    return " ".join(parts)

def _safe_url(driver) -> str:
    try:
        u = driver.current_url
        if not u:
            return "<unknown>"
        p = urlsplit(u)
        # Format "ATTACH": scheme + host uniquement (pas de path/query)
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}"
        return u
    except Exception:
        return "<unknown>"


def _recover_overlay_cta_text(driver, el) -> str:
    """
    Récupère le texte CTA depuis un label frère pour les overlays cliquables
    de type "oc_inX" / "oc_tX" (DOM start_screen observé).
    """
    try:
        el_id = (el.get_attribute("id") or "").strip()
    except Exception:
        return ""

    m = re.fullmatch(r"oc_in(\d+)", el_id)
    if not m:
        return ""

    idx = m.group(1)
    try:
        labels = driver.find_elements(By.CSS_SELECTOR, f"#oc_t{idx}")
    except Exception:
        labels = []

    if not labels:
        return ""

    try:
        return (labels[0].text or labels[0].get_attribute("innerText") or "").strip()
    except Exception:
        return ""

def _nav_log(prefix: str, msg: str, driver=None):
    url = ""
    if driver is not None:
        url = f" url={_safe_url(driver)}"
    log_info(prefix, f"{msg}{url}")


def _dom_progress_marker(driver):
    """Construit un marqueur léger pour détecter une progression de page."""
    js = """
    return (function () {
      try {
        const url = String(location.href || '');
        const root = document.querySelector('#root') || document.body;
        const txt = ((root && (root.innerText || root.textContent)) || '')
          .replace(/\s+/g, ' ')
          .trim()
          .slice(0, 220);
        const qNodes = document.querySelectorAll(
          'input, textarea, select, [role="radio"], [role="checkbox"], [data-testid*="question"], [class*="question"]'
        ).length;
        const activeNotif = document.querySelector('.siteNotification.error, .siteNotification.success, .siteNotification.warning');
        let notifSig = '';
        if (activeNotif) {
          const klass = String(activeNotif.className || '').replace(/\s+/g, ' ').trim();
          const msgEl = activeNotif.querySelector('.message') || activeNotif;
          const msg = String((msgEl && (msgEl.innerText || msgEl.textContent)) || '')
            .replace(/\s+/g, ' ')
            .trim()
            .slice(0, 140);
          notifSig = `${klass}::${msg}`;
        }
        return { url, txt, qNodes, notifSig };
      } catch (e) {
        return { url: '', txt: '', qNodes: -1, notifSig: '' };
      }
    })();
    """
    try:
        marker = driver.execute_script(js)
        return marker if isinstance(marker, dict) else {"url": "", "txt": "", "qNodes": -1, "notifSig": ""}
    except Exception:
        return {"url": "", "txt": "", "qNodes": -1, "notifSig": ""}


def _did_progress(before_marker, after_marker) -> bool:
    """Détecte une progression via URL ou fingerprint DOM léger."""
    if not isinstance(before_marker, dict) or not isinstance(after_marker, dict):
        return False

    # Contexte sans marqueur exploitable (ex: driver de test minimal):
    # on évite un 2e clic inutile.
    if (
        int(before_marker.get("qNodes") or -1) == -1
        and int(after_marker.get("qNodes") or -1) == -1
        and not (before_marker.get("url") or "")
        and not (after_marker.get("url") or "")
    ):
        return True

    before_url = before_marker.get("url") or ""
    after_url = after_marker.get("url") or ""
    if before_url and after_url and before_url != after_url:
        return True
    return (
        (before_marker.get("txt") or "") != (after_marker.get("txt") or "")
        or (before_marker.get("notifSig") or "") != (after_marker.get("notifSig") or "")
        or int(before_marker.get("qNodes") or -1) != int(after_marker.get("qNodes") or -1)
    )


def _wait_post_click_stabilization(driver, el, before_marker, timeout_s=5.0):
    """
    Attend une réaction post-clic avant l'évaluation PROGRESSED.
    Critères d'attente (DOM-first) :
    - changement d'URL
    - changement du marqueur de progression (texte utile / qNodes)

    Note: les mutations locales du CTA cliqué (stale/déconnectée/remplacée/cachée)
    ne valident pas à elles seules une progression; elles servent seulement au
    diagnostic et à prolonger l'attente jusqu'au timeout si aucun signal fort
    de progression n'apparaît.
    """
    before_url = (before_marker or {}).get("url") or ""
    before_outer = ""
    try:
        before_outer = driver.execute_script("return arguments[0] ? (arguments[0].outerHTML || '') : '';", el) or ""
    except Exception:
        before_outer = ""

    state = {
        "after_marker": _dom_progress_marker(driver),
        "target_changed": False,
        "reason": "timeout",
    }

    def _condition(_):
        after_marker = _dom_progress_marker(driver)
        state["after_marker"] = after_marker

        after_url = after_marker.get("url") or ""
        if before_url and after_url and before_url != after_url:
            state["reason"] = "url_changed"
            return True

        try:
            is_connected = driver.execute_script("return (arguments[0] && arguments[0].isConnected);", el)
            if is_connected is False:
                state["target_changed"] = True
                if state["reason"] == "timeout":
                    state["reason"] = "target_disconnected"

            if not _is_visible(driver, el):
                state["target_changed"] = True
                if state["reason"] == "timeout":
                    state["reason"] = "target_hidden"

            if before_outer:
                after_outer = driver.execute_script("return arguments[0] ? (arguments[0].outerHTML || '') : '';", el) or ""
                if after_outer and after_outer != before_outer:
                    state["target_changed"] = True
                    if state["reason"] == "timeout":
                        state["reason"] = "target_replaced"
        except StaleElementReferenceException:
            state["target_changed"] = True
            if state["reason"] == "timeout":
                state["reason"] = "target_stale"
        except Exception:
            pass

        if _did_progress(before_marker, after_marker):
            state["reason"] = "dom_marker_changed"
            return True

        return False

    try:
        WebDriverWait(driver, timeout_s, poll_frequency=0.1).until(_condition)
    except TimeoutException:
        pass

    return state["after_marker"], bool(state["target_changed"]), state["reason"]


def _press_click_release(driver, el):
    """Séquence CTA déterministe: down -> pause -> up, puis release JS safety net."""
    release_sent = False

    # Forsta/Confirmit (DOM observé):
    # - button.cf-navigation__button.cf-navigation-next (texte symbolique >>)
    # - button.cf-navigation__button.cf-navigation-ok (texte "OK" sur page info)
    # Le cycle press/release Selenium peut ne pas déclencher le handler attendu,
    # alors qu'un click natif WebElement fonctionne.
    try:
        tag = (el.tag_name or "").lower()
    except Exception:
        tag = ""
    try:
        cls = (el.get_attribute("class") or "").lower()
    except Exception:
        cls = ""
    is_forsta_navigation_button = (
        tag == "button"
        and "cf-navigation__button" in cls
        and ("cf-navigation-next" in cls or "cf-navigation-ok" in cls)
    )

    if is_forsta_navigation_button:
        try:
            el.click()
            return True, False
        except Exception:
            pass

    try:
        ActionChains(driver).move_to_element(el).click_and_hold(el).pause(0.06).release(el).perform()
        click_ok = True
    except Exception:
        click_ok = False

    try:
        release_sent = bool(driver.execute_script(
            """
            const el = arguments[0];
            const mk = (Ctor, type) => {
              try { return new Ctor(type, { bubbles: true, cancelable: true, view: window }); }
              catch(e) { return null; }
            };
            const push = (target, evt) => {
              if (!target || !evt) return;
              try { target.dispatchEvent(evt); } catch(e) {}
            };
            push(el, mk(PointerEvent, 'pointerup'));
            push(document, mk(PointerEvent, 'pointerup'));
            push(el, mk(MouseEvent, 'mouseup'));
            push(document, mk(MouseEvent, 'mouseup'));
            return true;
            """,
            el,
        ))
    except Exception:
        release_sent = False

    return bool(click_ok), bool(release_sent)


def _click_with_intercept(driver, el) -> bool:
    """Clique un CTA en mode normal ou en mode interception non destructif."""
    if not _cta_intercept_enabled():
        before = _dom_progress_marker(driver)
        used = "press_click_release"

        first_ok, first_release = _press_click_release(driver, el)
        after_first, first_target_changed, first_wait_reason = _wait_post_click_stabilization(driver, el, before, timeout_s=5.0)
        progressed = _did_progress(before, after_first)
        log_debug(
            "[CTA_CLICK]",
            f"strategy={used} attempt=1 release_sent={str(first_release).lower()} wait_reason={first_wait_reason} target_changed={str(bool(first_target_changed)).lower()} progressed={str(progressed).lower()}",
        )

        if progressed:
            return True

        # Budget anti-boucle: 1 tentative additionnelle max si pas de progression.
        second_ok, second_release = _press_click_release(driver, el)
        after_second, second_target_changed, second_wait_reason = _wait_post_click_stabilization(driver, el, before, timeout_s=5.0)
        progressed = _did_progress(before, after_second)
        log_debug(
            "[CTA_CLICK]",
            f"strategy={used} attempt=2 release_sent={str(second_release).lower()} wait_reason={second_wait_reason} target_changed={str(bool(second_target_changed)).lower()} progressed={str(progressed).lower()}",
        )
        return bool(progressed)

    # En mode interception : on arme, on marque la cible avec un token, on dispatch,
    # puis on désarme TOUJOURS pour ne jamais bloquer les autres inputs.
    try:
        armed_ok = arm_interceptor(driver)
    except Exception:
        armed_ok = False

    # ✅ Ne pas se fier au booléen: on probe l'état réel après tentative d'armement.
    probe = _probe_interceptor_state(driver)
    is_armed = bool(
        isinstance(probe, dict)
        and probe.get("hasState")
        and probe.get("installed")
        and probe.get("armed")
        and probe.get("armedOk")
    )

    # Si Selenium signale un souci MAIS que le probe dit "armé", alors l'armement a réussi.
    # On ne doit PAS logger failed_to_arm=true dans ce cas (sinon faux diagnostic).
    if (not armed_ok) and is_armed:
        err = _read_arm_error(driver)
        perr = probe.get("msg") if isinstance(probe, dict) else None
        last_js_err = probe.get("lastError") if isinstance(probe, dict) else None
        log_debug("[CTA_INTERCEPT]", "arm_warn=true reason=selenium_execute_script_failed "
            f"probe={probe if isinstance(probe, dict) else '<none>'} "
            f"err={err or last_js_err or perr or '<none>'}")

    if not is_armed:
        # Armement absent (confirmé par probe) : en mode CTA_INTERCEPT_ONLY,
        # on n'a PAS le droit de faire un clic réel potentiellement destructif.
        err = _read_arm_error(driver)
        disarm_interceptor(driver)
        perr = probe.get("msg") if isinstance(probe, dict) else None
        last_js_err = probe.get("lastError") if isinstance(probe, dict) else None
        log_debug("[CTA_INTERCEPT]", "failed_to_arm=true reason=probe_not_armed "
            f"selenium_error={str(not armed_ok).lower()} "
            f"probe={probe if isinstance(probe, dict) else '<none>'} "
            f"err={err or last_js_err or perr or '<none>'}")
        log_info("[CTA_INTERCEPT]", "result=INTERCEPTION_IMPOSSIBLE")
        return False

    token = f"{int(time.time()*1000)}_{os.getpid()}"

    try:
        driver.execute_script(
            """
            const el = arguments[0];
            const tok = arguments[1];
            // Active le filtrage d'interception et marque UNIQUEMENT cet élément.
            window.__sbCtaInterceptToken = tok;
            try { el.setAttribute('data-sb-cta-token', tok); } catch(e) {}
            window.__sbCtaInterceptSelected = {
              tag: (el.tagName || '').toLowerCase(),
              id: el.id || '',
              name: (el.getAttribute && el.getAttribute('name')) || '',
              className: el.className || '',
              textPreview: ((el.innerText || el.textContent || '').replace(/\s+/g,' ').trim()).slice(0,120),
            };
            const evt = new MouseEvent('click', { bubbles: true, cancelable: true, view: window });
            return el.dispatchEvent(evt);
            """,
            el,
            token,
        )
    except Exception:
        # Désarmement garanti pour ne pas bloquer les clics utilisateur.
        disarm_interceptor(driver)
        log_debug("[CTA_INTERCEPT]", "dispatch_error=true")
        return False

    report = None
    deadline = time.time() + 0.5
    while time.time() < deadline:
        report = read_intercept_report(driver)
        if isinstance(report, dict) and report.get("clickCaptured"):
            break
        time.sleep(0.05)

    report = report if isinstance(report, dict) else {}
    # Si l'armement JS a eu une erreur interne, on la log (utile pour Ipsos).
    try:
        if driver.execute_script("return window.__sbCtaInterceptArmedOk === false;"):
            log_debug("[CTA_INTERCEPT]", f"arm_internal_error=true err={_read_arm_error(driver) or '<none>'}")
    except Exception:
        pass
    selected = None
    try:
        selected = driver.execute_script("return window.__sbCtaInterceptSelected || null;")
    except Exception:
        selected = None

    same_target = bool(selected and report.get("target") == selected)
    log_debug("[CTA_INTERCEPT]", f"captured={bool(report.get('clickCaptured'))} "
        f"submitCaptured={bool(report.get('submitCaptured'))} "
        f"prevented={bool(report.get('prevented'))} "
        f"sameTarget={same_target} "
        f"target={_format_intercept_target(report.get('target'))}")
    ok = bool(report.get("clickCaptured") and report.get("prevented"))
    if ok:
        log_info("[CTA_INTERCEPT]", "result=INTERCEPT_OK")
    else:
        log_info("[CTA_INTERCEPT]", "result=INTERCEPTION_IMPOSSIBLE")

    # Nettoyage + désarmement : CRITIQUE pour ne jamais bloquer les autres inputs.
    try:
        driver.execute_script(
            """
            const el = arguments[0];
            try { el.removeAttribute('data-sb-cta-token'); } catch(e) {}
            """,
            el,
        )
    except Exception:
        pass
    disarm_interceptor(driver)
    return ok


# =============================================================================
# IFRAME HELPERS
# =============================================================================

def _iter_iframes_safe(driver):
    """Retourne la liste des <iframe>/<frame> probablement interactifs."""
    frames = []
    for fr in driver.find_elements(By.CSS_SELECTOR, "iframe, frame"):
        try:
            tag = (fr.tag_name or "").strip().lower()
            r = fr.rect
            if fr.is_displayed() and r.get("width", 0) > 20 and r.get("height", 0) > 20:
                frames.append(fr)
                continue

            # Legacy <frameset>/<frame>: certains drivers reportent frame non visible
            # (ou dimensions nulles) malgré un contenu interactif réel.
            if tag == "frame":
                src = (fr.get_attribute("src") or "").strip().lower()
                if src and not src.startswith("about:blank"):
                    frames.append(fr)
        except Exception:
            continue
    return frames


def _in_each_frame_recursive(driver, fn_try, depth=2):
    """
    Appelle fn_try(driver) dans le contexte courant.
    Si échec, essaye récursivement dans chaque iframe (profondeur limitée).
    Reviens toujours au default_content() après chaque descente.
    """
    if depth < 0:
        return False

    # 1) Essai dans le contexte courant
    try:
        if fn_try(driver):
            return True
    except Exception:
        pass

    # 2) Descente dans les iframes si non trouvé
    frames = _iter_iframes_safe(driver)
    for idx, fr in enumerate(frames):
        try:
            tag = (fr.tag_name or "").strip().lower()
            if tag == "frame":
                frame_name = (fr.get_attribute("name") or "").strip()
                frame_id = (fr.get_attribute("id") or "").strip()
                target = frame_name or frame_id
                if target:
                    driver.switch_to.frame(target)
                else:
                    driver.switch_to.frame(idx)
            else:
                driver.switch_to.frame(fr)
            if _in_each_frame_recursive(driver, fn_try, depth - 1):
                driver.switch_to.default_content()
                return True
            driver.switch_to.default_content()
        except Exception:
            try:
                driver.switch_to.default_content()
            except:
                pass
            continue

    return False


# =============================================================================
# CLICK_BUTTON_BY_TEXT
# =============================================================================

def click_button_by_text(driver, text) -> bool:
    """
    Clique un bouton par son texte visible.

    Stratégie multi-niveaux:
    1) Collecte candidats (buttons, inputs, role=button, anchors CTA)
    2) Match par texte normalisé
    3) Fallback XPath large
    4) Fallback JS sur tous les boutons visibles
    5) Si texte ressemble à nav, fallback click_primary_cta

    Args:
        driver: WebDriver
        text: texte du bouton à cliquer

    Returns:
        True si bouton cliqué avec succès
    """
    target = _normalize_lbl(text)
    print(f"Label normalisé: '{target}'; source: cta_handler.py")

    # 1) Candidats "boutons" sûrs
    candidates = []
    candidates += driver.find_elements(By.TAG_NAME, "button")
    candidates += driver.find_elements(
        By.CSS_SELECTOR, "input[type='submit'], input[type='button']"
    )
    candidates += driver.find_elements(
        By.CSS_SELECTOR, "div[role='button'], span[role='button']"
    )

    # Inclure les <a> qui ressemblent à des boutons/CTA
    anchor_ctas = []
    anchor_ctas += driver.find_elements(
        By.CSS_SELECTOR, "a.btn, a.button, a.btn-primary, a.primary, a.cta"
    )
    anchor_ctas += driver.find_elements(
        By.CSS_SELECTOR, "a[class*='btn'], a[class*='button'], a[class*='cta']"
    )
    anchor_ctas += driver.find_elements(By.CSS_SELECTOR, "#btn a")

    def _is_blacklisted_anchor(a):
        lbl = _normalize_lbl(
            (a.get_attribute("innerText") or a.text or a.get_attribute("aria-label") or "")
        )
        href = (a.get_attribute("href") or "").lower()
        bad = ("privacy", "policy", "confidentialit", "cookies", "terms", "conditions", "vie privée", "legal")
        if any(b in lbl for b in bad):
            return True
        return any(b in href for b in bad)

    for a in anchor_ctas:
        try:
            if not _is_blacklisted_anchor(a):
                candidates.append(a)
        except Exception:
            continue

    # 2) Ajouter des <a> qui se comportent comme des boutons
    for a in driver.find_elements(By.TAG_NAME, "a"):
        try:
            role = (a.get_attribute("role") or "").lower()
            href = (a.get_attribute("href") or "").strip().lower()
            looks_like_button = (
                role == "button" or href in ("", "#") or href.startswith("javascript:")
            )
            blacklist = ("privacy", "policy", "cookies", "confidentialit", "terms", "política", "bedingungen")
            if looks_like_button and not any(bad in href for bad in blacklist):
                candidates.append(a)
        except Exception:
            continue

    for el in candidates:
        try:
            lbl = el.get_attribute("value") or el.text
            if not lbl:
                spans = el.find_elements(By.TAG_NAME, "span")
                for sp in spans:
                    if sp.text and sp.text.strip():
                        lbl = sp.text
                        break
            if not lbl:
                continue

            if (
                _normalize_lbl(lbl).find(target) != -1
                or target.find(_normalize_lbl(lbl)) != -1
            ):
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", el
                )
                time.sleep(0.1)
                if _click_with_intercept(driver, el):
                    time.sleep(PAUSE_AFTER_CTA_CLICK)
                    return True
        except Exception:
            continue

    # Fallback 1: XPath large
    try:
        xpath = (
            "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), '{t}')] | "
            "//*[self::div or self::span][@role='button'][contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), '{t}')] | "
            "//input[(translate(@value,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')='{t}') and (@type='submit' or @type='button')] | "
            "//a[(contains(@class,'btn') or contains(@class,'button') or contains(@class,'cta')) "
            " and contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), '{t}')]"
        ).format(t=target)

        elems = driver.find_elements(By.XPATH, xpath)
        for el in elems:
            try:
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", el
                )
                time.sleep(0.1)
                if _click_with_intercept(driver, el):
                    time.sleep(PAUSE_AFTER_CTA_CLICK)
                    return True
            except Exception:
                continue
    except Exception:
        pass

    # Fallback 2: JS sur tous les boutons visibles
    try:
        # IMPORTANT: en mode interception, ne jamais cliquer via JS (bypass _click_with_intercept).
        if _cta_intercept_enabled():
            # Mini-scan "type JS fallback" mais clic via _click_with_intercept => interception armée + pas de navigation.
            candidates2 = []
            candidates2 += driver.find_elements(By.TAG_NAME, "button")
            candidates2 += driver.find_elements(By.CSS_SELECTOR, "input[type='submit'], input[type='button']")
            candidates2 += driver.find_elements(By.CSS_SELECTOR, "[role='button']")

            for el in candidates2:
                try:
                    if not el.is_displayed() or not el.is_enabled():
                        continue
                    label = (el.get_attribute("value") or el.text or el.get_attribute("aria-label") or "").strip()
                    if not label:
                        continue
                    lbl_norm = _normalize_lbl(label)
                    if lbl_norm and (lbl_norm.find(target) != -1 or target.find(lbl_norm) != -1):
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                        time.sleep(0.1)
                        if _click_with_intercept(driver, el):
                            time.sleep(PAUSE_AFTER_CTA_CLICK)
                            return True
                except Exception:
                    continue

            # Si interception active et pas trouvé/cliqué, on n'exécute pas le JS destructif.
            return False


        js = """
        const norm = s => (s||'').toLowerCase()
            .replaceAll('\\u00A0',' ')
            .replace(/[»«""\\"'›→·•:]/g,'')
            .replace(/\\s+/g,' ')
            .trim();
        const target = arguments[0];
        const candidates = Array.from(document.querySelectorAll(
          'button, input[type=submit], input[type=button], [role=button]'
        ));
        for (const el of candidates) {
          const label = (el.value || el.innerText || el.textContent || '').trim();
          if (norm(label).includes(target) || target.includes(norm(label))) {
            el.scrollIntoView({block:'center'});
            el.click();
            return true;
          }
        }
        return false;
        """
        ok = driver.execute_script(js, target)
        if ok:
            time.sleep(PAUSE_AFTER_CTA_CLICK)
            return True
    except Exception:
        pass

    # 5) Dernier fallback: si nav label, cliquer CTA principal
    if looks_like_nav_label(text):
        return click_primary_cta(driver)

    return False


# =============================================================================
# CLICK_ICON_LIKE_BUTTON
# =============================================================================

def click_icon_like_button(driver, hints=None) -> bool:
    """
    Clique un bouton sans texte (icône, flèche, play).

    Heuristique:
    - candidats : button/a/[role=button] visibles
    - score : taille, proximité du centre, présence d'icône/svg/img, hints dans class/aria
    """
    hints = hints or []
    hints_norm = [_normalize_lbl(h) for h in hints if h]

    candidates = []
    candidates += driver.find_elements(By.TAG_NAME, "button")
    candidates += driver.find_elements(By.CSS_SELECTOR, "[role='button']")
    candidates += driver.find_elements(By.TAG_NAME, "a")

    visibles = [el for el in candidates if _is_visible(driver, el)]
    if not visibles:
        return False

    vw = driver.execute_script("return window.innerWidth") or 1200
    vh = driver.execute_script("return window.innerHeight") or 800

    def score(el):
        try:
            r = el.rect
            area = r["width"] * r["height"]
            cx = r["x"] + r["width"] / 2
            cy = r["y"] + r["height"] / 2
            dx = abs(cx - vw / 2)
            dy = abs(cy - vh / 2)
            center = -(dx + dy)

            cls = (el.get_attribute("class") or "").lower()
            aria = (el.get_attribute("aria-label") or "").lower()
            title = (el.get_attribute("title") or "").lower()
            href = (el.get_attribute("href") or "").lower()

            has_icon = False
            try:
                if el.find_elements(By.TAG_NAME, "svg") or el.find_elements(By.TAG_NAME, "img") or el.find_elements(By.TAG_NAME, "i"):
                    has_icon = True
            except Exception:
                pass

            s = area + center
            if has_icon:
                s += 500

            for h in hints_norm:
                if h and (h in cls or h in aria or h in title or h in href):
                    s += 600

            # éviter les liens footer
            if any(b in href for b in ["privacy", "terms", "cookie", "policy"]):
                s -= 800

            return s
        except Exception:
            return -1e9

    visibles.sort(key=score, reverse=True)

    for el in visibles[:6]:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            time.sleep(0.1)
            if _click_with_intercept(driver, el):
                time.sleep(PAUSE_AFTER_CTA_CLICK)
                return True
        except Exception:
            continue

    return False


# =============================================================================
# CLICK_PRIMARY_CTA
# =============================================================================

def click_primary_cta(driver) -> bool:
    """
    Clique le CTA principal d'une page (le plus gros bouton visible).

    Heuristique: plus grand bouton visible et proche du centre de l'écran.

    Returns:
        True si CTA cliqué
    """
    def center_score(el, vw, vh):
        try:
            r = el.rect
            cx = r["x"] + r["width"] / 2
            cy = r["y"] + r["height"] / 2
            dx = abs(cx - vw / 2)
            dy = abs(cy - vh / 2)
            return -(dx + dy)
        except Exception:
            return -1e9

    candidates = []
    candidates += driver.find_elements(By.TAG_NAME, "button")
    candidates += driver.find_elements(
        By.CSS_SELECTOR, "input[type='submit'], input[type='button']"
    )
    candidates += driver.find_elements(By.CSS_SELECTOR, "[role='button']")

    for a in driver.find_elements(By.TAG_NAME, "a"):
        try:
            if (a.get_attribute("role") or "").lower() == "button":
                candidates.append(a)
        except Exception:
            continue

    visibles = [el for el in candidates if _is_visible(driver, el)]

    if not visibles:
        print("✗ Aucun CTA visible. source: cta_handler.py")
        return False

    vw = driver.execute_script("return window.innerWidth") or 1200
    vh = driver.execute_script("return window.innerHeight") or 800

    def score(el):
        try:
            r = el.rect
            area = r["width"] * r["height"]
            return area + 2000 + center_score(el, vw, vh)
        except Exception:
            return 0

    visibles.sort(key=score, reverse=True)

    for el in visibles:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            time.sleep(0.1)
            if _click_with_intercept(driver, el):
                time.sleep(PAUSE_AFTER_CTA_CLICK)
                print("✓ CTA principal cliqué. source: cta_handler.py")
                return True
        except Exception:
            continue

    print("✗ Impossible de cliquer le CTA principal. source: cta_handler.py")
    return False


# =============================================================================
# TRY_CLICK_NAVIGATION_CTA
# =============================================================================

def try_click_navigation_cta(driver) -> bool:
    """
    Cherche un CTA de navigation (Continue/Suivant/Next/Valider…)
    et clique le meilleur candidat visible.

    Supporte:
    - AreYouNet (#btn_next, EnqueteDef_submit)
    - Decipher (#btn_continue)
    - RSCH (#btnsmall, .enterButton.submitButton)
    - Boutons génériques avec scoring

    Returns:
        True si CTA navigation cliqué
    """
    # --- MetrixLab / Toluna: CTA icon-only <div id="next" class="next ..."> ---
    # DOM observé:
    #   <div class="next arrow_on" id="next" style="display:block !important"> ... </div>
    # CTA sans texte => doit être ciblé par signature DOM précise, pas par label.
    try:
        next_nodes = driver.find_elements(By.CSS_SELECTOR, ".footer #next, #next.next")
        for el in next_nodes:
            try:
                if not el.is_displayed() or not el.is_enabled():
                    continue
                cls = (el.get_attribute("class") or "").lower()
                if "next" not in cls:
                    continue
                rect = el.rect or {}
                if rect.get("width", 0) < 24 or rect.get("height", 0) < 24:
                    continue
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                _nav_log("[CTA_NAV]", "CTA_FOUND pattern=icon_div_next id=next", driver)
                clicked = _click_with_intercept(driver, el)
                _nav_log("[CTA_NAV]", f"CTA_CLICKED pattern=icon_div_next id=next PROGRESSED={str(bool(clicked)).lower()}", driver)
                if clicked:
                    if _cta_intercept_enabled():
                        _nav_log("[CTA_NAV]", "INTERCEPT_OK pattern=icon_div_next id=next", driver)
                    return True
            except Exception:
                continue
    except Exception:
        pass

    # --- B3netSurvey / ask.dll : CTA image (Play) dans #NAVBAR ---
    # Exemple DOM:
    #   <table id="NAVBAR"> ... <a href="javascript:Next();" title="Page suivante">
    #       <img id="nextButton" class="BtnDuBas" ...>
    #   </a>
    # Ici, le CTA n'a souvent AUCUN texte; on doit utiliser href/title/img.
    try:
        # 1) Clic direct du <a> "Next" dans la navbar
        nav_links = driver.find_elements(
            By.CSS_SELECTOR,
            "#NAVBAR a[href^='javascript:Next'], #NAVBAR a[title*='suivante'], a[href^='javascript:Next'][title], a[title*='Page suivante']",
        )
        for a in nav_links:
            try:
                if not a.is_displayed():
                    continue
                # éviter un éventuel Prev() si la page contient les 2
                href = (a.get_attribute("href") or "").lower()
                if "javascript:prev" in href:
                    continue
                title = (a.get_attribute("title") or "").lower()
                if title and ("précéd" in title or "preced" in title or "previous" in title):
                    continue

                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", a)
                if _click_with_intercept(driver, a):
                    _nav_log("[CTA_NAV]", "clicked navbar Next link", driver)
                    log_info("[CTA_NAV]", "Survey: clicked navbar Next link")
                    return True
            except Exception:
                continue

        # 2) Fallback: cliquer l'image elle-même (moins fiable mais utile si le <a> est masqué)
        imgs = driver.find_elements(By.CSS_SELECTOR, "#NAVBAR img#nextButton, img#nextButton, img.BtnDuBas")
        for img in imgs:
            try:
                if not img.is_displayed():
                    continue
                try:
                    a = img.find_element(By.XPATH, "ancestor::a[1]")
                except Exception:
                    a = None
                el = a or img
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                if _click_with_intercept(driver, el):
                    _nav_log("[CTA_NAV]", "clicked nextButton image", driver)
                    log_info("[CTA_NAV]", "B3netSurvey: clicked nextButton image")
                    return True
            except Exception:
                continue
    except Exception:
        pass

    # --- Forsta/Confirmit: prioriser STRICTEMENT le vrai bouton de navigation ---
    # DOM observé:
    # - button.cf-navigation__button.cf-navigation-next
    # - button.cf-navigation__button.cf-navigation-ok
    # Objectif: éviter les wrappers tabindex/focusables qui captent "Suivant"
    # dans leur texte agrégé mais ne déclenchent pas la navigation réelle.
    try:
        forsta_next = driver.find_elements(
            By.CSS_SELECTOR,
            "button.cf-navigation__button.cf-navigation-next, button.cf-navigation__button.cf-navigation-ok",
        )
        for btn in forsta_next:
            try:
                if not btn.is_displayed() or not btn.is_enabled():
                    continue
                if (btn.get_attribute("aria-disabled") or "").lower() == "true":
                    continue
                _nav_log(
                    "[CTA_NAV]",
"CTA_FOUND provider_hint=forsta button=cf-navigation",
                    driver,
                )
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                clicked = _click_with_intercept(driver, btn)
                _nav_log("[CTA_NAV]", f"CTA_CLICKED provider_hint=forsta PROGRESSED={str(bool(clicked)).lower()}", driver)
                if _cta_intercept_enabled() and clicked:
                    _nav_log("[CTA_NAV]", "INTERCEPT_OK provider_hint=forsta", driver)
                if clicked:
                    return True
            except Exception:
                continue
    except Exception:
        pass

    # --- Consent modal (RGPD): bouton Confirmer explicite ---
    try:
        consent_btns = driver.find_elements(By.CSS_SELECTOR, "#consent-button-confirm")
        for btn in consent_btns:
            try:
                if not btn.is_displayed() or not btn.is_enabled():
                    continue
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                if _click_with_intercept(driver, btn):
                    _nav_log("[CTA_NAV]", "CTA_FOUND provider_hint=consent_modal button=consent-button-confirm", driver)
                    if _cta_intercept_enabled():
                        _nav_log("[CTA_NAV]", "INTERCEPT_OK provider_hint=consent_modal", driver)
                    return True
            except Exception:
                continue
    except Exception:
        pass

    # --- AreYouNet / runet : CTA image sans texte ---
    try:
        btns = driver.find_elements(By.CSS_SELECTOR, "#btn_next")
        if btns:
            el = btns[0]
            try:
                a = el.find_element(By.XPATH, "ancestor::a[1]")
                if a:
                    el = a
            except Exception:
                pass

            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            if _click_with_intercept(driver, el):
                _nav_log("[CTA_NAV]", "clicked #btn_next", driver)
                log_info("[CTA_NAV]", "AreYouNet: clicked #btn_next")
                return True
    except Exception:
        pass

    # Variante AreYouNet: lien direct vers EnqueteDef_submit()
    try:
        links = driver.find_elements(By.CSS_SELECTOR, "a[href*='EnqueteDef_submit']")
        if links:
            el = links[0]
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            if _click_with_intercept(driver, el):
                _nav_log("[CTA_NAV]", "clicked EnqueteDef_submit link", driver)
                log_info("[CTA_NAV]", "AreYouNet: clicked EnqueteDef_submit link")
                return True
    except Exception:
        pass

    # --- Decipher : CTA avec value symbolique (">>" etc.) ---
    try:
        btns = driver.find_elements(By.CSS_SELECTOR, "#btn_continue")
        if btns:
            el = btns[0]
            if el.is_displayed():
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                if _click_with_intercept(driver, el):
                    _nav_log("[CTA_NAV]", "clicked #btn_continue", driver)
                    log_info("[CTA_NAV]", "Decipher: clicked #btn_continue")
                    return True
    except Exception:
        pass

    # --- Decipher gridClick: CTA réel sur div.next-nav.active ---
    # Activation UNIQUEMENT sur signature DOM stricte:
    # 1) présence du widget `div.gridclick-container`
    # 2) `input#btn_continue` présent mais masqué
    try:
        gridclick_widget = driver.find_elements(By.CSS_SELECTOR, "div.gridclick-container")
        btn_continue_nodes = driver.find_elements(By.CSS_SELECTOR, "input#btn_continue")
        if gridclick_widget and btn_continue_nodes and not btn_continue_nodes[0].is_displayed():
            widget_cta = driver.find_elements(
                By.CSS_SELECTOR,
                "div.next-nav.active > div.nav-container[class*='ion-android-arrow-forward']",
            )
            if not widget_cta:
                _nav_log("[CTA_NAV]", "CTA_FOUND gridclick_widget INTERCEPT_IMPOSSIBLE reason=widget_not_ready", driver)
                return False

            el = widget_cta[0]
            if not el.is_displayed() or not el.is_enabled():
                _nav_log("[CTA_NAV]", "CTA_FOUND gridclick_widget INTERCEPT_IMPOSSIBLE reason=widget_not_visible", driver)
                return False

            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            clicked = _click_with_intercept(driver, el)
            if clicked:
                _nav_log("[CTA_NAV]", "CTA_FOUND gridclick_widget INTERCEPT_OK", driver)
                return True

            _nav_log("[CTA_NAV]", "CTA_FOUND gridclick_widget INTERCEPT_IMPOSSIBLE reason=click_failed", driver)
            return False
    except Exception:
        return False

    # --- RSCH / Survey japonais ---
    try:
        btns = driver.find_elements(By.CSS_SELECTOR, "#btnsmall")
        if not btns:
            btns = driver.find_elements(By.CSS_SELECTOR, "input.enterButton.submitButton, button.enterButton.submitButton")
        if btns:
            el = btns[0]
            if el.is_displayed():
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                if _click_with_intercept(driver, el):
                    _nav_log("[CTA_NAV]", "clicked #btnsmall or .enterButton.submitButton", driver)
                    log_info("[CTA_NAV]", "RSCH: clicked #btnsmall or .enterButton.submitButton")
                    return True
    except Exception:
        pass

    candidates = []

    # --- Encuesta: coexistence d'un CTA intra-question et du vrai CTA footer ---
    # Cas DOM observé: bouton "encuesta__done-button" (inline, non-nav)
    # + bouton footer "ee__button--next" (navigation réelle).
    # Garde-fou minimal: ne filtrer "encuesta__done-button" QUE si les deux existent.
    has_encuesta_done_button = False
    has_encuesta_footer_next = False
    try:
        has_encuesta_done_button = bool(driver.find_elements(By.CSS_SELECTOR, "button.encuesta__done-button"))
        has_encuesta_footer_next = bool(driver.find_elements(By.CSS_SELECTOR, "button.ee__button--next"))
    except Exception:
        has_encuesta_done_button = False
        has_encuesta_footer_next = False

    should_filter_encuesta_inline_done = has_encuesta_done_button and has_encuesta_footer_next

    nav_xpath = (
        "//button"
        "|//input[@type='submit' or @type='button' or @type='image']"
        "|//span[contains(concat(' ', normalize-space(@class), ' '), ' fakeNextButton ')]"
        "|//span[@id='NextBtn' and contains(concat(' ', normalize-space(@class), ' '), ' NavBtn ')]"
        "|//a[@role='button']"
        "|//a[contains(concat(' ', normalize-space(@class), ' '), ' btn ')]"
        "|//li[@id='next' or contains(concat(' ', normalize-space(@class), ' '), ' next-button ') or contains(@onclick, 'submitForm')]"
        "|//*[contains(@onmousedown, 'ToggSel')]"
        "|//*[@tabindex and not(self::input or self::textarea or self::select)]"
    )

    for el in driver.find_elements(By.XPATH, nav_xpath):
        try:
            if not el.is_displayed() or not el.is_enabled():
                continue

            tag = ""
            try:
                tag = (el.tag_name or "").lower()
            except Exception:
                tag = ""

            if (el.get_attribute("aria-disabled") or "").lower() == "true":
                continue

            if _is_internal_task_carousel_arrow(driver, el):
                continue

            if _is_inline_hidden_cta(el):
                continue

            cls = (el.get_attribute("class") or "").lower()
            cls_tokens = cls.split()

            if should_filter_encuesta_inline_done and "encuesta__done-button" in cls:
                continue

            disabled_patterns = ("disabled", "btn-disabled", "is-disabled", "button--disabled", "btn--disabled")
            if any(tok in disabled_patterns for tok in cls_tokens):
                continue

            # Les CTA "image-only" (Play/Next) ont souvent txt vide.
            # On élargit la lecture aux attributs title/alt et au premier <img> enfant.
            txt = (
                el.text
                or el.get_attribute("value")
                or el.get_attribute("alt")
                or el.get_attribute("aria-label")
                or el.get_attribute("title")
                or ""
            )
            if not txt:
                try:
                    img = el.find_element(By.CSS_SELECTOR, "img")
                    txt = img.get_attribute("alt") or img.get_attribute("title") or ""
                except Exception:
                    txt = ""
            if not txt:
                txt = _recover_overlay_cta_text(driver, el)
            t = _norm_btn_text(txt)

            el_id = (el.get_attribute("id") or "").lower()
            el_name = (el.get_attribute("name") or "").lower()
            href = (el.get_attribute("href") or "").lower()
            role = (el.get_attribute("role") or "").lower()
            tabindex = (el.get_attribute("tabindex") or "").strip()
            signature = " ".join(
                part for part in [t, el_id, el_name, cls, href, role] if part
            )

            # Garde-fou anti-wrapper: certains conteneurs focusables (tabindex)
            # héritent de tout le texte de la page, y compris "Suivant".
            # Ces wrappers sont souvent non actionnables et provoquent la boucle
            # click -> rescan sans progression. On ne garde que les labels courts
            # pour les éléments non sémantiques.
            if (
                len(t) > 40
                and tag not in {"button", "a", "input"}
                and role != "button"
            ):
                continue

            # Certains CTA sont purement iconiques (ex: a#cm-NextButton avec <img>)
            # et n'ont aucun texte/alt exploitable. On ne les écarte pas d'office.
            # Même logique pour le submit IntelliSurvey structurel (contbtn) à value vide.
            # On ne rejette que les éléments sans texte ET sans indice de navigation,
            # sauf signature DOM IntelliSurvey explicite.
            has_intellisurvey_structural_submit = _is_intellisurvey_structural_submit_cta(el)
            has_mriweb_structural_submit = _is_mriweb_structural_submit_cta(el)
            has_mriweb_vue_next = _is_mriweb_vue_next_cta(el)
            if (
                not t
                and not has_intellisurvey_structural_submit
                and not has_mriweb_structural_submit
                and not has_mriweb_vue_next
                and not any(k in signature for k in ["next", "continue", "submit", "suivant", "valider", "confirm", "confirmer", "confirmez"])
            ):
                continue

            bad = ("refuser", "disagree", "quitter", "quit", "exit", "annuler", "cancel", "fermer", "close", "retour", "précédent", "precedent", "previous", "back")
            if any(b in signature for b in bad):
                continue

            score = 0
            if any(x in t for x in ["continue", "continuer", "next", "suivant", "proceed"]):
                score += 50
            if any(x in t for x in ["valider", "submit", "envoyer", "terminer", "send", "start", "commencer", "démarrer", "confirm", "confirmer", "confirmez"]):
                score += 30

            if el_id == "submitquestion":
                score += 120
            elif any(k in el_id for k in ["submit", "next", "continue", "confirm"]):
                score += 60

            if has_intellisurvey_structural_submit:
                score += 90

            if has_mriweb_structural_submit:
                score += 220

            if has_mriweb_vue_next:
                score += 180

            if any(k in el_name for k in ["submit", "next", "continue", "confirm"]):
                score += 60

            if any(k in cls for k in ["cm-navigation-next-button", "next-button", "nav-next"]):
                score += 70
            if role == "button":
                score += 20
            if tabindex == "0":
                score += 10
            if any(k in href for k in ["next", "continue", "submit", "confirm"]):
                score += 40

            try:
                if el.find_elements(By.XPATH, "ancestor::form[1]"):
                    score += 10
            except Exception:
                pass

            if "primary" in cls:
                score += 10
            if "btn" in cls:
                score += 5

            candidates.append((score, el))
        except Exception:
            continue

    if not candidates:
        _nav_log("[CTA_NAV]", "CTA_NOT_FOUND (no candidates)", driver)
        return False

    candidates.sort(key=lambda x: x[0], reverse=True)

    tried = 0
    for score, el in candidates[:6]:
        if score < MIN_NAV_CTA_SCORE:
            continue
        try:
            _nav_log(
                "[CTA_NAV]",
f"CTA_FOUND candidate score={score}",
                driver,
            )
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            tried += 1
            clicked = _click_with_intercept(driver, el)
            _nav_log("[CTA_NAV]", f"CTA_CLICKED candidate score={score} PROGRESSED={str(bool(clicked)).lower()}", driver)
            if clicked:
                if _cta_intercept_enabled():
                    _nav_log("[CTA_NAV]", f"INTERCEPT_OK candidate score={score}", driver)
                else:
                    _nav_log("[CTA_NAV]", f"CLICKED candidate score={score}", driver)
                return True
        except Exception:
            continue

    if _cta_intercept_enabled():
        _nav_log("[CTA_NAV]", f"CTA_FOUND INTERCEPT_IMPOSSIBLE candidates={len(candidates)} tried={tried}", driver)
    else:
        _nav_log("[CTA_NAV]", f"CTA_FOUND CLICK_IMPOSSIBLE candidates={len(candidates)} tried={tried}", driver)
    return False


# =============================================================================
# WRAPPERS *_ANY_CONTEXT (recherche dans les iframes)
# =============================================================================

def click_button_by_text_any_context(driver, text, depth=2) -> bool:
    """
    Tente de cliquer un bouton par texte dans le DOM courant et,
    en cas d'échec, dans les iframes (jusqu'à 'depth' niveaux).
    """
    def _try_here(drv):
        return click_button_by_text(drv, text)
    return _in_each_frame_recursive(driver, _try_here, depth=depth)


def click_icon_like_button_any_context(driver, hints=None, depth=2) -> bool:
    """
    Même logique mais pour les boutons sans texte (icône/flèche).
    """
    def _try_here(drv):
        return click_icon_like_button(drv, hints=hints)
    return _in_each_frame_recursive(driver, _try_here, depth=depth)


def click_primary_cta_any_context(driver, depth=2) -> bool:
    """
    Clique le CTA principal, en testant aussi à travers les iframes.
    """
    def _try_here(drv):
        return click_primary_cta(drv)
    return _in_each_frame_recursive(driver, _try_here, depth=depth)


def try_click_navigation_cta_any_context(driver, depth=2) -> bool:
    """
    Même CTA nav, mais tente aussi à travers les iframes.
    """
    def _try_here(drv):
        return try_click_navigation_cta(drv)
    return _in_each_frame_recursive(driver, _try_here, depth=depth)


# =============================================================================
# CLICK_CTA_STRONG_ANY_CONTEXT (version robuste multi-frame)
# =============================================================================

def click_cta_strong_any_context(driver, text=None, label_hint=None, depth: int = 2, **_kwargs) -> bool:
    """
    Clique un CTA (Suivant / Continuer / Next / Continue / Start...) en scannant
    default_content + iframes (Decipher/Confirmit).

    Args:
        driver: WebDriver
        text: texte explicite du CTA
        label_hint: alias pour text
        depth: profondeur maximale d'exploration des iframes

    Returns:
        True si CTA cliqué
    """
    # Import dynamique pour éviter dépendances circulaires
    try:
        from frame_utils import iter_frame_chains, switch_to_frame_chain
    except ImportError:
        try:
            from Survey.frame_utils import iter_frame_chains, switch_to_frame_chain
        except ImportError:
            # Fallback sans frame_utils
            return try_click_navigation_cta_any_context(driver, depth=depth)

    raw = text if text is not None else (label_hint or "")
    raw = (raw or "").strip()
    if not raw:
        return False

    def norm(s: str) -> str:
        s = unicodedata.normalize("NFKC", s or "").replace("\u00A0", " ").lower()
        s = re.sub(r"[»«""\"›→·•:]+", "", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    needle = norm(raw)
    if not needle:
        return False

    bad = ["exit", "quit", "refuse", "do not agree", "disagree", "je ne suis pas d'accord", "pas d'accord"]
    good_fallback = ["suivant", "continuer", "next", "continue", "proceed", "start", "begin", "accept", "agree"]

    def is_bad(t: str) -> bool:
        return any(b in t for b in bad)

    def is_match(t: str) -> bool:
        if not t:
            return False
        if is_bad(t):
            return False
        if needle in t or t in needle:
            return True
        if len(needle) <= 5:
            return any(w in t for w in good_fallback)
        return False

    css = "button, input[type='submit'], input[type='button'], a, [role='button']"

    for chain in iter_frame_chains(driver, max_depth=depth):
        with switch_to_frame_chain(driver, chain) as ok:
            if not ok:
                continue

            try:
                els = driver.find_elements(By.CSS_SELECTOR, css)
            except Exception:
                els = []

            for el in els:
                try:
                    if not el.is_displayed():
                        continue

                    raw_val = (el.text or "") or (el.get_attribute("value") or "")
                    t = norm(raw_val)
                    if not t or not any(c.isalpha() for c in t):
                        t = norm(el.get_attribute("aria-label") or "")
                    if not is_match(t):
                        continue

                    try:
                        if el.get_attribute("aria-disabled") == "true":
                            continue
                        if el.get_attribute("disabled") is not None:
                            continue
                    except Exception:
                        pass

                    try:
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                    except Exception:
                        pass

                    if not _click_with_intercept(driver, el):
                        continue

                    try:
                        setattr(driver, "last_action_success", True)
                    except Exception:
                        pass
                    return True

                except Exception:
                    continue

    return False
