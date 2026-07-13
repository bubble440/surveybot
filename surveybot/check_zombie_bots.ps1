# check_zombie_bots.ps1
# Détecte les bots zombies (heartbeat périmé) et les redémarre via NSSM.
#
# Usage :
#   .\check_zombie_bots.ps1
#   .\check_zombie_bots.ps1 -HeartbeatTimeoutSec 180 -PidsDir "D:\surveybot\pids"
#
# Installation en tâche planifiée (une seule fois, en tant qu'administrateur) :
#   $action  = New-ScheduledTaskAction -Execute "powershell.exe" `
#                -Argument "-NonInteractive -File C:\surveybot\check_zombie_bots.ps1"
#   $trigger = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 3) `
#                -Once -At (Get-Date)
#   $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
#   Register-ScheduledTask -TaskName "SurveyBot_ZombieCheck" -Action $action `
#     -Trigger $trigger -Settings $settings -RunLevel Highest -Force

param(
    [int]$HeartbeatTimeoutSec = 300,          # seuil zombie : 5 minutes sans heartbeat
    [string]$PidsDir = "C:\surveybot\pids",   # dossier des fichiers .state
    [string]$ServicePrefix = "surveybot_"     # préfixe des services NSSM : surveybot_<account_id>
)

$now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$stateFiles = Get-ChildItem -Path $PidsDir -Filter "bot_*.state" -ErrorAction SilentlyContinue

if (-not $stateFiles) {
    Write-Output "[ZOMBIE_CHECK] Aucun fichier .state dans $PidsDir — rien à vérifier."
    exit 0
}

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

    # Ne pas alerter si le bot est arrêté volontairement (last_exit_code = 0 ou 3)
    # et qu'il n'y a pas eu de heartbeat récent (il est juste à l'arrêt).
    if ($lastExit -eq 0 -or $lastExit -eq 3) {
        Write-Output "[ZOMBIE_CHECK] bot=$accountId arrêt volontaire/fatal (exit=$lastExit, reason=$lastReason) — ignoré."
        continue
    }

    if (-not $lastHb) {
        Write-Warning "[ZOMBIE_CHECK] bot=$accountId : last_heartbeat_ts manquant, ignoré."
        continue
    }

    $ageSeconds = $now - [long]$lastHb

    if ($ageSeconds -gt $HeartbeatTimeoutSec) {
        Write-Output "[ZOMBIE_CHECK] bot=$accountId ZOMBIE détecté : heartbeat_age=${ageSeconds}s > ${HeartbeatTimeoutSec}s → restart NSSM"
        $svcName = "$ServicePrefix$accountId"
        try {
            $result = & nssm restart $svcName 2>&1
            Write-Output "[ZOMBIE_CHECK] NSSM restart envoyé : $svcName — $result"
        } catch {
            Write-Warning "[ZOMBIE_CHECK] Impossible de restart $svcName : $_"
        }
    } else {
        Write-Output "[ZOMBIE_CHECK] bot=$accountId OK (heartbeat_age=${ageSeconds}s)"
    }
}
