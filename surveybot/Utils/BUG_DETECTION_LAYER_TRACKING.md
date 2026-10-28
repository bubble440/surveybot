# BUG DETECTION LAYER --- SUIVI DU CHANTIER

**Projet :** SurveyBot\
**Statut :** CONCEPTION --- chantier non implémenté (feuille de route révisée deux fois le 2026-10-11, voir §25 ; BDL-0 terminée)\
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

Cette décision séparée est désormais cadrée : voir §20 ter (retrait des
règles parasites, phase BDL-9) et §20 bis (contrat de représentation
attendue).

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

# 20. Roadmap (révisée)

Principe d'ordonnancement : on mesure avant de construire (corpus avant
oracles), on commence par la famille la plus déterministe (ACTION), et on
enchaîne sur l'extraction, et termine par le CTA, qui n'a aujourd'hui
aucun chemin de capture (voir §20 quinquies).

``` text
BDL-0 (FAIT) → BDL-1 → BDL-2 → BDL-3 (ACTION) → BDL-4 (EXTRACTION)
             → BDL-5 (CTA : observabilité v1) → BDL-6 → BDL-7
             → BDL-8 (optionnel) → BDL-9 (retrait des règles)
```

## BDL-0 --- Cartographie + taxonomie minimale

**Statut : FAIT (2026-10-11)**

Livrables produits en lecture seule : un inventaire des signaux et des
règles, puis un livrable de vérifications préalables et de recensement du
corpus. Constats principaux consignés en §20 quinquies.

Fusion des anciennes BDL-0 (cartographie) et BDL-1 (taxonomie) : la
taxonomie découle directement de l'inventaire des signaux existants.

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

Livrable (un fichier Markdown d'inventaire, aucune modification du code) :

1.  tableau des signaux existants (producteur, ce qu'il prouve réellement,
    famille, persisté ou non dans les artefacts d'un failure case) ;
2.  artefacts disponibles / manquants en entrée d'un oracle, par famille ;
3.  signaux trompeurs comme preuve (ex. succès/échec du dispatcher) ;
4.  taxonomie minimale et stable par famille (EXTRACTION / ACTION / CTA) ;
5.  lacunes d'observabilité priorisées : ACTION, puis CTA, puis EXTRACTION ;
6.  **inventaire des règles qui altèrent volontairement l'extraction ou
    l'action** (normalisation, ignorance, regroupement, réécriture,
    filtrage, dont la règle « Autre »), recensées sans jugement :
    fichier/fonction, transformation, finalité (documentée vs déduite),
    sources/DOM déclencheurs, observabilité dans un failure case.

Objectif : ne pas reconstruire ce qui existe déjà, et rendre BDL-9
possible.

------------------------------------------------------------------------

## BDL-1 --- Contrat de représentation attendue

**Statut : PARTIEL --- contrat v0.5 rédigé
(`Utils/BDL1_CONTRAT_REPRESENTATION_ATTENDUE_v0.5.md`) ; inventaire runtime
à exécuter sur le corpus**

Rédiger une courte spécification, par `itype`, de la représentation
SurveyBot attendue (voir §20 bis). Livrable hors du core, versionné,
écrit une seule fois.

------------------------------------------------------------------------

## BDL-2 --- Corpus d'évaluation et mode collecte

**Statut : 2a FAIT (2026-10-11) ; 2b IMPLÉMENTÉ (2026-10-28), famille ACTION
uniquement ; validation par un run local À FAIRE**

Le corpus passe AVANT les oracles. État réel constaté par l'indexation
(2a) : 38 cas répartis sur cinq emplacements non unifiés. Le dossier
`snapshots/`, hors dépôt Git, est le sur-ensemble strict des autres ; 24
cas n'existent que là (jamais promus, diagnostiqués ni classés). La
promotion vers `failure_cases/` est un geste manuel, cas par cas, sans
critère trouvé dans le code ; aucune politique automatique de rétention.

Plusieurs cas sont des captures quasi simultanées de la même page dans la
même session (vérifié par comparaison de contenu pour deux paires). Le
regroupement par provider et cible ramène les 38 cas à environ 21
situations indépendantes (estimation, non vérifiée au niveau DOM).

La vérité terrain n'est documentée que pour 4 cas (ACTION, une même
question MetrixLab). Aucun cas CTA, aucun cas où le validateur a conclu à
tort à un succès.

