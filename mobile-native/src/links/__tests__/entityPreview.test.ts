/**
 * The preview is a partial disclosure of a post, so the assertions that matter
 * are about *where the answer comes from* rather than about the fields.
 *
 * The first test is the load-bearing one: the preview must call the same
 * endpoint that opening the post calls. That is what makes "a preview can never
 * be more permissive than a tap" true by construction. A test that only checked
 * a 403 produced an unavailable card would pass just as happily against a
 * dedicated preview endpoint with its own, laxer rules.
 */
import { PulseApiError } from "../../api/pulseApi";
import { clearEntityPreviewCache, resolveEntityPreview } from "../entityPreview";
import { resolvePulseEntity } from "../pulseEntity";

jest.mock("../../api/feed", () => {
  const actual = jest.requireActual("../../api/feed");
  return { ...actual, getPostDetail: jest.fn(), loadCachedPostDetail: jest.fn() };
});

jest.mock("../../api/profile", () => {
  const actual = jest.requireActual("../../api/profile");
  return { ...actual, getPublicProfile: jest.fn(), loadCachedProfile: jest.fn() };
});

// eslint-disable-next-line @typescript-eslint/no-var-requires
const feed = require("../../api/feed") as {
  getPostDetail: jest.Mock;
  loadCachedPostDetail: jest.Mock;
};

// `api/profileTarget` is deliberately NOT mocked. The assertion that matters is
// that the card looks a person up the way the profile screen does, and that is
// `resolveProfileTarget`'s job -- stubbing it would leave the test agreeing with
// a hand-written target rather than with the app's.
// eslint-disable-next-line @typescript-eslint/no-var-requires
const profileApi = require("../../api/profile") as {
  getPublicProfile: jest.Mock;
  loadCachedProfile: jest.Mock;
};

const REF = resolvePulseEntity("https://pulsesoc.com/pulse/post/2432")!;
const PROFILE_REF = resolvePulseEntity("https://pulsesoc.com/pulse/profile/roody")!;

function publicProfile(overrides: Record<string, unknown> = {}) {
  return {
    user_id: 77,
    display_name: "Roody Cherie",
    username: "roody",
    avatar_thumbnail_url: "https://cdn/avatar-sm.jpg",
    avatar_url: "https://cdn/avatar-lg.jpg",
    cover_url: "https://cdn/cover.jpg",
    bio: "Building PulseSoc from Port-au-Prince.",
    ...overrides
  };
}

function postDetail(overrides: Record<string, unknown> = {}) {
  return {
    post: {
      id: 2432,
      post_id: 2432,
      body: "Shipping the new upload engine today.",
      author: { display_name: "Ada Lovelace", username: "ada", avatar_url: "https://cdn/a.jpg" },
      media: [],
      ...overrides
    }
  };
}

beforeEach(() => {
  clearEntityPreviewCache();
  feed.getPostDetail.mockReset();
  feed.loadCachedPostDetail.mockReset();
  feed.loadCachedPostDetail.mockResolvedValue(null);
  profileApi.getPublicProfile.mockReset();
  profileApi.loadCachedProfile.mockReset();
  profileApi.loadCachedProfile.mockResolvedValue(null);
});

