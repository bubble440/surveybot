from __future__ import annotations

"""Orchestrateur autofix — lancement automatique de Claude Code (mode headless)
puis enchaînement des phases existantes (8, 9, 11-A, 12, 13) jusqu'à la
notification humaine, pour jusqu'à --max-cases cases éligibles, UN À LA FOIS
(jamais en parallèle dans ce module — cf. RÈGLES STRICTES ci-dessous).

N'orchestre que des phases déjà écrites et déjà validées séparément
(Survey/autofix/static_validator.py, Survey/autofix/patch_replay.py,
Survey/autofix/extractor_integrity_gate.py, Survey/autofix/confidence_score.py,
Survey/autofix/human_review.py, Survey/autofix/parallel_safety.py, et — pour l'étape amont
ci-dessous — Survey/autofix/fleet_case_import.py, Survey/autofix/failure_diagnosis.py,
Survey/autofix/case_grouping.py, Survey/autofix/context_selector.py,
Survey/autofix/prompt_generator.py, Survey/autofix/autofix_worktree.py, et — pour l'étape
aval — Survey/autofix/patch_commit.py, Survey/autofix/merge_executor.py) — importées et
appelées TELLES QUELLES, jamais réimplémentées ni modifiées (Survey/autofix/
human_review.py excepté : une petite fonction ADDITIVE, send_status_notification,
y a été ajoutée pour l'étape aval — cf. section dédiée). Ne touche à aucun
extracteur ni stratégie de dispatch.

Depuis l'introduction de l'étape amont (cf. section dédiée ci-dessous), la
Phase 7 (préparation du worktree, Survey/autofix/autofix_worktree.py) EST déclenchée
par ce module, par défaut, avant l'étape ci-dessous — sauf --no-upstream, qui
restaure exactement le comportement antérieur (worktree.json/prompt.txt déjà
présents restent une précondition satisfaite manuellement en amont). La
section "Éligibilité d'un case" ci-dessous décrit l'étape EXISTANTE
(inchangée) qui part de worktree.json déjà présent.

Depuis l'introduction de l'étape aval (cf. section dédiée ci-dessous), l'ordre
réel d'une invocation est : (0) verrou d'exclusion, (1) étape AVAL (décision
Telegram Phase 13 -> commit -> merge local, sauf --no-downstream), (2) étape
AMONT (sauf --no-upstream), (3) étape EXISTANTE ci-dessous. L'aval passe avant
l'amont pour que les correctifs déjà mergés soient présents dans le dépôt
quand l'amont diagnostique de nouveaux cases. Chaque étape découvre donc
naturellement les artefacts que la précédente vient de produire dans la même
invocation (l'amont trouve les worktrees fraîchement créés par une Phase 7
manuelle ou par une invocation antérieure ; l'existante trouve les worktrees
que l'amont vient de créer).

── Éligibilité d'un case (toutes les conditions ensemble) ────────────────────
autofix_worktrees/<case_id>/worktree.json existe (Phase 7 déjà faite) ; ET
prompts/<case_id>/prompt.txt existe RÉELLEMENT (jamais
MANUAL_REVIEW_REQUIRED.txt à sa place) ; ET codex_runs/<case_id>/
run_result.json N'EXISTE PAS ENCORE. Cases triés par case_id (ordre
lexicographique — les case_id de ce pipeline sont des horodatages, donc cet
ordre est aussi chronologique), les --max-cases premiers retenus pour cette
invocation. Aucune tentative de réordonnancement plus intelligent au-delà de
ce tri déterministe : un case durablement bloqué par le contrôle de
parallélisme (point 1 ci-dessous) resterait en tête à chaque invocation —
accepté explicitement, cf. Survey/autofix/parallel_safety.py ("l'ordre de traitement
[...] reste une décision humaine").

── Conséquence disclosée de la règle d'éligibilité ────────────────────────────
run_result.json, une fois écrit (succès OU échec de l'invocation Claude
Code), rend le case définitivement non éligible à une reprise AUTOMATIQUE par
une future invocation de ce module. La seconde tentative bornée d'un échec
informatif se déroule dans la même invocation, avant sa synthèse finale.
Une reprise du même case lors d'une invocation ultérieure reste MANUELLE : soit un
humain supprime codex_runs/<case_id>/ après investigation, soit — si
l'invocation Claude Code avait réussi mais qu'une phase suivante a échoué ou
que ce process a été interrompu en cours de chaîne — un humain relance
directement les façades CLI existantes (tools/validate_patch_static.py,
tools/replay_patch.py, tools/check_extractor_integrity.py,
tools/score_patch_confidence.py, tools/notify_human_review.py) sur les
artefacts déjà écrits sur disque : rien n'est perdu, seule l'automatisation
de bout en bout s'arrête à l'endroit exact où ce module s'est arrêté.

── Point 1 : contrôle de sécurité du parallélisme (avant tout lancement) ─────
Réutilise Survey/autofix/parallel_safety.py::check_pre_launch_safety (le nom exact
dans le code — pas "check_pre_launch_safety_check" — vérifié en lisant le
module avant d'écrire celui-ci), importée telle quelle, jamais réimplémentée.
Le comportement de ce contrôle lui-même (comparaison au niveau fichier) ne
change pas ; seul l'ensemble des worktrees comparés change (cf. ci-dessous).

Définition resserrée de "en vol" (remplace l'ancienne définition volontairement
large "tout worktree.json présent", documentée comme provisoire dès son
introduction — cf. suite 35 de SURVEYBOT_AUTOFIX_PLAN.md — maintenant que
merge_result.json (Survey/autofix/merge_executor.py) existe) : un worktree n'est "en
vol" que s'il peut encore réellement aboutir à un merge. Un worktree dont
l'issue est définitive ne bloque plus aucun autre case. Est définitive,
et SEULEMENT, l'une de ces conditions, vérifiée sur un artefact réellement lu
et bien formé (jamais devinée) :
  - merge_results/<case_id>/merge_result.json (Phase "merge automatique",
    Survey/autofix/merge_executor.py) : status="MERGED" ou "ALREADY_MERGED" — un
    status="CONFLICT" reste "en vol" (résolution manuelle encore possible) ;
  - human_reviews/<case_id>/decision.json (Phase 13) : decision="REJECTED" ;
  - merge_reviews/<case_id>/decision.json (Phase 16) : decision="REJECTED"
    (confirmation de merge annulée) ;
  - confidence_scores/<case_id>/confidence_score.json (Phase 12) :
    confidence="REJECT" — confidence="MEDIUM" reste "en vol" (un humain peut
    encore trancher) ;
  - autofix_static_validations/<case_id>/validation_static.json (Phase 8) :
    verdict="REJECTED" ;
  - codex_runs/<case_id>/run_result.json (invocation Claude Code de CE
    module) : status différent de "SUCCESS" (invocation échouée, aucun patch
    produit) ;
  - autofix_pipeline_runs/<case_id>/pipeline_run.json : stopped_at="NO_CHANGES"
    avec is_error=false (validation statique ACCEPTED, changed_files vide).
Un artefact absent, illisible, malformé ou dont le champ attendu est absent
laisse le worktree "en vol" — jamais une hypothèse optimiste. Une seule
définition, pas de règles empilées ni de délai d'expiration : un worktree qui
ne remplit AUCUNE de ces conditions reste "en vol" quel que soit son âge.

Pour chaque case candidat, si au moins un autre worktree est "en vol" (au sens
resserré ci-dessus) : compare context_selection.json (Phase 5) du candidat à
celui de chacun de ces autres cases, au niveau fichier (granularité déjà celle
de check_pre_launch_safety). Toute paire non sûre IMPLIQUANT LE CANDIDAT
(jamais une paire non sûre entre deux AUTRES cases en vol, hors de propos ici)
reporte ce case : il n'est pas lancé cette fois, signalé explicitement — avec
le ou les case_id qui bloquent et l'état lu de chacun — dans
autofix_pipeline_runs/<case_id>/pipeline_run.json et dans le résumé imprimé,
mais reste éligible à une invocation future (aucun codex_runs/ écrit pour un
case reporté). Une erreur de lecture d'un context_selection.json (candidat ou
en vol) est traitée de la même façon (jamais deviné sûr par défaut) —
ParallelSafetyError capturée et convertie en report.

── Point 2 : invocation Claude Code en mode headless ─────────────────────────
Syntaxe vérifiée dans `claude --help` (CLI réellement installée dans cet
environnement, version 2.1.283) AVANT d'écrire ce module, jamais supposée :
  - Pas de flag --cwd (n'existe pas dans cette version). "worktree_path EXACT"
    est obtenu par le paramètre `cwd=` du sous-processus Python lui-même —
    équivalent fonctionnel exact, seule façon réellement disponible.
  - --allowedTools <tools...> : "Comma or space-separated list of tool names"
    — une seule chaîne espacée est passée ("Read Edit Write Grep Glob" par
    défaut), vérifié fonctionnel par un appel réel (--allowedTools "Read Edit
    Write Grep Glob" a bien limité l'agent à ces outils lors d'un test réel
    dans ce chantier). Ensemble volontairement restreint : ni Bash, ni accès
    réseau/web, ni sous-agents — seuls les outils nécessaires pour lire et
    modifier des fichiers dans le worktree isolé (le prompt Phase 6 demande
    "identifier la cause racine, appliquer un patch minimal", jamais
    d'exécuter des tests — ceux-ci sont déjà couverts séparément par les
    Phases 8/9/11-A qui suivent).
  - --output-format json (exige --print/-p) : un seul objet JSON en sortie
    standard, vérifié par un appel réel — contient au moins
    is_error/subtype/session_id/result/num_turns. Stocké tel quel (sortie
    brute) dans run_result.json ; total_cost_usd et num_turns y sont aussi
    copiés lorsqu'ils sont numériques et valides, sans peser sur la décision.
  - Transmission du prompt : NI un argument positionnel (risque de limite de
    longueur argv/de quoting shell pour un prompt long et multi-lignes), NI un
    fichier temporaire supplémentaire (le prompt existe déjà sur disque,
    prompts/<case_id>/prompt.txt, mais le lire puis le réécrire ailleurs
    n'apporterait rien) : ENTRÉE STANDARD, vérifiée fonctionnelle par un appel
    réel (aucun argument positionnel donné à `claude -p`, prompt fourni via
    subprocess.run(..., input=prompt_text)). Une seule stratégie, comme les
    Phases 8/9 avant ce module.
  - --permission-mode acceptEdits : passé explicitement plutôt que de compter
    sur un défaut ambiant (potentiellement différent d'un environnement à
    l'autre) — accepte automatiquement les opérations d'édition de fichier
    (Edit/Write, déjà seuls listés dans --allowedTools avec Read/Grep/Glob qui
    ne mutent rien), sans ouvrir la porte à des outils non listés. Vérifié
    fonctionnel par un appel réel (Write a réussi sans blocage avec ce mode).
  - --restricted : actif par défaut, désactivable avec --no-restricted ; retire
    les outils de commande et borne les outils de fichiers au worktree.
  - --permission-prompts none : refuse les demandes sans attente interactive.
  - --max-budget-usd 5 : plafond par session ; 0 omet cette option.

Un seul mécanisme d'invocation, un seul essai par session (subprocess.run avec
timeout=claude_timeout_s explicite) — jamais de reprise de session. Un
dépassement de budget (TimeoutExpired) ou un code de sortie non nul arrête la
chaîne ICI pour ce case, jamais les phases suivantes pour LUI, et jamais les
autres cases de cette même invocation (chaque case est indépendant). Le
binaire "claude" est résolu via shutil.which (jamais un chemin codé en dur) ;
son absence sur PATH est un statut ERROR explicite, jamais une exception non
gérée. Écrit systématiquement codex_runs/<case_id>/run_result.json (schéma :
schema_version/case_id/created_at + branch/worktree_path/status/exit_code/
timed_out/session_id/is_error/subtype/command/raw_stdout/raw_stderr/error,
total_cost_usd/num_turns facultatifs),
même convention JSON que les phases précédentes. raw_stdout/raw_stderr sont
bornés (_MAX_RAW_OUTPUT_CHARS) par précaution, même si --output-format json
produit normalement une sortie compacte.

── Point 3 : enchaînement des phases existantes, dans l'ordre imposé ─────────
a. Phase 8 (Survey.autofix.static_validator.write_static_validation) : verdict
   REJECTED (ou StaticValidationError) arrête la chaîne ici pour ce case.
b. Phase 9 (Survey.autofix.patch_replay.write_patch_replay) : continue quel que soit
   l'outcome (CORRECTIF_CONFIRME/BUG_PERSISTANT/NON_CONCLUANT, ou même
   refused=true) — seule une PatchReplayError (précondition d'usage cassée)
   arrête la chaîne ici. Un refused=true est transmis tel quel à la Phase 12,
   qui sait déjà le traiter (CRITERION_INCONCLUSIVE), jamais réinterprété ici.
c. Phase 11-A (Survey.autofix.extractor_integrity_gate.write_extractor_integrity_check).
d. Phase 12 (Survey.autofix.confidence_score.write_patch_confidence),
   live_validation_path=None explicitement (Phase 10 jamais tentée
   automatiquement par ce module — exige un humain avec un vrai navigateur,
   structurellement hors de portée ici, cf. demande d'origine).
e. confidence="HIGH" -> Phase 13 (Survey.autofix.human_review.send_review_request).
   MEDIUM/REJECT arrêtent la chaîne ici. Un MEDIUM dont seule la validation
   live manque déclenche un message de statut après la dernière tentative ;
   les autres restent visibles via confidence_scores/<case_id>/confidence_score.json.
Un verdict statique ACCEPTED avec changed_files=[] s'arrête avant b. à
NO_CHANGES ; une notification simple signale l'issue à l'opérateur.
L'orchestrateur peut ouvrir une nouvelle session dans le même worktree après
un rejet statique imputable au patch ou un rejeu BUG_PERSISTANT exploitable :
--max-attempts=2 par défaut (3 maximum, 1 pour le traitement historique) et
--max-case-budget-usd=8 par défaut (0 désactive ce plafond cumulé). Un coût
inconnu ou moins de 1 USD restant interdit une autre session.

── Sortie : résumé par case ───────────────────────────────────────────────────
Pour CHAQUE case retenu dans cette invocation (traité OU reporté),
autofix_pipeline_runs/<case_id>/pipeline_run.json est écrit avec le point
d'arrêt exact (stopped_at), la raison, les chemins des artefacts déjà produits
par les phases atteintes, et si la notification a eu lieu. CONTRAIREMENT à
tous les autres artefacts de ce chantier, ce fichier est TOUJOURS écrasé sans
garde --force : ce n'est pas un artefact consommé comme précondition par une
autre phase (à la différence de worktree.json/validation_static.json/etc.),
seulement un instantané diagnostique de la dernière tentative de ce module
pour ce case — un case reporté peut légitimement être réexaminé plusieurs
fois avant d'être enfin lancé, et chaque réexamen doit remplacer le résumé
précédent, pas être bloqué par lui. Décision documentée explicitivement ici
plutôt que masquée, en écart volontaire avec la convention par défaut du
reste du chantier.

── RÈGLES STRICTES respectées ────────────────────────────────────────────────
Un seul mécanisme d'invocation Claude Code, jamais de fallback. Budget de
temps explicite sur cette invocation ; les budgets internes des phases
réutilisées (Phases 8/9/11-A) sont déjà les leurs, inchangés, jamais modifiés
ici. Traitement strictement séquentiel des cases retenus — une simple boucle
for Python, jamais un thread/process/async lancé par ce module : le contrôle
de sécurité du parallélisme (point 1) reste un filet de sécurité pour une
parallélisation future, hors périmètre de ce patch. Aucune logique de phase
existante réimplémentée : chaque étape n'est qu'un appel direct à la fonction
déjà écrite et déjà testée de la phase correspondante.

── Point 0 : verrou d'exclusion (acquire_lock/release_lock) ───────────────────
Deux invocations ne doivent jamais s'exécuter en même temps (une session
Claude Code, étape existante, peut durer jusqu'à --claude-timeout-s par case —
des invocations planifiées qui se chevauchent verraient sinon deux Phases 7
préparer le même worktree, ou deux merges tenter la même branche en même
temps). Fichier orchestrator.lock sous --pipeline-runs-root, créé par
os.O_CREAT | os.O_EXCL (exclusion atomique niveau OS, une seule stratégie,
jamais un verrou en mémoire — doit tenir entre deux invocations séparées du
process), contenant horodatage + PID. Verrou déjà présent et non périmé :
OrchestratorLockedError (sous-classe distincte d'AutofixOrchestratorError,
jamais confondue avec une erreur d'usage — code de sortie distinct côté CLI),
jamais une attente bloquante ni un retry. Âge dépassant --lock-stale-after-s
(DEFAULT_LOCK_STALE_AFTER_S, généreux — cf. sa propre justification en tête de
fichier) : repris avec un avertissement (process mort sans nettoyage : kill
-9, crash, coupure). Toujours actif, pour LES TROIS étapes (aval/amont/
existante) — aucune option ne le désactive, contrairement à --no-downstream/
--no-upstream qui ne désactivent que leur étape propre. Toujours libéré en fin
d'invocation, y compris sur exception (try/finally autour du corps de
run_autofix_pipeline).

── Étape aval : de la décision Telegram jusqu'au merge local (run_downstream_stage) ─
Active par défaut, EN PREMIER (avant l'étape amont et l'étape existante), dans
la MÊME invocation. Désactivée entièrement par --no-downstream. La Phase 16
(Survey/autofix/merge_review.py::send_merge_confirmation_request, seconde
confirmation Telegram avant merge) SORT de la chaîne orchestrée : ses modules
et outils (tools/propose_merge.py) restent en place et utilisables à la main,
mais ce module ne les appelle jamais, et aucune option ne rétablit la double
confirmation — un seul "Approuver" (Phase 13) suffit désormais à déclencher
commit puis merge.

1. Relevé des décisions : Survey/autofix/human_review.py::check_pending_reviews
   appelée UNE fois, exactement comme tools/check_human_review.py (mêmes
   arguments — out_root=human_reviews_root, merge_out_root=merge_reviews_root
   — donc même fichier d'offset persisté partagé : _telegram_offset.json sous
   human_reviews_root) : jamais un second poller Telegram (Telegram ne
   fournit qu'UN SEUL flux getUpdates par bot, cf. docstring de
   Survey/autofix/human_review.py). Un HumanReviewError (réseau, credentials Telegram
   absentes) est un avertissement journalisé, jamais un arrêt : les décisions
   déjà écrites sur disque (par une invocation précédente, ou par un humain
   ayant lancé tools/check_human_review.py entre-temps) restent traitées
   normalement ci-dessous.

2. Cases aval éligibles (discover_downstream_candidates, triés par case_id) :
   human_reviews/<case_id>/decision.json (Phase 13) avec decision="APPROVED"
   exactement, ET merge_results/<case_id>/merge_result.json ABSENT — cet
   artefact n'existe que pour une issue terminale (write_merge_result ne
   l'écrit jamais pour un refus opérationnel, vérifié dans le code avant
   d'écrire ce module), sa seule présence suffit donc comme filtre de
   non-retraitement. Un REJECTED, ou l'absence de decision.json (revue encore
   en attente), n'appelle AUCUNE action : ni artefact, ni tentative, ni
   notification.

3. Pour chaque case retenu, séquentiellement (le merge modifie le dépôt
   principal, jamais en parallèle) :
   a. Commit (Phase 15, Survey/autofix/patch_commit.py::commit_patch, réutilisée
      telle quelle) — sauté si commit_results/<case_id>/commit_result.json
      existe déjà (reprise après interruption). diagnosis_dir/
      context_selection_dir TOUJOURS fournis (filet de sécurité BEM actif,
      cf. Survey/autofix/patch_commit.py). Éligibilité (confidence="HIGH",
      decision="APPROVED", cohérence case_id, worktree Git réel) déjà vérifiée
      par commit_patch lui-même via check_commit_eligibility — jamais
      revérifiée séparément ici (contrairement à la Phase 7 côté amont, où
      check_eligibility devait être appelée séparément pour distinguer
      inéligibilité et échec git ; ici PatchCommitError couvre les deux, sans
      distinction nécessaire — cf. point 4).
   b. Merge (Survey/autofix/merge_executor.py::write_merge_result, réutilisée telle
      quelle, AUCUNE modification de ce module). La décision qui l'autorise
      est celle de la Phase 13 (human_reviews/<case_id>/decision.json), PAS
      une decision.json de merge_reviews/ (Phase 16) : vérifié dans le code de
      check_merge_execution_eligibility avant d'écrire ce module — elle ne
      lit que merge_review_dir/decision.json (schema_version/case_id/
      decision/decided_at/telegram_user_id/telegram_username, AUCUN champ
      "kind" qui distinguerait sa provenance) et compare des case_id entre
      artefacts/noms de dossiers, sans jamais supposer que ce dossier
      s'appelle "merge_reviews" — la seule chose qui distingue une décision
      Phase 13 d'une décision Phase 16 est le NOM DU DOSSIER appelant, jamais
      un champ interne. write_merge_result(merge_review_dir=human_reviews_root
      / case_id, ...) est donc appelée directement, sans le moindre changement
      de Survey/autofix/merge_executor.py. Toutes ses gardes restent actives telles
      quelles : source_branch hors PROTECTED_BRANCHES, dépôt principal
      entièrement propre avant tout basculement, branche cible réelle, jamais
      de push, conflit réel -> git merge --abort puis signalement, jamais de
      résolution automatique.
   c. Idempotence (vérifiée dans le code de Survey/autofix/patch_commit.py avant
      d'écrire ce point, cf. consigne) : un worktree entièrement propre au
      moment de commit_patch (rien en attente) est traité comme DÉJÀ
      committé — le sha HEAD courant est rapporté tel quel, jamais un commit
      vide, jamais une erreur — donc une invocation interrompue APRÈS un
      commit Git réel mais AVANT l'écriture de commit_result.json ne produit
      jamais de second commit à la reprise : commit_patch retombe sur ce même
      chemin "déjà committé" et se contente d'écrire (pour la première fois)
      commit_result.json. Le duplicate-check de commit_patch
      (_find_existing_case_commit, PatchCommitDuplicateError) ne s'applique
      qu'à une tentative avec le worktree ENCORE modifié (dirty) — un cas qui
      ne devrait pas survenir dans ce flux (rien ne touche le worktree entre
      la Phase 7 et ce commit) mais reste un garde-fou de
      Survey/autofix/patch_commit.py, non contourné. Côté merge,
      write_merge_result::execute_confirmed_merge cherche lui-même un commit
      déjà marqué "case_id=<id>" sur la branche CIBLE avant de tenter quoi que
      ce soit -> ALREADY_MERGED, jamais un second merge : ce module n'ajoute
      aucune logique d'idempotence supplémentaire ici, la sienne suffit.

4. Issues et reprise (classification faite par ce module, à partir du
   vocabulaire RÉEL vérifié dans Survey/autofix/merge_executor.py :
   status="MERGED"|"ALREADY_MERGED"|"CONFLICT", aucune autre valeur) :
   - MERGED / ALREADY_MERGED : terminal=true, is_error=false (issue conclusive,
     pas un défaut de l'automatisme).
   - CONFLICT : terminal=true POUR CET AUTOMATISME (merge_result.json déjà
     écrit par write_merge_result, qui a déjà exécuté `git merge --abort` —
     jamais de retry automatique, jamais de résolution automatique),
     is_error=false (même raisonnement que MANUAL_REVIEW_REQUIRED côté amont :
     un arrêt légitime nécessitant un humain, pas un bug de ce module) —
     résolution manuelle requise (cf. tools/execute_confirmed_merge.py,
     utilisable à la main sur le conflit une fois résolu autrement).
   - Refus NON terminal (PatchCommitError au commit : dépôt/worktree dans un
     état imprévu ; MergeExecutionError au merge : dépôt principal non propre,
     échec Git, source_branch invalide) : is_error=true, terminal=false —
     retenté à l'invocation SUIVANTE (aucune exclusion dans
     discover_downstream_candidates au-delà de decision.json/merge_result.json,
     cf. point 2), jamais de retry dans la MÊME invocation.
   Chaque case aval écrit autofix_pipeline_runs/<case_id>/downstream_run.json
   (même convention JSON que pipeline_run.json/upstream_run.json —
   schema_version/case_id/created_at/stopped_at/stop_reason/terminal/is_error/
   artifacts, PLUS notified/notified_state, cf. point 5), TOUJOURS écrasé sans
   garde --force (même raisonnement que pipeline_run.json : instantané de la
   dernière tentative, jamais une précondition consommée ailleurs).

5. Notification des issues (Survey/autofix/human_review.py::send_status_notification,
   petite fonction ADDITIVE — vérifié dans le code avant d'écrire ce module
   qu'aucune fonction d'envoi simple sans bouton n'existait déjà ; réutilise
   _telegram_api_call et la résolution telegram_bot_token/telegram_chat_id
   existantes, jamais un second client). UN message concis par CHANGEMENT
   D'ÉTAT parmi trois catégories seulement — merged (MERGED ou ALREADY_MERGED :
   identiques du point de vue de l'opérateur, pour ne jamais perdre la
   notification si une invocation précédente a mergé avec succès mais s'est
   arrêtée avant de notifier), conflict, blocked (tout refus non terminal,
   quelle que soit sa raison précise) — jamais le diff, jamais deux fois pour
   le même état d'un même case : notified_state (downstream_run.json) est relu
   au début du traitement de CE case et comparé à l'état qui vient d'être
   atteint ; identique -> pas de renvoi. Un échec d'envoi
   (send_status_notification lève HumanReviewError) est un avertissement,
   JAMAIS un arrêt — et notified_state N'EST PAS mis à jour dans ce cas
   précis, pour qu'une invocation future retente plutôt que de perdre
   silencieusement la notification. Limite assumée et documentée (jamais
   masquée) : la granularité est le case entier, pas la raison précise d'un
   blocage — un premier blocage (ex. dépôt non propre) puis un second d'une
   autre nature (ex. commit refusé) partagent la même clé "blocked" et ne
   déclenchent donc qu'une seule notification tant que le case reste bloqué,
   quelle que soit l'évolution de la raison exacte.

Limites de l'étape aval à connaître (documentées, pas masquées) :
- Chaque patch a été validé (Phases 8/9/11-A/12) contre l'état D'ORIGINE du
  dépôt, jamais contre les patchs déjà mergés avant lui par cette même étape :
  Git (conflits de lignes au merge) ne détecte aucune incompatibilité de
  LOGIQUE entre deux correctifs qui touchent des zones disjointes.
- Le merge exige un dépôt principal entièrement propre (cf. point 3b) : si
  l'orchestrateur tourne dans le dépôt où l'opérateur travaille, un travail
  local non committé bloque tout merge de cette étape (refus non terminal,
  retenté à l'invocation suivante — jamais un stash automatique).
- tools/report_autofix_metrics.py (Phase 17, lecture seule) ne compte PAS ces
  merges : il lit les décisions de merge_reviews/ (Phase 16), désormais
  contournées par cette étape — hors périmètre de ce patch, à corriger
  séparément si ces statistiques doivent un jour refléter les merges aval.

── Étape amont : de failure_cases/ jusqu'au worktree prêt (run_upstream_stage) ─
Active par défaut, avant l'étape existante ci-dessus, dans la MÊME invocation
(même simple boucle for séquentielle — jamais de thread/process concurrent,
la Phase 7 modifiant l'état Git du dépôt principal). Désactivée entièrement
par --no-upstream, qui restaure alors le comportement exact d'avant cette
extension.

1. Import fleet optionnel (--import-fleet, désactivé par défaut car il exige
   FLEET_R2_* dans l'environnement) : Survey/autofix/fleet_case_import.py::
   import_available_cases(), appelée SANS argument (racine/prefixe/budgets par
   défaut de ce module, non exposés ici — hors périmètre de cette extension).
   Un échec (FleetImportError : credentials R2 absentes ; ou toute autre
   exception, notamment réseau, puisque le listing S3 interne à ce module
   n'est pas lui-même protégé) est un avertissement journalisé (log_info),
   JAMAIS un arrêt de l'invocation.

2. Sélection déterministe des cases amont (discover_upstream_candidates,
   triée par case_id) : dossiers de failure_cases/<case_id>/ (manifest.json
   présent, case_id sûr comme composant de chemin) qui n'ont NI
   autofix_worktrees/<case_id>/worktree.json (Phase 7 déjà faite — le case
   relève alors déjà de l'étape existante ci-dessus), NI
   autofix_pipeline_runs/<case_id>/upstream_run.json avec terminal=true (état
   définitif déjà atteint par une invocation précédente de CETTE étape — cf.
   point 6), ET qui ne sont membres d'AUCUN groupe déjà formé
   (failure_cases/dupgroup_*/group_members.json, cf. point 4 : un membre n'est
   jamais un candidat individuel).

3. Phase 4 (Survey/autofix/failure_diagnosis.py::write_diagnosis) pour chaque
   candidat sans diagnostic ou avec un diagnostic dont l'empreinte du code
   est absente, différente ou marquée dirty. Borné par
   --max-upstream-cases (DEFAULT_MAX_UPSTREAM_CASES) sur le nombre de
   diagnostics nouveaux ou régénérés tentés (succès ou échec) — cf. DEFAULT_MAX_UPSTREAM_CASES
   pour la justification (coût Chromium réel de la Phase 4 pour
   stage="action"). Un candidat sans diagnostic ou avec un diagnostic périmé
   au moment où ce budget est épuisé n'est PAS transmis aux phases suivantes
   cette invocation (aucun upstream_run.json écrit pour lui) : il reste éligible à une invocation
   future, exactement comme un case au-delà de --max-cases pour l'étape
   existante. Un DiagnosisError (échec opérationnel : manifest.json/
   validation_report.json manquant ou invalide) est signalé (upstream_run.json
   terminal=false, is_error=true) — jamais de retry dans cette même invocation.

4. Déduplication, UNE FOIS, après le point 3 et AVANT toute sélection de
   contexte (Survey/autofix/case_grouping.py::write_case_groups, sur ses racines par
   défaut — jamais réimplémentée : déjà idempotente par construction, un
   groupe complet sur disque n'est jamais régénéré ni étendu, vérifié dans
   son code avant d'écrire ce point). Ce regroupement est reporté si un diagnostic
   encore périmé reste sur disque après la Phase 4. Chaque membre d'un groupe FORMÉ (nouveau
   ou déjà gelé, recalculé identique par compute_case_groups) reçoit, s'il
   n'en a pas déjà un, un upstream_run.json terminal=true (stopped_at=
   "deduplication") nommant le group_id qui le remplace désormais — c'est
   cet artefact qui, à l'invocation suivante, l'exclut définitivement de la
   sélection du point 2 (en plus de l'exclusion directe déjà faite via
   group_members.json). Le group_id lui-même est un dossier failure_cases/
   ordinaire une fois formé (manifest.json + diagnosis.json déjà copiés par
   Survey/autofix/case_grouping.py) : il retraverse naturellement la sélection du
   point 2 et poursuit au point 5 comme n'importe quel case. Un
   CaseGroupingError (racine failure_cases/diagnoses introuvable — véritable
   erreur d'usage, jamais un case précis) est un avertissement : les cases
   continuent individuellement, jamais un arrêt de l'invocation.

5. Pour chaque case retenu (sélection du point 2 RECALCULÉE après le point 4,
   filtrée aux seuls cases ayant désormais un diagnosis.json valable pour
   cette invocation — ce qui exclut les candidats reportés faute de budget au point 3
   et inclut les group_id fraîchement formés) : Phase 5
   (Survey/autofix/context_selector.py::write_context_selection), Phase 6
   (Survey/autofix/prompt_generator.py::write_prompt), Phase 7
   (Survey/autofix/autofix_worktree.py::prepare_autofix_worktree) — chacune appelée
   SEULEMENT si son artefact de sortie n'existe pas déjà (context_selection.json/
   prompt.txt ou MANUAL_REVIEW_REQUIRED.txt/worktree.json), pour permettre une
   reprise après interruption sans jamais recalculer un artefact déjà présent.
   Deux arrêts NORMAUX (terminal=true, is_error=false, jamais une exception) :
   MANUAL_REVIEW_REQUIRED.txt écrit par la Phase 6 (case non éligible à un
   prompt automatique) ; case jugé inéligible par
   Survey/autofix/autofix_worktree.py::check_eligibility (nom exact vérifié dans le
   code), appelée SÉPARÉMENT par ce module avant prepare_autofix_worktree —
   seule façon de distinguer une INÉLIGIBILITÉ (stage/verdict/confiance :
   étale les raisons, terminal) d'un ÉCHEC OPÉRATIONNEL git dans
   prepare_autofix_worktree lui-même (AutofixWorktreeError une fois
   l'éligibilité déjà confirmée par ce module : terminal=false, is_error=true,
   jamais de retry automatique — prepare_autofix_worktree ne distingue pas
   ces deux cas par des exceptions différentes, seul un appel préalable à
   check_eligibility le permet).

6. Chaque case touché par les points 3 à 5 écrit
   autofix_pipeline_runs/<case_id>/upstream_run.json (même convention JSON que
   les phases précédentes : schema_version, case_id, created_at, stopped_at,
   stop_reason, terminal, artefacts déjà produits) — TOUJOURS écrasé sans
   garde --force, même raisonnement que pipeline_run.json pour l'étape
   existante (instantané de la dernière tentative, jamais une précondition
   consommée par une autre phase). terminal=true empêche tout retraitement
   AUTOMATIQUE par une invocation future (évite de relancer un Chromium pour
   un résultat déterministe, cf. point 3) ; pour retenter malgré tout :
   suppression manuelle de ce fichier. terminal=false (échec opérationnel)
   n'empêche PAS une invocation future de retenter automatiquement ce même
   case (pas d'exclusion dans discover_upstream_candidates), mais jamais de
   retry AUTOMATIQUE dans la même invocation.

7. --no-upstream désactive entièrement cette étape (comportement exact
   d'avant ce chantier) ; l'étape amont est active PAR DÉFAUT.

Sortie : run_autofix_pipeline() retourne désormais (downstream_summaries,
upstream_summaries, case_summaries) — dans cet ordre, celui de l'exécution
(cf. Point 0) — les trois imprimés par tools/run_autofix_pipeline.py, avec
pour chaque case son point d'arrêt et sa raison (dont les cases aval mergés/en
conflit/bloqués, et les cases amont fusionnés dans un groupe, point 4 de la
section amont).
"""

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from Survey.autofix.autofix_worktree import (
    AutofixWorktreeError, check_eligibility, dom_evidence_relative_dir,
    prepare_autofix_worktree, remove_merged_case_worktree,
)
from Survey.autofix.case_grouping import CaseGroupingError, write_case_groups
from Survey.autofix.confidence_score import (
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_REJECT,
    ConfidenceScoreError,
    write_patch_confidence,
)
from Survey.autofix.context_selector import ContextSelectionError, write_context_selection
from Survey.autofix.extractor_integrity_gate import (
    IntegrityGateError,
    write_extractor_integrity_check,
)
from Survey.autofix.failure_diagnosis import DiagnosisError, get_code_fingerprint, write_diagnosis
from Survey.autofix.human_review import (
    HumanReviewError,
    check_pending_reviews,
    send_review_request,
    send_status_notification,
)
from Survey.log_utils import log_debug, log_info
from Survey.autofix.merge_executor import (
    STATUS_ALREADY_MERGED,
    STATUS_CONFLICT,
    STATUS_MERGED,
    MergeExecutionError,
    write_merge_result,
)
from Survey.autofix.parallel_safety import ParallelSafetyError, check_pre_launch_safety
from Survey.autofix.patch_commit import PatchCommitError, commit_patch
from Survey.autofix.patch_replay import PatchReplayError, write_patch_replay
from Survey.autofix.prompt_generator import (
    MANUAL_REVIEW_FILENAME,
    PROMPT_FILENAME,
    PromptGenerationError,
    _DISPATCH_STEP_RE,
    add_dom_evidence_context,
    write_prompt,
)
from Survey.autofix.static_validator import StaticValidationError, write_static_validation

