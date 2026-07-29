# stop_bot.ps1
# Arret cible et isole d'UN SEUL bot lance manuellement via launch_all.ps1 (hors
# supervision NSSM - cf. Utils/ORCHESTRATION_TRACKING.md section 9). Envoie un
# CTRL_BREAK_EVENT au groupe de processus du bot cible : c'est exactement le meme
# signal que celui envoye par `nssm stop` en production (methode Console), capte par
# install_sigint_handler/_make_stop_handler dans launch.py, qui declenche la sequence
# de fermeture propre (liberation du slot Postgres, record_exit EXIT_VOLUNTARY, arret
# du heartbeat, suppression du fichier PID, fermeture Playwright/Chrome).
#
# Ce script ne fonctionne QUE sur un bot lance via launch_all.ps1 (qui isole chaque
# bot dans son propre groupe de processus via CREATE_NEW_PROCESS_GROUP - cf. sa note
# d'isolation en tete de fichier et SurveyBotIsolatedLauncher). Sans cette isolation
# a la creation, il serait impossible de cibler un seul bot sans risquer d'envoyer le
# signal a d'autres process partageant la meme console (autre bot manuel, le lanceur
# PowerShell lui-meme).
#
# Ne concerne PAS les bots geres par NSSM : utiliser `nssm stop surveybot_<id>` pour
# ceux-la (chemin de production, deja valide, non modifie par ce script).
#
# Usage :
#   .\stop_bot.ps1 -AccountId "bot1"
#
# Prerequis : pids\bot_<AccountId>.pid, ecrit par launch_all.ps1 au lancement, format
# "PID|StartTicks" (StartTicks = heure de demarrage du process, pour detecter un PID
# recycle par un process sans rapport apres la fin du bot).
#
# Comportement en cas de doute : si le fichier PID est absent, illisible, ou si le
# process qu'il designe n'est plus celui attendu (PID recycle) - abandon immediat
# avec log clair, AUCUN signal envoye. Ce script ne doit jamais pouvoir atteindre un
# process autre que celui explicitement identifie par account_id.

