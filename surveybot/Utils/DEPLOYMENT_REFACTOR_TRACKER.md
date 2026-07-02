# DEPLOYMENT_REFACTOR_TRACKER.md
# Suivi des modifications — Migration Fly.io → Bare-metal Windows
# Créé lors de la session de conception architecture (2026-07-02)
# À passer à Claude en contexte avant chaque session de travail.

================================================================================
DÉCISIONS D'ARCHITECTURE
================================================================================

**Contexte :**
Migration du déploiement Fly.io (VMs éphémères Linux) vers des mini-PCs Windows
bare-metal (NiPoGi, Ryzen 5 7430U, 32 GB RAM, 512 GB NVMe, Windows 11 Pro).
7 bots par machine. Chaque bot a un proxy ISP dédié et un profil Chrome local permanent.

**Principes retenus :**
- Profil Chrome créé manuellement une fois (avec le bon proxy), réutilisé en permanence.
- Pas de rotation de profil, pas de recréation automatique.
- Fail-closed : si le Postgres central est injoignable, le bot s'arrête.
- Mise à jour du code via git pull + redémarrage au retour au listing TopSurveys.
- Compilation PyInstaller : un binaire distinct par licencié, LICENSE_KEY et
  DATABASE_URL embarquées en dur — invisibles pour l'utilisateur final.
- Pas de compte backup.

================================================================================
ENVIRONNEMENTS — COMPORTEMENT ATTENDU
================================================================================

**Décision : deux environnements seulement, RUN_ENV=local supprimé.**
  - RUN_ENV=prod        → bots en production (fonctionnement autonome)
  - BROWSER_MODE=attach → debug ponctuel sur un survey précis (via main.py)
RUN_ENV=local et LOCAL_UNATTENDED sont a supprimr du projet.

Tableau de référence :

  Comportement                        | prod bare-metal | attach (debug)
  ------------------------------------|-----------------|---------------
  Pauses interactives (input())       |       NON       |     OUI
  should_pause_for_captcha()          |       NON       |     OUI
  RuntimeGuard activé                 |       OUI       |     NON
  Heartbeat Postgres                  |       OUI       |     NON
  Hot reload (hot_reload.py)          |       NON       |     NON
  Mise à jour code (git pull)         |       OUI       |     NON
  chrome_profile_store (load/save)    |    SUPPRIMÉ     |     NON
  Serveur HTTP debug local            |       NON       |     NON
  Écriture fichier PID                |       OUI       |     NON
  Vérification licence (license_guard)|       OUI       |     NON
  Handler arrêt propre                |  SIGINT Windows |   Ctrl+C

**Note sur SIGTERM :**
Le handler SIGTERM existant était conçu pour Fly.io (signal envoyé avant
destruction VM). Sur Windows bare-metal, SIGTERM n'est pas le signal natif.
→ Ajouter un handler SIGINT (Ctrl+C) avec le même comportement :
  libérer le slot Postgres + arrêt propre + suppression du fichier PID.
SIGTERM conservé en parallèle pour compatibilité.

**Note sur config.py :**
RUN_ENV=local et LOCAL_UNATTENDED sont supprimés du projet. config.py doit
être nettoyé de toutes les branches qui en dépendent (voir section SUPPRESSIONS).

================================================================================
FICHIERS À SUPPRIMER
================================================================================

[ ] preselection/chrome_profile_store.py
    — Obsolète : le profil Chrome vit en permanence sur le NVMe local.
      Plus aucun besoin de sérialiser/désérialiser vers Postgres.

================================================================================
SUPPRESSIONS DANS LES FICHIERS EXISTANTS
================================================================================

[ ] launch.py
    — Supprimer l'import de chrome_profile_store.
    — Supprimer les appels à load_profile() et save_profile().
    — Supprimer le démarrage du thread start_profile_autosave().
    — Supprimer toute référence à chrome_profile_chunks.
    — Ajouter un handler SIGINT/Ctrl+C Windows avec le même comportement
      que l'actuel handler SIGTERM : libérer slot Postgres + arrêt propre
      + suppression PID. Conserver SIGTERM en parallèle.

[ ] account_state.py
    — Supprimer les variables et logiques liées à fivesim
      (solution SMS non finalisée, à traiter séparément).
    — Auditer les autres variables d'état devenues inutiles en bare-metal
      (item 13 de l'ordre d'implémentation).

[ ] config.py
    — Supprimer RUN_ENV=local : toutes les branches conditionnées sur
      is_local_env() doivent être supprimées ou reconditionnées sur
      BROWSER_MODE=attach si elles sont utiles en mode debug.
    — Supprimer LOCAL_UNATTENDED et toutes ses branches associées.
    — Supprimer les fonctions devenues sans objet après suppression du
      mode local (is_local_env(), should_block_for_input(),
      should_run_hot_reload(), serveur HTTP debug, etc.).
    — Vérifier que les pauses interactives (input(), captcha) sont
      conditionnées sur is_attach_mode() avant suppression de is_local_env().
    — Conserver : is_attach_mode(), is_prod_like(), should_run_guard_monitor(),
      should_run_heartbeat(), should_pause_for_captcha() (reconditionné sur
      attach), get_captcha_behavior(), log_config_summary().

