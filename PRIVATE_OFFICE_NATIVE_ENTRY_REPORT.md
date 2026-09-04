# PRIVATE OFFICE NATIVE ENTRY + PRIVATE FACTS SCREEN — FINAL REPORT

Task #227 · P0 PRODUCT SURFACE · 2026-09-03
Branch `codex/emergency-live-audio-recovery`

---

## VERDICT LINES

| Gate | Result |
| --- | --- |
| PRIVATE OFFICE SCREEN | **PASS** |
| PRIVATE FACTS SCREEN | **PASS** |
| PREMIUM ENTRY | **PASS** |
| SERVER-DRIVEN VISIBILITY | **PASS** |
| CLIENT TIER INFERENCE (0 NEW VIOLATIONS REQUIRED) | **PASS — 0 new violations** |
| KNOWLEDGE MAP DESTINATION | **PASS** |
| 11-LOCALE i18n | **PASS — 2897/2897 keys, 11 locales** |
| EXISTING PREMIUM PRESERVED | **PASS** |
| DEVICE QA | **BLOCKED — owner-run, not performed** |
| BUSINESS.CAMPAIGN.PAUSE CONFLICT | **PRE-EXISTING / PARKED** |
| **FINAL VERDICT** | **CODE COMPLETE — NOT SHIPPABLE UNTIL DEVICE QA PASSES** |

The hard rule of this task is that PASS requires Premium → Private Office →
Private Facts working against real canonical server state **on device**. That
step has not been performed, so the mission verdict cannot be an unqualified
PASS. Everything that can be established without a phone is established below.

---

## WHAT WAS BUILT

Three files are new, and they are the whole surface:

- `mobile-native/src/api/privateOffice.ts` — the client contract. `getPrivateOfficeOverview` and `getPrivateFacts` over the existing canonical routes, plus `parseOverview` / `parseProductState` / `parseFact` and the `UNKNOWN_OVERVIEW` sentinel.
- `mobile-native/src/screens/PrivateOfficeScreen.tsx` — the umbrella entry.
- `mobile-native/src/screens/PrivateFactsScreen.tsx` — the first real capability.

Everything else is additive wiring: `PremiumCenterScreen.tsx` gains a
`PrivateOfficeEntrySection` (+85 lines, 0 deletions), `AppNavigator.tsx` gains
two `Stack.Screen` registrations, `types.ts` gains two `RootStackParamList`
entries, `linking.ts` gains two deep-link paths, and the 22 locale catalogs gain
the Private Office string family.

No existing screen lost a line of behaviour.

---

## SERVER-DRIVEN VISIBILITY

The property that matters is that neither screen holds an opinion. Both render
`office.product_state()` as the server emits it:

- the child list is whatever the server sent — a capability this build knows a
  name for but the server omitted is not drawn, and a capability this build has
  never heard of **is** drawn rather than dropped;
- `opens` decides tappability. The screen does not re-derive it from
  `availability`, because a local re-derivation would be a second authority;
- `PROVIDER_REQUIRED`, `NOT_IMPLEMENTED` and `TEMPORARILY_DISABLED` render as
  three different reasons. Collapsing them would make `private_shield` read as
  "we are watching and would tell you", which is false;
- `ENTRY_UNKNOWN` is not `ENTRY_UNAVAILABLE`. A resolver that did not answer
  offers a retry rather than reporting an empty office — "could not look" and
  "looked and found nothing" never share a shape.

The Premium entry follows the same rule: `PrivateOfficeEntrySection` fetches the
overview and renders the server's `state` and `upgradeTier`. The hierarchy is
not hardcoded in the screen.

---

## STAGE 11 — THE KILL SWITCH IS REAL

`private_facts` is the only Private Office row that is both `IMPLEMENTED` and
carries a `flag_env`. That combination is what makes `PRIVATE_FACTS_ENABLED`
load-bearing, and before this task **nothing in the repo tested it** — a grep
found the flag in exactly one file (`feature_matrix.py:153`) and zero tests.

`tests/private_office/test_private_facts_kill_switch.py` (new, 9 tests, all
passing) closes that. The failure it exists to prevent is a *partial* off: four
places independently decide whether a member reaches this capability — the
matrix, the shared access decision, the product state the native screen renders,
and the HTTP gate. A switch that flipped three of them presents as "the office
lists Private Facts, the member taps it, the route 404s" — a broken app rather
than a disabled feature, on the one day the switch is being used.

Proven:

- off at **every** tier including `PRIVATE_OFFICE`, for `false/0/off/no/FALSE/Off`;
- **default ON** — absent or empty leaves the feature available, so the feature
  does not vanish on a host that simply never set the variable;
- off is `TEMPORARILY_DISABLED`, never `UPGRADE_REQUIRED` (a disabled capability
  must not be sold) and never `NOT_IMPLEMENTED` (it is built and it is coming back);
- `minimum_tier` is dropped, so no surface can render an upgrade prompt from a
  leftover field;
- a degraded resolver still reports `UNAVAILABLE`, not `FEATURE_DISABLED` —
  independent failures stay independent and the retry affordance survives;
- the switch changes no other row in the office.

Native cannot bypass it: the client has no path to the store that does not pass
the HTTP gate, and `_gate()` maps `FEATURE_DISABLED` to 404 with
`state: "FEATURE_DISABLED"`, which `getPrivateFacts` matches to the
`FEATURE_DISABLED` arm.

---

## STAGE 12 — CLIENT TIER INFERENCE

`npx jest src/entitlements` — **33 passed**, 0 new violations.

This result was checked for vacuousness before being reported.
`noClientTierInference.test.ts` scans the native `src` tree using **explicit file
allowlists with deliberately no directory wildcard** — the comment in the file
says a wildcard "would let a new file dropped into `screens/` bypass the boundary
on the day it is created". Neither new Private Office screen appears on any
allowlist, so the pass is a real result rather than an exemption.

---

## STAGE 13 — EXISTING PREMIUM PRESERVED

Full native suite, run as 12 shards:

**320 suites · 5,249 tests · 5,248 passed · 1 skipped · 0 failed.**

`PremiumCenterScreen` specifically: 4 suites, **63 passed**. Targeted regression
across Portfolio, alerts, Briefings, Market Pulse, watchlists and entitlements:
11 suites, **137 passed**.

Read against the source, every pre-existing Premium section is still present and
still points where it did: plans, billing, benefits, "not yet", the crypto
intelligence section (alerts / portfolio / watchlists / UNDX / Market Pulse), the
command center (verified, UNDX, creator, support, and the four locked rows plus
the founder row), and the free section. `BriefingsHub` remains registered in the
navigator. Private Office is a new section between crypto intelligence and the
command center. **Additive, confirmed.**

---

## STAGE 14 — TEST MATRIX

### Native — 23 new tests

`PrivateOfficeScreen.test.tsx` (9 passed): heading and purpose; the children the
server sent and no others; an unknown capability rendered rather than dropped;
navigates when `opens` is true; does **not** navigate when `opens` is false;
three distinct unavailable reasons; upgrade only when the server says so, naming
the tier it sent; degraded resolve says "could not confirm" with a retry rather
than showing an empty office; retry re-reads the server.

`PrivateFactsScreen.test.tsx` (14 passed): LOADING before an answer exists;
READY under the domain the record declares; grouping in server row order; EMPTY
with no invented rows; **UNAVAILABLE is not EMPTY**; NOT_ENTITLED naming the
tier; generic upgrade copy when no tier was named; switched-off / never-built /
unreadable / broken kept as four separate answers; retry; the provenance sheet
explains **without exposing `source_id`**; "source not recorded" rather than a
blank line; closing the sheet leaves the list undisturbed; a stale record marked
and the others not; an unknown verification word rendered rather than blanked.

Both suites stub **only** the network read and keep the real parsers via
`jest.requireActual`. A stubbed parser would leave a suite that proves the stub
agrees with itself.

### Backend — one file per process

| File | Result |
| --- | --- |
| `test_entitlement_routes.py` | 12 passed |
| `test_private_facts_capability.py` | 1 passed |
| `test_private_facts_kill_switch.py` | **9 passed (new)** |
| `test_private_observability.py` | 1 passed |
| `test_private_office_routes.py` | 1 passed |
| `test_private_office_surface.py` | 1 passed |
| `test_private_retrieval.py` | 1 passed |
| `test_private_substrate.py` | 1 passed |
| `test_private_write_boundary.py` | 4 passed |
| `test_tier_resolver.py` | 29 passed |
| `tests/undx_agent/test_knowledge_map.py` | 37 passed |

These must be run one file per process: each sets `DATABASE_URL` at import time,
so a single pytest process lets the first module's temp DB win and produces
spurious failures. Several files also use the house pattern of a `main()` holding
dozens of assertions behind a single `test_*` wrapper, which is why "1 passed" is
the correct count rather than under-collection.

---

## STAGE 15 — KNOWLEDGE MAP

