import time, os, unicodedata
from preselection.question_validation import detect_disqualification_reason
from Cash.payout import _payout_and_check_daily_stop
from config import is_cta_intercept_only
from Survey.log_utils import log_info, log_debug




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


def _is_target_closed(e: Exception) -> bool:
    msg = str(e).lower()
    return "target page" in msg or "has been closed" in msg or "target closed" in msg


def _handle_topsurveys_genial_reward_popup(driver) -> bool:
    """
    Gere le popup de recompense/remerciement TopSurveys dont le bouton de validation
    affiche 'Genial' (peut apparaitre au chargement de la page listing, au retour sur
    cette page, ou au retour apres clic sur un sondage).
    Fonction additive et independante de _handle_topsurveys_exclusion_popup : aucune
    navigation forcee apres fermeture, le flux appelant reprend normalement ensuite.
    Guard strict : bouton visible dont le texte normalise (accents retires) == 'genial'.
    Budget : 1 scan de detection, 1 tentative de clic. Retourne False si non detecte.
    """
    def _norm_genial(s):
        s = s.replace("‘", "'").replace("’", "'")
        s = unicodedata.normalize('NFD', s)
        s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
        return s.lower().strip()

    try:
        btn = None
        for b in driver.query_selector_all("button[data-test-id='ps-common-actions-button']"):
            try:
                if b.is_visible() and _norm_genial(b.inner_text() or "") == "genial":
                    btn = b
                    break
            except Exception:
                continue
        if not btn:
            for b in driver.query_selector_all("button"):
                try:
                    if not b.is_visible():
                        continue
                    # Exclusion stricte : ne jamais capturer un bouton appartenant a un
                    # popup connu deja gere par un handler distinct (ex. le bouton
                    # 'Genial' de streak_complete_modal, gere par
                    # _handle_topsurveys_streak_complete_popup) - collision confirmee
                    # sur DOM de reference (cf. BOT_EVOLUTION_MEMORY.md).
                    if (b.get_attribute("data-test-id") or "") == "streak-complete-modal-button":
                        continue
                    if _norm_genial(b.inner_text() or "") == "genial":
                        btn = b
                        break
                except Exception:
                    continue
    except Exception as e:
        if _is_target_closed(e):
            log_debug("[TOPSURVEYS_GENIAL_POPUP]", f"page fermee pendant le scan: {e}")
        return False

    if not btn:
        return False

    log_info("[TOPSURVEYS_GENIAL_POPUP]", "popup 'Genial' detecte - fermeture...")
    _local_pause_before_cta("[TOPSURVEYS_GENIAL_POPUP] popup detecte")

    try:
        if is_cta_intercept_only():
            log_info("[TOPSURVEYS_GENIAL_POPUP]", "bouton 'Genial' trouve - interception OK (CTA_INTERCEPT_ONLY actif)")
        else:
            # Timeout explicite (etait absent) : sans borne, un clic obstrue par un
            # popup superpose (ex. 'Bon travail !' au premier plan) bloque jusqu'au
            # timeout par defaut Playwright (~30s) au lieu d'echouer vite pour laisser
            # _resolve_topsurveys_popups re-scanner et traiter l'autre popup.
            btn.click(timeout=3000)
            log_info("[TOPSURVEYS_GENIAL_POPUP]", "bouton 'Genial' clique.")
        time.sleep(1.0)
    except Exception as e:
        log_info("[TOPSURVEYS_GENIAL_POPUP]", f"erreur clic: {e}")
        return False

    return True


