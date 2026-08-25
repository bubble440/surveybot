# stop_bot_manual.ps1
# Arret manuel operateur d'un bot NSSM, destine a rester arrete durablement (jusqu'a
# une reprise explicite via `nssm start`), sans etre relance par wake_scheduler.ps1
# dans les minutes qui suivent.
#
# Pourquoi ce script plutot qu'un simple `nssm stop` :
#   `nssm stop` envoie un CTRL_BREAK_EVENT strictement identique a celui recu lors
#   d'un arret de service Windows ordinaire (redemarrage machine, Windows Update).
#   launch.py::_make_stop_handler ne peut donc pas distinguer les deux cas depuis
#   l'interieur du process pour decider si le cooldown Postgres doit rester ouvert
#   (redemarrage machine : le parc doit reprendre normalement au reboot) ou ferme
#   durablement (arret operateur : ne doit PAS reprendre tout seul). Ce script pose
#   donc, avant d'appeler `nssm stop` lui-meme, un marqueur explicite distinct du
#   cooldown (pids\bot_<id>.manual_stop), lu uniquement par wake_scheduler.ps1.
#
# Marqueur leve automatiquement : au tout prochain demarrage reel du bot (`nssm
# start` manuel, ou redemarrage machine qui relance le service NSSM) -
# bot_supervisor.clear_manual_stop_marker(), appelee tout au debut de main.py.
#
# Hors perimetre : ce script cible exclusivement un bot gere par un service NSSM
# (surveybot_<id>). Pour un bot lance manuellement via launch_all.ps1 (hors NSSM),
# voir stop_bot.ps1 (mecanisme distinct, base sur CTRL_BREAK_EVENT cible via PID).
#
# Usage :
#   .\stop_bot_manual.ps1 -AccountId "topsurveys_bot_001"
#   .\stop_bot_manual.ps1 -AccountId "topsurveys_bot_001" -PidsDir "D:\surveybot\pids"

param(
    [Parameter(Mandatory = $true)]
    [string]$AccountId,
    [string]$PidsDir       = "C:\surveybot\pids",
    [string]$ServicePrefix = "surveybot_"
)

if (-not (Test-Path $PidsDir)) {
    New-Item -ItemType Directory -Path $PidsDir -Force | Out-Null
}

$markerFile = Join-Path $PidsDir "bot_$AccountId.manual_stop"
try {
    Set-Content -Path $markerFile -Value "stopped_by_operator_at=$(Get-Date -Format o)" -Encoding UTF8 -ErrorAction Stop
    Write-Output "[STOP_MANUAL] bot=$AccountId - marqueur pose : $markerFile"
} catch {
    Write-Warning "[STOP_MANUAL] bot=$AccountId - impossible de poser le marqueur ($markerFile) : $_"
    exit 1
}

$svcName = "$ServicePrefix$AccountId"
Write-Output "[STOP_MANUAL] bot=$AccountId - nssm stop $svcName"
try {
    $result = & nssm stop $svcName 2>&1
    $nssmExitCode = $LASTEXITCODE
    Write-Output "[STOP_MANUAL] NSSM stop $svcName -> $result"
} catch {
    Write-Warning "[STOP_MANUAL] bot=$AccountId - impossible d'arreter $svcName : $_"
    exit 1
}

if ($nssmExitCode -ne 0) {
    Write-Warning "[STOP_MANUAL] bot=$AccountId - ECHEC : nssm stop $svcName a retourne le code $nssmExitCode (pas de service NSSM installe pour ce compte, ou inaccessible). Le marqueur manual_stop a ete pose mais AUCUN arret reel n'a eu lieu. Si ce bot tourne via launch_all.ps1 (process isole, hors NSSM), completer avec : .\stop_bot.ps1 -AccountId `"$AccountId`""
    exit 2
}
