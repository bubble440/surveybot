"""
replay_failure.py — Rejoue localement un failure_case (failure_cases/case_<id>/)
contre dom_analyzer.analyze_dom() et le validator concerné, sur le HTML figé du
case — sans navigateur réel, sans dispatch/interaction, sans réseau.

Outil de diagnostic en lecture seule : ne modifie jamais le case ni le snapshot
source, ne tente jamais de corriger quoi que ce soit ni de deviner un résultat
quand l'information manque (annonce NON_REJOUABLE avec la raison dans ce cas).
Toute la logique vit dans Survey/failure_replay.py ; ce script n'est qu'une
façade CLI.

Usage :
    python tools\\replay_failure.py failure_cases\\case_<id>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Survey.failure_replay import (  # noqa: E402
    VERDICT_DIFFERENT,
    VERDICT_NON_REJOUABLE,
    VERDICT_NON_REPRODUIT,
    VERDICT_REPRODUIT,
    replay_failure_case,
)


def _print_result(result) -> None:
    print(f"case        : {result.case_id}")
    print(f"stage       : {result.stage}")
    print(f"verdict     : {result.verdict}")

    if result.verdict == VERDICT_NON_REJOUABLE:
        print(f"raison      : {result.reason}")
        if result.original_failure_types:
            print(f"failure_types (origine) : {result.original_failure_types}")
        return

    print(f"DOM rejoué  : artifacts/{result.dom_file_used}")
    print(f"blocs extraits (replay) : {result.replayed_blocks_count}")
    print(f"failure_types origine   : {result.original_failure_types}")
    print(f"failure_types replay    : {result.replayed_failure_types}")
    print(f"evaluate() honorés statiquement / déclinés : {result.evaluate_handled} / {result.evaluate_declined}")
    for w in result.warnings:
        print(f"  ! {w}")


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Rejoue localement un failure_case (extraction + validator concerné) sur "
            "le HTML figé du case, sans navigateur réel — diagnostic en lecture seule."
        )
    )
    parser.add_argument("case_dir", help="Dossier du case (ex: failure_cases\\case_20260907_142347_action_validation_failure)")
    args = parser.parse_args(argv)

    result = replay_failure_case(args.case_dir)
    _print_result(result)

    return 0 if result.verdict in (VERDICT_REPRODUIT, VERDICT_NON_REPRODUIT, VERDICT_DIFFERENT) else 1


if __name__ == "__main__":
    sys.exit(main())