Conséquence : le corpus actuel est une **suite de non-régression**, pas un
benchmark. Aucun taux de precision ou de recall n'est calculable avec lui.
Le corpus de pages saines évoqué par le plan (phase 11-B) n'existe qu'à
l'état d'une copie manuelle d'un seul cas ; le mode collecte (2b) est ce
qui le constituera.

Deux volets :

-   **2a --- Index unique du corpus** : un seul fichier d'index (famille,
    provider, cible, types d'échec, verdict replay, vérité terrain
    documentée uniquement, doublons). Aucun cas déplacé ni supprimé.
-   **2b --- Mode collecte** : un flag d'environnement, activé dans les
    runs de test, qui persiste les faits runtime avant/après pour TOUS
    les plans d'actions (y compris les `PASS`). Il alimente le corpus en
    TN et en faux négatifs potentiels sans coût en production.

    *Implémentation (2026-10-28, famille ACTION uniquement)* : flag
    `SURVEY_BDL_COLLECT` (désactivé par défaut, distinct de
    `SURVEY_OBSERVABILITY` qu'il suppose actif). Pour un plan jugé `PASS`,
    `meta.json`, `pass_report.json` et `action_trace.json` (faits runtime
    avant/après et mutations) sont écrits dans une racine distincte de
    `snapshots/` (`SURVEY_BDL_COLLECT_DIR`, défaut `./bdl_collect`), jamais
    de DOM complet ni de screenshot. Plafond de 200 cas par racine
    (`SURVEY_BDL_COLLECT_MAX`). Un `PASS` sans faits « après »
    exploitables n'est pas écrit. Le chemin d'échec existant est inchangé.
    Limites : les CTA ne passent pas par ce hook (BDL-5) ; l'extraction
    n'est pas couverte ; le surcoût de capture sur un run réel n'est pas
    mesuré ; aucune annotation des `PASS` collectés n'est prévue à ce stade
    (voir §25, entrée du 2026-10-28).

Le corpus doit aussi couvrir les sources touchées par des règles
historiques (préparation de BDL-9).

------------------------------------------------------------------------

## BDL-3 --- Oracle d'action

**Statut : À FAIRE (décisions validées, voir §25)**

Première famille implémentée : la plus déterministe, avec une preuve
indépendante déjà disponible (faits runtime avant/après, DOM post-action).

Prérequis d'observabilité (ajouts purement additifs, sans changer le type
de retour du dispatcher ni le comportement du bot) :

-   un statut par action individuelle d'un plan (aujourd'hui seul
    l'agrégat `dispatcher_success`, un OR sur tout le plan, survit) ;
-   la valeur réellement utilisée pour chaque action, après toute
    substitution (voir §20 quinquies, point 1) ;
-   les faits runtime « après » disponibles pour l'oracle sans dépendre
    d'un échec préalable du validateur.

Ces ajouts touchent `action_dispatcher.py` : ils nécessitent une
validation explicite et un diff minimal (décision 3 du 2026-10-11,
validée).

Exploiter les états avant / après action :

``` text
dispatcher failure
≠
état attendu absent
```

et identifier les cas où le dispatcher annonce un succès non confirmé.
Le cas IFOP zip2city sert de premier cas de référence.

------------------------------------------------------------------------

## BDL-4 --- Oracle d'extraction

**Statut : À FAIRE**

Deuxième famille. Plus exposée aux faux positifs que l'action (règles
historiques de représentation), donc jugée par rapport au contrat de
BDL-1. Ajouter des signaux indépendants pour détecter les contradictions
FORTES entre DOM observable et représentation SurveyBot, sans transformer
l'oracle en second extracteur.

Prérequis : trace passive des déclenchements de règles (identifiant de la
règle, effet, blocs concernés), afin de distinguer « absent » de
« absorbé par la règle X ». Les validateurs d'extraction existants et
leurs artefacts constituent déjà une base.

