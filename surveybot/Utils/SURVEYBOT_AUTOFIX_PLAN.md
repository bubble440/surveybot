# SurveyBot — mémoire active de l'autofix

Ce fichier est le point d'entrée à lire avant un diagnostic ou un prompt relatif à l'autofix. L'[archive historique figée](SURVEYBOT_AUTOFIX_PLAN_ARCHIVE.md) conserve, après son en-tête, **octet pour octet** l'ancien plan : journal des mises à jour jusqu'à la suite 47, descriptions détaillées des phases 1A à 20, règles, décisions, cas réels et architecture cible. Pour retrouver un détail, rechercher dans l'archive `Mise à jour ... (suite N)` ou le titre `Phase ...`. L'archive ne reçoit aucune nouvelle chronique ; l'état courant se trouve uniquement ici.

Les décisions de [BOT_EVOLUTION_MEMORY.md](../Survey/BOT_EVOLUTION_MEMORY.md), notamment ses « Patterns exclus », restent des frontières strictes. La séparation du cœur protégé et des points d'extension est décrite dans [SURVEYBOT_PROTECTED_CORE_ARCHITECTURE.md](SURVEYBOT_PROTECTED_CORE_ARCHITECTURE.md). Ces documents ne sont pas remplacés par cette mémoire.

## État actuel

