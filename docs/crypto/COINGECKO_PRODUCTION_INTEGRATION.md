# CoinGecko Production Integration

How PulseSoc talks to CoinGecko, why it is built this way, and what will bite
you if you change it. Written 2026-08-30 alongside the paid-API activation.

## The headline

Before this change, **every production CoinGecko request failed.** The paid plan
had been billed and never used.

The cause was a host/header mismatch. `services/market_data.py` sent the *paid*
key in the *demo* header to the *demo* host:

```
GET https://api.coingecko.com/api/v3/coins/markets
x-cg-demo-api-key: <paid key>
```

CoinGecko answers that combination with `HTTP 400`, `error_code 10010`,
"If you are using Pro API key, please change your root URL from
api.coingecko.com to pro-api.coingecko.com". Every time. Not intermittently.

Nothing alerted, because `live_market_board` catches the exception and falls
back to the Coinbase ticker for BTC/ETH/SOL only. Users saw a three-row board
and the message "Live data source is partially connected", which reads like a
provider outage rather than a configuration error that had never once worked.
The monthly credit counter agreed: 160 calls consumed against a 100,000 credit
allowance, and essentially all 160 were made by the engineer investigating it.

The lesson worth keeping: a fallback that is good enough to look plausible will
hide a total provider failure indefinitely. The board never went blank, so
nobody went looking.

## Plan and limits

Verified live from `GET /key` on 2026-08-30:

| Field | Value |
| --- | --- |
| Plan | Basic (paid) |
| Rate limit | 300 requests/minute |
| Monthly credit | 100,000 calls |
| Host | `https://pro-api.coingecko.com/api/v3` |
| Auth header | `x-cg-pro-api-key` |

Two limits, and **the monthly credit is the binding one.** 100,000 calls/month
is a sustained average of **2.31 requests/minute**. The per-minute ceiling of
300 is 130x higher than the budget can afford to sustain. Running flat out at
the governor's 270/min would exhaust the entire month in **6.2 hours**.

Treat 300/min as a burst limit and 2.31/min as the actual budget.

### Plan boundaries found by probing

- `days` accepts numbers up to **730**. `731` and the literal `max` return 401.
  This is why `HISTORY_RANGE_DAYS["ALL"]` is `730` and not `"max"` — the old
  value made the ALL chart tab fail on every single request.
- Endpoints confirmed available: `/simple/price`, `/coins/markets`, `/global`,
  `/search/trending`, `/coins/{id}/market_chart`, `/coins/{id}/ohlc`, `/key`.

## The canonical client

`services/coingecko_client.py` is the only module permitted to talk to
CoinGecko. Everything else goes through it.

It owns: base URL, auth header, timeouts, retries, 429 handling, the rate
governor, the shared cache, single-flight, error classification, and telemetry.

### Host and header cannot drift apart

```python
def base_url() -> str:
    override = os.getenv("COINGECKO_API_BASE", "").strip().rstrip("/")
    if override:
        return override
    return PRO_BASE if api_key() else DEMO_BASE

def auth_header_name() -> str:
    return "x-cg-pro-api-key" if "pro-api" in base_url() else "x-cg-demo-api-key"
```

The header is *derived from the resolved host*, never configured separately.
This is the direct structural fix for the outage above: there is no combination
of environment variables that produces a paid key on the demo host. If you add a
`COINGECKO_API_BASE` override, both the host and the header move together.

Do not reintroduce a second place that builds these headers. The benchmark
script `scripts/briefings/benchmark_crypto_provider.py` used to hardcode them
and consequently benchmarked a 401 against the wrong host — a benchmark that
does not exercise the production configuration is measuring fiction.

### Rate governor

A sliding 60-second window over **attempted** requests, default 270/min
(`RATE_LIMIT_PER_MIN`), which is 10% headroom under the real 300.

Attempts are counted, not successes. A failed request still consumed provider
quota, so counting only successes would let a failing loop run at full speed
into a 429 wall.

**Known gap:** the governor guards the per-minute limit only. It has no notion
of the monthly credit budget, which is the tighter constraint. See "Credit
budget" below.

### Cache, single-flight, stale serve

`get_json_cached_meta(path, params, ttl, cache_key)` returns
`(value, served_from_cache, duration_ms)`.

Concurrent callers for the same key share one upstream request via
`_FLIGHT_LOCKS`. Verified in production conditions: 8 concurrent callers
produced 1 upstream request. Cache hit 0.006 ms against a 580 ms miss.

On provider failure the client will serve a cached value up to
`STALE_MAX_SECONDS` (1800) old, tagging dict payloads with `stale=True`.
Consumers must surface staleness rather than present it as live. Never
fabricate a price.

### Symbol to ID mapping

`COIN_IDS` maps tickers to CoinGecko ids, and `coin_id(symbol)` returns `None`
for anything not in the table.

**A ticker is not an id.** Measured against `/coins/markets`: 50 of the top 60
assets by market cap have an id that differs from their lowercased ticker.
`BTC` is `bitcoin`, `AVAX` is `avalanche-2`, `MNT` is `mantle`.

`coin_id()` returning `None` means "we do not know", not "use the ticker".
Guessing is actively harmful here: the Coinbase fallback rows carry
`id = lowercased ticker`, so a guess produces `/coins/btc/market_chart`, a 404,
and a chart that fails for the single most important asset on the platform.

