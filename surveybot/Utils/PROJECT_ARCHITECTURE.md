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
- **OpenAI API** pour generer des reponses coherentes
- **AWS (ECS/Fargate)** pour l'execution en production

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
- **Automatisation**: Selenium WebDriver
- **IA**: OpenAI API (gpt-4o-mini / gpt-4o)
- **Infra**: AWS ECS Fargate, EventBridge Scheduler, Secrets Manager

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
              |   Platformes de sondage      |
              |  CloudResearch, Walr, Cint,  |
              |  QuestionPro, Decipher, etc. |
              +------------------------------+
```

### Architecture de deploiement (AWS)

```
+------------------------------------------------------------------+
|                         AWS                                      |
|  +-----------------+     +-----------------+                     |
|  | EventBridge     |---->| ECS Task:       |                     |
|  | Scheduler       |     | Scheduler       |                     |
|  | (toutes X min)  |     |                 |                     |
|  +-----------------+     +--------+--------+                     |
|                                   |                              |
|                    +--------------+--------------+               |
|                    v              v              v               |
|           +--------------+ +--------------+ +--------------+     |
|           | ECS Task:    | | ECS Task:    | | ECS Task:    |     |
|           | Bot Account1 | | Bot Account2 | | Bot AccountN |     |
|           | + Proxy1     | | + Proxy2     | | + ProxyN     |     |
|           +--------------+ +--------------+ +--------------+     |
|                                                                  |
|  +-----------------+                                             |
|  | Secrets Manager | <- credentials, API keys, proxy configs     |
|  +-----------------+                                             |
+------------------------------------------------------------------+
```

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

#### `config.py`
**Chemin**: `config.py`
**Taille**: ~154 lignes
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

3. PROD (ECS/Docker)
   - RUN_ENV=aws ou RUN_ENV=docker
   - Tout active, aucune pause interactive
```

**Fonctions principales**:

| Fonction | Description |
|----------|-------------|
| `is_local_env()` | True si environnement local |
| `is_attach_mode()` | True si mode debug sur navigateur existant |
| `is_prod_like()` | True si comportement production (AWS ou LOCAL_UNATTENDED) |
| `should_pause_for_captcha()` | True si pause interactive autorisee |
| `should_block_for_input()` | True si input() bloquants autorises |
| `should_run_guard_monitor()` | True si RuntimeGuard doit etre active |
| `should_run_heartbeat()` | True si heartbeat DynamoDB actif |
| `should_run_hot_reload()` | True si hot reload actif |
| `get_captcha_behavior()` | Retourne "pause" ou "restart" |
| `log_config_summary()` | Affiche un resume au demarrage |

---

#### `main.py`
**Chemin**: `main.py`
**Taille**: ~508 lignes
**Role**: Point d'entree principal du bot.

**Responsabilites**:
- Initialisation du driver Selenium
- Gestion du mode attach (debug sur navigateur existant)
- Lancement des threads auxiliaires (heartbeat, guard, hot reload)
- Boucle principale de traitement des surveys

**Fonctions cles**:

| Fonction | Description |
|----------|-------------|
| `_attach_tab_score(driver)` | Score un onglet pour selection automatique |
| `_attach_select_best_tab(driver)` | Selectionne l'onglet le plus pertinent |
| `_attach_is_user_web_url(url)` | Filtre les URLs utilisateur (http/https) |

---

#### `launch.py`
**Chemin**: `launch.py`
**Taille**: ~12 lignes
**Role**: Lancement d'une nouvelle session Chrome.

```python
def launch_new_chrome():
    options = Options()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--start-maximized")
    driver = webdriver.Chrome(options=options)
    return driver
```

---

### 4.2 Authentification & Etat

#### `auth_handler.py`
**Chemin**: `preselection/auth_handler.py`
**Taille**: ~276 lignes
**Role**: Authentification TopSurveys et verification de session.

**Fonctions principales**:

| Fonction | Signature | Description |
|----------|-----------|-------------|
| `_is_aws_env()` | `() -> bool` | Detecte environnement AWS |
| `dom_probe(driver)` | `(driver)` | Dump DOM pour debug (AWS only) |
| `is_session_expired(driver)` | `(driver) -> bool` | Detecte expiration de session |
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
**Taille**: ~417 lignes
**Role**: Stockage d'etat "prod-first" pour 100+ bots via DynamoDB.

**Backends**:
- **DynamoDB** (recommande en prod) : source de verite partagee
- **Fichier local** (fallback dev) : uniquement si `RUN_ENV=local`

**Structure d'etat par defaut**:
```python
{
    "account_id": str,
    "version": int,              # optimistic locking
    "banned": bool,
    "cooldown_until_ts": int,
    "status": str,               # idle | running | paused
    "lock_owner": str,
    "lock_until_ts": int,
    "proxy_id": str,
    "last_stop_reason": str,
    "last_heartbeat_ts": int,
    "daily_earned": dict,        # {"2025-12-31": 1.23}
    "total_earned": float,
    "updated_ts": int,
}
```

**Fonctions principales**:

