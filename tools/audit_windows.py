"""Read-only snapshots for VM acceptance. Never applies or reverts tweaks.

python tools/audit_windows.py > before.json
python tools/audit_windows.py > after.json
python tools/audit_windows.py --compare before.json after.json
"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Config
from app.core.catalog import CATALOG
from app.core.windows import require_windows
from app.core.windows_backend import WindowsBackend


def main():
    if len(sys.argv) == 4 and sys.argv[1] == "--compare":
        before = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8-sig"))
        after = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8-sig"))
        if any("error" in entry for entries in (before, after) for entry in entries.values()):
            print("Нельзя подтвердить сравнение: есть ошибки чтения параметров.")
            return 2
        differences = [key for key in before.keys() | after.keys() if before.get(key) != after.get(key)]
        print("Различия: " + ", ".join(differences) if differences else "Исходные значения совпадают.")
        return 1 if differences else 0
    require_windows()
    backend = WindowsBackend(Config.load())
    report = {}
    for tweak in CATALOG:
        for operation in tweak.operations:
            try:
                report[operation.id] = {"target": operation.target, "state": backend.read(operation)}
            except Exception as exc:
                report[operation.id] = {"target": operation.target, "error": str(exc)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if any("error" in entry for entry in report.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
