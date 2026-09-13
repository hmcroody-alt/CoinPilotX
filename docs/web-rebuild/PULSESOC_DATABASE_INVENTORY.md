# PulseSoc Database Inventory

**Read-only inventory of the production PostgreSQL database.**
Generated 2026-09-12 against production (`DATABASE_PUBLIC_URL`, Railway `Postgres` service) with
`SELECT`-only queries. No DDL, no writes, no migrations were executed.

Evidence rule for this document: every number is either a query result printed during the audit, or a
`file:line` citation. Anything that could not be verified is marked **UNVERIFIED**. `CLAUDE.md` and the
root `*_REPORT.md` files were **not** used as sources — `CLAUDE.md`'s "~170 tables in `AUTO_PK_TABLES`"
is off by 5x against production and should be corrected.

---

## 1. Hard counts

| Metric | Value | Evidence |
|---|---:|---|
| PostgreSQL version | 18.6 (Debian 18.6-1.pgdg13+2) | `select version()` |
| Base tables in `public` | **884** | `select count(*) from pg_tables where schemaname='public'` |
| Columns | **9,458** | `information_schema.columns` |
| Indexes | **2,045** | `pg_indexes` |
| **FOREIGN KEY constraints** | **0** | `pg_constraint` grouped by `contype` returns no `f` row |
| PRIMARY KEY constraints | 868 (→ **16 tables have no PK**) | `contype='p'` |
| UNIQUE constraints | 357, across 336 tables | `contype='u'` |
| CHECK constraints | 37, across 25 tables | `contype='c'` |
| NOT NULL columns | 3,010 (2,282 excluding serial PKs) | `is_nullable='NO'` |
| Columns with a DEFAULT | 2,844 (728 `nextval`, 2,116 real defaults) | `column_default` |
| **Distinct SQL data types used anywhere** | **4** | see §5 |
| Total database size | **1,133 MB** | `pg_database_size` |
| Tables with 0 live rows | **672 of 884 (76%)** | `pg_stat_user_tables.n_live_tup = 0` |
| Tables with 0 heap pages (never held data) | 473 | `pg_relation_size = 0` |
| Tables never `ANALYZE`d (`reltuples = -1`) | 666 | `pg_class` |
| Tables with no index at all | 0 | every table has at least a PK or UNIQUE index |
| Tables whose only index is the PK | 249 (99 of them non-empty) | `pg_class` index count = 1 |
| Rows in `users` | **39** | `select count(*) from users` |

### 1.1 Schema drift: production vs code

There is **no ORM and no migration framework.** `models/` contains exactly one file
(`models/live_session.py`) and the string `__tablename__` appears **zero** times in the entire repo.
All schema is imperative `CREATE TABLE IF NOT EXISTS` issued from Python.

Extracting `CREATE TABLE` from code required three passes, because DDL is written three different ways:

| Layer | How the table name is written | Distinct names found |
|---|---|---:|
| 1. Literal | `CREATE TABLE IF NOT EXISTS foo (...)` in `bot.py`, `services/`, `pulse_communications_v2/`, `backend/` | 853 |
| 2. Constant-interpolated | `f"CREATE TABLE IF NOT EXISTS {NOTES_TABLE} ..."` where `NOTES_TABLE = "seller_application_notes"` is a module constant — e.g. `services/seller_lifecycle.py:706,754`, `services/private_office/documents.py:105`, `services/undx_cost.py:297`, `services/marketplace_supplier_schema.py:110` | +35 |
| 3. Function-interpolated | `f"CREATE TABLE IF NOT EXISTS {private_table_for(kind)} ..."` — `services/private_office/records.py:647` and `:773`, looping over `RECORD_TYPES` | +8 |
| **Union** | | **896** |

**Drift result:**

| Set | Count | Notes |
|---|---:|---|
| Tables in production **and** creatable by code | **884** | the entire production schema |
| Tables in production that **no code creates** (orphans) | **0** | — |
| Tables code creates that production **lacks** (never deployed) | **12** | listed below |

**There are zero orphaned/abandoned tables.** This is a genuinely good result, but it is only visible
after resolving layers 2 and 3. A naive `grep 'CREATE TABLE'` reports **40 false orphans**, all of them
`private_*` (30), `seller_application_*` (3), `marketplace_listing_variants` / `marketplace_product_sources`,
`portfolio_outbox`, `undx_cost_ledger`, `undx_provider_health`, `business_os_confirmation_grants`,
`supplier_import_cart_items`. Any future schema-audit tooling must resolve interpolated DDL or it will
recommend dropping 40 live tables.

**The 12 tables code creates that production does not have** — every one is lazily created on first use
by a service module, so their absence proves that code path has **never executed in production**:

| Table | Creator | Meaning |
|---|---|---|
| `business_os_cj_diagnostic_origin` | `services/business_os/suppliers/diagnostics.py:31` | CJ supplier diagnostics never run |
| `business_os_supplier_intents__reshape` | `services/business_os/suppliers/` (reshape helper) | a table-rewrite path that has never fired |
| `financial_incidents` | `services/business_os/payments/incidents.py:137` | no financial incident has ever been recorded |
| `marketplace_listing_batches` | `services/business_os/marketplace/listing_batch.py:335` | bulk listing publish never exercised in prod |
| `private_office_conversations` | `services/private_office/conversations.py:100` | Private Office conversation classification unused |
| `private_office_conversation_links` | `services/private_office/conversations.py:101` | ditto |
| `private_record_fields` | `services/private_office/structured_records.py:363` | structured records feature never used |
| `private_record_revisions` | `services/private_office/structured_records.py:411` | ditto |
| `private_structured_records` | `services/private_office/structured_records.py:309` | ditto |
| `pulse_mutation_audit` | `services/pulse_mutation_audit.py:76` | mutation audit never written |
| `reconciliation_runs` | `services/business_os/payments/reconciliation.py:83` | payment reconciliation has never run |
| `undx_shadow_observations` | `services/undx_shadow.py:165,547` | UNDX shadow mode never engaged |

`reconciliation_runs` and `financial_incidents` are the notable ones: payment reconciliation and financial
incident capture exist in code and have never executed against production money.

### 1.2 Where schema actually gets created

`init_db` is defined **twice** in `bot.py` — at line 837 and line 110128. The second definition wins and
discards the first (same pattern as the duplicated `webhook_app = Flask(...)`). The surviving one delegates
to `_init_db_impl()` at `bot.py:110179–119009` (8,831 lines).

| Creation site | Tables |
|---|---:|
| `_init_db_impl()` (boot-time) | **499** |
| Elsewhere in `bot.py` (incl. the dead first `init_db` at 837–942) | 12 |
| `services/` + `pulse_communications_v2/` (lazy, request-time `ensure_*_schema()`) | 320 (literal) + 43 (interpolated) |
| **Production total** | **884** |

**385 of 884 production tables (44%) are not created at boot.** They are created the first time a request
touches the feature, by one of **96 distinct `ensure_*` / `_ensure_*` helpers**. There are **222 call sites
passing a route's own connection** as `ensure_schema(conn, ...)` — a known hang pattern, because passing the
route connection skips the commit, the DDL rolls back, and a second connection then blocks on the
uncommitted catalog lock.

---

## 2. Domain catalog — all 884 tables

Method: bucketed by name prefix, then ambiguous names verified against the owning code. `owning module`
is the file with the highest reference count for that table name (preferring a `services/` module over
`bot.py` where the service has ≥3 references); `bot.py` owns 338 of 884 (38%).
`rows` is `pg_class.reltuples` — `0` means zero heap pages (verified empty), `?` means the table has pages
but has never been `ANALYZE`d. Purposes marked `°` are derived from the table name only and were **not**
individually verified in code (**UNVERIFIED**); unmarked purposes were read from the query code.

### 2.1 Domain counts

| Domain | Tables |
|---|---:|
| Business OS | 121 |
| Ops / infra / admin / i18n | 66 |
| Arena (gamified trading) | 63 |
| Advertising | 62 |
| AI layer (`pulse_ai`, roast, simulator, prediction) | 62 |
| Identity / auth | 50 |
| Messaging / conversations | 46 |
| Crypto | 40 |
| Marketplace / orders / checkout / fulfillment | 38 |
| Payments / subscriptions / premium / wallet | 36 |
| Private Office / private facts / capital graph | 33 |
| Learning / education | 30 |
| Live / calls / streaming | 26 |
| Profiles, creators, verification | 24 |
| Sentinel (security incident engine) | 23 |
| Notifications | 22 |
| Analytics / events | 21 |
| Groups, pages, communities, spaces | 20 |
| Media | 15 |
| Security / audit | 13 |
| Status / stories | 12 |
| Market intelligence | 11 |
| Posts | 11 |
| Social graph (follows / friends / blocks / mutes) | 7 |
| Saves / collections | 6 |
| UNDX | 5 |
| Sessions, devices, tokens | 4 |
| Reels | 4 |
| Dropshipping / supplier | 4 |
| Briefings | 3 |
| Comments | 2 |
| Moderation | 2 |
| Reactions | 1 |
| Search / discovery | 1 |
| **Total** | **884** |

The shape of that table is the most important structural fact in this document: **the social product the
web rebuild is supposed to mirror occupies roughly 150 tables. The other ~730 are Business OS, Arena,
advertising, AI, crypto, Private Office and ops.** Posts + comments + reactions + social graph + saves +
reels + status + media = **58 tables**. Business OS alone is 121.

### 2.2 Table listing

<!-- CATALOG -->

---

## 3. Zero foreign keys

`pg_constraint` returns no row with `contype='f'`. **Not one declared relationship exists in the entire
884-table database.** Every join in the product is a convention enforced only by Python.

### 3.1 Implied ERD for the core social graph

Derived by reading the actual queries, primarily `services/pulse_feed_engine.py` and the `/api/pulse/*`
routes in `bot.py`.

```
users (PK user_id integer, 106 columns, 39 rows)
  │
  ├─1:N→ pulse_posts.user_id                    services/pulse_feed_engine.py:1499
  │        ├─1:N→ pulse_comments.post_id        :742   (+ .user_id → users)
  │        ├─1:N→ pulse_reactions.post_id       :730   UNIQUE(post_id,user_id)
  │        ├─1:N→ pulse_post_views.post_id      :775
  │        ├─1:N→ pulse_post_saves.post_id      :955
  │        ├─1:N→ pulse_post_hides.post_id      :1405  (viewer-scoped suppression)
  │        └─self→ pulse_posts.repost_of_post_id :762
  │
  ├─N:M→ pulse_follows(follower_user_id, followed_user_id)   :973, :1422
  ├─N:M→ pulse_friends(user_id, friend_user_id, status)      :1487
  ├─N:M→ blocked_users(blocker_user_id, blocked_user_id)     :1401, :1403
  ├─N:M→ pulse_user_mutes(user_id, muted_user_id, muted_until) :1410
  │
  ├─1:1→ arena_profiles.user_id     LEFT JOINed into every feed row  :1500
  │
  ├─1:N→ pulse_conversation_participants.user_id   bot.py:45329
  │        └─N:1→ pulse_conversations.id
  │                 └─1:N→ pulse_messages.conversation_id   bot.py:45320
  │                          └─1:N→ pulse_message_reactions.message_id
  │                          └─1:N→ pulse_message_receipts.message_id
  │
  ├─1:N→ pulse_notifications.user_id  (+ polymorphic entity_type/entity_id TEXT)
  ├─1:N→ pulse_media_assets           (referenced from pulse_posts.media_ids_json, a TEXT blob)
  └─1:N→ mobile_security_sessions.user_id
```

Two relationships in that diagram are *not* real foreign keys even in spirit:

- **`pulse_posts.media_ids_json`** is a TEXT column holding a JSON array of media ids. There is no join
  at all — `services/pulse_feed_engine.py:589` selects `media_ids_json` and resolves it in Python. A FK is
  impossible without normalising to a `pulse_post_media` join table.
- **`pulse_notifications.entity_type` / `entity_id`** is polymorphic; `entity_id` is TEXT while every
  target PK is integer. No FK is expressible.

### 3.2 Orphaned rows actually present in production

Probed with `LEFT JOIN ... WHERE right.pk IS NULL`. All these tables are small, so these are exact counts,
not estimates.

| Relationship | Total rows | Orphans | Verdict |
|---|---:|---:|---|
| `pulse_posts.user_id → users.user_id` | 2,370 | **1,915 (80.8%)** | **Broken** |
| `pulse_comments.post_id → pulse_posts.id` | 39 | 0 | clean |
| `pulse_comments.user_id → users.user_id` | 39 | 0 | clean |
| `pulse_reactions.post_id → pulse_posts.id` | 107 | 0 | clean |
| `pulse_follows.follower_user_id → users` | 14 | 0 | clean |
| `pulse_follows.followed_user_id → users` | 14 | 0 | clean |
| `pulse_messages.conversation_id → pulse_conversations.id` | 29 | 0 | clean |
| `pulse_messages.sender_user_id → users.user_id` | 29 | 0 | clean |
| `pulse_conversation_participants.conversation_id → pulse_conversations.id` | 374 | 0 | clean |
| `pulse_conversation_participants.user_id → users.user_id` | 374 | 0 | clean |
| `pulse_notifications.user_id → users.user_id` | 12,895 | 0 | clean |
| `comm_v2_messages.conversation_id → comm_v2_conversations.id` | 1,099 | 0 | clean |

