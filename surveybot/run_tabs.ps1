# 1 fenêtre Windows Terminal, 1 tab par port, chaque tab exécute tools\attach_tab.ps1

# Usage :
#   powershell -ExecutionPolicy Bypass -File .\run_tabs.ps1 -TargetUrl "https://www.ysense.com" -Platform "ysense"
#   powershell -ExecutionPolicy Bypass -File .\run_tabs.ps1 -TargetUrl "https://www.primeopinion.com" -Platform "primeopinion"
#   powershell -ExecutionPolicy Bypass -File .\run_tabs.ps1 -TargetUrl "https://www.heycash.com/fr-fr" -Platform "heycash"
#   powershell -ExecutionPolicy Bypass -File .\run_tabs.ps1 -TargetUrl "https://www.earnstar.com/fr-fr/" -Platform "earnstar"

param(
  # Valeur par défaut inchangée : préserve le comportement TopSurveys existant
  # pour tout appel sans -TargetUrl. Permet de cibler une autre plateforme
  # (ex: https://www.ysense.com) sans toucher au chemin par défaut.
  [string]$TargetUrl = "https://www.topsurveys.app",
  # Valeur par défaut inchangée : préserve le comportement TopSurveys existant
  # pour tout appel sans -Platform. Ex: -TargetUrl "https://www.primeopinion.com"
  # -Platform "primeopinion".
  [string]$Platform = "topsurveys"
)

$ports = 9009
$projectDir = "C:\projects\Surveys"
$tabScript  = "C:\projects\Surveys\surveybot\tools\attach_tab.ps1"

if(-not (Test-Path $tabScript)){
  throw "Script introuvable: $tabScript"
}

# wt.exe est un stub "App Execution Alias" (reparse point UWP/MSIX), pas un exécutable
# classique. Start-Process échoue à le lancer (0x80070002 - fichier introuvable) même
# avec un chemin résolu explicite. L'opérateur d'appel & fonctionne de façon fiable.
$wtCmd = (Get-Command wt.exe -ErrorAction SilentlyContinue).Source
if(-not $wtCmd){
  throw "wt.exe introuvable (Windows Terminal n'est peut-etre pas installe)."
}

$wtArgs = @()
$first = $true

foreach($p in $ports){
  $tabArgs = @(
    "new-tab","--title","bot:$p","--",
    "powershell.exe","-NoExit","-File",$tabScript,
    "-Port","$p",
    "-ProjectDir",$projectDir,
    "-TargetUrl",$TargetUrl,
    "-Platform",$Platform,
    "-AttachTabSelector","pick"
  )
  if($first){
    $wtArgs += $tabArgs
    $first = $false
  } else {
    $wtArgs += @(";") + $tabArgs
  }
}

& $wtCmd @wtArgs