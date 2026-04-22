# =========================
# BASE
# =========================
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# =========================
# SYSTEM DEPS (Chrome)
# =========================
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    unzip \
    gnupg \
    scrot \
    ca-certificates \
    fonts-liberation \
    libnss3 \
    libatk-bridge2.0-0 \
    libgtk-3-0 \
    libx11-xcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libasound2 \
    libgbm1 \
    libdrm2 \
    libxshmfence1 \
    libxkbcommon0 \
    libpango-1.0-0 \
    libcups2 \
    libatspi2.0-0 \
    libdbus-1-3 \
    socat \
    xvfb \
    --no-install-recommends \
 && rm -rf /var/lib/apt/lists/*

# =========================
# GOOGLE CHROME (STABLE)
# =========================
RUN wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor > /usr/share/keyrings/google-linux.gpg \
 && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-linux.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
    > /etc/apt/sources.list.d/google-chrome.list \
 && apt-get update \
 && apt-get install -y google-chrome-stable \
 && rm -rf /var/lib/apt/lists/*

ENV SURVEY_BROWSER_BIN=/usr/bin/google-chrome

# Après l'installation de Chrome, avant COPY app
RUN useradd -m -u 1000 botuser \
 && mkdir -p /data/chrome_profiles \
 && chown -R botuser:botuser /data /app \
 && mkdir -p /tmp/.X11-unix \
 && chmod 1777 /tmp/.X11-unix

USER botuser

# =========================
# PYTHON DEPS
# =========================
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir psycopg2-binary
 
# Playwright browsers (chromium only; Chrome est externe)
RUN python -m playwright install chromium

# =========================
# APP
# =========================
COPY --chmod=755 entrypoint.sh /app/entrypoint.sh
COPY . /app

# =========================
# RUNTIME DEFAULTS
# =========================
ENV RUN_ENV=prod
# SURVEY_HEADLESS=0 : Xvfb fournit un vrai écran virtuel — headless inutile
ENV SURVEY_HEADLESS=0
# DISPLAY sera positionné par entrypoint.sh au runtime
ENV DISPLAY=:99

CMD ["/app/entrypoint.sh"]