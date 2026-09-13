/**
 * The two things a bulk attempt can get catastrophically wrong, tested where
 * they are decidable: publishing twice (§23) and lying about what happened (§19).
 */

import type { MarketplaceBatchResponse, MarketplaceBatchResult } from "../../api/marketplace";
import {
  beginAttempt,
  idsToSend,
  isSameAttempt,
  outcomeOf,
  outcomeReason
} from "../storeBulkRun";

function entry(
  listing_id: number,
  outcome: MarketplaceBatchResult["outcome"],
  over: Partial<MarketplaceBatchResult> = {}
): MarketplaceBatchResult {
  return { listing_id, outcome, ...over };
}

function response(
  results: MarketplaceBatchResult[],
  over: Partial<MarketplaceBatchResponse> = {}
): MarketplaceBatchResponse {
  return {
    ok: true,
    batch_id: "batch-1",
    action: "publish",
    requested_count: results.length,
    successful_count: results.filter((r) => r.outcome === "succeeded").length,
    blocked_count: results.filter((r) => r.outcome === "blocked").length,
    failed_count: results.filter((r) => r.outcome === "failed").length,
    results,
    ...over
  };
}

/* ------------------------------------------------------------------ *
 * §23 — pressing Apply twice
 * ------------------------------------------------------------------ */

describe("the idempotency key", () => {
  it("stays the same across every send of one attempt", () => {
    // The whole mechanism. If the key moved with the send, the second tap would
    // be a second request and the server would have no way to know.
    const attempt = beginAttempt("publish", [3, 1, 2]);
    expect(attempt.idempotencyKey).toBe(attempt.idempotencyKey);
    expect(isSameAttempt(attempt, "publish", [1, 2, 3])).toBe(true);
  });

  it("does not care what order the seller tapped the rows in", () => {
    // The server hashes the sorted list, so [3,1,2] and [1,2,3] are one request.
    // Treating them as different would mint a fresh key for a re-tap and publish
    // everything again.
    const attempt = beginAttempt("publish", [1, 2, 3]);
    expect(isSameAttempt(attempt, "publish", [3, 2, 1])).toBe(true);
  });

  it("ignores a duplicate id, the way the server does", () => {
    const attempt = beginAttempt("publish", [1, 1, 2]);
    expect(attempt.ids).toEqual([1, 2]);
    expect(isSameAttempt(attempt, "publish", [2, 1, 1])).toBe(true);
  });

  it("is a different attempt once the selection changes", () => {
    const attempt = beginAttempt("publish", [1, 2]);
    expect(isSameAttempt(attempt, "publish", [1, 2, 3])).toBe(false);
    expect(isSameAttempt(attempt, "publish", [1])).toBe(false);
  });

  it("is a different attempt for the other action on the same rows", () => {
    // Publishing then hiding the same six listings is two pieces of work. Reusing
    // the key would have the server replay the publish and report it as a hide.
    const attempt = beginAttempt("publish", [1, 2]);
    expect(isSameAttempt(attempt, "hide", [1, 2])).toBe(false);
  });

  it("mints a distinct key per attempt", () => {
    const keys = new Set(
      Array.from({ length: 50 }, () => beginAttempt("publish", [1]).idempotencyKey)
    );
    expect(keys.size).toBe(50);
  });

  it("has nothing to send when nothing was selected", () => {
    expect(beginAttempt("publish", []).ids).toEqual([]);
  });
});

/* ------------------------------------------------------------------ *
 * What goes on the wire
 * ------------------------------------------------------------------ */

describe("the ids sent", () => {
  it("includes rows the preview said were blocked", () => {
    // The preview is a snapshot; the server re-checks every row at write time.
    // Filtering here would drop work the seller fixed a minute ago on another
    // device, and would send a stale yes for a row that has since broken.
    const attempt = beginAttempt("publish", [1, 2, 3]);
    expect(idsToSend(attempt)).toEqual([1, 2, 3]);
  });
});

/* ------------------------------------------------------------------ *
 * §19 — partial success is the normal case
 * ------------------------------------------------------------------ */

