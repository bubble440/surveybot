"""
playwright_access_check.py
---------------------------
Fork de multi_access_check.py utilisant Playwright natif.

Différence clé avec multi_access_check.py :
  - Chrome est lancé via Playwright (mode pipe, pas de --remote-debugging-port)
  - Pas de Selenium, pas de CDP execute_cdp_cmd
  - Le fingerprint JS est injecté via page.add_init_script() (même résultat, sans port TCP)
  - Le proxy est passé directement à Playwright (pas de relay pproxy local)

Objectif : vérifier si la suppression du debug port élimine le blocage DataDome sur Cint.

Usage (depuis la machine Fly.io) :
  DISPLAY=:99 ACCOUNT_ID=topsurveys_bot_001 python tools/playwright_access_check.py

Variables requises (identiques à multi_access_check.py) :
  PROXY_USER, PROXY_PASS, PROXY_URL (ou variables d'env déjà présentes)
  SNAP_ENABLED, SNAP_R2_* (optionnel)
"""

import io
import os
import sys
import time

# ---------------------------------------------------------------------------
# Même TARGETS / constantes que multi_access_check.py
# ---------------------------------------------------------------------------
TARGETS = [
    {
        "url":   "https://s.cint.com/Survey/Start/48212b3e-91d3-d1c0-066f-1e0a3ad47180?sq=1",
        "label": "s.cint_playwright",
    },
]

WAIT_AFTER_LOAD  = 10   # secondes après chargement initial
BLOCKED_KEYWORDS = [
    "access denied", "access is temporarily restricted",
    "forbidden", "blocked", "not allowed",
    "error 403", "error 404",
    "automated", "bot activity",
    "disqualified", "quota full",
]

# User-Agent cible : Chrome 149 Windows (identique au spoofing Selenium)
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0.7827.103 Safari/537.36"
)

# Client Hints low-entropy (sec-ch-ua*) — injectés comme headers HTTP sortants
_CLIENT_HINTS_HEADERS = {
    "sec-ch-ua":          '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
    "sec-ch-ua-mobile":   "?0",
    "sec-ch-ua-platform": '"Windows"',
}


# ---------------------------------------------------------------------------
# Helpers (identiques à multi_access_check.py, adaptés pour l'API Playwright)
# ---------------------------------------------------------------------------

def _upload_or_save(png_bytes: bytes, label: str) -> str:
    """Sauvegarde locale + upload R2 optionnel. Identique à multi_access_check."""
    path = f"/tmp/{label}.png"
    with open(path, "wb") as f:
        f.write(png_bytes)
    print(f"  [SAVE] {path} ({len(png_bytes) // 1024} KB)")

    if os.getenv("SNAP_ENABLED", "").strip() != "1":
        print("  [SAVE] SNAP_ENABLED != 1 — upload R2 ignoré")
        return path

    try:
        import boto3
        account_r2 = os.environ["SNAP_R2_ACCOUNT_ID"]
        endpoint   = f"https://{account_r2}.r2.cloudflarestorage.com"
        bucket     = os.environ["SNAP_R2_BUCKET"]
        account_id = os.getenv("ACCOUNT_ID", "access_probe")
        session_id = time.strftime("%Y%m%d_%H%M%S")
        key        = f"{account_id}/playwright_access_{session_id}/{label}.png"
        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=os.environ["SNAP_R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["SNAP_R2_SECRET_ACCESS_KEY"],
            region_name="auto",
        )
        client.put_object(Bucket=bucket, Key=key, Body=png_bytes, ContentType="image/png")
        print(f"  [R2]   r2://{bucket}/{key}")
    except ImportError:
        print("  [SAVE] boto3 absent — PNG local uniquement")
    except Exception as e:
        print(f"  [WARN] Upload R2 échoué ({e}) — PNG local disponible")

    return path


def _collect_page_info(page) -> dict:
    """Collecte URL finale, titre, extrait body — via l'API Playwright."""
    info = {}
    try:
        info["final_url"] = page.url
        info["title"]     = page.title()
        info["body_snippet"] = page.evaluate(
            "(document.body && document.body.innerText || '').slice(0, 500)"
        )
        info["status_code"] = page.evaluate("""
            (() => {
                try {
                    const e = performance.getEntriesByType('navigation');
                    if (e && e.length) return e[0].responseStatus || 'n/a';
                } catch(e) {}
                return 'unavailable';
            })()
        """)
    except Exception as e:
        info["error"] = str(e)
    return info


