# redirect_watcher.py
import time
from dataclasses import dataclass

from Survey.log_utils import log_info



@dataclass
class NavResult:
    """Résultat structuré de wait_for_navigation_or_dom_change.

    Utilisable comme bool (True si un changement a été détecté) :
        if nav:        ...  # changement quelconque
        if nav.url_changed:  ...  # URL a changé (latence proxy présente)
        if nav.dom_changed:  ...  # SPA : seul le DOM a changé
    """
    changed: bool
    url_changed: bool
    dom_changed: bool

    def __bool__(self) -> bool:
        return self.changed

def wait_for_final_redirection(driver, max_wait=30):
    """
    Attend que l'URL du navigateur se stabilise (donc redirection finale atteinte).
    """
    page = driver
    last_url = page.url
    start_time = time.time()
    stable_count = 0

    visited_urls = [last_url]

    def _on_nav(frame):
        if frame == page.main_frame:
            url = frame.url
            if url and (not visited_urls or visited_urls[-1] != url):
                visited_urls.append(url)

    page.on("framenavigated", _on_nav)

    try:
        while time.time() - start_time < max_wait:
            time.sleep(5)
            current_url = page.url

            if current_url != last_url:
                print(f"🔀 Redirection détectée : {last_url} -> {current_url}")
                last_url = current_url
                stable_count = 0
            else:
                stable_count += 1
                if stable_count >= 3:
                    print(f"✅ URL stabilisée : {current_url}")
                    log_info("REDIRECT_CHAIN", " -> ".join(visited_urls))
                    return current_url

        print(f"⏱️ Temps d'attente dépassé ({max_wait}s), URL actuelle : {page.url}")
        log_info("REDIRECT_CHAIN", " -> ".join(visited_urls))
        return page.url
    finally:
        page.remove_listener("framenavigated", _on_nav)

def switch_to_latest_window_and_close_others(driver, base_handles, timeout=10, prefer_external=True, platform_domains=None):
    """
    Switch vers le nouvel onglet (survey) ET ferme les anciens onglets (ex: plateforme).
    platform_domains : liste de domaines appartenant à la plateforme (ex: ['topsurveys.app']).
                       Si None, utilise ['topsurveys.app'] pour la rétrocompatibilité.
    """
    _domains = platform_domains if platform_domains is not None else ["topsurveys.app"]

    page = driver
    start = time.time()

    while time.time() - start < timeout:
        time.sleep(0.25)
        current_pages = page.context.pages
        new_pages = [p for p in current_pages if p not in base_handles]

        # 🪟 Cas 1 : nouvel onglet détecté
        if new_pages:
            new_page = new_pages[-1]
            new_page.bring_to_front()

            # 🔥 Fermer tous les anciens onglets
            for p in list(base_handles):
                try:
                    p.close()
                except Exception:
                    pass

            # FIX-B4: new_page peut avoir été fermé par Chrome pendant qu'on fermait
            # les anciens onglets (ex : le survey s'est lui-même redirigé et a détruit
            # son propre onglet).  On vérifie que la page est encore vivante.
            live_pages = page.context.pages
            if new_page in live_pages:
                new_page.bring_to_front()
            elif live_pages:
                live_pages[-1].bring_to_front()
            else:
                raise RuntimeError("Aucun onglet restant après fermeture des anciens onglets")
            print(f"🪟 Focus sur survey + anciens onglets fermés → {new_page.url}")
            return True

        # 🧭 Cas 2 : fallback (onglet externe déjà existant)
        if prefer_external:
            for p in current_pages:
                try:
                    url = p.url or ""
                    if not any(d in url for d in _domains):
                        # fermer les autres
                        for op in current_pages:
                            if op is not p:
                                try:
                                    op.close()
                                except Exception:
                                    pass
                        p.bring_to_front()
                        print(f"🧭 Fallback externe + nettoyage onglets → {url}")
                        return True
                except Exception:
                    continue

    print("⚠️ Aucun onglet externe détecté.")
    # 🛡️ Sécurité finale : s'assurer qu'on est sur une page valide
    live_pages = page.context.pages
    if live_pages:
        live_pages[-1].bring_to_front()
    else:
        raise RuntimeError("Aucun onglet restant après nettoyage")

    return False

def _dom_signature(driver) -> int:
    """
    Signature DOM cheap: basée sur innerText (moins lourd que page_source).
    """
    page = driver
    try:
        txt = page.evaluate("() => document.body ? (document.body.innerText || '') : ''") or ""
        txt = txt.strip()
        head = txt[:800]
        tail = txt[-800:] if len(txt) > 800 else ""
        return hash((len(txt), head, tail))
    except Exception:
        try:
            src = page.content() or ""
            return hash((len(src), src[:800], src[-800:]))
        except Exception:
            return 0


def wait_for_navigation_or_dom_change(driver, *, before_url: str, before_sig: str | None = None, timeout: int = 10) -> NavResult:
    """
    Attend un changement notable après un clic CTA.
    Best-effort, jamais bloquant.
    """
    page = driver
    end = time.time() + max(1, int(timeout or 10))

    try:
        if before_sig is None:
            before_sig = _dom_signature(driver)
    except Exception:
        before_sig = before_sig or ""

    while time.time() < end:
        try:
            if page.url != (before_url or ""):
                return NavResult(changed=True, url_changed=True, dom_changed=False)
        except Exception:
            pass

        try:
            sig = _dom_signature(driver)
            if sig and before_sig and sig != before_sig:
                return NavResult(changed=True, url_changed=False, dom_changed=True)
        except Exception:
            pass

        time.sleep(0.2)

    return NavResult(changed=False, url_changed=False, dom_changed=False)


def wait_for_page_load(driver, timeout=30):
    """
    Attend que document.readyState soit 'complete'.
    Retourne instantanément si la page est déjà chargée (cas radio/checkbox/SPA).
    Best-effort : ne lève jamais d'exception.
    """
    page = driver
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if page.evaluate("() => document.readyState") == "complete":
                return True
        except Exception:
            pass
        time.sleep(0.25)
    print(f"[wait_for_page_load] Timeout {timeout}s dépassé — readyState non 'complete'")
    return False
