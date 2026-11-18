"""Profile controller and local QR rendering. Network work runs off the GUI thread."""
import base64
import time

from PySide6.QtCore import QObject, QTimer, Qt, QUrl, QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QDesktopServices, QImage, QImageReader, QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout, QMessageBox

from app.ui.widgets import Button, label
from shared.network import api_base, bot_url, validate_url


def qr_pixmap(text):
    import qrcode  # Login-only dependency, not needed by the idle desktop UI.
    code = qrcode.QRCode(border=4, error_correction=qrcode.constants.ERROR_CORRECT_M)
    code.add_data(text)
    code.make(fit=True)
    matrix = code.get_matrix()
    image = QImage(len(matrix), len(matrix), QImage.Format.Format_RGB32)
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            image.setPixel(x, y, 0xFF101718 if dark else 0xFFFFFFFF)
    return QPixmap.fromImage(image).scaled(240, 240, Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.FastTransformation)


class LoginDialog(QDialog):
    def __init__(self, controller, session):
        super().__init__(controller.window)
        self.controller = controller
        self.session = session
        self.in_flight = False
        self.deadline = time.monotonic() + min(session["expires_in"], 300)
        self.setWindowTitle("Вход через Telegram")
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)
        layout.addWidget(label("Подтвердите вход в Telegram", "title"))
        layout.addWidget(label(
            "Сканируйте QR или откройте бота. Подтверждайте только тот вход, который начали сами."))
        image = label("")
        image.setPixmap(qr_pixmap(session["telegram_url"]))
        image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(image)
        self.status = label("Ожидание подтверждения · до 5 минут")
        layout.addWidget(self.status)
        open_button = Button("Открыть в Telegram", "primary")
        open_button.clicked.connect(lambda: controller.open_bot(session["telegram_url"]))
        layout.addWidget(open_button)
        cancel = Button("Закрыть")
        cancel.clicked.connect(self.reject)
        layout.addWidget(cancel)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.poll)
        self.finished.connect(self.timer.stop)
        self.timer.start()

    def poll(self):
        if self.in_flight:
            return
        if time.monotonic() >= self.deadline:
            self.timer.stop()
            self.status.setText("Срок входа истёк. Закройте окно и начните снова.")
            return
        self.in_flight = True

        def success(result):
            self.in_flight = False
            if result.get("status") == "authorized":
                self.timer.stop()
                self.accept()
                self.controller.refresh()

        def failure(message):
            self.in_flight = False
            self.timer.stop()
            self.status.setText(message + " Закройте окно и начните вход снова.")

        self.controller.window.start_job(lambda _: self.controller.service.poll(self.session),
                                         success, failure=failure)

    def reject(self):
        if self.in_flight:
            self.status.setText("Дождитесь ответа на текущий запрос, затем закройте окно.")
            return
        self.timer.stop()
        super().reject()

    def closeEvent(self, event):
        if self.in_flight:
            event.ignore()
        else:
            self.timer.stop()
            super().closeEvent(event)


