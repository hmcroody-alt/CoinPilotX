/**
 * Display-only membership mark for a profile that is not the signed-in member.
 *
 * Why this exists at all, given `canonicalTier` is supposed to be the one
 * authority: the canonical endpoint answers for the *caller* and takes no user
 * parameter, which is exactly what stops it being used to read someone else's
 * tier. So when the app draws a badge on a visited profile it has only what the
 * profile payload carried — `premium_status`, a string the server chose.
 *
 * The rule that keeps this from becoming a second authority is structural, not
 * a comment: this module returns a `boolean` and nothing else. It has no tier,
 * no feature id, and no availability, so there is nothing here a gate could
 * consume even by accident. Anything deciding what a member may *do* calls
 * `canonicalTier`; this decides whether to draw a diamond next to a stranger's
 * name.
 *
 * The set below is the server's own — `premium_visibility_engine.py:32` and
 * `premium_entitlement_service.py:970` both use `{active, founder, lifetime,
 * trial}`. Copying it here is still a copy, but it is one copy of a display
 * rule, stated once, instead of the four different arrays this replaced, none
 * of which agreed with the server or with each other.
 */

/** Verbatim from the backend's visibility predicate. Keep them in step. */
const MARKED_STATUSES = new Set(["active", "founder", "lifetime", "trial"]);

/**
 * Should this profile render a membership mark?
 *
 * @param premiumStatus The `premium_status` string from a profile payload.
 *   Anything unrecognised, empty or absent is false — an unknown status is not
 *   evidence of membership.
 */
export function hasMembershipMark(premiumStatus?: string | null): boolean {
  return MARKED_STATUSES.has(String(premiumStatus || "").trim().toLowerCase());
}
