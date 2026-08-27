# UNDX AUTHORITY RECONCILIATION

**Mission:** resolve the conflicting UNDX capability registries before any training corpus is written.
**Scope:** investigation and documentation only. No production behaviour was modified. No training data,
capability corpus, prompts, or examples were created.

**Method:** live introspection first (importing the modules, querying `coinpilotx.db`, counting real
registry objects), code reading second, grep last. Every count below is reproducible.

---

## 0. THE HEADLINE

The recon report (`11_UNKNOWN_AREAS.md` §1.1) recorded a blocking conflict: two tool registries,
87 capabilities versus 103, disagreeing about whether UNDX may send messages, create posts, and
create reels.

**That conflict is resolved. It was never a conflict between two authorities — it is a conflict
between an authority and a ledger.** The 87-entry capability registry decides what UNDX may do.
The 103-entry production tool registry records and describes. They are layers, not rivals.

**But the investigation found something larger.** There are not two registries. There are **eight**,
across **five independent execution surfaces**, and only one of those surfaces is governed by the
87-capability registry at all. The other surfaces — the Business OS advertising and marketplace
assistants, the Business OS UNDX governance engine, and the UNDX execution kernel — carry their own
tool tables, their own confirmation systems, their own risk vocabularies, and their own databases of
grants. They can pay orders, publish products, set advertising budgets, settle escrow, write files
to the repository, and `git push origin main`. None of that passes through
`undx_capability_registry.require()`.

The previous recon looked at the AI-chat surface and concluded UNDX cannot spend money. That is
true *of that surface*. It is not true of the system.

