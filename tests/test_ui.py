import ast
import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton

from app.main import create_application
from app.ui.pages import SecurityDialog, TABS
from app.ui.window import MainWindow

ROOT = Path(__file__).resolve().parents[1]


def wait_for_transition(window):
    # Qt timers are event-loop driven; don't assert wall-clock rendering deadlines.
    for _ in range(100):
        QTest.qWait(20)
        if not window.fade_effect.isEnabled():
            return
    pytest.fail("tab transition did not finish within two seconds")


@pytest.fixture(scope="session")
def application():
    return create_application()


@pytest.fixture
def window(application, monkeypatch, tmp_path):
    app, config = application
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr("app.ui.window.supported_windows", lambda: False)
    widget = MainWindow(config, start_system=False)
    widget.show()
    QTest.qWait(30)
    yield widget
    widget.close()
    widget.deleteLater()
    app.processEvents()


def test_initial_geometry_and_tabs(window):
    assert (window.width(), window.height()) == (1100, 720)
    assert (window.minimumWidth(), window.minimumHeight()) == (960, 640)
    assert window.stack.count() == len(TABS)
    assert len(window.tabs) == len(TABS)
    assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert window.preset_choice.currentData() == "free"


@pytest.mark.parametrize("index", range(len(TABS)))
def test_navigation(window, index):
    QTest.mouseClick(window.tabs[index], Qt.MouseButton.LeftButton)
    wait_for_transition(window)
    assert window.stack.currentIndex() == index
    assert window.tabs[index].isChecked()
    assert sum(tab.isChecked() for tab in window.tabs) == 1
    assert not window.fade_effect.isEnabled()


def test_rapid_navigation(window):
    for index in (1, 7, 2, 6, 4, 0):
        window.set_tab(index)
        QTest.qWait(10)
    wait_for_transition(window)
    assert window.stack.currentIndex() == 0
    assert not window.fade_effect.isEnabled()


def test_minimum_layout(window):
    window.resize(960, 640)
    QTest.qWait(30)
    for tab in window.tabs:
        assert tab.geometry().right() < window.width() - 16
        assert tab.width() >= tab.fontMetrics().horizontalAdvance(tab.text()) + 16
    for index in range(len(TABS)):
        window.set_tab(index)
        wait_for_transition(window)
        scroll = window.stack.widget(index)
        assert scroll.horizontalScrollBar().maximum() == 0


def test_actions_do_not_pretend_to_work(window):
    disabled = {"Оптимизировать ПК", "Проверить ПК", "FPS-тест", "Откатить всё",
                "Войти через Telegram", "Открыть PRO", "Выйти", "Обновить профиль",
                "Создать ярлык на рабочем столе", "Применить группу", "Откатить группу",
                "Проверить снова", "Выбрать пресет", "Подробнее",
                "Экспорт отчёта", "Замерить ДО", "Замерить ПОСЛЕ", "Новая серия", "Отменить",
                "FPS ДО", "FPS ПОСЛЕ", "Отменить FPS"}
    found = set()
    for button in window.findChildren(QPushButton):
        if button.text() in disabled and button not in window.tabs:
            found.add(button.text())
            assert not button.isEnabled(), button.text()
    assert found == disabled


def test_background_controls_are_removed(window):
    assert not any(c.text() == "Медленное движение фона"
                   for c in window.findChildren(QCheckBox))
    assert not hasattr(window, "motion")
    assert "background" not in window.config.values["paths"]
    assert "background_blurred" not in window.config.values["paths"]


def test_autostart_requires_windows_and_opt_in(window):
    toggle = next(c for c in window.findChildren(QCheckBox)
                  if c.text() == "Запускать вместе с Windows")
    assert not toggle.isChecked()
    assert not toggle.isEnabled()


def test_help_has_no_actions(window):
    scroll = window.stack.widget(TABS.index("Справка"))
    assert not scroll.widget().findChildren(QPushButton)


