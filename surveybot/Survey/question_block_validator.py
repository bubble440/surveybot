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
from Survey.log_utils import log_debug


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


def _focaldata_response_option_cards_signal(driver) -> dict | None:
    """Retourne un signal minimal pour des cartes de réponse focaldata non extraites.

    Ce n'est pas un extracteur : il ne lit ni ne reconstruit les options. Il
    constate uniquement le pattern DOM complet observé sur
    community.focaldata.com — titre de question rendu via l'éditeur Draft.js,
    cartes d'options MUI Paper/Grid portant un attribut `data-cy` discriminant
    et un marqueur de sélection, sans input natif radio/checkbox, avec un CTA
    "Suivant"/"Next" présent mais désactivé — afin d'éviter de classer un écran
    focaldata transitoire ou sans question comme une anomalie d'extraction.
    """
    try:
        current_frame = getattr(driver, "_current_frame", driver)
        signal = current_frame.evaluate("""() => {
            if (location.hostname !== 'community.focaldata.com') return null;

            const visible = node => {
                if (!node) return false;
                const style = getComputedStyle(node);
                return style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && node.getClientRects().length > 0;
            };

            const editor = document.querySelector(
                '[data-test-id="editor-input"] .public-DraftEditor-content'
            );
            if (!visible(editor) || editor.textContent.trim().length < 8) return null;

            const options = Array.from(
                document.querySelectorAll('[data-cy^="response-option-"]')
            ).filter(visible);
            if (options.length < 2) return null;

            for (const option of options) {
                if (option.querySelector('input[type="radio"], input[type="checkbox"]')) {
                    return null;
                }
                if (!option.querySelector('[class*="responseOptionMarker"]')) return null;
            }

            const next = Array.from(document.querySelectorAll('button[type="button"]')).find(
                btn => visible(btn) && /suivant|next/i.test(btn.textContent || '')
            );
            if (!next || !next.disabled) return null;

            return {
                options_count: options.length,
            };
        }""")
        return signal if isinstance(signal, dict) else None
    except Exception:
        return None


def _netsurvey_choice_buttons_signal(driver) -> dict | None:
    """Retourne un signal minimal pour un choix unique Net-Survey/Soft Concept non extrait.

    Ce n'est pas un extracteur : il ne lit ni ne reconstruit les options. Il
    constate uniquement le pattern DOM complet observé sur des enquêtes
    générées par le moteur Soft Concept NET-Survey (script `ethnos.dll`,
    domaine variable selon le client) — bloc question `.ns-zq.ns-quali`
    portant un libellé `.ns-zlq`, au moins deux boutons de réponse cliquables
    `.ns-bouton-cont-vert button.ns-bouton.ns-zlr[ntsrep]`, avec un CTA
    "Suivant" (`input.ns-next[name="SENDBTN"]`) visible — afin d'éviter de
    classer un écran Net-Survey transitoire ou sans question comme une
    anomalie d'extraction. Le domaine du client n'est jamais utilisé comme
    critère : seule la signature du moteur (méta `SCSoftware`) l'est.
    """
    try:
        current_frame = getattr(driver, "_current_frame", driver)
        signal = current_frame.evaluate("""() => {
            const scMeta = document.querySelector(
                'meta[name="SCSoftware"][content="NET-Survey"]'
            );
            if (!scMeta) return null;

            const visible = node => {
                if (!node) return false;
                const style = getComputedStyle(node);
                return style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && node.getClientRects().length > 0;
            };

            const questions = Array.from(
                document.querySelectorAll('.ns-zq.ns-quali[data-ns-zq]')
            );
            for (const question of questions) {
                if (!visible(question)) continue;

                const questionText = question.querySelector('.ns-zlq');
                if (!questionText || questionText.textContent.trim().length < 8) {
                    continue;
                }

                const buttons = Array.from(
                    question.querySelectorAll(
                        '.ns-bouton-cont-vert button.ns-bouton.ns-zlr[ntsrep]'
                    )
                ).filter(visible);
                if (buttons.length < 2) continue;

                const next = document.querySelector(
                    '.button-bloc input.ns-next[name="SENDBTN"]'
                );
                if (!next || !visible(next)) continue;

                return {
                    question_id: question.getAttribute('data-ns-zq') || question.id || '',
                    buttons_count: buttons.length,
                };
            }
            return null;
        }""")
        return signal if isinstance(signal, dict) else None
    except Exception:
        return None


