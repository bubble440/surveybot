# decommission_nssm.ps1
# Desinstalle les services NSSM surveybot_* d'une machine, une fois la nouvelle
# orchestration (launch_all.ps1 au logon + check_zombie_bots.ps1/wake_scheduler.ps1
# retargetes PID) validee en conditions reelles sur cette machine. Cf.
# Utils/ORCHESTRATION_TRACKING.md section 18.
#
# NE JAMAIS executer automatiquement depuis un autre script ou une tache planifiee -
# decommissionnement manuel explicite uniquement (regle du projet). Ce script n'est
# appele nulle part ailleurs dans le depot.
#
# Dry-run par defaut (liste uniquement ce qui serait fait) : il faut passer -Execute
# pour reellement arreter/supprimer les services. Action destructrice et difficilement
# reversible (reinstallation via nssm_setup_bot.ps1 necessaire pour revenir en arriere).
#
# Ne touche PAS :
#   - aux taches planifiees SurveyBot_* (LaunchAllOnLogon, ZombieCheck, WakeScheduler,
#     OrchestrationSync, LogRotation) - elles restent en place, desormais ciblees PID.
#   - a nssm_setup_bot.ps1 lui-meme (script conserve pour un eventuel retour arriere).
#   - aux fichiers pids\/accounts.json/profiles\ - aucun etat bot n'est modifie.
#
# Usage :
#   .\decommission_nssm.ps1                 # dry-run : liste les services concernes
#   .\decommission_nssm.ps1 -Execute         # arret + suppression reels (admin requis)
#   .\decommission_nssm.ps1 -Execute -AccountId "topsurveys_bot_001"   # un seul compte

param(
    [string]$InstallDir = "C:\surveybot",
    [string]$AccountId  = "",     # filtre optionnel : ne traiter qu'un seul compte
    [switch]$Execute               # sans ce switch : dry-run (aucune action reelle)
)

# Garde-fou boucle : abandon si le nombre de services surveybot_* est anormalement
# grand (meme convention que le reste de l'orchestration).
$MAX_SERVICES = 200

if ($Execute) {
    # -- Garde-fou droits administrateur -----------------------------------------
    # nssm stop/remove echoue silencieusement sans droits admin - on coupe court ici,
    # meme pattern que nssm_setup_bot.ps1.
    $_isAdmin = ([Security.Principal.WindowsPrincipal] `
        [Security.Principal.WindowsIdentity]::GetCurrent()
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $_isAdmin) {
        Write-Error "Droits administrateur requis pour -Execute - relance PowerShell via 'Executer en tant qu'administrateur'."
        exit 1
    }
}

try {
    $services = @(Get-Service -Name "surveybot_*" -ErrorAction Stop)
} catch {
    Write-Output "[DECOMMISSION] Aucun service surveybot_* trouve sur cette machine - rien a faire."
    exit 0
}

if ($AccountId) {
    $svcNameFilter = "surveybot_$AccountId"
    $services = @($services | Where-Object { $_.Name -eq $svcNameFilter })
    if ($services.Count -eq 0) {
        Write-Warning "[DECOMMISSION] Aucun service '$svcNameFilter' trouve - rien a faire."
        exit 0
    }
}

if ($services.Count -eq 0) {
    Write-Output "[DECOMMISSION] Aucun service surveybot_* trouve - rien a faire."
    exit 0
}

if ($services.Count -gt $MAX_SERVICES) {
    Write-Warning "[DECOMMISSION] $($services.Count) services > MAX_SERVICES ($MAX_SERVICES) - abort (verifier la machine)."
    exit 1
}

if (-not $Execute) {
    Write-Output "=== DRY-RUN (aucune action reelle) - $($services.Count) service(s) seraient arretes puis supprimes ==="
    foreach ($svc in $services) {
        Write-Output "  -> $($svc.Name)  [Status=$($svc.Status)]"
    }
    Write-Output ""
    Write-Output "Relancer avec -Execute pour appliquer reellement (admin requis)."
    Write-Output "Verifier au prealable que launch_all.ps1 (tache logon) + check_zombie_bots.ps1 +"
    Write-Output "wake_scheduler.ps1 (retargetes PID) fonctionnent correctement sur cette machine."
    exit 0
}

Write-Output "=== decommission_nssm.ps1 -Execute - $($services.Count) service(s) a traiter ==="

$processed = 0
foreach ($svc in $services) {
    if ($processed -ge $MAX_SERVICES) {
        Write-Warning "[DECOMMISSION] Budget MAX_SERVICES atteint - arret de la boucle."
        break
    }
    $processed++

    $svcName = $svc.Name
    Write-Output "--- $svcName (Status=$($svc.Status)) ---"

    try {
        Write-Output "    [NSSM] stop..."
        & nssm stop $svcName 2>&1 | ForEach-Object { Write-Output "      $_" }
    } catch {
        Write-Warning "[DECOMMISSION] $svcName - echec 'nssm stop' (on continue vers remove) : $_"
    }

    try {
        Write-Output "    [NSSM] remove (confirm)..."
        & nssm remove $svcName confirm 2>&1 | ForEach-Object { Write-Output "      $_" }
        Write-Output "    [OK] $svcName supprime."
    } catch {
        Write-Warning "[DECOMMISSION] $svcName - echec 'nssm remove' : $_"
    }
}

Write-Output ""
Write-Output "=== decommission_nssm.ps1 termine ($processed service(s) traite(s)) ==="
Write-Output "Rappel : les taches planifiees SurveyBot_* (LaunchAllOnLogon, ZombieCheck,"
Write-Output "WakeScheduler, OrchestrationSync, LogRotation) n'ont pas ete touchees."
