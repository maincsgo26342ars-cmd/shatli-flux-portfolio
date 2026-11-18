"""Small elevated registry worker. Accepts only IDs from the built-in catalog."""
import json
from pathlib import Path
import sys

from app.core.catalog import BY_ID
from app.core.tweaks import validate_snapshot
from app.core.windows import UserError


def perform(job_path):
    path = Path(job_path)
    job = json.loads(path.read_text(encoding="utf-8"))
    if set(job) != {"writes", "result"} or not isinstance(job["writes"], list) or not job["writes"]:
        raise UserError("Некорректное задание администратора.")
    result_path = Path(job["result"])
    operations = {op.id: op for tweak in BY_ID.values() for op in tweak.operations if op.requires_admin}
    pairs = []
    for item in job["writes"]:
        if set(item) != {"id", "expected", "desired"}:
            raise UserError("Некорректное задание администратора.")
        op = operations[item["id"]]
        validate_snapshot(item["expected"], op)
        validate_snapshot(item["desired"], op)
        pairs.append((op, item["expected"], item["desired"]))
    import winreg

    def read(op):
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, op.path, 0,
                                 winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
        except FileNotFoundError:
            if op.create_key:
                return {"exists": False, "type": None, "value": None}
            raise
        with key:
            try:
                value, kind = winreg.QueryValueEx(key, op.name)
            except FileNotFoundError:
                return {"exists": False, "type": None, "value": None}
            if kind != op.value_type:
                raise UserError("Обнаружен неожиданный тип настройки.")
            return {"exists": True, "type": kind, "value": value}

    for op, expected, _ in pairs:
        if read(op) != expected:
            raise UserError("Настройка изменилась до операции администратора. Запись отменена.")
    for op, _, desired in pairs:
        if read(op) == desired:
            continue
        access = winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE | winreg.KEY_WOW64_64KEY
        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, op.path, 0, access) as key:
            if desired["exists"]:
                winreg.SetValueEx(key, op.name, 0, desired["type"], desired["value"])
            else:
                try:
                    winreg.DeleteValue(key, op.name)
                except FileNotFoundError:
                    pass
            winreg.FlushKey(key)
    for op, _, desired in pairs:
        if read(op) != desired:
            raise UserError("Windows не подтвердила значение. Журнал сохранён для отката.")
    result_path.write_text(json.dumps({"ok": True}), encoding="utf-8")


def main():
    if len(sys.argv) != 2:
        return 2
    try:
        perform(sys.argv[1])
        return 0
    except Exception as exc:
        try:
            job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
            Path(job["result"]).write_text(json.dumps({"ok": False, "error": str(exc)[:500]}), encoding="utf-8")
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
