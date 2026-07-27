# PulseSoc Native — Futuristic Status Reaction Action Rail

## 1. Executive summary

The plain-text Status action buttons ("React", "Reply", "Share") in the native Status viewer have been replaced with a compact, translucent, icon-only action rail (`StatusActionRail`) built from real `@expo/vector-icons` Ionicons — a heart (React), a chat bubble (Reply), and a paper-plane (Share) — stacked vertically along the right edge of the viewer. All existing Status behavior, gestures, backend routes, notification behavior, and video performance are unchanged. The reaction mutation is now optimistic and sequence-guarded against rapid taps, duplicate requests, and stale responses. No new backend routes were added or modified. The rail was verified with 23 new automated tests (17 component-level, 6 screen/state-machine-level) plus direct, observed interaction on the already-running iOS Simulator dev client (screenshots below). No physical device was available in this session, so physical-device QA was not performed and is not claimed.

## 2. Before / after

**Before:** `StatusViewerCard` rendered three plain-text `Pressable` buttons in a horizontal row reading "React", "Reply", "Share" (bare `Text`, no icons).

**After:** `StatusActionRail` renders three 48×48pt translucent glass icon buttons stacked vertically on the right edge: a heart (outline when unreacted, filled + tinted + count when reacted), a chat-bubble outline, and a paper-plane outline. No literal "React"/"Reply"/"Share" text is rendered anywhere in the component tree; all three controls retain full accessibility labels.

## 3. Status component found

Audited and confirmed the real production Status implementation before making any change:
- `mobile-native/src/screens/StatusScreen.tsx` — owns Status list/state, `handleReact`, `handleShare`, `submitReply`, and renders the viewer inside a `Modal`.
- `mobile-native/src/components/StatusViewerCard.tsx` — the full-bleed per-Status viewer card (media, progress bar, author header, gesture zones, caption, music attribution) that previously rendered the three text action buttons.
- `mobile-native/src/api/status.ts` — Status domain/API layer (`reactToStatus`, `replyToStatus`, `shareStatus`, types).
- Backend routes confirmed via direct `bot.py` audit: `POST /api/pulse/status/<id>/react`, `POST /api/pulse/status/<id>/reply`, `POST /api/pulse/status/<id>/share`.

## 4. Files changed

| File | Change |
|---|---|
| `mobile-native/src/api/status.ts` | Added `StatusReactionType`, `DEFAULT_STATUS_REACTION`, `STATUS_REACTIONS` (curated reaction vocabulary, all legal for the freeform backend route), `describeStatusReactionError`, client-local `viewer_reaction` field on `PulseStatus`, changed default reaction from legacy `"fire"` to `"love"`. |
| `mobile-native/src/components/StatusActionRail.tsx` | **New.** Reusable icon-only action rail: heart (tap/long-press), chat bubble, paper-plane. Owns its own press/tray animation state; no Status-wide re-render on interaction. |
| `mobile-native/src/components/StatusViewerCard.tsx` | Replaced the 3-button text row and `Action` sub-component with `<StatusActionRail>`; double-tap-to-like now applies the default Love reaction (was `"fire"`); added `reactionPending`/`reactionError` props. |
| `mobile-native/src/screens/StatusScreen.tsx` | Rewrote `handleReact` as a sequence-guarded optimistic mutation (`reactionSeqRef`); added `reactingIds`/`reactionError` state with 3.2s auto-clear; `ReplyModal`'s `TextInput` now `autoFocus`s. |
| `mobile-native/src/components/__tests__/StatusActionRail.test.tsx` | **New.** 17 component tests. |
| `mobile-native/src/screens/__tests__/StatusScreen.reaction.test.tsx` | **New.** 6 integration tests for the reaction state machine. |

No other files were touched. No backend (`bot.py`) changes were made.

## 5. Icon mapping

| Action | Icon (unselected → selected) | Library | Behavior |
|---|---|---|---|
| React | `heart-outline` → `heart` (tinted per reaction color, default pink `#ff5fa8` for Love) | Ionicons | Tap with no current reaction → applies Love. Tap with an existing reaction of a *different* type → replaces it (mirrors production backend, which always replaces). Tap on the *already-selected* reaction → no-op (no request sent). Long-press (280ms) → opens a 7-item reaction tray (`love, fire, clap, laugh, wow, hundred, pulse`), all legal values for the existing freeform `reaction_type` field. |
| Reply | `chatbubble-outline` | Ionicons | Tap opens the existing reply composer (`ReplyModal`), auto-focused, same Status context, same `replyToStatus` route. |
| Share | `paper-plane-outline` | Ionicons | Tap invokes the existing `handleShare` → `shareStatus` route → native `Share.share`, unchanged. |

