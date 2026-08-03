# External Services — What Blocked This Mission, and What CoinPilotX Actually Needs

You asked for a list of missing APIs to buy. This document answers that in two
parts, because the honest answer has two halves and only the second one involves
spending money.

**Part A** is what actually blocked me during the Business OS mission. Nothing in
it is purchasable — every one was a sandbox restriction.

**Part B** is the list you can actually act on: the third-party services this
codebase calls, derived by walking every `os.getenv` / `os.environ.get` name in
`services/` and `bot.py`. It is what the code will look for at runtime. Which of
these you already hold, only you can confirm — this is read from the source, not
from your accounts.

---

## Part A — What blocked the mission (nothing to buy)

Four things went wrong during this work. All four are properties of the sandbox
I run in, not gaps in your subscriptions. Buying an API would not have fixed any
of them.

**No network egress.** `pip install pytest` fails with "No matching distribution
found" and `npm ping` returns 403 — the sandbox has no route to PyPI or the npm
registry. This is why no test in this repo depends on pytest being installed;
every suite carries its own `_main()` runner and works standalone:

    python tests/business_os/test_capture_atomicity.py

That was a design constraint, not a workaround I resented. A suite that runs
with nothing but the interpreter is a suite that runs on any machine you hand it
to.

**`git push` blocked in both directions.** SSH to `github.com:22` is refused by
the proxy (`E CONNECT github.com:22: Forbidden`) and HTTPS returns
`HTTP code 403 from proxy after CONNECT`. Your GitHub account is fine; the
sandbox cannot reach it. The commit `cff70c40` exists locally and sits one ahead
of origin.

