# Pulse Briefing QA Checklist

## Automated (in repo — run these first)

```
python3 -m unittest tests.briefings.test_pulse_briefings -v   # 35 tests
```

Covers: provider normalization, Coinbase fallback, cache TTL + single-flight
(N callers → 1 fetch), stale-serve marking, staleness omission, significance
weights, fingerprint dedupe (bucketing), idempotency claim, quiet hours
incl. wrap-around, timezone windows (every_6h + morning_evening), jitter
determinism, preferences validation/gating, template localization
(en/es/fr/ht) + self-grounding, UNDX grounding rejection → template
fallback, advice rejection, malformed-JSON fallback, push payload privacy +
deeplink shape, owner-scoped reads (cross-account blocked), kill switch.

## Manual — staging/production (cannot be proven from the sandbox)

### Delivery
- [ ] Briefing push arrives on a real device (Expo/FCM/APNs and web push).
- [ ] Tapping the push opens the app at notifications with the briefing
      (`pulse://notifications?briefing=<id>` deeplink resolves).
- [ ] In-app inbox shows the same briefing copy.
- [ ] Lock screen shows title/body only — no message contents, no
      sender names, nothing sensitive.

### Scheduling honesty
- [ ] User in a non-UTC timezone receives windows at *their* 00/06/12/18.
- [ ] No push between 22:00–07:00 local (default quiet hours); briefing
      arrives after quiet hours end, same window.
- [ ] Two worker restarts inside one window ⇒ still exactly one push.
- [ ] `frequency=morning_evening` ⇒ only 06/18 windows.
- [ ] `frequency=important_only` ⇒ no push for routine activity; push for
      security alert or ≥2% BTC move.
- [ ] `frequency=off` / `enabled=false` ⇒ silence.

### Suppression honesty
- [ ] Quiet account + flat market ⇒ no push, row recorded as
      `briefing_suppressed_no_change`.
- [ ] Identical state across two windows ⇒ second is `duplicate_fingerprint`.

### Data integrity
- [ ] Numbers in the push match `/api/pulse/briefings/<id>` facts exactly.
- [ ] With CoinGecko blocked (staging): copy degrades to majors (Coinbase)
      or omits crypto — never shows stale/fabricated prices.
- [ ] With UNDX keys removed (staging): template copy sends in the user's
      language.
- [ ] No copy ever contains buy/sell/hold or price predictions.

### Ops
- [ ] `BRIEFINGS_DISABLED=true` stops sends within one worker interval;
      alerts continue.
- [ ] `/api/admin/briefings/status` requires owner auth (401/403 otherwise).
- [ ] `GET /api/pulse/briefings` returns only the caller's rows.
- [ ] `scripts/briefings/benchmark_crypto_provider.py` run on Railway;
      p50/p95 recorded in CRYPTO_PROVIDER_DECISION.md.

### Load (limited rollout)
- [ ] `crypto_cache_misses` stays ~flat as cohort grows (shared cache proof).
- [ ] sent/suppressed ratio sane (expect suppression majority).
- [ ] No 429 growth at target cohort size.
