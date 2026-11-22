# REGISTRE_HORS_SCOPE.md

## 1. Mode d'emploi

**À quoi sert ce fichier.** Ce registre centralise les bugs, défauts, limites connues, points « à trancher », « non vérifiés » et « reste ouvert » qui ont été identifiés au fil des audits/plans/trackings de `Utils/` mais volontairement laissés hors périmètre de l'étape en cours. Il évite que ces constats se perdent dans des documents d'archive très longs.

**Quand ajouter une entrée.** Dès qu'un audit, un diagnostic ou une session de travail met de côté un constat (bug, limite, ambiguïté, décision non prise) qui n'est pas traité immédiatement et qui risquerait sinon de n'être noté que dans un document de travail daté.

**Format d'une entrée.** Voir la structure de fiche en section 3 : identifiant stable, famille, symptôme/constat, impact, origine précise (fichier + section), statut, étape de suivi liée, date si connue.

**Règle « on recense, on ne corrige pas ici ».** Ce fichier ne prescrit aucune solution technique et ne doit jamais devenir un plan d'action. On décrit *le quoi* et *le pourquoi* (impact), jamais *le comment* le corriger. Toute correction se fait dans le code et se documente dans les fichiers de suivi habituels (BDL-x, SURVEYBOT_AUTOFIX_PLAN.md, etc.) ; seule la clôture est ensuite reportée ici (section 4).

**Règle de traçabilité.** Aucune entrée sans citation précise de son origine (fichier Utils + section, ou fonction/fichier de code vérifié en lecture). Un même constat cité à plusieurs endroits = une seule entrée avec plusieurs renvois.

**Règle de statut.** Le statut d'une entrée reste celui constaté dans la source : CONFIRMÉ DANS LE CODE (vérifié par lecture directe du code à une date donnée), HYPOTHÈSE (avancée dans une source mais non testée), NON VÉRIFIÉ (ni confirmée ni infirmée), ou RÉSOLU (clôture explicite retrouvée dans une source). Ne jamais promouvoir une HYPOTHÈSE ou un NON VÉRIFIÉ en CONFIRMÉ sans nouvelle vérification explicite tracée.

---

## 2. Tableau d'index

