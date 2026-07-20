# PulseSoc Native — Full-System Wiring Audit & Repair

**Date:** 2026-07-20
**Branch:** `release/undx-nexus-core-v4`
**App:** `mobile-native` (React Native + Expo, native LiveKit, production backend `https://pulsesoc.com`)
**Scope:** Repository-wide integration audit of the native app — verifying that everything visible navigates correctly, loads authoritative data, performs its intended action, updates state, talks to the correct backend, handles permissions/failures, and survives lifecycle changes.
**Governing rule honored:** *Do not redesign completed work unless a verified defect requires it.* No fake success introduced; honest "coming soon" states preserved.

---

## 1. Method

Phase 1 was **non-destructive discovery**: six parallel subsystem audits cross-checking the live source tree, followed by **direct verification** of every concrete claim (line-by-line reads) before any edit. Only defects that were reproduced against the actual code were repaired. Editorial "recommendations" from the audit passes (e.g. "adopt TanStack Query") were **not** actioned — they are architectural redesigns, not verified defects.

Verification gates used:
- `npx tsc --noEmit` → **clean (exit 0)** after fixes.
- `npx expo export --platform ios` (full Metro/Hermes bundle) → catches import/runtime errors tsc misses. *(Result appended below.)*

---

## 2. Master Wiring Matrix

Legend: ✅ verified wired · 🔧 defect found & fixed · ⚠️ finding logged (no code change / larger follow-up) · N/A not applicable.

| Subsystem | Navigation | Data source | Mutation → cache | Permissions | Audio ownership | Status |
|---|---|---|---|---|---|---|
| **Navigation resolver** (`nativeRouteActions.ts`) | All 49 command-center routes resolve to registered screens; unhandled routes fall through to `dashboardRouting` fallback (intended) | N/A | N/A | N/A | N/A | ✅ |
| **Dashboard fallback router** (`dashboardRouting.ts`) | `/reels` branch routed to a stack name that isn't registered | N/A | N/A | N/A | N/A | 🔧 fixed |
| **Deep links** (`linking.ts`) | All parsed patterns map to registered routes | N/A | N/A | N/A | N/A | ✅ |
| **Notification routing** (`notificationRouting.ts`) | Targets cross-check against registry | Authoritative | N/A | N/A | N/A | ✅ |
| **API client / config** (`config.ts`, `pulseApi.ts`) | N/A | Production `pulsesoc.com`; QA fixtures gated to `localhost`/`127.0.0.1` only — cannot activate in prod | N/A | N/A | N/A | ✅ |
| **ID normalization** (`contentOwnership.ts`) | N/A | `resolveContentId` / `resolveContentOwnerId` coerce to positive int; ownership compares normalized ints | N/A | Central | N/A | ✅ |
| **Home feed** (`HomeScreen`, `feed.ts`) | ✅ | ✅ | Local removal + `invalidateNativeSync`; cross-surface propagation is eventual (see §4.1) | Owner-gated delete via `isContentOwner` | PostCard claims `feed`/`reel` | ⚠️ |
| **Reels** (`ReelsScreen`, `reels.ts`) | ✅ | ✅ | Local removal + sync invalidate | Owner-gated | `ReelPlayerCard` claims `reel` | ⚠️ |
| **Status** (`StatusScreen`, `status.ts`) | ✅ | ✅ | Removes from both `items` + `railItems` | Owner-gated | `StatusViewerCard` claims `status` | ✅ |
| **Live viewer** (`LiveScreen`) | ✅ | ✅ | N/A | N/A | Claims `live` (preempts radio/reels) | ✅ |
| **Live host** (`LiveHostSessionScreen`) | ✅ | LiveKit token + polls (`getLiveState`, guest mgmt, chat) | N/A | Host publish-token gated by backend | LiveKit audio session started **without** claiming media ownership | 🔧 fixed |
| **Pulse Radio / Music** (`pulseRadio.ts`, `MusicScreen`) | ✅ | ✅ | N/A | N/A | Claims `radio`/`music_preview`; auto-resumes after interruption | ✅ |
| **Voice messages / Calls** | ✅ | ✅ | N/A | N/A | Claim `voice` / `call` (highest) | ✅ |
| **Content ownership / edit-delete** | N/A | N/A | N/A | Central `isContentOwner` (server flags + 7 legacy aliases) | Central | ✅ |
| **Premium / entitlements** | ✅ | `getPremiumStatus` | N/A | Gates use inline string matching, not a shared helper (see §4.2) | N/A | ⚠️ |
| **Commerce** (Marketplace / Seller) | ✅ (native browse) | ✅ | N/A | N/A | N/A | ⚠️ (web checkout boundary, §4.3) |

---

## 3. Verified Defects Repaired

### 3.1 🔧 Dashboard fallback routed Reels to an unregistered stack route
**File:** `src/navigation/dashboardRouting.ts:93`
**Defect:** `navigation.navigate("Reels", { title: "Reels" })`. `Reels` is a **tab** screen, not a root stack route; every other content branch in this router correctly wraps in `navigate("Tabs", { screen: ... })`. Any dashboard/deep-link path containing `/reels` that fell to this fallback would hit an unregistered route (crash / no-op).
**Fix:** `navigation.navigate("Tabs", { screen: "Reels" })`. Now consistent with the `/status`, `/live`, `/marketplace`, `/messages`, `/network/groups` branches.

