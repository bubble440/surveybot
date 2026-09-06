# setup_machine.ps1
# A executer UNE SEULE FOIS par mini-PC, avant le premier lancement de launch_all.ps1.
# Ne fait rien lie a l'auto-update (code\) : ce script prepare uniquement ce qui est
# persistant et ne doit JAMAIS etre gere par l'auto-update (venv, profiles, nssm, tools).
#
# Prerequis avant de lancer ce script :
#   - Python 3.11 installe sur la machine (accessible via 'python' dans le PATH).
#   - requirements.txt copie manuellement dans C:\surveybot\ (il n'est PAS dans le
#     zip de code\ -- les dependances ne changent pas a chaque update de code).
#   - accounts.json deja present dans C:\surveybot\ (pour connaitre les profile_dir
#     a creer).
#   - Execution en tant qu'administrateur (necessaire pour ecrire le PATH systeme
#     lors de l'installation de NSSM).
#
# Usage :
#   .\setup_machine.ps1
#   .\setup_machine.ps1 -InstallDir "C:\surveybot"

param(
    [string]$InstallDir     = "C:\surveybot",
    [string]$RequirementsFile = "$InstallDir\requirements.txt",
    [string]$AccountsFile   = "$InstallDir\accounts.json"
)

$ErrorActionPreference = "Stop"

Write-Output "=== setup_machine.ps1 -- $InstallDir ==="

# -- 1) venv -------------------------------------------------------------------
$venvDir    = Join-Path $InstallDir "venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"

if (Test-Path $venvPython) {
    Write-Output "[VENV] Deja present : $venvPython -- rien a faire."
} else {
    Write-Output "[VENV] Creation dans $venvDir ..."
    python -m venv $venvDir
    if (-not (Test-Path $venvPython)) {
        Write-Error "[VENV] Echec de creation -- python.exe introuvable apres 'python -m venv'."
        exit 1
    }
    Write-Output "[VENV] OK."
}

# -- 2) Dependances --------------------------------------------------------------
if (-not (Test-Path $RequirementsFile)) {
    Write-Warning "[DEPS] requirements.txt introuvable ($RequirementsFile) -- installation sautee."
    Write-Warning "[DEPS] Copie manuellement requirements.txt depuis le repo dev puis relance ce script."
} else {
    Write-Output "[DEPS] Installation depuis $RequirementsFile ..."
    & $venvPython -m pip install --upgrade pip
    & $venvPython -m pip install -r $RequirementsFile
    Write-Output "[DEPS] OK."
}

# -- 3) Dossiers profiles\ (un par bot, d'apres accounts.json) -------------------
if (-not (Test-Path $AccountsFile)) {
    Write-Warning "[PROFILES] accounts.json introuvable ($AccountsFile) -- etape sautee."
} else {
    $raw      = Get-Content -Path $AccountsFile -Raw -Encoding UTF8
    $accounts = $raw | ConvertFrom-Json

    $created = 0
    $skipped = 0
    foreach ($account in $accounts) {
        $profileDir = $null
        if ($account.PSObject.Properties.Name -contains "profile_dir") {
            $profileDir = "$($account.profile_dir)".Trim()
        }
        if (-not $profileDir -and ($account.PSObject.Properties.Name -contains "CHROME_PROFILE_DIR")) {
            $profileDir = "$($account.CHROME_PROFILE_DIR)".Trim()
        }

        if (-not $profileDir) {
            Write-Warning "[PROFILES] Compte $($account.account_id) sans profile_dir -- ignore."
            continue
        }

        if (Test-Path $profileDir) {
            $skipped++
            continue
        }

        New-Item -ItemType Directory -Path $profileDir -Force | Out-Null
        Write-Output "[PROFILES] Cree : $profileDir"
        $created++
    }
    Write-Output "[PROFILES] $created cree(s), $skipped deja existant(s)."
}

# -- 4) Dossiers pids\ / logs\ (normalement auto-crees par launch_all.ps1, mais -----
#       autant les avoir des maintenant pour eviter toute surprise au premier lancement)
foreach ($dir in @("pids", "logs")) {
    $path = Join-Path $InstallDir $dir
    if (-not (Test-Path $path)) {
        New-Item -ItemType Directory -Path $path | Out-Null
        Write-Output "[DIRS] Cree : $path"
    }
}

# -- 5) NSSM (prerequis pour nssm_setup_bot.ps1 / supervision des bots) ----------
# Outil tiers, jamais gere par le zip d'auto-update (voir $ExcludeDirs "tools"
# dans build_release_zip.ps1). Installe une fois pour toutes dans InstallDir\tools.
$nssmToolsDir = Join-Path $InstallDir "tools"
$nssmExePath  = Join-Path $nssmToolsDir "nssm.exe"
$nssmInPath   = $null -ne (Get-Command nssm.exe -ErrorAction SilentlyContinue)

