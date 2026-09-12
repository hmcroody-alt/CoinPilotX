# UNDX — Railway provider configuration

Variable names, status and purpose. **No values.** Statuses were read from the
`CoinPilotX` service in the `production` environment on 2026-09-11 by listing
keys only; secret values were never printed.

Reproduce the inventory without exposing anything:

```bash
railway variables --service CoinPilotX --json \
  | python3 -c "import json,sys; [print(k) for k in sorted(json.load(sys.stdin))]"
```

## Credentials

| Variable | Status | Provider | Notes |
|---|---|---|---|
| `OPENAI_API_KEY` | PRESENT | OpenAI | Verified live. |
| `CLAUDE_AI_API` | PRESENT | Claude | Verified live. The earlier 404 was a retired model ID, not the credential — see "Resolved". |
| `META_MODEL_API_KEY` | PRESENT | Meta Muse | Verified live. Not `MODEL_API_KEY`. |
| `PERPLEXITY_API_KEY` | PRESENT | Perplexity | Verified live. Also used by `services/undx_embedding_service.py`. |
| `Gemini_AI_API` | PRESENT | Gemini | Set, live check fails 404 — stale model ID. |
| `DEEPSEEK_AI_API` | PRESENT | DeepSeek | Set, live check fails 402 — no credit. |
| `GROQ_AI_API` | PRESENT | Groq | **Malformed.** See "Known-broken". |

The non-standard names (`CLAUDE_AI_API`, `Gemini_AI_API`, `DEEPSEEK_AI_API`,
`GROQ_AI_API`) are the ones production uses. They were deliberately **not**
renamed to vendor-canonical forms: renaming a working production secret to make
a document tidier is a way to cause an outage. `undx_router.PROVIDERS` records
the real name per provider.

## Per-provider switches

Each provider can be taken out of rotation without deleting its credential
(§72, §73). Absent means enabled — adding these changes nothing until one is set
to a false value.

| Variable | Status | Effect when false |
|---|---|---|
| `META_MUSE_ENABLED` | PRESENT (`true`) | Muse is not planned or called. |
| `UNDX_OPENAI_ENABLED` | not set | Would drop OpenAI. |
| `UNDX_CLAUDE_ENABLED` | not set | Would drop Claude. |
| `UNDX_PERPLEXITY_ENABLED` | not set | Would drop Perplexity. |

A disabled provider is removed from the routing plan, not skipped when the loop
reaches it — otherwise `attempts` reports a provider that was never going to be
tried, and the kill switch reads as a failure.

## Timeouts and model overrides

| Variable | Status | Default if unset |
|---|---|---|
| `META_MUSE_TIMEOUT_MS` | PRESENT (`60000`) | 60000. **Load-bearing** — Meta's cold-route call measured 26.7s against a 25s module default. |
| `PERPLEXITY_TIMEOUT_MS` | not set | 45000 |
| `META_MUSE_MODEL` | not set | `muse-spark-1.3` (Standard tier) |
| `META_MUSE_REASONING_EFFORT` | not set | `high` |
| `PERPLEXITY_MODEL` | not set | `sonar` |
| `OPENAI_MODEL` | not set | `gpt-4o-mini` |
| `CLAUDE_MODEL` | not set | `claude-haiku-4-5` — live-verified; resolves to `claude-haiku-4-5-20251001` |
| `GEMINI_MODEL` | not set | `gemini-1.5-flash` — **retired upstream, returns 404** |

A provider's declared timeout can only raise the caller's, never shorten it.

## Routing

| Variable | Status | Effect |
|---|---|---|
| `UNDX_ROUTER_ENABLED` | PRESENT | Off ⇒ every request goes to `UNDX_DEFAULT_AI_PROVIDER` only. |
| `UNDX_MULTI_MODEL_MODE` | PRESENT | Off ⇒ the fallback chain collapses to the default provider. |
| `UNDX_DEFAULT_AI_PROVIDER` | PRESENT | `openai`. Meta is deliberately **not** the default (§7). |

## Account-level intent flags

`META_MODEL_API_ENABLED` and `META_MUSE_FALLBACK_ENABLED` are PRESENT and set to
`true`. **No code reads either.** They are recorded here rather than deleted
because they document an intent, but they should not be mistaken for controls:
the flag that actually stops Meta is `META_MUSE_ENABLED`, and the fallback chain
is `undx_router.provider_priority()`, which has no per-provider fallback switch.

## Known-broken, pre-existing

