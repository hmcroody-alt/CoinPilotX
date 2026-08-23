/**
 * A presence tab is a promise that something is behind it. What is tested here
 * is that the promise is kept:
 *
 *   1. Only the tabs the server sent are rendered. The client never widens the
 *      set, because the server is the one that knows which modules are backed.
 *   2. A module is fetched when its tab is opened and not before — the root
 *      payload stays light, and a presence whose music catalogue is down is
 *      still a working presence.
 *   3. A failed module says so and offers a retry. It never renders as "no
 *      music", which is a different and false statement.
 */
import React from "react";
import { fireEvent, render, waitFor } from "@testing-library/react-native";

const mockGetPage = jest.fn();
const mockListPosts = jest.fn();
const mockListMusic = jest.fn();
jest.mock("../../api/pages", () => ({
  ...jest.requireActual("../../api/pages"),
  getPage: (...args: unknown[]) => mockGetPage(...args),
  getPageByHandle: (...args: unknown[]) => mockGetPage(...args),
  listPagePosts: (...args: unknown[]) => mockListPosts(...args),
  listPageMusic: (...args: unknown[]) => mockListMusic(...args)
}));

const mockSearchMarketplace = jest.fn();
jest.mock("../../api/marketplace", () => ({
  searchMarketplace: (...args: unknown[]) => mockSearchMarketplace(...args)
}));

import { PageScreen } from "../PageScreen";

const nav = () => ({ navigate: jest.fn(), setOptions: jest.fn() });

function page(overrides: Record<string, unknown> = {}) {
  return {
    id: 7,
    page_type: "ARTIST",
    category: "Musician",
    subcategory: "",
    name: "Night Signal",
    handle: "nightsignal",
    avatar_url: "",
    cover_url: "",
    description: "",
    genre: "",
    website: "",
    email: "",
    location: "",
    hours: {},
    status: "ACTIVE",
    verification_status: "NONE",
    verified: false,
    followers_count: 3,
    posts_count: 1,
    tabs: ["posts", "music", "about"],
    modules: { posts: true, music: true, about: true },
    viewer: { role: null, following: false },
    ...overrides
  };
}

function show(overrides?: Record<string, unknown>) {
  const navigation = nav();
  const view = render(
    <PageScreen
      route={{ key: "p", name: "Page", params: { pageId: 7 } } as never}
      navigation={navigation as never}
    />
  );
  return { view, navigation, overrides };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockGetPage.mockResolvedValue(page());
  mockListPosts.mockResolvedValue({ posts: [], has_more: false, next_offset: 0 });
  mockListMusic.mockResolvedValue({ artist: "Night Signal", tracks: [], linked: true });
  mockSearchMarketplace.mockResolvedValue({ items: [] });
});

describe("presence tabs are server-decided", () => {
  it("renders exactly the tabs the server sent", async () => {
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Music")).toBeTruthy());
    expect(view.queryByText("About")).toBeTruthy();
    // Part of the ARTIST ceiling, but unbacked and withheld by the server.
    expect(view.queryByText("Merch")).toBeNull();
    expect(view.queryByText("Events")).toBeNull();
  });

  it("hides an unbacked module entirely rather than showing a dead tab", async () => {
    mockGetPage.mockResolvedValue(page({ tabs: ["posts", "about"], modules: { music: false } }));
    const { view } = show();
    await waitFor(() => expect(view.queryByText("About")).toBeTruthy());
    expect(view.queryByText("Music")).toBeNull();
    expect(mockListMusic).not.toHaveBeenCalled();
  });
});

describe("modules load lazily and fail independently", () => {
  it("does not fetch music until the music tab is opened", async () => {
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Music")).toBeTruthy());
    expect(mockListMusic).not.toHaveBeenCalled();
    fireEvent.press(view.getByText("Music"));
    await waitFor(() => expect(mockListMusic).toHaveBeenCalledWith(7));
  });

  it("renders the catalogue when it loads", async () => {
    mockListMusic.mockResolvedValue({
      artist: "Night Signal",
      linked: true,
      tracks: [{ id: "t1", title: "Signal", artist: "Night Signal", genre: "Synth" }]
    });
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Music")).toBeTruthy());
    fireEvent.press(view.getByText("Music"));
    await waitFor(() => expect(view.queryByText("Signal")).toBeTruthy());
  });

  it("says an empty catalogue is empty, not broken", async () => {
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Music")).toBeTruthy());
    fireEvent.press(view.getByText("Music"));
    await waitFor(() => expect(view.queryByText("No music yet.")).toBeTruthy());
  });

  it("reports a module failure honestly and retries on demand", async () => {
    mockListMusic.mockRejectedValueOnce(new Error("down"));
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Music")).toBeTruthy());
    fireEvent.press(view.getByText("Music"));
    await waitFor(() => expect(view.queryByText("We couldn't load this section.")).toBeTruthy());
    // Not the same sentence as an empty catalogue.
    expect(view.queryByText("No music yet.")).toBeNull();
    // The rest of the presence survived the module failure.
    expect(view.queryByText("Night Signal")).toBeTruthy();

    mockListMusic.mockResolvedValue({
      artist: "Night Signal",
      linked: true,
      tracks: [{ id: "t1", title: "Signal", artist: "Night Signal" }]
    });
    fireEvent.press(view.getByText("Try Again"));
    await waitFor(() => expect(view.queryByText("Signal")).toBeTruthy());
  });
});

