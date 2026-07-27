# nssm_setup_bot.ps1
# Configure un service NSSM par bot defini dans accounts.json.
# Idempotent : peut etre relance pour mettre a jour des services existants.
#
# Usage :
#   .\nssm_setup_bot.ps1                           # tous les comptes
#   .\nssm_setup_bot.ps1 -AccountId "bot1"         # un seul compte (ajout / mise a jour)
#   .\nssm_setup_bot.ps1 -AccountsFile "D:\surveybot\accounts.json"
#
# Prerequis : nssm.exe dans le PATH, droits administrateur.
# A executer depuis C:\surveybot\ ou avec -InstallDir pointant vers le bon dossier.

param(
    [string]$AccountId       = "",               # filtre optionnel : traiter un seul compte
    [string]$InstallDir      = "C:\surveybot",
    [string]$PythonRelPath   = "venv\Scripts\python.exe",
    [string]$MainRelPath     = "code\main.py",
    [string]$AccountsFile    = "",               # defaut : $InstallDir\accounts.json
    [string]$LogDir          = "",               # defaut : $InstallDir\logs
    [int]   $RestartDelaySec = 30,
    [int]   $MAX_ACCOUNTS    = 50               # garde-fou : abandon si > N comptes
)

# Valeurs derivees
if (-not $AccountsFile) { $AccountsFile = Join-Path $InstallDir "accounts.json" }
if (-not $LogDir)       { $LogDir       = Join-Path $InstallDir "logs" }
$pythonPath = Join-Path $InstallDir $PythonRelPath
$mainPath   = Join-Path $InstallDir $MainRelPath

# -- Garde-fou droits administrateur ------------------------------------------
# nssm install/set echoue silencieusement sans droits admin (Access is denied
# en cascade sur chaque "nssm set"), tout en laissant le script continuer et
# afficher un faux "[OK] configure." en fin de boucle. On coupe court ici,
# avant toute autre action, plutot que de laisser echouer chaque commande nssm
# une par une plus bas.
$_isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $_isAdmin) {
    Write-Error "Droits administrateur requis - relance PowerShell via 'Executer en tant qu'administrateur'."
    exit 1
}

# -- Pre-verifications --------------------------------------------------------

if (-not (Test-Path $pythonPath)) {
    Write-Error "python.exe introuvable : $pythonPath"
    exit 1
}

if (-not (Test-Path $mainPath)) {
    Write-Error "code\main.py introuvable : $mainPath"
    exit 1
}

if (-not (Test-Path $AccountsFile)) {
    Write-Error "accounts.json introuvable : $AccountsFile"
    exit 1
}

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

# -- Lecture accounts.json ----------------------------------------------------

$raw      = Get-Content -Path $AccountsFile -Raw -Encoding UTF8
$accounts = $raw | ConvertFrom-Json

if (-not $accounts -or @($accounts).Count -eq 0) {
    Write-Warning "accounts.json est vide - aucun service a configurer."
    exit 0
}

# Filtre optionnel par AccountId
if ($AccountId) {
    $accounts = @($accounts | Where-Object { $_.ACCOUNT_ID -eq $AccountId })
    if ($accounts.Count -eq 0) {
        Write-Error "Aucun compte trouve pour AccountId='$AccountId' dans $AccountsFile"
        exit 1
    }
}

Write-Output "=== nssm_setup_bot.ps1 - $(@($accounts).Count) compte(s) a traiter ==="

# -- Boucle principale --------------------------------------------------------

$processed  = 0
$accountIds = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)

