"""CS2 frame capture through the official PresentMon ETW console tool."""
from datetime import datetime, timezone
from hashlib import sha256
import csv
import json
import logging
import math
import os
from pathlib import Path
from statistics import fmean, pstdev
import time
from uuid import uuid4

from app.core.benchmark import BenchmarkCancelled
from app.core.windows import UserError, executable, require_windows, run_command, run_elevated

GAME_BENCHMARK_SECONDS = 60
GAME_BENCHMARK_DELAY = 20
PRESENTMON_VERSION = "2.6.0"
PRESENTMON_SHA256 = "b2a706bc6ad475749e3b7e3409263aa1e6906d45bdcf993f6dbc0f660188f1af"
PRESENTMON_SIZE = 980320


def cs2_running(config):
    """Read-only process check; the game is never launched or controlled."""
    result = run_command([
        executable(config, "powershell"), "-NoProfile", "-NonInteractive", "-Command",
        "if (Get-Process -Name cs2 -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }",
    ], timeout=10)
    return result.returncode == 0


def _percentile(sorted_values, fraction):
    index = max(0, math.ceil(len(sorted_values) * fraction) - 1)
    return sorted_values[index]


def _low_fps(frame_times, fraction):
    count = max(1, math.ceil(len(frame_times) * fraction))
    slowest = sorted(frame_times, reverse=True)[:count]
    return 1000.0 / fmean(slowest)


def analyze_presentmon_csv(path, requested_seconds=GAME_BENCHMARK_SECONDS):
    """Calculate presented FPS; 1% low is the reciprocal of the slowest 1% mean."""
    frame_times = []
    process_ids = set()
    try:
        with Path(path).open("r", encoding="utf-8-sig", newline="") as source:
            for row in csv.DictReader(source):
                if (row.get("Application") or "").casefold() != "cs2.exe":
                    continue
                raw = row.get("MsBetweenPresents")
                try:
                    value = float(raw)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(value) or not 0 < value <= 60000:
                    continue
                frame_times.append(value)
                if row.get("ProcessID"):
                    process_ids.add(row["ProcessID"])
    except OSError as exc:
        raise UserError("Не удалось прочитать данные игрового замера. Результат не сохранён.") from exc
    if len(frame_times) < 30:
        raise UserError(
            "PresentMon не получил достаточно кадров CS2. Откройте игровую сцену, "
            "вернитесь в окно CS2 и повторите замер. Ничего не сохранено."
        )
    if len(process_ids) > 1:
        raise UserError("Во время замера CS2 была перезапущена. Повторите его без перезапуска игры.")
    observed_seconds = sum(frame_times) / 1000.0
    if observed_seconds < requested_seconds * 0.70:
        raise UserError(
            f"Записано только {observed_seconds:.1f} сек. кадров из {requested_seconds} сек. "
            "Не сворачивайте CS2 во время замера. Результат не сохранён."
        )
    ordered = sorted(frame_times)
    return {
        "application": "cs2.exe",
        "source": f"PresentMon {PRESENTMON_VERSION}",
        "frames": len(frame_times),
        "duration_seconds": requested_seconds,
        "observed_seconds": observed_seconds,
        "average_fps": 1000.0 / fmean(frame_times),
        "fps_1_low": _low_fps(frame_times, 0.01),
        "fps_0_1_low": _low_fps(frame_times, 0.001),
        "frame_time_average_ms": fmean(frame_times),
        "frame_time_p95_ms": _percentile(ordered, 0.95),
        "frame_time_p99_ms": _percentile(ordered, 0.99),
        "frame_time_max_ms": max(frame_times),
        "frame_time_stddev_ms": pstdev(frame_times),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }


def _quote_ps(value):
    return "'" + str(value).replace("'", "''") + "'"


def _verify_presentmon(path):
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise UserError("Не найден компонент игрового замера PresentMon. Переустановите программу.") from exc
    if len(payload) != PRESENTMON_SIZE or sha256(payload).hexdigest() != PRESENTMON_SHA256:
        raise UserError("Компонент PresentMon повреждён или изменён. Игровой замер отменён.")


def _read_capture_log(path):
    try:
        with Path(path).open("rb") as source:
            payload = source.read(65536)
    except OSError:
        return ""
    encoding = "utf-16" if payload.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    return payload.decode(encoding, errors="replace").strip()[-8000:]


