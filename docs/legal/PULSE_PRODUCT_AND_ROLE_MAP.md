# PulseSoc — what the product actually does, and who the people in it are

Mission PULSE LEGAL, §5 (feature map) and §6 (user-role map).

§3 of the brief forbids starting from policy text: the documents have to describe a
product that exists. This file is the description. Everything below was read out of
`origin/main` at `7ef111a8d36d0e00fdf17ba2804797843e4bcb8d` — route tables, schema,
screen list, and the code paths that enforce (or do not enforce) each capability.
Where a feature is offered in the interface but not carried out by any code, that is
said here rather than smoothed over, because a policy written from the interface
alone would describe a platform that does not exist.

**One defect is registered in this file rather than in the surface audit: D-A1,
age-based ad targeting.** It was found while enumerating features and it is a
privacy/advertising finding, not a legal-text one. It is stated in full at the end.

---

## §5 Feature map

### The shape of the thing

PulseSoc is one Flask application with **1,818 routes in `bot.py`** plus **118 more
registered from blueprints in `services/`**. The largest families, by count of
`@app.route` in `bot.py`:

| Routes | Family | What lives there |
|---|---|---|
| 430 | `/api/pulse` | the social product — feed, posts, reels, stories, chat, presence, notifications |
| 203 | `/api/business-os` | the second-generation commerce + advertising domain (`services/business_os/`) |
| 170 | `/pulse` | server-rendered pages for the same social product |
| 120 | `/api/arena` | Arena (competitive/creator surface) |
| 66 | `/admin/business-os` | staff operation of commerce |
| 58 | `/api/admin` | staff APIs |
| 45 + 29 | `/dashboard`, `/api/dashboard` | the member dashboard |
| 25 | `/api/crypto` | the original crypto product, still live as a subsystem |
| 23 | `/api/messages` | private messaging |
| 21 | `/api/pages` | Pages (business/creator entities) |
| 20 | `/api/mobile` | the iOS app's own auth + bootstrap surface |
| 16 | `/api/account` | account self-service, including deletion and export |
| 12 | `/api/reels` | short video |
| 8 each | `/api/undx`, `/api/push`, `/api/payments` | AI execution layer, push delivery, money |

The iOS app (`mobile-native/`, Expo 54) has **122 screens**. They are not a subset of
the website — some capabilities exist only on one side, which matters for §107 because
a single Terms document has to cover both.

### What a member can actually do

Read as capability groups, each with the surfaces that carry it. "Live" means verified
reachable and acting in production; "shipped, inert" means the code is deployed and the
feature is reachable but nothing yet flows through it; "offered, unenforced" means the
interface presents a control that no code path honours.

**1. Social publishing and consumption — LIVE.**
Feed, posts, comments, reactions, reels, stories/status, saved items, search, groups,
events, pages, profiles. Screens: `HomeScreen`, `PostDetailScreen`, `ReelsScreen`,
`StatusScreen`, `SavedScreen`, `SearchScreen`, `GroupsScreen`, `EventsScreen`,
`PagesHubScreen`, `ProfileScreen`. This is user-generated content at scale and it is
the reason a content policy, a notice-and-takedown path and a moderation appeal path are
all required documents.

**2. Private messaging and calls — LIVE.**
`MessengerScreen`, `ChatScreen`, `NewChatScreen`, `CallScreen`, `PresenceHubScreen`,
plus private meetings (`PrivateMeetingsScreen`, `PrivateMeetingRoomScreen`). Realtime
media is Agora RTC bridged to Mux. **Message bodies are stored in plain text** — see
D-P7 in the data inventory, which matters because the App Store screenshots claim
end-to-end encryption.

**3. Live streaming and replays — LIVE.**
`LiveScreen`, `LiveStudioScreen`, `LiveHostSessionScreen`, `ReplayViewerScreen`,
`CameraStudioScreen`. Host publishes over Agora; the server bridges to Mux for
distribution and VOD. A replay is a durable recording of a member's likeness and voice
held by a third party (Mux) — a processor relationship the privacy notice must name.

