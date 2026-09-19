/**
 * The card is a claim that "this message is that object", so what is pinned
 * here is mostly what must *not* produce one: a near-miss host, a path the app
 * does not claim, an ambiguous body. A card drawn for the wrong object would
 * show one post's author over another post's link, which is worse than no card.
 */
import { bodyIsOnlyLinks, messageEntity, resolvePulseEntity } from "../pulseEntity";
import { detectLinks } from "../messageLinks";

/** The parser's own output, so the tests exercise the real pairing. */
function linksIn(body: string) {
  return detectLinks(body).map((token) => token.text);
}

describe("resolving a post link", () => {
  it("resolves the canonical post URL to its id and in-app path", () => {
    expect(resolvePulseEntity("https://pulsesoc.com/pulse/post/2432")).toEqual({
      kind: "post",
      id: 2432,
      url: "https://pulsesoc.com/pulse/post/2432",
      path: "/pulse/post/2432"
    });
  });

  it("keeps query and fragment on the opened URL while ignoring them for matching", () => {
    const entity = resolvePulseEntity("https://pulsesoc.com/pulse/post/2432?utm_source=sms#top");
    expect(entity?.id).toBe(2432);
    // What the sender sent is what the tap opens. Silently rewriting someone's
    // URL to a tidier one is a change nobody asked for.
    expect(entity?.url).toContain("utm_source=sms");
  });

  it("accepts www and a trailing slash as the same post", () => {
    expect(resolvePulseEntity("https://www.pulsesoc.com/pulse/post/2432/")?.id).toBe(2432);
  });

  it.each([
    ["a lookalike host", "https://pulsesoc.com.evil.example/pulse/post/2432"],
    ["a subdomain that is not the site", "https://staging.pulsesoc.com/pulse/post/2432"],
    ["a non-PulseSoc host", "https://example.com/pulse/post/2432"],
    ["a path the app does not claim", "https://pulsesoc.com/r/abc123"],
    ["a deeper path", "https://pulsesoc.com/pulse/post/2432/edit"],
    ["a non-numeric id", "https://pulsesoc.com/pulse/post/abc"],
    ["post zero", "https://pulsesoc.com/pulse/post/0"],
    ["a blocked scheme", "javascript:alert(1)"],
    ["nonsense", "not a url"]
  ])("refuses to card %s", (_label, url) => {
    expect(resolvePulseEntity(url)).toBeNull();
  });

  it("does not swallow an invite link, which only works in a browser", () => {
    // Guards the same boundary messageLinks.ts guards: /r/ is deferred-attribution
    // and must stay external. A card here would imply the app can open it.
    expect(resolvePulseEntity("https://pulsesoc.com/r/XK92QP")).toBeNull();
  });
});

describe("deciding what a message is about", () => {
  it("cards a message whose only content is the post link", () => {
    const body = "https://pulsesoc.com/pulse/post/2432";
    expect(messageEntity(body, linksIn(body))?.id).toBe(2432);
    expect(bodyIsOnlyLinks(body, linksIn(body))).toBe(true);
  });

  it("cards a link wrapped in prose, but keeps the prose", () => {
    const body = "you have to read this https://pulsesoc.com/pulse/post/2432 before tonight";
    expect(messageEntity(body, linksIn(body))?.id).toBe(2432);
    // The sentence is the sender's; the card is additive, so the text stays.
    expect(bodyIsOnlyLinks(body, linksIn(body))).toBe(false);
  });

  it("treats a repeated link to one post as one subject", () => {
    const body = "https://pulsesoc.com/pulse/post/2432 https://pulsesoc.com/pulse/post/2432";
    expect(messageEntity(body, linksIn(body))?.id).toBe(2432);
  });

  it("draws no card when two different posts are named", () => {
    // Promoting the first would be a guess the sender never made.
    const body = "https://pulsesoc.com/pulse/post/1 and https://pulsesoc.com/pulse/post/2";
    expect(messageEntity(body, linksIn(body))).toBeNull();
  });

  it("ignores a non-entity link alongside the post", () => {
    const body = "https://example.com/article and https://pulsesoc.com/pulse/post/2432";
    expect(messageEntity(body, linksIn(body))?.id).toBe(2432);
    expect(bodyIsOnlyLinks(body, linksIn(body))).toBe(false);
  });

  it("draws no card for a message with no links at all", () => {
    expect(messageEntity("see you at six", [])).toBeNull();
  });

  it("counts a link with only whitespace around it as bare", () => {
    const body = "  https://pulsesoc.com/pulse/post/2432\n";
    expect(bodyIsOnlyLinks(body, linksIn(body))).toBe(true);
  });

  it("does not count trailing punctuation as bare", () => {
    // `detectLinks` trims the full stop off the URL, so it is left over in the
    // body -- and a sentence ending in a link is still a sentence.
    const body = "https://pulsesoc.com/pulse/post/2432.";
    expect(bodyIsOnlyLinks(body, linksIn(body))).toBe(false);
  });
});
