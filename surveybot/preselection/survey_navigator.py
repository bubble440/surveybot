import os
import re
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from preselection.auth_handler import handle_proxy_error_page_if_needed
from Survey.log_utils import log_debug, log_info

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
    try:
        WebDriverWait(driver, timeout).until(
            EC.visibility_of_element_located((By.CSS_SELECTOR,
                "[data-test-id='ps-popup-content-wrapper'], "
                "[data-test-id='ps-user-qualified-notice'], "
                "[data-test-id='ps-question-answers-wrapper']"
            ))
        )
        print("✅ Popup survey chargé et visible.")
    except Exception:
        print("⚠️ Timeout attente popup survey — on continue quand même.")


def _is_debug_enabled() -> bool:
    return os.getenv("LOG_LEVEL", "INFO").strip().upper() == "DEBUG"


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
    """
    if not _is_truthy_env(os.getenv("CTA_INTERCEPT_ONLY")):
        driver.execute_script("arguments[0].click();", element)
        return True

    return bool(
        driver.execute_script(
            """
            const el = arguments[0];
            if (!el) return false;
            const blocker = (evt) => { evt.preventDefault(); };
            el.addEventListener('click', blocker, { capture: true, once: true });
            const evt = new MouseEvent('click', { bubbles: true, cancelable: true, view: window });
            return el.dispatchEvent(evt);
            """,
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
    complete_xpath = "//button[normalize-space()='Complète' or .//span[normalize-space()='Complète']]"

    has_mystery_boxes = bool(driver.find_elements(By.CSS_SELECTOR, mystery_presence_selector))
    has_complete_btn = bool(driver.find_elements(By.XPATH, complete_xpath))
    if not (has_mystery_boxes and has_complete_btn):
        _debug("Popup mystery box non détecté avant sélection de survey.")
        return

    reason = "popup_detected=true"
    log_info(tag, reason)
    _local_pause(f"{tag} {reason}")

    wait_short = WebDriverWait(driver, 5)
    try:
        open_btn = wait_short.until(EC.presence_of_element_located((By.CSS_SELECTOR, box_selector)))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", open_btn)
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
        complete_btn = wait_short.until(EC.element_to_be_clickable((By.XPATH, complete_xpath)))
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
    selectors = [
        "div.survey-tile",
        "[class*='survey-tile']",
        "[data-test-id*='survey-tile']",
        "[data-test-id*='survey-card']",
    ]
    for selector in selectors:
        cards = driver.find_elements(By.CSS_SELECTOR, selector)
        if cards:
            return cards
    return []

def _is_card_clickable(card) -> bool:
    try:
        return card.is_displayed() and card.is_enabled()
    except Exception:
        return False


def _extract_survey_uuid(driver, card) -> "str | None":
    """
    Remonte les ancêtres DOM de la carte pour trouver l'attribut
    data-test-id="ps-survey-<uuid>" et retourne l'UUID.
    Retourne None si absent ou non parsable.
    """
    try:
        result = driver.execute_script(
            """
            const el = arguments[0];
            let node = el.parentElement;
            while (node) {
                const tid = node.getAttribute('data-test-id') || '';
                if (tid.startsWith('ps-survey-')) return tid.slice(10);
                node = node.parentElement;
            }
            return null;
            """,
            card,
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
    Ces fonctions connaissent le vrai DOM TopSurveys ([data-test-id='ps-popup-content-wrapper'],
    priorité h1>h2>p>div>span, filtres longueur/mots clés).
    Ferme/annule le popup proprement après lecture pour revenir à la liste.
    Retourne la question normalisée (strip+lower) ou None si non détectable.
    """
    try:
        from preselection.question_analyzer import extract_popup_html, extract_question_text

        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", card)
        driver.execute_script("arguments[0].click();", card)
        time.sleep(1.5)  # laisser le popup s'ouvrir

        html = extract_popup_html(driver)
        raw_question = extract_question_text(html)

        # extract_question_text retourne "Question non trouvée" quand rien n'est détecté
        first_q = None
        if raw_question and raw_question != "Question non trouvée":
            first_q = raw_question.strip().lower()

        # Fermer le popup : tenter ESC puis bouton fermeture
        try:
            from selenium.webdriver.common.keys import Keys
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(0.8)
        except Exception:
            pass

        # Fallback fermeture via bouton close si le popup est encore présent
        for close_sel in [
            "button[data-test-id='ps-close-button']",
            "button[aria-label='Close']",
            "button[aria-label='Fermer']",
        ]:
            try:
                btn = driver.find_element(By.CSS_SELECTOR, close_sel)
                driver.execute_script("arguments[0].click();", btn)
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
        cached_q = _excluded_survey_first_questions.get(uuid)  # None si pas en cache

        current_q = _read_first_question_from_card(driver, card)

        if cached_q is None:
            # Pas de question en cache pour cet UUID : impossible de comparer → on débloque par prudence
            log_info("[TOPSURVEYS][CARD_RETRY]", f"UUID {uuid!r} flaggé sans cache question → déblocage par défaut")
            _excluded_survey_uuids.discard(uuid)
            _excluded_survey_first_questions.pop(uuid, None)
            return candidate

        if current_q is None or current_q != cached_q:
            # Question absente ou différente → le contenu a changé, on débloque
            log_info("[TOPSURVEYS][CARD_RETRY]", f"UUID {uuid!r} contenu renouvelé (question changée) → déblocage")
            _excluded_survey_uuids.discard(uuid)
            _excluded_survey_first_questions.pop(uuid, None)
            return candidate

        # Question identique → flag confirmé, on logue et on passe
        _debug(f"UUID {uuid!r} flag confirmé (question identique) → carte ignorée")

    return None


