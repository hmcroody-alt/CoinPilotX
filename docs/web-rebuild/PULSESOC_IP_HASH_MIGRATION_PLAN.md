# The `ip_hash` salt migration — investigation and plan

Follow-up to `PULSESOC_WEB_SECURITY_MODEL.md` §6.1 and `PULSESOC_WEB_SECURITY_FINDINGS_URGENT.md`
SEC-4, both of which deferred this with "fixing it is a migration, not an edit". This document is
the migration. **Nothing here has been executed.** No DDL has been run anywhere, and the
production database was opened read-only.

Every number below was measured against production on 2026-09-13, not inferred from the source.

---

## 1. The premise, re-verified — and it is worse than documented

§6.1 says `ANALYTICS_SALT` is unset in production, so `client_ip_hash()` is
`sha256("coinpilotxai-inc:" + ip)`. That was established from `railway variables`, which shows
what is *configured*. It does not show what the stored data was actually computed with — a
variable could have been set and later removed, leaving rows from both eras.

This was checked against the data instead. `auth_events.ip_address` holds **707 rows / 188
distinct raw IPv4 addresses in plaintext**. Hashing those 188 addresses with the committed
fallback constant and looking for the results in every hash column:

| table | column | matched distinct | of total distinct | rows |
|---|---|---|---|---|
| `mobile_security_sessions` | `ip_hash` | 63 | 351 (17.9%) | 8,247 |
| `admin_audit_logs` | `ip_hash` | 18 | 64 (28.1%) | 140 |
| `security_events` | `ip_address` | 6 | 23 (26.1%) | 591 |
| `analytics_events` | `ip_hash` | 83 | 1,786 (4.6%) | 3,994 |
| `visitor_logs` | `ip_address` | 39 | 5,865 (0.7%) | 6,582 |
| `visitor_sessions` | `ip_hash` | 32 | 4,628 (0.7%) | 103 |

The fallback constant reproduces stored hashes in all six tables. The premise holds, and it holds
for the entire history — there is no second salt era to discover mid-migration.

**The exposure is not theoretical, and it does not need the 2\*\*32 sweep.** The table above was
produced with no brute force at all: the plaintext addresses were already sitting in
`auth_events`, in the same database, and hashing 188 of them deanonymised 19,657 rows across six
tables in under a second. The full sweep only widens it. Measured on this machine, single-core
CPython does 1,435,174 hashes/sec, so the complete IPv4 space is **~50 minutes single-core, ~6
minutes across 8 cores**. On a GPU it is seconds. There are 6,985 distinct hashes platform-wide;
one pass recovers all of them.

Two findings that are not in §6.1 at all and change the shape of the work:

- **`visitor_logs.ip_address` is the largest hash store in the platform — 207,532 rows — and it
  is not named `ip_hash`.** Any inventory that greps for `ip_hash` misses it; the original static
  scan of `CREATE TABLE` bodies found 14 tables and this was not one of them. It only surfaced by
  introspecting production column names for `%ip_address%` as well.
- **`security_events.ip_address` holds two different things in one column.** 3,352 rows are raw
  IPv4 and 674 are sha256. `bot.log_security_event()` (bot.py:29785) writes
  `client_address.client_ip(...)` — the address — and `security_monitor.record()`
  (services/security_monitor.py:23) writes `ip_hash` into the same column. Any sweep that treats
  this column uniformly will either double-hash the hashed rows or leave the raw ones. This must
  be split before it is migrated.

A name-agnostic sweep of all **6,411** other text columns in the database, probing for 300 known
hash values, returned no hits; a second sweep for truncated derivatives (`device:<hex>`,
`ip:<hex>`, produced by `services/sentinel/request_bridge.py:190,198,225`) also returned none —
the Sentinel entity tables are empty. **The inventory below is closed.**

---

## 2. Inventory

### 2.1 Hash stores (all sha256 under the committed fallback)

| table | column | rows | distinct | oldest |
|---|---|---|---|---|
| `visitor_logs` | `ip_address` | 207,532 | 5,865 | — |
| `visitor_sessions` | `ip_hash` | 32,744 | 4,628 | 2026-05-18 |
| `analytics_events` | `ip_hash` | 29,771 (96 empty) | 1,787 | 2026-05-12 |
| `mobile_security_sessions` | `ip_hash` | 10,139 (2 empty) | 352 | 2026-07-01 |
| `admin_audit_logs` | `ip_hash` | 740 | 65 | 2026-05-12 |
| `security_events` | `ip_address` | 674 of 4,026 | 23 | 2026-05-16 |
| `admin_activity_logs` | `ip_hash` | 226 | 55 | 2026-05-20 |
| `admin_session_logs` | `ip_hash` | 77 | 34 | 2026-05-22 |
| `user_security_events` | `ip_hash` | 74 | 24 | 2026-06-09 |
| `pulse_ad_audit_logs` | `ip_hash` | 72 | 1 | 2026-07-29 |
| `profile_audit_logs` | `ip_hash` | 47 | 12 | 2026-06-29 |
| `referral_events` | `ip_hash` | 39 | 15 | 2026-08-14 |
| `verification_audit_logs` | `ip_hash` | 3 | 3 | 2026-06-29 |
| `referral_deferred_claims` | `ip_hash` | 1 | 1 | 2026-09-07 |
| `active_sessions` | `ip_hash` | 0 | — | — |
| `security_login_events` | `ip_hash` | 0 | — | — |

