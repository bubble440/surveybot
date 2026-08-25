import os
import re
import time

from preselection.auth_handler import handle_proxy_error_page_if_needed
from Survey.log_utils import is_debug, log_debug, log_info
from config import is_cta_intercept_only

# SNAP_ENABLED est une variable GLOBAL_CONFIG : en build compilé (Nuitka), elle provient
# exclusivement de global_config.py, jamais de l'environnement du process (cf. config.py).
# En dev/attach (global_config.py absent du projet), fallback os.getenv.
try:
    from global_config import SNAP_ENABLED  # type: ignore
except ImportError:
    SNAP_ENABLED = os.getenv("SNAP_ENABLED", "")

# UUIDs de surveys bloquants (irrésolvables) accumulés sur la durée du processus.
# Alimenté via mark_last_selected_survey_as_blocked() lors d'un soft restart.
_excluded_survey_uuids: set = set()
_last_selected_uuid: str | None = None

# Cache de la première question visible au moment du flagging d'un UUID.
# Clé : UUID — Valeur : première question (str, normalisée).
# Même durée de vie que _excluded_survey_uuids : réinitialisé après qualification réussie.
_excluded_survey_first_questions: dict = {}


def mark_last_selected_survey_as_blocked(first_question: "str | None" = None) -> None:
    """
    Ajoute l'UUID du dernier survey sélectionné au set d'exclusion runtime.
    Mémorise également la première question visible au moment du flag pour permettre
    la détection d'un renouvellement de contenu (même UUID, questions différentes).
    Appelé lors d'un soft restart déclenché par un survey irrésolvable.
    """
    global _last_selected_uuid
    if _last_selected_uuid:
        _excluded_survey_uuids.add(_last_selected_uuid)
        if first_question is not None:
            _excluded_survey_first_questions[_last_selected_uuid] = first_question.strip().lower()
        log_info("[TOPSURVEYS][EXCLUSION]", f"Survey exclu pour ce processus: uuid={_last_selected_uuid!r}")


def clear_exclusion_cache() -> None:
    """
    Réinitialise l'intégralité des flags UUID et du cache de première question.
    À appeler dès qu'un survey est qualifié avec succès.
    """
    _excluded_survey_uuids.clear()
    _excluded_survey_first_questions.clear()
    log_info("[TOPSURVEYS][EXCLUSION]", "Cache exclusion réinitialisé après qualification réussie.")


def _local_pause(reason: str = "") -> None:
    try:
        from config import should_pause_before_cta
        if not should_pause_before_cta():
            return
        msg = "[LOCAL][PAUSE] Appuie sur <Enter> pour continuer"
        if reason:
            msg += f" ({reason})"
        print(msg, flush=True)
        try:
            input()
        except KeyboardInterrupt:
            raise
    except Exception:
        return


def _wait_for_survey_popup(driver, timeout: int = 20) -> None:
    """
    Attend que le popup de qualification (ou la première question du preselect)
    soit visuellement présent dans le DOM avant de continuer le traitement.
    Évite une extraction DOM prématurée juste après le clic sur une carte survey.
    """
    page = driver
    try:
        page.wait_for_selector(
            "[data-test-id='ps-popup-content-wrapper'], "
            "[data-test-id='ps-user-qualified-notice'], "
            "[data-test-id='ps-question-answers-wrapper']",
            state='visible',
            timeout=timeout * 1000,
        )
        print("✅ Popup survey chargé et visible.")
    except Exception:
        print("⚠️ Timeout attente popup survey — on continue quand même.")


def _is_debug_enabled() -> bool:
    return is_debug()


def _debug(msg: str):
    if _is_debug_enabled():
        log_debug("[TOPSURVEYS][DEBUG]", msg)