[ ] Scheduler Fly.io (scheduler/scheduler_fly.py et dépendances)
    — Hors périmètre bare-metal. Archiver dans un dossier legacy/ ou supprimer.
    — Remplacé par launch_all.ps1.

================================================================================
BASE DE DONNÉES POSTGRES — ARCHITECTURE CENTRALISÉE
================================================================================

**Décision : une seule instance Postgres centrale, contrôlée uniquement par
l'opérateur. DATABASE_URL embarquée dans chaque compilé PyInstaller.
Les utilisateurs finaux n'ont aucun accès à la BD et ne connaissent pas
son adresse.**

Hébergement recommandé :
  - Fly.io Postgres existant (si déjà en place) — option de continuité.
  - Neon.tech tier gratuit — Postgres serverless, compatible psycopg2,
    0.5 GB stockage, suffisant pour account_state + licenses sur 100+ bots.

Supervision : requêtes SQL directes depuis l'opérateur. Les heartbeats des
bots (toutes les 60s, last_seen_at dans account_state) donnent une vue
temps réel du nombre de bots actifs par license_key sans update dédié.

TABLE À SUPPRIMER :
[ ] chrome_profile_chunks — plus aucun usage en bare-metal.

TABLE À CRÉER :
[ ] licenses
    Colonnes :
      license_key       TEXT PRIMARY KEY     -- UUID embarqué dans le compilé
      owner_label       TEXT                 -- label lisible ("ami_pierre")
      max_payout_eur    FLOAT                -- quota max, fixé par l'opérateur
      total_payout_eur  FLOAT DEFAULT 0      -- cumul des retraits réels détectés
      is_active         BOOLEAN DEFAULT TRUE -- kill switch manuel
      created_at        TIMESTAMPTZ DEFAULT NOW()

    Règles :
      - max_payout_eur : modifiable à tout moment par l'opérateur seul.
      - total_payout_eur : incrémenté par le bot à chaque retrait confirmé.
        UPDATE licenses SET total_payout_eur = total_payout_eur + <montant>
        WHERE license_key = <clé>
      - Quota partagé entre tous les bots utilisant la même license_key,
        peu importe le nombre de machines ou de redistributions.
      - Supervision du nombre de bots actifs par licencié : via les heartbeats
        de account_state filtrés sur license_key + last_seen_at < NOW() - 5min.

================================================================================
FICHIERS À CRÉER
================================================================================

