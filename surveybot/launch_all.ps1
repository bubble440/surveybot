# launch_all.ps1
# Lancement MANUEL et PONCTUEL d'un seul compte, pour un test isole - hors perimetre
# de la supervision de production (NSSM + check_zombie_bots.ps1 + wake_scheduler.ps1,
# cf. Utils/ORCHESTRATION_TRACKING.md section 9).
#
# NE JAMAIS planifier ce script (Planificateur de taches Windows ou autre) : un bot
# lance ainsi tourne comme process brut, invisible pour check_zombie_bots.ps1 et
# wake_scheduler.ps1 qui n'agissent que sur des services NSSM (nssm status/start/
# restart surveybot_<id>). Le parc de production est exploite exclusivement via les
# services NSSM installes par nssm_setup_bot.ps1.
#
# Usage :
#   .\launch_all.ps1 -AccountId "bot1"
#
# Prerequis :
#   - accounts.json dans le meme dossier que ce script (C:\surveybot\, la racine).
#   - venv\ contenant l'interpreteur Python + dependances (requirements.txt).
#   - code\ contenant les sources (main.py, _license_config.py, global_config.py...).
#     Ce dossier est remplace en entier par l'auto-update (update_checker.py) : il ne
#     doit contenir QUE du code source, jamais de donnees persistantes.
#   - Dossiers profiles\ crees manuellement (un par bot).
#   - Dossier pids\ cree automatiquement au premier lancement.
#
# IMPORTANT : le processus est lance avec WorkingDirectory = la racine (PSScriptRoot),
# jamais code\. Sur Windows, un dossier qui est le repertoire courant d'un process ne
# peut pas etre renomme/supprime - si le cwd etait code\, l'auto-update ne pourrait
# jamais swapper ce dossier tant que le bot tourne.