- Le pipeline dispose de la détection passive, des failure cases, du rejeu, du diagnostic, de la sélection de contexte, de la génération de prompt, d'un worktree isolé, des validations et du score de confiance. L'orchestrateur enchaîne l'amont, Claude Code headless, les contrôles, la décision Telegram puis le commit et le merge local après approbation. Le push partagé et la release restent des gestes manuels. Détails : archive, suites 35 à 37 et phases 1A à 20.
- Le rejeu Chromium fidèle est le mode par défaut pour `stage="extraction"` ; le rejeu statique reste disponible avec `--static`. Pour `stage="action"`, le défaut reste statique et le mode fidèle est explicite. La réexécution réelle du dispatcher (diagnostic Phase 4, rejeu de patch Phase 9) charge la page capturée **sans exécuter ses scripts** et enregistre ce mode dans ses artefacts (`execute_scripts`). Détails : archive, suites 43 et 44.
- Les 26 modules du pipeline vivent dans `Survey/autofix/` depuis le 29 septembre 2026 ; l'archive cite leurs anciens chemins `Survey/<module>.py`. Les modules du bot (`page_snapshot.py`, `browser_capsule.py`, validators, `failure_recorder.py`, `extractor_integrity.*`) et ceux des correctifs externes (`external_fix_registry.py`, `extraction_fix_hook.py`, `action_fix_hook.py`, `external_fix_loader.py`) restent dans `Survey/`. Le guide d'exploitation (`GUIDE_EXPLOITATION_AUTOFIX.md`) décrit le rôle de chaque module et les commandes courantes.
- **Premier cycle complet sur un cas réel, mené à son terme (MetrixLab, 29 septembre – 1er octobre 2026).** Quatre sessions de l'agent ont chacune révélé un défaut du pipeline, tous corrigés : (1) l'agent s'est arrêté pour demander une confirmation que le mode headless ne peut pas recevoir (un refus de Bash enregistré ne l'a pas empêché d'analyser) → prompt réécrit, voies additives `before`/`after`, aucune question ; (2) le worktree ne contenait pas le DOM du case (artefacts dans des dossiers ignorés par Git, prompt limité au symptôme) → copie des preuves DOM nettoyées sous `surveybot/failure_cases/<case_id>/artifacts/` (ignoré par Git), chemin indiqué dans la section technique du prompt, `snapshots/dom.txt` retiré du suivi ; (3) l'agent a tenté d'exécuter du code pour vérifier l'activation de son correctif (refusé), a laissé un script temporaire, et `pytest` manquait dans le venv du clone → contrôle d'activation du correctif dans la validation statique, `pytest` vérifié par le preflight, consignes du prompt complétées ; (4) patch complet (module de correctif, test synthétique, ancrage, entrée BEM) : validation statique `ACCEPTED`, rejeu `CORRECTIF_CONFIRME`, intégrité 58/58, score `HIGH`, approuvé via Telegram, commité puis mergé localement dans `autofix-integration`, récupéré dans le dépôt d'origine. L'extraction corrigée a été observée sur une vraie enquête MetrixLab (bloc de la question visible, trois options). Régressions rencontrées et corrigées en chemin : contrat de `_git_changed_paths` (couple au lieu d'une liste, plantage au commit), décodage des sous-processus (UTF-8 explicite), hook d'extraction écartant le résultat d'un correctif terminé après dépassement du budget (conservé désormais, comme dans le hook d'action), test dépendant de la liste de chargement réelle. Un case dont `run_result.json` existe n'est jamais repris automatiquement ; ne pas utiliser `Reset-AutofixCase` sur un case terminé (il le rendrait éligible). Depuis le 1er octobre 2026, l'orchestrateur retire lui-même le worktree et la branche d'un case mergé (chemin enregistré sous la racine des worktrees, branche ancêtre de la branche d'intégration, worktree propre, aucune suppression forcée ; un échec reste sans effet sur l'issue du case ; artefacts conservés) ; livré avec des tests synthétiques, première observation sur un vrai case à faire (guide d'exploitation, §6 I). Un second cycle, sur un cas d'action, est décrit ci-dessous.

- **Second cycle réel, stage `action` (MetrixLab, question radio `radioQT`, 2–3 octobre 2026) : aucun correctif adopté, le pipeline a été durci.** Le bug du bot (le clic sur l'option demandée expire car la cible est jugée non visible par Playwright ; cause racine non établie, laissée ouverte) n'était pas l'objet du test : le but était d'éprouver le mode action. Deux sessions de l'agent. La première (avant les durcissements 4 à 6 ci-dessous) a conclu à un blocage sans modifier de fichier (≈ 1 $). La seconde a écrit un correctif externe dont la prémisse (échange du `src` d'une image) n'était confirmée par aucun artefact et dont le critère de succès était la propriété native `checked` d'un input masqué (≈ 2,3 $, 66 tours) : validation statique d'abord `REJECTED` uniquement parce que `ruff` était absent du `PATH`, puis `ACCEPTED` ; rejeu `BUG_PERSISTANT`, intégrité 58/58, score `REJECT`. Un test live de ce correctif a annoncé un succès alors que l'option n'était pas sélectionnée : c'est le cas qui a motivé le point 9. Le worktree de ce case reste sur `base_sha` `7ace17d` : les points 7 à 9 n'y sont pas exercés, un nouveau case est nécessaire pour un test de bout en bout.
- **Durcissements livrés pendant ce cycle** (chacun avec des tests synthétiques) :
  1. Validateur : le faux négatif du dispatcher n'est plus déduit de `checked` seul pour les widgets QT (un clic JavaScript sur l'input masqué le rend vrai sans sélection UI).
  2. Dispatcher (cœur, baseline réenregistrée) : la post-vérification QT échouait sur une erreur JavaScript puis le dispatcher annonçait quand même un succès ; il retourne maintenant l'échec. Les méthodes pointer de la cascade de clic sont bornées à 1 s au lieu de 30 s par appel.
  3. Rejeu : la réexécution réelle ne retrouvait pas la cible (les scripts de la page capturée vident `#mainContent`, puis la modale masquée était extraite à la place de la question) ; la page est chargée sans scripts dans le diagnostic et le rejeu de patch.
  4. Sélection de contexte : un case d'action n'avait aucun fichier de code (`modules_likely_involved` vide, donc `MANUAL_REVIEW_REQUIRED`). Une table statique explicite fournit `action_dispatcher.py`, `input_handler.py`, le module `input_*` de l'itype (`cta_handler.py` pour un bouton), placés avant les modules de la Phase 4 pour ne jamais être retirés par le plafond de 8.
  5. Prompt et preuves : le récit du bug ne contenait aucun symptôme et l'agent ne recevait que quatre fichiers. Le prompt reçoit un extrait assaini des étapes du dispatcher (24 lignes de 160 caractères au plus, codes techniques seulement, capture indépendante de `LOG_LEVEL`), le fait que le rejeu s'exécute sans scripts, et les décisions d'un correctif d'action ; `runtime_state.json` et `action_trace.json` sont copiés dans le worktree pour le stage action.
  6. Issues silencieuses : un patch vide était `ACCEPTED`. Il arrête maintenant la chaîne à l'étape `NO_CHANGES` (reconnue comme définitive par le contrôle de parallélisme), le texte final de l'agent est conservé (`agent_final_text`, 8 000 caractères), une notification Telegram est tentée une fois, et une déclaration `CORE_CHANGE_CANDIDATE` écrit un JSON sous `core_change_candidates/`.
  7. Observabilité : `reason` d'un `NOT_EXECUTED`, message d'erreur d'un correctif ignoré par le registre (niveau info), traces des sorties du hook sur dépassement de budget, décisions d'un correctif d'action (`selected fix_id=…`, `verdict=…`).
  8. Diagnostic périmé : `diagnosis.json` porte une empreinte du code (`code_fingerprint` : `base_sha` et état `dirty`). À l'entrée de l'étape amont, un diagnostic dont l'empreinte est absente, différente ou marquée `dirty` est régénéré, dans le budget `--max-upstream-cases` ; un diagnostic périmé non régénéré faute de budget n'alimente pas la suite. Un merge local dans `autofix-integration` change `HEAD` et rend donc périmés les diagnostics en attente (voulu : le bug peut avoir été corrigé). Les cases ayant déjà un worktree ou un état terminal ne sont jamais régénérés.
  9. Vérification d'un succès de correctif d'action : un correctif qui retourne un succès contourne la post-vérification du chemin historique. Le hook mémorise ces succès et le validateur émet `action_fix_success_unconfirmed` (widgets QT seulement) si le marqueur UI de sélection est absent ; par lecture du code, le rejeu aboutit alors à un verdict non concluant plutôt qu'à `CORRECTIF_CONFIRME`.

## Règles et décisions en vigueur

### Observabilité et validators

La Phase 1A est **strictement passive** :

``` text
aucun retry
aucune auto-correction
aucun fallback supplémentaire
aucune modification des valeurs retournées par analyze_dom()
aucune modification des valeurs retournées par execute_actions_plan()
```

Un échec de validation est observé et enregistré, puis le bot poursuit
son flux normal.

``` text
ne pas transformer action_validator.py en second action_dispatcher
ne pas ajouter une cascade de fallbacks
ajouter seulement des invariants DOM forts, scopés et vérifiables
```

La collecte complète des cas 1B ne bloque pas les autres phases. Chaque nouveau cas réel utile doit être capturé, classé, puis traité par un patch 1B.x ciblé. Le doute sur `dispatcher_success=true` avec sélection erronée est **confirmé pour un correctif externe d'action** (3 octobre 2026, point 9 de l'état actuel) ; pour le chemin historique du dispatcher il reste non confirmé : ne pas patcher sans snapshot réel. Détails : archive, Phase 1B et suite 45.

