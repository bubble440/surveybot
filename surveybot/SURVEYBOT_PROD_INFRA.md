# SurveyBot — Documentation Infrastructure Production AWS

> **Document de référence opérationnel** — décrit l'état exact de l'infrastructure AWS prod,
> le fonctionnement interne du bot, et les procédures de lancement/arrêt.
> Rédigé en mars 2026. À mettre à jour après tout changement d'infrastructure.

---

## Table des matières

1. [Vue d'ensemble](#1-vue-densemble)
2. [Composants AWS déployés](#2-composants-aws-déployés)
3. [Stack technique du bot](#3-stack-technique-du-bot)
4. [Flux d'exécution complet](#4-flux-dexécution-complet)
5. [Variables d'environnement](#5-variables-denvironnement)
6. [État DynamoDB — schéma complet](#6-état-dynamodb--schéma-complet)
7. [RuntimeGuard — comportement prod](#7-runtimeguard--comportement-prod)
8. [Lancer le bot en production](#8-lancer-le-bot-en-production)
9. [Arrêter le bot en production](#9-arrêter-le-bot-en-production)
10. [Ajouter un compte bot](#10-ajouter-un-compte-bot)
11. [Coûts et ressources à surveiller](#11-coûts-et-ressources-à-surveiller)
12. [Checklist de santé infrastructure](#12-checklist-de-santé-infrastructure)
13. [Points d'architecture importants](#13-points-darchitecture-importants)

---

## 1. Vue d'ensemble

### Objectif
SurveyBot automatise la complétion de sondages sur TopSurveys et les plateformes partenaires.
Chaque bot = 1 compte TopSurveys = 1 conteneur ECS Fargate = 1 proxy externe.
**Cible : ~100 bots en parallèle.**

### Architecture simplifiée

```
EventBridge Scheduler (toutes les 5 min)
    └─► ECS Task: scheduler
            └─► ECS Task: surveybot × N  (1 par compte actif)
                    ├─ Secrets Manager  (credentials)
                    ├─ DynamoDB         (état partagé)
                    └─ Chrome headless  (Playwright + Selenium)
                            └─ Proxy externe → Site de sondage
```

### Région AWS
Tout est déployé dans **eu-west-3 (Europe / Paris)**.

---

## 2. Composants AWS déployés

### 2.1 ECS — Elastic Container Service

**Cluster** : `passionate-panda-alu75o`
**Type** : Fargate (serverless — aucune instance EC2 à gérer, facturation à la seconde)

**Task Definitions actives (3)** :

| Nom | Rôle |
|-----|------|
| `scheduler` | Orchestrateur : lit les comptes, lance les tasks `surveybot` |
| `surveybot` | Bot principal (Chrome + Playwright + Selenium + OpenAI) |
| `VISUAL` | Debug uniquement — jamais utilisé en prod normale |

---

### 2.2 ECR — Container Registry

2 repositories privés :

| Repository | URI complète |
|-----------|-------------|
| `surveybot` | `865626945801.dkr.ecr.eu-west-3.amazonaws.com/surveybot` |
| `surveybot-scheduler` | `865626945801.dkr.ecr.eu-west-3.amazonaws.com/surveybot-scheduler` |

> **Important** : après chaque modification du code, il faut rebuild et pusher l'image
> avant de relancer. Les repos sont en mode **Mutable** (le tag `latest` peut être écrasé).

---

### 2.3 EventBridge Scheduler

Un seul schedule actif en prod normale :

#### `scheduler-runner` — le seul à activer en prod

| Paramètre | Valeur |
|-----------|--------|
| Occurrence | Recurring schedule |
| Fréquence | `rate(5 minutes)` |
| Timezone | Europe/Paris |
| Cible ECS | Task definition `scheduler` (Latest) |
| Cluster | `passionate-panda-alu75o` |
| Subnets | `subnet-094096e26e2a427c8`, `subnet-041a1e9c66a9934fc` |
| Security group | `sg-0e05b37375448ca11` (surveybot-sg) |
| Auto-assign public IP | DISABLED |
| Rôle IAM | `eventbridge-run-surveybot-role` |
| Retry policy | Off |
| DLQ | None |
| Statut actuel | Disabled (à activer au lancement) |

> Le schedule `surveybot-scheduler` est un schedule de test personnel.
> Il ne fait pas partie du flow prod et ne doit jamais être activé en fonctionnement normal.

---

### 2.4 Secrets Manager

Convention de nommage : `topsurveys_bot_XXX`

Comptes actuellement déclarés : `bot_001` à `bot_006` (6 comptes).
Cible finale : ~100 comptes.

Contenu attendu dans chaque secret (JSON) :
```json
{
  "Email":               "compte@email.com",
  "Password":            "...",
  "openai_api_key":      "sk-...",
  "payout_name":         "...",
  "payout_revolut_tag":  "...",
  "telegram_bot_token":  "...",
  "telegram_chat_id":    "..."
}
```

La résolution des secrets suit cette priorité (`secret_loader.py`) :
1. Variable ENV `TOPSURVEYS_SECRET_JSON` (JSON inline)
2. AWS Secrets Manager via `TOPSURVEYS_SECRET_NAME`
3. Variables ENV unitaires (fallback dev uniquement)

---

### 2.5 DynamoDB

**Table** : `surveybot_account_state`
**ARN** : `arn:aws:dynamodb:eu-west-3:865626945801:table/surveybot_account_state`

| Paramètre | Valeur |
|-----------|--------|
| Partition key | `account_id` (String) |
| Sort key | aucune |
| Capacity mode | On-demand |
| TTL | Activé sur attribut `ttl_ts` |
| Items actuels | 6 (bot_001 à bot_006) |
| Taille moyenne item | ~292 bytes |
| Deletion protection | Off |

Source de vérité partagée entre le scheduler et tous les bots.
Accès concurrent géré par **optimistic locking** (champ `version`).

---

### 2.6 Réseau — VPC

**VPC** : `vpc-038ced7972306fa38` (`surveybot-vpc-01`)

**Subnets (3)** :

| Nom | Subnet ID | CIDR | Type |
|-----|-----------|------|------|
| `surveybot-public-1a` | `subnet-0bc8652bcf66d18fc` | 10.0.0.0/24 | Public (NAT Gateway) |
| `surveybot-private-1a` | `subnet-041a1e9c66a9934fc` | 10.0.1.0/24 | Privé (tasks Fargate) |
| `surveybot-private-1b` | `subnet-094096e26e2a427c8` | 10.0.2.0/24 | Privé (tasks Fargate) |

Les tasks ECS tournent dans les subnets privés (`-1a` et `-1b`).
Elles passent par le NAT Gateway pour sortir vers internet.

**Security Groups** :

| Nom | Inbound | Outbound | Utilisé par |
|-----|---------|----------|-------------|
| `surveybot-sg` | Aucun | All → 0.0.0.0/0 | Tasks ECS |
| `default` | All depuis lui-même | All → 0.0.0.0/0 | VPC interne |

**Elastic IPs (3)** :

| IP | Nom | Association | Action |
|----|-----|-------------|--------|
| `51.44.135.248` | — | NAT Gateway (`rnat`) — active | Conserver |
| `13.36.51.89` | `surveybot-nat-eip` | Aucune — orpheline | Libérer |
| `13.36.153.246` | — | Aucune — orpheline | Libérer |

> Les 2 EIPs orphelines coûtent ~$7.20/mois inutilement.
> Pour libérer : EC2 → Elastic IPs → sélectionner → Actions → Release.
> Ne jamais libérer `51.44.135.248` (NAT Gateway actif).

**NAT Gateway** : 1 actif sur `surveybot-public-1a`. Coûte ~$35/mois fixe
+ data processing — coût incompressible pour Fargate en subnet privé.

---

### 2.7 IAM

| Ressource | Détail |
|-----------|--------|
| Rôle principal | `eventbridge-run-surveybot-role` |
| Usage | EventBridge appelle `ecs:RunTask` avec ce rôle |
| Users IAM | 1 (admin) |
| Roles total | 8 |

---

## 3. Stack technique du bot

### 3.1 Browser — architecture Playwright + Selenium hybride

Le bot utilise une approche hybride en deux étapes (fichier `playwright_launcher.py`) :

**Étape 1 — Playwright lance Chrome**
- Gère l'authentification proxy nativement (point que Selenium/UC ne résout pas proprement)
- Configure : proxy, langue (`fr-FR`), timezone (`Europe/Paris`), géolocalisation (Paris par défaut)
- Injecte des overrides DevTools anti-détection : `navigator.webdriver = undefined`, platform `Win32`
- Lance Chrome en mode `--headless=new` en prod (Docker/ECS)
- Expose un port de debugging aléatoire (42000–52000)

**Étape 2 — Selenium s'attache au Chrome existant**
- Se connecte via `debuggerAddress: 127.0.0.1:{port}`
- Toute la logique d'interaction (DOM, clics, OpenAI) reste dans Selenium
- Les objets Playwright (`_pw`, `_pw_context`, `_pw_page`) sont attachés au driver Selenium
  pour maintenir la session vivante (sinon garbage collection ferme Chrome)

```
Playwright → Chrome headless (proxy auth, fingerprint, overrides)
                 ↑ remote debugging port (aléatoire 42000-52000)
Selenium   → attach → driver (logique bot complète)
```

**En local** : mode simplifié — `undetected-chromedriver` direct, sans proxy, Chrome visible.

### 3.2 Pipeline de résolution d'un sondage

```
1. analyze_dom()              → extraction question_blocks depuis le DOM
2. filter_blocks()            → filtre blocs déjà répondus / hors scope
3. build_batch_prompt()       → construction prompt OpenAI (prompt_builder.py)
4. OpenAI API call            → génération des réponses (gpt-4o-mini)
5. parse_batch_response()     → parsing + résolution conflits exclusifs
6. action_dispatcher          → dispatch vers input_radio / checkbox / text / matrix / slider
7. try_click_navigation_cta() → navigation vers page suivante
8. stabilize (2s)             → attente stabilisation DOM
```

**Anti-boucles** (`survey_solver.py`) :
- `MAX_TOTAL_STEPS = 200` — sécurité dure, jamais reset
- `MAX_STEPS_PER_URL = 80` — évite de tourner sur la même page
- `MAX_URL_CHANGES = 60` — évite les ping-pongs de redirection
- `STABILIZE_SLEEP = 2.0s` — délai entre actions

---

## 4. Flux d'exécution complet

```
1. EventBridge (rate: 5 min)
   └─► RunTask: scheduler
       └─► lit les comptes éligibles (DynamoDB ou Secrets Manager)
           └─► pour chaque compte (non banni, cooldown expiré, pas de lock valide) :
               RunTask: surveybot
               env: TOPSURVEYS_SECRET_NAME, ACCOUNT_ID, RUN_ENV=aws, STATE_BACKEND=dynamodb

2. Task surveybot démarre (Fargate, subnet privé)
   ├─ acquire_account_lock_or_exit()
   │    └─ DynamoDB ConditionExpression atomique (lock_owner vide ou expiré)
   │    └─ si lock déjà pris → exit propre (doublon évité)
   ├─ install_sigterm_handler()
   │    └─ capte SIGTERM ECS → écrit ecs_stop_requested=true dans DynamoDB
   ├─ start_heartbeat_thread()
   │    └─ touch_heartbeat() toutes les ~30s (prolonge lock_until_ts)
   ├─ launch_browser()         [playwright_launcher.py]
   │    └─ Playwright → Chrome headless avec proxy
   │    └─ Selenium s'attache via debuggerAddress
   ├─ login()                  [auth_handler.py]
   │    └─ navigue vers TopSurveys, saisit email + password via Selenium
   ├─ init_session_and_enter_surveys()
   │    └─ attend chargement de la liste de sondages
   └─ run_main_loop()
        └─ pour chaque sondage disponible :
             survey_solver → survey_executor (boucle page par page)
        └─ arrêt propre → update DynamoDB (status=idle, last_stop_reason)

3. Arrêt propre
   → RuntimeGuard lève SystemExit(reason)
   → DynamoDB mis à jour (cooldown_until_ts, pause_policy)
   → task ECS se termine → Fargate désalloue automatiquement
   → dans les 5 min, scheduler relance si cooldown expiré
```

**Chemin réseau sortant** :
`Task Fargate (subnet privé) → NAT Gateway (51.44.135.248) → Internet → Proxy externe → Site sondage`

L'IP vue par les sites de sondage est celle du **proxy externe**, pas du NAT Gateway.

---

## 5. Variables d'environnement

### Obligatoires en prod

| Variable | Description |
|----------|-------------|
| `ACCOUNT_ID` | Identifiant du compte bot (ex: `topsurveys_bot_006`) |
| `TOPSURVEYS_SECRET_NAME` | Nom du secret dans Secrets Manager |
| `RUN_ENV` | `aws` (active le mode prod complet) |
| `STATE_BACKEND` | `dynamodb` |

### Optionnelles importantes

| Variable | Défaut | Description |
|----------|--------|-------------|
| `STATE_TABLE` | — | Nom de la table DynamoDB (`surveybot_account_state`) |
| `AWS_REGION` | auto (boto3) | Région AWS |
| `PROXY_URL` | — | URL du proxy externe (`http://host:port`) |
| `PROXY_USER` | — | Login proxy |
| `PROXY_PASS` | — | Mot de passe proxy |
| `ACCOUNT_LOCK_TTL_SEC` | `240` | TTL du lock DynamoDB en secondes |
| `LOG_LEVEL` | — | Active les logs debug conditionnels |
| `GEO_LAT` / `GEO_LON` | 48.8566 / 2.3522 | Coordonnées géo navigateur (Paris) |
| `SURVEY_LANG` | `fr-FR` | Locale navigateur |
| `SURVEY_TZ` | `Europe/Paris` | Timezone navigateur |
| `SURVEY_HEADLESS` | `1` | Headless si pas de DISPLAY |
| `SURVEY_BROWSER_BIN` | auto-detect | Chemin vers Chrome/Chromium |

### Modes d'exécution (`RUN_ENV`)

| `RUN_ENV` | Comportement |
|-----------|-------------|
| `local` | Debug interactif, pas de DynamoDB, Chrome visible, pauses autorisées |
| `aws` ou `docker` | Mode prod : DynamoDB obligatoire, Chrome headless, pas de pauses |
| `local` + `LOCAL_UNATTENDED=1` | Simule le comportement prod en local |

---

## 6. État DynamoDB — schéma complet

Table : `surveybot_account_state`, clé primaire : `account_id` (String)

| Champ | Type | Description |
|-------|------|-------------|
| `account_id` | String | Clé primaire (ex: `topsurveys_bot_006`) |
| `version` | Int | Optimistic locking — incrémenté à chaque écriture |
| `status` | String | `idle` / `running` / `paused` |
| `banned` | Bool | Compte banni → ne plus jamais lancer |
| `cooldown_until_ts` | Int | Timestamp Unix — ne pas relancer avant cette date |
| `lock_owner` | String | Task ID ECS qui détient le lock |
| `lock_until_ts` | Int | Expiration du lock (prolongé par heartbeat) |
| `proxy_id` | String | Identifiant du proxy assigné |
| `proxy_lock_owner` | String | Bot qui utilise ce proxy |
| `proxy_lock_until_ts` | Int | Expiration du lock proxy |
| `last_stop_reason` | String | Raison du dernier arrêt (valeur de StopReason) |
| `last_heartbeat_ts` | Int | Dernier heartbeat reçu |
| `last_boot_ts` | Int | Dernier démarrage du container |
| `last_start_ts` | Int | Dernier début de session de sondage |
| `daily_earned` | Map | `{"2026-03-09": 1.23}` — gains par jour |
| `total_earned` | Float | Gains totaux historiques |
| `pause_policy` | String | Politique de pause appliquée (ex: `MEDIUM_COOLDOWN`) |
| `ecs_stop_requested` | Bool | SIGTERM reçu depuis ECS |
| `ecs_stop_ts` | Int | Timestamp du SIGTERM |
| `ecs_stop_notified` | Bool | Alerte Telegram déjà envoyée (anti-spam) |
| `updated_ts` | Int | Timestamp de la dernière mise à jour |
| `ttl_ts` | Int | Expiration TTL pour auto-purge DynamoDB |

### Consulter l'état de tous les bots
DynamoDB → Tables → `surveybot_account_state` → Explore table items → Run (Scan)

### Réinitialiser un compte bloqué en `running`
Si un bot est coincé (crash sans mise à jour de l'état) :
1. DynamoDB → sélectionner l'item → Edit
2. Mettre : `status = "idle"`, `lock_owner = ""`, `lock_until_ts = 0`
3. Le scheduler relancera le compte au prochain cycle

---

## 7. RuntimeGuard — comportement prod

Le RuntimeGuard (`runtime_guard.py`) supervise chaque bot via un thread daemon.

### Raisons d'arrêt (StopReason)

| Raison | Condition | Politique de pause |
|--------|-----------|-------------------|
| `idle` | Inactivité > 120s | SHORT_COOLDOWN (2 min) |
| `too_many_errors` | 5 erreurs consécutives | SHORT_COOLDOWN (2 min) |
| `no_gain` | Aucun gain détecté depuis 15 min | MEDIUM_COOLDOWN (5 min) |
| `runtime_limit` | Runtime > 2h, objectif non atteint | MEDIUM_COOLDOWN (5 min) |
| `daily_target_reached` | Gains >= objectif journalier (5€) | DAILY_RESET (jusqu'à minuit) |
| `session_expired` | Session TopSurveys expirée | SHORT_COOLDOWN |

### Politiques de pause (PausePolicy)

| Politique | Durée |
|-----------|-------|
| `SHORT_COOLDOWN` | 2 minutes |
| `MEDIUM_COOLDOWN` | 5 minutes |
| `LONG_COOLDOWN` | 30 minutes |
| `DAILY_RESET` | Jusqu'à minuit (Europe/Paris) |
| `UNTIL_MANUAL` | ~1 an (intervention humaine requise) |

### Comportement prod vs local

**En prod** (`RUN_ENV=aws`) : `_check_conditions()` est bypassé — ECS/scheduler gère les
redémarrages. La pause est appliquée via DynamoDB (`cooldown_until_ts`), puis `SystemExit`
est levé pour terminer proprement la task. Le scheduler relancera dans les 5 minutes
si le cooldown est expiré.

**En local** : les conditions sont vérifiées en temps réel par le thread monitor.
`SystemExit` est aussi levé pour reproduire fidèlement le comportement prod.

### Heartbeat
- Fréquence : toutes les ~30s via `touch_heartbeat()`
- Mécanisme : DynamoDB `UpdateExpression` (atomic — pas de load+put)
- Sécurité : ne prolonge le lock QUE si `lock_owner == task_id` (condition DynamoDB)
- TTL du lock : configurable via `ACCOUNT_LOCK_TTL_SEC` (défaut : 240s)

### Notifications Telegram
- Chaque bot a ses propres credentials Telegram dans son secret (bot_token + chat_id)
- Notifications envoyées via HTTP direct vers l'API Telegram (`notifier.py`)
- Pas de dépendance AWS supplémentaire pour les alertes

---

## 8. Lancer le bot en production

### Prérequis avant tout lancement

- [ ] Image ECR à jour (`docker build` + `docker push` si du code a changé depuis le dernier déploiement)
- [ ] Secrets des comptes présents et complets dans Secrets Manager
- [ ] Table DynamoDB `surveybot_account_state` : status = Active
- [ ] NAT Gateway : State = Available (VPC → NAT Gateways)
- [ ] Task definitions `scheduler` et `surveybot` : status = Active

### Lancement normal (mode prod multi-bots)

1. **EventBridge → Scheduler → Schedules**
2. Sélectionner **`scheduler-runner`**
3. Cliquer **Enable**
4. Dans les 5 minutes maximum, une task `scheduler` se lance
5. Le scheduler lance une task `surveybot` par compte éligible
   (non banni, cooldown expiré, pas de lock actif)
6. Vérifier : **ECS → Clusters → passionate-panda-alu75o → Tasks** → tasks en status RUNNING

### Lancement manuel d'un bot unique (test / debug)

1. **ECS → Clusters → passionate-panda-alu75o → Run new task**
2. Compute : Fargate / Task definition : `surveybot` (Latest)
3. Réseau : VPC `surveybot-vpc-01`, subnets privés, SG `surveybot-sg`, Auto-assign IP = DISABLED
4. Container overrides (adapter l'account_id) :
```json
{
  "containerOverrides": [{
    "name": "surveybot",
    "environment": [
      { "name": "TOPSURVEYS_SECRET_NAME", "value": "topsurveys_bot_006" },
      { "name": "ACCOUNT_ID",            "value": "topsurveys_bot_006" },
      { "name": "RUN_ENV",               "value": "aws" },
      { "name": "STATE_BACKEND",         "value": "dynamodb" }
    ]
  }]
}
```
5. Run task

### Vérifier qu'un bot tourne correctement

- **ECS → Tasks** : status RUNNING
- **DynamoDB** → item du compte : `status = running`, `last_heartbeat_ts` < 60s (récent)
- **CloudWatch Logs** → groupe `/ecs/surveybot` : logs de démarrage et de navigation

---

## 9. Arrêter le bot en production

### Arrêt propre automatique (cas normaux)

Le bot s'arrête seul quand RuntimeGuard détecte une condition d'arrêt.
La task ECS se termine → Fargate désalloue → aucune action manuelle requise.
Le scheduler relancera le bot au prochain cycle si le cooldown est expiré.

### Pause temporaire (arrêt du relancement automatique)

Pour empêcher le scheduler de lancer de nouveaux bots :
1. **EventBridge → Schedules → `scheduler-runner` → Disable**
2. Les bots en cours terminent leur session naturellement
3. Aucun nouveau bot ne sera lancé jusqu'à réactivation

### Arrêt d'urgence (immédiat)

1. Désactiver le schedule : **`scheduler-runner` → Disable**
2. **ECS → Clusters → passionate-panda-alu75o → Tasks**
3. Sélectionner toutes les tasks `surveybot` en RUNNING → **Stop** → confirmer

> Un Stop forcé ne met pas à jour l'état DynamoDB proprement.
> Les locks expireront naturellement après `lock_until_ts` (max 4 min).
> Si besoin de relancer immédiatement après, réinitialiser les locks manuellement dans DynamoDB.

### Mettre un seul compte en pause sans toucher les autres

Depuis DynamoDB → item `topsurveys_bot_XXX` → Edit :
- Mettre `cooldown_until_ts` = timestamp Unix lointain (ex: `9999999999`)
- Le scheduler ignorera ce compte tant que le cooldown n'est pas expiré
- La task en cours continuera jusqu'à sa fin naturelle

---

## 10. Ajouter un compte bot

1. **Créer le secret dans Secrets Manager**
   - Nom : `topsurveys_bot_007` (incrémenter)
   - JSON avec les 7 clés requises

2. **Créer l'item dans DynamoDB**
   - DynamoDB → `surveybot_account_state` → Create item
   - `account_id` = `"topsurveys_bot_007"`, `banned` = `false`, `status` = `"idle"`

3. **S'assurer que le scheduler inclut le nouveau compte**
   dans la liste des comptes à orchestrer (selon implémentation du scheduler)

4. **Tester** avec un lancement manuel avant activation automatique

5. **Vérifier** dans CloudWatch Logs que le bot démarre et se connecte correctement

---

## 11. Coûts et ressources à surveiller

### Répartition des coûts (mars 2026, ~$54/mois prévisionnel)

| Service | Coût/mois | Note |
|---------|-----------|------|
| EC2 - Other (NAT GW) | ~$9.10 | NAT GW heures + 2 EIPs orphelines |
| Amazon VPC | ~$2.73 | NAT Gateway data processing |
| Tax | ~$2.49 | |
| Secrets Manager | ~$0.59 | ~6 secrets actifs |
| ECR | ~$0.05 | Stockage images Docker |
| ECS Fargate | Variable | ~$0.75/bot pour 2h/jour |
| DynamoDB | ~$0 | On-demand, usage modéré = Free tier |
| OpenAI | Hors AWS | gpt-4o-mini, facturation séparée |
| Proxies | Hors AWS | Facturation séparée |

### Économies immédiates possibles

| Action | Économie/mois |
|--------|--------------|
| Libérer EIP orpheline `13.36.51.89` | ~$3.60 |
| Libérer EIP orpheline `13.36.153.246` | ~$3.60 |
| **Total** | **~$7.20** |

Procédure : EC2 → Elastic IPs → sélectionner → Actions → Release Elastic IP address.

### Estimation à 100 bots (2h actifs/bot/jour)

| Poste | Coût/mois estimé |
|-------|-----------------|
| NAT Gateway (fixe) | ~$35 |
| ECS Fargate (100 bots × 2h/j) | ~$75 |
| Secrets Manager (100 secrets) | ~$10 |
| DynamoDB | ~$0–5 |
| **Total AWS** | **~$125/mois** |

---

## 12. Checklist de santé infrastructure

À vérifier avant chaque activation en prod :

```
Réseau
[ ] NAT Gateway : State = Available (VPC → NAT Gateways)
[ ] Internet Gateway : attaché au VPC surveybot-vpc-01
[ ] Route table subnets privés → 0.0.0.0/0 via NAT Gateway

ECS
[ ] Task definition "surveybot" : Active, image ECR à jour
[ ] Task definition "scheduler" : Active, image ECR à jour
[ ] Cluster passionate-panda-alu75o : ACTIVE

Secrets Manager
[ ] Secrets topsurveys_bot_XXX existent avec les 7 clés requises

DynamoDB
[ ] Table surveybot_account_state : Active
[ ] Aucun item avec lock actif invalide bloquant un compte

EventBridge
[ ] scheduler-runner : Disabled si arrêt voulu
[ ] scheduler-runner : Enabled pour démarrer le mode automatique
[ ] surveybot-scheduler : toujours Disabled (schedule de test uniquement)

Coûts
[ ] 2 EIPs orphelines libérées (13.36.51.89 et 13.36.153.246)
[ ] Aucun NAT Gateway inutilisé actif
```

---

## 13. Points d'architecture importants

### Pas de filesystem partagé entre conteneurs
Les conteneurs Fargate sont éphémères et isolés. DynamoDB est la seule source de vérité
partagée. En prod (`RUN_ENV != local`), le fallback fichier est désactivé — DynamoDB
doit être configuré et accessible sinon le bot exit au démarrage.

### Lock DynamoDB atomique — anti-doublon
Avant de démarrer, chaque bot tente d'acquérir un lock via `ConditionExpression` DynamoDB.
Si le lock est déjà pris (autre bot actif pour ce compte), la task exit proprement.
Cela évite les doublons même si EventBridge déclenche plusieurs invocations simultanées.

### Proxy = externe, pas AWS
Les proxies sont des services tiers (non hébergés sur AWS).
L'authentification proxy se fait au niveau Playwright (Chrome launch args).
Selenium s'attache ensuite au Chrome déjà configuré avec le proxy.
L'IP vue par les sites de sondage est l'IP du proxy, pas celle du NAT Gateway.

### Soft restart avant hard exit
Le RuntimeGuard tente d'abord un soft restart (CTA "Ouvrir l'application") avant de
lever SystemExit. En prod, SystemExit termine la task ECS proprement, et le scheduler
relancera dans les 5 minutes si le cooldown est expiré.

### Notifications Telegram décentralisées
Chaque bot a ses propres credentials Telegram dans son secret.
À 100 bots, cela signifie 100 canaux de notification potentiels.
Envisager un canal centralisé (SQS → Lambda → Telegram) pour un monitoring unifié.

### Images ECR Mutable — risque en prod
Les deux repos ECR sont en Mutable : un `docker push :latest` écrase silencieusement
l'image précédente. Un push pendant qu'une task démarre peut entraîner une image
incohérente. Envisager des tags versionnés en montée en charge à 100 bots.

---

> **Version** : 2.0
> **Rédigé** : mars 2026
> **Sources** : captures console AWS (eu-west-3) + code source (main.py, launch.py,
> playwright_launcher.py, account_state.py, runtime_guard.py, pause_policy.py,
> survey_solver.py, auth_handler.py, config_loader.py)
> **À mettre à jour** : après ajout de comptes, changement réseau, ou évolution du scheduler