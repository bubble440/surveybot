# Survey/dom_frame_selector.py
"""
DOM Frame Selector - Gestion des frames et sélection du meilleur contexte DOM.

Ce module contient les fonctions pour :
- Attente de chargement DOM (_wait_for_survey_dom)
- Scoring de contexte DOM (_score_dom_context)
- Sélection de la meilleure chaîne de frames (_select_best_frame_chain)
"""

from __future__ import annotations
from typing import Dict, Any, Tuple, List
import time

# Import des utilitaires
try:
    from Survey.dom_utils import _env_truthy
    from Survey.frame_utils import iter_frame_chains, switch_to_frame_chain, _frame_elements
except ImportError:
    # Fallback pour tests locaux
    from Survey.dom_utils import _env_truthy
    import sys
    # Pour iter_frame_chains et switch_to_frame_chain, on devra les avoir disponibles

# ================================================================================
# ATTENTE CHARGEMENT DOM
# ================================================================================

def _wait_for_survey_dom(driver, timeout_s: float = 1.2, step_s: float = 0.2) -> bool:
    """
    Attend que le DOM soit stable (pas de mutation pendant step_s).
    
    Stratégie:
    - Surveille les mutations DOM via MutationObserver
    - Retourne True si stable pendant step_s
    - Retourne False si timeout atteint
    
    Args:
        driver: WebDriver Selenium
        timeout_s: Timeout total (défaut 1.2s)
        step_s: Durée de stabilité requise (défaut 0.2s)
    
    Returns:
        True si DOM stable, False sinon
    """
    try:
        # Playwright natif : évaluer dans le frame courant (getattr sur driver
        # shim, sinon driver lui-même = Page = frame racine). driver.execute_script
        # n'existe pas sur Page/Frame Playwright (API Selenium) — cf. dom_classifier.py
        # pour la même convention.
        current_frame = getattr(driver, "_current_frame", driver)

        start = time.time()

        # Installer un MutationObserver pour détecter les changements
        script_install = """
        () => {
            if (!window.__domStableObserver) {
                window.__domStableLastMutation = Date.now();
                window.__domStableObserver = new MutationObserver(() => {
                    window.__domStableLastMutation = Date.now();
                });
                window.__domStableObserver.observe(document.body, {
                    childList: true,
                    subtree: true,
                    attributes: true
                });
            }
        }
        """
        current_frame.evaluate(script_install)

        # Attendre la stabilité
        while time.time() - start < timeout_s:
            # Vérifier si le DOM est stable depuis step_s
            elapsed_since_mutation = current_frame.evaluate("""
                () => (Date.now() - window.__domStableLastMutation) / 1000
            """)

            if elapsed_since_mutation >= step_s:
                # DOM stable
                return True
            
            # Attendre un peu avant de revérifier
            time.sleep(0.05)
        
        # Timeout atteint
        return False

    except Exception:
        # En cas d'erreur, considérer que le DOM est prêt
        return True


