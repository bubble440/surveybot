# ORCHESTRATION_TRACKING.md

Suivi des décisions et modifications relatives à l'orchestration du lancement,
de la supervision et du redémarrage des bots SurveyBot en déploiement bare-metal
Windows (NiPoGi, NSSM). À lire par Claude Code avant tout diagnostic ou
correction touchant à l'orchestration (démarrage, arrêt, redémarrage,
scheduler, codes de sortie).

Ce fichier documente le **quoi et le pourquoi** des décisions prises. Le détail
d'implémentation (comment) est dans le code lui-même, commenté en conséquence.

---

## 1. Contexte

Ancienne architecture (Fly.io) : orchestrateur externe (Fly Machines) gérait
nativement le redémarrage des containers crashés. Ce comportement n'a **aucun
équivalent natif** en bare-metal Windows — il a fallu le reconstruire
explicitement à partir de trois briques complémentaires, chacune responsable
d'une cause d'arrêt différente :

| Brique                  | Rôle                                                          | Fréquence          |
|--------------------------|----------------------------------------------------------------|---------------------|
| NSSM (service par bot)   | Redémarre un bot qui crash ou fait un soft_restart              | Réactif (immédiat)  |
| `check_zombie_bots.ps1`  | Redémarre un bot vivant mais bloqué (heartbeat périmé)          | Tâche planifiée, 3 min |
| `wake_scheduler.ps1`     | Relance un bot arrêté volontairement (cooldown) une fois expiré | Tâche planifiée, 5 min |

Principe directeur validé : **1 bot = 1 service NSSM persistant = 1 proxy**.
Pas de scheduler qui relance périodiquement "tous les bots de accounts.json" —
chaque bot est un service dédié et continu ; les trois briques ci-dessus ne
font que décider *quand* NSSM doit (re)démarrer ce service.

### 1bis. Chemins de déploiement — dev vs prod (à ne jamais confondre)

- **Machine de dev** : `C:\projects\Surveys\surveybot` — Python + `.venv`,
  code source, exécution via `python main.py`.
