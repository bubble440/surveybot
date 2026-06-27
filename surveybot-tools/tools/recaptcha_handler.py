# recaptcha_handler.py
import os
import time
import json

from recaptcha_utils import extract_recaptcha_v2_sitekey, inject_recaptcha_token
from captcha_solver import TwoCaptchaClient, CapSolverClient
from log_utils import log_debug


def _get_proxy_config() -> dict | None:
    """
    Lit la configuration proxy depuis les variables d'environnement.

    Accepte deux formats (aligné avec playwright_launcher.py) :
      Format combiné  : PROXY_URL="geo.iproyal.com:12321"  (prioritaire)
      Format séparé   : PROXY_HOST="geo.iproyal.com" + PROXY_PORT=12321

    Auth (optionnel) : PROXY_USER / PROXY_PASS
    Type             : PROXY_TYPE = "http" | "socks4" | "socks5"  (défaut: "http")

    Retourne None si aucun proxy configuré → bascule sur Proxyless (CMIX local, etc.)
    """
    # 1) Format combiné PROXY_URL (même var que playwright_launcher)
    proxy_url = os.getenv("PROXY_URL", "").strip()
    if proxy_url:
        # Normalise : ajoute le scheme si absent pour urlparse
        if "://" not in proxy_url:
            proxy_url = "http://" + proxy_url
        from urllib.parse import urlparse
        parsed = urlparse(proxy_url)
        host = parsed.hostname or ""
        port = parsed.port or 8080
        if not host:
            return None
    else:
        # 2) Format séparé PROXY_HOST + PROXY_PORT
        host = os.getenv("PROXY_HOST", "").strip()
        if not host:
            return None
        port = int(os.getenv("PROXY_PORT", "8080"))

    return {
        "proxy_type":     os.getenv("PROXY_TYPE", "http").strip(),
        "proxy_address":  host,
        "proxy_port":     port,
        "proxy_login":    os.getenv("PROXY_USER", "").strip(),
        "proxy_password": os.getenv("PROXY_PASS", "").strip(),
    }



