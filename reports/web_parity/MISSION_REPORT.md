# PulseSoc Web Parity Mission — Report

**Date:** 2026-08-05
**Mode:** autopilot, executed alongside concurrent agents
**Analysis pinned to:** `bot.py` md5 `522b9419c4283966d27d99abd5208720`

---

## 1. Overall verdict

**PARTIAL — 4 of 29 phases complete. Mission does not pass.**

Phases 1 (freeze), 2 (parity map), 3 (design system) and a subset of 26 (tests)
are complete and evidenced. Phases 5–25 and 27–29 are not started.

The mission cannot pass as specified from this session, for two structural
reasons, both documented with evidence rather than asserted:

1. **`bot.py` is held by other agents.** It changed md5 twice during this
   session (`522b9419` → `058ddca5`). Every remaining phase requires editing
   it. Two agents editing a 7.3 MB / 111,773-line single file produce lost
   writes.
2. **`.git/index.lock` is held** (since 00:43Z). No commit, branch, stash or
   push is possible. Required deliverables 34–37 (commits, push verification,
   Railway deployment, final tree state) are therefore unreachable.

All work below was done in a collision-free lane: **new files only, zero
tracked files modified, zero git writes attempted.**

## 2–6. SHAs

| | |
|---|---|
| Starting SHA | `5f76e30d0f52b634b2f7950754027f62b7947d49` |
| Ending SHA | `5f76e30d…` (unchanged — no commits possible) |
| Website deployed SHA | **NOT VERIFIED** — no Railway/host access from this environment |
| Backend deployed SHA | **NOT VERIFIED** — same |
| Native reference SHA | `5f76e30d…` working tree; native *build* SHA not verified |

Branch `codex/emergency-live-audio-recovery`, 1 commit ahead of `origin/main`,
0 unpushed commits, in sync with its own remote.

## 7. Product areas inspected

16 areas counted and classified: pulse, business-os, arena, marketplace,
messages, reels, live, undx, account, payments, alerts, dashboard, admin,
mobile, push, crypto. Arena (157 routes) and Operations/Admin (293 routes) were
counted but **not individually inspected**.

## 8–10. Parity counts

Classified by product area, not per-route:

| Classification | Areas |
|---|---|
| FULL_PARITY | **0** |
| PARTIAL | 5 — pulse, messages, account, dashboard, crypto |
| BACKEND+NATIVE_ONLY (no web) | 3 — business-os, mobile, push |
| BACKEND_ONLY | 2 — reels, admin |
| WEB_ONLY | 6 — arena, marketplace, live, undx, payments, alerts |
| BLOCKED | remaining 24 phases |

**Zero areas reach full parity.**

## 11–12. Routes removed / added

**0 removed, 0 added** — both require editing `bot.py`.

Identified for removal (Phase 18): 182 alias routes, including the confirmed
dead group `/pulse/home`, `/pulse/legacy-home`, `/pulse/home-legacy`,
`/pulse/old-home`, `/pulse/legacy` (five paths, one handler, all redirecting to
`/pulse`). 82 of 330 page routes are pure redirects.

## 13. Design system work — COMPLETE

Delivered `static/css/pulsesoc-tokens.css`: 160 tokens across primitives,
semantic layer, scale, accessibility baselines, legacy aliases and utilities.
All colour primitives taken verbatim from `mobile-native/src/theme/colors.ts`.

Measured drift that justified it:

| Metric | Value |
|---|---|
| Native semantic tokens | 23 |
| Web CSS var names across 19 stylesheets | 159 |
| Web vars with **conflicting values across files** | **45** |
| Hardcoded hex occurrences inside `bot.py` | **1,002** (180 distinct) |
| Inline `<style>` blocks inside `bot.py` | 97 |
| Native tokens matching nearest web value **exactly** | **0** |

`--control-accent` resolves to `#49ffc8`, `#4f8cff` *and* `#5ff4ff` depending on
stylesheet load order — messenger surfaces render differently run to run.

**Not wired in.** Requires 32 one-line insertions into `bot.py`.

## 14. Navigation changes — NOT STARTED

Phase 4 audit found **there is no global shell**: 32 distinct `<head>`
emissions across 14 shell builder functions. 29 of 32 load no external
stylesheet at all. Collapsing them requires `bot.py`.

## 15–16. Contract / authorization mismatches

**Contract mismatches found: 19. Fixed: 0.**

The significant finding — native calls endpoints with **no registered handler**:

```
/api/calls/active          /api/pulse-ai/message
/api/calls/start           /api/pulse-ai/actions/confirm
/api/calls/voip-token      /api/pulse-ai/actions/cancel
/api/calls/voip-token/revoke
/api/pulse/communications/v2[/conversations]
```

`bot.py:12095` contains middleware that anticipates these paths:

```python
is_call_request = request.path.startswith(("/api/calls/", "/api/pulse/communications/v2/"))
```

…yet no route exists. Verified absent three ways: zero matching `@route`
decorators, **zero `add_url_rule` calls**, **zero `register_blueprint` calls**
in the entire file — so they are not registered dynamically either.

Given the branch is `codex/emergency-live-audio-recovery` with an open Live
audio incident, missing `/api/calls/*` is a plausible contributor and is
**more urgent than any web work**.