- **Machine de prod (NiPoGi mini PC), parc interne** : **`C:\surveybot\`** —
  code source Python (dossier `code\`), déployé par auto-update R2
  (`update_checker.py`), exécuté via un interpréteur Python présent dans un
  `venv\` propre à chaque machine : `venv\Scripts\python.exe code\main.py`.
  **Un Python/venv complet est bien installé sur cette machine** — pas de
  binaire compilé pour le parc interne. Vérifiable directement dans le code :
  `build_release_zip.ps1` (construit l'archive source uploadée sur R2),
  `nssm_setup_bot.ps1` (`Application = venv\Scripts\python.exe`,
  `AppParameters = code\main.py`), `setup_machine.ps1` (crée `venv\` et y
  installe `requirements.txt`, une seule fois par machine).
  **Correction (24/07/2026)** : cette section affirmait auparavant l'inverse
  (binaire `surveybot.exe` Nuitka onefile, aucun Python en prod). C'était
  exact au moment de la migration Nuitka (voir historique en section 6), mais
  obsolète depuis le pivot du 20/07/2026 vers ce mécanisme source+venv pour le
  parc interne. Le binaire compilé Nuitka (`nuitka_build_release.ps1`) sert
  désormais exclusivement au transfert à des tiers/récepteurs externes — un
  pipeline distinct du parc interne, voir
  `Utils/DEPLOIEMENT_BAREMETAL_DECISIONS.md` section 4.
- **Conséquence pratique** : un paquet Python ajouté à `requirements.txt` ne se
  propage PAS automatiquement sur une machine du parc interne déjà
  provisionnée — l'auto-update (`update_checker.py`) ne remplace que le
  dossier `code\`, jamais `venv\`. La mise à jour des dépendances n'est
  appliquée que par `setup_machine.ps1`, exécuté une seule fois par machine
  (à l'installation initiale, ou manuellement rejoué pour rafraîchir `venv\`).
- Cette distinction dev/prod reste utile : toute logique de résolution de
  chemin (`secret_loader.py::_bot_root_dirs()`, section 5) doit cibler
  `C:\surveybot\` par défaut pour la prod, jamais un chemin de dev codé en dur
  ou supposé — historique du bug qui a motivé cette règle en section 6
  (`query_cooldown_status.py`, invoqué via un chemin Python de dev absent en
  prod à l'époque du binaire Nuitka).

---

## 2. Codes de sortie normalisés (`bot_supervisor.py`)

Contrat central entre le bot et NSSM. Toute nouvelle cause d'arrêt doit être
mappée sur un de ces 4 codes — ne pas en ajouter sans raison forte (principe
de prédictibilité : peu de branches).

| Code | Nom                 | NSSM (`AppExit`) | Déclencheurs                                                        |
|------|---------------------|-------------------|----------------------------------------------------------------------|
| 0    | `EXIT_VOLUNTARY`     | `Exit`            | SIGINT/SIGBREAK propre, objectif journalier atteint, session expirée, proxy expiré |
| 1    | `EXIT_CRASH`         | `Restart`         | Crash Python, kill forcé (valeur sentinel écrite au démarrage)       |
| 2    | `EXIT_SOFT_RESTART`  | `Restart`         | Idle timeout, trop d'erreurs, runtime_limit atteint                  |
| 3    | `EXIT_FATAL`         | `Exit`            | Seuil de redémarrages dépassé (crash-loop) → alerte Telegram         |

Mécanique sentinel (`check_and_record_start`) : au démarrage, le bot écrit
immédiatement `last_exit_code = 1` (crash) dans son fichier d'état local. Si
le process est tué de force avant d'appeler `record_exit()`, le prochain
démarrage lit correctement ce run comme un crash. `record_exit()` écrase ce
sentinel avec le vrai code en fin de run normal.

État persisté localement (pas en Postgres) dans `pids\bot_<account_id>.state` :
`account_id`, `pid`, `restart_count`, `restart_window_start_ts`,
`last_start_ts`, `last_exit_code`, `last_exit_reason`, `last_exit_ts`,
`last_heartbeat_ts`.

**Décision** : ce fichier reste local (pas Postgres) — c'est un état de
supervision machine-locale, pas un état métier partagé entre bots/machines.

---

## 3. Signaux d'arrêt sous Windows — piège identifié et corrigé

- `SIGTERM` enregistré dans `launch.py` est **inerte sous Windows** pour un
  signal envoyé par un process externe (NSSM). Conservé uniquement pour
  portabilité Linux / `os.kill` intra-process.
- NSSM envoie en réalité `CTRL_BREAK_EVENT` (pas `CTRL_C_EVENT`, qui ne peut
  cibler qu'un process du même groupe console). Sous Python Windows, ça
  correspond à `signal.SIGBREAK`, **pas** à `SIGINT`.
- **Bug initial** : seul `SIGINT` était géré → un `nssm stop` tuait le process
  sans jamais passer par le cleanup (`record_exit`, fermeture Chrome,
  écriture Postgres) → le sentinel `EXIT_CRASH` persistait → le redémarrage
  suivant comptait à tort un arrêt de maintenance comme un crash.
- **Correction** : handler `SIGBREAK` ajouté dans `install_sigint_handler`
  (guard `hasattr(signal, "SIGBREAK")`, no-op sur Linux), réutilisant la même
  factory `_make_stop_handler` → écrit `EXIT_VOLUNTARY` avant de sortir.

## 4. Timeouts d'arrêt NSSM — piège identifié et corrigé

- Défauts NSSM : 4 méthodes d'arrêt en cascade (Console, Window, Thread,
  TerminateProcess), ~1,5 s chacune par défaut. Window/Thread n'ont aucun
  effet sur un process console Python → 3 s perdues avant le kill forcé.
- **Correction** (`nssm_setup_bot.ps1`) :
  - `AppStopMethodSkip 6` (bitmask Window=2 + Thread=4) → on saute directement
    à Console puis TerminateProcess.
  - `AppStopMethodConsole 30000` → 30 s de marge pour la séquence de fermeture
    propre (Chrome + Postgres + écriture état local).
- Risque résiduel non traité : si le nettoyage dépasse 30 s (Chrome
  réellement gelé), `TerminateProcess` s'enclenche et peut laisser des
  process Chrome enfants orphelins. À surveiller en usage réel ; pas de
  correction préventive appliquée pour l'instant (pas de symptôme observé).

---

## 5. Résolution de chemins bare-metal (`secret_loader.py`)

Fonction `_bot_root_dirs()` (refactorée depuis l'ancienne
`_receiver_config_candidates()`) : liste ordonnée de dossiers candidats pour
tout fichier de config/état local (`receiver_config.json`, `pids/`, etc.),
`C:\surveybot\` en tête absolue (confirmé en prod), puis
`NUITKA_ONEFILE_BINARY`, `sys.executable`, `__file__`, `cwd` en repli.

**Règle** : toute nouvelle fonctionnalité ayant besoin de résoudre un chemin
relatif à l'installation du bot doit consommer `_bot_root_dirs()` — ne jamais
recréer une logique de résolution de chemin en parallèle (ça avait déjà
dérivé une fois avec le chemin en dur dans l'ancienne fonction).

---

## 6. Mode CLI `--query-cooldown` (`main.py`)

- **Problème résolu (historique, contexte binaire Nuitka)** : `wake_scheduler.ps1`
  devait interroger le cooldown Postgres par compte. Un script Python autonome
  (`query_cooldown_status.py`) avait été créé, invoqué via un interpréteur
  Python + venv — à l'époque (parc interne sur binaire Nuitka onefile, avant
  le pivot du 20/07/2026 vers le pipeline source+venv, voir section 1bis),
  ceux-ci étaient effectivement absents de la machine cible. Le script
  échouait silencieusement en prod.
- **Décision (toujours valide, mécanisme d'invocation mis à jour)** : exposer
  ce besoin directement dans `main.py` via un argument CLI
  (`--query-cooldown <id1> <id2> ...`), intercepté en tout début du fichier,
  avant `load_config()`, `check_license_or_exit()` et tout import lourd.
  Sortie JSON sur stdout, `sys.exit(0)` systématique. Réutilise
  `State.account_state.load_state()` sans dupliquer la logique Postgres.
  **Correction (24/07/2026)** : depuis le pivot du 20/07/2026, le parc interne
  invoque ce mode via l'interpréteur Python du venv local, pas via un binaire
  compilé — `wake_scheduler.ps1` appelle littéralement
  `venv\Scripts\python.exe code\main.py --query-cooldown <ids>` (voir son
  code). L'invocation `surveybot.exe --query-cooldown` décrite ci-dessus à
  l'origine ne s'applique qu'au pipeline de transfert à des tiers (binaire
  Nuitka), pas au parc interne.
- `query_cooldown_status.py` a été supprimé (obsolète).
- **Vérifié** : `State.account_state` s'importe sans dépendre de
  `load_config()` (il lit `global_config`/`_license_config`, compilés dans le
  binaire) — le mode CLI est bien autonome et rapide, sans effet de bord sur
  le flux de démarrage normal d'un bot.

---

## 7. Injection des secrets PAR_BOT dans NSSM (`nssm_setup_bot.ps1`)

- **Problème résolu** : la première version du script ne configurait qu'un
  bot à la fois via paramètres manuels (`-AccountId`, `-ProxyUrl`) et
  n'injectait que `ACCOUNT_ID`/`PROXY_URL`/`BROWSER_MODE` — pas
  `EMAIL`/`PASSWORD`/`PROXY_USER`/`PROXY_PASS`, pourtant nécessaires
  (`secret_loader.py::_from_direct_env_keys()` les lit directement depuis
  `os.environ`).
- **Décision** : le script lit désormais `accounts.json` directement et
  boucle sur l'ensemble des comptes (ou un sous-ensemble via `-AccountId` en
  filtre). Un service NSSM par compte, idempotent (installe si absent,
  reconfigure si présent).
- Variables injectées par bot : `ACCOUNT_ID`, `EMAIL`, `PASSWORD`,
  `PROXY_URL`, `PROXY_USER`, `PROXY_PASS`, `CHROME_PROFILE_DIR` (alias de
  `profile_dir`), plus défauts fixes `GEO_LAT`/`GEO_LON`/`SURVEY_LANG`/
  `SURVEY_TZ` et `PYTHONIOENCODING`/`PYTHONUTF8`. **Mise à jour (cf. section 9)** :
  `launch_all.ps1` a été réintroduit le 20/07/2026 et définit à nouveau les
  mêmes défauts `GEO_LAT`/`GEO_LON`/`SURVEY_LANG`/`SURVEY_TZ` de son côté —
  l'affirmation précédente (« ne vivent plus que dans ce script ») ne tient
  donc plus ; les deux fichiers doivent être tenus synchronisés manuellement.
- **Sécurité** : `PASSWORD` n'est jamais affiché dans les logs du script.
- **Détection d'orphelins** : un service `surveybot_*` sans entrée
  correspondante dans `accounts.json` est signalé (`Write-Warning`) mais
  **jamais supprimé automatiquement** — décision humaine requise.

---

## 8. `wake_scheduler.ps1` — relance après cooldown expiré

- Complémentaire de `check_zombie_bots.ps1` (celui-ci ignore explicitement
  les arrêts volontaires/fatals — `last_exit_code` 0 ou 3 — car ce n'est pas
  son rôle de les relancer).
- Tourne en tâche planifiée (`Register-ScheduledTask`, pas un process
  continu), toutes les 5 min. Peut être suspendu (`Disable-ScheduledTask`) ou
  retiré (`Unregister-ScheduledTask`) sans toucher à NSSM.
- Logique : lit `accounts.json` → interroge le cooldown via
  `venv\Scripts\python.exe code\main.py --query-cooldown` (une seule
  connexion Postgres pour tous les comptes — **correction (24/07/2026)** :
  invocation réelle vérifiée dans le code du script, pas un binaire compilé,
  voir section 1bis/6) → ignore les comptes en `EXIT_FATAL` (fichier `.state`
  local, `last_exit_code == 3`) → ignore les services déjà `SERVICE_RUNNING`
  → `nssm start` pour le reste.
- Garde-fou boucle `MAX_ACCOUNTS = 200`.

---

## 9. `launch_all.ps1` — statut

- **Confirmé non actif** sur les machines de production (pas de tâche
  planifiée existante) au 13/07/2026.
- **Supprimé le 13/07/2026**, sur la base du constat ci-dessus : NSSM +
  `wake_scheduler.ps1` + `check_zombie_bots.ps1` couvraient alors
  l'intégralité de son rôle.
- **Réintroduit le 20/07/2026**, puis **son rôle a été restreint le
  26/07/2026** suite au constat suivant : `check_zombie_bots.ps1` et
  `wake_scheduler.ps1` n'agissent tous les deux **que** sur des noms de
  service NSSM (`nssm status`/`start`/`restart surveybot_<id>`) — ils n'ont
  strictement aucune connaissance des fichiers `pids\bot_<id>.pid` ni des
  process bruts lancés par `launch_all.ps1`. Le header du script recommandait
  pourtant explicitement de le planifier via le Planificateur de tâches
  Windows : un compte démarré ainsi aurait été invisible pour les deux
  briques de supervision périodique (aucune détection zombie, aucune relance
  après cooldown).
- **Décision (26/07/2026)** : `launch_all.ps1` n'est plus un mécanisme de
  démarrage de parc. Le parc de production est exploité **exclusivement** via
  les services NSSM installés par `nssm_setup_bot.ps1`, seuls couverts par
  `check_zombie_bots.ps1`/`wake_scheduler.ps1`. `launch_all.ps1` reste
  disponible mais uniquement pour un **lancement manuel et ponctuel d'un
  compte isolé** (test), jamais planifié :
  - Le paramètre `-AccountId` est désormais obligatoire (plus de boucle sur
    l'ensemble d'`accounts.json`).
  - Un garde-fou (`Test-NssmServiceExists`) refuse le lancement si un service
    NSSM `surveybot_<id>` est déjà installé pour ce compte (quel que soit son
    statut), pour éviter un double lancement sur le même profil Chrome/proxy.
  - Le header ne recommande plus de planification via le Planificateur de
    tâches Windows.
  - Aucune logique existante (`Start-Bot`, `Test-BotProcessAlive`, détection
    PID recyclé) n'a été modifiée — le garde-fou est additif, exécuté avant
    la vérification PID existante.
- Vérification `profile_dir` (que `launch_all.ps1` fait avant de lancer un
  bot) est également portée dans `nssm_setup_bot.ps1` : un compte dont
  `profile_dir`/`CHROME_PROFILE_DIR` est vide ou pointe vers un dossier
  inexistant est skippé (`Write-Warning`, service NSSM non configuré), sans
  être signalé comme "orphelin" (son `account_id` est ajouté au set de
  comptes connus avant le check, pas après).
- **Point de vigilance résiduel** : `launch_all.ps1::Start-Bot` définit ses
  propres défauts `GEO_LAT`/`GEO_LON`/`SURVEY_LANG`/`SURVEY_TZ`, séparément de
  ceux de `nssm_setup_bot.ps1` (cf. section 7). Toujours dupliqué à deux
  endroits — non corrigé à ce jour.
- **Décision du 26/07/2026 explicitement révisée le 16/08/2026 (section 18)** :
  `launch_all.ps1` redevient le mécanisme de démarrage de parc (bascule
  NSSM → PID, session interactive). `-AccountId` redevient optionnel (vide =
  tous les comptes) ; le garde-fou `Test-NssmServiceExists` reste néanmoins
  inchangé (protection utile pendant la transition, tant que NSSM n'est pas
  décommissionné) et s'applique désormais par bot dans la boucle "tous les
  comptes", pas seulement en usage mono-compte.

---

## 10. Fichiers créés / modifiés (récapitulatif)

**Nouveaux fichiers :**
- `bot_supervisor.py` — codes de sortie, heartbeat local, compteur crash-loop
- `check_zombie_bots.ps1` — détection zombie (heartbeat périmé) → `nssm restart`
- `wake_scheduler.ps1` — relance après cooldown expiré → `nssm start`
- `nssm_setup_bot.ps1` — configuration NSSM par bot depuis `accounts.json`

**Fichiers modifiés :**
- `secret_loader.py` — extraction de `_bot_root_dirs()` (résolution de dossier générique)
- `runtime_guard.py` — `heartbeat()` écrit l'état local ; `pause()` détermine et enregistre le code de sortie
- `launch.py` — handler `SIGBREAK` ajouté ; `EXIT_VOLUNTARY` sur arrêt propre
- `main.py` — check `check_and_record_start()` au démarrage (seuil crash-loop) ; mode CLI `--query-cooldown`
- `captcha_solver.py` — appel à `load_config()` désormais conditionné à `is_attach_mode()`

**Fichiers supprimés :**
- `State/query_cooldown_status.py` — supprimé (remplacé par le mode CLI du binaire)
- `launch_all.ps1` — supprimé le 13/07/2026, **puis réintroduit le 20/07/2026 et
  activement maintenu depuis** (voir section 9 pour le détail et le statut à
  jour) ; ne plus le considérer comme supprimé du projet.

---

## 11. Points ouverts (non résolus à ce jour)

1. **Risque de process Chrome orphelins** si le timeout de 30 s de fermeture propre est dépassé (cf. section 4) — à surveiller, pas encore instrumenté.
2. **Validation en conditions réelles non faite** : tous les correctifs ci-dessus ont été relus sur le code mais pas encore testés sur une machine de production réelle (rebuild Nuitka + déploiement + observation d'un cycle complet crash/zombie/cooldown).
3. **Sentinel `EXIT_CRASH` toujours non écrasé sur 3 chemins de sortie** (`max_restart_depth_reached`, `browser_launch_failed`, `CHROME_PROFILE_DIR` manquant/introuvable) — même défaut que celui corrigé en section 13 pour `MAX_MAIN_CYCLES`, mais pas encore traité sur ces trois chemins.
4. **Correctif `-ExecutionPolicy Bypass` (section 14) non redéployé sur tout le parc** : validé sur une seule machine à ce jour ; les autres machines déjà provisionnées ont probablement le même défaut sur leurs 3 tâches planifiées tant que `Set-ScheduledTaskAction` n'y est pas rejoué manuellement.

---

## 12. Collision secrets attach/prod (`captcha_solver.py`)

- **Problème résolu** : en mode attach (`BROWSER_MODE=attach`), `attach_tab.ps1`
  injecte déjà directement dans `os.environ` toutes les clés nécessaires
  (`TWO_CAPTCHA_KEY`, `CAPSOLVER_API_KEY`, `OPENAI_API_KEY`) avant de lancer
  `main.py`. Malgré ça, `captcha_solver.py` appelait
  `preselection.config_loader.load_config()` au niveau module (à l'import),
  sans jamais vérifier `is_attach_mode()` — contrairement au garde déjà en
  place dans `main.py` pour ce même besoin. Conséquence : une session de debug
  lisait quand même `receiver_config.json`, le fichier de config partagé de la
  machine de prod, sans raison fonctionnelle (les valeurs d'env avaient déjà
  priorité), et produisait des logs `[SECRETS]`/`[CONFIG_LOADER]` trompeurs en
  contexte attach.
- **Décision** : `captcha_solver.py` ne doit plus appeler `load_config()` sans
  condition — le garde `is_attach_mode()` (déjà centralisé dans `config.py`,
  même pivot que `should_run_guard_monitor()`/`should_run_heartbeat()`) doit
  couvrir ce point d'appel aussi, pas seulement celui de `main.py`.
- **Principe retenu** : un seul point de vérité pour "charge-t-on
  `receiver_config.json` ou pas" ne suffit pas si plusieurs modules peuvent
  déclencher `load_config()` indépendamment. Toute nouvelle dépendance à une
  clé PAR_RECEPTEUR doit lire l'environnement déjà peuplé (`os.getenv(...)`),
  pas rappeler `load_config()` de son côté.
- **Validé le 13/07/2026** en conditions réelles (session attach via
  `run_tabs.ps1` → `attach_tab.ps1`).

  ---

## 13. Sentinel `EXIT_CRASH` sur le recyclage périodique (`MAX_MAIN_CYCLES`) — corrigé partiellement

- **Problème** : à la sortie normale de la boucle `while cycle < max_cycles` dans
  `main.py::main` (recyclage volontaire et sain du process toutes les
  `MAX_MAIN_CYCLES` itérations, défaut 3), le code libérait bien le slot
  Postgres (`update_state(status="idle", cooldown_until_ts=epoch, ...)`) mais
  ne rappelait jamais `bot_supervisor.record_exit()` avant de faire
  `raise SystemExit("max_main_cycles_reached")`. Le sentinel `EXIT_CRASH`
  écrit par `check_and_record_start()` au tout début du run restait donc en
  place dans `pids\bot_<id>.state`. Au démarrage suivant,
  `check_and_record_start()` lisait ce sentinel comme un crash et incrémentait
  `restart_count` à tort — un recyclage sain répété plusieurs fois dans la
  fenêtre de 10 minutes pouvait donc faire atteindre le seuil de crash-loop
  (5 redémarrages) et déclencher `EXIT_FATAL` sans qu'aucun crash réel ne se
  soit produit.
- **Correction appliquée** (`main.py`, sortie de boucle `MAX_MAIN_CYCLES`) :
  appel explicite à `bot_supervisor.record_exit(account_id, EXIT_VOLUNTARY,
  "max_main_cycles_reached")` juste avant le `raise SystemExit`, dans le même
  bloc `if not is_attach_mode()` que la libération du slot Postgres — même
  principe que la correction `SIGBREAK` de la section 3 : marquer
  explicitement un arrêt sain comme `EXIT_VOLUNTARY` côté supervision locale,
  pour que le prochain démarrage réinitialise `restart_count` au lieu de
  l'incrémenter.
- **Point de vigilance** : le code réel renvoyé au process par
  `SystemExit("max_main_cycles_reached")` (argument chaîne) reste `1` côté
  OS/NSSM — NSSM continuera donc de redémarrer immédiatement le service via
  `AppExit Default → Restart`, ce qui correspond à l'intention (recyclage
  volontaire, pas une pause). Seule la donnée locale utilisée par
  `check_and_record_start()` change ; ce n'est pas un changement de
  comportement NSSM.
- **Portée du correctif — partielle** : l'audit (`Utils/AUDIT_ARRET_RELANCE_BOTS.md`,
  Observation 5) recense trois autres chemins de sortie qui présentent
  exactement le même défaut (sentinel `EXIT_CRASH` jamais écrasé) et qui
  **ne sont pas couverts par ce correctif** : `max_restart_depth_reached`
  (`preselection/survey_handler.py::run_survey`), `browser_launch_failed` et
  les deux cas `CHROME_PROFILE_DIR` manquant/introuvable
  (`launch.py::launch_driver_or_fail` / `preselection/playwright_launcher.py`).
  Ces chemins restent à corriger séparément si le même risque de faux
  crash-loop y est jugé pertinent.
- **Non validé en conditions réelles** à ce jour (relecture de code
  uniquement — voir section 11).

  ---

## 14. Tâches planifiées SYSTEM bloquées par la politique d'exécution — corrigé

- **Problème observé en prod (28/07/2026)** : `wake_scheduler.ps1` ne relançait
  jamais un bot dont le cooldown Postgres était pourtant expiré depuis
  longtemps. `Get-ScheduledTaskInfo` indiquait `LastTaskResult = 1` et
  `C:\surveybot\logs\wake_scheduler_task.log` **n'existait pas du tout** —
  signe que le script n'atteignait même pas sa première instruction
  (`Start-Transcript`), donc un blocage en amont du code, pas un bug de
  logique métier.
- **Cause racine** : la checklist de provisioning (`set-up.txt`) applique
  `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`.
  Ce scope ne s'applique qu'à l'utilisateur interactif qui l'exécute. Les 3
  tâches planifiées (`SurveyBot_ZombieCheck`, `SurveyBot_WakeScheduler`,
  `SurveyBot_OrchestrationSync`) tournent sous le principal `SYSTEM`
  (`-LogonType ServiceAccount`, cf. `set-up.txt` section CHECKLIST), qui lit
  le scope `LocalMachine` — resté `Undefined` sur la machine concernée
  (confirmé par `Get-ExecutionPolicy -List`), ce qui fait retomber PowerShell
  sur `Restricted` par défaut pour ce compte. Le script était donc bloqué à
  chaque exécution planifiée, silencieusement (aucune sortie capturée nulle
  part, puisque le blocage intervient avant que le script ait la main).
- **Correction appliquée** : ajout de `-ExecutionPolicy Bypass` dans
  l'argument `powershell.exe` de la tâche elle-même (`New-ScheduledTaskAction
  -Argument "-NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden
  -File ..."`), pour les 3 tâches. Portée volontairement limitée au process
  de la tâche (scope `Process`), sans toucher à la policy globale de la
  machine — cohérent avec le principe de patch minimal.