------------------------------------------------------------------------

## BDL-5 --- CTA : observabilité v1

**Statut : À FAIRE (périmètre v1 réduit, décision 2 du 2026-10-11)**

Il n'existe aujourd'hui aucun chemin de capture pour un échec CTA : ni
stage dédié, ni hook d'observation (voir §20 quinquies, point 3). Un oracle
CTA suppose donc d'abord une infrastructure.

Périmètre v1 : un résultat CTA structuré, conservé en mémoire, sans
oracle ni failure case tant qu'un hook d'observation n'existe pas.
Contenu à couvrir : plusieurs modèles de CTA, CTA texte / icône /
structurels, iframes, navigation classique, transitions internes,
sliders, `CTA_INTERCEPT_ONLY`.

Règle cible de l'oracle (liste fermée de signaux, voir §20 quater) :
l'absence de tous les signaux produit `SUSPECTED`, jamais
`BUG_CONFIRMED`, sauf CTA introuvable alors qu'un bouton de type CTA est
visible. En mode `CTA_INTERCEPT_ONLY`, l'absence de navigation donne
`PASS`.

Point à neutraliser avant de bâtir dessus : `_did_progress` retourne
`True` sans aucun signal réel quand les marqueurs avant et après sont
tous deux vides (faux positif construit par conception).

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

avec une attention particulière au taux de faux négatifs. Les seuils
atteints conditionnent le lancement de BDL-9.

------------------------------------------------------------------------

## BDL-7 --- Échantillonnage des PASS

**Statut : À FAIRE**

Vérification humaine limitée sur un échantillon de `PASS` pour rechercher
les bugs silencieux.

``` text
ne plus dépendre uniquement des bugs que l'humain a déjà vus.
```

------------------------------------------------------------------------

## BDL-8 --- IA d'interprétation éventuelle

**Statut : OPTIONNEL / APRÈS VALIDATION DU SOCLE**

N'envisager une IA spécialisée que si les signaux déterministes laissent
un volume significatif de cas ambigus. L'IA reçoit des preuves
structurées et produit une interprétation, elle ne devient pas l'oracle
unique.

------------------------------------------------------------------------

## BDL-9 --- Retrait des règles parasites

**Statut : LONG TERME / APRÈS BDL-2 et BDL-6**

Prérequis : corpus (BDL-2), mesure de qualité (BDL-6) et contrat de
représentation attendue (BDL-1) en place.

Un retrait = une règle, une preuve par replay avec / sans, une validation
explicite. Procédure détaillée en §20 ter.

------------------------------------------------------------------------

# 20 bis. Contrat de représentation attendue

Pour juger qu'une règle historique est « parasite », ou qu'une
représentation est « incorrecte », il faut savoir ce que l'extraction
DEVRAIT produire. Tant que ce choix reste implicite et enfoui dans le
code, l'oracle ne peut pas être indépendant : il risquerait de tolérer des
écarts uniquement parce que des règles les produisent (circularité).

Décision : rédiger une courte spécification, par `itype`, de la
représentation attendue (ex. pour « Autre » : l'option et son champ texte
associé sont-ils exposés ou absorbés ?).

Propriétés du contrat :

