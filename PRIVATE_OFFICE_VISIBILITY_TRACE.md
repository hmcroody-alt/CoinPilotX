# Private Office visibility — Stage 0–3 trace

**Mission:** PULSESOC PREMIUM → PRIVATE OFFICE VISIBILITY RECOVERY (P0)
**Date:** 2026-09-03
**Status:** Stages 0–3 complete. Stage 4 held — see "Decision required".

---

## Stage 0 — Baseline

| | |
|---|---|
| Branch | `release/full-sweep-20260826` |
| HEAD | `16d4a98aa22c0c60e9a67b3090cf9a046528df94` (2026-08-31) |
| origin/main | `61b2d07cb60d5ae2bf1dde692b05a2a791a437de` (2026-09-02) |
| Divergence | 36 commits on origin/main not in HEAD; 2 on HEAD not in origin/main |
| App version | `1.0.1`, buildNumber `19` |

Working tree left untouched. Modified: `bot.py`, `mobile-native/src/api/premium.ts`,
`ProfileHeader.tsx`, `AppNavigator.tsx`, `ProfileScreen.tsx`, `session/auth.ts`,
`services/business_os/entitlements/schema.py`. Untracked: `services/private_office/`,
`services/private_office_routes.py`, `tests/private_office/`, `mobile-native/src/entitlements/`,
two `PRIVATE_OFFICE_*.md` maps. No livestream, video-call, audio-call or realtime-protected
path was read for modification or touched.

---

## Stage 1 — Server entitlement truth

The server chain is present and correct.

`bot.py:1288` registers the route pack:
`_load_route_pack("private_office", "services.private_office_routes")`.

`GET /api/private-office/entitlement` (`services/private_office_routes.py`) resolves the
caller's own tier via `po_tiers.resolve_tier`, sets `Cache-Control: no-store`, and returns
`{ok, effective_tier, source, status, expires_at, features, verified_at, resolver_state}`.
`ok` is `resolver_state == "ok"`, so a degraded resolve is 200-with-`ok:false` rather than a
confident "you are on Free". That is all correct and needs no change.

`features` is `feature_matrix.availability_map(tier)`. Its full contents, computed from the
running code:

| feature_id | minimum tier | implementation | availability at PRIVATE_OFFICE |
|---|---|---|---|
| `advanced_undx` | PREMIUM | IMPLEMENTED | **ENTITLED** |
| `market_pulse` | PREMIUM | IMPLEMENTED | **ENTITLED** |
| `capital_graph` | PRIVATE | NOT_IMPLEMENTED | NOT_IMPLEMENTED |
| `private_briefings` | PRIVATE | NOT_IMPLEMENTED | NOT_IMPLEMENTED |
| `private_facts` | PRIVATE | NOT_IMPLEMENTED | NOT_IMPLEMENTED |
| `relationship_intelligence` | PRIVATE | NOT_IMPLEMENTED | NOT_IMPLEMENTED |
| `private_shield` | PRIVATE | PROVIDER_REQUIRED | NOT_IMPLEMENTED |
| `private_shield.breach_monitoring` | PRIVATE | PROVIDER_REQUIRED | NOT_IMPLEMENTED |
| `private_office.document.extraction` | PRIVATE | PROVIDER_REQUIRED | NOT_IMPLEMENTED |
| `human_concierge` | PRIVATE_OFFICE | NOT_IMPLEMENTED | NOT_IMPLEMENTED |

Two facts follow, and both matter:

1. **There is no `private_office` feature_id row.** Asking for it returns the unknown-feature
   fallback: `{"availability": "NOT_IMPLEMENTED", "minimum_tier": "PRIVATE_OFFICE",
   "implementation": "NOT_IMPLEMENTED", "note": "unknown feature_id"}`. There is no key a
   client could read to learn that a Private Office room exists.
2. **Not one Private Office capability is ENTITLED at any tier, including PRIVATE_OFFICE.**
   The only two ENTITLED features in the whole matrix are `advanced_undx` and `market_pulse`,
   both of which are PREMIUM-tier and both of which already render in the Premium screen today.

The live health surface agrees: `features: {total: 10, live: 2}`.

---

## Stage 2 — Premium composition, as it actually ships

`mobile-native/src/screens/PremiumCenterScreen.tsx` (1,726 lines) composes:

- hero / plan cards / billing facts
- `NotYetSection` — granted-but-unenforced benefits, labelled "not yet"
- `CryptoIntelligenceSection` — 5 rows: alerts, portfolio, watchlists, undx, marketPulse
- `CommandCenterSection` — 4 roadmap modules (`activity`, `valueRecap`, `achievements`,
  `unlocked`) plus a 9-tile `COMMAND_SPACES` grid: verified / undx / creator / support are
  Open and pressable; identity / media / storage / founder / labs are inert "Next" tiles
