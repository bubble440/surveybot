# nssm_setup_bot.ps1
# Configure un service NSSM par bot défini dans accounts.json.
# Idempotent : peut être relancé pour mettre à jour des services existants.
#
# Usage :
#   .\nssm_setup_bot.ps1                           # tous les comptes
#   .\nssm_setup_bot.ps1 -AccountId "bot1"         # un seul compte (ajout / mise à jour)
#   .\nssm_setup_bot.ps1 -AccountsFile "D:\surveybot\accounts.json"
#
# Prérequis : nssm.exe dans le PATH, droits administrateur.
# À exécuter depuis C:\surveybot\ ou avec -InstallDir pointant vers le bon dossier.

param(
    [string]$AccountId       = "",               # filtre optionnel : traiter un seul compte
    [string]$InstallDir      = "C:\surveybot",
    [string]$ExeName         = "surveybot.exe",
    [string]$AccountsFile    = "",               # défaut : $InstallDir\accounts.json
    [string]$LogDir          = "",               # défaut : $InstallDir\logs
    [int]   $RestartDelaySec = 30,
    [int]   $MAX_ACCOUNTS    = 50               # garde-fou : abandon si > N comptes
)

# Valeurs dérivées
if (-not $AccountsFile) { $AccountsFile = Join-Path $InstallDir "accounts.json" }
if (-not $LogDir)       { $LogDir       = Join-Path $InstallDir "logs" }
$exePath = Join-Path $InstallDir $ExeName

# ── Pré-vérifications ────────────────────────────────────────────────────────

if (-not (Test-Path $exePath)) {
    Write-Error "Exécutable introuvable : $exePath"
    exit 1
}

if (-not (Test-Path $AccountsFile)) {
    Write-Error "accounts.json introuvable : $AccountsFile"
    exit 1
}

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

# ── Lecture accounts.json ────────────────────────────────────────────────────

$raw      = Get-Content -Path $AccountsFile -Raw -Encoding UTF8
$accounts = $raw | ConvertFrom-Json

if (-not $accounts -or @($accounts).Count -eq 0) {
    Write-Warning "accounts.json est vide — aucun service à configurer."
    exit 0
}

# Filtre optionnel par AccountId
if ($AccountId) {
    $accounts = @($accounts | Where-Object { $_.ACCOUNT_ID -eq $AccountId })
    if ($accounts.Count -eq 0) {
        Write-Error "Aucun compte trouvé pour AccountId='$AccountId' dans $AccountsFile"
        exit 1
    }
}

Write-Output "=== nssm_setup_bot.ps1 — $(@($accounts).Count) compte(s) à traiter ==="

# ── Boucle principale ────────────────────────────────────────────────────────

$processed  = 0
$accountIds = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)