def _wait_for_mriweb_ready(driver, timeout_s: float = 1.5, poll_s: float = 0.1) -> None:
    """
    Garde-fou additif, strictement scopé au template Ipsos/mrIWeb (Sharky) :
    `document.body` porte une classe `everythingReady`, ajoutée par le template
    une fois la transition de changement de question terminée (cf. body_text.txt
    de référence : `class="progress-bar-minimal prevent-select no-touch
    direction-ltr everythingReady"`).

    _wait_for_survey_dom() (non modifiée) ne détecte que l'absence de mutation
    DOM pendant step_s — un fondu CSS pur (opacity/transform) sans mutation
    d'attribut ni de structure après la fin du chargement peut donc le déclarer
    "stable" avant la fin réelle de la transition visuelle du template, ce qui a
    été corrélé à un rendu dédoublé/fantôme à l'écran au moment précis de
    l'extraction sur une page Ipsos/mrIWeb (SavePoint=NEWPARENTQUESTION).

    Garde-fou DOM strict : n'attend QUE si `form[name="mrForm"]` est présent
    (signature mrIWeb déjà référencée dans BOT_EVOLUTION_MEMORY.md). Sur toute
    autre plateforme, retour immédiat sans attente ni effet de bord. Budget
    borné avec abandon contrôlé et log (DOM_CONTEXT_DEBUG) si la classe
    n'apparaît jamais dans le budget imparti (comportement alors identique à
    avant ce patch : pas de blocage).
    """
    try:
        current_frame = getattr(driver, "_current_frame", driver)
        is_mriweb = bool(
            current_frame.evaluate("() => !!document.querySelector('form[name=\"mrForm\"]')")
        )
        if not is_mriweb:
            return

        deadline = time.time() + max(0.0, timeout_s)
        while time.time() < deadline:
            ready = bool(
                current_frame.evaluate(
                    "() => !!(document.body && document.body.classList.contains('everythingReady'))"
                )
            )
            if ready:
                return
            time.sleep(poll_s)

        if _env_truthy("DOM_CONTEXT_DEBUG", "0"):
            print(f"[DOM_CONTEXT_DEBUG] mriweb_ready_timeout timeout_s={timeout_s}")
    except Exception:
        return


def _wait_for_ssi_ciwweb_ready(driver, timeout_s: float = 1.5, poll_s: float = 0.1) -> None:
    """
    Garde-fou additif, strictement scopé aux pages SSI/Confirmit legacy
    (ciwweb.pl, ex. eu.surveyme.online) : `document.body` porte une classe
    `element_hidden` (avec `aria-busy="true"`) pendant la phase de
    chargement/rendu, retirée par le template une fois la page prête (cf.
    snapshot de référence : `<body aria-busy="true" class="element_hidden">`).

    Sans cette attente, une extraction déclenchée pendant cette fenêtre peut
    lire un DOM dont le rendu visuel n'est pas encore stabilisé — même
    classe de symptôme que le fondu CSS mrIWeb/Sharky déjà couvert par
    _wait_for_mriweb_ready (non modifiée par cette fonction, purement
    additive et parallèle).

    Garde-fou DOM strict : n'attend QUE si `form#ssi-form-submit` ou
    `form[action*="ciwweb.pl"]` est présent (signature SSI legacy). Sur
    toute autre plateforme, retour immédiat sans attente ni effet de bord.
    Budget borné avec abandon contrôlé et log (DOM_CONTEXT_DEBUG) si la
    classe reste présente au-delà du budget imparti (comportement alors
    identique à avant ce patch : pas de blocage).
    """
    try:
        current_frame = getattr(driver, "_current_frame", driver)
        is_ssi_ciwweb = bool(
            current_frame.evaluate(
                "() => !!(document.querySelector('form#ssi-form-submit') "
                "|| document.querySelector('form[action*=\"ciwweb.pl\"]'))"
            )
        )
        if not is_ssi_ciwweb:
            return

        deadline = time.time() + max(0.0, timeout_s)
        while time.time() < deadline:
            ready = bool(
                current_frame.evaluate(
                    "() => !!(document.body && !document.body.classList.contains('element_hidden'))"
                )
            )
            if ready:
                return
            time.sleep(poll_s)

        if _env_truthy("DOM_CONTEXT_DEBUG", "0"):
            print(f"[DOM_CONTEXT_DEBUG] ssi_ciwweb_ready_timeout timeout_s={timeout_s}")
    except Exception:
        return


