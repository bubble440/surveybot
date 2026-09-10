Voici le plan complet que je recommande, de **1A jusqu'au système
d'auto-correction mature**. Je le découpe volontairement en étapes
courtes et validables : on ne passe jamais à l'automatisation d'un
niveau tant que le niveau précédent n'est pas fiable.


> Mise à jour 2026-09-07 : Phase 1A clôturée après validation live attach.
> Le snapshot IFOP zip2city `20260907_201456_action_validation_failure`
> devient le premier cas de stabilisation de la Phase 1B.
> Mise à jour 2026-09-07 soir : 1B.1 IFOP zip2city et 1B.2 capture pre-action validés live.
> Mise à jour 2026-09-09 : décision de ne pas bloquer la roadmap sur la collecte complète des cas 1B.
> La Phase 1B reste ouverte en tâche de fond ; le chantier principal passe à la Phase 2.
> Mise à jour 2026-09-09 (suite) : Phase 2 clôturée. `Survey/failure_case_builder.py` +
> `tools/create_failure_case.py` implémentés et validés après 2 correctifs (résolution
> itype scopée au target_id retenu ; sanitisation des attributs href/src des DOM HTML
> copiés, en plus du nettoyage déjà en place sur meta.json). Le chantier principal passe
> à la Phase 3 (replay local déterministe).
> Mise à jour 2026-09-09 (suite 2) : Phase 3 clôturée. `Survey/dom_replay_shim.py`
> (driver statique lxml, surface Playwright minimale, `evaluate()` limité à deux
> idiomes structurels reconnus, décline honnêtement le reste) + `Survey/failure_replay.py`
> (chargement DOM à stratégie unique par stage, verdicts REPRODUIT/NON_REPRODUIT/
> DIFFERENT/NON_REJOUABLE) + `tools/replay_failure.py`. `lxml`/`cssselect` ajoutés en
> dépendance dev-only (jamais embarqués dans le binaire Nuitka de distribution tiers,
> à revérifier avant la prochaine release). Limite structurelle actée : `child_frames`
> toujours vide (DOM figé post-résolution de frame) — ce replay ne peut pas re-tester
> un bug de sélection de frame elle-même, seulement l'extraction/validation à
> l'intérieur d'une frame déjà correctement choisie. Le chantier principal passe à la
> Phase 4 (diagnostic automatique).

## Contexte de travail actuel

Nous développons le module d'observabilité, de diagnostic et d'auto-correction supervisée de SurveyBot.

Le travail en cours ne consiste pas à renforcer directement tous les extracteurs
ou sélecteurs un par un. L'objectif est de construire une boucle outillée :

``` text
incident détecté
→ snapshot normalisé
→ failure case
→ replay local
→ diagnostic
→ sélection du contexte code
→ prompt Codex
→ patch isolé
→ validation
→ score de confiance
```

État actuel :

``` text
1A  terminée
1B  ouverte en tâche de fond
2   terminée
3   terminée
4   prochain chantier principal
```

Décision importante :

``` text
la collecte complète des cas 1B ne doit pas bloquer la progression vers la Phase 2
```

Raison :

``` text
les surveys problématiques n'apparaissent pas sur commande
attendre 15 à 20 cas réels avant de continuer figerait le développement
```

Donc la Phase 1B continue en parallèle. Pendant les tests des Phases 2 et
suivantes, tout nouveau cas utile pour les validators doit être capturé,
classé, puis traité par un patch 1B.x ciblé.

Rappel opérationnel :

``` text
penser régulièrement à améliorer 1B dès qu'un nouveau faux positif
ou faux négatif clair apparaît pendant les tests
```


## Phase 1A --- Observabilité passive

**Statut : TERMINÉE — implémentée sur la branche `feature/phase-1a-observability` et validée en live attach.**

Objectif : détecter les échecs que tu repères aujourd'hui visuellement,
**sans modifier le comportement du bot**.

### Implémentation réalisée

La Phase 1A est câblée autour du pipeline existant avec trois modules
dédiés :

``` text
Survey/
    question_block_validator.py
    action_validator.py
    failure_recorder.py
```

Le système réutilise `page_snapshot.py` au lieu de créer un second
mécanisme de capture.

### Extraction

Le flux actuel est :

``` text
dom_analyzer.analyze_dom()
        ↓
QuestionBlockValidator
        ↓
PASS → flux normal inchangé
FAIL → FailureRecorder
        ↓
snapshot + validation_report.json
        ↓
flux normal inchangé
```

