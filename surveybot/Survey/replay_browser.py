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
"""

import threading
from pathlib import Path
from typing import Any, Dict, List, Union

from Survey.failure_replay import _load_json, _pick_dom_file
from Survey.log_utils import log_debug, log_info

_TAG = "[REPLAY_BROWSER]"

# Borne du journal des requêtes refusées (budget max, pas de croissance
# illimitée si une page en boucle réessaie).
MAX_BLOCKED_LOG = 500

_DOCUMENT_URL = "http://replay-case.invalid/document.html"
_DOCUMENT_CSP = "script-src 'none'"


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
        self._served_html: Dict[str, str] = {}

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
        page = self._load_html(html)
        log_info(_TAG, f"document chargé: {case_dir.name}/{name} ({len(html)} car.)")
        return page

    def _load_html(self, html: str) -> Any:
        self._served_html[_DOCUMENT_URL] = html
        page = self.new_page()
        try:
            # domcontentloaded : les sous-ressources sont refusées, on n'attend
            # pas leur « load ».
            page.goto(_DOCUMENT_URL, wait_until="domcontentloaded", timeout=15000)
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
        html = self._served_html.get(request.url)
        if html is not None and request.resource_type == "document":
            route.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                headers={"Content-Security-Policy": _DOCUMENT_CSP},
                body=html,
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