**The 1,915 orphaned posts all have `user_id = 0`**, and `users` has `min(user_id) = 1`. So this is a
magic sentinel for a seeded/system author, not dangling data. But the consequence is real and it is a
web-rebuild hazard:

- The feed uses `LEFT JOIN users u ON u.user_id = p.user_id` (`services/pulse_feed_engine.py:1500`), so
  81% of posts render with a NULL username, NULL avatar and NULL plan. Any rebuild author who "tidies"
  that to an `INNER JOIN` — the natural thing to write against a schema with FKs — **silently deletes 81%
  of the feed**, and every test with a real author still passes.
- A FK on `pulse_posts.user_id` cannot be added today without first deciding what `user_id = 0` means:
  either create a real system user row with `user_id = 0`, or backfill those 1,915 rows.

### 3.3 What adding foreign keys would cost, and where it pays

Adding FKs across 884 tables is not the recommendation. The cost is not the DDL, it is the four
prerequisites that only some relationships can satisfy:

1. **Type agreement.** 37 logical id columns are typed inconsistently (§5.2). An FK cannot span
   `text` → `integer`.
2. **Referenced column must be UNIQUE.** 16 tables have no PK at all (§5.1), and polymorphic columns
   (`entity_id`, `target_id`, `content_id`) have no single referent.
3. **No orphan rows.** Only `pulse_posts.user_id` currently fails, but 672 tables are empty, so this
   is untested for most of the schema.
4. **Idempotent DDL with no migration framework.** Each FK must be wrapped so re-running `init_db()`
   does not fail — `ALTER TABLE ... ADD CONSTRAINT` is not `IF NOT EXISTS`-able before PG 9.6-style
   catalog probes, so each needs a `pg_constraint` existence check.

**Recommended: add FKs to exactly these 11 edges**, all in the core social graph, all already clean, all
type-consistent, all small enough that `ALTER TABLE ... ADD CONSTRAINT` validates in well under a second:

| Child | Column(s) | Parent | Recommended action |
|---|---|---|---|
| `pulse_comments` | `post_id` | `pulse_posts(id)` | `ON DELETE CASCADE` |
| `pulse_comments` | `user_id` | `users(user_id)` | `ON DELETE CASCADE` |
| `pulse_reactions` | `post_id` | `pulse_posts(id)` | `ON DELETE CASCADE` |
| `pulse_reactions` | `user_id` | `users(user_id)` | `ON DELETE CASCADE` |
| `pulse_post_views` | `post_id` | `pulse_posts(id)` | `ON DELETE CASCADE` |
| `pulse_post_saves` | `post_id` | `pulse_posts(id)` | `ON DELETE CASCADE` |
| `pulse_follows` | `follower_user_id`, `followed_user_id` | `users(user_id)` | `ON DELETE CASCADE` |
| `blocked_users` | `blocker_user_id`, `blocked_user_id` | `users(user_id)` | `ON DELETE CASCADE` |
| `pulse_conversation_participants` | `conversation_id` | `pulse_conversations(id)` | `ON DELETE CASCADE` |
| `pulse_conversation_participants` | `user_id` | `users(user_id)` | `ON DELETE CASCADE` |
| `pulse_messages` | `conversation_id` | `pulse_conversations(id)` | `ON DELETE CASCADE` |

That set buys the thing the rebuild actually needs: **a delete of a user or a post cannot leave the feed,
a thread or a notification badge pointing at nothing.** It costs 11 constraints, not 884.

Deliberately excluded, with reasons:

- `pulse_posts.user_id → users` — **blocked by the 1,915 `user_id = 0` rows.** Resolve the sentinel first.
- Anything polymorphic (`pulse_notifications.entity_id`, `pulse_saved_items.content_id`,
  `private_record_links.target_id`, all `*_audit_logs.target_id`) — not expressible.
- All 121 `business_os_*` tables — their ids are `text` while the core is `integer` (§5.2). FKs here
  would first require a type migration.
- Every empty table (672 of them) — no integrity to protect yet, and each constraint is permanent debt
  against `init_db()` idempotency.

---

## 4. Indexes

2,045 indexes over 884 tables. Every table has at least one index (the PK or a UNIQUE), so there is no
"completely unindexed table". The problems are elsewhere.

### 4.1 Tables whose only index is the primary key

249 tables. 99 of those hold data. The ones that matter, ordered by size:

| Table | Rows | Total size | Why it matters |
|---|---:|---:|---|
| `visitor_logs` | 206,807 | 78 MB | 1,194 seq scans reading **82.6M tuples**; no index on time or session |
| `analytics_events` | 29,712 | 7.5 MB | generic event sink, no index on `user_id`, `event_type` or time |
| `pulse_live_events` | 14,972 | 7.1 MB | live event stream, no index on session or time |
| `pulse_notification_deliveries` | 14,868 | 3.0 MB | delivery log, no index on notification or user |
| `education_lesson_views` | 783 | — | no index on lesson or user |
| `undx_embedding_cache` | 1,732 | — | cache with no lookup index — every hit is a seq scan |
| `i18n_missing_translations` | 1,467 | — | write-mostly, acceptable |
| `pulse_media_assets` | 192 | — | **no index on owner or post** — media lookup is a seq scan |
| `email_logs` | 2,623 | — | 1,104 seq scans reading 2.8M tuples |
| `notification_logs` | 4,202 | — | no index on user or time |
| `portfolio_snapshots` | 3,714 | 5.3 MB | no index on user or time |

### 4.2 Exact duplicate indexes (10 pairs)

Identical column list, opclass, uniqueness and predicate. One of each pair is pure write overhead:

| Table | Duplicate pair |
|---|---|
| `private_messages` | `idx_private_messages_conversation_id_id` = `idx_private_messages_thread_id` |
| `pulse_conversation_participants` | `idx_pulse_conversation_participants_user_only` = `idx_pulse_part_user` |
| `pulse_groups` | `pulse_groups_slug_key` = `idx_pulse_groups_slug` |
| `pulse_messages` | `idx_pulse_messages_conversation` = `idx_pulse_messages_conversation_created` |
| `pulse_messages` | `idx_pulse_messages_conversation_id` = `idx_pulse_msg_conv` |
| `pulse_reels` | `idx_pulse_reels_user` = `idx_pulse_reels_user_created` |
| `pulse_saved_collections` | `pulse_saved_collections_user_id_slug_key` = `ux_pulse_saved_collections_user_slug` |
| `pulse_saved_items` | `pulse_saved_items_user_id_content_type_content_id_key` = `ux_pulse_saved_items_user_content` |
| `push_delivery_jobs` | `push_delivery_jobs_idempotency_key_key` = `idx_push_delivery_jobs_idempotency` |
| `verification_requests` | `idx_verification_requests_user_id` = `idx_verification_requests_user` |

### 4.3 Prefix-redundant indexes (27)

Non-unique btree indexes whose column list is a leading prefix of another btree index on the same table,
with no partial predicate — the wider index already serves them. Highlights:

| Table | Redundant | Subsumed by |
|---|---|---|
| `pulse_follows` | `idx_pulse_follows_follower` | `pulse_follows_pkey (follower_user_id, followed_user_id)` |
| `pulse_reactions` | `idx_pulse_reactions_post` | `pulse_reactions_post_id_user_id_key` |
| `pulse_conversation_participants` | `idx_..._conversation_only`, `idx_..._user_only`, `idx_pulse_part_user` | the `(conversation_id, user_id)` and `(user_id, conversation_id)` pairs |
| `pulse_message_reactions` | `idx_pulse_message_reactions_message` | `pulse_message_reactions_message_id_user_id_key` |
| `pulse_user_badges` | `idx_pulse_user_badges_user` | `pulse_user_badges_user_id_badge_key_key` |
| `user_settings` | `idx_user_settings_user_id` | `user_settings_user_id_setting_key_key` |
| `conversation_members` | `idx_chat_participants_conversation`, `idx_chat_participants_user` | the two composite indexes |
| `chat_media_uploads` | `idx_chat_media_context` | `idx_chat_media_uploads_context_created` |
| `pulse_page_follows` | `idx_pulse_page_follows_page` | `pulse_page_follows_page_id_user_id_key` |

`pulse_conversation_participants` is the extreme case: **7 indexes on a 374-row table**, 3 of them provably
redundant. Total index size across the DB includes several tables where indexes are larger than the heap
(`market_observations` 5 MB heap / 35 MB indexes; `pulse_ai_schedules` 264 kB heap / **21 MB indexes** on
76 rows; `worker_heartbeats` 272 kB heap / 6.4 MB indexes on **5 rows**).

### 4.4 Hot read paths a web client will hammer

For each path: the actual query, then whether an index serves it.

#### Feed — `GET /api/pulse/feed` → `services/pulse_feed_engine.py:1379` `list_feed()`

```sql
SELECT p.*, u.username, u.email, u.full_name, ... (20 user columns) ...,
       ap.avatar_url AS arena_avatar_url, ap.public_player_id
FROM pulse_posts p
LEFT JOIN users u          ON u.user_id = p.user_id
LEFT JOIN arena_profiles ap ON ap.user_id = p.user_id
WHERE <visibility> AND <moderation> AND deleted_at IS NULL
  AND NOT EXISTS (SELECT 1 FROM blocked_users   bu WHERE bu.blocker_user_id=? AND bu.blocked_user_id=p.user_id)
  AND NOT EXISTS (SELECT 1 FROM blocked_users   bu WHERE bu.blocker_user_id=p.user_id AND bu.blocked_user_id=?)
  AND NOT EXISTS (SELECT 1 FROM pulse_post_hides ph WHERE ph.user_id=? AND ph.post_id=p.id)
  AND NOT EXISTS (SELECT 1 FROM pulse_user_mutes pum WHERE pum.user_id=? AND pum.muted_user_id=p.user_id AND ...)
ORDER BY p.created_at DESC, p.id DESC
LIMIT ? OFFSET ?
```
(`services/pulse_feed_engine.py:1493–1506`, filters at `:1396–1422`.)

**Index verdict:**
- `pulse_posts` has **11 indexes**, including `idx_pulse_posts_mobile_feed (visibility, moderation_status, status, created_at, id)` — a correct covering index for the default ordering. ✅
- Every `NOT EXISTS` subquery is served: `blocked_users_blocker_user_id_blocked_user_id_key`,
  `pulse_post_hides_user_id_post_id_key`, `pulse_user_mutes_user_id_muted_user_id_key`. ✅
- `arena_profiles_user_id_key` serves the LEFT JOIN. ✅
- **Not served:** the `for_you`-with-topic / profile ordering at `:1485–1490`, which sorts by a computed
  expression containing two correlated `IN (SELECT ...)` subqueries against `pulse_follows` and
  `pulse_friends`. No index can serve that — it is a full scan plus a full sort, per request.
- **Not served:** `LIMIT ? OFFSET ?`. Offset pagination re-scans and discards every preceding row. The web
  client's infinite scroll will re-read the whole prefix on every page.

**Production reality:** `pulse_posts` has **1,151,816 sequential scans that read 2,684,301,233 tuples**
(`pg_stat_user_tables`) against 1,207,612 index scans. At 2,370 rows the planner is *correct* to seq-scan —
a 2,370-row table fits in a handful of pages. So this is not a missing index today. It is a **shape that
becomes a cliff**: the same query at 1M posts does 1.15M full scans of a 1M-row table. The indexes are
already in place for the default path; the `OFFSET` pagination and the computed `ORDER BY` are not
fixable with indexes and must change in the rebuild.

Other seq-scan leaders, same reasoning (small table, planner correct, shape wrong):
`pulse_content_music` 898k scans / 8 rows · `arena_profiles` 2.86M scans / 5 rows ·
`pulse_live_sessions` 236k scans / 280 rows · `communication_calls` 194k scans / 418 rows ·
`alert_rules` 195k scans / 75 rows · `pulse_ad_campaigns` 277k scans / 0 rows ·
`pulse_user_badges` 231k scans / 0 rows.

#### Profile — `/@username`

```sql
SELECT user_id FROM users WHERE lower(username) = lower(?) LIMIT 1
```
(`bot.py:6072`, plus `services/pulse_feed_engine.py:325`; 3 call sites for username, 4 for
`lower(email)`.)

**Index verdict: ❌ NOT INDEXED.** `users` has exactly three indexes:
`users_pkey (user_id)`, `ux_users_pulse_id (pulse_id) WHERE pulse_id IS NOT NULL`, and
`idx_users_roast_call_sign_slug (roast_call_sign_slug)`.

There is **no index on `username` and no index on `email`**, and no functional index on
`lower(username)` / `lower(email)`. Both the public profile URL and **the login path** are sequential
scans of `users`. `pg_stat_user_tables` shows `users` with 45,931 seq scans reading 1,685,585 tuples.

There is also **no UNIQUE constraint on `username` or `email`.** Production currently has 0 duplicates
(verified: `group by lower(email) having count(*)>1` → 0 rows; same for username), but nothing prevents
them. 6 of 39 users have a blank username and 3 have a blank email.

This is the single highest-value index fix for the web rebuild:

```sql
CREATE UNIQUE INDEX CONCURRENTLY ux_users_lower_username ON users (lower(username)) WHERE COALESCE(username,'') <> '';
CREATE UNIQUE INDEX CONCURRENTLY ux_users_lower_email    ON users (lower(email))    WHERE COALESCE(email,'')    <> '';
```

#### Conversation list — `bot.py:45320`

