/**
 * §9 — "see exact rejection reason" on the CLIENT side of the wire.
 *
 * `tests/marketplace/test_seller_review_verdict.py` proves the server now sends
 * the verdict, and proves what it deliberately withholds (the reviewer's note,
 * the risk score, which admin decided). This file is the other half of that
 * journey, and it exists because the server sending it was never going to be
 * enough: a field on a payload that no screen reads is scaffolding (§31), and
 * the seller's complaint was never "the API is missing a key" — it was that a
 * rejected product said "Hidden from buyers" and nothing else, anywhere, ever.
 *
 * Three refusals are asserted here, and each is a case where rendering *less*
 * is the correct behaviour:
 *
 *   * a listing merely waiting in the queue gets no line at all, because
 *     `seller_message("")` falls back to the generic OTHER sentence and a
 *     merchant told their untouched product "needs a change before it can go
 *     live" will go and change something nobody objected to;
 *   * a verdict that says the seller must act and carries no sentence is
 *     refused whole by the normalizer, because an alarm with no cause is worse
 *     than the silence it replaced;
 *   * a state this build has never heard of keeps its server sentence and loses
 *     only its prefix, because the sentence is the part the seller acts on.
 *
 * The rule underneath: the words belong to the server. There is exactly one
 * code->English table, `listing_review.SELLER_MESSAGES`, and the last test in
 * this file is there to keep a second one from growing in the component.
 */

import { render } from "@testing-library/react-native";
import {
  normalizeMarketplaceListing,
  normalizeMarketplaceListings,
  type ListingReviewVerdict,
  type MarketplaceListing
} from "../marketplace";
import { deriveRows } from "../storeDashboard";
import { listingReviewCopy, StoreListingRow } from "../../components/store/StoreListingRow";

/** The real sentence for `INVALID_MEDIA`, copied from `SELLER_MESSAGES`. */
const INVALID_MEDIA = "The product images need to be replaced.";

/** A listing as the seller route sends it. */
function payload(over: Partial<MarketplaceListing> = {}): MarketplaceListing {
  return {
    id: 1,
    listing_id: 1,
    title: "Brass desk lamp",
    price_label: "$24.00",
    currency: "USD",
    quantity: 40,
    status: "rejected",
    approval_status: "rejected",
    listing_type: "physical",
    product_type: "physical",
    review: verdict(),
    ...over
  } as MarketplaceListing;
}

/**
 * A test double of `seller_verdict`'s output.
 *
 * The server never sends `needs_action` without a `message` — it derives the
 * second from the first's reason code — so the fixture defaults hold that
 * invariant and the tests that break it do so on purpose, one field at a time.
 */
function verdict(over: Partial<ListingReviewVerdict> = {}): ListingReviewVerdict {
  return {
    state: "rejected",
    decided: true,
    needs_action: true,
    reason_code: "INVALID_MEDIA",
    message: INVALID_MEDIA,
    review_version: 2,
    decided_at: "2026-09-10T12:00:00",
    ...over
  };
}

/** What the server sends about a listing still sitting in the queue. */
function queued(): ListingReviewVerdict {
  return {
    state: "pending_review",
    decided: false,
    needs_action: false,
    reason_code: "",
    message: "",
    review_version: 1,
    decided_at: ""
  };
}

/* ------------------------------------------------------------------ *
 * The verdict has to survive the normalizer
 * ------------------------------------------------------------------ */

