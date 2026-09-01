# Device Acceptance — Market Pulse → UNDX Context Bridge (d0d57de7)

Prep on the Mac: `cd mobile-native && npx expo start --dev-client`, open the dev
client on the QA iPhone and connect to Metro so the phone runs the d0d57de7 JS.

## Stage 2 — Launch
- [ ] App starts, session preserved, no crash, tabs navigate normally

## Stage 3 — ETH handoff
Market Pulse → Ethereum → confirm real price + chart → tap **Ask UNDX**
- [ ] Canonical UNDX chat opens (no new/duplicate crypto screen)
- [ ] No fake user message inserted
- [ ] Chip reads "Discussing Ethereum · ETH"
- [ ] Navigation immediate

## Stage 4 — Coreference
Ask: **"How much is it now?"**
- [ ] "it" = ETH, live price returned, freshness honest, no fabrication, no company-metric answer

## Stage 5 — Comparison
Ask: **"Compare it to Bitcoin."**
- [ ] ETH stays the subject, BTC resolved explicitly, both grounded

## Stage 6 — Range continuity
Ask: **"What about the last 30 days?"** → 30D facts without restating ETH
Then: **"Go back to today."** → 24H semantics return
- [ ] Both hold

## Stage 7 — Watchlist
Ask: **"Am I already watching it?"**
- [ ] Answer matches YOUR watchlist state exactly (true or false)

## Stage 8 — Governed write
Ask: **"Create an alert if it drops below 2300."**
- [ ] ETH resolved from context, confirmation prompt appears, NO auto-write

## Stage 9 — Explicit override
Ask: **"What is SOL doing?"** → SOL wins. Then **"Back to ETH."** → ETH restores.
- [ ] Both hold

## Stage 10 — Replacement
Leave chat → Market Pulse → Bitcoin → Ask UNDX
- [ ] Chip now BTC; ask **"What about its dominance?"** → BTC dominance, not ETH

## Stage 11 — Dismiss
Tap ✕ on the chip
- [ ] Chip gone, context stops steering, history intact

## Stage 13 — Snapshot vs live
Note the ETH price at handoff; wait a few minutes; ask **"How much is it now?"**
- [ ] Fresh value via live path, not the old handoff number echoed

## Stage 15 — Non-crypto override
From crypto context ask: **"How does PulseSoc Marketplace work?"**
- [ ] Product answer; crypto context doesn't hijack it

## Stage 18 — Cache behavior
Ask **"How much is ETH now?" / "Still?" / "What about now?"** in quick succession
- [ ] Answers arrive fast; no visible per-turn provider stall

## Stage 19 — Restart
Background + reopen the app mid-conversation
- [ ] Conversation continuity as designed (server-persisted context survives)

## Stage 20 — Full sequence (the acceptance flow)
Market Pulse → Ethereum → Ask UNDX → "How much is it now?" → "Compare it to
Bitcoin." → "What about the last 30 days?" → "Am I already watching it?" →
"Create an alert if it drops below 2300."
- [ ] Every step works without ever typing "Ethereum"

Already verified without a device (no action needed): TTL expiry, provider
failure honesty, write governance, no new CoinGecko client (Stages 12/14/16/17).