```sql
SELECT c.*, (SELECT pm.created_at FROM pulse_messages pm
             WHERE pm.conversation_id=c.id AND COALESCE(pm.deleted_at,'')=''
             ORDER BY pm.id DESC LIMIT 1) AS latest_message_created_at
FROM pulse_conversations c
JOIN pulse_conversation_participants mine
  ON mine.conversation_id=c.id AND mine.user_id=? AND COALESCE(mine.left_at,'')=''
WHERE COALESCE(c.status,'active')='active' AND COALESCE(c.deleted_at,'')=''
ORDER BY CASE WHEN COALESCE(mine.pinned_at,'')!='' THEN 0 ELSE 1 END,
         COALESCE(mine.pinned_at, c.last_message_at, c.last_activity_at, c.updated_at, c.created_at) DESC
LIMIT ?
```

**Index verdict: driving side ✅, ordering ❌.**
`idx_pulse_conversation_participants_user (user_id, conversation_id)` serves the driver, and the
correlated latest-message lookup is served by `idx_pulse_messages_conversation_id (conversation_id, id)`.
But the `ORDER BY` is a `COALESCE` across **five columns from two tables** — unindexable. Every
conversation-list load sorts the user's full conversation set in memory. Fine at 295 conversations,
not fine at web scale.

#### Message history — `pulse_messages` / `comm_v2_messages`

**Index verdict: ✅ over-served.** `pulse_messages` has 8 indexes for 29 rows, including two exact
duplicate pairs (§4.2). `comm_v2_read_receipts` has the highest index-scan count in the database at
**26,084,790** — it is being hammered, and it is also the worst-bloated table (§6).

#### Notifications — `bot.py:38277`

**Index verdict: ✅.** `idx_pulse_notifications_user_read_created (user_id, is_read, created_at)` and
`idx_pulse_notifications_user_created (user_id, created_at)` cover both the badge count and the list.

---

## 5. Constraints and type consistency

### 5.1 Constraint inventory

| Constraint kind | Count | Concentration |
|---|---:|---|
| FOREIGN KEY | **0** | — |
| PRIMARY KEY | 868 | 16 tables have none |
| UNIQUE | 357 | across 336 tables; 548 tables have no UNIQUE at all |
| CHECK | 37 | across 25 tables — **all** in `business_os_*`, `ledger_*`, `pulse_credit_ledger`, `reward_events`, `seller_payout_requests` |
| NOT NULL | 3,010 of 9,458 columns (32%) | — |
| DEFAULT | 2,844 (728 serial, 2,116 real) | — |

**Zero CHECK constraints exist anywhere in the core social graph** — no enum guard on
`pulse_posts.visibility`, `moderation_status`, `post_type`, or `pulse_reactions.reaction_type`. Every
one of those is free-text. The 37 CHECKs that do exist are exemplary (`amount_cents >= 0`,
`rating BETWEEN 1 AND 5`, `side IN ('buy','sell')`) and all live in code written later, against real
Postgres. This is a clean fault line: **newer subsystems write proper DDL, the core writes SQLite DDL.**

**The 16 tables with no primary key** (15 Arena + 1 payments):
`arena_academy_progress`, `arena_blocks`, `arena_companions`, `arena_faction_members`, `arena_follows`,
`arena_match_participants`, `arena_playbook_votes`, `arena_presence`, `arena_quest_progress`,
`arena_reputation`, `arena_spectators`, `arena_team_members`, `arena_tournament_entries`,
`arena_user_badges`, `arena_user_preferences`, **`connect_account_state`**.

`connect_account_state` is the dangerous one: it holds Stripe Connect account state, has no primary key,
**and** its `user_id` is `text` while `users.user_id` is `integer`.

### 5.2 Type consistency — the documented bug class, quantified

**The entire 9,458-column database uses only four data types:**

| Type | Columns | Share |
|---|---:|---:|
| `text` | 6,429 | 68.0% |
| `integer` | 2,834 | 30.0% |
| `real` | 169 | 1.8% |
| `double precision` | 26 | 0.3% |

There are **zero** columns of type `timestamp`, `timestamptz`, `date`, `boolean`, `numeric`/`decimal`,
`jsonb`, `uuid`, `bigint`, `varchar(n)`, or any array type. This is the SQLite type system projected
verbatim onto PostgreSQL 18. Three consequences:

1. **All timestamps are TEXT.** `created_at` is `text` in **722** tables (and `double precision` in 4);
   `updated_at` text in 391; `expires_at` text in 49; `deleted_at` text in 28.
   `ORDER BY created_at DESC` on the feed works only because ISO-8601 sorts lexically — and only while
   every writer produces the same format with the same precision and the same (absent) timezone suffix.
   Note `services/pulse_feed_engine.py` compares `pum.muted_until > ?` as a **string comparison**. No date
   arithmetic, no BRIN indexes, no timezone correctness.
2. **Money is sometimes `real`.** 189 money-ish columns are `integer` (cents — correct), 52 are `text`,
   and **63 are `real`**, a 4-byte float with ~7 significant decimal digits. Including
   **`payment_records.amount`** and **`payment_verifications.amount`**. A payment of `$12,345.67` already
   has 7 significant digits; anything larger cannot round-trip exactly. The `ledger_entries` /
   `ledger_transactions` tables get this right (`amount_cents integer` with `CHECK (>= 0)`) — the older
   payment tables do not.
3. **All JSON is TEXT.** `media_ids_json`, `tags_json`, `ai_tags_json`, `metadata_json` etc. No `jsonb`,
   so no containment operators, no GIN indexes, no server-side validation.

#### Inconsistently typed ID columns

408 distinct `*_id` column names exist. **37 of them are typed differently in different tables.**

| Column | Tables | integer | text | The split |
|---|---:|---:|---:|---|
| `id` | 712 | 708 | 4 | text in `business_os_supplier_connections`, `business_os_supplier_intents`, `business_os_supplier_sync_jobs`, `prediction_markets` |
| **`user_id`** | **407** | **391** | **16** | see below |
| `campaign_id` | 45 | 21 | 24 | integer in `ad_*` / `pulse_ad_*`; text in `ads_intel_*` / `business_os_ad_*` |
| `owner_user_id` | 42 | 41 | 1 | text only in `business_os_business` |
| `actor_user_id` | 23 | 21 | 2 | text in `business_os_event_audit`, `business_os_verification_runs` |
| `creative_id` | 21 | 13 | 8 | integer in `pulse_ad_*`; text in `business_os_ad_*` / `ads_intel_*` |
| `event_id` | 21 | 5 | 16 | integer in `intelligence_*`, `notifications`; text in `business_os_*` |
| **`seller_user_id`** | 21 | 13 | 8 | integer in `marketplace_*`; **text in all 8 `business_os_mkt_*`** |
| **`buyer_user_id`** | 16 | 10 | 6 | integer in `marketplace_*`; **text in `business_os_mkt_*`** |
| `target_id` | 15 | 3 | 12 | polymorphic audit columns |
| `source_id` | 12 | 2 | 10 | polymorphic |
| `session_id` | 12 | 1 | 11 | integer only in `pulse_live_streams` |
| `product_id` | 10 | 1 | 9 | integer only in `marketplace_product_media` |
| `request_id` | 10 | 5 | 5 | even split |
| `merchant_id` | 9 | 1 | 8 | integer only in `marketplace_product_media` |
| `audio_track_id` | 9 | 8 | 1 | text only in `pulse_content_music` |
| `room_id` | 8 | 6 | 2 | text in `pulse_room_members`, `pulse_room_messages` |
| `item_id` | 7 | 2 | 5 | — |
| `actor_id` | 7 | 4 | 3 | text in `sentinel_*` |
| `content_id` | 6 | 3 | 3 | — |
| `mission_id` | 5 | 1 | 4 | — |
| `entity_id` | 5 | 1 | 4 | — |
| `created_by_user_id` | 4 | 3 | 1 | text only in `business_os_events` |
| `fact_id` | 4 | 3 | 1 | text only in `pulse_ai_truth_facts` |
| `rule_id`, `creator_id`, `alert_id`, `source_event_id`, `ticket_id`, `return_id`, `collection_id`, `transaction_id`, `telegram_user_id`, `interaction_id`, `media_asset_id`, `thumbnail_asset_id`, `job_id` | 2–4 each | | | same pattern |

**The `user_id` split is the one that will crash production.** `users.user_id` is
`integer NOT NULL DEFAULT nextval('users_user_id_seq')`. 391 tables agree. **16 do not:**

```
business_os_ad_advertisers      business_os_attr_conversions    business_os_attr_credits
business_os_attr_touchpoints    business_os_business_members    business_os_crypto_alert_events
business_os_crypto_alerts       business_os_crypto_holdings     business_os_crypto_lots
business_os_crypto_transactions business_os_rec_interactions    business_os_rec_recommendations
connect_account_state           pulse_credit_ledger             reward_events
seller_payout_requests
```

Twelve are `business_os_*`, which is the documented case. **Four are not, and all four touch money:**
`connect_account_state` (Stripe Connect), `pulse_credit_ledger` (credit balance, with a
`CHECK (balance_after >= 0)`), `reward_events` (reward grants), `seller_payout_requests` (payout amounts).
Any query joining one of these to `users` — or binding an `int` from
`session['account_user_id']` into a `text` column — raises
`operator does not exist: text = integer` on PostgreSQL.

**This is invisible to the test suite.** SQLite has no static column types, so the same join and the same
parameter binding succeed locally and fail in production. The correct test assertion is on the *query
binding* (that the parameter is coerced to `str` before execution), not on behaviour. `marketplace_*`
(integer) and `business_os_mkt_*` (text) implement overlapping seller/order concepts with incompatible key
types — any rebuild that tries to present "orders" as one surface has to bridge them in Python.

---

## 6. Row counts and physical size

Total database: **1,133 MB**. Counts below are `pg_stat_user_tables.n_live_tup`.

**672 of 884 tables (76%) have zero rows.** Only 212 tables hold any data at all. `users` has **39 rows** —
this is a pre-launch production database, so every scale conclusion in this document is about *shape*, not
about current load.

### 6.1 Top 60 tables by row count

<!-- TOP60 -->

### 6.2 Largest tables on disk, heap vs index

| Table | Rows | Heap | Indexes | Note |
|---|---:|---:|---:|---|
| `pulse_jobs` | 976,803 | 172 MB | 61 MB | largest table; 127,582 dead tuples |
| `comm_v2_read_receipts` | 1,132 | **130 MB** | 20 MB | **~120 KB of heap per row** — severe bloat; 26.1M index scans |
| `visitor_logs` | 206,807 | 74 MB | 4.5 MB | only a pkey index |
| `chat_media_uploads` | 729 | **56 MB** | 1.3 MB | ~78 KB/row; 56 columns |
| `notification_delivery_logs` | 155,301 | 45 MB | 17 MB | |
| `notifications` | 62,279 | 29 MB | 28 MB | 35 columns, indexes ≈ heap |
| `alert_delivery_jobs` | 152,770 | 25 MB | 10 MB | |
| `pulse_notifications` | 12,895 | 23 MB | 3.2 MB | |
| `alert_events` | 64,613 | 18 MB | 4.7 MB | |
| `pulse_audio_tracks` | 21,276 | 14 MB | 3.5 MB | 40 columns |
| `notification_delivery_jobs` | 9,198 | 13 MB | 3.2 MB | |
| `visitor_sessions` | 32,668 | 12 MB | 22 MB | indexes 1.8x the heap |
| **`users`** | **39** | **12 MB** | 216 kB | **~315 KB of heap per row** — extreme bloat on 106 columns |
| `mobile_security_sessions` | 10,132 | 7.7 MB | 4.3 MB | 404 active / 8,008 revoked / 1,720 rotated — no TTL sweep |
| `market_observations` | 40,300 | 5.2 MB | **35 MB** | indexes 6.8x the heap |
| `pulse_ai_schedules` | 76 | 264 kB | **21 MB** | indexes 80x the heap on 76 rows |
| `worker_heartbeats` | **5** | 272 kB | **6.4 MB** | 6.4 MB of indexes for five rows |

`users` at 12 MB for 39 rows and `comm_v2_read_receipts` at 130 MB for 1,132 rows are free space that
`VACUUM` has reclaimed logically but not returned to the filesystem (both show low `n_dead_tup`).
**Inference, not verified:** a meaningful share of the 1,133 MB total is reusable free space rather than
data. Confirming this needs `pgstattuple`, which was not probed. **UNVERIFIED.**

`market_observations`, `pulse_ai_schedules`, `worker_heartbeats` and `visitor_sessions` all carry more
index than heap — index bloat and/or over-indexing on churn tables.

### 6.3 Dead schema

- **672 tables with 0 rows.** 473 of those have never had a single heap page allocated.
- **666 tables have `reltuples = -1`** — never `ANALYZE`d, so the planner has no statistics for them at
  all. When these tables start receiving traffic from the web client, the first queries will be planned
  blind.
- Whole domains are empty: most of Arena (63 tables), most of `business_os_*` (121 tables), all of
  Private Office's record tables, most of advertising.

---

## 7. Web-client readiness

### 7.1 Auth and session — better than the naming suggests

The resolution order is (`bot.py:3658`):

```python
def account_user_id():
    return (session.get("account_user_id")
            or account_user_id_from_mobile_access_token()
            or restore_account_from_persistent_cookie())
```