if ($nssmInPath) {
    Write-Output "[NSSM] Deja present dans le PATH -- rien a faire."
} elseif (Test-Path $nssmExePath) {
    Write-Warning "[NSSM] nssm.exe present dans $nssmToolsDir mais absent du PATH -- ajoute-le manuellement au PATH systeme puis rouvre le terminal."
} else {
    Write-Output "[NSSM] Telechargement (nssm.cc) ..."
    $nssmZipUrl  = "https://nssm.cc/release/nssm-2.24.zip"
    $nssmZipPath = Join-Path $env:TEMP "nssm-2.24.zip"
    $nssmTmpDir  = Join-Path $env:TEMP "nssm-2.24_extract"

    try {
        Invoke-WebRequest -Uri $nssmZipUrl -OutFile $nssmZipPath -UseBasicParsing -TimeoutSec 30
        if (Test-Path $nssmTmpDir) { Remove-Item $nssmTmpDir -Recurse -Force }
        Expand-Archive -Path $nssmZipPath -DestinationPath $nssmTmpDir -Force

        $extractedExe = Join-Path $nssmTmpDir "nssm-2.24\win64\nssm.exe"
        if (-not (Test-Path $extractedExe)) {
            throw "nssm.exe introuvable apres extraction ($extractedExe)"
        }

        if (-not (Test-Path $nssmToolsDir)) {
            New-Item -ItemType Directory -Path $nssmToolsDir -Force | Out-Null
        }
        Copy-Item $extractedExe -Destination $nssmExePath -Force

        # PATH machine (necessite droits admin) -- extension persistante, pas juste
        # pour la session courante.
        $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
        if ($machinePath -notlike "*$nssmToolsDir*") {
            [Environment]::SetEnvironmentVariable("Path", "$machinePath;$nssmToolsDir", "Machine")
            Write-Output "[NSSM] $nssmToolsDir ajoute au PATH systeme -- ROUVRIR le terminal pour que 'nssm' soit reconnu."
        }
        Write-Output "[NSSM] OK -- installe dans $nssmExePath."
    } catch {
        Write-Warning "[NSSM] Echec de l'installation automatique : $_"
        Write-Warning "[NSSM] Installation manuelle requise : https://nssm.cc/download -- extraire nssm.exe (win64) dans $nssmToolsDir et l'ajouter au PATH systeme."
    } finally {
        if (Test-Path $nssmZipPath) { Remove-Item $nssmZipPath -Force -ErrorAction SilentlyContinue }
        if (Test-Path $nssmTmpDir) { Remove-Item $nssmTmpDir -Recurse -Force -ErrorAction SilentlyContinue }
    }
}

# -- 6) Scripts d'orchestration racine (jamais geres par l'auto-update code\) ----
# nssm_setup_bot.ps1 / check_zombie_bots.ps1 / wake_scheduler.ps1 sont exclus du
# zip (voir $ExcludeFiles dans build_release_zip.ps1) -- ils doivent etre copies
# manuellement depuis le repo dev. Ce script ne peut que signaler leur absence.
$orchestrationScripts = @("nssm_setup_bot.ps1", "check_zombie_bots.ps1", "wake_scheduler.ps1")
$missingScripts = @()
foreach ($script in $orchestrationScripts) {
    if (-not (Test-Path (Join-Path $InstallDir $script))) {
        $missingScripts += $script
    }
}
if ($missingScripts.Count -gt 0) {
    Write-Warning "[ORCHESTRATION] Script(s) manquant(s) a la racine $InstallDir : $($missingScripts -join ', ')"
    Write-Warning "[ORCHESTRATION] A copier manuellement depuis le repo dev -- jamais fournis par l'auto-update."
} else {
    Write-Output "[ORCHESTRATION] Tous les scripts d'orchestration attendus sont presents."
}

Write-Output ""
Write-Output "=== setup_machine.ps1 termine ==="
Write-Output "Verifications avant premier lancement reel :"
Write-Output "  - C:\surveybot\code\      doit contenir UNIQUEMENT le contenu du zip (pas les .ps1, pas secret.env, pas Dockerfile/fly.toml/requirements.txt)"
Write-Output "  - C:\surveybot\receiver_config.json doit exister avec les cles OPENAI_API_KEY / TWO_CAPTCHA_KEY / telegram_bot_token / telegram_chat_id / payout_name / payout_revolut_tag"
Write-Output "  - Si NSSM vient d'etre installe : ROUVRIR le terminal avant d'executer nssm_setup_bot.ps1"
Write-Output "  - Test manuel recommande avant automatisation :"
Write-Output "      $venvPython $InstallDir\code\main.py"