def _select_best_value_card(driver):
    """
    Score chaque carte via reward_eur / duration_min et renvoie la meilleure exploitable.
    Les cartes non parsables/non cliquables sont ignorées pour garder une sélection stable.

    Logique d'exclusion en deux temps :
    1. Filtrage normal : exclut les UUIDs flaggés → retourne la meilleure carte non flaggée.
    2. Si toutes les cartes sont flaggées : retry conditionnel carte par carte (par score
       décroissant) en comparant la première question visible à celle mémorisée au moment
       du flagging. Une carte dont le contenu a changé est débloquée et retournée.
       Si toutes sont confirmées bloquantes : fallback sans exclusion (comportement précédent).
    """
    global _last_selected_uuid
    candidates = []
    for idx, card in enumerate(_find_survey_cards(driver), start=1):
        try:
            text = (card.text or "").strip()
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
        # On ne tente le retry que sur les cartes avec UUID résolu (les autres passent directement)
        flagged_with_uuid = [c for c in candidates if c[5] is not None]
        unresolved = [c for c in candidates if c[5] is None]

        unlocked = None
        if flagged_with_uuid:
            unlocked = _retry_flagged_cards_by_question(driver, flagged_with_uuid)

        if unlocked is not None:
            filtered = [unlocked] + unresolved
        else:
            # Aucune carte débloquée : fallback sans exclusion (comportement préservé)
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
    # Sélecteurs acceptables indiquant que la SPA est montée
    nav_selectors = [
        "[data-test-id='surveys-nav']",       # nav desktop surveys
        "[data-test-id='home-page-nav']",      # nav desktop home
        "[data-test-id='mobile-nav-wrapper']", # nav mobile
        ".p-nav-wrapper",                       # nav générique
        ".app-sidebar",                         # sidebar desktop
    ]
    combined = ", ".join(nav_selectors)
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, combined))
        )
        _debug("SPA prête: navigation détectée dans le DOM.")
        return True
    except Exception as e:
        _debug(f"_wait_for_spa_ready timeout: {type(e).__name__}")
        return False


