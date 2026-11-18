"""Unit-test native adapter semantics without loading or editing Windows registry."""
from types import SimpleNamespace
import sys

import pytest

from app.config import Config
from app.core.catalog import BY_ID
from app.core.windows import UserError
from app.core.windows_backend import WindowsBackend


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setattr("app.core.windows_backend.require_windows", lambda: None)
    return WindowsBackend(Config.load())


@pytest.fixture
def registry(monkeypatch):
    values = {"EnableTransparency": (1, 4)}
    flags = []
    flushed = []
    class Key:
        def __init__(self, access):
            self.access = access
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
    def open_key(hive, path, reserved=0, access=0):
        flags.append(access)
        return Key(access)
    def query(key, name):
        if name not in values:
            raise FileNotFoundError
        return values[name]
    def set_value(key, name, reserved, kind, value):
        assert key.access & 2
        values[name] = (value, kind)
    def delete(key, name):
        if name not in values:
            raise FileNotFoundError
        del values[name]
    def flush(key):
        assert key.access & 1  # KEY_QUERY_VALUE is required by RegFlushKey.
        flushed.append(True)
    fake = SimpleNamespace(HKEY_CURRENT_USER=123, HKEY_LOCAL_MACHINE=456, KEY_READ=1, KEY_QUERY_VALUE=1,
                           KEY_SET_VALUE=2, KEY_WOW64_64KEY=256, OpenKey=open_key, CreateKeyEx=open_key,
                           QueryValueEx=query, SetValueEx=set_value, DeleteValue=delete,
                           FlushKey=flush)
    monkeypatch.setitem(sys.modules, "winreg", fake)
    return fake, values, flags, flushed


def test_read_existing_value_and_type(adapter, registry):
    op = BY_ID["transparency_off"].operations[0]
    assert adapter.read(op) == {"exists": True, "type": 4, "value": 1}


def test_group_rechecks_expected_value_before_write(adapter, registry):
    op = BY_ID["transparency_off"].operations[0]
    with pytest.raises(UserError, match="изменилась"):
        adapter.write_group([(op, {"exists": True, "type": 4, "value": 99}, op.new)])
    assert registry[1]["EnableTransparency"] == (1, 4)
    assert registry[3] == []


def test_group_skips_unchanged_values(adapter, registry):
    op = BY_ID["transparency_off"].operations[0]
    expected = adapter.read(op)
    adapter.write_group([(op, expected, expected)])
    assert registry[3] == []


def test_read_missing_value_distinct_from_missing_key(adapter, registry):
    op = BY_ID["transparency_off"].operations[0]
    registry[1].clear()
    assert adapter.read(op) == {"exists": False, "type": None, "value": None}
    def no_key(*args):
        raise FileNotFoundError
    registry[0].OpenKey = no_key
    assert adapter.read(op) == {"exists": False, "type": None, "value": None}
    with pytest.raises(UserError, match="раздел"):
        adapter.read(BY_ID["diagtrack_disabled"].operations[0])


def test_unexpected_registry_type_is_refused(adapter, registry):
    registry[1]["EnableTransparency"] = ("1", 1)
    with pytest.raises(UserError, match="тип"):
        adapter.read(BY_ID["transparency_off"].operations[0])


def test_write_existing_key_with_flush_access(adapter, registry):
    op = BY_ID["transparency_off"].operations[0]
    adapter.write(op, op.new)
    assert registry[1]["EnableTransparency"] == (0, 4)
    assert registry[2][-1] == 1 | 2 | 256
    assert registry[3] == [True]


def test_revert_missing_value_deletes_only_value(adapter, registry):
    adapter.write(BY_ID["transparency_off"].operations[0],
                  {"exists": False, "type": None, "value": None})
    assert "EnableTransparency" not in registry[1]
    assert registry[3] == [True]


def test_power_guids_are_locale_independent(adapter, monkeypatch):
    balanced = "381b4222-f694-41f0-9685-ff5bb260df2e"
    high = BY_ID["high_performance"].operations[0]
    calls = []
    def power(*args):
        calls.append(args)
        if args[0] == "/getactivescheme":
            return ("Схема питания: " + balanced).encode("utf-8")
        if args[0] == "/list":
            return (balanced + "\n" + high.value).upper().encode("ascii")
        return b""
    monkeypatch.setattr(adapter, "_power", power)
    assert adapter.read(high)["value"] == balanced
    adapter.check_supported(high)
    adapter.write(high, high.new)
    assert ("/setactive", high.value) in calls


def test_absent_high_performance_is_not_created(adapter, monkeypatch):
    monkeypatch.setattr(adapter, "_power", lambda *args: b"381b4222-f694-41f0-9685-ff5bb260df2e")
    with pytest.raises(UserError, match="не найден"):
        adapter.check_supported(BY_ID["high_performance"].operations[0])


def test_deleted_original_power_plan_cannot_be_restored(adapter, monkeypatch):
    high = BY_ID["high_performance"].operations[0]
    calls = []
    monkeypatch.setattr(adapter, "_power", lambda *args: calls.append(args) or high.value.encode())
    with pytest.raises(UserError, match="удалён"):
        adapter.write(high, {"exists": True, "type": "power_guid",
                            "value": "381b4222-f694-41f0-9685-ff5bb260df2e"})
    assert calls == [("/list",)]