Le validator reste volontairement conservateur et cherche uniquement des
incohérences fortes, notamment :

``` text
target_id absent du DOM_REGISTRY
question vide/non exploitable
radio/checkbox sans options
options dupliquées
min_select > max_select
max_select impossible
kind/context incompatible avec registry
locator principal impossible à résoudre
```

Il ne tente pas de réinterpréter tout le DOM et ne constitue donc pas un
second `dom_analyzer`.

### Sélection / insertion

Le flux actuel est :

``` text
action_dispatcher.execute_actions_plan()
        ↓
ActionValidator
        ↓
PASS → flux normal inchangé
FAIL → FailureRecorder
        ↓
snapshot + validation_report.json
        + actions_requested.json
        ↓
flux normal inchangé
```

L'objectif est de comparer les actions demandées avec les incohérences
objectivement vérifiables dans le DOM/registry, sans introduire de
nouvelle stratégie d'insertion.

Exemple de mismatch visé :

``` text
demandé :
Nike
Adidas

observé :
Nike
Puma

=> selection_mismatch
missing = Adidas
unexpected = Puma
```

### En cas d'erreur

Le `FailureRecorder` enrichit le système de snapshot existant. Un
incident peut produire :

``` text
snapshot/
    dom_outer.html
    dom_body.html
    page_source.html
    question_blocks.json
    viewport.png
    meta.json
    frames/
    actions_requested.json
    validation_report.json
```

### Règle fondamentale de 1A

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

### Activation

Pour éviter un coût inutile sur le parc réel :

``` text
local / attach → observabilité activée par défaut
prod           → observabilité désactivée par défaut
```

Activation explicite possible :

``` text
SURVEY_OBSERVABILITY=1
```

### État Git au terme de l'implémentation

``` text
branche : feature/phase-1a-observability
base    : playwright-migration
avance  : 5 commits
retard  : 0 commit
```

Les changements concernent les trois modules d'observabilité et leur
intégration au système de snapshot/pipeline existant.

### Validation live réalisée

La Phase 1A a été validée en attach sur deux chemins complémentaires.

1. **Chemin positif CloudResearch Sentry**

   Cas testé : question radio CloudResearch Sentry avec options accentuées,
   notamment `Généralement`.

   Résultat attendu :

   ``` text
   action demandée
   → sélection réellement appliquée
   → dispatcher = succès
   → aucun action_validation_failure
   ```

   Résultat observé :

   ``` text
   [TARGET] apply ok=true strategy=cloudresearch_sentry_selection_signal reason=selected_marker_and_confirmation
   [TARGET] apply ok=true strategy=target_id reason=applied
   ```

   Ce test valide :
   - la correction de normalisation Unicode pour éviter le faux
     `action_value_not_in_registry_options` ;
   - la reconnaissance du signal de sélection CloudResearch Sentry ;
   - l'absence de faux `action_validation_failure` lorsque l'action est
     réellement appliquée.

2. **Chemin failure IFOP zip2city**

   Cas testé : widget IFOP `ifop_zip2city_widget` demandant un code postal
   français, avec action `75001`.

   Résultat observé :

   ``` text
   [TARGET] apply ok=false reason=ifop_zip2city_widget_failed target_id='single_25ab6372513b'
   [OBSERVABILITY] validation_failure stage=action issues=['dispatcher_reported_failure']
   ```

   Le dossier créé contient le format attendu pour un échec d'action :

   ``` text
   frames/
   actions_requested.json
   meta.json
   post_action_dom.html
   post_action_viewport.png
   question_blocks.json
   validation_report.json
   ```

   `meta.json` indique explicitement :

   ``` text
   reason = action_validation_failure
   capture_phase = post_action
   artifacts.dom = post_action_dom.html
   artifacts.screenshot = post_action_viewport.png
   ```

   Ce test valide :
   - la création automatique du failure snapshot ;
   - la conservation de l'action demandée ;
   - la conservation des question_blocks ;
   - la conservation du DOM post-action ;
   - la conservation du screenshot post-action ;
   - la normalisation de l'incident dans `validation_report.json` ;
   - le caractère non bloquant de l'observabilité.

### Décision de clôture 1A

La Phase 1A est considérée **terminée**.

Elle a rempli son objectif : détecter et documenter passivement les anomalies
sans modifier le flux normal du bot.

Le cas IFOP zip2city ne doit pas être corrigé dans 1A. Il devient le premier
cas de travail de la Phase 1B, car il illustre exactement le problème suivant :

