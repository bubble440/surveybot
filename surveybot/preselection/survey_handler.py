import time, os, threading
from Cash.payout import _payout_and_check_daily_stop

IS_LOCAL = os.getenv("RUN_ENV", "local") == "local"


def _pw_page(d):
    """Extrait la Page Playwright native depuis un PlaywrightDriverShim ou retourne d tel quel."""
    if hasattr(d, "_page"):
        return d._page
    return d

# FIX-B3: _restart_depth était un global partagé entre threads.
# Un soft_restart peut relancer run_survey() depuis n'importe quel thread
# (ex : on_soft_restart déclenché par le RuntimeGuard en background).
# Avec un compteur global, les profondeurs de threads différents se cumulent
# et peuvent déclencher un arrêt prématuré alors que le thread concerné n'est
# pas en récursion excessive.
# Solution : threading.local() → compteur strictement par thread.
_restart_tl = threading.local()
_MAX_RESTART_DEPTH = 10


def _get_restart_depth() -> int:
    return getattr(_restart_tl, "depth", 0)


def _set_restart_depth(v: int) -> None:
    _restart_tl.depth = v

import preselection.response_executor
if not IS_LOCAL:
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from preselection.question_validation import detect_disqualification_reason
from State.daily_target import DAILY_TARGET_EUR
from Cash.payout import MIN_CASHOUT_EUR


def _safe_page_text(driver) -> str:
    """Récupère un texte exploitable pour les détecteurs (robuste)."""
    try:
        page = _pw_page(driver)
        return page.evaluate("() => document.body ? (document.body.innerText || '') : ''") or ""
    except Exception:
        try:
            return _pw_page(driver).content() or ""
        except Exception:
            return ""


def is_topsurveys_preselection_popup(driver) -> bool:
    """Détection DOM minimale d'un popup de présélection TopSurveys déjà affiché."""
    try:
        page = _pw_page(driver)
        return bool(page.evaluate("""() => {
            try {
              const inTopSurveys = /(^|\.)topsurveys\\.app$/i.test(location.hostname || '')
                || /(^|\.)topsurveys\\.com$/i.test(location.hostname || '');
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
        }"""))
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

    page = _pw_page(driver)

    # FIX popup_not_detected : _wait_for_survey_popup (BLOC 1) attend ps-popup-content-wrapper,
    # mais is_topsurveys_preselection_popup requiert ps-common-actions-button (rendu plus tard
    # par Vue après chargement de la première question). On attend explicitement ce bouton.
    _ACTION_SEL = (
        "button[data-test-id='ps-common-actions-button'], "
        "button[data-test-id='ps-skip-question-button']"
    )
    try:
        page.wait_for_selector(_ACTION_SEL, state="attached", timeout=10_000)
    except Exception:
        pass  # best-effort : on tente la détection quand même

    if not is_topsurveys_preselection_popup(driver):
        return False, "popup_not_detected"

    print(f"[ATTACH][PRESEL] takeover start (max_rounds={max_rounds}, timeout={transition_timeout_s}s)")

    for round_idx in range(1, max_rounds + 1):
        if not is_topsurveys_preselection_popup(driver):
            print("[ATTACH][PRESEL] popup non détecté -> transition considérée réussie")
            return True, "transition_detected"

        question, answer, input_type = preselection.question_analyzer.get_response_for_question(driver, api_key)

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
                        skip_btn = page.query_selector("button[data-test-id='ps-skip-question-button']")
                        if skip_btn:
                            page.evaluate("(el) => el.click()", skip_btn)
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
            success = preselection.response_executor.execute_response(driver, answer, input_type)
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
            except Exception as _e:
                # H4: on logue l'erreur pour permettre le diagnostic — le bot risque
                # d'être sur le mauvais onglet si ce bloc échoue
                print(f"[ATTACH][PRESEL][WARN] Erreur lors du switch/redirect après Participer: {_e}")

            if not is_topsurveys_preselection_popup(driver):
                return True, "qualified_transition"

        print(f"[ATTACH][PRESEL] round={round_idx}/{max_rounds} encore sur préselection")

    return False, "max_rounds_reached"


