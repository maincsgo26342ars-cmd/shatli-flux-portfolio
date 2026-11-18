import logging

from PySide6.QtCore import QThread, Signal

from app.core.windows import UserError


class Job(QThread):
    succeeded = Signal(object)
    failed = Signal(str)
    progress = Signal(int, str)

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            self.succeeded.emit(self.operation(self.progress.emit))
        except UserError as exc:
            logging.getLogger("shatli_flux").warning("%s", exc)
            self.failed.emit(str(exc))
        except PermissionError:
            logging.getLogger("shatli_flux").exception("Недостаточно прав")
            self.failed.emit("Недостаточно прав для этой операции. Изменения не подтверждены.")
        except Exception:
            logging.getLogger("shatli_flux").exception("Ошибка операции")
            self.failed.emit(
                "Не удалось завершить операцию. Проверьте журнал и состояние изменений "
                "в разделе «Безопасность»."
            )
