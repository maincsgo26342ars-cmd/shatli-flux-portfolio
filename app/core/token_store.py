"""Current-user Windows DPAPI only. Never falls back to plaintext."""
import ctypes
from ctypes import wintypes
import json
import os
import tempfile

from app.core.windows import require_windows, UserError


class Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def dpapi(data, *, decrypt=False):
    require_windows()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = Blob(len(data), buffer)
    output = Blob()
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    # CRYPTPROTECT_UI_FORBIDDEN = 1; deliberately NOT LOCAL_MACHINE.
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise UserError("Windows не смогла открыть или защитить токен. Войдите снова.")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel.LocalFree(output.pbData)


class TokenStore:
    def __init__(self, path, crypt=dpapi):
        self.path = path
        self.crypt = crypt

    def load(self):
        if not self.path.exists():
            return None
        try:
            data = self.path.read_bytes()
            if len(data) > 16384:
                raise ValueError()
            value = json.loads(self.crypt(data, decrypt=True))
            if not isinstance(value["access_token"], str) or not 32 <= len(value["access_token"]) <= 128:
                raise ValueError()
            if type(value["expires_at"]) is not int:
                raise ValueError()
            return value
        except (OSError, ValueError, KeyError, TypeError):
            raise UserError("Сохранённый вход повреждён. Удалите его кнопкой «Выйти» и войдите снова.") from None

    def save(self, value):
        if (not isinstance(value, dict) or not isinstance(value.get("access_token"), str)
                or not 32 <= len(value["access_token"]) <= 128
                or type(value.get("expires_at")) is not int):
            raise UserError("Сервер вернул некорректный токен. Повторите вход.")
        encrypted = self.crypt(json.dumps(value).encode())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".session-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(encrypted)
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def clear(self):
        self.path.unlink(missing_ok=True)