**281,811 rows, 6,985 distinct values.** All columns are `text`, none `varchar(n)` — no width
constraint blocks a longer value.

### 2.2 Plaintext IP stores (a separate, arguably worse finding)

| table | column | rows | distinct |
|---|---|---|---|
| `visitor_logs` | — | — | (hashed, see above) |
| `auth_events` | `ip_address` | 707 non-null of 1,311 | 188 |
| `security_events` | `ip_address` | 3,352 raw of 4,026 | ~50 |

These are real addresses in plaintext, retained since 2026-05-12. They are what made §1 a
one-second exercise. They need a retention decision of their own (§6).

### 2.3 Dead column

`password_reset_tokens.request_ip_hash` — 31 rows, **0 non-null**. Nothing writes it. It should be
dropped rather than migrated.

---

## 3. Consumers: what breaks on a naive salt rotation

This is the question that matters, and the answer is much narrower than feared.

### 3.1 Live decisions that read a stored hash back

Only three, found by grepping for `ip_hash` in a `WHERE`/`GROUP BY` position:

| site | store | window | effect of rotation |
|---|---|---|---|
| `services/admin_gateway.py:161` `login_rate_limited()` | `admin_audit_logs.ip_hash` | **10 min**, 10 failures | admin login lockout counter resets once |
| `bot.py:14989` deferred referral claim | `referral_deferred_claims.ip_hash` + `ua_hash` | **48 h** | installs in flight lose attribution |
| `services/sentinel/identity_detections.py:475,483` `detect_admin_unseen_network()` | `admin_session_logs.ip_hash` | **30 d** history vs 24 h recent | **every admin looks like a new network for 30 days** |

The third is the sharp one and is not mentioned anywhere in §6.1. The rule opens an incident when
an admin appears from a network hash absent from 30 days of history. Rotate the salt without a
backfill and *every* admin session matches that condition for 30 days — a false-positive incident
storm in exactly the surface built to flag real compromise. It is `owner_action=True`, human
review, never auto-lockout, so it cannot lock anyone out; it can bury a true positive in noise.

### 3.2 What does *not* break — including the one the docs warn about

- **The 900-second failed-login lockout is unaffected.** §6.1 names it as the scariest consumer,
  but `failed_login_controls.control_value` for `control_type='ip'` stores `client_ip_address()` —
  the **raw address** (bot.py:6022, 6033) — and the velocity count is
  `auth_events WHERE ip_address=?` (bot.py:6063, 6198), also raw. No hash is involved anywhere in
  that path. A salt rotation cannot touch it.
- **The in-process limiters** (`RATE_LIMIT_BUCKETS`, `_RATE_BUCKETS`, `security_guard.BUCKETS`)
  key on `client_ip_hash()` but hold state in process memory, which a deploy discards regardless.
- **`sentinel_rate_counters`** persists a hashed subject, but it is **0 rows** — the distributed
  limiter is default OFF — and its windows are seconds.
- **`mobile_security_sessions.ip_hash`** is written and updated (bot.py:31621, 31702) but never
  used as a lookup key; sessions are found by id/token. Descriptive only.

All three persistent live-decision stores — `sentinel_rate_counters`, `failed_login_controls`,
`failed_login_safe_list` — are **empty in production right now**. The "must not break mid-flight"
risk is close to zero at this moment, which makes this an unusually cheap time to act.

### 3.3 Historical reporting

Everything else — `analytics_events`, `visitor_logs`, `visitor_sessions`, the six audit tables —
is read for reporting and counted as distinct-visitor cardinality. These fracture silently: a
rotation without a backfill makes one person look like two from the cutover onward, with no error
and no log line.

---

## 4. The premise that changed

The brief asked whether historical rows should be "rehashed, dropped, or accepted as orphaned,
given they cannot be rehashed without the original IPs — which, if genuinely unrecoverable, may
make 'reversible forever' vs 'delete the column' the real choice."

**The original IPs are recoverable, and that dissolves the dilemma.** 6,985 distinct values, a
~6-minute offline sweep, plus 188 addresses already in plaintext next door. The property that
makes this a vulnerability is the same property that makes a faithful rehash possible. A full
backfill is available, so "reversible forever vs delete the column" is a false choice — the third
option, *convert*, is on the table and is strictly better than both.

