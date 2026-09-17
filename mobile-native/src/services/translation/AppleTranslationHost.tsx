/**
 * The one place in the app where Apple's translation host is mounted.
 *
 * Apple only vends a `TranslationSession` through a live SwiftUI view, so
 * something has to be in the view hierarchy for on-device translation to work
 * at all. The native coordinator already multiplexes every language pair behind
 * a single mounted host, which is why one is enough — and why more than one
 * would be actively wrong: a second host is a second SwiftUI root asking for
 * sessions for the same pairs, which is the "one session per feed cell" failure
 * Stage 3 forbids, just spelled differently.
 *
 * Mounting is therefore deliberately not something a screen does. This
 * component takes no props and is rendered once, at the app root, beside the
 * other session-scoped layers. `__tests__/hostMount.test.ts` asserts the count.
 *
 * Renders nothing on Android, in Expo Go, and in any build made before the
 * native module existed: `AppleTranslationHostView` is null there, and the
 * router already reports `native_bridge_unavailable` and falls through to the
 * cloud, so there is nothing for this layer to decide.
 */

import { AppleTranslationHostView } from "pulse-apple-translation";

export function AppleTranslationHost() {
  if (!AppleTranslationHostView) return null;
  // No style prop: the native view sizes itself 1×1pt, transparent, with
  // hit-testing and accessibility off. Styling it from here would let a layout
  // change make it un-mountable, and an unmounted host is a silent fallback to
  // the billable provider rather than a visible bug.
  return <AppleTranslationHostView />;
}
