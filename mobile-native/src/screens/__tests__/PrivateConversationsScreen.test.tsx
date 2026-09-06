/**
 * Private Conversations, and the two sentences this screen is not allowed to
 * print.
 *
 * The API suite (`src/api/__tests__/privateConversations.test.ts`) proves the
 * client parses honestly. That is necessary and not sufficient: a correct
 * parser feeding a screen that renders the wrong branch still lies to the
 * member. So the claims are re-pinned here, at the layer the member actually
 * reads:
 *
 *   1. **"No conversations yet" only ever appears after a successful read.**
 *      A refusal drawn as an empty list is how a member concludes their threads
 *      were deleted and stops looking for them. Every refusal state is checked
 *      for this, not just the convenient one.
 *   2. **"End-to-end encrypted" only ever appears when the server said `true`.**
 *      Stage 53. The screen must not reach that string from a missing field,
 *      from the string `"true"`, or from a note that merely talks about
 *      encryption.
 *
 * ## Why this mocks the transport, not the client
 *
 * `pulseApi` is stubbed and `privateConversations` is left entirely real, so
 * these cases run the actual parser, the actual `asStrictTrue`, and the actual
 * refusal translator on their way to the render. Stubbing the client instead
 * would let a permissive parse pass here — the suite would be asserting that a
 * hand-built `capabilities` object renders, which nobody doubted. The raw
 * bodies below are the shapes `private_office_conversations_routes.py` emits.
 *
 * `t` returns the key, per the convention in the other screen tests: these
 * assertions survive a copy edit and fail on a wiring change.
 */

import React from "react";
import { fireEvent, render, waitFor, within } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));

jest.mock("../../i18n", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) => options?.defaultValue || key
  }),
  useFormatters: () => ({
    date: (value: string) => value,
    number: (value: number) => String(value)
  })
}));

jest.mock("expo-secure-store", () => ({
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => undefined),
  deleteItemAsync: jest.fn(async () => undefined),
  AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY: "afterFirstUnlockThisDeviceOnly"
}));

const mockPulseApi = jest.fn();

jest.mock("../../api/pulseApi", () => ({
  ...jest.requireActual("../../api/pulseApi"),
  pulseApi: (...args: unknown[]) => mockPulseApi(...args)
}));

const mockOfficeStatus = jest.fn();
const mockUnlockOffice = jest.fn();

// The two security reads are stubbed at the same boundary the Facts suite uses:
// the screen is wrapped in `PrivateOfficeLockGate`, which asks
// `/security/status` before it renders anything and mints a grant through
// `/security/unlock`. The gate, the lock store and the unlock flow all stay
// real, so the rows below render for the same reason they render in
// production — a live grant exists.
jest.mock("../../api/privateOffice", () => ({
  ...jest.requireActual("../../api/privateOffice"),
  getOfficeSecurityStatus: (...args: unknown[]) => mockOfficeStatus(...args),
  unlockOffice: (...args: unknown[]) => mockUnlockOffice(...args)
}));

// An unlock grant belongs to an account, and the gate relocks on any mount that
// finds no signed-in member. Without a session this suite would be modelling a
// signed-out device rather than a member with a locked office.
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
import {
  __resetOfficeLockForTests,
  isOfficeUnlocked,
  setOfficeUnlocked
} from "../../privateOffice/officeLock";
import { PrivateConversationsScreen } from "../PrivateConversationsScreen";

/** The member's office passcode for this suite. Any other value is refused. */
const OFFICE_PASSCODE = "846195";

/** The i18n keys this suite reasons about, named once. */
const EMPTY_TITLE = "premium:privateOffice.conversations.empty.title";
const ENCRYPTED = "premium:privateOffice.conversations.security.encrypted";
const NOT_ENCRYPTED = "premium:privateOffice.conversations.security.notEncrypted";
const LOADING = "premium:privateOffice.feature.loading";
const RETRY = "premium:privateOffice.feature.retry";

/** A conversation row exactly as the routes module emits it. */
function rawRow(overrides: Record<string, unknown> = {}) {
  const { private_office: office, ...rest } = overrides as Record<string, unknown> & {
    private_office?: Record<string, unknown>;
  };
  return {
    id: 77,
    conversation_id: 77,
    conversation_type: "group",
    title: "Acquisition working group",
    unread_count: 2,
    last_message_preview: "Draft terms attached.",
    private_office: {
      office_scope: "PROJECT_ROOM",
      sensitivity: "CONFIDENTIAL",
      organization_node_id: 0,
      operations_project_id: 4,
      meeting_id: 0,
      archived: false,
      conversation_domain: "PRIVATE_OFFICE",
      ...office
    },
    links: [],
    ...rest
  };
}

