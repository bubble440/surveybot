from __future__ import annotations

"""Phase 3C.1 — environnement Chromium isolé pour le worker local/dev d'autofix
(replay de failure cases). Ne charge aucun contenu de failure case, n'exécute
aucune action : fournit seulement le navigateur sur lequel la reconstruction de
page (3C.2) puis le rejeu d'actions (3C.4) s'appuieront.

── Séparation avec la production ──────────────────────────────────────────────
Jamais importé par main.py ni par le chemin du bot. Ne touche pas à
preselection/playwright_launcher.py : instance Chromium propre (`launch()`,
profil éphémère détruit à la fermeture), sans user-data-dir, cookies ni session
partagés avec le navigateur de production.

── Stratégie unique : refus réseau systématique au niveau du contexte ─────────
Un seul mécanisme, appliqué au BrowserContext (donc à toutes les pages, popups
et frames qu'il crée, sans traitement au cas par cas) : `context.route("**/*")`
abandonne toute requête (navigation, sous-ressource, fetch/XHR), et
`context.route_web_socket("**/*")` ferme toute WebSocket. Les service workers
sont bloqués (ils contourneraient le routage). Aucune liste blanche, aucune
ressource récupérée en ligne « pour améliorer le rendu » : une ressource
référencée mais absente reste visible comme absente, et chaque requête refusée
est consignée dans `blocked_requests` (bornée) pour que cette absence soit
observable plutôt que masquée. Les URLs `data:`/`about:` ne passent pas par le
réseau et ne sont donc pas concernées.

── Chargement du document principal d'un failure case (3C.2, première brique) ─
`load_case_document()` charge dans une page le seul HTML principal déjà figé du
case, choisi par la logique existante de Survey/failure_replay.py
(`_pick_dom_file`, une seule source de vérité pour « quel fichier selon le
stage », jamais dupliquée ici). Le document est servi depuis la mémoire sous une
origine synthétique (`.invalid`, jamais résolue) : c'est la seule réponse que le
garde-fou réseau laisse passer, et uniquement pour cette URL exacte. Toute
sous-ressource relative ou absolue reste refusée et journalisée. La réponse
porte une CSP `script-src 'none'` : les scripts du provider, déjà exécutés
avant la capture, ne se rejouent pas sur un DOM déjà muté (ils le
dupliqueraient ou le réécriraient) ; `page.evaluate()` de Playwright n'est pas
soumis à cette CSP, donc la page reste interrogeable par les modules
d'analyse. Hors périmètre ici : frames, shadow roots, état runtime des
contrôles, extraction, validation, actions.

── Ressources externes capturées (3C.2, suite) ────────────────────────────────
Si le case porte `external_scripts.json` et/ou `external_stylesheets.json`
(manifest.artifacts, format produit par Survey/browser_capsule.py, non
modifié), les entrées dont le contenu a été capturé sont servies depuis la
mémoire à leur URL EXACTE, et uniquement pour le type de requête attendu
(`script` / `stylesheet`) : c'est la seule extension du garde-fou réseau.
Toute autre requête reste refusée et journalisée. Une entrée sans contenu
(absente, trop volumineuse, erreur…) n'est jamais servie ; une même URL
présente avec deux contenus différents (les URLs des artefacts sont
sanitisées, deux scripts ne différant que par la query string se rejoignent)
est ambiguë et n'est pas servie non plus — jamais devinée.
Même règle pour `external_requests.json` (réponses XHR/fetch émises par la page
au chargement, cf. collect_xhr_fetch_resources) : chaque entrée est servie à son
URL exacte, pour son `request_type` (`xhr`/`fetch`) uniquement, avec le
`content_type` capturé pour CETTE ressource (il varie d'une entrée à l'autre,
contrairement aux scripts/feuilles de style) ; sans `content_type` capturé,
aucun n'est inventé. Une entrée dont le `content_type` est présent mais mal formé
n'est pas servie. Ces réponses ne s'exécutent que si des scripts le sont (elles
sont demandées par eux) : elles ne changent pas la décision `allow_scripts`.
Quand au moins une ressource est servable, le document est servi à son URL
d'origine (`url` de meta.json, sanitisée) pour que les références relatives
du HTML se résolvent comme à la capture ; sinon URL synthétique. Les scripts
(inline et servis) ne sont autorisés à s'exécuter que si au moins un script
externe est servable ; sinon la CSP `script-src 'none'` reste appliquée et le
comportement est identique à celui d'avant ce patch. Attention : les scripts
inline s'exécutent alors sur un DOM déjà muté et peuvent le modifier. Limite :
`url` de meta.json est celle de la page principale ; si le document capturé
est celui d'une frame, ses références relatives ne se résolvent pas (refusées
et journalisées, non devinées).

── État runtime restauré (3C.2, suite) ────────────────────────────────────────
Si le case porte `runtime_state.json` (état capturé au même instant que le
document chargé — l'état « après » pour un stage action), l'état LIVE de chaque
élément déjà résolu par la capture (cible, options, ancêtres) est réappliqué
une fois le document chargé : checked, indeterminate, disabled, readOnly,
selectedIndex/selected et value (input/textarea hors type=file) — ce que le HTML
sérialisé ne porte pas. Les xpaths ne sont pas persistés dans un case : un
élément est retrouvé par son identité capturée (tag + className + texte + input
natif imbriqué et sa valeur) et restauré seulement s'il est unique ; introuvable
ou ambigu = ignoré et journalisé, jamais deviné, jamais bloquant. Aucun
événement n'est émis (un `change` pourrait déclencher un autosubmit) : seules
les propriétés changent, l'affichage d'un widget JS piloté par son propre état
n'est donc pas resynchronisé. Limite : document principal uniquement (pas de
frames). Sans artefact, comportement identique à avant ce patch.

── Extraction du bot rejouée sur la page chargée (3C.3, première brique) ──────
`extract_case_blocks(page, case_dir)` exécute `dom_analyzer.analyze_dom(page)`
tel quel (aucune copie ni réimplémentation) sur la page issue de
`load_case_document` et en retourne les blocs, ainsi que, pour chaque
`target_id` de `actions_requested.json`, le payload du registre DOM
(`dom_registry.get_target`) — la cible sur laquelle une action pourra s'exécuter.
Le registre reste peuplé après l'appel (le dispatcher en dépend) jusqu'à
l'extraction suivante. Isolation entre cases : avant chaque extraction le
registre ET le cache de secours `_STABLE_TEXT_FIELD_LOCATOR` (que
`clear_registry` laisse volontairement survivre aux rescans) sont vidés — les
deux seuls états globaux mutables des modules d'extraction chargés — et les
extractions sont sérialisées par un verrou (état global du process).
Si `question_blocks.json` du case existe, les blocs rejoués lui sont comparés
(mêmes `target_id`, mêmes champs par bloc : options, itype, min/max_select,
question, context…) ; la comparaison n'est déclarée exploitable que si ni le
manifest ni la cible rejouée ne dépendent d'une frame (seul le document
principal est chargé). Aucune action, aucun clic, aucune validation ici.
"""

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlsplit, urlunsplit

