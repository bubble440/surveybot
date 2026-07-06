# nuitka_build.ps1
# Remplace surveybot.spec (PyInstaller) — build Nuitka onefile.
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
#   .\nuitka_build.ps1
#   .\nuitka_build.ps1 -UseMinGW      # si pas de Visual Studio Build Tools sur la machine

param(
    [switch]$UseMinGW
)

$ErrorActionPreference = "Stop"

$compilerArgs = @()
if ($UseMinGW) {
    $compilerArgs += "--mingw64"
    $compilerArgs += "--assume-yes-for-downloads"
}

python -m nuitka main.py `
    --onefile `
    --output-dir=dist_nuitka `
    --output-filename=surveybot.exe `
    --windows-console-mode=force `
    --follow-imports `
    --assume-yes-for-downloads `
    --include-module=_license_config `
    --include-module=global_config `
    --include-package=Survey `
    --include-package=Management `
    --include-package=preselection `
    --include-package=captcha `
    --include-package-data=playwright `
    --include-package-data=botocore `
    --include-package-data=boto3 `
    --product-name="SurveyBot" `
    --file-version=1.0.0.0 `
    --product-version=1.0.0.0 `
    @compilerArgs

Write-Host ""
Write-Host "Build terminé -> dist_nuitka\main.dist\ ou dist_nuitka\surveybot.exe (onefile)."
Write-Host "IMPORTANT : exécuter le plan de test (auto-update) avant diffusion au parc."
