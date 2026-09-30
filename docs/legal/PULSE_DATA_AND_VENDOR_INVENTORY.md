# PulseSoc — Personal Data, Data Flow and Vendor Inventory

Mission PULSE LEGAL §11–§14. Companion to `PULSE_LEGAL_SURFACE_AUDIT.md`.

**What this document is.** An inventory of the personal data PulseSoc actually holds,
where it goes, and which third parties receive it — assembled by reading the source,
not by describing what a social-commerce platform would plausibly do. Every table,
column and vendor named below was read at the cited location.

**What this document is not.** It is not a privacy policy, and it is not legal advice.
It is the factual basis a privacy policy has to be written *from*. Several findings
below are, on their face, compliance problems; they are recorded as findings with
evidence, and the question of what obligation attaches to each is marked
`LEGAL COUNSEL REVIEW REQUIRED`.

**Method note, and why it matters for how much you trust this.** A first pass at this
inventory was produced by a broad automated sweep. Spot-checking it against source
found three substantive errors, each in the same direction — the sweep reported the
*reassuring* answer:

* it reported that no raw IP addresses are stored, only hashes. Three tables have a
  raw `ip_address TEXT` column (`auth_events`, `visitor_logs`, `security_events`).
* it reported that no shipping or billing addresses are stored anywhere. Buyer street
  addresses are stored, in a JSON column on a financial table.
* it reported, as the single most critical finding, that a third-party model provider
  trains on user content. The source deliberately does the opposite and says why.

So the findings below are the ones that survived verification. Where a claim was not
verified to source, it is labelled. Anything unlabelled was read.

---

## §11 — Personal data inventory

Schema is created imperatively in `bot.init_db()` (there is no migration framework),
with more in `services/` modules. Tables holding no personal data — config, feature
flags, caches of public market data — are omitted.

### 11.1 Identity and account (`users`, `bot.py:120591`)

`username`, `display_name`, `email`, `password_hash`, `full_name`, `phone` /
`phone_number`, `date_of_birth`, `country`, `bio`, avatar/banner URLs,
`recovery_email`, `recovery_phone` (`bot.py:120647-120648`), `telegram_user_id` /
`telegram_username` / `telegram_chat_id`, `stripe_customer_id` /
`provider_customer_id`, `age_confirmed`, `email_verified`, the four opt-in flags,
`account_status`, `deleted_at`, `security_score`, `trust_level`.

Passwords are stored as a hash only. No plaintext password column exists.

Adjacent: `user_verifications` (`bot.py:120790`), `user_trusted_devices`
(`bot.py:120771`), `sms_verification_codes` (`bot.py:120938`), and the token tables
(`password_reset_tokens` `bot.py:121383`, `email_verification_tokens`
`bot.py:121399`, `account_recovery_tokens` `bot.py:121447`).

### 11.2 Staff and administrator personal data (`admin_users`, `bot.py:127475`)

A data-subject class distinct from members, and easy to miss. `admin_users` holds
`date_of_birth`, `address_line1`, `address_line2`, `city`, `state`, `zip_code`,
`country`, `job_title`, `emergency_contact_name`, `emergency_contact_phone`, `notes`
— written by the admin edit form at `bot.py:19892-19917`.

This is employment-context personal data (home address, date of birth, next of kin).
A privacy policy addressed to members does not cover it.
`LEGAL COUNSEL REVIEW REQUIRED` — whether a separate staff privacy notice is owed.

### 11.3 Buyer contact and address data — stored in a JSON column on a financial table

This is the finding the automated sweep inverted, and the reason it did: there is no
`addresses` table and no address column to grep for.

`services/marketplace_fulfillment.py:297-323` defines the buyer fields per fulfilment
lane. For `shipping`, `service_in_person` and `booking_in_person` that is
`contact_name`, `contact_phone`, `address_line1`, `address_line2`, `address_city`,
`address_region`, `address_postal_code`, `address_country`, plus free-text
`delivery_notes` / `notes`. For `event_*` lanes it is `attendee_name`.

`snapshot()` (`services/marketplace_fulfillment.py:497-504`) freezes that dictionary
into **`seller_transactions.metadata_json`**, written at
`services/marketplace_cart_routes.py:1185-1192`. Its own docstring states the intent:
"so an order read back next year still says where it was going".