def _check_one(page, url: str, label: str) -> dict:
    """Navigue, collecte infos, capture screenshot pleine page."""
    result = {"label": label, "url": url, "status": "unknown", "path": None}

    print(f"\n{'─' * 60}")
    print(f"  [TARGET] {label}")
    print(f"  [URL]    {url}")

    try:
        # wait_until="domcontentloaded" : ne pas bloquer sur les ressources lentes
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        print(f"  [WAIT]   {WAIT_AFTER_LOAD}s (redirects JS asynchrones) ...")
        time.sleep(WAIT_AFTER_LOAD)

        info = _collect_page_info(page)
        print(f"  [INFO]   URL finale    : {info.get('final_url', 'n/a')}")
        print(f"  [INFO]   Titre         : {info.get('title', 'n/a')}")
        print(f"  [INFO]   Status (perf) : {info.get('status_code', 'n/a')}")
        body = info.get("body_snippet", "")
        if body:
            print(f"  [INFO]   Body (500c)   :\n{body}")

        body_lower  = (body or "").lower()
        detected    = [kw for kw in BLOCKED_KEYWORDS if kw in body_lower]
        if detected:
            print(f"  [BLOCK]  Mots-clés détectés : {detected}")
            result["status"]           = "BLOCKED"
            result["blocked_keywords"] = detected
        else:
            print(f"  [OK]     Aucun mot-clé de blocage détecté.")
            result["status"] = "OK"

        result["final_url"] = info.get("final_url", "")
        result["title"]     = info.get("title", "")

        # Capture pleine page — built-in Playwright, pas besoin d'assemblage manuel
        print(f"  [SNAP]   Capture pleine page ...")
        png = page.screenshot(full_page=True)
        result["path"] = _upload_or_save(png, label)

    except Exception as e:
        print(f"  [ERROR]  Exception : {e}")
        result["status"] = "ERROR"
        result["error"]  = str(e)

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    sys.path.insert(0, os.getcwd())

    # Importer uniquement les utilitaires sans dépendance Selenium
    from preselection.playwright_launcher import (
        _fingerprint_js,
        _parse_proxy_env,
        _parse_locale_tz_env,
        _detect_chrome_binary,
    )
    from playwright.sync_api import sync_playwright

    account_id = os.getenv("ACCOUNT_ID", "access_probe")
    print(f"[PAC] Lancement Chrome Playwright (sans --remote-debugging-port)")
    print(f"[PAC] account_id={account_id}")
    print(f"[PAC] {len(TARGETS)} URL(s) à tester")

    proxy_server, proxy_user, proxy_pass = _parse_proxy_env()
    locale, tz = _parse_locale_tz_env()
    chrome_bin = _detect_chrome_binary()

    print(f"[PAC] chrome_bin={chrome_bin}")
    print(f"[PAC] proxy={'oui' if proxy_server else 'non'}")
    print(f"[PAC] locale={locale}  tz={tz}")

    # ── Arguments Chrome — identiques au launcher prod, SANS --remote-debugging-port ──
    is_root = not hasattr(os, "getuid") or os.getuid() == 0
    chrome_args = [
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-dev-shm-usage",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-sync",
        "--no-pings",
        "--disable-domain-reliability",
        "--disable-client-side-phishing-detection",
        "--safebrowsing-disable-auto-update",
        "--disable-features=Translate,OptimizationHints,SafeBrowsingProtections,"
            "SafeBrowsingRealTimeUrlLookupEnabled,ChromeWhatsNewUI,"
            "MediaRouter,DialMediaRouteProvider,"
            "WebRTC",                            # WebRTC désactivé (prod)
        "--disable-extensions",
        "--disable-notifications",
        "--enforce-webrtc-ip-permission-check",
        "--webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--window-size=1920,1080",
        "--lang=en-US",
        "--use-gl=angle",
        "--use-angle=swiftshader",              # Xvfb prod — SwiftShader WebGL
        *( ["--no-sandbox"] if is_root else [] ),
    ]

    # ── Config proxy — Playwright gère l'auth directement, pas de relay local ──
    proxy_config = None
    if proxy_server and proxy_user and proxy_pass:
        proxy_config = {
            "server":   proxy_server,
            "username": proxy_user,
            "password": proxy_pass,
        }
        print(f"[PAC] proxy_config={proxy_server} user={proxy_user[:4]}***")
    elif proxy_server:
        proxy_config = {"server": proxy_server}

    with sync_playwright() as p:
        # Lance Chrome en mode pipe (--remote-debugging-pipe interne à Playwright)
        # Pas de port TCP exposé, pas de /json endpoint, pas de flag debug port.
        browser = p.chromium.launch(
            executable_path=chrome_bin,
            headless=False,         # Xvfb fournit DISPLAY=:99
            args=chrome_args,
            env={**os.environ, "TZ": tz},
        )

        # Contexte : UA, locale, timezone, proxy — tout en un
        context = browser.new_context(
            user_agent=_UA,
            locale=locale,
            timezone_id=tz,
            viewport={"width": 1920, "height": 1080},
            proxy=proxy_config,
            extra_http_headers=_CLIENT_HINTS_HEADERS,
        )

        # Injection du fingerprint JS AVANT toute navigation
        # Équivalent de Page.addScriptToEvaluateOnNewDocument, sans CDP
        context.add_init_script(_fingerprint_js())
        print("[PAC] Fingerprint JS injecté via add_init_script ✓")

        page = context.new_page()
        results = []

        try:
            for target in TARGETS:
                r = _check_one(page, target["url"], target["label"])
                results.append(r)
        finally:
            browser.close()

    # ── Récapitulatif ─────────────────────────────────────────────────────────
    print(f"\n{'═' * 60}")
    print(f"  RÉCAPITULATIF ({len(results)} URLs testées)")
    print(f"{'═' * 60}")
    for r in results:
        status  = r.get("status", "?")
        label   = r.get("label", "?")
        path    = r.get("path") or "(pas de fichier)"
        blocked = r.get("blocked_keywords", [])
        marker  = "✅" if status == "OK" else ("🚫" if status == "BLOCKED" else "❌")
        line = f"  {marker}  [{status:8s}]  {label}"
        if blocked:
            line += f"  ← {blocked}"
        print(line)
        print(f"           PNG : flyctl ssh sftp get {path} -a surveybot-bot")
    print(f"{'═' * 60}\n")


if __name__ == "__main__":
    main()