- **Fichiers modifiés** : `set-up.txt` (les 3 blocs `Register-ScheduledTask`
  de la section CHECKLIST, + note explicative ajoutée juste avant). Les
  scripts `.ps1` eux-mêmes n'ont pas été modifiés (le défaut est dans la
  commande d'enregistrement de la tâche, pas dans leur contenu).
- **Validé en conditions réelles (28/07/2026)** : `Start-ScheduledTask` →
  `LastTaskResult = 0`, log `wake_scheduler_task.log` désormais généré,
  `nssm start surveybot_topsurveys_bot_001` déclenché, service repassé à
  `SERVICE_RUNNING`.
- **Point de vigilance** : toute machine déjà provisionnée AVANT ce correctif
  a ses tâches planifiées enregistrées avec l'ancienne action (sans
  `-ExecutionPolicy Bypass`) — `sync_orchestration_scripts.ps1` ne
  re-enregistre pas les tâches planifiées elles-mêmes (il ne fait que
  télécharger les fichiers `.ps1`), donc ce correctif doit être **réappliqué
  manuellement** (`Set-ScheduledTaskAction` avec la nouvelle commande) sur
  chaque machine du parc déjà en production, pas seulement sur les nouvelles.

  ---

## 15. Isolation de groupe de processus pour l'arrêt ciblé d'un bot manuel (`launch_all.ps1`, `stop_bot.ps1`) — corrigé

