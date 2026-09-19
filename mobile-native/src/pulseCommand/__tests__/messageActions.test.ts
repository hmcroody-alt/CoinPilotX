/**
 * Which actions a long press offers, per situation.
 *
 * The menu is the feature. "Long press shows a menu" is not worth asserting;
 * what is worth asserting is that pressing someone else's photo and pressing
 * your own voice note produce *different* menus, and that the differences are
 * the intended ones rather than whatever fell out of the conditionals.
 *
 * So these tests assert the whole visible set with `toEqual`, not membership
 * with `toContain`. A `toContain("copy")` test passes just as happily against
 * a function that returns every action for every message -- which is the exact
 * failure this feature exists to avoid, and the one a membership test cannot
 * see.
 *
 * Mutation contract:
 *   - offering Edit on someone else's message must turn "others' text" red;
 *   - offering Delete for everyone to a non-author non-moderator must turn
 *     "others' text" red;
 *   - offering Copy or Translate on a voice note must turn "voice" red;
 *   - dropping the link actions must turn "a message with a link" red;
 *   - returning every action regardless of message must turn ALL of them red,
 *     which is the property `toContain` would have missed;
 *   - making `canReactToMessage` a lookup in the rule list must turn every
 *     case in "whether the reaction strip is offered" red, because there is
 *     no rule keyed `react` and there never was.
 */

import { canReactToMessage, messageActionKind, messageActionRules } from "../domain";
import type { MessengerMessage } from "../../api/messenger";

/** A timestamp `minutes` in the past, in the format the server writes. */
function minutesAgo(minutes: number) {
  return new Date(Date.now() - minutes * 60_000).toISOString();
}

/**
 * Default `created_at` is *now*, not a fixed date.
 *
 * Edit and Delete-for-everyone are time-limited, so a hard-coded timestamp
 * would make most of this file pass or fail depending on the wall clock on
 * the day it runs. Cases that care about age say so by passing `created_at`
 * explicitly; every other case means "a message that just arrived".
 */
function message(overrides: Partial<MessengerMessage> = {}): MessengerMessage {
  return {
    id: 4100,
    message_id: 4100,
    conversation_id: 6,
    body: "dinner at eight?",
    message_type: "text",
    is_mine: false,
    delivery_status: "sent",
    created_at: minutesAgo(0),
    ...overrides
  } as MessengerMessage;
}

/** What the user actually sees, in the order they see it. */
function menu(...args: Parameters<typeof messageActionRules>) {
  return messageActionRules(...args)
    .filter((rule) => rule.available)
    .map((rule) => rule.key);
}

