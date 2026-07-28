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

                    # Scorer le contexte
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
            # Scorer le contexte principal
            with switch_to_frame_chain(driver, []):
                best_context = _score_dom_context(driver)
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
