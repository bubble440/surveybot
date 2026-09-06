"""
import_accounts.py — Import autonome d'accounts.json

Script indépendant du bot et de son pipeline de build (jamais importé par main.py,
jamais embarqué dans le binaire Nuitka). Prend en entrée un fichier JSON fourni par
l'utilisateur (liste d'objets, un objet par bot) et produit un accounts.json validé
qui REMPLACE ENTIÈREMENT le fichier existant dans le dossier surveybot\ (à côté de
ce script).

Validation indépendante par entrée : une entrée qui contient une clé qui n'est pas
une clé PAR_BOT valide (y compris une clé PAR_RECEPTEUR comme OPENAI_API_KEY, ou une
clé GLOBAL_CONFIG comme DATABASE_URL, qui s'y serait glissée), ou à qui il manque une
clé PAR_BOT obligatoire, est exclue du résultat — les autres entrées valides du même
fichier sont importées normalement.

Usage :
    python tools\\import_accounts.py chemin\\vers\\nouveau_accounts.json

Exemple de fichier d'entrée valide (une seule entrée) :
    [
      {
        "ACCOUNT_ID": "bot_300",
        "EMAIL": "user@example.com",
        "PASSWORD": "s3cr3t",
        "PROXY_URL": "185.134.194.152:12323",
        "PROXY_USER": "14abf236340a1",
        "PROXY_PASS": "bb82a9e63b",
        "profile_dir": "C:\\surveybot\\profiles\\bot_001"
      }
    ]
"""

from __future__ import annotations

import json
import os
import sys

# Clés PAR_BOT valides (propres à chaque bot). profile_dir et CHROME_PROFILE_DIR
# désignent la même information (alias historique) : une seule des deux est exigée.
PAR_BOT_REQUIRED_KEYS = [
    "ACCOUNT_ID", "EMAIL", "PASSWORD",
    "PROXY_URL", "PROXY_USER", "PROXY_PASS",
]
PROFILE_DIR_ALIASES = ["profile_dir", "CHROME_PROFILE_DIR"]
PAR_BOT_VALID_KEYS = set(PAR_BOT_REQUIRED_KEYS) | set(PROFILE_DIR_ALIASES)

# Listes utilisées uniquement pour produire un message d'erreur plus parlant quand une
# clé étrangère à PAR_BOT est détectée (reconnaissance "best effort", non exhaustive).
PAR_RECEPTEUR_KEYS = {
    "OPENAI_API_KEY", "TWO_CAPTCHA_KEY",
    "telegram_bot_token", "telegram_chat_id",
    "payout_name", "payout_revolut_tag",
}
GLOBAL_CONFIG_KEYS = {
    "PLATFORM", "STATE_BACKEND", "STATE_TABLE", "STATE_TTL_DAYS",
    "SURVEY_BROWSER_BIN", "SURVEY_HEADLESS", "SNAP_ENABLED",
    "UPDATE_CHECK_ENABLED", "UPDATE_MANIFEST_URL",
    "LICENSE_KEY", "DATABASE_URL", "BOT_VERSION", "RUN_ENV",
}


def _load_input_file(path: str) -> list:
    """Charge et valide la forme générale du fichier JSON d'entrée. Échoue clairement
    (message explicite sur stderr + exit(1)) si le fichier est absent, illisible, ou
    n'est pas une liste d'objets."""
    if not os.path.isfile(path):
        sys.exit(f"[IMPORT][ERREUR] Fichier introuvable : {path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        sys.exit(f"[IMPORT][ERREUR] Impossible de lire {path} : {e}")
    except json.JSONDecodeError as e:
        sys.exit(f"[IMPORT][ERREUR] JSON invalide dans {path} : {e}")

    if not isinstance(data, list):
        sys.exit(
            f"[IMPORT][ERREUR] {path} doit contenir une liste JSON d'objets "
            f"(un objet par bot) — type reçu : {type(data).__name__}"
        )
    return data


def _key_category(key: str) -> str:
    if key in PAR_RECEPTEUR_KEYS:
        return "PAR_RECEPTEUR"
    if key in GLOBAL_CONFIG_KEYS:
        return "GLOBAL_CONFIG"
    return "inconnue"


def _validate_entry(index: int, entry) -> tuple[dict | None, str | None]:
    """
    Valide une entrée indépendamment des autres.
    Retourne (entree_validee, None) si valide, ou (None, raison_du_rejet) sinon.
    """
    if not isinstance(entry, dict):
        return None, f"l'entrée n'est pas un objet JSON (type reçu : {type(entry).__name__})"

    # Clés non autorisées (y compris PAR_RECEPTEUR / GLOBAL_CONFIG glissées par erreur)
    for key in entry.keys():
        if key not in PAR_BOT_VALID_KEYS:
            category = _key_category(key)
            return None, f"clé non autorisée '{key}' (catégorie {category}, pas PAR_BOT)"

    # Clés PAR_BOT obligatoires
    for req in PAR_BOT_REQUIRED_KEYS:
        if not str(entry.get(req, "")).strip():
            return None, f"clé PAR_BOT obligatoire manquante ou vide : '{req}'"

    # profile_dir / CHROME_PROFILE_DIR : au moins l'une des deux, non vide
    if not any(str(entry.get(alias, "")).strip() for alias in PROFILE_DIR_ALIASES):
        return None, "clé PAR_BOT obligatoire manquante ou vide : 'profile_dir' (ou 'CHROME_PROFILE_DIR')"

    return entry, None


def import_accounts(input_path: str) -> None:
    raw_entries = _load_input_file(input_path)

    valid_entries = []
    rejected = []  # (identifiant, raison)

    for i, entry in enumerate(raw_entries):
        validated, reason = _validate_entry(i, entry)
        if validated is not None:
            valid_entries.append(validated)
        else:
            bot_id = None
            if isinstance(entry, dict):
                bot_id = str(entry.get("ACCOUNT_ID") or "").strip() or None
            identifier = bot_id if bot_id else f"index {i}"
            rejected.append((identifier, reason))

    accounts_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "accounts.json")
    accounts_path = os.path.normpath(accounts_path)

    # Sauvegarde de l'ancien fichier avant remplacement complet (filet de sécurité,
    # accounts.json contient des identifiants/secrets par bot difficiles à reconstituer).
    if os.path.isfile(accounts_path):
        backup_path = accounts_path + ".bak"
        try:
            with open(accounts_path, "r", encoding="utf-8") as src, open(backup_path, "w", encoding="utf-8") as dst:
                dst.write(src.read())
        except OSError as e:
            print(f"[IMPORT][WARN] Sauvegarde de l'ancien accounts.json échouée : {e}")

    with open(accounts_path, "w", encoding="utf-8") as f:
        json.dump(valid_entries, f, indent=2, ensure_ascii=False)
        f.write("\n")

    # Résumé
    print(f"[IMPORT] {len(valid_entries)} bot(s) importé(s) avec succès -> {accounts_path}")
    if rejected:
        print(f"[IMPORT] {len(rejected)} bot(s) rejeté(s) :")
        for identifier, reason in rejected:
            print(f"  - {identifier} : {reason}")
    else:
        print("[IMPORT] Aucun bot rejeté.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage : python tools\\import_accounts.py <chemin_vers_fichier_json>")
    import_accounts(sys.argv[1])