def _capture_failure(code, status_path, stdout_path, stderr_path):
    stdout = _read_capture_log(stdout_path)
    stderr = _read_capture_log(stderr_path)
    try:
        status = json.loads(_read_capture_log(status_path))
        if not isinstance(status, dict):
            status = {}
    except (ValueError, TypeError):
        status = {}
    detail = str(status.get("detail") or "")[:1000]
    logging.getLogger("shatli_flux").warning(
        "PresentMon: code=%s, state=%s, detail=%s, stderr=%s, stdout=%s",
        code, status.get("state", "unknown"), detail or "-", stderr or "-", stdout or "-",
    )
    technical = f"{detail}\n{stderr}".casefold()
    if "access denied" in technical:
        message = ("Windows отклонила доступ к записи кадров ETW. Подтвердите UAC и повторите; "
                   "другие программы записи лучше временно закрыть.")
    elif "already running" in technical or "already exists" in technical:
        message = "Сеанс записи кадров уже занят. Закройте другие программы захвата и повторите."
    else:
        message = "PresentMon завершился с ошибкой. Закройте другие программы захвата и повторите."
    return message + " Подробности сохранены в журнале. Настройки и файлы CS2 не изменены."


def run_game_benchmark(config, progress=None, duration=GAME_BENCHMARK_SECONDS,
                       delay=GAME_BENCHMARK_DELAY, cancel_event=None):
    """Run one elevated, cancellable ETW capture without modifying CS2."""
    require_windows()
    if not 5 <= duration <= 300 or not 0 <= delay <= 120:
        raise ValueError("invalid game benchmark duration")
    presentmon = config.asset("presentmon")
    _verify_presentmon(presentmon)
    if not cs2_running(config):
        raise UserError("Сначала запустите CS2 и оставьте её в меню. Замер не начат.")
    folder = config.user_data() / "captures"
    folder.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    csv_path = folder / f"cs2-{token}.csv"
    status_path = folder / f"cs2-{token}-status.json"
    cancel_path = folder / f"cs2-{token}-cancel"
    stdout_path = folder / f"cs2-{token}-stdout.log"
    stderr_path = folder / f"cs2-{token}-stderr.log"
    script = config.asset("game_capture_script").read_text(encoding="utf-8-sig")
    arguments = [
        "-NoProfile", "-NonInteractive", "-Command",
        "& { " + script + " } " + " ".join(
            f"-{name} {_quote_ps(value)}" for name, value in (
                ("PresentMon", presentmon), ("CsvPath", csv_path),
                ("StatusPath", status_path), ("CancelPath", cancel_path),
                ("StdOutPath", stdout_path), ("StdErrPath", stderr_path),
                ("Seconds", duration), ("Delay", delay),
            )
        ),
    ]
    started = None
    timed_out = False
    deadline = time.monotonic() + delay + duration + 60

    def waiting():
        nonlocal started, timed_out
        if cancel_event and cancel_event.is_set() and not cancel_path.exists():
            cancel_path.write_text("cancel", encoding="ascii")
        if time.monotonic() > deadline and not timed_out:
            timed_out = True
            cancel_path.write_text("timeout", encoding="ascii")
        state = ""
        try:
            state = json.loads(status_path.read_text(encoding="utf-8-sig")).get("state", "")
        except (OSError, ValueError, AttributeError):
            pass
        if state != "running" or not progress:
            if progress:
                progress(3, "Подтвердите запуск PresentMon в окне UAC. CS2 не изменяется.")
            return
        if started is None:
            started = time.monotonic()
        elapsed = time.monotonic() - started
        if elapsed < delay:
            remaining = max(0, math.ceil(delay - elapsed))
            progress(5 + min(10, int(10 * elapsed / max(1, delay))),
                     f"Вернитесь в меню CS2 и запустите карту. Запись через {remaining} сек.")
        else:
            captured = min(duration, elapsed - delay)
            remaining = max(0, math.ceil(duration - captured))
            progress(15 + min(80, int(80 * captured / duration)),
                     f"Идёт игровой замер: осталось {remaining} сек. Не покидайте тестовую сцену.")

    try:
        code = run_elevated(executable(config, "powershell"), arguments, wait_callback=waiting)
        if cancel_event and cancel_event.is_set():
            raise BenchmarkCancelled("Игровой замер отменён. Неполный результат не сохранён.")
        if timed_out:
            raise UserError("Игровой замер не завершился вовремя. Результат не сохранён.")
        if code != 0:
            raise UserError(_capture_failure(code, status_path, stdout_path, stderr_path))
        if progress:
            progress(96, "Захват завершён. Рассчитываю FPS и 1 % low.")
        result = analyze_presentmon_csv(csv_path, duration)
        if cancel_event and cancel_event.is_set():
            raise BenchmarkCancelled("Игровой замер отменён. Неполный результат не сохранён.")
        return result
    finally:
        for path in (csv_path, status_path, cancel_path, stdout_path, stderr_path):
            try:
                path.unlink()
            except OSError:
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
    if not isinstance(data, dict) or data.get("application") != "cs2.exe":
        raise ValueError("game benchmark")
    result = {
        "application": "cs2.exe",
        "source": f"PresentMon {PRESENTMON_VERSION}",
        "frames": int(_number(data, "frames", 30, 100000000)),
        "duration_seconds": int(_number(data, "duration_seconds", 5, 300)),
        "observed_seconds": _number(data, "observed_seconds", 1, 400),
        "average_fps": _number(data, "average_fps", 1, 100000),
        "fps_1_low": _number(data, "fps_1_low", 0.01, 100000),
        "fps_0_1_low": _number(data, "fps_0_1_low", 0.01, 100000),
        "frame_time_average_ms": _number(data, "frame_time_average_ms", 0.001, 60000),
        "frame_time_p95_ms": _number(data, "frame_time_p95_ms", 0.001, 60000),
        "frame_time_p99_ms": _number(data, "frame_time_p99_ms", 0.001, 60000),
        "frame_time_max_ms": _number(data, "frame_time_max_ms", 0.001, 60000),
        "frame_time_stddev_ms": _number(data, "frame_time_stddev_ms", 0, 60000),
        "recorded_at": str(data.get("recorded_at") or "")[:64],
    }
    datetime.fromisoformat(result["recorded_at"])
    return result


