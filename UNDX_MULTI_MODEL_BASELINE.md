# UNDX multi-model baseline — 2026-09-11

Source: `5a44fe82552235e7bee6240ebef409c563475730`.
Implementation worktree: `/private/tmp/undx-multi-model-fabric`.
Branch: `codex/undx-multi-model-fabric`. Local commits only; no push/deploy.
The shared main checkout has unrelated native Marketplace/localization edits;
those files are excluded from this worktree and will not be changed.

## Canonical request and authority

1. `pulse_communications_v2/routes.py` owns authenticated
   `POST /api/pulse-ai/message` and delegates to `pulse_ai_service.send_message`.
2. `pulse_ai_service._agent_turn` sends the original user utterance (not retrieved
   text/history) to the server-cohort-gated `undx_agent_runtime.handle`.
3. `undx_agent_runtime`, Brain policy/selection, and
   `undx_capability_registry` resolve known capabilities. `undx_agent_contracts`
   validates typed arguments. The registry owns risk, permissions, confirmation,
   executors and canonical readback verifiers; model text owns none of these.
4. `undx_tool_gateway.execute` is the governed mutation boundary. It enforces
   ownership/permissions, confirmation and idempotency, and distinguishes verified
   success from accepted/unverified writes. Model fallback must never retry a tool.
5. Existing agent run storage/worker and `undx_mission_runtime` own durable claims,
   leases, cancellation/recovery and bounded progression. `undx_architecture`
   persists plans in `pulse_ai_missions` / `pulse_ai_task_nodes`. Do not add another
   workflow, memory, product, conversation or tool ledger.
6. Handled actions return native receipt cards without LLM paraphrasing. Unhandled
   conversation builds policy/authorized knowledge/memory, then calls
   `pulse_ai_provider_router.generate_response`. The provider boundary injects
   UNDX/company/capability/fact grounding. The service subsequently strips
   unsupported execution claims before persistence. Both checks must remain.
7. `pulse_ai_provider_events` and existing architecture/agent audit stores provide
   telemetry. Native action-card contracts remain unchanged.

## Provider inventory (source, not deployed-account evidence)

| Provider | Current source support | Present limitation |
| --- | --- | --- |
| OpenAI | Chat Completions in shared fallback router | Text-only result; usage discarded |
| Anthropic | Messages in shared router | No role/privacy/budget admission |
| Gemini | generateContent in shared router | No role/privacy/budget admission |
| DeepSeek / Groq | Compatible chat in shared router | Key presence enables legacy fallback |
| UNDX candidate | Explicit opt-in compatible endpoint | Separate self-hosted candidate |
| Meta Muse | No chat adapter or flags consumed in baseline | Reported deployed flags are not source/live proof |
| Perplexity | Existing embeddings only | Embeddings are not Sonar research |

`pulse_ai_web_search` is an existing separate search adapter/cache (Brave, Bing,
SerpAPI, Tavily, DuckDuckGo); it is not the model planner. Preserve embeddings and
canonical retrieval. Direct OpenAI calls also exist in `intelligence`,
`scam_shield`, `telegram_text_router` and the image pipeline; these are separately
owned surfaces, not silently brought into a new enablement cohort.

## Integration decision

Extend the canonical provider boundary with an opt-in model-fabric path, using
server-owned request context. Default/off retains current conversational behavior.
An enabled fabric must fail closed on missing identity/privacy/budget context,
not escape to the legacy fallback pool. Add typed role/proposal/evidence contracts,
explicit provider/model eligibility, bounded usage admission and circuits.
Model proposals are inert; no new executor or model-directed worker is authorized.
Reuse canonical registry validators and existing durable runtime for later plan
admission. No automatic Private Office export; no client privacy downgrade.

## Evidence limits / acceptance sequence

Baseline inspection is static, not deployed evidence. Railway flags, exact active
Muse model, provider entitlement, current prices, account retention policies and
live quality are unverified. No credentials were read or printed. Provider wire
contracts must be verified against official docs before implementation. Offline
contracts/adversarial tests precede replay/shadow/canary. Production activation,
paid comparative benchmarks and durable PostgreSQL/distributed acceptance must
not be represented as passed by fixture tests. No mobile build or RTC changes.