Two consequences worth stating plainly, because they follow from the storage choice
rather than from anyone's decision about retention:

1. The buyer's street address and phone number live on the **payments ledger**, not in
   a profile table. Anything that retains financial records for accounting or tax
   purposes retains the home addresses inside them by the same rule.
2. It is invisible to schema inspection. Any data map, retention sweep, or deletion
   routine built by looking at column names will miss it — as the first pass here did.

`LEGAL COUNSEL REVIEW REQUIRED` — retention period for buyer addresses held inside
transaction records, and whether they may be retained on the same schedule as the
financial data they are embedded in.

### 11.4 Payment data

**No raw card or bank details are stored in PulseSoc's own tables.** Verified across
the payment surface: what is held is Stripe identifiers and amounts.

`seller_payout_accounts` (`bot.py:120271`) holds `connected_account_id`,
`onboarding_status`, `payouts_enabled`, `charges_enabled`, `requirements_json`.
`creator_wallets` (`bot.py:120235`), `creator_transactions` (`bot.py:120291`,
carrying `provider_payment_id` / `provider_checkout_id` / `provider_transfer_id`),
`creator_payouts` (`bot.py:120315`), `payout_queue` (`bot.py:120456`),
`subscriptions` (`bot.py:120357`), `seller_transactions`, and the ad-billing tables
(`pulse_ad_billing_profiles`, `bot.py:124777` onward) follow the same pattern.

`payment_webhook_events` (`bot.py:120372`) stores `raw_json` — the Stripe webhook
payload as received. Not verified: whether any retained payload contains more
personal data than the platform otherwise holds.

`creator_tax_profiles` (`bot.py:120521`) holds `tax_status`, `tax_form_type`,
`provider_reference` and a masked identifier column. Card collection on iOS is
client-side via `@stripe/stripe-react-native`, so card numbers never reach our
servers.

### 11.5 User-generated content

Posts `pulse_posts` (`bot.py:121687`: `body`, `media_ids_json`, `tags_json`,
`visibility`, plus derived `ai_summary`, `sentiment`, `risk_score`,
`engagement_score`). Reels `pulse_reels` (`bot.py:121815`). Comments
`pulse_comments` (`bot.py:122293`) and reactions (`bot.py:122318`, `122328`). Live
sessions `pulse_live_sessions` (`bot.py:123861`) and per-viewer join/leave rows
`pulse_live_viewers` (`bot.py:123989`). Saved collections (`bot.py:121760`,
`121782`). Listings `marketplace_listings` (`bot.py:122869`). Seller-uploaded
identity/business documents `marketplace_merchant_documents` (`bot.py:122846`:
`original_filename`, `stored_path`, `mime_type`).

Media binaries are not in the database; the tables hold keys and URLs, and the object
itself is in R2/S3 (§14).

### 11.6 Private messages — plaintext, and no application-layer encryption

`pulse_messages.body TEXT` (`bot.py:48557`, and a second narrower definition at
`bot.py:122501`). No cipher, key-id, or envelope column exists on the table.
Attachments are `message_attachments` (`bot.py:121600`). Conversations and
participants at `bot.py:122535` / `122565`; receipts at `bot.py:122605`.

Direct message content is therefore readable to anyone with database access. Note
this against `project_app_store_screenshots_claim_e2e_encryption` — the App Store
screenshots assert end-to-end encryption, which the schema does not support. The web
side of that claim was corrected in `4e4e6a5e`; the screenshots were not.
`OWNER DECISION REQUIRED` — the screenshots, tracked separately.

### 11.7 Device and technical identifiers — three registries, not one

This matters for §13, because deletion only clears one of them.

| Registry | Location | Holds |
|---|---|---|
| `user_device_tokens` | `bot.py:125828` | `device_id`, `push_token`, `push_provider`, `environment`, `app_version`, `device_label` |
| `push_subscriptions` | `bot.py:125816` | web push `endpoint`, `subscription_json`, `p256dh`, `auth`, `user_agent` |
| `notification_device_tokens` | `services/pulsesoc_notification_system.py:408` | `device_id`, `push_token`, `endpoint`, `p256dh`, `auth`, `user_agent`, `app_version` |

