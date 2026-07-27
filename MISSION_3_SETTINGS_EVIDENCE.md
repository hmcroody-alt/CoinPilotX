# Mission 3 — Settings Platform: Verification Evidence

PulseSoc Native (`mobile-native/`) + Flask backend (`services/`, `bot.py`)
Verified 25 July 2026.

## Summary of the gate

Three checks were run to completion on the final tree. All three are green.

| Gate | Command | Result |
| --- | --- | --- |
| Native unit + integration tests | `npx jest` | 76 suites, **858 tests, 858 passed** |
| Native typecheck | `npx tsc --noEmit` | **0 errors** |
| Backend tests | `python3 -m unittest tests.test_pulse_settings_routes` | **44 tests, 44 passed** |

Of the 858 native tests, 179 belong to the twelve suites that cover the settings
platform and its navigation directly; 51 of those are settings-screen and
settings-registry assertions specifically.

## What was built

The settings platform is now a registry-driven projection rather than a
hand-maintained screen. `src/settings/registry.ts` holds one declaration per
destination, and that single declaration simultaneously produces the sectioned
index, the search index, and the `pulsesoc://settings/<slug>` deep link. The
three used to be maintained separately and had already drifted apart; they can
no longer disagree, because there is only one of them.

Nineteen settings destinations ship as native screens under
`src/screens/settings/`, plus the index itself. The supporting layer —
`src/settings/` — is the design system (`components/SettingsShell.tsx`,
`components/SettingsControls.tsx`), the typed preference schema
(`schema.ts`), the backend client (`api.ts`), and the synchronizing store
(`store.tsx`). Together with the screens this is 8,616 lines of TypeScript.

The backend is `services/pulse_settings_routes.py`, 1,178 lines registered onto
`webhook_app` as a Flask blueprint, serving twelve endpoints under
`/api/pulse/mobile/settings`: preference read and patch, blocked and muted list
/ add / remove, session list and revoke, data export, and account deletion.

## Acceptance criteria, one at a time

**No placeholder UI.** Every row in the index resolves to a screen with real
controls wired to the preference store. The test
`SettingsScreen › rows › renders a row for every visible registry entry`
asserts a rendered row exists for every non-developer registry entry, and
`registry.test.ts` asserts every entry's `route` names a screen the navigator
actually registers — so a declared-but-unbuilt page fails the build rather than
shipping as a dead row.

**No broken navigation.** `settingsLinking.test.ts` resolves
`settings/<id>` for every registry id and asserts it lands on that entry's
screen, that params are carried through, that a leading or trailing slash and a
mixed-case id all still resolve, and that an unknown id falls back to the
Settings tab rather than dropping the user nowhere. It also asserts the new
scheme does not swallow the pre-existing `pulse/settings/:section` routes owned
by AccountCenter.

**No WebView fallback for Settings.** `grep` for `WebView` and
`openSupportWebFallback` across `src/settings/`, `src/screens/settings/` and
`SettingsScreen.tsx` returns five hits, all of which are comments recording the
absence. Legal text is bundled in `src/screens/settings/legalContent.ts` and
rendered natively, which is what removed the last reason for a browser view.

**Every permission enforced by the backend.** The blueprint exposes
`privacy_snapshot()`, `notification_snapshot()`, `is_blocked()` and
`is_muted()` as the documented entry points other subsystems call, so the
enforcement point and the storage format are the same object. `is_blocked` is
symmetric — it checks both directions — and blocking and muting are separate
relations, because muting someone you have not blocked is a real thing users do.

**Settings persist and survive restart.** Preferences are stored as one JSON
document per user in `user_settings`, normalized on both sides of the wire. The
client normalizes against a hostile server and the server normalizes against a
hostile or outdated client; `normalize_preferences` is total, so no input
produces an invalid document. Partial patches merge group-wise and key-wise, so
an older build that knows about fewer keys cannot reset the ones it has never
heard of by round-tripping its own smaller document.

**Saving means applying.** `_side_effects()` projects `appLanguage` onto
`users.preferred_language` and `accountVisibility` onto
`users.profile_visibility`, the two fields the web app and the email templates
read. Without that projection those two settings would save and then not apply
anywhere outside the native app.

**The deletion grace period is honest.** The Data & Privacy screen tells the
user that signing back in during the 30-day window cancels a pending deletion.
The only place that can be made true is the login path, so
`cancel_scheduled_account_deletion` is called from both interactive login paths
in `bot.py`, sharing the caller's cursor so the cancellation commits with the
login rather than as a separate write that could succeed while the login fails.

**Bottom navigation is consistent app-wide.** `bottomNavCoverage.test.ts` and
`bottomNavPolicy.test.ts` enumerate every scrollable surface — Home, Reels,
Search, Saved, Groups, Status, Messenger, Notifications, Profile, Marketplace,
Settings — and assert for each that it drives the hide/reveal gesture, reserves
dock clearance rather than hardcoding a dock-sized padding, and names a source
file. `bottomNavVisibility.test.tsx` covers the gesture's failure modes
directly: a restored scroll offset must not read as an 800pt flick, a rotation
must re-prime rather than commit to whichever direction the recomputed numbers
moved, sub-threshold jitter must not toggle, a surface too short to scroll the
dock back must keep it up, and a backgrounded screen must not move a dock the
user is looking at somewhere else.

**Every string is translated in every shipped language.**
`navigatorLocalization.test.ts` asserts that no navigator title is a hardcoded
literal, that every title routes through the core catalog tier (the header
renders on the first frame, before the extended tier is warmed), and that all
eleven shipped languages carry the same navigation chrome — a partially
translated header reads as a rendering bug rather than a missing string.

## Failures found and fixed during verification

**Registry drift in two test files.** `settingsLinking.test.ts` and
`SettingsScreen.test.tsx` each hardcoded `"safety"` as the entry that declares
`params`; the registry had since moved `params` onto `"account"`. Both now
discover whichever entry declares params rather than naming one, so the
assertion survives the registry being reshuffled instead of silently ceasing to
test anything.

**A real i18n bug surfaced by a test.** `SettingsScreen.test.tsx` rendered the
screen with raw dotted keys — `settings:root.title` — where copy should be. The
cause was the `I18nContext` default value, whose `t` echoed the key instead of
translating. The provider's job is to re-render consumers when the language
changes; translation itself is module-level state, so the default now delegates
to the same `translate` call the provider uses. Any subtree that renders outside
the provider — a test harness, a modal mounted above it, an error boundary's
fallback — now shows real copy rather than a key. The test also now warms the
`en` catalogs in `beforeAll`, which the app does before its first frame.

## Known caveat

`DeveloperSettingsScreen`'s `showPerfOverlay` toggle arms tracing through
`configurePerfTracing({ enabled })`, but `App.tsx` gates the overlay's *mount*
behind the build-time `PERF_OVERLAY_ENABLED` flag. On a release build the toggle
therefore arms tracing without visibly mounting the overlay. This is
pre-existing behaviour of the build flag rather than a settings defect, but it
is the one control on the platform whose effect is not fully visible where it is
toggled.

## Reproducing

```
cd mobile-native && npx jest && npx tsc --noEmit
cd .. && PYTHONPATH=$(ls -d .venv/lib/*/site-packages) \
  python3 -m unittest tests.test_pulse_settings_routes
```

The backend suite runs on `unittest` rather than pytest, and drives a real Flask
test client against an in-memory SQLite database with `bot` replaced by a fake
exposing only the four functions the blueprint actually uses. Importing the real
`bot` module would cost tens of thousands of lines and a live config to test
none of it.
