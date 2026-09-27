/**
 * The shoppable Signal, with the emphasis on what it must NOT draw.
 *
 * Most of a card is safe to leave to review: a misaligned price is visible, a
 * missing image is visible. Two things here are not.
 *
 * The first is invented data. The reference design carries "★★★★★ 4.8 (320)" and
 * a verified checkmark, and neither has a source in this product. An
 * implementation that hard-codes them looks *right* in a screenshot and is a lie
 * told to somebody deciding whether to send a stranger money. So the assertions
 * below are that no star, no rating, no review count and no checkmark reach the
 * tree from a real listing — and, separately, that each appears the moment its
 * field is non-null, so the card is finished and the backend aggregate is the only
 * work left.
 *
 * The second is the sponsorship disclosure. A FEATURED listing is a paid
 * placement. The eyebrow says Promoted visibly and the accessibility label says
 * Sponsored, because a disclosure carried by assistive tech alone is not a
 * disclosure, and one carried by pixels alone excludes the users least able to
 * verify a seller.
 */

import React from "react";
import { fireEvent, render } from "@testing-library/react-native";

jest.mock("@expo/vector-icons", () => ({ Ionicons: (props: object) => null }));

import type { MarketplaceListing } from "../../api/marketplace";
import { productSignalFromListing } from "../../commerce/productSignal";
import type { ProductSignal } from "../../commerce/productSignal";
import { activateLocale } from "../../i18n/engine";
import { ProductSignalCard } from "../ProductSignalCard";

/**
 * Every string on this card comes from the `extended` catalog tier, which the
 * provider warms in the background *after* first paint — so without this the
 * engine humanizes each leaf and "View product" arrives as "View Product Of".
 * The assertions below would then compare copy against copy neither the catalog
 * nor the design ever contained.
 */
beforeAll(async () => {
  await activateLocale("en");
});

const NOW = Date.parse("2026-09-26T12:00:00Z");

/**
 * Nested pressables call `event.stopPropagation()`, as `PostCard` does. RNTL
 * synthesizes no event, so each inner press supplies one — the same shim
 * `PostCard.test.tsx` uses.
 */
const NESTED_PRESS = { stopPropagation: () => undefined };

function listing(overrides: Partial<MarketplaceListing> = {}): MarketplaceListing {
  return {
    id: 41,
    listing_id: 41,
    seller_user_id: 7,
    seller_store_name: "Northwind Supply",
    seller_username: "northwind",
    title: "Aurora Desk Lamp",
    short_description: "Warm dimmable light for a small desk",
    price_label: "$64.99",
    currency: "USD",
    category: "Home",
    quantity: 4,
    thumbnail_url: "https://cdn.example/lamp.jpg",
    created_at: "2026-01-01T00:00:00Z",
    listing_type: "physical",
    delivery_type: "shipping",
    ...overrides
  };
}

const signalFor = (overrides: Partial<MarketplaceListing> = {}) =>
  productSignalFromListing(listing(overrides), NOW);

function renderCard(signal: ProductSignal, props: Partial<React.ComponentProps<typeof ProductSignalCard>> = {}) {
  const onOpenProduct = jest.fn();
  const utils = render(
    <ProductSignalCard testID="card" signal={signal} onOpenProduct={onOpenProduct} {...props} />
  );
  return { ...utils, onOpenProduct };
}

describe("it shows the product", () => {
  it("draws the name, the price and the seller", () => {
    const { queryByText } = renderCard(signalFor());
    expect(queryByText("Aurora Desk Lamp")).toBeTruthy();
    expect(queryByText("$64.99")).toBeTruthy();
    expect(queryByText("Northwind Supply")).toBeTruthy();
  });

  it("draws the caption and the handle", () => {
    const { queryByText } = renderCard(signalFor());
    expect(queryByText("Warm dimmable light for a small desk")).toBeTruthy();
    expect(queryByText("@northwind")).toBeTruthy();
  });

  it("draws a product image slot", () => {
    const { getByTestId } = renderCard(signalFor());
    expect(getByTestId("product-signal-media")).toBeTruthy();
  });

  it("draws the View product call to action", () => {
    const { queryByText, getByTestId } = renderCard(signalFor());
    expect(getByTestId("product-signal-cta")).toBeTruthy();
    expect(queryByText("View product")).toBeTruthy();
  });

  it("draws the like, comment, share and shop rail", () => {
    const { getByTestId } = renderCard(signalFor(), { onOpenMarketplace: jest.fn() });
    expect(getByTestId("product-signal-like")).toBeTruthy();
    expect(getByTestId("product-signal-comment")).toBeTruthy();
    expect(getByTestId("product-signal-share")).toBeTruthy();
    expect(getByTestId("product-signal-shop")).toBeTruthy();
  });

  it("omits the price line rather than printing a zero when none was sent", () => {
    const { queryByText } = renderCard(signalFor({ price_label: "" }));
    expect(queryByText("$0.00")).toBeNull();
    expect(queryByText("$0")).toBeNull();
  });
});