Plus `sessions` (`bot.py:121319`: `session_id`, `user_agent`, `referrer`,
`landing_page`, `utm_*`) and `pulse_online_sessions` (`bot.py:124429`).

### 11.8 IP addresses — raw, not only hashed

Three tables have a raw `ip_address TEXT` column:

* `auth_events` — `bot.py:6534` and `bot.py:128908`
* `visitor_logs` — `bot.py:127616`
* `security_events` — `bot.py:128627` and `bot.py:129040`

`ip_hash` columns also exist widely (`analytics_events.ip_hash` `bot.py:121312`,
and others). The two are not alternatives; both are in use. Per a prior read-only
production introspection recorded in project memory (2026-09-13, cited here as
prior work rather than re-verified): `visitor_logs.ip_address` held ~207k sha256
hashes despite its name, `security_events.ip_address` held a *mixture* of ~3.3k raw
IPv4 and ~674 hashes written by two different code paths, and `auth_events.
ip_address` held ~188 distinct plaintext addresses. The migration plan is
`docs/web-rebuild/PULSESOC_IP_HASH_MIGRATION_PLAN.md`.

For the privacy policy the operative fact is simply: **PulseSoc stores IP addresses,
some in clear.** A policy saying IPs are only ever held in hashed form would be
inaccurate.

### 11.9 Location data

No latitude/longitude, geohash or precise-location column was found. Location is held
at administrative granularity only: `users.country`, `analytics_events.country`
(IP-derived), `marketplace_sellers.country` / `state_region`, and buyer
`address_*` fields per §11.3 — which are precise by nature, being street addresses.

### 11.10 Special-category data

No biometric, health, ethnicity, religion, sexual-orientation or political-opinion
column was found. No government identifier is stored in clear; `creator_tax_profiles`
holds a masked identifier. `marketplace_merchant_documents` stores uploaded identity
documents as *files* (`stored_path`), with no extracted fields in the database — so
whether special-category data is held depends on what sellers upload, which the
schema cannot tell us. `LEGAL COUNSEL REVIEW REQUIRED`.

### 11.11 Children's data and the age gate

`users.age_confirmed` is a single flag; `users.date_of_birth` is free-text `TEXT`.
No parental-consent mechanism and no per-jurisdiction minimum-age logic was found.
`OWNER DECISION REQUIRED` — the minimum age (already open in the surface audit), and
whether a self-asserted flag is sufficient for the jurisdictions being shipped to.

### 11.12 Behavioural and ad-targeting data

`analytics_events` (`bot.py:121303`: `session_id`, `user_id`, `event_name`,
`page_url`, `referrer`, `device_type`, `browser`, `ip_hash`, `country`, free-form
`metadata`). `engagement_events` (`bot.py:121074`) including a `query` column, so
search terms are retained. `conversion_funnel_events` (`bot.py:121333`).
`pulse_post_views` (`bot.py:122362`) with `dwell_ms` per view, keyed by `user_id`
*or* an anonymous `visitor_id`. Reel retention events (`bot.py:124167`).

Advertising: `pulse_ad_impressions` (`bot.py:124636`), `pulse_ad_clicks`
(`bot.py:124652`), `pulse_ad_events` (`bot.py:124665`), `pulse_ad_frequency_caps`
(`bot.py:124675`), `pulse_ad_saved_audiences` (`bot.py:125076`).

AI interaction logs: `user_ai_interactions` (`bot.py:121471`) stores `prompt` and
`response`; `ai_messages` (`bot.py:121528`) stores `content`; `ai_chat_history`
(`bot.py:121143`); `command_history` (`bot.py:121482`) stores `input`.

### 11.13 Inferred and derived data

`pulse_posts.ai_summary` / `ai_tags_json` / `sentiment` / `risk_score` /
`engagement_score`; `pulse_reels.safety_score` / `reel_score`;
`users.security_score` / `trust_level`; `reputation_ledger` (`bot.py:120916`);
`pulse_creator_audience_segments` (`bot.py:124179`); the
`global_intelligence_nodes` / `_edges` graph (`bot.py:120804`, `120818`) carrying
`trust_score` / `influence_score`. No embedding-vector column was found.

