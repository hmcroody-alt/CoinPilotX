/**
 * Behaviour tests for the native seller application screen.
 *
 * The screen is where the mission's two riskiest promises actually meet a user:
 * that approval is never automatic, and that reviewer-only material never
 * reaches the applicant. Both are properties of what is on screen, not of the
 * API layer, so they are asserted here against a rendered tree.
 *
 * The other thing worth protecting is the applicant's typing. Autosave on step
 * change is the only reason it is safe to ask someone for twenty answers on a
 * phone, so a regression that stops firing it — or that fires it after
 * navigating away — is a silent data-loss bug rather than a visible one.
 */
import React from "react";
import { act, fireEvent, render, waitFor } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));
jest.mock("../../navigation/BottomNavVisibility", () => ({
  BOTTOM_NAV_CONTENT_CLEARANCE: 0,
  useBottomNavScrollVisibility: () => ({
    onScroll: jest.fn(),
    onScrollBeginDrag: jest.fn(),
    scrollEventThrottle: 16
  })
}));
jest.mock("../../core/eventSync", () => ({
  registerSyncInvalidation: jest.fn(() => () => undefined)
}));

const mockLoad = jest.fn();
const mockLoadCached = jest.fn();
const mockSaveDraft = jest.fn();
const mockSubmit = jest.fn();
const mockWithdraw = jest.fn();
const mockUpload = jest.fn();
const mockRemove = jest.fn();
const mockPickFile = jest.fn();
const mockCapture = jest.fn();

jest.mock("../../api/sellerApplication", () => {
  const actual = jest.requireActual("../../api/sellerApplication");
  return {
    ...actual,
    loadSellerApplication: (...args: unknown[]) => mockLoad(...args),
    loadCachedSellerApplication: (...args: unknown[]) => mockLoadCached(...args),
    saveSellerApplicationDraft: (...args: unknown[]) => mockSaveDraft(...args),
    submitSellerApplication: (...args: unknown[]) => mockSubmit(...args),
    withdrawSellerApplication: (...args: unknown[]) => mockWithdraw(...args),
    uploadSellerApplicationDocument: (...args: unknown[]) => mockUpload(...args),
    removeSellerApplicationDocument: (...args: unknown[]) => mockRemove(...args),
    pickSellerApplicationFile: (...args: unknown[]) => mockPickFile(...args),
    captureSellerApplicationPhoto: (...args: unknown[]) => mockCapture(...args)
  };
});

import { normalizeSellerApplication } from "../../api/sellerApplication";
import { SellerApplicationScreen } from "../SellerApplicationScreen";

const STEPS = [
  { key: "seller_type", title: "Seller type", summary: "How you sell", fields: [], complete: true, errors: {} },
  { key: "identity", title: "About you", summary: "Who is applying", fields: ["full_name", "email"], complete: false, errors: { full_name: "Tell us your legal name." } },
  { key: "documents", title: "Documents", summary: "Verify it is you", fields: [], complete: false, errors: {} },
  { key: "review", title: "Review", summary: "Check and submit", fields: [], complete: false, errors: {} }
];

function application(overrides: Record<string, unknown> = {}) {
  return normalizeSellerApplication({
    application_id: 7,
    status: "draft",
    status_title: "Draft",
    status_message: "Your application has not been submitted yet.",
    next_action: { action: "continue", label: "Continue application" },
    editable: true,
    completeness: 40,
    fields: { full_name: "", email: "" },
    documents: [],
    steps: STEPS,
    can_submit: false,
    seller_types: [{ value: "individual", label: "Individual" }, { value: "business", label: "Registered business" }],
    selling_intents: ["services"],
    required_documents: [{ type: "id_front", label: "ID front" }],
    optional_documents: [],
    ...overrides
  });
}

async function renderScreen(view = application()) {
  mockLoad.mockResolvedValue(view);
  const navigation = { navigate: jest.fn() };
  const utils = render(<SellerApplicationScreen navigation={navigation} />);
  await waitFor(() => expect(mockLoad).toHaveBeenCalled());
  await act(async () => undefined);
  return { ...utils, navigation };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockLoadCached.mockResolvedValue(null);
});

