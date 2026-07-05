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
Write-Host "Mode route attach : ATTACH_ROUTE=$env:ATTACH_ROUTE (modifiable ci-dessous)."
Write-Host "Valeurs possibles : resolution | preselection | login"
Write-Host ""

# Vars env (attach)
#test prod-like
$env:ACCOUNT_ID ="topsurveys_bot_101" # Valeur a modifier dans le terminal pour sauvegarder les cookies dans le bon compte.
$env:RUN_ENV="local"
$env:RUN_MODE="local"
$env:STATE_BACKEND = "postgres"
$env:DATABASE_URL = "postgres://postgres:3o1L6kfCFxuncbY@localhost:5432/postgres"
$env:BROWSER_MODE="attach"
$env:ATTACH_TAB_SELECTOR=$AttachTabSelector
$env:ATTACH_DEBUGGER_ADDRESS="127.0.0.1:$Port"

# Tes vars debug
$env:LOG_LEVEL="DEBUG"
$env:DOM_DEBUG_FRAMES="1"
$env:SURVEY_CTX_DEBUG="1"
$env:ATTACH_ROUTE="resolution"
$env:BOT_RUN_ID="port_$Port"
$env:ACTION_DEBUG_TARGET="1"
$env:LOCAL_CTA_REQUIRE_ENTER="1"
$env:CAPTCHA_PROVIDER="capsolver"
$env:SURVEY_RESOURCE_BLOCKING="0"
$env:TWO_CAPTCHA_KEY="ff2f59cd67845abf5c1b7db1c0a17cf2"
$env:CAPSOLVER_API_KEY="CAP-9AD6AB7A2E5A42B4935558CA7493AFC30CDDD8CC543AA5665419B7A63DAE26A0"

# Utiliser python du venv si présent (plus fiable)
$venvPy = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if(Test-Path $venvPy){
  & $venvPy ".\surveybot\main.py"
} else {
  python ".\surveybot\main.py"
}