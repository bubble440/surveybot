# SurveyBot — Documentation Infrastructure Production

> **Document de référence opérationnel** — décrit l'état exact de l'infrastructure prod,
> le fonctionnement interne du bot, et les procédures de lancement/arrêt.
> Rédigé en mars 2026. Déploiement : Fly.io (région cdg — Paris).
> À mettre à jour après tout changement d'infrastructure.

---

## Table des matières

1. [Vue d'ensemble](#1-vue-densemble)
2. [Infrastructure Fly.io](#2-infrastructure-flyio)
3. [Stack technique du bot](#3-stack-technique-du-bot)
4. [Flux d'exécution complet](#4-flux-dexécution-complet)
5. [Variables d'environnement](#5-variables-denvironnement)
6. [État Postgres — schéma complet](#6-état-postgres--schéma-complet)
7. [RuntimeGuard — comportement prod](#7-runtimeguard--comportement-prod)
8. [Lancer le bot en production](#8-lancer-le-bot-en-production)
9. [Arrêter le bot en production](#9-arrêter-le-bot-en-production)
10. [Ajouter un compte bot](#10-ajouter-un-compte-bot)
11. [Build et déploiement d'une nouvelle image](#11-build-et-déploiement-dune-nouvelle-image)
12. [Coûts et ressources à surveiller](#12-coûts-et-ressources-à-surveiller)
13. [Checklist de santé infrastructure](#13-checklist-de-santé-infrastructure)
14. [Points d'architecture importants](#14-points-darchitecture-importants)

---

## 1. Vue d'ensemble

### Objectif
SurveyBot automatise la complétion de sondages sur TopSurveys et les plateformes partenaires.
Chaque bot = 1 compte TopSurveys = 1 machine Fly.io éphémère = 1 proxy externe.
**Cible : ~100 bots en parallèle.**

### Architecture simplifiée

```
Machine Fly.io : surveybot-scheduler (always-on, boucle toutes les 5 min)
    ├─ ACCOUNTS_JSON   (secret Fly — liste des comptes + credentials)
    └─► Machine Fly.io : surveybot-bot × N  (1 par compte, éphémère, auto-destroy)
            ├─ Variables d'env    (credentials injectés par le scheduler au lancement)
            ├─ Postgres           (état partagé — source de vérité)
            └─ Chrome headless (Playwright + Selenium)
                    └─ Proxy externe → Site de sondage
```

### Logique scheduler / bot — règle fondamentale

```
Scheduler :
  1. Récupère la liste des comptes depuis ACCOUNTS_JSON
  2. Pour chaque compte : lance une machine bot avec ses credentials en env vars
  3. C'est tout — le scheduler n'écrit jamais dans la DB

Bot :
  1. Démarre, lit son état dans Postgres
  2. Si status != "idle" → exit(0) immédiatement (un bot tourne déjà pour ce compte)
  3. Si cooldown actif → exit(0)
  4. Si banni → exit(0)
  5. Travaille, écrit dans Postgres
  6. Met status="idle" + cooldown à la fin, exit(0)

=> La DB n'est écrite que par le bot lui-même. Une seule source d'écriture par compte.
```

---

## 2. Infrastructure Fly.io

### 2.1 Applications Fly.io

**Organisation** : `surveybot`
**Région** : `cdg` (Paris)

| App | Image | Rôle | CPU | RAM | Type |
|-----|-------|------|-----|-----|------|
| `surveybot-scheduler` | `registry.fly.io/surveybot-scheduler:latest` | Boucle toutes les 5 min, lance les machines bot | 0.25 shared | 256 MB | always-on |
| `surveybot-bot` | `registry.fly.io/surveybot-bot:latest` | Bot principal (Chrome + Playwright + Selenium + OpenAI) | 1 shared | 2048 MB | éphémère (auto-destroy) |

---

### 2.2 Registry d'images

Fly.io fournit un registry intégré. Pas de coût de stockage séparé.

| Image | Tag | Rôle |
|-------|-----|------|
| `registry.fly.io/surveybot-bot` | `latest` | Image du bot |
| `registry.fly.io/surveybot-scheduler` | `latest` | Image du scheduler |

> Le tag `latest` est mutable. Un `docker push :latest` écrase l'image précédente.
> Les prochaines machines lancées par le scheduler utiliseront automatiquement la nouvelle image.

**Script de build + push (PowerShell) :**

```powershell
# Authentification Fly registry
fly auth docker

# Bot
docker build -t registry.fly.io/surveybot-bot:latest ./surveybot
docker push registry.fly.io/surveybot-bot:latest

# Scheduler
docker build -t registry.fly.io/surveybot-scheduler:latest ./scheduler
docker push registry.fly.io/surveybot-scheduler:latest
```

---

### 2.3 Scheduler — machine always-on

Le scheduler est une machine Fly.io qui tourne en continu avec une boucle interne `time.sleep(300)`.
Pas de Cloud Scheduler externe, pas d'EventBridge. La machine redémarre automatiquement si elle crashe (`restart.policy = always`).

**fly.toml du scheduler :**
```toml
app = "surveybot-scheduler"
primary_region = "cdg"

[build]
  image = "registry.fly.io/surveybot-scheduler:latest"

[[vm]]
  cpu_kind = "shared"
  cpus = 1
  memory_mb = 256

[env]
  RUN_ENV = "prod"
  FLY_REGION = "cdg"
  LOOP_INTERVAL_SEC = "300"
```

---

### 2.4 Secrets Fly.io

Fly.io injecte les secrets comme variables d'environnement dans les machines au démarrage.
**Pas de Secret Manager externe, pas de coût par lecture.**

#### Secrets du scheduler (`--app surveybot-scheduler`)

| Secret | Description |
|--------|-------------|
| `FLY_API_TOKEN` | Token Fly.io pour appeler la Machines API |
| `FLY_BOT_APP` | Nom de l'app bot (`surveybot-bot`) |
| `FLY_BOT_IMAGE` | Image bot (`registry.fly.io/surveybot-bot:latest`) |
| `DATABASE_URL` | Connection string Postgres |
| `STATE_BACKEND` | `postgres` |
| `ACCOUNTS_JSON` | Liste JSON de tous les comptes (voir format ci-dessous) |

**Format `ACCOUNTS_JSON` :**
```json
[
  {
    "ACCOUNT_ID": "bot_001",
    "EMAIL": "bot001@example.com",
    "PASSWORD": "...",
    "PROXY_URL": "http://host:port",
    "PROXY_USER": "...",
    "PROXY_PASS": "...",
    "GEO_LAT": "48.8566",
    "GEO_LON": "2.3522",
    "SURVEY_LANG": "fr-FR",
    "SURVEY_TZ": "Europe/Paris"
  }
]
```

**Créer ou mettre à jour les secrets :**
```bash
fly secrets set FLY_API_TOKEN=xxx DATABASE_URL=xxx \
  ACCOUNTS_JSON='[{"ACCOUNT_ID":"bot_001",...}]' \
  --app surveybot-scheduler
```

---

### 2.5 Postgres (state store)

Fly.io propose un Postgres managé (Fly Postgres).

**Créer l'instance :**
```bash
fly postgres create \
  --name surveybot-db \
  --region cdg \
  --initial-cluster-size 1 \
  --vm-size shared-cpu-1x \
  --volume-size 1
```

**Attacher au scheduler (injecte DATABASE_URL automatiquement) :**
```bash
fly postgres attach surveybot-db --app surveybot-scheduler
```

La table `account_state` est créée automatiquement par `account_state.py` au premier accès (`_pg_ensure_table()`).

---

### 2.6 Réseau

Fly.io n'a pas de NAT Gateway séparé. Le trafic sortant des machines est routé nativement.

| Poste | Détail | Coût |
|-------|--------|------|
| Egress internet | $0.02/GB (région Europe) | À l'usage |
| Static Egress IP (optionnel) | $3.60/mois si IP fixe requise | Optionnel |
| Intra-app (scheduler → Machines API) | Gratuit | $0 |

> Les proxies Brightdata sont externes. Le trafic survey transite via le proxy — l'egress Fly.io
> correspond uniquement aux appels HTTP courts machine → proxy.

---

## 3. Stack technique du bot

### 3.1 Browser — architecture Playwright + Selenium hybride

**Étape 1 — Playwright lance Chrome**
- Gère l'authentification proxy nativement
- Configure : proxy, langue (`fr-FR`), timezone (`Europe/Paris`), géolocalisation (Paris par défaut)
- Injecte des overrides DevTools anti-détection : `navigator.webdriver = undefined`, platform `Win32`
- Lance Chrome en mode `--headless=new` en prod (Docker/Fly.io)
- Expose un port de debugging aléatoire (42000–52000)

**Étape 2 — Selenium s'attache au Chrome existant**
- Se connecte via `debuggerAddress: 127.0.0.1:{port}`
- Toute la logique d'interaction (DOM, clics, OpenAI) reste dans Selenium
- Les objets Playwright sont attachés au driver pour maintenir la session vivante

### 3.2 OpenAI

- Modèle : `gpt-4o-mini`
- API : `chat.completions.create()` (direct, pas Assistants API)
- Clé injectée via les variables d'env du compte

### 3.3 Notifications Telegram

- Chaque bot a ses propres credentials Telegram dans son entrée `ACCOUNTS_JSON`
- Notifications envoyées via HTTP direct vers l'API Telegram (`notifier.py`)

---

## 4. Flux d'exécution complet

```
1. Machine "surveybot-scheduler" (always-on) se réveille toutes les 5 min

2. scheduler/main.py :
   a. Lit ACCOUNTS_JSON → liste des account_id
   b. Pour chaque compte :
      - Charge les credentials depuis ACCOUNTS_JSON
      - Appelle fly.start_task(account_id, account)

3. fly.start_task() :
   a. POST https://api.machines.dev/v1/apps/surveybot-bot/machines
   b. Injecte les credentials du compte en env vars
   c. La machine démarre, se détruit automatiquement après exit (auto_destroy=True)

4. Machine bot démarre :
   a. Lit son état dans Postgres (account_state.py)
   b. Si status != "idle" → exit(0) immédiatement
   c. Si cooldown actif → exit(0)
   d. Si banni → exit(0)
   e. Lance Chrome via Playwright
   f. Se connecte à TopSurveys
   g. Boucle de complétion de sondages
   h. RuntimeGuard surveille les conditions d'arrêt

5. En fin de session :
   a. Bot met à jour Postgres (status=idle, cooldown, earnings)
   b. Machine exit(0) → auto-destroy → ressources libérées

6. Au prochain tick (5 min), le scheduler relance une machine pour ce compte.
   Si un bot tourne encore (status != idle), la nouvelle machine exit(0) immédiatement.
```

---

## 5. Variables d'environnement

### Variables communes (bot + scheduler)

| Variable | Valeur | Description |
|----------|--------|-------------|
| `RUN_ENV` | `prod` | Environnement actif |
| `STATE_BACKEND` | `postgres` | Backend état |
| `DATABASE_URL` | `postgres://...` | Connection string Postgres (injecté par Fly) |

### Variables spécifiques au scheduler

| Variable | Valeur | Description |
|----------|--------|-------------|
| `FLY_API_TOKEN` | `...` | Token Machines API |
| `FLY_BOT_APP` | `surveybot-bot` | Nom de l'app bot |
| `FLY_BOT_IMAGE` | `registry.fly.io/surveybot-bot:latest` | Image à lancer |
| `FLY_REGION` | `cdg` | Région des machines bot |
| `FLY_VM_MEMORY` | `2048` | RAM des machines bot (MB) |
| `LOOP_INTERVAL_SEC` | `300` | Intervalle entre les ticks (secondes) |
| `ACCOUNTS_JSON` | `[{...}]` | Liste JSON des comptes |

### Variables injectées par le scheduler dans chaque machine bot

Ces variables sont passées en `env` au moment du `POST /machines` :

| Variable | Source |
|----------|--------|
| `ACCOUNT_ID` | ACCOUNTS_JSON |
| `EMAIL` | ACCOUNTS_JSON |
| `PASSWORD` | ACCOUNTS_JSON |
| `PROXY_URL` | ACCOUNTS_JSON |
| `PROXY_USER` | ACCOUNTS_JSON |
| `PROXY_PASS` | ACCOUNTS_JSON |
| `GEO_LAT` | ACCOUNTS_JSON |
| `GEO_LON` | ACCOUNTS_JSON |
| `SURVEY_LANG` | ACCOUNTS_JSON |
| `SURVEY_TZ` | ACCOUNTS_JSON |
| `STATE_BACKEND` | Hérité du scheduler |
| `DATABASE_URL` | Hérité du scheduler |

---

## 6. État Postgres — schéma complet

**Table** : `account_state`
**Clé primaire** : `account_id`

| Champ | Type | Description |
|-------|------|-------------|
| `account_id` | TEXT PK | Identifiant unique du compte |
| `version` | INTEGER | Optimistic locking — incrémenté à chaque écriture |
| `banned` | BOOLEAN | Si True, le bot exit(0) sans travailler |
| `status` | TEXT | `idle` / `running` / `starting` |
| `lock_owner` | TEXT | ID de la machine qui détient le lock |
| `lock_until_ts` | TEXT (ISO) | Expiration du lock |
| `cooldown_until_ts` | TEXT (ISO) | Ne pas relancer avant cette date |
| `last_heartbeat_ts` | TEXT (ISO) | Dernier heartbeat du bot actif |
| `last_stop_reason` | TEXT | Raison du dernier arrêt |
| `last_boot_ts` | TEXT (ISO) | Dernier démarrage |
| `daily_earned` | JSONB | `{"2026-03-18": 1.23}` — revenus par jour |
| `total_earned` | FLOAT | Revenus totaux cumulés |
| `updated_ts` | TIMESTAMPTZ | Dernière mise à jour |

> La table est créée automatiquement au premier accès par `_pg_ensure_table()` dans `account_state.py`.
> Pas d'action manuelle requise.

---

## 7. RuntimeGuard — comportement prod

Le RuntimeGuard surveille en continu les conditions d'arrêt du bot. En prod Fly.io :

- Condition détectée → soft restart tenté
- Si soft restart échoue → `SystemExit` → la machine Fly se termine proprement
- `auto_destroy=True` → la machine est détruite automatiquement après exit
- Le scheduler relancera au prochain tick si le cooldown est expiré
- L'état est mis à jour dans Postgres avant l'arrêt

---

## 8. Lancer le bot en production

### Prérequis avant tout lancement

- [ ] Images `surveybot-bot:latest` et `surveybot-scheduler:latest` buildées et pushées
- [ ] Secrets du scheduler settés (`FLY_API_TOKEN`, `DATABASE_URL`, `ACCOUNTS_JSON`, etc.)
- [ ] Postgres `surveybot-db` créé et attaché au scheduler
- [ ] Machine scheduler déployée et running

### Lancement automatique (mode prod)

La machine scheduler tourne en continu. Elle se déclenche automatiquement toutes les 5 minutes.
Aucune action manuelle requise si les prérequis sont satisfaits.

```bash
# Vérifier que le scheduler tourne
fly status --app surveybot-scheduler

# Voir les logs du scheduler en temps réel
fly logs --app surveybot-scheduler

# Voir les machines bot actives
fly machines list --app surveybot-bot
```

### Déclenchement manuel immédiat (test / debug)

```bash
# Lancer manuellement une machine bot pour un compte spécifique
fly machine run \
  --app surveybot-bot \
  --region cdg \
  --vm-memory 2048 \
  --env ACCOUNT_ID=bot_001 \
  --env EMAIL=xxx \
  --env PASSWORD=xxx \
  --env PROXY_URL=xxx \
  --env STATE_BACKEND=postgres \
  --env DATABASE_URL=xxx \
  --rm \
  registry.fly.io/surveybot-bot:latest
```

### Vérifier qu'un bot tourne correctement

```bash
# Machines bot actives
fly machines list --app surveybot-bot

# Logs d'une machine spécifique
fly logs --app surveybot-bot --machine MACHINE_ID

# État dans Postgres
fly postgres connect -a surveybot-db
SELECT account_id, status, lock_owner, cooldown_until_ts, updated_ts FROM account_state;
```

---

## 9. Arrêter le bot en production

### Arrêt propre automatique (cas normaux)

Le bot s'arrête seul quand RuntimeGuard détecte une condition d'arrêt.
La machine Fly se détruit automatiquement (`auto_destroy=True`). Aucune action requise.
Le scheduler relancera au prochain tick si le cooldown est expiré.

### Pause temporaire (arrêt du relancement automatique)

```bash
# Suspendre la machine scheduler (arrête la boucle)
fly machine suspend MACHINE_ID --app surveybot-scheduler
```

Les bots en cours terminent leur session naturellement. Aucun nouveau bot ne sera lancé.

Pour reprendre :
```bash
fly machine start MACHINE_ID --app surveybot-scheduler
```

### Arrêt d'urgence (immédiat)

```bash
# 1. Suspendre le scheduler
fly machine suspend MACHINE_ID --app surveybot-scheduler

# 2. Lister les machines bot actives
fly machines list --app surveybot-bot

# 3. Stopper une machine spécifique
fly machine stop MACHINE_ID --app surveybot-bot
```

> Un arrêt forcé peut laisser `status="running"` dans Postgres.
> Le bot suivant lancé pour ce compte détectera `status != "idle"` et exitera.
> Pour forcer une reprise immédiate, réinitialiser manuellement dans Postgres :
> ```sql
> UPDATE account_state SET status='idle', lock_owner='', lock_until_ts='1970-01-01T00:00:00' WHERE account_id='bot_001';
> ```

### Mettre un seul compte en pause

```bash
fly postgres connect -a surveybot-db
UPDATE account_state SET cooldown_until_ts='2099-01-01T00:00:00' WHERE account_id='bot_001';
```

Le bot lancé pour ce compte détectera le cooldown et exitera immédiatement.

---

## 10. Ajouter un compte bot

**Une seule action requise** : ajouter l'entrée dans `ACCOUNTS_JSON`.

```bash
# Lire le secret actuel, ajouter le compte, re-setter
fly secrets set ACCOUNTS_JSON='[
  {"ACCOUNT_ID":"bot_001",...},
  {"ACCOUNT_ID":"bot_002","EMAIL":"...","PASSWORD":"...","PROXY_URL":"...","PROXY_USER":"...","PROXY_PASS":"...","GEO_LAT":"48.8566","GEO_LON":"2.3522","SURVEY_LANG":"fr-FR","SURVEY_TZ":"Europe/Paris"}
]' --app surveybot-scheduler
```

La table Postgres `account_state` est auto-créée pour ce compte au premier démarrage du bot.
Aucune autre action requise.

---

## 11. Build et déploiement d'une nouvelle image

### Bot (`./surveybot/`)

```powershell
fly auth docker
docker build -t registry.fly.io/surveybot-bot:latest ./surveybot
docker push registry.fly.io/surveybot-bot:latest
```

### Scheduler (`./scheduler/`)

```powershell
fly auth docker
docker build -t registry.fly.io/surveybot-scheduler:latest ./scheduler
docker push registry.fly.io/surveybot-scheduler:latest

# Redémarrer la machine scheduler pour prendre la nouvelle image
fly machine restart MACHINE_ID --app surveybot-scheduler
```

> Le tag `latest` est écrasé à chaque push. Les prochaines machines bot lancées par le scheduler
> utiliseront automatiquement la nouvelle image — aucune mise à jour de configuration requise.

---

## 12. Coûts et ressources à surveiller

### Fly.io — estimation mensuelle (100 bots, 6h actives/jour)

| Service | Coût/mois | Note |
|---------|-----------|------|
| Machines bot (compute) | ~$19 | 18 000h × $0.00108/h (1 shared vCPU) |
| Machines bot (RAM 2GB) | ~$273 | 18 000h × $0.01518/h |
| Machine scheduler (always-on) | ~$3 | 0.25 vCPU + 256MB |
| Postgres managé | ~$0–15 | Plan minimal, 1GB |
| Egress réseau | ~$14 | ~700GB/mois estimé à $0.02/GB |
| Secrets | $0 | Inclus natif |
| Registry | $0 | Inclus natif |
| **Total** | **~$309/mois** | vs ~$738/mois sur AWS |

---

## 13. Checklist de santé infrastructure

À vérifier avant chaque activation en prod :

```
Fly.io — Machines
[ ] Machine "surveybot-scheduler" : status=running (fly status --app surveybot-scheduler)
[ ] App "surveybot-bot" : existe (fly apps list)

Fly.io — Images
[ ] registry.fly.io/surveybot-bot:latest : buildée et pushée
[ ] registry.fly.io/surveybot-scheduler:latest : buildée et pushée

Fly.io — Secrets scheduler
[ ] FLY_API_TOKEN : présent et valide
[ ] FLY_BOT_APP : surveybot-bot
[ ] FLY_BOT_IMAGE : registry.fly.io/surveybot-bot:latest
[ ] DATABASE_URL : présent (injecté par fly postgres attach)
[ ] STATE_BACKEND : postgres
[ ] ACCOUNTS_JSON : liste valide, au moins 1 compte

Fly.io — Postgres
[ ] Cluster "surveybot-db" : running (fly status --app surveybot-db)
[ ] Attaché au scheduler (DATABASE_URL injecté)
[ ] Aucun compte bloqué avec status != idle sans raison valide
```

---

## 14. Points d'architecture importants

### Le scheduler ne touche jamais la DB
Le scheduler lit `ACCOUNTS_JSON`, lance une machine par compte, et c'est tout.
Il n'écrit pas dans Postgres. La DB est la responsabilité exclusive du bot.

### Le bot est sa propre source de vérité
Au démarrage, le bot lit son état. Si `status != "idle"`, il exit(0) sans rien faire.
C'est ce mécanisme — et non un lock scheduler — qui garantit qu'un seul bot tourne par compte.

### Machines éphémères avec auto_destroy
`auto_destroy=True` + `restart.policy=no` : la machine disparaît dès que le process Python se termine.
Pas de zombie, pas de coût idle. Un crash Python non intercepté termine aussi la machine.

### Pas de NAT Gateway
Fly.io facture l'egress à l'usage ($0.02/GB). Aucun coût fixe réseau — contrairement au NAT Gateway AWS ($32/mois fixe).

### Timestamps ISO string dans Postgres
Le schéma Postgres stocke les timestamps en TEXT ISO string (`"2026-03-18T08:00:00"`).
Le code `account_state.py` gère la conversion via `_ts_to_unix()` pour les comparaisons temporelles.

### Soft restart avant hard exit
Le RuntimeGuard tente d'abord un soft restart. Si échec → `SystemExit` → la machine Fly se termine et est détruite. Le scheduler relancera au prochain tick.

### Nom de machine unique par tick
Les machines bot sont nommées `bot-{account_id}-{timestamp}` pour éviter les conflits de noms en cas de crash avant auto-destroy.

---

> **Version** : 5.0
> **Rédigé** : mars 2026
> **Sources** : infrastructure Fly.io (cdg) + code source (main.py, fly.py, account_loader.py, account_state.py, playwright_launcher.py, runtime_guard.py)
> **À mettre à jour** : après ajout de comptes, changement réseau, évolution du scheduler