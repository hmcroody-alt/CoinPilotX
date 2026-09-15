/**
 * A refusal and an empty result must never render the same.
 *
 * Relationship Intelligence answers a tagged result, and the screens render
 * `READY` with a zero-length list as a settled statement about the member's own
 * belongings: "No one here yet." That sentence is only true if the server
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
 * This file used to cover five features. Four were withdrawn from the product
 * and their clients are gone, so their cases went with them rather than being
 * retargeted at whatever was nearest — including the one asymmetry the old
 * docstring warned a reader not to "fix", the concierge desk that failed closed
 * to UNSTAFFED. What survives here is the whole of what is still shipped, and
 * the deliberate non-guard below is the one that still has a subject.
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

import { PulseApiError } from "../pulseApi";
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

/** Answer every call by throwing — the server refusing, not the transport. */
function refuses(error: unknown) {
  mockPulseApi.mockRejectedValue(error);
}

describe("a 200 that did not carry its list is a refusal, not an empty directory", () => {
  const malformed: [string, unknown][] = [
    ["the key is missing entirely", {}],
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
    // render as "you have nobody".
    expect(result).not.toHaveProperty("people");
  });
});

describe("a phantom record is a refusal too", () => {
  it("a person profile refuses rather than opening on node 0", async () => {
    serves({ ok: true, person: { name: "" } });
    expect((await getPrivatePersonProfile(12)).state).toBe("ERROR");
  });

  it("a person profile refuses a body carrying no person block at all", async () => {
    serves({ ok: true, facts: [], commitments: [], timeline: [] });
    const result = await getPrivatePersonProfile(12);
    expect(result.state).toBe("ERROR");
    // Spelled out because this is the specific harm the guard prevents: without
    // it the surrounding lists parse fine and the header parses to a blank name
    // on node 0, so the screen opens on a person who does not exist and reports
    // that they have no commitments.
    expect(result).not.toHaveProperty("profile");
  });
});

describe("a refusal and an absence are different answers", () => {
  it("a 404 for a person who is not in the directory is NOT_FOUND, not ERROR", async () => {
    refuses(new PulseApiError("no such person", 404, undefined, {}));
    expect((await getPrivatePersonProfile(12)).state).toBe("NOT_FOUND");
  });

  it("a 404 that names a feature state is that state, not a missing person", async () => {
    // The route answering 404 because the whole feature is switched off is not
    // the member asking after somebody who was never added. Rendering the first
    // as the second tells them their own records are gone.
    refuses(new PulseApiError("off", 404, undefined, { state: "FEATURE_DISABLED" }));
    expect((await getPrivatePersonProfile(12)).state).toBe("FEATURE_DISABLED");
  });

  it("the second lock's refusal survives as LOCKED rather than collapsing to ERROR", async () => {
    refuses(new PulseApiError("locked", 423, undefined, { setup_required: false }));
    const result = await getPrivatePersonProfile(12);
    expect(result.state).toBe("LOCKED");
    if (result.state !== "LOCKED") throw new Error("unreachable");
    expect(result.setupRequired).toBe(false);
  });
});

describe("what is deliberately not guarded", () => {
  it("a real empty directory is still READY — the point is not to distrust the server", async () => {
    serves({ ok: true, people: [] });
    const result = await getPrivatePeople();
    expect(result.state).toBe("READY");
    if (result.state !== "READY") throw new Error("unreachable");
    expect(result.people).toEqual([]);
  });

  it("a real person with no facts yet is READY, not a refusal", async () => {
    // The asymmetry with the block above is deliberate. A missing `people` key
    // proves nothing about the member, because nothing in the payload
    // identified it as the directory document at all. Here `node_id` already
    // did that job: this *is* the profile for person 4, so an absent `facts`
    // key is the server saying there are none.
    serves({ ok: true, person: { node_id: 4, name: "Ada" } });
    const result = await getPrivatePersonProfile(4);
    expect(result.state).toBe("READY");
    if (result.state !== "READY") throw new Error("unreachable");
    expect(result.profile.nodeId).toBe(4);
    expect(result.profile.facts).toEqual([]);
    expect(result.profile.commitments).toEqual([]);
    expect(result.profile.timeline).toEqual([]);
  });
});
