# Market Pulse → UNDX — landing / verification attempt

Second pass. The implementation from the first pass is intact and unmodified;
this pass tried to verify and commit it, and could not.

---

## Mission D — scope and status block

```
IDENTITY SCOPE:                AUTHORITATIVE WITHIN CURRENT REACHABLE
                               MARKET PULSE UNIVERSE
CURRENT REACHABLE UNIVERSE:    services/market_pulse.py::_board() — capped at 80 assets
SEARCH:                        operates over the same reachable board
OUTSIDE-TOP-80 ASSET DETAIL:   UNREACHABLE IN CURRENT PRODUCT FLOW
OUTSIDE-TOP-80 HANDOFF:        NOT A RELEASE BLOCKER
REGISTRY EXPANSION:            NOT PART OF THIS MISSION
SOURCE CHANGES FROM RULING:    0
CURRENT VERDICT:               PARTIAL / HOLD FOR VERIFICATION
```

### Next required action after environment recovery

```
1. preserve patch first
2. inspect diff / branch
3. run backend tests
4. run native verify / rendered navigation test
5. run TypeScript
6. run i18n
7. run protection gate
8. simulator acceptance
9. surgically stage + local commit only   (PUSH: NO)
```

One note on step 4: the **rendered navigation test does not exist yet**. The
mission's earlier pass deliberately asserted at the `pulseApi` boundary instead
(Stage 17 — a rendered-chip test is the bug, not the proof), and recorded the
`goBackFromChat` branch order as read-but-untested. Writing that render test is
new work, not a re-run, and it is held until the sandbox can execute it — a
navigation test authored blind would be the same category of claim this mission
exists to reject.

---

## Stage 0 — implementation preserved

**YES.** Nothing destructive was run. No `git reset`, `git restore`, `git
checkout`, `git clean`, `git stash` or `git add`. The working tree at
`/Users/hmcherie/Desktop/CoinPilotX` holds every change exactly as the first pass
left it.

The requested out-of-repo patch (`git diff > /tmp/…patch`) **could not be
produced**: computing a diff requires git, and git requires a shell. I did not
attempt any substitute that would touch the index. Preservation here is by
non-interference rather than by snapshot — weaker than the mission asked for, and
worth saying plainly.

## Stage 1 — environment health

**SANDBOX RECOVERED: NO.**

Every shell call in this pass returned:

```
resume: RPC error: ensure user: useradd failed: fork/exec /usr/sbin/useradd: input/output error
create: RPC error: ensure user: useradd failed: fork/exec /usr/sbin/useradd: input/output error
```

This is not a timeout or a busy worker. The VM cannot create its own user
account because of a block-device I/O error, so no process starts at all —
`df`, `git status`, `node --version` and `python --version` are equally
unreachable. The first symptom, earlier, was `ENOSPC: no space left on device`
on a file write, which is consistent with the VM's backing disk having filled
and then failed.

I stopped retrying after this pass's attempts, per the mission's instruction.

**Moving verification elsewhere was not possible.** The mission's fallback is a
healthy checkout or worktree — but creating one is itself a shell operation, and
the only other execution route available in this session is the computer-use
Terminal, which is granted at the click-only tier: I can click a Run button, I
cannot type a command. There is no second environment to move to.

**What you can do:** free space on the machine and restart the session's
workspace. The VM disk is the thing that filled, not the repo — the repo changes
are on your Mac's disk and are fine.

## Stages 2–19 — what could be verified without execution

Nothing that requires running code was verified. What follows is static review by
reading the files, which is evidence about the code and not about its behaviour. I
have kept the two apart deliberately.

### Stage 2 — diff ownership audit (by inspection)

| Group | Files |
| --- | --- |
| MARKET PULSE | `mobile-native/src/screens/AssetDetailScreen.tsx` |
| UNDX CONTEXT | `mobile-native/src/undx/marketContext.ts`, `mobile-native/src/undx/undxChatTarget.ts` (new) |
| UNDX BACKEND CONTEXT | `services/undx_market_context.py`, `services/pulse_ai_service.py` |
| NAVIGATION | `mobile-native/src/navigation/types.ts`, `mobile-native/src/screens/PulseAiScreen.tsx`, `mobile-native/src/screens/ChatScreen.tsx` |
| TESTS | `src/undx/__tests__/marketContext.test.ts`, `src/undx/__tests__/undxChatTarget.test.ts` (new), `src/api/__tests__/pulseAiRequestContext.test.ts` (new), `tests/undx_agent/test_market_context_bridge.py` |
| REPORT | `MARKET_PULSE_UNDX_CONTEXT_REPORT.md`, this file |

Agora 0. Calls 0. Live 0. Audio 0. Premium 0. Private Office 0. Messenger 0
(`ChatScreen.tsx` is the UNDX chat surface and the change is to context and Back;
no messenger idempotency path touched). Unexpected unrelated files 0.

