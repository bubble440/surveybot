param(
  [Parameter(Mandatory=$true)][int]$Port,
  [string]$ProjectDir = "C:\projects\Surveys",
  [string]$TargetUrl = "https://www.topsurveys.app",
  [string]$AttachTabSelector = "pick",
  # Valeur par défaut inchangée : préserve le comportement TopSurveys existant
  # pour tout appel sans -Platform. Passer -Platform "ysense" ou
  # -Platform "primeopinion" pour cibler une autre plateforme.
  [string]$Platform = "topsurveys"
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
  # Le port est déjà en écoute — mais rien ne garantit que ce Chrome est bien
  # une instance de debug isolée : ça peut être un bot de PROD déjà en cours
  # d'exécution sur ce même port (ex: bot_001), auquel cas s'attacher dessus
  # revient à piloter un navigateur déjà occupé (profil, cookies, proxy et
  # navigation en cours différents de ce qu'on attend), ce qui explique un
  # comportement incohérent une fois attaché (pages qui ne chargent jamais,
  # navigation qui semble figée, etc.).
  # On identifie le process réel derrière le port et son --user-data-dir pour
  # comparer au profil de debug attendu, au lieu de réutiliser en silence.
  $ownerPid = ($listen | Select-Object -First 1 -ExpandProperty OwningProcess)
  $cmdLine = $null
  try {
    $cmdLine = (Get-CimInstance Win32_Process -Filter "ProcessId=$ownerPid" -ErrorAction Stop).CommandLine
  } catch {
    $cmdLine = $null
  }

  Write-Host "[PORT $Port] Port deja en ecoute - PID=$ownerPid"
  if($cmdLine){
    Write-Host "[PORT $Port] Ligne de commande du process existant : $cmdLine"
  } else {
    Write-Host "[PORT $Port] Impossible de lire la ligne de commande du process $ownerPid (droits insuffisants ?)."
  }

  if($cmdLine -and ($cmdLine -like "*$profileDir*")){
    Write-Host "[PORT $Port] Profil confirme : $profileDir (Chrome de debug deja lance, reutilisation OK)."
  } else {
    $msg = "[PORT $Port] Chrome deja en ecoute sur ce port n'utilise PAS le profil de debug attendu ($profileDir). Il s'agit probablement d'un autre process (ex: bot de prod) deja attache a ce port. Ferme ce Chrome ou choisis un autre port avant de relancer, pour eviter de piloter le mauvais profil."
    throw $msg
  }
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
$env:ACCOUNT_ID ="top surveys_bot_001" # Valeur a modifier dans le terminal pour sauvegarder les cookies dans le bon compte.
$env:RUN_ENV="local"
$env:RUN_MODE="local"
$env:STATE_BACKEND = "postgres"
$env:DATABASE_URL = "postgres://postgres:3o1L6kfCFxuncbY@localhost:5432/postgres"
$env:BROWSER_MODE="attach"
$env:ATTACH_TAB_SELECTOR=$AttachTabSelector
$env:ATTACH_DEBUGGER_ADDRESS="127.0.0.1:$Port"

# Tes vars debug
$env:Email="wilsaah456@gmail.com"
$env:Password="<TA_CLE_ICI>"
$env:LOG_LEVEL="DEBUG"
$env:PLATFORM=$Platform
$env:DOM_DEBUG_FRAMES="1"
$env:SURVEY_CTX_DEBUG="1"
$env:ATTACH_ROUTE="resolution" # ← changer ici pour tester d'autres routes attach (login, preselection, resolution)
$env:BOT_RUN_ID="port_$Port"
$env:ACTION_DEBUG_TARGET="1"
$env:LOCAL_CTA_REQUIRE_ENTER="1"
$env:CAPTCHA_PROVIDER="capsolver"
$env:SURVEY_RESOURCE_BLOCKING="0"
$env:telegram_bot_token= "<TA_CLE_ICI>"
$env:telegram_chat_id= "<TA_CLE_ICI>"
$env:TWO_CAPTCHA_KEY="<TA_CLE_ICI>"
$env:CAPSOLVER_API_KEY="<TA_CLE_ICI>"
$env:OPENAI_API_KEY="<TA_CLE_ICI>"

# Utiliser python du venv si présent (plus fiable)
$venvPy = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if(Test-Path $venvPy){
  & $venvPy ".\surveybot\main.py"
} else {
  python ".\surveybot\main.py"
}