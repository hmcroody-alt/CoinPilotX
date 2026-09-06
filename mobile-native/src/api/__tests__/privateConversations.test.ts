/**
 * Private Conversations client — the four claims this module is not allowed to
 * get wrong.
 *
 * 1. **It never upgrades a security answer.** Stage 53 forbids the product from
 *    saying "end-to-end encrypted" unless real cryptographic E2EE exists. The
 *    enforcement point is `asStrictTrue`: an absent, null, `0`, `"false"` or
 *    even `"true"` field parses to `false`. Only a literal boolean `true` is
 *    `true`. A permissive parser here would let a screen print the encrypted
 *    copy against a server that never said so, and the member would change what
 *    they are willing to type.
 *
 * 2. **A refusal is not an empty list.** Every call returns a tagged state.
 *    None of them return `[]` on failure, because a caller that cannot tell
 *    "we could not look" from "there is nothing here" will render the second.
 *
 * 3. **It reports its own count.** `listPrivateConversations` returns the
 *    length of what it parsed, not the server's `count`. A count that
 *    disagrees with the list is how a UI claims "3 threads" above an empty one.
 *
 * 4. **There is one conversation shape.** Rows go through the canonical
 *    `normalizeConversations`, so an Office row is the same object Messenger
 *    renders — not a parallel type that drifts.
 *
 * A note on what is *not* stubbed: `PulseApiError` and the real refusal
 * translator are kept, because the narrowing under test is theirs. Stubbing
 * either would leave a suite that proves the stub agrees with itself.
 */

const mockPulseApi = jest.fn();

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
  PRIVATE_CONVERSATIONS_PATH,
  PRIVATE_CONVERSATION_SCOPES,
  PRIVATE_CONVERSATION_SENSITIVITIES,
  createPrivateConversation,
  getPrivateConversation,
  getPrivateConversationCapabilities,
  getPrivateConversationMessages,
  linkPrivateConversation,
  listPrivateConversations,
  markPrivateConversationRead,
  parsePrivateConversationCapabilities,
  parsePrivateConversationClassification,
  sendPrivateConversationMessage,
  setPrivateConversationSensitivity,
  unlinkPrivateConversation
} from "../privateConversations";

/**
 * Build the error the transport would actually throw for a given status and
 * JSON body.
 *
 * `PulseApiError` is `(message, status, code, details)` — the body is the
 * *fourth* argument. A three-argument call puts the body in `code` and leaves
 * `details` undefined, so every refusal falls through to the generic branch and
 * the test passes or fails for a reason that has nothing to do with the code
 * under test. Mirroring `pulseApiRequest`'s construction here removes that
 * footgun and ties the suite to the real mapping: if the transport changes how
 * it packs an error, these tests break instead of quietly drifting.
 */
function wireError(status: number, body: Record<string, unknown>): PulseApiError {
  return new PulseApiError(
    String(body.message || body.error || "PulseSoc request failed."),
    status,
    typeof body.error_code === "string"
      ? body.error_code
      : typeof body.error === "string"
        ? body.error
        : undefined,
    body
  );
}

/** A conversation row exactly as the routes module emits it. */
function rawRow(overrides: Record<string, unknown> = {}) {
  return {
    id: 77,
    conversation_id: 77,
    conversation_type: "group",
    title: "Acquisition working group",
    unread_count: 2,
    private_office: {
      office_scope: "PROJECT_ROOM",
      sensitivity: "CONFIDENTIAL",
      organization_node_id: 0,
      operations_project_id: 4,
      meeting_id: 0,
      archived: false,
      created_at: "2026-08-01T10:00:00Z",
      is_private_office: true,
      end_to_end_encrypted: false
    },
    links: [{ link_type: "DOCUMENT", target_id: 91, label: "Term sheet" }],
    ...overrides
  };
}

function rawCapabilities(overrides: Record<string, unknown> = {}) {
  return {
    feature_id: "private_office.conversations",
    enabled: true,
    message_ledger: "comm_v2_messages",
    attachment_authority: "message_attachments",
    rtc_provider: "agora",
    scopes: PRIVATE_CONVERSATION_SCOPES,
    link_types: ["DOCUMENT", "RECORD", "FACT", "MEETING", "ORGANIZATION_NODE", "PROJECT"],
    end_to_end_encrypted: false,
    encryption_note: "Protected in transit and at rest. Not end-to-end encrypted.",
    disappearing_messages: false,
    ...overrides
  };
}

