# SurveyBot — fonctions critiques protégées et cycle de vie des correctifs

> Décisions d'architecture, 29 septembre 2026. Ce document complète `SURVEYBOT_AUTOFIX_PLAN.md` (SAP) sans en modifier le suivi des phases. La cible s'intègre progressivement au pipeline ; seule la tâche 1 ci-dessous est implémentée.

## 1. Portée et principe

Les **fonctions critiques protégées** comprennent les extracteurs, les fonctions d'action et les helpers structurants dont une dérive peut affecter plusieurs surveys. La protection s'applique aux stages extraction et action ; elle n'implique pas qu'une fonction soit intouchable.

Pour un incident confirmé, chercher d'abord une correction **externe, minimale, indépendante et précisément gardée** par les faits DOM qui ont produit l'échec. Le core historique reste une référence stable pendant la collecte. Corriger les bugs au fil de l'eau : aucun quota de métriques ne doit suspendre le traitement d'un cas reproductible. Un correctif externe est une mémoire temporaire d'une faiblesse du core, pas un fallback permanent ni un nouvel extracteur générique.

Le bot de production détecte et enregistre les incidents et les compteurs utiles. Le diagnostic causal, les patches, les replays, les validations et la consolidation du core appartiennent au worker local/dev séparé. Le comportement reste DOM-first, sans fallback Vision ; une détection précise prime sur la couverture maximale. Une correction ne doit pas multiplier les extracteurs pour contourner une fonction existante.

## 2. Contrat d'un correctif externe

Chaque correctif est rattaché à une fonction core et à un ou plusieurs failure cases identifiés. Il décrit le fait DOM déclencheur, le comportement erroné du core, la modification limitée attendue, les cas exclus et les preuves de validation. Son périmètre doit rester assez étroit pour être retiré ou réécrit lors d'une évolution du core.

Une couche d'orchestration unique évalue un ensemble **plat** de correctifs indépendants autour de la fonction concernée. Un correctif ne peut ni appeler un autre correctif, ni supposer son résultat, ni imposer un ordre caché d'application. Pas de chaîne `core → fix A → fix B`. Le nombre de correctifs évalués et le temps d'évaluation sont bornés ; en cas de dépassement, l'exécution abandonne la correction de façon contrôlée et trace la cause.

Pour l'extraction, la correction intervient avant la validation du résultat. Pour l'action, la condition doit être vérifiée **avant** tout clic ou effet irréversible ; un résultat déjà exécuté ne peut pas être « corrigé » après coup. `CTA_INTERCEPT_ONLY` conserve son sens : interception sans navigation lorsqu'il est activé, clic réel lorsqu'il est désactivé. Le placement et le découpage des modules seront choisis après lecture du dépôt ; les gros fichiers historiques ne doivent pas devenir des catalogues de fixes. Aucun refactor des extracteurs ou des stratégies d'action n'est requis par ce document.

### Position des points d'extension pour l'extraction

L'ordre d'évaluation dépend de la cause confirmée sur le DOM concerné :

- **DOM non couvert :** évaluer le correctif après les stratégies existantes pertinentes, lorsque celles-ci n'ont pas produit de résultat applicable. L'ajout reste strictement gardé par le fait DOM nouveau.
- **Faux positif d'un extracteur existant :** évaluer le correctif strictement gardé juste avant la stratégie qui correspond à tort. Un ajout après cette stratégie serait inopérant si la cascade s'arrête dès son résultat.

Ces positions logiques `before` et `after` décrivent l'ordre d'appel, sans imposer deux registres ni une réorganisation des extracteurs. Le garde-fou doit rester précis et le correctif indépendant ; aucun changement du corps de la stratégie existante n'est impliqué par ce seul placement.

## 3. Mesure et attribution

Les métriques doivent permettre de comparer l'usage d'une fonction à ses incidents, puis d'expliquer **où** elle échoue. Le minimum utile est :

| Niveau | Mesures et liens à conserver |
| --- | --- |
| Fonction core | Identifiant stable, version ou baseline, nombre d'appels, période d'observation et stage. |
| Incident | `case_id` unique, fonction(s) traversée(s), stade d'attribution, preuve et version du core observé. Les doublons d'un même cas ne gonflent pas le nombre d'incidents. |
| Correctif | Identifiant, fonction visée, cases associés, évaluations, activations, échecs ou incidents après activation, validations et période d'observation. |

