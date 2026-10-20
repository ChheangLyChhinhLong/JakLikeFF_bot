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