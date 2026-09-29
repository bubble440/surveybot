"""
import_fleet_cases.py — Partie B (importeur), transport fleet des
failure_cases depuis R2 vers la machine de dev. Toute la logique vit dans
Survey/autofix/fleet_case_import.py ; ce script n'est qu'une façade CLI.

Liste les cases disponibles côté stockage partagé et télécharge ceux pas
encore présents localement sous failure_cases/<case_id>/ (même structure que
la Phase 2). Refus explicite si un case_id existe déjà localement, sauf
--force. Écrit fleet_origin.json (jamais manifest.json ni aucun autre
fichier déjà produit par la Phase 2) : machine d'origine, horodatage
d'upload/d'import, live_validation_possible=false.

N'effectue AUCUNE suppression côté stockage partagé ni côté machine
d'origine — le nettoyage prod reste entièrement du ressort de
tools/upload_fleet_cases.py (Partie A2), sur son propre délai de rétention.

Credentials R2 exclusivement depuis l'environnement (mêmes variables que
l'uploader, même bucket partagé) :
  FLEET_R2_ACCOUNT_ID / FLEET_R2_ACCESS_KEY_ID / FLEET_R2_SECRET_ACCESS_KEY /
  FLEET_R2_BUCKET

Usage :
    python tools\\import_fleet_cases.py
    python tools\\import_fleet_cases.py --failure-cases-root failure_cases
    python tools\\import_fleet_cases.py --force
    python tools\\import_fleet_cases.py --download-timeout 120
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Survey.autofix.fleet_case_import import (  # noqa: E402
    DEFAULT_DOWNLOAD_TIMEOUT_S,
    FleetImportError,
    import_available_cases,
)
from Survey.autofix.fleet_case_upload import DEFAULT_PREFIX  # noqa: E402


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Télécharge depuis le stockage R2 partagé les failure_cases pas encore présents "
            "localement (refus explicite si un case_id existe déjà, sauf --force) et écrit "
            "fleet_origin.json pour chacun (machine d'origine, horodatage, "
            "live_validation_possible=false)."
        )
    )
    parser.add_argument(
        "--failure-cases-root", default="failure_cases",
        help="Dossier racine local des failure_cases (défaut : failure_cases)",
    )
    parser.add_argument(
        "--r2-prefix", default=DEFAULT_PREFIX,
        help=f"Préfixe des clés R2 (défaut : {DEFAULT_PREFIX}, doit correspondre à l'uploader)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Écrase un case_id déjà présent localement (défaut : refus explicite, jamais un écrasement silencieux).",
    )
    parser.add_argument(
        "--download-timeout", type=float, default=DEFAULT_DOWNLOAD_TIMEOUT_S,
        help=f"Budget de temps (s) par case téléchargé (défaut : {DEFAULT_DOWNLOAD_TIMEOUT_S})",
    )
    args = parser.parse_args(argv)

    try:
        results, warnings = import_available_cases(
            failure_cases_root=args.failure_cases_root,
            prefix=args.r2_prefix,
            force=args.force,
            budget_s=args.download_timeout,
        )
    except FleetImportError as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 1

    by_status: dict = {}
    for r in results:
        by_status[r.status] = by_status.get(r.status, 0) + 1
        marker = "OK" if r.status == "IMPORTED" else "!!"
        suffix = f" — {r.error}" if r.error else ""
        print(f"  [{marker}] {r.case_id} (machine_id={r.machine_id}) : {r.status} ({r.files} fichier(s)){suffix}")

    print()
    print(f"résumé : {by_status}")

    if warnings:
        print()
        print(f"avertissements ({len(warnings)}) :")
        for w in warnings:
            print(f"  ! {w}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
