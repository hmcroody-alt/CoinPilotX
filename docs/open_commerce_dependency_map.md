# Open Commerce — internal dependency map

Phase Zero output for the guest-checkout / commerce-identity mission (§5), and the
shared recon for Marketplace 2.0 (§5, §17). Everything below was read from the
real repository or queried against **production** on 2026-09-27. Where a fact was
checked against prod, the query result is quoted.

This document exists because both missions forbid structural work before the map.
It is not a design. It records what is true today, and which of those truths the
guest path collides with.

---

## 0. Corrections to things previously believed

Two beliefs that were carried into this mission were wrong. Both changed the answer
to a mission question, so they are recorded first.

**Production is PostgreSQL 18.6, not SQLite.** `select version()` against
`DATABASE_PUBLIC_URL` returns `PostgreSQL 18.6 (Debian 18.6-1.pgdg13+2)`. SQLite is
the *local* engine only. This reopens Marketplace 2.0 §17 completely: Postgres FTS
(`tsvector`/`tsquery`, `pg_trgm`) and `pgvector` are both reachable without a
database migration, and must be evaluated on merit rather than dismissed.

**`mobile-native/android/` is not a committed Android project.** It exists in a
working copy but `git ls-files mobile-native/android/` returns 0 and
`mobile-native/.gitignore:1` is `/android/`. It is an `expo prebuild` artifact.
`android.package = com.pulsesoc.nativeapp` remains scaffold residue, and production
agrees: `/.well-known/assetlinks.json` answers **503**
`PULSESOC_ANDROID_SHA256_CERT_FINGERPRINTS must contain a valid SHA-256 certificate
fingerprint`, while `/.well-known/apple-app-site-association` answers **200** with
`87ZC69AGSR.com.pulsesoc.app`.

---

## 1. The wall, stated precisely

The mission says guest checkout is not `if (!user) allowCheckout()`. In this
codebase that is literally true, because for an anonymous visitor there is no
checkout to gate — and no cart, and not even the same page.

| surface | anonymous visitor gets | signed-in member gets |
|---|---|---|
| `/pulse/marketplace` | `marketplace_index_public.html`, cards built by `marketplace_seo.index_card` — **a different renderer** | the member grid via `marketplace_storefront.render_discovery`, with the cart affordance |
| `/pulse/marketplace/<id>` | `marketplace_product_public.html` — a read-only shell | the member product page |
| `/pulse/cart` | **302 to `/login`** (`@auth_required`, bot.py:58765) | the cart |

The public PDP's entire action area, fetched live from `/pulse/marketplace/14`:

```html
<a class="button primary" href="...?pulse_app=1&pulse_src=web"
   data-app-link="product">Open in the PulseSoc app</a>
<a class="button" href="/login?next=/pulse/marketplace/14">Sign in to add to cart</a>
<p class="meta">Signed in, you can add this to your cart here and message the seller.
  Paying happens in the PulseSoc app, where the same cart is waiting.</p>
```

So the web storefront is a **double gate**: sign in to add, install the app to pay.
That is the exact state §1 and §36 forbid. Removing it is not a button change; the
anonymous branch has no cart component to enable, so guest commerce means giving the
anonymous branch a real grid, a real cart, and a real checkout.

---

## 2. Orders — the §56 answer

**No migration is required on the live order table.** The blocker everyone expects
is not there.

Production, `information_schema.columns`:

| table | rows | buyer column | nullable? |
|---|---|---|---|
| **`seller_transactions`** (live) | 32 | `buyer_user_id integer` | **YES — already nullable** |
| `marketplace_orders` | **0** | `buyer_user_id integer` | NO |
| `business_os_mkt_orders` | **0** | `buyer_user_id text` | NO |

The two tables that would have blocked guest orders are both empty and are not the
live write path. The live table already permits a null buyer, and
`select count(*) from seller_transactions where buyer_user_id is null` returns **0**,
so no existing row depends on the column being populated.

**Blast radius is small and safe.** `buyer_user_id` appears 63 times in `bot.py` and
in 12 `services/` modules. There is exactly one join to `users` on it —
`bot.py:100652` — and it is a **LEFT JOIN**:

```sql
seller_transactions t
  LEFT JOIN users b  ON b.user_id = t.buyer_user_id
  LEFT JOIN users s  ON s.user_id = t.seller_user_id
  LEFT JOIN marketplace_sellers ms ON ms.user_id = t.seller_user_id
ORDER BY t.id DESC LIMIT 100
```

A guest order will therefore **render with a blank buyer, not vanish**. There is no
INNER JOIN anywhere that would silently drop guest rows out of a seller dashboard or
an analytics aggregate. That is the failure mode this audit existed to rule out, and
it is ruled out. Every read site still needs a display fallback ("Guest · a@b.com"),
but that is presentation, not data loss.

