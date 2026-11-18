import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.config import Config
from app.core.auth import AuthService
from app.core.token_store import TokenStore, dpapi
from app.core.windows import UserError
from shared.client import ApiClient, ApiError
from shared.network import validate_url, bot_url

NETWORK = {"api_base_url": "https://api.example.test",
           "telegram_bot_url": "https://t.me/shatli_test_bot",
           "allowed_origins": ["https://api.example.test", "https://t.me"]}


@pytest.mark.parametrize("url", [
    "http://api.example.test", "https://api.example.test.evil.test",
    "https://api.example.test@evil.test", "https://api.example.test/#secret",
    "file:///etc/passwd", "https://api.example.test:444", "https://api.example.test\\@evil.test",
])
def test_network_rejects_unknown_and_insecure_urls(url):
    with pytest.raises(ValueError):
        validate_url(url, NETWORK)


def test_loopback_requires_explicit_configuration():
    net = {"allowed_origins": ["http://127.0.0.1:8000"]}
    with pytest.raises(ValueError):
        validate_url("http://127.0.0.1:8000/profile", net)
    net["allow_http_loopback"] = True
    assert validate_url("http://127.0.0.1:8000/profile", net)
    assert bot_url(NETWORK) == NETWORK["telegram_bot_url"]


@pytest.mark.parametrize("response", [
    httpx.Response(302, headers={"Location": "https://evil.test"}, json={}),
    httpx.Response(200, content=b"not-json"),
    httpx.Response(200, content=b"a" * 256001),
    httpx.Response(200, json=[]),
])
def test_transport_rejects_redirect_invalid_or_oversized_response(response):
    calls = []
    def handle(request):
        calls.append(request)
        return response
    client = ApiClient(NETWORK, httpx.MockTransport(handle))
    with pytest.raises(ApiError):
        client.call("GET", "/profile", headers={"Authorization": "Bearer secret"})
    assert len(calls) == 1
    assert "secret" not in str(calls[0].url)


def test_transport_offline_error_does_not_disclose_token():
    def handle(request):
        raise httpx.ConnectError("raw secret", request=request)
    with pytest.raises(ApiError) as caught:
        ApiClient(NETWORK, httpx.MockTransport(handle)).call("GET", "/profile")
    assert "secret" not in str(caught.value)


def test_default_config_uses_explicit_loopback_api():
    config = Config.load()
    assert config.network["api_base_url"] == "http://127.0.0.1:8000"
    assert config.network["allow_http_loopback"] is True
    assert "http://127.0.0.1:8000" in config.network["allowed_origins"]


def fake_crypt(data, *, decrypt=False):
    if decrypt:
        if not data.startswith(b"TEST-ENCRYPTED:"):
            raise ValueError()
        return data[15:][::-1]
    return b"TEST-ENCRYPTED:" + data[::-1]


def test_store_atomic_encrypted_roundtrip_and_clear(tmp_path):
    store = TokenStore(tmp_path / "token.dpapi", fake_crypt)
    token = {"access_token": "a" * 43, "expires_at": 100}
    assert store.load() is None
    store.save(token)
    assert json.dumps(token).encode() not in store.path.read_bytes()
    assert store.load() == token
    store.clear()
    assert not store.path.exists()


def test_dpapi_no_plaintext_fallback_and_failed_save_keeps_old(tmp_path, monkeypatch):
    store = TokenStore(tmp_path / "token.dpapi", fake_crypt)
    store.save({"access_token": "a" * 43, "expires_at": 100})
    old = store.path.read_bytes()
    def denied(*a, **k):
        raise UserError("DPAPI unavailable")
    store.crypt = denied
    with pytest.raises(UserError):
        store.save({"access_token": "b" * 43, "expires_at": 100})
    assert store.path.read_bytes() == old
    monkeypatch.setattr("app.core.token_store.require_windows", denied)
    with pytest.raises(UserError):
        dpapi(b"token")


class FakeStore:
    def __init__(self):
        self.value = None
    def load(self):
        return self.value
    def save(self, value):
        self.value = value
    def clear(self):
        self.value = None


def test_auth_poll_stores_token_only_after_authorization():
    store = FakeStore()
    response = {"status": "pending"}
    api = SimpleNamespace(call=lambda *a, **k: response)
    auth = AuthService(Config.load(), api=api, store=store, identity=lambda: "a" * 64)
    session = {"session_id": "id", "poll_secret": "secret"}
    auth.poll(session)
    assert store.value is None
    response = {"status": "authorized", "access_token": "x" * 43, "expires_at": 500}
    auth.poll(session)
    assert store.value["access_token"] == "x" * 43


