# ------------------------------------------------------------------------------------------------------------------------------------------
# 
# ------------------------------------------------------------------------------------------------------------------------------------------

![Cartographie des modes de lancements](image.png)

Récapitulatif des 4 modes
Mode 1 — Attach externe
ATTACH_DEBUGGER_ADDRESS=host:port → s'attache à un Chrome déjà lancé manuellement
Mode 2 — Local simple (chemin actuel)
RUN_ENV=local, pas LOCAL_USE_PROXY → webdriver.Chrome direct, bannière visible, pas de Popen
Mode 3 — Prod-like local (ATTACH_PROD_LIKE=1)
Popen + proxy relay + fingerprint + pause interactive → échoue en WSL si chrome.exe détecté
Mode 4 — Prod (RUN_ENV≠local ou LOCAL_USE_PROXY=1)


**Verdict brutal sur ta situation**

Tu as aujourd'hui **4 modes** dont 2 sont inutiles ou cassés pour ton cas d'usage :

- **Mode 1** (Attach externe) — utile uniquement pour du debug très manuel, pas pour tester le bot complet.
- **Mode 2** (Local simple) — c'est le chemin que tu empruntes. Il fonctionne pour tester rapidement, mais il génère la bannière automation et n'utilise pas Popen. Suffisant pour débugger la logique du bot.
- **Mode 3** (Prod-like local) — cassé en WSL car `_detect_chrome_binary()` matche Chrome Windows, dont le debug port est inaccessible depuis WSL.
- **Mode 4** (Prod) — le seul chemin robuste, mais déclenché uniquement si `LOCAL_USE_PROXY=1` ou `RUN_ENV≠local`.

**Ce que tu devrais avoir à la place : 2 modes seulement**

| Mode | Variables | Quand l'utiliser |
|---|---|---|
| Local debug | `RUN_ENV=local` | Debug rapide, cookies, logique surveybot — la bannière n'a pas d'impact sur le comportement réel |
| Prod-like | `RUN_ENV=local` + `LOCAL_USE_PROXY=1` + `SURVEY_BROWSER_BIN=/usr/bin/chromium` | Tester le comportement anti-détection avec proxy, identique à la prod |

**Mode 3 (`ATTACH_PROD_LIKE`) est mort-né en WSL** — à supprimer ou à corriger avec la logique bridge WSL. Et le vrai fix de fond reste de fusionner le Mode 2 dans le chemin Popen — c'est le prompt Codex qu'on avait préparé.


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
export ATTACH_PROD_LIKE="1"
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
export SURVEY_BROWSER_BIN="/usr/bin/chromium"
export TWO_CAPTCHA_KEY="ff2f59cd67845abf5c1b7db1c0a17cf2"
# export HTTP_PROXY="http://14abf236340a1:bb82a9e63b@185.134.194.152:12323"
# export HTTPS_PROXY="http://14abf236340a1:bb82a9e63b@185.134.194.152:12323"
export DATABASE_URL="postgres://postgres:3o1L6kfCFxuncbY@localhost:5432/postgres"

# Et le tunnel Fly doit être actif dans un terminal séparé :
flyctl proxy 5432 -a surveybot-db


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