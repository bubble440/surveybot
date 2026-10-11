# Bug Detection Layer — Index unique du corpus et points ouverts

**Statut :** livrable en lecture seule — aucun cas existant n'a été déplacé, renommé ou supprimé ; aucune fonction du bot n'a été modifiée pour produire ce document.
**Méthode :** lecture directe (`Read`/`Grep`/listings et scripts Python read-only ponctuels pour mesurer des tailles de fichiers déjà sur disque), sans sous-agent. Citations `fichier:ligne` et chemins vérifiés au 2026-10-11.
**Emplacement élargi** : en plus du dépôt Git (`C:\projects\Surveys\surveybot`), le dossier local accessible `C:\projects\Surveys\snapshots\` (hors dépôt, sibling de `surveybot/`) a été inspecté — c'est lui que `manifest.json["source_snapshot"]` désigne comme origine de chaque case, et il s'est révélé être **le véritable emplacement le plus complet du corpus**, non cité dans les missions précédentes.

---

## Mission A — Index unique du corpus

### Emplacements trouvés (cinq, non unifiés par aucun fichier d'index)

1. **`C:\projects\Surveys\snapshots\<case_id>\`** (hors dépôt Git) — écriture brute et systématique par `dump_page_snapshot`/`record_validation_failure` (`Survey/page_snapshot.py:325-330` : `default_root = "./snapshots"` quand `RUN_ENV != "prod"`). **38 dossiers de cas** + 1 dossier `Golden_DOMs/` (voir note ci-dessous, non compté comme un cas).
2. **`failure_cases/<case_id>/`** (dans le dépôt) — copie normalisée et assainie, produite au cas par cas par l'outil manuel `tools/create_failure_case.py` (voir Mission B.1). **10 dossiers.**
3. **`failure_cases/replay_ok/<case_id>/`** (sous-dossier du précédent) — **3 dossiers**, dont 1 duplique un cas déjà en (2).
4. **`diagnoses/<case_id>/diagnosis.json`** — produit au cas par cas par l'outil manuel `tools/diagnose_failure.py`. **5 dossiers.**
5. **`replayability/<case_id>/classification.json`** — produit au cas par cas par l'outil manuel `tools/classify_replayability.py` (`Survey/autofix/replayability_classifier.py:225`, `out_root: ... = "replayability"`). **3 dossiers.**

**Total : 38 identifiants de cas distincts** (tous présents dans `snapshots/`, qui s'avère être le sur-ensemble strict de toutes les autres localisations — vérifié : les 14 identifiants déjà recensés dans une mission précédente sont tous retrouvés dans `snapshots/`, plus 24 identifiants supplémentaires qui n'existent **nulle part ailleurs** que dans `snapshots/`).

**Note sur `Golden_DOMs/`** : ce sous-dossier de `snapshots/` ne contient qu'un seul élément, une copie manuelle complète du cas `20260924_180714_action_validation_failure` (fichiers identiques, mêmes tailles). Aucun fichier `.py` du dépôt ne référence la chaîne `"Golden_DOMs"` (recherche exhaustive, aucun résultat) — ce n'est donc pas un mécanisme du code, mais une curation manuelle, cohérente avec la mention du plan « corpus de pages saines (Phase 11-B) » resté à constituer. Non compté comme un cas distinct dans les totaux ci-dessus.

### Tableau de l'index

Colonnes : `case_id` · emplacements (abréviations : **SNAP**=`snapshots/`, **FC**=`failure_cases/`, **FC-OK**=`failure_cases/replay_ok/`, **DIAG**=`diagnoses/`, **REPL**=`replayability/`) · famille · provider · itype/target_id · failure_types (lus dans `validation_report.json`, présent à la racine de chaque dossier `snapshots/<id>/`) · diagnostic (verdict de replay s'il existe) · vérité terrain documentée (sinon « inconnue ») · doublon potentiel.

| case_id | emplacements | famille | provider | itype / target_id | failure_types | diagnostic (verdict) | vérité terrain | doublon potentiel |
|---|---|---|---|---|---|---|---|---|
| 20260907_142347_action_validation_failure | SNAP | ACTION | sentry.cloudresearch.com | radio / group_cc2062d86e9d | action_value_not_in_registry_options, dispatcher_reported_failure | aucun | inconnue | aucun identifié |
| 20260907_193016_action_validation_failure | SNAP | ACTION | data-collector.zappi.io | — | dispatcher_reported_failure | aucun | inconnue | voir les deux suivants (même provider, 50s et 40s d'écart) |
| 20260907_193106_action_validation_failure | SNAP | ACTION | data-collector.zappi.io | — | dispatcher_reported_failure | aucun | inconnue | proche du précédent (50s) et du suivant (40s) — **non comparé au niveau DOM dans cette mission** |
| 20260907_193146_action_validation_failure | SNAP | ACTION | data-collector.zappi.io | — | dispatcher_reported_failure | aucun | inconnue | idem |
| 20260907_201456_action_validation_failure | SNAP | ACTION | s2.ifoponline.com | — | dispatcher_reported_failure | aucun | inconnue | aucun identifié |
| 20260907_212002_action_validation_failure | SNAP | ACTION | s2.ifoponline.com | text / single_25ab6372513b | dispatcher_false_negative | aucun | inconnue | même target_id que le suivant (24 min d'écart) |
| 20260907_213458_action_validation_failure | SNAP | ACTION | s2.ifoponline.com | text / single_25ab6372513b | dispatcher_false_negative | aucun | inconnue | même target_id que le précédent |
| 20260909_195344_action_validation_failure | SNAP | ACTION | community.focaldata.com | — | dispatcher_reported_failure | aucun | inconnue | aucun identifié |
| 20260911_160609_extraction_validation_failure | SNAP, FC, DIAG, REPL | EXTRACTION | community.focaldata.com | — | missing_block | `NON_REPRODUIT` (`plausible`) | inconnue | aucun identifié |
| 20260911_173509_action_validation_failure | SNAP | ACTION | community.focaldata.com | — | dispatcher_reported_failure | aucun | inconnue | même provider que le précédent et le suivant, jour proche |
| 20260911_175323_action_validation_failure | SNAP | ACTION | community.focaldata.com | radio / group_ec0a582b54ee | dispatcher_false_negative | aucun | inconnue | aucun identifié |
| 20260914_154721_extraction_validation_failure | SNAP, FC | EXTRACTION | enquetes.panelia.fr | — | missing_block | aucun | inconnue | aucun identifié |
| 20260922_103945_action_validation_failure | SNAP | ACTION | api-icontrol.datadiggers-mr.com | radio / group_21db20e368ce | dispatcher_false_negative | aucun | inconnue | aucun identifié |
| 20260922_104006_action_validation_failure | SNAP | ACTION | api-icontrol.datadiggers-mr.com | radio / group_cead5509389a | dispatcher_false_negative | aucun | inconnue | même target_id que les deux suivants |
| 20260922_105334_action_validation_failure | SNAP | ACTION | api-icontrol.datadiggers-mr.com | radio / group_cead5509389a | dispatcher_false_negative | aucun | inconnue | même target_id que le précédent et le suivant |
| 20260922_114226_action_validation_failure | SNAP | ACTION | api-icontrol.datadiggers-mr.com | radio / group_cead5509389a | dispatcher_false_negative | aucun | inconnue | même target_id que les deux précédents — probablement la même question rencontrée à trois reprises, **pas nécessairement le même incident** (non comparé au niveau DOM) |
| 20260923_080004_action_validation_failure | SNAP | ACTION | rx.samplicio.us | — | dispatcher_reported_failure | aucun | inconnue | proche du suivant (53 min, même provider) |
| 20260923_085331_action_validation_failure | SNAP | ACTION | rx.samplicio.us | — | dispatcher_reported_failure | aucun | inconnue | idem |
| 20260923_195439_extraction_validation_failure | SNAP, FC | EXTRACTION | mcresearch2.co1.qualtrics.com | checkbox / group_f65630091ad2 | choice_without_options | aucun à cet identifiant (voir `20260923_211155`) | inconnue | **confirmé par comparaison de contenu avec `20260923_211155`** — voir Mission B.2 |
| 20260923_201701_extraction_validation_failure | SNAP, FC | EXTRACTION | env3.surveysip.com | checkbox / group_88428f81430e | question_text_is_help ×3 | aucun | inconnue | **confirmé par comparaison de contenu avec `20260923_201807`** — voir Mission B.2 |
| 20260923_201807_extraction_validation_failure | SNAP | EXTRACTION | env3.surveysip.com | checkbox / group_88428f81430e | question_text_is_help ×3 | aucun | inconnue | **confirmé, voir Mission B.2** — doublon de `20260923_201701` (66 s d'écart) |
| 20260923_211155_extraction_validation_failure | SNAP, DIAG, REPL | EXTRACTION | mcresearch2.co1.qualtrics.com | checkbox / group_f65630091ad2 | choice_without_options | `NON_REPRODUIT` (`plausible`) | inconnue | **confirmé, voir Mission B.2** — doublon de `20260923_195439` (1 h 17 d'écart) ; absent de `failure_cases/` (voir Mission B.1) |
| 20260924_140423_action_validation_failure | SNAP | ACTION | surveys.lifepointspanel.com | radio / group_54b8143033eb | dispatcher_false_negative | aucun | inconnue | aucun identifié |
| 20260924_164929_action_validation_failure | SNAP | ACTION | surveys.lifepointspanel.com | radio / group_3438ae863d64 | dispatcher_false_negative | aucun | inconnue | même target_id que les deux suivants |
| 20260924_172357_action_validation_failure | SNAP | ACTION | surveys.lifepointspanel.com | radio / group_3438ae863d64 | dispatcher_false_negative | aucun | inconnue | même target_id que le précédent et le suivant |
| 20260924_180714_action_validation_failure | SNAP, FC-OK, (copié dans `Golden_DOMs/`) | ACTION | surveys.lifepointspanel.com | radio / group_3438ae863d64 | dispatcher_false_negative | aucun | inconnue | même target_id que les deux précédents — probablement la même question, non comparé au niveau DOM |
| 20260925_084717_action_validation_failure | SNAP | ACTION | survey.euro.confirmit.com | — | dispatcher_reported_failure | aucun | inconnue | proche du suivant (23 min, même provider) |
| 20260925_091047_action_validation_failure | SNAP | ACTION | survey.euro.confirmit.com | — | dispatcher_reported_failure | aucun | inconnue | idem |
| 20260925_115552_action_validation_failure | SNAP | ACTION | surveys.lifepointspanel.com | radio / group_70d2fbdc16e2 | dispatcher_false_negative | aucun | inconnue | même target_id que le suivant |
| 20260925_211251_action_validation_failure | SNAP, FC-OK | ACTION | surveys.lifepointspanel.com | — | dispatcher_reported_failure | aucun | inconnue | aucun identifié |
| 20260925_211543_action_validation_failure | SNAP, DIAG, REPL | ACTION | surveys.lifepointspanel.com | radio / group_70d2fbdc16e2 | dispatcher_false_negative | `REPRODUIT` (`plausible`) ; `real_dispatch_replay.status=SUCCESS`, `dispatcher_success=True` au replay | inconnue | même target_id que `20260925_115552` ; absent de `failure_cases/` (voir Mission B.1) |
| 20260929_033834_extraction_validation_failure | SNAP, FC, FC-OK, DIAG | EXTRACTION | websurvey5.metrixlab.com | — / group_a6447bcc2032 | missing_block | `REPRODUIT` (`certain`) | **plausible, non confirmée par l'identifiant exact dans `SURVEYBOT_AUTOFIX_PLAN.md`** (même provider/`failure_type` que « le premier correctif externe adopté ») — laissée **inconnue** | aucun identifié |
| 20261002_064948_action_validation_failure | SNAP, FC, DIAG | ACTION | websurvey5.metrixlab.com | — | dispatcher_reported_failure | `REPRODUIT` (`certain`) ; `real_dispatch_replay.status=FAILURE`, `dispatcher_success=False` au replay | **plausible par date/provider (« 2–3 octobre 2026, radioQT »), non confirmée par l'identifiant exact** — laissée **inconnue** | aucun identifié |
| 20261003_205317_action_validation_failure | SNAP, FC | ACTION | websurvey5.metrixlab.com | — | dispatcher_reported_failure | aucun | **confirmée par identifiant et horodatage exacts** (plan : « Troisième session... 3 octobre 2026, soir... Case neuf sur `base_sha` `1513531` ») — **bug réel, l'agent correcteur a conclu à tort que l'échec était le comportement attendu** | aucun identifié |
| 20261003_223928_action_validation_failure | SNAP | ACTION | websurvey5.metrixlab.com | — | dispatcher_reported_failure | aucun | inconnue — non cité par cet identifiant dans le plan, bien que même page/jour que le précédent (1 h 46 d'écart) | proche de `20261003_205317` (même provider, même jour) — **non comparé au niveau DOM dans cette mission** |
| 20261004_120106_action_validation_failure | SNAP, FC | ACTION | websurvey5.metrixlab.com | — | dispatcher_reported_failure | aucun | **confirmée par identifiant exact** (plan : « Case `120106`... rejeu `BUG_PERSISTANT`... score `REJECT` ») | voir `20261004_122132` (doublon historique déclaré par le pipeline lui-même, `historical_duplicate`, selon le plan) |
| 20261004_122132_action_validation_failure | SNAP, FC | ACTION | websurvey5.metrixlab.com | — | dispatcher_reported_failure | aucun | **confirmée par identifiant exact** (plan : « Case `122132` ensuite : arrêt `historical_duplicate` (équivalent `120106`, issue `REJECT`) ») | **doublon déclaré par le pipeline lui-même** de `20261004_120106` |
| 20261004_205100_action_validation_failure | SNAP, FC | ACTION | websurvey5.metrixlab.com | — | dispatcher_reported_failure | aucun | **confirmée par identifiant exact** (plan : « Case `205100`... rejeu `NON_CONCLUANT`... validation live `CORRECTIF_CONFIRME`... mergé ») | aucun identifié |

### Cas présents à un seul emplacement, et dossiers d'origine absents

- **24 cas présents uniquement dans `snapshots/`** (tous ceux sans FC/FC-OK/DIAG/REPL dans le tableau ci-dessus) — jamais promus, jamais diagnostiqués, jamais classés.
- **2 cas dont le dossier d'origine `failure_cases/` est absent alors qu'un diagnostic et une classification existent** (`20260923_211155_extraction_validation_failure`, `20260925_211543_action_validation_failure`) — réponse en Mission B.1.
- **Aucun cas n'est présent dans `failure_cases/` ou `replay_ok/` sans être aussi dans `snapshots/`** — confirmé, `snapshots/` est bien le sur-ensemble strict.

---

## Mission B — Points ouverts

### B.1 — Politique de rétention / déplacement / suppression

**Recherche exhaustive dans le code** (`grep` sur tout `Survey/` et `tools/` pour `failure_cases`/`diagnoses` combinés à `move|rmtree|rename|shutil|delete|unlink`) : **aucune occurrence**. Recherche séparée de la chaîne littérale `"replay_ok"` dans tout fichier `.py` du dépôt : **aucune occurrence**.

**Mécanisme confirmé par lecture des façades CLI :**
- `tools/create_failure_case.py` (docstring `:1-16`) : outil **manuel**, invoqué case par case (`python tools\create_failure_case.py <snapshot_dir>`), qui lit un dossier de `snapshots/` et écrit dans `failure_cases/<id>/` via `Survey/autofix/failure_case_builder.py`. Énoncé explicitement : « ne modifie ni ne déplace jamais les snapshots source ». C'est la preuve directe que **la promotion vers `failure_cases/` est un geste opérateur, un cas à la fois**, jamais un balayage automatique — ce qui explique pourquoi seulement 10 des 38 cas de `snapshots/` ont été promus (sélection manuelle, critère non trouvé dans le code).
- `tools/diagnose_failure.py` (docstring `:1-17`) : même principe, outil manuel, lit un dossier et écrit dans `diagnoses/<id>/diagnosis.json` via `Survey/autofix/failure_diagnosis.py`. Énoncé explicitement : « ne modifie jamais le case ni le snapshot source ».
- `tools/classify_replayability.py` : même principe, lit un `diagnoses/<id>/` et écrit dans `replayability/<id>/classification.json` via `Survey/autofix/replayability_classifier.py`.

**Conclusion sur l'absence de dossier d'origine pour 2 diagnostics** : l'exemple d'usage du docstring de `diagnose_failure.py` montre un chemin `failure_cases\case_<id>` en entrée, ce qui suggérerait qu'un passage par `failure_cases/` est un prérequis. Mais deux diagnostics existent (`20260923_211155`, `20260925_211543`) sans dossier `failure_cases/` correspondant. **Deux explications restent ouvertes, non tranchées dans cette mission** (le corps de `failure_diagnosis.py`/`diagnose_failure.py` n'a pas été relu assez profondément pour choisir entre elles) :
  (a) l'outil accepte en réalité n'importe quel dossier au bon format (y compris directement un dossier `snapshots/<id>/`), et l'opérateur l'a pointé directement sur `snapshots/` sans passer par `failure_cases/` pour ces deux cas ;
  (b) un dossier `failure_cases/<id>/` a existé puis a été supprimé manuellement après diagnostic (aucune trace de ceci dans le code, mais aucune trace ne serait attendue puisqu'une suppression manuelle par l'opérateur, hors du dépôt Git, n'est pas un événement que le code peut enregistrer).
  **NON VÉRIFIÉ** — à trancher en relisant le corps de `failure_diagnosis.py`/`write_diagnosis` pour savoir s'il exige un `manifest.json` (donc un passage obligé par `failure_cases/`) ou s'il sait fonctionner sur un `snapshots/<id>/` brut.

**Conclusion générale** : il n'existe, dans le code lu, **aucune politique automatique de rétention, de déplacement ou de suppression**. `snapshots/` (hors dépôt) est la source de vérité jamais altérée par aucun outil de ce dépôt ; tout ce qui vit dans `failure_cases/`, `failure_cases/replay_ok/`, `diagnoses/` et `replayability/` est une **copie ou un sous-produit créé à la main, case par case**, dont la sélection (quels cas, pourquoi ceux-là) ne repose sur aucun critère trouvé dans le code — très probablement une décision de l'opérateur à chaque fois, non automatisée.

### B.2 — Les deux cas du 23 septembre (extraction Qualtrics) : même cas ou deux cas distincts ?

**Comparaison de contenu effectuée** entre `20260923_195439_extraction_validation_failure` et `20260923_211155_extraction_validation_failure` (mêmes provider/`target_id`/`failure_type`, ~1 h 17 d'écart) :

- `meta.json["url"]` **strictement identique dans les deux cas**, y compris le jeton de session intégré à l'URL (`_s=41d08de82f1f5a2cd030ee8637a1487f68845428994e45160a0ace8f67b604b8`) et le `psid` (`uzfbzNzImuuQENidxgubRD8P`) — **même session de navigation/même répondant**, pas deux respondents distincts.
- `meta.json["dom_sig"]` quasi identique : `complete|66497|354|7` contre `complete|66551|354|7` — même `readyState`, même nombre de champs de saisie (354) et de blocs détectés (7), longueur du `outerHTML` différant de 54 caractères sur ~66 500.
- `dom_outer.html` : 67 953 octets contre 68 007 octets ; `diff` ne rapporte que **14 lignes de différence** sur l'ensemble du fichier.
- `validation_report.json` et `question_blocks.json` : tailles identiques à l'octet (324 et 323 octets respectivement) dans les deux dossiers.

**Conclusion B.2** : ce sont **deux événements de capture distincts** (deux dossiers `snapshots/` séparés, deux déclenchements de `record_validation_failure`, ~1 h 17 d'écart) — ce n'est pas un même dossier renommé. Mais le contenu capturé est **quasiment identique** (même session, même page, même structure DOM, écart de 14 lignes probablement dû à un élément dynamique mineur) : tout indique **la même page/le même incident rencontré deux fois par le bot au cours de la même session**, pas deux occurrences indépendantes du même bug sur deux répondants différents.

**Observation additionnelle non demandée explicitement mais du même type** : `20260923_201701_extraction_validation_failure` et `20260923_201807_extraction_validation_failure` (env3.surveysip.com, même `target_id`, 66 secondes d'écart) présentent le même schéma, encore plus marqué : URL strictement identique, `dom_sig` quasi identique (`complete|270538|2298|88` contre `complete|270541|2298|88`), et seulement **4 lignes de différence** sur un fichier `dom_outer.html` de 274 Ko. Même conclusion : deux captures distinctes de la même page/session, quasi simultanées.

### B.3 — Document d'architecture du cœur protégé

**Fichier ouvert :** `Utils/SURVEYBOT_PROTECTED_CORE_ARCHITECTURE.md` (29 septembre 2026, mis à jour jusqu'au 5 octobre 2026). C'est le document le plus proche, par le nom et le contenu, de ce qu'une mission précédente cherchait sous le nom `PROJECT_ARCHITECTURE.md` (inexistant tel quel dans le dépôt).

**Résumé de ses règles de séparation des responsabilités, sans interprétation** (citations/paraphrase directe du texte) :

- « Le bot de production détecte et enregistre les incidents et les compteurs utiles. Le diagnostic causal, les patches, les replays, les validations et la consolidation du core appartiennent au worker local/dev séparé. » (§1)
- Le comportement reste « DOM-first, sans fallback Vision » ; « une correction ne doit pas multiplier les extracteurs pour contourner une fonction existante. » (§1)
- Chaque correctif externe est « rattaché à une fonction core et à un ou plusieurs failure cases identifiés » et doit rester « assez étroit pour être retiré ou réécrit lors d'une évolution du core » (§2). Les correctifs sont évalués par « une couche d'orchestration unique » sur un « ensemble plat » : « un correctif ne peut ni appeler un autre correctif, ni supposer son résultat, ni imposer un ordre caché d'application. » (§2)
- Pour l'extraction, la correction intervient « avant la validation du résultat » ; pour l'action, « avant tout clic ou effet irréversible » — « un résultat déjà exécuté ne peut pas être "corrigé" après coup. » (§2)
- L'attribution d'une fonction à un incident distingue explicitement quatre niveaux de preuve non interchangeables : `involved`, `suspected`, `root_cause_confirmed`, `patched` (§3).
- Cycle de vie d'un correctif en cinq états (`NOUVEAU → OBSERVÉ → VALIDÉ → STABLE`, plus `EVOLUTION_CANDIDATE`), chaque transition exigeant des preuves précises ; « les quotas d'appels, de cases ou de fixes sont des seuils de revue... jamais des seuils de fusion automatique. » (§5)
- Pipeline en cinq étapes à responsabilités distinctes (§8) : (1) production — observabilité et failure case, « sans retry ni auto-correction passive » ; (2) replay/diagnostic/sélection de contexte — « les validators vérifient les incohérences fortes entre DOM, registre, blocs et actions, sans devenir un second analyseur DOM » ; (3) patch local isolé ; (4) validation (contrôles statiques, replays, intégrité du core, live attach si nécessaire) ; (5) supervision et adoption humaine — « la décision d'adoption précède toute actualisation de la baseline ; le commit et le merge relèvent du worker local/dev et de l'orchestrateur, jamais du bot de production. »
- Le registre d'intégrité (`extractor_integrity.json`) distingue quatre états de comparaison (`UNCHANGED`, `EXPECTED_CHANGE`, `UNEXPECTED_CHANGE`, `BASELINE_MISMATCH`) et précise que « l'agent qui écrit le patch ne met jamais à jour lui-même la baseline. » (§7)

Le document précise lui-même qu'il « fixe les responsabilités et les critères de décision » sans « prescrire... de schéma de stockage définitif » (§8, dernier paragraphe) — cohérent avec ce que cette mission observe : aucune des localisations physiques du corpus (`snapshots/`, `failure_cases/`, `diagnoses/`, `replayability/`) n'est elle-même définie dans ce document d'architecture.

---

## Mission C — Mesures sur les fichiers déjà produits (38 cas de `snapshots/`, sans exécution du bot)

| Artefact | n (cas où présent) | minimum | médiane | maximum |
|---|---|---|---|---|
| Capture d'écran (`viewport.png` / `post_action_viewport.png`) | 38 / 38 | 19 002 octets (`20260925_115552_action_validation_failure`) | 35 312 octets | 136 145 octets (`20260909_195344_action_validation_failure`) |
| MHTML (`page.mhtml`) | 8 / 38 | 185 875 octets (`20260907_142347_action_validation_failure`) | 404 988 octets | 545 984 octets (`20260914_154721_extraction_validation_failure`) |
| DOM HTML (`dom_outer.html` pour le profil extraction, `post_action_dom.html` pour le profil action) | 38 / 38 | 7 316 octets (`20260907_193016_action_validation_failure`, action) | 32 987 octets | 274 365 octets (`20260923_201807_extraction_validation_failure`, extraction) |
| `external_scripts.json` | 12 / 38 | 303 247 octets (`20260925_084717_action_validation_failure`) | 730 979 octets | 952 117 octets (`20260925_211543_action_validation_failure`) |
| `external_stylesheets.json` | 11 / 38 | 18 780 octets (`20260929_033834_extraction_validation_failure`) | 18 780 octets | 161 590 octets (`20260925_091047_action_validation_failure`) |
| `external_requests.json` | 9 / 38 | 4 523 octets (`20260925_211251_action_validation_failure`) | 139 429 octets | 139 429 octets (`20261004_205100_action_validation_failure`) |
| Frames capturées (`meta.json["frames_count"]`) | **0 / 38** | — | — | — |

**Lecture des résultats, constatée sans l'interpréter au-delà du fait mesuré :**
- **Aucun des 38 cas n'a de `frames_count` renseigné dans `meta.json`** — `_dump_frames_best_effort` n'a jamais retenu la moindre iframe sur ce corpus (seuil de rétention : `inputs_count > 0` ou `text_len >= 200`, `page_snapshot.py:214-215`). Ceci ne distingue pas, avec les seules données disponibles, « aucune iframe présente sur ces pages » de « des iframes présentes mais sous le seuil de rétention » — **NON VÉRIFIÉ** laquelle des deux explications est la bonne, cela nécessiterait de relire le DOM brut de chaque cas pour compter les `<iframe>` réels.
- **`page.mhtml` est présent sur un cas de stage `action`** (`20260907_142347_action_validation_failure`, le cas le plus ancien du corpus, 7 septembre 2026), alors que le comportement actuel du code (`page_snapshot.py:437,475`, garde `if not is_action_validation`) l'exclut explicitement pour ce profil. Cohérent avec une capture antérieure à l'introduction de cette distinction dans le code (**non vérifié par l'historique Git**, hors périmètre de cette mission).
- Les tailles de `external_scripts.json`/`external_stylesheets.json`/`external_requests.json` ne sont renseignées, par construction du code (`Survey/browser_capsule.py`), que lorsque la collecte a trouvé au moins une ressource à inclure — leur absence sur la majorité des 38 cas (respectivement 26, 27 et 29 cas sans cet artefact) ne signifie pas un échec de collecte, elle peut aussi signifier « aucune ressource de ce type trouvée sur la page » ; **NON VÉRIFIÉ** sans relire le log debug associé à chaque cas, ce qui n'a pas été fait ici.
- Le fichier `external_scripts.json` atteint jusqu'à 952 117 octets (~930 Kio) sur un seul cas, proche mais toujours sous le plafond de 4 Mio documenté dans le code (`_MAX_SCRIPTS_TOTAL_CHARS`) — aucun cas de ce corpus n'atteint ce plafond.

---

## Liste finale des points restés non vérifiés

- Mission A : si `20260923_195439`/`20260923_211155` (et, observation additionnelle, `20260923_201701`/`20260923_201807`) correspondent à **deux appels distincts de `record_validation_failure` sur la même session de navigateur** (ex. deux déclenchements de l'observateur sur la même page, sans rechargement) ou à **deux runs du bot distincts qui ont, par coïncidence de session persistante côté provider, réutilisé la même URL/jeton** — la comparaison de contenu confirme la quasi-identité, mais ne permet pas de trancher ce point précis sans les logs d'exécution du bot.
- Mission A : les clusters de même `target_id` répétés sur plusieurs horodatages (ex. `group_cead5509389a` ×3, `group_3438ae863d64` ×3, `group_70d2fbdc16e2` ×2) n'ont été rapprochés que par égalité de `target_id`/provider, **jamais comparés au niveau du contenu DOM** comme cela a été fait pour la paire explicitement demandée par la mission — possible qu'ils soient, eux aussi, des captures quasi simultanées de la même session, ou au contraire des occurrences réellement indépendantes du même bug sur des sessions différentes.
- Mission B.1 : pourquoi exactement 2 diagnostics (`20260923_211155`, `20260925_211543`) existent sans dossier `failure_cases/` correspondant — hypothèse (a) passage direct de l'outil sur `snapshots/` vs hypothèse (b) suppression manuelle a posteriori d'un dossier `failure_cases/` existant — non tranché, nécessiterait de relire le corps de `Survey/autofix/failure_diagnosis.py`/`write_diagnosis` pour savoir s'il exige strictement un `manifest.json` en entrée.
- Mission B.1 : critère de sélection manuelle des 10 cas promus en `failure_cases/` sur les 38 disponibles dans `snapshots/` — aucun critère trouvé dans le code ; vraisemblablement une décision ad hoc de l'opérateur à chaque cas, non documentée dans le dépôt.
- Mission C : absence de `frames_count` sur 38/38 cas — « aucune iframe réelle » vs « iframes sous le seuil de rétention » non distingué.
- Mission C : présence/absence des artefacts `external_*.json` non corrélée aux logs debug de collecte (budget atteint, page sans ressource de ce type, erreur CDP) — seule la taille des fichiers présents a été mesurée, pas la raison de l'absence des autres.
- Mission C : la présence de `page.mhtml` sur un cas `action` (`20260907_142347`) n'a pas été recoupée avec l'historique Git de `page_snapshot.py` pour confirmer l'hypothèse d'une capture antérieure à la distinction de profil.
- Report des missions précédentes, toujours non résolu ici (hors périmètre de cette mission, non ré-instruit) : statut exact de `question_block_analyzer.py` au-delà de l'absence d'appelants ; calcul exact de `target_shapes`/`requested_option_dom_facts` dans `Survey/autofix/replay_browser.py` (non ouvert) ; fréquence réelle d'appel d'`execute_actions_plan` par parcours d'enquête ; latences réseau/CDP réelles en production.

---

**Titre de commit proposé :**
```
docs: Index unifie du corpus de failure cases pour la Bug Detection Layer (BDL-0)
```
