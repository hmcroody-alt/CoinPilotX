/**
 * The strip the user can actually get rid of, and the beacons behind it.
 *
 * `commerceRows.test.ts` proves the engine puts a commerce unit *between* whole
 * posts. That is only half the promise. The other half lives here, and it is
 * four separate things that a diff will not show you:
 *
 *   - **The menu opens inside the card.** The mission's hard rule is that a
 *     commerce unit never covers a post. The placement engine cannot express an
 *     overlay, but this component could reintroduce one the moment the ••• menu
 *     becomes a floating popover — so the menu's position is asserted
 *     structurally, as "a descendant of the card", not eyeballed. A 148pt tile
 *     cannot hold six options, and this pair of tests is the reason the menu
 *     lifted to the strip instead of becoming a Modal.
 *
 *   - **Every negative action reaches the parent with its own verb.** "Hide
 *     this", "Not interested" and "Don't recommend this seller" are three
 *     different durable preferences. A menu that collapses the card for all
 *     three but posts `hide` every time looks perfect and quietly throws away
 *     the signal the whole feedback loop is built on. So these assert the
 *     *argument*, never `toHaveBeenCalled()`.
 *
 *   - **"Served" and "seen" stay separate, and "seen" fires once per tile.** A
 *     visible impression that double-counts inflates the only number anyone will
 *     use to judge whether this feature works. A rail adds a second way to get
 *     this wrong: a tile scrolled off the right-hand edge is *in* a visible row
 *     without being visible itself.
 *
 *   - **The collapse animation is for the row, not for a tile.** Folding the
 *     whole strip away when one of four products is hidden would be a half-second
 *     of the feed moving for a change the user can see is local.
 *
 * The label is checked too, for a reason that is not cosmetic: an organic
 * placement rendered as "Sponsored" is an untruthful ad disclosure, which is the
 * single failure in this system with a legal shape rather than a UX one.
 */
import { act, fireEvent, render, waitFor } from "@testing-library/react-native";
import type { ReactTestInstance } from "react-test-renderer";
import { CommerceFeedCard } from "../CommerceFeedCard";
import {
  recordCommerceEngagement,
  recordCommerceImpression,
  type CommercePlacement
} from "../../api/commerceDiscovery";

jest.mock("../../api/commerceDiscovery", () => ({
  recordCommerceImpression: jest.fn(() => Promise.resolve(true)),
  recordCommerceEngagement: jest.fn(() => Promise.resolve(true)),
  explainCommercePlacement: jest.fn(() => Promise.resolve(null))
}));

jest.mock("../../api/marketplace", () => ({
  saveMarketplaceListing: jest.fn(() => Promise.resolve(true))
}));

// Keys echoed back: the catalog is the i18n validator's job, and echoing keeps
// these assertions about which key was chosen rather than about English.
jest.mock("../../i18n/I18nContext", () => ({
  useTranslation: () => ({ t: (key: string) => key })
}));

// Reduced motion by default so a dismissal resolves synchronously. The collapse
// animation gets its own test below, where the delay is the point.
let mockReducedMotion = true;
jest.mock("../../theme/logiNexusMotion", () => ({
  useLogiNexusReducedMotion: () => mockReducedMotion
}));

const impression = recordCommerceImpression as jest.MockedFunction<typeof recordCommerceImpression>;
const engagement = recordCommerceEngagement as jest.MockedFunction<typeof recordCommerceEngagement>;

function placement(overrides: Partial<CommercePlacement> = {}): CommercePlacement {
  return {
    placementId: "p1",
    impressionToken: "tok-1",
    surface: "feed",
    slot: 0,
    expiresAt: "",
    promotionClass: "organic",
    labelKey: "commerce:discovery.label.recommended",
    reason: "because_you_viewed",
    rankingVersion: "commerce-discovery-v1",
    priceMinor: 4999,
    priceCurrency: "USD",
    ...overrides,
    product: {
      listingId: 77,
      title: "Women's Casual Sneakers",
      priceLabel: "$49.99",
      coverImageUrl: "https://cdn.example/sneaker.jpg",
      sellerUserId: 42,
      sellerStoreName: "M&W Store",
      category: "shoes",
      rating: 4.8,
      ratingCount: 120,
      ...(overrides.product || {})
    }
  };
}

