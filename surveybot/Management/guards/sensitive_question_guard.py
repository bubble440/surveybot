import re

# Mots-clés à haut risque → SKIP direct
SENSITIVE_KEYWORDS = [
    # Hardware
    "webcam", "camera", "caméra",
    "micro", "microphone",
    "audio", "vidéo", "video",

    # Permissions / actions humaines
    "autoriser", "permission", "preview",
    "enregistrer", "record",
    "maintenir", "hold",
    "glisser", "drag",
    "captcha",
    "screen", "écran",
]

def is_sensitive_question(question_text: str) -> bool:
    """
    Détermine si une question doit être skippée
    pour éviter des états navigateur risqués.
    """
    if not question_text:
        return False

    q = question_text.lower()

    for kw in SENSITIVE_KEYWORDS:
        if kw in q:
            return True

    return False
