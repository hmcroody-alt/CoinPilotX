/**
 * Detection, segmentation and classification for links inside message bodies.
 *
 * The cases here are the ones the brief enumerated plus the ones that decide
 * whether this is safe: scheme rejection, host spoofing, and the `/r/` invite
 * link that must NOT be swallowed by in-app routing.
 */

import { classifyLink, detectLinks, normalizeLinkUrl, segmentLinks } from "../messageLinks";

describe("detectLinks", () => {
  it("finds a URL that is the entire message", () => {
    expect(detectLinks("https://pulsesoc.com/").map((token) => token.text)).toEqual([
      "https://pulsesoc.com/"
    ]);
  });

  it("finds a URL inside a sentence and leaves the sentence alone", () => {
    const tokens = detectLinks("Visit https://apple.com when you have time.");
    expect(tokens).toHaveLength(1);
    expect(tokens[0].text).toBe("https://apple.com");
  });

  it("finds several URLs in one message, in order", () => {
    const tokens = detectLinks("first https://pulsesoc.com/pulse/post/1 then https://apple.com/x");
    expect(tokens.map((token) => token.text)).toEqual([
      "https://pulsesoc.com/pulse/post/1",
      "https://apple.com/x"
    ]);
  });

  it("keeps query parameters and fragments", () => {
    const tokens = detectLinks("https://pulsesoc.com/pulse/search?q=hello&x=1#results");
    expect(tokens[0].text).toBe("https://pulsesoc.com/pulse/search?q=hello&x=1#results");
  });

  it("drops trailing sentence punctuation but not path characters", () => {
    expect(detectLinks("go to https://pulsesoc.com/pulse/post/2432.")[0].text)
      .toBe("https://pulsesoc.com/pulse/post/2432");
    expect(detectLinks("go to https://pulsesoc.com/pulse/post/2432, now")[0].text)
      .toBe("https://pulsesoc.com/pulse/post/2432");
    expect(detectLinks("really? https://pulsesoc.com/pulse!")[0].text)
      .toBe("https://pulsesoc.com/pulse");
    // A trailing slash is part of the URL, not punctuation.
    expect(detectLinks("https://pulsesoc.com/")[0].text).toBe("https://pulsesoc.com/");
  });

  it("drops an unmatched closing bracket but keeps a matched one", () => {
    expect(detectLinks("(see https://pulsesoc.com/pulse)")[0].text)
      .toBe("https://pulsesoc.com/pulse");
    expect(detectLinks("https://en.wikipedia.org/wiki/Foo_(bar)")[0].text)
      .toBe("https://en.wikipedia.org/wiki/Foo_(bar)");
    expect(detectLinks("(https://en.wikipedia.org/wiki/Foo_(bar)).")[0].text)
      .toBe("https://en.wikipedia.org/wiki/Foo_(bar)");
  });

  it("finds a URL on its own line after text", () => {
    const tokens = detectLinks("Hey! Check this out:\nhttps://pulsesoc.com/");
    expect(tokens).toHaveLength(1);
    expect(tokens[0].text).toBe("https://pulsesoc.com/");
  });

  it("never detects a non-http scheme", () => {
    expect(detectLinks("javascript:alert(1)")).toEqual([]);
    expect(detectLinks("data:text/html;base64,PHNjcmlwdD4=")).toEqual([]);
    expect(detectLinks("file:///etc/passwd")).toEqual([]);
    expect(detectLinks("ftp://example.com/x")).toEqual([]);
  });

  it("does not linkify prose that merely contains dots", () => {
    expect(detectLinks("I use node.js and it costs 1.50 etc.")).toEqual([]);
    expect(detectLinks("no link here at all")).toEqual([]);
    expect(detectLinks("")).toEqual([]);
  });

  it("treats a leading www. as a link and normalises it to https", () => {
    const tokens = detectLinks("see www.pulsesoc.com/pulse today");
    expect(tokens[0].text).toBe("www.pulsesoc.com/pulse");
    expect(normalizeLinkUrl(tokens[0].text)).toBe("https://www.pulsesoc.com/pulse");
  });
});

