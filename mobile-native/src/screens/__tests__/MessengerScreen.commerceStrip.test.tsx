/**
 * Where the Marketplace strip sits in the inbox, proved against the rendered
 * tree rather than against the source.
 *
 * The mission's Messenger rules are about *position*, and position is the one
 * thing a unit test of the component cannot see. `MessengerCommerceStrip.test`
 * proves the row behaves; this proves it is in the only place it is allowed to
 * be:
 *
 *   - **It is not a conversation.** The strip is not in the list's `data` — it
 *     is part of `ListHeaderComponent` — so no thread is replaced, none is
 *     pushed out of the first page, and the order the user knows is the order
 *     they get. This is asserted against the `FlatList`'s actual `data` prop,
 *     because "the list looks right" in a fixture of two conversations is not
 *     the same claim.
 *
 *   - **It is above the "Recent conversations" heading, not between the heading
 *     and the rows.** A section label separated from the things it labels is
 *     worse than no label at all, and that arrangement is also the one that
 *     genuinely reads as commerce inserted into someone's conversation list.
 *
 *   - **It is not sticky.** The list sets no `stickyHeaderIndices`, so a header
 *     child scrolls away with the header. Asserted because the fix for "the
 *     strip scrolls away too fast" is one prop, and that prop would turn a
 *     dismissible suggestion into a permanent banner over the inbox.
 *
 *   - **Nothing is the normal answer.** With an empty serve the screen renders
 *     exactly what it rendered before this feature existed.
 *
 * Ordering is asserted by comparing *indices* in one pre-order walk. Comparing
 * `ReactTestInstance`s directly makes Jest serialise two React trees to build a
 * diff on failure, which exhausts the heap and reports a V8 stack trace where
 * the assertion should be.
 */
import { act, fireEvent, render, waitFor } from "@testing-library/react-native";
import { FlatList } from "react-native";
import type { ReactTestInstance } from "react-test-renderer";
import { fetchCommercePlacements } from "../../api/commerceDiscovery";
import type { CommercePlacement, CommerceServeResult } from "../../api/commerceDiscovery";
import { listConversations, loadCachedConversations } from "../../api/messenger";
import { __resetCommerceSessionId } from "../../commerce/session";
import { MessengerScreen } from "../MessengerScreen";

jest.mock("@react-navigation/native", () => ({
  useNavigation: () => ({ navigate: jest.fn(), goBack: jest.fn() }),
  // Runs the effect once on mount, which is what focus does on a fresh screen.
  useFocusEffect: (effect: () => void | (() => void)) => {
    const { useEffect } = require("react");
    useEffect(effect, []);
  },
  useIsFocused: () => true
}));

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));

// Echo keys instead of translating them. Nothing here asserts on copy — the
// claims are all about *position* — and without an `I18nProvider` no namespace
// is ever loaded, so the real engine reports every one of the strip's keys as
// missing and prints eleven warnings over a green run. The keys are real: they
// live under `commerce` in each locale's `extended.json`, which the provider
// warms in the background once the first frame is up. A suite that warns on
// green is a suite whose warnings stop being read.
jest.mock("../../i18n/I18nContext", () => ({
  useTranslation: () => ({ t: (key: string) => key })
}));

jest.mock("expo-linear-gradient", () => ({
  LinearGradient: ({ children }: any) => children ?? null
}));

jest.mock("../../session/auth", () => ({
  useAuth: () => ({ authState: { status: "signedIn" }, requestReauthentication: jest.fn() })
}));

jest.mock("../../navigation/BottomNavVisibility", () => ({
  useBottomNavSurface: () => ({
    handlers: { onScroll: jest.fn(), onScrollBeginDrag: jest.fn(), scrollEventThrottle: 16 },
    contentPadding: { paddingBottom: 0 },
    paddingBottom: 0
  })
}));

jest.mock("../../navigation/refreshCoordinator", () => ({
  registerRefreshDestination: () => () => undefined
}));

