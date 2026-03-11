# 1 fenêtre Windows Terminal, 1 tab par port, chaque tab exécute tools\attach_tab.ps1

$ports = 9222
$projectDir = "C:\projects\Surveys"
$tabScript  = "C:\projects\Surveys\surveybot\tools\attach_tab.ps1"

Write-Host ""
Write-Host "Type d'attach à lancer :"
Write-Host "  1) attach preselection"
Write-Host "  2) attach resolution (comportement actuel)"

$attachChoice = (Read-Host "Choix [1/2, défaut=2]").Trim()
$attachRoute = "resolution"
if($attachChoice -eq "1"){
  $attachRoute = "preselection"
}
Write-Host "[ATTACH] Route sélectionnée: $attachRoute"

if(-not (Test-Path $tabScript)){
  throw "Script introuvable: $tabScript"
}

$wtArgs = @()
$first = $true

foreach($p in $ports){
  $cmd = "& `"$tabScript`" -Port $p -ProjectDir `"$projectDir`" -TargetUrl `"$("https://www.topsurveys.app")`" -AttachTabSelector pick -AttachRoute $attachRoute"
  if($first){
    $wtArgs += @("new-tab","--title","bot:$p","powershell.exe","-NoExit","-Command",$cmd)
    $first = $false
  } else {
    $wtArgs += @(";","new-tab","--title","bot:$p","powershell.exe","-NoExit","-Command",$cmd)
  }
}

Start-Process "wt.exe" -ArgumentList $wtArgs
