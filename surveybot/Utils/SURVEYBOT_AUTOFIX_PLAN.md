# SurveyBot — mémoire active de l'autofix

Ce fichier est le point d'entrée à lire avant un diagnostic ou un prompt relatif à l'autofix. L'[archive intégrale](SURVEYBOT_AUTOFIX_PLAN_ARCHIVE.md) conserve **octet pour octet** l'ancien plan : journal des mises à jour jusqu'à la suite 47, descriptions détaillées des phases 1A à 20, règles, décisions, cas réels et architecture cible. Pour retrouver un détail, rechercher dans l'archive `Mise à jour ... (suite N)` ou le titre `Phase ...`. Toute nouvelle chronique détaillée va dans cette archive unique ; ce fichier conserve l'état utile au travail en cours.

Les décisions de [BOT_EVOLUTION_MEMORY.md](../Survey/BOT_EVOLUTION_MEMORY.md), notamment ses « Patterns exclus », restent des frontières strictes. La séparation du cœur protégé et des points d'extension est décrite dans [SURVEYBOT_PROTECTED_CORE_ARCHITECTURE.md](SURVEYBOT_PROTECTED_CORE_ARCHITECTURE.md). Ces documents ne sont pas remplacés par cette mémoire.

## État actuel

- Le pipeline dispose de la détection passive, des failure cases, du rejeu, du diagnostic, de la sélection de contexte, de la génération de prompt, d'un worktree isolé, des validations et du score de confiance. L'orchestrateur enchaîne l'amont, Claude Code headless, les contrôles, la décision Telegram puis le commit et le merge local après approbation. Le push partagé et la release restent des gestes manuels. Détails : archive, suites 35 à 37 et phases 1A à 20.
- Le rejeu Chromium fidèle est le mode par défaut pour `stage="extraction"` ; le rejeu statique reste disponible avec `--static`. Pour `stage="action"`, le défaut reste statique et le mode fidèle est explicite. Détails : archive, suites 43 et 44.
- Le dernier passage supervisé consigné (MetrixLab, suite 46) s'est arrêté avec `confidence=REJECT` et sans notification. La suite 47 établit deux causes distinctes **encore non corrigées dans ce suivi** : Claude Code n'a écrit aucun patch après une commande Bash refusée par `--allowed-tools`, et le registre `extractor_integrity.json` présente 18/57 mésappariements déjà présents sur la branche de base. Vérifier chaque fonction concernée avant tout `record-batch` ; la baseline ne se réenregistre jamais automatiquement. L'extraction MetrixLab elle-même reste à corriger. Détails : archive, suites 45 à 47.

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

La collecte complète des cas 1B ne bloque pas les autres phases. Chaque nouveau cas réel utile doit être capturé, classé, puis traité par un patch 1B.x ciblé. Le doute sur `dispatcher_success=true` avec sélection erronée n'est pas confirmé : ne pas patcher sans snapshot réel. Détails : archive, Phase 1B et suite 45.

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

### Git et décisions humaines

Le patch se prépare dans une branche et un worktree dédiés. Le merge local suit une approbation Telegram dans le pipeline orchestré ; aucun push ni release n'est automatique. Détails : archive, suites 35 à 37 et Phases 7, 13, 15, 16.

## Phases ouvertes ou partielles

| Phase | État / prochaine condition utile |
| --- | --- |
| 1B | Ouverte en tâche de fond. Continuer la collecte de cas réels et corriger l'extraction MetrixLab séparément de sa détection. |
| 3B.3 / 3B.4 | Frames et Shadow DOM différés jusqu'à une mesure sur des cases réellement `NON_REJOUABLE` ; aucun volume suffisant n'est encore consigné. |
| 3D | Classification partielle. `EXTERNAL_NON_REPLAYABLE` n'est jamais produit ; le classificateur ne lit pas encore `real_extraction_replay`, donc des cas extraction restent `UNDETERMINED`. |
| 7 | Le module prépare le worktree et son manifeste. L'invocation de l'agent appartient désormais à l'orchestrateur, ajouté plus tard ; le statut historique du module reste « partiellement terminée ». |
| 11-B | Rejeu des `regression_cases/` différé faute de bibliothèque curée. Le contrôle 11-A et un score `HIGH` ne prouvent pas encore l'absence de régression comportementale. |
| 17 | Mesures disponibles ; décision par catégorie différée jusqu'à plusieurs semaines de données réelles. |
| 18 | Orchestrateur headless en place ; boucle complète en attach live non construite. |
| 19 | Déduplication stricte de cases disponible ; taux de réussite par extracteur non mesurable sans comptage des extractions réussies. |

Voir les sections de même nom dans l'[archive intégrale](SURVEYBOT_AUTOFIX_PLAN_ARCHIVE.md). Les phases closes consignées sont 1A, 2, 3A, 3C sur son périmètre retenu, 4 à 6, 8 à 10, 12 à 16 et 20 ; 3B et 3D restent partielles. Les sous-parties déjà achevées de ces phases partielles sont détaillées dans l'archive.

## Points de vigilance encore consignés

- Phase 4 : mettre à jour `_EXPECTED_BEHAVIOR` avec tout futur changement des validators ; Phase 5 : revisiter la source 2 si une vraie table de dispatch apparaît. Archive, Phases 4 et 5.
- Phase 3B : le contenu des scripts, CSS et réponses XHR/fetch externes n'est pas entièrement sanitisé ; les limites précises sont dans l'archive, suites du 25 septembre et Phase 3B.
- Phases 9 et 11-B : le rejeu des cas historiques voisins et la bibliothèque de DOM représentatifs restent différés.
- Orchestrateur : chaque patch est validé contre l'état de départ, pas contre tous les patchs mergés ensuite ; l'extension incrémentale d'un groupe de doublons est différée. Archive, suites 33, 36 et 37.
- Clone dédié : l'import des modules `Survey/` depuis une copie propre a été vérifié ; l'exécution d'un vrai rejeu dans cette copie, les fichiers hors `Survey/` et la complétude des dépendances avec le venv du clone restaient à vérifier dans la suite 42. La suite 46 a ensuite fourni un premier passage supervisé complet, avec les blocages décrits plus haut.

## Index de l'archive

L'[archive](SURVEYBOT_AUTOFIX_PLAN_ARCHIVE.md) contient l'ancien fichier entier, avec ses titres d'origine. Rechercher `## Contexte de travail actuel` pour l'ancien état, `# Phase 1B` ou `# Phase 3` pour les chantiers partiels, `# Phase 1A`, `# Phase 2`, `# Phase 4` à `# Phase 20` pour le détail historique, `# Architecture finale cible` pour le schéma, et `## Ordre concret de développement` pour la chronologie des statuts. Le journal de tête contient les mises à jour datées et les suites 1 à 47.
