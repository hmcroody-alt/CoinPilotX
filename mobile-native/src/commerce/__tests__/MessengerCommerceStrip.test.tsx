/**
 * The one row Messenger is allowed to give Marketplace, and the rules it keeps.
 *
 * `useMessengerCommerce.test.ts` proves the state — one suggestion or none, a
 * dismissal that is not refilled. `privateConversationsAreCommerceFree.test.ts`
 * proves the thing that matters most, which is that none of this exists inside a
 * conversation. What is left is the row itself, and three of its properties are
 * not visible in a diff:
 *
 *   - **The ••• menu does not move the inbox.** `CommerceFeedCard` expands its
 *     menu inline, which is right for a stream of cards. Here the thing directly
 *     underneath is the user's list of people, and pushing it half a screen down
 *     to show six options is the "pushes chats down aggressively" the mission
 *     forbids by name. So the menu's containment in a `Modal` is asserted
 *     structurally — a `Modal` takes no layout space, an expanding `View` does,
 *     and the difference between them is invisible in a screenshot of the
 *     closed state.
 *
 *   - **Every negative action carries its own verb.** Four menu items that all
 *     post `hide` would clear the strip correctly and silently throw away the
 *     signal the ranking loop runs on. These assert the *argument*, never
 *     `toHaveBeenCalled()`.
 *
 *   - **"Served" and "seen" stay separate.** A strip at the top of a list is on
 *     screen the instant the screen opens, which makes it the easiest place in
 *     the app to accidentally count every launch as an impression.
 *
 * The label is checked for a reason that is not cosmetic: Messenger serves
 * organic and house placements only, and an unpaid placement wearing an ad
 * disclosure is the one failure in this system with a legal shape.
 */
import { act, fireEvent, render } from "@testing-library/react-native";
import { Modal, StyleSheet } from "react-native";
import type { ReactTestInstance } from "react-test-renderer";
import { MessengerCommerceStrip } from "../MessengerCommerceStrip";
import {
  recordCommerceEngagement,
  recordCommerceImpression,
  type CommerceFeedbackAction,
  type CommercePlacement
} from "../../api/commerceDiscovery";

jest.mock("../../api/commerceDiscovery", () => ({
  recordCommerceImpression: jest.fn(() => Promise.resolve(true)),
  recordCommerceEngagement: jest.fn(() => Promise.resolve(true)),
  explainCommercePlacement: jest.fn(() => Promise.resolve(null))
}));

// Keys echoed back: the catalog is `npm run i18n:validate`'s job, and echoing
// keeps these assertions about *which key was chosen* rather than about English.
jest.mock("../../i18n/I18nContext", () => ({
  useTranslation: () => ({ t: (key: string) => key })
}));

const impression = recordCommerceImpression as jest.MockedFunction<typeof recordCommerceImpression>;
const engagement = recordCommerceEngagement as jest.MockedFunction<typeof recordCommerceEngagement>;

function placement(overrides: Partial<CommercePlacement> = {}): CommercePlacement {
  const { product: productOverrides, ...rest } = overrides as any;
  return {
    placementId: "p1",
    impressionToken: "tok-1",
    surface: "messenger",
    slot: 0,
    expiresAt: "",
    promotionClass: "organic",
    labelKey: "commerce:discovery.label.recommended",
    reason: "because_you_viewed",
    rankingVersion: "commerce-discovery-v1",
    priceMinor: 4999,
    priceCurrency: "USD",
    ...rest,
    product: {
      listingId: 501,
      title: "Women's Casual Sneakers",
      priceLabel: "$49.99",
      coverImageUrl: "https://cdn.example/p.jpg",
      sellerUserId: 77,
      sellerStoreName: "M&W Store",
      category: "shoes",
      rating: 4.8,
      ratingCount: 120,
      ...(productOverrides || {})
    }
  };
}

const navigate = jest.fn();
const onFeedback = jest.fn();

function renderStrip(props: Partial<React.ComponentProps<typeof MessengerCommerceStrip>> = {}) {
  return render(
    <MessengerCommerceStrip
      placement={props.placement || placement()}
      isViewable={props.isViewable ?? false}
      visibleDwellMs={props.visibleDwellMs ?? 1000}
      navigation={{ navigate }}
      onFeedback={props.onFeedback || onFeedback}
    />
  );
}

