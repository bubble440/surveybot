# nssm_setup_bot.ps1
# Configure un service NSSM pour un bot surveybot.
# À exécuter en tant qu'administrateur, une fois par bot.
#
# Usage :
#   .\nssm_setup_bot.ps1 -AccountId "bot1" -ProxyUrl "http://user:pass@host:port"
#   .\nssm_setup_bot.ps1 -AccountId "bot2" -ProxyUrl "..." -InstallDir "D:\surveybot"
#
# Le script est idempotent : il peut être relancé pour mettre à jour la config
# d'un service déjà installé (nssm set écrase la valeur existante).

param(
    [Parameter(Mandatory=$true)]
    [string]$AccountId,           # identifiant unique du bot (ex: "bot1")

    [Parameter(Mandatory=$true)]
    [string]$ProxyUrl,            # PROXY_URL pour ce bot (ex: "http://user:pass@host:port")

    [string]$InstallDir  = "C:\surveybot",
    [string]$ExeName     = "surveybot.exe",
    [string]$LogDir      = "C:\surveybot\logs",
    [int]$RestartDelaySec = 30    # délai NSSM avant redémarrage automatique
)

$svcName = "surveybot_$AccountId"
$exePath = Join-Path $InstallDir $ExeName

# Vérifier que l'exécutable existe
if (-not (Test-Path $exePath)) {
    Write-Error "Exécutable introuvable : $exePath"
    exit 1
}

# Créer le dossier de logs si nécessaire
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

$logStdout = Join-Path $LogDir "bot_${AccountId}_stdout.log"
$logStderr = Join-Path $LogDir "bot_${AccountId}_stderr.log"

Write-Output "=== Configuration NSSM : $svcName ==="

# ── Installation / mise à jour du service ───────────────────────────────────
$existing = & nssm status $svcName 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Output "[NSSM] Installation du service..."
    & nssm install $svcName $exePath
} else {
    Write-Output "[NSSM] Service déjà existant — mise à jour de la config."
}

# ── Paramètres de base ───────────────────────────────────────────────────────
& nssm set $svcName AppDirectory $InstallDir
& nssm set $svcName AppParameters ""          # l'exe ne prend pas d'argument positionnel

# ── Variables d'environnement par bot ────────────────────────────────────────
# ACCOUNT_ID et PROXY_URL sont PAR_BOT (propres à chaque instance).
# Les clés PAR_RECEPTEUR (OPENAI_API_KEY, telegram, etc.) sont lues depuis
# receiver_config.json par le bot au démarrage — ne pas les dupliquer ici.
& nssm set $svcName AppEnvironmentExtra `
    "ACCOUNT_ID=$AccountId" `
    "PROXY_URL=$ProxyUrl" `
    "BROWSER_MODE=prod"

# ── Logs ─────────────────────────────────────────────────────────────────────
& nssm set $svcName AppStdout        $logStdout
& nssm set $svcName AppStderr        $logStderr
& nssm set $svcName AppRotateFiles   1
& nssm set $svcName AppRotateSeconds 86400    # rotation quotidienne
& nssm set $svcName AppRotateBytes   10485760 # 10 Mo max par fichier

# ── Politique de redémarrage selon le code de sortie ─────────────────────────
#
# EXIT_VOLUNTARY    = 0  → ne pas redémarrer (arrêt intentionnel : SIGTERM, target journalier…)
# EXIT_CRASH        = 1  → redémarrer (crash Python ou sortie inattendue)
# EXIT_SOFT_RESTART = 2  → redémarrer (idle, trop d'erreurs, runtime_limit…)
# EXIT_FATAL        = 3  → ne pas redémarrer (seuil de crash-loop dépassé, alerte Telegram envoyée)
# Tout autre code  → redémarrer par défaut (comportement NSSM sûr)
#
& nssm set $svcName AppExit Default  Restart
& nssm set $svcName AppExit 0        Exit      # EXIT_VOLUNTARY : pas de restart
& nssm set $svcName AppExit 3        Exit      # EXIT_FATAL     : pas de restart
& nssm set $svcName AppRestartDelay  ($RestartDelaySec * 1000)   # en millisecondes

# ── Démarrage automatique au boot ────────────────────────────────────────────
& nssm set $svcName Start SERVICE_AUTO_START

Write-Output ""
Write-Output "=== Service $svcName configuré. ==="
Write-Output "  Démarrer  : nssm start $svcName"
Write-Output "  Arrêter   : nssm stop $svcName"
Write-Output "  Statut    : nssm status $svcName"
Write-Output "  Logs      : $logStdout"
Write-Output ""
Write-Output "Pour installer la tâche planifiée de détection zombie :"
Write-Output "  .\check_zombie_bots.ps1  (voir header du script pour Register-ScheduledTask)"
