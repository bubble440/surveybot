
## A chaque nouvelle version du projet, modifier le fichier `manifest.json`
## Update la `version` et le `sha256`

# Build du fichier
pyinstaller --onefile --name surveybot --add-data "_license_config.py;." main.py

# modifier la version dans _license_config

# Calculer le SHA du binanire
(Get-FileHash dist\surveybot.exe -Algorithm SHA256).Hash.ToLower()

# Lancer Chrome une première fois avec le bon proxy et pointer vers le dossier cible :
"C:\Program Files\Google\Chrome\Application\chrome.exe" --user-data-dir="C:\surveybot\profiles\bot_001" --proxy-server="http://host:port"



## Etape du build de surveybot.exe
$env:NUITKA_CACHE_DIR = "C:\projects\Surveys\surveybot\.nuitka_cache" # Definir le fichier de cache

# Run de la commande de build
.\nuitka_build_dev.ps1 #Fichier de debug

# lancer main.exe pour test
cd surveybot\dist_nuitka_dev\main.dist
$content = Get-Content -Raw .\launch_all.ps1 -Encoding UTF8
[System.IO.File]::WriteAllText("$PWD\launch_all.ps1", $content, [System.Text.Encoding]::UTF8)
.\launch_all.ps1 -ExePath "C:\projects\Surveys\surveybot\dist_nuitka_dev\main.dist\main.exe" -AccountsFile "C:\surveybot\accounts.json"


# Stopper une exection
Get-Content .\pids\bot_topsurveys_bot_001.pid
Stop-Process -Id <PID> -Force