> ### ⚠ CORRECTION — this section originally said "six registries, four surfaces"
>
> The first draft of this document counted six registries across four surfaces. An adversarial
> verification pass found two more of each. The missed items were:
>
> - **R7/R8** — `pulse_ai_tool_registry` and `pulse_ai_capability_registry`, two live database
>   tables holding **97 rows each**, seeded by `undx_architecture.py:174/186/197` with
>   `INSERT OR IGNORE` (so never updated after first write). Both contain `pulsesoc.send_message`,
>   `pulsesoc.create_post`, `pulsesoc.create_reel`. See §1 R7/R8.
> - **Surface E** — `/api/undx/kernel/{scan,propose,apply,validate,git}` (`bot.py:28930-29025`),
>   which writes files to the repository and runs `git push`, plus a sixth route on the same gate,
>   `/api/undx/desktop-connector/<path>` (`bot.py:28893`), which proxies `patch/apply` and
>   `git/push` to a process outside this repository. See §2 Surface E.
> - **`/api/pulse-ai/tools/simulate`**, which dispatches on a caller-supplied `tool_name` against
>   R2 directly, making all 16 "orphans" reachable **by name** behind login-only auth.
>   See §2 Surface B″.
>
> The error was the same one §10 claims to have transcended: an absence claim ("there are no other
> registries") stated with more confidence than the search supported. It is preserved here rather
> than overwritten, because the recurrence is the finding.
>
> **A second adversarial pass then found three further defects in the corrections themselves**, plus
> two unprompted findings. Read §10 before trusting anything in this document; the error rate on
> corrections was comparable to the error rate on the original text.

---

## 1. ALL TOOL REGISTRIES

**Eight registries.** Six are Python objects or database tables consulted at runtime; two (R7/R8)
are database tables consulted only by humans and introspection views. Reproduce the live counts
with:

```
python3 -c "
import sys; sys.path.insert(0,'.')
from services import undx_capability_registry as R, undx_policy as P
print(len(R.REGISTRY), len(P.PRODUCTION_TOOL_REGISTRY))"
# -> 87 103
```

### R1 — Capability registry (THE PERMISSION AUTHORITY)

| Field | Value |
|---|---|
| **Location** | `services/undx_capability_registry.py` |
| **Runtime location** | `REGISTRY: dict[str, CapabilitySpec]`, module-level, built at import |
| **Number of capabilities** | **87** (70 read-only, 13 reversible writes, 4 consequential writes) |
| **Purpose** | Declares every action UNDX may propose or execute, with risk, confirmation policy, permission scope, executor, verifier, undo pair, and a typed field schema |
| **Used by** | `undx_tool_gateway.execute()` via `require(capability_id)`; `undx_agent_policy.evaluate()` (type-bound — it imports `CapabilitySpec`); `undx_agent_runtime` |
| **Authority level** | **AUTHORITATIVE.** `require()` is the only entry to tool execution on the AI-chat surface. A capability absent here cannot run. |

`CapabilitySpec` fields: `capability_id, description, intents, risk, confirmation, tool_name,
permission, fields, executor, verifier, native_route, result_card, audit_category, target_field,
verified_fields, undo_capability_id, undo_argument_map, requires_authentication, failure_behavior,
idempotent`.

Live distribution:

```
risk        : {'read_only': 70, 'reversible_write': 13, 'consequential_write': 4}
confirmation: {'never': 75, 'contextual': 7, 'always': 5}
permission  : {'self_account_only': 85, 'other_user_target': 2}
```

Note what is *absent*: `RiskLevel.HIGH_RISK` is used by zero capabilities and is denied
unconditionally by the policy layer. `PermissionScope.OWNED_CONTENT_TARGET` is declared in
`services/undx_agent_contracts.py:151` and used by zero capabilities — ~~its enforcement branch is
dead code~~ **it has no enforcement branch, and that is the point: it falls through to the terminal
refusal at `undx_tool_gateway.py:457-462`, so declaring it gets a capability rejected rather than
executed under an ownership rule the gateway cannot apply. See the §6.2 reversal.** There is no
capability for sending a message, creating a post, creating a reel, uploading media, moving money,
or performing a moderation action.

### R2 — Production tool registry (LEDGER + PROMPT COPY)

| Field | Value |
|---|---|
| **Location** | `services/undx_policy.py:41` (`PRODUCTION_TOOL_REGISTRY`) |
| **Runtime location** | Module-level dict, **one single assignment**, `services/undx_policy.py:41-186` |
| **Number of capabilities** | **103 tool names** — but "capability" is the wrong word; these are route descriptors |
| **Purpose** | (a) the audit ledger's vocabulary — `undx_architecture.prepare_tool_operation` raises for any tool not in it (`:431-433`); (b) source of the `confirmation` flag that builds `HIGH_IMPACT_TOOLS` (`undx_architecture.py:24`); (c) the "Authorized tool registry for this request" block injected into the model prompt (`undx_policy.py:397-398`); (d) **the allowlist for the dry-run simulator** `simulate_operation` (`:484-485`) |
| **Used by** | `undx_architecture` (`:24` HIGH_IMPACT_TOOLS, `:184` seeding, `:431-433` audit veto, `:485` simulator gate, `:723`); `undx_policy.compile_context` / `_select_tools` |
| **Authority level** | **NOT AUTHORITATIVE for permission.** It can only *subtract*: a capability whose `tool_name` is missing here fails inside the gateway. It cannot *add* — a tool name here with no capability pointing at it cannot be **executed**. It *can*, however, be **named and simulated** — see §2 Surface B″. |

> ### ⚠ CORRECTION — the audit veto was cited at the wrong line
>
> This document originally cited `undx_architecture.py:485` as the raise that gives R2 its
> veto-by-omission. Line 485 is inside **`simulate_operation`** (def at `:484`) — the dry-run
> simulator, which returns `"production_write": False`. The real audit veto is in
> **`prepare_tool_operation` at `:431-433`**. The conclusion (R2 vetoes by omission) stands; the
> evidence originally offered for it pointed at a simulator.

The two registries are joined by `CapabilitySpec.tool_name`. Live result:

```
distinct capability tool_names   : 87
capability tools MISSING from PTR: []          <- perfect coverage
PTR tools reachable by no capability (orphans): 16
```

**The 16 orphans** — present in R2, reachable by nothing:

| Orphan tool name | R2 `confirmation` flag |
|---|---|
| `pulsesoc.send_message` | True |
| `pulsesoc.create_post` | True |
| `pulsesoc.create_reel` | True |
| `pulsesoc.draft_message` | False |
| `pulsesoc.media.init` / `.upload` / `.complete` | False |
| `pulsesoc.get_profile` / `.get_alerts` / `.get_conversation` / `.get_crypto_alert` | False |
| `pulsesoc.search` / `pulsesoc.content.search` | False |
| `pulsesoc.crypto.portfolio.summary` | False |
| `web.search` | False |
| `calculator.execute` | False |

These sixteen names are the entire source of the "103 vs 87" alarm, and of every stale document
claiming UNDX can post and message. ~~**They are inert.**~~

> ### ⚠ CORRECTION — "inert" is too strong
>
> The orphans cannot be **executed**: `require(capability_id)` has no entry for them, and that
> finding survived adversarial attack. But they are not unreachable.
>
> `POST /api/pulse-ai/tools/simulate` (`pulse_communications_v2/routes.py:796` (root-level package, **not** under `services/`)) →
> `pulse_ai_service.simulate_tool` (`:1403`) reads **`payload["tool_name"]`** — a caller-supplied
> string — and passes it to `undx_architecture.simulate_operation`, whose only gate is membership
> in **R2** (`:485`). Every one of the 16 orphans is therefore addressable **by name** by any
> ordinary authenticated user, and returns a structured description of itself.
>
> The response is explicitly a dry run (`"simulated": True, "production_write": False`) and mutates
> nothing, so the central thesis is unaffected. But three consequences matter for the corpus:
>
> 1. "A tool name in R2 with no capability is unreachable" is **false**. It is unreachable *for
>    execution* and reachable *by name*.
> 2. The simulator will happily describe `pulsesoc.send_message` to a caller, reinforcing the
>    belief that UNDX can send messages.
> 3. R2 — the registry this document argues is not an authority — **is** the authority for exactly
>    one runtime decision: what may be simulated.

### R2a — The drift detector, and why it cannot see this

`services/undx_capability_registry.py:1359-1378` defines `unregistered_tool_names()`. Its own
docstring is the clearest statement of the architecture anywhere in the repo:

> The registry decides what UNDX may *propose*; `undx_policy.PRODUCTION_TOOL_REGISTRY` decides what
> the audit ledger will *record* … a capability present here and absent there is not an error
> anyone sees.

It computes `REGISTRY tools − PTR`. Live value: `[]`. Clean.

**It never computes the reverse.** `PTR − REGISTRY tools` is the 16-orphan set, and nothing in the
codebase calculates it. The drift that produces false capability claims in documentation and in the
model prompt is *structurally invisible to the only tool built to detect drift*. This is the single
highest-value one-line fix available in this subsystem.

### R3 — Agent policy (THE DECISION AUTHORITY)

| Field | Value |
|---|---|
| **Location** | `services/undx_agent_policy.py` (330 lines) |
| **Runtime location** | `evaluate(spec, context) -> Decision`, called per request |
| **Number of capabilities** | n/a — it is a rule set over R1, not a tool table |
| **Purpose** | Deterministic allow/deny/confirm decision |
| **Used by** | `undx_tool_gateway.execute()` |
| **Authority level** | **AUTHORITATIVE for the decision.** Binds R1's declarations to runtime state. |

It imports `CapabilitySpec` from R1, so the policy layer is *type-bound* to the 87-registry — it
cannot evaluate an R2 orphan even in principle. Evaluation order: `HIGH_RISK` → unconditional deny;
cohort + master switch (`user_enabled()`); per-capability withdrawal (`capability_enabled()`);
`writes_available()` / `reads_available()`; **ambiguity refusal** (`if spec.is_write and
int(resolved_resource_count) != 1`); confirmation policy.

`REQUIRED_WRITE_GUARDS` (7 env names) **all default ON and fail closed.** The documented invariant —
"There is no code path from message content to `Decision.allow`" — holds: only `explicit_request` and
`resolved_resource_count` derive from user text, and both can only *tighten* the decision.

One inversion worth knowing: `EXECUTOR_ONLY_SUCCESS_ENV`
(`UNDX_COMPLETION_ALLOW_EXECUTOR_ONLY_SUCCESS`, `:73`) — when truthy, `writes_available()` returns
**False** (`:200-201`). Enabling the loosest-sounding flag disables writes entirely. Correct, but
the name invites the opposite reading.

### R4 — Marketplace assistant tools

| Field | Value |
|---|---|
| **Location** | `services/business_os/marketplace/assistant.py:223` (`_TOOLS`) |
| **Runtime location** | Module-level dict |
| **Number of capabilities** | **12** (4 read, 2 unconfirmed writes, 6 confirmed writes) |
| **Purpose** | Governed marketplace verbs: order lifecycle, product lifecycle, payout balance |
| **Used by** | `services/business_os/marketplace/api.py:35`, reached by `bot.py:25138` (tools) / `:25149` (plan) / `:25163` (execute) — `/api/business-os/marketplace/assistant/{tools,plan,execute}`; and by `undx_actions/marketplace_workflow.py:136, 175, 207` |
| **Authority level** | **AUTHORITATIVE within marketplace.** Completely independent of R1/R2/R3. |

`pay_order`, `fulfill_order`, `complete_order`, `cancel_order`, `publish_product`, `pause_product`
are `confirm: True, risk: "high"`. `create_product` and `create_order` are **writes with
`confirm: False`**. `complete_order` "settles escrow to platform fee + seller payable" — this is a
money-moving verb, and it does not exist in R1 in any form.

### R5 — Advertising assistant tools

| Field | Value |
|---|---|
| **Location** | `services/business_os/advertising/assistant.py:219` (`_TOOLS`) |
| **Runtime location** | Module-level dict |
| **Number of capabilities** | **11** (4 read, 1 unconfirmed write, 6 confirmed writes) |
| **Purpose** | Campaign lifecycle and budget |
| **Used by** | `services/business_os/advertising/api.py:40`, reached by `bot.py:23259/23270/23284` |
| **Authority level** | **AUTHORITATIVE within advertising.** Independent of R1/R2/R3. |

`set_budget` is `confirm: True, risk: "high"` — UNDX-adjacent tooling can change an advertising
budget. `create_draft` is an unconfirmed write. Its own docstring says "The model cannot act outside
this registry" (`:256-257`), which is true — and true of a registry the recon had not found.

### R6 — Business OS UNDX governance tool registry (DB-backed, runtime-mutable)

| Field | Value |
|---|---|
| **Location** | `services/business_os/undx_actions/engine.py:214` (`register_tool`) |
| **Runtime location** | **SQLite/Postgres table `business_os_undx_tool_registry`** — not a Python literal |
| **Number of capabilities** | **0 rows locally.** Registered lazily at call time (`marketplace_workflow.register_marketplace_tools()` inserts 2: `marketplace.create_product`, `marketplace.publish_product`) |
| **Purpose** | Descriptive catalog for the org-scoped governance projection (`action_type` → effect, risk ceiling, feature flag, allowed modes) |
| **Used by** | `services/business_os/undx_actions/api.py`, ~17 routes under `/api/business-os/undx/*` |
| **Authority level** | **DESCRIPTIVE, not executive** — its own docstring: "This catalog is descriptive. It does not execute the tool." The *decision* authority is the policy/permission projection in the same module. |

Companion tables, all present and all empty locally: `business_os_undx_policies`, `_permissions`,
`_action_requests`, `_decisions`, `_action_receipts`, `_confirmations`, `_emergency_stops`, `_audit`.

### R7 / R8 — `pulse_ai_tool_registry` and `pulse_ai_capability_registry` (DB, **97 rows each**)

*Found by adversarial verification, not by the first pass.*

| Field | Value |
|---|---|
| **Location** | Seeded by `services/undx_architecture.py:174 / :186 / :197` |
| **Runtime location** | Database tables `pulse_ai_tool_registry`, `pulse_ai_capability_registry` (and `pulse_ai_skill_registry`, 12 rows) |
| **Number of capabilities** | **97 rows each** — matching neither R1's 87 nor R2's 103. `pulse_ai_capability_registry` is keyed on `capability_name`, not `capability_id`. |
| **Purpose** | Descriptive catalog surfaced to admin/introspection views; columns include `authorization_policy`, `risk_level`, `confirmation_required`, `method`, route path |
| **Used by** | Read by admin and self-knowledge surfaces. No execution path consults them. |
| **Authority level** | **DESCRIPTIVE ONLY — and stale by construction.** |

Live sample row:

```
(5, 'pulsesoc.send_message', '1.0', 'existing_pulsesoc_backend', 'pulsesoc.send_message',
 'POST', '/api/pulse/comm/v2/conversations/<conversation_ref>/messages', '{}', '{}',
 'authenticated_server_per_call', 'high', 'required_for_writes', 'correlation_id_redacted',
 1, 'active', '2026-07-30T02:29:09+00:00')
```

Two things make these the most dangerous registries in the set for corpus purposes:

1. **They are seeded with `INSERT OR IGNORE`.** Rows written once, on first boot, and never updated
   when R1 or R2 changes. A grep for `ON CONFLICT` / `INSERT OR REPLACE` against these two tables
   returns hits only in `tests/`. Nothing in `services/` or `bot.py` ever refreshes them. The 97 is
   a fossil of a July 2026 state.
2. **They say `status: 'active'` next to `pulsesoc.send_message`.** Anything — a person, a
   retrieval step, a corpus generator — that reads the database to answer "what can UNDX do?" gets
   a confident, timestamped, *wrong* answer, with no orphan marker and no cross-reference to R1.

**The drift is directional, and that is worse than it first appears.** Live set arithmetic:

```
db_tools 97   PTR 103   capability tool_names 87

PTR - DB  (added to code after the seed, absent from DB):  6
    pulsesoc.crypto.market.window        pulsesoc.crypto_market.observations
    pulsesoc.crypto.portfolio.summary    pulsesoc.crypto_portfolio.history
    pulsesoc.crypto_alerts.activity      pulsesoc.crypto_portfolio.summary

DB - PTR  (in DB, removed from code):                      0
DB rows that are R2 orphans (no capability):              15
```

So the DB is a **strict subset** of R2, frozen at the 97-tool state R2 had when the seed first ran;
six crypto tools have been added to code since and the tables never learned about them. Nothing has
ever been *removed*, which is why the fossil looks plausible rather than obviously broken: every row
in it is a real R2 entry. It is stale by omission, not by contradiction — the hardest kind of
staleness to notice, and the reason "97" reads like a considered number rather than an artefact.

Fifteen of those 97 rows are R2 orphans reachable by no capability, presented with
`authorization_policy`, `risk_level`, `confirmation_required` and a route path, as though they were
live.

Neither `unregistered_tool_names()` nor any other check compares these tables to R1 or R2. **97 vs
103 vs 87 is live, unmonitored, three-way drift.**

---

## 2. RUNTIME EXECUTION TRACE

**Five** surfaces. They do not share an authority model.

### Surface A — `/api/undx/chat` (`bot.py:28795`)

```
POST /api/undx/chat
  -> require_super_user_api()
  -> undx_router  ->  LLM provider
  -> text response
```

No tool execution. No registry consulted. Super-user only. ~~This surface cannot act.~~
**The chat route cannot act. The other routes on the same blueprint can — see Surface E.**

### Surface B — `/api/pulse-ai/*` → the agent path (**the governed write path**)

```
POST /api/pulse-ai/message             pulse_communications_v2/routes.py:629
  -> pulse_ai_service.send_message()                       :759
     -> safety classify / rate limit                       :771-807
     -> undx_policy.compile_context()                      :823   [R2 -> prompt text only]
     -> _agent_turn()                                      :842   [<- THE AGENT PATH]
        -> undx_agent_runtime.handle()                     :726
           -> capability resolution from intents
           -> undx_tool_gateway.execute(capability_id, args)
              1. authentication                            gateway :693
              2. spec = require(capability_id)             gateway :698   [R1 <- AUTHORITY]
              3. typed schema validation from spec.fields  gateway :703
              4. permission-scope enforcement              gateway :707   [fails closed, §6.2]
              5. policy.evaluate(user, spec, args)         gateway :710   [R3 <- DECISION]
                 -> if decision.denied: receipt, RETURN    gateway :715
              6. confirmation redemption (BURNED FIRST)    gateway :384
              7. idempotency
              8. executor = undx_agent_tools.resolve()     gateway :504
              9. independent read-back verification
             10. audit  (undx_architecture)                gateway :939   [R2 <- LEDGER]
     -> if agent_outcome is not None: RETURN               :847-886
```

**Which registry controls what, on this surface:**

| Concern | Controlled by |
|---|---|
| **Read actions** | R1 (`require`) + R3, *except* the read bypass below |
| **Write actions** | **R1** — `require(capability_id)` is the sole gate |
| **Confirmation** | R1 declares (`spec.confirmation`), R3 enforces, `undx_architecture` redeems |
| **Privileged actions** | Nothing here is privileged. No capability targets another user's content, money, or moderation state. |
| **Audit vocabulary** | R2 — and R2 can *veto* by omission (`undx_architecture.prepare_tool_operation`, `:431-433`, raises for unknown tool names) |

### Surface B′ — the same route, conversational fallback

Reached only when `_agent_turn` returns `None` (agent off, or no capability matched).
`compiled_policy`, computed at `:823`, is read across `:890-996` — the notification-action branch
(`:890-915`), the knowledge-message build (`:955`), and the response envelope (`:990-996`) — all of
which sit **after** the agent short-circuit at `:847-886`. So it shapes the fallback conversation and
never the agent path, but it is not a single-use value at `:955` as this document first stated.

This is where the orphans surface. `undx_policy._select_tools` (`:653-657, :667`) selects
`send_message` / `create_post` / `create_reel` / `media.upload` into the block headed
**"Authorized tool registry for this request"** (`:397-398`), which is injected into the model prompt.

**The model is told it is authorized for four writes, on the one surface that has no tool execution
at all.** The only write reachable on this path is a notification-preference confirmation card
(`pulse_ai_service.py:890-915`) via `undx_architecture.create_confirmation`, redeemed at `:1648`
with `expect_action_id="notifications.preference.update"` — hard-bound to that one action.

This is a **truthfulness defect, not an authority breach**. Nothing executes. But it is precisely
the sentence a training corpus would learn from, and it is false.

### Surface B″ — the dry-run simulator (**the one place R2 is the authority**)

*Missed by the first pass; found by adversarial verification.*

```
POST /api/pulse-ai/tools/simulate       pulse_communications_v2/routes.py:796
  -> _require_user()                                routes.py:183-187
     [401 if no session. THAT IS THE WHOLE AUTHORIZATION. No role, no super-user.]
  -> pulse_ai_service.simulate_tool(user_id, payload)          :1403-1410
     -> tool_name = _clean(payload.get("tool_name") or "", 120)      :1405
        [TRUNCATION ONLY — no allowlist, no filter, caller-supplied]
     -> undx_architecture.simulate_operation(tool_name, args)  :484-497
        -> if tool_name not in undx_policy.PRODUCTION_TOOL_REGISTRY:
               raise ValueError("tool_not_registered")         :485   [R2 <- SOLE GATE]
        -> return {"simulated": True, "production_write": False, ...}   :489
```

`require()` is never called. R1 is never consulted. R3 never evaluates. The function body is a dict
literal — no cursor, no database call, no write of any kind. The only membership test is against
**R2**, so all 103 names — including all 16 orphans — are addressable by any **ordinarily
authenticated** user, and each returns a structured self-description.

**Nothing mutates**: the response is explicitly `production_write: False`. The execution thesis is
unaffected. What this surface breaks is the *description*: it is a live endpoint that will confirm,
to anyone who asks, that `pulsesoc.send_message` is a known tool. Both a curious engineer and a
retrieval-augmented corpus generator would read that as capability.

**Which registry controls what, here:** R2 controls everything. It is the only surface where that
is true.

### Surface C — Business OS assistants (`/api/business-os/{advertising,marketplace}/assistant/*`)

```
POST /api/business-os/marketplace/assistant/plan       bot.py:25149
  -> pulse_ads_api_user_required()  + pulse_ads_verify_write()  [CSRF]
  -> marketplace/api.py -> assistant.plan(user_id, tool, params)
     -> _spec(tool)                       [R4 <- AUTHORITY]
     -> read tool: runs immediately
     -> confirmed write: _norm_params -> confirmations.mint(ns, user, tool, canonical)

POST /api/business-os/marketplace/assistant/execute    bot.py:25163
  -> assistant.execute(...)
     -> _spec(tool)                       [R4]
     -> if not write: run read, return
     -> if _writes_disabled(): 409
     -> if spec['confirm']: _consume_confirmation()  <- BURNED BEFORE HANDLER
     -> spec['handler'](uid, canonical)   <- the real mutation
     -> spec['verify'](...)               <- read-after-write against canonical state
```

**R1, R2 and R3 are not consulted anywhere on this path.** Confirmation uses a *different* system:
`services/business_os/confirmations.py`, table `business_os_confirmation_grants`, namespaced
(`"marketplace"` / `"advertising"`), not `pulse_ai_confirmations`.

### Surface D — Business OS UNDX governance + the marketplace bridge

```
POST /api/business-os/undx/marketplace/listings/draft   bot.py:26124
  -> _business_os_undx_actions_enabled()   [BUSINESS_OS_UNDX_ACTIONS; unset => DARK 404]
  -> pulse_ads_api_user_required() + CSRF
  -> _business_os_undx_user_scope(user)    <- org_id and actor are SERVER-OWNED, not client-supplied
  -> undx_actions/api.marketplace_create_listing_draft(trusted_org_id=, trusted_actor=)
     -> marketplace_workflow.create_listing_draft()
        -> canonical_listing_params()          [price/fulfilment must be explicit]
        -> register_marketplace_tools()        [R6 <- 2 rows inserted]
        -> record_action_request()
        -> evaluate_org()                      [R6 governance <- DECISION]
        -> if effect != "allow": record blocked/cancelled receipt, RETURN (no write)
        -> _mkt_assistant.execute(user_id, "create_product", params)   [R4 <- EXECUTION]
        -> record_receipt(verified | failed)
```

**Governance precedence** (`engine.py:691-711`), highest first:

1. **Emergency stop** (`_active_stops`) → `deny`, unconditional.
2. **Actor permission** (`_resolve_permission`) → if any row matches, **org policy is never
   consulted**. An `allow` permission overrides an org-level `deny` policy.
3. **Org policy** (`_resolve`) — exact `action_type` beats `*`; higher priority wins; an `allow`
   whose `max_risk` ceiling is exceeded escalates to `require_approval`.
4. **No match → `require_approval`** (`:655-657`) — **fail-closed default.**

With all governance tables empty (the current local state) the marketplace draft path returns
`requires_approval` and **executes nothing**. The default posture is safe.

The permission-granting route (`POST /api/business-os/undx/permissions`, `bot.py:25972`) is
`require_owner_api()` + CSRF. A user cannot grant themselves a permission. The precedence inversion
in step 2 is therefore owner-controlled, not user-reachable — but it *is* a genuine inversion, and
it is undocumented.

**Contradiction inside bot.py.** The section header at `bot.py:25908-25915` states:

> **NOTHING here executes an action — a decision is a governance label, not an instruction.**

~~Three routes~~ **Two routes** below that comment reach `_mkt_assistant.execute()` and mutate
marketplace state: `/undx/marketplace/listings/draft` (`bot.py:26124` →
`marketplace_workflow.py:136`) and `/undx/marketplace/listings/publish/execute` (`bot.py:26156` →
`marketplace_workflow.py:207`). The third route, `/publish/plan` (`bot.py:26140`), calls
`_mkt_assistant.**plan**()` at `marketplace_workflow.py:175` — it mints a confirmation and writes
nothing, so the comment is accurate for it. Two mutating routes is still two more than "nothing";
the comment was accurate for the governance vertical and was not updated when the marketplace
bridge was added beneath it.

### Surface E — `/api/undx/kernel/*` (**writes files and pushes to git**)

*Missed by the first pass; found by adversarial verification.*

```
POST /api/undx/kernel/{scan,propose,apply,validate,git}
                                        bot.py:28930 / 28950 / 28978 / 29006 / 29025
  -> user, gated = undx_kernel_user()   <- IN-BODY CALL, NOT A DECORATOR
     if gated: return gated               :28932-34, 28952-54, 28980-82,
                                          :29008-10, 29027-29
     undx_kernel_user() == require_super_user_api()            bot.py:28874-28875
  -> undx_execution_kernel
     - propose: diff against the repository working tree
     - apply:   writes files, gated on the approval phrase APPROVE UNDX WRITE
     - git:     git_gateway(action)                            :823-845
                action allowlist  status | add | commit | push
                status is ungated (read); add / commit / push each check the phrase
                push -> :837-839 approval check, then
                        :840 command = ["git","push","origin","main"]
                APPROVAL_PHRASE = "APPROVE UNDX WRITE"         :27
                GUARD_APPROVAL_PHRASE = "APPROVE UNDX GUARD CHANGE"   :80

AND a sixth route on the same gate:
GET|POST /api/undx/desktop-connector/<path:connector_path>     bot.py:28893-28895
  -> undx_kernel_user()  (same super-user gate)
  -> allowlist of 10 connector paths                           bot.py:28879-28890
     including patch/apply, git/commit, git/push
  -> proxies to UNDX_DESKTOP_CONNECTOR_URL (default http://127.0.0.1:8765)
```

**Auth here is a function call in the route body, not a decorator.** All six routes do apply it —
none is reachable without super-user — but the pattern is worth naming: a decorator omission is
visible at a glance, whereas a missing two-line prologue in a 111k-line file is not. This is a
convention that depends on every future route author remembering.

**No capability registry is consulted on this surface at all.** Not R1, not R2, not R3, not R6.
Authority is: super-user session **and** a literal approval phrase in the request body. The
protected-path denylist (`.env`, `.git`, venv, secrets, sqlite) is enforced inside the kernel, and
every action appends to `undx_execution_log.jsonl`.

The desktop-connector proxy deserves separate note: it forwards `patch/apply` and `git/push` to a
process on `127.0.0.1:8765` that is outside this repository entirely. Whatever governs that process
is not visible from here, and no conclusion in this document covers it.

This is the **highest-consequence surface in the system** — it can modify the application's own
source and publish that modification — and it is the one furthest from the capability model. That is
not necessarily wrong: it is a developer tool, restricted to super-users, with a human-typed phrase
as the final gate. But any statement of the form "UNDX cannot change X" must exclude this surface
explicitly, because on this surface UNDX can change essentially anything.

---

## 3. REGISTRY COMPARISON

**Reading the columns.** *R1* = capability registry (permission authority). *R2* = production tool
registry (ledger/prompt/simulator allowlist). *Production runtime* = what an authenticated user can
actually cause, across all five surfaces. *Policy should-be* = what the existing security design
implies.

Note the third column now distinguishes **unexecutable** from **unreachable**. Every orphan is
unexecutable; none is unreachable, because Surface B″ addresses them by name. R7/R8 also carry them,
marked `'active'`.

### The contested capabilities

| Capability | R1 (87) | R2 (103) | Production runtime | Policy should-be |
|---|---|---|---|---|
| **Send a message** | **absent** | `pulsesoc.send_message` present, `confirmation: True` | **NOT EXECUTABLE** — orphan, no `require()` path. *But* simulable by name (B″) and listed `'active'` in R7/R8. | Unavailable. Sending as the user is impersonation-adjacent; there is no ownership model for it. |
| **Create a post** | **absent** | `pulsesoc.create_post` present, `confirmation: True` | **NOT EXECUTABLE** — orphan; simulable by name | Unavailable. Publishing to third parties is irreversible in practice. |
| **Create a reel** | **absent** | `pulsesoc.create_reel` present, `confirmation: True` | **NOT EXECUTABLE** — orphan; simulable by name | Unavailable. Same reasoning. |
| **Draft a message** | `messages.draft`, **read_only** | `pulsesoc.draft_message` (orphan, separate name) | **AVAILABLE as a read.** Produces text; does not send. | Correct as-is. Drafting is the right primitive. |
| **Upload media** | **absent** | `media.init/upload/complete` present | **NOT EXECUTABLE** — orphans; simulable by name | Unavailable until an ownership model exists. |

### Account actions

| Capability | R1 | R2 | Production runtime | Policy should-be |
|---|---|---|---|---|
| `notifications.preference.update` | present, `reversible_write`, **`always`** confirm | present | **AVAILABLE**, confirmed, on both Surface B and B′ | Correct. |
| `profile.preferences.update` | present, `reversible_write`, `contextual` | present | **AVAILABLE**, sometimes unconfirmed | Defensible; reversible and self-scoped. |
| Change password / email / delete account | **absent** | absent | **UNAVAILABLE** | Correct — must stay absent. |

### Financial actions

| Capability | R1 | R2 | Production runtime | Policy should-be |
|---|---|---|---|---|
| Any payment / payout / transfer | **absent** | absent | **UNAVAILABLE on Surfaces A/B/B′/B″** | Correct for the AI-chat surface. |
| `pay_order` (captures total into escrow) | absent | absent | **AVAILABLE via R4**, Surface C — `confirm: True`, `risk: high` | Confirmation is right. Should also be represented in R1's vocabulary so one document can describe UNDX's full authority. |
| `complete_order` (settles escrow → platform fee + seller payable) | absent | absent | **AVAILABLE via R4**, confirmed | As above. |
| `set_budget` (advertising spend) | absent | absent | **AVAILABLE via R5**, confirmed | As above. |
| `payout_balance` (read) | absent | absent | **AVAILABLE via R4**, read-only | Fine. |

### Moderation actions

| Capability | R1 | R2 | Production runtime | Policy should-be |
|---|---|---|---|---|
| Report / block / mute / remove content | **absent** | absent | **UNAVAILABLE on every registry-governed surface** (A/B/B′/B″/C/D). Surface E, being unbounded file access, is excluded from this claim by construction. | Correct. Moderation acting on a third party has no ownership model here. |

### Marketplace actions

| Capability | R1 | R2 | Production runtime | Policy should-be |
|---|---|---|---|---|
| `marketplace.listing.summary`, `.order.status`, `.search` | present, **read_only** | present | **AVAILABLE**, reads | Correct. |
| `create_product` (draft) | absent | absent | **AVAILABLE via R4 / R6 bridge — write, `confirm: False`** | Acceptable *only* because a draft is inert and the R6 default is `require_approval`. The unconfirmed-write posture should be documented, not implicit. |
| `publish_product` | absent | absent | **AVAILABLE via R4 / R6 bridge**, `confirm: True`, dual gate (R6 decision **and** R4 token) | Correct — this is the best-governed write in the system. |
| `create_order` | absent | absent | **AVAILABLE via R4 — write, `confirm: False`** | Defensible (no money moves until `pay_order`), but it decrements nothing and creates an obligation record. Worth revisiting. |

### Social actions

| Capability | R1 | R2 | Production runtime | Policy should-be |
|---|---|---|---|---|
| `social.follow` / `social.unfollow` | present, `reversible_write`, **`never`** confirm, **`other_user_target`** | present | **AVAILABLE, unconfirmed, third-party-visible.** Target existence *is* verified at the gateway (`:447-449`). | See §6.5 — the ownership check works; the concern is that its refusal is an existence oracle. |
| `feed.posts.like` / `.unlike`, `saved.post.set` | present, `reversible_write`, `never` | present | **AVAILABLE**, unconfirmed | Acceptable; reversible, low-visibility. |
| `feed.posts.delete` | present, **`consequential_write`**, `always` | present | **AVAILABLE**, confirmed, own posts only | Correct. |

---

## 4. THE SOURCE OF TRUTH

Chosen from the existing security design, not from convenience. The design already declares its own
answer in three places, and they agree.

### 4.1 For the AI-chat surface: **R1, the 87-entry capability registry.**

Three independent structural facts force this, none of them a matter of preference:

1. **`require(capability_id)` is the only door to execution.** `undx_tool_gateway.py:698` calls it
   before anything else touches an executor. No capability, no execution. R2 has no equivalent
   entry point — its one runtime gate (Surface B″) admits names to a *simulator*, never to an
   executor.
2. **The policy layer is type-bound to R1.** `undx_agent_policy.evaluate()` takes a `CapabilitySpec`.
   It is not merely conventional that R2 orphans are unevaluable — it is a type error.
3. **R1's own docstring assigns the roles.** `undx_capability_registry.py:1359-1378`: the registry
   decides what UNDX may *propose*; `PRODUCTION_TOOL_REGISTRY` decides what the ledger will *record*.
   The authors wrote the answer down; the recon simply had not read it before counting.

R2 is authoritative for exactly two things: **the audit vocabulary**, where it holds a veto by
omission (`undx_architecture.prepare_tool_operation`, `:431-433`), and **the simulator allowlist**
(`simulate_operation`, `:484-485`). It is authoritative for **nothing** about permission to execute.

### 4.2 For the Business OS surfaces: **R4 and R5, gated by R6.**

Different domain, different authority, and that is defensible — marketplace and advertising verbs
have a state machine, an ownership model, and an escrow ledger that the social capability vocabulary
does not. The mistake would be to pretend one registry governs both.

For the UNDX-initiated marketplace path specifically, authority is **conjunctive**: R6 must decide
`allow` *and* R4 must redeem a valid confirmation token. Two independent systems must both say yes.
That is the strongest posture in the codebase.

### 4.3 The single sentence a corpus may assert

> UNDX's authority on the AI-chat surface is exactly the 87 capabilities in
> `services/undx_capability_registry.REGISTRY`. Marketplace and advertising writes are a separate,
> separately-governed surface reached through the Business OS assistants, and are not part of the
> conversational agent's authority. The execution kernel (`/api/undx/kernel/*`) is a super-user
> developer tool outside the capability model entirely and must never be described as an agent
> capability.

Anything broader is false. Anything narrower omits the money — or the kernel.

---

## 5. STALE DOCUMENTATION

Recorded, not edited. No file below was modified by this mission.

### TIER 1 — Asserts capabilities that do not exist (blocks corpus creation)

| Document | Old claim | Verified reality | Recommended update |
|---|---|---|---|
| Hand-written bootstrap/v5 capability YAMLs (4 files) | UNDX can send messages, create posts, publish content | All four verbs are R2 orphans; no capability, no `require()` path | Delete the four verbs, or regenerate the files from `REGISTRY` programmatically so they cannot drift again |
| `docs/undx_manual.md` | Describes a tool surface including messaging/posting | Not reachable | Rewrite the capability section as a generated table from `REGISTRY` |
| `CLAUDE.md:52-58` | UNDX summary omits the Business OS assistant surfaces entirely | Two additional live registries with money-moving verbs | Add R4/R5/R6 to the UNDX section |
| `bot.py:25908-25915` (code comment) | "NOTHING here executes an action" | **Two** routes below it execute marketplace writes (`:26124`, `:26156`); `/publish/plan` at `:26140` genuinely does not | Amend the comment to carve out the marketplace bridge |
| `services/business_os/advertising/assistant.py:256-257` | "The model cannot act outside this registry" | True, but reads as though *this* is the UNDX registry | Add one line naming the relationship to `undx_capability_registry` |
| **DB tables `pulse_ai_tool_registry` / `pulse_ai_capability_registry` (R7/R8)** | 97 rows each; `pulsesoc.send_message` carries `risk_level: 'high'`, `status: 'active'`, a real route path and a July 2026 timestamp | Not executable — orphan in R2, absent from R1. The rows are `INSERT OR IGNORE` fossils never refreshed after first boot | **Highest-priority item in this table.** These are *data*, so any retrieval step or corpus generator reading the DB will treat them as authoritative and dated. Either re-seed from `REGISTRY` on boot, add an `authoritative: false` column, or stop surfacing them. Documentation can be ignored; a database row will be believed. |
| `POST /api/pulse-ai/tools/simulate` (behaviour, not text) | Returns a structured description for any R2 name, including all 16 orphans, behind login-only auth | Dry run only — `production_write: False` | Gate the simulator on R1 rather than R2, or have it return `executable: false` for orphans. Today it is a live endpoint that confirms non-existent capabilities on request. |
| **`UNDX_RECON/README.md:56-59`** | "Read `11_UNKNOWN_AREAS.md` Tier 1 first. The top blocking question is §1.1 — two tool registries (87 vs 103) … disagree about whether UNDX may send messages" | **Resolved by this document.** Not a conflict between authorities; an authority and a ledger. R1 is the source of truth; UNDX may not send messages. | Repoint the "before writing any corpus" note at this file. Also note the filename collision: `11_UNKNOWN_AREAS.md` and `11_AUTHORITY_RECONCILIATION.md` share a numeric prefix. *Not edited by this mission — §5 records, it does not modify.* |
| `UNDX_RECON/11_UNKNOWN_AREAS.md` §1.1 | Same blocking question, stated as open | Same — resolved | Mark §1.1 resolved with a pointer here, rather than deleting it; the reasoning is worth keeping. |
| **`services/undx_tool_gateway.py:419-425` (docstring)** | "Only `self_account_only` has an enforcement rule today"; scopes for acting on another user "are refused rather than executed … until Stage 6 and 8 build one" | **`OTHER_USER_TARGET` is now enforced and executed** at `:437-456`, with a real `is_following` lookup. Stage 6/8 arrived; the docstring did not. | Update the docstring to describe two enforced scopes and one refused. *Found while verifying the §6.2 reversal — a comment that undersells its own code, which is the failure mode that produced the §6.2 error in the first place.* |

### TIER 2 — Payout claims (already corrected in the recon set, still wrong at source)

| Document | Old claim | Verified reality | Recommended update |
|---|---|---|---|
| `FLAG_REGISTRY.md:215` | Payout initiation gated by `EXPO_PUBLIC_PAYMENTS_PAYOUT_INITIATION` | Flag retired; `paymentsHub.ts:198` `payoutInitiationIsLive()` returns `true` unconditionally | Remove the flag row; describe the current unconditional state |
| `PAYMENTS_SCREEN_REBUILD.md:55` | Same | Same | Same |
| 3 further payout docs | Money-out unbuilt | Built, unexercised | Restate as "built, not exercised in production" |

### TIER 3 — Minor

| Document | Old claim | Verified reality | Recommended update |
|---|---|---|---|
| `mobile/docs/api-integration-map.md:55` | Legacy UNDX endpoint shape | `mobile/` is the legacy app | Mark the file legacy or delete |

**The 1.43 MB v6 corpus is largely clean** — its apparent hits are mechanical symbol dumps, not
assertions. It does not need regeneration on account of this finding.

---

## 6. SECURITY REVIEW

For every capability class that sends messages, creates content, modifies accounts, affects money,
or affects permissions.

| Class | Can UNDX execute directly? | Requires confirmation? | Confirmation enforced? | Ownership checked? | Audit logged? |
|---|---|---|---|---|---|
| Send message | **No** — no capability | n/a | n/a | n/a | n/a |
| Create post / reel / upload media | **No** — no capability | n/a | n/a | n/a | n/a |
| Delete own post | Yes | Yes (`always`) | **Yes** — token burned pre-execution | By executor convention only | Yes (write path) |
| Notification preference | Yes | Yes (`always`) | **Yes**, action-id-bound | Self-scoped by definition | Yes |
| Profile preferences | Yes | `contextual` — often not | n/a when skipped | Self-scoped | Yes |
| Like / unlike / save | Yes | No | n/a | Executor-scoped; gateway checks actor-naming only (§6.2) | Yes |
| **Follow / unfollow** | Yes | **No** | n/a | **Yes** — gateway DB check, `undx_tool_gateway.py:447-449` (§6.5) | Yes |
| Crypto alert create/update/delete | Yes | Yes (`always`) | **Yes** | Yes — `get_alert_rule(alert_id, user_id)` is user-scoped (§6.6) | Yes |
| **Marketplace pay/fulfil/complete/cancel** | Yes, **Surface C only** | Yes | **Yes** — separate namespaced grant | **Yes** — inside canonical service verbs | Via receipts, on the R6 path only |
| **Marketplace create product / create order** | Yes, Surface C | **No** | n/a | Yes, in service layer | Receipts on R6 path |
| **Advertising set_budget / lifecycle** | Yes, Surface C | Yes | **Yes** | Yes (`_assert_owned`, `requester_user_id`) | Via service layer |
| Permission grants (R6) | **No** — `require_owner_api()` | n/a | n/a | Owner-only | Yes |

### 6.1 What is genuinely well built

**Confirmation redemption.** Both systems get the hard part right. Grants are bound to
(namespace/user, tool/action, canonical argument hash), single-use via a compare-and-swap
(`UPDATE … WHERE token_hash=? AND status='pending'` with a rowcount guard —
`confirmations.py:265-275`; `undx_architecture.py:1021-1026`), TTL-bounded (30–300 s), revocable,
and **burned before the handler runs**, so a failed action cannot leave a replayable approval.
Binding is checked *before* status and expiry (`confirmations.py:239-243`) specifically so a
mis-bound token is indistinguishable from an unknown one — a deliberate, correct anti-enumeration
choice.

**Read-after-write verification.** Both surfaces re-read canonical state and derive `ok` from the
observation rather than the verb's return value.

**Deterministic policy.** No path from message content to `allow`.

**Fail-closed defaults.** R3's seven write guards default on. R6's no-match default is
`require_approval`. `BUSINESS_OS_MARKETPLACE` and `BUSINESS_OS_ADVERTISING` are unset ⇒ inert.
`BUSINESS_OS_UNDX_ACTIONS` unset ⇒ dark 404.

### 6.2 ~~GAP — the gateway does not enforce ownership~~ **RETRACTED — the reverse is true**

> ### ⚠ REVERSAL — this section was wrong, and wrong in the dangerous direction
>
> The original text read:
>
> > `_enforce_permission_scope` (`undx_tool_gateway.py:406-462`) performs **no database lookup**. It
> > compares declared field names against `_ACTOR_NAMING_FIELDS` (`:400-403`) … Every resource-scoped
> > write therefore passes the scope check trivially. `PermissionScope.OWNED_CONTENT_TARGET` … the
> > refusal branch is unreachable. … The gateway's scope check contributes nothing.
>
> **Both load-bearing claims are false.** The function was read to its first `return` and the
> remainder was inferred. Actual code at `undx_tool_gateway.py:427-462`:
>
> - `:427-436` `SELF_ACCOUNT_ONLY` — the structural check. No DB lookup here, correctly: the rule is
>   that such a capability may not declare a field naming *whose* data to touch, so there is nothing
>   for a hostile argument to point at. This is a **stronger** guarantee than a lookup, not a weaker
>   one — it removes the attack surface rather than validating it.
> - `:437-456` `OTHER_USER_TARGET` — **does perform a database lookup.** `:447-449` imports
>   `is_following` and calls it; a non-positive or non-existent `target_id` raises
>   `invalid_user_target` / `PERMISSION_DENIED`. It also refuses (`:439-445`) any capability that
>   declares this scope without a `target_user_id` field — `capability_scope_unenforceable`.
> - `:457-462` — a **terminal, reachable** `raise`. Any capability declaring a scope with no
>   enforcement rule (including `OWNED_CONTENT_TARGET`) hits this and is refused. It is not dead
>   code; it is the fail-closed floor.
>
> The function's own docstring states the design intent verbatim: *"So it fails closed."* It also
> records why the field exists — `permission` "spent its first life as a comment … consulted by
> nothing," which the authors identified as *worse* than having no field, and then fixed.
>
> **Corrected assessment: `_enforce_permission_scope` is one of the better-designed components in
> the subsystem.** It is a fail-closed dispatch with a terminal refusal, not an inert check.

**What survives of the original concern** — narrowed, and correctly stated:

`_ACTOR_NAMING_FIELDS` (`:400-403`) covers actor-naming identifiers (`user_id`, `owner_id`,
`target_user_id`, `on_behalf_of`, …) and does **not** cover *resource* identifiers (`post_id`,
`alert_id`, `conversation_id`). For a `self_account_only` capability that takes a resource id, the
gateway does not verify that the resource belongs to the caller; that is left to the executor's own
query scoping. This is deliberate and documented — the docstring says the content-item scope "needs
a resolver that authorises the target before execution; until Stage 6 and 8 build one, a capability
declaring them is refused." So the gap is **acknowledged and gated**, not silent: a capability that
wanted gateway-level content ownership must declare `OWNED_CONTENT_TARGET`, and declaring it gets
the capability refused outright rather than executed under an unenforced rule.

**Impact today: low, and structurally bounded** — 85 of 87 capabilities are `self_account_only`, the
2 `other_user_target` ones are DB-checked, and a future capability that needs content ownership
cannot quietly opt out: its only options are executor-scoped queries or an outright refusal.

### 6.3 GAP — audit is not unconditional

`begin_tool_operation` runs at `undx_tool_gateway.py:848` only `if spec.is_write`. **No ledger row
is written for any denial:** unauthenticated (`:693`), unsupported capability (`:698`), scope
violation (`:707`), policy deny (`:715-721`), confirmation required (`:754`), grant not redeemable
(`:772`).

The security-relevant events — repeated denials, probing for capabilities, replayed tokens — are
exactly the events that leave no trace. A `WARNING` log line is not an audit record.

### 6.4 GAP — read-path bypass

`undx_agent_runtime.py:497, 509, 541, 2049, 2058` call executors **directly**, skipping
`policy.evaluate`, idempotency, and audit. Reads only, owner-scoped, guarded by `_read_permitted`
(`:488`). Defensible as an optimisation; undesirable as an undocumented second path into the same
executors. Any future capability moved into that set stops being policy-governed silently.

### 6.5 GAP — `social.follow` / `social.unfollow` are a user-enumeration oracle

Both are `permission: other_user_target`, `confirmation: never`, `risk: reversible_write` — the only
two capabilities in R1 that touch another user, and neither is confirmed.

**Ownership *is* checked.** `_enforce_permission_scope` calls `is_following(user_id, target_id)` at
`undx_tool_gateway.py:447-449` and refuses on `None` — see the §6.2 reversal. The original version of
this section leaned on the (false) claim that the scope check was inert; it does not need to.

The surviving concern is narrower and still real. `is_following(...)`
(`services/social_relationship_service.py:77-99`) returns `None` for self-target, deleted, or
nonexistent user, and a `bool` otherwise — so a refusal and a success are **distinguishable
outcomes keyed on account existence**. Because the verbs are unconfirmed, an attacker with a session
can enumerate account existence at conversational speed, and each successful probe produces a
**follow notification visible to the target**.

So: the gate works, and the gate's *response* is the oracle. Recommended: `contextual` confirmation
at minimum, and a uniform refusal that does not distinguish "no such account" from "not eligible".

### 6.6 ~~GAP — verification trusts the executor's own claim~~ **RETRACTED — the gap does not exist**

> ### ⚠ RETRACTION
>
> The original text read:
>
> > `crypto_alert_exists` (`services/undx_verification.py:118`) reads `alert_id` from `result.data` —
> > the executor's own output — then verifies that ID exists. **An executor that returned a
> > *different* user's alert ID would verify successfully.**
>
> The bolded consequence is false. `services/undx_verification.py:127` is:
>
> ```python
> rule = _alert_engine().get_alert_rule(alert_id, int(user_id))
> ```
>
> The lookup is **scoped by the acting user**. An executor returning another user's `alert_id`
> resolves to `None` and the verification **fails**, which is exactly the desired behaviour.
>
> The narrow, true residue: the *identifier* being verified originates with the executor, so a
> compromised executor could name a different alert **belonging to the same user**. That is a
> self-scoped confusion at worst, not a cross-tenant one, and it requires an already-compromised
> executor. It does not warrant a gap entry. This risk is removed from §8.

### 6.7 GAP — divergent write-flag philosophy

R3's guards default **ON** (fail closed). R4/R5's `*_ASSISTANT_DISABLE_WRITES` default **OFF**, i.e.
writes enabled (fail open). The outer `BUSINESS_OS_*` flags default off, so the composite posture is
still safe — but two opposite conventions in one codebase is exactly how a future flag gets the
wrong default.

### 6.8 NOTE — governance precedence inversion

`engine.py:703-711`: a matching actor permission short-circuits org policy entirely. A per-actor
`allow` beats an org-wide `deny`. Only an emergency stop beats a permission. Owner-only to grant,
so not user-reachable — but undocumented, and the opposite of what "org policy" suggests.

---

## 7. RECOMMENDED AUTHORITY MODEL

```
                         EMERGENCY STOP  (R6, org-scoped)
                                  |  deny beats everything
                                  v
   +------------------------------+------------------------------+
   |                                                             |
AI-CHAT SURFACE                                     BUSINESS OS SURFACE
(/api/pulse-ai/*)                          (/api/business-os/**/assistant/*,
   |                                        /api/business-os/undx/marketplace/*)
   v                                                             |
R1  undx_capability_registry.REGISTRY  (87)                      v
    == THE PERMISSION AUTHORITY ==            R6  governance projection  (org policy,
    require(capability_id) or nothing runs        actor permission, risk ceiling;
   |                                              no match => require_approval)
   v                                                             |
R3  undx_agent_policy.evaluate()                                 v  (conjunctive)
    == THE DECISION AUTHORITY ==              R4/R5  assistant _TOOLS
    deterministic; content cannot allow            == DOMAIN AUTHORITY ==
   |                                               plan() mints, execute() burns
   v                                                             |
undx_tool_gateway.execute()  (9 steps)                           v
   |                                          canonical service verb
   v                                          (ownership + state machine)
R2  PRODUCTION_TOOL_REGISTRY  (103)                              |
    == AUDIT VOCABULARY ==                                       v
    veto-by-omission; never grants           read-after-write verification
    + simulator allowlist (dry run only)     -> receipt


   OUTSIDE THE MODEL ENTIRELY:

   Surface E  /api/undx/kernel/*     require_super_user_api() + "APPROVE UNDX WRITE"
              writes repo files, git push origin main.  No registry consulted.

   R7 / R8    pulse_ai_{tool,capability}_registry  (97 rows each, INSERT OR IGNORE)
              DESCRIPTIVE AND STALE. Read by humans and retrieval. Grants nothing.
```

**Rules this model implies, none of which require a behaviour change to adopt:**

1. R1 is the outer bound of conversational-agent authority. Full stop.
2. R2 may only subtract, and may admit names to the *simulator* only. It must never be read as a
   capability list — by a human or by a corpus.
3. R4/R5/R6 are a *separate* authority domain. Documents describing "what UNDX can do" must say
   which surface they mean.
4. Money moves only under conjunctive authority (R6 decision **and** R4 token).
5. Surface E is not a capability. It is a super-user developer tool, and every capability statement
   must be scoped to exclude it explicitly rather than by omission.
6. R7/R8 are descriptive artefacts, not authority. Anything generated from them is generated from a
   fossil.
7. Every claim about UNDX's capabilities should be **generated from `REGISTRY`**, never hand-written.

---

## 8. UNRESOLVED RISKS

**Renumbered after the verification pass.** Two entries from the first draft are gone: *"gateway
ownership check is structurally inert"* (retracted — §6.2, the reverse is true) and *"verification
trusts executor-supplied IDs"* (retracted — §6.6, the lookup is user-scoped). Three entries are new:
R7/R8, the simulator, and Surface E. A risk table that shrank in one place and grew in two is the
expected shape of an honest correction.

| # | Risk | Severity | Why it is unresolved |
|---|---|---|---|
| 1 | **R7/R8 assert `send_message` is `'active'` in the live database** (§1 R7/R8, §5) | **High** | 97 stale rows, `INSERT OR IGNORE`, never refreshed. Documentation can be corrected; a timestamped database row will be believed by the next reader and by any retrieval step. This is now the top corpus-contamination risk. |
| 2 | Denials are never audited (§6.3) | **High** | `begin_tool_operation` runs only `if spec.is_write`. Six denial paths write no ledger row. Probing and replay attempts leave no record. |
| 3 | **Three-way registry drift, 87 / 97 / 103, monitored by nothing** (§1 R2a, §1 R7/R8) | **Medium-High** | `unregistered_tool_names()` computes `R1 − R2` only. `R2 − R1` (the 16 orphans) and both comparisons against R7/R8 are computed nowhere. The drift that produced every stale claim in §5 is structurally invisible to the only tool built to detect drift. |
| 4 | `social.follow/unfollow` refusal is an existence oracle (§6.5) | **Medium** | Ownership *is* checked; the check's outcome is the leak. Unconfirmed and third-party-visible, so enumeration is cheap and noisy to the target. Downgraded from Medium-High: the gateway does verify the target. |
| 5 | **Simulator admits orphan tool names by caller-supplied string** (§2 Surface B″) | **Medium** | `production_write: False`, so no execution risk. But it is a live endpoint that confirms `pulsesoc.send_message` exists to anyone who asks, and R2 is its sole gate. |
| 6 | Prompt asserts unauthorized writes (§Surface B′) | **Medium** | Harmless to execution, poisonous to a corpus. |
| 7 | Read-path bypass (§6.4) | **Medium** | Undocumented second route into executors. Any future capability moved into that set stops being policy-governed silently. |
| 8 | **Surface E is outside the capability model entirely** (§2 Surface E) | **Medium (by design)** | Super-user + approval phrase is the whole authority. Appropriate for a developer tool; dangerous only if a capability statement is written that forgets to exclude it. Listed so it cannot be forgotten. |
| 9 | Gateway does not check *resource* ownership for `self_account_only` (§6.2, narrowed) | **Low-Medium** | Acknowledged and gated by the authors: content-target scope is refused rather than executed unverified. Executor query scoping carries it today. |
| 10 | Governance precedence inversion (§6.8) | **Low-Medium** | Owner-only, but counter-intuitive and unwritten. |
| 11 | Divergent flag-default philosophy (§6.7) | **Low** | Composite posture safe; convention is a trap. |
| 12 | R6 tool registry is empty locally | **Unknown** | Runtime-registered. **Production contents unverified** — see below. |
| 13 | **Desktop-connector proxy forwards `patch/apply` and `git/push` off-repo** (§2 Surface E) | **Unknown** | `bot.py:28893` proxies 10 allowlisted paths to `127.0.0.1:8765`. Super-user gated on this side; whatever governs the receiving process is outside this repository and outside this recon. |
| 14 | Kernel-surface auth is an in-body call, not a decorator (§2 Surface E) | **Low (latent)** | All six routes currently apply it. A future route that omits the two-line prologue is unauthenticated and looks normal in review. |

---

## 9. CORPUS BLOCKERS

**Cleared.** The blocker recorded in `11_UNKNOWN_AREAS.md` §1.1 — "two registries disagree about
whether UNDX may send messages" — is resolved. It may not. R1 is the source of truth.

**Remaining, before a corpus is written:**

| # | Blocker | What is needed |
|---|---|---|
| B1 | The corpus must be **generated from `REGISTRY`**, not hand-authored | A generator script. Hand-authoring is what produced the four Tier-1 YAMLs. |
| B2 | Scope statement required | Every capability claim must name its surface. "UNDX can publish a product" is true of Surface C and false of Surface B. |
| B3 | Tier-1 stale docs must be corrected **first** | Four YAMLs, `docs/undx_manual.md`, `CLAUDE.md:52-58`. A corpus built while they exist will be contaminated by retrieval. |
| B4 | Surface B′ prompt text must be fixed or excluded | The "Authorized tool registry" block currently names four writes UNDX cannot perform. |
| B5 | **R7/R8 must be reconciled or excluded from retrieval** | 97 database rows marking `pulsesoc.send_message` `'active'`. A corpus generator with database access will produce confident false capability claims from structured, timestamped data. Stronger contaminant than any prose document. |
| B6 | **The scope statement must name five surfaces, not two** | Any sentence of the form "UNDX cannot X" is false unless it excludes Surface E (`/api/undx/kernel/*`), which can write files and `git push`. |

**Live-QA questions this mission could not settle from the repository:**

- Which route packs actually mount in the deployed Railway environment. Route-pack registration is
  wrapped in `try/except`; a subsystem can vanish silently in production.
- The deployed values of `BUSINESS_OS_MARKETPLACE`, `BUSINESS_OS_ADVERTISING`,
  `BUSINESS_OS_UNDX_ACTIONS`, and the seven `REQUIRED_WRITE_GUARDS`. Every conclusion about what is
  *reachable* in production depends on these; every conclusion about what is *permitted* does not.
- The production contents of `business_os_undx_tool_registry`, `_policies`, and `_permissions`. All
  empty locally. A production `allow` permission row would change the marketplace posture from
  "require_approval" to "executes".

---

## 10. METHOD NOTE

The recon README warns that one class of claim in this codebase is systematically unreliable:
**statements that something does not exist.** Payouts, comm_v2 DDL, and the UNDX tool gateway were
each documented as absent and each turned out to be present.

**This mission produced a fourth instance.** The two-registry conflict was framed as complete because
the search had been scoped to the UNDX modules — and the marketplace assistant, the advertising
assistant, and the Business OS governance engine all sit outside them, under `services/business_os/`,
carrying money-moving verbs. The finding surfaced only by following `create_confirmation` outward
from an unrelated line in `pulse_ai_service.py`.

The generalisation is not "search harder." It is: **in this repository, a subsystem's authority is
bounded by its imports, not by its directory.** The reliable question is never "what does
`undx_*` contain?" but "who calls the confirmation and execution primitives?" That question found
six registries in three greps. The directory-scoped question found two, twice.

### And then it produced a fifth

The paragraphs above were written, in this document, and then immediately falsified by this
document. Having just diagnosed the absence-claim failure mode and prescribed the cure, the first
draft asserted *"there are six registries across four surfaces"* — an absence claim about registries,
stated in the section explaining why absence claims here are unreliable. R7/R8 and Surface E were
both missed. So was the simulator route.

That is the more useful finding than any individual correction. **Knowing the failure mode does not
prevent it.** The confidence that follows a thorough search feels identical to the confidence that
follows a complete one, and no amount of having written down the lesson changes how it feels from
the inside.

What actually caught it was structural, not cognitive: a subagent briefed to **find errors** rather
than to check work, given the document and told the conclusion was probably wrong somewhere. It
returned 12 defects. Ten survived independent re-verification against the code; two were themselves
wrong and are noted as such. Two of the ten were critical.

### Then a second adversarial pass, on the corrections themselves

The corrections were not trusted either. A second pass verified the nine *new* claims introduced by
the first round of fixes. It found three more defects — in text written specifically to fix earlier
defects:

- `pulse_communications_v2/` was cited as `services/pulse_communications_v2/`. It is a root-level
  package.
- The kernel routes were described as decorator-gated. The gate is an in-body call.
- "97 matches neither 87 nor 103" was true but shallow. The DB is a **strict subset** of R2 missing
  exactly six crypto tools — directional staleness, which reads as a considered number and is
  therefore harder to catch than a contradiction would be.

It also surfaced two things nobody asked about: the desktop-connector proxy at `bot.py:28893`
(reaching `git/push` off-repo), and the stale `_enforce_permission_scope` docstring at `:419-425`
that undersells its own enforcement. That second one is worth sitting with: **the stale comment is
what produced the §6.2 error.** The docstring says other-user scopes "are refused rather than
executed"; the code beneath it executes them after a database check. Reading the comment and
sampling the code was enough to get it backwards.

**Error rate on corrections was roughly the same as on the original text.** There is no round after
which the document becomes reliable by virtue of having been checked.

The corrections are applied **in place, visibly marked**, never silently overwritten — so a reader
can see which claims have already failed. A twice-corrected passage is the *least* trustworthy in
the document, not the most.

**Recommended as policy, not as advice:**

1. No absence claim about this codebase reaches a document without an adversarial pass by a reader
   whose task is to break it. Self-review found none of the four missed registries.
2. **Corrections get the same treatment as originals.** They are written under time pressure, with
   the relief of having found the bug, and they fail at a similar rate.
3. Never trust a docstring about enforcement. Read to every `return` and every terminal `raise`.
   Both §6.2 defects — the original error and its persistence through self-review — came from
   trusting prose adjacent to code.

Every absence claim in this document was derived by asking "who calls the primitives?" — and one
class of them still failed, twice. They should be re-derived before anyone relies on them.
