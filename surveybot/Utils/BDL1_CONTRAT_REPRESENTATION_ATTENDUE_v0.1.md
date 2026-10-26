# SurveyBot — Contrat de représentation attendue
Version : 0.3 — cartographie statique élargie du pipeline
Statut : PARTIEL — types cartographiés, contrat sémantique encore à compléter
Portée : contrat indépendant de l'implémentation, destiné à l'oracle d'extraction BDL-4 et au classement des règles historiques BDL-9.

## 1. Rôle du contrat

Ce document décrit ce que SurveyBot doit exposer au modèle de réponse après analyse d'une page d'enquête. Il ne décrit ni les sélecteurs DOM, ni les extracteurs, ni les stratégies de nettoyage.

Le contrat est indépendant du code : une représentation ne devient pas correcte simplement parce que le code actuel la produit. Les snapshots historiques non qualifiés ne constituent pas, à eux seuls, une vérité terrain.

## 2. Principes normatifs

1. Une question active à laquelle le répondant doit répondre doit être représentée, sauf si elle est explicitement hors du périmètre de réponse ou si une décision produit documentée autorise son exclusion.
2. Les informations nécessaires pour répondre doivent rester accessibles : formulation de la question, type de réponse, choix disponibles lorsqu'il y en a, et champ de saisie associé lorsqu'il est nécessaire à la réponse.
3. Le contrat porte sur le sens et les informations exposées, pas sur l'identité exacte des nœuds DOM ni sur une égalité brute entre le DOM et la sortie SurveyBot.
4. Un élément DOM supplémentaire ne constitue pas automatiquement une erreur d'extraction : il peut être décoratif, auxiliaire, technique ou volontairement regroupé. L'oracle doit disposer d'indices indépendants avant de conclure à une contradiction.
5. Si le contrat ne tranche pas explicitement un cas, l'oracle doit le classer comme non déterminé, et non comme échec confirmé.
6. Les substitutions de valeur effectuées pendant l'exécution d'une action relèvent du contrat d'action, pas du contrat de représentation d'une question.

## 3. Contrat par type `itype`

Les types ci-dessous sont ceux attestés dans les documents de vérification disponibles. La liste n'est pas garantie exhaustive : elle devra être comparée à l'ensemble des `itype` effectivement émis par le pipeline avant de déclarer BDL-1 complet.

### 3.1 `radio`

**Représentation attendue**
- La question ou le libellé qui donne le sens au groupe de choix est exposé lorsqu'il est disponible.
- Les choix répondables du groupe sont exposés avec leurs libellés compréhensibles.
- Le fait qu'il s'agit d'un choix unique est conservé.
- Une seule option choisie ne doit pas être confondue avec une liste de choix multiples.

**Contradictions fortes**
- Une question active visible avec un groupe radio répondable est absente de la représentation sans exclusion documentée.
- Un choix visible et répondable manque de la représentation, ou un choix présenté au modèle n'existe pas dans le groupe actif, sous réserve des règles de visibilité et de désactivation.

**Non concluants à eux seuls**
- Différence du nombre de nœuds DOM et du nombre d'options exposées.
- Éléments cachés, désactivés, dupliqués ou auxiliaires sans preuve qu'ils sont répondables.

### 3.2 `checkbox`

