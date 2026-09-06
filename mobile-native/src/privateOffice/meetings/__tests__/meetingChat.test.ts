import {
  maxMessageId,
  mergeMeetingMessages,
  MESSAGE_CAP,
  newReactionEvents,
  unreadTextCount
} from "../meetingChat";
import { MeetingMessage, MeetingMessageKind } from "../types";

function msg(
  id: number,
  sender = 1,
  kind: MeetingMessageKind = "text",
  body = `m${id}`
): MeetingMessage {
  return { id, meeting_id: 1, sender_user_id: sender, kind, body, created_at: "" };
}

describe("mergeMeetingMessages", () => {
  it("merges, dedupes by id, and sorts ascending", () => {
    const existing = [msg(1), msg(3)];
    const merged = mergeMeetingMessages(existing, [msg(2), msg(3), msg(4)]);
    expect(merged.map((m) => m.id)).toEqual([1, 2, 3, 4]);
  });

  it("returns the same instance when nothing changes (skip re-render)", () => {
    const existing = [msg(1), msg(2)];
    expect(mergeMeetingMessages(existing, [])).toBe(existing);
    expect(mergeMeetingMessages(existing, [msg(2)])).toBe(existing);
  });

  it("ignores malformed rows without an id", () => {
    const existing = [msg(1)];
    const bad = { id: 0 } as MeetingMessage;
    expect(mergeMeetingMessages(existing, [bad])).toBe(existing);
  });

  it("caps to the newest MESSAGE_CAP entries", () => {
    const flood: MeetingMessage[] = [];
    for (let i = 1; i <= MESSAGE_CAP + 25; i += 1) flood.push(msg(i));
    const merged = mergeMeetingMessages([], flood);
    expect(merged).toHaveLength(MESSAGE_CAP);
    expect(merged[0].id).toBe(26);
    expect(merged[merged.length - 1].id).toBe(MESSAGE_CAP + 25);
  });
});

describe("maxMessageId", () => {
  it("returns 0 for empty and the max id otherwise", () => {
    expect(maxMessageId([])).toBe(0);
    expect(maxMessageId([msg(4), msg(9), msg(2)])).toBe(9);
  });
});

describe("newReactionEvents", () => {
  it("returns only reaction-kind messages newer than afterId", () => {
    const messages = [
      msg(1, 1, "reaction", "👍"),
      msg(2, 2, "text"),
      msg(3, 2, "reaction", "🎉"),
      msg(4, 3, "system")
    ];
    expect(newReactionEvents(messages, 1).map((m) => m.id)).toEqual([3]);
    expect(newReactionEvents(messages, 0).map((m) => m.id)).toEqual([1, 3]);
  });
});

describe("unreadTextCount", () => {
  it("counts only others' text messages past the read cursor", () => {
    const self = 7;
    const messages = [
      msg(1, 2, "text"),
      msg(2, self, "text"),
      msg(3, 2, "reaction", "👍"),
      msg(4, 3, "text"),
      msg(5, 3, "system")
    ];
    expect(unreadTextCount(messages, 0, self)).toBe(2);
    expect(unreadTextCount(messages, 1, self)).toBe(1);
    expect(unreadTextCount(messages, 4, self)).toBe(0);
  });
});
