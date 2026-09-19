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

// eslint-disable-next-line @typescript-eslint/no-var-requires
const feed = require("../../api/feed") as {
  getPostDetail: jest.Mock;
  loadCachedPostDetail: jest.Mock;
};

const REF = resolvePulseEntity("https://pulsesoc.com/pulse/post/2432")!;

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