_TAG = "[AUTOFIX_ORCHESTRATOR]"
SCHEMA_VERSION = "1.0"

DEFAULT_MAX_CASES = 5
DEFAULT_CLAUDE_TIMEOUT_S = 600.0
DEFAULT_ALLOWED_TOOLS = "Read Edit Write Grep Glob"
DEFAULT_PERMISSION_MODE = "acceptEdits"
DEFAULT_MAX_BUDGET_USD = 5.0
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_MAX_CASE_BUDGET_USD = 8.0
_MAX_ATTEMPTS = 3
_MIN_NEXT_ATTEMPT_BUDGET_USD = 1.0
_MAX_FEEDBACK_CHARS = 1200
_MAX_PATCH_FINGERPRINT_FILES = 128
_MAX_PATCH_FINGERPRINT_FILE_BYTES = 1_000_000
_MAX_ATTEMPT_ARTIFACT_BYTES = 8_000_000

# Borne conservatrice, distincte de DEFAULT_MAX_CASES : la Phase 4 (diagnostic)
# lance un vrai Chromium isolé pour tout case stage="action" avec
# real_dispatch_replay (Survey/autofix/replay_browser.py::execute_case_action) — un
# coût par case bien plus élevé qu'un simple calcul JSON. Ne borne QUE le
# nombre de cases NOUVELLEMENT diagnostiqués par invocation (cf. docstring de
# run_upstream_stage) : un case déjà diagnostiqué lors d'une invocation
# précédente continue d'avancer (Phases 5/6/7) sans être compté ici.
DEFAULT_MAX_UPSTREAM_CASES = 3
_MAX_COMPLETED_HISTORY_CASES = 200

