# 1 fenêtre Windows Terminal, 1 tab par port, chaque tab exécute tools\attach_tab.ps1

$ports = 9220
$projectDir = "C:\projects\Surveys"
$tabScript  = "C:\projects\Surveys\surveybot\tools\attach_tab.ps1"

if(-not (Test-Path $tabScript)){
  throw "Script introuvable: $tabScript"
}

$wtArgs = @()
$first = $true

foreach($p in $ports){
  $cmd = "& `"$tabScript`" -Port $p -ProjectDir `"$projectDir`" -TargetUrl `"$("https://www.topsurveys.app")`" -AttachTabSelector pick"
  if($first){
    $wtArgs += @("new-tab","--title","bot:$p","powershell.exe","-NoExit","-Command",$cmd)
    $first = $false
  } else {
    $wtArgs += @(";","new-tab","--title","bot:$p","powershell.exe","-NoExit","-Command",$cmd)
  }
}

Start-Process "wt.exe" -ArgumentList $wtArgs