---

## 3. The cart — the real structural blocker

This is the one that does not fall out for free, and it is invisible in the
`CREATE TABLE`. From `pg_constraint` in production:

```
marketplace_cart_items_user_id_listing_id_key   UNIQUE (user_id, listing_id)
```

and `user_id` is **nullable**. In PostgreSQL, `NULL` is not equal to `NULL` for the
purpose of a unique constraint. So with `user_id = NULL` for every guest, the
constraint stops constraining: a guest adding the same listing twice creates a
**second row** rather than incrementing the first. The guest cart would silently
duplicate lines, and the badge — which sums `qty` — would silently inflate.

A guest cart therefore needs a genuinely non-null owner key, not a nullable
`user_id` plus a nullable `guest_session_id` (which reproduces the same defect on
the other column). The shape that actually holds is a single NOT NULL owner
discriminator, or two partial unique indexes each with a `WHERE ... IS NOT NULL`
predicate.

Related, from the same mission's §15 work already shipped: `marketplace_cart_items`
has **no `variant_id`**, and `POST /api/pulse/marketplace/cart` takes only
`listing_id` and `qty` — grep `marketplace_cart_routes.py` for "variant" and it
returns nothing. This is why the storefront withholds quick-add on a multi-variant
listing and offers "Choose options" instead. Guest checkout does not change that; it
inherits it.

---

## 4. Order addressing — §22 is violated today

There is no order-number or public-reference generator. `grep` for `order_number`,
`order_ref`, `reference_code`, `public_order_id` across `services/marketplace_*.py`
and `bot.py` returns nothing. Orders are addressed by the integer primary key —
`seller_transactions.id` is `nextval(...)`, so ids are sequential and guessable.

§22 requires that a guest order is not reachable at `/orders/1234`. Today every
order is. A guest order must be addressed by an opaque identifier, and the claim
link must be a signed, purpose-bound, expiring token rather than the id.

---

## 5. Identity

**Account model.** `users` is created sparse at `bot.py:919` (9 columns) and grown by
`add_columns_if_missing()` at `bot.py:115119-115214` on every boot. Nothing in the
table is `NOT NULL` except the primary key.

**`users.email` has no UNIQUE constraint and no index.** Production
`pg_indexes` for `users` returns only `users_pkey`,
`idx_users_roast_call_sign_slug`, `ux_users_pulse_id`. Collisions are caught by a
SELECT-then-INSERT in `create_account()` (`bot.py:6513`) — a race, not a constraint.
Email is the anchor the entire claim flow hangs on, so this matters.

It has never been cheaper to fix: production has **42 users, 0 duplicate emails, 4
null/blank**. A partial unique index on `lower(email) WHERE email IS NOT NULL AND
email <> ''` applies today with zero conflicts and becomes progressively harder
forever after.

**Email normalization** is `normalize_email()` at `bot.py:649` — `.strip().lower()`
and nothing else. No plus-address truncation, no gmail dot-stripping. §15 says reuse
it, and reuse is also the only safe option: adding canonicalization now would change
the identity of existing accounts.

**Sign-in methods that exist:** password (web `/login`, mobile
`/api/mobile/auth/login`), with real brute-force throttling. **OAuth does not exist
at all** — zero hits for `accounts.google.com`, `appleid.apple.com`, authlib,
oauthlib, flask_dance across `bot.py` and all of `services/`, and no
`expo-apple-authentication` or `@react-native-google-signin` in
`mobile-native/package.json`. Phone OTP exists but only as post-signup verification
of an already-authenticated account. Telegram links an identity, it does not
authenticate one. There are no magic links.

Consequence: "Continue with Google / Continue with Apple" is an identity subsystem,
not a button — and offering Google on iOS obliges Sign in with Apple alongside it.
The claim flow does not depend on either; it works against the password signup that
already exists. Sequence it after the guest path, not before.

**Sessions.** Web: Flask signed cookie, `HttpOnly`, `SameSite=Lax`, `Secure` from
env (`bot.py:539-541`), key derived via HMAC-SHA256 from the root secret
(`services/signing_keys.py`). Plus a `pulse_refresh_session` cookie holding a `psr_`
token, rotated on each request. Mobile: a custom HMAC-SHA256 token (not JWT),
900-second TTL, plus a `psr_` refresh token stored server-side as a SHA-256 hash in
`mobile_security_sessions`.

