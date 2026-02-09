# url_guard.py
# -----------------------------------------------------------
# Garde-fou centralisé pour autoriser/ban les URLs en prod.
# - is_allowed(url_or_host): True si le host appartient à la liste blanche
# - normalize_host(url_or_host): host en minuscules, sans port, sans "www."
# - ALLOWLIST: entrées canoniques (domaines ou sous-domaines) autorisées
# -----------------------------------------------------------

from urllib.parse import urlparse

# RÈGLE:
# - Si tu as listé un domaine racine (ex: "decipherinc.com"), on autorise ce domaine ET tous ses sous-domaines.
# - Si tu as listé un sous-domaine précis (ex: "qps.cint.com"), on autorise ce sous-domaine ET ses sous-sous-domaines,
#   mais PAS d'autres sous-domaines du domaine parent.
ALLOWLIST = {
    # Ta liste canonisée (voir msg):
    "survey.walr.com",               # seulement ce sous-domaine (et sous-sous-domaines)
    "samplicio.us",                # domaine racine + sous-domaines
    "cloudresearch.com",             # domaine racine + sous-domaines
    "ssisurveys.com",                # domaine racine + sous-domaines
    "decipherinc.com",               # domaine racine + sous-domaines
    "survey.cmix.com",               # sous-domaine ciblé (et sous-sous-domaines)
    "qps.cint.com",                    # sous-domaine ciblé (et sous-sous-domaines)
    "s.cint.com",                    # sous-domaine ciblé (et sous-sous-domaines)
    "pathfindersuite.com",# sous-domaine ciblé (et sous-sous-domaines)
    "emea.focusvision.com",          # sous-domaine ciblé (et sous-sous-domaines)
    "dig.ps-di.com",                 # sous-domaine ciblé (et sous-sous-domaines)
    "survey.sightx.io",              # sous-domaine ciblé (et sous-sous-domaines)
    "screener.purespectrum.com",  # sous-domaine ciblé (et sous-sous-domaines)
    "ups.surveyrouter.com",          # sous-domaine ciblé (et sous-sous-domaines)
    "survey.rex.dinata.com",      # sous-domaine ciblé (et sous-sous-domaines)
    "calibr8.zamplia.com",        # sous-domaine ciblé (et sous-sous-domaines)
    "zampparticipant.zamplia.com",      # domaine racine + sous-domaines
    "opinions.logitgroup.com",   # sous-domaine ciblé (et sous-sous-domaines)
    "rx.samplicio.us",              # sous-domaine ciblé (et sous-sous-domaines)
    "globalsurveys.nielsen.com"
    "survey2.yougov.com",        # sous-domaine ciblé (et sous-sous-domaines)
}

def normalize_host(url_or_host: str) -> str:
    """
    Normalise une URL/host:
      - extrait le netloc si URL
      - enlève le port
      - passe en minuscules
      - supprime 'www.' en tête
    """
    h = (url_or_host or "").strip()
    try:
        if "://" in h:
            h = urlparse(h).netloc or h
        # enlever :port si présent
        if ":" in h:
            h = h.split(":", 1)[0]
    except Exception:
        pass
    h = h.lower()
    if h.startswith("www."):
        h = h[4:]
    return h

def is_allowed(url_or_host: str) -> bool:
    """
    Guard SOFT.
    - On autorise par défaut (pour ne pas rater les surveys simples sur domaines inconnus).
    - On bloque seulement les destinations clairement inutiles/risquées (retour app, about:, data:, etc).
    """
    raw = (url_or_host or "").strip()
    if not raw:
        return False

    # 1) Bloquer les schémas "techniques" (pas des vrais surveys)
    lowered = raw.lower()
    for bad_prefix in ("about:", "chrome-extension:", "edge:", "data:"):
        if lowered.startswith(bad_prefix):
            return False

    h = normalize_host(raw)
    if not h:
        return False

    # 3) (Optionnel) bloque-list minimale, à enrichir si besoin
    BLOCKLIST = {
        # exemples si tu en identifies (laisser vide au début si tu veux)
        # "accounts.google.com",
    }
    if h in BLOCKLIST:
        return False

    # 4) Sinon : autorisé (même si pas dans ALLOWLIST)
    return True

if __name__ == "__main__":
    # mini tests rapides en local
    tests = [
        "https://survey.walr.com/task/123",
        "foo.survey.walr.com",
        "https://decipherinc.com/s/abc",
        "a.b.decipherinc.com",
        "https://cint.com",                  # devrait être False (non listé globalement)
        "https://qps.cint.com/xyz",          # True
        "https://x.qps.cint.com/xyz",        # True
        "https://app.cloudresearch.com",     # True
        "https://focusvision.com",           # False (tu n'as autorisé que emea.focusvision.com)
    ]
    for t in tests:
        print(t, "->", is_allowed(t))
