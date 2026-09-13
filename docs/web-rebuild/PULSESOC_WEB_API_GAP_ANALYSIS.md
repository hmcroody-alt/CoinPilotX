# PulseSoc — Web API Gap Analysis

**Question this document answers:** if the website must reach parity with the native app,
how many backend endpoints does the web client have to learn to call that it has never
called before?

**Answer: 236.**

Method and confidence are stated in §6. Everything below is derived from static extraction
over the repository at `main` (`ffd356c5`), 2026-09-12, not from documentation.

---

## 1. Corrected route baseline

The figure carried in earlier notes — and in `CLAUDE.md` — is wrong, in two different ways.

| Source | Routes | Why it differs |
|---|---|---|
| `CLAUDE.md` | ~1,538 | Stale. |
| First extraction (this project) | 1,775 | **Scanned `bot.py` only.** |
| **Corrected, repo-wide** | **2,146** | Includes blueprint route packs. |

369 routes — 17% of the application — are declared **outside `bot.py`**, in Flask Blueprints
that are registered as optional route packs:

| Routes | File |
|---|---|
| 162 | `pulse_communications_v2/routes.py` |
| 26 | `services/private_office_routes.py` |
| 26 | `services/private_office_meetings_routes.py` |
| 26 | `services/command_center_worker/app.py` |
| 18 | `services/sentinel/api.py` |
| 11 | `services/private_office_structured_records_routes.py` |
| 10 | `undx_desktop_connector.py` |
| 10 | `services/pulse_settings_routes.py` |
| 10 | `services/private_office_conversations_routes.py` |
| 9 | `services/presence_routes.py` |
| 8 | `services/private_office_concierge_routes.py` |
| 7 | `services/market_pulse_routes.py` |
| 7 | `services/private_office_documents_routes.py` |
| 6 | `services/marketplace_cart_routes.py` |
| 5 | `services/private_office_relationships_routes.py` |
| 5 | `services/private_office_briefings_routes.py` |
| 5 | `services/marketplace_offers_routes.py` |
| 4 | `services/private_office_shield_routes.py` |
| 4 | `services/marketplace_returns_routes.py` |
| ≤3 each | `undx_execution_kernel.py`, `services/business_os_supplier_routes.py`, `services/business_os_web.py`, `services/business_os_commerce_routes.py`, `services/undx_agent_run_routes.py`, `services/undx_agent_run_control_routes.py`, `cj_staging_backend.py` |

**Two extraction traps, both of which produced false results before they were fixed. Any
future audit of this codebase will hit both:**

1. **Blueprint prefixes.** `grep '@app.route' bot.py` misses 369 routes. The private-office
   and marketplace-cart families look non-existent.
2. **f-string constants.** `services/marketplace_cart_routes.py:400` reads
   `@cart_blueprint.route(f"{API_PREFIX}/checkout-options")`. A regex that captures the raw
   string literal records the rule as `{API_PREFIX}/checkout-options`. Before resolving
   module-level constants, 88 routes were mis-recorded and the marketplace cart, offers,
   returns and market-pulse families all read as "native calls an endpoint that does not
   exist." They exist. *(The database inventory hit the identical trap on `CREATE TABLE`
   statements and reported 40 false orphans — see `PULSESOC_DATABASE_INVENTORY.md` §1.1.)*

Final counts: **1,331 distinct `/api` rules**, 727 non-`/api` rules (after collapsing
`<int:id>`-style converters to a wildcard, which merges rules differing only by method).

---

## 2. The headline: web and native are near-disjoint API consumers

| Set | Count | Meaning |
|---|---:|---|
| `/api` rules consumed by **native** | 281 | What the product actually is today |
| `/api` rules consumed by **web** | 223 | What the website actually calls today |
| **Shared by both** | **45** | The genuine common surface |
| **Native-only — the web gap** | **236** | **The rebuild's API workload** |
| Web-only | 178 | Mostly admin + `/api/arena` legacy |
| Consumed by **neither** | 872 | 65% of the API surface (see §5) |

Two clients have been built against the same backend and they overlap on **45 endpoints —
3.4% of the API surface**. This is the single most important number in the inventory, and
it reframes the project:

> The website rebuild is not a re-skin of an existing web client. The existing web client
> and the native app are two different products that happen to share a database. Reaching
> parity means writing web callers for 236 endpoints that have never had one.

---

## 3. The 236-endpoint gap, by family

Ranked by size. This ordering is the natural input to the phase plan.

