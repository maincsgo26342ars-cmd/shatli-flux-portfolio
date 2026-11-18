"""Verify signed preset metadata; remote documents never define system operations."""
import base64
import json

from cryptography.exceptions import InvalidSignature

from app.core.catalog import PRESETS
from app.core.windows import UserError


def verify_presets(document, config):
    try:
        payload = base64.b64decode(document["payload"], validate=True)
        signature = base64.b64decode(document["signature"], validate=True)
        public_key = base64.b64decode(config.values["release_public_key"], validate=True)
        if len(payload) > 65536 or len(signature) != 64 or len(public_key) != 32:
            raise ValueError
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, payload)
        data = json.loads(payload)
        if set(data) != {"version", "notes", "presets"} or not isinstance(data["presets"], dict):
            raise ValueError
        for key, ids in data["presets"].items():
            if key not in PRESETS or not isinstance(ids, list) or set(ids) != set(PRESETS[key][1]):
                raise ValueError("Remote operations are not allowed")
        return data
    except (KeyError, ValueError, TypeError, json.JSONDecodeError, InvalidSignature) as exc:
        raise UserError("Подпись или содержимое пресетов недействительно. Обновление отклонено.") from exc
