from __future__ import annotations

"""Shim DOM statique (lxml) exposant une surface Playwright-compatible minimale
pour rejouer dom_analyzer.analyze_dom() / question_block_validator.py /
action_validator.py sur le HTML figé d'un failure_case, sans navigateur réel.

Portée strictement lecture seule : aucune méthode d'interaction (click/fill/type)
n'est exposée — ce shim ne couvre que l'extraction/l'analyse, jamais le dispatch,
et n'écrit jamais dans le DOM qu'il expose.

evaluate() n'exécute PAS de JavaScript : pas de moteur JS embarqué, et surtout pas
de moteur de layout — getComputedStyle()/getBoundingClientRect() nécessitent un
rendu réel (cascade CSS, boîtes, polices), catégoriquement hors de portée d'un
parseur HTML statique, quel qu'il soit. evaluate() lève JsEvaluationUnavailable
pour toute expression JS, SAUF un petit nombre d'idiomes strictement structurels
— reconnus par correspondance exacte/regex sur le texte source, jamais interprétés
au sens général — observés tels quels dans dom_analyzer.py et calculables sans
layout ni exécution JS :
  - "e => e.tagName.toLowerCase()" / "(e) => e.tagName.toLowerCase()" : lecture
    de la balise, triviale et sans ambiguïté.
  - "(el) => el.closest('SELECTEUR')" et sa variante "... !== null" : recherche
    d'ancêtre par sélecteur CSS, purement structurelle (même moteur cssselect que
    query_selector_all, pas de layout requis).
Tout le reste (getComputedStyle, getBoundingClientRect, JS multi-instructions,
signaux de widgets spécifiques utilisés par les validators) lève proprement. Le
code appelant de dom_analyzer.py / dom_frame_selector.py / question_block_validator.py
/ action_validator.py est déjà défensif (try/except) sur CES appels précis —
comportement existant, non modifié ici — donc analyze_dom() dégrade vers ses
chemins de repli déjà prévus au lieu de planter. Choix délibéré : plutôt que de
deviner un résultat, ce shim décline honnêtement et laisse le code existant
absorber le manque, comme il le fait déjà en production face à une évaluation JS
qui échoue (erreur réseau, contexte de sécurité, etc.) — cf. le module docstring
de Survey/failure_replay.py pour comment cette limite est ensuite disclosée.

query_selector_all()/query_selector() supportent le CSS standard (via
lxml.cssselect) et la syntaxe Playwright "xpath=<expr>" (via lxml.xpath, moteur
XPath 1.0 complet — toutes les expressions "xpath=" observées dans dom_analyzer.py
sont soit relatives (axe ancestor::, correctement résolu par rapport à l'élément
appelant) soit absolues (//..., résolues depuis la racine du document quel que
soit l'appelant) : les deux formes sont nativement supportées par lxml sans
traitement spécial.

get_attribute()/text_content() sont des lectures directes de l'arbre lxml
(fidèles à 100%). inner_text()/is_visible() sont des APPROXIMATIONS statiques
(present d'un attribut "hidden", d'un style inline display:none/visibility:hidden)
— elles ne peuvent PAS détecter une visibilité pilotée par une règle CSS externe
(feuille de style, media query) ni par une classe dont l'effet dépend du CSSOM :
c'est une limite disclosée, pas une tentative de rendu fidèle.
"""

import itertools
import re
from typing import Any, Optional

import lxml.html as _lxml_html
from lxml.html import HtmlElement

from Survey.log_utils import log_debug

_TAG = "[REPLAY_SHIM]"


class JsEvaluationUnavailable(Exception):
    """evaluate() n'a pas pu être honoré statiquement (pas de moteur JS/layout)."""


class ReplayLoadError(Exception):
    """Le HTML source n'a pas pu être chargé/parsé pour le replay."""


_TAGNAME_IDIOM_RE = re.compile(r"^\(?\s*e\s*\)?\s*=>\s*e\.tagName\.toLowerCase\(\)\s*$")
_CLOSEST_IDIOM_RE = re.compile(
    r"^\(el\)\s*=>\s*el\.closest\(\s*(['\"])(?P<selector>.*?)\1\s*\)\s*(?P<null_check>!==\s*null)?\s*$"
)

