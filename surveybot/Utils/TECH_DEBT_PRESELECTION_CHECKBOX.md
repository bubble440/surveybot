# Fiche de suivi — Points reportés / décisions d'abandon

Ce fichier recense les problèmes identifiés mais **non résolus à la racine**, pour lesquels une décision pragmatique (contournement, abandon de diagnostic, retry, skip) a été prise afin de débloquer le déploiement. À reprendre une fois l'app stabilisée en production.

---

## 1. Blocage intermittent du clic sur les cases à cocher (présélection TopSurveys)

**Statut : contourné, cause racine non identifiée.**

### Symptôme
Lors de la phase de présélection TopSurveys (questions à cases à cocher, `select_checkbox_answers` dans `response_executor.py`), le clic natif Playwright sur le `label` d'une option échoue de façon intermittente avec :
```
TimeoutError - ElementHandle.click: Timeout 30000ms exceeded.
waiting for element to be visible, enabled and stable
```

### Caractéristiques observées
- **Intermittent et non reproductible de façon déterministe** : un même label, sur la même question, à la même position de défilement de la liste, échoue dans un run et réussit dans un autre run ultérieur, sans différence structurelle observable dans le DOM.
- N'est pas lié à un masquage CSS de l'élément (confirmé : l'input est masqué par design du widget, mais le label englobant est bien visible et actionnable une fois ciblé correctement).
- N'est pas lié à une instabilité géométrique mesurable côté JS (position stable sur 15 échantillons / 3 secondes, aucune animation détectée).
- N'est pas systématiquement lié à un défilement insuffisant (le défilement vers l'élément a été corrigé et n'élimine pas le problème).
- N'est pas systématiquement lié au recouvrement par le bouton "Suivant" fixe (observé une fois, mais pas reproductible comme cause unique).
- N'est pas systématiquement lié à la longueur du label / au texte multi-lignes (hypothèse testée et infirmée : des labels courts sur une seule ligne échouent aussi).
- Playwright peut interroger ses propres critères d'actionabilité (`visible`, `enabled`, `stable`) individuellement et les voir tous passer (`ok`), alors que le clic complet échoue quand même avec le même TimeoutError.
- Le call log Playwright natif ne donne aucune information supplémentaire au-delà des deux lignes déjà visibles dans l'exception.
- En observation directe (navigateur local non-headless, DevTools ouverts), **aucune activité n'est visible côté page** pendant le blocage : pas de requête réseau, pas d'erreur console, pas de changement visuel.

### Pistes explorées et écartées
1. Masquage CSS de l'input → écarté (le label, pas l'input, est la bonne cible — corrigé séparément, ce patch reste valide).
2. Animation `ripple` du framework Vue → écarté (aucune animation active détectée au moment des échecs).
3. Re-render complet de la liste de checkboxes à chaque clic (confirmé structurellement, via l'attribut `value` qui s'accumule sur toutes les checkboxes) → contribue probablement à des références DOM obsolètes, corrigé partiellement en ré-résolvant la référence à chaque itération, mais n'explique pas tous les cas.
4. Élément hors viewport (scroll insuffisant) → corrigé (scroll réintroduit avant chaque clic), mais n'élimine pas tous les échecs.
5. Recouvrement par le CTA "Suivant" en position fixe → observé une fois mais non confirmé comme cause systématique.
6. **Focus de la fenêtre/page côté Playwright** → patch appliqué (mise au focus explicite de la page avant clic), **a nettement réduit la fréquence des échecs** (passage d'un quasi-100% d'échec sur certaines questions à une majorité de succès, parfois après retry) mais n'élimine pas complètement le phénomène.
7. Ciblage du `label` multi-lignes vs. ciblage de la `checkbox-box` interne (taille fixe) → patch appliqué, n'a pas résolu les échecs résiduels sur labels courts à une seule ligne.

### Hypothèses non testées (pistes pour reprise future)
- **Comportement de throttling de Chromium sur fenêtres/onglets sans focus système réel**, particulièrement pertinent en environnement Xvfb (prod, Fly.io) où aucun gestionnaire de fenêtres interactif ne gère le focus de façon classique. Le patch de focus a réduit le problème sans l'éliminer — il est possible qu'un mécanisme de focus plus robuste (au niveau CDP plutôt qu'au niveau Playwright haut niveau) soit nécessaire.
- Investigation côté **CDP brut** (`Input.dispatchMouseEvent` directement, en contournant la couche d'attente d'actionabilité de Playwright) pour voir si le clic bas niveau aboutit même quand `element.click()` haut niveau bloque.
- Possible interaction avec le **profil Chrome partagé/persistant** restauré depuis PostgreSQL en prod (vs. profil éphémère en test local) — non testé comparativement.
- Possible lien avec la charge CPU/mémoire de la VM Fly.io au moment précis du clic (non mesuré, écarté seulement pour les tests locaux où le bot tournait seul).

### Décision actuelle (contournement)
- Retry borné (3 tentatives) sur TimeoutError avant abandon de l'option.
- Après épuisement du retry sur une option, **l'option est ignorée** et le traitement continue avec les options suivantes de la liste, plutôt que de faire échouer toute la question/le sondage.
- Compromis assumé : certaines réponses checkbox seront incomplètes (options manquantes) plutôt que de provoquer une disqualification systématique ou un coût temporel élevé (jusqu'à plusieurs minutes par question dans les pires cas observés).

### Pour reprise future
- Mesurer le taux de complétude réel des réponses checkbox en production (combien d'options sont effectivement ignorées en moyenne) pour évaluer l'impact réel sur la qualité des réponses et le taux de disqualification associé.
- Si le taux d'options ignorées s'avère significatif, prioriser l'investigation CDP bas niveau plutôt que de continuer à empiler des hypothèses côté Playwright haut niveau.

---

## 2. Non-correspondance "Natation" vs "La natation" (et variantes similaires)

**Statut : non corrigé, fréquence à mesurer.**

### Symptôme
```
⚠️ Cible non trouvée dans les labels : natation
```
GPT propose parfois un libellé d'option légèrement différent du texte réellement affiché dans le DOM (ex. "Natation" proposé alors que l'option affichée est "La natation", avec article défini). La normalisation de texte actuelle (mise en minuscule) ne suffit pas à faire correspondre ces variantes.

### Décision actuelle
Aucune — l'option concernée est simplement non trouvée et ignorée silencieusement (log d'avertissement seulement).

### Pour reprise future
- Évaluer la fréquence de ce type de désaccord sur un échantillon de runs en production.
- Si fréquent, envisager une correspondance plus tolérante (ex. : ignorer les articles définis/indéfinis en début de libellé, ou une distance d'édition courte) côté `select_checkbox_answers`, sans toucher à `prompt_builder.py` ni à la logique de génération GPT elle-même.

---

*Dernière mise à jour : à la suite du diagnostic approfondi du bug de clic checkbox en présélection TopSurveys (migration Selenium → Playwright).*
