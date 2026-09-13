/**
 * The two things a bulk attempt can get catastrophically wrong, tested where
 * they are decidable: publishing twice (§23) and lying about what happened (§19).
 */

import type {
  MarketplaceBatchPreview,
  MarketplaceBatchPreviewResult,
  MarketplaceBatchResponse,
  MarketplaceBatchResult
} from "../../api/marketplace";
import {
  beginAttempt,
  categoryPayload,
  pricePayload,
  idsToSend,
  isSameAttempt,
  outcomeOf,
  outcomeReason,
  reviewFromPartition,
  reviewFromPreview,
  reviewLabel
} from "../storeBulkRun";
import type { StoreListingRow } from "../../api/storeDashboard";

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

/* ------------------------------------------------------------------ *
 * §23 again — the key has to cover the rule, not just the rows
 * ------------------------------------------------------------------ */

describe("an attempt that carries a pricing rule", () => {
  const PLUS_20 = { type: "COST_PLUS_PERCENT", value: 20 } as const;
  const PLUS_25 = { type: "COST_PLUS_PERCENT", value: 25 } as const;
  const TO_HOME = { category: "Home & Kitchen", subcategory: "" } as const;

  /**
   * The failure this exists for, and it is not a double-write.
   *
   * A key that identifies only (action, ids) cannot tell "cost + 20% on these
   * fourteen" from "cost + 25% on these fourteen". The seller previews 20%,
   * changes their mind, previews 25%, taps Apply — and the key is already spent,
   * so the server answers by *replaying the 20% batch it already ran*. The sheet
   * then shows a confirmation for prices that were never written. Nothing errors,
   * nothing is written twice, and the numbers on screen are fiction.
   */
  it("is not the same attempt once the rule changes", () => {
    const attempt = beginAttempt("price", [3, 1, 2], pricePayload(PLUS_20));
    expect(isSameAttempt(attempt, "price", [1, 2, 3], pricePayload(PLUS_20))).toBe(true);
    expect(isSameAttempt(attempt, "price", [1, 2, 3], pricePayload(PLUS_25))).toBe(false);
  });

  /** A different rule *type* at the same number is different work too. */
  it("tells a percentage from a margin at the same value", () => {
    const attempt = beginAttempt("price", [1], pricePayload({ type: "COST_PLUS_PERCENT", value: 40 }));
    expect(
      isSameAttempt(attempt, "price", [1], pricePayload({ type: "TARGET_MARGIN", value: 40 }))
    ).toBe(false);
  });

  /** And dropping the rule entirely is not a match for having one. */
  it("does not match a rule-less request", () => {
    const attempt = beginAttempt("price", [1], pricePayload(PLUS_20));
    expect(isSameAttempt(attempt, "price", [1], null)).toBe(false);
    expect(isSameAttempt(attempt, "price", [1])).toBe(false);
  });

  /**
   * The same failure, one action over: a seller who previews "Home & Kitchen",
   * goes back, picks "Education" and applies must not be handed the first move's
   * summary. The category is in the key for the identical reason the rule is.
   */
  it("is not the same attempt once the category changes", () => {
    const attempt = beginAttempt("category", [1, 2], categoryPayload(TO_HOME));
    expect(isSameAttempt(attempt, "category", [2, 1], categoryPayload(TO_HOME))).toBe(true);
    expect(
      isSameAttempt(attempt, "category", [2, 1], categoryPayload({ category: "Education", subcategory: "" }))
    ).toBe(false);
  });

  /**
   * A move within the same parent is a real change, so the subcategory has to be
   * in the key too. Were it not, "Education / Trading" would replay the summary
   * of "Education / Crypto Basics" and report products filed somewhere they are
   * not.
   */
  it("is not the same attempt once only the subcategory changes", () => {
    const attempt = beginAttempt(
      "category",
      [1],
      categoryPayload({ category: "Education", subcategory: "Crypto Basics" })
    );
    expect(
      isSameAttempt(attempt, "category", [1], categoryPayload({ category: "Education", subcategory: "Trading" }))
    ).toBe(false);
  });

  /**
   * Two payload kinds cannot collide however they are spelled. Contrived on
   * purpose: the guarantee is that the *kind* is part of the compared string, so
   * no pair of payloads from different actions can ever hash alike.
   */
  it("never confuses a price payload with a category payload", () => {
    const attempt = beginAttempt("price", [1], pricePayload(PLUS_20));
    expect(
      isSameAttempt(attempt, "price", [1], categoryPayload({ category: "COST_PLUS_PERCENT", subcategory: "20" }))
    ).toBe(false);
  });

  /**
   * The same guarantee one level down, where it is much easier to lose.
   *
   * Categories are free text, so every printable character is one a seller may
   * type — including whichever one a key builder picks to glue the pair together.
   * Joined by `|`, the aisle `"Toys|Games"` with no child and the aisle `"Toys"`
   * with the child `"Games"` produce one string, and the second of those two
   * moves is silently treated as a repeat of the first: the seller taps Apply,
   * nothing is sent, and the sheet reports the previous move's results.
   *
   * Two spellings chosen to collide under every separator a person would reach
   * for. This passes because the pair is encoded rather than concatenated.
   */
  it("keeps the pair apart even when the text contains the obvious separators", () => {
    const separators = ["|", "/", ":", " ", ",", "\t"];

    separators.forEach((sep) => {
      const glued = categoryPayload({ category: `Toys${sep}Games`, subcategory: "" });
      const split = categoryPayload({ category: "Toys", subcategory: "Games" });
      const attempt = beginAttempt("category", [1], glued);

      expect(isSameAttempt(attempt, "category", [1], split)).toBe(false);
      expect(isSameAttempt(attempt, "category", [1], glued)).toBe(true);
    });
  });

  /**
   * The precomputed actions carry no rule and must keep matching as they did
   * before — otherwise Try again after a publish timeout mints a second key and
   * publishes everything twice, which is the original §23 failure reintroduced
   * by the fix for this one.
   */
  it("leaves publish and hide exactly as they were", () => {
    const attempt = beginAttempt("publish", [1, 2]);
    expect(attempt.payload).toBeNull();
    expect(isSameAttempt(attempt, "publish", [2, 1])).toBe(true);
    expect(isSameAttempt(attempt, "hide", [2, 1])).toBe(false);
  });
});

