import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import sqlite3

import pytest
from fastapi.testclient import TestClient

from server.main import Settings, create_app, DAY

SECRET = "test-only-secret-never-use-in-deployment-" * 2
ADMIN_ID = 1474747313
BOT = {"X-Bot-Secret": SECRET}
NETWORK = {
    "api_base_url": "https://api.example.test",
    "telegram_bot_url": "https://t.me/shatli_test_bot",
    "allowed_origins": ["https://api.example.test", "https://t.me", "https://example.test"],
}


@pytest.fixture
def setup(tmp_path):
    clock = [1800000000]
    service = {"free_tweak_ids": None, "sales_enabled": True, "support_contact": "test",
               "terms_url": "https://example.test/terms", "privacy_url": "https://example.test/privacy",
               "lifetime_pro_users": [{"telegram_id": ADMIN_ID, "username": "hjjkjjk"}],
               "plans": {"PRO30": {"days": 30, "stars": 50}, "PRO90": {"days": 90, "stars": 120},
                         "PRO": {"lifetime": True, "stars": 300}}}
    settings = Settings(tmp_path / "db.sqlite3", SECRET, NETWORK, service)
    app = create_app(settings, clock=lambda: clock[0])
    with TestClient(app, base_url="https://api.example.test") as client:
        yield client, settings, clock


def start(client, machine="one"):
    response = client.post("/auth/start", json={
        "hwid": hashlib.sha256(machine.encode()).hexdigest(), "name": machine})
    assert response.status_code == 200, response.text
    return response.json()


def confirm(client, session, user=123):
    return client.post("/bot/confirm-auth", headers=BOT, json={
        "session_id": session["session_id"], "telegram_id": user, "username": "test_user"})


def poll(client, session):
    return client.get("/auth/poll", params={"session_id": session["session_id"]},
                      headers={"X-Poll-Secret": session["poll_secret"]})


def login(client, machine="one", user=123):
    session = start(client, machine)
    assert confirm(client, session, user).status_code == 200
    result = poll(client, session)
    assert result.status_code == 200, result.text
    return {"Authorization": "Bearer " + result.json()["access_token"]}


def order(client, user=123, plan="PRO30"):
    response = client.post("/bot/orders", headers=BOT, json={"telegram_id": user, "plan": plan})
    assert response.status_code == 200, response.text
    return response.json()


def payment(value, charge="telegram-charge-one", user=123):
    return {"order_id": value["order_id"], "telegram_id": user, "amount": value["amount"],
            "currency": "XTR", "charge_id": charge}


def test_auth_separate_poll_secret_one_time_and_hashed_token(setup):
    client, settings, _ = setup
    session = start(client)
    assert session["expires_in"] == 300
    assert session["poll_secret"] not in session["telegram_url"]
    assert client.get("/auth/poll", params={"session_id": session["session_id"]}).status_code == 401
    assert poll(client, session).json() == {"status": "pending"}
    assert confirm(client, session).status_code == 200
    result = poll(client, session).json()
    assert result["status"] == "authorized"
    assert poll(client, session).status_code == 410
    with sqlite3.connect(settings.database) as db:
        stored = db.execute("SELECT hash FROM tokens").fetchone()[0]
    assert stored != result["access_token"]
    assert stored == hashlib.sha256(result["access_token"].encode()).hexdigest()


def test_lifetime_pro_follows_telegram_id_not_username(setup):
    client, _, clock = setup
    owner_session = start(client, "owner")
    confirmed = client.post("/bot/confirm-auth", headers=BOT, json={
        "session_id": owner_session["session_id"], "telegram_id": ADMIN_ID,
        "username": "hjjkjjk"})
    assert confirmed.json() == {"confirmed": True, "pro_lifetime": True}
    owner = {"Authorization": "Bearer " + poll(client, owner_session).json()["access_token"]}
    data = client.get("/profile", headers=owner).json()
    assert data["telegram_id"] == ADMIN_ID
    assert data["pro_active"] is True
    assert data["pro_lifetime"] is True
    assert data["days_left"] is None
    assert client.get("/access", headers=owner).json()["pro_active"] is True

    other_session = start(client, "other")
    other_confirm = client.post("/bot/confirm-auth", headers=BOT, json={
        "session_id": other_session["session_id"], "telegram_id": 456,
        "username": "hjjkjjk"})
    assert other_confirm.json()["pro_lifetime"] is False
    other = {"Authorization": "Bearer " + poll(client, other_session).json()["access_token"]}
    other_data = client.get("/profile", headers=other).json()
    assert other_data["pro_active"] is False
    assert other_data["pro_lifetime"] is False

    clock[0] += 100 * 365 * DAY
    data = client.get("/bot/profile", headers=BOT, params={"telegram_id": ADMIN_ID}).json()
    assert data["pro_active"] is True
    assert data["pro_lifetime"] is True


