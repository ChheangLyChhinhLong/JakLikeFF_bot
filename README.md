# Free Fire AutoLike Bot

This Telegram bot sells Free Fire Like packages with admin-editable prices, creates KHPAY KHQR payments, and submits daily delivery batches to AutoLikeFF, whose service enforces its own per-day limit. All Telegram interaction uses inline buttons and text input; the bot does not register slash commands.

## Setup

1. Create a Supabase project and run [`schema.sql`](./schema.sql) in its SQL editor. **This migration drops all existing `users`, `orders`, `package_prices`, `bot_settings`, `categories`, `products`, and `stock_items` data.** Back up anything you need first. The application never runs this destructive migration automatically.
2. Copy `.env.example` to `.env` and configure the Telegram token, `TELEGRAM_ADMIN_IDS`, `ADMIN_GROUP_ID`, Supabase service-role credentials, the KHPAY API key, webhook secret and public webhook URL, and the reseller credentials.
3. Configure the KHPAY webhook URL as `https://<your-public-host>/webhook/khpay`. The app verifies `X-Webhook-Signature` or `X-KHPAY-Signature` using the raw request body and `KHPAY_WEBHOOK_SECRET`.
4. Install dependencies with `pip install -r requirements.txt`.
5. Start the service with `python -m app.main`.

The service runs Telegram long polling, payment reconciliation, and a daily delivery worker. The first delivery is sent after payment confirmation; subsequent deliveries run at most once per day until the purchased package is complete. `RESELLER_DAILY_BATCH_LIKES` controls the requested batch size (default `220`); AutoLikeFF remains authoritative for its own daily system limit and returns the delivered count used for progress tracking. The configured reseller endpoint is upgraded to HTTPS for `mean-like.com` so the bearer key is not sent over cleartext HTTP. Configure a persistent process with one running bot instance. Database claims reduce duplicate daily deliveries when workers overlap.

## Operations

- Package options and USD prices are seeded in `package_prices`; admins can edit active prices from the inline Admin Panel.
- Admins can broadcast text, photos, or videos to active users and view aggregate user/order/revenue statistics.
- `ADMIN_GROUP_ID` receives progress reports for each daily delivery batch, including the visual progress bar.
- Customers can see the same progress bar and their delivery history from the inline menu.
- All bot interaction is through inline buttons and message/state handlers; no slash commands are registered.
- Keep the Supabase service-role key private. Do not expose it to clients.
