# BUG DETECTION LAYER --- SUIVI DU CHANTIER

**Projet :** SurveyBot\
**Statut :** CONCEPTION --- chantier non implémenté\
**Périmètre :** détection indépendante et évaluation des anomalies
d'exécution avant déclenchement de l'AutoFix\
**Relation avec l'AutoFix :** amont direct du pipeline de failure cases
/ diagnostic / correction

------------------------------------------------------------------------

## 1. Contexte

Le pipeline d'AutoFix a été créé pour automatiser une activité qui était
auparavant principalement manuelle :

``` text
page live / mode attach
        ↓
Wilfried constate visuellement un bug
        ↓
description du bug
        ↓
snapshot + fichiers + contexte
        ↓
diagnostic préparé pour Claude
        ↓
prompt transmis à Claude Code / Codex
        ↓
correctif
        ↓
tests
        ↓
intégration
```

Cette méthode fonctionne, mais elle présente deux limites structurelles
:

1.  la vitesse d'identification des incidents dépend d'une intervention
    humaine ;
2.  rien ne garantit, à elle seule, qu'un correctif adopté aujourd'hui
    ne sera pas rendu obsolète ou altéré par une évolution ultérieure du
    core.

Le pipeline AutoFix répond principalement au deuxième problème et
automatise la chaîne :

``` text
incident
→ failure case
→ replay
→ diagnostic
→ contexte
→ prompt
→ patch isolé
→ validation
→ confiance
→ revue
→ merge
```

Mais cette chaîne suppose une chose essentielle :

> **le système doit d'abord savoir qu'un incident existe.**

C'est précisément le problème traité par ce chantier.

------------------------------------------------------------------------

# 2. Problème fondamental

Le problème n'est pas simplement de savoir si
`question_block_validator.py` ou `action_validator.py` contient
suffisamment de règles.

La question fondamentale est :

> **Comment SurveyBot sait-il qu'il vient réellement de rencontrer un
> bug lorsque le résultat produit peut lui-même être incorrect sans
> générer de signal d'erreur ?**

Exemple :

``` text
DOM réel
   ↓
extracteur
   ↓
résultat incorrect
   ↓
validator
   ↓
PASS
```

Le pipeline AutoFix ne peut rien corriger si aucune anomalie n'est
détectée.

Cela introduit un risque de faux négatif :

``` text
BUG RÉEL
   ↓
non détecté
   ↓
aucun failure case
   ↓
aucun diagnostic
   ↓
aucun AutoFix
```

La priorité de ce chantier est donc de fiabiliser **l'oracle de
détection**, avant de chercher à augmenter davantage l'autonomie du
moteur de correction.

------------------------------------------------------------------------

# 3. Objectif du Bug Detection Layer

Le Bug Detection Layer doit fournir une observation indépendante et
continue du comportement de SurveyBot.

Il ne doit pas nécessairement déterminer immédiatement la cause exacte
du bug.

Son rôle premier est de répondre à :

> **L'exécution observée est-elle suffisamment incohérente pour être
> considérée comme normale, suspecte ou défaillante ?**

Le modèle conceptuel retenu est :

``` text
PASS
    comportement cohérent, aucun signal fort

SUSPECTED
    incohérence détectée, mais preuve insuffisante pour affirmer un bug

BUG_CONFIRMED
    preuve suffisamment forte d'un comportement incorrect
```

Le niveau `SUSPECTED` est important : le système ne doit pas être forcé
à transformer toute incertitude en `BUG` ou `PASS`.

------------------------------------------------------------------------

# 4. Trois familles de bugs à couvrir

La Phase 1B historique s'est principalement concentrée sur l'extraction
et les actions. Le Bug Detection Layer doit explicitement couvrir une
troisième famille : les CTA.

## 4.1 Bugs d'extraction

Questions à détecter :

-   information réellement perdue ;
-   bloc absent ;
-   options manquantes ;
-   question polluée ;
-   doublons ;
-   mauvais `itype` ;
-   limites de sélection incohérentes ;
-   représentation incompatible avec les éléments observables ;
-   information visible et pertinente qui n'est couverte par aucune
    représentation exploitable.

Mais une différence entre DOM et résultat SurveyBot n'est **pas
automatiquement un bug**.

------------------------------------------------------------------------

## 4.2 Bugs d'action / sélection

Questions à détecter :