| Fonction | Description |
|----------|-------------|
| `load_state(account_id)` | Charge l'etat depuis DynamoDB/fichier |
| `update_state(account_id, **kwargs)` | Met a jour l'etat (optimistic lock) |
| `touch_heartbeat(account_id)` | Met a jour le timestamp heartbeat |

---

#### `secret_loader.py`
**Chemin**: `preselection/secret_loader.py`
**Taille**: ~100 lignes
**Role**: Chargement robuste des secrets (AWS Secrets Manager + ENV).

**Strategie d'empilement** (priorite decroissante):
1. `TOPSURVEYS_SECRET_JSON` (variable ENV contenant JSON)
2. AWS Secrets Manager via `TOPSURVEYS_SECRET_NAME`
3. Variables ENV unitaires (TOPSURVEYS_EMAIL, OPENAI_API_KEY, etc.)

**Cles supportees**:
```python
mapping = {
    "Email": "TOPSURVEYS_EMAIL",
    "Password": "TOPSURVEYS_PASSWORD",
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
**Taille**: ~58 lignes
**Role**: Fusion config locale + secrets distants.

```python
def load_config() -> dict:
    """
    Ordre de priorite (du plus fort au plus faible):
      1) Overrides ENV + Secrets Manager (via secret_loader)
      2) Fichier local config.json (dev)
    """
```

---

### 4.3 Guards & Monitoring

#### `runtime_guard.py`
**Chemin**: `Management/guards/runtime_guard.py`
**Taille**: ~403 lignes
**Role**: Superviseur central d'execution - protege OpenAI, AWS et Proxy.

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
- `earnings_today_eur`
- `openai_calls`
- `last_activity_ts` / `last_success_ts`

---

#### `survey_difficulty_guard.py`
**Chemin**: `Management/guards/survey_difficulty_guard.py`
**Taille**: ~226 lignes
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

**Detection speciale image evaluation (Walr)**:
```python
def _detect_image_evaluation(driver) -> bool:
    # Pattern: .rsScrollGridWrappper (image) + div.rsBtn (boutons)
```

---

#### `sensitive_question_guard.py`
**Chemin**: `Management/guards/sensitive_question_guard.py`
**Taille**: ~71 lignes
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

#### `url_guard.py`
**Chemin**: `Management/guards/url_guard.py`
**Taille**: ~108 lignes
**Role**: Whitelist/blacklist des URLs de survey.

**Allowlist (sous-domaines autorises)**:
```python
ALLOWLIST = {
    "survey.walr.com", "samplicio.us", "cloudresearch.com",
    "ssisurveys.com", "decipherinc.com", "survey.cmix.com",
    "qps.cint.com", "s.cint.com", "emea.focusvision.com",
    "screener.purespectrum.com", "survey.rex.dinata.com",
    # ... etc
}
```

**Fonctions**:
```python
def normalize_host(url_or_host: str) -> str
def is_allowed(url_or_host: str) -> bool  # Guard SOFT (autorise par defaut)
```

---

#### `redirect_watcher.py`
**Chemin**: `Management/redirect_watcher.py`
**Taille**: ~177 lignes
**Role**: Surveillance des redirections URL.

**Fonctions principales**:

| Fonction | Description |
|----------|-------------|
| `wait_for_final_redirection(driver, max_wait=30)` | Attend stabilisation URL |
| `switch_to_latest_window_and_close_others(driver, base_handles)` | Switch vers nouvel onglet + cleanup |

---

#### `idle_monitor.py`
**Chemin**: `Management/idle_monitor.py`
**Taille**: ~110 lignes
**Role**: Surveille le solde et alerte si aucun gain pendant N minutes.

**Classe `GainWatchdog`**:
```python
GainWatchdog(
    driver,
    threshold_sec: int = 900,   # 15 min sans gain -> alerte
    poll_seconds: int = 900,    # intervalle de sondage
    notify_fn: Callable,
)
```

**Comportement**:
- Thread daemon autonome
- Anti-spam: une seule notification tant qu'aucun nouveau gain
- Declenche `get_guard().signal_no_gain()` apres timeout

---

#### `pause_policy.py`
**Chemin**: `Management/pause_policy.py`
**Taille**: ~62 lignes
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
**Chemin**: `notifier.py`
**Taille**: ~30 lignes
**Role**: Envoi de notifications Telegram.

```python
def send_telegram(message: str, bot_token: str, chat_id: str) -> bool:
    """Envoie un message via le bot Telegram."""
```

---

### 4.4 Preselection (TopSurveys)

#### `survey_handler.py`
**Chemin**: `preselection/survey_handler.py`
**Taille**: ~238 lignes
**Role**: Handler principal pour les surveys TopSurveys (preselection).

**Fonction principale**:
```python
def run_survey(driver, api_key, *, account_id: str):
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
**Taille**: ~394 lignes
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
**Taille**: ~216 lignes
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
**Taille**: ~149 lignes
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
**Taille**: ~4450 lignes (1.9M)
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