| Endpoints | Family | Notes for the rebuild |
|---:|---|---|
| 19 | `/api/pulse/marketplace` | Cart, offers, returns, seller listings, digital-file upload. Money path. |
| 17 | `/api/pulse/live` | **Inventory only — protected system.** See §4. |
| 16 | `/api/pages` | Pages/identities/members/invites. Entirely absent from web. |
| 14 | `/api/pulse/ads` | Campaign drafts, audiences, wallet, targeting estimate. |
| 13 | `/api/account` | 2FA, recovery codes, trusted devices, session revocation, re-auth. |
| 11 | `/api/pulse/reels` | Comments, reactions, reposts, view tracking, camera-create. |
| 10 | `/api/business-os/undx` | Agent permissions, policies, receipts, emergency stop. |
| 9 | `/api/mobile/auth` | Register, login, refresh, recover, logout-all, confirmation. |
| 9 | `/api/pulse/business` | Business profile, hours, address, publish/preview. |
| 8 | `/api/progress` | Missions, milestones, activity, invite. |
| 8 | `/api/pulse/payments` | Checkout, intents, seller orders, payouts, Connect status. |
| 7 | `/api/private-office/capital-graph` | Portfolio, exposure, obligations, cash-flow, integrity. |
| 6 | `/api/pulse/profile` | Avatar/cover upload + removal, profile update, `me`. |
| 6 | `/api/pulse/seller` | Seller application lifecycle incl. document upload. |
| 5 | `/api/premium` | Checkout, billing portal, status/usage centre. |
| 5 | `/api/pulse/saved` | Saved items + collections. |
| 5 | `/api/pulse/status` | Stories: react, reply, share, AI story. |
| 4 | `/api/dashboard/account` | Settings, state, strikes appeal, verification appeal. |
| 4 | `/api/education` | Lessons, categories, quiz submission. |
| 4 | `/api/messages` | Media init/upload/complete/download. |
| 4 | `/api/pulse-ai` | Conversation, message, action confirm/cancel. |
| 3 | `/api/calls` | **Inventory only — protected system.** |
| — | remainder | translations, follows, friends, groups, orders, rewards, mute, support, security report, `/api/portfolio`, `/api/crypto` favourites |

Full machine-readable list: **`docs/web-rebuild/data/api_consumers.json`**, key `native_only`.

### 3.1 What the shared 45 actually covers

Worth reading as a list of what the *current* website is: posts (6), ads (6), notifications
(4), camera config (3), status (2), push (2), crypto (2), account (2), music (2),
dashboard/account (2), and 14 singletons. Feed reading and notification polling, essentially.

The current website can read a post and show a notification count. It cannot check out a
cart, enrol a second factor, upload an avatar, run a seller application, or open a story.

---

## 4. Protected systems — inventory only, no work proposed

Per the mission's PROTECTED SYSTEM LOCK, the following appear in the gap **as a count only**.
No web implementation is proposed, scoped, or scheduled for them in any deliverable.

- `/api/pulse/live/*` — 17 native-only endpoints (RTC token, join/guest arbitration,
  native-publish, replay retry, live chat, state).
- `/api/calls/*` — 3 native-only (`active`, `capabilities`, `start`), plus 21 further
  `/api/calls` rules consumed by neither client.

Two independent facts that the architecture must respect, both verified:

- **The web has no realtime transport at all.** Zero `RTCPeerConnection`, zero
  `new WebSocket`; the single `EventSource` is gated off at both ends. Voice/video calling
  does not exist on web today in any form.
- **RTC is Agora, not LiveKit.** `react-native-agora@4.6.2`
  (`mobile-native/package.json:71`), `agora-token-builder` (`requirements.txt:19`), `AGORA_*`
  and no `LIVEKIT_*` keys in `.env.example`. The variable names in the backend still say
  `livekit_room` — `bot.py:49214` renders `<label>Agora channel<code>{livekit_room}</code></label>`.
  `CLAUDE.md` is wrong on this point and will send a web team hunting for a package that is
  not in the tree.

---

## 5. The 872 endpoints consumed by neither client

65% of the `/api` surface has no statically-resolvable caller in either client. This is not
872 pieces of dead code, and it should not be treated as a deletion list. It decomposes as:

| Endpoints | Family | Most likely status |
|---:|---|---|
| 82 | `/api/arena` | CoinPilotX-era legacy. No native caller; 38 web callers exist. Strongest deletion candidate. |
| 73 | `/api/admin` | Called from inline admin HTML that the extractor reads only partially. **Live.** |
| 53 | `/api/pulse/communications` | The v2 blueprint (162 routes). Superseded/partially wired. |
| 36 | `/api/pulse/ads` | Admin-facing and server-to-server ad plumbing. |
| 30 | `/api/business-os/advertising` | Business OS console surface. |
| 29 | `/api/pulse/groups` | Groups is largely unwired on both clients. |
| 26 | `/api/pulse/mobile` | Duplicate prefix of `/api/mobile` — see below. |
| 23 | `/api/private-office/meetings` | Blueprint registered; no resolvable caller. |
| 21 | `/api/calls` | Protected system; excluded from analysis. |