beforeEach(() => {
  jest.clearAllMocks();
});

describe("Stage 53 — the encryption answer is never upgraded", () => {
  /**
   * The mutation this is built to kill: `asStrictTrue` relaxed to `Boolean(v)`
   * or to `v !== false`. Both survive a test that only checks the honest
   * `false` case, because `false` maps to `false` under every variant. So the
   * cases that discriminate are the *absent* and *truthy-but-not-true* ones.
   */
  // Columns are (label, expected, patch) so the printf title reads
  // "reads <label> as <expected>". Putting the patch object second would make
  // every title report the input twice and never say what the answer was.
  it.each([
    ["absent", false, {}],
    ["null", false, { end_to_end_encrypted: null }],
    ["the string \"true\"", false, { end_to_end_encrypted: "true" }],
    ["the number 1", false, { end_to_end_encrypted: 1 }],
    ["the string \"false\"", false, { end_to_end_encrypted: "false" }],
    ["boolean false", false, { end_to_end_encrypted: false }],
    ["boolean true", true, { end_to_end_encrypted: true }]
  ])("reads %s as %s on capabilities", (_label, expected, patch) => {
    const parsed = parsePrivateConversationCapabilities({ ...rawCapabilities(), ...patch });
    expect(parsed.endToEndEncrypted).toBe(expected);
  });

  it.each([
    ["absent", false, {}],
    ["the string \"true\"", false, { end_to_end_encrypted: "true" }],
    ["boolean true", true, { end_to_end_encrypted: true }]
  ])("reads %s as %s on a conversation's classification", (_label, expected, patch) => {
    const parsed = parsePrivateConversationClassification({
      ...rawRow().private_office,
      ...patch
    });
    expect(parsed.endToEndEncrypted).toBe(expected);
  });

  it("does not infer encryption from a note that talks about encryption", () => {
    // A server that describes its transport must not thereby claim E2EE. The
    // note is prose; the flag is the answer.
    const parsed = parsePrivateConversationCapabilities(
      rawCapabilities({
        encryption_note: "Messages are encrypted in transit with TLS 1.3.",
        end_to_end_encrypted: false
      })
    );
    expect(parsed.encryptionNote).toContain("encrypted");
    expect(parsed.endToEndEncrypted).toBe(false);
  });

  it("applies the same strictness to every other security-relevant flag", () => {
    const parsed = parsePrivateConversationCapabilities(
      rawCapabilities({ disappearing_messages: "yes", enabled: 1 })
    );
    expect(parsed.disappearingMessages).toBe(false);
    expect(parsed.enabled).toBe(false);

    const classification = parsePrivateConversationClassification({
      is_private_office: "yes",
      archived: 1
    });
    expect(classification.isPrivateOffice).toBe(false);
    expect(classification.archived).toBe(false);
  });

  it("carries the server's real capability answer through the list call", async () => {
    mockPulseApi.mockResolvedValue({
      ok: true,
      conversations: [rawRow()],
      count: 1,
      capabilities: rawCapabilities()
    });
    const result = await listPrivateConversations();
    expect(result.state).toBe("READY");
    if (result.state !== "READY") return;
    expect(result.capabilities.endToEndEncrypted).toBe(false);
    expect(result.capabilities.rtcProvider).toBe("agora");
    // The "no second RTC provider" constraint, made checkable from the client.
    expect(result.capabilities.rtcProvider).not.toBe("livekit");
    expect(result.capabilities.messageLedger).toBe("comm_v2_messages");
  });
});

