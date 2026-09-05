/**
 * Private Records carries the server's answers — and refusals — untranslated.
 *
 * The Operations client's product is the same as the rest of the Office: a
 * tagged result per call, never a thrown Error that collapses "we could not
 * look" into "there is nothing here". Each case pins one translation the
 * server decided in `services/private_office_routes.py`:
 *
 *   - a 423 (or a PRIVATE_OFFICE_LOCKED state word) is LOCKED, whatever else
 *     the body claims;
 *   - a writer's 400 arrives as REJECTED with the server's verbatim message,
 *     because it was written for a person;
 *   - a 404 on a status move is NOT_FOUND — "not yours" and "never existed"
 *     arrive identically, on purpose;
 *   - attention never renders a refusal as zeros. Confident zeros over real
 *     obligations are the failure that shape exists to prevent.
 */

const mockPulseApi = jest.fn();

// The real `PulseApiError` is kept deliberately: `refusal` narrows with an
// `instanceof` test, and a stubbed class would decide these cases for reasons
// unrelated to the code under test.
jest.mock("../pulseApi", () => ({
  ...jest.requireActual("../pulseApi"),
  pulseApi: (...args: unknown[]) => mockPulseApi(...args)
}));

jest.mock("expo-secure-store", () => ({
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => undefined),
  deleteItemAsync: jest.fn(async () => undefined),
  AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY: "afterFirstUnlockThisDeviceOnly"
}));

import { PulseApiError } from "../pulseApi";
import {
  PRIVATE_ATTENTION_PATH,
  PRIVATE_RECORDS_PATH,
  RECORD_VIEWS,
  asRecordView,
  createPrivateRecord,
  getPrivateAttention,
  getPrivateRecords,
  parsePrivateRecord,
  setPrivateRecordStatus
} from "../privateRecords";
import {
  OFFICE_DEVICE_HEADER,
  OFFICE_GRANT_HEADER,
  __resetOfficeLockForTests,
  setOfficeUnlocked
} from "../../privateOffice/officeLock";

const TOKEN = "grant-token-a1b2c3d4e5f6";

function apiError(
  status: number,
  details?: Record<string, unknown>,
  message = "refused"
): PulseApiError {
  return new PulseApiError(message, status, undefined, details);
}

function lastRequest(): { path: string; options: Record<string, any> } {
  const call = mockPulseApi.mock.calls[mockPulseApi.mock.calls.length - 1];
  return { path: call[0] as string, options: (call[1] ?? {}) as Record<string, any> };
}

/** A row exactly as `records._serialize` emits it. */
function rawRecord(overrides: Record<string, unknown> = {}) {
  return {
    id: 41,
    record_type: "OBLIGATION",
    title: "Renew home insurance",
    status: "OPEN",
    effective_status: "DUE_SOON",
    domain: "FINANCIAL",
    sensitivity: "NORMAL",
    source_type: "USER",
    created_at: "2026-08-20T09:00:00Z",
    updated_at: "2026-08-21T09:00:00Z",
    summary: "Policy 8841 lapses this month",
    question: "",
    outcome: "",
    due_at: "2026-09-14T00:00:00Z",
    occurred_at: "",
    amount: "$240",
    ...overrides
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  __resetOfficeLockForTests();
});

describe("asRecordView", () => {
  it("normalises case and whitespace into the canonical six", () => {
    expect(asRecordView("  Obligations ")).toBe("obligations");
    expect(asRecordView("DECISIONS")).toBe("decisions");
    RECORD_VIEWS.forEach((view) => expect(asRecordView(view)).toBe(view));
  });

  it("refuses anything outside the vocabulary rather than guessing", () => {
    expect(asRecordView("tasks")).toBeNull();
    expect(asRecordView("")).toBeNull();
    expect(asRecordView(undefined)).toBeNull();
    expect(asRecordView(7)).toBeNull();
  });
});

describe("parsePrivateRecord", () => {
  it("prefers summary for the long field and falls back to description", () => {
    expect(parsePrivateRecord(rawRecord({ summary: "the summary", description: "the description" })).body).toBe(
      "the summary"
    );
    expect(parsePrivateRecord(rawRecord({ summary: "", description: "the description" })).body).toBe(
      "the description"
    );
  });

  it("falls back to the decided status when no effective status arrived", () => {
    expect(parsePrivateRecord(rawRecord({ effective_status: "" })).effectiveStatus).toBe("OPEN");
  });
});

