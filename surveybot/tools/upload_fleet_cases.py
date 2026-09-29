"""
upload_fleet_cases.py — Partie A (uploader), transport fleet des
failure_cases vers R2. Tourne sur CHAQUE machine de production. Toute la
logique vit dans Survey/autofix/fleet_case_upload.py ; ce script n'est qu'une façade
CLI.

Sous-partie A1 : upload des cases pas encore marqués "uploadé et vérifié"
(marqueur local dédié fleet_upload_state.json), avec vérification positive
(head_object) avant tout marquage.

Sous-partie A2 : nettoyage local différé des cases déjà uploadés et vérifiés
dont le marqueur dépasse --retention-days. --no-cleanup désactive entièrement
cette sous-partie.

Credentials R2 exclusivement depuis l'environnement (jamais en dur) :
  FLEET_R2_ACCOUNT_ID / FLEET_R2_ACCESS_KEY_ID / FLEET_R2_SECRET_ACCESS_KEY /
  FLEET_R2_BUCKET

Usage :
    python tools\\upload_fleet_cases.py
    python tools\\upload_fleet_cases.py --failure-cases-root failure_cases
    python tools\\upload_fleet_cases.py --machine-id bot17
    python tools\\upload_fleet_cases.py --retention-days 14
    python tools\\upload_fleet_cases.py --no-cleanup
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Survey.autofix.fleet_case_upload import (  # noqa: E402
    DEFAULT_PREFIX,
    DEFAULT_RETENTION_DAYS,
    DEFAULT_UPLOAD_TIMEOUT_S,
    FleetUploadError,
    cleanup_verified_cases,
    upload_pending_cases,
)


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Upload des failure_cases (Phase 2) pas encore transportés vers le stockage R2 "
            "partagé, avec vérification positive (head_object) avant tout marquage local "
            "'uploadé et vérifié', puis nettoyage local optionnel des cases déjà uploadés et "
            "vérifiés au-delà d'un délai de rétention configurable."
        )
    )
    parser.add_argument(
        "--failure-cases-root", default="failure_cases",
        help="Dossier racine local des failure_cases (défaut : failure_cases)",
    )
    parser.add_argument(
        "--machine-id", default=None,
        help=(
            "Identifiant de machine/bot à associer aux cases uploadés (défaut : résolu via "
            "Management.guards.runtime_guard, repli 'unknown' si le guard n'est pas initialisé)"
        ),
    )
    parser.add_argument(
        "--r2-prefix", default=DEFAULT_PREFIX,
        help=f"Préfixe des clés R2 (défaut : {DEFAULT_PREFIX})",
    )
    parser.add_argument(
        "--upload-timeout", type=float, default=DEFAULT_UPLOAD_TIMEOUT_S,
        help=f"Budget de temps (s) par case, upload + vérification (défaut : {DEFAULT_UPLOAD_TIMEOUT_S})",
    )
    parser.add_argument(
        "--no-cleanup", action="store_true",
        help="Désactive entièrement le nettoyage local différé (sous-partie A2).",
    )
    parser.add_argument(
        "--retention-days", type=float, default=DEFAULT_RETENTION_DAYS,
        help=(
            "Délai de rétention (jours) après vérification avant suppression locale d'un case "
            f"déjà uploadé et vérifié (défaut : {DEFAULT_RETENTION_DAYS} jours)"
        ),
    )
    args = parser.parse_args(argv)

    try:
        upload_result = upload_pending_cases(
            failure_cases_root=args.failure_cases_root,
            machine_id=args.machine_id,
            prefix=args.r2_prefix,
            budget_s=args.upload_timeout,
        )
    except FleetUploadError as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 1

    print(f"machine_id : {upload_result.machine_id}")
    print(f"candidats  : {upload_result.candidates}")
    by_status: dict = {}
    for r in upload_result.results:
        by_status[r.status] = by_status.get(r.status, 0) + 1
        marker = "OK" if r.status == "UPLOADED_VERIFIED" else "!!"
        suffix = f" — {r.error}" if r.error else ""
        print(f"  [{marker}] {r.case_id} : {r.status} ({r.files} fichier(s)){suffix}")

    print()
    print(f"résumé : {by_status}")

    if upload_result.warnings:
        print()
        print(f"avertissements ({len(upload_result.warnings)}) :")
        for w in upload_result.warnings:
            print(f"  ! {w}")

    if args.no_cleanup:
        print()
        print("nettoyage local désactivé (--no-cleanup)")
        return 0

    try:
        cleanup_results = cleanup_verified_cases(
            failure_cases_root=args.failure_cases_root,
            retention_days=args.retention_days,
        )
    except FleetUploadError as exc:
        print(f"[ERREUR nettoyage] {exc}", file=sys.stderr)
        return 1

    removed = [r for r in cleanup_results if r.removed]
    print()
    print(f"nettoyage (rétention {args.retention_days}j) : {len(removed)} case(s) supprimé(s)")
    for r in removed:
        print(f"  - {r.case_id} (uploaded_at={r.uploaded_at}, verified_at={r.verified_at})")

    failed = [r for r in cleanup_results if not r.removed]
    if failed:
        print(f"échecs de suppression ({len(failed)}) :")
        for r in failed:
            print(f"  ! {r.case_id} : {r.reason}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
