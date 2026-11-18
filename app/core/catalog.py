"""Fixed, auditable operation allowlist. The server cannot supply operations."""
from dataclasses import dataclass

BLOCK_START = "// >>> SHATLI FLUX >>>"
BLOCK_END = "// <<< SHATLI FLUX <<<"


def managed_file(old, commands):
    """Replace only our block; preserve every unrelated line and final newline."""
    source = old.get("value") or ""
    lines = source.splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == BLOCK_START]
    ends = [i for i, line in enumerate(lines) if line.strip() == BLOCK_END]
    if len(starts) > 1 or len(ends) > 1 or bool(starts) != bool(ends):
        from app.core.windows import UserError
        raise UserError("В autoexec.cfg повреждён блок Shatli FLUX. Исправьте его вручную; файл не изменён.")
    if starts and (ends[0] < starts[0] or starts[0] != 0 and any(
            line.strip() in (BLOCK_START, BLOCK_END) for line in lines[:starts[0]])):
        from app.core.windows import UserError
        raise UserError("В autoexec.cfg повреждён блок Shatli FLUX. Файл не изменён.")
    managed = []
    if starts:
        managed = lines[starts[0] + 1:ends[0]]
        lines = lines[:starts[0]] + lines[ends[0] + 1:]
    updated = commands.splitlines()
    keys = {line.split(maxsplit=1)[0].lower() for line in updated if line.strip()}
    managed = [line for line in managed
               if not line.strip() or line.split(maxsplit=1)[0].lower() not in keys]
    block = [BLOCK_START, *managed, *updated, BLOCK_END]
    value = "\r\n".join(lines + block) + ("\r\n" if source.endswith(("\n", "\r")) or not source else "")
    return {"exists": True, "type": "managed_file", "value": value}


def desired_snapshot(operation, old):
    if operation.kind == "file":
        return managed_file(old, operation.value)
    return operation.new


@dataclass(frozen=True)
class Operation:
    id: str
    kind: str
    path: str
    name: str
    value_type: int | str
    value: str | int
    hive: str = "HKCU"
    create_key: bool = False
    min_build: int = 10240

    @property
    def target(self):
        if self.kind == "power":
            return "powercfg /setactive"
        if self.kind == "file":
            return "CS2 autoexec.cfg · управляемый блок"
        return f"{self.hive}\\{self.path}\\{self.name}"

    @property
    def new(self):
        return {"exists": True, "type": self.value_type, "value": self.value}

    @property
    def requires_admin(self):
        return self.hive == "HKLM"


@dataclass(frozen=True)
class Tweak:
    id: str
    title: str
    group: str
    description: str
    effect: str
    risk: str
    source: str
    operations: tuple[Operation, ...]
    experimental: bool = False
    excludes: tuple[str, ...] = ()


def reg(identifier, path, name, value=0, *, hive="HKCU", kind=4, create=True, build=10240):
    if kind == 1 and value == 0:
        value = "0"
    return Operation(identifier, "registry", path, name, kind, value, hive, create, build)


