/**
 * Selection mode for the seller's listing list — §16–§20, and the honest
 * partial-success preview §34 is built on.
 *
 * This is a pure state model over listing ids. It owns no React state, renders
 * nothing, and calls nothing. The reason is that every genuinely dangerous
 * question about bulk editing is a question about *which rows*, and those are
 * far easier to get right — and to prove right — as functions over arrays than
 * as effects tangled into a screen.
 *
 * The four questions, and the answers this module commits to:
 *
 * 1. **What does Select All select?** The rows the seller can currently see:
 *    the active tab, filtered by the active search. Not the whole catalogue.
 *    A seller who filters to "Out of stock", taps Select All and then taps a
 *    destructive action means *those*. Selecting 200 listings because 6 were on
 *    screen is the single worst thing this feature could do, and "All" is
 *    exactly the word that would make someone expect it — hence
 *    {@link selectAllLabel}, which says how many and of what.
 *
 * 2. **What happens to a selection when the filter changes?** It survives.
 *    Gathering four listings from one tab and two from another is a real
 *    workflow, and silently dropping half a selection because the seller looked
 *    at a different tab would be worse than keeping it. The count is always
 *    shown, so nothing is selected invisibly — see {@link selectionSummary},
 *    which names the off-screen part rather than letting it lurk.
 *
 * 3. **What happens when the list reloads?** {@link reconcile}. A listing
 *    deleted on another device, or filtered out of the payload entirely, must
 *    leave the selection — otherwise a bulk action posts an id that no longer
 *    exists and the failure is reported against a row the seller cannot see.
 *    The screen must call this on every load, and there is a test that a
 *    vanished id does not survive.
 *
 * 4. **Which of the selected rows can actually take the action?**
 *    {@link partition}. This is §34's preview: "14 will publish, 4 are blocked"
 *    is shown *before* the seller commits, not discovered afterwards.
 *
 * The rule inherited from the readiness work applies here too, and is the one
 * thing in this file most likely to be "simplified" later:
 *
 *   **A row with no verdict is not eligible.** Not eligible-by-default, not
 *   optimistically eligible. A missing `bulkEligibility` means the payload
 *   never said, and a bulk publish that treats "never said" as "fine" is how
 *   you publish a listing with no price. Absence is not a clean bill of health.
 */

import type { StoreListingRow } from "../api/storeDashboard";

/** Selected listing ids. A set, because membership is the only query. */
export type StoreSelection = ReadonlySet<number>;

export const EMPTY_SELECTION: StoreSelection = new Set<number>();

/** Add or remove one row. */
export function toggle(selection: StoreSelection, id: number): StoreSelection {
  const next = new Set(selection);
  if (!next.delete(id)) next.add(id);
  return next;
}

/**
 * Add every visible row to the selection; if they are all already in it, remove
 * them.
 *
 * The toggle is scoped to `visible` rather than clearing outright, so a seller
 * who selected rows on another tab does not lose them by tapping Select All
 * here. Deselecting what you just selected is the expected second tap; wiping a
 * selection you gathered elsewhere is not.
 */
export function toggleAll(selection: StoreSelection, visible: StoreListingRow[]): StoreSelection {
  const next = new Set(selection);
  if (allSelected(selection, visible)) {
    visible.forEach((row) => next.delete(row.id));
    return next;
  }
  visible.forEach((row) => next.add(row.id));
  return next;
}

/**
 * `visible.length > 0` guards the vacuous truth of `[].every(...)`.
 *
 * No test covers it, and that is deliberate rather than an omission: removing
 * it changes nothing observable through this module's exports. `selectAllState`
 * early-returns on an empty list before reaching here, and `toggleAll`'s two
 * branches are both no-ops over an empty array — measured, not assumed. It
 * stays because this is the one place "all" is decided, and a future caller
 * that does not guard first would otherwise be told an empty list is fully
 * selected.
 */
function allSelected(selection: StoreSelection, visible: StoreListingRow[]): boolean {
  return visible.length > 0 && visible.every((row) => selection.has(row.id));
}

/**
 * Tri-state for the Select All control.
 *
 * "some" exists so the control can render a dash rather than a tick. A checkbox
 * that shows *checked* when only four of twelve are selected is a lie told in
 * one glyph, and it is the state a seller is most likely to act on without
 * re-reading the count.
 */
export type SelectAllState = "none" | "some" | "all";

