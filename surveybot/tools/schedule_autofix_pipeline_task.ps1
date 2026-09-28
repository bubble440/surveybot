# schedule_autofix_pipeline_task.ps1
#
# Partie D (optionnelle) du chantier "clone dedie a l'orchestrateur autofix"
# (cf. Utils/AUTOFIX_CLONE_ORCHESTRATEUR.md). Enregistre une tache planifiee
# Windows qui execute tools\run_autofix_pipeline.py DEPUIS LE CLONE, a un
# intervalle regulier. Ce script ne fait qu'ENREGISTRER la tache -- il ne lance
# JAMAIS lui-meme le pipeline, et n'est appele par AUCUN autre script de ce
# chantier (setup/preflight/sync ne le referencent jamais). Une fois enregistree,
# la tache planifiee elle-meme s'execute automatiquement selon son intervalle --
# c'est le but d'une tache planifiee -- mais l'ENREGISTREMENT (l'execution de CE
# script) reste une decision explicite de l'operateur, jamais automatisee.
#
# Intervalle par defaut : 60 minutes. Justification (cf. investigation Partie A/B
# de ce meme chantier) : une invocation peut enchainer jusqu'a --max-cases (5 par
# defaut, Survey/autofix_orchestrator.py::DEFAULT_MAX_CASES) cases, CHACUN
# pouvant occuper Claude Code jusqu'a --claude-timeout-s (600s par defaut,
# DEFAULT_CLAUDE_TIMEOUT_S) avant les phases de validation suivantes (compilation,
# import, lint, rejeu navigateur) -- une invocation complete peut donc durer
# plusieurs dizaines de minutes. Le verrou d'exclusion de l'orchestrateur
# (Survey/autofix_orchestrator.py::DEFAULT_LOCK_STALE_AFTER_S = 7200s = 2h, la
# "duree maximale theorique d'une invocation" au sens du code lui-meme) rend un
# intervalle plus court QUE LA DUREE REELLE D'UNE INVOCATION sans danger -- le
# declenchement suivant obtient alors juste le code de sortie 3 (verrou deja
# detenu), qui est NORMAL, pas un echec de la tache planifiee (cf. plus bas).
#
# Usage :
#   .\schedule_autofix_pipeline_task.ps1
#   .\schedule_autofix_pipeline_task.ps1 -IntervalMinutes 120 -PipelineExtraArgs "--max-cases 3"
#   .\schedule_autofix_pipeline_task.ps1 -Unregister   # retire la tache planifiee