**4. Marketplace and commerce — LIVE, and carrying real money.**
Buyer side: `MarketplaceScreen`, `MarketplaceProductScreen`, `MarketplaceCartScreen`,
`MarketplaceCheckoutScreen`, `BuyerOrdersScreen`, `SellerStoreScreen`,
`CommerceInboxScreen`. Seller side: `SellerApplicationScreen`,
`SellerListingComposerScreen`, `MarketplaceManagerScreen`, `StoreDashboardScreen`,
`OrdersManagerScreen`, plus dropshipping (`mobile-native/src/screens/dropshipping/`)
and a CJ supplier integration. There is a **public, anonymous, search-indexable web
storefront** with Product JSON-LD.
Two generations of schema coexist: `marketplace_sellers` (bot.py:122808) and
`business_os_mkt_sellers` (`services/business_os/marketplace/schema.py:110`). Card
payments are live. The platform fee is currently **0%**, held there by three unset
attestation variables — the fee documents must not be written as though 5% were in force.

**5. A self-serve advertising platform — LIVE as a product surface.**
`AdsManagerScreen`, `AdsCampaignWizardScreen`, `AdsCampaignDetailScreen`,
`AdsAudiencesScreen`, `AdsLibraryScreen`, `AdsInsightsScreen`, `AdsReportsScreen`,
`AdsWalletScreen`, `AdsPolicyCenterScreen`, `AdsSubPageScreen`,
`PromoteContentWizardScreen`, `BusinessOsAdvertisingScreen`. Advertisers fund a wallet
and buy delivery against PulseSoc's own audience. Same duplication as commerce:
`advertisers` (bot.py:126436) and `business_os_ad_advertisers`.
Targeting accepted and stored: device type, contextual category, country, language,
premium audience, interests, keywords, **and age**. Only the first five are enforced at
delivery — **D-A1, below**.

**6. Money layer — LIVE.**
`MoneyLayerScreen`, `MoneyDetailScreen`, `BusinessOsPaymentsScreen`,
`PremiumCenterScreen`, `RewardsScreen`. Stripe subscriptions plus marketplace
payments. Treasury/wallet/payout tables exist and are empty in production; no
Stripe Transfer has ever been made, so seller payouts are a documented capability that
has never executed.

**7. Crypto analytics — LIVE, the original product.**
`CryptoPortfolioScreen`, `PortfolioScreen`, `AssetDetailScreen`, `MarketPulseScreen`,
`WatchlistsScreen`, `CryptoAlertCenterScreen`, `CryptoAlertHistoryScreen`,
`AlertManagementScreen`, plus `/api/crypto`. **This is the single largest legal
exposure that is easy to forget**: the sitemap is still mostly crypto URLs, and price
analytics with alerting invites a "is this investment advice" reading that a purely
social platform would not. The Terms must keep its crypto disclaimer, not drop it as
legacy.

**8. AI — LIVE (assistant) and partially inert (automation).**
`PulseAiScreen`, `IntelligenceCenterScreen`, `UndxActionCenterScreen`,
`UndxCapabilitiesScreen`, `BriefingsHubScreen`, `ContentPlannerScreen`. The UNDX
layer routes server-side between OpenAI, Anthropic, Gemini, DeepSeek and Groq — so
member content can reach up to five model vendors depending on configuration. This is
why the vendor inventory treats model providers as processors and why `META_MUSE_MODEL`
(D-P10) is a live risk: an env var can undo a no-training decision.

**9. Trust, safety and identity — LIVE.**
`TrustSafetyScreen`, `SafetyHubScreen`, `AccountHealthAppealsScreen`,
`VerificationCenterScreen`, `PulseIdentityScreen`, `AccountRecoveryScreen`. An appeals
surface exists, which means the moderation policy has a real process to describe.

**10. Account self-service — PARTLY BROKEN, already registered.**
`SettingsScreen`, `AccountCenterScreen`, `NotificationPreferencesScreen`,
`settings/` subtree, and `/api/account`. Deletion now removes contact data and all
four push registries (D-P3/D-P4, fixed on this branch). **An account deletion
requested through the iOS app is still scheduled and promised by date but never
executed (D-P1), and the data export promises an emailed archive that nothing
produces (D-P2).** Both are P0 and both are promises the product makes in its own
words.

**11. Learning, progress and private office — LIVE.**
`CoursesLearningScreen`, `ProgressCenterScreen`, `PrivateOfficeScreen`,
`PrivateOfficeSecurityScreen`, `PrivatePeopleScreen`. Private Office stores
structured identity records including document numbers and dates of birth — a
category of data heavier than anything else in the product.

