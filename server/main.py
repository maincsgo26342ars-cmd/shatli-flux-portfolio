"""Stage 4 API: opaque hashed tokens, bounded sessions, transactional payments.

Run one process behind an HTTPS reverse proxy. No private signing keys here.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from shared.network import api_base, bot_url, validate_url

DAY = 86400
SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, username TEXT NOT NULL, avatar TEXT, expires INTEGER NOT NULL DEFAULT 0,
 lifetime INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS devices (
 id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, hwid TEXT NOT NULL, name TEXT NOT NULL,
 first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL, removed INTEGER);
CREATE UNIQUE INDEX IF NOT EXISTS active_device ON devices(user_id,hwid) WHERE removed IS NULL;
CREATE TABLE IF NOT EXISTS sessions (
 id TEXT PRIMARY KEY, poll_hash TEXT NOT NULL, hwid TEXT NOT NULL, name TEXT NOT NULL,
 expires INTEGER NOT NULL, user_id INTEGER, consumed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS tokens (
 hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, device_id TEXT NOT NULL, expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS orders (
 id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, plan TEXT NOT NULL, days INTEGER NOT NULL,
 amount INTEGER NOT NULL, expires INTEGER NOT NULL, charge TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS payments (
 charge TEXT PRIMARY KEY, order_id TEXT UNIQUE NOT NULL, user_id INTEGER NOT NULL,
 amount INTEGER NOT NULL, currency TEXT NOT NULL, created INTEGER NOT NULL,
 refunded INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS reminders (
 user_id INTEGER NOT NULL, expires INTEGER NOT NULL, created INTEGER NOT NULL,
 PRIMARY KEY(user_id,expires));
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY, user_id INTEGER, type TEXT NOT NULL, created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS limits (key TEXT PRIMARY KEY, bucket INTEGER NOT NULL, count INTEGER NOT NULL);
"""


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass
class Settings:
    database: Path
    bot_secret: str
    network: dict
    service: dict

    @classmethod
    def load(cls):
        root = Path(__file__).resolve().parents[1]
        service = json.loads((root / "config/service.json").read_text())
        network = json.loads((root / "config/network.json").read_text())
        api_base(network)
        bot_url(network)
        return cls(root / service["database"], os.environ.get(service["bot_secret_env"], ""),
                   network, service)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Start(Model):
    hwid: str = Field(pattern=r"^[a-f0-9]{64}$")
    name: str = Field(min_length=1, max_length=80)


class Confirm(Model):
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{32}$")
    telegram_id: int = Field(gt=0)
    username: str = Field(max_length=80)
    avatar: str | None = Field(default=None, max_length=180000)


class Remove(Model):
    device_id: str = Field(max_length=64)


class BotRemove(Remove):
    telegram_id: int = Field(gt=0)


class Order(Model):
    telegram_id: int = Field(gt=0)
    plan: str = Field(max_length=12)


class Checkout(Model):
    telegram_id: int = Field(gt=0)
    order_id: str = Field(max_length=64)
    currency: str = Field(max_length=8)
    amount: int = Field(gt=0)


class Payment(Checkout):
    charge_id: str = Field(min_length=1, max_length=256)


class Refund(Model):
    telegram_id: int = Field(gt=0)
    charge_id: str = Field(min_length=1, max_length=256)


class Reminded(Model):
    telegram_id: int = Field(gt=0)
    expires_at: int = Field(gt=0)


