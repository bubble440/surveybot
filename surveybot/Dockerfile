# FROM python:3.11-slim

# ENV DEBIAN_FRONTEND=noninteractive
# ENV DISPLAY=:99
# ENV RUN_ENV=docker

# # ---------------------------------------
# # 1. Install system dependencies
# # ---------------------------------------
# # X virtual framebuffer (écran virtuel)
# RUN apt-get update && apt-get install -y \
#     xvfb \
#     x11-utils \
#     xauth \
#     && rm -rf /var/lib/apt/lists/*

# RUN apt-get update && apt-get install -y --no-install-recommends \
#     wget curl unzip gnupg nano ca-certificates fonts-liberation \
#     libnss3 libatk1.0-0 libcups2 libxkbcommon0 libxcomposite1 \
#     libxdamage1 libxrandr2 libgbm1 libasound2 libxshmfence1 \
#     libxfixes3 libdrm2 libx11-xcb1 libxss1 libxtst6 \
#     libgtk-3-0 libatspi2.0-0 libatk-bridge2.0-0 \
#     && rm -rf /var/lib/apt/lists/*

# # RUN apt-get update && apt-get install -y \
# # chromium \
# # chromium-driver \
# # fonts-liberation \
# # libnss3 \
# # libatk1.0-0 \
# # libatk-bridge2.0-0 \
# # libcups2 \
# # libdrm2 \
# # libxkbcommon0 \
# # libxcomposite1 \
# # libxrandr2 \
# # libxdamage1 \
# # libgbm1 \
# # libasound2 \
# # libpangocairo-1.0-0 \
# # libpango-1.0-0 \
# # libxshmfence1 \
# # libgtk-3-0 \
# # --no-install-recommends \
# #  && rm -rf /var/lib/apt/lists/*

# # ---------------------------------------
# # 2. Add Google Chrome repository & install Chrome
# # ---------------------------------------
# RUN wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-linux-signing-keyring.gpg \
#     && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-linux-signing-keyring.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
#         > /etc/apt/sources.list.d/google-chrome.list \
#     && apt-get update \
#     && apt-get install -y google-chrome-stable

# # ---------------------------------------
# # 3. Install matching Chromedriver (auto-detected by webdriver-manager)
# # ---------------------------------------
# RUN pip install webdriver-manager undetected-chromedriver selenium

# # ---------------------------------------
# # 4. Install Python dependencies of your bot
# # ---------------------------------------
# COPY requirements.txt /app/requirements.txt
# RUN pip install --no-cache-dir -r /app/requirements.txt

# # ---------------------------------------
# # 5. Copy your bot code
# # ---------------------------------------
# COPY . /app
# WORKDIR /app

# ENV CHROME_EXECUTABLE=/usr/bin/google-chrome

# CMD bash -c "Xvfb :99 -screen 0 1920x1080x24 & python3 main.py"



# ------------------------------------------------------------------------nouvelle version du fichier dockerfile ci-dessous------------------------------------------------------------------------------------

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

# =========================
# PYTHON DEPS
# =========================
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Playwright browsers (chromium only; Chrome est externe)
RUN python -m playwright install chromium

# =========================
# APP
# =========================
COPY . /app

# =========================
# RUNTIME DEFAULTS
# =========================
ENV RUN_ENV=aws
ENV SURVEY_HEADLESS=1
ENV DISPLAY=

CMD ["python", "main.py"]