def _fire_recaptcha_callbacks(driver, token: str) -> dict:
    """
    Déclenche les callbacks reCAPTCHA via recherche récursive dans ___grecaptcha_cfg.clients.

    POURQUOI récursif : grecaptcha obfusque ses clés à chaque version (l, o, sJ, RI, ...).
    Le callback n'est pas toujours à 1 niveau de profondeur — il peut être à 2, 3 niveaux.
    La recherche récursive (max 5 niveaux) trouve le callback quelle que soit la version.

    Gère les deux formes : function directe et string (nom de fonction globale).

    Retourne un rapport dict Python (pas console.log, invisible depuis Python).
    """
    js = """
    (tok) => {
        var report = {
            clients_found: 0,
            callbacks_found: 0,
            callbacks_called: 0,
            callback_paths: [],
            errors: []
        };

        // Recherche et appel récursifs de tous les callbacks dans un objet (max depth=5)
        function fireCallbacks(obj, path, depth) {
            if (depth > 5 || !obj || typeof obj !== 'object') return;
            var keys = Object.keys(obj);
            for (var i = 0; i < keys.length; i++) {
                var k = keys[i];
                var val = obj[k];
                if (k === 'callback' && val !== null && val !== undefined) {
                    var cbType = typeof val;
                    if (cbType === 'function') {
                        report.callbacks_found++;
                        report.callback_paths.push(path + '.callback [function]');
                        try {
                            val(tok);
                            report.callbacks_called++;
                        } catch(e) {
                            report.errors.push('fn@' + path + ': ' + String(e));
                        }
                    } else if (cbType === 'string' && val.length > 0) {
                        report.callbacks_found++;
                        report.callback_paths.push(path + '.callback [string=' + val + ']');
                        try {
                            if (typeof window[val] === 'function') {
                                window[val](tok);
                                report.callbacks_called++;
                            } else {
                                report.errors.push('string_cb_not_global: ' + val);
                            }
                        } catch(e) {
                            report.errors.push('str@' + path + '[' + val + ']: ' + String(e));
                        }
                    }
                } else if (val && typeof val === 'object' && !Array.isArray(val)) {
                    fireCallbacks(val, path + '.' + k, depth + 1);
                }
            }
        }

        try {
            if (!window.___grecaptcha_cfg) {
                report.errors.push('___grecaptcha_cfg absent');
                // Fallback : noms de callback courants CMIX/custom en global
                var knownCbs = ['verifyCallback', 'captchaCallback', 'onCaptchaSuccess',
                                'recaptchaCallback', 'captchaVerify'];
                for (var n = 0; n < knownCbs.length; n++) {
                    if (typeof window[knownCbs[n]] === 'function') {
                        report.callbacks_found++;
                        report.callback_paths.push('window.' + knownCbs[n]);
                        try {
                            window[knownCbs[n]](tok);
                            report.callbacks_called++;
                        } catch(e) {
                            report.errors.push('global_cb[' + knownCbs[n] + ']: ' + String(e));
                        }
                    }
                }
            } else {
                var clients = Object.values(window.___grecaptcha_cfg.clients || {});
                report.clients_found = clients.length;
                for (var i = 0; i < clients.length; i++) {
                    if (clients[i]) {
                        fireCallbacks(clients[i], 'clients[' + i + ']', 0);
                    }
                }
            }
            // DOM-based scan: data-callback attribute (e.g. Decipher)
            // Certains widgets reCAPTCHA exposent le callback via data-callback HTML
            // plutôt que via une clé "callback" dans clients — scan complémentaire.
            var _dcbSeen = {};
            var rcDivs = document.querySelectorAll('[data-callback]');
            for (var j = 0; j < rcDivs.length; j++) {
                var cbName = rcDivs[j].getAttribute('data-callback');
                if (cbName && !_dcbSeen[cbName] && typeof window[cbName] === 'function') {
                    _dcbSeen[cbName] = true;
                    report.callbacks_found++;
                    report.callback_paths.push('data-callback[' + cbName + ']');
                    try {
                        window[cbName](tok);
                        report.callbacks_called++;
                    } catch(e) {
                        report.errors.push('data-cb[' + cbName + ']: ' + String(e));
                    }
                }
            }
        } catch(e) {
            report.errors.push('outer: ' + String(e));
        }

        // Fallback events sur la textarea (Vue/Angular)
        try {
            var textarea = document.getElementById('g-recaptcha-response');
            if (textarea) {
                textarea.value = tok;
                textarea.dispatchEvent(new Event('change', {bubbles: true}));
                textarea.dispatchEvent(new Event('input',  {bubbles: true}));
            }
        } catch(e) {
            report.errors.push('textarea_dispatch: ' + String(e));
        }

        return JSON.stringify(report);
    }
    """
    # Diagnostic : snapshot de ___grecaptcha_cfg.clients avant d'appeler les callbacks
    _cfg_js = """
    () => {
        var out = {
            present: !!window.___grecaptcha_cfg,
            clients_count: 0,
            clients_snapshot: [],
            grecaptcha_ready: typeof window.grecaptcha !== 'undefined'
        };
        if (window.___grecaptcha_cfg) {
            var clients = Object.values(window.___grecaptcha_cfg.clients || {});
            out.clients_count = clients.length;
            for (var i = 0; i < clients.length; i++) {
                out.clients_snapshot.push(clients[i] ? Object.keys(clients[i]) : []);
            }
        }
        return JSON.stringify(out);
    }
    """
    try:
        _cfg_raw = driver.evaluate(_cfg_js)
        log_debug("RECAPTCHA_HANDLER][CFG_DUMP", _cfg_raw or "(empty)")
    except Exception as _cfg_e:
        log_debug("RECAPTCHA_HANDLER][CFG_DUMP", f"evaluate failed: {_cfg_e}")

    try:
        raw = driver.evaluate(js, token)
        return json.loads(raw) if raw else {"error": "script returned None"}
    except Exception as e:
        return {"error": f"evaluate failed: {e}"}


def _log_callback_report(report: dict):
    """Affiche le rapport callback JS de façon lisible en Python."""
    if "error" in report:
        print(f"[RECAPTCHA_HANDLER] ❌ evaluate error: {report['error']}")
        return

    clients = report.get("clients_found", "?")
    found   = report.get("callbacks_found", "?")
    called  = report.get("callbacks_called", "?")
    paths   = report.get("callback_paths", [])
    errors  = report.get("errors", [])

    status = "✅" if (isinstance(called, int) and called > 0) else "⚠️"
    print(f"[RECAPTCHA_HANDLER] {status} Callbacks: "
          f"clients={clients} | trouvés={found} | appelés={called}")

    for p in paths:
        print(f"[RECAPTCHA_HANDLER]   📍 {p}")
    for err in errors:
        print(f"[RECAPTCHA_HANDLER]   ❌ JS: {err}")

    if isinstance(found, int) and found == 0 and not errors:
        print("[RECAPTCHA_HANDLER]   ⚠️  Aucun .callback trouvé jusqu'à 5 niveaux "
              "→ nom de propriété différent ou structure inattendue")


def _verify_recaptcha_resolved(driver, callback_report: dict) -> bool:
    """
    Source de vérité : callbacks_called > 0 (callback CMIX exécuté sans erreur).
    Fallback : token présent dans la textarea (vérifié côté serveur par la plateforme).
    """
    if "error" not in callback_report:
        callbacks_called = callback_report.get("callbacks_called", 0)
        if isinstance(callbacks_called, int) and callbacks_called > 0:
            print(f"[RECAPTCHA_HANDLER] ✅ Callback appelé ({callbacks_called}x) → résolution acceptée")
            return True

    # Fallback token
    try:
        token_len = int(driver.evaluate(
            "() => { var el = document.getElementById('g-recaptcha-response');"
            " return el ? (el.value||'').length : 0; }"
        ) or 0)
        if token_len > 20:
            print(f"[RECAPTCHA_HANDLER] ⚠️  Callback non déclenché MAIS token présent "
                  f"({token_len} chars) → résolution partielle, on continue")
            return True
    except Exception:
        pass

    print("[RECAPTCHA_HANDLER] ❌ Ni callback appelé, ni token → échec résolution")
    return False


