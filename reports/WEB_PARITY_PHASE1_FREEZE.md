# PulseSoc Web Parity Mission — Phase 1 Freeze + Phase 2 Inventory

**Date:** 2026-08-05
**Mission:** Website total parity with native app + backend
**Status:** PHASE 1 COMPLETE / PHASE 2 PARTIAL — **IMPLEMENTATION BLOCKED**

---

## 1. Frozen state

| Item | Value |
|---|---|
| Branch | `codex/emergency-live-audio-recovery` |
| Local HEAD | `5f76e30d0f52b634b2f7950754027f62b7947d49` |
| Remote (`origin/codex/emergency-live-audio-recovery`) | `ef99f6aab4c7ef85b4a9fb62344270228333d8de` |
| Unpushed commits | 0 |
| Commits ahead of `origin/main` | 1 (`5f76e30d` — "Backend operations recovery + Live audio engine recovery") |
| Dirty files | 9 (6 modified, 3 untracked) |
| Railway deployed SHA | **NOT VERIFIED** — no Railway access from this session |
| Website deployed SHA | **NOT VERIFIED** |
| Native embedded SHA | **NOT VERIFIED** |

Three of the six stale worktrees under `/private/tmp` are marked prunable and one
session worktree is locked. None of them hold the current mission.

### Dirty files

```
 M mobile-native/ios/.../PulseSocNative.xcscheme
 M mobile-native/patches/@livekit+react-native-webrtc+144.1.1.patch
 M mobile-native/src/core/__tests__/realtimeAudioEngine.test.ts   *** PROTECTED
 M mobile-native/src/core/realtimeAudioEngine.ts                  *** PROTECTED
 M mobile-native/src/live/useLiveBroadcastRoom.ts                 *** PROTECTED
 M reports/realtime_audio_change_declaration.md
?? mobile-native/src/core/__tests__/realtimeAudioNative.test.ts
?? mobile-native/src/core/realtimeAudioNative.ts
?? mobile-native/tsconfig.scoped.tmp.json
```

---

## 2. BLOCKER — uncommitted work inside the hard-locked audio boundary

The working tree carries in-flight edits to **three paths listed in
`config/realtime-audio-protected-paths.json` → `categories[].paths`**:

- `mobile-native/src/core/realtimeAudioEngine.ts` (shared audio-session coordinator)
- `mobile-native/src/core/__tests__/realtimeAudioEngine.test.ts`
- `mobile-native/src/live/useLiveBroadcastRoom.ts`

The manifest's own `unrelated_mission_policy` reads:

> "A mission whose subject is not real-time audio must not edit any path in
> `categories[].paths` or `dependency_watch.files`."

…and names *Marketplace, Advertising, Premium, Crypto, Profile, Feed, Settings,
Search, General UI* as examples. A website-parity mission is squarely in that set.

**Consequence:** any commit made on this branch risks sweeping unrelated,
physically-unvalidated audio-recovery work into a website milestone. That
violates both the mission's own "do not merge unrelated work" rule and the
repo's audio change policy. It also means the branch cannot be cleanly deployed.

**Required before any website implementation begins:** the audio recovery work
must be finished, declared, device-QA'd and committed (or stashed/reverted) by
its owner, and website work must start from a clean tree — ideally on a fresh
branch cut from `main`.

---

## 2b. HARD STOP — the repository is under active concurrent write

An attempt to park the audio work via `git stash push -u` failed. Investigation
found **another process is editing the protected audio files right now.**

Evidence:

```
02:00:29Z  ls -l realtimeAudioEngine.ts        -> mtime 01:58:57Z  (92s old)
02:00:42Z  md5sum realtimeAudioEngine.ts       -> fb5811d423ab1126338715eb5d212896
02:01:22Z  md5sum realtimeAudioEngine.ts       -> 2ce7e78220d3e769a4d31e821ea7456c   *** CHANGED
```

The file's contents changed inside a 40-second observation window.

Git is also locked:

```
.git/HEAD.lock    2026-08-05 23:13:41Z
.git/index.lock   2026-08-06 00:43:33Z
```