### Rejeu, simplicité et validation

``` text
la page live sert à capturer l'incident une seule fois
→ elle ne doit pas être nécessaire pour reproduire ou valider le bug ensuite
```

Le système choisit toujours le niveau le plus simple capable de reproduire
honnêtement le cas. Il ne doit jamais inventer un état manquant pour rendre
artificiellement un incident rejouable.

Cette extension ne doit pas devenir un système général d'archivage complet
du Web. Ne pas chercher à sérialiser fidèlement toute la mémoire
JavaScript, tous les listeners, tout le trafic réseau, tous les storages
navigateur, ou tout le backend du provider. Le but est uniquement de
capturer les faits nécessaires aux bugs réellement rencontrés par SurveyBot.

``` text
si un nouvel artefact améliore clairement
la reproduction, le diagnostic, la validation ou la non-régression
→ il est justifié
sinon
→ ne pas l'ajouter
```

Toute boucle de capture ou de reconstruction a un budget maximal, un
abandon contrôlé, un `log_debug` utile, et jamais de boucle infinie.

Un `TRACE_REPLAY` n'a pas le niveau de preuve d'une réexécution réelle du dispatcher. Le correctif n'est confirmé que par le verdict `CORRECTIF_CONFIRME` du chemin approprié. La Phase 11-A protège les fonctions gelées ; son registre et sa baseline ne sont jamais mis à jour automatiquement. Détails : archive, Phases 3C.4, 3D, 9, 11 et 12.

