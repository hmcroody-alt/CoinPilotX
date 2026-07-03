# PulseSoc Intelligence Collectors Phase 2

## Summary

Phase 2 adds real, isolated source collectors for the PulseSoc Galaxy Intelligence Center. Collectors run out of band through `scripts/run_intelligence_collectors.py` or the admin collector trigger. Normal user page loads continue to read from cached database state only.

## Collector Architecture

- Added `services/intelligence_collectors/` with a shared base, normalized candidate dataclass, source status records, short network timeouts, and an in-memory TTL cache.
- Added registered collectors for `crypto_pulse`, `market_pulse`, `world_pulse`, `security_pulse`, `technology_pulse`, `pulsesoc_discoveries`, `pulsesoc_pulse`, `creator_pulse`, `music_pulse`, and `system_pulse`.
- Added `scripts/run_intelligence_collectors.py` with `--stream`, `--all`, `--dry-run`, `--limit`, `--json`, `--deliver`, and `--target-user-id`.
- The admin `/api/admin/intelligence/collect` route now uses the collector runner instead of the Phase 1 seed-only collector.

## Sources Activated

- Crypto Pulse: CoinGecko public simple price API and Binance public 24-hour ticker for BTC, ETH, and SOL.
- Market Pulse: Yahoo Finance public quote endpoint for major indices and commodities, skipped safely if unavailable.
- World Pulse: USGS significant earthquake feed.
- Security Pulse: CISA Known Exploited Vulnerabilities catalog.
- Technology Pulse: official OpenAI and Apple RSS/newsroom feeds when reachable.
- PulseSoc Discoveries: internal Pulse AI feature registry.
- PulseSoc Pulse: internal report-backed platform updates.
- System Pulse: explicit `PULSESOC_SYSTEM_PULSE_*` environment events only.

## Sources Skipped / Config Missing

- CoinMarketCap, Polygon, Alpha Vantage, Reuters, and AP remain config-gated until provider keys are present.
- Creator Pulse skips if supported internal creator telemetry tables are absent.
- Music Pulse skips if supported PulseSoc Music telemetry tables are absent.
- System Pulse skips when no explicit system event environment value is configured.

## Cache Strategy

Collectors use in-process TTL caching. Current TTLs are conservative and per source:

- Crypto: 30-45 seconds.
- Markets: 90 seconds.
- World: 180 seconds.
- Security: 900 seconds.
- Technology: 900 seconds.
- Internal sources: database/file state only.

## Dedupe Strategy

Each normalized candidate includes a stream-aware `dedupe_key`. The central engine still owns final dedupe through `intelligence_events.event_key`, and merges source evidence when duplicate events arrive.

## Confidence Scoring

Collectors provide source confidence, freshness, impact, severity, source keys, and spam-safe metadata. The central engine remains the final scorer and applies stream thresholds before accepting an Intelligence Pulse.

## Forecast Behavior

Forecast records are still created only by the central engine when accepted high-priority or forecast-eligible signals justify one. Forecasts remain labeled by confidence and are not guarantees.

## Delivery Behavior

Live collector runs persist through `pulsesoc_intelligence_engine.ingest_signal`. Notification delivery still goes through the central PulseSoc notification system with user stream preferences, thresholds, digest/push settings, and dedupe.

## Admin UI Changes

The admin collector form now supports all-stream runs, dry-run mode, limits, explicit delivery, and the new System Pulse stream. Secrets are never shown.

## User UI Changes

The user center continues to render streams, signals, forecasts, feedback, and stream settings from cached state. Phase 2 does not fetch external providers during user page loads.

## Pulse AI Integration

Pulse AI knowledge now includes official download/share behavior and the App Store action-button pattern so users can ask how to download or share PulseSoc without seeing raw links by default.

## QA Results

Static audits and compile checks are documented in the final handoff.

Executed dry-run:

```bash
venv/bin/python scripts/run_intelligence_collectors.py --all --dry-run --limit 5 --json
```

Result:

- `ok=true`, duration about `3285ms`.
- 15 normalized candidates returned across Technology Pulse, PulseSoc Discoveries, and PulseSoc Pulse.
- CoinGecko returned successfully; no crypto move exceeded threshold.
- Binance returned `http_451` and was marked failed without stopping other collectors.
- Yahoo Finance returned `http_401` and was marked failed without stopping other collectors.
- USGS returned successfully; no major event exceeded filters.
- CISA returned successfully; no recent item exceeded the collector filter window.
- Creator, Music, and System skipped honestly because supported telemetry or explicit system event input was not present.

Executed safe live internal collector:

```bash
venv/bin/python scripts/run_intelligence_collectors.py --stream pulsesoc_discoveries --limit 1 --json
```

Result: one PulseSoc Discoveries event was accepted through central `ingest_signal` with event id `2`, no delivery requested.

State verification:

```bash
venv/bin/python - <<'PY'
from services import pulsesoc_intelligence_engine as e
state=e.center_state(1, limit=5)
print({'ok': state.get('ok'), 'events': len(state.get('events') or []), 'first_actions': (state.get('events') or [{}])[0].get('actions')})
PY
```

Result: user state returned sanitized action objects for `Try It` and `Invite Friends`.

## Known Limitations

- No external provider key was added by this change.
- Creator and Music signals require real internal telemetry tables before they can emit user-facing Pulses.
- In-process cache resets between worker invocations; Phase 3 should add Redis or durable worker cache when deployed at scale.

## Next Phase

- Add scheduler/worker orchestration.
- Add Redis-backed cache and source circuit breakers.
- Add admin threshold tuning and rejected-candidate inspection.
- Add engagement-based personalization and digest bundling.