def _empty():
    return {"schema": 1, "before": None, "after": None, "history": []}


class GameBenchmarkStore:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("schema") != 1:
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
            raise ValueError("unknown game benchmark slot")
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


def _delta(before, after, key):
    return (after[key] - before[key]) / before[key] * 100.0


def format_game_benchmarks(data):
    lines = []
    for slot, title in (("before", "ДО"), ("after", "ПОСЛЕ")):
        result = data.get(slot)
        lines.append(f"{title}: {_time(result)}" if result else f"{title}: игровой замер не выполнен")
        if result:
            lines += [
                f"  Средний FPS: {result['average_fps']:.1f}",
                f"  1 % low: {result['fps_1_low']:.1f} FPS",
                f"  0,1 % low: {result['fps_0_1_low']:.1f} FPS",
                f"  Время кадра: {result['frame_time_average_ms']:.2f} мс · "
                f"p99 {result['frame_time_p99_ms']:.2f} мс",
                f"  Кадров: {result['frames']} · захват {result['observed_seconds']:.1f} сек.",
            ]
    before, after = data.get("before"), data.get("after")
    if before and after:
        lines += ["", "СРАВНЕНИЕ · изменение ПОСЛЕ относительно ДО",
                  f"Средний FPS: {_delta(before, after, 'average_fps'):+.1f}%",
                  f"1 % low: {_delta(before, after, 'fps_1_low'):+.1f}%",
                  f"0,1 % low: {_delta(before, after, 'fps_0_1_low'):+.1f}%",
                  f"Среднее время кадра: {_delta(before, after, 'frame_time_average_ms'):+.1f}%",
                  "", "Для FPS больше — лучше; для времени кадра меньше — лучше.",
                  "Разные сцены, настройки и случайные колебания могут исказить вывод. Повторите одинаковый тест несколько раз."]
    history = data.get("history", [])
    if history:
        lines += ["", f"ИСТОРИЯ CS2 · последние замеры: {len(history)}"]
        for item in history[-6:]:
            result = item["result"]
            lines.append(f"  {'ДО' if item['slot'] == 'before' else 'ПОСЛЕ'} · {_time(result)} · "
                         f"{result['average_fps']:.1f} FPS · 1 % low {result['fps_1_low']:.1f}")
    lines += ["", f"Источник: PresentMon {PRESENTMON_VERSION}, Windows ETW. Без чтения памяти и внедрения в CS2.",
              "1 % low = 1000 / среднее время самых медленных 1 % представленных кадров."]
    return "\n".join(lines)
