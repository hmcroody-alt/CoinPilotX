/**
 * The Presence hub is the first thing an owner sees, and it had two ways of
 * being hollow.
 *
 * The first was a button. Artist cards carried "Insights" next to "Manage",
 * and both called `navigate("PagesHub", { focusPageId })` — the same screen,
 * the same argument, under a word naming a section that does not exist (the
 * management view calls its analytics Overview). A duplicate control is worse
 * than a missing one: it teaches people that buttons here do not mean anything.
 *
 * The second was the pitch. The two creation cards listed Releases, Fans,
 * Customers, Services and Insights — none of which are modules — and named the
 * artist shop Store when the tab is Merch. Services in particular was removed
 * deliberately, because Marketplace already carries service and booking
 * listings.
 *
 * What replaces both is measured rather than asserted: the card's setup line
 * is the server's own `modules` availability map, the same answer that decides
 * which tabs a visitor is shown, so the hub and the page cannot disagree.
 */
import React from "react";
import { fireEvent, render, waitFor } from "@testing-library/react-native";

const mockListMyPages = jest.fn();
jest.mock("../../api/pages", () => ({
  ...jest.requireActual("../../api/pages"),
  listMyPages: (...args: unknown[]) => mockListMyPages(...args)
}));

import { PresenceHubScreen } from "../PresenceHubScreen";
import { presenceAccent } from "../../theme/presenceAccent";
import { presenceTheme } from "../../theme/presenceTheme";

function nav() {
  return { navigate: jest.fn(), addListener: jest.fn(() => jest.fn()) };
}

function presence(overrides: Record<string, unknown> = {}) {
  return {
    id: 7,
    page_type: "ARTIST",
    category: "",
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
    posts_count: 2,
    tabs: ["posts", "music", "events", "about"],
    modules: { posts: true, music: true, events: false, about: true },
    role: "OWNER",
    ...overrides
  };
}

function show() {
  const navigation = nav();
  const view = render(<PresenceHubScreen navigation={navigation as never} route={{} as never} />);
  return { view, navigation };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockListMyPages.mockResolvedValue([presence()]);
});

describe("every control on a presence card goes somewhere of its own", () => {
  it("does not offer a second button to the screen Manage already opens", async () => {
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Night Signal")).toBeTruthy());
    expect(view.queryByText("Manage")).toBeTruthy();
    // "Insights" navigated to `PagesHub` with the same focus id as Manage, and
    // named a section the management view does not have.
    expect(view.queryByText("Insights")).toBeNull();
  });

  it("sends Manage and View to different places", async () => {
    const { view, navigation } = show();
    await waitFor(() => expect(view.queryByText("Night Signal")).toBeTruthy());
    fireEvent.press(view.getByText("Manage"));
    fireEvent.press(view.getByText("View"));
    expect(navigation.navigate).toHaveBeenCalledWith("PagesHub", { focusPageId: 7 });
    expect(navigation.navigate).toHaveBeenCalledWith("Page", {
      pageId: 7,
      handle: "nightsignal",
      title: "Night Signal"
    });
  });

  it("keeps Business OS on a business presence, where there is a third place to go", async () => {
    mockListMyPages.mockResolvedValue([
      presence({ page_type: "BUSINESS", name: "Vault Coffee", business_os_capable: true })
    ]);
    const { view, navigation } = show();
    await waitFor(() => expect(view.queryByText("Vault Coffee")).toBeTruthy());
    fireEvent.press(view.getByText("Business OS"));
    expect(navigation.navigate).toHaveBeenCalledWith("BusinessOs", { title: "Vault Coffee" });
  });

  it("does not invent a third door for an artist to keep the cards symmetrical", async () => {
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Night Signal")).toBeTruthy());
    expect(view.queryByText("Business OS")).toBeNull();
  });

  it("asks the server which presences have a Business OS rather than deciding here", async () => {
    // This file used to carry its own copy of the server's
    // `BUSINESS_PAGE_TYPES`, as two frozensets, and the copy had drifted: a
    // type in neither set fell through to "business", so an OTHER presence was
    // offered a door the server does not think it has. A page type is a string
    // on both sides of the wire, so nothing could see the two disagree.
    //
    // The type below is a real one the copy got wrong. The button is withheld
    // because the server said so, not because this file now names it in the
    // right set — the fixture keeps the type and flips only the server's word.
    mockListMyPages.mockResolvedValue([
      presence({ page_type: "OTHER", name: "Loose Ends", business_os_capable: false })
    ]);
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Loose Ends")).toBeTruthy());
    expect(view.queryByText("Business OS")).toBeNull();
  });

  it("withholds the door when an older server does not say either way", async () => {
    // A missing field must read as "no". A shorter card is a smaller loss than
    // a button that lands on a Business OS this presence has no seat in, and
    // this is the same presence type the test above is told yes about.
    mockListMyPages.mockResolvedValue([
      presence({ page_type: "BUSINESS", name: "Vault Coffee", business_os_capable: undefined })
    ]);
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Vault Coffee")).toBeTruthy());
    expect(view.queryByText("Business OS")).toBeNull();
  });
});

