# Crypto Provider Decision (Stages 1–5)

## Decision

**PRIMARY: CoinGecko** · **FALLBACK: Coinbase public ticker** ·
**OWNER_PAYMENT_REQUIRED: NO** for the current build.

## Why CoinGecko stays primary

1. **Already the platform provider.** `services/market_data.py` has used
   CoinGecko in production for the crypto board and alert engine;
   `COINGECKO_API_KEY` (demo tier, `x-cg-demo-api-key`) is already wired.
   No new vendor, credential, or billing relationship.
2. **Field depth matches briefing needs in one call.**
   `/coins/markets?price_change_percentage=1h,24h,7d` returns price, cap,
   volume, rank, and 1h/24h/7d deltas for the top 50; `/global` adds total
   cap, 24h cap change, and BTC dominance; `/search/trending` adds trending.
   Three endpoints cover the entire fact schema.
3. **Rate profile fits the shared-cache design.** Demo tier ≈ 30 req/min /
   ~10k/mo. With the 180s shared snapshot, the whole platform uses ~480–720
   calls/day *regardless of user count* — far inside demo limits.

## Alternatives assessed

| Provider | Verdict | Reason |
|---|---|---|
| CoinMarketCap | Available later | `COINMARKETCAP_API_KEY` exists in `.env.example` but was never wired; free tier lacks 1h/7d windows on the basic endpoint; adds a vendor without adding a needed field |
| Coinbase public ticker | Fallback only | Keyless and reliable, but majors-only (BTC/ETH/SOL), price+volume only, no % changes, no global stats |
| CryptoCompare / Messari | Rejected for now | New vendor + key management for no schema gain at current scale |

## Failure ladder

CoinGecko error/429 → Coinbase fallback overview (majors, `provider:
"coinbase"`, change fields null) → last cached snapshot served if younger
than `BRIEFING_MARKET_STALE_MAX` (30min, marked `stale: true`) → crypto
section **omitted** from the briefing (`stale_or_provider_down`). Fabricating
or silently reusing old numbers is structurally impossible: every snapshot
carries `provider` + `observed_at`, and `facts.collect_crypto_facts` drops
stale data before the summarizer ever sees it.

## Latency baseline (Stage 1)

The build sandbox's egress proxy blocks `api.coingecko.com` (tunnel 403), so
the live p50/p95 baseline must be captured from the production network:

```
python3 scripts/briefings/benchmark_crypto_provider.py   # on Railway
```

Reports p50/p95/min/max latency, error and 429 rates, and field depth per
endpoint. Code-audit baseline of the pre-existing integration: 60s cache in
`market_data.py`, 45s TTL in `live_market_service`, 8–10s HTTP timeouts.
The briefing path *reduces* provider pressure: 180s shared TTL + single-flight
versus per-request 60s caching.

## When to pay (documented, nothing purchased)

Move to CoinGecko's paid tier when any of: sustained 429s in
`crypto_provider_429` metrics, >10k calls/mo, or SLA requirements for the
briefing product. Decision + payment remain with the owner; the code path is
unchanged (same key header).