# Âge de péremption du verrou d'exclusion (point 0) : généreux, délibérément
# très supérieur au temps maximal théorique d'une invocation avec les valeurs
# par défaut (--max-cases=5 * --claude-timeout-s=600s = 3000s pour les seules
# invocations Claude Code, plus l'étape amont — Chromium isolé, --max-upstream
# -cases diagnostics — et l'étape aval — commit/merge Git locaux, rapides).
# Passé ce délai, un verrou est considéré abandonné (process mort sans
# nettoyage : kill -9, crash, coupure) et repris avec un avertissement plutôt
# que de bloquer indéfiniment les invocations suivantes.
DEFAULT_LOCK_STALE_AFTER_S = 7200.0

# Bornes de stockage pour la sortie brute de l'invocation Claude Code —
# --output-format json produit normalement une sortie compacte, mais jamais
# de croissance non bornée par précaution (même philosophie que
# Survey/autofix/static_validator.py::_check_tests, err.strip()[-4000:]).
_MAX_RAW_OUTPUT_CHARS = 200_000
# Début du texte final de l'agent conservé séparément de la sortie brute
# (8 000 caractères maximum) ; la raison courte garde ses 240 premiers caractères.
_MAX_AGENT_FINAL_TEXT_CHARS = 8_000
_MAX_NO_CHANGES_REASON_CHARS = 240
_MAX_LIVE_STATUS_CHARS = 1000
_MAX_AGENT_FINAL_SUMMARY_CHARS = 200
_AGENT_SUMMARY_TAIL_LINES = 10
_AGENT_SUMMARY_RE = re.compile(r"^\s*r\s*[eé]\s*s\s*u\s*m\s*[eé]\s*:\s*(.*)$", re.IGNORECASE)

STATUS_SUCCESS = "SUCCESS"
STATUS_FAILURE = "FAILURE"
STATUS_TIMEOUT = "TIMEOUT"
STATUS_ERROR = "ERROR"

STAGE_PARALLEL_SAFETY = "parallel_safety"
STAGE_CLAUDE_INVOCATION = "claude_invocation"
STAGE_STATIC_VALIDATION = "static_validation"
STAGE_NO_CHANGES = "NO_CHANGES"
STAGE_CORE_CHANGE_CANDIDATE = "core_change_candidate"
STAGE_PATCH_REPLAY = "patch_replay"
STAGE_EXTRACTOR_INTEGRITY = "extractor_integrity"
STAGE_CONFIDENCE_SCORE = "confidence_score"
STAGE_HUMAN_REVIEW = "human_review"

# Étapes de l'étape amont (failure_cases/ -> worktree prêt), distinctes des
# étapes ci-dessus (qui commencent, elles, une fois worktree.json déjà là).
STAGE_UPSTREAM_DIAGNOSIS = "diagnosis"
STAGE_UPSTREAM_DEDUPLICATION = "deduplication"
STAGE_UPSTREAM_HISTORY_DUPLICATE = "historical_duplicate"
STAGE_UPSTREAM_CONTEXT_SELECTION = "context_selection"
STAGE_UPSTREAM_PROMPT = "prompt_generation"
STAGE_UPSTREAM_WORKTREE_ELIGIBILITY = "worktree_eligibility"
STAGE_UPSTREAM_WORKTREE = "worktree"

# Étapes de l'étape aval (décision Telegram Phase 13 -> merge local).
STAGE_DOWNSTREAM_COMMIT = "commit"
STAGE_DOWNSTREAM_MERGE = "merge"

# Clés d'état pour la garde anti-doublon de notification (point 5) — cinq
# catégories seulement, au niveau du CASE entier, pas par raison précise de
# blocage : un premier blocage (ex. dépôt non propre) puis un second blocage
# d'une autre nature (ex. commit refusé) partagent la même clé "blocked" et ne
# génèrent donc qu'une seule notification tant que le case reste bloqué —
# simplification assumée et documentée (cf. docstring de _maybe_notify),
# jamais masquée. "merged" couvre à la fois MERGED et ALREADY_MERGED
# (identique du point de vue de l'opérateur : le patch est mergé), pour ne
# jamais perdre la notification si une invocation précédente a réussi le
# merge mais s'est arrêtée avant de notifier.
NOTIFY_STATE_MERGED = "merged"
NOTIFY_STATE_CONFLICT = "conflict"
NOTIFY_STATE_BLOCKED = "blocked"
NOTIFY_STATE_NO_CHANGES = "no_changes"
NOTIFY_STATE_LIVE_VALIDATION_PENDING = "live_validation_pending"

# Composant de chemin unique, allowlist conservatrice — même garde-fou que
# Survey/autofix/autofix_worktree.py::_CASE_ID_RE, dupliqué volontairement (modules
# indépendants, cf. convention déjà en place ailleurs dans ce chantier pour
# ce même petit utilitaire).
_CASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
_WINDOWS_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


class AutofixOrchestratorError(Exception):
    """Erreur d'usage bloquante (racine invalide, --max-cases invalide). Jamais
    levée pour l'échec/le report d'un case particulier — cf. CaseRunSummary."""


class AutofixOrchestratorExistsError(AutofixOrchestratorError):
    """codex_runs/<case_id>/run_result.json existe déjà et force=False."""


class OrchestratorLockedError(AutofixOrchestratorError):
    """Refus NON lié à un usage incorrect : une autre invocation détient déjà
    le verrou d'exclusion (point 0) et il n'est pas encore périmé. Sous-classe
    distincte (jamais confondue avec AutofixOrchestratorError générique) pour
    que la façade CLI puisse lui affecter un code de sortie distinct."""


def _is_safe_case_id(case_id: str) -> bool:
    if not case_id or not _CASE_ID_RE.match(case_id):
        return False
    if ".." in case_id:
        return False
    if case_id.lower() in _WINDOWS_RESERVED_NAMES:
        return False
    return True


def _load_json(path: Path) -> "tuple[Any, Optional[str]]":
    if not path.is_file():
        return None, f"{path} absent"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except OSError as exc:
        return None, f"{path} illisible ({exc})"
    except ValueError as exc:  # json.JSONDecodeError est une sous-classe de ValueError
        return None, f"{path} JSON invalide ({exc})"


# ═══════════════════════ Point 0 — verrou d'exclusion ═════════════════════════


LOCK_FILENAME = "orchestrator.lock"


@dataclass
class LockHandle:
    path: Path
    acquired: bool
    stale_reclaimed: bool = False


def _lock_path(pipeline_runs_root: Path) -> Path:
    return pipeline_runs_root / LOCK_FILENAME


def _lock_age_s(created_at: Optional[str]) -> Optional[float]:
    """None si created_at absent/illisible — jamais une hypothèse optimiste
    d'âge (traité comme non périmé par l'appelant dans ce cas, cf.
    acquire_lock)."""
    if not created_at:
        return None
    try:
        created_dt = datetime.fromisoformat(created_at)
    except ValueError:
        return None
    if created_dt.tzinfo is None:
        created_dt = created_dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - created_dt).total_seconds()


