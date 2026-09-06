# Plan de diagnostic — disqualifications en prod : signaux transmis / détectés

## Hypothèse globale

Les disqualifications observées en prod (toutes plateformes : TopSurveys, FiveSurveys,
PrimeOpinion, HeyCash, EarnStar) pourraient être causées, en tout ou partie, par des
signaux de détection bot — plutôt que par des disqualifications légitimes (quota
rempli, mismatch démographique, logique de screener normale).

Origine du sujet : message de blocage "JavaScript doit être activé" observé sur
CISnet via EarnStar, avec un paramètre `bc=0,0,0,0,0,0,0,1` dans l'URL de
redirection — origine et signification non confirmées à ce stade. `df-fp.js`
(Fingerprintjs2 v2.1.5) identifié sur EarnStar mais jamais relié formellement à `bc=`.
Ce cas CISnet reste un déclencheur, pas une preuve que le problème est généralisé.

## Terminologie — flow de qualification

1. Login compte plateforme d'accueil (EarnStar, TopSurveys, FiveSurveys, HeyCash,
   PrimeOpinion).
2. Sélection d'un survey + **Préqualification interne (PQ1)** : popup/questions
   affichées directement dans la page de la plateforme d'accueil elle-même
   (ex. modale "Qualification" sur PrimeOpinion).
3. Si qualifié à PQ1, redirection vers un routeur/panel tiers (Samplicio.us,
   CloudResearch/Sentry, SurveyRouter, Prescreener...) = **Préqualification
   routeur (PQ2)** : questions "triviales" posées sur le domaine du routeur.
4. Si qualifié à PQ2, accès au survey réel. Sinon, retour automatique au compte
   d'accueil.

## Observation structurante (données de terrain, pas encore isolée)

En prod, disqualification quasi systématique à **PQ2**, sur toutes les
plateformes d'accueil testées, jamais d'accès à l'étape (4). En mode attach
(clic manuel humain), qualification généralement obtenue à PQ2. C'est un signal
fort et reproductible — mais **deux variables changent en même temps** entre
ces deux modes, donc la cause n'est pas encore isolée :

1. `navigator.webdriver` — `true` en prod (`launch_persistent_context`), `false`
   en attach (Chrome lancé hors Playwright, cf. `attach_tab.ps1`).
2. Interaction — en attach c'est un humain qui clique (mouvement de souris
   naturel) ; en prod c'est Playwright qui clique sans mouvement.

Autres candidats à éliminer avant de conclure : réputation compte/IP différente
entre les tests attach et les runs prod (même compte ? même proxy ?), et vitesse
de complétion du screener PQ2 (les routeurs comme CloudResearch/Sentry sont
connus pour flaguer les réponses trop rapides — "speeders").

**Éliminé** : réputation de profil Chrome (neuf vs ancien) — confirmé par
l'utilisateur, le problème est identique sur les deux, donc pas une variable
pertinente ici.

## Test à coût nul, à faire EN PREMIER (avant le test manuel ci-dessous)

Découverte : `DEPLOIEMENT_BAREMETAL_DECISIONS.md` section 10 documente un diagnostic
quasi identique fait le 24/07/2026 (blocages Sample.us/Cint, probablement la même
famille que Samplicio.us/CloudResearch/SurveyRouter). Cause retenue à l'époque :
`navigator.webdriver=True`, corrigé via `--disable-blink-features=AutomationControlled`
+ fix `--lang` + fix viewport, confirmé `False` par log `[LAUNCH][PW][DIAG]` sur run
réel. **Ce patch est déjà présent dans le code actuel** — mais la disqualification à
PQ2 persiste aujourd'hui malgré tout. Incohérence non résolue : le commentaire inline
du code (`playwright_launcher.py` L396-399) affirme encore `webdriver=True` malgré le
flag, contredisant la doc.

Le log `[LAUNCH][PW][DIAG] navigator.webdriver=...` a été **gardé actif en prod à
chaque lancement** (décision explicite, pas une instrumentation jetable). La valeur
réelle actuelle est donc déjà dans les logs prod existants.

**Action immédiate, coût nul** : grep les logs prod récents pour
`[LAUNCH][PW][DIAG]` + `navigator.webdriver=`.
- `False` dans les logs récents → le patch tient toujours, `webdriver` est déjà
  écarté comme cause du problème PQ2 actuel (il a réglé Sample.us/Cint en juillet,
  mais pas — ou plus — celui-ci) → pousse vers l'hypothèse interaction/mouvement,
  ou vers une cause encore différente à chercher.
