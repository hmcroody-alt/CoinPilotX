# Market Pulse → UNDX contextual handoff and return path

Mission: make the visible chip and the intelligence context the same object, and
stop UNDX from being a screen you have to kill the app to leave.

---

## ROOT CAUSE — CONTEXT

Two defects, and the first one is the opposite of what it looks like.

**The bridge already worked in the forward direction.** `src/undx/marketContext.ts`
parked a validated envelope, the chip rendered it, and `ChatScreen` attached it to
the first send. The server (`services/undx_market_context.py`) sanitised it,
persisted it per conversation in `pulse_ai_client_contexts`, and `merge_for_persist`
preserved it on every later turn. `resolve_asset` used it for deixis. So "tell me
more about it" *did* resolve to Bitcoin. The consume-once send is deliberate and
correct: re-sending would re-stamp a minutes-old price snapshot as fresh.

**Defect 1 — dismissal was local-only, so the chip was not a control.**
`clearMarketContext()` set a module variable to `null`. Nothing told the server,
which was still holding the envelope. `merge_for_persist` then took its
`elif stored and not is_expired(stored)` branch on the next message and kept
steering. The member tapped X, the words disappeared, and UNDX went on resolving
"how is it doing?" to the coin they had just said they were finished with — the
exact inversion of stage 7's requirement that UI state and intelligence state stay
synchronised. The chip described a context it could not end.

**Defect 2 — the asset id was a fabrication.** `buildMarketContextEnvelope` set
`id: symbol.toLowerCase()`, producing `"btc"`. The market layer calls Bitcoin
`"bitcoin"`. The client could not do better: `AssetQuote` in `src/api/watchlists.ts`
carries `symbol`, `name`, `image` and prices, and no id at all. So every downstream
consumer that had an id available was given a string the price engine does not
recognise, and identity fell back to matching on a display name — the collision the
id exists to prevent.

## ROOT CAUSE — NAVIGATION

`AssetDetailScreen.onAskUndx` called `navigation.navigate("Tabs", { screen: "PulseAI" })`.

`AssetDetail` is a **root Stack** screen (`AppNavigator.tsx:620`). `PulseAI` is a
**Tab** inside `Tabs`, and `Tabs` is itself a root Stack screen at index 0. React
Navigation's `navigate` to a route already in the stack *pops back to it*, so that
call destroyed the `AssetDetail` entry on the way. `PulseAiScreen` is a redirect
whose `useEffect` then called `parentNavigation.replace("Chat", …)`, consuming the
`Tabs` entry too.

The root stack was left holding exactly one entry. `goBack()` was a no-op, the tab
bar was gone with `Tabs`, and there was no route left to return to. Force-closing
the app was not a workaround — it was the only exit.

**The normal tab entry hit the same trap**: `[Tabs]` → replace → `[Chat]`, a
single-entry stack, dead Back. This was not specific to the drill-in.

---

## What changed

**One object, one place that turns it into request fields.** `buildUndxSendContext()`
in `marketContext.ts` is now the only way market fields reach a request. It returns
the parked envelope on a handoff turn, `market_context_cleared: true` on a dismissal
turn, and `{}` otherwise. The chip renders `peekMarketContext()`, and both read the
same parked state, so there is no way to attach an envelope the chip is not showing
and no way to dismiss the chip without the next request saying so. Stage 3's "do not
maintain displayContext + undxContext as independent values" is enforced by there
being no second value to maintain.

**Dismissal reaches the server.** `clearMarketContext()` arms a one-shot flag that
rides the next send. `merge_for_persist(..., cleared=True)` drops the stored envelope
rather than merely declining to return it, and `pulse_ai_service` now writes the row
on the clear path — the previous `if persisted_context:` guard would have left the
stored envelope standing when there was nothing left to persist. A dismissal that
arrives alongside a fresh envelope is a replacement, not an erasure: the member's
newest intent wins.