/** A second product, so the strip is a strip rather than a card in disguise. */
function secondPlacement(): CommercePlacement {
  return placement({
    placementId: "p2",
    impressionToken: "tok-2",
    product: { listingId: 78, title: "Canvas Tote", sellerUserId: 43 } as never
  });
}

function renderStrip(props: Partial<React.ComponentProps<typeof CommerceFeedCard>> = {}) {
  const onFeedback = jest.fn();
  const navigate = jest.fn();
  const utils = render(
    <CommerceFeedCard
      placements={[placement()]}
      isViewable={false}
      visibleDwellMs={1000}
      navigation={{ navigate }}
      onFeedback={onFeedback}
      {...props}
    />
  );
  return { ...utils, onFeedback, navigate };
}

/**
 * Tell the rail which tiles are on screen.
 *
 * The component gates "seen" on the *tile's* viewability, not the row's, and in
 * a test nothing ever lays out — so without this every visible impression is
 * suppressed and the impression tests would pass while asserting nothing. Driving
 * the real callback is also what pins the gate: delete it from the component and
 * these calls stop mattering, which the last test in that block catches.
 */
function showTiles(getByTestId: (id: string) => ReactTestInstance, items: CommercePlacement[]) {
  act(() => {
    getByTestId("commerce-strip-rail").props.onViewableItemsChanged({
      viewableItems: items.map((item) => ({ item })),
      changed: []
    });
  });
}

beforeEach(() => {
  mockReducedMotion = true;
  jest.clearAllMocks();
});

describe("CommerceFeedCard — it is a strip", () => {
  it("draws one tile per product in one row", () => {
    const { queryByTestId } = renderStrip({ placements: [placement(), secondPlacement()] });
    expect(queryByTestId("commerce-tile-p1")).not.toBeNull();
    expect(queryByTestId("commerce-tile-p2")).not.toBeNull();
    // One rail, so the two tiles are siblings in a horizontal list rather than
    // two stacked rows spending the page's whole commerce budget.
    expect(queryByTestId("commerce-strip-rail")).not.toBeNull();
  });

  it("scrolls horizontally rather than growing the feed", () => {
    // §1 and §4 together: a commerce unit is one row. If the rail ever stopped
    // being horizontal, four products would become four rows of feed height.
    const { getByTestId } = renderStrip({ placements: [placement(), secondPlacement()] });
    expect(getByTestId("commerce-strip-rail").props.horizontal).toBe(true);
  });

  it("offers a route to the rest of the catalogue", () => {
    // §9. The feed is a doorway; Marketplace is the destination.
    const { getByTestId, navigate } = renderStrip();
    fireEvent.press(getByTestId("commerce-strip-see-all"));
    expect(navigate).toHaveBeenCalledWith("Marketplace");
  });

  it("does not report 'See all' as engagement with any one product", () => {
    // Nobody clicked a product. Attributing this to the lead tile would credit
    // it with a click it never received.
    const { getByTestId } = renderStrip({ placements: [placement(), secondPlacement()] });
    fireEvent.press(getByTestId("commerce-strip-see-all"));
    expect(engagement).not.toHaveBeenCalled();
  });

  it("draws a single-product strip as a strip, heading and all", () => {
    // The production catalogue is thin today, so this is the common case rather
    // than an edge one — and the heading and "See all" are worth *more* here,
    // because they are the only route to what the feed could not show.
    const { queryByTestId } = renderStrip({ placements: [placement()] });
    expect(queryByTestId("commerce-tile-p1")).not.toBeNull();
    expect(queryByTestId("commerce-strip-see-all")).not.toBeNull();
  });

  it("renders nothing at all for an empty strip", () => {
    const { toJSON } = renderStrip({ placements: [] });
    expect(toJSON()).toBeNull();
  });
});

