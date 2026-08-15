# Spatial Console — Rollback & Baseline Record

Branch: `feature/spatial-console` off `codex/insight-image-pipeline` @ `6e67e408`.

## Product decision: motion is a Reels feature (scope correction)

Spatial paging and phone-tilt navigation were built on the **Home Feed**, shipped
to physical-device testing, and then withdrawn. Reels is the only browsing
surface that gets the motion experience; the Feed is back to its original
vertical list.

The reasoning is about what each surface is for. Reels is a full-screen,
one-item-at-a-time player where "next" is unambiguous and a tilt maps onto an
intent the user already has. The Feed is a scannable vertical list with mixed
content heights, inline media and comment affordances — there is no single
"next" for a tilt to mean, and a page-turn there is a surprise rather than a
shortcut.

What this means in the code:

- **Home does not import the motion layer at all.** Not "imports it behind a
  flag that reads false" — there is no call site. `useTiltNavigation` takes a
  `surface: "reels"` literal, so mounting it from any other screen is a compile
  error. Both properties are pinned by
  `mobile-native/src/spatial/__tests__/homeFeedRollback.test.ts`.
- **The dormant spatial feed is NOT deleted.** `SpatialPager` stays on Home
  behind `spatialHomeFeedEnabled` (OFF, and staying OFF) so the implementation
  remains recoverable. As mounted from Home it is a touch-only pager: no tilt
  hook, no immersive navigator, no parallax wrapper.
- **`immersiveNavigatorEnabled` is scoped to the Reels route**, not to the flag
  alone. Previously the flag retimed the bottom-dock animation on *every* tab,
  so enabling immersive Reels would have quietly changed dock timing on Home,
  Messages, Create and Profile. Off-Reels the timings are now byte-identical to
  legacy regardless of what the flag says.
- **The settings screen no longer asks where motion applies.** The
  Feed/Reels/both picker is gone; copy names Reels explicitly.

### Persisted-settings migration

Real devices are carrying a `scope` value written by the Home-Feed-motion
builds. `sanitize()` in `motionSettings.ts` migrates on every read:

| Stored `scope` | Result | Why |
|---|---|---|
| `"feed"` | `mode` → `swipe-only` | They consented to motion on the Feed *and nowhere else*. Carrying that to Reels would turn tilt on somewhere they never agreed to. Re-enabling is one tap; an unexpected page-turn is not undoable the same way. |
| `"reels"` | `mode` kept | Reels motion was already wanted. |
| `"both"` | `mode` kept | Reels motion was already wanted. |
| unknown / absent | `mode` kept | No opinion on record. |

`onboarded` survives every branch — nobody is re-prompted for a decision they
already made. The migration needs **no version marker and no storage-key bump**
to stay idempotent: the first write after a read persists an object with no
`scope` key, and a record without `scope` takes the no-op branch from then on.
Pinned by the `legacy motion scope migration` block in
`mobile-native/src/spatial/motion/__tests__/motionSettings.test.ts`.

## Rollback model

All spatial behavior is gated in `mobile-native/src/spatial/flags.ts` via
`EXPO_PUBLIC_*` env vars. **Every flag defaults OFF** — an unset var means the
legacy code path runs unchanged. No legacy code was deleted; no data
migrations were introduced.

| Flag | Env var | Rolls back |
|---|---|---|
| `spatialConsoleEnabled` (master) | `EXPO_PUBLIC_SPATIAL_CONSOLE` | Everything below (total rollback) |
| `spatialHomeFeedEnabled` | `EXPO_PUBLIC_SPATIAL_HOME_FEED` | Dormant touch-only Home pager → legacy vertical feed. **OFF is the product decision, not a rollout stage.** |
| `spatialReelsEnabled` | `EXPO_PUBLIC_SPATIAL_REELS` | Reels horizontal paging → legacy vertical paging. Also stops the motion sensor: motion exists to move this pager. |
| `spatialCreateEnabled` | `EXPO_PUBLIC_SPATIAL_CREATE` | Create console → legacy composer jump |
| `messagesVisualRefreshEnabled` | `EXPO_PUBLIC_MESSAGES_VISUAL_REFRESH` | Messenger refinements → legacy layout |
| `immersiveNavigatorEnabled` | `EXPO_PUBLIC_IMMERSIVE_NAVIGATOR` | Nav auto-hide **on the Reels route only** → legacy scroll-responsive behavior. Has no effect on any other tab. |
| `spatialMotionEnabled` (motion master) | `EXPO_PUBLIC_SPATIAL_MOTION` | All motion features: settings section, onboarding, sensors |
| `tiltNavigationEnabled` | `EXPO_PUBLIC_TILT_NAVIGATION` | Tilt page-commits in Reels → parallax/swipe only |
| `tiltParallaxEnabled` | `EXPO_PUBLIC_TILT_PARALLAX` | Tilt parallax preview in Reels → swipe only |

