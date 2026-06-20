# preselection/playwright_shim.py
"""
Shim de compatibilité Playwright → Selenium (Option A de la migration).

Expose les classes PlaywrightDriverShim et PlaywrightElementShim qui imitent
l'API webdriver.Chrome / WebElement de Selenium, en déléguant à un objet
Playwright Page / ElementHandle sous-jacent.

Objectif : permettre au code existant (action_dispatcher, dom_utils, frame_utils,
survey_executor…) de fonctionner sans modification, après avoir remplacé le
lancement Selenium par launch_browser_playwright() dans playwright_launcher.py.

Limitations connues → voir PLAYWRIGHT_MIGRATION.md.

Usage :
    from preselection.playwright_shim import PlaywrightDriverShim
    # Ne pas instancier directement — utiliser launch_browser_playwright().
"""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from typing import Any, List, Optional

from selenium.webdriver.remote.webelement import WebElement

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mapping By.* → sélecteur Playwright
# ---------------------------------------------------------------------------

def _build_selector(by: str, value: str) -> str:
    """
    Convertit un localisateur Selenium (By.*, value) en sélecteur Playwright.

    Playwright reconnaît nativement :
      - CSS  : passé tel quel
      - XPath : préfixé "xpath="
      - text  : préfixé "text="
    """
    by_lower = by.lower()
    if by_lower in ("css selector", "css"):
        return value
    if by_lower == "xpath":
        return f"xpath={value}"
    if by_lower in ("tag name", "tag_name"):
        return value  # CSS tag selector
    if by_lower == "id":
        return f"#{value}"
    if by_lower == "name":
        return f"[name='{value}']"
    if by_lower == "class name":
        return f".{value}"
    if by_lower == "link text":
        return f"text={value}"
    if by_lower == "partial link text":
        return f"text={value}"
    return value


# ---------------------------------------------------------------------------
# Mapping Keys Selenium → touches Playwright
# ---------------------------------------------------------------------------

_KEYS_MAP = {
    "\ue006": "Enter",    # Keys.RETURN
    "\ue007": "Enter",    # Keys.ENTER
    "\ue004": "Tab",      # Keys.TAB
    "\ue003": "Backspace",# Keys.BACK_SPACE
    "\ue017": "Delete",   # Keys.DELETE
    "\ue00c": "Escape",   # Keys.ESCAPE
    "\ue00d": " ",        # Keys.SPACE
    "\ue013": "ArrowUp",
    "\ue015": "ArrowDown",
    "\ue011": "ArrowLeft",
    "\ue012": "ArrowRight",
    "\ue009": "Control",
    "\ue008": "Shift",
    "\ue00a": "Alt",
}


def _split_keys(value: str) -> List[str]:
    """
    Découpe une chaîne send_keys en tokens (texte ordinaire + touches spéciales).
    Retourne une liste de strings à taper ou de noms de touche à presser.
    """
    tokens = []
    buf = ""
    for ch in value:
        if ch in _KEYS_MAP:
            if buf:
                tokens.append(("type", buf))
                buf = ""
            tokens.append(("press", _KEYS_MAP[ch]))
        else:
            buf += ch
    if buf:
        tokens.append(("type", buf))
    return tokens


# ---------------------------------------------------------------------------
# Shim élément (WebElement)
# ---------------------------------------------------------------------------

