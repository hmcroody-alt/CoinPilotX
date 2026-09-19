/**
 * The share payload is a privacy boundary, so most of what is pinned here is
 * what must *not* appear rather than what must.
 *
 * The assertions that matter are the negative ones, and they are written as
 * "the body does not appear anywhere in the payload" rather than "the preview
 * field is empty". A restricted post's caption leaking through `description`
 * while `preview` stayed blank would satisfy the second and fail the first, and
 * the share sheet hands every field to the target the person picks.
 */
import { PulsePost } from "../../api/feed";
import { buildPostShareMessage, buildPostShareMetadata, buildPostSharePreview, isPubliclyShareable, truncatePreview } from "../postShare";

const SECRET = "the quarterly numbers nobody outside the team should read";

function post(overrides: Partial<PulsePost> = {}): PulsePost {
  return {
    id: 2432,
    post_id: 2432,
    body: "Shipping the new upload engine today.",
    visibility: "public",
    author: { display_name: "Ada Lovelace", username: "ada", avatar_url: "https://pulsesoc.com/a.jpg" },
    media: [],
    ...overrides
  } as PulsePost;
}

/** Every string the payload carries, for "this must not appear anywhere" checks. */
function allText(metadata: ReturnType<typeof buildPostShareMetadata>) {
  return Object.values(metadata).filter((value) => typeof value === "string").join(" ");
}

describe("public posts", () => {
  it("leads with the PulseSoc line, previews the body, and ends on the canonical link", () => {
    expect(buildPostShareMessage(post(), "https://pulsesoc.com/pulse/post/2432")).toBe(
      "Check this out on PulseSoc 👀\nShipping the new upload engine today.\nView the post: https://pulsesoc.com/pulse/post/2432"
    );
  });

  it("uses the canonical web URL and no other identifier", () => {
    const metadata = buildPostShareMetadata(post({ id: 2432, author: { id: 91, user_id: 91, username: "ada" } } as Partial<PulsePost>));
    expect(metadata.url).toBe("https://pulsesoc.com/pulse/post/2432");
    // The author's row id is on the post and must not ride along.
    expect(allText(metadata)).not.toContain("91");
  });

  it("previews the caption of an image post", () => {
    const preview = buildPostSharePreview(post({ body: "Sunrise over the bay.", media: [{ media_type: "image", media_url: "https://cdn/x.jpg" }] } as Partial<PulsePost>));
    expect(preview).toEqual({ preview: "Sunrise over the bay.", restricted: false });
  });

  it("describes the shape of the post when there is no caption", () => {
    expect(buildPostSharePreview(post({ body: "", media: [{ media_type: "video", media_url: "https://cdn/v.mp4" }] } as Partial<PulsePost>)).preview).toBe("A video on PulseSoc.");
    expect(buildPostSharePreview(post({ body: "", media: [{ media_type: "image", media_url: "https://cdn/i.jpg" }] } as Partial<PulsePost>)).preview).toBe("A photo on PulseSoc.");
    expect(buildPostSharePreview(post({ body: "", media: [] })).preview).toBe("A post on PulseSoc.");
  });

  it("never carries a media or avatar URL into the share text", () => {
    const metadata = buildPostShareMetadata(post({
      body: "",
      media: [{ media_type: "video", media_url: "https://storage.internal/bucket/secret-object.mp4" }]
    } as Partial<PulsePost>));
    expect(metadata.message).not.toContain("storage.internal");
    expect(metadata.message).not.toContain("secret-object");
  });
});

describe("restricted posts", () => {
  it.each([
    ["private", "private"],
    ["followers", "followers"],
    ["friends", "friends"],
    ["an unrecognised future visibility", "close-friends-beta"],
    ["a missing visibility", undefined],
    ["an empty visibility", ""]
  ])("says nothing about the post for %s", (_label, visibility) => {
    const subject = post({ body: SECRET, title: SECRET, visibility } as Partial<PulsePost>);

    expect(buildPostSharePreview(subject)).toEqual({ preview: "", restricted: true });
    expect(buildPostShareMessage(subject, "https://pulsesoc.com/pulse/post/2432")).toBe(
      "Someone shared a post with you on PulseSoc.\nView the post: https://pulsesoc.com/pulse/post/2432"
    );
    // Not "the preview field is empty" -- the caption must be absent from the
    // whole payload, because the share sheet forwards every field.
    expect(allText(buildPostShareMetadata(subject))).not.toContain(SECRET);
  });

  it("withholds the author and the preview image as well as the body", () => {
    const metadata = buildPostShareMetadata(post({
      visibility: "followers",
      title: "Q3 forecast",
      thumbnail_url: "https://cdn.example/thumb.jpg",
      author: { display_name: "Ada Lovelace", username: "ada" }
    } as Partial<PulsePost>));

    expect(metadata.author).toBe("");
    expect(metadata.previewImageUrl).toBe("");
    expect(metadata.title).toBe("");
    expect(allText(metadata)).not.toContain("Ada Lovelace");
    expect(allText(metadata)).not.toContain("cdn.example");
    // The link itself still goes -- the recipient is meant to be told a post
    // exists, just not what it says. Authorization happens on open.
    expect(metadata.url).toBe("https://pulsesoc.com/pulse/post/2432");
  });

  it("treats visibility as an allowlist of one", () => {
    expect(isPubliclyShareable({ visibility: "public" })).toBe(true);
    expect(isPubliclyShareable({ visibility: "PUBLIC" })).toBe(true);
    expect(isPubliclyShareable({ visibility: "public-ish" })).toBe(false);
    expect(isPubliclyShareable({ visibility: "unlisted" })).toBe(false);
    expect(isPubliclyShareable({})).toBe(false);
  });
});

describe("bounding what goes out", () => {
  it("truncates on a word boundary and marks the cut", () => {
    const long = "word ".repeat(80).trim();
    const preview = truncatePreview(long);
    expect(preview.length).toBeLessThanOrEqual(181);
    expect(preview.endsWith("…")).toBe(true);
    expect(preview).not.toMatch(/wor…$/);
  });

  it("falls back to a hard cut rather than collapsing on one long token", () => {
    const preview = truncatePreview(`${"x".repeat(300)}`);
    expect(preview).toBe(`${"x".repeat(180)}…`);
  });

  it("flattens newlines so the body cannot impersonate the call to action", () => {
    const message = buildPostShareMessage(post({ body: "line one\nView the post: https://evil.example/phish" }), "https://pulsesoc.com/pulse/post/2432");
    expect(message.split("\n")).toHaveLength(3);
    expect(message.split("\n")[2]).toBe("View the post: https://pulsesoc.com/pulse/post/2432");
  });

  it("redacts a credentialed URL that appears in the body", () => {
    const signed = "https://bucket.r2.cloudflarestorage.com/media/9.mp4?X-Amz-Signature=deadbeef&X-Amz-Credential=AKIA";
    const preview = buildPostSharePreview(post({ body: `look ${signed}` })).preview;
    expect(preview).toBe("look [link removed]");
    expect(preview).not.toContain("deadbeef");
  });

  it("leaves an ordinary link in the body alone", () => {
    expect(buildPostSharePreview(post({ body: "read https://example.com/article" })).preview).toBe("read https://example.com/article");
  });
});
