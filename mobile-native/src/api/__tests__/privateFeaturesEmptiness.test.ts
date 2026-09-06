/**
 * A refusal and an empty result must never render the same.
 *
 * Every Private Office feature client answers a tagged result, and the screens
 * render `READY` with a zero-length list as a settled statement about the
 * member's own belongings: "No documents yet." "No open findings." Those
 * sentences are only true if the server actually said so.
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
 * Two deliberate non-guards are pinned here too, so that a later reader does
 * not "fix" them:
 *
 *   - the concierge `desk` block fails closed to UNSTAFFED, because implying a
 *     human who is not on the roster is the one error this feature exists to
 *     never make;
 *   - a genuinely empty list from a well-formed payload stays `READY`. The
 *     point is not to distrust the server. It is to stop speaking for it.
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

import {
  getConciergeHome,
  getConciergeRequest,
  getPrivateBriefing,
  getPrivateBriefings,
  getPrivateDocument,
  getPrivateDocuments,
  getPrivatePeople,
  getPrivatePersonProfile,
  getShieldHome
} from "../privateFeatures";
import { __resetOfficeLockForTests } from "../../privateOffice/officeLock";

beforeEach(() => {
  jest.clearAllMocks();
  __resetOfficeLockForTests();
});

/** Answer every call with one body. */
function serves(body: unknown) {
  mockPulseApi.mockResolvedValue(body);
}

/** Answer calls in order — the shield home issues two. */
function servesInOrder(...bodies: unknown[]) {
  bodies.forEach((body) => mockPulseApi.mockResolvedValueOnce(body));
}

describe("a 200 that did not carry its list is a refusal, not an empty vault", () => {
  const malformed: [string, unknown][] = [
    ["the key is missing entirely", {}],
    ["the key is null", { documents: null }],
    ["the key is an object, not a list", { documents: {} }],
    ["the key is a string", { documents: "" }],
    ["the body is not an object at all", "<html>maintenance</html>"]
  ];

  it.each(malformed)("documents: %s", async (_label, body) => {
    serves(body);
    const result = await getPrivateDocuments();
    expect(result.state).toBe("ERROR");
    // The assertion that matters: the caller is never handed a list it could
    // render as "you have nothing".
    expect(result).not.toHaveProperty("documents");
  });

  it("people: a payload with no people list refuses", async () => {
    serves({ ok: true });
    expect((await getPrivatePeople()).state).toBe("ERROR");
  });

  it("briefings: a payload with no briefings list refuses", async () => {
    serves({ ok: true });
    expect((await getPrivateBriefings()).state).toBe("ERROR");
  });

  it("concierge: a payload with no requests list refuses", async () => {
    serves({ ok: true, desk: { staffed: false, operator_count: 0, note: "" } });
    expect((await getConciergeHome()).state).toBe("ERROR");
  });
});

describe("the shield never assembles a clean bill of health from a bad response", () => {
  const posture = {
    posture: {
      open_findings: 0,
      by_severity: {},
      checks: ["breached_credentials", "exposed_contact"],
      external: {
        dark_web: { monitored: false, state: "NOT_MONITORED", note: "No provider integrated." }
      }
    }
  };

  it("refuses when the findings list is absent", async () => {
    servesInOrder(posture, { ok: true });
    expect((await getShieldHome()).state).toBe("ERROR");
  });

  it("refuses when the posture block is absent", async () => {
    servesInOrder({ ok: true }, { findings: [] });
    const result = await getShieldHome();
    expect(result.state).toBe("ERROR");
    // Spelled out because this is the specific harm: a missing posture parses
    // to zero findings and an empty `external` list, which is the block that
    // says what nobody has checked. Rendered, that reads as "you are clear"
    // — assembled entirely from a response we failed to read.
    expect(result).not.toHaveProperty("posture");
  });

  it("still reports a real all-clear when the server actually sent one", async () => {
    servesInOrder(posture, { findings: [] });
    const result = await getShieldHome();
    expect(result.state).toBe("READY");
    if (result.state !== "READY") throw new Error("unreachable");
    expect(result.findings).toEqual([]);
    // The external caveat survives the parse — absence of findings is not
    // external safety, and the member is told so.
    expect(result.posture.external).toHaveLength(1);
    expect(result.posture.external[0].monitored).toBe(false);
    expect(result.posture.checks).toHaveLength(2);
  });
});

describe("a phantom record is a refusal too", () => {
  it("document detail refuses a payload with no document block", async () => {
    serves({ ok: true, claims: [] });
    expect((await getPrivateDocument(7)).state).toBe("ERROR");
  });

  it("document detail refuses a payload with no claims list", async () => {
    serves({ ok: true, document: { id: 7, title: "Deed" } });
    expect((await getPrivateDocument(7)).state).toBe("ERROR");
  });

  it("a person profile refuses rather than opening on node 0", async () => {
    serves({ ok: true, person: { name: "" } });
    expect((await getPrivatePersonProfile(12)).state).toBe("ERROR");
  });

  it("a briefing refuses rather than rendering an untitled empty one", async () => {
    serves({ ok: true, briefing: {} });
    expect((await getPrivateBriefing(3)).state).toBe("ERROR");
  });

  it("a concierge thread refuses rather than opening on request 0", async () => {
    serves({ ok: true, thread: [], desk: {} });
    expect((await getConciergeRequest(9)).state).toBe("ERROR");
  });
});

describe("what is deliberately not guarded", () => {
  it("a real empty vault is still READY — the point is not to distrust the server", async () => {
    serves({ ok: true, documents: [] });
    const result = await getPrivateDocuments();
    expect(result.state).toBe("READY");
    if (result.state !== "READY") throw new Error("unreachable");
    expect(result.documents).toEqual([]);
  });

  it("an empty concierge thread on a real request is ordinary, not a refusal", async () => {
    serves({ ok: true, request: { id: 9, title: "Book the notary" }, thread: [], desk: {} });
    const result = await getConciergeRequest(9);
    expect(result.state).toBe("READY");
    if (result.state !== "READY") throw new Error("unreachable");
    expect(result.thread).toEqual([]);
  });

  it("a missing desk block fails closed to UNSTAFFED instead of refusing", async () => {
    // The asymmetry is the point. Everywhere else a missing block is a
    // refusal; here the parsed default is itself the safe claim, and refusing
    // would hide the member's real requests over a block whose absence cannot
    // mislead them.
    serves({ ok: true, requests: [] });
    const result = await getConciergeHome();
    expect(result.state).toBe("READY");
    if (result.state !== "READY") throw new Error("unreachable");
    expect(result.desk.staffed).toBe(false);
    expect(result.desk.operatorCount).toBe(0);
  });
});
