/**
 * `setPageLink` and `listPageLinks` shipped in the client with no caller
 * anywhere. A link is what raises the Shop and Music tabs, so every presence
 * in the app had tabs that could never turn on — not broken-looking, just
 * permanently empty.
 *
 * This screen is that missing caller, and the things worth pinning are the
 * ones that would quietly re-create the old state:
 *
 *   1. Members pick a thing by its name. The moment this screen asks for a
 *      ref id it is unusable, and an id typed by hand is also how a presence
 *      came to be able to point at somebody else's storefront.
 *   2. What a role may change is the server's answer, not a guess from the
 *      role name — and a role that may change nothing is told why rather than
 *      shown a dead control.
 *   3. "You own nothing to connect" and "you may not connect" are different
 *      answers to different questions and must not collapse into one message.
 */
import React from "react";
import { fireEvent, render, waitFor } from "@testing-library/react-native";

const mockGetPageLinkOptions = jest.fn();
const mockSetPageLink = jest.fn();
const mockClearPageLink = jest.fn();
jest.mock("../../api/pages", () => ({
  ...jest.requireActual("../../api/pages"),
  getPageLinkOptions: (...args: unknown[]) => mockGetPageLinkOptions(...args),
  setPageLink: (...args: unknown[]) => mockSetPageLink(...args),
  clearPageLink: (...args: unknown[]) => mockClearPageLink(...args)
}));

import { PageConnectionsScreen } from "../PageConnectionsScreen";

function slot(overrides: Record<string, unknown> = {}) {
  return {
    link_type: "store",
    label: "Shop",
    permission: "manage_marketplace",
    can_manage: true,
    connected_ref_id: "",
    options: [{ ref_id: "42", label: "Night Signal Merch" }],
    ...overrides
  };
}

function options(links: unknown[] = [slot()]) {
  return { page_id: 41, role: "OWNER", links };
}

function renderScreen() {
  const navigation = { navigate: jest.fn(), goBack: jest.fn(), setOptions: jest.fn() };
  const utils = render(
    <PageConnectionsScreen
      route={{ key: "c", name: "PageConnections", params: { pageId: 41 } } as never}
      navigation={navigation as never}
    />
  );
  return { ...utils, navigation };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockGetPageLinkOptions.mockResolvedValue(options());
  mockSetPageLink.mockResolvedValue({ ok: true, link: { link_type: "store", ref_id: "42" } });
  mockClearPageLink.mockResolvedValue({ ok: true, link: { link_type: "store", ref_id: "" } });
});

/**
 * Connecting was a one-way door. There was no unlink route on the server and no
 * caller for one here, so a shop attached to a presence stayed attached — and
 * because `set_link` appended rather than replaced, connecting a different one
 * did not even take the first away.
 *
 * The case that matters most is the one where the connected thing is not in the
 * options list: a marketplace manager may connect their own storefront, and if
 * they later leave the team their seller id is no longer among anything the
 * owner is offered. The owner is then the only person who can act and the only
 * person with no control to act with.
 */
describe("taking a connection back", () => {
  it("offers to disconnect what is connected", async () => {
    mockGetPageLinkOptions.mockResolvedValue(options([slot({ connected_ref_id: "42" })]));
    const { getByText } = renderScreen();
    await waitFor(() => expect(getByText("Disconnect shop")).toBeTruthy());

    fireEvent.press(getByText("Disconnect shop"));
    await waitFor(() => expect(mockClearPageLink).toHaveBeenCalledWith(41, "store"));
  });

  it("offers nothing to disconnect when nothing is connected", async () => {
    const { getByText, queryByText } = renderScreen();
    await waitFor(() => expect(getByText("Night Signal Merch")).toBeTruthy());
    expect(queryByText("Disconnect shop")).toBeNull();
  });

  it("can disconnect something that is no longer among the options", async () => {
    // The manager who attached this shop has left, so the owner is offered
    // nothing that matches it. Hanging the control off a matching option would
    // make exactly this connection permanent.
    mockGetPageLinkOptions.mockResolvedValue(
      options([slot({ connected_ref_id: "77", options: [] })])
    );
    const { getByText } = renderScreen();
    await waitFor(() => expect(getByText("Connected: 77")).toBeTruthy());

    fireEvent.press(getByText("Disconnect shop"));
    await waitFor(() => expect(mockClearPageLink).toHaveBeenCalledWith(41, "store"));
  });

  it("does not offer it to a role that may not change the connection", async () => {
    mockGetPageLinkOptions.mockResolvedValue(
      options([slot({ can_manage: false, connected_ref_id: "42", options: [] })])
    );
    const { getByText, queryByText } = renderScreen();
    await waitFor(() => expect(getByText("Connected: 42")).toBeTruthy());
    expect(queryByText("Disconnect shop")).toBeNull();
  });

  it("re-reads from the server rather than assuming the tab is gone", async () => {
    mockGetPageLinkOptions.mockResolvedValue(options([slot({ connected_ref_id: "42" })]));
    const { getByText } = renderScreen();
    await waitFor(() => expect(getByText("Disconnect shop")).toBeTruthy());
    expect(mockGetPageLinkOptions).toHaveBeenCalledTimes(1);

    mockGetPageLinkOptions.mockResolvedValue(options());
    fireEvent.press(getByText("Disconnect shop"));

    await waitFor(() => expect(mockGetPageLinkOptions).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(getByText("Not connected yet.")).toBeTruthy());
  });

  it("surfaces the server's refusal rather than reporting success", async () => {
    const { PulseApiError } = jest.requireActual("../../api/pulseApi");
    mockClearPageLink.mockRejectedValue(
      new PulseApiError("You don't have permission to do that on this page.", 403)
    );
    mockGetPageLinkOptions.mockResolvedValue(options([slot({ connected_ref_id: "42" })]));
    const { getByText, queryByText } = renderScreen();
    await waitFor(() => expect(getByText("Disconnect shop")).toBeTruthy());

    fireEvent.press(getByText("Disconnect shop"));

    await waitFor(() =>
      expect(getByText("You don't have permission to do that on this page.")).toBeTruthy()
    );
    expect(queryByText("Shop is no longer connected.")).toBeNull();
  });

  it("says which connection it took down", async () => {
    mockGetPageLinkOptions.mockResolvedValue(options([slot({ connected_ref_id: "42" })]));
    const { getByText } = renderScreen();
    await waitFor(() => expect(getByText("Disconnect shop")).toBeTruthy());

    fireEvent.press(getByText("Disconnect shop"));
    await waitFor(() => expect(getByText("Shop is no longer connected.")).toBeTruthy());
  });
});

