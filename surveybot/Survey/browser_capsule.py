from __future__ import annotations

"""Capture additive "Browser Capsule" (SURVEYBOT_AUTOFIX_PLAN.md, Phase 3B) : faits
d'état runtime, trace d'action structurée et mutations DOM bornées, en complément
de la capture existante de Survey/page_snapshot.py (outerHTML, pré/post-action,
question_blocks, screenshot, MHTML best-effort) — jamais à sa place.

Portée strictement additive et passive :
- Nouvelles fonctions uniquement, appelées depuis le hook d'action déjà installé
  dans Survey/page_snapshot.py (_install_action_observer), sans modifier son
  comportement existant ni le corps des fonctions de capture qu'il appelle déjà
  (dump_page_snapshot, _capture_outer_html_cssom_safe, _capture_current_outer_html).
- Aucune interaction : ni clic, ni saisie, ni navigation. Le MutationObserver
  installé ici n'observe que les mutations déjà produites par le flux normal
  existant (l'exécution réelle du plan d'actions par le dispatcher, non modifié)
  — il n'en produit jamais lui-même.
- Éléments concernés strictement scopés au cas courant : la cible de chaque
  action demandée (résolue via le registry DOM_REGISTRY déjà rempli par
  l'extraction, get_target), ses options connues (option_xpath_map), et jusqu'à
  deux niveaux d'ancêtres directs via l'axe XPath générique ancestor:: (déjà
  utilisé ailleurs dans dom_analyzer.py, pas une nouvelle heuristique) — jamais
  l'ensemble du document.
- Hors périmètre (différé, cf. Phase 3B.3/3B.4 du plan) : hiérarchie complète
  des frames, Shadow DOM. Le contexte de frame utilisé ici est celui déjà
  résolu par le registry pour la cible (payload["frame_chain"]) — simplification
  bornée assumée : si plusieurs cibles d'un même plan portent des frame_chain
  différents, seule celle de la première action résolue est utilisée.
- CTA : aucun champ générique et déjà vérifié n'identifie une cible de CTA dans
  le registry actuel (vérifié avant d'écrire ce module — pas de clé "cta_xpath"/
  "next_button_xpath" ou équivalente dans dom_analyzer.py) — non capturé plutôt
  que deviné par une traversée DOM provider-spécifique. "Éléments déjà lus par
  le validator concerné" sont couverts par réutilisation directe des champs déjà
  présents dans le rapport de validation (dom_signal/observed d'un issue), pas
  par une nouvelle traversée.
- reason du dispatcher : action_dispatcher.py ne retourne qu'un booléen
  (dispatcher_success) — la raison textuelle n'existe que dans des logs non
  structurés (log_debug/log_info, conditionnés par LOG_LEVEL). Aucune tentative
  de parser ces logs ici (fragile, hors périmètre) : action_trace.json ne porte
  donc que ce que le validator a déjà calculé de façon structurée
  (dispatcher_success, et dom_signal/observed quand le validator en a produit),
  jamais une raison reconstruite.
- Secrets : aucun cookie/storage/token/header conservé. Tout champ "href"
  observé est capturé tel quel ici (même principe que le reste de la capture
  passive, qui écrit brut dans snapshots/) — le retrait de query string/fragment
  est appliqué au moment de la copie sanitisée vers failure_cases/
  (Survey/failure_case_builder.py), pas ici, pour rester cohérent avec le
  découpage déjà en place (capture brute vs sanitisation à la copie).
"""

from typing import Any, Optional

from Survey.dom_registry import get_target
from Survey.log_utils import log_debug

_TAG = "[BROWSER_CAPSULE]"

# Budgets bornés — jamais de capture non bornée (règle stricte du chantier).
_MAX_ELEMENTS = 40
_MAX_OPTIONS_PER_TARGET = 20
_MAX_MUTATIONS = 50

