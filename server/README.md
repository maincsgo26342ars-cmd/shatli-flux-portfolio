# Сервер Shatli FLUX

FastAPI + SQLite, версия 0.4.0. Запускать явно из корня проекта; интерфейс пользователя сам сервер не запускает.

```powershell
.\.venv\Scripts\python.exe -m pip install -r server/requirements.txt
.\.venv\Scripts\python.exe -m uvicorn server.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

До запуска настройте `config/network.json` и служебный секрет в окружении по `docs/STAGE_4.md`. Приватный Ed25519-ключ серверу не нужен. Telegram-бот находится в отдельном проекте; этот API сохраняет маршруты внешней интеграции. Telegram Bot Token здесь не используется.