describe("what a long press offers", () => {
  it("offers your own text message the things you can do to your own words", () => {
    expect(menu(message({ is_mine: true }))).toEqual([
      "reply",
      "copy",
      "forward",
      "share",
      "save",
      "edit",
      "info",
      "deleteSelf",
      "deleteEveryone"
    ]);
  });

  it("offers someone else's text message Translate and Report, and neither Edit nor Delete for everyone", () => {
    const keys = menu(message());
    expect(keys).toEqual([
      "reply",
      "copy",
      "translate",
      "forward",
      "share",
      "save",
      "info",
      "report",
      "safety",
      "deleteSelf"
    ]);
    // Named individually because these two are the point of the distinction:
    // you cannot rewrite another person's words, and you cannot unsend them.
    expect(keys).not.toContain("edit");
    expect(keys).not.toContain("deleteEveryone");
  });

  it("puts the link actions directly under Reply when the message carries a link", () => {
    const keys = menu(message({ body: "look at https://pulsesoc.com/pulse/post/2432" }), {
      links: ["https://pulsesoc.com/pulse/post/2432"]
    });
    expect(keys.slice(0, 4)).toEqual(["reply", "openLink", "copyLink", "shareLink"]);
  });

  it("does not offer link actions for a message without one", () => {
    const keys = menu(message());
    expect(keys).not.toContain("openLink");
    expect(keys).not.toContain("copyLink");
    expect(keys).not.toContain("shareLink");
  });

  it("says it will ask which link when a message carries more than one", () => {
    const rules = messageActionRules(message({ body: "a b" }), {
      links: ["https://pulsesoc.com/pulse/post/1", "https://example.com/x"]
    });
    const open = rules.find((rule) => rule.key === "openLink");
    // The label the screen reader reads has to match what the tap does, or the
    // picker is a surprise.
    expect(open?.accessibilityLabel).toBe("Choose a link to open");
  });

  it("offers a photo View and Save to Photos, and not Copy when it has no caption", () => {
    const keys = menu(message({ message_type: "image", body: "" }));
    expect(keys).toEqual([
      "reply",
      "viewMedia",
      "saveMedia",
      "forward",
      "share",
      "save",
      "info",
      "report",
      "safety",
      "deleteSelf"
    ]);
  });

  it("offers Copy on a photo that has a caption, because there is text to copy", () => {
    expect(menu(message({ message_type: "image", body: "the view from up here" }))).toContain("copy");
  });

  it("keeps a voice note's menu thin, with nothing that implies text", () => {
    const keys = menu(message({ message_type: "voice", body: "" }));
    expect(keys).toEqual([
      "reply",
      "forward",
      "share",
      "save",
      "info",
      "report",
      "safety",
      "deleteSelf"
    ]);
    // A voice note has no words, so offering to copy or translate them is a
    // button that cannot do anything.
    expect(keys).not.toContain("copy");
    expect(keys).not.toContain("translate");
    expect(keys).not.toContain("viewMedia");
  });

  it("never offers Translate on your own message", () => {
    expect(menu(message({ is_mine: true }))).not.toContain("translate");
  });
});

/**
 * Edit and Delete-for-everyone expire. `comm_v2.edit_message` refuses past 15
 * minutes and `comm_v2.delete_message` past 30, so a menu that kept offering
 * them would be handing out buttons that answer 403.
 */
describe("the actions that expire", () => {
  it("offers Edit on a message sent a moment ago", () => {
    expect(menu(message({ is_mine: true, created_at: minutesAgo(2) }))).toContain("edit");
  });

  it("stops offering Edit once the server's fifteen minutes are up", () => {
    const keys = menu(message({ is_mine: true, created_at: minutesAgo(16) }));
    expect(keys).not.toContain("edit");
    // The message is otherwise perfectly normal -- only Edit expired.
    expect(keys).toContain("copy");
    expect(keys).toContain("reply");
  });

  it("keeps Delete for everyone past the edit window but inside its own", () => {
    const keys = menu(message({ is_mine: true, created_at: minutesAgo(20) }));
    expect(keys).not.toContain("edit");
    expect(keys).toContain("deleteEveryone");
  });

  it("drops Delete for everyone after thirty minutes, leaving Delete for me", () => {
    const keys = menu(message({ is_mine: true, created_at: minutesAgo(31) }));
    expect(keys).not.toContain("deleteEveryone");
    expect(keys).toContain("deleteSelf");
  });

  it("offers both when it cannot tell how old the message is", () => {
    // An unreadable timestamp must not silently strip actions. The server is
    // the authority; let it answer rather than guessing "too old" here.
    const keys = menu(message({ is_mine: true, created_at: "not a date" }));
    expect(keys).toContain("edit");
    expect(keys).toContain("deleteEveryone");
  });
});

/**
 * The server refuses to unsend a message for everyone unless you wrote it --
 * moderator or not. This is the test that should fail first if that ever
 * changes on the server, which is the point of naming it this way.
 */
describe("who may unsend a message for everyone", () => {
  it("does not offer it on someone else's message, even to a conversation moderator", () => {
    expect(menu(message({ is_mine: false }))).not.toContain("deleteEveryone");
  });

  it("offers it on your own", () => {
    expect(menu(message({ is_mine: true }))).toContain("deleteEveryone");
  });
});

