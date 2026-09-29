"""
check_parallel_safety.py — Partie A du contrôle de sécurité du parallélisme :
AVANT le lancement de Codex, compare au niveau FICHIER les code_files d'au
moins deux context_selection.json (Phase 5, Survey/autofix/context_selector.py) pour
détecter les cases qui viseraient les mêmes fichiers candidats.

Purement en lecture seule, aucun effet de bord sur context_selections/ ni sur
quoi que ce soit d'autre du pipeline ; n'écrit que son propre rapport
horodaté. Ne décide jamais d'un ordre de traitement ni ne résout un conflit —
seulement un rapport pour l'opérateur, qui reste seul décisionnaire. Toute la
logique vit dans Survey/autofix/parallel_safety.py ; ce script n'est qu'une façade
CLI.

Vérification approximative par nature (documenté explicitement dans le
rapport) : à ce stade, Codex n'a pas encore écrit de code — seuls les fichiers
candidats sont connus, jamais les fonctions réellement touchées. Pour une
vérification précise au niveau fonction, une fois les patchs produits, voir
tools/check_function_overlap.py (Partie B).

Usage :
    python tools\\check_parallel_safety.py context_selections\\<case_id_1>\\context_selection.json context_selections\\<case_id_2>\\context_selection.json
    python tools\\check_parallel_safety.py context_selections\\*\\context_selection.json --out-root parallel_safety_checks
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Survey.autofix.parallel_safety import (  # noqa: E402
    ParallelSafetyError,
    write_pre_launch_safety_check,
)


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compare au niveau FICHIER les code_files d'au moins deux context_selection.json "
            "(Phase 5) pour signaler les cases dont le lancement en parallèle viserait les mêmes "
            "fichiers — jamais une décision automatique, seulement un rapport."
        )
    )
    parser.add_argument(
        "context_selections",
        nargs="+",
        help="Au moins 2 chemins vers des context_selection.json (Phase 5)",
    )
    parser.add_argument(
        "--out-root",
        default="parallel_safety_checks",
        help="Dossier racine des rapports générés (défaut : parallel_safety_checks)",
    )
    args = parser.parse_args(argv)

    if len(args.context_selections) < 2:
        parser.error("au moins 2 context_selection.json sont requis pour comparer des paires de cases")

    try:
        out_file = write_pre_launch_safety_check(
            args.context_selections,
            out_root=args.out_root,
        )
    except ParallelSafetyError as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 1

    data = json.loads(out_file.read_text(encoding="utf-8"))
    print(f"niveau de vérification : {data['granularity']} (approximatif — cf. note)")
    print(f"cases comparés          : {data['case_ids']}")
    for case in data["cases"]:
        print(f"  - {case['case_id']} : {len(case['code_files'])} fichier(s) candidat(s)")
    print(f"paires non sûres        : {data['unsafe_pairs_count']}/{len(data['pairs'])}")
    for pair in data["pairs"]:
        if not pair["safe"]:
            print(f"  ! {pair['case_id_a']} <-> {pair['case_id_b']}")
            for f in pair["shared_files"]:
                print(f"      partagé : {f}")
            print(f"      recommandation : {pair['recommendation']}")
    for w in data["warnings"]:
        print(f"  ! {w}")
    print(f"sûr                     : {data['safe']}")
    print(f"-> {out_file}")

    return 0 if data["safe"] else 1


if __name__ == "__main__":
    sys.exit(main())
