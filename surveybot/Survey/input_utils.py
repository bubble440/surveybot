"""
input_utils.py - Fonctions utilitaires et constantes partagées pour input_handler

Ce module contient:
- Constantes globales (placeholders, synonymes CTA, hints de date)
- Fonctions de normalisation de texte (multiples variantes selon les besoins)
- Helpers génériques de DOM (scroll, click sécurisé, vérification état)
- Fonction de pause debug

Toutes ces fonctions sont utilisées par les autres modules input_*.py
"""

from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import (
    TimeoutException,
    StaleElementReferenceException,
    ElementClickInterceptedException,
)
import unicodedata
import re
import time


# =============================================================================
# CONSTANTES GLOBALES
# =============================================================================

# Placeholders typiques pour dropdowns (à ignorer lors de la sélection)
DROPDOWN_PLACEHOLDERS = {
    "veuillez sélectionner", "veuillez selectionner", "sélectionner", "selectionner",
    "please select", "select", "choose", "choose an option", "choose option",
    "select one", "select…", "select...", "seleccione", "seleziona", "wählen",
}

# Tokens de placeholder pour dropdowns (version normalisée)
PLACEHOLDER_TOKENS = {
    "sélectionnez",
    "selectionnez",
    "sélectionner",
    "selectionner",
    "choisir",
    "choisissez",
    "select",
    "select…",
    "select ...",
    "select one",
    "choose",
    "please select",
    "pick one",
}

# Synonymes CTA pour boutons de navigation
CTA_SYNONYMS = [
    "accepter et commencer",
    "accepter",
    "commencer",
    "continuer",
    "suivant",
    "soumettre",
    "valider",
    "start",
    "next",
    "continue",
    "submit",
    "proceed",
]

# Hints pour détection des champs date (month/day/year)
DATE_HINTS = {
    "month": {"placeholders": ("mm",), "names": ("month", "mm"), "maxlen": 2},
    "day":   {"placeholders": ("dd",), "names": ("day", "dd"), "maxlen": 2},
    "year":  {"placeholders": ("yyyy", "yy"), "names": ("year", "yyyy", "yy"), "maxlen": 4},
}

# Synonymes colonnes matrice (FR/EN)
MATRIX_COL_SYNONYMS = {
    # FR
    "oui": "oui",
    "non": "non",
    "d'accord": "daccord",
    "pas d'accord": "pas daccord",
    "plutôt d'accord": "plutot daccord",
    "tout à fait d'accord": "tout a fait daccord",
    "tout a fait d'accord": "tout a fait daccord",
    "tout à fait daccord": "tout a fait daccord",
    "jamais": "jamais",
    "rarement": "rarement",
    "parfois": "parfois",
    "souvent": "souvent",
    "toujours": "toujours",
    "très satisfait": "tres satisfait",
    "satisfait": "satisfait",
    "neutre": "neutre",
    "insatisfait": "insatisfait",
    "très insatisfait": "tres insatisfait",
    "1": "1", "2": "2", "3": "3", "4": "4", "5": "5",
    # EN courants
    "yes": "yes",
    "no": "no",
    "strongly agree": "strongly agree",
    "agree": "agree",
    "neutral": "neutral",
    "disagree": "disagree",
    "strongly disagree": "strongly disagree",
    "never": "never",
    "rarely": "rarely",
    "sometimes": "sometimes",
    "often": "often",
    "always": "always",
    "very satisfied": "very satisfied",
    "satisfied": "satisfied",
    "unsatisfied": "unsatisfied",
    "very unsatisfied": "very unsatisfied",
}

# Flag de pause debug (désactiver en prod)
DEBUG_PAUSE = False


# =============================================================================
# FONCTIONS DE NORMALISATION DE TEXTE
# =============================================================================