-   cible introuvable ;
-   option introuvable ;
-   action non appliquée ;
-   mauvaise option sélectionnée ;
-   sélection multiple incomplète ;
-   sélection inattendue ;
-   valeur de champ incorrecte ;
-   dispatcher signalant un échec alors que l'état attendu est présent ;
-   dispatcher signalant un succès alors que l'état attendu n'est pas
    confirmé.

Le cas IFOP zip2city a notamment montré :

``` text
dispatcher_success = false
≠
preuve certaine que l'état attendu est absent
```

Le DOM post-action peut constituer une preuve indépendante du résultat
du dispatcher.

------------------------------------------------------------------------

## 4.3 Bugs de CTA / navigation

Les CTA doivent être considérés comme une famille à part entière.

Exemples :

-   CTA non trouvé ;
-   mauvais CTA choisi ;
-   clic non exécuté ;
-   clic exécuté mais transition attendue absente ;
-   navigation réussie malgré un signal d'échec ;
-   transition interne réussie sans navigation classique.

Le CTA ne doit pas être traité comme une simple action de sélection.

------------------------------------------------------------------------

# 5. Particularité essentielle des CTA

`cta_handler.py` contient plusieurs modèles de CTA et plusieurs
contextes d'exécution : boutons texte, boutons icône, CTA structurels
propres à certaines sources, variantes dans les iframes, etc.

Il existe également des comportements où un clic est volontairement
suivi d'une modification interne de la page plutôt que d'une navigation
classique.

Exemple : une page de type slider peut changer d'état ou de panneau sans
changement d'URL et sans nouvelle page HTML.

Donc :

``` text
clic CTA
↓
URL inchangée
```

ne signifie pas automatiquement :

``` text
BUG
```

Le Bug Detection Layer doit rechercher une **transition attendue**, pas
uniquement une navigation classique.

Conceptuellement :

``` text
CTA
 │
 ├── navigation classique
 │      └── URL/document/page change
 │
 ├── transition interne
 │      └── état DOM/UI pertinent change
 │
 ├── CTA_INTERCEPT_ONLY
 │      └── aucune navigation attendue
 │
 └── véritable échec
        └── aucune transition attendue détectable
```

Le mode `CTA_INTERCEPT_ONLY` doit être explicitement pris en compte :
lorsqu'il est activé, l'absence de navigation est le comportement
attendu.

------------------------------------------------------------------------

# 6. Principe d'indépendance de l'oracle

Le Bug Detection Layer ne doit pas devenir une copie de
`dom_analyzer.py`, `action_dispatcher.py` ou `cta_handler.py`.

Il doit conserver une distance suffisante avec le mécanisme qu'il
observe.

Principe :

``` text
SurveyBot
   ↓
résultat produit
   ↓
observation indépendante
   ↓
cohérence / contradiction
```

L'oracle n'a pas besoin de savoir reproduire toute la logique métier du
core.

Il cherche plutôt des preuves indépendantes ou complémentaires :

-   présence d'éléments DOM ;
-   états avant / après ;
-   éléments interactifs ;
-   transitions observables ;
-   contradictions entre le résultat du bot et l'état réel de la page ;
-   cohérence entre plusieurs signaux.

------------------------------------------------------------------------

# 7. Attention aux règles historiques du core

Une difficulté importante a été identifiée pendant la conception.

Certaines règles du projet ont été créées tôt pour modifier
volontairement la représentation de certaines informations.

Exemple :

``` text
DOM
→ plusieurs éléments / options
→ règle historique
→ représentation SurveyBot volontairement différente
```

Le cas conceptuel « Autre / AUTRE » illustre ce problème : certains
éléments peuvent être volontairement ignorés, regroupés ou traités comme
auxiliaires afin de ne pas polluer le prompt destiné au modèle de
réponse.

Par conséquent :

``` text
DOM = 6 éléments
SurveyBot = 5 éléments
```

ne suffit pas à conclure :

``` text
BUG
```

La différence peut être une transformation intentionnelle.

## Décision de conception

Le Bug Detection Layer ne doit pas, dans sa première version, devenir un
lecteur complet de toutes les règles historiques du core.

Sinon :

``` text
oracle
→ comprend dom_analyzer
→ comprend ses exceptions
→ comprend les exceptions des exceptions
→ devient une copie du core
```

Ce serait contraire à l'objectif d'indépendance.

Le détecteur doit plutôt rechercher des contradictions suffisamment
fortes pour signaler :

