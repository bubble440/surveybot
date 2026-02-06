# survey_solver.py
# Orchestration minimaliste et robuste pour enchaîner les actions de page
# ➜ Laisse l’intelligence d’action à survey_executor.execute_survey_page()

from selenium.webdriver.support.ui import WebDriverWait  # [AJOUT]
from selenium.webdriver.support import expected_conditions as EC  # [AJOUT]
from selenium.webdriver.common.action_chains import ActionChains  # [AJOUT]
from selenium.webdriver.common.by import By
import time, os, sys
from preselection.question_validation import detect_disqualification_reason

# ⚙️ Paramètres de boucle pour éviter les boucles infinies
# NOTE: à l'échelle 100+ bots, il faut des caps qui ne se reset jamais.
MAX_TOTAL_STEPS = 200  # sécurité dure : itérations TOTALES (ne se reset jamais)
MAX_STEPS_PER_URL = 80  # sécurité : évite de tourner en rond sur la même URL
MAX_URL_CHANGES = 60  # sécurité : évite les ping-pongs de redirection
STABILIZE_SLEEP = 2.0  # délai court entre deux actions pour laisser le DOM respirer


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
    print("⚠️ Aucun onglet externe détecté. Reste sur TopSurveys.")
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
    Détecte le popup 'Tu as partiellement répondu...' et clique sur 'Complète'.
    Retourne True s'il a été traité.
    """
    txt = _page_text_lc(driver)
    if ("tu as partiellement répondu au sondage" in txt and
        ("l'annonceur cherchait quelqu'un d'autre" in txt or "l'annonceur cherchait quelqu un d autre" in txt)):
        print("🧩 Popup 'partiellement répondu' détecté (TopSurveys).")
        # on nettoie les onglets maintenant
        _close_other_tabs_in_current_session(driver)

        # bouton 'Complète' (ou 'Complete') - plusieurs fallbacks
        btn = None
        try:
            btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-test-id='ps-common-actions-button']"))
            )
        except Exception:
            pass
        if not btn:
            # fallback sur le libellé (span inside button)
            try:
                xp = ("//button[.//span[contains(translate(normalize-space(.),"
                      "'ABCDEFGHIJKLMNOPQRSTUVWXYZÀÂÄÇÉÈÊËÎÏÔÖÙÛÜŸ','abcdefghijklmnopqrstuvwxyzàâäçéèêëîïôöùûüÿ'),"
                      "'complète')] or contains(translate(normalize-space(.),"
                      "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'complete')]")
                btn = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, xp)))
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
                    print("✅ Bouton 'Complète' cliqué.")
                    return True
                except Exception:
                    continue

        print("⚠️ Bouton 'Complète' introuvable/incliquable.")
    return False

def _if_on_topsurveys_handle(driver, api_key, account_id) -> bool:
    """
    Si on est sur app.topsurveys.app :
      - traite le popup 'partiellement répondu' (ferme autres onglets + 'Complète' + relance)
      - sinon, vérifie la disqualification (ferme autres onglets + relance)
    Retourne True si on a *orchestré un retour* vers run_survey().
    """
    url = (driver.current_url or "").lower()
    if "topsurveys.app" not in url:
        return False

    # Cas A : popup partiel -> Complète + relance
    if _handle_topsurveys_partial_popup(driver):
        try:
            import preselection.survey_navigator
            import preselection.survey_handler 
            time.sleep(1.0)
            preselection.survey_navigator.go_to_best_paid_survey(driver)
            preselection.survey_handler.run_survey(driver, api_key, account_id=account_id)
            return True
        except Exception as e:
            print("💥 Erreur relance après 'Complète' :", e)
            return False

    # Cas B : check disqualification puis relance si besoin
    try:
        # ✅ Détection disqualification centralisée (robuste)
        page_txt = _page_text_lc(driver)
        dq_reason = detect_disqualification_reason("", page_txt)
        if dq_reason:
            print(f"⚠️ Disqualification TopSurveys détectée (reason={dq_reason}).")

            # best-effort : ferme le popup si présent (mais la détection ne dépend plus de ça)
            try:
                import preselection.question_analyzer
                preselection.question_analyzer.handle_disqualification_and_retry(driver)
            except Exception as e:
                print("⚠️ Popup disqualification détecté mais fermeture 'Ok' a échoué:", e)

            _close_other_tabs_in_current_session(driver)
            import preselection.survey_navigator
            import preselection.survey_handler
            time.sleep(0.7)
            preselection.survey_navigator.go_to_best_paid_survey(driver)
            preselection.survey_handler.run_survey(driver, api_key, account_id=account_id)
            return True
    except Exception as e:
        print("💥 Erreur check disqualification TopSurveys :", e)

    return False

def solve_full_survey(driver, api_key, *, account_id: str):
    import Management.redirect_watcher as redirect_watcher
    import Survey.survey_executor  
    import Management.guards.survey_difficulty_guard
    import Management.guards.runtime_guard

    """
    Boucle principale de résolution du survey.
    1) Bascule vers l’onglet externe + stabilisation d’URL
    2) Répète : execute_survey_page() ➜ petite pause ➜ test si on continue
    On sort si :
      - plus rien d’actionnable détecté
      - écran de fin détecté
      - seuil MAX_TOTAL_STEPS atteint (sécurité)
    """
    print("🧪 [solve_full_survey] Début de traitement du survey...")
    # 🔁 Sécurité : si plusieurs onglets existent, on prend le dernier
    try:
        if len(driver.window_handles) > 1:
            driver.switch_to.window(driver.window_handles[-1])
            print(f"🧭 Focus forcé sur l’onglet actif : {driver.current_url}")
    except Exception as e:
        print("⚠️ Impossible de forcer le focus onglet :", e)


    _switch_to_external_tab(driver)

    # 1) Attendre que la redirection s’arrête sur une URL stable
    final_url = redirect_watcher.wait_for_final_redirection(driver)
    print(f"🌐 URL finale stabilisée : {final_url}")

    # 2) Boucle d’exécution des actions
    steps_total = 0
    steps_on_url = 0
    url_changes = 0
    last_url = driver.current_url

    while steps_total < MAX_TOTAL_STEPS:
        steps_total += 1
        steps_on_url += 1
        print(
            f"🔁 Étape {steps_total}/{MAX_TOTAL_STEPS} "
            f"(page {steps_on_url}/{MAX_STEPS_PER_URL}) — exécution de la page courante"
        )

        # sécurité : si on stagne sur la même URL, on sort plutôt que de brûler du budget
        if steps_on_url > MAX_STEPS_PER_URL:
            print(
                f"🛑 Trop d'itérations sur la même URL ({MAX_STEPS_PER_URL}). "
                f"Stop pour éviter une boucle infinie. URL={last_url}"
            )
            break

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
                run_env = (os.getenv("RUN_ENV", "local") or "").strip().lower()
                
                # PROD/DOCKER : arrêt contrôlé immédiat (inchangé)
                if run_env != "local":
                    print(f"[STRICT_SURVEY][MID] Détecté en cours de survey (captcha) -> restart propre")
                    Management.guards.runtime_guard.get_guard().record_success()
                    Management.guards.runtime_guard.get_guard().signal_strict_survey(f"strict_mid_captcha")
                    return
                
                # LOCAL : pause manuelle pour résolution utilisateur
                print("[LOCAL][CAPTCHA] ⚠️  CAPTCHA détecté → résolution MANUELLE requise")
                
                # Anti-boucle : ne pas mettre en pause plusieurs fois sur la même URL
                try:
                    captcha_url = driver.current_url or ""
                    last_captcha_url = getattr(driver, "_last_captcha_pause_url", None)
                    if last_captcha_url == captcha_url:
                        print("[LOCAL][CAPTCHA] ⏭️  Captcha déjà traité sur cette URL, on continue")
                        # On continue l'exécution normale sans repause
                    else:
                        # Marquer cette URL comme traitée
                        setattr(driver, "_last_captcha_pause_url", captcha_url)
                        
                        # Pause interactive si terminal disponible
                        if getattr(sys.stdin, "isatty", lambda: False)():
                            try:
                                input("[LOCAL][PAUSE] 🧩 Résous le CAPTCHA dans le navigateur, puis appuie sur Entrée...\n")
                            except KeyboardInterrupt:
                                print("[LOCAL] ⏹️  Abandon demandé par l'utilisateur")
                                Management.guards.runtime_guard.get_guard().record_success()
                                Management.guards.runtime_guard.get_guard().signal_strict_survey("captcha_user_abort")
                                return
                        else:
                            print("[LOCAL][CAPTCHA] ⚠️  Terminal non-interactif, pas de pause possible")
                            Management.guards.runtime_guard.get_guard().record_success()
                            Management.guards.runtime_guard.get_guard().signal_strict_survey("captcha_no_tty")
                            return
                        
                        # Vérification : attendre que le captcha disparaisse (max 30s)
                        print("[LOCAL][CAPTCHA] 🔍 Vérification de la disparition du captcha...")
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
                            print("[LOCAL][CAPTCHA] ⏱️  Timeout : captcha toujours présent après 30s")
                            Management.guards.runtime_guard.get_guard().record_success()
                            Management.guards.runtime_guard.get_guard().signal_strict_survey("captcha_timeout")
                            return
                        
                        # Captcha résolu avec succès : on continue la boucle normale
                        print("[LOCAL][CAPTCHA] 🚀 Reprise de l'exécution du survey")
                except Exception as e:
                    print(f"[LOCAL][CAPTCHA] ❌ Erreur lors de la gestion du captcha : {e}")
                    Management.guards.runtime_guard.get_guard().record_success()
                    Management.guards.runtime_guard.get_guard().signal_strict_survey("captcha_error")
                    return
            
            # ⚠️ AUTRES RAISONS (drag_drop, hold_button, etc.) : arrêt immédiat (inchangé)
            else:
                print(f"[STRICT_SURVEY][MID] Détecté en cours de survey ({reason}) -> restart propre")
                Management.guards.runtime_guard.get_guard().record_success()
                Management.guards.runtime_guard.get_guard().signal_strict_survey(f"strict_mid_{reason}")
                return        
        
        # -------------------------------------------------------------------
        # a) Laisser GPT décider de l’action à partir de la capture d’écran
        success = Survey.survey_executor.execute_survey_page(driver, api_key)

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

        # b) Micro-pause pour laisser le DOM respirer
        time.sleep(STABILIZE_SLEEP)

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
            print(
                "⏭️ Action en-page réussie et autres éléments visibles → pas d'attente de navigation."
            )
            time.sleep(0.4)  # laisser le framework réagir
            # on repart tout de suite sur une nouvelle itération (nouvelle capture)
            continue

        # Sinon, il y a peut-être une navigation : stabilisation courte si succès, sinon normale
        maxw = 3 if just_succeeded else 8
        stabilized_url = redirect_watcher.wait_for_final_redirection(driver, max_wait=maxw)
        current_url = stabilized_url or driver.current_url

        # Si l’URL a changé → on inspecte le nouvel emplacement
        if current_url != last_url:
            url_changes += 1
            steps_on_url = 0  # ✅ reset PER-URL seulement (le cap total ne se reset jamais)
            print(
                f"🔀 Changement d’URL détecté ({url_changes}/{MAX_URL_CHANGES})\n"
                f"   {last_url} → {current_url}"
            )

            if url_changes > MAX_URL_CHANGES:
                print(
                    f"🛑 Trop de changements d'URL ({MAX_URL_CHANGES}). "
                    f"Stop pour éviter un ping-pong de redirection."
                )
                break

            last_url = current_url

            # [NEW] Retour TopSurveys ? Traite popup 'Complète' ou disqualification, puis relance.
            try:
                if _if_on_topsurveys_handle(driver, api_key, account_id):
                    print("↩️ Retour orchestré vers la pré-sélection depuis TopSurveys. Arrêt de solve_full_survey().")
                    return  # on laisse run_survey() reprendre la main
            except Exception as e:
                print("💥 Hook TopSurveys a échoué :", e)

            # sinon on repart sur la prochaine itération (nouvelle page)
            continue


        # d) Conditions d’arrêt
        # if _looks_like_end_screen(driver):
            # print("🏁 Écran de fin détecté. Fin du survey.")
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
                    "⏳ Pas encore d’élément actionnable, mais action réussie à l’étape précédente. On continue."
                )
                # petit délai de grâce
                time.sleep(1.0)
                continue

            print("ℹ️ Aucun élément actionnable détecté. Arrêt de la boucle.")
            break

        # e) Si l’URL n’évolue pas ET l’action a échoué 2 fois d’affilée, on sort (sécurité douce)
        if current_url == last_url and success is False:
            print("⚠️ Ni changement d’URL ni action réussie. Nouvelle tentative…")
            # on laisse encore 1 tour; si ça persiste, la condition ci‑dessus arrêtera.
        last_url = current_url

    print("✅ [solve_full_survey] Survey traité.")
