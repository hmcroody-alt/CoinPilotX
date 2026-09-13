/**
 * One bulk attempt, from the seller confirming it to the sentence they read
 * afterwards — §19, §22, §23, §31.
 *
 * `storeSelection` answers *which rows*. This answers *what happens to them*,
 * and it is separate for the same reason: the two dangerous questions here are
 * both answerable without rendering anything.
 *
 * **1. Does pressing Apply twice publish twice?** (§23) The idempotency key is
 * minted once per *attempt* — one (action, id-set) pair — by {@link beginAttempt},
 * and every send of that attempt carries it. A key minted inside the send would
 * defeat the entire mechanism: the second tap would mint a second key, the
 * server would see a different request, and the seller would get two batches.
 * The key is therefore a property of the attempt, and {@link isSameAttempt}
 * exists so a caller can tell "the seller pressed again" from "the seller
 * changed their mind and this is new work".
 *
 * **2. Does the result say what actually happened?** (§19) The sentence is built
 * from the server's `results`, one entry per requested listing, and never from
 * the request. Fourteen of eighteen publishing is the *expected* shape of a bulk
 * publish, not an error, and the two failures it must not produce are "18
 * published" and "publish failed".
 *
 * One thing this module deliberately does NOT do is filter the selection down to
 * the eligible rows before sending. See {@link idsToSend}.
 */

import type {
  MarketplaceBatchPreview,
  MarketplaceBatchResponse,
  MarketplaceBatchPreviewResult,
  MarketplaceBatchResult,
  MarketplaceCategoryTarget,
  MarketplacePricingRule
} from "../api/marketplace";
import { BULK_VERB, type StoreBulkAction, type StoreBulkPartition } from "./storeSelection";

/**
 * A confirmed intention: this action, on these rows, under this key.
 *
 * Frozen at confirm time. The seller's selection can go on changing behind the
 * sheet — a reload can even reconcile rows out of it — and the attempt still
 * describes what they agreed to.
 */
export type StoreBulkAttempt = {
  action: StoreBulkAction;
  /** Sorted and deduplicated, matching what the server hashes the request by. */
  ids: number[];
  /**
   * What the action is being asked to write, for the actions that need telling;
   * `null` for the ones whose whole meaning is their name.
   *
   * Part of the attempt's identity, not a detail hanging off it. A key that
   * covers only (action, ids) is a key that cannot tell "cost + 20%" from
   * "cost + 25%" on the same fourteen products — and the second one would be
   * answered with a *replay of the first*, because a spent key means the server
   * returns its stored result without looking at the new payload. The seller
   * would watch a confirmation for prices that were never applied.
   *
   * One field for both payload actions rather than a nullable `rule` beside a
   * nullable `categoryTarget`, mirroring the server, where `evaluate_rows` takes
   * one `plans` argument for the same reason. Two channels for one idea means
   * two things to remember in {@link isSameAttempt}, and the thing forgotten
   * there is a replayed batch reporting work that never happened.
   */
  payload: StoreBulkPayload | null;
  idempotencyKey: string;
};

/**
 * The settings a payload action carries.
 *
 * Discriminated, so a `category` attempt cannot be handed a pricing rule and a
 * `price` attempt cannot be handed an aisle. The alternative — one loose object
 * — compiles for the mismatch and fails at the server as `INVALID_CATEGORY`,
 * which reads to the seller as "that aisle is not valid" when what happened is
 * that the phone sent the wrong thing.
 */
export type StoreBulkPayload =
  | { kind: "price"; rule: MarketplacePricingRule }
  | { kind: "category"; target: MarketplaceCategoryTarget };

function normalizeIds(ids: number[]): number[] {
  return Array.from(new Set(ids.filter((id) => Number.isFinite(id) && id > 0))).sort((a, b) => a - b);
}

/**
 * A payload as one comparable string. `null` and "no payload" are the same thing.
 *
 * The `kind` is part of the string so that no two payload types can ever
 * collide, however they are later spelled. It is belt and braces today and no
 * test holds it down — removing both prefixes leaves the suite green, because
 * `isSameAttempt` compares the action first and the two encodings could not
 * collide anyway (`MULTIPLIER:2` against `["Toys","Games"]`). It stays because
 * the thing keeping them apart is otherwise an accident of how each branch
 * happens to be spelled, and the next payload kind added here inherits that
 * accident rather than the guarantee. The subcategory is included because
 * moving within a parent is a real change — `Education / Trading` is not
 * `Education / Crypto Basics` — and a key that ignored it would replay the first
 * move's summary for the second.
 *
 * The pair is `JSON.stringify`d rather than joined by a separator character
 * because categories are free text: any printable delimiter is one a seller may
 * legitimately type, and `["A|B", ""]` colliding with `["A", "B"]` means a second
 * tap is mistaken for the same attempt and silently dropped. An unprintable
 * separator also works and is what this first used — with the byte written
 * literally into the source, which made the file binary to `grep` and left the
 * collision one editor-save away.
 */
