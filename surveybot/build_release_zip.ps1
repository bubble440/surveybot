# build_release_zip.ps1
# Remplace nuitka_build_release.ps1 pour la phase de deploiement interne (mini-PC
# du parc bare metal). Ne compile plus rien : zippe le code source tel quel.
# Duree attendue : quelques secondes, pas des heures.
#
# A executer depuis la racine du projet dev (la ou se trouve main.py), PAS depuis
# une machine mini-PC de prod.
#
# Ce que fait ce script :
#   1. Lit BOT_VERSION depuis _license_config.py (source de verite unique - pas de
#      parametre de version separe a maintenir en double).
#   2. Zippe tout le projet SAUF les repertoires/fichiers listes dans $ExcludeDirs /
#      $ExcludeFiles (donnees runtime, secrets, scripts d'orchestration qui restent
#      a la racine C:\surveybot\ et ne sont jamais remplaces par l'auto-update).
#   3. Calcule le SHA256 du zip produit.
#   4. Reecrit manifest.json (version, url, sha256) pret a uploader sur R2.
#
# Usage :
#   .\build_release_zip.ps1
#   .\build_release_zip.ps1 -OutputDir "dist_zip" -R2BaseUrl "https://pub-xxx.r2.dev"
#
# Apres execution : uploader manuellement (ou via ta commande rclone/aws s3 cp
# habituelle) le zip produit ET manifest.json vers le bucket R2 - ce script ne
# fait volontairement pas l'upload (pas d'hypothese sur ton outil de sync R2).

param(
    [string]$ProjectRoot = (Get-Location).Path,
    [string]$OutputDir   = "dist_zip",
    [string]$R2BaseUrl   = "https://pub-565d2bb59d364c1490255c5dddc296aa.r2.dev",
    # Contourne le garde-fou "version identique au manifeste distant" ci-dessous.
    # Cas legitime : reposter le meme zip suite a une corruption/erreur d'upload R2,
    # sans avoir change de code depuis. Hors de ce cas, ne pas utiliser -Force.
    [switch]$Force
)

$ErrorActionPreference = "Stop"

# -- Dossiers / fichiers exclus du zip ----------------------------------------
# Regle : tout ce qui est donnee runtime, secret, ou script d'orchestration qui
# vit a la racine C:\surveybot\ (jamais a l'interieur de code\) est exclu.
$ExcludeDirs = @(
    ".venv", "venv", "__pycache__", ".nuitka_cache", "dist_nuitka", $OutputDir,
    ".git", ".claude", "profiles", "pids", "logs", "code.old", "code_new_tmp", 
    "Utils", "tools"
)
$ExcludeFiles = @(
    "accounts.json", "receiver_config.json", "secret.env",
    "manifest.json", "README",
    "launch_all.ps1", "nssm_setup_bot.ps1", "wake_scheduler.ps1",
    "check_zombie_bots.ps1", "run_tabs.ps1", "attach_tab.ps1",
    "nuitka_build_dev.ps1", "nuitka_build_release.ps1", "build_release_zip.ps1",
    "Dockerfile", "fly.toml", "requirements.txt"
)

# -- Lecture de BOT_VERSION depuis _license_config.py -------------------------
$licenseConfigPath = Join-Path $ProjectRoot "_license_config.py"
if (-not (Test-Path $licenseConfigPath)) {
    Write-Error "_license_config.py introuvable dans $ProjectRoot"
    exit 1
}

$licenseContent = Get-Content $licenseConfigPath -Raw
if ($licenseContent -notmatch 'BOT_VERSION\s*=\s*"([^"]+)"') {
    Write-Error "BOT_VERSION introuvable dans _license_config.py"
    exit 1
}
$version = $Matches[1]
Write-Output "=== Version detectee : $version ==="

# -- Garde-fou : version identique a celle deja publiee sur R2 ----------------
# Oubli frequent : modifier le code sans remonter BOT_VERSION. Resultat silencieux
# sans ce garde-fou : le build reussit, l'upload reussit, mais update_checker.py
# cote bot voit current_version == remote_version et ignore la mise a jour -
# aucune erreur nulle part, juste un no-op qui passe inapercu.
# Verification unique et non bloquante en cas d'echec reseau (le manifeste distant
# peut simplement ne pas encore exister, ex. tout premier build).
$remoteManifestUrl = "$R2BaseUrl/manifest.json"
try {
    $remoteManifestRaw = Invoke-WebRequest -Uri $remoteManifestUrl -UseBasicParsing -TimeoutSec 10
    $remoteManifest    = $remoteManifestRaw.Content | ConvertFrom-Json
    $remoteVersion     = $remoteManifest.version

    if ($remoteVersion -eq $version) {
        if ($Force) {
            Write-Output "=== ATTENTION : version $version identique au manifeste distant deja publie - poursuite forcee (-Force) ==="
        } else {
            Write-Error (
                "BOT_VERSION ($version) est identique a la version deja publiee sur R2 ($remoteManifestUrl). " +
                "Si du code a change depuis la derniere release, incrementer BOT_VERSION dans _license_config.py " +
                "avant de relancer ce script. Si tu reposte volontairement la meme version (ex. correction d'un " +
                "upload R2 corrompu, aucun changement de code), relancer avec -Force."
            )
            exit 1
        }
    } else {
        Write-Output "=== Version distante actuelle : $remoteVersion -> $version, OK ==="
    }
} catch {
    Write-Output "=== Manifeste distant inaccessible ($remoteManifestUrl) - garde-fou de version ignore : $_ ==="
}