Note for the policy: profiling is not hypothetical here. A per-user trust score and
per-post risk score are computed and stored.

---

## §12 — Data flow map

Read as: subject → what leaves the platform → who receives it. Vendor detail in §14.

```
MEMBER (web / iOS)
  ├─ account + profile ────────────────► own database only
  ├─ email / phone ───────────────────► Brevo (verification, password reset, receipts)
  ├─ push registration ───────────────► APNs (iOS) / FCM (Android) / browser push service
  ├─ posts, reels, comments ──────────► own DB; media binaries ► Cloudflare R2 / S3
  │    └─ translation of that text ───► Google Cloud Translation v3
  ├─ direct messages ─────────────────► own DB, PLAINTEXT. Not sent to any AI provider
  │                                      (verified: no undx/pulse_ai call site reads
  │                                       pulse_messages)
  ├─ live audio/video ────────────────► Agora RTC (from device) ──► Mux (RTMP, for
  │                                      distribution/VOD/replay)
  ├─ behavioural events ──────────────► own DB only (no third-party analytics SDK found)
  ├─ AI features (opt-in surfaces) ───► OpenAI / Claude / Gemini / DeepSeek / Meta /
  │                                      Perplexity, per §14.3 privacy ceiling
  ├─ web search behind an AI answer ──► Brave / Bing / SerpAPI / Tavily / DuckDuckGo
  │                                      (whichever is keyed; query text leaves)
  └─ linked Telegram account ─────────► Telegram Bot API

BUYER
  ├─ card details ────────────────────► Stripe, direct from device. Never touches us
  └─ name, phone, street address ─────► own DB inside seller_transactions.metadata_json
                                         AND ► Stripe (as the Checkout shipping object)
                                         AND ► visible to the seller

SELLER
  ├─ identity / business documents ───► own DB metadata + R2/S3 object
  └─ Connect onboarding identity ─────► Stripe

STAFF / ADMIN
  └─ home address, DOB, next of kin ──► own database only (admin_users)
```

Two properties of this map are worth calling out for the policy, because they are
better than a reader might assume and should be stated accurately rather than
hedged:

* **No third-party analytics or attribution SDK is present.** Behavioural events go
  to our own tables. (Mux Data viewer analytics is gated behind
  `MUX_DATA_ANALYTICS_ENABLED`; not verified whether it is on in production.)
* **Private messages are not sent to any AI provider.** No call site in the AI layer
  reads the message tables. A member's DM content reaches a model only if they
  paste it into an AI surface themselves.

---

## §13 — Retention, deletion, and access rights

This section is the most serious in the document.

### 13.1 The web deletion path works, and is incomplete

`bot.permanently_delete_account()` (`bot.py:7293`) requires password re-entry, then:

* anonymises `users`: blanks `username` → `deleted-user-{id}-{rand}`, `display_name`,
  `full_name`, `email`, `password_hash`, `phone`, `phone_number`, `country`, `bio`,
  avatar/banner URLs; clears Telegram and Stripe customer ids; sets
  `account_status='deleted'`, `deleted_at`, and all four opt-in flags to 0;
* `DELETE`s from exactly four tables: `push_subscriptions`,
  `password_reset_tokens`, `email_verification_tokens`, `telegram_link_codes`;
* soft-deletes content in `pulse_posts`, `pulse_status`, `pulse_comments`,
  `pulse_group_posts`, `pulse_group_post_comments`.

**Two contact channels survive it.** `users.recovery_email` and
`users.recovery_phone` exist (`bot.py:120647-120648`) and are **not** in the update
dictionary. A deletion that blanks `email` and `phone` while leaving a working
recovery email and recovery phone has not removed the member's contact details. This
looks like the ordinary consequence of columns being added after the routine was
written, not a decision — the routine already guards every write with
`if column in user_columns`, so it was built to tolerate schema change, and these
two columns simply were never added to the list.

