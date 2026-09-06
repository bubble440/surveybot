# wake_scheduler.ps1
# Relance les bots dont le cooldown Postgres est expire (arret EXIT_VOLUNTARY avec cooldown,
# session expiree, objectif journalier atteint, etc.) mais dont le process n'a pas ete
# redemarre automatiquement (NSSM n'est plus dans la boucle - relance via launch_all.ps1,
# cf. Utils/ORCHESTRATION_TRACKING.md section 18).
#
# Complementaire de check_zombie_bots.ps1 :
#   check_zombie_bots.ps1  -> restart les bots vivants mais bloques (zombie heartbeat) ou
#                             reellement arretes suite a un crash/soft_restart
#   wake_scheduler.ps1     -> restart les bots volontairement arretes apres cooldown expire
#
# Regles (inchangees) :
#   - Cooldown lu en base Postgres via le mode CLI --query-cooldown de main.py (python.exe).
#   - EXIT_FATAL (last_exit_code = 3) : jamais relance automatiquement - intervention humaine.
#   - Marqueur pids\bot_<id>.manual_stop present (pose par stop_bot_manual.ps1) : jamais
#     relance automatiquement tant que le bot n'a pas ete redemarre au moins une fois.
#   - Bot deja actif (PID vivant, verifie via pids\bot_<id>.pid) : ignore.
#
# Capture de sortie :
#   Toute la sortie (Write-Output/Write-Warning/erreurs non gerees) est ecrite dans
#   $LogFile (Start-Transcript), en plus de la console si lancee en interactif.
#   Utile car une tache planifiee (Register-ScheduledTask) n'affiche aucune sortie
#   nulle part par defaut : sans ceci, un echec silencieux de la tache est invisible.
#
# Usage :
#   .\wake_scheduler.ps1
#   .\wake_scheduler.ps1 -AccountsFile "D:\surveybot\accounts.json" -PidsDir "D:\surveybot\pids"
#
# Installation en tache planifiee (une seule fois, en tant qu'administrateur) - principal
# INTERACTIVE (compte admin de l'operateur), PAS SYSTEM : un bot relance par ce script doit
# atterrir dans la meme session Windows interactive que les bots demarres au logon
# (compositeur DWM actif, GPU reel), pas en Session 0. Voir set-up.txt pour la commande
# Register-ScheduledTask complete et sa justification.

param(
    [string]$AccountsFile  = "$PSScriptRoot\accounts.json",
    [string]$PidsDir       = "C:\surveybot\pids",
    [string]$LaunchAllScript = "$PSScriptRoot\launch_all.ps1",
    [string]$PythonExe     = "$PSScriptRoot\venv\Scripts\python.exe",
    [string]$MainScript    = "$PSScriptRoot\code\main.py",
    [string]$LogFile       = "C:\surveybot\logs\wake_scheduler_task.log"
)

$MAX_ACCOUNTS = 200   # garde-fou boucle : abort si accounts.json est anormalement grand

# -- Helpers PID (cible launch_all.ps1, plus NSSM) ------------------------------
# Dupliques ici plutot que partages, pour garder ce script autonome - meme convention
# deja etablie dans ce projet (cf. stop_bot.ps1, check_zombie_bots.ps1).

function Test-BotProcessAlive {
    param([int]$ProcessId, [long]$ExpectedStartTicks)
    try {
        $p = Get-Process -Id $ProcessId -ErrorAction Stop
        return ($p.StartTime.Ticks -eq $ExpectedStartTicks)
    } catch {
        return $false
    }
}

function Test-BotAliveByAccountId {
    param([string]$AccountId)
    $pidPath = Join-Path $PidsDir "bot_$AccountId.pid"
    if (-not (Test-Path $pidPath)) { return $false }
    $raw = Get-Content -Path $pidPath -Raw -ErrorAction SilentlyContinue
    if (-not $raw) { return $false }
    $parts = $raw.Trim() -split '\|'
    $pidInt = 0
    $startTicks = 0L
    if ($parts.Count -ne 2 -or -not [int]::TryParse($parts[0], [ref]$pidInt) -or $pidInt -le 0 -or -not [long]::TryParse($parts[1], [ref]$startTicks)) {
        return $false
    }
    return Test-BotProcessAlive -ProcessId $pidInt -ExpectedStartTicks $startTicks
}

function Invoke-LaunchAll {
    # Sous-process powershell.exe dedie (pas un dot-source/appel en processus) : evite
    # tout risque de ré-execution du bloc Add-Type de launch_all.ps1 dans le meme
    # AppDomain si plusieurs bots sont relances dans le meme passage de ce script.
    param([string]$AccountId)
    try {
        $result = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $LaunchAllScript -AccountId $AccountId 2>&1
        Write-Output "[WAKE] launch_all.ps1 -AccountId $AccountId -> $result"
    } catch {
        Write-Warning "[WAKE] echec appel launch_all.ps1 pour $AccountId : $_"
    }
}

# -- Capture de sortie (transcript) --------------------------------------------
$_logDir = Split-Path -Path $LogFile -Parent
if ($_logDir -and -not (Test-Path $_logDir)) {
    New-Item -ItemType Directory -Path $_logDir -Force | Out-Null
}
try {
    Start-Transcript -Path $LogFile -Append -ErrorAction Stop | Out-Null
} catch {
    Write-Warning "[WAKE] Impossible de demarrer le transcript ($LogFile) : $_"
}
# NB : Stop-Transcript est appele en toute fin de script (chemin nominal). En cas
# d'"exit" premature plus haut (accounts.json absent, etc.), le transcript n'est
# pas explicitement ferme mais son contenu est deja ecrit sur disque au fil de
# l'eau - suffisant pour le diagnostic, sans restructurer les sorties existantes.

