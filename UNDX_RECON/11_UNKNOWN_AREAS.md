# DELIVERABLE 10 — UNKNOWN AREAS REQUIRING MORE INVESTIGATION

Consolidated from all thirteen stages. Every item here is something the recon **could not
settle from source alone**. Nothing below is a guess about what the answer might be.

Ordered by how badly it would damage a training corpus to get it wrong.

---

## TIER 0 — CLOSED DURING THE VERIFICATION PASS

Kept, not deleted, because both were listed as blocking and someone will otherwise re-open
them.

### 0.1 Which UNDX tool surface is live — **CLOSED, then RE-OPENED, then closed correctly**

This question was answered wrongly once. The wrong answer is preserved because it shows the
failure mode.

**First answer (WRONG):** "Neither. `/api/undx/chat` executes no tools, and
`PRODUCTION_TOOL_REGISTRY` is dead code reachable from no route."

**Correct answer:** there are **two** surfaces, and only the first is toolless.

`/api/undx/chat` (`bot.py:28795`) → `undx_openai_response` (`bot.py:28772`) →
`undx_router.route_undx_request()` with `UNDX_SYSTEM_PROMPT`. Text in, text out, no tool
execution, gated by `require_super_user_api()`. **That part was right.**

The agent surface is elsewhere — in the comm_v2 blueprint, not `bot.py`:

```
pulse_communications_v2/routes.py:629   POST /api/pulse-ai/message
  → routes.py:638   pulse_ai_service.send_message(...)
  → services/pulse_ai_service.py:726   undx_agent_runtime.handle(..., confirmation_token=...)
  → services/undx_tool_gateway.execute

pulse_communications_v2/routes.py:811   POST /api/pulse-ai/actions/confirm
  → pulse_ai_service.py:1454-1459   undx_tool_gateway.execute(...)   ← direct mutating execution
```

Registered at `bot.py:1247` via `_load_route_pack("pulse_communications_v2", ...)`. Thirteen
`/api/pulse-ai/*` routes total. `undx_worker.py:19,88` is a second driver (`undx_mission_runtime.poll_once()`).
`bot.py`'s only registry reference is `/health/undx` (`bot.py:115256`), introspection only.

**Why the first answer was wrong — the lesson, not the fact.** The search was scoped to
`bot.py` because `bot.py` is where routes live. It is where **1,713 of 2,006** routes live,
which is exactly enough for the heuristic to feel safe and still be wrong. The remaining ~293
routes are in blueprints and `add_url_rule` calls, and the single most consequential finding in
this entire recon was hiding in one of them. **Any claim of the form "no route does X" must be
tested against the full route inventory in `03_API_MAP.md`, never against `bot.py` alone.**

**Consequence for the corpus:** the "UNDX drafts but never sends" boundary is **NOT
established** and must not be assumed. `POST /api/pulse-ai/actions/confirm` calls
`undx_tool_gateway.execute` directly on a mutating path. The governed runtime's own safeguards
(confirmation tokens burned before execution, ambiguity refusal, read-back verification) are
what bound UNDX's behaviour — not the absence of a wire. Those safeguards are real and tested,
but they now carry the whole load, and their production configuration is unverified. See
Tier 1.

### 0.2 ~~The agent flags exist only in the test harness~~ — **CLOSED, evidence was wrong**

`grep -c 'UNDX_AGENT' .env.example` does return 0, but the flags are **not** confined to
`tests/undx_agent/harness.py`. They are a declared environment contract in
`services/undx_brain/config.py:644-654` — `UNDX_AGENT_ENABLED`, `_READS_ENABLED`,
`_WRITES_ENABLED`, `_DISABLE_WRITES`, `_QA_USER_IDS` and others, each with `required: True`,
defaults, consumers, and rollout stages — and they are read at `bot.py:115356+`.

The **conclusion** survives: defaults are off, `user_enabled()` requires explicit cohort
membership ("Empty means nobody, never everybody"), so the runtime is dark unless set on
Railway. But the stated evidence was false, and the file that disproved it was sitting inside
`services/undx_brain/`, which this recon had listed as "unexplored" one tier below.

---

## TIER 1 — BLOCKING. Do not build a corpus until these are closed.

### 1.1 Which registry governs a given `/api/pulse-ai/*` call — **THE top blocking question**

> This item replaces a previous 1.1 that asked "which endpoint, if any, mounts the gateway."
> That question is **closed** — the gateway is mounted, on 13 `/api/pulse-ai/*` routes; see
> §0.1. The question that replaces it is worse, not better.