``` text
PASS
SUSPECTED
BUG_CONFIRMED
```

Une règle historique ne doit être supprimée ou déclarée obsolète
uniquement parce qu'elle complique la détection.

Si une règle est réellement devenue obsolète, sa suppression relève
d'une décision séparée sur le core.

------------------------------------------------------------------------

# 8. Distinction entre extraction brute et interprétation

Le projet ne réalise pas uniquement :

``` text
DOM → extraction
```

Il existe conceptuellement plusieurs niveaux :

``` text
DOM brut
   ↓
détection de structures
   ↓
interprétation
   ↓
normalisation
   ↓
représentation SurveyBot
   ↓
génération du prompt
```

Le Bug Detection Layer doit donc distinguer :

``` text
élément absent
```

de :

``` text
élément volontairement transformé / absorbé / ignoré
```

Cette distinction est essentielle pour éviter une explosion de faux
positifs.

------------------------------------------------------------------------

# 9. Oracle d'action : principe avant / après

Pour les actions, l'observation avant et après action constitue un
signal particulièrement important.

Conceptuellement :

``` text
état avant
   ↓
action
   ↓
état après
```

Le système peut alors distinguer :

``` text
état attendu absent avant
+
état attendu présent après
→ action probablement effective
```

de :

``` text
état attendu absent avant
+
état attendu absent après
→ action suspecte / potentiellement échouée
```

et :

``` text
dispatcher = failure
+
état attendu présent après
→ possible faux négatif du dispatcher
```

La capture `pre_action_dom.html` déjà ajoutée à l'observabilité est
particulièrement utile pour cette approche.

------------------------------------------------------------------------

# 9 bis. Principe commun : état / comportement attendu

Le Bug Detection Layer ne doit pas seulement constater qu'un comportement
est différent ou qu'un bug peut être rejoué. Pour qu'un cas soit exploitable
par l'AutoFix, il faut pouvoir distinguer :

``` text
comportement observé
        ≠
comportement attendu
```

Ce principe vaut pour les trois familles, avec un oracle adapté à chacune.

### Extraction

``` text
DOM / page initiale
        ↓
extraction
        ↓
représentation observée
        ↓
représentation attendue
        ↓
comparaison
```

L'état attendu concerne la représentation SurveyBot attendue, et non une
égalité mécanique entre le DOM brut et la sortie du bot. Une différence DOM
peut être volontairement transformée, absorbée ou ignorée par une règle
d'interprétation existante.

### Action

``` text
état avant
   ↓
action
   ↓
état observé
   ↓
état attendu
   ↓
comparaison
```

Le replay post-patch doit donc vérifier que le patch produit réellement
l'état attendu. Le simple fait que le symptôme initial ne soit plus observé
ne suffit pas.

### CTA

``` text
état avant
   ↓
CTA
   ↓
transition observée
   ↓
transition attendue
   ↓
comparaison
```

La transition attendue peut être une navigation classique, une transition
interne, un changement de slide/panneau ou un autre changement fonctionnel
observable. Le changement d'URL n'est jamais un oracle universel.

Cette structure permet d'utiliser le même principe de preuve pour les trois
familles sans créer trois systèmes de validation indépendants.

------------------------------------------------------------------------

# 9 ter. Replay : rôle fondamental dans la validation

Le replay est une condition de confiance fondamentale du chantier, mais il
ne doit pas être réduit à :

``` text
rejouer le bug
→ constater qu'il a disparu
```

La chaîne attendue est :

``` text
scénario original
      ↓
replay pré-patch
      ↓
reconfirmation du comportement défaillant
      ↓
patch
      ↓
replay post-patch du même scénario
      ↓
vérification de l'état / comportement attendu
      ↓
CORRECTIF_CONFIRME / BUG_PERSISTANT / NON_CONCLUANT
```

Un patch ne doit donc pas être considéré comme correct uniquement parce que
le signal du bug original n'apparaît plus. Il faut établir que le résultat
obtenu correspond au comportement attendu lorsque celui-ci est suffisamment
observable.

La classification de rejouabilité reste déterminante :

``` text
STATIC_DOM
BROWSER_CAPSULE
TRACE_REPLAY
EXTERNAL_NON_REPLAYABLE
```

