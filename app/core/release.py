"""Release authenticity helpers. Updates never execute automatically."""
import base64
import hashlib
import json
from pathlib import Path
import sys

from cryptography.exceptions import InvalidSignature

from app.core.windows import UserError, executable, run_command
from shared.network import validate_url


def executable_security(config):
    path = Path(sys.executable if getattr(sys, "frozen", False) else sys.argv[0]).resolve()
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        digest = "не удалось вычислить"
    signature = "Режим разработки: файл не является подписанной сборкой."
    if getattr(sys, "frozen", False) and sys.platform == "win32":
        command = ("$s=Get-AuthenticodeSignature -LiteralPath $args[0];"
                   "Write-Output $s.Status; if($s.SignerCertificate){Write-Output $s.SignerCertificate.Subject}")
        result = run_command([executable(config, "powershell"), "-NoProfile", "-Command", command, str(path)], timeout=30)
        if result.returncode == 0:
            lines = result.stdout.decode("utf-8", errors="replace").splitlines()
            signature = "Authenticode: " + "\n".join(lines[:2])
    return f"Файл: {path}\nSHA-256: {digest}\n{signature}"


def verify_release(document, config):
    try:
        payload = base64.b64decode(document["payload"], validate=True)
        signature = base64.b64decode(document["signature"], validate=True)
        public_key = base64.b64decode(config.values["release_public_key"], validate=True)
        if len(payload) > 65536 or len(signature) != 64 or len(public_key) != 32:
            raise ValueError
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, payload)
        data = json.loads(payload)
        if set(data) != {"version", "download_url", "sha256", "notes"}:
            raise ValueError
        if not isinstance(data["version"], str) or len(data["version"]) > 30:
            raise ValueError
        if not isinstance(data["sha256"], str) or len(data["sha256"]) != 64 or any(c not in "0123456789abcdef" for c in data["sha256"]):
            raise ValueError
        if not isinstance(data["notes"], str) or len(data["notes"]) > 5000:
            raise ValueError
        validate_url(data["download_url"], config.network)
        return data
    except (KeyError, ValueError, TypeError, json.JSONDecodeError, InvalidSignature) as exc:
        raise UserError("Обновление имеет неверный формат или подпись. Загрузка запрещена.") from exc


def newer(remote, local):
    def parts(value):
        return tuple(int(part) for part in value.split("."))
    try:
        return parts(remote) > parts(local)
    except ValueError as exc:
        raise UserError("Сервер вернул некорректный номер версии.") from exc
