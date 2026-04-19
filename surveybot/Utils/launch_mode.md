# ------------------------------------------------------------------------------------------------------------------------------------------
#  3 Modes de lancement
# ------------------------------------------------------------------------------------------------------------------------------------------
![Cartographie des mode de lancement](image-1.png)

## 1 Mode Attach
 Sa fais via les fichiers ps1 - rien a configurer ### Dans ce mode, le navigateur Chrome ouvert n'a aucun flag.

## 2 Mode prod
Lancement Fly.io, les variables sont stockées dans Fly Secrets ### Flag et navigateur inconnu car les screenshots ne capture que la page et non le bureau complet.

## 3 Mode Local ()

Se fais dans un terminal WSL, injecte toutes les variables ci dessous et lance le programme. ### Le navigateur est Chromium et le flag est --no-sandbox


# ------------------------------------------------------------------------------------------------------------------------------------------
# Deploiement en mode Local (terminal WSL)
# ------------------------------------------------------------------------------------------------------------------------------------------
# Activate the terminal
source /mnt/c/projects/Surveys/.venv-wsl/bin/activate

# Et le tunnel Fly doit être actif dans un terminal séparé :
flyctl proxy 5432 -a surveybot-db

# Variables requises pour que les cookies soient restaurés

export ACCOUNT_ID="topsurveys_bot_003" 
export EMAIL="antoinne.overstate714@8alias.com" # L'email est associé a un ACCOUNT_ID, regarde account_details.md
export DISPLAY=:0
export RUN_ENV="local"
export SNAP_ENABLED="0"
export RUN_MODE="local"
export LOG_LEVEL="DEBUG"
export LOCAL_CTA_DEBUG="1"
export SURVEY_HEADLESS="0"
export LOCAL_UNATTENDED="1" # le bot prend la main sur une page déjà ouverte manuellement.
export PROXY_PASS="9a42e8da8a"
export STATE_BACKEND="postgres"
export LIBGL_ALWAYS_SOFTWARE="1"
export PROXY_USER="14a4b3f88b892"
export LOCAL_CTA_REQUIRE_ENTER="1" # Pause before cta click
export PROXY_URL="178.210.254.157:12323"
export SURVEY_BROWSER_BIN="/usr/bin/chromium-browser"
export TWO_CAPTCHA_KEY="ff2f59cd67845abf5c1b7db1c0a17cf2"
export DATABASE_URL="postgres://postgres:3o1L6kfCFxuncbY@localhost:5432/postgres"
python3 surveybot/main.py