from Survey.failure_replay import _load_json, _pick_dom_file
from Survey.log_utils import log_debug, log_info

_TAG = "[REPLAY_BROWSER]"

# Borne du journal des requêtes refusées (budget max, pas de croissance
# illimitée si une page en boucle réessaie).
MAX_BLOCKED_LOG = 500

_DOCUMENT_URL = "http://replay-case.invalid/document.html"
_DOCUMENT_CSP = "script-src 'none'"

# Artefact -> type de requête Playwright pour lequel ses entrées peuvent être
# servies, et Content-Type de la réponse.
_RESOURCE_ARTIFACTS = {
    "external_scripts.json": "script",
    "external_stylesheets.json": "stylesheet",
}
_RESOURCE_CONTENT_TYPES = {
    "script": "application/javascript; charset=utf-8",
    "stylesheet": "text/css; charset=utf-8",
}
# Artefact dont chaque entrée porte son propre type de requête (`request_type`)
# et son propre Content-Type (`content_type`), au lieu d'un type fixe par
# artefact ci-dessus.
_REQUEST_ARTIFACT = "external_requests.json"
_REQUEST_TYPES = ("xhr", "fetch")
# Budget max d'entrées lues par artefact (la capture est déjà bornée à 60).
_MAX_RESOURCES_PER_ARTIFACT = 200
# Extraction rejouée : budgets (cibles d'actions lues, blocs comparés) et verrou
# (le registre DOM et le cache de secours sont des états globaux du process).
_MAX_ACTION_TARGETS = 40
_MAX_COMPARED_BLOCKS = 200
_EXTRACTION_LOCK = threading.Lock()

