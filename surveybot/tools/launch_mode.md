## Étape 1 — Lancer une machine idle (PowerShell)
# Lance l'image bot avec Xvfb démarré mais sans le bot (sleep 3600 = 1h disponible)
flyctl machine run registry.fly.io/surveybot-bot:latest `
  --app surveybot-bot `
  --region cdg `
  --vm-memory 2048 `
  --entrypoint "/bin/bash -c 'Xvfb :99 -screen 0 1920x1080x24 & sleep 3600'"
# Note l'ID de machine affiché (ex: d8901e2c5ee738)

## Étape 2 — SSH sur la machine (PowerShell)
flyctl ssh console -a surveybot-bot -s
# Sélectionner la machine avec "sleep" dans la liste si plusieurs machines

## Étape 3 — Dans le shell Linux (une commande à la fois)
su - botuser -c 'cd /app && DISPLAY=:99 PYTHONPATH=/app RUN_ENV=prod LOCAL_UNATTENDED=1 SNAP_ENABLED=1 SNAP_R2_ACCOUNT_ID='"$SNAP_R2_ACCOUNT_ID"' SNAP_R2_ACCESS_KEY_ID='"$SNAP_R2_ACCESS_KEY_ID"' SNAP_R2_SECRET_ACCESS_KEY='"$SNAP_R2_SECRET_ACCESS_KEY"' SNAP_R2_BUCKET='"$SNAP_R2_BUCKET"' PROXY_URL='"'http://14abf236340a1:bb82a9e63b@185.134.194.152:12323'"' PROXY_USER=14abf236340a1 PROXY_PASS='"'bb82a9e63b'"' ACCOUNT_ID=topsurveys_bot_001 python main.py'

su - botuser -c 'cd /app && DISPLAY=:99 PYTHONPATH=/app RUN_ENV=prod LOCAL_UNATTENDED=1 SNAP_ENABLED=1 SNAP_R2_ACCOUNT_ID='"$SNAP_R2_ACCOUNT_ID"' SNAP_R2_ACCESS_KEY_ID='"$SNAP_R2_ACCESS_KEY_ID"' SNAP_R2_SECRET_ACCESS_KEY='"$SNAP_R2_SECRET_ACCESS_KEY"' SNAP_R2_BUCKET='"$SNAP_R2_BUCKET"' PROXY_URL='"'http://14abf236340a1:bb82a9e63b@185.134.194.152:12323'"' PROXY_USER=14abf236340a1 PROXY_PASS='"'bb82a9e63b'"' ACCOUNT_ID=topsurveys_bot_001 python tools/multi_access_check.py'

# Les PNG sont sauvegardés dans /tmp/fp_*.png
# L'upload R2 échouera si SNAP_R2_ACCOUNT_ID n'est pas défini — c'est normal, les PNG locaux suffisent

## Étape 4 — Récupérer les PNG (nouveau terminal PowerShell, pas dans le SSH, en gardant le SSH ouvert)
flyctl ssh sftp get /tmp/prod_canvas.png -a surveybot-bot
flyctl ssh sftp get /tmp/prod_webgl.png -a surveybot-bot
flyctl ssh sftp get /tmp/prod_javascript.png -a surveybot-bot
flyctl ssh sftp get /tmp/prod_ip.png -a surveybot-bot
# Les fichiers atterrissent dans le répertoire courant PowerShell