-   court, stable, versionné ;
-   situé hors du core (il ne dépend pas de l'implémentation) ;
-   source de vérité pour l'oracle d'extraction (BDL-4) et pour le
    classement des règles historiques (BDL-9) ;
-   toute décision de produit (ce que le modèle de réponse doit voir)
    y est explicite, et non cachée dans une règle.

Classement des règles historiques par rapport au contrat :

``` text
conforme au contrat                     → conservée
contournement d'un ancien bug           → candidate au retrait
contradiction avec le contrat           → retrait ou décision produit
incertaine                              → conservée, à réévaluer
```

------------------------------------------------------------------------

# 20 ter. Retrait des règles parasites (cap long terme)

Cap : à moyen ou long terme, le Bug Detection Layer doit permettre de
retirer en sécurité les règles historiques qui altèrent l'extraction ou
l'action (règle « Autre » et autres) lorsqu'elles sont obsolètes ou
parasites.

Précision de rôle : le BDL ne remplace pas ces règles, il rend leur
suppression évaluable. Il détecte, il ne produit rien. Une règle de
normalisation volontaire (éviter de polluer le prompt du modèle de
réponse) n'est pas un bug.

Conflit à gérer avec la règle projet « zéro modification d'extracteur
existant » : supprimer une règle est une modification d'un existant. Le
retrait suit donc une procédure d'exception explicite et rare.

Procédure :

``` text
1. Inventaire des règles qui altèrent extraction/action (BDL-0, point 6)
2. Classement par rapport au contrat de représentation (§20 bis)
3. Couverture corpus : pas de cas réels sur les sources concernées
   → pas de retrait
4. Replay hors ligne du corpus AVEC et SANS la règle, comparaison des
   verdicts de l'oracle
5. Retrait uniquement si aucune régression ; une règle par patch ;
   diff minimal ; validation explicite avant patch
6. Adoption humaine du retrait, puis mise à jour de la mémoire des
   extracteurs et de la baseline d'intégrité (l'agent qui écrit le patch
   ne met jamais lui-même la baseline à jour)
```

Un retrait de fonction protégée apparaît comme un changement attendu
pour le contrôle d'intégrité : la décision d'adoption précède toute
actualisation de la baseline.

Ordre de retrait : d'abord les règles à faible risque et bien couvertes
par le corpus. La règle « Autre » viendra probablement tard car elle
touche beaucoup de sources.

------------------------------------------------------------------------

# 20 quater. Décisions d'architecture complémentaires

## Oracle = fonction pure sur artefacts capturés

L'oracle prend en entrée des artefacts déjà capturés (DOM avant/après,
résultat du bot, état du CTA) et renvoie un verdict avec ses preuves. Il
n'appelle jamais le navigateur. Conséquences :

-   rejouable hors ligne sur le corpus, donc mesurable ;
-   observation passive garantie ;
-   aucun coût navigateur supplémentaire dans le flux normal.

Forme de sortie conceptuelle : `famille, niveau, preuves[], règle`.

## Devenir d'un SUSPECTED

Pour la v1 : aucune promotion automatique. Les `SUSPECTED` sont
journalisés et relus lors de l'échantillonnage (BDL-7). Une promotion par
récurrence ajouterait des branches avant d'avoir des données. Un
`SUSPECTED` ne déclenche jamais d'AutoFix.

## CTA : signaux de changement en liste fermée (v1)

Signaux : URL, document, empreinte du conteneur principal, indicateur de
page. Absence de tous les signaux → `SUSPECTED`. `BUG_CONFIRMED`
uniquement si le CTA est introuvable alors qu'un bouton de type CTA est
visible. `CTA_INTERCEPT_ONLY` actif → l'absence de navigation donne
`PASS`.

------------------------------------------------------------------------

# 20 quinquies. Constats de la phase BDL-0 (2026-10-11)

## 1. Substitution silencieuse de la valeur de réponse

`_sanitize_instruction_with_page_context` (`action_dispatcher.py`) peut
remplacer la valeur de réponse elle-même avant la recherche de l'élément,
dans le chemin de repli historique (radio, checkbox, dropdown). La valeur
substituée reste locale à l'appel : seule la valeur demandée est
persistée dans les artefacts.

Conséquences pour le BDL :

-   un oracle d'action doit comparer l'état observé à la valeur
    RÉELLEMENT utilisée, sinon il produit de faux signaux ;
-   c'est une règle de politique de réponse, pas d'extraction : elle ne
    relève ni de BDL-9 ni du contrat de représentation ; le BDL se
    contente de l'observer ;
-   deux coquilles dans ses listes de mots (`prfère`, `rgulièrement`)
    empêchent ces entrées de correspondre : bug latent à traiter
    séparément, hors BDL.

## 2. Code non intégré

`question_block_analyzer.py` n'est importé ni appelé nulle part dans le
dépôt (code, tests, outils). Une seule forme de bloc circule dans le
pipeline réel : le `dict` issu de `analyze_dom`. Le BDL ne doit pas
s'appuyer sur ce module. Sa suppression éventuelle est une décision
indépendante.

