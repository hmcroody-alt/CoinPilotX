PULSESOC — PREMIUM CRYPTO INTELLIGENCE SUPER MISSION

RESULT: PARTIAL — every repository-controllable requirement is PASS; capped at PARTIAL solely because simulator verification and the push are impossible from this environment (details in BLOCKERS)

START SHA: 8b4e02e530e3128bdee7753b1dcb1d41e6c63dd1
END SHA: 77692675 (branch tip)
BRANCH: codex/premium-crypto-intelligence

COMMITS:
1. f1ab2be2 feat(premium): register crypto capabilities in canonical entitlements + server gate
2. 787071ae feat(crypto): portfolio intelligence service over existing portfolio backend
3. fc15bad1 feat(crypto): advanced alerts, market observations, mobile crypto API
4. aba707a5 feat(undx): read-only crypto intelligence tools
5. cf5f6041 feat(mobile): crypto alert center, advanced alert UI, premium portfolio
6. 77692675 feat(premium-ui): Crypto Intelligence section + cross-account isolation proofs

PUSH: BLOCKED — sandbox cannot reach github.com over SSH (`socat E CONNECT github.com:22: Forbidden`). Owner: `git push origin codex/premium-crypto-intelligence`

ENTITLEMENTS
ADVANCED ALERT CAPABILITY: premium.crypto.advanced_alerts
PORTFOLIO CAPABILITY: premium.crypto.portfolio_intelligence
Both seeded onto existing pulse_premium_monthly / pulse_premium_annual / pulse_premium_grandfathered plans — com.pulsesoc.premium.monthly and .annual inherit with NO new IAP SKU. Server-authoritative via services/crypto_premium_gate.py (fail-closed; denials are HTTP-200 premium_required, never 403/dev errors). Owner bypass reuses existing PULSESOC_OWNER_USER_IDS.
FREE USER: PASS (denied advanced capability; basic alerts fully functional; test-proven)
PREMIUM USER: PASS (test-proven, incl. end-to-end sqlite proof that an apple_app_store-sourced monthly projection grants both capabilities under BUSINESS_OS_ENTITLEMENTS=canonical)
RESTORE: PASS at repo level — entitlement is resolved server-side per request through the same canonical facade Restore refreshes; expired premium denied (existing 3-day grace respected). Live StoreKit restore journey pending device.

ALERT ENGINE
EXISTING ENGINE REUSED: YES — advanced branch lives inside evaluate_alert_rule() reusing the armed/latched machine, cooldown, repeat and trigger-seq dedup. No second engine, no second worker.
SUPPORTED CONDITIONS: price_above, price_below, price_crosses_above, price_crosses_below, price_move_pct, price_move_abs, volume_above, volume_below, volume_move_pct, market_cap_above, market_cap_below, market_cap_move_pct, portfolio_value_above, portfolio_value_below, portfolio_move_pct, allocation_above (legacy above/below/moves_up_percent/moves_down_percent/volatility_above untouched)
COMPOUND AND: PASS
COMPOUND OR: PASS
CROSSING: PASS (prev<=t AND cur>t edge semantics; per-condition state persisted in advanced_state; restart-safe, test-proven)
VOLUME: PASS
MARKET CAP: PASS
RECURRING: PASS (once / every_crossing / recurring)
COOLDOWN: PASS
DEDUP: PASS (existing trigger-key dedup preserved; no duplicate for identical observation)
Limits: free 5 basic rules, premium 100 total, ≤5 conditions/rule, windows 15–1440m, watchlist-wide rules premium-only. Structured JSON rules, strictly validated, no eval of any kind. Lapsed-premium advanced rules are skipped with recorded status, never deleted.

OBSERVATION SERIES
IMPLEMENTED: YES — services/market_observations.py + market_observations table (AUTO_PK_TABLES, unique asset_id+observed_at, indexed, idempotent schema)
SAMPLE SOURCE: the alert worker's existing quote fetch (appended in current_observed_value / advanced path — zero extra provider calls, no second poller)
CADENCE: the worker's real ~45s evaluation cycle
RETENTION: 7 days, throttled prune (max 1/hour) inside the existing worker cycle
15M WINDOW: PASS (real timestamps; nearest-to-window-start lookup)
1H WINDOW: PASS
INSUFFICIENT DATA: PASS — baseline valid only within ±20% of window length, else honest insufficient_data skip; never last-N-rows, never fabricated precision

PORTFOLIO
EXISTING BACKEND REUSED: YES — portfolio_items (amount, average_buy_price) + portfolio_snapshots; no duplicate backend, no WebView
NATIVE CLIENT: PASS (repo) — CryptoPortfolioScreen + typed src/api/cryptoPremium.ts over pulseApi(); visual pass pending simulator
TOTAL VALUE: PASS (server-computed from authoritative prices; client prices never trusted)
ALLOCATION: PASS (percentages + concentration; sums ≈100%, test-proven)
UNREALIZED P/L: PASS — only where average_buy_price exists; per-holding and total null otherwise, UI renders "—"
REALIZED P/L: NOT IMPLEMENTED (no transaction ledger exists — correctly refused)
HISTORY: PASS — real portfolio_snapshots only, honest coverage full/partial/none, no interpolation; hourly append-on-read cadence so history accrues; empty-history state honest
PORTFOLIO ALERTS: PASS — portfolio_value_above/below, portfolio_move_pct, allocation_above evaluate in the SAME engine via portfolio_intelligence
PRIVACY: strict owner scoping; cross-account isolation suite proves user A cannot read, mutate or delete user B's holdings, totals, cost basis, rules or history

