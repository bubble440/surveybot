# tencent_handler.py
"""
Résolution automatique du Tencent CAPTCHA (slider puzzle) via 2Captcha.

Flow:
  1. _extract_app_id(driver)           → extrait l'appId du widget Tencent
  2. TwoCaptchaClient.solve_tencent*() → soumet à 2Captcha, retourne {ticket, randstr}
  3. _inject_tencent_token(driver, ...) → appelle le callback JS du widget avec le token
  4. solve_tencent_auto(driver)         → orchestrateur complet (1 tentative max)

Règles:
  - 1 tentative max — pas de retry.
  - Aucun clic CTA — navigation déléguée au flux survey.
  - Proxy via _get_proxy_config() (recaptcha_handler.py) — pas de duplication.
"""
import json
import time

from Survey.log_utils import log_info, log_debug
from captcha.captcha_solver import TwoCaptchaClient
from captcha.recaptcha_handler import _get_proxy_config

_TAG = "TENCENT_HANDLER"


# ──────────────────────────────────────────────────────────────────────────────
# Extraction appId
# ──────────────────────────────────────────────────────────────────────────────

def _extract_app_id(driver) -> str | None:
    """
    Extrait l'appId Tencent depuis le DOM de la page courante.

    Stratégies (ordre de priorité) :
      1. Attribut data-appid sur le widget container (#sliderpanel, .tcaptcha-*,
         ou tout ancêtre direct).
      2. Scan des <script> inline : new TencentCaptcha('APPID', ...)
      3. Variable JS globale TencentCaptcha._appId ou __tcaptcha_appid.
    """
    js = r"""
    return (function() {
        // --- Stratégie 1 : attribut data-appid ---
        var candidates = [
            document.querySelector('[data-appid]'),
            document.querySelector('#sliderpanel[data-appid]'),
            document.querySelector('.tcaptcha-wrap[data-appid]'),
            document.querySelector('[data-app-id]'),
        ];
        for (var i = 0; i < candidates.length; i++) {
            var el = candidates[i];
            if (!el) continue;
            var v = el.getAttribute('data-appid') || el.getAttribute('data-app-id');
            if (v && v.trim()) return v.trim();
        }

        // Remonter depuis #sliderpanel jusqu'à 5 niveaux
        var panel = document.querySelector('#sliderpanel');
        if (panel) {
            var cur = panel;
            for (var d = 0; d < 5; d++) {
                if (!cur) break;
                var attr = cur.getAttribute('data-appid') || cur.getAttribute('data-app-id');
                if (attr && attr.trim()) return attr.trim();
                cur = cur.parentElement;
            }
        }

        // --- Stratégie 2 : scan des scripts inline ---
        var scripts = Array.from(document.querySelectorAll('script:not([src])'));
        for (var s = 0; s < scripts.length; s++) {
            var text = scripts[s].textContent || '';
            // new TencentCaptcha('123456789', ...)
            var m = text.match(/new\s+TencentCaptcha\s*\(\s*['"](\d{6,12})['"]/);
            if (m) return m[1];
            // TencentCaptcha('123456789', ...)
            m = text.match(/TencentCaptcha\s*\(\s*['"](\d{6,12})['"]/);
            if (m) return m[1];
            // appId: '123456789'  ou  appId:"123456789"
            m = text.match(/appId\s*[=:]\s*['"](\d{6,12})['"]/);
            if (m) return m[1];
        }

        // --- Stratégie 3 : variable JS globale ---
        if (typeof window.__tcaptcha_appid !== 'undefined') return String(window.__tcaptcha_appid);
        if (window.TencentCaptcha && window.TencentCaptcha._appId) return String(window.TencentCaptcha._appId);

        return null;
    })();
    """
    try:
        result = driver.execute_script(js)
        return result if result else None
    except Exception as e:
        log_debug(_TAG, f"_extract_app_id exception: {e}")
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Injection token + déclenchement callback
# ──────────────────────────────────────────────────────────────────────────────