param(
    [Parameter(Mandatory = $true)]
    [string]$AccountId,
    [string]$PidsDir = "$PSScriptRoot\pids",
    [string]$LogDir  = "$PSScriptRoot\logs"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Nombre de verifications post-signal (500 ms chacune) avant d'abandonner l'attente de
# confirmation - purement informatif, n'affecte pas le resultat du signal deja envoye.
$CONFIRM_POLL_MAX = 10

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Write-Log {
    param([string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $Message"
    # Ce process (stop_bot.ps1) n'appelle plus FreeConsole/AttachConsole lui-meme
    # (cf. Invoke-CtrlBreakInChildProcess) - Write-Host reste fiable ici. Garde le
    # try/catch par prudence (aucun cout).
    try { Write-Host $line } catch {}
    try {
        Add-Content -Path "$LogDir\stop_bot.log" -Value $line -Encoding UTF8
    } catch {}
}

function Test-BotProcessAlive {
    # Meme verification que launch_all.ps1::Test-BotProcessAlive (duplique ici plutot
    # que partage, pour garder ce script autonome et ne pas modifier launch_all.ps1
    # au-dela de son propre correctif d'isolation).
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

# Delai max (ms) accorde au sous-process jetable pour envoyer le signal et se
# terminer. Budget borne : au-dela, on le tue de force et on abandonne avec un log
# clair plutot que de bloquer stop_bot.ps1 indefiniment.
$CTRL_BREAK_CHILD_TIMEOUT_MS = 15000

function Invoke-CtrlBreakInChildProcess {
    <#
    Envoie CTRL_BREAK_EVENT au PID cible, depuis un process powershell.exe separe et
    jetable, plutot que directement dans CE process (stop_bot.ps1).

    Raison : FreeConsole()/AttachConsole() detachent le PROCESS QUI LES APPELLE de sa
    console courante, et rien ne le reattache jamais a son origine. Si cette sequence
    tournait directement ici, une session PowerShell interactive de l'operateur qui
    invoque ".\stop_bot.ps1" perdrait definitivement son attachement console (plus de
    Write-Host/Read-Host fonctionnels) pour le reste de la session. En isolant
    FreeConsole/AttachConsole/GenerateConsoleCtrlEvent dans un sous-process cree pour
    cette seule operation puis aussitot detruit, seul CE sous-process jetable subit la
    perte d'attachement - jamais l'appelant, quel que soit l'endroit d'ou stop_bot.ps1
    est invoque.
    #>
    param([int]$TargetPid)

    # Meme classe C# et meme logique Win32 que precedemment (FreeConsole/AttachConsole/
    # GenerateConsoleCtrlEvent/FreeConsole cible sur le groupe TargetPid uniquement,
    # jamais la console entiere) - seul le PROCESS qui l'execute change.
    $childScript = @"
Add-Type -Language CSharp -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.ComponentModel;

public static class SurveyBotConsoleCtrl
{
    [DllImport(`"kernel32.dll`", SetLastError = true)]
    private static extern bool FreeConsole();

    [DllImport(`"kernel32.dll`", SetLastError = true)]
    private static extern bool AttachConsole(uint dwProcessId);

    [DllImport(`"kernel32.dll`", SetLastError = true)]
    private static extern bool GenerateConsoleCtrlEvent(uint dwCtrlEvent, uint dwProcessGroupId);

    private const uint CTRL_BREAK_EVENT = 1;

    public static void SendCtrlBreak(int targetPid)
    {
        FreeConsole();
        try
        {
            if (!AttachConsole((uint)targetPid))
                throw new Win32Exception(Marshal.GetLastWin32Error(), `"AttachConsole a echoue pour PID `" + targetPid);
            if (!GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, (uint)targetPid))
                throw new Win32Exception(Marshal.GetLastWin32Error(), `"GenerateConsoleCtrlEvent a echoue pour PID `" + targetPid);
        }
        finally
        {
            FreeConsole();
        }
    }
}
'@
try {
    [SurveyBotConsoleCtrl]::SendCtrlBreak($TargetPid)
    exit 0
} catch {
    Write-Error "`$_"
    exit 1
}
"@

    $bytes   = [System.Text.Encoding]::Unicode.GetBytes($childScript)
    $encoded = [Convert]::ToBase64String($bytes)

    # [System.Diagnostics.Process]::Start direct (PAS la cmdlet Start-Process) : la
    # cmdlet Start-Process, avec -WindowStyle Hidden + redirection vers fichiers, rompt
    # l'heritage de console du sous-process cree (constate empiriquement - le
    # sous-process se retrouve sans console attachable, AttachConsole echoue avec
    # ERROR_INVALID_PARAMETER). Un [System.Diagnostics.Process]::Start avec
    # UseShellExecute=$false / CreateNoWindow=$true preserve cet heritage et est le
    # meme mecanisme deja utilise ailleurs dans ce projet (launch_all.ps1).
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName               = "powershell.exe"
    $psi.Arguments              = "-NoProfile -NonInteractive -EncodedCommand $encoded"
    $psi.UseShellExecute        = $false
    $psi.CreateNoWindow         = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError  = $true

    try {
        $childProc = [System.Diagnostics.Process]::Start($psi)
        # Lecture asynchrone des deux flux avant WaitForExit : evite un deadlock si le
        # sous-process remplit le buffer d'un flux pendant qu'on bloque sur l'autre.
        $outTask = $childProc.StandardOutput.ReadToEndAsync()
        $errTask = $childProc.StandardError.ReadToEndAsync()

        if (-not $childProc.WaitForExit($CTRL_BREAK_CHILD_TIMEOUT_MS)) {
            try { $childProc.Kill() } catch {}
            Write-Log "ERREUR - sous-process d'envoi CTRL_BREAK (PID sous-process=$($childProc.Id)) n'a pas termine sous ${CTRL_BREAK_CHILD_TIMEOUT_MS}ms pour cible PID=$TargetPid - tue de force, abandon."
            return $false
        }

        if ($childProc.ExitCode -ne 0) {
            $outText = $outTask.GetAwaiter().GetResult()
            $errText = $errTask.GetAwaiter().GetResult()
            Write-Log "ERREUR - sous-process d'envoi CTRL_BREAK termine avec exit=$($childProc.ExitCode) pour PID=$TargetPid : $errText$(if ($outText) { " (stdout: $outText)" })"
            return $false
        }
        
        return $true
    } catch {
        Write-Log "ERREUR - impossible de lancer le sous-process d'envoi CTRL_BREAK pour PID=$TargetPid : $_"
        return $false
    }
}

# ---------------------------------------------------------------------------
# Lecture + validation du fichier PID
# ---------------------------------------------------------------------------

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

$pidPath = Join-Path $PidsDir "bot_$AccountId.pid"

if (-not (Test-Path $pidPath)) {
    Write-Log "ABANDON $AccountId - fichier PID introuvable : $pidPath (bot non lance via launch_all.ps1, ou deja arrete) - aucun signal envoye."
    exit 1
}

$raw   = (Get-Content -Path $pidPath -Raw).Trim()
$parts = $raw -split '\|'
$targetPid = 0
$startTicks = 0L

if ($parts.Count -ne 2 -or -not [int]::TryParse($parts[0], [ref]$targetPid) -or $targetPid -le 0 -or -not [long]::TryParse($parts[1], [ref]$startTicks)) {
    Write-Log "ABANDON $AccountId - fichier PID illisible ou format inattendu ('$raw') - aucun signal envoye."
    exit 1
}

if (-not (Test-BotProcessAlive -ProcessId $targetPid -ExpectedStartTicks $startTicks)) {
    Write-Log "ABANDON $AccountId - PID=$targetPid absent ou recycle (heure de demarrage differente de celle attendue) - aucun signal envoye."
    exit 1
}

# ---------------------------------------------------------------------------
# Envoi du signal cible
# ---------------------------------------------------------------------------

Write-Log "STOP $AccountId - envoi CTRL_BREAK_EVENT cible au groupe de processus PID=$targetPid (via sous-process jetable)"

$sent = Invoke-CtrlBreakInChildProcess -TargetPid $targetPid
if (-not $sent) {
    Write-Log "ABANDON $AccountId - signal non confirme envoye a PID=$targetPid (voir erreur ci-dessus)."
    exit 1
}

Write-Log "STOP $AccountId - signal envoye, arret propre en cours cote bot (voir logs\bot_$AccountId.log)"

# ---------------------------------------------------------------------------
# Confirmation best-effort (n'affecte pas le resultat : le signal a deja ete envoye)
# ---------------------------------------------------------------------------

$confirmed = $false
for ($i = 0; $i -lt $CONFIRM_POLL_MAX; $i++) {
    Start-Sleep -Milliseconds 500
    if (-not (Test-BotProcessAlive -ProcessId $targetPid -ExpectedStartTicks $startTicks)) {
        $confirmed = $true
        break
    }
}

if ($confirmed) {
    Write-Log "STOP $AccountId - process PID=$targetPid confirme arrete."
} else {
    Write-Log "STOP $AccountId - process PID=$targetPid toujours actif apres $($CONFIRM_POLL_MAX * 500) ms (fermeture propre peut prendre plus de temps - cf. AppStopMethodConsole 30s cote NSSM pour un ordre de grandeur) - non bloquant, verifier logs\bot_$AccountId.log."
}
