# Выпуск Shatli FLUX

## Клиент

1. Установите `requirements-dev.txt` в `.venv`.
2. Выполните `python -m pytest -q` и проверьте интерфейс на Windows 10/11.
3. Выполните `pyinstaller --clean -y packaging/ShatliFlux.spec` из корня проекта.
4. Проверьте `dist/ShatliFlux/ShatliFlux.exe` на чистой VM: вход, FREE/PRO, файлы CS2, UAC и откат.
5. Соберите `packaging/installer.iss` через Inno Setup 6.
6. Подпишите программу и установщик своим сертификатом Authenticode. Самозаверяющий сертификат не заменяет публичное доверие.
7. Проверьте SHA-256 и антивирусную репутацию до публикации.

Клиентская сборка не включает `server/`, `config/service.json`, базы или секреты.

## Подпись обновлений

Приватный Ed25519-ключ храните только у владельца, вне репозитория и сервера. Скопируйте публичный ключ из `tools/sign_release.py public --key ...` в `config/app.json → release_public_key` до сборки клиента.

После публикации установщика на HTTPS:

```powershell
python tools/sign_release.py release --key release-private.pem --version 1.0.1 --url https://ВАШ-ДОМЕН/ShatliFlux-Setup.exe --sha256 ХЕШ --notes "Описание"
python tools/sign_release.py presets --key release-private.pem --version 1.0.1 --notes "Описание каталога"
```

Скопируйте подписанные JSON только на сервер. Клиент проверяет подпись, адрес и SHA-256 манифеста; автоматической установки нет.

## Перед продажами

Сначала выполните `python -X utf8 tools/check_release.py` (или `--json` для автоматизации).
Код 1 означает, что конфигурация не готова. Проверка не заменяет VM-приёмку, подпись
бинарников и реальную оплату. Если `config/service.json` отсутствует, создайте его
из `config/service.example.json`, заполните поля и задайте секреты через окружение.
Сам пример не содержит секретов и не включает продажи.

Задайте публичный HTTPS API, цену Stars, поддержку, оферту, политику конфиденциальности и возврата. Только затем включите `sales_enabled`. Проведите реальную оплату бессрочного PRO, возврат и напоминание на тестовом аккаунте.
