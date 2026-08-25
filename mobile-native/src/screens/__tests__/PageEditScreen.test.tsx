/**
 * `updatePage` existed, was permission-checked, and was audited — and had no
 * caller anywhere in the app. Every identity field a member set while creating
 * a presence was frozen from that moment on: a typo in the handle, a
 * placeholder bio, a missing avatar, none of them fixable from the native app.
 *
 * This screen is that missing caller, so what matters here is not that the form
 * renders but that it sends the server something the server will honour:
 *
 *   1. Only fields the member actually changed. A whole-object PATCH would
 *      rewrite — and re-audit — values nobody touched.
 *   2. Nothing at all for a role without `edit_page`. The server refuses those
 *      anyway; a form that collects input and then 403s on save is a worse
 *      answer than one that never opened.
 *   3. A handle check scoped to this page. Unscoped, a page collides with
 *      itself and its own handle reports as "taken by another page", which
 *      blocks saving any *other* field on the form.
 */
import React from "react";
import { fireEvent, render, waitFor } from "@testing-library/react-native";

const mockGetPageManageView = jest.fn();
const mockUpdatePage = jest.fn();
const mockCheckPageHandle = jest.fn();
jest.mock("../../api/pages", () => ({
  ...jest.requireActual("../../api/pages"),
  getPageManageView: (...args: unknown[]) => mockGetPageManageView(...args),
  updatePage: (...args: unknown[]) => mockUpdatePage(...args),
  checkPageHandle: (...args: unknown[]) => mockCheckPageHandle(...args)
}));

const mockUploadNativeMedia = jest.fn();
jest.mock("../../media/nativeMediaUpload", () => ({
  uploadNativeMedia: (...args: unknown[]) => mockUploadNativeMedia(...args)
}));

jest.mock("expo-image-picker", () => ({
  requestMediaLibraryPermissionsAsync: jest.fn(async () => ({ granted: true })),
  launchImageLibraryAsync: jest.fn(async () => ({
    canceled: false,
    assets: [{ uri: "file:///tmp/pick.jpg", mimeType: "image/jpeg", fileName: "pick.jpg", fileSize: 2048, width: 800, height: 800 }]
  })),
  MediaTypeOptions: { Images: "Images" }
}));

import { PageEditScreen } from "../PageEditScreen";

const OWNER_CAPABILITIES = ["edit_page", "manage_links", "manage_members", "manage_status", "view_analytics"];

/** The manage view as `getPageManageView` hands it back, already normalized. */
function manageView(overrides: Record<string, unknown> = {}) {
  return {
    page: {
      id: 41,
      page_type: "ARTIST",
      category: "Musician",
      subcategory: "",
      name: "Night Signal",
      handle: "nightsignal",
      avatar_url: "",
      cover_url: "",
      description: "Some bio",
      genre: "Synthwave",
      website: "",
      email: "",
      location: "Brooklyn",
      hours: {},
      status: "ACTIVE",
      verification_status: "unverified",
      verified: false,
      followers_count: 3,
      posts_count: 1,
      tabs: ["posts", "about"]
    },
    role: "OWNER",
    capabilities: OWNER_CAPABILITIES,
    owner_user_id: 7,
    phone: "555-0100",
    links: [],
    ...overrides
  };
}

function renderScreen() {
  const navigation = { navigate: jest.fn(), goBack: jest.fn(), setOptions: jest.fn() };
  const utils = render(
    <PageEditScreen
      route={{ key: "e", name: "PageEdit", params: { pageId: 41 } } as never}
      navigation={navigation as never}
    />
  );
  return { ...utils, navigation };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockGetPageManageView.mockResolvedValue(manageView());
  mockUpdatePage.mockResolvedValue(manageView().page);
});