describe("normalizeMarketplaceListing", () => {
  it("carries the server's verdict through untouched", () => {
    // The normalizer rebuilds every field by hand rather than spreading, so a
    // field it forgets to name vanishes silently and every screen falls back to
    // the stock copy — which is the exact state this change retires.
    expect(normalizeMarketplaceListing(payload()).review).toEqual(verdict());
  });

  it("survives the list normalizer too, which is what the store screen calls", () => {
    const [listing] = normalizeMarketplaceListings([payload()]);
    expect(listing.review?.message).toBe(INVALID_MEDIA);
    expect(listing.review?.reason_code).toBe("INVALID_MEDIA");
  });

  it("leaves a payload with no verdict undefined rather than inventing one", () => {
    // A cached snapshot written before this field existed. `undefined` reads
    // downstream as "not told", which every surface already handles; a
    // synthesised empty verdict would read as "decided, nothing wrong" and put
    // a clean bill of health over a rejected listing.
    expect(normalizeMarketplaceListing(payload({ review: undefined })).review).toBeUndefined();
  });

  it("refuses a verdict that demands action and cannot say why", () => {
    // The headline refusal. A row rendering this would show a red rejection
    // line with a state word and no cause — an alarm the seller cannot act on,
    // which is worse than the silence it replaced. Refused whole so the row
    // falls back to its stock copy, which at least promises nothing.
    const mute = verdict({ message: "" });
    expect(normalizeMarketplaceListing(payload({ review: mute })).review).toBeUndefined();
  });

  it("keeps a decided verdict that needs no action, message or not", () => {
    // The other direction, and the reason the guard is `needs_action && !message`
    // rather than a flat message check. An approved listing has a decision worth
    // carrying (its revision, when it was decided) and nothing to tell the
    // seller to do, so refusing it would strip most of a healthy store.
    const approved = verdict({
      state: "approved",
      needs_action: false,
      reason_code: "",
      message: ""
    });
    expect(normalizeMarketplaceListing(payload({ review: approved })).review).toEqual(approved);
  });

  it("keeps the queued verdict, which is the one that must render nothing", () => {
    // Carried, not refused: `decided: false` is information. What it must not
    // do is produce a line, and that is enforced one layer up in
    // `listingReviewCopy` rather than by throwing the verdict away here.
    expect(normalizeMarketplaceListing(payload({ review: queued() })).review).toEqual(queued());
  });

  it("refuses a verdict with no state rather than rendering a headless one", () => {
    const headless = { decided: true, needs_action: true, message: INVALID_MEDIA };
    expect(
      normalizeMarketplaceListing(payload({ review: headless as unknown as ListingReviewVerdict }))
        .review
    ).toBeUndefined();
  });

  it("does not treat a non-object as a verdict", () => {
    expect(
      normalizeMarketplaceListing(payload({ review: "rejected" as unknown as ListingReviewVerdict }))
        .review
    ).toBeUndefined();
  });

  it("does not turn a missing revision into NaN", () => {
    // `Number(undefined)` is NaN, and NaN rendered into a "Revision {n}" string
    // is the kind of thing that ships.
    const sparse = { state: "rejected", needs_action: false } as unknown as ListingReviewVerdict;
    const review = normalizeMarketplaceListing(payload({ review: sparse })).review;
    expect(review?.review_version).toBe(0);
    expect(Number.isNaN(review?.review_version as number)).toBe(false);
  });
});

/* ------------------------------------------------------------------ *
 * Payload -> normalizer -> row
 * ------------------------------------------------------------------ */

describe("a seller payload carried all the way to a row", () => {
  const rowsFor = (listings: MarketplaceListing[]) =>
    deriveRows(
      { listings: normalizeMarketplaceListings(listings), orders: [], cached_at: "" } as never,
      new Date(2026, 8, 14)
    );

  it("puts the reason on the row, which is the only place the seller looks", () => {
    expect(rowsFor([payload()])[0].review?.message).toBe(INVALID_MEDIA);
  });

  it("leaves the verdict null when the payload carried none", () => {
    // `null` and not `undefined`, matching `readiness` — the row type says
    // "not told" in one shape so a caller cannot get it right for one field and
    // wrong for the other.
    expect(rowsFor([payload({ review: undefined })])[0].review).toBeNull();
  });

  it("does not lose the reason when the listing is one of many", () => {
    const rows = rowsFor([
      payload({ id: 1, listing_id: 1, review: queued() }),
      payload({ id: 2, listing_id: 2 })
    ]);
    expect(rows.find((row) => row.id === 1)?.review?.needs_action).toBe(false);
    expect(rows.find((row) => row.id === 2)?.review?.message).toBe(INVALID_MEDIA);
  });
});

/* ------------------------------------------------------------------ *
 * §9 — the sentence itself
 * ------------------------------------------------------------------ */

describe("the review-reason line", () => {
  it("names the decision and the cause", () => {
    expect(listingReviewCopy(verdict())).toBe(`Rejected · ${INVALID_MEDIA}`);
  });

  it("does not print a snake_cased column value at a merchant", () => {
    const copy = listingReviewCopy(verdict({ state: "changes_requested" }));
    expect(copy).toBe(`Changes needed · ${INVALID_MEDIA}`);
    expect(copy).not.toContain("changes_requested");
    expect(copy).not.toContain("_");
  });

  it("speaks up about a restricted listing, which looks live to its own seller", () => {
    // `restrict` parks the moderation axis and leaves `status` where the
    // merchant put it, so the seller's own release switch still reads published
    // while no buyer can reach the product. It is the one state where saying
    // nothing looks exactly like nothing being wrong.
    expect(listingReviewCopy(verdict({ state: "restricted" }))).toBe(
      `Restricted · ${INVALID_MEDIA}`
    );
  });

  it("says nothing at all about a listing that is merely waiting", () => {
    // The false-alarm case, and the reason `seller_verdict` returns an empty
    // message rather than letting `seller_message("")` fall back to OTHER. That
    // fallback reads "This listing needs a change before it can go live." — a
    // sentence a merchant with an untouched, unreviewed product would act on.
    expect(listingReviewCopy(queued())).toBeNull();
  });

  it("says nothing about an approved listing", () => {
    expect(listingReviewCopy(verdict({ state: "approved", needs_action: false }))).toBeNull();
  });

  it("says nothing when no verdict arrived", () => {
    // Not the same situation as "approved", deliberately the same result. An
    // old snapshot has no news, and no news is not good news — but it is also
    // not grounds for the row to raise an alarm of its own.
    expect(listingReviewCopy(null)).toBeNull();
  });

  it("renders a state this build has never heard of, because the sentence is the server's", () => {
    // A moderation state added after this build shipped. The prefix degrades to
    // a generic one; the sentence — the only part that tells the seller what to
    // do — is untouched. Losing the whole line here would mean a new state
    // silently turning §9 off in the field.
    const copy = listingReviewCopy(verdict({ state: "quarantined" }));
    expect(copy).toBe(`Needs attention · ${INVALID_MEDIA}`);
    expect(copy).not.toContain("quarantined");
  });

  it("renders a reason code this build has never heard of", () => {
    // Same argument one field over. The component never sees the code, so a
    // thirteenth entry in `SELLER_MESSAGES` needs no client release.
    expect(
      listingReviewCopy(
        verdict({ reason_code: "SOME_FUTURE_CODE", message: "The brand needs authorisation." })
      )
    ).toBe("Rejected · The brand needs authorisation.");
  });

  it("stays silent rather than printing a bare state word", () => {
    // Unreachable through the normalizer, which refuses this shape outright.
    // Checked anyway because the helper is exported and the fallback being
    // silence rather than "Rejected · " is what makes the double guard safe.
    expect(listingReviewCopy(verdict({ message: "" }))).toBeNull();
  });
});