Sub-flags require the master (`spatialHomeFeedEnabled() = master && sub`).
Motion flags are doubly layered: `spatialMotionEnabled()` requires
`spatialConsoleEnabled()`, and both tilt flags require `spatialMotionEnabled()`.

Beyond the flags, motion has a **consent layer**: even with all flags on,
`mode` defaults to `"swipe-only"` and `onboarded` to `false` in
`src/spatial/motion/motionSettings.ts` — sensors stay untouched until the user
completes onboarding and explicitly picks a motion mode. Flags ship the
capability, not the behavior.

**Total rollback:** unset (or set to `0`) `EXPO_PUBLIC_SPATIAL_CONSOLE` and
rebuild/OTA. **Motion-only rollback:** unset `EXPO_PUBLIC_SPATIAL_MOTION` —
touch paging keeps working, all sensor code stays dormant. **Individual
rollback:** unset the specific var. **Hard rollback:** revert the branch
merge; no other cleanup is required.

Note: `expo-sensors` is declared in `package.json` (`~15.0.7`) but is loaded
via a safe dynamic require in `useTiltNavigation` — if the package is absent
or the device has no motion sensor, motion reports "unavailable" and the app
behaves as swipe-only. Raw motion data is processed on-device only and never
persisted or transmitted (§20).

## Sensor eligibility — "motion is enabled" never means "the sensor is running"

DeviceMotion is subscribed only while **every** gate below agrees, and is torn
down the moment any one of them stops agreeing. This is a battery and privacy
property as much as a behavioral one: a sensor left running behind a
backgrounded app or a blurred screen is invisible in QA and expensive on a real
phone. Each row is pinned by an `addListener`/`remove` call-count assertion in
`mobile-native/src/spatial/motion/__tests__/reelsOnlyMotion.test.tsx`.

| Gate | Sensor stops when |
|---|---|
| `spatialReelsEnabled()` | spatial Reels is off (the pager is no longer horizontal) |
| `spatialMotionEnabled()` | motion master is off |
| `tiltNavigationEnabled()` / `tiltParallaxEnabled()` | the flag matching the chosen *mode* is off |
| consent | `onboarded` is false, or `mode` is `swipe-only` |
| focus | Reels is not the focused screen |
| app lifecycle | the app is `background` **or** `inactive` (app switcher counts) |
| Reduce Motion | the OS accessibility setting is on |
| screen reader | VoiceOver/TalkBack is on |
| sensor availability | the device reports no sensor, or motion permission is denied — swipe keeps working, state reports `unavailable` |
| unmount | the screen is torn down |

## Full-screen Reels and directional navigator visibility

Reels is a full-bleed pager: one reel per viewport, edge to edge, header and
bottom navigator overlaying the media rather than shrinking it. The navigator's
visibility is a function of the **direction of a committed touch page
transition** and of nothing else.

| Event | Navigator |
|---|---|
| Committed swipe to the **previous** reel (finger drags right) | hides |
| Committed swipe to the **next** reel (finger drags left) | reveals |
| Swipe that fails the threshold and springs back | unchanged |
| Swipe at the first reel that cannot move | unchanged — no transition committed |
| Single tap on unclaimed media | reveals (recovery) |
| **Tilt commit** | unchanged — motion moves reels, never chrome |
| Entering Reels, returning from a child sheet, foregrounding, unmount | reveals |
| Rotation, layout recalc, data refresh, auto-advance | unchanged |

The decision table itself is a pure function,
`navigatorIntentForSettle()` in `mobile-native/src/spatial/navigatorVisibility.ts`,
tested in isolation. `useImmersiveNavigator` adds the flag gate, the
accessibility override and the gesture-independent reveals.

Three implementation properties are worth knowing before changing any of it:

- **Source attribution is asserted, not inferred.** `onMomentumScrollEnd` is
  byte-identical for a finger swipe and a tilt commit. `onScrollBeginDrag` fires
  only for a real drag, so ReelsScreen records the origin index there; a settle
  with no recorded origin is `"motion"` and cannot move the navigator.
- **The settle offset is authoritative for the index**, not viewability.
  Viewability answers "what is visible enough to play", flips mid-drag at 72%,
  and reports `-1` when nothing qualifies. Reading it at release would report
  the destination as the origin and swallow the transition; collapsing `-1` to
  `0` would invert the direction of the next backward swipe.
- **Hiding is flag-gated; revealing is not.** A recovery affordance that is
  itself feature-flagged is a way to strand somebody behind an invisible dock.
  A screen reader follows the same asymmetry: hides are dropped, reveals pass.

Layout does not depend on navigator state. The reel's overlay inset derives from
the `BOTTOM_NAV_CONTENT_CLEARANCE` constant, never from `immersive.hidden`, so a
hide or reveal moves only the dock — the media never resizes or reflows.