def _wait_for_askandanswer_layout_ready(driver, timeout_s: float = 1.5, poll_s: float = 0.1) -> None:
    """
    Garde-fou additif, strictement scopé aux pages Ask&Answer/TopSurveys (Angular
    Material : `app-survey-page` + `div[id^="appQuestionContainer-"]`, cf.
    _extract_askandanswer_selection_list_questions dans dom_extractors_misc.py) :
    attend que la géométrie (position/hauteur) des conteneurs de question soit stable
    entre deux lectures consécutives avant de laisser l'extraction se déclencher.

    Sur ce template, les questions text/textarea sans conteneur sémantique dédié (pas
    de classe 'question'/'form-group', pas de <label>, pas de <fieldset>/<section>)
    sont résolues par proximité géométrique (_find_question_text_near_element,
    dom_question_extractor.py, non modifiée ici). _wait_for_survey_dom() (non modifiée)
    ne détecte que l'absence de mutation DOM pendant step_s -- même classe de biais que
    celle déjà documentée pour _wait_for_mriweb_ready : le rendu Angular Material
    (mat-card, `ng-trigger-animate`) peut continuer à mettre à jour des attributs
    `style` (opacity/transform) après la fin de l'insertion structurelle des noeuds,
    avec un budget de stabilité mutation-based (1.2s) parfois insuffisant. Confirmé sur
    DOM de référence : un scan précédent immédiatement avant l'extraction rapportait 0
    input détecté, suivi d'une extraction sur une géométrie encore en mouvement (texte
    de question pollué -- bannière + questions concaténées + compteur de progression --
    pour un seul des 4 champs texte de la page, celui dont la carte n'avait pas fini de
    se positionner à l'instant précis de la lecture DOM).

    Garde-fou DOM strict : n'attend QUE si `app-survey-page` ET au moins un
    `div[id^="appQuestionContainer-"]` sont présents. Sur toute autre plateforme,
    retour immédiat sans attente ni effet de bord. Budget borné (poll_s, timeout_s)
    avec abandon contrôlé et log (DOM_CONTEXT_DEBUG) si la géométrie ne se stabilise
    jamais dans le budget imparti (comportement alors identique à avant ce patch : pas
    de blocage).
    """
    try:
        current_frame = getattr(driver, "_current_frame", driver)
        is_askandanswer = bool(
            current_frame.evaluate(
                "() => !!(document.querySelector('app-survey-page') "
                "&& document.querySelector('div[id^=\"appQuestionContainer-\"]'))"
            )
        )
        if not is_askandanswer:
            return

        snapshot_script = """
        () => {
            const nodes = Array.from(document.querySelectorAll('div[id^="appQuestionContainer-"]'));
            return nodes.map(n => {
                const r = n.getBoundingClientRect();
                return n.id + ':' + Math.round(r.top) + ':' + Math.round(r.height);
            }).join('|');
        }
        """

        deadline = time.time() + max(0.0, timeout_s)
        last_sig = None
        while time.time() < deadline:
            sig = current_frame.evaluate(snapshot_script) or ""
            if sig and sig == last_sig:
                return
            last_sig = sig
            time.sleep(poll_s)

        if _env_truthy("DOM_CONTEXT_DEBUG", "0"):
            print(f"[DOM_CONTEXT_DEBUG] askandanswer_layout_ready_timeout timeout_s={timeout_s}")
    except Exception:
        return


# ================================================================================
# SCORING CONTEXTE DOM
# ================================================================================