export function selectAllState(selection: StoreSelection, visible: StoreListingRow[]): SelectAllState {
  if (visible.length === 0 || selection.size === 0) return "none";
  if (allSelected(selection, visible)) return "all";
  return visible.some((row) => selection.has(row.id)) ? "some" : "none";
}

/**
 * "Select all 6 shown" / "Deselect all 6 shown".
 *
 * The word "shown" is load-bearing and is why this returns a sentence instead
 * of the caller writing "Select all". It is the difference between a seller
 * believing they have selected their catalogue and knowing they have selected
 * what is on screen.
 */
export function selectAllLabel(selection: StoreSelection, visible: StoreListingRow[]): string {
  const verb = selectAllState(selection, visible) === "all" ? "Deselect" : "Select";
  return `${verb} all ${visible.length} shown`;
}

/**
 * Drop ids that are no longer in the list.
 *
 * Called on every reload. A selection is a set of promises about rows, and a
 * row that has gone is a promise that cannot be kept.
 */
export function reconcile(selection: StoreSelection, rows: StoreListingRow[]): StoreSelection {
  if (selection.size === 0) return selection;
  const live = new Set(rows.map((row) => row.id));
  const next = new Set<number>();
  selection.forEach((id) => {
    if (live.has(id)) next.add(id);
  });
  // Returning the same reference when nothing changed keeps `useMemo` and
  // `===` comparisons downstream from re-running on every refresh.
  return next.size === selection.size ? selection : next;
}

/** The selected rows, in the list's own order rather than selection order. */
export function selectedRows(selection: StoreSelection, rows: StoreListingRow[]): StoreListingRow[] {
  return rows.filter((row) => selection.has(row.id));
}

/**
 * "4 selected" — and, when some of them are not on screen, where they are.
 *
 * Returns `null` for an empty selection so the caller renders nothing rather
 * than "0 selected".
 */
export function selectionSummary(
  selection: StoreSelection,
  rows: StoreListingRow[],
  visible: StoreListingRow[]
): string | null {
  const total = selectedRows(selection, rows).length;
  if (total === 0) return null;
  const onScreen = visible.filter((row) => selection.has(row.id)).length;
  const hidden = total - onScreen;
  // Naming the hidden part is the whole point: it is what makes decision 2
  // above safe. A seller about to bulk-delete 6 listings while looking at 2 of
  // them should be told so here, not by the result.
  return hidden > 0 ? `${total} selected · ${hidden} not shown` : `${total} selected`;
}

/* ------------------------------------------------------------------ *
 * Eligibility — §34's preview
 * ------------------------------------------------------------------ */

/**
 * The actions whose verdict follows from the listing row alone.
 *
 * These are the ones the list payload can carry an answer for, because nothing
 * about "can this be published" depends on anything the seller has not typed
 * yet. `bulk_eligibility` on every row holds those answers, and
 * {@link partition} reads them.
 *
 * The name mirrors `PRECOMPUTED_ACTIONS` in
 * `services/business_os/marketplace/listing_batch.py`, and the two must stay in
 * step: the server only attaches verdicts for the actions in its tuple, so an
 * action listed here but not there gets "No readiness check yet" on every row
 * in the store.
 */
export type StorePrecomputedBulkAction = "publish" | "hide";

/**
 * Every action the docked bar offers, including the one no row can be
 * pre-judged for.
 *
 * `price` and `category` are the odd ones and the reason this type is split in
 * two. What blocks either depends on a choice the seller has not made yet — the
 * same listing is `PRICE_UNCHANGED` under cost+20% and a clean success under
 * cost+25%, and `CATEGORY_UNCHANGED` for the aisle it is already in and a clean
 * success for any other — so there is no verdict to attach to a row in advance
 * and no honest partition to compute here. The server answers both with a dry
 * run (`previewMarketplaceSellerBatch`), and {@link partition} refuses them at
 * the type level so nobody re-adds a local guess.
 */
export type StoreBulkAction = StorePrecomputedBulkAction | "price" | "category";

/**
 * The actions whose meaning is incomplete without a payload.
 *
 * Mirrors `PAYLOAD_ACTIONS` in `listing_batch.py`, where `PRECOMPUTED_ACTIONS`
 * is likewise derived by subtraction rather than listed twice. Spelled as a set
 * instead of `action !== "price"` because a two-valued inequality is the same
 * silent bug the moment there are three: `category` would have been treated as
 * precomputable and every row in the store would have read
 * "No category worked out" before the seller chose one.
 */
const PAYLOAD_ACTIONS: readonly StoreBulkAction[] = ["price", "category"];