_MUTATION_OBSERVER_KEY = "__sb_capsule_observer__"
_MUTATION_BUFFER_KEY = "__sb_capsule_mutations__"
_MUTATION_TRUNCATED_KEY = "__sb_capsule_truncated__"


def _first_frame_chain(actions: Any) -> list:
    for action in actions or []:
        if not isinstance(action, dict):
            continue
        target_id = str(action.get("target_id") or "").strip()
        if not target_id:
            continue
        payload = get_target(target_id)
        if isinstance(payload, dict):
            chain = payload.get("frame_chain")
            if isinstance(chain, list):
                return chain
    return []


def _resolve_pertinent_xpaths(actions: Any) -> "dict[str, str]":
    """{label lisible: xpath} pour la cible de chaque action + ses options
    connues + jusqu'à deux niveaux d'ancêtres directs. Budget borné."""
    xpaths: "dict[str, str]" = {}
    seen_targets: set = set()

    for action in actions or []:
        if len(xpaths) >= _MAX_ELEMENTS:
            break
        if not isinstance(action, dict):
            continue
        target_id = str(action.get("target_id") or "").strip()
        if not target_id or target_id in seen_targets:
            continue
        seen_targets.add(target_id)

        payload = get_target(target_id)
        if not isinstance(payload, dict):
            continue

        xpath = payload.get("xpath")
        if isinstance(xpath, str) and xpath.strip():
            xpaths[f"target:{target_id}"] = xpath
            xpaths[f"target:{target_id}:parent"] = f"({xpath})/parent::*"
            xpaths[f"target:{target_id}:grandparent"] = f"({xpath})/ancestor::*[2]"

        option_map = payload.get("option_xpath_map")
        if isinstance(option_map, dict):
            for i, (label, opt_xpath) in enumerate(option_map.items()):
                if i >= _MAX_OPTIONS_PER_TARGET or len(xpaths) >= _MAX_ELEMENTS:
                    break
                if isinstance(opt_xpath, str) and opt_xpath.strip():
                    xpaths[f"target:{target_id}:option:{label}"] = opt_xpath

    return xpaths


# Lecture seule d'un unique nœud résolu par XPath — aucune mutation, aucun
# side-effect. Champs génériques (pas de logique provider-spécifique).
_RUNTIME_FACTS_JS = r"""(arg) => {
    try {
        const xpath = arg.xpath;
        const el = document.evaluate(
            xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null
        ).singleNodeValue;
        if (!el || el.nodeType !== 1) return null;

        // Wrapper (td, label, div...) résolu pour une cible/option radio/checkbox :
        // l'état réel est porté par l'input natif imbriqué (descente demandée
        // uniquement pour les cibles/options, jamais pour les ancêtres).
        let native = null;
        if (arg.descend && el.tagName && el.tagName.toLowerCase() !== 'input') {
            native = el.querySelector('input[type="checkbox"], input[type="radio"]');
        }
        const src = native || el;

        let rect = null;
        let visible = null;
        try {
            const r = el.getBoundingClientRect();
            rect = { x: r.x, y: r.y, width: r.width, height: r.height };
            const st = window.getComputedStyle(el);
            visible = !!(st && st.display !== 'none' && st.visibility !== 'hidden'
                && r.width > 0 && r.height > 0);
        } catch (e) {}

        let text = '';
        try { text = (el.innerText || el.textContent || '').trim().slice(0, 300); } catch (e) {}

        return {
            tag: el.tagName ? el.tagName.toLowerCase() : null,
            nativeInput: native ? (native.type || 'input') : null,
            value: (typeof src.value !== 'undefined') ? String(src.value) : null,
            checked: (typeof src.checked !== 'undefined') ? !!src.checked : null,
            selected: (typeof src.selected !== 'undefined') ? !!src.selected : null,
            selectedIndex: (typeof src.selectedIndex !== 'undefined') ? src.selectedIndex : null,
            disabled: (typeof src.disabled !== 'undefined') ? !!src.disabled : null,
            readOnly: (typeof src.readOnly !== 'undefined') ? !!src.readOnly : null,
            indeterminate: (typeof src.indeterminate !== 'undefined') ? !!src.indeterminate : null,
            isContentEditable: !!el.isContentEditable,
            className: el.className ? String(el.className) : '',
            ariaChecked: el.getAttribute ? el.getAttribute('aria-checked') : null,
            ariaSelected: el.getAttribute ? el.getAttribute('aria-selected') : null,
            ariaExpanded: el.getAttribute ? el.getAttribute('aria-expanded') : null,
            ariaDisabled: el.getAttribute ? el.getAttribute('aria-disabled') : null,
            href: el.getAttribute ? el.getAttribute('href') : null,
            text: text,
            visible: visible,
            rect: rect,
        };
    } catch (e) {
        return null;
    }
}"""