def test_security_honest_state(window):
    dialog = SecurityDialog(window.config, window)
    text = "\n".join(item.text() for item in dialog.findChildren(QLabel))
    assert "SHA-256" in text
    assert "https://t.me" in text
    assert "https://api.telegram.org" not in text
    assert "Исходные значения сохраняются до применения" in text
    dialog.deleteLater()


def test_fps_test_is_a_separate_page(window):
    fps_page = window.stack.widget(TABS.index("FPS-тест")).widget()
    diagnostics = window.stack.widget(TABS.index("Диагностика")).widget()
    assert fps_page.isAncestorOf(window.game_benchmark_report)
    assert not diagnostics.isAncestorOf(window.game_benchmark_report)
    assert window.game_before_button.text() == "FPS ДО"
    assert window.game_delay_input.value() == 20
    assert window.game_delay_input.maximum() == 120
    assert window.game_duration_input.value() == 60
    assert window.game_duration_input.maximum() == 300


def test_assets_and_config(application):
    _, config = application
    for key in ("font", "font_semibold", "font_cyrillic",
                "font_cyrillic_semibold", "icons", "stylesheet", "presentmon", "game_capture_script"):
        assert config.asset(key).exists()
    assert config.network["allowed_origins"] == ["http://127.0.0.1:8000", "https://t.me"]
    assert config.network["telegram_bot_url"] == "https://t.me/Shatli_bot"
    assert config.network["api_base_url"] == "http://127.0.0.1:8000"
    assert config.values["product_name"] == "Shatli FLUX"


def test_network_imports_are_confined_to_auth_boundary():
    forbidden = {"socket", "requests", "httpx", "urllib", "webbrowser", "QtNetwork",
                 "QDesktopServices"}
    for source in (ROOT / "app").rglob("*.py"):
        if source.name == "profile.py":
            continue  # Explicit user click opens the validated Telegram link.
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name.split(".")[0] for alias in node.names}
                assert not names & forbidden
            if isinstance(node, ast.ImportFrom):
                names = set((node.module or "").split("."))
                names.update(alias.name for alias in node.names)
                assert not names & forbidden


def test_user_data_path(application, monkeypatch, tmp_path):
    _, config = application
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert config.user_data() == tmp_path / "ShatliFlux"


def test_custom_set_ring_counts_journal_only(window):
    window.preset_choice.setCurrentIndex(window.preset_choice.findData("custom"))
    window.select_tweak("transparency_off", True)
    assert window.status_ring.total == 1
    assert window.status_ring.applied == 0
    window.accept_journal({"tweaks": {"transparency_off": {"status": "applied"}}, "events": []})
    assert window.status_ring.applied == 1
    window.accept_journal({"tweaks": {"transparency_off": {"status": "pending"}}, "events": []})
    assert window.status_ring.applied == 0
    assert window.tweak_switches["transparency_off"].text() == "Откат"


def test_home_button_uses_current_preset_and_shows_result(window, monkeypatch):
    from app.core.catalog import PRESETS
    calls = []
    monkeypatch.setattr(window, "request_batch",
                        lambda ids, **kwargs: calls.append((ids, kwargs)))
    window.preset_choice.setCurrentIndex(window.preset_choice.findData("balanced"))
    optimize = next(button for button in window.findChildren(QPushButton)
                    if button.text() == "Оптимизировать ПК")
    optimize.setEnabled(True)
    QTest.mouseClick(optimize, Qt.MouseButton.LeftButton)
    assert calls == [(list(PRESETS["balanced"][1]), {"revert": False, "custom": True})]
    window.accept_batch("Применение подтверждено.")
    assert "Готово" in window.home_result.text()
    assert "замером ПОСЛЕ" in window.home_result.text()
    window.accept_batch("Исходные значения восстановлены.")
    assert window.home_result.text() == "Готово. Исходные настройки восстановлены."


def test_tweak_controls_in_every_group(window):
    assert len(window.tweak_switches) == 23
    for name in ("Игра", "Система", "Ввод", "Питание", "Приватность"):
        assert window.stack.widget(TABS.index(name)).findChildren(QPushButton)


