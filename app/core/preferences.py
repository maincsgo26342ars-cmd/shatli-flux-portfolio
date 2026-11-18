"""Durable non-secret preferences, separate from the tweak backup."""
import json
import os
from copy import deepcopy
from pathlib import Path
import tempfile

from app.core.catalog import BY_ID

DEFAULTS = {"schema": 1, "selected_tweaks": [],
            "cs2_autoexec": None, "cs2_executable": None,
            "window_geometry": None, "maximized": False}


class Preferences:
    def __init__(self, path):
        self.path = Path(path)
        self.data = self.load()

    def load(self):
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("schema") != 1:
                raise ValueError
            result = deepcopy(DEFAULTS)
            selected = value.get("selected_tweaks", [])
            result["selected_tweaks"] = [x for x in selected if isinstance(x, str) and x in BY_ID] if isinstance(selected, list) else []
            for key in ("cs2_autoexec", "cs2_executable"):
                item = value.get(key)
                result[key] = item if isinstance(item, str) and len(item) <= 1024 else None
            geometry = value.get("window_geometry")
            if isinstance(geometry, str) and 16 <= len(geometry) <= 512 and all(
                    character.isalnum() or character in "+/=" for character in geometry):
                result["window_geometry"] = geometry
            result["maximized"] = value.get("maximized") is True
            return result
        except (OSError, ValueError, TypeError):
            return deepcopy(DEFAULTS)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".preferences-", suffix=".tmp",
                                                dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def update(self, **values):
        previous = self.data
        self.data = {**previous, **values}
        try:
            self.save()
        except Exception:
            self.data = previous
            raise
