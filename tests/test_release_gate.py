import base64
import json

import pytest

from tools.check_release import audit, public_https


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000", "https://localhost",
                                 "https://192.168.1.2", "https://api.example.test",
                                 "https://example.com", None])
def test_release_gate_rejects_local_and_placeholder_urls(url):
    assert not public_https(url, {"allowed_origins": [url], "allow_http_loopback": True})


def test_missing_configuration_is_reported_without_crash(tmp_path):
    checks = audit(tmp_path)
    assert not any(ok for _, ok in checks)


def test_complete_configuration_and_invalid_key(tmp_path):
    folder = tmp_path / "config"
    folder.mkdir()
    origin = "https://shatli-flux.ru"
    documents = {
        "app": {"release_public_key": base64.b64encode(bytes(range(32))).decode()},
        "network": {"api_base_url": origin, "allowed_origins": [origin], "allow_http_loopback": False},
        "service": {"support_contact": "support", "terms_url": origin + "/terms",
                    "privacy_url": origin + "/privacy"},
    }
    for name, value in documents.items():
        (folder / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")
    assert all(ok for _, ok in audit(tmp_path))
    (folder / "app.json").write_text('{"release_public_key": "bad"}', encoding="utf-8")
    assert not all(ok for _, ok in audit(tmp_path))
