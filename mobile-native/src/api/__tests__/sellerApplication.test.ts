/**
 * Contract tests for the native seller application client.
 *
 * The client's whole job is to be a faithful, total translation of one server
 * view. So the properties worth asserting are not "does it call the endpoint" —
 * they are the three ways a client like this silently corrupts an application:
 *
 *  1. It invents status. The applicant's status is a legal fact about a review;
 *     if the client ever derives, advances, or optimistically guesses it, the
 *     screen and the reviewer disagree about what is true.
 *  2. It drops options. The server names the same concept `value` here and
 *     `type` there. A parser that reads only `key` renders an empty picker,
 *     which looks like an application with no seller types rather than a bug.
 *  3. It leaks documents. The upload path must hand the file to the network
 *     layer and keep nothing — not in the cache, not in a returned object.
 *
 * Everything is exercised against the real key names `seller_lifecycle.applicant_view`
 * emits, so a rename on the server breaks a test here rather than a picker in
 * the applicant's hands.
 */
const mockPulseApi = jest.fn();
const mockReadJsonCache = jest.fn();
const mockWriteJsonCache = jest.fn();

jest.mock("../pulseApi", () => ({
  pulseApi: (...args: unknown[]) => mockPulseApi(...args)
}));

jest.mock("../../core/cache", () => ({
  readJsonCache: (...args: unknown[]) => mockReadJsonCache(...args),
  writeJsonCache: (...args: unknown[]) => mockWriteJsonCache(...args)
}));

jest.mock("expo-document-picker", () => ({ getDocumentAsync: jest.fn() }));
jest.mock("expo-image-picker", () => ({
  requestCameraPermissionsAsync: jest.fn(),
  launchCameraAsync: jest.fn()
}));

import * as DocumentPicker from "expo-document-picker";
import * as ImagePicker from "expo-image-picker";

import {
  captureSellerApplicationPhoto,
  emptySellerApplication,
  loadSellerApplication,
  normalizeSellerApplication,
  pickSellerApplicationFile,
  removeSellerApplicationDocument,
  saveSellerApplicationDraft,
  sellerApplicationIsEditable,
  sellerApplicationIsPending,
  sellerApplicationStatusTone,
  submitSellerApplication,
  uploadSellerApplicationDocument,
  withdrawSellerApplication
} from "../sellerApplication";

/** A response shaped exactly the way `applicant_view` builds one. */
const SERVER_VIEW = {
  application_id: 42,
  status: "information_requested",
  status_title: "More information needed",
  status_message: "We need a clearer photo of the back of your ID.",
  next_action: { action: "respond", label: "Update your application" },
  editable: true,
  completeness: 72,
  fields: { full_name: "Ada Lovelace", seller_type: "business", seller_intent: ["services"] },
  documents: [
    { id: 9, type: "id_front", label: "ID front", filename: "front.jpg", size_kb: 220, uploaded_at: "2026-07-01", state: "accepted" }
  ],
  steps: [
    { key: "identity", title: "About you", summary: "Who is selling", fields: ["full_name"], complete: true, errors: {} },
    { key: "documents", title: "Documents", summary: "Verify it is you", fields: [], complete: false, errors: { id_back: "Required" } }
  ],
  can_submit: false,
  information_request: "Please re-upload the back of your ID.",
  submitted_at: "2026-06-30",
  updated_at: "2026-07-02",
  // The server names this key `value` for seller types and `type` for documents.
  seller_types: [{ value: "business", label: "Registered business" }, { value: "individual", label: "Individual" }],
  selling_intents: ["services", "digital goods"],
  required_documents: [{ type: "id_front", label: "ID front", required: true }, { type: "selfie", label: "Selfie", required: true }],
  optional_documents: [{ type: "tax_certificate", label: "Tax certificate", required: false }]
};

beforeEach(() => {
  mockPulseApi.mockReset();
  mockReadJsonCache.mockReset();
  mockWriteJsonCache.mockReset();
  mockWriteJsonCache.mockResolvedValue(undefined);
});

