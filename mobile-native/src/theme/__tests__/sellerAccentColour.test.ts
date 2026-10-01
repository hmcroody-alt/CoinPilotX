import { readFileSync } from "fs";
import { join } from "path";

import { insightsLight } from "../insightsLight";
import { marketplaceLight } from "../marketplaceLight";
import { STORE_CTA_PULSESOC, storeLight } from "../storeLight";

/**
 * The Store and Marketplace chrome carries PulseSoc's green, not the reference
 * design's orange.
 *
 * Two things are worth pinning here rather than trusting to review. The first is
 * the swap itself: four Store surfaces (search button, unread badge, status-strip
 * action, active tab underline) and one Marketplace surface (the FEATURED badge)
 * used a yellow token, and a later edit that reaches for `accent.orange` again
 * would restore the orange on a screen nobody re-screenshots.
 *
 * The second is the blast radius, which is the part that is genuinely easy to get
 * wrong. `insightsLight` is `{ ...storeLight }`, so `storeLight.accent` is not a
 * Store-private object — it is the accent object for nine light themes. Repainting
 * `accent.orange` green would have been the obvious one-line fix and would have
 * silently turned the Insights card links green too, which is outside what was
 * asked for. The tests below fail if anyone tries it.
 *
 * Colours are compared against `STORE_CTA_PULSESOC` rather than against hex
 * literals, so that if the brand green is ever retuned this file follows it
 * instead of becoming a second, competing definition of the brand.
 */

const GREEN = STORE_CTA_PULSESOC;

/** #FF9900, #FFA41C, #FFD814 and friends: red high, green mid, blue floor. */
const isYellowish = (hex: string): boolean => {
  const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
  if (!m) return false;
  const [r, g, b] = [m[1], m[2], m[3]].map((pair) => parseInt(pair, 16));
  return r > 180 && g > 120 && b < 100 && r >= g;
};

const isGreenish = (hex: string): boolean => {
  const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
  if (!m) return false;
  const [r, g, b] = [m[1], m[2], m[3]].map((pair) => parseInt(pair, 16));
  return g > r && g > b;
};

describe("the yellow detector the other tests rely on", () => {
  it("recognises the three colours this change removed", () => {
    expect(isYellowish("#FF9900")).toBe(true);
    expect(isYellowish("#FFA41C")).toBe(true);
    expect(isYellowish("#FFD814")).toBe(true);
  });

  it("does not mistake the brand green for yellow", () => {
    expect(isYellowish(GREEN.from)).toBe(false);
    expect(isYellowish(GREEN.to)).toBe(false);
    expect(isGreenish(GREEN.from)).toBe(true);
    expect(isGreenish(GREEN.to)).toBe(true);
  });
});

describe("the Store accent is the brand green", () => {
  it("reuses the CTA green rather than introducing a second one", () => {
    expect(storeLight.accent.brand).toBe(GREEN.from);
    expect(storeLight.accent.brandOnLight).toBe(GREEN.to);
  });

  /**
   * The underline is 2px on a white card. The mint that reads well on the navy
   * header all but disappears there, so it takes the deeper end of the same
   * gradient — the point being that it is still one green, one step darker, and
   * not a third value someone eyedropped.
   */
  it("keeps the on-white accent inside the same gradient", () => {
    expect(storeLight.accent.brandOnLight).not.toBe(storeLight.accent.brand);
    expect([GREEN.from, GREEN.to]).toContain(storeLight.accent.brandOnLight);
  });

  it("leaves no yellow in either brand accent", () => {
    expect(isYellowish(storeLight.accent.brand)).toBe(false);
    expect(isYellowish(storeLight.accent.brandOnLight)).toBe(false);
  });
});

describe("the Marketplace FEATURED badge is the brand green", () => {
  it("takes the Store accent instead of the reference yellow", () => {
    expect(marketplaceLight.badge.featuredText).toBe(storeLight.accent.brand);
    expect(isYellowish(marketplaceLight.badge.featuredText)).toBe(false);
  });

  /** Green text on the navy plate, not green on green. */
  it("still contrasts against its plate", () => {
    expect(marketplaceLight.badge.featuredBg).toBe("#131A22");
    expect(marketplaceLight.badge.featuredText).not.toBe(marketplaceLight.badge.featuredBg);
  });
});

describe("nothing outside Store and Marketplace changed colour", () => {
  /**
   * The trap. `insightsLight` spreads `storeLight`, so had the orange token been
   * repainted instead of replaced, this screen would have gone green too.
   */
  it("leaves the Insights card link on its own orange", () => {
    expect(insightsLight.accent.orange).toBe("#FF9900");
    expect(isYellowish(insightsLight.accent.orange)).toBe(true);
  });

  /** Ratings are gold everywhere, and Insights reads this one for its rank marker. */
  it("leaves review stars gold", () => {
    expect(storeLight.accent.star).toBe("#FFA41C");
    expect(insightsLight.rankGold).toBe(storeLight.accent.star);
    expect(insightsLight.ring.warn).toBe(storeLight.accent.star);
  });

  it("leaves the semantic status colours alone", () => {
    expect(storeLight.status.success).toBe("#067D62");
    expect(storeLight.status.warning).toBe("#C7511F");
    expect(storeLight.status.error).toBe("#B12704");
  });
});

/**
 * Source pins. The token values above can be correct while a component still
 * points at the old token, and nothing in a render test of these components would
 * say so — an orange search button renders perfectly.
 */
describe("the Store components read the brand accent", () => {
  const COMPONENTS = join(__dirname, "..", "..", "components");
  const read = (...parts: string[]) => readFileSync(join(...parts), "utf8");
  const flat = (source: string) => source.replace(/\s+/g, " ");

  const header = read(COMPONENTS, "store", "StoreHeader.tsx");
  const tabBar = read(COMPONENTS, "store", "StoreTabBar.tsx");
  const grid = read(COMPONENTS, "marketplace", "ItemGridCard.tsx");

  it("finds the styles it is guarding", () => {
    expect(header).toContain("searchButton:");
    expect(header).toContain("stripAction:");
    expect(header).toContain("badge:");
    expect(tabBar).toContain("underline:");
    expect(grid).toContain("badgeTextFeatured:");
  });

  it("paints the search button, the unread badge and the strip action green", () => {
    const styles = flat(header.slice(header.indexOf("const styles")));
    expect(styles).toMatch(/badge: \{[^}]*backgroundColor: storeLight\.accent\.brand/);
    expect(styles).toMatch(/searchButton: \{[^}]*backgroundColor: storeLight\.accent\.brand/);
    expect(styles).toMatch(/stripAction: \{[^}]*color: storeLight\.accent\.brand/);
  });

  it("paints the active tab underline green", () => {
    const styles = flat(tabBar.slice(tabBar.indexOf("const styles")));
    expect(styles).toMatch(/underline: \{[^}]*backgroundColor: storeLight\.accent\.brandOnLight/);
  });

  it("draws FEATURED from the token rather than a literal", () => {
    expect(grid).toContain("color: marketplaceLight.badge.featuredText");
  });

  /**
   * The regression this whole file exists to prevent: `accent.orange` is still a
   * live token, one autocomplete away, and it is what every one of these styles
   * used to say.
   */
  it("no Store or Marketplace component reaches for the orange again", () => {
    for (const source of [header, tabBar, grid]) {
      expect(source).not.toContain("accent.orange");
      expect(source).not.toMatch(/#FF9900|#FFD814/i);
    }
  });
});