Un cas non entièrement rejouable ne doit jamais être présenté comme une
preuve locale complète. `TRACE_REPLAY` permet d'exploiter les faits capturés
sans prétendre avoir reproduit une interaction provider impossible à
reconstituer fidèlement. `EXTERNAL_NON_REPLAYABLE` reste une conclusion
valide, orientée vers une validation live lorsque nécessaire.

Le replay est donc à considérer comme un partenaire direct du Bug Detection
Layer :

``` text
BUG DETECTION
    ↓
preuve d'un comportement incorrect
    ↓
FAILURE CASE
    ↓
REPLAY
    ↓
preuve pré-patch
    ↓
PATCH
    ↓
preuve post-patch contre l'état attendu
```

------------------------------------------------------------------------

# 9 quater. Extractor Integrity et faux correctifs

`Extractor Integrity` conserve son rôle de garde-fou de l'intégrité du core
et des fonctions protégées. Il ne doit pas être remplacé par le replay ni
absorbé dans le Bug Detection Layer.

Les responsabilités sont distinctes :

``` text
Extractor Integrity
→ le patch a-t-il modifié illicitement un comportement protégé ?

Replay + oracle fonctionnel
→ le patch produit-il réellement le comportement attendu ?
```

Les deux contrôles sont complémentaires.

Les idées de rejeu de cas voisins, de bibliothèque de régression et de
détection de conflits entre correctifs restent pertinentes, mais elles sont
des mécanismes complémentaires et ne doivent pas être artificiellement
fusionnées avec `Extractor Integrity`.

------------------------------------------------------------------------

# 12 bis. Faisabilité du Bug Detection Layer

La faisabilité du chantier doit être évaluée séparément de la faisabilité
générale de l'AutoFix. Le BDL ne nécessite pas de comprendre 100 % des
sites ni de reconstruire le moteur SurveyBot. Son objectif est plus limité :
identifier avec suffisamment de fiabilité les contradictions observables
et refuser de conclure lorsque la preuve est insuffisante.

Évaluation initiale :

| Domaine | Faisabilité estimée |
|---|---|
| Détection des anomalies d'extraction | Élevée |
| Détection des anomalies d'action | Élevée |
| Détection des anomalies CTA | Moyenne à élevée |
| Vérification d'un état attendu | Élevée pour les cas déterministes |
| Replay extraction | Déjà largement couvert par l'infrastructure existante |
| Replay action | Déjà largement couvert sur le périmètre retenu |
| Replay CTA | À étendre selon les transitions réellement observées |
| Détection automatique de 100 % des bugs | Non réaliste |
| Objectif stable de 80–90 % | Réaliste sous réserve de mesure sur cas réels |

La principale difficulté n'est donc pas de détecter une anomalie simple.
Elle est de construire des preuves suffisamment fortes pour distinguer :

``` text
comportement réellement incorrect
vs
comportement inhabituel mais valide
vs
comportement dont la preuve est insuffisante
```

Le BDL doit rester indépendant sans devenir un second SurveyBot. C'est cette
contrainte qui doit limiter le nombre de règles, de branches et de fallbacks.

Le critère de faisabilité n'est donc pas « détecter tous les bugs », mais
plutôt :

``` text
réduire significativement les bugs silencieux
+
maintenir les faux positifs à un niveau acceptable
+
refuser explicitement les cas non prouvables
```

Une première implémentation déterministe est donc suffisamment crédible
pour être développée et mesurée avant d'envisager une couche IA.


# 10. Oracle CTA : principe avant / action / transition

Pour les CTA, l'observation doit être adaptée au type de transition
attendu.

Conceptuellement :

``` text
état avant
   ↓
CTA identifié
   ↓
clic tenté
   ↓
état après
   ↓
transition observable ?
```

Les signaux possibles peuvent inclure, selon le cas :

-   changement d'URL ;
-   changement de document ;
-   changement d'état DOM ;
-   changement d'un panneau ou d'un slider ;
-   évolution d'un indicateur de page ;
-   modification d'un état interactif ;
-   disparition / apparition d'éléments pertinents.

Aucun de ces signaux ne doit être considéré universellement obligatoire.

------------------------------------------------------------------------

# 11. L'IA n'est pas le fondement du détecteur

Une IA spécialisée dans la détection des bugs peut être envisagée, mais
elle ne doit pas être considérée comme un oracle absolu.

Architecture à éviter :

``` text
DOM + résultat
      ↓
IA
      ↓
BUG / PAS BUG
```

