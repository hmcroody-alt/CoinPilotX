/**
 * Private Operations, and the ways a screen about obligations can lie.
 *
 * The screen's promise mirrors Private Facts': every row came from the
 * member's own store, and every refusal names the actual reason. The cases
 * here pin the properties review cannot see:
 *
 *   1. The refusals stay separate, and UNAVAILABLE is never drawn as EMPTY —
 *      "we could not look" dressed as "nothing needs you" is how a member
 *      misses a deadline the server knew about.
 *   2. A LOCKED answer relocks the office locally, so the gate's door comes
 *      back instead of a dead screen behind a stale grant.
 *   3. The status sheet lists exactly the statuses the server returned for
 *      this view, minus the record's current one — no local copy of the
 *      vocabulary that would go stale when the server grows a word.
 *   4. A writer's rejection is shown verbatim. It was written for a person,
 *      and a generic "invalid input" leaves the member unable to fix it.
 *
 * `t` returns the key, per the convention in the other screen tests: these
 * assertions survive a copy edit and fail on a wiring change.
 */

import React from "react";
import { act, fireEvent, render, waitFor } from "@testing-library/react-native";

// No `SafeAreaProvider` in the test tree; the house pattern is to stub the
// insets rather than wrap every render.
jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));

jest.mock("../../i18n", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) => options?.defaultValue || key
  }),
  useFormatters: () => ({ date: (value: string) => value, number: (value: number) => String(value) })
}));

const mockGetRecords = jest.fn();
const mockGetOverview = jest.fn();
const mockCreateRecord = jest.fn();
const mockSetStatus = jest.fn();
const mockOfficeStatus = jest.fn();
const mockUnlockOffice = jest.fn();

// Only the network calls are replaced. `parsePrivateRecord`, `asRecordView`
// and `RECORD_VIEWS` are the real ones — they are the contract under test, and
// a stubbed parser would leave a suite that proves the stub agrees with itself.
jest.mock("../../api/privateRecords", () => ({
  ...jest.requireActual("../../api/privateRecords"),
  getPrivateRecords: (...args: unknown[]) => mockGetRecords(...args),
  getPrivateOverview: (...args: unknown[]) => mockGetOverview(...args),
  createPrivateRecord: (...args: unknown[]) => mockCreateRecord(...args),
  setPrivateRecordStatus: (...args: unknown[]) => mockSetStatus(...args)
}));

// The screen is wrapped in `PrivateOfficeLockGate`: it asks `/security/status`
// before it will render anything, and mints a grant through `/security/unlock`.
// Both are replaced with the answers a member who has set a passcode actually
// gets. The gate, the lock store and the unlock flow all stay real —
// `unlockOffice`'s stub does exactly what the real one does once the server has
// answered (stow the minted grant via `setOfficeUnlocked`), so the records
// below only render for the same reason they render in production: a live
// grant exists.
jest.mock("../../api/privateOffice", () => ({
  ...jest.requireActual("../../api/privateOffice"),
  getOfficeSecurityStatus: (...args: unknown[]) => mockOfficeStatus(...args),
  unlockOffice: (...args: unknown[]) => mockUnlockOffice(...args)
}));

// An unlock grant belongs to an account, and the gate relocks on every mount
// that finds no signed-in member (`reconcileOfficeOwner`). Without a session
// the suite would be modelling a signed-out device rather than a member with a
// locked office, so the envelope is supplied here and the grant is bound to it.
jest.mock("../../session/sessionStore", () => ({
  ...jest.requireActual("../../session/sessionStore"),
  getSessionEnvelope: async () => ({
    version: 1,
    userId: 4021,
    accessToken: "access-token",
    accessTokenExpiresAt: Date.now() + 600_000,
    refreshToken: "refresh-token",
    refreshTokenExpiresAt: Date.now() + 600_000
  })
}));

import { parseOverview, parsePrivateRecord } from "../../api/privateRecords";
import {
  __resetOfficeLockForTests,
  isOfficeUnlocked,
  setOfficeUnlocked
} from "../../privateOffice/officeLock";
import { PrivateOperationsScreen } from "../PrivateOperationsScreen";