Un appel n'est pas un bug, une fonction présente dans la pile d'appels n'en est pas nécessairement la cause, et l'activation d'un fix n'est pas à elle seule une preuve de succès. Les taux éventuels utilisent une période et une version comparables ; ils ne doivent pas mélanger les appels de versions différentes ni prétendre représenter des surveys entiers si seul un sous-ensemble est observé. Les métriques ne contiennent ni secret ni donnée brute de répondant. Les logs de diagnostic utilisent `log_debug(tag, msg)` selon `LOG_LEVEL`, avec de rares `log_info(tag, msg)`, jamais `print()`.

L'attribution d'une fonction à un incident distingue explicitement :

- `involved` : fonction observée sur le chemin du cas ; aucune causalité déduite ;
- `suspected` : hypothèse motivée par un indice, encore non confirmée ;
- `root_cause_confirmed` : cause reliée au symptôme par le replay et le diagnostic ;
- `patched` : fonction ou zone effectivement ciblée par le correctif, sans inférer que le patch a réussi.

Ces états ne sont pas des synonymes ni nécessairement une chaîne automatique. La preuve et son incertitude restent visibles ; l'absence de preuve laisse l'attribution indéterminée. Les compteurs d'incidents **attribués** à une fonction ne retiennent pas les simples `involved` ou `suspected`.

Pour raconter l'histoire des défaillances, associer aux cas et aux correctifs un `root_area` (zone conceptuelle, par exemple visibilité ou résolution de cible) et un `failure_mechanism` (mécanisme plus précis). Ces valeurs restent courtes, révisables et éventuellement inconnues ; elles ne doivent pas être déduites d'un nom de provider ou d'un seul symptôme. Deux fixes distincts peuvent partager un mécanisme ou révéler une même faiblesse plus générale du core. Le regroupement sert à prioriser une analyse causale, jamais à déclencher un changement automatique.

## 4. Compatibilité entre correctifs

Avant l'adoption d'un nouveau fix, examiner le chevauchement de ses conditions DOM et de ses effets avec les fixes de la même fonction. Deux fixes peuvent être localement valides mais contradictoires sur un DOM commun ou impossibles à absorber ensemble dans le core.

Si plusieurs fixes deviennent applicables à la même situation, l'orchestrateur ne choisit pas arbitrairement le premier et ne les compose pas par défaut. Il trace l'ambiguïté, abandonne la correction automatique concernée sans action risquée et crée un cas exploitable pour le diagnostic local. Des domaines d'application démontrés disjoints peuvent coexister. Une incompatibilité non résolue bloque leur consolidation commune, pas le traitement des autres cas indépendants.

## 5. Cycle de vie

| État | Signification et sortie possible |
| --- | --- |
| `NOUVEAU` | Proposition reliée à un cas et à un fait DOM précis ; pas d'adoption sans contrôles. |
| `OBSERVÉ` | Cas et conditions documentés ; mesure d'usage disponible ou explicitement absente. |
| `VALIDÉ` | Échec avant patch, succès après patch sur le cas rejouable, contrôles statiques et cas voisins pertinents passés ; limites du replay explicites. |
| `STABLE` | Correctif adopté puis observé sans contradiction connue sur une période et un volume documentés. Aucune durée universelle présumée. |
| `EVOLUTION_CANDIDATE` | Analyse de consolidation justifiée par l'accumulation de cas, de fixes ou d'activations sur une même zone ; le core reste inchangé à ce stade. |

Une défaillance de validation rejette ou révise la proposition ; un incident nouveau peut faire revenir un fix stable en analyse. Les quotas d'appels, de cases ou de fixes sont des **seuils de revue** à calibrer sur les données réelles, jamais des seuils de fusion automatique. Chaque passage d'état conserve les cases et preuves qui le motivent.