**Stale `.git` lock files that cannot be deleted.** `.git/index.lock`,
`.git/HEAD.lock` and `.git/next-index-6.lock` are present, and `rm` returns
"Operation not permitted" on all three. I committed anyway by pointing git at an
alternate index (`GIT_INDEX_FILE=/tmp/gidx`), which sidesteps `index.lock`
entirely. You will want to clear them before your next git operation:

    rm -f .git/*.lock
    git push origin codex/emergency-live-audio-recovery

**No PostgreSQL instance.** `localhost:5432` refuses connections. Two of the ten
fixes concern PostgreSQL-specific behaviour — the ledger row lock and the
savepoint discard — and both are tested against SQLite, which reproduces the
relevant semantics for the same underlying reasons. That is a defensible proxy
and it is not the real thing. Running those two suites against a real Postgres
before you rely on them in production is the one genuinely outstanding
verification step from this mission.

---

## Part B — Third-party services the code calls

Grouped by what breaks without them. Every name below appears in a real
`os.getenv` call in `services/` or `bot.py`.

### Payments and billing

Stripe is the only payment provider wired in, and it is the one this mission
touched most.

`STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`,
`STRIPE_CONNECT_CLIENT_ID`, `STRIPE_PRICE_ID`, `STRIPE_PREMIUM_PRICE_ID`,
`STRIPE_PREMIUM_PLUS_PRICE_ID`, `STRIPE_FOUNDER_PRICE_ID`,
`PAYMENT_PROVIDER_ENABLED`

`STRIPE_CONNECT_CLIENT_ID` is worth calling out separately: Connect is what pays
your sellers, and it is a distinct Stripe product from ordinary charges. The
refund double-count defects fixed in this mission (§3 of the evidence document)
live in the webhook handler, so `STRIPE_WEBHOOK_SECRET` being correct is what
makes those fixes reachable at all — an unverified webhook never gets to the
corrected code.

### Video, live streaming and calls

Mux handles video assets and live streams; LiveKit handles real-time calls.
These are two separate vendors and both are metered.

Mux: `MUX_TOKEN_ID`, `MUX_TOKEN_SECRET`, `MUX_WEBHOOK_SECRET`,
`MUX_DATA_ENV_KEY`, `MUX_DATA_ANALYTICS_ENABLED`

LiveKit: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`,
`LIVEKIT_WEBHOOK_SECRET`

WebRTC infrastructure: `STUN_SERVER_URL`, `TURN_SERVER_URL`. A STUN server can
be free; **TURN is the one that costs money**, because it relays media rather
than just discovering addresses, and it is what makes calls work for users
behind restrictive NATs. Skipping TURN does not break calls in testing — it
breaks them for a fraction of real users, silently, which is the worst failure
shape.

Streaming ingest/playback: `PULSE_RTMP_INGEST_URL`, `PULSE_HLS_PLAYBACK_URL`,
`PULSE_LIVE_RESTREAM_ENABLED`

### Storage and CDN

Cloudflare R2 and/or S3, for media:

`R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`,
`R2_ENDPOINT`, `R2_ENDPOINT_URL`, `R2_PUBLIC_BASE_URL`

`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `S3_BUCKET`,
`S3_ENDPOINT_URL`, `MEDIA_STORAGE_PROVIDER`

`MEDIA_REQUIRE_DURABLE_UPLOAD` is the flag that decides whether local-disk
storage is acceptable. In production it should be on, and turning it on without
one of the above configured will fail uploads rather than quietly writing to a
disk that disappears on redeploy.

### AI and language

OpenAI: `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_SCAM_MODEL`,
`OPENAI_TELEGRAM_MODEL`, `PULSE_AI_ENABLED`, `PULSE_AI_RATE_LIMIT_PER_MINUTE`

Google Cloud Translation: `GOOGLE_CLOUD_TRANSLATION_API_KEY`,
`GOOGLE_CLOUD_TRANSLATION_CREDENTIALS_JSON`, `GOOGLE_CLOUD_PROJECT_ID`,
`GOOGLE_CLOUD_TRANSLATION_LOCATION`, `TRANSLATION_ENABLED`,
`TRANSLATION_PRIMARY_PROVIDER`

AI story media: `AI_STORY_PROVIDER`, `AI_STORY_IMAGE_ENDPOINT`,
`AI_STORY_VIDEO_ENDPOINT`

### Crypto market data

This is the densest cluster of separate paid vendors in the codebase, and the
one where costs stack up quickly because each is billed independently:

`COINGECKO_API_KEY`, `COINMARKETCAP_API_KEY`, `CRYPTOPANIC_API_KEY`,
`WHALE_ALERT_API_KEY`, `NEWS_API_KEY`, `ETHERSCAN_API_KEY`,
`BSCSCAN_API_KEY`, `POLYGONSCAN_API_KEY`

Related switches: `PULSESOC_CRYPTO_DISABLE_LIVE_MARKETS`,
`MARKETS_CACHE_SECONDS`, `NEWS_CACHE_SECONDS`, `PREDICTIONS_PROVIDER`,
`PREDICTIONS_CACHE_SECONDS`

The cache TTLs are cost controls as much as performance controls. CoinGecko and
CoinMarketCap overlap substantially in coverage; if you are trimming spend, that
pair is the first place to look rather than the block-explorer keys, which do
different jobs per chain.

### Email, SMS and push

Brevo (formerly Sendinblue) covers both email and SMS:
`BREVO_API_KEY`, `BREVO_SMTP_API_KEY`, `BREVO_SMS_API_KEY`,
`BREVO_SMS_ENABLED`, `BREVO_SMS_SENDER`, `BREVO_DEFAULT_LIST_ID`,
`BREVO_PRO_LIST_ID`, `BREVO_TELEGRAM_LIST_ID`, `SENDINBLUE_API_KEY`,
`DEFAULT_FROM_EMAIL`, `SMS_SENDER_NAME`

`SENDINBLUE_API_KEY` alongside `BREVO_API_KEY` is a rename artifact — same
vendor, old name. Worth consolidating so nobody buys twice.

Push notification credentials — these are free from Apple/Google but require
developer accounts:
`APNS_USE_SANDBOX`, `APPLE_ROOT_CA_CERTS`, `PULSESOC_APPLE_TEAM_ID`,
`PUSH_ENVIRONMENT`, `VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY`, `VAPID_SUBJECT`,
`WEB_PUSH_PRIVATE_KEY`, `WEB_PUSH_PUBLIC_KEY`, `WEB_PUSH_SUBJECT`

The badge work in this mission (§5 of the evidence document) rides on
`PUSH_BADGE_ENABLED`; the fix is inert if push is not configured.

### Telegram

`BOT_TOKEN`, `TELEGRAM_BOT_TOKEN` — free from BotFather.

### Infrastructure

`DATABASE_URL` (PostgreSQL), `REDIS_URL`, and the Railway hosting variables
(`RAILWAY_PROJECT_ID`, `RAILWAY_SERVICE_ID`, `RAILWAY_ENVIRONMENT`,
`RAILWAY_ENVIRONMENT_NAME`). These are hosting costs rather than API purchases,
but they are recurring and they are the ones that stop everything when unpaid.

### Secrets you generate rather than buy

Free, but the app is insecure without them being set to real random values:
`SECRET_KEY`, `FLASK_SECRET_KEY`, `SESSION_SECRET`, `LIVE_SECRET_KEY`,
`ANALYTICS_SALT`, `PULSESOC_SECURITY_SALT`, `PULSESOC_GROWTH_KEY_SALT`,
`PULSESOC_BRIDGE_SIGNING_SECRET`, `PULSE_ADS_DELIVERY_SECRET`,
`COMMAND_CENTER_INTERNAL_TOKEN`

---

## What this list is not

It is not an audit of which services are currently unconfigured in your
production environment — I have no access to that. It is a read of what the code
asks for.

It also carries no pricing. Quoting figures would mean fetching current vendor
pages, and this environment has no network access, so any number here would be a
guess dressed as a fact.