def _topsurveys_qualification_popup_active(driver) -> bool:
    """
    Detecte le popup de qualification TopSurveys ('Tu t'es qualifie pour ce sondage !'
    / bouton 'Participer') pouvant remplacer le popup 'Bon travail !' entre sa detection
    par scan texte et le clic de fermeture (course DOM declenchee par la selection du
    survey en cours de transition dans go_to_best_value_survey).
    Garde DOM stricte : div.popup.integration-script-popup visible contenant un
    bouton/texte normalise == 'participer'. Fonction additive et independante des
    handlers existants, aucune interaction avec le popup detecte (lecture seule).
    """
    def _norm_p(s):
        s = s.replace("‘", "'").replace("’", "'")
        s = unicodedata.normalize('NFD', s)
        s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
        return s.lower().strip()

    try:
        for el in driver.query_selector_all("div.popup.integration-script-popup"):
            try:
                if not el.is_visible():
                    continue
                if "participer" in _norm_p(el.inner_text() or ""):
                    return True
            except Exception:
                continue
    except Exception as e:
        if _is_target_closed(e):
            log_debug("[TOPSURVEYS_POPUP_RACE]", f"page fermee pendant le scan: {e}")
    return False


_TOPSURVEYS_POPUP_RESOLVE_MAX_ATTEMPTS = 5
_TOPSURVEYS_BON_TRAVAIL_PATTERNS = ("bon travail", "tu as partiellement repondu", "credite ton compte")


def _normalize_topsurveys_text(s: str) -> str:
    s = (s or "").replace("‘", "'").replace("’", "'")
    s = unicodedata.normalize('NFD', s)
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return s.lower().strip()


def _close_topsurveys_bon_travail_popup_once(driver) -> bool:
    """
    Une seule tentative de detection+fermeture du popup 'Bon travail !' / resultat
    de sondage (bouton 'Complete'). Appelee depuis _resolve_topsurveys_popups a
    chaque iteration de la boucle de re-scan. Logique de detection/clic reprise
    telle quelle de l'ancienne priorite 2 de _handle_topsurveys_exclusion_popup.
    """
    try:
        txt = (driver.evaluate("() => document.body.innerText || ''") or "")
    except Exception as e:
        if _is_target_closed(e):
            log_debug("[TOPSURVEYS_POPUP_RESOLVE]", f"page fermee pendant lecture innerText: {e}")
        return False

    if not any(p in _normalize_topsurveys_text(txt) for p in _TOPSURVEYS_BON_TRAVAIL_PATTERNS):
        return False

    reason = "[TOPSURVEYS_POPUP_RESOLVE] Popup 'Bon travail !' detecte - fermeture..."
    print(reason)
    _local_pause_before_cta(reason)

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
                    if b.is_visible() and "compl" in _normalize_topsurveys_text(b.inner_text() or ""):
                        btn = b
                        break
                except Exception:
                    continue
        except Exception:
            pass

    if not btn:
        reason = "[TOPSURVEYS_POPUP_RESOLVE] Bouton 'Complete' non trouve."
        print(reason)
        _local_pause_before_cta(reason)
        return False

    # === GARDE COURSE DOM : popup de qualification ('Participer') a remplace le
    # popup 'Bon travail !' entre la detection texte et le clic (cf. BOT_EVOLUTION_MEMORY.md).
    # Garde dediee conservee en parallele de la boucle de re-scan (voir decision
    # documentee sur _topsurveys_qualification_popup_active plus bas dans ce fichier) :
    # protege specifiquement la micro-fenetre entre "bouton trouve" et "clic execute"
    # au sein de CETTE iteration, ce que le check en tete de boucle ne couvre pas.
    if _topsurveys_qualification_popup_active(driver):
        reason = ("[TOPSURVEYS_POPUP_RESOLVE] Popup 'Bon travail !' remplace par le popup de "
                  "qualification ('Participer') avant clic - abandon controle (pas de clic).")
        print(reason)
        _local_pause_before_cta(reason)
        return False

    try:
        if is_cta_intercept_only():
            reason = "[TOPSURVEYS_POPUP_RESOLVE] Bouton 'Complete' trouvé — interception OK (CTA_INTERCEPT_ONLY actif)"
            print(reason)
            _local_pause_before_cta(reason)
        else:
            # Timeout court explicite : un clic obstrue par un autre popup superpose
            # ne doit jamais bloquer au-dela d'un delai court (cf. bug superposition
            # Genial/Complete) — l'echec relance simplement une nouvelle iteration
            # de re-scan dans _resolve_topsurveys_popups plutot que de stagner.
            btn.click(timeout=3000)
            reason = "[TOPSURVEYS_POPUP_RESOLVE] Bouton 'Complete' clique."
            print(reason)
            _local_pause_before_cta(reason)
        time.sleep(1.0)
    except Exception as e:
        reason = f"[TOPSURVEYS_POPUP_RESOLVE] Erreur clic 'Complete': {e}"
        print(reason)
        _local_pause_before_cta(reason)
        return False

    return True


