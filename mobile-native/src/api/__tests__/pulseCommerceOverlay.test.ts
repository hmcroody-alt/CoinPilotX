/**
 * The PulseDrop commerce overlay: the client half of a two-implementation rule.
 *
 * Three separate things are pinned here, and they fail for three different
 * reasons.
 *
 * **1. Parity with the marketplace.** `commerceBlock` prefers the server's
 * availability code and falls back to running `marketplacePurchaseBlock` over
 * the four fields the overlay ships for exactly that purpose. Both sides are
 * given identical inputs, so a disagreement is a bug in one of them rather than
 * an invisible difference of opinion about the same listing on two screens in
 * the same session. That parity is asserted below rather than described in a
 * comment, which is what `pulseCommerceOverlay.ts`'s own docblock promises.
 *
 * **2. The i18n keys the server emits actually resolve.** This is the important
 * one, because it is the failure nothing else catches. `extended` is a catalog
 * *tier filename*; the namespaces are the blocks inside it. A key written
 * `extended:pulsedrop.label.trending` does not throw and does not register as
 * missing coverage — `parseKey` keeps the whole string as a path inside
 * `common`, the lookup misses, and `humanizeKey` hands back "Trending". So the
 * card renders confident title-cased English in Arabic, Japanese and Korean,
 * `npm run i18n:validate` reports 100%, and a reviewer who reads English sees
 * nothing wrong. This suite reads the key strings **out of the Python source**
 * rather than restating them, so a server that adds a sixth editorial label or
 * renames a state turns this file red instead of shipping a humanized string to
 * eleven locales.
 *
 * **3. A cached overlay makes no claim it cannot stand behind.** The reels feed
 * writes whole `PulseReel` objects to AsyncStorage. Pixels and captions cache
 * correctly; price, stock and availability do not. The assertions below are
 * about what `commerceOverlayForCache` *removes*.
 */
import fs from "node:fs";
import path from "node:path";
import {
  commerceBlock,
  commerceCtaEnabled,
  commerceOverlayForCache,
  commerceOverlayIsStale,
  commercePriceVisible,
  isPulseCommerceOverlay,
  type PulseCommerceOverlay
} from "../pulseCommerceOverlay";
import { marketplacePurchaseBlock } from "../marketplaceBuyerPresentation";
import { activateLocale, hasTranslation, resetCatalogCache } from "../../i18n/engine";

/** The repo root, from `mobile-native/src/api/__tests__`. */
const REPO_ROOT = path.resolve(__dirname, "..", "..", "..", "..");

function overlay(patch: Partial<PulseCommerceOverlay> = {}): PulseCommerceOverlay {
  return {
    pulsedrop: true,
    surface: "reel",
    publication_id: 1,
    attribution: {
      token: "pd1.1.reel.77",
      publisher_role: "publisher",
      merchant_role: "merchant",
      listing_id: 77,
      seller_user_id: 10,
      surface: "reel"
    },
    product: {
      listing_id: 77,
      title: "Aurora Desk Lamp",
      price_label: "$49.00",
      currency: "USD",
      image_url: "https://cdn.example/lamp.jpg",
      buyer_visible: true,
      inventory_state: "in_stock",
      quantity: 4,
      product_type: "physical",
      denial_code: ""
    },
    seller: {
      seller_user_id: 10,
      store_name: "Northlight Studio",
      username: "northlight",
      route: "/pulse/merchant/10",
      screen: "MerchantProfile"
    },
    label: { key: "TRENDING", i18n_key: "commerce:pulsedrop.label.trending", fallback: "Trending" },
    cta: {
      code: "VIEW_PRODUCT",
      i18n_key: "commerce:productSignal.viewProduct",
      fallback: "View product",
      enabled: true,
      route: "/pulse/marketplace/77",
      screen: "MarketplaceDetail",
      url: "https://pulsesoc.com/pulse/marketplace/77"
    },
    availability: { code: "", i18n_key: "", fallback: "", purchasable: true },
    accessibility_text: "Aurora Desk Lamp, $49.00, from Northlight Studio",
    ...patch
  };
}

