# PulseSoc Native — App Store Compliance Matrix

- **Policy audit date:** 2026-07-20
- **Release branch:** `release/undx-nexus-core-v4`
- **Release commit:** `7ac897135424efc60dde1549433803ce091c5895`
- **Working tree:** DIRTY (uncommitted changes, including in `mobile-native/src`)
- **Bundle id:** `com.pulsesoc.nativeapp` · **Team:** `87ZC69AGSR`
- **Native version/build:** `1.0` / `1` — **conflicts with** `app.json` `0.1.0`
- **Published App Store version:** UNKNOWN (no App Store Connect access in this environment)
- **Final judgment:** **NO-GO**

> Note on policy sourcing: this matrix is derived from repository evidence and standing App Review Guidelines knowledge. Apple's live guidelines must be re-read in App Store Connect immediately before any real submission (Guideline numbers below are indicative).

| Apple rule / requirement | PulseSoc feature | Current implementation | Risk | Required action | Status |
|---|---|---|---|---|---|
| 2.1 App completeness; distribution signing | iOS archive | All configs signed **Apple Development**; no Apple Distribution / App Store profile (`project.pbxproj`) | **P0** | Configure App Store distribution signing (account-side) | OPEN |
| Push in production | Notifications | `aps-environment = development` (`.entitlements`) | **P0** | Production APNs entitlement for Release build | OPEN |
| 3.1.1 In-App Purchase | Premium subscription, marketplace, creator | **No IAP SDK**; digital-purchase entry points (premium, marketplace checkout, seller payouts, course enrollment) now gated behind `DIGITAL_COMMERCE_ENABLED` (default OFF) so no web-payment surfaces render in production. Underlying web-purchase code preserved. | **P0** | Ship StoreKit for digital goods before re-enabling the flag; keep entry points hidden until then | OPEN (mitigated: entry points hidden) |
| Version must exceed published build; single listing | Versioning | Native `1.0/1` vs `app.json 0.1.0`; product self-references "WebView app" | **P0** | Read published version in ASC; set higher; confirm existing record | OPEN |
| 4.2 Minimum functionality / not a web wrapper | Profile, seller, dashboard, events, creator, calls, etc. | Duplicate `Linking.openURL` web buttons removed or native-routed across Marketplace, SellerStore, ContentPlanner, GrowthCenter, SellerListingComposer, CreatorStudio, Profile, AlertManagement, IntelligenceCenter, CameraStudio, Courses, Events. Remaining escapes limited to legal/support pages (exempt), no-native-equivalent fallbacks, and flag-gated commerce. | **P1** | Continue building native equivalents for the documented residual fallbacks | OPEN (largely mitigated) |
| iPad support quality | Layout | `supportsTablet: false`; `TARGETED_DEVICE_FAMILY = 1` (iPhone-only) | ✅ Low | — | FIXED (iPhone-only) |
| 5.1.1 Purpose strings must be specific | Face ID | Was generic placeholder → **fixed** to honest purpose | P1 | — | **FIXED** |
| Release freeze integrity | Build hygiene | Dirty tree with uncommitted release work | P1 | Commit + tag RC before archive | OPEN |
| 5.1 Privacy manifest / App Privacy | Data collection | `NSPrivacyCollectedDataTypes` empty for a data-collecting social app | P2 | Reconcile manifest + ASC App Privacy; verify SDK manifests | OPEN |
| Background modes justified | Live/calls audio | `UIBackgroundModes: audio` declared | P2 | Keep if used; document in reviewer notes | OPEN |
| 5.1.1(v) Account deletion | Settings | `AccountCenterScreen.tsx` present | ✅ Low | Verify end-to-end on device | PASS (verify) |
| 1.2 UGC safety (report/block) | Feed, Reels, profiles | Report/block wired (Reels, SafetyHub, Home, PostCard) | ✅ Low | Verify server-side enforcement | PASS (verify) |
| ATS / secure transport | Networking | `NSAllowsArbitraryLoads=false`, HTTPS base URL | ✅ Low | — | PASS |
| Static gates | CI | `tsc` 0 errors; `jest` 241/241 | ✅ | — | PASS |

## Items requiring App Store Connect / Apple Developer account (cannot be done from this environment)
1. Distribution signing identity + App Store provisioning profile (R1).
2. Production push entitlement wired to an App Store profile (R2).
3. Reading the published version and setting a higher version/build (R4).
4. Confirming `com.pulsesoc.nativeapp` maps to the existing PulseSoc app record (R4).
5. TestFlight upload, physical-device QA, remote live-audio verification, and submission.

## Local mitigations landed (2026-07-20)
Within the local-only scope, the P1 review risks have been addressed: `supportsTablet` set to `false` with `TARGETED_DEVICE_FAMILY = 1` (iPhone-only), and the ~30 internal web escapes systematically removed, native-routed, or flag-gated. Digital-purchase entry points are hidden behind `DIGITAL_COMMERCE_ENABLED` (default OFF), so the 3.1.1 web-payment surfaces no longer render in production while the underlying code is preserved for a future StoreKit implementation. Static gates remain green (`tsc` 0 errors; `jest` 241/241).

## Bottom line
This project is currently a **development-signed, phone-side-loaded build**, not an App-Store-distribution-ready release candidate. Despite the local P1 mitigations above, four independent **P0** blockers (distribution signing, production push, StoreKit-vs-web digital payments, version/listing identity) each independently force **NO-GO**. None can be honestly resolved without Apple account access, which this environment does not have.