DOCS = "https://learn.microsoft.com/"
CATALOG = (
    Tweak("game_capture_off", "Отключить запись Game DVR", "Игра",
          "Отключает запись клипов Windows для текущего пользователя. Game Bar и игровые службы не удаляются.",
          "Может убрать фоновую нагрузку, если запись использовалась. Прирост FPS не гарантируется.",
          "Перезапуск · запись клипов будет недоступна", DOCS + "windows-apps/gaming/",
          (reg("capture_app", r"Software\Microsoft\Windows\CurrentVersion\GameDVR", "AppCaptureEnabled"),
           reg("capture_store", r"System\GameConfigStore", "GameDVR_Enabled"))),
    Tweak("game_mode_on", "Включить игровой режим", "Игра",
          "Включает штатный игровой режим Windows для текущего пользователя.",
          "Windows может ограничивать фоновую активность во время игры; результат зависит от системы.",
          "Перезапуск игры", DOCS + "windows/apps/gaming/",
          (reg("game_mode", r"Software\Microsoft\GameBar", "AutoGameModeEnabled", 1),)),
    Tweak("cs2_fullscreen_off", "Полноэкранная оптимизация cs2.exe", "Игра",
          "Добавляет для cs2.exe флаг отключения полноэкранной оптимизации Windows. Путь к исполняемому файлу не определяется автоматически.",
          "Может изменить поведение режима отображения; проверьте вариант на своём ПК.",
          "Перезапуск CS2 · может измениться переключение между окнами", DOCS + "windows/win32/dwm/",
          (reg("cs2_fso", r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers",
               "cs2.exe", "~ DISABLEDXMAXIMIZEDWINDOWEDMODE", kind=1),)),
    Tweak("cs2_high_performance_gpu", "Высокопроизводительный GPU для CS2", "Игра",
          "Назначает выбранному cs2.exe высокопроизводительный GPU.",
          "На системе с несколькими GPU Windows сможет выбрать более производительный адаптер.",
          "Перезапуск CS2 · увеличение энергопотребления", DOCS + "windows/win32/direct3d12/",
          (reg("cs2_gpu", r"Software\Microsoft\DirectX\UserGpuPreferences", "cs2.exe", "GpuPreference=2;", kind=1),)),
    Tweak("cs2_fps_400", "CS2 · лимит 400 FPS", "Игра",
          "Добавляет fps_max 400 в управляемый блок выбранного autoexec.cfg. Другие команды сохраняются.",
          "Ограничивает верхний предел частоты кадров; не повышает FPS.",
          "Перезапуск CS2 · параметр можно изменить консолью игры", DOCS,
          (Operation("cs2_fps_400_file", "file", "", "autoexec.cfg", "managed_file", "fps_max 400"),),
          excludes=("cs2_fps_unlimited",)),
    Tweak("cs2_fps_unlimited", "CS2 · снять лимит FPS", "Игра",
          "Добавляет fps_max 0 в управляемый блок autoexec.cfg. Взаимоисключающие пресеты не объединяются автоматически.",
          "Убирает ограничение fps_max; возможен рост нагрузки и температуры.",
          "Перезапуск CS2 · возможны нагрев и шум", DOCS,
          (Operation("cs2_fps_unlimited_file", "file", "", "autoexec.cfg", "managed_file", "fps_max 0"),),
          excludes=("cs2_fps_400",)),
    Tweak("cs2_fps_counter", "CS2 · счётчик FPS", "Игра",
          "Добавляет cl_showfps 1 в управляемый блок autoexec.cfg.",
          "Показывает счётчик частоты кадров для самостоятельных замеров.",
          "Перезапуск CS2 · на экране появится счётчик", DOCS,
          (Operation("cs2_fps_counter_file", "file", "", "autoexec.cfg", "managed_file", "cl_showfps 1"),)),
    Tweak("transparency_off", "Отключить прозрачность Windows", "Система",
          "Меняет прозрачность интерфейса Windows, не оформление Shatli FLUX.",
          "Упрощает отрисовку интерфейса. Прирост производительности не гарантируется.",
          "Перезапуск или повторный вход", DOCS + "windows/apps/desktop/",
          (reg("transparency", r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "EnableTransparency"),)),
    Tweak("visual_effects_performance", "Эффекты Windows · производительность", "Система",
          "Отключает часть визуальных эффектов рабочего стола для текущего пользователя.",
          "Может уменьшить оформление Windows. Не меняет графику внутри CS2.",
          "Перезапуск · изменится внешний вид Windows", DOCS + "windows/win32/winshake/",
          (reg("visual_effects", r"Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects", "VisualFXSetting", 2),
           reg("min_animate", r"Control Panel\Desktop\WindowMetrics", "MinAnimate", kind=1),)),
    Tweak("background_apps_off", "Отключить фоновые приложения", "Система",
          "Запрещает приложениям Microsoft Store работать в фоне для текущего пользователя.",
          "Может уменьшить фоновую активность; уведомления некоторых приложений перестанут приходить.",
          "Перезапуск · ограничение фоновых уведомлений", DOCS + "windows/apps/launch-resume/",
          (reg("background_apps", r"Software\Microsoft\Windows\CurrentVersion\BackgroundAccessApplications", "GlobalUserDisabled", 1),
           reg("background_toggle", r"Software\Microsoft\Windows\CurrentVersion\Search", "BackgroundAppGlobalToggle"),)),
    Tweak("startup_delay_off", "Убрать задержку автозагрузки", "Система",
          "Убирает искусственную задержку запуска пользовательских программ после входа в Windows.",
          "Программы автозагрузки могут начать запускаться раньше; это не ускоряет CS2.",
          "Повторный вход · возможна нагрузка сразу после входа", DOCS + "windows/win32/shell/",
          (reg("startup_delay", r"Software\Microsoft\Windows\CurrentVersion\Explorer\Serialize", "StartupDelayInMSec"),)),
    Tweak("fast_startup_off", "Отключить быстрый запуск Windows", "Система",
          "Отключает гибридное завершение работы Windows. Не отключает спящий режим.",
          "Полное выключение станет полным холодным завершением; загрузка может занимать больше времени.",
          "Права администратора · более медленное включение", DOCS + "windows-hardware/design/device-experiences/",
          (reg("fast_startup", r"SYSTEM\CurrentControlSet\Control\Session Manager\Power", "HiberbootEnabled", hive="HKLM"),)),
    Tweak("timer_requests_win11", "Запросы точного таймера · Windows 11", "Система",
          "Разрешает системные запросы повышенного разрешения таймера. Это НЕ фиксированные 0,5 мс и не постоянное удержание таймера программой.",
          "Поведение зависит от Windows и приложений; измеримый эффект не обещается.",
          "Эксперимент · Windows 11 · права администратора · перезагрузка", DOCS + "windows/win32/multimedia/",
          (reg("timer_requests", r"SYSTEM\CurrentControlSet\Control\Session Manager\kernel", "GlobalTimerResolutionRequests", 1, hive="HKLM", build=22000),),
          experimental=True),
    Tweak("hags_on", "Планирование GPU с аппаратным ускорением", "Система",
          "Включает HAGS, если Windows и драйвер GPU поддерживают эту функцию.",
          "Может изменить задержку и стабильность кадров в обе стороны. Проводите собственные замеры.",
          "Эксперимент · права администратора · перезагрузка", DOCS + "windows-hardware/drivers/display/",
          (reg("hags", r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers", "HwSchMode", 2, hive="HKLM", build=19041),),
          experimental=True),
    Tweak("mmcss_games", "MMCSS · приоритет профиля Games", "Система",
          "Меняет GPU Priority, Priority и SFIO Priority только встроенного профиля Games. Scheduling Category специально не повышается.",
          "Экспериментальные параметры планировщика могут улучшить или ухудшить работу системы.",
          "Эксперимент · права администратора · перезагрузка", DOCS + "windows/win32/procthread/",
          (reg("mmcss_gpu", r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile\Tasks\Games", "GPU Priority", 8, hive="HKLM"),
           reg("mmcss_priority", r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile\Tasks\Games", "Priority", 6, hive="HKLM"),
           reg("mmcss_sfio", r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile\Tasks\Games", "SFIO Priority", "High", hive="HKLM", kind=1)),
          experimental=True),
    Tweak("mouse_acceleration_off", "Отключить ускорение мыши", "Ввод",
          "Меняет параметры указателя Windows, не чувствительность мыши в игре и не автоматизирует ввод.",
          "Движение указателя Windows становится более предсказуемым; эффект на raw input в игре не обещается.",
          "Перезапуск · изменится привычное движение указателя", DOCS + "windows/win32/api/winuser/",
          tuple(reg("mouse_" + name.lower(), r"Control Panel\Mouse", name, kind=1)
                for name in ("MouseSpeed", "MouseThreshold1", "MouseThreshold2"))),
    Tweak("sticky_keys_off", "Отключить залипание клавиш", "Ввод",
          "Выключает функцию залипания клавиш и её активацию пятикратным нажатием Shift. Другие функции доступности не меняются.",
          "Случайное нажатие Shift не будет открывать предложение включить залипание.",
          "Повторный вход · изменится поведение клавиши Shift", DOCS + "windows/win32/winauto/",
          (reg("sticky_flags", r"Control Panel\Accessibility\StickyKeys", "Flags", "2", kind=1),)),
    Tweak("high_performance", "Высокая производительность", "Питание",
          "Выбирает существующий стандартный план питания. Отсутствующие схемы не создаются.",
          "Может уменьшить энергосбережение. Возможны больший нагрев, шум и расход батареи.",
          "Повышенное энергопотребление · права зависят от политики ПК",
          DOCS + "windows-hardware/design/device-experiences/powercfg-command-line-options",
          (Operation("power_scheme", "power", "", "active_scheme", "power_guid", "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"),)),
    Tweak("advertising_id_off", "Отключить рекламный ID", "Приватность",
          "Отключает рекламный идентификатор приложений для текущего пользователя.",
          "Настройка приватности, не средство увеличения FPS.",
          "Повторный вход", DOCS + "windows/privacy/",
          (reg("advertising_id", r"Software\Microsoft\Windows\CurrentVersion\AdvertisingInfo", "Enabled"),)),
    Tweak("windows_tips_off", "Отключить советы Windows", "Приватность",
          "Отключает подсказки и рекомендации в интерфейсе Windows для текущего пользователя.",
          "Меньше рекомендаций и уведомлений; производительность игры не изменяется напрямую.",
          "Повторный вход", DOCS + "windows/client-management/",
          (reg("windows_tips", r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager", "SubscribedContent-338389Enabled"),)),
    Tweak("telemetry_minimum", "Минимальная диагностика Windows", "Приватность",
          "Устанавливает политику AllowTelemetry=0. На редакциях без поддержки этого уровня Windows применит доступный минимум.",
          "Ограничивает диагностические данные в пределах редакции Windows; не обещает полного отключения телеметрии.",
          "Права администратора · перезагрузка · ограничение зависит от редакции", DOCS + "windows/privacy/configure-windows-diagnostic-data-in-your-organization",
          (reg("telemetry", r"SOFTWARE\Policies\Microsoft\Windows\DataCollection", "AllowTelemetry", hive="HKLM"),)),
    Tweak("diagtrack_disabled", "Отключить автозапуск DiagTrack", "Приватность",
          "Устанавливает тип запуска службы диагностического отслеживания «Отключено». Уже запущенная служба остановится после перезапуска.",
          "Отключает автозапуск штатной службы диагностики; другие механизмы диагностики не удаляются.",
          "Права администратора · перезагрузка · возможна зависимость диагностических функций", DOCS + "windows/privacy/",
          (reg("diagtrack", r"SYSTEM\CurrentControlSet\Services\DiagTrack", "Start", 4, hive="HKLM", create=False),)),
    Tweak("update_peering_off", "Отключить раздачу обновлений", "Приватность",
          "Запрещает оптимизации доставки получать и отправлять обновления другим ПК. Центр обновления Windows не отключается.",
          "Обновления загружаются только с серверов Microsoft; может снизиться фоновая сетевая активность.",
          "Права администратора · перезагрузка службы обновлений", DOCS + "windows/deployment/do/",
          (reg("delivery_optimization", r"SOFTWARE\Policies\Microsoft\Windows\DeliveryOptimization", "DODownloadMode", 99, hive="HKLM"),)),
)
BY_ID = {tweak.id: tweak for tweak in CATALOG}
PENDING = {}
PRESETS = {
    "free": ("Базовый · FREE", ("game_capture_off", "transparency_off", "mouse_acceleration_off")),
    "weak": ("Слабый ПК · PRO", ("game_capture_off", "game_mode_on", "transparency_off", "visual_effects_performance", "background_apps_off", "mouse_acceleration_off", "windows_tips_off")),
    "balanced": ("Средний ПК · сбалансированный · PRO", ("game_capture_off", "game_mode_on", "transparency_off", "mouse_acceleration_off", "windows_tips_off")),
    "laptop": ("Ноутбук · PRO", ("game_capture_off", "game_mode_on", "transparency_off", "background_apps_off", "mouse_acceleration_off", "windows_tips_off")),
    "performance": ("Производительность · PRO", ("game_capture_off", "game_mode_on", "transparency_off", "visual_effects_performance", "background_apps_off", "mouse_acceleration_off", "high_performance", "windows_tips_off")),
    "privacy": ("Приватность · PRO", ("advertising_id_off", "windows_tips_off", "update_peering_off")),
    "cs2": ("CS2 · PRO", ("game_capture_off", "game_mode_on", "cs2_fps_400", "cs2_fps_counter")),
}

FREE_TWEAK_IDS = PRESETS["free"][1]

# Short, human labels for the preset cards on the home screen.
PRESET_META = {
    "free": ("Базовый", "3 базовых изменения", False),
    "balanced": ("Сбалансированный", "Игра и система", True),
    "performance": ("Производительность", "Больше разгрузки", True),
    "laptop": ("Ноутбук", "Без смены питания", True),
    "weak": ("Слабый ПК", "Максимум разгрузки", True),
    "cs2": ("CS2", "Игра и autoexec", True),
    "privacy": ("Приватность", "Реклама и телеметрия", True),
    "custom": ("Свой набор", "Выбор в разделах", True),
}

PRESET_ORDER = ("free", "balanced", "performance", "laptop", "weak", "cs2", "privacy", "custom")