def test_navigation_matches_square_reference_style():
    qss = (ROOT / "app/ui/theme.qss").read_text()
    assert 'background: #0178D4;' in qss
    assert 'background: #333333;' in qss
    assert "text-align: left;" in qss
    assert "#A8D5C8" not in qss
    assert not any("border-radius:" in line and "0px" not in line
                   for line in qss.splitlines())


def test_ui_apply_then_revert_worker_roundtrip(window, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox
    from app.core.backup_store import BackupStore
    from app.core.tweaks import TweakEngine
    from test_tweaks import FakeBackend
    store = BackupStore(tmp_path / "backup.json", tmp_path / "backup.lock")
    backend = FakeBackend(store)
    window.engine = TweakEngine(store, backend)
    window.windows_available = True
    monkeypatch.setattr(window.auth, "authorize_apply", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args))
    window.preset_choice.setCurrentIndex(window.preset_choice.findData("custom"))
    window.select_tweak("transparency_off", True)
    window.request_selected()
    for _ in range(200):
        QTest.qWait(10)
        if not window.jobs:
            break
    assert not window.jobs and not errors
    assert window.status_ring.applied == 1
    assert backend.values["transparency"]["value"] == 0
    window.request_revert_all()
    for _ in range(200):
        QTest.qWait(10)
        if not window.jobs:
            break
    assert not window.jobs and not errors
    assert window.status_ring.applied == 0
    assert backend.values["transparency"]["value"] == 1


def test_every_widget_uses_muli(window):
    from PySide6.QtWidgets import QWidget
    from PySide6.QtGui import QFontInfo
    for widget in [window, *window.findChildren(QWidget)]:
        assert QFontInfo(widget.font()).family() == "Muli"
    dialog = SecurityDialog(window.config, window)
    assert QFontInfo(dialog.font()).family() == "Muli"
    dialog.deleteLater()


@pytest.mark.parametrize("weight", [400, 600])
def test_cyrillic_is_rendered_by_bundled_mulish(application, weight):
    from PySide6.QtGui import QFont, QRawFont, QTextLayout
    app, config = application
    raw = QRawFont(str(config.asset("font_cyrillic")), 15)
    assert all(raw.supportsCharacter(ord(c)) for c in "ГлавнаяЁёЙй")
    font = QFont(app.font())
    font.setWeight(QFont.Weight(weight))
    layout = QTextLayout("ГлавнаяЁёЙй", font)
    layout.beginLayout()
    layout.createLine()
    layout.endLayout()
    runs = layout.glyphRuns()
    assert runs
    assert all(run.rawFont().familyName() == "Mulish" for run in runs)
    assert all(run.rawFont().weight() == weight for run in runs)
    assert all(index > 0 for run in runs for index in run.glyphIndexes())