Three layers:
1. **Flask signed-cookie session** (`session["account_user_id"]`). Config at `bot.py:467–470` and
   `1216–1219`: `SESSION_COOKIE_HTTPONLY=True`, `SAMESITE="Lax"`, `SECURE` from env. There is **no
   `SESSION_TYPE` and no Flask-Session backend**, so this is the default client-side signed cookie —
   **no server-side row, therefore not server-revocable.**
2. **Bearer access token** (`account_user_id_from_mobile_access_token`, `bot.py:3605`) — native.
3. **Persistent refresh cookie** (`bot.py:3578` `restore_account_from_persistent_cookie`) reading
   `PULSESOC_REFRESH_COOKIE_NAME` (default `pulse_refresh_session`) and calling
   `rotate_mobile_refresh_token(...)` with `source="web_cookie_refresh"`.

**The key finding: `mobile_security_sessions` is already the shared web + native session store, despite
its name.** Verified against production:

| `platform` | sessions |
|---|---:|
| `web` | **7,446** |
| `ios` | 2,686 |

| `device_label` | sessions |
|---|---:|
| `desktop-web` | 4,743 |
| *(blank)* | 4,234 |
| `ios-app` | 1,111 |
| `ios-web` | 39 |

| `status` | sessions |
|---|---:|
| `revoked` | 8,008 |
| `rotated` | 1,720 |
| `active` | 404 |

So the browser is **already** a first-class session citizen: refresh rotation, `session_family_id`,
`reuse_detected_at`, `revoked_reason`, `last_risk_score`, and per-platform labelling all work for web today.
`device_hash` is `NOT NULL` but the web path supplies a browser-derived value — 7,446 rows prove it.

**Three real gaps:**

1. **The `or` short-circuit hides the bearer token.** Because `session.get("account_user_id")` is
   evaluated first, a client that sends *both* a cookie and a bearer token resolves via the cookie, and
   anything downstream that reads `g.mobile_access_user_id` sees nothing. When the web client and native
   app share a browser (an in-app webview), this is a live hazard.
2. **No TTL sweep.** 9,728 of 10,132 rows are `revoked` or `rotated` and are never deleted. The table only
   grows. A web launch multiplies session churn.
3. **`active_sessions`** — the table backing the "Active sessions / Sign out all devices" UI
   (`bot.py:84159`, `84195`) — **has only a pkey index and no UNIQUE on `session_hash`.** Session lookup
   there is a sequential scan and duplicate session hashes are not prevented.

Also note `sessions` (226 rows) is **not** an auth table at all despite the name — it holds
`utm_source`, `referrer`, `landing_page`, i.e. anonymous visitor analytics. Do not wire the web client's
auth to it.

### 7.2 Readiness by product area

| Area | Backing tables | Web-ready? |
|---|---|---|
| **Auth / session** | `mobile_security_sessions` (shared, 7,446 web rows), `active_sessions` (UI listing), `auth_events` (rate limiter), `users`, `user_recovery_codes`, `account_recovery_tokens`, `email_verification_tokens` | **Mostly yes.** Fix: index `lower(email)` on `users`; add UNIQUE + index on `active_sessions.session_hash`; add a session TTL sweep. The Flask cookie session is not server-revocable — a "sign out everywhere" button cannot actually kill a web cookie session today. |
| **Profile** | `users` (106 cols), `creator_profiles`, `pulse_profile_themes`, `arena_profiles`, `verification_*`, `pulse_user_badges`, `reputation_ledger` | **No — blocked on one index.** `/@username` is a sequential scan; there is no index and no UNIQUE on `username`. Also `users` is a 106-column god table; a web profile page over-fetches badly. |
| **Feed** | `pulse_posts` (11 indexes), `users`, `arena_profiles`, `blocked_users`, `pulse_post_hides`, `pulse_user_mutes`, `pulse_follows`, `pulse_friends` | **Indexes yes, shape no.** `OFFSET` pagination and the computed `ORDER BY` do not survive growth. And 81% of rows have `user_id = 0` — a rebuild that switches to `INNER JOIN users` loses them silently. |
| **Posts** | `pulse_posts`, `pulse_post_views`, `pulse_post_saves`, `pulse_post_hides`, `pulse_content_*` | Usable. Media is `media_ids_json` TEXT — the web client must replicate the Python-side resolution at `services/pulse_feed_engine.py:589`, there is no join to write. |
| **Comments** | `pulse_comments`, `pulse_comment_reactions` | **Yes.** `idx_pulse_comments_post_visible_created (post_id, deleted_at, moderation_status, created_at)` is exactly right. |
| **Messages** | **three parallel stacks** — `comm_v2_*` (18 tables, 1,099 live messages), `pulse_conversations`/`pulse_messages`/`pulse_conversation_participants` (29 messages), and bare `conversations`/`conversation_members`/`message_attachments` | **No — pick one first.** `comm_v2_*` holds the live data (1,099 vs 29 messages). `pulse_messages` carries 8 indexes, two of them exact duplicates, for 29 rows. Building web against the wrong lineage is the single largest wasted-effort risk in the rebuild. |
| **Notifications** | `pulse_notifications` (12,895) + `notifications` (62,279, 35 cols, 60 MB) + `notification_*` (10 tables) + `pulse_notification_*` (3) | **No — three lineages again.** Indexing is fine on `pulse_notifications`; the question is which table the web bell reads. `entity_id` is TEXT and polymorphic, so deep links must be resolved in Python. |
| **Social graph** | `pulse_follows`, `pulse_friends`, `pulse_friendships`, `pulse_friend_requests`, `blocked_users`, `pulse_user_mutes`, `pulse_muted_users`, `pulse_user_mutes` | Indexed and clean (0 orphans). But `pulse_friends` vs `pulse_friendships` is another duplicate lineage, and there are two mute tables. |
| **Saved content** | `pulse_saved_items` (polymorphic `content_type`/`content_id` TEXT), `pulse_saved_collections`, `pulse_saved_sounds` | Works, but each saved item's target must be resolved per-type in Python; the duplicate UNIQUE indexes (§4.2) should be dropped. |
| **Settings** | `user_settings` (K/V, UNIQUE `(user_id, setting_key)`), `notification_preferences`, `privacy_preferences`, `pulse_region_preferences`, `pulse_translation_preferences` | **Yes.** K/V shape means no schema change is needed to add web-only preferences — the cheapest surface in the whole rebuild. |

### 7.3 Duplicate-lineage summary

The rebuild must choose a winner for each of these before writing a line of web code:

| Concept | Competing tables | Live data |
|---|---|---|
| Conversations / messages | `comm_v2_*` · `pulse_conversations`+`pulse_messages` · `conversations`+`conversation_members` | `comm_v2_messages` = 1,099; `pulse_messages` = 29 |
| Notifications | `notifications` · `pulse_notifications` · `notification_*` · `pulse_notification_*` | `notifications` = 62,279; `pulse_notifications` = 12,895 |
| Status / stories | `pulse_status` · `pulse_statuses` · `pulse_stories` | `pulse_status` = 51; others near-empty |
| Friends | `pulse_friends` · `pulse_friendships` · `pulse_friend_requests` | both near-empty |
| Mutes | `pulse_user_mutes` · `pulse_muted_users` | both near-empty |
| Sellers / orders | `marketplace_*` (integer ids) · `business_os_mkt_*` (text ids) | see §5.2 |
| Rooms | `pulse_room_*` (text `room_id`) · `arena_room_*` (integer `room_id`) | — |

---

## 8. Risks for the rebuild, and a safe change methodology

### 8.1 Schema changes the web rebuild will actually need

Ranked by value/risk. "Size" is the table the change touches.

| # | Change | Why | Risk |
|---:|---|---|---|
| 1 | `CREATE UNIQUE INDEX CONCURRENTLY` on `lower(users.username)` and `lower(users.email)` | Profile URL and login are seq scans today | **Low** (39 rows) — but must be `CONCURRENTLY` and must tolerate the 6 blank usernames / 3 blank emails via a partial predicate |
| 2 | Decide the `user_id = 0` sentinel: insert a real system user, or backfill 1,915 posts | Unblocks the `pulse_posts.user_id` FK and prevents the `INNER JOIN` trap | **Medium** — an `INSERT` into `users` with an explicit id must not desync `users_user_id_seq` |
| 3 | Add the 11 core-social FKs from §3.3 | Turns convention into enforcement where it is already clean | **Low** — all target tables are small; validate immediately |
| 4 | Add UNIQUE + index on `active_sessions.session_hash` | Web session lookup and revocation | **Low** |
| 5 | Add a session TTL sweep for `mobile_security_sessions` | 9,728 of 10,132 rows are dead | **Low**, but it is a DELETE — must be batched and must never touch `status='active'` |
| 6 | Index the pkey-only hot tables: `visitor_logs`, `analytics_events`, `pulse_live_events`, `pulse_media_assets`, `undx_embedding_cache` | Seq scans today | **Low** if `CONCURRENTLY`; `visitor_logs` at 207k rows is the only one where build time is noticeable |
| 7 | Drop the 10 exact-duplicate and 27 prefix-redundant indexes | Pure write overhead | **Low** — `DROP INDEX CONCURRENTLY` |
| 8 | Replace `OFFSET` pagination with keyset pagination on the feed | The web infinite scroll re-reads the prefix on every page | **Low schema risk** (no DDL), **high code risk** — changes the `/api/pulse/feed` contract that the native app also consumes |
| 9 | Choose one messaging lineage and one notification lineage | Avoids building web against dead tables | **High** — a data migration between lineages |
| 10 | `payment_records.amount` / `payment_verifications.amount`: `real` → `integer` cents | 4-byte float cannot represent money above ~7 significant digits | **High** — a money-column type change |
| 11 | `created_at` TEXT → `timestamptz` across 722 tables | Correctness, date arithmetic, BRIN | **Do not attempt.** See below |

### 8.2 Why the dangerous ones are dangerous here

There is **no migration framework**. `_init_db_impl()` (`bot.py:110179–119009`) runs on boot and must be
idempotent, because it runs again on every deploy and on every worker start. That has three consequences:

- **`CREATE TABLE IF NOT EXISTS` never alters an existing table.** If a column's definition changes in
  `init_db()`, production keeps the old definition forever. The schema in code and the schema in
  production diverge silently. This is why §5.2's type inconsistencies persist: the tables were created
  once, correctly for their author, and nothing has re-typed them since.
- **`ALTER TABLE` has no `IF NOT EXISTS` for constraints.** Every FK, CHECK or UNIQUE added to
  `init_db()` must be guarded with an explicit `pg_constraint` lookup, or the second boot fails and
  `INIT_DB_COMPLETED` never gets set.
- **A long `ALTER TABLE` blocks boot.** `init_db()` runs inside the boot path with an
  `INIT_DB_TIMEOUT_SECONDS` watchdog. A rewrite on `pulse_jobs` (976k rows, 172 MB) or `visitor_logs`
  (207k rows, 74 MB) would take the app down for the duration, on **every** instance, because the DDL
  runs at boot rather than in a controlled migration window.

A `created_at` TEXT → `timestamptz` conversion across 722 tables is therefore not a refactor, it is an
outage. `ALTER TABLE ... ALTER COLUMN ... TYPE timestamptz USING created_at::timestamptz` rewrites the
entire table and takes an `ACCESS EXCLUSIVE` lock. On `pulse_jobs` that is a multi-minute full rewrite
inside the boot path, and any single malformed timestamp string anywhere in 722 tables aborts it. **Do not
do this as part of the web rebuild.** If timestamps must be fixed, do it one table at a time, out of band,
using the add-column/backfill/swap pattern below — and start with tables that are already empty (672 of
them) where the change is free.

### 8.3 Proposed safe-change methodology

1. **Never put a schema change in `init_db()` first.** Write it as a standalone, idempotent, re-runnable
   script under `scripts/`, run it manually against production in a controlled window, verify, and only
   then add the now-no-op guarded form to `init_db()` so fresh environments match.
2. **Guard every constraint addition** with an existence probe, so the second run is a no-op:
   ```sql
   DO $$ BEGIN
     IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_pulse_comments_post') THEN
       ALTER TABLE pulse_comments
         ADD CONSTRAINT fk_pulse_comments_post
         FOREIGN KEY (post_id) REFERENCES pulse_posts(id) ON DELETE CASCADE NOT VALID;
     END IF;
   END $$;
   ```
   Then `ALTER TABLE pulse_comments VALIDATE CONSTRAINT fk_pulse_comments_post;` as a **separate**
   statement — `NOT VALID` takes only a brief lock, and `VALIDATE` takes a weaker one.
3. **All index work uses `CONCURRENTLY`**, which cannot run inside a transaction and therefore cannot run
   inside `init_db()`'s transaction. This is a second reason index changes belong in a script.
4. **Type changes use add-column → dual-write → backfill → swap**, never in-place `ALTER TYPE`:
   add `created_at_ts timestamptz`, write both from application code, backfill in batches with a
   `WHERE created_at_ts IS NULL LIMIT n` loop, verify equality, then switch reads, then drop. Each step is
   independently revertible and none holds a long lock.
5. **Before any destructive step, re-probe.** The orphan queries in §3.2 are cheap and exact at this data
   size. Re-run them immediately before adding each FK — 672 tables are empty today and will not stay that
   way.
6. **Run `ANALYZE` on the tables the web client will hit.** 666 tables have never been analysed. The
   planner will make blind choices the moment web traffic arrives. `ANALYZE` is read-only-ish, cheap, and
   safe.
