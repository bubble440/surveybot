"""
collect_cookies.py — Collecte et persistence des cookies depuis un Chrome local.

Usage :
    # Collecte depuis Chrome + sauvegarde Postgres + backup fichier
    python collect_cookies.py [--port 9222] [--account-id topsurveys_test_prod_like]

    # Aperçu sans écriture
    python collect_cookies.py --dry-run

    # Restaurer Postgres depuis le backup fichier (après suppression accidentelle)
    python collect_cookies.py --restore-from-backup --account-id topsurveys_test_prod_like

Prérequis :
    - Chrome lancé via attach_tab.ps1 (proxy actif, --remote-debugging-port=PORT)
    - Avoir navigué manuellement sur les domaines survey cibles
    - DATABASE_URL dans l'environnement (ou .env local)
    - pip install websocket-client requests psycopg2-binary

Ce que fait ce script :
    1. Se connecte au Chrome debug sur le port indiqué
    2. Récupère tous les cookies via CDP (Network.getAllCookies)
    3. Filtre les domaines pertinents (exclut Google, CDN, pub, etc.)
    4. Persiste dans Postgres (table cookie_store) — upsert par (account_id, domain)
    5. Écrit un backup JSON local : cookies_backup/{account_id}.json
    6. Affiche un résumé par domaine
"""

import os
import sys
import json
import argparse
import requests
from datetime import datetime, timezone
from pathlib import Path

# ── Charger .env local si présent (pratique pour DATABASE_URL) ─────────────
_env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.isfile(_env_path):
    with open(_env_path, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip())

# ── Répertoire backup local ─────────────────────────────────────────────────
# Un fichier JSON par account_id, à côté de ce script.
# Modifiable via --backup-dir.
_DEFAULT_BACKUP_DIR = Path(__file__).parent / "cookies_backup"

# ── Domaines à exclure (bruit — pub, CDN, analytics) ───────────────────────
_EXCLUDE_DOMAIN_FRAGMENTS = [
    "google", "gstatic", "googleapis", "doubleclick", "googletagmanager",
    "facebook", "fbcdn", "twitter", "linkedin", "tiktok",
    "cloudflare", "cloudfront", "akamai", "fastly",
    "youtube", "ytimg",
    "amazon", "amazonaws",
    "newrelic", "segment", "mixpanel", "hotjar", "intercom",
    "chartbeat", "quantserve", "scorecardresearch",
    "topsurveys",          # cookies session TopSurveys — pas utiles pour anti-détection survey
]

# ── Domaines à inclure explicitement (même s'ils contiennent un fragment exclu) ──
_FORCE_INCLUDE_DOMAINS = [
    "captcha-delivery.com",   # DataDome
]


def _should_keep_domain(domain: str) -> bool:
    """Retourne True si ce domaine vaut la peine d'être persisté."""
    d = domain.lower().lstrip(".")
    for forced in _FORCE_INCLUDE_DOMAINS:
        if forced in d:
            return True
    for excl in _EXCLUDE_DOMAIN_FRAGMENTS:
        if excl in d:
            return False
    return True