**Three of the four device registries survive it.** Only `push_subscriptions` (web) is
deleted. `user_device_tokens` and `notification_device_tokens` keep `push_token`,
`device_id` and `user_agent`, with `enabled` still 1 and `revoked_at` still NULL — so
to every reader they are indistinguishable from a live device. No `account_status`
filter exists anywhere in the push path (checked `services/push_service.py` and
`services/pulsesoc_notification_system.py`). Not verified: whether a notification is
ever actually addressed to a deleted account.

**Correction to an earlier count in this document: there are four registries, not
three.** `pulse_notification_devices` (`bot.py:123823`) was missed on the first pass
because it is written by `services/notification_service.py:1656` rather than by either
push service, and it is the most serious of the four: it stores the *entire* web-push
subscription in `subscription_json` — the actual delivery credential, not a preview —
alongside `user_agent`, and its two unsubscribe paths
(`notification_service.py:2341,2350` and `push_service.py:514,599`) only set
`active=0`. `active` defaults to 1. A row left behind by deletion is therefore an
addressable device, marked live, belonging to an account that no longer exists.

It was found by asking the schema rather than by reading the routine: any table
carrying both a `user_id` and a `push_token`/`endpoint` column *plus* a device or
subscription column is a per-user push registry. That query returns four tables. The
same query is now the gate (below), so a fifth is covered the day it appears.

**Also untouched:** `pulse_messages.body` (the member's DMs remain, readable by the
other participant and by anyone with DB access), buyer addresses inside
`seller_transactions.metadata_json`, `auth_events` / `visitor_logs` /
`security_events` IP rows, `analytics_events`, ad impressions and clicks,
`user_ai_interactions.prompt` / `.response`, `sms_verification_codes`,
`user_trusted_devices`, `account_recovery_tokens`.

Some of that is legitimately retained (financial records, security logs). Some of it
plainly is not. Which is which is `LEGAL COUNSEL REVIEW REQUIRED`; the point here is
that the current behaviour is not the result of anyone having drawn that line.

### 13.2 The iOS deletion path does nothing after the grace period — P0

`POST /api/pulse/mobile/settings/delete-account`
(`services/pulse_settings_routes.py:1210`) requires the member to type `DELETE`,
then inserts a row into `pulse_account_data_requests` with `status='pending'` and
`scheduled_for = now + 30 days` (`DELETION_GRACE_DAYS`, line 73), and replies:

> "Your account is scheduled for deletion on {date}. Sign out to finish — signing
> back in before then will cancel it."

The **cancel** side is wired: `cancel_pending_deletion()` is called from the sign-in
path at `bot.py:1509-1511`, exactly as its docstring says it must be.

**The execute side is not wired.** Nothing reads
`pulse_account_data_requests WHERE status='pending' AND scheduled_for <= now`.
Grepping the table name across every `.py` in the repository returns
`services/pulse_settings_routes.py` and its test file, and nothing else — no worker,
no cron, no admin queue. `permanently_delete_account()` is reachable only from the
web password-confirmation flow, and nothing calls it from here.

So an iOS member is given a specific date on which their account will be deleted,
that date passes, and nothing happens. The account, its messages, its addresses, its
device tokens and its behavioural history all remain. The member has been told
otherwise, in writing, by the app.

This is a defect of the same shape as D-L2 (signup demanded agreement and discarded
it) and D-L4 (the Privacy Center wrote preferences it never read): a surface that
represents a state the backend does not hold. It is not a legal judgement — the
product already decided what deletion means and said so to the user.

**It is nonetheless not a fix I should land unilaterally.** The missing component is a
background job that irreversibly destroys member accounts on a timer. Writing that
without the owner present, against a production database, is exactly the kind of
high-blast-radius action to escalate rather than perform. It also needs the §13.1
line — *what* gets deleted — drawn by counsel first, or the worker will faithfully
execute an incomplete deletion at scale.

Recommended, for owner approval: (a) as an immediate mitigation, correct the iOS copy
so it promises only what happens — a request has been recorded and will be processed
by support; (b) then build the worker, with the deletion scope agreed in 13.1.

### 13.3 The data export promises an email nobody sends — P0

`POST /api/pulse/mobile/settings/data-export`
(`services/pulse_settings_routes.py:1169`) inserts an `export` row into the same
`pulse_account_data_requests` table and replies:

