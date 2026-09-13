/**
 * The decisions the category module makes on the seller's behalf.
 *
 * Its sibling `storeBulkPricing.test.ts` has arithmetic to check. This module
 * has none — a category is a name — so what is worth testing is narrower and
 * easier to get wrong by accident:
 *
 * - **What counts as a request.** A parent is required and a child never is, and
 *   "no child" has to be a decision the module makes rather than a key it forgets
 *   to send.
 * - **That the normalisation is the server's.** The module trims and collapses
 *   because `isSameAttempt` keys an idempotency token off the result. Trimming
 *   *differently* from the server is worse than not trimming: it mints a second
 *   key for text the server would have called identical.
 * - **That the suggestions are stable.** Chips that reorder between renders of an
 *   unchanged store are a moving tap target.
 */

import {
  CATEGORY_MAX,
  EMPTY_CATEGORY_DRAFT,
  alreadyThere,
  categoriesInUse,
  parseCategoryTarget
} from "../storeBulkCategory";

describe("parseCategoryTarget", () => {
  it("refuses an empty draft with a reason, not a silent null", () => {
    const parsed = parseCategoryTarget(EMPTY_CATEGORY_DRAFT);

    expect(parsed.target).toBeNull();
    expect(parsed.error).toBe("Choose a category.");
  });

  it("refuses whitespace, which is what a tapped-then-cleared field holds", () => {
    expect(parseCategoryTarget({ category: "   ", subcategory: "" }).target).toBeNull();
  });

  it("refuses a child with no parent", () => {
    // "Lighting" under nothing is not a filing. The server refuses this too;
    // refusing it here is what keeps the button from being tappable.
    const parsed = parseCategoryTarget({ category: "", subcategory: "Lighting" });

    expect(parsed.target).toBeNull();
    expect(parsed.error).toBe("Choose a category.");
  });

  it("accepts a bare parent and sends the cleared child explicitly", () => {
    // The empty string is the payload, not an omission. An absent key would let
    // the server read "leave the subcategory alone", and a move that keeps
    // "Crypto Basics" under "Home & Kitchen" is the incoherent pair.
    const parsed = parseCategoryTarget({ category: "Home & Kitchen", subcategory: "" });

    expect(parsed.target).toEqual({ category: "Home & Kitchen", subcategory: "" });
    expect(parsed.error).toBeNull();
    expect(Object.keys(parsed.target!)).toContain("subcategory");
  });

  it("trims and collapses both halves the way the server will", () => {
    const parsed = parseCategoryTarget({
      category: "  Home   &   Kitchen  ",
      subcategory: "\tLighting \n"
    });

    expect(parsed.target).toEqual({ category: "Home & Kitchen", subcategory: "Lighting" });
  });

  it("produces one target for two spellings of the same intention", () => {
    // The whole reason normalisation lives here as well as on the server: a
    // seller who previews, goes back, retypes with a trailing space and applies
    // is doing one piece of work, and two targets would be two idempotency keys.
    const a = parseCategoryTarget({ category: "Home & Kitchen", subcategory: "Lighting" });
    const b = parseCategoryTarget({ category: " Home  & Kitchen ", subcategory: "Lighting " });

    expect(a.target).toEqual(b.target);
  });

  it("caps at the server's limit rather than sending something it will cut", () => {
    const long = "A".repeat(CATEGORY_MAX + 40);
    const parsed = parseCategoryTarget({ category: long, subcategory: long });

    expect(parsed.target!.category).toHaveLength(CATEGORY_MAX);
    expect(parsed.target!.subcategory).toHaveLength(CATEGORY_MAX);
  });

  it("mirrors the server's cap, so the two do not disagree about a long name", () => {
    // Pinned against the Python constant. If `CATEGORY_MAX` drifts, a seller
    // previews one name and the server stores a shorter one.
    expect(CATEGORY_MAX).toBe(80);
  });
});