### Features whose interface over-promises

Collected here because §107 cannot be written without them. Each is a place where a
policy drafted from the screens would state something untrue.

| Surface | Says | Does |
|---|---|---|
| iOS account deletion | deletes by a stated date | schedules, never executes (D-P1) |
| Data export | emails an archive | nothing fulfils it (D-P2) |
| `/shipping` | tax shown at checkout | tax never calculated (D-L6) |
| Ads targeting | age range applied | age ignored at delivery (**D-A1**) |
| Ads reach estimate | estimates age-targeted reach | filters a column with zero writers (**D-A1**) |
| Privacy Center | four controls | one is enforced; three are stored and read by nothing |
| App Store screenshots | end-to-end encrypted messaging | message bodies stored in plain text (D-P7) |
| Checkout | — | no terms disclosed at authorisation (D-L5) |

---

## §6 User-role map

### Two identity tables, not one

There is no single `role` column on a member. Identity is split:

* **`users`** — members. Every consumer capability. No role column, no admin flag
  column: `user.get("is_admin")` is derived at request time, not stored.
* **`admin_users`** — staff. Separate table, separate credential, its own `role`
  column, and it carries employment-grade personal data: `date_of_birth`,
  `address_line1/2`, `city`, `state`, `zip_code`, `country`, `job_title`,
  `emergency_contact_name`, `emergency_contact_phone` (bot.py:19930; required set at
  bot.py:16933). There is a third table, `employees`, with the same shape
  (bot.py:29217).

**Consequence for §107: PulseSoc needs a staff/employee privacy notice, not only a
member one.** The heaviest personal data in the schema belongs to staff, and no notice
addresses them. That is D-P9.

### How a member acquires a capability

A member's role is not a value on their row; it is the existence of a row in a
capability table. This is the accurate way to describe roles in the Terms, because
"you become a seller when we approve your application" is literally how it works.

| Role | Established by | Approval? |
|---|---|---|
| Member | a row in `users` | self-serve signup |
| Seller / merchant | `marketplace_sellers.user_id` (UNIQUE), `status` default `'pending'`, plus `verification_status` default `'unverified'`, `risk_score`, `reviewed_by`, `reviewed_at`, `review_notes` | **staff review** — an application in `marketplace_merchant_applications` precedes it |
| Seller (2nd gen) | `business_os_mkt_sellers` | same idea, separate lineage |
| Advertiser | `advertisers.owner_user_id`, `status` default `'pending'` | **staff review** |
| Advertiser (2nd gen) | `business_os_ad_advertisers` | same |
| Buyer | any member who checks out | none |
| Creator | engagement/eligibility surfaces (`CreatorStudioScreen`, `GrowthCenterScreen`) | mixed |
| Page owner / team member | `PageTeamScreen`, `/api/pages` | page owner grants |
| Premium member | Stripe subscription state | payment |

Two things a policy must get right here. First, **seller approval is not the same as
payment readiness** — a seller can be approved and still be unable to receive money,
because payouts depend on Stripe Connect state that is separate. Second, **the two
seller lineages mean "seller" is ambiguous in the data**; any document that promises a
seller something must be checked against both tables.

### Staff roles

`bot.py:129103` seeds **25** role names into both `roles` and `admin_roles`:

`owner`, `super_admin`, `admin`, `department_manager`, `social_manager`,
`pulse_moderator`, `trust_safety_agent`, `senior_moderator`, `creator_manager`,
`arena_operator`, `roast_operator`, `alerts_operator`, `marketing_manager`,
`seo_manager`, `monetization_manager`, `support_manager`, `support_agent`,
`security_analyst`, `analytics_viewer`, `developer_ops`, `billing_manager`, `analyst`,
`content_manager`, `developer`, `read_only`.

Both tables get the same 25 names, and `admin_roles` has no reader anywhere
(`grep "FROM admin_roles\|JOIN admin_roles"` → no match) — it is seeded and never
consulted. The permission join uses `admin_role_permissions`, which is a different
table.

Enforcement is real and it is `admin_has_permission(admin, permission)` at
**bot.py:20004**. Its order matters for any statement about internal access control:

1. no admin → `False`;
2. `role == "owner"` → `True` for everything, unconditionally;
3. a **hardcoded** `ROLE_FALLBACK_PERMISSIONS` map in source (bot.py:~19985) — a role's
   permissions exist in code even if the database has no rows;
