"""Opt-in per-user startup and desktop shortcut."""
from pathlib import Path
import os
import subprocess
import sys

from app.core.windows import UserError, executable, require_windows, run_command

RUN_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_command():
    if getattr(sys, "frozen", False):
        return subprocess.list2cmdline([sys.executable]), ""
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    return subprocess.list2cmdline([str(pythonw if pythonw.exists() else python), "-m", "app.main"]), str(Path(__file__).resolve().parents[2])


def autostart_enabled():
    require_windows()
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_PATH) as key:
            value = winreg.QueryValueEx(key, "ShatliFlux")[0]
            return value == launch_command()[0]
    except FileNotFoundError:
        return False


def set_autostart(enabled):
    require_windows()
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_PATH, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, "ShatliFlux", 0, winreg.REG_SZ, launch_command()[0])
        else:
            try:
                winreg.DeleteValue(key, "ShatliFlux")
            except FileNotFoundError:
                pass


def create_shortcut(config):
    require_windows()
    desktop = Path(os.path.expandvars(r"%USERPROFILE%")) / "Desktop"
    if not desktop.is_dir():
        raise UserError("Папка рабочего стола не найдена.")
    _, working = launch_command()
    if getattr(sys, "frozen", False):
        exe, arguments = sys.executable, ""
    else:
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        exe = str(pythonw if pythonw.exists() else Path(sys.executable))
        arguments = "-m app.main"
    script = ("$w=New-Object -ComObject WScript.Shell;$s=$w.CreateShortcut($args[0]);"
              "$s.TargetPath=$args[1];$s.Arguments=$args[2];$s.WorkingDirectory=$args[3];"
              "$s.IconLocation=$args[4];$s.Save()")
    icon = str(config.asset("icons") / "app.ico") + ",0"
    result = run_command([executable(config, "powershell"), "-NoProfile", "-Command", script,
                          str(desktop / "Shatli FLUX.lnk"), exe, arguments, working, icon])
    if result.returncode:
        raise UserError("Windows не создала ярлык. Проверьте доступ к рабочему столу.")
    return "Ярлык создан на рабочем столе."
