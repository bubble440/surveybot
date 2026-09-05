# Survey/page_snapshot.py
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from config import RUN_ENV




def _wait_dom_settle(
    driver,
    *,
    max_wait_s: float = 6.0,
    stable_rounds: int = 3,
    poll_s: float = 0.25,
) -> None:
    """Attends un DOM "stable" avant de capturer (best-effort).

    Heuristique:
      - attend document.readyState == 'complete'
      - puis attends que (len(outerHTML), len(innerText), count(inputs)) soit stable N fois
    """
    page = driver

    _JS_SIG = """() => {
        try {
            const html = document.documentElement ? document.documentElement.outerHTML : '';
            const txt = document.body ? (document.body.innerText || '') : '';
            const inputs = document.querySelectorAll(
                "input:not([type='hidden']), select, textarea, [role='radio'], [role='checkbox'], [contenteditable='true']"
            ).length;
            const rs = document.readyState || '';
            return [rs, html.length, txt.length, inputs].join('|');
        } catch(e) {
            return 'err';
        }
    }"""

    deadline = time.time() + max_wait_s
    stable = 0
    last = None

    while time.time() < deadline:
        try:
            rs = page.evaluate("() => document.readyState")
        except Exception:
            rs = ""

        if rs != "complete":
            time.sleep(poll_s)
            continue

        try:
            cur = page.evaluate(_JS_SIG)
        except Exception:
            cur = None

        if cur and cur == last:
            stable += 1
            if stable >= stable_rounds:
                return
        else:
            stable = 0
            last = cur

        time.sleep(poll_s)


def _dump_frames_best_effort(driver, folder: Path) -> List[Dict[str, Any]]:
    """Dump les DOM des iframes dans ./frames (best-effort).

    Utilise frame_utils.iter_frame_chains / switch_to_frame_chain sur un objet
    Page Playwright natif. Après le switch, la Frame courante est utilisée
    directement via les API Playwright natives (query_selector_all, inner_text, etc.).
    """
    try:
        from Survey.frame_utils import iter_frame_chains, switch_to_frame_chain  # type: ignore
    except Exception:
        return []

    frames_dir = folder / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    out: List[Dict[str, Any]] = []

    for chain in iter_frame_chains(driver, max_depth=2):
        if not chain:
            continue

        with switch_to_frame_chain(driver, chain) as ok:
            if not ok:
                continue

            current_frame = getattr(driver, "_current_frame", driver)

            try:
                url = current_frame.evaluate("() => location.href") or ""
            except Exception:
                url = ""

            try:
                title = current_frame.evaluate("() => document.title") or ""
            except Exception:
                title = ""

            try:
                text_len = int(
                    current_frame.evaluate(
                        "() => (document.body && (document.body.innerText||'').length) || 0"
                    ) or 0
                )
            except Exception:
                text_len = 0

            try:
                inputs_count = int(
                    current_frame.evaluate(
                        """() => document.querySelectorAll(
                            "input:not([type='hidden']),select,textarea,[role='radio'],[role='checkbox'],[contenteditable='true']"
                        ).length"""
                    ) or 0
                )
            except Exception:
                inputs_count = 0

            if inputs_count <= 0 and text_len < 200:
                continue

            try:
                outer = current_frame.evaluate("() => document.documentElement.outerHTML") or ""
            except Exception:
                outer = ""

            try:
                src = current_frame.content() or ""
            except Exception:
                src = ""

            chain_str = "_".join(str(x) for x in chain)
            outer_name = f"frame_{chain_str}.dom_outer.html"
            src_name = f"frame_{chain_str}.page_source.html"

            (frames_dir / outer_name).write_text(outer, encoding="utf-8", errors="ignore")
            (frames_dir / src_name).write_text(src, encoding="utf-8", errors="ignore")

            out.append(
                {
                    "chain": chain,
                    "chain_str": chain_str,
                    "url": url,
                    "title": title,
                    "text_len": text_len,
                    "inputs_count": inputs_count,
                    "files": {
                        "dom_outer": f"frames/{outer_name}",
                        "page_source": f"frames/{src_name}",
                    },
                }
            )

    return out


