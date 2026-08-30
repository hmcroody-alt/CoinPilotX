# Pulse Briefing Runbook

## Kill switch (first move in any incident)

```
railway variables set BRIEFINGS_DISABLED=true   # then restart alert_worker
```

Stops scheduled briefing sends only. Alerts, pushes, and all other
notifications are unaffected. Re-enable by unsetting or `false`.

## Where it runs

The briefing tick lives inside `alert_worker` (Procfile process
`alert_worker: python alert_worker.py`). There is no separate briefing
service. The tick is wrapped in its own try/except: a briefing fault cannot
break the alert sweep.

## Health checks

- `GET /api/admin/briefings/status` (owner auth) — enabled flags, metrics
  counters, windows, rate cap.
- Worker logs, one line per cycle:
  `BRIEFING_CYCLE {'processed': n, 'sent': n, 'suppressed': n, ...} metrics={...}`
  and `Briefing tick processed=… sent=… suppressed=… failed=…`.
- DB: `SELECT status, COUNT(*) FROM pulse_briefings GROUP BY status;`

## Common incidents

### Users report no briefings
1. Kill switch set? Check `BRIEFINGS_DISABLED` / `PULSE_BRIEFINGS_ENABLED`.
2. Is alert_worker running? (`railway logs`) — the tick logs every cycle.
3. Is it suppression working as designed? `suppressed` rows with
   `briefing_suppressed_no_change` mean nothing significant happened —
   that is correct behavior, not an outage.
4. User-level: prefs enabled? frequency ≠ off? active push subscription?
   account older than 24h? inside quiet hours (22:00–07:00 default)?

### Duplicate pushes
Should be structurally impossible (UNIQUE(user_id, window_key) claim +
push_service device dedupe). If seen: check for two alert_worker instances
running against the same DB, and inspect `pulse_briefings` for the window —
one row per user per window is the invariant.

### Provider degradation
Symptoms: `crypto_provider_errors` / `crypto_provider_429` climbing, copy
lacking market data. Ladder is automatic (Coinbase → stale ≤30min → omit).
Actions: verify CoinGecko status page; consider raising
`BRIEFING_MARKET_OVERVIEW_TTL`; consider paid tier if 429s are sustained.
Never "fix" by loosening staleness — omission is the designed behavior.

### UNDX degradation
Symptoms: `summary_source='template'` on all rows,
`BRIEFING_UNDX_*` warnings. Briefings continue via deterministic templates;
no action required for delivery. Investigate undx_router provider keys at
leisure. `BRIEFING_UNDX_UNGROUNDED_REJECTED` spikes mean a model is
inventing numbers — the guard is doing its job; consider dropping that
provider in undx_router ordering.

### Push spike / runaway volume
`BRIEFING_SEND_RATE_CAP` (default 200) bounds sends per cycle;
`BRIEFING_CYCLE_BATCH_LIMIT` (default 50) bounds evaluations. Lower them or
kill-switch. History self-prunes at 60 days.

## Tuning knobs

All env, all optional (defaults in parentheses): BRIEFING_CYCLE_BATCH_LIMIT
(50), BRIEFING_SEND_RATE_CAP (200), BRIEFING_JITTER_MINUTES (25),
BRIEFING_MIN_ACCOUNT_AGE_HOURS (24), BRIEFING_HISTORY_RETENTION_DAYS (60),
BRIEFING_MARKET_OVERVIEW_TTL (180s), BRIEFING_MARKET_STALE_MAX (1800s),
BRIEFING_MARKET_TIMEOUT (8s).

## Rollout (Stages 66–69 — operator actions, not code)

1. **Shadow**: deploy with `PULSE_BRIEFINGS_ENABLED=false`; watch logs.
2. **QA cohort**: enable; only QA accounts get prefs rows with enabled=1 by
   flipping others off via `POST /api/pulse/briefings/preferences`-equivalent
   admin SQL, or keep batch limit tiny.
3. **Limited**: raise BRIEFING_CYCLE_BATCH_LIMIT gradually; watch
   sent/suppressed ratio and provider metrics.
4. **Global**: default-on remains gated by per-user prefs + push opt-in.