def test_pro_free_custom_and_expiry_gate():
    response = {"pro_active": False, "free_tweak_ids": None}
    auth = AuthService(Config.load(), api=SimpleNamespace(call=lambda *a, **k: response), store=FakeStore())
    with pytest.raises(UserError, match="FREE"):
        auth.authorize_apply(["transparency_off"])
    response["free_tweak_ids"] = ["transparency_off"]
    auth.authorize_apply(["transparency_off"])
    with pytest.raises(UserError):
        auth.authorize_apply(["mouse_acceleration_off"])
    with pytest.raises(UserError):
        auth.authorize_apply(["transparency_off"], custom=True)
    response["pro_active"] = True
    auth.authorize_apply(["mouse_acceleration_off"], custom=True)
    response["pro_active"] = False
    with pytest.raises(UserError):
        auth.authorize_apply(["mouse_acceleration_off"])


def test_logout_offline_clears_only_token(tmp_path):
    backup = tmp_path / "backup.json"
    backup.write_text("keep")
    store = FakeStore()
    store.value = {"access_token": "x" * 43}
    def offline(*a, **k):
        raise ApiError("offline")
    auth = AuthService(Config.load(), api=SimpleNamespace(call=offline), store=store)
    assert "Сервер недоступен" in auth.logout()
    assert store.value is None
    assert backup.read_text() == "keep"


@pytest.mark.parametrize("decrypt", [False, True])
def test_dpapi_native_call_flags_and_free_are_correct(monkeypatch, decrypt):
    import ctypes
    from app.core.token_store import Blob
    output_bytes = (ctypes.c_ubyte * 6).from_buffer_copy(b"result")
    freed, seen = [], []
    class Native:
        def __call__(self, source, description, entropy, reserved, prompt, flags, output):
            seen.append(flags)
            assert description is entropy is reserved is prompt is None
            blob = ctypes.cast(output, ctypes.POINTER(Blob)).contents
            blob.cbData = 6
            blob.pbData = output_bytes
            return 1
    class Free:
        def __call__(self, pointer):
            freed.append(True)
    dll = SimpleNamespace(CryptProtectData=Native(), CryptUnprotectData=Native(), LocalFree=Free())
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: dll, raising=False)
    monkeypatch.setattr("app.core.token_store.require_windows", lambda: None)
    assert dpapi(b"secret", decrypt=decrypt) == b"result"
    assert seen == [1] and freed == [True]


def test_free_preset_is_never_invented():
    response = {"free_tweak_ids": None}
    auth = AuthService(Config.load(), api=SimpleNamespace(call=lambda *a, **k: response), store=FakeStore())
    with pytest.raises(UserError, match="FREE"):
        auth.free_preset()
    from app.core.catalog import BY_ID
    response["free_tweak_ids"] = list(BY_ID)[:3]
    assert auth.free_preset() == list(BY_ID)[:3]
    response["free_tweak_ids"] = list(BY_ID)[:2]
    with pytest.raises(UserError):
        auth.free_preset()


@pytest.mark.parametrize("status", [0, 502, 503])
def test_offline_free_access_never_unlocks_pro(status):
    from app.core.catalog import BY_ID, FREE_TWEAK_IDS
    def offline(*a, **k):
        raise ApiError("Нет соединения с сервером.", status)
    auth = AuthService(Config.load(), api=SimpleNamespace(call=offline), store=FakeStore())
    assert auth.free_preset() == list(FREE_TWEAK_IDS)
    auth.authorize_apply(list(FREE_TWEAK_IDS))
    paid = next(tweak_id for tweak_id in BY_ID if tweak_id not in FREE_TWEAK_IDS)
    with pytest.raises(UserError):
        auth.authorize_apply([paid])
    with pytest.raises(UserError):
        auth.authorize_apply(list(FREE_TWEAK_IDS), custom=True)


def test_denied_access_is_not_replaced_by_the_offline_free_set():
    def denied(*a, **k):
        raise ApiError("Доступ запрещён.", 403)
    auth = AuthService(Config.load(), api=SimpleNamespace(call=denied), store=FakeStore())
    with pytest.raises(UserError, match="запрещён"):
        auth.free_preset()
