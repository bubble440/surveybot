"""
report_extractor_stability.py — registre de stabilité prouvée des fonctions
gelées (Survey/extractor_integrity.json, Phase 11-A), calculé à partir de
l'historique Git réel de ce fichier et de diagnoses/ (Phase 4).

Pour chaque fonction actuellement enregistrée : depuis quel commit son hash
actuel est en vigueur, combien de valeurs de hash distinctes elle a jamais
portées, et combien de cases diagnostiqués (Phase 4) l'ont citée dans
modules_likely_involved DEPUIS cette dernière modification (les incidents
plus anciens ne concernent plus la version actuelle du code, ignorés).
Jamais un pourcentage de réussite (aucune source ne les compte, cf.
Survey/autofix_metrics.py) — seulement des faits comptables et datés.

Purement en lecture seule : ne modifie jamais BOT_EVOLUTION_MEMORY.md,
Survey/extractor_integrity.json, ni aucun artefact d'une phase existante.
Toute la logique vit dans Survey/extractor_stability.py ; ce script n'est
qu'une façade CLI.

Écrit un instantané horodaté (jamais un fichier écrasé) sous
out-root/<horodatage>/stability_report.json et imprime un résumé lisible
(fonctions les plus instables depuis leur dernière modification en tête).

Usage :
    python tools\\report_extractor_stability.py
    python tools\\report_extractor_stability.py --diagnoses-root diagnoses --out-root extractor_stability_reports
    python tools\\report_extractor_stability.py --git-timeout 30
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Survey.extractor_stability import (  # noqa: E402
    DEFAULT_GIT_TIMEOUT_S,
    ExtractorStabilityError,
    write_stability_report,
)


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Calcule un instantané de stabilité prouvée des fonctions gelées (Survey/"
            "extractor_integrity.json) à partir de l'historique Git réel et de diagnoses/ (Phase 4) — "
            "purement en lecture seule, jamais un pourcentage de réussite."
        )
    )
    parser.add_argument(
        "--diagnoses-root", default="diagnoses",
        help="Dossier racine des diagnostics Phase 4 (défaut : diagnoses)",
    )
    parser.add_argument(
        "--out-root", default="extractor_stability_reports",
        help="Dossier racine des instantanés générés (défaut : extractor_stability_reports)",
    )
    parser.add_argument(
        "--git-timeout", type=float, default=DEFAULT_GIT_TIMEOUT_S,
        help=f"Budget de temps (secondes) par commande git (défaut : {DEFAULT_GIT_TIMEOUT_S})",
    )
    args = parser.parse_args(argv)

    try:
        out_file = write_stability_report(
            diagnoses_root=args.diagnoses_root,
            out_root=args.out_root,
            git_timeout_s=args.git_timeout,
        )
    except ExtractorStabilityError as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 1

    data = json.loads(out_file.read_text(encoding="utf-8"))
    print(f"registre         : {data['registry_path']}")
    print(f"entrées          : {data['total_entries']}")
    print()

    for entry in data["entries"]:
        if not entry["history_exploitable"]:
            print(f"  ? {entry['key']:<60s} historique non exploitable : {entry['history_reason']}")
            continue
        print(
            f"  {entry['incidents_since_stable']:>3d} incident(s) depuis {entry['stable_since_date']} : "
            f"{entry['key']} (changements historiques : {entry['changes_count']}"
            + (", trou dans l'historique" if entry["had_gap_in_history"] else "")
            + ")"
        )

    if data["warnings"]:
        print()
        print(f"avertissements ({len(data['warnings'])}) :")
        for w in data["warnings"][:20]:
            print(f"  ! {w}")
        if len(data["warnings"]) > 20:
            print(f"  ... et {len(data['warnings']) - 20} de plus (voir {out_file})")

    print()
    print(f"-> {out_file}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
