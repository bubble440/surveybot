# survey_solver.py
# Orchestration minimaliste et robuste pour enchaÃƒÆ’Ã‚Â®ner les actions de page
# ÃƒÂ¢Ã…Â¾Ã…â€œ Laisse lÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢intelligence dÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢action ÃƒÆ’Ã‚Â  survey_executor.execute_survey_page()

from selenium.webdriver.support.ui import WebDriverWait  # [AJOUT]
from selenium.webdriver.support import expected_conditions as EC  # [AJOUT]
from selenium.webdriver.common.action_chains import ActionChains  # [AJOUT]
from selenium.webdriver.common.by import By
import time, os, sys
from preselection.question_validation import detect_disqualification_reason

STABILIZE_SLEEP = 2.0  # dÃƒÆ’Ã‚Â©lai court entre deux actions pour laisser le DOM respirer


def _switch_to_external_tab(driver):
    """
    Basculer sur lÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢onglet du survey (ÃƒÂ¢Ã¢â‚¬Â°Ã‚Â  TopSurveys).
    Utile juste aprÃƒÆ’Ã‚Â¨s avoir cliquÃƒÆ’Ã‚Â© sur Ãƒâ€šÃ‚Â« Participer Ãƒâ€šÃ‚Â».
    """
    time.sleep(3)  # laisse le temps aux nouveaux onglets dÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢apparaÃƒÆ’Ã‚Â®tre
    for handle in driver.window_handles:
        driver.switch_to.window(handle)
        current_url = driver.current_url
        if "topsurveys.app" not in current_url:
            print(f"ÃƒÂ°Ã…Â¸Ã‚Â§Ã‚Â­ Onglet externe dÃƒÆ’Ã‚Â©tectÃƒÆ’Ã‚Â© : {current_url}")
            return True
    print("ÃƒÂ¢Ã…Â¡Ã‚Â ÃƒÂ¯Ã‚Â¸Ã‚Â Aucun onglet externe dÃƒÆ’Ã‚Â©tectÃƒÆ’Ã‚Â©. Reste sur TopSurveys.")
    return False


def count_actionable_elements(driver) -> int:
    """
    Compte rapidement les ÃƒÆ’Ã‚Â©lÃƒÆ’Ã‚Â©ments actionnables visibles sur la page.
    Sert ÃƒÆ’Ã‚Â  savoir s'il reste 'beaucoup' d'inputs (ÃƒÆ’Ã‚Â©vite d'envoyer prev inutilement).
    """
    total = 0
    try:
        sels = [
            "input[type='radio']",
            "input[type='checkbox']",
            "input[type='text']",
            "textarea",
            "select",
            "button",
            "[role='button']",
            "input[type='submit']",
            "input[type='button']",
        ]
        for sel in sels:
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                try:
                    if (
                        el.is_displayed()
                        and el.rect.get("width", 0) > 10
                        and el.rect.get("height", 0) > 10
                    ):
                        total += 1
                except:
                    continue
    except:
        pass
    return total


def _has_actionable_elements(driver):
    """
    Heuristique : y aÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬ËœtÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Ëœil des ÃƒÆ’Ã‚Â©lÃƒÆ’Ã‚Â©ments actionnables ?
    ÃƒÂ¢Ã…Â¾Ã…â€œ VÃƒÆ’Ã‚Â©rifie le DOM courant **et** les iframes (profondeur 2).
    """

    def _here(drv):
        def _is_actionable(el) -> bool:
            """ÃƒÆ’Ã¢â‚¬Â°vite les faux positifs : cachÃƒÆ’Ã‚Â© / disabled / taille nulle."""
            try:
                if not el.is_displayed():
                    return False
                if not el.is_enabled():
                    return False
                r = getattr(el, "rect", None) or {}
                return (r.get("width", 0) or 0) > 2 and (r.get("height", 0) or 0) > 2
            except Exception:
                return False

        try:
            # Inputs classiques (uniquement visibles)
            inputs = drv.find_elements(
                By.CSS_SELECTOR,
                "input[type='radio'], input[type='checkbox'], input[type='text'], textarea, select",
            )
            if any(_is_actionable(el) for el in inputs):
                return True
            # Boutons navigation (FR/EN), inclut Start! et Start

            # ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ NEW: beaucoup de surveys cachent l'input (0x0) et rendent le label cliquable
            labels = drv.find_elements(By.CSS_SELECTOR, "label[for]")
            if any(_is_actionable(el) for el in labels):
                return True

            # ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ NEW: widgets custom (role=checkbox/radio)
            custom = drv.find_elements(By.CSS_SELECTOR, "[role='checkbox'], [role='radio']")
            if any(_is_actionable(el) for el in custom):
                return True

            # Boutons navigation (FR/EN), inclut Start! et Start (cas insensitive)
            btn_xpath = (
                "//button[normalize-space()='Start!' or "
                "contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'start') or "
                "contains(., 'Continuer') or contains(., 'Suivant') or "
                "contains(., 'Next') or contains(., 'Continue') or "
                "contains(., 'Commencer') or contains(., 'Soumettre') or contains(., 'Submit')]"
                " | //a[(contains(@class,'btn') or contains(@class,'button') or contains(@class,'cta')) and "
                "(contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'start') or "
                "contains(., 'Continuer') or contains(., 'Suivant') or contains(., 'Next') or "
                "contains(., 'Continue') or contains(., 'Commencer'))]"
            )

            if any(_is_actionable(el) for el in drv.find_elements(By.XPATH, btn_xpath)):
                return True

            # Inputs submit / boutons (uniquement visibles)
            submit_buttons = drv.find_elements(
                By.CSS_SELECTOR, "input[type='submit'], input[type='button'], button"
            )
            if any(_is_actionable(el) for el in submit_buttons):
                return True
        except Exception:
            pass
        return False

    # essaie ici
    if _here(driver):
        return True

    # essaie dans les iframes (profondeur 2)
    try:
        frames = driver.find_elements(By.TAG_NAME, "iframe")
        for fr in frames:
            try:
                driver.switch_to.frame(fr)
                if _here(driver):
                    driver.switch_to.default_content()
                    return True
                # profondeur supplÃƒÆ’Ã‚Â©mentaire
                subframes = driver.find_elements(By.TAG_NAME, "iframe")
                for sub in subframes:
                    try:
                        driver.switch_to.frame(sub)
                        if _here(driver):
                            driver.switch_to.default_content()
                            return True
                        driver.switch_to.parent_frame()
                    except Exception:
                        driver.switch_to.parent_frame()
                        continue
                driver.switch_to.default_content()
            except Exception:
                try:
                    driver.switch_to.default_content()
                except:
                    pass
                continue
    except Exception:
        pass

    return False


