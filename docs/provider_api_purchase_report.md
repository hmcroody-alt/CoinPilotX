# Provider and API Purchase Report

Mission phase 6. This is the buy / don't-buy decision document for every
third-party service PulseSoc reads a credential for.

It supersedes the Part B inventory in `docs/external_services_and_blockers.md`,
which was a grep of `os.getenv` call sites in `services/` and `bot.py`. That
inventory was incomplete in a way that mattered: sixteen wrapper functions
(`_env_value`, `_flag`, `subflag_enabled`, and thirteen others) hide variable
names from a plain grep, and two registries — `undx_router.PROVIDERS` and the
`required_env` lists in `services/pulsesoc_intelligence_engine.py` — resolve
names at runtime that appear at no call site at all. The inventory below is
derived from `tests/protection/test_environment_contract.py`, which now covers
all three shapes and accounts for **452 variables read by production code, 462
declared in `.env.example`, 0 undocumented**.

Two things this document is not. It is not a statement of what is configured in
your Railway environment — I have no access to that, and every "currently
configured" question is one only you can answer by reading the Railway
variables page. And it carries no prices: this environment has no route to
vendor pricing pages, so any figure here would be a guess wearing the costume
of a fact.

---

## 1. The finding that changes what you should spend

Six credentials in this codebase are read **only to decide the colour of a
status light**. No code anywhere calls the corresponding vendor. Buying any of
them today would produce no new data, no new feature, and no change in
behaviour beyond a readiness row flipping from red to green.

| Credential | Declared in | Actually fetched by | Buying it today gets you |
|---|---|---|---|
| `POLYGON_API_KEY` | `pulsesoc_intelligence_engine.py:804`, `intelligence_collectors/markets.py:289` | nothing — no `polygon.io` call exists | nothing |
| `FINNHUB_API_KEY` | `pulsesoc_intelligence_engine.py:805`, `markets.py:290` | nothing — no `finnhub.io` call exists | nothing |
| `ALPHA_VANTAGE_API_KEY` | `pulsesoc_intelligence_engine.py:806`, `markets.py:291` | nothing — no `alphavantage` call exists | nothing |
| `REUTERS_API_KEY` | `pulsesoc_intelligence_engine.py:807` | nothing | nothing |
| `AP_NEWS_API_KEY` | `pulsesoc_intelligence_engine.py:808` | nothing | nothing |
| `WHALE_ALERT_API_KEY` | `services/live_market_service.py:130` | nothing | nothing |

The market-pulse collector is worth reading in full, because it shows the shape
precisely. `MarketPulseCollector.run()` loops over the three market keys and,
for each one that is unset, appends a `config_missing` status row. Then — in a
completely separate block that consults none of those keys — it fetches the
actual quote matrix from Yahoo Finance, which needs no credential:

```python
for source_key, env_name in [("polygon", "POLYGON_API_KEY"), ...]:
    if not os.getenv(env_name):
        statuses.append(_source_config_status(source_key, env_name))

data, cached, duration = self.fetch_json(
    f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={QUOTE_SYMBOLS}", ...)
```

So the market feed already works. The three red rows next to it are not
reporting a degraded feed; they are reporting the absence of keys that no
fetcher consumes. Setting them would silence the rows and change nothing else.

This is the same defect class as the phantom readiness variables fixed earlier
in this mission (`R2_BUCKET_NAME`, `PULSE_AI_ROUTER_URL`) — a status signal
wired to something other than the thing it claims to describe. The difference
is that these six are *coherent* as a plan; someone intended to add those
collectors. The honest reading is "not built yet", not "not configured".

**Recommendation: do not purchase any of the six.** Two of them — the whale
surface and the crypto command centre — already label themselves `PARTIAL` with
"live provider required", which is the correct and honest state. Revisit when a
collector is written; the key is the last step, not the first.

---

## 2. Required — the platform does not work without these

Every row here is already load-bearing in code paths users hit. If any is
missing in production, the failure is real and user-visible.