This audit is by inspection of the file list, **not** by `git diff --name-only`.

### Stage 4 — canonical identity, and a limitation the first report did not state

`sanitize_market_context` calls `_canonical_asset_id(symbol, asset_raw.get("id"))`,
which matches the symbol against `_board_assets()` and returns the provider's id.
For BTC that is `bitcoin`, and a client claiming `"ethereum"` for BTC is overruled.

**But `_board_assets()` is `market_pulse.market_rows("all", 80)` — the top 80 by
rank.** An asset outside that window falls through to `symbol.lower()`. The first
report's "canonical id = bitcoin" was stated as a universal guarantee; it is not,
and that overstatement is corrected here.

**Follow-up research changes what that bound means.** See "The top-80 bound"
below: the window is not a gap in the resolver, it is the size of the product's
entire reachable asset universe.

### Scope ruling — the reachable asset universe

The top-80 board is the authoritative reachable Market Pulse asset universe for
this mission. Identity resolution is therefore specified, and certified, against
that universe:

```
WITHIN the reachable universe:
  symbol / client-supplied id
    → server canonical resolution against the board
    → authoritative canonical asset id

OUTSIDE the reachable universe:
  UNREACHABLE BY CURRENT PRODUCT FLOW
```

Consequently the outside-top-80 case is **not a release blocker** for the Market
Pulse → UNDX contextual handoff. The registry is not widened, and no source
behaviour is changed while the sandbox is dead. Building a complete asset
registry is separate work, logged below and not attempted here.

### The top-80 bound — the evidence behind that ruling

`services/market_pulse.py:103`:

```python
return market_data.live_market_board(category=sort_key, limit=max(1, min(int(limit or 50), 80)))
```

The same cap appears at lines 248 (`trending`), 295 (`snapshot`), 320 and 332
(`search`) and 355 (`asset_intelligence`). `search()` searches the same 80-row
board, and its own docstring says resolution "stays server-side in
`coingecko_client` and the board — there is no second resolver."

There is no complete asset registry in this backend. The consequence is that an
asset outside the top 80 cannot be searched, so its AssetDetail screen cannot be
opened, so "Ask UNDX" on it cannot be reached. The rank window is not a leak in
`_canonical_asset_id` — it is the boundary of everything a member can navigate to.

So identity resolution is canonical across the product's entire reachable asset
universe. That is a weaker sentence than "canonical, universally", and it is the
true one. Widening the lookup is not possible without first building a registry
that does not exist, which is separate work and is logged as such rather than
attempted here. `_canonical_asset_id` is left unmodified: adding an explicit
unresolved-identity marker would be a behaviour change written with no ability to
execute a single test against it, and the mission's own instruction is not to
modify behaviour until the 20 tests can run.

### Stage 6 — clear-when-nothing-else-to-persist

`pulse_ai_service.py:953` reads `if persisted_context or (market_cleared and
stored_market):`. The second clause is exactly the case the stage names: the
envelope was the only thing stored, clearing empties `persisted_context`, and
without that clause the `INSERT … ON CONFLICT` would be skipped and yesterday's
row would survive the dismissal. A backend regression test for it exists
(`test_dismissal_drops_the_stored_context_not_just_this_turn`), unexecuted.

### Stage 9 — Back priority

`goBackFromChat` in `ChatScreen.tsx:345` is three branches in the required order:
`navigation.canGoBack()` → `goBack()`; else `route.params.undxReturn` →
`navigate(screen, params)`; else `navigate("Tabs", { screen: "Dashboard" })`.
There is no path through it that does nothing. That is a reading of three
branches of plain logic — it is **not** a test, and the mission was right to call
its absence out in the first pass. It is still absent.

### Stage 12 — governance

No changed file touches entitlement, policy or capability. `undx_agent_policy.py`,
`undx_architecture.py` and the Premium and Private Office surfaces are untouched.
The envelope reaches the model as a knowledge item through the pre-existing
`grounding_block()`, which is subject to the same fact policy it always was. The
"Bitcoin subject preserved, Premium crypto intelligence still refuses" case is
therefore unchanged by this mission rather than newly proven by it.

## Stage 13 — the test count was wrong

The first report said 13 new tests. **It is 20.** Recounted from the files:

| File | Added |
| --- | --- |
| `src/undx/__tests__/marketContext.test.ts` | 5 |
| `src/undx/__tests__/undxChatTarget.test.ts` | 6 |
| `src/api/__tests__/pulseAiRequestContext.test.ts` | 3 |
| `tests/undx_agent/test_market_context_bridge.py` | 6 |
| **Total** | **20** |

**0 of 20 have been executed.** Two risks I can see by reading, which is precisely
why unexecuted tests are not evidence:

- `pulseAiRequestContext.test.ts` imports the real `api/messenger.ts`, which pulls
  `./config` (expo-constants) and `expo-file-system`. I mocked file-system and
  async-storage; whether `jest-expo` covers the rest is unverified. If it does not,
  that file fails to load — which looks identical to a failing assertion until you
  read the output.