describe("getPrivateRecords", () => {
  it("parses a READY body — records, open count and the server's status vocabulary", async () => {
    mockPulseApi.mockResolvedValueOnce({
      records: [rawRecord()],
      open_count: 3,
      statuses: ["OPEN", "DONE", "WAIVED"]
    });
    const result = await getPrivateRecords("obligations");
    expect(result).toEqual({
      state: "READY",
      view: "obligations",
      records: [parsePrivateRecord(rawRecord())],
      openCount: 3,
      statuses: ["OPEN", "DONE", "WAIVED"]
    });
    expect(lastRequest().path).toBe(`${PRIVATE_RECORDS_PATH}/obligations`);
  });

  it("sends the office headers on every read, and the grant only once unlocked", async () => {
    mockPulseApi.mockResolvedValue({ records: [], open_count: 0, statuses: [] });
    await getPrivateRecords("events");
    const locked = lastRequest().options.headers as Record<string, string>;
    expect(locked[OFFICE_DEVICE_HEADER]).toBeTruthy();
    expect(locked[OFFICE_GRANT_HEADER]).toBeUndefined();

    setOfficeUnlocked(TOKEN, new Date(Date.now() + 900_000).toISOString(), 4021);
    await getPrivateRecords("events");
    const unlocked = lastRequest().options.headers as Record<string, string>;
    expect(unlocked[OFFICE_GRANT_HEADER]).toBe(TOKEN);
  });

  it("maps a 423 to LOCKED before any entitlement word gets a say", async () => {
    mockPulseApi.mockRejectedValueOnce(
      apiError(423, { state: "NOT_ENTITLED", minimum_tier: "gold", setup_required: false })
    );
    expect(await getPrivateRecords("obligations")).toEqual({ state: "LOCKED", setupRequired: false });
  });

  it("recognises the lock by state word alone, and carries setup_required", async () => {
    mockPulseApi.mockRejectedValueOnce(
      apiError(403, { state: "PRIVATE_OFFICE_LOCKED", setup_required: true })
    );
    expect(await getPrivateRecords("risks")).toEqual({ state: "LOCKED", setupRequired: true });
  });

  it("still names the other refusals when no lock is involved", async () => {
    mockPulseApi.mockRejectedValueOnce(apiError(403, { state: "NOT_ENTITLED", minimum_tier: "PRIVATE" }));
    expect(await getPrivateRecords("decisions")).toEqual({
      state: "NOT_ENTITLED",
      minimumTier: "PRIVATE"
    });

    mockPulseApi.mockRejectedValueOnce(apiError(403, { state: "FEATURE_DISABLED" }));
    expect(await getPrivateRecords("decisions")).toEqual({ state: "FEATURE_DISABLED" });

    mockPulseApi.mockRejectedValueOnce(apiError(501, { state: "NOT_IMPLEMENTED" }));
    expect(await getPrivateRecords("decisions")).toEqual({ state: "NOT_IMPLEMENTED" });

    mockPulseApi.mockRejectedValueOnce(apiError(500, { state: "UNAVAILABLE" }));
    expect(await getPrivateRecords("decisions")).toEqual({ state: "UNAVAILABLE" });

    mockPulseApi.mockRejectedValueOnce(apiError(503, {}));
    expect(await getPrivateRecords("decisions")).toEqual({ state: "UNAVAILABLE" });
  });

  it("keeps a plain failure as ERROR rather than dressing it as a server refusal", async () => {
    mockPulseApi.mockRejectedValueOnce(new TypeError("network down"));
    expect(await getPrivateRecords("requests")).toEqual({ state: "ERROR", message: "" });

    mockPulseApi.mockRejectedValueOnce(apiError(500, {}, "boom"));
    expect(await getPrivateRecords("requests")).toEqual({ state: "ERROR", message: "boom" });
  });
});

describe("createPrivateRecord", () => {
  it("posts the draft to the view's route and returns the created record", async () => {
    mockPulseApi.mockResolvedValueOnce({ record: rawRecord() });
    const result = await createPrivateRecord("obligations", {
      title: "Renew home insurance",
      obligation_type: "INSURANCE_RENEWAL",
      due_at: "2026-09-14T00:00:00Z"
    });
    expect(result).toEqual({ state: "OK", record: parsePrivateRecord(rawRecord()) });
    expect(lastRequest().path).toBe(`${PRIVATE_RECORDS_PATH}/obligations`);
    expect(lastRequest().options.method).toBe("POST");
    expect((lastRequest().options.headers as Record<string, string>)[OFFICE_DEVICE_HEADER]).toBeTruthy();
  });

  it("trims every field and omits the empty ones from the request body", async () => {
    mockPulseApi.mockResolvedValueOnce({ record: null });
    await createPrivateRecord("requests", {
      title: "  Ask accountant  ",
      description: "",
      category: "   ",
      due_at: undefined
    });
    expect(JSON.parse(lastRequest().options.body)).toEqual({ title: "Ask accountant" });
  });

  it("relays the writer's 400 verbatim — it was written for a person", async () => {
    mockPulseApi.mockRejectedValueOnce(
      apiError(400, { message: "due_at must be a date in the future" })
    );
    expect(await createPrivateRecord("obligations", { title: "x" })).toEqual({
      state: "REJECTED",
      message: "due_at must be a date in the future"
    });
  });

  it("maps the shared refusals like the reads do", async () => {
    mockPulseApi.mockRejectedValueOnce(apiError(423, { setup_required: true }));
    expect(await createPrivateRecord("events", { title: "x" })).toEqual({
      state: "LOCKED",
      setupRequired: true
    });

    mockPulseApi.mockRejectedValueOnce(apiError(403, { state: "NOT_ENTITLED", minimum_tier: "PRIVATE" }));
    expect(await createPrivateRecord("events", { title: "x" })).toEqual({
      state: "NOT_ENTITLED",
      minimumTier: "PRIVATE"
    });

    mockPulseApi.mockRejectedValueOnce(new RangeError("nope"));
    expect(await createPrivateRecord("events", { title: "x" })).toEqual({ state: "ERROR", message: "" });
  });
});

