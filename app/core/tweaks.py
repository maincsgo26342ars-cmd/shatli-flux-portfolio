"""Write-ahead apply/revert engine. No auth dependency: rollback is always local."""
from copy import deepcopy
from datetime import datetime, timezone
import logging
from pathlib import PureWindowsPath
import re

from app.core.catalog import BY_ID, desired_snapshot
from app.core.windows import UserError

STATES = {"prepared", "applied", "reverting", "reverted", "conflict"}
SCHEMA = 2


def now():
    return datetime.now(timezone.utc).isoformat()


def validate_locator(locator, operation):
    """A journal path must only point at the same kind of object it was written for."""
    if not isinstance(locator, dict):
        raise ValueError("invalid locator")
    if operation.kind == "power":
        if locator != {"kind": "power"}:
            raise ValueError("invalid locator")
        return
    if operation.kind == "file":
        if set(locator) != {"kind", "path"} or locator["kind"] != "file":
            raise ValueError("invalid locator")
        path = locator["path"]
        if not isinstance(path, str) or not path or len(path) > 4096 or "\x00" in path:
            raise ValueError("invalid locator")
        candidate = PureWindowsPath(path)
        if (not candidate.is_absolute() or candidate.name.lower() != "autoexec.cfg"
                or candidate.parent.name.lower() != "cfg"):
            raise ValueError("invalid locator")
        return
    if set(locator) != {"kind", "hive", "path", "name"} or locator["kind"] != "registry":
        raise ValueError("invalid locator")
    if locator["hive"] != operation.hive or locator["path"] != operation.path:
        raise ValueError("journal cannot redirect operations")
    name = locator["name"]
    if not isinstance(name, str) or not name or len(name) > 1024 or "\x00" in name:
        raise ValueError("invalid locator")
    # Only the two AppCompat/DirectX values are keyed by the cs2.exe path.
    if operation.id in {"cs2_fso", "cs2_gpu"}:
        candidate = PureWindowsPath(name)
        if not candidate.is_absolute() or candidate.name.lower() != "cs2.exe":
            raise ValueError("invalid locator")
    elif name != operation.name:
        raise ValueError("journal cannot redirect operations")


def validate_snapshot(snapshot, operation):
    if not isinstance(snapshot, dict) or set(snapshot) != {"exists", "type", "value"}:
        raise ValueError("invalid snapshot")
    if type(snapshot["exists"]) is not bool:
        raise ValueError("invalid existence marker")
    if not snapshot["exists"]:
        if snapshot["type"] is not None or snapshot["value"] is not None or operation.kind == "power":
            raise ValueError("invalid missing value")
        return
    if type(snapshot["type"]) is not type(operation.value_type) or snapshot["type"] != operation.value_type:
        raise ValueError("invalid registry type")
    value = snapshot["value"]
    if operation.value_type == 4:
        if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
            raise ValueError("invalid DWORD")
    elif operation.value_type == 1:
        if not isinstance(value, str) or len(value) > 65536:
            raise ValueError("invalid string")
    elif operation.value_type == "power_guid":
        if not isinstance(value, str) or not re.fullmatch(
            r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value
        ):
            raise ValueError("invalid GUID")
    elif operation.value_type == "managed_file":
        if not isinstance(value, str) or len(value.encode("utf-8")) > 1048576 or "\x00" in value:
            raise ValueError("invalid managed file")
    else:
        raise ValueError("unsupported snapshot type")


