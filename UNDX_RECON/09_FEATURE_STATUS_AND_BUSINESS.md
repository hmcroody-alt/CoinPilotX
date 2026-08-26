# Stage 9 & 11 — Feature Status and Business Knowledge

Read-only recon of `CoinPilotX` / PulseSoc. Every claim is cited as `file:line` or
`table=rowcount` (row counts read from `coinpilotx.db`, 776 tables, opened read-only).
Where a `*_REPORT.md` claim conflicts with the current code, the **code wins** and the
conflict is recorded in "CONFLICTS: report vs. code" at the end of Part 1.

Status buckets used:

- **PRODUCTION READY** — code complete, flag on, and real (non-fixture) rows present.
- **PARTIALLY READY** — core path implemented, but gated to beta/internal, or with an
  empty telemetry/companion table showing a sub-feature never runs.
- **UNDER DEVELOPMENT** — schema and/or routes exist, but the feature is dark by default
  (flag defaults off, or the DB is empty across the whole vertical).
- **PLANNED** — declared in a registry/roadmap with `backed:false` or `COMING_SOON`;
  no route, no screen, or no state machine.
- **BROKEN / DEAD CONTROL** — shipped to users but structurally incapable of working.
- **DEPRECATED** — intentionally removed from the product surface, code retained.

---

## PART 1 — FEATURE STATUS

