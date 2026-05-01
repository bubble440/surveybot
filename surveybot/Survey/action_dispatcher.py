from __future__ import annotations
import re, unicodedata, os, time, zlib
from selenium.webdriver.common.by import By
import Survey.input_handler
from Survey.dom_registry import get_target
from typing import Optional
from selenium.webdriver.common.action_chains import ActionChains
from Survey.log_utils import is_debug, log_debug, log_info

PAUSE_INTER_DISPATCH = 0.5  # pause entre deux applications de réponse consécutives (laisser le DOM re-rendre)

def _short_exc(e: Exception) -> str:
    """Rend les exceptions Selenium lisibles (sans Stacktrace bruyant)."""
    try:
        msg = getattr(e, "msg", None) or str(e) or ""
    except Exception:
        msg = ""
    # Selenium ajoute souvent un bloc 'Stacktrace:' énorme dans le message
    if "Stacktrace:" in msg:
        msg = msg.split("Stacktrace:")[0]
    msg = re.sub(r"\s+", " ", msg).strip()
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__

def solve_decipher_cardrating_rows(driver, preferred_label: Optional[str] = None, max_widgets: int = 3) -> bool:
    """
    Résout les questions Decipher 'sq-cardrating' groupées par rows.
    Stratégie (DOM-only, prédictible):
    - détecter les widgets .sq-cardrating-widget[data-uid]
    - choisir une colonne (option) (préférée si fournie, sinon un choix "safe")
    - cocher (via JS events) tous les radios cachés ans<uid>.<col>.<row>
    - vérifier que chaque groupe de name (ans<uid>.*) a un checked
    Retourne True si au moins un widget multi-row a été complété.
    """
    def _norm(s: str) -> str:
        return " ".join((s or "").split()).strip().lower()

    def _dispatch_check(el) -> None:
        # radios souvent non-interactables (hidden) ; JS events
        driver.execute_script(
            """
            const el = arguments[0];
            if (!el) return;
            try { el.checked = true; } catch(e) {}
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
            """,
            el,
        )

    def _all_rows_answered(widget_el) -> bool:
        return bool(driver.execute_script(
            """
            const widget = arguments[0];
            if (!widget) return false;
            const uid = widget.getAttribute('data-uid');
            if (!uid) return false;
            const root = widget.closest('.question') || document;
            const inputs = Array.from(root.querySelectorAll("input[type='radio'][name^='ans" + uid + ".']"));
            const names = [...new Set(inputs.map(i => i.name))];
            if (!names.length) return false;
            return names.every(n => !!root.querySelector("input[type='radio'][name='" + n + "']:checked"));
            """,
            widget_el
        ))

    def _row_group_count(widget_el) -> int:
        return int(driver.execute_script(
            """
            const widget = arguments[0];
            if (!widget) return 0;
            const uid = widget.getAttribute('data-uid');
            if (!uid) return 0;
            const root = widget.closest('.question') || document;
            const inputs = Array.from(root.querySelectorAll("input[type='radio'][name^='ans" + uid + ".']"));
            const names = [...new Set(inputs.map(i => i.name))];
            return names.length;
            """,
            widget_el
        ) or 0)

    def _pick_option_button(widget_el):
        btns = widget_el.find_elements(By.CSS_SELECTOR, ".sq-cardrating-buttons .sq-cardrating-button[data-clickable='true']")
        if not btns:
            return None

        # map text -> button
        wanted = _norm(preferred_label or "")
        if wanted:
            for b in btns:
                try:
                    t = b.text or ""
                    if not t:
                        t = b.find_element(By.CSS_SELECTOR, ".sq-cardrating-content").text
                    if _norm(t) == wanted:
                        return b
                except Exception:
                    continue

        # fallback "safe" (évite 'Jamais')
        safe_order = ["il y a quelques jours", "il y a 1-3 mois", "il y a 2-4 semaines", "il y a 1 semaine", "hier", "aujourd'hui"]
        # index par texte
        norm_to_btn = {}
        for b in btns:
            try:
                t = b.text or ""
                if not t:
                    t = b.find_element(By.CSS_SELECTOR, ".sq-cardrating-content").text
                nt = _norm(t)
                if nt and "jamais" not in nt:
                    norm_to_btn[nt] = b
            except Exception:
                continue

        for key in safe_order:
            if key in norm_to_btn:
                return norm_to_btn[key]

        # dernier recours: premier bouton non-'Jamais'
        for b in btns:
            try:
                t = b.text or ""
                if not t:
                    t = b.find_element(By.CSS_SELECTOR, ".sq-cardrating-content").text
                if "jamais" not in _norm(t):
                    return b
            except Exception:
                continue
        return None

    widgets = driver.find_elements(By.CSS_SELECTOR, ".sq-cardrating-widget[data-uid]")
    if not widgets:
        return False

    completed_any = False
    for widget in widgets[:max_widgets]:
        try:
            # on ne s'occupe que des multi-rows
            if _row_group_count(widget) <= 1:
                continue

            if _all_rows_answered(widget):
                completed_any = True
                continue

            btn = _pick_option_button(widget)
            if not btn:
                continue

            uid = (widget.get_attribute("data-uid") or "").strip()
            col = (btn.get_attribute("data-index") or "").strip()
            if not uid or not col.isdigit():
                continue

            # cocher toutes les rows pour cette colonne (ans<uid>.<col>.<row>)
            # Les inputs sont dans le meme bloc question (souvent dans une QA-view cachée)
            qroot = widget.find_element(By.XPATH, "ancestor::*[contains(@class,'question')][1]")
            inputs = qroot.find_elements(By.CSS_SELECTOR, f"input[type='radio'][id^='ans{uid}.{col}.']")
            if not inputs:
                # fallback global (au cas où la structure varie)
                inputs = driver.find_elements(By.CSS_SELECTOR, f"input[type='radio'][id^='ans{uid}.{col}.']")

            if not inputs:
                continue

            for inp in inputs:
                _dispatch_check(inp)
                time.sleep(0.03)

            # check final
            if _all_rows_answered(widget):
                completed_any = True
        except Exception:
            continue

    return completed_any

def solve_focusvision_cardsort(
    driver,
    preferred_label: Optional[str] = None,
    max_cards: int = 20,
    assignments: list[dict] | None = None,
) -> bool:
    """
    FocusVision/Decipher cardsort (DOM-only, prédictible, budget borné):
    - détecte .sq-cardsort
    - clique une bucket "safe" pour chaque carte visible, jusqu'à completion ou max_cards
    Retourne True uniquement si au moins une carte a effectivement progressé.
    """
    def _norm(s: str) -> str:
        if not s:
            return ""
        s = unicodedata.normalize("NFKC", s).replace("\xa0", " ").strip()
        s = re.sub(r"\s+", " ", s)
        return s

    def _norm_lc(s: str) -> str:
        return _norm(s).lower()

    def _pick_cardsort_root():
        try:
            css = driver.find_elements(By.CSS_SELECTOR, ".sq-cardsort")
            return css[0] if css else None
        except Exception:
            return None

    def _active_card(cs):
        try:
            cards = cs.find_elements(By.CSS_SELECTOR, ".sq-cardsort-cards li")
        except Exception:
            cards = []
        for c in cards:
            try:
                cl = (c.get_attribute("class") or "").lower()
                if "sq-cardsort-completion" in cl:
                    continue
                style = (c.get_attribute("style") or "").lower()
                if "display: none" in style:
                    continue
                # fallback selenium visibility
                try:
                    if c.is_displayed():
                        return c
                except Exception:
                    return c
            except Exception:
                continue
        return None

    def _completion_visible(cs) -> bool:
        try:
            el = cs.find_elements(By.CSS_SELECTOR, ".sq-cardsort-completion")
            if not el:
                return False
            try:
                return bool(el[0].is_displayed())
            except Exception:
                return True
        except Exception:
            return False

    def _read_bucket_label(b) -> str:
        try:
            ps = b.find_elements(By.CSS_SELECTOR, ".sq-cardsort-bucket-legend")
            if ps:
                return _norm(ps[0].text or ps[0].get_attribute("innerText") or "")
        except Exception:
            pass
        try:
            raw = _norm(b.text or b.get_attribute("innerText") or "")
            return _norm((raw.splitlines()[0] if raw else ""))
        except Exception:
            return ""

    def _get_question_text(cs, card) -> str:
        parts = []
        # Question globale
        try:
            qels = driver.find_elements(By.CSS_SELECTOR, ".question-text")
            if qels:
                parts.append(_norm(qels[0].text or qels[0].get_attribute("innerText") or ""))
        except Exception:
            pass

        # Texte carte active
        try:
            parts.append(_norm(card.text or card.get_attribute("innerText") or ""))
        except Exception:
            pass

        return "  ".join([p for p in parts if p])

    def _pick_bucket(cs, question_text: str, card_text: str):
        try:
            buckets = cs.find_elements(By.CSS_SELECTOR, "li.sq-cardsort-bucket")
        except Exception:
            buckets = []

        label_to_el = {}
        for b in buckets:
            try:
                style = _norm_lc(b.get_attribute("style") or "")
                if "pointer-events: none" in style:
                    continue
                lbl = _read_bucket_label(b)
                if not lbl:
                    continue
                label_to_el[_norm_lc(lbl)] = b
            except Exception:
                continue

        if not label_to_el:
            return None

        qt = _norm_lc(question_text)

        if assignments:
            card_lc = _norm_lc(card_text)
            for assignment in assignments:
                assignment_card = _norm_lc(str((assignment or {}).get("card") or ""))
                if not assignment_card or assignment_card != card_lc:
                    continue
                for bucket_label in (assignment or {}).get("buckets") or []:
                    bucket_lc = _norm_lc(str(bucket_label or ""))
                    if bucket_lc in label_to_el:
                        return label_to_el[bucket_lc]

        # 1) Attention check explicite : si la consigne contient une option textuelle
        triggers = ["veuillez", "selectionnez", "sélectionnez", "choisissez", "pour vérifier", "attention"]
        if any(t in qt for t in triggers):
            for opt_lc, el in label_to_el.items():
                if opt_lc and opt_lc in qt:
                    return el

        # 2) Choix safe mais variable (déterministe)
        safe_order = [
            "il y a 2 ou 3 jours",
            "il y a 4 à 7 jours",
            "il y a 1 à 2 semaines",
            "il y a 2 à 3 semaines",
            "il y a 3 à 4 semaines",
            "il y a plus de 4 semaines",
            "hier",
            "aujourd'hui",
        ]
        available = [k for k in safe_order if k in label_to_el]

        # fallback : tout sauf "jamais"
        if not available:
            available = [k for k in label_to_el.keys() if "jamais" not in k and "ne se souvient" not in k]
        if not available:
            available = list(label_to_el.keys())

        # Seed stable par bot (optionnel) + question_text
        seed = (_norm_lc(os.getenv("BOT_ID", "")) or _norm_lc(os.getenv("ACCOUNT_ID", "")) or "")
        h = zlib.crc32(f"{seed}|{qt}".encode("utf-8")) & 0xffffffff
        chosen = available[h % len(available)]
        return label_to_el[chosen]

    def _click(el) -> bool:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            time.sleep(0.05)
        except Exception:
            pass
        try:
            el.click()
            return True
        except Exception:
            try:
                ActionChains(driver).move_to_element(el).click().perform()
                return True
            except Exception:
                try:
                    driver.execute_script("arguments[0].click();", el)
                    return True
                except Exception:
                    return False

    cs = _pick_cardsort_root()
    if not cs:
        return False

    progressed = False

    for _ in range(max_cards):
        if _completion_visible(cs):
            break

        card = _active_card(cs)
        if not card:
            break

        before_idx = ""
        try:
            before_idx = (card.get_attribute("index") or "").strip()
        except Exception:
            before_idx = ""

        qtxt = _get_question_text(cs, card)
        card_text = _norm(card.text or card.get_attribute("innerText") or "")
        bucket = _pick_bucket(cs, qtxt, card_text)
        if not bucket:
            break

        if not _click(bucket):
            break

        # petit wait pour l'auto-advance (page JS)
        time.sleep(0.12)

        # si la carte n'a pas changé, on retente 1 fois en cliquant l'item interne
        card2 = _active_card(cs)
        after_idx = ""
        try:
            after_idx = (card2.get_attribute("index") or "").strip() if card2 else ""
        except Exception:
            after_idx = ""

        if before_idx and after_idx and before_idx == after_idx:
            try:
                inner = bucket.find_element(By.CSS_SELECTOR, ".sq-cardsort-bucket-item")
                _click(inner)
                time.sleep(0.12)
            except Exception:
                pass
        card3 = _active_card(cs)
        after_retry_idx = ""
        try:
            after_retry_idx = (card3.get_attribute("index") or "").strip() if card3 else ""
        except Exception:
            after_retry_idx = ""

        if _completion_visible(cs):
            progressed = True
            break

        if before_idx and after_retry_idx and before_idx != after_retry_idx:
            progressed = True

    return progressed

def _norm(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s).replace("\xa0", " ").strip()
    return re.sub(r"\s+", " ", s)

def _norm_lc(s: str) -> str:
    return _norm(s).lower()

