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

**Decision : deux environnements seulement, RUN_ENV=local supprime.**
  - RUN_ENV=prod        -> bots en production (fonctionnement autonome)
  - BROWSER_MODE=attach -> debug ponctuel sur un survey precis (via main.py)
RUN_ENV=local et LOCAL_UNATTENDED sont a supprimer du projet (item 9).

Tableau de reference :

  Comportement                        | prod bare-metal | attach (debug)
  ------------------------------------|-----------------|---------------
  Pauses interactives (input())       |       NON       |     OUI
  should_pause_for_captcha()          |       NON       |     OUI
  RuntimeGuard active                 |       OUI       |     NON
  Heartbeat Postgres                  |       OUI       |     NON
  Hot reload (hot_reload.py)          |       NON       |     NON
  Mise a jour code (git pull)         |       OUI       |     NON
  chrome_profile_store (load/save)    |    SUPPRIME     |     NON
  Serveur HTTP debug local            |       NON       |     NON
  Ecriture fichier PID                |       OUI       |     NON
  Verification licence (license_guard)|       OUI       |     NON
  Handler arret propre                |  SIGINT Windows |   Ctrl+C

**Note sur SIGTERM :**
Le handler SIGTERM existant etait concu pour Fly.io. Sur Windows bare-metal,
SIGTERM n'est pas le signal natif.
-> Handler SIGINT (Ctrl+C) ajoute avec le meme comportement :
   liberer le slot Postgres + arret propre + suppression du fichier PID.
SIGTERM conserve en parallele pour compatibilite. [FAIT item 8]

**Note sur config.py :**
RUN_ENV=local et LOCAL_UNATTENDED sont supprimes du projet. config.py doit
etre nettoye de toutes les branches qui en dependent (voir item 9).

================================================================================
FICHIERS A SUPPRIMER
================================================================================

[x] preselection/chrome_profile_store.py — FAIT (supprime manuellement)

================================================================================
SUPPRESSIONS DANS LES FICHIERS EXISTANTS
================================================================================

[x] launch.py
    — Import chrome_profile_store supprime.
    — Appels load_profile() / save_profile() / start_profile_autosave() supprimes.
    — References chrome_profile_chunks supprimees.
    — Handler SIGINT/Ctrl+C ajoute (install_sigint_handler) — meme comportement
      que SIGTERM : liberer slot Postgres + supprimer PID + arreter heartbeat.
    — SIGTERM conserve en parallele. [FAIT items 7 & 8]

[ ] account_state.py
    — Supprimer les variables et logiques liees a fivesim
      (solution SMS non finalisee, a traiter separement).
    — Auditer les autres variables d'etat devenues inutiles en bare-metal
      (item 13 de l'ordre d'implementation).

[ ] config.py  [item 9]
    — Supprimer RUN_ENV=local : toutes les branches conditionnees sur
      is_local_env() doivent etre supprimees ou reconditionnees sur
      BROWSER_MODE=attach si elles sont utiles en mode debug.
    — Supprimer LOCAL_UNATTENDED et toutes ses branches associees.
    — Supprimer les fonctions devenues sans objet apres suppression du
      mode local (is_local_env(), should_block_for_input(),
      should_run_hot_reload(), serveur HTTP debug, etc.).
    — Verifier que les pauses interactives (input(), captcha) sont
      conditionnees sur is_attach_mode() avant suppression de is_local_env().
    — Conserver : is_attach_mode(), is_prod_like(), should_run_guard_monitor(),
      should_run_heartbeat(), should_pause_for_captcha() (reconditionne sur
      attach), get_captcha_behavior(), log_config_summary().

[ ] Scheduler Fly.io (scheduler/scheduler_fly.py et dependances)  [item 12]
    — Hors perimetre bare-metal. Archiver dans un dossier legacy/ ou supprimer.
    — Remplace par launch_all.ps1.

================================================================================
BASE DE DONNEES POSTGRES — ARCHITECTURE CENTRALISEE
================================================================================