class PlaywrightElementShim(WebElement):
    """
    Imite selenium.webdriver.remote.webelement.WebElement.
    Délègue à un Playwright ElementHandle (_h).

    Hérite de WebElement pour que isinstance(el, WebElement) soit True
    (ActionChains.move_to_element, Select, expected_conditions…).
    super().__init__() n'est PAS appelé car il requiert parent/id_.
    """

    def __init__(self, handle, frame=None):
        # handle : playwright ElementHandle
        # frame  : playwright Frame ou Page dont provient cet élément
        self._h = handle
        self._frame = frame
        # Attributs minimaux attendus par certains chemins Selenium internes
        self._w3c = True
        self._parent = None
        self._id = None

    # ── Propriétés lues ──────────────────────────────────────────────────────

    @property
    def tag_name(self) -> str:
        try:
            return self._h.evaluate("el => el.tagName.toLowerCase()")
        except Exception:
            return ""

    @property
    def text(self) -> str:
        try:
            return self._h.inner_text()
        except Exception:
            return ""

    @property
    def location(self) -> dict:
        try:
            bb = self._h.bounding_box()
            return {"x": bb["x"], "y": bb["y"]} if bb else {"x": 0, "y": 0}
        except Exception:
            return {"x": 0, "y": 0}

    @property
    def size(self) -> dict:
        try:
            bb = self._h.bounding_box()
            return {"width": bb["width"], "height": bb["height"]} if bb else {"width": 0, "height": 0}
        except Exception:
            return {"width": 0, "height": 0}

    # ── Attributs / propriétés DOM ───────────────────────────────────────────

    def get_attribute(self, name: str) -> Optional[str]:
        try:
            return self._h.get_attribute(name)
        except Exception:
            return None

    def get_property(self, name: str) -> Any:
        try:
            return self._h.evaluate(f"el => el['{name}']")
        except Exception:
            return None

    def value_of_css_property(self, name: str) -> str:
        try:
            return self._h.evaluate(
                f"el => window.getComputedStyle(el).getPropertyValue('{name}')"
            )
        except Exception:
            return ""

    # ── État ─────────────────────────────────────────────────────────────────

    def is_displayed(self) -> bool:
        try:
            return self._h.is_visible()
        except Exception:
            return False

    def is_enabled(self) -> bool:
        try:
            return self._h.is_enabled()
        except Exception:
            return False

    def is_selected(self) -> bool:
        try:
            return self._h.is_checked()
        except Exception:
            return False

    # ── Actions ──────────────────────────────────────────────────────────────

    def click(self) -> None:
        self._h.click()

    def send_keys(self, *value) -> None:
        """
        Tape du texte et/ou presse des touches spéciales.
        Compatible avec les combinaisons Keys.RETURN, Keys.TAB, etc.
        """
        text = "".join(str(v) for v in value)
        for kind, val in _split_keys(text):
            if kind == "type":
                self._h.type(val)
            else:
                self._h.press(val)

    def clear(self) -> None:
        self._h.fill("")

    def submit(self) -> None:
        self._h.evaluate(
            "el => { const f = el.closest('form'); if (f) f.submit(); }"
        )

    # ── Recherche DOM enfant ─────────────────────────────────────────────────

    def find_element(self, by: str, value: str) -> "PlaywrightElementShim":
        from selenium.common.exceptions import NoSuchElementException
        sel = _build_selector(by, value)
        child = self._h.query_selector(sel)
        if child is None:
            raise NoSuchElementException(
                f"PlaywrightShim: aucun élément ({by}={value!r})"
            )
        return PlaywrightElementShim(child, self._frame)

    def find_elements(self, by: str, value: str) -> List["PlaywrightElementShim"]:
        sel = _build_selector(by, value)
        try:
            children = self._h.query_selector_all(sel)
        except Exception:
            return []
        return [PlaywrightElementShim(c, self._frame) for c in children]

    # ── Accès au handle natif (pour les appels execute_script(script, el)) ───

    @property
    def _playwright_handle(self):
        return self._h


# ---------------------------------------------------------------------------
# Shim switch_to (gestion des iframes)
# ---------------------------------------------------------------------------