def _handle_topsurveys_streak_complete_popup(driver):
    """
    Gere la modale de fin de serie quotidienne TopSurveys ('La serie est terminee'),
    dont le bouton de validation unique affiche aussi 'Genial' mais dans un conteneur
    structurellement distinct (data-test-id='streak_complete_modal') du popup de
    recompense periodique deja couvert par _handle_topsurveys_genial_reward_popup.
    Fonction additive et independante : guard DOM strict et disjoint, ne fusionne
    jamais avec ce dernier (aucune modification de son guard ni de son role). Aucune
    navigation forcee apres fermeture, le flux appelant reprend normalement.
    Guard strict : conteneur(s) [data-test-id='streak_complete_modal'] visible(s), chacun
    contenant le bouton [data-test-id='streak-complete-modal-button'] visible.
    Le DOM peut contenir plusieurs occurrences de ce conteneur empilees simultanement
    (chacune dans son propre div.p-modal-mask, contenu identique) - confirme sur DOM de
    reference reel (cf. BOT_EVOLUTION_MEMORY.md). Un clic sur l'instance la plus basse
    du DOM peut alors etre intercepte par le masque d'une autre instance empilee
    au-dessus ; toutes les instances candidates sont donc essayees jusqu'a la premiere
    qui accepte le clic. Budget : 1 scan de detection (toutes instances), 1 tentative
    de clic par instance candidate.

    Valeur de retour (tri-etat, cf. BOT_EVOLUTION_MEMORY.md - module TOPSURVEYS_POPUP_RESOLVE) :
    - True  : popup detecte et ferme (au moins une instance).
    - False : popup detecte mais aucune instance candidate n'a pu etre fermee (toutes
              interceptees) - distinct de "non detecte" pour permettre a l'appelant
              (_resolve_topsurveys_popups) de redonner une chance de re-scan plutot
              que de conclure a tort a "aucun popup connu".
    - None  : popup non detecte.
    """
    try:
        candidates = []
        for container in driver.query_selector_all("[data-test-id='streak_complete_modal']"):
            try:
                if not container.is_visible():
                    continue
                btn = container.query_selector("[data-test-id='streak-complete-modal-button']")
                if btn and btn.is_visible():
                    candidates.append(btn)
            except Exception:
                continue
    except Exception as e:
        if _is_target_closed(e):
            log_debug("[TOPSURVEYS_STREAK_POPUP]", f"page fermee pendant le scan: {e}")
        return None

    if not candidates:
        return None

    log_info("[TOPSURVEYS_STREAK_POPUP]",
             f"modale 'fin de serie quotidienne' detectee ({len(candidates)} instance(s)) - fermeture...")
    _local_pause_before_cta("[TOPSURVEYS_STREAK_POPUP] modale detectee")

    if is_cta_intercept_only():
        log_info("[TOPSURVEYS_STREAK_POPUP]",
                  "bouton 'Genial' (streak_complete_modal) trouve - interception OK (CTA_INTERCEPT_ONLY actif)")
        return True

    for btn in candidates:
        try:
            btn.click(timeout=3000)
            log_info("[TOPSURVEYS_STREAK_POPUP]", "bouton 'Genial' (streak_complete_modal) clique.")
            time.sleep(1.0)
            return True
        except Exception as e:
            log_debug("[TOPSURVEYS_STREAK_POPUP]", f"instance obstruee, tentative instance suivante: {e}")
            continue

    log_info("[TOPSURVEYS_STREAK_POPUP]", "erreur clic: toutes les instances empilees sont obstruees.")
    return False


