from app.config import Config
from app.core.reporting import export_html


def test_html_report_is_offline_escaped_and_complete(tmp_path):
    diagnostic = {"text": "CPU: <TEST> & GPU\nБез сети",
                  "recommended_title": "Средний ПК",
                  "recommendation_reason": "Только рекомендация"}
    benchmark = {"schema": 2, "before": None, "after": None, "history": []}
    path = export_html(tmp_path / "report", Config.load(), diagnostic, benchmark)
    text = path.read_text(encoding="utf-8")
    assert path.suffix == ".html"
    assert "&lt;TEST&gt; &amp; GPU" in text
    assert "<TEST>" not in text
    assert "Только рекомендация" in text
    assert "не FPS" in text
    assert "Проверяйте его перед передачей" in text
    assert "<script" not in text
    assert not (tmp_path / "report.html.tmp").exists()


def test_html_report_handles_missing_diagnostics(tmp_path):
    path = export_html(tmp_path / "empty.html", Config.load(), None,
                       {"schema": 2, "before": None, "after": None, "history": []})
    text = path.read_text(encoding="utf-8")
    assert "Диагностика ещё не выполнена" in text
    assert "замер не выполнен" in text
    assert "Игровой бенчмарк CS2" in text


def test_html_report_includes_real_game_metrics(tmp_path):
    measured = {"application": "cs2.exe", "source": "PresentMon 2.6.0", "frames": 18000,
                "duration_seconds": 60, "observed_seconds": 60.0, "average_fps": 300.0,
                "fps_1_low": 150.0, "fps_0_1_low": 75.0, "frame_time_average_ms": 3.33,
                "frame_time_p95_ms": 6.0, "frame_time_p99_ms": 8.0,
                "frame_time_max_ms": 20.0, "frame_time_stddev_ms": 1.0,
                "recorded_at": "2026-11-01T12:00:00+00:00"}
    game = {"schema": 1, "before": measured, "after": None, "history": []}
    path = export_html(tmp_path / "game.html", Config.load(), None,
                       {"schema": 2, "before": None, "after": None, "history": []}, game)
    text = path.read_text(encoding="utf-8")
    assert "Средний FPS: 300.0" in text
    assert "1 % low: 150.0 FPS" in text
    assert "PresentMon 2.6.0" in text
