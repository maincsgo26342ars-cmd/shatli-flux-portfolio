from pathlib import Path
from threading import Event

from PySide6.QtCore import QByteArray, QEasingCurve, QEvent, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QScrollArea,
    QSizeGrip, QStackedWidget, QVBoxLayout, QWidget, QMessageBox, QProgressBar, QFileDialog,
)

from app.core.windows import supported_windows
from app.core.system_info import read_system_info
from app.core.diagnostics import build_diagnostic_report
from app.core.benchmark import (
    BENCHMARK_SECONDS, BenchmarkCancelled, BenchmarkStore, format_benchmarks, run_benchmark,
)
from app.core.game_benchmark import (
    GAME_BENCHMARK_DELAY, GAME_BENCHMARK_SECONDS, GameBenchmarkStore,
    format_game_benchmarks, run_game_benchmark,
)
from app.core.reporting import export_html
from app.core.restore import create_restore_point, open_restore_wizard
from app.core.catalog import BY_ID, PRESETS
from app.core.preferences import Preferences
from app.core.backup_store import BackupStore
from app.core.tweaks import TweakEngine
from app.core.windows_backend import WindowsBackend
from app.core.auth import AuthService
from app.core.app_features import autostart_enabled, set_autostart, create_shortcut as make_shortcut
from app.core.release import verify_release, newer
from app.core.steam import discover as discover_steam
from app.ui.profile import ProfileController
from app.ui.jobs import Job
from app.ui.background import DarkSurface
from app.ui.pages import (
    TABS, DetailsDialog, SecurityDialog, TweakInfoDialog, diagnostics_page, game_benchmark_page,
    group_page, home_page, ping_page, profile_page,
)
from app.ui.widgets import Button, label