describe("CommerceFeedCard — the menu stays inside the card", () => {
  it("renders no menu until a tile's ••• is pressed", () => {
    const { queryByTestId, getByTestId } = renderStrip();
    expect(queryByTestId("commerce-strip-menu")).toBeNull();
    fireEvent.press(getByTestId("commerce-tile-menu-button"));
    expect(queryByTestId("commerce-strip-menu")).not.toBeNull();
  });

  it("expands the menu as a descendant of the card, not as a layer over the feed", () => {
    const { getByTestId } = renderStrip();
    fireEvent.press(getByTestId("commerce-tile-menu-button"));

    // Walk up from the menu. If it ever becomes a popover — a Modal, or a
    // sibling absolutely positioned over the row below — this walk no longer
    // reaches the card root, and the card is covering a post again.
    let node: ReactTestInstance | null = getByTestId("commerce-strip-menu");
    const root = getByTestId("commerce-strip-p1");
    let reachedRoot = false;
    while (node) {
      if (node === root) {
        reachedRoot = true;
        break;
      }
      node = node.parent;
    }
    expect(reachedRoot).toBe(true);
  });

  it("never positions any part of the card absolutely", () => {
    const { getAllByTestId, toJSON } = renderStrip({ placements: [placement(), secondPlacement()] });
    fireEvent.press(getAllByTestId("commerce-tile-menu-button")[0]);

    // `position: "absolute"` is how a row stops being a row. The tile's ••• is
    // the standing temptation here — overlaying it on the cover is free — and
    // this is the assertion that costs it something.
    const flattened = JSON.stringify(toJSON());
    expect(flattened).not.toContain('"position":"absolute"');
  });

  it("names the product the menu is about", () => {
    // A full-width menu below the rail is no longer visually attached to the
    // tile that opened it, so "Hide this" has to be answerable without it.
    const { getAllByTestId, getByTestId } = renderStrip({
      placements: [placement(), secondPlacement()]
    });
    fireEvent.press(getAllByTestId("commerce-tile-menu-button")[1]);
    expect(getByTestId("commerce-strip-menu-subject").props.children).toBe("Canvas Tote");
  });
});

describe("CommerceFeedCard — each negative action keeps its own verb", () => {
  it.each([
    ["hide", "commerce-strip-menu-hide"],
    ["not_interested", "commerce-strip-menu-not_interested"],
    ["see_fewer", "commerce-strip-menu-see_fewer"],
    ["hide_seller", "commerce-strip-menu-hide_seller"],
    ["snooze", "commerce-strip-menu-snooze"]
  ])("reports %s to the parent", (action, testID) => {
    const { getByTestId, onFeedback } = renderStrip();
    fireEvent.press(getByTestId("commerce-tile-menu-button"));
    fireEvent.press(getByTestId(testID));

    expect(onFeedback).toHaveBeenCalledTimes(1);
    expect(onFeedback.mock.calls[0][0].placementId).toBe("p1");
    expect(onFeedback.mock.calls[0][1]).toBe(action);
  });

  it("reports the product whose ••• was pressed, not the one that leads the strip", () => {
    // The whole reason the menu carries a subject. If it defaulted to
    // `placements[0]`, hiding the second tile would hide the first and the
    // server would be told about a product the shopper never objected to.
    const { getAllByTestId, getByTestId, onFeedback } = renderStrip({
      placements: [placement(), secondPlacement()]
    });
    fireEvent.press(getAllByTestId("commerce-tile-menu-button")[1]);
    fireEvent.press(getByTestId("commerce-strip-menu-hide"));
    expect(onFeedback.mock.calls[0][0].placementId).toBe("p2");
  });

  it("closes the menu when an action is taken", () => {
    const { getByTestId, queryByTestId } = renderStrip();
    fireEvent.press(getByTestId("commerce-tile-menu-button"));
    fireEvent.press(getByTestId("commerce-strip-menu-hide"));
    expect(queryByTestId("commerce-strip-menu")).toBeNull();
  });

  it("folds the row away before telling the parent to remove its last product", () => {
    mockReducedMotion = false;
    jest.useFakeTimers();
    try {
      const { getByTestId, onFeedback } = renderStrip();
      // The collapse animates the height measured by `onLayout`, which RNTL
      // never fires on its own — without this the card takes the immediate
      // path and the test would pass without exercising anything.
      fireEvent(getByTestId("commerce-strip-card"), "layout", {
        nativeEvent: { layout: { height: 320, width: 360, x: 0, y: 0 } }
      });
      fireEvent.press(getByTestId("commerce-tile-menu-button"));
      fireEvent.press(getByTestId("commerce-strip-menu-hide"));

      // The parent's response is to delete the row. If that happened now, the
      // posts either side would snap together under the user's thumb.
      expect(onFeedback).not.toHaveBeenCalled();

      act(() => {
        jest.advanceTimersByTime(400);
      });
      expect(onFeedback).toHaveBeenCalledWith(expect.objectContaining({ placementId: "p1" }), "hide");
    } finally {
      jest.useRealTimers();
    }
  });

  it("does not fold the row when the strip has other products left", () => {
    // Hiding one of two is a change inside the rail: the remaining tiles close
    // up horizontally and the row's height never moves. Animating the whole
    // strip away and back would be half a second of the feed shifting for a
    // change the user can see is local.
    mockReducedMotion = false;
    jest.useFakeTimers();
    try {
      const { getAllByTestId, getByTestId, onFeedback } = renderStrip({
        placements: [placement(), secondPlacement()]
      });
      fireEvent(getByTestId("commerce-strip-card"), "layout", {
        nativeEvent: { layout: { height: 320, width: 360, x: 0, y: 0 } }
      });
      fireEvent.press(getAllByTestId("commerce-tile-menu-button")[0]);
      fireEvent.press(getByTestId("commerce-strip-menu-hide"));
      expect(onFeedback).toHaveBeenCalledWith(expect.objectContaining({ placementId: "p1" }), "hide");
    } finally {
      jest.useRealTimers();
    }
  });

  it("folds the row for a snooze even with products left, because none survive it", () => {
    mockReducedMotion = false;
    jest.useFakeTimers();
    try {
      const { getAllByTestId, getByTestId, onFeedback } = renderStrip({
        placements: [placement(), secondPlacement()]
      });
      fireEvent(getByTestId("commerce-strip-card"), "layout", {
        nativeEvent: { layout: { height: 320, width: 360, x: 0, y: 0 } }
      });
      fireEvent.press(getAllByTestId("commerce-tile-menu-button")[0]);
      fireEvent.press(getByTestId("commerce-strip-menu-snooze"));
      expect(onFeedback).not.toHaveBeenCalled();
      act(() => {
        jest.advanceTimersByTime(400);
      });
      expect(onFeedback).toHaveBeenCalledWith(expect.anything(), "snooze");
    } finally {
      jest.useRealTimers();
    }
  });
});