``` text
dispatcher_success = false
≠
preuve certaine que l'état attendu est absent du DOM
```

Le DOM post-action et le screenshot montrent que le code postal `75001` et la
ville `Paris 01` sont visibles après tentative, alors que le dispatcher a
retourné `ifop_zip2city_widget_failed`. La stabilisation de cet oracle relève
de 1B.

------------------------------------------------------------------------

# Phase 1B --- Fiabilisation des validators

**Statut : OUVERTE EN TÂCHE DE FOND — 1B.1 et 1B.2 validés.**

Objectif : réduire les faux positifs, réduire les faux négatifs et augmenter
progressivement la couverture des validators.

Décision de roadmap :

``` text
ne pas attendre la collecte complète de tous les cas 1B pour passer à la Phase 2
```

La Phase 1B sera améliorée opportunément au fil des tests. Chaque fois qu'un
nouveau survey révèle un cas clair, on crée un patch 1B.x ciblé, sans interrompre
le chantier principal.

Les quatre familles de cas à conserver sont :

``` text
1. Extraction mauvaise mais aucun extraction_validation_failure
2. Extraction signalée mauvaise alors qu'elle est correcte
3. Sélection/action mauvaise mais aucun action_validation_failure
4. Action signalée mauvaise alors que le DOM final est correct
```

## Cas de départ 1B — IFOP zip2city

Le premier cas de 1B est le snapshot :

``` text
20260907_201456_action_validation_failure
```

Symptôme observé :

``` text
dispatcher_reported_failure
```

Faits conservés par 1A :

``` text
action demandée : 75001
itype : text
target_id : single_25ab6372513b
context.ifop_zip2city_widget : true
post_action_dom.html présent
post_action_viewport.png présent
```

Observation live :

``` text
le widget affiche 75001
le libellé ville affiche Paris 01
le dispatcher retourne pourtant ifop_zip2city_widget_failed
```

Objectif 1B pour ce cas :

``` text
distinguer un vrai échec d'insertion texte
d'un dispatcher_failure contredit par un état DOM post-action fort
```

Règle importante :
 
``` text
ne pas transformer action_validator.py en second action_dispatcher
ne pas ajouter une cascade de fallbacks
ajouter seulement des invariants DOM forts, scopés et vérifiables
```

## Patch 1B.1 — classification IFOP zip2city

**Statut : TERMINÉ — validé live.**

Le validator distingue maintenant le cas :

``` text
dispatcher_success = false
mais état DOM post-action fort observé
```

du vrai échec brut du dispatcher.

Pour IFOP zip2city, lorsque le DOM post-action contient le code postal demandé
et un libellé ville concret, le rapport peut classer l'incident comme :

``` text
dispatcher_false_negative
dom_signal = ifop_zip2city_resolved
```

Exemple validé :

``` text
value = 75001
city_label = Paris 01
```

Le signal dispatcher reste visible dans les snapshots d'observabilité. Le patch
ne transforme pas ce cas en succès silencieux.

## Patch 1B.2 — capture pre-action légère

**Statut : TERMINÉ — validé live.**

Les snapshots `action_validation_failure` contiennent désormais aussi :

``` text
pre_action_dom.html
```

en plus de :

``` text
post_action_dom.html
post_action_viewport.png
validation_report.json
actions_requested.json
question_blocks.json
meta.json
frames/
```

Objectif :

``` text
savoir si l'état observé après action existait déjà avant l'action,
ou si le bot l'a réellement créé pendant la tentative d'insertion
```

Cette capture reste passive :

``` text
aucun clic
aucune navigation
aucun retry
aucune modification du résultat dispatcher
```

On teste les validators sur tes DOM réels existants.

Objectif pratique pour clôturer 1B :

``` text
minimum : 8 à 12 incidents réels utiles
idéal   : 15 à 20 incidents
```

Répartition cible :

``` text
4 à 6 cas action/sélection
3 à 5 cas extraction
2 à 4 cas ambigus/faux positifs validator
```

1B ne cherche pas à couvrir tous les providers. Elle cherche à fiabiliser les
validators sur les familles les plus fréquentes, avec priorité à la précision.

On construit une petite taxonomie :

``` text
EXTRACTION_FAILURE
    missing_block
    missing_options
    polluted_question
    duplicate_block
    wrong_itype
    wrong_selection_limits
    invalid_registry_target

ACTION_FAILURE
    target_not_found
    option_not_found
    selection_not_applied
    wrong_option_selected
    incomplete_multiselect
    unexpected_selection
    field_value_mismatch
```