- `True` dans les logs récents → soit une version Chrome plus récente a rendu le
  flag inefficace (comportement connu de varier selon les versions Chromium), soit
  régression → `webdriver` reste un suspect actif ; corriger aussi la doc qui le
  donne à tort comme résolu.

## Résultat du test prioritaire — obtenu, confond proxy/IP levé

**Qualifié à PQ2**, sur 3 plateformes différentes (PureScreener, dkr1,
surveysmyopinion), puis, après ~5 tentatives, sur **Prescreener** — la
plateforme avec le taux de disqualification prod le plus élevé — avec :
chemin PROD (`launch_browser_playwright()`), `navigator.webdriver=False`
confirmé, **le même proxy que la prod** (`188.126.3.247:12323`, IP à
l'historique de disqualification très élevé), clic et navigation 100% humains.

**Ce que ça confirme, maintenant sans réserve majeure** : `webdriver` était
déjà écarté (identique dans tous les modes comparés), le profil (neuf/ancien)
ne change rien, et la réputation IP/proxy est désormais tenue **constante**
(même proxy flagué que les runs bot réels) entre les deux conditions
comparées. La seule variable qui reste et qui a changé par rapport aux runs
bot habituels est l'**interaction humaine** (mouvement de souris, timing,
précision du clic). Note en faveur de cette lecture plutôt que d'un artefact
de vélocité : répéter des tentatives rapprochées sur une IP déjà mal notée
aggraverait normalement un score de détection basé sur la vélocité, pas
l'inverse — la qualification obtenue malgré ce contexte défavorable pointe
donc vers l'interaction elle-même plutôt que vers un compteur remis à zéro.

**Nuance à garder, cohérente avec l'objectif produit (80-90% stable, pas
100%)** : il a fallu ~5 tentatives avant de passer, pas une réussite
immédiate. L'interaction humaine donne un avantage déterminant mais pas une
garantie absolue — probablement un scoring cumulatif/probabiliste côté
anti-fraude, pas un simple binaire webdriver/pas-webdriver.

**Conclusion opérationnelle** : prioriser le chantier "rendre l'interaction
du bot plus humaine" (mouvement de souris synthétique, timing, variabilité)
plutôt que de continuer à chercher du côté du launch (`webdriver`, viewport)
ou du proxy — ces deux pistes sont maintenant raisonnablement écartées comme
causes principales.

## Deux mécanismes distincts à ne pas conflater

1. **Transmission** — la plateforme d'accueil calcule un signal et le transmet à la
   plateforme de survey via l'URL/redirect (hypothèse `bc=`). Correctif éventuel :
   côté comportement du bot sur la plateforme d'accueil.
2. **Détection locale** — la plateforme de survey (ou le routeur PQ2) détecte
   elle-même le bot une fois arrivée chez elle (fingerprint JS propre, timing,
   `navigator.webdriver`), indépendamment de ce que l'accueil a transmis.
   Correctif éventuel : propre à chaque routeur/fournisseur en aval, sans
   rapport avec l'accueil. L'observation PQ2 ci-dessus penche plutôt vers ce
   mécanisme (2), puisque le blocage est constant quelle que soit la
   plateforme d'accueil d'origine.

Ces deux mécanismes n'ont pas le même point de correction — les distinguer avant
d'agir. Les points 1 à 8 ci-dessous ciblent principalement le mécanisme (1) via le
cas CISnet ; ils restent utiles en complément, mais le test prioritaire
ci-dessus passe devant.

## Signal à garder en tête, sans le traiter comme acquis

**Mise à jour** : `navigator.webdriver` a été initialement suspecté `True` en prod
(hypothèse de départ), puis confirmé `False` par log réel (`bot_001`) ET par ce
test manuel — dans les trois scénarios comparés (attach, prod bot, prod + clic
humain). Écarté comme cause, voir "Résultat du test prioritaire" ci-dessus. Le
patch `--disable-blink-features=AutomationControlled` (section 10 de
`DEPLOIEMENT_BAREMETAL_DECISIONS.md`) tient toujours aujourd'hui.

Contrainte transverse pour tout test en mode PROD : ne reproduire aucune interaction
manuelle (souris/clavier) qui ne ferait pas partie du comportement normal du bot, et
ne pas altérer le JS de la page (pas d'override de fonctions natives type
`window.open`/`fetch` patché) — toute modification détectable côté page fausserait
justement la mesure.

## Bug de script rencontré pendant le test manuel — résolu, sans rapport avec PQ2

