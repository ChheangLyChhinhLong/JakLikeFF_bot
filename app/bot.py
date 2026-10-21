import asyncio
import html
import io
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import qrcode
from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.config import Settings
from app.payments import KHPayClient, aba_mobile_deeplink
from app.reseller import AutoLikeClient
from app.store import Store

logger = logging.getLogger(__name__)
router = Router()


@dataclass(frozen=True)
class LikePackage:
    likes: int
    amount: Decimal


PACKAGES = (
    LikePackage(220, Decimal("0.20")),
    LikePackage(1_000, Decimal("0.50")),
    LikePackage(2_000, Decimal("1.00")),
    LikePackage(3_000, Decimal("1.50")),
    LikePackage(4_000, Decimal("2.00")),
    LikePackage(5_000, Decimal("2.50")),
    LikePackage(6_000, Decimal("2.75")),
    LikePackage(7_000, Decimal("3.25")),
    LikePackage(8_000, Decimal("3.75")),
    LikePackage(9_000, Decimal("4.25")),
    LikePackage(10_000, Decimal("4.50")),
    LikePackage(20_000, Decimal("8.50")),
    LikePackage(30_000, Decimal("12.00")),
    LikePackage(50_000, Decimal("19.00")),
)

conversation_state: dict[int, str] = {}
pending_uids: dict[int, str] = {}
admin_state: dict[int, str] = {}
pending_price_packages: dict[int, int] = {}
broadcast_jobs: set[asyncio.Task[None]] = set()
DAILY_BATCH_FALLBACK = 220
AUTOLIKE_TUTORIAL_VIDEO_SETTING = "autolike_tutorial_video_url"


def keyboard(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_menu() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text="🔙 ត្រឡប់ទៅម៉ឺនុយ", callback_data="home")]


def guide_keyboard(video_url: str | None) -> InlineKeyboardMarkup:
    rows = []
    if video_url:
        rows.append([InlineKeyboardButton(text="▶️ មើលវីដេអូរបៀបទិញ", url=video_url)])
    rows.append(back_to_menu())
    return keyboard(rows)


def menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🚀 ទិញមេដៃ Free Fire", callback_data="buy")],
        [InlineKeyboardButton(text="📊 ពិនិត្យប្រវត្តិ · Status", callback_data="history")],
        [InlineKeyboardButton(text="🎥 របៀបទិញ", callback_data="guide")],
        [InlineKeyboardButton(text="💬 ជំនួយ", callback_data="help")],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton(text="🛠 Admin Panel", callback_data="admin:home")])
    return keyboard(rows)


def welcome_text(user: Any) -> str:
    first_name = html.escape(str(user.first_name or "Player"))
    username = f"@{html.escape(user.username)}" if user.username else "មិនមាន"
    return (
        f"🙏🏻 <b>សូមស្វាគមន៍ {first_name}!</b>\n\n"
        "<b>ព័ត៌មានអ្នកប្រើប្រាស់</b>\n"
        f"├─ 🆔 <b>ID:</b> <code>{user.id}</code>\n"
        f"└─ 👤 <b>ឈ្មោះ:</b> {username}\n\n"
        "💎 <b>Free Fire AutoLike</b>\n"
        "សូមជ្រើសរើសសេវាកម្មខាងក្រោម៖"
    )


def package_keyboard(packages: tuple[LikePackage, ...] | list[LikePackage] = PACKAGES) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"👍 {package.likes:,} Likes • ${package.amount:.2f}",
                callback_data=f"package:{package.likes}",
            )
        ]
        for package in packages
    ]
    rows.append(back_to_menu())
    return keyboard(rows)


def _progress_bar(sent_likes: int, total_likes: int) -> str:
    if total_likes <= 0:
        raise ValueError("The total number of likes must be positive")
    sent = min(total_likes, max(0, sent_likes))
    percentage = sent * 100 // total_likes
    filled = percentage * 10 // 100
    bar = "█" * filled + "░" * (10 - filled)
    return f"[{bar}] {sent:,}/{total_likes:,} ({percentage}%)"