| FEATURE | STATUS | EVIDENCE | CONFIDENCE |
|---|---|---|---|
| Home feed (Pulse posts) | PRODUCTION READY | `pulse_posts=1140`, `pulse_post_media=372`, `pulse_reactions=1044`, `pulse_comments=232`; rollout flag `pulse_posts` = enabled @ 100% | High |
| Stories / Status | PRODUCTION READY | `pulse_status=965`, `pulse_status_media=490`, `pulse_status_views=1263` | High |
| Direct messaging (v1 + v2) | PRODUCTION READY | `pulse_messages=2108`, `pulse_conversations=560`; v2 engine `comm_v2_messages=1411`, `comm_v2_conversations=305`, `comm_v2_attachments=80`; flag `pulse_messenger` = enabled | High |
| Groups | PRODUCTION READY | `pulse_groups=410`, `pulse_group_members=410`, `pulse_group_posts=120`; flag `pulse_groups` = enabled | High |
| Notifications (push + email) | PRODUCTION READY | `pulse_notification_deliveries=7951`, `push_delivery_jobs=2200`, `push_tokens=53`, `email_logs=405` | High |
| Account management | PRODUCTION READY | 16 routes under `/api/account` in `bot.py`; `auth_events=460`, `users` populated | High |
| Settings | PRODUCTION READY | route pack `pulse_mobile_settings` (`bot.py:1248-1264` region), 7 `/pulse/settings` routes | High |
| Admin console | PRODUCTION READY | `admin_users=34`, `role_permissions=139`, `admin_audit_log` populated; flag `admin_command` = enabled | High |
| Identity verification | PRODUCTION READY | `verification_requests=14644` — the single busiest workflow table in the DB | High |
| Music / audio library | PRODUCTION READY | `pulse_audio_tracks=33947` (largest content table); used by reels + status composer | High |
| Search | PRODUCTION READY | `/api/pulse/marketplace/search` plus feed search routes; no dedicated table (search is a query layer, absence of rows is expected) | Medium |
| Presence | PRODUCTION READY | route pack `pulse_presence`; `presence_sessions=32`, `user_presence=54` | Medium |
| Reels | PARTIALLY READY | flag `pulse_reels` = **beta**; `pulse_reels=130` real rows, but `pulse_reel_retention_events=0` — the retention/analytics half has never run | High |
| Livestream | PARTIALLY READY | flag `pulse_livestream` = **beta**; `pulse_live_sessions=793`, `pulse_live_webrtc_signals=5` real; but `pulse_live_clips=0`, `pulse_live_moderation=0`, `pulse_live_scene_presets=0` — clipping, moderation and scenes are dark | High |
| Voice / video calls (LiveKit) | PARTIALLY READY | Full server path exists: `services/pulsesoc_communications_engine.py:1112` inserts calls, `:819` updates state, `:853` ring queue; `CallScreen.tsx` + `EXPO_PUBLIC_NATIVE_CALLKIT_ENABLED` on client. **All five `communication_call*` tables = 0 rows** — implemented but never exercised in this environment | High |
| Marketplace — buyer browse & search | PARTIALLY READY | flag `marketplace_browse` = enabled @ 100%; `marketplace_listings` populated but 20 of 21 rows are literal "Audit Listing"/"Browser QA Listing" fixtures (see Mock Data §6) | High |
| Marketplace — cart / offers | PARTIALLY READY | route packs `pulse_marketplace_cart`, `pulse_marketplace_offers` registered; client flags `MARKETPLACE_CART_ENABLED` / `MARKETPLACE_OFFERS_ENABLED` now `true` (conflicts with report, see Conflicts) | Medium |
| Marketplace — checkout | PARTIALLY READY | flag `marketplace_checkout` = **internal-only @ 0%**; single-item path exists, no basket checkout | High |
| Seller / Store console | PARTIALLY READY | `marketplace_sellers=2`; `STORE_MOCK_DATA_GAPS` = 8 documented gaps; `EXPO_PUBLIC_STORE_READINESS` defaults off | High |
| Orders / fulfilment | PARTIALLY READY | `ORDERS_MOCK_DATA_GAPS` = 7 gaps; escrow and fulfilment flags default off | High |
| Payments — Stripe | PARTIALLY READY | Real webhook handler + checkout session path; `payment_records=16` and `subscriptions=160` are **all test fixtures** (`cus_codex`, `cus_smoke_001`, `cus_test_*`) | High |
| Payments — Apple IAP | UNDER DEVELOPMENT | 5 ad-credit SKUs + premium SKUs sit at "Prepare for Submission" in App Store Connect; screenshots pending | High |
| Premium subscriptions | PARTIALLY READY | 9 plans priced in `subscription_plans`; `premium_entitlements=179` but **every row revoked** — no live entitlement | High |
| Ads Manager (advertiser side) | PARTIALLY READY | `pulse_ad_campaigns=1` (internal only); `ADS_MOCK_DATA_GAPS` = 9 gaps; legacy platform still serves impressions | High |
| Ads — delivery billing | UNDER DEVELOPMENT | `PULSE_ADS_BILLING_ENABLED` env gate, plus hard-coded `live_charging: False` at `services/pulse_advertiser_portal.py:737` and `:1216` | High |
| Ads — intelligence / targeting layer | UNDER DEVELOPMENT | referenced table `ads_intel_delivery_decisions` **does not exist** in the schema | High |
| Crypto — portfolio & alerts | PARTIALLY READY | `alert_rules=30`, `portfolio_snapshots=40` real; realized P/L is explicitly refused rather than approximated | Medium |
| Crypto — watchlists | PARTIALLY READY | legacy `watchlists=6` populated, but the new schema `crypto_watchlists=0` and `watchlist_items=0` — migration never ran | High |
| Whale intelligence | PLANNED | `whale_alerts=0`, `whale_intelligence=0`; state recorded PARTIAL with "live provider required" | High |
| UNDX AI layer | PARTIALLY READY | Registries populated: `pulse_ai_capability_registry=97`, `pulse_ai_tool_registry=97`, `pulse_ai_messages=138`; but `pulse_ai_missions=0` and `ai_agents=0` — chat works, autonomous missions have never run | High |
| Education / courses | PARTIALLY READY | `education_lessons=16`, `education_sections=80` seeded; but `pulse_courses=0` and `education_progress=0` — no learner has ever progressed | High |
| Trust & Safety | PARTIALLY READY | `user_trust_profiles=130` populated, but `user_trust_score=0` and `verification_appeals=0` — scoring and appeals never run | High |
| Camera / filters | PARTIALLY READY | `PULSE_CAMERA_ENABLED` defaults **false**; Banuba integration is foundation-only | High |
| Business OS (vertical) | UNDER DEVELOPMENT | 104 `business_os_*` tables, only **4 non-empty, 40 rows total**; every `/api/business-os` endpoint is dark until its `BUSINESS_OS_*` flag is on; `BUSINESS_OS_ENTITLEMENTS` defaults off | High |
| Business OS — Events | UNDER DEVELOPMENT | registry state `business:events` = BUILDING; `business_os_events=1`, all ticket tables 0 | High |
| Business OS — Presence entry | UNDER DEVELOPMENT | registry state `presence:businessOs` = BUILDING | High |
| Business OS — Customers | PLANNED | `businessOs.ts:197` `backed:false`; no screen, no route, no endpoint; state `business:customers` = COMING_SOON | High |
| Business OS — Team | PLANNED | `businessOs.ts:240` `backed:false`; state `business:team` = COMING_SOON | High |
| Progress / rewards / referral | UNDER DEVELOPMENT | `referral_conversions=0`, `referral_events=0`, `referral_rewards=0`; tables `progress_reward_cycles` and `reward_events` **do not exist** | High |
| Sentinel (security monitoring) | UNDER DEVELOPMENT | 21 of 22 `sentinel_*` tables empty | High |
| Returns (marketplace) | PLANNED (routes only) | `commerceInbox.ts:651` states there is no model, no route and no state machine; a `marketplace_returns_routes` pack does now register — routes without a domain model | High |
| **Returns filter chip (Commerce Inbox)** | **BROKEN / DEAD CONTROL** | `commerceInbox.ts:606` ships the chip in the UI; `:650-653` returns `false` unconditionally — the control is tappable and structurally incapable of ever showing a row | High |
| Communities | PLANNED | `comm_v2_communities=45` seeded by fixtures; no dedicated screen or navigation entry | Medium |
| Brand deals / sponsorships | PLANNED | `brand_deals=0`, `sponsorships=0`, `sponsor_slots=0` | High |
| Ad revenue share | PLANNED | `services/pulse_dashboard_mission_control.py:483` status COMING_SOON | High |
| Affiliate revenue | PLANNED | `pulse_dashboard_mission_control.py:484` COMING_SOON | High |
| Revenue forecasting | PLANNED | `pulse_dashboard_mission_control.py:487` COMING_SOON | High |
| Creator music distribution | PLANNED | `pulse_dashboard_mission_control.py:490` COMING_SOON | High |
| Sponsored audio | PLANNED | `pulse_dashboard_mission_control.py:494` COMING_SOON | High |
| `premium_plus` tier | PLANNED | `subscription_plans` row status = `coming_soon` | High |
| Arena | DEPRECATED | `reports/arena_pause_strategy.md`: removed from primary navigation, code and direct URLs preserved. 120 `/api/arena` routes still live; 63 tables, 53 non-empty. Only mobile trace is `HomeScreen.tsx:88` "Arena Highlights" | High |
| Legacy generic ads schema | DEPRECATED | `ads=0`, `advertisers=0`, `ad_campaigns=0`, `ad_impressions=0` — superseded by the `pulse_ad_*` family | High |
| `mobile/` Expo 51 app | DEPRECATED | Project guide: legacy app, do not develop here; `mobile-native/` is active | High |

