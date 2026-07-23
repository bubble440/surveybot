# Suivi des décisions — Déploiement bare-metal & protection du code

> Document de suivi d'implémentation. Récapitule les décisions d'architecture prises,
> pas encore le détail des patchs (ceux-ci seront traités un par un via des prompts Claude Code dédiés).

Dernière mise à jour : à la suite de la discussion sur le passage bare-metal + protection du code.

---

## 1. Contexte du changement

Passage du déploiement Fly.io (machines éphémères, secrets injectés via `fly secrets`) vers un
déploiement bare-metal (mini-PC Windows, plusieurs bots par machine). Ce changement supprime le
mécanisme de secrets managé par la plateforme cloud — il faut le remplacer par un mécanisme
équivalent, propre au bare-metal.

En parallèle, remise à plat de la protection du code contre la rétro-ingénierie par un récepteur
qui possède physiquement une machine (ou reçoit le binaire).

---

## 2. Catégorisation des variables — décision actée et corrigée

**Correction actée** : la version précédente de cette section rangeait `OPENAI_API_KEY`,
`TWO_CAPTCHA_KEY`/`CAPSOLVER_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` dans
`GLOBAL_CONFIG` (figées à la compilation, non modifiables par le récepteur). Ce classement
était une erreur : ce sont des ressources qui **appartiennent au récepteur** (son propre compte
OpenAI, son propre bot Telegram, sa propre clé 2Captcha) — lui imposer les ressources de
l'opérateur (toi) poserait un problème de coût et de responsabilité (qui paie le quota OpenAI de
tout le parc, qui répond si une clé est bannie). Le récepteur doit pouvoir les renseigner et les
changer lui-même, sans recompilation. Ces quatre clés sortent donc de `GLOBAL_CONFIG` (section
mise à jour ci-dessous) et forment une troisième catégorie, distincte des deux existantes :

Trois catégories strictement séparées, ne jamais les mélanger dans un même fichier :

### PAR_BOT (propre à chaque instance, modifiable par le récepteur)
Transmises via `accounts.json` → injectées dans l'environnement du process enfant par
`launch_all.ps1` (1 bloc `$env_vars` par bot). Isolation déjà garantie par le mécanisme standard
de process Windows : aucune fuite possible entre bots sur une même machine.

Liste actuelle : `ACCOUNT_ID`, `EMAIL`, `PASSWORD`, `PROXY_URL`, `PROXY_USER`, `PROXY_PASS`,
`profile_dir` / `CHROME_PROFILE_DIR`.

**Correction actée** : `payout_name` et `payout_revolut_tag` sortent de cette catégorie.
Confirmé par l'utilisateur : ces deux clés sont identiques sur toutes les machines et tous les
bots d'un même récepteur (un récepteur n'a qu'une identité de paiement), donc `PAR_RECEPTEUR`,
pas `PAR_BOT`.

**Décision** : `accounts.json` ne doit contenir QUE les clés listées ci-dessus — identité et
accès propres à un bot individuel, qui diffèrent forcément d'un bot à l'autre. `OPENAI_API_KEY`,
`TWO_CAPTCHA_KEY`, `telegram_bot_token`, `telegram_chat_id`, `payout_name`, `payout_revolut_tag`
n'y ont pas leur place : ce ne sont pas des clés `GLOBAL_CONFIG` (voir correction plus haut),
mais elles ne varient pas non plus par bot — voir catégorie `PAR_RECEPTEUR` ci-dessous. Les
dupliquer dans chaque entrée d'`accounts.json` (état initial du fichier) fonctionnait mais
créait un risque de désynchronisation (clé modifiée sur un bot, oubliée sur un autre).

### PAR_RECEPTEUR (nouvelle catégorie — appartient au récepteur, partagé entre tous ses bots et toutes ses machines, éditable par lui)
Ressources propres au récepteur (comptes/services qu'il paie ou gère lui-même, ou son identité
de paiement), mais **constantes entre tous les bots qu'il fait tourner, sur toutes ses
machines** — une seule valeur logique par récepteur, pas une par bot ni une par machine. Ni
figées à la compilation (le récepteur doit pouvoir les changer quand il veut), ni dupliquées
par bot (source unique, pas de désynchronisation possible).

Liste : `OPENAI_API_KEY`, `TWO_CAPTCHA_KEY` (ou `CAPSOLVER_API_KEY` selon le fournisseur
retenu), `telegram_bot_token`, `telegram_chat_id`, `payout_name`, `payout_revolut_tag`.

**Décision de structure (actée, à implémenter)** : nouveau fichier séparé, non versionné, non
compilé — ex. `receiver_config.json` à la racine du dossier d'installation, à côté
d'`accounts.json`. Lu une fois au démarrage par chaque bot de la machine (les valeurs sont
partagées, pas besoin de les relire par bot). Édité directement par le récepteur avec un éditeur
de texte — pas besoin d'un mécanisme d'import dédié comme pour `accounts.json`, puisqu'il n'y a
qu'une seule instance de ce fichier par machine, pas une liste à valider bot par bot.

