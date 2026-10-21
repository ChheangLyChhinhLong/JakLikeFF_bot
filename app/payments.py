import hashlib
import hmac
from typing import Any
from urllib.parse import quote

import httpx


def aba_mobile_deeplink(qr_string: str) -> str:
    if not qr_string.strip():
        raise ValueError("QR string must not be empty")
    return f"abamobilebank://ababank.com?type=payway&qrcode={quote(qr_string, safe='')}"


def verify_webhook_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    if not signature or not secret:
        return False
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    expected = f"sha256={digest}" if signature.startswith("sha256=") else digest
    return hmac.compare_digest(expected, signature)


class KHPayClient:
    def __init__(self, api_key: str, base_url: str, webhook_url: str = "") -> None:
        self._webhook_url = webhook_url
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=20.0,
        )

    async def create_payment(
        self,
        amount: str,
        order_id: str,
        telegram_id: int,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "amount": amount,
            "currency": "USD",
            "note": f"Telegram order {order_id}",
            "metadata": {"telegram_id": telegram_id, "order_id": order_id},
        }
        if self._webhook_url:
            body["callback_url"] = self._webhook_url
        response = await self._client.post(
            "/qr/generate",
            headers={"Idempotency-Key": order_id},
            json=body,
        )
        try:
            payload = response.json()
        except ValueError:
            raise RuntimeError(f"KHPAY returned HTTP {response.status_code} with an invalid JSON response") from None
        if not isinstance(payload, dict):
            raise RuntimeError(f"KHPAY returned HTTP {response.status_code} with an invalid response")
        if response.is_error or not payload.get("success"):
            error = payload.get("error") or payload.get("message") or f"HTTP {response.status_code}"
            error_code = payload.get("error_code")
            request_id = payload.get("request_id")
            context = ", ".join(value for value in (error_code, f"request_id={request_id}" if request_id else None) if value)
            raise RuntimeError(f"KHPAY error: {error}" + (f" ({context})" if context else ""))
        data = payload.get("data", {})
        if not isinstance(data, dict):
            raise RuntimeError("KHPAY response is missing payment data")
        if not data.get("transaction_id"):
            raise RuntimeError("KHPAY response is missing transaction_id")
        if not data.get("qr_string"):
            raise RuntimeError("KHPAY response is missing qr_string")
        return data

    async def check_payment(self, transaction_id: str) -> dict[str, Any]:
        response = await self._client.get(f"/qr/check/{transaction_id}")
        try:
            payload = response.json()
        except ValueError:
            raise RuntimeError(f"KHPAY returned HTTP {response.status_code} with an invalid JSON response") from None
        if not isinstance(payload, dict):
            raise RuntimeError("KHPAY returned an invalid payment status response")
        if response.is_error or not payload.get("success"):
            raise RuntimeError(payload.get("error", f"KHPAY returned HTTP {response.status_code}"))
        data = payload.get("data", {})
        if not isinstance(data, dict):
            raise RuntimeError("KHPAY response is missing payment status data")
        return data

    async def close(self) -> None:
        await self._client.aclose()