/**
 * The invitee's half of the invite flow, which did not exist.
 *
 * `invitePageMember` returns the token to the person who *sent* the invite,
 * and the server pushes, mails and notifies nobody. So the only way onto a
 * team was for the inviter to copy a secret out of an API response and paste
 * it to you — the shared-credential habit the seven-role system exists to
 * replace, reintroduced one layer up. `acceptPageInvite` had shipped in the
 * client with zero callers to prove it.
 *
 * What is pinned here is what would quietly restore that state:
 *
 *   1. An invite is *findable* without anyone handing over a token.
 *   2. It says which presence, from whom, and as what — an invite that only
 *      says "you have an invite" is a prompt to click yes to anything.
 *   3. Declining is offered. Removal is gated on `manage_members`, which an
 *      invitee does not have, so without Decline the only exit from an
 *      unwanted invite is to accept it and ask to be let out.
 *   4. Accept and decline are distinct calls. Wiring both to accept is
 *      invisible in review and catastrophic in use.
 *   5. Both re-read from the server. The invite list and the page list are
 *      two different reads and accepting changes both.
 */
import React from "react";
import { fireEvent, render, waitFor, within } from "@testing-library/react-native";

// The hub mounts the composer, which reaches `expo-av` through the media
// preview, and `expo-av` wants the ExponentAV native module at import time.
// Stubbed rather than the composer itself, because a mocked composer would
// make "the composer is actually reachable from here" untestable — and that is
// the defect this screen exists to fix.
jest.mock("expo-av", () => ({
  ResizeMode: { COVER: "cover", CONTAIN: "contain" },
  Video: () => null,
  Audio: {}
}));

const mockListMyPages = jest.fn();
const mockListMyPageInvites = jest.fn();
const mockAcceptPageInvite = jest.fn();
const mockDeclinePageInvite = jest.fn();
const mockGetPageManageView = jest.fn();
const mockListPageIdentities = jest.fn();

jest.mock("../../api/pages", () => ({
  ...jest.requireActual("../../api/pages"),
  listMyPages: (...args: unknown[]) => mockListMyPages(...args),
  listMyPageInvites: (...args: unknown[]) => mockListMyPageInvites(...args),
  acceptPageInvite: (...args: unknown[]) => mockAcceptPageInvite(...args),
  declinePageInvite: (...args: unknown[]) => mockDeclinePageInvite(...args),
  getPageManageView: (...args: unknown[]) => mockGetPageManageView(...args),
  listPageIdentities: (...args: unknown[]) => mockListPageIdentities(...args)
}));

import { PagesHubScreen } from "../PagesHubScreen";

function invite(overrides: Record<string, unknown> = {}) {
  return {
    token: "tok-abc",
    role: "ADMIN",
    expires_at: "2099-01-01T00:00:00+00:00",
    expired: false,
    invited_at: "2026-01-01T00:00:00+00:00",
    page_id: 41,
    page_name: "Night Signal",
    page_handle: "nightsignal",
    page_avatar_url: "",
    page_type: "ARTIST",
    invited_by_name: "Roody",
    ...overrides
  };
}

function page(overrides: Record<string, unknown> = {}) {
  return {
    id: 41,
    name: "Night Signal",
    handle: "nightsignal",
    page_type: "ARTIST",
    avatar_url: "",
    role: "ADMIN",
    status: "ACTIVE",
    verified: false,
    ...overrides
  };
}

function renderScreen() {
  const navigation = { navigate: jest.fn(), goBack: jest.fn(), setOptions: jest.fn() };
  const utils = render(
    <PagesHubScreen
      route={{ key: "h", name: "PagesHub", params: {} } as never}
      navigation={navigation as never}
    />
  );
  return { ...utils, navigation };
}

function section(overrides: Record<string, unknown> = {}) {
  return {
    key: "identity",
    label: "Identity",
    hint: "Name, handle, description and contact details.",
    permission: "edit_page",
    permitted: true,
    ready: true,
    setup: "",
    ...overrides
  };
}

/** A manage view carrying exactly the sections a test cares about. */
function manageView(sections: Record<string, unknown>[]) {
  return {
    page: page(),
    role: "OWNER",
    capabilities: ["edit_page"],
    owner_user_id: 7,
    links: [],
    sections
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockListMyPages.mockResolvedValue([]);
  mockListMyPageInvites.mockResolvedValue([invite()]);
  mockAcceptPageInvite.mockResolvedValue({ ok: true, membership: { page_id: 41, role: "ADMIN" } });
  mockDeclinePageInvite.mockResolvedValue({ ok: true, page_id: 41, status: "declined" });
  mockGetPageManageView.mockResolvedValue(null);
  mockListPageIdentities.mockResolvedValue({
    personal: { kind: "personal", id: 0, name: "You" },
    pages: [{ kind: "page", id: 41, name: "Night Signal" }]
  });
});