/** The member's office passcode for this suite. Any other value is refused. */
const OFFICE_PASSCODE = "846195";

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
    amount: "",
    ...overrides
  };
}

function ready(
  rows: Record<string, unknown>[],
  view = "obligations",
  statuses = ["OPEN", "DONE", "WAIVED"]
) {
  return {
    state: "READY",
    view,
    records: rows.map(parsePrivateRecord),
    openCount: rows.length,
    statuses
  };
}

/** A payload exactly as `operations.overview` emits it. */
function rawOverview(overrides: Record<string, unknown> = {}) {
  return {
    as_of: "2026-09-05T12:00:00Z",
    needs_attention: 3,
    due_today: 1,
    due_this_week: 2,
    overdue: 1,
    pending_decisions: 4,
    open_requests: 2,
    awaiting_response: 1,
    active_risks: 5,
    active_high_risks: 2,
    active_opportunities: 3,
    recently_completed: 6,
    // The field the model genuinely cannot answer.
    expiring_opportunities: "UNSUPPORTED",
    counts: { OBLIGATION: 7 },
    attention: {
      items: [
        {
          id: 41,
          record_type: "OBLIGATION",
          // Deliberately not the title of the row in the list below. The queue
          // and the list are two different reads of the same store, and giving
          // them the same string here would let a test pass by finding the
          // wrong one.
          title: "File the quarterly return",
          status: "OPEN",
          effective_status: "OVERDUE",
          due_at: "2026-09-01T00:00:00Z",
          deadline_field: "due_at",
          priority: "HIGH",
          severity: "",
          domain: "FINANCIAL",
          updated_at: "2026-08-21T09:00:00Z",
          primary_reason: "OVERDUE",
          reasons: ["OVERDUE"]
        }
      ],
      total: 3,
      truncated: false
    },
    recent_activity: [],
    recent_window_days: 14,
    unsupported: { EXPIRING_OPPORTUNITY: "no expiry column" },
    ...overrides
  };
}

function overviewReady(overrides: Record<string, unknown> = {}) {
  return { state: "READY", overview: parseOverview(rawOverview(overrides)) };
}

/**
 * Open the second lock the way a member does: the gate's own passcode field and
 * unlock button. Nothing here bypasses the lock — the records are unreachable
 * until this runs.
 */
async function unlockOffice(utils: ReturnType<typeof render>) {
  const { getByLabelText, getByText, queryByText } = utils;
  if (isOfficeUnlocked()) return;
  await waitFor(() => getByText("premium:privateOffice.lock.unlock"));
  fireEvent.changeText(
    getByLabelText("premium:privateOffice.lock.placeholder"),
    OFFICE_PASSCODE
  );
  // Pressability latches `disabled` one effect flush behind the prop, so a
  // press dispatched in the same turn as the passcode entry silently no-ops.
  await waitFor(() =>
    expect(getByLabelText("premium:privateOffice.lock.placeholder").props.value).toBe(
      OFFICE_PASSCODE
    )
  );
  fireEvent.press(getByText("premium:privateOffice.lock.unlock"));
  await waitFor(() => expect(queryByText("premium:privateOffice.lock.unlock")).toBeNull());
}

async function renderScreen(params: Record<string, unknown> = {}) {
  const navigation = { navigate: jest.fn(), goBack: jest.fn(), setOptions: jest.fn() };
  const utils = render(
    <PrivateOperationsScreen
      route={{ key: "o", name: "PrivateOperations", params } as never}
      navigation={navigation as never}
    />
  );
  await unlockOffice(utils);
  return { ...utils, navigation };
}