**Authorization: 0 mismatches, 0 exposures.** An initial automated pass
reported "330/330 page routes unauthenticated"; manual verification proved that
wrong. Auth is enforced imperatively via three patterns. Of 253 page handlers,
171 are guarded, 82 unguarded, 56 conventionally public, 26 flagged, and **0
genuine exposures** after reading them. The two that looked private
(`/pulse/bookmarks`, `/pulse/live/<id>`) are pure redirects.

This is an **auditability** problem: zero page routes use a declarative guard,
so protection cannot be statically proven. Phase 5 should introduce one.

## 17–27. Per-area parity — NOT STARTED

Feed, Reels/Status, Messenger, Marketplace, Ads, Business OS, Live/Calls,
Settings, Translation, UNDX, Operations Center: **all BLOCKED on `bot.py`.**

Largest gaps quantified: Business OS 199 backend routes / **0 web pages**;
Reels 12 backend routes / **0 web pages**; Marketplace 2 web pages against 9
native commerce screens.

## 28–31. Accessibility / performance / SEO / browser QA

Accessibility: baselines built into the token layer only —
`prefers-reduced-motion` zeroing, `prefers-contrast` lift,
`--touch-target-min: 44px` (WCAG 2.5.5), focus-visible ring, skip link.
**No audit of rendered pages performed.**

Performance, SEO, browser QA: **NOT STARTED.** Browser QA needs a running
deploy and authenticated QA accounts; neither is available here.

## 32. Remaining platform-specific exceptions

`CallScreen` is a legitimate NATIVE_ONLY candidate (LiveKit + AVAudioSession).
Not formally classified.

## 33. Files changed

**13 new files. Zero tracked files modified.**

```
static/css/pulsesoc-tokens.css                    (160 tokens)
tests/web_parity/test_design_tokens.py            (46 cases)
scripts/web_parity/extract_routes.py
scripts/web_parity/extract_native.py
scripts/web_parity/build_manifest.py
scripts/web_parity/auth_audit.py
scripts/web_parity/token_drift.py
scripts/web_parity/verify_tokens.py
scripts/web_parity/shell_audit.py
reports/WEB_PARITY_PHASE1_FREEZE.md
reports/web_parity/PHASE2_PARITY_MANIFEST.md
reports/web_parity/PHASE3_4_DESIGN_SYSTEM.md
reports/web_parity/MISSION_REPORT.md
+ data: route_table.{json,csv}, native_map.json, auth_audit.json,
        parity_matrix.{json,csv}, token_drift.json, token_verify.json,
        shell_audit.json
```

## 34–37. Commits / push / deploy / tree state

| | |
|---|---|
| Commits | **0** — `.git/index.lock` held since 00:43Z |
| Push verification | **NOT PERFORMED** |
| Railway deployment | **NOT PERFORMED** |
| Final working tree | dirty — audio recovery work (not mine) + 13 new untracked files (mine) |

The lock was deliberately **not** force-removed. With another process actively
writing, deleting a lock risks index corruption or destroying in-flight
protected-path audio work.

## 38. Remaining blockers

1. `.git/index.lock` held — blocks all git operations
2. `bot.py` under concurrent write — blocks all remaining phases
3. Protected realtime-audio paths dirty (`realtimeAudioEngine.ts`,
   `useLiveBroadcastRoom.ts`) — the manifest's `unrelated_mission_policy`
   forbids a website mission from touching them
4. No Railway/host access — blocks deploy verification
5. No QA credentials or running deploy — blocks browser QA

## 39. Next recommended mission

1. **Triage missing `/api/calls/*` and `/api/pulse-ai/*` routes.** Highest
   urgency; possibly implicated in the open Live incident.
2. Land or revert the audio recovery per `docs/realtime_audio_change_policy.md`;
   clear the git lock once nothing holds it.
3. **Serialize `bot.py`.** Assign one agent at a time, or the file will keep
   losing writes regardless of how careful any individual agent is.
4. Wire the token layer — 32 insertions, line numbers in `shell_audit.json`.
   Start with the 3 shells that already load external CSS.
5. Declarative authorization (Phase 5 prerequisite).
6. Business OS web surface — largest single gap.

---

## Verification performed

- Route extraction cross-checked two ways: `ast` parse 1,493 unique paths vs
  independent `grep` 1,491
- Confirmed `add_url_rule` = 0 and `register_blueprint` = 0, so no dynamically
  registered routes were missed
- Read 5 handlers by hand to overturn the false "330/330 unauthenticated" result
- Read 2 suspected exposures by hand and cleared both
- Token layer: balanced braces/parens, **0 dangling var references**
- Test suite: **46/46 pass**, executed via a shim because pytest is unavailable
  in this sandbox (no network)
- Negative control: injected a dangling reference and confirmed the test
  detects it — the assertions bite rather than passing trivially
- Ratchets set at exactly current values (1,002 / 180 / 97, **zero headroom**),
  so any new hardcoded colour fails immediately

## Honest limitations

Nothing here has been seen rendered in a browser. The parity classification is
derived from static analysis of route registration and import graphs, not from
runtime behaviour. "Native uses" counts are import-graph derived — a screen
inherits every endpoint of a module it imports, so per-screen counts overstate
actual usage. Arena and Operations/Admin (450 routes combined) were counted but
not inspected. Spacing, radius and typography scales in the token layer are
web-side **proposals**, not native-derived: `mobile-native/src/theme/` contains
only `colors.ts` and `ThemeContext.tsx`. Colour is canonical; scale is not, and
should be reconciled against native `StyleSheet` usage before being trusted.