**Two tool registries are reachable in production and they disagree about what UNDX may do.**

| Registry | Entries | Exposes writes like send-message / create-post / create-reel? |
|---|---|---|
| `services/undx_capability_registry.py` REGISTRY | 87 | **No** — deliberately withheld |
| `undx_policy.PRODUCTION_TOOL_REGISTRY` | 103 at runtime | **Yes** — `pulsesoc.send_message`, `pulsesoc.create_post`, `pulsesoc.create_reel`, `media.upload` |

The 103 figure is a runtime measurement: the dict literal at `undx_policy.py:41` holds 50 keys
and packs merge the remainder at import, so static reading of that file undercounts by half.

This recon documented the 87-capability registry in detail (`08_UNDX_CAPABILITY_MAP.md` §§3–6)
on the assumption it was the governing surface. That assumption is **not established**. If
`PRODUCTION_TOOL_REGISTRY` governs any live path, then every "UNDX cannot do X" statement
derived from the 87-capability list is potentially wrong in the most dangerous direction — a
corpus would teach UNDX that it lacks powers it actually has.

**How to close it:** trace `services/undx_tool_gateway.execute` and
`services/undx_agent_runtime.handle` to see which registry each consults, whether both are
consulted, and what happens when they disagree. Start from
`services/pulse_ai_service.py:690-756` (`_agent_turn`) and `:1413-1470` (`_agent_confirm`).

### 1.2 Whether the agent runtime's enforcement is configured in production

Substance unchanged from §0.2, but **the stakes have risen**: this was a question about whether
a dark feature might light up. It is now a question about whether a **live, mutating** surface
has its safety configuration set. `UNDX_AGENT_ENABLED`, `_READS_ENABLED`, `_WRITES_ENABLED`,
`_DISABLE_WRITES`, `_QA_USER_IDS` are declared `required: True` at
`services/undx_brain/config.py:644-654`, read at `bot.py:115356+`, and absent from
`.env.example`.

Read the live Railway variables for the `web` service.
`scripts/undx_railway_variable_audit.py` (untracked in the working tree) appears to have been
written for exactly this.

### 1.3 `undx_training_v6_source_corpus.yaml` — 1.43 MB, unread

`backend/undx/config/` holds six generations of corpus:
`undx_intelligence_bootstrap.yaml` (35 KB), `_v2` (37 KB), `_v3` (35 KB),
`undx_training_v4_nexus_core.yaml` (37 KB), `undx_training_v5_pulsesoc_operator.yaml` (35 KB),
and `undx_training_v6_source_corpus.yaml` (**1,429,115 bytes**).

The v6 file has not been read. It may already assert capabilities, permissions, or company
facts that contradict the registry as it stands today. Writing a v7 without diffing against
v6 would produce a corpus that fights its predecessor rather than replacing it — and which of
the six is actually loaded at runtime is itself unestablished.

**How to close it:** determine which YAML the loader reads, then diff v6's claims against
`REGISTRY` and `RECORDS`. Expect the reconciliation, not the writing, to be the bulk of the
corpus work.

---

## TIER 2 — SIGNIFICANT. Unexplored surface area that likely changes conclusions.

### 2.1 `services/undx_brain/` — 1.6 MB across 21 modules, entirely unexplored

Large enough to contain a third answer-routing path independent of both registries in §1.1.
Until it is read, no statement about "how UNDX decides what to do" is complete.

### 2.2 `undx_agent_runtime.py` (3,383 lines) and `undx_response_intelligence.py`

Read only in outline. These sit between the policy layer and the response, and are where a
divergence between "what policy allowed" and "what the user was told" would live.

### 2.3 ~~The 23 db-only tables, especially `comm_v2_*`~~ — **RETRACTED**

This entry originally claimed that twenty `comm_v2_*` tables holding 1,411 real messages had
**no `CREATE TABLE` DDL anywhere in the repository**, and `04_DATABASE_KNOWLEDGE_MAP.md` drew
the conclusion that "a rebuild from source would not recreate the messaging engine."

**That was false.** All nineteen `comm_v2_*` tables except `comm_v2_pinned_messages` (0 rows)
are declared in `pulse_communications_v2/models.py` (`COMM_V2_TABLES`, executed by
`ensure_schema()` at `:410`, called from `service.py:214`). The database extraction pass
simply never scanned the `pulse_communications_v2/` package — the API pass did.