class _SwitchToShim:
    """
    Imite driver.switch_to de Selenium.
    Maintient un pointeur _current_frame sur le driver shim parent.
    """

    def __init__(self, driver_shim: "PlaywrightDriverShim"):
        self._d = driver_shim

    def default_content(self) -> None:
        """Retourne au contexte principal (page racine)."""
        self._d._current_frame = self._d._page

    def frame(self, frame_reference) -> None:
        """
        Bascule dans une iframe.
        Accepte :
          - PlaywrightElementShim (élément <iframe>)
          - int (index dans la liste des frames)
          - str (name/id — non implémenté, lève NotImplementedError)
        """
        if isinstance(frame_reference, PlaywrightElementShim):
            # Récupérer l'objet Frame Playwright depuis le handle de l'élément
            pw_frame = frame_reference._h.content_frame()
            if pw_frame is not None:
                self._d._current_frame = pw_frame
            else:
                log.warning("[SHIM] switch_to.frame : content_frame() = None")
        elif isinstance(frame_reference, int):
            frames = self._d._page.frames
            # frames[0] = main frame, sous-frames à partir de [1]
            child_frames = [f for f in frames if f != self._d._page.main_frame]
            if frame_reference < len(child_frames):
                self._d._current_frame = child_frames[frame_reference]
            else:
                log.warning(
                    "[SHIM] switch_to.frame(%d) : index hors limites (%d frames)",
                    frame_reference, len(child_frames),
                )
        else:
            raise NotImplementedError(
                f"PlaywrightShim: switch_to.frame({type(frame_reference)}) non supporté"
            )

    def parent_frame(self) -> None:
        """Remonte d'un niveau d'iframe."""
        parent = getattr(self._d._current_frame, "parent_frame", None)
        if callable(parent):
            pf = parent()
            self._d._current_frame = pf if pf is not None else self._d._page
        else:
            self._d._current_frame = self._d._page

    def window(self, handle: str) -> None:
        """Bascule vers un onglet par handle (index string)."""
        self._d.switch_to_window(handle)

# ---------------------------------------------------------------------------
# Shim ActionChains
# ---------------------------------------------------------------------------

class ActionChains:
    """
    Imite selenium.webdriver.common.action_chains.ActionChains.
    Implémente les actions chaînées les plus courantes.
    """

    def __init__(self, driver: "PlaywrightDriverShim"):
        self._d = driver
        self._actions: List[tuple] = []

    def move_to_element(self, element: PlaywrightElementShim) -> "ActionChains":
        self._actions.append(("hover", element))
        return self

    def click(self, element: Optional[PlaywrightElementShim] = None) -> "ActionChains":
        self._actions.append(("click", element))
        return self

    def double_click(self, element: Optional[PlaywrightElementShim] = None) -> "ActionChains":
        self._actions.append(("dblclick", element))
        return self

    def context_click(self, element: Optional[PlaywrightElementShim] = None) -> "ActionChains":
        self._actions.append(("right_click", element))
        return self

    def send_keys(self, *keys) -> "ActionChains":
        self._actions.append(("keys", keys))
        return self

    def send_keys_to_element(self, element: PlaywrightElementShim, *keys) -> "ActionChains":
        self._actions.append(("keys_to", element, keys))
        return self

    def drag_and_drop(self, source: PlaywrightElementShim, target: PlaywrightElementShim) -> "ActionChains":
        self._actions.append(("drag", source, target))
        return self

    def drag_and_drop_by_offset(self, source: PlaywrightElementShim, xoffset: int, yoffset: int) -> "ActionChains":
        self._actions.append(("drag_offset", source, xoffset, yoffset))
        return self

    def perform(self) -> None:
        """Exécute les actions accumulées dans l'ordre."""
        page = self._d._page
        for action in self._actions:
            kind = action[0]
            try:
                if kind == "hover":
                    try:
                        action[1]._h.scroll_into_view_if_needed(timeout=3000)
                    except Exception:
                        pass
                elif kind == "click":
                    if action[1]:
                        action[1]._h.click()
                    else:
                        page.mouse.click(0, 0)  # position courante
                elif kind == "dblclick":
                    if action[1]:
                        action[1]._h.dblclick()
                elif kind == "right_click":
                    if action[1]:
                        action[1]._h.click(button="right")
                elif kind == "keys":
                    for k in action[1]:
                        text = str(k)
                        for t, v in _split_keys(text):
                            if t == "type":
                                page.keyboard.type(v)
                            else:
                                page.keyboard.press(v)
                elif kind == "keys_to":
                    action[1].send_keys(*action[2])
                elif kind == "drag":
                    source_bb = action[1]._h.bounding_box()
                    target_bb = action[2]._h.bounding_box()
                    if source_bb and target_bb:
                        page.mouse.move(
                            source_bb["x"] + source_bb["width"] / 2,
                            source_bb["y"] + source_bb["height"] / 2,
                        )
                        page.mouse.down()
                        page.mouse.move(
                            target_bb["x"] + target_bb["width"] / 2,
                            target_bb["y"] + target_bb["height"] / 2,
                        )
                        page.mouse.up()
                elif kind == "drag_offset":
                    source_bb = action[1]._h.bounding_box()
                    if source_bb:
                        sx = source_bb["x"] + source_bb["width"] / 2
                        sy = source_bb["y"] + source_bb["height"] / 2
                        page.mouse.move(sx, sy)
                        page.mouse.down()
                        page.mouse.move(sx + action[2], sy + action[3])
                        page.mouse.up()
            except Exception as e:
                log.warning("[SHIM][ActionChains] action=%s erreur=%s", kind, e)
        self._actions.clear()

    def reset_actions(self) -> None:
        self._actions.clear()


