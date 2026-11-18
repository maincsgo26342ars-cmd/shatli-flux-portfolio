"""Bounded JSON transport. Secrets never occur in URLs or error messages."""
import json

from shared.network import api_base, validate_url


class ApiError(RuntimeError):
    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status


class ApiClient:
    def __init__(self, network, transport=None):
        self.network = network
        self.transport = transport

    def call(self, method, path, *, body=None, headers=None, params=None):
        try:
            if not path.startswith("/") or path.startswith("//") or "://" in path:
                raise ValueError("Некорректный путь API.")
            url = validate_url(api_base(self.network) + path, self.network)
        except ValueError as exc:
            raise ApiError(str(exc)) from None
        import httpx  # Load TLS/network stack only when an API request is needed.
        try:
            with httpx.Client(timeout=8, follow_redirects=False, trust_env=False,
                              transport=self.transport) as client:
                with client.stream(method, url, json=body, headers=headers, params=params) as response:
                    chunks = bytearray()
                    for part in response.iter_bytes():
                        chunks.extend(part)
                        if len(chunks) > 256000:
                            raise ApiError("Ответ сервера слишком большой.")
                    if 300 <= response.status_code < 400:
                        raise ApiError("Перенаправления API запрещены.", response.status_code)
                    try:
                        value = json.loads(chunks)
                    except (ValueError, UnicodeError):
                        raise ApiError("Сервер вернул некорректный ответ.") from None
                    if not response.is_success:
                        message = value.get("detail") if isinstance(value, dict) else None
                        raise ApiError(
                            message[:300] if isinstance(message, str)
                            else "Сервер отклонил запрос. Проверьте конфигурацию.",
                            response.status_code)
                    if not isinstance(value, dict):
                        raise ApiError("Неверный формат ответа сервера.")
                    return value
        except httpx.HTTPError:
            raise ApiError("Нет соединения с сервером. Проверьте интернет и попробуйте снова.") from None
