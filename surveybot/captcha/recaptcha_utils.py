# recaptcha_utils.py
import re, json
from selenium.webdriver.common.by import By

def extract_recaptcha_v2_sitekey(driver):
    """Retourne (sitekey, is_invisible, is_enterprise) ou (None, None, False) si introuvable."""
    # Détection Enterprise : iframe src contenant /recaptcha/enterprise/
    is_enterprise = False
    try:
        frames = driver.find_elements(By.CSS_SELECTOR, 'iframe[src*="recaptcha"]')
        for fr in frames:
            src = fr.get_attribute("src") or ""
            if "/recaptcha/enterprise/" in src:
                is_enterprise = True
                break
    except Exception:
        pass

    # 1) balises avec data-sitekey
    try:
        els = driver.find_elements(By.CSS_SELECTOR, "[data-sitekey]")
        for el in els:
            sk = el.get_attribute("data-sitekey")
            if sk:
                inv = (el.get_attribute("data-size") == "invisible") or \
                      ("g-recaptcha-badge" in (el.get_attribute("class") or ""))
                return sk, bool(inv), is_enterprise
    except Exception:
        pass

    # 2) iframes /anchor?k=SITEKEY
    try:
        frames = driver.find_elements(By.CSS_SELECTOR, 'iframe[src*="recaptcha"]')
        for fr in frames:
            src = fr.get_attribute("src") or ""
            m = re.search(r"[?&]k=([A-Za-z0-9_-]+)", src)
            if m:
                inv = ("size=invisible" in src) or ("invisible" in src)
                return m.group(1), bool(inv), is_enterprise
    except Exception:
        pass

    # 3) fallback via ___grecaptcha_cfg
    js = """
    try {
      const cfg = window.___grecaptcha_cfg || {};
      const clients = Object.values(cfg.clients || {});
      for (const c of clients){
        const key = c?.l?.sitekey || c?.o?.sitekey || c?.R?.sitekey || c?.K?.sitekey;
        const size = c?.l?.size    || c?.o?.size    || c?.R?.size    || c?.K?.size;
        if (key) return JSON.stringify({sitekey: key, invisible: size === 'invisible'});
      }
    } catch(e) {}
    return null;
    """
    out = driver.execute_script(js)
    if out:
        d = json.loads(out)
        return d["sitekey"], bool(d.get("invisible")), is_enterprise
    return None, None, False


def inject_recaptcha_token(driver, token: str):
    """Insère le token dans #g-recaptcha-response et déclenche des events."""
    driver.execute_script("""
      (function(tok){
        var el = document.getElementById('g-recaptcha-response');
        if(!el){
          el = document.createElement('textarea');
          el.id = 'g-recaptcha-response';
          el.name = 'g-recaptcha-response';
          el.style.display = 'none';
          document.body.appendChild(el);
        }
        el.value = tok;
        el.dispatchEvent(new Event('change', {bubbles:true}));
        el.dispatchEvent(new Event('input', {bubbles:true}));
      })(arguments[0]);
    """, token)
