# PulseSoc Native Home — Approved Reference Implementation

Date: 2026-07-18

Subsystem: Home

Reference: `codex-clipboard-8d81e69f-a6b7-481c-be5d-e96b87e4509c.png`

## Scope and production safety

This milestone applies the approved spatial Home hierarchy to the existing native Home. It does not create a second Home, identity system, feed, Status system, Composer, radio backend, or navigation stack. Production WebView source and backend behavior remain unchanged. The iOS development artifact continues to use the separate `com.pulsesoc.nativeapp.dev` identity and `PulseSoc Native Dev` display name.

The reference supplies visual hierarchy only. Generated people, avatars, counts, health labels, Statuses, and posts were not copied. Native surfaces continue to use canonical API responses and authenticated identity state.

## Reference-to-code matrix

| Reference element | Existing native component | Production source | Reuse or modify | Required wiring | QA evidence |
| --- | --- | --- | --- | --- | --- |
| Menu | `LogiNexusGlobalHeader` / `IconButton` | Existing drawer navigator | Modify icon and treatment | `onOpenDrawer` | Audit + simulator |
| Brand | `LogiNexusGlobalHeader` home mode | PulseSoc product identity | Modify wordmark and waveform | Static accessible brand | Audit + simulator |
| Search | `IconButton` | Existing Search tab | Modify icon only | `Tabs/Search` | Code path + simulator |
| Notifications | `IconButton` badge | Canonical activity badge | Modify icon only | `ActivityInbox`; real count formatting | Audit + simulator |
| Avatar | Header profile button | Authenticated identity | Reuse | Real avatar or real initials; Profile route | Code path + simulator |
| Hero | `PulseNetworkHero` | Existing native Home aggregation | Modify composition | Feed/Status-derived aggregate values | Audit + simulator |
| Hero artwork | Hero decorative React Native layers | Local design primitives | Modify | No network image, video, or particle engine | Audit + simulator |
| Network label | `LogiNexusBadge` | Product copy | Reuse | Static `Pulse Network` label | Simulator |
| Network status | Hero health pill | Feed/Status cache state | Modify | `Connected` or truthful `Cached` | Audit + simulator |
| Heading | Hero mood copy | Existing feed state | Reuse | Derived display copy, no fabricated account data | Code path + simulator |
| Copy | Hero summary | Canonical feed length/cache state | Modify | Aggregate-only, no fake values | Audit + simulator |
| Radio | `PulseRadioHeroControl` | `src/core/pulseRadio.ts`, `src/api/radio.ts` | Modify and isolate | Explicit user play, paused default, truthful state | Audit + simulator |
| Metrics | `HeroMetricBlock` | Feed/Status API results | Modify density | Signals, unique creators, actual live Status count | Audit + simulator |
| UNDX | `HeroTile` | Existing Pulse AI/UNDX route | Reuse | Existing navigation only | Code path + simulator |
| Safety | `HeroTile` | Existing Safety Hub | Reuse | Existing Safety route and aggregate alert count | Code path + simulator |
| Status header | `StatusRail` | Status feature | Modify typography | Existing Status route | Simulator |
| Add Status | `StatusRail` add control | Existing Status creator | Reuse | `Status` with `openCreator` | Code path + simulator |
| Status items | `StatusRail` | `src/api/status.ts` | Modify visual treatment | Real order, avatar, seen/unseen metadata | Audit + simulator |
| Composer title | `HomePulseComposer` | Existing production Composer | Modify | `CREATE A SIGNAL` | Audit + simulator |
| Draft indicator | `HomePulseComposer` | Existing native draft state | Modify behavior | Render only for real draft/sending state | Audit + simulator |
| Composer input | Existing collapsed/expanded Composer | Post/Reel production APIs | Reuse | Preserved body/media/validation/retry | Code path + simulator |
| Audience | Existing visibility selector | Production privacy contract | Reuse | Canonical audience value | Code path + simulator |
| Photo | Existing quick action | Existing native media picker/uploader | Reuse | `media.chooseImages` | Audit + simulator |
| Video | Existing quick action | Existing native video picker/uploader | Reuse | `media.chooseVideo` | Audit + simulator |
| Camera | Existing quick action | Existing Camera Studio route | Reuse | `onOpenCamera` | Audit + simulator |
| Create | Existing collapsed Create control | Existing full Composer | Reuse | Expand canonical Composer | Code path + simulator |
| Filters | Existing Home feed rail | `src/api/feed.ts` and backend feed contract | Modify visual weight | Existing selection/cache/pagination behavior | Audit + simulator |
| Feed | Existing virtualized `FlatList`/`PostCard` | Canonical feed API | Reuse | First post, loading, empty, or error directly below filters | Audit + simulator |
| Home tab | `LogiNexusBottomNavigation` | Existing tab navigator | Modify icon/treatment | Existing Home route | Audit + simulator |
| Reels tab | Same | Existing Reels route | Modify icon/treatment | Existing Reels route | Audit + simulator |
| Create tab | Same | Existing Home Composer intent | Modify central treatment | `Home { openComposer: true }` | Audit + simulator |
| Messages tab | Same | Existing Messenger route | Modify icon/treatment | Existing Messenger route and real badge | Audit + simulator |
| Profile tab | Same | Existing Profile route | Modify icon/treatment | Existing Profile route | Audit + simulator |

## Implementation decisions