describe("PageEditScreen", () => {
  it("loads the page's current details into the form", async () => {
    const { getByDisplayValue } = renderScreen();
    await waitFor(() => expect(getByDisplayValue("Night Signal")).toBeTruthy());
    expect(getByDisplayValue("nightsignal")).toBeTruthy();
    expect(getByDisplayValue("Synthwave")).toBeTruthy();
    expect(getByDisplayValue("Brooklyn")).toBeTruthy();
    // Phone is management-only — it is absent from every public payload, so
    // reading it from anywhere but the manage view would leave it blank.
    expect(getByDisplayValue("555-0100")).toBeTruthy();
  });

  it("sends only the fields that changed", async () => {
    const { getByDisplayValue, getByText } = renderScreen();
    await waitFor(() => expect(getByDisplayValue("Night Signal")).toBeTruthy());

    fireEvent.changeText(getByDisplayValue("Some bio"), "Synth duo out of Brooklyn.");
    fireEvent.press(getByText("Save changes"));

    await waitFor(() => expect(mockUpdatePage).toHaveBeenCalled());
    expect(mockUpdatePage).toHaveBeenCalledWith(41, { description: "Synth duo out of Brooklyn." });
  });

  it("does not offer to save when nothing has been edited", async () => {
    const { getByDisplayValue, getByText } = renderScreen();
    await waitFor(() => expect(getByDisplayValue("Night Signal")).toBeTruthy());

    fireEvent.press(getByText("Save changes"));
    expect(mockUpdatePage).not.toHaveBeenCalled();
  });

  /**
   * The server takes a name of at least two characters. Letting the form submit
   * a shorter one would surface as a generic failure after a round trip.
   */
  it("refuses to save a name the server would reject", async () => {
    const { getByDisplayValue, getByText } = renderScreen();
    await waitFor(() => expect(getByDisplayValue("Night Signal")).toBeTruthy());

    fireEvent.changeText(getByDisplayValue("Night Signal"), "N");
    fireEvent.press(getByText("Save changes"));

    expect(mockUpdatePage).not.toHaveBeenCalled();
    expect(getByText("A page name needs at least two characters.")).toBeTruthy();
  });

  describe("handle", () => {
    it("does not check the handle the page already has", async () => {
      const { getByDisplayValue } = renderScreen();
      await waitFor(() => expect(getByDisplayValue("Night Signal")).toBeTruthy());
      // Unchanged: nothing to check, and checking would report it taken.
      expect(mockCheckPageHandle).not.toHaveBeenCalled();
    });

    it("scopes the availability check to this page", async () => {
      jest.useFakeTimers();
      mockCheckPageHandle.mockResolvedValue({
        candidate: "nightsignalofficial",
        handle: "nightsignalofficial",
        available: true,
        reason: "Available."
      });
      const { getByDisplayValue } = renderScreen();
      await waitFor(() => expect(getByDisplayValue("Night Signal")).toBeTruthy());

      fireEvent.changeText(getByDisplayValue("nightsignal"), "nightsignalofficial");
      jest.advanceTimersByTime(500);

      await waitFor(() => expect(mockCheckPageHandle).toHaveBeenCalled());
      // The page id is what stops the page from colliding with itself.
      expect(mockCheckPageHandle).toHaveBeenCalledWith("nightsignalofficial", 41);
      jest.useRealTimers();
    });

    it("blocks the save while a new handle is unavailable", async () => {
      jest.useFakeTimers();
      mockCheckPageHandle.mockResolvedValue({
        candidate: "taken",
        handle: "taken",
        available: false,
        reason: "That handle is taken by another page."
      });
      const { getByDisplayValue, getByText } = renderScreen();
      await waitFor(() => expect(getByDisplayValue("Night Signal")).toBeTruthy());

      fireEvent.changeText(getByDisplayValue("nightsignal"), "taken");
      jest.advanceTimersByTime(500);
      await waitFor(() => expect(mockCheckPageHandle).toHaveBeenCalled());

      fireEvent.press(getByText("Save changes"));
      expect(mockUpdatePage).not.toHaveBeenCalled();
      jest.useRealTimers();
    });
  });

  /**
   * Fail closed, and say why. The role names come from the server's matrix;
   * ANALYST can read a page and change nothing on it.
   */
  it("offers no form at all to a role that cannot edit", async () => {
    mockGetPageManageView.mockResolvedValue(
      manageView({ role: "ANALYST", capabilities: ["view_analytics"] })
    );
    const { queryByText, getByText } = renderScreen();

    await waitFor(() => expect(getByText("You can view this page, not edit it")).toBeTruthy());
    expect(queryByText("Save changes")).toBeNull();
  });

  it("treats a missing capability list as granting nothing", async () => {
    mockGetPageManageView.mockResolvedValue(manageView({ role: "OWNER", capabilities: [] }));
    const { queryByText, getByText } = renderScreen();

    await waitFor(() => expect(getByText("You can view this page, not edit it")).toBeTruthy());
    expect(queryByText("Save changes")).toBeNull();
  });

  describe("images", () => {
    it("uploads through the shared media pipeline and patches the returned url", async () => {
      mockUploadNativeMedia.mockReturnValue({
        promise: Promise.resolve({ ok: true, media_url: "https://cdn.example/avatar.jpg" }),
        controller: { cancel: jest.fn() }
      });
      const { getByLabelText, getByDisplayValue } = renderScreen();
      await waitFor(() => expect(getByDisplayValue("Night Signal")).toBeTruthy());

      fireEvent.press(getByLabelText("Change profile picture"));

      await waitFor(() => expect(mockUpdatePage).toHaveBeenCalled());
      // One canonical upload path — no page-specific media endpoint.
      expect(mockUploadNativeMedia).toHaveBeenCalledWith(
        expect.objectContaining({ uri: "file:///tmp/pick.jpg", mediaType: "image" }),
        expect.objectContaining({ contextType: "pulse_avatar" })
      );
      expect(mockUpdatePage).toHaveBeenCalledWith(41, { avatar_url: "https://cdn.example/avatar.jpg" });
    });

    it("does not patch anything when the upload returns no url", async () => {
      mockUploadNativeMedia.mockReturnValue({
        promise: Promise.resolve({ ok: true }),
        controller: { cancel: jest.fn() }
      });
      const { getByLabelText, getByDisplayValue, getByText } = renderScreen();
      await waitFor(() => expect(getByDisplayValue("Night Signal")).toBeTruthy());

      fireEvent.press(getByLabelText("Change cover image"));

      await waitFor(() => expect(getByText("The upload finished but returned no image.")).toBeTruthy());
      expect(mockUpdatePage).not.toHaveBeenCalled();
    });
  });
});