/* ------------------------------------------------------------------ *
 * What the seller actually reads
 * ------------------------------------------------------------------ */

describe("the rendered row", () => {
  const rowData = (over: Record<string, unknown> = {}) => ({
    id: 1,
    title: "Brass desk lamp",
    thumbnailUrl: null,
    priceLabel: "$24.00",
    currency: "USD",
    quantity: 40,
    health: "hidden" as const,
    readiness: null,
    review: verdict(),
    unitsSold7d: 0,
    rating: null,
    reviewCount: null,
    ...over
  });

  function renderRow(over: Record<string, unknown> = {}) {
    return render(
      <StoreListingRow
        row={rowData(over) as never}
        priceText="$24.00"
        soldText={null}
        onPress={() => undefined}
        onEdit={() => undefined}
        reducedMotion
      />
    );
  }

  it("tells the seller why, not just that it is hidden", () => {
    const { getByText } = renderRow();
    // The effect, which the row already stated before this change...
    expect(getByText("Hidden from buyers")).toBeTruthy();
    // ...and the cause, which it did not.
    expect(getByText(`Rejected · ${INVALID_MEDIA}`)).toBeTruthy();
  });

  it("renders no reason line for a listing waiting in the queue", () => {
    const { getByText, queryByText } = renderRow({ health: "pending_review", review: queued() });
    expect(getByText("In review — not live yet")).toBeTruthy();
    expect(queryByText(/needs a change before it can go live/)).toBeNull();
    expect(queryByText(/·/)).toBeNull();
  });

  it("renders no reason line when the payload carried no verdict", () => {
    const { getByText, queryByText } = renderRow({ review: null });
    expect(getByText("Hidden from buyers")).toBeTruthy();
    expect(queryByText(new RegExp(INVALID_MEDIA))).toBeNull();
  });

  it("announces the reason to a screen reader too", () => {
    // A seller using VoiceOver got the effect and none of the cause, about the
    // listing that needed the most explaining.
    const { getByLabelText } = renderRow();
    expect(getByLabelText(new RegExp(INVALID_MEDIA))).toBeTruthy();
  });

  it("announces the reason ahead of the stock label", () => {
    // Announcement order is the screen reader's only emphasis. "Rejected —
    // replace the images" outranks "Hidden from buyers" for a seller deciding
    // what to do next, and the visual order cannot be relied on to carry that.
    const { getByLabelText } = renderRow();
    expect(getByLabelText(/Rejected · .*Hidden from buyers/)).toBeTruthy();
  });
});

/* ------------------------------------------------------------------ *
 * The rule the client must not restate
 * ------------------------------------------------------------------ */

describe("the server owns the words", () => {
  it("keeps no code-to-English table of its own", () => {
    // The failure this prevents is slow and quiet: a second copy of
    // `SELLER_MESSAGES` in the component drifts the first time a reason code is
    // added or reworded, and the seller is left with a rejection this build can
    // count and cannot name. Asserted on source because a stale table only
    // shows up on the codes the test author thought to enumerate.
    const source = listingReviewCopy.toString();
    for (const code of [
      "PROHIBITED_PRODUCT",
      "INVALID_MEDIA",
      "INVALID_PRICE",
      "CATEGORY_MISMATCH",
      "POLICY_VIOLATION",
      "MISSING_INFORMATION"
    ]) {
      expect(source).not.toContain(code);
    }
    // And it must not reach for the code at all — the sentence is already
    // resolved, so a branch on `reason_code` could only be a second opinion.
    expect(source).not.toContain("reason_code");
  });
});