describe("where the preview comes from", () => {
  it("reads the post through the same call that opening the post makes", async () => {
    feed.getPostDetail.mockResolvedValue(postDetail());
    await resolveEntityPreview(REF);
    // Not "a preview endpoint returned the right shape" -- the point is that
    // there is no preview endpoint to hold a second, laxer authorization rule.
    expect(feed.getPostDetail).toHaveBeenCalledWith(2432);
  });

  it("carries the author, handle, caption and thumbnail onto the card", async () => {
    feed.getPostDetail.mockResolvedValue(
      postDetail({ media: [{ media_type: "image", media_url: "https://cdn/x.jpg" }] })
    );
    const state = await resolveEntityPreview(REF);
    expect(state).toMatchObject({
      status: "ready",
      preview: {
        authorName: "Ada Lovelace",
        authorHandle: "ada",
        caption: "Shipping the new upload engine today.",
        video: false
      }
    });
  });

  it("marks a video post so the card can badge the thumbnail", async () => {
    feed.getPostDetail.mockResolvedValue(
      postDetail({ media: [{ media_type: "video", media_url: "https://cdn/v.mp4" }] })
    );
    const state = await resolveEntityPreview(REF);
    expect(state.status === "ready" && state.preview.video).toBe(true);
  });

  /**
   * The picture on the card, which for months was a black rectangle.
   *
   * Every shared video post rendered an empty media frame with a "Video" badge
   * on it. The resolver was asking `mediaDisplayUrl` -- "where does this media
   * live" -- and for a video that is the video, so an `.m3u8` was handed to an
   * `<Image>`. Nothing threw, `onError` never fired, and the card went on
   * believing it had a thumbnail; the badge only draws inside the branch that
   * has one, which is why the screenshot showed a badge floating on nothing.
   *
   * So these assert on the *shape* of the URL, not merely that one exists. A
   * test that checked `thumbnailUrl` was non-empty passed throughout the bug.
   */
  it("shows a video post's still frame, not its playback URL", async () => {
    feed.getPostDetail.mockResolvedValue(
      postDetail({
        media: [
          {
            media_type: "video",
            media_url: "https://cdn/v.mp4",
            playback_url: "https://stream.mux.com/PLAY123.m3u8",
            thumbnail_url: "https://image.mux.com/PLAY123/thumbnail.jpg"
          }
        ]
      })
    );
    const state = await resolveEntityPreview(REF);
    const thumbnail = state.status === "ready" ? state.preview.thumbnailUrl : "";
    expect(thumbnail).toBe("https://image.mux.com/PLAY123/thumbnail.jpg");
  });

  it("does not trust a thumbnail_url that is really the video", async () => {
    /**
     * This is the payload the reporter's device actually had.
     *
     * `resolve_media` blanks a video URL out of `poster_url` and then returns
     * `thumbnail_url: thumb or source` one line later, putting the asset
     * straight back into the field named after the thumbnail;
     * `_canonical_media_payload` repeats the same fallback onto `valid_url`.
     * Both are fixed server-side now, but a payload cached before the fix is
     * still on disk, so the client has to survive being handed one.
     */
    feed.getPostDetail.mockResolvedValue(
      postDetail({
        media: [
          {
            media_type: "video",
            media_url: "https://stream.mux.com/PLAY123/high.mp4",
            valid_url: "https://stream.mux.com/PLAY123/high.mp4",
            thumbnail_url: "https://stream.mux.com/PLAY123/high.mp4",
            mux_thumbnail_url: "https://image.mux.com/PLAY123/thumbnail.jpg",
            poster_url: ""
          }
        ]
      })
    );
    const state = await resolveEntityPreview(REF);
    const thumbnail = state.status === "ready" ? state.preview.thumbnailUrl : "";
    expect(thumbnail).toBe("https://image.mux.com/PLAY123/thumbnail.jpg");
  });

  it("draws no picture at all for a video with no still, rather than a black box", async () => {
    // The old fallback produced the mp4 here, and an <Image> pointed at an mp4
    // is a filled aspect box that never draws. An empty string is the honest
    // answer: the card renders its text and skips the media frame entirely.
    feed.getPostDetail.mockResolvedValue(
      postDetail({ media: [{ media_type: "video", media_url: "https://cdn/v.mp4" }] })
    );
    const state = await resolveEntityPreview(REF);
    expect(state.status === "ready" && state.preview.thumbnailUrl).toBe("");
  });

  it("still uses a photo itself when the server sent no separate thumbnail", async () => {
    // Strictness about stills is about video. A photo is its own poster, and a
    // resolver that refused to say so would have traded one blank card for
    // another.
    feed.getPostDetail.mockResolvedValue(
      postDetail({ media: [{ media_type: "image", media_url: "https://cdn/x.jpg" }] })
    );
    const state = await resolveEntityPreview(REF);
    expect(state.status === "ready" && state.preview.thumbnailUrl).toBe("https://cdn/x.jpg");
  });

  it("shortens a long caption rather than letting it set the card's height", async () => {
    feed.getPostDetail.mockResolvedValue(postDetail({ body: "word ".repeat(80) }));
    const state = await resolveEntityPreview(REF);
    const caption = state.status === "ready" ? state.preview.caption : "";
    expect(caption.length).toBeLessThanOrEqual(141);
    expect(caption.endsWith("…")).toBe(true);
  });
});