### Correctifs d'action et preuve de succès

``` text
un correctif externe d'action qui retourne un succès contourne la post-vérification historique
→ son succès doit être confirmé indépendamment, sinon il reste non concluant
```

La propriété native `checked` d'un input masqué n'est pas une preuve de sélection d'un widget QT. Le rejeu sans scripts ne peut pas observer un état posé par le JavaScript du site : un succès non confirmé reste non concluant et la validation live (Phase 10) tranche ; aucun état ne doit être inventé pour rendre un cas confirmable. Une garde de correctif d'action est évaluée **avant** l'action : des sélecteurs exclus décrivant un état d'après-clic sont toujours absents et laissent la garde trop large. Le détecteur `action_fix_success_unconfirmed` ne couvre aujourd'hui que les groupes `radioQT` / `checkboxQT`.

### Git et décisions humaines

Le patch se prépare dans une branche et un worktree dédiés. Le merge local suit une approbation Telegram dans le pipeline orchestré ; aucun push ni release n'est automatique. Détails : archive, suites 35 à 37 et Phases 7, 13, 15, 16.

## Phases ouvertes ou partielles

| Phase | État / prochaine condition utile |
| --- | --- |
| 1B | Ouverte en tâche de fond. Continuer la collecte de cas réels. L'extraction MetrixLab est corrigée par le premier correctif externe adopté (`Survey/external_fix_hidden_consent_radio_question.py`, ancrage `before` sur le consentement en modale) ; l'ancrage, le chargement explicite et la copie des preuves DOM sont livrés. Cas d'action MetrixLab `radioQT` (2–3 octobre 2026) : la sélection de l'option échoue (cible non visible pour Playwright), cause racine non établie, aucun correctif adopté. |
| 3 / 9 (rejeu) | Les deux rejeux Chromium diffèrent par l'exécution des scripts de la page : le mode par défaut pour l'extraction (scripts désactivés) reproduit le bug sans patch, le mode scripts activés ne le reproduit pas même sans patch. Depuis octobre 2026 la réexécution réelle du dispatcher (diagnostic, Phase 9) charge aussi la page sans scripts, et le mode est enregistré (`execute_scripts`). Limite : un état posé par le JavaScript du site n'y est pas observable, donc le correctif d'un widget dont la sélection dépend de ce JavaScript ne peut pas y être confirmé (verdict non concluant ou `BUG_PERSISTANT`) ; la Phase 10 live reste nécessaire. La Phase 9 exige `BUG_PERSISTANT` en amont. Reste : le diagnostic annonce `confiance=certain` sans signaler la divergence entre modes. |
| 3B.3 / 3B.4 | Frames et Shadow DOM différés jusqu'à une mesure sur des cases réellement `NON_REJOUABLE` ; aucun volume suffisant n'est encore consigné. |
| 3D | Classification partielle. `EXTERNAL_NON_REPLAYABLE` n'est jamais produit ; le classificateur ne lit pas encore `real_extraction_replay`, donc des cas extraction restent `UNDETERMINED`. |
| 7 | Le module prépare le worktree et son manifeste. L'invocation de l'agent appartient désormais à l'orchestrateur, ajouté plus tard ; le statut historique du module reste « partiellement terminée ». Il retire aussi, à la demande de l'orchestrateur, le worktree d'un case mergé. Pour le stage action, il copie aussi `runtime_state.json` et `action_trace.json` (jamais `actions_requested.json`). |
| 11-B | Rejeu des `regression_cases/` différé faute de bibliothèque curée. Le contrôle 11-A et un score `HIGH` ne prouvent pas encore l'absence de régression comportementale. |
| 17 | Mesures disponibles ; décision par catégorie différée jusqu'à plusieurs semaines de données réelles. |
| 18 | Orchestrateur headless en place ; boucle complète en attach live non construite. |
| 19 | Déduplication stricte de cases disponible ; taux de réussite par extracteur non mesurable sans comptage des extractions réussies. |

