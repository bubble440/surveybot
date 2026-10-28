# SurveyBot — Contrat BDL-1 : représentation attendue

Version : 0.5
Statut : CLÔTURÉ — contrat documentaire de représentation attendue défini ; les validations complémentaires relèvent de travaux ultérieurs
Branche étudiée : `feature/phase-1a-observability`
Portée : blocs `dict` retournés par `Survey.dom_analyzer.analyze_dom()`.

## 1. Objectif et limites

Le contrat décrit la représentation fonctionnelle attendue et les invariants structurels qui peuvent être testés. Il ne transforme pas la sortie actuelle du bot en vérité terrain : les snapshots historiques servent à la régression, pas à prouver à eux seuls qu’une extraction est correcte.

Trois niveaux doivent rester distincts :

1. **Type attesté statiquement** : une valeur `itype` est explicitement produite par le code inspecté.
2. **Type observé à l’exécution** : la valeur est apparue dans une sortie réelle `analyze_dom()`, capturée dans un artefact.
3. **Conformité sémantique validée** : un DOM de référence et une vérité terrain indépendante confirment que le bloc représente correctement la question.

Pour clôturer BDL-1, le présent document établit le premier niveau : le contrat documentaire de représentation attendue. L’inventaire runtime et la validation sémantique indépendante sont des travaux complémentaires ; ils ne conditionnent pas cette clôture.

## 2. Types attestés statiquement

Valeurs attestées dans les blocs construits par les modules principaux et spécialisés inspectés :

- `radio`
- `checkbox`
- `dropdown`
- `text`
- `textarea`
- `matrix`
- `number`
- `select`
- `select_rps`
- `button`

Cette liste correspond aux types attestés statiquement dans le périmètre inspecté ; elle pourra être révisée si de nouveaux types sont découverts. Tout `itype` inattendu doit être signalé comme « non documenté », pas automatiquement déclaré bug d’extraction.

`unknown` est retourné par `Survey.dom_utils._detect_itype()` mais le chemin générique principal l’écarte normalement. `rtype` est attesté dans un payload du registre CMix, sans confirmation qu’il soit exposé comme bloc retourné. `Survey.question_block_analyzer._infer_itype()` appartient à un analyseur séparé et ne doit pas être mélangé à `analyze_dom()` sans preuve d’intégration.

## 3. Invariants communs aux blocs

Les invariants ci-dessous sont structurels. Ils ne prouvent pas à eux seuls que le contenu est sémantiquement juste.

| Champ / propriété | Contrat vérifiable | Réserve |
|---|---|---|
| Bloc | Chaque entrée retournée est un dictionnaire | Le retour de `analyze_dom()` est documenté comme `List[Dict[str, Any]]` |
| `itype` | Chaîne non vide appartenant à la liste documentée, ou signalée comme nouveau type | Un nouveau type observé déclenche une revue du contrat, pas une correction automatique |
| `target_id` | Présent et non vide | Le validateur existant le signale déjà lorsqu’il manque |
| Registre | En exécution, chaque `target_id` doit résoudre vers une cible enregistrée | Vérification possible uniquement dans le même processus, après extraction et avant nettoyage du registre |
| `options` | Si présent, doit être une liste | Les champs ouverts et CTA utilisent normalement `[]` |
| Options | Pas de doublons après normalisation pour les types de choix lorsque les options sont censées représenter des choix distincts | Des widgets particuliers peuvent réutiliser des libellés ; analyser avant de transformer ce signal en échec dur |
| `min_select`, `max_select` | Si les deux limites sont positives, `min_select <= max_select` | Zéro ou absence peut signifier « non renseigné » ; ne pas le convertir automatiquement en contrainte fonctionnelle |
| JSON | Blocs et rapport doivent être sérialisables | Le registre lui-même n’est pas un artefact JSON nécessairement sérialisable |

## 4. Contrat détaillé par `itype`

### `radio`

