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

describe("resolving a profile link", () => {
  it("resolves the canonical profile URL to its key and in-app path", () => {
    expect(resolvePulseEntity("https://pulsesoc.com/pulse/profile/roody")).toEqual({
      kind: "profile",
      id: "roody",
      url: "https://pulsesoc.com/pulse/profile/roody",
      path: "/pulse/profile/roody"
    });
  });

  it("keeps a numeric key a string rather than making it look like a post id", () => {
    const entity = resolvePulseEntity("https://pulsesoc.com/pulse/profile/2432");
    // The server resolves a numeric key as `users.user_id`; turning it into a
    // number here would make `post:2432` and `profile:2432` indistinguishable
    // anywhere the two are compared.
    expect(entity).toMatchObject({ kind: "profile", id: "2432" });
    expect(typeof entity?.id).toBe("string");
  });

  it("decodes a percent-encoded key so the lookup is the one the URL means", () => {
    // `getPublicProfile` encodes again on its way out, so handing it the still
    // encoded segment would look up a person literally called `roody%2Echerie`.
    expect(resolvePulseEntity("https://pulsesoc.com/pulse/profile/roody%2Echerie")?.id).toBe("roody.cherie");
  });

  it.each([
    ["a deeper path", "https://pulsesoc.com/pulse/profile/roody/posts"],
    ["an empty key", "https://pulsesoc.com/pulse/profile/"],
    ["a key with a space", "https://pulsesoc.com/pulse/profile/roody%20cherie"],
    ["a key that is an injected path", "https://pulsesoc.com/pulse/profile/..%2F..%2Fadmin"],
    ["a lookalike host", "https://pulsesoc.com.evil.example/pulse/profile/roody"]
  ])("refuses to card %s", (_label, url) => {
    expect(resolvePulseEntity(url)).toBeNull();
  });

  it.each([
    ["the profile editor", "https://pulsesoc.com/pulse/profile/edit"],
    ["the profile editor in caps", "https://pulsesoc.com/pulse/profile/EDIT"]
  ])("refuses to card %s, which is a screen and not a person", (_label, url) => {
    // `linking.ts` routes this to settings before it ever looks for a member,
    // so a card here would look up somebody called "edit", be told there is no
    // such person, and report that their profile had been deleted.
    expect(resolvePulseEntity(url)).toBeNull();
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

  it("draws no card when a post and a profile are both named", () => {
    // Two entities of *different* kinds are still two subjects. The dedupe
    // compares kind as well as id, so this must not collapse into one card --
    // and it is the case that would break first if `id` alone became the
    // identity, because a post id and a profile key can be the same string.
    const body = "https://pulsesoc.com/pulse/post/2432 and https://pulsesoc.com/pulse/profile/roody";
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
