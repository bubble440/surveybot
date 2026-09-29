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
> Mise à jour 2026-09-11 (suite 3) : Phase 4 clôturée. `Survey/failure_diagnosis.py`
> (niveau de cause dérivé uniquement du verdict de replay ; attribution de modules
> limitée à une recherche exacte de signaux structurés — flags de contexte, group_key —
> dans BOT_EVOLUTION_MEMORY.md, provider_domain délibérément exclu car source de faux
> positifs vérifiée en pratique ; confiance plafonnée à "plausible" si le case est
> incomplete) + `tools/diagnose_failure.py`, sortie `diagnoses/case_<id>/diagnosis.json`.
> Aucun prompt Codex généré (hors périmètre, prévu Phase 6). Point de vigilance acté :
> la table `_EXPECTED_BEHAVIOR` (description du comportement attendu par failure_type)
> est maintenue à la main en miroir du code des validators — à mettre à jour
> explicitement lors de tout futur patch touchant action_validator.py/
> question_block_validator.py, sinon elle peut devenir silencieusement obsolète. Le
> chantier principal passe à la Phase 5 (sélection automatique du contexte code).
> Mise à jour 2026-09-11 (suite 4) : Phase 5 clôturée. `Survey/context_selector.py` +
> `tools/select_context.py`. Investigation documentée avant écriture : aucune table de
> mapping itype/stage -> fichier n'existe dans le code (action_dispatcher.py route par
> ~150 branches if/elif inline, dom_analyzer.py par cascade try/except séquentielle,
> le seul dict itype trouvé — _TYPE_ALIASES — mappe vers des synonymes texte, pas des
> fichiers) — cette source de signal contribue donc 0 fichier, documenté explicitement
> plutôt que masqué. La sélection repose à 100% sur modules_likely_involved (Phase 4),
> filtré (références obsolètes exclues) et plafonné (8 fichiers par défaut, troncature
> déterministe et tracée). BOT_EVOLUTION_MEMORY.md toujours inclus, hors plafond.
> Deux points de vigilance ajoutés à la même checklist que la Phase 4 : (a) si
> action_dispatcher.py/dom_analyzer.py sont un jour refactorés vers une vraie table de
> dispatch, revisiter la Source 2 de context_selector.py ; (b) pour un pattern non
> encore documenté dans BEM, la sélection sera vide — la Phase 6 doit traiter
> explicitement le cas code_files vide comme "revue manuelle nécessaire", pas générer
> un prompt Codex normal avec zéro fichier ciblé. Le chantier principal passe à la
> Phase 6 (génération automatique du prompt Codex).
> Mise à jour 2026-09-13 (suite 5) : Phase 6 clôturée. `Survey/prompt_generator.py` +
> `tools/generate_prompt.py` — gabarit Claude Code reproduit verbatim (seule BUG
> IDENTIFIÉ est dynamique), garde-fou d'éligibilité (code_files non vide ET
> confidence_global in {probable, certain}) sinon `MANUAL_REVIEW_REQUIRED.txt` à la
> place d'un prompt, jamais un entre-deux. Cas verdict=DIFFERENT correctement traité :
> le symptôme décrit ce que le replay a réellement reconfirmé, pas le rapport
> d'origine resté non reproduit. Aucune mécanique de pipeline (case_id, verdict,
> confiance, provider_domain, target_id/action_index/block_index) exposée dans le
> texte du prompt généré. Point de vigilance sérieux ouvert, à vérifier avant tout
> usage en volume : le champ `value` d'un issue de validation_report.json est recopié
> tel quel dans le prompt généré, alors que ce fichier n'a jamais été sanitisé
> (contrairement à meta.json/aux DOM HTML en Phase 2) — si ce champ contient parfois
> une donnée réellement saisie pour le répondant (ex. un code postal), elle fuiterait
> sans filtre dans une conversation Codex externe. À vérifier sur des cases réels avant
> la Phase 7 ; si confirmé, sanitiser à la source (Phase 2, validation_report.json) ou
> exclure le champ en Phase 6. Le chantier principal passe à la Phase 7 (génération
> automatique d'un patch dans une branche isolée).
> Mise à jour 2026-09-13 (suite 6) : réexamen du replay avant de poursuivre la Phase 7.
> Deux limites déjà actées en Phase 3 (renommée 3A) restent bloquantes pour la valeur
> de la Phase 9 : le dispatcher n'est jamais réellement réexécuté par le replay
> (`stage=action` réutilise le `dispatcher_success` d'origine, aucun dispatcher réel ne
> tourne), et la sélection de frame reste hors de portée (`child_frames` toujours vide).
> Concrètement, un patch touchant `action_dispatcher.py` ou la sélection de frame n'est
> aujourd'hui vérifié par aucun replay local, avant comme après correction. Décision :
> insérer un chantier de fiabilisation du replay avant de considérer un verdict de
> Phase 9 comme un signal de confiance pour ces catégories de patch. Phase 3 devient
> 3A (statut inchangé, TERMINÉE) ; nouvelles phases 3B (replay navigateur local —
> capsule runtime, incluant un trace replay pour les cas action), 3C (reconstruction
> frames + Shadow DOM) et 3D (classificateur de rejouabilité `static_dom` /
> `browser_capsule` / `external_non_replayable`, qui fait de la Phase 10 la voie
> normale des cas non rejouables localement plutôt qu'un recours tardif isolé). Le
> chantier principal passe à la Phase 3B. La Phase 7 peut être préparée en parallèle
> pour les cas `static_dom` déjà bien couverts par 3A, mais n'est pas considérée
> fiable pour le reste tant que 3B n'existe pas. Priorité affichée entre les nouvelles
> phases : 3B avant 3C — 3C (frames/Shadow DOM) est plus coûteuse et ne doit être
> construite qu'après avoir mesuré, sur des cas réels, le volume d'incidents
> réellement bloqués par la frame ou un Shadow DOM fermé, pour ne pas construire un
> replay plus complexe que les bugs qu'il couvre réellement.
> Mise à jour 2026-09-13 (suite 7) : affinement de la structure ci-dessus. Phase 3
> redevient un chapeau unique (3A à 3D, un seul critère de clôture commun) plutôt que
> quatre phases numérotées séparément, avec une séparation plus nette capture/moteur :
> 3B ne fait que capturer (DOM+CSS, `runtime_state.json`, `frame_tree.json`,
> `shadow_roots.json`, `action_trace.json`, mutations bornées, MHTML, règles secrets/
> données sensibles) ; 3C exécute le replay Chromium local à partir de cette capture
> (isolation réseau, reconstruction, extraction/frame selection, actions). La
> classification 3D gagne un quatrième mode explicite, `TRACE_REPLAY` (état avant/après
> suffisant sans réexécution fidèle du dispatcher), distinct de `BROWSER_CAPSULE` — les
> deux n'ont pas le même niveau de preuve et ne doivent pas être traités à égalité par
> la Phase 9/le score de confiance (Phase 12). Deux garde-fous ajoutés : (a) toute
> exécution réelle du dispatcher en 3C.4 est bornée par un timeout explicite, avec
> déclassement automatique vers `TRACE_REPLAY` en cas de dépassement, jamais un replay
> qui pend ; (b) le pari `dispatcher_success=false` mais DOM prouvant le succès (déjà
> posé par `action_validator.py` en Phase 1B.1) et le mode `TRACE_REPLAY` doivent
> partager une seule fonction oracle, pas deux implémentations parallèles. Les schémas
> de fichiers (`runtime_state.json` etc.) sont explicitement indicatifs, pas figés. Le
> chantier principal reste la Phase 3B ; l'ordre de priorité 3B avant 3C est inchangé.
> Mise à jour 2026-09-13 (suite 8) : Phase 3B PARTIELLEMENT clôturée — 3B.1, 3B.2,
> 3B.5, 3B.6 et 3B.8 implémentés et validés (`Survey/browser_capsule.py`, additif,
> appelé depuis le hook d'action déjà existant dans `Survey/page_snapshot.py` ;
> `runtime_state.json`/`action_trace.json` ajoutés à `Survey/failure_case_builder.py`
> — `_KNOWN_FILES` + sanitisation dédiée `href`/`src`/`url` en plus de celle déjà en
> place sur `meta.json`/le HTML). 3B.3 (frames) et 3B.4 (Shadow DOM) restent
> délibérément différées, non traitées par ce patch, dans l'attente de la mesure sur
> cas réels prévue depuis la suite 6/7. Compromis assumé explicitement : la capture
> de l'état runtime "avant" et l'installation du `MutationObserver` s'exécutent sur
> chaque action du plan (pas seulement celles qui échouent), car l'état "avant" doit
> être capturé avant de savoir si l'action va échouer — le coût (jusqu'à 40 éléments,
> budget déjà borné) est donc systématique tant que l'observabilité est active, pas
> seulement au moment d'un incident, contrairement à l'intention initiale de ce
> chantier. Atténuation actée : ce hook reste conditionné par `SURVEY_OBSERVABILITY`,
> désactivé par défaut en prod, donc le coût inconditionnel ne s'applique pas au parc
> réel tant que l'observabilité n'y est pas explicitement activée. Point de vigilance
> ouvert, à surveiller sur les premiers cas réels : si l'action provoque une
> navigation avant la lecture du `MutationObserver`, celui-ci est perdu avec l'ancien
> contexte et la collecte redescend silencieusement à "aucune mutation" plutôt que de
> disclose explicitement cette limite. Le chantier principal reste la Phase 3B pour
> 3B.3/3B.4, mesurée avant construction ; la Phase 7 peut continuer à être préparée
> en parallèle pour les cas `STATIC_DOM`.
> Mise à jour 2026-09-13 (suite 9) : Phase 7 PARTIELLEMENT clôturée pour le
> sous-ensemble `stage="extraction"` — `Survey/autofix_worktree.py` +
> `tools/prepare_autofix_worktree.py` implémentés et validés. Ne lance aucun agent
> de coding, n'applique aucun patch, ne commit/push/merge rien : prépare
> uniquement une branche `autofix/<case_id>` et un worktree Git dédié, en amont
> d'un lancement manuel de Codex/Claude Code sur le `prompt.txt` déjà produit par
> la Phase 6. Éligibilité vérifiée intégralement avant tout effet de bord
> (`stage="extraction"`, `replay.verdict="REPRODUIT"`, `confidence_global="certain"`,
> `case_incomplete=false`, `prompt.txt` réel et non `MANUAL_REVIEW_REQUIRED.txt`,
> `case_id` cohérent entre manifest/diagnosis/dossiers et sûr comme composant de
> chemin/référence Git via `git check-ref-format`) — refus explicite avec la liste
> complète des raisons si une seule condition manque, jamais un état partiel.
> Restriction volontaire actée : `stage="action"` reste explicitement hors
> périmètre de cette phase, car le replay (3A/3B) ne réexécute jamais le
> dispatcher réel pour ces cases — un verdict `REPRODUIT` y est attendu par
> construction et ne constitue pas une preuve suffisante pour déclencher une
> préparation automatique d'espace de travail. Ce sous-ensemble restera bloqué sur
> la Phase 3C (et, pour l'automatisation complète du schéma `... → Codex → patch`
> du pipeline, potentiellement sur une décision ultérieure distincte : cette phase
> ne fait que préparer l'espace isolé, elle n'invoque aucun agent par programme).
> Stratégie Git strictement séquentielle sans fallback (branche créée depuis un
> `base_sha` figé une seule fois, jamais un `git checkout` qui bougerait le dépôt
> principal du développeur ; rollback ciblé de la seule branche créée par
> l'invocation en cours si `worktree add` échoue). Amélioration ajoutée après
> revue : la branche source courante (`git symbolic-ref` sur HEAD, ou
> `HEAD (detached)`) est rapportée dans la sortie CLI à titre purement informatif
> — jamais un critère de blocage, pour rester cohérent avec l'objectif "pas
> d'hypothèse fragile sur le workflow git du développeur". Point de vigilance
> mineur non bloquant, noté en revue : `PROTECTED_BRANCHES` ne peut structurellement
> jamais se déclencher (le nom de branche calculé est toujours préfixé
> `autofix/`) — défense en profondeur symbolique, pas une vraie protection
> supplémentaire ; à clarifier ou nettoyer si retouché. Le chantier principal
> reste la Phase 3B pour 3B.3/3B.4 ; la Phase 7 pour `stage="action"` reste
> en attente de 3C.
> Mise à jour 2026-09-22 : cas 1B collecté hors changement de phase — DataDiggers
> iControl, radio `attention_questions` (target_id=`group_cead5509389a`). Oracle
> `_checkbox_radio_false_negative_issue` (extension 1B.1 déjà en place) confirmé
> correct sur 3 captures live indépendantes (`dispatcher_false_negative`
> `dom_signal=checkbox_radio_checked`). Cause racine trouvée hors
> `action_validator.py` : bug dans `Survey/action_dispatcher.py` (stratégie
> `datadiggers_icontrol_radio`, résolution `_first_input_under()` incompatible
> avec l'expression XPath relative utilisée sur ce DOM) — corrigé et validé live
> (`apply ok=true reason=input_checked`, option demandée effectivement cochée à
> l'écran ; cf. BOT_EVOLUTION_MEMORY.md section DATADIGGERS ICONTROL et section
> « Cas 1B collecté » de ce document). Point de vigilance NON confirmé ajouté à
> la collecte 1B : `validate_actions()` ne vérifie jamais le DOM quand
> `dispatcher_success=true` — aucun cas réel ne le confirme à ce jour (un doute
> soulevé pendant ce diagnostic provenait d'une capture d'écran envoyée par
> erreur), ne pas patcher sans snapshot réel. Aucun changement de phase : le
> chantier principal reste la Phase 3B pour 3B.3/3B.4 ; la Phase 7 pour
> `stage="action"` reste en attente de 3C.
> Mise à jour 2026-09-24 : 3B.6 clos formellement. `collect_mutation_observer`
> (`Survey/browser_capsule.py`) expose désormais `observer_present` (booléen)
> dans son résultat, distinguant un observateur perdu (navigation avant
> collecte) ou jamais installé d'une absence réelle de mutation — limite
> constatée à l'implémentation mais jusque-là non disclosée dans l'artefact
> (cf. suite 8). Câblage vérifié dans `Survey/page_snapshot.py::_install_action_observer` :
> le dict complet retourné par `collect_mutation_observer` est transmis sans
> déstructuration partielle à `build_action_trace`, donc `observer_present`
> atteint bien `action_trace.json` sans être perdu en route. Décision confirmée
> sur 3B.3/3B.4 : volume de cases `NON_REJOUABLE` actuellement insuffisant pour
> qu'une mesure soit utile (cf. avertissement de cadrage de la Phase 3B) —
> restent différées, à réévaluer quand les cases s'accumulent naturellement via
> l'observabilité courante, pas avant et pas sur une estimation. Aucun
> changement de phase : le chantier principal reste la Phase 3B pour 3B.3/3B.4
> (en attente d'un volume de cases suffisant) ; la Phase 7 pour `stage="action"`
> reste en attente de 3C.
> Mise à jour 2026-09-24 (suite) : oracle checked-state étendu au rejeu statique.
> Nouvelle fonction additive `Survey/action_validator.py::_checkbox_radio_captured_state_false_negative_issue`, ajoutée à la chaîne de
> détection combinée existante (`_dispatcher_false_negative_issue`) sans modifier
> les détecteurs déjà en place. Cause du besoin : sur le driver statique du rejeu
> (3A), le détecteur checkbox/radio existant décline proprement (retourne un état
> non concluant plutôt qu'une exception) faute de méthode `is_checked()` sur le
> shim — donc jamais de faux positif, mais jamais de reclassification non plus pour
> ce type d'incident au rejeu. Le nouveau détecteur consulte à la place l'état déjà
> capturé au même instant que le HTML rejoué (`runtime_state.json`, produit par
> 3B.2), transmis en paramètre optionnel (`captured_option_states`) par
> `Survey/failure_replay.py` — `validate_actions()` et `_dispatcher_false_negative_issue`
> gagnent ce paramètre, `None` par défaut, sans changer le comportement du chemin
> live existant (qui ne le fournit pas). Validé sur un case réel
> (`20260924_180714_action_validation_failure`, widget radio QARTS_HIDDEN
> Decipher/LifePoints) : verdict de rejeu passé de `DIFFERENT` à `REPRODUIT`, même
> `failure_type` que l'origine. Ce détecteur constitue un premier pas, partiel,
> vers la « fonction oracle unique » déjà réclamée en Phase 3C.4 (le pari
> `dispatcher_success=false` mais état prouvant le succès, partagé entre validator
> live et replay) — partiel car il ne couvre que checkbox/radio via l'état déjà
> capturé, pas une réexécution réelle du dispatcher (3C.4 reste à construire pour
> ça). Aucun changement du chemin live de production. Le chantier principal passe
> à la Phase 3C.
> Mise à jour 2026-09-25 : Phase 3C PARTIELLEMENT clôturée — 3C.1 (isolation
> réseau) et première brique de 3C.2 (chargement du document principal)
> implémentés et vérifiés, dans un seul module `Survey/replay_browser.py`
> (`IsolatedReplayBrowser`), hors du chemin de production (jamais importé par
> `main.py`, `preselection/playwright_launcher.py` non touché). Aucune
> extraction, validation ni action n'est exécutée par ce module. 3C.1 : Chromium
> propre (`launch()`, profil éphémère, aucun user-data-dir/cookie partagé avec
> le bot), refus réseau systématique posé au niveau du BrowserContext
> (`context.route("**/*")` abandonne toute requête ; `route_web_socket("**/*")`
> ferme toute WebSocket ; service workers bloqués) — un seul mécanisme, pas de
> traitement au cas par cas. Chaque requête refusée est journalisée dans
> `blocked_requests` (bornée à 500, compteur total séparé) : une ressource
> référencée mais absente reste visible comme absente, jamais récupérée en
> ligne. `file://` est aussi refusé (vérifié). 3C.2 (première brique) :
> `load_case_document(case_dir)` charge le seul HTML principal figé du case,
> choisi par `failure_replay._pick_dom_file` réutilisé tel quel (une seule
> source de vérité stage -> fichier, aucun repli sur un autre fichier ;
> `ReplayBrowserError` sinon). Le document est servi depuis la mémoire sous une
> origine synthétique `.invalid`, seule réponse autorisée par le garde-fou et
> uniquement pour cette URL exacte ; les sous-ressources restent refusées.
> Choix de conception à connaître : la réponse porte une CSP `script-src 'none'`
> — les scripts du provider, déjà exécutés avant la capture, ne se rejouent pas
> sur un DOM déjà muté ; `page.evaluate()` n'y est pas soumis, la page reste
> interrogeable. Piège Playwright constaté : les handlers `route_web_socket`
> tournent directement sur la boucle asyncio (pas dans le greenlet de l'API
> sync), un `ws.close()` sync s'y bloque indéfiniment — le code retourne donc la
> coroutine `ws._impl_obj.close(...)` (attribut privé, à revérifier à tout
> changement de version de Playwright ; installée : 1.60.0). Vérification faite
> par scripts ponctuels (aucun test versionné) : http/IP/`file://`/`fetch`/
> `<img>`/WebSocket refusés, contenu local rendu, script du document non
> exécuté, erreur explicite si artefact absent ; service workers, popups et
> iframes non testés (mêmes garde-fous contexte, non vérifiés). Restent à faire :
> 3C.2 (frames, shadow roots ouverts, restauration de l'état runtime depuis
> `runtime_state.json`), 3C.3 (extraction/frame selection sur la page), 3C.4
> (actions, avec timeout et déclassement `TRACE_REPLAY`), puis 3D. Le chantier
> principal reste la Phase 3C.
> Mise à jour 2026-09-25 (suite) : Phase 3B — nouvelle capture 3B.9 (scripts
> externes) implémentée et vérifiée. Cause racine : `dump_page_snapshot` ne
> conservait que le HTML/CSS/MHTML, jamais le contenu des `<script src>` — un
> futur rejeu navigateur ne pourrait donc jamais exécuter ces scripts.
> `Survey/browser_capsule.py::collect_external_scripts` (additif, appelé depuis
> un bloc try/except unique ajouté dans `Survey/page_snapshot.py::
> dump_page_snapshot`, profils historique et `action_validation`) écrit
> `external_scripts.json` ; `Survey/failure_case_builder.py` le reconnaît
> (`_KNOWN_FILES`) et lui applique la sanitisation d'URL déjà en place
> (`_CAPSULE_JSON_FILES_TO_SANITIZE`). Stratégie unique : lecture depuis l'arbre
> de ressources déjà chargé par la page (CDP `Page.getResourceTree` +
> `Page.getResourceContent`, même mécanisme de session que le MHTML) — aucune
> requête réseau, navigation ni interaction, et fonctionne pour les scripts
> cross-origin (un `fetch()` depuis la page serait bloqué par CORS). Bornes :
> 60 scripts, 512 Ko par script, 4 Mo au total ; tolérant par script
> (`content` vide + `error` : `trop_volumineux`, `absent_de_l_arbre_de_ressources`,
> `cdp_erreur`, `budget_total_atteint`, `contenu_binaire`), jamais d'exception
> propagée. Chromium uniquement. Aucun changement du rejeu statique (3A) ni du
> Chromium isolé (3C) : matière première seulement. Limites actées : (a) seule
> l'URL est sanitisée, pas le contenu des scripts — un jeton codé en dur dans
> un script passerait tel quel dans `failure_cases/` ; (b) le retrait de la
> query string rend indiscernables deux scripts qui ne diffèrent que par elle,
> à garder en tête pour le futur rejeu ; (c) un script inséré puis retiré du DOM
> avant capture n'est pas collecté ; (d) vérifié par script ponctuel sur une
> vraie page Playwright (cross-origin OK, 600 Ko refusé, 404 isolé, sanitisation
> OK), aucun test versionné, `create_failure_case.py` non lancé de bout en bout.
> Le chantier principal reste la Phase 3C.
> Mise à jour 2026-09-25 (suite 2) : Phase 3B — 3B.10 (feuilles de style
> externes) implémentée et vérifiée, même situation et même correctif que
> 3B.9. Cause racine : `page_snapshot.py` ne reconstruit que les `<style>` depuis
> le CSSOM ; le contenu des `<link rel="stylesheet" href>` n'était sauvegardé
> nulle part. `Survey/browser_capsule.py::collect_external_stylesheets`
> (additif ; `collect_external_scripts` non modifiée) écrit
> `external_stylesheets.json` via un bloc try/except indépendant dans
> `Survey/page_snapshot.py::dump_page_snapshot` (l'échec de l'un des deux blocs
> n'affecte pas l'autre) ; `Survey/failure_case_builder.py` le reconnaît
> (`_KNOWN_FILES`) et lui applique la sanitisation d'URL existante. Même
> mécanisme (arbre de ressources CDP déjà chargé, aucune requête réseau), mêmes
> bornes (60 / 512 Ko / 4 Mo) et même format d'entrée `{url, size, content,
> error}` que 3B.9. Compromis assumé : la logique est dupliquée plutôt que
> factorisée avec la collecte des scripts, pour ne pas modifier celle déjà
> validée — à factoriser lors d'un futur nettoyage si un troisième type de
> ressource s'ajoute. Limites : `@import` et feuilles ajoutées hors `<link>` non
> collectés ; seules les URL sont sanitisées, pas le contenu CSS (ses `url(...)`
> internes peuvent porter des paramètres de session) ; vérifié par script
> ponctuel sur une vraie page Playwright (capture OK, 900 Ko refusé,
> `?tok=` retiré, `external_scripts.json` toujours produit ; le cas 404 n'est
> pas concluant car le serveur de test répondait 200 avec du HTML), aucun test
> versionné. Aucun changement du rejeu statique ni Chromium. Le chantier
> principal reste la Phase 3C.
> Mise à jour 2026-09-25 (suite 3) : Phase 3C.2 étendue — ressources externes
> capturées (3B.9/3B.10) servies dans le navigateur isolé, CSP relâchée quand au
> moins un script est servable. `IsolatedReplayBrowser.load_case_document` lit
> désormais aussi `external_scripts.json`/`external_stylesheets.json` du case
> (si présents) et enrichit le garde-fou réseau existant (`_block_request`,
> non remplacé, une branche ajoutée) pour servir chaque entrée à son URL EXACTE
> et au type de requête attendu (`script`/`stylesheet`) uniquement — toute autre
> requête reste refusée et journalisée comme avant. Une entrée sans contenu
> capturé n'est jamais servie ; une URL présente avec deux contenus différents
> (conséquence du retrait de la query string à la sanitisation, cf. 3B.9) est
> détectée comme ambiguë et n'est pas servie non plus, plutôt que de deviner
> laquelle. Quand au moins une ressource est servable, le document est servi à
> son URL d'origine (`meta.json`, déjà sanitisée) pour que ses références
> relatives se résolvent comme à la capture, sinon URL synthétique `.invalid`
> comme avant ce patch. La CSP `script-src 'none'` n'est levée que si au moins un
> script externe est servable (sinon comportement inchangé) ; un
> `Access-Control-Allow-Origin: *` est ajouté aux ressources servies, nécessaire
> car le document, désormais à sa vraie origine, rend ces requêtes réellement
> cross-origin du point de vue du navigateur. Limite actée : l'URL d'origine
> réutilisée vient de `meta.json`, donc sans sa query string (sanitisation
> Phase 2) — un script inline qui lirait `location.search` à l'exécution ne
> verrait pas la vraie query string de capture ; tension assumée entre fidélité
> d'exécution et retrait des données de session, non résolue. Validé sur un case
> réel (`20260925_091047_action_validation_failure`, Confirmit "Nepa") via script
> ponctuel : jQuery et le script du provider s'exécutent (`typeof window.jQuery
> === "function"`), la feuille de style capturée s'applique visuellement
> (capture d'écran), le script trop volumineux (3B.9) reste refusé comme prévu,
> une requête XHR non capturée (vérification anti-fraude du provider) est
> refusée et journalisée. Aucun test versionné. Aucun changement du rejeu
> statique (3A). Le chantier principal reste la Phase 3C.
> Mise à jour 2026-09-25 (suite 4) : bug de fidélité corrigé dans le rejeu
> statique (3A), trouvé en creusant un verdict `DIFFERENT` inattendu
> (`action_target_missing`) sur un case par ailleurs correctement rejouable.
> Cause racine : `Survey/dom_replay_shim.py::_inner_text_static` normalisait
> l'espace insécable (U+00A0, `&nbsp;`) comme une espace normale lors du calcul
> du texte visible d'un élément, alors qu'un navigateur réel la préserve dans
> `innerText`. Comme `Survey/dom_registry.py::make_target_id` hache ce texte
> pour produire le `target_id`, un seul caractère de différence suffisait à
> produire un identifiant différent de celui enregistré au moment de l'incident
> — la cible devenait introuvable au rejeu, alors que l'extraction elle-même
> avait bien retrouvé le bon bloc de question. Bug du rejeu uniquement,
> confirmé sans rapport avec l'extracteur ni avec `make_target_id` eux-mêmes
> (aucun des deux modifié). Corrigé en restreignant la normalisation des espaces
> du shim aux espaces ASCII (` \t\n\r\f`), U+00A0 explicitement exclu du
> collapse et du strip. Validé sur un case réel
> (`20260925_115552_action_validation_failure`, question contenant une espace
> insécable avant `?`, typographie française) : verdict de rejeu passé de
> `DIFFERENT`/`action_target_missing` à `REPRODUIT`, même `failure_type` que
> l'origine. Ce même case (widget radio QARTS_HIDDEN Decipher/LifePoints,
> provider `surveys.lifepointspanel.com`) porte déjà `external_scripts.json`/
> `external_stylesheets.json` — prêt pour tester en conditions JS réelles,
> via 3C.2 (suite 3), si le clic sur le widget visuel "rp" se resynchronise
> réellement, sans nouvelle capture. Le chantier principal reste la Phase 3C ;
> le bug de dispatch lui-même (widget "rp" Decipher/rowpicker qui ne se
> resynchronise pas après un clic sur le label natif caché, diagnostiqué —
> cf. suite 8 du 2026-09-13) reste non corrigé, volontairement reporté après
> la fiabilisation de la Phase 3.
> Mise à jour 2026-09-25 (suite 5) : Phase 3C.2 étendue — troisième type de
> ressource capturé et servi, contenu des requêtes XHR/fetch émises au
> chargement (`Survey/browser_capsule.py::collect_xhr_fetch_resources`,
> artefact `external_requests.json`). Cause : certains widgets (confirmé sur
> le widget radio QARTS "rp" Decipher/LifePoints) chargent leur configuration
> par XHR/fetch au chargement, pas par <script src>/<link stylesheet> — donc
> hors de portée des collecteurs 3B.9/3B.10. Vérifié empiriquement que
> Page.getResourceTree/getResourceContent (CDP, la stratégie de 3B.9/3B.10)
> ne liste aucun XHR/fetch sur un vrai Chromium — stratégie différente
> retenue : lister via `performance.getEntriesByType('resource')`
> (initiatorType fetch/xmlhttprequest), puis relire chaque réponse depuis le
> cache HTTP uniquement (`fetch(url, {cache:'only-if-cached'})` : un cache
> miss échoue au lieu de charger, aucune requête réseau nouvelle, jamais).
> Mêmes bornes que 3B.9/3B.10, tolérant par requête. Limite actée, non
> résolue : seule l'URL est sanitisée (retrait query string), pas le corps
> JSON de la réponse — une XHR qui porterait une vraie donnée de réponse
> plutôt qu'une configuration statique ne serait pas filtrée ; risque
> identifié, pas encore concrétisé sur un cas réel.
> `Survey/replay_browser.py` étendu en parallèle pour servir ce nouvel
> artefact au navigateur isolé (même garde-fou réseau, même principe
> d'URL/type de requête exacts que 3C.2 pour scripts/styles). Résultat,
> validé sur le case de référence (`20260925_211543_action_validation_failure`,
> widget radio QARTS "rp") : une fois la configuration XHR servie, React
> prend enfin possession du nœud du widget (`__reactFiber`/`__reactProps`
> présents, absents avant ce patch) et un clic réel dans le navigateur isolé
> déclenche une vraie navigation — première confirmation de bout en bout,
> Phase 3C, d'un widget interactif piloté par JS. Aucun changement du rejeu
> statique (3A). Le chantier principal reste la Phase 3C (état runtime dans
> la page reconstruite, frames, shadow roots, 3C.3, 3C.4 toujours à faire).
> Mise à jour 2026-09-25 (suite 6) : correctif de dispatch pour le bug
> diagnostiqué en suite 8 du 2026-09-13 — widget radio/checkbox QARTS "rp"
> Decipher/LifePoints (motif conteneur `sq-QARTS-container-`, déjà réservé
> mais jamais implémenté, cf. "Patterns exclus" des entrées kantar_rowpicker).
> Nouvelle stratégie nommée `click_qarts_widget_by_label`
> (Survey/input_checkbox.py), qui clique le div overlay visuel réel du widget
> (via un clic natif hover+click, pas un clic JS) au lieu du <label> natif
> caché ; vérifie la sélection via l'opacité du marqueur SVG plutôt que
> `.checked`. Câblée dans Survey/action_dispatcher.py, gardée par un nouveau
> flag `qarts_widget` sur le payload du registry. Aucune stratégie existante
> modifiée (click_kantar_rowpicker_radio, click_decipher_grid_radio_strict
> intacts) ; click_decipher_grid_radio_strict reste utilisé pour les grilles
> Decipher sans cet overlay. Validé empiriquement en conditions JS réelles
> (Phase 3C, cf. suite 5) : contrairement au clic sur le label caché
> (`.checked` bascule mais aucune mise à jour visuelle), un clic manuel sur
> l'overlay produit la mise à jour visuelle ET une vraie navigation.
> Point ouvert, non encore validé : le flag `qarts_widget` est posé
> inconditionnellement (`True`) par l'extracteur existant
> `_extract_qarts_hidden_answers_groups` (Survey/dom_extractors_decipher.py),
> sans vérifier que l'overlay `_rowpicker` existe réellement pour ce groupe
> précis — une modification du corps de cet extracteur, faite sans la
> validation explicite que ce chantier exige normalement avant tout
> changement d'un extracteur existant. Risque : un groupe qarts_hidden sans
> overlay `_rowpicker` (aucun cas de ce genre observé à ce jour) serait routé
> vers `click_qarts_widget_by_label`, qui échouerait sans jamais retomber sur
> `click_decipher_grid_radio_strict` — régression potentielle non confirmée,
> correctif proposé (vérifier la présence réelle de l'overlay à l'extraction
> plutôt que de la supposer) en attente de validation.
> Mise à jour 2026-09-25 (suite 7) : point ouvert de la suite 6 clos.
> `_extract_qarts_hidden_answers_groups` calcule désormais `has_qarts_overlay`
> par groupe (résolu via l'attribut `qartsqname` propre à chaque groupe pour
> reconstruire l'id exact de son conteneur, `sq-QARTS-container-{qname}`, puis
> vérifier la présence réelle de `div._rowpicker` à cet endroit précis — même
> critère que `click_qarts_widget_by_label`, réutilisé tel quel) au lieu du
> `True` fixe précédent. `qarts_widget` porte cette valeur calculée ; repli
> sûr à `False` sur toute exception. Diff minimal confirmé : rien d'autre dans
> la fonction n'a changé. Un groupe qarts_hidden sans overlay `_rowpicker`
> retombe donc à nouveau sur `click_decipher_grid_radio_strict`, comme avant
> l'introduction de `qarts_widget` (suite 6) — plus de risque de régression
> silencieuse pour ce cas, aucun exemple réel de ce cas rencontré à ce jour.
> Mise à jour 2026-09-25 (suite 8) : Phase 3C.2 close sur son dernier volet
> volontairement scopé — l'état runtime déjà capturé au même instant que le
> document (`runtime_state.json`, état "après" pour un case action) est
> maintenant restauré sur la page réelle une fois chargée : `checked`,
> `indeterminate`, `disabled`, `readOnly`, `selectedIndex`/`selected`, `value`
> — propriétés live jamais portées par le HTML sérialisé. Aucun xpath n'étant
> persisté dans un case, chaque élément est retrouvé par identité capturée
> (tag + className exact + texte visible + input natif imbriqué et sa valeur)
> et restauré seulement si cette identité est unique ; introuvable ou ambigu =
> ignoré et journalisé, jamais deviné. Aucun événement émis (un `change`
> pourrait déclencher un autosubmit) : seules les propriétés changent —
> limite assumée, l'affichage d'un widget JS piloté par son propre état n'est
> donc pas resynchronisé par cette seule restauration. Borné, tolérant par
> élément, jamais bloquant ; sans artefact, comportement inchangé. Frames et
> shadow roots ouverts restent hors périmètre — même raison que 3B.3/3B.4 :
> aucun case réel n'en dépend à ce jour, pas de construction spéculative.
> Mise à jour 2026-09-25 (suite 9) : Phase 3C.3 démarrée — première brique.
> Correction de nommage au passage : ce qui avait été annoncé comme "3C.4" au
> moment de le lancer est en réalité 3C.3 (extraction/registry/question_blocks,
> pas le dispatcher réel) — 3C.4 reste entièrement à faire. Nouvelle fonction
> `extract_case_blocks(page, case_dir)` dans `Survey/replay_browser.py` :
> exécute `dom_analyzer.analyze_dom(page)` tel quel (non modifié) sur la page
> réelle issue de `load_case_document`, peuple `DOM_REGISTRY` comme en
> production, et compare les blocs obtenus à `question_blocks.json` du case
> (mêmes `target_id`, diff champ par champ). Isolation entre cases : `DOM_REGISTRY`
> ET `_STABLE_TEXT_FIELD_LOCATOR` sont vidés avant chaque extraction — ce dernier
> survit volontairement aux rescans en production (une même page), mais fuirait
> d'un case à l'autre dans cet outil (pages différentes) ; extractions
> sérialisées par un verrou (état global du process). Comparaison non forcée :
> `comparable=false` si le case ou une cible rejouée dépend d'une frame (seul le
> document principal est chargé). Aucune modification de `dom_analyzer.py`,
> `dom_registry.py` ni `dom_frame_selector.py`.
> Mise à jour 2026-09-25 (suite 10) : Phase 3C.3, suite. `extract_case_blocks`
> fait maintenant tourner, pour les cases `stage="extraction"` uniquement,
> `question_block_validator.validate_question_blocks(blocks, driver=page)`
> (non modifié — même fonction déjà appelée telle quelle par le rejeu statique
> 3A) sur les blocs fraîchement extraits, avec la vraie page comme pilote.
> Résultat comparé à `validation_report.json` du case via le même vocabulaire
> de verdict que 3A (`REPRODUIT`/`DIFFERENT`/`NON_REPRODUIT`, mêmes
> `VERDICT_*`/`_report_failure_types` importés de `failure_replay.py`/
> `failure_case_builder.py`, pas réimplémentés) — une seule logique de verdict
> à travers 3A et 3C, pas deux. Comparaison non forcée sur dépendance à une
> frame, comme pour les blocs.
> Mise à jour 2026-09-25 (suite 11) : Phase 3C.3 considérée suffisamment
> validée, clôturée sur cette base — testée sur deux cases extraction réels,
> chacun instructif pour une raison différente :
> - `20260923_211155_extraction_validation_failure` (Qualtrics, carrousel
> checkbox) : `target_id`/question rejoués différents de l'origine. Cause
> confirmée par lecture directe du texte rejoué (pas supposée) : ce case,
> capturé avant 3B.9/3B.10, n'a aucun script à servir — sans JS, le carrousel
> empile toutes ses lignes au lieu d'en paginer une seule, et l'extracteur de
> question capture la première ligne du carrousel avec la vraie question.
> Artefact de l'ancienneté du case, pas un défaut de 3C.3. Reste un doute
> distinct, hors 3C.3 : l'incident d'origine (`choice_without_options`) est
> probablement une vraie condition de course en production (DOM scanné avant
> que le carrousel JS ait fini de peupler ses cases) — aucun rejeu ne peut la
> reproduire après coup, quel qu'il soit ; candidat pour une classification
> 3D dédiée plutôt qu'un cas à "corriger".
> - `20260911_160609_extraction_validation_failure` (Focaldata, cartes MUI
> radio) : le rejeu retrouve le bloc que l'extraction de production, avec
> JavaScript complet, n'avait pas trouvé (`missing_block`). Piste d'abord
> creusée (signal de validator `_focaldata_response_option_cards_signal`,
> lecture live confirmée du même DOM juste après l'échec de l'extraction
> principale — cohérent avec une course de rendu MUI), puis close autrement :
> confirmé par l'utilisateur que ce bug était déjà corrigé dans le bot avant
> ce jour. Le rejeu exécute le code actuel (déjà corrigé) sur le DOM figé
> d'avant correctif — succès attendu, confirmation incidente d'un "avant
> correctif échoue / après correctif réussit" via l'historique git plutôt
> qu'un test construit exprès. Bon point en faveur de 3C.3, pas un mystère.
> Décision : chantier principal passe à la Phase 3C.4.
> Mise à jour 2026-09-25 (suite 12) : Phase 3C.4 démarrée — première brique.
> Nouvelle fonction `execute_case_action(page, case_dir, budget_s)` dans
> `Survey/replay_browser.py` : exécute, pour un case `stage="action"`,
> `action_dispatcher.execute_actions_plan(page, actions, stop_on_navigation=True)`
> tel quel (non modifié, même point d'entrée que `survey_executor`) avec les
> actions de `actions_requested.json`, après `extract_case_blocks` sur la même
> page (même verrou, le dispatcher lit le registre global). Garde-fou de
> timeout obligatoire : un thread watchdog ferme la page à l'échéance (appel
> thread-safe sur la boucle asyncio de Playwright — débloque un `evaluate`/une
> attente en cours) ; un résultat tardif, succès ou échec, n'écrase jamais un
> verdict déjà déclaré `TIMEOUT` — vérifié explicitement (scénario : succès
> simulé après le budget → reste `TIMEOUT`). Statuts jamais confondus :
> `SUCCESS`/`FAILURE` (booléen du dispatcher), `TIMEOUT` (budget dépassé),
> `ERROR` (le dispatcher a levé), `NOT_EXECUTED` (précondition absente : cible
> hors registre, budget invalide, page sans les leviers d'abandon nécessaires),
> `NOT_APPLICABLE` (stage autre qu'action). Une tentative d'injection
> d'exception dans le thread du dispatcher (pour interrompre une boucle
> Python) a été essayée puis rejetée : elle atteint la boucle asyncio interne
> de Playwright et la bloque — la fermeture de page seule suffit pour les
> blocages Playwright, mais une boucle Python pure sans borne propre n'est pas
> interrompue (limite disclosée, pas masquée). `action_dispatcher.py` non
> modifié. Limite notée à ce stade : le dispatcher tournait sur le document
> post-action avec l'état "après" restauré, pas sur l'état pré-action — point
> repris et résolu en suite 13.
> Mise à jour 2026-09-25 (suite 13) : Phase 3C.4, suite — chargement
> pré-action ajouté (`load_case_document(case_dir, pre_action=True)`, cases
> `stage="action"` uniquement, `pre_action_dom.html` requis dans les artefacts,
> jamais de repli sur le post-action si absent). Mêmes ressources externes
> servies et même CSP relâchée que le chargement existant (code partagé, non
> dupliqué) ; restauration de l'état runtime explicitement non appliquée ici
> (cet état correspond à un autre instant, après l'action). **Validation
> décisive sur le case de référence Decipher/rowpicker
> (`20260925_211543_action_validation_failure`) : extraction sur le document
> pré-action retrouve `group_70d2fbdc16e2` identique à l'original
> (`comparaison identical=true`), puis `execute_case_action` rapporte `SUCCESS`
> en 1,019 s — et le clic est visuellement confirmé dans l'onglet ouvert, pas
> seulement rapporté.** C'est la première fois que la chaîne complète
> (extraction réelle → dispatcher réel corrigé → succès réel) est vérifiée de
> bout en bout sur le bug qui a motivé tout ce chantier (diagnostiqué en
> suite 8 du 2026-09-13, corrigé en suite 6/7 du 2026-09-25).
> Mise à jour 2026-09-25 (suite 14) : Phase 3C.4, suite — comparaison
> structurée avant/après ajoutée. Quand le dispatcher a rendu un verdict
> exploitable (`SUCCESS`/`FAILURE`, jamais après `TIMEOUT`/`ERROR`/
> `NOT_EXECUTED`/`NOT_APPLICABLE`), `execute_case_action` fait maintenant
> tourner `action_validator.validate_actions` (non modifié) sur la même page,
> avec le booléen réel de ce run et SANS `captured_option_states` (réservé au
> rejeu statique — ici le validator lit l'état live, ses détecteurs qui
> déclinaient systématiquement sur le shim statique peuvent enfin s'exécuter
> pour de vrai). Résultat comparé à `validation_report.json` via
> `_compare_validation`, réutilisée telle quelle (même fonction que 3C.3, une
> seule logique de comparaison). Validé sur le case de référence
> Decipher/rowpicker (pré-action) : extraction identique à l'origine,
> dispatcher réel corrigé → `SUCCESS`, validator → `NON_REPRODUIT`.
> Mise à jour 2026-09-25 (suite 15) : Phase 3C.4, suite — ambiguïté de
> vocabulaire corrigée, repérée immédiatement sur ce même `NON_REPRODUIT`
> (suite 14) : ce mot juge la fidélité d'un rejeu PASSIF (aucune exécution
> réelle) et y est déjà ambigu en soi (absence de détection vs absence réelle
> du problème) ; après une exécution RÉELLE du dispatcher, le réutiliser tel
> quel aurait rendu indiscernables "le rejeu ne voit plus le problème" et "le
> dispatcher l'a réellement corrigé". Nouvelle fonction `_action_outcome`
> (`Survey/replay_browser.py`) : traduit la comparaison de `_compare_validation`
> (non modifiée, toujours utilisée telle quelle) en un vocabulaire distinct et
> exclusif au chemin dispatch réel — retire explicitement la clé `verdict` du
> résultat exposé (elle ne doit pas être lue seule ici) et expose `outcome` :
> `CORRECTIF_CONFIRME` (dispatcher `SUCCESS` réel ET validator sans aucune
> issue), `BUG_PERSISTANT` (dispatcher `FAILURE` réel ET mêmes `failure_types`
> qu'à l'origine), `NON_CONCLUANT` (toute autre combinaison comparable — y
> compris les combinaisons contradictoires — jamais confondue avec les deux
> premières). Le chemin passif (extraction, `failure_replay.py`) garde son
> vocabulaire `REPRODUIT`/`NON_REPRODUIT`/`DIFFERENT` inchangé, aucune
> régression possible pour cet usage. Revalidé sur le même case : même run
> qu'en suite 14 (`SUCCESS`, extraction identique), mais rapporte maintenant
> `CORRECTIF_CONFIRME` sans ambiguïté au lieu du `NON_REPRODUIT` de suite 14.
> **Avec cette entrée, la chaîne complète est vérifiée de bout en bout, sans
> zone grise de vocabulaire, sur le bug Decipher/rowpicker qui a motivé tout
> ce chantier depuis suite 8 du 2026-09-13.**
> Mise à jour 2026-09-25 (suite 16) : Phase 3C.4, suite — `TRACE_REPLAY`
> implémenté. Après un `TIMEOUT` (page déjà fermée par le garde-fou de
> budget, plus aucune lecture live possible), nouvelle fonction
> `_trace_replay_fallback` (`Survey/replay_browser.py`) : rejoue
> `action_validator.validate_actions` (non modifié) avec `driver=None` et le
> paramètre déjà existant `captured_option_states` (`runtime_state.json`,
> capturé avant cette tentative — même mécanisme déjà exploité par le rejeu
> statique passif, jamais réimplémenté séparément). `dispatcher_success`
> repris du case d'origine (`validation_report.json`), jamais un verdict de
> cette tentative qui n'en a pas produit. Résultat exposé dans un champ
> distinct `ActionExecution.trace_replay` (`None` pour tout statut autre que
> `TIMEOUT`), avec son propre vocabulaire de fidélité
> `REPRODUIT`/`NON_REPRODUIT`/`DIFFERENT` (comme 3A — volontairement PAS
> `CORRECTIF_CONFIRME`/`BUG_PERSISTANT`/`NON_CONCLUANT`, puisqu'aucun
> dispatcher n'a réellement tourné cette fois). Statut du résultat jamais
> réécrit : reste `TIMEOUT`, ce repli l'enrichit sans le remplacer.
> `available=False` avec raison explicite si aucun `runtime_state.json`
> exploitable ou si `validate_actions()` lève — jamais un repli silencieux.
> Relu et vérifié ligne par ligne (portée respectée : seul `TIMEOUT`, pas
> `ERROR`/`NOT_EXECUTED` — choix conscient, pas une évidence si un jour on
> veut étendre). Pas encore observé sur un vrai dépassement de budget en
> conditions réelles (revue de code uniquement à ce stade).
> Mise à jour 2026-09-25 (suite 17) : vérification demandée de la fusion en
> fonction oracle unique (point resté ouvert en suite 15) — délégation déjà
> complète sur les trois chemins, aucune fusion à faire. `action_validator.py`
> porte seul la logique du pari (`_dispatcher_false_negative_issue`,
> `validate_actions`) ; chemin 1 (`failure_replay.py`, rejeu statique passif)
> et chemin 2 (`execute_case_action`, dispatch réel) délèguent tous deux
> intégralement, déjà confirmé en suite 15. Chemin 3 (repli après `TIMEOUT`,
> `_trace_replay_fallback` posée en suite 16) délègue tout autant — vérifié
> directement dans le code (appel à `validate_actions()` bien présent et bien
> invoqué). Une première vérification automatisée avait conclu à tort que ce
> troisième chemin n'appelait jamais le validator — confusion entre les
> champs `validation`/`validation_comparison` (qui restent `None` après
> `TIMEOUT`, à raison) et le fait que `validate_actions()` y est bien appelée,
> juste exposée sous un autre champ (`trace_replay`). Erreur corrigée après
> relecture directe du fichier plutôt que confiance aveugle dans le rapport —
> aucun changement de code nécessaire, la conclusion pratique (rien à
> fusionner) reste la même que celle envisagée avant cette vérification.
> Point clos : `_action_outcome` n'est pas une seconde implémentation du
> pari, seulement une traduction de vocabulaire sur un résultat déjà tranché
> par le module unique. **Avec cette entrée, 3C.4 est considérée close sur
> tout ce qui était prévu.**
> Mise à jour 2026-09-26 (suite 18) : Phase 7 étendue à `stage="action"`,
> maintenant que 3C.4 fournit une preuve exploitable pour ce stage. Choix de
> coût explicitement tranché : la réexécution réelle du dispatcher (Chromium,
> plusieurs secondes) tourne systématiquement dans `diagnose_failure_case`
> (Phase 4) pour tout case `stage="action"`, pas à la demande juste avant la
> Phase 7 — la Phase 4 devient plus lente sur ce stage, assumé.
> `Survey/failure_diagnosis.py` : nouveau champ `real_dispatch_replay`,
> strictement additif — `replay` (rejeu passif, `failure_replay.py`) reste
> calculé tel quel pour tous les stages, jamais remplacé. Pour
> `stage="action"` uniquement, `_attempt_real_dispatch_replay` appelle
> `Survey/replay_browser.py::execute_case_action` (non modifié, vérifié :
> `OUTCOME_FIX_CONFIRMED`/`OUTCOME_BUG_PERSISTS`/`OUTCOME_INCONCLUSIVE`,
> `ReplayBrowserError`, `_DEFAULT_DISPATCH_BUDGET_S` préexistaient déjà tous
> depuis la suite 15) sur `pre_action_dom.html`, sous le même budget de temps
> que ce worker impose déjà — jamais d'exception propagée (pré-requis
> absents, Playwright indisponible, ou toute autre erreur : `None`, jamais
> un résultat deviné). Signal exact retenu pour "certain" côté action :
> `real_dispatch_replay.validation_comparison.outcome="BUG_PERSISTANT"` —
> confirmation ACTIVE que le bug persiste sur le code non corrigé, jamais
> `CORRECTIF_CONFIRME` (ce serait le signal inverse, utile après un
> correctif, pas avant) ni une absence de détection. Sans cette
> confirmation active, `confidence_global` plafonne à `"plausible"` — même
> mécanisme de plafond que `manifest.incomplete=true`, appliqué en plus,
> pas une règle indépendante.
> `Survey/autofix_worktree.py::check_eligibility` : `stage` accepte
> maintenant `"extraction"` et `"action"` ; `replay.verdict="REPRODUIT"`
> reste exigé pour les deux ; pour `stage="action"` seulement, exigence
> supplémentaire sur `real_dispatch_replay.validation_comparison.outcome`
> (`"BUG_PERSISTANT"` requis, garde défensif si le champ est absent) —
> jamais un critère plus permissif que pour l'extraction. Pas encore
> chronométré sur un vrai run (`tools/diagnose_failure.py` sur un case
> action va devenir sensiblement plus lent — attendu, à confirmer).
> Mise à jour 2026-09-26 (suite 19) : point de vigilance data de la Phase 6
> fermé — champ `value` d'un issue de `validation_report.json`, jusqu'ici
> jamais sanitisé contrairement à `meta.json`/aux DOM HTML du même snapshot.
> Portée précisée avant le patch : le risque ne concerne que les champs de
> saisie libre (la donnée réellement tapée par le répondant, ex. le cas de
> référence IFOP zip2city cité par le plan) — pas un libellé d'option
> radio/checkbox/dropdown, déjà prédéfini par le sondage lui-même et pas
> plus sensible que le texte de la question, déjà reproduit sans filtre.
> Nouvelle fonction `_sanitize_validation_report` (`Survey/failure_case_builder.py`),
> appliquée au moment de la copie sanitisée (Phase 2), comme `meta.json` juste
> au-dessus dans le même fichier — pas en Phase 4/6, pour que tout consommateur
> futur de `validation_report.json` en bénéficie. Rapprochement par option
> prédéfinie réelle (`question_blocks.json` du même snapshot, jamais
> recalculé), pas par `itype` déclaré (pouvant être erroné) : `value`
> conservée telle quelle seulement si elle correspond, après normalisation
> casse/espaces stricte, à une option connue pour ce `target_id` ; sinon
> retirée (remplacée par `null`). Toute ambiguïté (target_id absent de
> l'issue, bloc introuvable, `question_blocks.json` indisponible/sans
> options exploitables) traitée comme potentiellement sensible — jamais
> laissée passer par défaut. Portée strictement limitée à `issues[].value`
> (pas un parcours générique du document comme `_sanitize_meta`/
> `_sanitize_capsule_json`, ni l'une ni l'autre modifiées) ; copie profonde
> indépendante, jamais de mutation en place ; bornée (`_MAX_SANITIZED_ISSUES`),
> repli côté sûr (retrait) au-delà du budget. Retrait documenté dans les
> avertissements du manifeste, même convention que `meta.json`, jamais
> silencieux. **Avec cette entrée, le dernier point ouvert de la liste
> dressée en fin de chantier 3C est fermé.**
> Mise à jour 2026-09-26 (suite 20) : Phase 3D démarrée —
> `Survey/replayability_classifier.py` + `tools/classify_replayability.py`.
> Classification en lecture seule à partir des seuls signaux déjà présents
> dans `diagnosis.json` (Phase 4) — aucun replay/dispatcher/navigateur
> recalculé ici. `stage="extraction"` : `STATIC_DOM` seulement si
> `replay.verdict="REPRODUIT"` ET `evaluate_declined` nul (un `REPRODUIT`
> obtenu malgré des signaux déclinés ne prouve pas que la structure DOM
> seule suffisait), sinon `UNDETERMINED`. `stage="action"` :
> `real_dispatch_replay.status` en `SUCCESS`/`FAILURE` → `BROWSER_CAPSULE`
> (dispatcher réel réexécuté jusqu'à un verdict exploitable) ;
> `status="TIMEOUT"` avec `trace_replay.available=true` → `TRACE_REPLAY` ;
> tout le reste → `UNDETERMINED`. `EXTERNAL_NON_REPLAYABLE` structurellement
> défini (vocabulaire du plan) mais jamais produit — aucun signal de ce
> type n'existe encore dans le pipeline (donnée de session, captcha, shadow
> DOM fermé), l'inventer aurait été deviner. Asymétrie découverte et
> correctement traitée, non anticipée dans le prompt : `stage="extraction"`
> n'a aujourd'hui aucun signal de vérification par navigateur réel exposé
> dans `diagnosis.json` (`replay_browser.py::extract_case_blocks` existe,
> mais `failure_diagnosis.py` ne l'invoque que pour `stage="action"`) — un
> case extraction hors `STATIC_DOM` retombe donc sur `UNDETERMINED`, jamais
> deviné en `BROWSER_CAPSULE` par symétrie avec le stage action. Ordre
> d'exécution révisé par rapport à l'intention initiale du plan ("la Phase 4
> devra lire ce champ") : 3D tourne après 4 (lit `diagnosis.json` déjà
> produit), pas avant — sans conséquence réelle aujourd'hui puisque
> `EXTERNAL_NON_REPLAYABLE` n'est encore jamais produit, donc aucun cas où
> la Phase 4 aurait besoin d'éviter un calcul à cause d'un verdict 3D. Sortie
> `classification.json` distincte, jamais écrite dans `diagnoses/` — jamais
> d'exception levée (diagnostic incomplet/inattendu → `UNDETERMINED` avec la
> raison exacte).
> Mise à jour 2026-09-26 (suite 21) : lancé sur trois cases réels
> (`20260911_160609`/Focaldata MUI extraction, `20260923_211155`/Qualtrics
> carrousel extraction, `20260925_211543`/Decipher rowpicker action) — voir
> suite 22 pour l'extension qui a suivi côté extraction, motivée directement
> par ce qu'un de ces trois runs a révélé.
> Mise à jour 2026-09-26 (suite 22) : `Survey/failure_diagnosis.py` étendu —
> `real_extraction_replay`, symétrique de `real_dispatch_replay` pour
> `stage="extraction"`. Motivé par un doute concret sur le case Focaldata MUI
> (suite 21) : son rejeu passif était `NON_REPRODUIT`, mais l'utilisateur
> doutait que le bug soit vraiment corrigé plutôt que simplement absent du
> DOM figé — exactement l'ambiguïté que `cause.justification` reconnaît déjà
> elle-même sans trancher ("soit... soit..."). Nouvelle fonction
> `_attempt_real_extraction_replay` : fait tourner
> `replay_browser.py::extract_case_blocks` (non modifié) sur le document du
> case dans le Chromium isolé — `dom_analyzer.analyze_dom()` et
> `question_block_validator.validate_question_blocks()` réellement réexécutés
> avec layout/CSS réels, contrairement au rejeu passif (DOM statique sans
> JS/layout). Résultat conservé dans `real_extraction_replay`, à côté de
> `replay` — jamais à sa place. Volontairement purement informatif : aucune
> nouvelle règle de plafond adossée (contrairement à `real_dispatch_replay`
> côté action) — `cause_level`/`confidence_global` restent dérivés du seul
> replay passif pour ce stage, vérifié inchangé sur les deux cases relancés.
> Résultat sur le case Focaldata : le bloc à 7 options est bien retrouvé sur
> layout réel, mais `target_id` diffère toujours de l'original et le
> validator dit encore `NON_REPRODUIT` — cohérent avec le rejeu passif, mais
> **ne tranche pas** le doute d'origine : un DOM déjà figé, même rejoué dans
> un vrai navigateur, ne peut par construction jamais rejouer une vraie
> condition de course de production (la fenêtre de course n'existe plus une
> fois le DOM capturé) — limite déjà actée pour le rejeu statique (suite 8 du
> 2026-09-11), qui s'applique donc également ici, pas propre au shim lxml.
> Résultat sur le case Qualtrics (carrousel, capturé avant 3B.9/10/11,
> scripts=off confirmé) : confirme, cette fois via le chemin officiel plutôt
> qu'un script ad hoc, exactement l'explication déjà trouvée manuellement —
> sans JS, le carrousel empile ses lignes, la question rejouée les absorbe.
> Suite logique délibérément non incluse ici (un patch à la fois) :
> `replayability_classifier.py` ne lit pas encore `real_extraction_replay` —
> `stage="extraction"` hors `STATIC_DOM` reste `UNDETERMINED` pour l'instant.
> Mise à jour 2026-09-26 (suite 23) : Phase 7 complétée (traçabilité) + Phase 8
> clôturée. Gap comblé côté Phase 7 : `prepare_autofix_worktree` ne se
> contentait que d'un `print()` de son résultat, sans laisser de trace sur
> disque — cassant le principe de lecture seule/artefact déjà écrit suivi par
> les phases 2 à 6. Nouveau `write_worktree_manifest`
> (`Survey/autofix_worktree.py`) persiste désormais `case_id`/`branch`/
> `base_sha`/`worktree_path`/`source_branch`/`prompt_path` sous
> `autofix_worktrees/<case_id>/worktree.json`, même convention JSON que les
> phases précédentes (schema_version, horodatage, avertissements), refus
> explicite si l'artefact existe déjà sans `--force` — vérifié avant toute
> mutation Git, pas après. `WorktreeResult` gagne un champ `manifest_path`,
> strictement additif ; comportement Git (branche/worktree) inchangé.
> Phase 8 : `Survey/static_validator.py` + `tools/validate_patch_static.py`.
> Lecture seule sur le seul artefact que produit la Phase 7 (`worktree.json`)
> — jamais `manifest.json`/`diagnosis.json` rouverts, jamais l'éligibilité
> Phase 7 recalculée. Sous-ensemble de fichiers vérifiés déterminé par
> comparaison Git (`git diff --name-only base_sha` ∪ nouveaux fichiers non
> trackés), jamais l'ensemble du dépôt — conforme au choix tranché à
> l'ouverture de ce chantier. Quatre vérifications dans l'ordre, chacune avec
> son propre budget de temps explicite : (1) compilation isolée
> (`python -m py_compile`, capture aussi un argument dupliqué — déjà un
> SyntaxError CPython natif) ; (2) import isolé par sous-processus,
> `sys.executable` réutilisé tel quel (même venv que l'outil, jamais un
> `python` résolu au hasard sur PATH) ; (3) lint minimal via Ruff, choisi
> plutôt que pyflakes pour sa capacité à évoluer par simple config plutôt que
> changement d'outil (décidé à l'ouverture du chantier) — `--isolated`,
> sélection figée à `F821`/`F822`/`F823` (erreurs réelles uniquement, jamais
> de règle de style) ; F831 (argument dupliqué), envisagé initialement,
> n'existe pas comme règle sélectionnable dans la version installée — non
> grave, ce cas est déjà couvert par (1), documenté plutôt que masqué ; (4)
> tests unitaires déjà associés à chaque fichier modifié, si une convention
> fichier-source → fichier-de-test existe déjà. Investigation menée avant
> écriture, documentée dans le module (même principe que la Source 2 de
> `context_selector.py`) : ce dépôt ne contient à ce jour aucune suite de
> tests versionnée, ni `tests/`, ni `conftest.py`, ni dépendance pytest/ruff
> dans `requirements.txt` ; `test.py`/`test3c3.py`/`test_diag.py` à la racine
> sont des scripts manuels pilotant un vrai navigateur (`input()` bloquant),
> pas des tests unitaires, aucune convention établie. Le détecteur reconnaît
> néanmoins plusieurs conventions Python usuelles et s'activera de lui-même le
> jour où l'une apparaît réellement, sans nouveau patch sur ce module —
> `convention_found=false` n'est jamais à lui seul un motif de rejet, seul un
> test déjà existant qui échoue réellement fait échouer la phase, au même
> titre qu'un échec de compilation, d'import ou de lint. Verdict tracé sous
> `autofix_static_validations/<case_id>/validation_static.json`
> (`ACCEPTED`/`REJECTED`), même convention JSON que les phases précédentes.
> Aucun test live déclenché, quel que soit le verdict — conforme au principe
> de la phase. Ne modifie jamais le worktree, la branche autofix, ni aucun
> artefact d'une phase antérieure. Le chantier principal passe à la Phase 9
> (replay automatique du bug), avec la même limite déjà actée pour
> `stage="action"` (voir « Limite actuelle » de la Phase 9 ci-dessous) tant
> qu'un verdict `TRACE_REPLAY` n'est pas encore distingué d'un `REPRODUIT` par
> un score de confiance dédié (Phase 12).
> Mise à jour 2026-09-26 (suite 24) : Phase 9 clôturée.
> `Survey/patch_replay.py` + `tools/replay_patch.py`. Lecture seule sur les
> Phases 4/7/8 : lit uniquement diagnosis.json, worktree.json et
> validation_static.json déjà produits — n'en recalcule aucun, ne rouvre
> jamais manifest.json directement (seul un mécanisme de rejeu déjà existant,
> réutilisé tel quel, le lit indirectement). Préconditions vérifiées toutes
> ensemble, jamais la première seule : validation_static.verdict="ACCEPTED"
> requis (jamais recalculé) ; worktree.json complet et worktree_path pointant
> réellement vers un dépôt Git (.git présent) ; diagnosis.stage dans
> {extraction, action} avec le signal avant-patch déjà établi par la Phase 4
> (replay.verdict="REPRODUIT" en extraction,
> real_dispatch_replay.validation_comparison.outcome="BUG_PERSISTANT" en
> action) — jamais revérifié, seulement relu ; case_id cohérent entre les
> quatre sources (diagnosis.json, worktree.json, validation_static.json,
> noms de dossiers fournis) et sûr comme composant de chemin. Convention CLI
> alignée sur la Phase 8 : worktree.json et validation_static.json pris en
> argument comme CHEMINS DE FICHIER complets, jamais un dossier qui les
> contiendrait — diagnosis_dir reste un dossier, comme le fait déjà
> Survey/autofix_worktree.py::check_eligibility pour ce même artefact.
> Résolution de la racine de paquet du worktree : `_resolve_package_root`
> (Survey/static_validator.py, Phase 8) réimportée telle quelle plutôt que
> réimplémentée — vérifiée applicable sans changement, ne dépendant que du
> worktree lui-même. Exécution du rejeu dans un SOUS-PROCESSUS Python neuf,
> jamais dans le process appelant : ce dernier a déjà importé
> Survey.replay_browser/Survey.failure_replay depuis le dépôt principal, donc
> sys.modules les garderait en cache et ferait tourner le code non corrigé
> plutôt que celui du worktree — script généré passé en fichier temporaire,
> chemins en argv jamais interpolés dans le texte du script, sys.executable
> réutilisé (jamais un "python" résolu au hasard sur PATH), un seul budget de
> temps explicite par stage (60s rejeu statique extraction ; budget dispatcher
> + marge de 60s côté action), timeout traité comme échec contrôlé jamais un
> processus laissé pendre. Mécanisme de rejeu réutilisé tel quel par stage,
> jamais réimplémenté : `Survey.failure_replay.replay_failure_case` pour
> l'extraction (même vocabulaire REPRODUIT/NON_REPRODUIT/DIFFERENT/
> NON_REJOUABLE que le signal avant-patch) ; exactement la même séquence que
> `Survey/failure_diagnosis.py::_attempt_real_dispatch_replay`
> (IsolatedReplayBrowser -> load_case_document(pre_action=True) ->
> extract_case_blocks -> execute_case_action) pour l'action, fonctions
> publiques non modifiées de Survey/replay_browser.py. Vocabulaire de verdict
> après patch repris tel quel (Survey.replay_browser.OUTCOME_*, déjà calculé
> par execute_case_action côté action), jamais une seconde échelle de
> confiance ; côté extraction, seule traduction manquante ajoutée, symétrique
> de _action_outcome, sans réimplémenter le pari dispatcher_success/DOM qui ne
> s'applique pas à ce stage. Seul CORRECTIF_CONFIRME valide le patch
> (patch_validated=true) — un TIMEOUT reste NON_CONCLUANT même si
> trace_replay porte un signal favorable, jamais une confirmation active (cf.
> Phase 3D) ; DIFFERENT/NON_REJOUABLE/erreur de sous-processus/sortie non
> JSON traités uniformément comme NON_CONCLUANT, jamais une réussite
> partielle. Point ouvert, découvert en écrivant ce module et non anticipé
> par le prompt d'origine : au moment de commencer ce patch, ni worktree.json
> ni Survey/static_validator.py n'existaient encore dans ce dépôt — les deux
> ont été fusionnés dans cette branche pendant l'écriture de ce module ;
> schémas/CLI vérifiés sur le code réellement fusionné, jamais devinés.
> Portée explicitement exclue, documentée plutôt que masquée : le rejeu de
> cas historiques voisins (suite de non-régression DOM) reste un
> sous-chantier différé — cette phase ne rejoue que le case ciblé par le
> worktree. Verdict tracé sous patch_replays/<case_id>/patch_replay.json,
> même convention JSON que les phases précédentes. Le chantier principal
> passe à la Phase 10 (test live attach contrôlé) pour les cas hors portée du
> replay local, et à la Phase 11 (validation anti-régression) pour la suite
> de cas historiques volontairement différée ici.
> Mise à jour 2026-09-26 (suite 25) : Phase 10 clôturée.
> `Survey/live_validator.py` + `tools/validate_patch_live.py`. Garde-fou de
> sécurité vérifié à DEUX endroits indépendants (défense en profondeur) :
> `AUTOFIX_LIVE_VALIDATE` doit valoir EXACTEMENT `"1"` (égalité stricte,
> volontairement plus rigide que le parsing tolérant déjà utilisé par
> `SURVEY_OBSERVABILITY` en Phase 1A — un garde-fou de sécurité ne doit
> jamais s'activer par accident) ; vérifié dans `tools/validate_patch_live.py`
> avant même l'import d'`argparse`, puis revérifié indépendamment dans
> `check_preconditions()` si le module est appelé hors de cette façade.
> Éligibilité : `patch_replay.json` (Phase 9) avec `refused=False` et
> `outcome="NON_CONCLUANT"` EXACTEMENT — ni `CORRECTIF_CONFIRME` (déjà
> validé) ni `BUG_PERSISTANT` (patch déjà connu mauvais) ; `worktree.json`
> (Phase 7) revérifié comme un vrai worktree Git ; `diagnosis.json`
> (Phase 4) avec un `stage` cohérent avec celui déjà porté par
> `patch_replay.json` ; `--cdp-endpoint` fourni par l'opérateur, jamais
> lancé/configuré par l'outil. `validation_static.json` (Phase 8) n'est pas
> relu ici : son acceptation est déjà garantie par l'existence même de
> `patch_replay.json`, revérifier serait redondant.
> Racine de paquet du worktree reprise telle quelle de la Phase 8/9 ;
> exécution en sous-processus Python neuf (même raison qu'en Phase 9 :
> `sys.modules` garderait sinon en cache le code non corrigé du dépôt
> principal). Connexion `connect_over_cdp` uniquement (jamais `launch()`),
> page déjà ouverte retrouvée par énumération (jamais `new_page()`, jamais
> de navigation) ; plusieurs pages candidates → désambiguïsation par l'URL
> d'origine du case, sinon refus explicite listant les URLs plutôt qu'une
> page devinée.
> Signatures de `Survey/replay_browser.py` vérifiées avant écriture (pas
> supposées) : `extract_case_blocks`/`execute_case_action` n'exigent qu'une
> API Playwright standard sur `page`, déjà satisfaite par une page obtenue
> via CDP — aucune des deux fonctions n'a eu besoin d'être modifiée. Pour
> `stage="extraction"`, divergence documentée avec la Phase 9 : le replay
> statique (`Survey.failure_replay`, driver lxml sur DOM figé) est
> incompatible avec une page live — remplacé ici par `extract_case_blocks`
> seul, exactement le mécanisme déjà utilisé par
> `_attempt_real_extraction_replay` (Phase 4, suite 22).
> **Point de vigilance sérieux, découvert en écrivant ce module et non
> anticipé par le prompt d'origine, disclosé plutôt que masqué : pour
> `stage="action"`, `execute_case_action` (Phase 3C.4, non modifiée) ferme
> elle-même la PAGE si son propre budget interne est dépassé — comportement
> déjà existant, sans conséquence sur un Chromium isolé jetable, mais réel
> sur une page CDP live : un TIMEOUT du dispatcher ferme réellement l'onglet
> de l'opérateur/du répondant. Ce module ne le neutralise pas (aurait exigé
> de modifier Survey/replay_browser.py, interdit par ce chantier) ; seule
> atténuation : un budget conservateur et cette disclosure explicite, pour
> que l'opérateur lance ce test en connaissance de cause. Aucun risque
> équivalent côté extraction (lecture DOM + validation, aucune mutation).
> À traiter avant tout usage réel de la Phase 10 sur un case stage="action" :
> soit accepter le risque documenté ci-dessus au cas par cas, soit rouvrir ce
> chantier pour neutraliser la fermeture de page côté Survey/replay_browser.py
> (hors périmètre de ce patch-ci).**
> Jamais de `browser.close()`/`context.close()`/`page.close()` sur la
> session CDP par ce module lui-même (seul `sync_playwright().stop()` en fin
> de script, qui ferme la connexion locale, jamais le navigateur distant).
> Une seule tentative par invocation, jamais de boucle interne — un
> deuxième essai après nouvelle correction reste une invocation manuelle
> complète du cycle. Vocabulaire de sortie repris tel quel
> (`OUTCOME_FIX_CONFIRMED`/`OUTCOME_BUG_PERSISTS`/`OUTCOME_INCONCLUSIVE`),
> seul `CORRECTIF_CONFIRME` valide le patch. Verdict tracé sous
> `live_validations/<case_id>/live_validation.json`, même convention JSON
> que les phases précédentes. Avec cette clôture, **1A → 10 sont désormais
> tous au moins partiellement implémentés** ; le chantier principal passe à
> la Phase 11 (validation anti-régression) et à la fermeture de la 3D/3B.3/
> 3B.4 encore ouvertes, sans compter le point de vigilance ci-dessus.
> Mise à jour 2026-09-26 (suite 26) : Phase 11, partie A clôturée (hash
> d'intégrité des fonctions gelées). `Survey/extractor_integrity_gate.py` +
> `tools/check_extractor_integrity.py`, complément déterministe à
> `Survey/extractor_integrity.py`/`.json` (script de hash SHA256 par
> fonction protégée, fourni tel quel, ajouté à `Survey/` — jamais
> réimplémenté, jamais modifié). Registre fourni corrigé avant intégration :
> une clé dupliquée (`Survey/input_slider.py::set_sliderpoints`, même hash
> que `input_slider.py::set_sliderpoints`) retirée — avec la racine
> `worktree_path/Survey` déjà identifiée comme seule cohérente avec la
> quasi-totalité des clés du registre (`../preselection/...` remontant vers
> le dossier frère de `Survey/`), cette clé en double aurait cherché
> `Survey/Survey/input_slider.py`, introuvable, et rejeté systématiquement
> tout patch par ailleurs propre dès le premier run.
> Portée explicitement limitée à cette partie A : le rejeu de DOM
> historiques/génériques représentatifs (`regression_cases/`, décrit plus
> loin dans ce document pour la Phase 11) reste un sous-chantier différé,
> faute de bibliothèque de cas déjà curée — même principe que les
> sous-chantiers différés en Phase 9/10.
> Précondition orthogonale à la Phase 9/10 (ne dépend que de la Phase 8) :
> `worktree.json` (Phase 7, worktree Git réel) + `validation_static.json`
> (Phase 8) `verdict="ACCEPTED"` — inutile de vérifier l'intégrité d'un
> patch qui ne compile même pas. `Survey/extractor_integrity.py` ET
> `Survey/extractor_integrity.json` doivent exister réellement dans le
> worktree à la racine de paquet résolue (`_resolve_package_root`, Phase 8,
> réutilisée telle quelle) — sinon refus explicite plutôt qu'une racine
> devinée.
> Chargement du code gelé DU WORKTREE, jamais du dépôt principal : le
> registre est relu depuis `Survey/extractor_integrity.json` du worktree
> (le patch a pu légitimement y ajouter des entrées) ; `extractor_integrity.py`
> est chargé dynamiquement (`importlib.util.spec_from_file_location`) sous
> un nom de module dédié — jamais `"Survey.extractor_integrity"` — pour ne
> jamais lire un module déjà en cache dans `sys.modules` provenant du dépôt
> principal. Contrairement à la Phase 9/10, résolu SANS sous-processus :
> `extractor_integrity.py` n'a aucune dépendance hors stdlib, un chargement
> direct suffit et est exigé (jamais un sous-processus avec parsing de
> texte). `_load_registry`/`_hash_function` appelées telles quelles ensuite,
> jamais réimplémentées ; leur seule présence sur le module chargé est
> elle-même vérifiée avant usage (refus explicite sinon, jamais de repli).
> Budget de temps explicite sur le parcours du registre
> (`DEFAULT_TIME_BUDGET_S=30s`) : au-delà, les entrées restantes comptent
> comme des erreurs explicites (`budget_exceeded`), jamais un passe-droit
> silencieux sur ce qui n'a pas pu être vérifié. Verdict strict : un hash
> différent (fonction protégée modifiée) ET une fonction/fichier
> introuvable (fonction protégée supprimée/renommée) sont tous deux des
> motifs de rejet — jamais un passe-droit. Verdict tracé sous
> `extractor_integrity_checks/<case_id>/extractor_integrity_check.json`,
> même convention JSON que les phases précédentes. Le chantier principal
> passe à la Phase 11, partie B (rejeu de `regression_cases/`, différée
> jusqu'à curation d'une bibliothèque de DOM représentatifs) ou à la
> Phase 12 (score de confiance du patch), désormais alimentable par les
> verdicts déjà produits par les Phases 8/9/10/11-A.
> Mise à jour 2026-09-26 (suite 27) : Phase 12 clôturée. `Survey/confidence_score.py`
> + `tools/score_patch_confidence.py`. Différence assumée et documentée par
> rapport aux Phases 7 à 11 : celles-ci refusent avant tout effet de bord
> dès qu'une précondition manque (elles s'apprêtent à faire quelque chose de
> risqué — Git, un vrai navigateur, une vraie page CDP) ; cette phase-ci n'a
> AUCUN effet de bord, seulement une lecture et un calcul — sa valeur vient
> de toujours produire un verdict, même dégradé (MEDIUM/REJECT), plutôt que
> de refuser dès qu'une pièce manque. Seule une véritable erreur d'usage
> reste bloquante (`ConfidenceScoreError`, jamais un résultat dégradé) :
> case_id ou branch incohérents entre les artefacts/chemins fournis, ou
> aucun case_id exploitable du tout — toute autre absence/incohérence de
> contenu dégrade le critère concerné à MISSING/NOT_RUN, jamais un crash.
> Quatre critères indépendants, jamais un booléen simple
> (PASS/FAIL/INCONCLUSIVE/MISSING, et NOT_RUN spécifiquement pour la
> validation live — une absence normale et attendue, jamais confondue avec
> un échec) : validation statique (Phase 8) et intégrité des fonctions
> gelées (Phase 11-A) via le même vocabulaire ACCEPTED/REJECTED, vérifié
> identique dans les deux modules avant d'écrire la traduction ; correctif
> confirmé sur le case ciblé dérivé par défaut de patch_replay.json
> (Phase 9), sauf si son outcome est resté NON_CONCLUANT ET que
> live_validation.json (Phase 10) existe et porte refused=false, auquel cas
> c'est SON outcome qui prévaut pour ce seul critère — un verdict Phase 9
> déjà tranché (CORRECTIF_CONFIRME/BUG_PERSISTANT) n'est jamais réexaminé
> par la Phase 10 ; validation live rapportée indépendamment, pour
> transparence complète, mais n'entrant dans la décision que via ce même
> mécanisme de préséance (jamais une cinquième branche testée seule).
> Décision par règles ORDONNÉES, jamais une formule pondérée (conforme à la
> demande d'origine) : statique != PASS -> REJECT ; intégrité = FAIL ->
> REJECT (toujours dominant, même correctif confirmé par ailleurs) ;
> correctif = FAIL -> REJECT ; correctif = PASS (statique/intégrité déjà
> acquis à ce stade) -> HIGH ; sinon MEDIUM avec le détail exact du critère
> bloquant dans `reason`. Nuance explicitée dans le module, non anticipée
> mot pour mot par le prompt d'origine mais conforme à son intention :
> l'intégrité fonctionne en VETO (seul un FAIL réel bloque), jamais en
> confirmation positive requise comme la validation statique et le
> correctif confirmé — une intégrité MISSING (Phase 11-A jamais lancée) ne
> bloque donc pas à elle seule un HIGH, mais reste toujours visible telle
> quelle dans `criteria`, jamais masquée par le verdict global.
> Avertissement systématique, non conditionnel, présent dans `warnings`
> quel que soit le verdict : le critère d'intégrité ne couvre à ce jour que
> le hash des fonctions gelées (Phase 11, partie A) — la Partie B (rejeu de
> DOM représentatifs) n'existe pas encore, donc un HIGH ne garantit pas
> l'absence de régression comportementale, seulement l'absence de
> modification détectée du code gelé. Verdict tracé sous
> `confidence_scores/<case_id>/confidence_score.json`, même convention
> JSON que les phases précédentes. Avec cette clôture, **1A → 12 — le cœur
> du système tel que défini par le plan lui-même — sont désormais tous au
> moins partiellement implémentés** ; le chantier principal passe aux
> étapes 13 à 20 (automatisation opérationnelle), à la Phase 11 partie B
> dès curation d'une bibliothèque regression_cases/, ou à la fermeture des
> sous-chantiers encore ouverts (3D/3B.3/3B.4, point de vigilance Phase 10).
> Mise à jour 2026-09-26 (suite 28) : point de vigilance de la Phase 10
> fermé (fermeture réelle de la page live sur timeout, stage="action").
> Exception délibérée et documentée à la règle de lecture seule habituelle :
> `Survey/replay_browser.py::execute_case_action` (et son helper interne
> `_run_with_deadline`) sont modifiés — autorisé explicitement parce que cette
> fonction n'est PAS une fonction protégée du bot (absente d'`extractor_integrity
> .json`) : c'est l'infrastructure de test d'autofix construite par ce chantier
> lui-même (Phases 3C.4/9/10), pas un extracteur ni le dispatcher de
> production. Modification strictement ADDITIVE : nouveau paramètre
> `close_page_on_timeout: bool = True`, défaut préservant EXACTEMENT le
> comportement actuel pour tout appelant qui ne le fournit pas (Phases
> 3C.4/9, Chromium isolé jetable, aucune régression possible par
> construction du défaut) ; seul le geste de fermeture de page à
> l'échéance du watchdog est conditionné, jamais le calcul ni la détection
> du dépassement de budget. `Survey/live_validator.py` (Phase 10) appelle
> désormais explicitement `close_page_on_timeout=False` : un TIMEOUT du
> dispatcher pendant un test live ne ferme plus la page CDP distante.
> Conséquence réelle, non anticipée mot pour mot par le prompt d'origine
> mais documentée plutôt que masquée : sans ce watchdog, le dispatcher
> bloqué ne se débloque alors plus que par ses propres budgets internes —
> c'est désormais le budget global du sous-processus (déjà existant côté
> Phase 10, `_SUBPROCESS_MARGIN_S`) qui borne effectivement l'opération
> dans ce cas, pas ce watchdog. Docstrings et avertissement JSON de la
> Phase 10 corrigés en conséquence (l'ancienne affirmation "la page live a
> déjà été fermée par execute_case_action" aurait été fausse une fois ce
> patch appliqué — retirée, remplacée par la description du nouveau
> comportement). Phase 9 (`Survey/patch_replay.py`) non touchée, continue
> sur son comportement par défaut inchangé (Chromium isolé jetable, fermer
> sa page reste sans conséquence). Limite de vérification disclosée plutôt
> que masquée, cohérente avec celle déjà actée en Phase 10 : aucun
> Chromium/point CDP réel disponible dans cet environnement pour un test de
> bout en bout du nouveau paramètre.
> Mise à jour 2026-09-26 (suite 29) : Phase 13 clôturée (validation humaine
> simplifiée, via Telegram — recevable sur n'importe quel appareil : PC,
> téléphone, tablette). `Survey/human_review.py` + `tools/notify_human_review.py`
> + `tools/check_human_review.py`. Infrastructure Telegram existante réutilisée
> telle quelle (variables d'environnement telegram_bot_token/telegram_chat_id,
> déjà utilisées ailleurs dans ce dépôt — Management/notifier.py, launch.py,
> Survey/survey_executor.py, Cash/*.py, platforms/*.py, vérifié avant
> d'écrire ce module plutôt que supposé) — aucune nouvelle variable
> introduite. Management/notifier.py::send_telegram() non réutilisé tel
> quel (ne supporte ni reply_markup ni la récupération du message_id) :
> client HTTP minimal dédié, stdlib urllib uniquement, une seule stratégie
> de transport.
> Polling (getUpdates), jamais un webhook — pas de serveur public à exposer,
> cohérent avec l'architecture actuelle. Telegram met en file d'attente les
> callback_query côté serveur : une décision prise à tout moment ("en
> marchant dans la rue") est récupérée telle quelle à la prochaine
> invocation de check_human_review.py, sans exiger de processus persistant
> — cohérent avec le principe déjà en place que ce pipeline reste des
> façades CLI actionnées manuellement (cf. Phase 7), jamais un démon.
> Précondition stricte pour notifier (Partie 1) : confidence_score.json
> (Phase 12) avec confidence="HIGH" exactement — MEDIUM/REJECT ne
> déclenchent jamais de notification, déjà visibles via les sorties CLI
> existantes. Message composé à partir de diagnosis.json (symptôme, cause)
> et confidence_score.json (critères), jamais le diff du patch. Prudence
> notable, non anticipée mot pour mot par le prompt d'origine mais dans son
> esprit : le symptôme utilise volontairement `symptom.failure_types` (noms
> de catégories) plutôt que `symptom.issues` (qui recopierait une donnée de
> répondant déjà présente en amont), pour ne dépendre d'aucune sanitisation
> antérieure. Refus explicite (jamais un envoi silencieux en double) si
> pending.json/decision.json existe déjà sans --force.
> Encodage case_id <-> callback_data : une seule stratégie, jamais un repli
> conditionnel — hash SHA256 tronqué à 16 caractères hex (limite Telegram de
> 64 octets vérifiée, jamais supposée), décodage par recherche symétrique du
> même hash parmi les dossiers de case déjà connus sous human_reviews/
> (jamais un décodage inverse, impossible par construction).
> Partie 2 (check_human_review.py) : une seule interrogation getUpdates par
> invocation, offset persisté avancé seulement après que TOUTES les
> décisions du lot ont été durablement écrites ou explicitement ignorées —
> jamais avant. Cas gérés sans jamais planter : callback pour un case
> inconnu (avertissement, mise à jour quand même considérée traitée) ;
> décision déjà enregistrée pour ce case (la première fait foi, callback
> suivant ignoré). Bonus best-effort, sans risque pour la décision déjà
> écrite : le message Telegram d'origine est édité pour retirer les boutons
> et afficher la décision prise, et answerCallbackQuery est appelé pour
> lever l'indicateur de chargement côté client — tout échec de ces deux
> gestes est journalisé en debug, jamais bloquant.
> Portée confirmée strictement limitée à la notification et la capture de
> la décision (`decision.json`, APPROVED/REJECTED) : aucun merge, commit, ni
> modification d'un worktree autofix déclenché ici — reste le périmètre des
> Phases 15/16. Le chantier principal passe aux étapes 14 à 20, à la
> Phase 11 partie B dès curation d'une bibliothèque regression_cases/, ou à
> la fermeture des sous-chantiers encore ouverts (3D/3B.3/3B.4).
> Mise à jour 2026-09-26 (suite 30) : Phase 14 clôturée (proposition
> automatique, jamais collée automatiquement, d'entrée BOT_EVOLUTION_MEMORY.md).
> `Survey/bem_proposal.py` + `tools/propose_bem_entry.py`. Déclencheur exact :
> decision.json (Phase 13) avec decision="APPROVED" — REJECTED, ou Phase 13
> jamais déclenchée, refusent explicitement, jamais une proposition. Point
> noté par Codex, non une précondition inventée : confidence_score.json
> (Phase 12) est structurellement déjà garanti exister à ce stade (Phase 13
> exige déjà confidence="HIGH" avant de pouvoir notifier) — vérifié ici avec
> la même rigueur que les autres artefacts, pas une garantie supplémentaire
> supposée.
> Détection des fichiers réellement modifiés : Survey/static_validator.py::
> _git_changed_paths/_filter_existing_python_files/_resolve_package_root
> (Phase 8) réutilisées telles quelles, jamais réimplémentées. Limite héritée
> assumée et disclosée : un fichier .py supprimé par le patch est filtré par
> _filter_existing_python_files (réutilisée sans modification) — ses
> fonctions supprimées ne sont donc jamais énumérées, signalé explicitement
> en avertissement plutôt que masqué.
> Détection des fonctions ajoutées/modifiées/supprimées : même technique que
> Survey/extractor_integrity.py::_find_function_source (AST, segment source
> exact décorateurs inclus, hash SHA256) — mais ce fichier n'est ni importé
> ni modifié : nouvelle fonction d'énumération complète
> (_enumerate_top_level_functions, module + méthodes de classes top-level,
> jamais les imbrications) écrite dans le nouveau module, comparant le
> contenu à base_sha (git show) au contenu courant du worktree. Stratégie
> unique, sans repli textuel : un échec de parsing AST (worktree ou
> base_sha) est signalé explicitement, jamais deviné.
> Croisement avec context_selection.json (Phase 5) : la reason déjà calculée
> est reprise telle quelle pour un fichier anticipé ; un fichier modifié mais
> non anticipé par la Phase 5 est signalé explicitement, jamais omis.
> Brouillon composé dans le format EXACT de l'en-tête de
> BOT_EVOLUTION_MEMORY.md (### nom, Fichier, Bug corrigé, Correction,
> Patterns couverts, Patterns exclus, Diagnostic associé, Statut) —
> uniquement des faits déjà établis (fichiers/fonctions réellement modifiés,
> symptom/cause de la Phase 4, confidence de la Phase 12) ; le titre lui-même
> est construit uniquement à partir des noms de fonctions réellement
> détectées comme ajoutées/modifiées, jamais une prose inventée. "Patterns
> couverts"/"Patterns exclus" (jugement humain requis) portent un marqueur
> explicite "[À COMPLÉTER — ...]", jamais une supposition qui aurait l'air
> d'un fait. Sortie : bem_entry_proposal.md (le brouillon lui-même) +
> bem_proposal.json (traçabilité), sous bem_proposals/<case_id>/ — n'écrit
> JAMAIS dans BOT_EVOLUTION_MEMORY.md lui-même. Le chantier principal passe
> aux Phases 15/16 (commit/merge semi-automatique, consommateurs naturels de
> decision.json et bem_entry_proposal.md), à la Phase 11 partie B dès
> curation d'une bibliothèque regression_cases/, ou à la fermeture des
> sous-chantiers encore ouverts (3D/3B.3/3B.4).
> Mise à jour 2026-09-26 (suite 31) : Phases 15 et 16 clôturées (commit
> automatique, merge semi-automatique — jamais le merge lui-même).
> Phase 15 : `Survey/patch_commit.py` + `tools/commit_patch.py`. Précondition :
> confidence_score.json (12) confidence="HIGH" (réutilisé tel quel comme
> seule source de vérité pour patch/régression/live PASS, jamais redérivé) ;
> decision.json (13) decision="APPROVED" ; worktree.json (7) désignant un
> worktree Git réel ET la branche effectivement checked-out (garde contre une
> manipulation manuelle entre Phase 7 et cette phase) ; branch hors
> PROTECTED_BRANCHES (Survey/autofix_worktree.py, réutilisée telle quelle).
> "BEM mise à jour" vérifiée par un FAIT Git observable (Survey/
> BOT_EVOLUTION_MEMORY.md parmi les fichiers modifiés depuis base_sha,
> réutilisant _git_changed_paths/_resolve_package_root de la Phase 8), jamais
> une déclaration — --skip-bem-check-reason exige une raison explicite non
> vide pour les patchs n'ayant légitimement pas vocation à une entrée BEM.
> Refus si un commit portant déjà "case_id=<...>" en ligne exacte existe dans
> le log Git de la branche (correspondance exacte, jamais une sous-chaîne).
> Sujet de commit mécanique par défaut (noms de fonctions réellement
> ajoutées/modifiées, depuis bem_proposal.json de la Phase 14 si fourni et
> cohérent, sinon depuis les fichiers committés) — jamais une formulation
> inventée ; --message permet à l'opérateur de la remplacer. git add -A puis
> git commit -F - (message transmis sur stdin, jamais interpolé en argument) ;
> worktree déjà propre → traité comme déjà committé (sha courant rapporté),
> jamais un commit vide artificiel. Jamais de push, jamais de merge.
> Phase 16 : `Survey/merge_review.py` + `tools/propose_merge.py`, et
> généralisation ADDITIVE de `Survey/human_review.py` + `tools/
> check_human_review.py` (comportement de la Phase 13 vérifié inchangé :
> send_review_request garde exactement sa signature/son comportement).
> Raison impérative de cette généralisation, pas une préférence de style :
> Telegram ne fournit qu'un seul flux getUpdates par bot — deux pollers
> indépendants avec deux offsets indépendants se voleraient mutuellement les
> mises à jour dès qu'ils tournent tous les deux. Un seul poller
> (check_human_review.py), un seul offset partagé, chaque callback routé par
> son préfixe (kind) vers human_reviews/ (Phase 13, inchangé) ou
> merge_reviews/ (Phase 16, nouveau) — jamais deux flux concurrents sur le
> même bot. Plomberie Telegram partagée extraite dans une fonction interne
> réutilisable (_send_two_button_review) plutôt que dupliquée.
> Précondition Phase 16 : commit_result.json (15) avec commit_sha non vide
> (commit réussi ou déjà committé) ; worktree.json (7) pour la branche cible
> du merge, reprise telle quelle (source_branch), jamais supposée/codée en
> dur. Message Telegram : branche autofix, branche cible, sha/sujet du
> commit — jamais le diff complet. Décision capturée dans
> merge_reviews/<case_id>/decision.json via le même mécanisme que la
> Phase 13. Portée confirmée : cette phase ne déclenche JAMAIS elle-même un
> git merge, un push, ni une modification de la branche cible — la
> confirmation reste un signal à vérifier manuellement par l'opérateur avant
> d'exécuter le merge lui-même. Avec cette clôture, le cœur du pipeline
> autofix (1A à 16) est entièrement construit, de la détection à la
> proposition de merge ; seul le geste de merge réel reste manuel. Le
> chantier principal passe aux Phases 17-20 (automatisation opérationnelle,
> secondaire par rapport au cœur selon le plan lui-même), à la Phase 11
> partie B dès curation d'une bibliothèque regression_cases/, ou à la
> fermeture des sous-chantiers encore ouverts (3D/3B.3/3B.4).
> Mise à jour 2026-09-26 (suite 32) : Phase 17 — décision explicite de NE
> PAS construire le mécanisme de merge automatique tel que décrit par le
> plan ("quand les statistiques deviennent bonnes") : aucune statistique
> réelle n'existe encore, la Phase 16 venant d'être close dans cette même
> session — construire l'automatisation avant les "plusieurs semaines
> d'observation" que la Phase 16 réclame elle-même aurait été la sauter.
> Construit à la place le seul prérequis légitime : un outil de
> statistiques, purement en lecture seule, jamais un déclencheur.
> `Survey/autofix_metrics.py` + `tools/report_autofix_metrics.py`.
> `failure_cases/` retenu comme seule liste exhaustive de case_id (tout
> case y a un dossier dès la Phase 2) — jamais dérivée d'un autre dossier
> de phase, potentiellement partiel. Pour chaque case, l'artefact de
> chaque phase suivante (4, 6, 7, 8, 9, 10, 11-A, 12, 13, 14, 15, 16) est lu
> s'il existe, jamais supposé présent ; son absence compte comme "phase non
> atteinte", jamais une erreur — seule une racine failure_cases/ absente
> reste une vraie erreur d'usage. Vocabulaire d'artefact (verdict/outcome/
> confidence/decision) relu dans le code de chaque phase avant d'être
> agrégé, jamais deviné.
> "Validés du premier coup" défini précisément et exposé tel quel dans le
> rapport (pas seulement dans le code) : outcome=CORRECTIF_CONFIRME (Phase
> 9) SANS qu'un live_validation.json (Phase 10) n'existe pour ce case —
> une définition parmi d'autres défendables, mais explicite et vérifiable.
> Décisions humaines (Phases 13/16) rapportées telles quelles
> (APPROVED/REJECTED), jamais reformulées en "vrai positif"/"faux positif"
> — l'outil ne peut garantir cette équivalence. Répartition par module
> (modules_likely_involved, Phase 4) explicitement présentée comme un
> proxy grossier, jamais une catégorisation sémantique du bug (aucun
> libellé de catégorie inventé). "Régressions" explicitement marquée NON
> MESURABLE avec les artefacts actuels (nécessiterait une boucle de
> rétroaction production → patch mergé, qui n'existe pas) — jamais un
> chiffre deviné ni un zéro silencieux.
> Sortie en instantané horodaté sous autofix_metrics/<horodatage>/
> metrics_report.json, jamais un fichier unique écrasé — cet outil est
> fait pour tourner à répétition sur plusieurs semaines et préserver
> l'historique des tendances (suffixe numérique si deux runs tombent dans
> la même seconde). Ne construit aucun mécanisme de merge automatique,
> aucune liste de catégories de confiance, aucune logique de décision — le
> déclencheur réel de la Phase 17 reste en attente de plusieurs semaines de
> données réelles produites par cet outil.
> Mise à jour 2026-09-26 (suite 33) : deux outils additifs construits, HORS
> de la numérotation 1A-20 (à la demande de l'opérateur, en réaction aux
> Phases 18-20 — cf. section dédiée « Outils complémentaires » plus bas pour
> le détail complet) :
> (1) Sécurité du parallélisme, en deux parties — `Survey/parallel_safety.py`
> + `tools/check_parallel_safety.py` (avant lancement Codex, niveau fichier,
> approximatif par construction) et `tools/check_function_overlap.py` (après
> patchs produits, avant merge, niveau fonction, précis — réutilise
> intégralement `Survey/bem_proposal.py::_detect_changed_files`, jamais
> réimplémentée). BOT_EVOLUTION_MEMORY.md explicitement exclu de la
> comparaison (présent dans presque tout `context_selection.json`, un
> conflit dessus est un texte, pas un vrai risque de code). Ajouté et
> supprimé/modifié comptent tous deux comme changement réel d'une fonction.
> (2) Déduplication stricte de cases — `Survey/case_grouping.py` + `tools/
> group_duplicate_cases.py`. Critère retenu (choix explicite de
> l'opérateur, plus permissif écarté) : identité stricte de signature
> (module + ensemble de matched_signals), jamais un simple recouvrement —
> un case rattaché à plusieurs signatures distinctes est une ambiguïté,
> retiré de tout regroupement automatique plutôt qu'assigné arbitrairement.
> Groupe matérialisé comme un failure_case + diagnosis.json SYNTHÉTIQUES
> (copie du case représentatif, case_id remplacé par group_id) — consommés
> tels quels par les Phases 5 à 16 sans aucune modification de leur code.
> group_id dérivé de la signature (pas du case_id du représentant), pour
> que le même bug reconnu plus tard reproduise le même group_id — germe
> exact du "Bot A → Bot B : déjà supporté" de la Phase 19, mais sans
> aucune infrastructure fleet nécessaire pour ce mécanisme précis (il opère
> une fois les cases déjà présents localement, quelle que soit leur
> origine). Limite disclosée : Phase 9 ne rejoue le patch que contre le
> représentant, jamais les N membres — se raccroche au sous-chantier déjà
> différé "rejeu de cas historiques voisins" (Phase 9). Groupes gelés une
> fois formés (jamais régénérés/étendus) — extension incrémentale d'un
> groupe existant explicitement différée, documentée plutôt que masquée.
> Le transport fleet lui-même (upload/import depuis ~100 machines de prod
> vers la machine de dev, cf. échange précédent) reste à ce jour un prompt
> rédigé mais NON implémenté — Phase 20 n'est donc pas close.
> Mise à jour 2026-09-26 (suite 34) : transport fleet implémenté — le
> maillon manquant de la Phase 20 est comblé. `Survey/fleet_case_upload.py`
> (Partie A, tourne sur chaque machine de prod) + `tools/upload_fleet_cases.py` ;
> `Survey/fleet_case_import.py` (Partie B, machine de dev) + `tools/
> import_fleet_cases.py` ; extension additive de `Survey/autofix_metrics.py`
> (Partie C).
> Conventions R2 vérifiées avant écriture plutôt que devinées : seul
> client R2 déjà existant dans ce dépôt (`Management/snap_uploader.py`,
> boto3, endpoint Cloudflare S3-compatible) réutilisé à l'identique côté
> construction du client et résolution de machine_id
> (`Management.guards.runtime_guard`, repli "unknown") ; nouveau namespace
> `FLEET_R2_*` dédié (jamais réutilisé `SNAP_R2_*`, bucket distinct sans
> rapport), toutes les variables requises, jamais de repli en dur.
> Vérification positive après upload (sujet du point de vigilance soulevé
> par l'opérateur) : chaque objet est revérifié par `head_object` avant que
> le marqueur local `fleet_upload_state.json` (`verified=true`) ne soit
> écrit — jamais sur la seule confiance dans le code de retour de
> `put_object`. Nettoyage local différé (sous-partie A2) : ne supprime
> jamais un case sans ce marqueur vérifié, quel que soit son âge ; délai de
> rétention configurable (`--retention-days`, défaut 7 jours),
> `--no-cleanup` pour le désactiver entièrement ; chaque suppression
> individuellement journalisée, jamais groupée/silencieuse.
> Côté import : structure de clé `{prefix}/{machine_id}/{case_id}/...`
> reconstruite depuis les objets R2 réels, jamais supposée ; un case_id
> porté par plusieurs machine_id distincts est traité comme une origine
> ambiguë, jamais devinée. `case_id` revalidé via
> `Survey.autofix_worktree._is_safe_case_id` avant toute écriture disque —
> une donnée relue depuis un stockage partagé n'est jamais supposée sûre
> par construction, même si elle ne devrait provenir que de machines de la
> fleet. Téléchargement partiel/en échec entièrement nettoyé localement
> (même principe que `failure_case_builder.py`). `fleet_origin.json`
> (machine_id, uploaded_at réel — lu depuis `LastModified` de l'objet R2,
> jamais un horodatage auto-déclaré transporté séparément —,
> `live_validation_possible=false`) écrit en sidecar, jamais dans
> `manifest.json`. Aucune suppression déclenchée côté import : le nettoyage
> prod reste entièrement du ressort de la Partie A2, sur son propre délai,
> indépendant du moment où le dev importe.
> Partie C : extension purement additive de `Survey/autofix_metrics.py` —
> nouvelle section `phase20_fleet_transport` (cases d'origine fleet vs
> locale, cas fleet NON_CONCLUANT en Phase 9 signalés "Phase 10 non
> applicable" plutôt que comptés comme ambigus) ; la définition déjà
> existante de "validés du premier coup"/Phase 10 (Phase 17) a été
> affinée en cohérence, pour ne compter le "NON_CONCLUANT en attente de
> Phase 10" que parmi les cases d'origine LOCALE — un cas fleet
> NON_CONCLUANT n'a jamais été, et ne sera jamais, éligible à la Phase 10
> (aucun `--cdp-endpoint` valide n'existe pour une machine distante),
> le confondre aurait faussé ce compteur.
> Phase 20 est désormais close : le schéma "PROD BOTS → stockage des
> failure_cases → LOCAL/DEV AUTOFIX WORKER" du plan est entièrement
> opérationnel.
> Mise à jour 2026-09-27 (suite 35) : REVIREMENT DÉLIBÉRÉ sur trois garde-fous
> manuels, à la demande explicite de l'opérateur — et sur trois points, l'avis
> initial était mal calibré. (1) Le lancement de l'agent de coding n'a plus
> besoin d'un humain : la garantie ne vient pas du clic mais de tout ce qui
> suit (Phases 8, 9, 11-A, 12 -> confidence=HIGH), qui existe déjà et ne
> dépend d'aucune intervention. (2) L'entrée BEM n'est plus un brouillon à
> coller : l'opérateur ne relisait déjà pas ce que Claude Code y écrivait ;
> c'est désormais l'agent lui-même qui l'écrit, dans le même prompt que le
> patch. (3) Exiger en plus un `git merge` manuel après une confirmation
> Telegram n'ajoutait rien (la vraie décision a lieu au clic) ; le merge local
> s'exécute désormais seul. Ce qui RESTE manuel, seul point réellement
> difficile à annuler : le push vers un dépôt partagé et tout déclenchement de
> release. Les mentions "ne merge jamais", "jamais collé automatiquement" et
> "aucune invocation automatique d'agent" des Phases 7/14/16 et de la suite 31
> décrivent l'état AVANT cette suite ; elles restent vraies pour le module
> concerné pris isolément, mais ne décrivent plus le pipeline dans son
> ensemble.
> (A) Codex écrit l'entrée BEM : `Survey/prompt_generator.py` (Phase 6) reçoit
> une ligne fixe additive dans ACTION REQUISE — après implémentation et
> vérification, ajouter une entrée dans Survey/BOT_EVOLUTION_MEMORY.md au
> format exact de son en-tête — appliquée à TOUS les prompts, jamais
> conditionnelle. Filet de sécurité dans `Survey/patch_commit.py` (Phase 15) :
> si l'entrée manque (fait Git déjà vérifié) et que --diagnosis-dir +
> --context-selection-dir sont fournis, le brouillon mécanique de
> `Survey/bem_proposal.py` (Phase 14, non modifiée) est ajouté automatiquement
> en fin de BEM, précédé d'un marqueur explicite ("Entrée auto-générée —
> Codex n'a pas rédigé cette entrée, patterns couverts/exclus non validés"),
> pour qu'un futur diagnostic distingue une entrée validée d'une entrée de
> secours. Sans ces deux options, comportement strictement inchangé.
> (B) Merge automatique : `Survey/merge_executor.py` + `tools/
> execute_confirmed_merge.py`. Précondition : merge_reviews/<id>/decision.json
> APPROVED, worktree réel, commit_result.json avec commit_sha, source_branch
> hors PROTECTED_BRANCHES (protection RÉELLE ici — source_branch est purement
> informatif en Phase 7 et pourrait valoir "main"), jamais "HEAD (detached)".
> Première phase du chantier qui agit sur le dépôt PRINCIPAL et son checkout
> réel plutôt que sur le worktree isolé — d'où : dépôt principal exigé
> entièrement propre avant tout basculement (jamais de stash automatique),
> `git merge --no-ff` avec marqueur "case_id=<id>" (idempotent : un merge déjà
> fait donne ALREADY_MERGED, jamais un doublon), conflit réel -> `git merge
> --abort` puis signalement, jamais de résolution automatique. Jamais de push.
> Sortie : merge_results/<id>/merge_result.json.
> (C) Registre de stabilité prouvée : `Survey/extractor_stability.py` +
> `tools/report_extractor_stability.py`, complément de BEM (jamais un
> remplacement), lecture seule. Relit le contenu COMPLET de
> Survey/extractor_integrity.json à chaque commit qui le touche, comparé clé
> par clé — jamais un parsing de diff, invalidé en pratique par un renommage de
> clé déjà survenu. Par fonction : stable depuis quel commit, nombre de
> valeurs de hash distinctes jamais portées, trou dans l'historique, et
> incidents diagnostiqués DEPUIS la dernière modification (les incidents
> antérieurs ne concernent plus la version actuelle). Jamais un pourcentage
> (aucune source ne compte les succès) ; historique non exploitable = raison
> explicite, jamais un zéro silencieux. Limite à connaître : l'historique
> Git du registre ne compte que 4 commits — la preuve de stabilité ne couvre
> que la période APRÈS la création du registre, pas l'avant ; les hashs
> initiaux fixent l'état du code au moment du seed, sans prouver qu'il n'avait
> pas été retouché avant.
> (D) Orchestrateur : `Survey/autofix_orchestrator.py` + `tools/
> run_autofix_pipeline.py`. Lance Claude Code en mode headless puis enchaîne
> les Phases 8, 9, 11-A, 12, 13 (fonctions d'écriture existantes importées
> telles quelles, jamais réimplémentées ni appelées via leurs CLI) jusqu'à la
> notification Telegram si confidence=HIGH. Traite jusqu'à --max-cases (défaut
> 5) cases UN À LA FOIS, jamais en parallèle. Syntaxe de la CLI vérifiée
> contre la version réellement installée (`claude --help`) : le flag --cwd
> suggéré dans l'échange préalable n'existe PAS dans cette version — l'ERREUR
> venait de la description initiale, non d'un défaut de l'orchestrateur ;
> le répertoire du worktree est fixé par le paramètre `cwd=` du sous-processus,
> équivalent fonctionnel exact. Prompt transmis par entrée standard (pas en
> argument : limite de longueur/quoting d'un prompt long multi-lignes),
> --output-format json, --permission-mode acceptEdits explicite, outils
> restreints à "Read Edit Write Grep Glob" (pas de Bash : le prompt demande un
> patch, les tests étant couverts par les phases suivantes). Un seul essai,
> jamais de retry automatique.
> Contrôle de parallélisme actif avant chaque lancement (check_pre_launch_safety
> réutilisée) ; un case en collision est reporté, jamais lancé quand même.
> Limites disclosées : (i) la Phase 7 (préparation du worktree) n'est PAS
> déclenchée par l'orchestrateur — elle reste une précondition ; les Phases
> 4, 5, 6, 7 sont donc encore à lancer séparément case par case avant qu'il
> prenne le relais. (ii) run_result.json écrit (succès OU échec) rend le case
> inéligible à une reprise automatique : un échec transitoire d'une phase
> suivante exige une relance manuelle. (iii) Est "en vol" TOUT worktree
> présent, y compris déjà mergé — définition volontairement large qui, avec
> le temps, reportera de plus en plus de cases ; à resserrer maintenant que
> merge_result.json existe. (iv) Sans Bash, l'agent ne peut pas vérifier son
> propre patch avant de rendre la main : compromis sécurité/taux de réussite
> au premier essai, ajustable via --allowed-tools.
> Effet sur la Phase 18 : le lancement automatique de l'agent (mode headless,
> sans attach live) en constitue désormais la première brique ; la boucle
> complète en attach reste hors périmètre.
> Cette suite est décrite à partir des artefacts déposés ; les Phases 6, 14, 15
> et 16 ont été étendues/complétées, aucune autre phase modifiée.

> Mise à jour 2026-09-27 (suite 36) : `Survey/autofix_orchestrator.py` et
> `tools/run_autofix_pipeline.py` étendus (deux chantiers successifs sur le
> même fichier, lancés l'un après l'autre pour ne pas se marcher dessus).
> (1) Définition de "en vol" resserrée — corrige la limite (iii) de la suite
> 35. Un worktree n'est "en vol" que s'il peut encore aboutir à un merge ; son
> issue est définitive, et il ne bloque plus personne, dans l'un de ces cas
> UNIQUEMENT, lu sur un artefact bien formé : merge_result.json MERGED ou
> ALREADY_MERGED (un CONFLICT reste en vol : résolution manuelle encore
> possible) ; décision Phase 13 REJECTED ; décision Phase 16 REJECTED ;
> confidence="REJECT" (MEDIUM reste en vol : un humain peut trancher) ;
> validation statique REJECTED ; invocation de l'agent échouée. Artefact
> absent, illisible ou ambigu = reste en vol, jamais une hypothèse optimiste.
> Le message de report nomme désormais le ou les case_id bloquants et leur
> état. Sans aucun de ces artefacts, comportement identique à avant.
> (2) Étape amont, active par défaut (--no-upstream la désactive et restaure
> l'ancien comportement) — corrige la limite (i) de la suite 35 : les Phases 4,
> 5, 6 et 7 n'ont plus à être lancées à la main. Dans l'ordre : import fleet
> optionnel (--import-fleet, exige FLEET_R2_* ; un échec est un
> avertissement, jamais un arrêt) ; Phase 4 pour les cases sans diagnostic,
> bornée par --max-upstream-cases sur les diagnostics RÉELLEMENT nouveaux
> (la Phase 4 lance un vrai Chromium pour un case stage="action") ;
> déduplication une fois, avant toute sélection de contexte — un groupe formé
> remplace ses membres, qui reçoivent un upstream_run.json terminal ;
> puis Phases 5, 6, 7 par case, chacune sautée si son artefact existe déjà
> (reprise possible après interruption). Deux arrêts NORMAUX et terminaux,
> jamais des erreurs : prompt en MANUAL_REVIEW_REQUIRED, et case inéligible à
> la Phase 7 (check_eligibility appelée séparément pour distinguer une
> inéligibilité d'un échec git opérationnel). Chaque case écrit
> autofix_pipeline_runs/<id>/upstream_run.json. Un état terminal n'est jamais
> retraité (évite de relancer Chromium pour un résultat déterministe) ; un
> échec opérationnel non terminal, lui, EST retenté à l'invocation suivante,
> jamais dans la même. Traitement strictement séquentiel (la Phase 7 modifie
> l'état Git du dépôt principal).
> Limites restantes : le pipeline s'arrête toujours à la notification Telegram
> de la Phase 13. Ce qui suit le clic — relever la décision (check_human_review),
> committer (commit_patch), proposer le merge (propose_merge), relever la
> seconde décision, merger (execute_confirmed_merge) — reste cinq commandes
> séparées, non chaînées. Et la Phase 16 conserve une seconde confirmation
> Telegram, que l'opérateur a lui-même qualifiée de cérémonie pour le geste
> git equivalent. Groupes de doublons gelés une fois formés : un case de même
> signature diagnostiqué lors d'une invocation ultérieure ne les rejoint pas.

> Mise à jour 2026-09-27 (suite 37) : étape AVAL de l'orchestrateur — corrige les
> deux limites de la suite 36. `Survey/autofix_orchestrator.py`, `tools/
> run_autofix_pipeline.py`, et une fonction ADDITIVE dans `Survey/human_review.py`
> (`send_status_notification`). Ordre d'une invocation : (0) verrou, (1) aval,
> (2) amont, (3) Claude Code + Phases 8 à 13 ; l'aval passe en premier pour que
> les correctifs déjà mergés soient dans le dépôt quand l'amont diagnostique de
> nouveaux cases. --no-downstream restaure l'ancien comportement.
> Un seul "Approuver" Telegram (Phase 13) déclenche désormais commit puis merge
> local : la Phase 16 (seconde confirmation) SORT de la chaîne orchestrée
> (modules et tools/propose_merge.py intacts, utilisables à la main, jamais
> appelés par l'orchestrateur, aucune option pour rétablir la double
> confirmation). Elle ne s'appelle donc plus "confirmation avant merge" que
> pour un usage manuel ; les métriques (Phase 17) qui lisent merge_reviews/ ne
> voient plus ces merges — non traité, à signaler.
> Verrou d'exclusion (toujours actif, aucune option pour le couper) :
> orchestrator.lock sous --pipeline-runs-root, créé par O_CREAT|O_EXCL, avec
> horodatage et PID ; verrou présent et non périmé = refus explicite (code de
> sortie 3, distinct d'une erreur d'usage), jamais une attente ni un retry ;
> repris avec avertissement au-delà de --lock-stale-after-s ; libéré en
> try/finally. Sans lui, deux invocations planifiées qui se chevauchent
> (une session Claude Code peut durer --claude-timeout-s par case) préparaient
> le même worktree ou tentaient deux merges à la fois.
> Aval : check_pending_reviews appelée UNE fois, comme tools/check_human_review.py
> (même offset, un seul poller Telegram) ; un échec réseau est un avertissement.
> Cases éligibles : human_reviews/<id>/decision.json APPROVED sans
> merge_result.json (celui-ci n'est écrit que pour une issue terminale). Puis
> commit_patch (filet BEM activé) et write_merge_result, réutilisés tels quels.
> Vérifié dans le code plutôt que supposé : check_merge_execution_eligibility ne
> lit que decision.json et ne suppose jamais que le dossier s'appelle
> merge_reviews — seul le nom du dossier appelant distingue une décision
> Phase 13 d'une décision Phase 16 — donc write_merge_result est appelée avec
> le dossier human_reviews/<id>, sans aucune modification de merge_executor.py.
> Idempotence : un worktree propre au moment du commit est traité comme déjà
> committé (jamais un commit vide, jamais un doublon après interruption) ;
> le merge cherche lui-même un "case_id=<id>" sur la branche cible.
> Issues : MERGED/ALREADY_MERGED terminal ; CONFLICT terminal pour
> l'automatisme (merge --abort déjà fait, résolution manuelle), non une erreur ;
> refus opérationnel (dépôt non propre, commit refusé) non terminal, retenté à
> l'invocation SUIVANTE, jamais dans la même. Chaque case écrit
> downstream_run.json.
> Notification Telegram d'issue, non demandée à l'origine : une issue
> silencieuse est un défaut quand l'opérateur est dehors. Un message concis par
> changement d'état (merged, conflict, blocked), jamais le diff ; notified_state
> relu entre invocations pour ne jamais renvoyer le même état ; un échec
> d'envoi ne met PAS à jour notified_state, pour qu'une invocation future
> retente. Limite assumée : la clé "blocked" est unique, un second blocage d'une
> autre nature sur le même case ne renotifie pas.
> Limites : (a) chaque patch a été validé contre l'état d'origine, jamais contre
> les patchs déjà mergés avant lui — Git détecte les conflits de lignes, pas
> les incompatibilités de logique ; (b) le merge exige un dépôt principal
> entièrement propre, fichiers non suivis inclus (git status --porcelain
> --untracked-files=all). Conséquence à vérifier : les dossiers d'artefacts de
> ce pipeline (failure_cases, diagnoses, autofix_pipeline_runs — dont le verrou
> lui-même, présent pendant l'aval —, human_reviews, commit_results, etc.) sont
> relatifs au répertoire courant ; s'ils sont dans l'arbre du dépôt principal
> sans être ignorés par Git, chaque merge sera refusé. À vérifier par
> git status après une invocation.

> Mise à jour 2026-09-28 (suite 38) : clone dédié à l'orchestrateur livré, avec
> DEUX points ouverts constatés à la relecture (voir plus bas).
> Livrables (`surveybot/tools/`) : `setup_autofix_clone.ps1` (clone local depuis
> le dépôt de l'opérateur, branche d'intégration `autofix-integration`, venv,
> dépendances, ruff, Chromium ; idempotent, ne réinitialise jamais une branche
> d'intégration existante), `preflight_autofix_clone.ps1` (lecture seule :
> clone propre selon EXACTEMENT le critère de merge_executor, branche non
> protégée, racines d'artefacts ignorées, variables d'environnement présentes
> sans jamais afficher leur valeur, claude authentifié, dépendances, verrou),
> `sync_autofix_clone.ps1` (amène la branche de développement dans la branche
> d'intégration ; refuse si verrou récent, clone sale ou mauvaise branche ;
> conflit -> merge --abort, jamais de résolution automatique),
> `schedule_autofix_pipeline_task.ps1` (enregistre une tâche planifiée, toutes
> les 60 min par défaut ; n'exécute jamais le pipeline lui-même),
> `introspect_autofix_pipeline.py` (sonde en lecture seule : branches
> protégées, verrou et racines d'artefacts DÉRIVÉES du code par analyse
> syntaxique, jamais recopiées à la main) et `Utils/AUTOFIX_CLONE_
> ORCHESTRATEUR.md`. Le `.gitignore` a été complété (20 racines d'artefacts
> ajoutées ; seules failure_cases/, diagnoses/, diag_test_cases/ et
> replayability/ l'étaient) et testé dans un dépôt jetable reproduisant la
> disposition surveybot/.
> Décisions retenues et vérifiées : clone HORS de l'arbre de l'opérateur, cloné
> en local (pas depuis l'URL distante) ; les correctifs mergés restent dans
> `autofix-integration`, jamais poussés ni mergés dans la branche de
> développement, que l'opérateur récupère à son rythme (fetch + merge) ; un
> seul poller Telegram, celui du clone. Correction d'un motif : la garde
> PROTECTED_BRANCHES (main, prod, playwright-migration) n'est pas ce qui
> bloquait ici, la branche de développement réelle
> (feature/phase-1a-observability) n'étant pas protégée ; le motif décisif est
> l'exigence d'un dépôt entièrement propre.
> Constat ouvert n°1 — le lanceur généré rend le clone "sale". Le script de
> planification écrit `run_autofix_pipeline_task_launcher.ps1` DANS l'arbre du
> clone (surveybot/), sans qu'aucune règle du .gitignore ne le couvre. Vérifié
> sur un dépôt jetable : `git status --untracked-files=all` le liste. Or c'est
> exactement le critère qui fait refuser le merge (merge_executor), le
> pré-vol ("dépôt propre", bloquant) et la synchronisation. Autrement dit,
> planifier la tâche désactive le merge automatique. La mise en place, elle,
> avait écrit son propre fichier annexe hors de l'arbre pour cette raison. À
> corriger : écrire le lanceur hors de l'arbre du clone (même mécanisme que la
> Partie A), plutôt que d'ajouter une règle .gitignore dont dépendrait chaque
> clone.
> Constat ouvert n°2 — fichiers locaux ignorés non traités. La consigne
> demandait de déterminer, en exécutant le code, si des fichiers ignorés par
> Git (global_config.py, accounts.json, receiver_config.json, les
> _license_config.py, Utils/config) sont requis par les chemins exécutés dans
> le clone et dans chaque worktree (créé depuis HEAD, donc sans eux), et de
> fournir soit un mécanisme de copie, soit la preuve qu'aucun n'est requis.
> Ni les scripts ni la documentation livrés ne contiennent l'une ou l'autre.
> Le pré-vol ne teste que l'import de Survey.autofix_orchestrator et
> Survey.autofix_worktree ; il n'exerce ni le rejeu de la Phase 4 ni la
> compilation/import/rejeu des Phases 8 et 9. Risque : rejet à tort d'un patch
> par la Phase 8 pour un fichier manquant, ou échec du rejeu. Non tranché.
> Limites déjà connues, reprises : chaque patch est validé contre l'état
> d'origine et non contre les patchs déjà mergés ; BOT_EVOLUTION_MEMORY.md,
> append-only, produit un conflit textuel à la récupération des correctifs ; le
> dossier des worktrees grossit et n'est nettoyé par aucun script ; lancer
> `venv\Scripts\python.exe` sans activer le venv ne place pas ruff dans le PATH
> (le lanceur de la tâche planifiée le fait, un lancement manuel doit le faire).
> Nature du contrôle de cette suite : relecture du code livré et test du
> .gitignore et du critère de propreté sur un dépôt jetable ; les scripts
> PowerShell n'ont pas pu être exécutés ici (pas de Windows), aucun test de
> bout en bout (Telegram, claude authentifié, Register-ScheduledTask) n'a eu
> lieu.

> Mise à jour 2026-09-28 (suite 39) : constat ouvert n°2 de la suite 38 (fichiers
> locaux ignorés) — PARTIELLEMENT CLOS, sur mesure réelle. Test exécuté par
> l'opérateur sur une copie propre du dépôt (git clone local, donc uniquement
> les fichiers suivis) : Survey.dom_analyzer est bien importé depuis la copie
> (chemin vérifié), global_config.py, accounts.json, receiver_config.json et
> _license_config.py y sont absents, et l'import de CHAQUE module de Survey/,
> un par un, réussit sans erreur. Conclusion établie : aucun module de Survey/
> ne requiert ces fichiers à l'import, donc la compilation/import isolé de la
> Phase 8 ne peut pas échouer pour ce motif sur un fichier de Survey/.
> Ce qui n'est PAS établi : (a) qu'aucun de ces fichiers ne soit lu à
> l'EXÉCUTION (rejeu d'un vrai case : tools/replay_failure.py sur un case de
> stage extraction dans la copie propre) ; (b) le cas d'un patch touchant un
> fichier hors de Survey/ (par exemple preselection/, dont des fonctions sont
> gelées dans extractor_integrity.json) ; (c) que requirements.txt soit
> complet — le test a utilisé le venv de l'opérateur, pas celui que
> setup_autofix_clone.ps1 construira : refaire la boucle d'import avec
> l'interpréteur du venv du clone une fois créé.
> Constat ouvert n°1 (lanceur de la tâche planifiée écrit dans l'arbre du
> clone) : inchangé, prompt de correction rédigé, en attente d'exécution.

> Mise à jour 2026-09-28 (suite 40) : constat ouvert n°1 de la suite 38 (lanceur
> de la tâche planifiée écrit dans l'arbre du clone) — CORRIGÉ, vérifié à la
> relecture du script livré, non exécuté (pas de Windows ici).
> `schedule_autofix_pipeline_task.ps1` écrit désormais le lanceur HORS de
> l'arbre Git du clone : à côté du dossier du clone, sous le nom
> `<nom-du-clone>.run_autofix_pipeline_task_launcher.ps1` (même approche que le
> fichier annexe de setup_autofix_clone.ps1). Un ancien lanceur laissé dans le
> paquet du clone est supprimé UNIQUEMENT si sa première ligne correspond à
> l'en-tête exact de génération automatique ; un fichier homonyme non reconnu
> est conservé, et un homonyme non reconnu à l'emplacement neuf fait refuser
> l'écriture plutôt que de l'écraser. -Unregister retire aussi le lanceur du
> nouvel emplacement. Le contenu du lanceur (PATH du venv, positionnement dans
> le paquet, propagation du code de sortie) est inchangé. Conséquence : planifier
> la tâche ne laisse plus de fichier non suivi dans le clone, donc ne bloque plus
> le merge automatique, le pré-vol ni la synchronisation.
> Non levé : RepetitionDuration reste [TimeSpan]::MaxValue, valeur que certaines
> versions de Windows refusent à l'enregistrement ; si Register-ScheduledTask
> échoue avec une erreur de plage, remplacer par une durée finie très longue.
> Documentation Utils/AUTOFIX_CLONE_ORCHESTRATEUR.md corrigée sur deux points
> qui provenaient de la description initiale et non du code : (a) le clone se
> nomme `<nom-du-dépôt>-autofix-clone` (soit `C:\projects\Surveys-autofix-clone`
> ici) et non `surveybot-autofix-clone`, et les commandes `cd` et `git remote
> add` de la doc pointaient donc vers un chemin inexistant ; (b) la garde
> PROTECTED_BRANCHES n'est plus présentée comme ce qui empêche de merger sur la
> branche de développement (feature/phase-1a-observability n'est pas protégée) :
> le motif décisif est l'exigence d'un dépôt entièrement propre.
> État des constats de la suite 38 : n°1 corrigé ; n°2 partiellement clos
> (suite 39) — restent l'exécution d'un vrai rejeu dans une copie propre, le
> cas d'un patch hors de Survey/, et la complétude de requirements.txt avec le
> venv du clone.

> Mise à jour 2026-09-28 (suite 41) : PREMIÈRE EXÉCUTION RÉELLE d'un script
> PowerShell de ce chantier, sur la machine de l'opérateur (Windows PowerShell
> 5.1), et premier défaut qu'aucune relecture n'avait vu.
> Défaut : la fonction Invoke-GitTimed, dupliquée à l'identique dans
> setup_autofix_clone.ps1, preflight_autofix_clone.ps1 et sync_autofix_clone.ps1,
> lisait `$p.ExitCode` après `Start-Process -PassThru` + `WaitForExit(délai)`.
> Sous Windows PowerShell 5.1, ExitCode reste vide ($null) sans lecture préalable
> de `$p.Handle` : un `git` réussi était rapporté comme un échec, avec un message
> d'erreur vide (le setup s'arrêtait dès la résolution du dépôt). Confirmé sur la
> machine par un test direct : `ExitCode = []` alors que la sortie de git était
> correcte. Corrigé dans les trois scripts : lecture de `$p.Handle` juste après le
> démarrage, `WaitForExit()` sans délai une fois le processus terminé, et un code
> de sortie illisible produit désormais un message explicite au lieu d'un échec
> silencieux. Correction non testée par moi (pas de PowerShell 5.1 ici) : elle est
> validée par le fait que le setup corrigé s'est ensuite exécuté jusqu'au bout.
> Résultat du setup, exécuté sur la machine : clone créé (C:\projects\Surveys-
> autofix-clone), branche `autofix-integration` créée depuis
> origin/feature/phase-1a-observability, venv créé, requirements.txt installé
> sans erreur, ruff 0.16.9 et Chromium (Playwright) installés, métadonnées de
> synchronisation écrites hors de l'arbre du clone. Avertissement : binaire
> `claude` introuvable sur le PATH de la session.
> Toujours non exécutés sur la machine : preflight_autofix_clone.ps1,
> sync_autofix_clone.ps1 et schedule_autofix_pipeline_task.ps1. Le clone contient
> les scripts tels que commités AU MOMENT du clone : la version corrigée n'y figure
> que si elle avait été commitée avant. requirements.txt s'installe sans erreur,
> mais l'import de chaque module avec l'interpréteur du venv du clone reste à
> faire. Aucune invocation de l'orchestrateur n'a encore eu lieu sur un vrai case.

> Mise à jour 2026-09-29 (suite 42) : mise en place du clone menée jusqu'au bout
> sur la machine de l'opérateur, avec DEUX défauts réels rencontrés en cours de
> route et corrigés, chacun d'abord reproduit puis vérifié après correction.
> (1) Invoke-GitTimed (setup/preflight/sync, suite 41) : ExitCode lu vide sous
> Windows PowerShell 5.1 sans lecture préalable de $p.Handle -- confirmé sur la
> machine (git réussi rapporté comme un échec), corrigé, setup exécuté avec
> succès jusqu'au bout après correction (clone créé, branche
> autofix-integration, venv, dépendances, ruff, Chromium).
> (2) schedule_autofix_pipeline_task.ps1 : RepetitionDuration
> ([TimeSpan]::MaxValue -> P99999999DT23H59M59S) rejetée par
> Register-ScheduledTask -- confirmé mot pour mot sur la machine. Corrigé :
> New-TimeSpan -Days 3650 (P3650D, dans la plage acceptée), et
> Register-ScheduledTask encadrée d'un try/catch -ErrorAction Stop -- un échec
> sort désormais en erreur explicite (interrogeant Get-ScheduledTask pour
> distinguer un enregistrement partiel d'un état propre) au lieu d'afficher le
> même résumé qu'un succès, défaut qui avait laissé croire à une tâche créée
> alors qu'elle ne l'était pas.
> claude 2.1.283 installé et authentifié sur la machine (installeur natif
> PowerShell) ; --allowedTools/--output-format/--permission-mode confirmés
> présents dans cette version. telegram_bot_token/telegram_chat_id positionnées
> -- avec, en cours de route, une exposition réelle de secrets (clé OpenAI, clé
> 2Captcha, token Telegram) collés en clair dans la conversation depuis
> receiver_config.json : rotation recommandée à l'opérateur, jamais répétés
> dans les réponses suivantes, fichier copié localement supprimé une fois les
> variables extraites.
> preflight_autofix_clone.ps1 : TOUS les points bloquants OK (dépôt Git, dépôt
> propre, branche non protégée, racines d'artefacts ignorées, variables
> Telegram, claude authentifié, dépendances Python, worktrees hors de l'arbre
> suivi, aucun verrou actif).
> État à ce jour : schedule_autofix_pipeline_task.ps1 relancé après le
> correctif (2), résultat non encore rapporté. Toujours non fait avant toute
> planification : passage supervisé sur un seul cas
> (--max-cases 1 --max-upstream-cases 1, lancé à la main) -- recommandé à
> plusieurs reprises, pas encore exécuté à ce jour. Constat n°2 de la suite 38
> (fichiers locaux hors de Survey/, complétude requirements.txt avec le venv du
> clone, rejeu réel dans une copie propre) : toujours ouvert, non traité par
> cette suite.

> Mise à jour 2026-09-29 (suite 43) : replay passif fidèle ajouté à la Phase 3,
> sur option `tools/replay_failure.py --faithful` uniquement. Le mode statique
> par défaut et son shim restent inchangés. Le DOM figé est servi en mémoire
> par le Chromium isolé existant, sous le `provider_domain` du manifest ; les
> scripts du document sont interdits par CSP et toutes les autres requêtes sont
> refusées. Le pipeline d'extraction et les validators sont appelés tels quels,
> sans clic ni dispatch. Les compteurs du shim sont absents (`None`) dans ce mode.
> Cause corrigée : les `evaluate()` de layout et de hostname, déclinés par le
> shim statique, peuvent maintenant être évalués par le navigateur. Sur
> `20260911_160609_extraction_validation_failure`, le signal Focaldata voit 7
> cartes, mais l'extracteur actuel produit aussi 1 bloc radio de 7 options :
> verdict fidèle `NON_REPRODUIT`, cohérent avec la correction antérieure déjà
> documentée ci-dessus. Sur `20260923_201701_extraction_validation_failure`,
> verdict fidèle `NON_REPRODUIT` (11 blocs). Le mode statique reste exactement
> `NON_REPRODUIT` sur ces deux cases (respectivement 0 bloc, 12/71 evaluate ;
> 9 blocs, 409/470 evaluate honorés/déclinés).

> Mise à jour 2026-09-29 (suite 44) : le replay fidèle de la suite 43 est devenu
> le comportement par défaut de `replay_failure_case()` pour `stage="extraction"`
> (`--static`/`mode="static"` conservent le shim historique) ; le défaut pour
> `stage="action"` reste statique, `--faithful` y reste explicite. Cause : avec
> le shim, `NON_REPRODUIT` pouvait signifier qu'un `evaluate()` décisif avait
> été décliné, et `load_case_document()` donnait `replay-case.invalid` comme
> hostname aux diagnostics/rejeux de patch sans ressource externe capturée.
> `load_frozen_html()` a été supprimé : tous les appelants Chromium passent par
> `load_case_document()`, qui sert le HTML en mémoire sous le hostname
> `provider_domain` du manifest, conserve ressources capturées, scripts
> autorisés seulement selon la règle déjà existante, état runtime et pré-action.
> Le mode fidèle délègue l'extraction à `extract_case_blocks()` (verrou, registre
> DOM et cache de secours centralisés) ; erreur Chromium/document ou comparaison
> inexploitable -> `NON_REJOUABLE`, jamais de repli statique. Aucun module du
> pipeline bot, ni `failure_diagnosis.py`/`patch_replay.py`, n'a été modifié.
> Validation : Focaldata `20260911_160609_extraction_validation_failure` donne
> désormais par défaut `NON_REPRODUIT`/1 bloc/aucune issue avec le code actuel ;
> sur le parent `c86d1c8` du commit ajoutant l'extracteur MUI cards (outillage
> de replay actuel superposé, ancien analyseur/extracteur conservés), il donne
> `REPRODUIT`/0 bloc/`missing_block`. Worktree temporaire retiré après essai.
> `20260923_201701_extraction_validation_failure` : défaut `NON_REPRODUIT`/11
> blocs ; `--static` inchangé `NON_REPRODUIT`/9 blocs. Les deux cases action
> disponibles gardent exactement leur verdict/blocs/issues par défaut
> (`REPRODUIT`/1 bloc chacun) ; le mode fidèle explicite donne aussi `REPRODUIT`
> sur les deux, à titre informatif. Diagnostic Focaldata : verdict inchangé,
> mais replay passif passé de 0 à 1 bloc ; le signal hostname voit 7 cartes.
> `patch_replay` via CLI, avec manifestes Phase 7/8 de test : ancien code ->
> `BUG_PERSISTANT`, code actuel -> `CORRECTIF_CONFIRME`. Le case action testé
> est refusé par la précondition normale (`CORRECTIF_CONFIRME` avant patch,
> `BUG_PERSISTANT` requis). Trois replays successifs dans un même processus
> confirment l'effacement du registre/cache. Doublon restant, non corrigé :
> `failure_diagnosis._attempt_real_extraction_replay` refait l'extraction après
> le replay d'extraction par défaut, désormais fidèle.

> Mise à jour 2026-09-29 (suite 45) : cas 1B famille 1 (« extraction mauvaise
> mais aucun `extraction_validation_failure` ») traité côté DÉTECTION
> uniquement — validé live. Correction de classement au passage : ce cas relève
> de la Phase 1B, pas de la 3B (la capture 3B a fonctionné, c'est le validator
> qui ne disait rien). Cas : MetrixLab (FR), question visible « Etes-vous...? »
> avec trois réponses (Un homme / Une femme / Autre + champ texte). L'extraction
> retournait un unique bloc radio « Merci de répondre à cette question » /
> « JE CONSENS... » / « JE NE CONSENS PAS... » (log `[CONSENT_MODAL]
> detected=true options=2`), issu de la modale de consentement présente dans le
> DOM mais non affichée ; la vraie question n'était couverte par aucun bloc.
> Snapshot d'origine `20260929_031027_after_dom_analyze` (reason
> `after_dom_analyze`, donc pas un snapshot d'échec : aucune détection n'avait
> eu lieu).
> Cause du silence, structurelle et non ponctuelle : les signaux `missing_block`
> de `validate_question_blocks` ne s'exécutaient que si `blocks` était vide, et
> chacun était propre à un provider ; un bloc faux mais structurellement valide
> (target_id présent, options non vides, sans doublon) passait donc toujours.
> Correctif, strictement additif : nouvelle fonction
> `_hidden_block_visible_choice_signal` (`Survey/question_block_validator.py`),
> appelée quand `blocks` est non vide et qu'un driver est fourni, même
> `failure_type` `missing_block` (aucune nouvelle catégorie), `dom_signal=
> hidden_block_visible_choice`, champs `block_index`/`target_id`/
> `visible_question` (tronquée à 160 caractères)/`visible_options_count`. Un
> seul critère, en deux temps : (1) un bloc radio/checkbox d'au moins 2 options
> dont les libellés — résolus par `option_xpath_map` du registre, texte exact
> de l'option exigé — sont TOUS invisibles (`display`/`visibility`/rects, ou
> ancêtre `hidden`/`aria-hidden`) ; (2) un conteneur visible
> (`fieldset`/`radiogroup`/`.question`) avec un titre visible d'au moins 8
> caractères et un groupe d'au moins 2 inputs nommés à libellés visibles, dont
> aucune option n'appartient à un bloc extrait. Un input natif masqué avec son
> libellé visible ne déclenche JAMAIS le signal (garde-fou explicite, choisi
> pour privilégier la précision). Bornes : 30 blocs, 100 options, 30 options
> par bloc côté registre, 40 conteneurs/inputs, 10 libellés par input ; tout
> dépassement, exception ou XPath invalide → signal décliné (`None`), jamais
> une issue devinée. `_EXPECTED_BEHAVIOR["missing_block"]`
> (`Survey/failure_diagnosis.py`) mise à jour dans le même patch, comme l'exige
> le point de vigilance de la Phase 4. Aucun extracteur, ni `dom_analyzer.py`,
> modifié.
> Validation live (run réel, même page) : `[OBSERVABILITY] validation_failure
> stage=extraction issues=['missing_block']
> snapshot=snapshots\20260929_033834_extraction_validation_failure`, snapshot
> complet (dont `validation_report.json`, `external_requests.json`,
> `external_scripts.json`, `external_stylesheets.json`). Le bot a ensuite
> poursuivi son flux normal comme la Phase 1A l'exige (bloc de consentement
> envoyé au modèle, réponse « JE CONSENS... », dispatch lancé) : détection
> passive, comportement inchangé, le bug d'extraction lui-même N'EST PAS
> corrigé.
> Non vérifié à ce stade (aucun test exécuté pour cette entrée) : (a) le rejeu
> fidèle du failure_case issu de ce snapshot (attendu `REPRODUIT` ; le rejeu
> statique par shim ne le reproduira pas — `getComputedStyle` et les rects sont
> déclinés, donc `None`) ; (b) l'absence de faux positif quand la modale de
> consentement est réellement affichée (attendue par construction : ses
> libellés sont alors visibles) ; (c) l'absence de faux positif sur l'ensemble
> des DOM de référence existants. Limites connues du critère : le titre est
> cherché via une liste fixe de sélecteurs (`legend`, en-têtes, classes
> `question-text`/`question_text`/`q_text`/`prompt`) ; seuls les inputs
> radio/checkbox portant un `name` sont considérés ; un conteneur visible hors
> `fieldset`/`radiogroup`/`.question` n'est pas vu ; un bloc qui ne porte pas
> d'`option_xpath_map` dans le registre n'est pas évalué. Reste à faire,
> séparément : corriger l'extraction elle-même (le bot doit extraire la
> question visible tant que la modale n'est pas affichée) — cas de référence
> disponible.
> Mise à jour 2026-09-29 (suite 46) : PREMIER PASSAGE SUPERVISÉ COMPLET de
> l'orchestrateur (`--max-cases 1 --max-upstream-cases 1`), sur le clone
> dédié, sur le cas MetrixLab de la suite 45
> (`20260929_033834_extraction_validation_failure`, confirmé REPRODUIT/
> confiance=certain par diagnose_failure.py ET replay_failure.py en isolation
> avant le lancement).
> Chemin parcouru dans cette seule invocation, en une fois : diagnostic
> (confiance=certain) -> déduplication (solo) -> sélection de contexte ->
> prompt généré (éligible) -> worktree préparé -> Claude Code invoqué en
> headless (status=SUCCESS) -> validation statique (ACCEPTED) -> rejeu du
> patch (outcome=BUG_PERSISTANT, patch_validated=False) -> intégrité des
> fonctions gelées (REJECTED, 18/57 mésappariements) -> confiance=REJECT ->
> aucune notification envoyée (HIGH requis). Comportement attendu du
> pipeline : conforme — un patch qui ne corrige pas le bug n'atteint jamais
> l'opérateur, sans qu'aucune action manuelle n'ait été nécessaire entre le
> lancement et ce verdict.
> Anomalie constatée, EN COURS D'INVESTIGATION, non résolue à ce stade :
> validation_static.json rapporte 0 fichier modifié par rapport à base_sha,
> alors que extractor_integrity_check.json trouve 18 fonctions protégées sur
> 57 dont le hash ne correspond plus au registre. Si 0 fichier a réellement
> changé, ces 18 mésappariements ne peuvent pas provenir du patch de Claude
> Code — ils existeraient déjà sur `autofix-integration` elle-même,
> indépendamment de ce chantier, et rejetteraient alors N'IMPORTE QUEL futur
> patch de la même façon, rendant la Phase 11-A inopérante tant que non
> corrigé. Trois vérifications demandées à l'opérateur pour trancher (git
> status/diff réels du worktree ; `python extractor_integrity.py check` lancé
> directement sur l'état actuel de la branche, sans aucun patch ; contenu
> réel de run_result.json) — résultats non encore rapportés à la clôture de
> cette suite.
> Saga de mise en place du clone entre la suite 42 et ce premier passage,
> résumée ici plutôt que détaillée pas à pas : un fichier
> schedule_autofix_pipeline_task.ps1 retouché directement dans le clone
> (plutôt que dans le dépôt de travail puis synchronisé) y a réintroduit un
> doublon de paramètre PowerShell cassé — corrigé par `git checkout --`
> depuis l'état déjà propre du dépôt de travail ; deux fichiers parasites,
> sans rapport avec le pipeline ("git", un fragment de ligne de commande
> PowerShell mal collée), retrouvés à la RACINE du clone plutôt que sous
> surveybot/tools/ — nettoyés par `git clean`. `sync_autofix_clone.ps1`
> exécuté avec succès une fois ces deux points réglés (fast-forward propre,
> 8 fichiers ramenés dont le correctif de détection de la suite 45 et le
> replay fidèle des suites 43-44). `receiver_config.json` (copié
> temporairement dans le clone pour en extraire telegram_bot_token/
> telegram_chat_id, cf. suite 42) confirmé supprimé.
> Rappel de méthode qui a servi plusieurs fois dans cette saga : distinguer
> systématiquement quel dépôt/environnement (dépôt de travail vs clone,
> lequel des deux venv) exécute réellement une commande avant d'interpréter
> un résultat contradictoire entre deux tests apparemment identiques.
> Mise à jour 2026-09-29 (suite 47) : les deux points ouverts de la suite 46
> RÉSOLUS EN DIAGNOSTIC (pas encore corrigés) — CE SONT DEUX PROBLÈMES
> DISTINCTS, aucun n'est celui initialement soupçonné.
> (A) Claude Code n'a écrit AUCUN patch, pas un mauvais patch : `git status
> --short`/`git diff --stat HEAD` vides dans le worktree, confirmant
> `validation_static fichiers=0` au pied de la lettre. `run_result.json`
> montre pourquoi : une commande Bash composite (`grep -rl` + `find` +
> `xargs grep`), tentée pour vérifier si `hidden_block_visible_choice`
> existait déjà, refusée par `--allowed-tools` (Bash absent, décision de
> conception de l'orchestrateur) — Claude semble s'être rabattu sur un texte
> de diagnostic plutôt que d'utiliser l'outil Grep pourtant déjà autorisé.
> Contenu complet du texte de résultat non encore lu (coupé à l'affichage
> PowerShell côté opérateur, pas dans le fichier). Recommandation en
> attente de ce texte complet : autoriser Bash en lecture seule dans
> --allowed-tools.
> (B) Le registre extractor_integrity.json est désynchronisé du code réel
> de la branche autofix-integration, INDÉPENDAMMENT DE TOUT PATCH — confirmé
> en relançant `extractor_integrity.py check` directement sur la branche,
> sans aucun worktree ni patch : mêmes 18/57 mésappariements que ceux
> rapportés par la Phase 11-A sur ce case. Cause probable : du travail de
> production légitime (dom_analyzer.py::analyze_dom, action_dispatcher.py,
> etc.) a fait évoluer ces fonctions depuis le dernier enregistrement du
> registre, sans `record` relancé derrière. Conséquence sérieuse tant que non
> corrigé : la Phase 11-A rejette DÉSORMAIS TOUT case, quel que soit le
> patch, jamais HIGH atteignable — bloquant pour l'ensemble du pipeline, pas
> seulement ce cas de test. Correction volontairement non automatisée :
> réenregistrer une fonction certifie que son état actuel est le bon état à
> protéger, un jugement sur l'historique de code de l'opérateur, jamais une
> décision technique déléguée. Chaque fonction des 18 à vérifier (git log
> individuel) avant `record-batch`, jamais un réenregistrement en bloc sans
> revue. Ni (A) ni (B) corrigés à la clôture de cette suite.

## Contexte de travail actuel

Nous développons le module d'observabilité, de diagnostic et d'auto-correction supervisée de SurveyBot.

Le travail en cours ne consiste pas à renforcer directement tous les extracteurs
ou sélecteurs un par un. L'objectif est de construire une boucle outillée :

``` text
incident détecté
→ snapshot normalisé
→ failure case
→ classification de rejouabilité
→ replay local (statique / capsule navigateur)
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
3   PARTIELLEMENT TERMINÉE (3A terminée, correctif de fidélité espace insécable
    inclus ; replay Chromium fidèle par défaut pour l'extraction depuis la
    suite 44, statique par défaut pour l'action ; 3B.1/3B.2/3B.5/3B.6/3B.8
    terminés, oracle checked-state étendu au
    rejeu ; 3B.9/3B.10/3B.11 (scripts, feuilles de style, requêtes XHR/fetch
    externes) et 3C.1 + 3C.2 + 3C.3 + 3C.4 (document, ressources externes,
    CSP relâchée, état runtime restauré, extraction+validator rejoués,
    dispatcher réel + timeout + TRACE_REPLAY + comparaison structurée avec
    vocabulaire dédié sans ambiguïté, oracle unique confirmé — frames/shadow/
    frame selection hors périmètre, cf. 3B.3/3B.4) terminés — chaîne complète
    extraction réelle → dispatcher réel corrigé → succès réel →
    CORRECTIF_CONFIRME validée de bout en bout sur le bug Decipher/rowpicker
    qui a motivé ce chantier, clic visuellement confirmé ;
    3B.3/3B.4/reste de 3D à faire — voir Phase 3)
4   terminée
5   terminée
6   terminée
7   PARTIELLEMENT TERMINÉE (préparation d'espace Git isolé faite pour
    stage="extraction" (REPRODUIT/certain) et stage="action" (REPRODUIT +
    real_dispatch_replay BUG_PERSISTANT, coût Chromium systématique en Phase 4) ;
    résultat désormais persisté (worktree.json), traçabilité alignée sur les
    phases 2 à 6 ; aucune invocation automatique d'agent de coding — voir
    Phase 7)
8   TERMINÉE (compile/import/lint Ruff/tests existants sur les seuls fichiers
    modifiés, jamais l'ensemble du dépôt ; aucune suite de tests versionnée
    trouvée dans ce dépôt à ce jour, documenté plutôt que masqué — voir
    Phase 8)
9   TERMINÉE (rejeu du code patché dans le worktree, en sous-processus neuf
    pour éviter le cache sys.modules du dépôt principal ; ne valide le patch
    que sur CORRECTIF_CONFIRME, jamais un entre-deux ; suite de cas
    historiques voisins volontairement différée — voir Phase 9)
10  TERMINÉE (garde-fou AUTOFIX_LIVE_VALIDATE="1" vérifié à deux endroits ;
    connexion connect_over_cdp à une page déjà ouverte par l'opérateur,
    jamais de navigation/close() sur la session distante ; point de
    vigilance de la fermeture réelle de page sur timeout (stage="action")
    résolu — close_page_on_timeout=False, voir Phase 10)
11  PARTIELLEMENT TERMINÉE (partie A — hash d'intégrité des fonctions gelées,
    extractor_integrity_gate.py + CLI, code gelé chargé depuis le worktree
    sous un nom de module dédié, jamais du dépôt principal ; toute anomalie
    est un rejet ; partie B — rejeu de regression_cases/ — différée faute de
    bibliothèque de cas curée — voir Phase 11)
12  TERMINÉE (confidence_score.py + CLI ; aucun effet de bord, produit
    toujours un verdict même dégradé ; règles ordonnées HIGH/MEDIUM/REJECT,
    intégrité en veto plutôt qu'en confirmation requise ; avertissement
    systématique : "régressions" ne couvre encore que le hash, pas un rejeu
    DOM — voir Phase 12)
13  TERMINÉE (human_review.py + CLI notify/check, via Telegram existant —
    recevable sur PC/téléphone/tablette ; polling getUpdates, jamais de
    webhook, décision capturée même prise hors ligne ; notifie seulement
    confidence=HIGH ; ne merge/commit rien — voir Phase 13)
14  TERMINÉE (bem_proposal.py + CLI ; déclenchée seulement sur decision.json
    APPROVED ; fichiers/fonctions réellement modifiés détectés par diff Git +
    AST/hash (technique d'extractor_integrity.py généralisée, jamais ce
    fichier modifié) ; brouillon jamais collé automatiquement dans BEM,
    jugements humains explicitement marqués "à compléter" — voir Phase 14)
15  TERMINÉE (patch_commit.py + CLI ; confidence=HIGH + decision=APPROVED +
    BEM mise à jour vérifiée par un fait Git observable, jamais une
    déclaration ; sujet de commit mécanique, jamais inventé ; worktree propre
    → traité comme déjà committé ; jamais de push/merge — voir Phase 15)
16  TERMINÉE (merge_review.py + CLI propose_merge, généralisation additive de
    human_review.py/check_human_review.py — un seul poller Telegram partagé,
    jamais deux flux concurrents sur le même bot, Phase 13 vérifiée
    inchangée ; ne merge jamais elle-même — voir Phase 16)
17  PARTIELLEMENT TERMINÉE (outil de statistiques seulement — autofix_metrics.py
    + CLI, purement en lecture seule, instantané horodaté ; le mécanisme de
    merge automatique par catégorie reste explicitement différé, en attente
    de plusieurs semaines de données réelles — voir Phase 17)
20  TERMINÉE (transport fleet — fleet_case_upload.py/fleet_case_import.py +
    CLI, R2 déjà existant réutilisé ; vérification positive head_object
    avant tout marquage ; nettoyage local différé, jamais sans vérification
    préalable ; fleet_origin.json en sidecar, jamais dans manifest.json —
    voir Phase 20)
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

## Cas 1B collecté — DataDiggers iControl, radio attention_questions

**Statut : diagnostiqué et corrigé — validé live (`apply ok=true
strategy=datadiggers_icontrol_radio reason=input_checked`, option demandée
effectivement cochée à l'écran, capture bot:9009, 2026-09-22).**

Snapshots `action_validation_failure`, target_id=`group_cead5509389a`, itype
`radio`, provider `datadiggers_icontrol_radio` (cf. BOT_EVOLUTION_MEMORY.md,
section DATADIGGERS ICONTROL, pour le détail technique du bug et du
correctif).

Symptôme observé sur 3 captures live consécutives, avant correction :

``` text
dispatcher: apply ok=false reason=no_strategy
action_validator: dispatcher_false_negative dom_signal=checkbox_radio_checked
```

Ce cas confirme en pratique la famille 4 (« action signalée mauvaise alors
que le DOM final est correct ») sur un pattern DOM distinct d'IFOP
zip2city (radio générique à input caché sous `div.survey_radioBtn`, pas un
widget zip/ville) : `_checkbox_radio_false_negative_issue` dans
`action_validator.py` (extension de l'oracle 1B.1 au-delà du cas IFOP
spécifique, déjà en place avant ce cas) a correctement reclassé l'incident
sans faux positif, sur trois runs live indépendants.

Différence avec IFOP zip2city (1B.1) : ici la cause racine n'était pas dans
`action_validator.py` mais dans la propre vérification de succès du
dispatcher (`Survey/action_dispatcher.py`, bloc
`payload.get("datadiggers_icontrol_radio")` — résolution `_first_input_
under()` incompatible avec l'expression XPath relative utilisée, cf.
BOT_EVOLUTION_MEMORY.md pour le détail). L'oracle 1B n'a pas corrigé le bug
lui-même : il a fourni le signal (snapshot + reclassification) qui a permis
de le diagnostiquer et de le corriger via le pipeline failure_case →
diagnosis → prompt Codex → patch → validation live.

Point de vigilance ouvert, NON confirmé par un cas réel à ce jour :
`validate_actions()` (`action_validator.py`) n'exécute
`_dispatcher_false_negative_issue` que si `dispatcher_success is False`
(cf. `if requested and dispatcher_success is False:`). Si un dispatcher
rapporte `ok=true` sur une valeur différente de celle réellement
sélectionnée dans le DOM, aucune vérification n'a lieu actuellement — ce
cas resterait invisible à toute l'observabilité 1A/1B. Un doute soulevé
pendant le diagnostic de ce même cas DataDiggers (capture semblant montrer
une autre option cochée que celle demandée malgré `apply ok=true`) s'est
révélé être une capture d'écran envoyée par erreur (mauvais run) — donc PAS
un cas confirmé, à ne pas traiter comme tel. À garder en tête pour la
collecte 1B : un cas réel de ce type (faux positif dispatcher,
`dispatcher_success=true`, valeur affichée ≠ valeur demandée) constituerait
une 5e famille non couverte par les 4 familles actuelles (toutes bornées à
`dispatcher_success=False` ou à l'extraction) — ne pas patcher sur cette
seule hypothèse tant qu'aucun snapshot réel ne la confirme.

## Cas 1B collecté — MetrixLab, bloc de consentement masqué extrait à la place de la question visible

**Statut : détection corrigée et validée live (suite 45) ; extraction toujours
non corrigée, volontairement reportée.**

Famille 1 (« extraction mauvaise mais aucun `extraction_validation_failure` »).
Symptôme : la question visible (« Etes-vous...? », 3 réponses) n'est pas
extraite ; le seul bloc retourné vient de la modale de consentement, masquée.
Le bloc étant structurellement valide, aucune des vérifications existantes ne
pouvait le contester. Résolu côté validator par un signal additif générique
(`hidden_block_visible_choice`, `failure_type=missing_block`), sans règle propre
à MetrixLab ni à la modale de consentement — cf. suite 45 pour le critère, les
bornes et les limites non vérifiées.

Enseignement pour la collecte 1B : `missing_block` ne signifie plus seulement
« liste de blocs vide » ; il couvre aussi « bloc(s) extrait(s) ancré(s) sur du
contenu invisible alors qu'un choix visible n'est couvert par aucun bloc ».

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

# Phase 3 --- Replay local déterministe et capsule navigateur

**Statut : PARTIELLEMENT TERMINÉE — Phase 3A implémentée et validée ; Phase 3B
implémentée et validée pour 3B.1/3B.2/3B.5/3B.6/3B.8 ; Phase 3C implémentée
pour 3C.1 et le chargement du document principal (3C.2, 1re brique) ; 3B.3,
3B.4, le reste de 3C et 3D restent à implémenter avant d'engager la génération automatique de patchs de
la Phase 7 en conditions réelles pour les cas hors STATIC_DOM.**

Objectif : transformer chaque incident utile en cas de test durable,
rejouable localement après disparition de la page live.

### Principe fondamental

``` text
la page live sert à capturer l'incident une seule fois
→ elle ne doit pas être nécessaire pour reproduire ou valider le bug ensuite
```

Le replay live attach (Phase 10) ne constitue donc pas le mécanisme principal
de validation. Une page de survey peut être fermée, expirer, rediriger,
devenir inaccessible, changer de contenu, ou dépendre d'une session devenue
invalide — le moteur d'autofix ne doit pas dépendre de sa conservation.

La Phase 3 est divisée en quatre niveaux :

``` text
3A — replay DOM statique
3B — capture enrichie (browser capsule)
3C — replay Chromium local
3D — classification de rejouabilité
```

Le système choisit toujours le niveau le plus simple capable de reproduire
honnêtement le cas. Il ne doit jamais inventer un état manquant pour rendre
artificiellement un incident rejouable.

Note de fidélité : les schémas de fichiers indiqués dans les sections
suivantes (`runtime_state.json`, `frame_tree.json`, etc.) sont indicatifs —
ils fixent l'intention et le contenu minimal attendu, pas un format figé à
l'avance. Comme pour `dom_replay_shim.py` en 3A, l'implémentation réelle
ajustera ces champs au contact du code existant.

------------------------------------------------------------------------

## Phase 3A --- Replay DOM statique

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
Cette limite est levée par la capture du frame_tree (Phase 3B.3) et sa
réexécution dans le replay Chromium (Phase 3C.3).

### Limite actée : réutilisation de dispatcher_success

Pour `stage=action`, le replay tourne sur exactement le même DOM qui a servi à
produire le rapport d'origine, avec le `dispatcher_success` d'origine réutilisé
tel quel (aucun dispatcher réel ne tourne pendant le replay). Tant qu'aucun
patch n'a modifié le code, `REPRODUIT` est donc attendu par construction pour
ces cas — ce n'est pas un défaut, c'est la vérification de référence avant
patch. La valeur diagnostique réelle (confirmer qu'un patch a fait disparaître
le problème) s'active en Phase 9 (replay post-patch), en rejouant le même
outil après modification du code. Limite non résolue tant que le dispatcher
lui-même n'est jamais réellement réexécuté par le replay : voir la trace
d'action (Phase 3B.5), sa réexécution ou son analyse en Phase 3C.4, et le
mode `TRACE_REPLAY` de la Phase 3D pour les cas où le dispatcher réel ne peut
pas tourner localement.

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

## Phase 3B --- Capture enrichie : Browser Capsule

**Statut : PARTIELLEMENT TERMINÉE — 3B.1, 3B.2, 3B.5, 3B.6 et 3B.8 implémentés
et validés (`Survey/browser_capsule.py`, additif, appelé depuis le hook
d'action déjà existant de `Survey/page_snapshot.py` ; artefacts reconnus,
copiés et sanitisés par `Survey/failure_case_builder.py`). 3B.3 et 3B.4
restent délibérément différées (voir avertissement de cadrage ci-dessous et
la mise à jour d'historique correspondante) : prochain sous-chantier une fois
mesuré, sur des cas réels `NON_REJOUABLE` par 3A, le volume effectivement
bloqué par une sélection de frame ou un Shadow DOM fermé.**

Objectif : enrichir le failure case au moment même de l'incident afin de
conserver les informations qu'un simple `outerHTML` perd.

``` text
incident live
→ capture passive enrichie
→ failure case autonome
→ page live ensuite inutile
```

La capture reste strictement passive : aucun retry, aucune correction,
aucune navigation ni clic supplémentaire, aucune modification du résultat du
bot. Elle n'est déclenchée que lorsqu'un incident utile est enregistré, pour
ne pas imposer ce coût à chaque page normale.

Avertissement de cadrage : 3B.1 (DOM/CSS), 3B.2 (état runtime) et 3B.5
(trace d'action) couvrent vraisemblablement l'essentiel des cas aujourd'hui
bloqués en 3A. 3B.3 (frames) et 3B.4 (Shadow DOM) sont plus coûteuses à
capturer et à exploiter : à ne construire qu'après avoir mesuré, sur des cas
réels classés `NON_REJOUABLE` par 3A, combien restent bloqués spécifiquement
par une sélection de frame ou un Shadow DOM fermé.

### 3B.1 --- DOM et CSS

**Statut : TERMINÉE.**

La capture existante de `page_snapshot.py` reste la base : elle conserve
déjà `outerHTML`, le DOM pré/post-action, les `question_blocks`, les actions
demandées, les frames, le screenshot, un `page.mhtml` best-effort sur les
profils qui le permettent, et une reconstruction best-effort des règles CSS
injectées dans le CSSOM.

La Browser Capsule complète ces données avec les faits nécessaires à un
navigateur local pour restituer plus fidèlement la page — pour les éléments
réellement pertinents au cas (cible, options, wrapper/conteneur proche,
ancêtres nécessaires, CTA, éléments référencés par le validator), pas pour
tous les nœuds du document :

``` text
tag/type, target_id ou fingerprint structurel
value, checked, selected, selectedIndex, disabled, readOnly
indeterminate, contenteditable
classes, attributs aria pertinents
texte observable, visibilité observée
bounding rectangle
propriétés CSS nécessaires à l'actionability/visibilité
```

### 3B.2 --- État runtime

**Statut : TERMINÉE — `Survey/browser_capsule.py::capture_runtime_state`,
scopé aux cibles/options/deux niveaux d'ancêtres des actions demandées,
écrit dans `runtime_state.json` (état "après" ; le détail avant/après complet
vit dans `action_trace.json`, cf. 3B.5). Compromis assumé : capturé sur
chaque action du plan, pas seulement celles en échec, faute de pouvoir
connaître l'issue avant de capturer l'état "avant" — cf. mise à jour
d'historique correspondante pour le détail de ce compromis et son
atténuation (hook conditionné par `SURVEY_OBSERVABILITY`, désactivé par
défaut en prod).**

Artefact indicatif : `runtime_state.json`. Objectif : conserver les états
DOM que la sérialisation HTML ne garantit pas.

``` text
HTML capturé :         <input value="">
état runtime observé : input.value = "75001"
```

Le replay doit pouvoir restaurer cet état avant de relancer l'analyse.
Ne jamais supposer que `outerHTML == état JavaScript réel du contrôle`.

### 3B.3 --- Frames

**Statut : DIFFÉRÉE — non traitée par le patch 3B.1/3B.2/3B.5/3B.6/3B.8.
Prochain sous-chantier une fois mesuré, sur des cas réels `NON_REJOUABLE` par
3A, le volume effectivement bloqué par une sélection de frame.**

Artefact indicatif : `frame_tree.json`. La capture actuelle des frames
(présente depuis la Phase 1A) doit évoluer vers une représentation
explicite de leur hiérarchie, pas seulement de la frame déjà choisie :

``` text
chain, parent_chain
DOM, URL nettoyée
id/name/title/src utiles
dimensions observées, text_len, inputs_count
frame sélectionnée lors de l'incident
```

Le replay (Phase 3C) doit pouvoir vérifier : avec la hiérarchie observée
lors de l'incident, `dom_frame_selector` choisit-il maintenant la bonne
frame ? — ce qui rend enfin rejouable la catégorie « frame context oublié »
(Phase 17), hors de portée de 3A.

Toute boucle de capture de frames reste bornée : profondeur maximale, nombre
maximal de frames, abandon contrôlé, warning explicite si capture tronquée.

### 3B.4 --- Shadow DOM

**Statut : DIFFÉRÉE — non traitée par le patch 3B.1/3B.2/3B.5/3B.6/3B.8.
Prochain sous-chantier une fois mesuré, sur des cas réels `NON_REJOUABLE` par
3A, le volume effectivement bloqué par un Shadow DOM fermé.**

Artefact indicatif : `shadow_roots.json` — pour chaque host, un fingerprint
structurel stable et le HTML du shadow root capturé (open uniquement). Les
shadow roots **fermés** ne donnent lieu à aucune tentative intrusive : un
case qui en dépend est déclaré `replay limitation = closed_shadow_root`, sa
fidélité dégradée explicitement plutôt que contournée.

### 3B.5 --- Trace d'action

**Statut : TERMINÉE — `Survey/browser_capsule.py::build_action_trace`, écrit
dans `action_trace.json` via le hook d'action existant. Écart assumé avec le
schéma indicatif ci-dessous, constaté à l'implémentation : `action_dispatcher.py`
ne retourne qu'un booléen (`dispatcher_success`) — ni « stratégie principale
utilisée » ni « raison retournée » n'existent sous forme structurée (seulement
dans des logs non structurés `log_debug`/`log_info`). `action_trace.json` ne
porte donc pas ces deux champs ; à la place, il réutilise tel quel ce que le
validator concerné a déjà calculé de façon structurée (`dom_signal`/`observed`
d'un issue) — aucun parsing de logs tenté, jugé fragile et hors périmètre.**

Pour `stage=action`, compléter `pre_action_dom.html` / `post_action_dom.html` /
`actions_requested.json` par une trace structurée : `action_trace.json`.
Pas un log verbeux du dispatcher — uniquement les faits nécessaires à la
reproduction et au diagnostic :

``` text
action demandée, target résolu
stratégie principale utilisée, résultat retourné, raison retournée
état cible avant action / après action
changements DOM forts observés autour de l'action
```

Exemple (le cas IFOP zip2city de la Phase 1B, rendu durable même si la page
live disparaît) :

``` text
requested value = 75001
before:  input.value = ""
dispatcher: success = false, reason = ifop_zip2city_widget_failed
after:   input.value = "75001", city_label = "Paris 01"
```

### 3B.6 --- Mutations DOM ciblées

**Statut : TERMINÉE — `Survey/browser_capsule.py::install_mutation_observer`/
`collect_mutation_observer`, bornée à 50 mutations. Limite constatée à
l'implémentation : si l'action provoque une navigation avant la collecte,
l'observer est perdu avec l'ancien contexte. Corrigé (2026-09-24) : la
collecte expose désormais explicitement `observer_present` dans son résultat
(`false` = observateur perdu ou jamais installé, distinct d'un `mutations=[]`
qui confirme une absence réelle de mutation) ; câblage vérifié jusqu'à
`action_trace.json` via `Survey/page_snapshot.py::_install_action_observer`
(le dict complet retourné par `collect_mutation_observer` est transmis tel
quel à `build_action_trace`, sans déstructuration partielle qui aurait pu
perdre le champ en route).**

Observation bornée des mutations DOM autour d'une action (attribut `checked`
ajouté, `aria-selected` modifié, classe `selected` ajoutée, texte de
confirmation créé, nœud d'erreur ajouté, option devenue active), utile en
complément de 3B.5. Bornes obligatoires : fenêtre temporelle courte, nombre
maximal de mutations, taille maximale de sortie, arrêt automatique. L'observer
ne produit jamais de mutation lui-même — il observe uniquement le flux normal
existant.

### 3B.7 --- MHTML

**Statut : TERMINÉE — déjà acquise avant ce patch (capture best-effort déjà
présente dans `Survey/page_snapshot.py`, cf. Phase 1A/3A). Aucune action
requise dans le patch 3B.1/3B.2/3B.5/3B.6/3B.8.**

Artefact secondaire lorsqu'il est disponible (préserve plus de ressources
que l'HTML seul), jamais l'unique source du replay. Le failure case reste
exploitable à partir de ses artefacts structurés même si le MHTML est
absent, illisible, ou qu'une ressource n'est pas restaurable.

### 3B.8 --- Secrets et données sensibles

**Statut : TERMINÉE — `Survey/failure_case_builder.py::_sanitize_capsule_json`
généralise la sanitisation déjà en place sur `meta.json`/le HTML (retrait
query string/fragment) aux clés `href`/`src` en plus de `url`, appliquée
récursivement à `runtime_state.json`/`action_trace.json` avant copie dans le
failure_case. Aucun cookie/storage/token/header n'est capturé par
`Survey/browser_capsule.py`.**

La Browser Capsule augmente mécaniquement la quantité d'état capturé. Cette
extension ne doit jamais entraîner la conservation de cookies,
`localStorage`/`sessionStorage` complets, tokens d'authentification, headers
réseau, credentials ou secrets applicatifs. Les URLs suivent les règles de
sanitisation déjà en place (Phase 2). Les valeurs runtime capturées pour le
seul replay local sont des données de reproduction — elles ne doivent jamais
être injectées automatiquement dans un prompt Codex (Phase 6) sans passer par
la même sélection explicite des faits nécessaires au diagnostic.

### 3B.9 --- Scripts externes

**Statut : TERMINÉE — `Survey/browser_capsule.py::collect_external_scripts`,
écrit dans `external_scripts.json` (liste d'entrées `{url, size, content,
error}`), copié et sanitisé (URL uniquement) par
`Survey/failure_case_builder.py`. Lecture depuis l'arbre de ressources CDP déjà
chargé, sans requête réseau ; bornée (60 scripts / 512 Ko / 4 Mo) et tolérante
par script. Ne change rien au rejeu existant : matière première pour un futur
rejeu navigateur (les scripts du document sont aujourd'hui neutralisés par la
CSP de 3C.2). Cf. historique 2026-09-25 (suite) pour les limites.**

### 3B.10 --- Feuilles de style externes

**Statut : TERMINÉE — `Survey/browser_capsule.py::collect_external_stylesheets`,
écrit dans `external_stylesheets.json` (même format et mêmes bornes que
3B.9), copié et sanitisé (URL uniquement) par
`Survey/failure_case_builder.py`. Complète les `<style>` déjà reconstruits
depuis le CSSOM ; `@import` hors périmètre. Ne change rien au rejeu existant.
Cf. historique 2026-09-25 (suite 2).**

------------------------------------------------------------------------

## Phase 3C --- Replay Chromium local

**Statut : PARTIELLEMENT TERMINÉE — 3C.1 et le chargement du document
principal (première brique de 3C.2) implémentés et vérifiés dans
`Survey/replay_browser.py` (`IsolatedReplayBrowser`). Restent : frames, shadow
roots ouverts, restauration de l'état runtime (3C.2) ; 3C.3 ; 3C.4.**

Objectif : lorsqu'un replay lxml (3A) est insuffisant, reconstruire
localement une page dans un vrai Chromium Playwright, sans dépendre du
provider. Ce navigateur de replay tourne dans le worker local/dev
d'autofix, jamais dans le processus SurveyBot de production (Phase 20) : le
bot de prod détecte/capture/enregistre/continue, le worker charge le failure
case, reconstruit la capsule, rejoue, diagnostique, valide.

### 3C.1 --- Isolation réseau

**Statut : TERMINÉE — `Survey/replay_browser.py::IsolatedReplayBrowser`
(`with IsolatedReplayBrowser() as rb: page = rb.new_page()`). Refus posé au
niveau du BrowserContext (requêtes + WebSocket + service workers bloqués),
`blocked_requests` bornée à 500 pour garder les absences visibles. Voir
l'historique 2026-09-25 (piège du handler WebSocket sync).**

Par défaut : aucune navigation libre, aucun accès au provider original,
aucune dépendance au survey live. Le replay utilise uniquement les artefacts
du failure case. Une ressource absente n'est jamais récupérée silencieusement
depuis Internet pour améliorer artificiellement la fidélité du replay.

### 3C.2 --- Reconstruction

**Statut : PARTIELLE — document principal chargé
(`IsolatedReplayBrowser.load_case_document(case_dir)`, HTML choisi par
`failure_replay._pick_dom_file`), et les trois types de ressources externes
déjà capturées par 3B.9/3B.10/3B.11 (`external_scripts.json`/
`external_stylesheets.json`/`external_requests.json`, quand présentes) sont
servis à leur URL exacte avec la CSP `script-src` relâchée dès qu'au moins un
script est servable — sinon comportement inchangé (`script-src 'none'`,
origine synthétique `.invalid`). Validé sur deux cases réels : Confirmit
"Nepa" (scripts/styles s'exécutent et s'appliquent réellement, ressources non
capturées correctement refusées) et un widget radio QARTS "rp"
Decipher/LifePoints dépendant d'une config XHR au chargement — une fois
celle-ci servie (3B.11), React prend possession du nœud et un clic réel
déclenche une vraie navigation : première validation de bout en bout d'un
widget JS interactif via 3C. L'état runtime déjà capturé
(`runtime_state.json`, état "après") est ensuite restauré sur les éléments
correspondants de la page réelle, retrouvés par identité (tag/classe/texte/
input natif) plutôt que par xpath (jamais persisté) ; sans événement émis, une
limite assumée pour les widgets pilotés par leur propre état JS. Hors
périmètre, comme 3B.3/3B.4 : frames, shadow roots ouverts — aucun case réel
n'en dépend à ce jour. Ne fait ni extraction ni validation — voir 3C.3.**

Le worker reconstruit, dans la mesure des artefacts disponibles : document
principal, styles utiles, état runtime, frames, shadow roots ouverts, état
des contrôles — puis réutilise les vrais modules SurveyBot
(`dom_frame_selector`, `dom_analyzer`, `question_block_validator`,
`action_validator`) tels qu'ils existent dans le code courant. L'objectif
n'est pas d'écrire un second moteur d'analyse.

### 3C.3 --- Extraction / frame selection

Pour les bugs d'extraction, le replay Chromium doit permettre de retester
frame selection, visibilité, CSS/layout, DOM dynamique déjà capturé,
extraction, registry, `question_blocks` et validator. Résultat comparé au
failure case d'origine avec les mêmes verdicts que 3A.

**Statut : TERMINÉE (considérée suffisamment validée) —
`Survey/replay_browser.py::extract_case_blocks` fait tourner
`dom_analyzer.analyze_dom` (non modifié) sur la page réelle, compare les
blocs à `question_blocks.json`, et pour `stage="extraction"` fait aussi
tourner `question_block_validator.validate_question_blocks` (non modifié) et
compare son verdict à `validation_report.json` (même vocabulaire que 3A).
Isolation entre cases assurée (`DOM_REGISTRY`/`_STABLE_TEXT_FIELD_LOCATOR`
vidés, extractions sérialisées). Validé sur deux cases extraction réels (cf.
historique, suite 11) — un écart de reproduction expliqué sans ambiguïté par
l'ancienneté du case testé, un succès de reproduction expliqué par un
correctif déjà appliqué depuis la capture. Non fait, laissé de côté sciemment :
frame selection à proprement parler (les cases dépendant d'une frame sont
détectés et exclus de la comparaison, jamais rejoués) — même raison que
3B.3/3B.4, aucun cas réel ne l'a exigé à ce jour.**

### 3C.4 --- Actions

Deux catégories, à distinguer explicitement.

**Action réellement rejouable** — si la capsule contient assez d'état pour
exécuter le dispatcher localement sans dépendance externe :

``` text
pre-action capsule
→ dispatcher courant
→ état post-action local
→ validator
```

Le bug peut alors être validé réellement : avant patch → FAIL, après patch
→ PASS.

Garde-fou obligatoire : toute exécution réelle du dispatcher ici doit être
bornée par un timeout explicite (attente réseau, promesse non résolue,
callback jamais déclenché). Un dépassement de budget n'est jamais silencieux
et ne bloque jamais le replay : il déclasse automatiquement le cas vers
`TRACE_REPLAY` (Phase 3D) plutôt que de laisser le worker pendre ou de
forcer un verdict.

**Action non entièrement réexécutable** — dépendance à des listeners JS non
sérialisables, un état mémoire applicatif, un backend provider, un
WebSocket, une requête API, un timer serveur, une session distante ou un
captcha. Le système ne prétend alors pas avoir rejoué le dispatcher : il
utilise uniquement les faits capturés (pre-state, action demandée, trace
d'action, mutations observées, post-state) pour rejouer l'analyse/validation
disponible. Le résultat est identifié explicitement comme replay partiel
(`TRACE_REPLAY`). Aucun faux `PASS` n'est produit en faisant semblant que
l'interaction provider a été reproduite.

Ce pari (`dispatcher_success=false` mais état DOM prouvant que l'état
attendu a été atteint) est déjà posé une première fois par le validator à
l'incident — voir Phase 1B.1, `dispatcher_false_negative`/`dom_signal`, cas
IFOP zip2city. `TRACE_REPLAY` applique la même logique côté replay
post-patch. Pour éviter deux implémentations parallèles du même pari, cette
logique doit vivre dans **une fonction oracle unique**, appelée à la fois par
`action_validator.py` (Phase 1B) et par le replay (Phase 3C/3D) — pas dans
deux endroits différents.

**Statut : TERMINÉE — `Survey/replay_browser.py::execute_case_action`
exécute `action_dispatcher.execute_actions_plan` (non modifié) sur la page
rejouée, avec garde-fou de timeout (watchdog, jamais de verdict tardif après
budget dépassé) et statuts `SUCCESS`/`FAILURE`/`TIMEOUT`/`ERROR`/
`NOT_EXECUTED`/`NOT_APPLICABLE`. Chargement du document pré-action ajouté
(`load_case_document(..., pre_action=True)`), sans restauration d'état
(autre instant). Comparaison structurée avant/après ajoutée : quand un
verdict de dispatch est exploitable, `action_validator.validate_actions`
(non modifié) tourne sur la page live, comparé à `validation_report.json`
via `_compare_validation` (réutilisée, inchangée) puis traduit par
`_action_outcome` en vocabulaire distinct et sans ambiguïté
(`CORRECTIF_CONFIRME`/`BUG_PERSISTANT`/`NON_CONCLUANT`), pour ne pas
réutiliser tel quel `REPRODUIT`/`NON_REPRODUIT`/`DIFFERENT` (vocabulaire de
fidélité de rejeu passif, ambigu après une exécution réelle). Après un
`TIMEOUT`, `_trace_replay_fallback` rejoue `action_validator.validate_actions`
(non modifié) sans pilote live, via `captured_option_states`
(`runtime_state.json`) — même mécanisme que le rejeu statique passif, jamais
réimplémenté ; exposé dans un champ distinct (`trace_replay`), avec le
vocabulaire de fidélité (pas celui du dispatch réel), sans jamais réécrire le
statut `TIMEOUT`. Fonction oracle unique confirmée : les trois chemins (rejeu
statique passif, dispatch réel, repli après timeout) délèguent tous
intégralement à `action_validator.py`, aucune réimplémentation du pari nulle
part — vérifié directement dans le code après qu'une première vérification
automatisée s'était trompée sur ce point précis (confusion entre "champ resté
`None`" et "fonction jamais appelée"). Validé de bout en bout sur le case de
référence Decipher/rowpicker : extraction identique à l'origine sur le
document pré-action, dispatcher réel corrigé → `SUCCESS`, clic visuellement
confirmé, validator → `CORRECTIF_CONFIRME` sans ambiguïté. `TRACE_REPLAY`
revu par relecture de code, pas encore observé sur un vrai dépassement de
budget en conditions réelles.**

------------------------------------------------------------------------

## Phase 3D --- Classification automatique de rejouabilité

**Statut : PARTIELLE — `Survey/replayability_classifier.py` +
`tools/classify_replayability.py` implémentés, en lecture seule sur
`diagnosis.json` (Phase 4) déjà produit, aucun replay/dispatcher/navigateur
recalculé. `STATIC_DOM`/`BROWSER_CAPSULE`/`TRACE_REPLAY` correctement produits
à partir des signaux déjà existants ; `EXTERNAL_NON_REPLAYABLE` structurellement
défini mais jamais produit (aucun signal de ce type dans le pipeline à ce
jour). Validé sur trois cases réels (suite 21). `real_extraction_replay`
(Phase 4, suite 22) existe désormais pour `stage="extraction"`, mais reste
purement informatif — le classificateur ne le lit pas encore : `UNDETERMINED`
persiste pour `stage="extraction"` hors `STATIC_DOM`, asymétrie encore réelle
avec `stage="action"`, câblage restant à faire.**

Chaque failure case reçoit un mode de replay explicite :

``` text
STATIC_DOM               → reproductible par 3A (structure DOM pure)
                            ex. question block incohérent, option absente,
                            target_id invalide, registry incompatible,
                            contrainte min/max impossible

BROWSER_CAPSULE           → HTML statique insuffisant, mais 3B/3C suffisent
                            ex. visibilité CSS, layout, bounding boxes,
                            frame selection, shadow DOM ouvert, état
                            runtime d'un contrôle

TRACE_REPLAY              → interaction non réexécutable fidèlement, mais
                            l'état avant/après et les mutations observées
                            suffisent à rejouer l'oracle ou le diagnostic
                            ex. dispatcher retourne false mais mutation DOM
                            forte prouve que l'état attendu a été atteint

EXTERNAL_NON_REPLAYABLE   → dépendance intrinsèque à une donnée externe non
                            capturable de façon fiable
                            ex. validation serveur, captcha distant, état
                            backend, WebSocket, session distante expirée,
                            réponse réseau future
```

`EXTERNAL_NON_REPLAYABLE` n'est pas un échec du moteur, c'est une conclusion
explicite : les artefacts disponibles ne permettent pas une validation
locale fiable. Le système préfère ce verdict à une simulation fragile — ces
cas sont orientés vers la Phase 10 (test live attach), qui devient leur voie
de validation normale, pas un recours tardif isolé.

La Phase 4 (diagnostic) devra lire ce champ pour ne pas attendre un verdict
`REPRODUIT`/`NON_REPRODUIT` d'un case classé `EXTERNAL_NON_REPLAYABLE` — une
extension à prévoir, pas une réécriture rétroactive de la Phase 4 déjà
livrée.

------------------------------------------------------------------------

## Compatibilité avec les Phases 4, 5 et 6 déjà implémentées

Les Phases 4, 5 et 6 ont déjà été construites sur la sortie du replay
existant. La correction de la Phase 3 ne doit donc pas casser leur contrat
sans nécessité. Les verdicts principaux restent
`REPRODUIT`/`NON_REPRODUIT`/`DIFFERENT`/`NON_REJOUABLE` ; la Phase 3 enrichie
ajoute des métadonnées (`replay_mode`, `replay_fidelity`, `limitations`,
`artifacts_used`) sans toucher aux champs déjà consommés par
`Survey/failure_diagnosis.py`, `Survey/context_selector.py` et
`Survey/prompt_generator.py`. Ces modules pourront exploiter progressivement
les nouvelles métadonnées par patches minimaux, sans réécriture gratuite.

La Phase 9 (replay post-patch) consomme les mêmes verdicts et doit être
étendue dans le même esprit : un verdict obtenu en `TRACE_REPLAY` est une
preuve plus faible qu'un `REPRODUIT`/`NON_REPRODUIT` obtenu en `STATIC_DOM`
ou `BROWSER_CAPSULE` — la Phase 9 (et le score de confiance, Phase 12) doit
pouvoir les distinguer plutôt que les traiter à égalité. Un replay partiel
n'est pas une preuve certaine qu'un patch est correct.

------------------------------------------------------------------------

## Validation de la Phase 3 enrichie

La Phase 3 corrigée est considérée terminée lorsque les quatre niveaux ont
été validés sur des cas représentatifs :

``` text
1 cas STATIC_DOM                          → reproduit par 3A
1 cas nécessitant layout/CSS runtime      → non fiable en 3A,
                                             reproduit par BROWSER_CAPSULE
1 cas impliquant une frame                → frame tree reconstruit,
                                             sélection de frame rejouable
1 cas d'action avec état avant/après      → dispatcher ou trace rejoué
                                             selon sa capacité réelle
1 cas intrinsèquement externe             → classifié EXTERNAL_NON_REPLAYABLE,
                                             aucune tentative de faux replay
```

Pour chaque cas rejouable : code avant correction → incident reproduit ;
patch → même failure case → incident disparu ; cas voisins pertinents →
absence de régression. Aucune page live originale ne doit être nécessaire
pour ces validations.

------------------------------------------------------------------------

## Règles de simplicité

Cette extension ne doit pas devenir un système général d'archivage complet
du Web. Ne pas chercher à sérialiser fidèlement toute la mémoire
JavaScript, tous les listeners, tout le trafic réseau, tous les storages
navigateur, ou tout le backend du provider. Le but est uniquement de
capturer les faits nécessaires aux bugs réellement rencontrés par SurveyBot.

``` text
si un nouvel artefact améliore clairement
la reproduction, le diagnostic, la validation ou la non-régression
→ il est justifié
sinon
→ ne pas l'ajouter
```

Toute boucle de capture ou de reconstruction a un budget maximal, un
abandon contrôlé, un `log_debug` utile, et jamais de boucle infinie.

------------------------------------------------------------------------

## Décision de clôture

La Phase 3A existante reste valide et constitue le premier niveau, rapide,
du replay. Elle n'est plus considérée comme représentant à elle seule
l'ensemble de la Phase 3, qui devient :

``` text
incident détecté
        ↓
failure case enrichi
        ↓
classification de rejouabilité
        ↓
STATIC_DOM / BROWSER_CAPSULE / TRACE_REPLAY / EXTERNAL_NON_REPLAYABLE
        ↓
replay local le plus fidèle disponible
        ↓
diagnostic / contexte / prompt Codex existants
        ↓
validation du futur patch sur le même case
```

Objectif final : une page problématique n'est observée qu'une seule fois en
live pour devenir ensuite un cas de test durable du moteur d'autofix.

------------------------------------------------------------------------

# Phase 4 --- Générateur automatique de diagnostic

**Statut : TERMINÉE — `Survey/failure_diagnosis.py` + `tools/diagnose_failure.py`
implémentés et validés.**

Objectif atteint : transformer un failure_case (Phase 2) et son résultat de
replay (Phase 3, recalculé à la volée) en diagnostic structuré, sans jamais
prescrire de patch :

``` text
manifest.json + artifacts/ (Phase 2)
→ replay_failure_case() (Phase 3, réutilisé tel quel)
→ diagnosis.json
```

### Contenu réel du diagnostic

``` text
symptom              : issues de validation_report.json, recopiées telles quelles
expected_behavior    : description par failure_type, citant la fonction exacte du
                        validator dont elle est extraite (table statique tenue à
                        jour à la main — voir "Point de vigilance" ci-dessous) ;
                        "non documenté" plutôt qu'une description inventée si le
                        failure_type n'est pas dans la table
replay               : verdict + détails complets de Survey/failure_replay.py
cause.level          : dérivé UNIQUEMENT du verdict de replay — REPRODUIT -> certain,
                        DIFFERENT -> probable, NON_REPRODUIT/NON_REJOUABLE/absence
                        de verdict -> plausible (jamais déduit du symptôme seul)
cause.justification  : texte traçable à la donnée du replay utilisée pour ce niveau
modules_likely_involved : voir ci-dessous — vide si rien de fiable
confidence_global    : = cause.level, plafonné à "plausible" si manifest.incomplete
warnings              : notamment si aucun module trouvé, ou si le plafonnement
                        incomplete a été appliqué
```

Aucun prompt destiné à un agent de coding n'est généré ici — c'est le rôle des
Phases 5/6.

### Attribution de modules : recherche exacte, jamais heuristique

`modules_likely_involved` ne cherche jamais dans le code source. La recherche
se limite à des correspondances exactes (sous-chaîne, insensible à la casse)
entre des signaux structurés déjà présents dans `question_blocks.json` (un
flag booléen `=true` du `context` d'un bloc, ou son `group_key` — entier, ou
préfixe non générique) et le contenu des entrées `### ...` de
`BOT_EVOLUTION_MEMORY.md`. Le ou les fichiers associés viennent de la ligne
`Fichier : X` de l'entrée trouvée (ou de l'en-tête `### nom — Survey/x.py`
quand elle en tient lieu) — jamais devinés.

`provider_domain` est délibérément exclu de cette recherche : vérifié en
pratique, un même hostname héberge des dizaines de widgets différents et
génère des faux positifs — pas une supposition, un cas constaté.

Si aucun signal ne correspond à une entrée mémoire, la liste reste vide.

### Point de vigilance : `_EXPECTED_BEHAVIOR` maintenue à la main

La table qui associe chaque `failure_type` à sa description de comportement
attendu vit dans `Survey/failure_diagnosis.py`, en miroir du code réel des
validators, mais **sans mécanisme qui la garde synchronisée**. Contrairement à
`BOT_EVOLUTION_MEMORY.md`, aucune discipline de mise à jour n'existe encore
pour elle. Règle à appliquer dès le prochain patch touchant
`action_validator.py` ou `question_block_validator.py` : mettre à jour cette
table dans le même mouvement que BEM, pas après coup.

------------------------------------------------------------------------

# Phase 5 --- Sélection automatique du contexte code

Actuellement tu dois envoyer plusieurs fichiers à Claude/Codex.

On automatise cela.

**Statut : TERMINÉE — `Survey/context_selector.py` + `tools/select_context.py`
implémentés et validés.**

### Deux sources de signal prévues, une seule active

À l'origine, deux sources étaient envisagées : les modules déjà trouvés par la
Phase 4 (`modules_likely_involved`, ancrés dans BOT_EVOLUTION_MEMORY.md), et
une éventuelle table de mapping itype/stage → fichier déjà existante dans le
code. Investigation menée avant d'écrire le module (grep exhaustif de noms de
structure usuels, puis de tout dict littéral indexé par itype) : **cette
seconde source n'existe pas dans ce codebase.**

``` text
action_dispatcher.py::_apply_by_target_id  -> ~150 branches if/elif inline
dom_analyzer.py::_analyze_dom_current_context -> cascade try/except séquentielle
_TYPE_ALIASES (seul dict itype trouvé)      -> synonymes texte, pas des fichiers
```

Reconstruire une association à partir de ces enchaînements aurait été de
l'interprétation par ressemblance — exactement ce qui est exclu. Cette source
contribue donc 0 fichier aujourd'hui, documenté explicitement
(`mapping_table_signal` dans la sortie) plutôt que masqué.

Les deux exemples ci-dessous restent illustratifs de ce qu'*aurait pu* donner
une Source 2 si elle existait — ils ne correspondent pas (encore) à un
comportement réel de l'outil :

``` text
selection_not_applied, itype=checkbox
→ BOT_EVOLUTION_MEMORY.md, action_dispatcher.py, input_checkbox.py, input_utils.py, frame_utils.py

missing_options
→ BOT_EVOLUTION_MEMORY.md, dom_analyzer.py, dom_extractors_*.py concerné, dom_question_extractor.py
```

### Comportement réel

La sélection repose à 100 % sur `modules_likely_involved` de la Phase 4,
filtrée (les fichiers qui n'existent plus sur disque sont exclus et consignés
dans `stale_references`) et plafonnée (`DEFAULT_CODE_FILES_CAP=8` fichiers de
code, hors BEM ; au-delà, troncature déterministe consignée dans
`dropped_files`, jamais d'extension silencieuse). `BOT_EVOLUTION_MEMORY.md` est
toujours inclus séparément, hors plafond.

L'objectif n'est pas de transmettre tout le repo.

Trop de contexte dégrade souvent le diagnostic.

### Deux points de vigilance

1. Si `action_dispatcher.py`/`dom_analyzer.py` sont un jour refactorés vers une
   vraie table de dispatch énumérable, revisiter la Source 2 de
   `context_selector.py` — la conclusion "n'existe pas" est vraie aujourd'hui,
   pas garantie dans le temps (même famille de risque que `_EXPECTED_BEHAVIOR`,
   Phase 4).
2. Pour un pattern non encore documenté dans BEM, `code_files` sera vide. La
   Phase 6 doit traiter ce cas explicitement comme "revue manuelle nécessaire"
   plutôt que générer un prompt Codex normal avec zéro fichier ciblé.

------------------------------------------------------------------------

# Phase 6 --- Génération automatique du prompt Codex

À partir du dossier `failure_case`, on génère ton prompt standard.

**Statut : TERMINÉE — `Survey/prompt_generator.py` + `tools/generate_prompt.py`
implémentés et validés. Le point de vigilance data ci-dessous est fermé (voir
Phase 2, `_sanitize_validation_report`).**

### Fidélité au gabarit réel, pas à l'esquisse conceptuelle

Le gabarit effectivement utilisé n'est pas la version simplifiée envisagée au
départ (CONTEXTE / BUG IDENTIFIÉ / FICHIERS À ANALYSER / CONTRAINTES) : c'est
le gabarit complet déjà en usage pour chaque prompt de ce projet (BEM, règles
de lecture, variabilité intra-source, RÈGLE DURE zéro modification, logs,
CTA/clics, RÈGLES STRICTES, ACTION REQUISE) — reproduit **verbatim,
caractère pour caractère**. Seule la section BUG IDENTIFIÉ est générée
dynamiquement. Les fichiers probablement concernés (Phase 5) s'intègrent dans
cette section, comme déjà prévu par la règle existante de rédaction des
BUG IDENTIFIÉ — pas dans une section séparée inventée pour l'occasion.

> Mise à jour SPCA, tâche 5 (29 septembre 2026) : la fidélité verbatim et la
> « RÈGLE DURE » ci-dessus décrivent le gabarit historique. Le générateur actuel
> garde l'éligibilité et le récit factuel `BUG IDENTIFIÉ`, mais guide désormais
> les correctifs externes selon les points d'extension extraction/action de
> SPCA. Le `case_id` nécessaire au registre figure dans des métadonnées
> techniques distinctes ; la Phase 11-A n'est pas modifiée par cette tâche.

### Garde-fou d'éligibilité

Un prompt exploitable (`prompt.txt`) n'est produit que si `context_selection.json`
contient au moins un fichier de code **et** si `diagnosis.json` indique une
confiance globale `probable` ou `certain`. Dans tous les autres cas —
notamment tout case plafonné à `plausible` par la Phase 4 (NON_REJOUABLE,
NON_REPRODUIT, ou aucun fichier trouvé) — la sortie est
`MANUAL_REVIEW_REQUIRED.txt`, un format et un nom délibérément différents,
avec un en-tête et un pied de page explicites ("NE PAS TRANSMETTRE À CODEX" /
"CECI N'EST PAS UN PROMPT"), pour ne jamais pouvoir être confondu avec un
prompt exploitable ni transmis par erreur en aval.

### Ce que BUG IDENTIFIÉ peut et ne peut pas dire

Le symptôme reprend les faits d'origine (itype, valeur, question, décomptes...)
**sauf** si le verdict de replay est `DIFFERENT` : dans ce cas, seuls les
`failure_types` réellement rejoués sont décrits, jamais le rapport d'origine
resté non reproduit tel quel. Le comportement attendu vient de
`expected_behavior` (Phase 4), jamais recalculé ; "non documenté" si absent
plutôt qu'une description inventée.

Sont explicitement exclus du texte généré : `case_id`, le nom du verdict de
replay, le niveau de confiance, `provider_domain`, le chemin du snapshot, et
les identifiants de registry internes (`target_id`, `action_index`,
`block_index`) — cette section doit se lire comme un bug rapporté normalement,
jamais comme un export de données de pipeline.

### Point de vigilance fermé : `value` d'un issue, désormais sanitisé en Phase 2

Le champ `value` d'un issue de `validation_report.json` (repris dans le
symptôme via `_SAFE_ISSUE_FIELDS`) n'a longtemps transité par aucune
sanitisation, contrairement à `meta.json` et aux DOM HTML — risque documenté
pour un champ de saisie libre (ex. un code postal, cf. le cas de référence
IFOP zip2city). Fermé en Phase 2 (`Survey/failure_case_builder.py::
_sanitize_validation_report`, cf. historique 2026-09-26 suite 19) : `value`
n'est conservée que si elle correspond à une option prédéfinie réelle du bloc
(`question_blocks.json` du même snapshot), jamais devinée depuis `itype` ;
toute ambiguïté est traitée comme potentiellement sensible et retirée. Cette
section continue de lire un `validation_report.json` déjà sanitisé à la
source — rien à faire ici.

Mais **Codex ne sera pas encore exécuté automatiquement** — c'est l'objet de
la Phase 7.

------------------------------------------------------------------------

# Phase 7 --- Génération automatique d'un patch dans une branche isolée

**Statut : PARTIELLEMENT TERMINÉE — `Survey/autofix_worktree.py` +
`tools/prepare_autofix_worktree.py` implémentés et validés. `stage="extraction"` :
`replay.verdict="REPRODUIT"` et `confidence_global="certain"` (le sous-ensemble
`STATIC_DOM` le plus solide, sans attendre la Phase 3D). `stage="action"` :
maintenant dans le périmètre — 3C.4 étant close, `replay.verdict="REPRODUIT"`
seul (attendu par construction, le replay passif ne réexécute jamais le
dispatcher réel) ne suffit plus, une exigence supplémentaire s'ajoute :
`real_dispatch_replay.validation_comparison.outcome="BUG_PERSISTANT"`
(Phase 4, confirmation active par réexécution réelle du dispatcher que le bug
persiste sur le code non corrigé). Coût assumé : cette réexécution tourne
systématiquement dans `diagnose_failure_case` pour tout case action, pas à la
demande — Phase 4 devient plus lente sur ce stage, pas encore chronométré sur
un vrai run. Complété : le résultat (`case_id`/`branch`/`base_sha`/
`worktree_path`/`source_branch`/`prompt_path`) est désormais persisté sous
`autofix_worktrees/<case_id>/worktree.json`, même convention JSON que les
phases précédentes — sans cela, aucune phase avale n'avait d'artefact à lire
pour retrouver le worktree d'un case, contrairement au principe suivi partout
ailleurs dans ce pipeline. C'est cet artefact, et lui seul, que consomme la
Phase 8.**

**Précision sur le périmètre réellement couvert :** cette phase prépare
l'espace de travail isolé (branche + worktree Git dédiés) et s'arrête là —
elle n'invoque aucun agent de coding par programme, n'applique aucun patch,
ne commit/push/merge rien. L'étape « Codex » du schéma ci-dessous reste, à
ce stade, un lancement manuel de l'opérateur dans l'espace préparé, dans la
continuité du fonctionnement de tous les outils du pipeline en amont
(Phases 2 à 6), qui sont des façades CLI actionnées manuellement, jamais
enchaînées automatiquement entre elles.

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

**Statut : TERMINÉE — `Survey/static_validator.py` + `tools/validate_patch_static.py`.
Lecture seule sur le seul artefact produit par la Phase 7 (`worktree.json`) —
jamais `manifest.json`/`diagnosis.json` rouverts, jamais l'éligibilité Phase 7
recalculée. Fichiers vérifiés : uniquement le sous-ensemble modifié entre
`base_sha` et l'état courant du worktree (Git diff ∪ nouveaux fichiers non
trackés), jamais l'ensemble du dépôt. Quatre vérifications, chacune sous son
propre budget de temps explicite : compilation isolée (`py_compile`), import
isolé (même interpréteur/venv que l'outil, jamais un `python` résolu au hasard
sur PATH), lint minimal via Ruff (`--isolated`, `F821`/`F822`/`F823` uniquement
— jamais de règle de style), tests unitaires déjà associés aux fichiers
modifiés si une convention fichier-source → fichier-de-test existe. Aucune
trouvée à ce jour dans ce dépôt (aucun `tests/`, aucun `conftest.py`, pytest
non installé) — documenté explicitement plutôt que masqué ; le détecteur
s'activera de lui-même le jour où une convention apparaît réellement, sans
nouveau patch. L'absence de test associé n'est jamais à elle seule un motif
de rejet — seul un test déjà existant qui échoue réellement fait échouer la
phase. Verdict `ACCEPTED`/`REJECTED` tracé sous
`autofix_static_validations/<case_id>/validation_static.json`. Aucun test live
déclenché, quel que soit le verdict.**

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

**Statut : TERMINÉE — `Survey/patch_replay.py` + `tools/replay_patch.py`.
Lecture seule sur les Phases 4/7/8 (diagnosis.json, worktree.json,
validation_static.json) — aucune n'est recalculée, aucune n'est rouverte
directement (manifest.json n'est lu qu'indirectement, par les mécanismes de
rejeu déjà existants). Le patch n'est rejoué que si la Phase 8 a déjà accepté
(`verdict="ACCEPTED"`), sinon refus contrôlé avant tout effet de bord. Le
signal "avant patch" (`REPRODUIT` en extraction,
`real_dispatch_replay.validation_comparison.outcome="BUG_PERSISTANT"` en
action) vient tel quel de la Phase 4, jamais recalculé — seul le code après
patch est rejoué, dans un sous-processus Python neuf pointant vers le
worktree (jamais le process appelant, dont `sys.modules` garderait sinon en
cache le code non corrigé du dépôt principal). Mécanisme de rejeu réutilisé
tel quel par stage (`Survey.failure_replay.replay_failure_case` pour
l'extraction, la même séquence que
`Survey/failure_diagnosis.py::_attempt_real_dispatch_replay` pour l'action),
jamais réimplémenté ; résolution de la racine de paquet du worktree reprise
telle quelle de la Phase 8 (`Survey.static_validator._resolve_package_root`).
Verdict strict : seul `CORRECTIF_CONFIRME` valide le patch — un timeout, un
verdict `NON_CONCLUANT`, `DIFFERENT` ou `NON_REJOUABLE` sont tous traités
comme un rejet, jamais un entre-deux, même quand un signal partiel (trace
replay) semble favorable. Le rejeu de cas historiques voisins (suite de
non-régression DOM) reste un sous-chantier explicitement différé — cette
phase ne rejoue que le case ciblé.**

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

### Limite actuelle (avant Phases 3B/3C)

Pour les cas `stage=action`, cette phase ne peut rejouer que ce que couvre le
replay disponible. Tant que les Phases 3B/3C n'existent pas, un patch
touchant `action_dispatcher.py` n'est vérifié par aucun replay local — voir
la limite actée en Phase 3A. Une fois 3D en place, un verdict `TRACE_REPLAY`
doit être traité comme une preuve plus faible qu'un `REPRODUIT`/`NON_REPRODUIT`
obtenu en `STATIC_DOM`/`BROWSER_CAPSULE` (voir « Compatibilité avec les
Phases 4, 5 et 6 », en Phase 3). Pour les cas classés
`EXTERNAL_NON_REPLAYABLE`, seule la Phase 10 (live attach) constitue une
validation réelle.

------------------------------------------------------------------------

# Phase 10 --- Test live attach contrôlé

**Statut : TERMINÉE — `Survey/live_validator.py` + `tools/validate_patch_live.py`.
Garde-fou `AUTOFIX_LIVE_VALIDATE="1"` (égalité stricte) vérifié à deux endroits
indépendants (CLI avant tout import d'argparse, puis `check_preconditions()`
en défense en profondeur). Éligible seulement quand la Phase 9 est restée
`NON_CONCLUANT` (`refused=False`) — jamais quand elle a déjà confirmé le
correctif, ni quand elle a confirmé que le bug persiste. Ne lance ni ne
configure Chrome : se connecte via `connect_over_cdp` à une page déjà ouverte
par l'opérateur (jamais `new_page()`, jamais de navigation, jamais de
`close()` sur la session distante). Une seule tentative par invocation,
jamais de boucle interne.**

**Point de vigilance découvert en écrivant ce module — RÉSOLU (suite 28) :
pour `stage="action"`, `execute_case_action` fermait réellement la page si
son propre budget interne était dépassé — sans conséquence sur un Chromium
isolé jetable, mais réel sur une page CDP live. `Survey/replay_browser.py::
execute_case_action` accepte désormais un paramètre additif
`close_page_on_timeout` (défaut `True`, comportement inchangé pour les
Phases 3C.4/9) ; ce module l'appelle avec `close_page_on_timeout=False` — un
timeout du dispatcher ne ferme plus la page distante. Conséquence assumée :
le dispatcher bloqué ne se débloque alors plus que par le budget global du
sous-processus de cette phase, pas par ce watchdog.**

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

### Rôle après la Phase 3D

Une fois le classificateur de rejouabilité (Phase 3D) en place, cette phase
devient la voie de validation *normale* pour tout case classé
`EXTERNAL_NON_REPLAYABLE` — pas seulement un filet de sécurité tardif pour
quelques cas exceptionnels. Son fonctionnement décrit ci-dessus ne change
pas ; seul son déclenchement devient systématique pour cette catégorie.

------------------------------------------------------------------------

# Phase 11 --- Validation anti-régression

**Statut : PARTIELLEMENT TERMINÉE.**

**Partie A (hash d'intégrité des fonctions gelées) : TERMINÉE —
`Survey/extractor_integrity_gate.py` + `tools/check_extractor_integrity.py`.
Complément déterministe, jamais un remplacement, au rejeu DOM décrit
ci-dessous : un hash SHA256 par fonction protégée
(`Survey/extractor_integrity.py`/`.json`, fournis tels quels, jamais
réimplémentés ni modifiés) détecte toute modification du corps d'une
fonction gelée, même une modification qui ne casserait aucun cas de test
rejoué. Éligibilité : `worktree.json` (Phase 7, worktree Git réel) +
`validation_static.json` (Phase 8) `ACCEPTED` — orthogonal aux Phases 9/10.
Code gelé et registre relus DEPUIS LE WORKTREE patché, jamais du dépôt
principal ; `extractor_integrity.py` chargé dynamiquement sous un nom de
module dédié pour ne jamais entrer en collision avec un import déjà en
cache. Budget de temps explicite sur le parcours du registre, dépassement
traité en erreurs explicites. Toute anomalie (hash différent ou fonction/
fichier introuvable) est un motif de rejet, jamais un passe-droit. Registre
fourni corrigé avant intégration : une clé dupliquée
(`Survey/input_slider.py::set_sliderpoints`) aurait, avec la racine
`worktree_path/Survey` (seule cohérente avec le reste du registre), rejeté
systématiquement tout patch dès le premier run — retirée avant que ce module
ne soit écrit.**

**Partie B (rejeu de `regression_cases/` décrit ci-dessous) : DIFFÉRÉE —
aucune bibliothèque de DOM représentatifs n'est encore curée pour
l'alimenter, même principe que les sous-chantiers déjà différés en
Phase 9/10.**

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

**Statut : TERMINÉE — `Survey/confidence_score.py` + `tools/score_patch_confidence.py`.
Différence assumée par rapport aux Phases 7 à 11 : celles-ci refusent avant
tout effet de bord ; cette phase n'en a aucun (lecture + calcul seulement) et
produit toujours un verdict, même dégradé — seule une véritable incohérence
d'usage (case_id/branch incohérents entre artefacts fournis) reste bloquante.
Quatre critères indépendants (PASS/FAIL/INCONCLUSIVE/MISSING, NOT_RUN pour la
validation live) dérivés du vocabulaire déjà en usage dans les Phases 8/9/10/
11-A, jamais redéfini en dur. Décision par règles ordonnées, jamais une
formule pondérée : statique≠PASS ou intégrité=FAIL ou correctif=FAIL →
REJECT (intégrité toujours dominante) ; correctif=PASS (le reste déjà acquis)
→ HIGH ; sinon MEDIUM avec la raison exacte. Nuance notable : l'intégrité
fonctionne en veto (seul un FAIL bloque), jamais en confirmation positive
requise — une intégrité MISSING ne bloque pas HIGH à elle seule, mais reste
visible telle quelle dans `criteria`. Avertissement systématique, présent
quel que soit le verdict : "régressions" ne couvre à ce jour que le hash des
fonctions gelées (Phase 11-A), pas un rejeu DOM comportemental (Phase 11-B,
non construite) — un HIGH ne garantit donc pas l'absence de régression
comportementale.**

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

**Statut : TERMINÉE — `Survey/human_review.py` + `tools/notify_human_review.py`
+ `tools/check_human_review.py`, via l'infrastructure Telegram déjà existante
dans ce projet (monitoring/alertes bot), recevable sur n'importe quel
appareil (PC, téléphone, tablette). Polling (`getUpdates`), jamais de
webhook — Telegram met en file d'attente côté serveur, une décision prise à
tout moment est récupérée telle quelle à la prochaine invocation, sans
processus persistant à faire tourner. Notifie seulement les cas
`confidence="HIGH"` (Phase 12) ; message composé du symptôme, de la cause
probable et des critères déjà produits, jamais le diff du patch ni une
donnée brute de répondant. Encodage case_id/callback_data par hash SHA256
tronqué (limite Telegram de 64 octets vérifiée), décodage par recherche
symétrique — une seule stratégie, jamais un repli conditionnel. Décision
capturée dans `decision.json`, offset avancé seulement après écriture
durable de tout le lot. Portée strictement limitée à la notification et à la
capture de la décision — aucun merge, commit, ni modification de worktree
déclenché ici (Phases 15/16, hors périmètre).**

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

**Statut : TERMINÉE — `Survey/bem_proposal.py` + `tools/propose_bem_entry.py`.
Déclenchée uniquement par `decision.json` (Phase 13) avec `decision="APPROVED"`
exactement. Fichiers réellement modifiés détectés par réutilisation stricte
des helpers Git de la Phase 8 (`_git_changed_paths`/`_filter_existing_python_files`/
`_resolve_package_root`) ; fonctions ajoutées/modifiées/supprimées détectées
par la même technique AST/hash que `Survey/extractor_integrity.py` (généralisée
à une énumération complète, sans jamais importer ni modifier ce fichier).
Croisement avec `context_selection.json` (Phase 5) pour le contexte déjà
calculé ; tout fichier modifié non anticipé par cette phase est signalé
explicitement. Brouillon composé dans le format exact de l'en-tête de
`BOT_EVOLUTION_MEMORY.md`, uniquement à partir de faits déjà établis — les
sections exigeant un jugement humain ("Patterns couverts"/"Patterns exclus")
portent un marqueur explicite, jamais une prose inventée. N'écrit JAMAIS dans
`BOT_EVOLUTION_MEMORY.md` lui-même — seulement un brouillon séparé, destiné à
une relecture humaine puis un collage manuel. (Mise à jour suite 35 : ce
mode "brouillon à coller" n'est plus le chemin normal — c'est désormais
l'agent de coding qui écrit l'entrée dans son prompt, et ce module ne sert
plus que de filet de sécurité automatique côté Phase 15.)**

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

**Statut : TERMINÉE — `Survey/patch_commit.py` + `tools/commit_patch.py`.
"patch PASS + régression PASS + live PASS" repris intégralement du verdict
déjà calculé par la Phase 12 (`confidence="HIGH"`), jamais redérivé
séparément. "BEM mise à jour" vérifiée par un fait Git observable
(`Survey/BOT_EVOLUTION_MEMORY.md` parmi les fichiers modifiés depuis
`base_sha`, réutilisation stricte des helpers de la Phase 8), jamais une
déclaration — contournable uniquement via une raison explicite non vide.
Sujet de commit mécanique (fonctions réellement modifiées, jamais une
formulation évocatrice inventée), corps toujours généré (case_id, confiance,
fichiers). Refus si un commit référençant déjà ce case_id existe sur la
branche. Worktree déjà propre → traité comme déjà committé, jamais un commit
vide. Jamais de push, jamais de merge — la branche reste séparée.**

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

**Statut : TERMINÉE — `Survey/merge_review.py` + `tools/propose_merge.py`, et
généralisation additive de `Survey/human_review.py`/`tools/check_human_review.py`
(comportement de la Phase 13 vérifié inchangé). Raison impérative de cette
généralisation : Telegram ne fournit qu'un seul flux `getUpdates` par bot —
deux pollers indépendants se voleraient mutuellement leurs mises à jour. Un
seul poller, un seul offset partagé, chaque callback routé vers
`human_reviews/` (Phase 13) ou `merge_reviews/` (ici) selon son préfixe.
Message : branche autofix, branche cible (`worktree.json.source_branch`,
jamais codée en dur), sha/sujet du commit — jamais le diff complet. Ne
déclenche JAMAIS elle-même un git merge, un push, ni une modification de la
branche cible — la confirmation reste un signal à vérifier manuellement.
(Mise à jour suite 35 : le merge local s'exécute désormais seul, via
`Survey/merge_executor.py`, dès `APPROVED` — cette phase-ci, prise
isolément, ne merge toujours pas.)**

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

**Statut : PARTIELLEMENT TERMINÉE — décision délibérée de ne construire, pour
l'instant, que le prérequis de mesure, jamais le mécanisme de merge
automatique lui-même. `Survey/autofix_metrics.py` + `tools/
report_autofix_metrics.py`, purement en lecture seule sur les artefacts des
Phases 2 à 16 (`failure_cases/` comme seule liste exhaustive de case_id).
Compte et rapporte ce qui s'est réellement passé (diagnostics, prompts,
worktrees, validations statiques, rejeux, intégrité, confiance, décisions
humaines, commits, décisions de merge, répartition par stage/module) sans
jamais inventer une mesure que la donnée actuelle ne permet pas : "vrais/faux
positifs" restent des comptages bruts APPROVED/REJECTED, jamais reformulés ;
"régressions" est explicitement marquée non mesurable (pas de boucle de
rétroaction production → patch mergé) ; la répartition par module est
présentée comme un proxy grossier, jamais une catégorie sémantique. Sortie
en instantané horodaté, jamais un fichier écrasé — fait pour tourner à
répétition sur plusieurs semaines. Le mécanisme de merge automatique par
catégorie de confiance reste explicitement différé, en attente de ces
semaines de données réelles.**

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

**Statut : TERMINÉE — le maillon manquant (transport des `failure_cases` de
~100 machines de prod vers la machine de dev) est comblé par
`Survey/fleet_case_upload.py` + `Survey/fleet_case_import.py` (et leurs
façades CLI), réutilisant le client R2 déjà existant du dépôt
(`Management/snap_uploader.py`) plutôt qu'une nouvelle convention. Le bot de
prod lui-même n'est jamais modifié dans sa logique d'exécution — l'uploader
est un outil séparé, invoqué par l'orchestration déjà existante. Vérification
positive (`head_object`) avant tout marquage local "uploadé et vérifié" ;
nettoyage local différé sur un délai de rétention configurable, jamais sans
cette vérification préalable, jamais une suppression groupée silencieuse.
Le schéma "PROD BOTS → stockage des failure_cases → LOCAL/DEV AUTOFIX
WORKER" ci-dessous est désormais entièrement opérationnel.**

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

Note : le nœud « Replay Test » de ce schéma recouvre les niveaux détaillés
en Phase 3 (3A statique, 3B capture enrichie, 3C replay Chromium local,
3D classification `STATIC_DOM`/`BROWSER_CAPSULE`/`TRACE_REPLAY`/
`EXTERNAL_NON_REPLAYABLE`) ; « Live Validation » correspond à la Phase 10,
devenue le chemin normal pour les cas classés `EXTERNAL_NON_REPLAYABLE`.

## Outils complémentaires (hors numérotation 1A-20)

Ces outils ne correspondent à aucune phase numérotée du plan — construits à
la demande de l'opérateur en réaction directe aux Phases 18-20, avant que
ces phases elles-mêmes ne soient closes. Additifs, en lecture seule sur les
artefacts déjà produits, ne modifient aucun fichier existant du pipeline.

**Sécurité du parallélisme** (`Survey/parallel_safety.py`) — deux contrôles
complémentaires, jamais une décision automatique :
- `tools/check_parallel_safety.py` : AVANT le lancement de Codex, compare au
  niveau FICHIER les `code_files` d'au moins deux `context_selection.json`
  (Phase 5). Approximatif par construction (Codex n'a pas encore écrit de
  code) — signalé comme tel dans la sortie. `BOT_EVOLUTION_MEMORY.md`
  (`always_included`) explicitement exclu de la comparaison.
- `tools/check_function_overlap.py` : APRÈS que Codex a produit ses patchs,
  AVANT le merge, compare au niveau FONCTION au moins deux `worktree.json`
  (Phase 7) — réutilise intégralement `Survey/bem_proposal.py::
  _detect_changed_files` (elle-même déjà bâtie sur la Phase 8 et l'AST/hash
  de la Phase 14), jamais réimplémentée. Une fonction ajoutée, modifiée OU
  supprimée par plusieurs worktrees à la fois est un conflit réel signalé
  explicitement ; un fichier partagé sans fonction en commun n'est jamais
  signalé (Git le fusionne normalement) ; un échec de parsing AST est
  signalé "non comparable", jamais deviné ni classé sûr par défaut.

**Déduplication stricte de cases** (`Survey/case_grouping.py` + `tools/
group_duplicate_cases.py`) — regroupe des `failure_cases` déjà diagnostiqués
(Phase 4) mais pas encore engagés en Phase 5, qui partagent une signature
IDENTIQUE (module + ensemble de `matched_signals`, jamais un recouvrement
partiel — choix explicite de l'opérateur). Matérialise chaque groupe de
taille ≥2 comme un `failure_case`/`diagnosis.json` SYNTHÉTIQUES (copie du
représentant, `case_id` remplacé par `group_id`), consommés tels quels par
les Phases 5 à 16 sans aucune modification de leur code. `group_id` dérivé
de la signature (jamais du case_id du représentant) pour qu'un même bug
reconnu plus tard reproduise le même groupe. Limite disclosée : la Phase 9
ne rejoue le patch que contre le représentant, jamais les N membres (se
raccroche au sous-chantier déjà différé "rejeu de cas historiques voisins").
Groupes gelés une fois formés — extension incrémentale d'un groupe déjà
formé différée, documentée plutôt que masquée.

**Transport fleet** (`Survey/fleet_case_upload.py` + `Survey/fleet_case_import.py`)
— le maillon manquant de la Phase 20, désormais implémenté : upload depuis
chaque machine de prod (vérification positive `head_object` avant tout
marquage, nettoyage local différé sur délai de rétention configurable,
jamais sans vérification préalable) et import côté machine de dev
(`fleet_origin.json` en sidecar, `live_validation_possible=false`, aucune
suppression déclenchée côté import). Voir Phase 20 pour le détail complet.

**Orchestrateur autofix** (`Survey/autofix_orchestrator.py`) — lance Claude
Code en mode headless puis enchaîne les Phases 8, 9, 11-A, 12 et 13 jusqu'à la
notification Telegram. Un cas à la fois, jamais en parallèle. Depuis la suite
36, une étape amont (Phases 4 à 7, déduplication, import fleet optionnel)
s'exécute d'abord par défaut ; voir les suites 35 et 36 pour les limites.

**Merge automatique** (`Survey/merge_executor.py`) — merge local `--no-ff`
dès confirmation Telegram, sur la vraie branche cible du dépôt principal ;
jamais de push ni de release (voir suite 35).

**Registre de stabilité prouvée** (`Survey/extractor_stability.py`) —
complément factuel de BEM, calculé depuis l'historique Git réel de
`Survey/extractor_integrity.json` (voir suite 35).

## Ordre concret de développement

Je suivrais exactement cet ordre :

``` text
1A  Observabilité passive — TERMINÉE, validée en live attach
1B  Stabilisation des validators — OUVERTE EN TÂCHE DE FOND (1B.1 et 1B.2 validés)
2   Failure cases normalisés — TERMINÉE (failure_case_builder.py + CLI, 2 correctifs validés)
3A  Replay DOM statique — TERMINÉE (dom_replay_shim.py + failure_replay.py + CLI)
3B  Capture enrichie (browser capsule) — PARTIELLEMENT TERMINÉE
    (3B.1/3B.2/3B.5/3B.6/3B.8/3B.9/3B.10/3B.11 faits — 3B.11 : requêtes
    XHR/fetch externes, cf. historique suite 5 ; 3B.3/3B.4 différées,
    prochain sous-chantier après mesure sur cas réels)
3C  Replay Chromium local — TERMINÉE sur le périmètre retenu (3C.1 + 3C.2 +
    3C.3 + 3C.4) : document principal, scripts/styles/XHR-fetch servis, CSP
    relâchée, état runtime restauré, extraction+validator rejoués et
    comparés, dispatcher réel avec garde-fou de timeout, chargement
    pré-action, TRACE_REPLAY après timeout, comparaison structurée avec
    vocabulaire dédié sans ambiguïté (CORRECTIF_CONFIRME/BUG_PERSISTANT/
    NON_CONCLUANT), oracle unique confirmé (aucune réimplémentation du pari,
    les trois chemins délèguent à action_validator.py) — validé de bout en
    bout (extraction identique + dispatcher SUCCESS + validator
    CORRECTIF_CONFIRME + clic visuellement confirmé) sur le bug
    Decipher/rowpicker qui a motivé ce chantier. Frames/shadow roots/frame
    selection hors périmètre (cf. 3B.3/3B.4). Chantier clos.
3D  Classification de rejouabilité (STATIC_DOM/BROWSER_CAPSULE/TRACE_REPLAY/
    EXTERNAL_NON_REPLAYABLE)
4   Diagnostic automatique — TERMINÉE (failure_diagnosis.py + CLI)
5   Sélection automatique du contexte code — TERMINÉE (context_selector.py + CLI)
6   Génération du prompt Codex — TERMINÉE (prompt_generator.py + CLI, vigilance data ouverte)
7   Patch dans branche isolée — PARTIELLEMENT TERMINÉE (autofix_worktree.py +
    CLI, préparation branche/worktree pour stage="extraction"
    (REPRODUIT/certain) et stage="action" (REPRODUIT +
    real_dispatch_replay BUG_PERSISTANT, 3C étant close) ; résultat persisté
    (worktree.json) ; pas d'invocation automatique d'agent de coding à ce
    stade)
8   Tests statiques — TERMINÉE (static_validator.py + CLI ; compile/import/
    lint Ruff (F821/F822/F823)/tests existants, bornés aux fichiers modifiés
    du worktree ; aucune suite de tests versionnée trouvée dans ce dépôt à ce
    jour)
9   Replay post-patch — TERMINÉE (patch_replay.py + CLI ; rejeu du code
    patché en sous-processus neuf, contre le seul signal avant-patch déjà
    connu de la Phase 4 ; ne valide que sur CORRECTIF_CONFIRME ; suite de cas
    historiques voisins différée)
10  Validation live attach — TERMINÉE (live_validator.py + CLI ; éligible
    seulement sur patch_replay.json NON_CONCLUANT ; garde-fou
    AUTOFIX_LIVE_VALIDATE double-vérifié ; point de vigilance de la fermeture
    réelle de page sur timeout (stage="action") résolu par
    close_page_on_timeout=False dans replay_browser.py) ;
    devient la voie normale des cas EXTERNAL_NON_REPLAYABLE (post-3D)
11  Suite de régression — PARTIELLEMENT TERMINÉE (partie A : hash
    d'intégrité des fonctions gelées, extractor_integrity_gate.py + CLI,
    verdict sur le worktree patché uniquement ; partie B : rejeu de
    regression_cases/ différé, aucune bibliothèque de cas curée)
12  Score de confiance — TERMINÉE (confidence_score.py + CLI ; aucun
    effet de bord, verdict toujours produit ; HIGH/MEDIUM/REJECT par
    règles ordonnées ; avertissement systématique sur la portée limitée
    du critère "régressions" tant que la Phase 11-B n'existe pas)
13  UI/review humaine simplifiée — TERMINÉE (human_review.py + CLI
    notify/check, via Telegram existant, polling getUpdates ; notifie
    seulement confidence=HIGH ; ne merge/commit rien)
14  Proposition BEM — TERMINÉE (bem_proposal.py + CLI ; détection
    fichiers/fonctions par Git+AST/hash) ; depuis la suite 35, devenue un
    filet de sécurité : l'agent de coding écrit lui-même l'entrée BEM
15  Commit automatique — TERMINÉE (patch_commit.py + CLI ; confidence=HIGH
    + decision=APPROVED + BEM vérifiée par fait Git ; sujet mécanique,
    jamais push/merge)
16  Merge semi-automatique — TERMINÉE (merge_review.py + CLI propose_merge,
    généralisation additive de human_review.py ; un seul poller Telegram
    partagé) ; depuis la suite 35, le merge local s'exécute seul
    (merge_executor.py), jamais de push
17  Auto-fix supervisé — PARTIELLEMENT TERMINÉE (autofix_metrics.py + CLI,
    outil de statistiques en lecture seule uniquement ; mécanisme de merge
    automatique par catégorie explicitement différé, en attente de
    plusieurs semaines de données réelles)
18  Boucle autonome attach — BRIQUES EN PLACE, SANS ATTACH LIVE (autofix_orchestrator.py :
    verrou -> décision Telegram -> commit -> merge local -> import -> Phases 4-7
    -> Claude Code headless -> Phases 8-13 ; un seul "Approuver" ; la boucle
    complète en attach live reste hors périmètre)
19  Fleet learning / métriques — CONTRIBUTION PARTIELLE, hors numérotation
    (déduplication stricte de cases, case_grouping.py — résout "Bot A → Bot
    B déjà supporté" sans infrastructure fleet ; le tableau de taux de
    réussite par extracteur reste bloqué, aucune source ne compte les
    extractions réussies)
20  Pipeline de correction séparé de Prod — TERMINÉE (fleet_case_upload.py/
    fleet_case_import.py + CLI ; R2 existant réutilisé ; vérification
    positive avant marquage/suppression ; bot de prod jamais modifié)
```

Le point le plus important est que **1A → 12 constituent le vrai cœur du
système**. Les étapes 13--20 sont principalement de l'automatisation
opérationnelle. Si les validators, le replay et les tests de régression
sont mauvais, automatiser davantage ne fera qu'accélérer la production
de mauvais patches.