import json
from types import SimpleNamespace

import pytest

from app.config import Config
from app.core import restore, system_info, windows
from app.core.windows import UserError


def test_non_windows_system_info(monkeypatch):
    monkeypatch.setattr(system_info, "supported_windows", lambda: False)
    assert system_info.read_system_info(Config.load())["windows"] == "Не Windows 10/11"


@pytest.mark.parametrize("ram,expected", [(17179869184, "16.0 ГБ"), (None, "Не определено")])
def test_actual_values_from_cim(monkeypatch, ram, expected):
    monkeypatch.setattr(system_info, "supported_windows", lambda: True)
    monkeypatch.setattr(system_info, "executable", lambda *_: "powershell.exe")
    data = {"cpu": "TEST CPU", "gpu": "TEST GPU", "windows": "TEST OS", "ram_bytes": ram}
    monkeypatch.setattr(system_info, "run_command", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=json.dumps(data).encode("utf-8")))
    result = system_info.read_system_info(Config.load())
    assert result["cpu"] == "TEST CPU"
    assert result["gpu"] == "TEST GPU"
    assert result["ram"] == expected


def test_cim_failure_not_fake_success(monkeypatch):
    monkeypatch.setattr(system_info, "supported_windows", lambda: True)
    monkeypatch.setattr(system_info, "executable", lambda *_: "powershell.exe")
    monkeypatch.setattr(system_info, "run_command", lambda *a, **k: SimpleNamespace(returncode=1))
    with pytest.raises(UserError):
        system_info.read_system_info(Config.load())


@pytest.mark.parametrize("code", [1, 20, 21])
def test_restore_failure_not_success(monkeypatch, code):
    monkeypatch.setattr(restore, "executable", lambda *_: "powershell.exe")
    monkeypatch.setattr(restore, "run_elevated", lambda *args: code)
    with pytest.raises(UserError, match="не подтверждено"):
        restore.create_restore_point(Config.load())


def test_restore_confirmed_success(monkeypatch):
    monkeypatch.setattr(restore, "executable", lambda *_: "powershell.exe")
    calls = []
    monkeypatch.setattr(restore, "run_elevated", lambda *args: calls.append(args) or 0)
    assert "найдена" in restore.create_restore_point(Config.load())
    assert "-Command" in calls[0][1]
    assert "Checkpoint-Computer" in calls[0][1][-1]
    assert "-ExecutionPolicy" not in calls[0][1]
    assert "Set-ExecutionPolicy" not in calls[0][1][-1]


def test_uac_cancel_not_success(monkeypatch):
    monkeypatch.setattr(restore, "executable", lambda *_: "powershell.exe")
    def cancelled(*args):
        raise UserError("Запрос прав администратора отменён.")
    monkeypatch.setattr(restore, "run_elevated", cancelled)
    with pytest.raises(UserError, match="отменён"):
        restore.create_restore_point(Config.load())


def test_unsupported_windows_denied(monkeypatch):
    monkeypatch.setattr(windows, "supported_windows", lambda: False)
    with pytest.raises(UserError):
        windows.require_windows()