- **Problème observé (29/07/2026)** : aucun moyen fiable d'arrêter proprement UN SEUL
  bot lancé manuellement via `launch_all.ps1` (hors NSSM). `nssm stop` envoie en
  réalité un `CTRL_BREAK_EVENT` via `GenerateConsoleCtrlEvent` (cf. section 3) — capté
  par `install_sigint_handler`/`_make_stop_handler` (`launch.py`), qui déclenche la
  séquence de fermeture propre. Reproduire ce mécanisme pour un lancement manuel
  nécessite de cibler un `CTRL_BREAK_EVENT` sur un unique process.
- **Cause racine** : `launch_all.ps1::Start-Bot` créait le process bot via
  `[System.Diagnostics.Process]::Start($psi)`. `ProcessStartInfo` n'expose aucun moyen
  de définir le flag Win32 `CREATE_NEW_PROCESS_GROUP` — le bot héritait donc du groupe
  de processus de la console PowerShell qui l'a lancé. Un `CTRL_BREAK_EVENT` envoyé à
  ce groupe aurait atteint TOUS les process qui en sont membres : le lanceur
  PowerShell lui-même, et tout autre bot lancé manuellement depuis le même terminal
  resté ouvert (double lancement type "test isolé" fréquent en usage manuel, cf.
  section 9). Risque explicitement écarté : pas de mécanisme d'arrêt ciblé tant que
  cette isolation n'existait pas.
- **Correction appliquée** :
  - `launch_all.ps1::Start-Bot` : remplacement du point de création du process par un
    appel P/Invoke direct à `CreateProcess` (Win32), classe `SurveyBotIsolatedLauncher`
    ajoutée en tête de script (`Add-Type`), avec le flag `CREATE_NEW_PROCESS_GROUP`.
    Chaque bot devient ainsi la racine de son propre groupe de processus (id de groupe
    = son propre PID). Redirection stdout+stderr vers le fichier log via un handle
    `CreateFile` inheritable (remplace les pipes managés .NET + `Register-ObjectEvent`
    — plus simple, même résultat). Le format du fichier `pids\bot_<id>.pid`
    (`"PID|StartTicks"`) est strictement conservé : `StartTicks` recalculé côté
    `GetProcessTimes`/`DateTime.FromFileTime`, identique à ce que `Process.StartTime`
    aurait renvoyé — `Test-BotProcessAlive` (inchangée) reste compatible.
  - Nouveau fichier `stop_bot.ps1` (arrêt manuel ciblé, hors NSSM) : lit
    `pids\bot_<id>.pid`, revalide PID+StartTicks (même garde que
    `Test-BotProcessAlive`), puis envoie `CTRL_BREAK_EVENT` uniquement au groupe
    identifié par ce PID via `FreeConsole`/`AttachConsole`/`GenerateConsoleCtrlEvent`
    (classe `SurveyBotConsoleCtrl`). **Abandon sans envoi de signal** si le fichier PID
    est absent, illisible, ou si le PID ne correspond plus au process attendu (recyclé)
    — jamais de signal envoyé en cas de doute.
  - **Aucune modification** de `install_sigterm_handler`/`install_sigint_handler`/
    `_make_stop_handler`/`write_pid_file`/`delete_pid_file` (`launch.py`), ni de
    `nssm_setup_bot.ps1` — hors périmètre, chemin NSSM déjà validé en production
    (section 3/4). `Test-NssmServiceExists`, `Test-BotProcessAlive`, la boucle
    principale et la rotation de logs de `launch_all.ps1` restent inchangées.
