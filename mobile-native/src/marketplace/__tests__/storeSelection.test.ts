/**
 * Selection mode's correctness is not about pixels. Every dangerous question a
 * bulk edit can ask — "which rows did Select All take?", "what happened to the
 * six I picked on the other tab?", "what does this button actually do to the
 * four that aren't ready?" — is a question about a set of ids, and this file is
 * where those answers are pinned.
 *
 * The four properties under test are the four decisions `storeSelection.ts`
 * documents, stated as failures rather than features:
 *
 *   1. Select All must not select rows the seller cannot see. Taking 200
 *      listings because 6 were on screen is the worst thing this feature can do.
 *   2. Changing filter must not silently discard a selection — but the part
 *      that scrolled off screen must be *named*, not hidden.
 *   3. A reload must drop ids that no longer exist, or a bulk action posts an
 *      id whose failure is reported against a row nobody can see.
 *   4. A row the server has not ruled on is NOT eligible to publish. "The
 *      payload never said" is not "nothing is wrong".
 *
 * Eligibility itself is no longer decided here and the tests below are written
 * to keep it that way: they set `bulkEligibility` — what the server said — and
 * assert it is obeyed, including in the cases where obeying it contradicts
 * every other field on the row. A test that built eligibility out of
 * `readiness` would be re-stating the rule it is supposed to be guarding.
 */
import type { StoreListingRow } from "../../api/storeDashboard";
import {
  EMPTY_SELECTION,
  bulkActionLabel,
  partition,
  reconcile,
  selectAllLabel,
  selectAllState,
  selectedRows,
  selectionSummary,
  toggle,
  toggleAll
} from "../storeSelection";

function row(id: number, over: Partial<StoreListingRow> = {}): StoreListingRow {
  return {
    id,
    title: `Listing ${id}`,
    thumbnailUrl: null,
    priceLabel: "$10.00",
    currency: "USD",
    quantity: 4,
    health: "in_stock",
    readiness: {
      publishable: true,
      resubmittable: false,
      checkout_ready: true,
      blockers: [],
      warnings: [],
      summary: "Ready to publish",
      fixes: [],
      notes: []
    },
    // No review decision to report. `null` rather than a synthesised verdict,
    // because that is what the builder writes for a payload that carried none
    // and these rows are all healthy and live.
    review: null,
    bulkEligibility: { publish: null, hide: null },
    unitsSold7d: 0,
    category: "Home",
    subcategory: "",
    rating: null,
    reviewCount: null,
    ...over
  };
}

/** Shorthand: the ids in a selection, sorted, so assertions read as sets. */
function ids(selection: ReadonlySet<number>): number[] {
  return [...selection].sort((a, b) => a - b);
}

describe("toggle", () => {
  it("adds a row that was not selected and removes one that was", () => {
    const once = toggle(EMPTY_SELECTION, 7);
    expect(ids(once)).toEqual([7]);
    expect(ids(toggle(once, 7))).toEqual([]);
  });

  it("does not mutate the selection it was given", () => {
    const before = toggle(EMPTY_SELECTION, 1);
    toggle(before, 2);
    expect(ids(before)).toEqual([1]);
  });
});

describe("Select All takes what is on screen, and nothing else", () => {
  const all = [row(1), row(2), row(3), row(4), row(5), row(6)];
  const visible = [row(1), row(2)];

  it("selects only the visible rows, not the whole catalogue", () => {
    // The headline failure this module exists to prevent: a seller filters to
    // two rows, taps Select All, and a bulk delete takes six.
    const selection = toggleAll(EMPTY_SELECTION, visible);
    expect(ids(selection)).toEqual([1, 2]);
    expect(selectedRows(selection, all).map((r) => r.id)).toEqual([1, 2]);
  });

  it("says how many and of what, so 'All' cannot be misread", () => {
    expect(selectAllLabel(EMPTY_SELECTION, visible)).toBe("Select all 2 shown");
  });

  it("deselects on the second tap, and says so", () => {
    const selection = toggleAll(EMPTY_SELECTION, visible);
    expect(selectAllLabel(selection, visible)).toBe("Deselect all 2 shown");
    expect(ids(toggleAll(selection, visible))).toEqual([]);
  });

  it("deselecting the visible rows leaves rows gathered elsewhere alone", () => {
    // Tapping Select All twice is how a seller undoes a mistaken tap. It must
    // undo *that tap*, not empty a selection built up across tabs.
    const fromAnotherTab = new Set([5, 6]);
    const selection = toggleAll(fromAnotherTab, visible);
    expect(ids(selection)).toEqual([1, 2, 5, 6]);
    expect(ids(toggleAll(selection, visible))).toEqual([5, 6]);
  });
});

