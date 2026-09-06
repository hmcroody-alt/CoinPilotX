/**
 * Private Meetings — the screen's honesty properties, and the per-row
 * disclosure that now hosts `LinkedConversations`.
 *
 * The screen renders three server buckets and a set of host-only actions. Two
 * classes of lie are cheap to ship here and invisible in review:
 *
 *   1. A read that failed drawn as "you have no meetings". The refusal panel and
 *      the empty panel are different components on purpose; nothing but a test
 *      stops a future edit from rendering the empty one whenever `buckets` is
 *      falsy, which is also true of every failure.
 *   2. The reverse-link panel answering for the wrong object. Each row discloses
 *      its own panel, keyed by `public_id`. If the disclosure state or the
 *      target id were shared across rows, opening one meeting would show another
 *      meeting's conversations — and the member has no way to tell, because the
 *      panel renders titles, not ids.
 *
 * The link transport is stubbed at `pulseApi`, not at the panel: the real
 * client, the real parser and the real refusal translator stay in the path. A
 * stubbed panel would leave a suite proving that a hand-built result object
 * renders, which nobody doubted.
 *
 * `t` returns the key, per the convention in the other screen tests: these
 * assertions survive a copy edit and fail on a wiring change.
 */

import React from "react";
import { fireEvent, render, waitFor } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));

jest.mock("../../i18n", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) => options?.defaultValue || key
  }),
  useFormatters: () => ({ date: (value: string) => value, number: (value: number) => String(value) })
}));

const mockListMeetings = jest.fn();
const mockPulseApi = jest.fn();
const mockOfficeStatus = jest.fn();
const mockUnlockOffice = jest.fn();

jest.mock("../../api/pulseApi", () => ({
  ...jest.requireActual("../../api/pulseApi"),
  pulseApi: (...args: unknown[]) => mockPulseApi(...args)
}));

// Only the list read is replaced. The refusal mapping in `meetings/api` is not
// under test here, so cases that need a refusal throw the `MeetingRefusal` that
// module would have thrown.
jest.mock("../../privateOffice/meetings/api", () => ({
  ...jest.requireActual("../../privateOffice/meetings/api"),
  listMeetings: (...args: unknown[]) => mockListMeetings(...args)
}));

// Entering a meeting reaches the canonical call session store, which reaches
// Agora and (transitively) expo-av's native module. None of that is exercised
// by these cases, so the module is replaced outright rather than spread from
// `requireActual` — spreading would still execute the real module's imports and
// fail the suite at load on `ExponentAV`. These two are the only symbols the
// screen imports from here; a third would fail loudly rather than silently.
jest.mock("../../privateOffice/meetings/meetingSession", () => ({
  enterMeeting: jest.fn(async (ref: string) => ({ meetingRef: ref, meeting: null })),
  enterMeetingWithProjection: jest.fn(async () => undefined)
}));

jest.mock("../../api/privateOffice", () => ({
  ...jest.requireActual("../../api/privateOffice"),
  getOfficeSecurityStatus: (...args: unknown[]) => mockOfficeStatus(...args),
  unlockOffice: (...args: unknown[]) => mockUnlockOffice(...args)
}));

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

import { PulseApiError } from "../../api/pulseApi";
import { MeetingRefusal } from "../../privateOffice/meetings/types";
import {
  __resetOfficeLockForTests,
  isOfficeUnlocked,
  setOfficeUnlocked
} from "../../privateOffice/officeLock";
import { PrivateMeetingsScreen } from "../PrivateMeetingsScreen";

const OFFICE_PASSCODE = "846195";

/** A row exactly as `office.meetings` projects it for a non-host viewer. */
function meeting(overrides: Record<string, unknown> = {}) {
  return {
    public_id: "mtg-alpha",
    title: "Quarterly review",
    status: "SCHEDULED",
    waiting_room_enabled: false,
    locked: false,
    scheduled_start_at: "2026-09-10T15:00:00Z",
    duration_minutes: 30,
    started_at: "",
    ended_at: "",
    end_reason: "",
    owner_user_id: 4021,
    me: null,
    participants: [],
    recording_active: false,
    capabilities: {},
    created_at: "2026-09-01T10:00:00Z",
    ...overrides
  };
}

function buckets(over: Partial<Record<"live" | "upcoming" | "recent", unknown[]>> = {}) {
  return { live: [], upcoming: [], recent: [], ...over };
}

/** Open the second lock the way a member does — nothing here bypasses it. */
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

async function renderScreen() {
  const navigation = { navigate: jest.fn(), goBack: jest.fn(), setOptions: jest.fn() };
  const utils = render(
    <PrivateMeetingsScreen
      route={{ key: "m", name: "PrivateMeetings", params: {} } as never}
      navigation={navigation as never}
    />
  );
  await unlockOffice(utils);
  return { ...utils, navigation };
}

