import json

import pytest

from app.core.preferences import Preferences


def test_defaults_are_not_shared_between_instances(tmp_path):
    first = Preferences(tmp_path / "one.json")
    first.data["selected_tweaks"].append("transparency_off")
    assert Preferences(tmp_path / "two.json").data["selected_tweaks"] == []


def test_failed_commit_preserves_disk_and_memory(tmp_path, monkeypatch):
    preferences = Preferences(tmp_path / "preferences.json")
    preferences.update(maximized=True)
    original = preferences.path.read_bytes()

    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr("app.core.preferences.os.replace", fail)
    with pytest.raises(OSError):
        preferences.update(maximized=False)
    assert preferences.data["maximized"] is True
    assert preferences.path.read_bytes() == original
    assert list(tmp_path.glob("*.tmp")) == []


def test_successful_update_survives_restart(tmp_path):
    path = tmp_path / "preferences.json"
    Preferences(path).update(selected_tweaks=["transparency_off"])
    assert Preferences(path).data["selected_tweaks"] == ["transparency_off"]
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == 1