/** Capabilities exactly as the routes module emits them. */
function rawCapabilities(overrides: Record<string, unknown> = {}) {
  return {
    feature_id: "private_office.conversations",
    enabled: true,
    message_ledger: "comm_v2_messages",
    attachment_authority: "message_attachments",
    rtc_provider: "agora",
    scopes: ["DIRECT", "GROUP", "ORGANIZATION_ROOM", "PROJECT_ROOM"],
    link_types: ["DOCUMENT", "RECORD", "FACT", "MEETING", "ORGANIZATION_NODE", "PROJECT"],
    end_to_end_encrypted: false,
    disappearing_messages: false,
    ...overrides
  };
}

/** A successful list response, straight off the wire. */
function listBody(
  rows: Record<string, unknown>[],
  capabilities: Record<string, unknown> = {}
) {
  return {
    ok: true,
    conversations: rows,
    count: rows.length,
    capabilities: rawCapabilities(capabilities)
  };
}

/**
 * The error the transport would actually throw for a status and body — the
 * same construction `pulseApiRequest` performs, with the body in the fourth
 * argument where `details` lives.
 */
function wireError(status: number, body: Record<string, unknown>): PulseApiError {
  return new PulseApiError(
    String(body.message || body.error || "PulseSoc request failed."),
    status,
    typeof body.error_code === "string" ? body.error_code : undefined,
    body
  );
}

/**
 * Open the second lock the way a member does. Nothing here bypasses it — the
 * list is unreachable until this runs.
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
    <PrivateConversationsScreen
      route={{ key: "c", name: "PrivateConversations", params } as never}
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
  mockPulseApi.mockResolvedValue(listBody([rawRow()]));
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

describe("Stage 53 — the screen never claims encryption the server did not", () => {
  /**
   * The mutation this is built to kill: the footnote relaxed from
   * `capabilities.endToEndEncrypted` to a truthy check, or the client's
   * `asStrictTrue` relaxed underneath it. Both survive a test that only feeds
   * boolean `false`, so the discriminating bodies are the absent one and the
   * truthy-but-not-`true` ones — and the last row proves the encrypted string
   * is reachable at all, without which every case above passes vacuously.
   */
  it.each([
    ["the field is absent", NOT_ENCRYPTED, { end_to_end_encrypted: undefined }],
    ["the field is null", NOT_ENCRYPTED, { end_to_end_encrypted: null }],
    ["the field is the string \"true\"", NOT_ENCRYPTED, { end_to_end_encrypted: "true" }],
    ["the field is the number 1", NOT_ENCRYPTED, { end_to_end_encrypted: 1 }],
    ["the field is boolean false", NOT_ENCRYPTED, { end_to_end_encrypted: false }],
    ["the field is boolean true", ENCRYPTED, { end_to_end_encrypted: true }]
  ])("says %s → renders %s", async (_label, expected, capabilities) => {
    mockPulseApi.mockResolvedValue(listBody([rawRow()], capabilities));
    const { getByText, queryByText } = await renderScreen();
    await waitFor(() => getByText(expected));
    const forbidden = expected === ENCRYPTED ? NOT_ENCRYPTED : ENCRYPTED;
    expect(queryByText(forbidden)).toBeNull();
  });

  it("does not infer encryption from a note that talks about encryption", async () => {
    // The server's own sentence is shown verbatim when it sent one. A note
    // containing the words is still not a `true`, and the screen must print the
    // note rather than upgrade to the encrypted claim.
    mockPulseApi.mockResolvedValue(
      listBody([rawRow()], {
        end_to_end_encrypted: false,
        encryption_note: "Encrypted in transit and at rest. Not end-to-end encrypted."
      })
    );
    const { getByText, queryByText } = await renderScreen();
    await waitFor(() =>
      getByText("Encrypted in transit and at rest. Not end-to-end encrypted.")
    );
    expect(queryByText(ENCRYPTED)).toBeNull();
  });

  it("says nothing about security when it could not read the capabilities", async () => {
    // No capabilities means no claim in either direction. Falling back to the
    // reassuring string on a failed read would be the same lie in a quieter
    // voice.
    mockPulseApi.mockRejectedValue(wireError(503, { ok: false, state: "UNAVAILABLE" }));
    const { getByText, queryByText } = await renderScreen();
    await waitFor(() => getByText("premium:privateOffice.feature.unavailable.title"));
    expect(queryByText(ENCRYPTED)).toBeNull();
    expect(queryByText(NOT_ENCRYPTED)).toBeNull();
  });
});

