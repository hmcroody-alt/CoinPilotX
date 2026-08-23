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
import { fireEvent, render, waitFor } from "@testing-library/react-native";

const mockListMyPages = jest.fn();
const mockListMyPageInvites = jest.fn();
const mockAcceptPageInvite = jest.fn();
const mockDeclinePageInvite = jest.fn();
const mockGetPageManageView = jest.fn();

jest.mock("../../api/pages", () => ({
  ...jest.requireActual("../../api/pages"),
  listMyPages: (...args: unknown[]) => mockListMyPages(...args),
  listMyPageInvites: (...args: unknown[]) => mockListMyPageInvites(...args),
  acceptPageInvite: (...args: unknown[]) => mockAcceptPageInvite(...args),
  declinePageInvite: (...args: unknown[]) => mockDeclinePageInvite(...args),
  getPageManageView: (...args: unknown[]) => mockGetPageManageView(...args)
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

beforeEach(() => {
  jest.clearAllMocks();
  mockListMyPages.mockResolvedValue([]);
  mockListMyPageInvites.mockResolvedValue([invite()]);
  mockAcceptPageInvite.mockResolvedValue({ ok: true, membership: { page_id: 41, role: "ADMIN" } });
  mockDeclinePageInvite.mockResolvedValue({ ok: true, page_id: 41, status: "declined" });
  mockGetPageManageView.mockResolvedValue(null);
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
