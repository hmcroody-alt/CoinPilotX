/**
 * Six member-management calls shipped in the client with no caller anywhere:
 * `listPageMembers`, `invitePageMember`, `acceptPageInvite`,
 * `changePageMemberRole`, `removePageMember`, `transferPageOwnership`. The
 * server has modelled a seven-role team since the beginning; the app could
 * only ever be a one-account presence, so sharing one meant sharing a
 * password.
 *
 * What is pinned here is what would quietly restore that state:
 *
 *   1. People are named, not numbered. The old `PageMember` type declared
 *      `username`/`display_name` while the server sent `name`/`handle` — both
 *      optional, so nothing errored and every member rendered as "Member 22".
 *      That is the exact failure this screen exists to not repeat.
 *   2. A control is only rendered when the server said this caller may make
 *      that call. Re-deriving permission from the role name is how a client
 *      drifts from the server and starts offering buttons that 403.
 *   3. Ownership is not a role. It moves only through the server's literal
 *      confirmation phrase, and the screen must not carry its own copy.
 *   4. After any change the screen re-reads. One role change can alter what is
 *      offered for everyone else.
 */
import React from "react";
import { fireEvent, render, waitFor } from "@testing-library/react-native";

const mockGetPageTeam = jest.fn();
const mockInvitePageMember = jest.fn();
const mockChangePageMemberRole = jest.fn();
const mockRemovePageMember = jest.fn();
const mockTransferPageOwnership = jest.fn();

jest.mock("../../api/pages", () => ({
  ...jest.requireActual("../../api/pages"),
  getPageTeam: (...args: unknown[]) => mockGetPageTeam(...args),
  invitePageMember: (...args: unknown[]) => mockInvitePageMember(...args),
  changePageMemberRole: (...args: unknown[]) => mockChangePageMemberRole(...args),
  removePageMember: (...args: unknown[]) => mockRemovePageMember(...args),
  transferPageOwnership: (...args: unknown[]) => mockTransferPageOwnership(...args)
}));

import { PageTeamScreen } from "../PageTeamScreen";

const ASSIGNABLE = [
  "ADMIN",
  "MANAGER",
  "CONTENT_MANAGER",
  "ADVERTISING_MANAGER",
  "MARKETPLACE_MANAGER",
  "ANALYST"
];

function member(overrides: Record<string, unknown> = {}) {
  return {
    user_id: 22,
    name: "Friend",
    handle: "friend",
    avatar_url: "",
    role: "ANALYST",
    status: "active",
    is_owner: false,
    is_you: false,
    can_change_role: true,
    can_remove: true,
    can_receive_ownership: false,
    ...overrides
  };
}

function owner(overrides: Record<string, unknown> = {}) {
  return member({
    user_id: 11,
    name: "Roody",
    handle: "roody",
    role: "OWNER",
    is_owner: true,
    is_you: true,
    can_change_role: false,
    can_remove: false,
    can_receive_ownership: false,
    ...overrides
  });
}

function team(overrides: Record<string, unknown> = {}) {
  return {
    page_id: 41,
    role: "OWNER",
    owner_user_id: 11,
    can_manage_members: true,
    can_transfer_ownership: true,
    assignable_roles: ASSIGNABLE,
    transfer_confirm_phrase: "TRANSFER",
    members: [owner(), member()],
    ...overrides
  };
}

function renderScreen() {
  const navigation = { navigate: jest.fn(), goBack: jest.fn(), setOptions: jest.fn() };
  const utils = render(
    <PageTeamScreen
      route={{ key: "t", name: "PageTeam", params: { pageId: 41 } } as never}
      navigation={navigation as never}
    />
  );
  return { ...utils, navigation };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockGetPageTeam.mockResolvedValue(team());
  mockInvitePageMember.mockResolvedValue({ ok: true, invite: { role: "ANALYST" } });
  mockChangePageMemberRole.mockResolvedValue({ ok: true });
  mockRemovePageMember.mockResolvedValue({ ok: true });
  mockTransferPageOwnership.mockResolvedValue({ ok: true });
});

