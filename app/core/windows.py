"""Windows-only boundaries. No work or elevation on import."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import subprocess
import sys


class UserError(RuntimeError):
    """Safe, user-facing Russian error. Technical diagnostics go to log."""


def supported_windows() -> bool:
    if sys.platform != "win32":
        return False
    version = sys.getwindowsversion()
    if version.major != 10 or version.build < 10240:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\ProductOptions") as key:
            return winreg.QueryValueEx(key, "ProductType")[0] == "WinNT"
    except OSError:
        return False


def require_windows():
    if not supported_windows():
        raise UserError("Эта операция доступна только в Windows 10/11. Изменений нет.")


def executable(config, name):
    require_windows()
    path = Path(os.path.expandvars(config.values["paths"][name]))
    if not path.is_absolute() or not path.is_file():
        raise UserError("Не найден необходимый компонент Windows. Проверьте конфигурацию.")
    return str(path)


def run_command(arguments, timeout=30):
    """No shell, execution-policy bypass, hidden flags or detached process."""
    require_windows()
    try:
        return subprocess.run(
            arguments, shell=False, capture_output=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise UserError("Windows не ответила вовремя. Проверьте состояние и повторите.") from exc
    except OSError as exc:
        raise UserError("Не удалось запустить компонент Windows.") from exc


def run_elevated(exe: str, arguments: list[str], wait_callback=None) -> int:
    """Visible, single explicitly requested task. Wait and return its exit code."""
    require_windows()
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG),
            ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    shell = ctypes.WinDLL("shell32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    ole = ctypes.OleDLL("ole32")
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    shell.ShellExecuteExW.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoInitializeEx.restype = ctypes.c_long
    initialized = ole.CoInitializeEx(None, 0x2 | 0x4) in (0, 1)
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x40 | 0x100  # NOCLOSEPROCESS, NOASYNC
    info.lpVerb = "runas"
    info.lpFile = exe
    info.lpParameters = subprocess.list2cmdline(arguments)
    info.nShow = 1  # Visible normal window, deliberately not hidden.
    try:
        if not shell.ShellExecuteExW(ctypes.byref(info)):
            if ctypes.get_last_error() == 1223:
                raise UserError("Запрос прав администратора отменён. Операция не выполнена.")
            raise UserError("Windows не разрешила запустить операцию с правами администратора.")
        if not info.hProcess:
            raise UserError("Не удалось получить состояние запущенной операции.")
        if wait_callback is None:
            if kernel.WaitForSingleObject(info.hProcess, 0xFFFFFFFF) != 0:
                raise UserError("Не удалось дождаться результата операции администратора.")
        else:
            while True:
                status = kernel.WaitForSingleObject(info.hProcess, 200)
                if status == 0:
                    break
                if status != 0x102:  # WAIT_TIMEOUT
                    raise UserError("Не удалось дождаться результата операции администратора.")
                wait_callback()
        result = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(info.hProcess, ctypes.byref(result)):
            raise UserError("Windows не сообщила результат операции.")
        return result.value
    finally:
        if info.hProcess:
            kernel.CloseHandle(info.hProcess)
        if initialized:
            ole.CoUninitialize()