Both locks are stale by wall-clock age but `git stash` returns RC=1 because of
them, so no git write operation of any kind can proceed.

The dirty set also **grew** between the first and second inspection, without any
action from this session:

```
+ M mobile-native/src/calls/useNativeCallRoom.ts
+ M mobile-native/src/core/realtimeAudioTelemetry.ts
+ M mobile-native/src/core/__tests__/realtimeAudioTelemetry.test.ts
```

**Actions deliberately NOT taken, and why:**

- **Did not delete `.git/index.lock` / `.git/HEAD.lock`.** If the writing process
  holds them, removing them corrupts the index mid-write. Stale-looking locks
  plus a concurrently-writing process is the worst case for forced unlock.
- **Did not stash.** Stashing would yank files out from under a live editor
  mid-edit and lose in-flight audio-recovery work in the hard-locked boundary.
- **Did not commit, branch, or push.** Any git write is unsafe until the tree
  settles and the locks clear.

**This must be resolved by a human before any website work starts.** Identify
and stop the process writing to `mobile-native/src/core/`, let it finish or
terminate it cleanly, clear the stale locks only once nothing holds them, then
land or revert the audio recovery per
`docs/realtime_audio_change_policy.md`.

Only `reports/WEB_PARITY_PHASE1_FREEZE.md` (this file, untracked, unrelated
path) was written by this session. No tracked file was modified.

---

## 3. Scale of the surface (measured, not estimated)

| Metric | Count |
|---|---|
| `bot.py` | 111,773 lines |
| Route decorators in `bot.py` | 1,537 |
| Unique route paths | 1,491 |
| — `/api/*` | 910 |
| — `/admin*` | 256 |
| — user-facing page routes | 325 |
| Jinja templates | **17** |
| `render_template` call sites | **19** |
| Static JS files | 32 |
| `services/*.py` modules | 239 |
| Native screens (`*Screen.tsx`) | 89 |
| Native source files | 596 |
| Native API client modules | 40+ |

### The structural finding that dominates this mission

**325 web page routes are served by 17 templates and 19 `render_template` calls.**
The overwhelming majority of the website is generated by Python functions that
build HTML strings inline inside `bot.py` — e.g. `/pulse` and its eight feed
aliases all return `pulse_page_html(...)`.

This is the root cause of the drift the mission describes. There is no web
component layer, no design-token layer, and no template inheritance to align
against. Phase 3 (canonical design system) and Phase 4 (global shell) are
therefore not "extract tokens and restyle" — they are **introduce a view layer
that does not currently exist**, then migrate 325 routes onto it.

---

## 4. Route family distribution

**Web page routes (325):** pulse 120, dashboard 39, arena 38, education 6,
health 5, debug 5, account 4, legal 3, chat 3, sports-edge 3, predictions 3,
markets 3, messages 2, checkout 2, billing 2, upgrade 2, scam-shield 2,
verify-email 2, reset-password 2, remainder singletons.

**API families (910):** pulse 319, business-os 170, arena 120, admin 37,
dashboard 26, crypto 19, messages 16, account 15, mobile 14, reels 12,
alerts 9, undx 8, push 8, payments 8, chat 7, education 7.

Note: `/api/mobile/*` is only **14 routes**, while the native app has 40+ API
client modules. Native is consuming the same `/api/pulse` and `/api/business-os`
surface as the web — which is good news for contract parity, and means the gap
is presentation-layer, not data-layer, for most areas.

---

## 5. Native screens with no evident web counterpart

Keyword-matched 89 native screens against the 325 web page routes.
**77 have a plausible web route; 12 do not:**