# -- Preparation du dossier de sortie -----------------------------------------
$outputPath = Join-Path $ProjectRoot $OutputDir
if (Test-Path $outputPath) {
    Remove-Item $outputPath -Recurse -Force
}
New-Item -ItemType Directory -Path $outputPath | Out-Null

$zipName = "surveybot-code-$version.zip"
$zipPath = Join-Path $outputPath $zipName

# -- Construction de la liste des fichiers a zipper ---------------------------
Write-Output "=== Recherche des fichiers source (exclusions appliquees) ==="

$allItems = Get-ChildItem -Path $ProjectRoot -Recurse -File -Force | Where-Object {
    $relPath = $_.FullName.Substring($ProjectRoot.Length).TrimStart("\")
    $topDir  = ($relPath -split "\\")[0]

    $isExcludedDir  = $ExcludeDirs  -contains $topDir
    $isExcludedFile = ($relPath -notmatch "\\") -and ($ExcludeFiles -contains $relPath)
    $isPycache      = $relPath -match "\\__pycache__\\"
    $isPyc          = $relPath -match "\.pyc$"

    -not ($isExcludedDir -or $isExcludedFile -or $isPycache -or $isPyc)
}

if ($allItems.Count -eq 0) {
    Write-Error "Aucun fichier source trouve - verifier ProjectRoot et les exclusions."
    exit 1
}

Write-Output "  $($allItems.Count) fichier(s) a inclure."

# -- Verification garde-fou : main.py doit etre present -----------------------
$hasMain = $allItems | Where-Object { $_.Name -eq "main.py" -and ($_.FullName.Substring($ProjectRoot.Length).TrimStart("\")) -eq "main.py" }
if (-not $hasMain) {
    Write-Error "main.py absent de la selection - abandon (l'update_checker.py cote bot refuserait ce zip)."
    exit 1
}

# -- Creation du zip -----------------------------------------------------------
Write-Output "=== Compression -> $zipPath ==="

# Compress-Archive ne preserve pas facilement les chemins relatifs a partir d'une
# liste heterogene de FileInfo -> on passe par un dossier de staging temporaire,
# nettoye apres coup. Simple et previsible, pas d'optimisation prematuree.
$stagingDir = Join-Path $outputPath "_staging"
New-Item -ItemType Directory -Path $stagingDir | Out-Null

foreach ($item in $allItems) {
    $relPath = $item.FullName.Substring($ProjectRoot.Length).TrimStart("\")
    $destPath = Join-Path $stagingDir $relPath
    $destDir  = Split-Path $destPath -Parent
    if (-not (Test-Path $destDir)) {
        New-Item -ItemType Directory -Path $destDir -Force | Out-Null
    }
    Copy-Item -Path $item.FullName -Destination $destPath -Force
}

Compress-Archive -Path "$stagingDir\*" -DestinationPath $zipPath -CompressionLevel Optimal -Force
Remove-Item $stagingDir -Recurse -Force

# -- SHA256 --------------------------------------------------------------------
$sha256 = (Get-FileHash -Path $zipPath -Algorithm SHA256).Hash.ToLower()
Write-Output "=== SHA256 : $sha256 ==="

# -- Ecriture du manifest.json ------------------------------------------------
$manifest = @{
    version = $version
    url     = "$R2BaseUrl/$zipName"
    sha256  = $sha256
} | ConvertTo-Json

$manifestPath = Join-Path $outputPath "manifest.json"
Set-Content -Path $manifestPath -Value $manifest -Encoding UTF8

Write-Output ""
Write-Output "=== Build termine ==="
Write-Output "  Zip      : $zipPath"
Write-Output "  Manifest : $manifestPath"
Write-Output ""
Write-Output "PROCHAINE ETAPE (manuelle) : uploader ces deux fichiers vers R2 -"
Write-Output "  $zipName          -> doit correspondre exactement a l'url du manifest"
Write-Output "  manifest.json     -> ecrase le manifest.json distant existant"