Le modèle peut lui-même produire un faux positif ou un faux négatif sans
preuve indépendante.

Architecture privilégiée :

``` text
signaux déterministes / observables
        ↓
contradictions ou anomalies
        ↓
SUSPECTED / BUG_CONFIRMED
        ↓
IA éventuelle pour interprétation
```

L'IA peut donc devenir un interprète de preuves complexes, mais ne doit
pas être la seule source de vérité.

------------------------------------------------------------------------

# 12. Le Bug Detection Layer doit être évalué lui-même

La question centrale n'est pas seulement :

> « Combien de bugs savons-nous détecter ? »

Elle est :

> **« Combien de vrais bugs laissons-nous passer ? »**

Il faut donc constituer progressivement un jeu de cas réels :

``` text
cas réel
→ vérité connue
→ résultat du détecteur
```

Taxonomie minimale :

``` text
TP — vrai bug détecté
FN — vrai bug non détecté
FP — absence de bug signalée comme bug
TN — absence de bug correctement laissée en PASS
```

Les cas historiques de SurveyBot constituent le point de départ naturel
de cette base d'évaluation.

------------------------------------------------------------------------

# 13. Benchmark de détection

Le benchmark doit contenir au minimum :

### Extraction

``` text
vrai bug / détecté
vrai bug / non détecté
fausse alerte / correctement ignorée
transformation volontaire / correctement ignorée
```

### Action

``` text
action réellement échouée / détectée
action réellement échouée / non détectée
dispatcher faux négatif / détecté
dispatcher faux positif / détecté
```

### CTA

``` text
CTA réellement échoué / détecté
CTA réellement échoué / non détecté
navigation réussie / correctement reconnue
transition interne réussie / correctement reconnue
CTA_INTERCEPT_ONLY / correctement ignoré
```

Le benchmark doit privilégier les cas réels plutôt qu'une grande
quantité de scénarios synthétiques sans rapport avec les DOM rencontrés.

------------------------------------------------------------------------

# 14. Échantillonnage humain

L'objectif final est de supprimer le contrôle visuel systématique.

Mais il n'est pas nécessaire de supprimer immédiatement toute
intervention humaine.

Une stratégie progressive peut être :

``` text
exécutions réelles
      ↓
Bug Detection Layer
      ↓
┌───────────┬────────────┬────────────────┐
│ PASS      │ SUSPECTED  │ BUG_CONFIRMED  │
└───────────┴────────────┴────────────────┘
      ↓           ↓               ↓
   faible      observation       AutoFix
   contrôle
```

Une partie des `PASS` peut également être échantillonnée pour rechercher
des faux négatifs.

Cela transforme l'humain :

``` text
contrôleur permanent
```

en :

``` text
instrument d'échantillonnage / amélioration du détecteur
```

Le contrôle humain ne disparaît donc pas immédiatement ; il devient
progressivement moins fréquent.

------------------------------------------------------------------------

# 15. Relation avec la Phase 1B

La Phase 1B historique a été conçue pour réduire les faux positifs et
les faux négatifs des validators d'extraction et d'action.

Le Bug Detection Layer reprend cet objectif mais élargit le périmètre.

La Phase 1B doit désormais être considérée comme un chantier de :

> **fiabilisation et d'évaluation des mécanismes de détection des
> anomalies du bot.**

Périmètre élargi :

``` text
1. Extraction
2. Action / sélection
3. CTA / navigation / transitions internes
```

Et non plus uniquement :

``` text
Extraction
Action
```

------------------------------------------------------------------------

# 16. Ce qui ne doit pas être fait

## Ne pas créer un second `dom_analyzer`

Le détecteur ne doit pas devenir un extracteur concurrent complet.

## Ne pas ajouter une cascade de fallbacks

Une multitude de règles indépendantes et difficiles à expliquer
réduirait la prédictibilité.

## Ne pas considérer chaque différence DOM comme un bug

Les règles d'interprétation historiques du core peuvent produire
volontairement une représentation différente du DOM brut.

## Ne pas considérer l'absence de navigation comme un échec CTA

Les sliders et autres transitions internes peuvent fonctionner sans
navigation classique.

## Ne pas considérer le dispatcher comme une preuve absolue

Le dispatcher peut produire un faux positif ou un faux négatif.

## Ne pas faire de l'IA un oracle unique

L'IA doit interpréter des preuves, pas inventer la vérité.