class TitleBar(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self._drag_position = None
        self.setFixedHeight(36)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(7)
        icons = window.config.asset("icons")
        monogram = label("", "accent", False)
        monogram.setFixedSize(32, 32)
        monogram.setAlignment(Qt.AlignmentFlag.AlignCenter)
        monogram.setPixmap(QIcon(str(icons / "app.png")).pixmap(32, 32))
        monogram.setAccessibleName("Логотип Shatli FLUX")
        brand = label("Shatli FLUX", "heading", False)
        for item in (monogram, brand):
            item.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            row.addWidget(item)
        row.addStretch()
        row.addSpacing(16)
        security = Button(variant="chrome", icon_path=icons / "shield.svg")
        security.setToolTip("Безопасность")
        security.setAccessibleName("Открыть безопасность")
        security.clicked.connect(window.open_security)
        minimize = Button(variant="chrome", icon_path=icons / "minus.svg")
        minimize.setToolTip("Свернуть")
        minimize.setAccessibleName("Свернуть окно")
        minimize.clicked.connect(window.showMinimized)
        maximize = Button(variant="chrome", icon_path=icons / "maximize.svg")
        maximize.setToolTip("Развернуть")
        maximize.setAccessibleName("Развернуть окно")
        maximize.clicked.connect(window.toggle_maximize)
        close = Button(variant="chrome", icon_path=icons / "close.svg")
        close.setToolTip("Закрыть")
        close.setAccessibleName("Закрыть окно")
        close.clicked.connect(window.close)
        self.maximize = maximize
        for button in (security, minimize, maximize, close):
            button.setFixedSize(32, 32)
            row.addWidget(button)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.window.windowHandle().startSystemMove():
                return
            self._drag_position = event.globalPosition().toPoint() - self.window.pos()
            event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.window.toggle_maximize()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_position is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window.move(event.globalPosition().toPoint() - self._drag_position)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_position = None


class MainWindow(DarkSurface):
    def __init__(self, config, start_system=True, engine=None, auth=None):
        super().__init__(config)
        self.windows_available = supported_windows()
        self.jobs = []
        self.operation_busy = False
        self.action_buttons = []
        self.preferences = Preferences(config.user_data() / "preferences.json")
        self.selected_tweaks = set(self.preferences.data["selected_tweaks"])
        self.tweak_switches = {}
        self.tweak_checkboxes = {}
        self.tweak_states = {}
        self.journal_cache = {"tweaks": {}, "events": []}
        self.journal_error = ""
        self.benchmark_seconds = BENCHMARK_SECONDS
        self.benchmark_store = BenchmarkStore(config.user_data() / "benchmark.json")
        self.benchmark_data = self.benchmark_store.load()
        self.benchmark_cancel = None
        self.game_benchmark_seconds = GAME_BENCHMARK_SECONDS
        self.game_benchmark_delay = GAME_BENCHMARK_DELAY
        self.game_benchmark_store = GameBenchmarkStore(config.user_data() / "game_benchmark.json")
        self.game_benchmark_data = self.game_benchmark_store.load()
        self.game_benchmark_cancel = None
        self.system_loading = False
        self.diagnostics_requested = False
        self.start_system_enabled = start_system
        self.diagnostic_data = None
        self.recommended_preset = None
        self.engine = engine
        self.auth = auth or AuthService(config)
        if self.engine is None and self.windows_available:
            folder = config.user_data()
            backend = WindowsBackend(config)
            backend.configure_cs2(autoexec=self.preferences.data["cs2_autoexec"],
                                  executable_path=self.preferences.data["cs2_executable"])
            self.engine = TweakEngine(
                BackupStore(folder / config.values["paths"]["backup"],
                            folder / config.values["paths"]["backup_lock"]), backend)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle(config.values["product_name"])
        self.setWindowIcon(QIcon(str(config.asset("icons") / "app.ico")))
        self.setStyleSheet(config.asset("stylesheet").read_text(encoding="utf-8"))
        dimensions = config.values["window"]
        self.setMinimumSize(dimensions["min_width"], dimensions["min_height"])
        self.resize(dimensions["width"], dimensions["height"])
        self._restore_geometry(dimensions)
        self.setMouseTracking(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 4, 8, 6)
        root.setSpacing(6)
        self.titlebar = TitleBar(self)
        root.addWidget(self.titlebar)

        body = QHBoxLayout()
        body.setSpacing(10)
        root.addLayout(body, 1)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(180)
        sidebar.setStyleSheet(
            "QWidget#sidebar { background: #000000; border: 1px solid #292929; border-radius: 0px; }")
        nav = QVBoxLayout(sidebar)
        nav.setContentsMargins(6, 8, 6, 8)
        nav.setSpacing(2)
        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)
        self.tabs = []
        for index, name in enumerate(TABS):
            if index == 0:
                nav.addWidget(label("РАБОТА С ПК", "section", False))
            elif index == 3:
                nav.addSpacing(8)
                nav.addWidget(label("НАСТРОЙКИ", "section", False))
            elif index == 8:
                nav.addStretch(1)
                nav.addWidget(label("ПОМОЩЬ", "section", False))
            button = Button(name, "tab")
            button.setCheckable(True)
            button.setAccessibleName(name)
            key = index + 1 if index < 9 else 0
            button.setToolTip(f"{name} · Ctrl+{key}")
            self.tab_group.addButton(button, index)
            self.tabs.append(button)
            nav.addWidget(button)
            shortcut = QShortcut(QKeySequence(f"Ctrl+{key}"), self)
            shortcut.activated.connect(lambda i=index: self.set_tab(i))
        body.addWidget(sidebar)

        content = QVBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(8)
        body.addLayout(content, 1)
        self.stack = QStackedWidget()
        self.stack.setStyleSheet("QStackedWidget { background: transparent; }")
        pages = [home_page(self), diagnostics_page(self), game_benchmark_page(self)]
        pages += [group_page(self, name) for name in TABS[3:8]]
        pages += [ping_page(self), profile_page(self, self.open_security)]
        for page in pages:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(page)
            self.stack.addWidget(scroll)
        self.render_benchmarks()
        self.render_game_benchmarks()
        content.addWidget(self.stack, 1)
        self.operation_status = label(
            "Готово к работе" if self.windows_available else
            "Системные операции доступны только в Windows 10/11", "body")
        content.addWidget(self.operation_status)
        self.operation_progress = QProgressBar()
        self.operation_progress.setVisible(False)
        content.addWidget(self.operation_progress)
        self.progress_animation = QPropertyAnimation(self.operation_progress, b"value", self)
        self.progress_animation.setDuration(config.values["ui"]["animation_duration_ms"])
        self.progress_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.fade_effect = QGraphicsOpacityEffect(self.stack)
        self.stack.setGraphicsEffect(self.fade_effect)
        self.fade = QPropertyAnimation(self.fade_effect, b"opacity")
        self.fade.setDuration(config.values["ui"]["animation_duration_ms"])
        self.fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.fade.finished.connect(self._finish_fade)
        self.fade_effect.setEnabled(False)
        self.tab_group.idClicked.connect(self.set_tab)
        self.tabs[0].setChecked(True)
        footer = QHBoxLayout()
        footer.setSpacing(12)
        footer.addWidget(label(f"Shatli FLUX {config.values['version']}", "small", False))
        footer.addStretch()
        self.subscription_status = label("Подписка: нет входа", "small", False)
        footer.addWidget(self.subscription_status)
        grip = QSizeGrip(self)
        grip.setFixedSize(16, 16)
        grip.setToolTip("Изменить размер окна")
        footer.addWidget(grip)
        content.addLayout(footer)
        self.profile_controller = ProfileController(self, self.auth)
        self.profile_controller.initialize()
        if self.windows_available:
            try:
                self.autostart_toggle.setChecked(autostart_enabled())
            except Exception:
                self.autostart_toggle.setChecked(False)
        if self.preferences.data.get("maximized"):
            self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)
        if start_system:
            QTimer.singleShot(0, self.refresh_journal)
        else:
            self.render_journal()

    def action_button(self, text, callback, primary=False):
        button = Button(text, "primary" if primary else "")
        button.setEnabled(self.windows_available)
        button.clicked.connect(callback)
        self.action_buttons.append(button)
        return button

    def _restore_geometry(self, dimensions):
        saved = self.preferences.data.get("window_geometry")
        if not saved:
            return
        try:
            restored = self.restoreGeometry(QByteArray.fromBase64(saved.encode("ascii")))
        except (ValueError, UnicodeError):
            return
        screen = self.screen()
        if (not restored or screen is None
                or not screen.availableGeometry().intersects(self.frameGeometry())):
            self.resize(dimensions["width"], dimensions["height"])

    def persist_geometry(self):
        if self.isMinimized():
            return
        try:
            geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
            self.preferences.update(window_geometry=geometry, maximized=self.isMaximized())
        except OSError:
            pass  # A read-only profile must never block closing the app.

    def toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self._update_maximize_button()

    def _update_maximize_button(self):
        titlebar = getattr(self, "titlebar", None)
        if titlebar is None:
            return
        icons = self.config.asset("icons")
        maximized = self.isMaximized()
        titlebar.maximize.setIcon(QIcon(str(icons / ("restore.svg" if maximized else "maximize.svg"))))
        titlebar.maximize.setToolTip("Восстановить" if maximized else "Развернуть")
        titlebar.maximize.setAccessibleName("Восстановить окно" if maximized else "Развернуть окно")

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._update_maximize_button()

    def choose_preset(self, key):
        index = self.preset_choice.findData(key)
        if index < 0:
            return
        if index == self.preset_choice.currentIndex():
            self.render_journal()
        else:
            self.preset_choice.setCurrentIndex(index)

    def sync_preset_cards(self):
        key = self.preset_choice.currentData()
        for name, card in self.preset_cards.items():
            card.blockSignals(True)
            card.setChecked(name == key)
            card.blockSignals(False)

    def show_plan(self, footer, detail=""):
        self.subscription_status.setText("Подписка: " + footer)
        self.subscription_status.setToolTip(detail or footer)
        pro = footer.startswith("PRO")
        self.plan_badge.setText("PRO" if pro else "FREE")
        self.plan_badge.setProperty("tier", "pro" if pro else "free")
        self.plan_badge.setAccessibleName("Подписка " + ("PRO" if pro else "FREE"))
        self.plan_badge.setToolTip(detail or (
            "Подписка PRO активна. Откат работает и без неё." if pro
            else "Доступен бесплатный набор. Откат работает всегда."))
        self.plan_badge.style().unpolish(self.plan_badge)
        self.plan_badge.style().polish(self.plan_badge)

    def show_details(self, title, text):
        DetailsDialog(title, text, self).exec()

    def start_job(self, operation, success, *, changing=False, failure=None):
        if changing and self.operation_busy:
            return
        if changing:
            self.operation_busy = True
            for button in self.action_buttons:
                button.setEnabled(False)
            self.operation_progress.setRange(0, 0)
            self.operation_progress.setVisible(True)
            self.operation_status.setText("Выполняется операция. Дождитесь результата.")
        job = Job(operation, self)
        self.jobs.append(job)
        job.succeeded.connect(success)
        job.failed.connect(failure or self.operation_error)
        job.progress.connect(self.on_progress)
        job.finished.connect(lambda: self.finish_job(job, changing))
        job.start()

    def finish_job(self, job, changing):
        self.jobs.remove(job)
        job.deleteLater()
        if changing:
            self.operation_busy = False
            for button in self.action_buttons:
                button.setEnabled(self.windows_available)
            self.recommend_button.setEnabled(
                self.windows_available and self.recommended_preset is not None)
            self.export_report_button.setEnabled(
                self.windows_available and bool(
                    self.diagnostic_data or self.benchmark_data.get("history")
                    or self.game_benchmark_data.get("history")))
            self.benchmark_cancel_button.setEnabled(False)
            self.benchmark_cancel = None
            self.game_cancel_button.setEnabled(False)
            self.game_benchmark_cancel = None
            self.operation_progress.setVisible(False)
            self.refresh_journal()

    def on_progress(self, amount, message):
        self.operation_progress.setRange(0, 100)
        self.operation_progress.setValue(amount)
        self.operation_status.setText(message)
        if self.benchmark_cancel is not None:
            self.benchmark_status.setText(message)
        if self.game_benchmark_cancel is not None:
            self.game_benchmark_status.setText(message)

    def operation_error(self, message):
        self.operation_status.setText(message.splitlines()[0][:180])
        self.operation_status.setToolTip(message)
        QMessageBox.warning(self, "Shatli FLUX", message)

    def load_system(self):
        if self.system_loading or self.operation_busy:
            return
        self.diagnostics_requested = True
        self.system_loading = True
        self.refresh_diagnostics_button.setEnabled(False)
        self.refresh_diagnostics_button.setText("Обновление…")
        self.diagnostic_summary.setText("Читаю актуальные сведения Windows…")

        def failed(message):
            self.system_loading = False
            self.refresh_diagnostics_button.setText("Проверить снова")
            self.refresh_diagnostics_button.setEnabled(self.windows_available)
            self.show_system(dict(cpu="Не определено", gpu="Не определено",
                                  ram="Не определено", windows="Не определено"))
            self.diagnostic_data = None
            self.recommended_preset = None
            self.diagnostic_summary.setText("Диагностика недоступна. Попробуйте проверить ПК позже.")
            self.diagnostic_report.setPlainText(message)
            self.diagnostic_details_button.setEnabled(False)
            self.recommend_button.setEnabled(False)
            self.export_report_button.setEnabled(bool(
                self.benchmark_data.get("history") or self.game_benchmark_data.get("history")))
            self.operation_error(message)
        self.start_job(lambda _: read_system_info(self.config), self.show_system, failure=failed)

    def show_system(self, data):
        self.system_loading = False
        self.refresh_diagnostics_button.setText("Проверить снова")
        self.refresh_diagnostics_button.setEnabled(self.windows_available)
        for key, item in self.system_labels.items():
            prefix = {"cpu": "CPU", "gpu": "GPU", "ram": "ОЗУ", "windows": "Windows"}[key]
            full = str(data[key])
            # Full values remain accessible through tooltips, without wide chips.
            item.setText(f"{prefix} · {full[:34] + '…' if len(full) > 35 else full}")
            item.setToolTip(full)
        self.diagnostic_data = build_diagnostic_report(data)
        self.recommended_preset = self.diagnostic_data["recommended_preset"]
        count = self.diagnostic_data["recommendation_count"]
        self.diagnostic_summary.setText(
            f"Найдено рекомендаций: {count} · {self.diagnostic_data['recommended_title']}")
        self.diagnostic_report.setPlainText(self.diagnostic_data["text"])
        self.diagnostic_details_button.setEnabled(True)
        self.recommend_button.setEnabled(self.windows_available and not self.operation_busy)
        self.export_report_button.setEnabled(self.windows_available)
        self.recommendation_status.setText(
            self.diagnostic_data["recommendation_reason"] +
            " Нажатие только выберет пресет, ничего не применяя.")

    def choose_recommended_preset(self):
        if not self.recommended_preset:
            return
        index = self.preset_choice.findData(self.recommended_preset)
        if index < 0:
            self.operation_error("Рекомендованный пресет отсутствует в каталоге. Ничего не изменено.")
            return
        self.preset_choice.setCurrentIndex(index)
        self.recommendation_status.setText(
            f"Выбран пресет «{self.diagnostic_data['recommended_title']}». Проверьте его на главной; применение не выполнялось.")
        self.operation_status.setText("Рекомендованный пресет выбран. Настройки Windows не изменялись.")
        self.set_tab(0)

    def render_benchmarks(self):
        self.benchmark_report.setPlainText(format_benchmarks(self.benchmark_data))
        self.export_report_button.setEnabled(
            self.windows_available and bool(
                self.diagnostic_data or self.benchmark_data.get("history")
                or self.game_benchmark_data.get("history")))

    def request_benchmark(self, slot):
        if self.operation_busy or not self.windows_available:
            return
        title = "Замер ДО" if slot == "before" else "Замер ПОСЛЕ"
        if self.benchmark_data.get(slot):
            text = "Существующий результат будет перезаписан. История сохранится."
            if slot == "before" and self.benchmark_data.get("after"):
                text += " Текущий результат ПОСЛЕ будет убран из сравнения до нового замера."
            answer = QMessageBox.question(
                self, title, text + " Продолжить?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        cancel_event = Event()
        self.benchmark_cancel = cancel_event
        self.benchmark_status.setText("Подготовка замера…")
        self.benchmark_cancel_button.setEnabled(True)

        def measure(progress):
            result = run_benchmark(self.config, progress, self.benchmark_seconds, cancel_event)
            if cancel_event.is_set():
                raise BenchmarkCancelled("Замер отменён. Неполный результат не сохранён.")
            return self.benchmark_store.record(slot, result)

        self.start_job(measure, self.accept_benchmarks, changing=True,
                       failure=self.benchmark_failed)
        self.benchmark_cancel_button.setEnabled(True)

    def cancel_benchmark(self):
        if self.benchmark_cancel is None:
            return
        self.benchmark_cancel.set()
        self.benchmark_cancel_button.setEnabled(False)
        self.benchmark_status.setText("Отменяю замер. Неполный результат не будет сохранён…")
        self.operation_status.setText("Отмена замера…")

    def benchmark_failed(self, message):
        if self.benchmark_cancel is not None and self.benchmark_cancel.is_set():
            self.benchmark_status.setText("Замер отменён. Предыдущие результаты не изменены.")
            self.operation_status.setText("Замер отменён пользователем.")
            return
        self.benchmark_status.setText("Замер не завершён. Предыдущие результаты сохранены.")
        self.operation_error(message)

    def accept_benchmarks(self, data):
        self.benchmark_data = data
        self.render_benchmarks()
        self.benchmark_status.setText("Замер сохранён. Можно выполнить следующий или начать новую серию.")
        self.operation_status.setText("Замер сохранён локально. Настройки Windows не изменялись.")

    def request_new_series(self):
        if self.operation_busy:
            return
        if self.benchmark_data.get("before") or self.benchmark_data.get("after"):
            answer = QMessageBox.question(
                self, "Новая серия",
                "Текущие ДО и ПОСЛЕ будут убраны со сравнения. До 20 последних замеров останутся в истории. Продолжить?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.benchmark_data = self.benchmark_store.new_series()
        self.render_benchmarks()
        self.benchmark_status.setText("Новая серия готова. Сначала выполните замер ДО.")

    def render_game_benchmarks(self):
        self.game_benchmark_report.setPlainText(format_game_benchmarks(self.game_benchmark_data))
        self.export_report_button.setEnabled(
            self.windows_available and bool(
                self.diagnostic_data or self.benchmark_data.get("history")
                or self.game_benchmark_data.get("history")))

    def request_game_benchmark(self, slot):
        if self.operation_busy or not self.windows_available:
            return
        title = "Игровой замер ДО" if slot == "before" else "Игровой замер ПОСЛЕ"
        if self.game_benchmark_data.get(slot):
            text = "Существующий игровой результат будет перезаписан. История сохранится."
            if slot == "before" and self.game_benchmark_data.get("after"):
                text += " Текущий результат ПОСЛЕ будет убран из сравнения."
            answer = QMessageBox.question(
                self, title, text + " Продолжить?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        delay = self.game_delay_input.value()
        duration = self.game_duration_input.value()
        answer = QMessageBox.information(
            self, title,
            "1. Останьтесь в меню CS2, тестовую карту пока не запускайте.\n"
            "2. После «ОК» подтвердите UAC, вернитесь в CS2 и сразу запустите карту.\n"
            f"3. Запись начнётся через {delay} сек. и продлится {duration} сек.\n\n"
            "Отсрочка включает возврат в игру, загрузку карты и стартовый таймер. "
            "Программа не управляет игрой, не читает её память и не изменяет файлы CS2.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Ok)
        if answer != QMessageBox.StandardButton.Ok:
            return
        cancel_event = Event()
        self.game_benchmark_cancel = cancel_event
        self.game_benchmark_status.setText("Подготовка игрового замера…")
        self.game_delay_input.setEnabled(False)
        self.game_duration_input.setEnabled(False)
        self.game_cancel_button.setEnabled(True)

        def measure(progress):
            result = run_game_benchmark(
                self.config, progress, duration, delay, cancel_event)
            if cancel_event.is_set():
                raise BenchmarkCancelled("Игровой замер отменён. Неполный результат не сохранён.")
            return self.game_benchmark_store.record(slot, result)

        self.start_job(measure, self.accept_game_benchmarks, changing=True,
                       failure=self.game_benchmark_failed)
        self.game_cancel_button.setEnabled(True)

    def cancel_game_benchmark(self):
        if self.game_benchmark_cancel is None:
            return
        self.game_benchmark_cancel.set()
        self.game_cancel_button.setEnabled(False)
        self.game_benchmark_status.setText("Отменяю игровой замер. Неполный результат не будет сохранён…")
        self.operation_status.setText("Отмена игрового замера…")

    def game_benchmark_failed(self, message):
        self.game_delay_input.setEnabled(True)
        self.game_duration_input.setEnabled(True)
        if self.game_benchmark_cancel is not None and self.game_benchmark_cancel.is_set():
            self.game_benchmark_status.setText("Игровой замер отменён. Предыдущие результаты не изменены.")
            self.operation_status.setText("Игровой замер отменён пользователем.")
            return
        self.game_benchmark_status.setText("Игровой замер не завершён. Предыдущие результаты сохранены.")
        self.operation_error(message)

    def accept_game_benchmarks(self, data):
        self.game_delay_input.setEnabled(True)
        self.game_duration_input.setEnabled(True)
        self.game_benchmark_data = data
        self.render_game_benchmarks()
        self.game_benchmark_status.setText(
            "Игровой замер сохранён. Повторите ту же сцену для другого состояния.")
        self.operation_status.setText("FPS и время кадра сохранены локально. Настройки CS2 не изменялись.")

    def request_new_game_series(self):
        if self.operation_busy:
            return
        if self.game_benchmark_data.get("before") or self.game_benchmark_data.get("after"):
            answer = QMessageBox.question(
                self, "Новая FPS-серия",
                "Текущие ДО и ПОСЛЕ будут убраны со сравнения. До 20 игровых замеров останутся в истории. Продолжить?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.game_benchmark_data = self.game_benchmark_store.new_series()
        self.render_game_benchmarks()
        self.game_benchmark_status.setText("Новая игровая серия готова. Сначала выполните замер FPS ДО.")

    def export_report(self):
        if (not self.diagnostic_data and not self.benchmark_data.get("history")
                and not self.game_benchmark_data.get("history")):
            QMessageBox.information(self, "Экспорт отчёта",
                                    "Сначала обновите диагностику или выполните замер.")
            return
        folder = Path.home() / "Documents"
        if not folder.is_dir():
            folder = Path.home()
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить отчёт Shatli FLUX", str(folder / "Shatli-FLUX-report.html"),
            "HTML-отчёт (*.html)",
        )
        if not path:
            return
        try:
            saved = export_html(path, self.config, self.diagnostic_data,
                                self.benchmark_data, self.game_benchmark_data)
        except OSError:
            self.operation_error("Не удалось сохранить отчёт. Выберите доступную для записи папку.")
            return
        self.operation_status.setText("Отчёт сохранён: " + str(saved))
        QMessageBox.information(
            self, "Отчёт сохранён",
            f"Отчёт сохранён:\n{saved}\n\nПеред передачей проверьте сведения об оборудовании и имена процессов.",
        )

    def request_restore(self):
        if self.operation_busy:
            return
        answer = QMessageBox.question(
            self, "Создание точки восстановления",
            "Будет запрошено разрешение администратора для создания точки восстановления. "
            "Настройки защиты системы не изменяются. Продолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.start_job(lambda _: create_restore_point(self.config),
                           self.operation_status.setText, changing=True)

    def request_wizard(self):
        self.start_job(lambda _: open_restore_wizard(self.config),
                       self.operation_status.setText, changing=True)

    def refresh_journal(self):
        if not self.engine:
            self.render_journal()
            return
        if self.operation_busy:
            return
        self.start_job(lambda _: self.engine.journal(), self.accept_journal, failure=self.journal_failed)

    def journal_failed(self, message):
        self.journal_error = message
        self.render_journal()
        self.operation_error(message)

    def accept_journal(self, data):
        self.journal_cache = data
        self.journal_error = ""
        self.render_journal()

    def render_journal(self):
        records = self.journal_cache["tweaks"]
        key = self.preset_choice.currentData()
        selected = (self.selected_tweaks if key == "custom"
                    else set(PRESETS.get(key, ("", ()))[1]))
        count = sum(records.get(tid, {}).get("status") == "applied" for tid in selected)
        self.status_ring.set_counts(count, len(selected))
        unfinished = sum(r.get("status") in ("pending", "recovery_required") for r in records.values())
        if self.journal_error:
            summary = "Не удалось прочитать состояние"
        elif unfinished:
            summary = f"Нужен откат: {unfinished}"
        elif not selected:
            summary = "Выберите настройки в разделах слева"
        elif count == len(selected):
            summary = f"Готово · применено {count} из {len(selected)}"
        elif count:
            summary = f"Применено: {count} из {len(selected)}"
        else:
            summary = f"Готово к оптимизации · {len(selected)} настроек"
        self.journal_status.setText(summary)
        self.home_metrics.setText(f"В наборе: {len(selected)} · применено: {count}")
        for tid, switch in self.tweak_switches.items():
            state = records.get(tid, {}).get("status")
            active = bool(state and state != "reverted")
            switch.blockSignals(True)
            switch.setChecked(active)
            switch.setText("Откат" if state in ("pending", "recovery_required")
                           else "Вкл" if active else "Выкл")
            switch.blockSignals(False)
            blocked = None if active else next(
                (other for other in BY_ID[tid].excludes
                 if records.get(other, {}).get("status") not in (None, "reverted")), None)
            switch.setEnabled(self.windows_available and not self.operation_busy
                              and blocked is None)
            switch.setToolTip(
                f"Сначала откатите «{BY_ID[blocked].title}»: настройки взаимоисключающие."
                if blocked else "Применить или откатить: " + BY_ID[tid].title)
            self.tweak_states[tid].setText(
                "Состояние неизвестно: ошибка чтения журнала." if self.journal_error else
                {"applied": "Применён по журналу. Текущее значение проверяется перед повторной записью.",
                 "pending": "Операция не завершена. Исходные значения сохранены; доступен откат.",
                 "recovery_required": "Нужен откат или разбор конфликта. Резервная копия сохранена.",
                 "reverted": "Откат подтверждён."}.get(state, "Этой программой не применялся.")
            )

    def select_tweak(self, tweak_id, selected):
        if selected:
            for other in BY_ID[tweak_id].excludes:
                if other in self.selected_tweaks:
                    self.selected_tweaks.discard(other)
                    checkbox = self.tweak_checkboxes.get(other)
                    if checkbox is not None:
                        checkbox.blockSignals(True)
                        checkbox.setChecked(False)
                        checkbox.blockSignals(False)
                    self.operation_status.setText(
                        f"«{BY_ID[other].title}» снят из набора: настройки взаимоисключающие.")
            self.selected_tweaks.add(tweak_id)
        else:
            self.selected_tweaks.discard(tweak_id)
        try:
            self.preferences.update(selected_tweaks=sorted(self.selected_tweaks))
        except OSError:
            self.operation_error("Не удалось сохранить выбранный набор. Проверьте доступ к папке данных.")
        self.render_journal()

    def _configure_cs2(self, key, value):
        try:
            if self.engine is not None and isinstance(self.engine.backend, WindowsBackend):
                self.engine.backend.configure_cs2(**{key: value})
            self.preferences.update(**{key: value})
            self.cs2_path_label.setText(self.preferences.data["cs2_autoexec"] or "autoexec.cfg не выбран")
            self.cs2_exe_label.setText(self.preferences.data["cs2_executable"] or "cs2.exe не выбран")
        except Exception as exc:
            self.operation_error(str(exc))

    def detect_cs2(self):
        if not self.windows_available or self.operation_busy:
            return
        self.cs2_detect_status.setText("Ищу библиотеки Steam и CS2…")

        def found(result):
            autoexec = result.get("autoexec")
            executable = result.get("executable")
            if not autoexec and not executable:
                self.cs2_detect_status.setText(
                    "CS2 не найдена в библиотеках Steam. Укажите autoexec.cfg и cs2.exe вручную.")
                return
            if autoexec:
                self._configure_cs2("autoexec", autoexec)
            if executable:
                self._configure_cs2("executable_path", executable)
            names = [name for name, value in (
                ("autoexec.cfg", autoexec), ("cs2.exe", executable)) if value]
            self.cs2_detect_status.setText(
                "Найдено: " + " и ".join(names) + ". Пути сохранены; файлы не изменялись.")

        def failed(message):
            self.cs2_detect_status.setText(message)

        self.start_job(lambda _: discover_steam(), found, failure=failed)

    def choose_autoexec(self):
        path, _ = QFileDialog.getOpenFileName(self, "Выбор autoexec.cfg", "", "Конфигурация CS2 (autoexec.cfg)")
        if path:
            self._configure_cs2("autoexec", path)

    def choose_cs2_exe(self):
        path, _ = QFileDialog.getOpenFileName(self, "Выбор cs2.exe", "", "Counter-Strike 2 (cs2.exe)")
        if path:
            self._configure_cs2("executable_path", path)

    def change_autostart(self, enabled):
        try:
            set_autostart(enabled)
            self.operation_status.setText("Автозапуск включён." if enabled else "Автозапуск отключён.")
        except Exception as exc:
            self.autostart_toggle.blockSignals(True)
            self.autostart_toggle.setChecked(not enabled)
            self.autostart_toggle.blockSignals(False)
            self.operation_error(str(exc))

    def create_shortcut(self):
        self.start_job(lambda _: make_shortcut(self.config), self.operation_status.setText)

    def check_updates(self):
        def loaded(document):
            release = verify_release(document, self.config)
            if not newer(release["version"], self.config.values["version"]):
                self.version_status.setText("Установлена актуальная версия · " + self.config.values["version"])
                return
            if QMessageBox.question(self, "Доступно обновление",
                    f"Версия {release['version']}\n\n{release['notes']}\n\nОткрыть страницу загрузки? "
                    "Файл не будет установлен автоматически.") == QMessageBox.StandardButton.Yes:
                self.profile_controller.open_download(release["download_url"])
        self.start_job(lambda: self.auth.call("GET", "/latest"), loaded, failure=lambda message: self.version_status.setText(message))

    def request_selected(self):
        key = self.preset_choice.currentData()
        if key == "free":
            if not self.operation_busy:
                self.start_job(lambda _: self.auth.free_preset(), lambda ids: self.request_batch(ids, revert=False))
            return
        ids = sorted(self.selected_tweaks) if key == "custom" else list(PRESETS[key][1])
        self.request_batch(ids, revert=False, custom=True)

    def request_revert_all(self):
        self.request_batch(None, revert=True)

    def request_tweak(self, tweak_id, checked):
        self.render_journal()  # A click is a request, not proof of successful application.
        self.request_batch([tweak_id], revert=not checked)

    def request_batch(self, ids, *, revert, custom=False):
        if self.operation_busy or not self.engine:
            return
        if ids == []:
            QMessageBox.information(self, "Свой набор", "Отметьте «В свой набор» у нужных настроек.")
            return
        names = list(ids) if ids is not None else [
            tid for tid, record in self.journal_cache["tweaks"].items()
            if record["status"] != "reverted"
        ]
        if not names:
            QMessageBox.information(self, "Откат", "В журнале нет изменений для отката.")
            return
        if not revert:
            applied = {tid for tid, record in self.journal_cache["tweaks"].items()
                       if record.get("status") not in (None, "reverted")}
            for tweak_id in names:
                conflict = next((other for other in BY_ID[tweak_id].excludes
                                 if other in names or other in applied), None)
                if conflict:
                    QMessageBox.warning(
                        self, "Несовместимые настройки",
                        f"«{BY_ID[tweak_id].title}» и «{BY_ID[conflict].title}» нельзя применять "
                        "одновременно.\n\nОставьте в наборе только одну из них. Если вторая уже "
                        "применена, сначала откатите её.")
                    return
        if revert:
            title = "Откатить настройки?"
            text = (f"Будет отменено изменений: {len(names)}.\n\n"
                    "Shatli FLUX восстановит сохранённые исходные значения. "
                    "Чужие изменения перезаписываться не будут.")
        elif len(names) == 1:
            tweak = BY_ID[names[0]]
            title = "Применить настройку?"
            text = (f"{tweak.title}\n\n{tweak.risk}\n\n"
                    "Исходное значение будет сохранено для отката.")
        else:
            preset_ids = PRESETS.get(self.preset_choice.currentData(), ("", ()))[1]
            is_preset = custom or set(names) == set(preset_ids)
            title = (f"Применить пресет «{self.preset_choice.currentText()}»?"
                     if is_preset else "Применить настройки?")
            text = (f"Будет применено настроек: {len(names)}.\n\n"
                    "Исходные значения сохранятся автоматически. "
                    "Вернуть их можно кнопкой «Откатить всё».")
        if QMessageBox.question(
            self, title, text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        def apply(progress):
            self.auth.authorize_apply(ids, custom=custom)
            return self.engine.apply_many(ids, progress)

        operation = (lambda progress: self.engine.revert_many(ids, progress)) if revert else apply
        self.start_job(operation, self.accept_batch, changing=True)

    def accept_batch(self, message):
        self.operation_status.setText(message)
        self.home_result.setText(
            "Готово. Исходные настройки восстановлены."
            if message.startswith("Исходные") else
            "Готово. Оптимизация применена.\n"
            "Некоторым настройкам нужен перезапуск. FPS проверяется замером ПОСЛЕ."
        )
        self.home_result.setVisible(True)

    def request_info(self, tweak_id):
        if self.operation_busy or not self.engine:
            return
        self.start_job(lambda _: self.engine.describe(tweak_id),
                       lambda details: TweakInfoDialog(BY_ID[tweak_id], details, self).exec())

    def _finish_fade(self):
        self.fade_effect.setEnabled(False)

    def set_tab(self, index):
        if index == self.stack.currentIndex():
            return
        self.fade.stop()
        self.stack.setCurrentIndex(index)
        self.tabs[index].setChecked(True)
        if self.fade.duration() > 0 and not self.isMinimized():
            self.fade_effect.setEnabled(True)
            self.fade.setStartValue(0.0)
            self.fade.setEndValue(1.0)
            self.fade.start()
        else:
            self.fade_effect.setEnabled(False)
        if (index == TABS.index("Диагностика") and self.start_system_enabled
                and self.windows_available and not self.diagnostics_requested):
            self.load_system()

    def open_security(self):
        def show(data=None):
            if data is not None:
                self.accept_journal(data)
            SecurityDialog(self.config, self).exec()
        if self.engine and not self.operation_busy:
            def failed(message):
                self.journal_error = message
                show()
            self.start_job(lambda _: self.engine.journal(), show, failure=failed)
        else:
            show()

    def edges_at(self, point):
        if self.isMaximized():
            return Qt.Edge(0)
        edges = Qt.Edge(0)
        if point.x() < 8:
            edges |= Qt.Edge.LeftEdge
        if point.x() > self.width() - 8:
            edges |= Qt.Edge.RightEdge
        if point.y() < 8:
            edges |= Qt.Edge.TopEdge
        if point.y() > self.height() - 8:
            edges |= Qt.Edge.BottomEdge
        return edges

    def mousePressEvent(self, event):
        edges = self.edges_at(event.position())
        if event.button() == Qt.MouseButton.LeftButton and edges and self.windowHandle():
            self.windowHandle().startSystemResize(edges)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        edges = self.edges_at(event.position())
        if edges in (Qt.Edge.TopEdge | Qt.Edge.LeftEdge,
                     Qt.Edge.BottomEdge | Qt.Edge.RightEdge):
            cursor = Qt.CursorShape.SizeFDiagCursor
        elif edges in (Qt.Edge.TopEdge | Qt.Edge.RightEdge,
                       Qt.Edge.BottomEdge | Qt.Edge.LeftEdge):
            cursor = Qt.CursorShape.SizeBDiagCursor
        elif edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge):
            cursor = Qt.CursorShape.SizeHorCursor
        elif edges:
            cursor = Qt.CursorShape.SizeVerCursor
        else:
            cursor = Qt.CursorShape.ArrowCursor
        self.setCursor(cursor)
        super().mouseMoveEvent(event)

    def closeEvent(self, event):
        if self.jobs:
            QMessageBox.information(self, "Операция выполняется",
                                    "Дождитесь завершения текущей операции перед закрытием.")
            event.ignore()
            return
        self.fade.stop()
        self.progress_animation.stop()
        self.persist_geometry()
        super().closeEvent(event)