def _slug(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9_-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return (s[:60] or "snapshot")


def dump_page_snapshot(
    driver,
    *,
    reason: str,
    out_root: Optional[str] = None,
    question_blocks: Any = None,
    snapshot_name: Optional[str] = None,
) -> str:
    """
    Sauvegarde un snapshot de page "debug" :
    - meta.json (url, title, timestamp, reason)
    - dom_outer.html (documentElement.outerHTML)
    - page_source.html (page.content())
    - viewport.png (screenshot viewport)
    - page.mhtml (si Chrome/Chromium via CDP natif Playwright)
    - question_blocks.json (si fourni)
    """
    page = driver

    ts = time.strftime("%Y%m%d_%H%M%S")

    forced = (snapshot_name or os.getenv("SURVEY_SNAPSHOT_NAME", "") or "").strip()
    if forced:
        folder_name = _slug(forced)
    else:
        folder_name = f"{ts}_{_slug(reason)}"

    is_local = RUN_ENV != "prod"
    default_root = "./snapshots" if is_local else "/tmp/snapshots"

    root = Path(out_root or os.getenv("SURVEY_SNAPSHOT_DIR", default_root))
    folder = root / folder_name
    folder.mkdir(parents=True, exist_ok=True)

    try:
        _wait_dom_settle(driver)
    except Exception:
        pass

    snapshot_ctx = page
    try:
        from Survey.dom_frame_selector import _select_best_frame_chain
        from Survey.frame_utils import switch_to_frame_chain as _switch_ctx

        _best_chain, _ = _select_best_frame_chain(driver)
        with _switch_ctx(driver, _best_chain) as _ok:
            if _ok:
                snapshot_ctx = getattr(driver, "_current_frame", page)
    except Exception:
        snapshot_ctx = page

    try:
        url = page.url
    except Exception:
        url = ""

    ready_state = ""
    dom_sig = ""
    title = ""
    try:
        title = snapshot_ctx.evaluate("() => document.title") or ""
        try:
            ready_state = snapshot_ctx.evaluate("() => document.readyState") or ""
        except Exception:
            ready_state = ""

        try:
            dom_sig = snapshot_ctx.evaluate("""() =>
                [document.readyState,
                 (document.documentElement && document.documentElement.outerHTML || '').length,
                 (document.body && (document.body.innerText || '').length) || 0,
                 document.querySelectorAll(
                     "input:not([type='hidden']),select,textarea,[role='radio'],[role='checkbox'],[contenteditable='true']"
                 ).length].join('|')
            """) or ""
        except Exception:
            dom_sig = ""

    except Exception:
        title = ""

    meta = {
        "ts": ts,
        "ready_state": ready_state,
        "dom_sig": dom_sig,
        "reason": reason,
        "url": url,
        "title": title,
    }
    (folder / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    try:
        outer = snapshot_ctx.evaluate("() => document.documentElement.outerHTML") or ""
    except Exception:
        outer = ""
    (folder / "dom_outer.html").write_text(outer, encoding="utf-8", errors="ignore")

    try:
        body_outer = snapshot_ctx.evaluate("() => document.body ? document.body.outerHTML : ''") or ""
    except Exception:
        body_outer = ""
    (folder / "dom_body.html").write_text(body_outer, encoding="utf-8", errors="ignore")

    if forced:
        try:
            (folder / f"{_slug(forced)}.dom_body.html").write_text(body_outer, encoding="utf-8", errors="ignore")
        except Exception:
            pass

    try:
        src = snapshot_ctx.content() or ""
    except Exception:
        src = ""
    (folder / "page_source.html").write_text(src, encoding="utf-8", errors="ignore")

    try:
        body_text = page.evaluate("() => (document.body && (document.body.innerText || '')) || ''") or ""
    except Exception:
        body_text = ""
    try:
        (folder / "body_text.txt").write_text(body_text, encoding="utf-8", errors="ignore")
    except Exception:
        pass

    try:
        page.screenshot(path=str(folder / "viewport.png"))
    except Exception:
        pass

    try:
        cdp_session = page.context.new_cdp_session(page)
        res = cdp_session.send("Page.captureSnapshot", {"format": "mhtml"})
        cdp_session.detach()
        data = (res or {}).get("data")
        if data:
            (folder / "page.mhtml").write_text(data, encoding="utf-8", errors="ignore")
    except Exception as e:
        (folder / "mhtml_error.txt").write_text(repr(e), encoding="utf-8")

    try:
        frames = _dump_frames_best_effort(driver, folder)
    except Exception:
        frames = []

    if frames:
        try:
            best = sorted(
                frames,
                key=lambda f: (int(f.get("inputs_count", 0)), int(f.get("text_len", 0))),
                reverse=True,
            )[0]
        except Exception:
            best = None

        try:
            meta.update({"frames_count": len(frames), "frames": frames, "best_frame": best})
            (folder / "meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    if question_blocks is not None:
        try:
            qb_path = folder / "question_blocks.json"
            qb_path.write_text(
                json.dumps(question_blocks, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            dbg = (os.getenv("DOM_CONTEXT_DEBUG", "0") or "").strip().lower()
            if dbg in {"1", "true", "yes", "on"}:
                print(
                    f"[DOM_CONTEXT_DEBUG] snapshot_write question_blocks_path={qb_path} "
                    f"blocks_count={len(question_blocks or [])}"
                )
        except Exception:
            pass

    return str(folder)


def snapshot_if_enabled(driver, *, reason: str, question_blocks: Any = None) -> Optional[str]:
    """
    Hot-toggle snapshot via ENV SURVEY_SNAPSHOT ou flag-file.
    Retourne le chemin du snapshot si créé.
    """
    import os
    from pathlib import Path

    # À ce moment du flux, survey_executor a déjà importé action_dispatcher et
    # l'analyse DOM est terminée. On installe donc ici le hook passif d'action
    # avant le futur execute_actions_plan(), sans modifier son comportement.
    _install_action_observer()

    v = (os.getenv("SURVEY_SNAPSHOT", "1") or "").strip().lower()
    if v in ("1", "true", "all", "on", "yes"):
        return dump_page_snapshot(driver, reason=reason, question_blocks=question_blocks)
    if v in ("0", "false", "off", "no"):
        return None

    is_local = RUN_ENV != "prod"
    default_flag = "./snapshots/.snapshot_flag" if is_local else "/tmp/survey_snapshot.flag"
    flag_path = Path(os.getenv("SURVEY_SNAPSHOT_FLAG_FILE", default_flag))

    try:
        if not flag_path.exists():
            return None

        content = (flag_path.read_text(encoding="utf-8", errors="ignore") or "").strip().lower()
        if content in ("1", "true", "on", "yes", "all"):
            return dump_page_snapshot(driver, reason=reason, question_blocks=question_blocks)
        if content in ("0", "false", "off", "no", ""):
            return None

        return None
    except Exception:
        return None


# =============================================================================
# PHASE 1A — OBSERVABILITÉ PASSIVE
# =============================================================================
# Les hooks ci-dessous enveloppent les deux points critiques existants sans changer
# leurs valeurs de retour ni leur stratégie : analyze_dom() puis execute_actions_plan().
# Ils sont idempotents et toute erreur d'observabilité est absorbée. L'objectif de 1A
# est uniquement de constater/capturer les incohérences, jamais de retry ni de réparer.

_LAST_EXTRACTED_BLOCKS: dict[int, Any] = {}


def _install_extraction_observer() -> None:
    try:
        import Survey.dom_analyzer as dom_analyzer
        from Survey.question_block_validator import validate_question_blocks
        from Survey.failure_recorder import record_validation_failure

        original = dom_analyzer.analyze_dom
        if getattr(original, "_phase1a_observer", False):
            return

        def observed_analyze_dom(driver, *args, **kwargs):
            result = original(driver, *args, **kwargs)
            blocks = result or []
            try:
                _LAST_EXTRACTED_BLOCKS[id(driver)] = blocks
                report = validate_question_blocks(blocks)
                if not report.get("ok", True):
                    record_validation_failure(
                        driver,
                        stage="extraction",
                        report=report,
                        question_blocks=blocks,
                    )
            except Exception as exc:
                try:
                    from Survey.log_utils import log_debug
                    log_debug("[OBSERVABILITY]", f"extraction validator error: {type(exc).__name__}: {exc}")
                except Exception:
                    pass
            return result

        observed_analyze_dom._phase1a_observer = True
        observed_analyze_dom._phase1a_original = original
        dom_analyzer.analyze_dom = observed_analyze_dom
    except Exception:
        pass


def _install_action_observer() -> None:
    try:
        import Survey.action_dispatcher as action_dispatcher
        from Survey.action_validator import validate_actions
        from Survey.failure_recorder import record_validation_failure

        original = action_dispatcher.execute_actions_plan
        if getattr(original, "_phase1a_observer", False):
            return

        def observed_execute_actions_plan(driver, actions, *args, **kwargs):
            result = original(driver, actions, *args, **kwargs)
            try:
                report = validate_actions(actions, dispatcher_success=bool(result))
                if not report.get("ok", True):
                    record_validation_failure(
                        driver,
                        stage="action",
                        report=report,
                        question_blocks=_LAST_EXTRACTED_BLOCKS.get(id(driver)),
                        actions=actions,
                    )
            except Exception as exc:
                try:
                    from Survey.log_utils import log_debug
                    log_debug("[OBSERVABILITY]", f"action validator error: {type(exc).__name__}: {exc}")
                except Exception:
                    pass
            return result

        observed_execute_actions_plan._phase1a_observer = True
        observed_execute_actions_plan._phase1a_original = original
        action_dispatcher.execute_actions_plan = observed_execute_actions_plan
    except Exception:
        pass


# survey_executor importe page_snapshot avant son appel à analyze_dom(). Le hook
# extraction est donc installé immédiatement et reste transparent pour tout appelant.
_install_extraction_observer()
