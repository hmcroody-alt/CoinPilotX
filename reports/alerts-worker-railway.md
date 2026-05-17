# CoinPilotXAI Alerts Worker Deployment Note

The Alerts Command Center now stores real alert rules and can evaluate them through the admin manual check endpoint. For automatic production monitoring, Railway should run a second service:

```bash
python alert_worker.py
```

Recommended Railway setup:

- Web service: existing Flask app command.
- Worker service: `python alert_worker.py`.
- Shared environment: same `DATABASE_URL`, live market provider keys, Brevo, VAPID, Twilio, and Telegram variables as needed.

If the worker is not running, the web app still boots and users can create/manage alerts, but `/admin/alerts` will show the worker heartbeat as stale.

Delivery behavior:

- In-app notifications are always available.
- Email requires `BREVO_API_KEY`.
- PWA push requires `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, and an active browser subscription.
- SMS requires Twilio variables plus a verified, opted-in user phone.
- Telegram requires `BOT_TOKEN` and a linked `telegram_chat_id`.

External delivery failures are logged in `notification_delivery_logs` and do not crash the app.
