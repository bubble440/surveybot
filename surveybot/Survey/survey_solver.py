# survey_solver.py
# Orchestration minimaliste et robuste pour enchaîner les actions de page
# ➜ Laisse l’intelligence d’action à survey_executor.execute_survey_page()

from selenium.webdriver.support.ui import WebDriverWait  # [AJOUT]
from selenium.webdriver.support import expected_conditions as EC  # [AJOUT]
from selenium.webdriver.common.action_chains import ActionChains  # [AJOUT]
from selenium.webdriver.common.by import By
import time, os, sys
from preselection.question_validation import detect_disqualification_reason
from Survey.log_utils import log_debug, log_info

STABILIZE_SLEEP = 2.0       # délai court entre deux actions pour laisser le DOM respirer
PAUSE_BEFORE_FIRST_SCAN = 1.5  # post-chargement, avant le premier scan DOM (absorbe latence proxy)
PAUSE_POST_CTA_NAV = 2.0       # après navigation CTA, avant toute interaction avec la nouvelle page


def _switch_to_external_tab(driver):
    """
    Basculer sur l’onglet du survey (≠ TopSurveys).
    Utile juste après avoir cliqué sur « Participer ».
    """
    time.sleep(3)  # laisse le temps aux nouveaux onglets d’apparaître
    for handle in driver.window_handles:
        driver.switch_to.window(handle)
        current_url = driver.current_url
        if "topsurveys.app" not in current_url:
            print(f"🧭 Onglet externe détecté : {current_url}")
            return True
    print("⚠ Aucun onglet externe détecté. Reste sur TopSurveys.")
    return False


def count_actionable_elements(driver) -> int:
    """
    Compte rapidement les éléments actionnables visibles sur la page.
    Sert à savoir s'il reste 'beaucoup' d'inputs (évite d'envoyer prev inutilement).
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
    Heuristique : y a‑t‑il des éléments actionnables ?
    ➜ Vérifie le DOM courant **et** les iframes (profondeur 2).
    """

    def _here(drv):
        def _is_actionable(el) -> bool:
            """Évite les faux positifs : caché / disabled / taille nulle."""
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

            # ✅ NEW: beaucoup de surveys cachent l'input (0x0) et rendent le label cliquable
            labels = drv.find_elements(By.CSS_SELECTOR, "label[for]")
            if any(_is_actionable(el) for el in labels):
                return True

            # ✅ NEW: widgets custom (role=checkbox/radio)
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
                # profondeur supplémentaire
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


def _get_multi_page_state(driver) -> tuple:
    """
    Empreinte de l'état courant d'une page multi-inputs pour la stuck detection.
    Retourne un tuple (nb_questions, textes_questions, états_inputs) :
      - nb_questions  : nombre de labels de questions visibles         (niveau 2)
      - textes        : tuple des textes de questions (100 chars max)  (niveau 3)
      - inputs        : tuple trié des valeurs sélectionnées/cochées   (niveau 4)
    En cas d'erreur, retourne (0, (), ()) pour ne pas bloquer.
    """
    try:
        # Niveau 2 & 3 — textes des questions visibles
        q_texts = []
        for sel in [
            "fieldset legend",
            "[class*='question'] label",
            "[class*='Question'] label",
            "[role='group'] label",
        ]:
            elems = [e for e in driver.find_elements(By.CSS_SELECTOR, sel) if e.is_displayed()]
            if elems:
                q_texts = [e.text.strip()[:100] for e in elems if e.text.strip()]
                break

        # Niveau 4 — états des inputs sélectionnés/cochés
        states = []
        for r in driver.find_elements(By.CSS_SELECTOR, "input[type='radio']:checked"):
            states.append("r:" + (r.get_attribute("value") or r.get_attribute("id") or "?"))
        for c in driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']:checked"):
            states.append("c:" + (c.get_attribute("value") or c.get_attribute("id") or "?"))
        for s in driver.find_elements(By.CSS_SELECTOR, "select"):
            try:
                v = s.get_attribute("value") or ""
                if v:
                    states.append("s:" + v)
            except Exception:
                pass

        return (len(q_texts), tuple(q_texts), tuple(sorted(states)))
    except Exception:
        return (0, (), ())