Voir les sections de même nom dans l'[archive intégrale](SURVEYBOT_AUTOFIX_PLAN_ARCHIVE.md). Les phases closes consignées sont 1A, 2, 3A, 3C sur son périmètre retenu, 4 à 6, 8 à 10, 12 à 16 et 20 ; 3B et 3D restent partielles. Les sous-parties déjà achevées de ces phases partielles sont détaillées dans l'archive.

## Points de vigilance encore consignés

- Phase 4 : mettre à jour `_EXPECTED_BEHAVIOR` avec tout futur changement des validators. Phase 5 : la source 2 est une table statique pour `stage="action"` (fichiers de base et module `input_*` de l'itype) ; il n'en existe pas pour `extraction`, et elle est à revisiter si une vraie table de dispatch apparaît. Archive, Phases 4 et 5.
- Phase 3B : le contenu des scripts, CSS et réponses XHR/fetch externes n'est pas entièrement sanitisé ; les limites précises sont dans l'archive, suites du 25 septembre et Phase 3B.
- Phases 9 et 11-B : le rejeu des cas historiques voisins et la bibliothèque de DOM représentatifs restent différés.
- Orchestrateur : chaque patch est validé contre l'état de départ, pas contre tous les patchs mergés ensuite ; l'extension incrémentale d'un groupe de doublons est différée. Archive, suites 33, 36 et 37.
- Clone dédié : l'import des modules `Survey/` depuis une copie propre a été vérifié ; l'exécution d'un vrai rejeu dans cette copie, les fichiers hors `Survey/` et la complétude des dépendances avec le venv du clone restaient à vérifier dans la suite 42. La suite 46 a ensuite fourni un premier passage supervisé complet, avec les blocages décrits plus haut ; le second passage du 30 septembre a validé en conditions réelles l'exécution des rejeux, du rejeu de patch et du gate depuis `Survey/autofix/`.
- Sous-processus : décodage UTF-8 explicite depuis le 1er octobre 2026. Un `UnicodeDecodeError` cp1252 signalerait un appel non couvert (contournement : `$env:PYTHONUTF8 = "1"`). Les artefacts antérieurs gardent leurs accents corrompus.
- Phase 4 : sur le case MetrixLab, `modules_likely_involved` cite comme entrée de mémoire `_extract_confirmit_wix_fieldset_radio_block`, sans lien apparent avec le signal `consent_modal_radio` ; l'attribution d'entrées de mémoire est à vérifier.
- Dépôt : un fichier suivi par Git avant la règle qui l'ignore reste suivi et peut servir de fausse preuve à un agent dans son worktree. `git ls-files -ci --exclude-standard` (racine du dépôt) les liste ; voir le guide d'exploitation, §6 H.
- Registre d'intégrité : un correctif externe est refusé sans erreur visible si sa fonction core n'est pas enregistrée, ou si son `expected_core_hash` ne correspond plus à la baseline après un `record`. Le test du correctif MetrixLab l'assure pour ce correctif (enregistrement de la déclaration contre la baseline réelle) ; il faut le reproduire pour chaque nouveau correctif.
- Telegram : un clic sur « Approuver » peut arriver avec un retard de plusieurs minutes ; attendre avant de relancer l'orchestrateur et relever plusieurs fois avec `tools\check_human_review.py`. La première décision enregistrée fait foi.
- Un score `HIGH` obtenu sans validation live (Phase 10 `NOT_RUN`) ne couvre que l'extraction rejouée ; la validation en attach reste manuelle.
- Retrait des worktrees : seul un case mergé est nettoyé ; un case rejeté ou en échec garde son worktree. Sous Windows, un processus qui garde un fichier ouvert fait conserver le worktree d'un case mergé (`worktree_cleanup.status = PRESERVED` dans `downstream_run.json`) ; le merge n'est pas affecté.
- Livraison : `build_release_zip.ps1` écrit le manifeste sans BOM et lit le manifeste distant en UTF-8 depuis le 1er octobre 2026 ; avant, un BOM désactivait en silence la garde « version identique ».
- Environnement : la validation statique cherche `ruff` via le `PATH` de l'interpréteur actif ; un `ruff` absent produit un `REJECTED` indiscernable d'un patch rejeté (la cause est dans `reasons` de `validation_static.json`). Le préflight ne le contrôle pas encore. Le 3 octobre 2026, le terminal du clone utilisait le venv de l'origine, sans `ruff`.
- Cascade de clic (bug du bot, ouvert) : sur un nœud sans boîte englobante, la méthode de clic par coordonnées vise (0, 0) et rapporte un succès, ce qui empoisonne le cache de méthode gagnante (`skip_to=3`). Une instrumentation temporaire `[DIAG_CHECK_FORCE]` reste dans le dispatcher. À traiter avec la cause du « not visible » MetrixLab, hors pipeline.
- Agent : une entrée `BOT_EVOLUTION_MEMORY.md` qui consigne une « cause racine non établie » a conduit l'agent à conclure que le bug était déjà documenté et à ne rien faire ; ne pas y consigner de cause non établie. Les tests que l'agent écrit utilisent des données synthétiques qui peuvent s'écarter de la forme réelle du registre ; seul le rejeu de la Phase 9 les confronte au réel.
- Backlog du pipeline, non fait : `ruff` au préflight et statut d'erreur d'environnement distinct d'un rejet ; raison du rejet statique dans `stop_reason` ; codes de raison des clics (`not_visible`, `intercepted`, `not_enabled`) et dédoublonnage des étapes consécutives de la capture ; forme réelle du payload du registre fournie à l'agent pour ses tests ; un journal de niveau debug du validateur (`QT native checked ignored`) contient la valeur de réponse ; `action_trace.json`, copié dans le worktree, contient `requested_actions[].value` ; dans le hook d'action, un `DECLINED` rendu après le budget de 0,25 s est converti en `HANDLED_FAILURE` (le temps du handler compte dans ce budget) ; l'avertissement « workspace has not been trusted » de Claude Code ignore les permissions locales (effet réel non vérifié) ; aucun plafond de tours ou de coût de l'agent n'est évalué (≈ 1 à 2,3 $ par session observée).

## Index de l'archive

L'[archive](SURVEYBOT_AUTOFIX_PLAN_ARCHIVE.md) contient, après son en-tête, l'ancien fichier entier, avec ses titres d'origine ; ses sections d'état sont historiques. Rechercher `## Contexte de travail actuel` pour l'ancien état, `# Phase 1B` ou `# Phase 3` pour les chantiers partiels, `# Phase 1A`, `# Phase 2`, `# Phase 4` à `# Phase 20` pour le détail historique, `# Architecture finale cible` pour le schéma, et `## Ordre concret de développement` pour la chronologie des statuts. Le journal de tête contient les mises à jour datées et les suites 1 à 47.