[ ] launch_all.ps1  (PowerShell, sur chaque mini-PC)
    Rôle : lancer uniquement les bots qui ne tournent pas déjà.
    Logique :
      - Lire accounts.json (liste des bots de la machine).
      - Pour chaque bot :
        - Vérifier si pids\bot_<id>.pid existe.
          - OUI + processus vivant (tasklist) → skip.
          - OUI + processus mort (PID stale) → supprimer PID + lancer.
          - NON → lancer.
      - Chaque bot lancé via Start-Process en processus indépendant,
        avec variables d'env : ACCOUNT_ID, EMAIL, PASSWORD, PROXY_URL,
        PROXY_USER, PROXY_PASS, CHROME_PROFILE_DIR, RUN_ENV=prod,
        GEO_LAT, GEO_LON, SURVEY_LANG, SURVEY_TZ.
        (LICENSE_KEY et DATABASE_URL sont embarquées dans le compilé —
        ne pas les passer en variable d'env.)
      - Peut être planifié via le Planificateur de tâches Windows pour
        s'exécuter au démarrage de la machine et toutes les N minutes.

[ ] accounts.json  (sur chaque mini-PC, non versionné, non inclus dans le repo)
    [
      {
        "account_id": "bot_001",
        "email": "...",
        "password": "...",
        "proxy_url": "http://host:port",
        "proxy_user": "...",
        "proxy_pass": "...",
        "profile_dir": "C:\\surveybot\\profiles\\bot_001",
      }
    ]
    Note : LICENSE_KEY et DATABASE_URL absents — embarqués dans le compilé.

[ ] preselection/license_guard.py  (nouveau module)
    Rôle : vérification du quota de licence au démarrage du bot.
    Logique :
      - Lire LICENSE_KEY depuis une constante embarquée à la compilation
        (définie dans un fichier _license_config.py non versionné, inclus
        dans le build PyInstaller).
      - Connexion au Postgres central via DATABASE_URL embarquée.
      - SELECT max_payout_eur, total_payout_eur, is_active
        FROM licenses WHERE license_key = <clé>.
      - Si connexion impossible → log erreur + SystemExit (fail-closed).
      - Si is_active = false → log + SystemExit.
      - Si total_payout_eur >= max_payout_eur → log + SystemExit.
      - Sinon → OK, le bot continue.
    Appelé en tout premier dans main(), uniquement si RUN_ENV=prod.

================================================================================
MODIFICATIONS DANS LES FICHIERS EXISTANTS
================================================================================

[ ] Cash/payout.py
    — Après chaque retrait réel confirmé :
      UPDATE licenses SET total_payout_eur = total_payout_eur + <montant>
      WHERE license_key = <clé>
    — Clé lue depuis la même constante que license_guard.py.
    — Échec de l'UPDATE → loggué, non bloquant (retrait déjà effectué).
    — Actif uniquement si RUN_ENV=prod.

[ ] main.py
    — Appeler license_guard.check_license_or_exit() en tout premier,
      avant toute autre initialisation, uniquement si RUN_ENV=prod.

[ ] launch.py
    — Lire CHROME_PROFILE_DIR depuis os.getenv("CHROME_PROFILE_DIR").
    — Supprimer chrome_profile_store (imports + appels).
    — Écrire pids\bot_<account_id>.pid au démarrage.
    — Supprimer pids\bot_<account_id>.pid à l'arrêt propre
      (handler SIGINT + bloc finally).
    — Ajouter handler SIGINT Windows (voir section ENVIRONNEMENTS).

[ ] preselection/playwright_launcher.py
    — Lire user_data_dir depuis os.getenv("CHROME_PROFILE_DIR").
    — Si absent ou dossier inexistant → log erreur + SystemExit.
    — Supprimer toute logique de création ou restauration de profil.

================================================================================
LOGIQUE DE MISE À JOUR DU CODE
================================================================================

[ ] Nouveau module : update_checker.py
    Point d'appel : launch.py, au retour au listing TopSurveys.
    Logique :
      - Actif uniquement si UPDATE_CHECK_ENABLED=1.
      - git fetch origin (silencieux, timeout court).
      - Comparer git rev-parse HEAD vs git rev-parse origin/main.
      - Si identiques → rien à faire.
      - Si différents :
          1. Terminer proprement le cycle en cours.
          2. git pull origin main.
          3. Supprimer le fichier PID courant.
          4. Se relancer via os.execv() avec les mêmes arguments/env.
      - Si git inaccessible → ignorer, réessayer au prochain cycle.
    Prérequis sur chaque mini-PC :
      - Git installé.
      - Credentials GitHub configurés (token en variable d'env GIT_TOKEN
        ou dans les credentials Windows Git).
      - Code source présent (repo cloné sur la machine).

================================================================================
STRUCTURE DES DOSSIERS SUR CHAQUE MINI-PC
================================================================================

C:\surveybot\
  ├── surveybot.exe          ← compilé PyInstaller (par licencié, LICENSE_KEY
  │                            et DATABASE_URL embarquées en dur)
  ├── launch_all.ps1         ← script de lancement sélectif
  ├── accounts.json          ← credentials + config par bot (non versionné)
  ├── pids\
  │   ├── bot_001.pid
  │   └── bot_002.pid
  └── profiles\
      ├── bot_001\           ← user-data-dir Chrome (créé manuellement)
      ├── bot_002\
      └── ...

Si UPDATE_CHECK_ENABLED=1, le repo git est également présent sur la machine.

================================================================================
ORDRE D'IMPLÉMENTATION RECOMMANDÉ
================================================================================

1.  Créer la table `licenses` dans Postgres (script SQL).
2.  Créer preselection/license_guard.py.
3.  Intégrer license_guard dans main.py.
4.  Modifier Cash/payout.py pour incrémenter total_payout_eur.
5.  Supprimer chrome_profile_store.py et ses références dans launch.py.
6.  Adapter playwright_launcher.py pour le profil local (CHROME_PROFILE_DIR).
7.  Ajouter écriture/suppression du fichier PID dans launch.py.
8.  Ajouter handler SIGINT Windows dans launch.py.
9.  Mettre à jour config.py (commentaires + confirmation branches prod).
10. Créer launch_all.ps1.
11. Créer update_checker.py + intégrer dans launch.py.
12. Archiver le scheduler Fly.io (dossier legacy/).
13. Auditer account_state.py : supprimer vars fivesim + nettoyage
    LOCAL_UNATTENDED + autres obsolètes bare-metal.

================================================================================
POINTS EN SUSPENS (non bloquants pour les items 1–10)
================================================================================

[ ] Solution SMS pour vérifications de compte : 5sim abandonné, alternative
    non finalisée. Impact sur fivesim_client.py et account_state.py.
    À traiter après stabilisation du déploiement bare-metal.

================================================================================
STATUT
================================================================================

Tous les items ci-dessus sont EN ATTENTE d'implémentation.
Session de conception : 2026-07-02.
Aucun fichier modifié à ce stade.