# surveybot-tools — image légère pour scripts de diagnostic autonomes
# Contenu : Python + Playwright + Chromium uniquement (pas de Selenium, pas de bot)
# Build : docker build -t registry.fly.io/surveybot-tools:latest .
# Usage  : fly machine run ... -- python tools/ysense_probe.py

FROM python:3.11-slim

# ── Dépendances système pour Chromium headless ──────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Chromium et rendu
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libasound2 libpango-1.0-0 libpangocairo-1.0-0 \
    libx11-xcb1 libxcb-dri3-0 \
    # Fonts minimaux (evite les timeouts de rendu)
    fonts-liberation \
    # Utilitaires
    ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# ── Python : uniquement Playwright ──────────────────────────────────────────
RUN pip install --no-cache-dir playwright boto3

# Télécharge uniquement Chromium (pas Firefox ni WebKit)
RUN playwright install chromium

# ── App ─────────────────────────────────────────────────────────────────────
WORKDIR /app
COPY tools/ tools/

# Pas d'ENTRYPOINT fixe — la commande est passée via fly machine run
CMD ["python", "tools/ysense_probe.py"]