4. then the database: `role_permissions`, `admin_role_permissions`, and per-admin
   grants via `admin_user_roles` (with `active=1`);
5. `except Exception: return False`.

Two honest observations. The exception handler **fails closed**, which is correct. But
the hardcoded fallback in step 3 means **the database is not the authority on staff
permissions** — an owner revoking a permission in the admin UI does not necessarily
revoke it, because the fallback map still grants it. That is a finding worth stating in
the Legal Review Packet under internal access controls; it is not in scope to change
here, and it is not a legal-text defect.

The permission vocabulary (40 keys, seeded at bot.py:129118) is the useful part for a data-access statement:
`users.view`, `users.edit`, `billing.view`, `billing.repair`, `subscriptions.edit`,
`emails.view`, `emails.resend`, `telegram.view`, `analytics.view`, `audit.view`,
`security.view`, `system.view`, `settings.edit`, `ai.view`, `support.manage`,
`moderation.manage`, `trust_safety.manage`, `monetization.manage`, `growth.manage`,
`seo.manage`, `arena.operate`, `roast.operate`, `alerts.operate`,
`engineering.operate`, `command_center.view`. Note that `users.view` and `emails.view`
are held by `read_only`, `support_agent`, `support_manager` and `billing_manager` —
i.e. **member email addresses are visible to a wide set of staff roles**, which a
privacy notice's "who inside the company can see your data" section must reflect.

---

## D-A1 — Age-based ad targeting is sold to advertisers, estimated against an empty column, and ignored at delivery

**Severity: high. Not previously registered anywhere in this audit.**

This is one defect with three faces, and the faces point in opposite directions, which
is what makes it worse than either half alone.

### The capability is offered and stored

`services/pulse_ads_os.py:311` in `put_targeting()`:

```python
min_age = safe_int(payload.get("min_age"), 0, 0, 120)
...
if min_age and min_age < 13:
    raise PulseAdsError("Ads cannot target users under 13.")
```

The value is persisted into `pulse_ad_targeting` (`min_age=?, max_age=?` in both the
UPDATE and INSERT branches, ~line 351/363). The same validation and storage is
repeated in the ad-set path (`services/pulse_ads_adsets.py:175`) and audiences carry it
too (`services/pulse_ads_audiences.py:283`).

Note the shape of the floor: `if min_age and min_age < 13`. **`min_age = 0` passes**,
because `0` is falsy. Zero is also what `safe_int` returns for a missing or unparseable
value. So the under-13 refusal is only reached by an explicit 1–12.

### It is echoed back to the advertiser as though it were in force

`services/pulse_ads_adsets.py:208`:

```python
min_age = safe_int(targeting.get("min_age"), 0)
...
if min_age and max_age:
    ages = f"{min_age}-{max_age}"
elif min_age:
    ages = f"{min_age}+"
```

combined into `f"{location} — {ages}"`. An advertiser who sets 18+ sees an ad set named
"US — 18+". The platform is telling them the restriction exists.

### The reach estimate filters a column nothing writes

`services/pulse_ads_os.py:216` describes the estimate as being computed from
"(country, preferred_language, date_of_birth, follow/engagement graph)", and
`:239` converts the age range to date-of-birth bounds and filters:

```
COALESCE(date_of_birth,'') != '' AND date_of_birth <= ? AND date_of_birth >= ?
```

`users.date_of_birth` exists — it is declared at bot.py:120664 in the `users`
`add_columns_if_missing` list. **Nothing writes it for a member.** Enumerated
exhaustively:

* the only `UPDATE ... SET date_of_birth=?` in the entire repository outside tests is
  bot.py:19930, and its target is `admin_users`;
* the only other writer is the employee record at bot.py:29217, target `employees`;
* `grep date_of_birth templates/` → **no match**;
* `grep -rn "date_of_birth\|dateOfBirth" mobile-native/src/` → **no match**.

So for every member on the platform the column is empty, the `COALESCE(...) != ''`
clause excludes them, and **an age-targeted campaign's estimated reach is structurally
zero** regardless of the real audience.

### Delivery ignores age entirely