foreach ($account in $accounts) {

    if ($processed -ge $MAX_ACCOUNTS) {
        Write-Warning "[GUARD] MAX_ACCOUNTS=$MAX_ACCOUNTS atteint — comptes restants ignorés."
        break
    }

    # Extraction des champs PAR_BOT tels qu'ils existent dans accounts.json
    $acctId    = "$($account.ACCOUNT_ID)".Trim()
    $email     = "$($account.EMAIL)".Trim()
    $password  = "$($account.PASSWORD)".Trim()
    $proxyUrl  = "$($account.PROXY_URL)".Trim()
    $proxyUser = "$($account.PROXY_USER)".Trim()
    $proxyPass = "$($account.PROXY_PASS)".Trim()

    # profile_dir ou CHROME_PROFILE_DIR (alias historique) — même champ logique
    $profileDir = ""
    if ($account.PSObject.Properties.Name -contains "profile_dir") {
        $profileDir = "$($account.profile_dir)".Trim()
    }
    if (-not $profileDir -and ($account.PSObject.Properties.Name -contains "CHROME_PROFILE_DIR")) {
        $profileDir = "$($account.CHROME_PROFILE_DIR)".Trim()
    }

    if (-not $acctId) {
        Write-Warning "[SKIP] Entrée sans ACCOUNT_ID — ignorée."
        continue
    }

    # Ajouté AVANT la vérification profile_dir : un compte présent dans accounts.json
    # mais skippé ci-dessous ne doit jamais être signalé comme service NSSM "orphelin"
    # dans la détection en fin de script — le compte existe, il manque juste son dossier.
    [void]$accountIds.Add($acctId)

    # Vérification du dossier profil Chrome — même garde-fou que l'ancien launch_all.ps1.
    # Sans ça, NSSM (via SERVICE_AUTO_START) tenterait de démarrer un bot dont le profil
    # n'existe pas, avec un crash immédiat au lieu d'un skip propre et loggé.
    if (-not $profileDir -or -not (Test-Path $profileDir)) {
        Write-Warning "[SKIP] $acctId — profile_dir introuvable ou vide : '$profileDir' — service NSSM non configuré."
        continue
    }

    $processed++

    $svcName   = "surveybot_$acctId"
    $logStdout = Join-Path $LogDir "bot_${acctId}_stdout.log"
    $logStderr = Join-Path $LogDir "bot_${acctId}_stderr.log"

    Write-Output ""
    Write-Output "--- $svcName ---"
    # PASSWORD délibérément absent du log (règle projet : ne jamais afficher les mots de passe)
    Write-Output "    ACCOUNT_ID=$acctId  EMAIL=$email  PROXY_URL=$proxyUrl  CHROME_PROFILE_DIR=$profileDir"

    # ── Installation ou mise à jour ───────────────────────────────────────────
    & nssm status $svcName 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Output "    [NSSM] Installation du service..."
        & nssm install $svcName $exePath
    } else {
        Write-Output "    [NSSM] Service existant — mise à jour de la config."
    }

    # ── Paramètres de base ────────────────────────────────────────────────────
    & nssm set $svcName AppDirectory  $InstallDir
    & nssm set $svcName AppParameters ""

    # ── Variables d'environnement PAR_BOT ─────────────────────────────────────
    # Aligné sur launch_all.ps1 : même jeu de variables, mêmes valeurs par défaut
    # pour GEO_LAT/GEO_LON/SURVEY_LANG/SURVEY_TZ (absents de accounts.json car non
    # acceptés par import_accounts.py — toujours injectés avec les défauts ci-dessous).
    # CHROME_PROFILE_DIR est le nom normalisé côté env (alias de profile_dir).
    & nssm set $svcName AppEnvironmentExtra `
        "ACCOUNT_ID=$acctId" `
        "EMAIL=$email" `
        "PASSWORD=$password" `
        "PROXY_URL=$proxyUrl" `
        "PROXY_USER=$proxyUser" `
        "PROXY_PASS=$proxyPass" `
        "CHROME_PROFILE_DIR=$profileDir" `
        "BROWSER_MODE=prod" `
        "GEO_LAT=48.8566" `
        "GEO_LON=2.3522" `
        "SURVEY_LANG=fr-FR" `
        "SURVEY_TZ=Europe/Paris" `
        "PYTHONIOENCODING=utf-8" `
        "PYTHONUTF8=1"

    # ── Logs ──────────────────────────────────────────────────────────────────
    & nssm set $svcName AppStdout        $logStdout
    & nssm set $svcName AppStderr        $logStderr
    & nssm set $svcName AppRotateFiles   1
    & nssm set $svcName AppRotateSeconds 86400    # rotation quotidienne
    & nssm set $svcName AppRotateBytes   10485760 # 10 Mo max par fichier

    # ── Méthodes d'arrêt gracieux ─────────────────────────────────────────────
    #
    # Sur Windows, seule la méthode Console (GenerateConsoleCtrlEvent CTRL_BREAK_EVENT)
    # est reçue par Python et déclenche notre handler SIGBREAK → séquence de fermeture
    # propre (record_exit, Postgres, Chrome). Les méthodes Window (WM_CLOSE) et Thread
    # (PostQuitMessage) sont sans effet sur un process console Python — on les saute
    # (AppStopMethodSkip 6 = bitmask : Window=2 + Thread=4) pour éviter ~3 s d'attente
    # inutile avant que NSSM ne passe directement à TerminateProcess.
    # AppStopMethodConsole est le seul timeout qui compte : c'est la fenêtre accordée
    # à la séquence de fermeture propre (Postgres + Chrome + record_exit). 30 s est
    # une marge confortable ; TerminateProcess ne s'enclenche qu'au-delà.
    #
    & nssm set $svcName AppStopMethodSkip    6      # skip Window (2) + Thread (4)
    & nssm set $svcName AppStopMethodConsole 30000  # 30 s pour la fermeture propre

    # ── Politique de redémarrage selon le code de sortie ──────────────────────
    #
    # EXIT_VOLUNTARY    = 0  → ne pas redémarrer (SIGINT/SIGBREAK propre, target journalier…)
    # EXIT_CRASH        = 1  → redémarrer (crash Python ou sortie inattendue)
    # EXIT_SOFT_RESTART = 2  → redémarrer (idle, trop d'erreurs, runtime_limit…)
    # EXIT_FATAL        = 3  → ne pas redémarrer (seuil de crash-loop dépassé)
    # Tout autre code   → redémarrer par défaut (comportement NSSM sûr)
    #
    & nssm set $svcName AppExit Default  Restart
    & nssm set $svcName AppExit 0        Exit      # EXIT_VOLUNTARY : pas de restart
    & nssm set $svcName AppExit 3        Exit      # EXIT_FATAL     : pas de restart
    & nssm set $svcName AppRestartDelay  ($RestartDelaySec * 1000)   # en millisecondes

    # ── Démarrage automatique au boot ─────────────────────────────────────────
    & nssm set $svcName Start SERVICE_AUTO_START

    Write-Output "    [OK] $svcName configuré."
}

# ── Détection des services NSSM orphelins ────────────────────────────────────
# Un service surveybot_* sans entrée correspondante dans accounts.json est signalé
# clairement mais jamais supprimé automatiquement — une désinstallation est une
# décision humaine (nssm remove <nom> confirm).

Write-Output ""
Write-Output "=== Vérification des services orphelins (préfixe surveybot_) ==="

try {
    $existingSvcs = @(Get-Service -Name "surveybot_*" -ErrorAction SilentlyContinue)
    $orphans = @($existingSvcs | Where-Object {
        $svcAccountId = $_.Name -replace "^surveybot_", ""
        -not $accountIds.Contains($svcAccountId)
    })

    if ($orphans.Count -gt 0) {
        Write-Warning "Services sans compte dans accounts.json (vérifier manuellement) :"
        foreach ($o in $orphans) {
            Write-Warning "  → $($o.Name)  [Status=$($o.Status)]  — pour supprimer : nssm remove $($o.Name) confirm"
        }
    } else {
        Write-Output "Aucun service orphelin détecté."
    }
} catch {
    Write-Warning "Impossible de lister les services Windows : $_"
}

Write-Output ""
Write-Output "=== nssm_setup_bot.ps1 terminé ($processed compte(s) traité(s)) ==="
Write-Output "  Démarrer tous  : Get-Service surveybot_* | Start-Service"
Write-Output "  Statut tous    : Get-Service surveybot_* | Format-Table Name,Status -AutoSize"
Write-Output ""
Write-Output "Pour installer la tâche planifiée de détection zombie :"
Write-Output "  Voir le header de check_zombie_bots.ps1 (Register-ScheduledTask)"