Corrected figures: **835** code-declared table names (not 813), **65** code-only (not 57),
**5** db-only (not 23): `comm_v2_pinned_messages`, `business_os_confirmation_grants`, and
`seller_application_assignments` / `_notes` / `_status_history`. Live table count is **775**;
the earlier 776 included `sqlite_sequence`.

**The real remaining unknown is much smaller:** why those five tables exist without DDL, and
in particular why three `seller_application_*` tables do.

**Method lesson worth keeping:** a per-directory extraction that misses one package produces a
confident, specific, and completely wrong architectural conclusion. The claim was not vague —
it named a row count. Precision is not evidence of correctness.

### 2.4 Zero foreign keys, zero views, zero triggers — intent unknown

Verified directly against `coinpilotx.db`: 775 tables, 1,072 indexes, **0 foreign keys**,
0 views, 0 triggers. Whether PostgreSQL production carries constraints that SQLite local does
not is unverified. If prod also has none, every referential guarantee in the product is
enforced in Python, and roughly **263 of 459** ownership-bearing tables having no index led by
their ownership column becomes a performance question as well as a correctness one. (An
earlier pass reported 237 of 467; the two passes used different ownership-column lists and
neither published its list. Publishing the list is a prerequisite to trusting either number.)

### 2.5 Sentinel — 46 docs, 55 modules, 22 tables, never registered

A complete security-intelligence subsystem that is deliberately not mounted. Whether it is
abandoned, pre-launch, or mounted by a separate deployment is not established. A corpus should
not mention it either way until this is settled.

---

## TIER 3 — RUNTIME QUESTIONS THAT SOURCE CANNOT ANSWER

These are not gaps in the recon; they are categorically outside what reading code can tell you.

- **Which optional route packs actually mounted.** Route packs register inside
  `except Exception` blocks, so a subsystem can vanish in production without any 500. The only
  evidence is boot logs. Static route presence in `03_API_MAP.md` (2,007 registrations) is a
  ceiling, not a fact about production.
- **Provider health.** OpenAI, Claude, Gemini, DeepSeek, Groq, Stripe, Agora, Mux, Brevo, R2,
  FCM/APNs, Google Translation, CoinGecko — key presence is not key validity.
- **App Store / TestFlight state.** `PULSESOC_STOREKIT_STRIPE_UNIFIED_PAYMENTS_FINAL_REPORT.md`
  is marked **PARTIAL** with owner-only App Store Connect items outstanding.
- **Physical-device behaviour** for livestream, push, checkout, and uploads. `CLAUDE.md` is
  explicit that static checks do not replace device QA for these four.
- **Real-time audio in production.** The protection gate proves *changes* are policed. It
  proves nothing about whether a live call currently holds the audio session correctly.
- **Which of the six UNDX YAML corpora is loaded**, and whether the loader picks by filename,
  config, or env.

---

## TIER 4 — RESOLVED CONTRADICTIONS, RECORDED SO THEY DO NOT REOPEN

These were genuinely unknown mid-recon and are now settled. Kept here because both sides are
still written down somewhere in the repo, and someone will hit the losing side again.