## 6. Reaction behavior

- Route reused exactly as-is: `POST /api/pulse/status/<id>/react` (freeform `reaction_type` string, always replaces the caller's prior reaction, returns aggregate `reaction_count`).
- **Optimistic, sequence-guarded mutation** (`StatusScreen.handleReact`): each call bumps a per-status `reactionSeqRef` sequence number; only the response matching the *latest* sequence for that status is applied (success or failure). This means rapid taps never let a stale/older response overwrite a newer optimistic state, and there is no count drift — verified directly by an automated test that fires Love then Fire before the first request resolves, and asserts the final count only reflects the second (Fire) response.
- Duplicate-tap guard: tapping the currently-selected reaction again returns immediately without any network call (verified by test).
- Failure rollback: on network/API error, the optimistic `viewer_reaction`/`reaction_count` are reverted to their pre-tap values and a transient (3.2s auto-clearing) error message is shown; the UI is never left in a state claiming a reaction that the server didn't confirm (verified by test).
- **No "remove reaction" is offered or implied anywhere in the UI.** The production route has no removal contract (confirmed via backend audit — see "Known limitations" below), so the rail only ever supports applying/replacing a reaction, matching the mission's explicit constraint.

## 7. Reply behavior

Unchanged route (`replyToStatus` → `POST /api/pulse/status/<id>/reply`), unchanged composer (`ReplyModal`). The only change is that the `TextInput` now receives `autoFocus`, satisfying the "focus input" requirement without introducing a second/duplicate composer or a new route.

## 8. Share behavior

Unchanged: `handleShare` calls `shareStatus` (existing route) then the native `Share.share(...)` sheet, exactly as before. No new route, no new notification request, no WebView/analytics change. The rail's Share button additionally exposes `accessibilityState={{ disabled }}` while a share is already in flight (`shareBusy`), preventing a duplicate concurrent share tap — this reuses the screen's existing `busyId` state rather than introducing new state.

## 9. Gesture isolation

- The rail (and, when open, the reaction tray) render inside `StatusViewerCard`'s existing full-bleed card, as siblings via a `Fragment` return from `StatusActionRail` — this lets the tray's full-screen backdrop size itself against the card rather than the narrow rail container, without needing `pointerEvents` workarounds.
- All rail buttons are standard `Pressable`s with their own `onPress`/`onLongPress`; they sit outside and do not overlap the existing left/right/center tap zones or the press-and-hold-to-pause zone documented in `StatusViewerCard`'s gesture handling, so prev/next tap, mute tap, double-tap-Like, and hold-to-pause are all unaffected.
- Long-press on the heart opens the tray via the button's own `onLongPress` (280ms `delayLongPress`) and does not trigger the media's hold-to-pause gesture, because the rail sits in front of (and outside) the media's gesture-capturing zone.

## 10. Backend routes preserved

No backend files were modified in this mission. Confirmed via direct route inspection that `/api/pulse/status/<id>/react`, `/api/pulse/status/<id>/reply`, and `/api/pulse/status/<id>/share` are called with the same method, path, and payload shape as before this change.

## 11. Accessibility

- `accessibilityRole="button"` on all three rail controls.
- Labels: `"React to Status"`, `"Reply to Status"`, `"Share Status"`.
- React button: `accessibilityHint="Applies a Love reaction. Double tap and hold for more reactions."`, `accessibilityState={{ selected, busy }}`.
- Reaction count exposed as its own accessible text: `"1 reaction"` / `"N reactions"` (only rendered when count > 0).
- Tray: `accessibilityRole="menu"` labeled `"Open reaction options"`, each option `accessibilityRole="menuitem"` labeled `"<Reaction> reaction"` with `accessibilityState={{ selected }}`; backdrop labeled `"Close reaction options"`.
- `AccessibilityInfo.announceForAccessibility` fires on every reaction change (e.g. `"Love reaction selected"`).
- All three touch targets are 48×48pt (≥ the 44×44pt minimum).
- Selection is never color-only: the heart also switches from outline to filled glyph, and `accessibilityState.selected` is set.
- Reduced Motion: `useLogiNexusReducedMotion()` (the project's existing canonical hook) gates the press-pulse and count-transition `Animated` sequences — when enabled, state still updates correctly but the animations are skipped (verified by test).

## 12. Performance

- All animations use React Native's built-in `Animated` API with `useNativeDriver: true` (project convention; no `react-native-reanimated` dependency was added).
- `StatusActionRail` owns its own local animation/tray state — a reaction count change does not re-render the rest of `StatusViewerCard` beyond the props it already received.
- The reaction tray is only mounted while open (conditional render), not permanently mounted off-screen.
- No new dependency was added; `@expo/vector-icons` and `expo-haptics` were already project dependencies.
- No animation is tied to video playback frames; only opacity/scale/translateY are animated (no layout-dimension animation).

## 13. Tests

`npx tsc --noEmit` — clean, zero errors.

`npx jest` — full suite: **9 suites / 85 tests passed**, including the two new files:

- `src/components/__tests__/StatusActionRail.test.tsx` — **17 tests**: no literal React/Reply/Share text rendered; accessible labels present; count hidden at zero and shown/pluralized above zero; tap applies Love and announces it; long-press opens the tray with the backend-supported reaction set; tray selection invokes `onReact` and closes the tray; backdrop tap closes without reacting; `selected`/`busy` accessibility state reflected; Reply/Share tap invoke their handlers; Share disables while busy; plain tap never opens the tray; Reduced Motion doesn't block the tap handler.
- `src/screens/__tests__/StatusScreen.reaction.test.tsx` — **6 tests**: optimistic Love reaction applied on tap then reconciled with the server's `reaction_count`; failed reaction rolls back count and selection; a stale (slow-resolving) first response is discarded once a second reaction has already resolved, so the displayed count never drifts; tapping an already-selected reaction sends no duplicate request; Reply tap opens the composer without a duplicate composer or new route; Share tap invokes the existing `shareStatus` route without navigating to a new route.

`git diff --check` — clean (no whitespace errors) on all touched files.

`npx expo-doctor` — 18/18 checks passed.

## 14. Simulator QA (directly observed)

This session found Metro already running and connected to a booted **"PulseSoc iPhone 16 Pro"** simulator with the dev client installed from a prior session, live-reloading this exact working tree. This was used for genuine, directly-observed visual/interaction verification (not simulated or inferred):

- **Observed:** Opening a Status now shows the new translucent glass icon rail (heart / chat-bubble / paper-plane) on the right edge — the literal words "React", "Reply", "Share" are gone.
- **Observed:** Tapping the heart fills it pink and shows an incrementing reaction count beneath it; re-opening the same Status later in the session still shows the reaction as applied (client-session-local state working as designed).
- **Not conclusively observed in this session:** the long-press reaction tray and the Reply/Share tap outcomes. Simulating a long-press via host-OS mouse-hold repeatedly caused the macOS Simulator window itself to lose frontmost focus (an environment/tooling artifact of driving the simulator with a held mouse button, not an app crash), and the single fixture Status was a short auto-playing video whose auto-advance-to-close behavior (existing, unmodified behavior) interfered with reliably timing Reply/Share taps against a stable screenshot. These three behaviors are instead verified by the automated tests in Section 13 (`long-pressing the heart opens the reaction tray...`, `opens the reply composer...`, `shares via the existing production share flow...`), which exercise the exact same component code paths.

## 15. Physical-device QA

**Not performed.** No physical iPhone was connected or made available in this session. Per the mission's explicit instruction, a physical-device PASS is not being claimed. Everything reported above was either directly observed on the Simulator (Section 14) or verified by automated tests (Section 13).

## 16. Known limitations

- **No per-viewer reaction on cold load.** The production Status rail/list payload (`pulse_status_payload()` in `bot.py`) only returns an aggregate `reaction_count`, never a per-viewer "did I react, and with what" field (confirmed by direct backend audit; this differs from Reels, which do compute a server-side `viewer_reaction`). `PulseStatus.viewer_reaction` is therefore a **client-local-only** field, populated purely from this session's own optimistic/confirmed reaction state. If the app is restarted, or a different Status is loaded fresh from the server, the heart will render unfilled even if the user reacted in a previous session — this is a pre-existing backend gap, not something this mission's frontend change can fix without a backend change.
- **No reaction removal.** The backend route always replaces the caller's prior reaction; there is no DELETE/removal endpoint. The UI intentionally never advertises a "remove reaction" affordance.

## 17. Rollback notes

This is a purely additive/frontend change confined to five files (`api/status.ts`, `components/StatusActionRail.tsx` [new], `components/StatusViewerCard.tsx`, `screens/StatusScreen.tsx`, plus two new test files). To roll back: revert these files to their prior committed versions (git history preserves the exact prior 3-button text-row implementation); no backend, database, or route changes are involved, so no server-side rollback is needed.