def _package_rows(rows: list[dict[str, Any]]) -> list[LikePackage]:
    return [
        LikePackage(int(row["package_likes"]), Decimal(str(row["price_usd"])))
        for row in rows
    ]


def _price_keyboard(rows: list[dict[str, Any]]) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=f"{int(row['package_likes']):,} Likes · ${Decimal(str(row['price_usd'])):.2f}",
                callback_data=f"admin:price:{int(row['package_likes'])}",
            )
        ]
        for row in rows
    ]
    buttons.append([InlineKeyboardButton(text="🔙 Admin Panel", callback_data="admin:home")])
    return keyboard(buttons)


def payment_caption(order: dict[str, Any], transaction_id: str) -> str:
    amount = Decimal(str(order["amount"]))
    return (
        f"💳 <b>Pay ${amount:.2f}</b>\n\n"
        f"🎮 <b>Game UID:</b> <code>{html.escape(str(order['ff_uid']))}</code>\n"
        f"👍 <b>Package:</b> {int(order['total_likes']):,} Likes\n"
        f"💰 <b>Total Amount:</b> ${amount:.2f}\n"
        f"🟢 <b>Remaining to pay:</b> ${amount:.2f}\n"
        f"🏷 <b>Ref:</b> <code>{html.escape(transaction_id)}</code>\n"
        "⏱ <b>Scan KHQR to complete payment.</b>\n"
        "⏰ <b>You have 15 minutes to pay. The QR refreshes itself.</b>"
    )


def payment_keyboard(order_id: int, qr_string: str) -> InlineKeyboardMarkup:
    return keyboard(
        [
            [
                InlineKeyboardButton(
                    text="🏦 បើកក្នុង ABA Mobile",
                    url=aba_mobile_deeplink(qr_string),
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ ខ្ញុំបានទូទាត់ · ពិនិត្យ",
                    callback_data=f"check:{order_id}",
                )
            ],
        ]
    )


def _make_qr(qr_string: str) -> BufferedInputFile:
    image = qrcode.make(qr_string)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return BufferedInputFile(buffer.getvalue(), filename="payment-qr.png")


async def send_welcome(message: Message, store: Store, settings: Settings) -> None:
    user = message.from_user
    if user is None:
        return
    await store.upsert_user(user.id, user.username, user.first_name)
    conversation_state.pop(user.id, None)
    pending_uids.pop(user.id, None)
    is_admin = user.id in settings.admin_ids
    await message.answer(
        welcome_text(user),
        parse_mode="HTML",
        reply_markup=menu(is_admin=is_admin),
    )


def _admin_panel_keyboard() -> InlineKeyboardMarkup:
    return keyboard(
        [
            [InlineKeyboardButton(text="📢 Broadcast", callback_data="admin:broadcast")],
            [InlineKeyboardButton(text="✏️ Edit package prices", callback_data="admin:prices")],
            [InlineKeyboardButton(text="📈 System stats", callback_data="admin:stats")],
            [InlineKeyboardButton(text="🔙 Main menu", callback_data="home")],
        ]
    )


async def _broadcast_to_active_users(
    bot: Bot, store: Store, source: Message, admin_id: int
) -> None:
    sent = 0
    blocked = 0
    errors = 0
    after_id = 0
    while True:
        recipients = await store.active_user_ids(after_id)
        if not recipients:
            break
        for telegram_id in recipients:
            after_id = telegram_id
            try:
                await source.copy_to(chat_id=telegram_id)
                sent += 1
            except TelegramForbiddenError:
                await store.mark_user_blocked(telegram_id)
                blocked += 1
            except TelegramAPIError:
                errors += 1
                logger.exception("Broadcast delivery failed for user %s", telegram_id)
            await asyncio.sleep(0.05)
        if len(recipients) < 500:
            break
    try:
        await bot.send_message(
            admin_id,
            f"✅ Broadcast complete.\nDelivered: {sent}\nBlocked users: {blocked}\nFailed: {errors}",
        )
    except TelegramAPIError:
        logger.exception("Could not send broadcast summary to admin %s", admin_id)