# ---------------------------------------------------------------------------
# Shim Select
# ---------------------------------------------------------------------------

class Select:
    """
    Imite selenium.webdriver.support.select.Select.
    Délègue à Playwright ElementHandle.select_option().
    """

    def __init__(self, element: PlaywrightElementShim):
        self._el = element

    def select_by_value(self, value: str) -> None:
        self._el._h.select_option(value=value)

    def select_by_index(self, index: int) -> None:
        self._el._h.select_option(index=index)

    def select_by_visible_text(self, text: str) -> None:
        self._el._h.select_option(label=text)

    def deselect_all(self) -> None:
        self._el._h.evaluate("el => { for (const o of el.options) o.selected = false; }")

    @property
    def options(self) -> List[PlaywrightElementShim]:
        return self._el.find_elements("tag name", "option")

    @property
    def all_selected_options(self) -> List[PlaywrightElementShim]:
        return self._el.find_elements("css selector", "option:checked")

    @property
    def first_selected_option(self) -> PlaywrightElementShim:
        opts = self.all_selected_options
        if not opts:
            from selenium.common.exceptions import NoSuchElementException
            raise NoSuchElementException("Aucune option sélectionnée")
        return opts[0]


# ---------------------------------------------------------------------------
# Shim WebDriverWait / expected_conditions
# ---------------------------------------------------------------------------

class WebDriverWait:
    """
    Imite selenium.webdriver.support.wait.WebDriverWait.
    Délègue à page.wait_for_selector() ou à une condition callable.
    """

    def __init__(self, driver: "PlaywrightDriverShim", timeout: float, poll_frequency: float = 0.5):
        self._d = driver
        self._timeout_ms = int(timeout * 1000)
        self._poll = poll_frequency

    def until(self, condition, message: str = "") -> Any:
        """
        Attend que condition(driver) retourne une valeur truthy.
        Si condition est un tuple (EC style), convertit en wait_for_selector.
        """
        import time
        deadline = time.time() + self._timeout_ms / 1000
        last_exc = None
        while time.time() < deadline:
            try:
                result = condition(self._d)
                if result:
                    return result
            except Exception as e:
                last_exc = e
            time.sleep(self._poll)
        from selenium.common.exceptions import TimeoutException
        raise TimeoutException(message or f"Condition non satisfaite après {self._timeout_ms}ms")

    def until_not(self, condition, message: str = "") -> Any:
        import time
        deadline = time.time() + self._timeout_ms / 1000
        while time.time() < deadline:
            try:
                result = condition(self._d)
                if not result:
                    return result
            except Exception:
                return True
            time.sleep(self._poll)
        from selenium.common.exceptions import TimeoutException
        raise TimeoutException(message)


# ---------------------------------------------------------------------------
# Shim driver principal (WebDriver)
# ---------------------------------------------------------------------------