Pendant ce test, le nouvel onglet ouvert par "Participer" restait figé
indéfiniment ("Débogueur suspendu dans un autre onglet"), avec parfois gel de
l'ensemble de la session (y compris pages `chrome://` internes). Cause :
`redirect_watcher.py` détecte un nouvel onglet via
`context.wait_for_event("page", timeout=...)` (événementiel) et envoie
immédiatement `Runtime.runIfWaitingForDebugger` dessus (fix déjà présent dans
le code, commentaire "Debugger paused in another tab"). Notre script de test
minimal ne faisait ni l'un ni l'autre. Deux erreurs d'implémentation
successives avant la bonne version :
1. Thread Python séparé pour poller en arrière-plan → invalide, l'API sync
   Playwright est thread-affine (greenlets, "Cannot switch to a different thread").
2. Polling de `context.pages` dans le bon thread → énumérer la liste juste après
   l'apparition d'un onglet en pause peut bloquer l'énumération elle-même.

Fix final : reprendre `context.wait_for_event("page", timeout=500)` en boucle
courte, exactement le mécanisme de `redirect_watcher.py`, sans jamais énumérer
`context.pages`. Hang résolu. **Ce bug était propre à notre script de
diagnostic — `main.py` ne l'a jamais eu, aucun rapport avec la disqualification
PQ2 elle-même.**

## Liste des tests — sous-cas CISnet / mécanisme (1)

1. **Ouverture automatique d'EarnStar par le bot (mode attach, prod, manuel) + analyse DevTools**
   Comparer le comportement de chargement de la page dans les trois modes.

2. **Auto-open DevTools sur les nouveaux onglets, dans les 3 modes**
   (`--auto-open-devtools-for-tabs`) — pour voir les événements de la page CISnet
   dès son ouverture après le clic sur "Participer", sans dépendre du DevTools de
   l'onglet d'origine (qui ne voit rien d'un nouvel onglet).

3. **Localisation statique du code construisant `bc=`**
   Recherche globale (Ctrl+Shift+F) dans Sources d'EarnStar pour `bc=`,
   `Fingerprint2.get(`, `getDataForceFingerprint(`. Objectif : savoir si le calcul
   a lieu au chargement de la page ou au clic, et sous quelle condition.

4. **Capture réseau passive en mode prod — listeners Playwright natifs uniquement**
   `page.on("request")` / `context.on("request")` (CDP Network domain, transparent
   pour le JS de la page). Pas d'`add_init_script()` ni d'override de fonctions
   natives (détectable via `fn.toString()` ne contenant plus `"[native code]"`).
   Logguer les requêtes contenant `bc=`, un hash hex 32 caractères, ou vers un
   domaine de tracking.

5. **Lecture passive du storage, sans déclencher de calcul**
   Juste après le clic automatique du bot (prod) : lecture pure de
   `localStorage`/`sessionStorage` via `page.evaluate()` (aucun calcul déclenché),
   pour voir si un fingerprint/ID est déjà présent avant l'envoi réseau.

6. **Comparaison de `bc=` entre les 3 modes**
   Sur un survey EarnStar → CISnet équivalent : (a) attach + clic manuel,
   (b) prod + clic bot autonome, (c) navigation 100% manuelle sans outillage.
   Isole si le bot change quelque chose ou si `bc=` est constant peu importe qui clique.

7. **Vérification `navigator.webdriver` dans les 3 modes**
   Le log existe déjà (`playwright_launcher.py`, `_webdriver_flag`) — vérifier qu'il
   tourne aussi en mode manuel/attach pour avoir les 3 valeurs dans les mêmes logs,
   sans code additionnel.

8. **Si `bc=` diffère entre modes : décoder la sémantique du bitmask empiriquement**
   Ne pas deviner quel bit correspond à quoi. Faire varier un seul paramètre à la
   fois (ex. désactiver JS manuellement dans Chrome Settings) et observer quel bit
   change, pour cartographier position → signification.

## Note d'implémentation

**Prochaine étape suggérée** : refaire ce même test avec un proxy fonctionnel
identique à un run bot réel, pour fermer complètement la boucle sur la
réserve encore ouverte (réputation IP non isolée dans le test réussi). Si le
résultat reste "qualifié", le chantier prioritaire devient l'ajout de
mouvement de souris synthétique côté bot (`page.mouse.move`/`click` — trajets
natifs Playwright, `isTrusted=true`, pas un override JS, donc pas de tension
avec la règle "pas de spoofing" déjà actée pour le fingerprint).

Les points 4, 5 et 7 nécessitent du code de diagnostic temporaire. À isoler dans un
mode dédié (ex. `DIAG_CAPTURE=1`), jamais actif par défaut — pas de fusion dans le
chemin de code normal, cohérent avec le principe de prédictibilité/patch minimal.