def _looks_like_end_screen(driver):
    """
    DÃƒÆ’Ã‚Â©tection trÃƒÆ’Ã‚Â¨s simple dÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢un ÃƒÆ’Ã‚Â©cran de fin (messages de remerciement/soumission).
    ÃƒÆ’Ã¢â‚¬Â°vite de tourner en rond une fois le questionnaire terminÃƒÆ’Ã‚Â©.
    """
    try:
        page_text = " ".join(
            [
                el.text.strip()
                for el in driver.find_elements(
                    By.XPATH, "//body//*[self::h1 or self::h2 or self::p or self::div]"
                )
                if el.text and len(el.text.strip()) > 3
            ]
        ).lower()

        end_markers = [
            "merci d'avoir rÃƒÆ’Ã‚Â©pondu",
            "merci pour votre participation",
            "vos rÃƒÆ’Ã‚Â©ponses ont ÃƒÆ’Ã‚Â©tÃƒÆ’Ã‚Â© enregistrÃƒÆ’Ã‚Â©es",
            "thank you for completing",
            "your responses have been recorded",
            "survey complete",
            "enquÃƒÆ’Ã‚Âªte terminÃƒÆ’Ã‚Â©e",
            "fin du questionnaire",
        ]
        return any(tok in page_text for tok in end_markers)
    except Exception:
        return False


def _close_other_tabs_in_current_session(driver):
    """Ferme tous les autres onglets de ce driver, garde l'onglet courant."""
    current = driver.current_window_handle
    handles = list(driver.window_handles)
    for h in handles:
        if h != current:
            try:
                driver.switch_to.window(h)
                driver.close()
            except Exception:
                pass
    try:
        driver.switch_to.window(current)
    except Exception:
        pass

def _page_text_lc(driver) -> str:
    try:
        return (driver.execute_script("return document.body.innerText || ''") or "").lower()
    except Exception:
        return ""