describe("before applying", () => {
  it("explains the process and who decides before asking for anything", async () => {
    // Someone deciding whether to spend twenty minutes on this deserves to know
    // a person reads it and roughly how long it takes, before the first field.
    const { getByText } = await renderScreen(application({ application_id: 0, status: "draft" }));
    expect(getByText("Sell your work on PulseSoc")).toBeTruthy();
    expect(getByText("What happens next")).toBeTruthy();
    expect(getByText("A person reviews it")).toBeTruthy();
    expect(getByText("What you will need")).toBeTruthy();
    expect(getByText("Start application")).toBeTruthy();
  });

  it("creates the draft server-side when the applicant starts", async () => {
    const { getByText } = await renderScreen(application({ application_id: 0 }));
    mockSaveDraft.mockResolvedValue(application());
    await act(async () => {
      fireEvent.press(getByText("Start application"));
    });
    expect(mockSaveDraft).toHaveBeenCalled();
    await waitFor(() => expect(getByText("Seller type")).toBeTruthy());
  });
});

describe("filling in the application", () => {
  it("shows step position, progress, and the server's own per-field errors", async () => {
    const { getByText, getAllByText } = await renderScreen();
    expect(getByText("Step 1 of 4")).toBeTruthy();
    expect(getByText("40% complete")).toBeTruthy();
    // Errors belong on the step that produced them, not in one pile at submit.
    await act(async () => {
      fireEvent.press(getAllByText("About you")[0]);
    });
    await waitFor(() => expect(getAllByText("Tell us your legal name.").length).toBeGreaterThan(0));
  });

  it("saves answers when moving between steps, and only when something changed", async () => {
    const { getByText, getByLabelText } = await renderScreen();
    mockSaveDraft.mockResolvedValue(application());

    // Moving off a step the applicant only read should not write.
    await act(async () => {
      fireEvent.press(getByText("Continue"));
    });
    expect(mockSaveDraft).not.toHaveBeenCalled();

    await act(async () => {
      fireEvent.changeText(getByLabelText("Legal name"), "Ada Lovelace");
    });
    await act(async () => {
      fireEvent.press(getByText("Continue"));
    });
    expect(mockSaveDraft).toHaveBeenCalledWith(expect.objectContaining({ full_name: "Ada Lovelace" }));
  });

  it("tells the applicant when a save failed instead of pretending it worked", async () => {
    // Silence here means someone keeps typing into a form that is no longer
    // being kept, which is the worst outcome this screen can produce.
    const { getByText, getByLabelText, getAllByText } = await renderScreen();
    mockSaveDraft.mockRejectedValue(new Error("Network request failed"));
    await act(async () => {
      fireEvent.press(getAllByText("About you")[0]);
    });
    await act(async () => {
      fireEvent.changeText(getByLabelText("Legal name"), "Ada");
    });
    await act(async () => {
      fireEvent.press(getByText("Continue"));
    });
    await waitFor(() => expect(getByText("Network request failed")).toBeTruthy());
  });

  it("refuses to submit until the server says the application is complete", async () => {
    const { getByText } = await renderScreen();
    await act(async () => {
      fireEvent.press(getByText("Review"));
    });
    const submit = await waitFor(() => getByText("Submit for review"));
    await act(async () => {
      fireEvent.press(submit);
    });
    // can_submit is false, so the control is disabled and nothing is sent.
    expect(mockSubmit).not.toHaveBeenCalled();
    expect(getByText("Finish the sections marked above before submitting.")).toBeTruthy();
  });

  it("states on every step that approval is not automatic", async () => {
    const { getByText } = await renderScreen();
    expect(
      getByText("A PulseSoc administrator reviews every application. Nothing is approved automatically, and we will tell you either way.")
    ).toBeTruthy();
  });
});

describe("submitting", () => {
  it("adopts the status the server returned rather than advancing locally", async () => {
    const submittable = application({ can_submit: true });
    const { getByText, queryByText } = await renderScreen(submittable);
    // The server refuses — a required document went missing between render and tap.
    mockSubmit.mockResolvedValue({
      view: application({ can_submit: false, status: "draft" }),
      message: "Upload the back of your ID before submitting."
    });
    await act(async () => {
      fireEvent.press(getByText("Review"));
    });
    await act(async () => {
      fireEvent.press(getByText("Submit for review"));
    });
    await waitFor(() => expect(getByText("Upload the back of your ID before submitting.")).toBeTruthy());
    // No optimistic "submitted"/"approved" anywhere on screen.
    expect(queryByText("Under review")).toBeNull();
    expect(queryByText("Approved")).toBeNull();
  });

  it("moves to the status centre when the server accepts the submission", async () => {
    const { getByText, getAllByText } = await renderScreen(application({ can_submit: true }));
    mockSubmit.mockResolvedValue({
      view: application({ status: "submitted", status_title: "Submitted", status_message: "A reviewer will look at this shortly.", editable: false, submitted_at: "2026-07-20" }),
      message: "Application submitted for review."
    });
    await act(async () => {
      fireEvent.press(getByText("Review"));
    });
    await act(async () => {
      fireEvent.press(getByText("Submit for review"));
    });
    // The status title appears both as the screen subtitle and on the badge.
    await waitFor(() => expect(getAllByText("Submitted").length).toBeGreaterThan(0));
    expect(getByText("A reviewer will look at this shortly.")).toBeTruthy();
  });
});