def _help_text_as_question_signals(driver, items: list[dict]) -> dict:
    """Détecte les blocs dont `question` est un texte d'aide/validation alors que
    le même conteneur DOM porte un intitulé de question distinct.

    Ce n'est pas un extracteur : lecture seule, ne modifie aucun bloc. Pour chaque
    item {target_id, xpath, question}, remonte (8 niveaux max) depuis l'élément
    ancré jusqu'au premier ancêtre portant un intitulé marqué (`question-text`,
    `label-question`, `QuestionText`, <legend>, titres) ; s'arrête si l'ancêtre
    porte plusieurs intitulés (autre question). Signale uniquement si le
    texte retenu comme question est exactement celui d'un nœud marqué aide/message/
    tip/hint/alert de ce conteneur et que l'intitulé candidat n'est ni contenu dans
    la question, ni une option. Retourne {target_id: texte_candidat}.
    """
    try:
        current_frame = getattr(driver, "_current_frame", driver)
        found = current_frame.evaluate("""(items) => {
            const norm = t => (t || '').normalize('NFC').replace(/\\s+/g, ' ').trim();
            const TITLE = '[class*="question-text"],[class*="label-question"],'
                + '[class*="QuestionText"],legend,h1,h2,h3,h4,[role="heading"]';
            const HELP = '[class*="help"],[class*="message"],[class*="-tip"],'
                + '[class*="hint"],[role="alert"]';
            const out = {};
            for (const it of items) {
                let el = null;
                try {
                    el = document.evaluate(it.xpath, document, null,
                        XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                } catch (e) { el = null; }
                if (!el) continue;
                const q = norm(it.question);
                const opts = new Set((it.options || []).map(o => norm(o).toLowerCase()));
                let node = el.parentElement;
                for (let depth = 0; node && depth < 8; depth++, node = node.parentElement) {
                    const all = Array.from(node.querySelectorAll(TITLE)).filter(
                        n => !n.closest(HELP) && !n.querySelector('input,select'));
                    const titles = all.filter(n => !all.some(o => o !== n && n.contains(o)));
                    if (!titles.length) continue;
                    if (titles.length > 1) break;
                    const helps = Array.from(node.querySelectorAll(HELP)).map(n => norm(n.textContent));
                    if (q && helps.includes(q)) {
                        for (const t of titles) {
                            const txt = norm(t.textContent);
                            if (txt.length >= 8 && txt !== q && !q.includes(txt)
                                && !opts.has(txt.toLowerCase())) {
                                out[it.target_id] = txt;
                                break;
                            }
                        }
                    }
                    break;
                }
            }
            return out;
        }""", items)
        return found if isinstance(found, dict) else {}
    except Exception:
        return {}


