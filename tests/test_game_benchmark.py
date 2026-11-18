import csv
import json
from pathlib import Path
from threading import Event

import pytest

from app.config import Config
from app.core import game_benchmark
from app.core.benchmark import BenchmarkCancelled
from app.core.game_benchmark import (
    GameBenchmarkStore, analyze_presentmon_csv, format_game_benchmarks, run_game_benchmark,
)
from app.core.windows import UserError


def write_csv(path, times, application="cs2.exe", pid="100"):
    with Path(path).open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=["Application", "ProcessID", "MsBetweenPresents"])
        writer.writeheader()
        for value in times:
            writer.writerow({"Application": application, "ProcessID": pid,
                             "MsBetweenPresents": value})


def result(average=300.0, low=150.0):
    return {"application": "cs2.exe", "source": "PresentMon 2.6.0", "frames": 18000,
            "duration_seconds": 60, "observed_seconds": 60.0,
            "average_fps": average, "fps_1_low": low, "fps_0_1_low": low / 2,
            "frame_time_average_ms": 1000 / average, "frame_time_p95_ms": 6.0,
            "frame_time_p99_ms": 8.0, "frame_time_max_ms": 20.0,
            "frame_time_stddev_ms": 1.0, "recorded_at": "2026-11-01T12:00:00+00:00"}


def test_presentmon_metrics_and_explicit_low_definition(tmp_path):
    path = tmp_path / "capture.csv"
    write_csv(path, [10.0] * 99 + [100.0])
    measured = analyze_presentmon_csv(path, requested_seconds=1)
    assert measured["average_fps"] == pytest.approx(100000 / 1090)
    assert measured["fps_1_low"] == pytest.approx(10)
    assert measured["fps_0_1_low"] == pytest.approx(10)
    assert measured["frame_time_p99_ms"] == 10
    assert measured["frame_time_max_ms"] == 100
    assert measured["frames"] == 100
    text = format_game_benchmarks({"before": measured, "after": None, "history": []})
    assert "самых медленных 1 %" in text
    assert "Без чтения памяти" in text


def test_presentmon_ignores_other_processes_and_bad_rows(tmp_path):
    path = tmp_path / "capture.csv"
    write_csv(path, [10.0] * 100)
    with path.open("a", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=["Application", "ProcessID", "MsBetweenPresents"])
        writer.writerow({"Application": "other.exe", "ProcessID": "200", "MsBetweenPresents": 100})
        writer.writerow({"Application": "cs2.exe", "ProcessID": "100", "MsBetweenPresents": "NA"})
    measured = analyze_presentmon_csv(path, requested_seconds=1)
    assert measured["frames"] == 100
    assert measured["average_fps"] == 100


def test_presentmon_rejects_missing_or_too_short_capture(tmp_path):
    path = tmp_path / "capture.csv"
    write_csv(path, [100.0] * 10, application="other.exe")
    with pytest.raises(UserError, match="достаточно кадров"):
        analyze_presentmon_csv(path, requested_seconds=1)
    write_csv(path, [10.0] * 100)
    with pytest.raises(UserError, match="только 1.0 сек"):
        analyze_presentmon_csv(path, requested_seconds=2)


def test_presentmon_rejects_game_restart(tmp_path):
    path = tmp_path / "capture.csv"
    write_csv(path, [10.0] * 50)
    with path.open("a", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=["Application", "ProcessID", "MsBetweenPresents"])
        for _ in range(50):
            writer.writerow({"Application": "cs2.exe", "ProcessID": "200", "MsBetweenPresents": 10})
    with pytest.raises(UserError, match="перезапущена"):
        analyze_presentmon_csv(path, requested_seconds=1)


def test_game_store_series_history_and_corruption(tmp_path):
    store = GameBenchmarkStore(tmp_path / "game.json")
    store.record("before", result())
    data = store.record("after", result(average=330, low=165))
    assert data["after"]["average_fps"] == 330
    assert len(data["history"]) == 2
    text = format_game_benchmarks(data)
    assert "Средний FPS: +10.0%" in text
    assert "1 % low: +10.0%" in text
    data = store.record("before", result(average=290))
    assert data["after"] is None and len(data["history"]) == 3
    data = store.new_series()
    assert data["before"] is None and len(data["history"]) == 3
    assert GameBenchmarkStore(tmp_path / "game.json").load()["history"]
    (tmp_path / "game.json").write_text("broken", encoding="utf-8")
    assert store.load() == {"schema": 1, "before": None, "after": None, "history": []}


