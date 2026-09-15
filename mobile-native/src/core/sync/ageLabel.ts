/**
 * The English rendering of a cache age, kept apart from the derivation.
 *
 * `freshness.ts` deliberately returns `{ unit, value }` rather than a sentence,
 * because word order and pluralisation move between locales. This file is where
 * that becomes English — and having exactly one of it matters more than it
 * looks: the three surfaces that show a cached banner had begun growing three
 * near-identical formatters, which is how "12m ago" and "12 min ago" end up on
 * adjacent screens.
 *
 * When these surfaces are put through the i18n gate, this is the single file
 * that has to move, and the call sites do not change shape.
 */

import { describeAge } from "./freshness";

/**
 * "· 12m ago", or an empty string when the age is unknown.
 *
 * Empty rather than a placeholder is the whole point. An entry written before
 * cache entries carried timestamps has no age, and "just now" over content that
 * may be weeks old is the app asserting a freshness it never observed.
 */
export function cachedAgeSuffix(ageMs: number | null): string {
  const age = describeAge(ageMs);
  if (!age) return "";
  if (age.unit === "now") return " · just now";
  const unit = age.unit === "minutes" ? "m" : age.unit === "hours" ? "h" : "d";
  return ` · ${age.value}${unit} ago`;
}

/** `base` with the age appended when it is known. No trailing punctuation. */
export function withCachedAge(base: string, ageMs: number | null): string {
  return `${base}${cachedAgeSuffix(ageMs)}`;
}
