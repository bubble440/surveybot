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
  `SURVEY_BROWSER_BIN`, `SURVEY_HEADLESS`, `SURVEY_VISION_MODEL`, `LOG_LEVEL`,
  `LOG_STEP_SUMMARY`, `CTA_INTERCEPT_ONLY` (confirmé = 0 en prod), `MAX_MAIN_CYCLES`,
  `ACCOUNT_LOCK_TTL_SEC`, `HEARTBEAT_INTERVAL_SEC`, `HEARTBEAT_JITTER_SEC`,
  `DOM_FRAME_MAX_DEPTH`, `AA_MATRIX_MAX_ROWS`, `AA_SELECTION_LIST_MAX`, `MAX_ACTIONS_PER_PLAN`,
  `CAPTCHA_PROVIDER`, `SNAP_ENABLED` (= 0), `UPDATE_CHECK_ENABLED`, `UPDATE_MANIFEST_URL`.

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

**Constat technique important (à corriger, patch non encore fait)** : dans `surveybot.spec`
actuel, `_license_config.py` est déclaré dans `datas=[...]`, ce qui l'embarque **en clair, non
compilé** — extrait en clair dans `%TEMP%\_MEIxxxxxx` à chaque lancement (mode `--onefile`).
Cela ne protège donc PAS la confidentialité du contenu (lisible via extraction/inspection du
dossier temporaire), seulement l'intégrité (une édition ne persiste pas au lancement suivant,
car réextraite depuis l'exe immuable).

**Décision actée** : retirer `_license_config.py` de `datas` et le laisser être importé
normalement comme un module Python classique, pour qu'il soit compilé (par Nuitka, voir
section 4) au même niveau de protection que le reste du code applicatif.

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

**Statut : patché.** `config_loader.py` réinjecte désormais chaque clé listée dans
`key_aliases` dans `os.environ` via une boucle générique (pas de cas par cas), en respectant la
priorité stricte (ne jamais écraser une valeur déjà présente dans l'environnement — donc jamais
écraser ce que fournit `accounts.json`/le script de lancement). L'appel à `load_config()` dans
`main.py` a été vérifié comme placé avant l'import de tout module qui lit des constantes au
niveau module (ex: `State/account_state.py`, dont l'import est différé en local dans les
fonctions).

**Point ouvert, non encore implémenté** : suite au passage prévu de `GLOBAL_CONFIG` vers un
module compilé (`global_config.py`, section 2), le code qui consomme ces variables devra lire
la constante compilée **directement et exclusivement**, sans jamais consulter `os.environ` pour
ces noms précis. Sinon, un simple `$env:OPENAI_API_KEY=...` avant lancement (ou une clé ajoutée
par erreur dans `accounts.json`, injectée dans l'environnement du process enfant comme les
variables PAR_BOT légitimes) contournerait silencieusement toute la protection Nuitka, sans
même nécessiter de décompilation. Cette règle doit être appliquée uniquement aux noms de
variables `GLOBAL_CONFIG`/`GLOBAL_SECRET` — les variables `PAR_BOT` doivent continuer à être
lues depuis l'environnement du process, c'est leur mécanisme de transmission légitime.

---

## 7. Prochaines étapes (à traiter une par une, patchs séparés)

- [ ] Retirer `_license_config.py` de `datas` dans `surveybot.spec` (import module normal).
- [ ] Créer `global_config.py` avec les constantes `GLOBAL_CONFIG`/`GLOBAL_SECRET` listées en
      section 2, en remplacement du fichier JSON externe `Utils/config`.
- [ ] Modifier le code consommateur de ces variables pour lire exclusivement le module compilé
      (pas de fallback `os.getenv` pour ces noms précis) — voir section 6.
- [ ] Retirer `DATABASE_URL` de `key_aliases`/`config_loader.py` et du fichier `Utils/config`.
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