def test_game_history_is_bounded(tmp_path):
    store = GameBenchmarkStore(tmp_path / "game.json")
    for index in range(25):
        store.record("before" if index % 2 == 0 else "after", result())
    assert len(store.load()["history"]) == 20


def test_real_capture_command_and_cleanup(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(game_benchmark, "require_windows", lambda: None)
    monkeypatch.setattr(game_benchmark, "cs2_running", lambda _: True)
    calls = []

    def elevated(exe, arguments, wait_callback):
        calls.append((exe, arguments))
        raw_path = arguments[-1].split(" -CsvPath '", 1)[1].split("'", 1)[0]
        write_csv(Path(raw_path), [10.0] * 500)
        wait_callback()
        return 0

    monkeypatch.setattr(game_benchmark, "run_elevated", elevated)
    measured = run_game_benchmark(Config.load(), duration=5, delay=0)
    assert measured["average_fps"] == 100
    assert "--restart_as_admin" not in calls[-1][1]
    assert "-NonInteractive" in calls[-1][1]
    assert not list((tmp_path / "ShatliFlux" / "captures").iterdir())


def test_game_capture_cancellation_does_not_save(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(game_benchmark, "require_windows", lambda: None)
    monkeypatch.setattr(game_benchmark, "cs2_running", lambda _: True)
    cancelled = Event()

    def elevated(exe, arguments, wait_callback):
        cancelled.set()
        wait_callback()
        return 23

    monkeypatch.setattr(game_benchmark, "run_elevated", elevated)
    with pytest.raises(BenchmarkCancelled, match="Неполный результат не сохранён"):
        run_game_benchmark(Config.load(), duration=5, delay=0, cancel_event=cancelled)
    assert not list((tmp_path / "ShatliFlux" / "captures").iterdir())


def test_game_capture_failure_logs_details_before_cleanup(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(game_benchmark, "require_windows", lambda: None)
    monkeypatch.setattr(game_benchmark, "cs2_running", lambda _: True)

    def elevated(exe, arguments, wait_callback):
        command = arguments[-1]
        status_path = Path(command.split(" -StatusPath '", 1)[1].split("'", 1)[0])
        stderr_path = Path(command.split(" -StdErrPath '", 1)[1].split("'", 1)[0])
        status_path.write_text(json.dumps({"state": "failed", "detail": "PresentMon exit code 6"}),
                               encoding="utf-8")
        stderr_path.write_text("error: failed to start trace session: access denied.",
                               encoding="utf-8")
        return 6

    monkeypatch.setattr(game_benchmark, "run_elevated", elevated)
    caplog.set_level("WARNING", logger="shatli_flux")
    with pytest.raises(UserError, match="Windows отклонила доступ") as failure:
        run_game_benchmark(Config.load(), duration=5, delay=0)
    assert "Подробности сохранены в журнале" in str(failure.value)
    assert "PresentMon exit code 6" in caplog.text
    assert "access denied" in caplog.text
    assert not list((tmp_path / "ShatliFlux" / "captures").iterdir())


def test_game_requires_running_cs2_before_uac(monkeypatch):
    monkeypatch.setattr(game_benchmark, "require_windows", lambda: None)
    monkeypatch.setattr(game_benchmark, "cs2_running", lambda _: False)
    monkeypatch.setattr(game_benchmark, "run_elevated",
                        lambda *a, **k: pytest.fail("UAC requested without CS2"))
    with pytest.raises(UserError, match="Сначала запустите CS2"):
        run_game_benchmark(Config.load(), duration=5, delay=0)


def test_presentmon_binary_is_hash_checked(tmp_path):
    path = tmp_path / "PresentMon.exe"
    path.write_bytes(b"not the official binary")
    with pytest.raises(UserError, match="повреждён или изменён"):
        game_benchmark._verify_presentmon(path)
