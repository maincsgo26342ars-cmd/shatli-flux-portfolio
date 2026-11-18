# Вход и PRO: интеграция с отдельным проектом

В этом репозитории находятся Windows-клиент и API. Код Telegram-бота, его зависимости,
очередь платежей и средства запуска удалены по просьбе владельца. Отдельный проект
бота не открывался и не изменялся. Его совместимость с этим API ещё не подтверждена.

## Что остаётся в клиенте

- Вход через Telegram с QR и одноразовой сессией.
- Локальный DPAPI-токен, профиль, проверка PRO и привязка к одному ПК.
- Локальный откат независимо от входа и подписки.
- Ссылка на внешнего бота из config/network.json.

## API

Для запуска API из корня проекта:

```powershell
.\.venv\Scripts\python.exe -m pip install -r server/requirements.txt
.\.venv\Scripts\python.exe -m uvicorn server.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

Служебная конфигурация: config/service.json, пример: config/service.example.json.
Общий секрет API задаётся переменной окружения, имя которой указано в bot_secret_env.
Это секрет интеграции, не Telegram Bot Token. Telegram-токен в данном проекте не нужен.
Для публичного API требуются HTTPS и соответствующий allowed_origins; локальный HTTP
разрешён только для разработки. Продажи остаются выключенными.

## Сохраняемый контракт интеграции

Клиент вызывает /auth/start, получает telegram_url и poll_secret, затем опрашивает
/auth/poll. Внешний сервис после подтверждения пользователя вызывает
POST /bot/confirm-auth с X-Bot-Secret. После авторизации клиент получает токен.
Публичный клиент не должен знать X-Bot-Secret.

Служебные маршруты API сохранены для совместимости:

| Метод | Маршрут | Назначение |
|---|---|---|
| POST | /bot/confirm-auth | Подтверждение входа |
| GET | /bot/profile | Получение профиля |
| POST | /bot/devices/remove | Отвязка ПК |
| POST | /bot/orders | Создание заказа |
| POST | /bot/checkout | Проверка заказа перед оплатой |
| POST | /bot/payment | Идемпотентный учёт подтверждённого платежа |
| POST | /bot/refund | Учёт возврата в правах доступа |
| GET | /bot/reminders | Получение напоминаний |
| POST | /bot/reminded | Подтверждение обработки напоминания |

Эти маршруты сами не принимают оплату и не возвращают Stars через Telegram.
Достоверность платёжного события и вызов Telegram API обеспечиваются отдельным
доверенным проектом. До изучения его исходников не считать интеграцию готовой.

Проверки конфигурации API:

```powershell
.\.venv\Scripts\python.exe -X utf8 tools/check_service_config.py
.\.venv\Scripts\python.exe -X utf8 tools/check_release.py
```