#### `sliderpoints_extractor.py`
**Chemin**: `Survey/sliderpoints_extractor.py`
**Taille**: ~215 lignes
**Role**: Extraction specialisee FocusVision/Decipher sliderpoints.

---

### 4.6 Core - Classification & Mapping

#### `dom_classifier.py`
**Chemin**: `Survey/dom_classifier.py`
**Taille**: ~928 lignes
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
**Taille**: ~492 lignes
**Role**: Mapping spatial des inputs via bounding boxes.

---

#### `dom_metrics.py`
**Chemin**: `Survey/dom_metrics.py`
**Taille**: ~159 lignes
**Role**: Metriques d'usage OpenAI vs traitement local.

---

#### `dom_registry.py`
**Chemin**: `Survey/dom_registry.py`
**Taille**: ~50 lignes
**Role**: Registre en memoire des cibles DOM.

**Format target_id**: `{kind}_{sha1_hash[:12]}`

---

#### `frame_utils.py`
**Chemin**: `Survey/frame_utils.py`
**Taille**: ~89 lignes
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
**Taille**: ~489 lignes
**Role**: Construction carte logique locale des inputs.

---

#### `question_block_resolver.py`
**Chemin**: `Survey/question_block_resolver.py`
**Taille**: ~671 lignes
**Role**: Resolution robuste question -> champ input.

**Securite**:
- Ne JAMAIS ecraser un champ deja rempli
- Effacement safe: Ctrl+A + Backspace + events

---

#### `dropdown_block_resolver.py`
**Chemin**: `Survey/dropdown_block_resolver.py`
**Taille**: ~283 lignes
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
**Taille**: ~65 lignes
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
**Taille**: ~2899 lignes
**Role**: Dispatch specialise pour cas complexes (card-sort, card-rating).

---

### 4.9 Core - Construction des prompts

#### `prompt_builder.py`
**Chemin**: `Survey/prompt_builder.py`
**Taille**: ~290 lignes
**Role**: Transformation question_blocks -> prompts OpenAI.

**Format de sortie**:
```
QID //// target_id //// valeur //// itype //// contexte
```

---

### 4.10 Core - Parsing des reponses

#### `batch_response_parser.py`
**Chemin**: `Survey/batch_response_parser.py`
**Taille**: ~661 lignes
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

#### `survey_solver.py`
**Chemin**: `Survey/survey_solver.py`
**Taille**: ~793 lignes
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
**Taille**: ~815 lignes
**Role**: Execution single-page.

---

### 4.12 Utilitaires - Debug & Snapshots

#### `page_snapshot.py`
**Chemin**: `Survey/page_snapshot.py`
**Taille**: ~378 lignes
**Role**: Capture complete d'une page pour debug.

**Structure sortie**:
```
snapshot_20250211_143052/
  main.html
  metadata.json
  screenshot.png
  frames/
```

---

#### `replay_snapshot.py`
**Chemin**: `tools/replay_snapshot.py`
**Taille**: ~395 lignes
**Role**: Rejoue un snapshot DOM et compare avec baseline.

```bash
python tools/replay_snapshot.py <snapshot_dir> --save-baseline
python tools/replay_snapshot.py <snapshot_dir>  # compare avec baseline
```

---

#### `screenshot_analyzer.py`
**Chemin**: `Survey/screenshot_analyzer.py`
**Taille**: ~296 lignes
**Role**: Vision API fallback (DEPRECIE).

---

#### `hot_reload.py`
**Chemin**: `tools/hot_reload.py`
**Taille**: ~81 lignes
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
**Taille**: ~251 lignes
**Role**: Detection, lecture et capture audio des videos.

---

### 4.14 Cash & Payout

#### `payout.py`
**Chemin**: `Cash/payout.py`
**Taille**: ~426 lignes
**Role**: Gestion des paiements et cashout TopSurveys.

**Fonctions principales**:

| Fonction | Description |
|----------|-------------|
| `_read_balance(driver)` | Lit le solde actuel (widget TopSurveys) |
| `_open_cashout_modal(driver)` | Ouvre le modal d'encaissement |
| `_select_money_option_in_open_tab(driver, tab, amount)` | Selectionne option paiement |
| `do_cashout(driver, amount)` | Execute le cashout complet |

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

---

## 9. Conventions de code

- Fonctions privees: `_helper_function()`
- Constantes: `MAX_RETRIES = 3`
- Guard clauses pour early return

---

## 10. Quick Reference

### Variables d'environnement

```bash
# Obligatoires
OPENAI_API_KEY=sk-...
TOPSURVEYS_EMAIL=...
TOPSURVEYS_PASSWORD=...

# Optionnelles
RUN_ENV=local|aws|docker
LOCAL_UNATTENDED=1
PROXY_HOST=...
DEBUG=true
HEADLESS=true
```

### Format actions OpenAI

```
Q1 //// grp_gender_q1 //// Homme //// radio //// Genre ?
Q2 //// grp_interests_q2 //// Sport | Musique //// checkbox //// Interets ?
```

---

> **Version**: 4.0
> **Derniere mise a jour**: Fevrier 2025