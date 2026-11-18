"""Offline, escaped HTML report for diagnostics and benchmark results."""
from datetime import datetime
from html import escape
import os
from pathlib import Path

from app.core.benchmark import format_benchmarks
from app.core.game_benchmark import format_game_benchmarks


def export_html(path, config, diagnostic, benchmark_data, game_data=None):
    path = Path(path)
    if path.suffix.casefold() not in (".html", ".htm"):
        path = path.with_suffix(".html")
    generated = datetime.now().astimezone().strftime("%d.%m.%Y %H:%M")
    diagnostic_text = diagnostic["text"] if diagnostic else "Диагностика ещё не выполнена."
    benchmark_text = format_benchmarks(benchmark_data)
    game_text = format_game_benchmarks(game_data or {})
    recommendation = (diagnostic["recommended_title"] + "\n" + diagnostic["recommendation_reason"]
                      ) if diagnostic else "Рекомендация появится после диагностики."
    document = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Shatli FLUX · отчёт</title>
<style>
:root{{color-scheme:dark;--bg:#101718;--card:#18231f;--text:#e8ecee;--muted:#a4b0b4;--accent:#a8d5c8}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:960px;margin:auto;padding:40px 24px}}header{{border-bottom:1px solid #395047;margin-bottom:24px;padding-bottom:20px}}
h1{{font-size:28px;margin:0 0 8px}}h2{{font-size:20px;color:var(--accent)}}.muted{{color:var(--muted)}}
section{{background:var(--card);border:1px solid #34483f;border-radius:16px;padding:20px;margin:16px 0}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.6 ui-monospace,Consolas,monospace;margin:0}}
.badge{{display:inline-block;color:#10241b;background:var(--accent);padding:4px 10px;border-radius:999px;font-weight:600}}
footer{{color:var(--muted);font-size:13px;margin-top:24px}}@media print{{body{{background:white;color:#17221d}}section{{background:white}}h2{{color:#315b48}}.muted,footer{{color:#4d5c55}}}}
</style></head><body><main>
<header><span class="badge">Shatli FLUX {escape(config.values["version"])}</span>
<h1>Диагностика и замеры</h1><div class="muted">Сформировано: {escape(generated)} · локальный отчёт</div></header>
<section><h2>Рекомендация</h2><pre>{escape(recommendation)}</pre></section>
<section><h2>Диагностика ПК</h2><pre>{escape(diagnostic_text)}</pre></section>
<section><h2>Системный бенчмарк · до / после</h2><pre>{escape(benchmark_text)}</pre></section>
<section><h2>Игровой бенчмарк CS2 · FPS / 1 % low</h2><pre>{escape(game_text)}</pre></section>
<footer>Отчёт содержит сведения об оборудовании и имена отдельных процессов. Проверяйте его перед передачей другим людям.
Системный замер не является игровым FPS-тестом. Игровые результаты сопоставимы только в одинаковых сценах и настройках; прирост производительности не гарантируется.</footer>
</main></body></html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(document, encoding="utf-8")
    os.replace(temporary, path)
    return path
