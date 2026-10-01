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

  it("always renders the hero so the screen is never empty", () => {
    ["overview", "dashboard", "apply", "profile", "create", "payouts", "orders"].forEach((mode) => {
      expect(sellerStorePanels(mode)).toContain("hero");
    });
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