describe("it invents nothing", () => {
  it("draws no rating and no review count for a real listing", () => {
    const signal = signalFor();
    expect(signal.rating).toBeNull();
    const { queryByText, UNSAFE_queryAllByProps } = renderCard(signal);
    expect(queryByText("4.8")).toBeNull();
    expect(queryByText("(320)")).toBeNull();
    expect(UNSAFE_queryAllByProps({ name: "star" })).toHaveLength(0);
  });

  it("draws no verified checkmark for a real listing", () => {
    const { UNSAFE_queryAllByProps } = renderCard(signalFor());
    expect(UNSAFE_queryAllByProps({ name: "checkmark-circle" })).toHaveLength(0);
  });

  it("never claims the product is trending", () => {
    // Per-listing engagement is a registered mock-data gap. Every eyebrow this
    // card can draw is backed by a column.
    for (const overrides of [{}, { featured: 1 }, { created_at: new Date(NOW).toISOString() }]) {
      const { queryByText } = renderCard(signalFor(overrides));
      expect(queryByText(/trending/i)).toBeNull();
    }
  });

  it("draws the rating the moment the field is non-null", () => {
    // The other half of the claim above: the card is finished, so when the review
    // aggregate lands there is nothing here to un-fake.
    const signal: ProductSignal = { ...signalFor(), rating: 4.8, reviewCount: 320 };
    const { queryByText, UNSAFE_queryAllByProps } = renderCard(signal);
    expect(queryByText("4.8")).toBeTruthy();
    expect(queryByText("(320)")).toBeTruthy();
    expect(UNSAFE_queryAllByProps({ name: "star" })).toHaveLength(1);
  });

  it("draws the checkmark the moment verification is true", () => {
    const signal: ProductSignal = { ...signalFor(), sellerVerified: true };
    const { UNSAFE_queryAllByProps } = renderCard(signal);
    expect(UNSAFE_queryAllByProps({ name: "checkmark-circle" })).toHaveLength(1);
  });

  it("draws a rating with no review count when only the rating exists", () => {
    const signal: ProductSignal = { ...signalFor(), rating: 4.8, reviewCount: null };
    const { queryByText } = renderCard(signal);
    expect(queryByText("4.8")).toBeTruthy();
    expect(queryByText("(0)")).toBeNull();
  });
});

describe("a paid placement is disclosed twice", () => {
  it("says Promoted visibly and Sponsored to a screen reader", () => {
    const { queryByText, getByTestId } = renderCard(signalFor({ featured: 1 }));
    expect(queryByText("Promoted")).toBeTruthy();
    expect(getByTestId("card").props.accessibilityLabel).toContain("Sponsored");
  });

  it("says neither for an unboosted listing", () => {
    const { queryByText, getByTestId } = renderCard(signalFor());
    expect(queryByText("Promoted")).toBeNull();
    expect(getByTestId("card").props.accessibilityLabel).not.toContain("Sponsored");
  });

  it("prefers the paid disclosure over the new-listing eyebrow", () => {
    // A boosted listing that is also new must not hide the disclosure behind
    // "Just listed".
    const { queryByText } = renderCard(
      signalFor({ featured: 1, created_at: new Date(NOW).toISOString() })
    );
    expect(queryByText("Promoted")).toBeTruthy();
    expect(queryByText("Just listed")).toBeNull();
  });
});

