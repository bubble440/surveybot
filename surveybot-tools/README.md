# surveybot-tools

Image Fly.io légère pour les scripts de diagnostic autonomes.
Cycle de build indépendant du bot principal.

## Contenu

- `tools/ysense_probe.py`   — test login + sélection survey ySense
- `tools/snap_diagnostic.py` — diagnostic pipeline screenshot → R2

## Ajouter un script

Déposer le fichier dans `tools/`. Zéro autre modification si le script
n'importe que stdlib + playwright.

---

## Build & push

```powershell
# Depuis surveybot-tools/
fly auth docker
docker build -t registry.fly.io/surveybot-tools:latest .
docker push registry.fly.io/surveybot-tools:latest
```

## Créer l'app (une seule fois)

```bash
fly apps create surveybot-tools --org surveybot
```

---

## Lancer un script

### ysense_probe.py

```bash
fly machine run registry.fly.io/surveybot-tools:latest \
  --app surveybot-tools \
  --region cdg \
  --vm-size shared-cpu-1x \
  --vm-memory 1024 \
  --env YSENSE_EMAIL=xxx \
  --env YSENSE_PASSWORD=xxx \
  --env PROXY_URL=http://host:port \
  --env PROXY_USER=xxx \
  --env PROXY_PASS=xxx \
  --env SNAP_ENABLED=1 \
  --env SNAP_R2_ACCOUNT_ID=xxx \
  --env SNAP_R2_ACCESS_KEY_ID=xxx \
  --env SNAP_R2_SECRET_ACCESS_KEY=xxx \
  --env SNAP_R2_BUCKET=surveybot-snaps \
  --env ACCOUNT_ID=topsurveys_bot_001 \
  --rm \
  -- python tools/ysense_probe.py
```

### snap_diagnostic.py

```bash
fly machine run registry.fly.io/surveybot-tools:latest \
  --app surveybot-tools \
  --region cdg \
  --vm-size shared-cpu-1x \
  --vm-memory 1024 \
  --env DISPLAY=:99 \
  --env ACCOUNT_ID=topsurveys_bot_001 \
  --rm \
  -- python tools/snap_diagnostic.py
```

## Voir les logs

```bash
# Logs en temps réel pendant l'exécution
fly logs --app surveybot-tools
```

## Récupérer un fichier produit dans /tmp

```bash
# Pendant que la machine tourne (avant --rm)
# Retirer --rm et noter le MACHINE_ID affiché au lancement, puis :
fly ssh console --app surveybot-tools --machine MACHINE_ID
# Dans le shell :
cat /tmp/snap_diag_playwright.png > /dev/stdout | base64
# Ou via sftp :
fly ssh sftp get /tmp/snap_diag_playwright.png -a surveybot-tools
```

> **Note** : avec `--rm`, la machine est détruite à la fin.
> Pour inspecter /tmp, retirer `--rm` et stopper la machine manuellement après récup.
