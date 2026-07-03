# Market and Crypto Intelligence Advisor

## Summary

PulseSoc now treats traditional market intelligence as a first-class companion to Crypto Pulse. Market Pulse focuses on major, explainable signals across broad indexes, volatility, rates, dollar strength, commodities, sector ETFs, and macro calendar events. It is intentionally conservative: no weak market alerts, no buy/sell commands, and no profit promises.

Pulse AI language is educational only:

> Pulse AI provides educational market intelligence only. This is not financial advice.

## What Changed

- Expanded `services/intelligence_collectors/markets.py` from a small six-symbol watcher into a broad major-market collector.
- Added tracked assets for S&P 500, NASDAQ, Dow Jones, Russell 2000, VIX, gold, oil, 10Y Treasury yield, USD index, and major sector ETFs.
- Added market-specific status card metadata with asset, status, signal, confidence, risk, horizon, rationale, suggested action, and disclaimer.
- Added safe market language such as `Momentum improving`, `Risk elevated`, `Breakout watch`, `Support test`, `Confirmation needed`, and `Pullback risk rising`.
- Added Market Pulse source readiness for Yahoo Finance, Polygon, Finnhub, and Alpha Vantage.
- Added central Market Intelligence schedule, tracked assets, signal rules, disclaimer, and 3-hour cadence guard in `services/pulsesoc_intelligence_engine.py`.
- Added an admin-only Market Intelligence dashboard section to the Galaxy Intelligence Center.
- Expanded Pulse AI knowledge and feature registry with S&P 500 and major-market guidance.
- Added `scripts/market_and_crypto_intelligence_audit.py`.

## Market Assets Covered

- S&P 500 via `^GSPC` or `SPY`
- NASDAQ via `^IXIC` or `QQQ`
- Dow Jones via `^DJI` or `DIA`
- Russell 2000 via `^RUT` or `IWM`
- VIX via `^VIX`
- Gold via `GC=F` or `GLD`
- Oil via `CL=F` or `USO`
- 10Y Treasury yield via `^TNX`
- USD Index via `DX-Y.NYB`
- Major sector ETFs: `XLK`, `XLF`, `XLE`, `XLV`, `XLY`, `XLP`, `XLU`, `XLI`, `XLB`, `XLRE`, `XLC`

## Alert Rules

Market Pulse only emits alerts when a signal is meaningful:

- S&P 500 moves 1%+ intraday.
- NASDAQ moves 1.5%+ intraday.
- VIX jumps 8%+.
- Fed, CPI, PPI, or jobs report occurs when supported by configured sources.
- Major support/resistance or market-close recap signals can be accepted when supported.
- Sector, commodity, dollar, yield, and Russell alerts use stricter per-asset thresholds.

No market signal tells users to buy or sell. Alerts are framed as market intelligence and research context.

## Cadence

Market Intelligence does not send every three hours all day.

Default cadence:

- Normal users: Pre-Market Brief and Market Close Recap max.
- Active market users: Pre-Market, Market Open if needed, Power Hour, Close Recap.
- Urgent-only users: Fed/CPI/jobs, emergency market move, VIX spike, crash risk, or major macro/geopolitical event.

In the global one-alert-per-three-hours rotation, Market Pulse participates only when the event is a major market event or high-priority accepted event. Weak market alerts are skipped.

## Crypto Safety

Crypto Pulse remains separate but aligned with the same safety rule: market intelligence only, not investment advice. Crypto candidates already use multi-source movement checks where available and carry `no_investment_advice` metadata.

## Pulse AI Coverage

Pulse AI can now answer:

- What is the S&P 500 doing today?
- Is the market risky right now?
- Explain today’s market signal.
- Why did I get this S&P 500 alert?
- What does VIX mean?
- What should beginners watch in the market?

If live market data is unavailable, Pulse AI should say so and explain general concepts instead of inventing prices or current conditions.

## Admin Dashboard

The admin Galaxy Intelligence Center now includes a Market Intelligence section showing:

- Tracked assets
- Normal and active cadence limits
- Signal rules
- Market source health
- Educational disclaimer

Regular users do not see the admin command-center machinery.

## Known Limitations

- Yahoo Finance public quote access may rate-limit or reject requests in some environments. The collector records the source failure without stopping other streams.
- Polygon, Finnhub, and Alpha Vantage remain config-gated until API keys are present.
- Fed/CPI/jobs and earnings event alerts require a configured calendar/news source before automated event alerts can be emitted.
- Moving averages, RSI, market breadth, and earnings calendar scoring are represented in the design contract but require provider support before becoming live scoring inputs.

## QA Results

Completed in this change set:

- `venv/bin/python -m py_compile services/intelligence_collectors/markets.py services/pulsesoc_intelligence_engine.py services/pulse_ai_knowledge.py scripts/market_and_crypto_intelligence_audit.py`
- `venv/bin/python -m py_compile bot.py services/*.py services/intelligence_collectors/*.py scripts/run_intelligence_collectors.py scripts/pulsesoc_intelligence_collectors_phase2_audit.py scripts/market_and_crypto_intelligence_audit.py`
- JSON validation for `data/pulse_ai/pulsesoc_knowledge.json` and `data/pulse_ai/pulsesoc_feature_map.json`
- `venv/bin/python scripts/market_and_crypto_intelligence_audit.py`
- `venv/bin/python scripts/pulsesoc_intelligence_collectors_phase2_audit.py`
- `venv/bin/python scripts/run_intelligence_collectors.py --stream market_pulse --dry-run --limit 5 --json`
- `venv/bin/python scripts/run_intelligence_collectors.py --all --dry-run --limit 5 --json`
- `git diff --check`
- `curl -fsS http://127.0.0.1:5069/health`

Market collector dry-run result:

- `POLYGON_API_KEY`, `FINNHUB_API_KEY`, and `ALPHA_VANTAGE_API_KEY` were not configured locally and were marked `skipped_config_missing`.
- Yahoo Finance returned `http_401` locally and was marked failed.
- The collector returned safely with no candidates instead of fabricating data or slowing user routes.

All-collector dry-run result:

- Runner returned `ok=true`.
- Crypto Pulse produced one ETH movement candidate from CoinGecko public data and kept Binance failure isolated.
- Market Pulse did not emit weak or fake alerts.
- World, Security, Technology, PulseSoc Discovery, and PulseSoc Pulse collectors continued independently.