| ID | Famille | Titre court | Statut | Suivi lié |
|---|---|---|---|---|
| RHS-001 | INFRA | `PROJECT_ARCHITECTURE.md` cité en consigne introuvable dans le dépôt | NON VÉRIFIÉ | — |
| RHS-002 | INFRA | `launch_all.ps1` présent alors que documenté supprimé | CONFIRMÉ (contradiction doc) / NON VÉRIFIÉ (exécution réelle) | — |
| RHS-003 | INFRA | Pipeline Nuitka onefile vs mécanisme réel code\+ZIP+execv, statut vestige non tranché | NON VÉRIFIÉ | — |
| RHS-004 | INFRA | `global_config.py` désynchronisé de la liste documentée (variables en trop/manquantes) | CONFIRMÉ dans le code (partiellement résolu 25/08/2026) | — |
| RHS-005 | LOGS/INFRA | Échec licence Postgres échappe au plafond crash-loop | CONFIRMÉ dans le code | — |
| RHS-006 | LOGS | Plusieurs chemins de sortie n'appellent jamais `record_exit()` (sentinel EXIT_CRASH non écrasé) | CONFIRMÉ (mécanisme) / NON VÉRIFIÉ (occurrence réelle) | — |
| RHS-007 | INFRA/FLUX | Repli NSSM sur EXIT_SOFT_RESTART redémarre avant prise de relais de wake_scheduler | NON VÉRIFIÉ (déduction du code) | — |
| RHS-008 | INFRA/COLLECTE | Erreur Postgres transitoire indiscernable d'un cooldown légitime | CONFIRMÉ dans le code | — |
| RHS-009 | CTA/AUTOFIX | `save_datadome_cookie` inexistante → persistance cookie DataDome inopérante (ImportError avalé) | CONFIRMÉ dans le code | — |
| RHS-010 | ACTION | `MAX_ACTIONS_PER_PLAN` ne peut jamais tronquer un plan (slicing sans effet) | CONFIRMÉ dans le code | — |
| RHS-011 | FLUX | `platforms/ysense.py` stub non protégé si jamais activé | CONFIRMÉ dans le code (scénario non atteignable en build standard) | — |
| RHS-012 | LOGS/COLLECTE | Deux horloges heartbeat (local 300s / Postgres 240s TTL) sans lien explicite | NON VÉRIFIÉ (intention de l'écart) | — |
| RHS-013 | INFRA | Alerte Telegram `_check_ecs_stop_desync` référence une infra Fly.io/ECS abandonnée | CONFIRMÉ dans le code | — |
| RHS-014 | FLUX | `except KeyboardInterrupt` potentiellement mort en contexte NSSM | NON VÉRIFIÉ | — |
| RHS-015 | ACTION/FLUX | Risque de concurrence thread monitoring / thread principal sur le même driver Playwright | CONFIRMÉ (risque théorique documenté par le code) / NON VÉRIFIÉ (occurrence) | — |
| RHS-016 | INFRA | `Dockerfile`/`fly.toml` racine, périmètre réel ambigu (concerne `surveybot-db`) | CONFIRMÉ dans le code | — |
| RHS-017 | DIVERS | Aucun kill-switch manuel trouvé (recherche exhaustive négative) | CONFIRMÉ non trouvé | — |
| RHS-018 | DIVERS | Aucune limite mémoire/ressource explicite (psutil/RSS) | CONFIRMÉ non trouvé | — |
| RHS-019 | DIVERS | Aucune détection de fin de plage horaire fixe | CONFIRMÉ non trouvé | — |
| RHS-020 | CTA/FLUX | Échec DataDome (IP bannie / proxy absent) ne signale pas directement au RuntimeGuard | CONFIRMÉ (comportement) / NON VÉRIFIÉ (issue réelle) | — |
| RHS-021 | FLUX | Échec auth TopSurveys → simple `return`, dégradation aval non garantie | NON VÉRIFIÉ | — |
| RHS-022 | FLUX/INFRA | Pause DAILY_RESET suppose intervention humaine sur proxy expiré, non tranché | NON VÉRIFIÉ | — |
| RHS-023 | FLUX/INFRA | Cooldown `UNTIL_MANUAL` (1 an) après session TopSurveys expirée | CONFIRMÉ dans le code | — |
| RHS-024 | AUTOFIX | Nettoyage manuel des worktrees non mergés à la charge de l'opérateur | Décision actée (limite connue) | — |
| RHS-025 | AUTOFIX | Un patch n'est jamais validé contre les patchs déjà mergés avant lui (seulement contre `base_sha`) | Limite actée, non résolue | suites 35-37 (Phase 7) |
| RHS-026 | AUTOFIX | `BOT_EVOLUTION_MEMORY.md` append-only → conflit textuel attendu entre branches autofix | Comportement accepté | — |
| RHS-027 | AUTOFIX | Un seul poller Telegram à la fois (contrainte d'exploitation) | Contrainte connue, à respecter manuellement | — |
| RHS-028 | AUTOFIX | Worktrees non mergés conservés, nettoyage manuel | Décision actée | 1er oct. 2026 |
| RHS-029 | AUTOFIX/INFRA | Lancement manuel du pipeline sans activer le venv → `ruff` non résolu par `shutil.which` | CONFIRMÉ (mécanisme) | — |
| RHS-030 | AUTOFIX | Récupération des correctifs par l'opérateur explicitement hors périmètre de l'orchestrateur | Décision actée | — |
| RHS-031 | EXTRACTION | `unknown` (type `_detect_itype()`) atteignabilité réelle en sortie non confirmée | NON VÉRIFIÉ | — |
| RHS-032 | EXTRACTION | `rtype` attesté en payload CMix, exposition réelle par `analyze_dom()` non confirmée | NON VÉRIFIÉ | — |
| RHS-033 | EXTRACTION | `question_block_analyzer._infer_itype()` à ne pas mélanger avec `analyze_dom()` | NON VÉRIFIÉ (intégration) ; confirmé non intégré ailleurs (RHS-059/RHS-selon lot2) | voir BDL-4 |
| RHS-034 | EXTRACTION | Validation en exécution du `target_id` dans le registre, non vérifiable hors du processus | NON VÉRIFIÉ | Travaux ultérieurs §8 pt 3 |
| RHS-035 | EXTRACTION | Couverture de validation (`question_block_validator.py`) incomplète sur les 10 `itype` | CONFIRMÉ (constat sur code existant) | Travaux ultérieurs §6 |
| RHS-036 | EXTRACTION | Validateur ne prouve pas que chaque option exposée correspond à une option DOM répondable | Limite documentée | — |
| RHS-037 | EXTRACTION | Validateur n'est pas un inventaire exhaustif des types émis par le corpus | pas précisé | — |
| RHS-038 | EXTRACTION | Types statiquement attestés non observés dans le corpus de snapshots (matrix, number, select, select_rps, textarea, button) | CONFIRMÉ (résultat d'inventaire), qualifié informatif/non bloquant | Travaux possibles après BDL-1 |
| RHS-039 | EXTRACTION | 4 signalements de l'inventaire historique jamais triés (au moment de BDL-1) | NON VÉRIFIÉ à l'origine ; partiellement résolu ensuite (RHS-053) | BDL-4 étape 4 (C5) |
| RHS-040 | EXTRACTION | Décision de ne pas référencer le contrat BDL-1 par l'oracle BDL-4 | Décision différée | — |
| RHS-041 | EXTRACTION/AUTOFIX | Mode strict de l'inventaire automatisé à n'envisager qu'après tri des signalements | Condition explicite avant activation | — |
| RHS-042 | EXTRACTION | Cohérence registre ↔ `target_id` sur sorties réelles, non vérifiée | NON VÉRIFIÉ | — |
| RHS-043 | EXTRACTION | Tests représentatifs par type et pour « Autre » non ajoutés à la clôture de BDL-1 | Travaux ultérieurs, hors critères de clôture | — |
| RHS-044 | COLLECTE | Valeur effective de `SURVEY_SNAPSHOT` sur le parc/en local inconnue | CONFIRMÉ dans le code (défaut) / NON VÉRIFIÉ (valeur effective) | — |
| RHS-045 | COLLECTE | Surcoût navigateur réel des signaux JS du validateur non mesuré | NON VÉRIFIÉ | — |
| RHS-046 | EXTRACTION/LOGS | `question_blocks.json` écrit en `try/except: pass` → perte silencieuse si bloc non sérialisable | CONFIRMÉ (mécanisme) / NON VÉRIFIÉ (fréquence) | — |
| RHS-047 | EXTRACTION | Seules 4 règles d'extraction sur 16 laissent une trace exploitable dans `question_blocks.json` | CONFIRMÉ dans le code | — |
| RHS-048 | EXTRACTION | `option_xpath_map` : survie à la dédup/sérialisation non confirmée dans tous les cas | NON VÉRIFIÉ | — |
| RHS-049 | EXTRACTION | `hidden_block_visible_choice` décline silencieusement au-delà de ses budgets | CONFIRMÉ dans le code | — |
| RHS-050 | EXTRACTION | Comparaison DOM↔représentation (second extracteur) écartée pour l'oracle v1 | Décision explicite | — |
| RHS-051 | EXTRACTION | Aucun cas du corpus n'a de vérité terrain indépendante documentée | CONFIRMÉ (constat du corpus) | — |
| RHS-052 | EXTRACTION | Types non observés dans le corpus = oracle non testé sur ces types (doublon RHS-038) | CONFIRMÉ | renvoi croisé BDL-1 |
| RHS-053 | EXTRACTION | Signalements d'inventaire triés (passés à 11, 2 vrais bugs probables, 2 non détectables, 7 sans objet dont 5 non vérifiés) | RÉSOLU PARTIELLEMENT | Étape 4 C5, 2026-11-16 |
| RHS-054 | COLLECTE | Valeur réelle de `SURVEY_SNAPSHOT` à vérifier avant de se passer du mode collecte (doublon RHS-044) | NON VÉRIFIÉ | — |
| RHS-055 | EXTRACTION | Critères C2/C3 de l'étape 4 satisfaits « à vide » (aucun déclenchement sur 47 blocs) | CONFIRMÉ (constat explicite de l'auteur) | — |
| RHS-056 | EXTRACTION | Angle mort : bloc `qHab` (identifiant interne pris pour la question), oracle `PASS` / vérité `BAD`, rappel 0/1 | CONFIRMÉ (cas réel observé) | 2026-11-15/16 |
| RHS-057 | EXTRACTION | 5 des 7 signalements « sans objet » non vérifiés sur DOM | NON VÉRIFIÉ explicite | C5 |
| RHS-058 | EXTRACTION | Échantillon de 26 pages trop petit pour mesurer le rappel de l'oracle | CONFIRMÉ (constat du corpus) | — |
| RHS-059 | EXTRACTION | Sort de `question_block_analyzer.py` (non intégré) à décider | Hors périmètre explicite ; confirmé non intégré (VERIFICATIONS A.3/A.4) | — |
| RHS-060 | ACTION | Coquilles `_sanitize_instruction_with_page_context` (`prfère`, `rgulièrement`) non corrigées | CONFIRMÉ dans le code, renvoyé hors BDL | — |
| RHS-061 | INFRA/COLLECTE | Ne pas publier `snapshots/` tel quel (URLs à jetons de session) | Signalé, hors périmètre de lecture de ce lot | — |
| RHS-062 | EXTRACTION | Comparaison DOM des 2 cas Qualtrics / 2 cas SurveySip entre eux non faite | NON VÉRIFIÉ | — |
| RHS-063 | EXTRACTION | Contenu détaillé des 4 signalements historiques non examiné (au-delà du tri global) | NON VÉRIFIÉ | — |
| RHS-064 | EXTRACTION | `_is_auxiliary_text_for_choice_group` relu seulement via un document tiers, pas ligne à ligne | NON VÉRIFIÉ | — |
| RHS-065 | EXTRACTION | Fréquence réelle de blocs non sérialisables (doublon RHS-046) | NON VÉRIFIÉ | — |
| RHS-066 | CTA/EXTRACTION | Origine/signification du paramètre `bc=0,0,0,0,0,0,0,1` (CISnet) non confirmées | HYPOTHÈSE | — |
| RHS-067 | CTA/EXTRACTION | `df-fp.js` (Fingerprintjs2) jamais formellement relié au paramètre `bc=` | NON VÉRIFIÉ | — |
| RHS-068 | FLUX | Cas CISnet = déclencheur, pas preuve de généralisation à toutes plateformes | Nuance explicite | — |
| RHS-069 | FLUX | Disqualification PQ2 quasi systématique en prod — cause isolée depuis (interaction humaine) | RÉSOLU (identifié dans le même document) | — |
| RHS-070 | FLUX | Réputation compte/IP comme variable confondante — éliminée depuis | RÉSOLU | — |
| RHS-071 | FLUX | Vitesse de complétion du screener (risque « speeder ») — nuance non totalement écartée | NON VÉRIFIÉ | — |
| RHS-072 | FLUX/LOGS | Commentaire inline `playwright_launcher.py` contredisant le flag anti-détection appliqué | CONFIRMÉ dans le code (commentaire non à jour) | — |
| RHS-073 | FLUX | Réputation IP non totalement isolée même dans le test réussi | NON VÉRIFIÉ, explicitement ouvert | — |
| RHS-074 | DIVERS | Code de diagnostic temporaire CISnet (`DIAG_CAPTURE=1`) prévu non implémenté | Plan non exécuté | — |
| RHS-075 | CTA/EXTRACTION | Sémantique du bitmask `bc=` à décoder empiriquement si différence confirmée | NON VÉRIFIÉ / non réalisé | — |
| RHS-076 | INFRA | Deux explications concurrentes non tranchées pour l'absence de `failure_cases/` sur 2 diagnostics | NON VÉRIFIÉ | — |
| RHS-077 | INFRA | Critère de sélection manuelle des 10 cas promus sur 38 non trouvé dans le code | NON VÉRIFIÉ (hypothèse décision ad hoc) | — |
| RHS-078 | EXTRACTION/COLLECTE | Deux appels `record_validation_failure` sur même session navigateur, origine non tranchée | NON VÉRIFIÉ | — |
| RHS-079 | ACTION | Clusters de même `target_id` répétés jamais comparés au niveau DOM | NON VÉRIFIÉ | — |
| RHS-080 | EXTRACTION/COLLECTE | `frames_count` jamais renseigné sur 38/38 cas | NON VÉRIFIÉ | — |
| RHS-081 | COLLECTE | Présence/absence des artefacts externes non corrélée aux logs debug de collecte | NON VÉRIFIÉ | — |
| RHS-082 | LOGS/INFRA | `page.mhtml` présent sur un cas action alors que le code actuel l'exclut pour ce profil | NON VÉRIFIÉ | — |
| RHS-083 | EXTRACTION/ACTION/INFRA | Reports de missions précédentes non ré-instruits (statut `question_block_analyzer.py`, calculs replay_browser, latences réelles) | NON VÉRIFIÉ, hors périmètre | — |
| RHS-084 | EXTRACTION | Coexistence `QuestionBlock` dataclass vs blocs `dict` | CONFIRMÉ : coexistence non active, `analyze_dom` ne retourne que des dict | — |
| RHS-085 | ACTION | Mode de calcul exact de `target_shapes`/`requested_option_dom_facts` dans `replay_browser.py` | NON VÉRIFIÉ | — |
| RHS-086 | EXTRACTION | Zone `dom_analyzer.py:~300-520` (seuil score≥3) — documentation du seuil clarifiée depuis | Confirmé documenté (docstring) | — |
| RHS-087 | CTA | Chemin exact de `meta["reason"]` pour un cas CTA consommé par `failure_case_builder.py` | CONFIRMÉ : aucun chemin CTA n'existe (VERIFICATIONS A.5) | — |
| RHS-088 | CTA | `driver.last_action_success` mis à `True` dans `click_cta_strong_any_context`, aucun lecteur identifié | NON VÉRIFIÉ | — |
| RHS-089 | ACTION/COLLECTE | Artefacts ACTION écrits seulement en cas d'échec déjà conclu par le validateur (faux négatif invisible) | CONFIRMÉ dans le code | — |
| RHS-090 | ACTION | Aucune trace structurée par stratégie de clic ni par action individuelle d'un plan multi-actions | CONFIRMÉ | — |
| RHS-091 | ACTION | Faux succès `_cdp_click` sur `bounding_box()` vide (clic en 0,0 rapporté succès), empoisonne le cache `skip_to` | CONFIRMÉ dans le code | — |
| RHS-092 | ACTION/CTA | Aucune cible CTA n'entre dans `capture_runtime_state`/le registre | CONFIRMÉ (gap documenté dans le code) | — |
| RHS-093 | ACTION | Aucun contrôle mécanique de qualité du geste (délai borné, précondition testée) | Backlog ouvert, jamais implémenté | SURVEYBOT_AUTOFIX_PLAN.md:161 |
| RHS-094 | CTA | Quasiment rien n'est persisté côté CTA avant la trace BDL-5 (marqueurs, rapport interception, score) | CONFIRMÉ | — |
| RHS-095 | CTA | `_did_progress` : faux positif par conception (deux marqueurs vides → `True`) | CONFIRMÉ dans le code, correction différée (tests PIN) | repris BDL-5 |
| RHS-096 | CTA | Seul détecteur de « transition interne » scopé à un unique gabarit (Ipsos GridProgressive) | CONFIRMÉ | — |
| RHS-097 | CTA | Aucun lien vérifié entre verdict `cta_handler.py` et `failure_recorder.py`/`diagnosis.json` | CONFIRMÉ : aucun chemin n'existe (doublon RHS-087) | — |
| RHS-098 | EXTRACTION | Aucun signal ne distingue « élément absent » de « élément absorbé par une règle historique » | CONFIRMÉ (lacune) | priorité BDL-9 |
| RHS-099 | EXTRACTION | Décisions de dédup/fusion/élagage de `dom_analyzer.py` log-only, non auditables après coup | CONFIRMÉ | — |
| RHS-100 | ACTION/CTA | Vocabulaire fermé de raisons d'échec de clic (`not_visible`, etc.) absent côté `action_dispatcher.py`, existe seulement côté CTA | CONFIRMÉ (clarification de cadrage) | — |
| RHS-101 | ACTION | Post-vérification QT lit un marqueur CSS visuel, pas `checked` natif (auto-rapporté) | CONFIRMÉ dans le code | — |
| RHS-102 | ACTION | Bug réel MetrixLab `radioQT` : `checked` natif non fiable sur input masqué | CONFIRMÉ (cas réel documenté) | — |
| RHS-103 | ACTION/INFRA | `TRACE_REPLAY` sans scripts ne peut pas observer un état posé par le JS du site | CONFIRMÉ (mécanisme) | déjà noté dans SURVEYBOT_AUTOFIX_PLAN.md |
| RHS-104 | EXTRACTION | Signaux `missing_block` gated par provider, ne généralisent pas | CONFIRMÉ (limite de conception) | — |
| RHS-105 | INFRA | `PROJECT_ARCHITECTURE.md` introuvable sous ce nom exact (doublon RHS-001) | NON VÉRIFIÉ | — |
| RHS-106 | ACTION | Substitution silencieuse de la valeur de réponse non observable dans les artefacts, 2 coquilles mots-clés (doublon RHS-060) | CONFIRMÉ dans le code | repris TRACKING §20 quinquies pt 1 |
| RHS-107 | EXTRACTION | Heuristique « champ générique/filtre de recherche » : exclusion silencieuse sans trace structurée | CONFIRMÉ dans le code | — |
| RHS-108 | EXTRACTION | `question_block_analyzer.py` intégralement non intégré, réserve mineure sur import dynamique | CONFIRMÉ avec réserve mineure | décision sur son sort non tranchée |
| RHS-109 | CTA | Aucun chemin de capture de failure case pour un échec CTA (ni stage dédié ni hook) | CONFIRMÉ dans le code | — |
| RHS-110 | INFRA | Pourquoi 2 diagnostics sans `failure_cases/` correspondant (doublon RHS-076) | NON VÉRIFIÉ | — |
| RHS-111 | EXTRACTION | Même cas ou deux cas distincts `20260923_195439`/`20260923_211155` (doublon RHS-078) | NON VÉRIFIÉ | — |
| RHS-112 | ACTION/EXTRACTION | Corrélations plausibles mais non confirmées entre cas collectés et correctifs du plan | NON VÉRIFIÉ, laissées inconnues par discipline de lecture | — |
| RHS-113 | ACTION | Aucune vérité terrain pour 2 cas `replay_ok/` de lifepointspanel.com | inconnue / pas précisé | — |
| RHS-114 | INFRA/COLLECTE | Existence éventuelle de `diagnosis.json` pour 7 cas sans diagnostic, recherche limitée | NON VÉRIFIÉ | — |
| RHS-115 | INFRA/COLLECTE | Tous les points chiffrés de la Mission C (latence CDP, iframes, taille artefacts, `_wait_dom_settle`) non mesurés | NON VÉRIFIÉ | — |
| RHS-116 | ACTION | Trace indirecte éventuelle de la substitution `_sanitize_instruction_with_page_context` non vérifiée sur exemple concret | NON VÉRIFIÉ | — |
| RHS-117 | ACTION/INFRA | Coût théorique d'une capture avant/après inconditionnelle (≈12 MiB, ≈180 appels CDP, jusqu'à 6s d'attente) non mesuré | Estimation théorique, non mesurée | — |
| RHS-118 | ACTION | Oracle d'action v1 (BDL-3) n'exploite pas la transition avant/après, seulement l'état après | Limite actée en v1 | — |
| RHS-119 | ACTION | Valeur réellement appliquée après transformation interne d'une stratégie de clic non capturée | Limite connue, partielle | — |
| RHS-120 | ACTION | Faux échecs des pages « slide » (SPA qui avance seule) alimentant potentiellement AutoFix à tort | Hypothèse tirée des logs, non prouvée | — |
| RHS-121 | ACTION/COLLECTE | Limite de capture sur carrousels de cartes (mêmes coordonnées/option affichée pour tous les groupes) | CONFIRMÉ sur traces réelles (dossiers 00029, 00040) | décision : traiter hors oracle |
| RHS-122 | ACTION | Liste complète « reste ouvert » après clôture BDL-3 (promotion FALSE_POSITIVE, pages slide, carrousels, valeur finale non capturée, couverture dropdown/matrix, précision non mesurée) | Aucune décision prise | — |
| RHS-123 | ACTION/AUTOFIX | BDL-3 clos sans seuil numérique d'acceptation (taux faux positifs/INCONCLUSIVE) | Décision actée, pas de seuil | — |
| RHS-124 | EXTRACTION | Reste ouvert après clôture BDL-4 (précision non démontrée, rappel 0/1, bug Confirmit/Wix renvoyé hors BDL, 5 signalements non vérifiés, échantillon trop petit) | Mixte CONFIRMÉ/NON VÉRIFIÉ, aucune décision prise | — |
| RHS-125 | CTA | BDL-5 v1 — limites connues (clic mixte classé `observed` à tort, clic natif+retour faux classé `no_click`, chemins executor/dispatcher invisibles, buffer non consommé, pas d'oracle CTA) | CONFIRMÉ, livré ainsi | — |
| RHS-126 | CTA | `_did_progress` : trois voies vers `True` sans preuve de progression, figées par tests PIN (doublon RHS-095) | CONFIRMÉ dans le code, correction différée | — |
| RHS-127 | CTA | `arm_interceptor` retourne `True` même quand l'armement JS a échoué | CONFIRMÉ dans le code | — |
| RHS-128 | CTA | 5 clics CTA contournent le point de passage commun `_click_with_intercept` (invisibles à la trace v1) | CONFIRMÉ | — |
| RHS-129 | CTA | Reste ouvert après clôture BDL-5 (mesures en run réel absentes, `_did_progress` toujours défaillant, signal document absent, chemins executor/dispatcher non couverts) | Aucune décision prise | — |
| RHS-130 | CTA/COLLECTE | BDL-5 v2 — trace CTA non installée par défaut en prod (`SURVEY_OBSERVABILITY=0`) | CONFIRMÉ dans le code | — |
| RHS-131 | CTA/COLLECTE | BDL-5 v2 — `notifSig` scopé à un seul gabarit, signal faible ; fiabilité du signal `url` complet non vérifiable sans corpus | CONFIRMÉ pour les points `[V]`, `[NV]` pour fiabilité `url` | — |
| RHS-132 | CTA | Règles de départ de l'oracle CTA qualifiées d'hypothèse à geler après corpus réel (`qNodes`, signaux faibles `txt`/`notifSig`/`progGridSig`) | Hypothèse non validée | — |
| RHS-133 | CTA/COLLECTE | BDL-5 v2 — reste ouvert (chemin de remontée du corpus flotte→dev non défini, sensibilité signal `url` aux query, corpus presque entièrement PASS) | NON VÉRIFIÉ | — |
| RHS-134 | CTA | Angle mort CTA conservé et déclaré en v2 (chemins executor/dispatcher invisibles, signal `redirect_watcher` indépendant hors trace) | CONFIRMÉ, décision explicite de ne pas étendre | — |
| RHS-135 | AUTOFIX/COLLECTE | BDL-6 (mesure de qualité) différé, attend la collecte CTA | À FAIRE | — |
| RHS-136 | AUTOFIX/COLLECTE | BDL-7 (échantillonnage des PASS) : à faire, aucune implémentation | À FAIRE | — |
| RHS-137 | AUTOFIX | BDL-8 (IA d'interprétation) : optionnel, non engagé | Optionnel, non commencé | — |
| RHS-138 | EXTRACTION/ACTION | BDL-9 (retrait des règles parasites) : cap long terme, prérequis non tous remplis | Long terme | — |
| RHS-139 | AUTRE (tests/CI) | 12 échecs de tests préexistants non instruits (`test_action_validator_qt_native_checked_exclusion`) | NON VÉRIFIÉ, restant à instruire | — |
| RHS-140 | INFRA/COLLECTE | Surcoût réel d'un run local avec `SURVEY_BDL_COLLECT` actif non mesuré | Restant à faire | — |
| RHS-141 | COLLECTE | Règle d'échantillonnage/annotation des PASS collectés non définie | Restant à faire | — |
| RHS-142 | AUTRE (licence) | `CAPTCHA_PROVIDER` exclue de la migration GLOBAL_CONFIG, cause non investiguée | NON VÉRIFIÉ | — |
| RHS-143 | AUTRE (licence) | Pas de log explicite sur échec silencieux d'import `_license_config` | Point ouvert, non traité | — |
| RHS-144 | AUTRE (licence) | `license_guard.py` importe via préfixe `surveybot.` incorrect → `check_license_or_exit()` échoue silencieusement | CONFIRMÉ dans le code, non corrigé | tâche séparée requise |
| RHS-145 | INFRA | `sys.executable` en onefile Nuitka non testé en conditions réelles (cycle complet build+update) | Corrigé mais non testé en conditions réelles | — |
| RHS-146 | INFRA | Compatibilité `_replace_source_and_restart` avec pipeline tiers (binaire onefile sans dossier `code\`) | NON VÉRIFIÉ, à tester avant envoi à un récepteur | — |
| RHS-147 | INFRA | `psycopg2-binary` — suivi automatique Nuitka non confirmé au premier build réel | NON VÉRIFIÉ | — |
| RHS-148 | INFRA | `tzdata` détecté par diagnostic, pas validé par build réel | NON VÉRIFIÉ | — |
| RHS-149 | AUTRE (config) | Workflow dev local dépendant de `Utils/config` retiré, à vérifier en usage réel | NON VÉRIFIÉ | — |
| RHS-150 | AUTRE (sécurité/audit) | Heuristique de l'audit `os.getenv` : alias/import indirect non détecté | Limite connue de l'audit | — |
| RHS-151 | INFRA | Synchronisation manuelle `receiver_config.json` multi-machines, aucun mécanisme réseau | À documenter côté utilisateur, pas résolu par code | — |
| RHS-152 | INFRA | Packer/protecteur natif (VMProtect/Themida) non retenu dans l'immédiat | Différé | — |
| RHS-153 | ACTION/FLUX | Comportements spécifiques non-headless à re-vérifier avant bascule headless parc | NON VÉRIFIÉ | — |
| RHS-154 | ACTION/FLUX | Pas de test de lancement réel du patch anti-fingerprinting (compilation/relecture seulement) | NON VÉRIFIÉ | — |
| RHS-155 | CTA/AUTOFIX | `--lang` aligné sur `SURVEY_LANG` non revérifié par log dédié | NON VÉRIFIÉ | — |
| RHS-156 | COLLECTE | Aucune mesure chiffrée avant/après du taux de captcha | Suggéré, pas fait | — |
| RHS-157 | INFRA | Snapshot S3 (boto3/botocore, ~70 Mo) — question de l'alourdissement binaire à re-questionner | Différé | — |
| RHS-158 | INFRA | Process Chrome orphelins si timeout de fermeture NSSM dépassé | Risque résiduel non traité | — |
| RHS-159 | INFRA | Validation en conditions réelles non faite pour l'ensemble des correctifs d'orchestration | NON VÉRIFIÉ | — |
| RHS-160 | INFRA | Sentinel EXIT_CRASH non écrasé sur 3 chemins de sortie restants (doublon RHS-006) | CONFIRMÉ, non corrigé | — |
| RHS-161 | INFRA | Correctif `-ExecutionPolicy Bypass` non redéployé sur tout le parc | CONFIRMÉ comme risque, non corrigé sur le parc | — |
| RHS-162 | INFRA | Launcher venv moderne : PID différent, `stop_bot.ps1` potentiellement inopérant | Comportement préexistant, NON VÉRIFIÉ sur le parc | — |
| RHS-163 | INFRA | Marqueur `manual_stop` ne bloque pas le démarrage auto du service NSSM au boot | Hors périmètre, non investigué | — |
| RHS-164 | INFRA | Cycle `stop_bot_manual.ps1` → `wake_scheduler.ps1` non validé en conditions réelles | NON VÉRIFIÉ | — |
| RHS-165 | INFRA | `stop_bot_manual.ps1` no-op silencieux si bot lancé via `launch_all.ps1` sans service NSSM | Documenté, hors périmètre du patch, non corrigé | — |
| RHS-166 | INFRA | Bascule NSSM→PID/`launch_all.ps1` : cycle complet non validé en conditions réelles | NON VÉRIFIÉ | — |
| RHS-167 | ACTION/AUTOFIX | Mode gel `FREEZE_ON_TRIGGER` non validé en conditions réelles | NON VÉRIFIÉ | — |
| RHS-168 | ACTION/AUTOFIX | Cascade de gels possible pour un même événement de pause | Conséquence assumée | — |
| RHS-169 | AUTOFIX | `freeze_and_wait()` boucle sans limite (dérogation à la règle de budget borné) | Exception assumée | — |
| RHS-170 | INFRA | `launch_all.ps1::Start-Bot` duplique les défauts GEO/SURVEY_LANG/TZ avec `nssm_setup_bot.ps1` | CONFIRMÉ, non corrigé | — |
| RHS-171 | EXTRACTION/ACTION | `Survey/input_slider.py::set_sliderpoints()` résidu Selenium `find_elements` cassé silencieusement depuis retrait du shim | CONFIRMÉ dans le code, non corrigé | patch dédié hors périmètre |
| RHS-172 | EXTRACTION/ACTION | Autres résidus `find_elements`/`find_element` string-literal potentiellement non détectés | NON VÉRIFIÉ | — |
| RHS-173 | ACTION | `el.is_selected()` résiduel ailleurs que les 2 fonctions corrigées, non ré-audité | NON VÉRIFIÉ | — |
| RHS-174 | ACTION/EXTRACTION | Résidus shim dans les blocs attach partiels — règle de lecture générale (classement « migré » trompeur) | RÉSOLU (nettoyé en BLOC S7b), avertissement général conservé | — |
| RHS-175 | AUTOFIX | Les fichiers ignorés par Git ne sont jamais propagés vers le clone autofix | Toujours ouvert (doublon RHS-014 lot1/lot3) | — |
| RHS-176 | INFRA | Lanceur de tâche planifiée : sync automatique avant un run incertaine | À VÉRIFIER | — |
| RHS-177 | AUTOFIX | Aucune preuve que l'orchestrateur fonctionne avant un vrai passage supervisé (au moment du guide) | NON VÉRIFIÉ (réalisé depuis, voir lot5 RHS-016bis) | — |
| RHS-178 | INFRA | Tâche planifiée pourrait ne pas être enregistrée (le plan ne le confirme pas) | NON VÉRIFIÉ | — |
| RHS-179 | AUTOFIX | `Reset-AutofixCase` : tests synthétiques seulement, pas encore observée sur vrai cas (au moment du guide) | Tests synthétiques seulement | 01/10/2026 |
| RHS-180 | LOGS | Accents illisibles dans artefacts générés par sous-process (encodage page de code Windows) | Correction à faire dans un chantier séparé, non corrigé | — |
| RHS-181 | FLUX | Rejeu statique `REPRODUIT` vs rejeu fidèle `NON_REPRODUIT` (cas MetrixLab) — fausse confiance du diagnostic | À éclaircir avant de compter sur la Phase 9 | — |
| RHS-182 | AUTOFIX | Avertissement Claude Code « workspace not trusted » ignorant `permissions.allow` | À vérifier au prochain run | 04/10/2026 |
| RHS-183 | AUTOFIX | `--max-budget-usd` : comportement en cas de dépassement non observé | NON OBSERVÉ | 04/10/2026 |
| RHS-184 | AUTOFIX | `--max-attempts` (seconde tentative) jamais exercée sur un cas réel | NON EXERCÉ | 04/10/2026 |
| RHS-185 | INFRA | Préflight ne contrôle pas `ruff` comme cause distincte d'un rejet de validation statique | Constaté, non corrigé | — |
| RHS-186 | COLLECTE | `live_validation_pending` non exercé sur un cas réel | NON EXERCÉ | — |
| RHS-187 | AUTOFIX | `agent_final_summary` absent si formatage Markdown autour du libellé RÉSUMÉ | Constaté, replis fragile | — |
| RHS-188 | ACTION | Cause racine non établie du bug radio `radioQT` (clic jugé non visible), second cycle | Cause racine non établie, laissée ouverte (côté dispatcher) | — |
| RHS-189 | EXTRACTION | Phase 3D : `EXTERNAL_NON_REPLAYABLE` jamais produit, classificateur ne lit pas `real_extraction_replay` | Explicitement partiel/non fait | Phase 3D |
| RHS-190 | EXTRACTION | Phase 3B.3/3B.4 : Frames et Shadow DOM fermé différées faute de volume de cas | Différé, explicitement hors périmètre | Phase 3B.3/3B.4 |
| RHS-191 | EXTRACTION/ACTION | Phase 3/9 : état posé par JS du site non observable en rejeu sans scripts, divergence non signalée | Reste non traité | Phase 3/9 |
| RHS-192 | AUTOFIX | Phase 11-B (rejeu `regression_cases/`) différée faute de bibliothèque curée | Différé | Phase 11-B |
| RHS-193 | COLLECTE | Phase 17 : décision par catégorie différée jusqu'à plusieurs semaines de données réelles | Différé | Phase 17 |
| RHS-194 | AUTOFIX | Phase 18 : boucle complète en attach live non construite | Partiel | Phase 18 |
| RHS-195 | COLLECTE/AUTOFIX | Phase 19 : taux de réussite par extracteur non mesurable, dédup cases d'action non couverte | Partiel | Phase 19 |
| RHS-196 | EXTRACTION | Phase 5 : pas de vraie table de dispatch pour l'extraction (seulement pour l'action) | Hors périmètre temporaire | Phase 5 |
| RHS-197 | EXTRACTION/COLLECTE | Phase 3B : contenu scripts/CSS/XHR externes pas entièrement sanitisé (seule l'URL l'est) | Limite connue, non résolue | Phase 3B |
| RHS-198 | AUTOFIX | Orchestrateur : patch validé seulement contre `base_sha`, pas contre patchs mergés ensuite (doublon RHS-025) | Différé | suites 33,36,37 |
| RHS-199 | EXTRACTION | Phase 4 : `modules_likely_involved` cite une fonction sans lien apparent avec le signal (cas MetrixLab) | NON VÉRIFIÉ | — |
| RHS-200 | AUTOFIX | Registre d'intégrité : refus silencieux si fonction non enregistrée, vérifié pour un seul correctif | Partiellement couvert | — |
| RHS-201 | INFRA | Retrait des worktrees : process Windows gardant un fichier ouvert fait persister un worktree `PRESERVED` | Constaté, contournement manuel documenté | — |
| RHS-202 | INFRA | Absence de `ruff` → `REJECTED` indiscernable d'un vrai rejet (incident réel du 3/10/2026) | Partiellement atténué, préflight toujours sans contrôle ruff (doublon RHS-185) | — |
| RHS-203 | ACTION | Cascade de clic : clic par coordonnées sur nœud sans boîte englobante vise (0,0), empoisonne le cache `skip_to` (doublon RHS-091) | Cause établie, non corrigée dans le dispatcher | — |
| RHS-204 | AUTOFIX | Agent concluant à tort qu'un bug est déjà documenté via une entrée BOT_EVOLUTION_MEMORY « cause non établie » | RÉSOLU par consigne (pas de garde mécanique) | — |
| RHS-205 | ACTION/AUTOFIX | Backlog du pipeline non fait (contrôles mécaniques de qualité du geste non branchés, voir sous-points) | NON FAIT | — |
| RHS-206 | AUTOFIX | Corpus de pages saines (anti garde trop large) absent | NON FAIT | Phase 11-B |
| RHS-207 | ACTION/COLLECTE | Snapshot runtime_state.json non étendu aux frères visibles de l'option / élément visé par stratégie | NON FAIT | — |
| RHS-208 | AUTOFIX | Validation live automatique à la réapparition d'un incident (canary) absente | NON FAIT | — |
| RHS-209 | ACTION | Décision d'architecture : correctif exécuté seulement après échec du chemin historique (`action/after` interdit) | À TRANCHER | — |
| RHS-210 | AUTOFIX | Relevé des décisions Telegram sans invocation manuelle / re-notification non implémenté | NON FAIT | — |
| RHS-211 | AUTOFIX | Console `validate_patch_live` : raison d'un NON_CONCLUANT opérationnel non affichée, bug UX `<case_id>` littéral | NON FAIT (bug UX connu) | — |
| RHS-212 | AUTOFIX | Outils manuels créent une fausse issue terminale dans l'historique pour un case sans worktree | Risque connu, non corrigé | — |
| RHS-213 | LOGS/COLLECTE | Journal debug validateur (`QT native checked ignored`) contient la valeur de réponse | Constaté, non corrigé | — |
| RHS-214 | LOGS/COLLECTE | `action_trace.json` copié dans le worktree contient `requested_actions[].value` | Constaté, non corrigé | — |
| RHS-215 | AUTOFIX | Budget 0,25s du hook potentiellement trop serré ; choix de modèle agent non tranché | NON VÉRIFIÉ / À TRANCHER | — |
| RHS-216 | AUTOFIX | Rejet statique dû à l'environnement compte comme issue négative et bloque un nouvel essai | Constaté | — |
| RHS-217 | AUTOFIX | Règle sur les tests ajoutée au prompt par remplacement d'une phrase précise, fragile | Constaté, risque identifié | — |
| RHS-218 | INFRA | `cssselect` requis par le pilote statique, point d'attention | Constaté | — |
| RHS-219 | AUTOFIX | Boucle bornée ne relance pas l'agent après rejeu non concluant | Constaté, comportement voulu mais limite notée | — |
| RHS-220 | AUTOFIX | Diagnostic produit sur code contenant déjà le correctif fusionné pollue le cas de test | Constaté | — |
| RHS-221 | AUTOFIX | Test utilisant identifiant/valeur d'un cas réel peut faire conclure à tort que le cas est déjà résolu | RÉSOLU en partie par consigne | 3/10/2026 |
| RHS-222 | AUTOFIX | Tâche 7 (cycle de vie des correctifs) partiellement implémentée | Partiel, explicite | Phase 11-B |
| RHS-223 | AUTOFIX | Alternative architecturale (rejet par Phase 11-A de tout corps de fonction modifié) non décidée | À TRANCHER | — |
| RHS-224 | EXTRACTION | 81 stratégies `_extract_*` non enregistrées dans le registre d'intégrité | Constaté, non résolu | — |
| RHS-225 | AUTOFIX/SÉCURITÉ | Phase 11-A n'authentifie pas cryptographiquement l'auteur de `expected_change.json` | Limite architecturale explicite | — |
| RHS-226 | AUTOFIX | Phase 11-A ne démontre pas une absence de régression comportementale | Explicitement non fait | Phase 11-B |
| RHS-227 | AUTOFIX/INFRA | Déploiement binaire compilé futur ferait refuser toutes les déclarations de correctifs sans erreur visible | Limite identifiée, hors périmètre actuel | — |
| RHS-228 | AUTOFIX | Mesure de cohérence `expected_core_hash` non généralisée à toute déclaration future | À faire, non appliqué systématiquement | — |
| RHS-229 | ACTION | Correctif d'action rejeté (3/10) : `DomCondition` évaluée avant l'action, garde trop large | Constaté, corrigé seulement par consigne | — |
| RHS-230 | ACTION | Correctif d'action confirmé en live mais à resserrer avant merge origine (pas de condition d'échec testée, clic sans délai borné) | TODO explicite, non fait au moment de l'écriture | — |
| RHS-231 | AUTOFIX | Raison de verdict dépendante de la vitesse machine (ex. `handler_returned_failure` vs `post_handler_timeout`) | RÉSOLU pour ce cas, principe général en vigilance | 5/10/2026 |
| RHS-232 | AUTOFIX | Correctif fusionné modifie les diagnostics suivants du même cas (pollution des tests futurs, doublon RHS-220) | Constaté | 5/10/2026 |
| RHS-233 | EXTRACTION/ACTION | Faits DOM mesurés sans scripts ne confirment pas un effet produit par le JS du site | Limite explicite, non résolue | — |
| RHS-234 | AUTOFIX | Dispatcher jamais réellement réexécuté par le replay statique (stage action) | CONFIRMÉ à l'origine | RÉSOLU par 3C.4 |
| RHS-235 | EXTRACTION/FLUX | Sélection de frame hors de portée du replay (`child_frames` toujours vide) | NON VÉRIFIÉ / toujours ouvert | Phase 3B.3/3C.3 |
| RHS-236 | EXTRACTION | Shadow DOM fermé non capturé (3B.4) | Différé explicitement | — |
| RHS-237 | ACTION | Validators ne vérifient jamais le DOM quand `dispatcher_success=true` (5e famille 1B potentielle) | NON CONFIRMÉ par un cas réel | — |
| RHS-238 | COLLECTE | MutationObserver perdu si navigation avant collecte | RÉSOLU (champ `observer_present` ajouté) | Phase 3B.6 |
| RHS-239 | ACTION/EXTRACTION | Flag `qarts_widget` posé inconditionnellement sans vérifier l'overlay réel | RÉSOLU (même jour) | — |
| RHS-240 | EXTRACTION | Doute non tranché : vraie condition de course en production (carrousel Qualtrics) | NON TRANCHÉ explicite | — |
| RHS-241 | EXTRACTION/AUTOFIX | Limite structurelle : un DOM figé ne peut jamais rejouer une vraie condition de course | Limite structurelle actée, non résoluble | — |
| RHS-242 | AUTOFIX | `replayability_classifier` ne lit pas `real_extraction_replay` (doublon RHS-189) | Non câblé, délibérément différé | — |
| RHS-243 | AUTOFIX/INFRA | Lanceur de tâche planifiée écrivait dans l'arbre Git du clone (hors .gitignore) | CORRIGÉ (suite 40) | — |
| RHS-244 | INFRA | Fichiers locaux ignorés par Git requis à l'exécution dans clone/worktree — partiellement clos | Partiellement clos, points (a)(b)(c) toujours ouverts | — |
| RHS-245 | INFRA | RepetitionDuration `[TimeSpan]::MaxValue` rejeté par `Register-ScheduledTask` | RÉSOLU (suite 42) | — |
| RHS-246 | AUTOFIX | Anomalie Phase 11-A : Claude Code sans Bash autorisé n'a écrit aucun patch malgré 18/57 mésappariements | Diagnostiqué, non corrigé à la clôture | — |
| RHS-247 | AUTOFIX | Registre `extractor_integrity.json` désynchronisé du code réel indépendamment de tout patch | Diagnostiqué, correction volontairement non automatisée | — |
| RHS-248 | AUTOFIX | Doublon : double extraction en Phase 4 (`_attempt_real_extraction_replay` redondant) | NON CORRIGÉ, signalé explicitement | — |
| RHS-249 | AUTOFIX/INFRA | Fermeture réelle de la page live sur timeout (Phase 10) — risque sur page CDP live | RÉSOLU (`close_page_on_timeout=False`) | — |
| RHS-250 | EXTRACTION | Limites non vérifiées du signal `hidden_block_visible_choice` (rejeu fidèle, faux positifs modale/référentiel) | NON VÉRIFIÉ | — |
| RHS-251 | EXTRACTION | Correction d'extraction MetrixLab (modale masquée vs question visible) non faite, seule la détection l'est | Toujours non corrigé, volontairement reporté | — |
| RHS-252 | COLLECTE | Sanitisation : seule l'URL est nettoyée, jamais le contenu (scripts/CSS/XHR) | Limite actée, jamais traitée | — |
| RHS-253 | COLLECTE | Retrait de la query string rend indiscernables deux ressources différentes | Limite actée, non résolue | — |
| RHS-254 | COLLECTE | Tension non résolue entre fidélité d'exécution et retrait de données de session | Tension assumée, non résolue | — |
| RHS-255 | COLLECTE | `@import` et feuilles de style hors `<link>` non collectées | Limite actée, non traitée | — |
| RHS-256 | COLLECTE | Script inséré puis retiré du DOM avant capture, non collecté | Limite actée, non traitée | — |
| RHS-257 | AUTOFIX | `_EXPECTED_BEHAVIOR` maintenue à la main sans mécanisme de synchronisation | Point de vigilance permanent, pas de garde-fou | — |
| RHS-258 | AUTOFIX | Aucune table de dispatch énumérable (`context_selector.py` source 2 contribue 0 fichier) | Documenté comme limite actuelle, vraie aujourd'hui pas garantie dans le temps | — |
| RHS-259 | AUTOFIX | `PROTECTED_BRANCHES` structurellement inatteignable dans `autofix_worktree.py` | Point de vigilance mineur non bloquant, jamais traité | — |
| RHS-260 | AUTOFIX | Phase 16 (double confirmation Telegram) sort de la chaîne orchestrée, métriques Phase 17 ne la détectent plus | NON TRAITÉ, signalé explicitement | — |
| RHS-261 | AUTOFIX | Clé de notification « blocked » unique (second blocage sur même case ne renotifie pas) | Limite assumée, non résolue | — |
| RHS-262 | INFRA | Merge exige un dépôt principal propre — conséquence sur dossiers d'artefacts à vérifier | À vérifier par git status, non confirmé traité | — |
| RHS-263 | COLLECTE | Tableau de taux de réussite par extracteur (Phase 19) bloqué, aucune source ne compte les extractions réussies (doublon RHS-195) | Contribution partielle seulement, non résolu | — |
| RHS-264 | AUTOFIX | Mécanisme de merge automatique par catégorie (Phase 17) différé volontairement | Différé volontairement | — |
| RHS-265 | INFRA | Secrets exposés en clair (clé OpenAI, 2Captcha, token Telegram) lors de la mise en place du clone | Rotation recommandée, non confirmée effective | — |
| RHS-266 | CTA | Le clic CTA est effectué même lorsque l'exécution des actions a échoué ou qu'aucune réponse n'a été saisie | CONFIRMÉ DANS LE CODE (2026-11-21) : `Survey/survey_executor.py:2584-2627` — `result` (retour de `execute_actions_plan`) n'est jamais inspecté avant le bloc de clic CTA ; les seules gardes sont `_should_skip_post_actions_navigation` et `_has_unfilled_required_inputs` | — |
| RHS-267 | CTA/EXTRACTION | Après abandon DOM-only à zéro bloc, le flux peut cliquer le CTA sans avoir répondu à quoi que ce soit | CONFIRMÉ DANS LE CODE (2026-11-21) : `Survey/survey_executor.py:2657-2789`, branche « DOM-only: aucun input exploitable (abort) » qui recherche et clique un CTA (sélecteurs puis texte) sans qu'aucune réponse n'ait été produite en amont | — |
| RHS-268 | EXTRACTION | Une page analysée à zéro bloc est collectée comme extraction sans verdict de l'oracle d'extraction | NON VÉRIFIÉ (non confirmé par lecture directe dans cette passe ; cohérent avec RHS-046/RHS-055 sur l'absence de déclenchement de l'oracle, mais le chemin précis « 0 bloc → pas de verdict » n'a pas été localisé dans le code lors de cette vérification) | — |
| RHS-269 | CTA | Le marqueur de progression persiste le texte tronqué à 120 caractères alors que le signal est calculé sur une empreinte de 220 caractères ; bruit d'interface possible en tête de conteneur sur certaines plateformes | CONFIRMÉ DANS LE CODE (2026-11-21) : `Survey/cta_handler.py:623-626` calcule `txt` sur 220 premiers caractères (`.slice(0, 220)`) ; `Survey/cta_trace.py:74,98,239-247` (`_MARKER_STR_CAP = 120`) tronque ce même champ à 120 caractères au moment de la persistance (`compact_marker`) — un écart détecté entre les caractères 121 et 220 n'est donc pas visible dans les données collectées. Le volet « bruit d'interface (sélecteur de langue, région vocale) en tête de conteneur » reste NON VÉRIFIÉ (dépend du DOM réel d'une plateforme donnée, non testé) | — |
| RHS-270 | CTA | Le garde de saut du CTA après auto-navigation (`_should_skip_post_actions_navigation`) pourrait ne pas détecter un auto-avancement se produisant dans un iframe | HYPOTHÈSE, NON VÉRIFIÉE (2026-11-21) : la fonction (`Survey/survey_executor.py:1846+`) compare `page.url` et une signature DOM (`redirect_watcher._dom_signature`) au niveau document principal ; si ces deux fonctions ne recursent pas dans les frames, un changement confiné à un iframe échapperait à la détection — non confirmé par lecture de `_dom_signature` dans cette passe | — |
| RHS-271 | EXTRACTION/ACTION | Plateforme où l'extraction retourne un groupe de choix sans options (validation d'extraction en échec), suivi d'un échec de toutes les stratégies de sélection | NON VÉRIFIÉ dans cette passe (cohérent avec le signal `choice_without_options` déjà cité dans BUG_DETECTION_LAYER_TRACKING.md, section BDL-4, mais le cas précis décrit ici n'a pas été relocalisé nommément) | voir RHS-124 |
| RHS-272 | LOGS | La liste complète des requêtes XHR du capsule de navigateur serait réimprimée plusieurs fois par étape | NON VÉRIFIÉ : `Survey/browser_capsule.py` (`collect_xhr_fetch_resources`, lignes ~609-668) ne log qu'une ligne debug par URL en erreur et un résumé de troncature, pas de réimpression répétée identifiée dans cette fonction lors de cette vérification ciblée ; le mécanisme de réimpression décrit n'a pas été localisé | — |
| RHS-273 | COLLECTE | Une fois le plafond de dossiers atteint pour une racine, la collecte est suspendue définitivement, biaisant le corpus vers les premières exécutions d'une machine | CONFIRMÉ DANS LE CODE (2026-11-21) : `Utils/BUG_DETECTION_LAYER_CORPUS_INDEX.md` documente le mécanisme (« volume borné par un plafond de dossiers avec abandon contrôlé ») ; confirmé aussi dans le code applicatif de collecte (grep `plafond atteint ... collecte suspendue dans`, mécanisme répété pour cas, extraction et CTA) — le plafond, une fois atteint, n'est pas rouvert dynamiquement (pas de purge/rotation identifiée) | — |
| RHS-274 | CTA | `CTA_INTERCEPT_ONLY` déclaré obsolète par l'utilisateur mais son code reste présent dans le cœur protégé | CONFIRMÉ DANS LE CODE (2026-11-21) : `Survey/cta_handler.py` (lignes 41, 946, 1655, 2563, 2585, 2815, 2839) et `Survey/survey_executor.py` (lignes 2735-2786, via `is_cta_intercept_only()`) utilisent encore activement la variable — code non retiré. Le caractère « obsolète » relève d'une déclaration de l'utilisateur, non d'un constat du code lui-même | retrait éventuel = chantier séparé |
| RHS-275 | CTA | Les chemins CTA propres à l'exécuteur de survey et au dispatcher d'actions ne sont pas couverts par la trace CTA | CONFIRMÉ DANS LE CODE (recoupe RHS-134) : `Utils/BUG_DETECTION_LAYER_TRACKING.md` section BDL-5 v2 « Angle mort conservé et déclaré » — chemins `survey_executor.py`/`action_dispatcher.py` hors trace, décision explicite de ne pas étendre (complexité jugée trop coûteuse) | — |
| RHS-276 | CTA | Deux comportements « slider » distincts du point de vue du clic CTA : défilement automatique (normal) vs navigation interne par flèche (risque de faux SUSPECTED/INCONCLUSIVE) | NON VÉRIFIÉ (mode 2 non observé sur corpus réel ; absence de chemin de code dédié confirmée pour le point de passage CTA) | BDL-5 v2 (oracle CTA) |

---

## 3. Fiches détaillées

> Les fiches ci-dessous reprennent, par famille, les entrées de la section 2 avec l'ensemble des champs requis (symptôme/constat, impact, origine précise, statut, suivi, date). Pour les entrées très proches (doublons signalés dans l'index), le renvoi croisé figure dans la fiche plutôt qu'une répétition intégrale.

### CTA

**RHS-266 — Clic CTA sans condition de passage sur le résultat du plan d'actions**
Symptôme : après `action_dispatcher.execute_actions_plan(...)`, la variable `result` n'est lue par aucune condition avant le bloc de clic CTA ; seules deux gardes existent (`_should_skip_post_actions_navigation`, `_has_unfilled_required_inputs`), aucune ne porte sur le succès de l'exécution des actions ou sur le fait qu'une réponse ait été réellement saisie.
Impact : un CTA peut être cliqué après un échec d'exécution d'actions, faisant potentiellement avancer un survey sans réponse valide enregistrée.
Origine : `Survey/survey_executor.py:2584-2627` (vérifié en lecture directe le 2026-11-21).
Statut : CONFIRMÉ DANS LE CODE.
Suivi lié : aucun identifié dans les fichiers Utils à ce jour.

**RHS-267 — CTA cliqué après abandon DOM-only à zéro bloc**
Symptôme : la branche « DOM-only: aucun input exploitable (abort) » (`else` du bloc principal) recherche directement un bouton CTA par sélecteurs CSS puis par texte, sans qu'aucun bloc de question n'ait été traité.
Impact : un passage de page sans aucune réponse peut se produire.
Origine : `Survey/survey_executor.py:2657-2789` (vérifié le 2026-11-21).
Statut : CONFIRMÉ DANS LE CODE.
Suivi lié : —

**RHS-269 — Troncature du marqueur de progression à 120 caractères vs empreinte de 220**
Symptôme : `_dom_progress_marker` (cta_handler.py) calcule le champ `txt` sur les 220 premiers caractères du conteneur principal ; `compact_marker` (cta_trace.py) tronque ce même champ à 120 caractères (`_MARKER_STR_CAP`) avant persistance.
Impact : une différence de texte détectée entre les caractères 121 et 220 (utilisée en direct par `signals_changed` avant troncature) n'est pas visible dans les données collectées/persistées, rendant l'analyse a posteriori incomplète.
Origine : `Survey/cta_handler.py:623-626` ; `Survey/cta_trace.py:74,98,239-247` (vérifié le 2026-11-21).
Statut : CONFIRMÉ DANS LE CODE pour la troncature. Le volet « bruit d'interface en tête de conteneur sur certaines plateformes » reste NON VÉRIFIÉ.
Suivi lié : —

**RHS-270 — Garde de saut CTA après auto-navigation potentiellement aveugle aux iframes**
Symptôme : `_should_skip_post_actions_navigation` compare l'URL de la page principale et une signature DOM (`redirect_watcher._dom_signature`) au niveau document ; si un auto-avancement se produit dans un iframe sans changer l'URL top-level ni la signature observée, la garde ne le détecterait pas.
Impact potentiel : un clic CTA redondant ou mal synchronisé sur une page où l'avancement a déjà eu lieu dans un iframe.
Origine : `Survey/survey_executor.py:1846+` (fonction lue, mais `_dom_signature` non tracée jusqu'au bout dans cette passe).
Statut : HYPOTHÈSE, NON VÉRIFIÉE.
Suivi lié : —

**RHS-274 — `CTA_INTERCEPT_ONLY` présent dans le cœur protégé malgré déclaration d'obsolescence**
Symptôme : la variable `CTA_INTERCEPT_ONLY` continue d'être lue et de conditionner du comportement à plusieurs endroits de `cta_handler.py` et `survey_executor.py`.
Impact : code potentiellement mort ou partiellement actif selon l'environnement (`GLOBAL_CONFIG` vs variable d'env), à nettoyer si confirmé obsolète.
Origine : `Survey/cta_handler.py` lignes 41, 946, 1655, 2563, 2585, 2815, 2839 ; `Survey/survey_executor.py` lignes 2735, 2786.
Statut : CONFIRMÉ DANS LE CODE (présence) ; l'« obsolescence » est une déclaration de l'utilisateur, non un constat du code.
Suivi lié : retrait éventuel = chantier séparé (explicitement hors périmètre de ce recensement).

**RHS-275 — Chemins CTA de l'exécuteur et du dispatcher hors trace BDL-5**
Voir RHS-134 (même constat, même origine BDL-5 v2 « Angle mort conservé et déclaré »).

**RHS-276 — Deux comportements « slider » distincts vus du clic CTA**
Symptôme : deux mécanismes sont décrits côté slider.
(1) Slider à défilement automatique : la sélection d'une option fait défiler la question dans la page, les options restent les mêmes, et le clic CTA de changement de page n'intervient qu'une seule fois, après la réponse à la dernière question — du point de vue de l'oracle CTA, c'est un clic de changement de page ordinaire, à ne pas traiter comme un cas particulier.
(2) Slider à navigation interne (cas rare) : les options restent fixes, la question défile après un clic sur un bouton de navigation interne (généralement une flèche proche de la question), et un CTA de changement de page est présent en permanence en bas de page. Un clic sur la flèche interne ne change ni le document, ni l'URL, ni le nombre de nœuds de question.
Impact : pour le mode 1, aucun risque identifié (comportement déjà couvert par la règle générale « absence de navigation ≠ échec CTA », cf. `Utils/BUG_DETECTION_LAYER_TRACKING.md` §16/§23 et lignes 923-927). Pour le mode 2, si le clic sur la flèche interne passait par le chemin de clic CTA observé par la trace, il ressemblerait à un clic sans progression (`qNodes`, `txt`, `notifSig` inchangés) alors qu'il s'agit d'une transition interne normale — risque de faux `SUSPECTED` ou `INCONCLUSIVE` une fois l'oracle CTA (BDL-5 v2) en service.
Origine : constat formulé par l'utilisateur (2026-11-22), à rapprocher des mentions génériques « sliders et transitions internes » dans `Utils/BUG_DETECTION_LAYER_TRACKING.md` (lignes 230-234, 671-676, 923-927, 1454-1464, 1637-1665) et du détecteur de transition interne existant, scopé à un unique gabarit (RHS-096, Ipsos GridProgressive — `Survey/cta_handler.py` lignes 642-707, signal `progGridSig`) ; aucun mécanisme « flèche de navigation interne » distinct de GridProgressive n'a été localisé dans le code lors de cette vérification.
Vérifications effectuées (2026-11-22, lecture seule) :
- Point de passage commun des clics CTA observés par la trace = `_click_with_intercept` (`Survey/cta_handler.py`), utilisé uniquement dans ce fichier et dans `Survey/cta_trace.py`/`Survey/page_snapshot.py`. `Survey/action_dispatcher.py` ne l'appelle jamais ; il appelle `click_cta_strong_any_context` (`Survey/input_handler.py`) à plusieurs endroits (ex. lignes 6234, 6690, 6946, 6990, 7047, 7121-7147, 7507-7508, 8472) pour des libellés CTA connus explicitement. Aucun appel à l'un ou l'autre de ces points de passage n'a été trouvé qui corresponde à un clic sur une « flèche » de navigation interne — un tel clic, s'il existe sur une plateforme donnée, serait vraisemblablement traité comme une action de réponse ordinaire via `action_dispatcher.py` plutôt que via le chemin CTA. **NON VÉRIFIÉ** : quelle fonction appellerait effectivement ce clic sur une plateforme réelle présentant ce gabarit — aucun cas de ce type n'a été rencontré dans le corpus à ce jour.
- **NON VÉRIFIÉ** : si un CTA constant en bas de page (mode 2) est bien cliqué une seule fois par page après la dernière question, comme pour le mode 1 — comportement dépendant d'une plateforme réelle non observée dans le corpus actuel.
Statut : NON VÉRIFIÉ.
Suivi lié : BDL-5 v2 (oracle CTA).
Décision actée : le mode 2 n'est pas recherché ni traité pour l'instant ; aucune règle d'oracle ni collecte spécifique n'est décidée. Un événement collecté du type 2, s'il apparaît, devra être annoté à part.

**Autres entrées CTA** : voir RHS-009, 020, 087-100 (BDL inventaire/vérifications), 109, 121, 125-134 (BDL-5/BDL-5 v2), 155, 266-276 — toutes listées avec leur citation dans le tableau d'index (section 2).

### EXTRACTION

Regroupe notamment : couverture du contrat BDL-1 (RHS-031 à 043), audit de l'oracle d'extraction BDL-4 (RHS-044 à 065, 268, 271), diagnostic CISnet (RHS-066, 067, 075), inventaire/vérifications BDL (RHS-076 à 088, 098, 099, 104, 107, 108), archive autofix (RHS-189, 190, 235, 236, 240, 241, 250, 251), migration Playwright (RHS-171 à 174), autres (RHS-199, 224). Détail complet (symptôme, impact, origine, statut, suivi, date) : voir chaque ligne correspondante du tableau d'index section 2, qui porte déjà tous ces champs.

### ACTION

Regroupe notamment : audit arrêt/relance (RHS-010, 015), BDL inventaire/vérifications/tracking (RHS-089-106, 112, 116, 118-124, 188, 203, 229, 230, 233, 237, 239), plan autofix et archive (RHS-015 (doublon), 171-174, 205-207, 237, 239), et les deux entrées CTA/ACTION vérifiées ce jour (RHS-266, 267). Détail complet dans le tableau d'index section 2.

### FLUX/NAVIGATION

Regroupe : audit arrêt/relance (RHS-007, 011, 014, 020-023), diagnostic CISnet (RHS-068-074), déploiement bare-metal (RHS-153, 154), archive (RHS-181, 188, 235, 240, 241), RHS-271. Détail complet section 2.

### LOGS

Regroupe : RHS-005, 006 (doublon RHS-160), 012, 082, 180, 213, 214, 272. Détail complet section 2.

### INFRA/DÉPLOIEMENT

Regroupe un grand nombre d'entrées de `DEPLOIEMENT_BAREMETAL_DECISIONS.md`, `ORCHESTRATION_TRACKING.md` et de l'archive autofix : RHS-001-004, 013, 016-019, 029, 076, 077, 081, 083, 110, 114, 115, 142-152, 157-170, 175-178, 185, 198-202, 216-219, 224-228, 234-236, 242-249, 258-265, 273. Détail complet dans le tableau d'index section 2.

### COLLECTE/OBSERVABILITÉ

Regroupe : RHS-008, 044-046, 054, 065, 078-081, 140, 141, 156, 186, 193, 197, 238, 252-257, 263, 273. Détail complet section 2.

### AUTOFIX

Regroupe l'essentiel du plan autofix, du guide d'exploitation, de l'architecture cœur protégé et de l'archive : RHS-024-030, 123, 135-139, 175-179, 182-184, 187, 192, 194-196, 198, 200, 204-212, 215, 219-223, 225-228, 231, 232, 234, 243-249, 257-265. Détail complet section 2.

### DIVERS / AUTRE

RHS-017, 018, 019, 074, 139 (tests/CI), 142-144, 149, 150.

---

## 4. Entrées résolues

Seules les clôtures explicitement actées dans les fichiers sources sont reprises ici (voir aussi le statut RÉSOLU porté directement dans le tableau d'index pour le détail) :

- **RHS-069 / RHS-070** — Diagnostic CISnet : disqualification PQ2 attribuée à l'interaction humaine (pas à `navigator.webdriver` ni à la réputation IP, cette dernière désormais tenue constante). Source : `plan_diagnostic_cisnet_fingerprint.md`, « Résultat du test prioritaire ».
- **RHS-238** — MutationObserver perdu après navigation : champ `observer_present` ajouté pour distinguer observateur perdu d'absence réelle de mutation. Source : archive, mise à jour 2026-09-24.
- **RHS-239** — Flag `qarts_widget` : calcul réel de `has_qarts_overlay` ajouté le jour même de la découverte. Source : archive, mise à jour 2026-09-25 (suite 7).
- **RHS-243** — Lanceur de tâche planifiée écrit hors `.gitignore` dans l'arbre Git du clone : corrigé (suite 40). Source : archive.
- **RHS-245** — `RepetitionDuration` `[TimeSpan]::MaxValue` rejeté : corrigé par `New-TimeSpan -Days 3650` (suite 42, confirmé sur machine). Source : archive.
- **RHS-249** — Fermeture de page live sur timeout (Phase 10) : paramètre additif `close_page_on_timeout=False` ajouté (suite 28). Source : archive.
- **RHS-234** — Dispatcher jamais réexécuté par le replay statique (stage action) : résolu par l'extension 3C.4 (suites 12-18). Source : archive.
- **RHS-204** — Agent concluant à tort qu'un bug est déjà documenté via une entrée « cause non établie » : résolu par consigne (pas de garde mécanique). Source : `SURVEYBOT_AUTOFIX_PLAN.md`, « Points de vigilance ».
- **RHS-221** — Test utilisant l'identifiant/valeur d'un cas réel faisant conclure à tort à une résolution déjà faite : résolu en partie par consigne (3/10/2026). Source : `SURVEYBOT_AUTOFIX_PLAN.md`.
- **RHS-231** — Raison de verdict dépendante de la vitesse machine (`handler_returned_failure` vs `post_handler_timeout`) : résolu pour ce cas précis (5/10/2026), principe général retenu en vigilance. Source : `SURVEYBOT_PROTECTED_CORE_ARCHITECTURE.md`.
- **RHS-011 (section 2, « Rôle Postgres restreint »)** — Signalé ouvert en section 3 de `DEPLOIEMENT_BAREMETAL_DECISIONS.md`, clos en section 8 du même document (DATABASE_URL mise à jour).

---

*Dernière mise à jour : 2026-11-22. Entrées RHS-001 à RHS-265 extraites par lecture intégrale des 16 fichiers de `Utils/` (hors ce registre lui-même). Entrées RHS-266 à RHS-275 vérifiées en lecture seule dans le code source le 2026-11-21, statut précisé individuellement. Entrée RHS-276 ajoutée le 2026-11-22 (constat utilisateur sur la distinction slider défilement-automatique / navigation-interne), vérifications de code ciblées effectuées le même jour.*