| Native screen | Web gap | Notes |
|---|---|---|
| `MessengerScreen` | No `/messenger` route | Web has `/pulse/messages` + `pulse_messages_v2.html` — likely a **terminology** gap, not a functional one. Verify. |
| `AdsManagerScreen` | No ads-manager route | Web has `/pulse/ads`, `/pulse/advertise`, `pulse_advertiser_portal.html`. Verify contract alignment. |
| `CallScreen` | No web call surface | Genuinely native-only (LiveKit + AVAudioSession). Candidate **NATIVE_ONLY**. |
| `OrdersManagerScreen` | No `/orders` | Marketplace seller orders — **0 `/marketplace*` page routes exist at top level.** |
| `BuyerOrdersScreen` | No buyer orders page | Same. |
| `BlockedUsersScreen` | No `/settings/blocked` | Settings parity gap. |
| `MutedUsersScreen` | No muted-users page | Settings parity gap. |
| `SafetyHubScreen` | No safety hub | Web has `/scam-shield`, `/security`, `/trust-center` — terminology drift. |
| `RelationshipListScreen` | Partial | Web has `/pulse/following`, `/pulse/friends`. |
| `ActivityScreen` | Partial | Web has `/pulse/notifications`, `/pulse/alerts`. |
| `RegionTimeScreen` | No region/time settings | Settings parity gap. |
| `GalacticConstructionScreen` | None | Verify whether this is live or experimental. |

**Marketplace is the single largest structural gap:** zero top-level
`/marketplace*` page routes. Web marketplace lives under `/pulse/marketplace`,
`/pulse/merchant/*` while native has `MarketplaceScreen`,
`MarketplaceManagerScreen`, `SellerStoreScreen`, `SellerApplicationScreen`,
`SellerListingComposerScreen`, `StoreDashboardScreen`, `OrdersManagerScreen`,
`BuyerOrdersScreen`, `CommerceInboxScreen`.

### Legacy / duplicate web routes already identified for Phase 18 cleanup

`/pulse/home`, `/pulse/legacy-home`, `/pulse/home-legacy`, `/pulse/old-home`,
`/pulse/legacy` — five aliases all redirecting to `/pulse`.
Also `/pulse/messages-legacy`, `/arena-preview`, `/admin-dashboard` vs `/admin`.

---

## 6. Honest assessment of mission scope

The mission specifies 29 phases across 1,491 routes, 239 services, 89 native
screens and a 111k-line monolith, ending in commit, push, Railway deploy and
live multi-browser QA.

This is not a single-session task and cannot be completed on autopilot without
producing exactly what the mission forbids: unverified parity claims. Realistic
sizing, assuming a clean tree:

| Milestone | Rough effort |
|---|---|
| 1. Full route manifest + parity map (finish Phase 2) | 1–2 sessions |
| 2. Design tokens + view layer + global shell (Phase 3–4) | 3–5 sessions — *this is the hard one; the view layer does not exist* |
| 3. Auth/account/settings parity | 2 sessions |
| 4. Feed/social parity | 2–3 sessions |
| 5. Reels/status/creator | 2 sessions |
| 6. Messenger | 2 sessions |
| 7. Marketplace (largest gap) | 3–4 sessions |
| 8. Ads | 2 sessions |
| 9. Business OS (170 API routes) | 3–4 sessions |
| 10. Live/calls | 2 sessions — *gated on the audio incident* |
| 11. UNDX | 2 sessions |
| 12. Operations/admin (256 routes) | 3 sessions |
| 13. A11y / performance / SEO | 2–3 sessions |
| 14. Tests + report | 2 sessions |

Additionally, several success criteria are **not achievable from this
environment** and need to be assigned elsewhere:

- Railway deployed SHA verification and deployment (no Railway access here)
- Live browser QA in Safari / Chrome / mobile Safari with authenticated QA
  accounts (needs credentials + a running deploy)
- Physical device QA to close the Live audio incident

---

## 7. Recommended next mission

**Do not begin website implementation on this branch.**

1. Land or park the realtime-audio recovery work per
   `docs/realtime_audio_change_policy.md`, including device QA.
2. Cut `feature/web-parity` from `main` on a clean tree.
3. Run milestone 1 to completion: the full 1,491-route manifest with product
   area, native route, backend route, permission, feature flag, and parity
   classification for every entry. That artifact gates every later milestone
   and is the only defensible basis for a parity claim.
4. Then take milestone 2 (design tokens + view layer), because every
   subsequent surface milestone depends on it.

---

*Generated from direct repository inspection at `5f76e30d`. No files were
modified. All counts are measured, not estimated.*
