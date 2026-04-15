# ------------------------------------------------------------------------------------------------------------------------------------------
# 
# ------------------------------------------------------------------------------------------------------------------------------------------

![Cartographie des mode de lancement](image-1.png)

# ------------------------------------------------------------------------------------------------------------------------------------------
# Deploiement en mode prod-like (terminal WSL)
# ------------------------------------------------------------------------------------------------------------------------------------------
# Activate the terminal
source /mnt/c/projects/Surveys/.venv-wsl/bin/activate

# Et le tunnel Fly doit être actif dans un terminal séparé :
flyctl proxy 5432 -a surveybot-db

# Variables requises pour que les cookies soient restaurés

export ACCOUNT_ID="topsurveys_bot_001"
export DISPLAY=:0
export SNAP_ENABLED="0"
export RUN_ENV="local"
export RUN_MODE="local"
export LOG_LEVEL="DEBUG"
export LOCAL_CTA_DEBUG="1"
export SURVEY_HEADLESS="0"
export LOCAL_UNATTENDED="1" # le bot prend la main sur une page déjà ouverte manuellement.
export PROXY_PASS="bb82a9e63b"
export STATE_BACKEND="postgres"
export LIBGL_ALWAYS_SOFTWARE="1"
export PROXY_USER="14abf236340a1"
export LOCAL_CTA_REQUIRE_ENTER="1" # Pause before cta click
export PROXY_URL="185.134.194.152:12323"
export EMAIL="krakouz.cement183@8alias.com"
export SURVEY_BROWSER_BIN="/usr/bin/chromium-browser"
export TWO_CAPTCHA_KEY="ff2f59cd67845abf5c1b7db1c0a17cf2"
export DATABASE_URL="postgres://postgres:3o1L6kfCFxuncbY@localhost:5432/postgres"
python3 surveybot/main.py



# ------------------------------------------------------------------------------------------------------------------------------------------
# Deploiement en mode prod-like (terminal WSL)
# ------------------------------------------------------------------------------------------------------------------------------------------


# ------------------------------------------------------------------------------------------------------------------------------------------
# Deploiement en mode prod-like (terminal WSL)
# ------------------------------------------------------------------------------------------------------------------------------------------


# ------------------------------------------------------------------------------------------------------------------------------------------
# Mode Attach
# ------------------------------------------------------------------------------------------------------------------------------------------

Fichier: Attach_tabs.ps1 et run_tabs.ps1

# Routing
export RUN_ENV="local"
export ATTACH_DEBUGGER_ADDRESS="127.0.0.1:9222"   # port du Chrome déjà lancé

# Quel onglet cibler (optionnel)
export ATTACH_TAB_SELECTOR="pick"               # current | last | best | pick | <index>

# Quelle route exécuter après attach
export RUN_MODE="resolution"                       # ou "preselection"

# Fonctionnel
export ACCOUNT_ID="topsurveys_bot_001"
export OPENAI_API_KEY="sk-..."
export LOG_LEVEL="DEBUG"
export CTA_INTERCEPT_ONLY="1"                      # 1 = inspecte les CTA sans cliquer

# Optionnel
export STATE_BACKEND="postgres"
export DATABASE_URL="postgres://..."