describe("when the viewer may not see it", () => {
  it.each([
    ["forbidden", 403, "forbidden"],
    ["unauthenticated", 401, "forbidden"],
    ["deleted", 404, "missing"],
    ["gone", 410, "missing"]
  ])("reports %s without drawing any of the post", async (_label, status, reason) => {
    feed.getPostDetail.mockRejectedValue(new PulseApiError("nope", status));
    expect(await resolveEntityPreview(REF)).toEqual({ status: "unavailable", reason });
  });

  it("does not ask again for an answer that will not change", async () => {
    feed.getPostDetail.mockRejectedValue(new PulseApiError("nope", 403));
    await resolveEntityPreview(REF);
    await resolveEntityPreview(REF);
    // Twenty bubbles quoting one restricted post must not be twenty 403s.
    expect(feed.getPostDetail).toHaveBeenCalledTimes(1);
  });

  it("never reaches the offline cache for a post it was refused", async () => {
    feed.getPostDetail.mockRejectedValue(new PulseApiError("nope", 403));
    feed.loadCachedPostDetail.mockResolvedValue(postDetail());
    const state = await resolveEntityPreview(REF);
    // A post cached from back when the viewer could see it must not resurface
    // after the author restricted it.
    expect(state).toEqual({ status: "unavailable", reason: "forbidden" });
    expect(feed.loadCachedPostDetail).not.toHaveBeenCalled();
  });
});

describe("when the network is the problem", () => {
  it("falls back to the copy already on the device", async () => {
    feed.getPostDetail.mockRejectedValue(new Error("offline"));
    feed.loadCachedPostDetail.mockResolvedValue(postDetail());
    const state = await resolveEntityPreview(REF);
    expect(state.status === "ready" && state.preview.authorName).toBe("Ada Lovelace");
  });

  it("tries again next time rather than pinning a card to a dropped connection", async () => {
    feed.getPostDetail.mockRejectedValueOnce(new Error("offline"));
    expect(await resolveEntityPreview(REF)).toEqual({ status: "unavailable", reason: "error" });
    feed.getPostDetail.mockResolvedValue(postDetail());
    expect((await resolveEntityPreview(REF)).status).toBe("ready");
  });
});