def _capture_runtime_facts_in_context(ctx, xpaths: "dict[str, str]") -> "dict[str, Any]":
    out: "dict[str, Any]" = {}
    for label, xpath in xpaths.items():
        try:
            descend = not label.endswith((":parent", ":grandparent"))
            out[label] = ctx.evaluate(_RUNTIME_FACTS_JS, {"xpath": xpath, "descend": descend})
        except Exception as exc:
            log_debug(_TAG, f"runtime fact indisponible pour {label}: {type(exc).__name__}")
            out[label] = None
    return out


def capture_runtime_state(driver, actions: Any) -> "Optional[dict]":
    """Faits d'état runtime pour les éléments pertinents des actions demandées.

    Lecture seule, budget borné (_MAX_ELEMENTS/_MAX_OPTIONS_PER_TARGET). Ne
    lève jamais — retourne None sur toute impossibilité (ex. registry vide,
    aucun target_id résolu).
    """
    try:
        xpaths = _resolve_pertinent_xpaths(actions)
        if not xpaths:
            return None

        frame_chain = _first_frame_chain(actions)
        from Survey.frame_utils import switch_to_frame_chain

        with switch_to_frame_chain(driver, frame_chain) as ok:
            ctx = getattr(driver, "_current_frame", driver) if ok else driver
            facts = _capture_runtime_facts_in_context(ctx, xpaths)

        return {
            "elements_count": len(facts),
            "budget": {"max_elements": _MAX_ELEMENTS, "max_options_per_target": _MAX_OPTIONS_PER_TARGET},
            "facts": facts,
        }
    except Exception as exc:
        log_debug(_TAG, f"capture_runtime_state indisponible: {type(exc).__name__}: {exc}")
        return None


_INSTALL_OBSERVER_JS = f"""() => {{
    try {{
        if (window['{_MUTATION_OBSERVER_KEY}']) return true;
        window['{_MUTATION_BUFFER_KEY}'] = [];
        window['{_MUTATION_TRUNCATED_KEY}'] = false;
        const MAX = {_MAX_MUTATIONS};
        const buf = window['{_MUTATION_BUFFER_KEY}'];
        const observer = new MutationObserver((records) => {{
            for (const r of records) {{
                if (buf.length >= MAX) {{ window['{_MUTATION_TRUNCATED_KEY}'] = true; break; }}
                buf.push({{
                    type: r.type,
                    target_tag: (r.target && r.target.tagName) ? r.target.tagName.toLowerCase() : null,
                    target_id: (r.target && r.target.id) || null,
                    target_class: (r.target && r.target.className) || null,
                    attribute_name: r.attributeName || null,
                    old_value: (typeof r.oldValue === 'string') ? r.oldValue.slice(0, 200) : null,
                }});
            }}
        }});
        observer.observe(document.body, {{
            attributes: true, attributeOldValue: true,
            childList: true, subtree: true, characterData: false,
        }});
        window['{_MUTATION_OBSERVER_KEY}'] = observer;
        return true;
    }} catch (e) {{ return false; }}
}}"""