Important : **pas 50 catégories**.

Une dizaine de catégories fiables vaut mieux qu'une classification
ultra-fine instable.

### Résultat attendu

Chaque incident doit pouvoir être décrit comme :

``` json
{
  "stage": "action",
  "failure_type": "selection_not_applied",
  "qid": "Q2",
  "target_id": "...",
  "expected": ["Adidas"],
  "observed": []
}
```

À la fin de 1B, on possède un véritable **oracle automatique partiel**.

------------------------------------------------------------------------

# Phase 2 --- Création automatique de cas de reproduction

**Statut : TERMINÉE — `Survey/failure_case_builder.py` + `tools/create_failure_case.py`
implémentés et validés (2 correctifs appliqués).**

Objectif : transformer automatiquement chaque snapshot d'incident en cas de
reproduction exploitable par le pipeline de diagnostic et d'auto-fix supervisé.

Avant cette phase, la transformation était manuelle :

``` text
page live
→ snapshot
→ dom_body.html
→ question_blocks.json
→ fichiers concernés
→ prompt Claude/Codex
```

Cette étape a disparu : `tools/create_failure_case.py <snapshot_dir>...` convertit
un ou plusieurs snapshots `snapshots/*_validation_failure/` en cases normalisés,
en lecture seule sur le snapshot source (jamais modifié ni déplacé).

### Structure réelle produite

``` text
failure_cases/
    case_<nom_du_snapshot>/
        manifest.json
        artifacts/
            meta.json                    (sanitisé)
            validation_report.json
            question_blocks.json
            actions_requested.json
            pre_action_dom.html          (sanitisé)
            post_action_dom.html         (sanitisé)
            post_action_viewport.png
            dom_outer.html                (sanitisé, profil historique)
            dom_body.html                 (sanitisé, profil historique)
            page_source.html              (sanitisé, profil historique)
            viewport.png                  (profil historique)
            frames/
                frame_*.dom_outer.html    (sanitisé)
                frame_*.page_source.html  (sanitisé)
```

Seuls les noms de fichiers connus (produits par `page_snapshot.py` /
`failure_recorder.py`) sont copiés — pas de copie générique du dossier snapshot.

### Manifest réel

``` json
{
  "schema_version": "1.0",
  "case_id": "...",
  "created_at": "...",
  "source_snapshot": "...",
  "stage": "action|extraction|unknown",
  "reason": "...",
  "failure_types": ["..."],
  "itype": "...",
  "target_id": "...",
  "frame_chain": ["..."],
  "provider_domain": "...",
  "artifacts": { "meta.json": true, "...": false },
  "incomplete": false,
  "warnings": ["..."]
}
```

`failure_types` est dérivé strictement de `validation_report.json` (pas de
nouvelle taxonomie). `itype`/`target_id` ne sont jamais renseignés de façon
incohérente entre eux : si aucune source rattachée au `target_id` retenu ne
fournit d'`itype`, celui-ci reste `null` plutôt que d'être emprunté à un issue
sans rapport. Aucun champ spéculatif (ex. estimation de « replayability ») n'a
été ajouté à ce stade — ce sera à la Phase 3 de le déterminer par un test réel,
pas par une heuristique déclarée ici.

### Idempotence et secrets

Un case déjà existant fait échouer la commande par défaut (`FailureCaseExistsError`) ;
`--force` régénère, avec garde-fou (refuse de supprimer un dossier qui ne contient
pas de `manifest.json`, pour ne jamais effacer autre chose par erreur).

`meta.json` et l'ensemble des fichiers DOM HTML copiés (`pre_action_dom.html`,
`post_action_dom.html`, `dom_outer.html`, `dom_body.html`, `page_source.html`,
`frames/*.html`) sont nettoyés avant copie : la query string et le fragment de
tout champ/attribut porteur d'URL (`url` en JSON, `href`/`src` en HTML) sont
retirés, car ils peuvent porter un token de session (observé en pratique : JWT
Zappi, WID/XID CloudResearch). Le nettoyage HTML est fait par substitution
ciblée sur la syntaxe d'attribut, jamais par reparsing/reserialization, pour ne
jamais risquer d'altérer la structure DOM dont la Phase 3 (replay) dépendra.
`page.mhtml` / `body_text.txt` / `mhtml_error.txt` sont exclus de ce nettoyage
(risque de corruption d'un format d'archive/encodage) et copiés tels quels.

------------------------------------------------------------------------

