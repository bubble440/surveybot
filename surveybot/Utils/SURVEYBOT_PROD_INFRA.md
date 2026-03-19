# SurveyBot — Documentation Infrastructure Production

> **Document de référence opérationnel** — décrit l'état exact de l'infrastructure prod,
> le fonctionnement interne du bot, et les procédures de lancement/arrêt.
> Rédigé en mars 2026. Déploiement unique : GCP (europe-west1).
> À mettre à jour après tout changement d'infrastructure.

---

## Table des matières

1. [Vue d'ensemble](#1-vue-densemble)
2. [Infrastructure GCP](#2-infrastructure-gcp)
3. [Stack technique du bot](#3-stack-technique-du-bot)
4. [Flux d'exécution complet](#4-flux-dexécution-complet)
5. [Variables d'environnement](#5-variables-denvironnement)
6. [État Firestore — schéma complet](#6-état-firestore--schéma-complet)
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
Chaque bot = 1 compte TopSurveys = 1 conteneur Cloud Run Job = 1 proxy externe.
**Cible : ~100 bots en parallèle.**

### Architecture simplifiée

```
Cloud Scheduler (toutes les 5 min)
    └─► Cloud Run Job: scheduler
            ├─ Secret Manager    (liste des comptes + credentials)
            ├─ Firestore         (état partagé — auto-création si absent)
            └─► Cloud Run Job: surveybot × N  (1 par compte éligible)
                    ├─ Secret Manager  (credentials injectés par le scheduler)
                    ├─ Firestore       (état partagé)
                    └─ Chrome headless (Playwright + Selenium)
                            └─ Proxy externe → Site de sondage
```

### Projet et région

| Cloud | Projet | Région |
|-------|--------|--------|
| GCP | `surveybot-490607` | `europe-west1` (Belgique) |

---

## 2. Infrastructure GCP

### 2.1 Cloud Run Jobs

**Projet** : `surveybot-490607`
**Région** : `europe-west1`

| Job | Image | Rôle | CPU | RAM | Timeout |
|-----|-------|------|-----|-----|---------|
| `scheduler` | `surveybot/scheduler:latest` | Orchestrateur : liste les comptes, lance les jobs `surveybot` | 1 | 512Mi | 300s |
| `surveybot` | `surveybot/bot:latest` | Bot principal (Chrome + Playwright + Selenium + OpenAI) | 1 | 2Gi | 3600s |

Les deux jobs tournent dans le subnet privé `surveybot-private` avec egress `all-traffic` via Cloud NAT.

---

### 2.2 Artifact Registry

**Repository** : `europe-west1-docker.pkg.dev/surveybot-490607/surveybot`

| Image | Tag | Rôle |
|-------|-----|------|
| `bot` | `latest` | Image du bot surveybot |
| `scheduler` | `latest` | Image du scheduler |

> Les images sont en tag `latest` (mutable). Un `docker push :latest` écrase l'image précédente.
> Après chaque push, le prochain cycle du scheduler utilisera automatiquement la nouvelle image.

**Script de build + push (PowerShell) — à utiliser à chaque déploiement :**

```powershell
# Bot
$PROJECT = "surveybot-490607"; $REGION = "europe-west1"; $REPO = "surveybot"; $TAG = "latest"
gcloud auth configure-docker "$REGION-docker.pkg.dev" --project=$PROJECT
docker build -t "${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/bot:${TAG}" ./surveybot
docker push "${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/bot:${TAG}"

# Scheduler
docker build -t "${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/scheduler:${TAG}" ./scheduler
docker push "${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/scheduler:${TAG}"
```

---

### 2.3 Cloud Scheduler

**Schedule** : `scheduler-runner`
**Location** : `europe-west1`

| Paramètre | Valeur |
|-----------|--------|
| Fréquence | `*/5 * * * *` (toutes les 5 min) |
| Timezone | Europe/Paris |
| Cible | Cloud Run Job `scheduler` via HTTP POST |
| URI | `https://europe-west1-run.googleapis.com/v2/projects/surveybot-490607/locations/europe-west1/jobs/scheduler:run` |
| Auth | OAuth — Service Account `surveybot-sa@surveybot-490607.iam.gserviceaccount.com` |
| Retry | 0 |
| Statut | ENABLED (à désactiver pour pause) |

---

### 2.4 Secret Manager

**Convention de nommage** : `topsurveys_bot_XXX`

Comptes actuellement déclarés : `bot_001` (1 compte actif).
Cible finale : ~100 comptes.

Contenu attendu dans chaque secret (JSON, une seule ligne) :
```json
{"EMAIL":"compte@email.com","PASSWORD":"...","PROXY_URL":"host:port","PROXY_USER":"...","PROXY_PASS":"...","GEO_LAT":"48.8566","GEO_LON":"2.3522","SURVEY_LANG":"fr-FR","SURVEY_TZ":"Europe/Paris"}
```

> **Important** : le JSON doit être sur une seule ligne sans apostrophes. Utiliser `gcloud secrets versions add` avec `--data-file=-` et un heredoc `<<'EOF'` pour éviter les erreurs de parsing.

**Créer ou mettre à jour un secret :**
```bash
gcloud secrets versions add topsurveys_bot_001 --data-file=- --project=surveybot-490607 <<'EOF'
{"EMAIL":"...","PASSWORD":"...","PROXY_URL":"...","PROXY_USER":"...","PROXY_PASS":"...","GEO_LAT":"48.8566","GEO_LON":"2.3522","SURVEY_LANG":"fr-FR","SURVEY_TZ":"Europe/Paris"}
EOF
```

**Le scheduler découvre automatiquement les comptes** en listant les secrets avec le préfixe `topsurveys_bot_`. Il crée également le document Firestore correspondant si absent — aucune action manuelle requise pour enregistrer un nouveau bot.

---

### 2.5 Firestore

**Base** : `(default)`
**Mode** : Native
**Région** : `europe-west1`
**Collection** : `surveybot_account_state`

Chaque document correspond à un compte bot. Les documents sont **auto-créés** par le scheduler au premier cycle si le secret existe dans Secret Manager.

Schéma d'un document (voir section 6 pour détail complet).

---

### 2.6 Réseau — VPC GCP

**VPC** : `surveybot-vpc`

| Ressource | Nom | CIDR / Détail |
|-----------|-----|---------------|
| Subnet | `surveybot-private` | `10.0.1.0/24` — europe-west1 |
| Router | `surveybot-router` | europe-west1 |
| Cloud NAT | `surveybot-nat` | Auto-allocate IPs, all subnets |

> Cloud NAT assure l'egress vers internet. Pas de coût fixe — facturation uniquement sur le data processing (~$0.01/GB).

---

### 2.7 IAM — Service Account

**Service Account** : `surveybot-sa@surveybot-490607.iam.gserviceaccount.com`

| Rôle | Usage |
|------|-------|
| `roles/run.developer` | Lancer les Cloud Run Jobs |
| `roles/secretmanager.secretAccessor` | Lire les secrets |
| `roles/datastore.user` | Lire/écrire Firestore |
| `roles/logging.logWriter` | Écrire les logs Cloud Logging |

---

## 3. Stack technique du bot

### 3.1 Browser — architecture Playwright + Selenium hybride

**Étape 1 — Playwright lance Chrome**
- Gère l'authentification proxy nativement
- Configure : proxy, langue (`fr-FR`), timezone (`Europe/Paris`), géolocalisation (Paris par défaut)
- Injecte des overrides DevTools anti-détection : `navigator.webdriver = undefined`, platform `Win32`
- Lance Chrome en mode `--headless=new` en prod (Docker/Cloud Run)
- Expose un port de debugging aléatoire (42000–52000)

**Étape 2 — Selenium s'attache au Chrome existant**
- Se connecte via `debuggerAddress: 127.0.0.1:{port}`
- Toute la logique d'interaction (DOM, clics, OpenAI) reste dans Selenium
- Les objets Playwright sont attachés au driver pour maintenir la session vivante

### 3.2 OpenAI

- Modèle : `gpt-4o-mini`
- API : `chat.completions.create()` (direct, pas Assistants API)
- Clé injectée via le secret du compte

### 3.3 Notifications Telegram

- Chaque bot a ses propres credentials Telegram dans son secret
- Notifications envoyées via HTTP direct vers l'API Telegram (`notifier.py`)

---

## 4. Flux d'exécution complet

```
1. Cloud Scheduler déclenche le job "scheduler" toutes les 5 min
2. scheduler/main.py :
   a. Liste les secrets Secret Manager avec préfixe "topsurveys_bot_"
   b. Pour chaque compte :
      - Auto-crée le document Firestore si absent
      - Appelle scheduler_tick(account_id)
3. scheduler_tick() (ecs_bot_scheduler.py) :
   a. Charge l'état depuis Firestore
   b. Vérifie : pas banni, pas en cooldown, status=idle, pas de lock actif
   c. Acquiert le lock Firestore (transaction atomique)
   d. Appelle start_task(account_id) → _start_task_gcp()
4. _start_task_gcp() (ecs.py) :
   a. Charge le secret depuis GCP Secret Manager
   b. Lance le Cloud Run Job "surveybot" avec les variables d'env du compte en overrides
5. surveybot démarre :
   a. Lit ses credentials depuis les variables d'env injectées
   b. Lance Chrome via Playwright
   c. Se connecte à TopSurveys
   d. Boucle de complétion de sondages
   e. RuntimeGuard surveille les conditions d'arrêt
6. En fin de session :
   a. Bot met à jour Firestore (status=idle, cooldown, earnings)
   b. Cloud Run Job se termine → ressources libérées
7. Au prochain cycle (5 min), le scheduler peut relancer si cooldown expiré
```

---

## 5. Variables d'environnement

### Variables communes (bot + scheduler)

| Variable | Valeur | Description |
|----------|--------|-------------|
| `RUN_ENV` | `gcp` | Cloud actif |
| `STATE_BACKEND` | `firestore` | Backend état |
| `STATE_TABLE` | `surveybot_account_state` | Nom collection Firestore |
| `GCP_PROJECT` | `surveybot-490607` | Projet GCP |

### Variables spécifiques au scheduler

| Variable | Valeur | Description |
|----------|--------|-------------|
| `GCP_REGION` | `europe-west1` | Région Cloud Run |
| `GCP_JOB_NAME` | `surveybot` | Nom du job bot à lancer |
| `ACCOUNT_PREFIX` | `topsurveys_bot_` | Filtre sur les secrets |

### Variables injectées par le scheduler dans le job bot

Ces variables sont passées en overrides à chaque exécution du job `surveybot` :

| Variable | Source |
|----------|--------|
| `ACCOUNT_ID` | account_id du compte |
| `EMAIL` | Secret Manager |
| `PASSWORD` | Secret Manager |
| `PROXY_URL` | Secret Manager |
| `PROXY_USER` | Secret Manager |
| `PROXY_PASS` | Secret Manager |
| `GEO_LAT` | Secret Manager |
| `GEO_LON` | Secret Manager |
| `SURVEY_LANG` | Secret Manager |
| `SURVEY_TZ` | Secret Manager |

---

## 6. État Firestore — schéma complet

**Collection** : `surveybot_account_state`
**Document ID** : `account_id` (ex: `topsurveys_bot_001`)

| Champ | Type | Description |
|-------|------|-------------|
| `account_id` | String | Identifiant unique du compte |
| `version` | Integer | Optimistic locking — incrémenté à chaque écriture |
| `banned` | Boolean | Si True, le scheduler ne relance jamais ce compte |
| `status` | String | `idle` / `running` / `starting` |
| `lock_owner` | String | ID de la task qui détient le lock |
| `lock_until_ts` | String (ISO) | Expiration du lock |
| `cooldown_until_ts` | String (ISO) | Ne pas relancer avant cette date |
| `last_heartbeat_ts` | String (ISO) | Dernier heartbeat du bot actif |
| `last_stop_reason` | String | Raison du dernier arrêt |
| `last_boot_ts` | String (ISO) | Dernier démarrage |
| `daily_earned` | Map | `{"2026-03-18": 1.23}` — revenus par jour |
| `total_earned` | Float | Revenus totaux cumulés |
| `updated_ts` | String (ISO) | Dernière mise à jour |

> Firestore stocke les timestamps en ISO string. Le code `account_state.py` gère la conversion via `_ts_to_unix()` pour les comparaisons temporelles.

---

## 7. RuntimeGuard — comportement prod

Le RuntimeGuard surveille en continu les conditions d'arrêt du bot. En prod GCP :

- Condition détectée → soft restart tenté (CTA "Ouvrir l'application")
- Si soft restart échoue → `SystemExit` → le Cloud Run Job se termine proprement
- Le scheduler relancera au prochain cycle si le cooldown est expiré
- L'état est mis à jour dans Firestore avant l'arrêt

---

## 8. Lancer le bot en production

### Prérequis avant tout lancement

- [ ] Images `bot:latest` et `scheduler:latest` à jour dans Artifact Registry
- [ ] Secrets `topsurveys_bot_XXX` présents et valides dans Secret Manager (JSON sur une ligne)
- [ ] Cloud Run Jobs `surveybot` et `scheduler` créés
- [ ] Cloud NAT `surveybot-nat` : actif
- [ ] Service Account `surveybot-sa` : rôles corrects

### Lancement automatique (mode prod multi-bots)

Le Cloud Scheduler `scheduler-runner` est déjà **ENABLED** — il se déclenche automatiquement toutes les 5 minutes. Aucune action manuelle requise si les prérequis sont satisfaits.

Pour vérifier que le cycle fonctionne :
```bash
gcloud run jobs executions list --job=scheduler --region=europe-west1 --project=surveybot-490607
gcloud run jobs executions list --job=surveybot --region=europe-west1 --project=surveybot-490607
```

### Déclenchement manuel immédiat (test / debug)

```bash
# Déclencher le scheduler manuellement
gcloud scheduler jobs run scheduler-runner --location=europe-west1 --project=surveybot-490607

# Ou lancer directement un bot pour un compte spécifique
gcloud run jobs execute surveybot --region=europe-west1 --project=surveybot-490607 \
  --update-env-vars="ACCOUNT_ID=topsurveys_bot_001"
```

### Vérifier qu'un bot tourne correctement

- **Cloud Run → Jobs → surveybot → History** : exécution en status RUNNING
- **Firestore** → document `topsurveys_bot_001` : `status = running`, `last_heartbeat_ts` récent
- **Cloud Logging** → filtre `resource.type="cloud_run_job" resource.labels.job_name="surveybot"` : logs de navigation

---

## 9. Arrêter le bot en production

### Arrêt propre automatique (cas normaux)

Le bot s'arrête seul quand RuntimeGuard détecte une condition d'arrêt.
Le Cloud Run Job se termine → ressources libérées → aucune action manuelle requise.
Le scheduler relancera au prochain cycle si le cooldown est expiré.

### Pause temporaire (arrêt du relancement automatique)

```bash
gcloud scheduler jobs pause scheduler-runner --location=europe-west1 --project=surveybot-490607
```

Les bots en cours terminent leur session naturellement. Aucun nouveau bot ne sera lancé.

Pour reprendre :
```bash
gcloud scheduler jobs resume scheduler-runner --location=europe-west1 --project=surveybot-490607
```

### Arrêt d'urgence (immédiat)

```bash
# 1. Pauser le scheduler
gcloud scheduler jobs pause scheduler-runner --location=europe-west1 --project=surveybot-490607

# 2. Lister les exécutions en cours
gcloud run jobs executions list --job=surveybot --region=europe-west1 --project=surveybot-490607

# 3. Annuler une exécution spécifique
gcloud run jobs executions cancel EXECUTION_ID --region=europe-west1 --project=surveybot-490607
```

> Un cancel forcé peut laisser des locks actifs dans Firestore.
> Les locks expirent naturellement après `lock_until_ts` (max ~3 min).
> Pour relancer immédiatement, réinitialiser manuellement `lock_owner=""` et `lock_until_ts="1970-01-01T00:00:00"` dans le document Firestore.

### Mettre un seul compte en pause

Depuis **Firestore → surveybot_account_state → topsurveys_bot_XXX → Edit** :
- Mettre `cooldown_until_ts` = `"2099-01-01T00:00:00"`
- Le scheduler ignorera ce compte jusqu'à cette date

---

## 10. Ajouter un compte bot

**Une seule action requise** : créer le secret dans GCP Secret Manager.

```bash
gcloud secrets create topsurveys_bot_002 --project=surveybot-490607
gcloud secrets versions add topsurveys_bot_002 --data-file=- --project=surveybot-490607 <<'EOF'
{"EMAIL":"...","PASSWORD":"...","PROXY_URL":"...","PROXY_USER":"...","PROXY_PASS":"...","GEO_LAT":"48.8566","GEO_LON":"2.3522","SURVEY_LANG":"fr-FR","SURVEY_TZ":"Europe/Paris"}
EOF
```

Le scheduler découvrira automatiquement ce compte au prochain cycle et créera le document Firestore correspondant. Aucune autre action requise.

> Tester avec un lancement manuel avant le premier cycle automatique :
> ```bash
> gcloud scheduler jobs run scheduler-runner --location=europe-west1 --project=surveybot-490607
> ```

---

## 11. Build et déploiement d'une nouvelle image

### Bot (`./surveybot/`)

```powershell
$PROJECT = "surveybot-490607"; $REGION = "europe-west1"; $REPO = "surveybot"; $TAG = "latest"
gcloud auth configure-docker "$REGION-docker.pkg.dev" --project=$PROJECT
docker build -t "${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/bot:${TAG}" ./surveybot
docker push "${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/bot:${TAG}"
```

### Scheduler (`./scheduler/`)

```powershell
$PROJECT = "surveybot-490607"; $REGION = "europe-west1"; $REPO = "surveybot"; $TAG = "latest"
gcloud auth configure-docker "$REGION-docker.pkg.dev" --project=$PROJECT
docker build -t "${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/scheduler:${TAG}" ./scheduler
docker push "${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/scheduler:${TAG}"
```

> Le tag `latest` est écrasé à chaque push. Le prochain job Cloud Run utilisera automatiquement la nouvelle image — aucune mise à jour de configuration requise.

---

## 12. Coûts et ressources à surveiller

### GCP — estimation mensuelle

| Service | Coût/mois | Note |
|---------|-----------|------|
| Cloud Run Jobs (scheduler) | ~$0.50 | 288 exécutions/jour × 5 min |
| Cloud Run Jobs (bots) | ~$65 | 1 bot × 2h/jour (à l'échelle : 100 bots = ~$65) |
| Cloud NAT | ~$1–3 | Data processing uniquement, pas de coût fixe |
| Artifact Registry | ~$0.05 | Stockage images |
| Secret Manager | ~$0.06/secret/mois | ~$6 à 100 comptes |
| Firestore | ~$0 | Free tier largement suffisant à 100 bots |
| Cloud Scheduler | ~$0 | Free tier (3 jobs gratuits) |
| **Total GCP (1 bot)** | **~$5/mois** | |
| **Total GCP (100 bots, 2h/j)** | **~$70/mois** | |

---

## 13. Checklist de santé infrastructure

À vérifier avant chaque activation en prod :

```
GCP — Réseau
[ ] Cloud NAT "surveybot-nat" : actif (gcloud compute routers nats list)
[ ] VPC "surveybot-vpc" et subnet "surveybot-private" : présents

GCP — Cloud Run
[ ] Job "surveybot" : existe, image à jour
[ ] Job "scheduler" : existe, image à jour
[ ] Variables d'env des jobs : RUN_ENV=gcp, STATE_BACKEND=firestore, GCP_PROJECT=surveybot-490607

GCP — Secret Manager
[ ] Secrets topsurveys_bot_XXX existent avec JSON valide (une ligne, double quotes)

GCP — Firestore
[ ] Base "(default)" : active, mode Native, europe-west1
[ ] Aucun document avec lock actif invalide bloquant un compte

GCP — Cloud Scheduler
[ ] scheduler-runner : ENABLED pour mode automatique
[ ] scheduler-runner : PAUSED pour arrêt du relancement

GCP — IAM
[ ] surveybot-sa : rôles run.developer, secretmanager.secretAccessor, datastore.user, logging.logWriter
```

---

## 14. Points d'architecture importants

### Source de découverte des comptes = Secret Manager
Le scheduler liste les secrets GCP Secret Manager avec le préfixe `topsurveys_bot_`. C'est la **seule** action requise pour enregistrer un nouveau bot. Firestore est la source de vérité de l'état, pas de la liste des comptes.

### Auto-création des documents Firestore
Si un compte existe dans Secret Manager mais pas dans Firestore, le scheduler crée automatiquement le document avec l'état par défaut (`status=idle`). Aucune intervention manuelle requise à l'ajout d'un compte.

### Timestamps ISO string dans Firestore
Firestore utilise des ISO strings (`"2026-03-18T08:00:00"`). Le code `account_state.py` gère la conversion via `_ts_to_unix()` pour les comparaisons temporelles.

### Lock Firestore atomique — anti-doublon
`try_acquire_account_lock()` utilise une transaction Firestore (`@firestore.transactional`). Une seule task peut acquérir le lock — les autres exit proprement. Évite les doublons même si Cloud Scheduler déclenche plusieurs exécutions simultanées.

### Proxy = externe, pas GCP
Les proxies sont des services tiers. L'authentification se fait au niveau Playwright (Chrome launch args). L'IP vue par les sites de sondage est l'IP du proxy, pas celle du Cloud NAT.

### Soft restart avant hard exit
Le RuntimeGuard tente d'abord un soft restart avant de lever `SystemExit`. En prod, `SystemExit` termine le Cloud Run Job proprement, et le scheduler relancera dans les 5 minutes si le cooldown est expiré.

---

> **Version** : 4.0
> **Rédigé** : mars 2026
> **Sources** : infrastructure GCP (surveybot-490607, europe-west1) + code source (main.py, ecs.py, account_loader.py, account_state.py, ecs_bot_scheduler.py, playwright_launcher.py, runtime_guard.py)
> **À mettre à jour** : après ajout de comptes, changement réseau, évolution du scheduler