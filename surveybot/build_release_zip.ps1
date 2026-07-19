# build_release_zip.ps1
# Remplace nuitka_build_release.ps1 pour la phase de déploiement interne (mini-PC
# du parc bare metal). Ne compile plus rien : zippe le code source tel quel.
# Durée attendue : quelques secondes, pas des heures.
#
# À exécuter depuis la racine du projet dev (là où se trouve main.py), PAS depuis
# une machine mini-PC de prod.
#
# Ce que fait ce script :
#   1. Lit BOT_VERSION depuis _license_config.py (source de vérité unique — pas de
#      paramètre de version séparé à maintenir en double).
#   2. Zippe tout le projet SAUF les répertoires/fichiers listés dans $ExcludeDirs /
#      $ExcludeFiles (données runtime, secrets, scripts d'orchestration qui restent
#      à la racine C:\surveybot\ et ne sont jamais remplacés par l'auto-update).
#   3. Calcule le SHA256 du zip produit.
#   4. Réécrit manifest.json (version, url, sha256) prêt à uploader sur R2.
#
# Usage :
#   .\build_release_zip.ps1
#   .\build_release_zip.ps1 -OutputDir "dist_zip" -R2BaseUrl "https://pub-xxx.r2.dev"
#
# Après exécution : uploader manuellement (ou via ta commande rclone/aws s3 cp
# habituelle) le zip produit ET manifest.json vers le bucket R2 — ce script ne
# fait volontairement pas l'upload (pas d'hypothèse sur ton outil de sync R2).

param(
    [string]$ProjectRoot = (Get-Location).Path,
    [string]$OutputDir   = "dist_zip",
    [string]$R2BaseUrl   = "https://pub-565d2bb59d364c1490255c5dddc296aa.r2.dev"
)

$ErrorActionPreference = "Stop"

# ── Dossiers / fichiers exclus du zip ────────────────────────────────────────
# Règle : tout ce qui est donnée runtime, secret, ou script d'orchestration qui
# vit à la racine C:\surveybot\ (jamais à l'intérieur de code\) est exclu.
$ExcludeDirs = @(
    ".venv", "venv", "__pycache__", ".nuitka_cache", "dist_nuitka", $OutputDir,
    ".git", ".claude", "profiles", "pids", "logs", "code.old", "code_new_tmp",
    "Cash"   # dossier de travail local — a confirmer si a inclure ou non
)
$ExcludeFiles = @(
    "accounts.json", "receiver_config.json", "secret.env",
    "manifest.json",
    "launch_all.ps1", "nssm_setup_bot.ps1", "wake_scheduler.ps1",
    "check_zombie_bots.ps1", "run_tabs.ps1", "attach_tab.ps1",
    "nuitka_build_dev.ps1", "nuitka_build_release.ps1", "build_release_zip.ps1",
    "Dockerfile", "fly.toml", "requirements.txt"
)

# ── Lecture de BOT_VERSION depuis _license_config.py ─────────────────────────
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
Write-Output "=== Version détectée : $version ==="

# ── Préparation du dossier de sortie ─────────────────────────────────────────
$outputPath = Join-Path $ProjectRoot $OutputDir
if (Test-Path $outputPath) {
    Remove-Item $outputPath -Recurse -Force
}
New-Item -ItemType Directory -Path $outputPath | Out-Null

$zipName = "surveybot-code-$version.zip"
$zipPath = Join-Path $outputPath $zipName

# ── Construction de la liste des fichiers à zipper ───────────────────────────
Write-Output "=== Recherche des fichiers source (exclusions appliquées) ==="

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
    Write-Error "Aucun fichier source trouvé — vérifier ProjectRoot et les exclusions."
    exit 1
}

Write-Output "  $($allItems.Count) fichier(s) à inclure."

# ── Vérification garde-fou : main.py doit être présent ───────────────────────
$hasMain = $allItems | Where-Object { $_.Name -eq "main.py" -and ($_.FullName.Substring($ProjectRoot.Length).TrimStart("\")) -eq "main.py" }
if (-not $hasMain) {
    Write-Error "main.py absent de la sélection — abandon (l'update_checker.py côté bot refuserait ce zip)."
    exit 1
}

# ── Création du zip ───────────────────────────────────────────────────────────
Write-Output "=== Compression -> $zipPath ==="

# Compress-Archive ne préserve pas facilement les chemins relatifs à partir d'une
# liste hétérogène de FileInfo -> on passe par un dossier de staging temporaire,
# nettoyé après coup. Simple et prévisible, pas d'optimisation prématurée.
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

# ── SHA256 ────────────────────────────────────────────────────────────────────
$sha256 = (Get-FileHash -Path $zipPath -Algorithm SHA256).Hash.ToLower()
Write-Output "=== SHA256 : $sha256 ==="

# ── Écriture du manifest.json ────────────────────────────────────────────────
$manifest = @{
    version = $version
    url     = "$R2BaseUrl/$zipName"
    sha256  = $sha256
} | ConvertTo-Json

$manifestPath = Join-Path $outputPath "manifest.json"
Set-Content -Path $manifestPath -Value $manifest -Encoding UTF8

Write-Output ""
Write-Output "=== Build terminé ==="
Write-Output "  Zip      : $zipPath"
Write-Output "  Manifest : $manifestPath"
Write-Output ""
Write-Output "PROCHAINE ÉTAPE (manuelle) : uploader ces deux fichiers vers R2 —"
Write-Output "  $zipName          -> doit correspondre exactement à l'url du manifest"
Write-Output "  manifest.json     -> écrase le manifest.json distant existant"
Write-Output ""
Write-Output "IMPORTANT : vérifier -ExcludeDirs 'Cash' ci-dessus — inclus ou exclu selon"
Write-Output "que ce dossier contient du code source ou des données de travail locales."