# resume_bot_freeze.ps1
# Pose le marqueur de reprise du mode gel d'observation (FREEZE_ON_TRIGGER=1,
# cf. Management/guards/freeze_gate.py) pour un bot donne.
#
# Contexte : en mode FREEZE_ON_TRIGGER=1, le bot se fige (navigateur laisse
# ouvert, inspectable) a chaque point de declenchement automatique identifie
# (reprise de survey sur retour plateforme, redemarrage applicatif, seuil de
# surveillance RuntimeGuard atteint, fermeture/relance du navigateur en fin de
# cycle) au lieu d'agir dessus automatiquement. Ce script est le seul moyen de
# debloquer un point de gel : contrairement a stop_bot_manual.ps1 (lu par un
# script planifie externe, wake_scheduler.ps1), le marqueur pose ici est lu
# directement par le process Python du bot lui-meme
# (bot_supervisor.consume_freeze_resume_marker, appelee depuis
# Management/guards/freeze_gate.py::freeze_and_wait) - pas de `nssm`/process
# externe implique.
#
# A usage unique : le process supprime ce marqueur des qu'il le detecte, avant
# de reprendre - un marqueur laisse en place ne debloque donc jamais plus d'un
# point de gel. Un marqueur residu d'un lancement precedent est purge au tout
# debut du demarrage du bot (bot_supervisor.purge_freeze_resume_marker, appelee
# depuis main.py), avant toute boucle de gel.
#
# Le bot reprend exactement l'action qu'il s'appretait a effectuer au moment du
# gel (ce n'est pas une annulation de l'action, seulement une pause d'observation
# avant qu'elle ne se produise) - un enchainement de plusieurs points de gel
# distincts pour un meme evenement (ex: pause() du RuntimeGuard puis fermeture
# navigateur en fin de cycle) peut donc necessiter de reexecuter ce script
# plusieurs fois.
#
# Usage :
#   .\topsurveys_bot_freeze.ps1 -AccountId "ysense_bot_001"
#   .\topsurveys_bot_freeze.ps1 -AccountId "ysense_bot_001" -PidsDir "D:\surveybot\pids"

param(
    [Parameter(Mandatory = $true)]
    [string]$AccountId,
    [string]$PidsDir = "C:\surveybot\pids"
)

if (-not (Test-Path $PidsDir)) {
    New-Item -ItemType Directory -Path $PidsDir -Force | Out-Null
}

$markerFile = Join-Path $PidsDir "bot_$AccountId.freeze_resume"
try {
    Set-Content -Path $markerFile -Value "resume_requested_by_operator_at=$(Get-Date -Format o)" -Encoding UTF8 -ErrorAction Stop
    Write-Output "[RESUME_FREEZE] bot=$AccountId - marqueur de reprise pose : $markerFile"
} catch {
    Write-Warning "[RESUME_FREEZE] bot=$AccountId - impossible de poser le marqueur ($markerFile) : $_"
    exit 1
}