def test_window_background_is_solid_dark(window):
    QTest.qWait(30)
    image = window.grab().toImage()
    assert image.pixelColor(4, window.height() // 2).name() == "#000000"
    assert set(window.config.values["ui"]) == {"animation_duration_ms"}


def test_profile_has_real_controls_without_fake_account(window):
    assert window.profile_name.text() == "Вы не вошли"
    assert "Проверка подключения" in window.profile_status.text()
    assert window.refresh_profile_button.text() == "Обновить профиль"
    assert window.device_list.count() == 0


def test_qr_is_generated_locally(application):
    from app.ui.profile import qr_pixmap
    result = qr_pixmap("https://t.me/test_bot?start=auth_test")
    assert not result.isNull()
    assert result.width() == 240


def test_ui_cancel_apply_never_runs_engine(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    class NeverCalled:
        def apply_many(self, *args):
            pytest.fail("cancelled operation was run")
    window.engine = NeverCalled()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    window.request_batch(["transparency_off"], revert=False)
    assert not window.jobs


def test_ui_rollback_never_calls_subscription_api(window, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox
    from app.core.backup_store import BackupStore
    from app.core.tweaks import TweakEngine
    from test_tweaks import FakeBackend
    store = BackupStore(tmp_path / "backup.json", tmp_path / "backup.lock")
    backend = FakeBackend(store)
    window.engine = TweakEngine(store, backend)
    window.engine.apply_many(["transparency_off"])
    window.accept_journal(window.engine.journal())
    def forbidden(*a, **k):
        pytest.fail("rollback requested auth/network")
    monkeypatch.setattr(window.auth, "authorize_apply", forbidden)
    monkeypatch.setattr(window.auth, "call", forbidden)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window.request_revert_all()
    for _ in range(200):
        QTest.qWait(10)
        if not window.jobs:
            break
    assert not window.jobs
    assert backend.values["transparency"]["value"] == 1


def test_ui_failed_access_never_changes_registry(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from app.core.windows import UserError
    class NeverCalled:
        def apply_many(self, *args):
            pytest.fail("apply was executed without access")
        def journal(self):
            return {"tweaks": {}, "events": []}
    window.engine = NeverCalled()
    def denied(*a, **k):
        raise UserError("Подписка не подтверждена")
    monkeypatch.setattr(window.auth, "authorize_apply", denied)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args))
    window.request_batch(["transparency_off"], revert=False)
    for _ in range(200):
        QTest.qWait(10)
        if not window.jobs:
            break
    assert errors
    assert not window.jobs


def test_profile_long_names_do_not_push_layout(window):
    window.profile_controller.show_profile({
        "username": "Очень длинное имя " * 5, "pro_active": False, "expires_at": 1, "days_left": 0,
        "avatar": None, "devices": [{"id": "x", "name": "Имя компьютера " * 5}],
        "current_device_id": "x"})
    window.resize(960, 640)
    window.set_tab(TABS.index("Профиль"))
    wait_for_transition(window)
    assert window.width() == 960
    assert window.stack.widget(TABS.index("Профиль")).horizontalScrollBar().maximum() == 0


def test_profile_displays_lifetime_pro(window):
    window.profile_controller.show_profile({
        "username": "hjjkjjk", "pro_active": True, "pro_lifetime": True,
        "expires_at": 0, "days_left": None, "avatar": None, "devices": [],
        "current_device_id": None})
    assert "PRO · навсегда" in window.profile_status.text()
    assert window.subscription_status.text() == "Подписка: PRO · навсегда"


def test_login_poll_expiry_and_no_overlap(window):
    import time
    from app.ui.profile import LoginDialog
    dialog = LoginDialog(window.profile_controller, {
        "session_id": "id", "poll_secret": "secret", "expires_in": 300,
        "telegram_url": "https://t.me/test_bot?start=auth_test"})
    assert dialog.timer.interval() == 2000
    dialog.in_flight = True
    dialog.poll()
    assert not window.jobs
    dialog.in_flight = False
    dialog.deadline = time.monotonic() - 1
    dialog.poll()
    assert not dialog.timer.isActive()
    assert "истёк" in dialog.status.text()
    dialog.reject()
    dialog.deleteLater()


def test_diagnostics_selects_recommendation_without_applying(window):
    window.windows_available = True
    window.show_system({
        "cpu": "TEST CPU", "gpu": "TEST GPU", "ram": "8.0 ГБ",
        "ram_bytes": 8 * 1024 ** 3, "cpu_logical": 4, "windows": "Windows 11",
        "battery_present": False, "game_mode": 1, "hags": None,
        "power_plan": "Сбалансированная", "startup_count": 3,
        "system_drive_free_bytes": 100 * 1024 ** 3,
    })
    assert window.recommended_preset == "weak"
    assert window.recommend_button.isEnabled()
    window.choose_recommended_preset()
    assert window.preset_choice.currentData() == "weak"
    assert window.stack.currentIndex() == 0
    assert not window.jobs
    assert "применение не выполнялось" in window.recommendation_status.text()


def test_benchmark_displays_saved_comparison(window):
    result = {"samples": 10, "duration_seconds": 10, "cpu_average_percent": 30.0,
              "cpu_p95_percent": 50.0, "memory_used_percent": 60.0,
              "process_count_average": 120.0, "top_processes": [],
              "recorded_at": "2026-10-31T12:00:00+00:00"}
    after = dict(result, cpu_average_percent=25.0, process_count_average=115.0)
    window.benchmark_data = {"schema": 1, "before": result, "after": after}
    window.render_benchmarks()
    text = window.benchmark_report.toPlainText()
    assert "Средняя загрузка CPU: -5.0 п.п." in text
    assert "Среднее число процессов: -5.0" in text
    assert "не FPS, время кадра или 1 % low" in text


def test_benchmark_overwrite_requires_confirmation(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    result = {"samples": 10, "duration_seconds": 10, "cpu_average_percent": 30.0,
              "cpu_p95_percent": 50.0, "memory_used_percent": 60.0,
              "process_count_average": 120.0, "top_processes": [],
              "recorded_at": "2026-10-31T12:00:00+00:00"}
    window.windows_available = True
    window.benchmark_data = {"schema": 1, "before": result, "after": None}
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    def forbidden(*args):
        pytest.fail("benchmark ran despite cancelled overwrite")
    monkeypatch.setattr("app.ui.window.run_benchmark", forbidden)
    window.request_benchmark("before")
    assert not window.jobs


def test_benchmark_cancel_is_a_normal_user_action(window, monkeypatch):
    from threading import Event
    window.benchmark_cancel = Event()
    window.benchmark_cancel_button.setEnabled(True)
    monkeypatch.setattr(window, "operation_error",
                        lambda message: pytest.fail("cancellation displayed as an error"))
    window.cancel_benchmark()
    assert window.benchmark_cancel.is_set()
    assert not window.benchmark_cancel_button.isEnabled()
    window.benchmark_failed("Замер отменён. Неполный результат не сохранён.")
    assert "Предыдущие результаты не изменены" in window.benchmark_status.text()


def test_new_series_keeps_history(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    result = {"samples": 10, "duration_seconds": 10, "cpu_average_percent": 30.0,
              "cpu_p95_percent": 50.0, "memory_used_percent": 60.0,
              "process_count_average": 120.0, "top_processes": [],
              "recorded_at": "2026-10-31T12:00:00+00:00"}
    window.benchmark_store.record("before", result)
    window.benchmark_data = window.benchmark_store.record("after", result)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window.request_new_series()
    assert window.benchmark_data["before"] is None
    assert window.benchmark_data["after"] is None
    assert len(window.benchmark_data["history"]) == 2


def test_export_report_uses_selected_file(window, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    window.windows_available = True
    window.show_system({"cpu": "TEST", "gpu": "TEST", "ram": "16.0 ГБ",
                        "ram_bytes": 16 * 1024 ** 3, "cpu_logical": 8,
                        "windows": "Windows 11", "battery_present": False})
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        lambda *a, **k: (str(tmp_path / "report.html"), "HTML"))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    window.export_report()
    text = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "Диагностика и замеры" in text
    assert "TEST" in text


def test_system_refresh_does_not_overlap(window, monkeypatch):
    calls = []
    monkeypatch.setattr(window, "start_job", lambda *a, **k: calls.append((a, k)))
    window.load_system()
    window.load_system()
    assert len(calls) == 1
    assert window.system_loading
    assert window.refresh_diagnostics_button.text() == "Обновление…"


def test_diagnostics_run_on_first_visit_only(window, monkeypatch):
    window.windows_available = True
    window.start_system_enabled = True
    calls = []
    monkeypatch.setattr(window, "start_job", lambda *a, **k: calls.append((a, k)))
    assert not window.diagnostics_requested
    window.set_tab(TABS.index("Диагностика"))
    assert len(calls) == 1
    assert window.diagnostics_requested
    window.system_loading = False
    window.set_tab(0)
    window.set_tab(TABS.index("Диагностика"))
    assert len(calls) == 1
    window.load_system()
    assert len(calls) == 2


def test_opening_app_does_not_schedule_hardware_probe(application, monkeypatch, tmp_path):
    app, config = application
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr("app.ui.window.supported_windows", lambda: False)
    probes = []
    monkeypatch.setattr(MainWindow, "load_system", lambda self: probes.append(True))
    widget = MainWindow(config)
    widget.show()
    QTest.qWait(100)
    assert not probes
    widget.close()
    widget.deleteLater()
    app.processEvents()


def test_navigation_and_progress_do_not_start_decorative_animations(window):
    from PySide6.QtCore import QAbstractAnimation
    window.set_tab(2)
    window.on_progress(47, "Замер")
    assert not window.fade_effect.isEnabled()
    assert window.operation_progress.value() == 47
    assert window.fade.state() == QAbstractAnimation.State.Stopped
    assert window.progress_animation.state() == QAbstractAnimation.State.Stopped


def game_result(average=300.0, low=150.0):
    return {"application": "cs2.exe", "source": "PresentMon 2.6.0", "frames": 18000,
            "duration_seconds": 60, "observed_seconds": 60.0,
            "average_fps": average, "fps_1_low": low, "fps_0_1_low": low / 2,
            "frame_time_average_ms": 1000 / average, "frame_time_p95_ms": 6.0,
            "frame_time_p99_ms": 8.0, "frame_time_max_ms": 20.0,
            "frame_time_stddev_ms": 1.0, "recorded_at": "2026-11-01T12:00:00+00:00"}


def test_game_benchmark_worker_saves_and_compares(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    window.windows_available = True
    window.game_delay_input.setValue(37)
    window.game_duration_input.setValue(45)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    results = iter((game_result(), game_result(average=330, low=165)))
    calls = []

    def capture(*args, **kwargs):
        calls.append((args, kwargs))
        return next(results)

    monkeypatch.setattr("app.ui.window.run_game_benchmark", capture)
    window.request_game_benchmark("before")
    for _ in range(200):
        QTest.qWait(10)
        if not window.jobs:
            break
    assert not window.jobs
    window.request_game_benchmark("after")
    for _ in range(200):
        QTest.qWait(10)
        if not window.jobs:
            break
    assert not window.jobs
    assert [call[0][2:4] for call in calls] == [(45, 37), (45, 37)]
    assert window.game_delay_input.isEnabled()
    assert window.game_duration_input.isEnabled()
    assert len(window.game_benchmark_data["history"]) == 2
    text = window.game_benchmark_report.toPlainText()
    assert "Средний FPS: +10.0%" in text
    assert "1 % low: +10.0%" in text
    assert window.export_report_button.isEnabled()


def test_game_benchmark_overwrite_cancel_never_starts(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    window.windows_available = True
    window.game_benchmark_data = window.game_benchmark_store.record("before", game_result())
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    monkeypatch.setattr("app.ui.window.run_game_benchmark",
                        lambda *a, **k: pytest.fail("capture ran despite cancelled overwrite"))
    window.request_game_benchmark("before")
    assert not window.jobs


def test_game_cancel_is_not_an_error(window):
    from threading import Event
    window.game_benchmark_cancel = Event()
    window.game_cancel_button.setEnabled(True)
    window.cancel_game_benchmark()
    assert window.game_benchmark_cancel.is_set()
    assert not window.game_cancel_button.isEnabled()
    window.game_benchmark_failed("Игровой замер отменён")
    assert "Предыдущие результаты не изменены" in window.game_benchmark_status.text()


def test_new_game_series_keeps_history(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    window.game_benchmark_store.record("before", game_result())
    window.game_benchmark_data = window.game_benchmark_store.record("after", game_result())
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window.request_new_game_series()
    assert window.game_benchmark_data["before"] is None
    assert window.game_benchmark_data["after"] is None
    assert len(window.game_benchmark_data["history"]) == 2


def test_home_preset_cards_mirror_the_selector(window):
    window.preset_choice.setCurrentIndex(window.preset_choice.findData("weak"))
    assert window.preset_cards["weak"].isChecked()
    assert not window.preset_cards["balanced"].isChecked()
    QTest.mouseClick(window.preset_cards["cs2"], Qt.MouseButton.LeftButton)
    assert window.preset_choice.currentData() == "cs2"
    assert window.preset_cards["cs2"].isChecked()
    assert "В наборе:" in window.home_metrics.text()


def test_conflicting_fps_choices_are_mutually_exclusive(window):
    window.preset_choice.setCurrentIndex(window.preset_choice.findData("custom"))
    window.select_tweak("cs2_fps_400", True)
    window.select_tweak("cs2_fps_unlimited", True)
    assert window.selected_tweaks == {"cs2_fps_unlimited"}
    assert not window.tweak_checkboxes["cs2_fps_400"].isChecked()
    window.select_tweak("cs2_fps_400", True)
    assert window.selected_tweaks == {"cs2_fps_400"}


def test_apply_is_blocked_when_the_opposite_tweak_is_applied(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    window.engine = object()
    window.accept_journal({"tweaks": {"cs2_fps_400": {"status": "applied"}}, "events": []})
    warnings, jobs = [], []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: warnings.append(args))
    monkeypatch.setattr(window, "start_job", lambda *args, **kwargs: jobs.append(args))
    window.request_batch(["cs2_fps_unlimited"], revert=False)
    assert warnings and not jobs


def test_window_can_maximize_and_restore(window):
    window.showNormal()
    assert window.titlebar.maximize.toolTip() == "Развернуть"
    QTest.mouseClick(window.titlebar.maximize, Qt.MouseButton.LeftButton)
    assert window.isMaximized()
    assert window.titlebar.maximize.toolTip() == "Восстановить"
    QTest.mouseClick(window.titlebar.maximize, Qt.MouseButton.LeftButton)
    assert not window.isMaximized()


def test_maximized_state_is_saved_and_restored(application, monkeypatch, tmp_path):
    app, config = application
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr("app.ui.window.supported_windows", lambda: False)
    first = MainWindow(config, start_system=False)
    first.show()
    first.showMaximized()
    QTest.qWait(30)
    assert first.isMaximized()
    first.close()
    assert first.preferences.data["maximized"] is True
    first.deleteLater()
    app.processEvents()
    second = MainWindow(config, start_system=False)
    second.show()
    QTest.qWait(30)
    assert second.isMaximized()
    second.close()
    second.deleteLater()
    app.processEvents()


def test_geometry_is_saved_and_restored(application, monkeypatch, tmp_path):
    app, config = application
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr("app.ui.window.supported_windows", lambda: False)
    first = MainWindow(config, start_system=False)
    first.show()
    first.resize(960, 700)
    QTest.qWait(30)
    first.close()
    assert first.preferences.data["window_geometry"]
    first.deleteLater()
    app.processEvents()
    second = MainWindow(config, start_system=False)
    second.show()
    QTest.qWait(30)
    # restoreGeometry clamps a window to the available screen; the offscreen
    # platform's logical screen shrinks under QT_SCALE_FACTOR, so accept the
    # minimum height while still rejecting the 1100x720 default.
    assert second.width() == 960
    assert second.height() in (700, second.minimumHeight())
    assert second.height() <= 700
    second.close()
    second.deleteLater()
    app.processEvents()


def test_plan_badge_follows_the_profile(window):
    window.profile_controller.show_profile({
        "username": "player", "pro_active": True, "pro_lifetime": True,
        "expires_at": 0, "days_left": None, "avatar": None, "devices": [],
        "current_device_id": None})
    assert window.plan_badge.text() == "PRO"
    assert window.plan_badge.property("tier") == "pro"
    window.profile_controller.show_profile(None)
    assert window.plan_badge.text() == "FREE"
    assert window.plan_badge.property("tier") == "free"
