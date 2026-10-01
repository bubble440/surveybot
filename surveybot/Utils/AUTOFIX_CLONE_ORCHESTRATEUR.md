# Clone dédié à l'orchestrateur autofix

Ce document décrit la mise en place et l'usage du **clone Git séparé** dans
lequel tourne `tools\run_autofix_pipeline.py` (cf. `Utils/SURVEYBOT_AUTOFIX_PLAN.md`,
suites 35-37). Les décisions ci-dessous sont actées, pas à rediscuter ; seules
les valeurs par défaut des scripts sont paramétrables.

## Pourquoi un clone séparé

`Survey/autofix/merge_executor.py` (étape aval de l'orchestrateur) exige que le dépôt
où il s'exécute soit **entièrement propre** (`git status --porcelain
--untracked-files=all` vide, fichiers non suivis inclus) avant de merger un
correctif, et refuse toute branche cible protégée (`main`, `prod`,
`playwright-migration`, cf. `Survey/autofix/autofix_worktree.py::PROTECTED_BRANCHES`).
Faire tourner l'orchestrateur dans le dépôt où l'opérateur code le bloque dès
qu'un développement est en cours (fichiers modifiés ou non suivis) ; et si la
branche de développement est l'une des branches protégées, le merge y serait
refusé de toute façon. D'où un **clone dédié**, en permanence sur sa propre
branche d'intégration non protégée.

## Schéma du flux

```
Dépôt de l'opérateur                    Clone dédié autofix
(branche de développement,        clone      (branche d'intégration,
 ex. feature/phase-1a-...)  ────────────►     ex. "autofix-integration")
        ▲                    (local, Part.A)        │
        │                                            │  tools\run_autofix_pipeline.py
        │  git fetch + git merge                     │  (Phases 8-13, aval+amont, planifié
        │  (manuel, hors périmètre                   │   ou manuel — Part.D)
        │   de l'orchestrateur)                       ▼
        │                                    correctifs mergés localement
        └────────────────────────────────    dans "autofix-integration"
           récupération à son rythme          (jamais poussés, jamais mergés
                                                dans la branche de dev automatiquement)
```

Un seul poller Telegram (`Survey/autofix/human_review.py::check_pending_reviews`) : il
tourne dans le clone, jamais ailleurs. Telegram ne fournit qu'un flux
`getUpdates` par bot — un second checkout qui lancerait aussi
`tools\check_human_review.py` sur le même bot consommerait les mêmes mises à
jour de façon imprévisible. **Ne jamais lancer ce poller depuis le dépôt de
l'opérateur en parallèle du clone.**

## Scripts (`surveybot/tools/`)

| Script | Rôle | Modifie l'état Git ? |
|---|---|---|
| `setup_autofix_clone.ps1` (Partie A) | Crée le clone + la branche d'intégration + l'environnement (venv, dépendances, ruff, Chromium) si absents. Idempotent, ne réinitialise jamais un état existant. | Oui, une seule fois (création) |
| `preflight_autofix_clone.ps1` (Partie B) | Vérifie l'état du clone avant une invocation. Lecture seule. | Non |
| `sync_autofix_clone.ps1` (Partie C) | Amène les commits récents de la branche de développement dans la branche d'intégration. Refuse si verrou actif, dépôt sale, ou mauvaise branche. | Oui (fetch + merge, jamais de push) |
| `schedule_autofix_pipeline_task.ps1` (Partie D, optionnel) | Enregistre une tâche planifiée Windows exécutant le pipeline depuis le clone. N'exécute jamais le pipeline lui-même. | Non (registre Windows uniquement) |
| `introspect_autofix_pipeline.py` | Sonde en lecture seule utilisée par B et C (constantes réelles du code : branches protégées, verrou, racines d'artefacts — jamais recopiées à la main). | Non |

## Mise en place (première fois)

**Ordre impératif :**

1. **`.gitignore` du dépôt opérateur déjà à jour** avec toutes les racines
   d'artefacts du pipeline (`failure_cases/`, `diagnoses/`, `autofix_worktrees/`,
   `autofix_pipeline_runs/`, etc. — déjà le cas sur `feature/phase-1a-observability`
   depuis le commit `feat(.gitignore): add entries for autofix pipeline
   artifacts`). **Avant** de créer le clone : sinon le premier merge automatique
   échouera pour cause de dépôt non propre dès qu'un case y sera écrit.
2. Depuis le dépôt opérateur :
   ```powershell
   cd C:\chemin\vers\surveybot\tools
   .\setup_autofix_clone.ps1
   ```
   Crée `<nom-du-dépôt>-autofix-clone` (par exemple `C:\projects\Surveys-autofix-clone`
   pour un dépôt situé dans `C:\projects\Surveys` ; frère du dépôt opérateur, hors de son
   arbre suivi), sa branche `autofix-integration`, son venv, ses dépendances,
   `ruff` et Chromium (Playwright).
3. Configurer les variables d'environnement requises pour le compte Windows qui
   exécutera le pipeline (jamais leur valeur dans un fichier suivi par Git) :
   `telegram_bot_token`, `telegram_chat_id`, et `FLEET_R2_ACCOUNT_ID` /
   `FLEET_R2_ACCESS_KEY_ID` / `FLEET_R2_SECRET_ACCESS_KEY` / `FLEET_R2_BUCKET`
   si `--import-fleet` est utilisé.
4. Vérifier avant tout lancement :
   ```powershell
   cd <chemin-du-clone>\surveybot\tools   # ex. cd C:\projects\Surveys-autofix-clone\surveybot\tools
   .\preflight_autofix_clone.ps1
   ```
5. (Optionnel) Planifier :
   ```powershell
   .\schedule_autofix_pipeline_task.ps1
   ```
   Le lanceur généré est un frère du dossier du clone, nommé
   `<nom-du-clone>.run_autofix_pipeline_task_launcher.ps1` (par exemple à côté
   de `Surveys-autofix-clone/` dans l'exemple ci-dessus). Il reste hors du dépôt Git ; `-Unregister`
   retire aussi ce lanceur. Un ancien lanceur dans le paquet du clone est retiré
   lors d'une nouvelle planification ou désinstallation seulement si son en-tête
   de génération est reconnu.

Aucun de ces scripts ne lance l'orchestrateur lui-même, ne pousse quoi que ce
soit vers un dépôt distant, ni ne merge dans une branche protégée.

## Utilisation courante

- Avant chaque planification ou lancement manuel : `preflight_autofix_clone.ps1`.
- Pour que le clone ne dérive pas de la branche de développement :
  `sync_autofix_clone.ps1` (refuse automatiquement si le pipeline tourne déjà
  ou si le clone n'est pas dans un état sûr).
- **Récupération des correctifs par l'opérateur** — hors du périmètre de
  l'orchestrateur, à son rythme, depuis son propre dépôt :
  ```powershell
  git remote add autofix-clone <chemin-du-clone>   # une seule fois, ex. ..\Surveys-autofix-clone
  git fetch autofix-clone autofix-integration
  git merge autofix-clone/autofix-integration
  ```
  (ou `git fetch <chemin-du-clone> autofix-integration` sans remote nommé pour
  un usage ponctuel). Résoudre tout conflit comme un merge Git normal.

## Limites connues

- **Un patch n'est jamais validé contre les patchs déjà mergés avant lui**,
  seulement contre l'état d'origine (`base_sha` figé en Phase 7). Git détecte
  les conflits de lignes lors de la récupération par l'opérateur, jamais les
  incompatibilités de logique entre deux correctifs indépendants touchant la
  même zone fonctionnelle sans se chevaucher textuellement.
- **`Survey/BOT_EVOLUTION_MEMORY.md` est append-only.** Deux branches autofix
  qui ajoutent chacune une entrée en fin de fichier produisent un conflit
  textuel trivial (mêmes lignes de contexte) au moment où l'opérateur récupère
  les correctifs — attendu, à résoudre en conservant les deux entrées.
- **Un seul poller Telegram à la fois** (cf. ci-dessus) — jamais depuis deux
  checkouts simultanément.
- Le dossier réel des worktrees Git (Phase 7, distinct de l'artefact de
  traçabilité `autofix_worktrees/`) est un frère du clone
  (`<clone>-worktrees`) : depuis le 1er octobre 2026, l'orchestrateur retire
  lui-même le worktree et la branche d'un case mergé avec succès (aucun de ces
  scripts n'y intervient) ; les worktrees des cases non mergés (rejetés ou en
  échec) sont conservés volontairement et leur nettoyage manuel reste à la
  charge de l'opérateur (cf. `GUIDE_EXPLOITATION_AUTOFIX.md`, §6 I).
- Lancer `tools\run_autofix_pipeline.py` en invoquant directement
  `venv\Scripts\python.exe` (sans l'« activer ») ne préfixe pas automatiquement
  `venv\Scripts` au `PATH` du processus : `Survey/autofix/static_validator.py` (Phase 8)
  résout `ruff` via `shutil.which`, qui échouerait silencieusement sans ce
  préfixe. `schedule_autofix_pipeline_task.ps1` le fait automatiquement pour la
  tâche planifiée ; un lancement manuel doit faire de même (rappelé en fin de
  sortie de `setup_autofix_clone.ps1`).