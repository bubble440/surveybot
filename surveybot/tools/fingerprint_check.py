"""
fingerprint_check.py
--------------------
Lance Chrome via le launcher prod (playwright_launcher), navigue sur les pages
browserleaks, capture CHAQUE PAGE EN ENTIER (scroll + assemblage PNG) et uploade
vers R2 ou sauvegarde en /tmp si R2 indisponible.

Usage (depuis la machine Fly.io) :
  SNAP_ENABLED=1 ACCOUNT_ID=topsurveys_bot_001 python tools/fingerprint_check.py

Variables R2 optionnelles (si SNAP_ENABLED=1) :
  SNAP_R2_ACCOUNT_ID, SNAP_R2_ACCESS_KEY_ID, SNAP_R2_SECRET_ACCESS_KEY, SNAP_R2_BUCKET
"""

import io
import os
import sys
import time

FINGERPRINT_PAGES = [
    # ("tcp",           "https://browserleaks.com/tcp"),
    # ("http2",         "https://browserleaks.com/http2"),
    # ("webrtc",        "https://browserleaks.com/webrtc"),
    # ("dns",           "https://browserleaks.com/dns"),
    # ("tls",           "https://browserleaks.com/tls"),
    # ("fonts",         "https://browserleaks.com/fonts"),
    # ("features",      "https://browserleaks.com/features"),
    # ("geo",           "https://browserleaks.com/geo"),
    # ("canvas",        "https://browserleaks.com/canvas"),
    ("webgl",         "https://browserleaks.com/webgl"),
    ("javascript",    "https://browserleaks.com/javascript"),
    ("ip",            "https://browserleaks.com/ip"),
]

WAIT_AFTER_LOAD = 8   # WebRTC nécessite du temps pour le handshake ICE
SCROLL_PAUSE    = 0.4


def _capture_full_page(driver) -> bytes:
    """
    Capture la page entière par scroll progressif + assemblage vertical des viewports.
    Retourne un PNG bytes. Fonctionne sans GPU, sans scrot.
    """
    from PIL import Image

    viewport_h = driver.execute_script("return window.innerHeight")
    total_h    = driver.execute_script("return document.body.scrollHeight")
    viewport_w = driver.execute_script("return window.innerWidth")

    driver.execute_script("window.scrollTo(0, 0)")
    time.sleep(0.3)

    # Collecte des strips avec leur offset scroll réel
    strips = []
    y = 0
    while y < total_h:
        driver.execute_script(f"window.scrollTo(0, {y})")
        time.sleep(SCROLL_PAUSE)
        png = driver.get_screenshot_as_png()
        img = Image.open(io.BytesIO(png))
        strips.append((y, img))
        y += viewport_h

    # Strip final (bas de page exact)
    last_y = max(0, total_h - viewport_h)
    driver.execute_script(f"window.scrollTo(0, {last_y})")
    time.sleep(SCROLL_PAUSE)
    png = driver.get_screenshot_as_png()
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


def _extract_page_data(driver, label: str) -> dict:
    """
    Extrait les données textuelles clés selon la page visitée.
    Complète le screenshot par des valeurs directement comparables.
    """
    data = {}
    try:
        if label == "webrtc":
            # ⚠️ Vérifier si l'IP Fly.io fuite à travers le proxy
            data = driver.execute_script("""
                const rows = Array.from(document.querySelectorAll('table tr'));
                const out = {};
                for (const row of rows) {
                    const cells = row.querySelectorAll('td');
                    if (cells.length >= 2) {
                        const k = cells[0].innerText.trim();
                        const v = cells[1].innerText.trim();
                        if (k) out[k] = v;
                    }
                }
                return out;
            """)

        elif label == "features":
            data["features_hash"] = driver.execute_script("""
                // Hash MD5 affiché en haut de la page Features Detection
                const el = document.querySelector('td, .hash, [class*=hash]');
                // Chercher le pattern MD5 dans tout le texte de la page
                const text = document.body.innerText || '';
                const m = text.match(/[0-9A-F]{32}/i);
                return m ? m[0] : null;
            """)

        elif label == "canvas":
            data = driver.execute_script("""
                const rows = Array.from(document.querySelectorAll('table tr'));
                const out = {};
                for (const row of rows) {
                    const cells = row.querySelectorAll('td');
                    if (cells.length >= 2) {
                        out[cells[0].innerText.trim()] = cells[1].innerText.trim();
                    }
                }
                return out;
            """)

    except Exception as e:
        data["_error"] = str(e)

    return data