| Provider | Powers | Variables | Paid | Behaviour when unset |
|---|---|---|---|---|
| **PostgreSQL** (Railway) | all persistence | `DATABASE_URL` | yes, hosting | falls back to local SQLite `coinpilotx.db`, which is wiped on redeploy — silent data loss, not an error |
| **Stripe** | subscriptions, marketplace payouts, ads billing | `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_CONNECT_CLIENT_ID`, `STRIPE_PRICE_ID`, `STRIPE_PREMIUM_PRICE_ID`, `STRIPE_PREMIUM_PLUS_PRICE_ID`, `STRIPE_FOUNDER_PRICE_ID`, `PAYMENT_PROVIDER_ENABLED` | yes, per-transaction | checkout routes register but every charge fails; an unset `STRIPE_WEBHOOK_SECRET` means subscription state silently never updates after payment |
| **LiveKit** | live audio/video calls, Live broadcast | `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `LIVEKIT_WEBHOOK_SECRET` | yes, per-minute | token mint fails, so "Broadcast could not start" — this is the same surface as the phase 9 incident |
| **Cloudflare R2** (or S3) | all uploaded media | `R2_BUCKET` \| `S3_BUCKET`, `R2_ACCESS_KEY_ID` \| `AWS_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` \| `AWS_SECRET_ACCESS_KEY`, `R2_ENDPOINT_URL` \| `R2_ENDPOINT` \| `R2_ACCOUNT_ID` \| `S3_ENDPOINT_URL`, `R2_PUBLIC_BASE_URL` | yes, storage + egress | with `MEDIA_REQUIRE_DURABLE_UPLOAD` on, uploads fail loudly (correct); with it off, media writes to ephemeral disk and vanishes on redeploy |
| **Mux** | VOD assets, HLS playback, reels transcode | `MUX_TOKEN_ID`, `MUX_TOKEN_SECRET`, `MUX_WEBHOOK_SECRET`, `MUX_DATA_ENV_KEY`, `MUX_DATA_ANALYTICS_ENABLED` | yes, per-minute encoded + delivered | uploads accepted, never transcode; posts sit in "processing" forever |
| **Brevo** | transactional email, SMS | `BREVO_API_KEY`, `BREVO_SMTP_API_KEY`, `BREVO_SMS_API_KEY`, `BREVO_SMS_ENABLED`, `BREVO_SMS_SENDER`, `BREVO_DEFAULT_LIST_ID`, `BREVO_PRO_LIST_ID`, `BREVO_TELEGRAM_LIST_ID`, `DEFAULT_FROM_EMAIL`, `SMS_SENDER_NAME` | yes, per-send above free tier | password reset and verification email never arrive; signup completes and the account is unreachable |
| **TURN relay** | calls for users behind symmetric NAT | `TURN_SERVER_URL` (and `STUN_SERVER_URL`) | yes — TURN relays media | calls work in testing and fail for a minority of real users, silently. The worst-shaped failure on this page |

The alias columns are not cosmetic. `services/media_storage.py` resolves
`R2_BUCKET or S3_BUCKET`, `R2_ACCESS_KEY_ID or AWS_ACCESS_KEY_ID`, and a
four-way endpoint fallback. Until this mission the readiness tables demanded
the `R2_*` spelling exclusively and also demanded a variable named
`R2_BUCKET_NAME` that no code reads — so a correctly configured S3 deployment
reported `config_missing` forever. Both tables now accept exactly what the
runtime accepts, locked by
`test_r2_readiness_accepts_the_same_aliases_media_storage_accepts`.

---

## 3. Free, but you must generate or register them

| Provider | Powers | Variables | Cost | Behaviour when unset |
|---|---|---|---|---|
| **Telegram BotFather** | Telegram bot | `BOT_TOKEN`, `TELEGRAM_BOT_TOKEN` | free | bot silent; web app unaffected |
| **Apple Push (APNs)** | iOS push | `APNS_USE_SANDBOX`, `APPLE_ROOT_CA_CERTS`, `PULSESOC_APPLE_TEAM_ID`, `PUSH_ENVIRONMENT` | free with a $99/yr developer account | no iOS notifications; no error surfaced to the sender |
| **Firebase / FCM** | Android push | `FCM_*` group | free | as above, Android |
| **Web Push (VAPID)** | browser push | `VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY`, `VAPID_SUBJECT`, `WEB_PUSH_PRIVATE_KEY`, `WEB_PUSH_PUBLIC_KEY`, `WEB_PUSH_SUBJECT` | free — you generate the keypair | browser push silently unavailable |
| **Google Search Console / Bing** | site verification only | `BING_SITE_VERIFICATION`, `GOOGLE_SITE_VERIFICATION` | free | SEO verification only; no runtime effect |
| **App secrets** | sessions, CSRF, signing | `SECRET_KEY`, `FLASK_SECRET_KEY`, `SESSION_SECRET`, `LIVE_SECRET_KEY`, `ANALYTICS_SALT`, `PULSESOC_SECURITY_SALT`, `PULSESOC_GROWTH_KEY_SALT`, `PULSESOC_BRIDGE_SIGNING_SECRET`, `PULSE_ADS_DELIVERY_SECRET`, `COMMAND_CENTER_INTERNAL_TOKEN`, `ADMIN_BOOTSTRAP_TOKEN`, `ADMIN_DASHBOARD_TOKEN` | free, you generate | the app boots with weak or default values — this is a security defect, not an outage, so nothing goes red |

The last row deserves emphasis. Every other failure on this page announces
itself. A weak `SECRET_KEY` produces a perfectly healthy-looking deployment.

---

## 4. Build and release credentials — not runtime, and previously miscounted

| Provider | Powers | Variables | Paid |
|---|---|---|---|
| **Expo / EAS** | native builds | `EXPO_ACCESS_TOKEN` | paid above free tier |
| **App Store Connect** | iOS submission | `APP_STORE_CONNECT_*` | $99/yr |
| **Google Play** | Android submission | `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON` | $25 once |

These are consumed by the release pipeline, never by the running server. Until
this mission they were scored by `EXTERNAL_SERVICE_CHECKS` as runtime external
services, and so contributed three permanent "external service gaps" to the
operations headline — three red marks on a server that was fine. They now carry
`"scope": "build"` and are reported separately as `build_credential_gaps` in
`operating_system_snapshot()`. A production web dyno missing an Expo token is
not degraded, and the dashboard should not have been saying it was.

---

## 5. Genuinely optional — real degradation, real fallback

These do something when configured and degrade gracefully when not. Buy on
merit, not on red lights.

| Provider | Powers | Variables | Paid | Behaviour when unset |
|---|---|---|---|---|
| **CoinGecko** | crypto prices | `COINGECKO_API_KEY` | free tier works keyless; key raises rate limits | fully functional at lower rate limits — `bot.py:14565` literally labels it "CoinGecko key optional" |
| **CoinMarketCap** | crypto prices (alternate) | `COINMARKETCAP_API_KEY`, alias `CMC_API_KEY` | paid above free tier | falls through to CoinGecko. Overlaps CoinGecko substantially — the first place to trim |
| **NewsAPI** | crypto/world news feed | `NEWS_API_KEY` | paid above dev tier | `news_service.py:114` returns `[], "NEWS_API_KEY missing"` — an empty feed with a stated reason, which is the correct shape |
| **CryptoPanic** | crypto sentiment news | `CRYPTOPANIC_API_KEY` | free tier available | same shape, `news_service.py:132` |
| **Etherscan / BscScan / PolygonScan** | wallet intelligence per chain | `ETHERSCAN_API_KEY`, `BSCSCAN_API_KEY`, `POLYGONSCAN_API_KEY` | free tier generous | `wallet_intel.py:41` falls back to a public explorer and **says so in the user-facing note**. Model behaviour |
| **Brave / Bing / SerpAPI / Tavily** | Pulse AI live web search | `BRAVE_SEARCH_API_KEY`, `BING_SEARCH_API_KEY` \| `BING_SEARCH_V7_SUBSCRIPTION_KEY`, `SERPAPI_API_KEY`, `TAVILY_API_KEY` | all paid | tried in that order, then DuckDuckGo instant answers, which needs no key. Weaker results, never a hard failure |
| **Twilio** | SMS in Communications v2 | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` | paid per message | gated behind `COMM_V2_TWILIO_NOTIFICATIONS_ENABLED` and defaults to `COMM_V2_TWILIO_DRY_RUN=true`. Overlaps Brevo SMS — pick one |
| **Banuba** | camera filters | `BANUBA_TOKEN` | paid licence | camera works without filters |
| **Google Cloud Translation** | post translation | `GOOGLE_CLOUD_TRANSLATION_API_KEY`, `GOOGLE_CLOUD_TRANSLATION_CREDENTIALS_JSON`, `GOOGLE_CLOUD_PROJECT_ID`, `GOOGLE_CLOUD_TRANSLATION_LOCATION`, `TRANSLATION_ENABLED`, `TRANSLATION_PRIMARY_PROVIDER` | paid per character | translate button inert |
| **The Odds API / SportsData** | sports predictions | `THE_ODDS_API_KEY`, `SPORTSDATA_API_KEY` | paid | `bot.py:97165` gates the surface on the key; feature absent, not broken |
| **Redis** | caching, rate limiting | `REDIS_URL` | hosting cost | in-process fallback. Correct on one dyno, wrong the moment you scale to two — rate limits become per-worker |