def norm_txt(s: str) -> str:
    """
    Normalisation basique : trim + lowercase + NBSP → espace + ponctuation légère supprimée.
    Usage: comparaison souple de textes courts.
    """
    if not s:
        return ""
    t = s.strip().lower().replace("\u00a0", " ")
    for ch in ("«", "»", """, """, '"', "'", "'", "›", "•", "·", "…", ":"):
        t = t.replace(ch, " ")
    while "  " in t:
        t = t.replace("  ", " ")
    return t


def norms_txt(s: str) -> str:
    """Normalisation simple : collapse whitespace + lowercase."""
    return " ".join((s or "").strip().lower().split())


def normt_txt(s: str) -> str:
    """
    Normalisation robuste avec décomposition unicode.
    Retire accents via NFKD, remplace apostrophes typographiques.
    """
    if s is None:
        return ""
    s = s.replace("'", "'").replace("´", "'").replace("`", "'").replace("'", "'")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def norm(s: str) -> str:
    """
    Normalisation moyenne : NBSP + ponctuation typographique supprimée.
    """
    if not s:
        return ""
    s = s.replace("\u00a0", " ")
    s = s.lower().strip()
    s = re.sub(r"[»«""\"\''›→·•:]+", "", s)
    s = re.sub(r"\s+", " ", s)
    return s


