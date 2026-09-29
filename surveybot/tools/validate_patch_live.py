"""
validate_patch_live.py — Phase 10. Test live attach contrôlé d'un patch
NON_CONCLUANT après la Phase 9 (Survey/autofix/patch_replay.py), sur une VRAIE page déjà
ouverte par un opérateur humain (Chrome lancé avec --remote-debugging-port,
navigué manuellement dans l'état de l'incident) — potentiellement une vraie
session de répondant.

GARDE-FOU DE SÉCURITÉ, NON NÉGOCIABLE : $env:AUTOFIX_LIVE_VALIDATE doit valoir
EXACTEMENT "1". Vérifié EN PREMIER ci-dessous, avant tout parsing d'argument
(avant même d'importer argparse), avant toute tentative de connexion CDP —
aucun argument CLI ne peut jamais contourner ce contrôle. Voir Survey/autofix/
live_validator.py pour le détail complet (garde-fou revérifié indépendamment
par check_preconditions(), stratégie de connexion, budgets, vocabulaire).

Ne lance ni ne configure Chrome : suppose qu'un humain a déjà positionné
manuellement la page dans l'état de l'incident et laissé le port de débogage
distant ouvert. Ne s'exécute que si patch_replay.json (Phase 9) existe pour ce
case avec refused=False et outcome="NON_CONCLUANT" exactement — sinon refus
contrôlé avant tout effet de bord. Lit aussi, en lecture seule, worktree.json
(Phase 7) et diagnosis.json (Phase 4, pour le stage). Toute la logique vit dans
Survey/autofix/live_validator.py ; ce script n'est qu'une façade CLI.

Une seule tentative par invocation : jamais de boucle interne, jamais un
deuxième essai automatique. Pour retenter après une nouvelle correction,
relancer manuellement le cycle complet (nouvelle invocation).

Usage :
    set AUTOFIX_LIVE_VALIDATE=1
    python tools\\validate_patch_live.py failure_cases\\<case_id> diagnoses\\<case_id> autofix_worktrees\\<case_id>\\worktree.json patch_replays\\<case_id>\\patch_replay.json --cdp-endpoint http://localhost:9222
    python tools\\validate_patch_live.py failure_cases\\<case_id> diagnoses\\<case_id> autofix_worktrees\\<case_id>\\worktree.json patch_replays\\<case_id>\\patch_replay.json --cdp-endpoint http://localhost:9222 --budget-s 45 --out-root live_validations --force
"""

from __future__ import annotations

import os
import sys


def main(argv: "list[str] | None" = None) -> int:
    # ── GARDE-FOU DE SÉCURITÉ, NON NÉGOCIABLE ────────────────────────────────
    # Vérifié EN PREMIER, avant tout parsing d'argument applicatif (avant même
    # d'importer argparse), avant toute tentative de connexion CDP. Aucun
    # argument CLI ne peut atteindre ce point s'il n'est pas déjà satisfait.
    if os.environ.get("AUTOFIX_LIVE_VALIDATE") != "1":
        print(
            "[ERREUR] AUTOFIX_LIVE_VALIDATE doit valoir exactement \"1\" pour activer cette phase. "
            "Cette phase se connecte à une VRAIE page, potentiellement une vraie session de "
            "répondant : --remote-debugging-port/tout CDP exposé est un signal de détection "
            "documenté (BOT_EVOLUTION_MEMORY.md). Refus avant tout parsing d'argument et toute "
            "tentative de connexion CDP — aucun argument de cette commande ne peut contourner ce "
            "contrôle.",
            file=sys.stderr,
        )
        return 1

    import argparse
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    from Survey.autofix.live_validator import (  # noqa: E402
        DEFAULT_BUDGET_S,
        LiveValidationError,
        write_live_validation,
    )

    parser = argparse.ArgumentParser(
        description=(
            "Test live attach contrôlé d'un patch NON_CONCLUANT (Phase 9) sur une vraie page déjà "
            "ouverte par un opérateur (CDP) — valide le patch seulement si la validation live "
            "confirme activement la correction (CORRECTIF_CONFIRME)."
        )
    )
    parser.add_argument("failure_case_dir", help="Dossier du failure case (ex: failure_cases\\<case_id>)")
    parser.add_argument("diagnosis_dir", help="Dossier du diagnostic Phase 4 (ex: diagnoses\\<case_id>)")
    parser.add_argument(
        "worktree_manifest",
        help="Chemin de worktree.json produit par la Phase 7 (ex: autofix_worktrees\\<case_id>\\worktree.json)",
    )
    parser.add_argument(
        "patch_replay",
        help="Chemin de patch_replay.json produit par la Phase 9 (ex: patch_replays\\<case_id>\\patch_replay.json)",
    )
    parser.add_argument(
        "--cdp-endpoint",
        required=True,
        help=(
            "Point de connexion CDP explicite fourni par l'opérateur (ex: http://localhost:9222). "
            "Cet outil ne lance ni ne configure Chrome lui-même — un humain a déjà positionné "
            "manuellement la page dans l'état de l'incident et laissé le port ouvert."
        ),
    )
    parser.add_argument(
        "--out-root",
        default="live_validations",
        help="Dossier racine des résultats générés (défaut : live_validations)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Régénère un résultat déjà existant (le supprime avant reconstruction)",
    )
    parser.add_argument(
        "--budget-s",
        type=float,
        default=None,
        help=(
            "Budget de temps (s) explicite sur l'ensemble de l'opération (connexion CDP + rejeu) "
            f"(défaut : {DEFAULT_BUDGET_S})"
        ),
    )
    args = parser.parse_args(argv)

    try:
        out_file = write_live_validation(
            failure_case_dir=args.failure_case_dir,
            diagnosis_dir=args.diagnosis_dir,
            worktree_manifest_path=args.worktree_manifest,
            patch_replay_path=args.patch_replay,
            cdp_endpoint=args.cdp_endpoint,
            out_root=args.out_root,
            force=args.force,
            budget_s=args.budget_s,
        )
    except LiveValidationError as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 1

    import json
    data = json.loads(out_file.read_text(encoding="utf-8"))
    print(f"case               : {data['case_id']}")
    print(f"stage              : {data['stage']}")
    print(f"refused            : {data['refused']}")
    if data["refused"]:
        for reason in data["refusal_reasons"]:
            print(f"  ! {reason}")
    else:
        print(f"patch_replay avant : {data['patch_replay_outcome']}")
        print(f"outcome            : {data['outcome']}")
        print(f"patch_validated    : {data['patch_validated']}")
        if data.get("live_replay_error"):
            print(f"live_replay_error  : {data['live_replay_error']}")
        for warning in data["warnings"]:
            print(f"  ! {warning}")
    print(f"-> {out_file}")

    return 0 if not data["refused"] and data.get("patch_validated") else 1


if __name__ == "__main__":
    sys.exit(main())