/** Every ancestor of a node, innermost first. */
function ancestors(node: ReactTestInstance): ReactTestInstance[] {
  const chain: ReactTestInstance[] = [];
  let current: ReactTestInstance | null = node.parent;
  while (current) {
    chain.push(current);
    current = current.parent;
  }
  return chain;
}

beforeEach(() => {
  jest.clearAllMocks();
});

describe("the strip itself", () => {
  it("renders the server's label, and never an ad disclosure", () => {
    const { getByText, queryByText } = renderStrip();
    expect(getByText("commerce:discovery.label.recommended")).toBeTruthy();
    // Not inferred from a score, not hardcoded — carried from the placement.
    // Messenger is organic and house only, so "Sponsored" here would be a
    // statement about money that did not change hands.
    expect(queryByText("commerce:discovery.label.sponsored")).toBeNull();
  });

  it("renders a house promotion under its own label", () => {
    const { getByText } = renderStrip({
      placement: placement({ promotionClass: "house", labelKey: "commerce:discovery.label.trending" })
    });
    expect(getByText("commerce:discovery.label.trending")).toBeTruthy();
  });

  it("shows the product, the store and the price", () => {
    const { getByText } = renderStrip();
    expect(getByText("Women's Casual Sneakers")).toBeTruthy();
    expect(getByText("M&W Store · $49.99")).toBeTruthy();
  });

  it("renders nothing at all rather than a row with no product in it", () => {
    // The hook already filters these out; this is the second wall. A strip with
    // a price and no title is indistinguishable from a bug to the person
    // holding the phone, and "no placement is better than a bad placement".
    const { toJSON } = renderStrip({ placement: placement({ product: { title: "" } } as any) });
    expect(toJSON()).toBeNull();
  });

  it("renders nothing when there is no listing to open", () => {
    const { toJSON } = renderStrip({ placement: placement({ product: { listingId: 0 } } as any) });
    expect(toJSON()).toBeNull();
  });

  it("stays one row high and horizontal", () => {
    // The mission's Messenger allowance is "about the height of one conversation
    // row". `minHeight` is `ConversationRow`'s own 64 and the layout is a single
    // horizontal line — so what is asserted is the shape, not the literal: a
    // designer may retune the number, but a *column* here would be a module,
    // which is the thing Messenger does not get.
    const { getByTestId } = renderStrip();
    const style = StyleSheet.flatten(getByTestId("messenger-commerce-p1").props.style) as Record<string, any>;
    expect(style.flexDirection).toBe("row");
    expect(style.minHeight).toBeLessThanOrEqual(72);
    expect(style.minHeight).toBeGreaterThan(0);
  });
});

describe("the ••• menu does not move the inbox", () => {
  it("is closed until it is asked for", () => {
    const { queryByTestId } = renderStrip();
    expect(queryByTestId("messenger-commerce-menu")).toBeNull();
  });

  it("opens inside a Modal rather than expanding into the conversation list", () => {
    const { getByTestId, UNSAFE_getAllByType } = renderStrip();
    fireEvent.press(getByTestId("messenger-commerce-menu-button"));

    const menu = getByTestId("messenger-commerce-menu");
    const modals = new Set(UNSAFE_getAllByType(Modal));
    // Containment, not a testID on the Modal: what matters is that the menu is
    // *inside* one, because that is what makes it cost zero layout in the list
    // underneath. A menu that moved into a sibling `View` would keep its testID
    // and push every conversation down.
    expect(ancestors(menu).some((node) => modals.has(node))).toBe(true);
  });

  it("offers every control the mission requires", () => {
    const { getByTestId } = renderStrip();
    fireEvent.press(getByTestId("messenger-commerce-menu-button"));
    for (const id of [
      "messenger-commerce-menu-hide",
      "messenger-commerce-menu-not_interested",
      "messenger-commerce-menu-see_fewer",
      "messenger-commerce-menu-hide_seller",
      "messenger-commerce-menu-why",
      "messenger-commerce-menu-snooze"
    ]) {
      expect(getByTestId(id)).toBeTruthy();
    }
  });

  it.each<[string, CommerceFeedbackAction]>([
    ["messenger-commerce-menu-hide", "hide"],
    ["messenger-commerce-menu-not_interested", "not_interested"],
    ["messenger-commerce-menu-see_fewer", "see_fewer"],
    ["messenger-commerce-menu-hide_seller", "hide_seller"],
    ["messenger-commerce-menu-snooze", "snooze"]
  ])("%s reports its own verb", (testID, action) => {
    const { getByTestId } = renderStrip();
    fireEvent.press(getByTestId("messenger-commerce-menu-button"));
    fireEvent.press(getByTestId(testID));

    expect(onFeedback).toHaveBeenCalledTimes(1);
    expect(onFeedback.mock.calls[0][1]).toBe(action);
    expect(onFeedback.mock.calls[0][0].placementId).toBe("p1");
  });

  it("closes the menu when an action is taken", () => {
    const { getByTestId, queryByTestId } = renderStrip();
    fireEvent.press(getByTestId("messenger-commerce-menu-button"));
    fireEvent.press(getByTestId("messenger-commerce-menu-hide"));
    expect(queryByTestId("messenger-commerce-menu")).toBeNull();
  });

  it("explains itself without dismissing anything", async () => {
    // "Why am I seeing this?" is the one menu item that is not a preference.
    // Answering it by hiding the thing being asked about would be a strange
    // reply to a question.
    const { getByTestId, getByText } = renderStrip();
    fireEvent.press(getByTestId("messenger-commerce-menu-button"));
    // `await act` rather than a bare press: the explanation is fetched, and the
    // `finally` that clears the loading flag lands after this tick. Letting that
    // settle outside `act` prints a warning on a passing test, and a suite that
    // warns on green is a suite whose warnings stop being read.
    await act(async () => {
      fireEvent.press(getByTestId("messenger-commerce-menu-why"));
    });

    expect(onFeedback).not.toHaveBeenCalled();
    expect(getByText("commerce:discovery.why.title")).toBeTruthy();
  });
});

