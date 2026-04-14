$projectDir = "C:\projects\Surveys"
Set-Location $projectDir

# Vars prod
$env:ACCOUNT_ID         = "topsurveys_bot_001"
$env:EMAIL              = "krakouz.cement183@8alias.com"
$env:RUN_ENV            = "local"
$env:RUN_MODE           = "local"
$env:SURVEY_HEADLESS    = "0"        # visible pour observer
$env:PROXY_URL          = "185.134.194.152:12323"
$env:PROXY_USER         = "14abf236340a1"
$env:PROXY_PASS         = "bb82a9e63b"
$env:DATABASE_URL       = "postgres://postgres:3o1L6kfCFxuncbY@localhost:5432/postgres"
$env:STATE_BACKEND      = "postgres"
$env:HTTPS_PROXY        = "http://14abf236340a1:bb82a9e63b@185.134.194.152:12323"
$env:HTTP_PROXY         = "http://14abf236340a1:bb82a9e63b@185.134.194.152:12323"
$env:LOCAL_USE_PROXY    = "1"

# Vars debug
$env:LOG_LEVEL          = "DEBUG"
$env:SNAP_ENABLED       = "0"        # inutile pour le test
$env:CTA_INTERCEPT_ONLY = "1"
$env:TWO_CAPTCHA_KEY    = "ff2f59cd67845abf5c1b7db1c0a17cf2"

# Pas de ATTACH_DEBUGGER_ADDRESS → Playwright lance son propre Chrome
$venvPy = Join-Path $projectDir ".venv\Scripts\python.exe"
& $venvPy ".\surveybot\main.py"