/* ------------------------------------------------------------------ *
 * §34 — the review face
 * ------------------------------------------------------------------ */

function previewEntry(
  listing_id: number,
  outcome: MarketplaceBatchPreviewResult["outcome"],
  over: Partial<MarketplaceBatchPreviewResult> = {}
): MarketplaceBatchPreviewResult {
  return { listing_id, outcome, ...over };
}

function preview(results: MarketplaceBatchPreviewResult[]): MarketplaceBatchPreview {
  return {
    ok: true,
    preview: true,
    action: "price",
    requested_count: results.length,
    eligible_count: results.filter((r) => r.outcome === "would_apply").length,
    blocked_count: results.filter((r) => r.outcome === "blocked").length,
    failed_count: results.filter((r) => r.outcome === "failed").length,
    results
  };
}

const noTitles = (id: number) => `Listing ${id}`;

describe("the reprice review", () => {
  it("draws the arrow from the server's two labels", () => {
    const review = reviewFromPreview(
      preview([
        previewEntry(7, "would_apply", {
          title: "Lamp",
          current_price_label: "$49.00",
          price_label: "$12.00"
        })
      ]),
      "price",
      noTitles
    );
    expect(review.changing).toHaveLength(1);
    expect(review.changing[0].detail).toBe("$49.00 → $12.00");
  });

  /**
   * §11 in the UI. A listing with no price has *no* price, and an arrow starting
   * at "$0.00" or "Free" would invent the fact the rule forbids inventing.
   */
  it("does not invent a starting price for a listing that has none", () => {
    const review = reviewFromPreview(
      preview([previewEntry(7, "would_apply", { price_label: "$12.00" })]),
      "price",
      noTitles
    );
    expect(review.changing[0].detail).toBe("Set to $12.00");
  });

  /**
   * The warning that costs a seller sales if it is missing: `price_label` is a
   * material field, so a live approved product goes back to the review queue and
   * off sale. It has to be on the row, not in a footnote, because it is true of
   * some rows in the batch and not others.
   */
  it("carries the re-review warning per row, separately from the price", () => {
    const review = reviewFromPreview(
      preview([
        previewEntry(1, "would_apply", { price_label: "$1.00", returns_to_review: true }),
        previewEntry(2, "would_apply", { price_label: "$2.00" })
      ]),
      "price",
      noTitles
    );
    expect(review.changing[0].warning).toBe("Goes back to review");
    expect(review.changing[1].warning).toBeNull();
    // And it is not smuggled into `detail`, which is where a block reason goes.
    expect(review.changing[0].detail).toBe("Set to $1.00");
  });

  /**
   * Before the write, "blocked" and "failed" answer the same question — will this
   * row change — so they share the list. The distinction §19 draws is about the
   * *result*, where it decides whether the seller has a task or an error.
   */
  it("puts everything that will not change in one list, each with its reason", () => {
    const review = reviewFromPreview(
      preview([
        previewEntry(1, "would_apply", { price_label: "$1.00" }),
        previewEntry(2, "blocked", { reason: "Already at that price" }),
        previewEntry(3, "failed", { reason: "No longer in your store" })
      ]),
      "price",
      noTitles
    );
    expect(review.changing.map((line) => line.id)).toEqual([1]);
    expect(review.staying.map((line) => line.detail)).toEqual([
      "Already at that price",
      "No longer in your store"
    ]);
  });

  it("falls back to the local title only when the server did not name the row", () => {
    const review = reviewFromPreview(
      preview([
        previewEntry(1, "would_apply", { title: "Lamp", price_label: "$1.00" }),
        previewEntry(2, "would_apply", { price_label: "$2.00" })
      ]),
      "price",
      (id) => `local ${id}`
    );
    expect(review.changing.map((line) => line.title)).toEqual(["Lamp", "local 2"]);
  });

  it("keeps every requested row, so nothing is silently dropped from the review", () => {
    const review = reviewFromPreview(
      preview([
        previewEntry(1, "would_apply", { price_label: "$1.00" }),
        previewEntry(2, "blocked"),
        previewEntry(3, "failed")
      ]),
      "price",
      noTitles
    );
    expect(review.changing.length + review.staying.length).toBe(3);
  });
});

