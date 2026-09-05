from __future__ import annotations

"""Enregistrement passif des anomalies détectées par les validators Phase 1A."""

import json
from pathlib import Path
from typing import Any, Optional

from Survey.log_utils import log_debug, log_info


def record_validation_failure(
    driver,
    *,
    stage: str,
    report: dict,
    question_blocks: Any = None,
    actions: Any = None,
) -> Optional[str]:
    """Capture le contexte d'un validator en échec, sans jamais casser le survey.

    Réutilise page_snapshot comme source unique de capture DOM/frame/screenshot.
    Les artefacts propres à l'observabilité sont ajoutés dans le même dossier.
    """
    if not isinstance(report, dict) or report.get("ok", True):
        return None

    try:
        from Survey.page_snapshot import dump_page_snapshot

        folder = dump_page_snapshot(
            driver,
            reason=f"{stage}_validation_failure",
            question_blocks=question_blocks,
        )
        out = Path(folder)
        (out / "validation_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if actions is not None:
            (out / "actions_requested.json").write_text(
                json.dumps(actions, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        issue_types = [
            str(i.get("failure_type") or "unknown")
            for i in (report.get("issues") or [])
            if isinstance(i, dict)
        ]
        log_info(
            "[OBSERVABILITY]",
            f"validation_failure stage={stage} issues={issue_types} snapshot={folder}",
        )
        return folder
    except Exception as exc:
        # L'observabilité ne doit jamais devenir une nouvelle cause d'échec survey.
        log_debug("[OBSERVABILITY]", f"failure recorder error stage={stage}: {type(exc).__name__}: {exc}")
        return None