# Budget max de faits d'état runtime restaurés (la capture est déjà bornée à 40).
_MAX_RUNTIME_FACTS = 60

# Restauration de l'état live d'éléments déjà résolus par la capture. Un élément
# est retrouvé par son identité capturée (tag + className + texte + input natif
# imbriqué et sa valeur) ; restauré seulement s'il est unique. Aucun événement
# n'est émis : seules les propriétés changent.
_RESTORE_RUNTIME_JS = r"""(items) => {
    const out = {};
    const txt = el => { try { return (el.innerText || el.textContent || '').trim().slice(0, 300); } catch (e) { return ''; } };
    const nativeOf = el => el.querySelector('input[type="checkbox"], input[type="radio"]');
    for (const [label, f] of items) {
        try {
            const cands = Array.from(document.getElementsByTagName(f.tag)).filter(el => {
                if ((el.className ? String(el.className) : '') !== f.className) return false;
                if (txt(el) !== f.text) return false;
                if (f.nativeInput) {
                    const n = nativeOf(el);
                    return !!n && (n.type || 'input') === f.nativeInput && String(n.value) === f.value;
                }
                return true;
            });
            if (cands.length === 0) { out[label] = 'not_found'; continue; }
            if (cands.length > 1) { out[label] = 'ambiguous'; continue; }
            const src = f.nativeInput ? nativeOf(cands[0]) : cands[0];
            if (f.checked !== null && 'checked' in src) src.checked = f.checked;
            if (f.indeterminate !== null && 'indeterminate' in src) src.indeterminate = f.indeterminate;
            if (f.disabled !== null && 'disabled' in src) src.disabled = f.disabled;
            if (f.readOnly !== null && 'readOnly' in src) src.readOnly = f.readOnly;
            if (f.selectedIndex !== null && 'selectedIndex' in src) src.selectedIndex = f.selectedIndex;
            if (f.selected !== null && 'selected' in src) src.selected = f.selected;
            if (f.value !== null && (src.tagName === 'TEXTAREA' || (src.tagName === 'INPUT' && src.type !== 'file'))) src.value = f.value;
            out[label] = 'restored';
        } catch (e) { out[label] = 'error'; }
    }
    return out;
}"""


class ReplayBrowserError(RuntimeError):
    """Démarrage ou utilisation invalide de l'environnement de replay."""