> "Export requested. We'll email a download link to {email} when it's ready."

Nothing reads those rows either. No archive is assembled and no email is sent. A
member exercising what is, in several jurisdictions, a statutory access right
receives an affirmative promise of fulfilment and no fulfilment.

`LEGAL COUNSEL REVIEW REQUIRED` — the response deadline that attaches to this
request, per jurisdiction, and whether the recorded pending rows already represent
requests that are out of time. There may be live requests sitting in this table in
production right now; that should be checked read-only before anything else.

### 13.4 No expiry or retention sweep

No TTL, purge or retention job was found for any personal-data table. Expiring
tokens (`password_reset_tokens`, `email_verification_tokens`,
`sms_verification_codes`) carry `expires_at` and are validated against it at use
time, but are never deleted. `analytics_events`, ad impressions/clicks,
`pulse_post_views`, AI prompt logs and IP logs accumulate indefinitely.

`LEGAL COUNSEL REVIEW REQUIRED` — a retention schedule. There is currently no
retention period to disclose, because there is no retention behaviour.

### 13.5 Who can read member data

Admin surfaces reaching personal data, all permission-gated
(`require_admin_page` / `require_admin_api`):

* `/api/admin/users/<user_id>` (`bot.py:15130`) — profile including email, phone,
  country, Telegram id, Stripe ids, payment history.
* `/admin/users/<user_id>` (`bot.py:23776`) and `/edit` (`bot.py:23887`).
* `/admin/users/export.csv` (`bot.py:15003`), `/admin/transactions/export.csv`
  (`bot.py:18883`), `/admin/audit-logs/export.csv` (`bot.py:19850`).
* `/admin/emails` (`bot.py:18968`) — email log recipients and subjects.

No admin route was found that reads private message bodies. Access is role-gated but
not row-scoped: an admin with `users.view` can view any member.

---

## §14 — Vendor and subprocessor inventory

Only vendors with a verified live call site. A key in `.env.example` is not an
integration — `services/stripe_service.py` is an 11-line status stub that makes no
Stripe calls, while the real Stripe work is in `services/payment_provider.py`.

### 14.1 Live subprocessors

| Vendor | Purpose | Personal data received | Side | Evidence |
|---|---|---|---|---|
| **Stripe** | Payments, Connect payouts | Card data (device→Stripe, never ours); buyer name/phone/shipping address; seller identity for Connect; amounts | Both | `services/payment_provider.py:15,231,260,298,436`; `mobile-native/package.json` `@stripe/stripe-react-native` |
| **Agora** | Realtime audio/video, live ingest | Live audio and video; channel name; numeric uid | Both | `services/agora_media_push_service.py:36-98`; `react-native-agora@4.6.2` |
| **Mux** | Live distribution, VOD, replay | Audio/video bridged from Agora; playback ids | Server | `services/mux_live_service.py:16-17,128-152,185-220` |
| **Brevo** | Transactional email + SMS | Email address, phone number, name, message body | Server | `services/email_service.py:193-300`; `services/sms_service.py` |
| **Apple (APNs)** | iOS push | Device push token, notification title/body | Server | `services/pulsesoc_notification_system.py:2565-2600` |
| **Google (FCM)** | Android push | Device push token, notification title/body | Server | `services/pulsesoc_notification_system.py:2506-2562` |
| **Browser push services** | Web push | Endpoint, `p256dh`/`auth`, payload | Server | `services/push_service.py` (`pywebpush`) |
| **Google Cloud Translation v3** | Translating user content | The text being translated — posts, captions, and any other content routed to it | Server | `services/translation_providers.py` |
| **Cloudflare R2 / AWS S3** | Media object storage | Uploaded photos, video, audio, seller documents | Both (public read) | `services/media_storage.py:110-182` |
| **Telegram** | Bot notifications | Chat id, message content — linked accounts only | Server | `bot.py` ~`31813-31820`; `services/alert_engine.py` |
| **Apple App Store Server API** | IAP verification | Transaction ids, bundle id | Server | `services/pulse_apple_server_api.py:38-39` |
| **CoinGecko** | Crypto market data | No personal data (asset symbols; our server IP) | Server | `services/coingecko_client.py:29-30` |
| **Railway** | Hosting + Postgres | All of it, as processor | — | deployment |
| **Redis** | Cache | Cached responses, rate counters | Server | `services/cache_engine.py`; optional |

