"""Read-only readiness checks. Never prints values of environment secrets."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.catalog import BY_ID
from shared.network import api_base, bot_url, validate_url


def main():
    root = Path(__file__).resolve().parents[1]
    try:
        network = json.loads((root / "config/network.json").read_text(encoding="utf-8"))
        service = json.loads((root / "config/service.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("TODO Не удалось прочитать конфигурацию. Создайте config/service.json "
              "из config/service.example.json и проверьте формат JSON.")
        return 1
    checks = []
    for name, check in (("API", lambda: api_base(network)),
                        ("Telegram URL", lambda: bot_url(network))):
        try:
            check()
            checks.append((name, True))
        except (ValueError, KeyError):
            checks.append((name, False))
    checks.extend([
        ("Служебный секрет в окружении", len(os.environ.get(service["bot_secret_env"], "")) >= 32),
        ("Поддержка", bool(service.get("support_contact"))),
    ])
    free = service.get("free_tweak_ids")
    checks.append(("Три FREE-твика", isinstance(free, list) and len(set(free)) == len(free) == 3
                   and all(x in BY_ID for x in free)))
    for name in ("terms_url", "privacy_url"):
        try:
            validate_url(service.get(name), network)
            checks.append((name, True))
        except ValueError:
            checks.append((name, False))
    for name, plan in service["plans"].items():
        checks.append((name + " цена XTR", type(plan.get("stars")) is int and 0 < plan["stars"] <= 100000))
    for name, ready in checks:
        print(("OK   " if ready else "TODO ") + name)
    print("Продажи: " + ("включены" if service["sales_enabled"] else "выключены"))
    print("Проверка только локальная, не подтверждает доступность API или реальные платежи.")
    return 0 if all(ready for _, ready in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
