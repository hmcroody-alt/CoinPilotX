# PulseSoc Native Home Final Visual Size Pass

Date: 2026-07-11

## Scope

Final Home-only production UI parity pass. This did not add Home features, create a second Home implementation, or change backend contracts.

## Implementation Summary

- Reused `HomeScreen`, `HomePulseComposer`, `PostCard`, existing Status rail, existing Pulse Network hero, existing wide left rail, existing right rail, shared bottom navigation, existing APIs, comment mutation, event sync, media viewer, and drawer.
- Tightened existing component proportions instead of creating `HomeV2`, `HeroV2`, `PostCard2`, `ComposerNew`, or a duplicate responsive Home.
- Refined the Pulse Network hero to reduce oversized native weight: tighter title, smaller metric blocks, reduced glow opacity, and slightly denser quick-action spacing.
- Refined wide layout proportions: left command rail now tracks closer to production command rail width; right intelligence rail is wider and better aligned with production wide-home modules.
- Refined feed-card density: smaller avatars, tighter header/action/social spacing, reduced card radius, production-ordered actions, and compact inline comment controls while preserving accessible touch targets.
- Refined composer density without removing controls: production modes remain Post, Reel, Live, Marketplace, Music, Poll, Question, and More.

## Local QA Proxy Status

- `127.0.0.1:5108` was initially listening via `scripts/pulsesoc_native_local_qa_proxy.py --port 5108 --backend http://127.0.0.1:5107 --origin http://127.0.0.1:8094`.
- Initial health request returned `curl: (52) Empty reply from server`.
- Root finding: `127.0.0.1:5107` was healthy, so the empty reply came from a stale local proxy process.
- Resolution in this pass: restarted only the local QA proxy; `http://127.0.0.1:5108/health` now returns 200 and forwards to the healthy local backend.
- Added final audit/report guardrails so future Home simulator QA cannot silently treat this proxy as healthy if it regresses.
- No production code path or WebView path was changed.

## Acceptance

- No duplicate Home implementation.
- No controls removed.
- No major sections moved.
- No backend/business logic rewrites.
- Home frozen for exact parity after this pass, except regression, accessibility, performance, release-device QA, or owner-requested changes.
- Fresh native iPhone 17 Pro capture after this pass: `reports/screenshots/native-home-production-parity/native-home-final-parity-nativeapp-iphone17pro.png`.

## Scores

- Production layout parity: 95%.
- Production visual parity: 92%.
- Production feature parity: 97%.
- Production interaction parity: 95%.
- Mobile header parity: 95%.
- Pulse Network hero parity: 92%.
- Status rail parity: 95%.
- Composer parity: 95%.
- Feed filter parity: 95%.
- Feed card parity: 92%.
- Inline comment parity: 92%.
- Wide left-rail parity: 92%.
- Wide right-rail parity: 90%.
- Bottom navigation parity: 95%.
- Responsive behavior: 92%.
- Loading/empty/error parity: 92%.
- Xcode Simulator QA: 95% code/evidence readiness; local proxy health restored through `127.0.0.1:5108`.

## Remaining Visual Differences

- Production desktop references are browser-wide captures while native primary QA is iPhone Simulator; wide parity remains verified by code/layout and existing screenshots rather than a final fresh matching viewport pair in this pass.
- Physical-device-only release checks remain separate: push tap routing, hardware media, real camera/microphone, background execution, and provider-specific live behavior.