foreach ($account in $accounts) {

    if ($processed -ge $MAX_ACCOUNTS) {
        Write-Warning "[GUARD] MAX_ACCOUNTS=$MAX_ACCOUNTS atteint - comptes restants ignores."
        break
    }

    # Extraction des champs PAR_BOT tels qu'ils existent dans accounts.json
    $acctId    = "$($account.ACCOUNT_ID)".Trim()
    $email     = "$($account.EMAIL)".Trim()
    $password  = "$($account.PASSWORD)".Trim()
    $proxyUrl  = "$($account.PROXY_URL)".Trim()
    $proxyUser = "$($account.PROXY_USER)".Trim()
    $proxyPass = "$($account.PROXY_PASS)".Trim()

    # profile_dir ou CHROME_PROFILE_DIR (alias historique) - meme champ logique
    $profileDir = ""
    if ($account.PSObject.Properties.Name -contains "profile_dir") {
        $profileDir = "$($account.profile_dir)".Trim()
    }
    if (-not $profileDir -and ($account.PSObject.Properties.Name -contains "CHROME_PROFILE_DIR")) {
        $profileDir = "$($account.CHROME_PROFILE_DIR)".Trim()
    }

    if (-not $acctId) {
        Write-Warning "[SKIP] Entree sans ACCOUNT_ID - ignoree."
        continue
    }

    # Ajoute AVANT la verification profile_dir : un compte present dans accounts.json
    # mais skippe ci-dessous ne doit jamais etre signale comme service NSSM "orphelin"
    # dans la detection en fin de script - le compte existe, il manque juste son dossier.
    [void]$accountIds.Add($acctId)

    # Verification du dossier profil Chrome - meme garde-fou que l'ancien launch_all.ps1.
    # Sans ca, NSSM (via SERVICE_AUTO_START) tenterait de demarrer un bot dont le profil
    # n'existe pas, avec un crash immediat au lieu d'un skip propre et logge.
    if (-not $profileDir -or -not (Test-Path $profileDir)) {
        Write-Warning "[SKIP] $acctId - profile_dir introuvable ou vide : '$profileDir' - service NSSM non configure."
        continue
    }

    $processed++

    $svcName   = "surveybot_$acctId"
    $logStdout = Join-Path $LogDir "bot_${acctId}_stdout.log"
    $logStderr = Join-Path $LogDir "bot_${acctId}_stderr.log"

    Write-Output ""
    Write-Output "--- $svcName ---"
    # PASSWORD deliberement absent du log (regle projet : ne jamais afficher les mots de passe)
    Write-Output "    ACCOUNT_ID=$acctId  EMAIL=$email  PROXY_URL=$proxyUrl  CHROME_PROFILE_DIR=$profileDir"

    # -- Installation ou mise a jour -------------------------------------------
    & nssm status $svcName 2>$null | Out-Null
    $_serviceExisted = ($LASTEXITCODE -eq 0)
    if (-not $_serviceExisted) {
        Write-Output "    [NSSM] Installation du service..."
        & nssm install $svcName $pythonPath $MainRelPath
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "[SKIP] $svcName - echec 'nssm install' (code=$LASTEXITCODE) - service non configure. Verifier les droits admin."
            continue
        }
    } else {
        Write-Output "    [NSSM] Service existant - mise a jour de la config."
        & nssm set $svcName Application     $pythonPath
        & nssm set $svcName AppParameters   $MainRelPath
    }

    # -- Parametres de base ----------------------------------------------------
    # AppDirectory = racine (InstallDir), JAMAIS code\ - l'auto-update (update_checker.py)
    # renomme code\ pendant que le process tourne ; si AppDirectory pointait dedans,
    # Windows refuserait le renommage tant que le service est actif.
    & nssm set $svcName AppDirectory  $InstallDir

    # -- Variables d'environnement PAR_BOT -------------------------------------
    # Aligne sur launch_all.ps1 : meme jeu de variables, memes valeurs par defaut
    # pour GEO_LAT/GEO_LON/SURVEY_LANG/SURVEY_TZ (absents de accounts.json car non
    # acceptes par import_accounts.py - toujours injectes avec les defauts ci-dessous).
    # CHROME_PROFILE_DIR est le nom normalise cote env (alias de profile_dir).
    & nssm set $svcName AppEnvironmentExtra `
        "ACCOUNT_ID=$acctId" `
        "EMAIL=$email" `
        "PASSWORD=$password" `
        "PROXY_URL=$proxyUrl" `
        "PROXY_USER=$proxyUser" `
        "PROXY_PASS=$proxyPass" `
        "CHROME_PROFILE_DIR=$profileDir" `
        "BROWSER_MODE=prod" `
        "GEO_LAT=48.8566" `
        "GEO_LON=2.3522" `
        "SURVEY_LANG=fr-FR" `
        "SURVEY_TZ=Europe/Paris" `
        "PYTHONIOENCODING=utf-8" `
        "PYTHONUTF8=1"

    # -- Logs ------------------------------------------------------------------
    & nssm set $svcName AppStdout        $logStdout
    & nssm set $svcName AppStderr        $logStderr
    & nssm set $svcName AppRotateFiles   1
    & nssm set $svcName AppRotateSeconds 86400    # rotation quotidienne
    & nssm set $svcName AppRotateBytes   10485760 # 10 Mo max par fichier

    # -- Methodes d'arret gracieux ---------------------------------------------
    #
    # Sur Windows, seule la methode Console (GenerateConsoleCtrlEvent CTRL_BREAK_EVENT)
    # est recue par Python et declenche notre handler SIGBREAK -> sequence de fermeture
    # propre (record_exit, Postgres, Chrome). Les methodes Window (WM_CLOSE) et Thread
    # (PostQuitMessage) sont sans effet sur un process console Python - on les saute
    # (AppStopMethodSkip 6 = bitmask : Window=2 + Thread=4) pour eviter ~3 s d'attente
    # inutile avant que NSSM ne passe directement a TerminateProcess.
    # AppStopMethodConsole est le seul timeout qui compte : c'est la fenetre accordee
    # a la sequence de fermeture propre (Postgres + Chrome + record_exit). 30 s est
    # une marge confortable ; TerminateProcess ne s'enclenche qu'au-dela.
    #
    & nssm set $svcName AppStopMethodSkip    6      # skip Window (2) + Thread (4)
    & nssm set $svcName AppStopMethodConsole 30000  # 30 s pour la fermeture propre

    # -- Politique de redemarrage selon le code de sortie ----------------------
    #
    # EXIT_VOLUNTARY    = 0  -> ne pas redemarrer (SIGINT/SIGBREAK propre, target journalier...)
    # EXIT_CRASH        = 1  -> redemarrer (crash Python ou sortie inattendue)
    # EXIT_SOFT_RESTART = 2  -> redemarrer (idle, trop d'erreurs, runtime_limit...)
    # EXIT_FATAL        = 3  -> ne pas redemarrer (seuil de crash-loop depasse)
    # Tout autre code   -> redemarrer par defaut (comportement NSSM sur)
    #
    & nssm set $svcName AppExit Default  Restart
    & nssm set $svcName AppExit 0        Exit      # EXIT_VOLUNTARY : pas de restart
    & nssm set $svcName AppExit 3        Exit      # EXIT_FATAL     : pas de restart
    & nssm set $svcName AppRestartDelay  ($RestartDelaySec * 1000)   # en millisecondes

    # -- Demarrage automatique au boot -----------------------------------------
    & nssm set $svcName Start SERVICE_AUTO_START

    Write-Output "    [OK] $svcName configure."
}

