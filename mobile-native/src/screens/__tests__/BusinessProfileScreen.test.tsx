/**
 * The Business Profile screen makes promises that are easy to break and invisible
 * in a screenshot, so they are pinned here.
 *
 * 1. **One source decides verification.** The screen used to read three — the
 *    seller application's lifecycle, `api/verification`, and the Pulse profile's
 *    `verified_badge`, which it OR-ed into the answer. Three sources with no
 *    precedence between them is how one surface printed "in review" while another
 *    printed "Approved" for the same business. The server resolves that precedence
 *    once; the tests below hand the client a payload where the badge and the
 *    resolved state disagree, and assert the resolved state wins.
 * 2. **`requiresReview` is not `blocked`.** The old screen asked one question —
 *    "is the application editable" — and froze thirteen fields when the answer was
 *    no. A field a reviewer will look at is still a field the seller can type in.
 *    A field that is genuinely locked says so out loud rather than swallowing the
 *    press, because a control that looks live and quietly does nothing teaches the
 *    operator the wrong thing about their own business.
 * 3. **A partial save is a success with exceptions.** Fields that validated are
 *    kept; only the refused ones stay dirty. Losing four corrections because the
 *    fifth had a typo is the defect the brief rules out.
 * 4. **It never invents a number.** Rating, on-time rate, reply time, profile
 *    views, store clicks and follower deltas have no endpoint. The registry that
 *    routes this screen is explicit that coverage "reflects verified live
 *    coverage, not aspiration".
 * 5. **`unset` hours are not `closed` hours.** A new seller who has configured
 *    nothing is not a business that is shut, and a buyer told "Closed" when the
 *    truth is "we never said" has been misinformed.
 * 6. **Reduce-motion stops the animation, not the content.** Under the OS setting
 *    the ticker must still be readable and the ring must still report its real
 *    value.
 */
import React from "react";
import { act, fireEvent, render, waitFor } from "@testing-library/react-native";

const mockReducedMotion = jest.fn(() => false);

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));
jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));
jest.mock("expo-linear-gradient", () => ({ LinearGradient: "LinearGradient" }));
jest.mock("../../core/eventSync", () => ({
  registerSyncInvalidation: jest.fn(() => () => undefined)
}));
jest.mock("../../theme/logiNexusMotion", () => ({
  ...jest.requireActual("../../theme/logiNexusMotion"),
  // The real hook reads AccessibilityInfo, which the test environment cannot
  // toggle. Swapping the hook lets both branches be exercised for real.
  useLogiNexusReducedMotion: () => mockReducedMotion()
}));

/**
 * Only the network edges are mocked. `changedFields`, `openingStatus`,
 * `normalizeOwnerProfile` and the label tables stay real, so a test that says "the
 * save sent the diff" is checking the diff this screen would actually send rather
 * than a reimplementation of it living in the test file.
 */
const mockLoadOwner = jest.fn();
const mockSaveFields = jest.fn();
const mockSaveLink = jest.fn();
jest.mock("../../api/businessProfile", () => ({
  ...jest.requireActual("../../api/businessProfile"),
  loadOwnerProfile: (...args: unknown[]) => mockLoadOwner(...args),
  saveProfileFields: (...args: unknown[]) => mockSaveFields(...args),
  saveLink: (...args: unknown[]) => mockSaveLink(...args)
}));

const mockGetProfile = jest.fn();
const mockCachedProfile = jest.fn();
jest.mock("../../api/profile", () => ({
  getMyProfile: (...args: unknown[]) => mockGetProfile(...args),
  loadCachedProfile: (...args: unknown[]) => mockCachedProfile(...args)
}));

const mockStore = jest.fn();
const mockCachedStore = jest.fn();
jest.mock("../../api/marketplace", () => ({
  loadSellerStoreSnapshot: (...args: unknown[]) => mockStore(...args),
  loadCachedSellerStore: (...args: unknown[]) => mockCachedStore(...args)
}));

import { normalizeOwnerProfile } from "../../api/businessProfile";
import { BusinessProfileScreen } from "../BusinessProfileScreen";