describe("a refusal is never drawn as an empty list", () => {
  /**
   * The claim under test is mutual exclusion, so each case asserts both halves:
   * the refusal is named, *and* the empty-state copy is absent. A test that
   * only checked the first half would pass against a screen that rendered both
   * panels stacked — which is the actual failure mode, since the empty branch
   * and the refusal branches are separate conditionals over the same state.
   */
  it.each([
    [402, "notEntitled", { ok: false, state: "NOT_ENTITLED", minimum_tier: "elite" }],
    [403, "disabled", { ok: false, state: "FEATURE_DISABLED" }],
    [404, "notImplemented", { ok: false, state: "NOT_IMPLEMENTED" }],
    [503, "unavailable", { ok: false, state: "UNAVAILABLE" }],
    [500, "error", { ok: false, message: "boom" }]
  ])("HTTP %s renders the %s panel and no empty state", async (status, stem, body) => {
    mockPulseApi.mockRejectedValue(wireError(status, body));
    const { getByText, queryByText } = await renderScreen();
    await waitFor(() => getByText(`premium:privateOffice.feature.${stem}.title`));
    // The distinction the whole result union exists to protect.
    expect(queryByText(EMPTY_TITLE)).toBeNull();
    // And nothing is invented to fill the space.
    expect(queryByText("Acquisition working group")).toBeNull();
  });

  it("offers a retry only where retrying could change the answer", async () => {
    mockPulseApi.mockRejectedValue(wireError(503, { ok: false, state: "UNAVAILABLE" }));
    const transient = await renderScreen();
    await waitFor(() => transient.getByText(RETRY));

    __resetOfficeLockForTests();
    // A plan is not a transient failure, so there is nothing to retry.
    mockPulseApi.mockRejectedValue(
      wireError(402, { ok: false, state: "NOT_ENTITLED", minimum_tier: "elite" })
    );
    const entitlement = await renderScreen();
    await waitFor(() =>
      entitlement.getByText("premium:privateOffice.feature.notEntitled.title")
    );
    expect(entitlement.queryByText(RETRY)).toBeNull();
  });

  it("renders an empty office as empty, and invents no rows to fill it", async () => {
    mockPulseApi.mockResolvedValue(listBody([]));
    const { getByText, queryByText } = await renderScreen();
    await waitFor(() => getByText(EMPTY_TITLE));
    expect(getByText("premium:privateOffice.conversations.empty.body")).toBeTruthy();
    // An empty office is a real answer, not a failure to get one.
    expect(queryByText(RETRY)).toBeNull();
    expect(queryByText("premium:privateOffice.feature.error.title")).toBeNull();
  });

  it("claims nothing at all while the read is still in flight", async () => {
    // A promise that never settles: in the window where the screen knows
    // nothing it must say so, and must not pre-render either verdict.
    mockPulseApi.mockReturnValue(new Promise(() => undefined));
    const { getByText, queryByText } = await renderScreen();
    expect(getByText(LOADING)).toBeTruthy();
    expect(queryByText(EMPTY_TITLE)).toBeNull();
    expect(queryByText("premium:privateOffice.feature.error.title")).toBeNull();
  });
});