def _hidden_block_visible_choice_signal(driver, blocks: list[dict]) -> dict | None:
    """Signale un choix visible non couvert quand un bloc extrait pointe vers des options masquées.

    Les XPath doivent résoudre des libellés portant le texte exact des options :
    un input natif masqué avec son libellé visible ne suffit jamais au signal.
    Tout DOM trop grand ou ambigu est décliné.
    """
    if len(blocks) > 30:
        log_debug("[QUESTION_BLOCK_VALIDATOR]", "signal choix masqué décliné : plus de 30 blocs")
        return None
    items = []
    all_options = set()
    for idx, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        raw_options = block.get("options")
        if isinstance(raw_options, list) and len(raw_options) > 100:
            log_debug("[QUESTION_BLOCK_VALIDATOR]", "signal choix masqué décliné : plus de 100 options dans un bloc")
            return None
        options = [_norm_lc(o) for o in raw_options if _norm(o)] if isinstance(raw_options, list) else []
        all_options.update(options)
        if _norm_lc(block.get("itype")) not in {"radio", "checkbox"} or len(options) < 2:
            continue
        target_id = _norm(block.get("target_id"))
        target = get_target(target_id) if target_id else None
        xpath_map = target.get("option_xpath_map") if isinstance(target, dict) else None
        if not isinstance(xpath_map, dict) or len(xpath_map) < 2 or len(xpath_map) > 30:
            continue
        pairs = [
            {"option": _norm_lc(key), "xpath": xpath}
            for key, xpath in xpath_map.items()
            if isinstance(key, str) and isinstance(xpath, str) and key and xpath
        ]
        if len(pairs) >= 2:
            items.append({"block_index": idx, "target_id": target_id, "pairs": pairs})
    if len(all_options) > 100 or sum(len(item["pairs"]) for item in items) > 100:
        log_debug("[QUESTION_BLOCK_VALIDATOR]", "signal choix masqué décliné : budget options dépassé")
        return None
    if not items:
        return None

    try:
        current_frame = getattr(driver, "_current_frame", driver)
        signal = current_frame.evaluate("""({items, extractedOptions}) => {
            const norm = value => (value || '').normalize('NFC').replace(/\\s+/g, ' ').trim().toLowerCase();
            const visible = node => {
                if (!node || node.closest('[hidden], [aria-hidden="true"]')) return false;
                const style = getComputedStyle(node);
                return style.display !== 'none' && style.visibility !== 'hidden'
                    && node.getClientRects().length > 0;
            };
            let hidden = null;
            for (const item of items) {
                let matched = 0;
                for (const pair of item.pairs) {
                    let node;
                    try {
                        node = document.evaluate(pair.xpath, document, null,
                            XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                    } catch (e) { return null; }
                    if (!node || norm(node.textContent) !== norm(pair.option) || visible(node)) {
                        matched = 0;
                        break;
                    }
                    matched++;
                }
                if (matched >= 2) { hidden = item; break; }
            }
            if (!hidden) return null;

            const containers = document.querySelectorAll('fieldset, [role="radiogroup"], .question');
            if (containers.length > 40) return {budget_exceeded: 'containers'};
            const covered = new Set(extractedOptions.map(norm));
            for (const container of containers) {
                if (!visible(container)) continue;
                const title = container.querySelector(
                    'legend, [role="heading"], h1, h2, h3, h4, '
                    + '[class*="question-text"], [class*="question_text"], '
                    + '[class*="q_text"], [class*="prompt"]'
                );
                if (!visible(title) || norm(title.textContent).length < 8) continue;
                const inputs = container.querySelectorAll('input[type="radio"][name], input[type="checkbox"][name]');
                if (inputs.length > 40) return {budget_exceeded: 'inputs'};
                if (inputs.length < 2) continue;
                const groups = new Map();
                for (const input of inputs) {
                    const key = `${input.type}:${input.name}`;
                    const enclosingLabel = input.closest('label');
                    const labels = enclosingLabel
                        ? [enclosingLabel]
                        : input.parentElement.querySelectorAll('label, .option_label');
                    if (labels.length > 10) return {budget_exceeded: 'labels'};
                    const label = Array.from(labels).find(node => visible(node) && norm(node.textContent));
                    if (!label) continue;
                    const option = norm(label.textContent);
                    if (!groups.has(key)) groups.set(key, new Set());
                    groups.get(key).add(option);
                }
                for (const options of groups.values()) {
                    if (options.size < 2) continue;
                    if (Array.from(options).some(option => covered.has(option))) continue;
                    return {
                        block_index: hidden.block_index,
                        target_id: hidden.target_id,
                        visible_question: norm(title.textContent).slice(0, 160),
                        visible_options_count: options.size,
                    };
                }
            }
            return null;
        }""", {"items": items, "extractedOptions": list(all_options)})
        if isinstance(signal, dict) and signal.get("budget_exceeded"):
            log_debug("[QUESTION_BLOCK_VALIDATOR]", f"signal choix masqué décliné : budget {signal['budget_exceeded']} dépassé")
            return None
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
            else:
                signal = _focaldata_response_option_cards_signal(driver)
                if signal:
                    issues.append({
                        "failure_type": "missing_block",
                        "dom_signal": "focaldata_response_option_cards",
                        **signal,
                    })
                else:
                    signal = _netsurvey_choice_buttons_signal(driver)
                    if signal:
                        issues.append({
                            "failure_type": "missing_block",
                            "dom_signal": "netsurvey_choice_buttons",
                            **signal,
                        })

    # Signal additif : `question` retenue = texte d'aide/validation alors qu'un
    # intitulé de question distinct existe dans le même conteneur DOM.
    help_candidates: dict[str, str] = {}
    if blocks and driver is not None:
        items = []
        for b in blocks:
            if not isinstance(b, dict) or _norm_lc(b.get("itype")) not in {"radio", "checkbox", "dropdown"}:
                continue
            tid = _norm(b.get("target_id"))
            reg = get_target(tid) if tid else None
            if not reg:
                continue
            xp = reg.get("xpath")
            if not xp:
                oxm = reg.get("option_xpath_map")
                if isinstance(oxm, dict):
                    xp = next((v for v in oxm.values() if isinstance(v, str) and v), None)
            if isinstance(xp, str) and xp:
                items.append({
                    "target_id": tid,
                    "xpath": xp,
                    "question": _norm(b.get("question")),
                    "options": [str(o) for o in (b.get("options") or [])],
                })
        if items:
            help_candidates = _help_text_as_question_signals(driver, items)

        signal = _hidden_block_visible_choice_signal(driver, blocks)
        if signal:
            issues.append({
                "failure_type": "missing_block",
                "dom_signal": "hidden_block_visible_choice",
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

        if target_id in help_candidates:
            issues.append({
                "failure_type": "question_text_is_help",
                "block_index": idx,
                "target_id": target_id,
                "itype": itype,
                "question": question,
                "candidate_question": help_candidates[target_id],
            })

    return {
        "stage": "extraction",
        "ok": not issues,
        "blocks_count": len(blocks),
        "issues": issues,
    }
