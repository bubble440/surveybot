# DEPLOYMENT_REFACTOR_TRACKER.md
# Suivi des modifications — Migration Fly.io -> Bare-metal Windows
# Cree lors de la session de conception architecture (2026-07-02)
# A passer a Claude en contexte avant chaque session de travail.

================================================================================
DECISIONS D'ARCHITECTURE
================================================================================

**Contexte :**
Migration du deploiement Fly.io (VMs ephemeres Linux) vers des mini-PCs Windows
bare-metal (NiPoGi, Ryzen 5 7430U, 32 GB RAM, 512 GB NVMe, Windows 11 Pro).
7 bots par machine. Chaque bot a un proxy ISP dedie et un profil Chrome local permanent.

**Principes retenus :**
- Profil Chrome cree manuellement une fois (avec le bon proxy), reutilise en permanence.
- Pas de rotation de profil, pas de recreation automatique.
- Fail-closed : si le Postgres central est injoignable, le bot s'arrete.
- Mise a jour du code via git pull + redemarrage au retour au listing TopSurveys.
- Compilation PyInstaller : un binaire distinct par licence, LICENSE_KEY et
  DATABASE_URL embarquees en dur — invisibles pour l'utilisateur final.
- Pas de compte backup.

================================================================================
ENVIRONNEMENTS — COMPORTEMENT ATTENDU
================================================================================

**Decision : deux environnements seulement.**
  - RUN_ENV=prod        -> bots en production (fonctionnement autonome)
  - BROWSER_MODE=attach -> debug ponctuel sur un survey precis (via main.py)

RUN_ENV=local et LOCAL_UNATTENDED supprimes du projet. [FAIT item 9]
Pivot unique : is_attach_mode() = (BROWSER_MODE == "attach").
Tout le reste se derive de ce seul switch.

Tableau de reference :

  Comportement                        | prod bare-metal | attach (debug)
  ------------------------------------|-----------------|---------------
  Pauses interactives (input())       |       NON       |     OUI
  should_pause_for_captcha()          |       NON       |     OUI
  should_pause_before_cta()          |       NON       |     OUI (si LOCAL_CTA_REQUIRE_ENTER=1)
  should_block_for_input()           |       NON       |     OUI
  should_run_hot_reload()            |       NON       |     OUI
  RuntimeGuard active                 |       OUI       |     NON
  Heartbeat Postgres                  |       OUI       |     NON
  Mise a jour code (git pull)         |       OUI       |     NON
  chrome_profile_store (load/save)    |    SUPPRIME     |     NON
  Serveur HTTP debug local            |       NON       |     NON
  Ecriture fichier PID                |       OUI       |     NON
  Verification licence (license_guard)|       OUI       |     NON
  Handler arret propre                |  SIGINT Windows |   Ctrl+C

**Note sur SIGTERM :**
Handler SIGINT (Ctrl+C) ajoute avec le meme comportement que SIGTERM :
liberer le slot Postgres + arret propre + suppression du fichier PID.
SIGTERM conserve en parallele pour compatibilite. [FAIT item 8]

**Note sur IS_LOCAL dans les fichiers bas niveau :**
account_state.py, page_snapshot.py, action_dispatcher.py conservent leur propre
IS_LOCAL = (RUN_ENV != "prod") sans importer config.py (evite les circular imports).
La valeur par defaut de os.getenv("RUN_ENV", ...) a ete corrigee de "local" a "prod"
dans ces fichiers. [FAIT item 9]

================================================================================
FICHIERS SUPPRIMES
================================================================================

[x] preselection/chrome_profile_store.py — FAIT (supprime manuellement)

================================================================================
SUPPRESSIONS DANS LES FICHIERS EXISTANTS
================================================================================

[x] launch.py — FAIT (items 5, 7, 8)
    — Import chrome_profile_store supprime.
    — Appels load_profile() / save_profile() / start_profile_autosave() supprimes.
    — References chrome_profile_chunks supprimees.
    — Helpers PID : _pid_path(), write_pid_file(), delete_pid_file().
    — write_pid_file() appele dans mark_bot_running().
    — delete_pid_file() appele dans _make_stop_handler() et launch_driver_or_fail().
    — install_sigint_handler() ajoute. _make_sigterm_handler() remplace par
      _make_stop_handler(sig_name=...) partage entre SIGTERM et SIGINT.