_TOPSURVEYS_SURVEY_RESULT_PATTERNS = ("tu as repondu avec succes au sondage",)


def _close_topsurveys_survey_result_popup_once(driver) -> bool:
    """
    Une seule tentative de detection+fermeture du popup de resultat de sondage
    'Tu as repondu avec succes au sondage !' (bouton 'Complete', meme
    data-test-id='ps-common-actions-button' que le popup 'Bon travail !', mais
    variant de texte non couvert par _TOPSURVEYS_BON_TRAVAIL_PATTERNS - cf.
    BOT_EVOLUTION_MEMORY.md, module TOPSURVEYS_POPUP_RESOLVE). Fonction additive
    et independante : _close_topsurveys_bon_travail_popup_once n'est ni
    modifiee ni ses patterns etendus. Appelee depuis _resolve_topsurveys_popups.
    """
    try:
        txt = (driver.evaluate("() => document.body.innerText || ''") or "")
    except Exception as e:
        if _is_target_closed(e):
            log_debug("[TOPSURVEYS_POPUP_RESOLVE]", f"page fermee pendant lecture innerText (survey_result): {e}")
        return False

    if not any(p in _normalize_topsurveys_text(txt) for p in _TOPSURVEYS_SURVEY_RESULT_PATTERNS):
        return False

    log_info("[TOPSURVEYS_POPUP_RESOLVE]", "Popup resultat de sondage detecte - fermeture...")
    _local_pause_before_cta("[TOPSURVEYS_POPUP_RESOLVE] Popup resultat de sondage detecte")

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
        reason = "[TOPSURVEYS_POPUP_RESOLVE] Bouton 'Complete' (popup resultat de sondage) non trouve."
        print(reason)
        _local_pause_before_cta(reason)
        return False

    if _topsurveys_qualification_popup_active(driver):
        reason = ("[TOPSURVEYS_POPUP_RESOLVE] Popup resultat de sondage remplace par le popup de "
                  "qualification ('Participer') avant clic - abandon controle (pas de clic).")
        print(reason)
        _local_pause_before_cta(reason)
        return False

    try:
        if is_cta_intercept_only():
            reason = "[TOPSURVEYS_POPUP_RESOLVE] Bouton 'Complete' (popup resultat de sondage) trouvé — interception OK (CTA_INTERCEPT_ONLY actif)"
            print(reason)
            _local_pause_before_cta(reason)
        else:
            btn.click(timeout=3000)
            reason = "[TOPSURVEYS_POPUP_RESOLVE] Bouton 'Complete' (popup resultat de sondage) clique."
            print(reason)
            _local_pause_before_cta(reason)
        time.sleep(1.0)
    except Exception as e:
        reason = f"[TOPSURVEYS_POPUP_RESOLVE] Erreur clic 'Complete' (popup resultat de sondage): {e}"
        print(reason)
        _local_pause_before_cta(reason)
        return False

    return True


