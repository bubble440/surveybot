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
"""

import threading
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
# Budget max d'entrées lues par artefact (la capture est déjà bornée à 60).
_MAX_RESOURCES_PER_ARTIFACT = 200


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
        # (type de requête, url exacte) -> contenu capturé
        self._served_resources: Dict[Tuple[str, str], str] = {}

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
        content = self._served_resources.get((request.resource_type, request.url))
        if content is not None:
            route.fulfill(
                status=200,
                content_type=_RESOURCE_CONTENT_TYPES[request.resource_type],
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


def _load_case_resources(artifacts_dir: Path, flags: dict) -> Dict[Tuple[str, str], str]:
    """(type de requête, url) -> contenu, pour les seules entrées du case dont le
    contenu a été capturé et dont l'URL n'est pas ambiguë."""
    out: Dict[Tuple[str, str], str] = {}
    ambiguous: set = set()
    for artifact, kind in _RESOURCE_ARTIFACTS.items():
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
            key = (kind, url)
            if key in out and out[key] != content:
                ambiguous.add(key)
            out[key] = content
    for key in ambiguous:
        out.pop(key, None)
        log_info(_TAG, f"URL ambiguë (contenus différents), non servie: {key[1][:120]}")
    return out
