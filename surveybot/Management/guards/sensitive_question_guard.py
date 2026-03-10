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
    # Hardware (activation / permission contexts only)
    r"\b(activer|activez|enable|enabled?|turn on|allumer|autoriser|allow|permission|access|acces)\b[^\n]{0,80}\b(webcam|camera|cam(?:era)?|micro|microphone|mic)\b",
    r"\b(webcam|camera|cam(?:era)?|micro|microphone|mic)\b[^\n]{0,80}\b(activer|activez|enable|enabled?|turn on|allumer|autoriser|allow|permission|access|acces)\b",

    # Captcha / vérifs anti-bot
    r"\bcaptcha\b",
    r"\brecaptcha\b",

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

_HARDWARE_TERMS_RX = re.compile(r"\b(webcam|camera|cam(?:era)?|micro|microphone|mic)\b")
_POSSESSION_CUES_RX = re.compile(
    r"\b(avez[-\s]?vous|as[-\s]?tu|do you have|have you got|possedez[-\s]?vous|possedez|own|disposez[-\s]?vous)\b"
)
_ACTION_CUES_RX = re.compile(
    r"\b(activer|activez|enable|enabled?|turn on|allumer|autoriser|allow|permission|access|acces|partage|share|record|enregistrer)\b"
)


def _is_hardware_possession_question(q: str) -> bool:
    return bool(
        _HARDWARE_TERMS_RX.search(q)
        and _POSSESSION_CUES_RX.search(q)
        and not _ACTION_CUES_RX.search(q)
    )

def is_sensitive_question(question_text: str) -> bool:
    """
    Détermine si une question doit être skippée
    pour éviter des états navigateur risqués.
    """
    q = _norm(question_text or "")
    if not q:
        return False

    if _is_hardware_possession_question(q):
        return False

    for rx in _COMPILED:
        if rx.search(q):
            return True

    return False
