/**
 * "Discussed in N conversations", and the sentence it is not allowed to print.
 *
 * This component exists so that four screens — documents, records, facts,
 * meetings — share one answer to "where was this talked about". That makes it
 * the single place where a wrong branch would tell a member, on four different
 * surfaces, that an object was never discussed when in truth the read failed.
 * So the mutual-exclusion claim is pinned here rather than once per host
 * screen:
 *
 *   **"Not referenced in any conversation" may only render after a READY
 *   read.** Every refusal in the tagged union is checked for it, not just the
 *   convenient one, because the states differ in how they arrive — a 403 is a
 *   thrown `PulseApiError`, a 423 additionally relocks the office, and a
 *   network fault is a rejected promise with no status at all.
 *
 * ## Why this stubs the transport, not the client
 *
 * `pulseApi` is mocked and `src/api/privateConversations` is left entirely
 * real, so each case runs the true parser and the true refusal translator on
 * its way to the render. Stubbing `listConversationsForTarget` instead would
 * reduce these to a test that a hand-built result object renders — which
 * nobody doubted — and would let a parser that turns a 503 into an empty list
 * pass here unnoticed.
 *
 * `t` returns the key, per the convention in the screen suites: the assertions
 * survive a copy edit and fail on a wiring change.
 */

import React from "react";
import { fireEvent, render, waitFor } from "@testing-library/react-native";

jest.mock("../../i18n", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) => options?.defaultValue || key
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

import { PulseApiError } from "../../api/pulseApi";
import { LinkedConversations } from "../LinkedConversations";
import {
  __resetOfficeLockForTests,
  isOfficeUnlocked,
  setOfficeUnlocked
} from "../officeLock";

/** The copy that is a claim about data, and therefore the copy under guard. */
const EMPTY_LINE = "premium:privateOffice.conversations.linked.none";
const UNAVAILABLE_LINE = "premium:privateOffice.conversations.linked.unavailable";

/**
 * Mirrors how `pulseApiRequest` constructs a failure: `details` is the whole
 * parsed body and is the 4th argument. Building the error by hand rather than
 * importing a fixture keeps this suite honest about the shape the client is
 * actually handed at runtime.
 */
function wireError(status: number, body: Record<string, unknown>) {
  return new PulseApiError(
    String(body.message || "failed"),
    status,
    body.code ? String(body.code) : undefined,
    body
  );
}

function renderLinked(onOpen = jest.fn()) {
  return {
    onOpen,
    ...render(
      <LinkedConversations linkType="DOCUMENT" targetId={42} onOpenConversation={onOpen} />
    )
  };
}

const ONE_THREAD = {
  ok: true,
  count: 1,
  conversations: [
    {
      id: 77,
      conversation_id: 77,
      title: "Acquisition working group",
      private_office: { office_scope: "PROJECT_ROOM", sensitivity: "CONFIDENTIAL" }
    }
  ],
  capabilities: {}
};

beforeEach(() => {
  jest.clearAllMocks();
  __resetOfficeLockForTests();
});

describe("what it asks for", () => {
  it("reads the reverse-lookup route once per target", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, conversations: [], capabilities: {} });
    const { getByText } = renderLinked();

    await waitFor(() => getByText(EMPTY_LINE));
    expect(mockPulseApi).toHaveBeenCalledTimes(1);
    const [path] = mockPulseApi.mock.calls[0];
    expect(String(path)).toContain("/links/DOCUMENT/42");
  });
});

describe("a successful read", () => {
  it("lists the threads that reference the object", async () => {
    mockPulseApi.mockResolvedValue(ONE_THREAD);
    const { getByText, queryByText } = renderLinked();

    await waitFor(() => getByText("Acquisition working group"));
    // The heading is a label, not a claim, so it may accompany rows.
    expect(getByText("premium:privateOffice.conversations.linked.title")).toBeTruthy();
    // But the empty line is a claim, and this read was not empty.
    expect(queryByText(EMPTY_LINE)).toBeNull();
  });

  it("opens the canonical thread rather than an Office-side reader", async () => {
    mockPulseApi.mockResolvedValue(ONE_THREAD);
    const { getByText, onOpen } = renderLinked();

    await waitFor(() => getByText("Acquisition working group"));
    fireEvent.press(getByText("Acquisition working group"));
    expect(onOpen).toHaveBeenCalledWith(77);
  });

  it("says 'not referenced anywhere' when the server genuinely returned none", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, count: 0, conversations: [], capabilities: {} });
    const { getByText, queryByText } = renderLinked();

    await waitFor(() => getByText(EMPTY_LINE));
    expect(queryByText(UNAVAILABLE_LINE)).toBeNull();
  });
});

