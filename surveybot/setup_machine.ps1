# setup_machine.ps1
# A executer UNE SEULE FOIS par mini-PC, avant le premier lancement de launch_all.ps1.
# Ne fait rien lie a l'auto-update (code\) : ce script prepare uniquement ce qui est
# persistant et ne doit JAMAIS etre gere par l'auto-update (venv, profiles).
#
# Prerequis avant de lancer ce script :
#   - Python 3.11 installe sur la machine (accessible via 'python' dans le PATH).
#   - requirements.txt copie manuellement dans C:\surveybot\ (il n'est PAS dans le
#     zip de code\ -- les dependances ne changent pas a chaque update de code).
#   - accounts.json deja present dans C:\surveybot\ (pour connaitre les profile_dir
#     a creer).
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

Write-Output ""
Write-Output "=== setup_machine.ps1 termine ==="
Write-Output "Verifications avant premier lancement reel :"
Write-Output "  - C:\surveybot\code\      doit contenir UNIQUEMENT le contenu du zip (pas les .ps1, pas secret.env, pas Dockerfile/fly.toml/requirements.txt)"
Write-Output "  - C:\surveybot\receiver_config.json doit exister avec les cles OPENAI_API_KEY / TWO_CAPTCHA_KEY / telegram_bot_token / telegram_chat_id / payout_name / payout_revolut_tag"
Write-Output "  - Test manuel recommande avant automatisation :"
Write-Output "      $venvPython $InstallDir\code\main.py"
