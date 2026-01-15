import os

RUN_MODE = os.getenv("RUN_MODE", "prod")  # prod | local
BROWSER_MODE = os.getenv("BROWSER_MODE", "normal")  # normal | attach

def is_attach_mode():
    return RUN_MODE == "local" and BROWSER_MODE == "attach"
