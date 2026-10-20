from typing import Any

from supabase import AsyncClient


def _record(data: Any) -> dict[str, Any] | None:
    if isinstance(data, list):
        data = data[0] if data else None
    return data if isinstance(data, dict) else None


class Store:
    def __init__(self, client: AsyncClient) -> None:
        self.client = client

    async def upsert_user(self, telegram_id: int, username: str | None, first_name: str) -> None:
        await self.client.table("users").upsert(
            {
                "telegram_id": telegram_id,
                "username": username,
                "first_name": first_name,
            },
            on_conflict="telegram_id",
        ).execute()

    async def create_order(
        self,
        telegram_id: int,
        ff_uid: str,
        total_likes: int,
        amount: str,
    ) -> dict[str, Any]:
        result = await (
            self.client.table("orders")
            .insert(
                {
                    "telegram_id": telegram_id,
                    "ff_uid": ff_uid,
                    "total_likes": total_likes,
                    "amount": amount,
                    "status": "pending",
                }
            )
            .select("id,telegram_id,ff_uid,total_likes,amount,status,created_at")
            .single()
            .execute()
        )
        order = _record(result.data)
        if order is None:
            raise RuntimeError("Supabase did not return the created order")
        return order

    async def set_order_payment(self, order_id: int, transaction_id: str) -> None:
        result = await (
            self.client.table("orders")
            .update({"transaction_id": transaction_id})
            .eq("id", order_id)
            .eq("status", "pending")
            .select("id")
            .maybe_single()
            .execute()
        )
        if _record(result.data) is None:
            raise RuntimeError("Order is no longer pending")

    async def set_payment_message(self, order_id: int, message_id: int) -> str | None:
        result = await (
            self.client.table("orders")
            .update({"message_id": message_id})
            .eq("id", order_id)
            .select("status")
            .maybe_single()
            .execute()
        )
        row = _record(result.data)
        return str(row["status"]) if row else None

    async def clear_payment_message(self, order_id: int) -> None:
        await self.client.table("orders").update({"message_id": None}).eq("id", order_id).execute()

    async def order(self, order_id: int) -> dict[str, Any] | None:
        result = await (
            self.client.table("orders")
            .select(
                "id,transaction_id,telegram_id,ff_uid,total_likes,amount,sent_likes,status,"
                "message_id,last_run_date,created_at"
            )
            .eq("id", order_id)
            .maybe_single()
            .execute()
        )
        return _record(result.data)

    async def order_by_transaction(self, transaction_id: str) -> dict[str, Any] | None:
        result = await (
            self.client.table("orders")
            .select(
                "id,transaction_id,telegram_id,ff_uid,total_likes,amount,sent_likes,status,"
                "message_id,last_run_date,created_at"
            )
            .eq("transaction_id", transaction_id)
            .maybe_single()
            .execute()
        )
        return _record(result.data)

    async def pending_orders(self) -> list[dict[str, Any]]:
        result = await (
            self.client.table("orders")
            .select("id,transaction_id,telegram_id,amount,status,created_at")
            .eq("status", "pending")
            .not_.is_("transaction_id", "null")
            .order("created_at")
            .limit(100)
            .execute()
        )
        return result.data or []

    async def order_history(self, telegram_id: int) -> list[dict[str, Any]]:
        result = await (
            self.client.table("orders")
            .select("id,ff_uid,total_likes,amount,sent_likes,status,created_at")
            .eq("telegram_id", telegram_id)
            .order("created_at", desc=True)
            .limit(10)
            .execute()
        )
        return result.data or []

    async def package_prices(self, active_only: bool = True) -> list[dict[str, Any]]:
        query = self.client.table("package_prices").select(
            "package_likes,price_usd,is_active"
        )
        if active_only:
            query = query.eq("is_active", True)
        result = await query.order("package_likes").execute()
        return result.data or []

    async def update_package_price(self, package_likes: int, price_usd: str) -> None:
        result = await (
            self.client.table("package_prices")
            .update({"price_usd": price_usd})
            .eq("package_likes", package_likes)
            .eq("is_active", True)
            .select("package_likes")
            .maybe_single()
            .execute()
        )
        if _record(result.data) is None:
            raise RuntimeError("Package is unavailable or its price was not updated")

    async def active_user_ids(
        self, after_telegram_id: int = 0, limit: int = 500
    ) -> list[int]:
        result = await (
            self.client.table("users")
            .select("telegram_id")
            .eq("is_blocked", False)
            .gt("telegram_id", after_telegram_id)
            .order("telegram_id")
            .limit(limit)
            .execute()
        )
        return [int(row["telegram_id"]) for row in result.data or []]

    async def mark_user_blocked(self, telegram_id: int) -> None:
        await (
            self.client.table("users")
            .update({"is_blocked": True})
            .eq("telegram_id", telegram_id)
            .execute()
        )

    async def system_stats(self) -> dict[str, Any]:
        users = await (
            self.client.table("users")
            .select("telegram_id", count="exact", head=True)
            .execute()
        )
        orders = await self.client.rpc("system_stats").execute()
        stats = _record(orders.data)
        if stats is None or users.count is None:
            raise RuntimeError("Supabase did not return system statistics")
        stats["total_users"] = int(users.count)
        return stats

    async def activate_order(self, order_id: int) -> bool:
        result = await (
            self.client.table("orders")
            .update({"status": "active"})
            .eq("id", order_id)
            .eq("status", "pending")
            .select("id")
            .execute()
        )
        return bool(result.data)

    async def close_pending_order(self, order_id: int, status: str) -> bool:
        if status != "failed":
            raise ValueError("Invalid terminal order status")
        result = await (
            self.client.table("orders")
            .update({"status": status})
            .eq("id", order_id)
            .eq("status", "pending")
            .select("id")
            .execute()
        )
        return bool(result.data)

    async def claim_due_order(self, order_id: int | None = None) -> dict[str, Any] | None:
        result = await self.client.rpc("claim_due_order", {"p_order_id": order_id}).execute()
        return _record(result.data)

    async def record_delivery(self, order_id: int, delivered_count: int) -> dict[str, Any]:
        result = await self.client.rpc(
            "record_like_delivery",
            {"p_order_id": order_id, "p_delivered_count": delivered_count},
        ).execute()
        row = _record(result.data)
        if row is None:
            raise RuntimeError("Supabase did not return the delivery update")
        return row

    async def release_delivery_claim(self, order_id: int) -> None:
        await self.client.rpc("release_delivery_claim", {"p_order_id": order_id}).execute()
