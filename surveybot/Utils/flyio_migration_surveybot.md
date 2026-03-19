# SurveyBot — Migration Fly.io : Coûts & Plan

## Hypothèses de base

| Paramètre | Valeur |
|---|---|
| Bots actifs (cible) | 100 |
| Durée moyenne par session | 2h |
| Sessions par jour par bot | ~3 (avec cooldowns) |
| Heures actives/bot/jour | ~6h |
| **Machine-heures actives/mois** | **100 × 6h × 30 = 18 000h** |
| Région | `cdg` (Paris) |
| Proxies | Brightdata (externes — coût non inclus) |

> **Point clé** : Fly.io facture à la seconde. Les machines bot sont éphémères : elles démarrent,
> travaillent 2h, et s'arrêtent. On ne paie que le temps actif. Le calculator Fly.io (capture
> fournie) montre $48.05/mois pour 1 machine **toujours allumée** — ce n'est pas notre cas.

---

## Dérivation des tarifs horaires (depuis le calculator officiel)

Le calculator montre pour 1 machine always-on (730h), 2 shared vCPU, 8 000 MB RAM, région cdg :
- Compute : $1.58 → **$0.00216/h pour 2 vCPU shared**
- Memory : $44.32 → **$0.00759/h par GB**
- Volume : $0.15 pour 1 GB/mois (non pertinent pour bots éphémères)
- Bandwidth : $2.00 → ~100 GB inclus gratuits, puis $0.02/GB

---

## Scénario A — 1 vCPU shared + 1 GB RAM (risqué pour Chrome)

| Poste | Calcul | Coût/mois |
|---|---|---|
| Compute bots | 18 000h × $0.00108 | $19 |
| Memory bots | 18 000h × $0.00759 | $137 |
| **Sous-total compute** | | **$156** |

> ⚠️ Chrome headless peut dépasser 1 GB sur des pages surveys lourdes. À surveiller via `fly metrics`.

---

## Scénario B — 1 vCPU shared + 2 GB RAM (recommandé)

| Poste | Calcul | Coût/mois |
|---|---|---|
| Compute bots | 18 000h × $0.00108 | $19 |
| Memory bots | 18 000h × $0.01518 | $273 |
| **Sous-total compute** | | **$292** |

---

## Tous les postes — Scénario B complet

### Compute

| Composant | Spec | Actif | Coût/mois |
|---|---|---|---|
| 100 machines bot (éphémères) | 1 shared vCPU + 2 GB | 6h/jour × 30j | $292 |
| 1 machine scheduler (always-on) | 0.25 shared vCPU + 256 MB | 730h | ~$3 |
| **Total compute** | | | **~$295** |

### Stockage & State Store

| Composant | Détail | Coût/mois |
|---|---|---|
| Fly Postgres (managed) | Plan minimal, 1 GB storage | $0 (inclus plan Hobby) → $15 au-delà |
| Volumes bots | Non nécessaire (pas de persistance locale) | $0 |
| **Total stockage** | | **$0–$15** |

### Réseau

| Composant | Calcul | Coût/mois |
|---|---|---|
| Egress internet (trafic surveys) | ~500 GB/mois estimé (100 bots × ~5 MB/session × 3 sessions × 30j = ~45 GB min ; inclut JS/assets) | ~$10 |
| Intra-région (scheduler → bots) | Gratuit | $0 |
| Static Egress IP (optionnel) | $3.60/mois × 1 région | $4 |
| **Total réseau** | | **~$14** |

> Le trafic réel des bots transite via les proxies Brightdata. L'egress Fly.io correspond
> uniquement au flux machine → proxy (requêtes HTTP courtes), pas aux pages entières.
> Estimation conservatrice : **45–100 GB/mois**.

### Secrets & Config

| Composant | AWS actuel | Fly.io |
|---|---|---|
| Secrets Manager | ~$45/mois (100 secrets × $0.40 + lectures) | **$0** (fly secrets natif) |
| Config / env vars | Via task definitions | **$0** (fly.toml + secrets) |

### Scheduler

| Composant | AWS actuel | Fly.io |
|---|---|---|
| EventBridge Scheduler | ~$1/mois | **$0** (cron Fly Machine ou machine légère) |

### Registry d'images

| Composant | AWS actuel | Fly.io |
|---|---|---|
| ECR | ~$1/mois | **$0** (registry Fly.io inclus) |

---

## Récapitulatif mensuel — Scénario B (recommandé)

| Poste | AWS actuel (estimé) | Fly.io |
|---|---|---|
| Compute (bots + scheduler) | ~$590 | **~$295** |
| State store | ~$15 | **~$0–15** |
| Secrets | ~$45 | **$0** |
| Réseau (hors NAT fixe) | ~$54 | **~$14** |
| NAT Gateway (coût fixe) | ~$32 | **$0** |
| Registry | ~$1 | **$0** |
| Scheduler | ~$1 | **$0** |
| **TOTAL** | **~$738** | **~$324** |

> **Économie estimée : ~$414/mois soit ~56% de réduction.**
> La moitié de l'économie vient des Secrets Manager + NAT Gateway — des coûts fixes AWS
> qui n'existent pas chez Fly.io.

---

## Plan de migration

### Vue d'ensemble des substitutions