def _handle_ps_offers_platforms_popup(driver):
    """
    Gere la popup Prime Insights "Selectionner les appareils" / "Quels sont les
    appareils que tu possedes ?" ([data-test-id='ps-offers-platforms-popup']),
    observee bloquant toute la page (masque p-modal-mask, "subtree intercepts
    pointer events" sur tout clic) sur EarnStar (page d'accueil et liste de
    sondages) - infrastructure tierce Prime Insights partagee entre les
    plateformes, donc potentiellement rencontree aussi sur TopSurveys/
    PrimeOpinion/HeyCash. Fonction additive et independante : guard DOM strict
    et disjoint des autres handlers de ce fichier, aucune modification de leur
    logique.
    Action : coche la case "Desktop"
    ([data-test-id='ps-offers-platforms-popup-desktop'] input[type='checkbox'])
    si pas deja cochee, puis clique
    [data-test-id='ps-offers-platforms-popup-save'] ("Sauvegarder la selection").
    Valeur de retour (tri-etat, meme convention que
    _handle_topsurveys_streak_complete_popup) :
    - True  : popup detectee et fermee (checkbox cochee + sauvegarde cliquee,
              ou interception CTA_INTERCEPT_ONLY).
    - False : popup detectee mais fermeture impossible (checkbox ou bouton
              introuvable/inclickable) - permet a l'appelant de redonner une
              chance de re-scan plutot que de conclure a tort a "aucun popup
              connu".
    - None  : popup non detectee.
    """
    try:
        popup = driver.query_selector("[data-test-id='ps-offers-platforms-popup']")
        if not popup or not popup.is_visible():
            return None
    except Exception as e:
        if _is_target_closed(e):
            log_debug("[PS_OFFERS_PLATFORMS_POPUP]", f"page fermee pendant le scan: {e}")
        return None

    log_info("[PS_OFFERS_PLATFORMS_POPUP]", "popup 'Selectionner les appareils' detectee - fermeture...")
    _local_pause_before_cta("[PS_OFFERS_PLATFORMS_POPUP] popup detectee")

    try:
        desktop_wrapper = popup.query_selector("[data-test-id='ps-offers-platforms-popup-desktop']")
        checkbox = desktop_wrapper.query_selector("input[type='checkbox']") if desktop_wrapper else None
        save_btn = popup.query_selector("[data-test-id='ps-offers-platforms-popup-save']")
    except Exception as e:
        log_info("[PS_OFFERS_PLATFORMS_POPUP]", f"erreur lecture DOM: {e}")
        return False

    if not checkbox or not save_btn:
        log_info("[PS_OFFERS_PLATFORMS_POPUP]", "checkbox Desktop ou bouton Sauvegarder introuvable")
        return False

    try:
        already_checked = checkbox.is_checked()
    except Exception:
        already_checked = False

    try:
        if is_cta_intercept_only():
            log_info(
                "[PS_OFFERS_PLATFORMS_POPUP]",
                "checkbox Desktop + bouton Sauvegarder trouves - interception OK "
                "(CTA_INTERCEPT_ONLY actif), pas de clic reel.",
            )
        else:
            if not already_checked:
                checkbox.click(timeout=3000)
                log_info("[PS_OFFERS_PLATFORMS_POPUP]", "checkbox Desktop cochee.")
            save_btn.click(timeout=3000)
            log_info("[PS_OFFERS_PLATFORMS_POPUP]", "bouton 'Sauvegarder la selection' clique.")
        time.sleep(1.0)
    except Exception as e:
        log_info("[PS_OFFERS_PLATFORMS_POPUP]", f"erreur clic: {e}")
        return False

    return True