def _handle_topsurveys_partial_popup(driver) -> bool:
    """
    Detecte le popup 'Bon travail !' / 'Tu as partiellement repondu...' et clique sur 'Complete'.
    Retourne True s'il a ete traite.
    
    Ce popup apparait quand l'utilisateur est exclu d'un survey avant la fin.
    Le comportement souhaite: ignorer le popup et relancer un nouveau survey.
    
    Structure DOM identifiee:
    - <h1 class="notice-title">Bon travail !</h1>
    - <button data-test-id="ps-common-actions-button">Complete</button>
    """
    txt = _page_text_lc(driver)
    
    # Normaliser les apostrophes ET les accents pour matching fiable
    import unicodedata
    def _normalize(s):
        # Apostrophes courbes -> droites
        s = s.replace("'", "'").replace("'", "'")
        # Supprimer les accents (e avec accent -> e)
        s = unicodedata.normalize('NFD', s)
        s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
        return s.lower()
    
    txt_norm = _normalize(txt)
    
    # Patterns de detection (OR - un seul suffit)
    # Tous en minuscules et sans accents pour matcher le texte normalise
    partial_patterns = [
        "bon travail",                              # Titre du popup
        "tu as partiellement repondu",              # Debut du message (sans accent)
        "l'annonceur cherchait quelqu'un d'autre",  # Raison exclusion
        "credite ton compte pour l'effort",         # Mention de compensation (sans accent)
        "evalue ton experience",                    # Section rating du popup (sans accent)
    ]
    
    is_partial_popup = any(p in txt_norm for p in partial_patterns)
    
    if not is_partial_popup:
        return False
        
    print("[TOPSURVEYS] Popup exclusion partielle detecte: 'Bon travail !'")
    
    # Nettoyer les autres onglets maintenant
    _close_other_tabs_in_current_session(driver)

    # Bouton 'Complete' - sÃ©lecteur exact identifie dans le DOM
    btn = None
    
    # Strategie 1: data-test-id (EXACT - identifie dans le DOM reel)
    try:
        btn = WebDriverWait(driver, 3).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-test-id='ps-common-actions-button']"))
        )
    except Exception:
        pass
    
    # Strategie 2: classe p-btn--primary avec texte contenant "compl"
    if not btn:
        try:
            candidates = driver.find_elements(By.CSS_SELECTOR, "button.p-btn--primary")
            for c in candidates:
                try:
                    if c.is_displayed():
                        btn_text = _normalize(c.text or "")
                        if "compl" in btn_text:
                            btn = c
                            break
                except:
                    continue
        except Exception:
            pass
    
    # Strategie 3: span.p-btn-label avec texte "Complete"
    if not btn:
        try:
            spans = driver.find_elements(By.CSS_SELECTOR, "span.p-btn-label")
            for span in spans:
                try:
                    if "compl" in _normalize(span.text or ""):
                        # Remonter au bouton parent
                        btn = span.find_element(By.XPATH, "./ancestor::button")
                        if btn and btn.is_displayed():
                            break
                        btn = None
                except:
                    continue
        except Exception:
            pass

    if btn:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
        except Exception:
            pass
        for click_try in ("js", "ac", "native"):
            try:
                if click_try == "js":
                    driver.execute_script("arguments[0].click();", btn)
                elif click_try == "ac":
                    ActionChains(driver).move_to_element(btn).click().perform()
                else:
                    btn.click()
                print("[TOPSURVEYS] Bouton 'Complete' clique - popup ferme.")
                time.sleep(0.5)
                return True
            except Exception:
                continue

    # Fallback: cliquer sur le bouton X de fermeture si present
    try:
        close_selectors = [
            "button.popup-close",
            "button[class*='close']",
            "[aria-label='Close']", 
            "[aria-label='Fermer']",
        ]
        for sel in close_selectors:
            try:
                close_btn = driver.find_element(By.CSS_SELECTOR, sel)
                if close_btn.is_displayed():
                    driver.execute_script("arguments[0].click();", close_btn)
                    print("[TOPSURVEYS] Popup ferme via bouton X.")
                    time.sleep(0.3)
                    return True
            except:
                continue
    except Exception:
        pass

    print("[TOPSURVEYS] Bouton 'Complete' introuvable - tentative de continuer.")
    return False


def _payout_and_check_daily_stop(driver, account_id: str) -> bool:
    """
    À appeler à chaque retour sur TopSurveys. Vérifie dans l'ordre :
      1) Solde >= 5€  → retrait automatique (best-effort)
      2) Objectif journalier (1€) atteint → DAILY STOP (lève SystemExit via guard.pause)
    Retourne False si tout va bien (le bot peut continuer).
    Retourne True / lève SystemExit si DAILY STOP déclenché.
    """
    import Cash.payout as payout
    from State.daily_target import DAILY_TARGET_EUR
    from Management.guards.runtime_guard import get_guard, StopReason
    from Management.pause_policy import PausePolicy

    # 1) Retrait si solde >= 5€  (min_amount_eur=DAILY_TARGET_EUR : le modal ne propose
    #    que des options >= 5€, donc l'encaissement ne réussira que si le solde le permet)
    try:
        payout.check_and_cashout_if_needed(
            driver,
            account_id=account_id,
            min_amount_eur=DAILY_TARGET_EUR,
            cashout_order=("revolut", "paypal"),
            revolut_fullname="",
            revolut_tag="",
        )
    except Exception as e:
        print(f"[PAYOUT][WARN] retour TopSurveys: {e}")

    # 2) DAILY STOP si objectif journalier déjà atteint
    guard = get_guard()
    # FIX-C: même correction que dans soft_restart (launch.py) — le try/except
    # AttributeError était du dead code en prod. getattr couvre _NullGuard proprement.
    earnings = float(getattr(getattr(guard, "state", None), "earnings_today_eur", 0.0))

    if earnings >= DAILY_TARGET_EUR:
        print(f"[DAILY_STOP] {earnings:.2f}€ >= {DAILY_TARGET_EUR}€ → arrêt journalier")
        guard.pause(PausePolicy.DAILY_RESET, StopReason.DAILY_TARGET_REACHED)
        return True  # jamais atteint (pause lève SystemExit)

    return False