def _is_truthy_env(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _click_button_with_optional_intercept(driver, element) -> bool:
    """
    Clique un bouton normalement, ou en mode CTA_INTERCEPT_ONLY déclenche
    un click non destructif (dispatch évènement + preventDefault) pour exécuter
    les handlers UI sans soumission réelle.
    element : Playwright ElementHandle
    """
    page = driver
    if not is_cta_intercept_only():
        page.evaluate("(el) => el.click()", element)
        return True

    return bool(
        page.evaluate(
            """(el) => {
                if (!el) return false;
                const blocker = (evt) => { evt.preventDefault(); };
                el.addEventListener('click', blocker, { capture: true, once: true });
                const evt = new MouseEvent('click', { bubbles: true, cancelable: true, view: window });
                return el.dispatchEvent(evt);
            }""",
            element,
        )
    )


def _handle_mystery_box_popup(driver) -> None:
    """
    Gère le popup de récompense TopSurveys si présent:
    - détecte via présence d'un bouton de mystery box ET d'un bouton "Complète"
    - ouvre uniquement la 3e boîte
    - clique "Complète" pour fermer
    Budget strict: 1 tentative d'ouverture, 1 tentative de fermeture.
    """
    tag = "[TOPSURVEYS_MYSTERY_BOX]"
    box_selector = "[data-test-id='ps-mystery-box-item-button-2']"
    mystery_presence_selector = "[data-test-id^='ps-mystery-box-item-button']"
    complete_xpath = "xpath=//button[normalize-space()='Complète' or .//span[normalize-space()='Complète']]"

    page = driver
    has_mystery_boxes = bool(page.query_selector_all(mystery_presence_selector))
    has_complete_btn = bool(page.query_selector_all(complete_xpath))
    if not (has_mystery_boxes and has_complete_btn):
        _debug("Popup mystery box non détecté avant sélection de survey.")
        return

    reason = "popup_detected=true"
    log_info(tag, reason)
    _local_pause(f"{tag} {reason}")

    try:
        open_btn = page.wait_for_selector(box_selector, state='attached', timeout=5_000)
        open_btn.evaluate("(el) => el.scrollIntoView({block:'center'})")
        open_ok = _click_button_with_optional_intercept(driver, open_btn)
        reason = f"box3_click={'OK' if open_ok else 'INTERCEPTION_IMPOSSIBLE'}"
        log_info(tag, reason)
        _local_pause(f"{tag} {reason}")
    except Exception as e:
        reason = f"box3_click=FAILED reason={type(e).__name__}"
        log_info(tag, reason)
        _local_pause(f"{tag} {reason}")
        return

    time.sleep(1)

    try:
        complete_btn = page.wait_for_selector(complete_xpath, state='visible', timeout=5_000)
        complete_ok = _click_button_with_optional_intercept(driver, complete_btn)
        reason = f"complete_click={'OK' if complete_ok else 'INTERCEPTION_IMPOSSIBLE'}"
        log_info(tag, reason)
        _local_pause(f"{tag} {reason}")
    except Exception as e:
        reason = f"complete_click=FAILED reason={type(e).__name__}"
        log_info(tag, reason)
        _local_pause(f"{tag} {reason}")


def _parse_reward_eur(text: str):
    """Extrait un montant EUR depuis le texte de carte (ex: 0,66 €)."""
    if not text:
        return None
    match = re.search(r"(?:€\s*(\d+[\.,]?\d*)|(\d+[\.,]?\d*)\s*€)", text)
    if not match:
        return None
    raw = (match.group(1) or match.group(2) or "").replace(",", ".").strip()
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _parse_duration_min(text: str):
    """Extrait la durée estimée en minutes depuis le texte de carte (ex: 22 min)."""
    if not text:
        return None
    match = re.search(r"(\d+)\s*(?:min|mn|minute(?:s)?)\b", text, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        duration = int(match.group(1))
    except ValueError:
        return None
    return duration if duration > 0 else None


def _find_survey_cards(driver):
    page = driver
    selectors = [
        "div.survey-tile",
        "[class*='survey-tile']",
        "[data-test-id*='survey-tile']",
        "[data-test-id*='survey-card']",
    ]
    for selector in selectors:
        cards = page.query_selector_all(selector)
        if cards:
            return cards
    return []


def _is_card_clickable(card) -> bool:
    """card : Playwright ElementHandle"""
    try:
        return card.is_visible() and card.is_enabled()
    except Exception:
        return False


def _extract_survey_uuid(driver, card) -> "str | None":
    """
    Remonte les ancêtres DOM de la carte pour trouver l'attribut
    data-test-id="ps-survey-<uuid>" et retourne l'UUID.
    card : Playwright ElementHandle
    """
    try:
        result = card.evaluate(
            """(el) => {
                let node = el.parentElement;
                while (node) {
                    const tid = node.getAttribute('data-test-id') || '';
                    if (tid.startsWith('ps-survey-')) return tid.slice(10);
                    node = node.parentElement;
                }
                return null;
            }"""
        )
        if not result or not isinstance(result, str):
            return None
        return result
    except Exception:
        return None


def _read_first_question_from_card(driver, card) -> "str | None":
    """
    Ouvre le popup de présélection d'une carte et lit la première question via
    les fonctions canoniques de question_analyzer (extract_popup_html + extract_question_text).
    Ferme/annule le popup proprement après lecture pour revenir à la liste.

    FRONTIÈRE BLOC 1 → BLOC 2 interne : extract_popup_html attend un objet shim.
    On crée un shim temporaire juste pour cet appel, sans dupliquer de logique.
    """
    try:
        from preselection.question_analyzer import extract_popup_html, extract_question_text

        page = driver
        card.evaluate("(el) => el.scrollIntoView({block:'center'})")
        page.evaluate("(el) => el.click()", card)
        time.sleep(1.5)  # laisser le popup s'ouvrir

        html = extract_popup_html(page)
        raw_question = extract_question_text(html)

        first_q = None
        if raw_question and raw_question != "Question non trouvée":
            first_q = raw_question.strip().lower()

        # Fermer le popup : ESC puis bouton fermeture
        try:
            page.keyboard.press("Escape")
            time.sleep(0.8)
        except Exception:
            pass

        for close_sel in [
            "button[data-test-id='ps-close-button']",
            "button[aria-label='Close']",
            "button[aria-label='Fermer']",
        ]:
            try:
                btn = page.query_selector(close_sel)
                if btn:
                    page.evaluate("(el) => el.click()", btn)
                    time.sleep(0.8)
                    break
            except Exception:
                continue

        return first_q
    except Exception as e:
        _debug(f"_read_first_question_from_card: exception {type(e).__name__} - {e}")
        return None


def _retry_flagged_cards_by_question(driver, flagged_candidates) -> "tuple | None":
    """
    Stratégie de secours quand toutes les cartes candidates sont flaggées.
    Pour chaque carte (triée par score décroissant) :
      - ouvre son popup et lit la première question visible
      - compare à la question mémorisée au moment du flagging
      - si question identique → flag confirmé, on passe à la suivante
      - si question différente (ou absente du cache) → contenu renouvelé :
          on lève le flag UUID + cache pour cette carte et on la retourne comme candidate
    Retourne le tuple candidat (score, reward, duration, idx, card, uuid) si une carte
    est débloquée, ou None si toutes sont confirmées bloquantes.
    """
    log_info("[TOPSURVEYS][CARD_RETRY]", f"Carte bloquée (disqualification_or_retry), essai 1/{len(flagged_candidates)} → carte suivante")
    for candidate in flagged_candidates:
        score, reward, duration, idx, card, uuid = candidate
        cached_q = _excluded_survey_first_questions.get(uuid)

        current_q = _read_first_question_from_card(driver, card)

        if cached_q is None:
            log_info("[TOPSURVEYS][CARD_RETRY]", f"UUID {uuid!r} flaggé sans cache question → déblocage par défaut")
            _excluded_survey_uuids.discard(uuid)
            _excluded_survey_first_questions.pop(uuid, None)
            return candidate

        if current_q is None or current_q != cached_q:
            log_info("[TOPSURVEYS][CARD_RETRY]", f"UUID {uuid!r} contenu renouvelé (question changée) → déblocage")
            _excluded_survey_uuids.discard(uuid)
            _excluded_survey_first_questions.pop(uuid, None)
            return candidate

        _debug(f"UUID {uuid!r} flag confirmé (question identique) → carte ignorée")

    return None


def _select_best_value_card(driver):
    """
    Score chaque carte via reward_eur / duration_min et renvoie la meilleure exploitable.
    Les cartes non parsables/non cliquables sont ignorées pour garder une sélection stable.
    """
    global _last_selected_uuid
    candidates = []
    for idx, card in enumerate(_find_survey_cards(driver), start=1):
        try:
            text = (card.inner_text() or "").strip()
            reward = _parse_reward_eur(text)
            duration = _parse_duration_min(text)
            if reward is None:
                _debug(f"Carte #{idx} ignorée: reward non parsable | text={text!r}")
                continue
            if duration is None:
                _debug(f"Carte #{idx} ignorée: durée non parsable | text={text!r}")
                continue
            if duration <= 0:
                _debug(f"Carte #{idx} ignorée: durée <= 0 | text={text!r}")
                continue
            if not _is_card_clickable(card):
                _debug(f"Carte #{idx} ignorée: non cliquable")
                continue
            score = reward / duration
            uuid = _extract_survey_uuid(driver, card)
            candidates.append((score, reward, duration, idx, card, uuid))
            _debug(
                f"Carte #{idx} candidate: reward={reward:.2f}€ duration={duration}min score={score:.4f}€/min uuid={uuid!r}"
            )
        except Exception as e:
            _debug(f"Carte #{idx} ignorée: exception {type(e).__name__} - {e}")

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)

    # --- Étape 1 : filtrage normal des UUIDs bloquants ---
    filtered = [c for c in candidates if c[5] is None or c[5] not in _excluded_survey_uuids]

    if not filtered:
        # --- Étape 2 : toutes les cartes sont flaggées → retry conditionnel par question ---
        log_info("[TOPSURVEYS][EXCLUSION]", "Toutes les cartes candidates sont flaggées → tentative retry conditionnel par question")
        flagged_with_uuid = [c for c in candidates if c[5] is not None]
        unresolved = [c for c in candidates if c[5] is None]

        unlocked = None
        if flagged_with_uuid:
            unlocked = _retry_flagged_cards_by_question(driver, flagged_with_uuid)

        if unlocked is not None:
            filtered = [unlocked] + unresolved
        else:
            log_info("[TOPSURVEYS][EXCLUSION]", "Aucune carte débloquée après retry conditionnel — fallback sans exclusion")
            filtered = candidates

    best_score, best_reward, best_duration, best_idx, best_card, best_uuid = filtered[0]
    _last_selected_uuid = best_uuid
    print(
        "🧠 Survey sélectionné par rentabilité: "
        f"carte #{best_idx} | {best_reward:.2f}€ / {best_duration} min = {best_score:.4f} €/min"
    )
    return best_card


def _wait_for_spa_ready(driver, timeout: int = 60) -> bool:
    """
    Attend que la SPA Vue soit réellement chargée après login:
    - attend que document.readyState == 'complete'
    - attend qu'au moins un élément de navigation principal soit présent dans le DOM
    Retourne True si prêt, False si timeout.
    """
    page = driver
    nav_selectors = [
        "[data-test-id='surveys-nav']",
        "[data-test-id='home-page-nav']",
        "[data-test-id='mobile-nav-wrapper']",
        ".p-nav-wrapper",
        ".app-sidebar",
    ]
    combined = ", ".join(nav_selectors)
    try:
        page.wait_for_load_state("load", timeout=timeout * 1000)
        page.wait_for_selector(combined, state='attached', timeout=timeout * 1000)
        _debug("SPA prête: navigation détectée dans le DOM.")
        return True
    except Exception as e:
        _debug(f"_wait_for_spa_ready timeout: {type(e).__name__}")
        return False


def go_to_best_value_survey(driver):
    page = driver

    # Attendre que la SPA soit réellement montée avant toute interaction nav
    if not _wait_for_spa_ready(driver, timeout=60):
        log_info("[TOPSURVEYS][WARN]", "SPA non prête après 60s — on tente quand même la navigation")

    def _click_surveys_tab():
        """
        Tente de cliquer l'onglet Sondages par plusieurs stratégies successives.
        Labels observés: 'Sondages' (desktop/mobile), 'Enquêtes' (ancienne version).
        """
        # (selector, label_log, timeout_ms)
        strategies = [
            ("[data-test-id='surveys-nav']",                               "data-test-id surveys-nav",    10_000),
            ("xpath=//span[normalize-space()='Sondages']",                 "xpath Sondages",              10_000),
            ("[data-test-id='surveys-nav'] .p-app-mobile-nav",             "mobile surveys-nav",           5_000),
            ("xpath=//span[normalize-space()='Enquêtes']",                 "xpath Enquêtes",               5_000),
            ("[data-test-id='ps-side-menu-surveys']",                      "ps-side-menu-surveys",         5_000),
        ]
        for selector, label, timeout_ms in strategies:
            try:
                tab = page.wait_for_selector(selector, state='visible', timeout=timeout_ms)
                page.evaluate("(el) => el.click()", tab)
                time.sleep(1)
                print(f"🗂️  Onglet Sondages cliqué. [{label}]")
                return True
            except Exception:
                _debug(f"Stratégie [{label}] échouée.")
        return False

    def _reload_and_retry_surveys_tab(max_reloads: int = 2) -> bool:
        """
        DOM figé après échec de toutes les stratégies de _click_surveys_tab (inchangées) :
        recharge la page en cours un nombre borné de fois et retente la détection sur le
        DOM rafraîchi, avant d'escalader vers le fallback de navigation directe existant.
        N'agit pas si la page/contexte est déjà fermé (reload garanti en échec dans ce cas
        — cf. TargetClosedError observé sur le fallback direct).
        """
        for attempt in range(1, max_reloads + 1):
            try:
                if page.is_closed():
                    _debug(f"[RELOAD_RETRY] page déjà fermée avant tentative {attempt} — abandon")
                    return False
            except Exception:
                return False
            try:
                _debug(f"[RELOAD_RETRY] tentative {attempt}/{max_reloads} — reload page")
                page.reload(wait_until="domcontentloaded")
                time.sleep(2)
            except Exception as e:
                _debug(f"[RELOAD_RETRY] reload échoué tentative {attempt}: {type(e).__name__}")
                return False
            if _click_surveys_tab():
                log_info("[TOPSURVEYS][RELOAD_RETRY]", f"Onglet Sondages détecté après reload (tentative {attempt}/{max_reloads})")
                return True
        return False

    if not _click_surveys_tab() and not _reload_and_retry_surveys_tab():
        # Fallback: navigation directe vers /surveys
        print("⚠️ Onglet Sondages introuvable via nav — navigation directe vers /surveys")
        try:
            page.goto("https://app.topsurveys.app/surveys", wait_until="domcontentloaded")
            handle_proxy_error_page_if_needed(driver)
            if SNAP_ENABLED.strip() == "1":
                from Management.snap_uploader import capture_and_upload
                capture_and_upload(driver, "nav_fallback")
            print("↪️  Navigation directe /surveys")
            page.wait_for_selector("[data-test-id='ps-surveys-root']", state='attached', timeout=30_000)
        except Exception as e:
            print("🛑 Exception navigation directe :", type(e).__name__, "-", e)
            return

    # Attendre que la vue /surveys soit montée (transition de route Vue.js via clic JS synthétique)
    try:
        page.wait_for_selector("[data-test-id='ps-surveys-root']", state='attached', timeout=15_000)
    except Exception:
        _debug("Timeout attente ps-surveys-root après clic onglet — on continue.")

    # Attendre que les cartes surveys soient dans le DOM après la transition de route
    try:
        survey_card_selector = ", ".join([
            "div.survey-tile",
            "[class*='survey-tile']",
            "[data-test-id*='survey-tile']",
            "[data-test-id*='survey-card']",
        ])
        page.wait_for_selector(survey_card_selector, state='attached', timeout=20_000)
        _debug("Cartes surveys détectées dans le DOM.")
    except Exception:
        _debug("Timeout attente cartes surveys — on continue quand même.")

    # Fenetre d'attente bornee (non bloquante si absente) : la modale de fin de serie
    # quotidienne (streak_complete_modal) se monte via un appel API distinct du rendu
    # initial de la page, avec un delai plus marque apres un chargement complet
    # post-login qu'apres un simple changement d'onglet sur une SPA deja "chaude".
    # Sans cette attente, _resolve_topsurveys_popups s'executait avant le montage de
    # la modale (1 seul passage sans popup detecte a cet instant => sortie immediate
    # de boucle, cf. BOT_EVOLUTION_MEMORY.md) et ne la voyait donc jamais. Ne ferme
    # rien elle-meme (lecture seule) : la fermeture reste entierement deleguee a
    # _resolve_topsurveys_popups ci-dessous (pas de duplication du mecanisme
    # centralise). Timeout aligne sur celui deja utilise pour l'attente du bouton
    # 'Complete' dans _close_topsurveys_bon_travail_popup_once (Survey/functions.py).
    try:
        page.wait_for_selector("[data-test-id='streak_complete_modal']", state="attached", timeout=2000)
    except Exception:
        pass

    # Consolidation : remplace l'ancien double appel independant
    # (_handle_mystery_box_popup puis _handle_topsurveys_genial_reward_popup en un
    # seul passage chacun, sans re-scan) par le meme mecanisme de re-scan borne que
    # _handle_topsurveys_exclusion_popup (Survey/functions.py) — elimine la
    # duplication de logique et couvre le meme cas de popups superposes en ordre
    # non deterministe (Genial / boite mystere / Bon travail) au chargement ou au
    # retour sur le listing.
    from Survey.functions import _resolve_topsurveys_popups
    _resolve_topsurveys_popups(driver)
    time.sleep(0.5)  # stabilisation post-popup

    if not _find_survey_cards(driver):
        if SNAP_ENABLED.strip() == "1":
            from Management.snap_uploader import capture_and_upload
            capture_and_upload(driver, "no_surveys_available")
        time.sleep(3)
        log_info("[TOPSURVEYS][COOLDOWN]", "Aucun survey disponible → cooldown 15 min (DB + stop task)")
        from Management.guards.runtime_guard import get_guard, StopReason
        from Management.pause_policy import PausePolicy
        get_guard().pause(PausePolicy.MEDIUM_LONG_COOLDOWN, StopReason.NO_SURVEY_AVAILABLE)
        # pause() lève SystemExit — jamais atteint

    best_card = _select_best_value_card(driver)
    if best_card is not None:
        try:
            best_card.evaluate("(el) => el.scrollIntoView({block:'center'})")
            page.evaluate("(el) => el.click()", best_card)
            print("📝 Survey le plus rentable cliqué.")
            try:
                page.wait_for_load_state("load", timeout=30_000)
            except Exception:
                pass
            _wait_for_survey_popup(driver)
            return
        except Exception as e:
            print("⚠️ Échec clic survey le plus rentable:", type(e).__name__, "-", e)

    print("⚠️ Aucune carte exploitable trouvée via score €/min — fallback premier survey cliquable.")
    try:
        first = page.wait_for_selector("div.survey-tile", state='visible', timeout=10_000)
        first.evaluate("(el) => el.scrollIntoView({block:'center'})")
        page.evaluate("(el) => el.click()", first)
        print("📝 Fallback: premier survey cliqué.")
        try:
            page.wait_for_load_state("load", timeout=30_000)
        except Exception:
            pass
        _wait_for_survey_popup(driver)
    except Exception as e:
        print("🛑 Exception sélection du survey :", type(e).__name__, "-", e)


def go_to_best_paid_survey(driver):
    """Alias rétrocompatible: redirige vers la sélection par meilleure rentabilité €/min."""
    go_to_best_value_survey(driver)