def _upload_or_save(png_bytes: bytes, label: str) -> None:
    """
    Sauvegarde en /tmp (toujours) + upload R2 si SNAP_ENABLED=1 et boto3 disponible.
    """
    path = f"/tmp/prod_{label}.png"
    with open(path, "wb") as f:
        f.write(png_bytes)
    print(f"[FP] Sauvegardé : {path} ({len(png_bytes)//1024} KB)")

    if os.getenv("SNAP_ENABLED", "").strip() != "1":
        return

    try:
        import boto3
        account_r2 = os.environ["SNAP_R2_ACCOUNT_ID"]
        endpoint   = f"https://{account_r2}.r2.cloudflarestorage.com"
        bucket     = os.environ["SNAP_R2_BUCKET"]
        account_id = os.getenv("ACCOUNT_ID", "fp_probe")
        session_id = time.strftime("%Y%m%d_%H%M%S")
        key        = f"{account_id}/fp_{session_id}/{label}.png"

        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=os.environ["SNAP_R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["SNAP_R2_SECRET_ACCESS_KEY"],
            region_name="auto",
        )
        client.put_object(Bucket=bucket, Key=key, Body=png_bytes, ContentType="image/png")
        print(f"[FP] Uploadé R2 → r2://{bucket}/{key}")

    except ImportError:
        print("[FP] boto3 absent — PNG local disponible dans /tmp")
    except Exception as e:
        print(f"[FP][WARN] Upload R2 échoué ({e}) — PNG local disponible")


def main():
    sys.path.insert(0, os.getcwd())

    # Utilise le launcher prod : mêmes flags Chrome, mêmes overrides CDP fingerprint
    from preselection.playwright_launcher import launch_browser
    account_id = os.getenv("ACCOUNT_ID", "fingerprint_probe")
    print(f"[FP] Lancement Chrome prod pour account_id={account_id} ...")
    driver = launch_browser()

    try:
        try:
            from PIL import Image  # noqa
        except ImportError:
            import subprocess
            print("[FP] Installation Pillow ...")
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "Pillow", "--quiet"],
                check=True
            )

        for label, url in FINGERPRINT_PAGES:
            print(f"[FP] Navigation vers {url} ...")
            driver.get(url)
            print(f"[FP] Attente {WAIT_AFTER_LOAD}s ...")
            time.sleep(WAIT_AFTER_LOAD)

            # Extraction données textuelles
            page_data = _extract_page_data(driver, label)
            if page_data:
                print(f"[FP][{label.upper()}] Données extraites :")
                for k, v in page_data.items():
                    print(f"      {k}: {v}")

            # Screenshot pleine page
            print(f"[FP] Capture pleine page : {label} ...")
            try:
                png = _capture_full_page(driver)
                _upload_or_save(png, label)
            except Exception as e:
                print(f"[FP][ERROR] Capture {label} échouée : {e}")
                try:
                    png = driver.get_screenshot_as_png()
                    _upload_or_save(png, f"{label}_viewport_only")
                except Exception as e2:
                    print(f"[FP][ERROR] Fallback aussi échoué : {e2}")

    finally:
        try:
            driver.quit()
        except Exception:
            pass

    print("\n[FP] Done. Récupère les PNG via :")
    for label, _ in FINGERPRINT_PAGES:
        print(f"  flyctl ssh sftp get /tmp/fp_{label}.png -a <app-name>")


if __name__ == "__main__":
    main()