describe("CommerceFeedCard — impressions", () => {
  it("reports 'served' once per tile without claiming any was seen", () => {
    renderStrip({ placements: [placement(), secondPlacement()] });
    expect(impression).toHaveBeenCalledTimes(2);
    expect(impression.mock.calls.every((call) => call[1]?.visible === false)).toBe(true);
  });

  it("does not report a visible impression for a tile that never met the dwell", () => {
    jest.useFakeTimers();
    try {
      const { getByTestId, unmount } = renderStrip({ isViewable: true, visibleDwellMs: 1000 });
      showTiles(getByTestId, [placement()]);
      act(() => {
        jest.advanceTimersByTime(200);
      });
      unmount();
      expect(impression.mock.calls.filter((call) => call[1]?.visible)).toHaveLength(0);
    } finally {
      jest.useRealTimers();
    }
  });

  it("reports a visible impression once, and only once, after the dwell", () => {
    jest.useFakeTimers();
    try {
      const { getByTestId, rerender, unmount } = renderStrip({
        isViewable: true,
        visibleDwellMs: 1000
      });
      showTiles(getByTestId, [placement()]);
      act(() => {
        jest.advanceTimersByTime(1200);
      });

      // Scrolling away and back must not file a second one: the dwell is a
      // property of the tile, not of each time it crosses the fold.
      rerender(
        <CommerceFeedCard
          placements={[placement()]}
          isViewable={false}
          visibleDwellMs={1000}
          navigation={{ navigate: jest.fn() }}
          onFeedback={jest.fn()}
        />
      );
      act(() => {
        jest.advanceTimersByTime(2000);
      });
      unmount();

      const visibleCalls = impression.mock.calls.filter((call) => call[1]?.visible);
      expect(visibleCalls).toHaveLength(1);
      expect(visibleCalls[0][1]?.viewDurationMs).toBeGreaterThanOrEqual(0);
    } finally {
      jest.useRealTimers();
    }
  });

  it("does not count a tile scrolled off the rail as seen", () => {
    // The row being in the feed's viewport says nothing about a tile past the
    // right-hand edge. Counting those is how a rail inflates the one number
    // §18 exists to make trustworthy — and it is invisible in the data, because
    // the figures simply come out higher.
    jest.useFakeTimers();
    try {
      const { getByTestId, unmount } = renderStrip({
        placements: [placement(), secondPlacement()],
        isViewable: true,
        visibleDwellMs: 1000
      });
      showTiles(getByTestId, [placement()]);
      act(() => {
        jest.advanceTimersByTime(1200);
      });
      unmount();

      const seen = impression.mock.calls.filter((call) => call[1]?.visible);
      expect(seen).toHaveLength(1);
      expect(seen[0][0]).toMatchObject({ placementId: "p1" });
    } finally {
      jest.useRealTimers();
    }
  });
});