## 3. Absence de chemin de capture CTA

Les clics CTA sont appelés depuis `survey_executor.py` et depuis des
handlers d'écran, en dehors de `execute_actions_plan`. Les deux seuls
observateurs de la phase 1A (extraction et action) ne les voient donc
jamais. Aucun stage `cta` n'existe dans le vocabulaire des failure cases.

## 4. Artefacts conditionnés à l'échec

Les artefacts avant/après ne sont écrits que si le validateur a déjà
conclu à un échec. Un oracle placé après le validateur ne peut donc pas
voir les bugs que celui-ci a validés à tort. Le corpus actuel est lui
aussi biaisé : il ne contient que des cas déjà signalés.

## 5. Coût de la capture

Déjà payé à chaque exécution de plan : environ 42 appels navigateur (une
capture DOM, jusqu'à 40 `evaluate()` de faits runtime non batchés, un
observateur de mutations). La partie lourde ne doit rester réservée qu'aux
échecs : jusqu'à environ 12 MiB, environ 180 appels CDP, jusqu'à 6 s
d'attente bloquante. Pour l'oracle d'action, les faits runtime avant/après
suffisent. Rendre la capture « après » inconditionnelle ajoute environ 40
appels ; le batch de ces appels est une optimisation séparée.

## 6. Règles historiques (rappel pour BDL-9)

-   La règle « Autre » est en réalité une famille d'au moins quatre
    règles (détection inline, élagage FocusVision, élagage générique
    trailing, opt-out companion).
-   La plupart des règles d'extraction inventoriées sont des nettoyages de
    sur-génération des extracteurs, documentés et gardés par un DOM précis.
-   Leur effet est généralement absent des artefacts persistés : une trace
    passive des déclenchements est nécessaire avant tout retrait.
-   Le seuil `score>=3` de l'heuristique de champ générique est documenté
    (docstring).

## 7. Points restés non vérifiés

-   Deux diagnostics existent sans dossier `failure_cases/` d'origine :
    soit l'outil de diagnostic accepte un dossier `snapshots/` brut, soit
    un dossier a été supprimé à la main. Non tranché.
-   Critère de sélection des 10 cas promus sur 38 : aucun critère trouvé
    dans le code, vraisemblablement une décision de l'opérateur.
-   Les groupes de cas de même cible n'ont pas été comparés au niveau DOM :
    captures quasi simultanées ou occurrences indépendantes, non tranché.
-   Aucune frame capturée sur les 38 cas : « aucune iframe » ou « iframes
    sous le seuil de rétention », non distingué.
-   Absence des artefacts `external_*` sur la majorité des cas : cause non
    vérifiée (aucune ressource, budget, erreur).
-   Vérité terrain de 34 cas sur 38.
-   Fréquence d'appel d'`execute_actions_plan` par parcours, latence réelle
    des appels, fréquence d'atteinte du plafond d'attente de 6 s.
-   Calcul exact de `target_shapes` et `requested_option_dom_facts` dans
    le module de replay (non ouvert).

## 8. Tâches distinctes du BDL

-   Correction des coquilles de `_sanitize_instruction_with_page_context`
    (prompt de correction séparé).
-   Décision sur le sort de `question_block_analyzer.py`.
-   Avant de committer l'index du corpus dans un dépôt public : retirer de
    ce livrable le jeton de session et l'identifiant de répondant cités
    dans sa comparaison des deux cas Qualtrics.
-   Les dossiers `snapshots/` bruts contiennent des URLs avec jetons de
    session : ne pas les copier tels quels dans le dépôt.

## 9. Corpus réel et mesures de taille (indexation BDL-2a)

Mesures sur les 38 cas de `snapshots/` :

``` text
capture d'écran      médiane ~35 Ko   maximum ~136 Ko
DOM HTML             médiane ~33 Ko   maximum ~274 Ko
MHTML (8 cas)        médiane ~405 Ko  maximum ~546 Ko
external_scripts     médiane ~731 Ko  maximum ~952 Ko (12 cas)
external_stylesheets maximum ~162 Ko  (11 cas)
external_requests    maximum ~139 Ko  (9 cas)
frames capturées     0 sur 38
```

