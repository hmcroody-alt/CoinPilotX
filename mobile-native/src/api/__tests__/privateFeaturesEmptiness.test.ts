/**
 * A refusal and an empty result must never render the same.
 *
 * Relationship Intelligence answers a tagged result, and the screen renders
 * `READY` with a zero-length list as a settled statement about the member's own
 * affairs: "No people recorded yet." That sentence is only true if the server
 * actually said so.
 *
 * `pulseApi` already throws on a non-2xx, on `ok: false`, and on a body it
 * could not parse, so the whole obvious class of failure never reaches these
 * parsers. What is left is narrow and real: a well-formed 200 carrying a
 * payload that is not the one the parser was written against — a route that
 * changed shape, a cache or proxy answering with a different document, a
 * partial serialization. `asList` turns every one of those into `[]`, and `[]`
 * renders as a fact about the member.
 *
 * These tests pin the distinction at the seam where it is decided. They are
 * written to fail if the guard is removed: each one asserts the state word, not
 * merely that the list is empty, because an implementation that returned
 * `READY` with no rows would satisfy a laxer assertion perfectly.
 *
 * One deliberate non-guard is pinned here too, so that a later reader does not
 * "fix" it: a genuinely empty list from a well-formed payload stays `READY`.
 * The point is not to distrust the server. It is to stop speaking for it.
 *
 * This file once covered documents, briefings, shield and the concierge desk as
 * well. Those features left the Private Office surface and their clients left
 * with them; the guard itself is unchanged and is still the same `sentList`
 * they shared, so what is pinned here pins it for anything that comes back.
 */

const mockPulseApi = jest.fn();

// The real `PulseApiError` is kept: `refusal` narrows with `instanceof`, and a
// stubbed class would decide these cases for reasons unrelated to the code
// under test.
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

import { getPrivatePeople, getPrivatePersonProfile } from "../privateFeatures";
import { __resetOfficeLockForTests } from "../../privateOffice/officeLock";

beforeEach(() => {
  jest.clearAllMocks();
  __resetOfficeLockForTests();
});

/** Answer every call with one body. */
function serves(body: unknown) {
  mockPulseApi.mockResolvedValue(body);
}

describe("a 200 that did not carry its list is a refusal, not an empty office", () => {
  const malformed: [string, unknown][] = [
    ["the key is missing entirely", { ok: true }],
    ["the key is null", { people: null }],
    ["the key is an object, not a list", { people: {} }],
    ["the key is a string", { people: "" }],
    ["the body is not an object at all", "<html>maintenance</html>"]
  ];

  it.each(malformed)("people: %s", async (_label, body) => {
    serves(body);
    const result = await getPrivatePeople();
    expect(result.state).toBe("ERROR");
    // The assertion that matters: the caller is never handed a list it could
    // render as "you have nothing".
    expect(result).not.toHaveProperty("people");
  });
});

describe("a phantom record is a refusal too", () => {
  it("a person profile refuses rather than opening on node 0", async () => {
    serves({ ok: true, person: { name: "" } });
    expect((await getPrivatePersonProfile(12)).state).toBe("ERROR");
  });
});

describe("what is deliberately not guarded", () => {
  it("a real empty office is still READY — the point is not to distrust the server", async () => {
    serves({ ok: true, people: [] });
    const result = await getPrivatePeople();
    expect(result.state).toBe("READY");
    if (result.state !== "READY") throw new Error("unreachable");
    expect(result.people).toEqual([]);
  });
});