def _handle_heycash_level_up_popup(driver):
    """
    Gere la modale de felicitations HeyCash 'Niveau superieur !' (passage de niveau
    du programme de fidelite, avec credit d'un bonus), pouvant apparaitre de facon
    intermittente juste apres le login ou au retour sur le listing de sondages (ex.
    apres completion d'un sondage). Fonction additive et independante : guard DOM
    strict et disjoint des autres handlers de ce fichier, aucune modification de
    leur logique. Partagee via _resolve_topsurveys_popups entre les plateformes
    Prime Insights (TopSurveys, PrimeOpinion, EarnStar, HeyCash, FiveSurveys), mais
    guard scope au DOM confirme sur HeyCash - ne suppose pas de structure identique
    sur les autres plateformes de la meme famille.
    Guard strict : conteneur [data-test-id='user_level_modal'] visible, contenant le
    bouton [data-test-id='congrats-level-button'] visible (recherche du bouton
    scopee au conteneur, pas de scan page entiere - evite toute collision avec le
    bouton de fermeture generique [data-test-id='close-modal-button'], partage par
    d'autres modales du meme composant generique p-modal-container/p-modal-layout,
    et volontairement non utilise ici comme mecanisme de fermeture).
    Le DOM peut en theorie contenir plusieurs occurrences du conteneur (meme
    convention defensive que _handle_topsurveys_streak_complete_popup) - toutes les
    instances candidates sont essayees jusqu'a la premiere qui accepte le clic.
    Budget : 1 scan de detection (toutes instances), 1 tentative de clic par
    instance candidate.

    Valeur de retour (tri-etat, meme convention que
    _handle_topsurveys_streak_complete_popup) :
    - True  : popup detectee et fermee (au moins une instance).
    - False : popup detectee mais aucune instance candidate n'a pu etre fermee
              (toutes interceptees) - permet a l'appelant de redonner une chance de
              re-scan plutot que de conclure a tort a "aucun popup connu".
    - None  : popup non detectee.
    """
    try:
        candidates = []
        for container in driver.query_selector_all("[data-test-id='user_level_modal']"):
            try:
                if not container.is_visible():
                    continue
                btn = container.query_selector("button[data-test-id='congrats-level-button']")
                if btn and btn.is_visible():
                    candidates.append(btn)
            except Exception:
                continue
    except Exception as e:
        if _is_target_closed(e):
            log_debug("[HEYCASH_LEVEL_UP_POPUP]", f"page fermee pendant le scan: {e}")
        return None

    if not candidates:
        return None

    log_info("[HEYCASH_LEVEL_UP_POPUP]",
             f"modale 'Niveau superieur !' detectee ({len(candidates)} instance(s)) - fermeture...")
    _local_pause_before_cta("[HEYCASH_LEVEL_UP_POPUP] modale detectee")

    if is_cta_intercept_only():
        log_info("[HEYCASH_LEVEL_UP_POPUP]",
                  "bouton 'Continue a gagner de l'argent' trouve - interception OK (CTA_INTERCEPT_ONLY actif)")
        return True

    for btn in candidates:
        try:
            btn.click(timeout=3000)
            log_info("[HEYCASH_LEVEL_UP_POPUP]", "bouton 'Continue a gagner de l'argent' clique.")
            time.sleep(1.0)
            return True
        except Exception as e:
            log_debug("[HEYCASH_LEVEL_UP_POPUP]", f"instance obstruee, tentative instance suivante: {e}")
            continue

    log_info("[HEYCASH_LEVEL_UP_POPUP]", "erreur clic: toutes les instances candidates sont obstruees.")
    return False


