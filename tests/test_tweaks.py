"""Destructive-failure simulation against an in-memory backend, never real Windows."""
from copy import deepcopy
import json

import pytest

from app.core.backup_store import BackupStore
from app.core.catalog import CATALOG, BY_ID
from app.core.tweaks import TweakEngine
from app.core.windows import UserError


class FakeBackend:
    def __init__(self, store):
        self.store = store
        self.owner = "test-owner"
        self.values = {}
        self.writes = []
        self.fail_at = None
        self.fail_after_write = False
        self.unsupported = set()
        for tweak in CATALOG:
            for op in tweak.operations:
                value = ("381b4222-f694-41f0-9685-ff5bb260df2e" if op.kind == "power"
                         else "sensitivity 2.5\r\n" if op.kind == "file"
                         else "6" if op.value_type == 1 else 1)
                self.values[op.id] = {"exists": True, "type": op.value_type, "value": value}

    def identity(self):
        return self.owner

    def check_supported(self, op):
        if op.id in self.unsupported:
            raise UserError("Параметр отсутствует.")

    def read(self, op):
        return deepcopy(self.values[op.id])

    def write(self, op, snapshot):
        # Every attempted write must already have a flushed on-disk backup.
        data = json.loads(self.store.path.read_text(encoding="utf-8"))
        entries = [change for record in data["tweaks"].values() for change in record["changes"]
                   if change["operation"] == op.id]
        assert len(entries) == 1
        assert entries[0]["state"] in ("prepared", "reverting")
        self.writes.append((op.id, deepcopy(snapshot)))
        should_fail = self.fail_at == len(self.writes)
        if should_fail and not self.fail_after_write:
            raise PermissionError("simulated denied")
        self.values[op.id] = deepcopy(snapshot)
        if should_fail and self.fail_after_write:
            raise OSError("simulated crash after write")


@pytest.fixture
def setup(tmp_path):
    store = BackupStore(tmp_path / "backup.json", tmp_path / "backup.lock")
    backend = FakeBackend(store)
    engine = TweakEngine(store, backend)
    return engine, backend, store


@pytest.mark.parametrize("tweak_id", list(BY_ID))
def test_apply_revert_roundtrip(setup, tweak_id):
    engine, backend, store = setup
    original = deepcopy(backend.values)
    engine.apply_many([tweak_id])
    assert engine.journal()["tweaks"][tweak_id]["status"] == "applied"
    engine.revert_many([tweak_id])
    assert backend.values == original
    assert engine.journal()["tweaks"][tweak_id]["status"] == "reverted"


def test_repeat_apply_preserves_first_snapshot(setup):
    engine, backend, _ = setup
    engine.apply_many(["mouse_acceleration_off"])
    first = engine.journal()
    count = len(backend.writes)
    engine.apply_many(["mouse_acceleration_off"])
    assert engine.journal() == first
    assert len(backend.writes) == count


def test_conflicting_batch_rejected_before_any_write(setup):
    engine, backend, store = setup
    with pytest.raises(UserError, match="Несовместимые"):
        engine.apply_many(["transparency_off", "cs2_fps_400", "cs2_fps_unlimited"])
    assert backend.writes == []
    assert not store.path.exists()


def test_conflict_with_active_tweak_requires_revert(setup):
    engine, backend, store = setup
    engine.apply_many(["cs2_fps_400"])
    original = store.path.read_bytes()
    count = len(backend.writes)
    with pytest.raises(UserError, match="Несовместимые"):
        engine.apply_many(["cs2_fps_unlimited"])
    assert store.path.read_bytes() == original
    assert len(backend.writes) == count
    engine.revert_many()
    engine.apply_many(["cs2_fps_unlimited"])


def test_missing_value_is_deleted_on_revert(setup):
    engine, backend, _ = setup
    backend.values["advertising_id"] = {"exists": False, "type": None, "value": None}
    engine.apply_many(["advertising_id_off"])
    engine.revert_many()
    assert backend.values["advertising_id"] == {"exists": False, "type": None, "value": None}


def test_already_equal_does_not_write(setup):
    engine, backend, _ = setup
    backend.values["transparency"]["value"] = 0
    engine.apply_many(["transparency_off"])
    engine.revert_many()
    assert backend.writes == []


def test_backup_failure_prevents_system_write(setup, monkeypatch):
    engine, backend, store = setup
    def broken(_):
        raise OSError("disk full")
    monkeypatch.setattr(store, "save", broken)
    with pytest.raises(OSError):
        engine.apply_many(["mouse_acceleration_off"])
    assert backend.writes == []
    assert not store.path.exists()