Only one AI provider key is needed, not all six. `undx_router.PROVIDERS`
resolves `OPENAI_API_KEY`, `CLAUDE_AI_API`, `Gemini_AI_API` (the mixed-case
spelling is real and deliberate; `GEMINI_AI_API` is accepted as a fallback),
`DEEPSEEK_AI_API` and `GROQ_AI_API` through `os.getenv(config.key_env)`, and
falls through to whichever is present. The `ai` readiness row previously
demanded a variable called `PULSE_AI_ROUTER_URL` that appears nowhere else in
the repository; it is now an alias group over the six real keys.

---

## 6. Buy twice by accident

| Variable | Same vendor as | Note |
|---|---|---|
| `SENDINBLUE_API_KEY` | `BREVO_API_KEY` | rename artifact — Sendinblue became Brevo. Read at `services/email_service.py:48` |
| `CMC_API_KEY` | `COINMARKETCAP_API_KEY` | `bot.py:96460` reads `COINMARKETCAP_API_KEY or CMC_API_KEY` |
| `BING_SEARCH_V7_SUBSCRIPTION_KEY` | `BING_SEARCH_API_KEY` | two spellings of one Azure key |
| `BOT_TOKEN` | `TELEGRAM_BOT_TOKEN` | one BotFather token |
| `AWS_*` / `S3_*` | `R2_*` | one bucket, two naming conventions |

Consolidating these is not urgent, but anyone provisioning from `.env.example`
without this table would reasonably conclude they need two email vendors and
two market-data subscriptions.

---

## 7. What I recommend, and what I did not do

Nothing was purchased. Nothing could have been — the authorization for this
mission forbids buying paid APIs and forbids inventing secret values, and I
have no view of your billing accounts or your Railway variables either way.

If you are deciding where money goes, the order I would defend is: confirm the
seven **required** services in section 2 are live in Railway, because each one
has a user-visible failure mode and two of them (Postgres falling back to
ephemeral SQLite, R2 falling back to ephemeral disk) fail by losing data
quietly rather than by erroring. Then TURN, because NAT-relay failures are
invisible to everyone testing on a normal network and affect a real fraction of
users. Then nothing, for a while: the optional tier in section 5 all degrades
honestly, and the six in section 1 would be money spent on status lights.

The verification that remains yours: this document says what the code asks
for. Only the Railway variables page says what the code is getting.
