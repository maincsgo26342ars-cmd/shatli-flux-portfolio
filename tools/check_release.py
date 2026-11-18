"""Offline release configuration gate. Never reads secrets or starts services."""
import argparse
import base64
import ipaddress
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.network import validate_url


def public_https(url, network):
    try:
        parsed = urlsplit(validate_url(url, network))
        host = parsed.hostname
        if parsed.scheme != "https" or host == "localhost" or "." not in host:
            return False
        if host.endswith((".localhost", ".local", ".test", ".invalid", ".example")):
            return False
        if host in {"example.com", "example.org", "example.net"}:
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return True  # Syntax only; availability and DNS require a separate check.
    except (ValueError, TypeError):
        return False


def audit(root):
    checks = []
    documents = {}
    for name in ("app", "network", "service"):
        try:
            value = json.loads((root / "config" / f"{name}.json").read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError
            documents[name] = value
            checks.append((f"config/{name}.json читается", True))
        except (OSError, ValueError):
            checks.append((f"config/{name}.json читается", False))
    app, network, service = (documents.get(name, {}) for name in ("app", "network", "service"))
    checks.append(("Публичный HTTPS API", public_https(network.get("api_base_url"), network)))
    checks.append(("Локальный HTTP отключён", network.get("allow_http_loopback") is False))
    try:
        key = base64.b64decode(app.get("release_public_key", ""), validate=True)
        key_ready = len(key) == 32
    except (ValueError, TypeError):
        key_ready = False
    checks.append(("Публичный Ed25519-ключ задан", key_ready))
    checks.append(("Поддержка задана", isinstance(service.get("support_contact"), str)
                   and bool(service["support_contact"].strip())))
    for name in ("terms_url", "privacy_url"):
        checks.append((f"Публичный адрес {name}", public_https(service.get(name), network)))
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    checks = audit(args.root)
    ready = all(ok for _, ok in checks)
    if args.as_json:
        print(json.dumps({"configuration_ready": ready,
                          "checks": [{"name": name, "passed": ok} for name, ok in checks]},
                         ensure_ascii=False, indent=2))
    else:
        for name, ok in checks:
            print(("OK   " if ok else "TODO ") + name)
        print("Проверена только конфигурация: подпись EXE, VM, платежи и доступность сервиса проверяются отдельно.")
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