describe("normalizeSellerApplication", () => {
  it("keeps every field the server sent", () => {
    const view = normalizeSellerApplication(SERVER_VIEW);
    expect(view.application_id).toBe(42);
    expect(view.status).toBe("information_requested");
    expect(view.status_message).toBe("We need a clearer photo of the back of your ID.");
    expect(view.next_action).toEqual({ action: "respond", label: "Update your application" });
    expect(view.completeness).toBe(72);
    expect(view.fields.full_name).toBe("Ada Lovelace");
    expect(view.information_request).toBe("Please re-upload the back of your ID.");
    expect(view.can_submit).toBe(false);
    expect(view.editable).toBe(true);
  });

  it("reads the server's own key names for options rather than requiring one spelling", () => {
    // `seller_types` uses `value`; `required_documents` uses `type`. A parser
    // that insisted on `key` would render an empty picker and an applicant with
    // nothing to choose.
    const view = normalizeSellerApplication(SERVER_VIEW);
    expect(view.seller_types).toEqual([
      { key: "business", label: "Registered business" },
      { key: "individual", label: "Individual" }
    ]);
    expect(view.required_documents.map((doc) => doc.key)).toEqual(["id_front", "selfie"]);
    expect(view.optional_documents).toEqual([{ key: "tax_certificate", label: "Tax certificate" }]);
    expect(view.selling_intents).toEqual(["services", "digital goods"]);
  });

  it("carries per-step errors through so the screen can show them where they happened", () => {
    const view = normalizeSellerApplication(SERVER_VIEW);
    const documents = view.steps.find((step) => step.key === "documents");
    expect(documents?.complete).toBe(false);
    expect(documents?.errors).toEqual({ id_back: "Required" });
  });

  it("survives any malformed response rather than stranding an applicant mid-application", () => {
    // Totality is the point: a half-parsed response must degrade to the empty
    // draft, never throw. A crash here loses answers the applicant already typed.
    const garbage: unknown[] = [
      null,
      undefined,
      "not an object",
      42,
      [],
      { status: null, steps: "nope", documents: {}, seller_types: 7, next_action: "respond" }
    ];
    for (const input of garbage) {
      const view = normalizeSellerApplication(input);
      expect(Array.isArray(view.steps)).toBe(true);
      expect(Array.isArray(view.documents)).toBe(true);
      expect(Array.isArray(view.seller_types)).toBe(true);
      expect(typeof view.status).toBe("string");
      expect(view.next_action.action).toBe("continue");
    }
  });

  it("clamps completeness into the range a progress bar can render", () => {
    expect(normalizeSellerApplication({ completeness: 900 }).completeness).toBe(100);
    expect(normalizeSellerApplication({ completeness: -5 }).completeness).toBe(0);
    expect(normalizeSellerApplication({ completeness: "not a number" }).completeness).toBe(0);
  });

  it("defaults an unknown status to draft rather than to anything approved-adjacent", () => {
    // If a future server status reaches an old build, the safe reading is the
    // one that grants nothing.
    const view = normalizeSellerApplication({ status: "" });
    expect(view.status).toBe("draft");
    expect(emptySellerApplication().status).toBe("draft");
    expect(emptySellerApplication().editable).toBe(true);
    expect(emptySellerApplication().can_submit).toBe(false);
  });

  it("never reconstructs reviewer-only signals even when the server sends them", () => {
    // The server whitelists, so these should never arrive. If one ever does,
    // the client must not carry it into a rendered object.
    const contaminated = {
      ...SERVER_VIEW,
      internal_notes: [{ body: "applicant looks fine" }],
      reviewer_id: 3,
      risk_score: 88,
      decision_reason: "internal only",
      assigned_to: "admin@example.com"
    };
    const serialized = JSON.stringify(normalizeSellerApplication(contaminated));
    expect(serialized).not.toContain("internal_notes");
    expect(serialized).not.toContain("reviewer_id");
    expect(serialized).not.toContain("risk_score");
    expect(serialized).not.toContain("decision_reason");
    expect(serialized).not.toContain("assigned_to");
  });
});

