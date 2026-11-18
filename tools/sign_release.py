"""Local release signing. The private key is never used by client/server code."""
import argparse
import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def write_signed(path, payload, private):
    document = {"payload": base64.b64encode(canonical(payload)).decode("ascii"),
                "signature": base64.b64encode(private.sign(canonical(payload))).decode("ascii")}
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["public", "presets", "release"])
    parser.add_argument("--key", type=Path, default=Path("release-private.pem"))
    parser.add_argument("--version", default="1.4.5")
    parser.add_argument("--url")
    parser.add_argument("--sha256")
    parser.add_argument("--notes", default="Обновление каталога и программы.")
    args = parser.parse_args()
    if args.mode == "public":
        if not args.key.is_file():
            raise SystemExit("Укажите существующий --key; новый ключ случайно не создаётся.")
        private = serialization.load_pem_private_key(args.key.read_bytes(), password=None)
        raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        print(base64.b64encode(raw).decode("ascii"))
        return
    private = serialization.load_pem_private_key(args.key.read_bytes(), password=None)
    root = Path(__file__).resolve().parents[1]
    if args.mode == "presets":
        from app.core.catalog import PRESETS
        write_signed(root / "config/presets.signed.json", {"version": args.version, "notes": args.notes,
                    "presets": {key: list(value[1]) for key, value in PRESETS.items()}}, private)
    else:
        if not args.url or not args.sha256 or len(args.sha256) != 64:
            raise SystemExit("Для release обязательны --url и --sha256.")
        write_signed(root / "config/release.signed.json", {"version": args.version,
                    "download_url": args.url, "sha256": args.sha256.lower(), "notes": args.notes}, private)


if __name__ == "__main__":
    main()