class TweakEngine:
    def __init__(self, store, backend):
        self.store, self.backend = store, backend

    def _load(self):
        data = self.store.load()
        owner = self.backend.identity()
        if data is None:
            return {"schema": SCHEMA, "owner": owner, "tweaks": {}, "events": [], "archives": []}
        if data.get("owner") != owner:
            raise UserError("Эта резервная копия принадлежит другому ПК или пользователю. Изменения запрещены.")
        try:
            if not isinstance(data["tweaks"], dict) or not isinstance(data["events"], list):
                raise ValueError("invalid journal")
            if not isinstance(data["archives"], list):
                raise ValueError("invalid history")
            for tweak_id, record in data["tweaks"].items():
                tweak = BY_ID[tweak_id]
                if record["id"] != tweak_id or len(record["changes"]) != len(tweak.operations):
                    raise ValueError("changed operation set")
                if record["status"] not in {"pending", "applied", "recovery_required", "reverted"}:
                    raise ValueError("unknown state")
                for entry, op in zip(record["changes"], tweak.operations, strict=True):
                    if entry["operation"] != op.id or entry["target"] != op.target:
                        raise ValueError("journal cannot redirect operations")
                    if entry["state"] not in STATES:
                        raise ValueError("changed desired state")
                    if "locator" in entry:
                        validate_locator(entry["locator"], op)
                    validate_snapshot(entry["new"], op)
                    validate_snapshot(entry["old"], op)
                    if entry["new"] != desired_snapshot(op, entry["old"]):
                        raise ValueError("changed desired state")
                states = [change["state"] for change in record["changes"]]
                if record["status"] == "reverted" and any(state != "reverted" for state in states):
                    raise ValueError("inconsistent reverted status")
                if record["status"] == "applied" and any(state != "applied" for state in states):
                    raise ValueError("inconsistent applied status")
        except (KeyError, TypeError, ValueError) as exc:
            raise UserError(
                "Структура backup.json не соответствует встроенному списку твиков. "
                "Файл сохранён без изменений; применять и откатывать по нему автоматически нельзя."
            ) from exc
        # Schema 1 journals are still readable; the next save upgrades them.
        data["schema"] = SCHEMA
        return data

    def journal(self):
        with self.store.locked():
            return deepcopy(self._load())

    def _save(self, data, tweak_id, event):
        data["events"].append({"at": now(), "tweak": tweak_id, "event": event})
        data["tweaks"][tweak_id]["updated_at"] = now()
        self.store.save(data)

    def _locate(self, operation):
        locate = getattr(self.backend, "locate", None)
        if locate is None:
            return None
        locator = locate(operation)
        try:
            validate_locator(locator, operation)
        except ValueError as exc:
            raise UserError("Не удалось зафиксировать путь настройки. Изменений нет.") from exc
        return locator

    def _read(self, operation, locator=None):
        if locator is None:
            return self.backend.read(operation)
        return self.backend.read(operation, locator)

    def _write(self, operation, snapshot, locator=None):
        if locator is None:
            self.backend.write(operation, snapshot)
        else:
            self.backend.write(operation, snapshot, locator)

    def _apply(self, data, tweak_id):
        tweak = BY_ID[tweak_id]
        previous = data["tweaks"].get(tweak_id)
        if previous and previous["status"] != "reverted":
            if previous["status"] == "applied" and all(
                self._read(op, entry.get("locator")) == entry["new"]
                for op, entry in zip(tweak.operations, previous["changes"], strict=True)
            ):
                return  # Idempotent. Preserve the first original values.
            raise UserError("Твик уже имеет изменения или незавершённую запись. Сначала выполните его откат.")
        snapshots, locators = [], []
        for op in tweak.operations:
            self.backend.check_supported(op)
            locator = self._locate(op)
            old = self._read(op, locator)
            try:
                validate_snapshot(old, op)
            except ValueError as exc:
                raise UserError("Тип исходной настройки не поддерживается. Изменений нет.") from exc
            snapshots.append(old)
            locators.append(locator)
        if previous:
            data["archives"].append(deepcopy(previous))
        changes = []
        for op, old, locator in zip(tweak.operations, snapshots, locators, strict=True):
            entry = {"operation": op.id, "target": op.target, "old": old,
                     "new": desired_snapshot(op, old), "state": "prepared"}
            if locator is not None:
                entry["locator"] = locator
            changes.append(entry)
        record = {
            "id": tweak.id, "title": tweak.title, "description": tweak.description,
            "group": tweak.group, "status": "pending", "created_at": now(),
            "changes": changes,
        }
        # Dict order is the rollback dependency order. Re-applying an older
        # entry must move it after settings applied since its last rollback.
        data["tweaks"].pop(tweak_id, None)
        data["tweaks"][tweak_id] = record
        self._save(data, tweak_id, "apply-prepared")  # MUST precede every system write.
        pairs = list(zip(tweak.operations, record["changes"], strict=True))
        if hasattr(self.backend, "write_group"):
            for op, entry in pairs:
                if self._read(op, entry.get("locator")) != entry["old"]:
                    entry["state"] = "conflict"
                    record["status"] = "recovery_required"
                    self._save(data, tweak_id, "external-change-before-write")
                    raise UserError("Настройку изменили во время операции. Выполните откат.")
            self.backend.write_group([
                (op, entry["old"], entry["new"], entry.get("locator")) for op, entry in pairs
            ])
            for op, entry in pairs:
                entry["state"] = "applied"
                self._save(data, tweak_id, f"applied:{op.id}")
        else:
            for op, entry in pairs:
                if self._read(op, entry.get("locator")) != entry["old"]:
                    entry["state"] = "conflict"
                    record["status"] = "recovery_required"
                    self._save(data, tweak_id, "external-change-before-write")
                    raise UserError("Настройку изменили во время операции. Выполните откат.")
                if entry["old"] != entry["new"]:
                    self._write(op, entry["new"], entry.get("locator"))
                if self._read(op, entry.get("locator")) != entry["new"]:
                    raise UserError("Windows не подтвердила новое значение. Исходные данные сохранены для отката.")
                entry["state"] = "applied"
                self._save(data, tweak_id, f"applied:{op.id}")
        record["status"] = "applied"
        self._save(data, tweak_id, "apply-complete")

    def _revert(self, data, tweak_id):
        record = data["tweaks"].get(tweak_id)
        if not record or record["status"] == "reverted":
            return
        errors = []
        pairs = list(zip(BY_ID[tweak_id].operations, record["changes"], strict=True))
        record["status"] = "recovery_required"
        self._save(data, tweak_id, "revert-start")
        for op, entry in reversed(pairs):
            # Already restored values must never be overwritten on retry.
            if entry["state"] == "reverted":
                continue
            try:
                locator = entry.get("locator")
                current = self._read(op, locator)
                if current != entry["old"] and current != entry["new"]:
                    entry["state"] = "conflict"
                    self._save(data, tweak_id, f"conflict:{op.id}")
                    errors.append(f"{op.name}: обнаружено внешнее изменение")
                    continue
                if current != entry["old"]:
                    entry["state"] = "reverting"
                    self._save(data, tweak_id, f"revert-prepared:{op.id}")
                    self._write(op, entry["old"], locator)
                    if self._read(op, locator) != entry["old"]:
                        raise UserError("Windows не подтвердила восстановленное значение.")
                entry["state"] = "reverted"
                self._save(data, tweak_id, f"reverted:{op.id}")
            except (UserError, PermissionError) as exc:
                logging.getLogger("shatli_flux").warning("Откат %s: %s", op.id, exc)
                errors.append(f"{op.name}: {exc}")
        if not errors:
            record["status"] = "reverted"
            self._save(data, tweak_id, "revert-complete")
        else:
            self._save(data, tweak_id, "revert-incomplete")
            raise UserError(
                "Откат выполнен не полностью. Чужие изменения не перезаписаны, резервная копия сохранена.\n"
                + "\n".join(errors)
            )

    def apply_many(self, ids, progress=lambda *_: None):
        ids = list(dict.fromkeys(ids))
        if not ids or any(item not in BY_ID for item in ids):
            raise UserError("Выберите хотя бы один доступный твик.")
        with self.store.locked():
            data = self._load()
            active = {key for key, record in data["tweaks"].items()
                      if record["status"] != "reverted"}
            requested = active | set(ids)
            for tweak_id in ids:
                conflicts = set(BY_ID[tweak_id].excludes) & requested
                conflicts.update(key for key in requested if tweak_id in BY_ID[key].excludes)
                if conflicts:
                    titles = ", ".join(BY_ID[key].title for key in sorted(conflicts))
                    raise UserError("Несовместимые настройки: " + titles
                                    + ". Сначала снимите выбор или выполните откат.")
            for index, tweak_id in enumerate(ids):
                progress(round(index * 100 / len(ids)), "Применение: " + BY_ID[tweak_id].title)
                try:
                    self._apply(data, tweak_id)
                except Exception:
                    logging.getLogger("shatli_flux").exception("Применение не завершено: %s", tweak_id)
                    raise
            progress(100, "Применение завершено.")
        return "Применение подтверждено. Для настроек с соответствующим тегом нужен перезапуск."

    def revert_many(self, ids=None, progress=lambda *_: None):
        errors = []
        with self.store.locked():
            data = self._load()
            selected = list(ids) if ids is not None else list(data["tweaks"])
            selected = list(dict.fromkeys(selected))
            if any(item not in BY_ID for item in selected):
                raise UserError("Неизвестный твик. Автоматический откат остановлен.")
            for index, tweak_id in enumerate(reversed(selected)):
                progress(round(index * 100 / max(1, len(selected))), "Откат: " + BY_ID[tweak_id].title)
                try:
                    self._revert(data, tweak_id)
                except UserError as exc:
                    errors.append(str(exc))
            progress(100, "Проверка отката завершена.")
        if errors:
            raise UserError("\n".join(errors))
        return "Исходные значения восстановлены. Для некоторых настроек нужен перезапуск."

    def describe(self, tweak_id):
        with self.store.locked():
            data = self._load()
            tweak = BY_ID[tweak_id]
            record = data["tweaks"].get(tweak_id)
            if record:
                current = [self._read(op, entry.get("locator"))
                           for op, entry in zip(tweak.operations, record["changes"], strict=True)]
            else:
                current = [self._read(op, self._locate(op)) for op in tweak.operations]
            return {
                "journal": deepcopy(record),
                "current": current,
            }
