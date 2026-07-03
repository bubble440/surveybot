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

================================================================================
FICHIERS SUPPRIMES
================================================================================

[x] preselection/chrome_profile_store.py — FAIT (supprime manuellement)

================================================================================
SUPPRESSIONS / MODIFICATIONS DANS LES FICHIERS EXISTANTS
================================================================================

[x] launch.py — FAIT (items 5, 7, 8, 11)
    — chrome_profile_store supprime.
    — Helpers PID : _pid_path(), write_pid_file(), delete_pid_file().
    — install_sigint_handler() + _make_stop_handler() unifie SIGTERM/SIGINT.
    — check_and_apply(account_id) appele dans run_main_loop() apres run_survey().

[x] config.py — FAIT (item 9)
    — Pivot unique : is_attach_mode() = (BROWSER_MODE == "attach").
    — is_local_env(), LOCAL_UNATTENDED, IS_LOCAL, should_run_hot_reload (reconditionne)
      supprimes ou reconditionnees.
    — should_pause_before_cta() conservee (outil debug attach, LOCAL_CTA_REQUIRE_ENTER).
    — RUN_MODE conserve comme constante no-op pour compatibilite d'import.

[x] main.py — FAIT (items 3, 8, 9)
    — IS_LOCAL supprime, remplace par is_attach_mode() / not is_attach_mode().
    — check_license_or_exit() en tout premier.
    — install_sigint_handler() ajoute.

[x] account_state.py — FAIT (items 9, 13)
    — Defaut RUN_ENV corrige : "local" -> "prod".
    — IS_LOCAL = RUN_ENV != "prod".
    — fivesim_phone et fivesim_order_id supprimes de _default_state().
    — license_key ajoute dans _default_state() via _get_license_key()
      (lit _license_config.LICENSE_KEY, vide si absent).
    — _get_license_key() ajoute comme helper prive.

[x] page_snapshot.py — FAIT (item 9)
    — 2 occurrences defaut "local" -> "prod".

[x] action_dispatcher.py — FAIT (item 9)
    — Defaut "local" -> "prod" (variable locale is_local_env L5608).
    — import is_local_env remplace par import is_attach_mode (L5888).

[x] survey_handler.py — FAIT (item 9)
    — IS_LOCAL declare mais jamais utilise : supprime.

[x] preselection/playwright_launcher.py — FAIT (item 6)
    — user_data_dir lu depuis CHROME_PROFILE_DIR.
    — Fail-fast en prod si absent ou dossier inexistant.

[x] Cash/payout.py — FAIT (item 4)
    — _increment_license_payout() appele apres retrait confirme.

================================================================================
FICHIERS CREES
================================================================================

[x] preselection/license_guard.py — FAIT (item 2)
[x] update_checker.py — FAIT (item 11)
    check_and_apply(account_id) : git fetch -> compare HEAD/origin/main ->
    si diff : git pull + delete_pid + os.execv(). No-op si UPDATE_CHECK_ENABLED!=1
    ou git inaccessible. Ne bloque jamais le bot.

[x] launch_all.ps1 — FAIT (item 10)
    Lit accounts.json, detecte PID stale via tasklist, lance les bots manquants
    via System.Diagnostics.Process (sans fenetre console), redirige stdout/stderr
    vers logs\bot_<id>.log.
    Planifier via Planificateur de taches Windows :
      powershell.exe -ExecutionPolicy Bypass -File "C:\surveybot\launch_all.ps1"
      Declencheur : demarrage + repetition toutes les 5 min.

[ ] _license_config.py (non versionne, dans .gitignore, un fichier par compile)
    Contenu :
      LICENSE_KEY = "<uuid>"
      DATABASE_URL = "postgres://..."
    A creer manuellement avant chaque build PyInstaller.

[ ] accounts.json (sur chaque mini-PC, non versionne)
    Voir structure ci-dessous.

================================================================================
BASE DE DONNEES POSTGRES
================================================================================

[x] licenses — CREEE (item 1)
    license_key, owner_label, max_payout_eur, total_payout_eur, is_active, created_at.

[ ] chrome_profile_chunks — A SUPPRIMER :
      DROP TABLE IF EXISTS chrome_profile_chunks;

Requete de supervision (prerequis : license_key dans _default_state — FAIT item 13) :
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

================================================================================
ARCHIVAGE SCHEDULER FLY.IO
================================================================================

[x] Item 12 — FAIT : commandes git a executer une fois sur le repo :

  git mv scheduler/ legacy/scheduler/
  git commit -m "chore: archive Fly.io scheduler (remplace par launch_all.ps1)"

  Ou, si suppression directe preferee :
  git rm -r scheduler/
  git commit -m "chore: remove Fly.io scheduler (remplace par launch_all.ps1)"

================================================================================
STRUCTURE DES DOSSIERS SUR CHAQUE MINI-PC
================================================================================

C:\surveybot\
  |-- surveybot.exe          <- compile PyInstaller (par licence)
  |-- launch_all.ps1
  |-- accounts.json          <- non versionne
  |-- pids\
  |   |-- bot_001.pid
  |   `-- bot_002.pid
  |-- logs\
  |   |-- bot_001.log
  |   `-- launch_all.log
  `-- profiles\
      |-- bot_001\           <- user-data-dir Chrome (cree manuellement)
      `-- bot_002\

Format accounts.json :
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
  Champs optionnels (defauts : Paris/fr-FR) : geo_lat, geo_lon, survey_lang, survey_tz.

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
[x] 10. Creer launch_all.ps1.
[x] 11. Creer update_checker.py + integrer dans launch.py.
[x] 12. Archiver le scheduler Fly.io (commandes git ci-dessus).
[x] 13. Auditer account_state.py : supprimer vars fivesim + license_key dans
        _default_state().

================================================================================
POINTS EN SUSPENS
================================================================================

[ ] Solution SMS pour verifications de compte : 5sim abandonne, alternative
    non finalisee. Impact sur fivesim_client.py.
    A traiter apres stabilisation du deploiement bare-metal.

[ ] Table chrome_profile_chunks a supprimer via SQL.

[ ] _license_config.py a creer manuellement avant chaque build PyInstaller.

[ ] accounts.json a creer sur chaque mini-PC (non versionne).

================================================================================
STATUT
================================================================================

Items 1-13 : TOUS TERMINES (2026-07-02 / 2026-07-03).
Refactoring bare-metal complet.