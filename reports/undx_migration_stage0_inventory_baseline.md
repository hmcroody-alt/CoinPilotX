# UNDX Safe Independence Migration — Stage 0: Inventory & Baseline

Status: **Stage 0 (Inventory and Baseline)** · Traffic to self-hosted UNDX: **0%**
Prepared: 2026-07-23 · Branch: `release/undx-nexus-core-v4`

This report satisfies the `requiredReport` items that are knowable at Stage 0. It is a
point-in-time inventory; live baseline metrics must be pulled from telemetry (see §4).

---

## 1. Existing provider inventory

Five external providers are wired and routable today. **No self-hosted UNDX model
exists yet** — "UNDX" is currently an identity/branding layer applied on top of these
external providers, not an independent model.

| Provider | Kind | API key env (first match wins) | Model env | Default model |
|----------|------|--------------------------------|-----------|---------------|
| openai | openai | `OPENAI_API_KEY` | `PULSE_AI_OPENAI_MODEL` → `OPENAI_MODEL` → `PULSE_AI_MODEL` | `gpt-4o-mini` |
| claude | anthropic | `CLAUDE_AI_API`, `ANTHROPIC_API_KEY` | `PULSE_AI_CLAUDE_MODEL` | `claude-3-5-haiku-latest` |
| gemini | gemini | `GEMINI_AI_API`, `Gemini_AI_API`, `GOOGLE_AI_API_KEY` | `PULSE_AI_GEMINI_MODEL` | `gemini-1.5-flash` |
| deepseek | openai-compatible | `DEEPSEEK_AI_API`, `DEEPSEEK_API_KEY` | `PULSE_AI_DEEPSEEK_MODEL` | `deepseek-chat` |
| groq | openai-compatible | `GROQ_AI_API`, `GROQ_API_KEY` | `PULSE_AI_GROQ_MODEL` | `llama-3.1-8b-instant` |

Routing/behavior env flags:
`PULSE_AI_PROVIDER_ORDER`, `PULSE_AI_PROVIDER_TIMEOUT_SECONDS` (default 18s, clamped 2–45s),
`UNDX_ROUTER_ENABLED=false`, `UNDX_MULTI_MODEL_MODE=false`, `UNDX_DEFAULT_AI_PROVIDER=openai`.

Note: earlier grep suggested "xAI/Grok" references; on verification these are **not** a
configured provider in any router and are treated as non-existent for this migration.

---

## 2. Model call sites (where inference happens)

Routing is **fragmented across three parallel layers**. The spec calls for a single
provider-neutral gateway; today there are three.

### 2a. `services/pulse_ai_provider_router.py` (346 lines) — the real gateway
The closest thing to the spec's gateway. Provides: fallback ordering across all 5
providers, per-provider timeouts, error masking (never leaks provider errors/secrets to
users), recorded per-attempt telemetry, task-aware provider preference, and UNDX identity
enforcement (system-prompt injection + response validation + safe-reply fallback).
- Entry: `generate_response(messages, correlation_id, task)` — `services/pulse_ai_provider_router.py:262`
- Status: `provider_status()` — `services/pulse_ai_provider_router.py:123`
- **Callers:** `services/pulse_ai_service.py:807` (main PulseSOC AI path), plus audit scripts.

### 2b. `undx_router.py` (473 lines) — UNDX "Intelligence Router"
Separate provider config + council-agent provider map + health/status. Gated by
`UNDX_ROUTER_ENABLED`.
- `route_undx_request(...)` — called at `bot.py:19995`
- `council_agent_provider_plan(mission)` — called at `bot.py:20077`
- `provider_status()`, `default_provider()`, `router_enabled()`, `multi_model_mode()` — used by `undx_worker.py`

### 2c. `services/ai_router.py` (99 lines) — CoinPlotX crypto assistant
Keyword-routes to live market / scam-shield / wallet / predictions services; the
fallthrough case calls `intelligence.assistant_response` (a **direct OpenAI call**, not
through the gateway).
- `route(user_id, message, pro, ...)` — `services/ai_router.py:26`
- **Callers:** `bot.py:19869`, `bot.py:19924`

**Direct-provider bypass:** `services/intelligence.py` (`assistant_response`) reaches OpenAI
directly, bypassing the gateway. This is a Stage 0 finding — the spec requires all AI
requests to flow through one gateway.

---

## 3. Provider-neutral gateway design (current vs. target)

**Current:** `pulse_ai_provider_router` implements most gateway responsibilities from the
spec: provider selection, auth, timeouts, retries, fallbacks, error masking, identity/safety
enforcement, and attempt accounting. Missing from it: circuit breaking, streaming
normalization, structured-output validation, cohort routing, and cost accounting.

**Target:** one gateway that `ai_router`, `undx_router`, and the `intelligence` direct call
all delegate to, with the self-hosted UNDX candidate registered as one more selectable
provider behind config.

---

## 4. Baseline metrics — TO BE CAPTURED (not fabricated)

Baselines (quality, p95 latency, TTFT, error rate, throughput, cost) are **not measured in
this report** — they require pulling production telemetry. The backend registry references an
`ai_usage_logs` table (`services/backend_management_registry.py`) as the intended source.
Action item: extract per-provider latency/error/cost from `ai_usage_logs` over a
representative window before any candidate comparison (Stage 1).

---

## 5. Gaps blocking migration start

1. **No self-hosted UNDX candidate model** — the migration's premise. Nothing to route to yet.
2. **Three parallel routers**, not one gateway (§2). Consolidation needed for consistent
   safety/rollout control.
3. **Direct OpenAI bypass** in `services/intelligence.py` sidesteps the gateway.
4. **No rollout controls** the spec's rollback section requires: no cohort-percentage
   routing, no per-feature/per-model kill switch (only the coarse `UNDX_ROUTER_ENABLED`),
   no conversation pinning to a model version.
5. **No shadow-mode path** — no way to send a copy of eligible traffic to a candidate while
   showing users the production response.
6. **Baseline metrics uncaptured** (§4).

---

## 6. Recommendation for next rollout stage

Remain at **Stage 0**. Do not advance to Stage 1 (offline eval) until a self-hosted UNDX
candidate exists to evaluate. Recommended near-term, zero-user-impact increments:

1. Register the UNDX candidate as an off-by-default 6th provider in
   `pulse_ai_provider_router` (`UNDX_CANDIDATE_ENABLED=false`), so later stages are wireable
   without further user-facing change. **(In progress alongside this report.)**
2. Capture baseline metrics from `ai_usage_logs` (§4).
3. Route the `services/intelligence.py` direct OpenAI call and `ai_router` fallthrough
   through the gateway to close the bypass (§2c, gap #3).

---

## 7. Explicit statement on current APIs

**All current provider integrations remain fully intact.** No provider integration, API key,
routing path, or fallback has been removed, disabled, or revoked. All five external providers
continue to serve production traffic exactly as before. Rollback to the current state
requires no code change.