**Représentation attendue**
- La question ou l'instruction qui donne le sens au groupe est exposée lorsqu'elle est disponible.
- Les choix répondables sont exposés avec leurs libellés compréhensibles.
- Le fait que plusieurs choix peuvent être sélectionnés est conservé.
- Une instruction de sélection (« un seul », « tous ceux qui s'appliquent », limite de sélection) doit être conservée lorsqu'elle modifie la manière correcte de répondre.

**Contradictions fortes**
- Une question active visible avec des cases répondables est absente sans exclusion documentée.
- Un choix répondable est omis ou un choix inexistant est présenté comme disponible, après prise en compte de la visibilité et de l'état actif.

**Non concluants à eux seuls**
- Présence de cases techniques, masquées ou désactivées.
- Différence brute entre les contrôles DOM et les choix exposés.

### 3.3 `dropdown`

**Représentation attendue**
- La question ou le libellé du champ est exposé lorsqu'il est disponible.
- Les choix réellement disponibles pour répondre sont exposés avec leurs libellés.
- Le caractère de sélection unique est conservé, sauf preuve explicite que le contrôle autorise plusieurs valeurs.
- Une option placeholder servant uniquement d'invite (par exemple « Choisir… ») ne doit pas être présentée comme une réponse réelle sans preuve qu'elle est sélectionnable comme réponse.

**Contradictions fortes**
- Le champ actif est absent sans exclusion documentée.
- Les choix réels ne sont pas représentés ou des choix non disponibles sont exposés comme répondables.

**Non concluants à eux seuls**
- Présence d'une option placeholder ou d'options masquées sans preuve de leur rôle fonctionnel.

### 3.4 `text`

**Représentation attendue**
- Un champ texte autonome et répondable est exposé avec son libellé ou son contexte de question lorsqu'ils sont disponibles.
- Le champ doit rester distinct d'un champ de recherche, filtre, navigation ou autre contrôle qui ne recueille pas la réponse à l'enquête.
- Si le champ est indispensable pour compléter une option choisie, sa relation avec cette option doit être préservée ou rendue intelligible.

**Contradictions fortes**
- Un champ répondable autonome, visiblement associé à une question active, est omis sans exclusion documentée.
- Un champ technique de recherche ou de filtre est présenté comme une réponse d'enquête alors que les éléments disponibles montrent qu'il ne sert pas à répondre.

**Non concluants à eux seuls**
- Champ texte voisin d'un groupe radio/checkbox : sa fonction doit être établie par le contexte, et non déduite de la proximité seule.

### 3.5 `textarea`

**Représentation attendue**
- Même principe que `text`, pour un champ de saisie multiligne.
- Le champ est exposé avec le contexte qui permet de comprendre ce qui est demandé.
- Un champ multiligne répondable ne doit pas disparaître uniquement parce qu'un groupe de choix se trouve à proximité.

**Contradictions fortes**
- Un champ multiligne répondable associé à une question active est omis sans exclusion documentée.
- Un champ non répondable est présenté comme un champ de réponse sans preuve fonctionnelle suffisante.

**Non concluants à eux seuls**
- Proximité d'options, présence d'un placeholder générique ou absence d'un attribut `required`, pris isolément.

### 3.6 `matrix`

**Représentation attendue**
- La consigne ou question globale est exposée, ainsi que les libellés des lignes et des colonnes nécessaires à l'interprétation.
- La structure ligne × colonne est préservée : une valeur choisie doit pouvoir être rattachée à la bonne ligne et à la bonne échelle/colonne.
- Les contraintes de réponse (choix unique ou multiple par ligne, réponses obligatoires) sont conservées lorsqu'elles sont établies.

**Contradictions fortes**
- La grille est réduite à une liste plate qui ne permet plus de relier les choix aux lignes concernées.
- Une ligne ou une option répondable est omise alors que son rôle est confirmé.

**Non concluants à eux seuls**
- Nombre de cellules DOM, nombre de blocs produits ou présence de cellules cachées sans preuve qu'elles sont des réponses actives.

### 3.7 `number`

**Représentation attendue**
- La demande numérique et son contexte sont exposés ; les contraintes de valeur (minimum, maximum, unité, précision) sont conservées lorsqu'elles sont disponibles.
- Le champ reste distingué d'un champ texte libre si le type numérique affecte la réponse attendue.

**Contradictions fortes**
- Un champ numérique répondable est omis ou représenté comme une réponse textuelle générique alors que cette différence change les contraintes de réponse.

**Non concluants à eux seuls**
- L'absence d'une contrainte min/max dans le DOM observé ne prouve pas que toute valeur numérique est valide.

### 3.8 `select`

**Représentation attendue**
- Même intention fonctionnelle que `dropdown` : le champ, son contexte et les options réellement répondables sont exposés.
- Le type brut `select` est conservé dans les traces tant qu'une règle de normalisation explicite ne le ramène pas à une famille commune.
- Le caractère unique ou multiple de la sélection est établi à partir du contrôle et du contexte, pas seulement du nom du type.

**Contradictions fortes**
- Le champ actif ou ses options répondables sont absents sans exclusion justifiée.

**Non concluants à eux seuls**
- Une liste vide dans la sortie ne prouve pas à elle seule qu'aucune option n'est disponible : certains widgets nécessitent une ouverture ou un état DOM particulier.

### 3.9 `select_rps`

**Représentation attendue**
- Le contrôle de sélection spécialisé est représenté avec son libellé, ses options et ses contraintes de sélection disponibles.
- Ce type est distinct dans le code actuel de `select` et `dropdown` ; l'oracle ne doit pas les fusionner silencieusement sans vérifier les différences sémantiques.

**Contradictions fortes**
- Le contrôle est absent, ou ses choix répondables sont omis/inventés, alors que le DOM confirme leur disponibilité.

**Non concluants à eux seuls**
- Le suffixe technique du type ne suffit pas à inférer une différence de comportement fonctionnel. Celle-ci doit être documentée à partir du widget concerné.

### 3.10 `button`

**Rôle dans le pipeline**
- Le type `button` est émis par le chemin `sliderpoints_extractor` pour un contrôle de type CTA. Sa présence dans la sortie d'analyse ne signifie donc pas automatiquement qu'il s'agit d'une question répondable.
- Il doit être traité comme un contrôle d'action/navigation, sauf preuve explicite qu'il représente une option de réponse.
- Son observation et son résultat relèvent prioritairement du contrat CTA/action (BDL-5/BDL-3), et non de l'oracle d'extraction de questions.

**Contradictions fortes**
- Un CTA nécessaire au parcours est introuvable ou son état/résultat est mal déclaré par la couche d'action.

**Non concluants à eux seuls**
- La présence d'un bloc `button` ne constitue ni une question manquante ni une réponse d'enquête supplémentaire.

## 4. Cas « Autre / AUTRE » — décision produit requise

Les documents de vérification signalent plusieurs règles distinctes autour des champs « Autre, précisez » et des champs texte auxiliaires. Le contrat ne doit pas reprendre implicitement leur comportement actuel.

**Décision produit validée : option A — représentation liée.**

Lorsqu'une option « Autre / Other / Précisez » est associée à un champ texte de précision, la représentation attendue est une **réponse composée** :
- l'option « Autre » reste un choix disponible du groupe radio/checkbox ;
- le champ de précision reste explicitement associé à cette option ;
- le modèle doit pouvoir comprendre que le texte est la précision de l'option « Autre », et non une question texte autonome sans relation ;
- cette association ne doit pas faire disparaître l'option du groupe, ni dupliquer le champ de précision comme une question indépendante parasite.

Cette règle s'applique uniquement lorsque l'association est établie par le contexte fonctionnel et/ou des indices DOM suffisants. La simple proximité entre un champ texte et un groupe de choix ne suffit pas à établir le lien. Les différents motifs DOM spécifiques déjà présents dans le code sont des indices de détection, pas à eux seuls la définition complète du contrat.

## 5. Questions, instructions et champs auxiliaires

- Le texte qui modifie la réponse attendue — par exemple une instruction de sélection — fait partie de la représentation attendue.
- Les éléments de navigation, de recherche, de filtrage, de consentement technique ou purement décoratifs ne sont pas des réponses d'enquête par défaut.
- L'exclusion d'un champ ne peut pas être considérée comme conforme sur la seule base du fait qu'une heuristique actuelle l'exclut. Il faut une justification fonctionnelle ou DOM indépendante.
- Une relation entre un champ et une option doit être préservée lorsque cette relation change le sens de la réponse.

## 6. Ce que l'oracle peut conclure

| Situation observée | Conclusion autorisée |
|---|---|
| Question active et répondable absente, sans exclusion documentée | Contradiction potentielle forte |
| Choix répondable manquant ou inventé dans un groupe actif | Contradiction potentielle forte |
| Champ auxiliaire dont le rôle n'est pas déterminable | Indéterminé |
| Écart de comptage DOM/sortie sans preuve de répondabilité | Insuffisant pour conclure |
| Cas « Autre » avant validation de la décision produit | Hors verdict de conformité |
| Cas dont la vérité terrain n'est pas documentée | Cas de régression possible, pas vérité terrain |

Ce contrat n'autorise pas l'oracle à réimplémenter les extracteurs ni à inférer la représentation attendue à partir de la sortie actuelle du bot.

## 7. Cartographie statique du pipeline d'extraction

Cette vérification porte sur la branche `feature/phase-1a-observability` du dépôt, en distinguant les blocs de questions retournés au pipeline des types internes au registre et des types produits par un analyseur séparé.

### 7.1 Types confirmés dans les blocs du pipeline principal

La lecture croisée de `Survey/dom_analyzer.py`, `Survey/dom_utils.py`, `Survey/dom_extractors_misc.py`, `Survey/dom_extractors_decipher.py`, `Survey/dom_extractors_areyounet.py` et `Survey/sliderpoints_extractor.py` confirme les valeurs suivantes dans les blocs construits ou ajoutés au résultat du pipeline :

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

Les valeurs `radio` et `checkbox` peuvent être déterminées par `_detect_itype`, par des extracteurs spécialisés ou par `_block_itype` (ce dernier ne produit que `radio` ou `checkbox`). `_detect_itype` dans `Survey/dom_utils.py` retourne directement `radio`, `checkbox`, `dropdown`, `text`, `textarea` ou `unknown`. `dom_analyzer.py` comporte aussi une réclassification locale de certains champs `input[type=search]` vers `text`, sous garde-fou DOM spécifique.

Les extracteurs spécialisés ajoutent des types que la détection générique ne couvre pas à elle seule : notamment `matrix`, `number`, `select` et `select_rps`. Le chemin `sliderpoints_extractor` émet aussi `button` pour un contrôle CTA et `dropdown` pour des options de sélection liées au widget.

### 7.2 Valeurs qui ne doivent pas être confondues avec des types de question confirmés

- `unknown` est une valeur de détection intermédiaire. La lecture du chemin générique montre qu'elle est normalement écartée, mais certains extracteurs spécialisés peuvent décider autrement ; elle n'est pas retenue ici comme type de question effectivement exposé au modèle.
- `hidden` et `button` apparaissent dans des branches ou commentaires de traitement, mais cela ne suffit pas à les considérer comme types de question. `button` est toutefois confirmé séparément comme type de bloc CTA par `sliderpoints_extractor` ; il doit donc être traité comme contrôle d'action, pas automatiquement comme question.
- `rtype` apparaît dans un payload de registre d'un extracteur CMix. La valeur a été trouvée dans le registre, mais pas confirmée comme valeur d'un bloc de question retourné ; elle n'est donc pas ajoutée à la liste des types de représentation confirmés.
- `multi_text` apparaît comme `kind`/contexte dans certains chemins, et non comme valeur littérale confirmée de `itype` dans les passages vérifiés.
- `Survey/question_block_analyzer.py` possède un `_infer_itype` distinct (dont `button`) ; ses sorties ne doivent pas être mélangées sans preuve au contrat de sortie de `analyze_dom`.

### 7.3 Niveau de confiance et limite d'exhaustivité

La liste ci-dessus est **confirmée par inspection statique des modules principaux et des modules spécialisés importés dans `dom_analyzer.py`**, mais elle ne constitue pas une garantie absolue d'exhaustivité à l'exécution. Certains blocs sont construits via des variables calculées, des branches conditionnelles ou des extracteurs ajoutés ultérieurement ; le dépôt peut évoluer et un test exhaustif sur sorties réelles n'a pas été exécuté dans cette étape.

En conséquence :
- la liste est suffisante pour étendre le contrat aux types supplémentaires confirmés ;
- BDL-1 ne doit pas être déclaré définitivement exhaustif tant qu'un inventaire automatisé ou des tests représentatifs ne vérifient pas les `itype` réellement retournés par le pipeline ;
- tout type inconnu rencontré à l'exécution doit être classé « type non documenté / verdict indéterminé », et non transformé en contradiction forte par défaut.

## 8. Points à vérifier avant de déclarer BDL-1 terminé

- [x] Décider le traitement de « Autre » et de son champ texte associé : option A, réponse composée avec association explicite.
- [x] Examiner `dom_analyzer.py` et relever les types visibles dans ce fichier.
- [x] Vérifier `_detect_itype` dans `Survey/dom_utils.py`.
- [x] Vérifier les types émis par les principaux extracteurs spécialisés appelés par `dom_analyzer.py`.
- [x] Ajouter des sections de contrat pour les types supplémentaires confirmés (`matrix`, `number`, `select`, `select_rps`) et distinguer le CTA `button` des questions.
- [ ] Vérifier les champs obligatoires, les limites de sélection et les options placeholder pour chaque famille/type.
- [ ] Ajouter un inventaire automatisé ou des tests de contrat sur les blocs réellement retournés par le pipeline, afin de confirmer l'exhaustivité à l'exécution.
- [ ] Versionner ce contrat hors du core et faire référencer cette version par l'oracle BDL-4.

## 9. Statut de cette version

**BDL-1 reste en cours.** La règle produit « Autre » est fixée et la cartographie statique a été étendue aux modules principaux et spécialisés. Dix valeurs `itype` sont confirmées dans les blocs du pipeline inspecté. L’exhaustivité à l’exécution et les contraintes détaillées de certains types restent à valider.

Aucune fonction du core n'est modifiée par ce document.