def _resolve_topsurveys_popups(driver, max_attempts: int = _TOPSURVEYS_POPUP_RESOLVE_MAX_ATTEMPTS) -> dict:
    """
    Ferme successivement les popups TopSurveys connus (recompense 'Genial', boite
    mystere, resultat de sondage 'Bon travail !'/'Complete', modale de fin de serie
    quotidienne, popup Prime Insights "Selectionner les appareils", modale HeyCash
    'Niveau superieur !') pouvant apparaitre superposes au retour sur
    app.topsurveys.app, dans un ordre non deterministe d'une session a l'autre.

    Re-evalue l'etat de la page apres CHAQUE tentative (reussie ou non) plutot
    qu'un scan+clic unique par type de popup — corrige le blocage observe quand
    le popup detecte en premier n'est pas celui au premier plan (clic intercepte
    par l'autre popup, "element intercepts pointer events", jusqu'a un timeout non
    borne sur ce chemin avant ce patch).

    Ne touche jamais au popup de qualification ('Participer', cf.
    _topsurveys_qualification_popup_active) : detection = arret immediat de la
    boucle, ce n'est pas un popup a fermer par ce mecanisme (flux qualification
    gere ailleurs, via click_participer_if_qualified).

    Budget : max_attempts tentatives (defaut 5). Abandon controle avec log
    explicite si le budget est atteint sans etat stable (aucun popup connu
    restant detectable, ni qualification active, ni sortie de topsurveys.app).

    Ne navigue jamais vers le sondage suivant — reste la responsabilite de
    l'appelant, a declencher une seule fois apres stabilisation.

    Retourne un dict {"genial_closed", "mystery_box_closed", "bon_travail_closed",
    "streak_complete_closed", "survey_result_closed", "offers_platforms_closed",
    "heycash_level_up_closed"} (bool chacun) indiquant quels types de popup ont ete
    fermes au moins une fois.
    """
    result = {
        "genial_closed": False,
        "mystery_box_closed": False,
        "bon_travail_closed": False,
        "streak_complete_closed": False,
        "survey_result_closed": False,
        "offers_platforms_closed": False,
        "heycash_level_up_closed": False,
    }

    for attempt in range(1, max_attempts + 1):
        try:
            url = (driver.url or "").lower()
        except Exception as e:
            if _is_target_closed(e):
                log_debug("[TOPSURVEYS_POPUP_RESOLVE]", f"page fermee pendant re-scan (attempt={attempt}): {e}")
            break
        if (
            "topsurveys.app" not in url
            and "primeopinion.com" not in url
            and "earnstar.com" not in url
            and "heycash.com" not in url
            and "fivesurveys.com" not in url
        ):
            break

        if _topsurveys_qualification_popup_active(driver):
            log_debug("[TOPSURVEYS_POPUP_RESOLVE]",
                      f"popup de qualification actif - arret re-scan (attempt={attempt})")
            break

        offers_platforms_state = _handle_ps_offers_platforms_popup(driver)
        if offers_platforms_state is True:
            result["offers_platforms_closed"] = True
            continue
        if offers_platforms_state is False:
            log_debug("[TOPSURVEYS_POPUP_RESOLVE]",
                      f"popup 'offres appareils' detectee mais fermeture echouee (attempt={attempt}) - nouveau passage.")
            continue

        if _handle_topsurveys_genial_reward_popup(driver):
            result["genial_closed"] = True
            continue

        try:
            has_mystery_boxes = bool(driver.query_selector_all("[data-test-id^='ps-mystery-box-item-button']"))
        except Exception as e:
            if _is_target_closed(e):
                log_debug("[TOPSURVEYS_POPUP_RESOLVE]", f"page fermee pendant scan mystery-box: {e}")
            has_mystery_boxes = False
        if has_mystery_boxes:
            import preselection.survey_navigator as survey_navigator
            survey_navigator._handle_mystery_box_popup(driver)
            result["mystery_box_closed"] = True
            time.sleep(0.5)
            continue

        if _close_topsurveys_bon_travail_popup_once(driver):
            result["bon_travail_closed"] = True
            continue

        streak_state = _handle_topsurveys_streak_complete_popup(driver)
        if streak_state is True:
            result["streak_complete_closed"] = True
            continue

        # streak_state False (detecte, clic echoue - ex. intercepte par le popup de
        # resultat de sondage reste au premier plan sous la modale) ou None (non
        # detecte) : dans les deux cas, tenter le popup de resultat de sondage avant
        # de conclure a "etat stable" (cf. BOT_EVOLUTION_MEMORY.md).
        if _close_topsurveys_survey_result_popup_once(driver):
            result["survey_result_closed"] = True
            continue

        level_up_state = _handle_heycash_level_up_popup(driver)
        if level_up_state is True:
            result["heycash_level_up_closed"] = True
            continue

        if streak_state is False:
            # Detecte mais ni son propre clic ni le popup sous-jacent n'ont debloque
            # l'etat a ce passage - nouveau passage de re-scan dans le budget existant
            # plutot que de sortir a tort comme si aucun popup connu n'etait present.
            log_debug("[TOPSURVEYS_POPUP_RESOLVE]",
                      f"streak_complete detecte mais clic echoue, popup de resultat non ferme (attempt={attempt}) - nouveau passage.")
            continue

        if level_up_state is False:
            # Meme logique que streak_state False ci-dessus : detectee mais clic
            # echoue (toutes instances obstruees) - nouveau passage de re-scan dans
            # le budget existant plutot que de conclure a tort a "etat stable".
            log_debug("[TOPSURVEYS_POPUP_RESOLVE]",
                      f"modale HeyCash 'Niveau superieur' detectee mais clic echoue (attempt={attempt}) - nouveau passage.")
            continue

        # Aucun popup connu detecte a ce passage -> etat stable, fin de boucle.
        break
    else:
        log_info("[TOPSURVEYS_POPUP_RESOLVE]",
                 f"budget de {max_attempts} tentatives atteint sans etat stable - abandon controle.")

    return result


