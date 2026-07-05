# launch_all.ps1
# Lance uniquement les bots qui ne tournent pas deja sur cette machine.
# A placer dans C:\surveybot\ et planifier via le Planificateur de taches Windows.
#
# Prerequis :
#   - accounts.json dans le meme dossier que ce script.
#   - surveybot.exe compile avec LICENSE_KEY et DATABASE_URL embarquees.
#   - Dossiers profiles\ crees manuellement (un par bot).
#   - Dossier pids\ cree automatiquement au premier lancement.

param(
    [string]$AccountsFile = "$PSScriptRoot\accounts.json",
    [string]$ExePath      = "$PSScriptRoot\surveybot.exe",
    [string]$PidsDir      = "$PSScriptRoot\pids",
    [string]$LogDir       = "$PSScriptRoot\logs"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

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

function Test-ProcessAlive {
    param([int]$ProcessId)
    # tasklist ne leve pas d'exception si le PID est absent — on parse la sortie
    $result = & tasklist /FI "PID eq $ProcessId" /NH 2>$null
    return ($result -match "\b$ProcessId\b")
}

function Start-Bot {
    param([hashtable]$Bot)

    $id          = $Bot.account_id
    $profileDir  = $Bot.profile_dir

    # Verification du dossier profil Chrome
    if (-not (Test-Path $profileDir)) {
        Write-Log "SKIP $id — profile_dir introuvable : $profileDir"
        return
    }

    # Variables d'environnement passees au processus
    # LICENSE_KEY et DATABASE_URL sont embarquees dans le compile — absentes ici.
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

    # Rotation : archive le log du cycle precedent avant d'en demarrer un nouveau.
    # On conserve toujours le cycle courant + le cycle precedent (.old) — suffisant
    # pour diagnostiquer un crash sans laisser le fichier grossir indefiniment.
    $logOld = "$logFile.old"
    try {
        if (Test-Path $logFile) {
            Move-Item -Path $logFile -Destination $logOld -Force
        }
    } catch {
        Write-Log "WARN $id — rotation log echouee : $_"
    }

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName               = $ExePath
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
    Register-ObjectEvent -InputObject $process -EventName "OutputDataReceived" -Action {
        if ($Event.SourceEventArgs.Data) {
            Add-Content -Path $logFile -Value $Event.SourceEventArgs.Data -Encoding UTF8
        }
    } | Out-Null
    Register-ObjectEvent -InputObject $process -EventName "ErrorDataReceived" -Action {
        if ($Event.SourceEventArgs.Data) {
            Add-Content -Path $logFile -Value $Event.SourceEventArgs.Data -Encoding UTF8
        }
    } | Out-Null

    # Ecriture du PID (le bot ecrit aussi le sien via write_pid_file — double securite)
    $pidPath = Get-PidPath $id
    $process.Id | Out-File -FilePath $pidPath -Encoding ASCII -NoNewline

    Write-Log "START $id — PID=$($process.Id) log=$logFile"
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
    Write-Log "ERREUR — accounts.json introuvable : $AccountsFile"
    exit 1
}

if (-not (Test-Path $ExePath)) {
    Write-Log "ERREUR — surveybot.exe introuvable : $ExePath"
    exit 1
}

$raw      = Get-Content -Path $AccountsFile -Raw -Encoding UTF8
$accounts = $raw | ConvertFrom-Json

Write-Log "=== launch_all.ps1 demarrage — $($accounts.Count) compte(s) configure(s) ==="

# ---------------------------------------------------------------------------
# Boucle principale
# ---------------------------------------------------------------------------

foreach ($account in $accounts) {
    $bot = @{}
    $account.PSObject.Properties | ForEach-Object { $bot[$_.Name] = $_.Value }

    $id      = $bot.account_id
    $pidPath = Get-PidPath $id

    # --- Cas 1 : fichier PID present ---
    if (Test-Path $pidPath) {
        $pidRaw = (Get-Content -Path $pidPath -Raw).Trim()
        $pidInt = 0

        if ([int]::TryParse($pidRaw, [ref]$pidInt) -and $pidInt -gt 0) {
            if (Test-ProcessAlive $pidInt) {
                Write-Log "SKIP $id — deja actif (PID=$pidInt)"
                continue
            } else {
                # PID stale : processus mort sans avoir supprime son PID
                Write-Log "STALE $id — PID=$pidInt mort, nettoyage + relance"
                Remove-Item -Path $pidPath -Force
            }
        } else {
            # Fichier PID corrompu
            Write-Log "CORRUPT $id — fichier PID illisible, nettoyage + relance"
            Remove-Item -Path $pidPath -Force
        }
    }

    # --- Cas 2 : lancer le bot ---
    try {
        Start-Bot $bot
    } catch {
        Write-Log "ERREUR $id — echec lancement : $_"
    }
}

Write-Log "=== launch_all.ps1 termine ==="