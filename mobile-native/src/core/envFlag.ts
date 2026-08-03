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