def _score_dom_context(driver) -> Dict[str, Any]:
    """
    Score le contexte DOM actuel pour déterminer s'il contient une survey.
    
    Critères de scoring:
    - Présence d'inputs/selects/textareas
    - Présence de boutons submit/next
    - Présence de labels/questions
    - Ratio texte/inputs
    
    Returns:
        Dict avec:
        - 'score': int (0-100)
        - 'input_count': nombre d'inputs
        - 'select_count': nombre de selects
        - 'textarea_count': nombre de textareas
        - 'button_count': nombre de boutons
        - 'has_submit': bool
        - 'has_next': bool
        - 'text_length': longueur du texte visible
    """
    try:
        # Playwright natif : évaluer dans le frame courant, même convention que
        # _wait_for_survey_dom / dom_classifier.py. driver.execute_script n'existe
        # pas sur Page/Frame Playwright (API Selenium).
        current_frame = getattr(driver, "_current_frame", driver)
        result = current_frame.evaluate("""
            () => {
            const inputs = document.querySelectorAll('input:not([type=hidden])');
            const selects = document.querySelectorAll('select');
            const textareas = document.querySelectorAll('textarea');
            const buttons = document.querySelectorAll('button, input[type=submit], input[type=button]');
            const psRoot = document.querySelectorAll('ps-root').length;
            const psDateQuestion = document.querySelectorAll('ps-date-question').length;
            const psQuestionOrchestrator = document.querySelectorAll('ps-question-orchestrator').length;
            const psSelectScroll = document.querySelectorAll('ps-select-scroll').length;
            const questionTitle = document.querySelectorAll('.question-title').length;
            const selectScrollSlide = document.querySelectorAll('.select-scroll-slide').length;
            const runtimeAnswerRows = document.querySelectorAll(".answer[data-aut='Runtime_AnswerRow']").length;
            const runtimeRadioWrappers = document.querySelectorAll(".radio_button[data-aut='Runtime_Wrapper']").length;
            
            // Détecter submit/next
            let hasSubmit = false;
            let hasNext = false;
            buttons.forEach(btn => {
                const text = (btn.textContent || btn.value || '').toLowerCase();
                if (text.includes('submit') || text.includes('send')) hasSubmit = true;
                if (text.includes('next') || text.includes('continue')) hasNext = true;
            });
            
            // Texte visible
            const textLength = (document.body.innerText || '').length;
            
            return {
                input_count: inputs.length,
                select_count: selects.length,
                textarea_count: textareas.length,
                button_count: buttons.length,
                has_submit: hasSubmit,
                has_next: hasNext,
                text_length: textLength,
                ps_root_count: psRoot,
                ps_date_question_count: psDateQuestion,
                ps_question_orchestrator_count: psQuestionOrchestrator,
                ps_select_scroll_count: psSelectScroll,
                question_title_count: questionTitle,
                select_scroll_slide_count: selectScrollSlide,
                runtime_answer_rows_count: runtimeAnswerRows,
                runtime_radio_wrappers_count: runtimeRadioWrappers,
            };
            }
        """)
        
        # Calculer le score
        score = 0
        
        # Inputs (max 40 points)
        total_inputs = result['input_count'] + result['select_count'] + result['textarea_count']
        if total_inputs > 0:
            score += min(40, total_inputs * 5)
        
        # Boutons (max 20 points)
        if result['has_submit'] or result['has_next']:
            score += 20
        elif result['button_count'] > 0:
            score += 10
        
        # Texte (max 20 points)
        if result['text_length'] > 100:
            score += 10
        if result['text_length'] > 500:
            score += 10
        
        # Ratio inputs/texte (max 20 points)
        if total_inputs > 0 and result['text_length'] > 0:
            ratio = total_inputs / (result['text_length'] / 100)
            if ratio > 0.1:  # Au moins 1 input pour 100 caractères
                score += 20
            elif ratio > 0.05:
                score += 10

        # PureSpectrum custom UI (Angular, sans inputs natifs)
        pure_custom_score = 0
        if result.get('ps_root_count', 0) > 0:
            pure_custom_score += 10
        if result.get('ps_question_orchestrator_count', 0) > 0:
            pure_custom_score += 10
        if result.get('ps_date_question_count', 0) > 0:
            pure_custom_score += 25
        if result.get('ps_select_scroll_count', 0) > 0:
            pure_custom_score += 20
        if result.get('question_title_count', 0) > 0:
            pure_custom_score += 8
        if result.get('select_scroll_slide_count', 0) > 0:
            pure_custom_score += 12

        if pure_custom_score:
            score += min(55, pure_custom_score)

        # Toluna Runtime custom radio wrappers (sans input radio natif exploitable)
        runtime_custom_score = 0
        runtime_rows = int(result.get('runtime_answer_rows_count', 0) or 0)
        runtime_wrappers = int(result.get('runtime_radio_wrappers_count', 0) or 0)
        if runtime_rows >= 2:
            runtime_custom_score += 25
        if runtime_wrappers >= 2:
            runtime_custom_score += 20
        if runtime_custom_score:
            score += min(45, runtime_custom_score)

        result['pure_custom_score'] = min(55, pure_custom_score)
        result['runtime_custom_score'] = min(45, runtime_custom_score)
        
        result['score'] = min(100, score)
        return result
    
    except Exception:
        return {
            'score': 0,
            'input_count': 0,
            'select_count': 0,
            'textarea_count': 0,
            'button_count': 0,
            'has_submit': False,
            'has_next': False,
            'text_length': 0
        }


