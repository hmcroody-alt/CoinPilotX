/**
 * Shoppable product Signals in the social feed — one gate, one place.
 *
 * Read through {@link isFlagValueOnUnlessDisabled}: unset means *on*, and
 * turning it off costs somebody a deliberate `=0`. That direction is chosen for
 * the reason `discovery/flags.ts` documents at length — five finished discovery
 * modules shipped default-OFF behind variables that have no committed home in
 * the repo, so the feature was live for exactly the one build whose operator
 * typed the exports and dark for every build made afterwards, and was duly
 * reported as having come undone. A feature with a real source should not depend
 * on a shell variable nobody sets. Rollback stays a flag flip; the flip just
 * runs in the direction that costs an action.
 *
 * The read is a STATIC `process.env.EXPO_PUBLIC_*` member expression, which is
 * load-bearing rather than stylistic: `babel-preset-expo` inlines that form at
 * bundle time only when the key is a literal, and a release bundle has no
 * populated `process.env` at runtime. A computed lookup reads `undefined` on
 * device while passing every jest assertion.
 */
import { isFlagValueOnUnlessDisabled } from "../core/envFlag";

let override: boolean | undefined;

/**
 * Test-only. Production code never calls this; jest suites use it to exercise
 * both sides of the gate without mutating `process.env`.
 */
export function __setProductSignalsFlagOverride(value: boolean | undefined) {
  override = value;
}

/** Off = the feed fetches no listings and renders precisely the rows it does today. */
export function feedProductSignalsEnabled(): boolean {
  if (override !== undefined) return override;
  return isFlagValueOnUnlessDisabled(process.env.EXPO_PUBLIC_FEED_PRODUCT_SIGNALS);
}
