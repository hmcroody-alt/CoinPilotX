# Pulse Briefing Architecture

Server-side intelligence briefings: every ~6h window per user is an
**evaluation, never a mandatory send**. Facts come from authoritative
platform systems, UNDX only phrases them, and every send is idempotent.

## Pipeline

```
alert_worker cycle (every 45s)
  └─ pulse_briefings.run_scheduled_cycle(limit)
       ├─ kill switch check (BRIEFINGS_DISABLED / PULSE_BRIEFINGS_ENABLED)
       ├─ eligible users: active push subscription + prefs enabled
       │  + account age > BRIEFING_MIN_ACCOUNT_AGE_HOURS
       └─ per user: evaluate_user_briefing(conn, user)
            1. CLAIM     INSERT (user_id, window_key) — UNIQUE index is the
                         idempotency anchor; violation => already handled
            2. GATHER    facts.build_briefing_facts (network + crypto)
            3. SCORE     significance weights vs SEND_THRESHOLD
            4. SUPPRESS  important_only_filter | briefing_suppressed_no_change
                         | duplicate_fingerprint
            5. SUMMARIZE UNDX (grounding-validated) or deterministic template
            6. SEND      push_service.send_push (canonical path) + in-app inbox
            7. SETTLE    status: sent | generated | suppressed | failed
```

## Modules

| Module | Responsibility |
|---|---|
| `services/pulse_briefings/crypto_provider.py` | Provider abstraction, normalization, shared TTL cache + single-flight, stale-serve, fallback |
| `services/pulse_briefings/facts.py` | Owner-scoped fact packs, significance scoring, dedupe fingerprint |
| `services/pulse_briefings/summarizer.py` | UNDX copy w/ grounding + advice validation; localized deterministic templates |
| `services/pulse_briefings/engine.py` | Schema, prefs, scheduling, windows, quiet hours, jitter, claim/suppress/send/settle, worker tick, owner-scoped reads |
| `alert_worker.py` | Hosts the tick (no new Railway service); fault-isolated from the alert sweep |
| `bot.py` | `/api/pulse/briefings` (list/detail/preferences) + `/api/admin/briefings/status` |

## Scheduling (server-side, never phone-local)

- Windows start at 00/06/12/18 **user-local time** (timezone from
  `pulse_region_preferences.preferred_timezone`, UTC fallback).
- `window_key = local_date:HH`. UNIQUE(user_id, window_key) makes worker
  restarts and overlapping cycles unable to double-send.
- Deterministic per-user jitter (0..BRIEFING_JITTER_MINUTES, seeded by
  user_id) spreads sends inside a window; a user's slot is stable.
- Quiet hours (default 22:00–07:00, wrap-around aware) **defer without
  consuming the window** — the claim happens only after the quiet check.
- Frequencies: `off`, `important_only`, `every_6h`, `morning_evening` (06/18).

## Facts contract (Stage 10)

`build_briefing_facts` output is the ONLY payload the summarizer sees:
`{user_id, generated_at, timezone, locale, network{counts}, crypto{btc/eth,
cap, direction, gainers/losers, watchlist, alert_proximity}, urgency,
significance_score}`. Crypto facts carry `provider` + `observed_at`; a
snapshot older than BRIEFING_MARKET_STALE_MAX is **omitted**
(`unavailable_reason: stale_or_provider_down`), never presented as current.

## Suppression

- Significance = Σ(weight × count); security 50, marketplace 10, unread 8,
  friend request/mention 5, comment/follower 3 … SEND_THRESHOLD = 10.
- Market movement overrides: |BTC 24h| ≥ 2.0% is significant on its own.
- Fingerprint = sha256 of bucketed (1%) market changes + network counts;
  equal to previous **sent** briefing ⇒ `duplicate_fingerprint` suppression.
- Suppressed evaluations are recorded rows — auditable, and they consume the
  window (no retry-spam).

## Summarization safety

UNDX (`undx_router.route_structured_request`) receives the bounded fact JSON
and returns `{"title","body"}`. Output is rejected — falling back to the
deterministic localized template (en/es/fr/ht) — if it contains **any number
absent from the fact payload** (grounding check; structural 1/7/24 period
tokens excepted), matches the advice regex (buy/sell/hold/…), is empty, or is
malformed. If UNDX is down entirely, templates always work: the system is
fully functional with zero LLM availability.

## Delivery

Canonical `push_service.send_push` (device dedupe, retries, delivery jobs
come free). Payload carries counts-free metadata only: `notification_type`,
`briefing_id`, `deep_link: pulse://notifications?briefing=<id>`,
`generated_at` — never message bodies or fact details (lock-screen safe).
An in-app inbox copy is written best-effort.

## Failure model

| Failure | Behavior |
|---|---|
| Provider down | Coinbase fallback → cached stale (≤30min, marked) → crypto omitted |
| UNDX down/invalid | Deterministic template |
| Worker crash mid-send | Claim row settles as `failed`; window not re-sent |
| Briefing tick exception | Caught in alert_worker; alert sweep unaffected |
| Kill switch | `BRIEFINGS_DISABLED=true` stops sends only |

## Observability

`metrics_snapshot()` (surfaced at `/api/admin/briefings/status`):
briefing_jobs_started/completed/failed, briefings_sent, briefings_suppressed,
briefings_duplicate_suppressed, crypto_cache_hits/misses,
crypto_provider_errors, crypto_provider_429. Worker logs `BRIEFING_CYCLE`
per cycle and structured warn/error lines
(`BRIEFING_UNDX_UNGROUNDED_REJECTED`, `BRIEFING_CRYPTO_PROVIDER_ERROR`, …).

## Load / cost model (Stage 62)

Provider calls are **decoupled from user count**: the shared snapshot cache
(single-flight) makes the whole fleet consume ~1 call per TTL per endpoint.

| Users | Provider calls/day | LLM calls/day (upper bound) | Notes |
|---|---|---|---|
| 1k | ~720 (2 endpoints × 180s TTL ceiling) | ≤ 4k, realistically ≪ (suppression) | Demo CoinGecko key sufficient |
| 10k | ~720 | ≤ 40k | Paid CoinGecko tier recommended (rate headroom) |
| 100k | ~720 | ≤ 400k | Shard worker by user_id ranges; raise batch limit |
| 1M | ~720 | ≤ 4M | Dedicated briefing worker + queue; LLM budget gate |

LLM cost scales with *sends*, not users: suppression typically removes the
majority of windows. `BRIEFING_SEND_RATE_CAP` bounds per-cycle burst.