def _score_dom_context_ready(driver, timeout_s: float = 1.5, poll_s: float = 0.2) -> Dict[str, Any]:
    """
    Variante additive de _score_dom_context() (non modifiée) : sur une attache CDP
    fraîche, l'evaluate() interne de _score_dom_context peut échouer transitoirement
    (même classe de fragilité que _wait_for_frames_attached, cf. son docstring), ce
    qui est silencieusement absorbé par son except Exception et retourne un contexte
    à zéro strict sur tous les champs simultanément (score, input_count, select_count,
    textarea_count, button_count, text_length) — signature confirmée sur une page
    Ipsos/mrIWeb (MA checkboxes natifs) où le DOM contenait pourtant des inputs
    exploitables au même instant.

    Ne fait AUCUNE hypothèse sur le contenu réel de la page : une frame légitimement
    vide (ex: sous-iframe sans contenu) retourne aussi ce pattern de zéros et sera
    retentée jusqu'au budget avant d'être acceptée telle quelle (abandon contrôlé,
    comportement final identique à un appel direct de _score_dom_context en cas de
    frame réellement vide).

    Appelée uniquement au(x) point(s) d'appel scopés au chain racine [] dans
    _select_best_frame_chain, où le bug a été confirmé (pas de changement sur les
    autres chaînes ni sur _score_dom_context elle-même).
    """
    debug_ctx = _env_truthy("DOM_CONTEXT_DEBUG", "0")
    deadline = time.time() + max(0.0, timeout_s)
    attempt = 0
    context = _score_dom_context(driver)
    while True:
        attempt += 1
        is_hard_zero = (
            context.get('score', 0) == 0
            and context.get('input_count', 0) == 0
            and context.get('select_count', 0) == 0
            and context.get('textarea_count', 0) == 0
            and context.get('button_count', 0) == 0
            and context.get('text_length', 0) == 0
        )
        if not is_hard_zero or time.time() >= deadline:
            if is_hard_zero and debug_ctx and attempt > 1:
                print(f"[DOM_CONTEXT_DEBUG] score_dom_context_ready hard_zero_persists attempts={attempt}")
            return context
        if debug_ctx:
            print(f"[DOM_CONTEXT_DEBUG] score_dom_context_ready hard_zero_retry attempt={attempt}")
        time.sleep(poll_s)
        context = _score_dom_context(driver)


