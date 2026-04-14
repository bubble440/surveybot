# ------------------------------------------------------------------------------------------------------------------------------------------
# Deploiement en mode prod-like (terminal WSL)
# ------------------------------------------------------------------------------------------------------------------------------------------

# Variables requises pour que les cookies soient restaurés

export ACCOUNT_ID="topsurveys_bot_001"
export DISPLAY=:0
export SNAP_ENABLED="0"
export RUN_ENV="local"
export RUN_MODE="local"
export LOG_LEVEL="DEBUG"
# export LOCAL_USE_PROXY="1"
export SURVEY_HEADLESS="0"
# export LOCAL_HEADLESS="0"
export CTA_INTERCEPT_ONLY="1"
export STATE_BACKEND="postgres"
export PROXY_PASS="bb82a9e63b"
export LIBGL_ALWAYS_SOFTWARE="1"
export PROXY_USER="14abf236340a1"
export PROXY_URL="185.134.194.152:12323"
export EMAIL="krakouz.cement183@8alias.com"
export TWO_CAPTCHA_KEY="ff2f59cd67845abf5c1b7db1c0a17cf2"
# export HTTP_PROXY="http://14abf236340a1:bb82a9e63b@185.134.194.152:12323"
# export HTTPS_PROXY="http://14abf236340a1:bb82a9e63b@185.134.194.152:12323"
export DATABASE_URL="postgres://postgres:3o1L6kfCFxuncbY@localhost:5432/postgres"

# Et le tunnel Fly doit être actif dans un terminal séparé :
flyctl proxy 5432 -a surveybot-db