| Claim | Verdict | Evidence |
|---|---|---|
| "No endpoint initiates a payout anywhere in the codebase" (`FLAG_REGISTRY.md`) | **FALSE — historical.** A payout path exists end-to-end **and is live** | `sellerPayouts.ts` → `bot.py:19955` `api_pulse_seller_payouts` → `services.business_os.payments.seller_payouts` (`request_payout` / `get_payout` / `list_payouts` / `seller_balance_summary`) → `pulse_submit_seller_payout` → Stripe webhook |
| …and payouts are gated off by `EXPO_PUBLIC_PAYMENTS_PAYOUT_INITIATION` (**this recon's own first correction**) | **ALSO FALSE.** That flag was **retired**; `paymentsHub.ts:198` is `payoutInitiationIsLive() { return true; }` | `envFlag.test.ts:207-209` records the retirement. Nothing is gating payouts |
| `readiness.ts` holds 24 `EXPO_PUBLIC_*` flags | **FALSE.** It holds **zero**; it is a 150-line, four-row deny-list | direct read; real flag count is **20**, pinned at `envFlag.test.ts:210` |
| Twenty `comm_v2_*` tables have no DDL in the repo | **FALSE.** 18 of 19 are declared in `pulse_communications_v2/models.py` | `COMM_V2_TABLES`, `ensure_schema()` `:410`, called from `service.py:214` |
| 776 tables / 813 code-declared / 57 code-only / 23 db-only | **FALSE. 775 / 835 / 65 / 5** | 776 counted `sqlite_sequence` |
| Registry has 83 capabilities | **FALSE, undercount.** Live `len(REGISTRY)` is **87** | 70 read_only / 13 reversible_write / 4 consequential_write; 75 never / 7 contextual / 5 always confirm — exact by import |
| `bot.py` has 1,715 routes | **1,713.** The grep counts two `ai_router_service.route(...)` calls at `:28650`, `:28705` | AST count |
| `03_API_MAP.md` totals 2,007 routes | **Internally inconsistent** — the source table sums to **2,006**, the area table to 2,007. Also `commerce_gateway.ROUTES` is **37**, not 36, and the `undx_execution_kernel.py` "1 route" is inside a string literal at `:617` | independent recount |
| The desktop connector is "proxied from the public Flask app" | **Overstated.** It binds `127.0.0.1` only (`:1167`) and the `bot.py:28893` proxy requires `undx_kernel_user()` → `require_super_user_api()` plus a path allowlist | direct read |
| `AUTO_PK_TABLES` is in `bot.py` with ~170 entries (`CLAUDE.md`) | **FALSE.** `services/db.py:143`, **354 entries** | direct read |
| `.env.example` has ~180 keys (`CLAUDE.md`) | **FALSE. 533 keys** | `grep -cE '^[A-Z0-9_]+='` |
| `services/` has 239 modules (`CLAUDE.md`) | **FALSE. 285** | `ls services/*.py \| wc -l` |
| `alert_worker` is not in the Procfile (`CLAUDE.md`) | **FALSE.** Procfile is `web`, `undx_worker`, `email_worker`, `ads_worker`, `alert_worker` | direct read |
| Branch is `codex/emergency-live-audio-recovery` (`CLAUDE.md`) | **FALSE.** `codex/premium-crypto-intelligence` | `git rev-parse --abbrev-ref HEAD` |
| LiveKit is the RTC provider (`CLAUDE.md`) | **Retired on native**; Agora is sole RTC. Zero `LIVEKIT_*` in `.env.example`, but residual LiveKit `publish_state` strings persist in the **web** live path | Stage 0 + verification |
| No APScheduler (`CLAUDE.md` implies it is used) | **No first-party import** — but it *is* still pinned in `requirements.txt:2` | grep |
| The Flask double-bind is at `bot.py:384` / `:1130` and discards config (`CLAUDE.md`) | **Wrong on both counts.** It is at `:429` / `:1181`, and nothing is lost | direct read |
| `bot.py` is ~111k lines (`CLAUDE.md`) | **117,902 lines** | `wc -l` |

**`CLAUDE.md` is stale in at least eleven verifiable places.** It should not be used as a
source for the corpus. It is also the file most likely to be read first by the next person or
agent to touch this repo — updating it is the highest-leverage cleanup available, and is
explicitly *not* something this recon did, since the mission forbade changing anything.

---

## TIER 5 — ONE FINDING THAT IS NOT AN UNKNOWN, BUT SHOULD NOT BE FILED ANYWHERE ELSE

`undx_desktop_connector.py` (1,171 lines) exposes repository write and `git push` over a
localhost Flask app on `UNDX_DESKTOP_CONNECTOR_PORT` (default 8765) with:

- **no authentication inside the connector itself** — the only gate there is the hardcoded,
  source-visible approval phrases `APPROVE UNDX WRITE` (`:41`) and `APPROVE UNDX GUARD CHANGE`;
- `Access-Control-Allow-Private-Network: true` set at `:177`.

The path protections (`.env`, `.git/`, `venv/`, `secret`, `.sqlite`, `.github/workflows/`) are
real and correctly written, but they constrain *what* can be written, not *who* may write.

**Severity corrected downward during verification.** An earlier draft said it was "proxied
from the public Flask app," implying unauthenticated reachability. That is wrong: the connector
binds `127.0.0.1` only (`:1167`), and the `bot.py:28893` proxy requires `undx_kernel_user()` →
`require_super_user_api()` plus a path allowlist. Two real gates stand in front of it.

What remains true is that the connector's own authorisation is a string constant visible in
source, so anything that reaches it locally — another process on the developer's machine, a
browser page exploiting the private-network CORS header — meets no further check. That is a
defence-in-depth observation, not an open door.

Stated as a finding, not a recommendation. No change was made; the mission prohibited touching
production code. It is recorded here because it is the sharpest edge in the repository and it
belongs somewhere a person will read.

---

*End of Deliverable 10.*
