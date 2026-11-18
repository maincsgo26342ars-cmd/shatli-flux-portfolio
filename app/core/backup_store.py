"""Exclusive per-file lock and atomic, flushed JSON replacement.

No silent reset of corrupt, foreign or future-schema journals.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile

from app.core.windows import UserError


class BackupStore:
    def __init__(self, path: Path, lock_path: Path):
        self.path, self.lock_path = Path(path), Path(lock_path)

    @contextmanager
    def locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+b")
        acquired = False
        try:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as exc:
                raise UserError("Другая операция уже использует резервную копию. Дождитесь её окончания.") from exc
            yield
        finally:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def load(self):
        if not self.path.exists():
            return None
        try:
            if self.path.stat().st_size > 16 * 1024 * 1024:
                raise ValueError("journal too large")
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("schema") not in (1, 2):
                raise ValueError("unknown schema")
            return data
        except (OSError, ValueError, UnicodeError) as exc:
            raise UserError(
                "Не удалось прочитать backup.json. Он не перезаписан. "
                "Не удаляйте файл: сохраните его копию для восстановления."
            ) from exc

    def save(self, data):
        # Caller owns locked(). The next Windows write is forbidden if this fails.
        payload = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
        if len(payload) > 16 * 1024 * 1024:
            raise UserError("Журнал слишком большой. Изменения остановлены; сохраните backup.json.")
        descriptor, temp = tempfile.mkstemp(prefix=".backup-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
            # POSIX test hosts: persist the directory entry as well.
            if os.name != "nt":
                directory = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
