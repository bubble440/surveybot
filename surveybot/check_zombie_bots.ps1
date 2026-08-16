# check_zombie_bots.ps1
# Detecte les bots zombies (heartbeat perime, process vivant mais bloque) ET les bots
# reellement arretes (process mort, dernier code de sortie = crash/soft_restart) puis
# les relance - cible des process bruts (PID, lances par launch_all.ps1), plus des
# services NSSM. Cf. Utils/ORCHESTRATION_TRACKING.md section 18.
#
# Logique de DECISION inchangee par rapport a la version NSSM (cooldown/EXIT_FATAL/
# manual_stop non touches ici - seul le MECANISME de verification d'etat et de
# redemarrage change de cible) :
#   - last_exit_code 0 (EXIT_VOLUNTARY) ou 3 (EXIT_FATAL) : jamais relance ici.
#   - Marqueur pids\bot_<id>.manual_stop present (pose par stop_bot_manual.ps1) : jamais
#     relance ici (meme regle que wake_scheduler.ps1).
#   - Sinon, deux cas distincts :
#     1) Process reellement arrete (PID absent/mort/recycle) : plus rien ne le
#        supervise (NSSM ne gere plus ce chemin) -> relance directe via launch_all.ps1.
#     2) Process vivant mais heartbeat perime (zombie, > HeartbeatTimeoutSec) : arret
#        cible via stop_bot.ps1 (CTRL_BREAK, meme mecanisme que `nssm stop`), attente
#        bornee de confirmation, kill force en dernier recours, puis relance via
#        launch_all.ps1.
#
# Usage :
#   .\check_zombie_bots.ps1
#   .\check_zombie_bots.ps1 -HeartbeatTimeoutSec 180 -PidsDir "D:\surveybot\pids"
#
# Installation en tache planifiee (une seule fois, en tant qu'administrateur) - principal
# INTERACTIVE (compte admin de l'operateur), PAS SYSTEM : un bot relance par ce script
# doit atterrir dans la meme session Windows interactive que les bots demarres au logon
# (compositeur DWM actif, GPU reel), pas en Session 0. Voir set-up.txt pour la commande
# Register-ScheduledTask complete et sa justification.

param(
    [int]$HeartbeatTimeoutSec = 300,          # seuil zombie : 5 minutes sans heartbeat
    [string]$PidsDir          = "C:\surveybot\pids",   # dossier des fichiers .state/.pid
    [string]$LaunchAllScript  = "$PSScriptRoot\launch_all.ps1",
    [string]$StopBotScript    = "$PSScriptRoot\stop_bot.ps1"
)

# Garde-fou boucle : abandon si le nombre de fichiers .state est anormalement grand
# (meme convention que wake_scheduler.ps1/launch_all.ps1).
$MAX_ACCOUNTS = 200

# Budget d'attente apres stop_bot.ps1 avant kill force (chemin zombie uniquement) -
# coherent avec l'ancien AppStopMethodConsole NSSM (30s), marge de 5s en plus.
$STOP_CONFIRM_MAX_SEC  = 35
$STOP_CONFIRM_POLL_SEC = 2

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Test-BotProcessAlive {
    # Meme verification que launch_all.ps1/stop_bot.ps1 (dupliquee ici plutot que
    # partagee, pour garder ce script autonome - meme convention deja etablie dans
    # ce projet, cf. stop_bot.ps1).
    param(
        [int]$ProcessId,
        [long]$ExpectedStartTicks
    )
    try {
        $p = Get-Process -Id $ProcessId -ErrorAction Stop
        return ($p.StartTime.Ticks -eq $ExpectedStartTicks)
    } catch {
        return $false
    }
}

function Get-BotPidInfo {
    # Retourne $null si le fichier PID est absent, illisible ou de format inattendu ;
    # sinon un objet {ProcessId, StartTicks}.
    param([string]$AccountId)
    $pidPath = Join-Path $PidsDir "bot_$AccountId.pid"
    if (-not (Test-Path $pidPath)) { return $null }
    $raw = Get-Content -Path $pidPath -Raw -ErrorAction SilentlyContinue
    if (-not $raw) { return $null }
    $parts = $raw.Trim() -split '\|'
    $pidInt = 0
    $startTicks = 0L
    if ($parts.Count -eq 2 -and [int]::TryParse($parts[0], [ref]$pidInt) -and $pidInt -gt 0 -and [long]::TryParse($parts[1], [ref]$startTicks)) {
        return [PSCustomObject]@{ ProcessId = $pidInt; StartTicks = $startTicks }
    }
    return $null
}

function Test-BotAliveByAccountId {
    param([string]$AccountId)
    $info = Get-BotPidInfo -AccountId $AccountId
    if (-not $info) { return $false }
    return Test-BotProcessAlive -ProcessId $info.ProcessId -ExpectedStartTicks $info.StartTicks
}

function Invoke-LaunchAll {
    # Sous-process powershell.exe dedie (pas un dot-source/appel en processus) : evite
    # tout risque de ré-execution du bloc Add-Type de launch_all.ps1 dans le meme
    # AppDomain si plusieurs bots sont relances dans le meme passage de ce script.
    param([string]$AccountId)
    try {
        $result = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $LaunchAllScript -AccountId $AccountId 2>&1
        Write-Output "[ZOMBIE_CHECK] launch_all.ps1 -AccountId $AccountId -> $result"
    } catch {
        Write-Warning "[ZOMBIE_CHECK] echec appel launch_all.ps1 pour $AccountId : $_"
    }
}