## Ne pas corriger automatiquement le core à partir d'une simple suspicion

Un `SUSPECTED` doit pouvoir alimenter l'observation sans déclencher
automatiquement une correction.

------------------------------------------------------------------------

# 17. Architecture cible

Architecture conceptuelle minimale :

``` text
                    SURVEY RÉEL
                         ↓
                    SurveyBot
                         ↓
              résultat extraction/action/CTA
                         ↓
              ┌──────────────────────┐
              │ BUG DETECTION LAYER  │
              └──────────┬───────────┘
                         ↓
             preuves / contradictions
                         ↓
             ┌───────────┼───────────┐
             ↓           ↓           ↓
           PASS      SUSPECTED   BUG_CONFIRMED
                         │           │
                         │           ↓
                         │      failure case
                         │           ↓
                         │       AutoFix
                         │
                         ↓
                  observation /
                  revue éventuelle
```

Le Bug Detection Layer reste donc **en amont** de l'AutoFix.

------------------------------------------------------------------------

# 18. Relation avec le pipeline AutoFix

Le pipeline complet visé devient :

``` text
Survey
  ↓
SurveyBot
  ↓
Bug Detection Layer
  ↓
BUG_CONFIRMED
  ↓
Failure Case
  ↓
Replay
  ↓
Diagnostic
  ↓
Sélection du contexte
  ↓
Prompt
  ↓
Agent de coding
  ↓
Patch isolé
  ↓
Validation
  ↓
Score de confiance
  ↓
Revue
  ↓
Merge
```

Le Bug Detection Layer ne remplace donc aucune phase de l'AutoFix.

Il améliore la qualité de son entrée.

------------------------------------------------------------------------

# 19. Critère de réussite du chantier

Le chantier ne sera pas considéré comme réussi simplement parce qu'un
nouveau validator détecte quelques bugs.

Il faudra démontrer progressivement :

``` text
1. vrais bugs détectés ;
2. faux positifs maîtrisés ;
3. faux négatifs identifiés et réduits ;
4. comportements normaux complexes non classés à tort comme bugs ;
5. CTA classiques correctement observés ;
6. transitions internes / sliders correctement observés ;
7. transformations historiques d'extraction non confondues avec des pertes d'information ;
8. résultats du détecteur mesurables sur un corpus de cas réels.
```

Le critère le plus important reste :

> **réduire le nombre de bugs réels qui traversent le système sans aucun
> signal.**

------------------------------------------------------------------------

# 20. Roadmap proposée

## BDL-0 --- Cartographie

**Statut : À FAIRE**

Inventorier les signaux déjà disponibles dans :

-   `question_block_validator.py`
-   `action_validator.py`
-   `failure_recorder.py`
-   `page_snapshot.py`
-   `browser_capsule.py`
-   `action_dispatcher.py`
-   `cta_handler.py`
-   `dom_analyzer.py`
-   générateur de prompts et représentation des `question_blocks`.

Objectif : ne pas reconstruire ce qui existe déjà.

------------------------------------------------------------------------

## BDL-1 --- Taxonomie minimale

**Statut : À FAIRE**

Définir une taxonomie courte et stable couvrant :

``` text
EXTRACTION
ACTION
CTA
```

avec un nombre volontairement limité de catégories.

------------------------------------------------------------------------

## BDL-2 --- Oracle d'extraction

**Statut : À FAIRE**

Ajouter des signaux indépendants permettant de détecter les
contradictions fortes entre :

``` text
DOM observable
vs
représentation SurveyBot
```

sans transformer l'oracle en second extracteur.

------------------------------------------------------------------------

## BDL-3 --- Oracle d'action

**Statut : À FAIRE**

Exploiter les états avant / après action et les signaux DOM déjà
capturés.

Objectif particulier :

``` text
dispatcher failure
≠
état attendu absent
```

et identifier les cas où le dispatcher annonce un succès non confirmé.

------------------------------------------------------------------------

## BDL-4 --- Oracle CTA

**Statut : À FAIRE**

Intégrer explicitement :

-   plusieurs modèles de CTA ;
-   CTA texte ;
-   CTA icône ;
-   CTA structurels ;
-   iframes ;
-   navigation classique ;
-   transitions internes ;
-   sliders ;
-   `CTA_INTERCEPT_ONLY`.

Le détecteur doit chercher la transition attendue plutôt que supposer
qu'une navigation URL est obligatoire.

