/**
 * Why the PulseSoc Insight account rendered as an empty circle.
 *
 * The server described its avatar the way a web page would: `/static/brand/...`.
 * A browser resolves that against the current origin, so the website was fine
 * and nobody looking at web QA would have seen a problem. React Native's
 * `Image` has no origin to resolve against. `{ uri: "/static/brand/x.png" }`
 * does not throw, does not fire `onError` usefully, and does not render — it
 * just leaves the circle blank, which reads as "this account has no picture"
 * rather than as a bug.
 *
 * `api/profile.ts` had always absolutised its URLs, which is why the profile
 * screen looked right while the feed did not. The rule now lives in `./config`
 * and both normalizers share it, so the two cannot drift apart again.
 *
 * These tests are about the *shape* of what reaches `<Image>`, not about the
 * Insight account specifically: any relative avatar from any account failed the
 * same way, and the fix is deliberately account-agnostic.
 */
import { absoluteApiUrl, PULSE_API_BASE_URL } from "../config";
import { normalizeComment, normalizePost } from "../feed";

describe("absoluteApiUrl", () => {
  it("prefixes a server-relative path with the API origin", () => {
    expect(absoluteApiUrl("/static/brand/x.png")).toBe(`${PULSE_API_BASE_URL}/static/brand/x.png`);
  });

  it("prefixes a bare relative path too", () => {
    expect(absoluteApiUrl("static/brand/x.png")).toBe(`${PULSE_API_BASE_URL}/static/brand/x.png`);
  });

  it("leaves an absolute URL alone", () => {
    // Every uploaded avatar arrives as a CDN URL. Prefixing one would produce
    // `https://pulsesoc.com/https://cdn...` and break the working case while
    // fixing the broken one.
    const cdn = "https://cdn.example.com/avatars/9.png";
    expect(absoluteApiUrl(cdn)).toBe(cdn);
  });

  it("leaves local preview schemes alone", () => {
    // A picture chosen from the camera roll is rendered before upload; turning
    // `file://...` into a web URL would blank the pre-publish preview.
    expect(absoluteApiUrl("file:///tmp/pick.jpg")).toBe("file:///tmp/pick.jpg");
    expect(absoluteApiUrl("data:image/png;base64,AAA")).toBe("data:image/png;base64,AAA");
  });

  it("maps nothing to nothing", () => {
    // The empty string is what the fallback branch tests for. Returning the
    // bare origin instead would make every avatar-less account render an
    // `<Image>` pointed at the homepage rather than the initial fallback.
    expect(absoluteApiUrl("")).toBe("");
    expect(absoluteApiUrl(null)).toBe("");
    expect(absoluteApiUrl(undefined)).toBe("");
  });
});

describe("a feed post author", () => {
  it("gets a loadable avatar from a server-relative path", () => {
    const post = normalizePost({
      id: 1,
      author: { display_name: "PulseSoc Insight", username: "pulsesoc_insight", avatar_url: "/static/brand/pulsesoc-insight-avatar-20260823.png" }
    } as never);
    expect(post.author?.avatar_url).toBe(`${PULSE_API_BASE_URL}/static/brand/pulsesoc-insight-avatar-20260823.png`);
  });

  /**
   * The reason this matters after the server was fixed: an install that has
   * been open since before the change is holding a cached payload full of the
   * old relative paths. Without this, that install shows the empty circle until
   * something forces a refetch.
   */
  it("gets a loadable avatar from the flat author_avatar_url field", () => {
    const post = normalizePost({ id: 2, author_avatar_url: "/static/brand/legacy.png" } as never);
    expect(post.author?.avatar_url).toBe(`${PULSE_API_BASE_URL}/static/brand/legacy.png`);
  });

  it("keeps an uploaded CDN avatar exactly as sent", () => {
    const post = normalizePost({ id: 3, author: { avatar_url: "https://cdn.example.com/a.png" } } as never);
    expect(post.author?.avatar_url).toBe("https://cdn.example.com/a.png");
  });

  it("still reports no avatar when there is none", () => {
    const post = normalizePost({ id: 4, author: { display_name: "Ada" } } as never);
    expect(post.author?.avatar_url).toBe("");
  });
});

describe("a comment author", () => {
  it("gets a loadable avatar", () => {
    const comment = normalizeComment({ id: 7, body: "hi", author: { display_name: "PulseSoc Insight", avatar_url: "/static/brand/x.png" } } as never);
    expect(comment.author?.avatar_url).toBe(`${PULSE_API_BASE_URL}/static/brand/x.png`);
  });

  it("is otherwise passed through untouched", () => {
    // Comment rendering reads fields the feed's author normalizer would
    // overwrite with invented defaults, so only the avatar may change.
    const author = { display_name: "Ada", username: "ada", avatar_url: "https://cdn.example.com/a.png", badges: ["Member"] };
    const comment = normalizeComment({ id: 8, body: "hi", author } as never);
    expect(comment.author).toBe(author);
  });

  it("survives a comment with no author at all", () => {
    const comment = normalizeComment({ id: 9, body: "hi" } as never);
    expect(comment.author).toBeUndefined();
  });
});
