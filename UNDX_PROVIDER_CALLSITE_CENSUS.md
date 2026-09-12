# UNDX AI provider call-site census

Taken 2026-09-12 against `claude/adoring-gates-fdbf0a` @ `d89b6330`, before any code
was modified. §1 of the consolidation brief requires the census first and warns against
trusting the previous count of nine as permanently complete. It was not complete, in both
directions: the detector misses one chat call and five paid research calls, and two of the
three non-chat findings it reports are not call sites at all.

## How the search was conducted

A hostname grep is the obvious method and it is not sufficient, because a URL can be
composed at runtime from configuration. Seven independent nets were run, and each one
found something the previous one had not:

| Net | Result |
|---|---|
| Provider hostname literals | 36 hits, 11 outside tests |
| Provider SDK imports (`openai`, `anthropic`, `google.generativeai`, `groq`, …) | **zero** — every call in this repo is hand-rolled HTTP |
| SDK client construction (`OpenAI(`, `Anthropic(`, `GenerativeModel(`, …) | **zero** |
| Endpoint path fragments (`chat/completions`, `:generateContent`, `embeddings`, …) | found `pulse_ai_provider_router.py:258`, a URL composed from an env base |
| **Provider API-key env reads, repo-wide** | the decisive net — a provider call cannot happen without a credential |
| Non-`requests` HTTP clients (`urllib`, `httpx`, `http.client`, `aiohttp`) | the image generator uses `urllib.request`; a `requests.post` grep never sees it |
| Capability keywords (transcription, rerank, moderation, search) | found five paid **search** providers nobody was counting as AI spend |

The key-env-read net is the one worth keeping. Fifteen files read a provider key; six of
them do so only for health-dashboard presence checks, and two files that
looked like call sites by name (`services/ai_router.py`, `services/ai_story_service.py`)
make no outbound request at all. Conversely it is the only net that would survive somebody
moving a URL into configuration.

## 1. GOVERNED_CHAT — reaches a provider through `undx_router`

These are the legitimate adapters. They are the intended bottom of the chain, not
violations, and Phase 9's allowlist should name exactly this set.

| Site | Provider | Notes |
|---|---|---|
| `undx_router.py:1118` via `_openai_compatible` | openai / deepseek / groq / meta | one shared adapter, four endpoints |
| `undx_router.py:1195` `_call_perplexity` | perplexity | |
| `undx_router.py:1243` `_call_claude` | claude | |
| `undx_router.py:1269` `_call_gemini` | gemini | |
| `bot.py:29650` `undx_openai_response` | *(routed)* | calls `undx_router.route_undx_request` |

**Finding G1 — a governed call that misreports who answered it.**
`bot.py:29650` is correctly routed, but it is named `undx_openai_response`, it logs
`"OpenAI API key configured"`, and both of its error branches hardcode `"source": "OpenAI"`.
Its caller `api_undx_chat` then writes `result.get("source", "OpenAI")` into command
history at `bot.py:29697`, into the error record at `:29710`, and into a product-analytics
event at `:29716`. The router may have answered from any of seven providers. This is not a routing bypass — no control is skipped — but every
attribution it writes is unreliable, which makes the command-history table useless as
evidence of which provider served a user. Cosmetic in mechanism, substantive in
consequence: it is exactly the sort of record an incident review would trust.

## 2. UNROUTED_CHAT — reaches a provider directly, bypassing every control

Seven distinct call expressions across five modules, carrying ten URL literals, as found.
Each one is outside the cost ledger, the circuit breaker, provider health, and the privacy
ceilings.

**Six remain.** The census is kept as found and annotated with status, rather than shrunk
as sites are migrated: a table that only lists what is still broken cannot answer "was this
ever a direct call, and when did it stop being one", which is the question an incident
review asks.

| # | Call expression | Function | URL literals | Privacy | Domain | Status |
|---|---|---|---|---|---|---|
| U1 | ~~`bot.py:108625`~~ | `sports_edge_ai_analysis` | — | PUBLIC | **TELEGRAM** | **MIGRATED** |
| U2 | `services/intelligence.py:50` | `assistant_response` | 51 | CONFIDENTIAL | GENERAL | pending |
| U3 | `services/scam_shield.py:184` | `_openai_assessment` | 185 | CONFIDENTIAL | SCAM_SHIELD | pending |
| U4 | `services/telegram_text_router.py:121` | `answer_telegram_with_openai` | 122 | CONFIDENTIAL | TELEGRAM | pending |
| U5 | `services/pulse_ai_provider_router.py:264` | `_post_openai_compatible` | 251, 253, **258**, 260 | CONFIDENTIAL | GENERAL | pending |
| U6 | `services/pulse_ai_provider_router.py:282` | `_post_anthropic` | 283 | CONFIDENTIAL | GENERAL | pending |
| U7 | `services/pulse_ai_provider_router.py:312` | `_post_gemini` | 313 | CONFIDENTIAL | GENERAL | pending |