describe("selectAllState", () => {
  const visible = [row(1), row(2), row(3)];

  it("is 'none' with nothing selected", () => {
    expect(selectAllState(EMPTY_SELECTION, visible)).toBe("none");
  });

  it("is 'all' only when every visible row is in the selection", () => {
    expect(selectAllState(new Set([1, 2, 3]), visible)).toBe("all");
  });

  it("is 'some' for a partial selection, so the control can render a dash", () => {
    // A tick shown for 1-of-3 is a lie told in one glyph, and it is the state a
    // seller is most likely to act on without re-reading the count.
    expect(selectAllState(new Set([2]), visible)).toBe("some");
  });

  it("is 'none' when the selection is entirely off-screen", () => {
    expect(selectAllState(new Set([9]), visible)).toBe("none");
  });

  it("is 'none' for an empty list rather than vacuously 'all'", () => {
    // `[].every(...)` is true. Reporting "all selected" for an empty filter
    // would light the control up over nothing.
    expect(selectAllState(new Set([1]), [])).toBe("none");
    expect(selectAllState(EMPTY_SELECTION, [])).toBe("none");
  });
});

describe("a selection survives a filter change, but says where it went", () => {
  const all = [row(1), row(2), row(3), row(4)];

  it("keeps ids that scrolled out of view", () => {
    const selection = new Set([1, 2, 3]);
    expect(selectedRows(selection, all).map((r) => r.id)).toEqual([1, 2, 3]);
  });

  it("names the off-screen count instead of letting it lurk", () => {
    // The seller is looking at one row and about to act on three. The summary is
    // the only thing standing between them and a surprise.
    expect(selectionSummary(new Set([1, 2, 3]), all, [row(1)])).toBe("3 selected · 2 not shown");
  });

  it("omits the suffix when everything selected is visible", () => {
    expect(selectionSummary(new Set([1, 2]), all, [row(1), row(2)])).toBe("2 selected");
  });

  it("returns null rather than '0 selected'", () => {
    expect(selectionSummary(EMPTY_SELECTION, all, all)).toBeNull();
  });

  it("counts only ids that still exist in the list", () => {
    // A stale id must not inflate the number the seller reads before confirming.
    expect(selectionSummary(new Set([1, 99]), all, [row(1)])).toBe("1 selected");
  });
});

describe("reconcile", () => {
  const all = [row(1), row(2), row(3)];

  it("drops an id that vanished from the payload", () => {
    // Deleted on another device, or filtered out of the response entirely. Left
    // in, it becomes a bulk-action failure reported against a row the seller
    // cannot see.
    expect(ids(reconcile(new Set([1, 3, 42]), all))).toEqual([1, 3]);
  });

  it("empties a selection when the list comes back empty", () => {
    expect(ids(reconcile(new Set([1, 2]), []))).toEqual([]);
  });

  it("returns the identical reference when nothing changed", () => {
    // Not just an equal set: `useMemo` and `===` downstream depend on this, and
    // a fresh Set on every refresh re-runs the partition on every poll.
    const selection = new Set([1, 2]);
    expect(reconcile(selection, all)).toBe(selection);
  });

  it("returns the identical reference for an empty selection", () => {
    expect(reconcile(EMPTY_SELECTION, all)).toBe(EMPTY_SELECTION);
  });
});

describe("selectedRows", () => {
  it("returns rows in the list's order, not the order they were tapped", () => {
    const all = [row(1), row(2), row(3)];
    const tappedBackwards = new Set([3, 1]);
    expect(selectedRows(tappedBackwards, all).map((r) => r.id)).toEqual([1, 3]);
  });
});

