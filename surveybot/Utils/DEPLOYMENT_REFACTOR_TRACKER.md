

================================================================================
STRUCTURE DES DOSSIERS SUR CHAQUE MINI-PC
================================================================================

C:\surveybot\
  |-- surveybot.exe          <- compile PyInstaller (par licence)
  |-- launch_all.ps1
  |-- accounts.json          <- non versionne
  |-- pids\
  |   |-- bot_001.pid
  |   `-- bot_002.pid
  |-- logs\
  |   |-- bot_001.log
  |   `-- launch_all.log
  `-- profiles\
      |-- bot_001\           <- user-data-dir Chrome (cree manuellement)
      `-- bot_002\

Format accounts.json :
  [
    {
      "account_id": "bot_001",
      "email": "...",
      "password": "...",
      "proxy_url": "http://host:port",
      "proxy_user": "...",
      "proxy_pass": "...",
      "profile_dir": "C:\\surveybot\\profiles\\bot_001"
    }
  ]
  Note : LICENSE_KEY et DATABASE_URL absents — embarques dans le compile.
  Champs optionnels (defauts : Paris/fr-FR) : geo_lat, geo_lon, survey_lang, survey_tz.