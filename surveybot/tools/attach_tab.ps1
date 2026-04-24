param(
  [Parameter(Mandatory=$true)][int]$Port,
  [string]$ProjectDir = "C:\projects\Surveys",
  [string]$TargetUrl = "https://www.topsurveys.app",
  [string]$AttachTabSelector = "pick"
)

# Chrome path
$chromeCandidates = @(
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "$env:ProgramFiles(x86)\Google\Chrome\Application\chrome.exe"
)
$chromePath = $chromeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if(-not $chromePath){ throw "chrome.exe introuvable." }

# Profile dédié
$profilesRoot = Join-Path $env:TEMP "sb_chrome_profiles"
New-Item -ItemType Directory -Force $profilesRoot | Out-Null
$profileDir = Join-Path $profilesRoot "chrome_$Port"

# Aller dans le projet
Set-Location $ProjectDir

# S’assurer qu’on lance bien le bon entrypoint
if(-not (Test-Path ".\surveybot\main.py")){
  throw "Introuvable: $ProjectDir\surveybot\main.py (ProjectDir mauvais?)"
}

# Lancer Chrome si port non listening
$listen = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
if(-not $listen){
  New-Item -ItemType Directory -Force $profileDir | Out-Null
  $chromeArgs = @(
    "--remote-debugging-port=$Port"
    "--user-data-dir=$profileDir"
    "--no-first-run"
    "--no-default-browser-check"
    "--remote-allow-origins=*"
    $TargetUrl
  )  
  Start-Process -FilePath $chromePath -ArgumentList $chromeArgs -WindowStyle Minimized | Out-Null
  Write-Host "[PORT $Port] Chrome lancé + URL ouverte: $TargetUrl"
} else {
  Write-Host "[PORT $Port] Port déjà en écoute (Chrome debug déjà lancé)"
}

Write-Host ""
Write-Host "MANUEL:"
Write-Host "1) Va dans le Chrome du port $Port."
Write-Host "2) Connecte-toi, lance un survey, arrête-toi sur la page cible."
Write-Host "3) Reviens ici."
Write-Host ""
Write-Host "Mode route attach : resolution par défaut (sans prompt)."
Write-Host "Pour activer le choix interactif à chaque lancement de main.py :"
Write-Host "  `$env:ATTACH_ROUTE_PROMPT=1"
Write-Host ""

# Vars env (attach)
#test prod-like
$env:ACCOUNT_ID ="topsurveys_bot_101" # Valeur a modifier dans le terminal pour sauvegarder les cookies dans le bon compte.
# $env:PROXY_USER="14abf236340a1"
# $env:PROXY_PASS="bb82a9e63b"
# $env:PROXY_URL="185.134.194.152:12323" #Ces 3 valeurs sont responsable de l'ouverture du pop-up de saisi des creds lors du launch.

$env:RUN_ENV="local"
$env:RUN_MODE="local"
$env:DATABASE_URL = "postgres://postgres:3o1L6kfCFxuncbY@localhost:5432/postgres"
# $env:SURVEY_HEADLESS="1"
# $env:ACCOUNT_ID = "topsurveys_test_prod_like"
# $env:STATE_BACKEND = "postgres"
# $env:LOCAL_USE_PROXY = "1"

# $env:HTTPS_PROXY = "http://14abf236340a1:bb82a9e63b@185.134.194.152:12323"
# $env:HTTP_PROXY  = "http://14abf236340a1:bb82a9e63b@185.134.194.152:12323"

$env:BROWSER_MODE="attach"
$env:ATTACH_TAB_SELECTOR=$AttachTabSelector
$env:ATTACH_DEBUGGER_ADDRESS="127.0.0.1:$Port"

# Tes vars debug
$env:LOG_LEVEL="DEBUG"
$env:DOM_DEBUG_FRAMES="1"
$env:SURVEY_CTX_DEBUG="1"
$env:ATTACH_ROUTE_PROMPT="0"
$env:BOT_RUN_ID="port_$Port"
$env:ACTION_DEBUG_TARGET="1"
$env:LOCAL_CTA_REQUIRE_ENTER="1"
$env:SURVEY_RESOURCE_BLOCKING="0"
$env:TWO_CAPTCHA_KEY="ff2f59cd67845abf5c1b7db1c0a17cf2"
$env:FIVESIM_API_KEY="eyJhbGciOiJSUzUxMiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE4MDgxNjY5MDMsImlhdCI6MTc3NjYzMDkwMywicmF5IjoiMjlkMzI4MGM4YjMzYTI2MWUwYWNiNzMyNjQ3MjY1ZjUiLCJzdWIiOjM5ODIyNjN9.jtHwiXwr-F1PCKJ8Ka79woFNMm4c7VLJYRzgskCrSKSMy9FIxktAUztjEiafTD-Hw_UKGwdUEDZMw2o8K_4JzIwBWa_Gb2noQqODIwF0Pj7xhe1-znbd49wNMJnCsicj03nYRyEefkieZUUROzeKLfGiKESuwk9xqoXxhIcPNu8OZCft6pOxrTmXI26gSgZiwtEDkd2DlvDsMTyRbAAVHEI8BwCS32OhckC4p0GfGwL326R-bk2iN_m1oJ0-cOKaM8B40MT-Fsasfe_IbjNAguPyZtVSjCdiHv91aHfHCYgHKFUOkMpFmRqhBp7T2gthCG6sY462sWBIYFZS2gh9-g"
# $env:SNAP_ENABLED="1"
# $env:CTA_INTERCEPT_ONLY="0"
# $env:SNAP_R2_BUCKET="surveybot-snaps"
# $env:SNAP_R2_ACCOUNT_ID="abd97f9fc4f9f5bb4300c66f9cd135b8"
# $env:SNAP_R2_ACCESS_KEY_ID="f4bf1a250dba4cdb8638c565138c3de7"
# $env:SNAP_R2_SECRET_ACCESS_KEY="4e4a5db003a362773c8e4eff3c5441e034a49d4aaa78b3d202b0aecd53e0e742"
# $env:FAILURE_PIPELINE_TRIGGER_FILE = "C:/tmp/fp_trigger"

# Utiliser python du venv si présent (plus fiable)
$venvPy = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if(Test-Path $venvPy){
  & $venvPy ".\surveybot\main.py"
} else {
  python ".\surveybot\main.py"
}