def _if_on_topsurveys_handle(driver, api_key, account_id, survey_context=None) -> bool:
    """
    Si on est sur app.topsurveys.app :
      - traite le popup 'partiellement rÃƒÆ’Ã‚Â©pondu' (ferme autres onglets + 'ComplÃƒÆ’Ã‚Â¨te' + relance)
      - sinon, vÃƒÆ’Ã‚Â©rifie la disqualification (ferme autres onglets + relance)
    Retourne True si on a *orchestrÃƒÆ’Ã‚Â© un retour* vers run_survey().
    """
    url = (driver.current_url or "").lower()
    if "topsurveys.app" not in url:
        return False

    # Cas A : popup partiel -> Complète + retrait/daily-stop + relance
    if _handle_topsurveys_partial_popup(driver):
        try:
            _payout_and_check_daily_stop(driver, account_id)  # retrait + DAILY STOP
            import preselection.survey_navigator
            import preselection.survey_handler
            time.sleep(1.0)
            preselection.survey_navigator.go_to_best_value_survey(driver)
            preselection.survey_handler.run_survey(driver, api_key, account_id=account_id, ctx=survey_context)
            return True
        except Exception as e:
            print("ÃƒÂ°Ã…Â¸Ã¢â‚¬â„¢Ã‚Â¥ Erreur relance aprÃƒÆ’Ã‚Â¨s ‘ComplÃƒÆ’Ã‚Â¨te’ :", e)
            return False

    # Cas B : check disqualification puis relance si besoin
    try:
        # ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ DÃƒÆ’Ã‚Â©tection disqualification centralisÃƒÆ’Ã‚Â©e (robuste)
        page_txt = _page_text_lc(driver)
        dq_reason = detect_disqualification_reason("", page_txt)
        if dq_reason:
            print(f"ÃƒÂ¢Ã…Â¡Ã‚Â ÃƒÂ¯Ã‚Â¸Ã‚Â Disqualification TopSurveys dÃƒÆ’Ã‚Â©tectÃƒÆ’Ã‚Â©e (reason={dq_reason}).")

            # best-effort : ferme le popup si prÃƒÆ’Ã‚Â©sent (mais la dÃƒÆ’Ã‚Â©tection ne dÃƒÆ’Ã‚Â©pend plus de ÃƒÆ’Ã‚Â§a)
            try:
                import preselection.question_analyzer
                preselection.question_analyzer.handle_disqualification_and_retry(driver)
            except Exception as e:
                print("ÃƒÂ¢Ã…Â¡Ã‚Â ÃƒÂ¯Ã‚Â¸Ã‚Â Popup disqualification dÃƒÆ’Ã‚Â©tectÃƒÆ’Ã‚Â© mais fermeture 'Ok' a ÃƒÆ’Ã‚Â©chouÃƒÆ’Ã‚Â©:", e)

            _close_other_tabs_in_current_session(driver)
            _payout_and_check_daily_stop(driver, account_id)  # retrait + DAILY STOP
            import preselection.survey_navigator
            import preselection.survey_handler
            time.sleep(0.7)
            preselection.survey_navigator.go_to_best_value_survey(driver)
            preselection.survey_handler.run_survey(driver, api_key, account_id=account_id, ctx=survey_context)
            return True
    except Exception as e:
        print("ÃƒÂ°Ã…Â¸Ã¢â‚¬â„¢Ã‚Â¥ Erreur check disqualification TopSurveys :", e)

    # -------------------------------------------------------------------
    # Cas C : Page de preselection TopSurveys (popup "Qualification" avec questions)
    # On detecte si on a des radios/checkboxes dans le popup et on les traite
    # avec la logique de preselection au lieu de survey_executor
    # -------------------------------------------------------------------
    try:
        has_preselection_inputs = driver.execute_script("""
            const popup = document.querySelector("div[class*='common-container']");
            if (!popup) return false;
            
            // Cherche des radios ou checkboxes visibles dans le popup
            const inputs = popup.querySelectorAll("input[type='radio'], input[type='checkbox'], [role='radio'], [role='checkbox']");
            if (inputs.length < 2) return false;
            
            // Verifie qu'au moins 2 sont visibles
            let visibleCount = 0;
            for (const inp of inputs) {
                const rect = inp.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) visibleCount++;
                if (visibleCount >= 2) return true;
            }
            
            // Fallback: cherche les labels radio TopSurveys
            const labels = popup.querySelectorAll("span[class*='p-radio-text'], span[class*='p-checkbox-text']");
            return labels.length >= 2;
        """)
        
        if has_preselection_inputs:
            print("[TOPSURVEYS][PRESELECTION] Page de questions detectee -> traitement preselection")
            
            import preselection.question_analyzer
            import preselection.response_executor
            import Management.guards.runtime_guard
            
            # Boucle de preselection : traiter les questions jusqu'a qualification ou sortie
            max_preselection_steps = 20
            for step in range(max_preselection_steps):
                print(f"[TOPSURVEYS][PRESELECTION] Step {step+1}/{max_preselection_steps}")
                
                # Verifier si on est toujours sur TopSurveys
                current_url = (driver.current_url or "").lower()
                if "topsurveys.app" not in current_url:
                    print("[TOPSURVEYS][PRESELECTION] Redirection hors TopSurveys -> qualification reussie")
                    return False  # Laisser solve_full_survey continuer sur le survey externe
                
                # Verifier qualification
                page_text_check = _page_text_lc(driver)
                if "qualifie pour cette enquete" in page_text_check or "qualifi" in page_text_check:
                    # Double check avec le texte exact
                    full_text = driver.execute_script("return document.body.innerText || ''").lower()
                    if "tu t'es qualifi" in full_text or "you are qualified" in full_text:
                        print("[TOPSURVEYS][PRESELECTION] Message qualification detecte -> clic Participer")
                        if preselection.question_analyzer.click_participer_if_qualified(driver):
                            time.sleep(2)
                            return False  # Laisser solve_full_survey continuer sur le survey externe
                
                # Verifier disqualification
                dq = detect_disqualification_reason("", page_text_check)
                if dq:
                    print(f"[TOPSURVEYS][PRESELECTION] Disqualification detectee ({dq}) -> relance")
                    try:
                        preselection.question_analyzer.handle_disqualification_and_retry(driver)
                    except:
                        pass
                    _close_other_tabs_in_current_session(driver)
                    _payout_and_check_daily_stop(driver, account_id)  # retrait + DAILY STOP
                    import preselection.survey_navigator
                    time.sleep(0.7)
                    preselection.survey_navigator.go_to_best_value_survey(driver)
                    continue  # Recommencer la boucle de preselection
                
                # Extraire et repondre a la question
                try:
                    question, answer = preselection.question_analyzer.get_response_for_question(driver, api_key)
                    
                    if question and answer and not isinstance(answer, dict):
                        print(f"[TOPSURVEYS][PRESELECTION] Q: {question[:50]}... -> R: {answer}")
                        success = preselection.response_executor.execute_response(driver, answer)
                        if success:
                            Management.guards.runtime_guard.get_guard().record_success()
                        time.sleep(1.5)
                        continue
                    
                    elif isinstance(answer, dict) and answer.get("action") == "SKIP":
                        print("[TOPSURVEYS][PRESELECTION] Question sensible -> skip")
                        decline_labels = ["Je ne peux pas repondre", "Prefer not to answer"]
                        for lab in decline_labels:
                            try:
                                if preselection.response_executor.execute_response(driver, lab):
                                    break
                            except:
                                pass
                        time.sleep(1.2)
                        continue
                    
                    else:
                        # Pas de question extraite - peut-etre ecran intermediaire
                        print("[TOPSURVEYS][PRESELECTION] Pas de question extraite - attente...")
                        time.sleep(1.0)
                        
                except Exception as e:
                    print(f"[TOPSURVEYS][PRESELECTION] Erreur extraction/reponse: {e}")
                    time.sleep(1.0)
            
            print("[TOPSURVEYS][PRESELECTION] Max steps atteint -> sortie")
            return True  # Forcer sortie de solve_full_survey
            
    except Exception as e:
        print(f"[TOPSURVEYS][PRESELECTION] Erreur detection: {e}")

    return False
    