**Point de vigilance propre à cette portée multi-machines** : comme il n'existe aucun mécanisme
de synchronisation réseau entre les machines d'un même récepteur (chaque `receiver_config.json`
est local à sa machine), c'est au récepteur de recopier manuellement le même fichier sur chacune
de ses machines. Aucune garantie logicielle qu'il reste identique partout — à documenter côté
utilisateur final (ex. dans les instructions d'installation), pas à résoudre par du code tant
que ça reste 1 récepteur = quelques machines gérées à la main.

**Statut : implémenté.**
- `preselection/secret_loader.py` : ajout de `_from_receiver_config_file()`, qui lit
  `receiver_config.json` (racine du dossier bot, à côté d'`accounts.json`) et le fusionne dans
  `load_remote_secrets()` avec la priorité la plus basse de la pile (écrasable par
  `TOPSURVEYS_SECRET_JSON`, puis par les ENV directs, puis par les overrides ENV nommés — ordre
  inchangé, `receiver_config.json` vient juste combler le point de départ). Absence de fichier
  = dict vide, silencieux, cas normal en dev/attach.
- `receiver_config.example.json` : gabarit fourni au récepteur, avec les 6 clés `PAR_RECEPTEUR`.
- `import_accounts.py` (voir section 7) : reconnaît désormais `payout_name`/`payout_revolut_tag`
  comme clés `PAR_RECEPTEUR` (et non `PAR_BOT`) pour produire un message de rejet correct si
  elles apparaissent par erreur dans un import `accounts.json`.

**Bug de casse détecté dans l'implémentation livrée — corrigé.**
`_from_direct_env_keys()` lisait `PAYOUT_NAME`, `PAYOUT_REVOLUT_TAG`, `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID` (majuscules) alors que `_from_receiver_config_file()` et
`_from_env_overrides()` produisaient ces mêmes valeurs en minuscules
(`payout_name`, `payout_revolut_tag`, `telegram_bot_token`, `telegram_chat_id`) — la casse
différente cassait la logique de priorité pour ces 4 clés. Corrigé : `_from_direct_env_keys()`
remappe désormais explicitement le nom ENV (majuscules) vers la clé logique en minuscules avant
insertion dans le résultat, avec commentaire expliquant pourquoi une casse unique est requise.
`OPENAI_API_KEY` confirmé non concerné (casse déjà uniforme partout).



### GLOBAL_CONFIG / GLOBAL_SECRET (constant entre toutes les instances, NE DOIT PAS être modifiable par le récepteur)
Décision actée : ces variables seront compilées en dur dans le binaire (module Python
`global_config.py`, compilé nativement par Nuitka — voir section 4), et non plus lues depuis un
fichier JSON externe éditable.

Liste retenue (issue du tri effectué dans la conversation, hors `LICENSE_KEY`/`DATABASE_URL`/
`BOT_VERSION` qui restent dans `_license_config.py` — voir section 3 ; hors `OPENAI_API_KEY`,
`TWO_CAPTCHA_KEY`/`CAPSOLVER_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` qui relèvent
désormais de `PAR_RECEPTEUR` ci-dessus, pas de cette catégorie) :

- Config : `RUN_ENV`, `PLATFORM`, `STATE_BACKEND`, `STATE_TABLE`, `STATE_TTL_DAYS`,
  `SURVEY_BROWSER_BIN`, `SURVEY_HEADLESS`, `SURVEY_VISION_MODEL`,
  `CTA_INTERCEPT_ONLY` (confirmé = 0 en prod), `MAX_MAIN_CYCLES`,
  `ACCOUNT_LOCK_TTL_SEC`, `HEARTBEAT_INTERVAL_SEC`, `HEARTBEAT_JITTER_SEC`,
  `DOM_FRAME_MAX_DEPTH`, `AA_MATRIX_MAX_ROWS`, `AA_SELECTION_LIST_MAX`, `MAX_ACTIONS_PER_PLAN`,
  `SNAP_ENABLED` (= 0), `UPDATE_CHECK_ENABLED`, `UPDATE_MANIFEST_URL`.

**Retrait décidé lors de l'implémentation** : `LOG_LEVEL`, `LOG_STEP_SUMMARY` et
`CAPTCHA_PROVIDER` sortent finalement de la liste `GLOBAL_CONFIG`.
- `LOG_LEVEL`/`LOG_STEP_SUMMARY` ne sont pas des clés de sécurité (rien à protéger contre
  un tiers) — les figer dans le binaire compilé aurait ajouté de la rigidité opérationnelle
  (impossible de debug un bot en prod sans recompiler) sans gagner en prédictibilité.
  Restent en `os.getenv` classique, pilotables par bot via `accounts.json` si besoin.
- `CAPTCHA_PROVIDER` : aucun consommateur direct trouvé dans le code au moment du patch.
  Exclu de la migration ; à investiguer séparément (variable probablement morte, ou lue
  ailleurs) avant de décider de sa catégorie définitive.

**Exclu explicitement de cette liste** (variables `DEBUG_LOCAL`/attach uniquement, jamais lues
en prod, à ne jamais mettre dans le fichier/module global) : `LOCAL_CTA_REQUIRE_ENTER`,
`LOCAL_CTA_DEBUG`, `DIAG_ISTRUSTED`, `DIAG_CHECKBOX_CLICK`, `DIAG_STABILITY`,
`DOM_DEBUG_FRAMES`, `ACTION_DEBUG_TARGET`, `SURVEY_CTX_DEBUG`, `DOM_CONTEXT_DEBUG`,
`FIVESIM_ORDER_ID`, `YSENSE_EMAIL`, `YSENSE_PASSWORD`, `HTTP_PROXY`/`HTTPS_PROXY`,
`FAILURE_PIPELINE_TRIGGER_FILE`, `LOCAL_USE_PROXY`, et les variables spécifiques
Docker/Fly/Xvfb sans équivalent bare-metal (`PYTHONUNBUFFERED`, `PYTHONDONTWRITEBYTECODE`,
`DEBIAN_FRONTEND`, `DISPLAY`).

**`GIT_TOKEN`** : confirmé non utilisé — l'auto-update se fait via Cloudflare R2
(`manifest.json` + binaire), pas de `git pull` en prod. Ne pas l'ajouter nulle part.

---

## 3. `_license_config.py` — statut et rôle

Contient `LICENSE_KEY`, `DATABASE_URL`, `BOT_VERSION`. Reste séparé du reste de `GLOBAL_CONFIG`
pour une raison précise : la propriété recherchée n'est pas la confidentialité mais la
**résistance à la modification persistante** (une édition du récepteur ne doit avoir aucun effet
au redémarrage suivant).

**Statut : patché.** `_license_config.py` retiré de `datas` dans `surveybot.spec` ; il est
désormais importé comme un module Python classique (`hiddenimports=['_license_config']`),
compilé au même niveau de protection que le reste du code applicatif (Nuitka, section 4).

**Bug corrigé au passage** : `license_guard.py` importait via `from surveybot._license_config
import ...` (préfixe de package `surveybot` inexistant dans l'Analysis), alors que
`update_checker.py`/`account_state.py` importaient déjà correctement via `from _license_config
import ...`. Avec l'ancien `.spec`, cet import échouait silencieusement dans `license_guard.py`
(capturé par un `except ImportError` sans log), désactivant de fait `check_license_or_exit()`
sur tout le parc sans qu'aucune erreur ne soit visible. Corrigé : les deux imports de
`license_guard.py` alignés sur `from _license_config import ...`, sans préfixe.

**Point ouvert, non traité** : ajouter un `log.warning` explicite dans le `except ImportError`
de `_get_license_key()` pour qu'une régression future sur cet import soit visible dans les
logs plutôt que de retomber silencieusement dans un mode qui contourne la licence.

**Bug actif détecté (session Nuitka), non corrigé — tâche séparée requise.**
`preselection/license_guard.py` importe toujours `from surveybot._license_config import
LICENSE_KEY` (préfixe de package `surveybot` inexistant dans ce repo), alors que
`db_config.py` fait l'import plat correct (`from _license_config import ...`). C'est très
exactement le même bug que celui déjà corrigé une fois dans ce fichier (voir plus haut dans
cette section) — réapparu ou jamais propagé à ce module. Conséquence : `check_license_or_exit()`
échoue silencieusement à l'import (capturé par le `except ImportError` sans log réclamé par le
point ouvert ci-dessus) et traite la situation comme un mode dev sans licence, désactivant de
fait tout contrôle de quota sur le parc actuel. Indépendant du chantier Nuitka — à traiter comme
patch dédié, pas dans le cadre du packaging.

**Décision initiale (dépassée) sur `DATABASE_URL`** : un remplacement par un endpoint HTTP côté
serveur avait été envisagé (le client n'enverrait que `LICENSE_KEY`, recevrait un simple
booléen/statut, sans jamais voir la chaîne de connexion complète).

**Décision finale actée et implémentée** : l'endpoint a été écarté. Il ne résolvait qu'une
moitié du problème (la lecture du quota), alors que `payout.py` **écrit** aussi dans
`licenses` (incrément de `total_payout_eur` après chaque retrait confirmé) — et `DATABASE_URL`
doit de toute façon rester embarquée pour `account_state.py`/`survey_memory.py` (lock
row-level, heartbeat, mémoire inter-bots : pas transposable en HTTP sans réarchitecture
disproportionnée). Ajouter un endpoint n'aurait donc rien fermé : un récepteur ayant
décompilé le binaire récupère `DATABASE_URL` de toute façon, endpoint ou pas, et peut agir
directement sur `licenses` par un simple client Postgres — pour un coût d'infrastructure
supplémentaire (service à héberger, monitorer, sécuriser) et un gain de sécurité nul.

**Solution retenue, plus simple et effective** : restreindre les permissions Postgres
elles-mêmes plutôt que d'ajouter une couche applicative. Un rôle dédié `surveybot_client`
(celui utilisé par `DATABASE_URL` embarquée) a été créé côté serveur, avec :
- accès `SELECT/INSERT/UPDATE` normal sur `account_state` et `survey_memory` (inchangé) ;
- **aucun accès direct** (`REVOKE ALL`, y compris pour `PUBLIC`) sur la table `licenses` ;
- deux fonctions SQL `SECURITY DEFINER`, seules portes d'entrée possibles sur `licenses` :
  `check_license(license_key)` (lecture : `is_active`, `total_payout_eur`, `max_payout_eur`)
  et `increment_license_payout(license_key, amount_eur)` (écriture bornée : montant strictement
  compris entre 0 et 10 exclus/inclus, rejette toute valeur hors bornes).

Avec ce rôle, même un binaire totalement décompilé qui révèle `DATABASE_URL` ne permet à un
récepteur ni de lire, ni de modifier `licenses` directement — seulement d'appeler ces deux
fonctions dans les limites qu'elles imposent (impossible de mettre `is_active = true` ou
d'effacer son propre quota). L'objectif de la décision initiale est donc atteint sans ajouter
aucun service, ni latence réseau supplémentaire au démarrage du bot.

**Statut : implémenté côté serveur (SQL exécuté sur la base de prod `surveybot-db`, via
`flyctl proxy` + Adminer) et côté client** (`license_guard.py` appelle désormais
`check_license()`, `payout.py` appelle `increment_license_payout()`, plus aucune requête
directe sur `licenses` dans le code applicatif).

**Point ouvert, non encore fait** : le rôle `surveybot_client` n'est pas encore utilisé en
pratique — `DATABASE_URL` dans `_license_config.py` doit être mise à jour avec ses identifiants
avant le prochain build, et testée (connexion + `check_license`/`increment_license_payout` en
transaction annulée) avant bascule définitive. Voir aussi section 8 (accès réseau) : ce nouveau
`DATABASE_URL` ne peut être finalisé qu'après la décision sur l'exposition réseau de la base.

**`LICENSE_KEY` reste embarquée**, propre à chaque récepteur. Sa compromission éventuelle
(après décompilation réussie) reste contenue à ce récepteur — révocable unitairement via
`UPDATE licenses SET is_active = false WHERE license_key = ...`, sans impact sur le reste du
parc. Pas besoin de builds uniques par machine pour obtenir cette propriété : elle existe déjà
au niveau de la table Postgres `licenses`.

**Statut : centralisation de la résolution de `DATABASE_URL` — patché.** `license_guard.py`
appliquait déjà la bonne priorité (`_license_config.py` en premier, `os.getenv` en fallback
dev/attach uniquement), mais `State/account_state.py` et `State/survey_memory.py` lisaient
`DATABASE_URL` uniquement via `os.getenv`, sans passer par `_license_config.py` — alors qu'ils
se connectent à la même base centrale. Corrigé en créant `db_config.py` (module dédié, racine
du projet), qui expose une fonction unique `get_database_url()` appliquant la même priorité.
Les trois consommateurs (`license_guard.py`, `State/account_state.py`,
`State/survey_memory.py`) appellent désormais tous cette même fonction — plus de logique
dupliquée à trois endroits.

---

## 4. Protection contre la décompilation — décision actée : migration vers Nuitka

**Constat** : PyInstaller n'empaquette que du bytecode Python — décompilable avec des outils
publics (`pyinstxtractor`, `uncompyle6`/`decompyle3`, `pycdc`). Aucune configuration PyInstaller
ne rend ça réellement difficile.

**Décision actée** : remplacer PyInstaller par **Nuitka** comme outil de build. Nuitka traduit
le code Python en C puis compile en code machine natif — il n'y a alors plus de bytecode Python
à extraire, seulement de l'assembleur natif comme un exécutable C++ classique. Aucune
réécriture du code applicatif requise, seulement un changement d'outil/pipeline de build.

**Important — aucune garantie absolue.** Il a été explicitement clarifié qu'aucune protection ne
rend un exécutable tournant sur une machine contrôlée par le récepteur "impossible" à rétro-
ingénierier. L'objectif réaliste est d'élever significativement la barre technique nécessaire,
pas de l'annuler. Une option de durcissement supplémentaire (packer/protecteur natif type
VMProtect/Themida) reste envisageable plus tard, mais n'est pas retenue dans l'immédiat.

**Étape de validation avant généralisation (non encore faite au moment de la rédaction ; plan
depuis dépassé pour le parc interne, voir correction 24/07/2026 ci-dessous)** : tester le cycle
complet `build Nuitka → upload R2 → auto-update → relance` sur **une seule machine** avant de
déployer aux 14 PC. Point d'incertitude à vérifier spécifiquement : le comportement de
`sys.executable` / l'auto-remplacement de l'exe (`_replace_exe_and_restart` dans
`update_checker.py`) avec un binaire `--onefile` Nuitka, qui gère l'extraction de ses
dépendances différemment de PyInstaller en interne.

**Correction (24/07/2026)** : ce paragraphe supposait que les 14 PC du parc interne
tourneraient sur ce binaire Nuitka. Depuis le pivot du 20/07/2026, le parc interne utilise un
pipeline distinct (code source zippé + venv Python local, voir
`Utils/ORCHESTRATION_TRACKING.md` section 1bis) — ce plan de validation ne concerne donc plus
que le pipeline de transfert à des tiers/récepteurs, s'il est encore utilisé. Par ailleurs, la
fonction `_replace_exe_and_restart` citée ci-dessus n'existe plus dans `update_checker.py` : le
fichier a été réécrit (pivot du 20/07/2026) en `_replace_source_and_restart`, qui remplace un
dossier `code\` plutôt qu'un exécutable — voir le point ouvert correspondant plus bas dans
cette section.

**Statut : patché côté configuration de build, test réel restant.** `surveybot.spec`
(PyInstaller, non versionné) supprimé. Créé `nuitka_build.ps1` — commande de build
`python -m nuitka main.py --onefile --output-dir=dist_nuitka --output-filename=surveybot.exe
--windows-console-mode=force --follow-imports --assume-yes-for-downloads
--include-module=_license_config --include-module=global_config --include-package=Survey
--include-package=Management --include-package=preselection --include-package=captcha
--include-package-data=playwright --include-package-data=botocore --include-package-data=boto3`.
Prérequis machine de build : `pip install nuitka ordered-set zstandard` + compilateur C — soit
Visual Studio Build Tools (détecté automatiquement par Nuitka), soit `-UseMinGW` sur le script
(Nuitka télécharge son propre MinGW64 portable, aucune install manuelle).

**Inclusions manuelles vérifiées (équivalent `hiddenimports`), pas supposées** :
- `_license_config` / `global_config` : suivi automatique déjà probable (imports directs),
  déclarés explicitement en défense en profondeur — une régression silencieuse sur ce point
  ferait retomber licence/config sur les fallbacks `os.getenv` sans avertissement.
- `hot_reload/hot_reload.py` : `importlib.import_module()` sur ~35 modules par nom de chaîne
  (`Survey.*`, `preselection.*`, `Management.*`, `captcha.*`) — non suivi par l'analyse statique
  de Nuitka. Couvert par `--include-package` sur les 4 packages concernés.
- `playwright` : dossier `driver/` (binaire Node.js + JS, non-Python) requis à l'exécution,
  jamais suivi par défaut → `--include-package-data=playwright` obligatoire.
- `boto3`/`botocore` : modèles de service internes en JSON, chargés dynamiquement → package-data
  requis, sinon `NoRegionError`/`UnknownServiceError` au runtime. Alourdit le binaire d'environ
  70 Mo — à re-questionner si le snapshot S3 (`SNAP_ENABLED`) doit rester dans le binaire prod ou
  passer dans un outil séparé (hors scope de ce patch).
- `psycopg2-binary` : extension C + DLLs OpenSSL vendues, suivi automatique attendu en mode
  standalone/onefile mais à confirmer explicitement au premier build réel (connexion Postgres).
- `tzdata` : base de données IANA des fuseaux horaires (fichiers, pas du code Python), consultée
  par le module stdlib `zoneinfo`. Windows ne fournit pas cette base nativement (contrairement à
  Linux/macOS, où `zoneinfo` retombe sur le système) — sans `tzdata`, `ZoneInfo("Europe/Paris")`
  lève `ZoneInfoNotFoundError`. Même cas que `playwright`/`boto3`/`botocore` ci-dessus : données
  pures, non suivies par `--follow-imports` → `--include-package-data=tzdata` ajouté. Utilisé par
  `Management/pause_policy.py::resolve_pause_seconds()` (`PausePolicy.DAILY_RESET`, déclenché par
  `StopReason.DAILY_TARGET_REACHED` et `StopReason.PROXY_EXPIRED`, donc par
  `Management/guards/runtime_guard.py::pause()`). **Détecté par diagnostic (24/07/2026), pas
  encore validé par un build réel** — à couvrir par la même étape de validation que le reste de
  cette section (voir "Étape de validation avant généralisation" en tête de section 4). Avant ce
  patch, `tzdata` était absent à la fois de `requirements.txt` et du venv de build : le paquet
  aurait été manquant dès l'étape `pip install`, avant même la question de son embarquement par
  Nuitka.
- `selenium`/`undetected-chromedriver` : plus aucun `import selenium` dans le code (migration
  Playwright déjà faite) — rien à déclarer, ne seront pas embarqués malgré leur présence dans
  `requirements.txt`.

**Point critique non résolu, à traiter en premier lors du test réel** : le comportement de
`sys.executable` dans un process onefile Nuitka n'est pas garanti identique à PyInstaller — il
peut pointer vers le binaire extrait en dossier temporaire plutôt que vers l'exe distribué, ce
qui casserait silencieusement `_replace_exe_and_restart` (renommage d'un fichier temporaire
jetable, mise à jour perdue au redémarrage suivant). Nuitka expose pour ce cas la variable
d'environnement `NUITKA_ONEFILE_BINARY` (chemin absolu de l'exe onefile réel). **Ne rien modifier
dans `update_checker.py` avant d'avoir vérifié** — c'est la toute première étape du plan de test,
avant même le cycle auto-update complet. Si le diagnostic montre un écart, seul changement
autorisé : `current_exe = os.environ.get("NUITKA_ONEFILE_BINARY") or sys.executable` (fallback
non intrusif, no-op en dev/attach où la variable est absente).

**Correction (24/07/2026) — ce point ne concerne plus le parc interne, mais soulève une
question non résolue pour le pipeline tiers.** Le parc interne n'exécute plus de binaire
Nuitka onefile (voir correction plus haut dans cette section) : ce risque `sys.executable`/
`NUITKA_ONEFILE_BINARY` ne s'applique donc plus à lui. Mais `update_checker.py` a depuis été
réécrit (pivot du 20/07/2026) pour un mécanisme différent : `_replace_source_and_restart`
renomme un dossier `code\` puis relance via
`os.execv(sys.executable, [sys.executable, main_py, ...])` — logique pensée pour une exécution
source (un vrai `python.exe` de venv, un vrai dossier `code\` à côté). Or
`nuitka_build_release.ps1` compile toujours `main.py` (et donc `update_checker.py` tel qu'il
existe aujourd'hui) pour le pipeline tiers/récepteurs. **Point non vérifié, à tester avant tout
envoi d'un binaire à un récepteur** : dans un process onefile Nuitka, il n'existe pas de dossier
`code\` distinct à renommer, et `sys.executable` peut ne pas pointer vers un interpréteur
Python exploitable (voir paragraphe ci-dessus) — `_replace_source_and_restart` pourrait donc
échouer silencieusement ou se comporter de façon imprévisible si un récepteur reçoit un jour un
binaire compilé avec cette version du fichier.

**Statut : corrigé (24/07/2026), non testé en conditions réelles (pas de cycle complet
build+update exécuté).** `update_checker.py::check_and_apply()` détecte désormais le contexte
onefile via `NUITKA_ONEFILE_BINARY` (même variable que `secret_loader.py::_bot_root_dirs()`) et
branche vers `_replace_exe_and_restart()` — remplacement de l'exe réel (renommer + copier +
`os.execv` sur le chemin trouvé via `NUITKA_ONEFILE_BINARY`, jamais `sys.executable`), adapté de
l'implémentation pré-pivot de ce fichier (git history, avant le 20/07/2026), qui utilisait déjà
cette stratégie mais avec `sys.executable` — donc sujette au même piège. Le chemin parc interne
(`_replace_source_and_restart`, `onefile` faux) est inchangé : vérifié par lecture de code et
test de la fonction de détection, pas par un cycle d'update réel de bout en bout.

**Bug hors-scope détecté au passage, non corrigé ici (tâche séparée)** :
`preselection/license_guard.py` importe `from surveybot._license_config import LICENSE_KEY` —
package `surveybot` inexistant dans ce repo, contrairement à `db_config.py` qui fait l'import
plat correct. Conséquence : le contrôle de licence/quota est actuellement mort silencieusement
sur tous les builds existants, y compris en prod (capturé par un `except ImportError` sans log,
même défaut que celui déjà documenté en section 3). Indépendant du packaging — à traiter comme
patch dédié.

**Rejeté explicitement** : build unique par machine ("app non-décompilable car unique") —
casse le mécanisme d'auto-update actuel (un seul `manifest.json`/SHA256 pour tout le parc) pour
un gain de protection nul (l'unicité ne rend pas un binaire donné plus difficile à décompiler,
elle limite seulement l'impact d'une compromission — propriété déjà obtenue autrement, voir
section 3).

**Rejeté explicitement** : piloter les navigateurs à distance depuis un serveur central (les
machines ne téléchargeraient jamais le projet, juste un agent minimal piloté à distance) —
réintroduirait un pilotage réseau du navigateur, exactement le pattern qui déclenchait DataDome
via `--remote-debugging-port` avant la migration Playwright native. Ne résout de plus pas le
problème (l'agent minimal resterait lui-même décompilable), pour un coût de réarchitecture very
disproportionné (orchestrateur central multi-sessions Chrome).

---

## 5. Mise à jour automatique du parc — description historique, dépassée pour le parc interne

**Correction (24/07/2026)** : cette section décrivait le mécanisme tel qu'il existait juste
après la migration Nuitka (06-08/07/2026), pour l'ensemble du parc à l'époque. Depuis le pivot
du 20/07/2026, le parc interne utilise un pipeline différent — voir
`Utils/ORCHESTRATION_TRACKING.md` section 1bis et `build_release_zip.ps1`. La description
ci-dessous (build d'un binaire, auto-remplacement de l'exe) ne s'applique donc plus qu'au
pipeline de transfert à des tiers/récepteurs, s'il est encore utilisé — voir aussi le point
ouvert non vérifié ajouté en section 4 sur la compatibilité de `update_checker.py` (réécrit
depuis pour un remplacement de dossier source, pas d'exécutable) avec ce pipeline tiers.

Le pipeline R2 existant (`update_checker.py` + `manifest.json` hébergé sur Cloudflare R2) reste
valable tel quel après migration vers Nuitka. Le cycle reste :

1. Toi : build Nuitka du nouveau binaire → upload sur R2 → mise à jour de `manifest.json`
   (version, url, sha256). **Cette étape reste manuelle, quel que soit l'outil de build —
   l'automatisation ne couvre que la propagation aux machines, pas la publication.**
2. Chaque bot (via `UPDATE_CHECK_ENABLED=1`) compare sa version courante au manifeste à chaque
   cycle, télécharge et vérifie le SHA256, puis se remplace lui-même et redémarre
   (`os.execv`) — automatique, sans intervention.

**Mécanisme actuel du parc interne (depuis le 20/07/2026)** : le principe ci-dessus (manifeste
JSON, SHA256, `os.execv` pour relancer) reste le même, mais ce qui est téléchargé et remplacé a
changé — un zip du dossier `code\` (source Python), pas un exécutable. Voir
`build_release_zip.ps1` (construit le zip + `manifest.json`) et `update_checker.py` actuel
(`_replace_source_and_restart`, `_swap_code_dir` : renomme `code\` en `code.old`, extrait le zip
en remplacement, puis `os.execv(sys.executable, [sys.executable, main_py, ...])` — un vrai
interpréteur Python du `venv\` local, pas un binaire).

---

## 6. Bug de fond identifié et corrigé — injection des variables globales dans `os.environ`

**Problème initial** : `config_loader.load_config()` produisait un dict Python avec des clés
alias, mais rien ne réinjectait ces valeurs dans `os.environ`. Or tout le code applicatif lit
ces paramètres via `os.getenv(...)` avec la casse d'origine, sans jamais consulter ce dict —
toute valeur définie uniquement dans le fichier de config globale était donc silencieusement
ignorée.

**Statut initial (dépassé) : réinjection généralisée.** Une première version du patch faisait
réinjecter par `config_loader.py` chaque clé de `key_aliases` dans `os.environ` via une boucle
générique. Cette approche a été identifiée comme incompatible avec la protection recherchée par
`global_config.py` : tant qu'un mécanisme réinjecte une clé dans `os.environ`, un tiers peut
définir cette variable d'environnement avant le lancement du binaire compilé et, selon l'ordre
d'exécution, contourner la valeur figée à la compilation. Les deux mécanismes ne peuvent pas
coexister sur les mêmes clés.

**Statut : migration terminée pour les variables non-exclues.** `global_config.py` créé (module
à la racine du projet, à côté de `_license_config.py`, même convention d'import à plat — voir
section 3) avec l'ensemble des constantes `GLOBAL_CONFIG` retenues en section 2 (hors
`LOG_LEVEL`, `LOG_STEP_SUMMARY`, `CAPTCHA_PROVIDER`, explicitement exclus — voir section 2).
`surveybot.spec` mis à jour (`hiddenimports=['_license_config', 'global_config']`).

Convention de migration appliquée à chaque consommateur : `try: from global_config import X /
except ImportError: fallback os.getenv("X", défaut)` — garantit qu'en build compilé (module
forcé par `hiddenimports`) l'import réussit toujours et l'environnement n'est jamais consulté
pour ces noms, tout en gardant le workflow dev/attach intact (module absent en local → fallback
naturel). Les variables `PAR_BOT` ne sont pas concernées et continuent d'être lues depuis
l'environnement du process — c'est leur mécanisme de transmission légitime.

**`config_loader.py` corrigé en conséquence** : la boucle de réinjection dans `os.environ` a été
retirée pour les clés désormais couvertes par `global_config.py` (`RUN_ENV`, `PLATFORM`,
`STATE_BACKEND`, `STATE_TABLE`, `STATE_TTL_DAYS`, `SURVEY_BROWSER_BIN`, `SURVEY_HEADLESS`,
`SNAP_ENABLED`, `UPDATE_CHECK_ENABLED`, `UPDATE_MANIFEST_URL`, `CTA_INTERCEPT_ONLY`). Le
mécanisme de réinjection continue de fonctionner sans changement pour le reste des clés
`PAR_BOT`/secrets (`EMAIL`, `PASSWORD`, `OPENAI_API_KEY`, `DATABASE_URL`, `LICENSE_KEY`,
`payout_name`, `payout_revolut_tag`, `telegram_*`, `TWO_CAPTCHA_KEY`, `SURVEY_TZ`,
`ACTION_DEBUG_TARGET`, `DOM_CONTEXT_DEBUG`, `SURVEY_CTX_DEBUG`).

**Migrés vers `global_config.py` avec la convention import direct + fallback dev** :
- `config.py` : `RUN_ENV`, `CTA_INTERCEPT_ONLY` (via `is_cta_intercept_only()`) — fait en premier,
  a servi de pattern de référence.
- `platforms/__init__.py`, `main.py` : `PLATFORM`.
- `preselection/auth_handler.py`, `preselection/survey_handler.py`,
  `preselection/survey_navigator.py`, `Survey/survey_solver.py`, `Management/snap_uploader.py`,
  `launch.py` : `SNAP_ENABLED`.
- `State/account_state.py` : `STATE_BACKEND`, `STATE_TABLE`, `STATE_TTL_DAYS`, `RUN_ENV`.
- `State/survey_memory.py` : `STATE_BACKEND`.
- `Cash/payout.py`, `Survey/page_snapshot.py` : `RUN_ENV`.
- `preselection/playwright_launcher.py` : `SURVEY_BROWSER_BIN`, `SURVEY_HEADLESS`.
- `update_checker.py` : `UPDATE_CHECK_ENABLED`, `UPDATE_MANIFEST_URL`.

**Deuxième patch — trou de sécurité détecté par audit, puis corrigé.** L'audit exhaustif (grep
sur tous les `os.getenv` restants, voir méthode ci-dessous) a révélé que la première vague de
migration avait laissé deux variables partiellement non protégées, dans des fichiers non
couverts par le premier patch :
- `RUN_ENV` : lu encore via `os.getenv` direct dans `Cash/payout.py`,
  `preselection/auth_handler.py`, `State/account_state.py`, `Survey/action_dispatcher.py`,
  `Survey/dom_extractors_decipher.py` (4 occurrences), `Survey/dom_extractors_misc.py`,
  `Survey/page_snapshot.py` (2 occurrences).
- `CTA_INTERCEPT_ONLY` : lu encore via `os.getenv` direct à 17 endroits, notamment
  `Survey/action_dispatcher.py` (11 occurrences), `Cash/payout.py`,
  `Management/guards/runtime_guard.py`, `preselection/question_analyzer.py`,
  `preselection/survey_navigator.py`, `Survey/functions.py`, `Survey/survey_executor.py`.
  Point le plus sensible de toute la liste `GLOBAL_CONFIG` : c'est l'interrupteur qui décide si
  les clics CTA sont réels ou interceptés (y compris potentiellement sur le chemin de retrait) —
  tant qu'il restait lisible via `os.getenv`, un tiers pouvait le neutraliser en définissant la
  variable d'environnement avant le lancement du binaire compilé.

**Statut : patché et vérifié par audit.** Les deux variables ont été migrées vers le pattern
import direct + fallback dev dans tous les fichiers listés ci-dessus. Un script d'audit
PowerShell (grep de tout `os.getenv("X")` sur les noms `GLOBAL_CONFIG` migrés, avec détection
heuristique de la présence d'un `from global_config import X` dans le même fichier pour
distinguer un fallback légitime d'un consommateur oublié) a été passé après ce second patch :
**0 occurrence à vérifier** — chaque `os.getenv` restant sur `RUN_ENV`, `CTA_INTERCEPT_ONLY`, et
toutes les autres variables migrées correspond bien au point de définition unique dans le
fichier qui importe `global_config` (le fallback `except ImportError` attendu), confirmé par
relecture manuelle des lignes.

**Point de vigilance de la section précédente : clos.** `preselection/auth_handler.py` a bien
été inclus dans ce second patch pour `RUN_ENV` ; l'audit confirme qu'il ne reste plus de lecture
`os.getenv` isolée pour cette variable dans ce fichier.

**Limite connue de l'audit, à garder en tête** : la détection est une heuristique texte (regex),
pas une preuve formelle. Un import indirect (`import global_config as gc` puis `gc.RUN_ENV`) ou
un alias ne serait pas détecté par le script actuel. Aucune occurrence de ce style n'a été
observée dans le code à ce jour, mais si un futur patch en introduit une, l'audit devra être
adapté en conséquence.

**Toujours hors périmètre** (décision actée, voir section 2) : `Survey/log_utils.py`
(`LOG_LEVEL`, `LOG_STEP_SUMMARY` restent en `os.getenv` classique) et toute lecture de
`CAPTCHA_PROVIDER`.

**Troisième patch — retrait de `Utils/config` et nettoyage de `key_aliases`.** Maintenant que
l'audit confirme qu'aucun consommateur `GLOBAL_CONFIG` ne dépendait plus du fichier JSON externe
`Utils/config`, sa lecture (`_load_local_config()`) a été retirée de `config_loader.py`. La seule
source de secrets restante est `load_remote_secrets()` (`secret_loader.py` — ENV unitaires,
`TOPSURVEYS_SECRET_JSON`, overrides) : mécanisme non touché par ce patch, toujours indépendant
de `Utils/config`.

Au passage, `LICENSE_KEY` a été retirée de `key_aliases` dans `config_loader.py` : cette entrée
n'était consommée nulle part via `os.getenv`/`os.environ` — `LICENSE_KEY` est lue exclusivement
depuis `_license_config.py` par `license_guard.py` (voir section 3). Sa présence dans
`key_aliases` était trompeuse (laissait croire à un mécanisme de réinjection qui n'existait pas
pour cette clé) et a été supprimée.

**Point ouvert** : le retrait de `Utils/config` change le comportement du workflow dev local si
un développeur s'appuyait sur ce fichier pour des secrets non définis en variable
d'environnement — à vérifier une fois en usage réel ; sinon aucun impact, `load_remote_secrets()`
couvre déjà tous les secrets nécessaires en dev/attach.

---

## 7. Prochaines étapes (à traiter une par une, patchs séparés)

- [x] Retirer `_license_config.py` de `datas` dans `surveybot.spec` (import module normal).
- [x] Corriger l'incohérence d'import `surveybot._license_config` vs `_license_config` dans
      `license_guard.py`.
- [x] Créer `global_config.py` avec les constantes `GLOBAL_CONFIG`/`GLOBAL_SECRET` listées en
      section 2. Ajouté à `hiddenimports` dans `surveybot.spec`.
- [ ] Ajouter un `log.warning` dans le `except ImportError` de `license_guard._get_license_key()`
      pour rendre visible toute régression future sur cet import (voir section 3).
- [x] Modifier le code consommateur des variables `GLOBAL_CONFIG` retenues (hors `LOG_LEVEL`,
      `LOG_STEP_SUMMARY`, `CAPTCHA_PROVIDER`, exclus — voir section 2) pour lire exclusivement
      le module compilé, avec fallback `os.getenv` uniquement en dev/attach — voir section 6
      pour la liste exhaustive des fichiers migrés. `config_loader.py` corrigé en parallèle :
      la boucle de réinjection `os.environ` ne couvre plus ces clés.
- [x] Deuxième patch : corriger les lectures `os.getenv` directes de `RUN_ENV` et
      `CTA_INTERCEPT_ONLY` détectées par audit dans des fichiers non couverts par le premier
      patch (`Cash/payout.py`, `preselection/auth_handler.py`, `State/account_state.py`,
      `Survey/action_dispatcher.py`, `Survey/dom_extractors_decipher.py`,
      `Survey/dom_extractors_misc.py`, `Survey/page_snapshot.py`,
      `Management/guards/runtime_guard.py`, `preselection/question_analyzer.py`,
      `preselection/survey_navigator.py`, `Survey/functions.py`, `Survey/survey_executor.py`) —
      voir section 6.
- [x] Point de vigilance sur `RUN_ENV` dans `preselection/auth_handler.py` : clos par le
      deuxième patch ci-dessus.
- [x] Audit exhaustif (grep) de tout `os.getenv` restant sur les noms `GLOBAL_CONFIG` migrés :
      réalisé après le deuxième patch, 0 occurrence à vérifier — voir section 6 pour la méthode
      et ses limites connues (heuristique texte, pas une preuve formelle).
- [x] Retirer `Utils/config` (fichier JSON externe) et sa lecture dans
      `config_loader.py`/`key_aliases`. Fait — `load_remote_secrets()` (`secret_loader.py`)
      reste l'unique source de secrets restante. Nettoyage au passage : entrée `LICENSE_KEY`
      retirée de `key_aliases` (jamais consommée via `os.environ`, voir section 3/6).
      Point ouvert : vérifier en usage réel qu'aucun workflow dev local ne dépendait de ce
      fichier pour un secret non couvert par `load_remote_secrets()`.

- [x] Aligner `State/account_state.py` et `State/survey_memory.py` sur la même logique de
      résolution que `license_guard._get_database_url()`. Fait — centralisé dans un module
      dédié `db_config.py` (`get_database_url()`, même priorité `_license_config` → `os.getenv`
      dev/attach), consommé par les trois fichiers au lieu de dupliquer la logique. Voir
      section 3. Le remplacement par l'endpoint serveur (item suivant) reste un chantier
      séparé, non résolu par ce patch.
- [x] Sécuriser l'accès à `licenses` depuis le client (remplace l'item "concevoir un endpoint
      HTTP", écarté — voir section 3 pour le raisonnement). Fait via un rôle Postgres restreint
      (`surveybot_client`) + deux fonctions `SECURITY DEFINER` (`check_license`,
      `increment_license_payout`), côté serveur et côté client (`license_guard.py`,
      `payout.py`). Reste à faire : basculer `DATABASE_URL` sur ce rôle (voir section 8).
- [x] Basculer le pipeline de build de PyInstaller vers Nuitka. `surveybot.spec` supprimé,
      `nuitka_build.ps1` créé (voir section 4 pour la commande exacte et les inclusions
      manuelles vérifiées). Reste à faire : tester le cycle complet build → upload R2 →
      auto-update → relance sur une seule machine avant généralisation aux 14 PC — en
      particulier le diagnostic `sys.executable`/`NUITKA_ONEFILE_BINARY` (voir section 4,
      point critique), à valider avant tout autre test.
      **Correction (24/07/2026)** : « généralisation aux 14 PC » visait le parc interne, qui
      n'utilise plus ce pipeline Nuitka depuis le pivot du 20/07/2026 (voir
      `Utils/ORCHESTRATION_TRACKING.md` section 1bis). Cette étape de test ne concerne donc plus
      le parc interne ; si le pipeline tiers/récepteurs est encore actif, voir le point ouvert
      ajouté en section 4 sur la compatibilité de `update_checker.py` réécrit avec un binaire
      onefile.
- [x] Implémenter la fonctionnalité d'import JSON pour `accounts.json` côté logiciel. Fait via
      `import_accounts.py` — script autonome, jamais embarqué dans le binaire Nuitka, remplace
      entièrement `accounts.json` (avec sauvegarde `.bak` de l'ancien fichier), validation
      indépendante par entrée (une entrée invalide est exclue, les autres sont importées),
      résumé clair en fin d'exécution (succès + raisons de rejet). Reconnaît les clés
      `PAR_RECEPTEUR`/`GLOBAL_CONFIG` glissées par erreur pour produire un message de rejet
      explicite plutôt qu'un simple "clé inconnue".
      **Bug de casse détecté puis corrigé** (voir section 2) : `_from_direct_env_keys()` dans
      `secret_loader.py` remappe désormais explicitement `PAYOUT_NAME`/`PAYOUT_REVOLUT_TAG`/
      `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` vers leur clé logique en minuscules — la pile de
      priorité (receiver_config → JSON env → ENV directs → overrides nommés) fonctionne
      maintenant comme documenté pour ces 4 clés.
- [x] Allouer une IP publique à `surveybot-db`, configurer `pg_hba.conf`/SSL (`sslmode=require`),
      tester en transaction annulée avant bascule définitive — voir section 8 pour le détail
      complet (IP dédiée `137.66.7.173`, connexion via `surveybot-db.fly.dev` obligatoire — pas
      l'IP directement, `pg_hba.conf` déjà permissif par défaut, test réussi avec la licence
      `Wilfried` insérée). Seule étape restante, non cochée séparément : mettre à jour
      `DATABASE_URL` dans `_license_config.py` avec le nouveau host, puis rebuild Nuitka.

---

## 8. Accès réseau à la base Postgres depuis le parc bare-metal — décision actée

**Contexte** : `DATABASE_URL` est partagée entre toutes les instances (une seule base centrale
pour tout le parc, d'où le hardcoding). La base tourne sur Fly.io (`surveybot-db`), dont le
hostname canonique (`surveybot-db.flycast`) n'est routable que depuis le réseau privé Fly.io
(WireGuard/6PN) — pas depuis des mini-PC Windows bare-metal situés hors de ce réseau. Aucun
déploiement prod n'existe encore à ce jour : la question doit être tranchée avant le premier
déploiement, pas corrigée après coup.

**Deux options considérées** :
- **A — IP publique Fly.io + TLS obligatoire.** Allouer une IP publique à `surveybot-db`,
  forcer les connexions chiffrées (`sslmode=require`), autoriser les connexions distantes.
  `DATABASE_URL` devient directement joignable depuis n'importe quel mini-PC sans dépendance
  réseau supplémentaire.
- **B — VPN mesh (Tailscale/ZeroTier) partagé avec l'administration.** Réutiliser le mesh déjà
  prévu pour l'administration multi-site (RDP/SSH, voir section "infrastructure" du projet)
  pour aussi faire transiter l'accès à la base.

**Décision actée : option A.** Coupler la disponibilité de la fonction cœur du bot (accès BD,
nécessaire à chaque cycle pour tourner et retirer) à la disponibilité d'un VPN mesh tiers
aurait introduit une dépendance disproportionnée : un incident ou un agent Tailscale down sur
une machine aurait empêché cette machine de gagner de l'argent, pas seulement d'être
administrée à distance. Un bot ne doit dépendre que du strict nécessaire pour fonctionner
(proxy + Chrome + accès direct BD). Le risque de l'exposition publique est jugé acceptable
car déjà borné par les permissions Postgres restreintes de la section 3 : en cas de fuite de
`DATABASE_URL`, l'accès reste limité à `account_state`/`survey_memory` (gênant mais pas
critique), avec zéro accès direct possible à `licenses`. Le mesh VPN reste pertinent et
prévu pour l'administration (RDP/SSH), un besoin différent et non-bloquant pour les gains.

**Option B reconsidérée puis explicitement écartée** : une variante plus légère de l'option B
a été proposée en cours de discussion — WireGuard natif Fly.io (`flyctl wireguard create`),
distinct du mesh Tailscale/ZeroTier d'administration, qui aurait évité toute exposition
publique de la base en faisant rejoindre chaque mini-PC au réseau privé Fly directement.
Confirmé par l'utilisateur : option A maintenue malgré cette alternative plus étanche par
construction. Décision actée, ne pas rouvrir sans nouvel élément.

**Précisions actées avant exécution (points non évidents, tranchés explicitement)** :
- `sslmode=require` (chiffrement, sans vérification d'identité serveur) retenu plutôt que
  `verify-full` (chiffrement + vérification via certificat CA distribué à chaque binaire) —
  jugé disproportionné vu le rôle déjà restreint aux deux fonctions `SECURITY DEFINER` de la
  section 3 ; une interception ne donnerait accès qu'à ce que ces fonctions autorisent.
- La protection réelle contre une connexion en clair se joue côté serveur (`pg_hba.conf` en
  `hostssl` uniquement pour `surveybot_client`, aucune ligne `host`/`hostnossl` de repli), pas
  côté client (`sslmode=require` seul n'empêche rien si le serveur accepte aussi le non-chiffré).
- Restriction par IP source jugée impraticable (machines sur plusieurs sites, IP dynamiques) —
  acceptée en `0.0.0.0/0`, compensée uniquement par mot de passe fort (`scram-sha-256`) + rôle
  restreint + SSL forcé côté serveur.

**Statut : non exécuté — plan détaillé fourni, en attente d'exécution manuelle par
l'utilisateur** (nécessite `flyctl`/accès Fly.io, hors d'atteinte de cette session) :
1. `flyctl ips list -a surveybot-db` puis `flyctl ips allocate-v4 -a surveybot-db`.
2. Vérifier que `fly.toml` expose bien un service TCP externe sur le port Postgres (5432) —
   un cluster managé `flyctl postgres create` n'expose par défaut que le réseau privé 6PN ;
   ajouter le bloc `[[services]]` correspondant si absent, puis redéployer avant que l'IP
   allouée serve à quelque chose.
3. Éditer `pg_hba.conf` sur la VM Postgres (SSH via `flyctl ssh console`) : ajouter des lignes
   `hostssl all surveybot_client 0.0.0.0/0 scram-sha-256` (IPv4 et IPv6), sans ligne de repli
   non-SSL pour ce rôle. Confirmer `ssl = on` dans `postgresql.conf`, recharger la config.
4. **Test en transaction annulée avant toute bascule définitive** : depuis une machine externe,
   `psql` vers le nouveau host public avec `sslmode=require`, exécuter
   `BEGIN; SELECT * FROM check_license('Wilfried'); ROLLBACK;` — valide la connexion SSL, le
   résultat de la fonction, et garantit qu'aucune écriture n'est persistée pendant le test.
5. Seulement après succès du test : mettre à jour `DATABASE_URL` dans `_license_config.py` avec
   le nouveau host public, puis rebuild Nuitka. Pas de cycle d'auto-update à gérer pour ce
   changement précis — aucun déploiement prod n'existe encore, c'est le tout premier build avec
   la bonne valeur.

**Statut : test réseau/SSL validé — un point de données reste en suspens.**
Exécuté par l'utilisateur :
- IP dédiée allouée (`137.66.7.173`, ~2$/mois) — la partagée ne convient pas ici (Fly ne
  supporte le TCP brut sur IPv4 partagée que pour HTTP/TLS avec SNI, pas pour le protocole
  Postgres `pg_tls`, confirmé par la doc Fly).
- Service TCP externe déjà présent dans `fly.toml` généré (`internal_port = 5432`,
  `protocol = "tcp"`, `handlers = ["pg_tls"]`) — l'app managée l'avait déjà, aucun ajout requis.
- `pg_hba.conf` déjà permissif par défaut sur l'image (`host all all 0.0.0.0/0 md5`) — aucune
  édition nécessaire, contrairement à l'hypothèse initiale de cette section.
- **Point non anticipé, corrigé en cours de route** : une connexion par IP brute échoue
  (`SSL SYSCALL error: EOF detected`) — le proxy Fly route/termine le TLS du handler `pg_tls`
  sur la base du nom d'hôte demandé (SNI), y compris avec une IP dédiée. Il faut se connecter
  via `surveybot-db.fly.dev` (résolu automatiquement vers l'IP dédiée), jamais l'IP en direct.
  **`DATABASE_URL` finale devra donc utiliser ce nom d'hôte, pas l'IP.**
- Test en transaction annulée exécuté avec succès (SSL négocié, rôle `surveybot_client`
  authentifié, fonction `check_license('Wilfried')` exécutée, `ROLLBACK` propre) — la fonction
  ne retourne aucune ligne (`None`) **confirmé normal, pas un bug** : la table `licenses` est
  vérifiée vide (`SELECT * FROM licenses` via Adminer → "No rows.") — elle n'a simplement jamais
  été peuplée depuis sa création. `check_license` elle-même n'est donc pas mise en cause.

**Reste à faire avant bascule définitive** :
1. ~~Insérer la ligne de licence attendue dans `licenses`~~ **Fait** — ligne insérée
   (`license_key='Wilfried'`, `is_active=true`, `total_payout_eur=200`,
   `max_payout_eur=1000000000`).
2. ~~Re-tester `check_license('Wilfried')` en transaction annulée~~ **Fait, succès confirmé** —
   `(True, Decimal('200'), Decimal('1000000000'))` retourné, `ROLLBACK` propre. Le test complet
   (réseau, SSL, authentification, droits `SECURITY DEFINER`, données) est validé de bout en
   bout.
3. **Fait** — `DATABASE_URL` mis à jour dans `_license_config.py` :
   `postgres://surveybot_client:p%40ssw0rD%21123@surveybot-db.fly.dev:5432/postgres?sslmode=require`.
   Mot de passe percent-encodé (`%40`=`@`, `%21`=`!`) — la valeur précédente avait le même bug
   de parsing d'URI que celui contourné plus haut pour les tests (`@` non encodé dans le mot de
   passe cassait le découpage user:password/host), corrigé au passage. `sslmode=require` ajouté
   dans l'URL. **Reste seulement le rebuild Nuitka** avant tout déploiement.