describe("segmentLinks", () => {
  it("preserves every character of the body across the segments", () => {
    const body = "Hey! Check this out:\nhttps://pulsesoc.com/ and also https://apple.com. Bye";
    const segments = segmentLinks(body);
    expect(segments.map((segment) => segment.text).join("")).toBe(body);
  });

  it("marks only the link segments", () => {
    const segments = segmentLinks("Visit https://apple.com when you have time.");
    expect(segments.map((segment) => Boolean(segment.url))).toEqual([false, true, false]);
    expect(segments[0].text).toBe("Visit ");
    expect(segments[2].text).toBe(" when you have time.");
  });

  it("returns a single plain segment when there is no link", () => {
    expect(segmentLinks("just words")).toEqual([{ text: "just words" }]);
  });

  it("returns nothing for an empty body", () => {
    expect(segmentLinks("")).toEqual([]);
  });

  it("keeps emoji and mention text intact around a link", () => {
    const body = "@pilot 🚀 https://pulsesoc.com/pulse/post/2432 🎉";
    const segments = segmentLinks(body);
    expect(segments.map((segment) => segment.text).join("")).toBe(body);
    expect(segments.filter((segment) => segment.url)).toHaveLength(1);
  });

  it("shows exactly what it opens", () => {
    // The displayed slice and the destination cannot disagree: one is derived
    // from the other. This is the structural form of "do not trust the
    // displayed text if the metadata differs".
    segmentLinks("a https://pulsesoc.com/pulse/post/7 b")
      .filter((segment) => segment.url)
      .forEach((segment) => {
        expect(segment.url).toBe(normalizeLinkUrl(segment.text));
      });
  });
});

describe("classifyLink", () => {
  it("routes a claimed PulseSoc path in-app", () => {
    expect(classifyLink("https://pulsesoc.com/pulse/post/2432")).toEqual({
      kind: "internal",
      url: "https://pulsesoc.com/pulse/post/2432",
      path: "/pulse/post/2432"
    });
  });

  it("routes the site root to the feed", () => {
    const destination = classifyLink("https://pulsesoc.com/");
    expect(destination.kind).toBe("internal");
    expect(destination.kind === "internal" && destination.path).toBe("/pulse");
  });

  it.each([
    ["/pulse/reels/12", "/pulse/reels/12"],
    ["/pulse/messages/44", "/pulse/messages/44"],
    ["/pulse/marketplace/9", "/pulse/marketplace/9"],
    ["/pulse/profile/pilot", "/pulse/profile/pilot"]
  ])("routes %s in-app", (path, expected) => {
    const destination = classifyLink(`https://pulsesoc.com${path}`);
    expect(destination.kind).toBe("internal");
    expect(destination.kind === "internal" && destination.path).toBe(expected);
  });

  it("carries query and fragment into the native path", () => {
    const destination = classifyLink("https://pulsesoc.com/pulse/search?q=hello#top");
    expect(destination.kind).toBe("internal");
    expect(destination.kind === "internal" && destination.path).toBe("/pulse/search?q=hello#top");
  });

  it("sends an invite link to the browser, because only the browser can honour it", () => {
    // `/r/<code>` is the deferred-attribution redirect. Routing it in-app would
    // land on the dashboard module fallback and break referral attribution.
    expect(classifyLink("https://pulsesoc.com/r/ab12cd").kind).toBe("external");
  });

  it("sends an unclaimed PulseSoc path to the browser rather than guessing", () => {
    expect(classifyLink("https://pulsesoc.com/some/marketing/page").kind).toBe("external");
  });

  it("treats a foreign host as external", () => {
    expect(classifyLink("https://apple.com")).toEqual({
      kind: "external",
      url: "https://apple.com/"
    });
  });

  it("does not mistake a lookalike host for PulseSoc", () => {
    expect(classifyLink("https://pulsesoc.com.evil.net/pulse/post/1").kind).toBe("external");
    expect(classifyLink("https://evil-pulsesoc.com/pulse/post/1").kind).toBe("external");
    expect(classifyLink("https://notpulsesoc.com/pulse/post/1").kind).toBe("external");
  });

  it("accepts the www subdomain as PulseSoc", () => {
    expect(classifyLink("https://www.pulsesoc.com/pulse/post/5").kind).toBe("internal");
  });

  it("blocks every scheme that is not http or https", () => {
    expect(classifyLink("javascript:alert(1)").kind).toBe("blocked");
    expect(classifyLink("data:text/html,<script>").kind).toBe("blocked");
    expect(classifyLink("file:///etc/passwd").kind).toBe("blocked");
    expect(classifyLink("ftp://example.com").kind).toBe("blocked");
    expect(classifyLink("not a url at all").kind).toBe("blocked");
    expect(classifyLink("").kind).toBe("blocked");
  });

  it("blocks a scheme smuggled behind whitespace or case", () => {
    expect(classifyLink("  JavaScript:alert(1)").kind).toBe("blocked");
    expect(classifyLink("JAVASCRIPT:alert(1)").kind).toBe("blocked");
  });
});
