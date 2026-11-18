"""Cancellable passive Windows measurements with local series and history."""
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import threading
import time

from app.core.windows import UserError, executable

BENCHMARK_SECONDS = 10
METRICS = (
    ("Средняя загрузка CPU", "cpu_average_percent", "%"),
    ("Пиковая загрузка CPU · 95-й перцентиль", "cpu_p95_percent", "%"),
    ("Использование ОЗУ", "memory_used_percent", "%"),
    ("Среднее число процессов", "process_count_average", ""),
)


class BenchmarkCancelled(UserError):
    pass


def _number(data, key, minimum, maximum):
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(key)
    value = float(value)
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(key)
    return value


def _validate(data):
    if not isinstance(data, dict):
        raise ValueError("benchmark")
    result = {key: _number(data, key, 0, 100) for key in (
        "cpu_average_percent", "cpu_p95_percent", "memory_used_percent")}
    result["process_count_average"] = _number(data, "process_count_average", 1, 100000)
    result["samples"] = int(_number(data, "samples", 2, 60))
    result["duration_seconds"] = int(_number(data, "duration_seconds", 2, 60))
    top = data.get("top_processes", [])
    result["top_processes"] = [
        {"name": str(item.get("name") or "Не определено")[:120],
         "memory_mb": _number(item, "memory_mb", 0, 10000000)}
        for item in top[:5] if isinstance(item, dict)
    ] if isinstance(top, list) else []
    result["recorded_at"] = str(data.get("recorded_at") or
                                datetime.now(timezone.utc).isoformat())[:64]
    return result


def _stop_process(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def run_benchmark(config, progress=None, duration=BENCHMARK_SECONDS, cancel_event=None):
    """Stream real samples; cancellation never saves a partial measurement."""
    if progress:
        progress(5, f"Подготовка замера Windows на {duration} секунд…")
    script = config.asset("benchmark_script").read_text(encoding="utf-8-sig")
    arguments = [
        executable(config, "powershell"), "-NoProfile", "-NonInteractive",
        "-Command", f"& {{ {script} }} -Seconds {duration}",
    ]
    process = subprocess.Popen(
        arguments, shell=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    lines = queue.Queue()

    def read_output():
        for line in process.stdout:
            lines.put(line)
        lines.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    payload = None
    deadline = time.monotonic() + duration + 45
    try:
        while True:
            if cancel_event and cancel_event.is_set():
                raise BenchmarkCancelled("Замер отменён. Неполный результат не сохранён.")
            try:
                line = lines.get(timeout=0.2)
            except queue.Empty:
                if time.monotonic() > deadline:
                    raise UserError("Windows не завершила замер вовремя. Результат не сохранён.")
                continue
            if line is None:
                break
            line = line.strip().lstrip("\ufeff")
            if line.startswith("PROGRESS "):
                try:
                    _, completed, total = line.split()
                    completed, total = int(completed), int(total)
                    remaining = max(0, total - completed)
                    if progress:
                        progress(min(93, 5 + round(88 * completed / total)),
                                 f"Идёт замер: осталось {remaining} сек. Не изменяйте настройки.")
                except (ValueError, ZeroDivisionError):
                    continue
            elif line.startswith("RESULT "):
                payload = line[7:]
        returncode = process.wait(timeout=2)
        if returncode:
            raise UserError("Не удалось выполнить замер Windows. Закройте лишние программы и повторите.")
        if not payload:
            raise UserError("Windows вернула неполные результаты замера. Ничего не сохранено.")
        try:
            measured = _validate(json.loads(payload))
        except (ValueError, TypeError, KeyError) as exc:
            raise UserError("Windows вернула неполные результаты замера. Ничего не сохранено.") from exc
        if cancel_event and cancel_event.is_set():
            raise BenchmarkCancelled("Замер отменён. Неполный результат не сохранён.")
        if progress:
            progress(97, "Замер завершён. Сохраняю результат.")
        return measured
    finally:
        _stop_process(process)
        reader.join(timeout=2)


def _empty():
    return {"schema": 2, "before": None, "after": None, "history": []}


class BenchmarkStore:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("schema") not in (1, 2):
                return _empty()
            result = _empty()
            for slot in ("before", "after"):
                result[slot] = _validate(data[slot]) if data.get(slot) else None
            history = data.get("history", [])
            if isinstance(history, list):
                for item in history[-20:]:
                    if isinstance(item, dict) and item.get("slot") in ("before", "after"):
                        result["history"].append({"slot": item["slot"],
                                                  "result": _validate(item["result"])})
            return result
        except (OSError, ValueError, TypeError, KeyError):
            return _empty()

    def _save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def record(self, slot, result):
        if slot not in ("before", "after"):
            raise ValueError("unknown benchmark slot")
        data = self.load()
        measured = _validate(result)
        if slot == "before":
            data["after"] = None
        data[slot] = measured
        data["history"].append({"slot": slot, "result": measured})
        data["history"] = data["history"][-20:]
        self._save(data)
        return data

    def new_series(self):
        data = self.load()
        data["before"] = None
        data["after"] = None
        self._save(data)
        return data


def _time(value):
    try:
        return datetime.fromisoformat(value["recorded_at"]).astimezone().strftime("%d.%m.%Y %H:%M")
    except (ValueError, TypeError, OSError):
        return "Время не определено"


def format_benchmarks(data):
    lines = []
    for slot, title in (("before", "ДО"), ("after", "ПОСЛЕ")):
        result = data.get(slot)
        lines.append(f"{title}: {_time(result)}" if result else f"{title}: замер не выполнен")
        if result:
            for label, key, unit in METRICS:
                lines.append(f"  {label}: {result[key]:.1f}{unit}")
    before, after = data.get("before"), data.get("after")
    if before and after:
        lines += ["", "СРАВНЕНИЕ · ПОСЛЕ минус ДО"]
        for label, key, unit in METRICS:
            delta = after[key] - before[key]
            suffix = " п.п." if unit == "%" else ""
            lines.append(f"{label}: {delta:+.1f}{suffix}")
        lines += ["", "Отрицательная разница означает снижение нагрузки, положительная — рост.",
                  "Короткий замер зависит от фоновой активности. Для надёжности сравните несколько серий в одинаковых условиях."]
        if after["top_processes"]:
            lines += ["", "Процессы ПОСЛЕ · наибольшая память:"]
            lines += [f"  {item['name']}: {item['memory_mb']:.1f} МБ" for item in after["top_processes"]]
    history = data.get("history", [])
    if history:
        lines += ["", f"ИСТОРИЯ · последние замеры: {len(history)}"]
        for item in history[-6:]:
            result = item["result"]
            lines.append(f"  {'ДО' if item['slot'] == 'before' else 'ПОСЛЕ'} · {_time(result)} · "
                         f"CPU {result['cpu_average_percent']:.1f}% · ОЗУ {result['memory_used_percent']:.1f}%")
    lines += ["", "Это пассивный замер Windows, не FPS, время кадра или 1 % low в CS2."]
    return "\n".join(lines)