def _wait_for_frames_attached(driver, timeout_s: float = 2.0, poll_s: float = 0.1) -> None:
    """
    Attend, avec budget borné, que les frames enfants déclarées dans le DOM
    racine (balises <frame>/<iframe>) soient effectivement synchronisées côté
    Playwright (page.main_frame.child_frames), avant toute recherche de chaîne.

    Sur une attache CDP fraîche à une page déjà chargée (main.py::run_attach_takeover),
    document.querySelectorAll('frame, iframe') peut déjà voir les balises alors que
    Playwright n'a pas fini de synchroniser son arbre de frames : _frame_elements()
    renvoie alors [] côté racine, ce qui fait échouer silencieusement le ciblage
    explicite frame#mainFrame ci-dessous ET la boucle de scoring générique
    (aucune chaîne enfant à parcourir) -> selected_chain=[] score=0 malgré un
    frameset réel avec question exploitable dans frame#mainFrame.

    Abandon silencieux au timeout : comportement identique à avant ce patch si
    les frames ne s'attachent jamais (page sans frameset, ou frames qui ne se
    synchronisent jamais côté Playwright).

    Important : juste après un `connect_over_cdp` frais, le tout premier appel
    `evaluate()` sur la page peut lui-même échouer transitoirement (contexte
    d'exécution pas encore prêt côté CDP) — une unique tentative ratée ne doit
    PAS être interprétée comme "pas de frameset" (declared=0 => abandon immédiat
    sans attente ni log, symptôme observé). Le nombre de balises déclarées est
    donc retenté à chaque itération tant qu'il n'a pas pu être établi, au même
    rythme que la vérification des frames attachées, jusqu'au budget commun.
    """
    try:
        with switch_to_frame_chain(driver, []) as ok_root:
            if not ok_root:
                return
            current_frame = getattr(driver, "_current_frame", driver)
            declared = None  # None = pas encore établi (transitoire), 0 = confirmé absent

            deadline = time.time() + max(0.0, timeout_s)
            while time.time() < deadline:
                if declared is None:
                    try:
                        declared = int(
                            current_frame.evaluate("() => document.querySelectorAll('frame, iframe').length") or 0
                        )
                    except Exception:
                        declared = None  # toujours indéterminé : on retente au prochain tour

                if declared == 0:
                    return  # confirmé : pas de frameset, aucune attente nécessaire

                if declared and len(_frame_elements(driver)) > 0:
                    return  # frames synchronisées côté Playwright

                time.sleep(poll_s)

            if _env_truthy("DOM_CONTEXT_DEBUG", "0"):
                print(
                    f"[DOM_CONTEXT_DEBUG] frames_wait_timeout declared={declared} "
                    f"attached=0 timeout_s={timeout_s}"
                )
    except Exception:
        return


def _resolve_named_frame_index_with_retry(
    driver, frame_name: str, timeout_s: float = 1.5, poll_s: float = 0.1
):
    """
    Résout l'indice, parmi les enfants du contexte racine, d'une frame portant
    le nom `frame_name`, avec re-tentatives bornées.

    Garde-fou additif : n'intervient qu'en repli, uniquement quand la
    résolution en un seul passage déjà en place dans `_select_best_frame_chain`
    n'a trouvé aucune frame de ce nom. Cas visé : juste après une navigation
    interne d'une frame nommée du frameset (le document racine ne navigue pas,
    seule la frame enfant se recharge) — sur ce type de transition, certains
    moteurs détachent puis ré-attachent un nouvel objet Frame côté Playwright
    pour cette frame, ce qui peut la rendre transitoirement absente (ou son nom
    transitoirement non résolu) de `_frame_elements()` au moment précis d'un
    unique passage de recherche, sans qu'aucun mécanisme de nouvelle tentative
    existant ne couvre spécifiquement cette étape de résolution par nom
    (`_wait_for_frames_attached` ne vérifie que la présence d'AU MOINS une
    frame enfant, déjà trivialement vraie si une autre frame sœur reste
    attachée pendant la transition de celle-ci).

    Abandon contrôlé au budget imparti (retourne None, log DOM_CONTEXT_DEBUG) :
    comportement alors identique à avant ce patch (repli sur le scoring
    générique `iter_frame_chains`). Ne modifie ni ne remplace la boucle de
    résolution en un seul passage existante — fonction nommée distincte,
    appelée en complément.
    """
    target_name = (frame_name or "").strip().lower()
    if not target_name:
        return None
    deadline = time.time() + max(0.0, timeout_s)
    while time.time() < deadline:
        try:
            with switch_to_frame_chain(driver, []) as ok_root:
                if ok_root:
                    for _i, _child in enumerate(_frame_elements(driver)):
                        try:
                            _fname = (_child.name or "").strip().lower()
                        except Exception:
                            _fname = ""
                        if _fname == target_name:
                            return _i
        except Exception:
            pass
        time.sleep(poll_s)

    if _env_truthy("DOM_CONTEXT_DEBUG", "0"):
        print(
            f"[DOM_CONTEXT_DEBUG] named_frame_retry_timeout name={frame_name!r} timeout_s={timeout_s}"
        )
    return None