### Header and navigation

- Replaced text glyph approximations with the existing Expo Ionicons set.
- Added brief selection haptics to actual header and tab presses only.
- Kept badge counts canonical and retained the existing threshold formatter.
- Preserved exact bottom order: Home, Reels, Create, Messages, Profile.

### Hero and ambient motion

- Built the space/planet, skyline, glow, network lines, and nodes from clipped local native views. No remote hero asset, video, timer-driven particle system, or continuous JavaScript frame loop was added.
- Uses two decorative loop classes: a very slow native-driver transform and a low-amplitude opacity/glow breath. The network opacity shares the ambient layer and does not touch data state.
- Motion starts only while Home is focused, the app is active, Reduce Motion is off, and Low Power Mode is off. Cleanup stops every animation on dependency change or unmount.
- Decorative layers use `pointerEvents="none"`.
- Removed the former visible internal `LN` hero artifact and replaced fabricated `Optimal`/`Live` fallbacks with truthful `Connected`, `Cached`, or an em dash.

### Radio

- The radio remains paused on launch and requires explicit user intent.
- The radio subscription lives in memoized `PulseRadioHeroControl`; progress/state changes do not update feed, Composer, or Status state.
- Accessibility announces paused, connecting, playing, or unavailable.
- Backgrounding an active playback invokes the existing radio pause/unload path.

### Density and first content

- Compressed hero metric, system-card, and internal spacing so Home controls do not consume the entire screen.
- Kept exactly one refined Status empty state, never repeated generated circles.
- The collapsed Composer now shows `CREATE A SIGNAL`; `DRAFT` appears only for a real draft.
- Loading no longer replaces the entire Home with a center screen. Header, hero, Status, Composer, and filters remain in place with a correct feed loading state directly below the filters.

## Rendering and accessibility review

- Feed remains a virtualized `FlatList` and existing `PostCard` implementation.
- Radio state is isolated in a memoized child.
- Hero animation uses native-driver transform/opacity only.
- AppState, route focus, Reduce Motion, and Low Power Mode gate hero animation.
- Animation and accessibility listeners are removed during cleanup.
- Header, Radio, metrics, Status controls, Composer actions, filter controls, and bottom tabs retain explicit accessibility roles/labels/states.
- Current layout retains iOS safe-area handling through the shared header and floating navigation.

## Verification record

| Check | Result | Classification |
| --- | --- | --- |
| TypeScript | PASS | Automated |
| Approved-reference audit | PASS | Automated |
| Existing generated-concept audit | PASS | Automated |
| Expo Doctor | PASS, 17/17 | Automated |
| Release iOS Simulator build | PASS | Build |
| iPhone 16 Pro simulator runtime | PASS; approved Home captured | Simulator |
| Compact simulator | Source breakpoint verified; authenticated Home screenshot not captured | Code-path only |
| Pro Max simulator | App/runtime PASS; authenticated Home gated by separate signed-out session | Simulator, session-gated |
| Physical iPhone 16 Pro build/install/launch | PASS; process remained alive after launch | Physical device |
| Real-account feed/Status/Composer route actions | Owner-controlled; not performed automatically | Physical device |
| WebView regression | No WebView files changed | Source boundary |

Simulator evidence:

- `reports/screenshots/native-home-approved-reference-2026-07-18/iphone16pro-home.png`
- `reports/screenshots/native-home-approved-reference-2026-07-18/iphone16promax-home.png` (signed-out runtime state; not used as Home parity evidence)

Physical artifact:

- Display name: `PulseSoc Native Dev`
- Bundle identifier: `com.pulsesoc.nativeapp.dev`
- API: `https://pulsesoc.com`
- Embedded JS bundle: present
- Install: passed
- Launch: passed
- Post-launch process check: passed
- Production bundle was never targeted, removed, or modified. The device app inventory did not independently enumerate an App Store PulseSoc bundle during this pass, so installed-production-app presence is not claimed from tooling alone.

## Performance evidence and limits

- Hero source assets: none; native vector-like view layers only.
- Hero bitmap dimensions/formats: not applicable.
- Decorative loops: one transform family and one opacity/glow family; Status rings remain static in this pass.
- Offscreen/background/Reduce Motion/Low Power gates: code-path verified; runtime toggles require device QA.
- Home render time, first-content time, scroll frame data, memory, thermal behavior, and battery impact are not claimed without profiler instrumentation. A Release runtime visual check is evidence of rendering correctness, not a performance benchmark.

## Visual review

The iPhone 16 Pro simulator pass found and corrected excessive vertical density. The final capture keeps the approved header, hero, single Status empty state, complete collapsed Composer, and the feed filter row in the initial viewport. Approximate visual parity from inspected geometry is 86% overall: header 91%, hero composition 88%, local hero artwork 78%, Radio 90%, metrics 89%, system cards 87%, Status rail 86%, Composer 91%, feed filters 88%, and bottom navigation 92%. These are implementation estimates, not owner approval.

## Release judgment

The source is production-wired, fixture-free in the Release artifact, simulator inspected, and physically installed/launched. Performance-safe motion controls are implemented, but visual approval and internal-beta readiness remain gated on owner review, owner-controlled real-account action checks, authenticated compact/Pro Max Home evidence, VoiceOver/Dynamic Type runtime passes, and profiler measurements for render time, scroll, memory, thermal, and battery behavior.
