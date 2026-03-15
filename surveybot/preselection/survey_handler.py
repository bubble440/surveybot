import time, os

IS_LOCAL = os.getenv("RUN_ENV", "local") == "local"

import preselection.response_executor
if not IS_LOCAL:
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
from preselection.auth_handler import snap
from selenium.webdriver.common.by import By
from preselection.question_validation import detect_disqualification_reason
from State.daily_target import DAILY_TARGET_EUR


def _safe_page_text(driver) -> str:
    """Récupère un texte exploitable pour les détecteurs (robuste)."""
    try:
        return driver.find_element(By.TAG_NAME, "body").text or ""
    except Exception:
        try:
            return driver.page_source or ""
        except Exception:
            return ""


def is_topsurveys_preselection_popup(driver) -> bool:
    """Détection DOM minimale d'un popup de présélection TopSurveys déjà affiché."""
    try:
        return bool(driver.execute_script("""
            try {
              const inTopSurveys = /(^|\.)topsurveys\.app$/i.test(location.hostname || '')
                || /(^|\.)topsurveys\.com$/i.test(location.hostname || '');
              if (!inTopSurveys) return false;

              const hasActions = !!document.querySelector(
                "button[data-test-id='ps-common-actions-button'], button[data-test-id='ps-skip-question-button']"
              );
              const hasQualificationTitle = Array.from(document.querySelectorAll('h1,h2,h3,div,span,p'))
                .some(el => /qualification/i.test((el.innerText || '').trim()));
              const hasQuestionPattern = !!document.querySelector(
                "input[placeholder*='Recherche'], [role='dialog'], [data-test-id*='ps-']"
              );

              return !!(hasActions && (hasQualificationTitle || hasQuestionPattern));
            } catch(e) {
              return false;
            }
        """))
    except Exception:
        return False


def run_attach_preselection_takeover(
    driver,
    api_key: str,
    *,
    max_rounds: int = 15,
    transition_timeout_s: int = 45,
    ctx=None,
) -> tuple[bool, str]:
    """
    Route attach préselection: prend la main sur le popup TopSurveys déjà ouvert,
    répond jusqu'à la redirection vers un survey externe, sans navigation 'best survey'.
    """
    import preselection.question_analyzer
    import preselection.response_executor

    if not is_topsurveys_preselection_popup(driver):
        return False, "popup_not_detected"

    print(f"[ATTACH][PRESEL] takeover start (max_rounds={max_rounds}, timeout={transition_timeout_s}s)")

    for round_idx in range(1, max_rounds + 1):
        if not is_topsurveys_preselection_popup(driver):
            print("[ATTACH][PRESEL] popup non détecté -> transition considérée réussie")
            return True, "transition_detected"

        question, answer = preselection.question_analyzer.get_response_for_question(driver, api_key)

        if isinstance(answer, dict) and answer.get("action"):
            action = (answer.get("action") or "").upper()
            if action == "SKIP":
                decline_labels = [
                    "Je ne peux pas répondre à cette question",
                    "Je ne peux pas répondre",
                    "Je préfère ne pas répondre",
                    "Prefer not to answer",
                    "I prefer not to answer",
                ]
                skipped = False
                for lab in decline_labels:
                    try:
                        if preselection.response_executor.execute_response(driver, lab):
                            skipped = True
                            break
                    except Exception:
                        continue
                if not skipped:
                    try:
                        skip_btn = driver.find_element(By.CSS_SELECTOR, "button[data-test-id='ps-skip-question-button']")
                        driver.execute_script("arguments[0].click();", skip_btn)
                        skipped = True
                    except Exception:
                        pass
                if not skipped:
                    return False, "skip_failed"
                time.sleep(1.0)
                continue

            if action == "DISQUALIFIED":
                return False, "disqualified"

            if action == "NOT_RETURNED":
                pass
            else:
                return False, f"unsupported_action_{action.lower()}"

        elif question and answer:
            success = preselection.response_executor.execute_response(driver, answer)
            if not success:
                return False, "answer_execution_failed"
            if ctx is not None:
                ctx.record(question, [], answer)
            time.sleep(1.2)
        else:
            # pas de question/réponse: on tente la phase de participation
            pass

        try:
            base_handles = set(driver.window_handles)
        except Exception:
            base_handles = set()

        transitioned = False
        try:
            if preselection.question_analyzer.click_participer_if_qualified(driver):
                transitioned = True
        except Exception:
            transitioned = False

        if transitioned:
            try:
                import Management.redirect_watcher
                Management.redirect_watcher.switch_to_latest_window_and_close_others(
                    driver,
                    base_handles=base_handles,
                    timeout=min(12, transition_timeout_s),
                    prefer_external=True,
                )
                Management.redirect_watcher.wait_for_final_redirection(driver, max_wait=transition_timeout_s)
            except Exception:
                pass

            if not is_topsurveys_preselection_popup(driver):
                return True, "qualified_transition"

        print(f"[ATTACH][PRESEL] round={round_idx}/{max_rounds} encore sur préselection")

    return False, "max_rounds_reached"


