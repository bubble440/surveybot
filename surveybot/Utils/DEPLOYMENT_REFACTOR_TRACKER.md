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
- Compilation PyInstaller : un binaire distinct par licencié, license_key embarquée en dur.

================================================================================
ENVIRONNEMENTS — COMPORTEMENT ATTENDU
================================================================================

**Décision : RUN_ENV=prod couvre désormais bare-metal Windows ET Fly.io.**
Aucune nouvelle valeur d'environnement n'est créée. Le comportement prod est
"production, quelle que soit l'infrastructure".

Tableau de référence :

  Comportement                        | local | local+UNATTENDED | prod (bare-metal + Fly.io)
  ------------------------------------|-------|------------------|---------------------------
  Pauses interactives (input())       |  OUI  |      NON         |  NON
  should_pause_for_captcha()          |  OUI  |      NON         |  NON
  RuntimeGuard activé                 |  NON  |      OUI         |  OUI
  Heartbeat Postgres                  |  NON  |      OUI         |  OUI
  Hot reload (hot_reload.py)          |  OUI  |      NON         |  NON
  Mise à jour code (git pull)         |  NON  |      NON         |  OUI (UPDATE_CHECK_ENABLED=1)
  chrome_profile_store (load/save)    |  NON  |      NON         |  SUPPRIMÉ
  Serveur HTTP debug local            |  OUI  |      NON         |  NON
  Écriture fichier PID                |  NON  |      NON         |  OUI
  Vérification licence (license_guard)|  NON  |      NON         |  OUI
  Handler arrêt propre                | Ctrl+C|     Ctrl+C       |  SIGINT/Ctrl+C (Windows)

**Note sur SIGTERM :**
Le handler SIGTERM existant était conçu pour Fly.io (signal envoyé avant destruction VM).
Sur Windows bare-metal, SIGTERM n'est pas le signal natif d'arrêt.
→ Remplacer par un handler SIGINT (Ctrl+C) avec le même comportement :
  libérer le slot Postgres + arrêt propre + suppression du fichier PID.
SIGTERM peut être conservé en parallèle pour compatibilité, mais SIGINT devient
le signal principal sur Windows.

**Note sur config.py :**
Documenter explicitement dans config.py que RUN_ENV=prod couvre bare-metal Windows
et Fly.io. Aucune branche conditionnelle supplémentaire à créer.

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
    — Remplacer le handler SIGTERM Fly.io par un handler SIGINT/Ctrl+C Windows
      avec le même comportement : libérer slot Postgres + arrêt propre + suppression PID.
      (Conserver SIGTERM en parallèle pour compatibilité minimale.)