describe("isPulseCommerceOverlay", () => {
  it("accepts a real overlay and rejects everything that merely looks like one", () => {
    expect(isPulseCommerceOverlay(overlay())).toBe(true);
    expect(isPulseCommerceOverlay(null)).toBe(false);
    expect(isPulseCommerceOverlay(undefined)).toBe(false);
    expect(isPulseCommerceOverlay({})).toBe(false);
    // The discriminator alone is not enough: a card with no product has nothing
    // to render and nowhere to navigate.
    expect(isPulseCommerceOverlay({ pulsedrop: true, publication_id: 1 })).toBe(false);
  });
});

describe("commerceBlock", () => {
  it("takes the server's verdict when the server has one", () => {
    expect(commerceBlock(overlay())).toBe("");
    for (const code of ["OUT_OF_STOCK", "UNAVAILABLE", "NOT_PRICED", "REMOVED"] as const) {
      const o = overlay();
      o.availability = { ...o.availability, code };
      expect(commerceBlock(o)).toBe(code);
    }
  });

  it("agrees with the marketplace's own helper on identical inputs", () => {
    // Same four fields, two implementations. Strip the server's answer so the
    // client is forced to derive one, then hold the derivation against the
    // shared helper a product screen would have used for the same listing.
    const cases: Array<Partial<PulseCommerceOverlay["product"]>> = [
      { buyer_visible: true, inventory_state: "in_stock", quantity: 4, product_type: "physical" },
      { buyer_visible: true, inventory_state: "out_of_stock", quantity: 0, product_type: "physical" },
      { buyer_visible: false, inventory_state: "in_stock", quantity: 4, product_type: "physical" },
      { buyer_visible: true, inventory_state: "in_stock", quantity: 0, product_type: "digital" },
      { buyer_visible: true, inventory_state: "", quantity: 1, product_type: "" }
    ];
    for (const product of cases) {
      const o = overlay();
      o.product = { ...o.product, ...product };
      // An unrecognised code is what an older or newer server looks like.
      o.availability = { ...o.availability, code: "SOMETHING_ELSE" as never };
      const expected = marketplacePurchaseBlock({
        id: o.product.listing_id,
        title: o.product.title,
        price_label: o.product.price_label,
        currency: o.product.currency,
        quantity: o.product.quantity,
        product_type: o.product.product_type,
        listing_type: o.product.product_type,
        inventory_state: o.product.inventory_state,
        buyer_visible: o.product.buyer_visible
      } as never);
      expect(commerceBlock(o)).toBe(expected);
    }
  });

  it("never reads an unknown state as available", () => {
    // Defaulting an unrecognised shape to "" is how a withdrawn product keeps an
    // enabled Buy button through a server upgrade.
    const o = overlay();
    o.availability = { ...o.availability, code: "WHO_KNOWS" as never };
    o.product = { ...o.product, buyer_visible: false };
    expect(commerceBlock(o)).not.toBe("");
  });
});

describe("commerceCtaEnabled", () => {
  it("requires the flag, a route, and an available product — all three", () => {
    expect(commerceCtaEnabled(overlay())).toBe(true);

    const noFlag = overlay();
    noFlag.cta = { ...noFlag.cta, enabled: false };
    expect(commerceCtaEnabled(noFlag)).toBe(false);

    // `/pulse/marketplace/<id>` 404s for anything not public, so a routeless CTA
    // that is somehow still enabled must not become a tap to an error screen.
    const noRoute = overlay();
    noRoute.cta = { ...noRoute.cta, route: "" };
    expect(commerceCtaEnabled(noRoute)).toBe(false);

    const gone = overlay();
    gone.availability = { ...gone.availability, code: "REMOVED" };
    expect(commerceCtaEnabled(gone)).toBe(false);
  });
});

describe("commercePriceVisible", () => {
  /**
   * The line is *who took the product down*, mirroring `_DISCLOSED` in
   * `services/pulsedrop/hydration.py`. Sold out and not-priced are the seller
   * still offering the listing. Withdrawn and removed were taken off sale, and
   * advertising a price for them under a verified badge would make PulseDrop the
   * one surface that does not honour a withdrawal.
   */
  it.each([
    ["", true],
    ["OUT_OF_STOCK", true],
    ["NOT_PRICED", true],
    ["UNAVAILABLE", false],
    ["REMOVED", false]
  ] as const)("code %s -> price visible %s", (code, visible) => {
    const o = overlay();
    o.availability = { ...o.availability, code };
    expect(commercePriceVisible(o)).toBe(visible);
  });
});

