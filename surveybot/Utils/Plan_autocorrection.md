Voici le plan complet que je recommande, de **1A jusqu’au système d’auto-correction mature**. Je le découpe volontairement en étapes courtes et validables : on ne passe jamais à l’automatisation d’un niveau tant que le niveau précédent n’est pas fiable.

## Phase 1A — Observabilité passive

Objectif : détecter les échecs que tu repères aujourd’hui visuellement, **sans modifier le comportement du bot**.

On ajoute :

```text
Survey/
    question_block_validator.py
    action_validator.py
    failure_recorder.py
```

### Extraction

Après :

```text
dom_analyzer.analyze_dom()
```

on valide les `question_blocks`.

Le validator cherche uniquement des incohérences fortes :

```text
target_id absent du DOM_REGISTRY
question vide/non exploitable
radio/checkbox sans options
options dupliquées
min_select > max_select
max_select impossible
kind/context incompatible avec registry
locator principal impossible à résoudre
```

Pas encore de tentative de comprendre tout le DOM.

### Sélection

Après :

```text
action_dispatcher.execute_actions_plan()
```

on compare :

```text
actions demandées
vs
état DOM réellement observé
```

Exemple :

```text
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

Création automatique d'un snapshot enrichi :

```text
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

Le bot continue normalement.

### Critère de validation

On utilise le bot en attach comme aujourd'hui et on vérifie :

> quand toi tu vois une extraction ou une sélection incorrecte, le validator doit également la signaler.

Cible : **forte précision**, même si on ne détecte encore que 60–70 % des vrais problèmes.

Un faux positif est plus dangereux qu'un problème non détecté.

---

# Phase 1B — Fiabilisation des validators

Objectif : réduire les faux positifs et augmenter progressivement la couverture.

On teste les validators sur tes DOM réels existants.

On construit une petite taxonomie :

```text
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

Une dizaine de catégories fiables vaut mieux qu'une classification ultra-fine instable.

### Résultat attendu

Chaque incident doit pouvoir être décrit comme :

```json
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

---

# Phase 2 — Création automatique de cas de reproduction

Aujourd'hui tu fais manuellement :

```text
page live
→ snapshot
→ dom_body.html
→ question_blocks.json
→ fichiers concernés
→ prompt Claude/Codex
```

Cette étape doit disparaître.

Chaque failure enregistré devient automatiquement un **case** :

```text
failure_cases/
    case_20260905_061542/
        manifest.json
        dom_outer.html
        dom_body.html
        page_source.html
        question_blocks.json
        validation_report.json
        actions_requested.json
        viewport.png
        frames/
```

Le `manifest.json` contient par exemple :

```text
stage
failure_type
itype
target_id
frame_chain
snapshot_path
timestamp
provider/domain
```

Mais il ne contient **aucune interprétation fragile du bug**.

---

# Phase 3 — Replay local déterministe

C'est une étape essentielle.

Avant de demander à une IA de modifier le code, on doit pouvoir reproduire les erreurs autant que possible sans dépendre de la page live.

On crée :

```text
tools/replay_failure.py
```

ou son équivalent.

Il devra pouvoir charger :

```text
failure_case
```

et rejouer au minimum :

```text
DOM
→ dom_analyzer
→ question_blocks
→ validator
```

Pour les problèmes d'extraction, c'est extrêmement utile.

Exemple :

```text
python replay_failure.py case_20260905_061542
```

résultat :

```text
EXPECTED FAILURE:
missing_options

CURRENT RESULT:
missing_options

STATUS:
REPRODUCED
```

Après patch :

```text
CURRENT RESULT:
PASS

STATUS:
FIXED
```

### Limite

Certains problèmes d'interaction nécessitent JavaScript/runtime réel.

Donc le replay ne couvrira pas 100 % des problèmes de sélection.

Ce n'est pas grave.

On ne doit surtout pas construire un navigateur artificiel gigantesque juste pour atteindre 100 %.

---

# Phase 4 — Générateur automatique de diagnostic

À ce stade, on dispose de :

```text
DOM
question_blocks
registry
failure report
actions
éventuellement replay
```

