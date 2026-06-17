"""
header_check.py
---------------
Capture les headers HTTP exacts envoyés par Chrome prod en lançant un
serveur HTTP local minimal, puis en naviguant vers ce serveur via Selenium.

Le serveur affiche TOUS les headers reçus — y compris Accept, Accept-Language,
Accept-Encoding, Cache-Control, Sec-Fetch-*, sec-ch-ua-*, User-Agent, etc.

Usage (depuis la machine Fly.io) :
  ACCOUNT_ID=bot_001 python tools/header_check.py

Ensuite compare la sortie avec les headers d'un Chrome local manuel
en ouvrant http://localhost:18765 dans ton navigateur.
"""

import os
import sys
import json
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler


# ── Serveur HTTP minimal de capture ─────────────────────────────────────────

captured = {}
_ready = threading.Event()


class _HeaderCapture(BaseHTTPRequestHandler):
    def do_GET(self):
        global captured
        captured = dict(self.headers)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - headers captured")
        _ready.set()

    def log_message(self, *args):
        pass  # silence les logs HTTPServer


def _start_server(port: int) -> HTTPServer:
    server = HTTPServer(("0.0.0.0", port), _HeaderCapture)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    sys.path.insert(0, os.getcwd())

    port = 18765

    print(f"[HDR] Démarrage serveur de capture sur port {port} ...")
    server = _start_server(port)

    print("[HDR] Lancement Chrome prod ...")
    from preselection.playwright_launcher import launch_browser
    driver = launch_browser()

    try:
        # Navigue vers le serveur local — Chrome envoie ses headers natifs
        target = f"http://127.0.0.1:{port}/"
        print(f"[HDR] Navigation vers {target} ...")
        driver.get(target)

        # Attendre la capture (max 10s)
        if not _ready.wait(timeout=10):
            print("[HDR][WARN] Timeout — headers non reçus")
        else:
            print("\n" + "=" * 60)
            print("HEADERS ENVOYÉS PAR CHROME PROD VERS LE SERVEUR LOCAL")
            print("=" * 60)
            for k, v in sorted(captured.items()):
                print(f"  {k}: {v}")
            print("=" * 60 + "\n")

            # Sauvegarde JSON pour comparaison
            out_path = "/tmp/prod_headers.json"
            with open(out_path, "w") as f:
                json.dump(captured, f, indent=2)
            print(f"[HDR] Headers sauvegardés : {out_path}")
            print(f"  → flyctl ssh sftp get {out_path} -a <app-name>")

        # Maintenant navigue vers s.cint.com et capture ce qui se passe
        print("\n[HDR] Navigation vers s.cint.com pour observer le comportement ...")
        cint_url = "https://s.cint.com/Survey/Start/48212b3e-91d3-d1c0-066f-1e0a3ad47180?sq=1"
        driver.get(cint_url)
        time.sleep(5)

        title = driver.title
        url   = driver.current_url
        print(f"[HDR] Titre page Cint : {title}")
        print(f"[HDR] URL finale Cint : {url}")

        # Screenshot de la page Cint
        png_path = "/tmp/prod_cint_result.png"
        driver.save_screenshot(png_path)
        print(f"[HDR] Screenshot Cint : {png_path}")
        print(f"  → flyctl ssh sftp get {png_path} -a <app-name>")

    finally:
        server.shutdown()
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