describe("status helpers", () => {
  it("treats only the server's own editable flag as permission to edit", () => {
    // Editability is the server's call — it is what stops an applicant editing
    // the application out from under the reviewer reading it.
    expect(sellerApplicationIsEditable(normalizeSellerApplication({ status: "draft", editable: true }))).toBe(true);
    expect(sellerApplicationIsEditable(normalizeSellerApplication({ status: "draft", editable: false }))).toBe(false);
    expect(sellerApplicationIsEditable(normalizeSellerApplication({ status: "under_review", editable: false }))).toBe(false);
  });

  it("knows which states mean the applicant is waiting on us", () => {
    for (const status of ["submitted", "under_review", "resubmitted"]) {
      expect(sellerApplicationIsPending(normalizeSellerApplication({ status }))).toBe(true);
    }
    for (const status of ["draft", "information_requested", "approved", "rejected", "withdrawn", "expired", "suspended"]) {
      expect(sellerApplicationIsPending(normalizeSellerApplication({ status }))).toBe(false);
    }
  });

  it("gives every status a tone, and reserves the positive one for approval", () => {
    const statuses = [
      "draft", "submitted", "under_review", "information_requested", "resubmitted",
      "approved", "rejected", "withdrawn", "expired", "suspended"
    ] as const;
    const tones = statuses.map((status) => [status, sellerApplicationStatusTone(status)] as const);
    for (const [, tone] of tones) {
      expect(["positive", "warning", "critical", "neutral"]).toContain(tone);
    }
    expect(tones.filter(([, tone]) => tone === "positive").map(([status]) => status)).toEqual(["approved"]);
    expect(sellerApplicationStatusTone("rejected")).toBe("critical");
    expect(sellerApplicationStatusTone("suspended")).toBe("critical");
    expect(sellerApplicationStatusTone("information_requested")).toBe("warning");
  });
});

describe("requests", () => {
  it("loads from the documented endpoint and caches what came back", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, application: SERVER_VIEW });
    const view = await loadSellerApplication();
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pulse/seller/application");
    expect(view.application_id).toBe(42);
    expect(mockWriteJsonCache).toHaveBeenCalledWith("pulsesoc.native.seller.application", view);
  });

  it("still returns a usable view when the cache write fails", async () => {
    // The cache is a convenience. Losing it must not lose the response.
    mockPulseApi.mockResolvedValue({ ok: true, application: SERVER_VIEW });
    mockWriteJsonCache.mockRejectedValue(new Error("disk full"));
    await expect(loadSellerApplication()).resolves.toMatchObject({ application_id: 42 });
  });

  it("posts a draft as a patch of fields and adopts the status the server returns", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, application: { ...SERVER_VIEW, status: "draft", completeness: 30 } });
    const view = await saveSellerApplicationDraft({ full_name: "Ada Lovelace" });
    const [path, init] = mockPulseApi.mock.calls[0];
    expect(path).toBe("/api/pulse/seller/application/draft");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ fields: { full_name: "Ada Lovelace" } });
    // Autosave is safe precisely because it cannot advance an application; the
    // client must not pretend otherwise.
    expect(view.status).toBe("draft");
    expect(view.completeness).toBe(30);
  });

  it("takes submitted status from the response instead of assuming the submit worked", async () => {
    // A submit that the server refused — say a required document was removed
    // between rendering and tapping — must leave the screen showing the truth.
    mockPulseApi.mockResolvedValue({
      ok: false,
      message: "Upload the back of your ID before submitting.",
      application: { ...SERVER_VIEW, status: "draft", can_submit: false }
    });
    const { view, message } = await submitSellerApplication();
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pulse/seller/application/submit", expect.objectContaining({ method: "POST" }));
    expect(view.status).toBe("draft");
    expect(message).toBe("Upload the back of your ID before submitting.");
  });

  it("reports a real submission as whatever status the server moved it to", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, message: "Application submitted.", application: { ...SERVER_VIEW, status: "submitted", editable: false } });
    const { view } = await submitSellerApplication();
    expect(view.status).toBe("submitted");
    expect(sellerApplicationIsPending(view)).toBe(true);
    expect(sellerApplicationIsEditable(view)).toBe(false);
  });

  it("withdraws through its own endpoint", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, message: "Withdrawn.", application: { ...SERVER_VIEW, status: "withdrawn" } });
    const { view, message } = await withdrawSellerApplication();
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pulse/seller/application/withdraw", expect.objectContaining({ method: "POST" }));
    expect(view.status).toBe("withdrawn");
    expect(message).toBe("Withdrawn.");
  });

  it("propagates a network failure rather than resolving to an empty application", async () => {
    // Resolving to the empty draft would read on screen as "your answers are
    // gone", which is both false and alarming.
    mockPulseApi.mockRejectedValue(new Error("Network request failed"));
    await expect(loadSellerApplication()).rejects.toThrow("Network request failed");
    await expect(saveSellerApplicationDraft({ full_name: "Ada" })).rejects.toThrow("Network request failed");
  });
});

