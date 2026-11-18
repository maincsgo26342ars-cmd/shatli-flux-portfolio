"""Telegram login service; no network or machine reads on import/construction."""
import hashlib
import platform

from app.core.catalog import FREE_TWEAK_IDS
from app.core.token_store import TokenStore
from app.core.windows import require_windows, UserError
from shared.client import ApiClient, ApiError
from shared.network import api_base, bot_url


def device_identity():
    require_windows()
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography",
                        0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
        guid = winreg.QueryValueEx(key, "MachineGuid")[0]
    # Send a product-specific digest, never raw MachineGuid.
    return hashlib.sha256(("ShatliFLUX|" + guid).encode()).hexdigest()


class AuthService:
    def __init__(self, config, api=None, store=None, identity=device_identity):
        self.config = config
        self.api = api or ApiClient(config.network)
        self.store = store or TokenStore(config.user_data() / config.values["paths"]["token"])
        self.identity = identity

    def configured(self):
        try:
            api_base(self.config.network)
            bot_url(self.config.network)
            return True
        except ValueError:
            return False

    def call(self, method, path, **kwargs):
        try:
            return self.api.call(method, path, **kwargs)
        except ApiError as exc:
            if exc.status == 401 and path not in ("/auth/start", "/auth/poll"):
                self.store.clear()
            raise UserError(str(exc)) from None

    def headers(self):
        saved = self.store.load()
        if not saved:
            return {}
        return {"Authorization": "Bearer " + saved["access_token"]}

    def _access(self):
        """Server decides access; the shipped FREE set is a network-failure fallback only."""
        try:
            data = self.api.call("GET", "/access", headers=self.headers())
        except ApiError as exc:
            if exc.status == 401:
                self.store.clear()
            if exc.status == 0 or exc.status >= 500:
                return {"pro_active": False, "free_tweak_ids": list(FREE_TWEAK_IDS),
                        "offline": True}
            raise UserError(str(exc)) from None
        if not isinstance(data, dict):
            raise UserError("Сервер вернул некорректный ответ. Повторите позже.")
        return data

    def start(self):
        return self.call("POST", "/auth/start",
                         body={"hwid": self.identity(), "name": platform.node()[:80] or "Windows PC"})

    def poll(self, session):
        result = self.call("GET", "/auth/poll", params={"session_id": session["session_id"]},
                           headers={"X-Poll-Secret": session["poll_secret"]})
        if result.get("status") == "authorized":
            self.store.save({"access_token": result["access_token"], "expires_at": result["expires_at"]})
        return result

    def profile(self):
        headers = self.headers()
        if not headers:
            return None
        data = self.call("GET", "/profile", headers=headers)
        saved = self.store.load()
        if saved and saved["expires_at"] - data["server_time"] < 7 * 86400:
            renewed = self.call("POST", "/auth/refresh", headers=headers)
            saved["expires_at"] = renewed["expires_at"]
            self.store.save(saved)
        return data

    def remove_device(self, device_id):
        self.call("POST", "/devices/remove", headers=self.headers(), body={"device_id": device_id})

    def logout(self):
        warning = ""
        try:
            headers = self.headers()
            if headers:
                self.call("POST", "/auth/logout", headers=headers)
        except UserError:
            warning = "Локальный вход удалён. Сервер недоступен; для отзыва сессии удалите устройство через бота."
        finally:
            self.store.clear()
        return warning or "Вы вышли из аккаунта. Откат остаётся доступным."

    def authorize_apply(self, ids, *, custom=False):
        # A fresh server check for each new operation. No cached paid access.
        data = self._access()
        if data.get("pro_active") is True:
            return
        if custom:
            raise UserError("«Свой набор» доступен с PRO. Откат доступен всегда.")
        free = data.get("free_tweak_ids")
        if free is None:
            raise UserError("Состав FREE ещё не настроен владельцем продукта. Откат и восстановление доступны.")
        if not set(ids).issubset(set(free)):
            raise UserError("Для этих настроек нужен PRO. Откат доступен всегда.")

    def free_preset(self):
        data = self._access()
        if data.get("offline") is True:
            return list(FREE_TWEAK_IDS)
        free = data.get("free_tweak_ids")
        if free is None:
            raise UserError("Состав FREE ещё не настроен владельцем продукта. Откат и восстановление доступны.")
        from app.core.catalog import BY_ID
        if (not isinstance(free, list) or not all(isinstance(x, str) for x in free)
                or len(free) != 3 or len(set(free)) != 3 or any(x not in BY_ID for x in free)):
            raise UserError("Сервер вернул некорректный состав FREE.")
        return free