def test_confirm_requires_service_secret(setup):
    client, _, _ = setup
    session = start(client)
    body = {"session_id": session["session_id"], "telegram_id": 123, "username": "test"}
    assert client.post("/bot/confirm-auth", json=body).status_code == 401
    assert poll(client, session).json()["status"] == "pending"


def test_auth_expiry_and_cannot_switch_telegram_user(setup):
    client, _, clock = setup
    session = start(client)
    assert confirm(client, session).status_code == 200
    assert confirm(client, session, user=456).status_code == 409
    clock[0] += 301
    assert poll(client, session).status_code in (401, 410)
    assert confirm(client, session).status_code == 410


def test_one_device_same_device_relogin_and_revocation(setup):
    client, _, clock = setup
    first = login(client)
    newest = login(client)
    assert client.get("/profile", headers=first).status_code == 401
    session = start(client, "two")
    confirm(client, session)
    assert poll(client, session).status_code == 409
    data = client.get("/profile", headers=newest).json()
    assert len(data["devices"]) == 1
    foreign = login(client, "foreign", 456)
    device = data["current_device_id"]
    assert client.post("/devices/remove", headers=foreign, json={"device_id": device}).status_code == 404
    assert client.post("/bot/devices/remove", headers=BOT,
                       json={"telegram_id": 123, "device_id": device}).status_code == 200
    assert client.get("/profile", headers=newest).status_code == 401
    assert poll(client, session).status_code == 200
    clock[0] += 60
    again = login(client, "two")
    assert len(client.get("/profile", headers=again).json()["devices"]) == 1


def test_reinstall_and_relogin_do_not_consume_transfer_allowance(setup):
    client, settings, clock = setup
    for _ in range(7):
        clock[0] += 61
        token = login(client, "same-pc")
        assert len(client.get("/profile", headers=token).json()["devices"]) == 1
        assert client.post("/auth/logout", headers=token).status_code == 200
    with sqlite3.connect(settings.database) as db:
        assert db.execute("SELECT count(*) FROM events WHERE type='remove_device'").fetchone()[0] == 0


def test_monthly_device_removal_limit(setup):
    client, _, clock = setup
    for i in range(6):
        clock[0] += 61  # stay below IP auth-start limit
        token = login(client, str(i))
        profile = client.get("/profile", headers=token).json()
        result = client.post("/devices/remove", headers=token,
                             json={"device_id": profile["current_device_id"]})
        assert result.status_code == (200 if i < 5 else 409)


def test_refresh_expiry_logout(setup):
    client, _, clock = setup
    token = login(client)
    clock[0] += 29 * DAY
    refreshed = client.post("/auth/refresh", headers=token)
    assert refreshed.json()["expires_at"] == clock[0] + 30 * DAY
    clock[0] += 2 * DAY
    assert client.get("/profile", headers=token).status_code == 200
    assert client.post("/auth/logout", headers=token).status_code == 200
    assert client.get("/profile", headers=token).status_code == 401
    token = login(client)
    clock[0] += 30 * DAY + 1
    assert client.get("/profile", headers=token).status_code == 401