def _inject_tencent_token(driver, ticket: str, randstr: str) -> dict:
    """
    Injecte le résultat Tencent et déclenche le callback JS du widget.

    Stratégies :
      1. Appel du callback enregistré via window.__tencent_captcha_callback
         (certains sites exposent une var globale).
      2. Dispatch d'un CustomEvent 'tencent-captcha-success' sur le widget.
      3. Scan récursif de window pour trouver des fonctions callback nommées.
      4. Remplissage des champs cachés ticket/randstr (fallback formulaire).

    Retourne un dict rapport (callbacks_called, errors, ...).
    """
    js = r"""
    return (function(ticket, randstr) {
        var result = {ret: 0, ticket: ticket, randstr: randstr};
        var report = {callbacks_called: 0, errors: [], strategies: []};

        // --- Stratégie 1 : callback global __tencent_captcha_callback ---
        try {
            if (typeof window.__tencent_captcha_callback === 'function') {
                window.__tencent_captcha_callback(result);
                report.callbacks_called++;
                report.strategies.push('__tencent_captcha_callback');
            }
        } catch(e) { report.errors.push('s1: ' + String(e)); }

        // --- Stratégie 2 : noms communs de callback Tencent ---
        var knownNames = [
            'tcaptchaCallback', 'tencentCaptchaCallback', 'onTencentCaptchaFinish',
            'captchaCallback', 'onCaptchaSuccess', 'onCaptchaFinish',
            'captchaVerify', 'verifyCallback'
        ];
        for (var i = 0; i < knownNames.length; i++) {
            var name = knownNames[i];
            if (typeof window[name] === 'function') {
                try {
                    window[name](result);
                    report.callbacks_called++;
                    report.strategies.push('window.' + name);
                } catch(e) { report.errors.push(name + ': ' + String(e)); }
            }
        }

        // --- Stratégie 3 : CustomEvent sur le widget ---
        try {
            var panel = document.querySelector('#sliderpanel') || document.body;
            panel.dispatchEvent(new CustomEvent('tencent-captcha-success',
                {detail: result, bubbles: true}));
            report.strategies.push('CustomEvent:tencent-captcha-success');
        } catch(e) { report.errors.push('s3_event: ' + String(e)); }

        // --- Stratégie 4 : champs cachés ticket / randstr ---
        try {
            var ticketSels = ['input[name="ticket"]', 'input[id*="ticket"]',
                              'input[name*="ticket"]'];
            var randstrSels = ['input[name="randstr"]', 'input[id*="randstr"]',
                               'input[name*="randstr"]'];
            for (var ts = 0; ts < ticketSels.length; ts++) {
                var tf = document.querySelector(ticketSels[ts]);
                if (tf) {
                    tf.value = ticket;
                    tf.dispatchEvent(new Event('change', {bubbles: true}));
                    tf.dispatchEvent(new Event('input', {bubbles: true}));
                    report.strategies.push('ticket_field:' + ticketSels[ts]);
                    break;
                }
            }
            for (var rs = 0; rs < randstrSels.length; rs++) {
                var rf = document.querySelector(randstrSels[rs]);
                if (rf) {
                    rf.value = randstr;
                    rf.dispatchEvent(new Event('change', {bubbles: true}));
                    rf.dispatchEvent(new Event('input', {bubbles: true}));
                    report.strategies.push('randstr_field:' + randstrSels[rs]);
                    break;
                }
            }
        } catch(e) { report.errors.push('s4_fields: ' + String(e)); }

        return JSON.stringify(report);
    })(arguments[0], arguments[1]);
    """
    try:
        raw = driver.execute_script(js, ticket, randstr)
        return json.loads(raw) if raw else {"error": "script returned None"}
    except Exception as e:
        return {"error": f"execute_script failed: {e}"}