On peut commencer à automatiser ton travail actuel avec ChatGPT/Claude.

On crée une couche :

```text
failure_diagnoser.py
```

Elle ne modifie rien.

Elle produit un dossier diagnostic :

```text
diagnosis.json
```

et/ou un prompt destiné à Codex.

Le diagnostic doit faire la distinction entre :

```text
symptôme observé
comportement attendu
cause certaine
cause probable
cause plausible
modules probablement concernés
```

Exemple :

```text
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

```text
ajoute telle fonction ligne 483
```

C'est à l'agent de coding de déterminer le patch.

---

# Phase 5 — Sélection automatique du contexte code

Actuellement tu dois envoyer plusieurs fichiers à Claude/Codex.

On automatise cela.

À partir du type d'échec :

```text
failure_type
itype
registry metadata
```

on sélectionne uniquement les modules pertinents.

Exemple :

```text
selection_not_applied
itype=checkbox
```

peut donner :

```text
BOT_EVOLUTION_MEMORY.md
action_dispatcher.py
input_checkbox.py
input_utils.py
frame_utils.py
```

Alors qu'un problème :

```text
missing_options
```

donnera plutôt :

```text
BOT_EVOLUTION_MEMORY.md
dom_analyzer.py
dom_extractors_*.py concerné
dom_question_extractor.py
```

L'objectif n'est pas de transmettre tout le repo.

Trop de contexte dégrade souvent le diagnostic.

---

# Phase 6 — Génération automatique du prompt Codex

À partir du dossier `failure_case`, on génère ton prompt standard.

Exemple conceptuel :

```text
CONTEXTE
...

BUG IDENTIFIÉ
...

FICHIERS À ANALYSER
...

CONTRAINTES
...
```

La section **BUG IDENTIFIÉ** sera générée automatiquement à partir du diagnostic.

Elle respectera tes règles existantes :

```text
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

```text
failure détectée
→ case
→ diagnostic
→ prompt Codex
```

sera automatique.

Mais **Codex ne sera pas encore exécuté automatiquement**.

---

# Phase 7 — Génération automatique d'un patch dans une branche isolée

Une fois Phase 6 fiable, on autorise l'agent à coder.

Le pipeline devient :

```text
failure
→ diagnostic
→ prompt
→ branche temporaire
→ Codex
→ patch
```

Exemple :

```text
autofix/case_20260905_061542
```

Jamais de modification directe de :

```text
playwright-migration
main
prod
```

L'agent ne doit travailler que dans une copie/branche dédiée.

---

# Phase 8 — Validation statique automatique

Avant même de tester le comportement :

```text
python compile
imports
lint minimum
tests unitaires existants
```

On cherche les erreurs triviales :

```text
SyntaxError
ImportError
NameError évident
signature cassée
module absent
```

Si ça échoue :

```text
PATCH_REJECTED
```

Aucun test live.

---

# Phase 9 — Replay automatique du bug

Si le cas est rejouable :

```text
avant patch → FAIL
après patch → PASS
```

Le patch n'est acceptable que si cette propriété est vérifiée.

Encore mieux :

on rejoue également quelques cas historiques voisins.

Par exemple si on corrige :

```text
Decipher checkbox
```

on rejoue automatiquement plusieurs snapshots Decipher déjà validés.

Cela constitue progressivement ta **suite de non-régression DOM**.

---

# Phase 10 — Test live attach contrôlé

Certains bugs ne peuvent être validés qu'avec une vraie page.

On ajoute donc un mode :

```text
AUTOFIX_LIVE_VALIDATE=1
```

En attach uniquement.

Le système :

```text
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

```text
1 patch
1 validation
éventuellement 1 deuxième correction
puis abandon
```

Jamais :

```text
while not fixed:
    ask_codex_again()
```

---

# Phase 11 — Validation anti-régression

C'est là que `BOT_EVOLUTION_MEMORY.md` devient particulièrement utile.

Avant d'accepter un patch :

```text
cas actuel
+
cas historiques du même module
+
cas génériques de référence
```

doivent continuer à fonctionner.

On pourra conserver quelque chose comme :

```text
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