def test_payment_extends_once_and_after_expiration(setup):
    client, _, clock = setup
    token = login(client)
    value = order(client)
    data = payment(value)
    checkout = {k: v for k, v in data.items() if k != "charge_id"}
    assert client.post("/bot/checkout", headers=BOT, json=checkout).json()["ok"]
    assert not client.get("/profile", headers=token).json()["pro_active"]
    result = client.post("/bot/payment", headers=BOT, json=data)
    assert result.json()["expires_at"] == clock[0] + 30 * DAY
    assert client.post("/bot/payment", headers=BOT, json=data).json()["duplicate"]
    value2 = order(client)
    result = client.post("/bot/payment", headers=BOT, json=payment(value2, "two"))
    assert result.json()["expires_at"] == clock[0] + 60 * DAY
    clock[0] += 65 * DAY
    value3 = order(client)
    result = client.post("/bot/payment", headers=BOT, json=payment(value3, "three"))
    assert result.json()["expires_at"] == clock[0] + 30 * DAY


@pytest.mark.parametrize("change", [
    {"amount": 1}, {"currency": "RUB"}, {"telegram_id": 999}, {"order_id": "missing"},
])
def test_payment_rejects_mismatched_receipt(setup, change):
    client, _, _ = setup
    token = login(client)
    data = payment(order(client))
    data.update(change)
    assert client.post("/bot/payment", headers=BOT, json=data).status_code == 400
    assert not client.get("/profile", headers=token).json()["pro_active"]


def test_payment_secret_unique_charge_and_order(setup):
    client, _, _ = setup
    login(client)
    data = payment(order(client))
    assert client.post("/bot/payment", json=data).status_code == 401
    assert client.post("/bot/payment", headers=BOT, json=data).status_code == 200
    assert client.post("/bot/payment", headers=BOT, json={**data, "charge_id": "other"}).status_code == 409
    second = payment(order(client))
    assert client.post("/bot/payment", headers=BOT, json=second).status_code == 409


def test_concurrent_payment_replay_credits_once(setup):
    client, settings, _ = setup
    login(client)
    data = payment(order(client))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: client.post("/bot/payment", headers=BOT, json=data), range(4)))
    assert all(r.status_code == 200 for r in results)
    assert sum(not r.json()["duplicate"] for r in results) == 1
    with sqlite3.connect(settings.database) as db:
        assert db.execute("SELECT count(*) FROM payments").fetchone()[0] == 1


def test_late_paid_receipt_accepted_but_expired_checkout_refused(setup):
    client, _, clock = setup
    login(client)
    data = payment(order(client))
    clock[0] += 3601
    checkout = {k: v for k, v in data.items() if k != "charge_id"}
    assert client.post("/bot/checkout", headers=BOT, json=checkout).status_code == 409
    assert client.post("/bot/payment", headers=BOT, json=data).status_code == 200


def test_lifetime_purchase_is_permanent_and_cannot_be_repeated(setup):
    client, _, _ = setup
    token = login(client)
    value = order(client, plan="PRO")
    assert value["amount"] == 300
    assert value["days"] == 0
    assert value["lifetime"] is True
    data = payment(value, "lifetime-one")
    checkout = {k: v for k, v in data.items() if k != "charge_id"}
    assert client.post("/bot/checkout", headers=BOT, json=checkout).json()["ok"]
    result = client.post("/bot/payment", headers=BOT, json=data)
    assert result.json()["lifetime"] is True
    profile = client.get("/profile", headers=token).json()
    assert profile["pro_lifetime"] is True
    assert profile["pro_active"] is True
    assert profile["days_left"] is None
    assert client.get("/access", headers=token).json()["pro_active"] is True
    repeat = client.post("/bot/orders", headers=BOT, json={"telegram_id": 123, "plan": "PRO"})
    assert repeat.status_code == 409


def test_refund_lifetime_restores_free_and_keeps_prior_term(setup):
    client, _, _ = setup
    token = login(client)
    term = order(client, plan="PRO30")
    client.post("/bot/payment", headers=BOT, json=payment(term, "term-one"))
    lifetime = order(client, plan="PRO")
    client.post("/bot/payment", headers=BOT, json=payment(lifetime, "lifetime-two"))
    assert client.get("/profile", headers=token).json()["pro_lifetime"] is True
    refund = {"telegram_id": 123, "charge_id": "lifetime-two"}
    result = client.post("/bot/refund", headers=BOT, json=refund)
    assert result.json()["refunded"] is True
    assert result.json()["lifetime"] is True
    profile = client.get("/profile", headers=token).json()
    assert profile["pro_lifetime"] is False
    assert profile["pro_active"] is True
    assert profile["days_left"] == 30
    duplicate = client.post("/bot/refund", headers=BOT, json=refund)
    assert duplicate.json()["duplicate"] is True