**An opaque anonymous identifier already exists.** `visitor_session_id` is
`secrets.token_urlsafe(18)` — 135 bits — generated in the `before_request` hook at
`bot.py:3256` and carried in the signed session cookie, with a `visitor_sessions`
row upserted by `upsert_visitor_session()` (`bot.py:14743`). §9 asks for a secure
opaque guest identifier; this is one, and reusing it satisfies §2's "never install
parallel infrastructure". Two caveats: it is gated off by
`PULSESOC_VISITOR_LOGGING_ENABLED`, and on login the upsert **overwrites `user_id`
without linking the prior anonymous row** — which is precisely the merge seam §11
needs to make deterministic.

---

## 6. Security — four live findings

All four are in production now, all four are named by the mission, none is
hypothetical.

**(a) Account-enumeration oracle, unauthenticated, unthrottled.**
`POST /api/mobile/auth/confirmation-status` (`bot.py:7569`) returns
`{"exists": <bool>}` for any address. Verified live against pulsesoc.com with a
nonexistent address: `{"confirmed":false,"email_verified":false,"exists":false,"ok":true}`,
HTTP 200, no auth. §16 and §79 forbid this. The only consumer is the signup
screen's polling loop (`mobile-native/src/components/auth/signup/VerifyEmailStep.tsx:60`),
which reads **only** `result.confirmed` — `exists` and `email_verified` are dead on
the client. `register` already returns a session, so the poll can be bound to the
pending signup's own session and the oracle closed without breaking the shipped
binary.

**(b) Two more enumeration surfaces.** Signup renders "An account already exists for
that contact method" (`bot.py:6513`), and login renders the distinct message "Please
confirm your email before logging in" (`bot.py:7190`), which separates *registered
but unverified* from *no such account* from *wrong password*. §16 requires a guest
buying with an already-registered email to complete the purchase without learning
the account exists.

**(c) Email bombing.** `POST /api/mobile/auth/resend-confirmation` (`bot.py:7542`)
has no decorator, no rate limit, no CSRF, and `resend_account_confirmation_by_email`
has no internal throttle. `ABUSE_GUARD_PROTECTED` (`bot.py:2984`) covers `/login`,
`/signup`, `/forgot-password`, `/forgot-username`, `/api/mobile/auth/recover`,
`/api/account/password/change-request`, `/admin/login`, `/create-checkout-session`,
`/api/create-checkout-session`, `/api/ai-assistant` — and not this. §78.

**(d) Cart and checkout are unrate-limited.** `services/marketplace_cart_routes.py`
contains no `rate_limited` and no `ABUSE_GUARD` reference. Note the nuance: the
*premium* `/create-checkout-session` **is** limited at 8/300; the *marketplace* cart
path is not. §77, and opening it to the anonymous public is what makes it urgent.

---

## 7. Transactional email

**Brevo only, no SMTP fallback.** `services/email_service.py:5` posts to
`https://api.brevo.com/v3/smtp/email`. The `smtplib` import and `/admin/test-smtp`
are diagnostics, never in the delivery path.

**A real durable outbox exists.** `failed_email_queue` carries `idempotency_key`,
`retry_count`, `max_attempts`, `next_retry_at`, `trace_id`, `last_error`, with
exponential backoff `min(3600, 30 * 2^(n-1))` (`bot.py:110909`), 5 attempts, and a
`dead_letter` terminal status (`bot.py:110985`). §29 and §30 have a foundation.

**But the default path is synchronous.** `send_platform_email` calls Brevo *inside
the request* and only enqueues on failure (`bot.py:111169-70`), passing no
idempotency key. `enqueue_platform_email` (`bot.py:110879`) queues directly and
accepts a key, but only two call sites use it. §28 wants the order write and the
email decoupled; the tool exists, the habit does not.

**No receipt template exists.** All email bodies are inline Python f-strings; the
shared chrome is `branded_email_html()` (`services/email_service.py:427`). The
`payment_confirmation` template is a premium-subscription acknowledgement, not an
itemized order receipt. §26 builds this from scratch.

**Consent is stored but never enforced.** `users.email_opt_in` and
`notification_email_opt_in` exist; nothing in `send_platform_email` or
`notification_service` reads them at send time. The `email_type` field is logged,
not gated. §27's separation must be made real, not assumed.

**§96 is a live hazard, and the safe pattern is already in the repo.**
`/verify-email/<token>` (`bot.py:13743`) is a **GET that consumes the token inline** —
`UPDATE email_verification_tokens SET used_at=?` at `bot.py:13757`, no confirmation
step, no POST. Any mail scanner that pre-fetches burns it. Tokens there are
`secrets.token_urlsafe(32)` but stored **in plaintext**, and creating a new one does
not invalidate the old, so multiple live tokens coexist for 24 hours. By contrast
`password_reset_tokens` stores an **HMAC hash**, expires in 1 hour, and consumes on
**POST** after a GET that only renders a form. A guest order-access link is a
credential, so it must follow the reset pattern, not the verification one.

---

## 8. Web-to-app