# Compteurs exposés au module d'orchestration (failure_replay.py) pour disclosure
# transparente : combien d'appels evaluate() ont pu être honorés statiquement vs
# combien ont dû décliner. Réinitialisés à chaque _StaticPage(...) construit.


class _EvalStats:
    def __init__(self) -> None:
        self.handled = 0
        self.declined = 0

    def as_dict(self) -> dict:
        return {"evaluate_handled": self.handled, "evaluate_declined": self.declined}


def _root_of(el: HtmlElement) -> HtmlElement:
    return el.getroottree().getroot()


def _css_matches_ids(root: HtmlElement, selector: str) -> set:
    try:
        return {id(x) for x in root.cssselect(selector)}
    except Exception:
        return set()


def _closest(el: HtmlElement, selector: str) -> Optional[HtmlElement]:
    matched = _css_matches_ids(_root_of(el), selector)
    for candidate in itertools.chain([el], el.iterancestors()):
        if id(candidate) in matched:
            return candidate
    return None


def _select(context_el: HtmlElement, selector: str) -> list:
    """query_selector_all générique : CSS standard ou "xpath=<expr>" Playwright."""
    if selector.startswith("xpath="):
        xp = selector[len("xpath="):]
        try:
            results = context_el.xpath(xp)
        except Exception:
            return []
        return [r for r in results if isinstance(r, HtmlElement)]

    try:
        matches = context_el.cssselect(selector)
    except Exception:
        return []
    # querySelectorAll ne renvoie jamais l'élément appelant lui-même, même s'il
    # matche le sélecteur (contrairement à cssselect(), qui inclut self) —
    # cf. vérification empirique en amont de ce module.
    return [m for m in matches if m is not context_el]


_HIDDEN_STYLE_RE = re.compile(r"(?i)(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)\s*(?:;|$)")


def _is_statically_hidden(el: HtmlElement) -> bool:
    if el.get("hidden") is not None:
        return True
    style = el.get("style") or ""
    return bool(_HIDDEN_STYLE_RE.search(style))


def _is_visible_static(el: HtmlElement) -> bool:
    """Approximation statique : hidden/display:none/visibility:hidden, self ou ancêtre.

    Ne peut pas voir une règle CSS externe (feuille de style, media query) —
    limite disclosée dans le docstring du module.
    """
    for node in itertools.chain([el], el.iterancestors()):
        if _is_statically_hidden(node):
            return False
    return True


_TEXT_SKIP_TAGS = {"script", "style", "noscript", "template"}


def _inner_text_static(el: HtmlElement) -> str:
    """Approximation statique de innerText : exclut script/style et les sous-arbres
    marqués hidden/display:none/visibility:hidden (cf. _is_statically_hidden).
    Ne reproduit pas l'algorithme de collapse d'espaces d'un vrai moteur de rendu —
    normalisation simple (espaces multiples -> un seul), cohérente avec les
    helpers _norm() déjà utilisés partout ailleurs dans ce codebase.
    """
    if el.tag in _TEXT_SKIP_TAGS or _is_statically_hidden(el):
        return ""

    parts: list[str] = []
    if el.text:
        parts.append(el.text)
    for child in el:
        if isinstance(child.tag, str) and child.tag not in _TEXT_SKIP_TAGS and not _is_statically_hidden(child):
            parts.append(_inner_text_static(child))
        if child.tail:
            parts.append(child.tail)
    return " ".join(" ".join(parts).split())


def _try_static_js_idiom(js: str, arg: Any) -> "tuple[bool, Any]":
    """Tente de répondre à un idiome JS structurel reconnu. Retourne (handled, value)."""
    source = (js or "").strip()

    if _TAGNAME_IDIOM_RE.match(source):
        raw = arg.raw if isinstance(arg, StaticElementHandle) else arg
        if isinstance(raw, HtmlElement):
            return True, raw.tag.lower()
        return True, ""

    m = _CLOSEST_IDIOM_RE.match(source)
    if m:
        raw = arg.raw if isinstance(arg, StaticElementHandle) else arg
        if not isinstance(raw, HtmlElement):
            return True, None
        found = _closest(raw, m.group("selector"))
        if m.group("null_check"):
            return True, found is not None
        return True, (StaticElementHandle(found, stats=getattr(arg, "_stats", None)) if found is not None else None)

    return False, None


