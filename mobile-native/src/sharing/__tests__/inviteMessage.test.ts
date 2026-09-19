import catalog from "../../i18n/catalogs/en/extended.json";
import {
  DEFAULT_INVITE_TONE,
  INVITE_TONES,
  buildInviteMessage,
  buildInviteSharePayload,
  inviteHandle,
  inviteSubject
} from "../inviteMessage";
import type { InviteTone } from "../inviteMessage";

const LINK = "https://pulsesoc.com/r/AB12CD";

/**
 * Resolves against the real shipped English catalog rather than a stub.
 *
 * A stub would let this file pass while the actual strings were missing,
 * misnamed or empty — which is most of what can realistically go wrong here,
 * since the code under test is a small amount of assembly around seven catalog
 * lookups. Reading the catalog makes a renamed key a test failure.
 */
function t(key: string, options?: Record<string, unknown>): string {
  const [namespace, path] = key.split(":");
  const value = path
    .split(".")
    .reduce<unknown>(
      (node, part) => (node as Record<string, unknown>)?.[part],
      (catalog as Record<string, unknown>)[namespace]
    );
  if (typeof value !== "string") throw new Error(`missing catalog key: ${key}`);
  return value.replace(/\{\{(\w+)\}\}/g, (_, name: string) =>
    String(options?.[name] ?? `{{${name}}}`)
  );
}

describe("invite message templates", () => {
  it("wraps the link in the approved default sentence", () => {
    expect(buildInviteMessage({ link: LINK, t })).toBe(
      "Hey! I'm using PulseSoc. Let's connect there and stay in touch.\n\n" +
        `Join me on PulseSoc:\n${LINK}`
    );
  });

  it("puts the link alone on its final line so clients linkify it cleanly", () => {
    const lines = buildInviteMessage({ link: LINK, t }).split("\n");
    expect(lines[lines.length - 1]).toBe(LINK);
  });

  it("passes the server's link through untouched — no second link system", () => {
    // The whole point of reusing /r/ is that nothing here rewrites it. A token
    // scheme or a /pulse/ rewrite would break deferred App Store attribution.
    const message = buildInviteMessage({ link: LINK, t, username: "cherie" });
    expect(message).toContain(LINK);
    expect(message.match(/https?:\/\/\S+/g)).toEqual([LINK]);
  });

  it("offers every approved tone, each a distinct non-empty message", () => {
    const messages = INVITE_TONES.map((tone) => buildInviteMessage({ link: LINK, t, tone }));
    messages.forEach((message) => {
      expect(message.length).toBeGreaterThan(LINK.length);
      expect(message.endsWith(LINK)).toBe(true);
    });
    expect(new Set(messages).size).toBe(INVITE_TONES.length);
  });

  it("is deterministic — the same inputs always produce the same message", () => {
    const once = buildInviteMessage({ link: LINK, t, tone: "casual", username: "cherie" });
    for (let i = 0; i < 25; i += 1) {
      expect(buildInviteMessage({ link: LINK, t, tone: "casual", username: "cherie" })).toBe(once);
    }
  });

  it("falls back to the default tone for an unrecognised one", () => {
    expect(buildInviteMessage({ link: LINK, t, tone: "shouty" as InviteTone })).toBe(
      buildInviteMessage({ link: LINK, t, tone: DEFAULT_INVITE_TONE })
    );
  });

  it("returns nothing when there is no link yet", () => {
    expect(buildInviteMessage({ link: "", t })).toBe("");
    expect(buildInviteMessage({ link: "   ", t })).toBe("");
    expect(buildInviteSharePayload({ link: "", t })).toBeNull();
  });
});

describe("invite personalisation", () => {
  it("appends the public handle as its own trailing line", () => {
    expect(buildInviteMessage({ link: LINK, t, username: "cherie" })).toBe(
      `${buildInviteMessage({ link: LINK, t })}\n\nI'm @cherie there.`
    );
  });

  it("normalises a leading @ rather than doubling it", () => {
    expect(buildInviteMessage({ link: LINK, t, username: "@cherie" })).toContain("@cherie there.");
    expect(buildInviteMessage({ link: LINK, t, username: "@cherie" })).not.toContain("@@");
  });

  it("drops the whole line when there is no usable handle, never leaving a hole", () => {
    const plain = buildInviteMessage({ link: LINK, t });
    [undefined, null, "", "   ", "a"].forEach((username) => {
      expect(buildInviteMessage({ link: LINK, t, username })).toBe(plain);
    });
  });

  it("refuses anything that is not a plain public handle", () => {
    // Deny-by-default: this value is about to be sent to another person, so
    // the bar is "definitely a handle", not "probably not harmful".
    [
      "Cherie Roody", // a display name
      "cherie@example.com", // an email address
      "cherie roody",
      "<script>alert(1)</script>",
      "https://example.com/cherie",
      "x".repeat(31)
    ].forEach((username) => {
      expect(inviteHandle(username)).toBe("");
      expect(buildInviteMessage({ link: LINK, t, username })).toBe(
        buildInviteMessage({ link: LINK, t })
      );
    });
  });

  it("accepts the handle shapes PulseSoc actually issues", () => {
    ["cherie", "cherie_roody", "cherie.roody", "cherie-1", "AB12"].forEach((username) => {
      expect(inviteHandle(username)).toBe(username);
    });
  });

  it("leaks nothing but the handle and the link", () => {
    const message = buildInviteMessage({ link: LINK, t, username: "cherie" });
    ["cherieroody@gmail.com", "Cherie Roody", "user_id", "4211"].forEach((secret) => {
      expect(message).not.toContain(secret);
    });
  });
});

describe("invite share payload", () => {
  it("carries the message and a subject for targets that have one", () => {
    expect(buildInviteSharePayload({ link: LINK, t, username: "cherie" })).toEqual({
      message: buildInviteMessage({ link: LINK, t, username: "cherie" }),
      subject: "Join me on PulseSoc"
    });
    expect(inviteSubject(t)).toBe("Join me on PulseSoc");
  });

  it("never sets a standalone url field", () => {
    // On iOS, `message` + `url` become two activity items and a target may take
    // one and drop the other — which is precisely how the explaining sentence
    // goes missing. The link rides inside the message instead.
    const payload = buildInviteSharePayload({ link: LINK, t });
    expect(payload).not.toBeNull();
    expect(Object.keys(payload as object).sort()).toEqual(["message", "subject"]);
  });
});
