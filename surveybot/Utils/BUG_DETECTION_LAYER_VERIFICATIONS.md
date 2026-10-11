# Bug Detection Layer — Vérifications préalables et recensement du corpus

**Statut :** livrable en lecture seule — aucune fonction du bot n'a été modifiée, aucun fichier existant n'a été altéré pour produire ce document.
**Méthode :** lecture directe du code (`Read`/`Grep`/`Glob`/listings read-only), sans sous-agent, pour garder un contrôle total sur le caractère lecture-seule de la tâche. Citations `fichier:ligne` vérifiées au 2026-10-11.
**Rappel de contrainte** : « PROJECT_ARCHITECTURE.md » cité dans la mission n'existe pas sous ce nom exact dans le dépôt (vérifié par recherche de fichier). Le document le plus proche par le nom est `Utils/SURVEYBOT_PROTECTED_CORE_ARCHITECTURE.md`, mais il n'a pas été ouvert dans cette mission (hors des cinq points demandés) — **NON VÉRIFIÉ**, signalé plutôt que supposé.

---

## Mission A — Cinq points levés

### A.1 — `_sanitize_instruction_with_page_context` (`action_dispatcher.py:5934-6000`)

Corps lu intégralement. Ce n'est **pas** une règle de résolution de cible DOM : c'est un filtre de sécurité de contenu qui peut remplacer la **valeur de réponse elle-même** avant la recherche de l'élément, dans le seul chemin de repli historique (`label == valeur`, après l'échec ou l'absence du chemin `target_id`, cf. `:8332-8343`).