describe("commerceOverlayForCache", () => {
  it("keeps the publication facts and drops everything perishable", () => {
    const cached = commerceOverlayForCache(overlay());
    expect(cached).toBeDefined();

    // Facts about the publication. These do not change and they are what makes
    // a restored card still recognisable as a product from this merchant.
    expect(cached!.pulsedrop).toBe(true);
    expect(cached!.product.listing_id).toBe(77);
    expect(cached!.product.title).toBe("Aurora Desk Lamp");
    expect(cached!.product.image_url).toBe("https://cdn.example/lamp.jpg");
    expect(cached!.seller.store_name).toBe("Northlight Studio");
    expect(cached!.attribution.token).toBe("pd1.1.reel.77");

    // "$49.00" from three days ago is a specific false claim; no price is a
    // visibly incomplete card the user can refresh.
    expect(cached!.product.price_label).toBe("");
    expect(cached!.product.quantity).toBe(0);
    expect(cached!.product.inventory_state).toBe("");
    expect(cached!.cta.enabled).toBe(false);
    expect(cached!.cta.route).toBe("");
    expect(cached!.availability.purchasable).toBe(false);
  });

  it("leaves a restored overlay unable to enable a CTA or show a price", () => {
    const cached = commerceOverlayForCache(overlay())!;
    expect(commerceCtaEnabled(cached)).toBe(false);
    expect(cached.product.price_label).toBe("");
  });

  it("passes undefined through, so an ordinary reel stays ordinary", () => {
    expect(commerceOverlayForCache(undefined)).toBeUndefined();
  });

  it("does not mutate the network copy it was handed", () => {
    // The same object is still on screen when the cache write happens.
    const live = overlay();
    commerceOverlayForCache(live);
    expect(live.product.price_label).toBe("$49.00");
    expect(live.cta.enabled).toBe(true);
  });
});

describe("commerceOverlayIsStale", () => {
  it("calls a cached overlay stale and a fetched one fresh", () => {
    expect(commerceOverlayIsStale("cache")).toBe(true);
    expect(commerceOverlayIsStale("network")).toBe(false);
  });
});

/**
 * Every i18n key the backend emits, read from the backend.
 *
 * Restating the list here would make this test pass forever while the server
 * moved, which is the failure mode it exists to prevent. The literals are
 * pulled from the two Python modules that own them.
 */
function serverEmittedKeys(): string[] {
  const sources = [
    path.join(REPO_ROOT, "services", "pulsedrop", "hydration.py"),
    path.join(REPO_ROOT, "services", "pulsedrop", "editorial.py")
  ];
  const keys = new Set<string>();
  for (const file of sources) {
    const text = fs.readFileSync(file, "utf8");
    // Only string literals in code, not the `#:` comment prose that explains
    // the broken `extended:` form.
    for (const line of text.split("\n")) {
      if (line.trimStart().startsWith("#")) continue;
      for (const match of line.matchAll(/"((?:commerce|extended|common|social|discovery):[\w.]+)"/g)) {
        keys.add(match[1]);
      }
    }
  }
  return [...keys].sort();
}

describe("the i18n keys the server sends", () => {
  beforeAll(async () => {
    resetCatalogCache();
    // Extended-tier copy is not loaded by default in a test process.
    await activateLocale("en");
  });

  it("finds some — a regex that silently matches nothing proves nothing", () => {
    const keys = serverEmittedKeys();
    // Five editorial labels, one CTA, four availability states, minus the two
    // availability codes that carry no chip. Any collapse toward zero here means
    // the extractor broke, not that the server stopped emitting keys.
    expect(keys.length).toBeGreaterThanOrEqual(8);
  });

  it("uses a registered namespace, never the tier filename", () => {
    // `extended:` is the bug. It resolves to humanized English in all eleven
    // locales and reports as 100% covered.
    const misprefixed = serverEmittedKeys().filter((key) => key.startsWith("extended:"));
    expect(misprefixed).toEqual([]);
  });

  it("resolves every one of them against the shipped catalog", () => {
    const unresolved = serverEmittedKeys().filter((key) => !hasTranslation(key, "en"));
    expect(unresolved).toEqual([]);
  });

  it("would have caught the original defect", () => {
    // The guard above is only meaningful if an unregistered namespace really
    // does fail this check. Prove it against the exact key that shipped.
    expect(hasTranslation("extended:pulsedrop.label.trending", "en")).toBe(false);
    expect(hasTranslation("commerce:pulsedrop.label.trending", "en")).toBe(true);
  });
});