**Decision : une seule instance Postgres centrale, controlee uniquement par
l'operateur. DATABASE_URL embarquee dans chaque compile PyInstaller.
Les utilisateurs finaux n'ont aucun acces a la BD.**

Hebergement recommande :
  - Fly.io Postgres existant (si deja en place) — option de continuite.
  - Neon.tech tier gratuit — Postgres serverless, compatible psycopg2,
    0.5 GB stockage, suffisant pour account_state + licenses sur 100+ bots.

Supervision : requetes SQL directes depuis l'operateur. Les heartbeats des
bots (toutes les 60s, updated_ts dans account_state) donnent une vue
temps reel du nombre de bots actifs par license_key.

  Requete de supervision recommandee :
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
  Prerequis : ajouter "license_key" dans _default_state() (item 13).

TABLE A SUPPRIMER :
[ ] chrome_profile_chunks — plus aucun usage en bare-metal. A supprimer via SQL.

TABLE CREEE :
[x] licenses — FAIT (item 1, 2026-07-02)
    Colonnes : license_key, owner_label, max_payout_eur, total_payout_eur,
               is_active, created_at.
    Contraintes CHECK : max_payout_eur >= 0, total_payout_eur >= 0.

================================================================================
FICHIERS A CREER
================================================================================

[x] preselection/license_guard.py — FAIT (item 2, 2026-07-02)
    Verifie licence au demarrage (is_active, quota). Fail-closed si Postgres
    injoignable. No-op si LICENSE_KEY absente (mode dev/attach).
    Lit LICENSE_KEY et DATABASE_URL depuis _license_config.py (non versionne).

[ ] _license_config.py  (non versionne, .gitignore, un fichier par compile)
    Contenu :
      LICENSE_KEY = "<uuid>"
      DATABASE_URL = "postgres://..."
    A creer manuellement avant chaque build PyInstaller.

[ ] launch_all.ps1  (PowerShell, sur chaque mini-PC)  [item 10]
    Role : lancer uniquement les bots qui ne tournent pas deja.
    Logique :
      - Lire accounts.json (liste des bots de la machine).
      - Pour chaque bot :
        - Verifier si pids\bot_<id>.pid existe.
          - OUI + processus vivant (tasklist) -> skip.
          - OUI + processus mort (PID stale) -> supprimer PID + lancer.
          - NON -> lancer.
      - Chaque bot lance via Start-Process en processus independant,
        avec variables d'env : ACCOUNT_ID, EMAIL, PASSWORD, PROXY_URL,
        PROXY_USER, PROXY_PASS, CHROME_PROFILE_DIR, RUN_ENV=prod,
        GEO_LAT, GEO_LON, SURVEY_LANG, SURVEY_TZ.
        (LICENSE_KEY et DATABASE_URL embarquees dans le compile.)
      - Peut etre planifie via le Planificateur de taches Windows.

[ ] accounts.json  (sur chaque mini-PC, non versionne, non inclus dans le repo)
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

[ ] update_checker.py  [item 11]
    Point d'appel : launch.py, au retour au listing TopSurveys.
    Logique :
      - Actif uniquement si UPDATE_CHECK_ENABLED=1.
      - git fetch origin (silencieux, timeout court).
      - Comparer git rev-parse HEAD vs git rev-parse origin/main.
      - Si identiques -> rien a faire.
      - Si differents :
          1. Terminer proprement le cycle en cours.
          2. git pull origin main.
          3. Supprimer le fichier PID courant.
          4. Se relancer via os.execv() avec les memes arguments/env.
      - Si git inaccessible -> ignorer, reessayer au prochain cycle.

================================================================================
MODIFICATIONS DANS LES FICHIERS EXISTANTS
================================================================================

[x] Cash/payout.py — FAIT (item 4, 2026-07-02)
    Apres chaque retrait reel confirme, appelle _increment_license_payout()
    qui execute :
      UPDATE licenses SET total_payout_eur = total_payout_eur + <montant>
      WHERE license_key = <cle>
    Non bloquant. Actif uniquement si RUN_ENV=prod.

