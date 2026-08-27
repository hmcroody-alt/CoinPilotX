# UNDX DATABASE AUTHORITY CLEANUP AUDIT

**Mission:** resolve database-stored capability records before the UNDX training corpus is created.
**Scope:** investigation and documentation only. No production behaviour was modified. No rows were
inserted, updated, or deleted. No training data, corpus, YAML, prompts, or examples were created.

**Method:** live queries against `coinpilotx.db` (776 tables) first, code reading second, `git log -S`
third to date the drift. Every count below is reproducible with the commands quoted inline.

**Prerequisite:** this document assumes `11_AUTHORITY_RECONCILIATION.md` and uses its register names
(R1–R8, Surfaces A–E). It corrects one claim from that document — see §7.

---

## 0. THE HEADLINE

**`pulsesoc.send_message` is a row in a write-only ledger. It is not authoritative, not retrieved,
not consulted by runtime authorization, and it cannot be executed.** It is seed data derived from a
descriptive registry, written once on 2026-07-30, and never read by anything since.

That is the narrow answer. The audit found three larger things.

**First: the three `pulse_ai_*` registry tables have no runtime reader. Neither does
`capability_audit_results`.** Four tables holding **12,086 rows** between them are written by
request-path code and read by nothing that serves a request. They are not stale caches — a stale
cache is at least read. They are sediment. `capability_audit_results` alone holds 11,880 rows that
no line of code has ever queried.

> ### ⚠ CORRECTION — this section originally said "no `SELECT` anywhere in the codebase"
>
> An adversarial verification pass found two readers. Both are offline audit scripts:
> `scripts/pulsesoc_undx_bootstrap_v2_migration_audit.py:42-44` and
> `scripts/pulsesoc_undx_bootstrap_v3_audit.py:61-62`. Neither is imported by the application;
> neither runs in any request path; neither is in CI. The **runtime** claim survives unchanged.
> The **absolute** claim does not.
>
> **This matters more than a footnote, because those scripts are a drift detector — and it is
> currently red.** See §3.5.
>
> This is the same class of error §7 corrects doc 11 for making: an absence asserted from a scoped
> search and stated as if unscoped. Third instance I have produced. See §8.

**Second: the seeding is not a one-time event. It runs on every database open.**
`pulse_ai_service._open_db()` calls `ensure_schema()` unconditionally — no memoization flag, no
per-process guard — and `ensure_schema()` calls `undx_architecture.seed_registries()` at line 305.
There are **19 `_open_db()` call sites**. Every one of them replays 12 skill inserts, 103 tool
inserts, and 103 capability inserts. The `INSERT OR IGNORE` makes this harmless to correctness and
invisible in behaviour, which is exactly why it has survived. It is ~218 redundant statements per
connection.

**Third, and this is the finding that changes the remediation plan: the drift is dated, and the
mechanism is provable.** The row timestamps in the local database form three cohorts — 85 rows at
`2026-07-30T02:29:09`, 10 rows at `2026-07-30T04:54:44`, 2 rows at `2026-08-09T06:45:47`. That is a
direct empirical demonstration that `INSERT OR IGNORE` **accretes**: new tool names added to
`PRODUCTION_TOOL_REGISTRY` do land in the table on the next `ensure_schema` run. Six crypto tools
added on 2026-08-23 (commit `aba707a5`) are absent only because `_open_db()` has not run locally
since.

The consequence for remediation: **the drift will self-heal in one direction and never in the other.**
Additions arrive. Removals never delete. Field changes never update. A tool marked `risk: high`
today and downgraded tomorrow keeps `risk_level: 'high'` in the row forever. And
`pulsesoc.send_message` will read `status: 'active'` in perpetuity no matter what the code says,
because nothing will ever write to that row again.

---

## 1. TABLE INVENTORY

776 tables in `coinpilotx.db`. 22 match `capab|tool_|_tool|registry|permission|scope|grant|role`.
Row counts are from the local development database.

```sql
SELECT name FROM sqlite_master WHERE type='table';
```

### 1.1 The four write-only tables

| Table | Rows | Purpose | Written by | Read by | Status |
|---|---:|---|---|---|---|
| `pulse_ai_tool_registry` | 97 | Descriptive mirror of `undx_policy.PRODUCTION_TOOL_REGISTRY` (R7) | `undx_architecture.py:186` | 2 offline audit scripts only | No runtime reader |
| `pulse_ai_capability_registry` | 97 | Parallel mirror, one row per tool name (R8) | `undx_architecture.py:197` | 1 offline audit script only | No runtime reader |
| `pulse_ai_skill_registry` | 12 | Mirror of the in-memory `SKILLS` constant | `undx_architecture.py:174` | 2 offline audit scripts only | No runtime reader |
| `capability_audit_results` | 11,880 | Output of a feature-capability audit script | `bot.py:92523` | **nothing** | Write-only |
| `pulse_ai_delegated_policies` | 0 | Bounded/revocable autonomy grants | `undx_architecture.py:573` `INSERT`, `:584` `UPDATE` | **nothing** | Write-only |