describe("refusals stay refusals", () => {
  it.each([
    [402, "NOT_ENTITLED", { state: "NOT_ENTITLED", minimum_tier: "elite" }],
    [403, "FEATURE_DISABLED", { state: "FEATURE_DISABLED" }],
    [404, "NOT_IMPLEMENTED", { state: "NOT_IMPLEMENTED" }],
    [423, "LOCKED", { state: "LOCKED", setup_required: false }],
    [503, "UNAVAILABLE", { state: "UNAVAILABLE" }]
  ])("turns HTTP %s into %s rather than an empty list", async (status, expected, details) => {
    mockPulseApi.mockRejectedValue(wireError(status, { ok: false, ...details }));
    const result = await listPrivateConversations();
    expect(result.state).toBe(expected);
    // The load-bearing half: no branch of this function returns rows.
    expect((result as { conversations?: unknown[] }).conversations).toBeUndefined();
  });

  it("keeps a locked office locked even when the body says otherwise", async () => {
    // 423 wins over any state word in the payload. A body claiming READY on a
    // 423 is either confused or hostile; either way the lock is the answer.
    mockPulseApi.mockRejectedValue(
      wireError(423, { ok: false, state: "READY", setup_required: true })
    );
    const result = await listPrivateConversations();
    expect(result.state).toBe("LOCKED");
  });

  it("distinguishes a thread that is not an Office thread from a gate refusal", async () => {
    mockPulseApi.mockRejectedValue(wireError(404, { ok: false, code: "not_found" }));
    await expect(getPrivateConversation(77)).resolves.toEqual({ state: "NOT_FOUND" });

    // Same status code, different meaning: the feature itself is not built.
    mockPulseApi.mockRejectedValue(wireError(404, { ok: false, state: "NOT_IMPLEMENTED" }));
    const gated = await getPrivateConversation(77);
    expect(gated.state).toBe("NOT_IMPLEMENTED");
  });

  it("surfaces a named product refusal instead of a generic failure", async () => {
    mockPulseApi.mockRejectedValue(
      wireError(400, {
        ok: false,
        code: "invalid_sensitivity",
        message: "Choose a supported sensitivity."
      })
    );
    const result = await setPrivateConversationSensitivity(77, "NONSENSE");
    expect(result).toEqual({
      state: "REJECTED",
      code: "invalid_sensitivity",
      message: "Choose a supported sensitivity."
    });
  });

  /**
   * `getPrivateConversationCapabilities` has no caller yet — the list returns
   * capabilities inline, so no screen needs the standalone read. It is kept
   * because it binds a real server route that a capabilities-only surface will
   * want, and it is tested because the mutation battery found it was the one
   * exported function whose refusal path nothing exercised: collapsing its
   * catch into an empty `READY` survived the whole suite.
   *
   * An untested refusal path on a *security* read is the worst place to have
   * one. A screen asking "is this encrypted?" and getting a fabricated
   * all-false capabilities object back would render the reassuring copy for a
   * server it never successfully reached.
   */
  it("keeps a refusal on the standalone capabilities read", async () => {
    mockPulseApi.mockRejectedValue(wireError(503, { ok: false, state: "UNAVAILABLE" }));
    const result = await getPrivateConversationCapabilities();
    expect(result.state).toBe("UNAVAILABLE");
    // The failure mode this guards: a synthesized capabilities object whose
    // `endToEndEncrypted: false` reads as an answer rather than as a silence.
    expect((result as { capabilities?: unknown }).capabilities).toBeUndefined();
  });

  it("parses the standalone capabilities read as strictly as the list does", async () => {
    mockPulseApi.mockResolvedValue({
      ok: true,
      capabilities: rawCapabilities({ end_to_end_encrypted: "true" })
    });
    const result = await getPrivateConversationCapabilities();
    expect(result.state).toBe("READY");
    if (result.state !== "READY") return;
    expect(result.capabilities.endToEndEncrypted).toBe(false);
    expect(result.capabilities.rtcProvider).toBe("agora");
  });

  it("never lets a failed signal throw into the caller", async () => {
    mockPulseApi.mockRejectedValue(wireError(500, { ok: false, message: "boom" }));
    await expect(markPrivateConversationRead(77)).resolves.toBe(false);
  });
});

describe("the list reports what it actually parsed", () => {
  it("ignores a server count that disagrees with the rows", async () => {
    mockPulseApi.mockResolvedValue({
      ok: true,
      conversations: [rawRow()],
      count: 3,
      capabilities: rawCapabilities()
    });
    const result = await listPrivateConversations();
    if (result.state !== "READY") throw new Error(`expected READY, got ${result.state}`);
    expect(result.conversations).toHaveLength(1);
    expect(result.count).toBe(1);
  });

  it("reads an empty list as empty, not as a failure", async () => {
    mockPulseApi.mockResolvedValue({
      ok: true,
      conversations: [],
      count: 0,
      capabilities: rawCapabilities()
    });
    const result = await listPrivateConversations();
    expect(result.state).toBe("READY");
    if (result.state !== "READY") return;
    expect(result.conversations).toEqual([]);
  });
});

