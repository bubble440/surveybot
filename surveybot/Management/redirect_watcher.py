# redirect_watcher.py
import time
from dataclasses import dataclass


def _pw_page(d):
    """Extrait la Page Playwright native depuis un PlaywrightDriverShim ou retourne d tel quel."""
    if hasattr(d, "_page"):
        return d._page
    return d

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
    page = _pw_page(driver)
    last_url = page.url
    start_time = time.time()
    stable_count = 0

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
                return current_url

    print(f"⏱️ Temps d'attente dépassé ({max_wait}s), URL actuelle : {page.url}")
    return page.url

def switch_to_latest_window_and_close_others(driver, base_handles, timeout=10, prefer_external=True, platform_domains=None):
    """
    Switch vers le nouvel onglet (survey) ET ferme les anciens onglets (ex: plateforme).
    platform_domains : liste de domaines appartenant à la plateforme (ex: ['topsurveys.app']).
                       Si None, utilise ['topsurveys.app'] pour la rétrocompatibilité.
    """
    _domains = platform_domains if platform_domains is not None else ["topsurveys.app"]

    start = time.time()

    while time.time() - start < timeout:
        time.sleep(0.25)
        current_handles = driver.window_handles
        new_handles = [h for h in current_handles if h not in base_handles]

        # 🪟 Cas 1 : nouvel onglet détecté
        if new_handles:
            new_handle = new_handles[-1]
            driver.switch_to.window(new_handle)

            # 🔥 Fermer tous les anciens onglets
            for h in list(base_handles):
                try:
                    driver.switch_to.window(h)
                    driver.close()
                except Exception:
                    pass

            # FIX-B4: new_handle peut avoir été fermé par Chrome pendant qu'on fermait
            # les anciens onglets (ex : le survey s'est lui-même redirigé et a détruit
            # son propre onglet).  Un switch aveugle lèverait NoSuchWindowException.
            live_handles = driver.window_handles
            if new_handle in live_handles:
                driver.switch_to.window(new_handle)
            elif live_handles:
                driver.switch_to.window(live_handles[-1])
            else:
                raise RuntimeError("Aucun onglet restant après fermeture des anciens onglets")
            print(f"🪟 Focus sur survey + anciens onglets fermés → {driver.current_url}")
            return True

        # 🧭 Cas 2 : fallback (onglet externe déjà existant)
        if prefer_external:
            for h in current_handles:
                try:
                    driver.switch_to.window(h)
                    url = driver.current_url or ""
                    if not any(d in url for d in _domains):
                        # fermer les autres
                        for oh in current_handles:
                            if oh != h:
                                try:
                                    driver.switch_to.window(oh)
                                    driver.close()
                                except Exception:
                                    pass
                        driver.switch_to.window(h)
                        print(f"🧭 Fallback externe + nettoyage onglets → {url}")
                        return True
                except Exception:
                    continue

    print("⚠️ Aucun onglet externe détecté.")
    # 🛡️ Sécurité finale : s'assurer qu'on est sur un handle valide
    handles = driver.window_handles
    if handles:
        driver.switch_to.window(handles[-1])
    else:
        raise RuntimeError("Aucun onglet restant après nettoyage")

    return False

def _dom_signature(driver) -> int:
    """
    Signature DOM cheap: basée sur innerText (moins lourd que page_source).
    """
    page = _pw_page(driver)
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
    page = _pw_page(driver)
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
    page = _pw_page(driver)
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
