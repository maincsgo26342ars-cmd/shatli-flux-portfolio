"""Launch from the project root: python -m app.main"""
from __future__ import annotations

import logging
import sys

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QMessageBox

from app.config import Config
from app.core.logging_setup import setup_logging
from app.ui.window import MainWindow


def create_application():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Shatli FLUX")
    app.setOrganizationName("ShatliFlux")
    app.setStyle("Fusion")
    config = Config.load()
    for key in ("font", "font_semibold", "font_cyrillic", "font_cyrillic_semibold"):
        font_id = QFontDatabase.addApplicationFont(str(config.asset(key)))
        if font_id == -1:
            raise RuntimeError("Не найден шрифт Muli/Mulish. Распакуйте архив программы полностью.")
    # Explicit family order is supported by the pinned PySide6 6.8 runtime.
    # Muli lacks Cyrillic; bundled Mulish supplies those glyphs.
    font = QFont()
    font.setFamilies(["Muli", "Mulish"])
    font.setPixelSize(15)
    app.setFont(font)
    # Includes dialogs, tooltips and popup menus, not just the main window.
    app.setStyleSheet(config.asset("stylesheet").read_text(encoding="utf-8"))
    return app, config


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--admin-operations":
        from app.core.admin_operations import main as admin_main
        sys.argv = [sys.argv[0], sys.argv[2]]
        return admin_main()
    app = QApplication.instance() or QApplication(sys.argv)
    try:
        app, config = create_application()
        setup_logging(config)
        logger = logging.getLogger("shatli_flux")

        def report_error(kind, value, traceback):
            logger.error("Ошибка интерфейса", exc_info=(kind, value, traceback))
            QMessageBox.warning(
                None, "Shatli FLUX",
                "Не удалось выполнить действие. Закройте программу и повторите запуск.\n"
                "Подробности сохранены в журнале приложения.",
            )

        sys.excepthook = report_error
        window = MainWindow(config)
        window.show()
        logger.info("Запуск версии %s, без повышения прав", config.values["version"])
        return app.exec()
    except Exception:
        logging.getLogger("shatli_flux").exception("Ошибка запуска")
        QMessageBox.critical(
            None, "Shatli FLUX",
            "Не удалось запустить интерфейс. Проверьте, что архив полностью распакован,\n"
            "зависимости установлены и папка данных пользователя доступна для записи.",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