describe("PageConnectionsScreen", () => {
  it("offers what the member holds, by name", async () => {
    const { getByText, queryByText } = renderScreen();
    await waitFor(() => expect(getByText("Night Signal Merch")).toBeTruthy());
    // The id is carried for the connect call and never put in front of anyone.
    expect(queryByText("42")).toBeNull();
  });

  it("connects the option the member chose", async () => {
    const { getByText } = renderScreen();
    await waitFor(() => expect(getByText("Night Signal Merch")).toBeTruthy());

    fireEvent.press(getByText("Night Signal Merch"));

    await waitFor(() => expect(mockSetPageLink).toHaveBeenCalled());
    expect(mockSetPageLink).toHaveBeenCalledWith(41, "store", "42");
  });

  it("re-reads from the server after connecting instead of assuming", async () => {
    const { getByText } = renderScreen();
    await waitFor(() => expect(getByText("Night Signal Merch")).toBeTruthy());
    expect(mockGetPageLinkOptions).toHaveBeenCalledTimes(1);

    mockGetPageLinkOptions.mockResolvedValue(
      options([slot({ connected_ref_id: "42" })])
    );
    fireEvent.press(getByText("Night Signal Merch"));

    // Connecting one thing can change what else is offered, so the screen
    // asks again rather than patching its own copy.
    await waitFor(() => expect(mockGetPageLinkOptions).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(getByText("Connected: Night Signal Merch")).toBeTruthy());
  });

  it("says what a connection is already pointing at", async () => {
    mockGetPageLinkOptions.mockResolvedValue(options([slot({ connected_ref_id: "42" })]));
    const { getByText } = renderScreen();
    await waitFor(() => expect(getByText("Connected: Night Signal Merch")).toBeTruthy());
  });

  it("will not re-send a connection that is already in place", async () => {
    mockGetPageLinkOptions.mockResolvedValue(options([slot({ connected_ref_id: "42" })]));
    const { getByText } = renderScreen();
    await waitFor(() => expect(getByText("Connected: Night Signal Merch")).toBeTruthy());

    fireEvent.press(getByText("Night Signal Merch"));
    expect(mockSetPageLink).not.toHaveBeenCalled();
  });

  it("surfaces the server's refusal rather than a generic failure", async () => {
    const { PulseApiError } = jest.requireActual("../../api/pulseApi");
    mockSetPageLink.mockRejectedValue(new PulseApiError("You can only connect something you own.", 403));
    const { getByText } = renderScreen();
    await waitFor(() => expect(getByText("Night Signal Merch")).toBeTruthy());

    fireEvent.press(getByText("Night Signal Merch"));

    await waitFor(() => expect(getByText("You can only connect something you own.")).toBeTruthy());
  });

  describe("what a role may change", () => {
    it("explains the refusal instead of showing a control that fails", async () => {
      mockGetPageLinkOptions.mockResolvedValue(
        options([slot({ can_manage: false, options: [] })])
      );
      const { getByText, queryByText } = renderScreen();

      await waitFor(() =>
        expect(
          getByText("Only an owner, admin or marketplace manager can change the shop.")
        ).toBeTruthy()
      );
      expect(queryByText("Night Signal Merch")).toBeNull();
    });

    /**
     * The two "nothing to show" cases have different answers: one is "go make
     * one", the other is "ask someone with the seat". Collapsing them sends
     * members to fix the wrong thing.
     */
    it("distinguishes owning nothing from being unable to connect", async () => {
      mockGetPageLinkOptions.mockResolvedValue(
        options([slot({ can_manage: true, options: [] })])
      );
      const { getByText } = renderScreen();

      await waitFor(() =>
        expect(
          getByText(/You don't have shop to connect yet\./)
        ).toBeTruthy()
      );
    });
  });

  it("reports a load failure rather than rendering an empty page as 'nothing to connect'", async () => {
    mockGetPageLinkOptions.mockRejectedValue(new Error("network"));
    const { getByText, queryByText } = renderScreen();

    await waitFor(() => expect(getByText("Your connections could not be loaded.")).toBeTruthy());
    expect(queryByText("There is nothing to connect for this kind of presence.")).toBeNull();
  });
});