This has an uncomfortable corollary that should be stated plainly rather than buried: **executing
the backfill means materialising every user's real IP address on whatever machine runs it.** That
is a privacy event, and it is precisely the attack. It is justifiable as a one-time act that ends
the exposure permanently, but it must be run in-process, in memory, with the reverse map never
written to disk and never logged. If that is not acceptable, Option C in §5 is the alternative
and it is not unreasonable.

---

## 5. Options

### Option A — reverse, rehash, backfill *(recommended)*

Recover the 6,985 addresses offline, recompute under a keyed HMAC, rewrite 281,811 rows.

- Correlation preserved everywhere; no analytics fracture, no 30-day incident storm.
- Exposure eliminated for history, not just for new rows.
- No permanent dual-read complexity to carry.
- Costs a controlled privacy event and a backfill window.

### Option B — versioned coexistence, no backfill

Write `v2:` values, dual-read old and new for the three live paths, leave history alone.

- Cheapest, no reversal, no privacy event.
- **Does not fix the finding.** 281,811 historical rows stay trivially reversible forever. Solves
  the correlation problem while leaving the exposure that prompted the work.
- Reporting still fractures at the cutover, just without breaking a live decision.

### Option C — null the historical hash columns

- Fixes the exposure completely and cheaply, no reversal, no privacy event.
- Destroys the audit trail on `admin_audit_logs`, `admin_session_logs`, `user_security_events` and
  the rest — the tables whose entire purpose is retrospective investigation. Those are the ones
  you least want blank if you later need to answer "where did this admin log in from".
- Defensible for `visitor_logs` (207k rows of page-view analytics) in isolation.

**Recommendation: A, with C applied selectively to `visitor_logs`** if the reversal's cost is
judged not worth paying for page-view data. A hybrid is coherent — the audit tables are small
(1,278 rows across all eight) and carry nearly all the investigative value, while `visitor_logs`
is 74% of the row count and the least valuable per row.

---

## 6. Proposed design

### 6.1 The new hash: derived, not configured

The root cause is not a weak constant — it is that **the salt was optional**. `os.getenv` with a
fallback cannot fail loudly, so an unset variable produced a working, wrong system for four
months. Setting `ANALYTICS_SALT` in Railway fixes today's value and leaves the mechanism that
produced it intact; the next environment that forgets it silently re-enters the same state.

`services/signing_keys.py` already solved exactly this, for exactly this reason — "Deriving
rather than adding five required environment variables means the split takes effect on the next
deploy with no operator action, and no possibility of a deploy that half-works because someone set
four of five." Add a sixth purpose:

```python
#: The correlation key behind `client_ip_hash()`. Unlike the other five this is
#: not a credential -- it is the index that groups rows for one address, so
#: rotating it orphans history rather than invalidating a token. Same trap as
#: PASSWORD_RESET, with no expiry to bound it: reset links die in an hour, an
#: `ip_hash` correlates for as long as the row is kept. Rotate only with the
#: backfill in docs/web-rebuild/PULSESOC_IP_HASH_MIGRATION_PLAN.md.
ANALYTICS_IP = "analytics-ip"
```

`client_ip_hash()` becomes `HMAC-SHA256(derive(root, ANALYTICS_IP), ip)`. With a secret key the
2\*\*32 enumeration is worthless — an attacker holding the repo gains nothing, which is the actual
fix. `ANALYTICS_SALT` is retired rather than set, so there is no variable left to forget.

Worth flagging honestly: this makes the ip-hash key a derivative of `COINPILOTX_SECRET_KEY`, so a
root rotation orphans correlation history as a side effect. That risk exists today too, and the
`PULSESOC_ANALYTICS_IP_SECRET` override is the escape hatch — but the rotation-cost table at the
top of `signing_keys.py` must gain a row saying so, or the next person rotating the root will
trip over it.

### 6.2 Versioning: a `v2:` prefix

Store `v2:<64 hex>` rather than a bare digest, in the same column.

The reason is idempotency, which this repo requires absolutely because there is no migration
framework. Without a marker, a resumed or re-run backfill cannot distinguish a
not-yet-converted sha256 from an already-converted HMAC — both are 64 hex characters. It would
happen to be self-correcting (a converted value finds no match in the reverse table and is
skipped), but that is an accident of the data, not a property of the code, and it is exactly the
kind of "correct by accident" this codebase has already been bitten by. With the prefix,
`WHERE col NOT LIKE 'v2:%'` makes re-running the backfill a provable no-op.