7. **Assume the test suite cannot catch type bugs.** Local tests run on SQLite, which has no static column
   types. Any change involving §5.2's id columns must be verified against a real PostgreSQL instance
   (a throwaway `postgres:18` container), asserting on the *query binding*, not on behaviour.
8. **Treat `bot.py` edits as gated.** `bot.py` is protected by diff content, not path; run
   `python3 scripts/realtime_audio_change_gate.py --base origin/main --head HEAD` locally before pushing
   any `init_db()` change.

### 8.4 The three highest-risk items, restated

1. **Building the web client against the wrong messaging or notification lineage.** There are three of
   each. `comm_v2_*` has 1,099 live messages; `pulse_messages` has 29. Nothing in the schema says which is
   canonical — only the row counts do. Decide before writing code.
2. **Switching the feed's `LEFT JOIN users` to an `INNER JOIN`.** It is the natural thing to write against
   a normalised schema and it silently removes 81% of production posts. Tests with a real author pass.
3. **Any in-place type change on a large table inside `init_db()`.** `pulse_jobs` (172 MB) and
   `visitor_logs` (74 MB) will take an `ACCESS EXCLUSIVE` lock inside the boot path, on every instance,
   with no migration framework to run it anywhere else.

---

## Appendix A — Reproducing this inventory

```bash
export DBURL=$(railway variables --service Postgres --kv 2>/dev/null | grep '^DATABASE_PUBLIC_URL=' | cut -d= -f2-)
.venv/bin/python -c "
import psycopg2,os
c=psycopg2.connect(os.environ['DBURL'],connect_timeout=20)
cur=c.cursor(); cur.execute('<SELECT>'); print(cur.fetchall())
c.close()"
```

`.venv/bin/python` has `psycopg2` 2.9.12; the system `python3` does not. There is no `psql` binary on this
machine. `export DBURL=...` must be a separate statement — appending it after `-c` makes it argv, not env.

Extracting DDL from `bot.py` (123,710 lines) must be done with `grep`/`awk`/`sed` over line ranges. The
surviving `init_db` body is `bot.py:110179–119009` (`_init_db_impl`); the definition at `bot.py:837` is
dead. Any `CREATE TABLE` extraction must also resolve `{CONSTANT}` and `{function(...)}` interpolation or
it will report 40 false orphans (§1.1).

---

## Appendix B — Full table catalog

All 884 base tables in `public`, from production Postgres 18.6, 2026-09-12.
`est. rows` is the planner's `reltuples` (approximate, and `-1`/`0` means never analyzed —
not necessarily empty). Size is `pg_total_relation_size`, so it includes indexes and TOAST.

