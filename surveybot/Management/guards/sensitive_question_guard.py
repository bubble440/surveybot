import re
import unicodedata

def _norm(s: str) -> str:
    if not s:
        return ""
    s = s.lower()
    s = s.replace("\u00A0", " ")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s

# Patterns à haut risque → SKIP direct
# NOTE: on évite "screen" tout seul (sinon "screening questions" devient un faux positif)
SENSITIVE_PATTERNS = [
    # Hardware
    r"\bwebcam\b",
    r"\bcamera\b",
    r"\bcamera\b",
    r"\bmicro\b",
    r"\bmicrophone\b",

    # Captcha / vérifs anti-bot
    r"\bcaptcha\b",
    r"\brecaptcha\b",

    # Actions humaines risquées (drag/hold)
    r"\bglisser\b",
    r"\bdrag\b",
    r"\bmaintenir\b",
    r"\bpress\s+and\s+hold\b",
    r"\bhold\b",

    # Permissions / enregistrement
    r"\bautoriser\b",
    r"\bpermission\b",
    r"\benregistrer\b",
    r"\brecord\b",
    r"\brecording\b",

    # Screen sharing / screen recording (strict)
    r"\bscreen\s*(share|sharing|record|recording|capture)\b",
    r"\bshare\s+your\s+screen\b",
    r"\bpartage\s+d[' ]ecran\b",
    r"\benregistrement\s+d[' ]ecran\b",
    r"\bcapture\s+d[' ]ecran\b",

    # Audio/vidéo seulement si mention explicite (on reste strict)
    r"\baudio\b",
    r"\bvideo\b",
    r"\bvideo\b",
    r"\bvideo\b",
]

_COMPILED = [re.compile(p) for p in SENSITIVE_PATTERNS]

def is_sensitive_question(question_text: str) -> bool:
    """
    Détermine si une question doit être skippée
    pour éviter des états navigateur risqués.
    """
    q = _norm(question_text or "")
    if not q:
        return False

    for rx in _COMPILED:
        if rx.search(q):
            return True

    return False