class IsolatedReplayBrowser:
    """Chromium isolé, réseau bloqué par défaut. Usage :

        with IsolatedReplayBrowser() as rb:
            page = rb.new_page()
            ...
            rb.blocked_requests  # ressources demandées mais refusées
    """

    def __init__(self, headless: bool = True) -> None:
        self._headless = headless
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._lock = threading.Lock()
        self.blocked_requests: List[Dict[str, str]] = []
        self.blocked_total = 0
        # url -> (html, scripts_autorisés)
        self._served_html: Dict[str, Tuple[str, bool]] = {}
        # (type de requête, url exacte) -> (contenu capturé, Content-Type ou None)
        self._served_resources: Dict[Tuple[str, str], Tuple[str, Optional[str]]] = {}
        # clé du fait -> restored / not_found / ambiguous / error (dernier case chargé)
        self.runtime_restore_report: Dict[str, str] = {}

    # -- cycle de vie ---------------------------------------------------------
    def start(self) -> "IsolatedReplayBrowser":
        if self._context is not None:
            raise ReplayBrowserError("déjà démarré")
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(headless=self._headless)
            self._context = self._browser.new_context(
                service_workers="block",
                accept_downloads=False,
            )
            # Garde-fou installé avant toute page : aucune requête ne peut
            # partir avant que le blocage soit en place.
            self._context.route("**/*", self._block_request)
            self._context.route_web_socket("**/*", self._block_websocket)
        except Exception:
            self.close()
            raise
        log_info(_TAG, "démarré (réseau bloqué par défaut, profil éphémère)")
        return self

    def new_page(self) -> Any:
        if self._context is None:
            raise ReplayBrowserError("non démarré")
        return self._context.new_page()

    def load_case_document(self, case_dir: Union[str, Path]) -> Any:
        """Charge le HTML principal figé d'un failure case dans une nouvelle page
        et la retourne. Lève ReplayBrowserError si le document n'est pas
        chargeable (jamais de repli sur un autre fichier)."""
        case_dir = Path(case_dir)
        manifest, err = _load_json(case_dir / "manifest.json")
        if err or not isinstance(manifest, dict):
            raise ReplayBrowserError(f"manifest.json {err or 'invalide (objet JSON attendu)'}")
        flags = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
        artifacts_dir = case_dir / "artifacts"
        name, err = _pick_dom_file(str(manifest.get("stage") or "unknown"), artifacts_dir, flags)
        if err or not name:
            raise ReplayBrowserError(err or "aucun document sélectionné")
        try:
            html = (artifacts_dir / name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ReplayBrowserError(f"{name} illisible ({exc})") from exc
        resources = _load_case_resources(artifacts_dir, flags)
        # Réinitialisé à chaque chargement : les ressources servies sont celles
        # du dernier case chargé.
        self._served_resources = resources
        doc_url = _DOCUMENT_URL
        allow_scripts = any(kind == "script" for kind, _ in resources)
        if resources:
            doc_url = _original_document_url(artifacts_dir) or _DOCUMENT_URL
        page = self._load_html(html, doc_url, allow_scripts)
        self.runtime_restore_report = {}
        if flags.get("runtime_state.json") is True:
            self.runtime_restore_report = _restore_runtime_state(page, artifacts_dir / "runtime_state.json")
        log_info(
            _TAG,
            f"document chargé: {case_dir.name}/{name} ({len(html)} car., "
            f"{len(resources)} ressource(s) servie(s), scripts={'on' if allow_scripts else 'off'})",
        )
        return page

    def _load_html(self, html: str, url: str = _DOCUMENT_URL, allow_scripts: bool = False) -> Any:
        self._served_html[url] = (html, allow_scripts)
        page = self.new_page()
        try:
            # domcontentloaded : les sous-ressources sont refusées, on n'attend
            # pas leur « load ».
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
        except Exception as exc:
            page.close()
            raise ReplayBrowserError(f"chargement du document échoué ({exc})") from exc
        return page

    def close(self) -> None:
        for name, closer in (
            ("context", lambda: self._context and self._context.close()),
            ("browser", lambda: self._browser and self._browser.close()),
            ("playwright", lambda: self._pw and self._pw.stop()),
        ):
            try:
                closer()
            except Exception as exc:
                log_debug(_TAG, f"close {name}: {exc!r}")
        self._context = self._browser = self._pw = None
        if self.blocked_total:
            log_info(_TAG, f"fermé — {self.blocked_total} requête(s) réseau refusée(s)")

    def __enter__(self) -> "IsolatedReplayBrowser":
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- garde-fou réseau -----------------------------------------------------
    def _record(self, kind: str, url: str, resource_type: str = "") -> None:
        with self._lock:
            self.blocked_total += 1
            if len(self.blocked_requests) < MAX_BLOCKED_LOG:
                self.blocked_requests.append(
                    {"kind": kind, "resource_type": resource_type, "url": url}
                )
        log_debug(_TAG, f"refusé {kind} {resource_type} {url[:200]}")

    def _block_request(self, route: Any, request: Any) -> None:
        doc = self._served_html.get(request.url)
        if doc is not None and request.resource_type == "document":
            html, allow_scripts = doc
            route.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                headers={} if allow_scripts else {"Content-Security-Policy": _DOCUMENT_CSP},
                body=html,
            )
            return
        served = self._served_resources.get((request.resource_type, request.url))
        if served is not None:
            content, content_type = served
            route.fulfill(
                status=200,
                content_type=content_type,
                # Requis pour les ressources `crossorigin`/modules : l'origine
                # du document rejoué est celle d'origine, pas celle du CDN.
                headers={"Access-Control-Allow-Origin": "*"},
                body=content,
            )
            return
        self._record("request", request.url, request.resource_type)
        route.abort("blockedbyclient")

    def _block_websocket(self, ws: Any) -> Any:
        self._record("websocket", ws.url, "websocket")
        # Playwright exécute les handlers WebSocket directement sur sa boucle
        # asyncio (pas dans le greenlet de l'API sync) : `ws.close()` sync s'y
        # bloquerait pour toujours. On retourne la coroutine de l'implémentation,
        # que Playwright attend lui-même.
        return ws._impl_obj.close(code=1008, reason="replay_network_blocked")