### 14.2 Web search providers — query text leaves the platform

`services/pulse_ai_web_search.py` will call whichever is keyed: Brave (`:201-220`),
Bing (`:223-241`), SerpAPI (`:244-261`), Tavily (`:264-281`), falling back to
DuckDuckGo (`:284-299`, no key needed, so this path is always available).

The search string derived from a member's question leaves the platform. Not verified:
which of these is keyed in production. `SERPAPI_API_KEY` and `TAVILY_API_KEY` appear
to be absent from `.env.example` despite being read — worth confirming against the
env-contract gate.

### 14.3 AI model providers

Configured in `undx_router.py:91-168`:

| Provider | Model default | Enable gate | Line |
|---|---|---|---|
| OpenAI | `gpt-4o-mini` | — | 91 |
| Claude (Anthropic) | `claude-haiku-4-5` | — | 105 |
| Gemini | `gemini-flash-lite-latest` | — | 131 |
| DeepSeek | `deepseek-chat` | — | 138 |
| Groq | `llama-3.1-8b-instant` | — | 146 |
| Meta Muse | `muse-spark-1.3` | `META_MUSE_ENABLED` | 152 |
| Perplexity | `sonar` | `UNDX_PERPLEXITY_ENABLED` | 165 |

Perplexity is the grounded-research lane — it answers from a live search and returns
sources (`undx_router.py:157-159`). It is *not* an embeddings provider; the first
pass at this inventory had that wrong.

**Routing is privacy-classified, not open.** `undx_privacy` assigns each provider a
ceiling and the router refuses a request whose classification exceeds it. Per project
memory, `UNKNOWN_CLASS_RANK` resolves an unrecognised class name to the *most*
restrictive rank — fail-closed for a request, but permissive if used as a ceiling.
Worth a dedicated check before the policy describes this as a guarantee.

**The Meta Contributor question, stated correctly.** `undx_router.py:147-156` pins
the Standard-tier `muse-spark-1.3` and gives the reason in the source: the Contributor
variant is far cheaper "because Meta trains on its inputs and outputs, which is not a
trade PulseSoc user content can make." So the platform has already made the protective
choice and recorded why.

The residual risk is narrower, and real: the model is chosen by the `META_MUSE_MODEL`
environment variable. Setting it to the Contributor variant would begin sending user
content to Meta for model training, with no code change, no review, and nothing in the
product saying so. That is a configuration control standing in for a policy control.
`OWNER DECISION REQUIRED` — whether `META_MUSE_MODEL` should be pinned in code or
guarded by a test, so the protective decision already made cannot be undone by an
environment variable.

**What member content reaches a model.** Verified: not direct messages, and not
private posts — no AI call site reads the message tables. Content reaches a provider
when a member uses an AI surface: the UNDX chat route, Scam Shield (where the member
pastes suspect text themselves, classified `CONFIDENTIAL`), and the public sports
lane. Not verified: the full set of AI entry points on the consumer surface, which
needs its own pass before the policy enumerates them.

### 14.4 Named in config but not integrated — do not disclose as subprocessors

No call site found for: `ALPHA_VANTAGE_API_KEY`, `BLOCKSTREAM_BASE_URL`,
`BANUBA_TOKEN`, `AP_NEWS_API_KEY` (the news service reads RSS, not an API).
`GOOGLE_SITE_VERIFICATION` is a meta tag, not a processor. **LiveKit** is retired —
no package, no call site (the stale reference in
`config/realtime-audio-protected-paths.json:445` is already documented in CLAUDE.md).

Over-disclosing a vendor is its own accuracy problem: it describes a data flow that
does not exist.

---

## Gap summary from §11–14