A sibling `ip_hash_version` column is the alternative. It avoids the length change, but costs 16
idempotent `ADD COLUMN` guards in `init_db()` — 16 chances to get it wrong — and the version stops
travelling with the value the moment it is copied into a Sentinel subject or an evidence string.
The prefix is self-describing wherever it lands. Recommended.

**Six truncation sites must be adjusted for the 64 → 67 length change** (none is a correctness
bug, all silently change a derived string):

| site | now | with prefix |
|---|---|---|
| `bot.py:18776` | `client_ip_hash()[:16]` in an ads rate-limit key | 16 hex → 13 hex of entropy |
| `services/sentinel/request_bridge.py:190` | `f"device:{ip_hash[:32]}"` | actor id format changes |
| `services/sentinel/request_bridge.py:198` | `entities.make_ref("ip", ip_hash[:64])` | **silently truncates the digest** |
| `services/sentinel/request_bridge.py:225` | `f"ip:{ip_hash[:32]}"` | ref format changes |
| `services/sentinel/identity_detections.py:497` | `str(ip_hash)[:16]` in evidence text | evidence string changes |
| `services/pulsesoc_dashboard_centers.py:437` | `ip_hash[:160]` | harmless |

`request_bridge.py:198` is the one that matters: `[:64]` was written to mean "the whole hash" and
would start dropping three characters. All of these should slice *after* the prefix.

### 6.3 Split `security_events.ip_address` first

This is a prerequisite, not part of the migration. The column has two writers with two meanings.
Proposal: add `ip_hash TEXT` (idempotent), point `security_monitor.record()` at it, leave
`log_security_event()` writing the address to `ip_address`, and move the 674 existing hashed rows
across. Then the column means one thing and can be migrated.

---

## 7. Sequence

Ordering is the whole problem: the backfill and the code deploy cannot be atomic.

1. **Split `security_events`** (§6.3). Independent, shippable alone.
2. **Add `ANALYTICS_IP` to `signing_keys.py`.** No behaviour change yet.
3. **Deploy dual-read + new-write.** `client_ip_hash()` returns `v2:` HMAC. The three live reads
   in §3.1 query the new value *and* the legacy sha256, new first — the two-query pattern
   `load_password_reset_record` already uses (bot.py:6636), for the same stated reason: the hash
   is the index, so a miss orphans rather than rejects. Legacy computation lives behind
   `client_ip_hash_legacy()`, read path only, deletable after step 6.
4. **Backfill**, in-memory reverse map, `WHERE col NOT LIKE 'v2:%'`, batched, resumable. Rows
   written during the backfill are already `v2:` and are skipped by the predicate.
5. **Reconcile.** Assert zero remaining non-`v2:` rows; assert distinct cardinality per table is
   unchanged (a correct rehash is a bijection — if 5,865 distinct becomes 5,864, two addresses
   collided or one failed to reverse and the run is wrong).
6. **Delete the dual-read** after the longest window has drained — 30 days, bounded by
   `ADMIN_UNSEEN_NETWORK_HISTORY_DAYS`, not the 48 h or 10 min.
7. **Drop `password_reset_tokens.request_ip_hash`** (§2.3).

Unreversible values — IPv6, or an address outside the sweep — must be nulled, not left. There are
**zero IPv6 values in production today** (checked: no stored value contains a colon), but
`client_ip()` can return one, so the backfill needs the branch and a count of how often it fires.

## 8. Verification

- **All DDL on throwaway Docker Postgres 18**, per the local-verification workflow. Never against
  production. `open -a Docker` first — the daemon is installed but stopped.
- **Idempotency proved by re-running**: `init_db()` twice, backfill twice, asserting the second
  pass is a no-op. This is the property the `v2:` prefix exists to give.
- **Restore a production dump into the throwaway instance and rehearse the full backfill there**,
  including the reconcile in step 5. The row counts in §2 are the expected output; a rehearsal
  that does not reproduce them has found something this document missed.
- **SQLite-vs-Postgres**: these are all `text` columns, so the usual integer/text binding trap
  does not apply here — but the backfill must be verified on Postgres regardless, since that is
  the only place the data exists.
- Backend work under `source .venv/bin/activate`; system `python3` lacks `psycopg2` and `pytest`.

## 9. Decisions needed before any of this runs

1. **Option A, B, or C** — and if A, whether `visitor_logs` (74% of rows, lowest value) is
   converted or nulled.
2. **Is the reverse-map privacy event acceptable?** §4. If not, A is off the table and the honest
   choice is C.
3. **Retention for the plaintext columns** (§2.2). `auth_events.ip_address` is read only within a
   300-second velocity window, so retaining 4 months of it has no functional purpose. Truncating
   to 24 h costs nothing and removes the shortcut that made §1 instant. This is arguably more
   urgent than the hash migration and is much cheaper.
4. **Prefix vs sibling column** (§6.2).
5. **Whether step 1 ships separately** — it is independent and small.
