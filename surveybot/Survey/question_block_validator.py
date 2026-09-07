from __future__ import annotations

"""Validation passive des question_blocks extraits.

Phase 1A : observabilité uniquement.
- Ne modifie jamais les blocs.
- Ne déclenche aucun retry.
- Ne bloque jamais l'exécution du survey.
- Ne signale que des incohérences structurelles à forte confiance.
"""

from typing import Any

from Survey.dom_registry import get_target


_ALLOWED_CHOICE_TYPES = {"radio", "checkbox", "dropdown", "matrix"}


def _norm(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _norm_lc(value: Any) -> str:
    return _norm(value).lower()


def _qualtrics_ranked_choices_signal(driver) -> dict | None:
    """Retourne un signal minimal pour un ranking Qualtrics non extrait.

    Ce n'est pas un extracteur : il ne lit ni ne reconstruit les options. Il
    constate uniquement le pattern DOM complet du widget de ranking observé,
    afin d'éviter de classer un écran Qualtrics transitoire ou CTA-only comme
    une anomalie d'extraction.
    """
    try:
        current_frame = getattr(driver, "_current_frame", driver)
        signal = current_frame.evaluate("""() => {
            const root = document.querySelector('#Questions[role="main"]');
            if (!root) return null;

            const visible = node => {
                if (!node || node.classList.contains('Hidden')) return false;
                const style = getComputedStyle(node);
                return style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && node.getClientRects().length > 0;
            };

            for (const question of root.querySelectorAll(
                '.QuestionOuter[id^="QID"]'
            )) {
                if (!visible(question)) continue;
                const questionText = question.querySelector('.QuestionText');
                const ranking = question.querySelector(
                    '.ChoiceStructure .DND > ul.ui-sortable[role="list"]'
                );
                const choices = ranking
                    ? ranking.querySelectorAll('li[role="listitem"][data-choiceid]')
                    : [];
                if (!questionText || questionText.textContent.trim().length < 8 || choices.length < 2) {
                    continue;
                }
                const next = document.querySelector('#NextButton[type="button"]');
                if (!next || !visible(next)) continue;
                return {
                    question_id: question.id || '',
                    choices_count: choices.length,
                };
            }
            return null;
        }""")
        return signal if isinstance(signal, dict) else None
    except Exception:
        return None


def _zappi_max_diff_signal(driver) -> dict | None:
    """Retourne un signal minimal pour le widget Zappi MaxDiff observé.

    Le garde exige le domaine et la structure complète question + lignes de
    choix gauche/droite + CTA du widget. Il ne reconstruit aucune option et ne
    tente aucune interaction.
    """
    try:
        current_frame = getattr(driver, "_current_frame", driver)
        signal = current_frame.evaluate("""() => {
            if (location.hostname !== 'data-collector.zappi.io') return null;

            const visible = node => {
                if (!node) return false;
                const style = getComputedStyle(node);
                return style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && node.getClientRects().length > 0;
            };

            const question = document.querySelector(
                '#root .max-diff-question-container'
            );
            if (!visible(question)) return null;

            const questionText = question.querySelector(
                '.question-description-container .zappi-header-text'
            );
            if (!visible(questionText) || questionText.textContent.trim().length < 8) {
                return null;
            }

            const rows = Array.from(
                question.querySelectorAll('.max-diff-container.row[id]')
            );
            if (rows.length < 2) return null;

            for (const row of rows) {
                const left = row.querySelector(
                    '.max-diff-radio-container.left-radio-container input[type="radio"][readonly]'
                );
                const right = row.querySelector(
                    '.max-diff-radio-container.right-radio-container input[type="radio"][readonly]'
                );
                const content = row.querySelector('.content-container');
                if (!left || !right || !visible(content)) return null;
            }

            const next = document.querySelector('#root #timer-button-id.btn-timer');
            if (!visible(next)) return null;

            return {
                rows_count: rows.length,
                radio_count: rows.length * 2,
            };
        }""")
        return signal if isinstance(signal, dict) else None
    except Exception:
        return None


def validate_question_blocks(question_blocks: list[dict] | None, *, driver=None) -> dict:
    """Retourne un rapport JSON-sérialisable sans effet de bord."""
    blocks = question_blocks or []
    issues: list[dict] = []

    # Une liste vide est légitime sur les transitions et écrans sans question.
    # Seuls les patterns DOM complets, strictement définis ci-dessus et
    # confirmés par leurs DOM de reproduction, sont signalés.
    if not blocks and driver is not None:
        signal = _qualtrics_ranked_choices_signal(driver)
        if signal:
            issues.append({
                "failure_type": "missing_block",
                "dom_signal": "qualtrics_ranked_choices",
                **signal,
            })
        else:
            signal = _zappi_max_diff_signal(driver)
            if signal:
                issues.append({
                    "failure_type": "missing_block",
                    "dom_signal": "zappi_max_diff",
                    **signal,
                })

    for idx, block in enumerate(blocks):
        if not isinstance(block, dict):
            issues.append({
                "failure_type": "invalid_block_shape",
                "block_index": idx,
                "details": "question block is not a dict",
            })
            continue

        itype = _norm_lc(block.get("itype"))
        target_id = _norm(block.get("target_id"))
        question = _norm(block.get("question"))
        options = block.get("options") if isinstance(block.get("options"), list) else []
        normalized_options = [_norm_lc(opt) for opt in options if _norm(opt)]

        if not target_id:
            issues.append({
                "failure_type": "missing_target_id",
                "block_index": idx,
                "itype": itype,
                "question": question,
            })
        elif get_target(target_id) is None:
            issues.append({
                "failure_type": "registry_target_missing",
                "block_index": idx,
                "target_id": target_id,
                "itype": itype,
                "question": question,
            })

        # Pour radio/checkbox/dropdown, une liste d'options vide est une anomalie
        # structurelle forte. Matrix est exclu ici car certains extracteurs portent
        # lignes/colonnes dans context plutôt que dans options.
        if itype in {"radio", "checkbox", "dropdown"} and not normalized_options:
            issues.append({
                "failure_type": "choice_without_options",
                "block_index": idx,
                "target_id": target_id,
                "itype": itype,
                "question": question,
            })

        if normalized_options and len(normalized_options) != len(set(normalized_options)):
            issues.append({
                "failure_type": "duplicate_options",
                "block_index": idx,
                "target_id": target_id,
                "itype": itype,
                "question": question,
            })

        try:
            min_select = int(block.get("min_select", 0) or 0)
        except Exception:
            min_select = 0
        try:
            max_select = int(block.get("max_select", 0) or 0)
        except Exception:
            max_select = 0

        if min_select > 0 and max_select > 0 and min_select > max_select:
            issues.append({
                "failure_type": "invalid_selection_limits",
                "block_index": idx,
                "target_id": target_id,
                "itype": itype,
                "min_select": min_select,
                "max_select": max_select,
            })

        if itype == "checkbox" and max_select > 0 and normalized_options and max_select > len(normalized_options):
            issues.append({
                "failure_type": "max_select_exceeds_options",
                "block_index": idx,
                "target_id": target_id,
                "max_select": max_select,
                "options_count": len(normalized_options),
            })

    return {
        "stage": "extraction",
        "ok": not issues,
        "blocks_count": len(blocks),
        "issues": issues,
    }
