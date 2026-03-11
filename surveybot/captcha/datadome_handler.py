# datadome_handler.py
"""
Résolution automatique DataDome CAPTCHA via 2Captcha (DataDomeSliderTask).

Flow:
  1. _detect_datadome(driver)    → repère l'iframe geo.captcha-delivery.com
  2. solve_datadome_auto(driver) → orchestrateur complet (1 tentative max)

Règles:
  - Proxy obligatoire (DataDome sans proxy = inutile) — None → return False.
  - 1 tentative max — pas de retry.
  - Aucun clic CTA — navigation déléguée au flux survey après driver.refresh().
"""
import time
from urllib.parse import urlparse, parse_qs

from Survey.log_utils import log_info, log_debug
from captcha.captcha_solver import TwoCaptchaClient
from captcha.recaptcha_handler import _get_proxy_config

_TAG = "DATADOME_HANDLER"


# ──────────────────────────────────────────────────────────────────────────────
# Détection
# ──────────────────────────────────────────────────────────────────────────────

def _detect_datadome(driver) -> dict | None:
    """
    Détecte un DataDome CAPTCHA dans la page courante.

    Critères :
      - Présence d'un <iframe> pointant vers captcha-delivery.com
        OU dont le title contient "DataDome".

    Retourne {"iframe_src": str} ou None si aucun DataDome détecté.
    Le paramètre t (lb = IP bannie) est vérifié dans solve_datadome_auto.
    """
    try:
        iframe_src = driver.execute_script(
            """
            var frames = document.querySelectorAll(
                'iframe[src*="captcha-delivery.com"], iframe[title*="DataDome"]'
            );
            for (var i = 0; i < frames.length; i++) {
                var src = frames[i].getAttribute('src') || '';
                if (src) return src;
            }
            return null;
            """
        )
    except Exception as e:
        log_debug(_TAG, f"_detect_datadome DOM query failed: {e}")
        return None

    if not iframe_src:
        return None

    return {"iframe_src": iframe_src}


# ──────────────────────────────────────────────────────────────────────────────
# Orchestrateur principal
# ──────────────────────────────────────────────────────────────────────────────

def solve_datadome_auto(driver) -> bool:
    """
    Résolution automatique DataDome CAPTCHA via 2Captcha. 1 tentative max.

    Retourne True  : cookie injecté et page rechargée avec succès.
    Retourne False : non détecté / IP bannie (t=lb) / proxy absent / erreur API.

    Navigation : déléguée au flux survey après driver.refresh() — 0 clic CTA ici.
    """
    # 1. Détecter
    info = _detect_datadome(driver)
    if info is None:
        return False

    iframe_src = info["iframe_src"]
    log_info(_TAG, f"DataDome CAPTCHA détecté : {iframe_src[:80]}")

    # 2. Vérifier t=lb (IP bannie — non résolvable)
    try:
        qs = parse_qs(urlparse(iframe_src).query)
        t_val = (qs.get("t") or [""])[0]
        if t_val == "lb":
            log_info(_TAG, "DataDome t=lb → IP bannie → résolution impossible → return False")
            return False
    except Exception:
        pass

    # 3. Proxy obligatoire pour DataDome
    proxy_cfg = _get_proxy_config()
    if not proxy_cfg:
        log_info(_TAG, "Proxy absent → DataDome sans proxy inutile → return False")
        return False

    # 4. Extraire userAgent courant du navigateur
    try:
        user_agent = driver.execute_script("return navigator.userAgent") or ""
    except Exception as e:
        log_info(_TAG, f"Impossible d'extraire userAgent : {e}")
        return False

    website_url = driver.current_url
    log_info(_TAG, f"Envoi à 2Captcha (url={website_url})")

    # 5. Résoudre via 2Captcha (DataDomeSliderTask)
    _t_start = time.time()
    try:
        client = TwoCaptchaClient()
        if not client.api_key:
            log_info(_TAG, "Clé 2Captcha manquante → return False")
            return False

        cookie_raw = client.solve_datadome(
            captcha_url=iframe_src,
            website_url=website_url,
            user_agent=user_agent,
            proxy_type=proxy_cfg["proxy_type"],
            proxy_address=proxy_cfg["proxy_address"],
            proxy_port=proxy_cfg["proxy_port"],
            proxy_login=proxy_cfg.get("proxy_login", ""),
            proxy_password=proxy_cfg.get("proxy_password", ""),
        )
    except TimeoutError as e:
        log_info(_TAG, f"Timeout 2Captcha ({time.time() - _t_start:.1f}s) : {e}")
        return False
    except Exception as e:
        log_info(_TAG, f"Erreur 2Captcha ({time.time() - _t_start:.1f}s) : {e}")
        return False

    if not cookie_raw:
        log_info(_TAG, "Cookie vide reçu de 2Captcha")
        return False

    _dur = time.time() - _t_start
    log_info(_TAG, f"Cookie reçu en {_dur:.1f}s → injection + refresh")

    # 6. Extraire la valeur du cookie depuis la chaîne "datadome=VALUE; Path=/; ..."
    if "datadome=" in cookie_raw:
        cookie_value = cookie_raw.split("datadome=", 1)[1].split(";")[0].strip()
    else:
        cookie_value = cookie_raw.split(";")[0].strip()

    if not cookie_value:
        log_info(_TAG, "Valeur cookie datadome vide après parsing → return False")
        return False

    # 7. Injecter le cookie datadome sur le domaine courant
    try:
        domain = urlparse(website_url).hostname or ""
        driver.add_cookie({
            "name": "datadome",
            "value": cookie_value,
            "domain": domain,
            "path": "/",
        })
        log_info(_TAG, f"Cookie datadome injecté sur domaine={domain}")
    except Exception as e:
        log_info(_TAG, f"Erreur injection cookie : {e}")
        return False

    # 8. Recharger la page pour que le cookie soit pris en compte
    try:
        driver.refresh()
        time.sleep(2.0)
    except Exception as e:
        log_info(_TAG, f"Erreur refresh : {e}")
        return False

    log_info(_TAG, "✅ DataDome résolu → page rechargée → navigation déléguée au flux survey")
    return True
