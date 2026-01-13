# Survey/page_snapshot.py
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

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
    folder_name = f"{ts}_{_slug(reason)}"

    # Dossier par défaut:
    # - local: ./snapshots
    # - prod/docker: /tmp/snapshots (évite d’écrire dans l’image)
    is_local = os.getenv("RUN_ENV", "local") == "local"
    default_root = "./snapshots" if is_local else "/tmp/snapshots"

    root = Path(out_root or os.getenv("SURVEY_SNAPSHOT_DIR", default_root))
    folder = root / folder_name
    folder.mkdir(parents=True, exist_ok=True)

    # Meta
    try:
        url = driver.current_url
    except Exception:
        url = ""

    try:
        title = driver.execute_script("return document.title") or ""
    except Exception:
        title = ""

    meta = {
        "ts": ts,
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

    # page_source (parfois différent du DOM live, mais utile)
    try:
        src = driver.page_source or ""
    except Exception:
        src = ""
    (folder / "page_source.html").write_text(src, encoding="utf-8", errors="ignore")

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
