"""One origin allowlist; no redirects, URL credentials, fragments or implicit HTTP."""
from urllib.parse import urlsplit
import re


def validate_url(url, network):
    if not isinstance(url, str) or any(ord(c) < 33 for c in url) or "\\" in url:
        raise ValueError("Некорректный сетевой адрес.")
    parsed = urlsplit(url)
    if not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Некорректный сетевой адрес.")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and local and network.get("allow_http_loopback") is True
    ):
        raise ValueError("Для подключения требуется HTTPS.")
    if origin not in network.get("allowed_origins", []):
        raise ValueError("Адрес не включён в config/network.json → allowed_origins.")
    return url


def api_base(network):
    url = validate_url(network.get("api_base_url"), network).rstrip("/")
    if urlsplit(url).query:
        raise ValueError("Адрес API не должен содержать query.")
    return url


def bot_url(network):
    url = validate_url(network.get("telegram_bot_url"), network).rstrip("/")
    part = urlsplit(url)
    if part.scheme != "https" or part.netloc != "t.me" or part.query:
        raise ValueError("Ожидается HTTPS-ссылка t.me на бота.")
    if not re.fullmatch(r"/[A-Za-z0-9_]{5,32}", part.path):
        raise ValueError("Не задано корректное имя Telegram-бота.")
    return url