def _looks_like_end_screen(driver):
    """
    Détection très simple d’un écran de fin (messages de remerciement/soumission).
    Évite de tourner en rond une fois le questionnaire terminé.
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
            "merci d'avoir répondu",
            "merci pour votre participation",
            "vos réponses ont été enregistrées",
            "thank you for completing",
            "your responses have been recorded",
            "survey complete",
            "enquête terminée",
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
                time.sleep(3)
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


_NETWORK_ERR_SIGNALS = (
    "err_tunnel_connection_failed",
    "this site can\u2019t be reached",
    "this site can't be reached",
    "err_connection_refused",
    "err_name_not_resolved",
)


def _recover_from_network_error(driver) -> bool:
    """
    Détecte une erreur réseau Chrome (ERR_TUNNEL_CONNECTION_FAILED, etc.) et tente
    un refresh unique (budget = 1 par URL) pour récupérer.

    Séquence :
      1. Détecte l'erreur via le titre + body de la page.
      2. Vérifie le budget (1 refresh par URL) — abandon si déjà tenté.
      3. Attend 12 secondes (délai fixe pour laisser le proxy se rétablir).
      4. Rafraîchit la page.
      5. Si dialog natif "Confirm Form Resubmission" présent → l'accepte.
      6. Attend le chargement de la page.
      7. Si l'erreur persiste → log (abandon contrôlé, budget épuisé).

    Retourne True si une erreur réseau a été détectée (page en erreur avant l'appel),
    False si la page est saine (chemin rapide, aucun effet de bord).
    """
    # -- Détection via page_source (seul signal fiable sur chrome-error://) --
    # driver.title et document.body.innerText sont vides sur les pages d'erreur Chrome
    # car leur contenu est dans un Shadow DOM natif inaccessible à JavaScript.
    # driver.page_source expose le HTML complet, y compris id="main-frame-error".
    try:
        source_lc = (driver.page_source or "").lower()
    except Exception:
        return False

    if not any(sig in source_lc for sig in _NETWORK_ERR_SIGNALS):
        return False

    # -- Budget : 2 erreurs consécutives max (robuste aux SPA à URL stable) --
    # Le compteur est remis à 0 par l'appelant à chaque itération saine (retour=False).
    _count = getattr(driver, "_net_err_consecutive_count", 0) + 1
    try:
        setattr(driver, "_net_err_consecutive_count", _count)
    except Exception:
        pass

    if _count > 1:
        log_info("NET-ERR", f"Budget épuisé ({_count} erreurs consécutives) → abandon")
        return True

    try:
        current_url = driver.current_url or ""
    except Exception:
        current_url = ""

    log_info("NET-ERR", "Erreur réseau détectée → attente 12s puis refresh")
    time.sleep(12)

    # driver.get() au lieu de driver.refresh() : évite le dialog natif Chrome
    # "Confirm Form Resubmission" (overlay hors DOM, inaccessible à Selenium)
    # qui apparaît systématiquement lors d'un refresh sur une page POST en erreur.
    try:
        driver.get(current_url)
    except Exception as e:
        log_info("NET-ERR", f"driver.get() a échoué : {e}")
        return True

    # -- Attente chargement --
    try:
        from Management import redirect_watcher as _rw
        _rw.wait_for_page_load(driver, timeout=30)
    except Exception:
        time.sleep(5)

    # -- Vérification post-refresh (log seulement, budget déjà consommé) --
    try:
        source2 = (driver.page_source or "").lower()
        if any(sig in source2 for sig in _NETWORK_ERR_SIGNALS):
            log_info("NET-ERR", "Page toujours en erreur après refresh → abandon (budget épuisé)")
    except Exception:
        pass

    return True


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

    # Bouton 'Complete' - sélecteur exact identifie dans le DOM
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
    from Cash.payout import MIN_CASHOUT_EUR
    from State.daily_target import DAILY_TARGET_EUR
    from Management.guards.runtime_guard import get_guard, StopReason
    from Management.pause_policy import PausePolicy

    # 1) Retrait uniquement si solde >= 5 € (MIN_CASHOUT_EUR) :
    #    le modal TopSurveys ne propose que des options >= 5 €,
    #    donc l'ouverture en dessous de ce seuil est inutile.
    try:
        payout.check_and_cashout_if_needed(
            driver,
            account_id=account_id,
            min_amount_eur=MIN_CASHOUT_EUR,
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
      - traite le popup 'partiellement répondu' (ferme autres onglets + 'Complète' + relance)
      - sinon, vérifie la disqualification (ferme autres onglets + relance)
    Retourne True si on a *orchestré un retour* vers run_survey().
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
            print("💥 Erreur relance après Complète :", e)
            return False

    # Cas B : check disqualification puis relance si besoin
    try:
        # ✅ Détection disqualification centralisée (robuste)
        page_txt = _page_text_lc(driver)
        dq_reason = detect_disqualification_reason("", page_txt)
        if dq_reason:
            print(f"⚠ Disqualification TopSurveys détectée (reason={dq_reason}).")

            # best-effort : ferme le popup si présent (mais la détection ne dépend plus de ça)
            try:
                import preselection.question_analyzer
                preselection.question_analyzer.handle_disqualification_and_retry(driver)
            except Exception as e:
                print("⚠ Popup disqualification détecté mais fermeture 'Ok' a échoué:", e)

            _close_other_tabs_in_current_session(driver)
            _payout_and_check_daily_stop(driver, account_id)  # retrait + DAILY STOP
            import preselection.survey_navigator
            import preselection.survey_handler
            time.sleep(0.7)
            preselection.survey_navigator.go_to_best_value_survey(driver)
            preselection.survey_handler.run_survey(driver, api_key, account_id=account_id, ctx=survey_context)
            return True
    except Exception as e:
        print("💥 Erreur check disqualification TopSurveys :", e)

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
    print("🧪 [solve_full_survey] Début de traitement du survey...")
    # One SurveyContext per survey run — tracks Q/R history for coherent OpenAI responses
    _survey_ctx = survey_context or SurveyContext(session_id=account_id, openai_api_key=api_key)
    global _current_survey_ctx
    _current_survey_ctx = _survey_ctx

    #  Sécurité : si plusieurs onglets existent, on prend le dernier
    try:
        if len(driver.window_handles) > 1:
            driver.switch_to.window(driver.window_handles[-1])
            print(f"🧭 Focus forcé sur l’onglet actif : {driver.current_url}")
    except Exception as e:
        print("⚠ Impossible de forcer le focus onglet :", e)


    _switch_to_external_tab(driver)

    # 1) Attendre que la redirection s’arrête sur une URL stable
    final_url = redirect_watcher.wait_for_final_redirection(driver)
    print(f" URL finale stabilisée : {final_url}")
    time.sleep(PAUSE_BEFORE_FIRST_SCAN)  # laisser le DOM se stabiliser avant le premier scan

    # 2) Boucle d'exécution des actions
    _no_progress_count = 0        # Option B : succès sans avance de page (single-question)
    _NO_PROGRESS_THRESHOLD = 3
    last_url = driver.current_url
    last_question_key = ""        # Clé de la dernière question vue (détection intra-page)
    _multi_no_progress_count = 0  # Stuck detection pour pages multi-inputs
    _last_multi_page_state = None # Empreinte (count, texts, inputs) de la dernière itération multi
    _cta_fail_count = 0           # Failure pipeline : URL inchangée + success=False consécutifs
    guard = Management.guards.runtime_guard.get_guard()

    while True:
        print("[solve_full_survey] Exécution de la page courante")

        # Réinitialise le drapeau de succès côté handlers
        try:
            setattr(driver, "last_action_success", False)
        except Exception:
            pass

        # [PATCH] Purge d'un overlay trop ancien (>3s) pour éviter des états collants
        try:
            ov = getattr(driver, "_ui_overlay_opened", None)
            if ov and (time.time() - ov.get("ts", 0) > 3.0):
                setattr(driver, "_ui_overlay_opened", None)
        except Exception:
            pass

        # --- STRICT GUARD (per-step, throttlé) -----------------------------

        # on ne fait pas le check à chaque micro-iteration si overlay dropdown etc.
        # mais par défaut: 1 check par étape suffit
        is_strict, reason = Management.guards.survey_difficulty_guard.detect_strict_survey(driver)
        if is_strict:
            # ✅ CAPTCHA : comportement différent selon environnement
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
                # LOCAL : pause manuelle pour résolution utilisateur
                print("[LOCAL][CAPTCHA] ⚠  CAPTCHA détecté → résolution MANUELLE requise")
                
                # Anti-boucle : ne pas mettre en pause plusieurs fois sur la même URL
                try:
                    captcha_url = driver.current_url or ""
                    last_captcha_url = getattr(driver, "_last_captcha_pause_url", None)
                    if last_captcha_url == captcha_url:
                        print("[LOCAL][CAPTCHA]   Captcha déjà traité sur cette URL, on continue")
                        # On continue l'exécution normale sans repause
                    else:
                        # Marquer cette URL comme traitée
                        setattr(driver, "_last_captcha_pause_url", captcha_url)
                        
                        # Pause interactive si terminal disponible
                        from config import should_block_for_input
                        if should_block_for_input():
                            try:
                                input("[LOCAL][PAUSE] 🧩 Résous le CAPTCHA dans le navigateur, puis appuie sur Entrée...\n")
                            except KeyboardInterrupt:
                                print("[LOCAL]   Abandon demandé par l'utilisateur")
                                Management.guards.runtime_guard.get_guard().record_success()
                                Management.guards.runtime_guard.get_guard().signal_strict_survey("captcha_user_abort")
                                return
                        else:
                            print("[LOCAL][CAPTCHA] ⚠  Terminal non-interactif, pas de pause possible")
                            Management.guards.runtime_guard.get_guard().record_success()
                            Management.guards.runtime_guard.get_guard().signal_strict_survey("captcha_no_tty")
                            return
                        
                        # Vérification : attendre que le captcha disparaisse (max 30s)
                        print("[LOCAL][CAPTCHA]  Vérification de la disparition du captcha...")
                        deadline = time.time() + 30.0
                        captcha_resolved = False
                        
                        while time.time() < deadline:
                            # Re-check si le captcha est toujours là
                            still_strict, still_reason = Management.guards.survey_difficulty_guard.detect_strict_survey(driver)
                            if not still_strict or still_reason != "captcha":
                                print("[LOCAL][CAPTCHA] ✅ Captcha résolu → continuation de l'exécution")
                                captcha_resolved = True
                                break
                            time.sleep(1.0)
                        
                        if not captcha_resolved:
                            print("[LOCAL][CAPTCHA]   Timeout : captcha toujours présent après 30s")
                            Management.guards.runtime_guard.get_guard().record_success()
                            Management.guards.runtime_guard.get_guard().signal_strict_survey("captcha_timeout")
                            return
                        
                        # Captcha résolu avec succès : on continue la boucle normale
                        print("[LOCAL][CAPTCHA] 🚀 Reprise de l'exécution du survey")
                except Exception as e:
                    print(f"[LOCAL][CAPTCHA]  Erreur lors de la gestion du captcha : {e}")
                    Management.guards.runtime_guard.get_guard().record_success()
                    Management.guards.runtime_guard.get_guard().signal_strict_survey("captcha_error")
                    return
            
            # ⚠ AUTRES RAISONS (drag_drop, hold_button, etc.) : arrêt immédiat (inchangé)
            else:
                print(f"[STRICT_SURVEY][MID] Détecté en cours de survey ({reason}) -> restart propre")
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

        # --- Récupération erreur réseau Chrome (ERR_TUNNEL_CONNECTION_FAILED) ---
        # Retour False = page saine → reset du compteur d'erreurs consécutives.
        if not _recover_from_network_error(driver):
            try:
                setattr(driver, "_net_err_consecutive_count", 0)
            except Exception:
                pass

        # -------------------------------------------------------------------
        # a) Laisser GPT décider de l’action à partir de la capture d’écran
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
            
        # [PATCH] Mode "overlay ouvert" → recapture rapide
        try:
            overlay = getattr(driver, "_ui_overlay_opened", None)
        except Exception:
            overlay = None

        if overlay and overlay.get("type") == "dropdown":
            print(
                "🎯 Dropdown ouvert → recapture immédiate (on saute l'attente/redirection)."
            )
            time.sleep(0.3)  # laisser la liste se peindre
            continue  # on relance une itération : GPT verra la liste OUVERTE

        # b) Attente chargement page avant d'inspecter le DOM (proxy lent en prod)
        redirect_watcher.wait_for_page_load(driver, timeout=30)
        time.sleep(0.3)  # laisser le framework JS réagir post-load

        # c) Attente ADAPTATIVE après action
        #    - Si une action vient de réussir et qu'il reste des choses à faire sur la page,
        #      on NE bloque PAS sur une redirection (les surveys exigent souvent plusieurs entrées).
        try:
            just_succeeded = bool(
                getattr(driver, "last_action_success", False) or success
            )
        except Exception:
            just_succeeded = bool(success)

        # y a-t-il encore des éléments actionnables visibles ?
        has_more_to_do = _has_actionable_elements(driver)

        if just_succeeded and has_more_to_do:
            # -------------------------------------------------------------------
            # Stuck detection — pages multi-inputs (3 niveaux)
            # Niveau 2 : nombre de questions identique
            # Niveau 3 : textes des questions identiques
            # Niveau 4 : états des inputs (radio/checkbox/select) identiques
            # → si les 3 niveaux sont inchangés, la page n'a pas progressé
            # -------------------------------------------------------------------
            _cur_multi_state = _get_multi_page_state(driver)
            if _last_multi_page_state is not None:
                _prev_n_q, _prev_q_texts, _prev_inputs = _last_multi_page_state
                _cur_n_q,  _cur_q_texts,  _cur_inputs  = _cur_multi_state
                if _cur_n_q == _prev_n_q:                          # niveau 2
                    if _cur_q_texts == _prev_q_texts:              # niveau 3
                        if _cur_inputs == _prev_inputs:            # niveau 4
                            _multi_no_progress_count += 1
                            log_debug("STUCK-MULTI",
                                f"Aucune progression ({_multi_no_progress_count}/{_NO_PROGRESS_THRESHOLD})"
                                f" — q={_cur_n_q} textes={_cur_q_texts} inputs={_cur_inputs}")
                            if _multi_no_progress_count >= _NO_PROGRESS_THRESHOLD:
                                log_info("STUCK-MULTI",
                                    f"Page multi-inputs inchangée {_NO_PROGRESS_THRESHOLD} fois → soft-restart")
                                guard.record_success()
                                guard.request_survey_restart("solve_no_progress_multi")
                                return
                        else:
                            log_debug("STUCK-MULTI", f"Progression : états inputs modifiés {_prev_inputs} → {_cur_inputs}")
                            _multi_no_progress_count = 0
                    else:
                        log_debug("STUCK-MULTI", f"Progression : textes modifiés ({_prev_n_q}q → {_cur_n_q}q)")
                        _multi_no_progress_count = 0
                else:
                    log_debug("STUCK-MULTI", f"Progression : nb questions modifié {_prev_n_q} → {_cur_n_q}")
                    _multi_no_progress_count = 0
            _last_multi_page_state = _cur_multi_state
            print(" Action en-page réussie et autres éléments visibles → pas d'attente de navigation.")
            redirect_watcher.wait_for_page_load(driver, timeout=10)
            time.sleep(0.4)  # laisser le framework réagir
            # on repart tout de suite sur une nouvelle itération (nouvelle capture)
            continue

        # Sinon, il y a peut-être une navigation.
        # L’executor a déjà attendu jusqu’à 10s via wait_for_navigation_or_dom_change ;
        # une vérification immédiate de l’URL évite un wait_for_final_redirection inutile
        # sur les surveys SPA (DOM-only, URL stable entre les pages).
        _check_url = driver.current_url

        if _check_url != last_url:
            # URL déjà changée → attendre la stabilisation finale (redirections chaînées possibles)
            maxw = 3 if just_succeeded else 8
            stabilized_url = redirect_watcher.wait_for_final_redirection(driver, max_wait=maxw)
            current_url = stabilized_url or _check_url
        else:
            # URL inchangée : navigation SPA probable (DOM-only).
            # Pas de latence proxy à absorber → on saute wait_for_final_redirection (5s+ de polling).
            # Fenêtre de sécurité minimale pour les redirects asynchrones tardifs.
            time.sleep(0.5)
            current_url = driver.current_url

        # Si l’URL a changé → on inspecte le nouvel emplacement
        if current_url != last_url:
            _no_progress_count = 0         # URL a changé, réinitialisation du détecteur stuck
            _multi_no_progress_count = 0   # Reset stuck multi-inputs
            _last_multi_page_state = None  # Reset empreinte multi-inputs
            last_question_key = ""         # Reset aussi la clé question
            _cta_fail_count = 0            # Reset failure pipeline CTA counter
            print(f"[solve_full_survey] Changement d’URL {last_url} \u2192 {current_url}")
            last_url = current_url

            # Attendre que la nouvelle page soit pleinement chargée (proxy lent en prod)
            redirect_watcher.wait_for_page_load(driver, timeout=30)
            time.sleep(PAUSE_POST_CTA_NAV)  # absorbe la latence proxy avant d’interagir avec la page suivante

            # Retour TopSurveys ? Traite popup ‘Complète’ ou disqualification, puis relance.
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
            # Extraire la question courante pour détecter les changements intra-page
            try:
                import preselection.question_analyzer as _qa
                _html = _qa.extract_popup_html(driver)
                _current_q = (_qa.extract_question_text(_html) or "")[:150]
            except Exception:
                _current_q = ""

            if _current_q and _current_q != last_question_key:
                # La question a changé → progression réelle malgré URL identique
                print(f"[solve_full_survey] Question changée sans changement d'URL → progression détectée")
                last_question_key = _current_q
                _no_progress_count = 0
            else:
                _no_progress_count += 1
                if _no_progress_count >= _NO_PROGRESS_THRESHOLD:
                    print(f"[STUCK] Réponse acceptée {_NO_PROGRESS_THRESHOLD} fois sans avance → soft-restart")
                    guard.record_success()
                    guard.request_survey_restart("solve_no_progress")
                    return
        else:
            _no_progress_count = 0
            try:
                import preselection.question_analyzer as _qa
                _html = _qa.extract_popup_html(driver)
                last_question_key = (_qa.extract_question_text(_html) or "")[:150]
            except Exception:
                last_question_key = ""

        # d) Conditions d’arrêt
        # if _looks_like_end_screen(driver):
            # print(" Écran de fin détecté. Fin du survey.")
            # break

        # Heuristique : si aucune actionnable visible MAIS on vient de réussir une action,
        # on laisse 1 tour de plus au DOM pour apparaître (évite l’arrêt prématuré).
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
                    " Pas encore d’élément actionnable, mais action réussie à l’étape précédente. On continue."
                )
                # petit dlai de grce
                time.sleep(1.0)
                continue

            print("[solve_full_survey] Aucun élément actionnable → survey terminé, soft-restart.")
            guard.record_success()
            guard.request_survey_restart("survey_end")
            return

        # # e) Si l’URL n’évolue pas ET l’action a échoué 2 fois d’affilée, on sort (sécurité douce)
        # if current_url == last_url and success is False:
        #     print("⚠ Ni changement d’URL ni action réussie. Nouvelle tentative…")
        #     # on laisse encore 1 tour; si ça persiste, la condition ci‑dessus arrêtera.
        #     # --- FAILURE PIPELINE : injection point 3 (clic_cta) — attach uniquement ---
        #     _cta_fail_count += 1
        #     if _cta_fail_count >= 2:
        #         try:
        #             from config import is_attach_mode as _fp_is_attach
        #             if _fp_is_attach():
        #                 import Survey.page_snapshot as _fp_ps
        #                 _fp_snap = _fp_ps.snapshot_if_enabled(driver, reason="failure_pipeline")
        #                 if not _fp_snap:
        #                     _fp_snap = _fp_ps.dump_page_snapshot(driver, reason="failure_pipeline")
        #                 if _fp_snap:
        #                     from tools.failure_pipeline import trigger_pipeline as _fp_trigger
        #                     _fp_trigger(_fp_snap, step="clic_cta")
        #         except Exception as _fp_e:
        #             print(f"[FAILURE_PIPELINE] Erreur non-bloquante (clic_cta) : {_fp_e}")
        #     # --- FIN injection point 3 ---
        # else:
        #     _cta_fail_count = 0
        last_url = current_url
        # Non-blocking: triggers async summary generation every N pages
        try:
            _survey_ctx.maybe_update_summary()
        except Exception:
            pass