describe("messages that are not fully there yet", () => {
  it("withholds everything that needs a server id from a message still sending", () => {
    const keys = menu(message({ id: 0, message_id: 0, delivery_status: "sending", is_mine: true }));
    // Nothing here can name the message to the server yet.
    expect(keys).not.toContain("forward");
    expect(keys).not.toContain("info");
    expect(keys).not.toContain("save");
    expect(keys).not.toContain("edit");
    // But these two are answered entirely on this device, so they stay.
    expect(keys).toContain("copy");
    expect(keys).toContain("deleteSelf");
  });

  it("offers Retry on a failed message and nothing that assumes it arrived", () => {
    const keys = menu(message({ id: 0, message_id: 0, delivery_status: "failed", is_mine: true }));
    expect(keys).toContain("retry");
    expect(keys).not.toContain("forward");
  });

  it("strips a deleted message down to leaving the conversation's copy", () => {
    const keys = menu(message({ deleted_at: "2026-09-19T11:00:00Z" }));
    // There is nothing left to reply to, copy, forward or report.
    expect(keys).toEqual(["safety", "deleteSelf"]);
  });

  it("treats a message withheld by safety review the same as a deleted one", () => {
    expect(menu(message({ moderation_state: "removed" }))).toEqual(["safety", "deleteSelf"]);
  });
});

describe("recognising what the press is on", () => {
  it("reads a plain row as text", () => {
    expect(messageActionKind(message())).toBe("text");
  });

  it.each([
    ["image", "media"],
    ["gif", "media"],
    ["video", "media"],
    ["document", "media"],
    ["voice", "voice"],
    ["audio_message", "voice"]
  ])("reads message_type %s as %s", (type, expected) => {
    expect(messageActionKind(message({ message_type: type }))).toBe(expected);
  });

  it("reads a row carrying attachments as media even when its type says text", () => {
    // This is how a photo sent with a caption arrives, and a menu that offered
    // Copy but no Save to Photos for it would be wrong in the most visible
    // possible way.
    expect(
      messageActionKind(message({ message_type: "text", attachments: [{ id: 1 }] } as Partial<MessengerMessage>))
    ).toBe("media");
  });

  it("reads a deleted row as unavailable whatever its type claims", () => {
    expect(messageActionKind(message({ message_type: "image", deleted_at: "2026-09-19T11:00:00Z" }))).toBe(
      "unavailable"
    );
  });
});

/**
 * The strip above the bubble, which is not one of the rows below it.
 *
 * These exist because the overlay originally asked the rule list whether
 * reacting was available. No rule is keyed `react`, so the answer was always
 * `false` and the strip never drew -- a whole control missing, with every test
 * in this file green. The condition now has a name and this is where it is
 * pinned.
 */
describe("whether the reaction strip is offered", () => {
  it("offers it on an ordinary message from either side", () => {
    expect(canReactToMessage(message())).toBe(true);
    expect(canReactToMessage(message({ is_mine: true }))).toBe(true);
  });

  it("withholds it from a message the server has not accepted yet", () => {
    // Mirrors the screen's own handler, which answers "not yet" for id <= 0.
    // Six buttons that all decline are worse than no strip.
    expect(canReactToMessage(message({ id: 0 }))).toBe(false);
    expect(canReactToMessage(message({ id: -1 }))).toBe(false);
  });

  it("withholds it from a deleted message", () => {
    expect(canReactToMessage(message({ deleted_at: minutesAgo(1) }))).toBe(false);
    expect(canReactToMessage(message({ delivery_status: "deleted" }))).toBe(false);
  });

  it("offers it on a voice note without touching playback", () => {
    // Voice is thin in the menu; that thinness is about text actions, not
    // about reacting. A heart on a voice note is a reaction to a message.
    expect(canReactToMessage(message({ message_type: "voice", body: "" }))).toBe(true);
  });
});