class PlaywrightDriverShim:
    """
    Imite selenium.webdriver.Chrome (webdriver.Remote).

    Encapsule un browser + context + page Playwright et expose l'API Selenium
    attendue par le reste du code (action_dispatcher, survey_executor, etc.).

    Ne pas instancier directement — utiliser launch_browser_playwright()
    dans playwright_launcher.py.
    """

    def __init__(self, browser, context, page):
        self._browser = browser
        self._context = context
        self._page    = page
        # _current_frame : Page ou Frame selon le contexte switch_to.frame()
        self._current_frame = page
        self.switch_to = _SwitchToShim(self)

        # Attributs custom attachés par launch_browser() original
        # (utilisés par main.py pour cleanup)
        self._chrome_proc          = None
        self._chrome_user_data_dir = None
        self._proxy_relay_proc     = None
        self._pw                   = None

    # ── Navigation ───────────────────────────────────────────────────────────

    def bring_to_front(self) -> None:
        """Donne le focus OS à l'onglet Playwright actif (évite le throttle Chromium)."""
        self._page.bring_to_front()

    def get(self, url: str) -> None:
        """Navigue vers url. Réinitialise le frame courant au contexte principal."""
        self._current_frame = self._page
        self._page.goto(url, wait_until="domcontentloaded")

    def back(self) -> None:
        self._page.go_back()

    def forward(self) -> None:
        self._page.go_forward()

    def refresh(self) -> None:
        self._page.reload()

    # ── Propriétés de la page ────────────────────────────────────────────────

    @property
    def current_url(self) -> str:
        return self._page.url

    @property
    def title(self) -> str:
        return self._page.title()

    @property
    def page_source(self) -> str:
        return self._page.content()

    # ── Recherche DOM ─────────────────────────────────────────────────────────

    def find_element(self, by: str, value: str) -> PlaywrightElementShim:
        from selenium.common.exceptions import NoSuchElementException
        sel = _build_selector(by, value)
        el = self._current_frame.query_selector(sel)
        if el is None:
            raise NoSuchElementException(
                f"PlaywrightShim: aucun élément ({by}={value!r})"
            )
        return PlaywrightElementShim(el, self._current_frame)

    def find_elements(self, by: str, value: str) -> List[PlaywrightElementShim]:
        sel = _build_selector(by, value)
        try:
            els = self._current_frame.query_selector_all(sel)
        except Exception:
            return []
        return [PlaywrightElementShim(e, self._current_frame) for e in els]

    # ── Exécution JavaScript ──────────────────────────────────────────────────

    @staticmethod
    def _convert_arg(a: Any) -> Any:
        """Convertit récursivement PlaywrightElementShim → _playwright_handle dans les listes."""
        if isinstance(a, PlaywrightElementShim):
            return a._playwright_handle
        if isinstance(a, list):
            return [PlaywrightDriverShim._convert_arg(item) for item in a]
        return a

    def execute_script(self, script: str, *args) -> Any:
        """
        Exécute du JavaScript synchrone.

        Imite Selenium : les arguments sont accessibles via arguments[0], arguments[1]…
        Les PlaywrightElementShim sont automatiquement déballés en ElementHandle,
        y compris lorsqu'ils sont passés dans une liste.
        """
        pw_args = [self._convert_arg(a) for a in args]
        if not pw_args:
            return self._page.evaluate(f"() => {{ {script} }}")
        # Enveloppe dans une IIFE pour que arguments[N] soit disponible
        fn = f"(args) => (function() {{ {script} }}).apply(null, args)"
        return self._page.evaluate(fn, pw_args)

    def execute_async_script(self, script: str, *args) -> Any:
        """
        Exécute du JavaScript asynchrone (callback-style Selenium).
        Playwright n'a pas d'équivalent direct — on évalue via evaluate_handle.
        """
        pw_args = [
            a._playwright_handle if isinstance(a, PlaywrightElementShim) else a
            for a in args
        ]
        # Playwright attend une Promise ; on wrap le script callback en Promise
        wrapped = f"""
        (args) => new Promise((resolve) => {{
            const allArgs = [...args, resolve];
            (function() {{ {script} }}).apply(null, allArgs);
        }})
        """
        return self._page.evaluate(wrapped, pw_args)

    # ── CDP (limité) ─────────────────────────────────────────────────────────

    def execute_cdp_cmd(self, cmd: str, params: dict) -> Any:
        """
        Exécute une commande CDP via une session Playwright CDPSession.

        Note : dans l'architecture Playwright (mode pipe), le CDP reste
        accessible mais le port remote-debugging n'est plus ouvert.
        """
        try:
            cdp_session = self._page.context.new_cdp_session(self._page)
            result = cdp_session.send(cmd, params)
            cdp_session.detach()
            return result
        except Exception as e:
            log.warning("[SHIM][CDP] %s échoué : %s", cmd, e)
            return None

    # ── Captures ─────────────────────────────────────────────────────────────

    def get_screenshot_as_png(self) -> bytes:
        return self._page.screenshot()

    def save_screenshot(self, path: str) -> bool:
        try:
            self._page.screenshot(path=path)
            return True
        except Exception:
            return False

    # ── Timeouts ─────────────────────────────────────────────────────────────

    def implicitly_wait(self, seconds: float) -> None:
        """
        Pas d'équivalent direct Playwright.
        On fixe le timeout de recherche par défaut (heuristique).
        """
        self._page.set_default_timeout(seconds * 1000)

    def set_page_load_timeout(self, seconds: float) -> None:
        self._page.set_default_navigation_timeout(seconds * 1000)

    # ── Fenêtres / onglets ───────────────────────────────────────────────────

    @property
    def window_handles(self) -> List[str]:
        return [str(i) for i in range(len(self._context.pages))]

    @property
    def current_window_handle(self) -> str:
        for i, p in enumerate(self._context.pages):
            if p == self._page:
                return str(i)
        return "0"

    def switch_to_window(self, handle: str) -> None:
        idx = int(handle)
        pages = self._context.pages
        if idx < len(pages):
            self._page = pages[idx]
            self._current_frame = self._page
            self.switch_to = _SwitchToShim(self)

    # ── Cookies ──────────────────────────────────────────────────────────────

    def get_cookies(self) -> List[dict]:
        return self._context.cookies()

    def add_cookie(self, cookie_dict: dict) -> None:
        self._context.add_cookies([cookie_dict])

    def delete_all_cookies(self) -> None:
        self._context.clear_cookies()

    # ── Fermeture ────────────────────────────────────────────────────────────

    def close(self) -> None:
        """Ferme l'onglet courant."""
        try:
            self._page.close()
        except Exception:
            pass

    def quit(self) -> None:
        """Ferme tout le navigateur (browser + contexte)."""
        try:
            self._browser.close()
        except Exception:
            pass
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None