_COLLECT_OBSERVER_JS = f"""() => {{
    try {{
        const observer = window['{_MUTATION_OBSERVER_KEY}'];
        // Observateur absent = perdu (navigation/nouveau document) ou jamais
        // installé : distinct d'une absence réelle de mutation.
        const observerPresent = !!observer;
        if (observer) observer.disconnect();
        const buf = window['{_MUTATION_BUFFER_KEY}'] || [];
        const truncated = !!window['{_MUTATION_TRUNCATED_KEY}'];
        delete window['{_MUTATION_OBSERVER_KEY}'];
        delete window['{_MUTATION_BUFFER_KEY}'];
        delete window['{_MUTATION_TRUNCATED_KEY}'];
        return {{ mutations: buf, truncated: truncated, observer_present: observerPresent }};
    }} catch (e) {{ return {{ mutations: [], truncated: false, observer_present: false }}; }}
}}"""


def install_mutation_observer(driver, actions: Any) -> bool:
    """Installe un MutationObserver passif dans le contexte de frame de la
    première cible résolue. N'observe que les mutations déjà produites par le
    flux normal existant (jamais déclenchées ici). Retourne False sans lever
    si l'installation échoue (contexte détruit, page en transition, etc.)."""
    try:
        frame_chain = _first_frame_chain(actions)
        from Survey.frame_utils import switch_to_frame_chain

        with switch_to_frame_chain(driver, frame_chain) as ok:
            ctx = getattr(driver, "_current_frame", driver) if ok else driver
            return bool(ctx.evaluate(_INSTALL_OBSERVER_JS))
    except Exception as exc:
        log_debug(_TAG, f"install_mutation_observer indisponible: {type(exc).__name__}: {exc}")
        return False


def collect_mutation_observer(driver, actions: Any) -> "Optional[dict]":
    """Lit et déconnecte l'observateur installé par install_mutation_observer,
    dans le même contexte de frame. Retourne None sur toute impossibilité."""
    try:
        frame_chain = _first_frame_chain(actions)
        from Survey.frame_utils import switch_to_frame_chain

        with switch_to_frame_chain(driver, frame_chain) as ok:
            ctx = getattr(driver, "_current_frame", driver) if ok else driver
            result = ctx.evaluate(_COLLECT_OBSERVER_JS)
        return result if isinstance(result, dict) else None
    except Exception as exc:
        log_debug(_TAG, f"collect_mutation_observer indisponible: {type(exc).__name__}: {exc}")
        return None


def build_action_trace(
    *,
    actions: Any,
    dispatcher_success: Optional[bool],
    report: Any,
    before_state: Optional[dict],
    after_state: Optional[dict],
    mutations: Optional[dict],
) -> dict:
    """Assemble un déroulé structuré (requête/avant/après/résultat) à partir de
    faits déjà capturés ou déjà calculés par le validator (report) — aucune
    interprétation ni recalcul différent.
    """
    issues = []
    if isinstance(report, dict):
        issues = [i for i in (report.get("issues") or []) if isinstance(i, dict)]

    requested = [
        {
            "target_id": a.get("target_id"),
            "itype": a.get("itype"),
            "value": a.get("value"),
            "qid": a.get("qid"),
        }
        for a in (actions or [])
        if isinstance(a, dict)
    ]

    # Signaux déjà lus/calculés par le validator (ex. dom_signal/observed d'un
    # issue dispatcher_false_negative) — réutilisés tels quels.
    validator_signals = [
        {"failure_type": i.get("failure_type"), "dom_signal": i.get("dom_signal"), "observed": i.get("observed")}
        for i in issues
        if i.get("dom_signal") or i.get("observed")
    ]

    return {
        "requested_actions": requested,
        "dispatcher_success": dispatcher_success,
        "validator_signals": validator_signals,
        "before": before_state,
        "after": after_state,
        "dom_mutations": mutations,
    }


