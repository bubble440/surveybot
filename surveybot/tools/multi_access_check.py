"""
multi_access_check.py
----------------------
Vérifie l'accessibilité de plusieurs URLs depuis la machine Fly.io :
  - Lance Chrome une seule fois via le launcher prod (playwright_launcher)
  - Navigue sur chaque URL cible séquentiellement
  - Capture la page entière (scroll + assemblage PNG) pour chaque URL
  - Upload vers R2 ou sauvegarde en /tmp si R2 indisponible

Usage (depuis la machine Fly.io) :
  DISPLAY=:99 ACCOUNT_ID=topsurveys_bot_001 python tools/multi_access_check.py

Variables R2 optionnelles (si SNAP_ENABLED=1) :
  SNAP_R2_ACCOUNT_ID, SNAP_R2_ACCESS_KEY_ID, SNAP_R2_SECRET_ACCESS_KEY, SNAP_R2_BUCKET
"""

import io
import os
import re
import sys
import time

# ---------------------------------------------------------------------------
# URLs à tester — ajouter / retirer des entrées ici
# label : nom du fichier PNG produit (sans extension, sans espaces)
# ---------------------------------------------------------------------------
TARGETS = [
    {
        "url":   "https://s.cint.com/Survey/Start/48212b3e-91d3-d1c0-066f-1e0a3ad47180?sq=1",
        "label": "s.cint",
    },
]

# Temps d'attente après le chargement initial de chaque URL (secondes)
# Généreux pour absorber les redirects JS asynchrones
WAIT_AFTER_LOAD = 10
SCROLL_PAUSE    = 0.4

# Mots-clés indiquant un blocage dans le body de la page
BLOCKED_KEYWORDS = [
    "access denied", "access is temporarily restricted",
    "forbidden", "blocked", "not allowed",
    "error 403", "error 404",
    "automated", "bot activity",
    "disqualified", "quota full",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _capture_full_page(driver) -> bytes:
    """
    Capture la page entière par scroll progressif + assemblage vertical des viewports.
    Retourne un PNG bytes. Fonctionne sans GPU, sans scrot.
    """
    from PIL import Image

    viewport_h = driver.evaluate("() => window.innerHeight")
    total_h    = driver.evaluate("() => document.body.scrollHeight")
    viewport_w = driver.evaluate("() => window.innerWidth")

    driver.evaluate("() => window.scrollTo(0, 0)")
    time.sleep(0.3)

    strips = []
    y = 0
    while y < total_h:
        driver.evaluate(f"() => window.scrollTo(0, {y})")
        time.sleep(SCROLL_PAUSE)
        png = driver.screenshot()
        img = Image.open(io.BytesIO(png))
        strips.append((y, img))
        y += viewport_h

    # Strip final pour couvrir exactement le bas de page
    last_y = max(0, total_h - viewport_h)
    driver.evaluate(f"() => window.scrollTo(0, {last_y})")
    time.sleep(SCROLL_PAUSE)
    png = driver.screenshot()
    img = Image.open(io.BytesIO(png))
    strips.append((last_y, img))

    # Facteur DPI (Retina = 2, normal = 1)
    scale    = img.width / viewport_w
    canvas_h = int(total_h * scale)
    canvas_w = img.width
    canvas   = Image.new("RGB", (canvas_w, canvas_h), (255, 255, 255))

    seen = set()
    for scroll_y, strip_img in strips:
        pixel_y = int(scroll_y * scale)
        if pixel_y in seen:
            continue
        seen.add(pixel_y)
        canvas.paste(strip_img, (0, pixel_y))

    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _collect_page_info(driver) -> dict:
    """
    Collecte des méta-informations sur la page chargée :
    URL finale, titre, extrait body, code HTTP via Performance API.
    """
    info = {}
    try:
        info["final_url"]    = driver.url
        info["title"]        = driver.title()
        info["body_snippet"] = driver.evaluate(
            "() => (document.body && document.body.innerText || '').slice(0, 500)"
        )
        info["status_code"]  = driver.evaluate(
            """() => {
                try {
                    var entries = performance.getEntriesByType('navigation');
                    if (entries && entries.length) return entries[0].responseStatus || 'n/a';
                } catch(e) {}
                return 'unavailable';
            }"""
        )
    except Exception as e:
        info["error"] = str(e)
    return info


def _upload_or_save(png_bytes: bytes, label: str) -> str:
    """
    Sauvegarde en /tmp (toujours).
    Upload vers R2 si SNAP_ENABLED=1 et boto3 disponible.
    Retourne le chemin local du fichier.
    """
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
        key        = f"{account_id}/multi_access_{session_id}/{label}.png"

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


def _check_one(driver, url: str, label: str) -> dict:
    """
    Navigue vers une URL, collecte les infos, capture le screenshot.
    Retourne un dict résumant le résultat (ok, blocked, error).
    """
    result = {"label": label, "url": url, "status": "unknown", "path": None}

    print(f"\n{'─' * 60}")
    print(f"  [TARGET] {label}")
    print(f"  [URL]    {url}")

    try:
        driver.goto(url)
        print(f"  [WAIT]   {WAIT_AFTER_LOAD}s ...")
        time.sleep(WAIT_AFTER_LOAD)

        info = _collect_page_info(driver)
        print(f"  [INFO]   URL finale    : {info.get('final_url', 'n/a')}")
        print(f"  [INFO]   Titre         : {info.get('title', 'n/a')}")
        print(f"  [INFO]   Status (perf) : {info.get('status_code', 'n/a')}")
        body = info.get("body_snippet", "")
        if body:
            print(f"  [INFO]   Body (500c)   :\n{body}")

        # Détection blocage
        body_lower = (body or "").lower()
        detected = [kw for kw in BLOCKED_KEYWORDS if kw in body_lower]
        if detected:
            print(f"  [BLOCK]  Mots-clés détectés : {detected}")
            result["status"] = "BLOCKED"
            result["blocked_keywords"] = detected
        else:
            print(f"  [OK]     Aucun mot-clé de blocage détecté.")
            result["status"] = "OK"

        result["final_url"] = info.get("final_url", "")
        result["title"]     = info.get("title", "")

        # Capture screenshot
        print(f"  [SNAP]   Capture pleine page ...")
        try:
            png = _capture_full_page(driver)
        except Exception as e:
            print(f"  [WARN]   Capture pleine page échouée ({e}), fallback viewport ...")
            png = driver.screenshot()
            label = label + "_viewport_only"

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

    from preselection.playwright_launcher import launch_browser_playwright

    account_id = os.getenv("ACCOUNT_ID", "access_probe")
    print(f"[MA] Lancement Chrome prod — account_id={account_id}")
    print(f"[MA] {len(TARGETS)} URL(s) à tester")

    # Pillow requis pour l'assemblage pleine page
    try:
        from PIL import Image  # noqa
    except ImportError:
        import subprocess
        print("[MA] Installation de Pillow ...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "Pillow", "--quiet"],
            check=True,
        )

    driver = launch_browser_playwright()
    results = []

    try:
        for target in TARGETS:
            r = _check_one(driver, target["url"], target["label"])
            results.append(r)
    finally:
        try:
            driver.context.browser.close()
        except Exception:
            pass

    # Récapitulatif
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