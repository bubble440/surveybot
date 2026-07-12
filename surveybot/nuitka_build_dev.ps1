# nuitka_build_dev.ps1
# Build STANDALONE (pas onefile) — pour itérer rapidement pendant le dev/debug.
# Pas de packaging onefile => gain de temps important à chaque test.
# Le résultat n'est PAS destiné à la diffusion (utiliser nuitka_build_release.ps1 pour ça).
#
# Prérequis machine de build (une fois) :
#   1. Python 3.11 (même version que le venv du projet) + venv activé.
#   2. pip install nuitka ordered-set zstandard
#   3. Un compilateur C :
#        - Option A (recommandée si Visual Studio Build Tools déjà présent) :
#          "Desktop development with C++" installé -> Nuitka détecte cl.exe automatiquement.
#        - Option B (zéro install manuel) : ajouter --mingw64 --assume-yes-for-downloads
#          ci-dessous -> Nuitka télécharge et utilise son propre MinGW64 portable.
#
# Usage :
#   .\nuitka_build_dev.ps1
#   .\nuitka_build_dev.ps1 -UseMinGW      # si pas de Visual Studio Build Tools sur la machine

param(
    [switch]$UseMinGW
)

$ErrorActionPreference = "Stop"

# Cache persistant Nuitka — évite de recompiler les modules C inchangés à chaque run.
$env:NUITKA_CACHE_DIR = "C:\surveybot\.nuitka_cache"

$compilerArgs = @()
if ($UseMinGW) {
    $compilerArgs += "--mingw64"
    $compilerArgs += "--assume-yes-for-downloads"
}

# Le dossier driver/ de Playwright (binaire Node.js + cli.js) n'est pas du code Python :
# --include-package-data=playwright ne le capture pas de façon fiable selon les versions
# de Nuitka. On le localise dynamiquement (chemin dépendant du venv) et on le copie tel quel.
$playwrightDriverSrc = python -c "import playwright, os; print(os.path.join(os.path.dirname(playwright.__file__), 'driver'))"
if (-not (Test-Path $playwrightDriverSrc)) {
    Write-Host "[ERREUR] Dossier driver Playwright introuvable : $playwrightDriverSrc"
    exit 1
}
$dataDirArg = "--include-data-dir=$playwrightDriverSrc=playwright/driver"

python -m nuitka main.py `
    --standalone `
    --output-dir=dist_nuitka_dev `
    --windows-console-mode=force `
    --follow-imports `
    --assume-yes-for-downloads `
    --lto=no `
    --jobs=$([Environment]::ProcessorCount) `
    --include-module=_license_config `
    --include-module