def _original_document_url(artifacts_dir: Path) -> Optional[str]:
    """URL http(s) d'origine du document (meta.json, déjà sanitisée), normalisée
    comme Chromium la présente (chemin vide -> "/"). None si absente/invalide."""
    meta, err = _load_json(artifacts_dir / "meta.json")
    url = meta.get("url") if isinstance(meta, dict) and not err else None
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))


def _restore_runtime_state(page: Any, state_path: Path) -> Dict[str, str]:
    """Réapplique sur `page` l'état live capturé dans runtime_state.json (voir
    docstring du module). Ne lève jamais : tout échec est journalisé et ignoré."""
    state, err = _load_json(state_path)
    facts = state.get("facts") if isinstance(state, dict) and not err else None
    if not isinstance(facts, dict):
        log_info(_TAG, f"runtime_state.json illisible/invalide ({err or 'objet facts attendu'}) — ignoré")
        return {}
    items = []
    for label, fact in facts.items():
        if not isinstance(label, str) or not isinstance(fact, dict):
            continue  # fait non résolu à la capture (null) : rien à restaurer
        if not isinstance(fact.get("tag"), str):
            continue
        items.append([label, {
            "tag": fact["tag"],
            "className": fact.get("className") or "",
            "text": fact.get("text") or "",
            "nativeInput": fact.get("nativeInput"),
            "value": fact.get("value"),
            "checked": fact.get("checked"),
            "indeterminate": fact.get("indeterminate"),
            "disabled": fact.get("disabled"),
            "readOnly": fact.get("readOnly"),
            "selectedIndex": fact.get("selectedIndex"),
            "selected": fact.get("selected"),
        }])
    if len(items) > _MAX_RUNTIME_FACTS:
        log_info(_TAG, f"runtime_state: {len(items)} faits, tronqué à {_MAX_RUNTIME_FACTS}")
        items = items[:_MAX_RUNTIME_FACTS]
    try:
        report = page.evaluate(_RESTORE_RUNTIME_JS, items) or {}
    except Exception as exc:
        log_info(_TAG, f"état runtime non restauré ({type(exc).__name__}) — document conservé tel quel")
        return {}
    for label, status in report.items():
        if status != "restored":
            log_debug(_TAG, f"état runtime non restauré: {label} -> {status}")
    counts = {s: sum(1 for v in report.values() if v == s) for s in set(report.values())}
    log_info(_TAG, f"état runtime restauré: {counts.get('restored', 0)}/{len(items)} élément(s) {counts}")
    return report


