# Survey/page_snapshot.py
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    js_sig = """
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
    """

    deadline = time.time() + max_wait_s
    stable = 0
    last = None

    while time.time() < deadline:
        try:
            rs = driver.execute_script("return document.readyState")
        except Exception:
            rs = ""

        if rs != "complete":
            time.sleep(poll_s)
            continue

        try:
            cur = driver.execute_script(js_sig)
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
    """Dump les DOM des iframes dans ./frames (best-effort)."""
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

            try:
                url = driver.execute_script("return location.href") or ""
            except Exception:
                url = ""

            try:
                title = driver.execute_script("return document.title") or ""
            except Exception:
                title = ""

            try:
                text_len = int(
                    driver.execute_script(
                        "return (document.body && (document.body.innerText||'').length) || 0"
                    )
                )
            except Exception:
                text_len = 0

            try:
                inputs_count = int(
                    driver.execute_script(
                        "return document.querySelectorAll(\"input:not([type='hidden']),select,textarea,[role='radio'],[role='checkbox'],[contenteditable='true']\").length"
                    )
                )
            except Exception:
                inputs_count = 0

            # évite d'exploser la taille des snapshots
            if inputs_count <= 0 and text_len < 200:
                continue

            try:
                outer = driver.execute_script("return document.documentElement.outerHTML") or ""
            except Exception:
                outer = ""

            try:
                src = driver.page_source or ""
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
    - page_source.html (driver.page_source)
    - viewport.png (screenshot viewport)
    - page.mhtml (si Chrome/Chromium via CDP)
    - question_blocks.json (si fourni)

    Conçu pour être:
    - opt-in (activé par env)
    - budget friendly (pas d'appel réseau, pas d'OCR)
    - robuste (try/except partout)
    """

    ts = time.strftime("%Y%m%d_%H%M%S")

    # Nom forcé (ex: case.json["name"]) :
    # priorité: param snapshot_name -> ENV SURVEY_SNAPSHOT_NAME -> fallback ts+reason
    forced = (snapshot_name or os.getenv("SURVEY_SNAPSHOT_NAME", "") or "").strip()
    if forced:
        folder_name = _slug(forced)
    else:
        folder_name = f"{ts}_{_slug(reason)}"

    # Dossier par défaut:
    # - local: ./snapshots
    # - prod/docker: /tmp/snapshots (évite d’écrire dans l’image)
    is_local = os.getenv("RUN_ENV", "local") == "local"
    default_root = "./snapshots" if is_local else "/tmp/snapshots"

    root = Path(out_root or os.getenv("SURVEY_SNAPSHOT_DIR", default_root))
    folder = root / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    # IMPORTANT: stabilise le DOM avant de capturer (évite de figer un loader/état transitoire)
    try:
        _wait_dom_settle(driver)
    except Exception:
        pass

    # Meta
    try:
        url = driver.current_url
    except Exception:
        url = ""

    try:
        title = driver.execute_script("return document.title") or ""
        try:
            ready_state = driver.execute_script("return document.readyState") or ""
        except Exception:
            ready_state = ""

        try:
            dom_sig = driver.execute_script(
                "return [document.readyState,(document.documentElement&&document.documentElement.outerHTML||'').length,(document.body&&(document.body.innerText||'').length)||0,document.querySelectorAll(\"input:not([type='hidden']),select,textarea,[role='radio'],[role='checkbox'],[contenteditable='true']\").length].join('|')"
            ) or ""
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

    # DOM outerHTML (le plus utile pour tes tests DOM)
    try:
        outer = driver.execute_script("return document.documentElement.outerHTML") or ""
    except Exception:
        outer = ""
    (folder / "dom_outer.html").write_text(outer, encoding="utf-8", errors="ignore")

    # DOM body uniquement (demandé pour tes snapshots/cases)
    try:
        body_outer = driver.execute_script("return document.body ? document.body.outerHTML : ''") or ""
    except Exception:
        body_outer = ""
    (folder / "dom_body.html").write_text(body_outer, encoding="utf-8", errors="ignore")

    # Si on a un nom de case, on écrit aussi un fichier nommé comme le case.json["name"]
    if forced:
        try:
            (folder / f"{_slug(forced)}.dom_body.html").write_text(body_outer, encoding="utf-8", errors="ignore")
        except Exception:
            pass

    # page_source (parfois différent du DOM live, mais utile)
    try:
        src = driver.page_source or ""
    except Exception:
        src = ""
    (folder / "page_source.html").write_text(src, encoding="utf-8", errors="ignore")
    # Texte visible (audit rapide sans navigateur)
    try:
        body_text = driver.execute_script("return (document.body && (document.body.innerText || '')) || ''") or ""
    except Exception:
        body_text = ""
    try:
        (folder / "body_text.txt").write_text(body_text, encoding="utf-8", errors="ignore")
    except Exception:
        pass

    # Screenshot viewport
    try:
        driver.save_screenshot(str(folder / "viewport.png"))
    except Exception:
        pass

    # MHTML (Chrome/Chromium uniquement)
    try:
        if hasattr(driver, "execute_cdp_cmd"):
            res = driver.execute_cdp_cmd("Page.captureSnapshot", {"format": "mhtml"})
            data = (res or {}).get("data")
            if data:
                (folder / "page.mhtml").write_text(
                    data, encoding="utf-8", errors="ignore"
                )
    except Exception as e:
        (folder / "mhtml_error.txt").write_text(repr(e), encoding="utf-8")

    # Dump frames (utile quand le contenu est dans un iframe)
    try:
        frames = _dump_frames_best_effort(driver, folder)
    except Exception:
        frames = []

    if frames:
        # meilleur frame = plus d'inputs, puis plus de texte
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

    # Question blocks (super important pour valider dom_analyzer/prompt_builder)
    if question_blocks is not None:
        try:
            (folder / "question_blocks.json").write_text(
                json.dumps(question_blocks, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    return str(folder)

def snapshot_if_enabled(driver, *, reason: str, question_blocks: Any = None) -> Optional[str]:
    """
    Hot-toggle snapshot via:
      1) ENV SURVEY_SNAPSHOT (prioritaire):
         - unset => on regarde le flag-file
         - "1"/"true"/"all"/"on" => ON
         - "0"/"false"/"off"/"no" => OFF
      2) Flag-file (modifiable pendant l'exécution):
         - chemin: SURVEY_SNAPSHOT_FLAG_FILE (sinon défaut)
         - contenu: "on"/"1"/"true" => ON, "off"/"0"/"false" => OFF
         - si le fichier n'existe pas => OFF

    Retourne le chemin du snapshot si créé.
    """
    import os
    from pathlib import Path

    # 1) ENV prioritaire
    v = (os.getenv("SURVEY_SNAPSHOT", "") or "").strip().lower()
    if v in ("1", "true", "all", "on", "yes"):
        return dump_page_snapshot(driver, reason=reason, question_blocks=question_blocks)
    if v in ("0", "false", "off", "no"):
        return None

    # 2) Flag-file hot-toggle
    is_local = os.getenv("RUN_ENV", "local") == "local"
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

        # Si contenu inconnu => OFF (safe)
        return None
    except Exception:
        # Si on ne peut pas lire le fichier => OFF (safe)
        return None