describe("the rows are canonical threads, opened in the one thread screen", () => {
  it("renders what the server classified, through the canonical normalizer", async () => {
    const { getByText, getByLabelText } = await renderScreen();
    await waitFor(() => getByText("Acquisition working group"));
    // Scoped to the row rather than to the screen: the scope vocabulary also
    // appears in the filter chips above, so a bare `getByText` would match the
    // chip and pass even if the row never rendered its classification.
    const row = within(getByLabelText("Acquisition working group"));
    expect(row.getByText("Draft terms attached.")).toBeTruthy();
    expect(
      row.getByText("premium:privateOffice.conversations.scopes.PROJECT_ROOM")
    ).toBeTruthy();
    // The mock `t` prefers `defaultValue`, so this exercises the row's fallback
    // branch. In the app the catalog string wins, because `defaultValue` only
    // applies when the key is missing — which is precisely what makes keeping
    // the fallback safe, and what the next case relies on.
    expect(row.getByText("CONFIDENTIAL")).toBeTruthy();
    // The canonical unread count, not an Office-side recount.
    expect(row.getByText("2")).toBeTruthy();
  });

  it("prints a sensitivity level this build has never heard of, rather than a blank", async () => {
    // The ladder is versioned server-side. If it gains a level before the app
    // does, the honest render is the server's own word: dropping it would show
    // a thread as unclassified, and showing the raw key would show plumbing.
    mockPulseApi.mockResolvedValue(
      listBody([rawRow({ private_office: { sensitivity: "QUANTUM_SEALED" } })])
    );
    const { getByText, getByLabelText } = await renderScreen();
    await waitFor(() => getByText("Acquisition working group"));
    const row = within(getByLabelText("Acquisition working group"));
    expect(row.getByText("QUANTUM_SEALED")).toBeTruthy();
  });

  it("opens a thread in Chat rather than a second Office thread screen", async () => {
    // The "no second messenger" constraint, made checkable. If a
    // `PrivateConversationThread` route ever appears, this fails.
    const { getByText, navigation } = await renderScreen();
    await waitFor(() => getByText("Acquisition working group"));
    fireEvent.press(getByText("Acquisition working group"));
    expect(navigation.navigate).toHaveBeenCalledWith("Chat", {
      conversationId: 77,
      title: "Acquisition working group"
    });
  });

  it("routes the classification to the info screen, which is the part that is new", async () => {
    const { getByText, getByLabelText, navigation } = await renderScreen();
    await waitFor(() => getByText("Acquisition working group"));
    fireEvent.press(getByLabelText("premium:privateOffice.conversations.info.open"));
    expect(navigation.navigate).toHaveBeenCalledWith("PrivateConversationInfo", {
      conversationId: 77
    });
  });

  it("shows an archived thread as archived rather than hiding it", async () => {
    mockPulseApi.mockResolvedValue(
      listBody([rawRow({ private_office: { archived: true } })])
    );
    const { getByText } = await renderScreen();
    await waitFor(() => getByText("premium:privateOffice.conversations.archived"));
    expect(getByText("Acquisition working group")).toBeTruthy();
  });
});

describe("the scope filter asks the server rather than filtering locally", () => {
  it("re-reads on a scope change and does not leave the old rows underneath", async () => {
    const { getByText, getByLabelText, queryByText } = await renderScreen();
    await waitFor(() => getByText("Acquisition working group"));

    // The second read never settles, so the screen is caught mid-flight — which
    // is exactly the window in which stale rows would still be on screen if the
    // list were not cleared before the request.
    mockPulseApi.mockReturnValue(new Promise(() => undefined));
    fireEvent.press(getByLabelText("premium:privateOffice.conversations.scopes.DIRECT"));

    await waitFor(() => expect(queryByText("Acquisition working group")).toBeNull());
    expect(getByText(LOADING)).toBeTruthy();
    // And the request carried the scope: the server decides membership, not us.
    expect(mockPulseApi).toHaveBeenLastCalledWith(
      "/api/private-office/conversations?scope=DIRECT",
      expect.anything()
    );
  });

  it("ignores a scope param it does not recognize instead of showing nothing", async () => {
    // An unrecognized deep-link scope falls back to "all". Treating it as a
    // filter would render an empty office for a member who has threads.
    const { getByText } = await renderScreen({ scope: "NONSENSE" });
    await waitFor(() => getByText("Acquisition working group"));
    expect(mockPulseApi).toHaveBeenLastCalledWith(
      "/api/private-office/conversations",
      expect.anything()
    );
  });
});

describe("the office lock is honoured after it has been opened", () => {
  it("relocks the office when the server answers a read with 423", async () => {
    // A grant can expire between the unlock and the next read. The 423 itself
    // must drive the relock.
    //
    // The first version of this test called `__resetOfficeLockForTests()` before
    // re-rendering, which relocked the office by hand — so the lock door
    // appeared for a reason that had nothing to do with the 423, and the
    // mutation battery correctly reported it as vacuous. Nothing here touches
    // the lock store: the grant minted at unlock stays live, and the only thing
    // that can close the door is the screen reacting to the refusal.
    const { getByText, getByLabelText, queryByText } = await renderScreen();
    await waitFor(() => getByText("Acquisition working group"));
    expect(isOfficeUnlocked()).toBe(true);

    mockPulseApi.mockRejectedValue(
      wireError(423, { ok: false, state: "LOCKED", setup_required: false })
    );
    // Any re-read will do; a scope tap is the one a member actually performs.
    fireEvent.press(getByLabelText("premium:privateOffice.conversations.scopes.DIRECT"));

    await waitFor(() => getByText("premium:privateOffice.lock.unlock"));
    expect(isOfficeUnlocked()).toBe(false);
    // The lock door, not an empty office and not the stale rows behind it.
    expect(queryByText(EMPTY_TITLE)).toBeNull();
    expect(queryByText("Acquisition working group")).toBeNull();
  });
});