Privacy classes are assigned by what the prompt actually carries, not by what would be
convenient to route (§4). U1 is PUBLIC because the payload is public scoreboard data and a
generated base read; the `user_id` is used only to gate on `is_pro` and is never sent. The
other four carry free-text the user wrote, so CONFIDENTIAL is the floor.

**Correction to this table, recorded rather than quietly overwritten — U1's domain was
wrong when first written.** It said GENERAL, assigned by reading the function: the prompt
carries a scoreboard feed and a generated read, so nothing in the text looks like it came
from Telegram. Tracing the callers instead showed both are Telegram command handlers
(`bot.py:122611`, `bot.py:122738`). A domain is a fact about *provenance*, not a summary of
today's payload (`services/undx_call_domain.py` says so at length), and the difference is
not academic: a content-derived label is a claim the next edit to the prompt invalidates
silently, while a caller-derived one stays true. The mistake was safe here only because a
domain cannot widen anything — §5's rule is what made a wrong label cheap.

U1's migration is covered by `tests/test_sports_edge_routing.py` (21 tests) and each of its
protections is proven to fail under mutation by
`scripts/undx_sports_edge_mutation_check.py` (19 mutations, 2 of which must stay green).
That test file is deliberately the template for U2-U7: declared privacy class, declared
call domain, both as named constants rather than literals, preserved sampling parameters,
preserved safety post-processing, and a failure that costs a paragraph rather than a reply.

**Finding U-a — the detector misses `pulse_ai_provider_router.py:258`.**
`scripts/undx_config_drift.py` reports nine unrouted chat calls and lines 251, 253, 260,
283 and 313 in this file. It does not report 258:

```python
base = _candidate_base_url()          # UNDX_CANDIDATE_BASE_URL
url = f"{base}/chat/completions"
```

This is the `UNDX_CANDIDATE` self-hosted provider. It is off by default and requires both
`UNDX_CANDIDATE_ENABLED=true` and a base URL, which is good hygiene — but it is the one
chat endpoint in the repo that an operator can point anywhere with an environment
variable, and it is the one the URL-literal detector cannot see. A hostname-based detector
is structurally blind to exactly the call site that most needs watching. **The true count
is ten URL literals, not nine.**

**Finding U-b — `services/intelligence.py` has five callers, so it is one change point
worth five.** `assistant_response` is called from `bot.py:30508`, `bot.py:118804`,
`services/ai_service.py:6`, `services/ai_router.py:84`, and
`services/command_router.py:195`. Migrating inside the function governs all five; migrating
at the call sites would be five edits and five chances to miss one.

**Finding U-c — `scam_shield.py` depends on an OpenAI-only feature.**
`_openai_assessment` sends `"response_format": {"type": "json_object"}` and then
`json.loads` the reply. Routing it to a provider without a JSON mode would not fail loudly;
it would return prose, `json.loads` would raise, and the `except` would swallow it into
`{"error": ...}` — a security control silently degrading to "unavailable". §3 requires the
response schema be preserved, so this call needs a structured-output *capability
requirement*, not just a provider. This is a genuine design constraint, not a detail.

**Finding U-d — two routers disagree about which model to use.**
`services/pulse_ai_provider_router.py` maintains its own five-provider table with its own
defaults, in its own env namespace, and they do not match `undx_router.PROVIDERS`:

| Provider | `undx_router` | `pulse_ai_provider_router` |
|---|---|---|
| claude | `claude-haiku-4-5` | `claude-3-5-haiku-latest` |
| gemini | `gemini-flash-lite-latest` | `gemini-1.5-flash` |
| openai | `gpt-4o-mini` | `gpt-4o-mini` |
| deepseek | `deepseek-chat` | `deepseek-chat` |
| groq | `llama-3.1-8b-instant` | `llama-3.1-8b-instant` |

Which model serves a user depends on which caller they happen to reach. It also runs its
own task-preference ordering (`_task_preference`) that is unrelated to the router's routing
policy, its own timeout (`PULSE_AI_PROVIDER_TIMEOUT_SECONDS`), and its own fallback loop.
This is §13's "two routers" in the concrete.

