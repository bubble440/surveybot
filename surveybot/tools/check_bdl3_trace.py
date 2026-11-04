"""
check_bdl3_trace.py — Vérifie, en lecture seule, que les action_trace.json produits
par un run réel contiennent les champs BDL-3.

Un rejeu statique (replay_failure.py) n'exécute pas le dispatcher : il ne peut pas
produire les résultats par action. La preuve BDL-3 vient donc d'un run réel
(observabilité active + collecte BDL), puis de ce contrôle.

Usage (depuis surveybot/) :
    python tools\check_bdl3_trace.py bdl_collect
    python tools\check_bdl3_trace.py ..\snapshots
Code retour : 0 si toutes les traces sont conformes, 1 sinon, 2 si aucune trace.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Champs attendus par action, et au niveau trace (contrat BDL-3).
_ACTION_KEYS = ("index", "target_id", "itype", "value", "requested_value", "dispatcher_action_success")
_TRACE_KEYS = ("requested_actions", "dispatcher_success", "validator_signals", "before", "after")


def _check(path: Path) -> list:
    """Retourne la liste des problèmes d'une trace (vide = conforme)."""
    try:
        trace = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"illisible: {type(exc).__name__}"]
    problems = [f"clé absente: {k}" for k in _TRACE_KEYS if k not in trace]
    if trace.get("before") is None or trace.get("after") is None:
        problems.append("état avant/après manquant")
    for i, act in enumerate(trace.get("requested_actions") or []):
        problems += [f"action[{i}] sans {k}" for k in _ACTION_KEYS if k not in act]
    return problems


def _oracle_summary(path: Path) -> str:
    """Résumé informatif du verdict d'oracle (non bloquant : absent sur les anciennes traces)."""
    try:
        oracle = json.loads(path.read_text(encoding="utf-8")).get("oracle")
    except Exception:
        return "illisible"
    if not isinstance(oracle, dict):
        return "absent"
    return ", ".join(f"{k}={v}" for k, v in sorted((oracle.get("summary") or {}).items())) or "vide"


def main(argv: list) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path("bdl_collect")
    traces = sorted(root.rglob("action_trace.json"))
    if not traces:
        print(f"aucune action_trace.json sous {root}")
        return 2
    bad = 0
    for path in traces:
        problems = _check(path)
        bad += bool(problems)
        print(f"{'KO' if problems else 'OK'}  {path.parent.name}  {'; '.join(problems)}")
        print(f"      oracle : {_oracle_summary(path)}")
    print(f"{len(traces) - bad}/{len(traces)} conformes")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
