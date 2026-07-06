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

## 2. Catégorisation des variables — décision actée

Deux catégories strictement séparées, ne jamais les mélanger dans un même fichier :

### PAR_BOT (propre à chaque instance, modifiable par le récepteur)
Transmises via `accounts.json` → injectées dans l'environnement du process enfant par
`launch_all.ps1` (1 bloc `$env_vars` par bot). Isolation déjà garantie par le mécanisme standard
de process Windows : aucune fuite possible entre bots sur une même machine.

Liste actuelle : `ACCOUNT_ID`, `EMAIL`, `PASSWORD`, `PROXY_URL`, `PROXY_USER`, `PROXY_PASS`,
`profile_dir` / `CHROME_PROFILE_DIR`, `payout_name`, `payout_revolut_tag`.

**Décision** : `accounts.json` ne doit contenir QUE ces clés. Toute clé globale qui s'y glisse
(ex: `OPENAI_API_KEY`, `TWO_CAPTCHA_KEY`, tokens Telegram) doit être retirée — déjà fait lors du
nettoyage initial.

**Point ouvert (non implémenté)** : une fonctionnalité d'import de fichier JSON pour peupler
`accounts.json` est prévue côté logiciel (build Nuitka). Elle doit valider le schéma à l'import
et rejeter/alerter toute clé qui ne fait pas partie de la liste PAR_BOT ci-dessus, pour éviter
qu'un import réintroduise silencieusement des secrets globaux dans un fichier par-bot.

### GLOBAL_CONFIG / GLOBAL_SECRET (constant entre toutes les instances, NE DOIT PAS être modifiable par le récepteur)
Décision actée : ces variables seront compilées en dur dans le binaire (module Python
`global_config.py`, compilé nativement par Nuitka — voir section 4), et non plus lues depuis un
fichier JSON externe éditable.

Liste retenue (issue du tri effectué dans la conversation, hors `LICENSE_KEY`/`DATABASE_URL`/
`BOT_VERSION` qui restent dans `_license_config.py` — voir section 3) :

- Secrets : `OPENAI_API_KEY`, `TWO_CAPTCHA_KEY` (ou `CAPSOLVER_API_KEY` selon le fournisseur
  retenu — un seul des deux, pas les deux), `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
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

**Décision actée sur `DATABASE_URL`** : ne devrait à terme plus être embarquée du tout, ni dans
`_license_config.py` ni ailleurs côté client. Remplacement prévu par un petit endpoint HTTP
côté serveur (hébergé par exemple à côté du bucket R2) qui reçoit `LICENSE_KEY`, interroge
Postgres côté serveur, et renvoie uniquement un résultat de validation (booléen/statut) —
jamais la chaîne de connexion complète. Ainsi, même un binaire totalement décompilé ne donnerait
jamais accès direct à la base centrale. **Non implémenté à ce stade — décision de principe
actée, à planifier comme chantier séparé.**

**`LICENSE_KEY` reste embarquée**, propre à chaque récepteur. Sa compromission éventuelle
(après décompilation réussie) reste contenue à ce récepteur — révocable unitairement via
`UPDATE licenses SET is_active = false WHERE license_key = ...`, sans impact sur le reste du
parc. Pas besoin de builds uniques par machine pour obtenir cette propriété : elle existe déjà
au niveau de la table Postgres `licenses`.

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

**Étape de validation avant généralisation (non encore faite)** : tester le cycle complet
`build Nuitka → upload R2 → auto-update → relance` sur **une seule machine** avant de déployer
aux 14 PC. Point d'incertitude à vérifier spécifiquement : le comportement de
`sys.executable` / l'auto-remplacement de l'exe (`_replace_exe_and_restart` dans
`update_checker.py`) avec un binaire `--onefile` Nuitka, qui gère l'extraction de ses
dépendances différemment de PyInstaller en interne.

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

## 5. Mise à jour automatique du parc — confirmé, aucun changement de mécanisme requis

Le pipeline R2 existant (`update_checker.py` + `manifest.json` hébergé sur Cloudflare R2) reste
valable tel quel après migration vers Nuitka. Le cycle reste :

1. Toi : build Nuitka du nouveau binaire → upload sur R2 → mise à jour de `manifest.json`
   (version, url, sha256). **Cette étape reste manuelle, quel que soit l'outil de build —
   l'automatisation ne couvre que la propagation aux machines, pas la publication.**
2. Chaque bot (via `UPDATE_CHECK_ENABLED=1`) compare sa version courante au manifeste à chaque
   cycle, télécharge et vérifie le SHA256, puis se remplace lui-même et redémarre
   (`os.execv`) — automatique, sans intervention.

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
- [ ] Considérer retirer `Utils/config` (fichier JSON externe) et sa lecture dans
      `config_loader.py`/`key_aliases`, maintenant que l'audit ne montre plus de consommateur
      manquant. Vérifier avant tout qu'aucune clé `PAR_BOT`/secret légitime ne dépend encore
      exclusivement de ce fichier avant de le supprimer.

- [ ] Aligner `State/account_state.py` et `State/survey_memory.py` sur la même logique de
      résolution que `license_guard._get_database_url()` (embarqué en priorité, fallback env
      pour le dev/attach uniquement) — en attendant le remplacement par l'endpoint serveur.
- [ ] Concevoir et implémenter l'endpoint de validation de licence côté serveur (remplace la
      connexion Postgres directe depuis le client pour la vérification de licence).
- [ ] Basculer le pipeline de build de PyInstaller vers Nuitka ; tester le cycle complet
      build → upload R2 → auto-update → relance sur une seule machine avant généralisation.
- [ ] Implémenter la fonctionnalité d'import JSON pour `accounts.json` côté logiciel, avec
      validation de schéma (rejet/alerte si une clé `GLOBAL_CONFIG` y apparaît).
- [ ] Nettoyer `accounts.json` des variables de diagnostic non-prod (`YSENSE_EMAIL`,
      `YSENSE_PASSWORD`) si souhaité.