describe("setPrivateRecordStatus", () => {
  it("posts the status to the record's own route, with the outcome only when given", async () => {
    mockPulseApi.mockResolvedValue({ record: rawRecord({ status: "DONE" }) });
    const moved = await setPrivateRecordStatus("obligations", 41, "DONE");
    expect(moved).toEqual({ state: "OK", record: parsePrivateRecord(rawRecord({ status: "DONE" })) });
    expect(lastRequest().path).toBe(`${PRIVATE_RECORDS_PATH}/obligations/41/status`);
    expect(lastRequest().options.method).toBe("POST");
    expect(JSON.parse(lastRequest().options.body)).toEqual({ status: "DONE" });
    expect((lastRequest().options.headers as Record<string, string>)[OFFICE_DEVICE_HEADER]).toBeTruthy();

    await setPrivateRecordStatus("decisions", 7, "DECIDED", "  Take the offer  ");
    expect(JSON.parse(lastRequest().options.body)).toEqual({
      status: "DECIDED",
      outcome: "Take the offer"
    });
  });

  it("keeps 'not yours' and 'never existed' as one NOT_FOUND", async () => {
    mockPulseApi.mockRejectedValueOnce(apiError(404, {}));
    expect(await setPrivateRecordStatus("risks", 999, "CLOSED")).toEqual({ state: "NOT_FOUND" });
  });

  it("relays a 400 verbatim, and maps the lock like every other call", async () => {
    mockPulseApi.mockRejectedValueOnce(apiError(400, { message: "unknown status word" }));
    expect(await setPrivateRecordStatus("risks", 41, "BOGUS")).toEqual({
      state: "REJECTED",
      message: "unknown status word"
    });

    mockPulseApi.mockRejectedValueOnce(apiError(423, { setup_required: false }));
    expect(await setPrivateRecordStatus("risks", 41, "CLOSED")).toEqual({
      state: "LOCKED",
      setupRequired: false
    });
  });
});

describe("getPrivateAttention", () => {
  it("parses counts per known view and the due-soon obligations", async () => {
    mockPulseApi.mockResolvedValueOnce({
      counts: { obligations: 3, risks: 1, not_a_view: 9 },
      due_soon: [rawRecord()]
    });
    const attention = await getPrivateAttention();
    expect(attention).toEqual({
      state: "READY",
      counts: { obligations: 3, risks: 1 },
      dueSoon: [parsePrivateRecord(rawRecord())]
    });
    expect(lastRequest().path).toBe(PRIVATE_ATTENTION_PATH);
    expect((lastRequest().options.headers as Record<string, string>)[OFFICE_DEVICE_HEADER]).toBeTruthy();
  });

  it("never renders a refusal as zeros — an entitlement no becomes REFUSED, empty", async () => {
    mockPulseApi.mockRejectedValueOnce(apiError(403, { state: "NOT_ENTITLED", minimum_tier: "PRIVATE" }));
    expect(await getPrivateAttention()).toEqual({ state: "REFUSED", counts: {}, dueSoon: [] });
  });

  it("keeps the lock and the outage as their own words", async () => {
    mockPulseApi.mockRejectedValueOnce(apiError(423, { setup_required: false }));
    expect(await getPrivateAttention()).toEqual({ state: "LOCKED", counts: {}, dueSoon: [] });

    mockPulseApi.mockRejectedValueOnce(apiError(503, {}));
    expect(await getPrivateAttention()).toEqual({ state: "UNAVAILABLE", counts: {}, dueSoon: [] });

    // A dead network is a failure to look, not a refusal with a reason.
    mockPulseApi.mockRejectedValueOnce(new TypeError("network down"));
    expect(await getPrivateAttention()).toEqual({ state: "UNAVAILABLE", counts: {}, dueSoon: [] });
  });
});
