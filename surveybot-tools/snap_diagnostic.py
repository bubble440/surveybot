"""
snap_diagnostic.py
------------------
Diagnostic isolé du pipeline screenshot → R2.
Teste chaque couche indépendamment sans démarrer le bot.

Usage depuis la machine Fly.io (SSH) :
  su - botuser -c 'cd /app && DISPLAY=:99 SNAP_ENABLED=1 \
    SNAP_R2_ACCOUNT_ID=xxx SNAP_R2_ACCESS_KEY_ID=xxx \
    SNAP_R2_SECRET_ACCESS_KEY=xxx SNAP_R2_BUCKET=xxx \
    ACCOUNT_ID=topsurveys_bot_001 python tools/snap_diagnostic.py'

Sans R2 (test local uniquement) :
  su - botuser -c 'cd /app && DISPLAY=:99 ACCOUNT_ID=topsurveys_bot_001 python tools/snap_diagnostic.py'
"""

import io
import os
import subprocess
import sys
import tempfile
import time

PASS = "[OK]"
FAIL = "[FAIL]"
SKIP = "[SKIP]"

results = []


def log(status, step, msg=""):
    line = f"{status} {step}" + (f" — {msg}" if msg else "")
    print(line, flush=True)
    results.append(line)


# ── 1. DISPLAY ──────────────────────────────────────────────────────────────
def check_display():
    display = os.getenv("DISPLAY", "")
    if not display:
        log(FAIL, "DISPLAY", "variable non définie — lancer avec DISPLAY=:99")
    else:
        log(PASS, "DISPLAY", f"DISPLAY={display}")
    return bool(display)


# ── 2. SCROT ────────────────────────────────────────────────────────────────
def check_scrot():
    # Vérifie si scrot est installé
    r = subprocess.run(["which", "scrot"], capture_output=True)
    if r.returncode != 0:
        log(FAIL, "scrot binary", "non trouvé dans PATH — `apt-get install scrot`")
        return False
    log(PASS, "scrot binary", r.stdout.decode().strip())

    display = os.getenv("DISPLAY", ":99")
    path = "/tmp/snap_diag_scrot.png"
    try:
        os.remove(path)
    except FileNotFoundError:
        pass

    try:
        r2 = subprocess.run(
            ["scrot", path],
            env={**os.environ, "DISPLAY": display},
            timeout=8,
            capture_output=True,
        )
        if r2.returncode == 0 and os.path.exists(path):
            size = os.path.getsize(path)
            log(PASS, "scrot capture", f"{size} bytes → {path}")
            return True
        else:
            log(FAIL, "scrot capture", f"returncode={r2.returncode} stderr={r2.stderr!r}")
            return False
    except subprocess.TimeoutExpired:
        log(FAIL, "scrot capture", "timeout 8s — Xvfb probablement absent ou gelé")
        return False
    except Exception as e:
        log(FAIL, "scrot capture", str(e))
        return False


# ── 3. XVFB ─────────────────────────────────────────────────────────────────
def check_xvfb():
    # Vérifie si le process Xvfb tourne sur :99
    r = subprocess.run(["pgrep", "-a", "Xvfb"], capture_output=True, text=True)
    if r.returncode != 0:
        log(FAIL, "Xvfb process", "aucun process Xvfb trouvé — le display est mort")
        return False
    log(PASS, "Xvfb process", r.stdout.strip().replace("\n", " | "))
    return True


# ── 4. PLAYWRIGHT screenshot (page about:blank) ──────────────────────────────
def check_playwright_screenshot():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log(FAIL, "playwright import", "playwright non installé")
        return False

    display = os.getenv("DISPLAY", ":99")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False,
                args=[
                    f"--display={display}",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--use-gl=angle",
                    "--use-angle=swiftshader",
                ],
                env={**os.environ, "DISPLAY": display},
            )
            page = browser.new_page()
            page.goto("about:blank", timeout=10_000)
            page.set_content("<h1 style='color:green'>snap_diagnostic OK</h1>")

            # Screenshot avec timeout court — si ça plante ici, c'est Xvfb/GPU
            png = page.screenshot(timeout=15_000)
            browser.close()

        if png:
            path = "/tmp/snap_diag_playwright.png"
            with open(path, "wb") as f:
                f.write(png)
            log(PASS, "playwright screenshot", f"{len(png)} bytes → {path}")
            return png
        else:
            log(FAIL, "playwright screenshot", "bytes vides")
            return False

    except Exception as e:
        log(FAIL, "playwright screenshot", f"{type(e).__name__}: {e}")
        return False


# ── 5. R2 UPLOAD ─────────────────────────────────────────────────────────────
def check_r2_upload(png_bytes):
    required = ["SNAP_R2_ACCOUNT_ID", "SNAP_R2_ACCESS_KEY_ID",
                 "SNAP_R2_SECRET_ACCESS_KEY", "SNAP_R2_BUCKET"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        log(SKIP, "R2 upload", f"variables manquantes : {missing} — test local uniquement")
        return

    try:
        import boto3
    except ImportError:
        log(FAIL, "R2 upload", "boto3 non installé — `pip install boto3`")
        return

    try:
        account_r2 = os.environ["SNAP_R2_ACCOUNT_ID"]
        endpoint   = f"https://{account_r2}.r2.cloudflarestorage.com"
        bucket     = os.environ["SNAP_R2_BUCKET"]
        account_id = os.getenv("ACCOUNT_ID", "snap_diag")
        key        = f"{account_id}/snap_diagnostic_{int(time.time())}.png"

        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=os.environ["SNAP_R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["SNAP_R2_SECRET_ACCESS_KEY"],
            region_name="auto",
        )
        client.put_object(Bucket=bucket, Key=key, Body=png_bytes, ContentType="image/png")
        log(PASS, "R2 upload", f"r2://{bucket}/{key}")

    except Exception as e:
        log(FAIL, "R2 upload", f"{type(e).__name__}: {e}")


# ── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    sys.path.insert(0, os.getcwd())
    print("\n=== snap_diagnostic.py ===\n", flush=True)

    display_ok = check_display()
    xvfb_ok    = check_xvfb()
    scrot_ok   = check_scrot() if display_ok else False
    png        = check_playwright_screenshot() if display_ok else False

    if png:
        check_r2_upload(png)
    elif scrot_ok:
        # Fallback : utilise le PNG scrot pour tester R2 quand même
        with open("/tmp/snap_diag_scrot.png", "rb") as f:
            check_r2_upload(f.read())
    else:
        log(SKIP, "R2 upload", "aucun PNG disponible pour tester l'upload")

    print("\n=== RÉSUMÉ ===")
    for line in results:
        print(line)


if __name__ == "__main__":
    main()