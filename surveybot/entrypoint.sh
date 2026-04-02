#!/bin/bash
# =============================================================
# Entrypoint : démarre Xvfb (écran virtuel 1920×1080 24-bit)
# puis lance le bot Python.
#
# Pourquoi Xvfb ?
#   Chrome en mode --headless est détecté par les anti-bots via :
#     - navigator.plugins vide
#     - WebGL renderer = SwiftShader (--disable-gpu)
#     - window.chrome absent
#     - screen.width / height = 0
#   Xvfb fournit un vrai serveur X11 virtuel : Chrome se comporte
#   exactement comme un navigateur headed sur un bureau physique.
# =============================================================

set -e

DISPLAY_NUM=99
export DISPLAY=":${DISPLAY_NUM}"

echo "[XVFB] Démarrage de Xvfb sur ${DISPLAY} (1920x1080x24)..."
Xvfb ":${DISPLAY_NUM}" -screen 0 1920x1080x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!

# Attendre que Xvfb soit prêt (max 5s)
for i in $(seq 1 10); do
    if xdpyinfo -display ":${DISPLAY_NUM}" >/dev/null 2>&1; then
        echo "[XVFB] Prêt (tentative ${i})"
        break
    fi
    sleep 0.5
done

echo "[XVFB] PID=${XVFB_PID} — lancement du bot Python..."
exec python main.py