[x] main.py — FAIT (items 3 & 8, 2026-07-02)
    - check_license_or_exit() appele en tout premier (lignes 5-7).
    - install_sigint_handler(account_id) appele apres install_sigterm_handler.
    - import install_sigint_handler depuis launch ajoute.

[x] launch.py — FAIT (items 7 & 8, 2026-07-02)
    - Helpers PID : _pid_path(), write_pid_file(), delete_pid_file().
    - write_pid_file() appele dans mark_bot_running().
    - delete_pid_file() appele dans _make_stop_handler() (SIGTERM/SIGINT)
      et dans launch_driver_or_fail() avant SystemExit.
    - install_sigint_handler() ajoute (delegue a _make_stop_handler).
    - _make_sigterm_handler() remplace par _make_stop_handler(sig_name=...).
      SIGTERM et SIGINT partagent le meme handler.

[x] preselection/playwright_launcher.py — FAIT (item 6, 2026-07-02)
    - user_data_dir lu depuis CHROME_PROFILE_DIR (os.getenv).
    - Fail-fast en prod si absent ou dossier inexistant (SystemExit).
    - Fallback tempfile.mkdtemp() conserve pour mode local/attach uniquement.
    - launch_browser_playwright_debug() inchange (profil jetable intentionnel).

[ ] config.py  [item 9 — A FAIRE]
    Voir section SUPPRESSIONS ci-dessus.

================================================================================
LOGIQUE DE MISE A JOUR DU CODE
================================================================================

[ ] update_checker.py  [item 11 — A FAIRE]
    Voir section FICHIERS A CREER ci-dessus.

================================================================================
STRUCTURE DES DOSSIERS SUR CHAQUE MINI-PC
================================================================================

C:\surveybot\
  |-- surveybot.exe          <- compile PyInstaller (par licence, LICENSE_KEY
  |                             et DATABASE_URL embarquees en dur)
  |-- launch_all.ps1         <- script de lancement selectif
  |-- accounts.json          <- credentials + config par bot (non versionne)
  |-- pids\
  |   |-- bot_001.pid
  |   `-- bot_002.pid
  `-- profiles\
      |-- bot_001\           <- user-data-dir Chrome (cree manuellement)
      |-- bot_002\
      `-- ...

Si UPDATE_CHECK_ENABLED=1, le repo git est egalement present sur la machine.

================================================================================
ORDRE D'IMPLEMENTATION
================================================================================

[x] 1.  Creer la table `licenses` dans Postgres (script SQL).
[x] 2.  Creer preselection/license_guard.py.
[x] 3.  Integrer license_guard dans main.py.
[x] 4.  Modifier Cash/payout.py pour incrementer total_payout_eur.
[x] 5.  Supprimer chrome_profile_store.py et ses references dans launch.py.
[x] 6.  Adapter playwright_launcher.py pour le profil local (CHROME_PROFILE_DIR).
[x] 7.  Ajouter ecriture/suppression du fichier PID dans launch.py.
[x] 8.  Ajouter handler SIGINT Windows dans launch.py + main.py.
[ ] 9.  Nettoyer config.py (supprimer RUN_ENV=local, LOCAL_UNATTENDED, branches mortes).
[ ] 10. Creer launch_all.ps1.
[ ] 11. Creer update_checker.py + integrer dans launch.py.
[ ] 12. Archiver le scheduler Fly.io (dossier legacy/).
[ ] 13. Auditer account_state.py : supprimer vars fivesim + "license_key" dans
        _default_state() pour activer la requete de supervision.

================================================================================
POINTS EN SUSPENS (non bloquants pour les items 1-10)
================================================================================

[ ] Solution SMS pour verifications de compte : 5sim abandonne, alternative
    non finalisee. Impact sur fivesim_client.py et account_state.py.
    A traiter apres stabilisation du deploiement bare-metal.

[ ] Table chrome_profile_chunks a supprimer via SQL :
      DROP TABLE IF EXISTS chrome_profile_chunks;

================================================================================
STATUT
================================================================================

Items 1-8 : TERMINES (2026-07-02).
Items 9-13 : EN ATTENTE.