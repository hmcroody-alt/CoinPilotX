# Spatial Console — Rollback & Baseline Record

Branch: `feature/spatial-console` off `codex/insight-image-pipeline` @ `6e67e408`.

## Rollback model

All spatial behavior is gated in `mobile-native/src/spatial/flags.ts` via
`EXPO_PUBLIC_*` env vars. **Every flag defaults OFF** — an unset var means the
legacy code path runs unchanged. No legacy code was deleted; no data
migrations were introduced.

| Flag | Env var | Rolls back |
|---|---|---|
| `spatialConsoleEnabled` (master) | `EXPO_PUBLIC_SPATIAL_CONSOLE` | Everything below (total rollback) |
| `spatialHomeFeedEnabled` | `EXPO_PUBLIC_SPATIAL_HOME_FEED` | Home horizontal feed → legacy vertical feed |
| `spatialReelsEnabled` | `EXPO_PUBLIC_SPATIAL_REELS` | Reels horizontal paging → legacy vertical paging |
| `spatialCreateEnabled` | `EXPO_PUBLIC_SPATIAL_CREATE` | Create console → legacy composer jump |
| `messagesVisualRefreshEnabled` | `EXPO_PUBLIC_MESSAGES_VISUAL_REFRESH` | Messenger refinements → legacy layout |
| `immersiveNavigatorEnabled` | `EXPO_PUBLIC_IMMERSIVE_NAVIGATOR` | Nav auto-hide → legacy scroll-responsive behavior |
| `spatialMotionEnabled` (motion master) | `EXPO_PUBLIC_SPATIAL_MOTION` | All motion features: settings section, onboarding, sensors |
| `tiltNavigationEnabled` | `EXPO_PUBLIC_TILT_NAVIGATION` | Tilt page-commits → parallax/swipe only |
| `tiltParallaxEnabled` | `EXPO_PUBLIC_TILT_PARALLAX` | Tilt parallax preview → swipe only |

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