def norm_text(s: str) -> str:
    """
    Normalisation complète NFKD + apostrophes + lowercase.
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = s.replace("'", "'").replace("`", "'").replace(""", '"').replace(""", '"')
    s = re.sub(r"\s+", " ", s, flags=re.S).strip().lower()
    return s


def norm_hint(s: str) -> str:
    """Alias de norm_text pour compatibilité."""
    return norm_text(s)


def norm_soft(s: str) -> str:
    """Normalisation soft : NFKC, collapse whitespace."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s).replace("\xa0", " ").strip()
    return re.sub(r"\s+", " ", s)


def norm_lc_soft(s: str) -> str:
    """Normalisation soft + lowercase."""
    return norm_soft(s).lower()


def normalize_lbl(s: str) -> str:
    """Normalisation de label : NFKD, retire accents, lowercase."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


def norm_btn_text(s: str) -> str:
    """
    Normalisation pour texte de bouton : retire flèches et décorations.
    """
    s = re.sub(r"\s+", " ", (s or "")).strip().lower()
    s = s.replace("→", " ").replace("»", " ").replace(">", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def strip_accents(s: str) -> str:
    """Retire les accents d'une chaîne via NFKD."""
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def xpath_literal(s: str) -> str:
    """
    Échappe une chaîne pour utilisation dans un XPath.
    Gère les quotes simples et doubles.
    """
    if "'" not in s:
        return f"'{s}'"
    if '"' not in s:
        return f'"{s}"'
    parts = s.split("'")
    return "concat('" + "', \"'\", '".join(parts) + "')"


# =============================================================================
# HELPERS GÉNÉRIQUES DOM
# =============================================================================

def pause_here(msg="Appuie sur Entrée pour continuer…"):
    """
    Pause interactive pour debug (seulement si DEBUG_PAUSE=True).
    """
    if not DEBUG_PAUSE:
        return
    try:
        input(f"[PAUSE] {msg}")
    except (EOFError, KeyboardInterrupt):
        pass


def scroll_into_view(driver, el):
    """Scroll l'élément au centre du viewport."""
    driver.execute_script(
        "arguments[0].scrollIntoView({block:'center', inline:'center'})", el
    )


def js_click(driver, el):
    """Scroll + click via JavaScript."""
    driver.execute_script(
        "arguments[0].scrollIntoView({block:'center',inline:'center'});", el
    )
    driver.execute_script("arguments[0].click();", el)


def safe_click(driver, el, *, trace: str = "") -> bool:
    """
    Clic robuste multi-stratégie (UN SEUL plan de clic) :
      1) JS click
      2) ActionChains click
      3) el.click() natif
    
    Observabilité:
      - stocke la dernière méthode gagnante sur driver._last_click_method
      - incrémente des compteurs sur driver._click_stats
    
    Args:
        driver: WebDriver instance
        el: WebElement à cliquer
        trace: identifiant optionnel pour les stats
    
    Returns:
        True si clic réussi, False sinon
    """
    def _bump(method: str):
        try:
            setattr(driver, "_last_click_method", method)
            stats = getattr(driver, "_click_stats", None)
            if stats is None:
                stats = {}
                setattr(driver, "_click_stats", stats)
            key = f"{trace}:{method}" if trace else method
            stats[key] = stats.get(key, 0) + 1
        except Exception:
            pass

    try:
        el.location_once_scrolled_into_view  # force scroll
    except Exception:
        pass

    # 1) JS click
    try:
        js_click(driver, el)
        _bump("js")
        return True
    except Exception:
        pass

    # 2) ActionChains
    try:
        ActionChains(driver).move_to_element(el).pause(0.05).click().perform()
        _bump("actionchains")
        return True
    except Exception:
        pass

    # 3) Click natif
    try:
        el.click()
        _bump("native")
        return True
    except Exception:
        return False


def is_checked(el) -> bool:
    """
    Vérifie si un élément (checkbox/radio) est coché.
    Fonctionne avec input[type=checkbox|radio] et role=checkbox.
    """
    t = (el.get_attribute("type") or "").lower()
    if t in ("checkbox", "radio"):
        try:
            return el.is_selected()
        except Exception:
            pass
    aria = (el.get_attribute("aria-checked") or "").lower()
    if aria in ("true", "false"):
        return aria == "true"
    # Certaines libs utilisent des classes
    cls = (el.get_attribute("class") or "").lower()
    return "checked" in cls or "is-checked" in cls


def looks_like_nav_label(s: str) -> bool:
    """
    Détecte si un texte ressemble à un label de navigation (suivant, continuer, etc.)
    """
    if not s:
        return False
    s = s.lower().strip()
    nav_kw = {
        "continuer", "suivant", "start", "commencer", "démarrer",
        "accepter", "accepter et commencer", "next", "continue",
        "submit", "soumettre", "valider",
    }
    return any(k in s for k in nav_kw)


def set_input_value_with_events(driver, el, value: str):
    """
    Définit la valeur d'un input et déclenche les events JS attendus.
    Utilise Ctrl+A + Backspace pour effacer, puis send_keys.
    """
    try:
        el.click()
    except Exception:
        pass
    try:
        el.clear()
    except Exception:
        pass
    try:
        el.send_keys(Keys.CONTROL, "a")
        el.send_keys(Keys.BACKSPACE)
        el.send_keys(value)
    except Exception:
        driver.execute_script("arguments[0].value = arguments[1];", el, value)
    # Events attendus par les frameworks JS
    driver.execute_script("""
        const e = arguments[0];
        for (const t of ["input","change","blur"]) {
          try { e.dispatchEvent(new Event(t, {bubbles:true})); } catch(_){}
        }
    """, el)


def find_inputs_by_hint(driver, kind: str):
    """
    Retourne une liste d'<input> candidates pour month/day/year.
    
    Args:
        driver: WebDriver
        kind: 'month', 'day', ou 'year'
    """
    if kind not in DATE_HINTS:
        return []
    H = DATE_HINTS[kind]
    phs = " or ".join([
        f"translate(@placeholder,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')='{p}'"
        for p in H["placeholders"]
    ])
    als = f"translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')='{kind}'"
    nm = f"contains(translate(@name,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'{kind}')"
    iid = f"contains(translate(@id,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'{kind}')"
    xp = f"//input[({phs}) or {als} or {nm} or {iid}]"
    try:
        return driver.find_elements(By.XPATH, xp)
    except Exception:
        return []


# =============================================================================
# HELPERS DE CONTEXTE (scoping questions)
# =============================================================================

def find_question_container_by_ctx(driver, context_hint: str):
    """
    Retourne le <div class="question ..."> le plus pertinent pour le contexte.
    Score les containers par nombre de tokens du ctx présents dans le h1.question-text.
    """
    if not context_hint:
        return None

    tokens = [t for t in norms_txt(context_hint).split() if len(t) >= 4][:10]
    if not tokens:
        return None

    candidates = driver.find_elements(By.CSS_SELECTOR, "div.question")
    best, best_score = None, 0
    for c in candidates:
        try:
            h1 = c.find_element(By.CSS_SELECTOR, "h1.question-text")
            q = norms_txt(h1.get_attribute("innerText") or h1.text)
        except Exception:
            continue
        score = sum(1 for t in tokens if t in q)
        if score > best_score:
            best, best_score = c, score

    return best if best_score > 0 else None


def find_questions_container(driver, context_hint: str):
    """
    Essaie de limiter la recherche au bloc <div class='question'> qui porte le H1 du contexte.
    """
    ctx = (context_hint or "").strip()
    if not ctx:
        return None
    candidates = driver.find_elements(
        By.XPATH,
        "//div[contains(@class,'question')][.//h1 or .//h2]"
    )
    norm_ctx = normt_txt(ctx)
    for q in candidates:
        try:
            title = ""
            try:
                title = q.find_element(By.XPATH, ".//h1").text
            except Exception:
                try:
                    title = q.find_element(By.XPATH, ".//h2").text
                except Exception:
                    title = ""
            if normt_txt(title).find(norm_ctx) != -1:
                return q
        except Exception:
            continue
    return None


def find_context_container(driver, context_hint: str | None):
    """
    Trouve le container de question par contexte (wrapper pour find_questions_container).
    Retourne None si pas de contexte ou pas trouvé.
    """
    if not context_hint:
        return None
    return find_questions_container(driver, context_hint)


# =============================================================================
# HELPERS POUR OPEN-ENDED (chevron/toggle)
# =============================================================================

def has_visible_open_ended_field(container):
    """Vérifie s'il y a déjà un champ 'réponse libre' visible sous cette question."""
    try:
        # 1) textarea visible
        areas = container.find_elements(By.XPATH, ".//textarea[not(@disabled) and not(@readonly)]")
        for a in areas:
            try:
                if a.is_displayed() and a.rect.get("height", 0) > 5 and a.rect.get("width", 0) > 20:
                    return True
            except Exception:
                continue
        # 2) input texte 'open-ended' (plus rare)
        inputs = container.find_elements(
            By.XPATH,
            ".//input[(@type='text' or @type='search' or not(@type)) and not(@disabled) and not(@readonly)]"
        )
        for i in inputs:
            try:
                ph = (i.get_attribute("placeholder") or "").lower()
                nm = (i.get_attribute("name") or "").lower() + " " + (i.get_attribute("id") or "").lower()
                if i.is_displayed() and (len(ph) > 0 or "open" in nm or "free" in nm or "ended" in nm):
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def ensure_open_ended_open(
    driver,
    context_hint: str = "",
    desired_state: str = "open",
    max_retries: int = 2,
    postcheck: str = "field",
    selectors_override: list[str] | None = None,
) -> bool:
    """
    Ouvre (si besoin) le champ 'réponse libre' déclenché par un petit chevron.
    - Scopé par le contexte de la question.
    - Multi-fallbacks de clic.
    - Post-vérification : apparition d'un textarea OU flip de l'icône.

    Args:
        context_hint: texte de la question pour borner la recherche.
        desired_state: pour l'instant « open » (idempotent).
        postcheck: 'field' (textarea) ou 'icon' (flip chevron).
    """
    try:
        container = find_question_container_by_ctx(driver, context_hint) or driver
    except Exception:
        container = driver

    # Si déjà ouvert et on veut "open" → rien à faire
    if desired_state == "open":
        try:
            if has_visible_open_ended_field(container):
                print("Open-ended: déjà ouvert (précheck). source: input_utils.py")
                return True
        except Exception:
            pass

    # Sélecteurs ciblant le chevron/toggle open-ended
    default_selectors = [
        ".//span[contains(@ng-click,'handleOpenEnded')]",
        ".//span[contains(@ng-click,'openEnded') or contains(@ng-click,'openended')]",
        ".//i[contains(@class,'fa-chevron-down') or contains(@class,'fa-chevron-up')]/ancestor::*[self::span or self::div or self::button][1]",
        ".//span[contains(@class,'chevron') or contains(@class,'arrow')]",
        ".//i[contains(@class,'chevron') or contains(@class,'arrow')]/parent::*",
    ]
    selectors = selectors_override or default_selectors

    # Collecte candidats
    candidates = []
    for xp in selectors:
        try:
            candidates.extend(container.find_elements(By.XPATH, xp))
        except Exception:
            continue

    # Filtre visibles et 'cliquables' (petits hitbox autorisés)
    visible = []
    for el in candidates:
        try:
            if el.is_displayed():
                box = el.rect or {}
                if box.get("width", 0) >= 6 and box.get("height", 0) >= 6:
                    visible.append(el)
        except Exception:
            continue

    # Tri léger: les éléments avec icône <i>/<svg> d'abord
    def _score(el):
        sc = 0
        try:
            if el.find_elements(By.TAG_NAME, "i") or el.find_elements(By.TAG_NAME, "svg"):
                sc += 2
        except Exception:
            pass
        try:
            r = el.rect
            sc += min(int((r.get("width", 0) * r.get("height", 0)) / 400), 3)
        except Exception:
            pass
        return sc

    visible.sort(key=_score, reverse=True)

    # Clics (multi-fallback) + post-check
    for el in visible[:8]:
        for attempt in range(max_retries):
            try:
                if safe_click(driver, el, trace="open_ended_toggle"):
                    time.sleep(0.15)
                    # Post-vérif
                    if postcheck == "field":
                        if has_visible_open_ended_field(container):
                            print("✓ Open-ended: champ affiché (postcheck=field). source: input_utils.py")
                            return True
                    else:
                        if container.find_elements(By.XPATH, ".//i[contains(@class,'fa-chevron-up')]"):
                            print("✓ Open-ended: icône passée en 'up' (postcheck=icon). source: input_utils.py")
                            return True
            except Exception:
                try:
                    ActionChains(driver).move_to_element(el).pause(0.05).click().perform()
                except Exception:
                    pass

    print("⚠️ Open-ended: impossible d'ouvrir via chevron. source: input_utils.py")
    return False


def split_typed_instruction(s: str):
    """
    Retourne (label, type or None) depuis 'libellé //// type'.
    Le séparateur est 4+ slashes.
    """
    if not s:
        return "", None
    parts = re.split(r"/{4,}", s)
    lbl = norm_soft(parts[0])
    if len(parts) == 1:
        return lbl, None
    itype = norm_soft(parts[1]).lower()
    return lbl, itype


# =============================================================================
# HELPERS VIEWPORT
# =============================================================================

def viewport_penalty(driver, el) -> float:
    """
    Pénalité de position viewport : 0.0 si visible au centre, >0 si hors écran.
    Utile pour prioriser les éléments visibles dans le viewport.
    """
    try:
        r = el.rect
        if not r:
            return 999.0
        vp_height = driver.execute_script("return window.innerHeight") or 800
        vp_width = driver.execute_script("return window.innerWidth") or 1200
        
        el_center_y = r["y"] + r["height"] / 2
        el_center_x = r["x"] + r["width"] / 2
        
        penalty = 0.0
        if el_center_y < 0 or el_center_y > vp_height:
            penalty += abs(el_center_y - vp_height / 2) / vp_height
        if el_center_x < 0 or el_center_x > vp_width:
            penalty += abs(el_center_x - vp_width / 2) / vp_width
        
        return penalty
    except Exception:
        return 999.0


def similarity(a: str, b: str) -> float:
    """
    Similarité simple entre deux chaînes (Jaccard sur mots).
    Retourne un float entre 0.0 et 1.0.
    """
    if not a or not b:
        return 0.0
    sa = set(a.lower().split())
    sb = set(b.lower().split())
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0