------------------------------------------------------------------------

## BDL-5 --- Corpus d'évaluation

**Statut : À FAIRE**

Constituer un corpus de cas réels comprenant :

``` text
TP
FN
FP
TN
```

pour les trois familles.

------------------------------------------------------------------------

## BDL-6 --- Mesure de qualité

**Statut : À FAIRE**

Mesurer progressivement :

``` text
precision
recall
false positive rate
false negative rate
```

avec une attention particulière au taux de faux négatifs.

------------------------------------------------------------------------

## BDL-7 --- Échantillonnage des PASS

**Statut : À FAIRE**

Mettre en place une vérification humaine limitée sur un échantillon de
`PASS` afin de rechercher les bugs silencieux.

Objectif :

``` text
ne plus dépendre uniquement des bugs que l'humain a déjà vus.
```

------------------------------------------------------------------------

## BDL-8 --- IA d'interprétation éventuelle

**Statut : OPTIONNEL / APRÈS VALIDATION DU SOCLE**

N'envisager une IA spécialisée que si les signaux déterministes laissent
un volume significatif de cas ambigus.

L'IA doit recevoir des preuves structurées et produire une
interprétation, pas devenir l'oracle unique.

------------------------------------------------------------------------

# 21. Philosophie de développement

Le Bug Detection Layer doit respecter les mêmes principes que le reste
du projet :

-   DOM-first ;
-   comportement prédictible ;
-   peu de branches ;
-   peu de fallbacks ;
-   pas de logique spécifique ajoutée pour un seul site sans
    justification DOM ;
-   budgets bornés pour toute boucle ;
-   aucun contrôle visuel obligatoire dans le flux normal ;
-   observation passive lorsque possible ;
-   aucune modification du comportement du bot simplement pour améliorer
    la détection ;
-   priorité à une couverture stable de 80--90 % plutôt qu'à une
    tentative de couverture universelle ;
-   ne pas confondre complexité supplémentaire et amélioration réelle de
    la fiabilité.

------------------------------------------------------------------------

# 22. Point de vigilance majeur

Le plus grand risque de ce chantier serait de construire un détecteur
tellement complexe qu'il reproduise progressivement toute la logique de
SurveyBot.

La cible n'est pas :

``` text
deuxième SurveyBot
```

mais :

``` text
mécanisme indépendant de détection des incohérences
```

La question à poser pour chaque nouvelle règle est :

> **Cette règle apporte-t-elle une preuve indépendante ou
> augmente-t-elle seulement la complexité ?**

Si elle augmente la complexité sans améliorer clairement la
prédictibilité ou la capacité de détection, elle ne doit pas être
ajoutée.

------------------------------------------------------------------------

# 23. État de départ

À la création de ce document :

``` text
Bug Detection Layer       CONCEPTION
Extraction oracle         À CONCEVOIR
Action oracle             À CONCEVOIR / s'appuyer sur l'observabilité existante
CTA oracle                À CONCEVOIR
CTA slider handling       À intégrer explicitement
Corpus de référence       À constituer progressivement
Mesure FN/FP              À mettre en place
Échantillonnage PASS      À concevoir
IA d'interprétation       NON NÉCESSAIRE À CE STADE
Intégration AutoFix       CONCEPTUELLEMENT DÉFINIE
```

La Phase 1B existante reste utile et constitue le socle expérimental de
ce chantier. Elle ne doit cependant plus être considérée comme limitée
aux seuls bugs d'extraction et d'action.

------------------------------------------------------------------------

# 24. Décision actuelle

La décision de conception issue des discussions est :

> **Avant de chercher à rendre l'AutoFix plus autonome, fiabiliser le
> mécanisme qui décide qu'un bug existe réellement.**

Le Bug Detection Layer doit donc devenir une couche distincte, placée
entre l'exécution de SurveyBot et la création d'un failure case.

Son rôle n'est pas de corriger.

Son rôle n'est pas de réimplémenter les extracteurs.

Son rôle n'est pas de remplacer `cta_handler.py`.

Son rôle est de fournir une observation indépendante et mesurable
permettant de déterminer :

``` text
PASS
SUSPECTED
BUG_CONFIRMED
```

pour :

``` text
EXTRACTION
ACTION
CTA
```

et d'améliorer progressivement sa propre fiabilité à partir des cas
réels rencontrés en production/test.