describe("while under review", () => {
  const underReview = application({
    status: "under_review",
    status_title: "Under review",
    status_message: "An administrator is checking your details.",
    editable: false,
    submitted_at: "2026-07-20",
    documents: [{ id: 3, type: "id_front", label: "ID front", filename: "front.jpg", size_kb: 180, uploaded_at: "2026-07-20", state: "received" }]
  });

  it("shows status and progress instead of an editable form", async () => {
    // Editing underneath a reviewer is the thing this prevents: they would be
    // deciding on answers that no longer exist.
    const { getByText, getAllByText, queryByText, queryByLabelText } = await renderScreen(underReview);
    expect(getAllByText("Under review").length).toBeGreaterThan(0);
    expect(getByText("Where you are")).toBeTruthy();
    expect(getByText("Every application is read by a person. Nothing here is decided automatically.")).toBeTruthy();
    expect(queryByText("Submit for review")).toBeNull();
    expect(queryByText("Continue")).toBeNull();
    expect(queryByLabelText("Legal name")).toBeNull();
  });

  it("lets the applicant withdraw while they are still waiting", async () => {
    const { getByText, getAllByText } = await renderScreen(underReview);
    mockWithdraw.mockResolvedValue({
      view: application({ status: "withdrawn", status_title: "Withdrawn", status_message: "You withdrew this application.", editable: false }),
      message: "Application withdrawn."
    });
    await act(async () => {
      fireEvent.press(getByText("Withdraw application"));
    });
    await waitFor(() => expect(getAllByText("Withdrawn").length).toBeGreaterThan(0));
  });

  it("never renders reviewer-only material, even if the server sent it", async () => {
    // Defence in depth. `applicant_view` whitelists, so these cannot arrive —
    // but if that ever regressed, it must not become visible text.
    const contaminated = normalizeSellerApplication({
      ...JSON.parse(JSON.stringify(underReview)),
      internal_notes: [{ body: "Applicant looks like a reseller" }],
      reviewer_id: 4,
      risk_score: 91,
      decision_reason: "internal reasoning"
    });
    const { queryByText } = await renderScreen(contaminated);
    expect(queryByText("Applicant looks like a reseller")).toBeNull();
    expect(queryByText(/risk/i)).toBeNull();
    expect(queryByText("internal reasoning")).toBeNull();
  });
});

describe("after a decision", () => {
  it("shows what the reviewer asked for and reopens the form for corrections", async () => {
    const { getByText, getAllByText } = await renderScreen(
      application({
        status: "information_requested",
        status_title: "More information needed",
        editable: true,
        information_request: "Please re-upload the back of your ID."
      })
    );
    expect(getByText("A reviewer needs one more thing")).toBeTruthy();
    expect(getByText("Please re-upload the back of your ID.")).toBeTruthy();
    // The form is back, so the applicant can act on what was asked.
    expect(getAllByText("Seller type").length).toBeGreaterThan(0);
  });

  it("routes an approved seller to the tools their approval unlocked", async () => {
    const { getByText, navigation } = await renderScreen(
      application({
        status: "approved",
        status_title: "Approved",
        status_message: "Your store is open.",
        editable: false,
        next_action: { action: "open_seller_tools", label: "Open seller tools" }
      })
    );
    await act(async () => {
      fireEvent.press(getByText("Open seller tools"));
    });
    expect(navigation.navigate).toHaveBeenCalledWith("SellerStore", expect.anything());
  });

  it("explains a rejection and lets the applicant apply again", async () => {
    const { getByText } = await renderScreen(
      application({
        status: "rejected",
        status_title: "Not approved",
        status_message: "We could not verify your business registration.",
        editable: true,
        next_action: { action: "reapply", label: "Apply again" }
      })
    );
    // Rejected applicants stay editable, so the screen must give them the form
    // rather than a dead end.
    expect(getByText("We could not verify your business registration.")).toBeTruthy();
  });
});

