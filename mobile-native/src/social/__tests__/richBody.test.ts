/**
 * One body, both decorations, and the ordering that makes them agree.
 *
 * `segmentMentions` and `segmentLinks` were each already tested in isolation.
 * What could not be tested before `richBody` existed is what happens when a body
 * contains both, because nothing composed them — `CommentThread` ran mentions
 * *or* translation and never links, so the interaction had no code to have a bug
 * in. It does now, and the interaction has exactly one hazard.
 *
 * ## The hazard, stated as a property
 *
 * `MENTION_PATTERN` accepts any non-word character before the `@`, and `/` is
 * one. So `https://pulsesoc.com/@bob` contains, on a purely lexical reading, a
 * mention of `bob`. Whichever pass runs first wins the overlap, and only one of
 * the two orders produces something a reader can use. Measured, by building the
 * rejected order and running it:
 *
 *     mentionsFirst("see https://pulsesoc.com/@bob")
 *       -> "see " | https://pulsesoc.com/(link) | @bob(mention)
 *
 * Note what that is: not a lost link but a *working* one to the wrong place. The
 * fragment left behind is still a valid URL, so it is still underlined, still
 * tappable, and silently opens the site root — beside a mention of a user nobody
 * mentioned. So the cases below do not merely check that links and mentions both
 * appear; several exist to pin that a URL is never torn in half, which is the one
 * way this composition can be wrong while looking entirely finished.
 *
 * The inverse failure does not exist: the link pass leaves a bare `@carol` in
 * the prose it hands back untouched, so running links first costs the mention
 * pass nothing.
 */

import { hasRichContent, segmentRichBody } from "../richBody";

/** `{ text, url?, username? }` is awkward to assert on positionally. */
function shape(body: string) {
  return segmentRichBody(body).map((segment) =>
    segment.url
      ? `link(${segment.url})`
      : segment.username
        ? `mention(${segment.username})`
        : `text(${segment.text})`
  );
}

describe("segmentRichBody — a body holding both", () => {
  it("finds a link and a mention in the same body", () => {
    expect(shape("hi @carol see https://apple.com")).toEqual([
      "text(hi )",
      "mention(carol)",
      "text( see )",
      "link(https://apple.com)"
    ]);
  });

  it("keeps the text of every segment, so the body reads as it was written", () => {
    // A segmenter that decorates correctly but drops or duplicates a character
    // would satisfy every other case here. Rejoining is the only assertion that
    // can see that.
    const body = "hi @carol, look at https://pulsesoc.com/pulse/post/12 then ping @dave.";
    expect(
      segmentRichBody(body)
        .map((segment) => segment.text)
        .join("")
    ).toBe(body);
  });

  it("marks a segment as a link or a mention but never as both", () => {
    const segments = segmentRichBody("@carol https://pulsesoc.com/@bob");
    expect(segments.every((segment) => !(segment.url && segment.username))).toBe(true);
  });
});

describe("segmentRichBody — a mention inside a URL belongs to the URL", () => {
  it("does not tear a profile URL in half at its @", () => {
    // The measured mentions-first result is `text(see https://pulsesoc.com/)`
    // followed by `mention(bob)` -- a URL that has stopped being a URL and a
    // mention of a user who was never mentioned. Both halves are wrong and
    // neither is visible as a crash.
    expect(shape("see https://pulsesoc.com/@bob")).toEqual([
      "text(see )",
      "link(https://pulsesoc.com/@bob)"
    ]);
  });

  it("still finds a real mention in the prose beside such a URL", () => {
    // The ordering must not be paid for by the mention pass. This is the exact
    // body used to measure both orders before choosing.
    expect(shape("see https://pulsesoc.com/@bob and hi @carol")).toEqual([
      "text(see )",
      "link(https://pulsesoc.com/@bob)",
      "text( and hi )",
      "mention(carol)"
    ]);
  });

  it("emits no empty runs, so a body ending in a mention costs no extra Text", () => {
    // Composing two segmenters is where a stray `{ text: "" }` would come from:
    // the inner pass runs per outer run, so a boundary landing at the end of a
    // run could produce one per link in the body. Each would be a real `<Text>`
    // node in the paragraph. Measured to be absent rather than assumed.
    const bodies = [
      "see https://pulsesoc.com/@bob and hi @carol",
      "@carol",
      "https://apple.com",
      "@carol https://apple.com @dave"
    ];
    for (const body of bodies) {
      expect(segmentRichBody(body).filter((segment) => segment.text === "")).toEqual([]);
    }
  });

  it("gives the whole URL to the tap target, not the part before the @", () => {
    // What the reader is promised by the underline is that tapping the
    // characters they see goes where those characters say. `url` is the
    // normalised URL rather than the displayed slice, so this is the assertion
    // that the promise is kept.
    const [, link] = segmentRichBody("go https://pulsesoc.com/@bob/posts now");
    expect(link.url).toBe("https://pulsesoc.com/@bob/posts");
    expect(link.username).toBeUndefined();
  });
});

describe("segmentRichBody — bodies with nothing in them", () => {
  it("returns no segments at all for an empty body", () => {
    // Not `[{ text: "" }]`. `segments.length` is usable as "is there anything to
    // draw" only if this is empty, and `segmentMentions` already answers this
    // way.
    expect(segmentRichBody("")).toEqual([]);
  });

  it("returns plain prose as a single undecorated run", () => {
    expect(segmentRichBody("just a sentence")).toEqual([{ text: "just a sentence" }]);
  });

  it("leaves an email address alone rather than reading it as a mention", () => {
    // `MENTION_PATTERN` excludes a preceding `@`, and the character before the
    // `@` in an address is a word character, so neither pass claims it. Pinned
    // here because this body is the one users type most often that *looks* like
    // it should be decorated.
    expect(shape("write to sales@pulsesoc.com")).toEqual(["text(write to sales@pulsesoc.com)"]);
  });
});

describe("hasRichContent", () => {
  it("is false for prose and true as soon as there is anything to decorate", () => {
    expect(hasRichContent("")).toBe(false);
    expect(hasRichContent("just a sentence")).toBe(false);
    expect(hasRichContent("hi @carol")).toBe(true);
    expect(hasRichContent("see https://apple.com")).toBe(true);
  });

  it("is false for a body whose only @ is in an email address", () => {
    expect(hasRichContent("write to sales@pulsesoc.com")).toBe(false);
  });
});
