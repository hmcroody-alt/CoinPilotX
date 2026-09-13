import { storeFixTarget } from "../storeFixTarget";

/**
 * The server's whole section vocabulary, copied from
 * `services/business_os/marketplace/listing_readiness.SECTIONS`.
 *
 * A literal list rather than a derived one, because the point is to notice when
 * the two sides diverge. If the engine grows a seventh section, the fallback
 * below keeps the tap working, but nobody would ever find out that a new kind of
 * blocker is silently landing at the top of the form — so this list is the
 * reminder to come and route it.
 */
const SERVER_SECTIONS = ["details", "media", "pricing", "policies", "inventory", "overview"];

describe("storeFixTarget", () => {
  it("routes every section the engine can emit", () => {
    expect(SERVER_SECTIONS.map(storeFixTarget)).toEqual([
      "details",
      "camera",
      "price",
      "policy",
      "quantity",
      "details"
    ]);
  });

  it("sends an unknown section to the top of the form rather than nowhere", () => {
    // What happens the day the engine adds "shipping". The row still does
    // something, and it still carries the server's own label for what to do.
    expect(storeFixTarget("shipping")).toBe("details");
    expect(storeFixTarget("")).toBe("details");
  });

  it("keeps the two inventory problems on the same field", () => {
    // "Restock" and "Set stock count" are different sentences with different
    // causes -- and the same box fixes both. The distinction lives in the label
    // the server wrote, not in where the tap lands.
    expect(storeFixTarget("inventory")).toBe("quantity");
  });
});