### Bucket counts

| Bucket | Count |
|---|---|
| PRODUCTION READY | 12 |
| PARTIALLY READY | 19 |
| UNDER DEVELOPMENT | 8 |
| PLANNED | 12 |
| BROKEN / DEAD CONTROL | 1 |
| DEPRECATED | 3 |
| **Total** | **55** |

---

## MOCK / PLACEHOLDER DATA STILL SHIPPING

The codebase's stated convention is **omit-and-document**: when real data is unavailable
the surface is withheld and logged in a gap ledger rather than filled with invention.
71 ledger rows follow that convention correctly. The exceptions below do reach users.

1. **Returns filter chip — dead control.** `commerceInbox.ts:606` renders the chip;
   `:650-653` answers `return false` unconditionally. A user can tap it forever and it
   can never populate. This is the only outright broken shipped control found.
2. **`EXPO_PUBLIC_EVENTS_MOCK`** — deterministic sample events, `api/eventsData.ts:119`.
   Defaults off; when enabled the data is clearly marked as sample.
3. **`EXPO_PUBLIC_ADS_POST_MODE`** — `BusinessOsAdvertisingScreen.tsx:13` describes
   "clearly-tagged MOCK-DATA promotions". `AdsManagerScreen.tsx:1139` states promotions
   are now real and no longer depend on this flag → the removal is **partial**; the
   Business OS advertising screen still describes flag-gated mock promotions.
4. **`EXPO_PUBLIC_MESSAGES_MOCK_CHIPS`** — fabricated commerce context chips in messages.
5. **Ads KPI tiles labelled "to date"** — the window label was changed from "· 7d" to
   "to date" rather than the underlying number being corrected (`ADS_MOCK_DATA_GAPS`
   row 6). The number shown is not what the label implies.
6. **Marketplace test fixtures visible in browse.** 20 of 21 `marketplace_listings` rows
   are literal `"Audit Listing"` / `"Browser QA Listing"` records, and
   `marketplace_browse` is enabled at 100% — QA fixtures are user-visible inventory.
7. **Stripe test fixtures in the live DB.** `subscriptions=160` and `payment_records=16`
   carry `cus_codex`, `cus_smoke_001`, `cus_test_*` customer IDs. These will inflate any
   revenue or subscriber count read straight off the database.