[x] config.py — FAIT (item 9, 2026-07-03)
    — RUN_ENV=local et LOCAL_UNATTENDED supprimes.
    — is_local_env() supprimee.
    — should_block_for_input() reconditionnee sur is_attach_mode().
    — should_run_hot_reload() reconditionnee sur is_attach_mode().
    — should_pause_before_cta() conservee, reconditionnee sur is_attach_mode()
      + LOCAL_CTA_REQUIRE_ENTER (outil debug attach).
    — LOCAL_CTA_DEBUG supprime (branche LOCAL_UNATTENDED disparue).
    — RUN_MODE conserve comme constante no-op pour compatibilite d'import.
    — IS_LOCAL (alias) supprime.
    — Pivot unique : is_attach_mode() = (BROWSER_MODE == "attach").

[x] main.py — FAIT (items 3, 8, 9, 2026-07-03)
    — IS_LOCAL supprime (remplace par is_attach_mode() / not is_attach_mode()).
    — Garde attach_forbidden_in_prod supprimee (devenue sans objet).
    — Defaut RUN_ENV corrige a "prod" dans check_license_or_exit().
    — check_license_or_exit() appele en tout premier.
    — install_sigint_handler(account_id) appele apres install_sigterm_handler.

[x] account_state.py — FAIT (item 9, 2026-07-03)
    — Defaut os.getenv("RUN_ENV", "local") -> "prod".
    — IS_LOCAL = RUN_ENV != "prod" (semantique conservee, valeur par defaut corrigee).
    — Pas d'import config.py (evite circular import).

[x] page_snapshot.py — FAIT (item 9, 2026-07-03)
    — 2 occurrences defaut "local" -> "prod" dans is_local.

[x] action_dispatcher.py — FAIT (item 9, 2026-07-03)
    — L5608 : defaut "local" -> "prod" dans is_local_env local.
    — L5888 : import is_local_env remplace par import is_attach_mode.

[x] survey_handler.py — FAIT (item 9, 2026-07-03)
    — IS_LOCAL declare mais jamais utilise : supprime.

[ ] account_state.py — EN ATTENTE (item 13)
    — Supprimer les variables et logiques liees a fivesim.
    — Ajouter "license_key" dans _default_state() pour la requete de supervision.
    — Auditer les autres variables d'etat devenues inutiles en bare-metal.

[ ] Scheduler Fly.io (scheduler/scheduler_fly.py et dependances) — EN ATTENTE (item 12)
    — Hors perimetre bare-metal. Archiver dans legacy/ ou supprimer.
    — Remplace par launch_all.ps1.

================================================================================
BASE DE DONNEES POSTGRES — ARCHITECTURE CENTRALISEE
================================================================================

**Decision : une seule instance Postgres centrale, controlee uniquement par
l'operateur. DATABASE_URL embarquee dans chaque compile PyInstaller.**

Hebergement recommande :
  - Fly.io Postgres existant (si deja en place) — option de continuite.
  - Neon.tech tier gratuit — Postgres serverless, compatible psycopg2,
    0.5 GB stockage, suffisant pour account_state + licenses sur 100+ bots.

Supervision :
  SELECT l.license_key, l.owner_label,
         l.total_payout_eur, l.max_payout_eur, l.is_active,
         COUNT(a.account_id) AS bots_actifs
  FROM licenses l
  LEFT JOIN account_state a
    ON a.state->>'license_key' = l.license_key
   AND a.updated_ts > NOW() - INTERVAL '5 minutes'
   AND a.state->>'status' = 'running'
  GROUP BY l.license_key, l.owner_label,
           l.total_payout_eur, l.max_payout_eur, l.is_active
  ORDER BY l.owner_label;
  Prerequis : "license_key" dans _default_state() (item 13).

TABLES :
[x] licenses — CREEE (item 1, 2026-07-02)
    Colonnes : license_key, owner_label, max_payout_eur, total_payout_eur,
               is_active, created_at.
    Contraintes CHECK : max_payout_eur >= 0, total_payout_eur >= 0.

[ ] chrome_profile_chunks — A SUPPRIMER via SQL :
      DROP TABLE IF EXISTS chrome_profile_chunks;

================================================================================
FICHIERS A CREER
================================================================================

[x] preselection/license_guard.py — FAIT (item 2, 2026-07-02)
    Verifie licence au demarrage (is_active, quota). Fail-closed si Postgres
    injoignable. No-op si LICENSE_KEY absente (mode dev/attach).
    Lit LICENSE_KEY et DATABASE_URL depuis _license_config.py (non versionne).

[ ] _license_config.py  (non versionne, dans .gitignore, un fichier par compile)
    Contenu :
      LICENSE_KEY = "<uuid>"
      DATABASE_URL = "postgres://..."
    A creer manuellement avant chaque build PyInstaller.

