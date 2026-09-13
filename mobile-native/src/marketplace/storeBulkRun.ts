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
   * The pricing rule, for a reprice; `null` for actions that take no payload.
   *
   * Part of the attempt's identity, not a detail hanging off it. A key that
   * covers only (action, ids) is a key that cannot tell "cost + 20%" from
   * "cost + 25%" on the same fourteen products — and the second one would be
   * answered with a *replay of the first*, because a spent key means the server
   * returns its stored result without looking at the new payload. The seller
   * would watch a confirmation for prices that were never applied.
   */
  rule: MarketplacePricingRule | null;
  idempotencyKey: string;
};

function normalizeIds(ids: number[]): number[] {
  return Array.from(new Set(ids.filter((id) => Number.isFinite(id) && id > 0))).sort((a, b) => a - b);
}

/** A rule as one comparable string. `null` and "no rule" are the same thing. */
function ruleKey(rule: MarketplacePricingRule | null | undefined): string {
  return rule ? `${rule.type}:${rule.value}` : "-";
}

/**
 * Mint an attempt. Called once, when the seller confirms — not when the request
 * is sent, and not on retry.
 */
export function beginAttempt(
  action: StoreBulkAction,
  ids: number[],
  rule: MarketplacePricingRule | null = null
): StoreBulkAttempt {
  return {
    action,
    ids: normalizeIds(ids),
    rule,
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
  rule: MarketplacePricingRule | null = null
): boolean {
  if (!attempt || attempt.action !== action) return false;
  // A changed rule is different work, even over the identical id set — see the
  // note on `StoreBulkAttempt.rule`. Compared before the ids because it is the
  // cheaper check and the one more likely to differ between two taps.
  if (ruleKey(attempt.rule) !== ruleKey(rule)) return false;
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
        detail: priceMove(entry),
        // The material-field rule: `price_label` sends a live, approved listing
        // back to `pending_review`. A seller repricing their whole store needs
        // to know that before the tap, not from a buyer who cannot find the
        // product. The server decides it — `requires_rereview` — and says so per
        // row, because it is not true of the drafts in the same selection.
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

/** "Reprice 14 · 4 blocked" — the sentence on the confirm button. */
export function reviewLabel(review: StoreBulkReview): string {
  const verb = BULK_VERB[review.action];
  if (review.changing.length === 0) return `Nothing to ${verb.plain}`;
  return review.staying.length > 0
    ? `${verb.imperative} ${review.changing.length} · ${review.staying.length} blocked`
    : `${verb.imperative} ${review.changing.length}`;
}