class ProfileController(QObject):
    def __init__(self, window, service):
        super().__init__(window)
        self.window, self.service = window, service
        self.data = None
        self.busy = False
        self.dialog = None

    def run(self, operation, success):
        if self.busy:
            return
        self.busy = True
        self.render_buttons()

        def done(data):
            self.busy = False
            self.render_buttons()
            success(data)

        def failed(message):
            self.busy = False
            self.render_buttons()
            self.window.profile_status.setText(message)
            self.window.show_plan("статус не подтверждён", message)

        self.window.start_job(lambda _: operation(), done, failure=failed)

    def render_buttons(self):
        available = self.window.windows_available and self.service.configured()
        self.window.login_button.setEnabled(available and not self.busy)
        self.window.refresh_profile_button.setEnabled(available and not self.busy)
        self.window.renew_button.setEnabled(available and not self.busy)
        self.window.logout_button.setEnabled(self.window.windows_available and not self.busy)
        for i in range(self.window.device_list.count()):
            widget = self.window.device_list.itemAt(i).widget()
            if widget:
                for button in widget.findChildren(Button):
                    button.setEnabled(available and not self.busy)

    def initialize(self):
        self.render_buttons()
        if not self.service.configured():
            missing = []
            for name, check in (("адрес API и его origin", api_base),
                                ("адрес Telegram-бота и его origin", bot_url)):
                try:
                    check(self.window.config.network)
                except ValueError:
                    missing.append(name)
            self.window.profile_status.setText(
                "Подключение не настроено: " + ", ".join(missing) + ". Локальные настройки и откат доступны.")
        elif self.window.windows_available:
            self.refresh()

    def login(self):
        if self.dialog and self.dialog.isVisible():
            self.dialog.raise_()
            return
        if QMessageBox.question(
            self.window, "Вход через Telegram",
            "На настроенный сервер будут переданы имя ПК и хеш MachineGuid. "
            "После подтверждения сервер свяжет устройство с вашим Telegram ID и именем. "
            "Продолжить?\n\n" + str(self.window.config.network.get("api_base_url")),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self.run(self.service.start, self.show_login)

    def show_login(self, session):
        # Never open or encode an arbitrary link returned by the server.
        try:
            expected = bot_url(self.window.config.network) + "?start=auth_" + session["session_id"]
            if session["telegram_url"] != expected:
                raise ValueError("Сервер вернул неожиданную ссылку на бота.")
            validate_url(expected, self.window.config.network)
        except (ValueError, KeyError) as exc:
            self.window.profile_status.setText(str(exc))
            return
        self.dialog = LoginDialog(self, session)
        self.dialog.setModal(True)
        self.dialog.show()

    def open_bot(self, url=None):
        try:
            base = bot_url(self.window.config.network)
            target = url or base
            if target != base and not target.startswith(base + "?start=auth_"):
                raise ValueError("Неожиданная ссылка Telegram.")
            validate_url(target, self.window.config.network)
            if not QDesktopServices.openUrl(QUrl(target)):
                raise ValueError("Не удалось открыть Telegram в браузере.")
        except ValueError as exc:
            self.window.profile_status.setText(str(exc))

    def open_download(self, url):
        try:
            validate_url(url, self.window.config.network)
            if not QDesktopServices.openUrl(QUrl(url)):
                raise ValueError("Не удалось открыть страницу обновления.")
        except ValueError as exc:
            self.window.version_status.setText(str(exc))

    def refresh(self):
        self.run(self.service.profile, self.show_profile)

    def show_profile(self, data):
        self.data = data
        w = self.window
        w.profile_avatar.clear()
        w.profile_avatar.setText("SF")
        while w.device_list.count():
            item = w.device_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not data:
            w.renew_button.setVisible(True)
            w.profile_name.setText("Вы не вошли")
            w.profile_status.setText("Войдите через Telegram, чтобы увидеть доступ PRO и устройства.")
            w.show_plan("нет входа", "Войдите через Telegram, чтобы увидеть доступ PRO и устройства.")
            return
        w.profile_name.setText(data["username"] or "Telegram-пользователь")
        lifetime = data.get("pro_lifetime") is True
        if lifetime:
            status = "PRO · навсегда"
        elif data["pro_active"]:
            status = f"PRO · осталось дней: {data['days_left']}"
        else:
            status = "Срок PRO истёк · доступен FREE" if data["expires_at"] else "FREE · PRO не приобретён"
        w.profile_status.setText(status + "\nОткат работает без сети и покупки PRO.")
        w.renew_button.setVisible(not lifetime)
        footer = "PRO · навсегда" if lifetime else (
            f"PRO · {data['days_left']} дн." if data["pro_active"] else "FREE")
        w.show_plan(footer, status)
        if data.get("avatar"):
            try:
                raw = base64.b64decode(data["avatar"], validate=True)
                if len(raw) <= 128000:
                    buffer = QBuffer()
                    buffer.setData(QByteArray(raw))
                    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
                    reader = QImageReader(buffer)
                    size = reader.size()
                    if 0 < size.width() <= 1024 and 0 < size.height() <= 1024:
                        image = reader.read()
                        if not image.isNull():
                            w.profile_avatar.setPixmap(QPixmap.fromImage(image).scaled(
                                64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation))
            except (ValueError, TypeError):
                pass  # A missing avatar does not prevent login.
        for device in data["devices"]:
            from PySide6.QtWidgets import QWidget
            row_widget = QWidget()
            row = QHBoxLayout(row_widget)
            current = " · этот ПК" if device["id"] == data["current_device_id"] else ""
            row.addWidget(label(device["name"] + current, "body"), 1)
            button = Button("Удалить")
            button.clicked.connect(lambda _=False, d=device: self.remove(d))
            row.addWidget(button)
            w.device_list.addWidget(row_widget)
        self.render_buttons()

    def remove(self, device):
        if self.busy:
            return
        if QMessageBox.question(self.window, "Удалить устройство?",
            f"Устройство: {device['name']}\nЕго вход будет отозван. Применённые настройки "
            "не откатываются автоматически. Лимит: 5 отвязок ПК за календарный месяц UTC. "
            "Для переустановки приложения на том же ПК отвязка не нужна.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        current = self.data["current_device_id"] == device["id"]

        def done(_):
            if current:
                self.service.store.clear()
                self.show_profile(None)
            else:
                self.refresh()
        self.run(lambda: self.service.remove_device(device["id"]), done)

    def logout(self):
        def done(message):
            self.show_profile(None)
            self.window.profile_status.setText(message)
        self.run(self.service.logout, done)
