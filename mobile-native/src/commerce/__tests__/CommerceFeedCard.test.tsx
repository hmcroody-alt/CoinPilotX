/**
 * The card the user can actually get rid of, and the beacons behind it.
 *
 * `commerceRows.test.ts` proves the engine puts a commerce unit *between* whole
 * posts. That is only half the promise. The other half lives here, and it is
 * three separate things that a diff will not show you:
 *
 *   - **The menu opens inside the card.** The mission's hard rule is that a
 *     commerce unit never covers a post. The placement engine cannot express an
 *     overlay, but this component could reintroduce one the moment the ••• menu
 *     becomes a floating popover — so the menu's position is asserted
 *     structurally, as "a descendant of the card", not eyeballed.
 *
 *   - **Every negative action reaches the parent with its own verb.** "Hide
 *     this", "Not interested" and "Don't recommend this seller" are three
 *     different durable preferences. A menu that collapses the card for all
 *     three but posts `hide` every time looks perfect and quietly throws away
 *     the signal the whole feedback loop is built on. So these assert the
 *     *argument*, never `toHaveBeenCalled()`.
 *
 *   - **"Served" and "seen" stay separate, and "seen" fires once.** A visible
 *     impression that double-counts inflates the only number anyone will use to
 *     judge whether this feature works.
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

function renderCard(props: Partial<React.ComponentProps<typeof CommerceFeedCard>> = {}) {
  const onFeedback = jest.fn();
  const navigate = jest.fn();
  const utils = render(
    <CommerceFeedCard
      placement={placement()}
      isViewable={false}
      visibleDwellMs={1000}
      navigation={{ navigate }}
      onFeedback={onFeedback}
      {...props}
    />
  );
  return { ...utils, onFeedback, navigate };
}

beforeEach(() => {
  mockReducedMotion = true;
  jest.clearAllMocks();
});

describe("CommerceFeedCard — the menu stays inside the card", () => {
  it("renders no menu until the ••• button is pressed", () => {
    const { queryByTestId, getByTestId } = renderCard();
    expect(queryByTestId("commerce-card-menu")).toBeNull();
    fireEvent.press(getByTestId("commerce-card-menu-button"));
    expect(queryByTestId("commerce-card-menu")).not.toBeNull();
  });

  it("expands the menu as a descendant of the card, not as a layer over the feed", () => {
    const { getByTestId } = renderCard();
    fireEvent.press(getByTestId("commerce-card-menu-button"));

    // Walk up from the menu. If it ever becomes a popover — a Modal, or a
    // sibling absolutely positioned over the row below — this walk no longer
    // reaches the card root, and the card is covering a post again.
    let node: ReactTestInstance | null = getByTestId("commerce-card-menu");
    const root = getByTestId("commerce-card-p1");
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
    const { getByTestId, toJSON } = renderCard();
    fireEvent.press(getByTestId("commerce-card-menu-button"));

    // `position: "absolute"` is how a row stops being a row. The Modal is
    // exempt — the user opens that one deliberately — and it is not rendered
    // here because `whyOpen` is false.
    const flattened = JSON.stringify(toJSON());
    expect(flattened).not.toContain('"position":"absolute"');
  });
});

describe("CommerceFeedCard — each negative action keeps its own verb", () => {
  it.each([
    ["hide", "commerce-card-menu-hide"],
    ["not_interested", "commerce-card-menu-not_interested"],
    ["see_fewer", "commerce-card-menu-see_fewer"],
    ["hide_seller", "commerce-card-menu-hide_seller"],
    ["snooze", "commerce-card-menu-snooze"]
  ])("reports %s to the parent", (action, testID) => {
    const { getByTestId, onFeedback } = renderCard();
    fireEvent.press(getByTestId("commerce-card-menu-button"));
    fireEvent.press(getByTestId(testID));

    expect(onFeedback).toHaveBeenCalledTimes(1);
    expect(onFeedback.mock.calls[0][0].placementId).toBe("p1");
    expect(onFeedback.mock.calls[0][1]).toBe(action);
  });

  it("closes the menu when an action is taken", () => {
    const { getByTestId, queryByTestId } = renderCard();
    fireEvent.press(getByTestId("commerce-card-menu-button"));
    fireEvent.press(getByTestId("commerce-card-menu-hide"));
    expect(queryByTestId("commerce-card-menu")).toBeNull();
  });

  it("folds the row away before telling the parent to remove it", () => {
    mockReducedMotion = false;
    jest.useFakeTimers();
    try {
      const { getByTestId, onFeedback } = renderCard();
      // The collapse animates the height measured by `onLayout`, which RNTL
      // never fires on its own — without this the card takes the immediate
      // path and the test would pass without exercising anything.
      fireEvent(getByTestId("commerce-card-menu-button").parent!.parent!, "layout", {
        nativeEvent: { layout: { height: 320, width: 360, x: 0, y: 0 } }
      });
      fireEvent.press(getByTestId("commerce-card-menu-button"));
      fireEvent.press(getByTestId("commerce-card-menu-hide"));

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
});

describe("CommerceFeedCard — impressions", () => {
  it("reports 'served' on mount without claiming it was seen", () => {
    renderCard();
    expect(impression).toHaveBeenCalledTimes(1);
    expect(impression.mock.calls[0][1]).toMatchObject({ visible: false });
  });

  it("does not report a visible impression for a row that never met the dwell", () => {
    jest.useFakeTimers();
    try {
      const { unmount } = renderCard({ isViewable: true, visibleDwellMs: 1000 });
      act(() => {
        jest.advanceTimersByTime(200);
      });
      unmount();
      const visibleCalls = impression.mock.calls.filter((call) => call[1]?.visible);
      expect(visibleCalls).toHaveLength(0);
    } finally {
      jest.useRealTimers();
    }
  });

  it("reports a visible impression once, and only once, after the dwell", () => {
    jest.useFakeTimers();
    try {
      const { rerender, unmount } = renderCard({ isViewable: true, visibleDwellMs: 1000 });
      act(() => {
        jest.advanceTimersByTime(1200);
      });

      // Scrolling away and back must not file a second one: the dwell is a
      // property of the card, not of each time it crosses the fold.
      rerender(
        <CommerceFeedCard
          placement={placement()}
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
});

describe("CommerceFeedCard — engagement", () => {
  it("records the click before navigating to the product", async () => {
    const { getByTestId, navigate } = renderCard();
    await act(async () => {
      fireEvent.press(getByTestId("commerce-card-view"));
    });

    expect(engagement).toHaveBeenCalledWith(expect.objectContaining({ placementId: "p1" }), "click");
    expect(navigate).toHaveBeenCalledWith("MarketplaceProduct", {
      listingId: 77,
      title: "Women's Casual Sneakers"
    });
    // The beacon is started first on purpose — navigating unmounts this card,
    // and a request begun on the way out races its own teardown.
    expect(engagement.mock.invocationCallOrder[0]).toBeLessThan(navigate.mock.invocationCallOrder[0]);
  });

  it("does not navigate for a placement that arrived without a listing id", async () => {
    const { getByTestId, navigate } = renderCard({
      placement: placement({ product: { listingId: 0 } as never })
    });
    await act(async () => {
      fireEvent.press(getByTestId("commerce-card-body"));
    });
    expect(navigate).not.toHaveBeenCalled();
  });

  it("records a save and says so without waiting for the server", async () => {
    const { getByTestId } = renderCard();
    fireEvent.press(getByTestId("commerce-card-save"));
    expect(engagement).toHaveBeenCalledWith(expect.objectContaining({ placementId: "p1" }), "save");
    await waitFor(() =>
      expect(getByTestId("commerce-card-save").props.accessibilityState?.selected).toBe(true)
    );
  });
});

describe("CommerceFeedCard — labelling", () => {
  it("renders the label the server sent and never the Sponsored one", () => {
    const { queryByText } = renderCard();
    expect(queryByText("commerce:discovery.label.recommended")).not.toBeNull();
    expect(queryByText("commerce:discovery.label.sponsored")).toBeNull();
  });

  it("carries a house promotion's own label rather than relabelling it", () => {
    const { queryByText } = renderCard({
      placement: placement({ promotionClass: "house", labelKey: "commerce:discovery.label.trending" })
    });
    expect(queryByText("commerce:discovery.label.trending")).not.toBeNull();
    expect(queryByText("commerce:discovery.label.sponsored")).toBeNull();
  });

  it("names the reason the server gave, not a generic one", () => {
    const { queryByText } = renderCard();
    expect(queryByText("commerce:discovery.subtitle.because_you_viewed")).not.toBeNull();
  });
});