**Identity is resolved server-side.** `_canonical_asset_id` matches the symbol
against the same `market_pulse` board that `resolve_asset` and the price answers use,
so `BTC` becomes `bitcoin` deterministically and consistently with the thing that
will later be asked for a price. A client-claimed id is accepted only when the board
has nothing to say. This needed no API change and does not trust the client for
identity. The client gained an optional `assetId` input for any screen that does have
one, and still falls back to the lowercased symbol.

**The drill-in stops routing through the tab.** `AssetDetailScreen` now calls
`navigation.push("Chat", undxChatTarget({ returnTo: assetReturnTarget(...) }))`,
keeping `AssetDetail` underneath where `goBack()` finds it. `PulseAiScreen` keeps
using `replace` — it is a redirect, and pushing on top of it would bounce straight
back into `Chat` on the first Back press — but both entry points now build their
route params from the shared `undxChatTarget()` so they cannot drift apart.

**Back has three answers and never has none.** `goBackFromChat` tries `canGoBack()`,
then the recorded `undxReturn`, then `Tabs → Dashboard`. The third case is what
removes the trap for the tab entry, which legitimately has an empty stack beneath it.
`undxReturn` is a narrow `{ screen: "AssetDetail" }` union, not a route-name-and-params
bag: it travels through route state, which is not a place to accept an arbitrary
"navigate here" instruction (stage 21 — context describes a subject, never authority).

---

## Report fields

| Field | Result |
| --- | --- |
| CANONICAL HANDOFF TYPE | `MarketContextEnvelope` (`src/undx/marketContext.ts`), already existed; extended with `assetId` input. Return target is `UndxReturnTarget` (`src/undx/undxChatTarget.ts`), new. |
| MARKET ASSET ID PASSED | **YES** — and now canonical. Client sends `assetId` or the symbol; `_canonical_asset_id` upgrades it against the market board. |
| VISIBLE CHIP CONNECTED TO REAL CONTEXT | **YES** — chip renders `peekMarketContext()`, request is built by `buildUndxSendContext()` from the same parked state. |
| UNDX API RECEIVES ACTIVE CONTEXT | **YES** — `ui_context.market_context` on the handoff turn; server persists and preserves it thereafter. |
| UNDX CONTEXT COMPILER RECEIVES IT | **YES** — via the existing `grounding_block()` knowledge item and `resolve_asset()`. No parallel prompt system; no untrusted text concatenated into a system prompt. |
| PRONOUN/TOPIC RESOLUTION | **PASS (by existing covered behaviour, not re-observed)** — `resolve_asset` deixis is covered by the pre-existing `Coreference` suite, which this mission did not change. See the verification caveat below. |
| CHIP DISMISS CLEARS CONTEXT | **PASS (implemented and unit-tested; tests NOT EXECUTED)** — see caveat. |
| BACK TO SOURCE MARKET SCREEN | **PASS (implemented; NOT EXECUTED on device or in a render test)** — see caveat. |
| NORMAL UNDX ENTRY | **PASS, and improved** — tab entry unchanged in behaviour, no crypto context attached, and its Back is no longer dead. |
| TYPESCRIPT | **PASS** — `npx tsc --noEmit`, exit 0, run after all source edits. Re-run NOT OBSERVED after the two test files and the chip accessibility labels were added. |
| TESTS | **13 added, 0 EXECUTED.** 4 client context (`marketContext.test.ts`), 6 navigation/handoff (`undxChatTarget.test.ts`), 3 request-payload (`api/__tests__/pulseAiRequestContext.test.ts`), plus 6 backend cases in `tests/undx_agent/test_market_context_bridge.py`. |
| AUDIO/AGORA FILES CHANGED | **0** — no file in `config/realtime-audio-protected-paths.json` was touched, no `expo-av` or `AVAudioSession` call site added or moved. |
| COMMIT SHA | **NONE** — git is read-only in this environment (`.git/index.lock`: Operation not permitted). Nothing staged, nothing committed. |
| VERDICT | **PARTIAL** |

## Why PARTIAL and not PASS

The verification sandbox failed part-way through this mission and did not recover.
The first symptom was `ENOSPC: no space left on device` on a file write; every
subsequent shell call has returned `useradd failed: input/output error`. I retried
six times across the remainder of the work.