Si les fixes deviennent nombreux pour une fonction, si leurs gardes se chevauchent ou si une correction externe doit dupliquer une large partie du core, marquer `CORE_CHANGE_CANDIDATE` et examiner la cause commune. Ne pas ajouter une couche supplémentaire simplement pour préserver artificiellement le hash.

## 6. Évolution et consolidation du core

Une évolution du core vise le mécanisme commun mis en évidence par les incidents ; elle ne consiste pas à copier les fixes externes dans la fonction. Le worker local/dev examine la fonction, tous les correctifs associés, leurs domaines d'application et d'exclusion, les cases attribués, les métriques comparables et les contradictions éventuelles. Une consolidation n'est admissible que si la nouvelle règle est plus simple ou plus robuste que l'ensemble qu'elle remplace, sans élargir aveuglément la détection.

Avant adoption : rejouer l'ensemble des failure cases associés, y compris les cas des fixes candidats à l'absorption ; vérifier les cas voisins et les cas de régression représentatifs disponibles ; exécuter les contrôles statiques et, si nécessaire, une validation live **attach uniquement** sur le cas déclencheur. Si un cas n'est pas rejouable, consigner ce manque et ne pas le convertir en preuve positive. Le budget du cycle d'autofix reste d'un patch, d'une validation et d'au plus une seconde correction justifiée ; tout échec au-delà produit un abandon documenté.

Après consolidation validée et adoptée, retirer les correctifs devenus inutiles, conserver les cases et leur historique causal, puis mesurer la nouvelle version séparément. Un fix incompatible ou encore nécessaire reste hors de cette absorption ; si la coexistence sûre n'est pas démontrée, la consolidation attend une analyse supplémentaire.

## 7. Rôle révisé d'`extractor_integrity`

`extractor_integrity.json` reste une **baseline détectant la dérive du core protégé**, et non une interdiction définitive de corriger un extracteur. Les fixes externes ne changent pas le hash de la fonction core lorsqu'ils ne modifient pas son corps ; ils sont contrôlés par leurs propres preuves et validations. La protection doit couvrir conceptuellement les fonctions critiques d'extraction **et d'action**, même si le registre actuel porte un nom historique centré sur les extracteurs.

Une future vérification doit distinguer l'état du registre, celui de la base Git du worktree et celui du patch :

| État | Décision attendue |
| --- | --- |
| `UNCHANGED` | Core identique à une baseline saine. |
| `EXPECTED_CHANGE` | Fonction protégée explicitement reliée à une cause confirmée ; validations renforcées et revue du résultat concret. |
| `UNEXPECTED_CHANGE` | Autre fonction protégée modifiée ou supprimée ; rejet. |
| `BASELINE_MISMATCH` | Désaccord déjà présent avant le patch ; diagnostic et remise en état volontaire du registre avant de lui faire certifier un nouveau changement. |

L'agent qui écrit le patch ne met jamais à jour lui-même la baseline pour faire accepter son travail. Une nouvelle entrée ou un nouveau hash n'est enregistré **qu'après** validation de la consolidation et décision d'adoption, par l'étape responsable du workflow. L'approbation porte sur un patch et ses preuves, pas sur une question interactive abstraite au milieu d'une session headless. Un changement direct du core, lorsqu'aucun fix externe sûr et petit n'est possible, suit cette même voie explicite de changement attendu et de validation renforcée.

**État du SAP consulté :** la Phase 11-A rejette actuellement tout écart de hash ; la Phase 11-B de replay de régression est différée. Le plan joint rapporte aussi 18 écarts sur 57 fonctions déjà présents sur la branche de base au 29 septembre 2026. Les états ci-dessus et le changement de rôle du registre sont donc des décisions **à implémenter et vérifier dans le dépôt réel**, sans présumer qu'ils sont déjà actifs ni rebaseliner automatiquement ces écarts historiques.

## 8. Intégration au pipeline d'autofix

