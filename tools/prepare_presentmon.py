"""Download the pinned, official PresentMon console binary and its licenses."""
from hashlib import sha256
from pathlib import Path
import os
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "app" / "assets" / "tools"
VERSION = "2.6.0"
BINARY_SHA256 = "b2a706bc6ad475749e3b7e3409263aa1e6906d45bdcf993f6dbc0f660188f1af"
BINARY_SIZE = 980320
BASE = "https://github.com/GameTechDev/PresentMon"
RAW = "https://raw.githubusercontent.com/GameTechDev/PresentMon/v2.6.0"
FILES = (
    (f"{BASE}/releases/download/v{VERSION}/PresentMon-{VERSION}-x64.exe",
     "PresentMon.exe", BINARY_SHA256, BINARY_SIZE),
    (f"{RAW}/LICENSE.txt", "PresentMon-LICENSE.txt", None, None),
    (f"{RAW}/THIRD_PARTY.txt", "PresentMon-THIRD_PARTY.txt", None, None),
)


def download(url):
    with urlopen(url, timeout=45) as response:
        return response.read()


def main():
    DESTINATION.mkdir(parents=True, exist_ok=True)
    for url, name, expected_hash, expected_size in FILES:
        payload = download(url)
        if expected_hash and sha256(payload).hexdigest() != expected_hash:
            raise RuntimeError(f"SHA-256 mismatch: {name}")
        if expected_size and len(payload) != expected_size:
            raise RuntimeError(f"Size mismatch: {name}")
        target = DESTINATION / name
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(payload)
        os.replace(temporary, target)
        print(f"OK {name}: {len(payload)} bytes")


if __name__ == "__main__":
    main()
