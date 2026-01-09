import time, os

IS_LOCAL = os.getenv("RUN_ENV", "local") == "local"

import preselection.response_executor
if not IS_LOCAL:
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.common.by import By
from preselection.auth_handler import snap

def run_survey(driver, api_key, *, account_id: str):
    import preselection.question_analyzer
    import Survey.survey_solver 
    import Cash.payout as payout
    import Management.guards.runtime_guard
    import Management.guards.survey_difficulty_guard
    import Management.redirect_watcher
    import Management.guards.url_guard
    

    snap(driver, "before_survey_loop")
    try:
        while True:
            question, answer= preselection.question_analyzer.get_response_for_question(driver, api_key)

            # 🎛️ Cas : question sensible → cliquer sur "Je ne peux pas répondre"
            if isinstance(answer, dict) and answer.get("action") == "SKIP":
                print("🚫 Question sensible → clic sur 'Je ne peux pas répondre'")

                try:
                    skip_btn = driver.find_element(
                        By.CSS_SELECTOR,
                        "button[data-test-id='ps-skip-question-button']"
                    )
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center'});", skip_btn
                    )
                    time.sleep(0.3)
                    driver.execute_script("arguments[0].click();", skip_btn)

                    Management.guards.runtime_guard.get_guard().record_success()
                    time.sleep(1.5)
                    continue  # 🔁 revenir à la boucle des questions

                except Exception as e:
                    Management.guards.runtime_guard.get_guard().record_error(e)
                    print("❌ Impossible de cliquer sur 'Je ne peux pas répondre' :", e)
                    Management.guards.runtime_guard.get_guard().request_survey_restart("sensitive_question_skip_failed")
                    return

            # Cas : on est disqualifié → cliquer sur OK puis relancer
            if question and "Tu n'as pas été qualifié cette fois" in question:
                print("⚠️ Disqualification détectée, raison: tu n'as pas été qualifié cette fois.")
                preselection.question_analyzer.handle_disqualification_and_retry(driver)
                time.sleep(2)
                try:
                    payout.check_and_cashout_if_needed(
                        driver,
                        account_id=account_id,
                        min_amount_eur=5.0,
                        cashout_order=("paypal", "revolut"),
                        revolut_fullname="Wilfred Jamein Saah",
                        revolut_tag="@jameinsaah",
                    )
                    Management.guards.runtime_guard.get_guard().record_success()
                except Exception as e:
                    Management.guards.runtime_guard.get_guard().record_error(e)
                    print(f"[PAYOUT][WARN] Encaissement automatique: {e}")
                if IS_LOCAL:
                    print("⏹️ Environnement local, relance d'un nouveau survey après disqualification.")
                    return
                else:
                    Management.guards.runtime_guard.get_guard().request_survey_restart("disqualification_or_retry")
                return
            
            # Cas normal : une réponse est attendue
            if question and answer:
                success = preselection.response_executor.execute_response(driver, answer)
                #save_question_result(question, answer, input_type, success=success, choices=options, context="preselection")
                time.sleep(2)
                # 🔄 Si l'action échoue, relancer un survey complet
                if not success:
                    print("⚠️ Réponse non appliquée correctement, relance du survey...")
                    try:
                        payout.check_and_cashout_if_needed(
                            driver,
                            account_id=account_id,
                            min_amount_eur=5.0,
                            cashout_order=("paypal", "revolut"),
                            revolut_fullname="Wilfred Jamein Saah",
                            revolut_tag="@jameinsaah",
                        )
                        Management.guards.runtime_guard.get_guard().record_success()
                    except Exception as e:
                        Management.guards.runtime_guard.get_guard().record_error(e)
                        print(f"[PAYOUT][WARN] Encaissement automatique: {e}")
                    Management.guards.runtime_guard.get_guard().request_survey_restart("disqualification_or_retry")
                    return

            else:
                try:
                    # Cas : on est qualifié → lancer solve_full_survey()
                    base_handles = set(driver.window_handles)
                    if preselection.question_analyzer.click_participer_if_qualified(driver):
                        # 🔑 CRUCIAL : bascule vers le nouvel onglet du survey
                        Management.redirect_watcher.switch_to_latest_window_and_close_others(
                            driver,
                            base_handles=base_handles,
                            timeout=10,
                            prefer_external=True
                        )

                        final_url = Management.redirect_watcher.wait_for_final_redirection(driver, max_wait=60)  # déjà présent dans ton repo
                        host = Management.guards.url_guard.normalize_host(final_url)

                        is_strict, reason = Management.guards.survey_difficulty_guard.detect_strict_survey(driver)
                        if is_strict:
                            print(f"[STRICT_SURVEY] Ignoré ({reason}) → retour TopSurveys")
                            Management.guards.runtime_guard.get_guard().record_success()
                            Management.guards.runtime_guard.get_guard().request_survey_restart("disqualification_or_retry")
                            return

                        if not Management.guards.url_guard.is_allowed(final_url):
                            print(f"[URL_GUARD] Bloqué : {final_url} — tentative de retour propre via l'app")
                            Management.guards.runtime_guard.get_guard().record_error(e)
                            # 🧠 Délégation complète au RuntimeGuard
                            Management.guards.runtime_guard.get_guard().request_survey_restart("url_guard_blocked")
                            return

                        print(f"[URL_GUARD] Autorisé : {final_url} (host: {host})")
                        # feu vert → on entre en résolution complète
                        Survey.survey_solver.solve_full_survey(driver, api_key=api_key, account_id=account_id)
                        return

                    # Cas : on est disqualifié → cliquer sur OK puis relancer
                    if preselection.question_analyzer.handle_disqualification_and_retry(driver):
                        print("⚠️ Disqualification détectée après question finale.")
                        time.sleep(2)
                        Management.guards.runtime_guard.get_guard().record_success()
                        Management.guards.runtime_guard.get_guard().request_survey_restart("disqualification_or_retry")
                        return

                    print("ℹ️ Aucun bouton Participer ou Ok détecté. Fin de boucle.")
                    break

                except Exception as e:
                    Management.guards.runtime_guard.get_guard().record_error(e)
                    print(f"❌ Erreur lors du clic sur Participer ou redirection : {e}")
                    break

    except KeyboardInterrupt:
        Management.guards.runtime_guard.get_guard().record_error()
        driver.quit ()
        print("⏹️  Fermeture manuelle détectée.")
    except Exception as e:
        Management.guards.runtime_guard.get_guard().record_error(e)
        driver.quit ()
        print("💥 Erreur en boucle principale :", e)