async def _run_broadcast(bot: Bot, store: Store, source: Message, admin_id: int) -> None:
    try:
        await _broadcast_to_active_users(bot, store, source, admin_id)
    except Exception:
        logger.exception("Broadcast job failed for admin %s", admin_id)
        try:
            await bot.send_message(admin_id, "❌ Broadcast stopped because of a database or service error.")
        except TelegramAPIError:
            logger.exception("Could not report broadcast failure to admin %s", admin_id)


def _broadcast_finished(task: asyncio.Task[None]) -> None:
    broadcast_jobs.discard(task)


async def delete_payment_qr(bot: Bot, store: Store, order: dict[str, Any]) -> None:
    message_id = order.get("message_id")
    if not message_id:
        return
    telegram_id = int(order["telegram_id"])
    try:
        await bot.delete_message(chat_id=telegram_id, message_id=int(message_id))
    except TelegramBadRequest:
        logger.info("Payment QR message %s for order %s was already unavailable", message_id, order["id"])
    except TelegramAPIError:
        logger.exception("Could not delete payment QR message for order %s", order["id"])
        return
    await store.clear_payment_message(int(order["id"]))


def _progress_text(order: dict[str, Any], delivered_today: int, sent_likes: int, complete: bool) -> str:
    total = int(order["total_likes"])
    percentage = min(100, sent_likes * 100 // total)
    status = "Completed" if complete else f"In Progress ({percentage}%)"
    order_line = (
        f"🧾 <b>Order:</b> <code>#{int(order['id'])}</code>\n"
        if order.get("id") is not None
        else ""
    )
    return (
        "<code>💎 AutoLikeFF Daily Update</code>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"{order_line}"
        f"🆔 <b>Game UID:</b> <code>{html.escape(str(order['ff_uid']))}</code>\n"
        f"👍 <b>Package:</b> {total:,} Likes\n"
        f"➕ <b>Likes Added Today:</b> +{delivered_today}\n"
        f"📊 <b>Progress:</b> {_progress_bar(sent_likes, total)}\n"
        f"⏳ <b>Status:</b> {status}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        + (
            "<i>ការបញ្ជាទិញរបស់អ្នកបានបញ្ចប់ហើយ។ សូមអរគុណ!</i>"
            if complete
            else "<i>ប្រព័ន្ធនឹងបន្តបញ្ចូល ២២០ មេដៃនៅថ្ងៃស្អែកទៀត!</i>"
        )
    )


async def _notify_delivery(
    bot: Bot,
    settings: Settings,
    order: dict[str, Any],
    delivered_today: int,
    updated: dict[str, Any],
) -> None:
    sent = int(updated["sent_likes"])
    complete = updated["status"] == "completed"
    text = _progress_text(order, delivered_today, sent, complete)
    try:
        await bot.send_message(int(order["telegram_id"]), text, parse_mode="HTML")
    except TelegramAPIError:
        logger.exception("Could not send delivery progress to user %s", order["telegram_id"])
    admin_chat_id = settings.admin_chat_id
    if admin_chat_id is not None:
        try:
            await bot.send_message(admin_chat_id, text, parse_mode="HTML")
        except TelegramAPIError:
            logger.exception("Could not send delivery progress for order %s to the admin group", order["id"])
    else:
        logger.error("Delivery progress for order %s was not logged: ADMIN_GROUP_ID is not configured", order["id"])


async def process_daily_delivery(
    bot: Bot,
    store: Store,
    reseller: AutoLikeClient,
    settings: Settings,
    order_id: int | None = None,
) -> bool:
    order = await store.claim_due_order(order_id)
    if order is None:
        return False
    claimed_order_id = int(order["id"])
    try:
        remaining = int(order["total_likes"]) - int(order["sent_likes"])
        batch_size = int(getattr(settings, "reseller_daily_batch_likes", DAILY_BATCH_FALLBACK))
        if batch_size < 1:
            raise ValueError("RESELLER_DAILY_BATCH_LIKES must be positive")
        requested = min(batch_size, remaining)
        delivered = await reseller.deliver(str(order["ff_uid"]), requested)
        updated = await store.record_delivery(claimed_order_id, delivered)
    except Exception:
        await store.release_delivery_claim(claimed_order_id)
        raise
    await _notify_delivery(bot, settings, order, delivered, updated)
    return True


