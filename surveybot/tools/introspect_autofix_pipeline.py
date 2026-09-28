"""introspect_autofix_pipeline.py — sonde en LECTURE SEULE utilisee par
preflight_autofix_clone.ps1 (Partie B du chantier "clone dedie a l'orchestrateur
autofix", cf. Utils/AUTOFIX_CLONE_ORCHESTRATEUR.md). Derive depuis le code
REELLEMENT en usage (jamais recopie a la main dans un script PowerShell separe,
qui pourrait silencieusement se desynchroniser d'un futur renommage) :

  - PROTECTED_BRANCHES (Survey/autofix_worktree.py) — jamais une branche cible
    de merge automatique (Survey/merge_executor.py).
  - LOCK_FILENAME / DEFAULT_LOCK_STALE_AFTER_S (Survey/autofix_orchestrator.py)
    — nom et seuil de peremption du verrou d'exclusion de l'orchestrateur.
  - La liste des racines d'artefacts par defaut des facades tools/*.py : chaque
    argument --xxx-root d'un tools/*.py, avec sa valeur par defaut, obtenue par
    analyse syntaxique (module ast, jamais une execution de ces scripts) —
    jamais une liste recopiee a la main, qui se desynchroniserait silencieusement
    du jour ou une facade ajoute/renomme une racine.

N'importe aucun module qui aurait un effet de bord, n'execute aucune phase du
pipeline, n'ecrit jamais rien sur disque. Sortie : un seul objet JSON sur
stdout.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path


def _derive_artifact_roots(tools_dir: Path) -> "list[str]":
    """Analyse syntaxique (ast, jamais une exécution) de chaque tools/*.py :
    toute paire (argument --xxx-root, default="...") d'un appel add_argument()
    — convention déjà uniformément suivie par toutes les façades existantes de
    ce répertoire (vérifié avant d'écrire cette fonction)."""
    roots: "set[str]" = set()
    for py_file in sorted(tools_dir.glob("*.py")):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"
            ):
                continue
            flag = None
            for arg in node.args:
                if (
                    isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str)
                    and arg.value.startswith("--")
                    and arg.value.endswith("-root")
                ):
                    flag = arg.value
            if not flag:
                continue
            for kw in node.keywords:
                if (
                    kw.arg == "default"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                ):
                    roots.add(kw.value.value)
    return sorted(roots)


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sonde en lecture seule : constantes réelles du pipeline autofix + racines d'artefacts dérivées de tools/*.py."
    )
    parser.add_argument("package_root", help="Racine du paquet (dossier contenant Survey/, tools/, requirements.txt)")
    args = parser.parse_args(argv)

    package_root = Path(args.package_root).resolve()
    sys.path.insert(0, str(package_root))

    try:
        from Survey.autofix_worktree import PROTECTED_BRANCHES
        from Survey.autofix_orchestrator import DEFAULT_LOCK_STALE_AFTER_S, LOCK_FILENAME
    except Exception as exc:  # import isolé volontairement large : préflight distingue déjà un venv cassé ailleurs
        print(json.dumps({"error": f"import Survey.autofix_worktree/autofix_orchestrator échoué : {exc!r}"}))
        return 1

    result = {
        "protected_branches": sorted(PROTECTED_BRANCHES),
        "default_lock_stale_after_s": DEFAULT_LOCK_STALE_AFTER_S,
        "lock_filename": LOCK_FILENAME,
        "artifact_roots": _derive_artifact_roots(package_root / "tools"),
    }
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
