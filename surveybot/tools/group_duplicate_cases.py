"""
group_duplicate_cases.py — Regroupe les failure_cases déjà diagnostiqués
(Phase 4) mais pas encore engagés en Phase 5 (context_selection.json absent)
qui partagent une signature de diagnostic IDENTIQUE (modules_likely_involved :
même module + même ensemble de matched_signals), pour éviter de corriger N
fois le même bug déjà identifié.

Outil additif, hors des Phases 1A-17 numérotées : ne modifie jamais
failure_cases/ ni diagnoses/ des cases existants, ne touche à aucun
extracteur/validator/dispatcher. Toute la logique vit dans
Survey/case_grouping.py ; ce script n'est qu'une façade CLI.

Écrit, pour chaque groupe de taille >= 2 formé :
  - failure_cases/<group_id>/ (manifest.json + artifacts/, copie du case
    représentatif, case_id remplacé par group_id)
  - diagnoses/<group_id>/diagnosis.json (copie du diagnostic du représentant,
    case_id remplacé par group_id)
  - failure_cases/<group_id>/group_members.json (traçabilité : membres,
    représentant, signature partagée)
et un instantané horodaté sous out-root/<horodatage>/grouping_report.json
(jamais un fichier unique écrasé — cet outil est fait pour tourner à
répétition à mesure que de nouveaux cases sont diagnostiqués).

Usage :
    python tools\\group_duplicate_cases.py
    python tools\\group_duplicate_cases.py --diagnoses-root diagnoses --failure-cases-root failure_cases
    python tools\\group_duplicate_cases.py --context-selections-root context_selections
    python tools\\group_duplicate_cases.py --out-root case_groupings
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Survey.case_grouping import (  # noqa: E402
    CaseGroupingError,
    write_case_groups,
)


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Regroupe les failure_cases diagnostiqués (Phase 4) partageant une signature "
            "identique (modules_likely_involved) et pas encore engagés en Phase 5, en "
            "failure_case + diagnosis.json synthétiques consommables tels quels par les "
            "Phases 5 à 16."
        )
    )
    parser.add_argument(
        "--diagnoses-root",
        default="diagnoses",
        help="Dossier racine des diagnostics Phase 4 (défaut : diagnoses)",
    )
    parser.add_argument(
        "--failure-cases-root",
        default="failure_cases",
        help="Dossier racine des failure_cases Phase 2 (défaut : failure_cases)",
    )
    parser.add_argument(
        "--context-selections-root",
        default="context_selections",
        help="Dossier racine des sélections de contexte Phase 5, pour exclure les cases déjà "
        "engagés individuellement (défaut : context_selections)",
    )
    parser.add_argument(
        "--out-root",
        default="case_groupings",
        help="Dossier racine des instantanés de rapport générés (défaut : case_groupings)",
    )
    args = parser.parse_args(argv)

    try:
        result, out_file = write_case_groups(
            diagnoses_root=args.diagnoses_root,
            failure_cases_root=args.failure_cases_root,
            context_selections_root=args.context_selections_root,
            report_root=args.out_root,
        )
    except CaseGroupingError as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 1

    data = json.loads(out_file.read_text(encoding="utf-8"))

    print(f"généré              : {data['generated_at']}")
    print(f"cases candidats     : {data['candidates_considered']} (diagnostiqués, pas encore en Phase 5)")
    print()

    groups = data["groups"]
    print(f"groupes formés ({len(groups)}) :")
    for g in groups:
        sig = g["signature"]
        print(f"  - {g['group_id']}  ({len(g['members'])} membre(s), représentant={g['representative_case_id']})")
        print(f"      module          : {sig['module']}")
        print(f"      matched_signals : {sig['matched_signals']}")
        print(f"      membres         : {g['members']}")
    if not groups:
        print("  (aucun)")

    print()
    print(f"cases solo ({len(data['solo_case_ids'])}) : {data['solo_case_ids']}")

    ambiguous = data["ambiguous_cases"]
    print()
    print(f"cases exclus pour ambiguïté ({len(ambiguous)}) :")
    for a in ambiguous:
        print(f"  - {a['case_id']} : {a['conflicting_signatures']}")
    if not ambiguous:
        print("  (aucun)")

    if data["warnings"]:
        print()
        print(f"avertissements ({len(data['warnings'])}) :")
        for w in data["warnings"]:
            print(f"  ! {w}")

    print()
    print(f"-> {out_file}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