def test_refund_term_only_removes_purchased_days(setup):
    client, _, _ = setup
    token = login(client)
    for charge in ("term-one", "term-two"):
        client.post("/bot/payment", headers=BOT, json=payment(order(client), charge))
    assert client.get("/profile", headers=token).json()["days_left"] == 60
    result = client.post("/bot/refund", headers=BOT, json={"telegram_id": 123, "charge_id": "term-two"})
    assert result.json()["lifetime"] is False
    profile = client.get("/profile", headers=token).json()
    assert profile["pro_active"] is True
    assert profile["days_left"] == 30


def test_static_lifetime_owner_cannot_buy_again(setup):
    client, _, _ = setup
    login(client, user=ADMIN_ID)
    response = client.post("/bot/orders", headers=BOT, json={"telegram_id": ADMIN_ID, "plan": "PRO"})
    assert response.status_code == 409


def test_old_users_table_gets_lifetime_column(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT NOT NULL, "
                   "avatar TEXT, expires INTEGER NOT NULL DEFAULT 0)")
    service = {"free_tweak_ids": None, "sales_enabled": False, "support_contact": None,
               "terms_url": None, "privacy_url": None, "lifetime_pro_users": [], "plans": {}}
    create_app(Settings(path, SECRET, NETWORK, service))
    with sqlite3.connect(path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
    assert "lifetime" in columns


def test_unconfigured_sales_fail_closed(setup):
    client, settings, _ = setup
    login(client)
    settings.service["sales_enabled"] = False
    body = {"telegram_id": 123, "plan": "PRO30"}
    assert client.post("/bot/orders", headers=BOT, json=body).status_code == 503
    settings.service["sales_enabled"] = True
    settings.service["plans"]["PRO30"]["stars"] = None
    assert client.post("/bot/orders", headers=BOT, json=body).status_code == 503


def test_access_and_subscription_expiry(setup):
    client, _, clock = setup
    assert client.get("/access").json() == {"pro_active": False, "free_tweak_ids": None}
    token = login(client)
    client.post("/bot/payment", headers=BOT, json=payment(order(client)))
    assert client.get("/access", headers=token).json()["pro_active"]
    # Keep token renewed so expiry under test is the subscription, not login.
    clock[0] += 29 * DAY
    client.post("/auth/refresh", headers=token)
    clock[0] += 2 * DAY
    assert not client.get("/access", headers=token).json()["pro_active"]


def test_start_rate_limit_and_https_only(setup):
    client, settings, _ = setup
    for i in range(6):
        start(client, str(i))
    response = client.post("/auth/start", json={"hwid": "a" * 64, "name": "limit"})
    assert response.status_code == 429
    with TestClient(create_app(settings), base_url="http://api.example.test") as insecure:
        assert insecure.get("/access").status_code == 400


def test_rejects_missing_secret_bad_free_ids_and_oversized_body(setup):
    client, settings, _ = setup
    assert client.post("/auth/start", content=b"x" * 200001).status_code == 413
    settings.bot_secret = ""
    with pytest.raises(RuntimeError):
        create_app(settings)
    settings.bot_secret = SECRET
    settings.service["free_tweak_ids"] = ["missing"]
    with pytest.raises(RuntimeError):
        create_app(settings)


def test_invalid_avatar_and_unknown_input_fields(setup):
    client, _, _ = setup
    session = start(client)
    body = {"session_id": session["session_id"], "telegram_id": 123, "username": "test",
            "avatar": base64.b64encode(b"not an image").decode()}
    assert client.post("/bot/confirm-auth", headers=BOT, json=body).status_code == 400
    assert client.post("/auth/start", json={"hwid": "a" * 64, "name": "pc", "pro": True}).status_code == 422