# ── Scripts externes (contenu) ────────────────────────────────────────────────
# Matière première pour un futur rejeu navigateur : le contenu des <script src>
# du document capturé, jamais sauvegardé jusqu'ici. Stratégie unique : lecture
# depuis l'arbre de ressources DÉJÀ chargé par la page (CDP Page.getResourceTree
# + Page.getResourceContent, session CDP native Playwright comme la capture
# MHTML) — aucune requête réseau, aucune navigation, aucune interaction, et
# fonctionne aussi pour les scripts cross-origin (là où un fetch() depuis la
# page serait bloqué par CORS). Chromium uniquement ; toute erreur est absorbée.
_MAX_SCRIPTS = 60
_MAX_SCRIPT_CHARS = 512 * 1024
_MAX_SCRIPTS_TOTAL_CHARS = 4 * 1024 * 1024

_SCRIPT_SRCS_JS = r"""() => Array.from(document.querySelectorAll('script[src]'))
    .map(s => s.src || '').filter(u => u)"""


def _index_resource_tree(node: Any, out: "dict[str, str]") -> None:
    """url -> frameId des ressources de type Script (premier vu conservé)."""
    if not isinstance(node, dict):
        return
    frame_id = (node.get("frame") or {}).get("id")
    for res in node.get("resources") or []:
        if isinstance(res, dict) and res.get("type") == "Script" and res.get("url"):
            out.setdefault(res["url"], frame_id)
    for child in node.get("childFrames") or []:
        _index_resource_tree(child, out)


def collect_external_scripts(driver, ctx) -> "Optional[list]":
    """Contenu des <script src> du document de `ctx`, borné et tolérant par script.

    Retourne None si la collecte est impossible dans son ensemble (pas de
    session CDP, DOM illisible) — jamais d'exception. Sinon une liste
    d'entrées {url, size, content, error} : `content` est None et `error` porte
    la raison quand un script n'a pas pu être conservé (absent de l'arbre de
    ressources, trop volumineux, budget total atteint, erreur CDP).
    """
    try:
        srcs = ctx.evaluate(_SCRIPT_SRCS_JS) or []
    except Exception as exc:
        log_debug(_TAG, f"external_scripts: lecture des src impossible ({exc!r})")
        return None

    urls: "list[str]" = []
    for u in srcs:
        if isinstance(u, str) and u not in urls:
            urls.append(u)
    if not urls:
        return []

    session = None
    try:
        session = driver.context.new_cdp_session(driver)
        session.send("Page.enable")
        tree = (session.send("Page.getResourceTree") or {}).get("frameTree")
        frame_of: "dict[str, str]" = {}
        _index_resource_tree(tree, frame_of)

        entries: "list[dict]" = []
        total_chars = 0
        for url in urls[:_MAX_SCRIPTS]:
            entry: "dict[str, Any]" = {"url": url, "size": None, "content": None, "error": None}
            entries.append(entry)
            frame_id = frame_of.get(url)
            if frame_id is None:
                entry["error"] = "absent_de_l_arbre_de_ressources"
                continue
            if total_chars >= _MAX_SCRIPTS_TOTAL_CHARS:
                entry["error"] = "budget_total_atteint"
                continue
            try:
                res = session.send("Page.getResourceContent", {"frameId": frame_id, "url": url}) or {}
                if res.get("base64Encoded"):
                    entry["error"] = "contenu_binaire"
                    continue
                content = res.get("content") or ""
            except Exception as exc:
                entry["error"] = f"cdp_erreur: {type(exc).__name__}"
                log_debug(_TAG, f"external_scripts: {url[:120]} -> {exc!r}")
                continue
            entry["size"] = len(content)
            if len(content) > _MAX_SCRIPT_CHARS:
                entry["error"] = "trop_volumineux"
                continue
            entry["content"] = content
            total_chars += len(content)
        if len(urls) > _MAX_SCRIPTS:
            log_debug(_TAG, f"external_scripts: {len(urls)} scripts, tronqué à {_MAX_SCRIPTS}")
        return entries
    except Exception as exc:
        log_debug(_TAG, f"external_scripts: collecte impossible ({exc!r})")
        return None
    finally:
        if session is not None:
            try:
                session.detach()
            except Exception:
                pass