- The existing `marketContext.test.ts` had its `afterEach` changed from
  `clearMarketContext()` to `resetMarketContextForTests()`. That change is
  necessary — dismissal is now a pending instruction that would leak between
  tests — but it means the pre-existing suite in that file is also unverified.

## Stages 14–19 — gates

| Gate | Result |
| --- | --- |
| TypeScript (`tsc --noEmit`) | **PASS on the source change set only.** Exit 0, run in the first pass after every edit to `ChatScreen.tsx`, `AssetDetailScreen.tsx`, `PulseAiScreen.tsx`, `types.ts`, `marketContext.ts`, `undxChatTarget.ts`. **NOT re-run** since the three test files and the chip accessibility labels were added. |
| Jest (scoped) | **NOT RUN** |
| Jest (full affected) | **NOT RUN** |
| pytest (scoped) | **NOT RUN** |
| pytest (full affected) | **NOT RUN** |
| i18n (`validate-i18n.mjs`) | **NOT RUN** |
| Protection suite | **NOT RUN** |
| `realtime_audio_change_gate.py` | **NOT RUN** |

On Stage 16: no new hardcoded string was introduced into a localized surface.
`ChatScreen.tsx` contains no `useTranslation` and no `t()` anywhere — the chip sits
among "Back to conversations", "UNDX is typing" and "PULSE LINK". ChatScreen
localization debt is logged here as separate work and was not taken on.

## Stages 20–21 — device acceptance

**SIMULATOR: NOT RUN.** **PHYSICAL DEVICE: NOT RUN.** Cases A through E are
unobserved. A simulator run needs a Metro bundler, which needs a shell.

## Stage 22 — commit

**NOT DONE.** No shell, so no `git add`, no `git commit`, no SHA. Nothing is
staged. The change set is uncommitted in the working tree.

---

## FINAL REPORT

```
IMPLEMENTATION PRESERVED:                     YES
SANDBOX RECOVERED:                            NO
CANONICAL CONTEXT PIPELINE:                   NOT VERIFIED (static review consistent)
BITCOIN ID:                                   bitcoin — by code path, unexecuted;
                                              top-80 board only, else falls back to symbol
CANONICAL ID INDEPENDENT OF TOP-80:           NO — and out of scope: the top-80 board
                                              IS the reachable asset universe
CANONICAL ID WITHIN REACHABLE UNIVERSE:       AUTHORITATIVE (by code path, unexecuted)
OUTSIDE-TOP-80 ASSET:                         UNREACHABLE BY CURRENT PRODUCT FLOW —
                                              not a release blocker
SERVER CLEAR:                                 NOT VERIFIED (implemented, test written, unexecuted)
EMPTY CLEAR PERSISTENCE:                      NOT VERIFIED (implemented, test written, unexecuted)
VISIBLE CHIP / REQUEST / SERVER SYNCHRONIZED: NOT VERIFIED (single-source by construction)
STACK RETURN:                                 NOT VERIFIED (implemented, no test)
RECORDED RETURN TARGET:                       NOT VERIFIED (implemented, no test)
DASHBOARD FALLBACK:                           NOT VERIFIED (implemented, no test)
NORMAL UNDX ENTRY:                            NOT VERIFIED (implemented, no test)
NEW TESTS:                                    0 / 20 executed  (count corrected from 13)
JEST:                                         NOT RUN
PYTEST:                                       NOT RUN
I18N:                                         NOT RUN
PROTECTION:                                   NOT RUN
TYPESCRIPT:                                   PASS on source; NOT re-run after test files
AGORA/AUDIO FILES CHANGED:                    0
COMMIT SHA:                                   NONE
FINAL VERDICT:                                PARTIAL / HOLD FOR VERIFICATION
```

PARTIAL is the mission's own definition: the implementation remains valid and the
verification environment remains blocked. It is not FAIL — the three FAIL
conditions (cosmetic local-only context, identity manufactured from ticker, Back
able to strand) are each addressed in the source. It is not PASS, because not one
of the fourteen PASS conditions has been observed, and the whole subject of this
mission is the gap between a thing that says it works and a thing that does.

## To land it

```
# free disk space, restart the workspace, then from the repo root:
git diff > /tmp/market-pulse-undx-context-recovery.patch   # preserve first
git status --short && git diff --stat

cd mobile-native
npx tsc --noEmit
npx jest src/undx src/api                    # the 14 new client tests
npx jest src/screens                          # slow; run without a timeout
node scripts/validate-i18n.mjs
cd ..
python3 -m pytest tests/undx_agent/test_market_context_bridge.py
python3 scripts/protection/run_protection_suite.py
python3 scripts/realtime_audio_change_gate.py --base origin/main --head HEAD
```

Then the simulator cases A–E, then commit. Suggested message:

```
fix(undx): synchronize market handoff context and return navigation
```