def solve_recaptcha_v2_auto(driver) -> bool:
    """
    Résolution automatique reCAPTCHA v2 via 2Captcha.
    1 seule tentative — pas de retry.

    Retourne True  : résolu (callback appelé ou token injecté).
    Retourne False : sitekey introuvable / erreur API / token absent.

    Navigation : déléguée au flux survey (cta_handler) — 0 clic CTA ici.
    """
    # 1. Extraire le sitekey
    sitekey, invisible, is_enterprise = extract_recaptcha_v2_sitekey(driver)
    if not sitekey:
        print("[RECAPTCHA_HANDLER] sitekey introuvable")
        return False

    inv_label = "invisible" if invisible else "visible"
    variant = "enterprise" if is_enterprise else "standard"
    print(f"[RECAPTCHA_HANDLER] sitekey extrait : {sitekey} ({inv_label}, {variant})")

    # Chronomètre global — démarre après validation du sitekey
    _t_start = time.time()

    # 2. Résoudre via 2Captcha — avec proxy si disponible, sinon Proxyless
    # Certaines plateformes (Decipher) valident l'IP côté serveur : le token doit
    # être généré depuis la même IP que la soumission du formulaire.
    # Si PROXY_HOST est défini → RecaptchaV2Task (proxy) ; sinon → Proxyless (CMIX, etc.)
    # Si is_enterprise → RecaptchaV2EnterpriseTask / RecaptchaV2EnterpriseTaskProxyless
    current_url = driver.url
    proxy_cfg = _get_proxy_config()
    mode = "proxy" if proxy_cfg else "proxyless"
    provider = os.getenv("CAPTCHA_PROVIDER", "2captcha").strip().lower()
    client = CapSolverClient() if provider == "capsolver" else TwoCaptchaClient()
    print(f"[RECAPTCHA_HANDLER] Envoi à {provider} (mode={mode}, variant={variant}, url={current_url})")
    try:
        _t_api = time.time()
        if is_enterprise:
            # Enterprise : Proxyless forcé — RecaptchaV2EnterpriseTaskProxyless est
            # le type correct pour les sites survey (IPSOS, Qualtrics...) qui n'exigent
            # pas de matching IP. RecaptchaV2EnterpriseTask retourne errorId=12 sur ces sites.
            print("[RECAPTCHA_HANDLER] enterprise détecté → mode proxyless forcé")
            token = client.solve_recaptcha_v2_enterprise(sitekey, current_url, invisible)
        elif proxy_cfg:
            token = client.solve_recaptcha_v2_with_proxy(
                sitekey, current_url,
                proxy_type=proxy_cfg["proxy_type"],
                proxy_address=proxy_cfg["proxy_address"],
                proxy_port=proxy_cfg["proxy_port"],
                proxy_login=proxy_cfg["proxy_login"],
                proxy_password=proxy_cfg["proxy_password"],
                invisible=invisible,
            )
        else:
            token = client.solve_recaptcha_v2(sitekey, current_url, invisible)
    except TimeoutError as e:
        print(f"[RECAPTCHA_HANDLER] Timeout {provider} ({time.time() - _t_api:.1f}s) : {e}")
        return False
    except Exception as e:
        print(f"[RECAPTCHA_HANDLER] Erreur {provider} ({time.time() - _t_api:.1f}s) : {e}")
        return False

    if not token:
        print(f"[RECAPTCHA_HANDLER] Token vide reçu de {provider}")
        return False

    _dur_2captcha = time.time() - _t_api
    print(f"[RECAPTCHA_HANDLER] Token reçu en {_dur_2captcha:.1f}s ({len(token)} chars), injection...")

    # 3. Injecter le token dans #g-recaptcha-response
    try:
        inject_recaptcha_token(driver, token)
        print("[RECAPTCHA_HANDLER] Token injecté dans #g-recaptcha-response ✓")
    except Exception as e:
        print(f"[RECAPTCHA_HANDLER] Erreur injection token : {e}")
        return False

    # 4. Déclencher les callbacks via recherche récursive (max 5 niveaux)
    print("[RECAPTCHA_HANDLER] Déclenchement callbacks JS (recherche récursive)...")
    callback_report = _fire_recaptcha_callbacks(driver, token)
    _log_callback_report(callback_report)

    # Laisser le JS se propager
    time.sleep(1.5)

    # 5. Vérifier la résolution
    resolved = _verify_recaptcha_resolved(driver, callback_report)
    if not resolved:
        return False

    # 6. Navigation déléguée au flux survey — 0 clic CTA ici
    _dur_total = time.time() - _t_start
    print(f"[RECAPTCHA_HANDLER] ✅ Résolution terminée en {_dur_total:.1f}s "
          f"({provider}: {_dur_2captcha:.1f}s) → navigation déléguée au flux survey")
    return True