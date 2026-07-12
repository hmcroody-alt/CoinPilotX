# PulseSoc Native Home Simulator QA

Date: 2026-07-11

## Status

Xcode iPhone Simulator remains the primary QA target for Home parity. This pass focused on code-level parity alignment and audit guardrails after the owner directive changed visual authority back to production UI.

## Evidence Sources

- Production reference screenshots supplied by Roody:
  - `/Users/hmcherie/Desktop/Screenshot 2026-07-10 at 12.21.16 PM.png`
  - `/Users/hmcherie/Desktop/Screenshot 2026-07-10 at 12.21.36 PM.png`
- Fresh native iPhone 17 Pro Simulator capture:
  - `reports/screenshots/native-home-production-parity/native-home-current-bundle-route2.png`
- Final native iPhone 17 Pro Simulator capture after this size pass:
  - `reports/screenshots/native-home-production-parity/native-home-final-parity-nativeapp-iphone17pro.png`
- Current-bundle iPhone 17 Pro feed-card capture after the inline-comment pass:
  - `reports/screenshots/native-home-production-parity/native-home-feed-card-inline-comment-verified.png`
- Current-bundle iPhone 17 Pro Home upper-section capture from the same run:
  - `reports/screenshots/native-home-production-parity/native-home-qa5110-home-return.png`

## QA Notes

- Current bundle shows the production-facing corrections: Home header no longer carries the extra Home subtitle, Status copy is production-aligned, and the composer presents as `Pulse Composer`.
- Code-level parity now also includes the production-shaped feed card controls and inline comment composer; a fresh simulator capture is required after this patch to visually confirm proportions.
- The current simulator run confirms the production-shaped feed-card controls and inline comment composer are visible in the native iPhone app.
- The normal local QA proxy on `127.0.0.1:5108` returned empty replies during this run, so authenticated visual QA used a temporary local-only API fixture on `127.0.0.1:5110`. No production code path or backend contract was changed for that fixture.
- Final pass proxy repair: `127.0.0.1:5107` backend health returned 200, the stale `5108` proxy process was restarted, and `http://127.0.0.1:5108/health` now returns 200 through the local QA proxy.
- Final visual-size pass tightened the existing hero, rails, composer, feed card, and inline-comment proportions without adding duplicate Home components.
- Available simulator matrix on this machine: booted iPhone 17 Pro plus available iPhone 17 Pro Max and iPhone 17e. The code/layout pass is ready for those captures once the local QA proxy/backend path is healthy.
- Fresh iPhone 17 Pro capture confirms the tightened hero, Status rail, composer, feed filter, and bottom navigation render inside the native app surface after proxy repair.
- The visible Status error in the final capture is an authenticated/local fixture data availability state; the native state is intentional and does not indicate a missing production control.
- The screenshot still shows the Expo dev warning banner; this is a development overlay and not a production UI element.
- A full side-by-side matrix across compact iPhone, Pro Max, and wide web remains required before claiming 95%+ exact visual parity.
- This pass does not claim final Home replacement readiness; it restores production UI authority, adds parity audit coverage, and uses iPhone 17 Pro simulator proof points as the primary visual QA track.
