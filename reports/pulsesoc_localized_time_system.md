# PulseSOC Native — Localized Time, Date & Time-Zone System

Mission: every date/time displays correctly for each viewer based on their device
time zone, locale, and DST rules. Store absolute UTC instants; convert to the
viewer's local zone only at display time.

Date: 2026-07-20 · Branch: `release/undx-nexus-core-v4`

## 1. Current timestamp audit

- Nearly all display already funneled through one helper: `formatShortTime` in
  `mobile-native/src/utils/format.ts`, used by PostCard, StatusViewerCard,
  ChatScreen, ReelsScreen, StatusScreen, EventsScreen, LiveScreen, GroupsScreen,
  NotificationCenter, ActivityInbox, AccountCenter, PostDetail, TrustSafety, etc.
- Ad-hoc formatting existed in one place: `BuyerOrdersScreen.formatDate`
  (`toLocaleDateString`).
- Duration counters (CallScreen, LiveHostSessionScreen, ReelsScreen `cacheAge`)
  measure elapsed intervals, not stored instants — correctly left unchanged.
- Time-zone detection already existed in ChatScreen via
  `Intl.DateTimeFormat().resolvedOptions().timeZone` (kept).
- No date library was installed; Hermes (Expo 54 / RN 0.81) provides `Intl` with
  IANA zone + DST support, which the new service builds on.

## 2. Legacy timestamp problems found

- `formatShortTime` used `toLocaleTimeString`/`toLocaleDateString` with no zone
  control and a same-day-vs-other-day branch — no relative labels, no manual
  override, no year handling.
- No handling for legacy timestamps lacking an offset. `parseServerInstant` now
  normalizes bare ISO strings as **UTC** (never device-local).
- No central place to change/override the viewing zone.

## 3. Central service implemented

`mobile-native/src/core/localTime.ts` — pure, engine-backed (`Intl`), no
hard-coded offsets:

- `parseServerInstant(value)` — string/number/Date → absolute instant; legacy
  no-offset strings normalized as UTC; unparseable → null.
- `getDeviceTimeZone()` / `getResolvedLocale()` / `getActiveTimeZone()` /
  `getCurrentUserTimeZone()` / `refreshTimeZoneContext()`.
- Manual override: `getManualTimeZone`, `setManualTimeZone`,
  `loadTimeZonePreference` (persisted via AsyncStorage), `isValidTimeZone`.
- Formatters: `formatAbsoluteDate`, `formatClockTime`, `formatRelativeTime`,
  `formatShortTimestamp`, `formatDateRange`, `formatScheduledTime`,
  `formatAccessibleTimestamp`.

`mobile-native/src/core/TimeZoneContext.tsx` — React layer:

- `TimeZoneProvider` (mounted in `App.tsx` above the navigation tree) loads the
  saved preference and listens to `AppState` to detect travel/zone changes on
  foreground, bumping a `revision` so bound formatters re-render.
- `useTimeZonePreference()`, `useLocalTime()` (context-bound formatters), and
  `useRelativeTime()` (recalculates every 30s while a screen is mounted so
  relative labels never go stale).

## 4. Backend contract changes

No backend code changed in this pass. The client already receives ISO
timestamps; the service tolerates both offset-bearing and legacy no-offset
values. Documented contract for the backend team: return UTC with explicit
offset, return `eventTimeZone` for wall-clock/scheduled events, keep recurrence
rules separate from formatted text, and use server time for expiry/ordering.
`formatScheduledTime(value, eventTimeZone)` is ready to consume `eventTimeZone`
as soon as the API supplies it.

## 5. Screens and features migrated

- `formatShortTime` now delegates to `formatShortTimestamp` → **every** existing
  call site (feed, reels, statuses, messages, notifications, activity, groups,
  live, events, account/security, post detail, trust & safety) is instantly
  localized to the viewer's active zone with relative labels for recent content.
- `BuyerOrdersScreen.formatDate` → `formatAbsoluteDate(..., { withYear: true })`.
- New `RegionTimeScreen` (Settings → "Language, Region & Time") shows the active
  zone + a live sample, an **Automatic** option, and a curated IANA picker; route
  registered in `AppNavigator`/`types.ts`, linked from `SettingsScreen`.

## 6. Scheduling and recurrence behavior

- Instant-based events (posts, messages, calls, notifications) use relative/
  absolute local formatting.
- Wall-clock/scheduled events use `formatScheduledTime`, which shows the viewer's
  local time and appends the event's own zone when different
  ("1:30 PM your time · … New York"). Recurrence expansion remains a backend
  responsibility using IANA rules (no client-side 24h-millisecond arithmetic).

## 7. Daylight-saving test results

Automated (`src/core/__tests__/localTime.test.ts`): London BST vs GMT and
Los Angeles PDT vs PST verified from summer/winter UTC instants — offsets applied
correctly from IANA rules, none hard-coded. **PASS.**

## 8. Time-zone change / conversion test results

Same UTC instant renders correctly across LA / New York / London / Kolkata /
Tokyo; date-line crossing into the next local day (Auckland); explicit 24-hour
preference; manual override supersedes and reverts to Automatic; invalid zones
rejected. 19/19 automated tests **PASS**.

## 9. Physical-device results

Not yet run on the P3r7or iPhone this pass — code-complete and green in CI/tsc.
Recommended device checks: change the device zone while backgrounded and
confirm feeds refresh on foreground; toggle 24-Hour Time; set a manual override
in Settings and confirm it persists across relaunch.

## 10. Simulator results

`npx tsc --noEmit` clean. `jest` for the new suite and all touched suites: 33+19
tests pass (a pre-existing VirtualizedList timer-leak warning is unrelated).

## 11. Remaining risks

- **12/24-hour source.** Clock format follows the resolved locale's default hour
  cycle, not iOS's independent "24-Hour Time" toggle. Adding `expo-localization`
  (`uses24hourClock`) would make this exact; currently approximated + overridable
  via the `hour12` option.
- Remote push payloads with server-formatted times could still conflict with the
  device; prefer structured timestamps formatted on-device (documented for the
  notifications/backend team).
- Relative labels are opt-in-live only where `useRelativeTime` is used; the broad
  migration recomputes on re-render/foreground rather than on a per-item timer.

## 12. Recommendation

**GO** for the client-side localized time system. It is centralized, DST- and
locale-aware, override-capable, persisted, and fully covered by tests with a
clean type-check. Follow-ups (non-blocking): add `expo-localization` for exact
24h detection, wire `eventTimeZone` from the backend for scheduled events, and
run the physical-device travel/DST checks on P3r7or before store submission.