- `ActionsSection`, `FreeCoreSection`

There is no Private Office tile, row, module or space key in any of those catalogs.

Note also: `PremiumCenterScreen.tsx` does not import `entitlements/canonicalTier` at all. It
drives entirely off `api/premium.ts`, which carries no feature-availability map. The only two
consumers of canonical tier today are `AppNavigator.tsx` (`isMember`) and
`ProfileScreen`/`ProfileHeader` (`hasMembershipMark`).

---

## Stage 3 — The first point where it disappears

**It does not disappear. It was never rendered.**

Evidence, exhaustive rather than sampled:

```
grep -rn "PRIVATE_OFFICE" mobile-native/src
```
→ 11 hits, all in `entitlements/canonicalTier.ts` and its test, all of them the tier-ladder
string constant. Zero UI references.

| check | result |
|---|---|
| screen file named `*Private*` in `src/screens/` | none |
| `private`/`Office` in `navigation/types.ts` (`RootStackParamList`) | none |
| `private` in `navigation/AppNavigator.tsx` (`Stack.Screen` registrations) | none |
| `privateOffice` / "Private Office" in `src/i18n/` (any locale) | none |
| "private office" in `templates/` or `static/` (web surface) | none |
| `git log --all --diff-filter=D` for a deleted Private Office file under `mobile-native/` | none |
| `git log --all -S "PrivateOffice" -- mobile-native/` | **no commit has ever added or removed such a reference** |

The last row is the decisive one. This is not a regression: nothing was deleted, disabled,
flag-gated off, or lost in a merge. No Private Office client surface has existed in this
repository at any point on any branch.

**Cause, against the A–H list:** none of A–H, which all presuppose an existing surface that
broke. The accurate cause is **the native entry point was never built** — it is the still-open
mission item #211, "Stages 32-33 — minimal native Private Office entry point + honest states",
which sits behind #208/#209/#210 in the Private Office plan and has not been started.

What *is* built and working today: the tier ladder, the resolver, the entitlement endpoint,
the feature matrix, the private fact store, the private graph, retrieval with owner isolation
and traversal bounds, the audit trail, schema bootstrap, telemetry, and the health surface —
all server-side, all under `services/private_office/`, all covered by `tests/private_office/`.
The client authority module (`entitlements/canonicalTier.ts`) is built and correct; nothing
has yet been wired to it beyond the membership badge and the navigator's `isMember` check.

---

## Decision required before Stage 4

Stage 4 says "restore the real Private Office entry". Two mission constraints now collide,
because the premise that it once existed turned out to be false:

- *"Do not hide the entire Private Office because one subfeature is unavailable."*
- *"DO NOT add a fake tile."* / *"show only capabilities that actually exist."*

At present **every** Private Office subfeature is unavailable — not one, all nine. A tile added
today would open a room containing nothing but "not yet available" copy. That is defensible as
an honest roadmap surface (the Premium screen already does exactly this with its inert "Next"
tiles), but it is not a recovered feature, and calling it one would be the failure mode this
mission was written to prevent.

Three options:

**A. Honest roadmap tile.** Add a `privateOffice` space to `COMMAND_SPACES` with `open: false`
— inert, non-pressable, "Next" chip, consistent with the five tiles already shipping that way.
No new screen, no new route, ~10 lines plus i18n across 11 locales. Truthful, visible, cheap.
Does not make Private Office usable, because nothing behind it is built.

**B. Build the real entry (mission #211, Stages 32-33).** Add the `private_office` feature row
to the server matrix, add a `PrivateOffice` screen and route, drive it from
`fetchCanonicalTier()`, and render each of the nine subfeatures with its true per-feature state
(`NOT_IMPLEMENTED` vs `PROVIDER_REQUIRED` distinguished, since the matrix currently collapses
both into `NOT_IMPLEMENTED` in `availability` and only preserves the difference in
`implementation`). Larger: server change, new screen, new route, i18n, tests, and it still
shows a room where nothing yet works.

**C. Build a capability first**, then the entry, so the room has something in it. The nearest
candidates are `private_facts` and `capital_graph` — both have complete server substrates
already (fact store, graph, retrieval, audit) and are marked NOT_IMPLEMENTED only because no
surface consumes them.

My recommendation is **C, with A as an immediate stopgap** if the tile needs to be visible now.
B alone produces the thing the mission explicitly forbids: a Private Office that is visible and
empty.

No code has been changed. Awaiting direction.
