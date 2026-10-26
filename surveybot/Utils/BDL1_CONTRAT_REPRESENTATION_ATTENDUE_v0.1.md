# SurveyBot — Contrat de représentation attendue
Version : 0.2 — brouillon de cadrage BDL-1
Statut : PARTIEL — décisions produit encore nécessaires
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

## 7. Vérification de `dom_analyzer.py`

Lecture du fichier `Survey/dom_analyzer.py` fourni pour cette étape.

### Types identifiables dans ce fichier

Les valeurs suivantes sont visibles dans les blocs produits, dans les branches de traitement ou dans les commentaires explicites :
- `radio`
- `checkbox`
- `dropdown`
- `text`
- `textarea`
- `hidden` (type reconnu dans la boucle des champs simples, puis ignoré)
- `unknown` (valeur de détection intermédiaire, parfois réévaluée localement)
- `button` (type traité spécifiquement dans la boucle des champs simples)
- `matrix` (type explicitement émis par l'extracteur Nfield drag-and-drop)

`multi_text` apparaît comme valeur de `kind`/contexte et de registre, pas comme valeur explicitement attribuée à `itype` dans les passages vérifiés ; il ne faut donc pas le compter comme un `itype` confirmé sur cette seule base.

### Limite de cette vérification

**Cette liste n'est pas prouvée exhaustive par `dom_analyzer.py` seul.** Le fichier importe `_detect_itype` depuis `Survey.dom_utils`, et plusieurs extracteurs spécialisés transmettent des variables calculées (`itype`, `_block_itype`) dont l'ensemble des valeurs ne peut pas être déterminé en lisant uniquement ce fichier. Le fichier fourni permet donc d'établir une liste provisoire, pas de certifier tous les types effectivement émis à l'exécution.

Pour confirmer l'exhaustivité, il faut également vérifier la fonction `_detect_itype` dans `Survey/dom_utils.py` et les valeurs de `_block_itype` produites par les extracteurs appelés. Je ne complète pas ces éléments par supposition.

## 8. Points à vérifier avant de déclarer BDL-1 terminé

- [x] Décider le traitement de « Autre » et de son champ texte associé : option A, réponse composée avec association explicite.
- [x] Examiner `dom_analyzer.py` et relever les types visibles dans ce fichier.
- [ ] Vérifier `_detect_itype` dans `Survey/dom_utils.py`.
- [ ] Confirmer les valeurs produites par les variables de type `_block_itype` dans les extracteurs spécialisés.
- [ ] Ajouter une entrée de contrat pour chaque `itype` effectivement émis qui n'est pas encore couvert.
- [ ] Vérifier les champs obligatoires, les limites de sélection et les options placeholder.
- [ ] Versionner ce contrat hors du core et faire référencer cette version par l'oracle BDL-4.

## 9. Statut de cette version

**BDL-1 reste en cours.** La règle produit « Autre » est maintenant fixée. L'examen de `dom_analyzer.py` identifie les types listés ci-dessus, mais ne suffit pas à garantir l'exhaustivité puisque la détection et une partie des types de blocs sont définies ou calculées ailleurs.

Aucune fonction du core n'est modifiée par ce document.