def run_survey(driver, api_key, *, account_id: str, ctx=None, payout_name: str = "", payout_revolut_tag: str = "", platform=None):
    # FIX-B3: compteur thread-local (voir _restart_tl ci-dessus)
    current_depth = _get_restart_depth() + 1
    _set_restart_depth(current_depth)
    try:
        if current_depth > _MAX_RESTART_DEPTH:
            print(f"[SURVEY][FATAL] Profondeur de redémarrage max atteinte ({_MAX_RESTART_DEPTH}) → arrêt forcé")
            raise SystemExit("max_restart_depth_reached")
        _run_survey_impl(driver, api_key, account_id=account_id, ctx=ctx, payout_name=payout_name, payout_revolut_tag=payout_revolut_tag, platform=platform)
    finally:
        _set_restart_depth(current_depth - 1)


def _run_survey_impl(driver, api_key, *, account_id: str, ctx=None, payout_name: str = "", payout_revolut_tag: str = "", platform=None):
    import preselection.question_analyzer
    import preselection.response_executor
    import Survey.survey_solver
    from Survey.survey_solver import TopSurveysReturn
    import Survey.survey_executor
    import Survey.log_utils
    import Cash.payout as payout
    import Management.guards.runtime_guard
    import Management.guards.survey_difficulty_guard
    import Management.redirect_watcher
    import launch
    from State.survey_memory import SurveySession, flush_disqualified, flush_qualified
    
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
                "payout_name": "",
                "payout_revolut_tag": "",
                "platform": platform
            }
            launch.soft_restart(ctx, driver, reason)
        except Exception as e:
            print(f"[RESTART][FATAL] soft_restart fallback échoué: {e}")

    time.sleep(1)
    _STUCK_THRESHOLD = 5
    _last_scan_key = None
    _same_scan_count = 0
    _card_retry_count = 0
    _MAX_CARD_RETRIES = 20
    _cashout_done = False          # ← ajout
    session = SurveySession()      # Session mémoire inter-bots (locale jusqu'au flush)

    def _skip_card_and_retry(reason: str) -> bool:
        """
        Marque la carte courante comme bloquée et navigue vers la meilleure carte suivante.
        Retourne True si le budget est épuisé (soft restart déclenché → appelant doit `return`),
        False si la navigation a réussi (appelant doit `continue`).
        """
        nonlocal _card_retry_count, _last_scan_key, _same_scan_count
        from preselection.survey_navigator import mark_last_selected_survey_as_blocked, go_to_best_paid_survey
        mark_last_selected_survey_as_blocked()
        _card_retry_count += 1
        if _card_retry_count >= _MAX_CARD_RETRIES:
            print(f"[SURVEY][CARD_RETRY] Budget épuisé ({_MAX_CARD_RETRIES} cartes) → soft restart ({reason})")
            _restart(reason)
            return True
        print(f"[SURVEY][CARD_RETRY] Carte bloquée ({reason}), essai {_card_retry_count}/{_MAX_CARD_RETRIES} → carte suivante")
        _last_scan_key = None
        _same_scan_count = 0
        go_to_best_paid_survey(driver)
        return False

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

            question, answer, input_type = preselection.question_analyzer.get_response_for_question(driver, api_key, session=session)

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
                        if _skip_card_and_retry("sensitive_question_skip_failed"):
                            return
                        continue

                # ❌ Cas : disqualification détectée par la validation
                if action == "DISQUALIFIED":
                    print(f"⚠️ Disqualification détectée (validator) | reason={answer.get('reason')}")
                    if os.getenv("SNAP_ENABLED", "").strip() == "1":
                        from Management.snap_uploader import capture_and_upload
                        capture_and_upload(driver, "disqualified")
                    try:
                        flush_disqualified(session)
                    except Exception:
                        pass
                    session = SurveySession()
                    preselection.question_analyzer.handle_disqualification_and_retry(driver)
                    time.sleep(1.5)
                    from preselection.survey_navigator import go_to_best_value_survey
                    go_to_best_value_survey(driver)
                    continue

                # ℹ️ Cas : pas une vraie question (ex: écran 'Soumettre')
                if action == "NOT_RETURNED":
                    print("ℹ️ Écran non-question détecté (ex: 'Soumettre') → tentative de passer à l'étape suivante.")
                    # On force la logique 'Participer / Ok' plus bas
                    question, answer = None, None

                else:
                    # Toute autre action inconnue → carte bloquée, prochaine carte
                    print(f"⚠️ Action préselection inconnue: {action} → carte suivante")
                    if _skip_card_and_retry(f"preselection_action_{action.lower()}"):
                        return
                    continue
                
            # ✅ Disqualification : détection centralisée (robuste)
            dq_reason = detect_disqualification_reason(question, _safe_page_text(driver))
            if dq_reason:
                print(f"⚠️ Disqualification détectée (reason={dq_reason}).")
                try:
                    flush_disqualified(session)
                except Exception:
                    pass
                session = SurveySession()
                # best-effort: fermer popup si présent
                try:
                    preselection.question_analyzer.handle_disqualification_and_retry(driver)
                except Exception:
                    pass
                time.sleep(1.2)
                from preselection.survey_navigator import go_to_best_value_survey
                go_to_best_value_survey(driver)
                continue


            # Cas normal : une réponse est attendue
            if question and answer:
                success = preselection.response_executor.execute_response(driver, answer, input_type)
                if success and ctx is not None:
                    ctx.record(question, [], answer)
                #save_question_result(question, answer, input_type, success=success, choices=options, context="preselection")
                time.sleep(2)
                # 🔄 Si l'action échoue, relancer un survey complet
                if not success:
                    print("⚠️ Réponse non appliquée correctement, relance du survey...")
                    if not _cashout_done:
                        try:
                            _payout_and_check_daily_stop(driver, account_id, email="")  # retrait + DAILY STOP
                            Management.guards.runtime_guard.get_guard().record_success()
                        except Exception as e:
                            Management.guards.runtime_guard.get_guard().record_error(e)
                            print(f"[PAYOUT][WARN] Encaissement automatique: {e}")
                        _cashout_done = True                       # ← ajout
                    if _skip_card_and_retry("disqualification_or_retry"):
                        return
                    continue
            else:
                try:
                    # Cas : on est qualifié → lancer solve_full_survey()
                    if preselection.question_analyzer.click_participer_if_qualified(driver):
                        # H3: click_participer_if_qualified fait déjà le switch de fenêtre
                        # en interne — ne pas rappeler switch_to_latest_window_and_close_others
                        # ici pour éviter la race condition du double switch.
                        final_url = Management.redirect_watcher.wait_for_final_redirection(driver, max_wait=60)

                        if final_url and "app.topsurveys.app/surveys" in final_url:
                            Survey.log_utils.log_info("SURVEY_HANDLER", "Retour TopSurveys après clic Participer — popup/exclusion attendue")
                            Survey.survey_executor._handle_topsurveys_exclusion_popup(driver, account_id)
                            # if _skip_card_and_retry("topsurveys_redirect"):
                            #     return
                            continue

                        is_strict, reason = Management.guards.survey_difficulty_guard.detect_strict_survey(driver)
                        if is_strict:
                            print(f"[STRICT_SURVEY] Ignoré ({reason}) → retour TopSurveys")
                            Management.guards.runtime_guard.get_guard().record_success()
                            # if _skip_card_and_retry("disqualification_or_retry"):
                            #     return
                            continue

                        # feu vert → on entre en résolution complète
                        try:
                            flush_qualified(session)
                        except Exception:
                            pass
                        try:
                            Survey.survey_solver.solve_full_survey(
                                driver,
                                api_key=api_key,
                                account_id=account_id,
                                survey_context=ctx,
                                platform=platform,
                            )
                        except TopSurveysReturn:
                            continue
                        return

                    # Cas : on est disqualifié → cliquer sur OK puis relancer
                    if preselection.question_analyzer.handle_disqualification_and_retry(driver):
                        print("⚠️ Disqualification détectée après question finale.")
                        try:
                            flush_disqualified(session)
                        except Exception:
                            pass
                        session = SurveySession()
                        time.sleep(2)
                        Management.guards.runtime_guard.get_guard().record_success()
                        # if _skip_card_and_retry("disqualification_or_retry"):
                        #     return
                        continue

                    print("ℹ️ Aucun bouton Participer ou Ok détecté. Fin de boucle.")
                    break

                except Exception as e:
                    Management.guards.runtime_guard.get_guard().record_error(e)
                    print(f"❌ Erreur lors du clic sur Participer ou redirection : {e}")
                    break

    except KeyboardInterrupt:
        Management.guards.runtime_guard.get_guard().record_error()
        print("⏹️  Fermeture manuelle détectée.")
        raise
    except Exception as e:
        Management.guards.runtime_guard.get_guard().record_error(e)
        print("💥 Erreur en boucle principale :", e)
        raise
