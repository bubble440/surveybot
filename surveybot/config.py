import os

# RUN_ENV décrit l'environnement d'exécution (utilisé partout dans le projet)
# - local: dev/tests
# - aws/docker: prod (ECS)
RUN_ENV = os.getenv("RUN_ENV", "local")  # local | aws | docker

RUN_MODE = os.getenv("RUN_MODE", "prod")        # prod | local
BROWSER_MODE = os.getenv("BROWSER_MODE", "normal")  # normal | attach

def is_local_env() -> bool:
    return RUN_ENV == "local"

def is_attach_mode() -> bool:
    # 🔒 attach doit être IMPOSSIBLE hors local
    return is_local_env() and RUN_MODE == "local" and BROWSER_MODE == "attach"