def _log_injection_report(report: dict) -> None:
    if "error" in report:
        log_info(_TAG, f"injection error: {report['error']}")
        return
    called = report.get("callbacks_called", 0)
    strategies = report.get("strategies", [])
    errors = report.get("errors", [])
    status = "✅" if called > 0 else "⚠️"
    log_info(_TAG, f"{status} injection: callbacks_called={called} strategies={strategies}")
    for err in errors:
        log_debug(_TAG, f"  JS error: {err}")


# ──────────────────────────────────────────────────────────────────────────────
# Résolution NielsenIQ slider (drag ActionChains)
# ──────────────────────────────────────────────────────────────────────────────

def solve_nielseniq_slider_auto(driver) -> bool:
    """
    Résolution du slider puzzle NielsenIQ (web70.gfk.com) via drag ActionChains.
    Widget : .verify-move-block (poignée) → position left du .verify-gap.
    1 tentative max. Aucun clic CTA.
    """
    from selenium.webdriver.common.action_chains import ActionChains

    try:
        has_widget = bool(driver.execute_script(
            "return !!(document.querySelector('.verify-move-block') && "
            "document.querySelector('.verify-gap') && "
            "document.querySelector('.verify-bar-area'));"
        ))
        if not has_widget:
            log_debug(_TAG, "solve_nielseniq_slider_auto: widget .verify-move-block introuvable")
            return False

        gap_left = driver.execute_script(
            "var gap = document.querySelector('.verify-gap');"
            "if (!gap) return null;"
            "var m = (gap.style.left || '').match(/([\\d.]+)px/);"
            "return m ? parseFloat(m[1]) : null;"
        )
        if gap_left is None:
            log_info(_TAG, "solve_nielseniq_slider_auto: impossible d'extraire left du .verify-gap")
            return False

        log_info(_TAG, f"solve_nielseniq_slider_auto: gap_left={gap_left}px — drag en cours")

        handle = driver.find_element("css selector", ".verify-move-block")
        actions = ActionChains(driver)
        actions.click_and_hold(handle)
        steps = 8
        step_x = gap_left / steps
        for _ in range(steps):
            actions.move_by_offset(int(step_x), 0)
            actions.pause(0.05)
        actions.release()
        actions.perform()

        time.sleep(1.0)

        widget_gone = bool(driver.execute_script(
            "var panel = document.querySelector('.verify-img-panel');"
            "if (!panel) return true;"
            "var cs = getComputedStyle(panel);"
            "return cs.display === 'none' || cs.visibility === 'hidden';"
        ))

        if widget_gone:
            log_info(_TAG, "✅ solve_nielseniq_slider_auto: widget disparu → résolu")
            return True

        log_info(_TAG, "❌ solve_nielseniq_slider_auto: widget toujours présent après drag")
        return False

    except Exception as e:
        log_info(_TAG, f"❌ solve_nielseniq_slider_auto: exception — {e}")
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Orchestrateur principal
# ──────────────────────────────────────────────────────────────────────────────

