/**
 * The bulk category move, from what the seller picks to what the server is sent
 * — §33, §34.
 *
 * The sibling of `storeBulkPricing.ts`, and it owns less, because a category is
 * a name rather than a calculation. There is nothing to compute and nothing to
 * convert; what this module does is decide when a draft is a *request* and when
 * it is still half-typed, and offer the aisles the store already uses.
 *
 * **Categories are free text, not an enum.** There is no taxonomy on the server
 * — `normalize_category` trims, caps at 80 characters and accepts what is left —
 * so inventing a fixed list here would be inventing a product decision in the
 * client layer, and it would be the wrong one: a seller whose aisle is not on
 * the list could set it one product at a time in the single-listing editor and
 * not forty at a time here, which is §21's "two answers to one question" wearing
 * a picker.
 *
 * What the face offers instead is the categories **already in this seller's
 * store**, which is a suggestion and not a constraint. That keeps a store's
 * aisles spelled consistently — the real failure mode for free text is
 * "Home & Kitchen" beside "Home and Kitchen", which no filter can join — without
 * refusing a name nobody has used yet.
 */

import type { MarketplaceCategoryTarget } from "../api/marketplace";

/** Mirrors `CATEGORY_MAX` in `services/business_os/marketplace/listing_batch.py`. */
export const CATEGORY_MAX = 80;

/**
 * What the seller has chosen so far.
 *
 * Both are strings and neither is optional, because "no subcategory" is a
 * decision this face makes on the seller's behalf every time they pick a bare
 * category, and an optional field would let it look like an oversight. See
 * {@link parseCategoryTarget}.
 */
export type StoreCategoryDraft = {
  category: string;
  subcategory: string;
};

export const EMPTY_CATEGORY_DRAFT: StoreCategoryDraft = { category: "", subcategory: "" };

/** Collapse runs of whitespace and trim, the way the server does before storing. */
function tidy(value: string): string {
  return value.split(/\s+/).filter(Boolean).join(" ").slice(0, CATEGORY_MAX);
}

/**
 * The target to send, or the reason there isn't one.
 *
 * Normalised **here as well as** on the server, and not because the server needs
 * help. It is so `isSameAttempt` compares what the server will compare: a seller
 * who previews "Home & Kitchen", goes back, retypes it as "Home & Kitchen "
 * and applies is doing the same work, and an attempt keyed on the untrimmed text
 * would mint a second idempotency key for it. Two keys for one intention is how
 * a double tap becomes two batches.
 */
export function parseCategoryTarget(
  draft: StoreCategoryDraft
): { target: MarketplaceCategoryTarget; error: null } | { target: null; error: string } {
  const category = tidy(draft.category);
  if (!category) return { target: null, error: "Choose a category." };
  return { target: { category, subcategory: tidy(draft.subcategory) }, error: null };
}

/**
 * The aisles this store already uses, for the suggestion chips.
 *
 * Sorted by how many products are in each, descending, then alphabetically —
 * frequency first because the aisle a seller is most likely to be moving *into*
 * is usually one they already use heavily, and the alphabetical tie-break so the
 * chip order does not shuffle between renders of an equally-weighted store.
 *
 * Parents only. A subcategory chip would have to be filtered by the chosen
 * parent to mean anything, and an unfiltered one invites exactly the incoherent
 * pair — "Home & Kitchen / Crypto Basics" — that clearing the subcategory on a
 * move exists to prevent.
 */
export function categoriesInUse(rows: { category: string }[]): string[] {
  const counts = new Map<string, number>();
  rows.forEach((row) => {
    const name = tidy(row.category || "");
    if (!name) return;
    counts.set(name, (counts.get(name) || 0) + 1);
  });
  return Array.from(counts.entries())
    .sort((a, b) => (b[1] - a[1]) || a[0].localeCompare(b[0]))
    .map(([name]) => name);
}

/**
 * How many of the selected rows are already filed exactly there.
 *
 * Shown on the face before the seller previews, because it is the one thing
 * about a move they cannot guess and would otherwise learn as a screenful of
 * "Already in that category" blocks. It is a count from local rows and therefore
 * a *hint*, not a verdict — the server decides per row at write time, and the
 * preview is what the seller acts on. Named `alreadyThere` rather than
 * `blockedCount` for that reason.
 */
export function alreadyThere(
  rows: { category: string; subcategory: string }[],
  target: MarketplaceCategoryTarget
): number {
  return rows.filter(
    (row) =>
      tidy(row.category || "") === target.category &&
      tidy(row.subcategory || "") === target.subcategory
  ).length;
}