[ ] launch_all.ps1  (PowerShell, sur chaque mini-PC)  [item 10 — A FAIRE]
    Role : lancer uniquement les bots qui ne tournent pas deja.
    Logique :
      - Lire accounts.json.
      - Pour chaque bot :
        - pids\bot_<id>.pid existe + processus vivant -> skip.
        - pids\bot_<id>.pid existe + processus mort   -> supprimer PID + lancer.
        - pids\bot_<id>.pid absent                    -> lancer.
      - Chaque bot lance via Start-Process avec env vars :
        ACCOUNT_ID, EMAIL, PASSWORD, PROXY_URL, PROXY_USER, PROXY_PASS,
        CHROME_PROFILE_DIR, RUN_ENV=prod, GEO_LAT, GEO_LON, SURVEY_LANG, SURVEY_TZ.
        (LICENSE_KEY et DATABASE_URL embarquees dans le compile — ne pas passer en env.)
      - Planifiable via le Planificateur de taches Windows.

[ ] accounts.json  (sur chaque mini-PC, non versionne)
    [
      {
        "account_id": "bot_001",
        "email": "...",
        "password": "...",
        "proxy_url": "http://host:port",
        "proxy_user": "...",
        "proxy_pass": "...",
        "profile_dir": "C:\\surveybot\\profiles\\bot_001"
      }
    ]
    Note : LICENSE_KEY et DATABASE_URL absents — embarques dans le compile.

[ ] update_checker.py  [item 11 — A FAIRE]
    Point d'appel : launch.py, au retour au listing TopSurveys.
    Actif uniquement si UPDATE_CHECK_ENABLED=1.
    Logique : git fetch -> comparer HEAD vs origin/main -> si diff :
      git pull + supprimer PID + os.execv() pour se relancer.
    Si git inaccessible -> ignorer, reessayer au prochain cycle.

================================================================================
MODIFICATIONS DANS LES FICHIERS EXISTANTS
================================================================================

[x] Cash/payout.py — FAIT (item 4, 2026-07-02)
    _increment_license_payout() appele apres retrait confirme.
    UPDATE licenses SET total_payout_eur = total_payout_eur + <montant>
    Non bloquant. Actif uniquement si RUN_ENV=prod.

[x] preselection/playwright_launcher.py — FAIT (item 6, 2026-07-02)
    user_data_dir lu depuis CHROME_PROFILE_DIR (os.getenv).
    Fail-fast en prod si absent ou dossier inexistant.
    Fallback tempfile.mkdtemp() conserve pour mode attach uniquement.

[ ] update_checker.py a integrer dans launch.py — EN ATTENTE (item 11)

================================================================================
STRUCTURE DES DOSSIERS SUR CHAQUE MINI-PC
================================================================================

C:\surveybot\
  |-- surveybot.exe          <- compile PyInstaller (par licence)
  |-- launch_all.ps1         <- script de lancement selectif
  |-- accounts.json          <- credentials + config par bot (non versionne)
  |-- pids\
  |   |-- bot_001.pid
  |   `-- bot_002.pid
  `-- profiles\
      |-- bot_001\           <- user-data-dir Chrome (cree manuellement)
      |-- bot_002\
      `-- ...

================================================================================
ORDRE D'IMPLEMENTATION
================================================================================

[x] 1.  Creer la table `licenses` dans Postgres.
[x] 2.  Creer preselection/license_guard.py.
[x] 3.  Integrer license_guard dans main.py.
[x] 4.  Modifier Cash/payout.py pour incrementer total_payout_eur.
[x] 5.  Supprimer chrome_profile_store.py et ses references dans launch.py.
[x] 6.  Adapter playwright_launcher.py pour le profil local (CHROME_PROFILE_DIR).
[x] 7.  Ajouter ecriture/suppression du fichier PID dans launch.py.
[x] 8.  Ajouter handler SIGINT Windows dans launch.py + main.py.
[x] 9.  Nettoyer config.py + tous les consommateurs de IS_LOCAL / is_local_env.
[ ] 10. Creer launch_all.ps1.
[ ] 11. Creer update_checker.py + integrer dans launch.py.
[ ] 12. Archiver le scheduler Fly.io (dossier legacy/).
[ ] 13. Auditer account_state.py : supprimer vars fivesim + "license_key" dans
        _default_state() pour activer la requete de supervision.

================================================================================
POINTS EN SUSPENS (non bloquants pour les items 1-12)
================================================================================

[ ] Solution SMS pour verifications de compte : 5sim abandonne, alternative
    non finalisee. Impact sur fivesim_client.py et account_state.py.
    A traiter apres stabilisation du deploiement bare-metal.

[ ] Table chrome_profile_chunks a supprimer via SQL :
      DROP TABLE IF EXISTS chrome_profile_chunks;

================================================================================
STATUT
================================================================================

Items 1-9  : TERMINES (2026-07-02 / 2026-07-03).
Items 10-13 : EN ATTENTE.