def _valid_content_type(value: Any) -> bool:
    """Content-Type capturé : absent (None) ou chaîne ASCII imprimable bornée."""
    return value is None or (
        isinstance(value, str) and 0 < len(value) <= 200 and value.isascii() and value.isprintable()
    )


def _load_case_resources(
    artifacts_dir: Path, flags: dict
) -> Dict[Tuple[str, str], Tuple[str, Optional[str]]]:
    """(type de requête, url) -> (contenu, Content-Type), pour les seules entrées
    du case dont le contenu a été capturé et dont l'URL n'est pas ambiguë."""
    out: Dict[Tuple[str, str], Tuple[str, Optional[str]]] = {}
    ambiguous: set = set()
    artifacts = [*_RESOURCE_ARTIFACTS.items(), (_REQUEST_ARTIFACT, None)]
    for artifact, fixed_kind in artifacts:
        if flags.get(artifact) is not True or not (artifacts_dir / artifact).is_file():
            continue
        entries, err = _load_json(artifacts_dir / artifact)
        if err or not isinstance(entries, list):
            log_info(_TAG, f"{artifact} illisible/invalide ({err or 'liste attendue'}) — ignoré")
            continue
        if len(entries) > _MAX_RESOURCES_PER_ARTIFACT:
            log_info(_TAG, f"{artifact}: {len(entries)} entrées, tronqué à {_MAX_RESOURCES_PER_ARTIFACT}")
        for entry in entries[:_MAX_RESOURCES_PER_ARTIFACT]:
            if not isinstance(entry, dict):
                continue
            url, content = entry.get("url"), entry.get("content")
            if not isinstance(url, str) or not isinstance(content, str):
                continue
            if fixed_kind is not None:
                kind, content_type = fixed_kind, _RESOURCE_CONTENT_TYPES[fixed_kind]
            else:
                kind, content_type = entry.get("request_type"), entry.get("content_type")
                if kind not in _REQUEST_TYPES or not _valid_content_type(content_type):
                    continue
            key = (kind, url)
            value = (content, content_type)
            if key in out and out[key] != value:
                ambiguous.add(key)
            out[key] = value
    for key in ambiguous:
        out.pop(key, None)
        log_info(_TAG, f"URL ambiguë (contenus différents), non servie: {key[1][:120]}")
    return out


@dataclass
class CaseExtraction:
    """Résultat de extract_case_blocks. `error` non vide = analyze_dom a levé
    (blocs/cibles vides) ; `comparison` None = pas de question_blocks.json."""

    blocks: List[Dict[str, Any]] = field(default_factory=list)
    # target_id d'une action du case -> copie du payload du registre (None = introuvable)
    targets: Dict[str, Optional[Dict[str, Any]]] = field(default_factory=dict)
    comparison: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