describe("opening the product", () => {
  it("records the click before navigating away", async () => {
    // Ordering matters: the transition unmounts this component, so a beacon
    // started on the way out races its own teardown.
    const { getByTestId } = renderStrip();
    await act(async () => {
      fireEvent.press(getByTestId("messenger-commerce-body"));
    });

    expect(engagement).toHaveBeenCalledTimes(1);
    expect(engagement.mock.calls[0][1]).toBe("click");
    expect(navigate).toHaveBeenCalledWith("MarketplaceProduct", {
      listingId: 501,
      title: "Women's Casual Sneakers"
    });
  });

  it("does not fire twice on a double tap", async () => {
    const { getByTestId } = renderStrip();
    await act(async () => {
      fireEvent.press(getByTestId("messenger-commerce-body"));
      fireEvent.press(getByTestId("messenger-commerce-body"));
    });
    expect(navigate).toHaveBeenCalledTimes(1);
  });
});

describe("served and seen are two different events", () => {
  it("reports served on mount even though nothing has been looked at yet", () => {
    renderStrip({ isViewable: false });
    expect(impression).toHaveBeenCalledTimes(1);
    expect(impression.mock.calls[0][1]).toMatchObject({ visible: false });
  });

  it("does not report a visible impression for a strip nobody looked at", () => {
    jest.useFakeTimers();
    try {
      const { unmount } = renderStrip({ isViewable: false, visibleDwellMs: 1000 });
      act(() => {
        jest.advanceTimersByTime(5000);
      });
      unmount();
      expect(impression.mock.calls.filter((call) => call[1]?.visible)).toHaveLength(0);
    } finally {
      jest.useRealTimers();
    }
  });

  it("does not report a visible impression that was scrolled past inside the dwell", () => {
    // The strip is at the top of the list, so it is technically on screen for a
    // fraction of a second on every launch. That is not someone seeing a
    // product, and counting it would inflate the only number anyone will use to
    // judge whether this feature works.
    jest.useFakeTimers();
    try {
      const { unmount } = renderStrip({ isViewable: true, visibleDwellMs: 1000 });
      act(() => {
        jest.advanceTimersByTime(200);
      });
      unmount();
      expect(impression.mock.calls.filter((call) => call[1]?.visible)).toHaveLength(0);
    } finally {
      jest.useRealTimers();
    }
  });

  it("reports a visible impression once the dwell is met, and only once", () => {
    jest.useFakeTimers();
    try {
      const { unmount } = renderStrip({ isViewable: true, visibleDwellMs: 1000 });
      act(() => {
        jest.advanceTimersByTime(1200);
      });
      act(() => {
        jest.advanceTimersByTime(4000);
      });
      unmount();
      const visible = impression.mock.calls.filter((call) => call[1]?.visible);
      expect(visible).toHaveLength(1);
      expect(visible[0][1]?.viewDurationMs).toBeGreaterThanOrEqual(0);
    } finally {
      jest.useRealTimers();
    }
  });
});