UNDX
CRYPTO CAPABILITIES: crypto.portfolio.summary → pulsesoc.crypto_portfolio.summary; crypto.portfolio.history → pulsesoc.crypto_portfolio.history; crypto.alerts.activity → pulsesoc.crypto_alerts.activity; crypto.market.observations → pulsesoc.crypto_market.observations — all READ-ONLY, registered in the existing capability registry + PRODUCTION_TOOL_REGISTRY; no new agent stack
PORTFOLIO GROUNDING: PASS — real compute_portfolio_valuation data with freshness metadata (market_data_observed_at, calculated_at); honest *_unavailable errors; no fabricated prices/balances/cost basis
ALERT EXPLANATION: PASS — rules + trigger activity exposed for grounded "why did my alert trigger" answers; trigger detail premium-gated with the premium_required payload for honest upsell
CROSS-ACCOUNT ISOLATION: PASS — user scoping from authenticated user_id only; hostile arguments["user_id"] ignored (test-proven); no trade/transfer/mutation capability added

END-TO-END ALERT EVIDENCE
MARKET DATA: PASS (existing provider path + new sampled series)
EVALUATION: PASS (16 conditions + compound, in-engine, 23-test suite)
TRIGGER: PASS (edge semantics, cooldown, dedup, disabled-rule, restart persistence)
PERSISTENCE: PASS (rules, advanced_state, events, observations — restart-safe)
NOTIFICATION: PASS at unit level through the existing dispatch path with delivery stubbed (incl. notification-failure safety); live push delivery requires production — unproven here by design
HISTORY: PASS (paged mobile history endpoint; no internal/debug fields leaked)

TESTS
PYTEST: not runnable in sandbox (no PyPI; flask/pytest uninstallable) — equivalent stdlib suites run instead: PYTHONPATH=. python3 -m unittest over tests/crypto_premium/ → 87 tests OK (gate 14, portfolio 16, undx 21, observations 10, advanced engine 23, isolation 3); UNDX regression suites 127/128 (1 pre-existing werkzeug-missing sandbox error, unrelated); py_compile clean on bot.py + all touched modules. Owner: run python3 -m pytest tests/ -q once on a dependency-complete machine.
TSC: PASS (npx tsc --noEmit exit 0)
JEST: PASS (271 suites / ~4,450 tests incl. 17 new cryptoAlertForm tests + premium copy/accessibility suites)
I18N: PASS (validate-i18n OK, 11 locales 100% — 2369/2369; find-hardcoded-strings: 0 in all new files, touched files at baseline)
AUDIO/RTC GATE: PASS — "No protected real-time audio path changed (54 files inspected)" on the full committed diff; git diff --check clean

DEVICE
IPHONE 17 PRO MAX: BLOCKED — sandbox has no iOS toolchain/network and desktop terminal control is restricted. Owner journey: free account (basic alert works, advanced + portfolio locked with Premium gate), premium account (advanced compound + recurring rule creation, portfolio valuation, honest history, UNDX questions), cold relaunch persistence.
P3R7OR: NOT REQUIRED (per Stage 36 — do not expand scope for device tooling)

FINAL
WORKING TREE CLEAN: YES, except the 9 pre-existing uncommitted marketplace files, exactly per the owner's "leave untouched" decision. src/navigation/types.ts was staged SELECTIVELY: only the three crypto route types were committed; its marketplace hunk remains uncommitted in the working tree (verified). No scratch files, debug logs, credentials or financial exports committed.

KNOWN LIMITATIONS:
- Time-window alerts self-enable truthfully as the observation series accrues post-deploy; until then they return insufficient_data (by design).
- change_24h_pct and unrealized P/L are null whenever real data is absent — never estimated.
- Live notification delivery and StoreKit restore are unprovable pre-deploy.
- UNDX Premium-screen row for "UNDX Crypto Intelligence" is informational (no dedicated screen destination yet, consistent with the app's no-dead-buttons rule).

BLOCKERS (owner, in order):
1. git push origin codex/premium-crypto-intelligence (sandbox SSH forbidden)
2. rm .git/index.lock .git/HEAD.stale.* and stale refs/heads/codex/junk_*.lock (sandbox cannot unlink inside .git; commits used an alternate index — the real index is synced)
3. Deploy → confirm market_observations fills from alert_worker; smoke the mobile crypto endpoints
4. Simulator run (iPhone 17 Pro Max) per the DEVICE journey above; StoreKit sandbox purchase/restore unlocking both capabilities
5. python3 -m pytest tests/ -q on a dependency-complete machine

FINAL VERDICT: All 38 stages that can be satisfied from the repository are complete, tested and committed in 6 clean, isolated commits — no duplicate systems, no fabricated financial data, no realized P/L, free basic alerts intact, protected realtime/audio systems untouched. CONDITIONAL GO: push, deploy, and the simulator journey convert this PARTIAL to PASS.