Items 1, 6 and 7 are the ones with real user or reporting impact.

---

## FRAGILE REGISTRATIONS

Eight route packs are loaded via `_load_route_pack` at `bot.py:1248-1264`. Each call is
wrapped in `except Exception` and, on failure, emits
`logging.critical(... "every endpoint in this pack will 404")` and lets boot continue.
A subsystem can therefore vanish in production without the app failing to start. State is
observable at `/health/routes`.

| Pack | Blast radius if it fails to register |
|---|---|
| `pulse_communications_v2` | All v2 messaging (`comm_v2_*`), including calls |
| `pulse_presence` | Presence / online state |
| `pulse_mobile_settings` | Settings screen **and account-deletion cancel** |
| `pulse_marketplace_cart` | Cart endpoints |
| `pulse_marketplace_offers` | Offers endpoints |
| `pulse_marketplace_returns` | Returns endpoints |
| `business_os_web` | Business OS web surface |
| `business_os_commerce` | 37 `/api/business-os` endpoints + seller console |

**Compounding risk:** cart and offers are now flag-`true` on the client. A silent pack
failure surfaces as `fetchCart` / `fetchOffers` **failing soft to an empty list** — the
user sees an empty cart, not an error, and no alarm fires.

Additional note from the project guide, confirmed relevant here: `webhook_app = Flask(...)`
is assigned twice (`bot.py:384` and `bot.py:1130`); the second wins and discards anything
attached to the first.

---

## CONFLICTS: report vs. code

Three places where a report file disagrees with the code. Code is authoritative.

1. **Cart / offers flags.** `MOCK_DATA_TABLES.md` states all three marketplace flags are
   hard-coded `false`. In current code `MARKETPLACE_OFFERS_ENABLED` and
   `MARKETPLACE_CART_ENABLED` are `true`. The report is stale.
2. **Payments gap count.** Code exports `PAYMENTS_MOCK_DATA_GAP_COUNT = 8`; the
   accompanying documentation says 9.
3. **Ads mock promotions.** `AdsManagerScreen.tsx:1139` says promotions are real and no
   longer depend on `EXPO_PUBLIC_ADS_POST_MODE`, while
   `BusinessOsAdvertisingScreen.tsx:13` still documents flag-gated MOCK-DATA promotions.
   The migration off mock promotions covered Ads Manager but not Business OS Advertising.

---

# PART 2 — BUSINESS KNOWLEDGE

## Marketplace strategy

The marketplace is built **buyer-first, sequentially**, not as one launch. The rollout
flags encode the order plainly: `marketplace_browse` is enabled at 100%, while
`marketplace_checkout` sits at **internal-only, 0%**. Browse and search are real product;
the money path is not open to the public.

The buying flow that exists today is search → listing detail → **single-item** checkout.
There is no basket-based checkout; cart and offers endpoints were added later as separate
route packs (`pulse_marketplace_cart`, `pulse_marketplace_offers`) and their client flags
have since flipped `true`, which is the most recent movement in this area.

Returns is the clearest example of the sequencing: `commerceInbox.ts:651` records that
there is **no model, no route and no state machine** for returns, yet a
`marketplace_returns_routes` pack registers and a Returns filter chip ships in the
Commerce Inbox UI. The chip is wired to a function that returns `false` (`:650-653`).
The surface was built ahead of the domain, and one control escaped the
omit-and-document convention.

Inventory quality is the other open issue: 20 of 21 production listings are QA fixtures
(`"Audit Listing"`, `"Browser QA Listing"`) and browse is at 100% rollout. The
marketplace is technically live and commercially empty.

## Creator economy

The intended structure is visible in the fee tables even though almost none of it is
running. `platform_fee_rules` carries **1000 bps and 1500 bps** rules (10% / 15%) for
creator, merchant and teacher categories, alongside a **500 bps** (5%) marketplace take
rate. The ledger that would record creator earnings, `creator_ledger_entries`, holds
**4 rows, all audit entries** — no creator has ever earned.

Money-out is entirely unbuilt: `seller_payout_accounts=0`, `seller_payouts=0`, and
`payout_queue=2` (fixtures). Stripe Connect is the intended rail; no account has been
onboarded.

The creator surfaces that would generate the earnings are themselves incomplete —
brand deals (`brand_deals=0`), sponsorships (`sponsorships=0`, `sponsor_slots=0`),
creator music distribution and sponsored audio (both COMING_SOON at
`pulse_dashboard_mission_control.py:490` and `:494`), and ad revenue share (`:483`).

