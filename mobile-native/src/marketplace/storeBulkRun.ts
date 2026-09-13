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

import type { MarketplaceBatchResponse, MarketplaceBatchResult } from "../api/marketplace";
import type { StoreBulkAction } from "./storeSelection";

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
  idempotencyKey: string;
};

function normalizeIds(ids: number[]): number[] {
  return Array.from(new Set(ids.filter((id) => Number.isFinite(id) && id > 0))).sort((a, b) => a - b);
}

/**
 * Mint an attempt. Called once, when the seller confirms — not when the request
 * is sent, and not on retry.
 */
export function beginAttempt(action: StoreBulkAction, ids: number[]): StoreBulkAttempt {
  return {
    action,
    ids: normalizeIds(ids),
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
  ids: number[]
): boolean {
  if (!attempt || attempt.action !== action) return false;
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

  const verb = action === "publish" ? "published" : "hidden";
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
export function outcomeReason(entry: MarketplaceBatchResult): string {
  if (entry.reason) return entry.reason;
  return entry.outcome === "blocked" ? "Not ready yet" : "Could not be updated";
}