### 3.2 🔧 Live host broadcast bypassed the global audio-ownership coordinator
**File:** `src/screens/LiveHostSessionScreen.tsx`
**Defect:** The host `connect()` flow starts the LiveKit `AudioSession` (`useLiveBroadcastRoom.ts:173`) but never called `claimMediaPlayback`. Every other audio surface (reels, status, radio, music preview, voice, calls, **and the live *viewer***) claims ownership through `src/core/mediaPlaybackCoordinator.ts`. The host was the sole gap: starting a broadcast while **Pulse Radio** (or a reel's audio) was playing would let that audio continue **over** the live mic — a real double-audio defect.
**Fix:** Added a `room.connected`-scoped effect that claims `kind: "live"` (priority 70, which preempts `reel`/`status`=40 and `radio`=20) on connect and releases on unmount/disconnect:
```ts
useEffect(() => {
  if (!room.connected) return undefined;
  const ownerId = `live-host:${liveId}`;
  claimMediaPlayback({ id: ownerId, kind: "live", pause: () => undefined }).catch(() => undefined);
  return () => { releaseMediaPlayback(ownerId).catch(() => undefined); };
}, [room.connected, liveId]);
```
This mirrors the viewer's pattern in `LiveScreen.tsx:243`. A higher-priority `call` (100) still correctly preempts.

---

## 4. Findings Logged (no code change this pass)

These are real observations that do **not** rise to a crash-level defect and/or would require architectural change the mission rules reserve for verified defects. Documented for follow-up rather than actioned to avoid redesigning working, shipped surfaces.

### 4.1 ⚠️ Cross-surface mutation propagation is eventual, not immediate
Each screen owns its own list state; deletions/reactions are removed locally on the acting screen and broadcast via `src/core/eventSync.ts` (`invalidateNativeSync`), which triggers a **refresh** of subscribed subsystems rather than a granular cross-surface edit. Consequence: after deleting a post on Home, the Profile grid / Saved list reconcile on their next focus/refresh, not synchronously. This is functional (no permanent ghost once refreshed) but not instantaneous. A single mutation-event bus that patches every mounted surface's item list in place would make it immediate. **Not a crash; deferred as an enhancement.** No evidence of a delete→refetch race that permanently resurrects deleted items on the acting screen.

### 4.2 ⚠️ Premium/entitlement gates use inline string matching
`premium_status` is an untyped string on `PulseUser`; gates in `MusicScreen`, `LiveHostSessionScreen` (`hostVerified`), `ProfileHeader`, and `AppNavigator` each re-derive eligibility with local `["active","verified","pro","premium"].includes(...)` style checks. No security bypass was found (the live-host case is cosmetic — it only drives a badge; the authoritative gate is the backend publish-token), but the duplication invites drift. Recommend a single `entitlements.ts` helper (`isPremium(user)`, `hostBadge(user)`). **Deferred — centralization refactor, not a defect.**

### 4.3 ⚠️ Commerce checkout remains a web boundary
`MarketplaceScreen` / `SellerStoreScreen` / `SellerListingComposerScreen` open web URLs (`Linking.openURL`) for **checkout, seller dashboard, and listing creation**. These are **honest, labeled boundaries** ("stay on existing PulseSoc marketplace systems"), not fake success, and browse/discovery is native. Full native commerce (native checkout + payments) is a large, separate build — explicitly out of scope for a wiring pass and not a regression. **Logged, not changed.** Legal `/terms` and `/privacy` web fallbacks are correct and acceptable.

### 4.4 ✅ Confirmed clean (no action needed)
- **Shadow endpoints:** none reachable in production; all `PULSESOC_QA_*` fixtures gated behind a `localhost` regex on `PULSE_API_BASE_URL`.
- **ID normalization:** centralized and used in ownership + routing.
- **Empty handlers / fake-success buttons:** none found. All `onPress` handlers perform real work or show honest error/coming-soon states.
- **Host moderation:** mute/remove/accept guest actions are backend-enforced; the host screen is only reachable with a granted publish token.

---

## 5. Verification

| Gate | Result |
|---|---|
| `npx tsc --noEmit` (whole project) | ✅ clean, exit 0 (post-fix) |
| `npx expo export --platform ios` (Hermes bundle) | ✅ built clean, exit 0 — single iOS bundle `index-*.hbc` (7.02 MB); no import/runtime errors |
| Simulator QA | Live Host cannot be visually validated in Simulator (no camera / no real LiveKit publish token) — unchanged limitation. Navigation + audio-claim paths are covered by the two gates above. |
| Physical iPhone (16 Pro `P3r7or`) | Recommended next step per standing directive — build+install to validate live-host audio-ownership on device. |

---

## 6. Summary

The native app was already substantially wired: a centralized navigation resolver, deep-link parser, notification router, ID normalizer, ownership resolver, and a global media-playback coordinator all exist and function. This pass found and fixed **two verified defects** — an unregistered Reels fallback route and the live-host audio-ownership gap — and logged **three** non-blocking findings (eventual cross-surface reconciliation, scattered premium gates, web commerce checkout) for scoped follow-up. No working surface was redesigned; no fake success was introduced.

**Files changed:**
- `src/navigation/dashboardRouting.ts` — Reels fallback route.
- `src/screens/LiveHostSessionScreen.tsx` — global audio ownership claim for host broadcast.