beforeEach(() => {
  jest.clearAllMocks();
  __resetOfficeLockForTests();
  mockListMeetings.mockResolvedValue(buckets({ upcoming: [meeting()] }));
  // Default: the reverse lookup answers honestly with nothing. Cases that care
  // override it. Without a default, every disclosure test would exercise the
  // refusal path by accident.
  mockPulseApi.mockResolvedValue({ ok: true, count: 0, conversations: [], capabilities: {} });
  mockOfficeStatus.mockResolvedValue({
    state: "READY",
    passcodeSet: true,
    setupRequired: false,
    cooldownSeconds: 0,
    biometricPreference: "unset",
    unlocked: false
  });
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

const EMPTY_LINE = "premium:privateOffice.conversations.linked.none";
const UNAVAILABLE_LINE = "premium:privateOffice.conversations.linked.unavailable";
const EMPTY_TITLE = "premium:privateOffice.meetings.empty.title";

describe("PrivateMeetingsScreen", () => {
  it("renders the rows the server bucketed, and invents none", async () => {
    const utils = await renderScreen();
    await waitFor(() => utils.getByText("Quarterly review"));
    expect(utils.getByText("premium:privateOffice.meetings.buckets.upcoming")).toBeTruthy();
    // The other two buckets came back empty; no heading may appear for them.
    expect(utils.queryByText("premium:privateOffice.meetings.buckets.live")).toBeNull();
    expect(utils.queryByText("premium:privateOffice.meetings.buckets.recent")).toBeNull();
    // A populated list is not an empty one.
    expect(utils.queryByText(EMPTY_TITLE)).toBeNull();
  });

  it("draws a schedule it could not read as a refusal, never as an empty schedule", async () => {
    // This is the failure that matters: "we could not look" printed as "you
    // have nothing" is how a member concludes a meeting they booked was lost.
    mockListMeetings.mockRejectedValue(new MeetingRefusal("unavailable", 503, "down"));
    const utils = await renderScreen();
    await waitFor(() => utils.getByText("premium:privateOffice.feature.unavailable.title"));
    expect(utils.queryByText(EMPTY_TITLE)).toBeNull();
  });

  it("says the schedule is empty only when the server actually returned none", async () => {
    mockListMeetings.mockResolvedValue(buckets());
    const utils = await renderScreen();
    await waitFor(() => utils.getByText(EMPTY_TITLE));
    expect(utils.queryByText("premium:privateOffice.feature.unavailable.title")).toBeNull();
  });
});

describe("PrivateMeetingsScreen — linked conversations", () => {
  /** Render, then disclose the row with this title. */
  async function discloseRow(title = "Quarterly review") {
    const utils = await renderScreen();
    await waitFor(() => utils.getByText(title));
    fireEvent.press(utils.getByText(title));
    return utils;
  }

  it("asks nothing until the member opens a row", async () => {
    const utils = await renderScreen();
    await waitFor(() => utils.getByText("Quarterly review"));
    // Three buckets' worth of eager reverse lookups is exactly what the
    // one-at-a-time disclosure exists to avoid.
    expect(mockPulseApi).not.toHaveBeenCalled();
    expect(utils.queryByText("premium:privateOffice.conversations.linked.title")).toBeNull();
  });

  it("asks the reverse-link route about this meeting, under the MEETING link type", async () => {
    await discloseRow();
    await waitFor(() => expect(mockPulseApi).toHaveBeenCalled());
    const path = String(mockPulseApi.mock.calls[0][0]);
    expect(path).toContain("/links/MEETING/mtg-alpha");
    // The public id is the member-facing handle. The meeting code is host-only
    // and must never become a link key.
    expect(path).not.toContain("meeting_code");
  });

  it("does not print the empty line when the reverse lookup was refused", async () => {
    mockPulseApi.mockRejectedValue(
      new PulseApiError("upstream", 503, undefined, { state: "unavailable" })
    );
    const utils = await discloseRow();
    await waitFor(() => utils.getByText(UNAVAILABLE_LINE));
    expect(utils.queryByText(EMPTY_LINE)).toBeNull();
  });

  it("says nothing was found only when the server genuinely returned none", async () => {
    const utils = await discloseRow();
    await waitFor(() => utils.getByText(EMPTY_LINE));
    expect(utils.queryByText(UNAVAILABLE_LINE)).toBeNull();
  });

  it("opens the canonical thread rather than a second reader", async () => {
    mockPulseApi.mockResolvedValue({
      ok: true,
      count: 1,
      // The row IS the canonical conversation with Office keys added — it is
      // not nested under a `conversation` field. Shaped as the server sends it
      // so the real normalizer is what turns it into a row.
      conversations: [{ id: 77, conversation_id: 77, title: "Budget thread" }],
      capabilities: {}
    });
    const utils = await discloseRow();
    await waitFor(() => utils.getByText("Budget thread"));
    fireEvent.press(utils.getByText("Budget thread"));
    expect(utils.navigation.navigate).toHaveBeenCalledWith("Chat", { conversationId: 77 });
  });

  it("closes the row again, and asks about the second meeting rather than the first", async () => {
    // The disclosure is keyed by `public_id`. Were it a shared boolean, opening
    // the second row would leave the first row's panel — and its answer — on
    // screen, attributing one meeting's conversations to another.
    mockListMeetings.mockResolvedValue(
      buckets({
        upcoming: [meeting(), meeting({ public_id: "mtg-beta", title: "Board sync" })]
      })
    );
    const utils = await renderScreen();
    await waitFor(() => utils.getByText("Board sync"));

    fireEvent.press(utils.getByText("Quarterly review"));
    await waitFor(() => utils.getByText(EMPTY_LINE));
    expect(String(mockPulseApi.mock.calls[0][0])).toContain("/mtg-alpha");

    fireEvent.press(utils.getByText("Board sync"));
    await waitFor(() => expect(mockPulseApi).toHaveBeenCalledTimes(2));
    expect(String(mockPulseApi.mock.calls[1][0])).toContain("/mtg-beta");
    // Exactly one panel is mounted: the second row's.
    expect(utils.getAllByText("premium:privateOffice.conversations.linked.title")).toHaveLength(1);

    // Pressing the open row again closes it and leaves no panel behind.
    fireEvent.press(utils.getByText("Board sync"));
    await waitFor(() =>
      expect(utils.queryByText("premium:privateOffice.conversations.linked.title")).toBeNull()
    );
  });
});