Verification for the first three — the complete set of references in the entire repository
(unscoped search, excluding `.claude/worktrees/`):

```
services/undx_architecture.py:96   CREATE TABLE pulse_ai_skill_registry
services/undx_architecture.py:101  CREATE TABLE pulse_ai_capability_registry
services/undx_architecture.py:105  CREATE TABLE pulse_ai_tool_registry
services/undx_architecture.py:174  INSERT OR IGNORE INTO pulse_ai_skill_registry
services/undx_architecture.py:186  INSERT OR IGNORE INTO pulse_ai_tool_registry
services/undx_architecture.py:197  INSERT OR IGNORE INTO pulse_ai_capability_registry
scripts/pulsesoc_undx_bootstrap_v2_migration_audit.py:42,43,44   _count(...)      [offline]
scripts/pulsesoc_undx_bootstrap_v3_audit.py:61,62,118            SELECT COUNT(*)  [offline]
```

Six application lines — three `CREATE`, three `INSERT` — plus six lines in two standalone audit
scripts. **No `UPDATE` and no `DELETE` exist for any of the three tables in any file.** Nothing in
`services/`, `bot.py`, or any blueprint reads them.

**`pulse_ai_delegated_policies` is a fifth write-only table**, found during the adversarial pass.
`create_delegated_policy()` (`:567`) inserts and `revoke_delegated_policy()` (`:584`) updates; no
`SELECT` exists anywhere, and the only caller of either function is
`pulsesoc_undx_bootstrap_v3_audit.py`. A grant can be created and revoked, and no code path can
consult it to decide anything. Note that this function filters against the **in-memory**
`PRODUCTION_TOOL_REGISTRY`, not the database mirror — `allowed = [n for n in allowed_actions if n in
undx_policy.PRODUCTION_TOOL_REGISTRY and n not in HIGH_IMPACT_TOOLS]` (`:568`) — so it is not a
counterexample to the "no runtime reader" claim about the tables.

For `capability_audit_results`, four references: a `CREATE TABLE` at `bot.py:109469`, two
`CREATE INDEX` at `bot.py:109528-109529`, one `INSERT INTO` at `bot.py:92523`, plus a primary-key
declaration in `services/db.py:362`. **The two indexes are built on a table nothing queries.**

### 1.2 The tables that are genuinely read