`services/app_links.py` (1,417 lines, real) is the canonical authority. It emits two
shapes deliberately: `build_app_link()` returns an absolute
`https://pulsesoc.com/pulse/...?pulse_app=1&pulse_src=...` universal link for
off-site surfaces such as email, and `open_interstitial_url()` returns a relative
`/open/<key>/<id>` path for links already on pulsesoc.com — because iOS does not
hand same-domain taps to associated domains, so an on-site universal link would
always fall through to the App Store even with the app installed.

`APP_STORE_FALLBACK_URL = https://apps.apple.com/us/app/pulsesoc/id6777591572` —
a real numeric Apple id. **There is no Play Store URL anywhere in the repo**, and as
established in §0 Android App Links cannot verify. App promotion is iOS-only today.

Three gaps against §36-40:

- The `order` destination is declared `web_equivalent=False` (`app_links.py:595`),
  so a receipt link to a specific order sends a desktop or Android visitor to the
  `app_only_destination.html` interstitial even though the Flask route renders. For
  guest order access this inverts the rule — it must be `True`.
- **Deferred deep linking does not exist.** The referral claim
  (`mobile-native/src/api/referral.ts`) fires once after first auth and carries no
  destination. A guest who installs from a receipt lands on home, not the order.
- `linking` is passed to `NavigationContainer` only when `signedIn`
  (`App.tsx:426`), so a deep link on a fresh unauthenticated install is dropped and
  never replayed after auth.

`templates/_app_link_cta.html` is the canonical macro (`app_cta`,
`app_store_badge`), used by `index.html`, `terms.html`, `support.html`,
`privacy.html`. Marketplace pages use a programmatic `.mkt-appcta` aside instead.

---

## 9. What the mockup asks for that does not exist

Recorded here so it is settled once, per Marketplace 2.0 §97 and the standing
real-data rule.

**Real, may render:** star ratings and review counts (`business_os_mkt_reviews`,
unique on `(buyer_user_id, order_id, product_id)`, verified-purchase gated), seller
ratings (`business_os_mkt_seller_ratings`), price and compare-at price, variant
option groups, Stripe card / Apple Pay / Google Pay, order confirmation, receipt
email, App Store link.

**Fiction — must not render:** "Best Seller" badge; follower counts on a seller;
"Free shipping / Orders over $50"; "Easy returns / 30-day policy"; "Buyer protection
/ Secure checkout"; "Ships within 2-4 days"; a `Shipping: Free` line; "Estimated
Tax"; a promo-code field. There is no shipping-rate engine, no returns-policy model,
no carrier integration and no tax calculation; `promo_code` appears once in the
entire codebase. A discount percentage may render only where a genuine compare-at
price exists.

---

## 10. Constraints that shape the rollout

**Feature flags are in-code only.** `services/feature_flag_engine.py` has no DB
backing, no Redis, no hot reload — a flag flip requires a redeploy. §102, §103 and
§104 must be designed around that: rollout is deploy-shaped, and rollback cannot
assume a runtime kill switch.

**Workers are polling loops.** There is no queue abstraction;
`email_worker.py` (45 lines) drains `bot.process_email_delivery_jobs()`. That is the
async email foundation and it is adequate, but it is the only one.

**No E2E harness.** No Playwright, no Cypress, no visual regression, no
accessibility linting. §106-112's matrices have nothing to run in; they will be
server-rendered assertion suites in the three-layer style
(`marketplace_web` → facts, `marketplace_storefront` → markup, routes → SQL/HTTP),
which boot in ~0.1s with no database.

**Scale.** Production is 42 users, 32 lifetime transactions, one active marketplace
seller, 11 eligible listings. Search is in-Python substring matching
(`matches_query`), load-all-then-slice, `PAGE_SIZE = 24`. Marketplace 2.0 §80 asks
for 10K-1M products; §17's REUSE/BUILD/ADOPT analysis is now a Postgres-native
question, not an external-engine one.

---

## 11. Sequence implied by the above

1. **Security hardening** — close (a), (b), (c), (d) in §6. Small, live, and §77-79
   put them in scope regardless of guest checkout.
2. **`users.email` partial unique index** — 0 conflicts today, never cheaper.
3. **Guest commerce session + cart ownership key** — the §3 cart constraint is the
   one true structural blocker; nothing downstream is safe until a guest cart can
   hold a line correctly.
4. **Guest checkout against `seller_transactions`** — no migration needed, plus
   display fallbacks at every read site.
5. **Opaque order addressing + signed, hashed, expiring, POST-consumed claim
   tokens** — §21-23, following `password_reset_tokens`, never
   `email_verification_tokens`.
6. **Receipt email** — `enqueue_platform_email` with an idempotency key, built on
   `branded_email_html`.
7. **Claim → account**, then optional app handoff.
