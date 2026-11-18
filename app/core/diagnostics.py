"""Read-only PC diagnostics and conservative preset recommendations."""
from app.core.catalog import PRESETS

GIB = 1024 ** 3


def _finding(identifier, title, status, detail, advice=""):
    return {"id": identifier, "title": title, "status": status,
            "detail": detail, "advice": advice}


def recommend_preset(info):
    """Recommend only; never apply a tweak or change a power plan here."""
    ram = info.get("ram_bytes") or 0
    logical = info.get("cpu_logical") or 0
    if info.get("battery_present") is True:
        return "laptop", "Обнаружена батарея: набор для ноутбука не меняет план питания и не включает эксперименты."
    if 0 < ram < 12 * GIB or 0 < logical <= 4:
        return "weak", "Ограниченные ресурсы: рекомендован набор без HKLM, экспериментальных настроек и смены питания."
    gpu = str(info.get("gpu") or "").casefold()
    integrated = any(name in gpu for name in (
        "intel", "uhd graphics", "iris", "radeon graphics", "vega", "integrated"))
    if integrated and (0 < ram < 16 * GIB or 0 < logical <= 8):
        return "weak", ("Встроенная графика и скромный запас ресурсов: рекомендован набор без смены плана питания "
                        "и без экспериментальных настроек.")
    return "balanced", "Для среднего ПК рекомендован базовый сбалансированный набор без экспериментальных настроек."


def build_diagnostic_report(info):
    findings = []
    game_mode = info.get("game_mode")
    if game_mode == 0:
        findings.append(_finding(
            "game_mode", "Игровой режим", "warning", "Выключен",
            "Можно рассмотреть штатную настройку «Включить игровой режим»."))
    elif game_mode == 1:
        findings.append(_finding("game_mode", "Игровой режим", "ok", "Включён"))
    else:
        findings.append(_finding(
            "game_mode", "Игровой режим", "info", "Значение не задано или не прочитано",
            "Windows может использовать стандартное поведение; это не подтверждённая проблема."))

    hags = info.get("hags")
    hags_text = {2: "Включён", 1: "Выключен"}.get(hags, "Не задан или поддержка не подтверждена")
    findings.append(_finding(
        "hags", "HAGS", "info", hags_text,
        "Экспериментальная настройка: автоматически включать её не рекомендуется."))

    plan = str(info.get("power_plan") or "Не определено")
    lowered = plan.casefold()
    if "saver" in lowered or "эконом" in lowered:
        findings.append(_finding(
            "power", "План питания", "warning", plan,
            "Для стационарного ПК можно проверить сбалансированный план; на ноутбуке учитывайте нагрев и батарею."))
    else:
        findings.append(_finding("power", "План питания", "ok" if plan != "Не определено" else "info", plan))

    startup = info.get("startup_count")
    if startup is None:
        findings.append(_finding("startup", "Автозагрузка", "info", "Количество не определено"))
    elif startup >= 12:
        findings.append(_finding(
            "startup", "Автозагрузка", "warning", f"Записей в Run: {startup}",
            "Проверьте лишние программы в диспетчере задач. Shatli FLUX ничего не отключает автоматически."))
    else:
        findings.append(_finding("startup", "Автозагрузка", "ok", f"Записей в Run: {startup}"))

    ram = info.get("ram_bytes")
    if ram and ram < 12 * GIB:
        findings.append(_finding(
            "memory", "Оперативная память", "warning", f"Установлено: {ram / GIB:.1f} ГБ",
            "Закройте лишние фоновые программы перед игрой; прирост FPS не гарантируется."))
    else:
        findings.append(_finding(
            "memory", "Оперативная память", "ok" if ram else "info",
            f"Установлено: {ram / GIB:.1f} ГБ" if ram else "Не определено"))

    free = info.get("system_drive_free_bytes")
    if free is not None and free < 20 * GIB:
        findings.append(_finding(
            "disk", "Системный диск", "warning", f"Свободно: {free / GIB:.1f} ГБ",
            "Оставьте свободное место для обновлений и временных файлов."))
    else:
        findings.append(_finding(
            "disk", "Системный диск", "ok" if free is not None else "info",
            f"Свободно: {free / GIB:.1f} ГБ" if free is not None else "Не определено"))

    preset, reason = recommend_preset(info)
    warnings = sum(item["status"] == "warning" for item in findings)
    hardware = [
        "CPU: " + str(info.get("cpu") or "Не определено"),
        "GPU: " + str(info.get("gpu") or "Не определено"),
        "ОЗУ: " + str(info.get("ram") or "Не определено"),
        "Windows: " + str(info.get("windows") or "Не определено"),
        "Тип: " + ("Ноутбук (обнаружена батарея)" if info.get("battery_present") is True
                    else "Стационарный ПК / батарея не обнаружена" if info.get("battery_present") is False
                    else "Не определён"),
    ]
    status_names = {"ok": "ОК", "warning": "Проверить", "info": "Сведения"}
    lines = hardware + ["", "СОСТОЯНИЕ"]
    for item in findings:
        lines.append(f"[{status_names[item['status']]}] {item['title']}: {item['detail']}")
        if item["advice"]:
            lines.append("  " + item["advice"])
    lines += ["", f"Найдено рекомендаций: {warnings}",
              f"Рекомендованный пресет: {PRESETS[preset][0]}", reason,
              "", "Это диагностика отдельных параметров, а не оценка здоровья ПК или обещание прироста FPS."]
    return {"findings": findings, "recommendation_count": warnings,
            "recommended_preset": preset, "recommendation_reason": reason,
            "recommended_title": PRESETS[preset][0], "text": "\n".join(lines)}