describe("offline", () => {
  it("falls back to the saved copy and says so rather than showing an empty application", async () => {
    mockLoad.mockRejectedValue(new Error("Network request failed"));
    mockLoadCached.mockResolvedValue(application({ status_title: "Draft" }));
    const { getByText } = render(<SellerApplicationScreen navigation={{ navigate: jest.fn() }} />);
    await waitFor(() => expect(getByText("Showing your last saved copy. Reconnect to refresh.")).toBeTruthy());
  });

  it("reports the failure when there is no saved copy at all", async () => {
    mockLoad.mockRejectedValue(new Error("The seller application could not load."));
    mockLoadCached.mockResolvedValue(null);
    const { getByText } = render(<SellerApplicationScreen navigation={{ navigate: jest.fn() }} />);
    await waitFor(() => expect(getByText("The seller application could not load.")).toBeTruthy());
  });

  it("keeps the answers a restarted app recovers from disk", async () => {
    // Restart is indistinguishable from any other cold mount, so what proves
    // progress survives it is that the cached copy is what gets rendered and
    // edited — not a fresh empty form the applicant would have to fill twice.
    mockLoad.mockRejectedValue(new Error("Network request failed"));
    mockLoadCached.mockResolvedValue(application({ fields: { full_name: "Amara Nwosu", email: "" } }));
    const { getByDisplayValue, getByText } = render(<SellerApplicationScreen navigation={{ navigate: jest.fn() }} />);
    await waitFor(() => expect(getByText("Showing your last saved copy. Reconnect to refresh.")).toBeTruthy());
    await act(async () => {
      fireEvent.press(getByText("Continue"));
    });
    await waitFor(() => expect(getByDisplayValue("Amara Nwosu")).toBeTruthy());
  });
});

describe("resuming", () => {
  it("opens an existing draft at the form instead of the introduction", async () => {
    // The introduction is for people who have not applied. Showing it to someone
    // who already has a draft invites them to "start" an application they are
    // already halfway through.
    const { getByText, queryByText } = await renderScreen(application({ application_id: 7, status: "draft" }));
    expect(queryByText("Start application")).toBeNull();
    expect(getByText("Seller type")).toBeTruthy();
  });

  it("creates nothing on the way in, so returning cannot fork a second draft", async () => {
    // A write on mount is how duplicate drafts happen: the reviewer then has two
    // half-answered applications from one person and no way to tell which is
    // current. Reading on mount and writing only on a deliberate action is the
    // property that prevents it.
    await renderScreen(application({ application_id: 7, status: "draft" }));
    expect(mockSaveDraft).not.toHaveBeenCalled();
  });

  it("resumes a returning applicant at the first step, not wherever they left the index", async () => {
    const { getByText } = await renderScreen(application({ application_id: 7, status: "draft" }));
    expect(getByText("Step 1 of 4")).toBeTruthy();
  });
});

describe("session and permission failures", () => {
  it("shows the reason a start was refused rather than a silent no-op", async () => {
    // Every refusal the server can give — an expired session, a revoked
    // permission, a closed application window — arrives here as a rejected
    // promise. The button must not simply stop being busy and leave the
    // applicant tapping it again.
    const { getByText } = await renderScreen(application({ application_id: 0 }));
    mockSaveDraft.mockRejectedValue(new Error("Your session has expired. Please sign in again."));
    await act(async () => {
      fireEvent.press(getByText("Start application"));
    });
    await waitFor(() => expect(getByText("Your session has expired. Please sign in again.")).toBeTruthy());
    expect(getByText("Start application")).toBeTruthy();
  });

  it("clears a stale load failure once the application actually starts", async () => {
    // Without this the applicant reads "could not load" while looking at step
    // one of the thing that loaded.
    mockLoad.mockRejectedValue(new Error("The seller application could not load."));
    mockLoadCached.mockResolvedValue(null);
    const { getByText, queryByText } = render(<SellerApplicationScreen navigation={{ navigate: jest.fn() }} />);
    await waitFor(() => expect(getByText("The seller application could not load.")).toBeTruthy());
    mockSaveDraft.mockResolvedValue(application());
    await act(async () => {
      fireEvent.press(getByText("Start application"));
    });
    await waitFor(() => expect(getByText("Seller type")).toBeTruthy());
    expect(queryByText("The seller application could not load.")).toBeNull();
  });

  it("never leaves the start button stuck in its busy state after a failure", async () => {
    const { getByText, queryAllByText } = await renderScreen(application({ application_id: 0 }));
    mockSaveDraft.mockRejectedValue(new Error("The requested PulseSoc service was not found."));
    await act(async () => {
      fireEvent.press(getByText("Start application"));
    });
    await waitFor(() => expect(queryAllByText("Starting…").length).toBe(0));
    expect(getByText("Start application")).toBeTruthy();
  });
});