def solve_tencent_auto(driver) -> bool:
    """
    Résolution automatique Tencent CAPTCHA via 2Captcha. 1 tentative max.

    Retourne True  : résolu (callback appelé ou champs remplis).
    Retourne False : appId introuvable / erreur API / injection échouée.

    Navigation : déléguée au flux survey — 0 clic CTA ici.
    """
    # 1. Extraire l'appId
    app_id = _extract_app_id(driver)
    if not app_id:
        # Fallback jQuery slideVerify (ex. NielsenIQ) :
        # même classes CSS que Tencent MAIS widget purement jQuery, sans appId.
        # Condition stricte : #sliderpanel ET #btn_continue présents,
        # ce qui exclut tout vrai widget Tencent.
        try:
            has_slide_verify = bool(driver.execute_script(
                "return !!(document.querySelector('#sliderpanel') && "
                "document.querySelector('#btn_continue'));"
            ))
        except Exception:
            has_slide_verify = False

        if has_slide_verify:
            log_info(_TAG, "appId introuvable — détection jQuery slideVerify (btn_continue présent)")
            try:
                clicked = driver.execute_script(
                    "var btn = document.querySelector('#btn_continue');"
                    "if (!btn) return false;"
                    "btn.click();"
                    "return true;"
                )
            except Exception as _click_err:
                log_info(_TAG, f"❌ jQuery slideVerify bypass — clic JS échoué : {_click_err}")
                return False
            if clicked:
                log_info(_TAG, "✅ jQuery slideVerify bypass → btn_continue cliqué → navigation déléguée au flux survey")
                return True
            log_info(_TAG, "❌ jQuery slideVerify bypass — btn_continue introuvable via JS")
            return False

        log_info(_TAG, "appId introuvable et #btn_continue absent — tentative drag NielsenIQ")
        if solve_nielseniq_slider_auto(driver):
            return True
        from Management.guards.runtime_guard import get_guard
        get_guard().signal_strict_survey("slider_captcha_unresolvable")
        log_info(_TAG, "❌ slider NielsenIQ non résolvable → abandon")
        return False
    log_info(_TAG, f"appId extrait : {app_id}")

    current_url = driver.current_url
    proxy_cfg = _get_proxy_config()
    mode = "proxy" if proxy_cfg else "proxyless"
    log_info(_TAG, f"Envoi à 2Captcha (mode={mode}, url={current_url})")

    # 2. Résoudre via 2Captcha
    _t_start = time.time()
    try:
        client = TwoCaptchaClient()
        if not client.api_key:
            log_info(_TAG, "Clé 2Captcha manquante — abandon")
            return False

        if proxy_cfg:
            solution = client.solve_tencent_with_proxy(
                app_id=app_id,
                url=current_url,
                proxy_type=proxy_cfg["proxy_type"],
                proxy_address=proxy_cfg["proxy_address"],
                proxy_port=proxy_cfg["proxy_port"],
                proxy_login=proxy_cfg["proxy_login"],
                proxy_password=proxy_cfg["proxy_password"],
            )
        else:
            solution = client.solve_tencent(app_id=app_id, url=current_url)

    except TimeoutError as e:
        log_info(_TAG, f"Timeout 2Captcha ({time.time() - _t_start:.1f}s) : {e}")
        return False
    except Exception as e:
        log_info(_TAG, f"Erreur 2Captcha ({time.time() - _t_start:.1f}s) : {e}")
        return False

    ticket = solution.get("ticket", "")
    randstr = solution.get("randstr", "")
    if not ticket:
        log_info(_TAG, "Token vide reçu de 2Captcha")
        return False

    _dur = time.time() - _t_start
    log_info(_TAG, f"Token reçu en {_dur:.1f}s → injection + callback")

    # 3. Injecter le token et déclencher le callback JS
    report = _inject_tencent_token(driver, ticket, randstr)
    _log_injection_report(report)

    # Laisser le JS se propager
    time.sleep(1.5)

    # 4. Vérifier la résolution : widget disparu ou callbacks appelés
    if "error" not in report and report.get("callbacks_called", 0) > 0:
        log_info(_TAG, "✅ Résolution terminée → navigation déléguée au flux survey")
        return True

    # Fallback: vérifier si le widget a disparu du DOM
    try:
        still_there = bool(driver.execute_script(
            "var p = document.querySelector('#sliderpanel');"
            "if (!p) return false;"
            "var cs = getComputedStyle(p);"
            "return cs.display !== 'none' && cs.visibility !== 'hidden';"
        ))
    except Exception:
        still_there = True

    if not still_there:
        log_info(_TAG, "✅ Widget disparu → résolution terminée → navigation déléguée au flux survey")
        return True

    log_info(_TAG, "❌ Callback non déclenché et widget toujours présent → échec")
    return False