All 46 entries were verified against the live API — every id resolves, and
every symbol→id pairing was confirmed by `/coins/markets`. Some ids legitimately
equal their lowercased ticker (`near`, `sui`, `aave`); that is not evidence of a
bad entry.

### Error classification

Failures are classified into stable internal codes rather than surfaced raw:

`AUTH`, `RATE_LIMIT`, `TIMEOUT`, `PROVIDER_5XX`, `BAD_RESPONSE`,
`UNKNOWN_ASSET`, `NETWORK`.

`_record_error()` deliberately records the path and code only — never params,
never headers. The API key travels in a header, and a diagnostic that echoes the
request is how keys end up in log aggregators.

`telemetry_snapshot()` exposes `cg_api_key_present` as a **boolean**, plus the
last error code and path. It never exposes the key.

## Credit budget

Call volume is a function of cache windows and process count, **not** user
count. Every cache is keyed by content, so a million users cost the same as one
user for any given cache slot. `get_watchlist_snapshots()` reads from the cached
overview and issues zero per-user calls.

Modelled as a renewal process — for a slot with TTL `T` and demand `r` req/sec
at one process, upstream fetches/hour is `3600 / (T + 1/r)`, tending to `3600/T`:

| Registered users | Calls/hour | Calls/month | % of 100k credit |
| --- | --- | --- | --- |
| 0 (timer floor) | 92 | 66,240 | 66% |
| 1,000 | 182 | 131,359 | 131% |
| 10,000 | 560 | 402,892 | 403% |
| 100,000 | 1,794 | 1,291,885 | 1,292% |
| 1,000,000 | 4,017 | 2,892,095 | 2,892% |

The caching works — 100k to 1M users is a 10x jump in traffic and only a 2.2x
jump in provider calls. The problem is the absolute ceiling, not the scaling.

Two things stand out:

1. **The floor is 66% of the plan with zero users.** That is pure timer-driven
   burn: the alert worker polls the board every 90 seconds and the briefings
   overview refreshes every 180 seconds — for briefings that are only ever
   *sent* every 6 hours.
2. **Charts dominate at scale.** `HISTORY_CACHE` is per-process and the 1H range
   has a 60-second TTL, so chart traffic is the only term that keeps climbing.

### Recommended retune (environment only, no code change)

| Variable | Now | Suggested | Effect |
| --- | --- | --- | --- |
| `MARKETS_CACHE_SECONDS` | 60 | 300 | 40 → 12 calls/hr |
| `BRIEFING_MARKET_OVERVIEW_TTL` | 180 | 900 | 40 → 8 calls/hr |
| `BRIEFING_MARKET_TRENDING_TTL` | 300 | 1800 | 12 → 2 calls/hr |

Floor drops from 66,240 to 15,840 calls/month — **66% of plan down to 16%**,
saving roughly 50,400 credits every month. A briefing delivered every six hours
does not need market data that is 180 seconds fresh.

Beyond that, the options are a plan upgrade (a billing decision) or a shared
Redis cache so the per-process multiplier stops applying.

## Which services need the key

`COINGECKO_API_KEY` belongs only on services that reach market data:

| Service | Needs it | Why |
| --- | --- | --- |
| CoinPilotX (web) | yes | crypto routes, market board, charts, collectors |
| python alert_worker.py | yes | crypto alerts and the briefings fact pack |
| python telegram_worker.py | yes | `/signals`, `/portfolio_live` → `get_best_price` |
| coinpilotx-undx-worker | no | UNDX imports no market module at all |
| coinpilotx-pulse-worker | no | feed and AI only |
| PulseSoc Command Center Worker | no | messaging, presence, push |
| coinpilotx-media-engine | no | media processing |
| pulsesoc-ads-worker | no | ads delivery |
| Postgres | no | database |

The telegram worker was missing the key until 2026-08-30, so its crypto commands
ran anonymously against the demo host. Added during this activation.

UNDX having no market path is worth stating explicitly: it can only summarize
facts handed to it, so the "UNDX must not invent prices or causality" rule is
enforced by structure rather than by discipline.

## Rules

1. **One client.** New CoinGecko traffic goes through
   `services/coingecko_client.py`. A request the governor cannot see is a request
   it cannot hold back, and it appears in no telemetry.
2. **Never fabricate.** The chain is CoinGecko → Coinbase → marked stale cache →
   omit. Omitting is always better than inventing.
3. **Never log the key.** Not in errors, not in benchmarks, not in telemetry.
   Booleans and SHA-256 prefixes only.
4. **Never guess a coin id.** `coin_id()` returning `None` means ask, not assume.
5. **Watch the monthly credit, not just the rate limit.** The governor does not
   protect you from the budget.

## Verification

```bash
# Tests
.venv/bin/python -m pytest tests/briefings/ -q

# Live benchmark, from a network that can reach the provider (i.e. Railway)
python3 scripts/briefings/benchmark_crypto_provider.py

# Plan and remaining credit
curl -s -H "x-cg-pro-api-key: $COINGECKO_API_KEY" \
  https://pro-api.coingecko.com/api/v3/key
```

Secret hygiene was verified across 4,787 tracked files and the full git history:
zero occurrences of the live key in either.
