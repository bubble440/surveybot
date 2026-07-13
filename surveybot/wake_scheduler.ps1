# wake_scheduler.ps1
# Relance les bots dont le cooldown Postgres est expiré (arrêt EXIT_VOLUNTARY avec cooldown,
# session expirée, objectif journalier atteint, etc.) mais que NSSM n'a pas redémarrés
# automatiquement (AppExit 0 Exit).
#
# Complémentaire de check_zombie_bots.ps1 :
#   check_zombie_bots.ps1  → restart les bots vivants mais bloqués (zombie heartbeat)
#   wake_scheduler.ps1     → restart les bots volontairement arrêtés après cooldown expiré
#
# Règles :
#   - Cooldown lu en base Postgres (via State\query_cooldown_status.py).
#   - EXIT_FATAL (last_exit_code = 3) : jamais relancé automatiquement — intervention humaine.
#   - Service NSSM déjà en cours d'exécution : ignoré.
#
# Usage :
#   .\wake_scheduler.ps1
#   .\wake_scheduler.ps1 -AccountsFile "D:\surveybot\accounts.json" -PidsDir "D:\surveybot\pids"
#
# Installation en tâche planifiée (une seule fois, en tant qu'administrateur) :
#   $action  = New-ScheduledTaskAction -Execute "powershell.exe" `
#                -Argument "-NonInteractive -File C:\surveybot\wake_scheduler.ps1"
#   $trigger = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 5) `
#                -Once -At (Get-Date)
#   $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 4)
#   Register-ScheduledTask -TaskName "SurveyBot_WakeScheduler" -Action $action `
#     -Trigger $trigger -Settings $settings -RunLevel Highest -Force

param(
    [string]$AccountsFile  = "$PSScriptRoot\accounts.json",
    [string]$PidsDir       = "C:\surveybot\pids",
    [string]$ServicePrefix = "surveybot_",
    [string]$PythonExe     = "C:\projects\Surveys\surveybot\.venv\Scripts\python.exe",
    [string]$ProjectDir    = "C:\projects\Surveys\surveybot"
)

$MAX_ACCOUNTS = 200   # garde-fou boucle : abort si accounts.json est anormalement grand

# ── Lecture accounts.json ─────────────────────────────────────────────────────

if (-not (Test-Path $AccountsFile)) {
    Write-Output "[WAKE] accounts.json introuvable : $AccountsFile"
    exit 1
}

try {
    $raw      = Get-Content -Path $AccountsFile -Raw -Encoding UTF8 -ErrorAction Stop
    $accounts = $raw | ConvertFrom-Json
} catch {
    Write-Warning "[WAKE] Impossible de lire $AccountsFile : $_"
    exit 1
}

$accountIds = [System.Collections.Generic.List[string]]::new()
foreach ($acc in $accounts) {
    if ($acc.account_id) {
        $accountIds.Add([string]$acc.account_id)
    }
}

if ($accountIds.Count -eq 0) {
    Write-Output "[WAKE] Aucun account_id dans $AccountsFile — rien à vérifier."
    exit 0
}

if ($accountIds.Count -gt $MAX_ACCOUNTS) {
    Write-Warning "[WAKE] $($accountIds.Count) comptes > MAX_ACCOUNTS ($MAX_ACCOUNTS) — abort (vérifier accounts.json)."
    exit 1
}

Write-Output "[WAKE] $($accountIds.Count) compte(s) à vérifier."

# ── Interrogation Postgres via point d'entrée Python ─────────────────────────

if (-not (Test-Path $PythonExe)) {
    Write-Warning "[WAKE] Python introuvable : $PythonExe"
    exit 1
}

$queryScript = Join-Path $ProjectDir "State\query_cooldown_status.py"
if (-not (Test-Path $queryScript)) {
    Write-Warning "[WAKE] Script de requête introuvable : $queryScript"
    exit 1
}

try {
    # Passe tous les account_ids d'un coup pour une seule connexion Postgres
    $jsonOut = & $PythonExe $queryScript @accountIds 2>$null
    if (-not $jsonOut) {
        Write-Warning "[WAKE] Aucune sortie du script Python — abandon."
        exit 1
    }
    $cooldownList = $jsonOut | ConvertFrom-Json
} catch {
    Write-Warning "[WAKE] Erreur lors de l'interrogation Postgres : $_"
    exit 1
}

# Indexer par account_id pour accès O(1)
$statusMap = @{}
foreach ($entry in $cooldownList) {
    if ($entry.account_id) {
        $statusMap[[string]$entry.account_id] = $entry
    }
}

# ── Boucle principale ─────────────────────────────────────────────────────────

$processed = 0
foreach ($accountId in $accountIds) {
    if ($processed -ge $MAX_ACCOUNTS) {
        Write-Warning "[WAKE] Budget MAX_ACCOUNTS atteint — arrêt de la boucle."
        break
    }
    $processed++

    # — Vérification cooldown Postgres —
    $status = $statusMap[$accountId]
    if (-not $status) {
        Write-Warning "[WAKE] bot=$accountId — statut Postgres absent, ignoré."
        continue
    }
    if ($status.PSObject.Properties['error'] -and $status.error) {
        Write-Warning "[WAKE] bot=$accountId — erreur Postgres : $($status.error)"
        continue
    }
    if (-not $status.is_expired) {
        Write-Output "[WAKE] bot=$accountId — cooldown actif jusqu'à $($status.cooldown_until_ts), ignoré."
        continue
    }

    # — Vérification EXIT_FATAL via fichier .state local —
    # last_exit_code = 3 → intervention humaine requise, ne pas relancer.
    $stateFile = Join-Path $PidsDir "bot_$accountId.state"
    if (Test-Path $stateFile) {
        try {
            $localState = Get-Content $stateFile -Raw -ErrorAction Stop | ConvertFrom-Json
            if ($localState.last_exit_code -eq 3) {
                Write-Output "[WAKE] bot=$accountId — EXIT_FATAL (code=3, reason=$($localState.last_exit_reason)) — intervention humaine requise, ignoré."
                continue
            }
        } catch {
            Write-Warning "[WAKE] bot=$accountId — impossible de lire $stateFile : $_ — on continue."
        }
    }

    # — Vérification statut NSSM —
    $svcName   = "$ServicePrefix$accountId"
    $svcStatus = & nssm status $svcName 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "[WAKE] bot=$accountId — service $svcName introuvable (nssm code=$LASTEXITCODE), ignoré."
        continue
    }
    if ($svcStatus -match "^SERVICE_RUNNING") {
        Write-Output "[WAKE] bot=$accountId — service déjà actif, ignoré."
        continue
    }

    # — Démarrage —
    Write-Output "[WAKE] bot=$accountId — cooldown expiré ($($status.cooldown_until_ts)) + service arrêté → nssm start $svcName"
    try {
        $result = & nssm start $svcName 2>&1
        Write-Output "[WAKE] NSSM start $svcName → $result"
    } catch {
        Write-Warning "[WAKE] Impossible de démarrer $svcName : $_"
    }
}

Write-Output "[WAKE] Terminé — $processed compte(s) traité(s)."
