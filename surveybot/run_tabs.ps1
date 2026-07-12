# 1 fenêtre Windows Terminal, 1 tab par port, chaque tab exécute tools\attach_tab.ps1

$ports = 9009
$projectDir = "C:\projects\Surveys"
$tabScript  = "C:\projects\Surveys\surveybot\tools\attach_tab.ps1"

if(-not (Test-Path $tabScript)){
  throw "Script introuvable: $tabScript"
}

$wtArgStr = ""
$first = $true

foreach($p in $ports){
  $tab = "new-tab --title `"bot:$p`" -- powershell.exe -NoExit -File `"$tabScript`" -Port $p -ProjectDir `"$projectDir`" -TargetUrl `"https://www.topsurveys.app`" -AttachTabSelector pick"
  if($first){
    $wtArgStr = $tab
    $first = $false
  } else {
    $wtArgStr += " ; $tab"
  }
}

Start-Process "wt.exe" -ArgumentList $wtArgStr