- **Rôle** : choix unique parmi des options.
- **Attendu** : contexte de question intelligible, options répondables avec libellés compréhensibles, `target_id` relié au groupe actif.
- **Invariants structurels** : `options` devrait être une liste ; pour un groupe de choix standard, au moins deux options répondables sont attendues ; il s’agit d’une attente contextuelle, pas d’une règle universelle applicable à tous les DOMs. Si `max_select` est renseigné, il ne devrait pas autoriser plusieurs choix.
- **À vérifier dans le registre** : cible de groupe et locators d’options cohérents avec le même groupe DOM.
- **Ne pas conclure automatiquement** : un seul nœud input par option n’est pas garanti ; options masquées, clonées ou désactivées peuvent exister dans le DOM.

### `checkbox`

- **Rôle** : sélection multiple, parfois limitée par une instruction.
- **Attendu** : libellés des options, contexte de question et limites de sélection disponibles.
- **Invariants structurels** : options en liste ; `min_select <= max_select` si les deux sont positifs ; `max_select` ne dépasse pas le nombre d’options lorsque les deux valeurs sont connues et comparables.
- **À vérifier dans le registre** : la cible doit permettre de retrouver le groupe et ses options.
- **Ne pas conclure automatiquement** : certains groupes sont exclusifs malgré une balise checkbox ; le DOM spécifique peut conduire à un `itype="radio"`.

### `dropdown`

- **Rôle** : contrôle de choix généralement unique.
- **Attendu** : question/libellé, options réellement sélectionnables lorsqu’elles sont accessibles, cible exploitable.
- **Invariants structurels** : `options` en liste si présent ; ne pas confondre placeholder et réponse.
- **Réserve importante** : une liste vide n’est pas une preuve universelle de bug ; certains widgets ne rendent les options qu’après ouverture ou dans un état runtime différent.

### `text`

- **Rôle** : champ de saisie textuelle courte, y compris certains champs reclassés localement par un extracteur spécifique.
- **Attendu** : libellé ou contexte de question et cible d’action.
- **Invariants structurels** : `options` normalement vide ; le contrat n’exige pas de limites numériques de sélection.
- **À vérifier sémantiquement** : ne pas présenter un filtre, champ de recherche ou contrôle de navigation comme réponse d’enquête ; préserver le lien avec « Autre » lorsque cette association est prouvée.

### `textarea`

- **Rôle** : saisie textuelle multiligne.
- **Attendu** : contexte de question, cible d’action ; champ distinct des options de réponse.
- **Invariants structurels** : `options` normalement vide ; aucune contrainte de choix à imposer par défaut.
- **À vérifier sémantiquement** : la présence d’un groupe de choix à proximité ne justifie pas d’écarter le champ.

### `matrix`

- **Rôle** : structure à lignes et colonnes, ou représentation spécialisée d’une grille.
- **Attendu** : conserver la relation entre consigne, lignes et échelle/colonnes ; une réponse doit rester rattachable à la bonne ligne.
- **Invariants structurels** : ne pas imposer systématiquement deux options dans `options` : certains extracteurs transportent lignes/colonnes dans `context` ou dans une représentation spécialisée.
- **Test représentatif** : si `context.matrix_rows` et `context.matrix_columns` existent, vérifier qu’ils sont des listes non vides et que les lignes/colonnes restent distinctes. L’absence de ces clés n’est pas un échec universel sans connaître le pattern d’extracteur.
- **Ne pas aplatir** la grille en options non contextualisées.

### `number`

- **Rôle** : champ numérique.
- **Attendu** : contexte de question et cible d’action ; les bornes, l’unité et la précision sont conservées lorsqu’elles existent dans le DOM ou les métadonnées ; leur absence ne doit pas être compensée par des valeurs inventées.
- **Invariants structurels** : `options` normalement vide ; ne pas inventer des bornes lorsque le DOM ne les expose pas.
- **Test représentatif** : vérifier que les métadonnées disponibles ne sont pas perdues, sans exiger `min`/`max` lorsqu’ils ne sont pas présents.

### `select`

- **Rôle** : type de sélection utilisé par des extracteurs spécialisés ; ne pas l’assimiler silencieusement à `dropdown` dans les traces.
- **Attendu** : question/libellé, cible, options lorsque le widget les expose.
- **Réserve importante** : le code produit au moins un chemin `select` avec `options=[]`. L’invariant « options non vides » ne peut donc pas être universel sans état DOM et pattern de widget.
- **Test représentatif** : inventorier les occurrences avec options vides, puis les regrouper par extracteur/plateforme avant de définir une règle plus stricte.

