# ------------------------------------------------------------------------------------------------------------------------------------------
# Deploiement en mode Local (terminal WSL)
# ------------------------------------------------------------------------------------------------------------------------------------------
# Activate the terminal
source /mnt/c/projects/Surveys/.venv-wsl/bin/activate

# Et le tunnel Fly doit être actif dans un terminal séparé :
flyctl proxy 5432 -a surveybot-db

# Variables requises pour que les cookies soient restaurés

export ACCOUNT_ID="topsurveys_bot_004" 
export EMAIL="bezbar.pacify458@simplelogin.fr" # L'email est associé a un ACCOUNT_ID, regarde account_details.md
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


# ------------------------------------------------------------------------------------------------------------------------------------------
# Procédure : Capture fingerprint prod (browserleaks)
# ------------------------------------------------------------------------------------------------------------------------------------------

## Étape 1 — Lancer une machine idle (PowerShell)
# Lance l'image bot avec Xvfb démarré mais sans le bot (sleep 3600 = 1h disponible)
flyctl machine run registry.fly.io/surveybot-bot:latest `
  --app surveybot-bot `
  --region cdg `
  --vm-memory 2048 `
  --env RUN_ENV=prod `
  --env ACCOUNT_ID=topsurveys_bot_001 `
  --entrypoint "/bin/bash -c 'Xvfb :99 -screen 0 1920x1080x24 & sleep 3600'"
# Note l'ID de machine affiché (ex: d8901e2c5ee738)

## Étape 2 — SSH sur la machine (PowerShell)
flyctl ssh console -a surveybot-bot -s
# Sélectionner la machine avec "sleep" dans la liste si plusieurs machines

## Étape 3 — Dans le shell Linux (une commande à la fois)
pip install boto3 pillow selenium --quiet
DISPLAY=:99 PROXY_URL="http://14a4b3f88b892:9a42e8da8a@81.181.236.36:12323" PROXY_USER="14a4b3f88b892" PROXY_PASS="9a42e8da8a" ACCOUNT_ID=topsurveys_bot_001 python tools/fingerprint_check.py

DISPLAY=:99 PROXY_URL="http://14a4b3f88b892:9a42e8da8a@81.181.236.36:12323" PROXY_USER="14a4b3f88b892" PROXY_PASS="9a42e8da8a" ACCOUNT_ID=topsurveys_bot_001 python tools/webgl_json_dump.py

DISPLAY=:99 PROXY_URL="http://14a4b3f88b892:9a42e8da8a@81.181.236.36:12323" PROXY_USER="14a4b3f88b892" PROXY_PASS="9a42e8da8a" ACCOUNT_ID=topsurveys_bot_001 python tools/multi_access_check.py

# Les PNG sont sauvegardés dans /tmp/fp_*.png
# L'upload R2 échouera si SNAP_R2_ACCOUNT_ID n'est pas défini — c'est normal, les PNG locaux suffisent

## Étape 4 — Récupérer les PNG (nouveau terminal PowerShell, pas dans le SSH, en gardant le SSH ouvert)
flyctl ssh sftp get /tmp/prod_canvas.png -a surveybot-bot
flyctl ssh sftp get /tmp/prod_webgl.png -a surveybot-bot
flyctl ssh sftp get /tmp/prod_javascript.png -a surveybot-bot
flyctl ssh sftp get /tmp/prod_ip.png -a surveybot-bot
# Les fichiers atterrissent dans le répertoire courant PowerShell
# Récupérer le résultat :

## Etape 4.2: download le JSON Dump de Webgl si tel etait le but
flyctl ssh sftp get /tmp/prod_webgl_dump.json -a surveybot-bot

## Étape 5 — Détruire la machine idle après les tests (PowerShell)
flyctl machine destroy <ID-DE-LA-MACHINE> -a surveybot-bot --force
# Remplacer <ID-DE-LA-MACHINE> par l'ID noté à l'étape 1