- **Validé en conditions réelles (29/07/2026, machine de dev)** : bot factice
  (handler `signal.SIGBREAK` imitant `_make_stop_handler`) lancé via `launch_all.ps1`
  patché, puis arrêté via `stop_bot.ps1` — log confirme la réception du signal côté
  process cible et sa terminaison propre. Un second bot lancé depuis la **même**
  console PowerShell n'est pas affecté, ni le process PowerShell appelant lui-même.
  Les 3 chemins d'abandon (fichier absent / format inattendu / PID recyclé) validés :
  aucun signal envoyé, log clair dans chaque cas.
- **Point de vigilance découvert pendant la validation (non corrigé, hors périmètre de
  ce patch)** : sur une installation Python utilisant le "launcher" de venv introduit
  par certains installeurs récents (le `venv\Scripts\python.exe` est un stub qui
  relance le véritable interpréteur comme process enfant, avec un PID différent),
  `write_pid_file()` (`launch.py`) détecte que `os.getpid()` ne correspond pas au PID
  déjà écrit par `launch_all.ps1` et écrase le fichier avec un PID nu (sans
  `StartTicks`) — comportement **préexistant**, indépendant de ce patch (le PID observé
  par `[System.Diagnostics.Process]::Start` avant ce correctif aurait eu exactement le
  même effet). Conséquence : `stop_bot.ps1` abandonnerait alors systématiquement
  (format de fichier inattendu), sans jamais envoyer de signal erroné — dégradation
  sûre, mais `stop_bot.ps1` ne fonctionnerait pas sur une telle machine. Non vérifié
  si ce cas de figure existe sur le parc de production (dépend de la méthode
  d'installation Python utilisée par `setup_machine.ps1` au provisioning de chaque
  machine, pas du code du dépôt) — à vérifier si `stop_bot.ps1` semble sans effet en
  usage réel.
- **Fichiers modifiés/créés** : `launch_all.ps1` (modifié, isolation de process
  additive) ; `stop_bot.ps1` (nouveau).

  ---

## 16. Fuite d'attachement console dans `stop_bot.ps1` — corrigé

- **Problème observé (29/07/2026)** : la première version de `stop_bot.ps1` (section 15)
  appelait `FreeConsole`/`AttachConsole`/`GenerateConsoleCtrlEvent`/`FreeConsole`
  directement dans le process PowerShell qui exécute le script lui-même.
  `FreeConsole()` détache CE process appelant de sa propre console, et la séquence ne le
  réattachait jamais à son origine. En usage réel (`stop_bot.ps1` invoqué depuis la même
  session PowerShell interactive que celle utilisée pour `launch_all.ps1`, consulter des
  logs, etc.), cette session perdait définitivement son attachement console (plus de
  `Write-Host`/`Read-Host` fonctionnels) pour le reste de la fenêtre.
- **Correction appliquée** : la logique `FreeConsole`/`AttachConsole`/
  `GenerateConsoleCtrlEvent` (classe C# `SurveyBotConsoleCtrl`, inchangée) est
  désormais exécutée dans un **sous-process powershell.exe séparé et jetable**
  (fonction `Invoke-CtrlBreakInChildProcess`), créé pour la seule durée de cette
  opération puis détruit — seul ce sous-process perd son attachement console, jamais
  le process appelant. Budget borné (`$CTRL_BREAK_CHILD_TIMEOUT_MS = 15000` ms) : si le
  sous-process ne se termine pas dans ce délai, il est tué de force et l'échec est
  loggé clairement (jamais de succès supposé silencieusement).
- **Détail d'implémentation notable (validé empiriquement)** : le sous-process doit être
  créé via `[System.Diagnostics.Process]::Start()` direct (`UseShellExecute=$false`,
  `CreateNoWindow=$true`), **pas** via la cmdlet `Start-Process` avec
  `-WindowStyle Hidden` + redirection vers fichiers — cette dernière combinaison casse
  l'héritage de console du sous-process créé (`AttachConsole` échoue alors avec
  `ERROR_INVALID_PARAMETER`, constaté en test). `[System.Diagnostics.Process]::Start`
  direct (même mécanisme que `launch_all.ps1`) préserve cet héritage et fonctionne de
  façon fiable.