### `select_rps`

- **Rôle** : sélection spécialisée produite par un extracteur RPS.
- **Attendu** : question/libellé, cible et options exposées par le widget.
- **Réserve** : `select_rps` doit rester identifiable comme type spécialisé dans la représentation d’extraction et le registre. Une normalisation ultérieure vers `dropdown` pour préparer le prompt du modèle relève d’une autre étape du pipeline et ne signifie pas que le type d’extraction est équivalent.
- **Test représentatif ultérieur** : comparer la structure des blocs observés à celle des cas DOM de référence RPS, notamment les options et le locator enregistré.

### `button`

- **Rôle attesté** : contrôle CTA produit par `sliderpoints_extractor`; ce n’est pas automatiquement une question.
- **Attendu** : label/action et cible exploitable ; le résultat du clic relève des contrats CTA/action.
- **Invariants structurels** : ne pas exiger d’options de réponse ; ne pas compter ce bloc comme une question répondable dans l’oracle d’extraction.
- **Test représentatif** : vérifier l’identification du contrôle et son `target_id`, sans effectuer de clic réel.

## 5. « Autre / Other / Précisez »

Décision produit retenue : **option A — réponse composée avec association explicite**.

- L’option « Autre » reste une option du groupe radio/checkbox.
- Le champ de précision reste relié à cette option lorsque l’association est confirmée.
- Le champ de précision ne doit pas être dupliqué comme question indépendante parasite.
- La proximité seule ne suffit pas à établir le lien.

## 6. État de l’existant et lacunes observées

`Survey/question_block_validator.py` réalise déjà une validation passive et sans effet de bord. Il vérifie notamment `target_id`, la présence de la cible dans le registre, les options vides pour `radio`/`checkbox`/`dropdown`, les doublons et certaines limites de sélection.

Limites importantes de la validation actuelle :

- les contrôles de forme ne couvrent pas uniformément les dix `itype` ;
- `matrix` est explicitement exempté de la règle générale d’options vides, car sa structure peut être dans le contexte ;
- `select`, `select_rps`, `number`, `text`, `textarea` et `button` n’ont pas tous de règles spécifiques complètes dans ce validateur ;
- le validateur ne prouve pas que chaque option exposée correspond à une option DOM réellement répondable ;
- le validateur n’est pas un inventaire de l’ensemble des types émis par le corpus.

Ces limites motivent des vérifications complémentaires, mais elles ne remettent pas en cause la clôture documentaire de BDL-1. Si elles sont poursuivies, l’étape suivante peut être additive : utiliser un outil d’inventaire sur des artefacts `question_blocks.json` existants, puis ajouter des tests de contrat ciblés. Ne pas modifier les corps des extracteurs existants pour réaliser ces vérifications.

## 7. Inventaire automatisé : protocole complémentaire (hors critères de clôture BDL-1)

Le fichier `tools/bdl1_inventory.py` fourni avec ce document :

1. accepte un ou plusieurs fichiers JSON ou dossiers de snapshots ;
2. pour un dossier, recherche les fichiers nommés `question_blocks.json` ;
3. accepte une liste de blocs ou un objet contenant `question_blocks` / `blocks` ;
4. compte les `itype` réellement observés ;
5. signale les types non documentés, les blocs mal formés, les `target_id` absents et les incohérences évidentes de limites ;
6. écrit un rapport JSON et un résumé console ;
7. ne modifie ni les snapshots ni le code SurveyBot.

L’inventaire statique du code et l’inventaire des snapshots doivent être rapportés séparément. Si un type statiquement attesté n’apparaît pas dans les snapshots, cela signifie seulement qu’il n’a pas été observé dans ce corpus.

Exemple d’exécution depuis la racine du dépôt :

```bash
python tools/bdl1_inventory.py path/to/corpus --output bdl1_inventory.json
```

Pour faire échouer le processus CI en cas d’anomalie structurelle ou de type non documenté :