param(
    [string]$ClonePackageRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$TaskName = "SurveyBot_AutofixPipeline",
    [int]$IntervalMinutes = 60,
    [string]$VenvDirName = "venv",
    [string]$VenvPythonRelPath = "",
    [string]$PipelineExtraArgs = "",
    [double]$ExecutionTimeLimitHours = 3,
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"

function Write-Log {
    param([string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Output "[$ts] [SCHEDULE_AUTOFIX_TASK] $Message"
}

# ---------------------------------------------------------------------------
# Garde-fou plateforme -- le module ScheduledTasks (Register-ScheduledTask,
# New-ScheduledTaskAction/Trigger/SettingsSet) est specifique a Windows. Verifie
# explicitement plutot que de laisser un message d'erreur PowerShell generique
# et peu clair pour l'operateur.
# ---------------------------------------------------------------------------

if (-not (Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue)) {
    Write-Error "Ce script requiert Windows (module ScheduledTasks, Register-ScheduledTask introuvable) -- non disponible sur cette plateforme."
    exit 1
}

# ---------------------------------------------------------------------------
# Desinstallation (optionnelle)
# ---------------------------------------------------------------------------

if ($Unregister) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $existing) {
        Write-Log "Tache '$TaskName' deja absente -- rien a faire."
        exit 0
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Log "Tache '$TaskName' desinstallee."
    exit 0
}

# ---------------------------------------------------------------------------
# 0) Resolution des chemins
# ---------------------------------------------------------------------------

if (-not (Test-Path $ClonePackageRoot)) {
    Write-Error "ClonePackageRoot introuvable : $ClonePackageRoot"
    exit 1
}
$ClonePackageRoot = (Resolve-Path $ClonePackageRoot).Path

if (-not $VenvPythonRelPath) {
    $VenvPythonRelPath = Join-Path $VenvDirName (Join-Path "Scripts" "python.exe")
}
$venvPython = Join-Path $ClonePackageRoot $VenvPythonRelPath
if (-not (Test-Path $venvPython)) {
    Write-Error "venv introuvable : $venvPython -- executer setup_autofix_clone.ps1 (Partie A) avant de planifier."
    exit 1
}
$venvScriptsDir = Split-Path -Parent $venvPython

$runnerScript = Join-Path $ClonePackageRoot (Join-Path "tools" "run_autofix_pipeline.py")
if (-not (Test-Path $runnerScript)) {
    Write-Error "tools\run_autofix_pipeline.py introuvable dans le clone : $runnerScript"
    exit 1
}

# ---------------------------------------------------------------------------
# 1) Lanceur genere -- fichier technique, jamais un fichier source de ce
#    chantier, regenere a chaque execution de ce script (jamais edite a la
#    main). Un seul mecanisme, ici, pour tout ce qu'un declenchement planifie
#    doit faire avant d'invoquer le pipeline : prefixer le PATH du dossier
#    Scripts du venv (verifie empiriquement en Partie A -- shutil.which("ruff"),
#    utilise par Survey/static_validator.py Phase 8, ne resout que par rapport
#    au PATH du PROCESSUS qui execute tools\run_autofix_pipeline.py) et se
#    positionner dans le paquet du clone (racines d'artefacts relatives).
#    Task Scheduler n'offre pas de mecanisme direct pour prefixer une variable
#    d'environnement dans New-ScheduledTaskAction -- ce lanceur est le seul
#    point qui en a besoin, plutot qu'une chaine -Command imbriquee fragile a
#    l'echappement de guillemets.
# ---------------------------------------------------------------------------

$launcherPath = Join-Path $ClonePackageRoot "run_autofix_pipeline_task_launcher.ps1"
$launcherContent = @"
# GENERE AUTOMATIQUEMENT par schedule_autofix_pipeline_task.ps1 ($(Get-Date -Format "yyyy-MM-dd HH:mm:ss")) -- NE PAS EDITER A LA MAIN, regenere a chaque (re)planification.
`$env:PATH = "$venvScriptsDir;`$env:PATH"
Set-Location -Path "$ClonePackageRoot"
& "$venvPython" "tools\run_autofix_pipeline.py" $PipelineExtraArgs
exit `$LASTEXITCODE
"@
# [System.IO.File]::WriteAllText avec un UTF8Encoding sans BOM -- Set-Content
# -Encoding UTF8 sous PowerShell 5.1 ecrit un BOM (convention deja etablie dans
# ce depot, cf. build_orchestration_release.ps1).
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($launcherPath, $launcherContent, $utf8NoBom)
Write-Log "Lanceur (re)genere : $launcherPath"

# ---------------------------------------------------------------------------
# 2) Enregistrement / mise a jour de la tache planifiee
# ---------------------------------------------------------------------------

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$launcherPath`""

# "hourly starting now" -- Once + RepetitionInterval/RepetitionDuration est la
# forme documentee par Microsoft pour un declenchement recurrent qui ne
# necessite pas un declencheur "AtStartup"/"Daily" separe.
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration ([TimeSpan]::MaxValue)

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours $ExecutionTimeLimitHours) `
    -StartWhenAvailable `
    -DontStopOnIdleEnd

# Pas de -RunLevel Highest : ce script ne fait rien qui necessite des droits
# eleves (aucune installation de service, aucune ecriture systeme) -- tourne
# avec les droits normaux du compte qui l'enregistre, meme principe de moindre
# privilege que le reste de ce chantier. Compte INTERACTIF de l'operateur
# (jamais SYSTEM), meme convention que wake_scheduler.ps1/check_zombie_bots.ps1
# -- necessaire si jamais le pipeline devait un jour piloter un navigateur non
# headless (non le cas aujourd'hui, Survey/replay_browser.py tourne headless).
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

Write-Output ""
Write-Output "=== schedule_autofix_pipeline_task.ps1 termine ==="
Write-Output "  Tache            : $TaskName"
Write-Output "  Intervalle       : toutes les $IntervalMinutes minutes"
Write-Output "  Limite d'execution: ${ExecutionTimeLimitHours}h (au-dela, Task Scheduler termine le processus)"
Write-Output "  Lanceur          : $launcherPath"
Write-Output ""
Write-Output "IMPORTANT -- lecture du resultat d'une invocation :"
Write-Output "  - Le code de sortie 3 de tools\run_autofix_pipeline.py (verrou d'exclusion deja"
Write-Output "    detenu par une autre invocation) est NORMAL -- ne JAMAIS le lire comme un echec"
Write-Output "    de la tache planifiee elle-meme (visible dans le Planificateur de taches comme"
Write-Output "    'Dernier resultat d'execution' = 0x3, sans rapport avec un vrai probleme)."
Write-Output "  - Le detail par case (succes/echec/notification humaine) est dans"
Write-Output "    $(Join-Path $ClonePackageRoot 'autofix_pipeline_runs')\<case_id>\ -- jamais le code de"
Write-Output "    sortie global seul, qui agrege plusieurs cases independants."
Write-Output "  - Executer preflight_autofix_clone.ps1 (Partie B) si le resultat semble anormal."
Write-Output ""
Write-Output "Pour retirer cette tache planifiee : .\schedule_autofix_pipeline_task.ps1 -Unregister"