describe("accessibility", () => {
  it("announces the product, the price and the seller in one label", () => {
    const { getByTestId } = renderCard(signalFor());
    const label = getByTestId("card").props.accessibilityLabel as string;
    expect(label).toContain("Aurora Desk Lamp");
    expect(label).toContain("$64.99");
    expect(label).toContain("Northwind Supply");
  });

  it("gives the CTA its own label naming the product", () => {
    const { getByTestId } = renderCard(signalFor());
    expect(getByTestId("product-signal-cta").props.accessibilityLabel).toBe(
      "View product Aurora Desk Lamp"
    );
  });

  it("gives the seller row its own label", () => {
    const { getByTestId } = renderCard(signalFor(), { onOpenSeller: jest.fn() });
    expect(getByTestId("product-signal-seller").props.accessibilityLabel).toBe(
      "Open the Northwind Supply store"
    );
  });

  it("marks a liked card as selected", () => {
    const { getByTestId } = renderCard(signalFor(), { onLike: jest.fn(), liked: true });
    const like = getByTestId("product-signal-like");
    expect(like.props.accessibilityState.selected).toBe(true);
    expect(like.props.accessibilityLabel).toBe("Liked");
  });

  it("gives every control the button role", () => {
    const { getByTestId } = renderCard(signalFor(), {
      onOpenSeller: jest.fn(),
      onOpenMarketplace: jest.fn(),
      onLike: jest.fn(),
      onComment: jest.fn(),
      onShare: jest.fn()
    });
    for (const testID of [
      "card",
      "product-signal-seller",
      "product-signal-cta",
      "product-signal-like",
      "product-signal-comment",
      "product-signal-share",
      "product-signal-shop"
    ]) {
      expect(getByTestId(testID).props.accessibilityRole).toBe("button");
    }
  });
});

describe("the four tap targets stay separate", () => {
  it("opens the product from the card body", () => {
    const signal = signalFor();
    const { getByTestId, onOpenProduct } = renderCard(signal);
    fireEvent.press(getByTestId("card"));
    expect(onOpenProduct).toHaveBeenCalledWith(signal);
  });

  it("opens the product from the CTA", () => {
    const signal = signalFor();
    const { getByTestId, onOpenProduct } = renderCard(signal);
    fireEvent.press(getByTestId("product-signal-cta"), NESTED_PRESS);
    expect(onOpenProduct).toHaveBeenCalledWith(signal);
  });

  it("opens the seller from the seller row without also opening the product", () => {
    const onOpenSeller = jest.fn();
    const signal = signalFor();
    const { getByTestId, onOpenProduct } = renderCard(signal, { onOpenSeller });
    fireEvent.press(getByTestId("product-signal-seller"), NESTED_PRESS);
    expect(onOpenSeller).toHaveBeenCalledWith(signal);
    // "Look at this product" must not be a way to land in a stranger's store,
    // and vice versa.
    expect(onOpenProduct).not.toHaveBeenCalled();
  });

  it("opens Marketplace from the shop action without opening the product", () => {
    const onOpenMarketplace = jest.fn();
    const signal = signalFor();
    const { getByTestId, onOpenProduct } = renderCard(signal, { onOpenMarketplace });
    fireEvent.press(getByTestId("product-signal-shop"), NESTED_PRESS);
    expect(onOpenMarketplace).toHaveBeenCalledWith(signal);
    expect(onOpenProduct).not.toHaveBeenCalled();
  });

  it("does not render the shop action when there is nowhere to send it", () => {
    const { queryByTestId } = renderCard(signalFor());
    expect(queryByTestId("product-signal-shop")).toBeNull();
  });

  it("disables the seller row rather than swallowing the tap when no handler is given", () => {
    const { getByTestId } = renderCard(signalFor());
    expect(getByTestId("product-signal-seller").props.accessibilityState.disabled).toBe(true);
  });
});

describe("reusability", () => {
  it("imports no navigator and names no route", () => {
    // The mission's requirement that this card work in Home, Search, a creator
    // profile, a community feed and Reels, enforced against the source rather
    // than promised in a comment. A single `navigation.navigate` here would make
    // every other surface a copy-paste.
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const source = require("fs").readFileSync(
      require("path").join(__dirname, "../ProductSignalCard.tsx"),
      "utf8"
    );
    const code = source.slice(source.indexOf("import { memo }"));
    expect(code).not.toMatch(/@react-navigation/);
    expect(code).not.toMatch(/navigation\./);
    expect(code).not.toMatch(/useNavigation/);
  });
});
