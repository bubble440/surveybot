#!/usr/bin/env python3
"""Inventaire BDL-1 des question_blocks capturés.

Outil en lecture seule : ne lance pas de navigateur, ne modifie pas SurveyBot
et ne déduit pas la vérité terrain à partir des sorties historiques.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

KNOWN_ITYPES = {
    "radio", "checkbox", "dropdown", "text", "textarea",
    "matrix", "number", "select", "select_rps", "button",
}


def _extract_blocks(payload: Any) -> list[dict[str, Any]]:
    """Accepte une liste de blocs ou un objet enveloppe connu."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    for key in ("question_blocks", "blocks"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    # Certains artefacts enveloppent les données dans une clé "data".
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("question_blocks", "blocks"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _audit_block(block: dict[str, Any], source: str, index: int) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []

    raw_itype = block.get("itype")
    itype = str(raw_itype or "").strip().lower()
    target_id = str(block.get("target_id") or "").strip()
    options = block.get("options", [])

    def issue(code: str, detail: str) -> None:
        issues.append({
            "source": source,
            "block_index": index,
            "itype": itype or None,
            "target_id": target_id or None,
            "code": code,
            "detail": detail,
        })

    if not itype:
        issue("missing_itype", "Champ itype absent ou vide.")
    elif itype not in KNOWN_ITYPES:
        issue("undocumented_itype", "Type non couvert par le contrat BDL-1 actuel.")

    if not target_id:
        issue("missing_target_id", "Bloc sans target_id.")

    if "options" in block and not isinstance(options, list):
        issue("options_not_list", "Le champ options existe mais n'est pas une liste.")
        options = []

    if isinstance(options, list):
        normalized = [
            " ".join(str(option or "").split()).casefold()
            for option in options
            if " ".join(str(option or "").split())
        ]
        if itype in {"radio", "checkbox", "dropdown"} and not normalized:
            issue(
                "choice_without_options",
                "Bloc de choix sans options non vides ; vérifier le DOM et le pattern avant de conclure.",
            )
        if len(normalized) != len(set(normalized)):
            issue(
                "duplicate_options",
                "Options dupliquées après normalisation ; vérifier les widgets et libellés répétés.",
            )

    def positive_int(key: str) -> int | None:
        value = block.get(key)
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            issue("invalid_" + key, f"{key} n'est pas convertible en entier.")
            return None

    min_select = positive_int("min_select")
    max_select = positive_int("max_select")
    if min_select is not None and max_select is not None:
        if min_select > 0 and max_select > 0 and min_select > max_select:
            issue("invalid_selection_limits", "min_select dépasse max_select.")

    if (
        itype == "checkbox"
        and max_select is not None
        and max_select > 0
        and isinstance(options, list)
        and options
        and max_select > len(options)
    ):
        issue(
            "max_select_exceeds_options",
            "max_select dépasse le nombre brut d'options ; vérifier les options réellement répondables.",
        )

    return issues


def _candidate_files(inputs: list[Path]) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    errors: list[str] = []
    for path in inputs:
        if not path.exists():
            errors.append(f"Chemin introuvable : {path}")
            continue
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(sorted(path.rglob("question_blocks.json")))
        else:
            errors.append(f"Chemin non pris en charge : {path}")
    unique = list(dict.fromkeys(p.resolve() for p in files))
    return unique, errors


def build_report(inputs: list[Path]) -> dict[str, Any]:
    files, path_errors = _candidate_files(inputs)
    type_counts: Counter[str] = Counter()
    per_file: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    blocks_total = 0
    unreadable_files: list[dict[str, str]] = []

    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            unreadable_files.append({"path": str(path), "error": str(exc)})
            continue

        blocks = _extract_blocks(payload)
        # Si le fichier ressemble à une liste de blocs mais contient des entrées
        # non-dictionnaires, les signaler explicitement au lieu de les ignorer.
        if isinstance(payload, list):
            for index, item in enumerate(payload):
                if not isinstance(item, dict):
                    issues.append({
                        "source": str(path), "block_index": index,
                        "itype": None, "target_id": None,
                        "code": "invalid_block_shape",
                        "detail": "Entrée de la liste non représentée par un dictionnaire.",
                    })

        local_counts: Counter[str] = Counter()
        for index, block in enumerate(blocks):
            itype = str(block.get("itype") or "").strip().lower() or "<missing>"
            local_counts[itype] += 1
            type_counts[itype] += 1
            issues.extend(_audit_block(block, str(path), index))
        blocks_total += len(blocks)
        per_file.append({
            "path": str(path),
            "blocks_count": len(blocks),
            "itype_counts": dict(sorted(local_counts.items())),
        })

        if not blocks:
            issues.append({
                "source": str(path), "block_index": None,
                "itype": None, "target_id": None,
                "code": "no_blocks_found",
                "detail": "Aucun tableau question_blocks/blocks reconnu dans cet artefact.",
            })

    return {
        "contract_version": "BDL-1-v0.4",
        "known_itypes": sorted(KNOWN_ITYPES),
        "files_scanned": len(per_file),
        "blocks_scanned": blocks_total,
        "itype_counts": dict(sorted(type_counts.items())),
        "files": per_file,
        "issues_count": len(issues),
        "issues": issues,
        "unreadable_files": unreadable_files,
        "path_errors": path_errors,
        "interpretation": (
            "Inventaire structurel des snapshots uniquement ; ne constitue pas "
            "une validation sémantique ni une vérité terrain."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths", nargs="+", type=Path,
        help="Fichiers JSON de blocs ou dossiers contenant question_blocks.json.",
    )
    parser.add_argument("--output", type=Path, help="Chemin du rapport JSON.")
    parser.add_argument(
        "--strict", action="store_true",
        help="Retourne le code 1 si une anomalie est détectée.",
    )
    args = parser.parse_args()

    report = build_report(args.paths)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(
        f"BDL-1 : {report['files_scanned']} fichier(s), "
        f"{report['blocks_scanned']} bloc(s), "
        f"{len(report['itype_counts'])} type(s), "
        f"{report['issues_count']} signalement(s)."
    )
    print("Types observés : " + (", ".join(
        f"{name}={count}" for name, count in report["itype_counts"].items()
    ) or "(aucun)"))
    if args.output:
        print(f"Rapport JSON : {args.output}")
    if report["unreadable_files"]:
        print(f"Fichiers illisibles : {len(report['unreadable_files'])}")
    if report["path_errors"]:
        print(f"Chemins invalides : {len(report['path_errors'])}")
    if args.strict and (
        report["issues"] or report["unreadable_files"] or report["path_errors"]
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