async def process_paid_order(
    bot: Bot,
    store: Store,
    reseller: AutoLikeClient,
    settings: Settings,
    order_id: int,
) -> None:
    order = await store.order(order_id)
    if order is None:
        raise RuntimeError(f"Paid KHPAY notification refers to missing order {order_id}")
    await delete_payment_qr(bot, store, order)
    if order["status"] == "pending":
        await store.activate_order(order_id)
        order = await store.order(order_id)
    if order and order["status"] == "active":
        await process_daily_delivery(bot, store, reseller, settings, order_id)


async def _expire_order(bot: Bot, store: Store, order: dict[str, Any]) -> None:
    if not await store.close_pending_order(int(order["id"]), "failed"):
        return
    await delete_payment_qr(bot, store, order)
    try:
        await bot.send_message(
            int(order["telegram_id"]),
            "⌛ ការទូទាត់ផុតកំណត់ហើយ។ សូមបង្កើតការបញ្ជាទិញថ្មី។",
        )
    except TelegramAPIError:
        logger.exception("Could not notify user of expired order %s", order["id"])


async def reconcile_payments(
    bot: Bot,
    store: Store,
    payments: KHPayClient,
    reseller: AutoLikeClient,
    settings: Settings,
) -> None:
    while True:
        try:
            now = datetime.now(timezone.utc)
            for order in await store.pending_orders():
                created_at = datetime.fromisoformat(str(order["created_at"]).replace("Z", "+00:00"))
                if (now - created_at).total_seconds() >= 15 * 60:
                    await _expire_order(bot, store, order)
                    continue
                try:
                    payment = await payments.check_payment(str(order["transaction_id"]))
                    if str(payment.get("status", "")).lower() == "paid":
                        expected = Decimal(str(order["amount"])).quantize(Decimal("0.01"))
                        confirmed = Decimal(str(payment["amount"])).quantize(Decimal("0.01"))
                        if expected != confirmed:
                            logger.error("KHPAY amount mismatch for order %s", order["id"])
                            continue
                        await process_paid_order(
                            bot, store, reseller, settings, int(order["id"])
                        )
                    elif str(payment.get("status", "")).lower() in {"expired", "failed"}:
                        await _expire_order(bot, store, order)
                except Exception:
                    logger.exception("Payment reconciliation failed for order %s", order["id"])
            while await process_daily_delivery(bot, store, reseller, settings):
                pass
        except Exception:
            logger.exception("Payment and daily delivery reconciliation failed")
        await asyncio.sleep(max(5, settings.poll_interval_seconds))