class StaticElementHandle:
    """Équivalent statique d'un Playwright ElementHandle, lecture seule."""

    __slots__ = ("raw", "_stats")

    def __init__(self, raw: HtmlElement, *, stats: Optional[_EvalStats] = None) -> None:
        self.raw = raw
        self._stats = stats

    def get_attribute(self, name: str) -> Optional[str]:
        return self.raw.get(name)

    def text_content(self) -> str:
        return self.raw.text_content()

    def inner_text(self) -> str:
        return _inner_text_static(self.raw)

    def is_visible(self) -> bool:
        return _is_visible_static(self.raw)

    def query_selector_all(self, selector: str) -> list:
        return [StaticElementHandle(e, stats=self._stats) for e in _select(self.raw, selector)]

    def query_selector(self, selector: str) -> Optional["StaticElementHandle"]:
        matches = _select(self.raw, selector)
        return StaticElementHandle(matches[0], stats=self._stats) if matches else None

    def evaluate(self, js: str, arg: Any = None) -> Any:
        handled, value = _try_static_js_idiom(js, arg if arg is not None else self)
        if self._stats is not None:
            if handled:
                self._stats.handled += 1
            else:
                self._stats.declined += 1
        if handled:
            return value
        log_debug(_TAG, f"evaluate declined (element-scope): {js[:120]!r}")
        raise JsEvaluationUnavailable(f"static replay cannot execute: {js[:200]!r}")

    def __eq__(self, other: object) -> bool:
        return isinstance(other, StaticElementHandle) and other.raw is self.raw

    def __hash__(self) -> int:
        return id(self.raw)


class StaticPage:
    """Équivalent statique d'une Playwright Page, lecture seule, sans frames.

    Un failure_case ne fige qu'un seul document déjà résolu (page_snapshot.py
    capture dom_outer.html/post_action_dom.html APRÈS sélection de la meilleure
    chaîne de frames — cf. Survey/page_snapshot.py::dump_page_snapshot) : ce
    document EST déjà le bon contexte, il n'y a pas de frameset live à reparcourir.
    child_frames renvoie donc toujours [] — ce qui fait converger
    dom_frame_selector._select_best_frame_chain() sur la chaîne racine [] sans
    tenter de replier une hiérarchie de frames qui n'existe plus statiquement.
    """

    def __init__(self, html_text: str) -> None:
        try:
            self._root: HtmlElement = _lxml_html.fromstring(html_text)
        except Exception as exc:
            raise ReplayLoadError(f"HTML illisible pour le replay : {exc}") from exc
        self.stats = _EvalStats()
        self.child_frames: list = []
        self.main_frame = self
        self.url = ""

    def query_selector_all(self, selector: str) -> list:
        return [StaticElementHandle(e, stats=self.stats) for e in _select(self._root, selector)]

    def query_selector(self, selector: str) -> Optional[StaticElementHandle]:
        matches = _select(self._root, selector)
        return StaticElementHandle(matches[0], stats=self.stats) if matches else None

    def content(self) -> str:
        return _lxml_html.tostring(self._root, encoding="unicode")

    def evaluate(self, js: str, arg: Any = None) -> Any:
        handled, value = _try_static_js_idiom(js, arg)
        if handled:
            self.stats.handled += 1
            return value
        self.stats.declined += 1
        log_debug(_TAG, f"evaluate declined (page-scope): {js[:120]!r}")
        raise JsEvaluationUnavailable(f"static replay cannot execute: {js[:200]!r}")


def load_static_driver(html_text: str) -> StaticPage:
    """Construit le shim driver à partir du HTML figé d'un failure_case."""
    return StaticPage(html_text)