describe("PageTeamScreen", () => {
  it("names the people on the team instead of numbering them", async () => {
    const { getByText, queryByText } = renderScreen();
    await waitFor(() => expect(getByText(/Friend/)).toBeTruthy());
    expect(getByText(/Roody/)).toBeTruthy();
    // The bug this screen replaced: `display_name || username` never matched
    // the server's `name`/`handle`, so the id fallback always won.
    expect(queryByText(/Member 22/)).toBeNull();
    expect(queryByText(/Member 11/)).toBeNull();
  });

  it("falls back to a handle before an id when someone has no name", async () => {
    mockGetPageTeam.mockResolvedValue(team({ members: [owner(), member({ name: "" })] }));
    const { getByText, queryByText } = renderScreen();
    await waitFor(() => expect(getByText(/@friend/)).toBeTruthy());
    expect(queryByText(/Member 22/)).toBeNull();
  });

  it("marks which row is you", async () => {
    const { getByText } = renderScreen();
    await waitFor(() => expect(getByText(/Roody \(you\)/)).toBeTruthy());
  });

  describe("what this caller may change", () => {
    it("offers no team controls to a role the server says cannot manage", async () => {
      mockGetPageTeam.mockResolvedValue(
        team({
          role: "ANALYST",
          can_manage_members: false,
          can_transfer_ownership: false,
          members: [
            owner({ is_you: false }),
            member({ can_change_role: false, can_remove: false })
          ]
        })
      );
      const { getByText, queryByText } = renderScreen();

      await waitFor(() =>
        expect(
          getByText("Only an owner or admin can invite people or change what someone can do.")
        ).toBeTruthy()
      );
      // Seeing who you work with is not privileged; handing out seats is.
      expect(getByText(/Friend/)).toBeTruthy();
      expect(queryByText("Send invite")).toBeNull();
      expect(queryByText("Manage")).toBeNull();
    });

    it("does not offer to edit the owner's seat", async () => {
      const { getAllByText, getByText } = renderScreen();
      await waitFor(() => expect(getByText(/Roody/)).toBeTruthy());
      // change_role and remove_member both refuse an OWNER target with 403.
      expect(
        getByText(
          "The owner's role can't be changed or removed here. Ownership moves only through a transfer."
        )
      ).toBeTruthy();
      // Exactly one Manage control: the non-owner row.
      expect(getAllByText("Manage")).toHaveLength(1);
    });

    /**
     * The three per-member flags are separate server answers and have to be
     * honoured separately. Checking them only through the owner row — where
     * all three happen to be false at once — would let any one of them be
     * ignored without a test noticing.
     */
    it("honours can_remove without can_change_role", async () => {
      mockGetPageTeam.mockResolvedValue(
        team({ members: [owner(), member({ can_change_role: false, can_remove: true })] })
      );
      const { getByText, queryByLabelText } = renderScreen();
      await waitFor(() => expect(getByText("Manage")).toBeTruthy());
      fireEvent.press(getByText("Manage"));

      expect(getByText("Remove Friend from the team")).toBeTruthy();
      expect(queryByLabelText("Make Friend Manager")).toBeNull();
    });

    it("honours can_change_role without can_remove", async () => {
      mockGetPageTeam.mockResolvedValue(
        team({ members: [owner(), member({ can_change_role: true, can_remove: false })] })
      );
      const { getByLabelText, getByText, queryByText } = renderScreen();
      await waitFor(() => expect(getByText("Manage")).toBeTruthy());
      fireEvent.press(getByText("Manage"));

      expect(getByLabelText("Make Friend Manager")).toBeTruthy();
      expect(queryByText("Remove Friend from the team")).toBeNull();
    });
  });

  it("changes a role to the one the member chose", async () => {
    const { getByLabelText, getByText } = renderScreen();
    await waitFor(() => expect(getByText("Manage")).toBeTruthy());

    fireEvent.press(getByText("Manage"));
    fireEvent.press(getByLabelText("Make Friend Content manager"));

    await waitFor(() => expect(mockChangePageMemberRole).toHaveBeenCalled());
    expect(mockChangePageMemberRole).toHaveBeenCalledWith(41, 22, "CONTENT_MANAGER");
  });

  it("takes the assignable roles from the server rather than its own list", async () => {
    mockGetPageTeam.mockResolvedValue(
      team({ assignable_roles: ["ADMIN"], members: [owner(), member({ role: "ANALYST" })] })
    );
    const { getByLabelText, getByText, queryByLabelText } = renderScreen();
    await waitFor(() => expect(getByText("Manage")).toBeTruthy());
    fireEvent.press(getByText("Manage"));

    expect(getByLabelText("Make Friend Admin")).toBeTruthy();
    // A role the server did not offer must not be offerable here — not in the
    // role panel and not in the invite form either.
    expect(queryByLabelText("Make Friend Marketplace manager")).toBeNull();
    expect(queryByLabelText("Invite as Marketplace manager")).toBeNull();
  });

  it("re-reads the team after a change instead of patching its own copy", async () => {
    const { getByLabelText, getByText } = renderScreen();
    await waitFor(() => expect(getByText("Manage")).toBeTruthy());
    expect(mockGetPageTeam).toHaveBeenCalledTimes(1);

    fireEvent.press(getByText("Manage"));
    fireEvent.press(getByLabelText("Make Friend Manager"));

    await waitFor(() => expect(mockGetPageTeam).toHaveBeenCalledTimes(2));
  });

  it("removes the member the server said could be removed", async () => {
    const { getByText } = renderScreen();
    await waitFor(() => expect(getByText("Manage")).toBeTruthy());
    fireEvent.press(getByText("Manage"));

    fireEvent.press(getByText("Remove Friend from the team"));

    await waitFor(() => expect(mockRemovePageMember).toHaveBeenCalledWith(41, 22));
  });

  describe("inviting", () => {
    it("invites by handle and role", async () => {
      const { getByText, getByLabelText } = renderScreen();
      await waitFor(() => expect(getByText("Send invite")).toBeTruthy());

      fireEvent.changeText(getByLabelText("Handle to invite"), "@newperson");
      fireEvent.press(getByLabelText("Invite as Manager"));
      fireEvent.press(getByText("Send invite"));

      await waitFor(() => expect(mockInvitePageMember).toHaveBeenCalled());
      // The leading @ is a typing convention, not part of the handle.
      expect(mockInvitePageMember).toHaveBeenCalledWith(41, { handle: "newperson" }, "MANAGER");
    });

    it("will not send an invite with no role chosen", async () => {
      const { getByText, getByLabelText } = renderScreen();
      await waitFor(() => expect(getByText("Send invite")).toBeTruthy());

      fireEvent.changeText(getByLabelText("Handle to invite"), "newperson");
      fireEvent.press(getByText("Send invite"));

      expect(mockInvitePageMember).not.toHaveBeenCalled();
      expect(getByText("Choose what this person will be able to do.")).toBeTruthy();
    });

    it("says what the server said when an invite is refused", async () => {
      const { PulseApiError } = jest.requireActual("../../api/pulseApi");
      mockInvitePageMember.mockRejectedValue(new PulseApiError("No member with that handle.", 404));
      const { getByText, getByLabelText } = renderScreen();
      await waitFor(() => expect(getByText("Send invite")).toBeTruthy());

      fireEvent.changeText(getByLabelText("Handle to invite"), "ghost");
      fireEvent.press(getByLabelText("Invite as Analyst"));
      fireEvent.press(getByText("Send invite"));

      await waitFor(() => expect(getByText("No member with that handle.")).toBeTruthy());
    });
  });

  describe("ownership", () => {
    function transferable() {
      return team({
        members: [owner(), member({ role: "ADMIN", can_receive_ownership: true })]
      });
    }

    it("is not offered for someone the server says cannot receive it", async () => {
      const { getByText, queryByText } = renderScreen();
      await waitFor(() => expect(getByText("Manage")).toBeTruthy());
      fireEvent.press(getByText("Manage"));
      // The default fixture's member is active but not transfer-eligible; an
      // invite that has not been accepted is not yet a person who can own this.
      expect(queryByText("Make Friend the owner")).toBeNull();
    });

    it("requires the server's confirmation phrase, not the client's idea of one", async () => {
      mockGetPageTeam.mockResolvedValue(transferable());
      const { getByText, getByLabelText } = renderScreen();
      await waitFor(() => expect(getByText("Manage")).toBeTruthy());
      fireEvent.press(getByText("Manage"));
      fireEvent.press(getByText("Make Friend the owner"));

      expect(getByText("Type TRANSFER to confirm.")).toBeTruthy();

      fireEvent.changeText(getByLabelText("Ownership transfer confirmation"), "yes please");
      fireEvent.press(getByText("Hand this presence to Friend"));
      expect(mockTransferPageOwnership).not.toHaveBeenCalled();

      fireEvent.changeText(getByLabelText("Ownership transfer confirmation"), "TRANSFER");
      fireEvent.press(getByText("Hand this presence to Friend"));

      await waitFor(() => expect(mockTransferPageOwnership).toHaveBeenCalled());
      expect(mockTransferPageOwnership).toHaveBeenCalledWith(41, 22, "TRANSFER");
    });

    it("uses whatever phrase the server names, having no copy of its own", async () => {
      mockGetPageTeam.mockResolvedValue({
        ...transferable(),
        transfer_confirm_phrase: "HAND OVER"
      });
      const { getByText, getByLabelText } = renderScreen();
      await waitFor(() => expect(getByText("Manage")).toBeTruthy());
      fireEvent.press(getByText("Manage"));
      fireEvent.press(getByText("Make Friend the owner"));

      expect(getByText("Type HAND OVER to confirm.")).toBeTruthy();
      fireEvent.changeText(getByLabelText("Ownership transfer confirmation"), "HAND OVER");
      fireEvent.press(getByText("Hand this presence to Friend"));

      await waitFor(() =>
        expect(mockTransferPageOwnership).toHaveBeenCalledWith(41, 22, "HAND OVER")
      );
    });
  });

  it("reports a load failure rather than rendering an empty team", async () => {
    mockGetPageTeam.mockRejectedValue(new Error("network"));
    const { getByText, queryByText } = renderScreen();

    await waitFor(() => expect(getByText("The team could not be loaded.")).toBeTruthy());
    expect(queryByText("Send invite")).toBeNull();
  });
});