jest.mock("../../api/messenger", () => {
  const actual = jest.requireActual("../../api/messenger");
  return {
    ...actual,
    listConversations: jest.fn(() => Promise.resolve([])),
    loadCachedConversations: jest.fn(() => Promise.resolve([])),
    subscribeConversationUpdates: jest.fn(() => () => undefined)
  };
});

jest.mock("../../api/commerceDiscovery", () => ({
  fetchCommercePlacements: jest.fn(),
  recordCommerceFeedback: jest.fn(() => Promise.resolve(true)),
  recordCommerceImpression: jest.fn(() => Promise.resolve(true)),
  recordCommerceEngagement: jest.fn(() => Promise.resolve(true)),
  explainCommercePlacement: jest.fn(() => Promise.resolve(null))
}));

const fetchPlacements = fetchCommercePlacements as jest.MockedFunction<typeof fetchCommercePlacements>;
const conversations = listConversations as jest.MockedFunction<typeof listConversations>;
const cached = loadCachedConversations as jest.MockedFunction<typeof loadCachedConversations>;

function conversation(id: number, title: string) {
  return {
    id,
    conversation_id: id,
    conversation_domain: "SOCIAL",
    title,
    conversation_type: "direct",
    latest_message: "hey",
    last_message_preview: "hey",
    presence: "offline",
    unread_count: 0
  } as any;
}

function placement(id = "p1"): CommercePlacement {
  return {
    placementId: id,
    impressionToken: `tok_${id}`,
    surface: "messenger",
    slot: 0,
    expiresAt: "",
    promotionClass: "organic",
    labelKey: "commerce:discovery.label.recommended",
    reason: "because_you_viewed",
    rankingVersion: "commerce-discovery-v1",
    priceMinor: 4999,
    priceCurrency: "USD",
    product: {
      listingId: 501,
      title: "Women's Casual Sneakers",
      priceLabel: "$49.99",
      coverImageUrl: "https://cdn.example/p.jpg",
      sellerUserId: 77,
      sellerStoreName: "M&W Store",
      category: "shoes",
      rating: 4.8,
      ratingCount: 120
    }
  };
}

function served(placements: CommercePlacement[]): CommerceServeResult {
  return {
    placements,
    visiblePercentThreshold: 60,
    visibleDwellMs: 1000,
    cadence: { leadIn: 0, interval: 1, maxPerPage: 1 }
  };
}

/** The whole rendered tree in render order. */
function inRenderOrder(node: ReactTestInstance): ReactTestInstance[] {
  return node.findAll(() => true, { deep: true });
}

const CONVERSATIONS = [conversation(1, "Ada"), conversation(2, "Grace")];

beforeEach(() => {
  jest.clearAllMocks();
  __resetCommerceSessionId();
  conversations.mockResolvedValue(CONVERSATIONS);
  cached.mockResolvedValue([]);
  fetchPlacements.mockResolvedValue(served([placement()]));
});

/** Render and let both the conversation load and the serve request settle. */
async function renderInbox() {
  const utils = render(<MessengerScreen />);
  await waitFor(() => expect(utils.queryAllByText("Ada").length).toBeGreaterThan(0));
  return utils;
}