# ================================================================================
# SÉLECTION MEILLEURE FRAME
# ================================================================================

def _select_best_frame_chain(driver, max_depth: int = 2) -> Tuple[List[int], Dict[str, Any]]:
    """
    Sélectionne la meilleure chaîne de frames contenant une survey.
    
    Stratégie:
    - Parcourt toutes les chaînes de frames jusqu'à max_depth
    - Score chaque contexte avec _score_dom_context()
    - Retourne la chaîne avec le meilleur score
    
    Args:
        driver: WebDriver Selenium
        max_depth: Profondeur maximale de frames à explorer
    
    Returns:
        Tuple (frame_chain, context_info):
        - frame_chain: Liste d'indices de frames [0, 1, ...] ou [] pour main
        - context_info: Dict avec score et métadonnées
    """
    try:
        debug_ctx = _env_truthy("DOM_CONTEXT_DEBUG", "0")
        best_chain = []
        best_score = 0
        best_context = None

        # Budget borné : attend la synchronisation Playwright des frames déclarées
        # dans le DOM racine avant tout ciblage/scoring (cf. _wait_for_frames_attached).
        _wait_for_frames_attached(driver)

        # Ciblage DOM explicite: frameset avec frame#mainFrame
        # (cas observé: top-level quasi vide et contenu survey dans ce frame).
        # Recherche effectuée directement dans la collection Playwright
        # child_frames (celle utilisée par switch_to_frame_chain pour résoudre
        # un indice de chaîne), et non plus via un indice calculé séparément
        # depuis document.querySelectorAll('iframe, frame') au niveau racine :
        # sur une frameset à plusieurs frames sœurs avec attache tardive (CDP
        # connecté à une page déjà chargée plutôt que navigation initiée par le
        # bot), l'ordre d'enregistrement des frames côté driver peut différer
        # de l'ordre DOM — réutiliser un indice calculé dans une collection pour
        # indexer l'autre pouvait alors désigner la mauvaise frame (ex: la
        # leftFrame vide au lieu de la mainFrame contenant la question).
        main_frame_idx = None
        try:
            # Playwright natif : reset au contexte racine via switch_to_frame_chain(driver, [])
            # (driver.switch_to n'existe pas sur Page/Frame Playwright — API Selenium).
            with switch_to_frame_chain(driver, []) as ok_root:
                if ok_root:
                    for _i, _child in enumerate(_frame_elements(driver)):
                        try:
                            # Frame.name (propriété Playwright native, calculée à la
                            # création de la frame via CDP, sans évaluation JS) :
                            # retourne l'attribut name, ou l'attribut id si name est
                            # vide — même sémantique id-ou-name que l'ancien check,
                            # sans dépendre d'un accès cross-frame à window.frameElement.
                            _fname = (_child.name or "").strip().lower()
                        except Exception:
                            _fname = ""
                        if _fname == "mainframe":
                            main_frame_idx = _i
                            break
        except Exception:
            main_frame_idx = None

        if main_frame_idx is None:
            # Repli additif : le passage unique ci-dessus n'a trouvé aucune
            # frame nommée "mainFrame" — nouvelle tentative bornée avant de
            # tomber dans le scoring générique (cf. _resolve_named_frame_index_with_retry
            # pour le mécanisme visé : transition de frame nommée en cours de
            # resynchronisation côté Playwright).
            main_frame_idx = _resolve_named_frame_index_with_retry(driver, "mainFrame")
            if debug_ctx and isinstance(main_frame_idx, int):
                print(
                    f"[DOM_CONTEXT_DEBUG] mainframe_resolved_after_retry idx={main_frame_idx}"
                )

        if isinstance(main_frame_idx, int) and main_frame_idx >= 0:
            forced_chain = [main_frame_idx]
            with switch_to_frame_chain(driver, forced_chain) as ok:
                if ok:
                    _wait_for_survey_dom(driver, timeout_s=0.5, step_s=0.1)
                    forced_context = _score_dom_context(driver)
                    forced_context["selected_chain"] = forced_chain.copy()
                    forced_context["selected_by"] = "frame#mainFrame"
                    if debug_ctx:
                        print(
                            f"[DOM_CONTEXT_DEBUG] forced_chain={forced_chain} selected_by=frame#mainFrame "
                            f"score={forced_context.get('score', 0)} inputs={forced_context.get('input_count', 0)}"
                        )
                    return forced_chain, forced_context
        
        # Parcourir toutes les chaînes de frames
        for chain in iter_frame_chains(driver, max_depth=max_depth):
            try:
                # Switcher vers cette chaîne
                with switch_to_frame_chain(driver, chain) as ok:
                    if not ok:
                        continue

                    # Attendre stabilité
                    _wait_for_survey_dom(driver, timeout_s=0.5, step_s=0.1)

                    # Scorer le contexte (variante avec retry borné sur zéro strict
                    # pour le contexte racine, cf. _score_dom_context_ready ; les
                    # sous-frames gardent le scoring direct, non concernées par le bug)
                    if chain == []:
                        context = _score_dom_context_ready(driver)
                    else:
                        context = _score_dom_context(driver)
                if debug_ctx:
                    print(
                        f"[DOM_CONTEXT_DEBUG] candidate_chain={chain} score={context.get('score', 0)} "
                        f"inputs={context.get('input_count', 0)} selects={context.get('select_count', 0)} "
                        f"ps_date_question={context.get('ps_date_question_count', 0)} "
                        f"ps_select_scroll={context.get('ps_select_scroll_count', 0)} "
                        f"runtime_rows={context.get('runtime_answer_rows_count', 0)} "
                        f"runtime_wrappers={context.get('runtime_radio_wrappers_count', 0)}"
                    )
                
                # Vérifier si c'est le meilleur
                if context['score'] > best_score:
                    best_score = context['score']
                    best_chain = chain.copy()
                    best_context = context
                
            except Exception:
                # Frame inaccessible ou erreur, continuer
                pass
            # Pas de reset explicite ici : switch_to_frame_chain(driver, chain) revient déjà
            # au contexte racine dans son propre `finally` en sortie du `with` ci-dessus
            # (driver.switch_to n'existe pas sur Page/Frame Playwright — API Selenium).

        # Si aucun bon contexte trouvé, rester sur main
        if best_context is None:
            # Scorer le contexte principal (variante avec retry borné, cf. ci-dessus)
            with switch_to_frame_chain(driver, []):
                best_context = _score_dom_context_ready(driver)
            best_chain = []

        try:
            best_context['selected_chain'] = list(best_chain)
            with switch_to_frame_chain(driver, best_chain) as ok_sel:
                current_frame = getattr(driver, "_current_frame", driver) if ok_sel else driver
                best_context['selected_ps_date_question_count'] = int(
                    current_frame.evaluate("() => document.querySelectorAll('ps-date-question').length") or 0
                )
        except Exception:
            best_context['selected_ps_date_question_count'] = best_context.get('ps_date_question_count', 0)

        if debug_ctx:
            print(
                f"[DOM_CONTEXT_DEBUG] selected_chain={best_chain} score={best_context.get('score', 0)} "
                f"selected_ps_date_question={best_context.get('selected_ps_date_question_count', 0)} "
                f"runtime_rows={best_context.get('runtime_answer_rows_count', 0)} "
                f"runtime_wrappers={best_context.get('runtime_radio_wrappers_count', 0)}"
            )
        
        return best_chain, best_context
    
    except Exception:
        # En cas d'erreur, retourner contexte principal
        try:
            with switch_to_frame_chain(driver, []):
                pass
        except Exception:
            pass

        return [], {
            'score': 0,
            'input_count': 0,
            'select_count': 0,
            'textarea_count': 0,
            'button_count': 0,
            'has_submit': False,
            'has_next': False,
            'text_length': 0,
            'selected_ps_date_question_count': 0
        }
