import time, os, unicodedata
from preselection.question_validation import detect_disqualification_reason
from Cash.payout import _payout_and_check_daily_stop




def _page_text_lc(driver) -> str:
    try:
        return (driver.evaluate("() => document.body.innerText || ''") or "").lower()
    except Exception:
        return ""

def _env_truthy(name: str, default: str = "0") -> bool:
    v = (os.getenv(name, default) or "").strip().lower()
    return v in ("1", "true", "yes", "on")

def _close_other_tabs_in_current_session(driver):
    """Ferme tous les autres onglets de ce driver, garde l'onglet courant."""
    page = driver
    current = page
    try:
        pages = page.context.pages
    except Exception:
        return
    for p in pages:
        if p is not current:
            try:
                p.close()
                time.sleep(3)
            except Exception:
                pass


def _local_pause_before_cta(reason: str = "") -> None:
    try:
        from config import should_pause_before_cta
        if not should_pause_before_cta():
            return
        msg = "[LOCAL][PAUSE] Appuie sur <Enter> pour autoriser le clic CTA"
        if reason:
            msg += f" ({reason})"
        print(msg, flush=True)
        try:
            input()
        except KeyboardInterrupt:
            raise
    except Exception:
        return


def _handle_topsurveys_exclusion_popup(driver, account_id) -> bool: #survey_executor
    """
    Gere les popups TopSurveys au retour sur app.topsurveys.app.

    Priorite 1 — Mystery boxes presentes (popup recompense, avec ou sans 'Bon travail !') :
      selectionne une boite via _handle_mystery_box_popup (qui clique aussi 'Complete'),
      puis navigue vers le meilleur survey. Retourne True.

    Priorite 2 — Popup 'Bon travail !' sans mystery boxes (disqualification simple) :
      clique 'Complete' directement, puis navigue. Retourne True.

    Retourne False si aucun des deux cas n'est detecte.
    """
    try:
        url = (driver.url or "").lower()
        if "topsurveys.app" not in url:
            return False
    except Exception:
        return False

    import preselection.survey_navigator as survey_navigator

    # === PRIORITE 1 : Mystery boxes ===
    try:
        has_boxes = bool(driver.query_selector_all("[data-test-id^='ps-mystery-box-item-button']"))
    except Exception:
        has_boxes = False

    if has_boxes:
        reason = "[TOPSURVEYS_POPUP] Mystery boxes detectees - selection en cours..."
        print(reason)
        _local_pause_before_cta(reason)
        try:
            survey_navigator._handle_mystery_box_popup(driver)
            time.sleep(1.0)
            _payout_and_check_daily_stop(driver, account_id, email="")  # retrait + DAILY STOP
        except Exception as e:
            print(f"[TOPSURVEYS_POPUP] Erreur mystery box: {e}")
        # Navigation vers le prochain survey
        try:
            survey_navigator.go_to_best_value_survey(driver)
            print("[TOPSURVEYS_POPUP] Navigation vers nouveau survey OK")
        except Exception as e:
            print(f"[TOPSURVEYS_POPUP] Erreur navigation: {e}")
            return False
        return True

    # === PRIORITE 2 : Popup 'Bon travail !' sans mystery boxes ===
    try:
        txt = (driver.evaluate("() => document.body.innerText || ''") or "").lower()
    except Exception:
        return False

    def _norm(s):
        s = s.replace("‘", "'").replace("’", "'")
        s = unicodedata.normalize('NFD', s)
        s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
        return s.lower()

    txt_norm = _norm(txt)
    patterns = ["bon travail", "tu as partiellement repondu", "credite ton compte"]

    if not any(p in txt_norm for p in patterns):
        return False

    reason = "[TOPSURVEYS_POPUP] Popup 'Bon travail !' detecte (sans mystery box) - fermeture..."
    print(reason)
    _local_pause_before_cta(reason)

    # Fermer le popup via le bouton 'Complete'
    btn = None
    try:
        btn = driver.wait_for_selector(
            "button[data-test-id='ps-common-actions-button']",
            state="visible",
            timeout=2000,
        )
    except Exception:
        pass

    if not btn:
        try:
            for b in driver.query_selector_all("button"):
                try:
                    if b.is_visible() and "compl" in _norm(b.inner_text() or ""):
                        btn = b
                        break
                except Exception:
                    continue
        except Exception:
            pass

    if btn:
        try:
            if os.getenv("CTA_INTERCEPT_ONLY", "0") == "1":
                reason = "[TOPSURVEYS_POPUP] Bouton 'Complete' trouvé — interception OK (CTA_INTERCEPT_ONLY actif)"
                print(reason)
                _local_pause_before_cta(reason)
            else:
                btn.click()
                reason = "[TOPSURVEYS_POPUP] Bouton 'Complete' clique."
                print(reason)
                _local_pause_before_cta(reason)
            time.sleep(1.0)
        except Exception as e:
            reason = f"[TOPSURVEYS_POPUP] Erreur clic: {e}"
            print(reason)
            _local_pause_before_cta(reason)
            return False
    else:
        reason = "[TOPSURVEYS_POPUP] Bouton non trouve."
        print(reason)
        _local_pause_before_cta(reason)
        return False

    # Navigation vers le prochain survey
    reason = "[TOPSURVEYS_POPUP] Relance preselection..."
    print(reason)
    _local_pause_before_cta(reason)
    try:
        survey_navigator.go_to_best_value_survey(driver)
        reason = "[TOPSURVEYS_POPUP] Navigation vers nouveau survey OK"
        print(reason)
        _local_pause_before_cta(reason)
        time.sleep(1.0)
    except Exception as e:
        reason = f"[TOPSURVEYS_POPUP] Erreur navigation: {e}"
        print(reason)
        _local_pause_before_cta(reason)
        return False


    # === PRIORITE 3 : check disqualification puis relance si besoin ===
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
            _payout_and_check_daily_stop(driver, account_id, email="")  # retrait + DAILY STOP
            import preselection.survey_navigator
            time.sleep(0.7)
            preselection.survey_navigator.go_to_best_value_survey(driver)
            return True
    except Exception as e:
        print("💥 Erreur check disqualification TopSurveys :", e)

    return True