/** Narrow to the actions {@link partition} can answer, at runtime. */
export function isPrecomputed(action: StoreBulkAction): action is StorePrecomputedBulkAction {
  return !PAYLOAD_ACTIONS.includes(action);
}

export type StoreBulkPartition = {
  /** Rows the action can be applied to. */
  eligible: StoreListingRow[];
  /** Rows it cannot, each with the reason a seller can act on. */
  blocked: { row: StoreListingRow; reason: string }[];
};

/**
 * How each action is spoken about, in one table.
 *
 * Every one of these used to be an inline `action === "publish" ? … : …`, which
 * is a correct way to write a two-valued switch and a silent bug the moment
 * there are three: `price` would have rendered as "Hide", on the button, in the
 * sheet title, in the result headline, and in the screen-reader label — each
 * one a separate ternary that nothing would have flagged. A `Record` keyed by
 * the union makes adding a fourth action a compile error in one place instead
 * of a mislabelled button in five.
 *
 * `done` is what the row says after it happened, and is not simply the past
 * tense: publishing a listing submits it for review rather than putting it in
 * front of buyers, and saying "Published" there is the claim §31 exists to stop.
 */
export const BULK_VERB: Record<
  StoreBulkAction,
  { imperative: string; plain: string; past: string; done: string }
> = {
  publish: {
    imperative: "Publish",
    plain: "publish",
    past: "published",
    done: "Submitted for review"
  },
  hide: { imperative: "Hide", plain: "hide", past: "hidden", done: "Hidden from buyers" },
  price: { imperative: "Reprice", plain: "reprice", past: "repriced", done: "Price updated" },
  // "Move" rather than "Categorise": the seller is relocating products between
  // aisles they already understand, and `done` names the new filing's effect
  // rather than the write, for the same reason `publish`'s does.
  category: { imperative: "Move", plain: "move", past: "moved", done: "Category updated" }
};

/**
 * Split a selection into what will happen and what will not, before anything
 * is sent.
 *
 * Eligibility is **read, never derived**. The server attaches
 * `bulk_eligibility` to every row of the seller's own listings, computed by
 * `listing_batch.block_reason` — the same function the batch endpoint runs when
 * the seller commits. So the preview and the outcome are not two answers that
 * happen to agree; they are one answer, asked twice.
 */
export function partition(
  rows: StoreListingRow[],
  action: StorePrecomputedBulkAction
): StoreBulkPartition {
  const eligible: StoreListingRow[] = [];
  const blocked: { row: StoreListingRow; reason: string }[] = [];

  rows.forEach((row) => {
    const reason = blockReason(row, action);
    if (reason) blocked.push({ row, reason });
    else eligible.push(row);
  });

  return { eligible, blocked };
}

/**
 * The one thing this file will not do is answer the question itself.
 *
 * There used to be a small TypeScript re-implementation here: no verdict blocks,
 * `!publishable` blocks with a counted string, hide blocks when already hidden.
 * Every clause of it was defensible and the whole was still wrong, because it
 * could not know the rule that has nothing to do with readiness — an
 * already-live listing is perfectly `publishable` and must never be published
 * again. Select-all plus Publish would have shown "Publish 18", sent 18, and
 * knocked fourteen live products back into the review queue.
 *
 * The fix was not to add the missing clause. It was to stop having clauses.
 */
function blockReason(row: StoreListingRow, action: StorePrecomputedBulkAction): string | null {
  const verdict = row.bulkEligibility?.[action];
  // `undefined` is the payload not carrying an answer for this action; `null` is
  // the server saying it would apply. Only the second is a yes. An older cached
  // snapshot has neither the field nor any news, and news is what eligibility
  // requires.
  if (verdict === undefined) return "No readiness check yet";
  return verdict === null ? null : verdict.reason;
}

/**
 * "Publish 14 · 4 blocked" — the sentence on the confirm button.
 *
 * Deliberately states the blocked count even though the action will not touch
 * those rows, because §34 is about the seller knowing the shape of the outcome
 * in advance. A button reading "Publish 18" that publishes 14 is the failure
 * this replaces.
 */
export function bulkActionLabel(
  partitioned: StoreBulkPartition,
  action: StorePrecomputedBulkAction
): string {
  const verb = BULK_VERB[action].imperative;
  const { eligible, blocked } = partitioned;
  if (eligible.length === 0) return `Nothing to ${BULK_VERB[action].plain}`;
  return blocked.length > 0
    ? `${verb} ${eligible.length} · ${blocked.length} blocked`
    : `${verb} ${eligible.length}`;
}