```bash
python tools/bdl1_inventory.py path/to/corpus --output bdl1_inventory.json --strict
```

Le mode strict doit d’abord être lancé sur le corpus historique en mode non strict : des anomalies préexistantes peuvent être révélées et doivent être triées avant d’activer le gate CI.

## 8. Tests représentatifs éventuels (travaux ultérieurs, hors critères de clôture BDL-1)

Ordre minimal recommandé :

1. **Test de forme commun** : chaque bloc est un dictionnaire, `itype` est connu, `target_id` non vide, `options` est une liste si présent.
2. **Test des limites** : `min_select <= max_select` quand les deux sont positifs ; `checkbox.max_select` ne dépasse pas le nombre d’options lorsque les valeurs sont comparables.
3. **Test registre** : sur une extraction réelle, chaque `target_id` résout vers une cible enregistrée avant tout `clear_registry()`.
4. **Test par famille** : un cas de référence radio, checkbox, dropdown, text, textarea, matrix, number, select, select_rps et CTA button, idéalement issu de DOM/snapshots déjà validés.
5. **Test inconnu** : un nouveau `itype` doit faire échouer le contrôle strict et apparaître clairement dans le rapport, sans déclencher de correction automatique.
6. **Test « Autre »** : l’option et le champ lié restent représentés ensemble sans duplication parasite.

Ne pas utiliser la seule présence d’un type dans un snapshot comme preuve de conformité sémantique. Pour les tests de référence, enregistrer séparément le DOM/cas, le bloc attendu et la justification de la vérité terrain.

## 9. Clôture BDL-1 et travaux reportés

**Statut : BDL-1 clôturé.** L’objectif initial est atteint : le contrat documentaire de représentation attendue est défini pour les dix types attestés statiquement, les invariants communs et les limites d’interprétation sont documentés, et la décision produit relative à « Autre » est explicite.

### Éléments établis pour la clôture

- [x] Décision produit « Autre » : option A, avec association explicite du champ de précision lorsque le lien est confirmé.
- [x] Cartographie statique des dix types attestés dans le périmètre inspecté.
- [x] Contrat attendu par `itype`, avec réserves contextuelles lorsque les structures DOM varient.
- [x] Invariants communs et limites du validateur existant documentés.
- [x] Distinction posée entre contrat documentaire, observation runtime et validation sémantique indépendante.
- [x] Frontière clarifiée entre le type spécialisé `select_rps` à l’extraction et la normalisation éventuelle lors de la préparation du prompt.

### Résultat d’inventaire historique — informatif, non bloquant

Un inventaire non strict a été exécuté sur `C:\projects\Surveys\snapshots` :

- **39** fichiers `question_blocks.json` parcourus ;
- **55** blocs analysés ;
- **4** types observés : `radio=36`, `checkbox=10`, `text=7`, `dropdown=2` ;
- **4** signalements émis, dont le détail reste à examiner.

Ce résultat décrit uniquement les types présents dans les snapshots analysés. Il ne prouve ni que les autres types statiquement attestés sont absents du code, ni que les blocs observés sont sémantiquement conformes. Les quatre signalements restent non triés ; cela ne bloque pas BDL-1.

### Travaux possibles après BDL-1

Ces tâches sont reportées et ne constituent pas des critères de clôture de BDL-1 :

- examiner le rapport détaillé et classer les quatre signalements historiques ;
- compléter le corpus de référence pour les types non observés dans les snapshots (`matrix`, `number`, `select`, `select_rps`, `textarea`, `button`), si nécessaire ;
- ajouter des tests représentatifs par type et pour l’association « Autre » ;
- vérifier la cohérence registre ↔ `target_id` sur des sorties réelles ;
- décider ultérieurement si le contrat doit être référencé par l’oracle BDL-4 ;
- envisager le mode strict uniquement après tri des signalements historiques et validation des tests représentatifs.

La clôture de BDL-1 ne signifie pas que tous les extracteurs ont été validés sémantiquement, que le corpus couvre tous les types, ni que le mode strict est prêt à être activé. Elle signifie que le contrat documentaire initial est suffisamment défini pour servir de référence aux étapes suivantes.