`services/pulse_ads_service.py` is 2,495 lines and
`grep -n "min_age\|max_age\|date_of_birth\|dob"` against it returns **nothing**.
`_matches_targeting()` (~1928–1948) filters `target_device`, then
`contextual_category`, then returns `True` on `personalized_opt_out`, then `country`,
`language`, `premium_audience`. There is no age branch. `select_ads()` (~2122–2235)
has no age clause in its SQL either.

### Why the combination is the problem

The estimate says *nobody*. Delivery reaches *everybody*. An advertiser who restricts a
campaign to 21+ — because their own product is age-restricted and that restriction is
their legal obligation, not PulseSoc's — is shown a zero-reach estimate they will
read as a data problem, and then their ad is served to the entire eligible audience
including any minors on the platform. PulseSoc has represented a control it does not
operate.

### And the platform holds no member age data at all

Signup collects a boolean, not an age. The only age copy anywhere is
`templates/account.html:413`:

```html
<input type="checkbox" name="age_confirmed" required />
<span>I confirm I meet the age requirements for my country.</span>
```

**No minimum age is stated.** It is enforced as a presence check at bot.py:7855 (web)
and bot.py:8280 (iOS), stored as `users.age_confirmed`, and that is the entirety of
the platform's age knowledge. So age targeting is not merely unimplemented — with the
data the platform holds it is currently **unimplementable**.

### Why this branch does not fix it

Three reasons, and the first is decisive.

1. **A server-side refusal would break the shipped iOS wizard.**
   `mobile-native/src/api/adsOs.ts:108` reads
   `min_age: clampInt(source.min_age, 13, 65, 13)`. The client *always* sends a
   non-zero `min_age`, defaulting to 13. Senders:
   `AdsCampaignWizardScreen.tsx:259-260` and
   `advertising/campaignDraft.ts:613-614`; redisplay at
   `AdsCampaignDetailScreen.tsx:360`. Making the server reject age targeting would make
   every campaign creation fail. Shipping that knowingly is worse than the defect.
2. **The surface belongs to another mission in flight.**
   `mobile-native/src/screens/AdsSubPageScreen.tsx:178-200` already carries copy about
   this — "No audience narrowing is applied" / "PulseSoc does not currently target
   ads" — landed on `origin/main` in `eb45b83aa` ("Ads portal money truth, UNDX
   self-knowledge, and notification dedupe"). Editing it risks colliding with work this
   branch is required not to disturb.
   **That comment's premise is now stale**: it states that `pulse_ad_targeting` has no
   write path anywhere in the repository. `put_targeting()` writes it today. Whoever
   owns that surface should be told.
3. **The honest remedy is a decision, not a correction.** §106 permits fixing clear
   technical defects. The choices here are: withdraw an advertiser-facing capability,
   or start collecting member dates of birth. The first removes something advertisers
   were sold; the second begins collecting a new category of personal data from
   minors-adjacent users and sets a minimum age. Both are owner decisions, and the
   second is a counsel question.

### What is required

* **OWNER DECISION REQUIRED** — the minimum age for a PulseSoc account. Nothing can be
  fixed here until a number exists. This also fixes `account.html:413`, which currently
  asks members to confirm a requirement the platform never states.
* **OWNER DECISION REQUIRED** — withdraw age targeting from the ads product, or
  implement it. If withdrawn, the ad-set naming at `pulse_ads_adsets.py:208` must stop
  rendering an age range, and the estimator must stop claiming to use
  `date_of_birth`.
* **LEGAL COUNSEL REVIEW REQUIRED** — whether having represented an age-restriction
  control to advertisers, while not operating it, creates exposure for campaigns already
  run.
* Technical, safe to do independently of the above: change
  `if min_age and min_age < 13` to treat `0` distinctly from "unset" so the under-13
  floor cannot be bypassed by omission. Not done on this branch because it is inside
  the other mission's blast radius and is moot if the capability is withdrawn.

---

## Carried forward

* §5/§6 are complete for the purposes of §107. The document set can now be derived:
  UGC content policy, moderation/appeals, marketplace buyer terms, seller agreement,
  advertiser terms, crypto-analytics disclaimer, member privacy notice, **staff privacy
  notice**, cookie notice, AI/model-processing disclosure.
* **D-A1 registered above.** Add to the gap report (§111) at high severity.
* The `ROLE_FALLBACK_PERMISSIONS` hardcoded map overriding database revocation is noted
  for the Legal Review Packet's internal-access-control section. Not a legal-text defect;
  not fixed here.
