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
import { processColor } from "react-native";
import { fireEvent, render, waitFor } from "@testing-library/react-native";

const mockGetPage = jest.fn();
const mockListPosts = jest.fn();
const mockListMusic = jest.fn();
const mockListEvents = jest.fn();
const mockToggleFollow = jest.fn();
jest.mock("../../api/pages", () => ({
  ...jest.requireActual("../../api/pages"),
  getPage: (...args: unknown[]) => mockGetPage(...args),
  getPageByHandle: (...args: unknown[]) => mockGetPage(...args),
  listPagePosts: (...args: unknown[]) => mockListPosts(...args),
  listPageMusic: (...args: unknown[]) => mockListMusic(...args),
  listPageEvents: (...args: unknown[]) => mockListEvents(...args),
  togglePageFollow: (...args: unknown[]) => mockToggleFollow(...args)
}));

const mockSearchMarketplace = jest.fn();
jest.mock("../../api/marketplace", () => ({
  searchMarketplace: (...args: unknown[]) => mockSearchMarketplace(...args)
}));

import { PageScreen } from "../PageScreen";
import { colors } from "../../theme/colors";
import { presenceAccent } from "../../theme/presenceAccent";

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

/** A presence whose server-decided tab set includes Events. */
const withEvents = { tabs: ["posts", "events", "about"], modules: { events: true } };

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
  mockListEvents.mockResolvedValue({ enabled: true, linked: true, events: [] });
  mockToggleFollow.mockResolvedValue({ page_id: 7, following: true, followers_count: 4 });
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

  it("blames the app version, not the page, for a tab it has no branch for", async () => {
    // Unreachable against a matching server: it only offers tabs in its
    // `RENDERABLE_TABS`, which is this screen's branch set written down. What
    // remains is a newer server talking to an older build, and saying "nothing
    // here yet" would be a claim about the page — and a false one.
    mockGetPage.mockResolvedValue(
      page({
        viewer: { role: "OWNER", following: false },
        tabs: ["posts", "seances", "about"],
        modules: { seances: true }
      })
    );
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Seances")).toBeTruthy());
    fireEvent.press(view.getByText("Seances"));
    await waitFor(() =>
      expect(view.queryByText("This section needs a newer version of the app.")).toBeTruthy()
    );
    expect(view.queryByText("Nothing here yet.")).toBeNull();
    // Nothing to offer the team either — updating is not done from here.
    expect(view.queryByText("Open Manage")).toBeNull();
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

  /**
   * The link stores a catalogue *name*, and connecting one is an ordinary
   * `manage_links` write — so a presence pointed at somebody else's releases
   * renders identically to one pointed at its own. The name on screen is the
   * only thing that tells them apart, and it is the visitor, not the team, who
   * most needs to know whose work they are being shown.
   */
  it("names the catalogue when it is not this presence's own", async () => {
    mockListMusic.mockResolvedValue({
      artist: "Other Artist",
      linked: true,
      tracks: [{ id: "t1", title: "Signal", artist: "Other Artist" }]
    });
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Music")).toBeTruthy());
    fireEvent.press(view.getByText("Music"));

    await waitFor(() =>
      expect(view.queryByText("From the catalogue of Other Artist.")).toBeTruthy()
    );
  });

  it("stays quiet when the catalogue is the presence's own", async () => {
    mockListMusic.mockResolvedValue({
      artist: "Night Signal",
      linked: true,
      tracks: [{ id: "t1", title: "Signal", artist: "Night Signal" }]
    });
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Music")).toBeTruthy());
    fireEvent.press(view.getByText("Music"));
    await waitFor(() => expect(view.queryByText("Signal")).toBeTruthy());

    // Restating the page's own name is noise, and noise is what stops the line
    // above being read on the one page where it means something.
    expect(view.queryByText(/From the catalogue of/)).toBeNull();
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
/**
 * The Events tab shows a visitor projection of the canonical events domain:
 * upcoming published dates and nothing else. What is tested here is that the
 * screen shows everything it is given, invents nothing it is not, and never
 * offers an affordance that leads nowhere.
 */
describe("events show real dates from the events domain", () => {
  const dates = [
    {
      event_id: "ev1",
      title: "Vault Session",
      venue: "The Vault",
      starts_at: "2099-09-14T20:00:00Z",
      currency: "GBP",
      ticket_types: [
        { ticket_type_id: "t_early", name: "Early", price_cents: 1200, sold_out: true },
        { ticket_type_id: "t_std", name: "Standard", price_cents: 1800, sold_out: false }
      ]
    }
  ];

  it("does not ask the events domain anything until the tab is opened", async () => {
    mockGetPage.mockResolvedValue(page(withEvents));
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Events")).toBeTruthy());
    expect(mockListEvents).not.toHaveBeenCalled();
    fireEvent.press(view.getByText("Events"));
    await waitFor(() => expect(mockListEvents).toHaveBeenCalledWith(7));
  });

  it("shows the date, the venue and the cheapest ticket still on sale", async () => {
    mockGetPage.mockResolvedValue(page(withEvents));
    mockListEvents.mockResolvedValue({ enabled: true, linked: true, events: dates });
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Events")).toBeTruthy());
    fireEvent.press(view.getByText("Events"));
    await waitFor(() => expect(view.queryByText("Vault Session")).toBeTruthy());
    expect(view.queryByText(/The Vault/)).toBeTruthy();
    // 1200 is cheaper but gone, so quoting it would be an advertised price
    // nobody can pay. 1800 is what the next person through the door spends.
    expect(view.queryByText("From GBP 18.00")).toBeTruthy();
    expect(view.queryByText("From GBP 12.00")).toBeNull();
    // The raw stored timestamp is never put on screen as-is.
    expect(view.queryByText(/2099-09-14T20:00:00Z/)).toBeNull();
  });

  it("quotes the cheapest way in, not the dearest", async () => {
    // Distinct from the test above, which leaves only one tier on sale and so
    // cannot tell "cheapest" from "whichever survived the filter". Three open
    // tiers, cheapest in the middle, so neither the first nor the last row is
    // the right answer by accident.
    mockGetPage.mockResolvedValue(page(withEvents));
    mockListEvents.mockResolvedValue({
      enabled: true,
      linked: true,
      events: [
        {
          ...dates[0],
          ticket_types: [
            { ticket_type_id: "t_a", name: "Standard", price_cents: 2500, sold_out: false },
            { ticket_type_id: "t_b", name: "Balcony", price_cents: 900, sold_out: false },
            { ticket_type_id: "t_c", name: "VIP", price_cents: 6000, sold_out: false }
          ]
        }
      ]
    });
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Events")).toBeTruthy());
    fireEvent.press(view.getByText("Events"));
    await waitFor(() => expect(view.queryByText("From GBP 9.00")).toBeTruthy());
    expect(view.queryByText("From GBP 60.00")).toBeNull();
    expect(view.queryByText("From GBP 25.00")).toBeNull();
  });

  it("says sold out rather than quoting a price nobody can buy", async () => {
    mockGetPage.mockResolvedValue(page(withEvents));
    mockListEvents.mockResolvedValue({
      enabled: true,
      linked: true,
      events: [
        {
          ...dates[0],
          ticket_types: [
            { ticket_type_id: "t_std", name: "Standard", price_cents: 1800, sold_out: true }
          ]
        }
      ]
    });
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Events")).toBeTruthy());
    fireEvent.press(view.getByText("Events"));
    await waitFor(() => expect(view.queryByText("Sold out")).toBeTruthy());
    expect(view.queryByText("From GBP 18.00")).toBeNull();
  });

  it("says nothing about price for an event nobody has priced", async () => {
    // Not "Free". An event with no tiers is one where ticketing has not been
    // set up, which is a different claim from one that costs nothing — and the
    // wrong one would be quoted back at the organiser on the door.
    mockGetPage.mockResolvedValue(page(withEvents));
    mockListEvents.mockResolvedValue({
      enabled: true,
      linked: true,
      events: [{ event_id: "ev2", title: "Open Rehearsal", ticket_types: [] }]
    });
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Events")).toBeTruthy());
    fireEvent.press(view.getByText("Events"));
    await waitFor(() => expect(view.queryByText("Open Rehearsal")).toBeTruthy());
    expect(view.queryByText("Free entry")).toBeNull();
    expect(view.queryByText("Sold out")).toBeNull();
  });

  it("shows a date it cannot parse exactly as it was typed", async () => {
    // `starts_at` is free text server-side and never format-checked. Dropping
    // what does not parse would delete the only thing the page says about when
    // this happens.
    mockGetPage.mockResolvedValue(page(withEvents));
    mockListEvents.mockResolvedValue({
      enabled: true,
      linked: true,
      events: [{ event_id: "ev3", title: "Winter Tour", starts_at: "Late 2099, dates TBC" }]
    });
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Events")).toBeTruthy());
    fireEvent.press(view.getByText("Events"));
    await waitFor(() => expect(view.queryByText("Late 2099, dates TBC")).toBeTruthy());
  });

  it("reports a failing events domain as a failure, not as an empty calendar", async () => {
    // "No dates announced yet." would be a claim about the artist. The events
    // service being down is a claim about us, and it is temporary.
    mockGetPage.mockResolvedValue(page(withEvents));
    mockListEvents.mockRejectedValueOnce(new Error("503"));
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Events")).toBeTruthy());
    fireEvent.press(view.getByText("Events"));
    await waitFor(() => expect(view.queryByText("We couldn't load this section.")).toBeTruthy());
    expect(view.queryByText("No dates announced yet.")).toBeNull();

    mockListEvents.mockResolvedValue({ enabled: true, linked: true, events: dates });
    fireEvent.press(view.getByText("Try Again"));
    await waitFor(() => expect(view.queryByText("Vault Session")).toBeTruthy());
  });

  it("gives a visitor no control on an event row, because there is nothing behind one", async () => {
    // The row is deliberately not pressable: this build has no event detail
    // screen, and a row that responds to a tap and then does nothing is the
    // shallow-control defect this work exists to remove.
    mockGetPage.mockResolvedValue(page(withEvents));
    mockListEvents.mockResolvedValue({ enabled: true, linked: true, events: dates });
    const { view, navigation } = show();
    await waitFor(() => expect(view.queryByText("Events")).toBeTruthy());
    fireEvent.press(view.getByText("Events"));
    await waitFor(() => expect(view.queryByText("Vault Session")).toBeTruthy());
    navigation.navigate.mockClear();
    fireEvent.press(view.getByText("Vault Session"));
    expect(navigation.navigate).not.toHaveBeenCalled();
  });
});

describe("empty modules read differently for the team than for a visitor", () => {
  const team = { viewer: { role: "OWNER", following: false } };

  it("tells a visitor what is missing and stops there", async () => {
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Music")).toBeTruthy());
    fireEvent.press(view.getByText("Music"));
    await waitFor(() => expect(view.queryByText("No music yet.")).toBeTruthy());
    expect(view.queryByText("Connect an artist profile")).toBeNull();
    // Neither hint, not merely the one that happens not to apply: how a
    // presence is wired is the team's business and nobody else's.
    expect(view.queryByText(/appear here/)).toBeNull();
    expect(view.queryByText(/Connect the one these releases live under/)).toBeNull();
  });

  /**
   * "Nothing is connected" and "the connected catalogue is empty" are the same
   * empty list and want opposite sentences. The screen used to read only
   * `tracks`, so it gave the connect-a-catalogue answer to both — and the team
   * it misdirected was precisely the one that had already done the connecting.
   */
  it("gives the team the step that fixes it when nothing is connected", async () => {
    mockListMusic.mockResolvedValue({ artist: "", tracks: [], linked: false });
    mockGetPage.mockResolvedValue(page(team));
    const { view, navigation } = show();
    await waitFor(() => expect(view.queryByText("Music")).toBeTruthy());
    fireEvent.press(view.getByText("Music"));
    await waitFor(() => expect(view.queryByText("No music yet.")).toBeTruthy());

    // The sentence, not just the button: "Releases published to  appear here."
    // is what a collapsed branch produces, and it reads like a working screen.
    expect(
      view.getByText(
        "Tracks are uploaded to an artist profile. Connect the one these releases live under and they appear here."
      )
    ).toBeTruthy();

    fireEvent.press(view.getByText("Connect an artist profile"));
    expect(navigation.navigate).toHaveBeenCalledWith("PageConnections", {
      pageId: 7,
      title: "Night Signal"
    });
  });

  it("does not send the team to connect a catalogue they already connected", async () => {
    // The default fixture is a linked catalogue with nothing in it yet.
    mockGetPage.mockResolvedValue(page(team));
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Music")).toBeTruthy());
    fireEvent.press(view.getByText("Music"));
    await waitFor(() => expect(view.queryByText("No music yet.")).toBeTruthy());

    expect(view.queryByText("Connect an artist profile")).toBeNull();
    // And says where the releases would come from, since publishing them is
    // not something this screen can offer a door to.
    expect(view.getByText("Releases published to Night Signal appear here.")).toBeTruthy();
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

  it("tells a visitor a presence has no dates without offering to fix it", async () => {
    mockGetPage.mockResolvedValue(page(withEvents));
    mockListEvents.mockResolvedValue({ enabled: true, linked: false, events: [] });
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Events")).toBeTruthy());
    fireEvent.press(view.getByText("Events"));
    await waitFor(() => expect(view.queryByText("No dates announced yet.")).toBeTruthy());
    // A visitor cannot connect a business, so they are never shown the button
    // or the reason — that is the team's business, not theirs.
    expect(view.queryByText("Connect a business")).toBeNull();
  });

  it("sends the team to Connections when no business runs the dates yet", async () => {
    mockGetPage.mockResolvedValue(page({ ...team, ...withEvents }));
    mockListEvents.mockResolvedValue({ enabled: true, linked: false, events: [] });
    const { view, navigation } = show();
    await waitFor(() => expect(view.queryByText("Events")).toBeTruthy());
    fireEvent.press(view.getByText("Events"));
    await waitFor(() => expect(view.queryByText("Connect a business")).toBeTruthy());

    fireEvent.press(view.getByText("Connect a business"));
    expect(navigation.navigate).toHaveBeenCalledWith("PageConnections", {
      pageId: 7,
      title: "Night Signal"
    });
  });

  it("stops offering to connect a business once one is connected", async () => {
    // Same rule as the shop: the connection exists and the calendar is simply
    // empty. Dates are created where the organiser already works, so a second
    // door into it here would be a second thing to keep in sync.
    mockGetPage.mockResolvedValue(page({ ...team, ...withEvents }));
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Events")).toBeTruthy());
    fireEvent.press(view.getByText("Events"));
    await waitFor(() =>
      expect(view.queryByText("Dates you schedule for the connected business appear here.")).toBeTruthy()
    );
    expect(view.queryByText("Connect a business")).toBeNull();
  });

  it("does not blame the presence when events are off for the whole environment", async () => {
    // Three different empties reach this tab and they read identically if the
    // screen only counts rows: the domain is off, nothing is connected, or
    // there are genuinely no dates coming up. Only the middle one has a step,
    // and offering it in the other two sends the team to do useless work.
    mockGetPage.mockResolvedValue(page({ ...team, ...withEvents }));
    mockListEvents.mockResolvedValue({ enabled: false, linked: false, events: [] });
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Events")).toBeTruthy());
    fireEvent.press(view.getByText("Events"));
    await waitFor(() =>
      expect(
        view.queryByText(
          "Events are switched off for this environment, so there is nothing to connect yet."
        )
      ).toBeTruthy()
    );
    // Nothing here is theirs to fix, so nothing is offered.
    expect(view.queryByText("Connect a business")).toBeNull();
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

/**
 * The Follow button is the one control on this screen a stranger is meant to
 * press, and it was the only one that could fail in silence.
 *
 * Two separate faults, one symptom. The screen never read `status`, so a
 * presence that is not published still rendered a Follow button — and the
 * server answers that with a flat 403, because an unpublished presence is not
 * accepting followers. Meanwhile `onFollow` caught every rejection and threw it
 * away. The result was a button that lifted under the finger, changed nothing,
 * and said nothing; the reading is "this app is broken", not "this presence is
 * not published yet".
 *
 * The visitor cannot reach that state at all — `_load_visible_page` answers a
 * non-member with 404 rather than confirming the presence exists — so the only
 * person who ever saw the dead button was somebody on the team, and they are
 * the one person who can do something about it.
 */
describe("following says what the server said", () => {
  const team = { viewer: { role: "OWNER", following: false } };

  it("follows and takes the server's count rather than adding one itself", async () => {
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Follow")).toBeTruthy());
    expect(view.getByText("3 followers · 1 posts")).toBeTruthy();

    fireEvent.press(view.getByText("Follow"));
    await waitFor(() => expect(view.queryByText("Following")).toBeTruthy());
    expect(mockToggleFollow).toHaveBeenCalledWith(7);
    // 4 because the server said 4, not because the screen incremented 3.
    expect(view.getByText("4 followers · 1 posts")).toBeTruthy();
  });

  it("unfollows back down to the server's count", async () => {
    mockGetPage.mockResolvedValue(page({ viewer: { role: null, following: true } }));
    mockToggleFollow.mockResolvedValue({ page_id: 7, following: false, followers_count: 2 });
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Following")).toBeTruthy());

    fireEvent.press(view.getByText("Following"));
    await waitFor(() => expect(view.queryByText("Follow")).toBeTruthy());
    expect(view.getByText("2 followers · 1 posts")).toBeTruthy();
  });

  it("repeats the server's refusal instead of swallowing it", async () => {
    const { PulseApiError } = jest.requireActual("../../api/pulseApi");
    mockToggleFollow.mockRejectedValue(
      new PulseApiError("This page isn't accepting followers right now.", 403)
    );
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Follow")).toBeTruthy());

    fireEvent.press(view.getByText("Follow"));
    await waitFor(() =>
      expect(view.queryByText("This page isn't accepting followers right now.")).toBeTruthy()
    );
  });

  it("leaves the follow state alone when the server refuses", async () => {
    const { PulseApiError } = jest.requireActual("../../api/pulseApi");
    mockToggleFollow.mockRejectedValue(new PulseApiError("Nope.", 403));
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Follow")).toBeTruthy());

    fireEvent.press(view.getByText("Follow"));
    await waitFor(() => expect(view.queryByText("Nope.")).toBeTruthy());
    // Still not following, and still 3 — the button does not get to claim a
    // follow the server declined to record.
    expect(view.queryByText("Following")).toBeNull();
    expect(view.getByText("3 followers · 1 posts")).toBeTruthy();
  });

  it("still says something when the failure carries no message of its own", async () => {
    mockToggleFollow.mockRejectedValue(new Error("Network request failed"));
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Follow")).toBeTruthy());

    fireEvent.press(view.getByText("Follow"));
    await waitFor(() => expect(view.queryByText("That did not go through.")).toBeTruthy());
    // The raw failure is not the sentence: "Network request failed" is a fact
    // about a socket, not about this presence.
    expect(view.queryByText("Network request failed")).toBeNull();
  });

  it("clears a stale refusal when the next attempt is made", async () => {
    const { PulseApiError } = jest.requireActual("../../api/pulseApi");
    mockToggleFollow.mockRejectedValueOnce(new PulseApiError("Try again in a moment.", 503));
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Follow")).toBeTruthy());

    fireEvent.press(view.getByText("Follow"));
    await waitFor(() => expect(view.queryByText("Try again in a moment.")).toBeTruthy());

    fireEvent.press(view.getByText("Follow"));
    await waitFor(() => expect(view.queryByText("Following")).toBeTruthy());
    // A message about the attempt that failed must not outlive the one that
    // worked, or a followed presence sits under a line saying it did not work.
    expect(view.queryByText("Try again in a moment.")).toBeNull();
  });

  it("does not offer a follow the server will refuse on an unpublished presence", async () => {
    mockGetPage.mockResolvedValue(page({ ...team, status: "UNPUBLISHED" }));
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Share")).toBeTruthy());

    expect(view.queryByText("Follow")).toBeNull();
    expect(
      view.getByText("Not published yet. Only the team can open this presence, and nobody can follow it.")
    ).toBeTruthy();
    expect(mockToggleFollow).not.toHaveBeenCalled();
  });

  it("says deactivated rather than unpublished when that is what it is", async () => {
    // Two different states the team put the presence into deliberately, and
    // the step back is different for each. One sentence for both would send an
    // owner looking for a Publish button that is not the one they need.
    mockGetPage.mockResolvedValue(page({ ...team, status: "DEACTIVATED" }));
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Share")).toBeTruthy());

    expect(view.queryByText("Follow")).toBeNull();
    expect(
      view.getByText("Deactivated. Only the team can open this presence, and nobody can follow it.")
    ).toBeTruthy();
  });

  it("keeps the follow control on a paused presence, which is still public", async () => {
    // PAUSED is a posting state, not a visibility state: the server keeps
    // answering follows for it, so withholding the button here would take away
    // a working control rather than hiding a dead one.
    mockGetPage.mockResolvedValue(page({ status: "PAUSED" }));
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Follow")).toBeTruthy());

    expect(view.queryByText(/Only the team can open this presence/)).toBeNull();
    fireEvent.press(view.getByText("Follow"));
    await waitFor(() => expect(view.queryByText("Following")).toBeTruthy());
  });

  it("says nothing about publishing on a presence that is published", async () => {
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Follow")).toBeTruthy());
    expect(view.queryByText(/Only the team can open this presence/)).toBeNull();
  });
});

describe("the page is drawn in its own type's colour", () => {
  // The accent table is tested on its own in `theme/presenceAccent.test.ts`.
  // What is tested here is only that it reaches the screen, on the controls
  // that carry a presence's identity — a table nothing reads is a very
  // well-tested constant.
  function flatten(style: unknown): Record<string, unknown> {
    return Object.assign({}, ...[style].flat(Infinity).filter(Boolean)) as Record<string, unknown>;
  }

  it("colours the identity controls from the page type, not from the app accent", async () => {
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Follow")).toBeTruthy());

    const artist = presenceAccent("ARTIST");
    // An artist is violet. `colors.accent` is teal, and was what every one of
    // these read before — so asserting the accent's own value here is not a
    // tautology: it is the difference between the type deciding and nothing
    // deciding.
    expect(flatten(view.getByText("Follow").props.style).color).toBe(artist.ink);
    expect(flatten(view.getByText("@nightsignal · Artist").props.style).color).toBe(artist.base);
    expect(flatten(view.getByText("Posts").props.style).color).toBe(artist.base);
    // The initial standing in for a missing avatar, and the ring around it.
    expect(flatten(view.getByText("N").props.style).color).toBe(artist.base);
  });

  it("fills the primary action itself, not only its caption", async () => {
    // The caption assertion above passes for a violet word on a teal button.
    // Colouring the ink and forgetting the fill is the likelier half to miss,
    // because it is the half that is not a word.
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Follow")).toBeTruthy());

    const button = view.getByTestId("page-follow");
    expect(flatten(button.props.style).backgroundColor).toBe(presenceAccent("ARTIST").base);
  });

  it("washes a coverless page in the accent instead of leaving a grey bar", async () => {
    // A presence with no cover is the state every presence starts in, so this
    // is the first thing anyone sees of a new page.
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Follow")).toBeTruthy());

    // LinearGradient turns colour strings into platform ints before they
    // reach props, so the expectation goes through the same conversion.
    expect(view.getByTestId("page-cover-wash").props.colors).toEqual(
      presenceAccent("ARTIST").wash.map((stop) => processColor(stop))
    );
  });

  it("draws a restaurant differently from an artist", async () => {
    // Two page types, one component, one screenshot apart. If the lookup were
    // hard-coded to a single hue every assertion above would still pass.
    mockGetPage.mockResolvedValue(page({ page_type: "RESTAURANT", handle: "ash", name: "Ash" }));
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Follow")).toBeTruthy());

    const drawn = flatten(view.getByText("@ash · Restaurant").props.style).color;
    expect(drawn).toBe(presenceAccent("RESTAURANT").base);
    expect(drawn).not.toBe(presenceAccent("ARTIST").base);
  });

  it("leaves the verified badge alone", async () => {
    // A verification badge is a claim about trust, and a trust marker that is
    // a different colour on every page is one people stop reading. It stays
    // brand teal on a violet page on purpose.
    mockGetPage.mockResolvedValue(page({ verified: true }));
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Verified")).toBeTruthy());

    expect(flatten(view.getByText("Verified").props.style).color).toBe(colors.accent);
  });
});