| Table | est. rows | total size |
|---|---:|---:|
| `account_audit_logs` | 52 | 32 KB |
| `account_health_events` | 0 | 32 KB |
| `account_recovery_tokens` | 0 | 24 KB |
| `account_restrictions` | 0 | 32 KB |
| `account_strike_appeals` | 0 | 16 KB |
| `account_strikes` | 0 | 32 KB |
| `account_system_events` | 0 | 32 KB |
| `account_warnings` | 0 | 32 KB |
| `active_sessions` | 0 | 16 KB |
| `ad_campaigns` | 0 | 24 KB |
| `ad_clicks` | 0 | 24 KB |
| `ad_creatives` | 0 | 16 KB |
| `ad_images` | 0 | 16 KB |
| `ad_impressions` | 0 | 24 KB |
| `ad_placements` | 0 | 24 KB |
| `ad_reports` | 0 | 16 KB |
| `ad_revenue` | 0 | 16 KB |
| `ad_reviews` | 0 | 16 KB |
| `ad_targeting` | 0 | 16 KB |
| `ad_videos` | 0 | 16 KB |
| `admin_activity_logs` | 107 | 184 KB |
| `admin_approvals` | 0 | 40 KB |
| `admin_audit_logs` | 613 | 400 KB |
| `admin_permissions` | 0 | 48 KB |
| `admin_role_permissions` | 131 | 80 KB |
| `admin_roles` | 0 | 48 KB |
| `admin_session_logs` | 51 | 72 KB |
| `admin_task_comments` | 0 | 24 KB |
| `admin_tasks` | 0 | 48 KB |
| `admin_user_actions` | 0 | 32 KB |
| `admin_user_notes` | 0 | 16 KB |
| `admin_user_roles` | 0 | 24 KB |
| `admin_users` | 2 | 96 KB |
| `ads` | 0 | 24 KB |
| `ads_intel_campaign_daily` | 0 | 32 KB |
| `ads_intel_campaign_pacing` | 0 | 32 KB |
| `ads_intel_creative_daily` | 0 | 32 KB |
| `ads_intel_delivery_decisions` | 0 | 56 KB |
| `ads_intel_diagnostics` | 0 | 40 KB |
| `ads_intel_events` | 0 | 72 KB |
| `ads_intel_frequency_windows` | 0 | 40 KB |
| `ads_intel_ingest_batches` | 0 | 32 KB |
| `ads_intel_interest_affinity` | 0 | 40 KB |
| `ads_intel_signal_policy` | 0 | 32 KB |
| `advertisers` | 0 | 16 KB |
| `ai_action_audit_logs` | 0 | 16 KB |
| `ai_action_requests` | 0 | 32 KB |
| `ai_action_results` | 0 | 16 KB |
| `ai_agents` | 0 | 16 KB |
| `ai_analyses` | 0 | 16 KB |
| `ai_chat_history` | 0 | 16 KB |
| `ai_context_summaries` | 0 | 16 KB |
| `ai_conversations` | 17 | 32 KB |
| `ai_feedback` | 0 | 16 KB |
| `ai_memory_cards` | 0 | 16 KB |
| `ai_messages` | 52 | 80 KB |
| `ai_observability_events` | 0 | 24 KB |
| `ai_recommendations` | 0 | 64 KB |
| `alert_delivery_jobs` | 152,770 | 35 MB |
| `alert_events` | 64,613 | 23 MB |
| `alert_rule_symbol_state` | 0 | 24 KB |
| `alert_rules` | 75 | 4 MB |
| `alert_worker_heartbeat` | 1 | 80 KB |
| `alerts_history` | 0 | 16 KB |
| `analytics_events` | 29,712 | 7 MB |
| `arena_academy_paths` | 0 | 48 KB |
| `arena_academy_progress` | 0 | 16 KB |
| `arena_ai_bosses` | 0 | 48 KB |
| `arena_ai_governors` | 0 | 48 KB |
| `arena_badges` | 0 | 48 KB |
| `arena_blocks` | 0 | 24 KB |
| `arena_boss_attempts` | 0 | 16 KB |
| `arena_chat_messages` | 0 | 64 KB |
| `arena_chat_threads` | 1 | 128 KB |
| `arena_companions` | 0 | 16 KB |
| `arena_crowd_reactions` | 0 | 48 KB |
| `arena_emotes` | 3 | 48 KB |
| `arena_events` | 0 | 32 KB |
| `arena_faction_members` | 0 | 16 KB |
| `arena_factions` | 0 | 48 KB |
| `arena_follows` | 0 | 32 KB |
| `arena_friend_challenges` | 3 | 80 KB |
| `arena_friendships` | 0 | 32 KB |
| `arena_highlights` | 0 | 24 KB |
| `arena_leaderboards` | 0 | 16 KB |
| `arena_legacy` | 0 | 16 KB |
| `arena_live_matches` | 0 | 48 KB |
| `arena_match_chat` | 0 | 32 KB |
| `arena_match_events` | 18 | 80 KB |
| `arena_match_participants` | 9 | 48 KB |
| `arena_matches` | 0 | 72 KB |
| `arena_message_requests` | 5 | 96 KB |
| `arena_mission_attempts` | 0 | 32 KB |
| `arena_missions` | 51 | 192 KB |
| `arena_os_activity` | 0 | 16 KB |
| `arena_play_sessions` | 51 | 120 KB |
| `arena_playbook_comments` | 0 | 16 KB |
| `arena_playbook_votes` | 0 | 16 KB |
| `arena_playbooks` | 0 | 16 KB |
| `arena_player_stories` | 0 | 16 KB |
| `arena_presence` | 5 | 80 KB |
| `arena_profiles` | 5 | 64 KB |
| `arena_psychology_scores` | 0 | 48 KB |
| `arena_quest_progress` | 0 | 16 KB |
| `arena_quests` | 0 | 48 KB |
| `arena_replays` | 0 | 32 KB |
| `arena_reports` | 0 | 16 KB |
| `arena_reputation` | 0 | 16 KB |
| `arena_rivalries` | 0 | 24 KB |
| `arena_roast_lines` | 0 | 64 KB |
| `arena_roast_participants` | 0 | 80 KB |
| `arena_room_messages` | 0 | 32 KB |
| `arena_rooms` | 0 | 32 KB |
| `arena_seasons` | 0 | 48 KB |
| `arena_share_events` | 0 | 32 KB |
| `arena_spectators` | 2 | 48 KB |
| `arena_team_members` | 0 | 16 KB |
| `arena_teams` | 0 | 32 KB |
| `arena_tournament_entries` | 0 | 16 KB |
| `arena_tournaments` | 0 | 48 KB |
| `arena_trade_positions` | 0 | 48 KB |
| `arena_trades` | 0 | 32 KB |
| `arena_user_badges` | 0 | 32 KB |
| `arena_user_preferences` | 0 | 32 KB |
| `arena_victory_events` | 0 | 32 KB |
| `arena_world_events` | 0 | 24 KB |
| `arena_world_history` | 0 | 16 KB |
| `arena_world_state` | 51 | 176 KB |
| `audit_logs` | 0 | 16 KB |
| `auth_events` | 1,092 | 1 MB |
| `backend_feature_registry` | 153 | 160 KB |
| `backend_management_audit_events` | 0 | 16 KB |
| `background_jobs` | 0 | 24 KB |
| `blocked_users` | 0 | 24 KB |
| `brand_deals` | 0 | 16 KB |
| `brevo_contact_sync_logs` | 107 | 80 KB |
| `business_os_ad_account_guardrails` | 0 | 24 KB |
| `business_os_ad_advertisers` | 0 | 24 KB |
| `business_os_ad_audit` | 0 | 32 KB |
| `business_os_ad_billing_events` | 0 | 64 KB |
| `business_os_ad_campaign_funding` | 0 | 24 KB |
| `business_os_ad_campaign_operations` | 0 | 24 KB |
| `business_os_ad_campaigns` | 0 | 32 KB |
| `business_os_ad_click_events` | 0 | 40 KB |
| `business_os_ad_creatives` | 0 | 48 KB |
| `business_os_ad_delivery_instances` | 0 | 56 KB |
| `business_os_ad_funding_ops` | 0 | 32 KB |
| `business_os_ad_impression_events` | 0 | 48 KB |
| `business_os_ad_pricing_policy` | 0 | 32 KB |
| `business_os_ad_sets` | 0 | 40 KB |
| `business_os_ad_spend_accumulator` | 0 | 16 KB |
| `business_os_attr_audit` | 0 | 24 KB |
| `business_os_attr_conversions` | 0 | 32 KB |
| `business_os_attr_credits` | 0 | 48 KB |
| `business_os_attr_touchpoints` | 0 | 40 KB |
| `business_os_business` | 0 | 24 KB |
| `business_os_business_audit` | 0 | 32 KB |
| `business_os_business_locations` | 0 | 24 KB |
| `business_os_business_members` | 0 | 40 KB |
| `business_os_business_policies` | 0 | 32 KB |
| `business_os_cj_account_quota` | 1 | 32 KB |
| `business_os_cj_egress_quota` | 1 | 64 KB |
| `business_os_commerce_thread_links` | 0 | 32 KB |
| `business_os_confirmation_grants` | 0 | 32 KB |
| `business_os_creator_audit` | 0 | 24 KB |
| `business_os_creator_contributions` | 0 | 40 KB |
| `business_os_creator_offerings` | 0 | 32 KB |
| `business_os_creator_supporters` | 0 | 32 KB |
| `business_os_crypto_alert_events` | 0 | 32 KB |
| `business_os_crypto_alerts` | 0 | 24 KB |
| `business_os_crypto_audit` | 0 | 32 KB |
| `business_os_crypto_holdings` | 0 | 24 KB |
| `business_os_crypto_lots` | 0 | 24 KB |
| `business_os_crypto_transactions` | 0 | 32 KB |
| `business_os_ent_audit` | 0 | 48 KB |
| `business_os_ent_catalog` | 0 | 32 KB |
| `business_os_ent_grants` | 0 | 96 KB |
| `business_os_ent_plans` | 0 | 32 KB |
| `business_os_ent_products` | 0 | 32 KB |
| `business_os_ent_provider_subs` | 0 | 64 KB |
| `business_os_ent_usage` | 0 | 16 KB |
| `business_os_event_audit` | 0 | 16 KB |
| `business_os_event_ticket_types` | 0 | 24 KB |
| `business_os_event_tickets` | 0 | 40 KB |
| `business_os_events` | 0 | 24 KB |
| `business_os_l10n_audit` | 0 | 24 KB |
| `business_os_l10n_locales` | 0 | 32 KB |
| `business_os_l10n_resolutions` | 0 | 32 KB |
| `business_os_l10n_strings` | 0 | 40 KB |
| `business_os_merchant_audit` | 0 | 24 KB |
| `business_os_merchant_proposals` | 0 | 32 KB |
| `business_os_merchant_rules` | 0 | 40 KB |
| `business_os_merchant_signals` | 0 | 32 KB |
| `business_os_mkt_audit` | 0 | 32 KB |
| `business_os_mkt_disputes` | 0 | 24 KB |
| `business_os_mkt_inventory_adjustments` | 0 | 32 KB |
| `business_os_mkt_listing_drafts` | 0 | 24 KB |
| `business_os_mkt_offer_events` | 0 | 16 KB |
| `business_os_mkt_offer_reservations` | 0 | 16 KB |
| `business_os_mkt_offers` | 0 | 40 KB |
| `business_os_mkt_order_events` | 0 | 24 KB |
| `business_os_mkt_order_items` | 0 | 24 KB |
| `business_os_mkt_orders` | 0 | 32 KB |
| `business_os_mkt_products` | 0 | 24 KB |
| `business_os_mkt_refunds` | 0 | 24 KB |
| `business_os_mkt_return_events` | 0 | 24 KB |
| `business_os_mkt_returns` | 0 | 40 KB |
| `business_os_mkt_reviews` | 0 | 32 KB |
| `business_os_mkt_seller_ratings` | 0 | 32 KB |
| `business_os_mkt_sellers` | 0 | 16 KB |
| `business_os_perf_audit` | 0 | 24 KB |
| `business_os_perf_samples` | 0 | 32 KB |
| `business_os_perf_summaries` | 0 | 32 KB |
| `business_os_perf_targets` | 0 | 32 KB |
| `business_os_rec_audit` | 0 | 24 KB |
| `business_os_rec_interactions` | 0 | 40 KB |
| `business_os_rec_items` | 0 | 40 KB |
| `business_os_rec_recommendations` | 0 | 32 KB |
| `business_os_seller_profile` | 0 | 32 KB |
| `business_os_seller_profile_addresses` | 0 | 16 KB |
| `business_os_seller_profile_audit` | 0 | 48 KB |
| `business_os_seller_profile_hours` | 0 | 16 KB |
| `business_os_seller_profile_hours_overrides` | 0 | 16 KB |
| `business_os_seller_profile_links` | 0 | 32 KB |
| `business_os_store_audit` | 0 | 64 KB |
| `business_os_store_collection_products` | 0 | 32 KB |
| `business_os_store_collections` | 0 | 24 KB |
| `business_os_store_products` | 0 | 32 KB |
| `business_os_store_return_policy` | 0 | 24 KB |
| `business_os_store_shipping_profiles` | 0 | 24 KB |
| `business_os_store_storefront` | 0 | 32 KB |
| `business_os_store_storefront_versions` | 0 | 32 KB |
| `business_os_supplier_account_owners` | 0 | 32 KB |
| `business_os_supplier_connections` | 1 | 112 KB |
| `business_os_supplier_credential_vault` | 0 | 48 KB |
| `business_os_supplier_drain_ticks` | 0 | 16 KB |
| `business_os_supplier_intents` | 0 | 40 KB |
| `business_os_supplier_outbox` | 0 | 24 KB |
| `business_os_supplier_sync_jobs` | 0 | 32 KB |
| `business_os_undx_action_receipts` | 0 | 32 KB |
| `business_os_undx_action_requests` | 0 | 40 KB |
| `business_os_undx_audit` | 0 | 24 KB |
| `business_os_undx_confirmations` | 0 | 24 KB |
| `business_os_undx_decisions` | 0 | 32 KB |
| `business_os_undx_emergency_stops` | 0 | 24 KB |
| `business_os_undx_permissions` | 0 | 40 KB |
| `business_os_undx_policies` | 0 | 40 KB |
| `business_os_undx_tool_registry` | 0 | 32 KB |
| `business_os_verification_checks` | 0 | 24 KB |
| `business_os_verification_runs` | 0 | 24 KB |
| `capability_audit_results` | 0 | 32 KB |
| `chat_media_uploads` | 729 | 58 MB |
| `chat_memory` | 0 | 16 KB |
| `chat_reports` | 0 | 16 KB |
| `checkout_attempts` | 0 | 72 KB |
| `comm_v2_attachments` | 54 | 184 KB |
| `comm_v2_blocks` | 0 | 24 KB |
| `comm_v2_channels` | 0 | 32 KB |
| `comm_v2_communities` | 0 | 32 KB |
| `comm_v2_conversation_items` | 0 | 32 KB |
| `comm_v2_conversation_settings` | 4 | 112 KB |
| `comm_v2_conversations` | 32 | 160 KB |
| `comm_v2_live_streams` | 0 | 40 KB |
| `comm_v2_message_deletions` | 0 | 64 KB |
| `comm_v2_message_reactions` | 0 | 48 KB |
| `comm_v2_messages` | 1,081 | 704 KB |
| `comm_v2_moderation_events` | 0 | 16 KB |
| `comm_v2_participants` | 91 | 640 KB |
| `comm_v2_presence` | 9 | 128 KB |
| `comm_v2_read_receipts` | 1,132 | 151 MB |
| `comm_v2_reports` | 0 | 16 KB |
| `comm_v2_typing` | 62 | 112 KB |
| `comm_v2_user_settings` | 0 | 16 KB |
| `command_center_ai_events` | 0 | 144 KB |
| `command_center_message_events` | 9,473 | 6 MB |
| `command_center_notification_events` | 591 | 800 KB |
| `command_center_security_events` | 0 | 192 KB |
| `command_history` | 108 | 168 KB |
| `communication_call_device_sessions` | 0 | 16 KB |
| `communication_call_events` | 8,060 | 2 MB |
| `communication_call_participants` | 821 | 336 KB |
| `communication_call_quality_reports` | 419 | 208 KB |
| `communication_calls` | 415 | 392 KB |
| `connect_account_state` | 0 | 24 KB |
| `connected_wallets` | 0 | 24 KB |
| `conversation_members` | 4 | 128 KB |
| `conversations` | 2 | 64 KB |
| `conversion_funnel_events` | 26,476 | 12 MB |
| `creator_balances` | 0 | 32 KB |
| `creator_dashboard_metrics` | 0 | 24 KB |
| `creator_ledger_entries` | 0 | 40 KB |
| `creator_payouts` | 0 | 24 KB |
| `creator_payouts_placeholder` | 0 | 16 KB |
| `creator_profiles` | 0 | 32 KB |
| `creator_revenue_events` | 0 | 16 KB |
| `creator_tax_profiles` | 0 | 24 KB |
| `creator_transactions` | 0 | 48 KB |
| `creator_wallets` | 0 | 32 KB |
| `crypto_ai_queries` | 0 | 48 KB |
| `crypto_alerts` | 0 | 48 KB |
| `crypto_audit_logs` | 202 | 120 KB |
| `crypto_favorite_assets` | 0 | 48 KB |
| `crypto_news_cache` | 53 | 112 KB |
| `crypto_recent_assets` | 12 | 80 KB |
| `crypto_watchlist_assets` | 0 | 48 KB |
| `crypto_watchlists` | 0 | 48 KB |
| `daily_briefs` | 0 | 24 KB |
| `dashboard_audit_logs` | 0 | 24 KB |
| `dashboard_categories` | 0 | 24 KB |
| `dashboard_entitlements` | 0 | 24 KB |
| `dashboard_events` | 0 | 16 KB |
| `dashboard_modules` | 0 | 32 KB |
| `dashboard_permissions` | 0 | 24 KB |
| `dashboard_recommendations` | 0 | 16 KB |
| `dashboard_usage` | 0 | 24 KB |
| `dashboard_visibility` | 0 | 24 KB |
| `dashboard_widget_access_rules` | 0 | 16 KB |
| `dashboard_widgets` | 0 | 24 KB |
| `day_signal_results` | 0 | 32 KB |
| `delivery_logs` | 0 | 24 KB |
| `department_members` | 0 | 16 KB |
| `departments` | 24 | 200 KB |
| `education_ai_tutor_logs` | 0 | 32 KB |
| `education_badges` | 0 | 24 KB |
| `education_categories` | 0 | 48 KB |
| `education_lesson_views` | 743 | 240 KB |
| `education_lessons` | 0 | 88 KB |
| `education_progress` | 0 | 24 KB |
| `education_quiz_questions` | 0 | 64 KB |
| `education_quizzes` | 0 | 32 KB |
| `education_sections` | 80 | 72 KB |
| `education_user_progress` | 0 | 24 KB |
| `email_logs` | 2,584 | 864 KB |
| `email_verification_tokens` | 36 | 48 KB |
| `email_verifications` | 0 | 16 KB |
| `employees` | 0 | 32 KB |
| `engagement_events` | 0 | 16 KB |
| `enterprise_leads` | 0 | 16 KB |
| `escrow_holds` | 0 | 32 KB |
| `event_bus_events` | 0 | 64 KB |
| `expo_push_tickets` | 6,993 | 3 MB |
| `failed_email_queue` | 2,100 | 5 MB |
| `failed_login_controls` | 0 | 32 KB |
| `failed_login_safe_list` | 0 | 32 KB |
| `feature_flags` | 15 | 96 KB |
| `fee_ledger` | 0 | 32 KB |
| `founder_memberships` | 0 | 32 KB |
| `founder_wall_entries` | 0 | 32 KB |
| `global_events` | 0 | 24 KB |
| `global_intelligence_edges` | 0 | 32 KB |
| `global_intelligence_nodes` | 0 | 32 KB |
| `global_intelligence_signals` | 0 | 24 KB |
| `global_intelligence_snapshots` | 0 | 104 KB |
| `i18n_missing_translations` | 1,457 | 400 KB |
| `intelligence_alert_cadence` | 0 | 64 KB |
| `intelligence_collector_runs` | 0 | 32 KB |
| `intelligence_delivery_jobs` | 0 | 40 KB |
| `intelligence_delivery_log` | 82 | 128 KB |
| `intelligence_digest_jobs` | 0 | 24 KB |
| `intelligence_events` | 0 | 208 KB |
| `intelligence_feedback` | 0 | 24 KB |
| `intelligence_forecasts` | 0 | 24 KB |
| `intelligence_sources` | 24 | 104 KB |
| `intelligence_streams` | 10 | 48 KB |
| `last_prices` | 0 | 16 KB |
| `last_signals` | 0 | 16 KB |
| `leads` | 0 | 24 KB |
| `ledger_balances` | 0 | 16 KB |
| `ledger_entries` | 0 | 32 KB |
| `ledger_transactions` | 0 | 32 KB |
| `live_events` | 0 | 104 KB |
| `live_ops_plans` | 0 | 96 KB |
| `livestream_access` | 2 | 64 KB |
| `livestream_eligibility` | 2 | 64 KB |
| `manual_portfolio` | 0 | 16 KB |
| `market_observations` | 40,100 | 40 MB |
| `marketplace_buyer_interest` | 0 | 16 KB |
| `marketplace_cart_checkout_keys` | 0 | 80 KB |
| `marketplace_cart_items` | 0 | 48 KB |
| `marketplace_commercial_refunds` | 0 | 24 KB |
| `marketplace_commercial_settlements` | 0 | 24 KB |
| `marketplace_digital_files` | 0 | 32 KB |
| `marketplace_inventory_reservations` | 8 | 64 KB |
| `marketplace_ip_case_events` | 0 | 16 KB |
| `marketplace_ip_cases` | 0 | 16 KB |
| `marketplace_listing_variants` | 223 | 176 KB |
| `marketplace_listings` | 0 | 112 KB |
| `marketplace_merchant_applications` | 1 | 48 KB |
| `marketplace_merchant_documents` | 6 | 48 KB |
| `marketplace_offers` | 0 | 40 KB |
| `marketplace_orders` | 0 | 24 KB |
| `marketplace_orders_placeholder` | 0 | 16 KB |
| `marketplace_payout_state_events` | 0 | 24 KB |
| `marketplace_product_media` | 0 | 48 KB |
| `marketplace_product_sources` | 0 | 80 KB |
| `marketplace_reconciliation_runs` | 0 | 16 KB |
| `marketplace_reports` | 0 | 32 KB |
| `marketplace_return_events` | 0 | 16 KB |
| `marketplace_returns` | 0 | 32 KB |
| `marketplace_saved_products` | 0 | 48 KB |
| `marketplace_seller_compliance` | 0 | 16 KB |
| `marketplace_seller_terms_acceptances` | 0 | 24 KB |
| `marketplace_sellers` | 1 | 64 KB |
| `message_attachments` | 80 | 176 KB |
| `message_read_receipts` | 0 | 48 KB |
| `mobile_security_sessions` | 10,052 | 12 MB |
| `moderation_cases` | 0 | 24 KB |
| `monetization_events` | 0 | 16 KB |
| `notification_delivery_jobs` | 8,840 | 16 MB |
| `notification_delivery_logs` | 155,301 | 62 MB |
| `notification_device_tokens` | 23 | 296 KB |
| `notification_events` | 4,107 | 8 MB |
| `notification_failures` | 0 | 24 KB |
| `notification_jobs` | 0 | 32 KB |
| `notification_logs` | 4,202 | 1 MB |
| `notification_preferences` | 1,262 | 2 MB |
| `notification_schedules` | 0 | 24 KB |
| `notifications` | 62,279 | 60 MB |
| `paper_portfolio` | 0 | 16 KB |
| `paper_simulator_trades` | 0 | 32 KB |
| `paper_simulator_wallets` | 0 | 48 KB |
| `password_reset_tokens` | 24 | 64 KB |
| `password_resets` | 0 | 16 KB |
| `payment_audit_logs` | 0 | 24 KB |
| `payment_email_logs` | 0 | 64 KB |
| `payment_records` | 0 | 32 KB |
| `payment_verifications` | 0 | 16 KB |
| `payment_webhook_events` | 26 | 248 KB |
| `payout_failures` | 0 | 16 KB |
| `payout_history` | 0 | 16 KB |
| `payout_queue` | 0 | 24 KB |
| `performance_traces` | 13,413 | 10 MB |
| `permissions` | 0 | 48 KB |
| `platform_fee_rules` | 3 | 64 KB |
| `platform_payouts` | 0 | 16 KB |
| `platform_wallets` | 0 | 32 KB |
| `portfolio_advice_history` | 0 | 16 KB |
| `portfolio_items` | 0 | 32 KB |
| `portfolio_outbox` | 0 | 24 KB |
| `portfolio_snapshots` | 3,714 | 5 MB |
| `prediction_markets` | 0 | 16 KB |
| `prediction_watches` | 0 | 48 KB |
| `premium_badges` | 0 | 24 KB |
| `premium_entitlements` | 0 | 48 KB |
| `presence_last_seen` | 14 | 232 KB |
| `presence_privacy_settings` | 0 | 16 KB |
| `presence_sessions` | 4,175 | 5 MB |
| `price_history` | 0 | 32 KB |
| `privacy_preferences` | 0 | 16 KB |
| `private_audit_events` | 410 | 192 KB |
| `private_concierge_messages` | 0 | 24 KB |
| `private_decisions` | 0 | 48 KB |
| `private_document_claims` | 0 | 24 KB |
| `private_documents` | 0 | 48 KB |
| `private_domain_events` | 0 | 48 KB |
| `private_fact_conflicts` | 0 | 32 KB |
| `private_fact_evidence` | 0 | 32 KB |
| `private_fact_history` | 0 | 24 KB |
| `private_facts` | 0 | 72 KB |
| `private_graph_edges` | 0 | 40 KB |
| `private_graph_nodes` | 0 | 64 KB |
| `private_meeting_artifacts` | 0 | 24 KB |
| `private_meeting_invites` | 0 | 32 KB |
| `private_meeting_messages` | 0 | 24 KB |
| `private_meeting_participants` | 0 | 80 KB |
| `private_meeting_recordings` | 0 | 24 KB |
| `private_meetings` | 0 | 96 KB |
| `private_messages` | 10 | 96 KB |
| `private_obligations` | 0 | 96 KB |
| `private_office_briefing_items` | 0 | 24 KB |
| `private_office_briefings` | 0 | 48 KB |
| `private_office_jobs` | 0 | 48 KB |
| `private_office_security` | 1 | 48 KB |
| `private_office_unlock_grants` | 51 | 112 KB |
| `private_opportunities` | 0 | 40 KB |
| `private_projects` | 0 | 48 KB |
| `private_record_links` | 0 | 40 KB |
| `private_requests` | 0 | 96 KB |
| `private_risks` | 0 | 48 KB |
| `private_shield_findings` | 0 | 64 KB |
| `private_tasks` | 0 | 48 KB |
| `product_health_checks` | 0 | 24 KB |
| `profile_audit_logs` | 0 | 120 KB |
| `progress_events` | 0 | 24 KB |
| `progress_milestone_awards` | 0 | 24 KB |
| `progress_missions` | 0 | 24 KB |
| `progress_posting_days` | 3 | 80 KB |
| `progress_referral_qualifications` | 0 | 32 KB |
| `progress_reward_cycles` | 0 | 24 KB |
| `promo_codes` | 0 | 24 KB |
| `provider_health` | 0 | 24 KB |
| `provider_webhook_events` | 0 | 200 KB |
| `pulse_account_data_requests` | 0 | 48 KB |
| `pulse_ad_account_profiles` | 0 | 24 KB |
| `pulse_ad_accounts` | 0 | 48 KB |
| `pulse_ad_adsets` | 0 | 64 KB |
| `pulse_ad_appeals` | 0 | 32 KB |
| `pulse_ad_attributions` | 0 | 40 KB |
| `pulse_ad_audit_logs` | 52 | 96 KB |
| `pulse_ad_billing_events` | 0 | 32 KB |
| `pulse_ad_billing_profiles` | 0 | 32 KB |
| `pulse_ad_campaign_history` | 0 | 80 KB |
| `pulse_ad_campaign_placements` | 0 | 32 KB |
| `pulse_ad_campaigns` | 1 | 80 KB |
| `pulse_ad_clicks` | 0 | 32 KB |
| `pulse_ad_creatives` | 0 | 80 KB |
| `pulse_ad_daily_aggregates` | 0 | 24 KB |
| `pulse_ad_events` | 0 | 24 KB |
| `pulse_ad_frequency_caps` | 0 | 32 KB |
| `pulse_ad_idempotency` | 0 | 64 KB |
| `pulse_ad_impressions` | 0 | 40 KB |
| `pulse_ad_invoices` | 0 | 64 KB |
| `pulse_ad_jobs` | 0 | 80 KB |
| `pulse_ad_media_assets` | 0 | 96 KB |
| `pulse_ad_moderation_queue` | 0 | 48 KB |
| `pulse_ad_notifications` | 0 | 48 KB |
| `pulse_ad_placements` | 12 | 120 KB |
| `pulse_ad_platform_settings` | 0 | 16 KB |
| `pulse_ad_policy_flags` | 0 | 24 KB |
| `pulse_ad_receipts` | 0 | 64 KB |
| `pulse_ad_refunds` | 0 | 24 KB |
| `pulse_ad_review_board` | 0 | 48 KB |
| `pulse_ad_saved_audiences` | 0 | 24 KB |
| `pulse_ad_targeting` | 0 | 48 KB |
| `pulse_ad_team_members` | 0 | 24 KB |
| `pulse_ad_wallet_events` | 0 | 24 KB |
| `pulse_ad_wallet_funding_sessions` | 0 | 96 KB |
| `pulse_ad_wallet_transactions` | 0 | 80 KB |
| `pulse_ad_wallets` | 0 | 64 KB |
| `pulse_ai_capability_registry` | 95 | 104 KB |
| `pulse_ai_client_contexts` | 5 | 80 KB |
| `pulse_ai_confirmations` | 22 | 112 KB |
| `pulse_ai_conversation_context_permissions` | 0 | 48 KB |
| `pulse_ai_conversations` | 10 | 64 KB |
| `pulse_ai_delegated_policies` | 0 | 24 KB |
| `pulse_ai_engagement` | 0 | 16 KB |
| `pulse_ai_feature_registry` | 54 | 80 KB |
| `pulse_ai_feedback` | 0 | 48 KB |
| `pulse_ai_knowledge_edges` | 0 | 24 KB |
| `pulse_ai_knowledge_items` | 82 | 120 KB |
| `pulse_ai_learning_events` | 398 | 216 KB |
| `pulse_ai_memory` | 38 | 128 KB |
| `pulse_ai_memory_provenance` | 0 | 24 KB |
| `pulse_ai_messages` | 694 | 856 KB |
| `pulse_ai_missions` | 0 | 40 KB |
| `pulse_ai_posts` | 1,671 | 3 MB |
| `pulse_ai_provider_events` | 238 | 152 KB |
| `pulse_ai_rotation_state` | 1 | 32 KB |
| `pulse_ai_safety_events` | 349 | 160 KB |
| `pulse_ai_safety_reviews` | 0 | 24 KB |
| `pulse_ai_schedules` | 76 | 22 MB |
| `pulse_ai_search_sessions` | 0 | 64 KB |
| `pulse_ai_skill_registry` | 0 | 48 KB |
| `pulse_ai_task_nodes` | 0 | 32 KB |
| `pulse_ai_tool_operations` | 0 | 96 KB |
| `pulse_ai_tool_registry` | 95 | 112 KB |
| `pulse_ai_topics` | 0 | 24 KB |
| `pulse_ai_truth_facts` | 0 | 24 KB |
| `pulse_ai_user_memory` | 0 | 24 KB |
| `pulse_ai_verification_events` | 0 | 24 KB |
| `pulse_ai_web_search_logs` | 0 | 112 KB |
| `pulse_audio_tracks` | 21,273 | 17 MB |
| `pulse_badges` | 22 | 80 KB |
| `pulse_briefing_prefs` | 0 | 32 KB |
| `pulse_briefings` | 508 | 744 KB |
| `pulse_camera_captures` | 0 | 32 KB |
| `pulse_camera_effects` | 0 | 48 KB |
| `pulse_camera_previews` | 0 | 80 KB |
| `pulse_chat_health_traces` | 868 | 304 KB |
| `pulse_chat_recovery_events` | 0 | 24 KB |
| `pulse_chat_room_members` | 360 | 256 KB |
| `pulse_chat_room_messages` | 0 | 48 KB |
| `pulse_chat_rooms` | 8 | 280 KB |
| `pulse_comment_reactions` | 0 | 24 KB |
| `pulse_comments` | 38 | 64 KB |
| `pulse_content_music` | 64 | 176 KB |
| `pulse_content_preferences` | 0 | 32 KB |
| `pulse_content_promotion_audit` | 0 | 48 KB |
| `pulse_content_promotions` | 2 | 72 KB |
| `pulse_content_sentiment` | 0 | 16 KB |
| `pulse_content_translations` | 0 | 128 KB |
| `pulse_conversation_participants` | 374 | 304 KB |
| `pulse_conversation_typing` | 14 | 96 KB |
| `pulse_conversations` | 295 | 424 KB |
| `pulse_courses` | 0 | 16 KB |
| `pulse_creator_analytics` | 0 | 24 KB |
| `pulse_creator_audience_segments` | 0 | 24 KB |
| `pulse_creator_energy_snapshots` | 0 | 24 KB |
| `pulse_creator_growth_profiles` | 13 | 48 KB |
| `pulse_credit_ledger` | 0 | 32 KB |
| `pulse_daily_mentor_conversations` | 0 | 48 KB |
| `pulse_daily_mentor_messages` | 0 | 24 KB |
| `pulse_filters` | 0 | 32 KB |
| `pulse_follows` | 12 | 64 KB |
| `pulse_friend_requests` | 1 | 80 KB |
| `pulse_friends` | 0 | 48 KB |
| `pulse_friendships` | 0 | 32 KB |
| `pulse_generated_media` | 66 | 112 KB |
| `pulse_group_action_logs` | 0 | 24 KB |
| `pulse_group_bans` | 0 | 32 KB |
| `pulse_group_comment_reports` | 0 | 16 KB |
| `pulse_group_creation_attempts` | 0 | 32 KB |
| `pulse_group_invites` | 0 | 16 KB |
| `pulse_group_members` | 0 | 32 KB |
| `pulse_group_post_comments` | 0 | 24 KB |
| `pulse_group_post_media` | 0 | 32 KB |
| `pulse_group_post_reactions` | 0 | 32 KB |
| `pulse_group_post_reports` | 0 | 16 KB |
| `pulse_group_posts` | 1 | 48 KB |
| `pulse_group_reports` | 0 | 16 KB |
| `pulse_group_roles` | 0 | 48 KB |
| `pulse_groups` | 1 | 96 KB |
| `pulse_growth_accounts` | 13 | 80 KB |
| `pulse_growth_ai_sessions` | 13 | 64 KB |
| `pulse_growth_analytics_containers` | 13 | 80 KB |
| `pulse_growth_api_keys` | 0 | 48 KB |
| `pulse_growth_audience_models` | 13 | 48 KB |
| `pulse_growth_audience_profiles` | 13 | 48 KB |
| `pulse_growth_billing_profiles` | 13 | 48 KB |
| `pulse_growth_ledger` | 0 | 64 KB |
| `pulse_growth_preferences` | 13 | 48 KB |
| `pulse_growth_promotion_history` | 0 | 48 KB |
| `pulse_growth_provisioning_log` | 168 | 112 KB |
| `pulse_growth_risk_profiles` | 13 | 80 KB |
| `pulse_growth_scores` | 13 | 48 KB |
| `pulse_growth_trust_links` | 13 | 48 KB |
| `pulse_growth_wallets` | 13 | 48 KB |
| `pulse_growth_workspaces` | 13 | 80 KB |
| `pulse_identity_effects` | 0 | 48 KB |
| `pulse_jobs` | 968,096 | 234 MB |
| `pulse_lesson_media` | 0 | 16 KB |
| `pulse_lessons` | 0 | 16 KB |
| `pulse_live_archive_shares` | 312 | 152 KB |
| `pulse_live_audio_profiles` | 0 | 32 KB |
| `pulse_live_audit_logs` | 339 | 688 KB |
| `pulse_live_chat` | 482 | 192 KB |
| `pulse_live_classes` | 0 | 16 KB |
| `pulse_live_clips` | 0 | 16 KB |
| `pulse_live_destinations` | 0 | 48 KB |
| `pulse_live_events` | 14,972 | 7 MB |
| `pulse_live_guest_requests` | 9 | 120 KB |
| `pulse_live_guests` | 7 | 96 KB |
| `pulse_live_moderation` | 0 | 16 KB |
| `pulse_live_provider_events` | 3,678 | 4 MB |
| `pulse_live_reactions` | 109 | 64 KB |
| `pulse_live_reports` | 0 | 16 KB |
| `pulse_live_restream_targets` | 240 | 120 KB |
| `pulse_live_scene_presets` | 0 | 32 KB |
| `pulse_live_sessions` | 280 | 1 MB |
| `pulse_live_streams` | 252 | 296 KB |
| `pulse_live_viewers` | 309 | 144 KB |
| `pulse_live_webrtc_signals` | 0 | 136 KB |
| `pulse_media_assets` | 169 | 184 KB |
| `pulse_media_upload_sessions` | 36 | 152 KB |
| `pulse_message_reactions` | 0 | 32 KB |
| `pulse_message_receipts` | 0 | 64 KB |
| `pulse_message_reports` | 0 | 16 KB |
| `pulse_message_threads` | 3 | 80 KB |
| `pulse_messages` | 29 | 144 KB |
| `pulse_music_events` | 7,579 | 2 MB |
| `pulse_music_reports` | 0 | 24 KB |
| `pulse_muted_users` | 0 | 32 KB |
| `pulse_notification_deliveries` | 14,551 | 3 MB |
| `pulse_notification_devices` | 18 | 448 KB |
| `pulse_notification_preferences` | 101 | 88 KB |
| `pulse_notifications` | 12,041 | 26 MB |
| `pulse_online_sessions` | 2,207 | 1 MB |
| `pulse_page_audit` | 0 | 48 KB |
| `pulse_page_follows` | 0 | 64 KB |
| `pulse_page_links` | 0 | 48 KB |
| `pulse_page_members` | 0 | 64 KB |
| `pulse_pages` | 0 | 64 KB |
| `pulse_payment_events` | 0 | 16 KB |
| `pulse_post_attempts` | 107 | 80 KB |
| `pulse_post_hides` | 0 | 24 KB |
| `pulse_post_saves` | 2 | 80 KB |
| `pulse_post_views` | 3,079 | 624 KB |
| `pulse_posts` | 2,343 | 5 MB |
| `pulse_premium_audit_logs` | 0 | 32 KB |
| `pulse_premium_entitlements` | 0 | 64 KB |
| `pulse_premium_feature_flags` | 0 | 48 KB |
| `pulse_premium_profiles` | 0 | 48 KB |
| `pulse_privileges` | 0 | 48 KB |
| `pulse_profile_themes` | 7 | 80 KB |
| `pulse_quiz_questions` | 0 | 16 KB |
| `pulse_quizzes` | 0 | 16 KB |
| `pulse_reactions` | 87 | 112 KB |
| `pulse_reel_audio` | 0 | 64 KB |
| `pulse_reel_retention_events` | 0 | 16 KB |
| `pulse_reel_sound_saves` | 0 | 64 KB |
| `pulse_reels` | 67 | 360 KB |
| `pulse_region_preference_events` | 0 | 24 KB |
| `pulse_region_preferences` | 0 | 16 KB |
| `pulse_reports` | 0 | 48 KB |
| `pulse_room_members` | 0 | 24 KB |
| `pulse_room_messages` | 0 | 24 KB |
| `pulse_saved_collections` | 0 | 80 KB |
| `pulse_saved_items` | 0 | 64 KB |
| `pulse_saved_sounds` | 0 | 64 KB |
| `pulse_space_members` | 2 | 64 KB |
| `pulse_status` | 51 | 136 KB |
| `pulse_status_live` | 0 | 16 KB |
| `pulse_status_media` | 51 | 72 KB |
| `pulse_status_music` | 0 | 32 KB |
| `pulse_status_reactions` | 51 | 48 KB |
| `pulse_status_replies` | 0 | 32 KB |
| `pulse_status_shares` | 0 | 32 KB |
| `pulse_status_views` | 218 | 88 KB |
| `pulse_statuses` | 0 | 16 KB |
| `pulse_stories` | 0 | 16 KB |
| `pulse_story_reactions` | 0 | 16 KB |
| `pulse_story_views` | 0 | 16 KB |
| `pulse_student_enrollments` | 0 | 24 KB |
| `pulse_subscriptions` | 0 | 16 KB |
| `pulse_teacher_applications` | 0 | 24 KB |
| `pulse_teacher_documents` | 0 | 16 KB |
| `pulse_teacher_profiles` | 0 | 24 KB |
| `pulse_teacher_reviews` | 0 | 16 KB |
| `pulse_translation_events` | 0 | 48 KB |
| `pulse_translation_preferences` | 0 | 48 KB |
| `pulse_trending_sounds` | 1,443 | 272 KB |
| `pulse_user_badges` | 20 | 64 KB |
| `pulse_user_mutes` | 0 | 24 KB |
| `pulse_user_privileges` | 42 | 128 KB |
| `pulse_video_categories` | 0 | 24 KB |
| `pulse_video_comments` | 0 | 32 KB |
| `pulse_video_reactions` | 0 | 80 KB |
| `pulse_video_views` | 310 | 104 KB |
| `pulse_videos` | 241 | 312 KB |
| `pulsesoc_content_campaigns` | 0 | 24 KB |
| `pulsesoc_content_planner_items` | 0 | 24 KB |
| `pulsesoc_dashboard_preferences` | 0 | 48 KB |
| `pulsesoc_premium_exploration` | 0 | 24 KB |
| `pulsesoc_seller_products` | 0 | 24 KB |
| `pulsesoc_seller_stores` | 0 | 24 KB |
| `pulsesoc_user_goals` | 0 | 48 KB |
| `push_delivery_jobs` | 2,320 | 4 MB |
| `push_subscriptions` | 20 | 464 KB |
| `referral_conversions` | 0 | 24 KB |
| `referral_deferred_claims` | 0 | 32 KB |
| `referral_events` | 0 | 32 KB |
| `referral_invites` | 0 | 16 KB |
| `referral_rewards` | 0 | 24 KB |
| `reliability_snapshots` | 0 | 24 KB |
| `reputation_ledger` | 0 | 24 KB |
| `revenue_breakdown` | 0 | 32 KB |
| `reward_events` | 0 | 40 KB |
| `risk_scores` | 0 | 16 KB |
| `roast_matches` | 0 | 32 KB |
| `roast_messages` | 7 | 48 KB |
| `roast_reactions` | 0 | 48 KB |
| `roast_rooms` | 0 | 16 KB |
| `roast_votes` | 0 | 48 KB |
| `role_permissions` | 113 | 80 KB |
| `roles` | 0 | 48 KB |
| `saved_command_results` | 0 | 16 KB |
| `saved_insights` | 0 | 16 KB |
| `saved_wallets` | 0 | 24 KB |
| `scam_alerts` | 0 | 16 KB |
| `scam_reports` | 0 | 16 KB |
| `scam_scans` | 0 | 48 KB |
| `scam_shield_scans` | 0 | 96 KB |
| `security_devices` | 0 | 16 KB |
| `security_events` | 3,597 | 2 MB |
| `security_login_events` | 0 | 16 KB |
| `security_reports` | 0 | 32 KB |
| `seller_application_assignments` | 0 | 24 KB |
| `seller_application_notes` | 0 | 24 KB |
| `seller_application_status_history` | 0 | 48 KB |
| `seller_payout_accounts` | 0 | 32 KB |
| `seller_payout_events` | 0 | 24 KB |
| `seller_payout_requests` | 0 | 40 KB |
| `seller_payouts` | 0 | 16 KB |
| `seller_transactions` | 22 | 80 KB |
| `sentinel_dependency_inventory` | 0 | 40 KB |
| `sentinel_detection_exclusions` | 0 | 32 KB |
| `sentinel_edges` | 0 | 24 KB |
| `sentinel_enrichment_requests` | 0 | 40 KB |
| `sentinel_events` | 0 | 64 KB |
| `sentinel_evidence` | 0 | 32 KB |
| `sentinel_external_data_audit` | 0 | 32 KB |
| `sentinel_external_observations` | 0 | 64 KB |
| `sentinel_external_providers` | 0 | 32 KB |
| `sentinel_financial_exposure` | 0 | 24 KB |
| `sentinel_financial_reconciliations` | 0 | 24 KB |
| `sentinel_financial_risk` | 0 | 32 KB |
| `sentinel_health_snapshots` | 0 | 24 KB |
| `sentinel_identity_risk` | 0 | 32 KB |
| `sentinel_incident_transitions` | 0 | 16 KB |
| `sentinel_incidents` | 0 | 32 KB |
| `sentinel_metrics` | 0 | 16 KB |
| `sentinel_provider_capabilities` | 0 | 24 KB |
| `sentinel_provider_circuits` | 0 | 24 KB |
| `sentinel_rate_counters` | 0 | 32 KB |
| `sentinel_runbook_executions` | 0 | 24 KB |
| `sentinel_sequence_firings` | 0 | 32 KB |
| `sentinel_vulnerability_findings` | 0 | 56 KB |
| `sessions` | 226 | 184 KB |
| `settlement_batches` | 0 | 24 KB |
| `simulator_accounts` | 0 | 24 KB |
| `simulator_ai_coaching_logs` | 0 | 16 KB |
| `simulator_lessons` | 0 | 24 KB |
| `simulator_orders` | 0 | 16 KB |
| `simulator_progress` | 0 | 24 KB |
| `simulator_trades` | 0 | 16 KB |
| `simulator_watchlists` | 0 | 24 KB |
| `sms_delivery_logs` | 0 | 16 KB |
| `sms_verification_codes` | 0 | 24 KB |
| `sponsor_slots` | 0 | 24 KB |
| `sponsorships` | 0 | 24 KB |
| `stripe_events` | 0 | 176 KB |
| `subscription_plans` | 3 | 120 KB |
| `subscriptions` | 51 | 64 KB |
| `supplier_import_cart_items` | 0 | 112 KB |
| `supplier_import_drafts` | 0 | 16 KB |
| `supplier_read_cache` | 34 | 208 KB |
| `supplier_snapshots` | 0 | 160 KB |
| `support_notes` | 0 | 16 KB |
| `support_ticket_messages` | 0 | 32 KB |
| `support_tickets` | 2 | 48 KB |
| `system_health_snapshots` | 0 | 80 KB |
| `teacher_applications` | 0 | 24 KB |
| `teacher_earnings_placeholder` | 0 | 16 KB |
| `teacher_lessons` | 0 | 16 KB |
| `teacher_profiles` | 0 | 24 KB |
| `telegram_debug_events` | 52 | 96 KB |
| `telegram_delivery_logs` | 0 | 16 KB |
| `telegram_link_codes` | 583 | 288 KB |
| `telegram_notifications` | 0 | 16 KB |
| `transaction_history` | 0 | 16 KB |
| `transactions` | 0 | 32 KB |
| `treasury_transactions` | 0 | 40 KB |
| `trial_email_events` | 100 | 80 KB |
| `undx_agent_runs` | 0 | 40 KB |
| `undx_cost_ledger` | 1 | 48 KB |
| `undx_embedding_cache` | 1,705 | 3 MB |
| `undx_provider_health` | 0 | 48 KB |
| `undx_semantic_index` | 1,667 | 1016 KB |
| `unmatched_payments` | 0 | 32 KB |
| `usage_events` | 0 | 16 KB |
| `user_activity` | 0 | 32 KB |
| `user_ai_interactions` | 51 | 128 KB |
| `user_alert_rules` | 0 | 16 KB |
| `user_alerts` | 0 | 32 KB |
| `user_dashboard_metrics` | 0 | 24 KB |
| `user_dashboard_preferences` | 0 | 48 KB |
| `user_dashboard_widget_state` | 0 | 24 KB |
| `user_device_tokens` | 19 | 176 KB |
| `user_education_preferences` | 0 | 48 KB |
| `user_entitlements` | 0 | 48 KB |
| `user_intelligence_streams` | 20 | 64 KB |
| `user_onboarding_progress` | 0 | 32 KB |
| `user_portfolio_settings` | 0 | 8 KB |
| `user_presence` | 13 | 96 KB |
| `user_privilege_profiles` | 8 | 72 KB |
| `user_privilege_snapshots` | 0 | 16 KB |
| `user_recovery_codes` | 0 | 32 KB |
| `user_reputation_scores` | 0 | 16 KB |
| `user_security_events` | 51 | 72 KB |
| `user_settings` | 28 | 120 KB |
| `user_streaks` | 4 | 80 KB |
| `user_subscriptions` | 0 | 24 KB |
| `user_trust_events` | 0 | 16 KB |
| `user_trust_profiles` | 7 | 64 KB |
| `user_trust_score` | 0 | 32 KB |
| `user_trusted_devices` | 0 | 16 KB |
| `user_verifications` | 0 | 32 KB |
| `user_watch_items` | 0 | 48 KB |
| `user_welcome_events` | 18 | 64 KB |
| `users` | 39 | 12 MB |
| `verification_appeals` | 0 | 16 KB |
| `verification_audit_logs` | 0 | 32 KB |
| `verification_badges` | 0 | 24 KB |
| `verification_documents` | 0 | 32 KB |
| `verification_requests` | 14,086 | 3 MB |
| `visitor_logs` | 206,807 | 78 MB |
| `visitor_sessions` | 32,668 | 34 MB |
| `wallet_risk_checks` | 0 | 16 KB |
| `watch_rules` | 0 | 32 KB |
| `watchlist_items` | 0 | 48 KB |
| `watchlists` | 0 | 16 KB |
| `whale_alerts` | 0 | 16 KB |
| `whale_intelligence` | 235 | 128 KB |
| `worker_heartbeats` | 5 | 7 MB |