- **Garde d'entrée** (`:5940-5941`) : ne s'applique que si `itype` ∈ `{radio, checkbox, dropdown}` et si le libellé normalisé n'est pas vide ; sinon retourne le label inchangé.
- **Lecture de contexte** (`:5944-5950`) : récupère les options visuellement présentes sur la page (`_get_visible_options`) et le texte visible de la page en minuscules (`_get_page_text_lc`), pour détecter une « question de secteur/emploi » (mots-clés `secteur, industrie, travaillez, employ, domaines`).
- **Exception secteur** (`:5968-5972`) : si la question porte sur le secteur/l'emploi et qu'une option « Aucune de ces réponses »/« Aucun de ces choix » existe, la retourne directement, sans même regarder la valeur proposée.
- **Substitution anti-disqualification** (`:5974-5989`) : si la valeur proposée contient un mot jugé « à risque » (`non, jamais, certainement pas, aucun, aucune, je prfère ne pas le dire, preferer ne pas, none, no, never` — notez la coquille `prfère`, présente telle quelle dans le code), elle est remplacée, dans cet ordre de priorité : (1) l'option littérale « Oui » si elle existe, (2) une option contenant un mot positif (`oui, souvent, parfois, rgulièrement, hebdomadaire, mensuel` — autre coquille `rgulièrement`), (3) une option neutre (`je ne sais pas, je ne m'en souviens pas, neutre`).
- **Cas ordinateur/Internet** (`:5991-5997`) : si la page mentionne `ordinateur, computer, internet, e-mail, email`, force « Oui » si cette option existe — indépendamment de toute détection de mot « à risque ».
- **Sinon** (`:5999-6000`) : retourne le label inchangé (« rien à corriger »).

**Observabilité** : la valeur substituée reste **locale à l'appel** (`action_dispatcher.py:8339-8341`, `label = safe_label`). Le `value` effectivement persisté dans `actions_requested.json`/`action_trace.json["requested_actions"]` provient de la liste `actions` passée à `execute_actions_plan`/`record_validation_failure` (`browser_capsule.py:316-325`), jamais réécrite par cette fonction. **La substitution n'est donc pas observable dans les artefacts persistés d'un cas d'échec** : si le bot répond « Oui » alors que le modèle de réponse avait proposé « Non », aucun artefact ne porte la trace de cette divergence — seule la valeur originale demandée apparaît, jamais la valeur réellement cliquée. Ce point n'avait pas été recensé dans l'inventaire précédent (`BUG_DETECTION_LAYER_INVENTAIRE.md`) car il concerne la valeur demandée, pas la cible ou la méthode de clic — à ajouter à une future révision du §6.2 (règles d'action) de ce document.

### A.2 — Heuristique « champ générique / filtre de recherche »

**Nom exact de la fonction :** `_is_auxiliary_text_for_choice_group(driver, el, container, question: str) -> bool` (`dom_analyzer.py:321-460`).
**Appelant unique identifié :** `dom_analyzer.py:4169-4171`, dans la boucle qui construit les blocs `text`/`textarea` autonomes (« singles ») : `if itype in ("text","textarea") and _is_auxiliary_text_for_choice_group(...): log_debug(...); continue`. Un retour `True` fait **disparaître entièrement** le champ candidat — il n'est ni fusionné ni réécrit, il n'est simplement jamais émis comme bloc. Aucune trace de cette exclusion n'est conservée au-delà du log debug `[DOM_DEBUG] skip_aux_text_with_choice_group` (`:4170`).

**Prérequis strict** (`:330-364`) : le conteneur doit déjà contenir ≥2 options de choix (`radio`/`checkbox`/`role=radio|checkbox`/`button`/`a[role=button]`, en excluant les libellés de navigation `next, suivant, continue, continuer, back, retour, submit, valider`) — sinon retour `False` immédiat.

**Deux cas à retour direct, hors scoring** :
- Inline « Autre, précisez » (`:366-397`) : si le champ est rendu dans le même wrapper d'option (`label`, `li`, `.answer_options`, `.option`, `.form-check`) qu'un contrôle radio/checkbox — détection par ancêtres XPath puis garde-fou JS (`el.closest(...)`) — retourne `True` directement.
- Placeholder de filtre/recherche (`:404-413`) : champ sans label propre (`has_own_label` faux) dont le `placeholder` matche `\bsearch\b|\brecherch\w*\b|\bfilter\w*\b|\bfiltr\w*\b` — retourne `True` directement.

**Scoring (seuil réel, documenté dans la docstring `:328` : « score >=3 sur 4 signaux DOM ») :**
1. `not has_own_label` (`:452-453`) — ni label associé, ni `aria-label`, ni `aria-labelledby`.
2. `generic_q` (`:454-455`, calculé `:421-427`) — question vide, ou dans `{other, autre, enter your answer here, type your answer, please specify}`, ou contenant `choose`/`select`, ou chevauchement lexical `overlap >= 0.7` avec les mots des options.
3. `generic_placeholder or not is_required` (`:456-457`) — placeholder dans `{enter your answer here, type your answer, other, please specify}`, ou attribut `required` absent/faux.
4. `has_choice_instruction` (`:458-459`, calculé `:444-449`) — texte du conteneur contenant `choose exactly, select all that apply, select one, choose one`.

Seuil : `score >= 3` sur ces 4 critères (`:460`). Contrairement à ce qu'une lecture partielle antérieure avait laissé entendre, **ce seuil est documenté** dans la docstring de la fonction (`:328`), pas seulement déduit du code.

**Ce qui en reste de visible après coup** : rien dans les artefacts persistés — seulement le log debug mentionné ci-dessus, lui-même non structuré et non capturé dans `validation_report.json`/`question_blocks.json`.

### A.3 et A.4 — `question_block_analyzer.py` : appelants et coexistence `QuestionBlock`/dict

**Recherche d'appelants (dépôt entier, code + tests + outils) :** `grep -r "question_block_analyzer"` ne trouve, en dehors du fichier lui-même (`Survey/question_block_analyzer.py:1`, son propre en-tête), que des mentions dans deux documents (`Utils/PLAYWRIGHT_NATIVE_MIGRATION.md:102,253,679` — journal de migration historique) et dans l'inventaire produit lors de la mission précédente (`Utils/BUG_DETECTION_LAYER_INVENTAIRE.md`, auto-référence). **Aucun import, aucun appel, dans aucun fichier `.py` du dépôt (code de production, tests, outils) ne référence ce module.** `grep -r "QuestionBlock\("` confirme que les 7 sites de construction de `QuestionBlock(...)` (`:248, :302, :337, :442, :466, :488, :518`) sont tous internes à `question_block_analyzer.py` lui-même.

**Conclusion A.3** : le module est, par cette lecture, **intégralement non intégré au pipeline réel** — ni appelé par `dom_analyzer.py`, ni par les validateurs, ni par les tests, ni par aucun outil (`tools/`). Il ne peut être qualifié de « mort » avec une certitude absolue (un mécanisme d'import dynamique par nom de chaîne échapperait à ce grep), mais aucun indice d'un tel mécanisme n'a été trouvé.

**Conclusion A.4** : `Survey/dom_analyzer.py:analyze_dom(driver) -> List[Dict[str, Any]]` (signature exacte, `:4806`) — confirmée par l'annotation de type elle-même et par des dizaines de sites `question_blocks.append(block)`/`question_blocks.append(_blk)` partout dans le fichier — **retourne exclusivement des `dict`**, jamais des instances de `QuestionBlock`. C'est ce retour qui alimente tout le reste du pipeline réel (`question_block_validator.py`, `action_validator.py`, `prompt_builder.py`, `Survey/autofix/failure_case_builder.py`).

La « coexistence » visible dans `prompt_builder.py:filter_blocks_for_openai` (`:990-1039`, ex. `:997 : it = getattr(qb, "itype", None) or qb.get("itype")`) est du code défensif à double lecture (attribut d'objet **ou** clé de dict), mais **n'est jamais réellement exercée côté objet** dans le fonctionnement actuel : `getattr(un_dict, "itype", None)` renvoie toujours `None` pour un `dict` (un `dict` n'a pas d'attribut `.itype`), donc la branche `qb.get("itype")` est la seule qui s'exécute en pratique, puisque seuls des `dict` (issus de `analyze_dom`) traversent ce chemin. **Il n'existe aucune fonction de conversion entre les deux formes** — il n'y a, dans les faits, qu'une seule forme réellement en circulation (`dict`) ; la forme `QuestionBlock` (dataclass) est produite et consommée uniquement à l'intérieur de son propre fichier, jamais par le reste du pipeline. Ceci corrige/précise le point `UNCLEAR` laissé dans `Utils/BUG_DETECTION_LAYER_INVENTAIRE.md` (§0.4) : il n'y a pas d'incohérence active entre deux représentations concurrentes dans le pipeline réel, seulement un module parallèle non branché.

### A.5 — Chemin de la raison d'échec d'un cas CTA

**Production du champ `reason` de `meta.json`** : `Survey/failure_recorder.py:42`, littéralement `reason=f"{stage}_validation_failure"` — toujours l'une des deux chaînes `"extraction_validation_failure"` ou `"action_validation_failure"`, jamais un texte plus spécifique (ni codes de clic, ni mention CTA). C'est exactement ce que `_resolve_stage` (`Survey/autofix/failure_case_builder.py:533-547`) relit pour classer un cas, en repli sur le nom du dossier de snapshot si `report["stage"]` est absent — et cette fonction **ne reconnaît que `"action"` et `"extraction"`** (`:536 : if stage in ("action", "extraction")`), avec retour `"unknown"` sinon. **Aucune valeur `"cta"` n'existe nulle part dans le vocabulaire de `stage` du dépôt** — confirmé par une recherche exhaustive de `stage="..."` sur l'ensemble du code et des tests (seuls `"action"`/`"extraction"` apparaissent, des dizaines de fois).

**Pourquoi** : les deux seuls points d'instrumentation de l'observabilité passive sont `_install_extraction_observer` (enveloppe `dom_analyzer.analyze_dom`) et `_install_action_observer` (enveloppe `action_dispatcher.execute_actions_plan`) — `Survey/page_snapshot.py:637-772`. **Aucun hook équivalent n'existe pour `cta_handler.py`.** Les clics de navigation CTA (`Survey.input_handler.try_click_navigation_cta_any_context`) sont appelés **directement depuis `survey_executor.py`** (`:2324, :2357, :2397, :2455, :2627, :2737, :2797`) et depuis des fonctions de gestion d'écran dans `action_dispatcher.py` (`handle_start_screen:7424-7428`, `handle_consent_screen` zone `:6301-7084`, etc.) — **en dehors de `execute_actions_plan`**, donc jamais vu par `_install_action_observer`, jamais soumis à `action_validator.validate_actions`, jamais enregistré par `record_validation_failure`.

**Conclusion A.5** : il n'existe aujourd'hui **aucun chemin de capture de failure case pour un échec CTA** — ni stage dédié, ni raison dédiée, ni hook d'observation. Un clic CTA raté ne produit ni `meta.json`, ni `validation_report.json`, ni aucun artefact. Cette conclusion **confirme et précise** la lacune déjà notée au §2 de `Utils/BUG_DETECTION_LAYER_INVENTAIRE.md` (« rien n'est persisté » côté CTA) : ce n'est pas seulement que les signaux existants de `cta_handler.py` ne sont pas écrits sur disque — c'est que le mécanisme même qui déciderait de les écrire (l'observateur Phase 1A) ne couvre pas cette famille.

---

## Mission B — Corpus de cas réels disponible localement

**Emplacements trouvés** (trois, non unifiés) :
1. `failure_cases/<case_id>/` — 10 dossiers avec `manifest.json` + `artifacts/`.
2. `failure_cases/replay_ok/<case_id>/` — 3 dossiers supplémentaires (même structure), dont un (`20260929_033834_extraction_validation_failure`) duplique un cas déjà listé en (1) ; les deux autres (`20260924_180714_action_validation_failure`, `20260925_211251_action_validation_failure`) n'existent **nulle part ailleurs**.
3. `diagnoses/<case_id>/diagnosis.json` — 5 dossiers, dont 2 (`20260923_211155_extraction_validation_failure`, `20260925_211543_action_validation_failure`) **n'ont pas de dossier correspondant dans `failure_cases/`** — un diagnostic existe sans que le manifeste/artefacts d'origine soit encore présent à cet emplacement. **NON VÉRIFIÉ** : pourquoi ces deux cas sont absents de `failure_cases/` (nettoyage après diagnostic ? déplacement ? suppression manuelle ?) — aucune politique de rétention de `failure_cases/` n'a été lue dans cette mission pour trancher.

**Total : 14 identifiants de cas distincts.** Aucun fichier d'index unique ne les réunit ; ce recensement a nécessité de croiser les trois emplacements.

### Tableau du corpus

| case_id | emplacement(s) | famille | provider | itype / target | failure_types | verdict replay (`diagnosis.json`, si présent) | vérité terrain documentée dans `SURVEYBOT_AUTOFIX_PLAN.md` |
|---|---|---|---|---|---|---|---|
| 20260911_160609_extraction_validation_failure | failure_cases + diagnoses | EXTRACTION | community.focaldata.com | — / — | missing_block | `NON_REPRODUIT` (confiance `plausible`, 70 `evaluate()` déclinés sur le DOM figé) | inconnue — aucune mention par cet identifiant dans le plan |
| 20260914_154721_extraction_validation_failure | failure_cases | EXTRACTION | enquetes.panelia.fr | — / — | missing_block | aucun diagnostic trouvé | inconnue |
| 20260923_195439_extraction_validation_failure | failure_cases | EXTRACTION | mcresearch2.co1.qualtrics.com | checkbox / group_f65630091ad2 | choice_without_options | aucun diagnostic trouvé sous cet identifiant exact (un diagnostic `20260923_211155` du même jour/provider existe, mais horodatage différent — **NON VÉRIFIÉ si même cas ou cas distinct**) | inconnue |
| 20260923_201701_extraction_validation_failure | failure_cases | EXTRACTION | env3.surveysip.com | dropdown / single_d2b27071264f | question_text_is_help | aucun diagnostic trouvé | inconnue |
| 20260929_033834_extraction_validation_failure | failure_cases + replay_ok + diagnoses | EXTRACTION | websurvey5.metrixlab.com | radio / group_a6447bcc2032 | missing_block | `REPRODUIT` (confiance `certain`, comparaison DOM identique) | **plausible, non confirmée par l'identifiant exact** : le plan mentionne « L'extraction MetrixLab est corrigée par le premier correctif externe adopté (`external_fix_hidden_consent_radio_question.py`) » — même provider et même `failure_type` (`missing_block`/consentement), mais le texte du plan ne cite pas ce `case_id` littéralement ; laissé **inconnue** par discipline de lecture |
| 20261002_064948_action_validation_failure | failure_cases + diagnoses | ACTION | websurvey5.metrixlab.com | radio / group_e52f6cc1915e | dispatcher_reported_failure | `REPRODUIT` (confiance `certain`), `real_dispatch_replay.status=FAILURE`, `dispatcher_success=False` au replay | **plausible par date/provider** (« deuxième cycle réel, stage action, MetrixLab, radioQT, 2–3 octobre 2026 »), **non confirmée par l'identifiant exact** dans le texte du plan lu — laissé **inconnue** |
| 20261003_205317_action_validation_failure | failure_cases | ACTION | websurvey5.metrixlab.com | radio / group_e52f6cc1915e | dispatcher_reported_failure | aucun diagnostic trouvé dans `diagnoses/` | **confirmée par horodatage exact** : le plan décrit « Troisième session sur la même page (3 octobre 2026, soir) : arrêt `NO_CHANGES` notifié... Case neuf sur `base_sha` `1513531` ». L'agent de correction a conclu à tort que l'échec observé était « le comportement correct et voulu » — **vérité terrain documentée : bug réel, conclusion de l'agent erronée** |
| 20261004_120106_action_validation_failure | failure_cases | ACTION | websurvey5.metrixlab.com | radio / group_e52f6cc1915e | dispatcher_reported_failure | aucun diagnostic trouvé | **confirmée par identifiant exact** : plan, « Case `120106`... Le rejeu conclut `BUG_PERSISTANT`... score `REJECT` » — **vérité terrain : correctif non retenu, bug persistant** |
| 20261004_122132_action_validation_failure | failure_cases | ACTION | websurvey5.metrixlab.com | radio / group_e52f6cc1915e | dispatcher_reported_failure | aucun diagnostic trouvé | **confirmée par identifiant exact** : plan, « Case `122132` ensuite : arrêt `historical_duplicate` (équivalent `120106`, issue `REJECT`) » — **vérité terrain : doublon du cas précédent, même issue** |
| 20261004_205100_action_validation_failure | failure_cases | ACTION | websurvey5.metrixlab.com | radio / group_e52f6cc1915e | dispatcher_reported_failure | aucun diagnostic trouvé | **confirmée par identifiant exact** : plan, « Case `205100`... rejeu `NON_CONCLUANT`... `validate_patch_live` a rendu `CORRECTIF_CONFIRME`... mergé » — **vérité terrain : bug réel, correctif confirmé en live et mergé** |
| 20260924_180714_action_validation_failure (dans `replay_ok/`) | replay_ok uniquement | ACTION | surveys.lifepointspanel.com | radio / group_3438ae863d64 | dispatcher_false_negative | aucun diagnostic trouvé | inconnue |
| 20260925_211251_action_validation_failure (dans `replay_ok/`) | replay_ok uniquement | ACTION | surveys.lifepointspanel.com | radio / group_c0a4d319dc62 | dispatcher_reported_failure | aucun diagnostic trouvé | inconnue |
| 20260923_211155_extraction_validation_failure | diagnoses uniquement | EXTRACTION | — (non lu, pas d'artefact manifest disponible) | checkbox / group_f65630091ad2 | choice_without_options | `NON_REPRODUIT` (confiance `plausible`, 92 `evaluate()` déclinés) | inconnue |
| 20260925_211543_action_validation_failure | diagnoses uniquement | ACTION | — (non lu, pas d'artefact manifest disponible) | radio / group_70d2fbdc16e2 | dispatcher_false_negative | `REPRODUIT` (confiance `plausible`), `real_dispatch_replay.status=SUCCESS`, `dispatcher_success=True` au replay | inconnue |

### Synthèse

**Par famille** : 14 cas EXTRACTION+ACTION, dont 6 EXTRACTION et 8 ACTION. **Zéro cas CTA** — cohérent avec la conclusion A.5 (aucun mécanisme de capture CTA n'existe).

**Par classe de rejouabilité (verdict `replay` quand un `diagnosis.json` existe, 7 cas sur 14)** : `REPRODUIT` ×3 (20260929_033834, 20261002_064948, 20260925_211543), `NON_REPRODUIT` ×2 (20260911_160609, 20260923_211155). Les 7 autres cas n'ont pas de diagnostic retrouvé dans `diagnoses/` à cet emplacement — **NON VÉRIFIÉ si un diagnostic existe ailleurs** (hors périmètre de recherche de cette mission, limité à `diagnoses/` et `failure_cases/`).

**Lacunes constatées (telles que demandées par la mission) :**
- **Aucun cas où le validateur a conclu à un succès (`ok: true`) alors qu'un bug réel existait** — par construction, `failure_recorder.record_validation_failure` ne capture jamais un passage que le validateur a jugé `ok` (`failure_recorder.py:34-35`), donc un tel cas ne peut mathématiquement pas apparaître dans ce corpus, quelle que soit sa fréquence réelle. C'est exactement la lacune déjà identifiée au §5 de `Utils/BUG_DETECTION_LAYER_INVENTAIRE.md` (priorité 1, point 1).
- **Aucun cas CTA** du tout (confirmé ci-dessus).
- **Corpus concentré sur deux providers** pour la famille ACTION : 6 des 8 cas ACTION partagent le même provider (`websurvey5.metrixlab.com`) et le même `target_id` (`group_e52f6cc1915e`) — une seule question radio QT, rejouée à travers plusieurs sessions de l'agent correcteur. Les 2 cas restants (`surveys.lifepointspanel.com`) sont les seuls autres représentants ACTION. Aucune diversité de provider pour `dispatcher_false_negative` en dehors de ces deux-là.
- **Corpus dispersé sur trois emplacements sans index unique** (déjà signalé ci-dessus) — un futur corpus d'évaluation (BDL-2 au sens de `Utils/BUG_DETECTION_LAYER_TRACKING.md`) devra d'abord répondre à la question « où sont réellement tous les cas » avant de pouvoir calculer des taux fiables.
- **Vérité terrain documentée seulement pour 4 des 14 cas** (tous ACTION, tous de la page MetrixLab `radioQT`, tous recoupés par identifiant exact cité dans `SURVEYBOT_AUTOFIX_PLAN.md`) — aucune vérité terrain documentée pour la famille EXTRACTION dans ce périmètre de lecture.

---

## Mission C — Coût d'une capture avant/après inconditionnelle

Établi uniquement à partir du code (`Survey/browser_capsule.py`, `Survey/page_snapshot.py`, `Survey/frame_utils.py`), sans exécution du bot. Deux sous-ensembles bien distincts, car l'un est **déjà inconditionnel aujourd'hui** et l'autre ne l'est pas.

### Ce qui est déjà inconditionnel aujourd'hui (à chaque appel de `execute_actions_plan`, pas seulement en cas d'échec)

D'après `page_snapshot.py:687-708` :
1. `_capture_current_outer_html` (`pre_action_dom`, `:262-286`) : 1 sélection de frame (`_select_best_frame_chain`) + 1 appel `evaluate()` CSSOM-safe (borné `MAX_STYLE_TAGS=500`, `MAX_RULES_PER_SHEET=5000`, `browser_capsule.py` n'étant pas concerné ici — bornes propres à `page_snapshot.py:88-123`).
2. `capture_runtime_state` (« avant », `browser_capsule.py:191-217`) : **jusqu'à `_MAX_ELEMENTS=40` appels `evaluate()` séparés**, un par élément résolu (`_capture_runtime_facts_in_context`, `:179-188`, boucle `for label, xpath in xpaths.items(): ctx.evaluate(...)` — **pas un seul appel batché pour tous les éléments**), plus un changement de frame (`switch_to_frame_chain`) partagé pour tous.
3. `install_mutation_observer` (`:266-280`) : 1 appel `evaluate()` additionnel.

**Total déjà payé aujourd'hui, par exécution de plan, avant même de savoir si l'action réussira** : jusqu'à **~42 appels navigateur** (1 + jusqu'à 40 + 1), plus les changements de contexte de frame associés. Ce coût est payé pour **chaque appel** de `execute_actions_plan`, pas pour chaque action individuelle d'un plan qui en contiendrait plusieurs (la granularité d'instrumentation est le plan, pas l'action unitaire — `page_snapshot.py:687`).

### Ce qui n'est déclenché qu'en cas d'échec (`report.get("ok") is False`, `page_snapshot.py:717-759`)

1. `capture_runtime_state` (« après ») : à nouveau jusqu'à **40 appels `evaluate()`** (même coût que le « avant », doublé).
2. `collect_mutation_observer` : 1 appel `evaluate()`.
3. `build_action_trace` : pur Python, coût navigateur nul.
4. `record_validation_failure` → `dump_page_snapshot(artifact_profile="action_validation", ...)`, qui ajoute :
   - **`_wait_dom_settle`** (`page_snapshot.py:16-72`) : boucle bloquante, `max_wait_s=6.0`, `poll_s=0.25`, exige `readyState=="complete"` **et** 3 lectures identiques consécutives d'une signature DOM (longueur `outerHTML`, longueur `innerText`, nombre de champs) avant de continuer. **Coût pire cas : jusqu'à 6 secondes d'attente bloquante**, à chaque capture.
   - Re-sélection de frame + 3 appels `evaluate()` pour `meta.json` (`title`, `ready_state`, `dom_sig`, `:364-380`).
   - 1 appel `evaluate()` CSSOM-safe pour `post_action_dom.html` (`:417`).
   - 1 appel `page.screenshot()` (`:471`) — taille du fichier **non mesurable depuis le code** (dépend de la résolution de viewport réelle).
   - `collect_external_scripts` (`browser_capsule.py:373-443`) : 1 `evaluate()` (liste des `src`) + 1 session CDP dédiée (`new_cdp_session` + `Page.enable` + `Page.getResourceTree`, 3 appels CDP) + **jusqu'à `_MAX_SCRIPTS=60` appels `Page.getResourceContent`**, chacun borné à `_MAX_SCRIPT_CHARS=512 KiB`, total borné à `_MAX_SCRIPTS_TOTAL_CHARS=4 MiB`.
   - `collect_external_stylesheets` (`:467-533`) : même gabarit, **session CDP distincte** (nouvelle `new_cdp_session`, pas de réutilisation de celle des scripts) — jusqu'à 60 appels CDP supplémentaires, jusqu'à 4 MiB supplémentaires.
   - `collect_xhr_fetch_resources` (`:579-651`) : 1 `evaluate()` (lecture `performance.getEntriesByType('resource')`) + **jusqu'à `_MAX_REQUESTS=60` appels `evaluate()`** (un `fetch(..., cache:'only-if-cached')` par requête), bornés à `_MAX_REQUEST_CHARS=512 KiB` chacun, `_MAX_REQUESTS_TOTAL_CHARS=4 MiB` au total.
   - Dump des frames (`_dump_frames_best_effort`, `page_snapshot.py:149-252`, via `frame_utils.iter_frame_chains(driver, max_depth=2)`) : **aucune borne numérique sur le nombre de frames** — `iter_frame_chains` (`frame_utils.py:114-136`) énumère récursivement toutes les iframes réellement présentes jusqu'à profondeur 2, sans plafond (`NON VÉRIFIÉ` quel est ce nombre sur une page réelle — dépend du provider). Pour chaque chaîne candidate : jusqu'à 4 `evaluate()` (url, title, longueur de texte, nombre de champs) pour décider si elle est retenue (`inputs_count<=0 and text_len<200` → ignorée, `:214-215`), puis, si retenue, 1 `evaluate()` CSSOM-safe de plus.
   - **Uniquement pour un profil non-action** (donc jamais pour une capture ACTION, seulement EXTRACTION, `page_snapshot.py:437,475`) : `dom_body.html` (1 `evaluate()`), `page_source.html` (1 appel `.content()`), `body_text.txt` (1 `evaluate()`), `page.mhtml` (1 appel CDP `Page.captureSnapshot`). Confirmé empiriquement : les 8 cas ACTION du corpus (Mission B) n'ont **aucun** de ces 4 artefacts, alors que les 6 cas EXTRACTION les ont tous.

### Estimation du coût d'une généralisation inconditionnelle

- **Avant/après « faits runtime » seul (sans le reste)** : rendre la capture « après » (point 1 ci-dessus) inconditionnelle double un coût déjà payé pour le « avant » — environ **+40 appels navigateur** et quelques dizaines de Ko de JSON par exécution de plan (chaque fait mesuré tient en quelques centaines d'octets : `tag, nativeInput, value, checked, ..., text (≤300 car.), rect`). C'est le sous-ensemble le moins coûteux à généraliser, puisque sa moitié « avant » est déjà systématique.
- **Snapshot disque complet (screenshot, MHTML, scripts/feuilles de style/XHR externes, frames) rendu inconditionnel** : c'est la partie réellement coûteuse. Pire cas théorique par exécution de plan : jusqu'à ~4 MiB (scripts) + ~4 MiB (feuilles de style) + ~4 MiB (XHR/fetch) ≈ **jusqu'à 12 MiB de données supplémentaires**, plus jusqu'à ~180 appels CDP/evaluate() additionnels (60+60+60), plus un nombre non borné d'allers-retours pour le dump des frames, **plus jusqu'à 6 secondes d'attente bloquante** (`_wait_dom_settle`) à chaque exécution de plan, pas seulement en cas d'échec.
- **Ce qui n'est pas mesurable depuis le code seul, et comment le mesurer** :
  - Nombre réel d'appels à `execute_actions_plan` par parcours d'enquête complet : **NON VÉRIFIÉ** — à mesurer en comptant les occurrences du log `log_info("[TARGET]", "execute_action raw=...")` (`action_dispatcher.py:7921`, déjà inconditionnel) sur une exécution réelle du bot, ou en ajoutant un compteur temporaire.
  - Nombre réel d'iframes par page réelle (donc coût réel du dump de frames) : **NON VÉRIFIÉ** — dépend du provider ; mesurable en comptant `meta.json["frames_count"]` sur un échantillon de snapshots déjà produits (`page_snapshot.py:551`), ou en instrumentant `iter_frame_chains`.
  - Latence réelle d'un aller-retour `evaluate()`/CDP sur la machine de production (pas seulement leur nombre) : **NON VÉRIFIÉ** — dépend de la machine, du navigateur, de la charge réseau de la page cible ; mesurable par chronométrage direct en conditions réelles.
  - Taille réelle moyenne d'un `viewport.png`/`page.mhtml` : **NON VÉRIFIÉ** — mesurable en inspectant la taille des fichiers déjà présents dans `failure_cases/*/artifacts/` (non fait dans cette mission, qui s'est concentrée sur la structure du code, pas sur la taille des artefacts déjà produits).
  - Fréquence réelle à laquelle `_wait_dom_settle` atteint son plafond de 6 s plutôt que de se stabiliser plus vite : **NON VÉRIFIÉ** — mesurable par chronométrage sur un échantillon de pages réelles.

---

## Liste finale des points restés non vérifiés

- `PROJECT_ARCHITECTURE.md` introuvable sous ce nom exact ; `Utils/SURVEYBOT_PROTECTED_CORE_ARCHITECTURE.md` non ouvert dans cette mission.
- Pourquoi deux cas de `diagnoses/` (`20260923_211155`, `20260925_211543`) n'ont pas de dossier correspondant dans `failure_cases/` — politique de rétention non lue dans cette mission.
- Si `20260923_195439_extraction_validation_failure` (dans `failure_cases/`) et `20260923_211155_extraction_validation_failure` (dans `diagnoses/`, même jour, même provider/`target_id`) sont le même cas sous deux horodatages, ou deux cas distincts — non tranché.
- Corrélation par date/provider entre `20260929_033834` (extraction MetrixLab) et la mention « premier correctif externe adopté » du plan, et entre `20261002_064948` et la « première session » du deuxième cycle radioQT — plausibles mais **non confirmées par un identifiant de cas cité littéralement** dans le texte du plan lu ; laissées « inconnue » par discipline de lecture plutôt que déduites.
- Vérité terrain pour les 2 cas de `replay_ok/` propres à `surveys.lifepointspanel.com` (`20260924_180714`, `20260925_211251`) — aucune mention trouvée dans le plan.
- Existence éventuelle d'un `diagnosis.json` pour les 7 cas du corpus qui n'en ont pas sous `diagnoses/` — recherche limitée à ce seul répertoire dans cette mission.
- Mécanisme d'import dynamique éventuel qui appellerait `question_block_analyzer.py` par un chemin que `grep` ne peut pas détecter (chaîne construite dynamiquement) — jugé improbable mais pas formellement exclu.
- Tous les points chiffrés de la Mission C marqués `NON VÉRIFIÉ` ci-dessus (fréquence réelle d'appel, nombre réel d'iframes, latences réelles, tailles réelles de fichiers, fréquence réelle du plafond de 6 s) — tous nécessitent une mesure en exécution, pas seulement une lecture de code.
- Si la substitution de `_sanitize_instruction_with_page_context` (A.1) laisse une trace *indirecte* détectable (ex. divergence entre `actions_requested.json[].value` et l'état réel observé dans `runtime_state.json`/`action_trace.json["after"]`) — plausible par construction (la cible résolue et son état observé correspondraient à l'option substituée, pas à celle demandée) mais non vérifié sur un exemple concret dans cette mission.

---

**Titre de commit proposé :**
```
docs: Verifications prealables et corpus pour la Bug Detection Layer (BDL-0)
```
