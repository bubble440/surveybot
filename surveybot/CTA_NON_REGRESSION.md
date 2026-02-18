# CTA non-régression (niveau B)

Ce guide décrit une méthode **fiable et répétable** pour vérifier qu'un patch n'a pas cassé la chaîne CTA:

1. CTA détecté
2. clic effectivement exécuté
3. clic qui produit un effet attendu (submit/navigation/changement d'état)

## 1) Contrat de test (oracle)

Pour chaque page de test niveau B, on valide les assertions minimales suivantes:

- `cta_detected=true`: au moins un candidat CTA de score positif est trouvé.
- `click_executed=true`: le bot parvient à exécuter le clic (native click ou fallback JS).
- `effect_observed=true`: au moins un signal post-clic est observé:
  - URL différente,
  - hash DOM différent,
  - augmentation des ressources réseau,
  - ou événement `submit` capturé.

> Important: on ne dépend pas du “look” de la page suivante. On dépend d'un **contrat d'effets**.

## 2) Outillage proposé

Un script dédié est fourni:

- `surveybot/tools/run_level_b_cta.py`

### Ce que fait le script

- collecte des candidats CTA (`button`, `input submit`, `role=button`, ancres CTA),
- score les labels (`continue`, `next`, `suivant`, `valider`, etc.),
- clique le meilleur candidat,
- instrumente la page (`submit` + `click` listeners),
- compare l'état avant/après (URL, hash DOM, compteur ressources),
- sort un rapport JSON exploitable en CI.

### Exécution

```bash
python surveybot/tools/run_level_b_cta.py --url "https://<step-url>" --timeout 12
```

Code retour:

- `0`: PASS (détection + clic + effet)
- `2`: FAIL détection/clic
- `3`: FAIL effet post-clic absent

## 3) Stratégie de campagne niveau B

Construire un petit pack de pages “canari” stable (5-20 URLs), couvrant:

- CTA texte classique (“Next”, “Continue”),
- CTA icône/flèche,
- CTA dans iframe,
- CTA anchor stylé bouton,
- CTA `input[type=submit]`.

Puis exécuter en série après chaque patch:

```bash
while read -r url; do
  python surveybot/tools/run_level_b_cta.py --url "$url" --timeout 12 || exit 1
done < surveybot/tools/urls
```

## 4) Renforcement conseillé

Pour rendre les tests encore plus robustes:

- **Tracer la décision CTA** dans `cta_handler.py` (candidats + score + sélecteur choisi).
- **Sauver un artefact JSON** par run (`before`, `after`, `probe`) pour diff historique.
- **Ajouter un mode “dry-run detect only”** pour isoler rapidement les régressions de sélection sans navigation.
- **Tagger les faux positifs** (liens privacy/cookies) dans une blacklist centralisée.

## 5) Pourquoi cette méthode est stable

Elle repose sur des signaux invariants du flux utilisateur (submit/navigation/state-change) plutôt que sur l'apparence de la page suivante.

Donc même si le CTA change de style entre deux étapes, le test niveau B garde le même critère de succès.
