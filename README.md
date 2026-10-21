# Free Fire AutoLike Bot

This Telegram bot sells Free Fire Like packages with admin-editable prices, creates KHPAY KHQR payments, and submits each paid package once to AutoLikeFF, whose service handles automatic delivery. All Telegram interaction uses inline buttons and text input; the bot does not register slash commands.

## Setup

1. Create a Supabase project and run [`schema.sql`](./schema.sql) in its SQL editor. **This migration drops all existing `users`, `orders`, `package_prices`, `bot_settings`, `categories`, `products`, and `stock_items` data.** Back up anything you need first. The application never runs this destructive migration automatically.
2. Copy `.env.example` to `.env` and configure the Telegram token, `TELEGRAM_ADMIN_IDS`, `ADMIN_GROUP_ID`, Supabase service-role credentials, the KHPAY API key, webhook secret and public webhook URL, and the reseller credentials.
3. Configure the KHPAY webhook URL as `https://<your-public-host>/webhook/khpay`. The app verifies `X-Webhook-Signature` or `X-KHPAY-Signature` using the raw request body and `KHPAY_WEBHOOK_SECRET`.
4. Install dependencies with `pip install -r requirements.txt`.
5. Start the service with `python -m app.main`.

The service runs Telegram long polling and payment reconciliation. After payment is confirmed, the bot makes one AutoLikeFF request for the full purchased package; AutoLikeFF handles the remaining delivery automatically. A claimed request is never retried automatically after failure because the outcome may be ambiguous, and the customer/admin are notified to review it. Paid orders for the same UID are queued FIFO and submitted once when eligible. The configured reseller endpoint is upgraded to HTTPS for `mean-like.com` so the bearer key is not sent over cleartext HTTP. Configure a persistent process with one running bot instance.

## Operations

- Package options and USD prices are seeded in `package_prices`; admins can edit active prices from the inline Admin Panel.
- The “របៀបទិញ” guide reads `autolike_tutorial_video_url` from `bot_settings`. For an existing Supabase project, run [`migrations/20261021_add_autolike_tutorial_video.sql`](./migrations/20261021_add_autolike_tutorial_video.sql) once to set the supplied tutorial video link.
- Admins can broadcast text, photos, or videos to active users and view aggregate user/order/revenue statistics.
- `ADMIN_GROUP_ID` receives customer/admin progress reports for each AutoLikeFF response, including the visual progress bar.
- Existing Supabase projects must run [`migrations/20261021_queue_same_uid_orders.sql`](./migrations/20261021_queue_same_uid_orders.sql), then [`migrations/20261021_single_reseller_request.sql`](./migrations/20261021_single_reseller_request.sql) to preserve the same-UID FIFO queue and prevent request retries.
- Customers can see the same progress bar and their delivery history from the inline menu.
- All bot interaction is through inline buttons and message/state handlers; no slash commands are registered.
- Keep the Supabase service-role key private. Do not expose it to clients.
