/**
 * One rule for reading a boolean out of the environment.
 *
 * ## The defect this closes
 *
 * There was never a flags module, so every surface that needed a gate wrote its
 * own parser inline, and by the time Tier 0.5 landed there were four of them.
 * The older ones accepted `1`, `true`, `on` and `yes`; the six flags Tier 0.5
 * added accepted the literal `1` and nothing else. `EXPO_PUBLIC_ORDERS_ESCROW=true`
 * turned escrow on. `EXPO_PUBLIC_STORE_READINESS=true` did nothing at all, and
 * said nothing about why — the operator set a flag, watched the build come up
 * unchanged, and had no way to tell a stricter parser from a broken feature.
 * A fifth variant lowercased without trimming, so it took `true` and rejected
 * `" 1"`.
 *
 * Disagreeing about what "on" means is not a style problem. It is the single
 * most likely source of a "we turned it on and nothing happened" report, and it
 * is invisible at the place where somebody actually sets the variable — a CI
 * definition or a build profile, both of which are far away from the module
 * that decides how to read it.
 *
 * So: one reader, one accepted set, imported everywhere. A flag added later
 * gets the shared behaviour by construction rather than by whoever writes it
 * remembering which of the four rules was the good one.
 *
 * ## The rule
 *
 * Trim the surrounding whitespace, lowercase, and compare against
 * {@link TRUTHY_FLAG_VALUES}. Everything else — including `0`, `false`, `off`,
 * an empty string, and an unset variable — is false. Unset resolving to false
 * is what keeps every gate off in a default build, which is the posture the
 * flag registry records and which this change does not alter: it widens what
 * counts as "on", never what happens when nobody says anything.
 *
 * Whitespace is trimmed because a value that arrives from a `.env` line, a
 * shell export or a CI variable panel frequently carries a trailing space that
 * nobody typed on purpose and nobody can see.
 *
 * ## What this is not for
 *
 * Only booleans. A URL, a key, a project id or an environment name is read by
 * the module that owns it, because each one has its own validation and its own
 * fallback chain — see `api/config.ts`. Passing one of those through here would
 * answer "is it truthy", which is never the question being asked of them.
 */

/**
 * Every spelling of "on" this app accepts, in the one place a reader can check.
 *
 * Exported so a test can pin the set rather than restate it. Widening it is a
 * deliberate edit to this line, which is the property the four inline parsers
 * did not have.
 */
export const TRUTHY_FLAG_VALUES = ["1", "true", "on", "yes"] as const;

/**
 * Every spelling of "off", for the default-on reader below.
 *
 * Deliberately the mirror of {@link TRUTHY_FLAG_VALUES} rather than "anything
 * not truthy": a default-on flag must only be disabled by somebody who meant
 * to disable it, so a typo (`EXPO_PUBLIC_SPATIAL_REELS=flase`) leaves the
 * feature on rather than silently killing it.
 */
export const FALSY_FLAG_VALUES = ["0", "false", "off", "no"] as const;

/**
 * Whether a raw value means "on".
 *
 * Takes the value rather than the name so a caller holding a string from
 * somewhere other than `process.env` gets the identical rule.
 */
export function isFlagValueOn(value: string | undefined | null): boolean {
  const raw = String(value ?? "").trim().toLowerCase();
  return (TRUTHY_FLAG_VALUES as readonly string[]).includes(raw);
}

/**
 * Whether a raw value means "on", for a flag whose default is ON.
 *
 * ## Why a second reader exists
 *
 * {@link isFlagValueOn} answers "did somebody switch this on", and unset means
 * off. That is right for a feature being rolled out, and wrong for one that has
 * already shipped and is expected to stay on, because it makes *staying on* the
 * thing that requires effort. Every build path has to remember to set the
 * variable; the first one that forgets ships the feature switched off, and the
 * build still succeeds, the tests still pass, and nothing anywhere says why the
 * behaviour vanished.
 *
 * That is not hypothetical here. The Reels horizontal pager and its full-screen
 * navigator were reported as repeatedly reverting. They had not been reverted —
 * the code was present and correct in every build that shipped without them.
 * What differed was whether `EXPO_PUBLIC_SPATIAL_CONSOLE` and friends happened
 * to be exported by whoever ran the build. No EAS profile set them, no `.env`
 * existed, and the repo's `.gitignore` excludes `.env` and `.env.*`, so there
 * was no committed place for them to live: the feature was on only for a build
 * whose operator typed the variables by hand, and off for every other build.
 * A shipped feature cannot depend on that.
 *
 * So a flag that has finished rolling out moves to this reader and its default
 * inverts. Rollback stays a flag flip and never a revert — set the variable to
 * any of {@link FALSY_FLAG_VALUES} — but the flip is now required to turn the
 * feature *off*, which is the direction that should cost somebody an action.
 *
 * ## The rule
 *
 * Trim, lowercase, and compare against {@link FALSY_FLAG_VALUES}: a match is
 * off. Unset, empty, and every other value — including an unrecognised one — is
 * on. See {@link FALSY_FLAG_VALUES} for why a typo resolves to on.
 */
export function isFlagValueOnUnlessDisabled(value: string | undefined | null): boolean {
  const raw = String(value ?? "").trim().toLowerCase();
  return !(FALSY_FLAG_VALUES as readonly string[]).includes(raw);
}

/**
 * Whether the named environment variable is on.
 *
 * Read at call time, never cached at module load, so a test can set the
 * variable and call the accessor without re-importing the module graph. Every
 * Business OS accessor is written this way on purpose; the two app-level
 * constants in `api/config.ts` predate the convention and are still evaluated
 * once at import, which is why a test cannot toggle those two.
 */
export function envFlagOn(name: string): boolean {
  return isFlagValueOn(process.env[name]);
}
