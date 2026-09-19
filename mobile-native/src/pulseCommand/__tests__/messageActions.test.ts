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
 *     which is the property `toContain` would have missed.
 */

import { messageActionKind, messageActionRules } from "../domain";
import type { MessengerMessage } from "../../api/messenger";

function message(overrides: Partial<MessengerMessage> = {}): MessengerMessage {
  return {
    id: 4100,
    message_id: 4100,
    conversation_id: 6,
    body: "dinner at eight?",
    message_type: "text",
    is_mine: false,
    delivery_status: "sent",
    created_at: "2026-09-19T10:00:00Z",
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

  it("lets a moderator unsend a message they did not write", () => {
    expect(menu(message(), { viewerModerates: true })).toContain("deleteEveryone");
  });

  it("never offers Translate on your own message", () => {
    expect(menu(message({ is_mine: true }))).not.toContain("translate");
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
