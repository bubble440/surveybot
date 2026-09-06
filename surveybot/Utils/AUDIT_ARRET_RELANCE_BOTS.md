# Audit — Conditions d'arrêt, pause, relance et retour source (déploiement bare-metal Windows)

> Document d'audit en lecture seule. Aucune modification de code n'a été effectuée pendant sa
> production. Chaque ligne est ancrée sur du code réellement lu (chemin + fonction, souvent avec
> numéro de ligne). Quand le code ne permet pas de trancher avec certitude, c'est signalé
> explicitement plutôt que deviné — voir la section Observations en fin de document.

## Préambule méthodologique

- **BOT_EVOLUTION_MEMORY.md** (`Survey/BOT_EVOLUTION_MEMORY.md`) a été lu avant l'analyse. C'est
  presque exclusivement une mémoire d'extracteurs DOM par plateforme (Askia, Confirmit/Forsta,
  Kantar, Toluna, etc.) — aucune section n'y documente les conditions d'arrêt/relance elles-mêmes.
  Il n'a donc rien apporté de directement exploitable pour cet audit, en dehors de la confirmation
  que `action_dispatcher.py`, `dom_analyzer.py` et `dom_extractors_misc.py` sont des fichiers
  d'extraction/action DOM — confirmé ensuite par grep (aucune occurrence de `SystemExit`,
  `guard.pause`, `request_survey_restart` ou `StopReason` dans ces trois fichiers).