Reels and livestream — the two features a creator economy would monetise — are both at
**beta** rollout, and their monetisation-adjacent tables (`pulse_reel_retention_events`,
`pulse_live_clips`) are empty. The audience measurement layer that ad or revenue-share
pricing would depend on has never run.

## Premium strategy

Premium is the **only** revenue line with a complete, live collection path.

`subscription_plans` prices **9 plans**. The consumer tier is `pulse_premium_monthly`
at **999¢/mo** and annual at **9999¢/yr**, with a legacy `founder_premium` at **499¢**.
Above it sit Business at **4999¢**, Creator Pro at **1999¢** and Crypto Pro at **1499¢**,
priced in `business_os_ent_plans`. A `premium_plus` tier exists with status
`coming_soon`.

The critical gap: the higher tiers are **priced but not purchasable**. No checkout path
was found for Business, Creator Pro or Crypto Pro — they exist as price rows, not as
products. Only the consumer premium plans route to a live Stripe checkout.

Entitlement state confirms nothing is live: `premium_entitlements=179` rows, **every one
revoked**. And the Apple IAP path — necessary for iOS, where a subscription cannot be
sold outside StoreKit — is still at "Prepare for Submission".

## Advertising business

Three independent gates all currently deny billing, which is worth stating precisely
because the ads product otherwise looks complete:

1. `PULSE_ADS_BILLING_ENABLED` — environment flag, off.
2. `live_charging: False` — **hard-coded** at `services/pulse_advertiser_portal.py:737`.
3. `live_charging: False` — **hard-coded** again at `:1216`.

Two of the three are source-level constants, so enabling ad billing is a code change, not
a config change. Advertisers can build campaigns; the platform cannot charge for delivery.

Campaign volume is `pulse_ad_campaigns=1`, internal only. `ADS_MOCK_DATA_GAPS` documents
9 gaps. The targeting/intelligence layer is not merely dark — the table it depends on,
`ads_intel_delivery_decisions`, **is absent from the schema entirely**.

The legacy generic ads schema (`ads`, `advertisers`, `ad_campaigns`, `ad_impressions`) is
fully empty and superseded by the `pulse_ad_*` family, but the legacy platform still
serves impressions — so reporting must not be read off the new tables alone.

The prepaid path is ad credits sold as **5 Apple IAP consumables, $4.99 to $99.99**, all
at "Prepare for Submission". Credits are the intended first monetisation of ads, ahead of
CPC/CPM delivery billing.

## Business OS and architectural decisions

Business OS is the largest unshipped vertical in the repo: **104 tables**, of which only
**4 are non-empty, totalling 40 rows**. It carries 199 `/api/business-os` routes and 49
`/admin/business-os` routes — roughly a sixth of the entire route surface — behind flags.

The governing decision, recorded in `docs/business_os_ground_truth.md`, is **seven feature
flags all defaulting off**. Every `/api/business-os` endpoint is dark until its specific
`BUSINESS_OS_*` flag is enabled, and `BUSINESS_OS_ENTITLEMENTS` — the gate on paid
access — defaults off too. This is deliberate: build the vertical in the main branch,
ship it dark, enable per-tenant.

Module states from the registry:

| Module | State | Evidence |
|---|---|---|
| Commerce / seller console | Registered (dark) | pack `business_os_commerce`, 37 endpoints |
| Events | BUILDING | `business_os_events=1`, ticket tables 0 |
| Presence entry | BUILDING | state `presence:businessOs` |
| Customers | COMING_SOON | `businessOs.ts:197` `backed:false` |
| Team | COMING_SOON | `businessOs.ts:240` `backed:false` |

`backed:false` is the repo's explicit marker for "a card exists in the UI registry but
nothing serves it" — it is the honest counterpart to the Returns chip, which shipped
without such a marker.

Two further architectural decisions shape everything above:

- **Optional route packs registered inside `except Exception`.** One broken feature cannot
  block boot; the cost is that a subsystem can silently 404 in production. See "Fragile
  registrations" in Part 1.
- **No migration framework.** Schema is created imperatively in `bot.init_db()` with ~170
  tables in `AUTO_PK_TABLES`. This is why `crypto_watchlists=0` / `watchlist_items=0` sit
  empty next to a populated legacy `watchlists=6` — the cutover is hand-rolled and was
  never run.

## Seller ecosystem