Found by `scripts/undx_provider_health_check.py`. **None of these were caused by
the multi-provider work** — the health check is simply the first thing to ask all
seven providers the same question.

### `GROQ_AI_API` — credential leak, needs rotation

The variable is set to a **JSON configuration document that contains a Groq API
key**, rather than to the key. Consequences, all live until 2026-09-11:

1. Every Groq request failed. `requests` rejects a header value containing
   newlines.
2. The rejection exception **quoted the offending header value, key included**,
   and the router logged it once per failed request.
3. The router's value-matching redactor did not fire, because the logged text
   was a *slice* of the variable and never equal to it.

Fixed in `undx_router.py` with two independent defences — `_api_key()` refuses to
return a value that cannot be sent as a header, so it never reaches the HTTP
layer; and `_safe_error()` now redacts credential-shaped fragments *within* a
configured value, not only the whole value. Pinned by
`tests/test_undx_router_multi_provider.py::MalformedCredentialTest`.

**The embedded key must still be treated as compromised and rotated.** The fix
stops future leaks; it does not un-log what was already written. Rotation and
resetting the variable to a bare key are account changes and are not done here.

### Gemini — 404, retired model

`gemini-1.5-flash` no longer resolves. Needs a current model ID in
`GEMINI_MODEL`.

### DeepSeek — 402 Payment Required

Out of credit. A billing matter, not a configuration one.

## Resolved

### Claude — was 404, now CONNECTED

Diagnosed by probing the credential directly against `api.anthropic.com`. The
result contradicted the working hypothesis: `CLAUDE_AI_API` is a **valid
Anthropic API key**. `POST /v1/messages` with it returns HTTP 200.

The 404 was the model ID. `claude-3-5-haiku-latest` has been retired upstream and
no longer resolves for this account. `GET /v1/models` lists 11 live IDs; the
cheapest current small model is `claude-haiku-4-5-20251001`, and the undated
alias `claude-haiku-4-5` resolves to it. The router default is now that alias —
read off the live model list, not guessed. Claude answers in ~785ms, the fastest
provider in the matrix.

So the credential was never the problem, and the gateway theory was wrong. Worth
recording, because the *evidence* for that theory is real and still present:

### `ANTHROPIC_*` — Claude Code CLI variables leaked into the service

These are set on the production `CoinPilotX` service:

| Variable | Value | |
|---|---|---|
| `ANTHROPIC_BASE_URL` | `https://api.meta.ai` | |
| `ANTHROPIC_AUTH_TOKEN` | byte-identical to `CLAUDE_AI_API` | |
| `ANTHROPIC_MODEL` | `muse-spark-1.3-contributor` | **Contributor tier** |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL` | `muse-spark-1.3-contributor` | **Contributor tier** |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | `muse-spark-1.3-contributor` | **Contributor tier** |
| `ANTHROPIC_DEFAULT_OPUS_MODEL` | `muse-spark-1.3-contributor` | **Contributor tier** |

Someone pointed a local Claude Code CLI at Meta's Anthropic-compatible endpoint,
and the variables ended up on the deployed service. No UNDX code reads them —
`undx_router._call_claude` hardcodes `https://api.anthropic.com/v1/messages` and
reads `CLAUDE_MODEL`, not `ANTHROPIC_MODEL`.

**That hardcoding is now load-bearing and must stay.** Any library in this
process that speaks to Anthropic through the official SDK will honour
`ANTHROPIC_BASE_URL` and `ANTHROPIC_MODEL` automatically, and would therefore
send its traffic to Meta's **Contributor** tier — the one whose console states
inputs and outputs are used to train Meta's models, and which
`UNDX_PROVIDER_DATA_POLICY.md` restricts to `SYNTHETIC` traffic only.

The obvious tidy-up here — "make the Claude adapter configurable, it already has
a base URL variable" — would silently convert a dead provider into a data
governance breach. Pinned against by
`tests/test_undx_router_multi_provider.py::ClaudeEndpointTest`.

**These six variables should be deleted from the service.** They serve no
runtime purpose, and every one of them is a loaded gun pointed at the next
library that gets added. That is an account change and is not done here.

## Verification

```bash
railway run --service CoinPilotX -- .venv/bin/python3 scripts/undx_provider_health_check.py
```

Exits 1 if a provider that is both configured and enabled fails to answer. A
provider that is unconfigured or switched off is reported and not counted as a
failure — that is a deployment choice, not a fault.