describe("PagesHubScreen invite inbox", () => {
  it("shows an invite without anyone having to hand over the token", async () => {
    const { getByText } = renderScreen();
    await waitFor(() => expect(getByText(/Roody invited you to Night Signal/)).toBeTruthy());
  });

  it("says which presence, from whom and as what", async () => {
    // An invite that says only "you have an invite" is a prompt to accept
    // something unidentified.
    const { getByText } = renderScreen();
    await waitFor(() => expect(getByText(/Roody invited you to Night Signal/)).toBeTruthy());
    expect(getByText(/Artist/)).toBeTruthy();
    expect(getByText(/@nightsignal/)).toBeTruthy();
    expect(getByText(/as admin/)).toBeTruthy();
  });

  it("still names the presence when the inviter is unknown", async () => {
    mockListMyPageInvites.mockResolvedValue([invite({ invited_by_name: "" })]);
    const { getByText } = renderScreen();
    await waitFor(() => expect(getByText(/You've been invited to Night Signal/)).toBeTruthy());
  });

  it("offers no invite card when there are none", async () => {
    mockListMyPageInvites.mockResolvedValue([]);
    const { queryByText, getByText } = renderScreen();
    await waitFor(() => expect(getByText(/Create a Presence/)).toBeTruthy());
    expect(queryByText(/invited you to/)).toBeNull();
  });

  it("accepts with the token the server issued", async () => {
    const { getByLabelText } = renderScreen();
    await waitFor(() => expect(getByLabelText("Accept invite to Night Signal")).toBeTruthy());
    fireEvent.press(getByLabelText("Accept invite to Night Signal"));
    await waitFor(() => expect(mockAcceptPageInvite).toHaveBeenCalledWith("tok-abc"));
    expect(mockDeclinePageInvite).not.toHaveBeenCalled();
  });

  it("declines through decline, not through accept", async () => {
    // Wiring Decline to acceptPageInvite reads identically and does the
    // opposite of what the button says.
    const { getByLabelText } = renderScreen();
    await waitFor(() => expect(getByLabelText("Decline invite to Night Signal")).toBeTruthy());
    fireEvent.press(getByLabelText("Decline invite to Night Signal"));
    await waitFor(() => expect(mockDeclinePageInvite).toHaveBeenCalledWith("tok-abc"));
    expect(mockAcceptPageInvite).not.toHaveBeenCalled();
  });

  it("re-reads both the pages and the invites after accepting", async () => {
    // Accepting adds a page and clears an invite. Patching state locally lets
    // the screen disagree with the server about what happened.
    const { getByLabelText } = renderScreen();
    await waitFor(() => expect(getByLabelText("Accept invite to Night Signal")).toBeTruthy());
    expect(mockListMyPages).toHaveBeenCalledTimes(1);
    expect(mockListMyPageInvites).toHaveBeenCalledTimes(1);
    fireEvent.press(getByLabelText("Accept invite to Night Signal"));
    await waitFor(() => expect(mockListMyPages).toHaveBeenCalledTimes(2));
    expect(mockListMyPageInvites).toHaveBeenCalledTimes(2);
  });

  it("re-reads after declining too", async () => {
    const { getByLabelText } = renderScreen();
    await waitFor(() => expect(getByLabelText("Decline invite to Night Signal")).toBeTruthy());
    fireEvent.press(getByLabelText("Decline invite to Night Signal"));
    await waitFor(() => expect(mockListMyPageInvites).toHaveBeenCalledTimes(2));
  });

  it("drops the card once the server says the invite is gone", async () => {
    mockListMyPageInvites.mockResolvedValueOnce([invite()]).mockResolvedValue([]);
    mockListMyPages.mockResolvedValue([page()]);
    const { getByLabelText, queryByText } = renderScreen();
    await waitFor(() => expect(getByLabelText("Accept invite to Night Signal")).toBeTruthy());
    fireEvent.press(getByLabelText("Accept invite to Night Signal"));
    await waitFor(() => expect(queryByText(/invited you to/)).toBeNull());
  });

  it("confirms in words what the acceptance actually granted", async () => {
    const { getByLabelText, getByText } = renderScreen();
    await waitFor(() => expect(getByLabelText("Accept invite to Night Signal")).toBeTruthy());
    fireEvent.press(getByLabelText("Accept invite to Night Signal"));
    await waitFor(() => expect(getByText(/You're now admin of Night Signal\./)).toBeTruthy());
  });

  it("surfaces the server's refusal instead of pretending it worked", async () => {
    const { PulseApiError } = jest.requireActual("../../api/pulseApi");
    mockAcceptPageInvite.mockRejectedValue(new PulseApiError("This invite has expired.", 410));
    const { getByLabelText, getByText } = renderScreen();
    await waitFor(() => expect(getByLabelText("Accept invite to Night Signal")).toBeTruthy());
    fireEvent.press(getByLabelText("Accept invite to Night Signal"));
    await waitFor(() => expect(getByText("This invite has expired.")).toBeTruthy());
  });

  it("does not offer to accept an expired invite", async () => {
    mockListMyPageInvites.mockResolvedValue([invite({ expired: true })]);
    const { queryByLabelText, getByText } = renderScreen();
    await waitFor(() => expect(getByText(/This invite expired/)).toBeTruthy());
    // Offering a button the server will 410 is worse than offering none.
    expect(queryByLabelText("Accept invite to Night Signal")).toBeNull();
  });

  it("lets an expired invite be cleared away", async () => {
    mockListMyPageInvites.mockResolvedValue([invite({ expired: true })]);
    const { getByLabelText } = renderScreen();
    await waitFor(() => expect(getByLabelText("Dismiss expired invite from Night Signal")).toBeTruthy());
    fireEvent.press(getByLabelText("Dismiss expired invite from Night Signal"));
    await waitFor(() => expect(mockDeclinePageInvite).toHaveBeenCalledWith("tok-abc"));
  });

  it("names who to ask for a fresh invite", async () => {
    mockListMyPageInvites.mockResolvedValue([invite({ expired: true })]);
    const { getByText } = renderScreen();
    await waitFor(() => expect(getByText(/Ask Roody for a new one\./)).toBeTruthy());
  });

  it("shows every pending invite, not just the first", async () => {
    mockListMyPageInvites.mockResolvedValue([
      invite(),
      invite({ token: "tok-def", page_id: 42, page_name: "Day Signal", page_handle: "daysignal" })
    ]);
    const { getByText } = renderScreen();
    await waitFor(() => expect(getByText(/Roody invited you to Night Signal/)).toBeTruthy());
    expect(getByText(/Roody invited you to Day Signal/)).toBeTruthy();
  });

  it("acts on the invite that was pressed", async () => {
    mockListMyPageInvites.mockResolvedValue([
      invite(),
      invite({ token: "tok-def", page_id: 42, page_name: "Day Signal", page_handle: "daysignal" })
    ]);
    const { getByLabelText } = renderScreen();
    await waitFor(() => expect(getByLabelText("Accept invite to Day Signal")).toBeTruthy());
    fireEvent.press(getByLabelText("Accept invite to Day Signal"));
    await waitFor(() => expect(mockAcceptPageInvite).toHaveBeenCalledWith("tok-def"));
  });

  it("keeps the hub usable when the invite read fails", async () => {
    // An older server without the endpoint must not break the pages the user
    // actually came here for.
    mockListMyPageInvites.mockRejectedValue(new Error("404"));
    mockListMyPages.mockResolvedValue([page()]);
    const { getByText, queryByText } = renderScreen();
    await waitFor(() => expect(getByText("Night Signal")).toBeTruthy());
    expect(getByText(/Create a Presence/)).toBeTruthy();
    expect(queryByText(/invited you to/)).toBeNull();
  });
});

/**
 * The management surface, which used to be a fixed grid of buttons with no
 * relationship to the page in front of it. A media page was offered
 * Marketplace, an artist was offered Business OS, and Advertising opened
 * whether or not an ad account had ever been connected — a control that fails
 * after the tap, which is a worse answer than no control.
 *
 * The server now decides. What is pinned here is that the client does not
 * quietly re-decide:
 *
 *   1. Tiles come from `sections`. A section the server withheld is absent,
 *      not greyed out — greying out would still tell a media page it has a
 *      shop somewhere.
 *   2. `permitted` is obeyed, not re-derived from `capabilities`. Two sources
 *      for one answer is how a screen drifts into rendering 403s.
 *   3. A section that is not ready still shows, and its tap goes to the place
 *      that makes it ready — Connections — rather than to the empty thing
 *      behind it.
 *   4. Posts opens the composer here rather than navigating, because
 *      `createPagePost` was mounted on no screen at all and posting as a
 *      presence was therefore impossible in the app.
 */
describe("PagesHubScreen management sections", () => {
  beforeEach(() => {
    mockListMyPages.mockResolvedValue([page()]);
    mockListMyPageInvites.mockResolvedValue([]);
  });

  it("renders the sections the server sent", async () => {
    mockGetPageManageView.mockResolvedValue(
      manageView([section(), section({ key: "team", label: "Team & access", permission: "view_analytics" })])
    );
    const { getByText } = renderScreen();
    await waitFor(() => expect(getByText("Identity")).toBeTruthy());
    expect(getByText("Team & access")).toBeTruthy();
  });

  it("renders no tile for a section the server withheld", async () => {
    // An artist page has merch; a media page has no shop at all. Absent, not
    // disabled — a disabled shop still claims the shop exists.
    mockGetPageManageView.mockResolvedValue(manageView([section()]));
    const { getByText, queryByTestId } = renderScreen();
    await waitFor(() => expect(getByText("Identity")).toBeTruthy());
    expect(queryByTestId("section-store")).toBeNull();
    expect(queryByTestId("section-business_os")).toBeNull();
  });

  it("does not make a tile out of a section that lives on this screen", async () => {
    // Overview, Insights, Settings and Verification are the blocks already
    // rendered here. A tile for one would be a button that scrolls to what you
    // are looking at.
    mockGetPageManageView.mockResolvedValue(
      manageView([
        section({ key: "overview", label: "Overview", permission: "view_analytics" }),
        section({ key: "insights", label: "Insights", permission: "view_analytics" }),
        section({ key: "settings", label: "Settings", permission: "manage_status" }),
        section({ key: "verification", label: "Verification", permission: "manage_status" }),
        section()
      ])
    );
    const { getByTestId, queryByTestId } = renderScreen();
    await waitFor(() => expect(getByTestId("section-identity")).toBeTruthy());
    for (const key of ["overview", "insights", "settings", "verification"]) {
      expect(queryByTestId(`section-${key}`)).toBeNull();
    }
  });

  it("says what a ready section is for", async () => {
    mockGetPageManageView.mockResolvedValue(manageView([section()]));
    const { getByText } = renderScreen();
    await waitFor(() =>
      expect(getByText("Name, handle, description and contact details.")).toBeTruthy()
    );
  });

  it("says what is missing instead, when nothing is behind it yet", async () => {
    // The whole point of keeping an empty section visible: a team that cannot
    // see it cannot fill it.
    mockGetPageManageView.mockResolvedValue(
      manageView([
        section({
          key: "store",
          label: "Merch",
          permission: "manage_marketplace",
          hint: "What this presence sells.",
          ready: false,
          setup: "Connect a shop you already run."
        })
      ])
    );
    const { getByText, queryByText } = renderScreen();
    await waitFor(() => expect(getByText("Connect a shop you already run.")).toBeTruthy());
    expect(queryByText("What this presence sells.")).toBeNull();
  });

  it("shows a count only where the server measured one", async () => {
    mockGetPageManageView.mockResolvedValue(
      manageView([section({ key: "content", label: "Posts", permission: "create_content", count: 12 }), section()])
    );
    const { getByText, getByTestId } = renderScreen();
    await waitFor(() => expect(getByText("12")).toBeTruthy());
    // Identity has no count, so it renders no number — not a zero.
    expect(within(getByTestId("section-identity")).queryByText("0")).toBeNull();
  });

  it("opens identity editing for the page in front of you", async () => {
    mockGetPageManageView.mockResolvedValue(manageView([section()]));
    const { getByTestId, navigation } = renderScreen();
    await waitFor(() => expect(getByTestId("section-identity")).toBeTruthy());
    fireEvent.press(getByTestId("section-identity"));
    expect(navigation.navigate).toHaveBeenCalledWith("PageEdit", { pageId: 41, title: "Night Signal" });
  });

  it("sends a section with nothing connected to Connections, not to the empty thing behind it", async () => {
    // Opening an inventory screen that belongs to nobody is the failure this
    // replaced. The tap goes where the gap can be closed.
    mockGetPageManageView.mockResolvedValue(
      manageView([
        section({ key: "store", label: "Merch", permission: "manage_marketplace", ready: false, setup: "Connect a shop." })
      ])
    );
    const { getByTestId, navigation } = renderScreen();
    await waitFor(() => expect(getByTestId("section-store")).toBeTruthy());
    fireEvent.press(getByTestId("section-store"));
    expect(navigation.navigate).toHaveBeenCalledWith("PageConnections", { pageId: 41, title: "Night Signal" });
    expect(navigation.navigate).not.toHaveBeenCalledWith("MarketplaceManager", expect.anything());
  });

  it("sends a connected shop to the Marketplace manager it is actually wired to", async () => {
    mockGetPageManageView.mockResolvedValue(
      manageView([section({ key: "store", label: "Merch", permission: "manage_marketplace" })])
    );
    const { getByTestId, navigation } = renderScreen();
    await waitFor(() => expect(getByTestId("section-store")).toBeTruthy());
    fireEvent.press(getByTestId("section-store"));
    expect(navigation.navigate).toHaveBeenCalledWith("MarketplaceManager", { title: "Night Signal" });
  });

  it("obeys the server on what this role may do rather than deciding again", async () => {
    // `capabilities` deliberately contains edit_page here. A client that
    // re-derived permission from it would offer the tile the server refuses.
    mockGetPageManageView.mockResolvedValue(manageView([section({ permitted: false })]));
    const { getByTestId, getByText, navigation } = renderScreen();
    await waitFor(() => expect(getByTestId("section-identity")).toBeTruthy());
    expect(getByText("Your role can't change this.")).toBeTruthy();
    fireEvent.press(getByTestId("section-identity"));
    expect(navigation.navigate).not.toHaveBeenCalledWith("PageEdit", expect.anything());
    // And it is announced as inert. Swallowing the tap silently is not enough:
    // a screen reader told "button" about something that will never respond is
    // being lied to, and pressing it is the only way to find out.
    const tile = getByTestId("section-identity");
    expect(tile.props.accessibilityState).toMatchObject({ disabled: true });
    expect(tile.props.accessibilityRole).toBe("text");
  });

  it("opens the composer from Posts instead of navigating away", async () => {
    // `createPagePost` and the identity switcher shipped working and mounted
    // on no screen, so publishing as a presence was impossible in the app.
    mockGetPageManageView.mockResolvedValue(
      manageView([section({ key: "content", label: "Posts", permission: "create_content", count: 0 })])
    );
    const { getByTestId, findByText, navigation } = renderScreen();
    await waitFor(() => expect(getByTestId("section-content")).toBeTruthy());
    fireEvent.press(getByTestId("section-content"));
    expect(await findByText("Create Post")).toBeTruthy();
    expect(navigation.navigate).not.toHaveBeenCalled();
  });

  it("opens that composer already speaking as the presence, not as the person", async () => {
    // One tap apart, and the difference between a band announcing a show and
    // a person doing so.
    mockGetPageManageView.mockResolvedValue(
      manageView([section({ key: "content", label: "Posts", permission: "create_content" })])
    );
    const { getByTestId, findByText } = renderScreen();
    await waitFor(() => expect(getByTestId("section-content")).toBeTruthy());
    fireEvent.press(getByTestId("section-content"));
    expect(await findByText(/Posting as Night Signal/)).toBeTruthy();
  });

  it("still renders a section this build has no destination for", async () => {
    // A server that grows a section should not make it invisible on an older
    // client — but there is nowhere honest to send the tap, so it does not
    // pretend to be a button.
    mockGetPageManageView.mockResolvedValue(
      manageView([section({ key: "podcast", label: "Podcast", hint: "Episodes.", permission: "create_content" })])
    );
    const { getByTestId, getByText, navigation } = renderScreen();
    await waitFor(() => expect(getByTestId("section-podcast")).toBeTruthy());
    expect(getByText("Episodes.")).toBeTruthy();
    fireEvent.press(getByTestId("section-podcast"));
    expect(navigation.navigate).not.toHaveBeenCalled();
    const tile = getByTestId("section-podcast");
    expect(tile.props.accessibilityState).toMatchObject({ disabled: true });
    expect(tile.props.accessibilityRole).toBe("text");
  });

  it("takes the insights heading from the server too", async () => {
    mockGetPageManageView.mockResolvedValue({
      ...manageView([section({ key: "insights", label: "Insights", permission: "view_analytics" })]),
      analytics: { followers: 3, posts: 1, team_members: 2 }
    });
    const { getByText } = renderScreen();
    await waitFor(() => expect(getByText("Insights")).toBeTruthy());
    expect(getByText(/Followers: 3/)).toBeTruthy();
  });

  it("still shows the measured numbers when the server predates sections", async () => {
    // Real figures must not disappear because a label is missing.
    mockGetPageManageView.mockResolvedValue({
      page: page(),
      role: "OWNER",
      capabilities: [],
      owner_user_id: 7,
      links: [],
      analytics: { followers: 3, posts: 1, team_members: 2 }
    });
    const { getByText, queryByTestId } = renderScreen();
    await waitFor(() => expect(getByText(/Followers: 3/)).toBeTruthy());
    expect(queryByTestId("section-identity")).toBeNull();
  });
});