---

# Phase 12 — Score de confiance du patch

On évite le choix binaire trop simpliste :

```text
test passé = déployer
```

On calcule plutôt une confiance.

Par exemple :

```text
reproduction bug : PASS
cas ciblé : PASS
régressions : PASS
validator : PASS
syntax/imports : PASS
live validation : PASS
```

alors :

```text
confidence = HIGH
```

Si le replay est impossible :

```text
confidence = MEDIUM
```

Si un test voisin échoue :

```text
confidence = REJECT
```

Pas besoin d'une formule mathématique sophistiquée.

Trois niveaux suffisent :

```text
HIGH
MEDIUM
REJECT
```

---

# Phase 13 — Validation humaine simplifiée

À ce stade ton travail change complètement.

Au lieu de :

```text
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

```text
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

```text
[APPROUVER]
[REJETER]
```

C'est le premier niveau réellement utile de semi-autonomie.

---

# Phase 14 — Mise à jour automatique proposée de BEM

Après validation du patch seulement.

Le système génère une proposition d'entrée :

```text
BOT_EVOLUTION_MEMORY.md
```

mais ne l'écrit pas automatiquement au début.

Tu valides :

```text
Patch validé
→ générer entrée BEM
→ review
→ commit
```

Plus tard, on pourra automatiser l'écriture pour les patches `HIGH confidence`.

---

# Phase 15 — Commit automatique

Une fois :

```text
patch PASS
régression PASS
live PASS
BEM mise à jour
```

le système crée un commit propre :

```text
fix(survey): support <pattern DOM>
```

avec référence au case :

```text
case_id=20260905_061542
```

La branche reste séparée.

---

# Phase 16 — Merge semi-automatique

Première version :

```text
HIGH confidence
→ proposer merge
→ confirmation humaine
```

Pas de merge automatique.

Cela te permet d'observer le système pendant plusieurs semaines.

On mesure notamment :

```text
nombre de bugs détectés
vrais positifs
faux positifs
patches générés
patches validés du premier coup
régressions
```

---

# Phase 17 — Auto-fix supervisé

Quand les statistiques deviennent bonnes :

```text
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

```text
missing option simple
locator cassé
frame context oublié
nouveau pattern DOM strictement identifié
```

Mais les changements touchant :

```text
orchestration
locks
prod
database
captcha
navigation globale
prompt global
```

restent obligatoirement manuels.

---

# Phase 18 — Auto-fix live complet en attach

Le système peut alors fonctionner comme un développeur local automatisé :

```text
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

À ce niveau tu peux littéralement laisser le bot parcourir des surveys pendant plusieurs heures.

Au retour tu regardes uniquement les incidents.

---

# Phase 19 — Fleet learning / mémoire globale des extracteurs

Une fois plusieurs bots en fonctionnement, les incidents deviennent une source d'apprentissage.

Exemple :

```text
Bot A rencontre nouveau widget X.
→ patch validé.

Bot B rencontre widget X deux heures plus tard.
→ déjà supporté.
```

Les snapshots et `failure_type` permettent également de déterminer quels extracteurs causent le plus de problèmes.

Exemple :

```text
input_radio       97.8 %
input_checkbox    91.2 %
dropdown          88.5 %
matrix            76.3 %
```

On sait alors précisément où investir du temps.

---

# Phase 20 — Production

Je ne recommande **pas** de faire tourner Codex directement dans le processus SurveyBot de production.

Architecture finale plus saine :

```text
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

```text
survey bot
+ coding agent
+ git client
+ test runner
+ patch manager
```

Ce serait fragile.

---

# Architecture finale cible

```text
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

```text
1A  Observabilité passive
1B  Stabilisation des validators
2   Failure cases normalisés
3   Replay local
4   Diagnostic automatique
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

Le point le plus important est que **1A → 12 constituent le vrai cœur du système**. Les étapes 13–20 sont principalement de l'automatisation opérationnelle. Si les validators, le replay et les tests de régression sont mauvais, automatiser davantage ne fera qu'accélérer la production de mauvais patches.