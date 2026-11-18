import base64
import hashlib
import json
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from fastapi.testclient import TestClient

from app.config import Config
from app.core.backup_store import BackupStore
from app.core.release import verify_release, newer
from app.core.tweaks import TweakEngine
from app.core.windows_backend import WindowsBackend
from server.main import Settings, create_app, DAY

SECRET = "stage5-test-secret-" * 3
BOT = {"X-Bot-Secret": SECRET}
NETWORK = {"api_base_url": "https://api.example.test", "telegram_bot_url": "https://t.me/shatli_test_bot",
           "allowed_origins": ["https://api.example.test", "https://t.me", "https://example.test"]}


def signed(value, private):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return {"payload": base64.b64encode(payload).decode(),
            "signature": base64.b64encode(private.sign(payload)).decode()}


def test_signed_release_and_tamper_rejection():
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    config = SimpleNamespace(values={"release_public_key": base64.b64encode(public).decode()}, network=NETWORK)
    value = {"version": "1.0.1", "download_url": "https://example.test/setup.exe",
             "sha256": "a" * 64, "notes": "Исправления"}
    document = signed(value, private)
    assert verify_release(document, config)["version"] == "1.0.1"
    assert newer("1.0.1", "1.0.0")
    document["payload"] = base64.b64encode(json.dumps(dict(value, version="9.9.9")).encode()).decode()
    with pytest.raises(Exception, match="подпись"):
        verify_release(document, config)


def test_autoexec_roundtrip_preserves_unrelated_text(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.windows_backend.require_windows", lambda: None)
    path = tmp_path / "cfg" / "autoexec.cfg"
    path.parent.mkdir()
    original = 'sensitivity 2.5\nbind "space" "+jump"\n'
    path.write_bytes(original.encode())
    backend = WindowsBackend(Config.load())
    backend.cs2_autoexec = path
    monkeypatch.setattr(backend, "identity", lambda: "test-owner")
    store = BackupStore(tmp_path / "backup.json", tmp_path / "backup.lock")
    engine = TweakEngine(store, backend)
    engine.apply_many(["cs2_fps_400", "cs2_fps_counter"])
    value = path.read_text(encoding="utf-8")
    assert "sensitivity 2.5" in value and 'bind "space" "+jump"' in value
    assert "fps_max 400" in value and "cl_showfps 1" in value
    assert value.count("SHATLI FLUX") == 2
    engine.revert_many()
    assert path.read_bytes() == original.encode()


def test_autoexec_reapply_rolls_back_in_actual_application_order(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.windows_backend.require_windows", lambda: None)
    path = tmp_path / "cfg" / "autoexec.cfg"
    path.parent.mkdir()
    original = b"sensitivity 2.5\n"
    path.write_bytes(original)
    backend = WindowsBackend(Config.load())
    backend.cs2_autoexec = path
    monkeypatch.setattr(backend, "identity", lambda: "test-owner")
    engine = TweakEngine(BackupStore(tmp_path / "backup.json", tmp_path / "backup.lock"), backend)
    engine.apply_many(["cs2_fps_400"])
    engine.revert_many()
    engine.apply_many(["cs2_fps_counter"])
    engine.apply_many(["cs2_fps_400"])
    engine.revert_many()
    assert path.read_bytes() == original


@pytest.fixture
def api(tmp_path):
    clock = [1800000000]
    service = {"free_tweak_ids": ["game_capture_off", "transparency_off", "mouse_acceleration_off"],
               "sales_enabled": True, "support_contact": "test", "terms_url": "https://example.test/terms",
               "privacy_url": "https://example.test/privacy", "lifetime_pro_users": [],
               "plans": {"PRO30": {"days": 30, "stars": 50}}}
    settings = Settings(tmp_path / "db.sqlite3", SECRET, NETWORK, service)
    with TestClient(create_app(settings, clock=lambda: clock[0]), base_url="https://api.example.test") as client:
        session = client.post("/auth/start", json={"hwid": hashlib.sha256(b"pc").hexdigest(), "name": "pc"}).json()
        client.post("/bot/confirm-auth", headers=BOT, json={"session_id": session["session_id"],
                    "telegram_id": 123, "username": "test"})
        client.get("/auth/poll", params={"session_id": session["session_id"]},
                   headers={"X-Poll-Secret": session["poll_secret"]})
        yield client, clock


def test_refund_is_idempotent_and_reminder_recorded_once(api):
    client, clock = api
    order = client.post("/bot/orders", headers=BOT, json={"telegram_id": 123, "plan": "PRO30"}).json()
    body = {"telegram_id": 123, "order_id": order["order_id"], "amount": 50, "currency": "XTR", "charge_id": "charge"}
    expires = client.post("/bot/payment", headers=BOT, json=body).json()["expires_at"]
    refunded = client.post("/bot/refund", headers=BOT, json={"telegram_id": 123, "charge_id": "charge"})
    assert refunded.status_code == 200 and refunded.json()["expires_at"] == clock[0]
    duplicate = client.post("/bot/refund", headers=BOT, json={"telegram_id": 123, "charge_id": "charge"})
    assert duplicate.json()["duplicate"] is True
    order = client.post("/bot/orders", headers=BOT, json={"telegram_id": 123, "plan": "PRO30"}).json()
    body.update(order_id=order["order_id"], charge_id="charge2")
    expires = client.post("/bot/payment", headers=BOT, json=body).json()["expires_at"]
    clock[0] = expires - 2 * DAY
    due = client.get("/bot/reminders", headers=BOT).json()["reminders"]
    assert len(due) == 1 and due[0]["telegram_id"] == 123
    assert client.post("/bot/reminded", headers=BOT, json={"telegram_id": 123, "expires_at": expires}).status_code == 200
    assert client.get("/bot/reminders", headers=BOT).json()["reminders"] == []
