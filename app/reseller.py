from typing import Any

import httpx


class AutoLikeClient:
    def __init__(self, base_url: str, seller_id: str, api_key: str) -> None:
        self._base_url = (
            "https://" + base_url[len("http://"):]
            if base_url.startswith("http://mean-like.com/")
            else base_url
        )
        self._seller_id = seller_id
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(20.0, connect=10.0),
        )

    async def deliver(self, ff_uid: str, total: int) -> int:
        if not ff_uid.isdigit():
            raise ValueError("Free Fire UID must contain only digits")
        if total < 1:
            raise ValueError("A delivery must request at least one like")
        response = await self._client.get(
            self._base_url,
            params={"uid": ff_uid, "total": total, "seller": self._seller_id},
        )
        response.raise_for_status()
        try:
            payload: Any = response.json()
        except ValueError:
            raise RuntimeError("AutoLikeFF returned an invalid JSON response") from None
        if isinstance(payload, dict):
            if payload.get("success") is False or payload.get("status") in {"error", "failed"}:
                raise RuntimeError(f"AutoLikeFF rejected the delivery: {payload.get('message', 'request failed')}")
            data = payload.get("data")
            if isinstance(data, dict):
                payload = {**payload, **data}
            actual = next(
                (
                    payload[key]
                    for key in ("delivered_count", "likes_added", "added")
                    if key in payload
                ),
                None,
            )
            if actual is None:
                raise RuntimeError("AutoLikeFF response did not include the delivered like count")
            try:
                delivered = int(actual)
            except (TypeError, ValueError):
                raise RuntimeError("AutoLikeFF returned an invalid delivered like count") from None
            if delivered < 1 or delivered > total:
                raise RuntimeError("AutoLikeFF returned a like count outside the requested range")
            return delivered
        raise RuntimeError("AutoLikeFF returned an invalid response")

    async def close(self) -> None:
        await self._client.aclose()
