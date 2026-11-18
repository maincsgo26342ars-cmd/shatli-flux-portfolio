"""Restore-point creation is not a tweak; no silent protection enable/limit bypass."""
import subprocess

from app.core.windows import UserError, executable, run_elevated


def create_restore_point(config):
    script = config.asset("restore_script").read_text(encoding="utf-8-sig")
    code = run_elevated(executable(config, "powershell"), [
        "-NoProfile", "-NonInteractive", "-Command", script,
    ])
    if code != 0:
        raise UserError(
            "Создание точки восстановления не подтверждено. "
            "Проверьте, включена ли защита системного диска. Windows также может "
            "не разрешать новую точку в течение 24 часов. "
            "Ограничения и настройки защиты программа не меняет."
        )
    return "Точка восстановления создана и найдена в списке Windows."


def open_restore_wizard(config):
    # User explicitly opens Windows' interactive UI; it is not a background task.
    try:
        subprocess.Popen([executable(config, "restore_wizard")], shell=False)
    except OSError as exc:
        raise UserError("Не удалось открыть мастер восстановления Windows.") from exc
    return "Мастер восстановления Windows открыт."