# ---------------------------------------------------------------------------
# Monkey-patches Selenium : ActionChains et Select → shim quand Playwright
# ---------------------------------------------------------------------------
# Cibler __new__ sur l'objet classe garantit que le patch est actif même
# quand le code appelant a déjà exécuté
#   "from selenium.webdriver.common.action_chains import ActionChains"
# car c'est le même objet classe qui est référencé dans les deux modules.
# Quand __new__ retourne un objet dont le type n'est pas une sous-classe
# de cls, Python n'appelle pas __init__ automatiquement — on doit donc
# l'appeler explicitement dans le patch.

try:
    import selenium.webdriver.common.action_chains as _ac_mod
    _SeleniumActionChains = _ac_mod.ActionChains

    def _patched_ac_new(cls, driver, *args, **kwargs):
        if isinstance(driver, PlaywrightDriverShim):
            inst = object.__new__(ActionChains)
            ActionChains.__init__(inst, driver)
            return inst
        return object.__new__(cls)

    _SeleniumActionChains.__new__ = _patched_ac_new
except Exception:
    pass

try:
    import selenium.webdriver.support.select as _select_mod
    _SeleniumSelect = _select_mod.Select

    def _patched_select_new(cls, element, *args, **kwargs):
        if isinstance(element, PlaywrightElementShim):
            inst = object.__new__(Select)
            Select.__init__(inst, element)
            return inst
        return object.__new__(cls)

    _SeleniumSelect.__new__ = _patched_select_new
except Exception:
    pass