def _fold_norm_lc(s: str) -> str:
    """
    Normalisation robuste pour comparer des libellés (options):
    - NFKD + suppression des diacritiques (Île -> Ile)
    - lower + collapse spaces
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    # Harmoniser les apostrophes/quotes typographiques pour fiabiliser
    # le matching option_xpath_map (ex: "J’y" vs "J'y" sur CMIX SIMPLE_GRID).
    s = (
        s.replace("’", "'")
         .replace("`", "'")
         .replace("´", "'")
         .replace("ʼ", "'")
    )
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[\u2026]", "...", s)
    s = s.replace("\xa0", " ").strip()
    s = re.sub(r"\s+", " ", s)
    return s.lower()


def _frequency_unit_key(text: str) -> str:
    """Retourne une clé de cadence (day/week/month/year/never) si détectable."""
    t = _fold_norm_lc(text)
    if not t:
        return ""

    checks = (
        ("never", (r"\bjamais\b", r"\bnever\b", r"\baucun\b", r"\bnone\b")),
        ("day", (r"\bjour\b", r"\bjours\b", r"\bday\b", r"\bdaily\b")),
        ("week", (r"\bsemaine\b", r"\bsemaines\b", r"\bweek\b", r"\bweekly\b")),
        ("month", (r"\bmois\b", r"\bmonth\b", r"\bmonthly\b")),
        ("year", (r"\ban\b", r"\bannee\b", r"\bans\b", r"\bannees\b", r"\byear\b", r"\byearly\b")),
    )
    for key, pats in checks:
        if any(re.search(pat, t) for pat in pats):
            return key
    return ""

def _click_xpath(driver, xpath: str) -> bool:
    if not xpath:
        return False
    try:
        el = driver.find_element(By.XPATH, xpath)
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        el.click()
        return True
    except Exception:
        return False

def _set_text_xpath(driver, xpath: str, text: str) -> bool:
    if not xpath:
        return False
    try:
        el = driver.find_element(By.XPATH, xpath)
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        el.click()
        try:
            el.clear()
        except Exception:
            pass
        el.send_keys(text)
        return True
    except Exception:
        return False

def _xpath_literal(s: str) -> str:
    """
    Construit un literal XPath safe, meme si la chaîne contient des quotes.
    """
    s = s or ""
    if "'" not in s:
        return f"'{s}'"
    if '"' not in s:
        return f'"{s}"'
    parts = s.split("'")
    out = []
    for i, p in enumerate(parts):
        if p:
            out.append(f"'{p}'")
        if i != len(parts) - 1:
            out.append("\"'\"")
    return "concat(" + ", ".join(out) + ")"



def _parse_matrix_value_parts(value: str) -> tuple[str, str]:
    raw = (value or "").strip()
    if "||" not in raw:
        return "", raw
    left, right = raw.split("||", 1)
    return (left or "").strip(), (right or "").strip()


def _try_gridclick_matrix_set(driver, row_label: str, col_label: str) -> bool:
    row_label = (row_label or "").strip()
    col_label = (col_label or "").strip()
    if not col_label:
        return False

    def _read_text(el) -> str:
        try:
            txt = el.find_element(By.CSS_SELECTOR, ".text-content").get_attribute("innerText") or ""
        except Exception:
            txt = ""
        if not txt:
            try:
                txt = el.get_attribute("innerText") or ""
            except Exception:
                txt = ""
        return _norm(txt)

    def _click_trusted(el) -> bool:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", el)
        except Exception:
            pass
        try:
            el.click()
            return True
        except Exception:
            pass
        try:
            ActionChains(driver).move_to_element(el).pause(0.05).click().perform()
            return True
        except Exception:
            pass
        try:
            driver.execute_script("arguments[0].click();", el)
            return True
        except Exception:
            return False

    def _current_row_text(root_el) -> str:
        try:
            cur = root_el.find_element(By.CSS_SELECTOR, ".item.current .text-content")
            return _norm(cur.get_attribute("innerText") or cur.text or "")
        except Exception:
            return ""

    def _match_fold(candidate: str, needle: str) -> bool:
        a = _fold_norm_lc(candidate)
        b = _fold_norm_lc(needle)
        if not a or not b:
            return False
        return a == b or a in b or b in a

    try:
        root = driver.find_element(By.CSS_SELECTOR, ".gridclick.horizontal.text-version")
    except Exception:
        return False

    buttons = root.find_elements(By.CSS_SELECTOR, ".scale-button")
    if len(buttons) < 2:
        return False

    row_before = _current_row_text(root)
    row_after = row_before
    if row_label:
        row_target = None
        for item in root.find_elements(By.CSS_SELECTOR, ".item.item-text"):
            if _match_fold(_read_text(item), row_label):
                row_target = item
                break
        if row_target is None:
            log_info("[GRIDCLICK]", f"apply_failed reason='row_not_found' row_target={row_label!r}")
            return False
        if not _click_trusted(row_target):
            log_info("[GRIDCLICK]", f"apply_failed reason='row_click_failed' row_target={row_label!r}")
            return False
        target_fold = _fold_norm_lc(row_label)
        deadline = time.time() + 0.6
        while time.time() < deadline:
            row_after = _current_row_text(root)
            if _fold_norm_lc(row_after) == target_fold:
                break
            time.sleep(0.05)
    else:
        row_after = _current_row_text(root)

    log_info("[GRIDCLICK]", f"row_target={row_label!r} row_before={row_before!r} row_after={row_after!r}")
    log_info("[MATRIX_ACTIVE_ROW]", f"row_active={row_after!r}")

    target_btn = None
    btn_text = ""
    btn_index = ""
    for btn in buttons:
        txt = _read_text(btn)
        if _match_fold(txt, col_label):
            target_btn = btn
            btn_text = txt
            btn_index = (btn.get_attribute("data-index") or "").strip()
            break

    if target_btn is None:
        log_info("[GRIDCLICK]", f"apply_failed reason='col_not_found' col_target={col_label!r}")
        return False

    log_info("[GRIDCLICK]", f"col_target={col_label!r} btn_text={btn_text!r} btn_index={btn_index!r}")
    if not _click_trusted(target_btn):
        log_info("[GRIDCLICK]", f"apply_failed reason='col_click_failed' col_target={col_label!r}")
        return False

    btn_selected = False
    item_answered = False
    progress_answered = False
    deadline = time.time() + 0.8
    while time.time() < deadline:
        try:
            btn_selected = "selected" in (target_btn.get_attribute("class") or "")
        except Exception:
            btn_selected = False
        try:
            item_answered = "answered" in (root.find_element(By.CSS_SELECTOR, ".item.current").get_attribute("class") or "")
        except Exception:
            item_answered = False
        try:
            progress_answered = "answeredNode" in (root.find_element(By.CSS_SELECTOR, ".node-container.currentNode").get_attribute("class") or "")
        except Exception:
            progress_answered = False
        if btn_selected or item_answered or progress_answered:
            break
        time.sleep(0.05)

    log_info(
        "[GRIDCLICK]",
        f"verify btn_selected={btn_selected} item_answered={item_answered} progress_answered={progress_answered}",
    )
    return bool(btn_selected or item_answered or progress_answered)


def _try_table_matrix_sge_set(driver, target_payload: dict, row_label: str, col_label: str) -> bool:
    """Applique une cellule de matrice sur le pattern table_matrix_sge (Alchemer/SGE-like).

    Garde-fous DOM:
    - activé uniquement si le target payload est marqué table_matrix_sge
    - nécessite un <tr> contenant des radios avec @name
    - cible la radio par croisement row label + aria-label (colonne)
    """
    if not isinstance(target_payload, dict) or not target_payload.get("table_matrix_sge"):
        return False

    def _matrix_label_norm(text: str) -> str:
        """Normalisation locale robuste pour matching de labels matrix SGE."""
        base = _fold_norm_lc(text)
        if not base:
            return ""
        # Harmonise les espaces unicode (NBSP, thin space, narrow NBSP, etc.).
        return re.sub(r"[\s\u00A0\u1680\u2000-\u200A\u202F\u205F\u3000]+", " ", base).strip()

    row_need = _matrix_label_norm(row_label)
    col_need = _matrix_label_norm(col_label)
    if not row_need or not col_need:
        return False

    def _matches(candidate: str, needle: str) -> bool:
        cand = _matrix_label_norm(candidate)
        if not cand or not needle:
            return False
        return cand == needle or cand in needle or needle in cand

    try:
        rows = driver.find_elements(
            By.XPATH,
            "//tr[.//input[@type='radio'][@name]]",
        )
    except Exception:
        rows = []

    if not rows:
        log_debug("[SGE_MATRIX]", "rows empty for table_matrix_sge candidate")
        return False

    matched_row = False
    for row in rows:
        try:
            row_text = driver.execute_script(
                """
                const tr = arguments[0];
                if (!tr) return '';
                const labelCell = tr.querySelector('th, td');
                return (labelCell && (labelCell.innerText || labelCell.textContent) || '').trim();
                """,
                row,
            ) or ""
        except Exception:
            row_text = ""

        if not _matches(row_text, row_need):
            continue

        matched_row = True

        try:
            radio = driver.execute_script(
                r"""
                const tr = arguments[0];
                const need = arguments[1];
                if (!tr) return null;
                const radios = Array.from(tr.querySelectorAll("input[type='radio'][name]"));
                const norm = (txt) => {
                  return (txt || '')
                    .normalize('NFD')
                    .replace(/[\u0300-\u036f]/g, '')
                    .replace(/[\u2018\u2019\u201A\u201B\u2032\u2035\u02BC\uFF07]/g, "'")
                    .replace(/[\s\u00A0\u1680\u2000-\u200A\u202F\u205F\u3000]+/g, ' ')
                    .trim()
                    .toLowerCase();
                };
                const needNorm = norm(need);
                const pick = radios.find((r) => {
                  const aria = (r.getAttribute('aria-label') || r.getAttribute('data-label') || '').trim();
                  const ariaNorm = norm(aria);
                  if (!!ariaNorm && !!needNorm && (ariaNorm === needNorm || ariaNorm.includes(needNorm) || needNorm.includes(ariaNorm))) {
                    return true;
                  }
                  const cellOptText = (r.closest('td')?.querySelector('.opt-text')?.textContent || '').trim();
                  const optNorm = norm(cellOptText);
                  return !!optNorm && !!needNorm && (optNorm === needNorm || optNorm.includes(needNorm) || needNorm.includes(optNorm));
                });
                return pick || null;
                """,
                row,
                col_need,
            )
        except Exception:
            radio = None

        if radio is None:
            log_debug("[SGE_MATRIX]", f"no radio matched col_need='{col_need}' in matched row")
            continue

        try:
            ok = bool(driver.execute_script(
                """
                const input = arguments[0];
                if (!input) return false;
                try { input.scrollIntoView({block:'center', inline:'center'}); } catch(e) {}
                const table = input.closest('table.i-question-table');
                const cell = input.closest('td.i-option-cell[tabindex]');
                const isIntelliSurveyCell = !!(table && cell);
                const id = input.getAttribute('id') || '';
                const label = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
                try {
                  if (isIntelliSurveyCell) {
                    cell.click();
                  } else if (label) {
                    label.click();
                  } else {
                    input.click();
                  }
                } catch(e) {}
                if (!input.checked) {
                  try { input.checked = true; } catch(e) {}
                  try { input.dispatchEvent(new Event('input', { bubbles: true })); } catch(e) {}
                  try { input.dispatchEvent(new Event('change', { bubbles: true })); } catch(e) {}
                }
                return !!input.checked;
                """,
                radio,
            ))
        except Exception:
            ok = False

        if ok:
            log_info("[TARGET]", "apply ok=true strategy=table_matrix_sge reason=applied")
            return True

    if not matched_row:
        log_debug("[SGE_MATRIX]", f"no row matched row_need='{row_need}'")

    return False


def _try_encuesta_matrix_set(driver, row_label: str, col_label: str) -> bool:
    """
    Encuesta.com (Vuetify) matrix rows:
    - row container: .layout.ee__matrix--row:not(.hidden-sm-and-down)
    - row label: .ee__matrix--first-column span
    - target cell input: .ee__matrix--column input[type=radio][name='<col>']
    - effective click target: .v-input--selection-controls__input
    """
    row_label = (row_label or "").strip()
    col_label = (col_label or "").strip()
    if not row_label or not col_label:
        return False

    target_row_fold = _fold_norm_lc(row_label)
    try:
        rows = driver.find_elements(By.CSS_SELECTOR, ".layout.ee__matrix--row:not(.hidden-sm-and-down)")
    except Exception:
        rows = []

    matched_row = None
    for row in rows:
        try:
            row_title = row.find_element(By.CSS_SELECTOR, ".ee__matrix--first-column span")
            row_text = _norm(row_title.text or row_title.get_attribute("innerText") or "")
        except Exception:
            continue
        row_fold = _fold_norm_lc(row_text)
        if row_fold and (row_fold == target_row_fold or target_row_fold in row_fold):
            matched_row = row
            break

    if matched_row is None:
        log_info("[TARGET]", f"apply ok=false strategy=encuesta_matrix reason=row_not_found row={row_label!r} col={col_label!r}")
        return False

    target_col_fold = _fold_norm_lc(col_label)
    target_col_name = None
    if target_col_fold:
        try:
            header_cells = driver.find_elements(
                By.CSS_SELECTOR,
                ".layout.ee__matrix--row.hidden-sm-and-down .ee__matrix--header-cells span",
            )
        except Exception:
            header_cells = []

        for idx, header in enumerate(header_cells, start=1):
            try:
                header_text = _norm(header.text or header.get_attribute("innerText") or "")
            except Exception:
                continue
            if _fold_norm_lc(header_text) == target_col_fold:
                target_col_name = str(idx)
                break

    if not target_col_name:
        log_info("[TARGET]", f"apply ok=false strategy=encuesta_matrix reason=col_not_found row={row_label!r} col={col_label!r}")
        return False

    try:
        target_input = matched_row.find_element(
            By.CSS_SELECTOR,
            f".ee__matrix--column input[type='radio'][name='{target_col_name}']",
        )
    except Exception:
        log_info("[TARGET]", f"apply ok=false strategy=encuesta_matrix reason=input_not_found row={row_label!r} col={col_label!r}")
        return False

    try:
        click_target = target_input.find_element(By.XPATH, "./ancestor::*[contains(@class,'v-input--selection-controls__input')][1]")
    except Exception:
        click_target = target_input

    _aa__safe_scroll_center(driver, click_target)
    if not _aa__safe_click(driver, click_target):
        log_info("[TARGET]", f"apply ok=false strategy=encuesta_matrix reason=click_failed row={row_label!r} col={col_label!r}")
        return False

    try:
        checked = bool(driver.execute_script(
            """
            const input = arguments[0];
            if (!input) return false;
            if (input.checked) return true;
            const radio = input.closest('.v-radio');
            const classes = (radio && radio.className) ? String(radio.className) : '';
            return classes.includes('v-item--active');
            """,
            target_input,
        ))
    except Exception:
        checked = False

    if not checked:
        log_info("[TARGET]", f"apply ok=false strategy=encuesta_matrix reason=not_checked row={row_label!r} col={col_label!r}")
        return False

    log_info("[TARGET]", "apply ok=true strategy=encuesta_matrix reason=applied")
    return True


def _apply_by_target_id(
    driver,
    target_id: str,
    itype: str,
    value: str,
    *,
    allow_mx_vertical_carousel_advance: bool = True,
) -> bool:
    """
    Applique l'action directement via DOM_REGISTRY (target_id -> xpath).
    Returns True si une action est exécutée.

    Support iframe: si le payload du registry contient frame_chain, on se positionne
    dans ce contexte le temps d'appliquer l'action.
    """
    try:
        payload = get_target(target_id)
        if not payload:
            return False

        # (Optionnel) exécution dans un iframe spécifique
        frame_chain = payload.get("frame_chain") or []
        
        # NEW: si le registry dit "pas d'iframe", on s'assure de revenir au default_content
        if not frame_chain:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass

        try:
            from Survey.frame_utils import switch_to_frame_chain  # type: ignore
        except Exception:
            switch_to_frame_chain = None  # type: ignore

        def _apply_in_current_context() -> bool:
            kind = payload.get("kind")
            reg_itype = (payload.get("itype") or "").lower()
            resolved_itype = (itype or reg_itype).lower().strip()

            debug_target = is_debug()

            def _apply_toluna_runtime_answerrow_cached() -> bool:
                """
                Toluna Runtime AnswerRow checkbox:
                - DOM custom sans input natif exploitable dans la row cible
                - cache uniquement le chemin DOM/strategy, jamais un WebElement stale
                - réutilisable pour les sélections suivantes du même target_id
                """
                if resolved_itype != "checkbox":
                    return False

                cache_key = f"{target_id}|toluna_runtime_answerrow|checkbox"
                path_cache = getattr(driver, "_target_path_cache", None)
                if not isinstance(path_cache, dict):
                    path_cache = {}
                    setattr(driver, "_target_path_cache", path_cache)

                cached = path_cache.get(cache_key)
                if cached:
                    log_debug("[TARGET_DEBUG]", f"toluna_runtime_answerrow cache hit target_id={target_id}")

                try:
                    data = driver.execute_script(
                        r"""
                        const norm = s => (s || '').toLowerCase().normalize('NFKC')
                          .replace(/\u00A0/g, ' ')
                          .replace(/[»«\u201c\u201d"'›→·•:]/g, '')
                          .replace(/\s+/g, ' ')
                          .trim();

                        const needle = norm(arguments[0]);
                        const rows = Array.from(document.querySelectorAll("[data-aut='Runtime_AnswerRow']"));
                        if (!needle) return { ok: false, reason: 'empty_needle' };
                        if (rows.length < 2) return { ok: false, reason: 'no_runtime_rows' };

                        const targetRow = rows.find(r => {
                          const txt = norm(r.innerText || r.textContent || '');
                          return txt === needle || txt.includes(needle) || needle.includes(txt);
                        });
                        if (!targetRow) return { ok: false, reason: 'row_not_found' };

                        // Ce handler est volontairement limité aux rows custom Runtime.
                        // Les rows avec input natif restent gérées par les chemins existants.
                        if (targetRow.querySelector("input[type='checkbox'], input[type='radio']")) {
                          return { ok: false, reason: 'native_input_present' };
                        }

                        const wrapper = targetRow.querySelector("[data-aut='Runtime_Wrapper']");
                        const inner = targetRow.querySelector("[data-aut='Runtime_IconBox'], [data-aut='Runtime_InnerFill']");
                        if (!wrapper || !inner) return { ok: false, reason: 'missing_runtime_state_nodes' };

                        const inners = rows.map(r => {
                          const w = r.querySelector("[data-aut='Runtime_Wrapper']");
                          return w ? w.querySelector("[data-aut='Runtime_IconBox'], [data-aut='Runtime_InnerFill']") : null;
                        }).filter(Boolean);
                        if (inners.length < 2) return { ok: false, reason: 'not_enough_state_nodes' };

                        const counts = {};
                        for (const i of inners) {
                          const cls = i.className || '';
                          counts[cls] = (counts[cls] || 0) + 1;
                        }
                        const uncheckedCls = Object.keys(counts).reduce((a, b) => counts[b] > counts[a] ? b : a);
                        const alreadyChecked = (inner.className || '') !== uncheckedCls;

                        return {
                          ok: true,
                          row: targetRow,
                          inner: inner,
                          clsBefore: inner.className || '',
                          alreadyChecked: alreadyChecked
                        };
                        """,
                        value,
                    )
                except Exception as e:
                    log_debug("[TARGET_DEBUG]", f"toluna_runtime_answerrow probe failed: {type(e).__name__}: {e}")
                    return False

                if not isinstance(data, dict) or not data.get("ok"):
                    if cached:
                        path_cache.pop(cache_key, None)
                    reason = data.get("reason") if isinstance(data, dict) else "invalid_probe_result"
                    log_debug("[TARGET_DEBUG]", f"toluna_runtime_answerrow skip reason={reason!r} label={value!r}")
                    return False

                if data.get("alreadyChecked"):
                    path_cache[cache_key] = {"kind": "toluna_runtime_answerrow"}
                    log_debug("[TARGET_DEBUG]", f"toluna_runtime_answerrow already_checked label={value!r}")
                    return True

                row_el = data.get("row")
                inner_el = data.get("inner")
                cls_before = data.get("clsBefore") or ""
                if row_el is None or inner_el is None:
                    return False

                try:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", row_el)
                    try:
                        row_el.click()
                    except Exception:
                        ActionChains(driver).move_to_element(row_el).click().perform()
                    time.sleep(0.15)
                    cls_after = driver.execute_script("return arguments[0].className || '';", inner_el)
                except Exception as e:
                    log_debug("[TARGET_DEBUG]", f"toluna_runtime_answerrow click failed label={value!r} err={type(e).__name__}: {e}")
                    return False

                if cls_after != cls_before:
                    path_cache[cache_key] = {"kind": "toluna_runtime_answerrow"}
                    log_debug("[TARGET_DEBUG]", f"toluna_runtime_answerrow applied cache_set target_id={target_id} label={value!r}")
                    return True

                log_debug("[TARGET_DEBUG]", f"toluna_runtime_answerrow not_applied label={value!r}")
                return False

            def _find_best_visible(xpath: str):
                try:
                    cands = driver.find_elements(By.XPATH, xpath)
                except Exception:
                    cands = []

                if not cands:
                    return None

                def _rect_ok(el) -> bool:
                    try:
                        r = el.rect or {}
                        return (r.get("width", 0) or 0) > 2 and (r.get("height", 0) or 0) > 2
                    except Exception:
                        return False

                def _click_priority(el) -> int:
                    """Priorise les nœuds réellement cliquables pour radios/checkbox matrix."""
                    try:
                        tag = (el.tag_name or "").lower()
                    except Exception:
                        tag = ""
                    try:
                        cls = ((el.get_attribute("class") or "").lower())
                    except Exception:
                        cls = ""

                    score = 0
                    if tag in ("label", "input", "button"):
                        score += 100
                    if "clickablecell" in cls or "cell-sub-wrapper" in cls:
                        score += 80
                    if tag == "td" and "clickablecell" not in cls:
                        score -= 40
                    return score

                def _pick_best(scored):
                    if not scored:
                        return None
                    # max(..., key=...) garde le 1er élément en cas d'égalité:
                    # on conserve l'ordre DOM comme tie-breaker stable.
                    return max(scored, key=lambda item: item[0])[1]

                # 1) affiché + taille > 2px (évite label 0x0)
                visible_rect = []
                for c in cands:
                    try:
                        if c.is_displayed() and _rect_ok(c):
                            visible_rect.append((_click_priority(c), c))
                    except Exception:
                        continue
                best = _pick_best(visible_rect)
                if best:
                    return best

                # 2) affiché
                visible_any = []
                for c in cands:
                    try:
                        if c.is_displayed():
                            visible_any.append((_click_priority(c), c))
                    except Exception:
                        continue
                best = _pick_best(visible_any)
                if best:
                    return best

                # 3) fallback
                return _pick_best([(_click_priority(c), c) for c in cands])

            def _wait_checked(input_id: str | None, input_name: str | None, timeout_s: float = 1.2) -> bool:
                import time
                end = time.time() + timeout_s

                while time.time() < end:
                    try:
                        if input_id:
                            ok = driver.execute_script(
                                "var e=document.getElementById(arguments[0]); return !!(e && e.checked);",
                                input_id,
                            )
                            if ok:
                                return True

                        if input_name:
                            ok = driver.execute_script(
                                "return !!document.querySelector("
                                "  \"input[type='radio'][name=\\\"\"+arguments[0]+\"\\\"]:checked, \" +"
                                "  \"input[type='checkbox'][name=\\\"\"+arguments[0]+\"\\\"]:checked\""
                                ");",
                                input_name,
                            )
                            if ok:
                                return True
                    except Exception:
                        pass

                    time.sleep(0.05)

                return False

            v_norm = _norm_lc(value)
            v_fold = _fold_norm_lc(value)

            if payload.get("mx_carousel_active") and resolved_itype == "radio":
                row_xpath = (payload.get("mx_carousel_row_xpath") or "").strip()
                scale_map = payload.get("mx_carousel_scale_xpath_map") or {}
                input_name = (payload.get("mx_carousel_input_name") or "").strip()
                input_id_map = payload.get("mx_carousel_input_id_map") or {}

                scale_xpath = scale_map.get(v_norm) or (scale_map.get(v_fold) if v_fold else None)
                if not scale_xpath:
                    for k, x in scale_map.items():
                        if not k:
                            continue
                        k_norm = _norm_lc(k)
                        k_fold = _fold_norm_lc(k)
                        if v_norm and (v_norm == k_norm or v_norm in k_norm or k_norm in v_norm):
                            scale_xpath = x
                            break
                        if v_fold and (
                            v_fold == k_norm or v_fold in k_norm or k_norm in v_fold
                            or v_fold == k_fold or v_fold in k_fold or k_fold in v_fold
                        ):
                            scale_xpath = x
                            break

                if not row_xpath or not scale_xpath:
                    if debug_target:
                        log_debug(
                            "[TARGET_DEBUG]",
                            f"target_id='{target_id}' value='{value}' -> mx carousel mapping introuvable",
                        )
                    return False

                def _click_xpath_node(xpath: str) -> bool:
                    node = _find_best_visible(xpath)
                    if node is None:
                        return False
                    try:
                        driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", node)
                    except Exception:
                        pass
                    try:
                        node.click()
                        return True
                    except Exception:
                        try:
                            driver.execute_script("arguments[0].click();", node)
                            return True
                        except Exception:
                            return False

                expected_input_id = None
                try:
                    expected_input_id = input_id_map.get(v_norm) or (input_id_map.get(v_fold) if v_fold else None)
                except Exception:
                    expected_input_id = None

                initial_checked_id = None
                if input_name:
                    try:
                        initial_checked_id = driver.execute_script(
                            """
                            const n = arguments[0];
                            if (!n) return null;
                            const sel = document.querySelector("input[type='radio'][name='" + n + "']:checked");
                            return sel ? (sel.id || null) : null;
                            """,
                            input_name,
                        )
                    except Exception:
                        initial_checked_id = None

                if not _click_xpath_node(row_xpath):
                    if debug_target:
                        log_debug("[TARGET_DEBUG]", f"mx carousel row click failed: target_id='{target_id}'")
                    return False

                if not _click_xpath_node(scale_xpath):
                    if debug_target:
                        log_debug("[TARGET_DEBUG]", f"mx carousel scale click failed: target_id='{target_id}' value='{value}'")
                    return False

                end = time.time() + 1.2
                while time.time() < end:
                    try:
                        checked_id = driver.execute_script(
                            """
                            const n = arguments[0];
                            if (!n) return null;
                            const sel = document.querySelector("input[type='radio'][name='" + n + "']:checked");
                            return sel ? (sel.id || null) : null;
                            """,
                            input_name,
                        )
                    except Exception:
                        checked_id = None

                    if checked_id and checked_id != initial_checked_id:
                        if not expected_input_id or checked_id == expected_input_id:
                            return True
                    time.sleep(0.05)

                if debug_target:
                    log_debug(
                        "[TARGET_DEBUG]",
                        f"mx carousel state unchanged: target_id='{target_id}' initial={initial_checked_id!r} expected={expected_input_id!r}",
                    )
                return False

            is_purespectrum_date_dropdown = payload.get("purespectrum_date_dropdown") and resolved_itype == "radio"
            is_ps_select_dropdown = bool(payload.get("ps_select_dropdown"))

            if is_purespectrum_date_dropdown or is_ps_select_dropdown:
                opt_map = payload.get("option_xpath_map") or {}
                toggle_xpath = (payload.get("dropdown_toggle_xpath") or "").strip()

                xp = opt_map.get(v_norm) or (opt_map.get(v_fold) if v_fold else None)
                if not xp:
                    for k, x in opt_map.items():
                        if not k:
                            continue
                        k_norm = _norm_lc(k)
                        k_fold = _fold_norm_lc(k)
                        if v_norm and (v_norm == k_norm or v_norm in k_norm or k_norm in v_norm):
                            xp = x
                            break
                        if v_fold and (
                            v_fold == k_norm or v_fold in k_norm or k_norm in v_fold
                            or v_fold == k_fold or v_fold in k_fold or k_fold in v_fold
                        ):
                            xp = x
                            break

                if not xp:
                    if debug_target:
                        log_debug("[TARGET_DEBUG]", f"target_id='{target_id}' kind='{kind}' itype='{resolved_itype}' value='{value}' -> purespectrum xpath dropdown option introuvable")
                    return False

                def _click_xpath(xpath: str) -> bool:
                    if not xpath:
                        return False
                    node = _find_best_visible(xpath)
                    if node is None:
                        try:
                            cands = driver.find_elements(By.XPATH, xpath)
                            node = cands[0] if cands else None
                        except Exception:
                            node = None
                    if node is None:
                        return False
                    try:
                        driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", node)
                    except Exception:
                        pass
                    try:
                        node.click()
                        return True
                    except Exception:
                        try:
                            driver.execute_script("arguments[0].click();", node)
                            return True
                        except Exception:
                            return False

                if toggle_xpath:
                    _click_xpath(toggle_xpath)
                    time.sleep(0.1)

                clicked = _click_xpath(xp)
                if clicked:
                    return True

                if debug_target:
                    log_debug("[TARGET_DEBUG]", f"target_id='{target_id}' value='{value}' -> purespectrum xpath dropdown click failed")
                return False

            # --- select_rps: dropdown Angular custom rps-select (Toluna/SurveyRouter) ---
            # reg_itype est lu directement du registry (jamais altéré par GPT) : c'est le signal fiable.
            # resolved_itype == "select_rps" garde la compat si GPT le renvoie tel quel.
            # payload.get("rps_select") est le flag explicite posé par l'extracteur.
            if resolved_itype == "select_rps" or reg_itype == "select_rps" or payload.get("rps_select"):
                opt_map = payload.get("option_xpath_map") or {}
                selection_xpath = (payload.get("selection_xpath") or "").strip()

                xp = opt_map.get(v_norm) or (opt_map.get(v_fold) if v_fold else None)
                if not xp:
                    for k, x in opt_map.items():
                        if not k:
                            continue
                        k_norm = _norm_lc(k)
                        k_fold = _fold_norm_lc(k)
                        if v_norm and (v_norm == k_norm or v_norm in k_norm or k_norm in v_norm):
                            xp = x
                            break
                        if v_fold and (
                            v_fold == k_norm or v_fold in k_norm or k_norm in v_fold
                            or v_fold == k_fold or v_fold in k_fold or k_fold in v_fold
                        ):
                            xp = x
                            break

                if not xp:
                    if debug_target:
                        log_debug("[TARGET_DEBUG]", f"target_id='{target_id}' kind='{kind}' itype='select_rps' value='{value}' -> option introuvable (opt_map={len(opt_map)})")
                    return False

                def _click_rps_node(xpath: str) -> bool:
                    """Ouvre le dropdown (click standard sur div.selection)."""
                    if not xpath:
                        return False
                    node = _find_best_visible(xpath)
                    if node is None:
                        try:
                            cands = driver.find_elements(By.XPATH, xpath)
                            node = cands[0] if cands else None
                        except Exception:
                            node = None
                    if node is None:
                        return False
                    try:
                        driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", node)
                    except Exception:
                        pass
                    try:
                        node.click()
                        return True
                    except Exception:
                        try:
                            driver.execute_script("arguments[0].click();", node)
                            return True
                        except Exception:
                            return False

                # Extraire data_selector depuis group_key ("rps_select:{data_selector}")
                # pour la vérification hold-model ng-valid après dispatch.
                _gk = (payload.get("group_key") or "")
                _rps_ds = _gk.split(":", 1)[1] if _gk.startswith("rps_select:") else ""

                def _mousedown_rps_option(xpath: str) -> bool:
                    """Sélectionne une option via mousedown (handler ng-mousedown d'Angular).
                    Vérifie la sélection effective via hold-model ng-valid (max 600 ms).
                    """
                    if not xpath:
                        return False
                    try:
                        cands = driver.find_elements(By.XPATH, xpath)
                        node = cands[0] if cands else None
                    except Exception:
                        node = None
                    if node is None:
                        return False
                    try:
                        driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", node)
                    except Exception:
                        pass
                    # Tenter mousedown puis click comme fallback
                    dispatched = False
                    try:
                        driver.execute_script(
                            "arguments[0].dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true,view:window}));",
                            node,
                        )
                        dispatched = True
                    except Exception:
                        pass
                    if not dispatched:
                        try:
                            node.click()
                            dispatched = True
                        except Exception:
                            try:
                                driver.execute_script("arguments[0].click();", node)
                                dispatched = True
                            except Exception:
                                pass
                    if not dispatched:
                        return False
                    # Vérifier la sélection effective via hold-model ng-valid (budget 600 ms / 12 x 50 ms)
                    if _rps_ds:
                        _dl = time.time() + 0.6
                        while time.time() < _dl:
                            try:
                                confirmed = driver.execute_script(
                                    """
                                    var ds = arguments[0];
                                    var inp = document.querySelector(
                                        "div[data-selector='" + ds + "'] input.hold-model"
                                    );
                                    if (!inp) return false;
                                    return inp.classList.contains('ng-valid') &&
                                           (inp.value || '').trim() !== '';
                                    """,
                                    _rps_ds,
                                )
                                if confirmed:
                                    return True
                            except Exception:
                                pass
                            time.sleep(0.05)
                        return False
                    # Pas de data_selector : on retourne True sur la bonne foi du dispatch
                    return True

                if selection_xpath:
                    _click_rps_node(selection_xpath)
                    # Attendre que JSP recalcule les dimensions des options (max 1.5 s, budget 15 x 100 ms)
                    _deadline = time.time() + 1.5
                    while time.time() < _deadline:
                        try:
                            cands = driver.find_elements(By.XPATH, xp)
                            if cands:
                                r = cands[0].rect or {}
                                if (r.get("width") or 0) > 2 and (r.get("height") or 0) > 2:
                                    break
                        except Exception:
                            pass
                        time.sleep(0.1)

                clicked = _mousedown_rps_option(xp)
                if clicked:
                    log_debug("[DOM_RPS_SELECT]", f"select_rps ok: target_id='{target_id}' value='{value}'")
                    return True

                if debug_target:
                    log_debug("[TARGET_DEBUG]", f"target_id='{target_id}' value='{value}' -> rps-select click failed")
                return False

            if payload.get("confirmit_slider_grid") and resolved_itype == "radio":
                row_id = (payload.get("slider_grid_row_id") or "").strip()
                scale_labels = [str(x or "").strip() for x in (payload.get("slider_grid_scale_labels") or []) if str(x or "").strip()]
                code_to_index = {
                    (str(k or "").strip().lower()): int(v)
                    for k, v in (payload.get("slider_grid_code_to_index") or {}).items()
                    if str(k or "").strip()
                }
                # idx_to_code: 1-based list position → actual slider code (= aria-valuenow target)
                # scale_code_to_index from payload maps code_str → 1-based list pos; we need the inverse.
                raw_ctoi = payload.get("slider_grid_code_to_index") or {}
                idx_to_code: dict[int, int] = {}
                for _c, _p in raw_ctoi.items():
                    try:
                        idx_to_code[int(_p)] = int(_c)
                    except (TypeError, ValueError):
                        pass

                selected_index: int | None = None

                if not selected_index and v_norm:
                    try:
                        digits = re.sub(r"\D+", "", v_norm)
                    except Exception:
                        digits = ""
                    if digits:
                        if digits in code_to_index:
                            # digits IS the scale code (= aria-valuenow target), not the list position
                            selected_index = int(digits)
                        else:
                            try:
                                maybe_idx = int(digits)
                                if 1 <= maybe_idx <= max(1, len(scale_labels)):
                                    # maybe_idx is a 1-based list position → convert to slider code
                                    selected_index = idx_to_code.get(maybe_idx, maybe_idx - 1)
                            except Exception:
                                pass

                if selected_index is None and scale_labels:
                    # Correspondance exacte uniquement : les labels numériques ("0%", "10%"…)
                    # produisent des faux-positifs avec le match sous-chaîne ("0%" ⊂ "60%").
                    # enumerate start=1 pour aligner avec idx_to_code (1-based list position).
                    for list_pos, opt in enumerate(scale_labels, start=1):
                        o_norm = _norm_lc(opt)
                        o_fold = _fold_norm_lc(opt)
                        if not o_norm:
                            continue
                        if v_norm and v_norm == o_norm:
                            selected_index = idx_to_code.get(list_pos, list_pos - 1)
                            break
                        if v_fold and v_fold == o_fold:
                            selected_index = idx_to_code.get(list_pos, list_pos - 1)
                            break

                if not row_id:
                    log_debug("[TARGET_DEBUG]", f"slider-grid row skipped row_id=<missing> value='{value}' reason='missing_row_id'")
                    return False
                if selected_index is None:
                    log_debug("[TARGET_DEBUG]", f"slider-grid row skipped row_id={row_id} value='{value}' reason='unmapped_value'")
                    return False

                try:
                    from selenium.webdriver.common.keys import Keys
                    row_el = driver.find_element(By.CSS_SELECTOR, f"[id='{row_id}']")
                    handle_el = row_el.find_element(By.CSS_SELECTOR, ".cf-slider__handle[role='slider']")

                    driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center',inline:'center'});", handle_el
                    )
                    time.sleep(0.05)

                    min_v = int(handle_el.get_attribute("aria-valuemin") or 0)
                    max_v = int(handle_el.get_attribute("aria-valuemax") or 0)
                    if max_v <= min_v:
                        log_debug("[TARGET_DEBUG]", f"slider-grid row skipped row_id={row_id} value='{value}' reason='invalid_slider_bounds'")
                        return False

                    # selected_index est 0-based = aria-valuenow cible directe
                    desired = max(min_v, min(max_v, selected_index))

                    # Lecture de la position courante (aria-valuenow=-1 en état no-value)
                    cur_str = handle_el.get_attribute("aria-valuenow")
                    try:
                        current = int(cur_str)
                    except (TypeError, ValueError):
                        current = -1

                    # .cf-slider__no-value n'a PAS de tabindex en état initial → pas de listeners actifs.
                    # Il ne gagne tabindex="0" qu'APRÈS activation, pour permettre de revenir à no-value.
                    # Seul le handle est interactif : focus JS + send_keys arrow keys directement.
                    # La 1ère pression depuis -1 active le composant (→0), les suivantes naviguent.
                    # delta = desired - (-1) = desired+1 pressions pour atteindre desired depuis -1.
                    driver.execute_script("arguments[0].focus();", handle_el)
                    time.sleep(0.05)

                    delta = desired - current
                    if delta != 0:
                        key = Keys.ARROW_RIGHT if delta > 0 else Keys.ARROW_LEFT
                        # budget : crans normaux + 1 cran d'activation depuis -1
                        max_steps = (max_v - min_v) + 2
                        for _ in range(min(abs(delta), max_steps)):
                            handle_el.send_keys(key)
                            time.sleep(0.05)  # laisser le composant Confirmit traiter le keydown

                    # Relire depuis le DOM : sécurité stale reference si re-render à l'activation
                    handle_el = row_el.find_element(By.CSS_SELECTOR, ".cf-slider__handle[role='slider']")
                    now_val = driver.execute_script(
                        "return arguments[0].getAttribute('aria-valuenow');", handle_el
                    )
                except Exception as e:
                    log_debug("[TARGET_DEBUG]", f"slider-grid row skipped row_id={row_id} value='{value}' reason='exception:{_short_exc(e)}'")
                    return False

                ok = str(now_val) == str(desired)
                if ok:
                    log_debug("[TARGET_DEBUG]", f"slider-grid row applied row_id={row_id} value='{value}'")
                    return True

                log_debug("[TARGET_DEBUG]", f"slider-grid row skipped row_id={row_id} value='{value}' reason='aria_mismatch:desired={desired},now={now_val}'")
                return False

            # --- Decipher MX Collapsible (radio) : clic natif sur la carte par texte de div.label ---
            # Guard DOM strict : déclenché seulement quand .mx-stage .mx-collapsible-container
            # est présent dans la question courante.  Scope : group_key issu de l'extracteur
            # focusvision_answers_list (préfixe "radio:name:").
            # Si la carte est trouvée, la logique est exclusive (return True/False) ; sinon
            # fall-through vers le chemin opt_map standard.
            if resolved_itype == "radio" and (payload.get("group_key") or "").startswith("radio:name:"):
                _input_name_mx = (payload.get("input_name") or "").strip()
                if _input_name_mx:
                    _mx_card_el = None
                    try:
                        _mx_card_el = driver.execute_script(
                            r"""
                            const rawValue = arguments[0];
                            if (!rawValue) return null;
                            const normVal = rawValue.replace(/\s+/g, ' ').trim().toLowerCase();

                            // Chercher directement dans tous les conteneurs MX Collapsible
                            // de la page (sans dépendre de l'existence d'inputs radio dans le DOM).
                            const containers = document.querySelectorAll(
                                '.mx-stage .mx-collapsible-container'
                            );
                            for (const mx of containers) {
                                const cards = mx.querySelectorAll(
                                    '.mx-collapsible-groupholder .mx-collapsible-row-item'
                                );
                                for (const card of cards) {
                                    const labelEl = card.querySelector('.label');
                                    if (!labelEl) continue;
                                    const cardText = String(labelEl.textContent || '')
                                        .replace(/\s+/g, ' ').trim().toLowerCase();
                                    if (cardText === normVal) return card;
                                }
                            }
                            return null;
                            """,
                            value,
                        )
                    except Exception:
                        _mx_card_el = None

                    if _mx_card_el is not None:
                        try:
                            driver.execute_script(
                                "arguments[0].scrollIntoView({block:'center', inline:'center'});",
                                _mx_card_el,
                            )
                        except Exception:
                            pass

                        _mx_clicked = False
                        try:
                            _mx_card_el.click()
                            _mx_clicked = True
                        except Exception:
                            pass
                        if not _mx_clicked:
                            try:
                                ActionChains(driver).move_to_element(_mx_card_el).click().perform()
                                _mx_clicked = True
                            except Exception:
                                pass

                        if _mx_clicked:
                            _t0_mx = time.time()
                            _mx_selected = False
                            while time.time() - _t0_mx < 1.2:
                                try:
                                    _mx_selected = bool(driver.execute_script(
                                        "return arguments[0].classList"
                                        " && arguments[0].classList.contains('mx-card-selected');",
                                        _mx_card_el,
                                    ))
                                    if _mx_selected:
                                        break
                                except Exception:
                                    break
                                time.sleep(0.05)

                            if _mx_selected:
                                log_info("[TARGET]", "apply ok=true strategy=mx_collapsible_radio reason=mx-card-selected")
                                return True

                        if debug_target:
                            log_debug(
                                "[TARGET_DEBUG]",
                                f"mx_collapsible_radio: card clicked but mx-card-selected absent: value='{value}'",
                            )
                        return False

            # --- QARTS widget (Decipher/LifePoints) --------------------------------
            # Les inputs natifs de la grille cachée (div.hidden.answers) ont size=0 et
            # ne sont pas interactables. On délègue à click_qarts_widget_by_label qui
            # cible le div[tabindex="0"] du widget visuel via JS.
            # Guard : flag posé par _extract_qarts_hidden_answers_groups.
            if payload.get("qarts_widget") and resolved_itype in ("radio", "checkbox"):
                try:
                    from Survey.input_checkbox import click_qarts_widget_by_label
                    if click_qarts_widget_by_label(driver, value):
                        log_debug("[TARGET_DEBUG]", f"qarts_widget dispatch ok: value={value!r}")
                        return True
                except Exception as _qe:
                    log_debug("[TARGET_DEBUG]", f"qarts_widget dispatch exception: {_short_exc(_qe)}")
                return False

            # --- Nfield dragndrop hidden radio (nfield_dragndrop_hidden=True) ---
            # Les inputs radio sont dans un fieldset display:none (DnD React skin).
            # On bypass la vérification de visibilité et on dispatch via JS uniquement.
            if payload.get("nfield_dragndrop_hidden") and resolved_itype in ("radio", "matrix"):
                _dnd_xp = None
                if resolved_itype == "matrix":
                    # value: "row_label || col_label"
                    _dnd_parts = [p.strip() for p in value.split("||", 1)]
                    if len(_dnd_parts) != 2 or not _dnd_parts[0] or not _dnd_parts[1]:
                        if debug_target:
                            log_debug("[TARGET_DEBUG]", f"nfield_dragndrop_hidden matrix: bad format value={value!r}")
                        return False
                    _dnd_row_raw, _dnd_col_raw = _dnd_parts
                    _dnd_nested = payload.get("option_xpath_map") or {}
                    _dnd_col_map = _dnd_nested.get(_dnd_row_raw)
                    if not _dnd_col_map:
                        _rn = _norm_lc(_dnd_row_raw)
                        for _k, _v in _dnd_nested.items():
                            if _norm_lc(_k) == _rn:
                                _dnd_col_map = _v
                                break
                    if not _dnd_col_map:
                        if debug_target:
                            log_debug("[TARGET_DEBUG]", f"nfield_dragndrop_hidden matrix: row not found row={_dnd_row_raw!r} map={list(_dnd_nested)}")
                        return False
                    _dnd_xp = _dnd_col_map.get(_dnd_col_raw)
                    if not _dnd_xp:
                        _cn = _norm_lc(_dnd_col_raw)
                        for _k, _v in _dnd_col_map.items():
                            if _norm_lc(_k) == _cn:
                                _dnd_xp = _v
                                break
                    if not _dnd_xp:
                        if debug_target:
                            log_debug("[TARGET_DEBUG]", f"nfield_dragndrop_hidden matrix: col not found col={_dnd_col_raw!r} row={_dnd_row_raw!r}")
                        return False
                else:
                    _dnd_opt_map = payload.get("option_xpath_map") or {}
                    _dnd_xp = _dnd_opt_map.get(v_norm) or (_dnd_opt_map.get(v_fold) if v_fold else None)
                    if not _dnd_xp:
                        for _k, _x in _dnd_opt_map.items():
                            _kn = _norm_lc(_k)
                            if v_norm and (v_norm == _kn or v_norm in _kn or _kn in v_norm):
                                _dnd_xp = _x
                                break
                            if v_fold and (v_fold == _kn or v_fold in _kn or _kn in v_fold):
                                _dnd_xp = _x
                                break
                    if not _dnd_xp:
                        if debug_target:
                            log_debug("[TARGET_DEBUG]", f"nfield_dragndrop_hidden: option not found value={value!r} opt_map={list(_dnd_opt_map)}")
                        return False
                try:
                    _dnd_cands = driver.find_elements(By.XPATH, _dnd_xp)
                    _dnd_radio = _dnd_cands[0] if _dnd_cands else None
                except Exception:
                    _dnd_radio = None
                if not _dnd_radio:
                    if debug_target:
                        log_debug("[TARGET_DEBUG]", f"nfield_dragndrop_hidden: element not found xpath={_dnd_xp}")
                    return False
                try:
                    driver.execute_script(
                        """
                        const inp = arguments[0];
                        try { inp.checked = true; } catch(e) {}
                        inp.dispatchEvent(new Event('input',  {bubbles:true}));
                        inp.dispatchEvent(new Event('change', {bubbles:true}));
                        inp.dispatchEvent(new MouseEvent('click', {bubbles:true}));
                        """,
                        _dnd_radio,
                    )
                    log_info("[TARGET]", f"apply ok=true strategy=nfield_dragndrop_hidden value={value!r}")
                    return True
                except Exception as _dnd_e:
                    if debug_target:
                        log_debug("[TARGET_DEBUG]", f"nfield_dragndrop_hidden JS error: {_short_exc(_dnd_e)}")
                    return False

            # --- Kantar rowrank : clic sur overlay visuel (div[tabindex='0']) via rowid ---
            # xp_rr pointe vers l'input.mrEdit caché → on lit son rowid pour cibler
            # le bon enfant de .__flexgrid_row, puis on clique l'overlay interactif.
            # Guard DOM strict : .__flexgrid_row présent ; sinon fall-through vers chemin générique.
            opt_map = payload.get("option_xpath_map") or {}
            if payload.get("kantar_rowrank") and opt_map and resolved_itype == "checkbox":
                try:
                    _rr_has_grid = bool(driver.find_elements(By.CSS_SELECTOR, ".__flexgrid_row"))
                except Exception:
                    _rr_has_grid = False

                if _rr_has_grid:
                    # 1) Lookup option dans opt_map (fuzzy identique au chemin générique)
                    xp_rr = opt_map.get(v_norm) or (opt_map.get(v_fold) if v_fold else None)
                    if not xp_rr:
                        for k, x in opt_map.items():
                            if not k:
                                continue
                            k_norm = _norm_lc(k)
                            k_fold = _fold_norm_lc(k)
                            if v_norm and (v_norm == k_norm or v_norm in k_norm or k_norm in v_norm):
                                xp_rr = x
                                break
                            if v_fold and (
                                v_fold == k_norm or v_fold in k_norm or k_norm in v_fold
                                or v_fold == k_fold or v_fold in k_fold or k_fold in v_fold
                            ):
                                xp_rr = x
                                break

                    if not xp_rr:
                        if debug_target:
                            log_debug("[TARGET_DEBUG]", f"kantar_rowrank: option introuvable value={value!r} opt_map={len(opt_map)}")
                        return False

                    # 2) Rang ordinal positionné par execute_actions_plan (1-based)
                    ordinal = int(getattr(driver, "_kantar_rowrank_ordinal", 1) or 1)

                    # 3) Résoudre l'input.mrEdit → lire rowid (0-based index de la carte)
                    try:
                        mr_input = driver.find_element(By.XPATH, xp_rr)
                        rowid_str = (mr_input.get_attribute("rowid") or "").strip()
                        rowid = int(rowid_str) if rowid_str.isdigit() else None
                    except Exception as _rr_fe:
                        if debug_target:
                            log_debug("[TARGET_DEBUG]", f"kantar_rowrank: find mrEdit error: {_short_exc(_rr_fe)}")
                        return False

                    if rowid is None:
                        if debug_target:
                            log_debug("[TARGET_DEBUG]", f"kantar_rowrank: rowid absent sur xp={xp_rr!r}")
                        return False

                    # 4) Cibler le div overlay dans le Nième enfant de .__flexgrid_row (N = rowid).
                    #    scrollIntoView inclus pour garantir que l'élément est dans le viewport
                    #    avant que ActionChains tente le clic (évite les faux-positifs silencieux).
                    try:
                        overlay = driver.execute_script(
                            """
                            var rowid = arguments[0];
                            var grids = document.querySelectorAll('.__flexgrid_row');
                            for (var g = 0; g < grids.length; g++) {
                                var cards = grids[g].querySelectorAll(':scope > div');
                                if (rowid < cards.length) {
                                    var ov = cards[rowid].querySelector(
                                        'div[tabindex="0"][style*="cursor: pointer"][style*="inset: 0"]'
                                    );
                                    if (ov) {
                                        try { ov.scrollIntoView({block:'center',inline:'center'}); } catch(e) {}
                                        return ov;
                                    }
                                }
                            }
                            return null;
                            """,
                            rowid,
                        )
                    except Exception as _rr_oe:
                        overlay = None
                        if debug_target:
                            log_debug("[TARGET_DEBUG]", f"kantar_rowrank: overlay lookup error: {_short_exc(_rr_oe)}")

                    if not overlay:
                        if debug_target:
                            log_debug("[TARGET_DEBUG]", f"kantar_rowrank: overlay introuvable rowid={rowid}")
                        return False

                    # 5) Cliquer l'overlay via ActionChains (isTrusted=true, reconnu par React)
                    import time as _time_rr
                    _time_rr.sleep(0.1)  # laisse le scroll se stabiliser
                    clicked_rr = False
                    try:
                        ActionChains(driver).move_to_element(overlay).click().perform()
                        clicked_rr = True
                    except Exception as _rr_ce:
                        if debug_target:
                            log_debug("[TARGET_DEBUG]", f"kantar_rowrank: clic overlay échoué: {_short_exc(_rr_ce)}")

                    if not clicked_rr:
                        return False

                    # 5b) Vérification DOM : badge bleu (rgb(64,81,188)) sur le div de transition interne.
                    _time_rr.sleep(0.35)  # CSS transition 250 ms
                    try:
                        _rr_verified = bool(driver.execute_script(
                            """
                            var rowid = arguments[0];
                            var grids = document.querySelectorAll('.__flexgrid_row');
                            for (var g = 0; g < grids.length; g++) {
                                var cards = grids[g].querySelectorAll(':scope > div');
                                if (rowid < cards.length) {
                                    var td = cards[rowid].querySelector('[style*="transition: background-color"]');
                                    var bg = td ? td.style.backgroundColor : '';
                                    return bg.indexOf('64') !== -1 && bg.indexOf('81') !== -1;
                                }
                            }
                            return false;
                            """,
                            rowid,
                        ))
                    except Exception:
                        _rr_verified = True  # impossibilité de vérifier → on suppose ok
                    log_debug("[TARGET_DEBUG]", f"kantar_rowrank: verify={'ok' if _rr_verified else 'ko'} rowid={rowid} ordinal={ordinal}")

                    # 6) Filet de sécurité : si le widget n'a pas écrit la valeur, écrire ordinal
                    try:
                        driver.execute_script(
                            """
                            var inp = arguments[0], rank = arguments[1];
                            if (!inp.value) {
                                inp.value = rank;
                                ['input','change'].forEach(function(n){
                                    inp.dispatchEvent(new Event(n,{bubbles:true,cancelable:true}));
                                });
                            }
                            """,
                            mr_input, str(ordinal),
                        )
                    except Exception as _rr_w:
                        if debug_target:
                            log_debug("[TARGET_DEBUG]", f"kantar_rowrank: write fallback error: {_short_exc(_rr_w)}")

                    log_info("[TARGET]", f"apply ok=true strategy=kantar_rowrank_ordinal value={value!r} ordinal={ordinal}")
                    return True
                # _rr_has_grid=False → fall through vers chemin générique opt_map

            # --- cas "options map" (radio/checkbox)
            # IMPORTANT: on n'exige pas kind=="group" pour éviter le couplage à la classification (ex: matrix_rows_single_choice)
            if opt_map and resolved_itype in ("radio", "checkbox"):

                # Toluna Runtime AnswerRow: chemin DOM custom prioritaire et idempotent.
                # Évite le faux échec _apply_by_target_id puis fallback legacy checkbox_main
                # sur chaque option du même groupe.
                if _apply_toluna_runtime_answerrow_cached():
                    return True

                # 1) lookup direct
                xp = opt_map.get(v_norm) or (opt_map.get(v_fold) if v_fold else None)

                # 2) lookup fuzzy (avec et sans accents)
                if not xp:
                    for k, x in opt_map.items():
                        if not k:
                            continue
                        k_norm = _norm_lc(k)
                        k_fold = _fold_norm_lc(k)

                        # match sur versions normalisées
                        if v_norm and (v_norm == k_norm or v_norm in k_norm or k_norm in v_norm):
                            xp = x
                            break
                        if v_fold and (
                            v_fold == k_norm or v_fold in k_norm or k_norm in v_fold
                            or v_fold == k_fold or v_fold in k_fold or k_fold in v_fold
                        ):
                            xp = x
                            break

                # 3) checkbox unique : "oui/true" => clique la seule option
                if not xp and resolved_itype == "checkbox" and len(opt_map) == 1:
                    if (v_norm in {"oui", "yes", "true", "1", "checked", "on", "x"} or not v_norm):
                        xp = next(iter(opt_map.values()))

                # 4) fréquence: si l'IA paraphrase mal l'intensité mais garde l'unité
                # (ex: "Plusieurs fois par semaine" vs "Au moins une fois par semaine"),
                # on autorise un mapping uniquement quand l'unité correspond à UNE seule option.
                if not xp:
                    freq_key = _frequency_unit_key(value)
                    if freq_key:
                        unit_matches = [
                            x for k, x in opt_map.items()
                            if _frequency_unit_key(k) == freq_key
                        ]
                        if len(unit_matches) == 1:
                            xp = unit_matches[0]
                            if debug_target:
                                log_debug(
                                    "[TARGET_DEBUG]",
                                    f"target_id='{target_id}' value='{value}' -> frequency_unit_match='{freq_key}'",
                                )

                if not xp:
                    if debug_target:
                        log_debug("[TARGET_DEBUG]", f"target_id='{target_id}' kind='{kind}' itype='{resolved_itype}' value='{value}' -> option introuvable (opt_map={len(opt_map)})")
                    return False

                def _first_input_under(node):
                    try:
                        if (node.tag_name or "").lower() == "input":
                            return node
                    except Exception:
                        pass
                    try:
                        return node.find_element(By.XPATH, ".//input[@type='radio' or @type='checkbox']")
                    except Exception:
                        return None

                def _is_selected(inp):
                    try:
                        return bool(inp and inp.is_selected())
                    except Exception:
                        return False

                def _dispatch_check_events(inp, force_when_selected=False):
                    """Set checkbox/radio de façon idempotente.

                    But: éviter le "coché puis décoché quand plusieurs stratégies s'enchaînent
                    (click label + events) et que la page a des handlers custom.

                    - checkbox: checked=true + input/change (PAS de click synthétique)
                    - radio: checked=true + input/change + click synthétique (souvent nécessaire)
                    """
                    try:
                        if not inp:
                            return

                        # Idempotence: si déjà sélectionné, ne rien faire sauf forçage explicite
                        # (ex: AngularJS ng-checked + label contenant un <a> où le modèle ng-model
                        # n'est pas forcément synchronisé tant que input/change n'ont pas été dispatchés).
                        try:
                            if inp.is_selected() and not force_when_selected:
                                return
                        except Exception:
                            pass

                        driver.execute_script(
                            """
                            const inp = arguments[0];
                            if (!inp) return;
                            const type = (inp.type || '').toLowerCase();
                            try { inp.checked = true; } catch(e) {}
                            inp.dispatchEvent(new Event('input',  {bubbles:true}));
                            inp.dispatchEvent(new Event('change', {bubbles:true}));
                            // Pour checkbox, click peut retoggler via handlers. Pour radio, click aide souvent.
                            if (type === 'radio') {
                              inp.dispatchEvent(new MouseEvent('click', {bubbles:true}));
                            }
                            """,
                            inp,
                        )
                    except Exception:
                        pass

                def _cdp_click(el) -> bool:
                    # Click "réel" via CDP (plus proche d'un user click)
                    try:
                        # IMPORTANT: certaines grilles (mat-table) scrollent horizontalement.
                        # On force le scroll dans les 2 axes pour éviter les éléments 0x0 / hors viewport.
                        try:
                            driver.execute_script(
                                "arguments[0].scrollIntoView({block:'center', inline:'center'});",
                                el,
                            )
                        except Exception:
                            pass

                        if not hasattr(driver, "execute_cdp_cmd"):
                            if debug_target:
                                log_debug("[TARGET_DEBUG]", "CDP click unavailable: driver has no execute_cdp_cmd()")
                            return False

                        rect = driver.execute_script(
                            "const r = arguments[0].getBoundingClientRect();"
                            "return {x:r.left + r.width/2, y:r.top + r.height/2, w:r.width, h:r.height};",
                            el,
                        )
                        x = int(rect.get("x", 0))
                        y = int(rect.get("y", 0))

                        driver.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y, "button": "none"})
                        driver.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
                        driver.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})
                        return True
                    except Exception as e:
                        if debug_target:
                            log_debug("[TARGET_DEBUG]", f"CDP click failed: {_short_exc(e)}")
                        return False

                def _ensure_pre_clicks_ready(target_xpath: str) -> None:
                    """
                    Rend le target visible AVANT de le chercher/click.
                    Cas typique: Ask&Answer mobile matrix (mat-expansion-panel replié).
                    Budget borné, pas de retry infini.
                    """
                    pre_click_xps = payload.get("pre_click_xpaths") or []
                    if not pre_click_xps:
                        return

                    # Djà visible -> no-op
                    try:
                        cur = _find_best_visible(target_xpath)
                        if cur:
                            try:
                                if cur.is_displayed():
                                    return
                            except Exception:
                                return
                    except Exception:
                        pass

                    # Ouvrir le panneau/accordéon (au plus 3 xpaths)
                    for pre_xp in pre_click_xps[:3]:
                        try:
                            pre_el = _find_best_visible(pre_xp)
                            if not pre_el:
                                continue

                            # éviter de retoggler un panneau déjà ouvert
                            try:
                                if (pre_el.get_attribute("aria-expanded") or "").strip().lower() == "true":
                                    break
                            except Exception:
                                pass

                            try:
                                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", pre_el)
                            except Exception:
                                pass

                            try:
                                pre_el.click()
                            except Exception:
                                try:
                                    driver.execute_script("arguments[0].click();", pre_el)
                                except Exception:
                                    continue
                            break
                        except Exception:
                            continue

                    # Attendre que l'option devienne visible (animation/lazy render Angular)
                    t0 = time.time()
                    while time.time() - t0 < 1.3:
                        try:
                            cur = _find_best_visible(target_xpath)
                            if cur:
                                try:
                                    if cur.is_displayed():
                                        return
                                except Exception:
                                    return
                        except Exception:
                            pass
                        time.sleep(0.05)

                def _click_candidate(node, label: str) -> bool:
                    # Cache de méthode gagnante borné au plan courant (target_id = clé de groupe)
                    _cm_cache = _get_block_strategy_memory(driver)['click_method']
                    _first = _cm_cache.get(target_id, 1)  # 1 = tout tenter
                    if _first > 1 and debug_target:
                        log_debug("[TARGET_DEBUG]", f"_click_candidate: skip_to={_first} target_id={target_id}")

                    # 1) click webdriver standard
                    if _first <= 1:
                        try:
                            node.click()
                            _cm_cache[target_id] = 1
                            return True
                        except Exception as e:
                            if debug_target:
                                log_debug("[TARGET_DEBUG]", f"native click failed on {label}: {_short_exc(e)}")

                    # 2) ActionChains (souvent plus robuste quand le DOM est "capricieux")
                    if _first <= 2:
                        try:
                            ActionChains(driver).move_to_element(node).pause(0.05).click().perform()
                            _cm_cache[target_id] = 2
                            return True
                        except Exception as e:
                            if debug_target:
                                log_debug("[TARGET_DEBUG]", f"actionchains click failed on {label}: {_short_exc(e)}")

                    # 3) CDP click (trusted-ish)
                    if _first <= 3:
                        if _cdp_click(node):
                            _cm_cache[target_id] = 3
                            return True

                    # 4) JS click (dernier recours, parfois ignoré si anti-bot)
                    try:
                        driver.execute_script("arguments[0].click();", node)
                        _cm_cache[target_id] = 4
                        return True
                    except Exception as e:
                        if debug_target:
                            log_debug("[TARGET_DEBUG]", f"js click failed on {label}: {_short_exc(e)}")

                    _cm_cache.pop(target_id, None)  # invalider si tout échoue
                    return False

                # 0) pre-clicks (ex: ouvrir un panneau accordéon AVANT de chercher l'option)
                # IMPORTANT: certains panels sont lazy-rendered: tant que le panel est fermé,
                # les mat-radio-button n'existent pas / ne sont pas visibles -> xpath introuvable.
                if (payload.get("pre_click_xpaths") or []) and not payload.get("_preclick_done"):
                    payload["_preclick_done"] = True
                    for pre_xp in (payload.get("pre_click_xpaths") or [])[:3]:
                        try:
                            pre_el = _find_best_visible(pre_xp)
                            if not pre_el:
                                continue
                            # éviter de retoggler un panneau déjà ouvert
                            try:
                                if (pre_el.get_attribute("aria-expanded") or "").strip().lower() == "true":
                                    continue
                            except Exception:
                                pass

                            try:
                                pre_el.click()
                            except Exception:
                                try:
                                    driver.execute_script("arguments[0].click();", pre_el)
                                except Exception:
                                    pass
                            time.sleep(0.12)
                        except Exception:
                            continue

                # --- Savanta JQM carousel : clic sur div.ui-btn + validation via carousel-values ---
                if payload.get("savanta_jqm_carousel"):
                    try:
                        el = _find_best_visible(xp)
                        if el is None:
                            try:
                                cands = driver.find_elements(By.XPATH, xp)
                                el = cands[0] if cands else None
                            except Exception:
                                el = None
                        if el is None:
                            if debug_target:
                                log_debug("[TARGET_DEBUG]", f"savanta_jqm_carousel: element not found xpath={xp}")
                            return False
                        try:
                            driver.execute_script(
                                "arguments[0].scrollIntoView({block:'center', inline:'center'});", el
                            )
                        except Exception:
                            pass
                        clicked = _click_candidate(el, "savanta_jqm_carousel_btn")
                        if not clicked:
                            if debug_target:
                                log_debug("[TARGET_DEBUG]", f"savanta_jqm_carousel: click failed xpath={xp}")
                            return False
                        # Validation : l'input hidden carousel-values[data-index=N] doit avoir reçu une valeur
                        current_idx = payload.get("jqm_carousel_current_data_index")
                        if current_idx is not None:
                            ok = driver.execute_script(
                                """
                                const idx = arguments[0];
                                const inp = document.querySelector(
                                    '.carousel-values input[data-index="' + idx + '"]'
                                );
                                if (!inp) return true;  // absent = pas de validation possible, optimiste
                                return !!(inp.value && inp.value.trim() !== '');
                                """,
                                current_idx,
                            )
                            if ok:
                                log_info("[TARGET]", "apply ok=true strategy=savanta_jqm_carousel reason=carousel_value_set")
                                return True
                            # Attente courte (JQM peut être async)
                            time.sleep(0.3)
                            ok = driver.execute_script(
                                """
                                const idx = arguments[0];
                                const inp = document.querySelector(
                                    '.carousel-values input[data-index="' + idx + '"]'
                                );
                                if (!inp) return true;
                                return !!(inp.value && inp.value.trim() !== '');
                                """,
                                current_idx,
                            )
                            if ok:
                                log_info("[TARGET]", "apply ok=true strategy=savanta_jqm_carousel reason=carousel_value_set_delayed")
                                return True
                            if debug_target:
                                log_debug("[TARGET_DEBUG]", f"savanta_jqm_carousel: carousel-values[data-index={current_idx}] still empty after click")
                            return False
                        # Pas d'index connu → retour optimiste si le clic a réussi
                        log_info("[TARGET]", "apply ok=true strategy=savanta_jqm_carousel reason=click_ok_no_index")
                        return True
                    except Exception as exc:
                        if debug_target:
                            log_debug("[TARGET_DEBUG]", f"savanta_jqm_carousel exception: {_short_exc(exc)}")
                        return False

                # --- Confirmit CF carousel (cf_carousel_item=True) ---
                # Les div.cf-answer-button sont dans des conteneurs aria-hidden="true" pour
                # les items non-courants : is_displayed() retourne False même après navigation.
                # On bypasse _find_best_visible et on clique directement par XPATH sans contrainte
                # de visibilité, après s'être assuré que le paging button a bien été activé.
                if payload.get("cf_carousel_item"):
                    try:
                        # Naviguer vers l'item si pas encore fait (paging button)
                        paging_xps = payload.get("pre_click_xpaths") or []
                        for pxp in paging_xps[:1]:
                            try:
                                paging_cands = driver.find_elements(By.XPATH, pxp)
                                if paging_cands:
                                    pel = paging_cands[0]
                                    aria_pressed = (pel.get_attribute("aria-pressed") or "").strip().lower()
                                    if aria_pressed != "true":
                                        try:
                                            driver.execute_script("arguments[0].click();", pel)
                                        except Exception:
                                            pass
                                        time.sleep(0.25)
                            except Exception:
                                pass

                        # Trouver le bouton cible sans contrainte is_displayed
                        btn_el = None
                        try:
                            cands = driver.find_elements(By.XPATH, xp)
                            btn_el = cands[0] if cands else None
                        except Exception:
                            pass
                        if btn_el is None:
                            if debug_target:
                                log_debug("[TARGET_DEBUG]", f"cf_carousel_item: element not found xpath={xp}")
                            return False

                        # Scroll + clic JS
                        try:
                            driver.execute_script(
                                "arguments[0].scrollIntoView({block:'center', inline:'center'});", btn_el
                            )
                        except Exception:
                            pass
                        try:
                            driver.execute_script("arguments[0].click();", btn_el)
                        except Exception:
                            if debug_target:
                                log_debug("[TARGET_DEBUG]", f"cf_carousel_item: JS click failed xpath={xp}")
                            return False

                        # Vérification via aria-checked="true"
                        time.sleep(0.15)
                        ok = False
                        try:
                            ok = (btn_el.get_attribute("aria-checked") or "").strip().lower() == "true"
                        except Exception:
                            ok = True  # stale element = clic probablement appliqué

                        if not ok:
                            # Tentative 2 : ActionChains (animation Confirmit parfois async)
                            try:
                                ActionChains(driver).move_to_element(btn_el).pause(0.05).click().perform()
                                time.sleep(0.15)
                                ok = (btn_el.get_attribute("aria-checked") or "").strip().lower() == "true"
                            except Exception:
                                pass

                        if ok:
                            log_info("[TARGET]", "apply ok=true strategy=cf_carousel_item reason=aria_checked")
                            return True
                        if debug_target:
                            log_debug("[TARGET_DEBUG]", f"cf_carousel_item: aria-checked not set after click xpath={xp}")
                        return False
                    except Exception as exc:
                        if debug_target:
                            log_debug("[TARGET_DEBUG]", f"cf_carousel_item exception: {_short_exc(exc)}")
                        return False

                # --- Confirmit CF HRS single (cf-hrs-single, grille NPS multi-marques) ---
                # Les div.cf-horizontal-rating-item pour les questions hors-viewport ont size=0 :
                # _find_best_visible échoue puis radio_main fait une recherche textuelle globale
                # qui coche toujours le premier bloc visible (LCL).
                # On bypasse la contrainte de visibilité : scroll inline puis JS click, vérifié
                # via aria-checked="true" sur le div[role='radio'].
                if payload.get("confirmit_cf_hrs_single"):
                    try:
                        _hrs_cands = driver.find_elements(By.XPATH, xp)
                        _hrs_el = _hrs_cands[0] if _hrs_cands else None
                    except Exception:
                        _hrs_el = None
                    if _hrs_el is None:
                        if debug_target:
                            log_debug("[TARGET_DEBUG]", f"confirmit_cf_hrs_single: element not found xpath={xp}")
                        return False
                    try:
                        driver.execute_script(
                            "arguments[0].scrollIntoView({block:'center', inline:'center'});", _hrs_el
                        )
                    except Exception:
                        pass
                    _hrs_ok = False
                    try:
                        driver.execute_script("arguments[0].click();", _hrs_el)
                        time.sleep(0.15)
                        try:
                            _hrs_ok = ((_hrs_el.get_attribute("aria-checked") or "").strip().lower() == "true")
                        except Exception:
                            _hrs_ok = True  # stale = clic probablement appliqué
                    except Exception as exc:
                        if debug_target:
                            log_debug("[TARGET_DEBUG]", f"confirmit_cf_hrs_single: JS click failed: {_short_exc(exc)}")
                    if not _hrs_ok:
                        try:
                            ActionChains(driver).move_to_element(_hrs_el).pause(0.05).click().perform()
                            time.sleep(0.15)
                            _hrs_ok = ((_hrs_el.get_attribute("aria-checked") or "").strip().lower() == "true")
                        except Exception:
                            pass
                    if _hrs_ok:
                        log_info("[TARGET]", "apply ok=true strategy=confirmit_cf_hrs_single reason=aria_checked")
                        return True
                    if debug_target:
                        log_debug("[TARGET_DEBUG]", f"confirmit_cf_hrs_single: aria-checked not set after click xpath={xp}")
                    return False

                # 1) trouver l'élément cible (label/span/input)
                _ensure_pre_clicks_ready(xp)
                try:
                    el = _find_best_visible(xp)
                    if not el:
                        if debug_target:
                            log_debug("[TARGET_DEBUG]", f"element not found for xpath: {xp}")
                        return False
                except Exception as ex:
                    if debug_target:
                        log_debug("[TARGET_DEBUG]", f"element not found for xpath={xp} ({type(ex).__name__}: {_short_exc(ex)})")
                    return False

                # --- Askia Ranking Isotope (adcRanking jQuery plugin) ---
                # Guard DOM strict : flag askia_ranking_isotope posé par l'extracteur.
                # Le widget adcRanking branche ses handlers via $.fn.on('click', ...) —
                # un click Selenium natif ou JS vanilla ne traverse pas jQuery, le plugin
                # reste muet. Stratégie : $(element).trigger('click') via le jQuery de la page,
                # suivi d'une vérification sur l'input hidden associé (id="R{qid}_{data-value}").
                # data-value est extrait depuis l'élément cible (div[data-value]).
                if payload.get("askia_ranking_isotope") and resolved_itype == "checkbox":
                    _adc_ok = bool(driver.execute_script(
                        """
                        var el = arguments[0];
                        if (!el) return false;

                        // Récupère le data-value sur le div cible (ou son ancêtre .statement)
                        var dataValue = el.getAttribute('data-value');
                        if (!dataValue) {
                            var stmt = el.closest ? el.closest('[data-value]') : null;
                            if (stmt) dataValue = stmt.getAttribute('data-value');
                        }

                        // Déclenche le clic via jQuery pour activer le plugin adcRanking
                        if (typeof jQuery !== 'undefined') {
                            jQuery(el).trigger('click');
                        } else if (typeof $ !== 'undefined' && typeof $.fn !== 'undefined') {
                            $(el).trigger('click');
                        } else {
                            return false;
                        }

                        // Vérification : l'input hidden associé doit avoir une valeur non vide
                        // Format attendu : id="R{qid}_{data-value}" (ex: R609_4385)
                        if (!dataValue) return true;  // pas de data-value → on fait confiance au clic
                        var hiddenInputs = document.querySelectorAll('input[type="hidden"][id$="_' + dataValue + '"]');
                        for (var i = 0; i < hiddenInputs.length; i++) {
                            if ((hiddenInputs[i].value || '').trim() !== '') return true;
                        }
                        // Délai possible : vérification après 80 ms (synchrone impossible, on retourne true
                        // sur la bonne foi du trigger — la vérification asynchrone se fera côté Python)
                        return true;
                        """,
                        el,
                    ))
                    if _adc_ok:
                        # Vérification asynchrone : l'input hidden doit avoir une valeur dans les 600 ms
                        _data_value = None
                        try:
                            _data_value = driver.execute_script(
                                """
                                var el = arguments[0];
                                if (!el) return null;
                                var dv = el.getAttribute('data-value');
                                if (!dv) {
                                    var s = el.closest ? el.closest('[data-value]') : null;
                                    if (s) dv = s.getAttribute('data-value');
                                }
                                return dv || null;
                                """,
                                el,
                            )
                        except Exception:
                            _data_value = None

                        _adc_confirmed = False
                        if _data_value:
                            _deadline = time.time() + 0.6
                            while time.time() < _deadline:
                                try:
                                    _adc_confirmed = bool(driver.execute_script(
                                        """
                                        var dv = arguments[0];
                                        var inputs = document.querySelectorAll('input[type="hidden"][id$="_' + dv + '"]');
                                        for (var i = 0; i < inputs.length; i++) {
                                            if ((inputs[i].value || '').trim() !== '') return true;
                                        }
                                        return false;
                                        """,
                                        _data_value,
                                    ))
                                    if _adc_confirmed:
                                        break
                                except Exception:
                                    pass
                                time.sleep(0.05)
                        else:
                            _adc_confirmed = True  # pas de data-value = on ne peut pas vérifier

                        if _adc_confirmed:
                            log_info("[TARGET]", f"apply ok=true strategy=askia_ranking_isotope reason=jquery_trigger value='{value}'")
                            return True
                        if debug_target:
                            log_debug("[TARGET_DEBUG]", f"askia_ranking_isotope: input hidden vide après trigger value='{value}' data-value={_data_value!r}")
                    else:
                        if debug_target:
                            log_debug("[TARGET_DEBUG]", f"askia_ranking_isotope: jQuery non disponible ou trigger échoué value='{value}' xpath='{xp}'")
                    return False

                # Kantar/Nfield swatches rowpicker: inputs natifs dans un fieldset masqué
                # (style="display: none"), overlay cliquable dans div#container_{questionname}.
                # Guard DOM strict : fieldset[questionname][display:none] contenant
                # input.mrMultiple.styled + div#container_{questionname} avec ._rowpicker.
                # Dans ce cas, cliquer l'input natif (via XPath) échoue toujours avec
                # ElementNotInteractableException. On redirige vers click_nfield_swatches_by_label.
                if resolved_itype == "checkbox":
                    try:
                        _nfield_qname = driver.execute_script(
                            """
                            const el = arguments[0];
                            if (!el || !el.closest) return null;
                            const fs = el.closest('fieldset[questionname]');
                            if (!fs) return null;
                            const st = fs.getAttribute('style') || '';
                            const cp = getComputedStyle(fs).display;
                            if (!(st.indexOf('display: none') !== -1 || st.indexOf('display:none') !== -1 || cp === 'none')) return null;
                            if (!fs.querySelector('input[class*="mrMultiple"][class*="styled"]')) return null;
                            const qname = fs.getAttribute('questionname');
                            if (!qname) return null;
                            const cont = document.getElementById('container_' + qname);
                            if (!cont || !cont.querySelector('[data-test="main-contain"]._rowpicker')) return null;
                            return qname;
                            """,
                            el,
                        )
                    except Exception:
                        _nfield_qname = None

                    if _nfield_qname:
                        from Survey.input_checkbox import click_nfield_swatches_by_label
                        _sw_ok = click_nfield_swatches_by_label(driver, value, scope=None)
                        log_debug(
                            "[TARGET_DEBUG]",
                            f"nfield_swatches_dispatch: {'ok' if _sw_ok else 'ko'} qname={_nfield_qname!r} value={value!r}",
                        )
                        return bool(_sw_ok)

                # Dynata/Decipher "shelf" custom tool:
                # - #custom-tool-area + .custom-product visibles
                # - answers-list natif masqué via display:none
                # Dans ce cas, le xpath target peut pointer une .clickableCell cachée.
                # Stratégie unique: cocher l'input radio natif par JS (checked + events),
                # puis vérifier strictement via input.checked.
                if resolved_itype == "radio":
                    try:
                        shelf_result = driver.execute_script(
                            """
                            const node = arguments[0];
                            if (!node) return { matched: false, ok: false, reason: 'no_node' };

                            const question = node.closest ? node.closest('div.question, fieldset, form, .question') : null;
                            const root = question || document;

                            const tool = root.querySelector('#custom-tool-area');
                            const hasCustomProducts = !!(tool && tool.querySelector('div.custom-product'));
                            const answers = root.querySelector('.answers.answers-list');
                            const answersHidden = !!(answers && getComputedStyle(answers).display === 'none');

                            if (!(tool && hasCustomProducts && answersHidden)) {
                              return { matched: false, ok: false, reason: 'pattern_not_matched' };
                            }

                            let input = null;
                            if ((node.tagName || '').toLowerCase() === 'input' && (node.type || '').toLowerCase() === 'radio') {
                              input = node;
                            }

                            if (!input && node.querySelector) {
                              input = node.querySelector("input[type='radio']");
                            }

                            if (!input && node.getAttribute) {
                              const fid = node.getAttribute('for');
                              if (fid) {
                                const byId = document.getElementById(fid);
                                if (byId && (byId.type || '').toLowerCase() === 'radio') input = byId;
                              }
                            }

                            if (!input && node.closest) {
                              const cell = node.closest('.clickableCell, .element');
                              if (cell) {
                                input = cell.querySelector("input[type='radio']");
                              }
                            }

                            if (!input) {
                              return { matched: true, ok: false, reason: 'input_not_found' };
                            }

                            try { input.checked = true; } catch (e) {}
                            try { input.dispatchEvent(new Event('input', { bubbles: true })); } catch (e) {}
                            try { input.dispatchEvent(new Event('change', { bubbles: true })); } catch (e) {}
                            try { input.dispatchEvent(new MouseEvent('click', { bubbles: true })); } catch (e) {}

                            return {
                              matched: true,
                              ok: !!input.checked,
                              reason: input.checked ? 'checked' : 'not_checked',
                              inputId: input.id || null,
                              inputName: input.name || null,
                            };
                            """,
                            el,
                        ) or {}
                    except Exception as e:
                        shelf_result = {"matched": False, "ok": False, "reason": f"script_error:{_short_exc(e)}"}

                    if shelf_result.get("matched"):
                        if bool(shelf_result.get("ok")):
                            if debug_target:
                                log_debug(
                                    "[TARGET_DEBUG]",
                                    "shelf radio js-select ok "
                                    f"target_id='{target_id}' reason='{shelf_result.get('reason')}'",
                                )
                            return True
                        if debug_target:
                            log_debug(
                                "[TARGET_DEBUG]",
                                "shelf radio js-select failed "
                                f"target_id='{target_id}' reason='{shelf_result.get('reason')}'",
                            )
                        return False

                if (
                    (payload.get("meta") or {}).get("source") == "sq-atm1d"
                    and v_norm
                    and v_norm in set((payload.get("meta") or {}).get("exclusive_options_norm") or [])
                ):
                    try:
                        driver.execute_script(
                            """
                            const target = arguments[0];
                            if (!target) return;
                            const targetLi = target.closest ? target.closest('li.sq-atm1d-button') : null;
                            if (!targetLi) return;
                            const all = Array.from(document.querySelectorAll('li.sq-atm1d-button.sq-atm1d-selected'));
                            for (const li of all) {
                              if (li === targetLi) continue;
                              li.classList.remove('sq-atm1d-selected');
                              for (const inp of li.querySelectorAll('input[type="checkbox"], input[type="radio"]')) {
                                try { inp.checked = false; } catch(e) {}
                                try { inp.dispatchEvent(new Event('input', { bubbles: true })); } catch(e) {}
                                try { inp.dispatchEvent(new Event('change', { bubbles: true })); } catch(e) {}
                              }
                            }
                            """,
                            el,
                        )
                    except Exception:
                        pass

                # Cas widgets sans <input> sous l'option (ex: Decipher cardrating):
                # la sélection est reflétée par data-selected / aria-selected / aria-checked.
                def _selected_like(node) -> bool:
                    try:
                        ds = (node.get_attribute("data-selected") or "").strip().lower()
                        if ds in ("true", "1", "yes", "on"):
                            return True
                        if (node.get_attribute("aria-selected") or "").strip().lower() == "true":
                            return True
                        if (node.get_attribute("aria-checked") or "").strip().lower() == "true":
                            return True
                        cls = (node.get_attribute("class") or "").lower()
                        if any(tok in cls for tok in (" selected", "selected ", " active", "active ", "checked", "is-selected", "is-checked")):
                            return True
                    except Exception:
                        pass
                    return False

                def _wait_selected_like(node, timeout_s: float = 1.0) -> bool:
                    import time
                    end = time.time() + timeout_s
                    while time.time() < end:
                        if _selected_like(node):
                            return True
                        # Angular Material: l'état est souvent porté par la classe sur mat-radio-button
                        try:
                            cls = (node.get_attribute("class") or "")
                            if "mat-radio-checked" in cls:
                                return True
                        except Exception:
                            pass
                        try:
                            node.find_element(By.XPATH, "ancestor-or-self::*[contains(@class,'mat-radio-checked')][1]")
                            return True
                        except Exception:
                            pass

                        # parfois l'état est porté par un parent (li -> span, etc.)
                        try:
                            node.find_element(
                                By.XPATH,
                                "ancestor-or-self::*[@data-selected='true' or @aria-selected='true' or @aria-checked='true'][1]"
                            )
                            return True
                        except Exception:
                            pass
                        time.sleep(0.05)
                    return False

                def _wait_decipher_clickable_ranking_effect(node, timeout_s: float = 1.0) -> bool:
                    """Validation DOM stricte pour Decipher clickable ranking."""
                    import time
                    end = time.time() + timeout_s
                    while time.time() < end:
                        try:
                            ok = driver.execute_script(
                                r"""
                                const node = arguments[0];
                                if (!node) return false;
                                const item = node.closest ? node.closest('.customItem') : null;
                                if (!item) return false;
                                const rank = item.querySelector('.customRank');
                                if (!rank) return false;
                                const txt = String(rank.textContent || '').trim();
                                if (/^\d+$/.test(txt)) return true;
                                const cls = String(rank.className || '').toLowerCase();
                                if (cls.includes('customrankselected')) return true;
                                return false;
                                """,
                                node,
                            )
                            if ok:
                                return True
                        except Exception:
                            pass
                        time.sleep(0.05)
                    return False

                if payload.get("decipher_clickable_ranking"):
                    clicked = _click_candidate(el, "decipher_clickable_ranking")
                    if not clicked:
                        if debug_target:
                            log_debug("[TARGET_DEBUG]", f"decipher_clickable_ranking click failed: value='{value}' xpath='{xp}'")
                        return False
                    if _wait_decipher_clickable_ranking_effect(el, timeout_s=1.0):
                        return True
                    if debug_target:
                        log_debug("[TARGET_DEBUG]", f"decipher_clickable_ranking no rank signal after click: value='{value}' xpath='{xp}'")
                    return False

                if payload.get("toluna_runtime_ranking"):
                    log_info("[TARGET]", f"toluna_runtime_ranking: clicking value='{value}'")
                    clicked = _click_candidate(el, "toluna_runtime_ranking")
                    if not clicked:
                        log_info("[TARGET]", f"toluna_runtime_ranking: click failed value='{value}'")
                        return False
                    # Attente du signal DOM post-clic : div[data-aut='Runtime_Rank'] avec texte numérique.
                    # IMPORTANT : après le clic, Toluna re-render le node React → `el` devient stale.
                    # On ne passe plus `el` au script JS : on requête le DOM vivant en cherchant le
                    # wrapper dont le texte contient `value` ET qui porte un Runtime_Rank numérique.
                    _rank_confirmed = False
                    _rank_deadline = time.time() + 1.5
                    _value_js = value  # capture locale (pas de fermeture sur `value` qui peut muter)
                    while time.time() < _rank_deadline:
                        try:
                            _rank_confirmed = driver.execute_script(
                                """
                                const needle = (arguments[0] || '').trim().toLowerCase();
                                const wrappers = document.querySelectorAll(
                                    "div.answer[data-aut='Runtime_RankingItemWrapper']"
                                );
                                for (const w of wrappers) {
                                    const txt = (w.textContent || '').trim().toLowerCase();
                                    if (!txt.includes(needle)) continue;
                                    const rank = w.querySelector("[data-aut='Runtime_Rank']");
                                    if (rank && /\d/.test(rank.textContent || '')) return true;
                                }
                                return false;
                                """,
                                _value_js,
                            )
                        except Exception:
                            _rank_confirmed = False
                        if _rank_confirmed:
                            break
                        time.sleep(0.1)
                    if not _rank_confirmed:
                        log_info("[TARGET]", f"toluna_runtime_ranking: rank signal timeout value='{value}'")
                        return False
                    return True

                # --- Confirmit/Forsta Wix ranking (cf-question--ranking) ---
                # Guard DOM strict : flag confirmit_cf_ranking posé par _extract_confirmit_cf_ranking_blocks.
                # L'interaction est un clic séquentiel sur des div[role="button"] — pas d'input natif.
                # Chaque clic attribue le rang suivant. La valeur `value` est le texte de l'option à cliquer.
                # Validation : cf-ranking-answer--selected apparaît sur la div après clic réussi.
                # Cas quota atteint : cf-ranking-answer--disabled sur les items restants → succès complet,
                # pas un échec — le dispatcher peut recevoir une valeur qui ne trouve plus de div cliquable.
                if payload.get("confirmit_cf_ranking") and resolved_itype == "checkbox":
                    # Recherche de la div cible par texte normalisé, directement dans le DOM vivant.
                    # On ne réutilise pas `el` (xpath du groupe) mais on cherche l'item par son texte.
                    _cfr_clicked = bool(driver.execute_script(
                        """
                        const norm = s => (s || '').toLowerCase()
                            .normalize('NFKC')
                            .replace(/\u00A0/g, ' ')
                            .replace(/\s+/g, ' ')
                            .trim();
                        const needle = norm(arguments[0]);
                        const items = document.querySelectorAll(
                            'div.cf-list__item.cf-ranking-answer[role="button"]'
                        );
                        for (const item of items) {
                            // Ignorer les items déjà sélectionnés
                            if (item.classList.contains('cf-ranking-answer--selected')) continue;
                            // Ignorer les items désactivés (quota atteint côté DOM)
                            if (item.classList.contains('cf-ranking-answer--disabled')) continue;
                            const txtEl = item.querySelector('div.cf-ranking-answer__text');
                            if (!txtEl) continue;
                            if (norm(txtEl.textContent) === needle) {
                                item.click();
                                return true;
                            }
                        }
                        return false;
                        """,
                        value,
                    ))
                    if not _cfr_clicked:
                        # Vérifier si l'item est déjà sélectionné (action déjà appliquée) ou quota atteint
                        _cfr_already = bool(driver.execute_script(
                            """
                            const norm = s => (s || '').toLowerCase()
                                .normalize('NFKC')
                                .replace(/\u00A0/g, ' ')
                                .replace(/\s+/g, ' ')
                                .trim();
                            const needle = norm(arguments[0]);
                            const items = document.querySelectorAll(
                                'div.cf-list__item.cf-ranking-answer'
                            );
                            for (const item of items) {
                                const txtEl = item.querySelector('div.cf-ranking-answer__text');
                                if (!txtEl) continue;
                                if (norm(txtEl.textContent) !== needle) continue;
                                // Déjà sélectionné = ok
                                if (item.classList.contains('cf-ranking-answer--selected')) return true;
                                // Quota atteint (disabled) sans être sélectionné = on ne peut plus cliquer
                                if (item.classList.contains('cf-ranking-answer--disabled')) return true;
                            }
                            return false;
                            """,
                            value,
                        ))
                        if _cfr_already:
                            log_info("[TARGET]", f"apply ok=true strategy=confirmit_cf_ranking reason=already_selected_or_quota value='{value}'")
                            return True
                        if debug_target:
                            log_debug("[TARGET_DEBUG]", f"confirmit_cf_ranking: item not found or not clickable value='{value}'")
                        return False

                    # Validation post-clic : l'item doit porter cf-ranking-answer--selected
                    _cfr_confirmed = False
                    _cfr_deadline = time.time() + 1.0
                    while time.time() < _cfr_deadline:
                        try:
                            _cfr_confirmed = bool(driver.execute_script(
                                """
                                const norm = s => (s || '').toLowerCase()
                                    .normalize('NFKC')
                                    .replace(/\u00A0/g, ' ')
                                    .replace(/\s+/g, ' ')
                                    .trim();
                                const needle = norm(arguments[0]);
                                const items = document.querySelectorAll(
                                    'div.cf-list__item.cf-ranking-answer'
                                );
                                for (const item of items) {
                                    const txtEl = item.querySelector('div.cf-ranking-answer__text');
                                    if (!txtEl) continue;
                                    if (norm(txtEl.textContent) !== needle) continue;
                                    if (item.classList.contains('cf-ranking-answer--selected')) return true;
                                    // Quota atteint immédiatement (max_select=1 ou dernier rang)
                                    if (item.classList.contains('cf-ranking-answer--disabled')) return true;
                                }
                                return false;
                                """,
                                value,
                            ))
                        except Exception:
                            _cfr_confirmed = False
                        if _cfr_confirmed:
                            break
                        time.sleep(0.05)

                    if _cfr_confirmed:
                        log_info("[TARGET]", f"apply ok=true strategy=confirmit_cf_ranking reason=selected value='{value}'")
                        return True
                    if debug_target:
                        log_debug("[TARGET_DEBUG]", f"confirmit_cf_ranking: no selection signal after click value='{value}'")
                    return False

                def _is_decipher_mx_collapsible_checkbox_selected(cell_node) -> bool:
                    """Validation stricte de sélection pour Decipher MX Collapsible checkbox.

                    Scope DOM (additif) :
                    - cellule `.clickableCell` avec `input[type='checkbox'].fir-hidden`
                    - présence d'un widget `.mx-stage .mx-collapsible-container` dans la même question
                    Signal de succès : la carte correspondante porte `.mx-card-selected`.
                    """
                    try:
                        ok = driver.execute_script(
                            r"""
                            const cell = arguments[0];
                            if (!cell || !cell.closest) return false;
                            const question = cell.closest('div.question');
                            if (!question) return false;
                            const hiddenCheckbox = cell.querySelector("input[type='checkbox'].fir-hidden");
                            if (!hiddenCheckbox) return false;

                            const mx = question.querySelector('.mx-stage .mx-collapsible-container');
                            if (!mx) return false;

                            const labelEl = cell.querySelector('label');
                            if (!labelEl) return false;
                            const raw = String(labelEl.textContent || '').replace(/\{@[^}]*@\}/g, ' ');
                            const label = raw.replace(/\s+/g, ' ').trim().toLowerCase();
                            if (!label) return false;

                            const cards = mx.querySelectorAll('.mx-collapsible-row-item');
                            for (const card of cards) {
                              const cardLabelEl = card.querySelector('.label');
                              if (!cardLabelEl) continue;
                              const cardLabel = String(cardLabelEl.textContent || '').replace(/\s+/g, ' ').trim().toLowerCase();
                              if (!cardLabel) continue;
                              if (cardLabel === label) {
                                return card.classList.contains('mx-card-selected');
                              }
                            }
                            return false;
                            """,
                            cell_node,
                        )
                        return bool(ok)
                    except Exception:
                        return False

                def _maybe_advance_mx_vertical_carousel_after_answer() -> None:
                    if not allow_mx_vertical_carousel_advance:
                        return
                    if not (payload.get("mx_vertical_carousel_next_xpath") and resolved_itype in ("radio", "checkbox")):
                        return
                    next_xpath = (payload.get("mx_vertical_carousel_next_xpath") or "").strip()
                    if not next_xpath:
                        return

                    intercept_only = (os.getenv("CTA_INTERCEPT_ONLY", "") or "").strip().lower() in {"1", "true", "yes", "on"}
                    moved = False
                    found_clickable_next = False
                    for _ in range(2):
                        next_btn = _find_best_visible(next_xpath)
                        if not next_btn:
                            break
                        try:
                            if (next_btn.get_attribute("aria-disabled") or "").strip().lower() == "true":
                                break
                        except Exception:
                            pass
                        found_clickable_next = True
                        if intercept_only:
                            moved = True
                            break
                        if _click_candidate(next_btn, "mx_vertical_carousel_next"):
                            moved = True
                            break
                        time.sleep(0.05)

                    if intercept_only:
                        if found_clickable_next:
                            log_info("[CTA_INTERCEPT]", "mx_vertical_carousel cta_found intercept_ok")
                        else:
                            log_info("[CTA_INTERCEPT]", "mx_vertical_carousel cta_found intercept_impossible")
                    elif not moved and debug_target:
                        log_debug(
                            "[TARGET_DEBUG]",
                            f"mx vertical carousel next unavailable or click failed: target_id='{target_id}'",
                        )

                # Idempotence checkbox: si la cible est déjà dans l'état voulu,
                # ne pas cliquer (évite les dérives sur widgets FocusVision/Decipher).
                if resolved_itype == "checkbox":
                    try:
                        inp_pre = _first_input_under(el)
                    except Exception:
                        inp_pre = None

                    try:
                        mx_collapsible_scope = bool(
                            driver.execute_script(
                                """
                                const node = arguments[0];
                                if (!node || !node.closest) return false;
                                const cell = node.closest('.clickableCell');
                                if (!cell) return false;
                                const q = cell.closest('div.question');
                                if (!q) return false;
                                if (!cell.querySelector("input[type='checkbox'].fir-hidden")) return false;
                                return !!q.querySelector('.mx-stage .mx-collapsible-container');
                                """,
                                el,
                            )
                        )
                    except Exception:
                        mx_collapsible_scope = False

                    if mx_collapsible_scope:
                        try:
                            cell_pre = driver.execute_script(
                                """
                                const node = arguments[0];
                                return (node && node.closest) ? node.closest('.clickableCell') : null;
                                """,
                                el,
                            )
                        except Exception:
                            cell_pre = None
                        if cell_pre is not None and _is_decipher_mx_collapsible_checkbox_selected(cell_pre):
                            return True
                    elif _is_selected(inp_pre) or _selected_like(el):
                        return True

                    try:
                        if (el.tag_name or "").lower() == "label":
                            fid = (el.get_attribute("for") or "").strip()
                            if fid:
                                inp_for = driver.find_element(By.ID, fid)
                                if _is_selected(inp_for):
                                    return True
                    except Exception:
                        pass

                # 2) clic "normal sur la cible
                # Decipher/FocusVision answers-list avec input natif masqué (fir-hidden):
                # la vraie surface cliquable est le wrapper `.clickableCell`.
                # On applique une stratégie unique et DOM-gardée pour éviter les faux positifs.
                if resolved_itype == "checkbox":
                    try:
                        decipher_cell = driver.execute_script(
                            """
                            const node = arguments[0];
                            if (!node || !node.closest) return null;
                            const cell = node.closest('.clickableCell');
                            if (!cell) return null;
                            const hiddenInput = cell.querySelector("input[type='checkbox'].fir-hidden");
                            if (!hiddenInput) return null;
                            return cell;
                            """,
                            el,
                        )
                    except Exception:
                        decipher_cell = None

                    if decipher_cell is not None:
                        clicked = _click_candidate(decipher_cell, "decipher_clickable_cell")
                        if not clicked:
                            if debug_target:
                                log_debug("[TARGET_DEBUG]", f"decipher clickableCell click failed: value='{value}' xpath='{xp}'")
                            return False

                        try:
                            ok_decipher = _is_decipher_mx_collapsible_checkbox_selected(decipher_cell)
                            if not ok_decipher:
                                ok_decipher = driver.execute_script(
                                    """
                                    const cell = arguments[0];
                                    if (!cell) return false;
                                    const inp = cell.querySelector("input[type='checkbox'].fir-hidden");
                                    if (!inp) return false;
                                    if (inp.checked) return true;
                                    const icon = cell.querySelector('.fir-icon');
                                    return !!(icon && icon.classList && icon.classList.contains('selected'));
                                    """,
                                    decipher_cell,
                                )
                        except Exception:
                            ok_decipher = False

                        if bool(ok_decipher):
                            _maybe_advance_mx_vertical_carousel_after_answer()
                            return True

                        if debug_target:
                            log_debug("[TARGET_DEBUG]", f"decipher clickableCell no checked/selected signal: value='{value}' xpath='{xp}'")
                        return False

                # --- Toluna Runtime AnswerRow (check_box / radio_button custom, sans input natif) ---
                # Guard DOM : row = [data-aut='Runtime_AnswerRow'] + [data-aut='Runtime_Wrapper']
                #             présent + aucun <input> natif.
                # Signal : changement de classe css-* sur l'enfant interne
                #   check_box  → [data-aut='Runtime_IconBox']
                #   radio_button → [data-aut='Runtime_InnerFill']
                # La classe capturée AVANT le clic = référence "non-coché" ; stockée dans
                # le payload (dict live) pour idempotence sur retries.
                # Le poll post-clic utilise document.getElementById(rowId) pour survivre
                # aux re-renders React qui invalident la référence WebElement originale.

                # Guard interview-layout : bouton <button role="option"> dans
                # ul[data-test-id="ChoiceMultiple_ChoiceFields"] — clic direct + vérification
                # aria-selected="true". Ce provider ne porte pas de Runtime_AnswerRow.
                # Court-circuit avant toluna_runtime_answerrow pour éviter faux négatif.
                _is_interview_layout_btn = False
                try:
                    _is_interview_layout_btn = driver.execute_script(
                        """
                        const el = arguments[0];
                        if (!el || !el.closest) return false;
                        if ((el.tagName || '').toLowerCase() !== 'button') return false;
                        if ((el.getAttribute('role') || '').toLowerCase() !== 'option') return false;
                        return el.closest('ul[data-test-id="ChoiceMultiple_ChoiceFields"]') !== null;
                        """,
                        el,
                    )
                except Exception:
                    _is_interview_layout_btn = False

                if _is_interview_layout_btn:
                    # Idempotence : déjà sélectionné ?
                    if (el.get_attribute("aria-selected") or "").strip().lower() == "true":
                        log_info("[TARGET]", f"apply ok=true strategy=interview_layout_btn reason=already_selected value='{value}'")
                        return True
                    clicked = _click_candidate(el, "interview_layout_btn")
                    if clicked:
                        import time as _time
                        _time.sleep(0.1)
                        if (el.get_attribute("aria-selected") or "").strip().lower() == "true":
                            log_info("[TARGET]", f"apply ok=true strategy=interview_layout_btn reason=clicked value='{value}'")
                            return True
                        log_debug("[TARGET_DEBUG]", f"interview_layout_btn aria-selected not true after click value='{value}'")
                    return False

                log_info("[TARGET]", "toluna_runtime_answerrow: entering block")
                try:
                    _toluna_guard = driver.execute_script(
                        """
                        const el = arguments[0];
                        if (!el || !el.closest) return null;
                        if (!el.closest("[data-aut='Runtime_AnswerRow']")) return null;
                        const wrapper = el.querySelector("[data-aut='Runtime_Wrapper']");
                        if (!wrapper) return null;
                        if (el.querySelector("input[type='checkbox'], input[type='radio']")) return null;
                        const inner = wrapper.querySelector(
                            "[data-aut='Runtime_IconBox'], [data-aut='Runtime_InnerFill']"
                        );
                        if (!inner) return null;
                        return { cls: inner.className || '', rowId: el.id || '' };
                        """,
                        el,
                    )
                except Exception:
                    _toluna_guard = None

                log_info("[TARGET]", f"toluna_runtime_answerrow: guard={_toluna_guard!r}")
                if isinstance(_toluna_guard, dict) and _toluna_guard.get("cls") is not None:
                    _toluna_cls_pre = _toluna_guard.get("cls", "")
                    _toluna_row_id = (_toluna_guard.get("rowId") or "").strip()

                    _cls_ref_key = f"_toluna_cls_unchecked_{xp}"
                    _cls_unchecked = payload.get(_cls_ref_key)
                    if _cls_unchecked is None:
                        _cls_unchecked = _toluna_cls_pre
                        payload[_cls_ref_key] = _cls_unchecked

                    # Idempotence : classe actuelle ≠ référence → déjà coché
                    if _toluna_cls_pre != _cls_unchecked:
                        log_info("[TARGET]", f"apply ok=true strategy=toluna_runtime_answerrow reason=already_selected value='{value}'")
                        return True

                    # Re-fetch par ID pour survivre au re-render React qui invalide el.
                    _toluna_clicked = False
                    try:
                        driver.execute_script(
                            """
                            var row = document.getElementById(arguments[0]);
                            if (!row) throw new Error('row not found: ' + arguments[0]);
                            row.querySelector("[data-aut='Runtime_Wrapper']").click();
                            """,
                            _toluna_row_id,
                        )
                        _toluna_clicked = True
                    except Exception:
                        try:
                            el.click()
                            _toluna_clicked = True
                        except Exception:
                            pass

                    if _toluna_clicked:
                        time.sleep(0.15)
                        log_info("[TARGET]", f"apply ok=true strategy=toluna_runtime_answerrow reason=clicked value='{value}'")
                        return True
                    return False

                if resolved_itype == "checkbox":
                    label_anchor_guard_active = False
                    try:
                        label_anchor_guard_active = (el.tag_name or "").lower() == "label" and bool(
                            el.find_elements(By.TAG_NAME, "a")
                        )
                    except Exception:
                        label_anchor_guard_active = False

                    if label_anchor_guard_active:
                        if is_debug():
                            log_debug(
                                "[TARGET_DEBUG]",
                                f"checkbox label anchor guard active: value='{value}' xpath='{xp}'",
                            )

                        inp_guard = None
                        try:
                            fid = (el.get_attribute("for") or "").strip()
                            if fid:
                                inp_guard = driver.find_element(By.ID, fid)
                        except Exception:
                            inp_guard = None

                        if inp_guard is None:
                            try:
                                inp_guard = _first_input_under(el)
                            except Exception:
                                inp_guard = None

                        if inp_guard is not None:
                            # Guard Angular: ng-model+ng-checked signalent un binding AngularJS.
                            # JS cb.checked=true+dispatchEvent ne propage pas dans $scope.
                            # Un click Selenium natif déclenche le cycle $digest normalement.
                            _is_angular_inp = False
                            try:
                                _is_angular_inp = bool(
                                    inp_guard.get_attribute("ng-model")
                                    and inp_guard.get_attribute("ng-checked") is not None
                                )
                            except Exception:
                                _is_angular_inp = False

                            if _is_angular_inp:
                                try:
                                    inp_guard.click()
                                except Exception:
                                    try:
                                        driver.execute_script("arguments[0].click();", inp_guard)
                                    except Exception:
                                        pass
                            else:
                                _dispatch_check_events(inp_guard, force_when_selected=True)

                        if inp_guard is not None and _is_selected(inp_guard):
                            if is_debug():
                                log_debug(
                                    "[TARGET_DEBUG]",
                                    f"checkbox label anchor guard success: value='{value}' xpath='{xp}'",
                                )
                            return True

                        if is_debug():
                            log_debug(
                                "[TARGET_DEBUG]",
                                f"checkbox label anchor guard did not select input: value='{value}' xpath='{xp}'",
                            )

                # --- Askia ResponsiveTable checkbox (inputs non-interactables masqués par CSS) ---
                # Guard DOM strict : flag askia_responsive_table_checkbox posé par l'extracteur.
                # Stratégie unique : clic JS sur le <label for=inputId> associé à l'input résolu,
                # ou forçage direct checked=true + dispatchEvent si le label est absent.
                # Vérification stricte via input.checked après action.
                # Ne pas modifier le chemin générique (fall-through si flag absent).
                if payload.get("askia_responsive_table_checkbox") and resolved_itype == "checkbox":
                    _artc_ok = bool(driver.execute_script(
                        """
                        var node = arguments[0];
                        if (!node) return false;

                        // Remonter à l'input si node est un label ou un wrapper
                        var input = null;
                        if ((node.tagName || '').toLowerCase() === 'input' && (node.type || '').toLowerCase() === 'checkbox') {
                            input = node;
                        }
                        if (!input && node.querySelector) {
                            input = node.querySelector("input[type='checkbox']");
                        }
                        if (!input && node.getAttribute) {
                            var fid = node.getAttribute('for');
                            if (fid) {
                                var byId = document.getElementById(fid);
                                if (byId && (byId.type || '').toLowerCase() === 'checkbox') input = byId;
                            }
                        }
                        if (!input) return false;

                        // Préférer le clic sur le label (déclenche les handlers Askia)
                        var inputId = input.getAttribute('id') || '';
                        var label = inputId ? document.querySelector('label[for="' + inputId + '"]') : null;
                        if (label) {
                            try { label.click(); } catch(e) {}
                        } else {
                            // Pas de label : forcer checked + events
                            try { input.checked = true; } catch(e) {}
                            try { input.dispatchEvent(new Event('input',  { bubbles: true })); } catch(e) {}
                            try { input.dispatchEvent(new Event('change', { bubbles: true })); } catch(e) {}
                        }
                        return !!input.checked;
                        """,
                        el,
                    ))
                    if _artc_ok:
                        log_info("[TARGET]", "apply ok=true strategy=askia_responsive_table_checkbox reason=label_js_click")
                        _maybe_advance_mx_vertical_carousel_after_answer()
                        return True
                    if debug_target:
                        log_debug("[TARGET_DEBUG]", f"askia_responsive_table_checkbox: input.checked=false after label click value='{value}' xpath='{xp}'")
                    return False

                _click_candidate(el, "target")

                _maybe_advance_mx_vertical_carousel_after_answer()

                def _ipsos_slider_value_matches(node, expected: str) -> bool:
                    """Validation DOM pour les sliders Likert IPSOS (bootstrap-slider)."""
                    if not payload.get("ipsos_slider"):
                        return False
                    exp = (expected or "").strip()
                    if not exp:
                        return False
                    try:
                        ok = driver.execute_script(
                            """
                            const marker = arguments[0];
                            const expected = String(arguments[1] || '').trim();
                            if (!expected) return false;

                            let container = marker;
                            for (let i = 0; i < 10 && container; i++) {
                              if (container.querySelector && container.querySelector('input.slider-form-field.bs-slider')) break;
                              container = container.parentElement;
                            }

                            let field = container && container.querySelector
                              ? container.querySelector('input.slider-form-field.bs-slider')
                              : null;
                            if (!field) {
                              field = document.querySelector('#questionContent input.slider-form-field.bs-slider, input.slider-form-field.bs-slider');
                            }
                            if (!field) return false;

                            const current = String(field.value || field.getAttribute('value') || field.getAttribute('data-value') || '').trim();
                            if (current === expected) return true;

                            const scope = field.parentElement || container || document;
                            const handle = scope.querySelector && scope.querySelector('.slider-handle[aria-valuenow]');
                            const ariaNow = handle ? String(handle.getAttribute('aria-valuenow') || '').trim() : '';
                            return ariaNow === expected;
                            """,
                            node,
                            exp,
                        )
                        return bool(ok)
                    except Exception:
                        return False

                inp = _first_input_under(el)
                if not inp:
                    if _wait_selected_like(el, timeout_s=1.0):
                        return True

                # AreYouNet: la sélection est stockée dans un <input type=hidden name=...>
                # (pas de radio/checkbox natif). On valide en lisant la valeur après clic.
                try:
                    ayn_name = (payload.get('ayn_field_name') or '').strip()
                    ayn_map = payload.get('ayn_value_map') or {}
                    if ayn_name and ayn_map:
                        exp = ayn_map.get(v_norm) or (ayn_map.get(v_fold) if v_fold else None)
                        if exp is not None and exp != '':
                            ok = driver.execute_script(
                                """
                                const name = arguments[0];
                                const exp  = arguments[1];
                                const els = document.getElementsByName(name);
                                for (const e of els) {
                                  if (!e) continue;
                                  if ((e.value || '') === exp) return true;
                                }
                                return false;
                                """,
                                ayn_name,
                                str(exp),
                            )
                            if ok:
                                return True
                except Exception:
                    pass

                # NEW: si la cible est un <label for="...">, forcer l'input associé
                try:
                    if (el.tag_name or "").lower() == "label":
                        fid = (el.get_attribute("for") or "").strip()
                        if fid:
                            inp_for = driver.find_element(By.ID, fid)
                            if not _is_selected(inp_for):
                                _dispatch_check_events(inp_for)
                except Exception:
                    pass

                inp = _first_input_under(el)
                if _is_selected(inp):
                    return True

                # NEW: Angular Material (Ask&Answer desktop matrix)
                # Le <label> peut etre 0x0 / non-interactif. On clique le conteneur mat-radio-button,
                # puis on valide via mat-radio-checked (plus fiable que input.checked après re-render).
                try:
                    mr = el.find_element(
                        By.XPATH,
                        "ancestor-or-self::*[self::mat-radio-button or contains(@class,'mat-radio-button')][1]",
                    )
                    _click_candidate(mr, "mat-radio-button")
                    if _wait_selected_like(mr, timeout_s=0.8):
                        return True
                    try:
                        mc = mr.find_element(By.XPATH, ".//span[contains(@class,'mat-radio-container')][1]")
                        _click_candidate(mc, "mat-radio-container")
                        if _wait_selected_like(mr, timeout_s=0.8):
                            return True
                    except Exception:
                        pass
                except Exception:
                    pass

                # 3) si on a cliqué un label non interactif (pointer-events, overlay), tenter le span
                try:
                    sp = el.find_element(By.XPATH, ".//span[1]")
                    _click_candidate(sp, "span")
                except Exception:
                    pass

                inp = inp or _first_input_under(el)
                if _is_selected(inp):
                    return True

                # 4) tenter un clic direct sur l'input (meme si masqué) via JS
                if inp:
                    _click_candidate(inp, "input")
                    if _is_selected(inp):
                        return True

                    # 5) dernier recours DOM-only: forcer checked + events (ce qui active souvent le bouton Continue)
                    if not _is_selected(inp):
                        _dispatch_check_events(inp)
                    if _is_selected(inp):
                        return True

                # --- vérification robuste (évite stale / re-render) ---
                inp_id = None
                inp_name = None

                try:
                    # si label[for] => input id
                    if (el.tag_name or "").lower() == "label":
                        _for = (el.get_attribute("for") or "").strip()
                        if _for:
                            inp_id = _for
                except Exception:
                    pass

                try:
                    if not inp_id:
                        if (el.tag_name or "").lower() == "input":
                            inp_id = (el.get_attribute("id") or "").strip() or None
                            inp_name = (el.get_attribute("name") or "").strip() or None
                except Exception:
                    pass

                try:
                    if not inp_id or not inp_name:
                        inp = _first_input_under(el)
                        if inp:
                            inp_id = inp_id or ((inp.get_attribute("id") or "").strip() or None)
                            inp_name = inp_name or ((inp.get_attribute("name") or "").strip() or None)
                except Exception:
                    pass

                if _wait_checked(inp_id, inp_name, timeout_s=1.2):
                    return True

                if _ipsos_slider_value_matches(el, value):
                    return True

                if debug_target:
                    log_debug("[TARGET_DEBUG]", f"selection failed after waits: value='{value}' xpath='{xp}' inp_id='{inp_id}' inp_name='{inp_name}'")
                return False

            # --- cas multi_text : plusieurs cases texte pour UNE meme question (OpenTextMultiLines)
            if kind == "multi_text" and resolved_itype in ("text", "textarea", "number"):
                fields = payload.get("fields") or []
                if not fields:
                    return False

                def _locate_field(fld: dict):
                    # 1) xpath + alt_xpaths
                    for cand_xp in [fld.get("xpath")] + list(fld.get("alt_xpaths") or []):
                        if not cand_xp:
                            continue
                        elc = _find_best_visible(cand_xp)
                        if elc:
                            return elc

                    # 2) By.NAME / By.ID (dernier recours)
                    nm = (fld.get("name") or "").strip()
                    if nm:
                        try:
                            for c in driver.find_elements(By.NAME, nm):
                                try:
                                    if c.is_displayed():
                                        return c
                                except Exception:
                                    continue
                        except Exception:
                            pass

                    fid = (fld.get("id") or "").strip()
                    if fid:
                        try:
                            for c in driver.find_elements(By.ID, fid):
                                try:
                                    if c.is_displayed():
                                        return c
                                except Exception:
                                    continue
                        except Exception:
                            pass

                    return None

                # Remplit la 1ère case vide (dterministe, pas de boucle/retry infini)
                for fld in fields:
                    try:
                        elx = _locate_field(fld)
                        if not elx:
                            continue
                        try:
                            if not elx.is_enabled():
                                continue
                        except Exception:
                            pass

                        cur = (elx.get_attribute("value") or "").strip()
                        if cur:
                            continue

                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", elx)
                        try:
                            elx.clear()
                        except Exception:
                            pass
                        elx.send_keys(value or "")
                        return True
                    except Exception:
                        continue

                # Si tout est déjà rempli, on ignore l'excès de valeurs (évite fallback)
                return True

            # --- cas single (text/textarea/dropdown/button)
            if kind == "single":
                xp = payload.get("xpath")
                if not xp:
                    return False

                if resolved_itype in ("text", "textarea", "number"):
                    try:
                        el = driver.find_element(By.XPATH, xp)
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                        try:
                            el.clear()
                        except Exception:
                            pass
                        el.send_keys(value or "")
                        return True
                    except Exception:
                        return False

                # --- Dropdown: set value via JS + dispatch events (works even if <select> is hidden) ---
                # + cas spcial "sq-sliderpoints" : cliquer / dragger la piste pour que l'UI se mette à jour
                if resolved_itype == "dropdown":

                    # Trouver le <select> meme si l'id a changé (re-render).
                    def _iter_dropdown_candidates():
                        cands = []

                        def _add(elems):
                            for e in elems or []:
                                try:
                                    if (e.tag_name or "").lower() == "select":
                                        cands.append(e)
                                except Exception:
                                    continue

                        # 1) xpath principal
                        if xp:
                            try:
                                _add(driver.find_elements(By.XPATH, xp))
                            except Exception:
                                pass

                        # 2) alt_xpaths (ex: //select[@name='...'])
                        for ax in (payload.get("alt_xpaths") or []):
                            try:
                                _add(driver.find_elements(By.XPATH, ax))
                            except Exception:
                                pass

                        # 3) By.NAME / By.ID (au cas où)
                        nm = (payload.get("name") or "").strip()
                        if nm:
                            try:
                                _add(driver.find_elements(By.NAME, nm))
                            except Exception:
                                pass

                        eid = (payload.get("id") or "").strip()
                        if eid:
                            try:
                                _add(driver.find_elements(By.ID, eid))
                            except Exception:
                                pass

                        # 4) fallback: si aucun locator n'a matché (re-render), tenter tous les <select>.
                        #    Le filtrage se fera plus bas via la présence de l'option demandée.
                        if not cands:
                            try:
                                _add(driver.find_elements(By.CSS_SELECTOR, "select"))
                            except Exception:
                                pass

                        # dédup
                        uniq = []
                        seen = set()
                        for e in cands:
                            try:
                                k = getattr(e, "_id", None) or getattr(e, "id", None)
                            except Exception:
                                k = None
                            if k and k in seen:
                                continue
                            if k:
                                seen.add(k)
                            uniq.append(e)
                        return uniq

                    v = (value or "").strip()
                    if not v:
                        return False

                    v = (value or "").strip()
                    if not v:
                        return False

                    def _key(s: str) -> str:
                        # Robust label normalization for dropdown/sliderpoints matching:
                        # - fold accents
                        # - normalize quotes/apostrophes
                        # - drop stray punctuation
                        s = (s or "").replace("\xa0", " ")
                        s = unicodedata.normalize("NFKD", s)
                        s = "".join(c for c in s if not unicodedata.combining(c))

                        # normalize common unicode quotes -> ascii
                        s = s.replace("\u2019", "'").replace("\u2018", "'")
                        s = s.replace("\u00b4", "'").replace("`", "'")
                        s = s.replace("\u201c", '"').replace("\u201d", '"')

                        # remove quotes/apostrophes and light punctuation
                        s = re.sub(r"[\"'\u2019\u2018\u00b4`]+", " ", s)
                        s = re.sub(r"[.,;:!?()\[\]{}<>]+", " ", s)
                        s = re.sub(r"\s+", " ", s).strip().lower()
                        return s

                    v_lc = _key(v)

                    for sel in _iter_dropdown_candidates():
                        # Best-effort: amener le select dans le viewport (meme s'il est masqué)
                        try:
                            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", sel)
                        except Exception:
                            pass

                        # options exploitables (hors placeholder)
                        try:
                            raw_opts = sel.find_elements(By.TAG_NAME, "option")
                        except Exception:
                            raw_opts = []

                        real_opts = []  # [(idx, text_lc, value)]
                        for o in raw_opts:
                            try:
                                if (o.get_attribute("disabled") or "").strip():
                                    continue
                                t = _key(o.text or "")
                                if not t:
                                    continue
                                ov = (o.get_attribute("value") or "").strip()
                                if ov in ("", "-1"):
                                    continue
                                if any(tok in t for tok in ("veuillez", "sélection", "selection", "select", "choose")):
                                    continue
                                real_opts.append((len(real_opts), t, ov))
                            except Exception:
                                continue

                        if not real_opts:
                            continue

                        best_val = None
                        best_idx = None

                        # 1) match exact (texte ou value) — toute la liste avant fallback partiel
                        for i, t, ov in real_opts:
                            if t == v_lc or ov.strip().lower() == v_lc:
                                best_val = ov
                                best_idx = i
                                break

                        # 2) match partiel uniquement si aucun exact trouvé
                        if best_val is None:
                            for i, t, ov in real_opts:
                                if v_lc and (v_lc in t or t in v_lc):
                                    best_val = ov
                                    best_idx = i
                                    break

                        if best_val is None or best_idx is None:
                            continue

                        # 1) source de vérité: <select>
                        try:
                            driver.execute_script(
                                """
                                const sel = arguments[0];
                                const val = arguments[1];
                                sel.value = val;
                                try { sel.dispatchEvent(new Event('input',  {bubbles:true})); } catch(e) {}
                                try { sel.dispatchEvent(new Event('change', {bubbles:true})); } catch(e) {}
                                try {
                                  if (window.jQuery && window.jQuery(sel).selectpicker) {
                                    window.jQuery(sel).selectpicker('refresh');
                                  }
                                } catch(e) {}
                                """,
                                sel,
                                best_val,
                            )
                        except Exception:
                            continue

                        # 2) sliderpoints: clic/drag sur la piste pour déplacer le handle (sinon off-scale)
                        try:
                            meta = payload.get("meta") or {}
                            src = (meta.get("source") or "").strip().lower()
                            sel_id = (sel.get_attribute("id") or "").strip()
                            is_sliderpoints = (src == "sq-sliderpoints") or ("sliderpoints" in sel_id.lower())

                            if is_sliderpoints:
                                track = None
                                if sel_id:
                                    try:
                                        track = driver.find_element(By.ID, f"sliderpoints_{sel_id}")
                                    except Exception:
                                        track = None

                                if track is None:
                                    try:
                                        container = sel.find_element(
                                            By.XPATH,
                                            "ancestor::*[contains(@class,'sq-sliderpoints-container') or contains(@class,'sq-sliderpoints-element')][1]",
                                        )
                                        track = container.find_element(By.CSS_SELECTOR, ".ui-slider-horizontal")
                                    except Exception:
                                        track = None

                                if track is not None:
                                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", track)
                                    r = track.rect or {}
                                    w = int(r.get("width", 0) or 0)
                                    h = int(r.get("height", 0) or 0)

                                    steps = max(1, len(real_opts) - 1)
                                    x = int((best_idx / steps) * max(1, w - 4)) + 2
                                    y = max(1, h // 2)

                                    try:
                                        ActionChains(driver).move_to_element_with_offset(track, x, y).click().perform()
                                    except Exception:
                                        pass

                                    def _handle_offscale() -> bool:
                                        try:
                                            hnd = track.find_element(By.CSS_SELECTOR, ".ui-slider-handle")
                                            st = (hnd.get_attribute("style") or "")
                                            return ("-40" in st) or ("offscale" in st.lower())
                                        except Exception:
                                            return False

                                    if _handle_offscale():
                                        try:
                                            hnd = track.find_element(By.CSS_SELECTOR, ".ui-slider-handle")
                                            ActionChains(driver).click_and_hold(hnd).move_to_element_with_offset(track, x, y).release().perform()
                                        except Exception:
                                            pass

                                    try:
                                        cur_val = (sel.get_attribute("value") or "").strip()
                                        if cur_val != best_val:
                                            # si on rate l'UI, on laisse quand meme le <select> en source de vérité
                                            pass
                                    except Exception:
                                        pass

                        except Exception:
                            # si le forcing UI choue, on considère quand meme le select comme set
                            pass

                        return True

                    return False

                if resolved_itype == "button":
                    try:
                        el = driver.find_element(By.XPATH, xp)
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                        el.click()
                        return True
                    except Exception:
                        return False

            # --- cas runtime_dropdown (Toluna/QuickSurveys React Select custom) ---
            # Guard DOM strict : flag runtime_dropdown posé par _extract_runtime_dropdown_blocks
            if payload.get("runtime_dropdown"):
                container_id = payload.get("container_id") or ""
                parts = payload.get("runtime_dropdown_parts") or []
                if not container_id:
                    return False
                try:
                    container = driver.find_element(By.ID, container_id)
                except Exception:
                    log_debug("[TARGET_DEBUG]", f"runtime_dropdown container '{container_id}' introuvable")
                    return False
                try:
                    wrappers = container.find_elements(By.CSS_SELECTOR, "[data-testid='MultiValueSelectWrapper']")
                except Exception:
                    return False
                if not wrappers:
                    return False

                _MONTH_FR = {
                    "1": "janvier", "01": "janvier", "2": "fevrier", "02": "fevrier",
                    "3": "mars", "03": "mars", "4": "avril", "04": "avril",
                    "5": "mai", "05": "mai", "6": "juin", "06": "juin",
                    "7": "juillet", "07": "juillet", "8": "aout", "08": "aout",
                    "9": "septembre", "09": "septembre", "10": "octobre",
                    "11": "novembre", "12": "decembre",
                }

                def _nopt(s: str) -> str:
                    s = (s or "").replace("\xa0", " ")
                    s = unicodedata.normalize("NFKD", s)
                    s = "".join(c for c in s if not unicodedata.combining(c))
                    return re.sub(r"\s+", " ", s).strip().lower()

                def _rsp_pick(wrapper, target_val: str, part_hint: str = "") -> bool:
                    v = (target_val or "").strip()
                    if not v:
                        return False
                    v_norm = _nopt(v)
                    if part_hint == "month":
                        v_norm = _MONTH_FR.get(v, v_norm)
                    try:
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", wrapper)
                        wrapper.click()
                    except Exception:
                        return False
                    menu = None
                    for _ in range(8):
                        try:
                            menus = driver.find_elements(By.CSS_SELECTOR, "[class*='-menu']")
                            visible = [m for m in menus if m.is_displayed()]
                            if visible:
                                menu = visible[-1]
                                break
                        except Exception:
                            pass
                        time.sleep(0.1)
                    if not menu:
                        log_debug("[TARGET_DEBUG]", f"runtime_dropdown: menu non ouvert pour '{v}'")
                        return False
                    try:
                        opts = menu.find_elements(By.CSS_SELECTOR, "[class*='-option']")
                    except Exception:
                        opts = []
                    for opt in opts:
                        try:
                            t = _nopt(opt.text or "")
                            if t and (t == v_norm or v_norm in t or t in v_norm):
                                opt.click()
                                return True
                        except Exception:
                            continue
                    # fallback ordinal pour les valeurs numériques (ex: mois "04" → 4ème option)
                    if v.isdigit():
                        num = int(v)
                        real = [
                            o for o in opts
                            if _nopt(o.text or "") and not any(
                                tok in _nopt(o.text or "")
                                for tok in ("selectionn", "choisir", "select", "veuillez")
                            )
                        ]
                        if 1 <= num <= len(real):
                            try:
                                real[num - 1].click()
                                return True
                            except Exception:
                                pass
                    try:
                        from selenium.webdriver.common.keys import Keys as _Keys
                        comboboxes = driver.find_elements(By.CSS_SELECTOR, "input[role='combobox']")
                        if comboboxes:
                            comboboxes[-1].send_keys(_Keys.ESCAPE)
                    except Exception:
                        pass
                    log_debug("[TARGET_DEBUG]", f"runtime_dropdown: option '{v}' introuvable dans le menu")
                    return False

                v = (value or "").strip()
                if not v:
                    return False

                if parts:
                    raw_parts = None
                    if "|" in v:
                        raw_parts = [p.strip() for p in v.split("|")]
                    elif "/" in v:
                        raw_parts = [p.strip() for p in v.split("/")]
                    elif v.count("-") >= 2:
                        raw_parts = [p.strip() for p in v.split("-")]

                    if raw_parts and len(raw_parts) >= len(parts):
                        # DD/MM/YYYY (convention française) → mapper sur les parts
                        if "/" in v and len(raw_parts) == 3:
                            date_map = {"day": raw_parts[0], "month": raw_parts[1], "year": raw_parts[2]}
                        else:
                            date_map = {p: raw_parts[i] for i, p in enumerate(parts) if i < len(raw_parts)}
                        ok_count = 0
                        for i, part in enumerate(parts):
                            if i >= len(wrappers):
                                break
                            pval = date_map.get(part, "")
                            if pval and _rsp_pick(wrappers[i], pval, part_hint=part):
                                ok_count += 1
                                time.sleep(0.15)
                        return ok_count > 0
                    else:
                        # valeur unique : tenter chaque wrapper dans l'ordre des parts
                        for i, wrapper in enumerate(wrappers[: len(parts)]):
                            part = parts[i] if i < len(parts) else ""
                            if _rsp_pick(wrapper, v, part_hint=part):
                                return True
                        return False
                else:
                    return _rsp_pick(wrappers[0], v)

            # --- cas runtime_text (Toluna/QuickSurveys textarea natif) ---
            # Guard DOM strict : flag runtime_text posé par _extract_runtime_dropdown_blocks
            if payload.get("runtime_text"):
                container_id = payload.get("container_id") or ""
                if not container_id:
                    return False
                try:
                    container = driver.find_element(By.ID, container_id)
                except Exception:
                    log_debug("[TARGET_DEBUG]", f"runtime_text container '{container_id}' introuvable")
                    return False
                try:
                    ta = container.find_element(By.CSS_SELECTOR, "textarea")
                except Exception:
                    log_debug("[TARGET_DEBUG]", f"runtime_text: textarea introuvable dans '{container_id}'")
                    return False
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", ta)
                # React contrôle la valeur via son état interne : ta.clear() n'a aucun effet.
                # On vide via le setter natif + event 'input' pour notifier React, puis on saisit.
                try:
                    driver.execute_script(
                        """
                        var el = arguments[0];
                        var setter = Object.getOwnPropertyDescriptor(
                            window.HTMLTextAreaElement.prototype, 'value'
                        ).set;
                        setter.call(el, '');
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        """,
                        ta,
                    )
                except Exception:
                    try:
                        from selenium.webdriver.common.keys import Keys as _Keys
                        ta.send_keys(_Keys.CONTROL + "a")
                        ta.send_keys(_Keys.DELETE)
                    except Exception:
                        pass
                try:
                    ta.send_keys(value or "")
                    return True
                except Exception:
                    return False

            return False

        # Exécuter dans le bon frame si possible
        if switch_to_frame_chain is not None and frame_chain:
            with switch_to_frame_chain(driver, frame_chain) as ok:
                if not ok:
                    return False
                return _apply_in_current_context()

        return _apply_in_current_context()

    except Exception as _e:
        log_info("[DISPATCH_ERROR]", f"_apply_in_current_context exception target_id={target_id!r} itype={itype!r}: {type(_e).__name__}: {_e}")
        return False

# ------------------- Parsing "libellé //// type" -------------------
# Types acceptés (synonymes FR/EN)
_TYPE_ALIASES = {
    "dropdown": {"dropdown", "menu", "select", "liste", "combobox"},
    "button":   {"button", "bouton", "cta"},
    "checkbox": {"checkbox", "case", "case à cocher", "check", "cocher"},
    "radio":    {"radio", "option"},
    "text":     {"text", "texte", "champ", "input"},
    "textarea": {"textarea", "texte long", "open-end", "ouvert"},
    "number":   {"number", "numérique", "numerique"},
    "matrix-col": {"matrix-col", "matrix column", "colonne", "col"},
    "open":     {"open", "ouvrir"},
}

def _parse_typed_instruction3(instr: str) -> tuple[str, str | None, str]:
    """
    Parse 'libellé //// type //// contexte-question' (slashes >=4 tolérés).
    - Retourne (label, type_normalisé|None, context)
    - Tolère des '>' accidentels après <type>.
    """
    parts = re.split(r"/{4,}", instr or "")
    label = _norm(parts[0]) if parts else ""
    raw_type = _norm_lc(parts[1]) if len(parts) > 1 else ""
    context = _norm(parts[2]) if len(parts) > 2 else ""

    # nettoyer un ventuel '>' parasite après type
    raw_type = raw_type.replace(">", "").strip()

    itype = None
    if raw_type:
        # rutilise la table d’alias existante
        for t, aliases in _TYPE_ALIASES.items():
            if raw_type in aliases:
                itype = t
                break
        if itype is None:
            # heuristiques déjà prsentes dans _parse_typed_instruction
            if re.search(r"drop|select|menu|combo", raw_type): itype = "dropdown"
            elif re.search(r"button|bouton|cta", raw_type): itype = "button"
            elif re.search(r"check|coch", raw_type): itype = "checkbox"
            elif re.search(r"radio|option", raw_type): itype = "radio"
            elif re.search(r"text|champ|input", raw_type): itype = "text"
    return label, itype, context

# ---------- Sanitize instruction : corrige une option à risque ----------
def _get_visible_options(driver):
    # Rcupère un set de libells d’options visibles (radios/checkbox)
    opts = set()
    sels = [
        "label span.p-radio-text",
        "label span.p-checkbox-text",
        "label",
        "li label",
        "[role='radio']",
        "[role='checkbox']",
    ]
    for css in sels:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, css):
                t = (el.text or el.get_attribute("innerText") or "").strip()
                if t and len(t) >= 2:
                    opts.add(_norm(t))
        except Exception:
            continue
    return opts

def _get_page_text_lc(driver):
    try:
        return " ".join(
            driver.execute_script(
                "return Array.from(document.querySelectorAll('body *'))"
                ".filter(e=>getComputedStyle(e).display!=='none' && e.offsetParent!==null)"
                ".map(e=>(e.innerText||'').trim()).filter(t=>t.length>4);"
            )
        ).lower()
    except Exception:
        return ""

def _sanitize_instruction_with_page_context(driver, label, itype):
    """
    Si l'IA propose une réponse potentiellement disqualifiante,
    tente de la remplacer par une alternative 'safe' disponible.
    """
    lbl = _norm(label)
    if not lbl or itype not in {"radio", "checkbox", "dropdown"}:
        return label  # pas concerné

    # Options visibles sur la page
    opts = _get_visible_options(driver)

    # Contexte question
    page = _get_page_text_lc(driver)
    is_sector_question = any(
        k in page for k in ["secteur", "industrie", "travaillez", "employ", "domaines"]
    )

    # Listes de mots à risque / sûrs
    risky = {
        "non",
        "jamais",
        "certainement pas",
        "aucun",
        "aucune",
        "je prfère ne pas le dire",
        "preferer ne pas",
        "none",
        "no",
        "never",
    }
    safe_pos = ["oui", "souvent", "parfois", "rgulièrement", "hebdomadaire", "mensuel"]
    safe_neutral = ["je ne sais pas", "je ne m'en souviens pas", "neutre"]

    # Exception emploi/secteurs ; privilégier "Aucune de ces réponses" si présente
    if is_sector_question:
        for o in opts:
            if "aucune de ces réponses" in o or "aucun de ces choix" in o:
                return "Aucune de ces réponses"

    # Si la proposition est manifestement risquée ; préférer une alternative
    if any(tok in lbl for tok in risky):
        # 1) 'Oui' si disponible
        for o in opts:
            if o == "oui":
                return "Oui"
        # 2) un choix positif si dispo
        for k in safe_pos:
            for o in opts:
                if k in o:
                    return _norm(o)
        # 3) sinon une neutre
        for k in safe_neutral:
            for o in opts:
                if k in o:
                    return _norm(o)

    # Cas spécifiques : ordinateur / internet
    if any(
        k in page for k in ["ordinateur", "computer", "internet", "e-mail", "email"]
    ):
        for o in opts:
            if o == "oui":
                return "Oui"

    # rien à corriger
    return label

_OPEN_FIELD_TOKENS = {
    "jour",
    "mois",
    "année",
    "annee",
    "year",
    "month",
    "pays",
    "country",
    "ville",
    "city",
    "state",
    "province",
}

def _split_multiline_instruction(instr: str) -> list[str]:
    items = []
    for line in (instr or "").splitlines():
        line = _norm(line)
        if line:
            line = re.sub(r"^\d+\)\s*", "", line)  # 1) ...
            line = re.sub(r"^[\-\•\–\\·]+\s*", "", line)  # - ...
            items.append(line)
    return items

_QID_RE = re.compile(r"^Q\d+$", re.IGNORECASE)

def _parse_action_line(raw: str) -> dict:
    """
    Parse une ligne d'instruction (batch + legacy).
    Formats supportés :
    - QID //// target_id //// valeur //// itype //// contexte
    - QID //// valeur //// itype //// contexte
    - target_id //// valeur //// itype //// contexte
    - valeur //// itype //// contexte
    """
    parts = [p.strip() for p in re.split(r"/{4,}", (raw or "")) if p.strip()]

    out = {"qid": None, "target_id": None, "value": "", "itype": "", "context": ""}

    if len(parts) >= 5:
        out["qid"] = parts[0]
        out["target_id"] = parts[1]
        out["value"] = parts[2]
        out["itype"] = parts[3]
        out["context"] = parts[4]
        return out

    if len(parts) == 4:
        if _QID_RE.match(parts[0] or ""):
            out["qid"] = parts[0]
            out["value"] = parts[1]
            out["itype"] = parts[2]
            out["context"] = parts[3]
        else:
            out["target_id"] = parts[0]
            out["value"] = parts[1]
            out["itype"] = parts[2]
            out["context"] = parts[3]
        return out

    if len(parts) == 3:
        out["value"] = parts[0]
        out["itype"] = parts[1]
        out["context"] = parts[2]
        return out

    return out

# action_dispatcher.py
# À placer AVANT la logique générique itype == "button"

def _wait_for_button_effect(driver, *, timeout=6):
    """
    Valide qu'un clic CTA a réellement eu un effet.
    Effets possibles :
    - changement d'URL
    - apparition spinner / overlay
    - disparition du bouton
    - désactivation du bouton
    """
    import time

    start_url = driver.current_url
    start_ts = time.time()

    while time.time() - start_ts < timeout:
        time.sleep(0.3)

        # 1⃣ URL change
        if driver.current_url != start_url:
            return True

        # 2⃣ Bouton disparu ou disabled
        try:
            btn = driver.find_element(By.ID, "acceptAndTakeSurveyLink2")
            if not btn.is_displayed():
                return True
            if btn.get_attribute("aria-disabled") == "true":
                return True
            if "disabled" in (btn.get_attribute("class") or ""):
                return True
        except Exception:
            # bouton plus présent ; effet OK
            return True

        # 3⃣ Overlay / spinner
        overlays = driver.find_elements(By.CSS_SELECTOR, ".loading, .spinner, .overlay")
        if overlays:
            return True

        try:
            spin = driver.find_element(By.ID, "loadingSpin3")
            if spin.is_displayed():
                return True
        except Exception:
            pass

    return False

def handle_consent_screen(driver):
    """
    Résout un écran/bandeau de consentement (cookies/RGPD) quand il est réellement bloquant.

    IMPORTANT:
    - Ne jamais retourner True si aucun effet DOM/URL n'est observé.
    - Ne pas confondre un simple widget cookies non bloquant (ex: Evidon) avec un overlay.
    """
    import time
    from selenium.webdriver.common.by import By

    def _norm_lc(s: str) -> str:
        return " ".join((s or "").lower().split()).strip()

    # 0) Toluna/MetrixLab consent modal (radio + "Confirmez")
    #    IMPORTANT: radios potentiellement cachés (display:none), cliquer le label puis fallback JS.
    def _handle_toluna_consent_modal() -> bool:
        try:
            detected = bool(driver.execute_script(r"""
                const confirm = document.querySelector('#consent-button-confirm');
                const hasRadio = !!document.querySelector("input[name='consent']") || !!document.querySelector('.consent-form-radiogroup');
                const hasLabel = !!document.querySelector('.consent-option-label');
                return !!(confirm && hasRadio && hasLabel);
            """))
        except Exception:
            detected = False

        if not detected:
            return False

        print("[CONSENT][TOLUNA] detected")
        intercept_only = (os.getenv("CTA_INTERCEPT_ONLY", "") or "").strip().lower() in {"1", "true", "yes", "on"}

        for _ in range(2):
            try:
                checked_ok = bool(driver.execute_script(r"""
                    const norm = (s) => (s || '').toLowerCase().replace(/\s+/g, ' ').trim();
                    const confirm = document.querySelector('#consent-button-confirm');
                    const accept = document.querySelector('#consent-radio-accept') || document.querySelector("input[type='radio'][name='consent'][value='accept']");
                    if (!confirm || !accept) return false;

                    let label = null;
                    try { label = accept.closest('label.consent-option-label'); } catch(_) {}

                    if (!label) {
                        const labels = Array.from(document.querySelectorAll('label.consent-option-label'));
                        label = labels.find((l) => {
                            const t = norm(l.innerText || l.textContent || '');
                            return t.includes('je consens') || t.includes('i consent') || t.includes('agree');
                        }) || null;
                    }

                    if (label) {
                        try { label.click(); } catch(_) {}
                    }

                    if (!accept.checked) {
                        try { accept.checked = true; } catch(_) {}
                        try { accept.dispatchEvent(new Event('input', { bubbles: true })); } catch(_) {}
                        try { accept.dispatchEvent(new Event('change', { bubbles: true })); } catch(_) {}
                    }
                    return !!accept.checked;
                """))
            except Exception:
                checked_ok = False

            print(f"[CONSENT][TOLUNA] selected=accept checked_ok={str(checked_ok).lower()}")

            clicked = False
            if intercept_only:
                try:
                    clicked = bool(Survey.input_handler.try_click_navigation_cta_any_context(driver))
                except Exception:
                    clicked = False
            else:
                try:
                    btn = driver.find_element(By.CSS_SELECTOR, "#consent-button-confirm")
                    try:
                        btn.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", btn)
                    clicked = True
                except Exception:
                    clicked = False

            print(f"[CONSENT][TOLUNA] confirm clicked intercept_only={str(intercept_only).lower()}")

            try:
                err_visible = bool(driver.execute_script(r"""
                    const err = document.querySelector('#consent-error-message-container');
                    if (!err) return false;
                    const s = window.getComputedStyle(err);
                    if (!s || s.display === 'none' || s.visibility === 'hidden') return false;
                    const r = err.getBoundingClientRect();
                    return !!(r && r.width > 1 && r.height > 1);
                """))
            except Exception:
                err_visible = False

            if not err_visible and (checked_ok or clicked):
                return True

            time.sleep(0.2)

        print("[CONSENT][TOLUNA] reason=toluna_consent_not_resolved")
        return False

    if _handle_toluna_consent_modal():
        return True

    # 0) Ipsos / entercdn : écran "Politique de confidentialité" (checkbox + CTA "Accepter et commencer")
    #    Ex: input#privacyPolicyCheckbox1 + a#acceptAndTakeSurveyLink2
    def _scroll_center(el) -> None:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", el)
        except Exception:
            pass

    def _click_best_effort(el) -> bool:
        if el is None:
            return False
        _scroll_center(el)
        time.sleep(0.12)
        try:
            el.click()
            return True
        except Exception:
            try:
                ActionChains(driver).move_to_element(el).click().perform()
                return True
            except Exception:
                try:
                    driver.execute_script("arguments[0].click();", el)
                    return True
                except Exception:
                    return False

    def _cmp_overlay_present() -> bool:
        """True si un container CMP *bloquant* (grand et visible) est présent."""
        try:
            return bool(driver.execute_script(
                r"""
                const vw = Math.max(320, window.innerWidth || 0);
                const vh = Math.max(240, window.innerHeight || 0);
                const minArea = vw * vh * 0.12;

                const selectors = arguments[0] || [];
                const kw = ['cookie','cookies','consent','gdpr','rgpd','privacy','confidential'];

                function isVisible(e){
                  try{
                    const s = window.getComputedStyle(e);
                    if (!s) return false;
                    if (s.display === 'none' || s.visibility === 'hidden') return false;
                    const r = e.getBoundingClientRect();
                    if (!r) return false;
                    if (r.width < 60 || r.height < 40) return false;
                    if (r.bottom < 0 || r.right < 0 || r.top > vh || r.left > vw) return false;
                    return true;
                  }catch(_){ return false; }
                }

                const cand = [];
                for (const sel of selectors){
                  try{ cand.push(...Array.from(document.querySelectorAll(sel))); }catch(_){ }
                }

                for (const el of cand){
                  if (!isVisible(el)) continue;
                  const r = el.getBoundingClientRect();
                  if ((r.width * r.height) < minArea) continue;
                  const t = (el.innerText || '').toLowerCase();
                  if (kw.some(k => t.includes(k))) return true;

                  const blob = ((el.id||'') + ' ' + (el.className||'')).toLowerCase();
                  if (blob.includes('onetrust') || blob.includes('qc-cmp') || blob.includes('didomi') || blob.includes('truste') || blob.includes('cookiebot'))
                    return true;
                }
                return false;
                """,
                CMP_CONTAINER_SELECTORS,
            ))
        except Exception:
            return False

    def _sig() -> str:
        try:
            url = driver.current_url or ""
        except Exception:
            url = ""
        try:
            txt_len = int(driver.execute_script("return (document.body && (document.body.innerText||'').length) || 0;") or 0)
        except Exception:
            txt_len = 0
        try:
            n_btn = len(driver.find_elements(By.CSS_SELECTOR, "button, a, [role='button'], input[type='submit'], input[type='button']"))
        except Exception:
            n_btn = 0
        return f"{url}||{txt_len}||{n_btn}||{int(_cmp_overlay_present())}"

    try:
        before_url = driver.current_url or ""
    except Exception:
        before_url = ""
    before_sig = _sig()

    def _wait_change(before_sig: str, before_url: str, timeout_s: float = 6.0) -> bool:
        end = time.time() + timeout_s
        while time.time() < end:
            time.sleep(0.25)

            # 1) URL changée
            try:
                if driver.current_url != before_url:
                    return True
            except Exception:
                pass

            # 2) Signature DOM/overlay changée
            if _sig() != before_sig:
                return True

        return False


    # 0bis) Cint/QPS consent collect page: mandatory checkboxes name="consents" + CTA submit.
    # Trigger strictement DOM-first pour éviter tout impact sur les autres providers/pages.
    def _handle_cint_collect_consent_page() -> bool:
        try:
            detected = bool(driver.execute_script(r"""
                const mandatory = Array.from(document.querySelectorAll("input.mandatory[type='checkbox'][name='consents']"));
                if (!mandatory.length) return false;

                const form = mandatory[0].closest('form');
                if (!form) return false;
                const action = (form.getAttribute('action') || '').toLowerCase();
                if (!action.includes('/consent/collect/')) return false;

                const submit = form.querySelector("input[type='submit'], button[type='submit']");
                return !!submit;
            """))
        except Exception:
            detected = False

        if not detected:
            return False

        print("[CONSENT][CINT] detected")
        intercept_only = (os.getenv("CTA_INTERCEPT_ONLY", "") or "").strip().lower() in {"1", "true", "yes", "on"}

        # Budget anti-boucle: max 2 passes pour forcer l'état checked + événements DOM.
        for _ in range(2):
            try:
                state = driver.execute_script(r"""
                    const mandatory = Array.from(document.querySelectorAll("input.mandatory[type='checkbox'][name='consents']"));
                    let checkedCount = 0;

                    for (const cb of mandatory) {
                        if (!cb.checked) {
                            let clicked = false;

                            if (cb.id) {
                                const lab = document.querySelector(`label[for="${cb.id}"]`);
                                if (lab) {
                                    try { lab.click(); clicked = true; } catch(_) {}
                                }
                            }

                            if (!clicked) {
                                try { cb.click(); clicked = true; } catch(_) {}
                            }

                            if (!cb.checked) {
                                try { cb.checked = true; } catch(_) {}
                                try { cb.dispatchEvent(new Event('input', { bubbles: true })); } catch(_) {}
                                try { cb.dispatchEvent(new Event('change', { bubbles: true })); } catch(_) {}
                            }
                        }

                        if (cb.checked) checkedCount += 1;
                    }

                    return {
                        total: mandatory.length,
                        checked: checkedCount,
                        allChecked: mandatory.length > 0 && checkedCount === mandatory.length,
                    };
                """) or {}
            except Exception:
                state = {}

            if bool(state.get("allChecked")):
                break
            time.sleep(0.2)

        if not bool(state.get("allChecked")):
            print("[CONSENT][CINT] mandatory_checkboxes_not_all_checked")
            return False

        if intercept_only:
            try:
                import Survey.input_handler as input_handler
                if input_handler.click_cta_strong_any_context(driver, "continuer"):
                    print("[CTA_INTERCEPT] cint_collect cta_found intercept_ok")
                    return True
                print("[CTA_INTERCEPT] cint_collect cta_found intercept_impossible")
                return False
            except Exception:
                print("[CTA_INTERCEPT] cint_collect cta_found intercept_impossible")
                return False

        try:
            cta = driver.find_element(
                By.CSS_SELECTOR,
                "form[action*='/Consent/Collect/'] input[type='submit'], form[action*='/Consent/Collect/'] button[type='submit']",
            )
        except Exception:
            print("[CONSENT][CINT] cta_not_found")
            return False

        if not _click_best_effort(cta):
            return False

        if _wait_change(before_sig, before_url, timeout_s=6.0):
            return True

        # Si pas de navigation, vérifier qu'il n'y a plus d'erreurs de validation obligatoires visibles.
        try:
            validation_visible = bool(driver.execute_script(r"""
                const box = document.querySelector('.validation-messages.alert-danger, .alert-danger.validation-messages');
                if (!box) return false;
                const style = window.getComputedStyle(box);
                if (!style || style.display === 'none' || style.visibility === 'hidden') return false;
                const requiredErrors = Array.from(box.querySelectorAll('li'))
                    .map(li => (li.textContent || '').toLowerCase().trim())
                    .filter(Boolean);
                return requiredErrors.some(t => t.includes('validationmessage_termsandconditions') || t.includes('validationmessage_takesurveyconsent'));
            """))
        except Exception:
            validation_visible = False

        return not validation_visible

    if _handle_cint_collect_consent_page():
        return True

    CMP_CONTAINER_SELECTORS = [
        "#onetrust-banner-sdk",
        "#onetrust-consent-sdk",
        ".qc-cmp2-container",
        ".qc-cmp2-ui",
        ".qc-cmp-cleanslate",
        ".didomi-popup-container",
        "#didomi-popup",
        ".truste_overlay",
        ".truste_box_overlay",
        "#CybotCookiebotDialog",
        ".cc-window",
        ".cookie-banner",
        "[role='alertdialog']",
        "[role='dialog'][aria-modal='true']",
        "[aria-modal='true']",
    ]

    ACCEPT_WORDS = [
        "tout accepter",
        "accepter",
        "j'accepte",
        "j accepte",
        "accept all",
        "accept",
        "i accept",
        "agree",
        "i agree",
        "ok",
        "d'accord",
    ]

    REJECT_WORDS = [
        "refuser",
        "reject",
        "decline",
        "disagree",
        "necessary",
        "nécessaire",
        "paramétrer",
        "settings",
        "préférences",
        "preferences",
    ]

    def _handle_ipsos_privacy_policy_page() -> bool:
        # Détection volontairement stricte (évite les faux positifs sur d'autres consent screens)
        intercept_only = (os.getenv("CTA_INTERCEPT_ONLY", "") or "").strip().lower() in {"1", "true", "yes", "on"}
        try:
            cta = driver.find_element(
                By.CSS_SELECTOR,
                "a.btn.btn-primary[id^='acceptAndTakeSurveyLink']",
            )
        except Exception:
            if intercept_only:
                print("[CTA_INTERCEPT] ipsos_privacy_policy cta_not_found")
            return False

        try:
            # Patterns IPSOS observés:
            # - privacyPolicyCheckbox* (ancien pattern, conservé pour rétrocompatibilité)
            # - consentCheckbox* / consentContainer:* (pattern actuel 2025+)
            cbs = driver.find_elements(
                By.CSS_SELECTOR,
                "input[type='checkbox']#privacyPolicyCheckbox1, "
                "input[type='checkbox'][name*='privacyPolicyCheckbox'], "
                "input[type='checkbox'][id*='privacyPolicyCheckbox'], "
                "input[type='checkbox'][id*='consentCheckbox'], "
                "input[type='checkbox'][name*='consentCheckbox'], "
                "input[type='checkbox'][name*='consentContainer']"
            )
        except Exception:
            cbs = []
            
        if not cbs:
            return False

        cb = cbs[0]

        # 1) Cocher la policy checkbox (préférer le <label for=...>)
        try:
            already = bool(cb.is_selected())
        except Exception:
            already = False

        if not already:
            try:
                cb_id = (cb.get_attribute("id") or "").strip()
            except Exception:
                cb_id = ""

            clicked = False
            if cb_id:
                try:
                    lab = driver.find_element(By.CSS_SELECTOR, f"label[for='{cb_id}']")
                    clicked = _click_best_effort(lab)
                except Exception:
                    clicked = False

            if not clicked:
                clicked = _click_best_effort(cb)

            # Validation : on attend que is_selected() passe à True
            deadline = time.time() + 2.5
            while time.time() < deadline:
                try:
                    if cb.is_selected():
                        break
                except Exception:
                    break
                time.sleep(0.1)

        try:
            if not cb.is_selected():
                return False  # pas d'effet observable -> on n'annonce pas le succès
        except Exception:
            return False

        # 2) Cliquer "Accepter et commencer" + valider un effet (URL/spinner/disparition)
        if intercept_only:
            try:
                import Survey.input_handler
                if Survey.input_handler.click_cta_strong_any_context(driver, "accepter et commencer"):
                    print("[CTA_INTERCEPT] ipsos_privacy_policy cta_found intercept_ok")
                    return True
                print("[CTA_INTERCEPT] ipsos_privacy_policy cta_found intercept_impossible")
                return False
            except Exception:
                print("[CTA_INTERCEPT] ipsos_privacy_policy cta_found intercept_impossible")
                return False

        if not _click_best_effort(cta):
            return False

        try:
            if _wait_for_button_effect(driver, timeout=10):
                return True
        except Exception:
            pass

        # fallback : signature/URL
        return _wait_change(before_sig, before_url, timeout_s=8.0)

    if _handle_ipsos_privacy_policy_page():
        return True

    # 0bis) Affinnova / NIQ launch gate: CTA "LANCER L'ÉTUDE" (popup + switch main/secondary)
    def _handle_affinnova_launch_gate() -> bool:
        try:
            launch = driver.find_element(By.CSS_SELECTOR, "a.launchButton[onclick*='showSurvey'], a.launchButton")
        except Exception:
            return False

        try:
            if not launch.is_displayed():
                return False
        except Exception:
            return False

        handles_before = set()
        try:
            handles_before = set(driver.window_handles)
        except Exception:
            pass

        if not _click_best_effort(launch):
            return False

        # Effet attendu: nouvelle fenêtre OU transition visuelle main->secondary
        end = time.time() + 7.0
        while time.time() < end:
            time.sleep(0.2)

            try:
                if set(driver.window_handles) != handles_before:
                    return True
            except Exception:
                pass

            try:
                transitioned = bool(driver.execute_script(r"""
                    const main = document.querySelector('#main');
                    const secondary = document.querySelector('#secondary');
                    if (!main || !secondary) return false;
                    const ms = window.getComputedStyle(main);
                    const ss = window.getComputedStyle(secondary);
                    const mainHidden = !!ms && (ms.display === 'none' || ms.visibility === 'hidden');
                    const secondaryShown = !!ss && (ss.display !== 'none' && ss.visibility !== 'hidden');
                    return mainHidden && secondaryShown;
                """))
                if transitioned:
                    return True
            except Exception:
                pass

        return _wait_change(before_sig, before_url, timeout_s=2.0)

    if _handle_affinnova_launch_gate():
        return True

    # 0ter) Walr country-routing gate: cliquer #btnNext (le JS de la page auto-sélectionne
    #        le pays via .cRadio, le bot doit juste déclencher le bouton Suivant).
    def _handle_walr_country_routing_gate() -> bool:
        # Vérifier le signal Walr distinctif (.cRadio + .cRef) avant de chercher #btnNext
        try:
            has_walr_signal = bool(driver.execute_script(
                "return document.querySelectorAll('.cRadio').length >= 10 && !!document.querySelector('.cRef');"
            ))
        except Exception:
            has_walr_signal = False
        if not has_walr_signal:
            return False

        try:
            btn = driver.find_element(By.CSS_SELECTOR, "#btnNext")
        except Exception:
            return False
        try:
            if not btn.is_displayed():
                return False
        except Exception:
            return False

        intercept_only = (os.getenv("CTA_INTERCEPT_ONLY", "") or "").strip().lower() in {"1", "true", "yes", "on"}
        if intercept_only:
            label = _norm_lc(btn.get_attribute("value") or btn.text or "suivant")
            try:
                import Survey.input_handler
                Survey.input_handler.click_cta_strong_any_context(driver, label)
            except Exception:
                pass
            return True  # interception signalée

        if not _click_best_effort(btn):
            return False
        return _wait_change(before_sig, before_url, timeout_s=8.0)

    if _handle_walr_country_routing_gate():
        return True

    # 0quater) Walr intro/final interstitial: Q=FINAL + #btnNext (sans .cRadio/.cRef).
    #          Exemple: page "Veuillez cliquer sur Suivant" avant entrée dans l'enquête.
    def _handle_walr_intro_final_gate() -> bool:
        try:
            has_signal = bool(driver.execute_script(r"""
                const hasWalrFooter = !!document.querySelector('a.logo2link[href*="walr.com"]');
                const q = document.querySelector('input[type="hidden"]#Q');
                const btn = document.querySelector('#btnNext');
                const isVisible = (el) => {
                    if (!el) return false;
                    const s = window.getComputedStyle(el);
                    if (!s || s.display === 'none' || s.visibility === 'hidden') return false;
                    const r = el.getBoundingClientRect();
                    return !!(r && r.width > 10 && r.height > 10);
                };
                return hasWalrFooter && q && (q.value || '').toUpperCase() === 'FINAL' && isVisible(btn);
            """))
        except Exception:
            has_signal = False
        if not has_signal:
            return False

        try:
            btn = driver.find_element(By.CSS_SELECTOR, "#btnNext")
        except Exception:
            return False

        intercept_only = (os.getenv("CTA_INTERCEPT_ONLY", "") or "").strip().lower() in {"1", "true", "yes", "on"}
        if intercept_only:
            label = _norm_lc(btn.get_attribute("value") or btn.text or "suivant")
            try:
                import Survey.input_handler
                Survey.input_handler.click_cta_strong_any_context(driver, label)
            except Exception:
                pass
            return True

        if not _click_best_effort(btn):
            return False
        return _wait_change(before_sig, before_url, timeout_s=8.0)

    if _handle_walr_intro_final_gate():
        return True

    # 0quinquies) Angular survey-final redirect gate (ex: edgesurvey.innovatemr.net)
    #             DOM: <app-survey-final> + button.next_btn visible + aucun input répondable.
    def _handle_angular_survey_final_gate() -> bool:
        try:
            btn_sel = driver.execute_script(r"""
                const isVisible = (el) => {
                    if (!el) return false;
                    const s = window.getComputedStyle(el);
                    if (!s || s.display === 'none' || s.visibility === 'hidden') return false;
                    const r = el.getBoundingClientRect();
                    return !!(r && r.width > 10 && r.height > 10);
                };
                const root = document.querySelector('app-survey-final, [class*="survey-final"]');
                if (!root) return null;
                const answerables = Array.from(document.querySelectorAll(
                    'input[type="radio"], input[type="checkbox"], select, textarea, input[type="text"], input[type="number"], input[type="email"], input[type="tel"], input[type="search"]'
                )).filter(isVisible);
                if (answerables.length > 0) return null;
                const btn = document.querySelector('button.next_btn') ||
                            document.querySelector('button[type="submit"]');
                if (!btn || !isVisible(btn)) return null;
                return btn.className || 'button[type="submit"]';
            """)
        except Exception:
            btn_sel = None
        if not btn_sel:
            return False

        from Survey.log_utils import log_info, log_debug
        log_info("CONSENT", "angular survey-final gate détecté — clic CTA next_btn")

        try:
            try:
                btn = driver.find_element(By.CSS_SELECTOR, "button.next_btn")
            except Exception:
                btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
        except Exception:
            return False

        intercept_only = (os.getenv("CTA_INTERCEPT_ONLY", "") or "").strip().lower() in {"1", "true", "yes", "on"}
        if intercept_only:
            label = _norm_lc(btn.text or "allons-y")
            log_info("CONSENT", f"CTA_INTERCEPT_ONLY: interception angular survey-final CTA '{label}'")
            try:
                import Survey.input_handler
                Survey.input_handler.click_cta_strong_any_context(driver, label)
            except Exception:
                pass
            return True

        if not _click_best_effort(btn):
            log_debug("CONSENT", "angular survey-final: _click_best_effort a échoué")
            return False
        return _wait_change(before_sig, before_url, timeout_s=8.0)

    if _handle_angular_survey_final_gate():
        return True

    # 1) Chercher le plus grand overlay CMP visible
    #    IMPORTANT: on ignore les containers cachés (ex: CookieYes avec .cky-hide)
    def _has_hidden_ancestor(el) -> bool:
        """Vérifie si un élément est dans un container caché (CookieYes, etc.)."""
        try:
            return bool(driver.execute_script(
                "return !!arguments[0].closest('.cky-hide, .ng-hide, [hidden], .hidden')",
                el
            ))
        except Exception:
            return False
    best = None
    for sel in CMP_CONTAINER_SELECTORS:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                try:
                    if not el.is_displayed():
                        continue
                    # NOUVEAU: vérifier si l'élément est dans un container caché
                    if _has_hidden_ancestor(el):
                        continue
                    r = el.rect or {}
                    area = float(r.get('width', 0) or 0) * float(r.get('height', 0) or 0)
                    if area <= 0:
                        continue
                    if (best is None) or (area > best[0]):
                        best = (area, el)
                except Exception:
                    continue
        except Exception:
            continue

    # 2) Si overlay trouv, cliquer un bouton Accept/Agree à l'intrieur
    if best is not None:
        _, container = best
        try:
            cands = container.find_elements(By.CSS_SELECTOR, "button, a, [role='button'], input[type='submit'], input[type='button']")
        except Exception:
            cands = []

        def _score(el) -> int:
            try:
                t = _norm_lc(el.text or el.get_attribute('value') or el.get_attribute('innerText') or "")
            except Exception:
                t = ""
            if not t:
                return -1
            if any(w in t for w in REJECT_WORDS):
                return -1
            for i, w in enumerate(ACCEPT_WORDS):
                if w in t:
                    return 100 - i
            return -1

        cands = sorted(cands, key=_score, reverse=True)
        if cands and _score(cands[0]) >= 0:
            btn = cands[0]
            # En mode CTA_INTERCEPT_ONLY=1 (tests/non-régression), on force un clic via input_handler
            # pour que le CTA passe par cta_handler et soit intercepté.
            intercept_only = (os.getenv("CTA_INTERCEPT_ONLY", "") or "").strip().lower() in {"1", "true", "yes", "on"}
            try:
                if intercept_only:
                    label = _norm_lc(btn.text or btn.get_attribute('value') or btn.get_attribute('innerText') or "")
                    if label:
                        Survey.input_handler.click_cta_strong_any_context(driver, label)
                    else:
                        btn.click()
                else:
                    btn.click()
            except Exception:
                try:
                    if intercept_only:
                        label = _norm_lc(btn.text or btn.get_attribute('value') or btn.get_attribute('innerText') or "")
                        if label:
                            Survey.input_handler.click_cta_strong_any_context(driver, label)
                        else:
                            driver.execute_script("arguments[0].click();", btn)
                    else:
                        driver.execute_script("arguments[0].click();", btn)
                except Exception:
                    pass

            if _wait_change(before_sig, before_url):
                return True

    # 3) Fallback: tenter un clic accept/agree global (iframe-safe), mais toujours avec validation
    try:
        import Survey.input_handler as input_handler
        for needle in ("tout accepter", "accepter", "j'accepte", "accept all", "accept", "agree", "ok"):
            try:
                if input_handler.click_cta_strong_any_context(driver, needle):
                    if _wait_change(before_sig, before_url):
                        return True
            except Exception:
                continue
    except Exception:
        pass

    # 4) Confirmit/Forsta : bouton "Suivant" souvent SANS texte (icône) -> .cf-navigation-next
    # Exemple DOM: <button class="cf-navigation__button cf-navigation-next"><img title="Suivant"></button>
    try:
        next_buttons = driver.find_elements(
        By.CSS_SELECTOR,
        "#navButtons button.cf-navigation-next, button.cf-navigation-next, .cf-navigation__button.cf-navigation-next",
        )
        for nb in next_buttons:
            try:
                if not nb.is_displayed():
                    continue
                if _click_best_effort(nb):
                    if _wait_change(before_sig, before_url, timeout_s=8.0):
                        return True
            except Exception:
                continue
    except Exception:
        pass
    return False



def _extract_drag_drop_target_value(instruction_text: str) -> Optional[str]:
    text = _norm(instruction_text or "")
    if not text:
        return None

    keyed = re.search(r"(?:num[eé]ro|nombre|valeur)\s*[:=\-]?\s*(\d+)", text, flags=re.IGNORECASE)
    if keyed:
        return keyed.group(1)

    any_num = re.search(r"\b(\d+)\b", text)
    return any_num.group(1) if any_num else None


def handle_drag_drop_logic(driver):
    def _el_text(el) -> str:
        for getter in (
            lambda: el.text,
            lambda: el.get_attribute("innerText"),
            lambda: el.get_attribute("textContent"),
        ):
            try:
                txt = _norm(getter() or "")
                if txt:
                    return txt
            except Exception:
                continue
        return ""

    def _is_enabled(btn) -> bool:
        if not btn:
            return False
        try:
            if not btn.is_displayed():
                return False
        except Exception:
            pass
        attrs = []
        for k in ("disabled", "aria-disabled", "class"):
            try:
                attrs.append((btn.get_attribute(k) or "").strip().lower())
            except Exception:
                attrs.append("")
        disabled_attr, aria_disabled, cls = attrs
        if disabled_attr and disabled_attr not in ("false", "0"):
            return False
        if aria_disabled in ("true", "1"):
            return False
        if "disabled" in cls:
            return False
        return True

    def _attempt_cta_once() -> bool:
        intercept_only = (os.getenv("CTA_INTERCEPT_ONLY", "") or "").strip().lower() in {"1", "true", "yes", "on"}
        cta_found = False
        try:
            candidates = driver.find_elements(By.CSS_SELECTOR, "button[aria-label='Go to next question']")
            cta_found = any(_is_enabled(btn) for btn in candidates)
        except Exception:
            cta_found = False

        clicked = False
        try:
            clicked = bool(Survey.input_handler.try_click_navigation_cta_any_context(driver))
        except Exception:
            clicked = False

        if intercept_only:
            print(
                "[DRAGDROP][CTA] "
                f"cta_found={str(cta_found).lower()} "
                f"clicked={str(clicked).lower()} "
                f"intercept_ok={str(clicked).lower()}"
            )
        else:
            print(
                "[DRAGDROP][CTA] "
                f"cta_found={str(cta_found).lower()} "
                f"clicked={str(clicked).lower()}"
            )
        return clicked

    def _cdkdrag_cards_ready() -> bool:
        """
        Vérifie que les cards Angular CDK sont réellement rendues dans le viewport :
        au moins un [cdkdrag] doit avoir width > 0 ET height > 0.
        Retourne False si les éléments existent mais n'ont pas encore de dimensions
        (cas typique d'un chargement incomplet côté Angular CDK).
        """
        try:
            return bool(driver.execute_script("""
                const drags = Array.from(document.querySelectorAll('[cdkdrag], .cdk-drag'));
                if (!drags.length) return false;
                return drags.some(function(el) {
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                });
            """))
        except Exception:
            return False

    def _wait_cards_ready(timeout: float = 8.0) -> bool:
        """Attend que les cards cdkdrag aient des dimensions valides (budget borné)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if _cdkdrag_cards_ready():
                return True
            time.sleep(0.3)
        return False

    def _run_drag_attempt(target_value: str) -> bool:
        """
        Cœur du drag-and-drop : localise la source, vérifie les coordonnées,
        exécute le drag (CDP ou ActionChains) et valide le résultat.
        Retourne True si le drag a abouti (drop_zone remplie ou bouton Next activé).
        """
        draggables = driver.find_elements(By.CSS_SELECTOR, "[cdkdrag], .cdk-drag, [draggable='true']")
        try:
            drop_zone = driver.find_element(
                By.CSS_SELECTOR,
                "#dropZoneList.cdk-drop-list.drop-zone, #dropZoneList.drop-zone, #dropZoneList",
            )
            print("[DRAGDROP] drop_zone_selected id=dropZoneList ok=true")
        except Exception:
            print("[DRAGDROP] drop_zone_selected id=dropZoneList ok=false")
            return False
        if not draggables or not drop_zone:
            return False

        source = None
        source_selector = ""
        for drag in draggables:
            try:
                imgs = drag.find_elements(By.CSS_SELECTOR, f'img[alt="{target_value}"]')
                if imgs:
                    source = drag
                    source_selector = f'img[alt="{target_value}"]'
                    break
            except Exception:
                continue

        if source is None:
            for drag in draggables:
                try:
                    imgs = drag.find_elements(By.CSS_SELECTOR, "img")
                except Exception:
                    imgs = []
                for img in imgs:
                    try:
                        src = (img.get_attribute("src") or "")
                    except Exception:
                        src = ""
                    if f'/{target_value}.png' in src or target_value in src:
                        source = drag
                        source_selector = f'img[src*="{target_value}"]'
                        break
                if source is not None:
                    break

        if source is None:
            for drag in draggables:
                txt = _el_text(drag)
                if target_value in txt:
                    source = drag
                    source_selector = "draggable_text"
                    break

        if source is None:
            return False

        print(f"[DRAGDROP] source_found selector={source_selector}")

        next_buttons = driver.find_elements(By.CSS_SELECTOR, "button[aria-label='Go to next question']")
        next_button = next_buttons[0] if next_buttons else None

        offsets = [(0, 0), (15, 0)]
        can_use_cdp = hasattr(driver, "execute_cdp_cmd")
        is_local_env = (os.getenv("RUN_ENV", "local") or "local").strip().lower() == "local"
        for idx, (ox, oy) in enumerate(offsets, start=1):
            print(f"[DRAGDROP] attempt={idx} start")
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", source)
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", drop_zone)
                points = driver.execute_script(
                    """
                    const src = arguments[0];
                    const dst = arguments[1];
                    const ox = arguments[2] || 0;
                    const oy = arguments[3] || 0;
                    if (!src || !dst) return null;
                    const srcRect = src.getBoundingClientRect();
                    const dstRect = dst.getBoundingClientRect();
                    const endX = Math.floor(dstRect.left + dstRect.width / 2 + ox);
                    const endY = Math.floor(dstRect.top + dstRect.height / 2 + oy);
                    const atPoint = document.elementFromPoint(endX, endY);
                    const insideDropZone = !!(atPoint && (atPoint === dst || dst.contains(atPoint)));
                    const insideDraggable = !!(atPoint && atPoint.closest('[cdkdrag], .cdk-drag, [draggable="true"]'));
                    return {
                        startX: Math.floor(srcRect.left + srcRect.width / 2),
                        startY: Math.floor(srcRect.top + srcRect.height / 2),
                        endX,
                        endY,
                        verified: insideDropZone && !insideDraggable,
                        elementTag: atPoint ? atPoint.tagName.toLowerCase() : '',
                        elementId: atPoint && atPoint.id ? atPoint.id : '',
                        elementClass: atPoint && atPoint.className ? String(atPoint.className) : '',
                    };
                    """,
                    source,
                    drop_zone,
                    ox,
                    oy,
                )
                if not points:
                    raise RuntimeError("drag_points_unavailable")

                element_desc = f"{points.get('elementTag', '')}#{points.get('elementId', '')}.{points.get('elementClass', '')}".strip(".")
                point_ok = bool(points.get("verified"))
                print(f"[DRAGDROP] end_point_verified ok={str(point_ok).lower()} elementFromPoint={element_desc}")
                if not point_ok:
                    continue

                drag_done = False
                if can_use_cdp:
                    start_x = int(points.get("startX", 0))
                    start_y = int(points.get("startY", 0))
                    end_x = int(points.get("endX", 0))
                    end_y = int(points.get("endY", 0))

                    driver.execute_cdp_cmd(
                        "Input.dispatchMouseEvent",
                        {"type": "mouseMoved", "x": start_x, "y": start_y, "button": "none"},
                    )
                    driver.execute_cdp_cmd(
                        "Input.dispatchMouseEvent",
                        {"type": "mousePressed", "x": start_x, "y": start_y, "button": "left", "clickCount": 1},
                    )

                    steps = 8
                    for step in range(1, steps + 1):
                        x = int(start_x + ((end_x - start_x) * step) / steps)
                        y = int(start_y + ((end_y - start_y) * step) / steps)
                        driver.execute_cdp_cmd(
                            "Input.dispatchMouseEvent",
                            {"type": "mouseMoved", "x": x, "y": y, "button": "left", "buttons": 1},
                        )
                        time.sleep(0.02)

                    driver.execute_cdp_cmd(
                        "Input.dispatchMouseEvent",
                        {"type": "mouseReleased", "x": end_x, "y": end_y, "button": "left", "clickCount": 1},
                    )
                    drag_done = True
                elif is_local_env:
                    chain = ActionChains(driver).click_and_hold(source).move_to_element(drop_zone)
                    if ox or oy:
                        chain = chain.move_by_offset(ox, oy)
                    chain.release().perform()
                    drag_done = True
                else:
                    raise RuntimeError("cdp_unavailable_non_local")

                if not drag_done:
                    raise RuntimeError("pointer_drag_failed")
                print(f"[DRAGDROP] attempt={idx} dropped")
            except Exception as e:
                print(f"[DRAGDROP] attempt={idx} dropped error={_short_exc(e)}")
                continue

            deadline = time.time() + 3.0
            enabled = False
            in_drop_zone = False
            while time.time() < deadline:
                try:
                    in_drop_zone = bool(
                        driver.execute_script(
                            """
                            const dst = arguments[0];
                            if (!dst) return false;
                            const draggableInZone = dst.querySelector('[cdkdrag], .cdk-drag, [draggable="true"]');
                            const hasVisibleContent = (dst.innerText || '').trim().length > 0;
                            return !!(draggableInZone || hasVisibleContent);
                            """,
                            drop_zone,
                        )
                    )
                except Exception:
                    in_drop_zone = False
                if next_button is not None and _is_enabled(next_button):
                    enabled = True
                    break
                time.sleep(0.2)

            print(f"[DRAGDROP] attempt={idx} next_enabled={str(enabled).lower()} in_drop_zone={str(in_drop_zone).lower()}")
            if enabled or in_drop_zone:
                _attempt_cta_once()
                return True

        return False

    # --- Extraction de la valeur cible depuis le titre de la question ---
    title_candidates = driver.find_elements(
        By.CSS_SELECTOR,
        "p.question-title[psquestiontitle], p.question-title, [psquestiontitle]",
    )
    instruction = ""
    for title in title_candidates:
        txt = _el_text(title)
        if any(v in txt.lower() for v in ("deposer", "déposer", "glisser", "drag", "drop")):
            instruction = txt
            break
    if not instruction and title_candidates:
        instruction = _el_text(title_candidates[0])

    target_value = _extract_drag_drop_target_value(instruction)
    print(f'[DRAGDROP] target_value={target_value} extracted_from="{instruction}"')
    if not target_value:
        return False

    # --- Garde : si les cards cdkdrag sont présentes mais sans dimensions (Angular CDK
    # pas encore initialisé), on rafraîchit la page une seule fois et on attend le rendu
    # avant de tenter le drag. Signal discriminant : au moins un [cdkdrag] dans le DOM
    # mais aucun avec getBoundingClientRect().width > 0. ---
    cards_in_dom = bool(driver.find_elements(By.CSS_SELECTOR, "[cdkdrag], .cdk-drag"))
    if cards_in_dom and not _cdkdrag_cards_ready():
        print("[DRAGDROP] cards_not_ready=true → refresh + wait (max 8s)")
        try:
            driver.refresh()
        except Exception as _ref_e:
            print(f"[DRAGDROP] refresh_failed error={_short_exc(_ref_e)}")
            return False
        if not _wait_cards_ready(timeout=8.0):
            print("[DRAGDROP] cards_still_not_ready after refresh → abort")
            return False
        print("[DRAGDROP] cards_ready after refresh → retry drag")

    return _run_drag_attempt(target_value)

def handle_start_screen(driver):
    """
    Start screen: accepter cookies si besoin, puis cliquer Start/Commencer.
    """
    import Survey.input_handler

    # 1) si un bandeau cookies bloque, on tente de le fermer (non bloquant)
    try:
        Survey.input_handler.click_cta_strong_any_context(driver, "accepter")
        Survey.input_handler.click_cta_strong_any_context(driver, "accept")
    except Exception:
        pass

    # 2) cliquer Start/Commencer (CTA nav le plus robuste)
    return (
        Survey.input_handler.try_click_navigation_cta_any_context(driver)
        or Survey.input_handler.click_button_by_text(driver, "commencer")
        or Survey.input_handler.click_button_by_text(driver, "démarrer")
        or Survey.input_handler.click_button_by_text(driver, "start")
        or Survey.input_handler.click_button_by_text(driver, "begin")
    )

def handle_end_screen(driver):
    return True  # on laisse la redirection se faire


def handle_error_recovery_screen(driver):
    """
    Page d'erreur récupérable GreenXP (data-testid="layout-card" + id="confirmation-button").
    Clique le bouton "Retour" pour relancer le flux.
    """
    import os
    from Survey.log_utils import log_info, log_debug

    TAG = "error_recovery"

    intercept_only = (os.getenv("CTA_INTERCEPT_ONLY", "") or "").strip().lower() in ("1", "true", "yes", "on")

    try:
        btn = driver.find_element("id", "confirmation-button")
    except Exception:
        log_info(TAG, "CTA introuvable (#confirmation-button absent)")
        return False

    if intercept_only:
        try:
            driver.execute_script("arguments[0].dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true}));", btn)
            log_info(TAG, "CTA trouvé + interception OK (CTA_INTERCEPT_ONLY)")
        except Exception as exc:
            log_info(TAG, f"CTA trouvé + interception impossible : {exc}")
            return False
    else:
        try:
            btn.click()
            log_info(TAG, "Bouton 'Retour' cliqué (error_recovery_screen)")
        except Exception as exc:
            log_debug(TAG, f"click() échoué, fallback JS : {exc}")
            try:
                driver.execute_script("arguments[0].click();", btn)
            except Exception:
                return False

    return True

def handle_captcha_guard(driver):
    """
    CAPTCHA / vérification humaine.

    Sécurité & prédictibilité:
    - Prod/Docker: on n'essaie pas de "résoudre" un CAPTCHA (arret controlé + snapshot si activé).
    - Local: on permet une résolution MANUELLE, puis on ATTEND la redirection / disparition du widget.

    Résolution automatique Tencent (slider puzzle) :
    - Détectée via signaux DOM (#sliderpanel + .verify-img-panel/.verify-gap).
    - Déléguée à captcha.tencent_handler.solve_tencent_auto().
    - Sans impact sur les autres branches captcha (recaptcha, hcaptcha, etc.).
    """
    import os, sys, time

    def _env_truthy(name: str, default: str = "0") -> bool:
        v = (os.getenv(name, default) or "").strip().lower()
        return v in ("1", "true", "yes", "on")

    from config import should_pause_for_captcha, get_captcha_behavior, should_block_for_input
    captcha_behavior = get_captcha_behavior()

    # Snapshot best-effort (utile pour debug / nouveaux cas)
    try:
        if _env_truthy("SNAPSHOT_ON_GUARD", "0"):
            from Survey.page_snapshot import save_snapshot
            save_snapshot(driver, reason="captcha_guard", out_root=os.getenv("SURVEY_SNAPSHOT_DIR"))
    except Exception:
        pass

    # ── Tencent CAPTCHA (slider puzzle) ── résolution automatique via 2Captcha
    try:
        is_tencent = bool(driver.execute_script(
            "var r = document.querySelector('#sliderpanel');"
            "if (!r) return false;"
            "return !!(r.querySelector('.verify-img-panel') || r.querySelector('.verify-gap') || r.querySelector('.verify-bar-area'));"
        ))
    except Exception:
        is_tencent = False

    if is_tencent:
        from Survey.log_utils import log_info as _log_info
        try:
            from captcha.tencent_handler import solve_tencent_auto
            _solved = solve_tencent_auto(driver)
        except Exception as _te:
            _log_info("CAPTCHA_GUARD", f"Exception inattendue tencent_handler : {_te}")
            _solved = False
        if not _solved:
            from Management.guards.runtime_guard import get_guard
            get_guard().signal_strict_survey("slider_captcha_unresolvable")
        return _solved

    # PROD/DOCKER: arret controlé (pas de bypass)
    if captcha_behavior == "restart":
        print("[GUARD] CAPTCHA détecté ; arret controlé (prod/docker)")
        from Management.guards.runtime_guard import get_guard
        get_guard().signal_strict_survey("captcha_guard_restart")
        return False

    # AUTO: résolution via 2Captcha (local + prod, si clé configurée)
    if captcha_behavior == "auto_2captcha":
        print("[GUARD] Tentative de résolution automatique via 2Captcha...")
        try:
            from captcha.recaptcha_handler import solve_recaptcha_v2_auto
            resolved = solve_recaptcha_v2_auto(driver)
        except Exception as _re:
            print(f"[GUARD] Erreur recaptcha_handler : {_re}")
            resolved = False
        if resolved:
            print("[GUARD] reCAPTCHA résolu automatiquement")
            return True
        else:
            print("[GUARD] Échec résolution automatique → abandon survey")
            from Management.guards.runtime_guard import get_guard
            get_guard().signal_strict_survey("captcha_auto_failed")
            return False

    # AWS/non-local : soft-restart même si auto_2captcha échoue (pas de terminal interactif)
    from config import is_local_env
    if not is_local_env():
        print("[GUARD] CAPTCHA détecté ; soft-restart (aws/non-local)")
        from Management.guards.runtime_guard import get_guard
        get_guard().signal_strict_survey("captcha_guard_aws")
        return False

    # LOCAL: pause manuelle si terminal interactif
    print("[GUARD] CAPTCHA détecté ; résolution MANUELLE requise (local)")
    try:
        if should_block_for_input():
            input("[LOCAL][PAUSE] Résous le CAPTCHA dans le navigateur, puis appuie Entrée:\n")
    except KeyboardInterrupt:
        print("[LOCAL] Abandon demandé.")
        return False
    except Exception:
        pass

    # Après rsolution: attendre que (1) l'URL change OU (2) le widget disparaisse
    try:
        before_url = driver.current_url
    except Exception:
        before_url = ""

    deadline = time.time() + float(os.getenv("CAPTCHA_WAIT_SEC", "25") or 25)

    while time.time() < deadline:
        try:
            cur_url = driver.current_url
        except Exception:
            cur_url = ""

        if cur_url and before_url and cur_url != before_url:
            print("[GUARD] CAPTCHA résolu ; URL changée")
            return True

        try:
            still_there = bool(driver.execute_script(r"""
                const isVisible = (e) => {
                  try{
                    const cs = getComputedStyle(e);
                    if (!cs) return false;
                    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') return false;
                    const r = e.getBoundingClientRect();
                    if (!r) return false;
                    if (r.bottom < 0 || r.right < 0) return false;
                    return (r.width > 2 && r.height > 2);
                  }catch(_){ return false; }
                };

                // 1) Slider puzzle (NIQ/GfK)
                const slider = document.querySelector('#sliderpanel');
                if (
                  slider && isVisible(slider) &&
                  slider.querySelector('.verify-img-panel, .verify-gap, .verify-bar-area, .verify-move-block, .verify-sub-block')
                ) return true;

                // 2) reCAPTCHA / hCaptcha visibles (ignorer 0x0 / 1x1)
                const widgetSels = [
                  "iframe[src*='recaptcha']",
                  "iframe[src*='captcha']",
                  "iframe[src*='hcaptcha']",
                  ".g-recaptcha",
                  ".h-captcha",
                  "#recaptcha",
                  "[data-sitekey]"
                ];
                for (const e of Array.from(document.querySelectorAll(widgetSels.join(",")))) {
                  if (!isVisible(e)) continue;
                  const r = e.getBoundingClientRect();
                  const tn = (e.tagName||"").toLowerCase();
                  if (tn === "iframe" || e.classList.contains("g-recaptcha") || e.classList.contains("h-captcha")) {
                    if (r.width >= 60 && r.height >= 40) return true;
                  } else {
                    if (r.width >= 60 && r.height >= 40) return true;
                  }
                }

                // 3) PureSpectrum CAPTCHA (ps-captcha-question)
                const psRoot = document.querySelector('ps-captcha-question') || document.querySelector('ps-captcha');
                if (psRoot && isVisible(psRoot)) {
                  const img = psRoot.querySelector("img[alt*='captcha' i], img[alt*='ps captcha' i]");
                  const inp = psRoot.querySelector("input[data-e2e='alpha-numeric-input'], ps-alpha-numeric-input input");
                  if (img && inp && isVisible(img) && isVisible(inp)) return true;
                }

                // 4) fallback: input id/name contient captcha (visible)
                for (const i of Array.from(document.querySelectorAll("input[id*='captcha' i], input[name*='captcha' i]"))) {
                  if (!isVisible(i)) continue;
                  const r = i.getBoundingClientRect();
                  if (r.width >= 10 && r.height >= 10) return true;
                }

                return false;
            """))
        except Exception:
            still_there = False

        if not still_there:
            print("[GUARD] CAPTCHA résolu - widget disparu")
            return True

        time.sleep(0.5)

    print("[GUARD] CAPTCHA: timeout d'attente après résolution manuelle (local)")
    return False

# ================================
# Anti double-fallback guard
# ================================

def _new_attempt_context(driver):
    """
    Initialise un contexte de tentative pour UNE instruction.
    Empeche toute stratégie d'etre exécutée 2 fois.
    """
    ctx = {
        "attempted": set(),
    }
    driver._action_attempt_ctx = ctx
    return ctx

def _try(driver, name: str, fn):
    """
    Exécute fn() UNE SEULE FOIS par nom de stratégie.
    """
    ctx = getattr(driver, "_action_attempt_ctx", None)
    if ctx is None:
        ctx = _new_attempt_context(driver)

    if name in ctx["attempted"]:
        return False

    ctx["attempted"].add(name)
    ok = bool(fn())
    if ok:
        log_info("[TARGET]", f"apply ok=true strategy={name} reason=applied")
    return ok

# --------------------------- Dispatcher principal ---------------------------
def _aa__norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def _aa__contains(hay: str, needle: str) -> bool:
    h = _aa__norm_ws(hay).lower()
    n = _aa__norm_ws(needle).lower()
    return bool(n) and (n in h)

def _aa__safe_scroll_center(driver, el) -> None:
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center', inline:'center'});",
            el
        )
    except Exception:
        pass

def _aa__safe_click(driver, el) -> bool:
    """
    Click robuste, sans retry infini.
    On scroll puis:
      1) click natif
      2) click ActionChains
      3) click JS
    """
    try:
        _aa__safe_scroll_center(driver, el)
    except Exception:
        pass

    try:
        el.click()
        return True
    except Exception:
        pass

    try:
        from selenium.webdriver.common.action_chains import ActionChains
        ActionChains(driver).move_to_element(el).click().perform()
        return True
    except Exception:
        pass

    try:
        driver.execute_script("arguments[0].click();", el)
        return True
    except Exception:
        return False

def _aa__is_mat_checked(node) -> bool:
    try:
        cls = (node.get_attribute("class") or "")
        return "mat-radio-checked" in cls
    except Exception:
        return False

def _aa__try_answer_matrix(driver, full_question: str, choice_text: str) -> bool:
    """
    Résout le cas Ask&Answer MATRIX Angular Material, en évitant les éléments cachés.
    Retourne True si l'action a bien été appliquée.
    """
    try:
        from selenium.webdriver.common.by import By
    except Exception:
        return False

    q = full_question or ""
    choice = _aa__norm_ws(choice_text)
    if not choice:
        return False

    # Garde-fou : on n'active ce helper que si on détecte app-matrix-question (Ask&Answer)
    try:
        if not driver.find_elements(By.XPATH, "//app-matrix-question"):
            return False
    except Exception:
        return False

    # Extraire le libell de ligne (celebrity) depuis " ...  <statement>"
    statement = ""
    for sep in ("", " - ", " – "):
        if sep in q:
            statement = q.split(sep, 1)[1]
            break
    statement = _aa__norm_ws(statement)
    statement_key = _aa__norm_ws(statement.split("(", 1)[0]) if statement else ""

    # ========== 1) Desktop table visible ==========
    try:
        tables = driver.find_elements(By.XPATH, "//app-matrix-question//table[contains(@class,'mat-table')]")
    except Exception:
        tables = []

    table = None
    for t in tables:
        try:
            if t.is_displayed():
                table = t
                break
        except Exception:
            continue

    if table is not None and statement_key:
        # Trouver la matrixId via le header (texte == choix)
        mid = None
        try:
            headers = table.find_elements(By.XPATH, ".//thead//th[contains(@class,'matrixId-')]")
        except Exception:
            headers = []

        for th in headers:
            try:
                if not th.is_displayed():
                    continue
                txt = _aa__norm_ws(th.text)
                if txt and _aa__contains(txt, choice):
                    cls = th.get_attribute("class") or ""
                    m = re.search(r"matrixId-(\d+)", cls)
                    if m:
                        mid = m.group(1)
                        break
            except Exception:
                continue

        if mid:
            # Trouver la ligne correspondant au statement (colonne "statement")
            try:
                rows = table.find_elements(By.XPATH, ".//tbody//tr")
            except Exception:
                rows = []

            for row in rows:
                try:
                    if not row.is_displayed():
                        continue
                    st_cell = row.find_element(By.XPATH, ".//td[contains(@class,'mat-column-statement')]")
                    st_txt = _aa__norm_ws(st_cell.text)
                    if not _aa__contains(st_txt, statement_key):
                        continue

                    # Cellule de la colonne mid
                    cell = row.find_element(By.XPATH, f".//td[contains(@class,'matrixId-{mid}')]")
                    # Cliquer la radio dans cette cellule (uniquement éléments visibles)
                    candidates = []
                    try:
                        candidates = cell.find_elements(
                            By.XPATH,
                            ".//mat-radio-button | .//label[contains(@class,'mat-radio-label')] | .//span[contains(@class,'mat-radio-container')] | .//input[@type='radio']"
                        )
                    except Exception:
                        candidates = []

                    for cand in candidates:
                        try:
                            if not cand.is_displayed():
                                continue
                        except Exception:
                            continue

                        _aa__safe_click(driver, cand)

                        # Vérif: mat-radio-button checked dans la cellule
                        try:
                            mr = cell.find_element(By.XPATH, ".//mat-radio-button[1]")
                            if _aa__is_mat_checked(mr):
                                return True
                        except Exception:
                            pass

                    return False
                except Exception:
                    continue

    # ========== 2) Mobile expansion visible ==========
    # (utile si la table est cachée par responsive)
    if statement_key:
        try:
            panels = driver.find_elements(By.XPATH, "//app-matrix-question//mat-expansion-panel")
        except Exception:
            panels = []

        for p in panels:
            try:
                if not p.is_displayed():
                    continue

                # header contient le statement
                hdr = p.find_element(By.XPATH, ".//mat-expansion-panel-header")
                hdr_txt = _aa__norm_ws(hdr.text)
                if not _aa__contains(hdr_txt, statement_key):
                    continue

                # ouvrir si nécessaire
                try:
                    expanded = (hdr.get_attribute("aria-expanded") or "").strip().lower() == "true"
                except Exception:
                    expanded = False
                if not expanded:
                    _aa__safe_click(driver, hdr)

                # choisir l'option par texte (labels visibles)
                opts = p.find_elements(
                    By.XPATH,
                    ".//mat-radio-button//label[contains(@class,'mat-radio-label')][.//span[contains(@class,'mat-radio-label-content')]]"
                )
                for lab in opts:
                    try:
                        if not lab.is_displayed():
                            continue
                        txt = _aa__norm_ws(lab.text)
                        if txt and _aa__contains(txt, choice):
                            _aa__safe_click(driver, lab)
                            # vérifier checked sur mat-radio-button parent
                            try:
                                mr = lab.find_element(By.XPATH, "ancestor::mat-radio-button[1]")
                                if _aa__is_mat_checked(mr):
                                    return True
                            except Exception:
                                pass
                            return False
                    except Exception:
                        continue
            except Exception:
                continue

    return False

def execute_action(
    driver,
    instruction: str,
    *,
    allow_mx_vertical_carousel_advance: bool = True,
) -> bool:
    """
    Applique une instruction OpenAI (batch + legacy).

    Formats supportés :
    - QID //// target_id //// valeur //// itype //// contexte
    - QID //// valeur //// itype //// contexte
    - target_id //// valeur //// itype //// contexte
    - valeur //// itype //// contexte
    """
    import Survey.input_handler
    import Survey.dom_context_mapper as dom_context_mapper
    import Survey.dropdown_block_resolver as dropdown_block_resolver
    from Survey.action_types import Action as ActionModel

    import os
    debug_target = is_debug()

    # Print 1 seule fois pour prouver que CE fichier est chargé
    if debug_target and not getattr(driver, "_target_debug_header_printed", False):
        log_debug("[TARGET_DEBUG]", f"action_dispatcher file={__file__}")
        driver._target_debug_header_printed = True

    if isinstance(instruction, ActionModel):
        instruction = instruction.to_dispatcher_line()

    log_info("[TARGET]", f"execute_action raw={instruction!r}")

    if not instruction or not instruction.strip():
        return False

    for raw in _split_multiline_instruction(instruction):
        parsed = _parse_action_line(raw)

        target_id = (parsed.get("target_id") or "").strip() or None
        value = (parsed.get("value") or "").strip()
        ctx = (parsed.get("context") or "").strip()

        raw_itype = (parsed.get("itype") or "").strip()
        _, itype, _ = _parse_typed_instruction3(f"x //// {raw_itype} //// y")
        itype = (itype or "").strip()
        raw_itype_norm = _norm_lc(raw_itype)
        matrix_like_itype = bool(raw_itype_norm and ("matrix" in raw_itype_norm or "grille" in raw_itype_norm))

        parsed_row, parsed_col = _parse_matrix_value_parts(value)
        matrix_row = parsed_row or ctx
        matrix_col = parsed_col or value

        matrix_by_target = False
        if target_id:
            try:
                p = get_target(target_id) or {}
                p_itype = (p.get("itype") or "").strip().lower()
                matrix_by_target = p_itype == "matrix"
            except Exception:
                matrix_by_target = False

        matrix_intent = matrix_like_itype or matrix_by_target
        cardsort_intent = False
        target_payload = None
        if target_id:
            try:
                target_payload = get_target(target_id) or None
            except Exception:
                target_payload = None
        if raw_itype_norm == "cardsort":
            cardsort_intent = True
        elif isinstance(target_payload, dict):
            cardsort_intent = (target_payload.get("kind") or "").strip().lower() == "cardsort"

        log_info("[TARGET]", f"parsed target_id={target_id!r} itype={itype!r} value={value!r} context_len={len(ctx)}")

        if not value and not target_id:
            continue

        # Matrix: row + col obligatoires. Pas de clic aveugle.
        if matrix_intent:
            encuesta_matrix_target = bool((target_payload or {}).get("encuesta_matrix") is True)
            if encuesta_matrix_target:
                if not (matrix_row or "").strip():
                    log_info("[MATRIX_ABORT]", "reason='missing_row' strategy=encuesta_matrix")
                    return False
                if not (matrix_col or "").strip():
                    log_info("[MATRIX_ABORT]", "reason='missing_col' strategy=encuesta_matrix")
                    return False

                return _try_encuesta_matrix_set(driver, matrix_row, matrix_col)

            if not (matrix_row or "").strip():
                log_info("[MATRIX_ABORT]", "reason='missing_row'")
                return False
            if not (matrix_col or "").strip():
                log_info("[MATRIX_ABORT]", "reason='missing_col'")
                return False

            if _try_gridclick_matrix_set(driver, matrix_row, matrix_col):
                return True

            if _try_table_matrix_sge_set(driver, target_payload or {}, matrix_row, matrix_col):
                return True

            try:
                if dom_context_mapper.try_click_matrix_by_visual_mapping(
                    driver,
                    row_label=matrix_row,
                    col_label=matrix_col,
                    debug=True,
                ):
                    log_info("[TARGET]", "apply ok=true strategy=matrix_visual_map reason=applied")
                    return True
            except Exception:
                pass

            try:
                if Survey.input_handler._looks_like_matrix(driver) and Survey.input_handler.click_matrix_cell_by_row_and_col(
                    driver,
                    row_label=matrix_row,
                    col_label=matrix_col,
                ):
                    log_info("[TARGET]", "apply ok=true strategy=matrix_cell reason=applied")
                    return True
            except Exception:
                pass

        if cardsort_intent:
            cardsort_card = ctx
            cardsort_buckets = [p.strip() for p in value.split("|") if p.strip()]
            if not cardsort_card or not cardsort_buckets:
                log_info("[CARDSORT_ABORT]", "reason='missing_card_or_bucket'")
                return False

            if solve_focusvision_cardsort(
                driver,
                max_cards=3,
                assignments=[{"card": cardsort_card, "buckets": cardsort_buckets}],
            ):
                log_info("[TARGET]", "apply ok=true strategy=cardsort reason=applied")
                return True

            log_info("[CARDSORT_ABORT]", "reason='apply_failed'")
            return False

        # 1) target_id => application directe via DOM_REGISTRY
        # IMPORTANT: sliderpoints (FocusVision/Decipher) ne doivent PAS passer par le chemin dropdown générique,
        # sinon on peut obtenir des faux positifs (dropdown ouvert) ou une valeur décalée (mapping 0/1-based).
        # On route donc explicitement vers set_sliderpoints.
        skip_apply_by_target_id = False
        if target_id:
            try:
                _p = get_target(target_id) or {}

                # ── Askia ADC Slider (noUiSlider) ──────────────────────────────────────
                # L'input hidden n'est pas interactable via Selenium click.
                # On injecte la valeur numérique (0–10) directement via JS,
                # puis on dispatche les events attendus par l'adcSlider jQuery plugin.
                if _p.get("askia_adc_slider"):
                    skip_apply_by_target_id = True
                    _adc_input_name = (_p.get("input_name") or "").strip()
                    _adc_value_map = _p.get("value_map") or {}
                    _adc_dk_xpath = None

                    # Résoudre la valeur numérique depuis value_map
                    _v_norm_adc = _norm_lc(value)
                    _v_fold_adc = _fold_norm_lc(value)
                    _adc_numeric = (
                        _adc_value_map.get(_v_norm_adc)
                        or (_adc_value_map.get(_v_fold_adc) if _v_fold_adc else None)
                    )
                    if not _adc_numeric:
                        for _k, _v in _adc_value_map.items():
                            _kn = _norm_lc(_k)
                            if _v_norm_adc and (_v_norm_adc == _kn or _v_norm_adc in _kn or _kn in _v_norm_adc):
                                _adc_numeric = _v
                                break
                            _kf = _fold_norm_lc(_k)
                            if _v_fold_adc and (_v_fold_adc == _kn or _v_fold_adc in _kn or _kn in _v_fold_adc
                                                or _v_fold_adc == _kf or _v_fold_adc in _kf or _kf in _v_fold_adc):
                                _adc_numeric = _v
                                break

                    if _adc_numeric and _adc_input_name:
                        # Cas DK : valeur non numérique → clic sur div.dk
                        _is_dk = not str(_adc_numeric).lstrip("-").isdigit()
                        if _is_dk:
                            # Récupérer le XPath DK depuis option_xpath_map
                            _adc_opt_map = _p.get("option_xpath_map") or {}
                            _adc_dk_xpath = (
                                _adc_opt_map.get(_v_norm_adc)
                                or (_adc_opt_map.get(_v_fold_adc) if _v_fold_adc else None)
                            )
                            if _adc_dk_xpath:
                                try:
                                    _dk_el = driver.find_element(By.XPATH, _adc_dk_xpath)
                                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", _dk_el)
                                    driver.execute_script("arguments[0].click();", _dk_el)
                                    log_info("[TARGET]", "apply ok=true strategy=askia_adc_slider_dk reason=applied")
                                    # Cache
                                    _radio_cache_adc = _get_block_strategy_memory(driver).get("radio", {})
                                    _radio_cache_adc["askia_adc_slider"] = "target_id"
                                    continue
                                except Exception as _dk_e:
                                    log_debug("[TARGET_DEBUG]", f"askia_adc_slider DK click failed: {_short_exc(_dk_e)}")
                            continue
                        else:
                            # Cas slider numérique : inject JS value + trigger noUiSlider
                            _adc_pos = int(_adc_numeric)   # 0–10
                            _adc_ok = False
                            try:
                                _adc_ok = bool(driver.execute_script(
                                    """
                                    var name = arguments[0], pos = arguments[1];
                                    var inp = document.querySelector("input[type='hidden'][name='" + name + "']");
                                    if (!inp) return false;
                                    // Remonter au container adc-slider
                                    var container = inp.closest('.adc-slider');
                                    if (!container) return false;
                                    // Récupérer les items depuis le plugin jQuery (valeurs 5306-5317)
                                    var jqCont = window.jQuery && window.jQuery(container);
                                    var items = null;
                                    try { items = jqCont && jqCont.data('adcSlider') && jqCont.data('adcSlider').items; } catch(e) {}
                                    var targetValue = null;
                                    if (items && items[pos] && items[pos].value !== undefined) {
                                        targetValue = String(items[pos].value);
                                    } else {
                                        // fallback: la valeur brute du DOM correspond à 5306+pos
                                        targetValue = String(5306 + pos);
                                    }
                                    // Injecter dans l'input hidden
                                    var nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                                    nativeInputValueSetter.call(inp, targetValue);
                                    inp.dispatchEvent(new Event('input',  {bubbles:true}));
                                    inp.dispatchEvent(new Event('change', {bubbles:true}));
                                    // Mettre à jour le handle noUiSlider (visuel)
                                    var origin = container.querySelector('.noUi-origin');
                                    if (origin) {
                                        origin.style.left = (pos * 10) + '%';
                                    }
                                    // Marquer le container comme selected
                                    var sc = container.querySelector('.sliderContainer');
                                    if (sc) { sc.classList.add('selected'); }
                                    return true;
                                    """,
                                    _adc_input_name,
                                    _adc_pos,
                                ))
                            except Exception as _adc_e:
                                log_debug("[TARGET_DEBUG]", f"askia_adc_slider JS inject failed: {_short_exc(_adc_e)}")

                            if _adc_ok:
                                log_info("[TARGET]", f"apply ok=true strategy=askia_adc_slider reason=applied pos={_adc_pos}")
                                # Cache partagé : toute la page utilise la même stratégie
                                _radio_cache_adc = _get_block_strategy_memory(driver).get("radio", {})
                                _radio_cache_adc["askia_adc_slider"] = "target_id"
                                continue
                            else:
                                log_debug("[TARGET_DEBUG]", f"askia_adc_slider JS inject returned false name={_adc_input_name!r}")
                    else:
                        log_debug("[TARGET_DEBUG]", f"askia_adc_slider: valeur non résolue value={value!r} value_map={list(_adc_value_map.keys())[:5]}")
                    continue

                _m = _p.get("meta") or {}
                if (_m.get("source") or "").strip().lower() == "sq-sliderpoints":
                    skip_apply_by_target_id = True

                    # Support iframe: on se place dans frame_chain si présent (meme logique que _apply_by_target_id)
                    frame_chain = _p.get("frame_chain") or []
                    if not frame_chain:
                        try:
                            driver.switch_to.default_content()
                        except Exception:
                            pass
                    else:
                        try:
                            from Survey.frame_utils import switch_to_frame_chain  # type: ignore
                            switch_to_frame_chain(driver, frame_chain)
                        except Exception:
                            pass

                    # sliderpoints: une seule stratégie (DOM). Pas de fallback générique,
                    # sinon on peut écraser une sélection correcte (ex: valeur contenant une virgule).
                    ok_sp = Survey.input_handler.set_sliderpoints(driver, value, context_hint=ctx)
                    if ok_sp:
                        log_info("[TARGET]", "apply ok=true strategy=target_id_sliderpoints reason=applied")
                        return True
                    continue

            except Exception as e:
                # meme en exception: pas de fallback générique pour sliderpoints
                continue

        if target_id and not skip_apply_by_target_id:
            try:
                if _apply_by_target_id(
                    driver,
                    target_id,
                    itype,
                    value,
                    allow_mx_vertical_carousel_advance=allow_mx_vertical_carousel_advance,
                ):
                    applied = True  # initialisation explicite avant post-verification MetrixLab
                    if applied:
                        # Post-vérification spécifique MetrixLab/Toluna checkboxQT/radioQT.
                        # Le clic seul ne suffit pas: cette UI custom ne confirme pas l'état via
                        # input.checked de manière fiable. L'état réel est porté par:
                        #   - .option_checkbox.input_on
                        #   - .option_label.input_label_on
                        try:
                            verified = driver.execute_script(
                                r"""
                                const tid = arguments[0];
                                if (!tid) return true;

                                const groups = Array.from(document.querySelectorAll('div.answer_options'));
                                if (!groups.length) return true;

                                const hasQt = groups.some(w => {
                                    const inp = w.querySelector('input[name]');
                                    return inp && /^(checkbox|radio)$/i.test(inp.type || '') && (inp.className || '').includes('QT');
                                });
                                if (!hasQt) return true;

                                // Cas group_<hash> : vérifier qu'au moins une option du groupe est réellement activée
                                if (String(tid).startsWith('group_')) {
                                    return groups.some(w => {
                                    const cb = w.querySelector('.option_checkbox');
                                    const lb = w.querySelector('.option_label');
                                    return !!(
                                        (cb && cb.classList.contains('input_on')) ||
                                        (lb && lb.classList.contains('input_label_on'))
                                    );
                                    });
                                }

                                // Cas option individuelle : tenter de retrouver le wrapper via l'input enregistré
                                const allInputs = Array.from(document.querySelectorAll('div.answer_options input[name]'));
                                for (const inp of allInputs) {
                                    const wrap = inp.closest('div.answer_options');
                                    if (!wrap) continue;
                                    const cb = wrap.querySelector('.option_checkbox');
                                    const lb = wrap.querySelector('.option_label');
                                    if (
                                    (cb && cb.classList.contains('input_on')) ||
                                    (lb && lb.classList.contains('input_label_on'))
                                    ) {
                                    return true;
                                    }
                                }
                                return false;
                                """,
                                target_id,
                            )
                        except Exception:
                            verified = False

                        if verified:
                            return True

                        # Faux positif: la stratégie target_id a "cliqué" mais l'UI n'a pas appliqué l'état.
                        # Continuer vers le fallback label-based au lieu de déclarer succès.
                        applied = False

                    log_info("[TARGET]", "apply ok=true strategy=target_id reason=applied")
                    return True
            except Exception as e:
                if debug_target:
                    log_debug("[TARGET_DEBUG]", f"_apply_by_target_id exception: {type(e).__name__}: {e}")

        # 2) fallback legacy: label == valeur (IMPORTANT: pas QID)
        label = value

        _new_attempt_context(driver)

        # 2⃣ SANITIZER
        try:
            safe_label = _sanitize_instruction_with_page_context(driver, label, itype or "")
            if safe_label != label:
                label = safe_label
        except Exception:
            pass

        # ==========================================================
        # 🟦 BUTTON
        # ==========================================================
        if itype == "button":

            if _try(driver, "btn_text", lambda:
                Survey.input_handler.click_button_by_text(driver, label)
            ):
                if _wait_for_button_effect(driver):
                    return True

            if _try(driver, "cta_strong", lambda:
                Survey.input_handler.click_cta_strong_any_context(
                    driver, label_hint=label, allow_generic=True
                )
            ):
                if _wait_for_button_effect(driver):
                    return True

            if _try(driver, "btn_fallback_radio", lambda:
                Survey.input_handler.click_radio_by_label(driver, label, context_hint=ctx)
            ):
                return True

            if _try(driver, "btn_fallback_checkbox", lambda:
                Survey.input_handler.click_checkbox_by_label(driver, label, context_hint=ctx)
            ):
                return True

        # ==========================================================
        # 🟦 DROPDOWN
        # ==========================================================
        if itype == "dropdown":

            # Guard: sliderpoints are rendered as <select> but behave like Likert sliders.
            # We forbid generic dropdown fallbacks here because they can return True without selecting a value.
            is_sliderpoints_target = False
            if target_id:
                try:
                    _p = get_target(target_id) or {}
                    _m = _p.get("meta") or {}
                    if (_m.get("source") or "").strip().lower() == "sq-sliderpoints":
                        is_sliderpoints_target = True
                except Exception:
                    pass

            if is_sliderpoints_target:
                if _try(driver, "dropdown_sliderpoints", lambda:
                    Survey.input_handler.set_sliderpoints(driver, label, context_hint=ctx)
                ):
                    return True
                # No other fallback for sliderpoints (avoid false positives).
                continue

            if ctx and _try(driver, "dropdown_block", lambda:
                dropdown_block_resolver.try_resolve_dropdown_block(
                    driver,
                    context_question=ctx,
                    value=label,
                    debug=True,
                )
            ):
                return True

            if _try(driver, "dropdown_sliderpoints", lambda:
                Survey.input_handler.set_sliderpoints(driver, label, context_hint=ctx)
            ):
                return True

            # Point d'entrée unique pour les dropdowns.
            # select_option_with_hint sélectionne directement les <select> natifs
            # et ouvre lui-même les dropdowns custom. Ne pas appeler dropdown_open
            # avant: sur un <select> natif, l'ouverture/focus clavier peut modifier
            # la valeur courante avant l'application de la réponse attendue.
            field_hint = ctx or label

            if _try(driver, "dropdown_select", lambda:
                Survey.input_handler.select_option_with_hint(
                    driver, label, field_hint=field_hint, context_hint=ctx
                )
            ):
                driver._last_dropdown_hint = None
                return True

            # nettoyage: ne pas polluer l'action suivante
            driver._last_dropdown_hint = None

        # ==========================================================
        # 🟦 CHECKBOX
        # ==========================================================
        if itype == "checkbox":

            # ✅ NEW: si OpenAI renvoie "Oui" pour une checkbox "statement", on clique le statement (ctx)
            if _norm_lc(label) in {"oui", "yes", "true", "1", "checked", "on", "x"} and ctx and len(ctx) >= 6:
                label = ctx

            checkbox_cache = _get_block_strategy_memory(driver).get("checkbox", {})
            cache_key = (target_id or "").strip() or _norm_lc(ctx)

            def _run_checkbox_strategy(strategy_name: str) -> bool:
                strategy_map = {
                    "checkbox_main": lambda: Survey.input_handler.click_checkbox_by_label(driver, label, context_hint=ctx),
                    "checkbox_buttonish": lambda: Survey.input_handler.click_checkbox_buttonish_by_label(driver, label, context_hint=ctx),
                    "checkbox_fallback_radio": lambda: Survey.input_handler.click_radio_by_label(driver, label, context_hint=ctx),
                }
                fn = strategy_map.get(strategy_name)
                if fn is None:
                    return False
                if _try(driver, strategy_name, fn):
                    if cache_key:
                        checkbox_cache[cache_key] = strategy_name
                    return True
                return False

            preferred_strategy = checkbox_cache.get(cache_key) if cache_key else None
            ordered_strategies = ["checkbox_main", "checkbox_buttonish", "checkbox_fallback_radio"]

            if preferred_strategy:
                if debug_target:
                    log_debug("[TARGET_DEBUG]", f"checkbox reuse cached_strategy={preferred_strategy} target_id={cache_key}")
                if _run_checkbox_strategy(preferred_strategy):
                    return True
                # DOM state changed after previous clicks — invalidate cache and retry full list
                if cache_key and cache_key in checkbox_cache:
                    del checkbox_cache[cache_key]
                log_debug("[TARGET_DEBUG]", f"checkbox cache invalidated after failure target_id={cache_key}, retrying full strategy list")
                preferred_strategy = None

            for strategy_name in ordered_strategies:
                if strategy_name == preferred_strategy:
                    continue
                if _run_checkbox_strategy(strategy_name):
                    return True

        # ==========================================================
        # 🟦 RADIO
        # ==========================================================
        if itype == "radio":

            # Ask&Answer (Angular Material MATRIX): éviter les éléments cachés (desktop/mobile) et cliquer la bonne cellule.
            # Ici, "ctx" contient le texte complet de la question (souvent avec " <statement>"),
            # et "label" contient le choix (colonne) à sélectionner.
            question_text = ctx or ""
            answer_text = label or ""

            radio_cache = _get_block_strategy_memory(driver).get("radio", {})
            _tp = target_payload or {}
            _tmr_opt_keys = (
                frozenset((_tp.get("option_xpath_map") or {}).keys())
                if _tp.get("table_matrix_radio")
                else frozenset()
            )
            if _tp.get("askia_adc_slider"):
                # Tous les sliders Askia ADC de la page partagent la même clé de cache
                radio_cache_key = "askia_adc_slider"
                if debug_target:
                    log_debug("[TARGET_DEBUG]", f"radio askia_adc_slider shared_cache_key={radio_cache_key!r} target_id={target_id!r}")
            elif _tmr_opt_keys:
                radio_cache_key = "tmr:" + ",".join(sorted(_tmr_opt_keys))
                if debug_target:
                    log_debug("[TARGET_DEBUG]", f"radio shared_cache_key={radio_cache_key!r} target_id={target_id!r}")
            else:
                radio_cache_key = (target_id or "").strip() or _norm_lc(ctx)

            def _run_radio_strategy(strategy_name: str) -> bool:
                if strategy_name == "aa_answer_matrix":
                    if not (question_text and answer_text):
                        return False
                    ok = _aa__try_answer_matrix(driver, question_text, answer_text)
                    if ok:
                        log_info("[TARGET]", "apply ok=true strategy=aa_answer_matrix reason=applied")
                elif strategy_name == "radio_slider":
                    ok = _try(driver, "radio_slider", lambda:
                        Survey.input_handler.set_sliderpoints(driver, label, context_hint=ctx))
                elif strategy_name == "radio_main":
                    ok = _try(driver, "radio_main", lambda:
                        Survey.input_handler.click_radio_by_label(driver, label, context_hint=ctx))
                elif strategy_name == "radio_buttonish":
                    ok = _try(driver, "radio_buttonish", lambda:
                        Survey.input_handler.click_checkbox_buttonish_by_label(driver, label, context_hint=ctx))
                else:
                    return False
                if ok and radio_cache_key:
                    radio_cache[radio_cache_key] = strategy_name
                return ok

            preferred_radio_strategy = radio_cache.get(radio_cache_key) if radio_cache_key else None
            radio_ordered_strategies = ["aa_answer_matrix", "radio_slider", "radio_main", "radio_buttonish"]

            if preferred_radio_strategy:
                if debug_target:
                    log_debug("[TARGET_DEBUG]", f"radio reuse cached_strategy={preferred_radio_strategy} cache_key={radio_cache_key!r}")
                if _run_radio_strategy(preferred_radio_strategy):
                    return True

            for strategy_name in radio_ordered_strategies:
                if strategy_name == preferred_radio_strategy:
                    continue
                if _run_radio_strategy(strategy_name):
                    return True

        # ==========================================================
        # 🟦 TEXT / NUMBER
        # ==========================================================
        if itype in ("text", "number"):
            try:
                resolved = Survey.question_block_resolver.try_resolve_number_block(
                    driver,
                    context_question=ctx,
                    value=label,
                    min_score=0.75,
                    allow_overwrite=False,
                    debug=True,
                )
                if resolved:
                    log_info("[TARGET]", "apply ok=true strategy=number_block_resolver reason=applied")
                    return True
            except Exception:
                pass

            if _try(driver, "text_input", lambda:
                Survey.input_handler.fill_text_input(driver, label, context_hint=ctx)
            ):
                return True

        # si cette ligne échoue, on tente la suivante (au lieu de return False)
        log_info("[TARGET]", f"apply ok=false reason=no_strategy strategy=none itype={itype!r} target_id={target_id!r}")
        log_debug("[TARGET_DEBUG]", f"Aucune stratégie n'a abouti pour: {raw}")
        continue

    return False

def reset_attempt_context(driver):
    """
    Reset du garde-fou anti double-fallback.
    À appeler avant CHAQUE instruction, sinon une stratgie peut etre bloque par un essai prcdent.
    """
    try:
        _new_attempt_context(driver)
    except Exception:
        # fallback: on efface juste l'attribut
        try:
            if hasattr(driver, "_action_attempt_ctx"):
                delattr(driver, "_action_attempt_ctx")
        except Exception:
            pass


def _init_block_strategy_memory(driver) -> dict:
    """Initialise la mémoire locale des stratégies réussies (durée: plan courant)."""
    cache = {"checkbox": {}, "radio": {}, "click_method": {}}
    driver._block_strategy_memory = cache
    return cache


def _get_block_strategy_memory(driver) -> dict:
    cache = getattr(driver, "_block_strategy_memory", None)
    if not isinstance(cache, dict):
        return _init_block_strategy_memory(driver)
    if not isinstance(cache.get("checkbox"), dict):
        cache["checkbox"] = {}
    if not isinstance(cache.get("radio"), dict):
        cache["radio"] = {}
    if not isinstance(cache.get("click_method"), dict):
        cache["click_method"] = {}
    return cache

def _same_matrix_table(tid1: str, tid2: str) -> bool:
    """True if both target_ids are rows of the same table (same parent block)."""
    try:
        p1 = get_target(tid1) or {}
        p2 = get_target(tid2) or {}

        # Classic HTML table matrix radio (table_matrix_radio)
        if p1.get("table_matrix_radio") and p2.get("table_matrix_radio"):
            opts1 = frozenset((p1.get("option_xpath_map") or {}).keys())
            opts2 = frozenset((p2.get("option_xpath_map") or {}).keys())
            return bool(opts1 and opts2 and opts1 == opts2)

        # Toluna Runtime AnswerRow grid
        if p1.get("runtime_answerrow_radio") and p2.get("runtime_answerrow_radio"):
            opts1 = frozenset((p1.get("option_xpath_map") or {}).keys())
            opts2 = frozenset((p2.get("option_xpath_map") or {}).keys())
            return bool(opts1 and opts2 and opts1 == opts2)

        # Decipher/FocusVision per-row blocks: group_key = "radio:name:<input_name>".
        # Two rows belong to the same grid when they share the same columns.
        # Primary: compare input_name prefix before last dot (e.g. "ans26138.0.1" and
        #   "ans26138.0.23" share "ans26138.0").
        # Fallback: when input_name has no dot (e.g. "QGENRE_MOBILE_r13_left"), compare
        #   the frozenset of option_xpath_map keys — all rows of the same grid share
        #   identical columns.
        _rn = "radio:name:"
        gk1 = (p1.get("group_key") or "")
        gk2 = (p2.get("group_key") or "")
        if gk1.startswith(_rn) and gk2.startswith(_rn):
            in1 = (p1.get("input_name") or "").strip()
            in2 = (p2.get("input_name") or "").strip()
            if in1 and in2:
                dot1 = in1.rfind(".")
                dot2 = in2.rfind(".")
                if dot1 > 0 and dot2 > 0:
                    return in1[:dot1] == in2[:dot2]
            opts1 = frozenset((p1.get("option_xpath_map") or {}).keys())
            opts2 = frozenset((p2.get("option_xpath_map") or {}).keys())
            if opts1 and opts2:
                return opts1 == opts2

        return False
    except Exception:
        return False


def execute_actions_plan(
    driver,
    actions: list[dict],
    *,
    stop_on_navigation: bool = True,
    rescan_between_actions: bool = True,
) -> bool:
    """
    Applique une série d'actions (issues du batch OpenAI).
    - reset attempt context avant chaque action
    - (NEW) rescan DOM entre actions si risque de re-render (vite xpaths obsolètes)
    """
    success_any = False

    # Mémoire locale par bloc, bornée au plan d'actions courant.
    _init_block_strategy_memory(driver)

    # Compteurs ordinaux kantar_rowrank : réinitialisés à chaque plan (par qid)
    driver._kantar_rr_counts = {}
    driver._kantar_rowrank_ordinal = 1

    try:
        url_before = driver.current_url
    except Exception:
        url_before = ""

    # cap sécurité (évite un flood si OpenAI hallucine)
    # Par défaut on accepte plus que 25 pour couvrir les matrices longues (ex: 28 items).
    try:
        max_actions = int(os.getenv("MAX_ACTIONS_PER_PLAN", "60") or 60)
    except Exception:
        max_actions = 60
    if max_actions < 1:
        max_actions = 1

    actions = (actions or [])
    # IMPORTANT: si OpenAI a renvoyé un plan plus long que MAX_ACTIONS_PER_PLAN,
    # on ne tronque pas ici : le parser batch borne déja  (max_select / qid_constraints),
    # donc la taille reste controlée. Tronquer = "dernières questions jamais appliquées".
    if actions and len(actions) > max_actions:
        print(f"[PLAN] MAX_ACTIONS_PER_PLAN={max_actions} < actions={len(actions)} -> pas de cap (plan déja  borné)")
        max_actions = len(actions)

    actions = actions[:max_actions]

    # Heuristique simple & prédictible:
    # - Un dropdown de langue peut reloader la page et annuler les réponses précédentes (ex: checkbox consent).
    # => si on détecte un "language selector", on l'applique AVANT le reste.
    def _is_language_dropdown(act: dict) -> bool:
        try:
            it = (act.get("itype") or "").strip().lower()
            if it != "dropdown":
                return False
            blob = " ".join([(act.get("context") or ""), (act.get("value") or "")]).lower()
            if any(k in blob for k in ("langue", "language", "idioma", "sprache", "lingua")):
                return True
            # valeurs frquentes (au cas où le contexte est vide)
            if any(k in blob for k in ("français", "anglais", "english", "español", "spanish", "deutsch", "german", "italiano", "portugu", "nederlands", "dutch")):
                return True
            return False
        except Exception:
            return False

    if any(_is_language_dropdown(a) for a in actions):
        actions = [a for _, a in sorted(list(enumerate(actions)), key=lambda t: (0 if _is_language_dropdown(t[1]) else 1, t[0]))]

    for idx, act in enumerate(actions):
        try:
            value = (act.get("value") or "").strip()
            itype = (act.get("itype") or "").strip()
            context = (act.get("context") or "").strip()

            matrix_row = (act.get("matrix_row_label") or "").strip()
            matrix_col = (act.get("matrix_col_label") or "").strip()
            cardsort_card = (act.get("cardsort_card_label") or "").strip()
            cardsort_buckets = (act.get("cardsort_bucket_labels") or "").strip()
            if matrix_row:
                context = matrix_row
            if matrix_col:
                value = matrix_col
            if cardsort_card:
                context = cardsort_card
            if cardsort_buckets:
                value = cardsort_buckets

            if not value or not itype:
                continue

            reset_attempt_context(driver)

            tid = (act.get("target_id") or "").strip()

            # nfield_dragndrop_hidden matrix: reconstruire value="row || col" pour le handler.
            # Ne pas vider context : l'instruction doit rester 5 parties pour que _parse_action_line
            # retrouve target_id (si context="", le parser filtre la partie vide → 4 parties → target_id=None).
            if matrix_row and matrix_col and tid:
                try:
                    _dnd_p = get_target(tid) or {}
                    if _dnd_p.get("nfield_dragndrop_hidden") and (_dnd_p.get("itype") or "").lower() == "matrix":
                        value = f"{matrix_row} || {matrix_col}"
                except Exception:
                    pass
            qid = (act.get("qid") or "").strip()

            # Kantar rowrank: calcul du rang ordinal pour cette action dans le plan
            if tid:
                try:
                    _rr_p = get_target(tid) or {}
                    if _rr_p.get("kantar_rowrank"):
                        _rr_key = qid or tid
                        _rr_counts = driver._kantar_rr_counts
                        _rr_counts[_rr_key] = _rr_counts.get(_rr_key, 0) + 1
                        driver._kantar_rowrank_ordinal = _rr_counts[_rr_key]
                    else:
                        driver._kantar_rowrank_ordinal = 1
                except Exception:
                    driver._kantar_rowrank_ordinal = 1

            if tid and qid:
                instruction = f"{qid} //// {tid} //// {value} //// {itype} //// {context}"
            elif qid:
                instruction = f"{qid} //// {value} //// {itype} //// {context}"
            else:
                instruction = f"{value} //// {itype} //// {context}"
            # Si dropdown: peut déclencher un refresh/reload sans changer d'URL.
            # On attend la stabilisation avant de continuer, sinon on perd l'état (ex: checkbox décochée).
            before_sig = None
            try:
                if (itype or "").strip().lower() == "dropdown":
                    rs = ""
                    try:
                        rs = driver.execute_script("return document.readyState") or ""
                    except Exception:
                        rs = ""
                    try:
                        html_len = int(driver.execute_script("return document.documentElement.outerHTML.length") or 0)
                    except Exception:
                        html_len = 0
                    before_sig = f"{driver.current_url}|{rs}|{html_len}"
            except Exception:
                before_sig = None

            next_tid = ""
            if idx < (len(actions) - 1):
                try:
                    next_tid = (actions[idx + 1].get("target_id") or "").strip()
                except Exception:
                    next_tid = ""

            # Carousel MX vertical: ne jamais avancer entre deux actions consécutives
            # partageant le même target_id (ex: multi-select checkbox d'un même slide).
            allow_mx_advance = (not tid) or (tid != next_tid)

            ok = execute_action(
                driver,
                instruction,
                allow_mx_vertical_carousel_advance=allow_mx_advance,
            )
            if ok:
                success_any = True
            # Wait DOM stable after dropdown (budget borné)
            if ok and before_sig and (itype or "").strip().lower() == "dropdown":
                try:
                    t0 = time.time()
                    last = None
                    stable_hits = 0
                    while time.time() - t0 < 10.0:
                        try:
                            rs = driver.execute_script("return document.readyState") or ""
                        except Exception:
                            rs = ""
                        try:
                            html_len = int(driver.execute_script("return document.documentElement.outerHTML.length") or 0)
                        except Exception:
                            html_len = 0
                        sig = f"{driver.current_url}|{rs}|{html_len}"

                        # on veut au minimum: readyState complete ET un DOM non trivial
                        if rs == "complete" and html_len > 500:
                            if sig == last:
                                stable_hits += 1
                            else:
                                stable_hits = 0
                                last = sig
                            if stable_hits >= 1:
                                break
                        time.sleep(0.2)
                except Exception:
                    pass

            if stop_on_navigation:
                try:
                    if driver.current_url != url_before:
                        break
                except Exception:
                    pass

            # (NEW) Entre deux actions, le DOM peut re-render (React/Qualtrics/etc.).
            #       On rebuild le registry pour que les target_id restent applicables.
            #       Exception : checkbox/radio consécutives sur la même question (même qid)
            #       → le DOM ne se reconstruit pas entre deux options d'un même bloc.
            if ok and rescan_between_actions and idx < (len(actions) - 1):
                try:
                    itype_lower = (itype or "").lower()
                    if itype_lower in ("radio", "checkbox", "dropdown", "text", "number"):
                        # Sauter le rescan si les deux actions consécutives sont des
                        # checkbox/radio appartenant à la même question (même qid non vide).
                        next_act = actions[idx + 1]
                        next_qid = (next_act.get("qid") or "").strip()
                        next_itype = (next_act.get("itype") or "").strip().lower()
                        same_question_block = (
                            itype_lower in ("radio", "checkbox")
                            and next_itype in ("radio", "checkbox")
                            and bool(qid)
                            and next_qid == qid
                        )
                        _skip_reason = f"same qid={qid!r}" if same_question_block else ""
                        # [DIAG] log avant guard _same_matrix_table
                        log_debug("[RESCAN_DIAG]",
                            f"idx={idx} ok={ok} itype={itype_lower!r} next_itype={next_itype!r} "
                            f"tid={tid!r} next_tid={next_tid!r} same_qblock={same_question_block}"
                        )
                        if not same_question_block and itype_lower == "radio" and next_itype == "radio" and tid and next_tid:
                            if _same_matrix_table(tid, next_tid):
                                same_question_block = True
                                _skip_reason = f"same matrix table tid={tid!r}"
                        if same_question_block:
                            log_debug("[DISPATCH]", f"skip rescan idx={idx} {_skip_reason} ({itype_lower})")
                        else:
                            import Survey.dom_analyzer as dom_analyzer
                            time.sleep(PAUSE_INTER_DISPATCH)  # laisse le framework appliquer l'état
                            dom_analyzer.analyze_dom(driver)  # clear+rebuild registry (target_id stable-ish)
                            # 📈 micro-métrique: nombre de rescans DOM déclenchés sur la page courante
                            try:
                                driver._dom_rescans_this_page = int(getattr(driver, "_dom_rescans_this_page", 0)) + 1
                            except Exception:
                                pass

                except Exception:
                    pass

        except Exception as e:
            try:
                debug_target = is_debug()
                if debug_target:
                    log_debug("[TARGET_DEBUG]", f"execute_actions_plan idx={idx} crashed: {type(e).__name__}: {e}")
            except Exception:
                pass
            continue

    return success_any