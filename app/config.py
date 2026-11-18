"""Read-only application configuration. No network, registry or UAC on import."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    root: Path
    values: dict
    network: dict

    @classmethod
    def load(cls, root: Path | None = None) -> "Config":
        root = root or Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
        values = json.loads((root / "config/app.json").read_text(encoding="utf-8"))
        network = json.loads(
            (root / values["paths"]["network_config"]).read_text(encoding="utf-8")
        )
        return cls(root, values, network)

    def asset(self, key: str) -> Path:
        return self.root / self.values["paths"][key]

    def user_data(self) -> Path:
        raw = self.values["paths"]["user_data"]
        # Non-Windows is for UI tests only, never for optimization.
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(raw.replace("%APPDATA%", appdata))
        if sys.platform == "win32":
            raise RuntimeError("Не найдена папка данных пользователя APPDATA.")
        return Path.home() / ".local/share" / raw.replace("%APPDATA%/", "")
