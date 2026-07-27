import {
  activeMentionQuery,
  authorHandle,
  insertMention,
  mentionedUsernames,
  parseMentions,
  seedReplyDraft,
  segmentMentions
} from "../mentions";

describe("mention grammar", () => {
  it("finds a mention at the start of a body and mid-sentence", () => {
    expect(mentionedUsernames("@roody hello")).toEqual(["roody"]);
    expect(mentionedUsernames("hey @roody hello")).toEqual(["roody"]);
  });

  it("does not treat an email address as a mention", () => {
    expect(mentionedUsernames("mail me at roody@pulsesoc.com")).toEqual([]);
  });

  it("does not treat a double-at as a mention", () => {
    expect(mentionedUsernames("@@roody")).toEqual([]);
  });

  it("leaves a trailing period as prose rather than part of the handle", () => {
    expect(mentionedUsernames("thanks @roody.")).toEqual(["roody"]);
  });

  it("keeps an interior period, which is legal in a handle", () => {
    expect(mentionedUsernames("cc @first.last here")).toEqual(["first.last"]);
  });

  it("de-duplicates repeated handles case-insensitively but preserves the first spelling", () => {
    expect(mentionedUsernames("@Roody and @roody and @ROODY")).toEqual(["Roody"]);
  });

  it("finds several distinct mentions in order", () => {
    expect(mentionedUsernames("@a then @b then @c")).toEqual(["a", "b", "c"]);
  });

  it("returns positions that slice the exact mention text back out", () => {
    const body = "hey @roody look";
    const [token] = parseMentions(body);
    expect(body.slice(token.start, token.end)).toBe("@roody");
  });

  it("ignores a bare at-sign", () => {
    expect(parseMentions("@ hello")).toEqual([]);
    expect(parseMentions("")).toEqual([]);
  });

  it("segments a body so the segments reassemble into the original", () => {
    const body = "hey @roody and @sam ok";
    const segments = segmentMentions(body);
    expect(segments.map((segment) => segment.text).join("")).toBe(body);
    expect(segments.filter((segment) => segment.username).map((segment) => segment.username)).toEqual(["roody", "sam"]);
  });

  it("segments a body with no mentions into a single plain segment", () => {
    expect(segmentMentions("plain text")).toEqual([{ text: "plain text" }]);
    expect(segmentMentions("")).toEqual([]);
  });
});

describe("reply draft seeding", () => {
  it("prefixes the author handle on an empty draft", () => {
    expect(seedReplyDraft("", { username: "roody" })).toBe("@roody ");
  });

  it("prefixes the handle ahead of existing text", () => {
    expect(seedReplyDraft("nice one", { username: "roody" })).toBe("@roody nice one");
  });

  it("does not stack a duplicate mention when re-opening the composer", () => {
    expect(seedReplyDraft("@roody nice one", { username: "roody" })).toBe("@roody nice one");
    expect(seedReplyDraft("@ROODY nice one", { username: "roody" })).toBe("@ROODY nice one");
  });

  it("returns the draft unchanged when the author has no usable handle", () => {
    expect(seedReplyDraft("hello", {})).toBe("hello");
    expect(seedReplyDraft("hello", null)).toBe("hello");
  });

  it("reads a handle from any of the fields the payload might use", () => {
    expect(authorHandle({ username: "a" })).toBe("a");
    expect(authorHandle({ public_player_id: "b" })).toBe("b");
    expect(authorHandle({ display_name: "c" })).toBe("c");
    expect(authorHandle({ username: "@d" })).toBe("d");
    expect(authorHandle(undefined)).toBe("");
  });
});

describe("mention insertion from a suggestion list", () => {
  it("replaces the partial handle the user was typing", () => {
    const result = insertMention("hey @ro", 7, "roody");
    expect(result.body).toBe("hey @roody ");
    expect(result.selection).toBe(11);
  });

  it("inserts at the caret when no partial handle is present", () => {
    const result = insertMention("hey ", 4, "roody");
    expect(result.body).toBe("hey @roody ");
  });

  it("leaves the tail of the body intact and does not double the space", () => {
    const result = insertMention("hey @ro there", 7, "roody");
    expect(result.body).toBe("hey @roody there");
    expect(result.selection).toBe(10);
  });

  it("places the caret immediately after the inserted mention", () => {
    const result = insertMention("a @b c", 4, "bob");
    expect(result.body.slice(0, result.selection)).toBe("a @bob");
  });

  it("is a no-op for an empty username", () => {
    expect(insertMention("hey ", 4, "")).toEqual({ body: "hey ", selection: 4 });
  });

  it("accepts a username already carrying its at-sign", () => {
    expect(insertMention("hey ", 4, "@roody").body).toBe("hey @roody ");
  });

  it("clamps a caret past the end of the body", () => {
    expect(insertMention("hi", 99, "roody").body).toBe("hi@roody ");
  });
});

describe("active mention query", () => {
  it("reports the partial handle while the caret is inside a mention", () => {
    expect(activeMentionQuery("hey @ro", 7)).toBe("ro");
    expect(activeMentionQuery("hey @", 5)).toBe("");
  });

  it("reports empty once the mention is closed by a space", () => {
    expect(activeMentionQuery("hey @roody ", 11)).toBe("");
  });

  it("reports empty when the caret is not in a mention at all", () => {
    expect(activeMentionQuery("hey there", 9)).toBe("");
  });

  it("ignores text after the caret", () => {
    expect(activeMentionQuery("hey @ro and more", 7)).toBe("ro");
  });
});