param(
    [Parameter(Mandatory = $true)]
    [string]$AccountId,
    [string]$AccountsFile = "$PSScriptRoot\accounts.json",
    [string]$PythonExe    = "$PSScriptRoot\venv\Scripts\python.exe",
    [string]$MainScript   = "$PSScriptRoot\code\main.py",
    [string]$PidsDir      = "$PSScriptRoot\pids",
    [string]$LogDir       = "$PSScriptRoot\logs"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Nombre de cycles de lancement passes conserves en historique de logs, au-dela 
# du cycle courant (bot_$id.log.1 = cycle precedent, ... .10 = plus ancien
# conserve). Permet l'analyse retrospective de comportements intermittents.
$LOG_HISTORY_CYCLES = 10

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Write-Log {
    param([string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $Message"
    Write-Host $line
    try {
        Add-Content -Path "$LogDir\launch_all.log" -Value $line -Encoding UTF8
    } catch {}
}

function Get-PidPath {
    param([string]$AccountId)
    return Join-Path $PidsDir "bot_$AccountId.pid"
}

function Test-NssmServiceExists {
    # Empeche un double lancement (meme profil Chrome/proxy) si un service NSSM
    # surveybot_<id> est deja installe pour ce compte, quel que soit son statut
    # (running ou stoppe - meme stoppe, il reste le chemin de supervision attendu).
    param([string]$AccountId)
    $svcName = "surveybot_$AccountId"
    try {
        & nssm status $svcName 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        # nssm.exe absent du PATH (ex: machine de test isolee sans NSSM) - pas de
        # service possible, donc pas de conflit a craindre.
        return $false
    }
}

function Test-BotProcessAlive {
    # Un PID Windows est recyclable : une fois le process du bot termine, l'OS peut
    # reattribuer ce meme PID a un process totalement different peu apres. Verifier
    # seulement l'existence du PID (ex: via tasklist) est donc insuffisant - il faut
    # aussi confirmer que c'est bien le MEME process (heure de demarrage identique).
    param(
        [int]$ProcessId,
        [long]$ExpectedStartTicks
    )
    try {
        $p = Get-Process -Id $ProcessId -ErrorAction Stop
        return ($p.StartTime.Ticks -eq $ExpectedStartTicks)
    } catch {
        # PID absent ou inaccessible (process systeme protege, etc.)
        return $false
    }
}

function Start-Bot {
    param([hashtable]$Bot)

    $id          = $Bot.account_id
    $profileDir  = $Bot.profile_dir

    # Verification du dossier profil Chrome
    if (-not (Test-Path $profileDir)) {
        Write-Log "SKIP $id - profile_dir introuvable : $profileDir"
        return
    }

    # Variables d'environnement passees au processus
    # LICENSE_KEY et DATABASE_URL sont embarquees dans le compile - absentes ici.
    $env_vars = @{
        "ACCOUNT_ID"        = $id
        "EMAIL"             = $Bot.email
        "PASSWORD"          = $Bot.password
        "PROXY_URL"         = $Bot.proxy_url
        "PROXY_USER"        = $Bot.proxy_user
        "PROXY_PASS"        = $Bot.proxy_pass
        "CHROME_PROFILE_DIR"= $profileDir
        "RUN_ENV"           = "prod"
        "GEO_LAT"           = if ($Bot.ContainsKey("geo_lat"))     { $Bot.geo_lat }     else { "48.8566" }
        "GEO_LON"           = if ($Bot.ContainsKey("geo_lon"))     { $Bot.geo_lon }     else { "2.3522" }
        "SURVEY_LANG"       = if ($Bot.ContainsKey("survey_lang")) { $Bot.survey_lang } else { "fr-FR" }
        "SURVEY_TZ"         = if ($Bot.ContainsKey("survey_tz"))   { $Bot.survey_tz }   else { "Europe/Paris" }
        # Sans ca, print() d'un caractere hors cp1252 (emoji, etc.) plante le process
        # des que stdout est redirige vers un pipe (cas de ce script) au lieu d'un
        # vrai terminal -- deja present dans nssm_setup_bot.ps1, manquait ici.
        "PYTHONIOENCODING"  = "utf-8"
        "PYTHONUTF8"        = "1"
    }

    # Construire le bloc d'environnement pour Start-Process
    # On modifie une copie de l'env courant pour ne pas polluer le processus lanceur
    $envBlock = [System.Collections.Specialized.StringDictionary]::new()
    foreach ($kv in [System.Environment]::GetEnvironmentVariables().GetEnumerator()) {
        $envBlock[$kv.Key] = $kv.Value
    }
    foreach ($kv in $env_vars.GetEnumerator()) {
        $envBlock[$kv.Key] = $kv.Value
    }

    $logFile = Join-Path $LogDir "bot_$id.log"

    # Rotation : conserve un historique borne de $LOG_HISTORY_CYCLES cycles de
    # lancement passes (bot_$id.log.1 = precedent, ... .N = plus ancien), au-dela
    # du cycle courant. Chaque decalage est independant : une interruption en
    # plein cycle perd au pire un cran d'historique, sans etat intermediaire
    # casse ni croissance illimitee (le plus ancien est ecrase a chaque tour).
    for ($i = $LOG_HISTORY_CYCLES - 1; $i -ge 1; $i--) {
        $src = "$logFile.$i"
        $dst = "$logFile.$($i + 1)"
        if (Test-Path $src) {
            try {
                Move-Item -Path $src -Destination $dst -Force
            } catch {
                Write-Log "WARN $id - rotation log echouee ($src -> $dst) : $_"
            }
        }
    }
    try {
        if (Test-Path $logFile) {
            Move-Item -Path $logFile -Destination "$logFile.1" -Force
        }
    } catch {
        Write-Log "WARN $id - rotation log echouee : $_"
    }

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName               = $PythonExe
    $psi.Arguments              = "`"$MainScript`""
    # cwd = racine, jamais code\ - cf. note en tete de fichier (renommage code\ par l'update).
    $psi.WorkingDirectory        = $PSScriptRoot
    $psi.UseShellExecute        = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError  = $true
    $psi.CreateNoWindow         = $true

    foreach ($kv in $envBlock.GetEnumerator()) {
        $psi.EnvironmentVariables[$kv.Key] = $kv.Value
    }

    $process = [System.Diagnostics.Process]::Start($psi)

    # Redirection asynchrone des sorties vers le log du bot
    $process.BeginOutputReadLine()
    $process.BeginErrorReadLine()
    Register-ObjectEvent -InputObject $process -EventName "OutputDataReceived" -MessageData $logFile -Action {
        if ($Event.SourceEventArgs.Data) {
            Add-Content -Path $Event.MessageData -Value $Event.SourceEventArgs.Data -Encoding UTF8
        }
    } | Out-Null
    Register-ObjectEvent -InputObject $process -EventName "ErrorDataReceived" -MessageData $logFile -Action {
        if ($Event.SourceEventArgs.Data) {
            Add-Content -Path $Event.MessageData -Value $Event.SourceEventArgs.Data -Encoding UTF8
        }
    } | Out-Null

    # Ecriture du PID + heure de demarrage (ticks) - le couple sert a distinguer ce
    # process precis d'un futur process sans rapport qui recyclerait le meme PID.
    # Le bot ecrit aussi son propre PID via write_pid_file - double securite.
    $pidPath = Get-PidPath $id
    "$($process.Id)|$($process.StartTime.Ticks)" | Out-File -FilePath $pidPath -Encoding ASCII -NoNewline

    Write-Log "START $id - PID=$($process.Id) log=$logFile"
}

# ---------------------------------------------------------------------------
# Init dossiers
# ---------------------------------------------------------------------------

foreach ($dir in @($PidsDir, $LogDir)) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir | Out-Null
    }
}

# ---------------------------------------------------------------------------
# Lecture accounts.json
# ---------------------------------------------------------------------------

if (-not (Test-Path $AccountsFile)) {
    Write-Log "ERREUR - accounts.json introuvable : $AccountsFile"
    exit 1
}

if (-not (Test-Path $PythonExe)) {
    Write-Log "ERREUR - python.exe introuvable : $PythonExe"
    exit 1
}

if (-not (Test-Path $MainScript)) {
    Write-Log "ERREUR - code\main.py introuvable : $MainScript"
    exit 1
}

$raw      = Get-Content -Path $AccountsFile -Raw -Encoding UTF8
$allAccounts = $raw | ConvertFrom-Json
$accounts = @($allAccounts | Where-Object { $_.account_id -eq $AccountId })

if ($accounts.Count -eq 0) {
    Write-Log "ERREUR - aucun compte trouve pour AccountId='$AccountId' dans $AccountsFile"
    exit 1
}

Write-Log "=== launch_all.ps1 demarrage - lancement manuel ponctuel de '$AccountId' ==="

# ---------------------------------------------------------------------------
# Boucle principale
# ---------------------------------------------------------------------------

foreach ($account in $accounts) {
    $bot = @{}
    $account.PSObject.Properties | ForEach-Object { $bot[$_.Name] = $_.Value }

    $id      = $bot.account_id
    $pidPath = Get-PidPath $id

    # --- Cas 0 : service NSSM deja installe pour ce compte ---
    # Le parc de production est exploite via NSSM (nssm_setup_bot.ps1) - un lancement
    # manuel ici sur un compte deja couvert par un service creerait un double process
    # sur le meme profil Chrome/proxy.
    if (Test-NssmServiceExists -AccountId $id) {
        Write-Log "ABORT $id - service NSSM surveybot_$id deja installe - lancement manuel refuse (double lancement meme profil/proxy). Utiliser 'nssm start surveybot_$id' ou verifier son statut a la place."
        continue
    }

    # --- Cas 1 : fichier PID present ---
    if (Test-Path $pidPath) {
        $pidRaw = (Get-Content -Path $pidPath -Raw).Trim()
        $parts  = $pidRaw -split '\|'
        $pidInt = 0
        $startTicks = 0L

        if ($parts.Count -eq 2 -and [int]::TryParse($parts[0], [ref]$pidInt) -and $pidInt -gt 0 -and [long]::TryParse($parts[1], [ref]$startTicks)) {
            if (Test-BotProcessAlive -ProcessId $pidInt -ExpectedStartTicks $startTicks) {
                Write-Log "SKIP $id - deja actif (PID=$pidInt)"
                continue
            } else {
                # PID stale ou recycle par un process sans rapport (start time different)
                Write-Log "STALE $id - PID=$pidInt mort ou recycle, nettoyage + relance"
                Remove-Item -Path $pidPath -Force
            }
        } else {
            # Fichier PID corrompu ou ancien format (sans heure de demarrage)
            Write-Log "CORRUPT $id - fichier PID illisible/obsolete, nettoyage + relance"
            Remove-Item -Path $pidPath -Force
        }
    }

    # --- Cas 2 : lancer le bot ---
    try {
        Start-Bot $bot
    } catch {
        Write-Log "ERREUR $id - echec lancement : $_"
    }
}

Write-Log "=== launch_all.ps1 termine ==="