- **`PROJECT_ARCHITECTURE.md` n'existe nulle part dans le projet** (recherche exhaustive,
  insensible à la casse, sur `C:\projects\Surveys\surveybot`). Seuls quatre documents `.md`
  existent : `Survey/BOT_EVOLUTION_MEMORY.md` et, dans `Utils/` : `ORCHESTRATION_TRACKING.md`,
  `DEPLOIEMENT_BAREMETAL_DECISIONS.md`, `PLAYWRIGHT_NATIVE_MIGRATION.md`. Les deux premiers ont
  été lus intégralement en remplacement (ce sont les documents les plus proches d'une cartographie
  d'architecture/orchestration disponible dans ce projet) et sont cités ci-dessous quand leurs
  décisions éclairent ou contredisent l'état actuel du code.
- Fichiers lus intégralement : `bot_supervisor.py`, `launch.py`, `main.py`, `config.py`,
  `global_config.py`, `db_config.py`, `update_checker.py`, `wake_scheduler.ps1`,
  `check_zombie_bots.ps1`, `nssm_setup_bot.ps1`, `launch_all.ps1`, `run_tabs.ps1`,
  `tools/attach_tab.ps1`, `setup_machine.ps1`, `Management/guards/runtime_guard.py`,
  `Management/guards/survey_difficulty_guard.py`, `Management/notifier.py`,
  `Management/pause_policy.py`, `Management/redirect_watcher.py`, `Management/snap_uploader.py`,
  `State/account_state.py`, `State/daily_target.py`, `State/survey_memory.py`,
  `preselection/auth_handler.py`, `preselection/config_loader.py`, `preselection/license_guard.py`,
  `preselection/playwright_launcher.py`, `preselection/question_analyzer.py`,
  `preselection/question_validation.py`, `preselection/secret_loader.py`,
  `preselection/survey_handler.py`, `preselection/survey_navigator.py`, `platforms/__init__.py`,
  `platforms/base.py`, `platforms/topsurveys.py`, `platforms/ysense.py`, `Survey/survey_solver.py`,
  `Survey/functions.py`, `Survey/fivesim_client.py`, `captcha/captcha_solver.py`,
  `captcha/datadome_handler.py`, `captcha/normal_captcha.py`, `captcha/recaptcha_handler.py`,
  `Cash/payout.py`, `_license_config.py`, `fly.toml`, `Dockerfile`.
- Fichiers volumineux couverts par lecture ciblée des sections pertinentes + grep exhaustif du
  reste : `Survey/survey_executor.py` (2350 lignes), `Survey/action_dispatcher.py` (7737 lignes),
  `Survey/cta_handler.py` (2151 lignes), `Survey/dom_analyzer.py` (4751 lignes),
  `Survey/dom_extractors_misc.py` (13150 lignes), `preselection/response_executor.py` (856 lignes,
  confirmé sans logique d'arrêt/relance par grep), `captcha/tencent_handler.py`,
  `captcha/recaptcha_utils.py`, `tools/import_accounts.py`.
- Hors périmètre volontaire (justifié dans le corps ou en observation) : `Survey/dom_*.py`
  restants, `Survey/input_*.py`, `Survey/prompt_builder.py`, `Survey/batch_response_parser.py`,
  scripts de build (`build_release_zip.ps1`, `nuitka_build_release.ps1`).

## Note architecturale préalable — indispensable à la lecture des tableaux

Deux mécanismes de « redémarrage » totalement différents coexistent dans ce code, et les
confondre fausserait toute la colonne « impact scheduling » :

1. **Soft-restart récursif interne (`RuntimeGuard.request_survey_restart()` /
   `signal_strict_survey()` / `on_soft_restart`)** — Le process Python **ne quitte pas**.
   `on_soft_restart` (toujours défini en prod, voir `launch.py::start_runtime_guard`, passé comme
   `_soft_restart` dans `main.py`) déclenche `launch.soft_restart()` →
   `soft_restart_resume()` → **rappel direct de `preselection.survey_handler.run_survey()`**,
   qui est la fonction actuellement sur la pile d'appel (elle a elle-même appelé, plus haut,
   `solve_full_survey()` → le code qui vient de détecter l'anomalie). Il s'agit donc d'une
   **récursion Python**, pas d'une boucle `for`/`while` : chaque soft-restart empile un nouvel
   appel à `run_survey()`. C'est exactement la raison d'être du compteur
   `_restart_tl` / `_MAX_RESTART_DEPTH = 10` dans `preselection/survey_handler.py:79-95,270-280`
   (commentaire du code : profondeur bornée car un soft-restart peut être déclenché depuis
   n'importe quel thread, y compris le thread de monitoring `RuntimeGuard._monitor_loop`, d'où un
   compteur `threading.local()` plutôt qu'un compteur global). Tant que la profondeur max n'est
   pas atteinte, **le slot Postgres reste `status="running"` et le PID ne change pas** — pour
   l'orchestrateur externe (scheduler, wake_scheduler.ps1), le bot est occupé en continu, même
   s'il a en réalité recommencé un sondage depuis zéro plusieurs fois.
2. **Pause avec libération de slot (`RuntimeGuard.pause(policy, reason)`)** — Le process
   **quitte réellement** (`raise SystemExit` sur le thread principal, `os._exit()` sinon —
   `runtime_guard.py:414-421`), après avoir écrit `cooldown_until_ts` et `status="idle"` en base
   (`runtime_guard.py:392-405`) et appelé `bot_supervisor.record_exit()`. Le slot est donc
   **libéré** : un futur `nssm start` (via NSSM lui-même ou `wake_scheduler.ps1`) pourra
   réacquérir le compte, mais seulement après expiration de `cooldown_until_ts`.

Sauf mention contraire, chaque ligne des tableaux ci-dessous précise laquelle des deux
catégories s'applique.

**Mapping codes de sortie (`bot_supervisor.py:15-18`) ↔ politique NSSM
(`nssm_setup_bot.ps1:190-201`)** — table de référence utilisée dans la colonne « Action
résultante » :

| Code | Nom | `AppExit` NSSM | Ensemble de raisons observé dans le code |
|---|---|---|---|
| 0 | `EXIT_VOLUNTARY` | `Exit` (pas de restart) | SIGINT/SIGBREAK propre ; `StopReason.DAILY_TARGET_REACHED` ; `StopReason.SESSION_EXPIRED` ; `StopReason.PROXY_EXPIRED` (ensemble `_VOLUNTARY_REASONS`, `runtime_guard.py:378-382`) ; `sys.exit(0)` brut de `acquire_account_lock_or_exit` (hors mécanisme `bot_supervisor`, voir 2.5) |
| 1 | `EXIT_CRASH` | `Default → Restart` | Sentinel écrit au démarrage par `check_and_record_start()` ; valeur *de fait* de tout `sys.exit("message")`/`SystemExit("message")` qui ne passe pas explicitement par `record_exit()` (voir §1.1 et Observations) |
| 2 | `EXIT_SOFT_RESTART` | `Default → Restart` | `StopReason.IDLE` ; `StopReason.TOO_MANY_ERRORS` (chemin `pause()` seulement) ; `StopReason.RUNTIME_LIMIT` ; `StopReason.NO_SURVEY_AVAILABLE` |
| 3 | `EXIT_FATAL` | `Exit` explicite (pas de restart) | Seuil crash-loop dépassé (`check_and_record_start`) — alerte Telegram |

---

## 1. Conditions d'ARRÊT

### 1.1 Arrêt définitif du process (process quitte, aucune récursion interne possible)

| Nom court | Fichier(s) / fonction(s) | Déclencheur exact | Action résultante | Niveau | Impact scheduling |
|---|---|---|---|---|---|
| Arrêt propre SIGINT | `launch.py::install_sigint_handler` (156-167), `_make_stop_handler` (169-196) | `signal.SIGINT` reçu (Ctrl+C, ou tout envoi Python `os.kill(pid, SIGINT)`) | Écrit `ecs_stop_requested/ts/notified`, `status="idle"`, `cooldown_until_ts` remis à epoch ; `record_exit(EXIT_VOLUNTARY)` ; stop heartbeat ; suppression PID ; `raise SystemExit(0)` | Interne Python (handler de signal) | Libère le slot immédiatement (cooldown = epoch = déjà expiré) |
| Arrêt propre SIGBREAK (canal réel `nssm stop`) | `launch.py::install_sigint_handler` (166-167), même `_make_stop_handler` | `signal.SIGBREAK` — signal réellement envoyé par NSSM via `GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT)` lors d'un `nssm stop` | Identique à SIGINT ci-dessus | Interne Python, déclenché *depuis* NSSM (externe) | Libère le slot immédiatement |
| Handler SIGTERM (portabilité, inerte en pratique sous Windows) | `launch.py::install_sigterm_handler` (146-154) | `signal.SIGTERM` | Identique à SIGINT/SIGBREAK si jamais déclenché | Interne Python | Non observable en pratique sous Windows — un process externe (NSSM, `taskkill`) ne peut pas délivrer ce signal à un process Python Windows ; conservé pour Linux/tests intra-process uniquement (commentaire explicite du code, confirmé par `ORCHESTRATION_TRACKING.md` §3) |
| Seuil de redémarrages dépassé (crash-loop) | `bot_supervisor.py::check_and_record_start` (108-160), appelé `main.py:858-872` | `restart_count >= max_restarts` (défaut 5) dans une fenêtre `window_sec` (défaut 600 s), calculé à partir du dernier `last_exit_code` persisté localement (`pids/bot_<id>.state`) | Alerte Telegram (`notify_fn`) puis `record_exit(EXIT_FATAL)` + `sys.exit(EXIT_FATAL)` (=3) | Interne Python, avec sentinel local (fichier), pas Postgres | Libère le slot (`EXIT_FATAL` → NSSM n'redémarre pas) ; **intervention humaine requise** (aucun mécanisme automatique ne relance un bot en `EXIT_FATAL` — `wake_scheduler.ps1` l'exclut explicitement, voir §2.2) |
| Échec `license_guard.check_license_or_exit()` (6 sous-cas) | `preselection/license_guard.py:45-104`, appelé au niveau module de `main.py:47-49`, **avant** `check_and_record_start()` | 6 branches distinctes : `DATABASE_URL` manquante (58-59) ; Postgres injoignable (65-67) ; erreur lecture table `licenses` (76-79) ; clé licence inconnue (83-85) ; licence `is_active=false` (89-91) ; quota atteint `total_payout_eur >= max_payout_eur` (93-98) | Chaque branche fait `sys.exit("message …")` → code de sortie process = 1 (convention Python pour `sys.exit(str)`) | Interne Python — **mais avant toute initialisation bot** (avant `acquire_account_lock_or_exit`, avant `mark_bot_running`, avant `check_and_record_start`) | Ne touche à aucun état Postgres bot ni fichier local — voir Observations : ce chemin échappe totalement au compteur de crash-loop |
| `browser_launch_failed` | `launch.py::launch_driver_or_fail` (402-422) | `launch_browser_playwright()` lève une exception, ou retourne `None` | En mode prod-like : `update_state(status="idle", cooldown_until_ts=epoch, last_stop_reason="browser_launch_failed")` puis `delete_pid_file` puis `raise SystemExit("browser_launch_failed")` | Interne Python | Libère le slot immédiatement (cooldown = epoch) |
| `CHROME_PROFILE_DIR` manquant (variable vide) | `preselection/playwright_launcher.py::launch_browser_playwright` (426-429) | `os.getenv("CHROME_PROFILE_DIR","").strip()` vide **et** pas en mode attach | `raise SystemExit("[LAUNCH][PW] CHROME_PROFILE_DIR manquant — arrêt.")` | Interne Python — **lève une `SystemExit`, pas une `Exception`** : ne passe donc pas par le `except Exception` de `launch_driver_or_fail` (§ ci-dessus), qui ne s'exécute jamais pour ce cas précis (voir Observations) | Ne réinitialise PAS explicitement `cooldown_until_ts`/`status` sur ce chemin précis — dépend du TTL fixé à l'acquisition du lock (§2.5) |
| `CHROME_PROFILE_DIR` renseigné mais dossier introuvable | `preselection/playwright_launcher.py::launch_browser_playwright` (433-434) | `os.path.isdir(user_data_dir)` False | `raise SystemExit(f"[LAUNCH][PW] CHROME_PROFILE_DIR introuvable : {user_data_dir!r} — arrêt.")` | Interne Python, même remarque `SystemExit` que ci-dessus | Idem ligne précédente |
| Profondeur max de soft-restart récursif atteinte | `preselection/survey_handler.py::run_survey` (270-280) | Compteur `threading.local()` `_restart_tl.depth > _MAX_RESTART_DEPTH` (10) — voir Note architecturale | `print("[SURVEY][FATAL] …")` puis `raise SystemExit("max_restart_depth_reached")` | Interne Python | Libère le slot uniquement via le comportement générique de fin de process (pas de remise à jour explicite de `cooldown_until_ts` sur ce chemin précis) |
| Recyclage périodique du process (`MAX_MAIN_CYCLES`) | `main.py::main` (887-1006) | Boucle `while cycle < max_cycles` ; `max_cycles = int(os.getenv("MAX_MAIN_CYCLES","3"))` ; incrémenté à chaque retour normal de `run_main_loop()` | En sortie de boucle : `update_state(status="idle", cooldown_until_ts=epoch, last_stop_reason="max_main_cycles_reached")` puis `raise SystemExit("max_main_cycles_reached")` | Interne Python | Libère le slot immédiatement (cooldown = epoch) ; **note** : ce chemin ferme le navigateur (`driver.quit()` dans le `finally` du cycle précédent) puis, à la relance du process, en relance un neuf — c'est un recyclage volontaire, pas une panne |
| Exception non interceptée hors boucle de cycle | `main.py::main`, tout code exécuté avant l'entrée dans `while cycle < max_cycles` (ex. `acquire_account_lock_or_exit`, `mark_bot_running`, `start_heartbeat_thread`) | Toute `Exception` non explicitement catchée à cet endroit précis | Remonte jusqu'à l'interpréteur (`sys.excepthook` installé par `setup_logging()`, `launch.py:390-392`, journalise sans changer le comportement) → sortie process avec traceback, code de sortie non-zéro | Interne Python | Aucune libération explicite de `cooldown_until_ts`/`status` sur ce chemin — dépend uniquement du TTL du lock (§2.5) |

### 1.2 Pause temporaire avec libération de slot (`RuntimeGuard.pause()`)

Toutes les lignes ci-dessous suivent le même mécanisme (`runtime_guard.py::pause`, 369-421) :
écriture de `cooldown_until_ts = now + resolve_pause_seconds(policy)` et `status="idle"` en base,
`record_exit()`, puis **le process quitte réellement** (ce n'est donc pas une pause qui garde le
process en vie — voir Note architecturale). Seul le déclencheur et la durée diffèrent.

| Nom court | Fichier(s) / fonction(s) | Déclencheur exact | Durée de pause (`pause_policy.py`) | Code de sortie | Impact scheduling |
|---|---|---|---|---|---|
| Inactivité prolongée | `runtime_guard.py::_check_conditions` (328-334), boucle `_monitor_loop` toutes les 30s | `now - state.last_activity_ts > idle_timeout_sec` (défaut 120 s) | `SHORT_COOLDOWN` = 2 min | `EXIT_SOFT_RESTART` | Libère le slot après 2 min de cooldown Postgres |
| Trop d'erreurs consécutives (repli) | `runtime_guard.py::_check_conditions` (336-345) | `state.consecutive_errors >= max_errors_in_row` (défaut 5) **et** `on_soft_restart` absent (repli uniquement — en prod `on_soft_restart` est défini, donc ce repli n'est normalement pas emprunté, voir §1.6/Observations) | `SHORT_COOLDOWN` = 2 min | `EXIT_SOFT_RESTART` | Libère le slot après 2 min |
| Objectif journalier atteint (moniteur watchdog) | `runtime_guard.py::_check_conditions` (347-353) | `state.earnings_today_eur >= daily_target_eur` (1 €, `State/daily_target.py:8`) | `DAILY_RESET` = secondes jusqu'à minuit Europe/Paris | `EXIT_VOLUNTARY` | Libère le slot jusqu'au lendemain minuit — NSSM ne redémarre pas (`AppExit 0 = Exit`), seul `wake_scheduler.ps1` peut relancer après expiration |
| Objectif journalier atteint (après retrait confirmé) | `Cash/payout.py::check_and_cashout_if_needed` (515-554) | Après un retrait de 5 € confirmé (baisse réelle du solde observée), `gain_total >= DAILY_TARGET_EUR` | `DAILY_RESET` | `EXIT_VOLUNTARY` | Idem ligne précédente |
| Objectif journalier atteint (vérif. systématique au retour plateforme) | `Cash/payout.py::_payout_and_check_daily_stop` (592-615), appelée depuis `Survey/functions.py::_handle_topsurveys_exclusion_popup` (103, 219) et `launch.py::soft_restart` (293-296) | `balance >= (start - gained) + DAILY_TARGET_EUR`, recalculé à chaque retour sur TopSurveys, indépendamment d'un retrait | `DAILY_RESET` | `EXIT_VOLUNTARY` | Idem |
| Session TopSurveys expirée | `preselection/auth_handler.py::is_session_expired` (117-135), déclenché dans `launch.py::safe_get` (94-105) | Texte de page contenant un signal de session expirée (`"session expired"`, `"reconnectez-vous"`, `"password expired"`, etc.) | `UNTIL_MANUAL` = 1 an (« infini » de fait) | `EXIT_VOLUNTARY` | Libère le slot mais avec un cooldown d'un an — **nécessite une action humaine** (ré-authentification manuelle) ; aucun mécanisme de relance automatique ne peut expirer ce cooldown en pratique |
| Proxy expiré / inaccessible | `preselection/auth_handler.py::handle_proxy_error_page_if_needed` (153-175), détection via `is_proxy_error_page` (138-150 : URL `chrome-error://` ou texte `err_timed_out`) | Page d'erreur Chrome ERR_TIMED_OUT détectée après navigation | `DAILY_RESET` | `EXIT_VOLUNTARY` | Libère le slot jusqu'au lendemain — le proxy n'étant pas censé changer avant, ce choix suppose une intervention humaine (changement de proxy) avant que le redémarrage du lendemain serve à quelque chose ; non tranché par le code lui-même (voir Observations) |
| Aucun survey disponible sur le listing | `preselection/survey_navigator.py::go_to_best_value_survey` (504-513) | `_find_survey_cards(driver)` ne retourne aucune carte après navigation vers l'onglet Sondages | `MEDIUM_LONG_COOLDOWN` = 15 min | `EXIT_SOFT_RESTART` | Libère le slot après 15 min |
| Runtime max atteint | `runtime_guard.py::_check_conditions` (355-361) | `now - state.start_ts >= max_runtime_sec` (défaut 2h) | `MEDIUM_LONG_COOLDOWN` = 15 min | `EXIT_SOFT_RESTART` | Libère le slot après 15 min ; c'est un plafond de durée de vie du process, indépendant du nombre de surveys traités |
| Repli ultime `request_survey_restart` | `runtime_guard.py::request_survey_restart` (172-218), branche finale (213-218) | CTA introuvable **et** délégation `on_soft_restart` elle-même en échec (exception levée) | `SHORT_COOLDOWN` = 2 min, raison `TOO_MANY_ERRORS` (réutilisée car jugée « plus cohérente » selon le commentaire du code, ligne 217) | `EXIT_SOFT_RESTART` | Libère le slot après 2 min — chemin de repli seulement, tous les appelants passent normalement par la délégation `on_soft_restart` réussie (soft-restart récursif, pas de pause) |

### 1.3 Arrêt déclenché par détection anti-bot / captcha non résolu

Distinction importante : selon le contexte, un captcha détecté déclenche soit un **soft-restart
récursif** (le process reste en vie, cf. Note architecturale), soit une **pause avec sortie
process** — jamais un blocage indéfini en attente de résolution en prod (le blocage interactif
`input()` n'existe qu'en mode attach/local).

| Nom court | Fichier(s) / fonction(s) | Déclencheur exact | Action résultante | Niveau | Impact scheduling |
|---|---|---|---|---|---|
| Détection anti-bot générique (survey trop strict) | `Management/guards/survey_difficulty_guard.py::detect_strict_survey` (279-387), consommé par `Survey/survey_solver.py::solve_full_survey` (525-636) | Sélecteurs DOM forts (`STRICT_SELECTORS` : iframe reCAPTCHA/captcha, `[data-sitekey]`, `#captcha`) ou mots-clés (`STRICT_KEYWORDS`), avec règles de désambiguïsation (ex. DataDome exclu du captcha générique, `_has_datadome_iframe`) | Selon `reason` retourné : `captcha` → voir lignes suivantes ; autre raison (`image_evaluation`, `drag_drop` non visible, `hold_button`, `audio_video_required`) → `guard.signal_strict_survey(f"strict_mid_{reason}")` (`survey_solver.py:632-636`) | Interne Python | Soft-restart récursif (process vivant, slot occupé) sauf échec de la délégation (repli pause, voir 1.2) |
| Captcha auto-résolu avec succès (reCAPTCHA v2) | `captcha/recaptcha_handler.py::solve_recaptcha_v2_auto` (267-360), appelé depuis `survey_solver.py:566-574` | `get_captcha_behavior() == "auto_2captcha"` (i.e. `TWO_CAPTCHA_KEY`/`CAPSOLVER_API_KEY` configurée), sitekey extrait, 2Captcha/CapSolver renvoie un token, callback JS exécuté ou token présent (`_verify_recaptcha_resolved`) | `continue` — reprise de la boucle `solve_full_survey`, aucun arrêt | Interne Python | Aucun (le bot continue dans le même cycle) |
| Boucle captcha détectée (résolutions répétées sans navigation) | `Survey/survey_solver.py:538-548` | Compteur `page._auto2captcha_attempts` (remis à zéro si l'URL change) `> 2` sur la même URL | `guard.signal_strict_survey("captcha_loop_detected")` | Interne Python | Soft-restart récursif |
| Échec résolution captcha image-texte (fallback non-reCAPTCHA) | `Survey/survey_solver.py:549-564`, délègue à `captcha/normal_captcha.py::handle_captcha` | Pas d'iframe/sitekey reCAPTCHA réel (`is_real_recaptcha_present` False) et `handle_normal_captcha` retourne False | `guard.signal_strict_survey("captcha_auto_failed")` | Interne Python | Soft-restart récursif |
| Échec résolution reCAPTCHA auto | `Survey/survey_solver.py:565-579` | `solve_recaptcha_v2_auto` retourne False (sitekey introuvable, timeout API, token vide, callback non déclenché et pas de token) | `guard.signal_strict_survey("captcha_auto_failed")` | Interne Python | Soft-restart récursif |
| Captcha en prod sans clé configurée | `Survey/survey_solver.py:581-585`, `get_captcha_behavior()` (`config.py:133-147`) → `"restart"` si `TWO_CAPTCHA_KEY` absente | `captcha_behavior == "restart"` | `guard.signal_strict_survey("strict_mid_captcha")` | Interne Python | Soft-restart récursif |
| Captcha en local, abandon manuel (Ctrl+C pendant la pause) | `Survey/survey_solver.py:596-603` | `KeyboardInterrupt` pendant `input()` bloquant (uniquement si `should_block_for_input()` vrai — jamais en prod) | `guard.signal_strict_survey("captcha_user_abort")` | Interne Python, attach/local uniquement | Non pertinent en prod bare-metal (aucun terminal interactif) |
| Captcha en local, terminal non-interactif | `Survey/survey_solver.py:604-608` | `should_block_for_input()` False (pas de TTY) | `guard.signal_strict_survey("captcha_no_tty")` | Interne Python | Soft-restart récursif |
| Captcha en local, timeout de résolution manuelle (30 s) | `Survey/survey_solver.py:610-624` | Boucle de vérification 30 s après la pause manuelle, captcha toujours présent | `guard.signal_strict_survey("captcha_timeout")` | Interne Python | Soft-restart récursif |
| Captcha slider Tencent non résolu | `Survey/action_dispatcher.py::handle_captcha_guard` (6212-6231), délègue à `captcha/tencent_handler.py::solve_tencent_auto` | Détection DOM `#sliderpanel` + `.verify-img-panel`/`.verify-gap`/`.verify-bar-area`, résolution CapSolver/2Captcha échouée | `guard.signal_strict_survey("slider_captcha_unresolvable")` | Interne Python | Soft-restart récursif |
| Captcha détecté hors flux principal (guard générique `action_dispatcher.py`) — prod sans résolution auto | `Survey/action_dispatcher.py::handle_captcha_guard` (6233-6238) | `captcha_behavior == "restart"` | `guard.signal_strict_survey("captcha_guard_restart")` | Interne Python | Soft-restart récursif |
| Captcha détecté hors flux principal — échec auto_2captcha | `Survey/action_dispatcher.py::handle_captcha_guard` (6240-6256) | `solve_recaptcha_v2_auto` échoue | `guard.signal_strict_survey("captcha_auto_failed")` | Interne Python | Soft-restart récursif |
| Captcha détecté hors flux principal — prod sans terminal (nommé "aws" dans le code, vestige de l'ancienne infra cloud) | `Survey/action_dispatcher.py::handle_captcha_guard` (6258-6264) | `not is_attach_mode()` et aucun des cas précédents | `guard.signal_strict_survey("captcha_guard_aws")` | Interne Python | Soft-restart récursif |
| DataDome résolu automatiquement | `captcha/datadome_handler.py::solve_datadome_auto` (71-189) | Iframe `captcha-delivery.com` détecté, `t≠lb` (IP non bannie), proxy configuré, cookie CapSolver injecté avec succès | `driver.reload()` puis retour `True` → traitement normal repris | Interne Python | Aucun arrêt (poursuite du cycle) |
| DataDome — IP bannie (non résolvable) | `captcha/datadome_handler.py:88-94` | Paramètre `t=lb` dans l'URL de l'iframe DataDome | `return False` (aucun signal direct au RuntimeGuard depuis cette fonction — c'est l'appelant, `execute_survey_page`, qui continue son traitement DOM normal, lequel finira probablement par déclencher une détection stuck/budget épuisé, §1.6) | Interne Python | Pas d'arrêt immédiat — dépend d'un mécanisme aval (stuck detection) pour finir par déclencher un soft-restart |
| DataDome — proxy absent | `captcha/datadome_handler.py:98-102` | `_get_proxy_config()` retourne None | `return False` (même remarque que ci-dessus) | Interne Python | Idem |

### 1.4 Arrêt déclenché par quota / limite / fin de plage horaire

| Nom court | Fichier(s) / fonction(s) | Déclencheur exact | Action résultante | Niveau | Impact scheduling |
|---|---|---|---|---|---|
| Objectif de gain journalier (1 €) | Voir toutes les lignes « Objectif journalier atteint » du §1.2 | `DAILY_TARGET_EUR = 1.0` (`State/daily_target.py:8`) comparé à `earnings_today_eur`/solde réel | `guard.pause(DAILY_RESET, DAILY_TARGET_REACHED)` | Interne Python | Libère le slot jusqu'à minuit Europe/Paris |
| Quota de licence atteint (parc entier, pas par bot) | `preselection/license_guard.py::check_license_or_exit` (93-98) | `total_payout_eur >= max_payout_eur` (colonnes Postgres `licenses`, lues via la fonction SQL `SECURITY DEFINER` `check_license`) | `sys.exit("license_guard: quota atteint")` | Interne Python, **avant** toute logique de cooldown/bot_supervisor (voir §1.1) | Process quitte immédiatement à chaque démarrage tant que le quota Postgres n'est pas relevé côté opérateur — pas de cooldown, pas de compteur crash-loop (voir Observations) |
| Licence désactivée | `preselection/license_guard.py:89-91` | `is_active=false` sur la ligne `licenses` correspondant à `LICENSE_KEY` | `sys.exit("license_guard: licence désactivée")` | Idem ligne précédente | Idem |
| **Fin de plage horaire fixe (heures d'ouverture)** | — | — | **Non trouvé.** Recherche ciblée (grep sur des motifs horaires : `business_hours`, `heure_debut/fin`, comparaisons sur `.hour`) sans résultat dans tout le projet. Le seul mécanisme temporel cyclique est le `DAILY_RESET` (minuit Europe/Paris), qui est indexé sur un **montant gagné**, pas sur une heure de la journée. | — | — |
| Nombre max de sondages / cycle (`MAX_MAIN_CYCLES`) | `main.py:887-890` | `os.getenv("MAX_MAIN_CYCLES","3")` — 3 cycles complets (login→navigation→résolution) par process avant recyclage | Voir §1.1 « Recyclage périodique » | Interne Python | Libère le slot, recyclage volontaire du process (pas un quota métier — un plafond technique de durée de vie du process) |

### 1.5 Arrêt déclenché par erreur proxy / perte réseau / erreur d'authentification

| Nom court | Fichier(s) / fonction(s) | Déclencheur exact | Action résultante | Niveau | Impact scheduling |
|---|---|---|---|---|---|
| Page d'erreur proxy (ERR_TIMED_OUT) | Voir §1.2 « Proxy expiré / inaccessible » | `is_proxy_error_page` | `guard.pause(DAILY_RESET, PROXY_EXPIRED)` + `raise SystemExit("proxy_expired")` explicite juste après (`auth_handler.py:175` — redondant avec le `SystemExit` déjà levé par `pause()`, jamais atteint en pratique) | Interne Python | Libère le slot jusqu'au lendemain |
| Erreur réseau Chrome en cours de résolution (tunnel/DNS/refus) | `Survey/survey_solver.py::_recover_from_network_error` (309-356) | Contenu de page contenant un des signaux `_NETWORK_ERR_SIGNALS` (`err_tunnel_connection_failed`, `this site can't be reached`, `err_connection_refused`, `err_name_not_resolved`) | Retry interne borné : jusqu'à 5 tentatives (`_MAX_ATTEMPTS=5`), 15 s d'attente entre chaque, `page.goto()` sur l'URL courante | Interne Python | Aucun (tant que le budget n'est pas épuisé) — voir ligne suivante pour l'épuisement |
| Budget de récupération réseau épuisé | Même fonction, retour `_NET_ERR_EXHAUSTED`, consommé par `survey_solver.py:657-659` | 5 tentatives échouées | `guard.request_survey_restart("net_err_max_attempts")` | Interne Python | Soft-restart récursif (process vivant) |
| Erreur de connexion Postgres pendant l'acquisition du lock compte | `State/account_state.py::try_acquire_cooldown_slot` (447-490), appelé par `launch.py::acquire_account_lock_or_exit` (73-77) | Toute exception pendant la transaction `SELECT … FOR UPDATE` (Postgres injoignable, réseau, timeout) — capturée par `except Exception as e: … return False` (486-488) | `acquire_account_lock_or_exit` traite ce `False` exactement comme un cooldown actif légitime : `print("[COOLDOWN] … déjà actif → exit")` puis `sys.exit(0)` | Interne Python | Voir Observations — ce cas est indiscernable côté appelant d'un cooldown normal, alors que la cause réelle est une panne d'infrastructure |
| Échec d'authentification TopSurveys (formulaire) | `preselection/auth_handler.py::login` (256-359) | Sélecteurs email/mot de passe/bouton introuvables (`wait_for_selector` en timeout) | `print(...)`, sauvegarde HTML de debug, `return` simple (pas d'exception) — la fonction appelante (`init_session_and_enter_surveys`, `launch.py:463-522`) poursuit ensuite vers `go_to_best_value_survey`, qui échouera probablement à son tour faute de session active | Interne Python | Pas d'arrêt direct à cet endroit précis — dégradation en cascade probable vers une autre condition (session expirée, aucun survey disponible) |

### 1.6 Arrêt déclenché par erreur DOM fatale ou boucle bornée épuisée (« budget »)

Toutes ces conditions utilisent le même schéma : un compteur (souvent indexé par
`urlsplit(url).path`, parfois enrichi d'une empreinte DOM) est stocké sur l'objet `driver`
lui-même (attributs `_disq_page_seen`, `_dom_only_abort_seen`) et remis à zéro implicitement à
chaque nouvelle URL/empreinte — ce n'est donc pas une boucle `for` bornée mais un compteur
persistant par clé, cumulatif tant que la clé ne change pas.

| Nom court | Fichier(s) / fonction(s) | Déclencheur / seuil exact | Action résultante | Niveau | Impact scheduling |
|---|---|---|---|---|---|
| Page de disqualification/fin détectée (DOM) | `Survey/survey_executor.py::_detect_disqualification_page` (495-523) → `_budgeted_disqualification_restart` (526-572) | Iframe callback (`samplicio.us`, `clientcallback`) ou texte (`_DISQ_TEXT_SIGNALS` : "screened out", "disqualified", "no longer available", etc.) | 1ʳᵉ détection sur une URL (`max_hits=1`) → `runtime_guard.get_guard().request_survey_restart(f"disqualification_page:{signal}")` | Interne Python | Soft-restart récursif |
| Budget disqualification épuisé (2ᵉ détection même URL) | Même fonction (546-554) | `counters.get(budget_key,0) >= 1` | `return "budget_exhausted"` → `execute_survey_page` retourne `False` → compté comme erreur générique (`guard.record_error()` dans `solve_full_survey`, pas de soft-restart immédiat ciblé) | Interne Python | Aucun arrêt direct ; contribue au compteur `consecutive_errors` du RuntimeGuard (seuil 5, §1.2) |
| Budget disqualification épuisé — **mode attach uniquement** | `_budgeted_disqualification_restart:560-564` | Idem, mais `is_attach_mode()` vrai | Positionne le sentinel global `_attach_disq_stop_requested = True`, retourne `"attach_stop"` | Interne Python, attach/dev uniquement | `main.py` (boucles `run_attach_takeover`/`run_attach_login_takeover`/`run_attach_preselection_takeover`) lit ce sentinel et fait `break` — arrêt de la boucle de takeover local, non pertinent en prod |
| Pattern DOM « rate & rank » image-eval non résoluble | `Survey/survey_executor.py::_detect_rate_rank_image_eval_dom` + `_budgeted_dom_only_abort_for_image_eval` (575-621) | Détection DOM spécifique (non détaillée ici — hors périmètre de cet audit, cf. `dom_analyzer.py`/`dom_extractors_misc.py` explicitement écartés) ; `max_hits=1` par URL | 1ʳᵉ détection → `request_survey_restart(f"dom_only_abort_image_eval:{reason}")` ; épuisement → `return False` (même traitement générique que ci-dessus) | Interne Python | Soft-restart récursif, puis compteur d'erreurs générique après épuisement |
| Inputs image-only non résolubles (2 variantes) | `Survey/survey_executor.py::_budgeted_soft_restart_for_image_only_inputs` (624-712) | Pattern `image_only_wrapped_inputs` (`max_hits=1`) ou `image_selection_challenge` (`max_hits=2`), clé incluant une empreinte DOM (`dom_fp`) | `image_only_wrapped_inputs` : tentative de sélection **aléatoire** d'un input interactif avant tout budget-exhausted (`return "random_selected"`) ; sinon `request_survey_restart(f"dom_only_abort:{reason}")` ; épuisement → `return False` | Interne Python | Soft-restart récursif (ou clic aléatoire best-effort, qui n'est pas un arrêt) ; épuisement → compteur d'erreurs générique |
| Question ouverte avec image intégrée non résoluble en DOM seul | `Survey/survey_executor.py::_detect_open_text_embedded_image_unresolvable_dom` + `_budgeted_soft_restart_for_open_text_embedded_image` (715-854) | Un seul `textarea` visible, aucun autre input, ≥1 image `taImage` large en `data:image/` avec `pointer-events:none`, `max_hits=1` | `request_survey_restart(f"dom_only_abort:open_text_embedded_image")` ; épuisement → `return False` | Interne Python | Idem |
| Écran vidéo gate ISD non résolvable | `Survey/survey_executor.py::_handle_forcewatch_video_gate` (~915-931) | `#ISD` + lecteur vidéo masqué + bouton de navigation `cf-navigation__button` désactivé, confirmé structurellement | `request_survey_restart("video_gate_isd")` | Interne Python | Soft-restart récursif |
| Écran vidéo gate « forcewatch » (2 points de détection) | `Survey/survey_executor.py:~952,~997` | `video[data-forcewatch]` visible, tentative JS de déclenchement `timeupdate`/`ended` sans effet observable après polling ~5s | `request_survey_restart("video_gate_forcewatch")` | Interne Python | Soft-restart récursif |
| Page vidéo ISD non résolvable — **mode attach uniquement** | `main.py::run_attach_takeover` (372-391) | Détection DOM directe (`#ISD`/`[id^="rootDiv_"]` + `<video>` visible) après un `execute_survey_page` en échec | `break` (sort de la boucle takeover locale) | Interne Python, attach/dev uniquement | Non pertinent en prod |
| Page inchangée N fois — stuck detection multi-inputs | `Survey/survey_solver.py:761-777` | 3 niveaux de comparaison (nb questions visibles → textes → états inputs cochés/sélectionnés) inchangés `_NO_PROGRESS_THRESHOLD` fois (8) | `request_survey_restart("solve_no_progress_multi")` | Interne Python | Soft-restart récursif |
| Réponse acceptée mais page ne progresse pas | `Survey/survey_solver.py:837-855` | `_no_progress_count >= _NO_PROGRESS_THRESHOLD` (8), question extraite identique à `last_question_key` | `request_survey_restart("solve_no_progress")` | Interne Python | Soft-restart récursif |
| Échec répété du clic CTA sans progression | `Survey/survey_solver.py:856-870` | `_cta_fail_count >= 3` sur la même URL | `request_survey_restart("cta_fail_no_progress")` | Interne Python | Soft-restart récursif |
| Aucun élément actionnable → survey terminé | `Survey/survey_solver.py:872-893` | `_has_actionable_elements(page)` False et pas de succès juste précédent | `_survey_ctx.flush()` puis `request_survey_restart("survey_end")` | Interne Python | Soft-restart récursif — c'est le chemin de **fin normale de sondage** (voir §3.1), pas une erreur |
| Erreur applicative Toluna/Confirmit (`errorPage`/`errorpage-wrapper`) | `Survey/survey_solver.py:669-683` | Présence DOM de ces classes | `request_survey_restart("platform_error_page")` | Interne Python | Soft-restart récursif |
| Erreur applicative Decipher (`div.survey-error`) | `Survey/survey_solver.py:685-707` | `div.survey-error` visible **et** aucune question radio/checkbox actionnable dans `div.question` | `request_survey_restart("decipher_survey_error")` | Interne Python | Soft-restart récursif |
| Erreur applicative YouGov | `Survey/survey_solver.py::_recover_from_yougov_app_error` (367-420) | `#notification.alert-error` visible et `#main_cont` masqué | Retry borné 3 tentatives (`_YG_MAX_ATTEMPTS`), 10 s entre chaque, puis `request_survey_restart("yougov_app_err_max_attempts")` si toujours en erreur | Interne Python | Aucun tant que le budget n'est pas épuisé ; soft-restart récursif ensuite |
| Erreur applicative YouGov — **mode attach uniquement** | `main.py:320-326` | `_recover_from_yougov_app_error` retourne `_YG_ERR_EXHAUSTED` | `break` (sort de la boucle takeover) | Interne Python, attach/dev uniquement | Non pertinent en prod |
| Page d'erreur applicative générique — **mode attach uniquement** | `main.py:328-360` | Classes `errorPage`/`errorpage-wrapper` ou `div.survey-error` visible sans question actionnable | `break` | Interne Python, attach/dev uniquement | Non pertinent en prod |

---

## 2. Conditions de RELANCE

### 2.1 Restart automatique via NSSM

| Nom court | Fichier(s) / fonction(s) | Déclencheur exact | Action résultante | Niveau | Impact scheduling |
|---|---|---|---|---|---|
| Restart sur code de sortie non mappé (`Default`) | `nssm_setup_bot.ps1:198` (`nssm set $svcName AppExit Default Restart`) | Tout code de sortie process autre que 0 et 3 (donc 1, 2, ou tout code inattendu) | Relance le process après `AppRestartDelay` (30 000 ms = 30 s, `nssm_setup_bot.ps1:201`) | Externe (service Windows NSSM) | Le bot redevient `PID` actif quasi immédiatement (30 s), mais peut se re-exit(0) tout de suite via le lock cooldown (§2.5) si le cooldown Postgres pertinent n'a pas expiré |
| Pas de restart sur `EXIT_VOLUNTARY` (0) | `nssm_setup_bot.ps1:199` | Code de sortie 0 | Service NSSM reste `SERVICE_STOPPED` | Externe | Le bot ne revient que via `wake_scheduler.ps1` (§2.2) une fois le cooldown expiré |
| Pas de restart sur `EXIT_FATAL` (3) | `nssm_setup_bot.ps1:200` | Code de sortie 3 | Service NSSM reste `SERVICE_STOPPED` | Externe | Aucune relance automatique prévue — `wake_scheduler.ps1` exclut explicitement ce cas (§2.2) ; intervention humaine requise |
| Séquence d'arrêt gracieux avant `TerminateProcess` | `nssm_setup_bot.ps1:187-188` | `nssm stop`/`nssm restart` déclenché (manuel ou par `check_zombie_bots.ps1`) | `AppStopMethodSkip 6` saute les méthodes Window/Thread (sans effet sur un process console Python) ; `AppStopMethodConsole 30000` laisse 30 s à la séquence de fermeture propre (SIGBREAK → cleanup) avant `TerminateProcess` forcé | Externe (NSSM), avec un budget de grâce pour le code interne Python | Si le cleanup dépasse 30 s (Chrome figé), `TerminateProcess` s'enclenche et peut laisser des process Chrome enfants orphelins — risque documenté mais non traité, `ORCHESTRATION_TRACKING.md` §4 |
| Démarrage automatique au boot machine | `nssm_setup_bot.ps1:204` (`nssm set $svcName Start SERVICE_AUTO_START`) | Démarrage du service Windows (boot machine, ou `Start-Service`) | Lance `python.exe code\main.py` avec les variables d'environnement PAR_BOT injectées (`AppEnvironmentExtra`, lignes 152-166) | Externe | Le bot est actif dès le boot, indépendamment de tout scheduler applicatif |

### 2.2 `wake_scheduler.ps1` — critères exacts de réveil après cooldown expiré

Tâche planifiée (pas un process continu), toutes les 5 min par défaut selon l'en-tête du script
(`New-TimeSpan -Minutes 5`, commentaire d'installation lignes 19-26 — **la tâche planifiée
elle-même n'est pas présente dans ce dépôt**, seule la commande d'enregistrement suggérée
est documentée en commentaire).

| Nom court | Fichier(s) / fonction(s) | Déclencheur exact | Action résultante | Niveau | Impact scheduling |
|---|---|---|---|---|---|
| Garde-fou volume de comptes | `wake_scheduler.ps1:36,60-68` | `$accountIds.Count -gt $MAX_ACCOUNTS` (200) | `exit 1` immédiat, aucun compte traité | Externe (PowerShell) | Aucun bot relancé ce cycle |
| Interrogation cooldown Postgres en lot | `wake_scheduler.ps1:86-97`, invoque `python.exe code\main.py --query-cooldown <id1> <id2> …` (mode CLI défini dans `main.py:8-25`) | Une seule connexion Postgres pour tous les comptes, lecture `cooldown_until_ts` par compte via `State/account_state.py::load_state`/`_ts_to_unix` | JSON `{account_id, cooldown_until_ts, is_expired}` par compte sur stdout, `sys.exit(0)` systématique côté Python | Interne Python (mode CLI dédié) invoqué depuis un contexte externe (PowerShell) | — |
| Cooldown encore actif | `wake_scheduler.ps1:127-130` | `status.is_expired` False | `continue` — compte ignoré ce cycle | Externe | Aucune action |
| Exclusion `EXIT_FATAL` | `wake_scheduler.ps1:132-145` | Fichier local `pids/bot_<id>.state`, `last_exit_code == 3` | `continue` — jamais relancé automatiquement, quel que soit le cooldown Postgres | Externe (lit un fichier local, pas Postgres) | Confirme qu'un bot `EXIT_FATAL` reste arrêté indéfiniment sans intervention humaine |
| Service déjà actif | `wake_scheduler.ps1:147-157` | `nssm status $svcName` matche `^SERVICE_RUNNING` | `continue` | Externe | Aucune action (évite un double démarrage) |
| Service NSSM introuvable | `wake_scheduler.ps1:150-153` | `$LASTEXITCODE -ne 0` après `nssm status` | `continue` avec avertissement | Externe | Aucune action — signale une incohérence entre `accounts.json` et les services NSSM installés, sans y remédier |
| Démarrage effectif | `wake_scheduler.ps1:159-166` | Cooldown expiré + pas `EXIT_FATAL` + service arrêté | `nssm start $svcName` | Externe | Le bot repasse `SERVICE_RUNNING`, ré-exécute tout le cycle de démarrage (`check_and_record_start`, `acquire_account_lock_or_exit`, etc.) |

### 2.3 `check_zombie_bots.ps1` — critères exacts de détection zombie et de réveil

Tâche planifiée, toutes les 3 min par défaut selon l'en-tête (commentaire d'installation lignes
8-15 — même remarque que ci-dessus, la tâche planifiée elle-même n'est pas présente dans ce
dépôt).

| Nom court | Fichier(s) / fonction(s) | Déclencheur exact | Action résultante | Niveau | Impact scheduling |
|---|---|---|---|---|---|
| Absence de fichiers `.state` | `check_zombie_bots.ps1:24-29` | `Get-ChildItem $PidsDir -Filter "bot_*.state"` vide | `exit 0`, rien à faire | Externe | — |
| Exclusion arrêt volontaire/fatal | `check_zombie_bots.ps1:50-55` | `last_exit_code -eq 0 -or last_exit_code -eq 3` | `continue` — pas d'alerte, considéré comme un arrêt normal | Externe | Confirme que ce script ne relance jamais un bot en `EXIT_VOLUNTARY`/`EXIT_FATAL` — rôle strictement complémentaire de `wake_scheduler.ps1` |
| Heartbeat manquant | `check_zombie_bots.ps1:57-60` | `last_heartbeat_ts` absent du fichier `.state` | Avertissement, `continue` (pas de restart) | Externe | Aucune action — cas non couvert par un restart (juste signalé) |
| **Détection zombie** | `check_zombie_bots.ps1:62-72` | `ageSeconds = now_unix - last_heartbeat_ts` ; seuil `$HeartbeatTimeoutSec` (défaut **300 s = 5 min**) | `nssm restart $svcName` | Externe | Le process est tué puis relancé par NSSM (le bot ne s'arrête pas de lui-même — c'est un `restart` NSSM forcé sur un process considéré comme figé mais toujours vivant côté OS) |
| Heartbeat local sous-jacent (émetteur du signal lu ci-dessus) | `bot_supervisor.py::write_heartbeat` (79-90), appelé par `runtime_guard.py::heartbeat` (224-253) depuis le thread `_heartbeat` (`launch.py:346-363`) | Toutes les `HEARTBEAT_INTERVAL_SEC` (défaut 60 s) + gigue aléatoire `[0, HEARTBEAT_JITTER_SEC]` (défaut 3 s) | Écrit `last_heartbeat_ts`/`pid`/`account_id` dans `pids/bot_<id>.state` | Interne Python (thread démon séparé du thread principal) | Le heartbeat est **best-effort** : toute exception est avalée (`except Exception: pass`, `launch.py:356-358`) — un heartbeat qui échoue silencieusement en continu mènerait `check_zombie_bots.ps1` à considérer le bot comme zombie même s'il travaille normalement (voir aussi le heartbeat Postgres distinct ci-dessous) |
| Heartbeat Postgres distinct (TTL du lock compte, pas la détection zombie locale) | `State/account_state.py::touch_heartbeat` (382-420), appelé par `runtime_guard.py::heartbeat` (234-236) | Même cadence que ci-dessus ; condition SQL `state->>'status' = 'running'` | Prolonge `cooldown_until_ts` à `now + ACCOUNT_LOCK_TTL_SEC` (défaut 240 s) | Interne Python | Ce TTL est indépendant du seuil de 300 s utilisé par `check_zombie_bots.ps1` — deux horloges séparées pour deux objectifs différents (lock d'exclusivité Postgres vs détection zombie locale), voir Observations |
| Échecs consécutifs de heartbeat Postgres | `runtime_guard.py::heartbeat` (243-253) | `_hb_fail_count >= 3` (`touch_heartbeat` retourne False à répétition) | `log.error(...)` uniquement — **aucune action corrective**, juste une alerte dans les logs applicatifs (pas Telegram) | Interne Python | Risque signalé par le code lui-même dans son propre commentaire : « lock potentiellement expiré, risque de double exécution » — non traité automatiquement |

### 2.4 `launch_all.ps1` — statut et mécanisme PID + heure de démarrage

**Ce fichier existe toujours dans le dépôt** (`launch_all.ps1`, 239 lignes, dernière modification
antérieure à cette session), alors que `Utils/ORCHESTRATION_TRACKING.md` §9 affirme explicitement
« ✅ Supprimé (13/07/2026) … Le fichier n'existe plus dans le projet. » — voir Observations pour
cette contradiction. Le contenu ci-dessous documente ce que le fichier fait *tel qu'il existe
actuellement*, sans trancher s'il est réellement exécuté par une tâche planifiée en prod (aucune
tâche planifiée n'est définie dans ce dépôt pour l'invoquer, tout comme pour les deux scripts
précédents).

| Nom court | Fichier(s) / fonction(s) | Déclencheur exact | Action résultante | Niveau | Impact scheduling |
|---|---|---|---|---|---|
| Bot déjà actif (PID valide + heure de démarrage concordante) | `launch_all.ps1::Test-BotProcessAlive` (49-65), utilisé dans la boucle principale (210-230) | Fichier `pids/bot_<id>.pid` au format `"<PID>|<StartTicks>"` ; `Get-Process -Id $ProcessId` réussit **et** `$p.StartTime.Ticks -eq $ExpectedStartTicks` | `continue` — le bot n'est pas relancé | Externe (PowerShell) | Empêche un double lancement du même bot |
| PID recyclé par un autre process (détection explicite) | `launch_all.ps1:216-224` | Fichier PID lisible (2 parties valides) mais `Test-BotProcessAlive` False — soit le PID n'existe plus, soit il existe mais avec un `StartTime` différent (Windows a réattribué ce PID à un autre process après la fin du bot) | Suppression du fichier PID (`Remove-Item`) puis relance via `Start-Bot` | Externe | C'est le mécanisme explicite anti-recycling PID demandé par l'audit : la comparaison **PID + ticks de démarrage**, pas le PID seul, déclenche la relance |
| Fichier PID corrompu / ancien format | `launch_all.ps1:225-229` | Le fichier ne contient pas exactement 2 parties séparées par `|`, ou parsing `int`/`long` échoue | Suppression + relance | Externe | Idem — traité comme un cas dégradé du précédent |
| Lancement effectif | `launch_all.ps1::Start-Bot` (67-162) | Aucun fichier PID, ou fichier PID nettoyé ci-dessus | `Start-Process` avec variables d'environnement PAR_BOT construites depuis `accounts.json`, écriture `pids/bot_<id>.pid` au format `"<PID>|<StartTime.Ticks>"` (155-159) | Externe | Nouveau process lancé, log dans `logs/launch_all.log` |
| Vérification profil Chrome | `launch_all.ps1::Start-Bot` (73-77) | `Test-Path $profileDir` False | `Write-Log "SKIP … profile_dir introuvable"`, `return` sans lancer | Externe | Le bot concerné n'est simplement pas démarré ce cycle |
| Double-écriture PID (bot lui-même) | `launch.py::write_pid_file` (21-42) | Appelé par `mark_bot_running` (`launch.py:394-400`) au démarrage du process Python | N'écrase le fichier `pids/bot_<id>.pid` que s'il ne contient pas déjà exactement le PID courant en première partie (`existing_pid == str(my_pid)` sinon écriture) — commentaire du code : ne doit pas dégrader le couple `PID|StartTicks` écrit par `launch_all.ps1` en un PID nu | Interne Python | Redondance volontaire (« double sécurité ») avec l'écriture PID de `launch_all.ps1` |

### 2.5 Retry interne (code Python) vs retry externe (orchestration Windows)

| Nom court | Fichier(s) / fonction(s) | Déclencheur exact | Portée | Niveau |
|---|---|---|---|---|
| Soft-restart récursif borné | Voir Note architecturale ; `preselection/survey_handler.py:79-95,270-280` | Tout appel à `request_survey_restart`/`signal_strict_survey` (≈30 sites listés dans ce document) | Interne au process — jusqu'à 10 niveaux de récursion (`_MAX_RESTART_DEPTH`), compteur `threading.local()` (explicitement pour supporter un déclenchement depuis le thread de monitoring `RuntimeGuard._monitor_loop`, pas seulement le thread principal) | 100% Python interne |
| Boucle `while cycle < max_cycles` (recyclage de process, pas de survey) | `main.py:887-1006` | Retour normal de `run_main_loop()` après un cycle complet | Un nouveau navigateur est relancé dans le **même process** jusqu'à `MAX_MAIN_CYCLES` (3), puis le process quitte réellement | Interne Python |
| Boucle `while cycle < max_cycles` — retry sur exception | `main.py:955-970` | `except Exception` (pas `SystemExit`) dans le corps du cycle | Libère le lock Postgres (`status=idle`, `last_stop_reason=f"crash_{type}"`), `time.sleep(2)`, `continue` — **le process ne quitte pas**, il retente un cycle complet (nouveau `launch_driver_or_fail`, etc.) tant que `cycle < max_cycles` | Interne Python |
| Retry manifeste de mise à jour (réseau au boot) | `update_checker.py::_fetch_manifest_with_early_boot_retry` (130-151) | `urllib.error.URLError` lors de la récupération du manifeste R2, jusqu'à `_MANIFEST_FETCH_MAX_ATTEMPTS` (3), 2 s entre chaque | Interne au process, avant tout lancement de navigateur | Interne Python |
| Retry lecture solde (UI pas encore prête) | `Cash/payout.py::check_and_cashout_if_needed` (428-443) | Boucle 12 s (`deadline`), 0.6 s entre tentatives, `_read_balance` échoue | Interne, best-effort | Interne Python |
| Retry API captcha (polling résultat de tâche) | `captcha/captcha_solver.py` (toutes les méthodes `TwoCaptchaClient`/`CapSolverClient`) | Polling toutes les `poll_interval` (défaut 4 s) jusqu'à `timeout` (défaut 180 s) ou `status=="ready"` | Interne, une seule tâche captcha à la fois | Interne Python |
| Retry SMS 5sim | `Survey/fivesim_client.py::poll_sms_code` (85-106) | Jusqu'à `max_attempts=12`, `interval_sec=5` (60 s au total), tant que `status != "RECEIVED"` | Interne, retourne `None` si budget épuisé (pas de propagation d'erreur bloquante observée dans le périmètre lu) | Interne Python |
| Restart externe NSSM | Voir §2.1 | Tout code de sortie non explicitement mappé à `Exit` | Nouveau process OS complet (nouveau PID, nouvelle allocation mémoire, nouveau Chrome) | Externe (service Windows) |
| Restart externe zombie | Voir §2.3 | Heartbeat périmé (> 300 s) | `nssm restart` — kill puis relance, nouveau process OS | Externe |
| Wake externe post-cooldown | Voir §2.2 | Cooldown Postgres expiré + service arrêté | `nssm start` — nouveau process OS | Externe |
| Auto-update puis relance (cas particulier — ni un crash ni une pause) | `update_checker.py::check_and_apply` (274-340), `_replace_source_and_restart` (234-271), appelé par `main.py:827-828` (avant toute logique bot) et `launch.py::run_main_loop` (541-542, entre deux cycles) | Version distante (`manifest.json` sur Cloudflare R2) différente de `BOT_VERSION` locale (`_license_config.py`), SHA256 du zip téléchargé validé | Remplace le dossier `code\` (`os.rename` code→code.old puis staging→code) puis `os.execv(python_exe, [python_exe, main_py, …])` — **remplace le process en place**, ne passe ni par NSSM ni par `bot_supervisor` | Interne Python (`os.execv`), mais produit un effet équivalent à un redémarrage complet sans jamais rendre la main à l'OS/NSSM entre-temps |

---

## 3. Retour à TopSurveys / source

### 3.1 Retour normal (fin de sondage tiers complétée avec succès)

| Nom court | Fichier(s) / fonction(s) | Déclencheur exact | Action résultante | Niveau | Impact scheduling |
|---|---|---|---|---|---|
| Détection du retour sur le domaine plateforme (avant chaque page) | `Survey/survey_solver.py::solve_full_survey` (643-651, 821-830) | `platform.is_on_platform(page)` vrai (URL contient `topsurveys.app`) **et** `platform.handle_post_survey(page, account_id)` retourne `True` | `raise TopSurveysReturn()` — exception qui hérite de `BaseException` (traverse les `except Exception` sans être avalée), interceptée dans `preselection/survey_handler.py::_run_survey_impl` (563-566) par `except TopSurveysReturn: driver = _resync_live_page(driver); continue` | Interne Python | Le process reste vivant (pas de libération de slot), reboucle sur la sélection d'un nouveau survey via la boucle de présélection |
| Implémentation TopSurveys de `handle_post_survey` — popup "Bon travail !" (disqualification simple, sans mystery box) | `Survey/functions.py::_handle_topsurveys_exclusion_popup` (58-227), branché via `platforms/topsurveys.py::handle_post_survey` (29-31) | Texte de page contenant `"bon travail"`/`"tu as partiellement repondu"`/`"credite ton compte"` (normalisé sans accents), et absence de mystery box | Ferme le popup (bouton `ps-common-actions-button` ou recherche texte `"compl"`), retente `_payout_and_check_daily_stop` en aval (§3.2/§1.4), puis `survey_navigator.go_to_best_value_survey()` | Interne Python | Idem (process vivant) |
| Implémentation TopSurveys — mystery box présente | `Survey/functions.py:86-113` | `[data-test-id^='ps-mystery-box-item-button']` présent | Sélection automatique via `survey_navigator._handle_mystery_box_popup` (ouvre uniquement la 3ᵉ boîte, clique « Complète »), puis retrait `_payout_and_check_daily_stop`, puis navigation vers un nouveau survey | Interne Python | Idem |
| Fin de sondage sans redirection explicite (aucun bouton Participer/Ok) | `preselection/survey_handler.py::_run_survey_impl` (588-590) | `click_participer_if_qualified` et `handle_disqualification_and_retry` retournent tous deux False | `print("ℹ️ Aucun bouton Participer ou Ok détecté. Fin de boucle."); break` — sort de la boucle `while True` de `_run_survey_impl`, la fonction se termine normalement | Interne Python | Le process reste vivant ; retour normal au niveau de l'appelant (`launch.py::run_main_loop`/`soft_restart_resume`) |
| Aucun élément actionnable en cours de résolution (fin de sondage tiers) | Voir §1.6 « Aucun élément actionnable → survey terminé » | `_has_actionable_elements(page)` False, pas de succès juste précédent | `_survey_ctx.flush()` puis `request_survey_restart("survey_end")` | Interne Python | Soft-restart récursif — traité identiquement à une anomalie du point de vue du mécanisme (pas de distinction de code entre « fin normale » et « soft-restart pour anomalie » à ce niveau, voir Observations) |

### 3.2 Retour anticipé (disqualification, quota rempli, erreur, screenout)

| Nom court | Fichier(s) / fonction(s) | Déclencheur exact | Action résultante | Niveau | Impact scheduling |
|---|---|---|---|---|---|
| Disqualification en phase de présélection TopSurveys (action GPT) | `preselection/survey_handler.py::_run_survey_impl` (442-457) | `question_analyzer.get_response_for_question` retourne `answer={"action":"DISQUALIFIED", ...}` | `flush_disqualified(session)` (persistance mémoire inter-bots, `State/survey_memory.py`), nouvelle `SurveySession()`, `handle_disqualification_and_retry`, puis `go_to_best_value_survey` (nouveau survey) | Interne Python | Process vivant, reboucle sur sélection |
| Disqualification en présélection (détection centralisée texte) | `preselection/survey_handler.py:472-489`, `preselection/question_validation.py::detect_disqualification_reason` (55-128) | Motifs FR/EN normalisés (« pas qualifié », « not eligible », « screened out », « disqualified », etc.) trouvés dans le texte de la question ou de la page | Identique à la ligne précédente | Interne Python | Idem |
| Disqualification détectée sur la page de résolution du sondage tiers (DOM) | Voir §1.6 « Page de disqualification/fin détectée » | `_detect_disqualification_page` (callback iframe ou texte `_DISQ_TEXT_SIGNALS`) | `request_survey_restart(f"disqualification_page:{signal}")` (1ʳᵉ détection) | Interne Python | Soft-restart récursif — retour implicite vers TopSurveys via la boucle englobante |
| Carte survey bloquée après échec de réponse répété | `preselection/survey_handler.py::_skip_card_and_retry` (335-353) | Une réponse appliquée par `execute_response` échoue (`success=False`) | Marque le survey courant comme bloqué (`mark_last_selected_survey_as_blocked`), retrait best-effort (`_payout_and_check_daily_stop`, une seule fois par session via `_cashout_done`), navigue vers la carte suivante (`go_to_best_paid_survey`) | Interne Python | Idem — pas de retour "source" au sens propre, mais changement de survey ciblé sans passer par un arrêt process |
| Budget de cartes bloquées épuisé | `preselection/survey_handler.py:344-349` | `_card_retry_count >= _MAX_CARD_RETRIES` (20) | `_restart(reason)` → délégation `on_soft_restart` (soft-restart récursif) ou fallback `launch.soft_restart` direct | Interne Python | Soft-restart récursif |
| Toutes les cartes candidates flaggées bloquantes | `preselection/survey_navigator.py::_select_best_value_card` (382-399), `_retry_flagged_cards_by_question` (308-341) | Chaque carte visible porte un UUID déjà marqué bloquant (`_excluded_survey_uuids`) | Tentative de déblocage conditionnel : réouverture du popup de chaque carte flaggée, comparaison de la première question à celle mémorisée au moment du flag ; si le contenu a changé (renouvellement), déblocage et sélection ; sinon, en dernier recours, sélection **sans exclusion** (`filtered = candidates`) | Interne Python | Pas d'arrêt — dégradation progressive de la stratégie d'exclusion |
| Quota atteint sur une question de type ranking (Confirmit) | `Survey/action_dispatcher.py:3108-3161` (référencé, hors lecture approfondie car DOM-interaction pure) | `cf-ranking-answer--disabled` sur les items restants | Traité comme un succès de sélection (pas un arrêt) — mentionné ici uniquement parce que le mot « quota » y apparaît dans un sens DOM (quota de rang atteint pour CETTE question), sans rapport avec le quota de licence (§1.4) ni le quota TopSurveys/screenout | Interne Python | Aucun — comportement normal de la question, pas une condition d'arrêt |

---

## 4. Autres sources de stop identifiées (hors 3 catégories ci-dessus)

| Nom court | Fichier(s) / fonction(s) | Déclencheur exact | Action résultante | Niveau | Impact scheduling |
|---|---|---|---|---|---|
| Alerte Telegram — supervision passive uniquement | `Management/notifier.py::send_telegram` (11-31), appelée depuis `runtime_guard.py` (`_notify`, `_check_ecs_stop_desync`), `main.py` (seuil crash-loop), `Cash/payout.py` (échecs de retrait) | Divers (voir lignes citées) | Envoi d'un message Telegram (best-effort, `requests` ou fallback `urllib`) — **ne déclenche jamais d'action corrective automatique en retour** ; c'est un canal d'information sortant uniquement, pas un canal de contrôle entrant | Interne Python | Aucun — confirmé : aucune lecture de commandes Telegram entrantes n'a été trouvée dans tout le projet (recherche ciblée) |
| Fichier de contrôle / kill switch manuel | — | — | **Non trouvé.** Recherche ciblée (motifs `KILL_SWITCH`, `kill_switch`, `FAILURE_PIPELINE_TRIGGER_FILE` — ce dernier cité comme variable exclue dans `DEPLOIEMENT_BAREMETAL_DECISIONS.md` §2 mais absent de toute référence `os.getenv` dans le code actuel) sans résultat | — | — | — |
| Signal manuel `SIGUSR1` (dump debug, pas un arrêt) | `launch.py::install_sigusr1_handler` (125-144) | `kill -SIGUSR1 <pid>` (non supporté sous Windows — le handler s'enregistre uniquement si `hasattr(signal,"SIGUSR1")`, absent sous Windows selon le commentaire du code) | Affiche l'état du `SurveyContext` actif (`ctx.print_debug()`) sans interrompre le bot | Interne Python | Aucun — confirmé non applicable en prod bare-metal Windows (seul le serveur HTTP de debug, actif uniquement en mode attach, joue un rôle équivalent, `launch.py::start_debug_http_server`, 424-461) |
| Limite de mémoire/ressource explicite | — | — | **Non trouvée dans le code applicatif.** Aucune vérification `psutil`/RSS/mémoire n'a été localisée. La seule contrainte de ressource observée est indirecte : `MAX_ACTIONS_PER_PLAN` (`Survey/action_dispatcher.py:7432-7449`, défaut 60) plafonne le nombre d'actions DOM appliquées par plan GPT — mais le code désactive lui-même ce plafond dès qu'il serait susceptible de s'appliquer (voir Observations) | — | — | — |
| Détection de fin de journée ouvrée fixe | — | — | **Non trouvée** — voir §1.4, seul le `DAILY_RESET` basé sur le gain cumulé existe | — | — | — |
| Variable d'environnement de désactivation globale de la vérification de mise à jour | `update_checker.py:280-281`, `config.py`/`global_config.py` (`UPDATE_CHECK_ENABLED`) | `UPDATE_CHECK_ENABLED != "1"` | `return` immédiat, aucune tentative réseau | Interne Python | N'affecte pas le cycle normal du bot — désactive uniquement l'auto-update, pas un stop/pause du survey |
| Détection de session active au démarrage (évite un login inutile, pas un stop) | `launch.py::init_session_and_enter_surveys` (463-522) | Sélecteur `[data-test-id='surveys-nav']` présent en `state="attached"` sous 8 s | Saute l'étape de login, poursuit directement vers la sélection de survey | Interne Python | Aucun — optimisation de démarrage, mentionnée ici pour exhaustivité car elle conditionne un embranchement du flux de démarrage |
| Mode CLI `--query-cooldown` (point d'entrée alternatif, pas un cycle bot) | `main.py:8-25` | `sys.argv[1] == "--query-cooldown"` | Court-circuite tout le reste de `main.py` (pas de `load_config`, pas de `check_license_or_exit`, pas de navigateur) : lit l'état local/Postgres pour chaque `account_id` donné en argument, imprime un JSON, `sys.exit(0)` | Interne Python, invoqué exclusivement par `wake_scheduler.ps1` (externe) | N'est pas un cycle de bot — c'est une requête ponctuelle sans effet de bord sur l'état du compte |
| `_check_ecs_stop_desync` — vestige d'architecture cloud (Fly.io/ECS) toujours actif | `Management/guards/runtime_guard.py::_check_ecs_stop_desync` (277-306), appelé toutes les 30 s par `_monitor_loop` | `state.get("ecs_stop_requested")` vrai (positionné par `_make_stop_handler` sur CHAQUE arrêt SIGINT/SIGBREAK, y compris en bare-metal) et `now - stop_ts > 60` s sans que le process ait réellement quitté | Alerte Telegram : « BOT TOUJOURS ACTIF APRÈS ARRÊT ECS … Action recommandée : kill forcé de la task ECS » | Interne Python | Le message d'alerte référence une action (« task ECS ») qui n'a plus de sens en bare-metal Windows — voir Observations ; le mécanisme de détection lui-même (un arrêt demandé mais jamais effectif après 60 s) reste potentiellement pertinent |

---

## Observations

Zones d'ombre, incohérences et risques repérés pendant la lecture. Aucun correctif proposé —
recensement factuel uniquement, conformément au périmètre de cet audit.

1. **`PROJECT_ARCHITECTURE.md` cité dans la consigne n'existe pas dans le dépôt** (recherche
   exhaustive, insensible à la casse). Seuls `Survey/BOT_EVOLUTION_MEMORY.md` et les trois
   documents `Utils/*.md` existent. Cet audit s'est donc appuyé sur ces quatre documents plus la
   lecture directe du code, sans cartographie d'architecture préexistante à confronter.

2. **`launch_all.ps1` existe toujours dans le dépôt** (239 lignes, contenu cohérent et à jour —
   il référence le layout `code\` post-migration Nuitka→source, les mêmes conventions
   d'environnement que `nssm_setup_bot.ps1`) **alors que `Utils/ORCHESTRATION_TRACKING.md` §9
   affirme explicitement qu'il a été « Supprimé (13/07/2026) » et que « le fichier n'existe plus
   dans le projet »**. Soit le fichier a été réintroduit après cette note sans que la note soit
   mise à jour, soit la suppression n'a en réalité pas eu lieu. Je n'ai trouvé aucune tâche
   planifiée Windows dans ce dépôt qui invoquerait effectivement ce script (ni lui, ni
   `wake_scheduler.ps1`, ni `check_zombie_bots.ps1` — les trois ne sont documentés que via des
   commentaires d'en-tête donnant la commande `Register-ScheduledTask` à exécuter manuellement).
   Je ne peux donc pas trancher, à la lecture du code seul, si `launch_all.ps1` tourne
   effectivement en production aujourd'hui, en parallèle ou à la place de NSSM.

3. **Le mécanisme de mise à jour automatique décrit dans `ORCHESTRATION_TRACKING.md` (§6, §11) et
   `DEPLOIEMENT_BAREMETAL_DECISIONS.md` (§4, §5) ne correspond plus au code actuel.** Ces deux
   documents décrivent un binaire unique compilé Nuitka (`surveybot.exe --onefile`) qui se
   remplace lui-même (`_replace_exe_and_restart`, `NUITKA_ONEFILE_BINARY`). Le fichier
   `update_checker.py` actuellement dans le dépôt documente lui-même, dans son propre docstring,
   un **changement de mécanisme** : le bot tourne désormais en Python interprété
   (`python.exe code\main.py`) et l'update remplace un dossier `code\` entier via une archive ZIP
   (`_replace_source_and_restart`, `os.rename` + `os.execv`), pas un exécutable Nuitka. Le dossier
   `dist_nuitka/` existe pourtant toujours dans l'arborescence du dépôt. Je ne peux pas déterminer
   avec certitude, à la seule lecture du code, si le pipeline Nuitka est un vestige inactif ou
   s'il sert encore à un usage parallèle non documenté.

4. **`global_config.py` ne correspond pas à la liste que `DEPLOIEMENT_BAREMETAL_DECISIONS.md`
   (§2, §6, §7) affirme y avoir migré.** Le fichier réellement présent contient `LOG_LEVEL` et
   `LOG_STEP_SUMMARY` (que la décision documentée dit explicitement avoir *exclus* de
   `GLOBAL_CONFIG`), ainsi que `CAPTCHA_PROVIDER` (idem, marqué « à investiguer séparément,
   probablement variable morte » dans le document). À l'inverse, `MAX_MAIN_CYCLES`,
   `ACCOUNT_LOCK_TTL_SEC`, `HEARTBEAT_INTERVAL_SEC`, `HEARTBEAT_JITTER_SEC`,
   `DOM_FRAME_MAX_DEPTH`, `AA_MATRIX_MAX_ROWS`, `AA_SELECTION_LIST_MAX`, `MAX_ACTIONS_PER_PLAN` —
   que la décision documentée dit avoir *retenus* dans `GLOBAL_CONFIG` — sont absents du fichier
   et restent lus directement via `os.getenv(...)` dans le code (`main.py`, `runtime_guard.py`,
   `dom_extractors_misc.py`, `dom_analyzer.py`, `action_dispatcher.py`). Autrement dit, plusieurs
   seuils qui pilotent directement des conditions d'arrêt/relance documentées dans cet audit
   (`MAX_MAIN_CYCLES`, `ACCOUNT_LOCK_TTL_SEC`, `HEARTBEAT_INTERVAL_SEC`) sont, au jour de cet
   audit, modifiables par une simple variable d'environnement au lancement du process — ce qui
   est exactement le scénario que la protection `GLOBAL_CONFIG` était censée exclure pour ces
   variables selon le document de décision.

   **Résolu (25/08/2026) — volet LOG_LEVEL uniquement.** `LOG_LEVEL` retiré de `global_config.py` :
   la déclaration n'était de toute façon lue par aucun consommateur (`preselection/secret_loader.py`
   ne produit aucune clé `log_level`, la réinjection conditionnelle dans `os.environ` par
   `config_loader.py` ne s'exécutait donc jamais). `LOG_LEVEL` reste un `os.getenv` classique,
   pilotable par bot (`accounts.json`, NSSM, `tools/attach_tab.ps1`) — décision actée par
   l'utilisateur pour préserver la capacité de débugger un bot isolément sans recompiler tout le
   parc, conforme à `DEPLOIEMENT_BAREMETAL_DECISIONS.md` §2/§6. Les 4 lecteurs
   `os.getenv("LOG_LEVEL")` dupliqués et incohérents (`preselection/survey_navigator.py`,
   `Survey/survey_executor.py` ×2, `Survey/batch_response_parser.py`, `launch.py`) délèguent
   désormais tous à `Survey/log_utils.py::is_debug()`/`current_log_level()`, seule source de
   vérité. Voir `Survey/BOT_EVOLUTION_MEMORY.md`, entrée « LOG_LEVEL — centralisation sur
   is_debug()/current_log_level() ».
   **Non résolu, toujours ouvert** : `LOG_STEP_SUMMARY` et `CAPTCHA_PROVIDER` restent présents
   dans `global_config.py` malgré leur exclusion documentée ; `MAX_MAIN_CYCLES`,
   `ACCOUNT_LOCK_TTL_SEC`, `HEARTBEAT_INTERVAL_SEC`, `HEARTBEAT_JITTER_SEC`, `DOM_FRAME_MAX_DEPTH`,
   `AA_MATRIX_MAX_ROWS`, `AA_SELECTION_LIST_MAX`, `MAX_ACTIONS_PER_PLAN` restent absents de
   `global_config.py` malgré leur inclusion documentée — aucun des deux volets n'a été traité par
   ce patch.

5. **Le compteur de crash-loop (`bot_supervisor.check_and_record_start`) peut ne jamais voir
   passer certains arrêts, ou les compter à tort.** Deux mécanismes distincts et tous deux
   vérifiés dans le code :
   - (a) `preselection/license_guard.check_license_or_exit()` s'exécute **avant** tout appel à
     `check_and_record_start()` (au niveau module de `main.py`, avant même `def main():`). Un
     échec de licence/quota Postgres provoque donc une boucle de redémarrage NSSM (`AppExit
     Default → Restart`) qui **échappe entièrement** au plafond de 5 redémarrages/10 min et à
     l'alerte `EXIT_FATAL` — rien dans le code ne borne ce cas particulier.
   - (b) Plusieurs chemins de sortie **après** `check_and_record_start()` (donc DANS la fenêtre
     qu'il est censé surveiller) n'appellent jamais `bot_supervisor.record_exit()` avant de
     quitter : `max_main_cycles_reached` (recyclage volontaire et sain du process, §1.1),
     `max_restart_depth_reached`, `browser_launch_failed`, `CHROME_PROFILE_DIR` manquant/introuvable.
     Le sentinel `EXIT_CRASH` écrit par `check_and_record_start()` au tout début du run **reste
     donc en place** pour tous ces cas — au prochain démarrage, `check_and_record_start()` lira ce
     sentinel `EXIT_CRASH` et incrémentera le compteur, qu'il s'agisse d'un véritable crash ou
     d'un recyclage volontaire toutes les 3 itérations (`MAX_MAIN_CYCLES`). Je n'ai pas pu
     déterminer, à la lecture du code seul, si un bot qui recycle normalement toutes les
     3 itérations peut effectivement atteindre 5 comptages dans la fenêtre de 10 minutes (cela
     dépend de la durée réelle de résolution d'un sondage, non observable statiquement) — je
     signale donc le mécanisme exact plutôt que d'affirmer qu'il se déclenche à coup sûr en
     pratique.

6. **Le repli NSSM sur `EXIT_SOFT_RESTART` (code 2) redémarre presque toujours "pour rien" avant
   que `wake_scheduler.ps1` prenne le relais.** Toutes les durées de `PausePolicy` associées à
   `EXIT_SOFT_RESTART` (`SHORT_COOLDOWN`=2 min, `MEDIUM_COOLDOWN`=5 min,
   `MEDIUM_LONG_COOLDOWN`=15 min) sont largement supérieures à `AppRestartDelay` (30 s, NSSM). Le
   process redémarré par NSSM va donc, dans son cycle de démarrage suivant,
   rencontrer `acquire_account_lock_or_exit()` → `try_acquire_cooldown_slot()` avec
   `cooldown_until_ts` encore dans le futur → `sys.exit(0)` immédiat (ce `sys.exit(0)` est un
   appel direct, **hors** du mécanisme `bot_supervisor`/`StopReason` — il ne repasse pas par
   `record_exit`). NSSM ne redémarre pas sur un code 0 (`AppExit 0 = Exit`). Le bot reste donc
   arrêté jusqu'au prochain passage de `wake_scheduler.ps1` (jusqu'à 5 min). Je note ce
   comportement tel que je le déduis de la lecture croisée de `runtime_guard.py`,
   `nssm_setup_bot.ps1` et `account_state.py` — je ne l'ai pas vu documenté ni testé nulle part
   (`ORCHESTRATION_TRACKING.md` §11 confirme lui-même qu'aucun cycle complet n'a encore été validé
   en conditions réelles), donc je le présente comme une déduction du code, pas comme un fait
   observé en production.

7. **Erreur Postgres transitoire à l'acquisition du lock est indiscernable d'un cooldown légitime
   côté appelant.** `State/account_state.py::try_acquire_cooldown_slot` retourne `False` aussi
   bien pour un cooldown réellement actif que pour toute exception de connexion (bloc
   `except Exception as e: … return False`, lignes 485-488). `launch.py::acquire_account_lock_or_exit`
   traite les deux cas de façon identique : `sys.exit(0)`. Une panne Postgres transitoire produit
   donc exactement le même comportement observable (arrêt propre, pas de restart NSSM) qu'un
   cooldown normal — sans qu'aucun signal ne distingue les deux cas dans les journaux consultés
   par l'opérateur au niveau de cet appel.

8. **`captcha/datadome_handler.py` appelle une fonction qui n'existe nulle part dans le
   projet.** `save_datadome_cookie` est importé depuis `State.account_state`
   (`datadome_handler.py:182-183`) mais n'est défini ni dans `State/account_state.py` (lu
   intégralement) ni ailleurs dans le dépôt (recherche exhaustive). Chaque appel lève donc un
   `ImportError`, systématiquement avalé par un `except Exception` englobant
   (`datadome_handler.py:179-186`) et journalisé uniquement en `log_debug` (invisible en niveau
   `INFO` par défaut). La persistance du cookie DataDome pour les sessions futures est donc
   totalement inopérante malgré une colonne dédiée (`datadome_cookies JSONB`) ajoutée à cet effet
   dans `State/account_state.py::_pg_ensure_table` (ligne 172). Sans impact sur les conditions
   d'arrêt/relance elles-mêmes (le retour `True`/`False` de `solve_datadome_auto` n'est pas
   affecté par cet échec, qui survient après), mais c'est une fonctionnalité de contournement
   anti-bot silencieusement morte.

9. **`MAX_ACTIONS_PER_PLAN` (garde-fou documenté comme "cap sécurité") ne peut, tel qu'écrit,
   jamais tronquer un plan.** `Survey/action_dispatcher.py:7442-1447` : si le nombre d'actions
   dépasse `MAX_ACTIONS_PER_PLAN` (défaut 60), le code affiche un message expliquant qu'il ne
   tronque pas, puis **réassigne `max_actions = len(actions)`** avant le slicing — ce qui rend le
   slicing (`actions[:max_actions]`) sans effet dans ce cas précis. Le commentaire qui présente
   cette variable comme un « cap sécurité (évite un flood si OpenAI hallucine) » ne correspond
   donc plus au comportement réel du code juste en dessous.

10. **`platforms/ysense.py` est un stub partiellement implémenté** (`select_survey`,
    `handle_post_survey`, `is_on_platform` lèvent toutes `NotImplementedError`). Si
    `PLATFORM=ysense` était un jour positionné (variable actuellement figée à `"topsurveys"` dans
    `global_config.py`, donc non atteignable en build compilé standard), le premier appel à l'une
    de ces méthodes ferait planter le bot avec une exception non gérée par le code applicatif
    autour — ce chemin n'est protégé par aucune des conditions d'arrêt documentées ci-dessus (pas
    de `try/except` dédié autour des appels `platform.select_survey`/`platform.handle_post_survey`
    dans `launch.py`/`Survey/survey_solver.py`).

11. **Deux horloges de heartbeat séparées, à des seuils différents, sans lien explicite dans le
    code.** Le heartbeat local (fichier `.state`, lu par `check_zombie_bots.ps1`, seuil 300 s) et
    le heartbeat Postgres (`touch_heartbeat`, prolonge `cooldown_until_ts` de `ACCOUNT_LOCK_TTL_SEC`
    = 240 s par défaut) sont écrits par le **même** appel `RuntimeGuard.heartbeat()` mais
    n'utilisent pas le même seuil de péremption (300 s vs 240 s) ni le même mécanisme de detection
    de staleness (comparaison directe de timestamp côté PowerShell vs condition SQL
    `status='running'` côté Postgres). Je note la coexistence de ces deux seuils différents sans
    trancher si l'écart de 60 s entre eux est intentionnel.

12. **Le message d'alerte de `_check_ecs_stop_desync` référence une infrastructure qui n'existe
    plus.** Le texte de l'alerte Telegram recommande explicitement un « kill forcé de la task
    ECS » (`runtime_guard.py:298`) — vocabulaire hérité de l'ancienne architecture Fly.io/ECS
    (`ORCHESTRATION_TRACKING.md` §1 confirme l'abandon de cette architecture). En bare-metal
    Windows, l'action équivalente serait un `nssm stop`/`taskkill`, jamais mentionnée. Le
    mécanisme de détection sous-jacent (un `ecs_stop_requested` resté vrai plus de 60 s après un
    signal d'arrêt) reste techniquement opérant puisque `_make_stop_handler` écrit toujours ces
    champs sur chaque SIGINT/SIGBREAK — seul le texte de l'alerte est obsolète.

13. **`except KeyboardInterrupt` dans `preselection/survey_handler.py::_run_survey_impl`
    (597-600) — accessibilité en prod non tranchée.** `main.py::install_sigint_handler` enregistre
    un handler `signal.signal(signal.SIGINT, …)` qui intercepte SIGINT avant que l'interpréteur ne
    lève une `KeyboardInterrupt` standard, et convertit systématiquement l'arrêt en
    `SystemExit(EXIT_VOLUNTARY)`. Je n'ai pas identifié, dans le périmètre lu, de chemin qui
    lèverait explicitement `KeyboardInterrupt` une fois ce handler installé (hors mode
    attach/local avec `input()` bloquant, où c'est le seul cas confirmé — `survey_solver.py:599`,
    `preselection/survey_navigator.py:64-65`). Ce bloc `except` pourrait donc être du code mort en
    contexte NSSM/prod strict ; je ne l'affirme pas car je n'ai pas tracé tous les appelants
    possibles de `run_survey()` en dehors du périmètre lu.

14. **Risque de concurrence entre le thread de monitoring et le thread principal sur le même
    driver Playwright, confirmé par le commentaire du code lui-même.** Le compteur
    `_restart_tl` (`preselection/survey_handler.py:79-86`) est explicitement documenté comme
    `threading.local()` "car un soft_restart peut relancer run_survey() depuis n'importe quel
    thread (ex : on_soft_restart déclenché par le RuntimeGuard en background)". Ceci confirme que
    `RuntimeGuard._monitor_loop` (thread démon séparé, `runtime_guard.py:78-82`) peut invoquer
    `on_soft_restart` → `launch.soft_restart` → `run_survey()` **pendant que** le thread principal
    est potentiellement encore en train d'exécuter sa propre instance de `run_survey()`/
    `solve_full_survey()` sur le même objet `driver`/page Playwright. Je n'ai pas trouvé, dans le
    périmètre lu, de verrou explicite empêchant deux exécutions concurrentes de piloter la même
    page en parallèle dans ce scénario — je signale la possibilité telle que le code la documente
    lui-même, sans avoir observé de symptôme concret dans les logs (aucun log fourni pour cet
    audit).

15. **`Dockerfile` et `fly.toml` (racine du projet) documentent un déploiement Fly.io/Docker qui
    semble intégralement remplacé par le bare-metal Windows** (Xvfb, `SURVEY_BROWSER_BIN=/usr/bin/google-chrome`,
    variables `DISPLAY`/`PYTHONUNBUFFERED` explicitement listées comme "sans équivalent bare-metal"
    dans `DEPLOIEMENT_BAREMETAL_DECISIONS.md` §2). `fly.toml` à la racine du projet
    surveybot concerne en réalité l'app `surveybot-db` (le Postgres, toujours hébergé sur Fly.io
    d'après `DEPLOIEMENT_BAREMETAL_DECISIONS.md` §8) et non le bot lui-même — sa présence à cet
    endroit peut prêter à confusion sur le périmètre qu'il couvre réellement.

16. **Aucun mécanisme de kill-switch par fichier de contrôle, variable d'environnement dédiée, ou
    commande Telegram entrante n'a été trouvé.** Je le mentionne explicitement (plutôt que de
    l'omettre) car la consigne de l'audit demandait spécifiquement de vérifier ce type de
    mécanisme. Le seul canal de supervision Telegram identifié est sortant (alertes), jamais
    entrant (aucune lecture de messages/commandes Telegram dans le code lu).

17. **`Survey/dom_analyzer.py` et `Survey/dom_extractors_misc.py`** (4751 et 13150 lignes) ont été
    passés au grep exhaustif sur tous les motifs d'arrêt/relance identifiés ailleurs dans le code
    (`SystemExit`, `guard.pause`, `request_survey_restart`, `signal_strict_survey`, `StopReason`,
    `sys.exit`, `os._exit`) : **aucune occurrence**. Je le mentionne explicitement pour confirmer
    que ces deux fichiers volumineux n'ont pas été superficiellement écartés mais bien vérifiés
    négatifs pour le périmètre de cet audit.