def _handle_topsurveys_exclusion_popup(driver, account_id, platform=None) -> bool: #survey_executor
    """
    Gere les popups TopSurveys au retour sur app.topsurveys.app.

    Delegue la detection/fermeture a _resolve_topsurveys_popups (boucle de
    re-scan bornee, gere les popups superposes en ordre non deterministe), puis
    applique le post-traitement propre au(x) type(s) de popup ferme(s) :

    - Boite mystere fermee (priorite haute) : encaissement
      (_payout_and_check_daily_stop) puis navigation vers le meilleur sondage
      suivant. Retourne True.
    - Sinon, popup 'Bon travail !' ferme : navigation vers le meilleur sondage
      suivant, puis verification de disqualification (comportement inchange).
      Retourne True.
    - Sinon, popup 'Genial' et/ou modale de fin de serie quotidienne fermes seuls
      (aucun autre popup superpose) : aucune navigation forcee, le flux appelant
      reprend son cours normal (comportement inchange par rapport a
      l'implementation additive d'origine).
    - Aucun popup detecte : False.
    """
    try:
        url = (driver.url or "").lower()
        if "topsurveys.app" not in url:
            return False
    except Exception as e:
        # 🔎 DIAG : si la page est déjà fermée à ce stade, c'est la toute première
        # instruction de la fonction — donc la page est morte AVANT même d'entrer ici
        # (entre le "Retour TopSurveys" du log_info et cet appel).
        if _is_target_closed(e):
            print(f"[TOPSURVEYS_POPUP][DIAG] Page déjà fermée dès l'entrée de "
                  f"_handle_topsurveys_exclusion_popup (avant tout traitement) : {e}")
        return False

    resolved = _resolve_topsurveys_popups(driver)

    if not any(resolved.values()):
        return False

    import preselection.survey_navigator as survey_navigator

    # === PRIORITE 1 : Mystery boxes (etaient fermees pendant le re-scan) ===
    if resolved["mystery_box_closed"]:
        try:
            _payout_and_check_daily_stop(driver, account_id, email="", platform=platform)  # retrait + DAILY STOP
        except Exception as e:
            print(f"[TOPSURVEYS_POPUP] Erreur payout mystery box: {e}")
        try:
            survey_navigator.go_to_best_value_survey(driver)
            print("[TOPSURVEYS_POPUP] Navigation vers nouveau survey OK")
        except Exception as e:
            print(f"[TOPSURVEYS_POPUP] Erreur navigation: {e}")
            return False
        return True

    # === PRIORITE 2 : Popup 'Bon travail !' (sans mystery box) ===
    if resolved["bon_travail_closed"]:
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

        # === PRIORITE 3 : check disqualification puis relance si besoin (inchange) ===
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
                _payout_and_check_daily_stop(driver, account_id, email="", platform=platform)  # retrait + DAILY STOP
                time.sleep(0.7)
                survey_navigator.go_to_best_value_survey(driver)
                return True
        except Exception as e:
            print("💥 Erreur check disqualification TopSurveys :", e)

        return True

    # === Popup 'Genial' ferme seul : pas de navigation forcee (comportement d'origine) ===
    return True