describe("shop and videos show the presence's own inventory, not a global list", () => {
  it("lists the linked seller's listings instead of sending the visitor to Marketplace", async () => {
    mockGetPage.mockResolvedValue(
      page({ tabs: ["posts", "merch", "about"], modules: { merch: true }, shop_seller_id: 42 })
    );
    mockSearchMarketplace.mockResolvedValue({
      items: [{ id: 5, listing_id: 5, title: "Tour Hoodie", price_label: "$40" }]
    });
    const { view, navigation } = show();
    await waitFor(() => expect(view.queryByText("Merch")).toBeTruthy());
    fireEvent.press(view.getByText("Merch"));
    await waitFor(() =>
      expect(mockSearchMarketplace).toHaveBeenCalledWith({ sellerUserId: 42, limit: 24 })
    );
    await waitFor(() => expect(view.queryByText("Tour Hoodie")).toBeTruthy());
    expect(navigation.navigate).not.toHaveBeenCalledWith("Tabs", { screen: "Marketplace" });
  });

  it("asks the server for this presence's video posts only", async () => {
    mockGetPage.mockResolvedValue(
      page({ tabs: ["posts", "videos", "about"], modules: { videos: true } })
    );
    mockListPosts.mockResolvedValue({
      posts: [{ id: 91, title: "Live at the Vault", post_type: "video" }],
      has_more: false,
      next_offset: 1
    });
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Videos")).toBeTruthy());
    fireEvent.press(view.getByText("Videos"));
    await waitFor(() =>
      expect(mockListPosts).toHaveBeenCalledWith(7, { limit: 24, kind: "videos" })
    );
    await waitFor(() => expect(view.queryByText("Live at the Vault")).toBeTruthy());
  });

  it("opens the product itself, not a marketplace search that might not contain it", async () => {
    mockGetPage.mockResolvedValue(
      page({ tabs: ["posts", "merch", "about"], modules: { merch: true }, shop_seller_id: 42 })
    );
    const listing = { id: 5, listing_id: 5, title: "Tour Hoodie", price_label: "$40" };
    mockSearchMarketplace.mockResolvedValue({ items: [listing] });
    const { view, navigation } = show();
    await waitFor(() => expect(view.queryByText("Merch")).toBeTruthy());
    fireEvent.press(view.getByText("Merch"));
    await waitFor(() => expect(view.queryByText("Tour Hoodie")).toBeTruthy());

    fireEvent.press(view.getByText("Tour Hoodie"));
    expect(navigation.navigate).toHaveBeenCalledWith("MarketplaceProduct", {
      listingId: 5,
      listing,
      title: "Tour Hoodie"
    });
    // `MarketplaceDetail` is the browse grid. It forwards to the product only
    // if the listing happens to be inside an unfiltered global search, so for a
    // small seller it lands the buyer in the marketplace at large instead.
    expect(navigation.navigate).not.toHaveBeenCalledWith(
      "MarketplaceDetail",
      expect.anything()
    );
  });
});

/**
 * An empty module means two different things to two different people. To a
 * visitor it is a fact about the page. To someone on the team it is a piece of
 * unfinished work, and the screen knows which screen finishes it.
 *
 * These tests hold the line at both ends: a visitor is never shown a control
 * they cannot use, and a team member is never shown a dead end.
 */
