# surveybot-tools — image légère pour scripts de diagnostic autonomes
# Contenu : Python + Playwright + Chromium uniquement (pas de Selenium, pas de bot)
# Build : docker build -t registry.fly.io/surveybot-tools:latest .
# Usage  : fly machine run ... -- python tools/ysense_probe.py

FROM python:3.11-slim

# ── Dépendances système pour Chromium headless ──────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libasound2 libpango-1.0-0 libpangocairo-1.0-0 \
    libx11-xcb1 libxcb-dri3-0 \
    fonts-liberation \
    ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# ── Python : Playwright + boto3 + requests (requis par captcha_solver) ───────
RUN pip install --no-cache-dir playwright boto3 requests

# Chemin fixe pour les browsers Playwright — évite le ~/.cache/ms-playwright
# imprévisible selon l'utilisateur courant.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Télécharge uniquement Chromium dans /ms-playwright
RUN playwright install chromium

# ── App ─────────────────────────────────────────────────────────────────────
WORKDIR /app
COPY tools/ tools/

CMD ["python", "tools/ysense_probe.py"]