Two sellers exist (`marketplace_sellers=2`). The seller product is documented by its own
gap ledgers rather than by feature docs: `STORE_MOCK_DATA_GAPS` lists **8** gaps and
`ORDERS_MOCK_DATA_GAPS` lists **7**. `EXPO_PUBLIC_STORE_READINESS` defaults off, so the
store surface is not shown to sellers by default.

Order lifecycle beyond purchase is unbuilt: escrow and fulfilment flags default off,
returns has no state machine, and payouts have no onboarded accounts. A seller can be
created and can list, but cannot be paid.

Marketplace boost — the natural seller upsell — is `MARKETPLACE_BOOST_ENABLED = false`,
and nothing in the codebase prices or sells a boost.

## Revenue lines — can it collect money today?

| Revenue line | Mechanism | Collects today? | Blocker |
|---|---|---|---|
| **Premium subscription (Stripe)** | `pulse_premium_monthly` 999¢/mo, annual 9999¢, legacy `founder_premium` 499¢ | **YES** | None — live checkout + webhook. All existing DB rows are test fixtures |
| Premium subscription (Apple IAP) | StoreKit 2 monthly/annual | No | SKUs at "Prepare for Submission" |
| Ad credits (Apple IAP consumables) | 5 SKUs, $4.99–$99.99 | No | App Store Connect "Prepare for Submission" |
| Ad delivery billing (CPC/CPM) | Advertiser portal | No | Three gates: env flag + hard-coded `live_charging: False` at `pulse_advertiser_portal.py:737` and `:1216` |
| Marketplace take rate | 500 bps | No | `fee_policy_active()` needs 3 unset owner gates; `marketplace_checkout` internal-only @ 0% |
| Creator / merchant / teacher fees | `platform_fee_rules` 1000 / 1500 bps | No | `creator_ledger_entries=4`, audit rows only |
| Seller payouts (money **out**) | Stripe Connect | No | `seller_payout_accounts=0`, `seller_payouts=0` |
| Business tier | 4999¢ | No | Priced in `business_os_ent_plans`; no purchase path exists |
| Creator Pro | 1999¢ | No | Same — priced, not purchasable |
| Crypto Pro | 1499¢ | No | Same — priced, not purchasable |
| Referral cash ($30 / 30 qualified) | Money **out** | No | `progress_reward_cycles` and `reward_events` tables absent; `referral_conversions=0` |
| Marketplace boost | — | No | `MARKETPLACE_BOOST_ENABLED = false`; nothing prices a boost |
| Ad revenue share / affiliate / enterprise intelligence | `revenue_layers()` | No | COMING_SOON widgets; `enterprise_leads=0` |

**Bottom line: exactly one of thirteen revenue lines can take money today** — Stripe
consumer premium. Everything else is priced, gated, or has no purchase path. Note that
iOS cannot legally sell that subscription outside StoreKit, so the one working line is
effectively web-only until the IAP SKUs clear review.

## Growth mechanics

- **Referral** is designed as a $30-per-30-qualified-signups reward, but the two tables it
  needs (`progress_reward_cycles`, `reward_events`) do not exist, and
  `referral_conversions`, `referral_events`, `referral_rewards` are all 0. The loop has
  never turned once.
- **Verification as an engagement engine** is the standout real number:
  `verification_requests=14644`, by far the busiest workflow table. Whatever drives
  verification is the platform's strongest actual funnel — but `user_trust_score=0` and
  `verification_appeals=0`, so nothing downstream consumes it.
- **Notifications** are the one growth channel genuinely operating at scale:
  `pulse_notification_deliveries=7951`, `push_delivery_jobs=2200`, `email_logs=405`.
  Only 53 push tokens are registered, so the volume is heavily email/in-app weighted.
- **Content supply is strong, retention measurement is absent.** `pulse_audio_tracks=33947`
  and `pulse_posts=1140` versus `pulse_reel_retention_events=0` and `education_progress=0`.
  The platform can produce and distribute content but has no instrumented view of whether
  anyone comes back for it.
- **UNDX AI** is positioned as a differentiator and is partly real —
  `pulse_ai_messages=138`, with 97 registered capabilities and 97 tools — but
  `pulse_ai_missions=0` and `ai_agents=0`: the conversational layer works, the autonomous
  execution layer has never run a mission.
- **Arena was paused deliberately** (`reports/arena_pause_strategy.md`) — removed from
  primary navigation with code and direct URLs preserved. 63 tables, 53 non-empty: it is
  the one subsystem with substantial real data that was intentionally taken off the
  growth surface, leaving 120 live routes with no entry point.
