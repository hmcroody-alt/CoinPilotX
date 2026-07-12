# PulseSoc Native Migration Mission Standard

Effective: 2026-07-12

This standard is mandatory for every PulseSoc native migration mission. The current production WebView application remains live and is the authoritative UI, feature, workflow, backend, and business-logic source. The React Native app is a parallel implementation; native work must not redesign, relocate, simplify, or silently replace production behavior.

## Required sequence

1. Inspect the current production WebView screen and map its layout, hierarchy, states, interactions, navigation, and production contracts before implementation.
2. Inspect the existing native implementation and production code. Reuse API wrappers, authentication, authorization, DTOs, validation, business and domain logic, realtime/socket behavior, permissions, media pipelines, notifications, moderation, analytics, design tokens, assets, and existing native components wherever technically appropriate.
3. Implement native platform layers and fully wire every control, navigation path, API, event, loading path, empty path, permission path, error path, retry path, offline path, and reconnect path. Placeholders, fake success, dead controls, and unwired UI are not acceptable.
4. Use the Xcode iPhone Simulator throughout implementation. Inspect meaningful intermediate states, compare them directly with production, correct visible gaps, and repeat; simulator review is not an end-only gate.
5. Run static checks, typecheck, Expo Doctor, applicable repository audits, and any feature-specific tests. These support simulator QA and never replace it.
6. Complete the evidence matrix, classify limitations honestly, update the Native Progress Report, then inspect production and native code to recommend the next feature supported by repository evidence.

## Primary QA visibility: Xcode iPhone Simulator

The Xcode iPhone Simulator is the primary visual truth-checking environment. Every reproducible major native state must be opened and visually inspected there. Check layout, spacing, typography, colors, icons, component hierarchy, responsive behavior, navigation, modals, sheets, loading, empty, error, offline, reconnect, permission denial, keyboard behavior, safe areas, simulator-supported gestures, animations, transitions, and long content.

Do not claim visual parity from code inspection, typecheck, unit tests, automated audits, assumptions, or previous reports. For every major screen:

1. Open and inspect the current production WebView equivalent.
2. Open the equivalent native screen in the Xcode iPhone Simulator.
3. Compare them side by side.
4. Correct visible differences.
5. Capture final simulator evidence.
6. Record remaining differences honestly.

## Required iPhone coverage

At the start of each mission, run `xcrun simctl list devices available` and use the exact available, repository-supported devices. The current local representative matrix is:

| Layout class | Current simulator |
| --- | --- |
| Compact | iPhone 17e |
| Standard | iPhone 17 |
| Pro | iPhone 17 Pro |
| Pro Max | iPhone 17 Pro Max |

If this inventory changes, record the replacement device and rationale in the mission report. A single simulator size is insufficient.

## Required screenshot evidence

Create a dedicated directory at `reports/screenshots/<mission-slug>/`. Capture, when the feature can reproduce them:

- default, populated, empty, loading, error, offline, reconnecting, and permission-denied states;
- modal, sheet, keyboard-open, long-content, small-screen, and large-screen states;
- every feature-specific interaction state needed to prove behavior and parity.

Use filenames that identify the device and state. The mission report must list every exact repository-relative screenshot path. If a required state is not applicable or cannot be reproduced, state why; do not silently omit it.

## Verification classifications

Every mission check must use exactly one classification:

- **Simulator verified**: directly reproduced and inspected in the Xcode iPhone Simulator.
- **Code-path verified**: implementation and wiring inspected or tested, but the behavior was not visually reproduced.
- **Mock-state verified**: a controlled fixture or mock was used and is identified.
- **Physical-device-only**: simulator evidence cannot reliably prove the behavior.

Real camera and microphone behavior, Bluetooth and speaker routing, lock-screen push behavior, background or app-killed calls, cellular transitions, real push delivery, large real-world uploads, and hardware permission edge cases normally remain physical-device-only. Keep them in the release checklist until a physical device verifies them.

## Completion gate

A native page is not complete unless its production equivalent was inspected, it was opened in the simulator, major reproducible states were visually inspected, visible parity gaps were corrected, screenshot evidence was captured, remaining differences and physical-device-only checks were documented, and the simulator QA percentage was updated honestly.

Before finishing, inspect `git diff`, run `git diff --check`, stage only intended files, commit accurately, push to origin, report the commit hash, and confirm working-tree status. A dirty shared worktree requires strict scoped staging; unrelated user changes must remain untouched.