describe("partition — what will happen, before it happens", () => {
  const ready = row(1);
  const blocked = row(2, {
    bulkEligibility: {
      publish: { code: "NOT_READY", reason: "1 thing left", blockers: ["MISSING_PRICE"] },
      hide: null
    }
  });

  it("splits a selection into eligible and blocked", () => {
    const { eligible, blocked: out } = partition([ready, blocked], "publish");
    expect(eligible.map((r) => r.id)).toEqual([1]);
    expect(out.map((b) => b.row.id)).toEqual([2]);
  });

  it("refuses a row the server said nothing about", () => {
    // THE rule. No `bulkEligibility` means the payload never said, and a bulk
    // publish that reads "never said" as "fine" is how you publish a listing
    // with no price. Absence is not a clean bill of health.
    const unknown = row(4, { bulkEligibility: null });
    const { eligible, blocked: out } = partition([unknown], "publish");
    expect(eligible).toEqual([]);
    expect(out[0].reason).toBe("No readiness check yet");
  });

  it("refuses a row the server ruled on for the other action only", () => {
    // A payload carrying `hide` but not `publish` has still not answered the
    // question being asked. Reading the sibling action's answer, or reading the
    // object's mere presence as a yes, both publish something unexamined.
    const halfAnswered = row(11, { bulkEligibility: { hide: null } });
    expect(partition([halfAnswered], "publish").blocked[0].reason).toBe("No readiness check yet");
  });

  it("repeats the server's sentence rather than composing one", () => {
    // The seller reads this string. It is written once, on the server, by the
    // same function that will refuse the row later — so the reason shown before
    // the tap and the reason returned after it cannot disagree.
    const odd = row(12, {
      bulkEligibility: { publish: { code: "ALREADY_SUBMITTED", reason: "Already in review" }, hide: null }
    });
    expect(partition([odd], "publish").blocked[0].reason).toBe("Already in review");
  });

  it("blocks a finished listing that is already live", () => {
    // The case a client-side derivation cannot see. Nothing is wrong with this
    // product — it is priced, stocked, photographed and `publishable` — and
    // publishing it again would pull it off the storefront and back into the
    // review queue. Only the server knows what state it is being published
    // *from*.
    const live = row(13, {
      readiness: {
        publishable: true,
        resubmittable: false,
        checkout_ready: true,
        blockers: [],
        warnings: [],
        summary: "Ready to publish",
        fixes: [],
        notes: []
      },
      bulkEligibility: {
        publish: { code: "ALREADY_PUBLISHED", reason: "Already published" },
        hide: null
      }
    });
    expect(partition([live], "publish").eligible).toEqual([]);
    expect(partition([live], "publish").blocked[0].reason).toBe("Already published");
  });

  it("obeys the server against every other field on the row", () => {
    // No price, no stock, unknown health, and a readiness verdict that says no
    // — and the server says this one publishes. The client does not get a
    // second opinion, because a second opinion is a second implementation.
    const contradictory = row(6, {
      priceLabel: "",
      quantity: null,
      health: "unknown_stock",
      readiness: {
        publishable: false,
        resubmittable: false,
        checkout_ready: false,
        blockers: ["MISSING_PRICE"],
        warnings: [],
        summary: "1 thing left",
        fixes: [{ code: "MISSING_PRICE", label: "Add price", section: "pricing" }],
        notes: []
      },
      bulkEligibility: { publish: null, hide: null }
    });
    expect(partition([contradictory], "publish").eligible.map((r) => r.id)).toEqual([6]);
  });

  describe("hide", () => {
    it("blocks only rows the server says are already hidden", () => {
      const already = row(8, {
        health: "hidden",
        bulkEligibility: { publish: null, hide: { code: "ALREADY_HIDDEN", reason: "Already hidden" } }
      });
      const { eligible, blocked: out } = partition([row(9), already], "hide");
      expect(eligible.map((r) => r.id)).toEqual([9]);
      expect(out[0].reason).toBe("Already hidden");
    });

    it("does not block a row the publish path would have blocked", () => {
      // Hiding removes a listing from buyers. A seller who cannot publish a
      // broken listing must still be able to pull it.
      const notPublishable = row(10, {
        bulkEligibility: {
          publish: { code: "NOT_READY", reason: "1 thing left", blockers: ["MISSING_PRICE"] },
          hide: null
        }
      });
      expect(partition([notPublishable], "hide").blocked).toEqual([]);
    });
  });
});

describe("bulkActionLabel — the sentence on the confirm button", () => {
  const ready = [row(1), row(2)];
  const blocked = row(3, {
    bulkEligibility: {
      publish: { code: "NOT_READY", reason: "1 thing left", blockers: ["MISSING_PRICE"] },
      hide: null
    }
  });

  it("states the blocked count alongside what will happen", () => {
    // "Publish 3" that publishes 2 is the failure this replaces.
    expect(bulkActionLabel(partition([...ready, blocked], "publish"), "publish")).toBe("Publish 2 · 1 blocked");
  });

  it("says just the count when nothing is blocked", () => {
    expect(bulkActionLabel(partition(ready, "publish"), "publish")).toBe("Publish 2");
  });

  it("says there is nothing to do rather than offering 'Publish 0'", () => {
    expect(bulkActionLabel(partition([blocked], "publish"), "publish")).toBe("Nothing to publish");
    expect(bulkActionLabel(partition([], "publish"), "publish")).toBe("Nothing to publish");
  });

  it("uses the verb of the action it was given", () => {
    const already = row(4, {
      health: "hidden",
      bulkEligibility: { publish: null, hide: { code: "ALREADY_HIDDEN", reason: "Already hidden" } }
    });
    expect(bulkActionLabel(partition([row(5), already], "hide"), "hide")).toBe("Hide 1 · 1 blocked");
    expect(bulkActionLabel(partition([already], "hide"), "hide")).toBe("Nothing to hide");
  });
});