beforeEach(() => {
  jest.clearAllMocks();
  // Every case starts locked: the in-memory grant does not survive a test.
  __resetOfficeLockForTests();
  mockGetRecords.mockResolvedValue(ready([rawRecord()]));
  mockGetOverview.mockResolvedValue(overviewReady());
  mockCreateRecord.mockResolvedValue({ state: "OK", record: null });
  mockSetStatus.mockResolvedValue({ state: "OK", record: null });
  mockOfficeStatus.mockResolvedValue({
    state: "READY",
    passcodeSet: true,
    setupRequired: false,
    cooldownSeconds: 0,
    biometricPreference: "unset",
    unlocked: false
  });
  // Mirrors the real `unlockOffice`: the server is the only thing that can mint
  // a grant, and a wrong passcode mints nothing.
  mockUnlockOffice.mockImplementation(async (passcode: string, userId: number) => {
    if (passcode !== OFFICE_PASSCODE) return { state: "WRONG_PASSCODE" };
    setOfficeUnlocked(
      "office-grant-token",
      new Date(Date.now() + 300_000).toISOString(),
      Number(userId) || 0
    );
    return { state: "UNLOCKED" };
  });
});

describe("PrivateOperationsScreen", () => {
  it("says it is loading before the server has answered", async () => {
    mockGetRecords.mockReturnValue(new Promise(() => undefined));
    const { getByText } = await renderScreen();
    expect(getByText("premium:privateOffice.operations.loading")).toBeTruthy();
  });

  it("reads the view from the route and renders the records the server sent", async () => {
    mockGetRecords.mockResolvedValue(
      ready([rawRecord({ title: "Vendor concentration", record_type: "RISK" })], "risks", [
        "OPEN",
        "MITIGATED"
      ])
    );
    const { getByText } = await renderScreen({ view: "risks" });
    await waitFor(() => getByText("Vendor concentration"));
    expect(mockGetRecords).toHaveBeenCalledWith("risks");
    expect(getByText("Policy 8841 lapses this month")).toBeTruthy();
    // The effective status is the server's word, carried across the label hook.
    expect(getByText("DUE_SOON")).toBeTruthy();
  });

  it("falls back to obligations when the route names a view that does not exist", async () => {
    const { getByText } = await renderScreen({ view: "chores" });
    await waitFor(() => getByText("Renew home insurance"));
    expect(mockGetRecords).toHaveBeenCalledWith("obligations");
  });

  it("re-reads the server for the view a pressed chip names", async () => {
    const { getByText } = await renderScreen();
    await waitFor(() => getByText("Renew home insurance"));
    fireEvent.press(getByText("premium:privateOffice.operations.views.decisions"));
    await waitFor(() => expect(mockGetRecords).toHaveBeenCalledWith("decisions"));
  });

  it("renders an empty view as empty, and invents no rows to fill it", async () => {
    mockGetRecords.mockResolvedValue(ready([]));
    const { getByText, queryByText } = await renderScreen();
    await waitFor(() => getByText("premium:privateOffice.operations.empty.title"));
    expect(getByText("premium:privateOffice.operations.emptyBody.obligations")).toBeTruthy();
    // An empty store is a real answer, not a failure to get one.
    expect(queryByText("premium:privateOffice.retry")).toBeNull();
  });

  it("does not draw a store it could not read as a store that is empty", async () => {
    mockGetRecords.mockResolvedValue({ state: "UNAVAILABLE" });
    const { getByText, queryByText } = await renderScreen();
    await waitFor(() => getByText("premium:privateOffice.operations.unavailable.title"));
    // The distinction the whole result union exists to protect.
    expect(queryByText("premium:privateOffice.operations.empty.title")).toBeNull();
    expect(getByText("premium:privateOffice.retry")).toBeTruthy();
    // A refused read also offers no composer: there is nothing to add rows to.
    expect(queryByText("premium:privateOffice.operations.add.obligations")).toBeNull();
  });

  it("names the tier when the plan does not include the capability", async () => {
    mockGetRecords.mockResolvedValue({ state: "NOT_ENTITLED", minimumTier: "PRIVATE" });
    const { getByText, queryByText } = await renderScreen();
    await waitFor(() => getByText("premium:privateOffice.operations.notEntitled.title"));
    expect(getByText("premium:privateOffice.operations.notEntitled.body")).toBeTruthy();
    // Nothing to retry: a plan is not a transient failure.
    expect(queryByText("premium:privateOffice.retry")).toBeNull();
  });

  it("falls back to generic upgrade copy when the server named no tier", async () => {
    mockGetRecords.mockResolvedValue({ state: "NOT_ENTITLED", minimumTier: "" });
    const { getByText, queryByText } = await renderScreen();
    await waitFor(() => getByText("premium:privateOffice.operations.notEntitled.bodyGeneric"));
    expect(queryByText("premium:privateOffice.operations.notEntitled.body")).toBeNull();
  });

  it("relocks the office locally when the server answers LOCKED", async () => {
    // The read is held open until the member is through the gate, so the LOCKED
    // answer arrives while the office reads as unlocked — the exact state a
    // grant revoked on another device produces.
    let release: (value: unknown) => void = () => undefined;
    mockGetRecords.mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      })
    );
    const { getByText } = await renderScreen();
    expect(isOfficeUnlocked()).toBe(true);
    await act(async () => {
      release({ state: "LOCKED", setupRequired: false });
    });
    // The gate follows the store: the door comes back without a network call.
    await waitFor(() => getByText("premium:privateOffice.lock.unlock"));
    expect(isOfficeUnlocked()).toBe(false);
  });

  it("lists the server's statuses minus the current one, and moves through the chosen word", async () => {
    const { getByText, queryByText } = await renderScreen();
    await waitFor(() => getByText("Renew home insurance"));
    fireEvent.press(getByText("premium:privateOffice.operations.move"));
    await waitFor(() => getByText("DONE"));
    // The record's own status is not an option — the sheet offers moves, not a
    // restatement — and the vocabulary is the server's, not a local copy.
    expect(getByText("WAIVED")).toBeTruthy();
    expect(queryByText("OPEN")).toBeNull();

    fireEvent.press(getByText("DONE"));
    await waitFor(() => expect(mockSetStatus).toHaveBeenCalledWith("obligations", 41, "DONE"));
    // A successful move re-reads the view rather than editing the row locally.
    await waitFor(() => expect(mockGetRecords).toHaveBeenCalledTimes(2));
  });

  it("shows a rejected move's server message verbatim", async () => {
    mockSetStatus.mockResolvedValue({
      state: "REJECTED",
      message: "This decision is already settled"
    });
    const { getByText } = await renderScreen();
    await waitFor(() => getByText("Renew home insurance"));
    fireEvent.press(getByText("premium:privateOffice.operations.move"));
    await waitFor(() => getByText("DONE"));
    fireEvent.press(getByText("DONE"));
    // The writer's words, not a generic failure banner.
    await waitFor(() => getByText("This decision is already settled"));
    // A rejection changes nothing, so nothing is re-read.
    expect(mockGetRecords).toHaveBeenCalledTimes(1);
  });

  it("sends the composer's draft with the kind normalised to the server's token grammar", async () => {
    const { getByText, getByPlaceholderText, queryByText } = await renderScreen();
    await waitFor(() => getByText("Renew home insurance"));
    fireEvent.press(getByText("premium:privateOffice.operations.add.obligations"));
    await waitFor(() => getByPlaceholderText("premium:privateOffice.operations.form.title"));
    fireEvent.changeText(
      getByPlaceholderText("premium:privateOffice.operations.form.title"),
      "  Renew passport  "
    );
    fireEvent.changeText(
      getByPlaceholderText("premium:privateOffice.operations.form.kind"),
      "insurance renewal"
    );
    fireEvent.changeText(
      getByPlaceholderText("premium:privateOffice.operations.form.details"),
      "Expires next quarter"
    );
    fireEvent.changeText(
      getByPlaceholderText("premium:privateOffice.operations.form.due"),
      "2027-01-15"
    );
    fireEvent.press(getByText("premium:privateOffice.operations.save"));
    await waitFor(() =>
      expect(mockCreateRecord).toHaveBeenCalledWith("obligations", {
        title: "Renew passport",
        obligation_type: "INSURANCE_RENEWAL",
        summary: "Expires next quarter",
        due_at: "2027-01-15T00:00:00Z"
      })
    );
    // Success closes the sheet and re-reads the view.
    await waitFor(() =>
      expect(queryByText("premium:privateOffice.operations.form.kind")).toBeNull()
    );
    await waitFor(() => expect(mockGetRecords).toHaveBeenCalledTimes(2));
  });

  it("renders the executive summary above the six views, from the server's numbers", async () => {
    const { getByText } = await renderScreen();
    await waitFor(() => getByText("premium:privateOffice.operations.overview.heading"));
    // The four headline figures.
    expect(getByText("premium:privateOffice.operations.overview.needsAttention")).toBeTruthy();
    expect(getByText("premium:privateOffice.operations.overview.dueToday")).toBeTruthy();
    expect(getByText("premium:privateOffice.operations.overview.overdue")).toBeTruthy();
    expect(getByText("premium:privateOffice.operations.overview.pendingDecisions")).toBeTruthy();
    // The rest of the summary the mission asks for.
    expect(getByText("premium:privateOffice.operations.overview.dueThisWeek")).toBeTruthy();
    expect(getByText("premium:privateOffice.operations.overview.highRisks")).toBeTruthy();
    expect(getByText("premium:privateOffice.operations.overview.openRequests")).toBeTruthy();
    expect(getByText("premium:privateOffice.operations.overview.opportunities")).toBeTruthy();
    expect(getByText("premium:privateOffice.operations.overview.recentlyCompleted")).toBeTruthy();
    // The six views are still there. This slice adds a summary; it does not
    // replace the thing the summary is about.
    expect(getByText("premium:privateOffice.operations.views.risks")).toBeTruthy();
    await waitFor(() => getByText("Renew home insurance"));
  });

  it("shows an unanswerable figure as not tracked rather than as zero", async () => {
    const { getByText, queryByText } = await renderScreen();
    await waitFor(() => getByText("premium:privateOffice.operations.overview.heading"));
    // `expiring_opportunities` has no column behind it. A zero here would be
    // indistinguishable from a real one, and the member would have no way to
    // know they were reading a gap.
    expect(getByText("premium:privateOffice.operations.overview.notTracked")).toBeTruthy();
    expect(queryByText("premium:privateOffice.operations.overview.unavailable")).toBeNull();
  });

  it("draws the ranked queue with each row's strongest reason", async () => {
    const { getAllByText, getByText } = await renderScreen();
    await waitFor(() => getByText("premium:privateOffice.operations.overview.queue"));
    expect(getByText("File the quarterly return")).toBeTruthy();
    // The reason is the server's, carried on the item — not recomputed here
    // from the row, which is how a chip and an ordering come to disagree. The
    // label hook resolves through `defaultValue`, so this is the reason name.
    expect(getByText("OVERDUE")).toBeTruthy();
    // And the queue row names the view that holds it, beside the chip.
    expect(
      getAllByText("premium:privateOffice.operations.views.obligations").length
    ).toBeGreaterThan(1);
  });

  it("reports the server's true total, not the length of the page it drew", async () => {
    const many = Array.from({ length: 9 }, (_, n) => ({
      id: 100 + n,
      record_type: "REQUEST",
      title: `Request ${n}`,
      status: "WAITING_ON_USER",
      effective_status: "WAITING_ON_USER",
      due_at: "",
      deadline_field: "deadline_at",
      priority: "NORMAL",
      severity: "",
      domain: "",
      updated_at: "2026-08-21T09:00:00Z",
      primary_reason: "RESPONSE_REQUIRED",
      reasons: ["RESPONSE_REQUIRED"]
    }));
    mockGetOverview.mockResolvedValue(
      overviewReady({ attention: { items: many, total: 312, truncated: true } })
    );
    const { getByText, queryByText } = await renderScreen();
    await waitFor(() => getByText("premium:privateOffice.operations.overview.truncated"));
    // Six drawn, 312 reported. Counting the page instead would tell a member
    // with 312 problems that they have six — correct until it matters most.
    expect(getByText("Request 5")).toBeTruthy();
    expect(queryByText("Request 6")).toBeNull();
  });

  it("says nothing needs attention only when the server said the queue was empty", async () => {
    mockGetOverview.mockResolvedValue(
      overviewReady({
        needs_attention: 0,
        attention: { items: [], total: 0, truncated: false }
      })
    );
    const { getByText } = await renderScreen();
    await waitFor(() => getByText("premium:privateOffice.operations.overview.queueEmpty"));
  });

  it("prints no figures at all when the overview could not be read", async () => {
    mockGetOverview.mockResolvedValue({ state: "UNAVAILABLE" });
    const { getByText, queryByText } = await renderScreen();
    await waitFor(() => getByText("premium:privateOffice.operations.overview.unavailable"));
    // The failure this whole shape exists to prevent: a wall of confident zeros
    // over a read that never happened.
    expect(queryByText("premium:privateOffice.operations.overview.needsAttention")).toBeNull();
    expect(queryByText("premium:privateOffice.operations.overview.queue")).toBeNull();
    expect(queryByText("premium:privateOffice.operations.overview.queueEmpty")).toBeNull();
  });

  it("treats a missing overview route as a refusal, not as an empty summary", async () => {
    // An optional route pack that failed to register looks exactly like this.
    mockGetOverview.mockResolvedValue({ state: "NOT_FOUND" });
    const { getByText, queryByText } = await renderScreen();
    await waitFor(() => getByText("premium:privateOffice.operations.overview.unavailable"));
    expect(queryByText("premium:privateOffice.operations.overview.dueToday")).toBeNull();
    // The six views are unaffected: this slice must not take the screen down
    // with a summary that is not there.
    await waitFor(() => getByText("Renew home insurance"));
  });

  it("relocks the office when the overview alone answers LOCKED", async () => {
    mockGetOverview.mockResolvedValue({ state: "LOCKED", setupRequired: false });
    const { getByText } = await renderScreen();
    await waitFor(() => getByText("premium:privateOffice.lock.unlock"));
    expect(isOfficeUnlocked()).toBe(false);
  });

  it("switches to the view that holds a queue row when the row is pressed", async () => {
    const { getByText } = await renderScreen({ view: "decisions" });
    await waitFor(() => getByText("File the quarterly return"));
    expect(mockGetRecords).toHaveBeenCalledWith("decisions");
    // The queue row names OBLIGATION; the list route wants "obligations". The
    // two vocabularies meet in exactly one place, and this is the assertion
    // that it is the right one.
    fireEvent.press(getByText("File the quarterly return"));
    await waitFor(() => expect(mockGetRecords).toHaveBeenCalledWith("obligations"));
  });

  it("re-reads the summary after a status move, so the header cannot contradict the list", async () => {
    const { getByText } = await renderScreen();
    await waitFor(() => getByText("Renew home insurance"));
    expect(mockGetOverview).toHaveBeenCalledTimes(1);
    fireEvent.press(getByText("premium:privateOffice.operations.move"));
    await waitFor(() => getByText("DONE"));
    fireEvent.press(getByText("DONE"));
    // A member who just cleared their last overdue item must not still be
    // looking at "1 overdue".
    await waitFor(() => expect(mockGetOverview).toHaveBeenCalledTimes(2));
  });

  it("keeps a rejected creation's message verbatim, inside the still-open sheet", async () => {
    mockCreateRecord.mockResolvedValue({
      state: "REJECTED",
      message: "title is required"
    });
    const { getByText, getByPlaceholderText } = await renderScreen();
    await waitFor(() => getByText("Renew home insurance"));
    fireEvent.press(getByText("premium:privateOffice.operations.add.obligations"));
    await waitFor(() => getByPlaceholderText("premium:privateOffice.operations.form.title"));
    fireEvent.press(getByText("premium:privateOffice.operations.save"));
    await waitFor(() => getByText("title is required"));
    // The sheet stays open so the member can fix what the server named.
    expect(getByPlaceholderText("premium:privateOffice.operations.form.title")).toBeTruthy();
    expect(mockGetRecords).toHaveBeenCalledTimes(1);
  });
});
