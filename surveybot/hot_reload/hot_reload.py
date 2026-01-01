# tools/hot_reload.py
from __future__ import annotations
import importlib, sys, time, os
from pathlib import Path
from typing import Dict, List, Optional


class ModuleReloader:
    """
    Surveille des modules (par nom) et les reload quand leur fichier .py change.
    Utilisation:
        r = ModuleReloader(["action_dispatcher","input_handler",...])
        changed = r.reload_changed()
        if changed: ... # réutiliser les nouveaux objets de module
    """

    def __init__(self, module_names: List[str], poll_interval: float = 0.5):
        self.module_names = module_names
        self.poll_interval = poll_interval
        self.paths: Dict[str, Path] = {}
        self.mtimes: Dict[str, float] = {}

        for name in module_names:
            mod = self._ensure_imported(name)
            path = self._module_path(mod)
            if path:
                self.paths[name] = path
                self.mtimes[name] = self._safe_mtime(path)

    def _ensure_imported(self, name: str):
        if name in sys.modules and sys.modules[name] is not None:
            return sys.modules[name]
        return importlib.import_module(name)

    def _module_path(self, mod) -> Optional[Path]:
        file = getattr(mod, "__file__", None)
        if not file:
            return None
        p = Path(file)
        # si c'est un .pyc → prendre le .py
        if p.suffix == ".pyc":
            py = p.with_suffix(".py")
            if py.exists():
                return py
        return p

    def _safe_mtime(self, path: Path) -> float:
        try:
            return path.stat().st_mtime
        except Exception:
            return 0.0

    def reload_changed(self) -> Dict[str, object]:
        """Recharge les modules modifiés. Retourne dict {name: module} rechargés."""
        reloaded: Dict[str, object] = {}
        for name in self.module_names:
            mod = self._ensure_imported(name)
            path = self.paths.get(name) or self._module_path(mod)
            if not path or not path.exists():
                continue
            new_mtime = self._safe_mtime(path)
            old_mtime = self.mtimes.get(name, 0.0)
            if new_mtime > old_mtime:
                mod = importlib.reload(mod)
                self.mtimes[name] = new_mtime
                reloaded[name] = mod
        return reloaded

    def watch_loop(self, on_change):
        """
        Boucle bloquante : quand un module change, on le reload et on appelle on_change(modules_reloaded).
        """
        try:
            while True:
                changed = self.reload_changed()
                if changed:
                    on_change(changed)
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            pass