1. **Production / phases d'observabilité et de failure case :** enregistrer le fait DOM, le stage, les artefacts reproductibles et les compteurs sobres, sans retry ni auto-correction passive.
2. **Replay, diagnostic et sélection de contexte :** confirmer l'échec avant patch si le cas est rejouable ; attribuer les fonctions avec leur niveau de preuve ; sélectionner le core, les correctifs liés et les cas voisins pertinents. Les validators vérifient les incohérences fortes entre DOM, registre, blocs et actions, sans devenir un second analyseur DOM.
3. **Patch local isolé :** essayer un seul fix externe précisément gardé. Si celui-ci est artificiel, dangereux, incompatible ou trop large, documenter `CORE_CHANGE_CANDIDATE` ; un changement direct du core exige une cause confirmée et les contrôles renforcés. La session headless ne pose pas de question à laquelle elle ne peut recevoir de réponse.
4. **Validation :** contrôles statiques, échec avant/succès après, replays des cas associés et voisins, détection de conflits entre fixes, intégrité du core, puis live attach seulement si nécessaire. Les absences de preuve, notamment celles dues aux limites de replay, restent visibles dans le score de confiance.
5. **Supervision et adoption :** proposer à l'humain le patch concret, ses validations, ses limites et le niveau `HIGH`, `MEDIUM` ou `REJECT`. La décision d'adoption précède toute actualisation de la baseline ; le commit et le merge relèvent du worker local/dev et de l'orchestrateur, jamais du bot de production.

Ce document fixe les responsabilités et les critères de décision. Il ne prescrit ni schéma de stockage définitif, ni nom de module, ni refactor préalable : leur forme dépendra de la lecture du dépôt et des cases effectivement disponibles.

## 9. Tâches à faire — ordre d'implémentation

1. **Implémentée — infrastructure commune d'identité et de métriques des fonctions critiques.** Identifiants stables, rattachement à la baseline et compteurs minimaux utiles aux incidents, sans métriques sophistiquées.
2. Créer le registre des correctifs externes pour l'extraction, avec des conditions DOM précises et des correctifs indépendants.
3. Ajouter un premier point d'extension extraction qui respecte les positions `before` et `after` décrites en section 2, sans correctif métier si possible.
4. Ajouter un point d'extension action unique, avec une modification volontaire et contrôlée du dispatcher, avant tout effet irréversible.
5. Adapter le prompt d'autofix pour produire des correctifs selon ces points d'extension et leurs garde-fous.
6. Adapter la Phase 11-A aux états `UNCHANGED`, `EXPECTED_CHANGE`, `UNEXPECTED_CHANGE` et `BASELINE_MISMATCH`, avec les décisions de la section 7.
7. Ajouter le cycle de vie des correctifs et la consolidation `EVOLUTION_CANDIDATE` décrits aux sections 5 et 6.

Chaque étape est validée sur le périmètre qu'elle introduit avant de passer à la suivante. Les correctifs métier répondent à des incidents confirmés ; ces points d'extension peuvent être préparés sans en inventer.

**Tâche 1 — état livré.** `Survey/core_function_metrics.py` expose `record_core_call(function_id, stage=...)` pour l'extraction et l'action. `function_id` reprend exactement la clé `fichier.py::fonction` relative à `Survey/` de `extractor_integrity.json` ; seules les clés de ce registre sont acceptées. Chaque série conserve le hash de baseline et le hash du code source observé (méthode d'`extractor_integrity.py`), le stage s'il est connu, le nombre d'appels et les premières/dernières dates d'appel. Le hash du code sépare les versions même si la baseline est désynchronisée. Les compteurs restent en mémoire et sont écrits au plus une fois par minute, puis à la sortie normale, sous `core_function_metrics/<producer_id>.json` (chemin configurable par `SURVEYBOT_CORE_METRICS_DIR`) ; chaque processus possède son propre instantané atomique. Le `.gitignore` à la racine Git exclut ce dossier généré et `tests/test_core_function_metrics.py` vérifie les propriétés essentielles.

La mesure est locale au processus et commence seulement quand les futurs points d'instrumentation appelleront cette API ; aucune fonction protégée existante n'est instrumentée à ce stade. Le hash du code est calculé une fois par fonction depuis les sources sur disque et suppose que le code chargé ne change pas pendant la vie du processus. Un arrêt brutal peut perdre les appels depuis le dernier instantané. Les fichiers restent locaux sans expiration automatique ni agrégation fleet ; leur rétention devra être décidée avec l'exploitation. Un échec de métriques laisse l'exécution du bot continuer.
