import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app import main


def test_paid_status_webhook_confirms_and_delivers_order(monkeypatch):
    secret = "webhook-secret"
    body = json.dumps(
        {"status": "paid", "transaction_id": "txn-123", "amount": "5.00"}
    ).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    store = SimpleNamespace(
        order_by_transaction=AsyncMock(
            return_value={
                "id": 123,
                "telegram_id": 456,
                "amount": "5.00",
                "status": "pending",
            }
        )
    )
    monkeypatch.setattr(main.settings, "khpay_webhook_secret", secret)
    monkeypatch.setattr(main.app.state, "store", store, raising=False)
    monkeypatch.setattr(
        main.payments,
        "check_payment",
        AsyncMock(return_value={"status": "paid", "amount": "5.00"}),
    )
    process_paid_order = AsyncMock()
    monkeypatch.setattr(main, "process_paid_order", process_paid_order)

    response = TestClient(main.app).post(
        "/webhook/khpay",
        content=body,
        headers={"x-webhook-signature": signature},
    )

    assert response.status_code == 200
    assert response.json() == {"received": True, "status": "paid"}
    process_paid_order.assert_awaited_once_with(
        main.bot, store, main.reseller, main.settings, 123
    )


def test_payment_proxy_redirects_to_fixed_khpay_checkout():
    response = TestClient(main.app).get("/pay/txn-123", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "https://khpay.site/pay/txn-123"


def test_payment_proxy_encodes_transaction_id_in_redirect_path():
    response = TestClient(main.app).get("/pay/txn%3F123", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "https://khpay.site/pay/txn%3F123"


def test_expired_webhook_deletes_qr_before_closing_order_and_notifying(monkeypatch):
    events = []
    secret = "webhook-secret"
    body = json.dumps(
        {"event": "payment.expired", "data": {"transaction_id": "txn-expired"}}
    ).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    store = SimpleNamespace(
        order_by_transaction=AsyncMock(
            return_value={
                "id": 124,
                "telegram_id": 456,
                "status": "pending",
                "message_id": 789,
            }
        ),
        clear_payment_message=AsyncMock(side_effect=lambda _: events.append("clear")),
        close_pending_order=AsyncMock(
            side_effect=lambda *_: (events.append("close"), True)[1]
        ),
    )
    bot = SimpleNamespace(
        delete_message=AsyncMock(side_effect=lambda **_: events.append("delete")),
        send_message=AsyncMock(side_effect=lambda *_: events.append("notify")),
    )
    monkeypatch.setattr(main.settings, "khpay_webhook_secret", secret)
    monkeypatch.setattr(main.app.state, "store", store, raising=False)
    monkeypatch.setattr(main, "bot", bot)

    response = TestClient(main.app).post(
        "/webhook/khpay",
        content=body,
        headers={"x-webhook-signature": signature},
    )

    assert response.status_code == 200
    assert events == ["delete", "clear", "close", "notify"]
    bot.delete_message.assert_awaited_once_with(chat_id=456, message_id=789)
    assert "ផុតកំណត់" in bot.send_message.await_args.args[1]