def go_to_best_value_survey(driver):
    # Attendre que la SPA soit réellement montée avant toute interaction nav
    if not _wait_for_spa_ready(driver, timeout=60):
        log_info("[TOPSURVEYS][WARN]", "SPA non prête après 60s — on tente quand même la navigation")

    wait = WebDriverWait(driver, 10)

    def _click_surveys_tab():
        """
        Tente de cliquer l'onglet Sondages par plusieurs stratégies successives,
        chacune avec son propre timeout court pour ne pas bloquer trop longtemps.
        Labels observés: 'Sondages' (desktop/mobile), 'Enquêtes' (ancienne version).
        """
        # Stratégies: (By, locator, label_log, timeout)
        strategies = [
            # data-test-id desktop
            (By.CSS_SELECTOR, "[data-test-id='surveys-nav']", "data-test-id surveys-nav", 10),
            # Texte "Sondages" (label actuel observé dans le HTML)
            (By.XPATH, "//span[normalize-space()='Sondages']", "xpath Sondages", 10),
            # data-test-id mobile
            (By.CSS_SELECTOR, "[data-test-id='surveys-nav'] .p-app-mobile-nav", "mobile surveys-nav", 5),
            # Texte "Enquêtes" (ancienne version)
            (By.XPATH, "//span[normalize-space()='Enquêtes']", "xpath Enquêtes", 5),
            # data-test-id ancienne version
            (By.CSS_SELECTOR, "[data-test-id='ps-side-menu-surveys']", "ps-side-menu-surveys", 5),
        ]
        for by, locator, label, timeout in strategies:
            try:
                tab = WebDriverWait(driver, timeout).until(
                    EC.element_to_be_clickable((by, locator))
                )
                driver.execute_script("arguments[0].click();", tab)
                # Laisser Vue démarrer la transition de route
                time.sleep(1)
                print(f"🗂️  Onglet Sondages cliqué. [{label}]")
                return True
            except Exception:
                _debug(f"Stratégie [{label}] échouée.")
        return False

    if not _click_surveys_tab():
        # Fallback: navigation directe vers /surveys
        print("⚠️ Onglet Sondages introuvable via nav — navigation directe vers /surveys")
        try:
            driver.get("https://app.topsurveys.app/surveys")
            handle_proxy_error_page_if_needed(driver)
            if os.getenv("SNAP_ENABLED", "").strip() == "1":
                from Management.snap_uploader import capture_and_upload
                capture_and_upload(driver, "nav_fallback")
            print("↪️  Navigation directe /surveys")
            # Attendre que la page surveys soit montée (timeout généreux)
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "[data-test-id='ps-surveys-root']"))
            )
        except Exception as e:
            print("🛑 Exception navigation directe :", type(e).__name__, "-", e)
            return

    # Attendre que les cartes surveys soient dans le DOM après la transition de route
    try:
        survey_card_selector = ", ".join([
            "div.survey-tile",
            "[class*='survey-tile']",
            "[data-test-id*='survey-tile']",
            "[data-test-id*='survey-card']",
        ])
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, survey_card_selector))
        )
        _debug("Cartes surveys détectées dans le DOM.")
    except Exception:
        _debug("Timeout attente cartes surveys — on continue quand même.")

    _handle_mystery_box_popup(driver)
    time.sleep(0.5)  # stabilisation post-popup
    
    if not _find_survey_cards(driver):
        time.sleep(3)  # délai pour que les éventuels logs/snapshots soient traités avant pause
        log_info("[TOPSURVEYS][COOLDOWN]", "Aucun survey disponible → cooldown 15 min (DB + stop task)")
        from Management.guards.runtime_guard import get_guard, StopReason
        from Management.pause_policy import PausePolicy
        get_guard().pause(PausePolicy.MEDIUM_LONG_COOLDOWN, StopReason.NO_SURVEY_AVAILABLE)
        # pause() lève SystemExit — jamais atteint

    best_card = _select_best_value_card(driver)
    if best_card is not None:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", best_card)
            driver.execute_script("arguments[0].click();", best_card)
            print("📝 Survey le plus rentable cliqué.")
            from Management.redirect_watcher import wait_for_page_load
            wait_for_page_load(driver, timeout=30)
            _wait_for_survey_popup(driver)
            return
        except Exception as e:
            print("⚠️ Échec clic survey le plus rentable:", type(e).__name__, "-", e)

    print("⚠️ Aucune carte exploitable trouvée via score €/min — fallback premier survey cliquable.")
    # Fallback simple et prévisible
    try:
        first = wait.until(EC.element_to_be_clickable((By.XPATH, "(//div[contains(@class, 'survey-tile')])[1]")))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", first)
        driver.execute_script("arguments[0].click();", first)
        print("📝 Fallback: premier survey cliqué.")
        from Management.redirect_watcher import wait_for_page_load
        wait_for_page_load(driver, timeout=30)
        _wait_for_survey_popup(driver)
    except Exception as e:
        print("🛑 Exception sélection du survey :", type(e).__name__, "-", e)


def go_to_best_paid_survey(driver):
    """Alias rétrocompatible: redirige vers la sélection par meilleure rentabilité €/min."""
    go_to_best_value_survey(driver)