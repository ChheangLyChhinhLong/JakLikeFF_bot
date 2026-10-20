import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.bot import (
    PACKAGES,
    _broadcast_to_active_users,
    guide_keyboard,
    _progress_bar,
    _progress_text,
    menu,
    package_keyboard,
    payment_caption,
    payment_keyboard,
    process_daily_delivery,
    welcome_text,
)
from app.payments import aba_mobile_deeplink


def test_fixed_packages_match_the_published_price_tier():
    assert [(item.likes, item.amount) for item in PACKAGES] == [
        (220, Decimal("0.20")),
        (1_000, Decimal("0.50")),
        (2_000, Decimal("1.00")),
        (3_000, Decimal("1.50")),
        (4_000, Decimal("2.00")),
        (5_000, Decimal("2.50")),
        (6_000, Decimal("2.75")),
        (7_000, Decimal("3.25")),
        (8_000, Decimal("3.75")),
        (9_000, Decimal("4.25")),
        (10_000, Decimal("4.50")),
        (20_000, Decimal("8.50")),
        (30_000, Decimal("12.00")),
        (50_000, Decimal("19.00")),
    ]
    assert len(package_keyboard().inline_keyboard) == 15


def test_welcome_escapes_user_supplied_html_and_shows_id_and_name():
    user = SimpleNamespace(id=12345, first_name="<Player>", username="ff_user")

    text = welcome_text(user)

    assert "<code>12345</code>" in text
    assert "&lt;Player&gt;" in text
    assert "@ff_user" in text
    assert all(button.callback_data for row in menu().inline_keyboard for button in row)
    assert all("/" not in button.callback_data for row in menu().inline_keyboard for button in row)


def test_guide_keyboard_links_to_the_configured_video_and_keeps_menu_navigation():
    markup = guide_keyboard("https://t.me/LongStores_kh/134")

    assert markup.inline_keyboard[0][0].url == "https://t.me/LongStores_kh/134"
    assert markup.inline_keyboard[1][0].callback_data == "home"
    assert len(guide_keyboard(None).inline_keyboard) == 1


def test_payment_screen_has_the_exact_two_inline_actions():
    order = {"ff_uid": "123456", "total_likes": 1_000, "amount": "0.50"}
    caption = payment_caption(order, "txn-456")
    markup = payment_keyboard(11, "KHQR+payload/123=")

    assert caption == (
        "💳 <b>Pay $0.50</b>\n\n"
        "🎮 <b>Game UID:</b> <code>123456</code>\n"
        "👍 <b>Package:</b> 1,000 Likes\n"
        "💰 <b>Total Amount:</b> $0.50\n"
        "🟢 <b>Remaining to pay:</b> $0.50\n"
        "🏷 <b>Ref:</b> <code>txn-456</code>\n"
        "⏱ <b>Scan KHQR to complete payment.</b>\n"
        "⏰ <b>You have 15 minutes to pay. The QR refreshes itself.</b>"
    )
    assert len(markup.inline_keyboard) == 2
    assert markup.inline_keyboard[0][0].url == aba_mobile_deeplink("KHQR+payload/123=")
    assert markup.inline_keyboard[1][0].callback_data == "check:11"


def test_progress_message_shows_partial_and_completed_order_states():
    order = {"ff_uid": "123456", "total_likes": 1_000}

    partial = _progress_text(order, 220, 440, False)
    complete = _progress_text(order, 120, 1_000, True)

    assert "Likes Added Today:</b> +220" in partial
    assert "440/1,000" in partial
    assert "In Progress (44%)" in partial
    assert "[████░░░░░░] 440/1,000 (44%)" in partial
    assert "Completed" in complete
    assert "1,000/1,000" in complete


def test_progress_bar_uses_ten_blocks_and_clamps_values():
    assert _progress_bar(440, 1_000) == "[████░░░░░░] 440/1,000 (44%)"
    assert _progress_bar(1_000, 1_000) == "[██████████] 1,000/1,000 (100%)"
    assert _progress_bar(1_100, 1_000) == "[██████████] 1,000/1,000 (100%)"


def test_daily_delivery_uses_one_atomic_claim_and_notifies_customer_and_group():
    async def run_test():
        order = {
            "id": 9,
            "telegram_id": 123,
            "ff_uid": "123456",
            "total_likes": 1_000,
            "sent_likes": 0,
        }
        store = SimpleNamespace(
            claim_due_order=AsyncMock(return_value=order),
            record_delivery=AsyncMock(
                return_value={"sent_likes": 220, "status": "active"}
            ),
            release_delivery_claim=AsyncMock(),
        )
        reseller = SimpleNamespace(deliver=AsyncMock(return_value=220))
        bot = SimpleNamespace(send_message=AsyncMock())
        settings = SimpleNamespace(admin_chat_id=-100123, telegram_admin_ids="")

        delivered = await process_daily_delivery(bot, store, reseller, settings, 9)

        assert delivered
        reseller.deliver.assert_awaited_once_with("123456", 220)
        store.record_delivery.assert_awaited_once_with(9, 220)
        assert [call.args[0] for call in bot.send_message.await_args_list] == [123, -100123]
        assert all(call.kwargs["parse_mode"] == "HTML" for call in bot.send_message.await_args_list)

    asyncio.run(run_test())


def test_daily_delivery_releases_claim_if_reseller_request_fails():
    async def run_test():
        store = SimpleNamespace(
            claim_due_order=AsyncMock(
                return_value={
                    "id": 9,
                    "ff_uid": "123456",
                    "total_likes": 500,
                    "sent_likes": 0,
                }
            ),
            release_delivery_claim=AsyncMock(),
            record_delivery=AsyncMock(),
        )
        reseller = SimpleNamespace(deliver=AsyncMock(side_effect=RuntimeError("offline")))

        try:
            await process_daily_delivery(
                SimpleNamespace(),
                store,
                reseller,
                SimpleNamespace(admin_chat_id=None, telegram_admin_ids=""),
                9,
            )
        except RuntimeError as exc:
            assert str(exc) == "offline"
        else:
            raise AssertionError("Expected the reseller error to propagate")

        store.release_delivery_claim.assert_awaited_once_with(9)
        store.record_delivery.assert_not_awaited()

    asyncio.run(run_test())


def test_broadcast_copies_message_to_active_users_and_reports_summary():
    async def run_test():
        store = SimpleNamespace(active_user_ids=AsyncMock(return_value=[123, 456]))
        source = SimpleNamespace(copy_to=AsyncMock())
        bot = SimpleNamespace(send_message=AsyncMock())

        await _broadcast_to_active_users(bot, store, source, 999)

        assert [call.kwargs["chat_id"] for call in source.copy_to.await_args_list] == [123, 456]
        assert bot.send_message.await_args.args[0] == 999
        assert "Delivered: 2" in bot.send_message.await_args.args[1]
        store.active_user_ids.assert_awaited_once_with(0)

    asyncio.run(run_test())