### Four rollback levels

| Level | Command | Result |
|---|---|---|
| 1 — Immediate | unset `EXPO_PUBLIC_IMMERSIVE_NAVIGATOR` | Full-screen Reels and horizontal paging stay; the navigator is permanently visible. The smallest possible retreat if only the hide/reveal behavior is wrong. |
| 2 — Motion | unset `EXPO_PUBLIC_TILT_NAVIGATION` and `EXPO_PUBLIC_TILT_PARALLAX` (or `EXPO_PUBLIC_SPATIAL_MOTION` for all of it) | Touch paging and full-screen layout keep working; no sensor subscribes anywhere. |
| 3 — Spatial Reels | unset `EXPO_PUBLIC_SPATIAL_REELS` | Reels returns to legacy vertical paging with the framed card. Implies level 2 — motion exists to move this pager. |
| 4 — Full | unset `EXPO_PUBLIC_SPATIAL_CONSOLE` | Entire spatial console off; every surface renders the legacy experience. |

Levels are independent in the sense that any one can be applied without the
others, but they nest downward: 4 implies 3 implies 2, and 3 implies 1.

A user-facing counterpart to level 2 exists in Settings → Accessibility →
**Reset Reels motion settings**. It returns the device to the pre-consent state
by removing the stored record rather than overwriting it with defaults, which is
the only state in which the sensor provably cannot subscribe at all. Distinct
from *Replay tutorial*, which re-shows the explainer and keeps the user's
choices.

## Physical-device QA matrix

Static checks do not replace device QA here — the pager, the safe-area
behavior and overlay readability are all things only a real screen shows.

| # | Check | Expected |
|---|---|---|
| 1 | Reel at rest | Fills the entire viewport, edge to edge, no card frame, no gaps |
| 2 | Notch / Dynamic Island | Media runs behind it; no interactive control is under it |
| 3 | Home indicator | Media runs behind it; controls clear it |
| 4 | Swipe left (next reel) | Pager advances exactly one reel; navigator reveals |
| 5 | Swipe right (previous reel) | Pager retreats exactly one reel; navigator hides |
| 6 | Fast flick | Advances exactly one reel — never two |
| 7 | Half-swipe released | Springs back to the same reel; navigator unchanged |
| 8 | Swipe right on the first reel | Nothing moves; navigator unchanged |
| 9 | Navigator hide / reveal | Only the dock animates; the reel does not resize or reflow |
| 10 | Tap on empty media area | Navigator reveals |
| 11 | Tap a like/comment/share control | Action fires; navigator does not toggle |
| 12 | Expand a long caption | Caption expands; no page change |
| 13 | Tap the creator avatar | Opens the profile; no page change |
| 14 | Open comments, then dismiss | Navigator visible throughout |
| 15 | Tilt commit (motion enabled) | Reel changes; navigator does not move |
| 16 | Drag and release without momentum, then tilt | Tilt still commits — the motion machine is not stuck suspended |
| 17 | Background and foreground the app | Navigator visible; exactly one reel playing |
| 18 | Rotate the device | Index preserved; navigator unchanged |
| 19 | **Bright reel and dark reel** | Header, caption and action icons readable over both |
| 20 | VoiceOver on | Navigator never hides; reveals immediately if it was hidden |

Only one reel owns playback at rest, and it is the reel the pager settled on.
Mid-swipe, when neither reel clears the viewability threshold, the outgoing reel
keeps ownership until the incoming one claims it — otherwise the arriving reel
renders black until the snap finishes.

## Locked color baseline (`mobile-native/src/theme/colors.ts`)

No new colors are introduced anywhere in the spatial console. Baseline pinned
by `src/spatial/__tests__/colorBaseline.test.ts`:

```
background #050910   surface #0b141c      surfaceRaised #111f2a
text #f4f7fb         muted #9aa8b7        accent #32e6b3
accentStrong #61d8ff warning #f3c461      danger #ff5f7e
border #203746       intelligence #9f7cff creator #42e7d4
economy #f6c85d      safety #3ff0a0       crypto #62e0ff
disabled #51606c     focus #8df7ff
glass rgba(11,24,34,0.82)        glassStrong rgba(15,36,50,0.94)
signalDim rgba(50,230,179,0.12)  signalSoft rgba(97,216,255,0.12)
dangerSoft rgba(255,95,126,0.14) warningSoft rgba(243,196,97,0.14)
```

## Audio / livestream protection

The spatial console never touches audio session code, LiveKit, Pulse Radio, or
rooms. The only livestream-adjacent change is the Create Console's Go Live
entry, which shows a warning + confirmation and then navigates to the
*existing* `LiveStudio` setup flow — it never broadcasts directly.
Gate check: `python3 scripts/realtime_audio_change_gate.py --base origin/main --head HEAD`.