def register_handlers(
    dispatcher: Dispatcher,
    bot: Bot,
    store: Store,
    payments: KHPayClient,
    reseller: AutoLikeClient,
    settings: Settings,
) -> None:
    @router.callback_query(F.data == "home")
    async def home(callback: CallbackQuery) -> None:
        await callback.answer()
        conversation_state.pop(callback.from_user.id, None)
        pending_uids.pop(callback.from_user.id, None)
        admin_state.pop(callback.from_user.id, None)
        pending_price_packages.pop(callback.from_user.id, None)
        await store.upsert_user(
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.first_name,
        )
        await callback.message.edit_text(
            welcome_text(callback.from_user),
            parse_mode="HTML",
            reply_markup=menu(callback.from_user.id in settings.admin_ids),
        )

    @router.callback_query(F.data.startswith("admin:"))
    async def admin_actions(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id
        if user_id not in settings.admin_ids:
            await callback.answer("Access denied.", show_alert=True)
            return

        action = (callback.data or "").split(":")
        if len(action) == 2 and action[1] == "home":
            admin_state.pop(user_id, None)
            pending_price_packages.pop(user_id, None)
            await callback.answer()
            await callback.message.answer(
                "🛠 <b>Admin Panel</b>",
                parse_mode="HTML",
                reply_markup=_admin_panel_keyboard(),
            )
            return
        if len(action) == 2 and action[1] == "broadcast":
            conversation_state.pop(user_id, None)
            admin_state[user_id] = "broadcast"
            await callback.answer()
            await callback.message.answer(
                "📢 Send the announcement as text, a photo with caption, or a video with caption. "
                "Send it here to broadcast, or tap Cancel.",
                reply_markup=keyboard(
                    [[InlineKeyboardButton(text="Cancel", callback_data="admin:home")]]
                ),
            )
            return
        if len(action) == 2 and action[1] == "prices":
            admin_state.pop(user_id, None)
            pending_price_packages.pop(user_id, None)
            await callback.answer()
            prices = await store.package_prices()
            await callback.message.answer(
                "✏️ Select a package to edit its USD price:",
                reply_markup=_price_keyboard(prices),
            )
            return
        if len(action) == 3 and action[1] == "price" and action[2].isdecimal():
            likes = int(action[2])
            prices = await store.package_prices()
            if not any(int(row["package_likes"]) == likes for row in prices):
                await callback.answer("Package not found.", show_alert=True)
                return
            conversation_state.pop(user_id, None)
            admin_state[user_id] = "price"
            pending_price_packages[user_id] = likes
            await callback.answer()
            await callback.message.answer(
                f"Enter the new USD price for {likes:,} Likes (e.g. 1.25):",
                reply_markup=keyboard(
                    [[InlineKeyboardButton(text="Cancel", callback_data="admin:home")]]
                ),
            )
            return
        if len(action) == 2 and action[1] == "stats":
            await callback.answer()
            stats = await store.system_stats()
            await callback.message.answer(
                "📈 <b>System statistics</b>\n"
                f"👥 Total users: {int(stats['total_users']):,}\n"
                f"🧾 Total orders: {int(stats['total_orders']):,}\n"
                f"💵 Total revenue: ${Decimal(str(stats['total_revenue'])):.2f}",
                parse_mode="HTML",
                reply_markup=_admin_panel_keyboard(),
            )
            return
        await callback.answer("Unknown admin action.", show_alert=True)

    @router.callback_query(F.data == "buy")
    async def start_order(callback: CallbackQuery) -> None:
        await callback.answer()
        conversation_state[callback.from_user.id] = "uid"
        pending_uids.pop(callback.from_user.id, None)
        admin_state.pop(callback.from_user.id, None)
        await callback.message.answer(
            "🎮 សូមបញ្ចូល Free Fire Game UID របស់អ្នក (លេខប៉ុណ្ណោះ)៖",
            reply_markup=keyboard([back_to_menu()]),
        )

    @router.message()
    async def text_input(message: Message) -> None:
        user = message.from_user
        if user is None:
            return

        active_admin_state = admin_state.get(user.id)
        if active_admin_state == "broadcast":
            if message.content_type not in {"text", "photo", "video"}:
                await message.answer("Please send text, a photo, or a video for the announcement.")
                return
            admin_state.pop(user.id, None)
            await message.answer("📤 Broadcast started. I’ll send you a completion summary.")
            task = asyncio.create_task(_run_broadcast(bot, store, message, user.id))
            broadcast_jobs.add(task)
            task.add_done_callback(_broadcast_finished)
            return
        if active_admin_state == "price":
            raw_price = (message.text or "").strip()
            try:
                amount = Decimal(raw_price)
                if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2:
                    raise InvalidOperation
            except InvalidOperation:
                await message.answer("Enter a positive USD price with no more than two decimal places.")
                return
            package_likes = pending_price_packages[user.id]
            try:
                await store.update_package_price(package_likes, f"{amount:.2f}")
            except Exception:
                logger.exception("Could not update price for package %s", package_likes)
                await message.answer("❌ Price update failed. Please try again.")
                return
            admin_state.pop(user.id, None)
            pending_price_packages.pop(user.id, None)
            await message.answer(
                f"✅ Updated {package_likes:,} Likes to ${amount:.2f}.",
                reply_markup=_admin_panel_keyboard(),
            )
            return

        state = conversation_state.get(user.id)
        if state == "uid":
            uid = (message.text or "").strip()
            if not message.text or not uid.isascii() or not uid.isdigit() or not 1 <= len(uid) <= 20:
                await message.answer("UID មិនត្រឹមត្រូវទេ។ សូមបញ្ចូលលេខ Free Fire UID ម្តងទៀត។")
                return
            pending_uids[user.id] = uid
            conversation_state[user.id] = "package"
            packages = _package_rows(await store.package_prices())
            if not packages:
                await message.answer("No active packages are available. Please contact support.")
                conversation_state.pop(user.id, None)
                pending_uids.pop(user.id, None)
                return
            await message.answer(
                f"✅ Game UID: <code>{html.escape(uid)}</code>\nសូមជ្រើសរើស Package៖",
                parse_mode="HTML",
                reply_markup=package_keyboard(packages),
            )
            return
        if state == "package":
            await message.answer("សូមជ្រើសរើស Package ដោយចុចប៊ូតុងខាងក្រោម។")
            return
        await send_welcome(message, store, settings)

    @router.callback_query(F.data.startswith("package:"))
    async def choose_package(callback: CallbackQuery) -> None:
        uid = pending_uids.get(callback.from_user.id)
        raw_likes = (callback.data or "").partition(":")[2]
        packages = _package_rows(await store.package_prices())
        package = next((item for item in packages if str(item.likes) == raw_likes), None)
        if not uid or conversation_state.get(callback.from_user.id) != "package" or package is None:
            await callback.answer("សំណើនេះផុតកំណត់ហើយ។ សូមចាប់ផ្តើមម្តងទៀត។", show_alert=True)
            return
        await callback.answer()
        conversation_state.pop(callback.from_user.id, None)
        pending_uids.pop(callback.from_user.id, None)
        await store.upsert_user(
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.first_name,
        )
        order = await store.create_order(
            callback.from_user.id,
            uid,
            package.likes,
            f"{package.amount:.2f}",
        )
        try:
            payment = await payments.create_payment(
                f"{package.amount:.2f}",
                str(order["id"]),
                callback.from_user.id,
            )
            transaction_id = str(payment["transaction_id"])
            await store.set_order_payment(int(order["id"]), transaction_id)
            sent_message = await callback.message.answer_photo(
                _make_qr(str(payment["qr_string"])),
                caption=payment_caption(order, transaction_id),
                parse_mode="HTML",
                reply_markup=payment_keyboard(int(order["id"]), str(payment["qr_string"])),
            )
            status = await store.set_payment_message(int(order["id"]), sent_message.message_id)
            if status and status != "pending":
                try:
                    await bot.delete_message(
                        chat_id=callback.from_user.id,
                        message_id=sent_message.message_id,
                    )
                except TelegramAPIError:
                    logger.exception("Could not delete late payment QR for order %s", order["id"])
                await store.clear_payment_message(int(order["id"]))
        except Exception:
            await store.close_pending_order(int(order["id"]), "failed")
            logger.exception("Could not create the KHPAY payment for order %s", order["id"])
            await callback.message.answer(
                "មិនអាចបង្កើត QR ទូទាត់បានទេ។ សូមព្យាយាមម្តងទៀតពេលក្រោយ។"
            )

    @router.callback_query(F.data.startswith("check:"))
    async def check_payment(callback: CallbackQuery) -> None:
        raw_order_id = (callback.data or "").partition(":")[2]
        if not raw_order_id.isdecimal():
            await callback.answer("លេខបញ្ជាទិញមិនត្រឹមត្រូវ។", show_alert=True)
            return
        order = await store.order(int(raw_order_id))
        if order is None or int(order["telegram_id"]) != callback.from_user.id:
            await callback.answer("រកមិនឃើញការបញ្ជាទិញទេ។", show_alert=True)
            return
        if order["status"] in {"active", "completed"}:
            await callback.answer("បានទូទាត់រួចហើយ។")
            return
        if order["status"] != "pending" or not order.get("transaction_id"):
            await callback.answer("ការបញ្ជាទិញនេះមិនអាចពិនិត្យបានទេ។", show_alert=True)
            return
        await callback.answer("កំពុងពិនិត្យការទូទាត់…")
        payment = await payments.check_payment(str(order["transaction_id"]))
        if str(payment.get("status", "")).lower() != "paid":
            await callback.message.answer("មិនទាន់ទទួលបានការទូទាត់ទេ។ សូមពិនិត្យម្តងទៀតបន្ទាប់ពីបង់ប្រាក់។")
            return
        expected = Decimal(str(order["amount"])).quantize(Decimal("0.01"))
        confirmed = Decimal(str(payment["amount"])).quantize(Decimal("0.01"))
        if confirmed != expected:
            logger.error("KHPAY amount mismatch for order %s", order["id"])
            await callback.message.answer("ចំនួនទឹកប្រាក់មិនត្រូវគ្នា។ សូមទាក់ទងជំនួយ។")
            return
        await process_paid_order(
            bot, store, reseller, settings, int(order["id"])
        )

    @router.callback_query(F.data == "history")
    async def order_history(callback: CallbackQuery) -> None:
        await callback.answer()
        orders = await store.order_history(callback.from_user.id)
        if not orders:
            text = "📊 មិនទាន់មានប្រវត្តិបញ្ជាទិញទេ។"
        else:
            rows = [
                (
                    f"• <code>#{item['id']}</code> · UID <code>{html.escape(str(item['ff_uid']))}</code>\n"
                    f"  {int(item['total_likes']):,} Likes · "
                    f"{_progress_bar(int(item['sent_likes']), int(item['total_likes']))}\n"
                    f"${Decimal(str(item['amount'])):.2f} · {html.escape(str(item['status']))}"
                )
                for item in orders
            ]
            text = "📊 <b>ប្រវត្តិបញ្ជាទិញ</b>\n\n" + "\n".join(rows)
        await callback.message.answer(
            text,
            parse_mode="HTML",
            reply_markup=keyboard([back_to_menu()]),
        )

    @router.callback_query(F.data == "guide")
    async def guide(callback: CallbackQuery) -> None:
        await callback.answer()
        video_url = await store.bot_setting(AUTOLIKE_TUTORIAL_VIDEO_SETTING)
        video_message = (
            ""
            if video_url
            else "\n\nវីដេអូណែនាំមិនទាន់មានទេ។"
        )
        if not video_url:
            logger.warning("Supabase setting %s is not configured", AUTOLIKE_TUTORIAL_VIDEO_SETTING)
        await callback.message.answer(
            "🎥 <b>របៀបទិញ AutoLike</b>\n"
            "1. ចុចប៊ូតុងទិញ ហើយបញ្ចូល UID Free Fire។\n"
            "2. ជ្រើសរើសចំនួន Likes និងស្កេន KHQR ក្នុងរយៈពេល 15 នាទី។\n"
            "3. ប្រព័ន្ធនឹងបញ្ចូលរហូតដល់ 220 Likes ក្នុងមួយថ្ងៃ."
            f"{video_message}",
            parse_mode="HTML",
            reply_markup=guide_keyboard(video_url),
        )

    @router.callback_query(F.data == "help")
    async def help_menu(callback: CallbackQuery) -> None:
        await callback.answer()
        support = settings.support_username.strip().lstrip("@")
        text = (
            f"💬 ជំនួយ៖ @{html.escape(support)}"
            if support
            else "💬 សូមទាក់ទងអ្នកគ្រប់គ្រងតាមរយៈក្រុមជំនួយ។"
        )
        await callback.message.answer(
            text,
            reply_markup=keyboard([back_to_menu()]),
        )

    dispatcher.include_router(router)
