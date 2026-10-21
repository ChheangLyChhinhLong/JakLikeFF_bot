import asyncio
import json
import logging
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urlsplit

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from supabase import acreate_client

from app.bot import delete_payment_qr, process_paid_order, reconcile_payments, register_handlers
from app.config import get_settings
from app.payments import KHPayClient, verify_webhook_signature
from app.reseller import AutoLikeClient
from app.store import Store

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
settings = get_settings()
bot = Bot(settings.bot_token)
dispatcher = Dispatcher()
payments = KHPayClient(settings.khpay_api_key, settings.khpay_base_url, settings.khpay_webhook_url)
reseller = AutoLikeClient(settings.reseller_api_url, settings.reseller_seller_id, settings.reseller_api_key)


@asynccontextmanager
async def lifespan(_: FastAPI):
    missing = [
        name
        for name, value in (
            ("ADMIN_GROUP_ID", settings.admin_chat_id),
            ("TELEGRAM_ADMIN_IDS", settings.admin_ids),
            ("RESELLER_API_KEY", settings.reseller_api_key.strip()),
            ("KHPAY_WEBHOOK_SECRET", settings.khpay_webhook_secret.strip()),
            ("KHPAY_WEBHOOK_URL", settings.khpay_webhook_url.strip()),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"Required configuration is missing: {', '.join(missing)}")
    webhook_url = urlsplit(settings.khpay_webhook_url)
    if (
        webhook_url.scheme != "https"
        or not webhook_url.netloc
        or webhook_url.path.rstrip("/") != "/webhook/khpay"
    ):
        raise RuntimeError("KHPAY_WEBHOOK_URL must be the public HTTPS /webhook/khpay endpoint")

    supabase = await acreate_client(settings.supabase_url, settings.supabase_service_role_key)
    store = Store(supabase)
    app.state.store = store
    await bot.delete_my_commands()
    register_handlers(dispatcher, bot, store, payments, reseller, settings)
    polling_task = asyncio.create_task(dispatcher.start_polling(bot))
    reconcile_task = asyncio.create_task(
        reconcile_payments(bot, store, payments, reseller, settings)
    )
    try:
        yield
    finally:
        polling_task.cancel()
        reconcile_task.cancel()
        await asyncio.gather(polling_task, reconcile_task, return_exceptions=True)
        await bot.session.close()
        await payments.close()
        await reseller.close()


app = FastAPI(lifespan=lifespan)


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
@app.api_route("/health", methods=["GET", "HEAD"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/pay/{transaction_id}")
async def payment_proxy(transaction_id: str) -> RedirectResponse:
    target_url = f"https://khpay.site/pay/{quote(transaction_id, safe='')}"
    return RedirectResponse(url=target_url, status_code=307)


@app.post("/webhook/khpay")
async def khpay_webhook(request: Request) -> dict[str, bool | str]:
    if not settings.khpay_webhook_secret:
        raise HTTPException(status_code=503, detail="KHPAY webhook secret is not configured")

    raw_body = await request.body()
    signature = request.headers.get("x-webhook-signature", "") or request.headers.get(
        "x-khpay-signature", ""
    )
    if not verify_webhook_signature(raw_body, signature, settings.khpay_webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid webhook payload")
    if payload.get("event") == "webhook.test":
        return {"received": True, "status": "test"}

    event_data = payload.get("data")
    if not isinstance(event_data, dict):
        event_data = payload
    event_status = {
        "payment.paid": "paid",
        "payment.expired": "failed",
        "payment.failed": "failed",
    }.get(payload.get("event"))
    status_value = event_status or event_data.get("status") or payload.get("status")
    status = str(status_value).lower()
    if status not in {"paid", "expired", "failed"}:
        return {"received": True, "status": "ignored"}
    if status == "expired":
        status = "failed"
    transaction_id = event_data.get("transaction_id") or payload.get("transaction_id")
    if not isinstance(transaction_id, str) or not transaction_id:
        raise HTTPException(status_code=400, detail="Missing transaction_id")

    store: Store = request.app.state.store
    order = await store.order_by_transaction(transaction_id)
    if not order:
        return {"received": True, "status": "unknown_transaction"}
    if order["status"] not in {"pending", "active", "completed"}:
        return {"received": True, "status": "order_already_closed"}
    if status != "paid" and order["status"] != "pending":
        return {"received": True, "status": "order_already_closed"}

    if status == "paid":
        try:
            payment = await payments.check_payment(transaction_id)
        except Exception:
            logger.exception("KHPAY status verification failed for %s", transaction_id)
            raise HTTPException(status_code=503, detail="Could not verify payment with KHPAY") from None
        if str(payment.get("status", "")).lower() != "paid":
            return {"received": True, "status": "awaiting_api_confirmation"}
        try:
            expected_amount = Decimal(str(order["amount"])).quantize(Decimal("0.01"))
            confirmed_amount = Decimal(str(payment["amount"])).quantize(Decimal("0.01"))
            webhook_amount = event_data.get("amount")
            if confirmed_amount != expected_amount or (
                webhook_amount is not None
                and Decimal(str(webhook_amount)).quantize(Decimal("0.01")) != expected_amount
            ):
                raise HTTPException(status_code=400, detail="Payment amount does not match the order")
        except (InvalidOperation, KeyError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid payment amount") from None
        try:
            await process_paid_order(
                bot, store, reseller, settings, int(order["id"])
            )
        except Exception:
            logger.exception("Could not activate paid AutoLike order %s", order["id"])
            raise HTTPException(status_code=503, detail="Payment confirmed; delivery will be retried") from None
        return {"received": True, "status": "paid"}

    if not await store.close_pending_order(int(order["id"]), status):
        return {"received": True, "status": "order_already_closed"}
    await delete_payment_qr(bot, store, order)
    try:
        await bot.send_message(
            int(order["telegram_id"]),
            "⌛ ការទូទាត់មិនបានបញ្ចប់។ សូមបង្កើតការបញ្ជាទិញថ្មី។",
        )
    except TelegramAPIError:
        logger.exception("Could not notify user of failed payment for order %s", order["id"])
    return {"received": True, "status": status}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=settings.port)
