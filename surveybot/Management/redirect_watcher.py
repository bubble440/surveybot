# redirect_watcher.py
import time

def wait_for_final_redirection(driver, max_wait=30):
    """
    Attend que l'URL du navigateur se stabilise (donc redirection finale atteinte).
    
    :param driver: instance du navigateur Selenium
    :param max_wait: temps maximal (en secondes) pour observer les redirections
    :return: URL finale (stable) ou URL actuelle après expiration du temps
    """
    last_url = driver.current_url
    start_time = time.time()
    stable_count = 0

    while time.time() - start_time < max_wait:
        time.sleep(5)  # on laisse le temps à la redirection de se faire
        current_url = driver.current_url

        if current_url != last_url:
            print(f"🔀 Redirection détectée : {last_url} -> {current_url}")
            last_url = current_url
            stable_count = 0  # reset car une nouvelle redirection est apparue
        else:
            stable_count += 1
            if stable_count >= 3:
                print(f"✅ URL stabilisée : {current_url}")
                return current_url

    print(f"⏱️ Temps d'attente dépassé ({max_wait}s), URL actuelle : {driver.current_url}")
    return driver.current_url

def switch_to_latest_window_and_close_others(driver, base_handles, timeout=10, prefer_external=True):
    """
    Switch vers le nouvel onglet (survey) ET ferme les anciens onglets (ex: TopSurveys).
    """
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

            driver.switch_to.window(new_handle)
            print(f"🪟 Focus sur survey + anciens onglets fermés → {driver.current_url}")
            return True

        # 🧭 Cas 2 : fallback (onglet externe déjà existant)
        if prefer_external:
            for h in current_handles:
                try:
                    driver.switch_to.window(h)
                    url = driver.current_url or ""
                    if "topsurveys.app" not in url:
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
    try:
        txt = driver.execute_script("return document.body ? (document.body.innerText || '') : ''") or ""
        txt = txt.strip()
        # signature stable & cheap
        head = txt[:800]
        tail = txt[-800:] if len(txt) > 800 else ""
        return hash((len(txt), head, tail))
    except Exception:
        try:
            src = driver.page_source or ""
            return hash((len(src), src[:800], src[-800:]))
        except Exception:
            return 0

def wait_for_navigation_or_dom_change(driver, before_url: str = "", before_sig: int = 0, timeout: float = 10.0, poll: float = 0.25) -> bool:
    """
    Attend soit:
    - un changement d'URL
    - OU un changement significatif du DOM (signature)
    Retourne True si changement détecté, sinon False.
    """
    try:
        start = time.time()
        if not before_url:
            before_url = driver.current_url
        if not before_sig:
            before_sig = _dom_signature(driver)

        while time.time() - start < timeout:
            time.sleep(poll)
            try:
                if driver.current_url != before_url:
                    return True
            except Exception:
                pass

            try:
                if _dom_signature(driver) != before_sig:
                    return True
            except Exception:
                pass

        return False
    except Exception:
        return False
