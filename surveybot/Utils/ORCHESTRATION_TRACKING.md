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
- **Machine de prod (NiPoGi mini PC)** : **`C:\surveybot\`** — uniquement le
  binaire compilé `surveybot.exe` (Nuitka onefile) + fichiers d'exploitation
  (`accounts.json`, `receiver_config.json`, `pids\`, `logs\`, scripts `.ps1`).
  **Aucun Python ni venv installé sur cette machine** — c'est tout l'intérêt
  du build Nuitka.
- Cette distinction n'est pas anecdotique : c'est la cause racine du bug
  documenté en section 6 (`query_cooldown_status.py` invoqué via un chemin
  Python de dev, qui n'existe pas en prod). Tout script d'orchestration
  (`.ps1`) ou toute logique de résolution de chemin doit cibler `C:\surveybot\`
  par défaut, jamais un chemin de dev codé en dur ou supposé.

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

- **Problème résolu** : `wake_scheduler.ps1` devait interroger le cooldown
  Postgres par compte. Un script Python autonome (`query_cooldown_status.py`)
  avait été créé, invoqué via un interpréteur Python + venv — **inexistants
  sur la machine de production bare-metal** (Nuitka onefile = aucune
  dépendance Python requise sur la machine cible). Le script échouait
  silencieusement en prod.
- **Décision** : exposer ce besoin directement dans le binaire compilé via un
  argument CLI (`surveybot.exe --query-cooldown <id1> <id2> ...`), intercepté
  en tout début de `main.py`, avant `load_config()`, `check_license_or_exit()`
  et tout import lourd. Sortie JSON sur stdout, `sys.exit(0)` systématique.
  Réutilise `State.account_state.load_state()` sans dupliquer la logique
  Postgres.
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
  `surveybot.exe --query-cooldown` (une seule connexion Postgres pour tous
  les comptes) → ignore les comptes en `EXIT_FATAL` (fichier `.state` local,
  `last_exit_code == 3`) → ignore les services déjà `SERVICE_RUNNING` →
  `nssm start` pour le reste.
- Garde-fou boucle `MAX_ACCOUNTS = 200`.

---

## 9. `launch_all.ps1` — statut

- **Confirmé non actif** sur les machines de production (pas de tâche
  planifiée existante) au 13/07/2026.
- **Supprimé le 13/07/2026**, sur la base du constat ci-dessus : NSSM +
  `wake_scheduler.ps1` + `check_zombie_bots.ps1` couvraient alors
  l'intégralité de son rôle.
- **Réintroduit le 20/07/2026** et activement maintenu depuis (dernier
  correctif : détection des PID recyclés par comparaison de l'heure de
  démarrage du process, pour éviter qu'un `bot_<id>.pid` obsolète fasse
  ignorer à tort un compte comme "déjà actif"). **Le fichier existe donc à
  nouveau dans le projet et doit être traité comme faisant partie de
  l'orchestration active**, pas comme un résidu — toute correction future
  touchant au démarrage/à la détection PID doit en tenir compte au même titre
  que NSSM/`wake_scheduler.ps1`/`check_zombie_bots.ps1`.
- Statut d'exécution effective en production (tâche planifiée l'invoquant
  réellement) non vérifiable depuis ce dépôt, au même titre que
  `wake_scheduler.ps1`/`check_zombie_bots.ps1` (cf. leurs en-têtes respectifs :
  seule la commande `Register-ScheduledTask` à exécuter manuellement y est
  documentée, aucune tâche planifiée n'est versionnée).
- Vérification `profile_dir` (que `launch_all.ps1` fait avant de lancer un
  bot) est également portée dans `nssm_setup_bot.ps1` : un compte dont
  `profile_dir`/`CHROME_PROFILE_DIR` est vide ou pointe vers un dossier
  inexistant est skippé (`Write-Warning`, service NSSM non configuré), sans
  être signalé comme "orphelin" (son `account_id` est ajouté au set de
  comptes connus avant le check, pas après).
- **Point de vigilance non résolu** : `launch_all.ps1::Start-Bot` définit ses
  propres défauts `GEO_LAT`/`GEO_LON`/`SURVEY_LANG`/`SURVEY_TZ`, séparément de
  ceux de `nssm_setup_bot.ps1` (cf. section 7) — les deux scripts existant à
  nouveau simultanément, ces valeurs sont désormais dupliquées à deux
  endroits, exactement le risque de divergence que la suppression du
  13/07/2026 avait éliminé. Non corrigé à ce jour.

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

*Dernière mise à jour de ce fichier : 23/07/2026 (section 9 : statut de
`launch_all.ps1` corrigé — réintroduit le 20/07/2026 après sa suppression du
13/07/2026, ne plus le documenter comme supprimé). À mettre à jour à chaque
décision ou correction touchant l'orchestration — pas seulement en fin de
chantier.*