What that means concretely:

- `npx tsc --noEmit` **passed** on the full source change set — every edit to
  `ChatScreen.tsx`, `AssetDetailScreen.tsx`, `PulseAiScreen.tsx`, `types.ts`,
  `marketContext.ts` and `undxChatTarget.ts`. It has **not** been re-run since the
  two new test files and the chip's `accessibilityLabel` were added.
- **No Jest suite was run.** The 13 new client tests have never executed. Neither
  has the existing `marketContext.test.ts`, whose `afterEach` I changed from
  `clearMarketContext()` to `resetMarketContextForTests()` — necessary, because
  dismissal is now a pending instruction that would otherwise leak between tests,
  but unverified.
- **No pytest run.** The 6 new backend cases and the existing bridge suite are
  unexecuted. The `test_board_outage_...` case in particular asserts a `try/except`
  path I reasoned about rather than watched.
- **No i18n gate, no protection suite, no realtime-audio gate run.**

I am not going to call that a pass. The design is sound and the reasoning behind
each change is written into the code, but "it type-checks" is not "it works", and
this mission's whole subject is the difference between a label that says something
and a system that does it.

**Before merge, run:**

```
cd mobile-native && npx tsc --noEmit
cd mobile-native && npx jest src/undx src/api
cd mobile-native && npx jest src/screens          # exceeds a 45s cap; run unbounded
cd mobile-native && node scripts/validate-i18n.mjs
python3 -m pytest tests/undx_agent/test_market_context_bridge.py
python3 scripts/protection/run_protection_suite.py
python3 scripts/realtime_audio_change_gate.py --base origin/main --head HEAD
```

## Two things I did not do, deliberately

**No i18n for the chip.** `ChatScreen.tsx` contains no `useTranslation` and no `t()`
call anywhere — "Back to conversations", "UNDX is typing", "PULSE LINK" and every
other string in the file are hardcoded English. Stage 20 says not to hardcode English
*if the existing i18n system applies*; in this screen it does not, and introducing a
single translated string into a file of untranslated ones would be inconsistent
without fixing the screen, which is outside a mission scoped to context and
navigation. The accessibility labels are improved: the chip now announces
"Discussing Bitcoin, BTC" rather than letting a screen reader read the interpunct,
and dismiss says "Stop discussing Bitcoin" rather than "…BTC".

**No render test of `ChatScreen`.** Stage 17 asks for proof that the *request*
carries structured context, and warns against accepting a test that only checks the
chip is visible. The payload test asserts at the `pulseApi` boundary — the last place
the client speaks — which is a stronger claim than a rendered chip and does not
require booting a 2,900-line screen. The Back priority order in `goBackFromChat` is
consequently **not** covered by a test; it is three branches of plain logic, but that
is an argument for reading it, not for calling it verified.

## Files changed

```
mobile-native/src/undx/marketContext.ts              pending-clear, buildUndxSendContext, assetId
mobile-native/src/undx/undxChatTarget.ts             NEW — shared entry params + return target
mobile-native/src/navigation/types.ts                Chat.undxReturn
mobile-native/src/screens/AssetDetailScreen.tsx      push Chat, carry return target
mobile-native/src/screens/PulseAiScreen.tsx          use the shared helper
mobile-native/src/screens/ChatScreen.tsx             send context, Back priority, a11y labels
services/undx_market_context.py                      _canonical_asset_id, merge_for_persist(cleared=)
services/pulse_ai_service.py                         read the clear flag, write on the clear path
mobile-native/src/undx/__tests__/marketContext.test.ts        +4 cases
mobile-native/src/undx/__tests__/undxChatTarget.test.ts       NEW, 6 cases
mobile-native/src/api/__tests__/pulseAiRequestContext.test.ts NEW, 3 cases
tests/undx_agent/test_market_context_bridge.py                +6 cases
```

No file under `config/realtime-audio-protected-paths.json`. No Agora, Live, Premium,
Private Office or Messenger-idempotency file touched.