def run_survey(driver, api_key, *, account_id: str, ctx=None):
    import preselection.question_analyzer
    import preselection.response_executor
    import Survey.survey_solver 
    import Cash.payout as payout
    import Management.guards.runtime_guard
    import Management.guards.survey_difficulty_guard
    import Management.redirect_watcher
    import Management.guards.url_guard
    import launch
    
    def _restart(reason: str) -> None:
        """
        Redémarrage robuste, compatible local/prod.
        - Si un vrai RuntimeGuard est initialisé avec on_soft_restart -> on délègue.
        - Sinon (local typiquement) -> soft_restart direct avec un ctx minimal.
        """
        g = Management.guards.runtime_guard.get_guard()

        # ✅ 1) Délégation au RuntimeGuard si réellement armé
        try:
            if getattr(g, "on_soft_restart", None):
                g.request_survey_restart(reason)
                return
        except Exception as e:
            print(f"[RESTART][WARN] Délégation RuntimeGuard échouée: {e}")

        # ✅ 2) Fallback local/dev : soft_restart direct
        try:
            ctx = {
                "account_id": account_id,
                "api_key": api_key,
                # payout_* optionnels (soft_restart_payout doit être tolérant)
                "payout_name": "",
                "payout_revolut_tag": "",
            }
            launch.soft_restart(ctx, driver, reason)
        except Exception as e:
            print(f"[RESTART][FATAL] soft_restart fallback échoué: {e}")

    snap(driver, "before_survey_loop")
    _STUCK_THRESHOLD = 5
    _last_scan_key = None
    _same_scan_count = 0
    try:
        while True:
            # =================================================================
            # CAPTCHA: Résolution automatique si captcha détecté (no-op sinon)
            # =================================================================
            try:
                from captcha.normal_captcha import handle_captcha
                if handle_captcha(driver):
                    print("[CAPTCHA] Captcha préselection traité → relance boucle")
                    _last_scan_key = None
                    _same_scan_count = 0
                    continue
            except Exception as _cap_exc:
                print(f"[CAPTCHA][WARN] {_cap_exc}")

            question, answer = preselection.question_analyzer.get_response_for_question(driver, api_key)

            # =================================================================
            # STUCK DETECTION: même page scannée N fois → soft-restart
            # =================================================================
            try:
                _cur_url = driver.current_url
            except Exception:
                _cur_url = ""
            _scan_key = (_cur_url, str(question)[:150] if question else "")
            if _scan_key == _last_scan_key:
                _same_scan_count += 1
            else:
                _last_scan_key = _scan_key
                _same_scan_count = 1
            if _same_scan_count >= _STUCK_THRESHOLD:
                print(f"[STUCK] Même page scannée {_STUCK_THRESHOLD} fois → soft-restart")
                _restart("same_page_stuck")
                return

            # ✅ 1) Actions de contrôle renvoyées par l'analyzer.
            # Important: ces actions ne doivent JAMAIS arriver dans execute_response().
            if isinstance(answer, dict) and answer.get("action"):
                action = (answer.get("action") or "").upper()

                # 🎛️ Cas : question sensible → décliner (option radio) ou bouton skip
                if action == "SKIP":
                    print("🚫 Question sensible → tentative 'Je ne peux pas répondre' (option) puis bouton skip")

                    try:
                        # 1) Beaucoup d'écrans TopSurveys mettent "Je ne peux pas répondre" comme OPTION (radio),
                        # pas comme bouton dédié. On essaye donc via le response_executor d'abord.
                        decline_labels = [
                            "Je ne peux pas répondre à cette question",
                            "Je ne peux pas répondre",
                            "Je préfère ne pas répondre",
                            "Prefer not to answer",
                            "I prefer not to answer",
                        ]

                        for lab in decline_labels:
                            try:
                                if preselection.response_executor.execute_response(driver, lab):
                                    Management.guards.runtime_guard.get_guard().record_success()
                                    time.sleep(1.2)
                                    # ✅ on revient à la boucle des questions
                                    break
                            except Exception:
                                pass
                        else:
                            # 2) Sinon, fallback bouton skip TopSurveys (quand il existe)
                            skip_btn = driver.find_element(
                                By.CSS_SELECTOR,
                                "button[data-test-id='ps-skip-question-button']"
                            )
                            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", skip_btn)
                            time.sleep(0.2)
                            try:
                                skip_btn.click()
                            except Exception:
                                driver.execute_script("arguments[0].click();", skip_btn)

                            Management.guards.runtime_guard.get_guard().record_success()
                            time.sleep(1.2)

                        continue  # 🔁 revenir à la boucle des questions

                    except Exception as e:
                        Management.guards.runtime_guard.get_guard().record_error(e)
                        print("❌ Impossible de décliner/skip la question :", e)
                        _restart("sensitive_question_skip_failed")
                        return

                # ❌ Cas : disqualification détectée par la validation
                if action == "DISQUALIFIED":
                    print(f"⚠️ Disqualification détectée (validator) | reason={answer.get('reason')}")
                    preselection.question_analyzer.handle_disqualification_and_retry(driver)
                    time.sleep(1.5)
                    _restart("preselection_disqualified")
                    return

                # ℹ️ Cas : pas une vraie question (ex: écran 'Soumettre')
                if action == "NOT_RETURNED":
                    print("ℹ️ Écran non-question détecté (ex: 'Soumettre') → tentative de passer à l'étape suivante.")
                    # On force la logique 'Participer / Ok' plus bas
                    question, answer = None, None

                else:
                    # Toute autre action inconnue → restart (fallback généraliste)
                    print(f"⚠️ Action préselection inconnue: {action} → restart")
                    _restart(f"preselection_action_{action.lower()}")
                    return
                
            # ✅ Disqualification : détection centralisée (robuste)
            dq_reason = detect_disqualification_reason(question, _safe_page_text(driver))
            if dq_reason:
                print(f"⚠️ Disqualification détectée (reason={dq_reason}).")
                # best-effort: fermer popup si présent
                try:
                    preselection.question_analyzer.handle_disqualification_and_retry(driver)
                except Exception:
                    pass
                time.sleep(1.2)
                _restart("preselection_disqualified")
                return


            # Cas normal : une réponse est attendue
            if question and answer:
                success = preselection.response_executor.execute_response(driver, answer)
                if success and ctx is not None:
                    ctx.record(question, [], answer)
                #save_question_result(question, answer, input_type, success=success, choices=options, context="preselection")
                time.sleep(2)
                # 🔄 Si l'action échoue, relancer un survey complet
                if not success:
                    print("⚠️ Réponse non appliquée correctement, relance du survey...")
                    try:
                        payout.check_and_cashout_if_needed(
                            driver,
                            account_id=account_id,
                            min_amount_eur=DAILY_TARGET_EUR,
                            cashout_order=("revolut", "paypal"),
                            revolut_fullname="Wilfred Jamein Saah",
                            revolut_tag="@jameinsaah",
                        )
                        Management.guards.runtime_guard.get_guard().record_success()
                    except Exception as e:
                        Management.guards.runtime_guard.get_guard().record_error(e)
                        print(f"[PAYOUT][WARN] Encaissement automatique: {e}")
                    _restart("disqualification_or_retry")
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
                            _restart("disqualification_or_retry")
                            return

                        if not Management.guards.url_guard.is_allowed(final_url):
                            print(f"[URL_GUARD] Bloqué : {final_url} — tentative de retour propre via l'app")
                            Management.guards.runtime_guard.get_guard().record_error(RuntimeError(f"url_guard_blocked: {final_url}"))
                            # 🧠 Délégation complète au RuntimeGuard
                            _restart("url_guard_blocked")
                            return

                        print(f"[URL_GUARD] Autorisé : {final_url} (host: {host})")
                        # feu vert → on entre en résolution complète
                        Survey.survey_solver.solve_full_survey(
                            driver,
                            api_key=api_key,
                            account_id=account_id,
                            survey_context=ctx,
                        )
                        return

                    # Cas : on est disqualifié → cliquer sur OK puis relancer
                    if preselection.question_analyzer.handle_disqualification_and_retry(driver):
                        print("⚠️ Disqualification détectée après question finale.")
                        time.sleep(2)
                        Management.guards.runtime_guard.get_guard().record_success()
                        _restart("disqualification_or_retry")
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