def _get_open_tabs(port: int) -> list[dict]:
    """Retourne la liste des onglets ouverts via Chrome DevTools Protocol."""
    try:
        r = requests.get(f"http://127.0.0.1:{port}/json", timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[ERROR] Impossible de contacter Chrome sur le port {port}: {e}")
        print("        → Vérifie que Chrome est lancé avec --remote-debugging-port")
        sys.exit(1)


def _collect_cookies_via_cdp(port: int) -> list[dict]:
    """
    Collecte tous les cookies via l'endpoint /json/version (Network.getAllCookies).
    Utilise le premier onglet disponible comme point d'entrée CDP.
    """
    import websocket  # websocket-client

    tabs = _get_open_tabs(port)
    if not tabs:
        print("[ERROR] Aucun onglet ouvert dans Chrome.")
        sys.exit(1)

    # Prendre le premier onglet de type "page"
    tab = next((t for t in tabs if t.get("type") == "page"), tabs[0])
    ws_url = tab.get("webSocketDebuggerUrl")
    if not ws_url:
        print("[ERROR] Pas de webSocketDebuggerUrl sur l'onglet.")
        sys.exit(1)

    print(f"[CDP] Connexion à {ws_url[:60]}...")

    all_cookies = []
    try:
        ws = websocket.create_connection(ws_url, timeout=10)
        # Network.getAllCookies retourne tous les cookies de toutes les origines
        ws.send(json.dumps({"id": 1, "method": "Network.getAllCookies"}))
        response = json.loads(ws.recv())
        ws.close()
        all_cookies = response.get("result", {}).get("cookies", [])
    except Exception as e:
        print(f"[ERROR] CDP WebSocket échoué: {e}")
        sys.exit(1)

    print(f"[CDP] {len(all_cookies)} cookie(s) collectés au total")
    return all_cookies


def _group_by_domain(cookies: list[dict]) -> dict[str, list[dict]]:
    """Groupe les cookies par domaine, après filtrage."""
    groups: dict[str, list[dict]] = {}
    for c in cookies:
        domain = c.get("domain", "").lstrip(".")
        if not domain:
            continue
        if not _should_keep_domain(domain):
            continue
        groups.setdefault(domain, []).append(c)
    return groups


def _backup_to_file(account_id: str, grouped: dict[str, list[dict]], backup_dir: Path) -> Path:
    """
    Écrit un backup JSON local : backup_dir/{account_id}.json
    Format : {"account_id": ..., "collected_at": ..., "domains": {domain: [cookies]}}
    Écrase le fichier précédent (même logique upsert que Postgres).
    Retourne le chemin du fichier écrit.
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{account_id}.json"
    payload = {
        "account_id": account_id,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "domains": grouped,
    }
    backup_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return backup_path


def _restore_from_backup(account_id: str, backup_dir: Path) -> dict[str, list[dict]]:
    """
    Charge le backup fichier pour un account_id.
    Retourne grouped {domain: [cookies]} ou {} si fichier absent.
    """
    backup_path = backup_dir / f"{account_id}.json"
    if not backup_path.is_file():
        print(f"[ERROR] Backup introuvable : {backup_path}")
        sys.exit(1)
    try:
        payload = json.loads(backup_path.read_text(encoding="utf-8"))
        grouped = payload.get("domains", {})
        collected_at = payload.get("collected_at", "?")
        print(f"[BACKUP] Fichier chargé : {backup_path}")
        print(f"[BACKUP] Collecté le   : {collected_at}")
        print(f"[BACKUP] Domaines      : {len(grouped)}")
        return grouped
    except Exception as e:
        print(f"[ERROR] Lecture backup échouée : {e}")
        sys.exit(1)


def _save_to_postgres(account_id: str, grouped: dict[str, list[dict]]) -> int:
    """
    Persiste les cookies groupés par domaine dans Postgres.
    Utilise une table dédiée `cookie_store` (séparée de account_state).
    Crée la table si elle n'existe pas.
    Retourne le nombre de domaines sauvegardés.
    """
    database_url = os.getenv("DATABASE_URL", "postgres://postgres:3o1L6kfCFxuncbY@localhost:5432/postgres").strip()
    if not database_url:
        print("[ERROR] DATABASE_URL non défini. Impossible de sauvegarder.")
        sys.exit(1)

    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        print("[ERROR] psycopg2 non disponible. pip install psycopg2-binary")
        sys.exit(1)

    conn = psycopg2.connect(database_url)
    conn.autocommit = False

    try:
        with conn.cursor() as cur:
            # Création table dédiée (idempotent)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS cookie_store (
                    account_id  TEXT        NOT NULL,
                    domain      TEXT        NOT NULL,
                    cookies     JSONB       NOT NULL DEFAULT '[]',
                    collected_at TIMESTAMPTZ DEFAULT now(),
                    PRIMARY KEY (account_id, domain)
                )
            """)

        saved = 0
        now_iso = datetime.now(timezone.utc).isoformat()

        for domain, cookies in grouped.items():
            # Sérialiser uniquement les champs utiles (réduire le volume)
            slim = [
                {
                    "name":     c.get("name", ""),
                    "value":    c.get("value", ""),
                    "path":     c.get("path", "/"),
                    "secure":   c.get("secure", False),
                    "httpOnly": c.get("httpOnly", False),
                    "expires":  c.get("expires", -1),
                    "sameSite": c.get("sameSite", ""),
                }
                for c in cookies
                if c.get("name") and c.get("value")
            ]
            if not slim:
                continue

            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO cookie_store (account_id, domain, cookies, collected_at)
                    VALUES (%s, %s, %s::jsonb, now())
                    ON CONFLICT (account_id, domain)
                    DO UPDATE SET cookies = EXCLUDED.cookies, collected_at = now()
                """, (account_id, domain, json.dumps(slim)))
            saved += 1

        conn.commit()
        return saved

    except Exception as e:
        conn.rollback()
        print(f"[ERROR] Postgres: {e}")
        sys.exit(1)
    finally:
        conn.close()


def _print_summary(grouped: dict[str, list[dict]], saved: int) -> None:
    print()
    print(f"{'─'*55}")
    print(f"  Résumé — {saved} domaine(s) sauvegardés")
    print(f"{'─'*55}")
    for domain, cookies in sorted(grouped.items()):
        names = [c.get("name", "?") for c in cookies[:5]]
        extra = f" (+{len(cookies)-5} autres)" if len(cookies) > 5 else ""
        print(f"  {domain:<35} {len(cookies):>3} cookie(s)  [{', '.join(names)}{extra}]")
    print(f"{'─'*55}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Collecte les cookies du Chrome local et les persiste en Postgres."
    )
    parser.add_argument(
        "--port", type=int, default=9222,
        help="Port de debug Chrome (défaut: 9222)"
    )
    parser.add_argument(
        "--account-id", type=str,
        default=os.getenv("ACCOUNT_ID", "topsurveys_test_prod_like"),
        help="account_id cible en Postgres (défaut: $ACCOUNT_ID ou topsurveys_test_prod_like)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Affiche les cookies collectés sans écrire en Postgres ni en fichier"
    )
    parser.add_argument(
        "--backup-dir", type=str, default=str(_DEFAULT_BACKUP_DIR),
        help=f"Répertoire des backups JSON (défaut: {_DEFAULT_BACKUP_DIR})"
    )
    parser.add_argument(
        "--restore-from-backup", action="store_true",
        help="Restaure Postgres depuis le backup fichier local (sans Chrome)"
    )
    args = parser.parse_args()

    backup_dir = Path(args.backup_dir)

    print()
    print(f"  collect_cookies.py")
    print(f"  Account ID   : {args.account_id}")
    print(f"  Backup dir   : {backup_dir}")
    if not args.restore_from_backup:
        print(f"  Port Chrome  : {args.port}")
    print(f"  Dry run      : {args.dry_run}")
    print()

    # ── Mode restauration depuis backup ────────────────────────────────────
    if args.restore_from_backup:
        grouped = _restore_from_backup(args.account_id, backup_dir)
        if not grouped:
            print("[WARN] Backup vide — rien à restaurer.")
            sys.exit(0)
        _print_summary(grouped, len(grouped))
        if args.dry_run:
            print("[DRY RUN] Aucune écriture en Postgres.")
            sys.exit(0)
        saved = _save_to_postgres(args.account_id, grouped)
        print(f"[OK] {saved} domaine(s) restaurés en Postgres pour account_id={args.account_id}")
        print()
        return

    # ── Mode collecte depuis Chrome ─────────────────────────────────────────
    # 1. Collecter via CDP
    cookies = _collect_cookies_via_cdp(args.port)

    # 2. Grouper + filtrer
    grouped = _group_by_domain(cookies)
    print(f"[FILTER] {len(grouped)} domaine(s) retenus après filtrage")

    if not grouped:
        print("[WARN] Aucun cookie pertinent trouvé.")
        print("       → Navigue sur les domaines survey cibles puis relance.")
        sys.exit(0)

    # 3. Afficher un aperçu
    _print_summary(grouped, len(grouped))

    if args.dry_run:
        print("[DRY RUN] Aucune écriture en Postgres ni en fichier.")
        sys.exit(0)

    # 4. Backup fichier local (avant Postgres — si Postgres échoue, le backup est déjà là)
    backup_path = _backup_to_file(args.account_id, grouped, backup_dir)
    print(f"[BACKUP] Fichier écrit : {backup_path}")

    # 5. Sauvegarder en Postgres
    saved = _save_to_postgres(args.account_id, grouped)
    print(f"[OK] {saved} domaine(s) sauvegardés en Postgres pour account_id={args.account_id}")
    print()


if __name__ == "__main__":
    main()