**Finding U-e — the two routers harden identity in different, non-overlapping ways.**
My first reading of this was wrong and is worth recording, because the correction changes
what reconciliation means. `undx_router` is *not* missing identity handling: it has
`IDENTITY_DIRECTIVE` (`:775`) applied through `_system_prompt` (`:787`). But the two
modules are solving different problems.

| | `undx_router` | `pulse_ai_provider_router` |
|---|---|---|
| Goal | provider **anonymity** — never name the vendor, never guess one | brand **identity** — always answer to "UNDX", never "Pulse AI"/"ChatGPT" |
| Grounding blocks | 1 (identity rules) | 4 (identity, company, capability lifecycle, fact policy) |
| Input-side check | none at runtime — a unit test (`IdentityDirectiveTest`) asserts the directive reaches the wire | runtime, **fail closed**: raises `identity_configuration_error` if any required phrase is absent |
| Output-side check | none | six violation patterns, one regeneration attempt, then a fixed safe reply |

So neither is a superset. `undx_router` has the anonymity directive and all the cost,
breaker, health and privacy controls; `pulse_ai_provider_router` has the company/capability/
fact grounding and both verification seams. Consolidation has to carry the grounding and the
two verification seams *into* the router — deleting the module outright would drop four
guarantees, and wrapping it would leave the duplicate model table of finding U-d in place.
That is the substance of §13, and it is more than a compatibility shim.

Note also that the router's own docstring concedes the weaker arrangement: the directive is
applied at three call sites because Claude and Gemini use different fields, and "it is the
test rather than the structure that holds the line." A runtime check like the one
`prepare_undx_model_request` already performs would close that honestly.

**Finding U-f — `generate_task_response` is dead code held up by a vacuous audit.**
It has zero production callers. Its docstring justifies its existence by naming content
translation — and `services/content_translation.py` makes no outbound call at all and does
not import it. The only thing referencing it is
`scripts/pulsesoc_content_translation_audit.py:42`:

```python
require("generate_task_response" in provider, "translation reuses the existing provider pool")
```

The audit substring-matches the function's *name* in the file's source text. It passes
whether or not anything calls it, and it has been passing while the feature it claims to
verify has no caller. A green audit asserting a dead function exists is worse than no
audit, because it occupies the space where a real check would go.

## 3. Non-chat AI — governed by nothing, but not chat either

The router has no equivalent capability for these, so they cannot be "routed"; §20-24
require metering them without forcing them into a chat abstraction.

| Kind | Real call expression | Notes |
|---|---|---|
| NON_CHAT_IMAGE | `services/pulse_ai/automated_image_pipeline.py:167` | `urllib.request.urlopen`, `gpt-image-1`, 90 s timeout |
| NON_CHAT_EMBEDDING | `services/undx_embedding_service.py:560` | endpoint from `configured_endpoint()` |
| NON_CHAT_TRANSCRIPTION | **none exist** | see below |

**Finding N-a — two of the detector's three non-chat findings are not call sites.**
It reports `services/undx_brain/config.py:710` and `services/undx_embedding_service.py:60`.
Neither executes a request. Line 710 is a `Flag(...)` declaration inside a configuration
*catalog* that documents the endpoint default; line 60 is the `DEFAULT_ENDPOINT` module
constant. The actual embedding call is at line **560**, and the URL never appears there
because it arrives via `configured_endpoint()`. The detector reports where a URL is
*written*, which is correct for alerting and wrong for migration targeting — pointing a
fix at line 60 or 710 would edit a constant and leave the call untouched. There is **one**
embedding HTTP call site, not two.

**Finding N-b — one embedding call site, two callers.** The brief asks for "both embedding
paths" metered. The two paths are callers, not endpoints:
`services/undx_semantic_retrieval.py:430` (`embed_texts`, the live retrieval path) and
`services/undx_embedding_diagnostic.py` (the operator diagnostic). Both funnel through
`undx_embedding_service.py:560`. Metering there covers both; metering at the callers would
miss the third caller added next month.

**Finding N-c — transcription has zero members, and I am not inventing one.**
A keyword sweep for `whisper`, `audio/transcriptions`, `transcribe`, `deepgram`,
`assemblyai`, `elevenlabs`, `speech_to_text` finds only
`pulse_communications_v2/service.py:914` declaring `"speech_to_text": False`, a
knowledge-map string, and a `private_office` comment warning against presenting a summary
as a transcript. The taxonomy in §21 should still carry the category, but the census
records it empty rather than manufacturing a call site to fill the row.

## 4. RESEARCH — paid API spend nobody classified as AI

**Finding R-a — five unmetered paid search providers.**
`services/pulse_ai_web_search.py` reaches five external search APIs, four of which bill per
query:

| Provider | Call | Credential |
|---|---|---|
| Brave | `:167` | `BRAVE_SEARCH_API_KEY` |
| Bing | `:187` | `BING_SEARCH_API_KEY` / `BING_SEARCH_V7_SUBSCRIPTION_KEY` |
| SerpAPI | `:207` | `SERPAPI_API_KEY` |
| Tavily | `:226` | `TAVILY_API_KEY` |
| DuckDuckGo | `:242` | *(keyless)* |

Reached in production from `services/pulse_ai_service.py:958` on the messenger path, and
from `services/pulse_ai_router.py:39` behind `should_search`. §21 puts this under RESEARCH
and §22 says no unclassified AI spend; this is unclassified AI spend, and it is invisible to
every provider-hostname detector in the repo because none of these hosts is a model vendor.

It is not unobserved — outcomes land in `pulse_ai_web_search_logs(provider, status)`. But
event logging is not cost accounting: the table records that Tavily was called and
returned 200, never that the call cost money. The one comment in the module that mentions
budget (`:359`, "budget spent to say nothing") is about rendering, not spend. This is the
clearest instance of the mission's own aphorism — observability is not metering.

## 5. Not a call site (checked and cleared)

Recorded so the next census does not re-investigate them.

- **Presence checks only**, no outbound call: `bot.py` ×12 (health/status/dashboard rows),
  `pulse_worker.py:238,282,289`, `services/pulsesoc_reliability.py:41-46`,
  `services/system_mission_control.py:157`, `services/ai_story_service.py:35`.
- **`services/ai_router.py`** — reads `OPENAI_API_KEY` only to *label* a response
  `"OpenAI + CoinPlotXAI context"`; contains no HTTP client. The label is decided by key
  presence, so it can claim OpenAI context on a request OpenAI never saw.
- **`services/ai_story_service.py`** — `AI_STORY_IMAGE_ENDPOINT` is read in one readiness
  dict and used nowhere else in the repo. Dead configuration.
- **`services/content_translation.py`** — no outbound call; see finding U-f.
- **Non-AI `urllib` users**: `services/sentinel/runtime.py` (GitHub, OSV, NVD, CISA),
  `services/pulsesoc_notification_system.py` (APNs, FCM), `services/media_service.py` and
  `services/mux_live_service.py` (Mux), `services/agora_*` (RTC — §52 hard lock, untouched),
  `services/intelligence_collectors/base.py` (own origin).
- **`scripts/undx_semantic_live_acceptance.py`** — ADMIN_TEST_ONLY. Does make live
  embedding calls, but through `undx_embedding_service` and behind its own cost estimate
  and explicit `--confirm-spend` approval gate. Not a bypass.
- **No script makes a direct provider call.** The audit scripts read source text.

## 6. Pending bypass — a tenth chat call that has not been written yet

**Finding P-a.** `services/command_center_worker/ai_messaging.py` exposes five AI tasks
(`chat_summary`, `smart_replies`, `scam_explanation`, `translation_prepare`,
`moderation_insight`), two of them already mounted as HTTP endpoints at
`services/command_center_worker/app.py:490` and `:501`. Every one of them terminates in
`_provider_adapter` at `:261`, which is a stub: all three branches return
`_provider_unavailable_response`. No provider is ever called.

So this is not a violation today. It is the shape of the next one — a finished request
surface with an empty provider seam and its own third model namespace (`PULSE_AI_MODEL`,
`PULSE_AI_PROVIDER`). Whoever fills that stub will write the tenth direct call unless the
seam points at `undx_router` first. Cheap to do now, and the reason Phase 9's structural
gate matters more than the count it currently reports.

## Counts

| Classification | Call expressions | Note |
|---|---|---|
| GOVERNED_CHAT | 4 adapters + 1 routed caller | the intended allowlist |
| UNROUTED_CHAT | **7** | 10 URL literals; detector sees 9 |
| NON_CHAT_IMAGE | 1 | `urllib`, not `requests` |
| NON_CHAT_EMBEDDING | 1 | 2 callers, 1 endpoint |
| NON_CHAT_TRANSCRIPTION | 0 | category genuinely empty |
| RESEARCH (search) | 5 | previously uncounted as AI spend |
| ADMIN_TEST_ONLY | 1 | live acceptance script, spend-gated |
| DEAD_CODE | 1 | `generate_task_response` |
| Pending seam | 1 | command-center stub |
| UNKNOWN | **0** | every credential read is accounted for |

Net correction to the previous census: **+1** unrouted chat call (composed URL), **+5**
unmetered research calls, **−1** non-chat call site (two of three were declarations), and
one dead function whose audit passes by substring.
