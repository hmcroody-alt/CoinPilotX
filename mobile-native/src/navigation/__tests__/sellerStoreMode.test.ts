import { BUSINESS_OS_SECTIONS } from "../../api/businessOs";
import { sellerStoreHeading, sellerStorePanels, sellerStoreShowsPanel } from "../sellerStoreMode";

describe("sellerStorePanels", () => {
  it("keeps the original everything-at-once view for overview and for no mode", () => {
    expect(sellerStorePanels("overview")).toEqual(sellerStorePanels(undefined));
    expect(sellerStorePanels("overview").length).toBeGreaterThan(5);
  });

  it("falls back to the full view for an unrecognised mode rather than rendering nothing", () => {
    expect(sellerStorePanels("not-a-mode")).toEqual(sellerStorePanels("overview"));
  });

  /**
   * `product` is deliberately absent from this list. Every other mode is a
   * section of the store and needs the hero to orient the merchant; `product` is
   * one listing's editor reached from a row they just tapped, and the hero is
   * exactly what made Edit look like it had opened the wrong screen.
   */
  it("always renders the hero so the screen is never empty", () => {
    ["overview", "dashboard", "apply", "profile", "create", "payouts", "orders"].forEach((mode) => {
      expect(sellerStorePanels(mode)).toContain("hero");
    });
  });

  /**
   * The Edit-routing fix, stated as a contract.
   *
   * `create` already contained the editor, which is why Edit did open the right
   * product — but under the Storefront hero, the Listing management card and the
   * whole listing table, beneath a heading reading "Listings". The merchant read
   * that as the wrong screen, and they were right to. So the requirement is not
   * "contains inventory", it is "contains nothing else".
   */
  it("gives the single-product editor the inventory panel and nothing else", () => {
    expect(sellerStorePanels("product")).toEqual(["inventory"]);
    expect(sellerStoreShowsPanel("product", "hero")).toBe(false);
    expect(sellerStoreShowsPanel("product", "listings")).toBe(false);
    expect(sellerStoreShowsPanel("product", "media")).toBe(false);
  });

  it("gives Store, Orders and Payments genuinely different panel sets", () => {
    const store = sellerStorePanels("dashboard");
    const orders = sellerStorePanels("orders");
    const payments = sellerStorePanels("payouts");
    expect(store).not.toEqual(orders);
    expect(orders).not.toEqual(payments);
    expect(store).not.toEqual(payments);
  });

  it("shows orders in the orders and payouts modes only", () => {
    expect(sellerStoreShowsPanel("orders", "orders")).toBe(true);
    expect(sellerStoreShowsPanel("payouts", "orders")).toBe(true);
    expect(sellerStoreShowsPanel("dashboard", "orders")).toBe(false);
    expect(sellerStoreShowsPanel("apply", "orders")).toBe(false);
  });

  it("shows inventory editing only where the owner manages the store", () => {
    expect(sellerStoreShowsPanel("dashboard", "inventory")).toBe(true);
    expect(sellerStoreShowsPanel("orders", "inventory")).toBe(false);
    expect(sellerStoreShowsPanel("payouts", "inventory")).toBe(false);
  });
});

describe("sellerStoreHeading", () => {
  it("titles each mode distinctly", () => {
    const titles = ["dashboard", "orders", "payouts", "profile"].map((mode) => sellerStoreHeading(mode).title);
    expect(new Set(titles).size).toBe(titles.length);
  });

  it("falls back to the original heading for an unknown mode", () => {
    expect(sellerStoreHeading("not-a-mode")).toEqual(sellerStoreHeading("overview"));
  });

  /**
   * The header is the merchant's only proof that Edit opened the row they
   * tapped, so in product mode it has to be the product's own name.
   */
  it("titles the single-product editor with the product", () => {
    expect(sellerStoreHeading("product", "Silver Necklace").title).toBe("Silver Necklace");
    expect(sellerStoreHeading("product", "  Cargo Pants  ").title).toBe("Cargo Pants");
  });

  it("falls back to a generic product title rather than an empty header", () => {
    // A listing saved without a name, or a deep link carrying an id and no
    // title, must not render a blank heading.
    expect(sellerStoreHeading("product", "   ").title).toBe("Edit product");
    expect(sellerStoreHeading("product").title).toBe("Edit product");
  });

  it("does not let a title param rewrite any other mode's heading", () => {
    // Only the single-product editor is about one product. `create` passing a
    // title must not retitle the Listings hub after whatever was tapped last.
    expect(sellerStoreHeading("create", "Silver Necklace").title).toBe(sellerStoreHeading("create").title);
    expect(sellerStoreHeading("dashboard", "Silver Necklace").title).toBe(sellerStoreHeading("dashboard").title);
  });
});

describe("Business OS integration", () => {
  it("supports every SellerStore mode Business OS links to", () => {
    BUSINESS_OS_SECTIONS.filter((section) => section.route === "SellerStore").forEach((section) => {
      const mode = String(section.params?.mode || "");
      expect(mode).not.toBe("");
      // A mode Business OS uses must be a real mode, not silently falling back
      // to the full overview — otherwise four links render one identical screen.
      expect(sellerStorePanels(mode)).not.toEqual(sellerStorePanels("overview"));
    });
  });
});