def create_app(settings=None, clock=time.time):
    cfg = settings or Settings.load()
    if len(cfg.bot_secret) < 32:
        raise RuntimeError("Задайте SHATLI_BOT_SECRET: случайный секрет не короче 32 символов.")
    free_ids = cfg.service.get("free_tweak_ids")
    from app.core.catalog import BY_ID
    if free_ids is not None and (len(free_ids) != 3 or len(set(free_ids)) != 3
                                or any(x not in BY_ID for x in free_ids)):
        raise RuntimeError("FREE должен содержать ровно три разных ID из каталога.")
    lifetime_entries = cfg.service.get("lifetime_pro_users", [])
    if not isinstance(lifetime_entries, list):
        raise RuntimeError("lifetime_pro_users должен быть списком.")
    lifetime_ids = set()
    for entry in lifetime_entries:
        if (not isinstance(entry, dict) or type(entry.get("telegram_id")) is not int
                or entry["telegram_id"] <= 0 or not isinstance(entry.get("username"), str)
                or not 1 <= len(entry["username"].lstrip("@")) <= 32
                or entry["telegram_id"] in lifetime_ids):
            raise RuntimeError("Некорректная или повторная запись lifetime_pro_users.")
        lifetime_ids.add(entry["telegram_id"])
    cfg.database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(cfg.database) as db:
        db.executescript(SCHEMA)
        columns = {row[1] for row in db.execute("PRAGMA table_info(payments)")}
        if "refunded" not in columns:
            db.execute("ALTER TABLE payments ADD COLUMN refunded INTEGER NOT NULL DEFAULT 0")
        columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
        if "lifetime" not in columns:
            db.execute("ALTER TABLE users ADD COLUMN lifetime INTEGER NOT NULL DEFAULT 0")

    @contextmanager
    def transaction():
        db = sqlite3.connect(cfg.database, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def now():
        return int(clock())

    def fail(code, message):
        raise HTTPException(code, message)

    def bot_auth(secret):
        if not hmac.compare_digest((secret or "").encode("utf-8"), cfg.bot_secret.encode("utf-8")):
            fail(401, "Служебный доступ запрещён.")

    def rate(db, key, maximum):
        bucket = now() // 60
        row = db.execute("SELECT * FROM limits WHERE key=?", (key,)).fetchone()
        count = row["count"] + 1 if row and row["bucket"] == bucket else 1
        if count > maximum:
            fail(429, "Слишком много запросов. Подождите минуту.")
        db.execute("INSERT OR REPLACE INTO limits VALUES (?,?,?)", (key, bucket, count))
        db.execute("DELETE FROM limits WHERE bucket < ?", (bucket - 2,))

    def authenticated(db, authorization):
        token = (authorization or "").removeprefix("Bearer ")
        if not authorization or not authorization.startswith("Bearer ") or len(token) > 128:
            fail(401, "Войдите через Telegram.")
        row = db.execute(
            "SELECT t.*, d.name FROM tokens t JOIN devices d ON d.id=t.device_id "
            "WHERE t.hash=? AND t.expires>? AND d.removed IS NULL", (digest(token), now())
        ).fetchone()
        if not row:
            fail(401, "Вход истёк или устройство удалено. Войдите снова.")
        rate(db, f"user:{row['user_id']}", 90)
        return row

    def event(db, user, kind):
        db.execute("INSERT INTO events(user_id,type,created) VALUES (?,?,?)", (user, kind, now()))

    def profile(db, user_id, current=None):
        user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not user:
            fail(404, "Сначала войдите в программу через Telegram.")
        lifetime = user_id in lifetime_ids or bool(user["lifetime"])
        active = lifetime or user["expires"] > now()
        devices = [dict(row) for row in db.execute(
            "SELECT id,name,first_seen,last_seen FROM devices WHERE user_id=? AND removed IS NULL",
            (user_id,))]
        return dict(telegram_id=user_id, username=user["username"], avatar=user["avatar"],
                    expires_at=user["expires"], server_time=now(),
                    days_left=None if lifetime else max(0, (user["expires"] - now() + DAY - 1) // DAY),
                    pro_active=active, pro_lifetime=lifetime, free_tweak_ids=free_ids,
                    devices=devices, current_device_id=current)

    def remove_device(db, user_id, device_id):
        device = db.execute("SELECT * FROM devices WHERE id=? AND user_id=? AND removed IS NULL",
                            (device_id, user_id)).fetchone()
        if not device:
            fail(404, "Устройство не найдено.")
        month = int(datetime.fromtimestamp(now(), timezone.utc).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())
        changes = db.execute(
            "SELECT count(*) FROM events WHERE user_id=? AND type='remove_device' AND created>=?",
            (user_id, month)).fetchone()[0]
        if changes >= 5:
            fail(409, "Лимит: не более пяти отвязок ПК за календарный месяц UTC. "
                 "Переустановка приложения на том же ПК не требует отвязки.")
        db.execute("UPDATE devices SET removed=? WHERE id=?", (now(), device_id))
        db.execute("DELETE FROM tokens WHERE device_id=?", (device_id,))
        db.execute("DELETE FROM sessions WHERE user_id=? AND hwid=?", (user_id, device["hwid"]))
        event(db, user_id, "remove_device")
        return {"removed": True}

    def checked_order(db, body, *, paying=False):
        order = db.execute("SELECT * FROM orders WHERE id=?", (body.order_id,)).fetchone()
        if not order or order["user_id"] != body.telegram_id:
            fail(400, "Заказ не найден.")
        if body.currency != "XTR" or order["amount"] != body.amount:
            fail(400, "Сумма или валюта не совпадает с заказом.")
        # A delayed successful_payment is still paid, even after order expiry.
        if not paying and (order["charge"] or order["expires"] <= now()):
            fail(409, "Счёт уже оплачен или истёк. Создайте новый.")
        return order

    app = FastAPI(title="Shatli FLUX", version="0.4.0", docs_url=None, redoc_url=None,
                  openapi_url=None)

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        # Uvicorn trusts forwarded headers only from the configured reverse proxy.
        if request.url.scheme != "https":
            local = request.client and request.client.host in ("127.0.0.1", "::1")
            if not (local and cfg.network.get("allow_http_loopback")):
                return JSONResponse({"detail": "Требуется HTTPS."}, status_code=400)
        try:
            length = int(request.headers.get("content-length", "0"))
        except ValueError:
            return JSONResponse({"detail": "Некорректный размер запроса."}, status_code=400)
        if length > 200000:
            return JSONResponse({"detail": "Запрос слишком большой."}, status_code=413)
        # Bound chunked bodies too, before validation/JSON parsing.
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 200000:
                return JSONResponse({"detail": "Запрос слишком большой."}, status_code=413)
        request._body = bytes(body)
        try:
            with transaction() as db:
                rate(db, "ip:" + (request.client.host if request.client else "unknown"), 180)
                db.execute("DELETE FROM sessions WHERE expires<?", (now(),))
                db.execute("DELETE FROM tokens WHERE expires<?", (now(),))
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.post("/auth/start")
    def start(body: Start, request: Request):
        sid, poll = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
        with transaction() as db:
            rate(db, "start:" + request.client.host, 6)
            db.execute("INSERT INTO sessions(id,poll_hash,hwid,name,expires) VALUES (?,?,?,?,?)",
                       (sid, digest(poll), body.hwid, body.name, now() + 300))
        return {"session_id": sid, "poll_secret": poll, "expires_in": 300,
                "telegram_url": bot_url(cfg.network) + "?start=auth_" + sid}

    @app.get("/auth/poll")
    def poll(session_id: str, x_poll_secret: str | None = Header(default=None)):
        with transaction() as db:
            row = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not row or not x_poll_secret or not hmac.compare_digest(
                    row["poll_hash"], digest(x_poll_secret)):
                fail(401, "Сессия входа недоступна.")
            if row["expires"] <= now() or row["consumed"]:
                fail(410, "Сессия входа истекла или уже использована. Начните снова.")
            if not row["user_id"]:
                return {"status": "pending"}
            uid = row["user_id"]
            device = db.execute("SELECT * FROM devices WHERE user_id=? AND hwid=? AND removed IS NULL",
                                (uid, row["hwid"])).fetchone()
            if not device:
                count = db.execute("SELECT count(*) FROM devices WHERE user_id=? AND removed IS NULL",
                                   (uid,)).fetchone()[0]
                if count >= 1:
                    fail(409, "Уже привязан один ПК. Для переноса доступа отвяжите старый ПК через бота. "
                         "На том же ПК можно войти повторно без отвязки.")
                device_id = secrets.token_hex(16)
                db.execute("INSERT INTO devices VALUES (?,?,?,?,?,?,NULL)",
                           (device_id, uid, row["hwid"], row["name"], now(), now()))
                event(db, uid, "add_device")
            else:
                device_id = device["id"]
                db.execute("UPDATE devices SET name=?,last_seen=? WHERE id=?",
                           (row["name"], now(), device_id))
            # A new login on the same device replaces prior bearer tokens.
            db.execute("DELETE FROM tokens WHERE device_id=?", (device_id,))
            token = secrets.token_urlsafe(32)
            expires = now() + 30 * DAY
            db.execute("INSERT INTO tokens VALUES (?,?,?,?)", (digest(token), uid, device_id, expires))
            db.execute("UPDATE sessions SET consumed=1 WHERE id=?", (session_id,))
            event(db, uid, "login")
        return {"status": "authorized", "access_token": token, "expires_at": expires}

    @app.post("/bot/confirm-auth")
    def confirm(body: Confirm, x_bot_secret: str | None = Header(default=None)):
        bot_auth(x_bot_secret)
        if body.avatar:
            try:
                raw = base64.b64decode(body.avatar, validate=True)
                if len(raw) > 128000 or not raw.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")):
                    raise ValueError()
            except (ValueError, TypeError):
                fail(400, "Некорректный аватар.")
        with transaction() as db:
            row = db.execute("SELECT * FROM sessions WHERE id=?", (body.session_id,)).fetchone()
            if not row or row["expires"] <= now() or row["consumed"]:
                fail(410, "Сессия входа истекла. Начните вход в программе снова.")
            if row["user_id"] and row["user_id"] != body.telegram_id:
                fail(409, "Вход уже подтверждён другим аккаунтом.")
            db.execute("INSERT INTO users(id,username,avatar) VALUES (?,?,?) "
                       "ON CONFLICT(id) DO UPDATE SET username=excluded.username,avatar=excluded.avatar",
                       (body.telegram_id, body.username, body.avatar))
            db.execute("UPDATE sessions SET user_id=? WHERE id=?", (body.telegram_id, body.session_id))
        return {"confirmed": True, "pro_lifetime": body.telegram_id in lifetime_ids}

    @app.get("/profile")
    def get_profile(authorization: str | None = Header(default=None)):
        with transaction() as db:
            auth = authenticated(db, authorization)
            db.execute("UPDATE devices SET last_seen=? WHERE id=?", (now(), auth["device_id"]))
            return profile(db, auth["user_id"], auth["device_id"])

    @app.get("/access")
    def access(authorization: str | None = Header(default=None)):
        with transaction() as db:
            uid = authenticated(db, authorization)["user_id"] if authorization else None
            active = bool(uid and profile(db, uid)["pro_active"])
        return {"pro_active": active, "free_tweak_ids": free_ids}

    @app.post("/auth/refresh")
    def refresh(authorization: str | None = Header(default=None)):
        with transaction() as db:
            auth = authenticated(db, authorization)
            expires = now() + 30 * DAY
            db.execute("UPDATE tokens SET expires=? WHERE hash=?", (expires, auth["hash"]))
        return {"expires_at": expires}

    @app.post("/auth/logout")
    def logout(authorization: str | None = Header(default=None)):
        with transaction() as db:
            auth = authenticated(db, authorization)
            db.execute("DELETE FROM tokens WHERE hash=?", (auth["hash"],))
        return {"logged_out": True}

    @app.post("/devices/remove")
    def remove(body: Remove, authorization: str | None = Header(default=None)):
        with transaction() as db:
            auth = authenticated(db, authorization)
            return remove_device(db, auth["user_id"], body.device_id)

    @app.get("/bot/profile")
    def bot_profile(telegram_id: int, x_bot_secret: str | None = Header(default=None)):
        bot_auth(x_bot_secret)
        with transaction() as db:
            return profile(db, telegram_id)

    @app.post("/bot/devices/remove")
    def bot_remove(body: BotRemove, x_bot_secret: str | None = Header(default=None)):
        bot_auth(x_bot_secret)
        with transaction() as db:
            return remove_device(db, body.telegram_id, body.device_id)

    @app.post("/bot/orders")
    def order(body: Order, x_bot_secret: str | None = Header(default=None)):
        bot_auth(x_bot_secret)
        service = cfg.service
        plan = service.get("plans", {}).get(body.plan)
        if not service.get("sales_enabled"):
            fail(503, "Продажи ещё не открыты.")
        if not service.get("support_contact") or not service.get("terms_url") or not service.get("privacy_url"):
            fail(503, "Сначала настройте поддержку, условия и политику конфиденциальности.")
        for key in ("terms_url", "privacy_url"):
            validate_url(service[key], cfg.network)
        if not plan or type(plan.get("stars")) is not int or not 0 < plan["stars"] <= 100000:
            fail(503, "Цена этого тарифа в Stars ещё не задана.")
        lifetime = plan.get("lifetime") is True
        if lifetime:
            days = 0
            if plan.get("days") not in (None, 0):
                fail(503, "У бессрочного тарифа не должно быть срока.")
        else:
            if plan.get("days") not in (30, 90, 365):
                fail(503, "Неверный срок тарифа.")
            days = plan["days"]
        oid = secrets.token_urlsafe(24)
        with transaction() as db:
            current = profile(db, body.telegram_id)
            if current["pro_lifetime"]:
                fail(409, "У вас уже есть бессрочный PRO.")
            rate(db, f"order:{body.telegram_id}", 5)
            db.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,NULL)",
                       (oid, body.telegram_id, body.plan, days, plan["stars"], now() + 3600))
        return {"order_id": oid, "amount": plan["stars"], "currency": "XTR",
                "days": days, "lifetime": lifetime}

    @app.post("/bot/checkout")
    def checkout(body: Checkout, x_bot_secret: str | None = Header(default=None)):
        bot_auth(x_bot_secret)
        if not cfg.service.get("sales_enabled"):
            fail(503, "Продажи приостановлены.")
        with transaction() as db:
            checked_order(db, body)
        return {"ok": True}

    @app.post("/bot/payment")
    def payment(body: Payment, x_bot_secret: str | None = Header(default=None)):
        bot_auth(x_bot_secret)
        with transaction() as db:
            order = checked_order(db, body, paying=True)
            old = db.execute("SELECT * FROM payments WHERE charge=?", (body.charge_id,)).fetchone()
            if old:
                if old["order_id"] != body.order_id:
                    fail(409, "Этот платёж уже относится к другому заказу.")
                return {"credited": True, "duplicate": True, "lifetime": order["days"] == 0}
            if order["charge"]:
                fail(409, "Заказ уже оплачен другим платежом. Требуется разбор и возврат.")
            user = db.execute("SELECT * FROM users WHERE id=?", (body.telegram_id,)).fetchone()
            if order["days"] == 0:
                db.execute("UPDATE users SET lifetime=1 WHERE id=?", (body.telegram_id,))
                expiry, lifetime = user["expires"], True
            else:
                expiry = max(now(), user["expires"]) + order["days"] * DAY
                db.execute("UPDATE users SET expires=? WHERE id=?", (expiry, body.telegram_id))
                lifetime = False
            db.execute("INSERT INTO payments(charge,order_id,user_id,amount,currency,created,refunded) VALUES (?,?,?,?,?,?,0)",
                       (body.charge_id, body.order_id, body.telegram_id, body.amount, "XTR", now()))
            db.execute("UPDATE orders SET charge=? WHERE id=?", (body.charge_id, body.order_id))
            event(db, body.telegram_id, "payment")
        return {"credited": True, "duplicate": False, "expires_at": expiry, "lifetime": lifetime}

    def signed_document(filename):
        path = Path(__file__).resolve().parents[1] / "config" / filename
        try:
            if path.stat().st_size > 128000:
                raise ValueError
            document = json.loads(path.read_text(encoding="utf-8"))
            if set(document) != {"payload", "signature"} or not all(isinstance(v, str) for v in document.values()):
                raise ValueError
            return document
        except (OSError, ValueError, TypeError):
            fail(503, "Подписанный документ ещё не опубликован.")

    @app.get("/latest")
    def latest():
        return signed_document("release.signed.json")

    @app.get("/presets")
    def presets(authorization: str | None = Header(default=None)):
        with transaction() as db:
            uid = authenticated(db, authorization)["user_id"]
            if not profile(db, uid)["pro_active"]:
                fail(403, "Для обновления пресетов нужна активная PRO-подписка.")
        return signed_document("presets.signed.json")

    @app.post("/bot/refund")
    def refund(body: Refund, x_bot_secret: str | None = Header(default=None)):
        bot_auth(x_bot_secret)
        with transaction() as db:
            payment_row = db.execute("SELECT p.*,o.days FROM payments p JOIN orders o ON o.id=p.order_id "
                                     "WHERE p.charge=? AND p.user_id=?", (body.charge_id, body.telegram_id)).fetchone()
            if not payment_row:
                fail(404, "Платёж не найден.")
            if payment_row["refunded"]:
                user = db.execute("SELECT expires FROM users WHERE id=?", (body.telegram_id,)).fetchone()
                return {"refunded": True, "duplicate": True, "expires_at": user["expires"],
                        "lifetime": payment_row["days"] == 0}
            user = db.execute("SELECT expires FROM users WHERE id=?", (body.telegram_id,)).fetchone()
            if payment_row["days"] == 0:
                db.execute("UPDATE users SET lifetime=0 WHERE id=?", (body.telegram_id,))
                expiry, lifetime = user["expires"], True
            else:
                expiry = max(now(), user["expires"] - payment_row["days"] * DAY)
                db.execute("UPDATE users SET expires=? WHERE id=?", (expiry, body.telegram_id))
                lifetime = False
            db.execute("UPDATE payments SET refunded=1 WHERE charge=?", (body.charge_id,))
            event(db, body.telegram_id, "refund")
        return {"refunded": True, "duplicate": False, "expires_at": expiry, "lifetime": lifetime}

    @app.get("/bot/reminders")
    def reminders(x_bot_secret: str | None = Header(default=None)):
        bot_auth(x_bot_secret)
        with transaction() as db:
            rows = db.execute("SELECT id,username,expires FROM users u WHERE lifetime=0 AND expires>? AND expires<=? "
                              "AND NOT EXISTS(SELECT 1 FROM reminders r WHERE r.user_id=u.id AND r.expires=u.expires) "
                              "LIMIT 100", (now(), now() + 3 * DAY)).fetchall()
            return {"reminders": [{"telegram_id": row["id"], "username": row["username"],
                                   "expires_at": row["expires"]} for row in rows if row["id"] not in lifetime_ids]}

    @app.post("/bot/reminded")
    def reminded(body: Reminded, x_bot_secret: str | None = Header(default=None)):
        bot_auth(x_bot_secret)
        with transaction() as db:
            db.execute("INSERT OR IGNORE INTO reminders VALUES (?,?,?)", (body.telegram_id, body.expires_at, now()))
        return {"recorded": True}

    return app