describe("a refusal is never an empty list", () => {
  // The bodies below are what `private_office_routes._gate`,
  // `_locked_refusal` and `_unavailable` actually emit — `state` is the
  // machine-readable field, and NOT_IMPLEMENTED and FEATURE_DISABLED share a
  // 404 because there is nothing to sell in either case.
  const refusals: [string, number, Record<string, unknown>][] = [
    [
      "a tier the member does not hold",
      403,
      { ok: false, state: "NOT_ENTITLED", minimum_tier: "ELITE" }
    ],
    ["the kill switch", 404, { ok: false, state: "FEATURE_DISABLED" }],
    ["a surface that is not built", 404, { ok: false, state: "NOT_IMPLEMENTED" }],
    [
      "a locked office",
      423,
      { ok: false, state: "PRIVATE_OFFICE_LOCKED", code: "PRIVATE_OFFICE_LOCKED" }
    ],
    ["a server fault", 503, { ok: false, state: "unavailable" }]
  ];

  it.each(refusals)("%s does not print the empty line", async (_label, status, body) => {
    mockPulseApi.mockRejectedValue(wireError(status, body));
    const { getByText, queryByText } = renderLinked();

    await waitFor(() => getByText(UNAVAILABLE_LINE));
    expect(queryByText(EMPTY_LINE)).toBeNull();
  });

  it("does not print the empty line when the request never reached the server", async () => {
    // No status at all: the transport threw. This is the case a `status`-keyed
    // refusal table is most likely to fall through, so it gets its own case.
    mockPulseApi.mockRejectedValue(new Error("Network request failed"));
    const { getByText, queryByText } = renderLinked();

    await waitFor(() => getByText(UNAVAILABLE_LINE));
    expect(queryByText(EMPTY_LINE)).toBeNull();
  });
});

describe("before the answer arrives", () => {
  it("claims nothing at all while the read is in flight", async () => {
    let release: (value: unknown) => void = () => undefined;
    mockPulseApi.mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      })
    );
    const { queryByText } = renderLinked();

    // Not the empty line, and not the refusal line either: a claim not yet
    // made is not a claim, and an in-flight read has not failed.
    expect(queryByText(EMPTY_LINE)).toBeNull();
    expect(queryByText(UNAVAILABLE_LINE)).toBeNull();

    release({ ok: true, conversations: [], capabilities: {} });
    await waitFor(() => queryByText(EMPTY_LINE));
  });
});

describe("recovery", () => {
  it("re-reads on retry and replaces the refusal with the real answer", async () => {
    mockPulseApi.mockRejectedValueOnce(wireError(503, { ok: false, state: "unavailable" }));
    const { getByText, queryByText } = renderLinked();

    await waitFor(() => getByText(UNAVAILABLE_LINE));
    mockPulseApi.mockResolvedValue(ONE_THREAD);
    fireEvent.press(getByText("premium:privateOffice.retry"));

    await waitFor(() => getByText("Acquisition working group"));
    expect(queryByText(UNAVAILABLE_LINE)).toBeNull();
    expect(queryByText(EMPTY_LINE)).toBeNull();
  });

  it("does not offer retry for a refusal that retrying cannot change", async () => {
    // Re-asking the server whether the member holds a tier they do not hold
    // is a button that is guaranteed to disappoint.
    mockPulseApi.mockRejectedValue(wireError(403, { ok: false, state: "NOT_ENTITLED" }));
    const { getByText, queryByText } = renderLinked();

    await waitFor(() => getByText(UNAVAILABLE_LINE));
    expect(queryByText("premium:privateOffice.retry")).toBeNull();
  });
});

describe("the office lock", () => {
  it("relocks the app-wide store when the server says the office is locked", async () => {
    setOfficeUnlocked("grant-token", new Date(Date.now() + 900_000).toISOString(), 4021);
    expect(isOfficeUnlocked(4021)).toBe(true);

    mockPulseApi.mockRejectedValue(wireError(423, {
      ok: false,
      state: "PRIVATE_OFFICE_LOCKED",
      code: "PRIVATE_OFFICE_LOCKED"
    }));
    const { getByText } = renderLinked();

    await waitFor(() => getByText(UNAVAILABLE_LINE));
    // It relocks the shared store rather than drawing its own door: the host
    // screen already sits inside `PrivateOfficeLockGate`, and the gate is the
    // one thing allowed to decide what a locked office looks like.
    expect(isOfficeUnlocked(4021)).toBe(false);
  });
});