| AWS | Fly.io équivalent |
|---|---|
| ECS Fargate (tâche bot) | `fly machine run` (machine éphémère) |
| EventBridge Scheduler | Machine scheduler always-on ou `fly machine run --schedule` |
| Secrets Manager | `fly secrets set KEY=VALUE` |
| DynamoDB (account_state) | Fly Postgres |
| ECR | Registry Fly.io intégré (ou ghcr.io) |
| NAT Gateway | Egress natif Fly ($0.02/GB) |
| CloudWatch Logs | `fly logs` + intégration Papertrail/Logtail (optionnel) |

---

### Étape 1 — Préparer l'environnement Fly.io

```bash
# Installer le CLI
curl -L https://fly.io/install.sh | sh

# Connexion
fly auth login

# Créer l'org et vérifier la région
fly orgs list
fly platform regions  # confirmer que cdg (Paris) est disponible
```

---

### Étape 2 — Migrer le state store (DynamoDB → Postgres)

```bash
# Créer une instance Postgres managée Fly
fly postgres create --name surveybot-db --region cdg --initial-cluster-size 1 --vm-size shared-cpu-1x --volume-size 1

# Noter la connection string retournée (DATABASE_URL)
```

Modifier `account_state.py` : remplacer les appels DynamoDB par des requêtes Postgres.
Le schéma reste simple : une table `account_state` avec les mêmes champs.

---

### Étape 3 — Migrer les secrets

```bash
# Pour chaque compte/bot
fly secrets set \
  ACCOUNT_EMAIL=xxx \
  ACCOUNT_PASSWORD=xxx \
  PROXY_URL=xxx \
  OPENAI_API_KEY=xxx \
  DATABASE_URL=xxx \
  --app surveybot-bot
```

Modifier `secret_loader.py` : lire depuis les variables d'environnement (os.environ)
au lieu de Secrets Manager. Fly.io injecte automatiquement les secrets comme env vars.

---

### Étape 4 — Adapter le Dockerfile

```dockerfile
# Aucun changement structurel nécessaire.
# Supprimer boto3/botocore si plus utilisés (DynamoDB + Secrets Manager).
# Vérifier que Chrome/Chromium est bien installé dans l'image.

# Exemple d'ajout si pas déjà présent :
RUN apt-get install -y chromium chromium-driver
```

```bash
# Builder et pusher l'image
fly auth docker
docker build -t registry.fly.io/surveybot-bot:latest .
docker push registry.fly.io/surveybot-bot:latest
```

---

### Étape 5 — Créer le fly.toml (app bot)

```toml
app = "surveybot-bot"
primary_region = "cdg"

[build]
  image = "registry.fly.io/surveybot-bot:latest"

[env]
  LOG_LEVEL = "INFO"
  ENV = "prod"

# Pas de [http_service] — les bots ne servent pas de trafic HTTP entrant

[[vm]]
  cpu_kind = "shared"
  cpus = 1
  memory_mb = 2048
```

---

### Étape 6 — Migrer le scheduler

Le scheduler lance `fly machine run` pour chaque bot au lieu de `ecs:run_task`.

```python
# scheduler.py — logique principale
import subprocess

def launch_bot(account_id: str):
    cmd = [
        "fly", "machine", "run",
        "--app", "surveybot-bot",
        "--region", "cdg",
        "--vm-memory", "2048",
        "--env", f"ACCOUNT_ID={account_id}",
        "--rm",          # détruire la machine après exit
        "--detach",      # non-bloquant
        "registry.fly.io/surveybot-bot:latest"
    ]
    subprocess.run(cmd, check=True)
```

Le scheduler lui-même tourne comme une machine Fly.io always-on légère
(0.25 vCPU + 256 MB) avec un cron interne toutes les 5 minutes.

---

### Étape 7 — Validation progressive

```
Phase 1 : 1 bot en local → fly machine run manuel → vérifier logs + DB
Phase 2 : 5 bots simultanés → mesurer RAM réelle dans fly metrics
Phase 3 : 20 bots → valider coûts sur la semaine
Phase 4 : 100 bots → production complète
```

```bash
# Suivre les logs en temps réel
fly logs --app surveybot-bot

# Métriques RAM (crucial pour valider 1GB vs 2GB)
fly metrics --app surveybot-bot
```

---

## Points d'attention

**RAM Chrome** : surveiller impérativement lors de la phase 2. Si une machine dépasse
régulièrement 1.8 GB, passer à 3 GB. L'impact coût est +$137/mois.

**Fly machine --rm** : s'assurer que le bot écrit bien son état final dans Postgres
*avant* d'appeler `exit()`, sinon la machine disparaît avec les données en mémoire.

**Secrets par bot** : si chaque bot a des credentials différents, utiliser soit
des apps Fly séparées (une par bot), soit passer les credentials en `--env` au
moment du `fly machine run`. Option `--env` recommandée pour sa simplicité.

**Pas de VPC/réseau privé nécessaire** : les bots n'ont pas besoin de se parler.
Fly Private Network (WireGuard) est disponible gratuitement si besoin futur.

**Logs** : `fly logs` est basique. Pour du volume (100 bots), envisager
Logtail (free tier généreux) ou Papertrail via drain de logs Fly.