function Invoke-StopBot {
    param([string]$AccountId)
    try {
        $result = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $StopBotScript -AccountId $AccountId 2>&1
        Write-Output "[ZOMBIE_CHECK] stop_bot.ps1 -AccountId $AccountId -> $result"
    } catch {
        Write-Warning "[ZOMBIE_CHECK] echec appel stop_bot.ps1 pour $AccountId : $_"
    }
}

function Wait-ForBotStop {
    # Attente bornee (budget explicite) que le process cible ne soit plus vivant.
    param([string]$AccountId, [int]$MaxSec, [int]$PollSec)
    $elapsed = 0
    while ($elapsed -lt $MaxSec) {
        if (-not (Test-BotAliveByAccountId -AccountId $AccountId)) { return $true }
        Start-Sleep -Seconds $PollSec
        $elapsed += $PollSec
    }
    return (-not (Test-BotAliveByAccountId -AccountId $AccountId))
}

# ---------------------------------------------------------------------------
# Boucle principale
# ---------------------------------------------------------------------------

$stateFiles = @(Get-ChildItem -Path $PidsDir -Filter "bot_*.state" -ErrorAction SilentlyContinue)

if ($stateFiles.Count -eq 0) {
    Write-Output "[ZOMBIE_CHECK] Aucun fichier .state dans $PidsDir — rien à vérifier."
    exit 0
}

if ($stateFiles.Count -gt $MAX_ACCOUNTS) {
    Write-Warning "[ZOMBIE_CHECK] $($stateFiles.Count) fichiers .state > MAX_ACCOUNTS ($MAX_ACCOUNTS) - abort (verifier $PidsDir)."
    exit 1
}

$now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()

foreach ($file in $stateFiles) {
    try {
        $raw   = Get-Content $file.FullName -Raw -ErrorAction Stop
        $state = $raw | ConvertFrom-Json
    } catch {
        Write-Warning "[ZOMBIE_CHECK] Impossible de lire $($file.Name) : $_"
        continue
    }

    $accountId  = $state.account_id
    $lastHb     = $state.last_heartbeat_ts
    $lastExit   = $state.last_exit_code
    $lastReason = $state.last_exit_reason

    if (-not $accountId) {
        Write-Warning "[ZOMBIE_CHECK] $($file.Name) : account_id manquant, ignoré."
        continue
    }

    # Ne pas alerter si le bot est arrêté volontairement (last_exit_code = 0 ou 3) -
    # ni zombie ni "réellement arrêté" au sens de ce script, c'est un arrêt normal.
    if ($lastExit -eq 0 -or $lastExit -eq 3) {
        Write-Output "[ZOMBIE_CHECK] bot=$accountId arrêt volontaire/fatal (exit=$lastExit, reason=$lastReason) — ignoré."
        continue
    }

    # Arrêt manuel opérateur (même marqueur/règle que wake_scheduler.ps1) : jamais
    # relancé automatiquement tant que le bot n'a pas redémarré au moins une fois.
    $manualStopFile = Join-Path $PidsDir "bot_$accountId.manual_stop"
    if (Test-Path $manualStopFile) {
        Write-Output "[ZOMBIE_CHECK] bot=$accountId - arrêt manuel opérateur signalé ($manualStopFile) - ignoré."
        continue
    }

    # --- Bot réellement arrêté (pas seulement zombie) ---
    # NSSM ne supervise plus ce chemin : sans ce check, un bot mort suite à un
    # EXIT_CRASH/EXIT_SOFT_RESTART (last_exit_code déjà filtré à 1/2/inconnu ici,
    # 0/3 exclus plus haut) resterait arrêté indéfiniment.
    if (-not (Test-BotAliveByAccountId -AccountId $accountId)) {
        Write-Output "[ZOMBIE_CHECK] bot=$accountId ARRÊTÉ (exit=$lastExit, reason=$lastReason) — relance via launch_all.ps1"
        Invoke-LaunchAll -AccountId $accountId
        continue
    }

    if (-not $lastHb) {
        Write-Warning "[ZOMBIE_CHECK] bot=$accountId : last_heartbeat_ts manquant, ignoré."
        continue
    }

    $ageSeconds = $now - [long]$lastHb

    if ($ageSeconds -gt $HeartbeatTimeoutSec) {
        Write-Output "[ZOMBIE_CHECK] bot=$accountId ZOMBIE détecté : heartbeat_age=${ageSeconds}s > ${HeartbeatTimeoutSec}s → stop puis relance"
        Invoke-StopBot -AccountId $accountId
        if (-not (Wait-ForBotStop -AccountId $accountId -MaxSec $STOP_CONFIRM_MAX_SEC -PollSec $STOP_CONFIRM_POLL_SEC)) {
            $info = Get-BotPidInfo -AccountId $accountId
            if ($info -and (Test-BotProcessAlive -ProcessId $info.ProcessId -ExpectedStartTicks $info.StartTicks)) {
                Write-Warning "[ZOMBIE_CHECK] bot=$accountId toujours actif après ${STOP_CONFIRM_MAX_SEC}s — kill forcé (PID=$($info.ProcessId))"
                try {
                    Stop-Process -Id $info.ProcessId -Force -ErrorAction Stop
                } catch {
                    Write-Warning "[ZOMBIE_CHECK] échec kill forcé PID=$($info.ProcessId) : $_"
                }
            }
        }
        Invoke-LaunchAll -AccountId $accountId
    } else {
        Write-Output "[ZOMBIE_CHECK] bot=$accountId OK (heartbeat_age=${ageSeconds}s)"
    }
}