@pytest.mark.parametrize("fail_at", [1, 2, 3])
@pytest.mark.parametrize("after_write", [False, True])
def test_partial_apply_recoverable_after_restart(setup, fail_at, after_write):
    engine, backend, store = setup
    original = deepcopy(backend.values)
    backend.fail_at = fail_at
    backend.fail_after_write = after_write
    with pytest.raises(OSError):
        engine.apply_many(["mouse_acceleration_off"])
    backend.fail_at = None
    restarted = TweakEngine(store, backend)
    with pytest.raises(UserError, match="Сначала"):
        restarted.apply_many(["mouse_acceleration_off"])
    restarted.revert_many()
    assert backend.values == original


def test_failure_to_persist_after_write_still_recoverable(setup, monkeypatch):
    engine, backend, store = setup
    original = deepcopy(backend.values)
    save = store.save
    calls = 0
    def fail_second(data):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk full after mutation")
        save(data)
    monkeypatch.setattr(store, "save", fail_second)
    with pytest.raises(OSError):
        engine.apply_many(["mouse_acceleration_off"])
    assert len(backend.writes) == 1
    monkeypatch.setattr(store, "save", save)
    engine.revert_many()
    assert backend.values == original


def test_external_change_is_not_overwritten(setup):
    engine, backend, _ = setup
    engine.apply_many(["mouse_acceleration_off"])
    backend.values["mouse_mousespeed"]["value"] = "99"
    with pytest.raises(UserError, match="не полностью"):
        engine.revert_many()
    assert backend.values["mouse_mousespeed"]["value"] == "99"
    assert backend.values["mouse_mousethreshold1"]["value"] == "6"
    assert backend.values["mouse_mousethreshold2"]["value"] == "6"
    assert engine.journal()["tweaks"]["mouse_acceleration_off"]["status"] == "recovery_required"


def test_repeat_revert_does_not_overwrite_later_changes(setup):
    engine, backend, _ = setup
    engine.apply_many(["transparency_off"])
    engine.revert_many()
    backend.values["transparency"]["value"] = 3
    engine.revert_many()
    assert backend.values["transparency"]["value"] == 3


def test_apply_after_revert_creates_new_snapshot_and_archive(setup):
    engine, backend, _ = setup
    engine.apply_many(["transparency_off"])
    engine.revert_many()
    backend.values["transparency"]["value"] = 3
    engine.apply_many(["transparency_off"])
    data = engine.journal()
    assert len(data["archives"]) == 1
    assert data["tweaks"]["transparency_off"]["changes"][0]["old"]["value"] == 3
    engine.revert_many()
    assert backend.values["transparency"]["value"] == 3


def test_foreign_owner_denied(setup):
    engine, backend, store = setup
    engine.apply_many(["transparency_off"])
    backup_bytes = store.path.read_bytes()
    backend.owner = "other-user-or-machine"
    with pytest.raises(UserError, match="другому"):
        engine.revert_many()
    assert store.path.read_bytes() == backup_bytes


def test_corrupt_journal_is_not_reset(setup):
    engine, backend, store = setup
    store.path.write_bytes(b"{broken")
    with pytest.raises(UserError):
        engine.apply_many(["transparency_off"])
    assert store.path.read_bytes() == b"{broken"
    assert not backend.writes


@pytest.mark.parametrize("mutation", ["target", "new", "type", "unknown", "status"])
def test_tampered_operation_denied(setup, mutation):
    engine, backend, store = setup
    engine.apply_many(["transparency_off"])
    with store.locked():
        data = store.load()
        record = data["tweaks"]["transparency_off"]
        entry = record["changes"][0]
        if mutation == "target":
            entry["target"] = "HKLM\\other"
        elif mutation == "new":
            entry["new"]["value"] = 42
        elif mutation == "type":
            entry["old"]["type"] = 1
        elif mutation == "unknown":
            data["tweaks"]["other"] = data["tweaks"].pop("transparency_off")
        else:
            record["status"] = "reverted"
        store.save(data)
    before = len(backend.writes)
    with pytest.raises(UserError):
        engine.revert_many()
    assert len(backend.writes) == before


def test_lock_blocks_second_engine(setup):
    engine, backend, store = setup
    other = TweakEngine(BackupStore(store.path, store.lock_path), backend)
    with store.locked():
        with pytest.raises(UserError, match="Другая"):
            other.apply_many(["transparency_off"])
    assert not backend.writes


def test_missing_scheme_does_not_create_or_change_anything(setup):
    engine, backend, store = setup
    backend.unsupported.add("power_scheme")
    with pytest.raises(UserError):
        engine.apply_many(["high_performance"])
    assert not backend.writes
    assert not store.path.exists()