/**
 * A payload in the server's wire shape, run through the real normaliser.
 *
 * Built from snake_case keys rather than a hand-written `OwnerProfile` so that a
 * change to the wire format breaks these tests instead of passing them while the
 * screen goes blank against the real server.
 */
function ownerProfile(overrides: Record<string, unknown> = {}) {
  return normalizeOwnerProfile({
    user_id: 7,
    handle: "@harbour",
    business_name: "Harbour Goods",
    business_category: "retail",
    business_category_label: "General retail",
    tagline: "Coastal homeware",
    contact: { email: "hi@harbour.co", preferred: "message" },
    public_location: { city: "Cork", country: "Ireland" },
    hours_mode: "unset",
    hours: [],
    links: [],
    verification: { state: "not_started", source: "none" },
    locks: { requires_review: [], blocked: [], explainer: "" },
    completion: {
      percent: 62,
      missing: [{ key: "logo", label: "Add a business logo", section: "brand" }],
      completed: [],
      total: 10,
      next_key: "logo",
      next_label: "Add a business logo"
    },
    sync: { state: "synced" },
    published_at: "2024-03-04T10:00:00Z",
    ...overrides
  });
}

function ready(profile = ownerProfile(), fromCache = false) {
  return { state: "ready" as const, profile, fromCache, savedAt: Date.now() };
}

function navigationSpy() {
  return { navigate: jest.fn(), goBack: jest.fn() };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockReducedMotion.mockReturnValue(false);
  mockLoadOwner.mockResolvedValue(ready());
  mockSaveFields.mockImplementation(async (fields: Record<string, unknown>) => ({
    saved: fields,
    rejected: {},
    queuedForReview: [],
    ignored: [],
    profile: ownerProfile({ business_name: String(fields.business_name ?? "Harbour Goods") })
  }));
  mockSaveLink.mockImplementation(async () => ownerProfile());
  mockGetProfile.mockResolvedValue({ user_id: 7, username: "harbour", display_name: "Harbour", follower_count: 2400 });
  mockCachedProfile.mockResolvedValue(null);
  mockStore.mockResolvedValue({ listings: [], orders: [] });
  mockCachedStore.mockResolvedValue(null);
});

async function renderScreen(navigation = navigationSpy()) {
  const view = render(<BusinessProfileScreen navigation={navigation} />);
  await waitFor(() => expect(mockLoadOwner).toHaveBeenCalled());
  await act(async () => undefined);
  return { ...view, navigation };
}