describe("the strip's place in the inbox", () => {
  it("is not a conversation row — the list's data is only conversations", async () => {
    const { UNSAFE_getByType, getByTestId } = await renderInbox();
    await waitFor(() => expect(getByTestId("messenger-commerce-p1")).toBeTruthy());

    const list = UNSAFE_getByType(FlatList);
    const data = (list.props.data || []) as any[];
    // Every entry is a conversation the server sent, plus the built-in UNDX AI
    // thread the screen has always prepended. Nothing commerce-shaped is in
    // here, so nothing was replaced and nothing was pushed off the page.
    expect(data.every((item) => typeof item.conversation_id !== "undefined")).toBe(true);
    expect(data.some((item) => String(item.id).startsWith("p"))).toBe(false);
    expect(data.filter((item) => item.title === "Ada" || item.title === "Grace")).toHaveLength(2);
  });

  it("renders above the Recent conversations heading", async () => {
    const { getByTestId, UNSAFE_getByType } = await renderInbox();
    await waitFor(() => expect(getByTestId("messenger-commerce-p1")).toBeTruthy());

    const order = inRenderOrder(UNSAFE_getByType(FlatList));
    const strip = order.indexOf(getByTestId("messenger-commerce-slot"));
    const heading = order.indexOf(getByTestId("messenger-recent-heading"));

    expect(strip).toBeGreaterThanOrEqual(0);
    expect(heading).toBeGreaterThanOrEqual(0);
    // Above the heading, so the heading stays attached to the rows it labels.
    expect(strip).toBeLessThan(heading);
  });

  it("is inside the list header, so it scrolls away with it", async () => {
    const { getByTestId, UNSAFE_getByType } = await renderInbox();
    await waitFor(() => expect(getByTestId("messenger-commerce-p1")).toBeTruthy());

    const list = UNSAFE_getByType(FlatList);
    const order = inRenderOrder(list);
    const strip = order.indexOf(getByTestId("messenger-commerce-slot"));
    const firstRow = order.indexOf(getByTestId("messenger-active-rail"));

    // The presence rail is unambiguously a header child, and the strip renders
    // after it — which places the strip in the header rather than in the body.
    expect(firstRow).toBeGreaterThanOrEqual(0);
    expect(strip).toBeGreaterThan(firstRow);
  });

  it("is not pinned to the top of the list", async () => {
    // One prop away from becoming a permanent banner over someone's inbox.
    const { UNSAFE_getByType } = await renderInbox();
    expect(UNSAFE_getByType(FlatList).props.stickyHeaderIndices).toBeUndefined();
  });
});

describe("nothing is the normal answer", () => {
  it("renders the inbox exactly as before when the engine has nothing", async () => {
    fetchPlacements.mockResolvedValue(served([]));
    const { queryByTestId, getByTestId } = await renderInbox();

    expect(queryByTestId("messenger-commerce-slot")).toBeNull();
    // And the heading and the conversations are untouched.
    expect(getByTestId("messenger-recent-heading")).toBeTruthy();
  });

  it("renders the inbox when the recommendation request fails", async () => {
    // A recommendation failure must never break Messenger. The inbox is the
    // feature; this is not.
    fetchPlacements.mockRejectedValue(new Error("engine down"));
    const { queryByTestId, queryAllByText } = await renderInbox();

    expect(queryByTestId("messenger-commerce-slot")).toBeNull();
    expect(queryAllByText("Grace").length).toBeGreaterThan(0);
  });

  it("asks the messenger surface for one, and only on mount", async () => {
    await renderInbox();
    await waitFor(() => expect(fetchPlacements).toHaveBeenCalledTimes(1));
    const [surface, options] = fetchPlacements.mock.calls[0];
    expect(surface).toBe("messenger");
    expect(options?.limit).toBe(1);
  });
});

describe("the strip retires itself", () => {
  it("leaves no gap behind when it is dismissed", async () => {
    const { getByTestId, queryByTestId } = await renderInbox();
    await waitFor(() => expect(getByTestId("messenger-commerce-p1")).toBeTruthy());

    // Two `act`s, not one: the menu is a `Modal` that does not exist in the tree
    // until the first press has been flushed, so querying for its items inside
    // the same callback finds nothing.
    await act(async () => {
      fireEvent.press(getByTestId("messenger-commerce-menu-button"));
    });
    await act(async () => {
      fireEvent.press(getByTestId("messenger-commerce-menu-hide"));
    });

    // The slot itself goes, not just its contents — an empty wrapper left in
    // the header is the "blank hole" the mission calls out.
    expect(queryByTestId("messenger-commerce-slot")).toBeNull();
    expect(queryByTestId("messenger-commerce-p1")).toBeNull();
    expect(getByTestId("messenger-recent-heading")).toBeTruthy();
  });
});