describe("the review for publish and hide", () => {
  function row(id: number): StoreListingRow {
    return { id, title: `Listing ${id}` } as StoreListingRow;
  }

  it("reads the server's block reason through unchanged", () => {
    const review = reviewFromPartition(
      { eligible: [row(1)], blocked: [{ row: row(2), reason: "1 thing left" }] },
      "publish"
    );
    expect(review.changing).toEqual([{ id: 1, title: "Listing 1", detail: null, warning: null }]);
    expect(review.staying[0].detail).toBe("1 thing left");
  });
});

describe("the sentence on the confirm button", () => {
  const line = (id: number) => ({ id, title: `L${id}`, detail: null, warning: null });

  it("names the verb, the count, and how many will not move", () => {
    expect(
      reviewLabel({ action: "price", changing: [line(1), line(2)], staying: [line(3)] })
    ).toBe("Reprice 2 · 1 blocked");
  });

  it("drops the blocked half when there is none", () => {
    expect(reviewLabel({ action: "price", changing: [line(1)], staying: [] })).toBe("Reprice 1");
  });

  /**
   * Every action gets its own verb from one table. This is the test that fails
   * if a fourth action is added without a word for it — the alternative, an
   * inline `action === "publish" ? … : …`, would have labelled a reprice "Hide".
   */
  it("uses the right verb for each action", () => {
    expect(reviewLabel({ action: "publish", changing: [line(1)], staying: [] })).toBe("Publish 1");
    expect(reviewLabel({ action: "hide", changing: [line(1)], staying: [] })).toBe("Hide 1");
    expect(reviewLabel({ action: "price", changing: [line(1)], staying: [] })).toBe("Reprice 1");
  });

  it("says nothing will happen rather than offering a zero", () => {
    expect(reviewLabel({ action: "price", changing: [], staying: [line(1)] })).toBe(
      "Nothing to reprice"
    );
  });
});

/* ------------------------------------------------------------------ *
 * The result headline, for the third action
 * ------------------------------------------------------------------ */

describe("a repriced batch reads back as repriced", () => {
  it("does not borrow publish's or hide's verb", () => {
    const outcome = outcomeOf(
      "price",
      response([entry(1, "succeeded", { price_label: "$12.00" }), entry(2, "blocked")])
    );
    expect(outcome.headline).toBe("1 product repriced");
    expect(outcome.attention).toBe("1 product needs attention");
  });

  it("says nothing repriced rather than nothing published", () => {
    expect(outcomeOf("price", response([entry(1, "blocked")])).headline).toBe("Nothing repriced");
  });
});