# Référence module-level au SurveyContext actif — mis à jour par solve_full_survey()
# Utilisé par le handler SIGUSR1 (launch.py) pour dump terminal à la demande.
_current_survey_ctx = None

def get_current_survey_ctx():
    """Retourne le SurveyContext actif, ou None si aucun survey en cours."""
    return _current_survey_ctx

def solve_full_survey(driver, api_key, *, account_id: str, survey_context=None):
    import Management.redirect_watcher as redirect_watcher
    from Survey.survey_context import SurveyContext
    import Survey.survey_executor  
    import Management.guards.survey_difficulty_guard
    import Management.guards.runtime_guard

    """
    Boucle principale de résolution du survey.
    1) Bascule vers l'onglet externe + stabilisation d'URL
    2) Répète : execute_survey_page() — petite pause — test si on continue
    On sort si :
      - plus rien d'actionnable détecté (survey terminé) → soft-restart
      - stuck : réponse acceptée mais page ne bouge pas (Option B) → soft-restart
    """
    print("ÃƒÂ°Ã…Â¸Ã‚Â§Ã‚Âª [solve_full_survey] DÃƒÆ’Ã‚Â©but de traitement du survey...")
    # One SurveyContext per survey run — tracks Q/R history for coherent OpenAI responses
    _survey_ctx = survey_context or SurveyContext(session_id=account_id, openai_api_key=api_key)
    global _current_survey_ctx
    _current_survey_ctx = _survey_ctx

    # ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ‚Â SÃƒÆ’Ã‚Â©curitÃƒÆ’Ã‚Â© : si plusieurs onglets existent, on prend le dernier
    try:
        if len(driver.window_handles) > 1:
            driver.switch_to.window(driver.window_handles[-1])
            print(f"ÃƒÂ°Ã…Â¸Ã‚Â§Ã‚Â­ Focus forcÃƒÆ’Ã‚Â© sur lÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢onglet actif : {driver.current_url}")
    except Exception as e:
        print("ÃƒÂ¢Ã…Â¡Ã‚Â ÃƒÂ¯Ã‚Â¸Ã‚Â Impossible de forcer le focus onglet :", e)


    _switch_to_external_tab(driver)

    # 1) Attendre que la redirection sÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢arrÃƒÆ’Ã‚Âªte sur une URL stable
    final_url = redirect_watcher.wait_for_final_redirection(driver)
    print(f"ÃƒÂ°Ã…Â¸Ã…â€™Ã‚Â URL finale stabilisÃƒÆ’Ã‚Â©e : {final_url}")

    # 2) Boucle d'exécution des actions
    _no_progress_count = 0        # Option B : succès sans avance de page
    _NO_PROGRESS_THRESHOLD = 3
    last_url = driver.current_url
    guard = Management.guards.runtime_guard.get_guard()

    while True:
        print("[solve_full_survey] Exécution de la page courante")

        # RÃƒÆ’Ã‚Â©initialise le drapeau de succÃƒÆ’Ã‚Â¨s cÃƒÆ’Ã‚Â´tÃƒÆ’Ã‚Â© handlers
        try:
            setattr(driver, "last_action_success", False)
        except Exception:
            pass

        # [PATCH] Purge d'un overlay trop ancien (>3s) pour ÃƒÆ’Ã‚Â©viter des ÃƒÆ’Ã‚Â©tats collants
        try:
            ov = getattr(driver, "_ui_overlay_opened", None)
            if ov and (time.time() - ov.get("ts", 0) > 3.0):
                setattr(driver, "_ui_overlay_opened", None)
        except Exception:
            pass

        # --- STRICT GUARD (per-step, throttlÃƒÆ’Ã‚Â©) -----------------------------

        # on ne fait pas le check ÃƒÆ’Ã‚Â  chaque micro-iteration si overlay dropdown etc.
        # mais par dÃƒÆ’Ã‚Â©faut: 1 check par ÃƒÆ’Ã‚Â©tape suffit
        is_strict, reason = Management.guards.survey_difficulty_guard.detect_strict_survey(driver)
        if is_strict:
            # ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ CAPTCHA : comportement diffÃƒÆ’Ã‚Â©rent selon environnement
            if reason == "captcha":
                from config import should_pause_for_captcha, get_captcha_behavior
                captcha_behavior = get_captcha_behavior()

                # === AUTO : résolution 2Captcha (local + prod) ===
                if captcha_behavior == "auto_2captcha":
                    print("[CAPTCHA] Tentative de résolution automatique via 2Captcha...")
                    try:
                        from captcha.recaptcha_handler import solve_recaptcha_v2_auto
                        resolved = solve_recaptcha_v2_auto(driver)
                    except Exception as e:
                        print(f"[CAPTCHA] Erreur inattendue recaptcha_handler: {e}")
                        resolved = False
                    if resolved:
                        print("[CAPTCHA] ✅ reCAPTCHA résolu — reprise du survey")
                        continue  # retour au début de la boucle principale
                    else:
                        print("[CAPTCHA] ❌ Échec résolution automatique → abandon survey")
                        Management.guards.runtime_guard.get_guard().record_success()
                        Management.guards.runtime_guard.get_guard().signal_strict_survey("captcha_auto_failed")
                        return

                # === PROD sans clé : restart immédiat (inchangé) ===
                elif captcha_behavior == "restart":
                    print(f"[STRICT_SURVEY][MID] Captcha détecté -> restart propre")
                    Management.guards.runtime_guard.get_guard().record_success()
                    Management.guards.runtime_guard.get_guard().signal_strict_survey(f"strict_mid_captcha")
                    return

                # === LOCAL interactif : pause manuelle (inchangé, fall-through) ===
                # LOCAL : pause manuelle pour rÃƒÆ’Ã‚Â©solution utilisateur
                print("[LOCAL][CAPTCHA] ÃƒÂ¢Ã…Â¡Ã‚Â ÃƒÂ¯Ã‚Â¸Ã‚Â  CAPTCHA dÃƒÆ’Ã‚Â©tectÃƒÆ’Ã‚Â© ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ rÃƒÆ’Ã‚Â©solution MANUELLE requise")
                
                # Anti-boucle : ne pas mettre en pause plusieurs fois sur la mÃƒÆ’Ã‚Âªme URL
                try:
                    captcha_url = driver.current_url or ""
                    last_captcha_url = getattr(driver, "_last_captcha_pause_url", None)
                    if last_captcha_url == captcha_url:
                        print("[LOCAL][CAPTCHA] ÃƒÂ¢Ã‚ÂÃ‚Â­ÃƒÂ¯Ã‚Â¸Ã‚Â  Captcha dÃƒÆ’Ã‚Â©jÃƒÆ’Ã‚Â  traitÃƒÆ’Ã‚Â© sur cette URL, on continue")
                        # On continue l'exÃƒÆ’Ã‚Â©cution normale sans repause
                    else:
                        # Marquer cette URL comme traitÃƒÆ’Ã‚Â©e
                        setattr(driver, "_last_captcha_pause_url", captcha_url)
                        
                        # Pause interactive si terminal disponible
                        from config import should_block_for_input
                        if should_block_for_input():
                            try:
                                input("[LOCAL][PAUSE] ÃƒÂ°Ã…Â¸Ã‚Â§Ã‚Â© RÃƒÆ’Ã‚Â©sous le CAPTCHA dans le navigateur, puis appuie sur EntrÃƒÆ’Ã‚Â©e...\n")
                            except KeyboardInterrupt:
                                print("[LOCAL] ÃƒÂ¢Ã‚ÂÃ‚Â¹ÃƒÂ¯Ã‚Â¸Ã‚Â  Abandon demandÃƒÆ’Ã‚Â© par l'utilisateur")
                                Management.guards.runtime_guard.get_guard().record_success()
                                Management.guards.runtime_guard.get_guard().signal_strict_survey("captcha_user_abort")
                                return
                        else:
                            print("[LOCAL][CAPTCHA] ÃƒÂ¢Ã…Â¡Ã‚Â ÃƒÂ¯Ã‚Â¸Ã‚Â  Terminal non-interactif, pas de pause possible")
                            Management.guards.runtime_guard.get_guard().record_success()
                            Management.guards.runtime_guard.get_guard().signal_strict_survey("captcha_no_tty")
                            return
                        
                        # VÃƒÆ’Ã‚Â©rification : attendre que le captcha disparaisse (max 30s)
                        print("[LOCAL][CAPTCHA] ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ‚Â VÃƒÆ’Ã‚Â©rification de la disparition du captcha...")
                        deadline = time.time() + 30.0
                        captcha_resolved = False
                        
                        while time.time() < deadline:
                            # Re-check si le captcha est toujours lÃƒÆ’Ã‚Â 
                            still_strict, still_reason = Management.guards.survey_difficulty_guard.detect_strict_survey(driver)
                            if not still_strict or still_reason != "captcha":
                                print("[LOCAL][CAPTCHA] ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ Captcha rÃƒÆ’Ã‚Â©solu ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ continuation de l'exÃƒÆ’Ã‚Â©cution")
                                captcha_resolved = True
                                break
                            time.sleep(1.0)
                        
                        if not captcha_resolved:
                            print("[LOCAL][CAPTCHA] ÃƒÂ¢Ã‚ÂÃ‚Â±ÃƒÂ¯Ã‚Â¸Ã‚Â  Timeout : captcha toujours prÃƒÆ’Ã‚Â©sent aprÃƒÆ’Ã‚Â¨s 30s")
                            Management.guards.runtime_guard.get_guard().record_success()
                            Management.guards.runtime_guard.get_guard().signal_strict_survey("captcha_timeout")
                            return
                        
                        # Captcha rÃƒÆ’Ã‚Â©solu avec succÃƒÆ’Ã‚Â¨s : on continue la boucle normale
                        print("[LOCAL][CAPTCHA] ÃƒÂ°Ã…Â¸Ã…Â¡Ã¢â€šÂ¬ Reprise de l'exÃƒÆ’Ã‚Â©cution du survey")
                except Exception as e:
                    print(f"[LOCAL][CAPTCHA] ÃƒÂ¢Ã‚ÂÃ…â€™ Erreur lors de la gestion du captcha : {e}")
                    Management.guards.runtime_guard.get_guard().record_success()
                    Management.guards.runtime_guard.get_guard().signal_strict_survey("captcha_error")
                    return
            
            # ÃƒÂ¢Ã…Â¡Ã‚Â ÃƒÂ¯Ã‚Â¸Ã‚Â AUTRES RAISONS (drag_drop, hold_button, etc.) : arrÃƒÆ’Ã‚Âªt immÃƒÆ’Ã‚Â©diat (inchangÃƒÆ’Ã‚Â©)
            else:
                print(f"[STRICT_SURVEY][MID] DÃƒÆ’Ã‚Â©tectÃƒÆ’Ã‚Â© en cours de survey ({reason}) -> restart propre")
                Management.guards.runtime_guard.get_guard().record_success()
                Management.guards.runtime_guard.get_guard().signal_strict_survey(f"strict_mid_{reason}")
                return        
        
        # -------------------------------------------------------------------
        # CHECK TOPSURVEYS AVANT execute_survey_page
        # Evite tout chemin alternatif sur le popup "Bon travail !" (disqualification)
        # -------------------------------------------------------------------
        try:
            current_url_check = (driver.current_url or "").lower()
            if "topsurveys.app" in current_url_check:
                if _if_on_topsurveys_handle(driver, api_key, account_id, survey_context=_survey_ctx):
                    print("[PRE-EXEC] Retour TopSurveys traite -> arret solve_full_survey()")
                    return
        except Exception as e:
            print(f"[PRE-EXEC] Check TopSurveys echoue: {e}")

        # -------------------------------------------------------------------
        # a) Laisser GPT dÃƒÆ’Ã‚Â©cider de lÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢action ÃƒÆ’Ã‚Â  partir de la capture dÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã‚Â©cran
        success = Survey.survey_executor.execute_survey_page(driver, api_key, ctx=_survey_ctx)

        # Connexion RuntimeGuard
        if success:
            guard.record_success()
        else:
            guard.record_error()

        # LOCAL DEBUG: affiche le contexte accumulé après chaque page
        if (os.getenv("SURVEY_CTX_DEBUG") or "").strip() == "1":
            dump = _survey_ctx.dump()
            print(
                f"\n[SURVEY_CTX] "
                f"entries={len(dump.get('history', []))} "
                f"summary={dump.get('summary', '')[:120] or '(none yet)'}"
            )
            for i, entry in enumerate(dump.get("history", [])[-5:], 1):
                print(f"  Q{i}: {entry.get('question','')[:80]} → {entry.get('answer','')}")
            print()
            
        # [PATCH] Mode "overlay ouvert" ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ recapture rapide
        try:
            overlay = getattr(driver, "_ui_overlay_opened", None)
        except Exception:
            overlay = None

        if overlay and overlay.get("type") == "dropdown":
            print(
                "ÃƒÂ°Ã…Â¸Ã…Â½Ã‚Â¯ Dropdown ouvert ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ recapture immÃƒÆ’Ã‚Â©diate (on saute l'attente/redirection)."
            )
            time.sleep(0.3)  # laisser la liste se peindre
            continue  # on relance une itÃƒÆ’Ã‚Â©ration : GPT verra la liste OUVERTE

        # b) Micro-pause pour laisser le DOM respirer
        time.sleep(STABILIZE_SLEEP)

        # c) Attente ADAPTATIVE aprÃƒÆ’Ã‚Â¨s action
        #    - Si une action vient de rÃƒÆ’Ã‚Â©ussir et qu'il reste des choses ÃƒÆ’Ã‚Â  faire sur la page,
        #      on NE bloque PAS sur une redirection (les surveys exigent souvent plusieurs entrÃƒÆ’Ã‚Â©es).
        try:
            just_succeeded = bool(
                getattr(driver, "last_action_success", False) or success
            )
        except Exception:
            just_succeeded = bool(success)

        # y a-t-il encore des ÃƒÆ’Ã‚Â©lÃƒÆ’Ã‚Â©ments actionnables visibles ?
        has_more_to_do = _has_actionable_elements(driver)

        if just_succeeded and has_more_to_do:
            print(
                "ÃƒÂ¢Ã‚ÂÃ‚Â­ÃƒÂ¯Ã‚Â¸Ã‚Â Action en-page rÃƒÆ’Ã‚Â©ussie et autres ÃƒÆ’Ã‚Â©lÃƒÆ’Ã‚Â©ments visibles ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ pas d'attente de navigation."
            )
            time.sleep(0.4)  # laisser le framework rÃƒÆ’Ã‚Â©agir
            # on repart tout de suite sur une nouvelle itÃƒÆ’Ã‚Â©ration (nouvelle capture)
            continue

        # Sinon, il y a peut-ÃƒÆ’Ã‚Âªtre une navigation : stabilisation courte si succÃƒÆ’Ã‚Â¨s, sinon normale
        maxw = 3 if just_succeeded else 8
        stabilized_url = redirect_watcher.wait_for_final_redirection(driver, max_wait=maxw)
        current_url = stabilized_url or driver.current_url

        # Si lÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢URL a changÃƒÆ’Ã‚Â© ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ on inspecte le nouvel emplacement
        if current_url != last_url:
            _no_progress_count = 0  # URL a changé, réinitialisation du détecteur stuck
            print(f"[solve_full_survey] Changement d'URL {last_url} \u2192 {current_url}")
            last_url = current_url

            # Retour TopSurveys ? Traite popup 'Complète' ou disqualification, puis relance.
            try:
                if _if_on_topsurveys_handle(driver, api_key, account_id, survey_context=_survey_ctx):
                    print("[solve_full_survey] Retour TopSurveys → arrêt.")
                    return
            except Exception as e:
                print(f"[solve_full_survey] Hook TopSurveys échoué : {e}")

            continue



        # -------------------------------------------------------------------
        # Option B — Stuck detection : succès accepté mais page ne bouge pas
        # (Ne s'active PAS sur les pages multi-inputs : ceux-ci passent par
        # "just_succeeded and has_more_to_do: continue" plus haut)
        # -------------------------------------------------------------------
        if success and current_url == last_url:
            _no_progress_count += 1
            if _no_progress_count >= _NO_PROGRESS_THRESHOLD:
                print(f"[STUCK] Réponse acceptée {_NO_PROGRESS_THRESHOLD} fois sans avance → soft-restart")
                guard.record_success()
                guard.request_survey_restart("solve_no_progress")
                return
        else:
            _no_progress_count = 0

        # d) Conditions dÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢arrÃƒÆ’Ã‚Âªt
        # if _looks_like_end_screen(driver):
            # print("ÃƒÂ°Ã…Â¸Ã‚ÂÃ‚Â ÃƒÆ’Ã¢â‚¬Â°cran de fin dÃƒÆ’Ã‚Â©tectÃƒÆ’Ã‚Â©. Fin du survey.")
            # break

        # Heuristique : si aucune actionnable visible MAIS on vient de rÃƒÆ’Ã‚Â©ussir une action,
        # on laisse 1 tour de plus au DOM pour apparaÃƒÆ’Ã‚Â®tre (ÃƒÆ’Ã‚Â©vite lÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢arrÃƒÆ’Ã‚Âªt prÃƒÆ’Ã‚Â©maturÃƒÆ’Ã‚Â©).
        has_actionables = _has_actionable_elements(driver)
        if not has_actionables:
            just_succeeded = False
            try:
                just_succeeded = bool(
                    getattr(driver, "last_action_success", False) or success
                )
            except Exception:
                pass

            if just_succeeded:
                print(
                    "ÃƒÂ¢Ã‚ÂÃ‚Â³ Pas encore dÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã‚Â©lÃƒÆ’Ã‚Â©ment actionnable, mais action rÃƒÆ’Ã‚Â©ussie ÃƒÆ’Ã‚Â  lÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã‚Â©tape prÃƒÆ’Ã‚Â©cÃƒÆ’Ã‚Â©dente. On continue."
                )
                # petit dÃƒÆ’Ã‚Â©lai de grÃƒÆ’Ã‚Â¢ce
                time.sleep(1.0)
                continue

            print("[solve_full_survey] Aucun élément actionnable → survey terminé, soft-restart.")
            guard.record_success()
            guard.request_survey_restart("survey_end")
            return

        # e) Si lÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢URL nÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã‚Â©volue pas ET lÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢action a ÃƒÆ’Ã‚Â©chouÃƒÆ’Ã‚Â© 2 fois dÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢affilÃƒÆ’Ã‚Â©e, on sort (sÃƒÆ’Ã‚Â©curitÃƒÆ’Ã‚Â© douce)
        if current_url == last_url and success is False:
            print("ÃƒÂ¢Ã…Â¡Ã‚Â ÃƒÂ¯Ã‚Â¸Ã‚Â Ni changement dÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢URL ni action rÃƒÆ’Ã‚Â©ussie. Nouvelle tentativeÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦")
            # on laisse encore 1 tour; si ÃƒÆ’Ã‚Â§a persiste, la condition ciÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Ëœdessus arrÃƒÆ’Ã‚Âªtera.
        last_url = current_url
        # Non-blocking: triggers async summary generation every N pages
        try:
            _survey_ctx.maybe_update_summary()
        except Exception:
            pass
