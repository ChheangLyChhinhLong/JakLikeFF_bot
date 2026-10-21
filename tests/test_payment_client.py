import asyncio
from unittest.mock import AsyncMock

import httpx

from app.payments import KHPayClient, aba_mobile_deeplink


def test_khpay_client_uses_the_configured_bearer_token():
    async def run_test():
        client = KHPayClient("known-api-key", "https://khpay.site/api/v1")
        try:
            assert client._client.headers["Authorization"] == "Bearer known-api-key"
        finally:
            await client.close()

    asyncio.run(run_test())


def test_qr_payment_uses_qr_endpoint():
    async def run_test():
        client = KHPayClient("api-key", "https://khpay.site/api/v1")
        request = httpx.Request("POST", "https://khpay.site/api/v1/qr/generate")
        response = httpx.Response(
            200,
            json={
                "success": True,
                "data": {"transaction_id": "txn-456", "qr_string": "KHQR+payload/123", "md5": "md5-456"},
            },
            request=request,
        )
        post = AsyncMock(return_value=response)
        client._client.post = post
        try:
            payment = await client.create_payment("10.00", "order-456", 789)
            assert post.await_args.args[0] == "/qr/generate"
            assert post.await_args.kwargs["json"]["currency"] == "USD"
            assert post.await_args.kwargs["headers"]["Idempotency-Key"] == (
                "jaklikeff-order-order-456"
            )
            assert client._client.headers["Authorization"] == "Bearer api-key"
            assert payment["qr_string"] == "KHQR+payload/123"
            assert payment["md5"] == "md5-456"
        finally:
            await client.close()

    asyncio.run(run_test())


def test_qr_payment_accepts_documented_response_without_md5():
    async def run_test():
        client = KHPayClient("api-key", "https://khpay.site/api/v1")
        request = httpx.Request("POST", "https://khpay.site/api/v1/qr/generate")
        response = httpx.Response(
            200,
            json={
                "success": True,
                "data": {"transaction_id": "txn-789", "qr_string": "KHQR+payload/789"},
            },
            request=request,
        )
        client._client.post = AsyncMock(return_value=response)
        try:
            payment = await client.create_payment("0.30", "order-789", 123)
            assert payment == {"transaction_id": "txn-789", "qr_string": "KHQR+payload/789"}
        finally:
            await client.close()

    asyncio.run(run_test())


def test_qr_payment_error_includes_gateway_status_and_request_id():
    async def run_test():
        client = KHPayClient("api-key", "https://khpay.site/api/v1")
        request = httpx.Request("POST", "https://khpay.site/api/v1/qr/generate")
        response = httpx.Response(
            409,
            json={
                "success": False,
                "error": "Conflict",
                "error_code": "duplicate_request",
                "request_id": "req-123",
            },
            request=request,
        )
        client._client.post = AsyncMock(return_value=response)
        try:
            try:
                await client.create_payment("0.50", "order-409", 123)
            except RuntimeError as exc:
                assert str(exc) == (
                    "KHPAY error: Conflict "
                    "(HTTP 409, duplicate_request, request_id=req-123)"
                )
            else:
                raise AssertionError("Expected the KHPAY conflict to be reported")
        finally:
            await client.close()

    asyncio.run(run_test())


def test_aba_mobile_deeplink_encodes_raw_qr_string():
    assert aba_mobile_deeplink("KHQR+payload/123=") == (
        "abamobilebank://ababank.com?type=payway&qrcode=KHQR%2Bpayload%2F123%3D"
    )