`private.facts.list` now names `native_screen="PrivateFacts"`, and
`NATIVE_ROUTES` declares both `PrivateOffice` → `/pulse/private-office` and
`PrivateFacts` → `/pulse/private-office/facts`, matching `linking.ts`. The
umbrella is declared beside the leaf because the leaf is only ever reached
through it, and a map that knew one but not the other would describe half a
journey.

This is a real destination rather than a link that lands on a refusal: the screen
renders the same `services.private_office.access` decision the capability does, so
a member who can get an answer from the agent can open the screen that holds it,
and a member who cannot gets the same reason from both.

**One stale claim fixed in this pass.** The record's `known_limitations` still
said "While `private_facts` is NOT_IMPLEMENTED the capability is registered but
refuses every caller" — untrue since Stage 9-10 flipped the row. Replaced with
the accurate statement: the row is IMPLEMENTED and reaches PRIVATE and above, but
carries the `PRIVATE_FACTS_ENABLED` kill switch, and with the switch off it
refuses every caller including PRIVATE_OFFICE as `FEATURE_DISABLED` rather than
as an upgrade prompt. Knowledge-map and capability suites re-run after the edit:
**38 passed**.

---

## GATES

- `npx tsc --noEmit` — **clean**.
  This caught a real defect that jest could not: `ScreenState` in
  `PrivateFactsScreen.tsx` omitted `"EMPTY"` even though two code paths wrote it
  and a third compared against it. Babel strips types, so the jest suite was
  green over a latent bug. Fixed by adding the member. Worth carrying forward:
  `npm run verify` catches things `npx jest` alone does not.
- `node scripts/validate-i18n.mjs` — **11 locales, 2897/2897, OK.** Four advisory
  warnings (ar zero/one/two forms; es/fr/pt `many` forms) are pre-existing and
  unrelated. No raw English fallback in production UI.
- `scripts/realtime_audio_change_gate.py --base origin/main --head HEAD` — flags
  `mobile-native/app.json`, `package.json`, `package-lock.json` under
  `dependency_watch`. **Not introduced by this mission**: `git log` attributes all
  three to the pre-existing commit `8c50a246` ("establish shared native media
  delivery foundation"), and this mission's working tree touches none of them.
  No protected audio path was edited; no `Audio.setAudioModeAsync` or
  `AVAudioSession` call was added.

---

## BUSINESS.CAMPAIGN.PAUSE — PRE-EXISTING / PARKED

`undx_capability_registry.authorization_surface()` still raises
`AuthorizationRecordConflict` with
`capability_id='business.campaign.pause'`, `field='knowledge_map'`,
`detail='registered capability has no record in the product knowledge map'`.

Confirmed this pass that the conflicting capability is `business.campaign.pause`
and **not** `private.facts.list`. Per the task instruction, not fixed here.

---

## STAGE 16 — DEVICE QA (BLOCKED, OWNER-RUN)

Not performed. Required before this ships:

1. Sign in on a real iPhone as an account whose server-resolved tier is PRIVATE or above.
2. Profile → Premium. Confirm the Private Office section appears, and that it appears because the server said so.
3. Confirm every pre-existing Premium section is still present and still opens.
4. Tap Private Office. Confirm the heading, the subhead, an AVAILABLE section containing Private Facts, and a COMING LATER / PROVIDER REQUIRED section whose rows do not look active.
5. Tap Private Facts. Confirm LOADING then READY (or EMPTY, with "No verified information has been added yet." and no placeholder rows).
6. Confirm facts are grouped by domain and each row shows only type, value, verification state and observed date.
7. Open "Why do you know this?" on a record. Confirm source type, observed date and confidence appear, and that no internal locator is visible.
8. Back out of the sheet, then back out of Private Facts, then back out of Private Office. Confirm back navigation works at every level.
9. Set `PRIVATE_FACTS_ENABLED=false` on the server. Reopen Private Office. Confirm Private Facts moves to unavailable as TEMPORARILY_DISABLED, does not open, and shows no upgrade prompt.
10. Restore the flag. Confirm the entry reopens with no redeploy and no data touched.
11. Sign in as a PREMIUM account. Confirm the upgrade path names the tier the server sent, and that no fact data is reachable.

---

## HARD RULE, RESTATED

> Do not call this complete because the screen exists. PASS only when:
> Premium → Private Office → Private Facts works against REAL canonical server
> state on device.

The screen exists, is server-driven, has 23 tests pinning the properties that
would otherwise be invisible in review, and does not bypass the kill switch. It
has not been opened on a phone. **Code complete; not shippable until step 16
passes.**
