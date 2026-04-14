# ------------------------------------------------------------------------------------------------------------------------------------------
# Deploiement en mode prod-like (terminal WSL)
# ------------------------------------------------------------------------------------------------------------------------------------------

# Variables requises pour que les cookies soient restaurés

export ACCOUNT_ID="topsurveys_bot_001"
export PROXY_PASS="bb82a9e63b"
export PROXY_USER="14abf236340a1"
export PROXY_URL="185.134.194.152:12323"
export STATE_BACKEND="postgres"
export DATABASE_URL="postgres://postgres:3o1L6kfCFxuncbY@localhost:5432/postgres"
export EMAIL="krakouz.cement183@8alias.com"

# Et le tunnel Fly doit être actif dans un terminal séparé :
flyctl proxy 5432 -a surveybot-db