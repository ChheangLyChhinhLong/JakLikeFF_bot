import asyncio

import httpx

from app.reseller import AutoLikeClient


def test_auto_like_request_uses_configured_credentials_and_daily_amount():
    async def run_test():
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.scheme == "https"
            assert request.url.params["uid"] == "123456"
            assert request.url.params["total"] == "220"
            assert request.url.params["seller"] == "8916235907"
            assert request.headers["Authorization"] == "Bearer api-key"
            return httpx.Response(200, json={"success": True, "likes_added": 200})

        client = AutoLikeClient(
            "http://mean-like.com/autolikeff",
            "8916235907",
            "api-key",
        )
        headers = client._client.headers
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            headers=headers,
            transport=httpx.MockTransport(handler),
        )
        try:
            assert await client.deliver("123456", 220) == 200
        finally:
            await client.close()

    asyncio.run(run_test())


def test_auto_like_rejects_invalid_uid_and_does_not_enforce_external_daily_limit():
    async def run_test():
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["total"] == "221"
            return httpx.Response(200, json={"success": True, "likes_added": 221})

        client = AutoLikeClient("http://mean-like.com/autolikeff", "seller", "api-key")
        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            try:
                await client.deliver("abc", 220)
            except ValueError:
                pass
            else:
                raise AssertionError("Invalid UID was accepted")
            assert await client.deliver("123", 221) == 221
        finally:
            await client.close()

    asyncio.run(run_test())


def test_auto_like_requires_a_delivered_count_in_the_api_response():
    async def run_test():
        client = AutoLikeClient("https://mean-like.com/autolikeff", "seller", "api-key")
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"success": True})
            )
        )
        try:
            try:
                await client.deliver("123", 220)
            except RuntimeError as exc:
                assert "delivered like count" in str(exc)
            else:
                raise AssertionError("Missing delivery count was treated as success")
        finally:
            await client.close()

    asyncio.run(run_test())