# ── Feuilles de style externes (contenu) ──────────────────────────────────────
# Même principe et mêmes bornes que collect_external_scripts (non modifiée) :
# lecture depuis l'arbre de ressources déjà chargé (CDP), sans requête réseau,
# pour les <link rel="stylesheet" href> du document capturé. Les @import et les
# <style> restent hors périmètre (ces derniers sont déjà reconstruits depuis le
# CSSOM par page_snapshot.py).
_STYLESHEET_HREFS_JS = r"""() => Array.from(document.querySelectorAll('link[rel~="stylesheet" i][href]'))
    .map(l => l.href || '').filter(u => u)"""


def _index_stylesheet_tree(node: Any, out: "dict[str, str]") -> None:
    """url -> frameId des ressources de type Stylesheet (premier vu conservé)."""
    if not isinstance(node, dict):
        return
    frame_id = (node.get("frame") or {}).get("id")
    for res in node.get("resources") or []:
        if isinstance(res, dict) and res.get("type") == "Stylesheet" and res.get("url"):
            out.setdefault(res["url"], frame_id)
    for child in node.get("childFrames") or []:
        _index_stylesheet_tree(child, out)


def collect_external_stylesheets(driver, ctx) -> "Optional[list]":
    """Contenu des <link rel=stylesheet> du document de `ctx`, borné et tolérant
    par ressource. Même contrat de retour que collect_external_scripts :
    None si la collecte est impossible dans son ensemble, sinon une liste
    d'entrées {url, size, content, error}. Ne lève jamais d'exception."""
    try:
        hrefs = ctx.evaluate(_STYLESHEET_HREFS_JS) or []
    except Exception as exc:
        log_debug(_TAG, f"external_stylesheets: lecture des href impossible ({exc!r})")
        return None

    urls: "list[str]" = []
    for u in hrefs:
        if isinstance(u, str) and u not in urls:
            urls.append(u)
    if not urls:
        return []

    session = None
    try:
        session = driver.context.new_cdp_session(driver)
        session.send("Page.enable")
        tree = (session.send("Page.getResourceTree") or {}).get("frameTree")
        frame_of: "dict[str, str]" = {}
        _index_stylesheet_tree(tree, frame_of)

        entries: "list[dict]" = []
        total_chars = 0
        for url in urls[:_MAX_SCRIPTS]:
            entry: "dict[str, Any]" = {"url": url, "size": None, "content": None, "error": None}
            entries.append(entry)
            frame_id = frame_of.get(url)
            if frame_id is None:
                entry["error"] = "absent_de_l_arbre_de_ressources"
                continue
            if total_chars >= _MAX_SCRIPTS_TOTAL_CHARS:
                entry["error"] = "budget_total_atteint"
                continue
            try:
                res = session.send("Page.getResourceContent", {"frameId": frame_id, "url": url}) or {}
                if res.get("base64Encoded"):
                    entry["error"] = "contenu_binaire"
                    continue
                content = res.get("content") or ""
            except Exception as exc:
                entry["error"] = f"cdp_erreur: {type(exc).__name__}"
                log_debug(_TAG, f"external_stylesheets: {url[:120]} -> {exc!r}")
                continue
            entry["size"] = len(content)
            if len(content) > _MAX_SCRIPT_CHARS:
                entry["error"] = "trop_volumineux"
                continue
            entry["content"] = content
            total_chars += len(content)
        if len(urls) > _MAX_SCRIPTS:
            log_debug(_TAG, f"external_stylesheets: {len(urls)} feuilles, tronqué à {_MAX_SCRIPTS}")
        return entries
    except Exception as exc:
        log_debug(_TAG, f"external_stylesheets: collecte impossible ({exc!r})")
        return None
    finally:
        if session is not None:
            try:
                session.detach()
            except Exception:
                pass