Aucun cas n'atteint le plafond théorique de 4 Mio de scripts. Les
scripts externes dominent le poids des snapshots, mais un oracle d'action
n'en a pas besoin : faits runtime et DOM post-action suffisent.

## 10. Règles du document d'architecture du cœur protégé

Le document `Utils/SURVEYBOT_PROTECTED_CORE_ARCHITECTURE.md` existe et
définit la séparation des responsabilités. Règles pertinentes pour le
BDL :

-   le bot de production détecte et enregistre ; diagnostic, patch,
    replay, validation et consolidation relèvent du worker local/dev ;
-   production sans retry ni auto-correction passive ;
-   les validators vérifient les incohérences fortes sans devenir un
    second analyseur DOM (cohérent avec le principe d'indépendance de
    l'oracle) ;
-   quatre niveaux de preuve non interchangeables pour attribuer une
    fonction à un incident : `involved`, `suspected`,
    `root_cause_confirmed`, `patched` ;
-   l'agent qui écrit un patch ne met jamais à jour la baseline
    d'intégrité ; la décision d'adoption précède toute actualisation.

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
Bug Detection Layer       CONCEPTION (BDL-0 terminée)
Extraction oracle         À CONCEVOIR (BDL-4, après l'action)
Action oracle             À CONCEVOIR (BDL-3, prérequis d'observabilité validés)
CTA oracle                APRÈS OBSERVABILITÉ v1 (BDL-5), aucun chemin de capture
CTA slider handling       À intégrer explicitement
Corpus de référence       14 cas indexables, suite de non-régression (BDL-2)
Mesure FN/FP              À mettre en place
Échantillonnage PASS      À concevoir
IA d'interprétation       NON NÉCESSAIRE À CE STADE
Intégration AutoFix       CONCEPTUELLEMENT DÉFINIE
Contrat de représentation À RÉDIGER (BDL-1)
Retrait règles parasites  CAP LONG TERME (BDL-9)
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

------------------------------------------------------------------------

# 25. Journal des décisions

## 2026-10-11 --- Révision de la feuille de route

Décisions issues de la discussion de revue :

1.  Le corpus passe avant les oracles (ancienne BDL-5 devenue BDL-2).
2.  Ordre d'implémentation des oracles : ACTION → CTA → EXTRACTION
    (extraction en dernier : plus fort risque de faux positifs lié aux
    règles historiques).
3.  Fusion de l'ancienne BDL-0 (cartographie) et de l'ancienne BDL-1
    (taxonomie).
4.  Ajout de l'inventaire des règles altérant extraction/action dans
    BDL-0.
5.  Ajout du contrat de représentation attendue (BDL-1) pour éviter la
    circularité de l'oracle.
6.  Ajout de BDL-9 : retrait des règles parasites (cap long terme),
    procédure en §20 ter, avec procédure d'exception à la règle « zéro
    modification d'extracteur existant ».
7.  Oracle défini comme fonction pure sur artefacts capturés.
8.  SUSPECTED : journalisé uniquement en v1, sans promotion automatique.
9.  CTA v1 : liste fermée de signaux de changement.
10. Précision de rôle : le BDL ne remplace pas les règles historiques, il
    rend leur retrait évaluable.

Correspondance de numérotation (ancienne → nouvelle) :

``` text
ancienne BDL-0 + BDL-1  → BDL-0
(nouveau)               → BDL-1 (contrat)
ancienne BDL-5 (corpus) → BDL-2
ancienne BDL-3 (action) → BDL-3
ancienne BDL-4 (CTA)    → BDL-4
ancienne BDL-2 (extr.)  → BDL-5
ancienne BDL-6, 7, 8    → BDL-6, 7, 8
(nouveau)               → BDL-9
```

## 2026-10-11 --- Seconde révision après la phase BDL-0

Décisions validées après lecture de l'inventaire et des vérifications :

1.  Ordre des oracles : ACTION → EXTRACTION → CTA (remplace la décision 2
    de la première révision).
2.  CTA en v1 : observabilité uniquement (résultat CTA structuré en
    mémoire), sans oracle ni failure case tant qu'aucun hook n'existe.
3.  Enregistrer la valeur réellement utilisée et un statut par action,
    par ajout purement additif dans la trace existante (touche
    `action_dispatcher.py` : diff minimal, validation explicite).
4.  Mode collecte par flag d'environnement pour récolter des cas `PASS`
    en test ; en production, seuls les faits runtime avant/après sont
    capturés.
5.  Le corpus actuel est une suite de non-régression ; les taux de
    precision et de recall attendent les données du mode collecte.

Correspondance de numérotation par rapport à la première révision :

``` text
BDL-4 (CTA)         → BDL-5 (CTA : observabilité v1)
BDL-5 (extraction)  → BDL-4 (extraction)
BDL-2 (corpus)      → BDL-2 (index + mode collecte)
```

## 2026-10-11 --- Indexation du corpus (BDL-2a) et correction d'une erreur

1.  BDL-2a terminée : 38 cas sur cinq emplacements, `snapshots/` hors
    dépôt est le sur-ensemble ; environ 21 situations indépendantes ;
    vérité terrain connue pour 4 cas seulement (voir §20 quinquies,
    point 9).
2.  Les deux cas Qualtrics du 23 septembre sont deux captures de la même
    session et de la même page, pas deux occurrences indépendantes.
3.  Le document d'architecture du cœur protégé existe sous un autre nom
    (`Utils/SURVEYBOT_PROTECTED_CORE_ARCHITECTURE.md`). La référence à un
    fichier nommé autrement venait d'un prompt de travail, pas du
    template de prompt du projet : la mention antérieure d'une correction
    de template était erronée et a été retirée du §20 quinquies.
4.  Le retrait de règles (BDL-9) est soumis à la règle de la baseline
    d'intégrité : adoption humaine avant toute mise à jour.

## 2026-10-28 --- BDL-2b : mode collecte des plans d'actions PASS

1.  Mode collecte implémenté pour la famille ACTION (hook d'action de
    `page_snapshot.py`, enregistrement dans `failure_recorder.py`). Voir
    §20, BDL-2, volet 2b, pour le détail du fonctionnement.
2.  Décisions de conception : flag distinct de `SURVEY_OBSERVABILITY` ;
    racine de collecte distincte de `snapshots/` ; rapport collecté nommé
    `pass_report.json` (et non `validation_report.json`), pour qu'un
    dossier collecté présenté par erreur au `failure_case_builder` soit
    vu comme un case incomplet et non comme un échec réel ; pas de DOM ni
    de screenshot sur un `PASS` (coût, §20 quinquies point 5) ; plafond
    par racine incluant les dossiers déjà présents.
3.  Vérifications : 14 tests synthétiques ajoutés
    (`tests/test_bdl_collect_action.py`) ; ils échouent sans le patch.
    Suite complète : 225 tests avant, 239 après ; 14 échecs identiques
    avant et après, présents sur la branche sans le patch (vérifié sur une
    copie propre). Parmi eux, 2 (`test_static_validator_activation`)
    s'expliquent par l'absence de `pytest` dans l'environnement de test
    et disparaissent une fois installé ; 12
    (`test_action_validator_qt_native_checked_exclusion`) subsistent, de
    cause non déterminée et sans lien établi avec 2b (même résultat sous
    Python 3.11, version du Dockerfile). Registre d'intégrité : 58/58.
4.  Restant à faire : mesurer le surcoût d'un run local avec le flag
    actif ; définir une règle d'échantillonnage et d'annotation des `PASS`
    collectés avant que le volume n'augmente (sans vérité terrain, le
    corpus collecté ne permet aucune mesure de précision ou de recall,
    voir BDL-6/BDL-7) ; instruire séparément les 12 échecs préexistants.
5.  Hors périmètre, non modifié : la section 23 (état de départ à la
    création du document) conserve son contenu d'origine.