describe("categoriesInUse", () => {
  it("offers nothing when the store has no filed products", () => {
    expect(categoriesInUse([])).toEqual([]);
    expect(categoriesInUse([{ category: "" }, { category: "   " }])).toEqual([]);
  });

  it("puts the heaviest aisle first, because that is the likely destination", () => {
    const rows = [
      { category: "Education" },
      { category: "Home & Kitchen" },
      { category: "Home & Kitchen" },
      { category: "Home & Kitchen" },
      { category: "Toys" },
      { category: "Toys" }
    ];

    expect(categoriesInUse(rows)).toEqual(["Home & Kitchen", "Toys", "Education"]);
  });

  it("breaks ties alphabetically, so an even store does not reshuffle", () => {
    // Without the tie-break the order is Map insertion order, which follows
    // whatever the last fetch happened to return. Chips would move under the
    // seller's thumb between refreshes of a store nothing changed in.
    expect(categoriesInUse([{ category: "Toys" }, { category: "Education" }])).toEqual([
      "Education",
      "Toys"
    ]);
    expect(categoriesInUse([{ category: "Education" }, { category: "Toys" }])).toEqual([
      "Education",
      "Toys"
    ]);
  });

  it("counts two spellings of one aisle as one aisle", () => {
    // The failure free text actually has: "Home & Kitchen" beside
    // "Home  & Kitchen ". Two chips for one aisle teaches the seller the store
    // has two, and the next product gets filed in the wrong one.
    const rows = [
      { category: "Home & Kitchen" },
      { category: " Home  & Kitchen " },
      { category: "Toys" },
      { category: "Toys" },
      { category: "Toys" }
    ];

    expect(categoriesInUse(rows)).toEqual(["Toys", "Home & Kitchen"]);
  });

  it("offers parents only, never a child that would pair with the wrong one", () => {
    // The argument type is `{ category }`, so a subcategory cannot leak in even
    // by accident — asserted so that widening the type is a decision somebody
    // makes on purpose.
    const rows = [{ category: "Education", subcategory: "Crypto Basics" }];

    expect(categoriesInUse(rows)).toEqual(["Education"]);
  });
});

describe("alreadyThere", () => {
  const target = { category: "Home & Kitchen", subcategory: "Lighting" };

  it("counts a row filed at exactly that pair", () => {
    const rows = [
      { category: "Home & Kitchen", subcategory: "Lighting" },
      { category: "Education", subcategory: "Crypto Basics" }
    ];

    expect(alreadyThere(rows, target)).toBe(1);
  });

  it("does not count the same parent with a different child", () => {
    // This one is a real change: the move rewrites the child. Counting it as
    // settled would promise the seller nothing happens to a row that moves.
    expect(alreadyThere([{ category: "Home & Kitchen", subcategory: "Cookware" }], target)).toBe(0);
  });

  it("does not count the same parent with no child", () => {
    expect(alreadyThere([{ category: "Home & Kitchen", subcategory: "" }], target)).toBe(0);
  });

  it("counts a cleared child against a cleared target", () => {
    const bare = { category: "Home & Kitchen", subcategory: "" };

    expect(alreadyThere([{ category: "Home & Kitchen", subcategory: "" }], bare)).toBe(1);
    expect(alreadyThere([{ category: "Home & Kitchen", subcategory: "Lighting" }], bare)).toBe(0);
  });

  it("sees through whitespace already sitting in the column", () => {
    // Stored categories are not guaranteed clean — a CJ feed writes them and the
    // single-listing editor predates the trimming here. Comparing raw would tell
    // the seller nothing is settled, then the server would block every row, and
    // a screenful of "Already in that category" reads as a malfunction.
    const rows = [{ category: " Home  & Kitchen ", subcategory: "Lighting " }];

    expect(alreadyThere(rows, target)).toBe(1);
  });

  it("survives rows the API left null", () => {
    const rows = [{ category: null, subcategory: null }] as unknown as {
      category: string;
      subcategory: string;
    }[];

    expect(alreadyThere(rows, target)).toBe(0);
    expect(alreadyThere(rows, { category: "", subcategory: "" })).toBe(1);
  });
});
