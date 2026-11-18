import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QGridLayout, QHBoxLayout, QPlainTextEdit, QPushButton,
    QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from app.core.catalog import CATALOG, PRESETS, PRESET_META, PRESET_ORDER
from app.core.release import executable_security
from app.ui.widgets import Button, StatusRing, GlassCard, label

TABS = ("Главная", "Диагностика", "FPS-тест", "Игра", "Система", "Ввод", "Питание", "Приватность", "Справка", "Профиль")


def page_layout():
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(4, 4, 6, 10)
    layout.setSpacing(10)
    return page, layout


def heading(layout, title, subtitle):
    layout.addWidget(label(title, "title"))
    layout.addWidget(label(subtitle))


def home_page(surface):
    page, layout = page_layout()
    heading(layout, "Оптимизация ПК", "Пресет, резервная копия и откат — в одном месте.")

    columns = QHBoxLayout()
    columns.setSpacing(10)

    presets = GlassCard(surface)
    presets.content.setContentsMargins(18, 16, 18, 16)
    presets.content.setSpacing(10)
    presets.content.addWidget(label("Пресеты", "heading"))
    presets.content.addWidget(label(
        "Карточка только выбирает набор. Изменения Windows начинаются после кнопки ниже.", "small"))

    choice = QComboBox()
    choice.addItem("Свой набор · PRO", "custom")
    for key, (title, _) in PRESETS.items():
        choice.addItem(title, key)
    choice.setCurrentIndex(choice.findData("free"))
    choice.setAccessibleName("Выбор пресета")
    # The combo stays the single source of truth for tests and keyboard users;
    # the cards below are the primary visual selector and mirror it.
    choice.setVisible(False)
    surface.preset_choice = choice
    choice.currentIndexChanged.connect(surface.render_journal)
    choice.currentIndexChanged.connect(surface.sync_preset_cards)
    presets.content.addWidget(choice)

    grid = QGridLayout()
    grid.setSpacing(8)
    grid.setColumnStretch(0, 1)
    grid.setColumnStretch(1, 1)
    surface.preset_cards = {}
    for position, key in enumerate(PRESET_ORDER):
        title, description, pro = PRESET_META[key]
        card = QPushButton(f"{'PRO' if pro else 'FREE'} · {title}\n{description}")
        card.setProperty("variant", "preset")
        card.setCheckable(True)
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        card.setMinimumHeight(60)
        card.setAccessibleName(f"Пресет: {title}")
        card.setToolTip(f"{title}. {description}. Применение — отдельной кнопкой.")
        card.clicked.connect(lambda _=False, name=key: surface.choose_preset(name))
        surface.preset_cards[key] = card
        grid.addWidget(card, position // 2, position % 2)
    presets.content.addLayout(grid)

    optimize = surface.action_button("Оптимизировать ПК", surface.request_selected, primary=True)
    optimize.setProperty("variant", "hero")
    optimize.style().unpolish(optimize)
    optimize.style().polish(optimize)
    optimize.setMinimumHeight(54)
    presets.content.addWidget(optimize)
    surface.home_result = label("", "body")
    surface.home_result.setVisible(False)
    presets.content.addWidget(surface.home_result)
    columns.addWidget(presets, 3)

    status = GlassCard(surface)
    status.content.setContentsMargins(18, 16, 18, 16)
    status.content.setSpacing(10)
    top = QHBoxLayout()
    top.setSpacing(8)
    top.addWidget(label("Состояние", "heading"), 1)
    surface.plan_badge = label("FREE", "badge", False)
    surface.plan_badge.setProperty("tier", "free")
    surface.plan_badge.setToolTip("Статус доступа PRO. Откат доступен бесплатно.")
    top.addWidget(surface.plan_badge)
    status.content.addLayout(top)
    ring_row = QHBoxLayout()
    ring_row.addStretch()
    surface.status_ring = StatusRing()
    surface.status_ring.setVisible(True)
    ring_row.addWidget(surface.status_ring)
    ring_row.addStretch()
    status.content.addLayout(ring_row)
    surface.journal_status = label("Загрузка состояния…", "heading")
    surface.journal_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
    status.content.addWidget(surface.journal_status)
    surface.home_metrics = label("", "small")
    surface.home_metrics.setAlignment(Qt.AlignmentFlag.AlignCenter)
    status.content.addWidget(surface.home_metrics)
    status.content.addWidget(label(
        "Резервная копия создаётся до записи. Откат бесплатный, офлайн и без подписки.", "small"))

    actions = QGridLayout()
    actions.setSpacing(8)
    actions.addWidget(surface.action_button(
        "Проверить ПК", lambda: surface.set_tab(TABS.index("Диагностика"))), 0, 0)
    actions.addWidget(surface.action_button(
        "FPS-тест", lambda: surface.set_tab(TABS.index("FPS-тест"))), 0, 1)
    actions.addWidget(surface.action_button("Откатить всё", surface.request_revert_all), 1, 0)
    help_button = Button("Справка")
    help_button.clicked.connect(lambda: surface.set_tab(TABS.index("Справка")))
    actions.addWidget(help_button, 1, 1)
    status.content.addLayout(actions)
    columns.addWidget(status, 2)

    layout.addLayout(columns)
    layout.addStretch()
    surface.sync_preset_cards()
    return page


def diagnostics_page(surface):
    page, layout = page_layout()
    heading(layout, "Ваш компьютер", "Диагностика только читает данные и ничего не меняет.")

    chips = QHBoxLayout()
    chips.setSpacing(8)
    surface.system_labels = {}
    for key, name in (("cpu", "CPU"), ("gpu", "GPU"), ("ram", "ОЗУ"), ("windows", "Windows")):
        item = label(f"{name} · нет замера", "chip")
        item.setMinimumWidth(0)
        surface.system_labels[key] = item
        chips.addWidget(item, 1)
    layout.addLayout(chips)

    report_card = GlassCard(surface)
    report_card.content.addWidget(label("Рекомендация", "heading"))
    surface.diagnostic_summary = label(
        "Диагностика выполняется при открытии раздела. Повторный замер — кнопкой «Проверить снова».", "body")
    report_card.content.addWidget(surface.diagnostic_summary)
    surface.diagnostic_report = QPlainTextEdit()
    surface.diagnostic_report.setReadOnly(True)
    surface.diagnostic_report.setVisible(False)
    report_card.content.addWidget(surface.diagnostic_report)
    report_actions = QHBoxLayout()
    surface.refresh_diagnostics_button = surface.action_button("Проверить снова", surface.load_system)
    report_actions.addWidget(surface.refresh_diagnostics_button)
    surface.recommend_button = surface.action_button(
        "Выбрать пресет", surface.choose_recommended_preset, primary=True)
    surface.recommend_button.setEnabled(False)
    report_actions.addWidget(surface.recommend_button)
    surface.diagnostic_details_button = Button("Подробнее")
    surface.diagnostic_details_button.setEnabled(False)
    surface.diagnostic_details_button.clicked.connect(
        lambda: surface.show_details("Диагностика компьютера", surface.diagnostic_report.toPlainText()))
    report_actions.addWidget(surface.diagnostic_details_button)
    surface.export_report_button = surface.action_button("Экспорт отчёта", surface.export_report)
    surface.export_report_button.setEnabled(False)
    report_actions.addWidget(surface.export_report_button)
    report_actions.addStretch()
    report_card.content.addLayout(report_actions)
    surface.recommendation_status = label("", "body")
    surface.recommendation_status.setVisible(False)
    report_card.content.addWidget(surface.recommendation_status)
    layout.addWidget(report_card)

    benchmark_card = GlassCard(surface)
    benchmark_card.content.addWidget(label("Фоновая нагрузка · до / после", "heading"))
    benchmark_actions = QHBoxLayout()
    surface.benchmark_before_button = surface.action_button(
        "Замерить ДО", lambda: surface.request_benchmark("before"), primary=True)
    surface.benchmark_after_button = surface.action_button(
        "Замерить ПОСЛЕ", lambda: surface.request_benchmark("after"))
    surface.new_series_button = surface.action_button("Новая серия", surface.request_new_series)
    surface.benchmark_cancel_button = Button("Отменить")
    surface.benchmark_cancel_button.setEnabled(False)
    surface.benchmark_cancel_button.clicked.connect(surface.cancel_benchmark)
    for button in (surface.benchmark_before_button, surface.benchmark_after_button,
                   surface.new_series_button, surface.benchmark_cancel_button):
        benchmark_actions.addWidget(button)
    benchmark_actions.addStretch()
    benchmark_card.content.addLayout(benchmark_actions)
    surface.benchmark_status = label(f"Замер занимает {surface.benchmark_seconds} секунд", "body")
    benchmark_card.content.addWidget(surface.benchmark_status)
    surface.benchmark_report = QPlainTextEdit()
    surface.benchmark_report.setReadOnly(True)
    surface.benchmark_report.setMinimumHeight(180)
    benchmark_card.content.addWidget(surface.benchmark_report)
    layout.addWidget(benchmark_card)
    layout.addStretch()
    return page


def game_benchmark_page(surface):
    page, layout = page_layout()
    heading(layout, "FPS-тест в CS2", "Замерьте одну и ту же автоматическую карту до и после оптимизации.")

    instructions = GlassCard(surface)
    instructions.content.addWidget(label("Настройка записи", "heading"))
    timing = QHBoxLayout()
    timing.setSpacing(10)
    timing.addWidget(label("Отсрочка", "body"))
    surface.game_delay_input = QSpinBox()
    surface.game_delay_input.setRange(5, 120)
    surface.game_delay_input.setValue(surface.game_benchmark_delay)
    surface.game_delay_input.setSuffix(" сек")
    surface.game_delay_input.setAccessibleName("Отсрочка записи FPS в секундах")
    surface.game_delay_input.setToolTip("Время на возврат в CS2, загрузку карты и её стартовый таймер.")
    timing.addWidget(surface.game_delay_input)
    timing.addSpacing(12)
    timing.addWidget(label("Запись", "body"))
    surface.game_duration_input = QSpinBox()
    surface.game_duration_input.setRange(5, 300)
    surface.game_duration_input.setSingleStep(5)
    surface.game_duration_input.setValue(surface.game_benchmark_seconds)
    surface.game_duration_input.setSuffix(" сек")
    surface.game_duration_input.setAccessibleName("Длительность записи FPS в секундах")
    surface.game_duration_input.setToolTip("Длительность автоматической сцены, которую нужно записать.")
    timing.addWidget(surface.game_duration_input)
    timing.addStretch()
    help_button = Button("Как замерить")
    help_button.clicked.connect(lambda: surface.set_tab(TABS.index("Справка")))
    timing.addWidget(help_button)
    instructions.content.addLayout(timing)
    layout.addWidget(instructions)

    game_card = GlassCard(surface)
    game_card.content.addWidget(label("Результат · до / после", "heading"))
    game_actions = QHBoxLayout()
    surface.game_before_button = surface.action_button(
        "FPS ДО", lambda: surface.request_game_benchmark("before"), primary=True)
    surface.game_after_button = surface.action_button(
        "FPS ПОСЛЕ", lambda: surface.request_game_benchmark("after"))
    surface.game_new_series_button = surface.action_button(
        "Новая серия", surface.request_new_game_series)
    surface.game_cancel_button = Button("Отменить FPS")
    surface.game_cancel_button.setEnabled(False)
    surface.game_cancel_button.clicked.connect(surface.cancel_game_benchmark)
    for button in (surface.game_before_button, surface.game_after_button,
                   surface.game_new_series_button, surface.game_cancel_button):
        game_actions.addWidget(button)
    game_actions.addStretch()
    game_card.content.addLayout(game_actions)
    surface.game_benchmark_status = label("CS2 должна быть запущена", "body")
    game_card.content.addWidget(surface.game_benchmark_status)
    surface.game_benchmark_report = QPlainTextEdit()
    surface.game_benchmark_report.setReadOnly(True)
    surface.game_benchmark_report.setMinimumHeight(280)
    game_card.content.addWidget(surface.game_benchmark_report)
    layout.addWidget(game_card)
    layout.addStretch()
    return page


GROUP_DESCRIPTIONS = {
    "Игра": "Windows и управляемый блок autoexec.cfg. Без изменений других файлов игры.",
    "Система": "Параметры Windows; экспериментальные настройки отмечены отдельно.",
    "Ввод": "Параметры мыши и клавиатуры. Без макросов и автоматизации ввода.",
    "Питание": "Выбор существующего плана питания Windows.",
    "Приватность": "Понятные ограничения диагностики и фоновых рекомендаций.",
}


def group_page(surface, name):
    page, layout = page_layout()
    layout.addWidget(label(name, "title"))
    ids = [tweak.id for tweak in CATALOG if tweak.group == name]
    for tweak in (item for item in CATALOG if item.group == name):
        card = GlassCard(surface)
        top = QHBoxLayout()
        title = ("Эксперимент · " if tweak.experimental else "") + tweak.title
        top.addWidget(label(title, "heading"), 1)
        selected = QCheckBox("В свой набор")
        selected.setChecked(tweak.id in surface.selected_tweaks)
        selected.toggled.connect(lambda value, tid=tweak.id: surface.select_tweak(tid, value))
        surface.tweak_checkboxes[tweak.id] = selected
        top.addWidget(selected)
        info = surface.action_button("i", lambda _=False, tid=tweak.id: surface.request_info(tid))
        info.setFixedSize(40, 40)
        info.setAccessibleName("Подробности: " + tweak.title)
        info.setToolTip(f"{tweak.description}\n\n{tweak.risk}")
        top.addWidget(info)
        switch = Button("Выкл", "switch")
        switch.setCheckable(True)
        switch.setAccessibleName("Применить или откатить: " + tweak.title)
        switch.setEnabled(surface.windows_available)
        switch.clicked.connect(lambda checked, tid=tweak.id: surface.request_tweak(tid, checked))
        surface.action_buttons.append(switch)
        surface.tweak_switches[tweak.id] = switch
        top.addWidget(switch)
        card.content.addLayout(top)
        state = label("", "body")
        state.setVisible(False)
        surface.tweak_states[tweak.id] = state
        card.content.addWidget(state)
        layout.addWidget(card)
    row = QHBoxLayout()
    row.addWidget(surface.action_button("Применить группу", lambda: surface.request_batch(ids, revert=False), primary=True))
    row.addWidget(surface.action_button("Откатить группу", lambda: surface.request_batch(ids, revert=True)))
    row.addStretch()
    layout.addLayout(row)
    layout.addStretch()
    return page


def ping_page(surface):
    page, layout = page_layout()
    heading(layout, "Справка", "Коротко о главном: что делать и как вернуть исходные настройки.")
    card = GlassCard(surface)
    card.content.addWidget(label("Порядок работы", "heading"))
    card.content.addWidget(label(
        "1. Базовый набор доступен без входа. Для PRO войдите через Telegram в «Профиле».\n"
        "2. В «Профиле» нажмите «Найти Steam и CS2» или укажите cs2.exe и autoexec.cfg вручную.\n"
        "3. При желании откройте «Диагностику» и нажмите «Выбрать пресет». Проверка ничего не меняет.\n"
        "4. Перед оптимизацией выполните «FPS ДО» на автоматической benchmark-карте CS2.\n"
        "5. На «Главной» выберите карточку пресета и нажмите «Оптимизировать ПК». Исходные значения сохранятся автоматически.\n"
        "6. В «FPS-тесте» выполните «FPS ПОСЛЕ» на той же карте и сравните результат."))
    card.content.addWidget(label("Как проходит FPS-тест", "heading"))
    card.content.addWidget(label(
        "Запустите CS2 и дождитесь главного меню. Нажмите нужный замер, подтвердите запуск и сразу вернитесь в игру. "
        "Запустите автоматическую benchmark-карту: отсрочка должна включать загрузку и стартовый таймер. "
        "Запись начнётся автоматически и завершится через заданное время. Программа не управляет игрой."))
    card.content.addWidget(label("Где находятся настройки", "heading"))
    card.content.addWidget(label(
        "Разделы «Игра», «Система», «Ввод», «Питание» и «Приватность» содержат отдельные переключатели. "
        "Нажмите i рядом с настройкой, чтобы увидеть описание, риск и значения. "
        "«В свой набор» сохраняет выбор для пресета «Свой набор»."))
    card.content.addWidget(label("Как всё вернуть", "heading"))
    card.content.addWidget(label(
        "Нажмите «Откатить всё» на главной или выключите отдельную настройку. Откат не требует сети, входа или подписки. "
        "Если значение изменила другая программа, Shatli FLUX сообщит о конфликте и не перезапишет его."))
    card.content.addWidget(label("Что программа не делает", "heading"))
    card.content.addWidget(label(
        "Не перенаправляет трафик, не обещает конкретный прирост FPS или снижение пинга, не использует макросы и антиотдачу, "
        "не отключает защиту Windows и античиты."))
    layout.addWidget(card)
    layout.addStretch()
    return page


def profile_page(surface, open_security):
    page, layout = page_layout()
    heading(layout, "Профиль", "Подписка, устройства и локальные настройки приложения.")
    row = QHBoxLayout()
    row.setSpacing(16)
    account = GlassCard(surface)
    account.content.addWidget(label("TELEGRAM", "accent"))
    identity = QHBoxLayout()
    surface.profile_avatar = label("SF", "title")
    surface.profile_avatar.setFixedSize(64, 64)
    surface.profile_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
    identity.addWidget(surface.profile_avatar)
    surface.profile_name = label("Вы не вошли", "heading")
    identity.addWidget(surface.profile_name, 1)
    account.content.addLayout(identity)
    surface.profile_status = label("Проверка подключения…")
    account.content.addWidget(surface.profile_status)
    surface.login_button = Button("Войти через Telegram", "primary")
    surface.login_button.clicked.connect(lambda: surface.profile_controller.login())
    account.content.addWidget(surface.login_button)
    account_actions = QHBoxLayout()
    surface.renew_button = Button("Открыть PRO")
    surface.renew_button.clicked.connect(lambda: surface.profile_controller.open_bot())
    surface.logout_button = Button("Выйти")
    surface.logout_button.clicked.connect(lambda: surface.profile_controller.logout())
    account_actions.addWidget(surface.renew_button)
    account_actions.addWidget(surface.logout_button)
    account.content.addLayout(account_actions)
    row.addWidget(account, 1)
    settings = GlassCard(surface)
    settings.content.addWidget(label("Настройки приложения", "heading"))
    autostart = QCheckBox("Запускать вместе с Windows")
    autostart.setEnabled(surface.windows_available)
    autostart.toggled.connect(surface.change_autostart)
    surface.autostart_toggle = autostart
    settings.content.addWidget(autostart)
    shortcut = Button("Создать ярлык на рабочем столе")
    shortcut.setEnabled(surface.windows_available)
    shortcut.clicked.connect(surface.create_shortcut)
    settings.content.addWidget(shortcut)
    row.addWidget(settings, 1)
    layout.addLayout(row)
    files = GlassCard(surface)
    files.content.addWidget(label("Файлы CS2", "heading"))
    surface.cs2_path_label = label(surface.preferences.data["cs2_autoexec"] or "autoexec.cfg не выбран")
    surface.cs2_exe_label = label(surface.preferences.data["cs2_executable"] or "cs2.exe не выбран")
    files.content.addWidget(surface.cs2_path_label)
    files.content.addWidget(surface.cs2_exe_label)
    file_actions = QHBoxLayout()
    choose_cfg = Button("Выбрать autoexec.cfg")
    choose_cfg.clicked.connect(surface.choose_autoexec)
    choose_exe = Button("Выбрать cs2.exe")
    choose_exe.clicked.connect(surface.choose_cs2_exe)
    detect = Button("Найти Steam и CS2")
    detect.setEnabled(surface.windows_available)
    detect.setToolTip("Прочитать библиотеки Steam и подставить найденные autoexec.cfg и cs2.exe. Файлы не изменяются.")
    detect.clicked.connect(surface.detect_cs2)
    file_actions.addWidget(choose_cfg)
    file_actions.addWidget(choose_exe)
    file_actions.addWidget(detect)
    files.content.addLayout(file_actions)
    surface.cs2_detect_status = label(
        "Поиск использует только локальные папки Steam и ничего не отправляет в сеть.", "small")
    files.content.addWidget(surface.cs2_detect_status)
    layout.addWidget(files)
    devices = GlassCard(surface)
    devices.content.addWidget(label("Устройства · максимум 2", "heading"))
    surface.device_list = QVBoxLayout()
    devices.content.addLayout(surface.device_list)
    surface.refresh_profile_button = Button("Обновить профиль")
    surface.refresh_profile_button.clicked.connect(lambda: surface.profile_controller.refresh())
    devices.content.addWidget(surface.refresh_profile_button)
    layout.addWidget(devices)
    card = GlassCard(surface)
    card.content.addWidget(label("Прозрачность и контроль", "heading"))
    surface.version_status = label(f"Shatli FLUX {surface.config.values['version']} · каталог {surface.config.values['catalog_version']}")
    card.content.addWidget(surface.version_status)
    actions = QHBoxLayout()
    security = Button("Безопасность", icon_path=surface.config.asset("icons") / "shield.svg")
    security.clicked.connect(open_security)
    actions.addWidget(security)
    update = Button("Проверить обновления")
    update.clicked.connect(surface.check_updates)
    actions.addWidget(update)
    actions.addStretch()
    card.content.addLayout(actions)
    layout.addWidget(card)
    layout.addStretch()
    return page


class DetailsDialog(QDialog):
    def __init__(self, title, text, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(700, 540)
        self.setStyleSheet(parent.styleSheet())
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)
        root.addWidget(label(title, "title"))
        report = QPlainTextEdit()
        report.setReadOnly(True)
        report.setPlainText(text or "Данные ещё не загружены.")
        root.addWidget(report, 1)
        close = Button("Закрыть", "primary")
        close.clicked.connect(self.accept)
        root.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)


class SecurityDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Shatli FLUX · Безопасность")
        self.setMinimumSize(560, 420)
        self.resize(680, 560)
        self.setStyleSheet(parent.styleSheet())
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)
        root.addWidget(label("Безопасность", "title"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body, layout = page_layout()
        layout.addWidget(label("ПРОЗРАЧНОСТЬ И КОНТРОЛЬ", "accent"))
        layout.addWidget(label(executable_security(config), "body"))
        layout.addWidget(label("Разрешённые сетевые адреса", "heading"))
        origins = config.network["allowed_origins"]
        layout.addWidget(label("\n".join(origins) if origins else "Список пуст. Программа не обращается к сети."))
        layout.addWidget(label("Журнал изменений", "heading"))
        layout.addWidget(label("Исходные значения сохраняются до применения. Откат не зависит от входа, сети или подписки."))
        journal = QPlainTextEdit()
        journal.setReadOnly(True)
        journal.setMinimumHeight(200)
        journal.setPlainText(parent.journal_error or json.dumps(parent.journal_cache, ensure_ascii=False, indent=2))
        layout.addWidget(journal)
        layout.addStretch()
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        actions = QHBoxLayout()
        revert = Button("Откатить всё")
        revert.setEnabled(parent.windows_available and not parent.operation_busy)
        revert.clicked.connect(lambda: (self.accept(), parent.request_revert_all()))
        actions.addWidget(revert)
        restore = Button("Создать точку восстановления")
        restore.setEnabled(parent.windows_available and not parent.operation_busy)
        restore.clicked.connect(lambda: (self.accept(), parent.request_restore()))
        actions.addWidget(restore)
        root.addLayout(actions)
        close = Button("Закрыть")
        close.clicked.connect(self.accept)
        root.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)


class TweakInfoDialog(QDialog):
    def __init__(self, tweak, details, parent):
        super().__init__(parent)
        self.setWindowTitle(tweak.title)
        self.resize(720, 560)
        self.setStyleSheet(parent.styleSheet())
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)
        root.addWidget(label(tweak.title, "title"))
        root.addWidget(label(tweak.effect, "body"))
        root.addWidget(label(tweak.risk))
        text = QPlainTextEdit()
        text.setReadOnly(True)
        record = details["journal"]
        lines = [tweak.description, "", "СТАРОЕ / ТЕКУЩЕЕ / НОВОЕ ЗНАЧЕНИЕ", ""]
        for index, op in enumerate(tweak.operations):
            old = record["changes"][index]["old"] if record else details["current"][index]
            desired = record["changes"][index]["new"] if record else op.new
            lines.extend([op.target, "Исходное из backup.json:" if record else "До применения:",
                          json.dumps(old, ensure_ascii=False),
                          "Сейчас: " + json.dumps(details["current"][index], ensure_ascii=False),
                          "Новое: " + json.dumps(desired, ensure_ascii=False), ""])
        lines += ["Типы: 1 = REG_SZ, 4 = REG_DWORD. exists=false: значение отсутствует.",
                  "HKLM требует явного подтверждения UAC. Для autoexec сохраняется исходный файл.",
                  "", "Описание параметров: " + tweak.source]
        text.setPlainText("\n".join(lines))
        root.addWidget(text, 1)
        close = Button("Закрыть")
        close.clicked.connect(self.accept)
        root.addWidget(close)
