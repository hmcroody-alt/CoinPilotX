# Pulse Briefings — Production Acceptance (0d00b15a)

Date: 2026-08-30 · Verdict: **CONTINUE QA** · Real pushes sent during acceptance: **0**

## Deployment chain

| Check | Result |
|---|---|
| origin/main SHA | `0d00b15a` |
| Web (CoinPilotX, deploy `a93fa477`) | SUCCESS at `0d00b15a` |
| alert_worker (deploy on `6d1323e3`) | SUCCESS at `0d00b15a` |
| Kill switch | `BRIEFINGS_DISABLED=true` on web + worker; worker boot log shows the disabled tick (engine loads, delivery hard-off) |

Kill-switch semantics caveat: `BRIEFINGS_DISABLED=true` wins over
`PULSE_BRIEFINGS_ENABLED=true`, so the "engine active, delivery blocked" combo
disables the scheduler tick entirely. Shadow evidence below was produced by a
harness calling `evaluate_user_briefing(send=False)` — the full pipeline runs,
nothing is delivered.

## Postgres RETURNING-id regression (the defect `0d00b15a` fixed)

Live INSERT into production `pulse_briefings` returned a canonical id
(`lastrowid=1`), the row was visible by that id, and the probe row was deleted.
Before the fix, `pulse_briefings` was missing from `AUTO_PK_TABLES`, so on
Postgres the claim INSERT had no RETURNING clause, claims stuck in
`processing`, and push deeplinks read `briefing=None`. Regression test added
(36th in the suite).

## CoinGecko benchmark (keyed, from the Railway production network)

Demo-host run (pre paid-migration): `simple_price` p50 30ms / p95 92ms,
`trending` p50 26ms / p95 36ms. `markets` and `global` failed 10/10 with
CoinGecko error 10010 — the key is a **paid (pro) key** that the demo host
rejects. This finding launched the paid-API activation mission; see
`COINGECKO_PAID_ACTIVATION` section below and commit `18891a16`.

## Stage 4 shadow generation (zero delivery)

Three QA users ran the full pipeline on the temp one-shot service
(`acceptance-briefings-DELETE-AFTER`, deploys `70ef5100` / `461b69ad`, both at
`0d00b15a`), with three independent no-send guards: harness `send=False`, push
sender monkeypatched with an interceptor, and `PUSH_NOTIFICATIONS_ENABLED=false`.
`PUSH_INTERCEPT` count: 0 escapes.

Evidence per behavior:

- **Grounding**: UNDX copy (provider `undx:openai`) passed the module's own
  `grounded()` check; `advice_hit=false`. (A harness regex flagged two comma
  artifacts — regex bug, module check authoritative.)
- **Idempotency**: re-run after restart returned `already_claimed` for the same
  window; fingerprints deterministic across runs.
- **Quiet hours**: 23:30 local → `quiet_hours` skip.
- **No-activity suppression**: significance 0 → `briefing_suppressed_no_change`.
- **Template fallback**: UNDX disabled path rendered the localized template.
- **Crypto fallback ladder (live)**: with CoinGecko markets failing (10010),
  overview came from Coinbase — `provider="coinbase"`, change fields `null`,
  direction `"unknown"`, no fabricated numbers anywhere.
- **Dedupe**: duplicate_fingerprint branch covered by determinism proof + unit
  tests (prod comparison only sees `status='sent'`, none exist).
- **Cleanup**: shadow rows (ids 2, 4, 6) deleted; 0 remaining.

Non-fatal prod schema gaps observed (both caught, both degrade):
`crypto_alerts` has no `symbol` column on Postgres
(`BRIEFING_WATCHLIST_FACTS_FAILED`); `pulse_region_preferences` absent
(timezone defaults to UTC).

## Perplexity semantic retrieval (deployed a69579ab)

Live vector probe: auth PASS, 256 dims, unit-normalized, 366ms.
`UNDX_SEMANTIC_RETRIEVAL_STAGE` remains `off` — set to `shadow` only after the
consuming service is confirmed; never global.

## COINGECKO_PAID_ACTIVATION (commit `18891a16`)

`/key` probe from Railway identified the plan: **CoinGecko Basic — 300
req/min, 100,000 monthly credits**. Pro-host benchmark (6 samples each, zero
errors, zero 429s):

| Endpoint | p50 | p95 |
|---|---|---|
| simple_price | 22.8ms | 23.8ms |
| markets_top50 | 27.6ms | 30.4ms |
| global | 108.7ms | 110.5ms |
| trending | 36.0ms | 43.8ms |

Depth: `market_chart` 1d = 289 points (~5-min granularity); `ohlc` 1d = 48
candles. Canonical client `services/coingecko_client.py` now owns auth
(host-derived header, demo/pro can never split), UA, retry, 429
classification, a 270/min governor (10% headroom under Basic's 300), shared
TTL cache with single-flight, stale-serve (≤30min, tagged), and telemetry.
All four production call sites migrated off `x-cg-demo-api-key`. Tests: 62/62.

## Cleanup

Temp service `acceptance-briefings-DELETE-AFTER` (`5a1b6104`) deleted after
evidence harvest. No temp variables remain on production services.

## Gates to QA activation (not done in this acceptance)

1. Owner pushes `18891a16`; web + alert_worker redeploy at that SHA.
2. Second shadow run on the paid API (expect `provider="coingecko"` with full
   depth: volume, dominance, volatility, trending in the fact pack).
3. QA-only delivery: flip `BRIEFINGS_DISABLED=false` scoped to the QA cohort
   (`UNDX_AGENT_QA_USER_IDS` + `UNDX_V5_QA_USER_IDS`) — never global.