describe("empty modules read differently for the team than for a visitor", () => {
  const team = { viewer: { role: "OWNER", following: false } };

  it("tells a visitor what is missing and stops there", async () => {
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Music")).toBeTruthy());
    fireEvent.press(view.getByText("Music"));
    await waitFor(() => expect(view.queryByText("No music yet.")).toBeTruthy());
    expect(view.queryByText("Connect an artist profile")).toBeNull();
    expect(
      view.queryByText(
        "Tracks are uploaded to an artist profile. Connect the one these releases live under and they appear here."
      )
    ).toBeNull();
  });

  it("gives the team the same fact plus the step that fixes it", async () => {
    mockGetPage.mockResolvedValue(page(team));
    const { view, navigation } = show();
    await waitFor(() => expect(view.queryByText("Music")).toBeTruthy());
    fireEvent.press(view.getByText("Music"));
    await waitFor(() => expect(view.queryByText("No music yet.")).toBeTruthy());

    fireEvent.press(view.getByText("Connect an artist profile"));
    expect(navigation.navigate).toHaveBeenCalledWith("PageConnections", {
      pageId: 7,
      title: "Night Signal"
    });
  });

  it("offers the composer, not Connections, when the missing thing is a post", async () => {
    mockGetPage.mockResolvedValue(page(team));
    const { view, navigation } = show();
    await waitFor(() => expect(view.queryByText("No posts yet.")).toBeTruthy());
    fireEvent.press(view.getByText("Open Manage"));
    expect(navigation.navigate).toHaveBeenCalledWith("PagesHub", { focusPageId: 7 });
  });

  it("sends an empty About to the editor rather than to Connections", async () => {
    mockGetPage.mockResolvedValue(page(team));
    const { view, navigation } = show();
    await waitFor(() => expect(view.queryByText("About")).toBeTruthy());
    fireEvent.press(view.getByText("About"));
    await waitFor(() => expect(view.queryByText("Edit details")).toBeTruthy());
    fireEvent.press(view.getByText("Edit details"));
    expect(navigation.navigate).toHaveBeenCalledWith("PageEdit", {
      pageId: 7,
      title: "Night Signal"
    });
  });

  it("does not call a page with details written on it empty", async () => {
    mockGetPage.mockResolvedValue(page({ ...team, description: "Synth duo from Leeds." }));
    const { view } = show();
    await waitFor(() => expect(view.queryByText("About")).toBeTruthy());
    fireEvent.press(view.getByText("About"));
    await waitFor(() => expect(view.queryByText("Synth duo from Leeds.")).toBeTruthy());
    expect(view.queryByText("Edit details")).toBeNull();
  });

  it("does not count a genre as something written about the page", async () => {
    // `genre` comes from the page type, not from a person. A page whose only
    // "about" is a genre has still had nothing said about it.
    mockGetPage.mockResolvedValue(page({ ...team, genre: "Synthwave" }));
    const { view } = show();
    await waitFor(() => expect(view.queryByText("About")).toBeTruthy());
    fireEvent.press(view.getByText("About"));
    await waitFor(() => expect(view.queryByText("Nothing here yet.")).toBeTruthy());
    expect(view.queryByText("Edit details")).toBeTruthy();
  });

  it("points an empty Videos tab at Manage, where a video post is written", async () => {
    mockGetPage.mockResolvedValue(
      page({ ...team, tabs: ["posts", "videos", "about"], modules: { videos: true } })
    );
    const { view, navigation } = show();
    await waitFor(() => expect(view.queryByText("Videos")).toBeTruthy());
    fireEvent.press(view.getByText("Videos"));
    await waitFor(() => expect(view.queryByText("No videos yet.")).toBeTruthy());

    fireEvent.press(view.getByText("Open Manage"));
    expect(navigation.navigate).toHaveBeenCalledWith("PagesHub", { focusPageId: 7 });
    expect(navigation.navigate).not.toHaveBeenCalledWith("PageConnections", expect.anything());
  });

  it("stops offering to connect a shop once one is connected", async () => {
    mockGetPage.mockResolvedValue(
      page({ ...team, tabs: ["posts", "merch", "about"], modules: { merch: true }, shop_seller_id: 42 })
    );
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Merch")).toBeTruthy());
    fireEvent.press(view.getByText("Merch"));
    await waitFor(() => expect(view.queryByText("Nothing for sale yet.")).toBeTruthy());
    // The shop exists and is simply empty. Listings are created in Marketplace,
    // so there is no step to offer here.
    expect(view.queryByText("Connect a shop")).toBeNull();
    expect(view.queryByText("Listings you publish in Marketplace appear here.")).toBeTruthy();
  });

  it("offers to connect a shop when there is no seller behind the tab", async () => {
    mockGetPage.mockResolvedValue(
      page({ ...team, tabs: ["posts", "merch", "about"], modules: { merch: true } })
    );
    const { view, navigation } = show();
    await waitFor(() => expect(view.queryByText("Merch")).toBeTruthy());
    fireEvent.press(view.getByText("Merch"));
    await waitFor(() => expect(view.queryByText("Connect a shop")).toBeTruthy());
    expect(view.queryByText("Listings you publish in Marketplace appear here.")).toBeNull();

    fireEvent.press(view.getByText("Connect a shop"));
    expect(navigation.navigate).toHaveBeenCalledWith("PageConnections", {
      pageId: 7,
      title: "Night Signal"
    });
  });
});