describe("a profile is the same card, read the same way", () => {
  it("reads the profile through the same call that opening the profile makes", async () => {
    profileApi.getPublicProfile.mockResolvedValue(publicProfile());
    await resolveEntityPreview(PROFILE_REF);
    // As with posts: the point is not that a profile endpoint answered, it is
    // that this is `ProfileScreen`'s own loader, so the private/suspended/
    // deleted rules it enforces are enforced here for free and cannot drift.
    expect(profileApi.getPublicProfile).toHaveBeenCalledWith(
      expect.objectContaining({ profileKey: "roody" })
    );
  });

  it("carries the name, handle, bio and cover onto the card", async () => {
    profileApi.getPublicProfile.mockResolvedValue(publicProfile());
    const state = await resolveEntityPreview(PROFILE_REF);
    expect(state).toMatchObject({
      status: "ready",
      preview: {
        kind: "profile",
        authorName: "Roody Cherie",
        authorHandle: "roody",
        caption: "Building PulseSoc from Port-au-Prince.",
        thumbnailUrl: "https://cdn/cover.jpg",
        // The 26pt avatar asks for the small asset; taking `avatar_url` would
        // download the full-size image to draw the identical circle.
        authorAvatarUrl: "https://cdn/avatar-sm.jpg",
        video: false
      }
    });
  });

  it.each([
    ["private", 403, "forbidden"],
    ["suspended", 403, "forbidden"],
    ["deleted", 410, "missing"],
    ["never existed", 404, "missing"]
  ])("reports a %s profile without drawing any of it", async (_label, status, reason) => {
    profileApi.getPublicProfile.mockRejectedValue(new PulseApiError("nope", status));
    expect(await resolveEntityPreview(PROFILE_REF)).toEqual({ status: "unavailable", reason });
  });

  it("never reaches the offline cache for a profile it was refused", async () => {
    profileApi.getPublicProfile.mockRejectedValue(new PulseApiError("private", 403));
    profileApi.loadCachedProfile.mockResolvedValue(publicProfile());
    const state = await resolveEntityPreview(PROFILE_REF);
    // Someone who went private after this device cached them must not keep
    // leaking a cover photo and a bio into a conversation.
    expect(state).toEqual({ status: "unavailable", reason: "forbidden" });
    expect(profileApi.loadCachedProfile).not.toHaveBeenCalled();
  });

  it("treats a payload with nobody in it as missing rather than drawing a blank card", async () => {
    // `normalizeProfile` always returns an object, so "the server sent nothing"
    // arrives as a well-formed profile with no `user_id`. Trusting the type
    // here would render a card with an empty name and a working CTA.
    profileApi.getPublicProfile.mockResolvedValue({ user_id: 0 });
    expect(await resolveEntityPreview(PROFILE_REF)).toEqual({ status: "unavailable", reason: "missing" });
  });

  /**
   * Post 2432 and the person addressed as `2432` are different objects.
   *
   * A profile is looked up by *key*, and a numeric key is a perfectly ordinary
   * one -- `resolveProfileTarget` produces `profileKey: String(userId)`. So the
   * two id spaces overlap completely, and a cache keyed on the id alone would
   * serve whichever was asked for first to both. The kind is in the cache key
   * for this reason and this test is what keeps it there.
   */
  it("does not confuse a post with a profile that has the same id", async () => {
    const numericProfile = resolvePulseEntity("https://pulsesoc.com/pulse/profile/2432")!;
    feed.getPostDetail.mockResolvedValue(postDetail());
    profileApi.getPublicProfile.mockResolvedValue(publicProfile());

    const post = await resolveEntityPreview(REF);
    const profile = await resolveEntityPreview(numericProfile);

    expect(post.status === "ready" && post.preview.kind).toBe("post");
    expect(profile.status === "ready" && profile.preview.kind).toBe("profile");
    expect(profileApi.getPublicProfile).toHaveBeenCalledTimes(1);
  });
});

describe("not asking twice", () => {
  it("serves a resolved post from cache", async () => {
    feed.getPostDetail.mockResolvedValue(postDetail());
    await resolveEntityPreview(REF);
    await resolveEntityPreview(REF);
    expect(feed.getPostDetail).toHaveBeenCalledTimes(1);
  });

  it("joins a resolution already running instead of racing it", async () => {
    let release: (value: unknown) => void = () => undefined;
    feed.getPostDetail.mockReturnValue(new Promise((resolve) => { release = resolve; }));
    // The burst a freshly mounted page of bubbles produces: the cache is still
    // empty for all of them, so only the in-flight table can collapse these.
    const all = Promise.all([resolveEntityPreview(REF), resolveEntityPreview(REF), resolveEntityPreview(REF)]);
    release(postDetail());
    const states = await all;
    expect(feed.getPostDetail).toHaveBeenCalledTimes(1);
    expect(states.every((state) => state.status === "ready")).toBe(true);
  });
});
