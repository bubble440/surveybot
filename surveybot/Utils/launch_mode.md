## Étape 1 — Lancer une machine idle (PowerShell)
# Lance l'image bot avec Xvfb démarré mais sans le bot (sleep 3600 = 1h disponible)
flyctl machine run registry.fly.io/surveybot-bot:latest `
  --app surveybot-bot `
  --region cdg `
  --vm-memory 2048 `
  --entrypoint "/bin/bash -c 'Xvfb :99 -screen 0 1920x1080x24 & sleep 3600'"

## Étape 2 — SSH sur la machine (PowerShell)
flyctl ssh console -a surveybot-bot -s
# Sélectionner la machine avec "sleep" dans la liste si plusieurs machines

su - botuser -c 'cd /app && DISPLAY=:99 PYTHONPATH=/app RUN_ENV=prod YSENSE_EMAIL=wilsaah456@gmail.com TWO_CAPTCHA_KEY=ff2f59cd67845abf5c1b7db1c0a17cf2 YSENSE_PASSWORD=p@ssw0rD!123 LOCAL_UNATTENDED=1 SNAP_ENABLED=1 SNAP_R2_ACCOUNT_ID='"$SNAP_R2_ACCOUNT_ID"' SNAP_R2_ACCESS_KEY_ID='"$SNAP_R2_ACCESS_KEY_ID"' SNAP_R2_SECRET_ACCESS_KEY='"$SNAP_R2_SECRET_ACCESS_KEY"' SNAP_R2_BUCKET='"$SNAP_R2_BUCKET"' PROXY_URL='"'http://14abf236340a1:bb82a9e63b@185.134.194.152:12323'"' PROXY_USER=14abf236340a1 PROXY_PASS='"'bb82a9e63b'"' ACCOUNT_ID=topsurveys_bot_001 python tools/ysense_probe.py'