describe("the card reports what the server measured", () => {
  it("names the modules that are not set up yet", async () => {
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Not set up yet: Events")).toBeTruthy());
  });

  it("never lists a module the server reported as backed", async () => {
    mockListMyPages.mockResolvedValue([
      presence({ modules: { posts: true, music: false, events: false, about: true } })
    ]);
    const { view } = show();
    // Ordering follows the server's ceiling, and a backed module is absent —
    // the exact string is the assertion, so an extra name cannot slip in.
    await waitFor(() => expect(view.queryByText("Not set up yet: Music, Events")).toBeTruthy());
    expect(view.queryByText(/Not set up yet:.*Posts/)).toBeNull();
    expect(view.queryByText(/Not set up yet:.*About/)).toBeNull();
  });

  it("says nothing at all when there is nothing outstanding", async () => {
    // A card that always carries a line is a line people stop reading.
    mockListMyPages.mockResolvedValue([
      presence({ modules: { posts: true, music: true, events: true, about: true } })
    ]);
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Night Signal")).toBeTruthy());
    expect(view.queryByText(/Not set up yet/)).toBeNull();
  });

  it("stays silent rather than guessing when the server sends no availability map", async () => {
    // An older server omitting `modules` must not be read as "nothing is set
    // up" — that would tell the owner their music is missing because a field
    // did not arrive.
    mockListMyPages.mockResolvedValue([presence({ modules: undefined })]);
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Night Signal")).toBeTruthy());
    expect(view.queryByText(/Not set up yet/)).toBeNull();
  });

  it("counts followers and posts from the server's own numbers", async () => {
    const { view } = show();
    await waitFor(() => expect(view.queryByText(/3 followers/)).toBeTruthy());
    expect(view.queryByText(/2 posts/)).toBeTruthy();
  });

  it("does not write '1 followers'", async () => {
    mockListMyPages.mockResolvedValue([presence({ followers_count: 1, posts_count: 1 })]);
    const { view } = show();
    await waitFor(() => expect(view.queryByText(/1 follower ·/)).toBeTruthy());
    expect(view.queryByText(/1 followers/)).toBeNull();
    expect(view.queryByText(/1 posts/)).toBeNull();
  });
});

describe("the creation pitch names only things that exist", () => {
  it("does not advertise modules the product does not have", async () => {
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Artist Presence")).toBeTruthy());
    for (const invented of [/Releases/, /Fans/, /Customers/, /Services/, /Insights/]) {
      expect(view.queryByText(invented)).toBeNull();
    }
  });

  it("does not promise a fixed module set it cannot keep for every type", async () => {
    // The artist flavor covers ARTIST, CREATOR, PUBLIC_FIGURE and SPORTS_TEAM,
    // and their ceilings differ — a public figure gets videos and no music. So
    // the pitch says the set depends on the type rather than listing one.
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Artist Presence")).toBeTruthy());
    // Both cards carry the caveat, so this counts them rather than expecting
    // one — `queryByText` treats two matches as an error.
    expect(view.queryAllByText(/depends on the type you pick next/)).toHaveLength(2);
  });
});

describe("the cards are told apart by more than their names", () => {
  // This list is the one screen where a member sees all their presences at
  // once, so it is the one screen where the type accent has a job beyond
  // decoration: telling a restaurant from an artist page at a glance.
  function flatten(style: unknown): Record<string, unknown> {
    return Object.assign({}, ...[style].flat(Infinity).filter(Boolean)) as Record<string, unknown>;
  }

  it("draws each card in its own type's colour", async () => {
    mockListMyPages.mockResolvedValue([
      presence({ id: 1, page_type: "ARTIST", name: "Night Signal" }),
      presence({ id: 2, page_type: "RESTAURANT", name: "Ash", handle: "ash" })
    ]);
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Ash")).toBeTruthy());

    // The avatar initial is the accent's only text on this card, so it is what
    // the assertion can reach. Both are present, and they differ.
    const artist = flatten(view.getAllByText("N")[0].props.style).color;
    const restaurant = flatten(view.getByText("A").props.style).color;
    expect(artist).toBe(presenceAccent("ARTIST").base);
    expect(restaurant).toBe(presenceAccent("RESTAURANT").base);
    expect(artist).not.toBe(restaurant);
  });

  it("runs the colour down the card, not only through the avatar", async () => {
    // The initial is two characters wide and only shows on a presence with no
    // avatar uploaded — which is the minority of them, and never the ones a
    // member has actually finished setting up. The spine is what survives an
    // avatar, so it is the part that has to be asserted separately: without
    // this, dropping `borderLeftColor` left every card edged in neutral grey
    // and the test above went on passing.
    mockListMyPages.mockResolvedValue([
      presence({ id: 1, page_type: "ARTIST", name: "Night Signal", avatar_url: "https://x/a.png" }),
      presence({ id: 2, page_type: "RESTAURANT", name: "Ash", handle: "ash", avatar_url: "https://x/b.png" })
    ]);
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Ash")).toBeTruthy());

    expect(flatten(view.getByTestId("presence-card-1").props.style).borderLeftColor).toBe(
      presenceAccent("ARTIST").base
    );
    expect(flatten(view.getByTestId("presence-card-2").props.style).borderLeftColor).toBe(
      presenceAccent("RESTAURANT").base
    );
  });

  it("leaves the state badges alone", async () => {
    // Public and Verified are claims about state and trust. A trust marker
    // that is a different colour on every card is one people stop reading, so
    // these stay brand teal on a violet card on purpose.
    mockListMyPages.mockResolvedValue([presence({ verified: true })]);
    const { view } = show();
    await waitFor(() => expect(view.queryByText("Public")).toBeTruthy());

    expect(flatten(view.getByText("Public").props.style).color).toBe(presenceTheme.teal);
    expect(flatten(view.getByText("✓ Verified").props.style).color).toBe(presenceTheme.teal);
  });
});
