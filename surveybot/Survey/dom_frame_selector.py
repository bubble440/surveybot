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
from selenium.webdriver.common.by import By

# Import des utilitaires
try:
    from Survey.dom_utils import _env_truthy
    from Survey.frame_utils import iter_frame_chains, switch_to_frame_chain
except ImportError:
    # Fallback pour tests locaux
    from Survey.dom_utils import _env_truthy
    from Survey.frame_utils import iter_frame_chains, switch_to_frame_chain
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
        start = time.time()
        
        # Installer un MutationObserver pour détecter les changements
        script_install = """
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
        """
        driver.execute_script(script_install)
        
        # Attendre la stabilité
        while time.time() - start < timeout_s:
            # Vérifier si le DOM est stable depuis step_s
            elapsed_since_mutation = driver.execute_script("""
                return (Date.now() - window.__domStableLastMutation) / 1000;
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
        result = driver.execute_script("""
            const inputs = document.querySelectorAll('input:not([type=hidden])');
            const selects = document.querySelectorAll('select');
            const textareas = document.querySelectorAll('textarea');
            const buttons = document.querySelectorAll('button, input[type=submit], input[type=button]');
            
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
                text_length: textLength
            };
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
        best_chain = []
        best_score = 0
        best_context = None
        
        # Parcourir toutes les chaînes de frames
        for chain in iter_frame_chains(driver, max_depth=max_depth):
            try:
                # Switcher vers cette chaîne
                switch_to_frame_chain(driver, chain)
                
                # Attendre stabilité
                _wait_for_survey_dom(driver, timeout_s=0.5, step_s=0.1)
                
                # Scorer le contexte
                context = _score_dom_context(driver)
                
                # Vérifier si c'est le meilleur
                if context['score'] > best_score:
                    best_score = context['score']
                    best_chain = chain.copy()
                    best_context = context
                
            except Exception:
                # Frame inaccessible ou erreur, continuer
                pass
            finally:
                # Retourner au contexte principal
                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass
        
        # Retourner au meilleur contexte trouvé
        if best_chain:
            switch_to_frame_chain(driver, best_chain)
        
        # Si aucun bon contexte trouvé, rester sur main
        if best_context is None:
            # Scorer le contexte principal
            driver.switch_to.default_content()
            best_context = _score_dom_context(driver)
            best_chain = []
        
        return best_chain, best_context
    
    except Exception:
        # En cas d'erreur, retourner contexte principal
        try:
            driver.switch_to.default_content()
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
            'text_length': 0
        }