| # | Gap | Severity | Class |
|---|---|---|---|
| D-P1 | iOS deletion is scheduled, promised by date, and never executed | **P0** | Technical defect; fix needs owner approval (destructive worker) |
| D-P2 | Data export promises an emailed archive; nothing fulfils it | **P0** | Technical + statutory deadline question |
| D-P3 | Deletion leaves `recovery_email` / `recovery_phone` intact | High | **RESOLVED** — §106 technical fix |
| D-P4 | Deletion clears 1 of 4 device registries; tokens stay `enabled=1` | High | **RESOLVED** — §106 technical fix |
| D-P5 | No retention schedule or expiry sweep for any personal data | High | Needs counsel before code |
| D-P6 | Buyer street addresses inside `seller_transactions.metadata_json` | High | Needs counsel (retention + discoverability) |
| D-P7 | DMs stored plaintext while App Store screenshots claim E2E encryption | High | Owner decision (tracked) |
| D-P8 | Raw IP addresses in three tables; mixed raw/hashed in one column | Medium | Migration plan already exists |
| D-P9 | `admin_users` holds staff home address, DOB, next of kin, with no staff notice | Medium | Needs counsel |
| D-P10 | `META_MUSE_MODEL` can enable training on user content via env var alone | Medium | Owner decision |

D-P3 and D-P4 were the two clean §106 technical fixes here — narrow, verifiable, and
requiring no legal judgement, because the deletion routine already decided that
contact details and push registrations go, and simply missed columns and tables added
after it was written.

### D-P3 / D-P4 — RESOLVED. Technical fix only, no legal judgement.

`bot.permanently_delete_account` now clears seven further `users` columns and deletes
from three further tables.

**Columns added to the update dictionary.** `recovery_email` and `recovery_phone` were
the reported defect. Fixing only those two would have left the same class of bug in
place, so the whole `users` column list was read (`bot.py:120624-120722`) and five more
personal-data columns were found missing from the routine: `date_of_birth`,
`social_links_json`, `expertise_tags_json`, `roast_call_sign`, `roast_call_sign_slug`.
All seven are now cleared. Each is still written through the routine's pre-existing
`if column in user_columns` guard, so a deployment whose schema lacks one of them is
unaffected — no new failure mode.

**Tables added to the delete loop.** `pulse_notification_devices`,
`user_device_tokens`, `notification_device_tokens`. `push_subscriptions` was already
there. `table_columns` returns `[]` for a table that does not exist, and the loop skips
any table without a `user_id`, so this too cannot fail on a deployment missing one.

**Deliberately left alone, and why.** `referral_code` and `referred_by` (clearing them
would rewrite another member's referral history, which is not this member's data to
erase); `pulse_id` (the permanent internal identity minted by `pulse_id_service`,
which is what lets the soft-deleted row stay referentially intact); and the billing,
subscription and moderation columns (`subscription_*`, `payment_*`, `stripe_*`,
`restricted_reason`, `suspended_reason`). Whether a deleted account may retain a
billing history is a retention question — it belongs to D-P5 and to counsel, not to
this fix. Nothing here was decided by code.

**The guard.** `tests/test_account_deletion_removes_contact_data.py`, 6 tests,
registered in `config/ci_test_manifest.json`. Neither half names what it checks,
because a test listing `recovery_email` would have passed the day before that column
existed and gone on passing afterwards — which is exactly how this defect got in:

* every live `users` column whose name is drawn from a contact/identity vocabulary is
  seeded with a sentinel, and no sentinel may survive deletion. Matched on whole
  `_`-separated name parts rather than substrings, after `hidden_from_discovery`
  (dis**cover**y) was caught as a false positive;
* every table matching the push-registry shape is seeded with a row, and no row may
  survive. A future fifth registry fails here rather than silently retaining a token.

Two named tests sit alongside the two sweeps, covering `recovery_email`/`recovery_phone`
and `pulse_notification_devices.subscription_json` explicitly, so that a later
narrowing of the vocabulary or the shape heuristic — which would quietly shrink a sweep
toward vacuity — still fails on the cases actually reported. A seventh assertion checks
the fixture seeded something at all, and that at least four registries were detected.

**Scope limit, stated so it is not mistaken for more than it is.** This gate covers
contact details and device registrations. It does not assert that deletion empties the
row, and it makes no claim about D-P1 — **an account deletion requested through the iOS
app still never executes.** This fix improves what `permanently_delete_account` does
when it runs; it does not make it run.