describe("one conversation shape, one ledger", () => {
  it("normalizes an Office row through the canonical conversation normalizer", async () => {
    mockPulseApi.mockResolvedValue({
      ok: true,
      conversations: [rawRow()],
      count: 1,
      capabilities: rawCapabilities()
    });
    const result = await listPrivateConversations();
    if (result.state !== "READY") throw new Error(`expected READY, got ${result.state}`);
    const [summary] = result.conversations;

    // `conversation_domain` is filled by the canonical normalizer and by
    // nothing else. Its presence is the evidence that this row took the shared
    // path rather than a private one.
    expect(summary.conversation.conversation_domain).toBeTruthy();
    expect(summary.conversation.conversation_id).toBe(77);
    expect(summary.conversation.title).toBe("Acquisition working group");
    // Unread is the canonical field, not an Office-side recount.
    expect(summary.conversation.unread_count).toBe(2);

    expect(summary.classification.officeScope).toBe("PROJECT_ROOM");
    expect(summary.classification.operationsProjectId).toBe(4);
    expect(summary.links).toEqual([
      expect.objectContaining({ linkType: "DOCUMENT", targetId: "91", label: "Term sheet" })
    ]);
  });

  it("refuses to invent a scope or a link type the server did not send", () => {
    const classification = parsePrivateConversationClassification({
      office_scope: "COMMUNITY_CHANNEL"
    });
    // Not silently coerced to DIRECT: an unrecognized scope is no scope, and
    // the screen renders "unknown" rather than a wrong classification.
    expect(classification.officeScope).toBe("");
  });

  it("sends messages through the canonical ledger's idempotency key", async () => {
    mockPulseApi.mockResolvedValue({
      ok: true,
      conversation_id: 77,
      message: { id: 500, conversation_id: 77, body: "hello" }
    });
    const result = await sendPrivateConversationMessage(77, {
      body: "hello",
      clientMessageId: "abc-123"
    });
    expect(result.state).toBe("SENT");
    const [, init] = mockPulseApi.mock.calls[0];
    expect(JSON.parse((init as { body: string }).body)).toEqual({
      body: "hello",
      client_message_id: "abc-123"
    });
  });

  it("reads a thread through the canonical message normalizer", async () => {
    mockPulseApi.mockResolvedValue({
      ok: true,
      conversation_id: 77,
      messages: [{ id: 501, body: "one" }, { id: 502, body: "two" }]
    });
    const result = await getPrivateConversationMessages(77);
    if (result.state !== "READY") throw new Error(`expected READY, got ${result.state}`);
    expect(result.messages).toHaveLength(2);
    // The normalizer backfills the conversation id the envelope carried; a
    // parallel parser would have left these undefined.
    expect(result.messages.every((m) => m.conversation_id === 77)).toBe(true);
  });
});

describe("the routes it actually calls", () => {
  it("mounts every call under the one documented route family", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, conversations: [], capabilities: {} });
    await listPrivateConversations();
    await listPrivateConversations("GROUP");

    mockPulseApi.mockResolvedValue({ ok: true, conversation: rawRow(), links: [] });
    await getPrivateConversation(77);

    mockPulseApi.mockResolvedValue({ ok: true, conversation_id: 78, conversation: rawRow() });
    await createPrivateConversation({ officeScope: "DIRECT" });

    mockPulseApi.mockResolvedValue({ ok: true, links: [] });
    await linkPrivateConversation(77, "FACT", 12);
    await unlinkPrivateConversation(77, "FACT", 12);

    const paths = mockPulseApi.mock.calls.map(([path]) => String(path));
    expect(paths.every((path) => path.startsWith(PRIVATE_CONVERSATIONS_PATH))).toBe(true);
    expect(paths).toContain(`${PRIVATE_CONVERSATIONS_PATH}?scope=GROUP`);
    // No second alias. One route family, as the foundation map settled.
    expect(paths.some((path) => path.includes("/api/pulse/"))).toBe(false);
  });

  it("uses DELETE to unlink rather than a second endpoint", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, links: [] });
    await unlinkPrivateConversation(77, "DOCUMENT", 91);
    const [path, init] = mockPulseApi.mock.calls[0];
    expect(path).toBe(`${PRIVATE_CONVERSATIONS_PATH}/77/links`);
    expect((init as { method: string }).method).toBe("DELETE");
  });

  it("keeps the sensitivity vocabulary aligned with the server model", () => {
    // Mirrors `services/private_office/model.py::SENSITIVITIES`. If the server
    // ladder changes, this is the line that should fail first.
    expect(PRIVATE_CONVERSATION_SENSITIVITIES).toEqual([
      "PUBLIC",
      "INTERNAL",
      "CONFIDENTIAL",
      "HIGHLY_SENSITIVE",
      "RESTRICTED"
    ]);
  });
});
