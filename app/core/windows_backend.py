"""Windows adapter: fixed registry operations, power plan and managed CS2 file."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid

from app.core.windows import UserError, executable, require_windows, run_command, run_elevated

GUID = re.compile(rb"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b")


class WindowsBackend:
    def __init__(self, config):
        self.config = config
        self._identity = None
        self.cs2_autoexec = None
        self.cs2_executable = None

    def configure_cs2(self, *, autoexec=None, executable_path=None):
        if autoexec:
            path = Path(autoexec)
            if not path.is_absolute() or path.name.lower() != "autoexec.cfg" or path.parent.name.lower() != "cfg":
                raise UserError("Выберите autoexec.cfg внутри папки cfg вашей установки CS2.")
            self.cs2_autoexec = path
        if executable_path:
            path = Path(executable_path)
            if not path.is_absolute() or path.name.lower() != "cs2.exe":
                raise UserError("Выберите исполняемый файл cs2.exe.")
            self.cs2_executable = path

    def locate(self, operation):
        """Pin the real file/registry value used by this operation.

        A journal must store this locator: changing the selected autoexec.cfg or
        cs2.exe later must never redirect an old rollback into another file.
        """
        if operation.kind == "power":
            return {"kind": "power"}
        if operation.kind == "file":
            return {"kind": "file", "path": str(self._file_path(operation))}
        return {"kind": "registry", "hive": operation.hive,
                "path": operation.path, "name": self._name(operation)}

    def identity(self):
        require_windows()
        if self._identity is None:
            import winreg
            result = run_command([executable(self.config, "whoami"), "/user", "/fo", "csv", "/nh"])
            sid = re.search(rb"\bS-1-(?:\d+-)+\d+\b", result.stdout)
            if result.returncode or not sid:
                raise UserError("Не удалось определить владельца резервной копии. Изменений нет.")
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography",
                                0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                machine = winreg.QueryValueEx(key, "MachineGuid")[0]
            self._identity = hashlib.sha256(sid.group() + b"|" + machine.encode("utf-8")).hexdigest()
        return self._identity

    def _power(self, *arguments):
        result = run_command([executable(self.config, "powercfg"), *arguments], timeout=20)
        if result.returncode:
            raise UserError("Windows не разрешила операцию с планом питания. Проверьте наличие схемы и права.")
        return result.stdout

    def _file_path(self, operation):
        if not self.cs2_autoexec:
            raise UserError("Сначала выберите autoexec.cfg в разделе «Профиль» → «Файлы CS2».")
        return self.cs2_autoexec

    def _name(self, operation):
        if operation.id == "cs2_fso" or operation.id == "cs2_gpu":
            if not self.cs2_executable:
                raise UserError("Сначала выберите cs2.exe в разделе «Профиль» → «Файлы CS2».")
            return str(self.cs2_executable)
        return operation.name

    def _actual(self, operation, snapshot, name=None):
        if operation.id == "cs2_gpu" and snapshot["exists"] and snapshot["value"] == operation.value:
            return dict(snapshot, value=f"{name or self._name(operation)};GpuPreference=2;")
        return snapshot

    def check_supported(self, operation):
        require_windows()
        if sys.getwindowsversion().build < operation.min_build:
            raise UserError("Эта настройка требует более новую версию Windows. Изменений нет.")
        if operation.kind == "power":
            choices = [item.decode("ascii").lower() for item in GUID.findall(self._power("/list"))]
            if operation.value not in choices:
                raise UserError("План «Высокая производительность» не найден. Новые схемы не создавались.")
        elif operation.kind == "file":
            self._file_path(operation)
        else:
            self.read(operation)

    def read(self, operation, locator=None):
        require_windows()
        if operation.kind == "power":
            value = GUID.search(self._power("/getactivescheme"))
            if not value:
                raise UserError("Windows не сообщила текущий план питания.")
            return {"exists": True, "type": "power_guid", "value": value.group().decode("ascii").lower()}
        if operation.kind == "file":
            path = Path(locator["path"]) if locator else self._file_path(operation)
            try:
                raw = path.read_bytes()
                if len(raw) > 1048576:
                    raise UserError("autoexec.cfg больше 1 МБ. Файл не изменён.")
                value = raw.decode("utf-8")
            except FileNotFoundError:
                return {"exists": False, "type": None, "value": None}
            except UnicodeError as exc:
                raise UserError("autoexec.cfg должен быть текстовым файлом UTF-8. Изменений нет.") from exc
            if "\x00" in value:
                raise UserError("autoexec.cfg содержит недопустимые данные. Изменений нет.")
            return {"exists": True, "type": "managed_file", "value": value}
        import winreg
        name = locator["name"] if locator else self._name(operation)
        hive = winreg.HKEY_LOCAL_MACHINE if operation.hive == "HKLM" else winreg.HKEY_CURRENT_USER
        try:
            key = winreg.OpenKey(hive, operation.path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
        except FileNotFoundError as exc:
            if operation.create_key:
                return {"exists": False, "type": None, "value": None}
            raise UserError("Параметр недоступен в этой конфигурации Windows. Отсутствующий раздел не создавался.") from exc
        with key:
            try:
                value, kind = winreg.QueryValueEx(key, name)
            except FileNotFoundError:
                return {"exists": False, "type": None, "value": None}
            if kind != operation.value_type:
                raise UserError("Обнаружен неожиданный тип настройки. Изменения остановлены.")
            result = {"exists": True, "type": kind, "value": value}
            if operation.id == "cs2_gpu" and value == self._actual(operation, operation.new, name)["value"]:
                return operation.new
            return result

    def _write_registry(self, operation, snapshot, locator=None):
        import winreg
        name = locator["name"] if locator else self._name(operation)
        hive = winreg.HKEY_LOCAL_MACHINE if operation.hive == "HKLM" else winreg.HKEY_CURRENT_USER
        access = winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE | winreg.KEY_WOW64_64KEY
        snapshot = self._actual(operation, snapshot, name)
        if operation.create_key:
            key = winreg.CreateKeyEx(hive, operation.path, 0, access)
        else:
            key = winreg.OpenKey(hive, operation.path, 0, access)
        with key:
            if snapshot["exists"]:
                winreg.SetValueEx(key, name, 0, snapshot["type"], snapshot["value"])
            else:
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
            winreg.FlushKey(key)

    def _elevated_group(self, pairs):
        import ctypes
        if ctypes.windll.shell32.IsUserAnAdmin():
            for operation, expected, desired, locator in pairs:
                if self.read(operation, locator) != expected:
                    raise UserError("Настройка изменилась до записи. Операция остановлена.")
            for operation, _, desired, locator in pairs:
                self._write_registry(operation, desired, locator)
            return
        folder = self.config.user_data()
        folder.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        job_path, result_path = folder / f"admin-{token}.json", folder / f"admin-{token}.result.json"
        # HKLM operations have static value names, so the elevated worker can
        # re-resolve them from the allowlist without a caller-provided locator.
        job = {"result": str(result_path), "writes": [
            {"id": op.id, "expected": expected, "desired": desired}
            for op, expected, desired, _ in pairs]}
        job_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        try:
            if getattr(sys, "frozen", False):
                exe, arguments = sys.executable, ["--admin-operations", str(job_path)]
            else:
                exe, arguments = sys.executable, ["-m", "app.core.admin_operations", str(job_path)]
            if run_elevated(exe, arguments) != 0:
                try:
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                    message = result.get("error")
                except (OSError, ValueError, AttributeError):
                    message = None
                raise UserError(message or "Операция администратора не завершена. Журнал сохранён для отката.")
            if not result_path.is_file():
                raise UserError("Операция администратора не вернула результат. Журнал сохранён.")
        finally:
            job_path.unlink(missing_ok=True)
            result_path.unlink(missing_ok=True)

    def write_group(self, pairs):
        """One UAC request per tweak; expected values are rechecked by the helper.

        Pairs are ``(operation, expected, desired)`` or, with a pinned journal
        path, ``(operation, expected, desired, locator)``.
        """
        normalized = [
            (pair[0], pair[1], pair[2], pair[3] if len(pair) > 3 else None)
            for pair in pairs
        ]
        for operation, expected, desired, locator in normalized:
            if not operation.requires_admin:
                if self.read(operation, locator) != expected:
                    raise UserError("Настройка изменилась до записи. Операция остановлена.")
                if expected != desired:
                    self.write(operation, desired, locator)
        elevated = [pair for pair in normalized if pair[0].requires_admin]
        if elevated:
            self._elevated_group(elevated)
        for operation, _, desired, locator in normalized:
            if self.read(operation, locator) != desired:
                raise UserError("Windows не подтвердила новое значение. Исходные данные сохранены для отката.")

    def write(self, operation, snapshot, locator=None):
        require_windows()
        if operation.kind == "power":
            value = snapshot["value"]
            choices = [item.decode("ascii").lower() for item in GUID.findall(self._power("/list"))]
            if value not in choices:
                raise UserError("Нужный для восстановления план питания был удалён. Журнал сохранён.")
            self._power("/setactive", value)
            return
        if operation.kind == "file":
            path = Path(locator["path"]) if locator else self._file_path(operation)
            path.parent.mkdir(parents=True, exist_ok=True)
            if not snapshot["exists"]:
                path.unlink(missing_ok=True)
                return
            temporary = path.with_name(f".shatli-flux-{uuid.uuid4().hex}.tmp")
            try:
                with temporary.open("wb") as handle:
                    handle.write(snapshot["value"].encode("utf-8"))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            return
        if operation.requires_admin:
            self._elevated_group([(operation, self.read(operation, locator), snapshot, locator)])
            return
        self._write_registry(operation, snapshot, locator)
