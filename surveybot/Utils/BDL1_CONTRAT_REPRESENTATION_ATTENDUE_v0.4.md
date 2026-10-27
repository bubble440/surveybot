# SurveyBot — Contrat BDL-1 : représentation attendue

Version : 0.4 — contraintes détaillées et protocole d’inventaire
Statut : PARTIEL — contraintes précisées statiquement ; inventaire runtime à exécuter sur le corpus
Branche étudiée : `feature/phase-1a-observability`
Portée : blocs `dict` retournés par `Survey.dom_analyzer.analyze_dom()`.

## 1. Objectif et limites

Le contrat décrit la représentation fonctionnelle attendue et les invariants structurels qui peuvent être testés. Il ne transforme pas la sortie actuelle du bot en vérité terrain : les snapshots historiques servent à la régression, pas à prouver à eux seuls qu’une extraction est correcte.

Trois niveaux doivent rester distincts :

1. **Type attesté statiquement** : une valeur `itype` est explicitement produite par le code inspecté.
2. **Type observé à l’exécution** : la valeur est apparue dans une sortie réelle `analyze_dom()`, capturée dans un artefact.
3. **Conformité sémantique validée** : un DOM de référence et une vérité terrain indépendante confirment que le bloc représente correctement la question.

Le présent document établit le premier niveau et fournit un outil pour mesurer le deuxième. Il ne prétend pas avoir exécuté le troisième.

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

La liste reste ouverte jusqu’à exécution de l’inventaire sur un corpus représentatif. Tout `itype` inattendu doit être signalé comme « non documenté », pas automatiquement déclaré bug d’extraction.

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
- **Invariants structurels** : `options` devrait être une liste ; pour un groupe de choix standard, au moins deux options répondables sont attendues. Si `max_select` est renseigné, il ne devrait pas autoriser plusieurs choix.
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
- **Attendu** : contexte de question et cible d’action ; les bornes, unité et précision sont conservées lorsqu’elles existent dans le DOM ou les métadonnées.
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
- **Réserve** : type distinct dans les sorties actuelles ; ne pas normaliser vers `select` ou `dropdown` avant de confirmer l’équivalence fonctionnelle.
- **Test représentatif** : comparer la structure des blocs observés à celle des cas DOM de référence RPS, notamment les options et le locator enregistré.

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

La prochaine étape recommandée est additive : utiliser un outil d’inventaire sur des artefacts `question_blocks.json` existants, puis ajouter des tests de contrat ciblés. Ne pas modifier les corps des extracteurs existants pour réaliser cette étape.

## 7. Inventaire automatisé : protocole

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

## 8. Tests représentatifs à ajouter après l’inventaire

Ordre minimal recommandé :

1. **Test de forme commun** : chaque bloc est un dictionnaire, `itype` est connu, `target_id` non vide, `options` est une liste si présent.
2. **Test des limites** : `min_select <= max_select` quand les deux sont positifs ; `checkbox.max_select` ne dépasse pas le nombre d’options lorsque les valeurs sont comparables.
3. **Test registre** : sur une extraction réelle, chaque `target_id` résout vers une cible enregistrée avant tout `clear_registry()`.
4. **Test par famille** : un cas de référence radio, checkbox, dropdown, text, textarea, matrix, number, select, select_rps et CTA button, idéalement issu de DOM/snapshots déjà validés.
5. **Test inconnu** : un nouveau `itype` doit faire échouer le contrôle strict et apparaître clairement dans le rapport, sans déclencher de correction automatique.
6. **Test « Autre »** : l’option et le champ lié restent représentés ensemble sans duplication parasite.

Ne pas utiliser la seule présence d’un type dans un snapshot comme preuve de conformité sémantique. Pour les tests de référence, enregistrer séparément le DOM/cas, le bloc attendu et la justification de la vérité terrain.

## 9. Critères de clôture BDL-1

- [x] Décision produit « Autre » : option A.
- [x] Cartographie statique des dix types attestés.
- [x] Contraintes fonctionnelles et limites de validation documentées par type.
- [x] Outil d’inventaire fourni.
- [ ] Exécuter l’inventaire sur le corpus de snapshots du dépôt et examiner les types observés.
- [ ] Ajouter/adapter les tests pytest représentatifs dans le dépôt.
- [ ] Confirmer la cohérence registre ↔ `target_id` sur les sorties réelles.
- [ ] Versionner le contrat hors du core et le faire référencer par l’oracle BDL-4.

**Statut : BDL-1 en cours.** La cartographie et les invariants de premier niveau sont prêts ; les résultats d’exécution et les cas de référence restent à collecter.