# -- Detection des services NSSM orphelins ------------------------------------
# Un service surveybot_* sans entree correspondante dans accounts.json est signale
# clairement mais jamais supprime automatiquement - une desinstallation est une
# decision humaine (nssm remove <nom> confirm).

Write-Output ""
Write-Output "=== Verification des services orphelins (prefixe surveybot_) ==="

try {
    $existingSvcs = @(Get-Service -Name "surveybot_*" -ErrorAction SilentlyContinue)
    $orphans = @($existingSvcs | Where-Object {
        $svcAccountId = $_.Name -replace "^surveybot_", ""
        -not $accountIds.Contains($svcAccountId)
    })

    if ($orphans.Count -gt 0) {
        Write-Warning "Services sans compte dans accounts.json (verifier manuellement) :"
        foreach ($o in $orphans) {
            Write-Warning "  -> $($o.Name)  [Status=$($o.Status)]  - pour supprimer : nssm remove $($o.Name) confirm"
        }
    } else {
        Write-Output "Aucun service orphelin detecte."
    }
} catch {
    Write-Warning "Impossible de lister les services Windows : $_"
}

Write-Output ""
Write-Output "=== nssm_setup_bot.ps1 termine ($processed compte(s) traite(s)) ==="
Write-Output "  Demarrer tous  : Get-Service surveybot_* | Start-Service"
Write-Output "  Statut tous    : Get-Service surveybot_* | Format-Table Name,Status -AutoSize"
Write-Output ""
Write-Output "Pour installer la tache planifiee de detection zombie :"
Write-Output "  Voir le header de check_zombie_bots.ps1 (Register-ScheduledTask)"