def _compare_extraction(
    original: Any, blocks: List[Dict[str, Any]], manifest: dict, targets: Dict[str, Optional[dict]]
) -> Optional[Dict[str, Any]]:
    """Blocs rejoués vs question_blocks.json du case, bloc par bloc (clé target_id)."""
    if not isinstance(original, list):
        return None
    reasons = []
    if manifest.get("frame_chain"):
        reasons.append("le case dépend d'une frame (manifest.frame_chain) — seul le document principal est chargé")
    if any(isinstance(p, dict) and p.get("frame_chain") for p in targets.values()):
        reasons.append("l'extraction rejouée cible une frame")
    if len(original) > _MAX_COMPARED_BLOCKS or len(blocks) > _MAX_COMPARED_BLOCKS:
        log_info(_TAG, f"comparaison bornée à {_MAX_COMPARED_BLOCKS} blocs")

    def _by_id(items: list, normalize: bool) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        for b in items[:_MAX_COMPARED_BLOCKS]:
            if isinstance(b, dict) and isinstance(b.get("target_id"), str):
                # Même passage JSON que l'artefact d'origine (tuples -> listes).
                out[b["target_id"]] = json.loads(json.dumps(b, default=str)) if normalize else b
        return out

    orig_by_id, new_by_id = _by_id(original, False), _by_id(blocks, True)
    missing = sorted(orig_by_id.keys() - new_by_id.keys())
    extra = sorted(new_by_id.keys() - orig_by_id.keys())
    differences: Dict[str, Dict[str, Any]] = {}
    for tid in sorted(orig_by_id.keys() & new_by_id.keys()):
        o, n = orig_by_id[tid], new_by_id[tid]
        diff = {k: {"original": o.get(k), "replay": n.get(k)} for k in sorted(set(o) | set(n)) if o.get(k) != n.get(k)}
        if diff:
            differences[tid] = diff
    return {
        "comparable": not reasons,
        "reasons": reasons,
        "identical": not (missing or extra or differences),
        "missing_target_ids": missing,
        "extra_target_ids": extra,
        "differences": differences,
    }


def extract_case_blocks(page: Any, case_dir: Union[str, Path]) -> CaseExtraction:
    """Exécute l'extraction du bot (dom_analyzer.analyze_dom, non modifié) sur
    `page` — la page retournée par IsolatedReplayBrowser.load_case_document pour
    ce même `case_dir` — et retourne blocs, cibles des actions du case et
    comparaison à question_blocks.json (voir docstring du module). Ne lève pas
    pour une erreur d'extraction : elle est reportée dans `CaseExtraction.error`."""
    case_dir = Path(case_dir)
    manifest, err = _load_json(case_dir / "manifest.json")
    manifest = manifest if isinstance(manifest, dict) and not err else {}
    artifacts_dir = case_dir / "artifacts"
    original, _ = _load_json(artifacts_dir / "question_blocks.json")
    actions, _ = _load_json(artifacts_dir / "actions_requested.json")
    target_ids: List[str] = []
    for action in (actions if isinstance(actions, list) else [])[:_MAX_ACTION_TARGETS]:
        tid = action.get("target_id") if isinstance(action, dict) else None
        if isinstance(tid, str) and tid and tid not in target_ids:
            target_ids.append(tid)

    result = CaseExtraction()
    with _EXTRACTION_LOCK:
        try:
            import Survey.dom_analyzer as dom_analyzer
            from Survey.dom_registry import _STABLE_TEXT_FIELD_LOCATOR, clear_registry, get_target

            # analyze_dom vide déjà le registre ; le cache de secours, lui, survit
            # volontairement aux rescans en production : ici il fuirait d'un case à l'autre.
            clear_registry()
            _STABLE_TEXT_FIELD_LOCATOR.clear()
            result.blocks = [b for b in (dom_analyzer.analyze_dom(page) or []) if isinstance(b, dict)]
            for tid in target_ids:
                payload = get_target(tid)
                result.targets[tid] = dict(payload) if isinstance(payload, dict) else None
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            result.blocks, result.targets = [], {}
    if result.error:
        log_info(_TAG, f"extraction échouée ({result.error[:200]}) — case {case_dir.name}")
        return result
    result.comparison = _compare_extraction(original, result.blocks, manifest, result.targets)
    cmp_ = result.comparison
    log_info(
        _TAG,
        f"extraction {case_dir.name}: {len(result.blocks)} bloc(s), cibles résolues "
        f"{sum(1 for p in result.targets.values() if p)}/{len(target_ids)}, comparaison "
        + ("absente" if cmp_ is None else f"identical={cmp_['identical']} comparable={cmp_['comparable']}"),
    )
    return result