**A live duplication worth naming:** both `/api/mobile/auth/*` and `/api/pulse/mobile/auth/*`
exist. The native app calls the former. The latter (26 rules) has no caller. Two auth
surfaces where there should be one is a security-relevant duplication, not just untidiness.

**Do not action this section as-is.** A dynamic-call audit (runtime route-hit logging over a
representative week) must precede any deletion. Static extraction cannot see a URL assembled
at runtime, and the cost of wrongly deleting a live payment or auth route is not symmetric
with the cost of keeping a dead one.

---

## 6. Method, and where this analysis is weak

**Extraction.** Route declarations: regex over every `.py` file outside `scripts/`, `tests/`,
`.git`, `node_modules`, `.venv`, `mobile/` (legacy), and `.claude/worktrees/` — the last of
which contains a full second checkout and doubled every count until excluded. Blueprint
`url_prefix` and module-level string constants are resolved. Native endpoints: 330 distinct
paths across 78 of 109 `mobile-native/src/api/` modules. Web endpoints: `/api/...` literals in
`static/**/*.js`, `static/**/*.html`, `templates/**`, plus `bot.py` occurrences restricted to
`fetch(` / `XHR.open(` / `axios` / `url:` contexts with route-decorator lines excluded.

**Normalisation.** `<int:id>` → `*`, `${expr}` → `*`, query strings dropped, trailing slash
dropped. Matching is bidirectional wildcard.

**Known weaknesses — read before quoting any number:**

1. **Restricting `bot.py` to fetch-like contexts is a deliberate under-count.** An unrestricted
   scan returns 1,229 "web" paths, but that figure is meaningless because it counts every route
   decorator string as if it were a call site. The 223 figure is the conservative end. The true
   web number sits between 223 and roughly 300; it does not approach 1,102.
2. **Inline admin JavaScript is under-sampled.** Admin lives in f-string HTML inside `bot.py`;
   its fetch calls are frequently assembled from variables. `/api/admin` is certainly
   under-counted on the web side, which inflates §5.
3. **23 native paths remain unresolved.** Nearly all are base-path constants that native
   modules concatenate onto (`/api/pulse/marketplace/cart` + `/checkout`). They are extraction
   artefacts, not missing routes. Listed in `data/api_gap.json`, key `unmatched`.
4. **Method-level granularity is collapsed.** `GET /x` and `POST /x` count once. Endpoint
   *counts* are therefore a floor for the implementation work, not a ceiling.
5. **Nothing here was executed.** No route was called. Existence of a rule is not evidence
   that it works, is authenticated correctly, or returns what the native client expects.

**What would raise confidence, cheaply:** one week of production route-hit logging keyed by
client (the native app already sends a distinguishable `User-Agent` and bearer token). That
single dataset would resolve weaknesses 1, 2 and 5 simultaneously, and would convert §5 from
an inference into a fact.

---

## 7. Consequences for the rebuild plan

1. **Sequence by the money and the account, not by the feed.** Marketplace (19) and payments
   (8) are the largest non-protected gaps and carry real financial risk. Account security (13)
   is the gap with the worst failure mode: a web session that cannot enrol 2FA, revoke a
   device, or re-authenticate is a weaker security posture than the native app for the same
   account.
2. **Auth is a rebuild, not a port.** The web has 0 of the 9 `/api/mobile/auth` endpoints and
   uses a hand-rolled cookie preamble with no decorator — `grep` for `@login_required`,
   `@require_login`, `@admin_required` returns zero matches; the gate is a copy-pasted
   three-line preamble repeated 152 times. See `PULSESOC_WEB_SECURITY_MODEL.md`.
3. **The backend does not need 236 new endpoints — it needs 236 new callers.** This is the
   plan's best news. The endpoints exist, are exercised daily by the native app, and already
   carry the domain logic. This is what makes the "one platform, multiple clients"
   architecture achievable rather than aspirational.
4. **But three of them are not reachable from a browser as written.** Endpoints that assume a
   bearer token in `expo-secure-store`, or return a payload shaped for a native uploader, need
   a browser-compatible path. The specific instances are enumerated in
   `PULSESOC_WEB_TARGET_ARCHITECTURE.md` §4 (auth) and §6 (uploads: R2 CORS must expose
   `ETag`, which is currently unconfigured — no browser multipart upload can complete without it).

---

## Cross-references

- `PULSESOC_BACKEND_ROUTES_AND_AUTH.md` — auth/authz per route family
- `PULSESOC_BACKEND_SERVICES_AND_INFRA.md` — what sits behind the endpoints
- `PULSESOC_EXISTING_WEB_INVENTORY.md` — the current web client
- `PULSESOC_NATIVE_PRODUCT_AREAS.md` — the native modules doing the calling
- `data/api_consumers.json`, `data/api_gap.json`, `data/all_routes_repowide.json`