function payloadKey(payload: StoreBulkPayload | null | undefined): string {
  if (!payload) return "-";
  if (payload.kind === "price") return `price:${payload.rule.type}:${payload.rule.value}`;
  return `category:${JSON.stringify([payload.target.category, payload.target.subcategory])}`;
}

/** Convenience for the common case, so callers need not spell the wrapper. */
export function pricePayload(rule: MarketplacePricingRule): StoreBulkPayload {
  return { kind: "price", rule };
}

export function categoryPayload(target: MarketplaceCategoryTarget): StoreBulkPayload {
  return { kind: "category", target };
}

/**
 * Mint an attempt. Called once, when the seller confirms — not when the request
 * is sent, and not on retry.
 */
export function beginAttempt(
  action: StoreBulkAction,
  ids: number[],
  payload: StoreBulkPayload | null = null
): StoreBulkAttempt {
  return {
    action,
    ids: normalizeIds(ids),
    payload,
    idempotencyKey: `bulk-${action}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
  };
}

/**
 * Is this the same work the attempt already covers?
 *
 * Order-insensitive, because the id list is a set and the server hashes it
 * sorted. A caller that reuses a key for a genuinely different request gets a
 * 409 rather than a wrong answer — the server checks too — but finding out here
 * means the seller gets a new batch instead of an error.
 */
export function isSameAttempt(
  attempt: StoreBulkAttempt | null,
  action: StoreBulkAction,
  ids: number[],
  payload: StoreBulkPayload | null = null
): boolean {
  if (!attempt || attempt.action !== action) return false;
  // A changed payload is different work, even over the identical id set — see
  // the note on `StoreBulkAttempt.payload`. Compared before the ids because it
  // is the cheaper check and the one more likely to differ between two taps.
  if (payloadKey(attempt.payload) !== payloadKey(payload)) return false;
  const wanted = normalizeIds(ids);
  return (
    wanted.length === attempt.ids.length && wanted.every((id, index) => id === attempt.ids[index])
  );
}

/**
 * Every selected id, including the ones the preview says are blocked.
 *
 * Sending only the eligible subset looks tidier and is wrong twice over. The
 * preview is a snapshot: a row the seller fixed on another device in the last
 * minute would be silently dropped from work they asked for, and a row that
 * became unready since the payload arrived would be sent anyway because the
 * stale preview still says yes. Either way the phone would be deciding, which
 * is the thing this whole change removes.
 *
 * Sending all of them costs nothing — the server re-checks each row with
 * `block_reason` before touching it — and buys the seller a per-row answer from
 * the authority at the moment of the write, which is the only moment that
 * counts.
 */
export function idsToSend(attempt: StoreBulkAttempt): number[] {
  return attempt.ids;
}

/* ------------------------------------------------------------------ *
 * Reading the answer
 * ------------------------------------------------------------------ */

export type StoreBulkOutcome = {
  succeeded: MarketplaceBatchResult[];
  /** The server looked and the row is not ready. A task, not an error. */
  blocked: MarketplaceBatchResult[];
  /** Could not be attempted at all — vanished, or not this seller's. */
  failed: MarketplaceBatchResult[];
  /** "14 products published" / "Nothing published". */
  headline: string;
  /** "4 products need attention", or `null` when everything landed. */
  attention: string | null;
  /** True when this exact request had already run and the server replayed it. */
  replayed: boolean;
};

function plural(count: number): string {
  return count === 1 ? "product" : "products";
}

/**
 * What actually happened, in the seller's words.
 *
 * The counts come from `results` rather than from the response's own
 * `successful_count`. Those should agree — the server derives one from the
 * other — and when they do not, the list of rows underneath the headline is the
 * thing the seller can check, so the headline has to match it. A response
 * claiming eighteen successes above a list showing fourteen is the §19 failure
 * wearing a summary.
 */
export function outcomeOf(
  action: StoreBulkAction,
  response: MarketplaceBatchResponse
): StoreBulkOutcome {
  const results = Array.isArray(response?.results) ? response.results : [];
  const succeeded = results.filter((entry) => entry.outcome === "succeeded");
  const blocked = results.filter((entry) => entry.outcome === "blocked");
  const failed = results.filter((entry) => entry.outcome === "failed");

  const verb = BULK_VERB[action].past;
  const headline = succeeded.length
    ? `${succeeded.length} ${plural(succeeded.length)} ${verb}`
    : `Nothing ${verb}`;

  const needing = blocked.length + failed.length;
  const needs = needing === 1 ? "needs" : "need";
  return {
    succeeded,
    blocked,
    failed,
    headline,
    attention: needing ? `${needing} ${plural(needing)} ${needs} attention` : null,
    replayed: Boolean(response?.replayed)
  };
}

/**
 * The reason to show beside one row of the result.
 *
 * `reason` is server-written prose. The fallbacks are last resorts and say so
 * rather than inventing a cause: a result entry with no reason is a gap in the
 * payload, and "Something went wrong" is at least true about it, where "Not
 * ready to publish" would be a diagnosis this build did not make.
 */
export function outcomeReason(entry: MarketplaceBatchResult | MarketplaceBatchPreviewResult): string {
  if (entry.reason) return entry.reason;
  return entry.outcome === "blocked" ? "Not ready yet" : "Could not be updated";
}

/* ------------------------------------------------------------------ *
 * The review face — §34
 * ------------------------------------------------------------------ */

export type StoreBulkReviewLine = {
  id: number;
  title: string;
  /** "$49.00 → $12.00", or the reason this row is staying as it is. */
  detail: string | null;
  /**
   * A consequence of going ahead — not a reason not to.
   *
   * Kept separate from `detail` because the two are opposite kinds of sentence
   * and must not share a slot. "Will go back to review" belongs on a row that
   * *is* changing, and rendering it in the same place as a block reason would
   * put a warning-coloured line under a row nothing is wrong with.
   */
  warning: string | null;
};

/**
 * Everything the seller reads before they commit, in two lists.
 *
 * Deliberately not `StoreBulkPartition`: that one holds `StoreListingRow`s, and
 * the whole point of a price review is the pair of numbers, which no listing row
 * carries. This is the shape both sources of a review narrow to, so the sheet
 * has one confirm face rather than one per action.
 */
export type StoreBulkReview = {
  action: StoreBulkAction;
  /** Rows the batch will touch. */
  changing: StoreBulkReviewLine[];
  /** Rows it will not, each saying why. Blocked and failed together: before the
   *  write the distinction §19 draws does not exist yet, and both answer the
   *  seller's question, which is "will this one change". */
  staying: StoreBulkReviewLine[];
};

/**
 * The review for an action the row already carries a verdict for.
 *
 * `detail` is null on every changing row, and that is the honest answer rather
 * than a gap: there is nothing to say about a publish beyond that it will
 * happen. The one thing that would go here — "and it will be reviewed first" —
 * is true of all of them and is said once, in the subtitle.
 */
export function reviewFromPartition(
  partitioned: StoreBulkPartition,
  action: StoreBulkAction
): StoreBulkReview {
  return {
    action,
    changing: partitioned.eligible.map((row) => ({
      id: row.id,
      title: row.title,
      detail: null,
      warning: null
    })),
    staying: partitioned.blocked.map(({ row, reason }) => ({
      id: row.id,
      title: row.title,
      detail: reason,
      warning: null
    }))
  };
}

/**
 * The review for a reprice: the server's dry run, rendered.
 *
 * Every string here comes off the wire. `price_label` is produced by
 * `_marketplace_batch_price_outcome`, which is the function that will write it,
 * so the right-hand side of the arrow is the value that lands in the row — not a
 * number this file worked out and hoped matched. That is the only reason the
 * arrow is allowed to exist.
 *
 * `titleFor` is a fallback, not the source: the preview names each row, and the
 * local lookup only covers a row the payload somehow did not.
 */
export function reviewFromPreview(
  response: MarketplaceBatchPreview,
  action: StoreBulkAction,
  titleFor: (listingId: number) => string
): StoreBulkReview {
  const results = Array.isArray(response?.results) ? response.results : [];
  const changing: StoreBulkReviewLine[] = [];
  const staying: StoreBulkReviewLine[] = [];

  results.forEach((entry) => {
    const line = {
      id: entry.listing_id,
      title: entry.title || titleFor(entry.listing_id)
    };
    if (entry.outcome === "would_apply") {
      changing.push({
        ...line,
        detail: changeDetail(action, entry),
        // The material-field rule: `price_label` and `category` both send a
        // live, approved listing back to `pending_review`. A seller repricing or
        // re-filing their whole store needs to know that before the tap, not
        // from a buyer who cannot find the product. The server decides it —
        // `requires_rereview` — and says so per row, because it is not true of
        // the drafts in the same selection.
        warning: entry.returns_to_review ? "Goes back to review" : null
      });
      return;
    }
    staying.push({ ...line, detail: outcomeReason(entry), warning: null });
  });

  return { action, changing, staying };
}

/**
 * "$49.00 → $12.00", or "Set to $12.00" for a listing that has no price yet.
 *
 * The second case is the §11 rule showing up in the UI: a listing with no price
 * has *no price*, and an arrow starting at "$0.00" or "Free" would be this
 * module inventing the very fact the rule forbids inventing. An empty
 * `current_price_label` is rendered as an absence.
 */
function priceMove(entry: MarketplaceBatchPreviewResult): string | null {
  const to = entry.price_label;
  if (!to) return null;
  const from = entry.current_price_label;
  return from ? `${from} → ${to}` : `Set to ${to}`;
}

/** "Education / Crypto Basics" — a filing as one readable string, or "". */
function filing(category: string | undefined, subcategory: string | undefined): string {
  const parent = (category || "").trim();
  const child = (subcategory || "").trim();
  if (!parent) return "";
  return child ? `${parent} / ${child}` : parent;
}

/**
 * "Education / Crypto Basics → Home & Kitchen", or "File under …" for a listing
 * that has no category yet.
 *
 * The uncategorised case is the same shape of honesty as `priceMove`'s: a
 * listing with no category has none, and an arrow starting at "Uncategorised"
 * would put a word in the store that nothing wrote. Worth getting right because
 * these are the rows the action most exists for — `MISSING_CATEGORY` is a
 * publish blocker, so a bulk move is how a seller clears it off forty drafts.
 */
function categoryMove(entry: MarketplaceBatchPreviewResult): string | null {
  const to = filing(entry.category, entry.subcategory);
  if (!to) return null;
  const from = filing(entry.current_category, entry.current_subcategory);
  return from ? `${from} → ${to}` : `File under ${to}`;
}

/**
 * What this row's change looks like, for the action doing it.
 *
 * A table rather than a chain of `action === "price" ? …`, for the reason
 * `BULK_VERB` is a table: the previous two-valued form was correct and would
 * have silently rendered a category move as a price move — `price_label` is
 * absent on a category entry, so the row would have shown no detail at all and
 * the confirm face would have hidden every changing row behind its
 * `line.detail` filter. A missing key here is a compile error instead.
 */
const CHANGE_DETAIL: Partial<
  Record<StoreBulkAction, (entry: MarketplaceBatchPreviewResult) => string | null>
> = {
  price: priceMove,
  category: categoryMove
};

function changeDetail(
  action: StoreBulkAction,
  entry: MarketplaceBatchPreviewResult
): string | null {
  return CHANGE_DETAIL[action]?.(entry) ?? null;
}

/**
 * What a *committed* row reads back as — the last step of §31.
 *
 * Reads the server's stored value rather than a word, for the same reason the
 * preview does: a figure or a filing the seller can check against the list
 * behind the sheet is the only version of "it worked" that proves anything.
 * Falls back to the action's `done` phrase for the actions that store no value
 * worth echoing.
 */
export function resultDetail(action: StoreBulkAction, entry: MarketplaceBatchResult): string {
  if (action === "price" && entry.price_label) return entry.price_label;
  if (action === "category") {
    const filed = filing(entry.category, entry.subcategory);
    if (filed) return filed;
  }
  return BULK_VERB[action].done;
}

/** "Reprice 14 · 4 blocked" — the sentence on the confirm button. */
export function reviewLabel(review: StoreBulkReview): string {
  const verb = BULK_VERB[review.action];
  if (review.changing.length === 0) return `Nothing to ${verb.plain}`;
  return review.staying.length > 0
    ? `${verb.imperative} ${review.changing.length} · ${review.staying.length} blocked`
    : `${verb.imperative} ${review.changing.length}`;
}