| Table | Rows | Purpose | Runtime consumer | Gate or report? |
|---|---:|---|---|---|
| `pulse_ai_tool_operations` | 44 | Execution ledger — the real one | `undx_tool_gateway.py` (`SELECT`), `undx_architecture.py` (`INSERT`/`UPDATE`) | Both — idempotency lookup and audit |
| `business_os_undx_permissions` | 0 | Per-org actor grants (part of R6's surface) | `engine.py:615` `_permission_rows` → `_resolve_permission` | **Authorization gate** |
| `business_os_undx_policies` | 0 | Per-org action policies | `engine.py:594` `_active_policies` → `_resolve` | **Authorization gate** |
| `business_os_undx_emergency_stops` | 0 | Kill switches | `engine.py:602` `_active_stops` | **Authorization gate** |
| `business_os_undx_tool_registry` | 0 | Business OS tool catalogue (R6) | `engine.py:238` upsert, `engine.py:819` `list_tools` | **Report only** |
| `backend_feature_registry` | 151 | Admin backend feature catalogue | `services/backend_management_registry.py`, four `SELECT`s in `bot.py` | Report |
| `pulse_ai_feature_registry` | 62 | Feature self-knowledge for the assistant | `services/pulse_ai_service.py:1831` | Retrieval |
| `permissions` / `role_permissions` | 40 / 139 | Web RBAC | RBAC middleware | Gate |
| `admin_permissions` / `admin_role_permissions` | 40 / 131 | Admin RBAC | Admin middleware | Gate |
| `roles` / `admin_roles` | 25 / 25 | Role definitions | RBAC | Gate |
| `pulse_group_roles` | 407 | Per-group membership roles | Group features | Gate |
| `pulse_ai_conversation_context_permissions` | 7 | Per-user AI consent flags | Pulse AI context assembly | Gate |
| `admin_user_roles` | 0 | Admin role assignment | Admin middleware | Gate |
| `business_os_confirmation_grants` | 0 | Confirmation tokens | `engine.py` redeem/revoke | Gate |
| `business_os_ent_grants` | 0 | Entitlement grants | Entitlements service | Gate |
| `dashboard_permissions` | 0 | Dashboard module access | Dashboard | Gate |
| `sentinel_provider_capabilities` | 0 | Provider capability probes | Sentinel | Report |

**Note on the empty Business OS governance tables.** All four are zero locally, and the fail-safe
holds: `_resolve()` returns `("require_approval", None, "no matching policy — default
require_approval")` when the policy pool is empty, and `_resolve_permission()` returns `None` when no
grant row matches. Empty means deny-pending-approval, not allow. This is correct behaviour, and it
is worth recording because the naive reading — "the authorization table is empty, therefore
authorization is bypassed" — is wrong here.

**Note on `business_os_undx_tool_registry` (R6).** Doc 11 listed it as a registry. It is, but
`list_tools()` at `engine.py:811` is a catalogue reader with no execution role — nothing consults it
before running a tool. Registration at `engine.py:214` is an admin-invoked upsert, not a seeding
routine. Zero rows means the catalogue is empty, not that the gate is open, because it was never a
gate.

---

## 2. THE `pulsesoc.send_message` ROWS

Three rows in the entire database mention it. All three are verbatim below.

### 2.1 `pulse_ai_tool_registry` id=5

```json
{
  "id": 5,
  "tool_name": "pulsesoc.send_message",
  "version": "1.0",
  "owner_service": "existing_pulsesoc_backend",
  "purpose": "pulsesoc.send_message",
  "method": "POST",
  "route": "/api/pulse/comm/v2/conversations/<conversation_ref>/messages",
  "input_schema_json": "{}",
  "output_schema_json": "{}",
  "authorization_policy": "authenticated_server_per_call",
  "risk_level": "high",
  "idempotency_policy": "required_for_writes",
  "audit_policy": "correlation_id_redacted",
  "confirmation_required": 1,
  "status": "active",
  "updated_at": "2026-07-30T02:29:09+00:00"
}
```

Note `purpose` is the tool name repeated. That is `seed_registries` line 187:
`clean(item.get("mode") or name, 240)` — `PRODUCTION_TOOL_REGISTRY` entries have no `mode` key, so
the fallback fires for all 97 rows. `input_schema_json` and `output_schema_json` are the literal
`'{}'` hardcoded in the `VALUES` clause. **This row carries no information that is not already in
`undx_policy.py:46`, and it carries two fields that are actively empty.**

### 2.2 `pulse_ai_capability_registry` id=5

```json
{
  "id": 5,
  "capability_name": "pulsesoc.send_message",
  "current_status": "available",
  "required_tool": "pulsesoc.send_message",
  "permission_scope": "server_authorized",
  "confidence": 0.95,
  "last_verified_at": "2026-07-30T02:29:09+00:00",
  "degradation_mode": "explain_limitation",
  "metadata_json": "{\"route\": \"...\", \"risk\": \"high\"}"
}
```

Four of these fields are false as stated.

- **`current_status: 'available'`** — it is not. No capability in R1 maps to this tool
  (verified live: `[k for k,v in REGISTRY.items() if v.tool_name == "pulsesoc.send_message"]` → `[]`).
  It is one of the 16 R2 orphans. `require()` cannot reach it.
- **`required_tool` equals `capability_name`** — the seeder passes `name` twice. There is no
  capability/tool distinction encoded here at all; the join is the identity function.
- **`permission_scope: 'server_authorized'`** — this string does not exist in
  `PermissionScope`. The real vocabulary is `SELF_ACCOUNT_ONLY`, `OTHER_USER_TARGET`,
  `OWNED_CONTENT_TARGET`, `PUBLIC_READ`. `'server_authorized'` is a hardcoded literal in the seeder's
  `VALUES` clause, identical for all 97 rows. **It is not a scope. It is a constant.**
- **`confidence: 0.95`** — also a hardcoded literal, identical for all 97 rows. It is not a
  measurement of anything.

`last_verified_at` is the seed timestamp. Nothing has ever verified this row.

### 2.3 `pulse_ai_skill_registry` id=5

```json
{
  "id": 5,
  "skill_id": "messenger.send",
  "purpose": "Send after confirmation and verify canonical message ID.",
  "risk_level": "high",
  "permissions_json": "[\"message.send\"]",
  "tools_json": "[\"pulsesoc.send_message\"]",
  "verification": "read_after_write",
  "status": "active"
}
```

`"message.send"` is a permission string that appears in no permission table, no `PermissionScope`
enum, and no RBAC row. It exists in the `SKILLS` constant and its database mirror and nowhere else.
Five of the twelve skill rows reference R2 orphans this way:

| Skill | Tool referenced | Reachable? |
|---|---|---|
| `messenger.draft` | `pulsesoc.draft_message` | Orphan |
| `messenger.send` | `pulsesoc.send_message` | Orphan |
| `creator.publish_post` | `pulsesoc.create_post` | Orphan |
| `creator.publish_reel` | `pulsesoc.create_reel` | Orphan |
| `product.open_profile` | `pulsesoc.get_profile` | Orphan |
| `saved.library.list` | `pulsesoc.saved_items.list` | **Reachable** |
| `social.relationships.list` | `pulsesoc.relationships.list` | **Reachable** |

### 2.4 The route exists — the path to it does not

`PRODUCTION_TOOL_REGISTRY` names the route `POST /api/pulse/comm/v2/conversations/<conversation_ref>/messages`.
That route is real: `pulse_communications_v2/routes.py:1002`. The tool cannot reach it, but the
endpoint is live and serves the ordinary messaging product. **The registry row is not pointing at
nothing; it is pointing at a real send-message endpoint that the UNDX capability layer has no
authorized path to.** That distinction matters for risk: the danger is not a dangling reference, it
is a plausible-looking one.

### 2.5 The six questions, answered

**1. Is this row authoritative?** **No.** Authority for what UNDX may execute is
`undx_capability_registry.REGISTRY` (87 entries, R1), enforced by `require(capability_id)` in
`undx_tool_gateway.py:698`. The row is a copy of R2, which doc 11 established is a ledger and a
prompt/simulator allowlist, not a permission source.

**2. Is this row used by retrieval?** **No.** No `SELECT` touches the table. The skill-selection
path that *looks* like retrieval — `undx_architecture.select_skills()` at `:205` — iterates the
in-memory `SKILLS` constant, not `pulse_ai_skill_registry`. The database mirror is not consulted even
by the function whose data it mirrors.

**3. Is this row used by runtime authorization?** **No.** The authorization chain is
`require()` → `validate_arguments()` → `_enforce_permission_scope()` → `policy.evaluate()`
(`undx_tool_gateway.py:698-710`). None of those four reads a database registry table. The only
DB read in that chain is `is_following()` inside `_enforce_permission_scope` at `:447`, which is a
social-graph lookup, not a capability lookup.

**4. Is this row stale seed data?** **Yes, but not in the way the brief's phrasing implies.** It is
not stale-because-forgotten — the seeder runs constantly. It is stale because `INSERT OR IGNORE`
cannot update. The row was accurate on 2026-07-30 relative to R2 and is *still* accurate relative to
R2. What it was never accurate about is R1, because it never described R1. **The row is a faithful
copy of the wrong source.**

**5. Should status change?** The literal answer is that `status: 'active'` and
`current_status: 'available'` are both wrong and should be `orphaned` / `unreachable`. The better
answer is that **the status column should not exist, because the table should not exist.** Changing
a field in a table nothing reads produces no behavioural improvement and creates a false impression
of remediation. See §4 R1.

**6. What migration/remediation is required?** See §4. Summary: there is no migration framework —
`migrations/` holds hand-applied `.sql` files, no Alembic, and schema lives imperatively in
`bot.init_db()` and per-service `ensure_schema()` functions. Any remediation must be idempotent and
must survive being re-run on every request. The recommended action is to stop seeding and drop, not
to correct the rows.

---

## 3. THE DRIFT MECHANISM, DATED

This is the part of the audit that produced a result I did not expect and that reverses the natural
assumption.

### 3.1 What the timestamps prove

```sql
SELECT updated_at, COUNT(*) FROM pulse_ai_tool_registry GROUP BY 1;
-- 2026-07-30T02:29:09+00:00 | 85
-- 2026-07-30T04:54:44+00:00 | 10
-- 2026-08-09T06:45:47+00:00 |  2
```

Identical cohorts in `pulse_ai_capability_registry`. `pulse_ai_skill_registry` is a single cohort of
12 at `2026-07-30T02:29:09` — the `SKILLS` constant has not changed since.

Three distinct write events, growing 85 → 95 → 97. The obvious reading of a stale table is "seeded
once, abandoned." **That reading is wrong.** The table has been added to twice since its creation.

### 3.2 The timeline

| Date | Event | Source |
|---|---|---|
| 2026-07-19 | `pulsesoc.send_message` added to `PRODUCTION_TOOL_REGISTRY` | commit `9eca4bca` |
| 2026-07-30 02:29:09 | First seed — 85 tools, 85 capabilities, 12 skills | row timestamps |
| 2026-07-30 02:29:10 | First entry in `pulse_ai_tool_operations` | ledger `MIN(created_at)` |
| 2026-07-30 04:54:44 | Second seed — +10 rows | row timestamps |
| 2026-07-31 04:30:10 | **Last entry in `pulse_ai_tool_operations`** | ledger `MAX(created_at)` |
| 2026-08-09 06:45:47 | Third seed — +2 rows | row timestamps |
| 2026-08-23 | Six crypto tools added to R2 | commit `aba707a5` |
| *(pending)* | Next `_open_db()` call → table becomes 103 | inevitable |

`PTR − DB` is exactly those six crypto tools. `DB − PTR` is empty. The database is a **strict
subset**, lagging by one commit's worth of additions.

### 3.3 Why it will repopulate

```python
def _open_db():                          # services/pulse_ai_service.py:96
    bot = _bot()
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    ensure_schema(cur, conn)             # :101 — unconditional
    return conn, cur
```

`ensure_schema` at `:105` has no guard — no `_SCHEMA_READY` flag, no module-level memo, no
`@lru_cache`. It runs its full CREATE sequence and then:

```python
        undx_architecture.ensure_schema(cur)    # :305
        _seed_foundation(cur)                   # :306
```

`undx_architecture.ensure_schema` ends by calling `seed_registries(cur)` at `:167`. There are **19
`_open_db()` call sites** in `pulse_ai_service.py`. Every Pulse AI request that opens a connection
replays 218 `INSERT OR IGNORE` statements.

**Therefore:** the first Pulse AI request after the next deployment will add the six crypto tools and
the table will read 103. `pulsesoc.send_message` will still say `status: 'active'`, because
`INSERT OR IGNORE` does not touch rows that already exist. **The table converges upward toward R2 and
never corrects downward.** Any future removal of a tool from R2, any risk downgrade, any route
change, any confirmation-requirement change, leaves a permanently wrong row behind.

### 3.4 The ledger corroborates

`pulse_ai_tool_operations` — the table that records what actually ran — holds 44 rows across 25
distinct tool names, all between 2026-07-30 and 2026-07-31.

```
pulsesoc.crypto_alerts.pause          verified  8
pulsesoc.activity.daily_summary       verified  6
pulsesoc.crypto_alerts.resume         verified  3
pulsesoc.account.health.summary       verified  2   (+1 failed_verification)
... 21 more, all count 1-2
```

**Zero rows mention `send_message`.** Zero rows mention any of the 16 orphans. The only non-read
operations ever executed are `crypto_alerts.pause` and `crypto_alerts.resume` — 11 of 44. Every
other logged operation is a read.

Two things follow. The empirical record confirms the authority model: nothing has ever executed an
orphan. And the ledger stopped on 2026-07-31 while seeding continued through 2026-08-09 —
**the write-only registries kept growing for ten days after the execution ledger went quiet.** The
seeding is decoupled from use.

### 3.5 A drift detector already exists, and it is red

The adversarial pass found that two scripts assert exactly the invariant this document spent §3
establishing is broken.

```python
# scripts/pulsesoc_undx_bootstrap_v2_migration_audit.py
"versioned_skill_registry": _count(cur, "pulse_ai_skill_registry") >= 8,                          # :42
"typed_tool_registry":      _count(cur, "pulse_ai_tool_registry") == len(PRODUCTION_TOOL_REGISTRY),   # :43
"capability_self_model":    _count(cur, "pulse_ai_capability_registry") == len(PRODUCTION_TOOL_REGISTRY),  # :44

# scripts/pulsesoc_undx_bootstrap_v3_audit.py
checks["versioned_skills"]     = ...COUNT(*) FROM pulse_ai_skill_registry WHERE status='active' >= 10   # :61
checks["typed_existing_tools"] = ...COUNT(*) FROM pulse_ai_tool_registry
                                    WHERE owner_service='existing_pulsesoc_backend' == len(PTR)          # :62
```

Evaluated live against the current database:

| Check | Script | Result | Actual |
|---|---|---|---|
| `typed_tool_registry` | v2 `:43` | **FAIL** | 97 ≠ 103 |
| `capability_self_model` | v2 `:44` | **FAIL** | 97 ≠ 103 |
| `typed_existing_tools` | v3 `:62` | **FAIL** | 97 ≠ 103 |
| `versioned_skill_registry` | v2 `:42` | pass | 12 ≥ 8 |
| `versioned_skills` | v3 `:61` | pass | 12 ≥ 10 |

**Three checks are failing right now.** Someone anticipated precisely this drift and wrote an
equality assertion against it. The assertion works. Nobody runs it — neither script is imported by
the application, invoked by `scripts/protection/run_protection_suite.py`, or referenced in
`.github/workflows/protection.yml`.

This changes one judgement in §4. The recommendation to drop the tables (R1) is unaffected — a
detector for a table that should not exist is not a reason to keep the table. But it means R1 must
also delete these five checks, or the audit scripts will fail on a missing table instead of a wrong
count, which is a worse failure mode than the one being fixed.

It also sharpens the §0 characterization. These tables are not merely unread. They were built with a
consistency invariant, that invariant was encoded as an executable test, the invariant then broke,
and the test that would have caught it was never wired to anything that runs. **The failure is not
that nobody thought about drift. It is that the thinking was never connected to execution** — which
is structurally the same failure as the registries themselves: correct-looking machinery with no
consumer.

---

## 4. RECOMMENDED ACTIONS

Ordered by risk-reduction per unit of change. None of these have been performed.

### R1 — Stop seeding; drop the three `pulse_ai_*` registry tables · **Risk: LOW · Priority: HIGH**

Delete `seed_registries()` (`undx_architecture.py:170-202`) and its call at `:167`, and the three
`CREATE TABLE` statements at `:96/:101/:105`. Add an idempotent
`DROP TABLE IF EXISTS pulse_ai_tool_registry` / `pulse_ai_capability_registry` /
`pulse_ai_skill_registry`.

*Why this is low-risk:* no runtime path reads them. The six application lines in §1.1 are the
complete blast radius for request-serving code. Removal is behaviourally invisible.

*Required companion change:* the five audit-script checks listed in §3.5 must be deleted in the same
commit (`pulsesoc_undx_bootstrap_v2_migration_audit.py:42-44`,
`pulsesoc_undx_bootstrap_v3_audit.py:61-62`), and `pulse_ai_skill_registry`,
`pulse_ai_capability_registry`, and `pulse_ai_delegated_policies` must be removed from the
`required_tables` set at `pulsesoc_undx_bootstrap_v3_audit.py:118`. Otherwise the scripts trade a
failing count assertion for a `no such table` exception.

*Why this is high-priority:* these tables are the single most dangerous input to a training corpus.
They are structured, they look canonical, they use words like `capability`, `authorization_policy`,
`permission_scope`, and `status: active`, and **every one of those words is wrong**. A corpus builder
scraping the schema would learn that UNDX has 97 available capabilities including sending messages,
creating posts, and creating reels with `confidence: 0.95`. The true figure is 87 capabilities,
none of which are those three.

*Do not instead "correct the rows."* Correcting `status` to `orphaned` leaves a write-only table
that still asserts `permission_scope: 'server_authorized'` for 97 rows, still hardcodes
`confidence: 0.95`, still has empty schema columns, and will still be re-seeded with wrong values for
every future tool. It converts an obvious fossil into a plausible one.

*Idempotency:* `DROP TABLE IF EXISTS` is idempotent and safe to run per-request during the
transition window.

### R2 — Add a memoization guard to `pulse_ai_service.ensure_schema()` · **Risk: LOW · Priority: MEDIUM**

Independent of R1. A module-level `_SCHEMA_READY` flag set after the first successful run eliminates
~218 statements per connection across 19 call sites. Purely a performance change; no behaviour
depends on re-running.

*Caveat worth stating:* if any deployment currently depends on `_open_db()` to create tables after a
fresh database is provisioned, the guard must be per-process, not per-import, and must reset if the
connection fails. Given Railway's process model this is safe, but it is the one place this change
could bite.

### R3 — Decide the fate of `capability_audit_results` · **Risk: LOW · Priority: MEDIUM**

11,880 rows, two indexes, one `INSERT` at `bot.py:92523`, no reader. Either wire the audit output to
a reporting surface or stop writing it. It is currently the largest write-only object in the system
and it is indexed, so it costs write amplification for no read.

*This one needs a human decision I cannot make from the code:* the rows may be intended as a
historical record consumed out-of-band (a BI tool, a manual query, an export). If so, it is not a
defect — it is an undocumented external contract. **Do not drop this table on my recommendation
alone.** Confirm no out-of-band consumer exists first.

### R4 — Resolve the 16 R2 orphans at the source · **Risk: MEDIUM · Priority: HIGH**

The database rows are a symptom. `PRODUCTION_TOOL_REGISTRY` describes 16 tools no capability can
reach, and doc 11 §3 established those 16 are addressable by name through
`POST /api/pulse-ai/tools/simulate` (`pulse_communications_v2/routes.py:796`), which is login-gated
only. Either add capabilities for the ones that should exist, or remove the entries for the ones that
should not. Leaving them descriptive-but-unreachable is what produced every downstream artifact in
this document.

*Risk is MEDIUM not LOW* because the simulate route consumes R2 directly, so removing entries changes
that surface's behaviour. That is a production behaviour change and is out of scope for this mission.

### R5 — Record `"message.send"` and `'server_authorized'` as non-vocabulary · **Risk: LOW · Priority: HIGH (corpus-blocking)**

Two strings in this data are shaped like permission identifiers and are not. `"message.send"`
(`pulse_ai_skill_registry` id=5) and `'server_authorized'` (all 97 capability rows) belong to no enum
and gate nothing. Whatever survives R1, these two strings must be excluded from any corpus. They are
the most likely candidates to be learned as real permission names.

### R6 — Leave the Business OS governance tables alone · **Risk: n/a · Priority: n/a**

`business_os_undx_permissions`, `_policies`, `_emergency_stops`, `_tool_registry`,
`_confirmation_grants`, `_ent_grants`, `dashboard_permissions`, `admin_user_roles`,
`sentinel_provider_capabilities` are all zero rows. All are genuine runtime consumers with correct
fail-safe behaviour on empty. **Empty is the correct state for an unconfigured org.** No action.

---

## 5. RISK REGISTER

| # | Finding | Risk | Rationale |
|---|---|---|---|
| 1 | Three `pulse_ai_*` registry tables assert 97 available capabilities with authoritative-looking vocabulary; nothing reads them | **HIGH (corpus)** / LOW (runtime) | Zero runtime impact. Maximum corpus-poisoning impact. |
| 2 | `permission_scope: 'server_authorized'` — hardcoded literal, not a `PermissionScope` member, on all 97 rows | **HIGH (corpus)** | Would be learned as a real scope. |
| 3 | `confidence: 0.95` on all 97 rows — hardcoded, measures nothing | **MEDIUM (corpus)** | Implies calibration that does not exist. |
| 4 | `"message.send"` permission string exists in no permission system | **MEDIUM (corpus)** | Plausible-looking phantom identifier. |
| 5 | `seed_registries` runs on every `_open_db()`, 19 call sites, ~218 statements | **LOW** | Performance only; correctness preserved by `INSERT OR IGNORE`. |
| 6 | Registry converges upward to R2, never corrects downward | **MEDIUM** | Any future removal/downgrade leaves a permanently wrong row. Grows worse with time. |
| 7 | `capability_audit_results` — 11,880 rows, 2 indexes, never read | **LOW** | Storage and write amplification. Possible undocumented external consumer. |
| 8 | 5 of 12 skill rows reference unreachable orphan tools | **MEDIUM (corpus)** | Describes workflows the system cannot perform. |
| 9 | `input_schema_json` / `output_schema_json` are literal `'{}'` for all 97 rows | **LOW** | Columns that promise schemas and hold none. |
| 10 | `purpose` column duplicates `tool_name` for all 97 rows | **LOW** | Fallback path in seeder; `mode` key never present in R2. |
| 11 | Route in the `send_message` row is real and live (`routes.py:1002`) | **LOW** | Not a dangling reference — which makes the row *more* plausible, not less. |
| 12 | Execution ledger silent since 2026-07-31 while seeding continued to 2026-08-09 | **INFO** | Confirms seeding is decoupled from use. |
| 13 | A drift detector exists (5 checks, 2 scripts) and 3 checks are currently failing; neither script is in CI or the protection suite | **MEDIUM** | The failure signal exists and is unobserved. Any remediation must delete these checks or they break louder. |
| 14 | `pulse_ai_delegated_policies` — fifth write-only table; grants can be created and revoked but never consulted | **MEDIUM** | A bounded-autonomy mechanism that cannot bind anything. Reads as a working safety control. |
| 15 | `pulse_ai_missions`, `pulse_ai_task_nodes`, `pulse_ai_truth_facts`, `pulse_ai_knowledge_edges` all 0 rows despite being in the v3 `required_tables` set | **LOW** | Schema exists, feature never exercised. `pulse_ai_memory_provenance` has 11 rows. |

---

## 6. CORPUS BLOCKERS

Extending doc 11's B1–B6.

**B7 — Do not ingest `pulse_ai_tool_registry`, `pulse_ai_capability_registry`, or
`pulse_ai_skill_registry`, and do not ingest their schema.** They are the highest-fidelity wrong
answer available in this system. If R1 is executed the problem disappears; if it is not, these three
tables must be on an explicit exclusion list.

**B8 — Do not ingest `capability_audit_results`.** 11,880 rows of feature-status assertions with no
reader and therefore no correctness pressure. Nothing has ever depended on them being right.

**B9 — The string `'server_authorized'` must not appear in the corpus as a permission scope.** The
four real members of `PermissionScope` are `SELF_ACCOUNT_ONLY`, `OTHER_USER_TARGET`,
`OWNED_CONTENT_TARGET`, `PUBLIC_READ`.

**B10 — The string `"message.send"` must not appear as a permission identifier.**

**B11 — `status: 'active'` and `current_status: 'available'` in these tables carry no truth value.**
They are constants in a `VALUES` clause. 97 of 97 rows say `active`; 97 of 97 say `available`. A
field with no variance encodes no information.

**B12 — Any statement of the form "UNDX can send messages / create posts / create reels" must be
treated as false** regardless of how many registry rows, skill definitions, or schema columns
support it. Verified twice now: no capability in R1 maps to those tools, and the execution ledger
contains zero such operations across its entire 44-row history.

**B13 — `pulse_ai_delegated_policies` must not be described as a working autonomy control.** The
`create_delegated_policy` / `revoke_delegated_policy` pair is real code with correct-looking filter
logic (`:568` correctly excludes `HIGH_IMPACT_TOOLS`, which correctly includes
`pulsesoc.send_message`), and nothing ever reads the resulting row. A corpus that learns "UNDX
supports bounded, revocable delegated autonomy" would be describing a mechanism with no consumer.

---

## 7. CORRECTION TO DOC 11

`11_AUTHORITY_RECONCILIATION.md` §1, entries R7/R8, states that the `pulse_ai_tool_registry` and
`pulse_ai_capability_registry` tables are *"Read by admin and self-knowledge surfaces."*

**That is false. No admin surface and no self-knowledge surface reads either table.** The only
readers in the repository are two standalone audit scripts that count rows (§3.5) — not admin
routes, not the assistant's self-knowledge path, and nothing that serves a request.

The complete reference set is in §1.1: six application lines in `services/undx_architecture.py`
(three `CREATE`, three `INSERT`) plus six lines across two offline scripts.

The likely origin of the error: `pulse_ai_feature_registry` (62 rows) *is* read by a self-knowledge
surface, at `services/pulse_ai_service.py:1831`, and `backend_feature_registry` (151 rows) *is* read
by admin surfaces in `bot.py`. Both have similar names and adjacent purposes. I attributed their
consumers to the wrong tables.

**This is the sixth instance of absence-claim decay in this codebase's documentation, and the second
one I have produced myself.** The pattern is consistent: a claim about what reads or reaches
something is asserted from plausibility rather than from a completed search, and then survives
because nobody re-runs the search. In this case the claim was upgraded in the wrong direction — from
"I did not check" to "it is read."

**The instructive part is that my correction to it was also wrong, in the mirror direction.** Doc 11
over-claimed presence; my first draft of §1.1 over-claimed absence ("no `SELECT` anywhere in the
codebase"), from a search scoped to `services/ bot.py *.py` that excluded `scripts/`. Both errors
have the same shape — a scoped observation stated at unscoped confidence. The corrected claim is
narrower than either and is the one that actually answers the brief: **no runtime consumer.**

Doc 11 §1 should be amended. I have not edited it; that is a separate action, flagged here.

---

## 8. METHOD NOTE

The `Grep` tool returns no matches for Python files in this repository. Every search in this document
used `bash grep -rn --include=*.py`. Any future audit that relies on `Grep` will produce false
absence claims — which is plausibly one contributing mechanism behind the decay pattern in §7.

Broad `grep -rn` across the repository root times out at 45s. First-pass searches were scoped to
`services/ bot.py *.py`, which excludes `scripts/`, `pulse_communications_v2/`, `tests/`, and
`mobile-native/`. **That scoping produced the §1.1 error.** The claims in §1.1 have since been
re-verified with an unscoped search (excluding only `.claude/worktrees/` and `node_modules`).

`.claude/worktrees/eloquent-herschel-64cb81/` contains a full duplicate checkout. It is excluded
from all searches and all counts. Every duplicated hit observed during this audit was a byte-identical
copy, but this exclusion is a standing source of false absence for any future search that forgets it.

Live introspection was preferred to reading wherever a module could be imported. The orphan set, the
`PTR − DB` difference, the R1 capability count in §2.5, and the drift-detector verdicts in §3.5 are
all computed values, not transcribed ones.

### Adversarial pass — run

Per the §10 policy established in doc 11. Two claims were attacked.

**Claim 1: "no `SELECT` anywhere in the codebase" (§1.1). FALSIFIED.** An unscoped search found six
lines across two audit scripts. The correction is recorded inline in §0 and §1.1, and the finding it
had been concealing — a red drift detector — became §3.5, which is now among the more consequential
sections of this document. A fifth write-only table (`pulse_ai_delegated_policies`) surfaced from
the same search. **The error was worth more than the claim would have been.**

**Claim 2: "the table will become 103 on the next deploy" (§3.3). NOT FALSIFIED, but it remains an
inference.** Verified: `_open_db()` calls `ensure_schema()` unconditionally (`:96-101`),
`ensure_schema` has no guard (no `_SCHEMA_READY`, no `lru_cache`, no early return), it calls
`undx_architecture.ensure_schema(cur)` at `:305`, and that calls `seed_registries(cur)` at `:167`.
The three timestamp cohorts (85/95/97) demonstrate the mechanism has in fact fired twice. What was
*not* done — because it would be a database write, which the brief forbids — is executing
`_open_db()` and observing the count change. **The prediction is structurally sound and empirically
supported but not directly observed.** It is the strongest remaining candidate for a future
correction.

**Not attacked, and therefore weaker than the rest of this document:** the §1.2 consumer
classifications for the RBAC tables (`permissions`, `role_permissions`, `admin_permissions`,
`admin_role_permissions`, `roles`, `pulse_group_roles`). Their purposes were inferred from names and
schema, not traced to middleware. They are outside the UNDX authority question and were not the
brief's focus, but the table in §1.2 states them with more confidence than the evidence behind them
supports.

---

**Mission constraints honoured:** no production changes, no training corpus, no YAML, no database
writes. Investigation and documentation only.