def acquire_lock(pipeline_runs_root: "str | Path", *, stale_after_s: float) -> LockHandle:
    """Création exclusive atomique (os.O_CREAT | os.O_EXCL) d'un fichier de
    verrou sous pipeline_runs_root — jamais un verrou en mémoire (doit tenir
    entre deux invocations séparées du process). Un verrou déjà présent et
    dont l'âge dépasse stale_after_s est repris avec un avertissement (process
    mort sans nettoyage) ; sinon, refus explicite via OrchestratorLockedError,
    jamais une attente bloquante ni un retry. Âge illisible/malformé : jamais
    considéré périmé (cohérent avec la convention du reste de ce chantier)."""
    pipeline_runs_root = Path(pipeline_runs_root)
    pipeline_runs_root.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path(pipeline_runs_root)
    payload = json.dumps({
        "pid": os.getpid(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }).encode("utf-8")

    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        data, _err = _load_json(lock_path)
        created_at = data.get("created_at") if isinstance(data, dict) else None
        pid = data.get("pid") if isinstance(data, dict) else None
        age_s = _lock_age_s(created_at)
        if age_s is None or age_s < stale_after_s:
            raise OrchestratorLockedError(
                f"verrou déjà détenu ({lock_path}, pid={pid!r}), âge="
                f"{'?' if age_s is None else f'{age_s:.0f}s'} (péremption à {stale_after_s:.0f}s) — "
                "une autre invocation est probablement en cours"
            )
        log_info(
            _TAG,
            f"avertissement : verrou périmé repris (âge={age_s:.0f}s > {stale_after_s:.0f}s, "
            f"pid précédent={pid!r}) : {lock_path}",
        )
        try:
            lock_path.unlink()
        except OSError as exc:
            raise OrchestratorLockedError(
                f"verrou périmé mais suppression échouée ({lock_path}) : {exc}"
            ) from exc
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise OrchestratorLockedError(
                f"verrou repris par une autre invocation entre-temps ({lock_path})"
            ) from exc
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        return LockHandle(path=lock_path, acquired=True, stale_reclaimed=True)

    with os.fdopen(fd, "wb") as fh:
        fh.write(payload)
    return LockHandle(path=lock_path, acquired=True)


def release_lock(handle: LockHandle) -> None:
    """Toujours appelée en fin d'invocation, y compris sur exception (cf.
    run_autofix_pipeline, try/finally). Best-effort : un échec de suppression
    est un avertissement, jamais une exception supplémentaire qui masquerait
    l'erreur d'origine."""
    if not handle.acquired:
        return
    try:
        handle.path.unlink()
    except OSError as exc:
        log_info(_TAG, f"avertissement : suppression du verrou a échoué ({handle.path}) : {exc}")


# ═══════════════════════ Étape aval : décision Telegram -> merge local ═══════


@dataclass
class DownstreamCaseSummary:
    case_id: str
    stopped_at: str
    stop_reason: Optional[str]
    terminal: bool
    is_error: bool
    notified: bool = False
    notified_state: Optional[str] = None
    artifacts: "dict[str, str]" = field(default_factory=dict)
    warnings: "list[str]" = field(default_factory=list)
    worktree_cleanup: "Optional[dict]" = None

    def as_dict(self) -> dict:
        result = {
            "schema_version": SCHEMA_VERSION,
            "case_id": self.case_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "stopped_at": self.stopped_at,
            "stop_reason": self.stop_reason,
            "terminal": self.terminal,
            "is_error": self.is_error,
            "notified": self.notified,
            "notified_state": self.notified_state,
            "artifacts": self.artifacts,
            "warnings": self.warnings,
        }
        if self.worktree_cleanup is not None:
            result["worktree_cleanup"] = self.worktree_cleanup
        return result


def _downstream_run_paths(out_root: Path, case_id: str) -> "tuple[Path, Path]":
    out_dir = out_root / case_id
    return out_dir, out_dir / "downstream_run.json"


def write_downstream_run_result(
    summary: DownstreamCaseSummary,
    *,
    out_root: "str | Path" = "autofix_pipeline_runs",
) -> Path:
    """TOUJOURS écrasé, sans garde --force — même raisonnement que
    write_pipeline_run_summary/write_upstream_run_result (instantané de la
    dernière tentative de l'étape aval pour ce case). Vit dans le même dossier
    que pipeline_run.json/upstream_run.json (nom de fichier distinct :
    downstream_run.json), sans conflit."""
    out_root = Path(out_root)
    out_dir, out_file = _downstream_run_paths(out_root, summary.case_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(summary.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    log_info(
        _TAG,
        f"case={summary.case_id} [aval] stopped_at={summary.stopped_at!r} "
        f"terminal={summary.terminal} is_error={summary.is_error} notifié={summary.notified} -> {out_file}",
    )
    return out_file


def discover_downstream_candidates(
    *,
    human_reviews_root: Path,
    merge_results_root: Path,
) -> "list[str]":
    """cf. docstring du module ("Étape aval", point 2) : dossiers de
    human_reviews/<case_id>/decision.json avec decision="APPROVED" exactement
    et sans merge_results/<case_id>/merge_result.json déjà écrit (cet artefact
    n'existe QUE pour une issue terminale — MERGED/ALREADY_MERGED/CONFLICT,
    write_merge_result ne l'écrit jamais pour un refus opérationnel, cf.
    Survey/autofix/merge_executor.py — sa seule présence suffit donc comme filtre).
    Un REJECTED, ou l'absence de decision.json, n'est jamais un candidat :
    "n'appelle aucune action" (point 2), ni artefact ni tentative. Triés par
    case_id (ordre déterministe). Lecture seule."""
    if not human_reviews_root.is_dir():
        return []
    out: "list[str]" = []
    for entry in sorted(human_reviews_root.iterdir()):
        if not entry.is_dir():
            continue
        case_id = entry.name
        if not _is_safe_case_id(case_id):
            continue
        decision, _err = _load_json(entry / "decision.json")
        if not isinstance(decision, dict):
            continue
        if str(decision.get("decision") or "") != "APPROVED":
            continue
        if (merge_results_root / case_id / "merge_result.json").is_file():
            continue
        out.append(case_id)
    return out


def _maybe_notify(
    case_id: str,
    *,
    notify_state: str,
    message: str,
    previous_notified_state: Optional[str],
) -> "tuple[bool, list[str]]":
    """N'envoie que si notify_state diffère de previous_notified_state —
    jamais deux fois pour le même état, y compris entre invocations (l'état
    précédent est relu depuis l'artefact de synthèse par l'appelant). Un échec
    d'envoi (HumanReviewError : credentials absentes, réseau) est un
    avertissement retourné à l'appelant, JAMAIS une exception : l'état n'est
    alors PAS marqué notifié (cf. appelant), pour qu'une invocation future
    retente plutôt que de perdre silencieusement la notification. Limite
    assumée et documentée plutôt que masquée (cf. NOTIFY_STATE_*, docstring
    du module) : cinq états au niveau du case entier — un
    changement de RAISON précise à l'intérieur du même état (ex. deux
    blocages non terminaux successifs pour des raisons différentes) ne
    déclenche pas une seconde notification tant que l'état lui-même
    (merged/conflict/blocked/no_changes/live_validation_pending) ne change pas."""
    if notify_state == previous_notified_state:
        return False, []
    try:
        send_status_notification(message)
    except HumanReviewError as exc:
        return False, [f"notification Telegram échouée (avertissement, jamais un arrêt) : {exc}"]
    return True, []


def _process_downstream_case(
    case_id: str,
    *,
    confidence_scores_root: Path,
    human_reviews_root: Path,
    worktrees_root: Path,
    diagnoses_root: Path,
    context_selections_root: Path,
    commit_results_root: Path,
    merge_results_root: Path,
    pipeline_runs_root: Path,
) -> DownstreamCaseSummary:
    """Phases 15 (commit) puis merge (Survey/autofix/merge_executor.py) pour UN case
    déjà APPROVED (Phase 13) — garanti par l'appelant. Chaque étape est
    sautée si son artefact de sortie existe déjà (reprise après interruption,
    cf. docstring du module, point 3c pour l'idempotence exacte)."""
    artifacts: "dict[str, str]" = {}

    previous, _err = _load_json(pipeline_runs_root / case_id / "downstream_run.json")
    previous_notified_state = previous.get("notified_state") if isinstance(previous, dict) else None

    # ── (a) Commit (Phase 15) ──────────────────────────────────────────────
    commit_result_path = commit_results_root / case_id / "commit_result.json"
    if commit_result_path.is_file():
        artifacts[STAGE_DOWNSTREAM_COMMIT] = str(commit_result_path)
    else:
        try:
            commit_patch(
                confidence_score_dir=confidence_scores_root / case_id,
                human_review_dir=human_reviews_root / case_id,
                worktree_dir=worktrees_root / case_id,
                diagnosis_dir=diagnoses_root / case_id,
                context_selection_dir=context_selections_root / case_id,
                out_root=commit_results_root,
            )
        except PatchCommitError as exc:
            notified, warns = _maybe_notify(
                case_id, notify_state=NOTIFY_STATE_BLOCKED,
                message=f"⚠️ autofix case={case_id} : bloqué au commit (Phase 15) — {exc}",
                previous_notified_state=previous_notified_state,
            )
            return DownstreamCaseSummary(
                case_id=case_id, stopped_at=STAGE_DOWNSTREAM_COMMIT, stop_reason=str(exc),
                terminal=False, is_error=True, notified=notified,
                notified_state=(NOTIFY_STATE_BLOCKED if notified else previous_notified_state),
                artifacts=artifacts, warnings=warns,
            )
        artifacts[STAGE_DOWNSTREAM_COMMIT] = str(commit_result_path)

    # ── (b) Merge (Survey/autofix/merge_executor.py) ───────────────────────────────
    merge_result_path = merge_results_root / case_id / "merge_result.json"
    if not merge_result_path.is_file():
        # La décision qui autorise ce merge est celle de la Phase 13
        # (human_reviews/<case_id>/decision.json) — jamais merge_reviews/ (Phase
        # 16, sortie de la chaîne orchestrée). Le schéma de decision.json est
        # identique dans les deux dossiers (schema_version/case_id/decision/
        # decided_at/telegram_user_id/telegram_username, aucun champ "kind" —
        # cf. Survey/autofix/merge_executor.py, docstring, et Survey/autofix/human_review.py,
        # write_pending/decision commun aux deux points d'entrée) : vérifié
        # dans le code de check_merge_execution_eligibility avant d'écrire ce
        # module, elle ne lit que decision.json et compare des case_id, sans
        # jamais supposer qu'elle vit sous merge_reviews/ — appelée ici
        # directement sur human_reviews/<case_id>, sans aucune modification de
        # Survey/autofix/merge_executor.py.
        try:
            write_merge_result(
                merge_review_dir=human_reviews_root / case_id,
                worktree_dir=worktrees_root / case_id,
                commit_result_dir=commit_results_root / case_id,
                out_root=merge_results_root,
            )
        except MergeExecutionError as exc:
            notified, warns = _maybe_notify(
                case_id, notify_state=NOTIFY_STATE_BLOCKED,
                message=f"⚠️ autofix case={case_id} : bloqué au merge — {exc}",
                previous_notified_state=previous_notified_state,
            )
            return DownstreamCaseSummary(
                case_id=case_id, stopped_at=STAGE_DOWNSTREAM_MERGE, stop_reason=str(exc),
                terminal=False, is_error=True, notified=notified,
                notified_state=(NOTIFY_STATE_BLOCKED if notified else previous_notified_state),
                artifacts=artifacts, warnings=warns,
            )
    artifacts[STAGE_DOWNSTREAM_MERGE] = str(merge_result_path)

    merge_data, _err2 = _load_json(merge_result_path)
    status = merge_data.get("status") if isinstance(merge_data, dict) else None

    if status == STATUS_CONFLICT:
        notified, warns = _maybe_notify(
            case_id, notify_state=NOTIFY_STATE_CONFLICT,
            message=f"🔴 autofix case={case_id} : CONFLIT de merge — résolution manuelle requise.",
            previous_notified_state=previous_notified_state,
        )
        return DownstreamCaseSummary(
            case_id=case_id, stopped_at=STAGE_DOWNSTREAM_MERGE, stop_reason="merge_result.json : CONFLICT",
            terminal=True, is_error=False, notified=notified,
            notified_state=(NOTIFY_STATE_CONFLICT if notified else previous_notified_state),
            artifacts=artifacts, warnings=warns,
        )

    if status in (STATUS_MERGED, STATUS_ALREADY_MERGED):
        notified, warns = _maybe_notify(
            case_id, notify_state=NOTIFY_STATE_MERGED,
            message=f"✅ autofix case={case_id} : mergé avec succès.",
            previous_notified_state=previous_notified_state,
        )
        try:
            cleanup = remove_merged_case_worktree(
                case_id=case_id,
                manifest_path=worktrees_root / case_id / "worktree.json",
                integration_branch=str(merge_data.get("target_branch") or ""),
            )
        except Exception as exc:
            # Le retrait est auxiliaire : il ne change jamais l'issue du merge ni sa notification.
            log_debug(_TAG, f"case={case_id} retrait du worktree impossible : {type(exc).__name__}: {exc}")
            cleanup = {"status": "PRESERVED", "reason": f"retrait impossible ({type(exc).__name__}: {exc})"}
        if cleanup["status"] in ("PRESERVED", "PARTIAL"):
            warns.append(f"retrait du worktree : {cleanup['reason']}")
        return DownstreamCaseSummary(
            case_id=case_id, stopped_at=STAGE_DOWNSTREAM_MERGE, stop_reason=f"merge_result.json : {status}",
            terminal=True, is_error=False, notified=notified,
            notified_state=(NOTIFY_STATE_MERGED if notified else previous_notified_state),
            artifacts=artifacts, warnings=warns, worktree_cleanup=cleanup,
        )

    # Statut inattendu — jamais deviné (vocabulaire vérifié dans le code avant
    # d'écrire ce module : seuls MERGED/ALREADY_MERGED/CONFLICT existent).
    return DownstreamCaseSummary(
        case_id=case_id, stopped_at=STAGE_DOWNSTREAM_MERGE,
        stop_reason=f"merge_result.json.status={status!r} inattendu",
        terminal=False, is_error=True, notified=False,
        notified_state=previous_notified_state, artifacts=artifacts,
    )


def run_downstream_stage(
    *,
    human_reviews_root: "str | Path" = "human_reviews",
    merge_reviews_root: "str | Path" = "merge_reviews",
    confidence_scores_root: "str | Path" = "confidence_scores",
    worktrees_root: "str | Path" = "autofix_worktrees",
    diagnoses_root: "str | Path" = "diagnoses",
    context_selections_root: "str | Path" = "context_selections",
    commit_results_root: "str | Path" = "commit_results",
    merge_results_root: "str | Path" = "merge_results",
    pipeline_runs_root: "str | Path" = "autofix_pipeline_runs",
) -> "list[DownstreamCaseSummary]":
    """Point d'entrée de l'étape aval — cf. docstring du module pour le détail
    point par point. Désactivée par --no-downstream côté run_autofix_pipeline
    (cette fonction n'est alors jamais appelée)."""
    human_reviews_root = Path(human_reviews_root)
    merge_reviews_root = Path(merge_reviews_root)
    confidence_scores_root = Path(confidence_scores_root)
    worktrees_root = Path(worktrees_root)
    diagnoses_root = Path(diagnoses_root)
    context_selections_root = Path(context_selections_root)
    commit_results_root = Path(commit_results_root)
    merge_results_root = Path(merge_results_root)
    pipeline_runs_root = Path(pipeline_runs_root)

    # ── Point 1 ──────────────────────────────────────────────────────────────
    try:
        result = check_pending_reviews(out_root=human_reviews_root, merge_out_root=merge_reviews_root)
        log_debug(
            _TAG,
            f"[aval] relevé Telegram : {result.updates_fetched} update(s), "
            f"{len(result.processed)} décision(s) traitée(s)",
        )
    except HumanReviewError as exc:
        log_info(_TAG, f"avertissement : relevé des décisions Telegram (étape aval) échoué : {exc}")

    # ── Points 2/3/4/5 ───────────────────────────────────────────────────────
    candidates = discover_downstream_candidates(
        human_reviews_root=human_reviews_root, merge_results_root=merge_results_root,
    )
    summaries: "list[DownstreamCaseSummary]" = []
    for case_id in candidates:
        summary = _process_downstream_case(
            case_id,
            confidence_scores_root=confidence_scores_root,
            human_reviews_root=human_reviews_root,
            worktrees_root=worktrees_root,
            diagnoses_root=diagnoses_root,
            context_selections_root=context_selections_root,
            commit_results_root=commit_results_root,
            merge_results_root=merge_results_root,
            pipeline_runs_root=pipeline_runs_root,
        )
        write_downstream_run_result(summary, out_root=pipeline_runs_root)
        summaries.append(summary)

    return summaries


# ═══════════════════════ Étape amont : failure_cases/ -> worktree prêt ═══════


@dataclass
class UpstreamCaseSummary:
    case_id: str
    stopped_at: str
    stop_reason: Optional[str]
    terminal: bool
    is_error: bool
    artifacts: "dict[str, str]" = field(default_factory=dict)
    equivalent_case_id: Optional[str] = None
    equivalent_outcome: Optional[str] = None
    retry_hint: Optional[str] = None

    def as_dict(self) -> dict:
        result = {
            "schema_version": SCHEMA_VERSION,
            "case_id": self.case_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "stopped_at": self.stopped_at,
            "stop_reason": self.stop_reason,
            "terminal": self.terminal,
            "is_error": self.is_error,
            "artifacts": self.artifacts,
        }
        if self.equivalent_case_id is not None:
            result["equivalent_case_id"] = self.equivalent_case_id
            result["equivalent_outcome"] = self.equivalent_outcome
            result["retry_hint"] = self.retry_hint
        return result


def _upstream_run_paths(out_root: Path, case_id: str) -> "tuple[Path, Path]":
    out_dir = out_root / case_id
    return out_dir, out_dir / "upstream_run.json"


def write_upstream_run_result(
    summary: UpstreamCaseSummary,
    *,
    out_root: "str | Path" = "autofix_pipeline_runs",
) -> Path:
    """TOUJOURS écrasé, sans garde --force — même raisonnement que
    write_pipeline_run_summary (instantané de la dernière tentative de
    l'étape amont pour ce case, jamais une précondition consommée par une
    autre phase). Vit dans le même dossier que pipeline_run.json (nom de
    fichier distinct : upstream_run.json), sans conflit."""
    out_root = Path(out_root)
    out_dir, out_file = _upstream_run_paths(out_root, summary.case_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(summary.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    log_info(
        _TAG,
        f"case={summary.case_id} [amont] stopped_at={summary.stopped_at!r} "
        f"terminal={summary.terminal} is_error={summary.is_error} -> {out_file}"
        + (f" equivalent={summary.equivalent_case_id} outcome={summary.equivalent_outcome}"
           if summary.equivalent_case_id is not None else ""),
    )
    return out_file


def _group_members_on_disk(failure_cases_root: Path) -> "set[str]":
    """Union des membres de TOUS les groupes déjà formés
    (failure_cases/dupgroup_*/group_members.json) — ces membres ne sont
    jamais des candidats individuels (cf. Survey/autofix/case_grouping.py, "le
    group_id est traité comme un case ordinaire"). Un group_members.json
    absent/illisible pour un dossier dupgroup_* présent est ignoré pour ce
    groupe (aucun membre compté pour lui) plutôt que de bloquer la
    découverte des autres cases — jamais une exception ici."""
    from Survey.autofix.case_grouping import GROUP_ID_PREFIX

    members: "set[str]" = set()
    if not failure_cases_root.is_dir():
        return members
    for entry in failure_cases_root.iterdir():
        if not entry.is_dir() or not entry.name.startswith(GROUP_ID_PREFIX):
            continue
        data, _err = _load_json(entry / "group_members.json")
        if isinstance(data, dict):
            for m in data.get("members") or []:
                if isinstance(m, str):
                    members.add(m)
    return members


def discover_upstream_candidates(
    *,
    failure_cases_root: Path,
    worktrees_root: Path,
    pipeline_runs_root: Path,
    retry_previous_failures: bool = False,
) -> "list[str]":
    """cf. docstring du module ("Étape amont", point 2) : dossiers de
    failure_cases/ (manifest.json présent, case_id sûr) sans worktree.json
    (Phase 7 déjà faite), sans upstream_run.json terminal=true (issue
    définitive déjà atteinte), et qui ne sont membres d'aucun groupe déjà
    formé. Triés par case_id (ordre déterministe). Lecture seule."""
    if not failure_cases_root.is_dir():
        return []
    grouped_members = _group_members_on_disk(failure_cases_root)

    out: "list[str]" = []
    for entry in sorted(failure_cases_root.iterdir()):
        if not entry.is_dir():
            continue
        case_id = entry.name
        if not _is_safe_case_id(case_id):
            log_debug(_TAG, f"case_id ignoré (non sûr comme composant de chemin) : {case_id!r}")
            continue
        if not (entry / "manifest.json").is_file():
            continue
        if case_id in grouped_members:
            continue
        if (worktrees_root / case_id / "worktree.json").is_file():
            continue
        upstream_data, _err = _load_json(pipeline_runs_root / case_id / "upstream_run.json")
        if (isinstance(upstream_data, dict) and upstream_data.get("terminal")
                and not (retry_previous_failures
                         and upstream_data.get("stopped_at") == STAGE_UPSTREAM_HISTORY_DUPLICATE)):
            continue
        out.append(case_id)
    return out


def _run_fleet_import() -> None:
    """--import-fleet uniquement (désactivé par défaut). Réutilise
    Survey/autofix/fleet_case_import.py::import_available_cases telle quelle, avec
    ses racines/budgets par défaut. Import lazy (dépendance boto3
    optionnelle, seulement nécessaire pour --import-fleet) : un échec de
    n'importe quelle nature (config R2 manquante, dépendance absente,
    réseau) est un avertissement journalisé, jamais un arrêt de
    l'invocation — cf. docstring du module."""
    try:
        from Survey.autofix.fleet_case_import import FleetImportError, import_available_cases
    except Exception as exc:  # dépendance optionnelle potentiellement absente
        log_info(_TAG, f"avertissement : import fleet indisponible (dépendance manquante ?) : {exc}")
        return

    try:
        results, warnings = import_available_cases()
    except FleetImportError as exc:
        log_info(_TAG, f"avertissement : import fleet échoué (FLEET_R2_* absentes ?) : {exc}")
        return
    except Exception as exc:
        log_info(_TAG, f"avertissement : import fleet échoué (réseau ?) : {type(exc).__name__}: {exc}")
        return

    imported = sum(1 for r in results if r.status == "IMPORTED")
    log_info(
        _TAG,
        f"import fleet : {imported}/{len(results)} case(s) importé(s), {len(warnings)} avertissement(s)",
    )


def _process_upstream_case(
    case_id: str,
    *,
    failure_cases_root: Path,
    diagnoses_root: Path,
    context_selections_root: Path,
    prompts_root: Path,
    worktrees_root: Path,
) -> UpstreamCaseSummary:
    """Phases 5, 6, 7 pour UN case déjà diagnostiqué (diagnosis.json présent —
    garanti par l'appelant). Chaque étape est sautée si son artefact de
    sortie existe déjà (reprise après interruption, cf. docstring du
    module)."""
    failure_case_dir = failure_cases_root / case_id
    diagnosis_dir = diagnoses_root / case_id
    artifacts: "dict[str, str]" = {}

    # ── Phase 5 ──────────────────────────────────────────────────────────────
    context_selection_dir = context_selections_root / case_id
    context_selection_path = context_selection_dir / "context_selection.json"
    if context_selection_path.is_file():
        artifacts[STAGE_UPSTREAM_CONTEXT_SELECTION] = str(context_selection_path)
    else:
        try:
            path = write_context_selection(
                diagnosis_dir, failure_cases_root=failure_cases_root,
                out_root=context_selections_root, force=False,
            )
        except ContextSelectionError as exc:
            return UpstreamCaseSummary(
                case_id=case_id, stopped_at=STAGE_UPSTREAM_CONTEXT_SELECTION,
                stop_reason=str(exc), terminal=False, is_error=True, artifacts=artifacts,
            )
        artifacts[STAGE_UPSTREAM_CONTEXT_SELECTION] = str(path)

    # ── Phase 6 ──────────────────────────────────────────────────────────────
    prompt_dir = prompts_root / case_id
    prompt_file = prompt_dir / PROMPT_FILENAME
    manual_review_file = prompt_dir / MANUAL_REVIEW_FILENAME
    if prompt_file.is_file() or manual_review_file.is_file():
        artifacts[STAGE_UPSTREAM_PROMPT] = str(prompt_file if prompt_file.is_file() else manual_review_file)
    else:
        try:
            path = write_prompt(diagnosis_dir, context_selection_dir, out_root=prompts_root, force=False)
        except PromptGenerationError as exc:
            return UpstreamCaseSummary(
                case_id=case_id, stopped_at=STAGE_UPSTREAM_PROMPT,
                stop_reason=str(exc), terminal=False, is_error=True, artifacts=artifacts,
            )
        artifacts[STAGE_UPSTREAM_PROMPT] = str(path)

    if manual_review_file.is_file():
        return UpstreamCaseSummary(
            case_id=case_id, stopped_at=STAGE_UPSTREAM_PROMPT,
            stop_reason=(
                f"{MANUAL_REVIEW_FILENAME} (Phase 6) — case non éligible à la génération "
                "automatique de prompt, revue manuelle requise"
            ),
            terminal=True, is_error=False, artifacts=artifacts,
        )

    # ── Phase 7 ──────────────────────────────────────────────────────────────
    worktree_manifest_path = worktrees_root / case_id / "worktree.json"
    if worktree_manifest_path.is_file():
        artifacts[STAGE_UPSTREAM_WORKTREE] = str(worktree_manifest_path)
        return UpstreamCaseSummary(
            case_id=case_id, stopped_at=STAGE_UPSTREAM_WORKTREE, stop_reason=None,
            terminal=True, is_error=False, artifacts=artifacts,
        )

    # check_eligibility (nom exact) appelée SÉPARÉMENT de prepare_autofix_worktree
    # (qui l'appelle aussi en interne, mais ne distingue jamais par des
    # exceptions différentes une inéligibilité d'un échec opérationnel git) —
    # seule façon de distinguer les deux, cf. docstring du module.
    eligibility = check_eligibility(
        failure_case_dir=failure_case_dir, diagnosis_dir=diagnosis_dir, prompt_dir=prompt_dir,
    )
    if not eligibility.eligible:
        return UpstreamCaseSummary(
            case_id=case_id, stopped_at=STAGE_UPSTREAM_WORKTREE_ELIGIBILITY,
            stop_reason="; ".join(eligibility.reasons), terminal=True, is_error=False, artifacts=artifacts,
        )

    try:
        prepare_autofix_worktree(
            failure_case_dir=failure_case_dir, diagnosis_dir=diagnosis_dir, prompt_dir=prompt_dir,
            out_root=worktrees_root, force=False,
        )
    except AutofixWorktreeError as exc:
        return UpstreamCaseSummary(
            case_id=case_id, stopped_at=STAGE_UPSTREAM_WORKTREE,
            stop_reason=str(exc), terminal=False, is_error=True, artifacts=artifacts,
        )

    artifacts[STAGE_UPSTREAM_WORKTREE] = str(worktree_manifest_path)
    return UpstreamCaseSummary(
        case_id=case_id, stopped_at=STAGE_UPSTREAM_WORKTREE, stop_reason=None,
        terminal=True, is_error=False, artifacts=artifacts,
    )


def _stale_diagnosis_reason(path: Path, current_fingerprint: Optional[dict]) -> Optional[str]:
    if current_fingerprint is None:
        return "empreinte courante indisponible"
    diagnosis, _err = _load_json(path)
    fingerprint = diagnosis.get("code_fingerprint") if isinstance(diagnosis, dict) else None
    if not isinstance(fingerprint, dict) or not fingerprint.get("base_sha"):
        return "empreinte absente"
    if fingerprint.get("dirty") is not False:
        return "arbre modifié lors du diagnostic"
    if fingerprint != current_fingerprint:
        return "empreinte différente"
    return None


_SIGNATURE_CODE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
_SIGNATURE_MODULE_RE = re.compile(r"Survey/[a-z][a-z0-9_]*\.py")


def _diagnosis_signature(diagnosis: dict) -> Optional[tuple]:
    """Signature de codes structurels ; un champ incertain désactive la comparaison."""
    stage, itype = diagnosis.get("stage"), diagnosis.get("itype")
    symptom = diagnosis.get("symptom")
    failure_types = symptom.get("failure_types") if isinstance(symptom, dict) else None
    if (stage not in ("action", "extraction") or not isinstance(itype, str)
            or not _SIGNATURE_CODE_RE.fullmatch(itype)
            or not isinstance(failure_types, list) or not failure_types
            or any(not isinstance(code, str) or not _SIGNATURE_CODE_RE.fullmatch(code)
                   for code in failure_types)):
        return None
    base = (stage, itype, tuple(sorted(set(failure_types))))
    if stage == "action":
        replay = diagnosis.get("real_dispatch_replay")
        steps = replay.get("dispatcher_steps") if isinstance(replay, dict) else None
        if not isinstance(steps, list) or not steps or len(steps) > 24:
            return None
        normalized = []
        for step in steps:
            if not isinstance(step, str) or len(step) > 160 or not _DISPATCH_STEP_RE.fullmatch(step):
                return None
            code = "action_fix selected" if step.startswith("action_fix selected fix_id=") else step
            if not normalized or normalized[-1] != code:
                normalized.append(code)
        return base + (tuple(normalized),)

    modules = diagnosis.get("modules_likely_involved")
    if not isinstance(modules, list) or not modules:
        return None
    normalized_modules = []
    for entry in modules:
        if not isinstance(entry, dict):
            return None
        module, signals = entry.get("module"), entry.get("matched_signals")
        if (not isinstance(module, str) or not _SIGNATURE_MODULE_RE.fullmatch(module)
                or not isinstance(signals, list) or not signals):
            return None
        # group_key peut porter un nom ou une valeur de sondage.
        if any(not isinstance(signal, str) or not signal.startswith("context_flag:")
               or not _SIGNATURE_CODE_RE.fullmatch(signal[len("context_flag:"):])
               for signal in signals):
            return None
        normalized_modules.append((module, tuple(sorted(set(signals)))))
    return base + (tuple(sorted(set(normalized_modules))),)


def _historical_failure_match(
    case_id: str, diagnosis: dict, *, diagnoses_root: Path,
    confidence_scores_root: Path, static_validations_root: Path,
    pipeline_runs_root: Path, human_reviews_root: Path,
    merge_reviews_root: Path, merge_results_root: Path,
) -> Optional[tuple[str, str]]:
    signature = _diagnosis_signature(diagnosis)
    fingerprint = diagnosis.get("code_fingerprint")
    if (signature is None or not isinstance(fingerprint, dict)
            or not fingerprint.get("base_sha") or fingerprint.get("dirty") is not False
            or not diagnoses_root.is_dir()):
        return None
    examined = 0
    match = None
    for entry in sorted(diagnoses_root.iterdir(), key=lambda path: path.name, reverse=True):
        history_id = entry.name
        if history_id == case_id or not entry.is_dir() or not _is_safe_case_id(history_id):
            continue
        examined += 1
        if examined > _MAX_COMPLETED_HISTORY_CASES:
            log_debug(_TAG, f"case={case_id} : historique > {_MAX_COMPLETED_HISTORY_CASES}, déduplication abandonnée")
            return None
        # Une revue ou un merge, même illisible, rend l'issue incertaine.
        if (merge_results_root / history_id / "merge_result.json").is_file() or (
            human_reviews_root / history_id / "decision.json"
        ).is_file() or (merge_reviews_root / history_id / "decision.json").is_file():
            continue
        score, _ = _load_json(confidence_scores_root / history_id / "confidence_score.json")
        static, _ = _load_json(static_validations_root / history_id / "validation_static.json")
        pipeline, _ = _load_json(pipeline_runs_root / history_id / "pipeline_run.json")
        if isinstance(score, dict) and score.get("confidence") == CONFIDENCE_REJECT:
            outcome = "REJECT"
        elif isinstance(pipeline, dict) and pipeline.get("stopped_at") == STAGE_NO_CHANGES and pipeline.get("is_error") is False:
            outcome = STAGE_NO_CHANGES
        elif isinstance(static, dict) and static.get("verdict") == "REJECTED":
            outcome = "REJECTED"
        else:
            continue
        historical, _ = _load_json(entry / "diagnosis.json")
        if (match is None and isinstance(historical, dict)
                and historical.get("code_fingerprint") == fingerprint
                and _diagnosis_signature(historical) == signature):
            match = (history_id, outcome)
    return match


def run_upstream_stage(
    *,
    failure_cases_root: "str | Path" = "failure_cases",
    diagnoses_root: "str | Path" = "diagnoses",
    context_selections_root: "str | Path" = "context_selections",
    prompts_root: "str | Path" = "prompts",
    worktrees_root: "str | Path" = "autofix_worktrees",
    pipeline_runs_root: "str | Path" = "autofix_pipeline_runs",
    confidence_scores_root: "str | Path" = "confidence_scores",
    static_validations_root: "str | Path" = "autofix_static_validations",
    human_reviews_root: "str | Path" = "human_reviews",
    merge_reviews_root: "str | Path" = "merge_reviews",
    merge_results_root: "str | Path" = "merge_results",
    retry_previous_failures: bool = False,
    import_fleet: bool = False,
    max_upstream_cases: int = DEFAULT_MAX_UPSTREAM_CASES,
) -> "list[UpstreamCaseSummary]":
    """Point d'entrée de l'étape amont — cf. docstring du module pour le
    détail point par point. Désactivée par --no-upstream côté
    run_autofix_pipeline (cette fonction n'est alors jamais appelée)."""
    failure_cases_root = Path(failure_cases_root)
    diagnoses_root = Path(diagnoses_root)
    context_selections_root = Path(context_selections_root)
    prompts_root = Path(prompts_root)
    worktrees_root = Path(worktrees_root)
    pipeline_runs_root = Path(pipeline_runs_root)
    confidence_scores_root = Path(confidence_scores_root)
    static_validations_root = Path(static_validations_root)
    human_reviews_root = Path(human_reviews_root)
    merge_reviews_root = Path(merge_reviews_root)
    merge_results_root = Path(merge_results_root)

    summaries: "list[UpstreamCaseSummary]" = []

    # ── Point 1 ────────────────────────────────────────────────────────────
    if import_fleet:
        _run_fleet_import()

    # ── Points 2/3 : sélection + Phase 4 bornée (nouveaux ou périmés) ───────
    candidates = discover_upstream_candidates(
        failure_cases_root=failure_cases_root, worktrees_root=worktrees_root,
        pipeline_runs_root=pipeline_runs_root, retry_previous_failures=retry_previous_failures,
    )
    current_fingerprint = get_code_fingerprint() if candidates else None
    new_diagnoses = 0
    unavailable: set[str] = set()
    for case_id in candidates:
        diagnosis_path = diagnoses_root / case_id / "diagnosis.json"
        stale_reason = (
            _stale_diagnosis_reason(diagnosis_path, current_fingerprint)
            if diagnosis_path.is_file() else None
        )
        if diagnosis_path.is_file() and stale_reason is None:
            continue
        if new_diagnoses >= max_upstream_cases:
            if stale_reason is not None:
                unavailable.add(case_id)
            log_debug(
                _TAG,
                f"case={case_id} : diagnostic (Phase 4) reporté "
                f"(--max-upstream-cases={max_upstream_cases} atteint pour cette invocation)",
            )
            continue
        new_diagnoses += 1
        if stale_reason is not None:
            log_info(_TAG, f"case={case_id} : régénération diagnostic ({stale_reason})")
        try:
            write_diagnosis(
                failure_cases_root / case_id, out_root=diagnoses_root,
                force=stale_reason is not None,
            )
        except (DiagnosisError, OSError) as exc:
            unavailable.add(case_id)
            summary = UpstreamCaseSummary(
                case_id=case_id, stopped_at=STAGE_UPSTREAM_DIAGNOSIS,
                stop_reason=str(exc), terminal=False, is_error=True,
            )
            write_upstream_run_result(summary, out_root=pipeline_runs_root)
            summaries.append(summary)
        else:
            if _stale_diagnosis_reason(diagnosis_path, current_fingerprint) is not None:
                unavailable.add(case_id)

    # ── Point 4 : déduplication, une fois, avant toute sélection de contexte ─
    grouping_result = None
    # Le regroupement lit tous les diagnostics présents sur disque : ne pas lui
    # transmettre ceux qui restent périmés après la Phase 4.
    grouping_blocked = any(
        (diagnoses_root / cid / "diagnosis.json").is_file()
        and not (context_selections_root / cid / "context_selection.json").is_file()
        for cid in unavailable
    )
    if not grouping_blocked:
        try:
            grouping_result, _report_path = write_case_groups(
                diagnoses_root=diagnoses_root, failure_cases_root=failure_cases_root,
                context_selections_root=context_selections_root,
            )
        except CaseGroupingError as exc:
            log_info(
                _TAG,
                f"avertissement : regroupement (déduplication) échoué, cases traités individuellement : {exc}",
            )

    if grouping_result is not None:
        for group in grouping_result.groups:
            for member_id in group.members:
                if (pipeline_runs_root / member_id / "upstream_run.json").is_file():
                    continue  # déjà signalé par une invocation précédente
                summary = UpstreamCaseSummary(
                    case_id=member_id, stopped_at=STAGE_UPSTREAM_DEDUPLICATION,
                    stop_reason=(
                        f"fusionné dans le groupe {group.group_id} "
                        f"(représentant={group.representative_case_id}) — traité désormais comme ce "
                        "seul case, jamais individuellement"
                    ),
                    terminal=True, is_error=False,
                    artifacts={"case_group": str(failure_cases_root / group.group_id)},
                )
                write_upstream_run_result(summary, out_root=pipeline_runs_root)
                summaries.append(summary)

    # ── Point 5 : Phases 5/6/7, sélection recalculée + filtrée aux diagnostiqués ─
    final_ids = [
        cid for cid in discover_upstream_candidates(
            failure_cases_root=failure_cases_root, worktrees_root=worktrees_root,
            pipeline_runs_root=pipeline_runs_root, retry_previous_failures=retry_previous_failures,
        )
        if cid not in unavailable and (diagnoses_root / cid / "diagnosis.json").is_file()
    ]
    for case_id in final_ids:
        if not retry_previous_failures:
            diagnosis, _ = _load_json(diagnoses_root / case_id / "diagnosis.json")
            historical = _historical_failure_match(
                case_id, diagnosis, diagnoses_root=diagnoses_root,
                confidence_scores_root=confidence_scores_root,
                static_validations_root=static_validations_root,
                pipeline_runs_root=pipeline_runs_root,
                human_reviews_root=human_reviews_root,
                merge_reviews_root=merge_reviews_root, merge_results_root=merge_results_root,
            ) if isinstance(diagnosis, dict) else None
            if historical is not None:
                equivalent_id, outcome = historical
                summary = UpstreamCaseSummary(
                    case_id=case_id, stopped_at=STAGE_UPSTREAM_HISTORY_DUPLICATE,
                    stop_reason=f"équivalent au case terminé {equivalent_id} ({outcome})",
                    terminal=True, is_error=False,
                    equivalent_case_id=equivalent_id, equivalent_outcome=outcome,
                    retry_hint="Relancer tools/run_autofix_pipeline.py --retry-previous-failures",
                )
                write_upstream_run_result(summary, out_root=pipeline_runs_root)
                summaries.append(summary)
                continue
        summary = _process_upstream_case(
            case_id,
            failure_cases_root=failure_cases_root, diagnoses_root=diagnoses_root,
            context_selections_root=context_selections_root, prompts_root=prompts_root,
            worktrees_root=worktrees_root,
        )
        write_upstream_run_result(summary, out_root=pipeline_runs_root)
        summaries.append(summary)

    return summaries


# ═══════════════════════ Découverte des cases éligibles ══════════════════════


@dataclass
class EligibleCase:
    case_id: str
    worktree_manifest_path: Path
    prompt_path: Path


def discover_eligible_cases(
    *,
    worktrees_root: "str | Path",
    prompts_root: "str | Path",
    codex_runs_root: "str | Path",
) -> "list[EligibleCase]":
    """Toutes les conditions ensemble (cf. docstring du module) ; triés par
    case_id (ordre déterministe). Lecture seule : ne modifie rien."""
    worktrees_root = Path(worktrees_root)
    prompts_root = Path(prompts_root)
    codex_runs_root = Path(codex_runs_root)

    if not worktrees_root.is_dir():
        return []

    eligible: "list[EligibleCase]" = []
    for entry in sorted(worktrees_root.iterdir()):
        if not entry.is_dir():
            continue
        case_id = entry.name
        if not _is_safe_case_id(case_id):
            log_debug(_TAG, f"case_id ignoré (non sûr comme composant de chemin) : {case_id!r}")
            continue

        worktree_manifest_path = entry / "worktree.json"
        if not worktree_manifest_path.is_file():
            continue

        prompt_path = prompts_root / case_id / "prompt.txt"
        manual_review_path = prompts_root / case_id / "MANUAL_REVIEW_REQUIRED.txt"
        if not prompt_path.is_file() or manual_review_path.is_file():
            continue

        if (codex_runs_root / case_id / "run_result.json").is_file():
            continue

        eligible.append(EligibleCase(
            case_id=case_id,
            worktree_manifest_path=worktree_manifest_path,
            prompt_path=prompt_path,
        ))

    return eligible


# ═══════════════════════ Point 1 — sécurité du parallélisme ══════════════════


@dataclass
class SafetyOutcome:
    checked: bool
    safe: bool
    reason: Optional[str]


def _worktree_terminal_state(
    case_id: str,
    *,
    merge_results_root: Path,
    human_reviews_root: Path,
    merge_reviews_root: Path,
    confidence_scores_root: Path,
    static_validations_root: Path,
    codex_runs_root: Path,
    pipeline_runs_root: "Path | None" = None,
) -> "tuple[bool, str]":
    """Détermine si le worktree case_id a atteint une issue définitive (donc
    n'est plus "en vol") — cf. docstring du module (Point 1) pour la
    définition exacte et sa justification. Retourne (definitif, état_lu) :
    état_lu résume la valeur lue de chaque source (ou None si absente/
    illisible), pour que l'appelant puisse exposer l'état exact d'un case
    bloquant. Un artefact absent, illisible, malformé, ou dont le champ
    attendu est absent, ne compte JAMAIS comme définitif — le worktree reste
    "en vol" dans ce cas (jamais une hypothèse optimiste)."""
    states: "dict[str, Any]" = {}

    merge_result, _ = _load_json(merge_results_root / case_id / "merge_result.json")
    merge_status = merge_result.get("status") if isinstance(merge_result, dict) else None
    states["merge_result.status"] = merge_status
    if merge_status in (STATUS_MERGED, STATUS_ALREADY_MERGED):
        return True, f"merge_result.status={merge_status!r}"

    human_decision_data, _ = _load_json(human_reviews_root / case_id / "decision.json")
    human_decision = human_decision_data.get("decision") if isinstance(human_decision_data, dict) else None
    states["human_review.decision"] = human_decision
    if human_decision == "REJECTED":
        return True, f"human_review.decision={human_decision!r}"

    merge_review_data, _ = _load_json(merge_reviews_root / case_id / "decision.json")
    merge_review_decision = merge_review_data.get("decision") if isinstance(merge_review_data, dict) else None
    states["merge_review.decision"] = merge_review_decision
    if merge_review_decision == "REJECTED":
        return True, f"merge_review.decision={merge_review_decision!r}"

    confidence_data, _ = _load_json(confidence_scores_root / case_id / "confidence_score.json")
    confidence = confidence_data.get("confidence") if isinstance(confidence_data, dict) else None
    states["confidence_score.confidence"] = confidence
    if confidence == CONFIDENCE_REJECT:
        return True, f"confidence_score.confidence={confidence!r}"

    static_data, _ = _load_json(static_validations_root / case_id / "validation_static.json")
    static_verdict = static_data.get("verdict") if isinstance(static_data, dict) else None
    states["validation_static.verdict"] = static_verdict
    if static_verdict == "REJECTED":
        return True, f"validation_static.verdict={static_verdict!r}"

    run_result_data, _ = _load_json(codex_runs_root / case_id / "run_result.json")
    run_status = run_result_data.get("status") if isinstance(run_result_data, dict) else None
    states["run_result.status"] = run_status
    if run_status is not None and run_status != STATUS_SUCCESS:
        return True, f"run_result.status={run_status!r}"

    if pipeline_runs_root is not None:
        pipeline_data, _ = _load_json(pipeline_runs_root / case_id / "pipeline_run.json")
        if (isinstance(pipeline_data, dict) and pipeline_data.get("stopped_at") == STAGE_NO_CHANGES
                and pipeline_data.get("is_error") is False):
            return True, f"pipeline_run.stopped_at={STAGE_NO_CHANGES!r}"

    return False, "en vol (" + ", ".join(f"{k}={v!r}" for k, v in states.items()) + ")"


def _check_case_parallel_safety(
    case_id: str,
    *,
    context_selections_root: Path,
    worktrees_root: Path,
    merge_results_root: Path,
    human_reviews_root: Path,
    merge_reviews_root: Path,
    confidence_scores_root: Path,
    static_validations_root: Path,
    codex_runs_root: Path,
    pipeline_runs_root: "Path | None" = None,
) -> SafetyOutcome:
    """cf. docstring du module (Point 1) pour la définition retenue de "en
    vol" et sa justification. N'écrit rien : lecture seule, jamais de
    décision automatique au-delà du report du seul case candidat."""
    all_other_ids = []
    if worktrees_root.is_dir():
        all_other_ids = sorted(
            p.name for p in worktrees_root.iterdir()
            if p.is_dir() and p.name != case_id and (p / "worktree.json").is_file()
        )

    in_flight_states: "dict[str, str]" = {}
    for other_id in all_other_ids:
        is_terminal, state_label = _worktree_terminal_state(
            other_id,
            merge_results_root=merge_results_root,
            human_reviews_root=human_reviews_root,
            merge_reviews_root=merge_reviews_root,
            confidence_scores_root=confidence_scores_root,
            static_validations_root=static_validations_root,
            codex_runs_root=codex_runs_root,
            pipeline_runs_root=pipeline_runs_root,
        )
        if is_terminal:
            log_debug(_TAG, f"case={other_id} : worktree exclu du contrôle (issue définitive — {state_label})")
            continue
        in_flight_states[other_id] = state_label

    in_flight_ids = sorted(in_flight_states)

    if not in_flight_ids:
        log_debug(_TAG, f"case={case_id} : aucun autre worktree en vol — contrôle trivialement sûr")
        return SafetyOutcome(checked=False, safe=True, reason=None)

    paths = [context_selections_root / case_id / "context_selection.json"] + [
        context_selections_root / cid / "context_selection.json" for cid in in_flight_ids
    ]

    try:
        result = check_pre_launch_safety(paths)
    except ParallelSafetyError as exc:
        return SafetyOutcome(
            checked=True, safe=False,
            reason=f"contrôle de sécurité du parallélisme impossible (Survey/autofix/parallel_safety.py) : {exc}",
        )

    unsafe_for_candidate = [
        pair for pair in result.unsafe_pairs
        if case_id in (pair.case_id_a, pair.case_id_b)
    ]
    if unsafe_for_candidate:
        detail = "; ".join(
            f"{p.case_id_a}<->{p.case_id_b} fichier(s) partagé(s)={p.shared_files}"
            for p in unsafe_for_candidate
        )
        blocker_ids = sorted({
            (pair.case_id_b if pair.case_id_a == case_id else pair.case_id_a)
            for pair in unsafe_for_candidate
        })
        blockers = "; ".join(f"{bid} [{in_flight_states[bid]}]" for bid in blocker_ids)
        return SafetyOutcome(
            checked=True, safe=False,
            reason=(
                f"fichier(s) candidat(s) partagé(s) avec au moins un case en vol : {detail} "
                f"— bloqué par : {blockers}"
            ),
        )

    return SafetyOutcome(checked=True, safe=True, reason=None)


# ═══════════════════════ Point 2 — invocation Claude Code ════════════════════


@dataclass
class ClaudeInvocationResult:
    case_id: str
    branch: str
    worktree_path: str
    status: str
    exit_code: Optional[int]
    timed_out: bool
    session_id: Optional[str]
    is_error: Optional[bool]
    subtype: Optional[str]
    command: "list[str]"
    raw_stdout: str
    raw_stderr: str
    error: Optional[str]
    warnings: "list[str]" = field(default_factory=list)
    agent_final_text: Optional[str] = None
    agent_final_summary: Optional[str] = None
    declares_core_change_candidate: bool = False
    total_cost_usd: Optional[float] = None
    num_turns: Optional[int] = None

    def as_dict(self) -> dict:
        result = {
            "schema_version": SCHEMA_VERSION,
            "case_id": self.case_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "branch": self.branch,
            "worktree_path": self.worktree_path,
            "status": self.status,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "session_id": self.session_id,
            "is_error": self.is_error,
            "subtype": self.subtype,
            "command": self.command,
            "raw_stdout": self.raw_stdout,
            "raw_stderr": self.raw_stderr,
            "error": self.error,
            "warnings": self.warnings,
        }
        if self.agent_final_text is not None:
            result["agent_final_text"] = self.agent_final_text
        if self.agent_final_summary is not None:
            result["agent_final_summary"] = self.agent_final_summary
        if self.total_cost_usd is not None:
            result["total_cost_usd"] = self.total_cost_usd
        if self.num_turns is not None:
            result["num_turns"] = self.num_turns
        return result


def _decode(part: Any) -> str:
    if isinstance(part, bytes):
        return part.decode("utf-8", errors="replace")
    return part or ""


def _extract_agent_final_summary(final_text: str) -> Optional[str]:
    for line in reversed(final_text.splitlines()[-_AGENT_SUMMARY_TAIL_LINES:]):
        match = _AGENT_SUMMARY_RE.match(line)
        if match:
            cleaned = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", match.group(1))
            summary = " ".join(cleaned.split())[:_MAX_AGENT_FINAL_SUMMARY_CHARS]
            if summary:
                return summary
    return None


def invoke_claude_headless(
    *,
    case_id: str,
    branch: str,
    worktree_path: Path,
    prompt_text: str,
    allowed_tools: str = DEFAULT_ALLOWED_TOOLS,
    permission_mode: str = DEFAULT_PERMISSION_MODE,
    timeout_s: float = DEFAULT_CLAUDE_TIMEOUT_S,
    restricted: bool = True,
    max_budget_usd: float = DEFAULT_MAX_BUDGET_USD,
) -> ClaudeInvocationResult:
    """Un seul mécanisme, un seul essai par session. cf. docstring du
    module (Point 2) pour la justification de chaque flag, vérifiée avant
    d'écrire cette fonction (claude --help + appels réels)."""
    if (isinstance(max_budget_usd, bool) or not isinstance(max_budget_usd, (int, float))
            or max_budget_usd < 0
            or (isinstance(max_budget_usd, float) and not math.isfinite(max_budget_usd))):
        raise AutofixOrchestratorError(f"--max-budget-usd doit être un nombre fini >= 0 ({max_budget_usd!r} fourni)")
    claude_bin = shutil.which("claude")
    if not claude_bin:
        return ClaudeInvocationResult(
            case_id=case_id, branch=branch, worktree_path=str(worktree_path),
            status=STATUS_ERROR, exit_code=None, timed_out=False,
            session_id=None, is_error=None, subtype=None, command=[],
            raw_stdout="", raw_stderr="",
            error="binaire 'claude' introuvable sur PATH — invocation impossible",
        )

    cmd = [
        claude_bin, "-p",
        "--output-format", "json",
        "--allowedTools", allowed_tools,
        "--permission-mode", permission_mode,
        "--permission-prompts", "none",
    ]
    if restricted:
        cmd.append("--restricted")
    if max_budget_usd > 0:
        cmd.extend(("--max-budget-usd", str(max_budget_usd)))
    log_debug(_TAG, f"case={case_id} : {' '.join(cmd)} (cwd={worktree_path}, timeout={timeout_s}s)")

    try:
        proc = subprocess.run(
            cmd, cwd=str(worktree_path), input=prompt_text,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout_s, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return ClaudeInvocationResult(
            case_id=case_id, branch=branch, worktree_path=str(worktree_path),
            status=STATUS_TIMEOUT, exit_code=None, timed_out=True,
            session_id=None, is_error=None, subtype=None, command=cmd,
            raw_stdout=_decode(exc.stdout)[-_MAX_RAW_OUTPUT_CHARS:],
            raw_stderr=_decode(exc.stderr)[-_MAX_RAW_OUTPUT_CHARS:],
            error=f"invocation Claude Code expirée après {timeout_s:.1f}s (budget dépassé)",
        )
    except OSError as exc:
        return ClaudeInvocationResult(
            case_id=case_id, branch=branch, worktree_path=str(worktree_path),
            status=STATUS_ERROR, exit_code=None, timed_out=False,
            session_id=None, is_error=None, subtype=None, command=cmd,
            raw_stdout="", raw_stderr="", error=f"exécution impossible : {exc}",
        )

    stdout, stderr = proc.stdout or "", proc.stderr or ""
    invalid_utf8 = "\ufffd" in stdout or "\ufffd" in stderr
    raw_stdout = stdout[-_MAX_RAW_OUTPUT_CHARS:]
    raw_stderr = stderr[-_MAX_RAW_OUTPUT_CHARS:]

    parsed: Optional[dict] = None
    parse_error: Optional[str] = None
    stripped = stdout.strip()
    if not stripped:
        parse_error = "sortie standard vide — aucun résultat JSON exploitable"
    else:
        try:
            candidate = json.loads(stripped)
        except ValueError as exc:
            parse_error = f"sortie standard non-JSON exploitable ({exc})"
        else:
            if isinstance(candidate, dict):
                parsed = candidate
            else:
                parse_error = "sortie standard JSON valide mais pas un objet"

    session_id = parsed.get("session_id") if parsed else None
    is_error = parsed.get("is_error") if parsed else None
    subtype = parsed.get("subtype") if parsed else None
    cost = parsed.get("total_cost_usd") if parsed else None
    total_cost_usd = (
        cost if isinstance(cost, (int, float)) and not isinstance(cost, bool)
        and cost >= 0 and (not isinstance(cost, float) or math.isfinite(cost)) else None
    )
    turns = parsed.get("num_turns") if parsed else None
    num_turns = turns if isinstance(turns, int) and not isinstance(turns, bool) and turns >= 0 else None
    final_text = parsed.get("result") if parsed else None
    final_text = final_text if isinstance(final_text, str) else None

    if invalid_utf8:
        status = STATUS_ERROR
        error = "sortie UTF-8 invalide de Claude Code" + (f" (subtype={subtype!r})" if subtype is not None else "")
    elif proc.returncode != 0:
        status = STATUS_FAILURE
        error = (f"code de sortie non nul ({proc.returncode})"
                 + (f" ; subtype={subtype!r}" if subtype is not None else "")
                 + (f" ; {parse_error}" if parse_error else ""))
    elif parsed is None:
        status = STATUS_ERROR
        error = parse_error
    elif is_error is False and subtype in (None, "success"):
        status = STATUS_SUCCESS
        error = None
    else:
        status = STATUS_FAILURE
        error = f"Claude Code a rapporté is_error={is_error!r} (subtype={subtype!r})"

    return ClaudeInvocationResult(
        case_id=case_id, branch=branch, worktree_path=str(worktree_path),
        status=status, exit_code=proc.returncode, timed_out=False,
        session_id=session_id, is_error=is_error, subtype=subtype,
        command=cmd, raw_stdout=raw_stdout, raw_stderr=raw_stderr, error=error,
        agent_final_text=final_text[:_MAX_AGENT_FINAL_TEXT_CHARS] if final_text is not None else None,
        agent_final_summary=_extract_agent_final_summary(final_text) if final_text is not None else None,
        total_cost_usd=total_cost_usd, num_turns=num_turns,
        # Détection déterministe de la mention explicite, insensible à la casse.
        declares_core_change_candidate="CORE_CHANGE_CANDIDATE" in final_text.upper() if final_text else False,
    )


def _run_result_paths(out_root: Path, case_id: str) -> "tuple[Path, Path]":
    out_dir = out_root / case_id
    return out_dir, out_dir / "run_result.json"


def write_run_result(
    result: ClaudeInvocationResult,
    *,
    out_root: "str | Path" = "codex_runs",
    force: bool = False,
) -> Path:
    """Persiste result sous out_root/<case_id>/run_result.json. Refuse un
    écrasement silencieux (sauf --force) — même si, en usage normal via
    run_autofix_pipeline, la découverte garantit déjà que ce chemin n'existe
    pas encore pour un case retenu (défense en profondeur, jamais une
    hypothèse fragile)."""
    out_root = Path(out_root)
    out_dir, out_file = _run_result_paths(out_root, result.case_id)

    if out_dir.exists():
        if not force:
            raise AutofixOrchestratorExistsError(
                f"run_result.json déjà existant : {out_dir} (utiliser --force pour régénérer)"
            )
        if not out_file.is_file():
            raise AutofixOrchestratorError(
                f"{out_dir} existe mais ne ressemble pas à une sortie générée par cet outil "
                "(pas de run_result.json) — suppression refusée, vérifier manuellement"
            )
        log_debug(_TAG, f"régénération forcée : suppression de {out_dir}")
        shutil.rmtree(out_dir)

    out_dir.mkdir(parents=True, exist_ok=False)
    out_file.write_text(json.dumps(result.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    log_info(_TAG, f"case={result.case_id} invocation Claude Code status={result.status} -> {out_file}")
    return out_file


def write_core_change_candidate(
    case_id: str, reason: str, agent_final_text: str, *, out_root: Path,
    agent_final_summary: Optional[str] = None,
) -> Path:
    """Conserve la mention explicite CORE_CHANGE_CANDIDATE, sans l'interpréter."""
    out_file = out_root / case_id / "core_change_candidate.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "signal": "CORE_CHANGE_CANDIDATE",
        "reason": reason,
        "agent_final_text": agent_final_text,
    }
    if agent_final_summary is not None:
        data["agent_final_summary"] = agent_final_summary
    out_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_file


# ═══════════════════════ Résumé de chaîne par case ════════════════════════════


@dataclass
class CaseRunSummary:
    case_id: str
    deferred: bool
    stopped_at: Optional[str]
    stop_reason: Optional[str]
    is_error: bool
    artifacts: "dict[str, str]" = field(default_factory=dict)
    confidence: Optional[str] = None
    notified: bool = False
    notified_state: Optional[str] = None
    warnings: "list[str]" = field(default_factory=list)
    attempt_count: Optional[int] = None
    attempts: "list[dict] | None" = None

    def as_dict(self) -> dict:
        result = {
            "schema_version": SCHEMA_VERSION,
            "case_id": self.case_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "deferred": self.deferred,
            "stopped_at": self.stopped_at,
            "stop_reason": self.stop_reason,
            "is_error": self.is_error,
            "artifacts": self.artifacts,
            "confidence": self.confidence,
            "notified": self.notified,
            "warnings": self.warnings,
        }
        if self.attempt_count is not None:
            result["attempt_count"] = self.attempt_count
            result["attempts"] = self.attempts or []
        if self.notified_state is not None:
            result["notified_state"] = self.notified_state
        return result


def _pipeline_run_paths(out_root: Path, case_id: str) -> "tuple[Path, Path]":
    out_dir = out_root / case_id
    return out_dir, out_dir / "pipeline_run.json"


def write_pipeline_run_summary(
    summary: CaseRunSummary,
    *,
    out_root: "str | Path" = "autofix_pipeline_runs",
) -> Path:
    """TOUJOURS écrasé, sans garde --force — cf. docstring du module ("Sortie
    : résumé par case") pour la justification de cet écart volontaire avec la
    convention --force du reste de ce chantier."""
    out_root = Path(out_root)
    out_dir, out_file = _pipeline_run_paths(out_root, summary.case_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(summary.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    log_info(
        _TAG,
        f"case={summary.case_id} stopped_at={summary.stopped_at!r} "
        f"notified={summary.notified} is_error={summary.is_error} -> {out_file}",
    )
    return out_file


def _maybe_notify_live_validation(summary: CaseRunSummary, *, pipeline_runs_root: Path) -> None:
    """Signale une seule fois le score MEDIUM qui attend uniquement la Phase 10."""
    if summary.stopped_at != STAGE_CONFIDENCE_SCORE or summary.is_error or summary.confidence != CONFIDENCE_MEDIUM:
        return
    score_path = summary.artifacts.get(STAGE_CONFIDENCE_SCORE)
    replay_path = summary.artifacts.get(STAGE_PATCH_REPLAY)
    if not isinstance(score_path, str) or not score_path or not isinstance(replay_path, str) or not replay_path:
        return
    score, _ = _load_json(Path(score_path))
    criteria = score.get("criteria") if isinstance(score, dict) and score.get("confidence") == CONFIDENCE_MEDIUM else None
    expected = {
        "static_validation": "PASS", "extractor_integrity": "PASS",
        "fix_confirmed": "INCONCLUSIVE", "live_validation": "NOT_RUN",
    }
    if not isinstance(criteria, dict) or any(
        not isinstance(criteria.get(name), dict) or criteria[name].get("value") != value
        for name, value in expected.items()
    ):
        return

    previous, _ = _load_json(pipeline_runs_root / summary.case_id / "pipeline_run.json")
    previous_state = (
        NOTIFY_STATE_LIVE_VALIDATION_PENDING
        if isinstance(previous, dict) and previous.get("notified_state") == NOTIFY_STATE_LIVE_VALIDATION_PENDING
        else None
    )
    run_path = summary.artifacts.get(STAGE_CLAUDE_INVOCATION)
    run, _ = _load_json(Path(run_path)) if isinstance(run_path, str) and run_path else (None, None)
    agent_summary = run.get("agent_final_summary") if isinstance(run, dict) else None
    if not isinstance(agent_summary, str) or not agent_summary.strip():
        agent_summary = run.get("agent_final_text") if isinstance(run, dict) else None
    excerpt = " ".join(agent_summary.split())[:_MAX_NO_CHANGES_REASON_CHARS] if isinstance(agent_summary, str) else ""
    excerpt = re.sub(
        r"(?:[A-Za-z][A-Za-z0-9+.-]*://|www\.)\S+|\b(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:/\S*)?",
        "<URL>", excerpt,
    )
    if re.search(r"\b(?:question|libellé|option|réponse|answer|value|qid|target_id)\b", excerpt, re.IGNORECASE):
        excerpt = "[résumé masqué : contenu de sondage possible]"
    message = (
        f"Autofix case={summary.case_id} : correctif en attente de validation live.\n"
        f"Résumé : {excerpt or 'indisponible'}\n"
        f"Rejeu de patch : {replay_path}\n"
        "Validation live : une seule page à l'URL du case, rechargée à l'état de l'incident. "
        "Guide d'exploitation autofix, §4.3 « Hors chaîne automatique (usage manuel) » (Phase 10)."
    )
    if len(message) > _MAX_LIVE_STATUS_CHARS:
        return
    try:
        notified, warnings = _maybe_notify(
            summary.case_id, notify_state=NOTIFY_STATE_LIVE_VALIDATION_PENDING,
            previous_notified_state=previous_state, message=message,
        )
    except Exception:
        notified, warnings = False, ["notification indisponible"]
    summary.notified = notified
    summary.notified_state = NOTIFY_STATE_LIVE_VALIDATION_PENDING if notified else previous_state
    if warnings:
        summary.warnings.append("notification Telegram échouée (avertissement, jamais un arrêt)")


def _retry_pending_live_notifications(pipeline_runs_root: Path, *, processed_case_ids: set[str]) -> None:
    """Retente les notifications échouées sans rejouer un case terminé."""
    if not pipeline_runs_root.is_dir():
        return
    try:
        entries = sorted(pipeline_runs_root.iterdir())
    except OSError:
        return
    for entry in entries:
        if not entry.is_dir() or entry.name in processed_case_ids or not _is_safe_case_id(entry.name):
            continue
        previous, _ = _load_json(entry / "pipeline_run.json")
        if (not isinstance(previous, dict) or previous.get("case_id") != entry.name
                or previous.get("stopped_at") != STAGE_CONFIDENCE_SCORE
                or previous.get("confidence") != CONFIDENCE_MEDIUM
                or previous.get("is_error") is not False
                or previous.get("deferred") is not False
                or previous.get("notified_state") == NOTIFY_STATE_LIVE_VALIDATION_PENDING):
            continue
        artifacts = previous.get("artifacts")
        if not isinstance(artifacts, dict):
            continue
        prior_warnings = previous.get("warnings") if isinstance(previous.get("warnings"), list) else []
        summary = CaseRunSummary(
            case_id=entry.name, deferred=False, stopped_at=STAGE_CONFIDENCE_SCORE,
            stop_reason=previous.get("stop_reason"), is_error=previous.get("is_error") is True,
            artifacts=artifacts, confidence=CONFIDENCE_MEDIUM,
            warnings=prior_warnings.copy(),
            attempt_count=previous.get("attempt_count"), attempts=previous.get("attempts"),
        )
        _maybe_notify_live_validation(summary, pipeline_runs_root=pipeline_runs_root)
        if summary.notified or any(warning not in prior_warnings for warning in summary.warnings):
            summary.warnings = summary.warnings[-20:]
            try:
                (entry / "pipeline_run.json").write_text(
                    json.dumps(summary.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8",
                )
            except OSError:
                pass


# ═══════════════════════ Traitement d'un case ═════════════════════════════════


def _process_case_once(
    case: EligibleCase,
    *,
    failure_cases_root: Path,
    diagnoses_root: Path,
    context_selections_root: Path,
    worktrees_root: Path,
    codex_runs_root: Path,
    static_validations_root: Path,
    patch_replays_root: Path,
    extractor_integrity_checks_root: Path,
    confidence_scores_root: Path,
    human_reviews_root: Path,
    merge_results_root: Path,
    merge_reviews_root: Path,
    claude_timeout_s: float,
    allowed_tools: str,
    permission_mode: str,
    force: bool,
    restricted: bool = True,
    max_budget_usd: float = DEFAULT_MAX_BUDGET_USD,
    expected_core_changes_root: "Path | None" = None,
    pipeline_runs_root: "Path | None" = None,
    core_change_candidates_root: "Path | None" = None,
    prompt_override: "str | None" = None,
    skip_parallel_safety: bool = False,
    attempt_state: "dict | None" = None,
    attempt_number: int = 1,
) -> CaseRunSummary:
    """Traite UN case, du contrôle de parallélisme jusqu'à la notification
    humaine (ou l'arrêt contrôlé le plus loin possible dans cet ordre).
    Ne lève jamais d'exception vers l'appelant pour un problème propre à ce
    case : toute erreur contrôlée devient un CaseRunSummary avec is_error=True
    et stopped_at pointant l'étape concernée."""
    case_id = case.case_id
    artifacts: "dict[str, str]" = {}
    warnings: "list[str]" = []

    worktree_data, wt_err = _load_json(case.worktree_manifest_path)
    if wt_err or not isinstance(worktree_data, dict):
        return CaseRunSummary(
            case_id=case_id, deferred=True, stopped_at=None,
            stop_reason=f"worktree.json (Phase 7) {wt_err or 'ne contient pas un objet JSON'}",
            is_error=True, artifacts=artifacts, warnings=warnings,
        )
    if str(worktree_data.get("case_id") or "") != case_id:
        return CaseRunSummary(
            case_id=case_id, deferred=True, stopped_at=None,
            stop_reason=(
                f"worktree.json.case_id={worktree_data.get('case_id')!r} incohérent avec le "
                f"dossier {case_id!r}"
            ),
            is_error=True, artifacts=artifacts, warnings=warnings,
        )
    branch = str(worktree_data.get("branch") or "")
    worktree_path = Path(str(worktree_data.get("worktree_path") or ""))

    # ── Point 1 ────────────────────────────────────────────────────────────
    safety = _check_case_parallel_safety(
        case_id,
        context_selections_root=context_selections_root,
        worktrees_root=worktrees_root,
        merge_results_root=merge_results_root,
        human_reviews_root=human_reviews_root,
        merge_reviews_root=merge_reviews_root,
        confidence_scores_root=confidence_scores_root,
        static_validations_root=static_validations_root,
        codex_runs_root=codex_runs_root,
        pipeline_runs_root=pipeline_runs_root,
    ) if not skip_parallel_safety else SafetyOutcome(checked=True, safe=True, reason=None)
    if not safety.safe:
        return CaseRunSummary(
            case_id=case_id, deferred=True, stopped_at=STAGE_PARALLEL_SAFETY,
            stop_reason=safety.reason, is_error=False, artifacts=artifacts, warnings=warnings,
        )

    # ── Point 2 ────────────────────────────────────────────────────────────
    if prompt_override is None:
        prompt_text = case.prompt_path.read_text(encoding="utf-8")
        evidence_relative_dir = dom_evidence_relative_dir(case_id)
        prompt_text = add_dom_evidence_context(
            prompt_text, case_id, worktree_path / evidence_relative_dir, evidence_relative_dir,
        )
    else:
        prompt_text = prompt_override
    if attempt_state is not None:
        attempt_state["prompt_text"] = prompt_text
    invocation = invoke_claude_headless(
        case_id=case_id, branch=branch, worktree_path=worktree_path,
        prompt_text=prompt_text, allowed_tools=allowed_tools,
        permission_mode=permission_mode, timeout_s=claude_timeout_s,
        restricted=restricted, max_budget_usd=max_budget_usd,
    )
    run_result_path = write_run_result(invocation, out_root=codex_runs_root, force=force)
    if attempt_state is not None:
        attempt_state["invocation"] = invocation
    artifacts[STAGE_CLAUDE_INVOCATION] = str(run_result_path)
    agent_text_start = " ".join((invocation.agent_final_text or "").split())[:_MAX_NO_CHANGES_REASON_CHARS]
    agent_summary = invocation.agent_final_summary or agent_text_start

    if invocation.declares_core_change_candidate:
        try:
            candidate_path = write_core_change_candidate(
                case_id, agent_text_start or "CORE_CHANGE_CANDIDATE déclaré",
                invocation.agent_final_text or "",
                out_root=core_change_candidates_root or codex_runs_root.parent / "core_change_candidates",
                agent_final_summary=invocation.agent_final_summary,
            )
            artifacts[STAGE_CORE_CHANGE_CANDIDATE] = str(candidate_path)
        except OSError as exc:
            warnings.append(f"artefact CORE_CHANGE_CANDIDATE indisponible : {exc}")

    if invocation.status != STATUS_SUCCESS:
        return CaseRunSummary(
            case_id=case_id, deferred=False, stopped_at=STAGE_CLAUDE_INVOCATION,
            stop_reason=invocation.error or f"status={invocation.status}",
            is_error=True, artifacts=artifacts, warnings=warnings,
        )

    # ── Point 3a — Phase 8 ──────────────────────────────────────────────────
    try:
        static_validation_path = write_static_validation(
            case.worktree_manifest_path, out_root=static_validations_root, force=force,
        )
    except StaticValidationError as exc:
        return CaseRunSummary(
            case_id=case_id, deferred=False, stopped_at=STAGE_STATIC_VALIDATION,
            stop_reason=str(exc), is_error=True, artifacts=artifacts, warnings=warnings,
        )
    artifacts[STAGE_STATIC_VALIDATION] = str(static_validation_path)

    static_data, _ = _load_json(static_validation_path)
    static_verdict = static_data.get("verdict") if isinstance(static_data, dict) else None
    if static_verdict != "ACCEPTED":
        stop_reason = f"validation_static.json.verdict={static_verdict!r} (\"ACCEPTED\" requis)"
        reasons = static_data.get("reasons") if isinstance(static_data, dict) else None
        if isinstance(reasons, list) and reasons and isinstance(reasons[0], str):
            # Première raison : espaces normalisés, 200 caractères au maximum.
            first_reason = " ".join(reasons[0].split())[:200]
            if first_reason:
                stop_reason += f" — {first_reason}"
                if len(reasons) > 1:
                    stop_reason += f" (+{len(reasons) - 1} raison(s) supplémentaire(s))"
        return CaseRunSummary(
            case_id=case_id, deferred=False, stopped_at=STAGE_STATIC_VALIDATION,
            stop_reason=stop_reason,
            is_error=False, artifacts=artifacts, warnings=warnings,
        )

    if static_data.get("changed_files") == []:
        reason = agent_summary or "aucun fichier modifié"
        # Une synthèse NO_CHANGES antérieure prouve que l'envoi a déjà été tenté.
        # Même après un échec incertain, ne pas risquer un second message.
        previous_data, _ = _load_json(
            pipeline_runs_root / case_id / "pipeline_run.json"
        ) if pipeline_runs_root is not None else (None, None)
        previously_attempted = (
            isinstance(previous_data, dict) and previous_data.get("stopped_at") == STAGE_NO_CHANGES
        )
        if previously_attempted:
            notified = previous_data.get("notified") is True
        else:
            notified, notify_warnings = _maybe_notify(
                case_id, notify_state=NOTIFY_STATE_NO_CHANGES,
                previous_notified_state=None,
                message=(
                    f"Autofix case={case_id} : aucun changement"
                    f"{f' (tentative {attempt_number})' if attempt_number > 1 else ''}. {reason}\n"
                    f"Artefact : {artifacts.get(STAGE_CORE_CHANGE_CANDIDATE, str(run_result_path))}"
                ),
            )
            warnings.extend(notify_warnings)
        return CaseRunSummary(
            case_id=case_id, deferred=False, stopped_at=STAGE_NO_CHANGES,
            stop_reason=reason, is_error=False, artifacts=artifacts,
            notified=notified, warnings=warnings,
        )

    # ── Point 3b — Phase 9 (continue quel que soit l'outcome) ──────────────
    try:
        patch_replay_path = write_patch_replay(
            failure_case_dir=failure_cases_root / case_id,
            diagnosis_dir=diagnoses_root / case_id,
            worktree_manifest_path=case.worktree_manifest_path,
            validation_static_path=static_validation_path,
            out_root=patch_replays_root, force=force,
        )
    except PatchReplayError as exc:
        return CaseRunSummary(
            case_id=case_id, deferred=False, stopped_at=STAGE_PATCH_REPLAY,
            stop_reason=str(exc), is_error=True, artifacts=artifacts, warnings=warnings,
        )
    artifacts[STAGE_PATCH_REPLAY] = str(patch_replay_path)

    # ── Point 3c — Phase 11-A ────────────────────────────────────────────────
    try:
        expected_path = (
            expected_core_changes_root / case_id / "expected_change.json"
            if expected_core_changes_root is not None else None
        )
        integrity_path = write_extractor_integrity_check(
            case.worktree_manifest_path, static_validation_path,
            out_root=extractor_integrity_checks_root, force=force,
            expected_changes_path=expected_path if expected_path and expected_path.is_file() else None,
            diagnosis_path=diagnoses_root / case_id / "diagnosis.json",
            patch_replay_path=patch_replay_path,
        )
    except IntegrityGateError as exc:
        return CaseRunSummary(
            case_id=case_id, deferred=False, stopped_at=STAGE_EXTRACTOR_INTEGRITY,
            stop_reason=str(exc), is_error=True, artifacts=artifacts, warnings=warnings,
        )
    artifacts[STAGE_EXTRACTOR_INTEGRITY] = str(integrity_path)

    # ── Point 3d — Phase 12 (live_validation_path=None, jamais automatique) ─
    try:
        confidence_path = write_patch_confidence(
            worktree_manifest_path=case.worktree_manifest_path,
            validation_static_path=static_validation_path,
            patch_replay_path=patch_replay_path,
            extractor_integrity_check_path=integrity_path,
            live_validation_path=None,
            out_root=confidence_scores_root, force=force,
        )
    except ConfidenceScoreError as exc:
        return CaseRunSummary(
            case_id=case_id, deferred=False, stopped_at=STAGE_CONFIDENCE_SCORE,
            stop_reason=str(exc), is_error=True, artifacts=artifacts, warnings=warnings,
        )
    artifacts[STAGE_CONFIDENCE_SCORE] = str(confidence_path)

    confidence_data, _ = _load_json(confidence_path)
    confidence = (confidence_data or {}).get("confidence")

    if confidence != CONFIDENCE_HIGH:
        return CaseRunSummary(
            case_id=case_id, deferred=False, stopped_at=STAGE_CONFIDENCE_SCORE,
            stop_reason=f"confidence={confidence!r} (\"HIGH\" requis pour notifier)",
            is_error=False, artifacts=artifacts, confidence=confidence, warnings=warnings,
        )

    # ── Point 3e — Phase 13 ─────────────────────────────────────────────────
    try:
        review_result = send_review_request(
            confidence_score_dir=confidence_scores_root / case_id,
            diagnosis_dir=diagnoses_root / case_id,
            out_root=human_reviews_root, force=force,
        )
    except HumanReviewError as exc:
        return CaseRunSummary(
            case_id=case_id, deferred=False, stopped_at=STAGE_HUMAN_REVIEW,
            stop_reason=str(exc), is_error=True, artifacts=artifacts,
            confidence=confidence, warnings=warnings,
        )
    artifacts[STAGE_HUMAN_REVIEW] = str(review_result.pending_path)

    return CaseRunSummary(
        case_id=case_id, deferred=False, stopped_at=STAGE_HUMAN_REVIEW, stop_reason=None,
        is_error=False, artifacts=artifacts, confidence=confidence, notified=True, warnings=warnings,
    )


def _informative_static_rejection(data: Any) -> bool:
    """Relance seulement un contrôle de patch reconnu, jamais un outil indisponible."""
    if not isinstance(data, dict) or data.get("verdict") != "REJECTED":
        return False
    checks = data.get("checks")
    if not isinstance(checks, dict) or len(checks) > 6:
        return False
    failed = [(name, check) for name, check in checks.items()
              if isinstance(check, dict) and check.get("ok") is False]
    if not failed:
        return False
    environment_tokens = (
        "introuvable sur path", "indisponible", "budget dépassé",
        "dépassé son budget", "exécution impossible", "sortie ruff illisible",
        "aucune sortie exploitable", "résultat du contrôle d'activation illisible",
        "modulenotfounderror", "no module named", "no such file or directory",
    )
    for name, check in failed:
        error = check.get("error")
        if (name not in {"compile", "import", "lint", "tests", "activation", "root_files"}
                or check.get("timed_out") is True):
            return False
        if name in {"compile", "import"}:
            files = check.get("files")
            bad = [row for row in files if isinstance(row, dict) and row.get("ok") is False] if isinstance(files, list) and len(files) <= _MAX_PATCH_FINGERPRINT_FILES else []
            if not bad or any(
                row.get("timed_out") is True or not isinstance(row.get("error"), str)
                or any(token in row["error"].lower() for token in environment_tokens)
                for row in bad
            ):
                return False
        elif name == "lint" and (not isinstance(check.get("violations"), list)
                                  or not check["violations"] or error is not None):
            return False
        elif name == "tests" and (not isinstance(error, str)
                                   or any(token in error.lower() for token in environment_tokens)
                                   or (not check.get("executed") and "test associé absent" not in error)):
            return False
        elif name == "activation" and (
            check.get("skipped") is not False or not isinstance(error, str)
            or any(token in error.lower() for token in environment_tokens)
        ):
            return False
        elif name == "root_files" and (not check.get("files") or not isinstance(error, str)):
            return False
    return True


def _patch_fingerprint(static_data: Any, worktree_path: Path) -> Optional[str]:
    """Empreinte bornée des fichiers contrôlés par la validation statique."""
    files = static_data.get("changed_files") if isinstance(static_data, dict) else None
    checks = static_data.get("checks") if isinstance(static_data, dict) else None
    root_check = checks.get("root_files") if isinstance(checks, dict) else None
    root_files = root_check.get("files", []) if isinstance(root_check, dict) else []
    if not isinstance(files, list) or not isinstance(root_files, list):
        return None
    names = files + root_files
    if not 0 < len(names) <= _MAX_PATCH_FINGERPRINT_FILES or any(not isinstance(name, str) for name in names):
        return None
    digest = hashlib.sha256()
    root = worktree_path.resolve()
    total_bytes = 0
    try:
        for name in sorted(set(names)):
            path = Path(name)
            path = (path if path.is_absolute() else root / path).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                return None
            size = path.stat().st_size
            total_bytes += size
            if total_bytes > _MAX_ATTEMPT_ARTIFACT_BYTES or size > _MAX_PATCH_FINGERPRINT_FILE_BYTES:
                return None
            digest.update(str(path.relative_to(root)).encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    except (OSError, ValueError):
        return None
    return digest.hexdigest()


_SAFE_FAILURE_TYPES = frozenset({
    "missing_block", "invalid_block_shape", "missing_target_id", "registry_target_missing",
    "choice_without_options", "duplicate_options", "invalid_selection_limits",
    "max_select_exceeds_options", "question_text_is_help", "dispatcher_false_negative",
    "action_fix_success_unconfirmed", "invalid_action_shape", "action_missing_target_id",
    "action_target_missing", "action_value_not_in_registry_options", "dispatcher_reported_failure",
})


def _retry_evidence(summary: CaseRunSummary, worktree_path: Path, *, candidate_declared: bool) -> Optional[dict]:
    if summary.is_error or summary.deferred or summary.stopped_at == STAGE_NO_CHANGES:
        return None
    run_data, _ = _load_json(Path(summary.artifacts.get(STAGE_CLAUDE_INVOCATION, "")))
    if not isinstance(run_data, dict) or run_data.get("status") != STATUS_SUCCESS:
        return None
    if candidate_declared:
        return None
    static_data, _ = _load_json(Path(summary.artifacts.get(STAGE_STATIC_VALIDATION, "")))
    fingerprint = _patch_fingerprint(static_data, worktree_path)
    if fingerprint is None:
        return None
    if summary.stopped_at == STAGE_STATIC_VALIDATION:
        if not _informative_static_rejection(static_data):
            return None
        reasons = static_data.get("reasons")
        first = reasons[0] if isinstance(reasons, list) and reasons else None
        if not isinstance(first, str) or not first.strip() or len(first) > 4000:
            return None
        checks = static_data["checks"]
        kinds = [name for name in ("compile", "import", "lint", "tests", "activation", "root_files")
                 if isinstance(checks.get(name), dict) and checks[name].get("ok") is False][:3]
        cause = (STAGE_STATIC_VALIDATION, hashlib.sha256(" ".join(first.split()).encode("utf-8")).hexdigest())
        # Les détails libres des contrôles peuvent citer une réponse du sondage.
        return {"fingerprint": fingerprint, "cause": cause,
                "feedback": [f"- {name} : échec (détail masqué)" for name in kinds]}
    if summary.stopped_at != STAGE_CONFIDENCE_SCORE or summary.confidence not in (CONFIDENCE_MEDIUM, CONFIDENCE_REJECT):
        return None
    integrity, _ = _load_json(Path(summary.artifacts.get(STAGE_EXTRACTOR_INTEGRITY, "")))
    replay, _ = _load_json(Path(summary.artifacts.get(STAGE_PATCH_REPLAY, "")))
    if (not isinstance(integrity, dict) or integrity.get("verdict") != "ACCEPTED"
            or not isinstance(replay, dict) or replay.get("refused") is not False
            or replay.get("after_replay_error") is not None or replay.get("outcome") != "BUG_PERSISTANT"):
        return None
    after = replay.get("after_replay")
    if not isinstance(after, dict):
        return None
    if replay.get("stage") == "action" and (
        after.get("status") not in ("SUCCESS", "FAILURE") or after.get("validation_error") is not None
    ):
        return None
    if replay.get("stage") == "extraction" and after.get("verdict") != "REPRODUIT":
        return None
    if replay.get("stage") not in ("action", "extraction"):
        return None
    steps = after.get("dispatcher_steps") if isinstance(after, dict) else None
    if steps is not None and (not isinstance(steps, list) or len(steps) > 24
                              or "capture=truncated" in steps
                              or any(not isinstance(step, str) or not _DISPATCH_STEP_RE.fullmatch(step)
                                     for step in steps)):
        return None
    steps = steps or []
    comparison = after.get("validation_comparison") if isinstance(after, dict) else None
    types = comparison.get("replayed_failure_types") if isinstance(comparison, dict) else None
    safe_types = [item for item in types[:6] if isinstance(item, str) and item in _SAFE_FAILURE_TYPES] if isinstance(types, list) else []
    feedback = ["- rejeu : BUG_PERSISTANT"]
    if safe_types:
        feedback.append("- types d'échec : " + ", ".join(safe_types))
    feedback.extend("- étape : " + step for step in steps[:12])
    return {"fingerprint": fingerprint, "cause": (STAGE_PATCH_REPLAY, tuple(steps)),
            "feedback": feedback}


def _archive_case_attempt(summary: CaseRunSummary, root: Path, number: int) -> bool:
    """Copie les artefacts terminés avant toute régénération au même chemin."""
    archive = root / summary.case_id / "attempts" / f"attempt_{number}"
    if len(summary.artifacts) > 8:
        return False
    try:
        for stage, name in summary.artifacts.items():
            source = Path(name)
            if not source.is_file() or source.stat().st_size > _MAX_ATTEMPT_ARTIFACT_BYTES:
                return False
            destination = archive / stage
            destination.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination / source.name)
    except OSError:
        return False
    return True


def process_case(
    case: EligibleCase, *, failure_cases_root: Path, diagnoses_root: Path,
    context_selections_root: Path, worktrees_root: Path, codex_runs_root: Path,
    static_validations_root: Path, patch_replays_root: Path,
    extractor_integrity_checks_root: Path, confidence_scores_root: Path,
    human_reviews_root: Path, merge_results_root: Path, merge_reviews_root: Path,
    claude_timeout_s: float, allowed_tools: str, permission_mode: str, force: bool,
    restricted: bool = True, max_budget_usd: float = DEFAULT_MAX_BUDGET_USD,
    expected_core_changes_root: "Path | None" = None,
    pipeline_runs_root: "Path | None" = None,
    core_change_candidates_root: "Path | None" = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    max_case_budget_usd: float = DEFAULT_MAX_CASE_BUDGET_USD,
) -> CaseRunSummary:
    """Une tentative inchangée ou, sur échec informatif, N sessions distinctes."""
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or not 1 <= max_attempts <= _MAX_ATTEMPTS:
        raise AutofixOrchestratorError(f"--max-attempts doit être entre 1 et {_MAX_ATTEMPTS}")
    if (isinstance(max_case_budget_usd, bool) or not isinstance(max_case_budget_usd, (int, float))
            or max_case_budget_usd < 0 or not math.isfinite(max_case_budget_usd)):
        raise AutofixOrchestratorError("--max-case-budget-usd doit être un nombre fini >= 0")
    options = dict(
        failure_cases_root=failure_cases_root, diagnoses_root=diagnoses_root,
        context_selections_root=context_selections_root, worktrees_root=worktrees_root,
        codex_runs_root=codex_runs_root, static_validations_root=static_validations_root,
        patch_replays_root=patch_replays_root,
        extractor_integrity_checks_root=extractor_integrity_checks_root,
        confidence_scores_root=confidence_scores_root, human_reviews_root=human_reviews_root,
        merge_results_root=merge_results_root, merge_reviews_root=merge_reviews_root,
        claude_timeout_s=claude_timeout_s, allowed_tools=allowed_tools,
        permission_mode=permission_mode, force=force, restricted=restricted,
        max_budget_usd=max_budget_usd, expected_core_changes_root=expected_core_changes_root,
        pipeline_runs_root=pipeline_runs_root, core_change_candidates_root=core_change_candidates_root,
    )
    if max_attempts == 1:
        return _process_case_once(case, **options)
    attempts: "list[dict]" = []
    previous: Optional[dict] = None
    spent = 0.0
    prompt_override = None
    base_prompt = None
    archive_root = pipeline_runs_root or codex_runs_root.parent / "autofix_pipeline_runs"
    worktree_data, _ = _load_json(case.worktree_manifest_path)
    worktree_path = Path(str((worktree_data or {}).get("worktree_path") or ""))
    for number in range(1, max_attempts + 1):
        state: dict = {}
        if max_case_budget_usd > 0:
            remaining = max_case_budget_usd - spent
            options["max_budget_usd"] = min(max_budget_usd, remaining) if max_budget_usd > 0 else remaining
        summary = _process_case_once(
            case, **{**options, "force": force if number == 1 else True},
            prompt_override=prompt_override, skip_parallel_safety=number > 1,
            attempt_state=state, attempt_number=number,
        )
        invocation = state.get("invocation")
        if invocation is None:
            return summary
        cost = invocation.total_cost_usd if invocation is not None else None
        # La ligne libre de l'agent peut contenir des données du sondage.
        attempts.append({"number": number, "stopped_at": summary.stopped_at,
                         "cost_usd": cost, "num_turns": invocation.num_turns if invocation is not None else None,
                         "summary": f"tentative {number} : arrêt {summary.stopped_at or 'indéterminé'}"})
        summary.attempt_count = len(attempts)
        summary.attempts = attempts.copy()
        archived = _archive_case_attempt(summary, archive_root, number)
        evidence = _retry_evidence(
            summary, worktree_path,
            candidate_declared=bool(invocation and invocation.declares_core_change_candidate),
        ) if archived and number < max_attempts else None
        if not archived:
            summary.warnings.append("archivage de tentative incomplet"
                                    + (" ; aucune relance" if number < max_attempts else ""))
            log_debug(_TAG, f"case={case.case_id} tentative={number} archivage incomplet ; arrêt")
        if (evidence is None or not isinstance(cost, (int, float)) or isinstance(cost, bool)
                or cost < 0 or not math.isfinite(cost)):
            return summary
        if previous is not None and (previous["fingerprint"] == evidence["fingerprint"]
                                     or previous["cause"] == evidence["cause"]):
            log_debug(_TAG, f"case={case.case_id} tentative={number} sans progrès ; arrêt")
            return summary
        spent += cost
        if max_case_budget_usd > 0 and max_case_budget_usd - spent < _MIN_NEXT_ATTEMPT_BUDGET_USD:
            log_debug(_TAG, f"case={case.case_id} tentative={number} budget restant insuffisant ; arrêt")
            return summary
        interim_dir, interim_file = _pipeline_run_paths(archive_root, summary.case_id)
        try:
            interim_dir.mkdir(parents=True, exist_ok=True)
            interim_file.write_text(json.dumps(summary.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            summary.warnings.append("synthèse intermédiaire indisponible ; aucune relance")
            log_debug(_TAG, f"case={case.case_id} tentative={number} synthèse intermédiaire indisponible ; arrêt")
            return summary
        log_debug(_TAG, f"case={case.case_id} tentative={number} état terminal conservé avant relance")
        previous = evidence
        if base_prompt is None:
            base_prompt = state["prompt_text"]
        instruction = "\nCorrige le patch présent dans ce worktree ; ne le réécris pas depuis zéro.\n"
        feedback = (f"\n\n## Retour des contrôles — tentative {number + 1}\n"
                    f"RÉSUMÉ : {attempts[-1]['summary']}")
        for line in evidence["feedback"][:16]:
            if len(feedback) + len(line) + len(instruction) + 1 > _MAX_FEEDBACK_CHARS:
                break
            feedback += "\n" + line
        prompt_override = base_prompt + feedback + instruction
        log_debug(_TAG, f"case={case.case_id} tentative={number + 1}/{max_attempts} après échec informatif")
    return summary


# ═══════════════════════ Orchestration de l'invocation complète ══════════════


def run_autofix_pipeline(
    *,
    failure_cases_root: "str | Path" = "failure_cases",
    diagnoses_root: "str | Path" = "diagnoses",
    context_selections_root: "str | Path" = "context_selections",
    prompts_root: "str | Path" = "prompts",
    worktrees_root: "str | Path" = "autofix_worktrees",
    codex_runs_root: "str | Path" = "codex_runs",
    static_validations_root: "str | Path" = "autofix_static_validations",
    patch_replays_root: "str | Path" = "patch_replays",
    extractor_integrity_checks_root: "str | Path" = "extractor_integrity_checks",
    expected_core_changes_root: "str | Path | None" = None,
    confidence_scores_root: "str | Path" = "confidence_scores",
    human_reviews_root: "str | Path" = "human_reviews",
    merge_results_root: "str | Path" = "merge_results",
    merge_reviews_root: "str | Path" = "merge_reviews",
    commit_results_root: "str | Path" = "commit_results",
    pipeline_runs_root: "str | Path" = "autofix_pipeline_runs",
    core_change_candidates_root: "str | Path" = "core_change_candidates",
    max_cases: int = DEFAULT_MAX_CASES,
    claude_timeout_s: float = DEFAULT_CLAUDE_TIMEOUT_S,
    allowed_tools: str = DEFAULT_ALLOWED_TOOLS,
    permission_mode: str = DEFAULT_PERMISSION_MODE,
    restricted: bool = True,
    max_budget_usd: float = DEFAULT_MAX_BUDGET_USD,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    max_case_budget_usd: float = DEFAULT_MAX_CASE_BUDGET_USD,
    force: bool = False,
    run_upstream: bool = True,
    import_fleet: bool = False,
    max_upstream_cases: int = DEFAULT_MAX_UPSTREAM_CASES,
    retry_previous_failures: bool = False,
    run_downstream: bool = True,
    lock_stale_after_s: float = DEFAULT_LOCK_STALE_AFTER_S,
) -> "tuple[list[DownstreamCaseSummary], list[UpstreamCaseSummary], list[CaseRunSummary]]":
    """Point d'entrée unique. Ordre d'exécution (cf. docstring du module, point
    0) : (0) verrou d'exclusion (toujours actif, aucune option pour le
    désactiver — protège aussi l'étape existante, dont une session Claude Code
    peut durer jusqu'à --claude-timeout-s par case) ; (1) étape AVAL (décision
    Telegram Phase 13 -> commit -> merge local), sauf run_downstream=False
    (--no-downstream) ; (2) étape AMONT (failure_cases/ -> worktree prêt), sauf
    run_upstream=False (--no-upstream) — après l'aval, pour que les correctifs
    déjà mergés soient présents dans le dépôt quand l'amont diagnostique de
    nouveaux cases ; (3) étape EXISTANTE (jusqu'à max_cases cases éligibles,
    triés par case_id, un à la fois). Le verrou est toujours libéré en fin
    d'invocation, y compris sur exception."""
    if max_cases < 1:
        raise AutofixOrchestratorError(f"--max-cases doit être >= 1 ({max_cases} fourni)")
    if max_upstream_cases < 1:
        raise AutofixOrchestratorError(f"--max-upstream-cases doit être >= 1 ({max_upstream_cases} fourni)")
    if (isinstance(max_budget_usd, bool) or not isinstance(max_budget_usd, (int, float))
            or max_budget_usd < 0
            or (isinstance(max_budget_usd, float) and not math.isfinite(max_budget_usd))):
        raise AutofixOrchestratorError(f"--max-budget-usd doit être un nombre fini >= 0 ({max_budget_usd!r} fourni)")
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or not 1 <= max_attempts <= _MAX_ATTEMPTS:
        raise AutofixOrchestratorError(f"--max-attempts doit être entre 1 et {_MAX_ATTEMPTS}")
    if (isinstance(max_case_budget_usd, bool) or not isinstance(max_case_budget_usd, (int, float))
            or max_case_budget_usd < 0 or not math.isfinite(max_case_budget_usd)):
        raise AutofixOrchestratorError("--max-case-budget-usd doit être un nombre fini >= 0")
    if lock_stale_after_s <= 0:
        raise AutofixOrchestratorError(f"--lock-stale-after-s doit être > 0 ({lock_stale_after_s} fourni)")

    failure_cases_root = Path(failure_cases_root)
    diagnoses_root = Path(diagnoses_root)
    context_selections_root = Path(context_selections_root)
    prompts_root = Path(prompts_root)
    worktrees_root = Path(worktrees_root)
    codex_runs_root = Path(codex_runs_root)
    static_validations_root = Path(static_validations_root)
    patch_replays_root = Path(patch_replays_root)
    extractor_integrity_checks_root = Path(extractor_integrity_checks_root)
    expected_core_changes_root = Path(expected_core_changes_root) if expected_core_changes_root is not None else None
    confidence_scores_root = Path(confidence_scores_root)
    human_reviews_root = Path(human_reviews_root)
    merge_results_root = Path(merge_results_root)
    merge_reviews_root = Path(merge_reviews_root)
    commit_results_root = Path(commit_results_root)
    pipeline_runs_root = Path(pipeline_runs_root)
    core_change_candidates_root = Path(core_change_candidates_root)

    # ── Point 0 : verrou d'exclusion (jamais deux invocations en même temps) ─
    lock = acquire_lock(pipeline_runs_root, stale_after_s=lock_stale_after_s)
    try:
        downstream_summaries: "list[DownstreamCaseSummary]" = []
        if run_downstream:
            downstream_summaries = run_downstream_stage(
                human_reviews_root=human_reviews_root,
                merge_reviews_root=merge_reviews_root,
                confidence_scores_root=confidence_scores_root,
                worktrees_root=worktrees_root,
                diagnoses_root=diagnoses_root,
                context_selections_root=context_selections_root,
                commit_results_root=commit_results_root,
                merge_results_root=merge_results_root,
                pipeline_runs_root=pipeline_runs_root,
            )

        upstream_summaries: "list[UpstreamCaseSummary]" = []
        if run_upstream:
            upstream_summaries = run_upstream_stage(
                failure_cases_root=failure_cases_root,
                diagnoses_root=diagnoses_root,
                context_selections_root=context_selections_root,
                prompts_root=prompts_root,
                worktrees_root=worktrees_root,
                pipeline_runs_root=pipeline_runs_root,
                confidence_scores_root=confidence_scores_root,
                static_validations_root=static_validations_root,
                human_reviews_root=human_reviews_root,
                merge_reviews_root=merge_reviews_root,
                merge_results_root=merge_results_root,
                retry_previous_failures=retry_previous_failures,
                import_fleet=import_fleet,
                max_upstream_cases=max_upstream_cases,
            )

        eligible = discover_eligible_cases(
            worktrees_root=worktrees_root, prompts_root=prompts_root, codex_runs_root=codex_runs_root,
        )
        batch = eligible[:max_cases]
        log_info(
            _TAG,
            f"{len(eligible)} case(s) éligible(s), {len(batch)} retenu(s) pour cette invocation "
            f"(--max-cases={max_cases})",
        )

        summaries: "list[CaseRunSummary]" = []
        for case in batch:
            summary = process_case(
                case,
                failure_cases_root=failure_cases_root,
                diagnoses_root=diagnoses_root,
                context_selections_root=context_selections_root,
                worktrees_root=worktrees_root,
                codex_runs_root=codex_runs_root,
                static_validations_root=static_validations_root,
                patch_replays_root=patch_replays_root,
                extractor_integrity_checks_root=extractor_integrity_checks_root,
                confidence_scores_root=confidence_scores_root,
                human_reviews_root=human_reviews_root,
                merge_results_root=merge_results_root,
                merge_reviews_root=merge_reviews_root,
                claude_timeout_s=claude_timeout_s,
                allowed_tools=allowed_tools,
                permission_mode=permission_mode,
                restricted=restricted,
                max_budget_usd=max_budget_usd,
                max_attempts=max_attempts,
                max_case_budget_usd=max_case_budget_usd,
                force=force,
                expected_core_changes_root=expected_core_changes_root,
                pipeline_runs_root=pipeline_runs_root,
                core_change_candidates_root=core_change_candidates_root,
            )
            _maybe_notify_live_validation(summary, pipeline_runs_root=pipeline_runs_root)
            write_pipeline_run_summary(summary, out_root=pipeline_runs_root)
            summaries.append(summary)

        _retry_pending_live_notifications(
            pipeline_runs_root, processed_case_ids={case.case_id for case in batch},
        )

        return downstream_summaries, upstream_summaries, summaries
    finally:
        release_lock(lock)