describe("CommerceFeedCard — engagement", () => {
  it("records the click before navigating to the product", async () => {
    const { getByTestId, navigate } = renderStrip();
    await act(async () => {
      fireEvent.press(getByTestId("commerce-tile-body"));
    });

    expect(engagement).toHaveBeenCalledWith(expect.objectContaining({ placementId: "p1" }), "click");
    expect(navigate).toHaveBeenCalledWith("MarketplaceProduct", {
      listingId: 77,
      title: "Women's Casual Sneakers"
    });
    // The beacon is started first on purpose — navigating unmounts this tile,
    // and a request begun on the way out races its own teardown.
    expect(engagement.mock.invocationCallOrder[0]).toBeLessThan(navigate.mock.invocationCallOrder[0]);
  });

  it("opens the product the shopper tapped, not the one leading the strip", async () => {
    const { getAllByTestId, navigate } = renderStrip({
      placements: [placement(), secondPlacement()]
    });
    await act(async () => {
      fireEvent.press(getAllByTestId("commerce-tile-body")[1]);
    });
    expect(navigate).toHaveBeenCalledWith("MarketplaceProduct", {
      listingId: 78,
      title: "Canvas Tote"
    });
  });

  it("does not render a tile for a placement that arrived without a listing id", () => {
    // A rail of blank boxes is worse than a shorter rail, and a tile with no
    // product cannot be tapped anywhere useful.
    const { queryByTestId } = renderStrip({
      placements: [placement({ product: { listingId: 0 } as never })]
    });
    expect(queryByTestId("commerce-tile-body")).toBeNull();
  });

  it("records a save and says so without waiting for the server", async () => {
    const { getByTestId } = renderStrip();
    fireEvent.press(getByTestId("commerce-tile-save"));
    expect(engagement).toHaveBeenCalledWith(expect.objectContaining({ placementId: "p1" }), "save");
    await waitFor(() =>
      expect(getByTestId("commerce-tile-save").props.accessibilityState?.selected).toBe(true)
    );
  });

  it("saves only the tile that was pressed", async () => {
    const { getAllByTestId } = renderStrip({ placements: [placement(), secondPlacement()] });
    fireEvent.press(getAllByTestId("commerce-tile-save")[1]);
    expect(engagement).toHaveBeenCalledWith(expect.objectContaining({ placementId: "p2" }), "save");
    await waitFor(() =>
      expect(getAllByTestId("commerce-tile-save")[0].props.accessibilityState?.selected).toBe(false)
    );
  });
});

describe("CommerceFeedCard — labelling", () => {
  it("renders the label the server sent and never the Sponsored one", () => {
    const { queryByText } = renderStrip();
    expect(queryByText("commerce:discovery.label.recommended")).not.toBeNull();
    expect(queryByText("commerce:discovery.label.sponsored")).toBeNull();
  });

  it("carries a house promotion's own label rather than relabelling it", () => {
    const { queryByText } = renderStrip({
      placements: [
        placement({ promotionClass: "house", labelKey: "commerce:discovery.label.trending" })
      ]
    });
    expect(queryByText("commerce:discovery.label.trending")).not.toBeNull();
    expect(queryByText("commerce:discovery.label.sponsored")).toBeNull();
  });

  it("names the reason the server gave, not a generic one", () => {
    const { queryByText } = renderStrip();
    expect(queryByText("commerce:discovery.subtitle.because_you_viewed")).not.toBeNull();
  });
});
