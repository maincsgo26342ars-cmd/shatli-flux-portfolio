"""Read real local hardware and settings using CIM; never substitute demo values."""
import json

from app.core.windows import UserError, executable, run_command, supported_windows


def _integer(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def read_system_info(config):
    if not supported_windows():
        return dict(cpu="Только Windows", gpu="Только Windows",
                    ram="Только Windows", windows="Не Windows 10/11")
    script = config.asset("system_info_script").read_text(encoding="utf-8-sig")
    result = run_command([
        executable(config, "powershell"), "-NoProfile", "-NonInteractive",
        "-Command", script,
    ], timeout=40)
    if result.returncode:
        raise UserError("Не удалось прочитать сведения о ПК. Проверьте доступность PowerShell.")
    try:
        data = json.loads(result.stdout.decode("utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError
        memory = _integer(data.get("ram_bytes"))
        ram = f"{memory / (1024 ** 3):.1f} ГБ" if memory else "Не определено"
        formatted = {key: str(data.get(key) or "Не определено")
                     for key in ("cpu", "gpu", "windows")}
        numeric = {key: _integer(data.get(key)) for key in (
            "ram_bytes", "ram_available_bytes", "cpu_logical", "game_mode", "hags",
            "startup_count", "system_drive_free_bytes",
        )}
        battery = data.get("battery_present")
        return {**formatted, "ram": ram, **numeric,
                "power_plan": str(data.get("power_plan") or "Не определено"),
                "battery_present": battery if isinstance(battery, bool) else None}
    except (ValueError, TypeError) as exc:
        raise UserError("Windows вернула неполные сведения о ПК.") from exc