- **Aucune modification** de la logique de validation PID+StartTicks existante (lecture
  du fichier PID, les 3 chemins d'abandon sans envoi de signal), ni de
  `SurveyBotIsolatedLauncher`/`CREATE_NEW_PROCESS_GROUP` dans `launch_all.ps1`, ni de
  `launch.py`/`nssm_setup_bot.ps1` — seul le mécanisme d'émission du signal, une fois la
  cible déjà validée, est concerné.
- **Validé en conditions réelles (29/07/2026, machine de dev)** : bot factice relancé,
  arrêté via `stop_bot.ps1` corrigé — signal bien reçu et traité côté process cible
  (log `GOT SIGBREAK`). Après exécution de `stop_bot.ps1` dans la session appelante,
  `Write-Host` et un calcul trivial exécutés ensuite dans **cette même session**
  fonctionnent normalement (console non perdue). Isolation reconfirmée : un second bot
  sur la même console reste non affecté. Les 3 chemins d'abandon existants re-testés,
  inchangés.
- **Fichiers modifiés** : `stop_bot.ps1` uniquement.

  ---

## 17. Arrêt manuel opérateur relancé par `wake_scheduler.ps1` — corrigé, non validé en conditions réelles

- **Problème observé (08/08/2026)** : plusieurs `nssm stop surveybot_topsurveys_bot_003`
  répétés par l'opérateur ne maintenaient pas le bot arrêté durablement — quelques
  minutes plus tard, le heartbeat local montrait un bot de nouveau actif (âge 31 s).
- **Cause racine** : `launch.py::_make_stop_handler` (§3, `_make_stop_handler`) traite
  `nssm stop` (SIGBREAK) exactement comme n'importe quel arrêt volontaire automatique
  normal (fin de session, objectif journalier) : `cooldown_until_ts` remis à l'epoch,
  `status="idle"`, `record_exit(EXIT_VOLUNTARY)`. `wake_scheduler.ps1` (§8) ne fait que
  lire ce cooldown Postgres — un cooldown à l'epoch est toujours expiré, donc le bot est
  relancé (`nssm start`) au prochain passage (jusqu'à 5 min), qu'il ait été arrêté par un
  cooldown normal ou par une décision opérateur destinée à durer.
- **Piège identifié pendant le diagnostic (a écarté une première approche)** : corriger
  ce cas dans `_make_stop_handler` lui-même (ex. réutiliser le cooldown ~1 an de
  `PausePolicy.UNTIL_MANUAL`, déjà utilisé pour `StopReason.SESSION_EXPIRED`) est
  **impossible à faire correctement** : `nssm stop` envoie le même `CTRL_BREAK_EVENT`
  qu'un arrêt de service Windows ordinaire (redémarrage machine, Windows Update) —
  rien côté process Python ne permet de distinguer les deux déclencheurs. Un correctif
  au niveau du signal aurait donc mis en pause ~1 an *tout le parc* après un simple
  reboot machine, pas seulement le bot visé par l'opérateur.
- **Décision** : ne pas toucher au cooldown Postgres ni à `_make_stop_handler`/
  `install_sigint_handler` (inchangés). Introduire un marqueur fichier explicite,
  distinct du cooldown, posé uniquement par une action opérateur volontaire et lu
  uniquement par `wake_scheduler.ps1` :
  - **Nouveau script `stop_bot_manual.ps1`** (racine du dépôt, hors périmètre de
    `stop_bot.ps1` qui cible les bots lancés via `launch_all.ps1`) : pose
    `pids\bot_<id>.manual_stop` (contenu horodaté, diagnostic seulement) puis appelle
    lui-même `nssm stop surveybot_<id>`. À utiliser à la place d'un `nssm stop` nu pour
    un arrêt destiné à durer.
  - **`wake_scheduler.ps1`** : nouveau bloc de vérification additif, entre le check
    `EXIT_FATAL` existant et le check de statut NSSM — `continue` (compte ignoré) si
    `pids\bot_<id>.manual_stop` existe. Même style que le check `EXIT_FATAL` voisin.
  - **`bot_supervisor.py`** : nouvelle fonction additive `clear_manual_stop_marker()`
    (mêmes conventions que `_pids_dir()`/`_state_path()` existants) — supprime le
    marqueur s'il existe.
  - **`main.py`** : un seul nouvel appel à `clear_manual_stop_marker(ACCOUNT_ID)`, au
    tout début du démarrage réel du bot (juste avant `check_and_record_start`, dans le
    même bloc `if not is_attach_mode()`). Tout démarrage réel du process — `nssm start`
    explicite par l'opérateur, **ou** redémarrage machine qui relance le service NSSM —
    lève donc le marqueur. Conséquence assumée : un redémarrage machine reprend le bot
    normalement (comportement inchangé), exactement comme avant ce correctif ; seul
    `wake_scheduler.ps1` (relance périodique à distance) est désormais bloqué par le
    marqueur tant que le process n'a pas redémarré au moins une fois.
  - **`build_orchestration_release.ps1`** : `stop_bot_manual.ps1` ajouté à
    `$TrackedFiles` (sinon le script ne serait jamais synchronisé sur le parc via
    `sync_orchestration_scripts.ps1`).
- **Aucune modification** de `launch.py` (`_make_stop_handler`, `install_sigint_handler`),
  `nssm_setup_bot.ps1`, `stop_bot.ps1`, `check_zombie_bots.ps1` (déjà correct : il ignore
  `last_exit_code == 0`, donc un bot manuellement arrêté n'est de toute façon jamais
  considéré comme zombie).
- **Point de vigilance** : le marqueur ne bloque que `wake_scheduler.ps1`. Il ne modifie
  pas le comportement de démarrage automatique du service NSSM lui-même (ex. au boot de
  la machine, si le service est configuré en démarrage automatique) — ce point était
  hors périmètre de la demande initiale (qui ne visait que la relance périodique par
  `wake_scheduler.ps1`) et n'a pas été investigué plus avant.
- **Non validé en conditions réelles à ce jour** : vérifié par relecture de code +
  parsing syntaxique (PowerShell `PSParser`, Python `ast.parse`) uniquement. Cycle
  complet (`stop_bot_manual.ps1` → `wake_scheduler.ps1` ignore le compte pendant
  plusieurs passages → `nssm start` manuel lève le marqueur → `wake_scheduler.ps1`
  reprend la gestion normale) à valider sur une machine réelle avant déploiement au
  parc.
- **Fichiers modifiés/créés** : `bot_supervisor.py`, `main.py`, `wake_scheduler.ps1`,
  `build_orchestration_release.ps1` (modifiés) ; `stop_bot_manual.ps1` (nouveau).

  ---

## 18. Bascule NSSM → PID/`launch_all.ps1` en session interactive — décision actée

- **Problème résolu** : le parc était exploité exclusivement via des services NSSM
  exécutés en **Session 0** (isolée, sans compositeur DWM, aucun écran physique ni
  session RDP ne peut afficher une fenêtre Session 0). Deux conséquences : un
  navigateur bloqué est invisible et irrécupérable pour l'opérateur, et Chrome peut
  basculer sur un pipeline de rendu logiciel (SwiftShader) au lieu du vrai pipeline
  GPU en Session 0, ce qui peut désaligner les valeurs WebGL/Canvas déjà travaillées
  pour coller à un profil de référence.
- **Décision** : chaque bot tourne désormais en premier plan, dans une session
  Windows interactive normale (compositeur DWM actif, GPU réel), démarré par une
  tâche planifiée déclenchée au logon manuel de l'opérateur (pas d'auto-logon, pas de
  mot de passe stocké — la connexion Windows reste un geste manuel). Les trois
  briques de supervision gardent **exactement** leur logique de décision existante
  (cooldown Postgres §8, `EXIT_FATAL`/crash-loop §2, marqueur `manual_stop` §17,
  seuil heartbeat) — seul leur **mécanisme de vérification d'état et de démarrage**
  change de cible : NSSM (`nssm status`/`start`/`restart`) → process brut (PID)
  lancé par `launch_all.ps1`.
- **`launch_all.ps1`** : `-AccountId` redevient optionnel (vide = tous les comptes
  de `accounts.json`, usage tâche planifiée au logon ; renseigné = comportement
  manuel/ponctuel existant, inchangé). Garde-fou `MAX_ACCOUNTS = 200` ajouté (même
  convention que `wake_scheduler.ps1`). `Test-NssmServiceExists`,
  `Test-BotProcessAlive`, `SurveyBotIsolatedLauncher`
  (`CREATE_NEW_PROCESS_GROUP`), la détection PID recyclé et la rotation de logs ne
  sont **pas modifiés** — ils s'appliquaient déjà par bot dans la boucle existante,
  réutilisables tels quels pour "tous les comptes".
- **Fenêtrage déterministe** (nouveau) : chaque bot reçoit une position/taille de
  fenêtre Chrome déduite de son index dans `accounts.json` (grille par défaut : 4
  colonnes, cellules 480×320, origine 0,0 — paramétrable), pour que l'opérateur
  connecté en RDP repère immédiatement le bon compte. L'index est calculé sur la
  liste **complète** de `accounts.json`, jamais sur un sous-ensemble filtré par
  `-AccountId` : un lancement manuel mono-compte obtient la même position que ce
  compte aurait en mode "tous les comptes". Transmis via de nouvelles variables
  d'environnement `SURVEYBOT_WINDOW_X/Y/W/H`, consommées par
  `preselection/playwright_launcher.py::launch_browser_playwright()` (remplace
  `--start-maximized` par `--window-position=X,Y --window-size=W,H` si ces variables
  sont présentes ; sinon comportement inchangé — additif, un bot NSSM garde
  `--start-maximized`). `SURVEY_HEADLESS` était déjà à `"0"` (visible) dans
  `global_config.py` — aucun changement requis sur ce point.
- **`check_zombie_bots.ps1`** : cible désormais `pids\bot_<id>.pid`/`launch_all.ps1`
  au lieu de `nssm restart`. Deux chemins distincts, additifs l'un par rapport à
  l'autre :
  1. **Bot réellement arrêté** (nouveau — pas seulement zombie) : le process
     `pids\bot_<id>.pid` n'est plus vivant et `last_exit_code` n'est ni 0 ni 3 (déjà
     filtré plus haut, donc crash/soft_restart/inconnu) → relance directe via
     `launch_all.ps1 -AccountId <id>`. NSSM ne supervise plus ce chemin ; sans ce
     nouveau check, un bot mort après `EXIT_CRASH`/`EXIT_SOFT_RESTART` resterait
     arrêté indéfiniment (NSSM faisait ce travail via `AppExit Default Restart`).
  2. **Zombie** (heartbeat périmé, process vivant, chemin existant, seuil 300 s
     inchangé) : `stop_bot.ps1 -AccountId <id>` (best-effort, `CTRL_BREAK_EVENT`,
     même mécanisme que `nssm stop`) → attente bornée (35 s, cohérent avec l'ancien
     `AppStopMethodConsole` NSSM 30 s) → `Stop-Process -Force` en dernier recours si
     toujours vivant (jamais d'abandon silencieux, log explicite dans tous les cas)
     → `launch_all.ps1 -AccountId <id>`.
  Nouveau check additif avant ces deux chemins : marqueur `manual_stop` (même règle
  que `wake_scheduler.ps1` §17) — jamais relancé si présent. `launch_all.ps1` est
  invoqué via un **sous-process `powershell.exe` dédié** (`-File`, pas un
  dot-source/appel en processus), pour éviter tout risque de ré-exécution du bloc
  `Add-Type` de `launch_all.ps1` dans le même AppDomain si plusieurs bots sont
  relancés dans le même passage. `$ServicePrefix` supprimé (mort, plus aucun appel
  `nssm` dans ce script).
- **`wake_scheduler.ps1`** : seul le bloc final (statut NSSM + `nssm start`) est
  remplacé par une lecture `pids\bot_<id>.pid` (`Test-BotProcessAlive`, même
  logique dupliquée que `check_zombie_bots.ps1`) → déjà actif = ignoré, sinon
  `launch_all.ps1 -AccountId <id>` (même sous-process dédié). Toute la logique de
  décision en amont (cooldown Postgres §8, exclusion `EXIT_FATAL`, exclusion
  `manual_stop` §17) est **inchangée**. `$ServicePrefix` supprimé (mort).
- **Tâche planifiée de démarrage au logon (nouvelle)** : `SurveyBot_LaunchAllOnLogon`
  (voir `set-up.txt`, étape 3bis), trigger `-AtLogOn` sur le compte admin de
  l'opérateur, principal `-LogonType Interactive -RunLevel Limited` (pas d'opération
  admin dans `launch_all.ps1`, contrairement à `nssm_setup_bot.ps1` — évite toute
  friction UAC silencieuse au logon), action `launch_all.ps1` sans `-AccountId`
  (tous les comptes).
- **Retargeting de `SurveyBot_ZombieCheck`/`SurveyBot_WakeScheduler`** (décision
  explicitement tranchée avec l'opérateur, pas déductible de la seule demande
  initiale) : ces deux tâches tournaient en `SYSTEM`/`ServiceAccount` (Session 0).
  Les laisser en `SYSTEM` tout en leur faisant spawner un bot via `launch_all.ps1`
  aurait fait atterrir le bot relancé de nouveau en Session 0 — exactement le défaut
  que ce patch corrige, pour tout bot relancé (pas le lancement initial au logon).
  Décision actée : ces deux tâches basculent aussi sur le compte admin interactif
  (même principal que `SurveyBot_LaunchAllOnLogon`). Contrepartie assumée : si
  personne n'est connecté, ces deux tâches ne peuvent plus relancer de bot — jugé
  cohérent avec le principe "aucun bot ne doit tourner hors session interactive"
  dans ce nouveau modèle. `SurveyBot_OrchestrationSync` et `SurveyBot_LogRotation`
  ne spawnent aucun process bot : restent `SYSTEM`, non modifiées.
- **NSSM non supprimé automatiquement** : `nssm_setup_bot.ps1` n'est pas touché.
  Nouveau script séparé `decommission_nssm.ps1` (racine du dépôt) : dry-run par
  défaut (liste uniquement), `-Execute` (admin requis) pour `nssm stop` puis
  `nssm remove confirm` sur chaque service `surveybot_*`. Jamais appelé
  automatiquement par un autre script. Ajouté à `$TrackedFiles` dans
  `build_orchestration_release.ps1` (distribution du fichier seule — la
  synchronisation ne fait que copier le script, jamais l'exécuter). Décommissionnement
  réel laissé à une exécution manuelle explicite de l'opérateur, une fois la nouvelle
  orchestration validée en conditions réelles sur au moins une machine.
- **`stop_bot_manual.ps1` — point de vigilance non corrigé (hors périmètre de ce
  patch)** : ce script pose le marqueur `manual_stop` PUIS appelle `nssm stop`. Pour
  un bot lancé uniquement via `launch_all.ps1` (pas de service NSSM), ce second appel
  est un no-op silencieux (le marqueur est bien posé, mais le process n'est pas
  réellement arrêté) — l'opérateur doit compléter avec `stop_bot.ps1 -AccountId <id>`
  pour arrêter effectivement le process. Documenté dans `set-up.txt` (section
  "COMMANDES USUELLES"), non corrigé dans le code de `stop_bot_manual.ps1` lui-même
  (explicitement hors périmètre de ce patch).
- **Non validé en conditions réelles à ce jour** : vérifié par relecture de code +
  parsing syntaxique uniquement (pas de machine de prod/RDP accessible depuis cette
  session). Cycle complet à valider par l'opérateur avant déploiement au parc : tous
  les comptes lancés au logon avec fenêtres visibles et positionnées, zombie détecté
  et relancé (fenêtre repositionnée à l'identique), bot réellement arrêté (crash)
  détecté et relancé, non-régression sur `EXIT_VOLUNTARY`/`EXIT_FATAL`/`manual_stop`.
- **Aucune modification** du pipeline de résolution de sondage (`survey_handler.py`,
  `survey_solver.py`, `dom_analyzer.py`, `action_dispatcher.py`, extracteurs par
  plateforme) — strictement hors périmètre de ce patch.
- **Fichiers modifiés/créés** : `launch_all.ps1`, `check_zombie_bots.ps1`,
  `wake_scheduler.ps1`, `preselection/playwright_launcher.py`, `set-up.txt`,
  `build_orchestration_release.ps1`, `Utils/ORCHESTRATION_TRACKING.md` (modifiés) ;
  `decommission_nssm.ps1` (nouveau). Non touchés : `nssm_setup_bot.ps1`,
  `bot_supervisor.py`, `stop_bot.ps1`, `stop_bot_manual.ps1`,
  `sync_orchestration_scripts.ps1`, `rotate_orchestration_logs.ps1`.

  ---

## 19. Retrait du fenêtrage déterministe par compte (`launch_all.ps1`, `playwright_launcher.py`) — corrigé

- **Problème observé** : le fenêtrage déterministe introduit en section 18
  (grille de position/taille Chrome déduite de l'index du compte dans
  `accounts.json`, variables `SURVEYBOT_WINDOW_X/Y/W/H`) réduisait la taille
  effective de la fenêtre Chrome pour chaque bot. Constat en présélection
  TopSurveys : une fenêtre réduite peut tomber sous le seuil responsive de
  certains sites (bascule en layout mobile/tablette, overlays en position
  fixe) et bloquer des clics pourtant valides en résolution desktop normale.
- **Décision** : abandon du fenêtrage déterministe par compte au profit de
  `--start-maximized` inconditionnel en non-headless.
  `preselection/playwright_launcher.py` ne lit plus
  `SURVEYBOT_WINDOW_X/Y/W/H` (retiré, commentaire en place expliquant la
  raison). En conséquence, ces 4 variables d'environnement sont devenues du
  code mort dans `launch_all.ps1::Start-Bot` — retirées, ainsi que tout ce qui
  n'existait que pour les alimenter : les paramètres `-WindowCols`/
  `-WindowWidth`/`-WindowHeight`/`-WindowOriginX`/`-WindowOriginY` du script,
  le paramètre `-WindowIndex` de `Start-Bot`, et le calcul d'index
  `$accountIndexById`/`$winIdx` (plus aucun consommateur après ce nettoyage).
  Le bloc de commentaire "FENETRAGE DETERMINISTE" en tête de fichier a été mis
  à jour pour documenter l'abandon, plutôt que retiré, afin qu'un futur
  lecteur qui chercherait ce mécanisme comprenne pourquoi il n'existe plus.
- **Conséquence pour l'opérateur** : l'identification du bon compte lors d'une
  session RDP ne repose plus sur la position/taille de la fenêtre Chrome. Elle
  repose désormais sur le dummy plug HDMI par machine (1 machine = 1 point
  d'observation stable), voir `Utils/DEPLOIEMENT_BAREMETAL_DECISIONS.md`.
- **Portée du patch — nettoyage uniquement** : aucune autre logique de
  `launch_all.ps1` touchée — isolation de groupe de processus
  (`CREATE_NEW_PROCESS_GROUP`/`SurveyBotIsolatedLauncher`), gestion
  PID/`StartTicks` (`Test-BotProcessAlive`), `Test-NssmServiceExists`,
  rotation de logs (`$LOG_HISTORY_CYCLES`), garde-fou `$MAX_ACCOUNTS`
  inchangés. Vérifié avant retrait : aucun appelant externe
  (`check_zombie_bots.ps1`, `wake_scheduler.ps1`,
  `build_orchestration_release.ps1`) ne référence les paramètres
  `-Window*` retirés — seul `launch_all.ps1` lui-même les utilisait.
- **Fichiers modifiés** : `launch_all.ps1`, `Utils/ORCHESTRATION_TRACKING.md`.
  `preselection/playwright_launcher.py` déjà modifié séparément (hors
  périmètre de ce patch).

  ---

*Dernière mise à jour de ce fichier : 17/08/2026 (section 19 : retrait du
fenêtrage déterministe par compte introduit en section 18 — un fenêtrage réduit
par bot pouvait tomber sous le seuil responsive de certains sites et bloquer des
clics valides en desktop, cas observé en présélection TopSurveys ;
`playwright_launcher.py` passe à `--start-maximized` inconditionnel, et
`launch_all.ps1::Start-Bot` perd le calcul `SURVEYBOT_WINDOW_X/Y/W/H` devenu
mort, ainsi que les paramètres `-Window*`/`-WindowIndex` et
`$accountIndexById`/`$winIdx` qui n'existaient que pour l'alimenter.
L'identification du bon compte en RDP repose désormais sur le dummy plug HDMI
par machine, pas sur la position/taille de fenêtre). Précédemment : 16/08/2026
(section 18 : bascule de
l'orchestration du parc, NSSM (services Session 0) → process PID lancés par
`launch_all.ps1` en session Windows interactive (compositeur DWM actif, GPU réel) —
résout l'invisibilité d'un navigateur bloqué en Session 0 et le risque de rendu
logiciel SwiftShader désalignant les valeurs WebGL/Canvas. `launch_all.ps1` redevient
le mécanisme de démarrage de parc (mode "tous les comptes" réintroduit, décision du
26/07/2026 explicitement révisée) avec fenêtrage Chrome déterministe par index de
compte (`SURVEYBOT_WINDOW_X/Y/W/H`, consommées par `playwright_launcher.py`).
`check_zombie_bots.ps1`/`wake_scheduler.ps1` ciblent désormais `pids\bot_<id>.pid` au
lieu de services NSSM (logique de décision — cooldown, `EXIT_FATAL`, `manual_stop`,
seuil heartbeat — inchangée) ; `check_zombie_bots.ps1` relance en plus les bots
réellement arrêtés (pas seulement zombie), chemin qu'NSSM couvrait auparavant.
Nouvelle tâche planifiée `SurveyBot_LaunchAllOnLogon` (logon de l'opérateur, pas
SYSTEM) ; `SurveyBot_ZombieCheck`/`SurveyBot_WakeScheduler` retargetées sur le même
principal interactif (décision tranchée explicitement avec l'opérateur, pas
déductible de la demande initiale — sinon un bot relancé par ces deux tâches
atterrirait de nouveau en Session 0). NSSM non supprimé automatiquement — nouveau
script séparé `decommission_nssm.ps1`, dry-run par défaut, exécution manuelle
explicite. Non validé en conditions réelles à ce jour). Précédemment : 08/08/2026
(section 17 : `nssm stop` répété ne maintenait pas un bot arrêté durablement —
`wake_scheduler.ps1` le relançait dès son cooldown Postgres expiré, indiscernable
d'un arrêt volontaire automatique normal. Correctif via marqueur fichier explicite
distinct du cooldown — `stop_bot_manual.ps1` nouveau, `wake_scheduler.ps1` ignore le
compte si le marqueur existe, levé automatiquement au prochain démarrage réel du bot
via `bot_supervisor.clear_manual_stop_marker()` appelée depuis `main.py`.
`_make_stop_handler` et le cooldown Postgres volontairement non touchés — `nssm stop`
envoie le même signal qu'un arrêt de service Windows ordinaire, un correctif au
niveau du signal aurait donc mis en pause tout le parc après un simple redémarrage
machine. Non validé en conditions réelles). Précédemment : 29/07/2026 (section 16 : correction d'une fuite
d'attachement console dans `stop_bot.ps1` — `FreeConsole`/`AttachConsole`/
`GenerateConsoleCtrlEvent` déplacés dans un sous-process jetable pour ne jamais affecter
la session PowerShell interactive appelante). Précédemment : 29/07/2026 (section 15 :
isolation de groupe de processus `CREATE_NEW_PROCESS_GROUP` pour `launch_all.ps1`,
nouveau script `stop_bot.ps1` pour l'arrêt ciblé d'un bot manuel via
`CTRL_BREAK_EVENT`, sans risque pour les autres process de la même console — chemin
NSSM non touché). Précédemment :
28/07/2026 (section 14 : les 3 tâches
planifiées SYSTEM — `SurveyBot_ZombieCheck`, `SurveyBot_WakeScheduler`,
`SurveyBot_OrchestrationSync` — étaient bloquées par la politique
d'exécution PowerShell, `Set-ExecutionPolicy -Scope CurrentUser` ne
s'appliquant pas au principal SYSTEM ; correctif : `-ExecutionPolicy Bypass`
ajouté dans l'action de chaque tâche, cf. `set-up.txt`. À réappliquer
manuellement sur les machines déjà provisionnées). Précédemment : 28/07/2026
(section 13 : correctif du sentinel `EXIT_CRASH` non écrasé à la sortie du
recyclage `MAX_MAIN_CYCLES` dans `main.py` — `record_exit(EXIT_VOLUNTARY,
"max_main_cycles_reached")` ajouté avant le `raise SystemExit`, pour éviter
un faux comptage de crash-loop sur des recyclages sains ; correctif partiel,
3 chemins similaires encore ouverts, voir section 11 point 3). Précédemment : 26/07/2026 (section 9 : rôle de
`launch_all.ps1` restreint à un lancement manuel/ponctuel, garde-fou NSSM
anti double-lancement — le parc de production n'est désormais exploité que
via NSSM, seul chemin couvert par `check_zombie_bots.ps1`/
`wake_scheduler.ps1`). Précédemment : 24/07/2026 (sections 1bis, 6, 8 :
corrigé l'affirmation obsolète « parc interne = binaire Nuitka, aucun Python »
— vérifié par lecture directe de `wake_scheduler.ps1`, `nssm_setup_bot.ps1`,
`launch_all.ps1`, `setup_machine.ps1`, `build_release_zip.ps1` et
`update_checker.py` : le parc interne tourne en Python interprété depuis un
`venv\` local sur du code source zippé depuis le pivot du 20/07/2026 ; le
binaire compilé Nuitka ne concerne plus que le transfert à des tiers, voir
`Utils/DEPLOIEMENT_BAREMETAL_DECISIONS.md` section 4). Précédemment :
23/07/2026 (section 9, statut de `launch_all.ps1`). À mettre à jour à chaque
décision ou correction touchant l'orchestration — pas seulement en fin de
chantier.*