[ ] account_state.py
    — Supprimer les variables et logiques liées à fivesim
      (remplacé par eSIM ou solution SMS alternative).
    — Identifier et supprimer toute variable d'état devenue inutile
      dans le contexte bare-metal (à auditer lors de l'implémentation).

[ ] Scheduler Fly.io (scheduler/scheduler_fly.py et dépendances)
    — Hors périmètre bare-metal. À archiver ou supprimer selon décision.
    — Remplacé par launch_all.ps1 (voir FICHIERS À CRÉER).

[ ] config.py
    — Ajouter un commentaire explicite : RUN_ENV=prod couvre désormais
      bare-metal Windows et Fly.io. Aucune nouvelle valeur à créer.
    — Vérifier que should_run_hot_reload() retourne False en prod (déjà le cas,
      à confirmer).
    — Vérifier que should_run_guard_monitor() et should_run_heartbeat()
      retournent True en prod (déjà le cas, à confirmer).

================================================================================
BASE DE DONNÉES POSTGRES (instance centrale, contrôlée par l'opérateur)
================================================================================

TABLE À SUPPRIMER :
[ ] chrome_profile_chunks
    — Plus aucun usage en bare-metal.

TABLE À CRÉER :
[ ] licenses
    Colonnes :
      license_key       TEXT PRIMARY KEY     -- UUID embarqué dans le compilé
      owner_label       TEXT                 -- label lisible ("ami_pierre")
      max_payout_eur    FLOAT                -- quota max fixé par l'opérateur
      total_payout_eur  FLOAT DEFAULT 0      -- cumul des retraits réels détectés
      is_active         BOOLEAN DEFAULT TRUE -- kill switch manuel
      created_at        TIMESTAMPTZ DEFAULT NOW()

    Règles :
      - max_payout_eur : modifiable à tout moment par l'opérateur seul.
      - total_payout_eur : incrémenté par le bot à chaque retrait détecté
        via UPDATE licenses SET total_payout_eur = total_payout_eur + <montant>
        WHERE license_key = <clé>.
      - Quota partagé entre tous les bots utilisant la même license_key,
        peu importe le nombre de machines ou de redistributions.

================================================================================
FICHIERS À CRÉER
================================================================================

[ ] launch_all.ps1  (PowerShell, sur chaque mini-PC)
    Rôle : lancer uniquement les bots qui ne tournent pas déjà.
    Logique :
      - Lire accounts.json (liste des bots de la machine).
      - Pour chaque bot : vérifier si un fichier pids\bot_<id>.pid existe.
        - Si oui : vérifier si le processus Windows avec ce PID est vivant (tasklist).
          - Vivant → skip (le bot tourne déjà).
          - Mort (PID stale) → supprimer le fichier PID et lancer le bot.
        - Si non → lancer le bot.
      - Chaque bot est lancé dans un processus indépendant (Start-Process)
        avec ses variables d'env (ACCOUNT_ID, EMAIL, PASSWORD, PROXY_URL,
        CHROME_PROFILE_DIR, RUN_ENV=prod, LICENSE_KEY, etc.).
      - Un bot stoppé manuellement sera relancé au prochain appel de launch_all.ps1.
      - launch_all.ps1 peut être planifié via le Planificateur de tâches Windows
        pour s'exécuter automatiquement au démarrage de la machine.

[ ] accounts.json  (sur chaque mini-PC, non versionné)
    Structure par bot :
    [
      {
        "account_id": "bot_001",
        "email": "...",
        "password": "...",
        "proxy_url": "http://host:port",
        "proxy_user": "...",
        "proxy_pass": "...",
        "profile_dir": "C:\\surveybot\\profiles\\bot_001",
        "geo_lat": "48.8566",
        "geo_lon": "2.3522",
        "survey_lang": "fr-FR",
        "survey_tz": "Europe/Paris"
      }
    ]
    Note : LICENSE_KEY n'apparaît pas ici — elle est embarquée dans le compilé.

[ ] preselection/license_guard.py  (nouveau module)
    Rôle : vérification du quota de licence au démarrage du bot.
    Logique :
      - Lire LICENSE_KEY depuis une constante embarquée à la compilation.
      - Connexion au Postgres central (DATABASE_URL en variable d'env).
      - SELECT max_payout_eur, total_payout_eur, is_active
        FROM licenses WHERE license_key = <clé>.
      - Si connexion impossible → log erreur + SystemExit (fail-closed).
      - Si is_active = false → log + SystemExit.
      - Si total_payout_eur >= max_payout_eur → log + SystemExit.
      - Sinon → OK, le bot continue.
    Appelé : au tout début de main() avant toute autre initialisation.
    Actif uniquement si RUN_ENV=prod (pas de vérification en dev local).

================================================================================
MODIFICATIONS DANS LES FICHIERS EXISTANTS
================================================================================

[ ] Cash/payout.py
    — Après chaque retrait réel détecté et confirmé, exécuter :
      UPDATE licenses SET total_payout_eur = total_payout_eur + <montant>
      WHERE license_key = <clé>
    — La clé est lue depuis la même constante embarquée que license_guard.py.
    — Échec de l'UPDATE → loggué mais non bloquant
      (le retrait a déjà eu lieu côté TopSurveys, on ne peut pas l'annuler).
    — Actif uniquement si RUN_ENV=prod.

[ ] main.py
    — Appeler license_guard.check_license_or_exit() en tout premier,
      avant launch_driver_or_fail() et toute autre initialisation.
    — Actif uniquement si RUN_ENV=prod.

[ ] launch.py
    — Adapter launch_driver_or_fail() pour lire CHROME_PROFILE_DIR
      depuis les variables d'env (fourni par launch_all.ps1).
    — Supprimer toute logique spécifique Fly.io découplable
      (machines éphémères, comportements liés à --rm, etc.).
    — Écrire le fichier pids\bot_<account_id>.pid au démarrage du processus.
    — Supprimer le fichier pids\bot_<account_id>.pid à l'arrêt propre
      (handler SIGINT + finally du main).
    — Remplacer handler SIGTERM Fly.io par handler SIGINT Windows
      (voir section ENVIRONNEMENTS ci-dessus).

[ ] preselection/playwright_launcher.py
    — Lire CHROME_PROFILE_DIR depuis os.getenv("CHROME_PROFILE_DIR").
    — Si la variable est absente ou le dossier inexistant →
      log erreur + SystemExit (le profil doit être créé manuellement).
    — Supprimer toute logique de création ou restauration de profil.

================================================================================
LOGIQUE DE MISE À JOUR DU CODE
================================================================================

[ ] À implémenter dans launch.py (point d'appel) + module dédié update_checker.py
    Logique :
      - Activé uniquement si UPDATE_CHECK_ENABLED=1 (variable d'env).
      - Déclenché au retour au listing TopSurveys (entre deux surveys).
      - Exécuter : git fetch origin (silencieux).
      - Comparer le hash local (git rev-parse HEAD) vs origin/main.
      - Si nouvelle version disponible :
        - Terminer proprement le cycle en cours.
        - Exécuter git pull origin main.
        - Supprimer le fichier PID courant.
        - Se relancer via os.execv() ou subprocess avec les mêmes arguments.
      - Si git inaccessible → ignorer silencieusement, réessayer au prochain cycle.
    Prérequis :
      - Git installé sur chaque mini-PC.
      - Repo GitHub privé accessible (token stocké en variable d'env GIT_TOKEN
        ou configuré dans les credentials Windows Git).

================================================================================
STRUCTURE DES DOSSIERS SUR CHAQUE MINI-PC
================================================================================

C:\surveybot\
  ├── surveybot.exe          ← binaire compilé PyInstaller (spécifique au licencié)
  ├── launch_all.ps1         ← script de lancement sélectif
  ├── accounts.json          ← credentials + config par bot (non versionné)
  ├── pids\
  │   ├── bot_001.pid
  │   └── bot_002.pid
  └── profiles\
      ├── bot_001\           ← user-data-dir Chrome (créé manuellement)
      ├── bot_002\
      └── ...

Note : le code source (repo git) est présent sur la machine uniquement si
UPDATE_CHECK_ENABLED=1. Sinon, seul le binaire compilé est nécessaire.

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
8.  Remplacer handler SIGTERM par SIGINT dans launch.py.
9.  Mettre à jour config.py (commentaires + vérification des branches prod).
10. Créer launch_all.ps1.
11. Implémenter la logique de mise à jour automatique du code (update_checker.py).
12. Supprimer / archiver le scheduler Fly.io.
13. Auditer account_state.py pour supprimer les vars fivesim et autres obsolètes.

================================================================================
POINTS EN SUSPENS (décision requise avant implémentation)
================================================================================

[ ] Compte backup : idée évoquée (basculer sur un compte backup si le compte
    principal est bloqué) mais non tranchée. À décider avant d'implémenter
    la logique de gestion des comptes dans launch.py.

[ ] Solution SMS pour vérifications de compte : 5sim abandonné, alternative
    (eSIM Free Mobile, téléphone dédié avec forwarding) non finalisée.
    Impact sur fivesim_client.py et account_state.py.

[ ] SQLite local vs Postgres pour account_state sur les mini-PCs : Postgres
    central implique une latence réseau sur chaque opération d'état. SQLite
    local serait zéro latence mais sans visibilité centralisée. Non tranché.

================================================================================
STATUT
================================================================================

Tous les items ci-dessus sont EN ATTENTE d'implémentation.
Session de conception : 2026-07-02.
Aucun fichier modifié à ce stade.