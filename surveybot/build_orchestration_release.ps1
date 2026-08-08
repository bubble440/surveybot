# build_orchestration_release.ps1
#
# ROLE : cote dev UNIQUEMENT. Genere orchestration_manifest.json + une copie
# des fichiers suivis, prets a etre uploades sur R2 (ou tout hebergement HTTP),
# pour etre consommes par sync_orchestration_scripts.ps1 sur chaque machine du
# parc (voir ce script pour le detail du mecanisme et ses limites).
#
# Mecanisme INDEPENDANT de build_release_zip.ps1 / update_checker.py / manifest.json
# (dist_zip\) - ce script ne les modifie pas et ne doit jamais etre fusionne avec eux.
# Ne s'execute jamais sur une machine du parc.
#
# Perimetre suivi (fichiers "files", remplaces automatiquement) :
#   launch_all.ps1, nssm_setup_bot.ps1, wake_scheduler.ps1, check_zombie_bots.ps1,
#   stop_bot.ps1, stop_bot_manual.ps1, rotate_orchestration_logs.ps1, run_tabs.ps1,
#   tools\attach_tab.ps1
# Perimetre "track_only" (hash uniquement, jamais applique automatiquement) :
#   requirements.txt
# Explicitement HORS perimetre (jamais inclus ici) :
#   build_release_zip.ps1, nuitka_build_release.ps1, setup_machine.ps1 - scripts
#   de build/provisioning ponctuelle, machine de dev uniquement.
#
# Usage :
#   .\build_orchestration_release.ps1
#   .\build_orchestration_release.ps1 -OutputDir "dist_orchestration" -R2BaseUrl "https://pub-xxx.r2.dev"
#
# Apres execution : uploader manuellement le contenu du dossier de sortie
# (fichiers + orchestration_manifest.json) vers R2, sous le prefixe "orchestration/"
# utilise dans les URLs generees - ce script ne fait volontairement pas l'upload.

param(
    [string]$ProjectRoot = (Get-Location).Path,
    [string]$OutputDir   = "dist_orchestration",
    [string]$R2BaseUrl   = "https://pub-565d2bb59d364c1490255c5dddc296aa.r2.dev"
)

$ErrorActionPreference = "Stop"

$TrackedFiles = @(
    "launch_all.ps1",
    "nssm_setup_bot.ps1",
    "wake_scheduler.ps1",
    "check_zombie_bots.ps1",
    "stop_bot.ps1",
    "stop_bot_manual.ps1",
    "rotate_orchestration_logs.ps1",
    "set-up.txt",
    "setup_machine.ps1",
    "sync_orchestration_scripts.ps1"
)
$TrackOnlyFiles = @(
    "requirements.txt"
)

function Get-Sha256Hex([string]$Path) {
    return (Get-FileHash -Path $Path -Algorithm SHA256).Hash.ToLower()
}

$outputPath = Join-Path $ProjectRoot $OutputDir
if (Test-Path $outputPath) {
    Remove-Item $outputPath -Recurse -Force
}
New-Item -ItemType Directory -Path $outputPath | Out-Null

$fileEntries = New-Object System.Collections.Generic.List[object]

foreach ($rel in $TrackedFiles) {
    $src = Join-Path $ProjectRoot $rel
    if (-not (Test-Path $src)) {
        Write-Error "Fichier suivi introuvable : $src"
        exit 1
    }

    $sha = Get-Sha256Hex -Path $src

    # Conserve l'arborescence relative dans le dossier de sortie (ex. tools\attach_tab.ps1)
    $dest = Join-Path $outputPath $rel
    $destDir = Split-Path -Path $dest -Parent
    if ($destDir -and -not (Test-Path $destDir)) {
        New-Item -ItemType Directory -Path $destDir -Force | Out-Null
    }
    Copy-Item -Path $src -Destination $dest -Force

    $urlRel = ($rel -replace "\\", "/")
    $fileEntries.Add([ordered]@{
        rel_path = $rel
        sha256   = $sha
        url      = "$R2BaseUrl/orchestration/$urlRel"
    })

    Write-Output "  [OK] $rel -> sha256=$sha"
}

$trackOnlyEntries = New-Object System.Collections.Generic.List[object]

foreach ($rel in $TrackOnlyFiles) {
    $src = Join-Path $ProjectRoot $rel
    if (-not (Test-Path $src)) {
        Write-Error "Fichier suivi (track_only) introuvable : $src"
        exit 1
    }

    $sha = Get-Sha256Hex -Path $src
    $trackOnlyEntries.Add([ordered]@{
        rel_path = $rel
        sha256   = $sha
    })

    Write-Output "  [OK, track_only] $rel -> sha256=$sha"
}

$manifest = [ordered]@{
    generated_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    files        = $fileEntries
    track_only   = $trackOnlyEntries
}

$manifestPath = Join-Path $outputPath "orchestration_manifest.json"
$manifestJson = $manifest | ConvertTo-Json -Depth 5

# Ecriture en UTF-8 SANS BOM (Set-Content -Encoding UTF8 insere toujours un BOM
# en PowerShell 5.1, ce qui casse le parsing JSON automatique d'Invoke-RestMethod
# cote sync_orchestration_scripts.ps1 - le manifeste est alors lu comme invalide,
# champ "files" absent, sans que le contenu du fichier soit reellement en cause).
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($manifestPath, $manifestJson, $utf8NoBom)

Write-Output "=== Manifeste genere (UTF-8 sans BOM) : $manifestPath ==="
Write-Output "=== Fichiers a uploader : $outputPath (prefixe R2 attendu : orchestration/) ==="