describe("Business Profile", () => {
  it("shows the server's completeness rather than a decorative default", async () => {
    const view = await renderScreen();
    expect(view.getByLabelText("Profile completeness")).toBeTruthy();
    expect(view.getByText("62")).toBeTruthy();
  });

  it("names the specific next step the server identified, not the percentage", async () => {
    const view = await renderScreen();
    // "62% complete" is a grade. "Add a business logo" is the homework.
    expect(view.getByText(/Add a business logo/)).toBeTruthy();
  });

  it("marks every unbacked metric as untracked instead of inventing a value", async () => {
    const view = await renderScreen();
    // The ticker exposes its content once, as a spoken summary, so the moving
    // copies are not read twice. That summary is where the placeholders live.
    const ticker = view.getByLabelText(/Profile complete: 62%/);
    const spoken = String(ticker.props.accessibilityLabel);
    ["Profile views today", "New followers", "Avg reply time", "Store clicks", "Next ship day"].forEach((label) => {
      expect(spoken).toContain(`${label}: Not tracked yet`);
    });
    expect(view.getByLabelText("Rating: —")).toBeTruthy();
    expect(view.getByLabelText("On time: —")).toBeTruthy();
  });

  it("explains what an empty field costs the seller rather than just saying it is blank", async () => {
    const view = await renderScreen();
    expect(view.getByLabelText(/Opening hours\. Not set\. buyers can't see when you're open/)).toBeTruthy();
    expect(view.getByLabelText(/Links\. Not set\. buyers can't check you out elsewhere/)).toBeTruthy();
  });

  it("reads unset opening hours as unset, never as closed", async () => {
    const view = await renderScreen();
    // `unset` and `closed` are different facts. A new seller who has configured
    // nothing is not a business that is shut on Mondays.
    expect(view.queryByLabelText(/Opening hours\. Closed/)).toBeNull();
  });

  it("shows real opening hours once the seller has set them", async () => {
    const hours = [
      { weekday: "mon", label: "Monday", state: "open", opens: "09:00", closes: "17:30" },
      { weekday: "tue", label: "Tuesday", state: "open", opens: "09:00", closes: "17:30" },
      { weekday: "wed", label: "Wednesday", state: "open", opens: "09:00", closes: "17:30" },
      { weekday: "thu", label: "Thursday", state: "open", opens: "09:00", closes: "17:30" },
      { weekday: "fri", label: "Friday", state: "open", opens: "09:00", closes: "17:30" },
      { weekday: "sat", label: "Saturday", state: "closed", opens: null, closes: null },
      { weekday: "sun", label: "Sunday", state: "closed", opens: null, closes: null }
    ];
    mockLoadOwner.mockResolvedValue(ready(ownerProfile({ hours_mode: "weekly", hours })));
    const view = await renderScreen();
    // The row is no longer the "this field is coming" placeholder it used to be.
    expect(view.queryByLabelText(/Opening hours\. Not set/)).toBeNull();
    expect(view.getByLabelText(/^Opening hours\. (Open now|Closed)/)).toBeTruthy();
  });

  it("keeps the ticker readable and the ring truthful under reduce-motion", async () => {
    mockReducedMotion.mockReturnValue(true);
    const view = await renderScreen();
    // Content is still present and still announces the real figures — the
    // setting suppresses movement, not information.
    expect(view.getByLabelText(/Profile complete: 62%/)).toBeTruthy();
    expect(view.getByText("62")).toBeTruthy();
  });

  /* ------------------------------------------------------------- verification */

  it("renders the resolved verification state, not a status it recomputed", async () => {
    mockLoadOwner.mockResolvedValue(
      ready(ownerProfile({ verification: { state: "under_review", source: "verification_request" } }))
    );
    const view = await renderScreen();
    expect(view.getByText(/Verification · Under review/)).toBeTruthy();
    expect(view.getByText(/reviewing your documents/)).toBeTruthy();
  });

  it("does not let a stale verified badge overrule the resolved state", async () => {
    // The exact disagreement the old screen resolved with `||`: a badge left on the
    // user row after a suspension would have printed "Verified" here while the
    // verification centre printed "Suspended".
    mockGetProfile.mockResolvedValue({ user_id: 7, username: "harbour", verified_badge: true, follower_count: 10 });
    mockLoadOwner.mockResolvedValue(
      ready(ownerProfile({ verification: { state: "suspended", source: "verification_request" } }))
    );
    const view = await renderScreen();
    expect(view.getByText(/Verification · Suspended/)).toBeTruthy();
    expect(view.queryByText(/Verification · Verified/)).toBeNull();
  });

  it("sends the trust callout to the business verification track", async () => {
    const view = await renderScreen();
    fireEvent.press(view.getByLabelText("Check status"));
    expect(view.navigation.navigate).toHaveBeenCalledWith("VerificationCenter", { track: "business" });
  });

  /* -------------------------------------------------------------------- locks */

  it("still lets the seller edit a field a reviewer will check", async () => {
    mockLoadOwner.mockResolvedValue(
      ready(
        ownerProfile({
          verification: { state: "approved", source: "verification_request" },
          locks: { requires_review: ["business_name"], blocked: [], explainer: "A reviewer checks name changes." }
        })
      )
    );
    const view = await renderScreen();
    // "A reviewer will look at this" is not "you cannot change this". Collapsing
    // the two is what froze thirteen fields because one was sensitive.
    const row = view.getByLabelText(/Business name \(a reviewer will check this\)\. Harbour Goods/);
    fireEvent.press(row);
    await act(async () => undefined);
    expect(view.getByLabelText(/Business name \(a reviewer will check this\)/)).toBeTruthy();
  });

  it("refuses a blocked field out loud instead of swallowing the press", async () => {
    mockLoadOwner.mockResolvedValue(
      ready(
        ownerProfile({
          verification: { state: "suspended", source: "verification_request" },
          locks: {
            requires_review: [],
            blocked: ["business_name"],
            explainer: "This field is locked while the business is under enforcement review."
          }
        })
      )
    );
    const view = await renderScreen();
    fireEvent.press(view.getByLabelText(/Business name \(locked\)\. Harbour Goods/));
    await act(async () => undefined);
    // The server's own sentence, surfaced. A row that looked live and did nothing
    // would teach the operator the wrong thing about their own business.
    expect(view.getByText(/locked while the business is under enforcement review/)).toBeTruthy();
    // Refused means refused: no editor opened behind the message.
    expect(view.queryByLabelText("Business name")).toBeNull();
  });

  /* ------------------------------------------------------------------- saving */

  it("leaves Save inert until something is actually edited, then sends only the diff", async () => {
    const view = await renderScreen();
    const save = view.getByLabelText("Save changes");
    expect(save.props.accessibilityState.disabled).toBe(true);

    fireEvent.press(view.getByLabelText("Business name. Harbour Goods"));
    fireEvent.changeText(view.getByLabelText("Business name"), "Harbour Goods Ltd");
    await act(async () => undefined);

    expect(view.getByLabelText("Save changes").props.accessibilityState.disabled).toBe(false);
    await act(async () => {
      fireEvent.press(view.getByLabelText("Save changes"));
    });

    // The diff, not the form. The audit trail exists so a reviewer can see what a
    // verified business changed; nine keys per save would bury the one real edit.
    expect(mockSaveFields).toHaveBeenCalledWith({ business_name: "Harbour Goods Ltd" });
    // A completed save is no longer dirty, so the control goes quiet again.
    await waitFor(() => expect(view.getByLabelText("Save changes").props.accessibilityState.disabled).toBe(true));
  });

  it("treats an edit back to the stored value as no edit at all", async () => {
    const view = await renderScreen();
    fireEvent.press(view.getByLabelText("Business name. Harbour Goods"));
    const input = view.getByLabelText("Business name");
    fireEvent.changeText(input, "Something else");
    await act(async () => undefined);
    expect(view.getByLabelText("Save changes").props.accessibilityState.disabled).toBe(false);

    fireEvent.changeText(input, "Harbour Goods");
    await act(async () => undefined);
    expect(view.getByLabelText("Save changes").props.accessibilityState.disabled).toBe(true);
  });

  it("keeps what saved and re-offers only what was refused", async () => {
    mockSaveFields.mockResolvedValue({
      saved: { public_city: "Cork" },
      rejected: { business_name: "That name is already taken." },
      queuedForReview: [],
      ignored: [],
      profile: ownerProfile({ public_location: { city: "Cork", country: "Ireland" } })
    });
    const view = await renderScreen();
    fireEvent.press(view.getByLabelText("Business name. Harbour Goods"));
    fireEvent.changeText(view.getByLabelText("Business name"), "Harbour Goods Ltd");
    await act(async () => undefined);
    await act(async () => {
      fireEvent.press(view.getByLabelText("Save changes"));
    });

    // The refusal is beside the field, and the screen says what did land — not a
    // single red banner implying all of it was lost.
    expect(view.getByText("That name is already taken.")).toBeTruthy();
    expect(view.getByText(/Saved 1 change/)).toBeTruthy();
    // Still dirty, because the refused edit is still on screen waiting to be fixed.
    expect(view.getByLabelText("Save changes").props.accessibilityState.disabled).toBe(false);
  });

  it("says a change was queued for review rather than letting it look unsaved", async () => {
    mockSaveFields.mockResolvedValue({
      saved: { business_name: "Harbour Goods Ltd" },
      rejected: {},
      queuedForReview: ["business_name"],
      ignored: [],
      profile: ownerProfile()
    });
    const view = await renderScreen();
    fireEvent.press(view.getByLabelText("Business name. Harbour Goods"));
    fireEvent.changeText(view.getByLabelText("Business name"), "Harbour Goods Ltd");
    await act(async () => undefined);
    await act(async () => {
      fireEvent.press(view.getByLabelText("Save changes"));
    });
    // Saved *and* queued. A seller who reads the unchanged public profile as a
    // failed save will type it again.
    expect(view.getByText(/reviewer will check/)).toBeTruthy();
  });

  it("saves the website through the link endpoint, not the field endpoint", async () => {
    const view = await renderScreen();
    fireEvent.press(view.getByLabelText(/Links\. Not set/));
    fireEvent.changeText(view.getByLabelText("Links"), "https://harbour.co");
    await act(async () => undefined);
    await act(async () => {
      fireEvent.press(view.getByLabelText("Save changes"));
    });
    // Links are a positioned collection server-side, not a text column. Sending
    // `website_url` to the field endpoint would come back as "that field cannot be
    // edited here", which reads to the seller as "your URL is invalid".
    expect(mockSaveLink).toHaveBeenCalledWith("website", "https://harbour.co");
    expect(mockSaveFields).not.toHaveBeenCalled();
  });

  /* --------------------------------------------------------------- live sync */

  it("reports what Live Sync actually knows instead of a permanent green light", async () => {
    const view = await renderScreen();
    expect(view.getByLabelText("Synced")).toBeTruthy();

    fireEvent.press(view.getByLabelText("Business name. Harbour Goods"));
    fireEvent.changeText(view.getByLabelText("Business name"), "Harbour Goods Ltd");
    await act(async () => undefined);
    // The moment anyone types, "Synced" stops being true: what is on screen is no
    // longer what a buyer would see.
    expect(view.getByLabelText("Changes pending")).toBeTruthy();
  });

  it("says Offline when it is showing a cached copy", async () => {
    mockLoadOwner.mockResolvedValue(ready(ownerProfile({ completion: { percent: 41, missing: [], completed: [], total: 10 } }), true));
    const view = await renderScreen();
    expect(view.getByText("Showing your last saved profile. Reconnect to sync.")).toBeTruthy();
    expect(view.getByLabelText("Offline")).toBeTruthy();
    // The cached completeness is what is shown — not a zero that would tell a
    // seller their profile is empty when it is not.
    expect(view.getByText("41")).toBeTruthy();
  });

  /* ------------------------------------------------------------------- routes */

  /**
   * This test previously asserted `ProfileDetail` and passed — which is what made it
   * worthless. `ProfileDetail` is the owner's own *social* profile: it renders owner
   * affordances and reads owner data, so "View as buyer" showed the seller a screen
   * no buyer will ever see. The old assertion pinned the destination without ever
   * asking whether the destination was the buyer's view, so it would have gone on
   * passing however wrong that answer became.
   */
  it("opens the buyer preview route for 'View as buyer', not the owner's social profile", async () => {
    const view = await renderScreen();
    fireEvent.press(view.getByLabelText("View as buyer"));
    expect(view.navigation.navigate).toHaveBeenCalledWith("BusinessBuyerPreview");
    expect(view.navigation.navigate).not.toHaveBeenCalledWith("ProfileDetail", expect.anything());
  });

  it("counts only publicly purchasable listings as active", async () => {
    mockStore.mockResolvedValue({
      listings: [
        { id: 1, status: "active", approval_status: "approved" },
        { id: 2, status: "paused", approval_status: "approved" },
        { id: 3, status: "active", approval_status: "pending" }
      ],
      orders: []
    });
    const view = await renderScreen();
    expect(view.getByLabelText("Marketplace. 1 active")).toBeTruthy();
    expect(view.getByLabelText("Store. 3 listings")).toBeTruthy();
  });

  it("reports a failure instead of rendering an empty profile as a real one", async () => {
    mockLoadOwner.mockResolvedValue({
      state: "failed",
      failure: { cause: "offline", message: "You're offline. Your business profile will load when you reconnect.", actionLabel: "Try again" }
    });
    const view = await renderScreen();
    expect(view.getByText(/You're offline/)).toBeTruthy();
    // A 0% ring on a profile that is actually 62% complete would send the seller
    // to re-enter details they already have.
    expect(view.queryByText("62")).toBeNull();
  });
});
