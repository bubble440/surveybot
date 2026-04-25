# SurveyBot - Architecture et Documentation Complete

> **Document de reference permanent** pour comprendre l'architecture et le fonctionnement du projet SurveyBot, meme si certains fichiers sont retires de l'indexation.

---

## Table des Matieres

1. [Vue d'ensemble](#1-vue-densemble)
2. [Architecture generale](#2-architecture-generale)
3. [Workflow principal](#3-workflow-principal)
4. [Fichiers du projet](#4-fichiers-du-projet)
   - 4.1 [Infrastructure & Entry Points](#41-infrastructure--entry-points)
   - 4.2 [Authentification & Etat](#42-authentification--etat)
   - 4.3 [Guards & Monitoring](#43-guards--monitoring)
   - 4.4 [Preselection (TopSurveys)](#44-preselection-topsurveys)
   - 4.5 [Core - Analyse DOM](#45-core---analyse-dom)
   - 4.6 [Core - Classification & Mapping](#46-core---classification--mapping)
   - 4.7 [Core - Resolution de blocs](#47-core---resolution-de-blocs)
   - 4.8 [Core - Gestion des interactions](#48-core---gestion-des-interactions)
   - 4.9 [Core - Construction des prompts](#49-core---construction-des-prompts)
   - 4.10 [Core - Parsing des reponses](#410-core---parsing-des-reponses)
   - 4.11 [Orchestration](#411-orchestration)
   - 4.12 [Utilitaires - Debug & Snapshots](#412-utilitaires---debug--snapshots)
   - 4.13 [Utilitaires - Media](#413-utilitaires---media)
   - 4.14 [Cash & Payout](#414-cash--payout)
   - 4.15 [Scheduler Fly.io](#415-scheduler-flyio)
   - 4.16 [Tools - Pipeline d'auto-correction (attach)](#416-tools---pipeline-dauto-correction-attach)
5. [Systeme de DOM Registry](#5-systeme-de-dom-registry)
6. [Plateformes supportees](#6-plateformes-supportees)
7. [Gestion des erreurs](#7-gestion-des-erreurs)
8. [Points critiques](#8-points-critiques)
9. [Conventions de code](#9-conventions-de-code)
10. [Quick Reference](#10-quick-reference)

---

## 1. Vue d'ensemble

### Objectif
SurveyBot est un systeme automatise de completion de sondages web. Il utilise:
- **Selenium** pour l'automatisation du navigateur
- **Playwright** pour le lancement de Chrome avec proxy authentifie (puis Selenium s'y attache)
- **OpenAI API** pour generer des reponses coherentes
- **Fly.io** pour l'execution en production (machines ephemerales `--rm`)

### Principes fondamentaux

```
+------------------------------------------------------------------+
|  PRINCIPES CLES                                                  |
+------------------------------------------------------------------+
|  - Stabilite 80-90% > Couverture 100%                            |
|  - DOM = source principale (DOM-only, sans fallback)              |
|  - 1 bot = 1 proxy (isolation)                                   |
|  - Anti-boucles : budget de tentatives strict                    |
|  - Approche generaliste : corrections robustes, pas de patchs    |
+------------------------------------------------------------------+
```

### Stack technique
- **Langage**: Python 3.11+
- **Browser**: Chrome/Chromium (headless en prod)
- **Automatisation**: Selenium WebDriver (+ undetected-chromedriver + Playwright pour le lancement)
- **IA**: OpenAI API (gpt-4o-mini / gpt-4o)
- **Infra**: Fly.io (machines ephemerales), PostgreSQL (Fly Postgres)
- **Scheduler**: script Python externe (`scheduler/scheduler_fly.py`)

---

## 2. Architecture Generale

### Diagramme de flux de donnees

```
                    +------------------+
                    |   TopSurveys     |
                    |   (Listing)      |
                    +--------+---------+
                             |
                             v
+------------------------------------------------------------------+
|                    SURVEYBOT CORE                                |
|  +-------------+    +-------------+    +---------------------+   |
|  |   Browser   |--->| DOM Analyzer|--->|  Prompt Builder     |   |
|  |  (Selenium) |    |             |    |                     |   |
|  +-------------+    +------+------+    +----------+----------+   |
|                            |                      |              |
|                            v                      v              |
|                   +-------------+         +-------------+        |
|                   | DOM Registry|         |  OpenAI API |        |
|                   | (target_id) |         |             |        |
|                   +------+------+         +------+------+        |
|                          |                       |               |
|                          v                       v               |
|                   +-------------------------------------+        |
|                   |        Batch Response Parser        |        |
|                   |  (resolution conflits, validation)  |        |
|                   +----------------+--------------------+        |
|                                    |                             |
|                                    v                             |
|                   +-------------------------------------+        |
|                   |          Input Handler              |        |
|                   |   (click, fill, select, navigate)   |        |
|                   +-------------------------------------+        |
+------------------------------------------------------------------+
                             |
                             v
              +------------------------------+
              |   Plateformes de sondage     |
              |  CloudResearch, Walr, Cint,  |
              |  QuestionPro, Decipher, etc. |
              +------------------------------+
```

### Architecture de deploiement (Fly.io)

```
+------------------------------------------------------------------+
|                         SCHEDULER (local/GCP)                    |
|  +---------------------------+                                   |
|  | scheduler_fly.py          |   Lit accounts.json,             |
|  | (cron / appel externe)    |   lance 1 machine par compte     |
|  +-------------+-------------+                                   |
+-----------------|------------------------------------------------+
                  |  flyctl machine run --rm --detach
                  v
+------------------------------------------------------------------+
|                         FLY.IO                                   |
|                                                                  |
|  +--------------+  +--------------+  +--------------+           |
|  | Machine      |  | Machine      |  | Machine      |           |
|  | Bot Account1 |  | Bot Account2 |  | Bot AccountN |           |
|  | + Proxy1     |  | + Proxy2     |  | + ProxyN     |           |
|  | (ephemere)   |  | (ephemere)   |  | (ephemere)   |           |
|  +--------------+  +--------------+  +--------------+           |
|                                                                  |
|  +------------------------------+                                |
|  | Fly Postgres                 |  <- etat partagé (account_    |
|  | (DATABASE_URL auto-injecte)  |     state, cooldowns, gains)  |
|  +------------------------------+                                |
|                                                                  |
|  +------------------------------+                                |
|  | fly secrets                  |  <- OPENAI_API_KEY,           |
|  |   set KEY=VALUE              |     DATABASE_URL, etc.        |
|  +------------------------------+                                |
+------------------------------------------------------------------+
```

**Cycle de vie d'une machine**:
1. Le scheduler appelle `flyctl machine run --rm --detach` avec les env vars du compte
2. La machine demarre, acquiert un slot Postgres (cooldown lock)
3. Lance Chrome via Playwright, s'attache avec Selenium
4. Boucle principale : preselection + resolution surveys
5. A la fin (ou crash), la machine se detruit automatiquement (`--rm`)
6. Le scheduler recrée une nouvelle machine au prochain tick (5 min)

---

## 3. Workflow Principal

### Boucle d'execution du bot

```python
# Pseudo-code du workflow principal
while survey_active and attempts < MAX_ATTEMPTS:

    # 1. ANALYSE DOM
    question_blocks = dom_analyzer.analyze_dom(driver)

    # 2. FILTRAGE
    filtered_blocks = prompt_builder.filter_blocks_for_openai(question_blocks)

    if not filtered_blocks:
        # Tenter navigation CTA ou detecter fin de survey
        if detect_survey_end(driver):
            break
        click_cta_navigation(driver)
        continue

    # 3. PROMPT OPENAI
    prompt = prompt_builder.build_batch_prompt(filtered_blocks)
    response = openai_handler.complete(prompt)

    # 4. PARSING + RESOLUTION CONFLITS
    actions = batch_response_parser.parse_batch_response(response)
    actions = batch_response_parser.filter_exclusive_conflicts(actions)

    # 5. EXECUTION
    for action in actions:
        input_handler.execute_action(driver, action)

    # 6. NAVIGATION
    input_handler.try_click_navigation_cta(driver)

    # 7. ATTENTE STABILISATION
    time.sleep(STABILIZE_DELAY)
```

---

## 4. Fichiers du Projet

### 4.1 Infrastructure & Entry Points

#### `fly.toml`
**Chemin**: `fly.toml`
**Role**: Configuration de l'application Fly.io.

```toml
app = "surveybot-bot"
primary_region = "cdg"

[build]
  image = "registry.fly.io/surveybot-bot:latest"

[env]
  LOG_LEVEL = "DEBUG"
  RUN_ENV = "prod"

[[vm]]
  cpu_kind = "shared"
  cpus = 1
  memory_mb = 2048
```

---

#### `config.py`
**Chemin**: `config.py`
**Role**: Configuration centrale du bot - point unique pour gerer les modes d'execution.

**Modes disponibles**:
```
1. LOCAL INTERACTIF (defaut)
   - RUN_ENV=local (ou absent)
   - Pauses CAPTCHA avec input()
   - Hot reload active

2. LOCAL UNATTENDED (simulation prod)
   - RUN_ENV=local + LOCAL_UNATTENDED=1
   - Pas de pauses bloquantes
   - RuntimeGuard active

3. PROD (Fly.io)
   - RUN_ENV=prod
   - Tout active, aucune pause interactive
```

**Fonctions principales**:

| Fonction | Description |
|----------|-------------|
| `is_local_env()` | True si RUN_ENV == "local" |
| `is_attach_mode()` | True si mode debug sur navigateur existant |
| `is_prod_like()` | True si comportement production (Fly.io ou LOCAL_UNATTENDED) |
| `should_pause_for_captcha()` | True si pause interactive autorisee |
| `should_block_for_input()` | True si input() bloquants autorises |
| `should_run_guard_monitor()` | True si RuntimeGuard doit etre active |
| `should_run_heartbeat()` | True si heartbeat Postgres actif |
| `should_run_hot_reload()` | True si hot reload actif (local seulement) |
| `get_captcha_behavior()` | Retourne "auto_2captcha", "pause" ou "restart" |
| `log_config_summary()` | Affiche un resume au demarrage |

---

#### `main.py`
**Chemin**: `main.py`
**Role**: Point d'entree principal du bot.

**Responsabilites**:
- Dispatch mode attach vs mode normal
- Logique de selection d'onglet en mode attach (heuristiques URL/title/DOM)
- `run_attach_takeover()` — resolution survey sur onglet existant
- `run_attach_preselection_takeover()` — preselection puis resolution
- Boucle principale `main()` avec `MAX_MAIN_CYCLES` iterations

**Fonctions attach (mode debug local)**:

| Fonction | Description |
|----------|-------------|
| `_attach_tab_score(driver)` | Score un onglet (nb inputs + taille texte) |
| `_attach_select_tab(driver)` | Selectionne l'onglet selon ATTACH_TAB_* env vars |
| `_attach_pick_ui_active_tab(driver, handles)` | Retrouve l'onglet UI actif (visibilityState) |
| `run_attach_takeover(driver, ...)` | Boucle survey sur onglet ouvert manuellement |
| `run_attach_preselection_takeover(driver, ...)` | Preselection + resolution en attach |

**Variables d'env attach**:
- `ATTACH_TAB_URL_CONTAINS` — filtre par URL
- `ATTACH_TAB_TITLE_CONTAINS` — filtre par titre
- `ATTACH_TAB_DOM_CONTAINS` — filtre par contenu DOM
- `ATTACH_TAB_SELECTOR` — "current" | "last" | "best" | "pick" | index
- `ATTACH_ROUTE_PROMPT=1` — propose le choix preselection/resolution
- `ATTACH_MAX_STEPS` — nombre max d'iterations (defaut 100)

---

#### `launch.py`
**Chemin**: `launch.py`
**Role**: Fonctions d'infrastructure du cycle de vie du bot (separees de main.py).

**Responsabilites**:
- Lancement du driver (`launch_driver_or_fail`)
- Connexion TopSurveys + payout initial (`init_session_and_enter_surveys`)
- Boucle principale survey (`run_main_loop`)
- Gestion des signaux SIGTERM / SIGUSR1
- Thread heartbeat Postgres
- Thread hot reload (local)
- RuntimeGuard (superviseur)
- Soft restart (retour listing + payout + reprise)
- Navigation securisee `safe_get()`
- Serveur HTTP debug local

**Fonctions principales**:

| Fonction | Description |
|----------|-------------|
| `acquire_account_lock_or_exit(account_id)` | Verifie cooldown Postgres ou exit |
| `mark_bot_running(account_id)` | Marque status="running" en Postgres |
| `install_sigterm_handler(account_id)` | SIGTERM → libere slot + exit propre |
| `install_sigusr1_handler()` | SIGUSR1 → dump SurveyContext (debug) |
| `launch_driver_or_fail(config, account_id)` | Lance Chrome ou SystemExit |
| `init_session_and_enter_surveys(driver, ...)` | Login + payout + navigation listing |
| `run_main_loop(driver, api_key, ...)` | Lance run_survey() en boucle |
| `soft_restart(ctx, driver, reason)` | Cleanup + payout + reprise survey |
| `start_runtime_guard(account_id, ...)` | Demarre RuntimeGuard + reinjecte gains |
| `start_heartbeat_thread()` | Thread heartbeat 60s + jitter |
| `start_hot_reload_thread()` | Thread hot reload modules (local) |
| `build_notifier(config)` | Fabrique la fonction de notification Telegram |
| `start_debug_http_server(ctx_getter)` | Serveur HTTP :port+1000 (local seulement) |
| `safe_get(driver, url)` | Navigation avec timeout + detection session expirée |

---

### 4.2 Authentification & Etat

#### `auth_handler.py`
**Chemin**: `preselection/auth_handler.py`
**Role**: Authentification TopSurveys et verification de session.

**Fonctions principales**:

| Fonction | Signature | Description |
|----------|-----------|-------------|
| `login(driver, email, password)` | `(driver, str, str)` | Authentification TopSurveys |
| `is_session_expired(driver)` | `(driver) -> bool` | Detecte expiration de session |
| `dom_probe(driver)` | `(driver)` | Dump DOM pour debug |
| `net_probe()` | `()` | Diagnostic reseau (IP NAT vs proxy) |
| `snap(driver, label)` | `(driver, str)` | Screenshot avec label (debug) |

**Signaux d'expiration detectes**:
```python
signals = [
    "session expired", "your session has expired",
    "please log in again", "mot de passe expire",
    "reconnectez-vous", "password expired", "log in again",
]
```

---

#### `account_state.py`
**Chemin**: `State/account_state.py`
**Role**: Stockage d'etat "prod-first" pour N bots via PostgreSQL.

**Backends**:
- **PostgreSQL** (prod) : source de verite partagee via `DATABASE_URL` (injecte par Fly.io)
- **Fichier local** (fallback dev) : uniquement si `RUN_ENV=local`

**Variables d'env**:
```bash
DATABASE_URL=postgres://...   # injecte automatiquement par fly postgres attach
STATE_BACKEND=postgres        # active le backend Postgres
STATE_TABLE=...               # optionnel (nom de table custom)
STATE_TTL_DAYS=0              # 0 = pas de TTL auto
```

**Structure d'etat par defaut**:
```python
{
    "account_id": str,
    "version": int,                 # optimistic locking (SELECT FOR UPDATE)
    "banned": bool,
    "cooldown_until_ts": str,       # ISO UTC ex: "2026-03-23T10:00:00"
    "status": str,                  # idle | running
    "last_stop_reason": str,
    "last_heartbeat_ts": str,
    "last_boot_ts": str,
    "last_start_ts": str,
    "daily_earned": dict,           # {"2025-12-31": 1.23}
    "daily_target_start_ts": dict,  # {"2026-03-17": "2026-03-17T08:00:00"}
    "total_earned": float,
    "updated_ts": str,
}
```

**Fonctions principales**:

| Fonction | Description |
|----------|-------------|
| `load_state(account_id)` | Charge depuis Postgres (ou fichier local) |
| `save_state(state)` | Sauvegarde directe (rare, preferer update_state) |
| `update_state(account_id, fn)` | Update atomique avec optimistic lock + retry |
| `touch_heartbeat(account_id)` | UPDATE cible (pas de load complet) — prolonge cooldown_until_ts |
| `try_acquire_cooldown_slot(account_id)` | Lock atomique (SELECT FOR UPDATE) — retourne True si slot libre |

---

#### `secret_loader.py`
**Chemin**: `preselection/secret_loader.py`
**Role**: Chargement robuste des secrets (Fly.io secrets + ENV).

**Strategie d'empilement** (priorite decroissante):
1. `TOPSURVEYS_SECRET_JSON` (variable ENV contenant JSON)
2. Variables ENV unitaires (EMAIL, PASSWORD, OPENAI_API_KEY, etc.)

> En prod Fly.io, les secrets sont injectes via `fly secrets set KEY=VALUE`.
> Plus de dependance a AWS Secrets Manager.

**Cles supportees**:
```python
mapping = {
    "Email": "EMAIL",
    "Password": "PASSWORD",
    "openai_api_key": "OPENAI_API_KEY",
    "payout_name": "PAYOUT_NAME",
    "payout_revolut_tag": "PAYOUT_REVOLUT_TAG",
    "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
    "telegram_chat_id": "TELEGRAM_CHAT_ID",
}
```

---

#### `config_loader.py`
**Chemin**: `preselection/config_loader.py`
**Role**: Fusion config locale + secrets distants.

```python
def load_config() -> dict:
    """
    Ordre de priorite (du plus fort au plus faible):
      1) Overrides ENV + secrets Fly.io (via secret_loader)
      2) Fichier local config.json (dev)
    """
```

---

#### `playwright_launcher.py`
**Chemin**: `preselection/playwright_launcher.py`
**Role**: Lance Chrome avec proxy authentifie via Playwright, puis attache Selenium au port de debug.

**Probleme resolu**: Chrome/undetected-chromedriver echoue sur les proxies authentifies (`ERR_INVALID_ARGUMENT`). Solution : Playwright ouvre Chrome avec le proxy, puis Selenium se connecte via `--remote-debugging-port`.

**Fonction principale**:
```python
def launch_browser(config: dict) -> webdriver.Chrome:
    """
    1) Detecte le binaire Chrome disponible.
    2) Lance Chrome headless via Playwright avec proxy (host:port + user:pass).
    3) Attache Selenium via debuggerAddress.
    4) Retourne le driver Selenium pret a l'emploi.
    """
```

**Variables d'env**:
```bash
SURVEY_BROWSER_BIN=...    # chemin explicite du binaire Chrome
PROXY_URL=http://host:port
PROXY_USER=user
PROXY_PASS=pass
GEO_LAT=48.8566           # geolocalisation simulee
GEO_LON=2.3522
SURVEY_LANG=fr-FR
SURVEY_TZ=Europe/Paris
```

---

### 4.3 Guards & Monitoring

#### `runtime_guard.py`
**Chemin**: `Management/guards/runtime_guard.py`
**Role**: Superviseur central d'execution - protege OpenAI, Postgres et Proxy.

**Classe `RuntimeGuard`**:
```python
RuntimeGuard(
    account_id: str,
    idle_timeout_sec: int = 120,       # 2 minutes
    restart_cooldown_sec: int = 60,    # 1 minute
    max_errors_in_row: int = 5,
    max_runtime_sec: int = 2 * 3600,   # 2h
    daily_target_eur: float = 5.0,
    notify_fn: Callable,
    on_soft_restart: Callable,
)
```

**Raisons d'arret (StopReason)**:
```python
class StopReason(Enum):
    IDLE = "idle"
    TOO_MANY_ERRORS = "too_many_errors"
    NO_GAIN = "no_gain"
    RUNTIME_LIMIT = "runtime_limit"
    DAILY_TARGET_REACHED = "daily_target_reached"
    SESSION_EXPIRED = "session_expired"
```

**Metriques trackees (RuntimeState)**:
- `consecutive_errors` / `total_errors`
- `surveys_completed_today`
- `earnings_today_eur` (reinjecte depuis Postgres au demarrage)
- `openai_calls`
- `last_activity_ts` / `last_success_ts`

---

#### `survey_difficulty_guard.py`
**Chemin**: `Management/guards/survey_difficulty_guard.py`
**Role**: Detection DOM des surveys "stricts" (anti-bot / interactions complexes).

**Selecteurs "forts"**:
```python
STRICT_SELECTORS = {
    "captcha": [
        "iframe[src*='captcha']", ".g-recaptcha", "[data-sitekey]",
    ],
    "drag_drop": [
        "[draggable='true']", "[class*='draggable']",
        "[cdkdrag]", "[class*='cdk-drag']",
    ],
    "hold_button": [
        "[class*='hold']", "[aria-label*='maintenir']",
    ],
}
```

**Fonction principale**:
```python
def detect_strict_survey(driver) -> Tuple[bool, Optional[str]]:
    """Retourne (is_strict, reason)"""
```

---

#### `sensitive_question_guard.py`
**Chemin**: `Management/guards/sensitive_question_guard.py`
**Role**: Detection des questions a haut risque -> SKIP direct.

**Patterns detectes**:
```python
SENSITIVE_PATTERNS = [
    r"\bwebcam\b", r"\bcamera\b", r"\bmicrophone\b",
    r"\bcaptcha\b", r"\brecaptcha\b",
    r"\bglisser\b", r"\bdrag\b", r"\bmaintenir\b",
    r"\bautoriser\b", r"\bpermission\b",
    r"\bscreen\s*(share|sharing|record)\b",
    r"\baudio\b", r"\bvideo\b",
]
```

---

#### `redirect_watcher.py`
**Chemin**: `Management/redirect_watcher.py`
**Role**: Surveillance des redirections URL.

**Fonctions principales**:

| Fonction | Description |
|----------|-------------|
| `wait_for_final_redirection(driver, max_wait=30)` | Attend stabilisation URL |
| `switch_to_latest_window_and_close_others(driver, base_handles)` | Switch vers nouvel onglet + cleanup |

---

#### `pause_policy.py`
**Chemin**: `Management/pause_policy.py`
**Role**: Politique centrale de pause du bot.

**Enum PausePolicy**:
```python
class PausePolicy(Enum):
    NONE = auto()              # pas de pause
    SHORT_COOLDOWN = auto()    # 2 min (incidents legers)
    MEDIUM_COOLDOWN = auto()   # 15 min (erreurs / no-gain)
    LONG_COOLDOWN = auto()     # 2h (environnement defavorable)
    DAILY_RESET = auto()       # jusqu'a minuit
    UNTIL_MANUAL = auto()      # intervention humaine requise (~1 an)
```

---

#### `notifier.py`
**Chemin**: `Management/notifier.py`
**Role**: Envoi de notifications Telegram.

```python
def send_telegram(message: str, bot_token: str, chat_id: str) -> bool:
    """Envoie un message via le bot Telegram."""
```

---

### 4.4 Preselection (TopSurveys)

#### `survey_navigator.py`
**Chemin**: `preselection/survey_navigator.py`
**Role**: Navigation vers les surveys TopSurveys (choix du meilleur survey disponible).

**Fonction principale**:
```python
def go_to_best_value_survey(driver) -> None:
    """
    Navigue vers le survey au meilleur rapport valeur/temps sur le listing TopSurveys.
    """
```

---

#### `survey_handler.py`
**Chemin**: `preselection/survey_handler.py`
**Role**: Handler principal pour les surveys TopSurveys (preselection).

**Fonction principale**:
```python
def run_survey(driver, api_key, *, account_id: str, ctx, payout_name, payout_revolut_tag):
    """
    Boucle de traitement des questions de preselection.
    Gere: SKIP, DISQUALIFIED, RESTART, et delegation au survey_solver.
    """
```

**Actions de controle**:
- `SKIP` : question sensible -> tente "Je ne peux pas repondre"
- `DISQUALIFIED` : retour au listing TopSurveys
- `RESTART_SURVEY` : redemarrage propre du survey

---

#### `question_analyzer.py`
**Chemin**: `preselection/question_analyzer.py`
**Role**: Analyse des questions de preselection (popup TopSurveys).

**Fonctions principales**:

| Fonction | Description |
|----------|-------------|
| `extract_popup_html(driver)` | Extrait le HTML du popup TopSurveys |
| `extract_question_text(html)` | Extrait le texte de la question |
| `detect_input_type(html)` | Detecte radio/checkbox |
| `get_response_for_question(driver, api_key)` | Obtient reponse OpenAI |

---

#### `response_executor.py`
**Chemin**: `preselection/response_executor.py`
**Role**: Execution des reponses dans l'interface TopSurveys.

**Fonctions principales**:
```python
def execute_response(driver, answer_text) -> bool:
    """
    Ordre de tentatives:
    1) Checkbox (select_checkbox_answers)
    2) Radio (labels avec spans)
    3) Click CTA suivant
    """
```

---

#### `question_validation.py`
**Chemin**: `preselection/question_validation.py`
**Role**: Validation "metier" - detection des disqualifications.

**Dataclass**:
```python
@dataclass
class QuestionDecision:
    action: str     # CONTINUE | SKIP | DISQUALIFIED | RESTART_SURVEY
    reason: Optional[str]
```

**Signaux de disqualification (robustes)**:
```python
strong_phrases = (
    "tu n'as pas ete qualifie", "vous n'avez pas ete qualifie",
    "not qualified", "did not qualify", "not eligible",
    "screened out", "disqualified",
)
```

---

### 4.5 Core - Analyse DOM

#### `dom_analyzer.py`
**Chemin**: `Survey/dom_analyzer.py`
**Role**: Extraction TEXT-ONLY des questions depuis le DOM des pages de survey.

**Fonctions principales**:

| Fonction | Signature | Description |
|----------|-----------|-------------|
| `analyze_dom` | `(driver) -> List[Dict]` | Point d'entree principal |
| `_detect_itype` | `(el) -> str` | Detecte le type d'input |
| `_get_option_text` | `(el, driver) -> str` | Extrait le texte d'une option |
| `_looks_like_system_field` | `(el) -> bool` | Detecte champs ASP.NET |
| `_extract_questionpro_dropdowns` | `(driver) -> List[Dict]` | Extraction QuestionPro |
| `_extract_walr_card_sort_questions` | `(driver) -> List[Dict]` | Extraction Walr card-sort |
| `_extract_cloudresearch_sentry_questions` | `(driver) -> List[Dict]` | Extraction CloudResearch |

**Structure question_block**:
```python
{
    "question": str,           # Texte de la question
    "itype": str,              # radio|checkbox|text|textarea|select|button
    "options": List[str],      # Options disponibles
    "max_select": int,         # Nombre max de selections
    "target_id": str,          # ID unique DOM Registry
    "scope_hint": str,         # Contexte DOM
    "frame_chain": List[int],  # Chemin iframes
}
```

---

#### `dom_extractors_areyounet.py`
**Chemin**: `Survey/dom_extractors_areyounet.py`
**Role**: Extracteur specialise pour la plateforme AreYouNet.

---

#### `dom_extractors_decipher.py`
**Chemin**: `Survey/dom_extractors_decipher.py`
**Role**: Extracteur specialise pour la plateforme Decipher/FocusVision.

---

#### `dom_extractors_misc.py`
**Chemin**: `Survey/dom_extractors_misc.py`
**Role**: Extracteurs divers pour plateformes non couvertes par les modules dedies.

---

#### `dom_frame_selector.py`
**Chemin**: `Survey/dom_frame_selector.py`
**Role**: Selection et navigation dans les iframes pour l'analyse DOM.

---

#### `dom_question_extractor.py`
**Chemin**: `Survey/dom_question_extractor.py`
**Role**: Extraction generique des blocs de questions depuis le DOM.

---

#### `dom_selection_rules.py`
**Chemin**: `Survey/dom_selection_rules.py`
**Role**: Regles declaratives de selection des elements DOM (allowlists/denylists par plateforme).

---

#### `dom_utils.py`
**Chemin**: `Survey/dom_utils.py`
**Role**: Utilitaires DOM bas niveau (helpers JavaScript, inspection elements).

---

#### `log_utils.py`
**Chemin**: `Survey/log_utils.py`
**Role**: Helpers de logging standardises (`log_debug`, `log_info`, etc.).

---

#### `sliderpoints_extractor.py`
**Chemin**: `Survey/sliderpoints_extractor.py`
**Role**: Extraction specialisee FocusVision/Decipher sliderpoints.

---

### 4.6 Core - Classification & Mapping

#### `dom_classifier.py`
**Chemin**: `Survey/dom_classifier.py`
**Role**: Classification deterministe des pages SANS IA.

**Detection CAPTCHA stricte**:
```python
# reCAPTCHA visible uniquement
"recaptcha/api2/bframe"  # iframe challenge = captcha visible

# hCaptcha
'class="h-captcha"' + "data-sitekey"

# Turnstile
"cf-turnstile" + "data-sitekey"
```

---

#### `dom_context_mapper.py`
**Chemin**: `Survey/dom_context_mapper.py`
**Role**: Mapping spatial des inputs via bounding boxes.

---

#### `dom_registry.py`
**Chemin**: `Survey/dom_registry.py`
**Role**: Registre en memoire des cibles DOM.

**Format target_id**: `{kind}_{sha1_hash[:12]}`

---

#### `frame_utils.py`
**Chemin**: `Survey/frame_utils.py`
**Role**: Utilitaires de traversee des iframes.

```python
FrameChain = List[int]  # Ex: [0, 2] = iframe 0 -> iframe 2

with switch_to_frame_chain(driver, chain) as ok:
    if ok:
        # Analyser dans ce contexte
```

---

### 4.7 Core - Resolution de blocs

#### `question_block_analyzer.py`
**Chemin**: `Survey/question_block_analyzer.py`
**Role**: Construction carte logique locale des inputs.

---

#### `question_block_resolver.py`
**Chemin**: `Survey/question_block_resolver.py`
**Role**: Resolution robuste question -> champ input.

**Securite**:
- Ne JAMAIS ecraser un champ deja rempli
- Effacement safe: Ctrl+A + Backspace + events

---

#### `dropdown_block_resolver.py`
**Chemin**: `Survey/dropdown_block_resolver.py`
**Role**: Resolution specialisee des dropdowns.

---


### 4.8 Core - Gestion des interactions (Architecture modulaire)

L'architecture d'interaction est organisee en **9 modules specialises** + 1 facade pour la retrocompatibilite.

```
+------------------------------------------------------------------+
|                    ARCHITECTURE INPUT HANDLER                     |
+------------------------------------------------------------------+
|                                                                  |
|   input_handler.py (Facade retro-compatible)                     |
|   └── Re-exporte toutes les fonctions des modules specialises    |
|                                                                  |
|   +----------------------------------------------------------+   |
|   |  MODULES SPECIALISES                                      |   |
|   +----------------------------------------------------------+   |
|   |                                                          |   |
|   |  input_utils.py      Constantes, normalisation, helpers  |   |
|   |  input_frame.py      Gestion des iframes                 |   |
|   |  input_radio.py      Boutons radio                       |   |
|   |  input_checkbox.py   Cases a cocher                      |   |
|   |  input_text.py       Champs texte/textarea               |   |
|   |  input_dropdown.py   Dropdowns et <select>               |   |
|   |  input_slider.py     Sliders (Decipher/Behaviorally)     |   |
|   |  input_matrix.py     Questions matricielles              |   |
|   |  cta_handler.py      Boutons CTA et navigation           |   |
|   |                                                          |   |
|   +----------------------------------------------------------+   |
+------------------------------------------------------------------+
```

---

#### `input_utils.py`
**Chemin**: `Survey/input_utils.py`
**Role**: Fonctions utilitaires et constantes partagees par tous les modules input.

**Constantes exportees**:

| Constante | Description |
|-----------|-------------|
| `DROPDOWN_PLACEHOLDERS` | Textes de placeholder dropdown a ignorer |
| `PLACEHOLDER_TOKENS` | Version normalisee des placeholders |
| `CTA_SYNONYMS` | Synonymes de boutons de navigation (FR/EN) |
| `DATE_HINTS` | Patterns pour champs date (month/day/year) |
| `MATRIX_COL_SYNONYMS` | Synonymes colonnes matrice (oui/non, agree/disagree) |

**Fonctions de normalisation**:

| Fonction | Description |
|----------|-------------|
| `norm_txt(s)` | Normalisation basique: trim + lowercase + ponctuation |
| `norms_txt(s)` | Collapse whitespace + lowercase |
| `normt_txt(s)` | Robuste avec decomposition unicode, retire accents |
| `norm_soft(s)` | NFKC + collapse whitespace |
| `normalize_lbl(s)` | Pour labels: NFKD, retire accents, lowercase |
| `strip_accents(s)` | Retire uniquement les accents |
| `xpath_literal(s)` | Echappe une chaine pour XPath |

**Helpers DOM generiques**:

| Fonction | Description |
|----------|-------------|
| `scroll_into_view(driver, el)` | Scroll l'element au centre du viewport |
| `js_click(driver, el)` | Clic via JavaScript |
| `safe_click(driver, el, trace)` | Clic robuste avec fallbacks |
| `is_checked(el)` | Verifie si input radio/checkbox est coche |
| `set_input_value_with_events(driver, el, value)` | Pose valeur + events (React/Angular) |
| `find_question_container_by_ctx(driver, ctx)` | Trouve le container de question |
| `viewport_penalty(driver, el)` | Penalite si element hors viewport |

---

#### `input_frame.py`
**Chemin**: `Survey/input_frame.py`
**Role**: Gestion des iframes pour les interactions cross-frame.

**Fonctions principales**:

| Fonction | Description |
|----------|-------------|
| `iter_iframes_safe(driver)` | Liste des iframes visibles (>20x20px) |
| `in_each_frame_recursive(driver, fn, depth)` | Execute fn dans chaque frame recursivement |
| `click_button_by_text_any_context(driver, text)` | Clic bouton cross-frame |
| `click_cta_strong_any_context(driver, text)` | Version robuste avec frame_utils |

---

#### `input_radio.py`
**Chemin**: `Survey/input_radio.py`
**Role**: Gestion complete des boutons radio.

**Fonctions principales**:

| Fonction | Description |
|----------|-------------|
| `click_radio_by_label(driver, label, ctx)` | **Point d'entree** - coche le radio par label |
| `click_decipher_grid_radio(driver, label, ctx)` | Decipher table.grid robuste |
| `click_decipher_grid_radio_strict(driver, label, ctx)` | Decipher strict avec post-verification |
| `click_radio_label_in_scope(driver, scope, label)` | Clic radio dans un scope limite |
| `fallback_click_radio_js_generic(driver, label)` | Fallback JS universel |

**Patterns supportes**:
- `<label for="...">` + `<input type=radio id="...">`
- Input radio voisin de `<label>`
- Conteneurs ARIA `role="radio"`
- Blocs styles (answer/option/choice)
- Decipher grid radio (tables)
- Confirmit cards/GridClick

---

#### `input_checkbox.py`
**Chemin**: `Survey/input_checkbox.py`
**Role**: Gestion complete des cases a cocher.

**Fonctions principales**:

| Fonction | Description |
|----------|-------------|
| `click_checkbox_by_label(driver, text, ctx)` | **Point d'entree** - coche checkbox par label |
| `force_checkbox_events(driver, el)` | Force events JS (React/Angular/Vue/jQuery) |
| `force_label_for_checkbox_js(driver, text)` | Force via JS: label + input + classes visuelles |
| `fallback_click_checkbox_js_alchemer(driver, text)` | Fallback cible Alchemer (sg-*) |
| `fallback_click_checkbox_js_generic(driver, text)` | Fallback generique multi-sites |
| `click_confirmit_checktable(driver, label, ctx)` | Checkbox dans tables Confirmit |

**Patterns supportes**:
- `<label for="id">` -> `<input id="id" type="checkbox">`
- Checkbox ARIA/custom (`role="checkbox"`)
- Checkbox button-like (jQuery Mobile)
- Confirmit checktable
- Alchemer (classes sg-*)

---

#### `input_text.py`
**Chemin**: `Survey/input_text.py`
**Role**: Gestion des champs texte, textarea et contenteditable.

**Fonctions principales**:

| Fonction | Description |
|----------|-------------|
| `fill_text_input(driver, text, ctx)` | **Point d'entree** - saisie fiable multi-fallback |
| `type_via_cdp(driver, text)` | Frappe via Chrome DevTools Protocol |
| `react_set_value_and_fire(driver, el, value)` | Setter natif + events (React/PRDG) |
| `is_numeric_field(el)` | Detecte champ numerique |
| `swagbucks_zip_patch(driver, value)` | Patch cible Swagbucks (champ zip) |

**Strategie de saisie (multi-fallback)**:
1. scroll + focus + clear (CTRL+A, DELETE)
2. send_keys standard
3. ActionChains char-par-char
4. Chrome DevTools Protocol (CDP)
5. JavaScript direct + events (input/change/blur)
6. Nudge clavier pour React/Angular

---

#### `input_dropdown.py`
**Chemin**: `Survey/input_dropdown.py`
**Role**: Gestion des dropdowns natifs et custom.

**Fonctions principales**:

| Fonction | Description |
|----------|-------------|
| `select_option_with_hint(driver, text, hint, ctx)` | **Point d'entree** - selection en un enchainement |
| `has_native_selects(driver)` | Verifie presence de `<select>` natifs |
| `select_like_elements(driver)` | Liste tous les dropdowns (natifs + custom) |
| `best_dropdown_for_hint(driver, hint, ctx)` | Trouve le dropdown le plus pertinent |
| `open_dropdown_generic(driver, hint, ctx)` | Ouvre un dropdown specifique |
| `try_select_option_any(driver, text)` | Selection dans dropdown deja ouvert |

**Dropdowns supportes**:
- `<select>` natif
- Angular Material, MUI, Select2, jQuery UI
- ARIA combobox (`role="combobox"`)

**Disambiguation intelligente**: Mois vs Annee (detection automatique par valeur)

---

#### `input_slider.py`
**Chemin**: `Survey/input_slider.py`
**Role**: Gestion des sliders (Decipher/Behaviorally).

**Fonction principale**:

| Fonction | Description |
|----------|-------------|
| `set_sliderpoints(driver, choice_text, ctx)` | Positionne un slider sq-sliderpoints |

**Strategie (2 tentatives max)**:
1. Scope strict par row-legend
2. Map choice_text -> index sur la legende visible
3. Applique via clic legend + set `<select>` + jQuery-UI `slider('value', ...)`
4. Verifie que slider n'est plus off-scale

---

#### `input_matrix.py`
**Chemin**: `Survey/input_matrix.py`
**Role**: Gestion des questions matricielles (tables, grilles).

**Fonctions principales**:

| Fonction | Description |
|----------|-------------|
| `click_matrix_cell_by_row_and_col(driver, row, col, ctx)` | **Point d'entree** - clic cellule |
| `looks_like_matrix(driver)` | Detecte presence de matrice |
| `iter_matrix_rows(driver)` | Itere sur les lignes de matrice |
| `get_matrix_columns(driver)` | Liste des en-tetes de colonnes |
| `apply_matrix_column_to_all_rows(driver, col)` | Applique une colonne a toutes les lignes |

**Matrices supportees**: Tables HTML, grilles div-based (Qualtrics, Dynata, SSI)

---

#### `cta_handler.py`
**Chemin**: `Survey/cta_handler.py`
**Role**: Gestion des CTA (Call To Action) et boutons de navigation.

**Fonctions principales**:

| Fonction | Description |
|----------|-------------|
| `click_button_by_text(driver, text)` | Clic bouton par texte visible |
| `click_icon_like_button(driver, hints)` | Clic bouton icone (sans texte) |
| `click_primary_cta(driver)` | Clic CTA principal de la page |
| `try_click_navigation_cta(driver)` | Recherche et clic CTA navigation |
| `try_click_navigation_cta_any_context(driver)` | Version cross-frame (captcha post-resolution) |
| `click_cta_strong_any_context(driver, text, depth)` | Version robuste multi-frame |

**CTA_SYNONYMS reconnus**:
```python
{"continuer", "suivant", "start", "commencer", "next", "continue",
 "submit", "soumettre", "valider", "proceed", "envoyer", "terminer"}
```

**Scoring try_click_navigation_cta**:
- `text_contains_continue`: +50
- `id_contains_submit`: +60
- `class_contains_primary`: +30
- `aria_disabled`: skip

---

#### `input_handler.py` (Facade)
**Chemin**: `Survey/input_handler.py`
**Role**: Facade retro-compatible qui re-exporte toutes les fonctions des modules specialises.

**Usage**:
```python
# Import depuis la facade (retrocompatible)
from input_handler import click_radio_by_label, fill_text_input, ...

# Ou import direct depuis les modules
from input_radio import click_radio_by_label
from input_text import fill_text_input
```

**Fonctions d'orchestration conservees**:
- `handle_generic_input(driver, gpt_answer)` - Dispatch generique
- `apply_ai_response(driver, response)` - Application reponse OpenAI

---

#### `action_types.py`
**Chemin**: `Survey/action_types.py`
**Role**: Dataclass canonique Action.

```python
@dataclass(frozen=True)
class Action:
    value: str
    itype: Optional[str] = None
    context: str = ""
    qid: str = ""
    target_id: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)
```

---

#### `action_dispatcher.py`
**Chemin**: `Survey/action_dispatcher.py`
**Role**: Dispatch specialise pour cas complexes (card-sort, card-rating).

---

### 4.9 Core - Construction des prompts

#### `prompt_builder.py`
**Chemin**: `Survey/prompt_builder.py`
**Role**: Transformation question_blocks -> prompts OpenAI.

**Format de sortie**:
```
QID //// target_id //// valeur //// itype //// contexte
```

---

### 4.10 Core - Parsing des reponses

#### `batch_response_parser.py`
**Chemin**: `Survey/batch_response_parser.py`
**Role**: Parser reponses OpenAI + resolution conflits.

**Patterns exclusifs (FR)**:
```python
_EXCLUSIVE_PATTERNS_FR = (
    r"^aucun(e)?(\s|$)",
    r"^je\s+ne\s+sais\s+pas",
    r"^pas\s+applicable",
    # ... 16 patterns
)
```

**Separateur multi-select**: `|` (JAMAIS `,`)

---

### 4.11 Orchestration

#### `survey_context.py`
**Chemin**: `Survey/survey_context.py`
**Role**: Contexte rolling en memoire d'une session survey (historique questions/reponses + resume OpenAI).

**Classe `SurveyContext`**:
```python
SurveyContext(
    session_id: str,
    openai_api_key: str,
    summary_every_n_pages: int = 1,  # frequence de mise a jour du resume
)
```

**Methodes**:
- `record(question, options, answer)` — enregistre une reponse dans l'historique
- `maybe_update_summary()` — declenche une mise a jour asynchrone du resume OpenAI
- `print_debug()` — dump terminal du contexte (accessible via SIGUSR1 ou HTTP debug)

**Usage**:
```python
_ctx = SurveyContext(session_id=account_id, openai_api_key=api_key)
# expose globalement pour le signal handler SIGUSR1
survey_solver._current_survey_ctx = _ctx
# apres chaque page
_ctx.maybe_update_summary()
```

---

#### `survey_solver.py`
**Chemin**: `Survey/survey_solver.py`
**Role**: Orchestration inter-pages.

**Constantes anti-boucle**:
```python
MAX_TOTAL_STEPS = 200      # Securite dure (jamais reset)
MAX_STEPS_PER_URL = 80     # Par URL
MAX_URL_CHANGES = 60       # Anti ping-pong
STABILIZE_SLEEP = 2.0      # Delai entre actions
```

---

#### `survey_executor.py`
**Chemin**: `Survey/survey_executor.py`
**Role**: Execution single-page.

---

### 4.12 Utilitaires - Debug & Snapshots

#### `page_snapshot.py`
**Chemin**: `Survey/page_snapshot.py`
**Role**: Capture complete d'une page pour debug et pour le pipeline d'auto-correction.

**Fonctions principales**:
- `snapshot_if_enabled(driver, reason, question_blocks)` — capture si `SURVEY_SNAPSHOT_FLAG_FILE` actif, retourne le chemin du dossier
- `dump_page_snapshot(driver, reason, question_blocks)` — capture inconditionnelle, retourne le chemin du dossier

**Structure du dossier produit**:
```
snapshots/snapshot_<YYYYMMDD_HHMMSS>_<reason>/
  dom_outer.html          ← DOM complet (outerHTML du <html>)
  question_blocks.json    ← blocs extraits par dom_analyzer au moment du snapshot
  metadata.json           ← url, timestamp, reason, ...
  screenshot.png
  frames/                 ← iframes capturees
```

> `dom_outer.html` et `question_blocks.json` sont les fichiers consommes par `failure_pipeline.py`.

---

#### `replay_snapshot.py`
**Chemin**: `tools/replay_snapshot.py`
**Role**: Rejoue un snapshot DOM via dom_analyzer et compare la sortie avec une baseline.

```bash
python tools/replay_snapshot.py <snapshot_dir> --save-baseline
python tools/replay_snapshot.py <snapshot_dir>          # compare avec baseline
python tools/replay_snapshot.py <snapshot_dir> --use-dom-outer  # utilise dom_outer.html
```

---

#### `screenshot_analyzer.py`
**Chemin**: `Survey/screenshot_analyzer.py`
**Role**: Vision API fallback (DEPRECIE).

---

#### `hot_reload.py`
**Chemin**: `hot_reload/hot_reload.py`
**Role**: Hot reload des modules Python en dev.

```python
class ModuleReloader:
    def __init__(self, module_names: List[str], poll_interval: float = 0.5)
    def reload_changed(self) -> Dict[str, object]
    def watch_loop(self, on_change)
```

---

### 4.13 Utilitaires - Media

#### `video_utils.py`
**Chemin**: `Survey/video_utils.py`
**Role**: Detection, lecture et capture audio des videos.

---

### 4.14 Cash & Payout

#### `payout.py`
**Chemin**: `Cash/payout.py`
**Role**: Gestion des paiements et cashout TopSurveys.

**Fonctions principales**:

| Fonction | Description |
|----------|-------------|
| `_read_balance(driver)` | Lit le solde actuel (widget TopSurveys) |
| `_open_cashout_modal(driver)` | Ouvre le modal d'encaissement |
| `_select_money_option_in_open_tab(driver, tab, amount)` | Selectionne option paiement |
| `do_cashout(driver, amount)` | Execute le cashout complet |
| `check_and_cashout_if_needed(driver, account_id, ...)` | Encaissement automatique si seuil atteint |

---

### 4.15 Scheduler Fly.io

Le scheduler est un module **independant** dans `../scheduler/` (hors de `surveybot/`).

```
scheduler/
├── accounts.json          # Liste des comptes (non versionne en prod)
├── account_loader.py      # Charge les comptes depuis ACCOUNTS_JSON env var
├── scheduler_fly.py       # Lance une machine Fly.io par compte
├── fly.py                 # Helpers flyctl
└── main.py                # Point d'entree du scheduler
```

#### `scheduler_fly.py`
**Role**: Lit `accounts.json` et lance une machine Fly.io ephemerales par compte.

**Fonctionnement**:
```python
# Pour chaque compte dans accounts.json :
flyctl machine run \
    --app surveybot-bot \
    --region cdg \
    --vm-memory 2048 \
    --name {account_id} \
    --env ACCOUNT_ID=... \
    --env EMAIL=... \
    --env PASSWORD=... \
    --env PROXY_URL=... \
    --env PROXY_USER=... \
    --env PROXY_PASS=... \
    --rm \        # detruit la machine apres exit
    --detach \    # non-bloquant
    registry.fly.io/surveybot-bot:latest
```

**Variables d'env du scheduler**:
```bash
ACCOUNTS_FILE=accounts.json   # chemin du fichier comptes
FLY_APP=surveybot-bot
FLY_REGION=cdg
FLY_MEMORY=2048
BOT_IMAGE=registry.fly.io/surveybot-bot:latest
LAUNCH_DELAY_SEC=2            # delai anti-burst entre lancements
```

#### `account_loader.py`
**Role**: Charge les comptes depuis la variable `ACCOUNTS_JSON` (secret Fly.io).

**Format ACCOUNTS_JSON**:
```json
[
  {
    "ACCOUNT_ID": "bot_001",
    "EMAIL": "bot001@example.com",
    "PASSWORD": "xxx",
    "PROXY_URL": "http://host:port",
    "PROXY_USER": "user",
    "PROXY_PASS": "pass",
    "GEO_LAT": "48.8566",
    "GEO_LON": "2.3522",
    "SURVEY_LANG": "fr-FR",
    "SURVEY_TZ": "Europe/Paris"
  }
]
```

---

### 4.16 Tools - Pipeline d'auto-correction (attach)

> **Actif UNIQUEMENT en mode attach** (`BROWSER_MODE=attach`). Jamais exécuté en prod ou `LOCAL_UNATTENDED`.

#### `failure_pipeline.py`
**Chemin**: `tools/failure_pipeline.py`
**Role**: Pipeline de diagnostic et d'auto-correction déclenché automatiquement (ou manuellement) lorsque le bot échoue à extraire ou appliquer des réponses sur une page.

**Configuration (variables en tête de fichier)**:

| Variable | Valeurs | Description |
|----------|---------|-------------|
| `PATCH_LLM` | `"claude"` (défaut) / `"codex"` | LLM utilisé pour rédiger le patch (étape 3) |

**Variable d'environnement**:

| Variable | Description |
|----------|-------------|
| `FAILURE_PIPELINE_TRIGGER_FILE` | Chemin d'un fichier-drapeau. Sa présence déclenche le pipeline sur la prochaine page traitée (déclenchement manuel). Défini automatiquement dans `attach_tab.ps1` → `C:/tmp/fp_trigger`. |

**Points d'injection dans le bot (tous gardés par `is_attach_mode()`)**:

| Point | Fichier | Condition de déclenchement |
|-------|---------|---------------------------|
| 1 — extraction | `survey_executor.py` | aucun bloc actionnable extrait **ou** flag manuel présent |
| 2 — application | `survey_executor.py` | `apply_answers()` retourne `False` |
| 3 — clic CTA | `survey_solver.py` | CTA échoue ≥ 2 fois de suite |

**Étapes du pipeline**:

```
Étape 1 — Lecture snapshot
  └── dom_outer.html + question_blocks.json

Étape 2 — Génération du expected (OpenAI gpt-4o)
  └── Appel API avec DOM + blocks actuels
  └── Affichage de la proposition + validation humaine obligatoire (o/n)
  └── Écriture de question_blocks_expected.json

Étape 3 — Rédaction du patch (PATCH_LLM)
  ├── "claude" → claude --print --file patch_request.md
  └── "codex"  → codex --approval-mode full-auto "<contenu>"
```

**Déclenchement manuel** (attach mode, depuis PowerShell) :
```powershell
# Créer le flag → le pipeline se déclenche sur la prochaine page
New-Item -Force "C:/tmp/fp_trigger"
```

**Usage CLI direct** (bypass de la vérification `is_attach_mode`) :
```bash
python tools/failure_pipeline.py ./snapshots/<nom_snapshot> --step extraction
python tools/failure_pipeline.py ./snapshots/<nom_snapshot> --step manual
```

**Flux LLM par étape** :

| Étape | LLM utilisé | Configurable |
|-------|-------------|--------------|
| 2 — génération expected | OpenAI `gpt-4o` | Non (toujours OpenAI) |
| 3 — rédaction patch | `PATCH_LLM` (`claude` ou `codex`) | Oui (`PATCH_LLM` dans le fichier) |

---

## 5. Systeme de DOM Registry

### Cycle de vie

```python
# 1. AVANT analyse
clear_registry()

# 2. PENDANT analyse
target_id = make_target_id("radio", "name:gender", "Quel est votre genre?")
register_target(target_id, {
    "kind": "group",
    "itype": "radio",
    "xpath": "//input[@name='gender']",
    "frame_chain": [0],
    "option_xpath_map": {"Homme": "...", "Femme": "..."}
})

# 3. APRES parsing OpenAI
target = get_target(target_id)
```

---

## 6. Plateformes Supportees

| Plateforme | Radio | Checkbox | Dropdown | Text | Slider | Matrix | Card-sort |
|------------|-------|----------|----------|------|--------|--------|-----------|
| Qualtrics | OK | OK | OK | OK | Partiel | OK | Non |
| CloudResearch/Sentry | OK | OK | OK | OK | Non | Partiel | Non |
| Walr | OK | OK | OK | OK | Non | OK | OK |
| QuestionPro | OK | OK | OK | OK | Non | Partiel | Non |
| Decipher/FocusVision | OK | OK | OK | OK | OK | OK | OK |
| Cint/QPS | OK | OK | OK | OK | Non | Partiel | Non |
| AreYouNet | OK | OK | Partiel | OK | Non | Partiel | Non |

---

## 7. Gestion des erreurs

### Codes de sortie

| Code | Signification |
|------|---------------|
| 0 | Success |
| 1 | Disqualification |
| 2 | Retry needed |
| 3 | Unsupported |
| 4 | Timeout |
| 5 | Budget exhausted |

### Codes SystemExit connus

| Code | Source | Description |
|------|--------|-------------|
| `max_main_cycles_reached` | main.py | MAX_MAIN_CYCLES epuise (Fly.io recrée la machine) |
| `ecs_sigterm` | launch.py | SIGTERM recu (Fly.io stop demande) |
| `session_expired` | launch.py | Session TopSurveys expiree |
| `browser_launch_failed` | launch.py | Chrome n'a pas pu demarrer |
| `attach_forbidden_in_prod` | main.py | Mode attach tente hors local |

---

## 8. Points Critiques

### Detection de visibilite
```python
def _is_element_visible(el, driver):
    # 1. Verifier l'input lui-meme
    if el.is_displayed():
        return True
    # 2. Verifier les wrappers connus
    wrappers = [".clickableCell", ".answer-choice-wrapper", "label[for]"]
    # ...
```

### Optimistic locking Postgres
```python
# UPDATE echoue si version a change entre le SELECT et le UPDATE
WHERE account_state.version = {current_version}
# Si rowcount == 0 -> conflit, retry avec backoff exponentiel
```

### Heartbeat Fly.io
- Thread daemon, toutes les 60s (+jitter 0-3s)
- Met a jour `cooldown_until_ts = now + 240s` tant que `status == 'running'`
- Si le bot crash sans liberer le slot, le scheduler attend ~4 min avant de relancer

---

## 9. Conventions de code

- Fonctions privees: `_helper_function()`
- Constantes: `MAX_RETRIES = 3`
- Guard clauses pour early return
- Logs prefixes: `[MODULE][LEVEL]` ex: `[SOFT_RESTART][WARN]`

---

## 10. Quick Reference

### Variables d'environnement

```bash
# ── Identite du bot ────────────────────────────────────────────────
ACCOUNT_ID=bot_001           # obligatoire en prod (injecte par scheduler)
RUN_ENV=local|prod           # local = interactif, prod = Fly.io

# ── Credentials TopSurveys ─────────────────────────────────────────
EMAIL=...
PASSWORD=...

# ── Proxy ──────────────────────────────────────────────────────────
PROXY_URL=http://host:port
PROXY_USER=...
PROXY_PASS=...

# ── IA ─────────────────────────────────────────────────────────────
OPENAI_API_KEY=sk-...
TWO_CAPTCHA_KEY=...          # optionnel, active resolution automatique CAPTCHA

# ── Base de donnees (injecte automatiquement par fly postgres attach)
DATABASE_URL=postgres://...
STATE_BACKEND=postgres        # active le backend Postgres

# ── Paiement ───────────────────────────────────────────────────────
PAYOUT_NAME=...              # nom complet Revolut
PAYOUT_REVOLUT_TAG=@...

# ── Notifications ──────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...

# ── Mode debug local ───────────────────────────────────────────────
LOCAL_UNATTENDED=1           # simule le comportement prod
BROWSER_MODE=attach          # se connecte a un Chrome ouvert
ATTACH_DEBUGGER_ADDRESS=localhost:9222
ATTACH_TAB_URL_CONTAINS=...
LOG_LEVEL=DEBUG

# ── Pipeline d'auto-correction (attach mode uniquement) ────────────
FAILURE_PIPELINE_TRIGGER_FILE=C:/tmp/fp_trigger
# Créer ce fichier (touch / New-Item) pour déclencher manuellement
# le pipeline sur la prochaine page traitée par le bot.
# Défini automatiquement dans attach_tab.ps1.

# ── Geolocalisation (Playwright launcher) ──────────────────────────
GEO_LAT=48.8566
GEO_LON=2.3522
SURVEY_LANG=fr-FR
SURVEY_TZ=Europe/Paris
```

### Format actions OpenAI

```
Q1 //// grp_gender_q1 //// Homme //// radio //// Genre ?
Q2 //// grp_interests_q2 //// Sport | Musique //// checkbox //// Interets ?
```

### Commandes Fly.io utiles

```bash
# Deployer une nouvelle image
fly deploy --app surveybot-bot

# Voir les logs d'une machine
fly logs --app surveybot-bot

# Injecter un secret
fly secrets set OPENAI_API_KEY=sk-... --app surveybot-bot

# Lister les machines actives
fly machine list --app surveybot-bot

# Attacher une base Postgres
fly postgres attach <pg-app-name> --app surveybot-bot
```

---

> **Version**: 5.1
> **Derniere mise a jour**: Mars 2026