# Phase 3 --- Replay local déterministe

**Statut : TERMINÉE — `Survey/dom_replay_shim.py` + `Survey/failure_replay.py` +
`tools/replay_failure.py` implémentés et validés.**

Objectif atteint : reproduire un incident sans dépendre de la page live, en
rejouant sur le HTML figé du failure_case :

``` text
DOM (artifacts/ du case)
→ dom_replay_shim (driver statique, surface Playwright minimale)
→ dom_analyzer.analyze_dom()
→ question_blocks
→ validator concerné (question_block_validator ou action_validator)
```

### Architecture réelle

`Survey/dom_replay_shim.py` expose un `StaticPage`/`StaticElementHandle` en
lecture seule (lxml + cssselect), suffisant pour que `dom_analyzer.py` et les
validators tournent sans modification (injection par le paramètre `driver`
déjà existant — aucune touche à leur code). Aucune méthode d'interaction
(click/fill/type) n'est exposée : ce shim ne couvre que l'extraction/l'analyse,
jamais le dispatch.

`evaluate()` n'exécute aucun JS générique — il ne reconnaît que deux idiomes
structurels observés tels quels dans `dom_analyzer.py` (lecture de `tagName`,
recherche d'ancêtre via `closest()`), calculables sans layout. Tout le reste
(`getComputedStyle`, `getBoundingClientRect`, signaux de widget spécifiques)
décline proprement (`JsEvaluationUnavailable`) plutôt que de deviner un
résultat — le code appelant existant absorbe déjà ce cas comme en prod face à
un `evaluate()` qui échoue. Les compteurs `evaluate_handled`/`evaluate_declined`
sont reportés dans chaque résultat de replay pour garder cette limite visible.

`Survey/failure_replay.py` orchestre : une seule stratégie de chargement DOM
par stage, jamais de cascade essai/erreur sur plusieurs fichiers en espérant
qu'un match :

``` text
stage=action      → post_action_dom.html exclusivement
                     (pre_action_dom.html : comparaison informative seulement,
                     jamais analysé)
stage=extraction  → dom_outer.html, sinon page_source.html, sinon dom_body.html
                     (ordre de fidélité décroissante, premier présent utilisé)
```

Fichier requis absent des artifacts du case (`manifest.artifacts`, jamais une
présence supposée) → `NON_REJOUABLE` immédiat, sans tenter d'alternative.

### Verdicts

``` text
python tools/replay_failure.py failure_cases/case_20260907_213458_action_validation_failure
```

Les `failure_types` du replay (mêmes listes que `failure_case_builder.py`,
dérivées de `validation_report.json`) sont comparés à ceux du case d'origine :

``` text
REPRODUIT       : ensembles de failure_types strictement identiques
NON_REPRODUIT   : le replay ne signale plus aucun problème
DIFFERENT       : le replay signale un problème, mais un ensemble différent
NON_REJOUABLE   : DOM requis absent / artefact illisible / erreur non
                  recouvrable pendant l'extraction ou la validation
```

### Limite actée : sélection de frame hors de portée

Un failure_case ne fige qu'un seul document déjà résolu — `page_snapshot.py`
capture le DOM **après** sélection de la meilleure chaîne de frames. Le shim
expose donc toujours `child_frames = []`. Conséquence assumée : ce replay peut
valider l'extraction/la validation à l'intérieur d'une frame déjà correctement
choisie, mais **ne peut pas** re-tester un bug de sélection de frame elle-même
(catégorie « frame context oublié », citée en Phase 17). La Phase 4
(diagnostic automatique) devra identifier ces cas en amont plutôt que de leur
appliquer un replay qui ne peut que produire `NON_REJOUABLE` ou un faux signal.

### Limite actée : réutilisation de dispatcher_success

Pour `stage=action`, le replay tourne sur exactement le même DOM qui a servi à
produire le rapport d'origine, avec le `dispatcher_success` d'origine réutilisé
tel quel (aucun dispatcher réel ne tourne pendant le replay). Tant qu'aucun
patch n'a modifié le code, `REPRODUIT` est donc attendu par construction pour
ces cas — ce n'est pas un défaut, c'est la vérification de référence avant
patch. La valeur diagnostique réelle (confirmer qu'un patch a fait disparaître
le problème) s'active en Phase 9 (replay post-patch), en rejouant le même
outil après modification du code.

### Dépendances

``` text
lxml         (nouveau, dev-only)
cssselect    (nouveau, dev-only)
```

Ajoutés à `requirements.txt`, jamais importés par `main.py` ni embarqués dans
le binaire Nuitka de distribution tiers (à revérifier explicitement avant la
prochaine `nuitka_build_release.ps1`, et à installer manuellement — via
`setup_machine.ps1` ou pip — sur toute machine où `replay_failure.py` doit
tourner ; l'auto-update R2 ne pousse jamais de nouvelles dépendances).

------------------------------------------------------------------------

# Phase 4 --- Générateur automatique de diagnostic

À ce stade, on dispose de :

``` text
DOM
question_blocks
registry
failure report
actions
éventuellement replay
```

On peut commencer à automatiser ton travail actuel avec ChatGPT/Claude.

On crée une couche :

``` text
failure_diagnoser.py
```

Elle ne modifie rien.

Elle produit un dossier diagnostic :

``` text
diagnosis.json
```

et/ou un prompt destiné à Codex.

Le diagnostic doit faire la distinction entre :

``` text
symptôme observé
comportement attendu
cause certaine
cause probable
cause plausible
modules probablement concernés
```

Exemple :

``` text
Symptôme :
17 radios visibles, seulement 12 options dans question_blocks.

Cause probable :
l'extracteur a filtré 5 options durant la construction du groupe.

Zone probable :
Survey/dom_analyzer.py
Survey/dom_extractors_decipher.py

Confiance :
probable
```

Il ne doit pas dire immédiatement :

``` text
ajoute telle fonction ligne 483
```

C'est à l'agent de coding de déterminer le patch.

------------------------------------------------------------------------

# Phase 5 --- Sélection automatique du contexte code

Actuellement tu dois envoyer plusieurs fichiers à Claude/Codex.

On automatise cela.

À partir du type d'échec :

``` text
failure_type
itype
registry metadata
```

on sélectionne uniquement les modules pertinents.

Exemple :

``` text
selection_not_applied
itype=checkbox
```

peut donner :

``` text
BOT_EVOLUTION_MEMORY.md
action_dispatcher.py
input_checkbox.py
input_utils.py
frame_utils.py
```

Alors qu'un problème :

``` text
missing_options
```

donnera plutôt :

``` text
BOT_EVOLUTION_MEMORY.md
dom_analyzer.py
dom_extractors_*.py concerné
dom_question_extractor.py
```

L'objectif n'est pas de transmettre tout le repo.

Trop de contexte dégrade souvent le diagnostic.

------------------------------------------------------------------------

# Phase 6 --- Génération automatique du prompt Codex

À partir du dossier `failure_case`, on génère ton prompt standard.

Exemple conceptuel :

``` text
CONTEXTE
...

BUG IDENTIFIÉ
...

FICHIERS À ANALYSER
...

CONTRAINTES
...
```

La section **BUG IDENTIFIÉ** sera générée automatiquement à partir du
diagnostic.

Elle respectera tes règles existantes :

``` text
DOM-first
pas de provider-wide logic
patch additif
pas de fallback empilé
budgets bornés
compatibilité Local / Prod
logs via log_debug
lecture BEM obligatoire
```

À ce stade :

``` text
failure détectée
→ case
→ diagnostic
→ prompt Codex
```

sera automatique.

Mais **Codex ne sera pas encore exécuté automatiquement**.

------------------------------------------------------------------------

# Phase 7 --- Génération automatique d'un patch dans une branche isolée

Une fois Phase 6 fiable, on autorise l'agent à coder.

Le pipeline devient :

``` text
failure
→ diagnostic
→ prompt
→ branche temporaire
→ Codex
→ patch
```

Exemple :

``` text
autofix/case_20260905_061542
```

Jamais de modification directe de :

``` text
playwright-migration
main
prod
```

L'agent ne doit travailler que dans une copie/branche dédiée.

------------------------------------------------------------------------

# Phase 8 --- Validation statique automatique

Avant même de tester le comportement :

``` text
python compile
imports
lint minimum
tests unitaires existants
```

On cherche les erreurs triviales :

``` text
SyntaxError
ImportError
NameError évident
signature cassée
module absent
```

Si ça échoue :

``` text
PATCH_REJECTED
```

Aucun test live.

------------------------------------------------------------------------

# Phase 9 --- Replay automatique du bug

Si le cas est rejouable :

``` text
avant patch → FAIL
après patch → PASS
```

Le patch n'est acceptable que si cette propriété est vérifiée.

Encore mieux :

on rejoue également quelques cas historiques voisins.

Par exemple si on corrige :

``` text
Decipher checkbox
```

on rejoue automatiquement plusieurs snapshots Decipher déjà validés.

Cela constitue progressivement ta **suite de non-régression DOM**.

------------------------------------------------------------------------

# Phase 10 --- Test live attach contrôlé

Certains bugs ne peuvent être validés qu'avec une vraie page.

On ajoute donc un mode :

``` text
AUTOFIX_LIVE_VALIDATE=1
```

En attach uniquement.

Le système :

``` text
applique le patch
→ relance/recharge le code
→ analyse la page actuelle
→ réexécute le scénario ciblé
→ validator vérifie
```

Important :

**pas de navigation libre du système de test.**

Il teste uniquement la page/cas qui a déclenché l'incident.

Budget strict :

``` text
1 patch
1 validation
éventuellement 1 deuxième correction
puis abandon
```

Jamais :

``` text
while not fixed:
    ask_codex_again()
```

------------------------------------------------------------------------

# Phase 11 --- Validation anti-régression

C'est là que `BOT_EVOLUTION_MEMORY.md` devient particulièrement utile.

Avant d'accepter un patch :

``` text
cas actuel
+
cas historiques du même module
+
cas génériques de référence
```

doivent continuer à fonctionner.

On pourra conserver quelque chose comme :

``` text
regression_cases/
    radio/
    checkbox/
    dropdown/
    text/
    matrix/
    cardsort/
    slider/
```

On ne cherche pas des milliers de tests.

Une sélection de **DOM représentatifs** suffit.

------------------------------------------------------------------------

# Phase 12 --- Score de confiance du patch

On évite le choix binaire trop simpliste :

``` text
test passé = déployer
```

On calcule plutôt une confiance.

Par exemple :

``` text
reproduction bug : PASS
cas ciblé : PASS
régressions : PASS
validator : PASS
syntax/imports : PASS
live validation : PASS
```

alors :

``` text
confidence = HIGH
```

Si le replay est impossible :

``` text
confidence = MEDIUM
```

Si un test voisin échoue :

``` text
confidence = REJECT
```

Pas besoin d'une formule mathématique sophistiquée.

Trois niveaux suffisent :

``` text
HIGH
MEDIUM
REJECT
```

------------------------------------------------------------------------

# Phase 13 --- Validation humaine simplifiée

À ce stade ton travail change complètement.

Au lieu de :

``` text
observer le survey
lire les logs
repérer le bug
capturer les fichiers
expliquer le bug
envoyer à Claude
envoyer à Codex
tester
```

tu reçois :

``` text
BUG détecté

Cause probable :
...

Patch :
...

Tests :
5/5 PASS

Live :
PASS

Confiance :
HIGH
```

avec :

``` text
[APPROUVER]
[REJETER]
```

C'est le premier niveau réellement utile de semi-autonomie.

------------------------------------------------------------------------

# Phase 14 --- Mise à jour automatique proposée de BEM

Après validation du patch seulement.

Le système génère une proposition d'entrée :

``` text
BOT_EVOLUTION_MEMORY.md
```

mais ne l'écrit pas automatiquement au début.

Tu valides :

``` text
Patch validé
→ générer entrée BEM
→ review
→ commit
```

Plus tard, on pourra automatiser l'écriture pour les patches
`HIGH confidence`.

------------------------------------------------------------------------

# Phase 15 --- Commit automatique

Une fois :

``` text
patch PASS
régression PASS
live PASS
BEM mise à jour
```

le système crée un commit propre :

``` text
fix(survey): support <pattern DOM>
```

avec référence au case :

``` text
case_id=20260905_061542
```

La branche reste séparée.

------------------------------------------------------------------------

# Phase 16 --- Merge semi-automatique

Première version :

``` text
HIGH confidence
→ proposer merge
→ confirmation humaine
```

Pas de merge automatique.

Cela te permet d'observer le système pendant plusieurs semaines.

On mesure notamment :

``` text
nombre de bugs détectés
vrais positifs
faux positifs
patches générés
patches validés du premier coup
régressions
```

------------------------------------------------------------------------

# Phase 17 --- Auto-fix supervisé

Quand les statistiques deviennent bonnes :

``` text
certaines catégories
+
HIGH confidence
+
replay disponible
+
régression PASS
```

peuvent être mergées automatiquement.

Exemple :

``` text
missing option simple
locator cassé
frame context oublié
nouveau pattern DOM strictement identifié
```

Mais les changements touchant :

``` text
orchestration
locks
prod
database
captcha
navigation globale
prompt global
```

restent obligatoirement manuels.

------------------------------------------------------------------------

# Phase 18 --- Auto-fix live complet en attach

Le système peut alors fonctionner comme un développeur local automatisé
:

``` text
survey tourne
    ↓
bug détecté
    ↓
snapshot
    ↓
diagnostic
    ↓
patch Codex
    ↓
tests
    ↓
hot reload/restart
    ↓
page rejouée
    ↓
validator PASS
    ↓
patch proposé
```

À ce niveau tu peux littéralement laisser le bot parcourir des surveys
pendant plusieurs heures.

Au retour tu regardes uniquement les incidents.

------------------------------------------------------------------------

# Phase 19 --- Fleet learning / mémoire globale des extracteurs

Une fois plusieurs bots en fonctionnement, les incidents deviennent une
source d'apprentissage.

Exemple :

``` text
Bot A rencontre nouveau widget X.
→ patch validé.

Bot B rencontre widget X deux heures plus tard.
→ déjà supporté.
```

Les snapshots et `failure_type` permettent également de déterminer quels
extracteurs causent le plus de problèmes.

Exemple :

``` text
input_radio       97.8 %
input_checkbox    91.2 %
dropdown          88.5 %
matrix            76.3 %
```

On sait alors précisément où investir du temps.

------------------------------------------------------------------------

# Phase 20 --- Production

Je ne recommande **pas** de faire tourner Codex directement dans le
processus SurveyBot de production.

Architecture finale plus saine :

``` text
PROD BOTS
   │
   └── détectent + enregistrent incidents
                ↓
       stockage des failure_cases
                ↓
LOCAL / DEV AUTOFIX WORKER
                ↓
diagnostic
patch
tests
validation
                ↓
repository
                ↓
release suivante
```

Le bot de prod reste donc simple et prévisible.

Il ne devient pas :

``` text
survey bot
+ coding agent
+ git client
+ test runner
+ patch manager
```

Ce serait fragile.

------------------------------------------------------------------------

# Architecture finale cible

``` text
                       SURVEYBOT
                          │
              ┌───────────┴───────────┐
              │                       │
         DOM extraction          Action dispatch
              │                       │
              ▼                       ▼
 QuestionBlockValidator        ActionValidator
              │                       │
              └───────────┬───────────┘
                          │
                       Failure
                          │
                          ▼
                   FailureRecorder
                          │
                          ▼
                     FailureCase
                          │
                          ▼
                 Diagnostic Agent
                          │
                          ▼
                   Context Selector
                          │
                          ▼
                   Codex Prompt
                          │
                          ▼
                  Isolated Branch
                          │
                          ▼
                       Patch
                          │
              ┌───────────┴───────────┐
              ▼                       ▼
        Static Tests              Replay Test
              │                       │
              └───────────┬───────────┘
                          ▼
                 Regression Suite
                          │
                          ▼
                  Live Validation
                          │
                          ▼
                  Confidence Score
                          │
              ┌───────────┴───────────┐
              │                       │
            REJECT                   HIGH
                                      │
                                      ▼
                                BEM update
                                      │
                                      ▼
                                   Commit
                                      │
                                      ▼
                                    Merge
```

## Ordre concret de développement

Je suivrais exactement cet ordre :

``` text
1A  Observabilité passive — TERMINÉE, validée en live attach
1B  Stabilisation des validators — OUVERTE EN TÂCHE DE FOND (1B.1 et 1B.2 validés)
2   Failure cases normalisés — TERMINÉE (failure_case_builder.py + CLI, 2 correctifs validés)
3   Replay local — TERMINÉE (dom_replay_shim.py + failure_replay.py + CLI)
4   Diagnostic automatique — PROCHAIN CHANTIER PRINCIPAL
5   Sélection automatique du contexte code
6   Génération du prompt Codex
7   Patch dans branche isolée
8   Tests statiques
9   Replay post-patch
10  Validation live attach
11  Suite de régression
12  Score de confiance
13  UI/review humaine simplifiée
14  Proposition BEM
15  Commit automatique
16  Merge semi-automatique
17  Auto-fix supervisé
18  Boucle autonome attach
19  Fleet learning / métriques
20  Pipeline de correction séparé de Prod
```

Le point le plus important est que **1A → 12 constituent le vrai cœur du
système**. Les étapes 13--20 sont principalement de l'automatisation
opérationnelle. Si les validators, le replay et les tests de régression
sont mauvais, automatiser davantage ne fera qu'accélérer la production
de mauvais patches.