"""
global_config.py

Constantes globales SurveyBot — communes à TOUTES les instances du parc bare-metal.

Ce fichier est l'équivalent, pour la configuration globale, de ce que _license_config.py
est pour la licence : un module Python compilé en dur dans le binaire (Nuitka), jamais lu
depuis un fichier externe modifiable. Un récepteur qui édite ce fichier après compilation
n'a aucun effet, puisque le binaire ré-exécute la version figée au moment du build.

RÈGLE D'USAGE OBLIGATOIRE POUR LE CODE APPELANT :
Le code qui consomme une variable listée ici doit importer et lire la constante
directement (`from global_config import OPENAI_API_KEY`), SANS jamais passer par
os.getenv("OPENAI_API_KEY", ...) en fallback. Un fallback vers l'environnement
annulerait totalement la protection : n'importe qui pourrait redéfinir la variable
d'environnement avant de lancer le bot pour écraser la valeur compilée.

Ne jamais confondre avec les variables PAR_BOT (accounts.json) : ACCOUNT_ID, EMAIL,
PASSWORD, PROXY_URL, PROXY_USER, PROXY_PASS, profile_dir/CHROME_PROFILE_DIR,
payout_name, payout_revolut_tag. Celles-ci doivent continuer à être lues depuis
l'environnement du process — c'est leur mécanisme de transmission légitime, propre
à chaque bot sur chaque machine.

Ne pas mettre ici : LICENSE_KEY, DATABASE_URL, BOT_VERSION (restent dans
_license_config.py — raison distincte : résistance à la modification persistante
pour la vérification de licence, cf. fichier de suivi section 3).

Fichier non versionné (mêmes règles que _license_config.py) — à remplir avec les
vraies valeurs juste avant chaque build, jamais committé avec des secrets réels.
"""

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION GLOBALE — constante entre toutes les instances
# ─────────────────────────────────────────────────────────────────────────────

RUN_ENV = "prod"
PLATFORM_ROTATION = ["topsurveys", "primeopinion", "heycash", "earnstar", "fivesurveys"]

# Objectif de gain journalier par plateforme, DANS L'UNITÉ NATIVE de son solde
# affiché (celle que sa fonction de lecture de solde retourne déjà) — aucune
# conversion €/$/Points n'est appliquée nulle part : topsurveys/heycash/
# fivesurveys sont en euros, earnstar/ysense en dollars (cf. Cash/ysense_balance.py
# ::_read_balance_usd et platforms/earnstar.py::_parse_amount), primeopinion en
# points (cf. Cash/primeopinion_balance.py::_read_points_balance). Toute
# plateforme de PLATFORM_ROTATION doit avoir une entrée ici — validé au
# démarrage par platforms.validate_platform_daily_targets(), jamais de repli
# implicite.
PLATFORM_DAILY_TARGET = {
    "topsurveys": 0.5,
    "primeopinion": 60.0,
    "heycash": 0.5,
    "earnstar": 0.6,
    "fivesurveys": 0.5,
    "ysense": 0.6,
}

STATE_BACKEND = "postgres"
STATE_TABLE = "account_state"
STATE_TTL_DAYS = "0"

SURVEY_BROWSER_BIN = ""          # vide = détection automatique du binaire Chrome
SURVEY_HEADLESS = "0"          # 0 = visible, 1 = headless

LOG_STEP_SUMMARY = "0"

# Interrupteur critique — confirmé à 0 en prod (clics CTA réels, pas d'interception).
CTA_INTERCEPT_ONLY = "0"

CAPTCHA_PROVIDER = "2captcha"
SNAP_ENABLED = "0"

UPDATE_CHECK_ENABLED = "1"
UPDATE_MANIFEST_URL = "https://pub-565d2bb59d364c1490255c5dddc296aa.r2.dev/manifest.json"