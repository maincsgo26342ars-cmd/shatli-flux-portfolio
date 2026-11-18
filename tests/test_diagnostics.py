import io
import json
from threading import Event

import pytest

from app.config import Config
from app.core import benchmark
from app.core.benchmark import BenchmarkCancelled, BenchmarkStore, format_benchmarks, run_benchmark
from app.core.diagnostics import GIB, build_diagnostic_report, recommend_preset
from app.core.windows import UserError


def measurement(cpu=25.0, memory=50.0, processes=120.0):
    return {"samples": 10, "duration_seconds": 10,
            "cpu_average_percent": cpu, "cpu_p95_percent": min(100, cpu + 20),
            "memory_used_percent": memory, "process_count_average": processes,
            "top_processes": [{"name": "cs2", "memory_mb": 4096.0}],
            "recorded_at": "2026-10-31T12:00:00+00:00"}


class FakeProcess:
    def __init__(self, output, running=False):
        self.stdout = io.StringIO(output)
        self.returncode = None if running else 0
        self.terminated = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -1

    def kill(self):
        self.returncode = -9


def fake_popen(monkeypatch, output, running=False):
    process = FakeProcess(output, running)
    calls = []
    monkeypatch.setattr(benchmark.subprocess, "Popen",
                        lambda *args, **kwargs: calls.append((args, kwargs)) or process)
    return process, calls


def test_laptop_recommendation_never_forces_high_performance():
    key, reason = recommend_preset({"battery_present": True, "ram_bytes": 32 * GIB,
                                    "cpu_logical": 24})
    assert key == "laptop"
    assert "план питания" in reason


def test_weak_pc_gets_conservative_preset():
    key, _ = recommend_preset({"battery_present": False, "ram_bytes": 8 * GIB,
                               "cpu_logical": 4})
    assert key == "weak"


def test_average_desktop_gets_balanced_preset():
    key, _ = recommend_preset({"battery_present": False, "ram_bytes": 16 * GIB,
                               "cpu_logical": 8})
    assert key == "balanced"


def test_report_distinguishes_unknown_hags_from_problem():
    report = build_diagnostic_report({
        "cpu": "TEST CPU", "gpu": "TEST GPU", "ram": "16.0 ГБ", "ram_bytes": 16 * GIB,
        "windows": "Windows 11", "cpu_logical": 8, "battery_present": False,
        "game_mode": 1, "hags": None, "power_plan": "Сбалансированная",
        "startup_count": 4, "system_drive_free_bytes": 100 * GIB,
    })
    hags = next(item for item in report["findings"] if item["id"] == "hags")
    assert hags["status"] == "info"
    assert report["recommendation_count"] == 0
    assert "не оценка здоровья ПК" in report["text"]


def test_report_counts_real_recommendations():
    report = build_diagnostic_report({
        "ram": "8.0 ГБ", "ram_bytes": 8 * GIB, "cpu_logical": 4,
        "battery_present": False, "game_mode": 0, "power_plan": "Экономия энергии",
        "startup_count": 15, "system_drive_free_bytes": 10 * GIB,
    })
    assert report["recommendation_count"] == 5
    assert report["recommended_preset"] == "weak"
    assert "Найдено рекомендаций: 5" in report["text"]


def test_benchmark_store_roundtrip_and_local_comparison(tmp_path):
    store = BenchmarkStore(tmp_path / "benchmark.json")
    store.record("before", measurement())
    data = store.record("after", measurement(cpu=20, processes=110))
    loaded = BenchmarkStore(tmp_path / "benchmark.json").load()
    assert loaded["before"]["cpu_average_percent"] == 25
    assert loaded["after"]["process_count_average"] == 110
    assert len(loaded["history"]) == 2
    text = format_benchmarks(data)
    assert "Средняя загрузка CPU: -5.0 п.п." in text
    assert "Среднее число процессов: -10.0" in text
    assert "не FPS, время кадра или 1 % low" in text
    assert "cs2: 4096.0 МБ" in text


def test_new_before_clears_stale_after_and_preserves_history(tmp_path):
    store = BenchmarkStore(tmp_path / "benchmark.json")
    store.record("before", measurement())
    store.record("after", measurement(cpu=20))
    data = store.record("before", measurement(cpu=24))
    assert data["after"] is None
    assert len(data["history"]) == 3
    data = store.new_series()
    assert data["before"] is None and data["after"] is None
    assert len(data["history"]) == 3


def test_benchmark_corrupt_file_fails_closed(tmp_path):
    path = tmp_path / "benchmark.json"
    path.write_text("not json", encoding="utf-8")
    store = BenchmarkStore(path)
    assert store.load() == {"schema": 2, "before": None, "after": None, "history": []}
    path.write_text(json.dumps({"schema": 2, "before": measurement(200), "after": None}), encoding="utf-8")
    assert store.load()["before"] is None


def test_benchmark_streams_progress_and_validates_output(monkeypatch):
    monkeypatch.setattr(benchmark, "executable", lambda *_: "powershell.exe")
    output = "PROGRESS 4 10\nRESULT " + json.dumps(measurement()) + "\n"
    _, calls = fake_popen(monkeypatch, output)
    progress = []
    result = run_benchmark(Config.load(), lambda amount, text: progress.append((amount, text)))
    assert result["cpu_average_percent"] == 25
    assert "осталось 6 сек" in progress[1][1]
    assert "-NonInteractive" in calls[0][0][0]
    assert "-Seconds 10" in calls[0][0][0][-1]
    assert calls[0][1]["shell"] is False


def test_benchmark_cancellation_terminates_without_result(monkeypatch):
    monkeypatch.setattr(benchmark, "executable", lambda *_: "powershell.exe")
    process, _ = fake_popen(monkeypatch, "PROGRESS 1 10\n", running=True)
    cancelled = Event()
    cancelled.set()
    with pytest.raises(BenchmarkCancelled, match="не сохранён"):
        run_benchmark(Config.load(), cancel_event=cancelled)
    assert process.terminated


def test_benchmark_rejects_invalid_output(monkeypatch):
    monkeypatch.setattr(benchmark, "executable", lambda *_: "powershell.exe")
    fake_popen(monkeypatch, "RESULT not json\n")
    with pytest.raises(UserError, match="Ничего не сохранено"):
        run_benchmark(Config.load())