def test_group_partial_failure_is_journaled_and_revert_all_available(setup):
    engine, backend, _ = setup
    original = deepcopy(backend.values)
    backend.fail_at = 2
    with pytest.raises(PermissionError):
        engine.apply_many(["transparency_off", "advertising_id_off"])
    assert engine.journal()["tweaks"]["transparency_off"]["status"] == "applied"
    backend.fail_at = None
    engine.revert_many()
    assert backend.values == original


def test_full_custom_group_and_reverse_revert(setup):
    engine, backend, _ = setup
    original = deepcopy(backend.values)
    progress = []
    compatible = [key for key in BY_ID if key != "cs2_fps_unlimited"]
    engine.apply_many(compatible, lambda value, text: progress.append(value))
    assert progress[-1] == 100
    engine.revert_many()
    assert backend.values == original


def test_crash_during_rollback_recoverable(setup):
    engine, backend, store = setup
    original = deepcopy(backend.values)
    engine.apply_many(["mouse_acceleration_off"])
    backend.fail_at = len(backend.writes) + 2
    backend.fail_after_write = True
    with pytest.raises(OSError):
        engine.revert_many()
    backend.fail_at = None
    TweakEngine(store, backend).revert_many()
    assert backend.values == original


def test_unsupported_original_type_prevents_write(setup):
    engine, backend, _ = setup
    backend.values["transparency"]["type"] = 1
    backend.values["transparency"]["value"] = "1"
    with pytest.raises(UserError):
        engine.apply_many(["transparency_off"])
    assert not backend.writes


def test_no_auth_required_for_revert(setup):
    engine, _, _ = setup
    engine.apply_many(["transparency_off"])
    # Engine has no auth, network, subscription or preset-service dependency.
    assert "восстановлены" in engine.revert_many()


class LocatorBackend(FakeBackend):
    """Adds path pinning on top of the in-memory backend."""

    def __init__(self, store):
        super().__init__(store)
        self.file_path = r"C:\Games\one\cfg\autoexec.cfg"
        self.seen = []

    def locate(self, op):
        if op.kind == "file":
            return {"kind": "file", "path": self.file_path}
        if op.kind == "power":
            return {"kind": "power"}
        return {"kind": "registry", "hive": op.hive, "path": op.path, "name": op.name}

    def read(self, op, locator=None):
        self.seen.append(("read", op.id, None if locator is None
                          else locator.get("path") or locator.get("name")))
        return super().read(op)

    def write(self, op, snapshot, locator=None):
        self.seen.append(("write", op.id, None if locator is None
                          else locator.get("path") or locator.get("name")))
        return super().write(op, snapshot)


def test_journal_pins_file_path_after_selection_changes(tmp_path):
    store = BackupStore(tmp_path / "backup.json", tmp_path / "backup.lock")
    backend = LocatorBackend(store)
    engine = TweakEngine(store, backend)
    engine.apply_many(["cs2_fps_400"])
    data = engine.journal()
    assert data["schema"] == 2
    assert data["tweaks"]["cs2_fps_400"]["changes"][0]["locator"] == {
        "kind": "file", "path": backend.file_path}
    backend.file_path = r"C:\Games\two\cfg\autoexec.cfg"
    backend.seen.clear()
    engine.revert_many(["cs2_fps_400"])
    touched = {path for _, _, path in backend.seen if path}
    assert touched == {r"C:\Games\one\cfg\autoexec.cfg"}


def test_tampered_locator_is_not_used(setup):
    engine, backend, store = setup
    engine.apply_many(["transparency_off"])
    op = BY_ID["transparency_off"].operations[0]
    with store.locked():
        data = store.load()
        data["tweaks"]["transparency_off"]["changes"][0]["locator"] = {
            "kind": "registry", "hive": "HKCU", "path": op.path, "name": "OtherValue"}
        store.save(data)
    before = len(backend.writes)
    with pytest.raises(UserError):
        engine.revert_many()
    assert len(backend.writes) == before


@pytest.mark.parametrize("locator", [
    {"kind": "file", "path": "relative/cfg/autoexec.cfg"},
    {"kind": "file", "path": r"C:\Games\cfg\other.cfg"},
    {"kind": "power"},
])
def test_invalid_file_locator_is_rejected(tmp_path, locator):
    store = BackupStore(tmp_path / "backup.json", tmp_path / "backup.lock")
    backend = LocatorBackend(store)
    engine = TweakEngine(store, backend)
    engine.apply_many(["cs2_fps_400"])
    with store.locked():
        data = store.load()
        data["tweaks"]["cs2_fps_400"]["changes"][0]["locator"] = locator
        store.save(data)
    with pytest.raises(UserError):
        engine.revert_many()