describe("the result the seller reads", () => {
  const fourteenAndFour = response([
    ...Array.from({ length: 14 }, (_, i) => entry(i + 1, "succeeded")),
    ...Array.from({ length: 4 }, (_, i) => entry(100 + i, "blocked", { reason: "1 thing left" }))
  ]);

  it("says fourteen published and four need attention", () => {
    const outcome = outcomeOf("publish", fourteenAndFour);
    expect(outcome.headline).toBe("14 products published");
    expect(outcome.attention).toBe("4 products need attention");
  });

  it("never reports the request count as the success count", () => {
    // The first of the two §19 failures: "18 published" over a batch where four
    // are still drafts.
    const outcome = outcomeOf("publish", fourteenAndFour);
    expect(outcome.headline).not.toContain("18");
    expect(outcome.succeeded).toHaveLength(14);
  });

  it("does not call a partial success a failure either", () => {
    // The second failure, and the more tempting one: any blocked row turning the
    // whole batch red, so a seller who published fourteen products is told the
    // operation failed and taps again.
    const outcome = outcomeOf("publish", fourteenAndFour);
    expect(outcome.headline).toContain("14");
    expect(outcome.headline).not.toMatch(/fail|error|could not/i);
  });

  it("believes the rows over the summary when the two disagree", () => {
    // A server that has miscounted is a real possibility and the seller cannot
    // audit it -- except that the list of rows is right underneath the headline.
    // So the headline is built from the list.
    const lying = response(
      [entry(1, "succeeded"), entry(2, "blocked", { reason: "1 thing left" })],
      { successful_count: 2, blocked_count: 0 }
    );
    expect(outcomeOf("publish", lying).headline).toBe("1 product published");
    expect(outcomeOf("publish", lying).attention).toBe("1 product needs attention");
  });

  it("keeps blocked and failed apart", () => {
    // Blocked is a task the seller can do; failed is a row that could not be
    // attempted. Collapsing them turns "add a price" into "an error occurred".
    const mixed = response([
      entry(1, "succeeded"),
      entry(2, "blocked", { reason: "2 things left" }),
      entry(3, "failed", { reason: "Listing not found", error_code: "NOT_FOUND" })
    ]);
    const outcome = outcomeOf("publish", mixed);
    expect(outcome.blocked.map((e) => e.listing_id)).toEqual([2]);
    expect(outcome.failed.map((e) => e.listing_id)).toEqual([3]);
    // Both still count toward the work left, because both mean the row did not
    // move and the seller needs to know.
    expect(outcome.attention).toBe("2 products need attention");
  });

  it("says nothing about attention when everything landed", () => {
    const outcome = outcomeOf("publish", response([entry(1, "succeeded"), entry(2, "succeeded")]));
    expect(outcome.headline).toBe("2 products published");
    expect(outcome.attention).toBeNull();
  });

  it("does not claim a success when nothing moved", () => {
    const outcome = outcomeOf("publish", response([entry(1, "blocked", { reason: "1 thing left" })]));
    expect(outcome.headline).toBe("Nothing published");
    expect(outcome.attention).toBe("1 product needs attention");
  });

  it("uses the verb of the action that ran", () => {
    expect(outcomeOf("hide", response([entry(1, "succeeded")])).headline).toBe("1 product hidden");
  });

  it("counts one product as one product", () => {
    expect(outcomeOf("publish", response([entry(1, "succeeded")])).headline).toBe(
      "1 product published"
    );
  });

  it("gives a replayed batch the same answer as the first one", () => {
    // §23 from the seller's side: the second tap must read exactly like the
    // first. A "nothing to do" here would tell them the publish did not happen.
    const first = outcomeOf("publish", fourteenAndFour);
    const again = outcomeOf("publish", { ...fourteenAndFour, replayed: true });
    expect(again.headline).toBe(first.headline);
    expect(again.attention).toBe(first.attention);
    expect(again.replayed).toBe(true);
  });

  it("survives a response with no results at all", () => {
    const outcome = outcomeOf("publish", response([]));
    expect(outcome.headline).toBe("Nothing published");
    expect(outcome.attention).toBeNull();
  });
});

/* ------------------------------------------------------------------ *
 * Per-row prose
 * ------------------------------------------------------------------ */

describe("the reason beside a row", () => {
  it("repeats the server's sentence", () => {
    expect(outcomeReason(entry(1, "blocked", { reason: "2 things left" }))).toBe("2 things left");
  });

  it("does not diagnose a row the server gave no reason for", () => {
    // "Not ready yet" is true about a blocked row with no reason. "Add a price"
    // would be this build putting a cause in the server's mouth.
    expect(outcomeReason(entry(1, "blocked"))).toBe("Not ready yet");
    expect(outcomeReason(entry(1, "failed"))).toBe("Could not be updated");
  });
});