describe("documents", () => {
  it("uploads as multipart with the document type, and keeps no copy of the file", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, message: "Uploaded.", application: SERVER_VIEW });
    const { view } = await uploadSellerApplicationDocument("id_back", {
      uri: "file:///private/tmp/id-back.jpg",
      name: "id-back.jpg",
      mimeType: "image/jpeg"
    });

    const [path, init] = mockPulseApi.mock.calls[0];
    expect(path).toBe("/api/pulse/seller/application/documents");
    expect(init.method).toBe("POST");
    // FormData, not JSON: `pulseApi` only omits its JSON Content-Type for
    // FormData, and a stringified body here would upload the path as text.
    expect(init.body).toBeInstanceOf(FormData);
    expect(typeof init.body.append).toBe("function");

    // The response is server metadata only — no local URI is retained, and the
    // cached document carries no path the file could be read back from.
    const cached = JSON.stringify(view);
    expect(cached).not.toContain("file:///private/tmp/id-back.jpg");
    expect(cached).not.toContain("path");
    expect(view.documents[0]).toEqual({
      id: 9, type: "id_front", label: "ID front", filename: "front.jpg",
      size_kb: 220, uploaded_at: "2026-07-01", state: "accepted"
    });
  });

  it("removes a document by id through the server", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, message: "Removed.", application: { ...SERVER_VIEW, documents: [] } });
    const { view, message } = await removeSellerApplicationDocument(9);
    expect(mockPulseApi).toHaveBeenCalledWith(
      "/api/pulse/seller/application/documents/9/remove",
      expect.objectContaining({ method: "POST" })
    );
    expect(view.documents).toEqual([]);
    expect(message).toBe("Removed.");
  });

  it("offers only the formats a reviewer can open", async () => {
    // A file the reviewer cannot open costs the applicant a round trip through
    // information_requested, which is the slowest way to learn about a filter.
    (DocumentPicker.getDocumentAsync as jest.Mock).mockResolvedValue({
      canceled: false,
      assets: [{ uri: "file:///doc.pdf", name: "registration.pdf", mimeType: "application/pdf" }]
    });
    const asset = await pickSellerApplicationFile();
    expect(DocumentPicker.getDocumentAsync).toHaveBeenCalledWith(
      expect.objectContaining({ multiple: false, type: ["image/jpeg", "image/png", "application/pdf"] })
    );
    expect(asset).toEqual({ uri: "file:///doc.pdf", name: "registration.pdf", mimeType: "application/pdf" });
  });

  it("treats a cancelled picker as no choice rather than an empty upload", async () => {
    (DocumentPicker.getDocumentAsync as jest.Mock).mockResolvedValue({ canceled: true, assets: null });
    await expect(pickSellerApplicationFile()).resolves.toBeNull();
    (DocumentPicker.getDocumentAsync as jest.Mock).mockResolvedValue({ canceled: false, assets: [] });
    await expect(pickSellerApplicationFile()).resolves.toBeNull();
  });

  it("does not open the camera without permission", async () => {
    (ImagePicker.requestCameraPermissionsAsync as jest.Mock).mockResolvedValue({ granted: false });
    await expect(captureSellerApplicationPhoto()).resolves.toBeNull();
    expect(ImagePicker.launchCameraAsync).not.toHaveBeenCalled();
  });
});