# -- Lecture accounts.json -----------------------------------------------------

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
    Write-Output "[WAKE] Aucun account_id dans $AccountsFile - rien a verifier."
    exit 0
}

if ($accountIds.Count -gt $MAX_ACCOUNTS) {
    Write-Warning "[WAKE] $($accountIds.Count) comptes > MAX_ACCOUNTS ($MAX_ACCOUNTS) - abort (verifier accounts.json)."
    exit 1
}

Write-Output "[WAKE] $($accountIds.Count) compte(s) a verifier."

# -- Interrogation Postgres via le mode CLI de main.py -------------------------
# python.exe code\main.py --query-cooldown account1 account2 ...
# Retourne un JSON sur stdout ; exit 0 dans tous les cas.

if (-not (Test-Path $PythonExe)) {
    Write-Warning "[WAKE] python.exe introuvable : $PythonExe"
    exit 1
}

if (-not (Test-Path $MainScript)) {
    Write-Warning "[WAKE] code\main.py introuvable : $MainScript"
    exit 1
}

try {
    # Passe tous les account_ids d'un coup : une seule connexion Postgres, sortie rapide.
    $jsonOut = & $PythonExe $MainScript --query-cooldown @accountIds 2>$null
    if (-not $jsonOut) {
        Write-Warning "[WAKE] Aucune sortie du binaire (--query-cooldown) - abandon."
        exit 1
    }
    $cooldownList = $jsonOut | ConvertFrom-Json
} catch {
    Write-Warning "[WAKE] Erreur lors de l'interrogation Postgres : $_"
    exit 1
}

# Indexer par account_id pour acces O(1)
$statusMap = @{}
foreach ($entry in $cooldownList) {
    if ($entry.account_id) {
        $statusMap[[string]$entry.account_id] = $entry
    }
}

# -- Boucle principale ---------------------------------------------------------

$processed = 0
foreach ($accountId in $accountIds) {
    if ($processed -ge $MAX_ACCOUNTS) {
        Write-Warning "[WAKE] Budget MAX_ACCOUNTS atteint - arret de la boucle."
        break
    }
    $processed++

    # - Verification cooldown Postgres -
    $status = $statusMap[$accountId]
    if (-not $status) {
        Write-Warning "[WAKE] bot=$accountId - statut Postgres absent, ignore."
        continue
    }
    if ($status.PSObject.Properties['error'] -and $status.error) {
        Write-Warning "[WAKE] bot=$accountId - erreur Postgres : $($status.error)"
        continue
    }
    if (-not $status.is_expired) {
        Write-Output "[WAKE] bot=$accountId - cooldown actif jusqu'a $($status.cooldown_until_ts), ignore."
        continue
    }

    # - Verification EXIT_FATAL via fichier .state local -
    # last_exit_code = 3 -> intervention humaine requise, ne pas relancer automatiquement.
    $stateFile = Join-Path $PidsDir "bot_$accountId.state"
    if (Test-Path $stateFile) {
        try {
            $localState = Get-Content $stateFile -Raw -ErrorAction Stop | ConvertFrom-Json
            if ($localState.last_exit_code -eq 3) {
                Write-Output "[WAKE] bot=$accountId - EXIT_FATAL (code=3, reason=$($localState.last_exit_reason)) - intervention humaine requise, ignore."
                continue
            }
        } catch {
            Write-Warning "[WAKE] bot=$accountId - impossible de lire $stateFile : $_ - on continue."
        }
    }

    # - Verification arret manuel operateur (marqueur distinct du cooldown) -
    # Pose par stop_bot_manual.ps1 (a utiliser a la place d'un `nssm stop` nu pour
    # un arret destine a durer) ; leve automatiquement au prochain demarrage reel
    # du bot (bot_supervisor.clear_manual_stop_marker, appelee au tout debut de
    # main.py). Necessaire car `nssm stop` seul envoie le meme CTRL_BREAK_EVENT
    # qu'un arret de service Windows ordinaire (redemarrage machine) : le cooldown
    # Postgres seul ne peut donc pas porter cette distinction sans risquer de
    # bloquer tout le parc apres un simple reboot.
    $manualStopFile = Join-Path $PidsDir "bot_$accountId.manual_stop"
    if (Test-Path $manualStopFile) {
        Write-Output "[WAKE] bot=$accountId - arret manuel operateur signale ($manualStopFile) - intervention humaine requise, ignore."
        continue
    }

    # - Verification PID (bot deja actif ?) -
    if (Test-BotAliveByAccountId -AccountId $accountId) {
        Write-Output "[WAKE] bot=$accountId - deja actif (PID vivant), ignore."
        continue
    }

    # - Demarrage -
    Write-Output "[WAKE] bot=$accountId - cooldown expire ($($status.cooldown_until_ts)) + arrete -> launch_all.ps1"
    Invoke-LaunchAll -AccountId $accountId
}

Write-Output "[WAKE] Termine - $processed compte(s) traite(s)."

try { Stop-Transcript | Out-Null } catch { }